# src/audio/av_sync/av_readiness.py
# Audio V1 — Phase A2：AVSyncReadiness（16.md §4）。
#
# 🔴 纯派生 / 零副作用：连续调用零文件变化（绝不写回 Shot、绝不持久化 status、
#    绝不自动 generate）。复用两套既有真相，只读：
#    - audio 侧 = A1 VoiceService（readiness / freshness / dialogue_audio store）
#    - video 侧 = av_truth.resolve_video_truth（final_video 指针 → ffprobe 实测）
# 🔴 命名适配说明（16.md 允许）：§4 枚举的 VIDEO_STALE 语义 = 「已存在 selected
#    sync version 但其 source video/timing/target 已过时」——这是对既有 sync 的
#    陈旧信号而非生成阻断（对新视频显式重新生成合法），故经
#    existing_sync_freshness 暴露，生成门（blocking status）不吸收它。
# 🔴 检查顺序（fail fast，语义分层）：speaker → audio → video → target →
#    duration → provider。
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from src.audio.av_sync.av_truth import VideoProbeError, resolve_video_truth
from src.audio.av_sync.models import (
    ADAPTATION_PAD_AUDIO,
    AV_SYNC_AMBIGUOUS_SPEAKER,
    AV_SYNC_AUDIO_NOT_APPROVED,
    AV_SYNC_AUDIO_STALE,
    AV_SYNC_MISSING_AUDIO,
    AV_SYNC_MISSING_VIDEO,
    AV_SYNC_MULTI_FACE_TARGET_REQUIRED,
    AV_SYNC_NO_SPEAKER,
    AV_SYNC_READY,
    AV_SYNC_UNSUPPORTED_PROVIDER,
    AV_SYNC_UNSUPPORTED_VIDEO,
    AV_SYNC_VIDEO_NOT_READY,
    AV_SYNC_VIDEO_TOO_SHORT,
    DEFAULT_SPEECH_START_MS,
    build_timing_plan,
    resolve_speaker_target,
)

# A2 支持的 lip-sync provider（§16/§36：canonical 不写死；本地模型未来在此扩展）
SUPPORTED_LIPSYNC_PROVIDERS = ("sync_labs",)


def compute_av_sync_readiness(project, audio_service, dialogue_id: str, *,
                              speaker: Optional[str] = None,
                              explicit_speaker_target=None,
                              speech_start_ms: int = DEFAULT_SPEECH_START_MS,
                              adaptation_mode: str = ADAPTATION_PAD_AUDIO,
                              provider: str = "sync_labs",
                              existing_sync: Optional[Dict[str, Any]] = None) -> dict:
    """AVSyncReadiness（derived only）。project = src.core.models.Project。

    返回 {status, code, detail, dialogue_id, speaker, speaker_target,
          timing_plan, audio, video, existing_sync}。
    status = ready 或 blocking code；existing_sync_freshness 独立暴露
    （missing/current/stale + stale_reasons）。
    """
    did = str(dialogue_id)

    def blocked(code: str, detail: str = "", **extra) -> dict:
        return {"dialogue_id": did, "status": code, "code": code,
                "detail": detail, "speaker": None, "speaker_target": None,
                "timing_plan": None, "audio": None, "video": None,
                "existing_sync_freshness": (existing_sync or {}).get("freshness"),
                "existing_sync_stale_reasons": (existing_sync or {}).get("stale_reasons", []),
                **extra}

    # ---- 1. speaker（A1 已解决「谁在说话」；解析失败直接映射）----
    a1 = audio_service.readiness(did, speaker)
    if a1["status"] != "ready":
        if a1["code"] == "no_speaker":
            return blocked(AV_SYNC_NO_SPEAKER, a1["detail"])
        if a1["code"] == "ambiguous_speaker":
            return blocked(AV_SYNC_AMBIGUOUS_SPEAKER, a1["detail"])
        # dialogue 不存在/空台词/角色无 profile 等 → 不可能有正式 audio
        return blocked(AV_SYNC_MISSING_AUDIO,
                       f"audio 侧不可用（A1 readiness: {a1['code']}）")
    spk = a1["speaker"]

    # ---- 2. audio（A1 真相复用：selected → approved → freshness → 实测时长）----
    entry = audio_service.audio_store.get_entry(audio_service.audio_store.load_doc(), did)
    selected_id = entry.get("selected_audio_version_id")
    if not selected_id:
        return blocked(AV_SYNC_MISSING_AUDIO, "没有 selected DialogueAudioVersion")
    if not entry.get("approved", False):
        return blocked(AV_SYNC_AUDIO_NOT_APPROVED,
                       f"selected audio {selected_id} 未 approve")
    fr = audio_service.freshness(did, speaker)
    if fr.get("status") != "current":
        return blocked(AV_SYNC_AUDIO_STALE,
                       f"selected audio {selected_id} 已 stale（{fr.get('code')}）"
                       "——先在 A1 显式 regenerate audio")
    av = next((v for v in entry["audio_versions"]
               if v.audio_version_id == selected_id), None)
    if av is None:
        return blocked(AV_SYNC_MISSING_AUDIO,
                       f"selected audio {selected_id} 记录缺失")
    wav_path = (av.production_wav or {}).get("path")
    if not wav_path or not Path(str(wav_path)).exists():
        return blocked(AV_SYNC_MISSING_AUDIO,
                       f"selected audio {selected_id} production WAV 缺失")
    from src.audio.media_ops import ffprobe_audio
    aprobe = ffprobe_audio(wav_path)
    if not aprobe or not aprobe.get("duration_s"):
        return blocked(AV_SYNC_MISSING_AUDIO,
                       f"selected audio {selected_id} ffprobe 失败（不伪造时长）")
    audio_duration_ms = int(round(aprobe["duration_s"] * 1000))
    audio_info = {
        "audio_version_id": selected_id,
        "production_wav_path": str(wav_path),
        "duration_ms": audio_duration_ms,
        "generation_basis_fingerprint": None,   # Commit B：basis 指纹（av pack 时填）
        "basis": dict(av.generation_basis or {}),
    }

    # ---- 3. video（authoritative truth，只读探测）----
    shot = project.get_shot(did)
    if shot is None:
        return blocked(AV_SYNC_MISSING_VIDEO, f"dialogue_id 不存在: {did}")
    try:
        vtruth = resolve_video_truth(shot)
    except VideoProbeError as e:
        msg = str(e)
        if "本地文件缺失" in msg or "不存在" in msg:
            return blocked(AV_SYNC_VIDEO_NOT_READY, msg)
        return blocked(AV_SYNC_VIDEO_NOT_READY, msg)
    if vtruth is None:
        return blocked(AV_SYNC_MISSING_VIDEO,
                       "无 authoritative video（final_video 指针缺失或无匹配版本；"
                       "绝不 glob/mtime 猜测）")
    if vtruth["status"] != "done":
        return blocked(AV_SYNC_VIDEO_NOT_READY,
                       f"authoritative video {vtruth['artifact_id']} 状态 {vtruth['status']!r}")
    vprobe = vtruth["probe"]
    if vprobe["duration_ms"] <= 0:
        return blocked(AV_SYNC_UNSUPPORTED_VIDEO, "视频时长非正（不可承载 lip-sync）")

    # ---- 4. speaker target（§11：多角色镜不猜，fail closed）----
    target, tcode = resolve_speaker_target(shot.characters, explicit_speaker_target)
    if target is None:
        return blocked(AV_SYNC_MULTI_FACE_TARGET_REQUIRED,
                       "多角色镜未显式指定 speaker target（manual_target 或 "
                       "auto_detect）——绝不静默同步「画面里最大的脸」")
    _ = tcode  # explicit/single_visible 仅 diagnostics 用

    # ---- 5. duration（§6/§7：audio 放不下 → fail closed + required 信号）----
    plan = build_timing_plan(
        dialogue_id=did, shot_id=shot.shot_id,
        audio_version_id=selected_id, audio_duration_ms=audio_duration_ms,
        video_artifact_id=vtruth["artifact_id"], video_duration_ms=vprobe["duration_ms"],
        speech_start_ms=speech_start_ms, adaptation_mode=adaptation_mode)
    if plan.duration_relation == "video_too_short":
        return blocked(AV_SYNC_VIDEO_TOO_SHORT,
                       f"video {vprobe['duration_ms']}ms 不足以承载 audio "
                       f"（start={plan.speech_start_ms}+audio={audio_duration_ms}，"
                       f"required={plan.required_video_duration_ms}ms）——"
                       "不自动截对白/loop/bounce/加速",
                       required_video_duration_ms=plan.required_video_duration_ms)

    # ---- 6. provider（§16：canonical 不写死；A2 仅 sync_labs）----
    if provider not in SUPPORTED_LIPSYNC_PROVIDERS:
        return blocked(AV_SYNC_UNSUPPORTED_PROVIDER,
                       f"provider {provider!r} 未实现（支持: {SUPPORTED_LIPSYNC_PROVIDERS}）")

    return {
        "dialogue_id": did,
        "status": AV_SYNC_READY,
        "code": AV_SYNC_READY,
        "detail": "",
        "speaker": spk,
        "speaker_target": target.to_dict(),
        "timing_plan": plan.to_dict(),
        "audio": audio_info,
        "video": {"artifact_id": vtruth["artifact_id"],
                  "local_path": vtruth["local_path"],
                  "url": vtruth["url"],
                  "duration_ms": vprobe["duration_ms"],
                  "probe": vprobe,
                  "fingerprint": vtruth["fingerprint"]},
        "existing_sync_freshness": (existing_sync or {}).get("freshness"),
        "existing_sync_stale_reasons": (existing_sync or {}).get("stale_reasons", []),
    }
