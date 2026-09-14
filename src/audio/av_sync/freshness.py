# src/audio/av_sync/freshness.py
# Audio V1 — Phase A2：sync freshness（16.md §24/§25）。
#
# MISSING  ：无 selected DialogueVideoSyncVersion（或指针悬空——绝不 glob 顶替）。
# CURRENT  ：stored basis 与当前重算 basis 逐键一致（§24 五项全对）。
# STALE    ：任一 actual dependency 变化（audio 换选/A1 audio stale/video 变/
#            timing policy 变/speaker target 变/provider·model·options 变/
#            production 产物文件缺失）——但 0 provider call（只读重算）。
#
# 🔴 actual dependencies only：绝不 hash project.json / 整份 store；无关 Shot /
#    无关角色变化 → 保持 CURRENT（测试钉死）。
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from src.audio.av_sync.av_truth import VideoProbeError, resolve_video_truth
from src.audio.av_sync.models import (
    ADAPTATION_PAD_AUDIO,
    DEFAULT_SPEECH_START_MS,
    basis_fingerprint,
    basis_mismatches,
    build_timing_plan,
    fingerprint8,
    resolve_speaker_target,
    timing_fingerprint,
)


def compute_sync_freshness(project, audio_service, sync_store, dialogue_id: str, *,
                           speaker: Optional[str] = None,
                           explicit_speaker_target=None,
                           speech_start_ms: int = DEFAULT_SPEECH_START_MS,
                           adaptation_mode: str = ADAPTATION_PAD_AUDIO,
                           provider: str = "sync_labs", model: str = "",
                           provider_options: Optional[Dict[str, Any]] = None) -> dict:
    """DialogueVideoSyncVersion freshness（derived only，0 provider call）。"""
    did = str(dialogue_id)
    base = {"dialogue_id": did, "status": "missing", "stale_reasons": [],
            "selected_sync_version_id": None}
    entry = sync_store.get_entry(sync_store.load_doc(), did)
    selected_id = entry.get("selected_sync_version_id")
    if not selected_id:
        return {**base, "detail": "没有 selected sync version"}
    version = next((v for v in entry["sync_versions"]
                    if v.sync_version_id == selected_id), None)
    if version is None:
        return {**base, "detail": f"selected sync version 不存在: {selected_id}"}
    base["selected_sync_version_id"] = selected_id

    reasons = []

    # ---- 0. production 产物存在性（文件被外部删除 = 不可消费）----
    if not version.production_output_path or \
            not Path(version.production_output_path).exists():
        reasons.append("production_output_missing")

    # ---- 1. video 侧（authoritative truth 重算）----
    shot = project.get_shot(did)
    vtruth = None
    try:
        if shot is not None:
            vtruth = resolve_video_truth(shot)
    except VideoProbeError:
        vtruth = None
    if vtruth is None:
        reasons.append("video_unresolvable")
    else:
        if (vtruth["artifact_id"], vtruth["fingerprint"]) != (
                version.source_video_artifact_id, version.source_video_fingerprint):
            reasons.append("video_changed")

    # ---- 2. audio 侧（A1 真相重算）----
    a_entry = audio_service.audio_store.get_entry(
        audio_service.audio_store.load_doc(), did)
    a_selected = a_entry.get("selected_audio_version_id")
    if not a_selected or a_selected != version.source_audio_version_id:
        reasons.append("selected_audio_changed")
    else:
        av = next((v for v in a_entry["audio_versions"]
                   if v.audio_version_id == a_selected), None)
        if av is None:
            reasons.append("selected_audio_missing")
        else:
            cur_fp = basis_fingerprint(av.generation_basis)
            if cur_fp != version.source_audio_basis_fingerprint:
                reasons.append("audio_basis_changed")
            wav_path = (av.production_wav or {}).get("path")
            if not wav_path or not Path(str(wav_path)).exists():
                reasons.append("audio_production_missing")
            else:
                from src.audio.media_ops import ffprobe_audio
                aprobe = ffprobe_audio(wav_path)
                audio_ms = (int(round(aprobe["duration_s"] * 1000))
                            if aprobe and aprobe.get("duration_s") else 0)
                if audio_ms <= 0:
                    reasons.append("audio_probe_failed")

    # ---- 3. timing / target / provider 侧重算（audio+video 都可解析时才比 basis）----
    audio_side_ok = not any(r in ("selected_audio_changed", "selected_audio_missing",
                                  "audio_production_missing", "audio_probe_failed")
                            for r in reasons)
    if vtruth is not None and audio_side_ok:
        from src.audio.media_ops import ffprobe_audio
        av = next((v for v in a_entry["audio_versions"]
                   if v.audio_version_id == a_selected), None)
        wav_path = (av.production_wav or {}).get("path") if av else None
        aprobe = ffprobe_audio(wav_path) if wav_path else None
        audio_ms = (int(round(aprobe["duration_s"] * 1000))
                    if aprobe and aprobe.get("duration_s") else 0)
        plan = build_timing_plan(
            dialogue_id=did, shot_id=version.shot_id,
            audio_version_id=a_selected or "", audio_duration_ms=audio_ms,
            video_artifact_id=vtruth["artifact_id"],
            video_duration_ms=vtruth["probe"]["duration_ms"],
            speech_start_ms=speech_start_ms, adaptation_mode=adaptation_mode)
        tfp = timing_fingerprint(plan)
        target, _ = resolve_speaker_target(shot.characters if shot else [],
                                           explicit_speaker_target)
        if target is None:
            reasons.append("speaker_target_unresolvable")
        provider_fp = fingerprint8(dict(provider_options or {}))
        expected_basis = {
            "schema": version.generation_basis.get("schema"),
            "shot_id": version.shot_id,
            "dialogue_id": did,
            "source_video_artifact_id": vtruth["artifact_id"],
            "source_video_fingerprint": vtruth["fingerprint"],
            "dialogue_audio_version_id": a_selected,
            "dialogue_audio_generation_basis_fingerprint":
                basis_fingerprint(av.generation_basis) if av else "",
            "speaker_character_name": version.speaker_character_name,
            "timing_fingerprint": tfp,
            "speaker_target_fingerprint": target.fingerprint() if target else "",
            "provider": provider,
            "model": model or version.model,
            "provider_options_fingerprint": provider_fp,
        }
        reasons.extend(basis_mismatches(version.generation_basis, expected_basis))

    if reasons:
        return {**base, "status": "stale", "stale_reasons": sorted(set(reasons)),
                "detail": f"actual dependencies changed: {sorted(set(reasons))}"}
    return {**base, "status": "current", "detail": ""}
