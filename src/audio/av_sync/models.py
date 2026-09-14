# src/audio/av_sync/models.py
# Audio V1 — Phase A2：AV Sync canonical 模型（16.md §4/§5/§10/§12/§13/§14）。
#
# 链路（16.md §0）：
#   selected DialogueAudioVersion（A1 复用，绝不重建）
#   + authoritative Visual Video（final_video 指针 → video_versions，只读复用）
#   → AVSyncReadiness（derived，绝不写回 Shot）
#   → DialogueTimingPlan（derived / deterministic，时长全部 ffprobe 实测）
#   → LipSyncGenerationPack（derived / ephemeral / 无 business ID）
#   → LipSync Adapter（sync_labs 先行；本地模型留 seam）
#   → DialogueVideoSyncVersion（artifact + provenance + review state）
#
# 🔴 freeze 边界：A1（voice/dialogue_audio 语义）与 Visual V1（Binding/Adapter/
#   generation_basis）零语义改动。VideoVersion 无 generation_basis 字段 → 视频身份
#   用「文件内容 sha256」＝source_video_fingerprint（比 basis 指纹更强的身份）。
# 🔴 指纹族 = sha256(canonical_json(语义子集))[:8]，与 A1/visual_profile 同族。
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from src.audio.models import semantic_fingerprint

AV_SYNC_BASIS_SCHEMA = "av_sync_generation_basis_v1"
DIALOGUE_VIDEO_SYNC_SCHEMA_VERSION = 1

# ============ AVSyncReadiness（16.md §4，derived only / 零副作用）============
AV_SYNC_READY = "ready"

# —— audio 侧（复用 A1 真相；AUDIO_STALE = A1 freshness 非 current）——
AV_SYNC_MISSING_AUDIO = "missing_audio"            # 无 selected DialogueAudioVersion
AV_SYNC_AUDIO_NOT_APPROVED = "audio_not_approved"  # selected 存在但未 approve
AV_SYNC_AUDIO_STALE = "audio_stale"                # A1 freshness ≠ current（文本/声线已变）

# —— video 侧（authoritative video 只读探测）——
AV_SYNC_MISSING_VIDEO = "missing_video"            # 无 authoritative video（指针缺失/无匹配版本）
AV_SYNC_VIDEO_NOT_READY = "video_not_ready"        # 状态非 DONE / 本地文件缺失 / ffprobe 失败
AV_SYNC_UNSUPPORTED_VIDEO = "unsupported_video"    # 无视频流 / 时长非正（不可承载 lip-sync）

# —— speaker target 侧（§10/§11：谁说话≠哪张脸，不得混淆/不得猜）——
AV_SYNC_NO_SPEAKER = "no_speaker"
AV_SYNC_AMBIGUOUS_SPEAKER = "ambiguous_speaker"
AV_SYNC_MULTI_FACE_TARGET_REQUIRED = "multi_face_target_required"  # 多角色镜未显式裁决目标

# —— duration 侧（§6/§7/§8）——
AV_SYNC_VIDEO_TOO_SHORT = "video_too_short"        # audio 放不进 video（fail closed 默认）

# —— provider 侧 ——
AV_SYNC_UNSUPPORTED_PROVIDER = "unsupported_provider"

# §17：V1 默认模型 lipsync-2；model 必须经 config/caller 传入（seam），不做
# 未经真实测试的 heuristic routing；provider/model 进 generation_basis。
LIPSYNC_DEFAULT_MODEL = "lipsync-2"

BLOCKING_READINESS = (
    AV_SYNC_MISSING_AUDIO, AV_SYNC_AUDIO_NOT_APPROVED, AV_SYNC_AUDIO_STALE,
    AV_SYNC_MISSING_VIDEO, AV_SYNC_VIDEO_NOT_READY, AV_SYNC_UNSUPPORTED_VIDEO,
    AV_SYNC_NO_SPEAKER, AV_SYNC_AMBIGUOUS_SPEAKER,
    AV_SYNC_MULTI_FACE_TARGET_REQUIRED, AV_SYNC_VIDEO_TOO_SHORT,
    AV_SYNC_UNSUPPORTED_PROVIDER,
)

# AV_SYNC_VIDEO_STALE（§4 枚举含之）：语义 = 「已存在 selected sync version，但其
# source video / timing / target 已过时」。这不是生成阻断（对新视频显式重新生成
# 合法），故作为 readiness.existing_sync_freshness="stale" 暴露（16.md 允许命名
# 适配项目 style；语义在报告钉死），生成门不吸收它。

# ============ Duration adaptation policy（16.md §6/§7/§8/§9）============
ADAPTATION_PAD_AUDIO = "pad_audio"        # 默认：sync_input = [lead][audio][tail] ≈ video 时长
ADAPTATION_REMAP_VIDEO = "remap_video"    # 仅 caller 显式；audio>video 时才允许进入 remap 协商
DEFAULT_SPEECH_START_MS = 0               # §6：第一版默认 0，接口保留
MIN_TAIL_HANDLE_MS = 0                    # §9 required_video_duration = start + audio + tail_handle
SYNC_INPUT_TOLERANCE_MS = 1               # pad 后与 video 时长的允许偏差（采样对齐）

# ============ Speaker target（16.md §10/§11）============
SPEAKER_TARGET_SINGLE_VISIBLE = "single_visible_speaker"   # 单人物镜：允许自动
SPEAKER_TARGET_AUTO_DETECT = "auto_detect"                 # 多角色镜需 caller 显式选择
SPEAKER_TARGET_MANUAL = "manual_target"                    # 显式 frame/coordinates


def fingerprint8(snapshot: Dict[str, Any]) -> str:
    return semantic_fingerprint(snapshot)


# ============ DialogueTimingPlan（16.md §5，derived / ephemeral / deterministic）============

@dataclass
class DialogueTimingPlan:
    """时间关系投影。audio/video duration 全部来自真实媒体 ffprobe，
    不使用数据库猜测值（seconds_returned 等仅可作交叉核对，不进 plan）。"""
    dialogue_id: str
    shot_id: str
    audio_version_id: str
    audio_duration_ms: int
    video_artifact_id: str
    video_duration_ms: int
    speech_start_ms: int
    speech_end_ms: int
    lead_in_ms: int
    tail_silence_ms: int
    required_video_duration_ms: int
    duration_relation: str            # audio_fits | video_too_short
    adaptation_mode: str              # pad_audio | remap_video
    playback_ratio: float = 1.0

    def to_dict(self) -> dict:
        return {
            "dialogue_id": self.dialogue_id,
            "shot_id": self.shot_id,
            "audio_version_id": self.audio_version_id,
            "audio_duration_ms": self.audio_duration_ms,
            "video_artifact_id": self.video_artifact_id,
            "video_duration_ms": self.video_duration_ms,
            "speech_start_ms": self.speech_start_ms,
            "speech_end_ms": self.speech_end_ms,
            "lead_in_ms": self.lead_in_ms,
            "tail_silence_ms": self.tail_silence_ms,
            "required_video_duration_ms": self.required_video_duration_ms,
            "duration_relation": self.duration_relation,
            "adaptation_mode": self.adaptation_mode,
            "playback_ratio": self.playback_ratio,
        }


DURATION_FITS = "audio_fits"
DURATION_TOO_SHORT = "video_too_short"


def build_timing_plan(*, dialogue_id: str, shot_id: str,
                      audio_version_id: str, audio_duration_ms: int,
                      video_artifact_id: str, video_duration_ms: int,
                      speech_start_ms: int = DEFAULT_SPEECH_START_MS,
                      adaptation_mode: str = ADAPTATION_PAD_AUDIO,
                      min_tail_handle_ms: int = MIN_TAIL_HANDLE_MS) -> DialogueTimingPlan:
    """确定性时长适配策略（16.md §6/§7/§9）：

    - audio 放得下（start + audio ≤ video）：保留完整视觉镜头——不截 video；
      tail = video − start − audio（垫到等长）；required = start + audio + tail_handle。
    - audio 放不下：duration_relation = video_too_short，required = start + audio
      + tail_handle——生产信号（镜头视觉长度不足），**绝不自动截对白/loop/bounce/
      加速**（§7 禁止清单）；remap_video 仅 caller 显式（§8）。
    - lead_in = speech_start（§6 语义：说话前静音）；tail_silence = 垫尾。
    """
    if adaptation_mode not in (ADAPTATION_PAD_AUDIO, ADAPTATION_REMAP_VIDEO):
        raise ValueError(f"未知 adaptation_mode: {adaptation_mode!r}")
    start = max(0, int(speech_start_ms))
    audio_ms = int(audio_duration_ms)
    video_ms = int(video_duration_ms)
    fits = (start + audio_ms) <= video_ms
    if adaptation_mode == ADAPTATION_REMAP_VIDEO and fits:
        # remap 仅在 audio>video 时有意义；放得下仍按 pad（playback_ratio=1）
        adaptation_mode = ADAPTATION_PAD_AUDIO
    tail = (video_ms - start - audio_ms) if fits else 0
    required = start + audio_ms + max(0, int(min_tail_handle_ms))
    relation = DURATION_FITS if fits else DURATION_TOO_SHORT
    ratio = 1.0
    if adaptation_mode == ADAPTATION_REMAP_VIDEO and video_ms > 0:
        ratio = round(video_ms / audio_ms, 6) if audio_ms > 0 else 1.0
    return DialogueTimingPlan(
        dialogue_id=str(dialogue_id), shot_id=str(shot_id),
        audio_version_id=str(audio_version_id), audio_duration_ms=audio_ms,
        video_artifact_id=str(video_artifact_id), video_duration_ms=video_ms,
        speech_start_ms=start,
        speech_end_ms=start + audio_ms,
        lead_in_ms=start,
        tail_silence_ms=max(0, tail),
        required_video_duration_ms=required,
        duration_relation=relation,
        adaptation_mode=adaptation_mode,
        playback_ratio=ratio,
    )


# ============ LipSyncSpeakerTarget（16.md §10/§11）============

@dataclass
class LipSyncSpeakerTarget:
    """画面里哪张脸要动（与「谁在说话」是两个问题）。mode 必须显式可追溯——
    多角色镜默认不猜（MULTI_FACE_TARGET_REQUIRED），auto_detect 必须是 caller
    显式选择并进 generation_basis（§11）。"""
    mode: str
    frame_number: Optional[int] = None
    coordinates: Optional[Dict[str, Any]] = None   # provider-specific 扩展（如 bbox）

    def __post_init__(self) -> None:
        if self.mode not in (SPEAKER_TARGET_SINGLE_VISIBLE, SPEAKER_TARGET_AUTO_DETECT,
                             SPEAKER_TARGET_MANUAL):
            raise ValueError(f"未知 speaker target mode: {self.mode!r}")
        if self.mode == SPEAKER_TARGET_MANUAL and self.coordinates is None \
                and self.frame_number is None:
            raise ValueError("manual_target 必须提供 frame_number 或 coordinates")

    def fingerprint(self) -> str:
        return fingerprint8({
            "mode": self.mode,
            "frame_number": self.frame_number,
            "coordinates": self.coordinates,
        })

    def to_dict(self) -> dict:
        return {"mode": self.mode, "frame_number": self.frame_number,
                "coordinates": self.coordinates}


def resolve_speaker_target(shot_characters: list,
                           explicit_target: Optional[LipSyncSpeakerTarget] = None
                           ) -> tuple:
    """§11 V1 规则（fail closed）：
    - 单人物镜：默认 single_visible_speaker（允许自动 targeting）；
    - 多人物镜：无显式 manual/auto_detect → multi_face_target_required；
    - character_name 绝不伪装成 face coordinates。
    返回 (target | None, code)。"""
    chars = [str(c or "").strip() for c in (shot_characters or []) if str(c or "").strip()]
    if explicit_target is not None:
        return explicit_target, "explicit"
    if len(chars) <= 1:
        return LipSyncSpeakerTarget(mode=SPEAKER_TARGET_SINGLE_VISIBLE), "single_visible"
    return None, "multi_face_target_required"


# ============ LipSyncGenerationPack（16.md §12，derived / ephemeral）============

LIPSYNC_PACK_SCHEMA = "lipsync_generation_pack_v1"


@dataclass
class LipSyncGenerationPack:
    """provider 面前的中性投影（16.md §12）。derived / ephemeral / read-only /
    无 business ID / 不持久化。同输入 → 同 pack；不含 clock / random UUID /
    temporary path（working 文件路径不进 semantic 字段）。"""
    shot_id: str
    dialogue_id: str
    speaker_character_name: str
    source_video: Dict[str, Any] = field(default_factory=dict)
    source_audio: Dict[str, Any] = field(default_factory=dict)
    timing: Dict[str, Any] = field(default_factory=dict)
    speaker_target: Dict[str, Any] = field(default_factory=dict)
    provider_binding: Dict[str, Any] = field(default_factory=dict)
    generation_basis: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema": LIPSYNC_PACK_SCHEMA,
            "shot_id": self.shot_id,
            "dialogue_id": self.dialogue_id,
            "speaker_character_name": self.speaker_character_name,
            "source_video": dict(self.source_video),
            "source_audio": dict(self.source_audio),
            "timing": dict(self.timing),
            "speaker_target": dict(self.speaker_target),
            "provider_binding": dict(self.provider_binding),
            "generation_basis": dict(self.generation_basis),
        }


def build_av_sync_basis(*, shot_id: str, dialogue_id: str,
                        source_video_artifact_id: str, source_video_fingerprint: str,
                        dialogue_audio_version_id: str,
                        dialogue_audio_generation_basis_fingerprint: str,
                        speaker_character_name: str,
                        timing_fingerprint: str, speaker_target_fingerprint: str,
                        provider: str, model: str,
                        provider_options_fingerprint: str) -> dict:
    """generation_basis（16.md §13）：actual dependencies only——绝不 hash
    project.json / 整份 resources.json / 整份 dialogue_audio.json；无关 Shot /
    无关角色变化不影响本 basis。"""
    return {
        "schema": AV_SYNC_BASIS_SCHEMA,
        "shot_id": str(shot_id),
        "dialogue_id": str(dialogue_id),
        "source_video_artifact_id": str(source_video_artifact_id),
        "source_video_fingerprint": str(source_video_fingerprint),
        "dialogue_audio_version_id": str(dialogue_audio_version_id),
        "dialogue_audio_generation_basis_fingerprint":
            str(dialogue_audio_generation_basis_fingerprint),
        "speaker_character_name": str(speaker_character_name),
        "timing_fingerprint": str(timing_fingerprint),
        "speaker_target_fingerprint": str(speaker_target_fingerprint),
        "provider": str(provider),
        "model": str(model),
        "provider_options_fingerprint": str(provider_options_fingerprint),
    }


def timing_fingerprint(plan: DialogueTimingPlan) -> str:
    """timing 语义指纹（§13）：policy 输入 + 关键时长。durations 本身也被
    video/audio 指纹覆盖，此处重复携带为可检查性（inspectability）。"""
    return fingerprint8({
        "speech_start_ms": plan.speech_start_ms,
        "adaptation_mode": plan.adaptation_mode,
        "playback_ratio": plan.playback_ratio,
        "sync_input_duration_ms": plan.video_duration_ms,
        "audio_duration_ms": plan.audio_duration_ms,
    })


def provider_options_fingerprint(options: Optional[Dict[str, Any]]) -> str:
    return fingerprint8(dict(options or {}))


def basis_fingerprint(basis: Optional[Dict[str, Any]]) -> str:
    """任意 basis dict 的整体指纹（audio 侧 basis 指纹用）。"""
    return fingerprint8(dict(basis or {}))


AV_SYNC_BASIS_KEYS = ("schema", "shot_id", "dialogue_id", "source_video_artifact_id",
                      "source_video_fingerprint", "dialogue_audio_version_id",
                      "dialogue_audio_generation_basis_fingerprint",
                      "speaker_character_name", "timing_fingerprint",
                      "speaker_target_fingerprint", "provider", "model",
                      "provider_options_fingerprint")


def basis_mismatches(basis: Optional[dict], expected: Optional[dict]) -> list:
    """§24 freshness 对比：返回不一致键列表（空 = 一致 = CURRENT）。"""
    return [k for k in AV_SYNC_BASIS_KEYS
            if (basis or {}).get(k) != (expected or {}).get(k)]


# ============ DialogueVideoSyncVersion（16.md §14，artifact + provenance）============

@dataclass
class DialogueVideoSyncVersion:
    """一次显式 lip-sync 生成的 artifact metadata。不是新的 Visual canonical——
    消费 authoritative video + approved DialogueAudio，产出可审核的 sync 结果。
    secret 永不落盘（provider_request_snapshot 落盘前脱敏由 adapter 构造保证）。"""
    sync_version_id: str
    shot_id: str
    dialogue_id: str
    speaker_character_name: str
    source_video_artifact_id: str
    source_video_fingerprint: str
    source_audio_version_id: str
    source_audio_basis_fingerprint: str
    provider: str
    model: str
    generation_basis: Dict[str, Any] = field(default_factory=dict)
    provider_generation_id: str = ""
    provider_metadata: Dict[str, Any] = field(default_factory=dict)
    raw_output_path: str = ""                 # provider 原始 mp4（原样留存，§22）
    production_output_path: str = ""          # production review mp4（正式音频 mux）
    working_sync_input_path: str = ""         # derived working artifact（信息性，非 semantic）
    video_probe: Dict[str, Any] = field(default_factory=dict)
    audio_probe: Dict[str, Any] = field(default_factory=dict)
    timing: Dict[str, Any] = field(default_factory=dict)
    speaker_target: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "sync_version_id": self.sync_version_id,
            "shot_id": self.shot_id,
            "dialogue_id": self.dialogue_id,
            "speaker_character_name": self.speaker_character_name,
            "source_video_artifact_id": self.source_video_artifact_id,
            "source_video_fingerprint": self.source_video_fingerprint,
            "source_audio_version_id": self.source_audio_version_id,
            "source_audio_basis_fingerprint": self.source_audio_basis_fingerprint,
            "provider": self.provider,
            "model": self.model,
            "generation_basis": dict(self.generation_basis),
            "provider_generation_id": self.provider_generation_id,
            "provider_metadata": dict(self.provider_metadata),
            "raw_output_path": self.raw_output_path,
            "production_output_path": self.production_output_path,
            "working_sync_input_path": self.working_sync_input_path,
            "video_probe": dict(self.video_probe),
            "audio_probe": dict(self.audio_probe),
            "timing": dict(self.timing),
            "speaker_target": dict(self.speaker_target),
            "created_at": self.created_at,
        }


def parse_sync_version(raw: dict) -> DialogueVideoSyncVersion:
    """从落盘 dict 解析（known-field 过滤前向兼容）。身份字段缺失 → fail closed。"""
    if not isinstance(raw, dict):
        raise ValueError("sync version 条目必须是对象")
    for required in ("sync_version_id", "dialogue_id"):
        if required not in raw:
            raise ValueError(f"sync version 缺少 {required}（fail closed）")
    known = {f for f in DialogueVideoSyncVersion.__dataclass_fields__}
    return DialogueVideoSyncVersion(**{k: v for k, v in raw.items() if k in known})
