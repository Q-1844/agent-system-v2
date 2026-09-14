# src/audio/av_sync/av_service.py
# Audio V1 — Phase A2：AVSyncService（16.md §21/§22/§25/§26）。
#
# 能力（§26，service 层先行，无 HTTP/UI——§27）：
#   get_av_sync_readiness / build_dialogue_timing_plan / compile_lipsync_generation_pack
#   generate_lipsync / get_sync_version / list_sync_versions / get_sync_freshness
#   select_sync_version / approve_sync_version
#
# 🔴 三段 race guard（§21）：
#   Phase 1（短）：read → validate → snapshot basis → 构造 sync_input（working，
#                以 provider generation id 命名，无时钟/无碰撞）→ 释放；
#   Phase 2（无锁）：provider submit→poll→download（分钟级，绝不持锁等待）；
#   Phase 3（短锁）：sync_store.write_lock 内重算 basis → 不一致即
#                dependency_changed_before_commit（provider 输出保留作 diagnostics
#                移入 evidence/，绝不 attach/selected/冒充 CURRENT）→ 一致才
#                分配正式 sync_version_id + 落库 + 置 selected 指针。
# 🔴 no auto re-lipsync（§25）：一切依赖变化只能显式 regenerate。
# 🔴 selection = 显式指针（generate 指向新版本，approved 独立人类门，§15）。
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from src.audio.av_sync.av_readiness import compute_av_sync_readiness
from src.audio.av_sync.av_truth import resolve_video_truth
from src.audio.av_sync.freshness import compute_sync_freshness
from src.audio.av_sync.models import (
    ADAPTATION_PAD_AUDIO,
    DEFAULT_SPEECH_START_MS,
    LIPSYNC_DEFAULT_MODEL,
    DialogueVideoSyncVersion,
    LipSyncGenerationPack,
    LIPSYNC_PACK_SCHEMA,
    basis_fingerprint,
    build_av_sync_basis,
    build_timing_plan,
    fingerprint8,
    provider_options_fingerprint,
    resolve_speaker_target,
    timing_fingerprint,
)
from src.audio.av_sync.store import DialogueVideoSyncStore
from src.audio.av_sync.sync_media import SyncMediaError, build_sync_input, mux_production_review
from src.audio.media_ops import ffprobe_audio
from src.audio.service import VoiceService


class AVSyncError(Exception):
    """AV Sync service 结构化错误（16.md §20）。detail 安全脱敏，零 secret。"""

    def __init__(self, code: str, message: str, detail: Optional[dict] = None):
        self.code = code
        self.detail = dict(detail or {})
        super().__init__(f"[{code}] {message}")


class AVSyncService:
    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.dir = Path(project_dir)
        self.project_name = project_name or self.dir.name
        self.audio_service = VoiceService(self.dir, self.project_name)   # A1 复用
        self.sync_store = DialogueVideoSyncStore(self.dir, self.project_name)

    # ==================================================================
    # ============ readiness / timing plan / pack（§4/§5/§12）==========
    # ==================================================================

    def get_av_sync_readiness(self, dialogue_id: str, *, speaker: Optional[str] = None,
                              explicit_speaker_target=None,
                              speech_start_ms: int = DEFAULT_SPEECH_START_MS,
                              adaptation_mode: str = ADAPTATION_PAD_AUDIO,
                              provider: str = "sync_labs",
                              model: str = LIPSYNC_DEFAULT_MODEL,
                              provider_options: Optional[dict] = None) -> dict:
        existing = self.get_sync_freshness(
            dialogue_id, speaker=speaker, explicit_speaker_target=explicit_speaker_target,
            speech_start_ms=speech_start_ms, adaptation_mode=adaptation_mode,
            provider=provider, model=model, provider_options=provider_options)
        project = self.audio_service.project_store.load()
        return compute_av_sync_readiness(
            project, self.audio_service, str(dialogue_id), speaker=speaker,
            explicit_speaker_target=explicit_speaker_target,
            speech_start_ms=speech_start_ms, adaptation_mode=adaptation_mode,
            provider=provider, existing_sync=existing)

    def build_dialogue_timing_plan(self, dialogue_id: str, *,
                                   speaker: Optional[str] = None,
                                   speech_start_ms: int = DEFAULT_SPEECH_START_MS,
                                   adaptation_mode: str = ADAPTATION_PAD_AUDIO) -> dict:
        """DialogueTimingPlan（§5）：audio/video duration 全部 ffprobe 实测。"""
        did = str(dialogue_id)
        entry = self.audio_service.audio_store.get_entry(
            self.audio_service.audio_store.load_doc(), did)
        selected_id = entry.get("selected_audio_version_id")
        if not selected_id:
            raise AVSyncError("validation_error", f"dialogue {did} 无 selected audio")
        av = next((v for v in entry["audio_versions"]
                   if v.audio_version_id == selected_id), None)
        if av is None or not (av.production_wav or {}).get("path"):
            raise AVSyncError("validation_error",
                              f"selected audio {selected_id} 记录或 production WAV 缺失")
        aprobe = ffprobe_audio(av.production_wav["path"])
        if not aprobe or not aprobe.get("duration_s"):
            raise AVSyncError("video_probe_failed",
                              "audio ffprobe 失败（不伪造时长）")
        project = self.audio_service.project_store.load()
        shot = project.get_shot(did)
        if shot is None:
            raise AVSyncError("validation_error", f"dialogue_id 不存在: {did}")
        try:
            vtruth = resolve_video_truth(shot)
        except Exception as e:  # noqa: BLE001 —— VideoProbeError 归一
            raise AVSyncError("video_probe_failed", str(e)[:200]) from e
        if vtruth is None:
            raise AVSyncError("validation_error", "无 authoritative video")
        plan = build_timing_plan(
            dialogue_id=did, shot_id=shot.shot_id,
            audio_version_id=selected_id,
            audio_duration_ms=int(round(aprobe["duration_s"] * 1000)),
            video_artifact_id=vtruth["artifact_id"],
            video_duration_ms=vtruth["probe"]["duration_ms"],
            speech_start_ms=speech_start_ms, adaptation_mode=adaptation_mode)
        return {"ok": True, "plan": plan.to_dict(),
                "video_probe": vtruth["probe"], "audio_probe": aprobe}

    def compile_lipsync_generation_pack(self, dialogue_id: str, *,
                                        speaker: Optional[str] = None,
                                        explicit_speaker_target=None,
                                        speech_start_ms: int = DEFAULT_SPEECH_START_MS,
                                        adaptation_mode: str = ADAPTATION_PAD_AUDIO,
                                        provider: str = "sync_labs",
                                        model: str = LIPSYNC_DEFAULT_MODEL,
                                        provider_options: Optional[dict] = None
                                        ) -> LipSyncGenerationPack:
        """编译 LipSyncGenerationPack（§12）。caller 必须先确认 READY；同输入 → 同 pack。"""
        did = str(dialogue_id)
        project = self.audio_service.project_store.load()
        shot = project.get_shot(did)
        if shot is None:
            raise AVSyncError("validation_error", f"dialogue_id 不存在: {did}")
        a_entry = self.audio_service.audio_store.get_entry(
            self.audio_service.audio_store.load_doc(), did)
        selected_id = a_entry.get("selected_audio_version_id")
        av = next((v for v in a_entry["audio_versions"]
                   if v.audio_version_id == selected_id), None) if selected_id else None
        if av is None or not (av.production_wav or {}).get("path"):
            raise AVSyncError("validation_error", "selected audio 缺失或 production WAV 缺失")
        aprobe = ffprobe_audio(av.production_wav["path"])
        if not aprobe or not aprobe.get("duration_s"):
            raise AVSyncError("video_probe_failed", "audio ffprobe 失败")
        try:
            vtruth = resolve_video_truth(shot)
        except Exception as e:  # noqa: BLE001
            raise AVSyncError("video_probe_failed", str(e)[:200]) from e
        if vtruth is None:
            raise AVSyncError("validation_error", "无 authoritative video")
        target, tcode = resolve_speaker_target(shot.characters, explicit_speaker_target)
        if target is None:
            raise AVSyncError("multi_face_target_required",
                              "多角色镜未显式指定 speaker target（§11 fail closed）")
        plan = build_timing_plan(
            dialogue_id=did, shot_id=shot.shot_id,
            audio_version_id=selected_id,
            audio_duration_ms=int(round(aprobe["duration_s"] * 1000)),
            video_artifact_id=vtruth["artifact_id"],
            video_duration_ms=vtruth["probe"]["duration_ms"],
            speech_start_ms=speech_start_ms, adaptation_mode=adaptation_mode)
        opts_fp = provider_options_fingerprint(provider_options)
        spk = self.audio_service.readiness(did, speaker).get("speaker") or ""
        basis = build_av_sync_basis(
            shot_id=shot.shot_id, dialogue_id=did,
            source_video_artifact_id=vtruth["artifact_id"],
            source_video_fingerprint=vtruth["fingerprint"],
            dialogue_audio_version_id=selected_id,
            dialogue_audio_generation_basis_fingerprint=basis_fingerprint(av.generation_basis),
            speaker_character_name=spk,
            timing_fingerprint=timing_fingerprint(plan),
            speaker_target_fingerprint=target.fingerprint(),
            provider=provider, model=model, provider_options_fingerprint=opts_fp)
        return LipSyncGenerationPack(
            shot_id=shot.shot_id, dialogue_id=did, speaker_character_name=spk,
            source_video={"artifact_id": vtruth["artifact_id"],
                          "duration_ms": vtruth["probe"]["duration_ms"],
                          "fingerprint": vtruth["fingerprint"]},
            source_audio={"audio_version_id": selected_id,
                          "duration_ms": plan.audio_duration_ms,
                          "generation_basis_fingerprint":
                              basis_fingerprint(av.generation_basis)},
            timing={"speech_start_ms": plan.speech_start_ms,
                    "lead_in_ms": plan.lead_in_ms,
                    "tail_silence_ms": plan.tail_silence_ms,
                    "sync_input_duration_ms": plan.video_duration_ms,
                    "adaptation_mode": plan.adaptation_mode,
                    "playback_ratio": plan.playback_ratio},
            speaker_target=target.to_dict(),
            provider_binding={"provider": provider, "model": model},
            generation_basis=basis)

    # ==================================================================
    # ============ 显式生成（§21 race guard / §22 production mux）=======
    # ==================================================================

    async def generate_lipsync(self, dialogue_id: str, adapter, *,
                               speaker: Optional[str] = None,
                               explicit_speaker_target=None,
                               speech_start_ms: int = DEFAULT_SPEECH_START_MS,
                               adaptation_mode: str = ADAPTATION_PAD_AUDIO,
                               provider: str = "sync_labs",
                               model: str = LIPSYNC_DEFAULT_MODEL,
                               provider_options: Optional[dict] = None,
                               before_dispatch_hook: Optional[Callable] = None) -> dict:
        """显式 lip-sync 生成（§25：只能显式调用）。三段 race guard 见模块头。"""
        did = str(dialogue_id)

        # ---- Phase 1：read → validate → snapshot → working artifact ----
        readiness = self.get_av_sync_readiness(
            did, speaker=speaker, explicit_speaker_target=explicit_speaker_target,
            speech_start_ms=speech_start_ms, adaptation_mode=adaptation_mode,
            provider=provider, model=model, provider_options=provider_options)
        if readiness["status"] != "ready":
            raise AVSyncError("av_sync_not_ready",
                              f"dialogue {did} 未就绪: {readiness['status']}",
                              {"readiness": readiness})
        pack = self.compile_lipsync_generation_pack(
            did, speaker=speaker, explicit_speaker_target=explicit_speaker_target,
            speech_start_ms=speech_start_ms, adaptation_mode=adaptation_mode,
            provider=provider, model=model, provider_options=provider_options)
        working = self.sync_store.working_dir()
        working.mkdir(parents=True, exist_ok=True)
        # sync_input 先构造（provider 输入）；正式名在 commit 时以 generation id 定
        plan = pack.timing
        pre_sync_input = working / f"{did}_pre_sync_input.wav"
        try:
            sync_input_info = build_sync_input(
                self._selected_production_wav(did), pre_sync_input,
                speech_start_ms=plan["speech_start_ms"],
                target_duration_ms=plan["sync_input_duration_ms"])
        except SyncMediaError as e:
            raise AVSyncError(e.code, e.message,
                              {"dialogue_id": did}) from e
        # 测试 seam：Phase 1 完成后、dispatch 前注入 canonical 变更（生产不传）
        if before_dispatch_hook is not None:
            await before_dispatch_hook(pack, sync_input_info)

        # ---- Phase 2：provider（无锁；分钟级任务绝不持锁等待）----
        pack_dict = pack.to_dict()
        try:
            result = await adapter.generate(
                pack_dict, video_path=self._video_path_for_provider(did),
                audio_path=sync_input_info["path"])
        except AVSyncError:
            pre_sync_input.unlink(missing_ok=True)
            raise
        except Exception as e:  # noqa: BLE001
            pre_sync_input.unlink(missing_ok=True)
            if hasattr(e, "code"):
                # provider structured 错误原样透传（A1 §25 同口径，不二次包装）
                raise
            raise AVSyncError("provider_request_failed", str(e)[:200]) from e

        gen_id = str(result.get("provider_generation_id") or "")
        raw_tmp = self.sync_store.raw_dir() / f"{did}_gen_{gen_id or 'unknown'}.mp4"
        raw_tmp.parent.mkdir(parents=True, exist_ok=True)
        raw_tmp.write_bytes(result["raw_bytes"])

        # ---- Phase 3：短锁内重算 basis → 一致才 commit ----
        async with self.sync_store.write_lock():
            def _reject(reason_detail: dict, message: str) -> AVSyncError:
                """§21：provider 输出保留作 diagnostics（移入 evidence/），
                绝不 attach/selected/冒充 CURRENT。"""
                ev = self.sync_store.evidence_dir()
                ev.mkdir(parents=True, exist_ok=True)
                rejected = ev / f"{did}_rejected_gen_{gen_id or 'unknown'}.mp4"
                if raw_tmp.exists():
                    shutil.move(str(raw_tmp), str(rejected))
                return AVSyncError(
                    "dependency_changed_before_commit", message,
                    {"dialogue_id": did, "provider_generation_id": gen_id,
                     "rejected_evidence": str(rejected), **reason_detail})

            # 3a. 依赖结构重算（compile 失败 = 状态已被改坏 → 也是依赖变化）
            try:
                expected = self._recompute_basis(
                    did, speaker=speaker, explicit_speaker_target=explicit_speaker_target,
                    speech_start_ms=speech_start_ms, adaptation_mode=adaptation_mode,
                    provider=provider, model=model, provider_options=provider_options)
            except AVSyncError as e:
                raise _reject({"recompute_error": e.code},
                              f"provider 窗口内依赖变化导致状态不可重算: {e.code}") from e
            # 3b. A1 audio freshness 重查——dialogue 文本/声线在窗口内变化时，
            #     audio 记录的 basis 指纹不变，但 A1 freshness 会 stale（链路补全）
            fr = self.audio_service.freshness(did, speaker)
            if fr.get("status") != "current":
                raise _reject({"audio_freshness": fr.get("status"),
                               "audio_freshness_code": fr.get("code")},
                              f"provider 窗口内 selected audio 变 stale"
                              f"（{fr.get('code')}）——不冒充 CURRENT")
            # 3c. basis 逐键对比
            mismatch = [k for k in pack.generation_basis
                        if k in expected and pack.generation_basis[k] != expected[k]]
            if mismatch:
                raise _reject({"mismatched_keys": mismatch},
                              f"provider 窗口内实际依赖变化: {mismatch}")
            # 正式 id 分配（锁内单调）
            doc = self.sync_store.load_doc()
            entry = self.sync_store.get_entry(doc, did)
            sync_id = self.sync_store.next_sync_version_id(entry, did)
            raw_path = self.sync_store.raw_dir() / f"{sync_id}.mp4"
            prod_path = self.sync_store.production_dir() / f"{sync_id}.mp4"
            raw_tmp.replace(raw_path)
            pre_sync_input_final = working / f"{sync_id}_sync_input.wav"
            shutil.move(str(pre_sync_input), str(pre_sync_input_final))
            try:
                mux = mux_production_review(raw_path, pre_sync_input_final, prod_path)
            except SyncMediaError as e:
                raise AVSyncError(e.code, e.message,
                                  {"sync_version_id": sync_id}) from e
            from src.audio.av_sync.av_truth import ffprobe_video
            vprobe_out = ffprobe_video(prod_path)
            version = DialogueVideoSyncVersion(
                sync_version_id=sync_id,
                shot_id=pack.shot_id,
                dialogue_id=did,
                speaker_character_name=pack.speaker_character_name,
                source_video_artifact_id=pack.source_video["artifact_id"],
                source_video_fingerprint=pack.source_video["fingerprint"],
                source_audio_version_id=pack.source_audio["audio_version_id"],
                source_audio_basis_fingerprint=pack.source_audio["generation_basis_fingerprint"],
                provider=pack.provider_binding["provider"],
                model=pack.provider_binding["model"],
                generation_basis=dict(pack.generation_basis),
                provider_generation_id=gen_id,
                provider_metadata=dict(result.get("provider_metadata") or {}),
                raw_output_path=str(raw_path),
                production_output_path=str(prod_path),
                working_sync_input_path=str(pre_sync_input_final),
                video_probe=vprobe_out,
                audio_probe=sync_input_info.get("ffprobe") or {},
                timing=dict(pack.timing),
                speaker_target=dict(pack.speaker_target),
                created_at=time.time())
            entry["sync_versions"].append(version)
            entry["selected_sync_version_id"] = sync_id   # 显式生成 = 显式指向新版本
            entry["approved"] = False                      # approve 独立人类门（§15）
            doc.setdefault("dialogues", {})[did] = {
                "sync_versions": [v.to_dict() for v in entry["sync_versions"]],
                "selected_sync_version_id": entry["selected_sync_version_id"],
                "approved": entry["approved"]}
            self.sync_store.save_doc(doc)

        return {"ok": True, "sync_version_id": sync_id,
                "sync_version": version.to_dict(),
                "freshness": self.get_sync_freshness(
                    did, speaker=speaker, explicit_speaker_target=explicit_speaker_target,
                    speech_start_ms=speech_start_ms, adaptation_mode=adaptation_mode,
                    provider=provider, model=model, provider_options=provider_options)}

    # ==================================================================
    # ============ 查询 / selection / approval（§15/§24）===============
    # ==================================================================

    def get_sync_freshness(self, dialogue_id: str, *, speaker: Optional[str] = None,
                           explicit_speaker_target=None,
                           speech_start_ms: int = DEFAULT_SPEECH_START_MS,
                           adaptation_mode: str = ADAPTATION_PAD_AUDIO,
                           provider: str = "sync_labs",
                           model: str = LIPSYNC_DEFAULT_MODEL,
                           provider_options: Optional[dict] = None) -> dict:
        return compute_sync_freshness(
            self.audio_service.project_store.load(), self.audio_service,
            self.sync_store, str(dialogue_id), speaker=speaker,
            explicit_speaker_target=explicit_speaker_target,
            speech_start_ms=speech_start_ms, adaptation_mode=adaptation_mode,
            provider=provider, model=model, provider_options=provider_options)

    def list_sync_versions(self, dialogue_id: str) -> dict:
        did = str(dialogue_id)
        entry = self.sync_store.get_entry(self.sync_store.load_doc(), did)
        selected = entry.get("selected_sync_version_id")
        return {"dialogue_id": did,
                "sync_versions": [{**v.to_dict(),
                                   "review_status": ("selected" if v.sync_version_id == selected
                                                     else "unselected")}
                                  for v in entry["sync_versions"]],
                "selected_sync_version_id": selected,
                "approved": entry.get("approved", False)}

    def get_sync_version(self, dialogue_id: str, sync_version_id: str) -> Optional[dict]:
        did, sid = str(dialogue_id), str(sync_version_id)
        entry = self.sync_store.get_entry(self.sync_store.load_doc(), did)
        v = next((v for v in entry["sync_versions"] if v.sync_version_id == sid), None)
        return v.to_dict() if v else None

    def select_sync_version(self, dialogue_id: str, sync_version_id: str) -> dict:
        did, sid = str(dialogue_id), str(sync_version_id)

        async def _do():
            async with self.sync_store.write_lock():
                doc = self.sync_store.load_doc()
                entry = self.sync_store.get_entry(doc, did)
                if not any(v.sync_version_id == sid for v in entry["sync_versions"]):
                    return {"ok": False, "error": "sync_version_not_found",
                            "detail": f"sync version 不存在: {sid}"}
                doc.setdefault("dialogues", {})[did] = {
                    "sync_versions": [v.to_dict() for v in entry["sync_versions"]],
                    "selected_sync_version_id": sid, "approved": False}
                self.sync_store.save_doc(doc)
                return {"ok": True, "dialogue_id": did,
                        "selected_sync_version_id": sid, "approved": False}

        return _run(_do())

    def approve_sync_version(self, dialogue_id: str) -> dict:
        """批准当前 selected（§15：approve 与 selection 分开，generate 不自动 approve）。"""
        did = str(dialogue_id)

        async def _do():
            async with self.sync_store.write_lock():
                doc = self.sync_store.load_doc()
                entry = self.sync_store.get_entry(doc, did)
                if not entry.get("selected_sync_version_id"):
                    return {"ok": False, "error": "no_selection",
                            "detail": "没有 selected sync version 可批准"}
                doc.setdefault("dialogues", {})[did] = {
                    "sync_versions": [v.to_dict() for v in entry["sync_versions"]],
                    "selected_sync_version_id": entry["selected_sync_version_id"],
                    "approved": True}
                self.sync_store.save_doc(doc)
                return {"ok": True, "dialogue_id": did,
                        "selected_sync_version_id": entry["selected_sync_version_id"],
                        "approved": True}

        return _run(_do())

    # ==================================================================
    # ============ 内部 helpers ============
    # ==================================================================

    def _selected_production_wav(self, dialogue_id: str) -> str:
        entry = self.audio_service.audio_store.get_entry(
            self.audio_service.audio_store.load_doc(), str(dialogue_id))
        selected_id = entry.get("selected_audio_version_id")
        av = next((v for v in entry["audio_versions"]
                   if v.audio_version_id == selected_id), None) if selected_id else None
        wav = (av.production_wav or {}).get("path") if av else None
        if not wav:
            raise AVSyncError("validation_error",
                              f"selected audio production WAV 缺失: {dialogue_id}")
        return str(wav)

    def _video_path_for_provider(self, dialogue_id: str) -> str:
        project = self.audio_service.project_store.load()
        shot = project.get_shot(str(dialogue_id))
        vtruth = resolve_video_truth(shot) if shot else None
        if not vtruth:
            raise AVSyncError("validation_error", "authoritative video 不可解析")
        return vtruth["local_path"]

    def _recompute_basis(self, dialogue_id: str, *, speaker, explicit_speaker_target,
                         speech_start_ms, adaptation_mode, provider, model,
                         provider_options) -> dict:
        """Phase 3 锁内重算 expected basis（与 pack 编译同一套指纹口径）。"""
        return self.compile_lipsync_generation_pack(
            dialogue_id, speaker=speaker, explicit_speaker_target=explicit_speaker_target,
            speech_start_ms=speech_start_ms, adaptation_mode=adaptation_mode,
            provider=provider, model=model, provider_options=provider_options
        ).generation_basis


def _run(coro):
    import asyncio as _a
    return _a.run(coro)
