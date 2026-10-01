# src/audio/narration.py
# Phase 44C — 旁白 cue 产物仓 + TTS 胶水层（复用 MiniMaxVoiceAdapter，44C_C0/C1）。
#
# 域边界（S2 §2/§3 分域纪律）：音频【生成】不属于 Sequence S2——本模块是 A1
# （DialogueAudio）的旁白兄弟域：复用同一 provider 适配器与媒体后处理，但拥有
# 独立的 artifact store（output/narration_audio.json）。旁白不是 Shot.dialogue，
# 绝不冒用 A1 的 dialogue canonical / voice profile 绑定（C1.4：不克隆、不绑角色声线）。
#
# 产物去向：规范化 WAV 写入项目 output/audio_imports/narration/ —— 这是 S2
# imported_audio 引用的规范目录，混音侧（SequenceAudioPlan）以 imported_audio
# 引用本仓产物，引用即溯源（render Phase 1 现场 sha256）。
#
# 幂等（C1.3）：idempotency_key = 指纹(text+model+voice_id+speed/vol/pitch+
# language_boost)。同 key 且文件在且哈希吻合 → 直接复用既有版本（零 API 调用、
# 零重复付费）；断线恢复查既有资产即此语义。
#
# 🔴 secret 纪律同 A1 §13：key 只进 Authorization header；request snapshot /
#    回执 / 错误体落盘前脱敏（adapter 构造保证 + 本层防御性再滤）。
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from src.core.atomic import atomic_write_json

from .media_ops import ffprobe_audio, normalize_to_production_wav
from .minimax_adapter import MiniMaxVoiceAdapter, VoiceProviderError
from .models import VoiceGenerationPack

NARRATION_STORE_SCHEMA = "narration_audio_v1"

# 默认旁白绑定（44C_C0 §2：主选温润男声；A/B 备选由 caller 显式传 voice_id）
DEFAULT_NARRATOR_MODEL = "speech-2.8-turbo"
DEFAULT_NARRATOR_VOICE = "Chinese (Mandarin)_Gentleman"
DEFAULT_NARRATOR_SPEED = 0.9

NARRATION_IMPORT_SUBDIR = "audio_imports/narration"

# 旁白文本护栏（cue 是短句；超长应拆句——C2 不硬塞原则的入口防线）
NARRATION_TEXT_MAX_CHARS = 200

_SECRET_MARKS = ("key", "token", "secret", "authorization", "password")


def _strip_secrets(meta: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (meta or {}).items():
        if any(m in str(k).lower() for m in _SECRET_MARKS):
            continue
        out[str(k)] = v
    return out


def narration_idempotency_key(*, text: str, model: str, voice_id: str,
                              speed: float, vol: float, pitch: int,
                              language_boost: str = "") -> str:
    """同语义合成 = 同 key（缓存/断线恢复依据；不含时间戳）。"""
    from src.resources.generation import fingerprint
    return fingerprint({
        "text": text, "model": model, "voice_id": voice_id,
        "speed": speed, "vol": vol, "pitch": pitch,
        "language_boost": language_boost,
    })


def sha256_file(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


@dataclass
class NarrationCueVersion:
    """一句旁白的一次合成 artifact（metadata；音频本体在 audio_imports/）。"""
    version_id: str
    cue_id: str
    text: str
    idempotency_key: str
    provider: str
    model: str
    voice_id: str
    speed: float
    vol: float
    pitch: int
    language_boost: str
    audio_import_path: str          # 相对项目 output 的规范路径（audio_imports/narration/…）
    audio_sha256: str
    duration_probe: Dict[str, Any] = field(default_factory=dict)
    provider_trace_id: str = ""
    provider_request_snapshot: Dict[str, Any] = field(default_factory=dict)
    provider_extra: Dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    cache_hit: bool = False
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "version_id": self.version_id, "cue_id": self.cue_id,
            "text": self.text, "idempotency_key": self.idempotency_key,
            "provider": self.provider, "model": self.model,
            "voice_id": self.voice_id, "speed": self.speed,
            "vol": self.vol, "pitch": self.pitch,
            "language_boost": self.language_boost,
            "audio_import_path": self.audio_import_path,
            "audio_sha256": self.audio_sha256,
            "duration_probe": dict(self.duration_probe),
            "provider_trace_id": self.provider_trace_id,
            "provider_request_snapshot": dict(self.provider_request_snapshot),
            "provider_extra": dict(self.provider_extra),
            "latency_ms": self.latency_ms, "cache_hit": self.cache_hit,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NarrationCueVersion":
        if not isinstance(d, dict):
            raise ValueError("narration version 条目必须是对象")
        for required in ("version_id", "cue_id", "idempotency_key",
                         "audio_import_path", "audio_sha256"):
            if required not in (d or {}):
                raise ValueError(f"narration version 缺少 {required}（fail closed）")
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


class NarrationStore:
    """output/narration_audio.json（A1 dialogue_audio.json 同构：cue → versions）。"""

    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.dir = Path(project_dir)
        self.path = self.dir / "output" / "narration_audio.json"
        self.project_name = project_name or self.dir.name

    def load(self) -> Dict[str, Dict[str, Any]]:
        if not self.path.exists():
            return {}
        import json
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            raise ValueError(f"narration_audio.json 读取失败（不静默重置）: {e}")
        if not isinstance(raw, dict) or raw.get("schema") != NARRATION_STORE_SCHEMA:
            raise ValueError("narration_audio.json schema 不支持（fail closed）")
        cues = raw.get("cues") or {}
        if not isinstance(cues, dict):
            raise ValueError("narration_audio.json cues 形状非法")
        return cues

    def save(self, cues: Dict[str, Dict[str, Any]]) -> None:
        atomic_write_json(self.path, {
            "schema": NARRATION_STORE_SCHEMA,
            "cues": cues,
            "updated_at": time.time(),
        })

    def versions_of(self, cue_id: str) -> list:
        entry = self.load().get(cue_id) or {}
        return [NarrationCueVersion.from_dict(v) for v in (entry.get("versions") or [])]

    def append(self, version: NarrationCueVersion) -> None:
        cues = self.load()
        entry = cues.setdefault(version.cue_id, {"versions": []})
        entry.setdefault("versions", []).append(version.to_dict())
        self.save(cues)

    def find_by_key(self, cue_id: str, key: str) -> Optional[NarrationCueVersion]:
        for v in self.versions_of(cue_id):
            if v.idempotency_key == key:
                return v
        return None


def _probe_duration_ms(probe: Optional[Dict[str, Any]]) -> int:
    """ffprobe_audio 实测秒（键 duration_s）→ 毫秒。"""
    if not probe or not probe.get("duration_s"):
        return 0
    return int(round(float(probe["duration_s"]) * 1000))


async def synthesize_narration_cue(project_dir, *, cue_id: str, text: str,
                                   model: str = DEFAULT_NARRATOR_MODEL,
                                   voice_id: str = DEFAULT_NARRATOR_VOICE,
                                   speed: float = DEFAULT_NARRATOR_SPEED,
                                   vol: float = 1.0, pitch: int = 0,
                                   language_boost: str = "",
                                   adapter: Optional[MiniMaxVoiceAdapter] = None,
                                   force_new: bool = False) -> Dict[str, Any]:
    """合成/复用一句旁白（C1 最小闭环）。
    - 幂等：同 key 已有版本且文件哈希吻合 → cache_hit 返回（零 API 调用）；
    - 产物：raw mp3 落 raw 资产（.raw.mp3，留证）+ 规范化 WAV 进 audio_imports/；
    - 回执：版本号/哈希/ffprobe 实测时长/trace_id/request snapshot（脱敏）。
    失败 → VoiceProviderError structured（本层不再包装）。"""
    text = str(text or "").strip()
    if not text:
        raise VoiceProviderError("narration_invalid", "旁白 text 为空（拒绝合成空音频）")
    if len(text) > NARRATION_TEXT_MAX_CHARS:
        raise VoiceProviderError(
            "narration_invalid",
            f"旁白 text 过长（{len(text)} > {NARRATION_TEXT_MAX_CHARS}）——"
            "cue 是短句，超长请拆句（C2 不硬塞原则）")
    cue_id = str(cue_id or "").strip()
    if not cue_id:
        raise VoiceProviderError("narration_invalid", "cue_id 必填")
    # 🔴 44C 防御：LLM 可能显式传 null——None 必须归一到默认值（不能透传给
    # adapter 的 float()/binding 造成 TypeError/空 voice_id）
    voice_id = voice_id or DEFAULT_NARRATOR_VOICE
    speed = DEFAULT_NARRATOR_SPEED if speed is None else float(speed)
    vol = 1.0 if vol is None else float(vol)
    pitch = 0 if pitch is None else int(pitch)

    pdir = Path(project_dir)
    store = NarrationStore(pdir)
    key = narration_idempotency_key(text=text, model=model, voice_id=voice_id,
                                    speed=speed, vol=vol, pitch=pitch,
                                    language_boost=language_boost)
    if not force_new:
        hit = store.find_by_key(cue_id, key)
        if hit is not None:
            f = pdir / "output" / hit.audio_import_path
            if f.exists() and sha256_file(f) == hit.audio_sha256:
                hit.cache_hit = True
                return {"ok": True, "cache_hit": True, "version": hit.to_dict()}
            # 文件丢失/被换 → 缓存失效，走重新合成（诚实，不假装命中）

    pack = VoiceGenerationPack(
        dialogue_id=f"narration:{cue_id}",
        dialogue_fingerprint=key,
        speaker_character_name="narrator",
        text=text,
        language_boost=language_boost,
        provider_binding={"default_model": model, "provider_voice_id": voice_id},
        synthesis_defaults={"speed": speed, "vol": vol, "pitch": pitch},
        generation_basis={"domain": "44c_narration", "cue_id": cue_id},
    )
    adapter = adapter or MiniMaxVoiceAdapter()
    result = await adapter.synthesize(pack)

    version_id = f"nver_{uuid.uuid4().hex[:8]}"
    imp_rel = f"{NARRATION_IMPORT_SUBDIR}/{cue_id}__{version_id}.wav"
    imp_abs = pdir / "output" / imp_rel
    if imp_abs.exists():
        raise VoiceProviderError("narration_write_conflict",
                                 f"产物路径已存在（id 分配异常）: {imp_abs.name}")
    normalize_to_production_wav(result["audio"], imp_abs, src_suffix=".mp3")
    raw_rel = f"{NARRATION_IMPORT_SUBDIR}/.raw/{cue_id}__{version_id}.raw.mp3"
    raw_abs = pdir / "output" / raw_rel
    raw_abs.parent.mkdir(parents=True, exist_ok=True)
    raw_abs.write_bytes(result["audio"])

    probe = ffprobe_audio(str(imp_abs))
    version = NarrationCueVersion(
        version_id=version_id, cue_id=cue_id, text=text,
        idempotency_key=key, provider="minimax", model=model,
        voice_id=voice_id, speed=speed, vol=vol, pitch=pitch,
        language_boost=language_boost,
        audio_import_path=imp_rel.replace("\\", "/"),
        audio_sha256=sha256_file(imp_abs),
        duration_probe=_strip_secrets(probe or {}),
        provider_trace_id=str(result.get("trace_id") or ""),
        provider_request_snapshot=_strip_secrets(result.get("request_snapshot") or {}),
        provider_extra=_strip_secrets(result.get("provider_extra_info") or {}),
        latency_ms=int(result.get("latency_ms") or 0),
        cache_hit=False,
    )
    store.append(version)
    return {"ok": True, "cache_hit": False, "version": version.to_dict()}
