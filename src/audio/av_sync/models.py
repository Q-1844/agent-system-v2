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
