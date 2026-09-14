# src/audio/models.py
# Audio V1 — Phase A1：Voice Foundation canonical 模型（15.md §4/§9/§10/§18）。
#
# 架构链路（15.md §1）：
#   Dialogue（真相 = Shot.dialogue，Audio 模块只读，绝不复制第二套文本真相）
#   → CharacterVoiceProfile（角色声音身份 canonical，voice_profiles.json）
#   → VoiceGenerationPack（derived / ephemeral / 不持久化，provider 面前的中性投影）
#   → MiniMaxVoiceAdapter（provider 调用）
#   → DialogueAudioVersion（生成 artifact metadata，dialogue_audio.json）
#
# 🔴 角色身份：本项目 Character 无 character_id，全项目身份原语 = character_name
#    （Resources V1 冻结决策先例：src/resources/models.py「Character 例外：继续
#    character_name FK」）。VoiceProfile 通过 character_name 引用 Character，
#    不复制名字以外的新身份（15.md §7：名字可作 display，但本项目 name 即 identity）。
# 🔴 revision / fingerprint = sha256(canonical_json(语义子集))[:8]（同 visual_profile
#    指纹族，P1 §8；不含时间戳，同语义 → 同指纹）。
#    - voice_profile_revision：只覆盖「决定声音是什么」的语义子集（§21 actual
#      dependencies only）——label（display）与 approval_status（生成门，不影响
#      已生成音频的语义）不进指纹。
#    - dialogue_fingerprint：覆盖 {dialogue_id, text, speaker}——文本变或说话人变
#      都必须 STALE（§21）。
# 🔴 fail closed：坏 JSON / 未知 schema_version → VoiceSchemaError，绝不静默重置
#    （与 visual_profile 同口径——静默会把「文件坏了」伪装成「从未设置」）。
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

VOICE_PROFILE_SCHEMA_VERSION = 1
DIALOGUE_AUDIO_SCHEMA_VERSION = 1

# 15.md §4：第一阶段只实现 source_kind=system；designed/cloned 是合法枚举占位。
VOICE_SOURCE_KINDS = ("system", "designed", "cloned")
VOICE_APPROVAL_DRAFT = "draft"
VOICE_APPROVAL_APPROVED = "approved"

MINIMAX_PROVIDER = "minimax"
MINIMAX_DEFAULT_MODEL = "speech-2.8-turbo"   # 15.md §0：本阶段正式默认（用户指示：测试一律 turbo）

# VoiceGenerationReadiness（15.md §8，derived only，绝不写 canonical status 字段）
VOICE_READINESS_UNBOUND = "unbound"        # speaker 角色没有 VoiceProfile
VOICE_READINESS_NOT_READY = "not_ready"    # 有 profile 但不可确定性解析
VOICE_READINESS_READY = "ready"

# DialogueAudioFreshness（15.md §21，derived only）
AUDIO_FRESHNESS_MISSING = "missing"        # 没有 selected audio
AUDIO_FRESHNESS_CURRENT = "current"        # generation_basis 与当前 Dialogue+VoiceProfile 一致
AUDIO_FRESHNESS_STALE = "stale"            # 任一 actual dependency 变化

# generation_basis schema 标记（inspectable；15.md §10 basis 必须可检查）
VOICE_GENERATION_BASIS_SCHEMA = "voice_generation_basis_v1"

# MiniMax voice_setting 合法范围（provider 请求映射前的双重守卫；
# 越界在 Profile 校验层已挡，这里防 pack 被构造绕过）。
VOICE_SPEED_MIN, VOICE_SPEED_MAX = 0.5, 2.0
VOICE_VOL_MIN, VOICE_VOL_MAX = 0.1, 10.0
VOICE_PITCH_MIN, VOICE_PITCH_MAX = -12, 12


class VoiceSchemaError(Exception):
    """voice_profiles.json / dialogue_audio.json 坏 JSON / 字段缺失 / schema 未知
    → 明确错误，绝不静默重置。"""


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def semantic_fingerprint(snapshot: Dict[str, Any]) -> str:
    """确定性语义指纹：sha256(canonical_json(snapshot))[:8]。不含时间戳。"""
    return hashlib.sha256(_canonical_json(snapshot).encode("utf-8")).hexdigest()[:8]


def dialogue_fingerprint(dialogue_id: str, text: str, speaker: str) -> str:
    """对话依赖指纹（15.md §21）：文本变 或 说话人变 → 指纹变 → STALE。"""
    return semantic_fingerprint(
        {"dialogue_id": str(dialogue_id), "text": str(text), "speaker": str(speaker)})


# ============ CharacterVoiceProfile（15.md §4，canonical）============

def _check_speed(speed: Any) -> float:
    try:
        v = float(speed)
    except (TypeError, ValueError):
        raise VoiceSchemaError(f"speed 必须是数值，收到: {speed!r}")
    if not (VOICE_SPEED_MIN <= v <= VOICE_SPEED_MAX):
        raise VoiceSchemaError(
            f"speed 超出 MiniMax 合法范围 [{VOICE_SPEED_MIN}, {VOICE_SPEED_MAX}]: {v}")
    return v


def _check_vol(vol: Any) -> float:
    try:
        v = float(vol)
    except (TypeError, ValueError):
        raise VoiceSchemaError(f"vol 必须是数值，收到: {vol!r}")
    if not (VOICE_VOL_MIN <= v <= VOICE_VOL_MAX):
        raise VoiceSchemaError(
            f"vol 超出 MiniMax 合法范围 ({VOICE_VOL_MIN}, {VOICE_VOL_MAX}]: {v}")
    return v


def _check_pitch(pitch: Any) -> int:
    try:
        v = int(pitch)
    except (TypeError, ValueError):
        raise VoiceSchemaError(f"pitch 必须是整数（半音），收到: {pitch!r}")
    if not (VOICE_PITCH_MIN <= v <= VOICE_PITCH_MAX):
        raise VoiceSchemaError(
            f"pitch 超出 MiniMax 合法范围 [{VOICE_PITCH_MIN}, {VOICE_PITCH_MAX}]: {v}")
    return v


@dataclass
class CharacterVoiceProfile:
    """某个 Character 当前批准使用的声音身份（15.md §4）。
    provider 特定字段（provider/provider_voice_id/default_model）允许存在——
    它们属于 VoiceProfile，不属于 Dialogue（15.md §5）。"""
    voice_profile_id: str
    character_name: str
    label: str = ""
    provider: str = MINIMAX_PROVIDER
    provider_voice_id: str = ""
    default_model: str = MINIMAX_DEFAULT_MODEL
    language_boost: str = ""
    speed: float = 1.0
    vol: float = 1.0
    pitch: int = 0
    source_kind: str = "system"
    approval_status: str = VOICE_APPROVAL_DRAFT
    schema_version: int = VOICE_PROFILE_SCHEMA_VERSION
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not str(self.voice_profile_id or "").strip():
            raise VoiceSchemaError("voice_profile_id 不能为空")
        if not str(self.character_name or "").strip():
            raise VoiceSchemaError("character_name 不能为空（VoiceProfile 必须 FK 到角色）")
        self.voice_profile_id = str(self.voice_profile_id).strip()
        self.character_name = str(self.character_name).strip()
        self.label = str(self.label or "").strip()
        self.provider = str(self.provider or "").strip()
        if not self.provider:
            raise VoiceSchemaError("provider 不能为空")
        self.provider_voice_id = str(self.provider_voice_id or "").strip()
        self.default_model = str(self.default_model or "").strip()
        if not self.default_model:
            raise VoiceSchemaError("default_model 不能为空")
        self.language_boost = str(self.language_boost or "").strip()
        self.speed = _check_speed(self.speed)
        self.vol = _check_vol(self.vol)
        self.pitch = _check_pitch(self.pitch)
        if self.source_kind not in VOICE_SOURCE_KINDS:
            raise VoiceSchemaError(
                f"source_kind 必须是 {VOICE_SOURCE_KINDS} 之一，收到: {self.source_kind!r}")
        if self.approval_status not in (VOICE_APPROVAL_DRAFT, VOICE_APPROVAL_APPROVED):
            raise VoiceSchemaError(
                f"approval_status 必须是 {VOICE_APPROVAL_DRAFT}/{VOICE_APPROVAL_APPROVED}，"
                f"收到: {self.approval_status!r}")
        # provider_voice_id 允许为空（§8：profile 存在但 voice_id missing →
        # readiness NOT_READY；approve 门另挡空 id 批准）——schema 不代行业务门。

    # ---- 语义子集（15.md §21 actual dependencies only：声音语义；不含 label /
    #      approval_status / 时间戳 / schema_version——它们不影响已生成音频的身份）----
    def voice_semantics_snapshot(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_voice_id": self.provider_voice_id,
            "default_model": self.default_model,
            "language_boost": self.language_boost,
            "speed": self.speed,
            "vol": self.vol,
            "pitch": self.pitch,
        }

    def revision(self) -> str:
        """OCC revision = 声音语义指纹。语义变化 → revision 变 → 旧音频 STALE。"""
        return semantic_fingerprint(self.voice_semantics_snapshot())

    def synthesis_settings_fingerprint(self) -> str:
        """generation_basis 组成项（15.md §10）。"""
        return semantic_fingerprint({
            "language_boost": self.language_boost,
            "speed": self.speed, "vol": self.vol, "pitch": self.pitch,
        })

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "voice_profile_id": self.voice_profile_id,
            "character_name": self.character_name,
            "label": self.label,
            "provider": self.provider,
            "provider_voice_id": self.provider_voice_id,
            "default_model": self.default_model,
            "language_boost": self.language_boost,
            "speed": self.speed,
            "vol": self.vol,
            "pitch": self.pitch,
            "source_kind": self.source_kind,
            "approval_status": self.approval_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def parse_voice_profile(raw: dict) -> CharacterVoiceProfile:
    """从落盘 dict 解析。schema 未知 / 身份字段缺失 → fail closed。"""
    if not isinstance(raw, dict):
        raise VoiceSchemaError("voice profile 条目必须是对象")
    v = raw.get("schema_version")
    if v != VOICE_PROFILE_SCHEMA_VERSION:
        raise VoiceSchemaError(
            f"voice profile schema_version={v!r} 不受支持（当前 {VOICE_PROFILE_SCHEMA_VERSION}）"
            "——fail closed，不静默迁移")
    for required in ("voice_profile_id", "character_name"):
        if required not in raw:
            raise VoiceSchemaError(f"voice profile 缺少 {required}（fail closed）")
    return CharacterVoiceProfile(
        voice_profile_id=str(raw.get("voice_profile_id") or ""),
        character_name=str(raw.get("character_name") or ""),
        label=str(raw.get("label") or ""),
        provider=str(raw.get("provider") or MINIMAX_PROVIDER),
        provider_voice_id=str(raw.get("provider_voice_id") or ""),
        default_model=str(raw.get("default_model") or MINIMAX_DEFAULT_MODEL),
        language_boost=str(raw.get("language_boost") or ""),
        speed=raw.get("speed", 1.0),
        vol=raw.get("vol", 1.0),
        pitch=raw.get("pitch", 0),
        source_kind=str(raw.get("source_kind") or "system"),
        approval_status=str(raw.get("approval_status") or VOICE_APPROVAL_DRAFT),
        schema_version=VOICE_PROFILE_SCHEMA_VERSION,
        created_at=float(raw.get("created_at") or 0.0) or time.time(),
        updated_at=float(raw.get("updated_at") or 0.0) or time.time(),
    )


# ============ VoiceGenerationPack（15.md §9，derived / ephemeral / 不持久化）============

@dataclass
class VoiceGenerationPack:
    """provider 面前的 model-neutral 投影。derived / read-only / 无 business ID /
    不持久化 / 不写回 canonical。同输入 → 同 pack（determinism，15.md §45）。"""
    dialogue_id: str
    dialogue_fingerprint: str
    speaker_character_name: str
    text: str
    language: str = ""            # Dialogue 级 language 字段当前 canonical 不存在（占位 seam）
    delivery_intent: str = ""     # 15.md §11：占位 seam，本阶段绝不解释/改写
    language_boost: str = ""
    voice_profile_id: str = ""
    voice_profile_revision: str = ""
    provider_binding: Dict[str, Any] = field(default_factory=dict)
    synthesis_defaults: Dict[str, Any] = field(default_factory=dict)
    generation_basis: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "dialogue_id": self.dialogue_id,
            "dialogue_fingerprint": self.dialogue_fingerprint,
            "speaker_character_name": self.speaker_character_name,
            "text": self.text,
            "language": self.language,
            "delivery_intent": self.delivery_intent,
            "language_boost": self.language_boost,
            "voice_profile_id": self.voice_profile_id,
            "voice_profile_revision": self.voice_profile_revision,
            "provider_binding": dict(self.provider_binding),
            "synthesis_defaults": dict(self.synthesis_defaults),
            "generation_basis": dict(self.generation_basis),
        }


# ============ DialogueAudioVersion（15.md §18，生成 artifact metadata）============

@dataclass
class DialogueAudioVersion:
    """一次显式生成的 artifact metadata。不是 canonical Dialogue，也不是 VoiceProfile。
    严禁包含 API key（provider_request_snapshot 落盘前必须脱敏）。"""
    audio_version_id: str
    dialogue_id: str
    voice_profile_id: str
    voice_profile_revision: str
    provider: str
    model: str
    provider_voice_id: str
    # 生成时的说话人裁决（含多角色镜的显式裁决留痕；
    # freshness 重算 basis 必须能复现同一裁决）
    speaker_character_name: str = ""
    generation_basis: Dict[str, Any] = field(default_factory=dict)
    provider_request_snapshot: Dict[str, Any] = field(default_factory=dict)
    provider_extra_info: Dict[str, Any] = field(default_factory=dict)
    raw_asset: Dict[str, Any] = field(default_factory=dict)      # {path, audio_spec, ffprobe}
    production_wav: Dict[str, Any] = field(default_factory=dict) # {path, audio_spec, ffprobe}
    word_timestamps: List[Dict[str, Any]] = field(default_factory=list)
    word_timestamps_note: str = ""    # provider 未返回 → "word_timestamps_unavailable"（§14）
    technical_probe: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "audio_version_id": self.audio_version_id,
            "dialogue_id": self.dialogue_id,
            "speaker_character_name": self.speaker_character_name,
            "voice_profile_id": self.voice_profile_id,
            "voice_profile_revision": self.voice_profile_revision,
            "provider": self.provider,
            "model": self.model,
            "provider_voice_id": self.provider_voice_id,
            "generation_basis": dict(self.generation_basis),
            "provider_request_snapshot": dict(self.provider_request_snapshot),
            "provider_extra_info": dict(self.provider_extra_info),
            "raw_asset": dict(self.raw_asset),
            "production_wav": dict(self.production_wav),
            "word_timestamps": list(self.word_timestamps),
            "word_timestamps_note": self.word_timestamps_note,
            "technical_probe": dict(self.technical_probe),
            "created_at": self.created_at,
        }


def parse_audio_version(raw: dict) -> DialogueAudioVersion:
    """从落盘 dict 解析（known-field 过滤前向兼容，同 core/models 风格）。
    身份字段缺失 → fail closed（artifact 没有 identity 就不能作 production 消费依据）。"""
    if not isinstance(raw, dict):
        raise VoiceSchemaError("audio version 条目必须是对象")
    for required in ("audio_version_id", "dialogue_id"):
        if required not in raw:
            raise VoiceSchemaError(f"audio version 缺少 {required}（fail closed）")
    known = {f for f in DialogueAudioVersion.__dataclass_fields__}
    filtered = {k: v for k, v in raw.items() if k in known}
    return DialogueAudioVersion(**filtered)
