# src/sequence/service.py
# Sequence S1/S2 — 业务服务（24.md §5/§7/§10/§12-§14/§21-§23 + 25.md §7/§12-§23/§26/§30）。
#
# 职责：
#   canonical ops        create/update/delete/list/get（独立 sequences.json）
#   audio plan ops       get/put SequenceAudioPlan（独立 sequence_audio_plans.json；
#                        OCC expected_revision → revision_conflict，25.md §38）
#   render 三阶段（24.md §23 + 25.md §30）：
#     Phase 1 prepare    snapshot sequence + source video versions + basis
#                        （S2：+ resolve audio plan/artifacts + §12/§14 越界
#                        fail closed + basis v1/v2 快照）——零 provider 调用
#     Phase 2 execute    ffmpeg render——不持锁（线程中跑）。S2 方案 A：有可用
#                        base（done 且 S1 子集 CURRENT）→ video stream copy + 混音
#                        （零视频重转码）；无 base → 同一次显式 render 内重建基线
#                        再混（视频重编码一次，诚实降级）；无 AudioPlan → S1 语义
#                        逐字节不变（25.md §25）
#     Phase 3 commit     recompute 全量 basis；不一致 → raced_superseded（evidence
#                        保留）；§21 clipping advisory（只报告不自动改 gain）
#
# 🔴 并发模型：两个 store 的读改写临界区共用 per-project threading.Lock（事件循环 +
#    JobQueue worker 线程双上下文；长工作一律锁外）。
# 🔴 No Auto Render（§22 24.md / §22 25.md）/ No Auto Select（§10）。
# 🔴 Source Video Truth（§7 24.md）/ Audio Source Truth（§10 25.md）：显式引用解析，
#    禁止 glob/mtime/末位回落。
# 🔴 S2 不触发 Shot regeneration（25.md §22）：来源只读 authoritative Shot media。
from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.core.models import Project
from src.core.store import ProjectStore

from .audio_basis import (
    build_render_basis_v2,
    compute_current_render_basis,
    full_basis_mismatched_keys,
    resolve_audio_source,
)
from .audio_basis import ResolvedAudioSource
from .audio_mix import (
    AudioMixPlan,
    MixLayer,
    build_audio_mix_plan,
    compile_audio_filter_graph,
    peak_warning,
)
from .audio_models import (SequenceAudioLayer, SequenceAudioPlan,
                           SequenceDuckingConfig, SequenceLoudnessTarget,
                           _check_gain)
from .audio_store import SequenceAudioPlanStore
from .basis import (
    basis_mismatched_keys,
    build_render_basis,
    compute_current_basis,
    resolve_clip_source,
    sequence_render_freshness,
)
from .models import (
    RSTATUS_DONE,
    RSTATUS_FAILED,
    RSTATUS_QUEUED,
    RSTATUS_RACED,
    RSTATUS_RENDERING,
    SequenceBlock,
    SequenceClip,
    SequenceEntry,
    SequenceError,
    SequenceRenderVersion,
    validate_render_profile,
)
from .store import SequenceStore
from .timeline import ResolvedClip, SequenceTimelinePlan, build_timeline_plan

# per-project 临界区锁（sequences.json 与 sequence_audio_plans.json 共用同族；
# 事件循环 + worker 线程共用）
_STORE_LOCKS: Dict[str, threading.Lock] = {}


def _store_lock(project_name: str) -> threading.Lock:
    if project_name not in _STORE_LOCKS:
        _STORE_LOCKS[project_name] = threading.Lock()
    return _STORE_LOCKS[project_name]


def _clear_locks_for_test() -> None:
    _STORE_LOCKS.clear()


@dataclass
class PreparedRender:
    """Phase 1 快照结果（一次 render 的全部输入；Phase 2 只消费不再探测）。"""
    render_version: SequenceRenderVersion
    timeline: SequenceTimelinePlan
    audio_plan: Optional[SequenceAudioPlan] = None          # None/空 = S1 语义（§25）
    resolved_audio: List[ResolvedAudioSource] = field(default_factory=list)

    @property
    def has_layers(self) -> bool:
        return bool(self.audio_plan and self.audio_plan.layers)


class SequenceService:
    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.project_dir = Path(project_dir)
        self.project_name = project_name or self.project_dir.name
        self.store = SequenceStore(self.project_dir, self.project_name)
        self.audio_store = SequenceAudioPlanStore(self.project_dir, self.project_name)

    # ============ 内部工具 ============

    def _sequences_root(self) -> Path:
        return self.project_dir / "output" / "sequences"

    def _load_project(self) -> Project:
        return ProjectStore(self.project_dir).load()

    def _require_entry(self, entries: Dict[str, SequenceEntry],
                       sequence_id: str) -> SequenceEntry:
        entry = entries.get(sequence_id)
        if entry is None:
            raise SequenceError("sequence_not_found", f"Sequence 不存在: {sequence_id}")
        return entry

    # ============ canonical ops（load→save 全程持锁）============

    def list_entries(self) -> Dict[str, SequenceEntry]:
        return self.store.load()

    def get_entry(self, sequence_id: str) -> SequenceEntry:
        return self._require_entry(self.store.load(), sequence_id)

    def create_sequence(self, *, title: str = "", scene_id: Optional[str] = None,
                        clips: List[Dict[str, Any]],
                        render_profile: Optional[Dict[str, Any]] = None) -> SequenceBlock:
        """创建 SequenceBlock。clips 结构非法 → fail closed（不自动修正）。
        render_profile（Phase 44B）：完整规格 dict（validate_render_profile 校验）；
        None = 用全局 SEQUENCE_RENDER_PROFILE。"""
        block = SequenceBlock(
            sequence_id=SequenceBlock.new_sequence_id(),
            clips=[SequenceClip.from_dict(c) for c in (clips or [])],
            title=str(title or ""),
            scene_id=scene_id,
            render_profile=(dict(render_profile) if render_profile else None))
        with _store_lock(self.project_name):
            entries = self.store.load()
            entries[block.sequence_id] = SequenceEntry(block=block)
            self.store.save(entries)
        return block

    def update_sequence(self, sequence_id: str, *, title: Optional[str] = None,
                        scene_id: Optional[str] = None, clips: Optional[List[Dict[str, Any]]] = None,
                        expected_revision: Optional[str] = None,
                        render_profile: Optional[Dict[str, Any]] = None) -> SequenceBlock:
        """更新 canonical。clips 传 None = 不改；传 list = 整体替换（顺序即播放顺序）。
        render_profile：None = 不改；完整规格 dict = 设置（校验 fail closed）；
        空 dict = 清除覆盖（回到全局 profile）。
        expected_revision 提供 OCC 门：不符 → revision_conflict。"""
        with _store_lock(self.project_name):
            entries = self.store.load()
            entry = self._require_entry(entries, sequence_id)
            if expected_revision is not None and expected_revision != entry.block.revision:
                raise SequenceError(
                    "revision_conflict",
                    f"expected_revision={expected_revision!r} 与当前 "
                    f"{entry.block.revision!r} 不符——请刷新后重试（OCC）")
            if clips is not None:
                entry.block.clips = [SequenceClip.from_dict(c) for c in clips]
            if title is not None:
                entry.block.title = str(title).strip()
            if scene_id is not None:
                entry.block.scene_id = scene_id
            if render_profile is not None:
                if render_profile:
                    entry.block.render_profile = validate_render_profile(render_profile)
                else:
                    entry.block.render_profile = None     # 空 dict = 清除覆盖
            entry.block.touch()
            self.store.save(entries)
            return entry.block

    def delete_sequence(self, sequence_id: str) -> dict:
        """删除 sequence（canonical + render versions + 输出文件 + audio plan）。"""
        with _store_lock(self.project_name):
            entries = self.store.load()
            self._require_entry(entries, sequence_id)
            del entries[sequence_id]
            self.store.save(entries)
            plans = self.audio_store.load()
            if sequence_id in plans:
                del plans[sequence_id]
                self.audio_store.save(plans)
        seq_dir = self._sequences_root() / sequence_id
        removed_files = 0
        if seq_dir.exists():
            try:
                seq_dir.resolve().relative_to((self.project_dir / "output").resolve())
            except ValueError:
                raise SequenceError("delete_refused",
                                    f"sequence 输出目录异常，拒绝删除: {seq_dir}")
            removed_files = sum(1 for _ in seq_dir.rglob("*") if _.is_file())
            shutil.rmtree(seq_dir, ignore_errors=True)
        return {"sequence_id": sequence_id, "deleted": True, "removed_files": removed_files}

    # ============ Audio Plan canonical ops（25.md §7/§37/§38/§29）============

    def get_audio_plan(self, sequence_id: str) -> Optional[SequenceAudioPlan]:
        """读某 sequence 的 AudioPlan（None = 无 plan → S1 语义 §25）。"""
        self._require_entry(self.store.load(), sequence_id)
        return self.audio_store.plan_for(sequence_id)

    def put_audio_plan(self, sequence_id: str, layers_payload: List[Dict[str, Any]],
                       expected_revision: Optional[str] = None,
                       plan_config: Optional[Dict[str, Any]] = None) -> Optional[SequenceAudioPlan]:
        """整体替换 AudioPlan（25.md §37：一个完整 PUT + OCC 足够第一版）。
        - layers 非法 → fail closed（role 限制 §8 / 负值 / fade 越界 / gain 越界）；
        - expected_revision 与当前不符 → revision_conflict（§38 409，不 last-write-wins）；
        - layers 为空 → 删除 plan（canonical 归一化：空 plan ≡ 无 plan = S1 语义）；
        - plan_config（44C）：混音配置整体替换 {base_gain_db, ducking, loudness}
          （ducking/loudness 为 dict 或 None；None = 显式关闭该特性）。 layers 为空时
          plan_config 一并清除（无 plan 即无配置）。
        时间轴媒体事实（start+duration <= sequence duration、source_in+duration <=
        源时长）在 render Phase 1 以 ffprobe 实测校验（§5-3/§12/§14——不根据历史值猜）。"""
        with _store_lock(self.project_name):
            self._require_entry(self.store.load(), sequence_id)
            plans = self.audio_store.load()
            current = plans.get(sequence_id)
            if expected_revision is not None:
                current_rev = current.revision if current else None
                if current_rev != expected_revision:
                    raise SequenceError(
                        "revision_conflict",
                        f"audio plan expected_revision={expected_revision!r} 与当前 "
                        f"{current_rev!r} 不符（OCC）")
            layers = [SequenceAudioLayer.from_dict(l) for l in (layers_payload or [])]
            if not layers:
                if current is not None:
                    del plans[sequence_id]
                    self.audio_store.save(plans)
                return None
            plan = current if current is not None else SequenceAudioPlan(sequence_id=sequence_id)
            plan.layers = layers
            if plan_config is not None:
                if not isinstance(plan_config, dict):
                    raise SequenceError("audio_plan_invalid",
                                        "plan_config 必须是对象（整体替换语义）")
                # 🔴 44C 修复：必须经 _check_gain 归一为 float——直接赋 int 会让
                # 内存指纹（-3）与重载指纹（-3.0）不一致（verified=False 实证）
                plan.base_gain_db = _check_gain(
                    plan_config.get("base_gain_db", 0.0) or 0.0)
                d = plan_config.get("ducking")
                plan.ducking = SequenceDuckingConfig.from_dict(d) if d else None
                ln = plan_config.get("loudness")
                plan.loudness = SequenceLoudnessTarget.from_dict(ln) if ln else None
            plan.touch()
            plans[sequence_id] = plan
            self.audio_store.save(plans)
            return plan

    # ============ render 三阶段（24.md §23 + 25.md §30）============

    def create_render(self, sequence_id: str) -> SequenceRenderVersion:
        """受理一次显式 render：append 一条 queued render version。
        不自动 select、不触发 ffmpeg——ffmpeg 在 handler 的 Phase 2。"""
        with _store_lock(self.project_name):
            entries = self.store.load()
            entry = self._require_entry(entries, sequence_id)
            rv = SequenceRenderVersion(version_id=SequenceRenderVersion.new_version_id(),
                                       sequence_id=sequence_id, status=RSTATUS_QUEUED)
            entry.render_versions.append(rv)
            self.store.save(entries)
            return rv

    def prepare_render(self, sequence_id: str, version_id: str) -> PreparedRender:
        """Phase 1：snapshot sequence + source video versions + audio plan + basis。
        零 provider 调用；fail closed 点：§7 源解析 / §14 probe / §12 trim /
        §12+§14 音频时间轴越界 / §10+§33 audio source 解析。probe/hash 锁外，行更新锁内。"""
        block = self._require_entry(self.store.load(), sequence_id).block
        plan = self.audio_store.plan_for(sequence_id)
        has_layers = bool(plan and plan.layers)
        try:
            project = self._load_project()
            # 函数内 import：单测经 monkeypatch(src.sequence.media_ops.probe_video)
            # 注入伪探测——调用时绑定保证 patch 生效
            from .media_ops import probe_video
            resolved: List[ResolvedClip] = []
            for i, clip in enumerate(block.clips):
                _shot, version = resolve_clip_source(project, clip)
                probe = probe_video(str(version.local or ""))
                if not probe.get("has_video", False):
                    raise SequenceError(
                        "source_unreadable",
                        f"clip {clip.shot_id}: 源无视频流（Sequence 源必须是视频）")
                resolved.append(ResolvedClip(
                    index=i, shot_id=clip.shot_id, version_id=version.version_id,
                    source_file=str(version.local), trim_in_ms=clip.trim_in_ms,
                    trim_out_ms=clip.trim_out_ms,
                    source_duration_ms=probe["duration_ms"],          # §14 文件实测
                    has_audio=bool(probe["has_audio"])))
            timeline = build_timeline_plan(resolved)                  # §13 + §12 验证
            # Phase51 B（B4）：接力端点 vs 实际保留端点审计（只报告不阻断）
            try:
                from .relay_audit import audit_relay_endpoints
                relay_warning = audit_relay_endpoints(project, resolved)
            except Exception:   # 审计自身异常不阻断 prepare（fail-open 观测件）
                relay_warning = None
            resolved_audio: List[ResolvedAudioSource] = []
            if has_layers:
                # 混音输出规格硬编码 aac 48k stereo（audio_mix 方案 A）——profile
                # 覆盖与之背离时 fail closed（防「规格声明≠实际输出」的第二真相）。
                _eff = block.effective_render_profile()
                if (_eff.get("sample_rate"), _eff.get("channels")) != (48000, 2):
                    raise SequenceError(
                        "profile_invalid",
                        f"含 AudioPlan 的渲染要求 profile 采样率/声道 = 48000/2"
                        f"（混音输出规格），收到 {_eff.get('sample_rate')}/"
                        f"{_eff.get('channels')}")
                for layer in plan.layers:
                    r = resolve_audio_source(self.project_dir, layer.layer_id, layer.source)
                    if layer.source_in_ms + layer.duration_ms > r.duration_ms:
                        raise SequenceError(
                            "audio_layer_invalid",
                            f"layer {layer.layer_id}: source_in({layer.source_in_ms}) + "
                            f"duration({layer.duration_ms}) > 源时长({r.duration_ms})ms"
                            "（§14 fail closed；不自动 loop §15）")
                    resolved_audio.append(r)
                for layer in plan.layers:
                    if layer.start_ms + layer.duration_ms > timeline.total_duration_ms:
                        raise SequenceError(
                            "audio_layer_invalid",
                            f"layer {layer.layer_id}: start({layer.start_ms}) + "
                            f"duration({layer.duration_ms}) > sequence duration("
                            f"{timeline.total_duration_ms})ms（§12 fail closed，不静默裁片尾）")
                basis = build_render_basis_v2(self.project_dir, block, resolved,
                                              plan, resolved_audio)         # §26 v2
            else:
                basis = build_render_basis(block, resolved)                 # §25 v1 原样
        except SequenceError as e:
            self._mark_failed(sequence_id, version_id, e.code, e.message)
            raise
        except Exception as e:   # 未知错误也 fail closed，不留半 prepared 状态
            self._mark_failed(sequence_id, version_id, "prepare_failed", str(e))
            raise

        with _store_lock(self.project_name):
            entries = self.store.load()
            entry = self._require_entry(entries, sequence_id)
            rv = entry.get_render(version_id)
            if rv is None:
                raise SequenceError("render_version_not_found",
                                    f"render version 不存在: {version_id}")
            rv.sequence_revision = block.revision
            rv.generation_basis = basis
            rv.relay_endpoint_warning = relay_warning
            rv.source_video_versions = [
                {"shot_id": rc.shot_id, "version_id": rc.version_id} for rc in resolved]
            rv.planned_duration_ms = timeline.total_duration_ms
            # 输出路径 Phase 1 即确定（§9 minimum fields；review 页/导出按此播放）
            rv.output_file = str(self._sequences_root() / sequence_id / f"{version_id}.mp4")
            rv.status = RSTATUS_RENDERING
            rv.error = None
            self.store.save(entries)
        return PreparedRender(render_version=rv, timeline=timeline,
                              audio_plan=plan if has_layers else None,
                              resolved_audio=resolved_audio)

    def execute_render(self, sequence_id: str, version_id: str,
                       prepared: PreparedRender) -> str:
        """Phase 2：ffmpeg render——不持锁。无 AudioPlan → S1 语义（§25）；
        有 AudioPlan → 方案 A：优先 base video stream copy + 混音（零视频重转码），
        无可用 base → 同一次显式 render 内重建基线再混（视频重编码一次，诚实降级）。"""
        self._require_entry(self.store.load(), sequence_id).get_render(version_id)
        from .media_ops import concat_clips, render_clip
        from .media_ops import _run as _run_ffmpeg
        version_dir = self._sequences_root() / sequence_id
        work_dir = version_dir / "_work" / version_id
        work_dir.mkdir(parents=True, exist_ok=True)
        plan_layers = prepared.audio_plan.layers if prepared.has_layers else []
        # Phase 44B：渲染 profile 来自 block（显式覆盖或全局）；audio=silent 时
        # 全部 clip 按无音轨处理（补等长 silence）——「关闭原音轨」显式策略，
        # 进 basis 指纹（策略变化 = 诚实 STALE），杜绝逐镜原生声漏进静音成片。
        profile = self.get_entry(sequence_id).block.effective_render_profile()
        silent_native = profile.get("audio") == "silent"
        output_file = version_dir / f"{version_id}.mp4"
        base_rv: Optional[SequenceRenderVersion] = None
        try:
            def _build_segments() -> List[str]:
                segments: List[str] = []
                for i, tc in enumerate(prepared.timeline.clips):
                    seg = work_dir / f"seg_{i:03d}_{tc.shot_id}.mp4"
                    render_clip(tc.source_file, str(seg),
                                trim_in_ms=tc.trim_in_ms, trim_out_ms=tc.trim_out_ms,
                                source_duration_ms=tc.source_duration_ms,
                                has_audio=bool(tc.has_audio) and not silent_native,
                                profile=profile)   # probe 实测随 plan 传递
                    segments.append(str(seg))
                return segments

            def _mix_to(mix: AudioMixPlan, dst: str) -> None:
                # 🔴 44C+ 接管修复：两遍渲染。单遍「混音图 → AAC」实测丢延迟层
                # （amix+adelay+定长帧编码器组合缺陷：多句旁白仅第一句幸存、
                # 其后全丢；同一图 PCM/MP3/FLAC 输出逐句相关 0.9+ 全在场，且
                # PCM 两次运行逐字节同判——图正确、编码路径坏）。第二遍无
                # filter_complex，视频流 copy + WAV→AAC，规避该缺陷。
                g = compile_audio_filter_graph(mix)
                tmp_wav = work_dir / "mix_pcm.wav"
                cmd1 = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                         "-i", mix.base_file] + g["input_args"]
                        + ["-filter_complex", g["filter_complex"],
                           "-map", g["final_label"],
                           "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                           "-map_metadata", "-1", str(tmp_wav)])
                _run_ffmpeg(cmd1, timeout=600)
                if not tmp_wav.exists() or tmp_wav.stat().st_size == 0:
                    raise SequenceError("render_failed",
                                        f"混音中间 PCM 为空: {tmp_wav}")
                # 44C：192k（默认 ~128k 的解码过冲可达 ~2dB，会吃掉 loudnorm 的
                # TP 余量导致解码后触顶）；TP 目标在 plan 中显式声明
                cmd2 = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                         "-i", mix.base_file, "-i", str(tmp_wav),
                         "-map", "0:v", "-c:v", "copy",     # 方案 A：视频零重转码
                         "-map", "1:a",
                         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                         "-map_metadata", "-1", dst])
                _run_ffmpeg(cmd2, timeout=600)
                if not Path(dst).exists() or Path(dst).stat().st_size == 0:
                    raise SequenceError("render_failed", f"混音输出为空: {dst}")

            if not plan_layers:
                segments = _build_segments()
                concat_clips(segments, str(output_file))              # §11 hard cut
            else:
                # base 选择：最近的 done 且输出在且 S1 子集 CURRENT（§23 方案 A 前提）
                current_s1 = compute_current_basis(self._load_project(),
                                                   self.get_entry(sequence_id).block)
                base_rv = self._eligible_mix_base(sequence_id, version_id, current_s1)
                cfg = prepared.audio_plan
                mix_kwargs = {
                    "base_gain_db": cfg.base_gain_db,
                    "ducking": cfg.ducking.semantic_snapshot() if cfg.ducking else None,
                    "loudness": cfg.loudness.semantic_snapshot() if cfg.loudness else None,
                }
                if base_rv is not None:
                    mix = build_audio_mix_plan(
                        str(self._sequences_root() / sequence_id / f"{base_rv.version_id}.mp4"),
                        base_rv.version_id, self._mix_layers(prepared), **mix_kwargs)
                    _mix_to(mix, str(output_file))                    # 视频零重编码
                else:
                    temp_base = work_dir / "base.mp4"                 # 降级：先重建基线
                    concat_clips(_build_segments(), str(temp_base))
                    mix = build_audio_mix_plan(str(temp_base), "",
                                               self._mix_layers(prepared), **mix_kwargs)
                    _mix_to(mix, str(output_file))
        except SequenceError as e:
            self._mark_failed(sequence_id, version_id, e.code, e.message)
            raise
        except Exception as e:
            self._mark_failed(sequence_id, version_id, "render_failed", str(e))
            raise
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)   # 中间段/临时基线不作 evidence
        # 溯源（provenance，非 freshness 依赖）：锁内落行
        with _store_lock(self.project_name):
            entries = self.store.load()
            entry = self._require_entry(entries, sequence_id)
            rv = entry.get_render(version_id)
            if rv is not None:
                rv.base_render_version_id = base_rv.version_id if base_rv else None
                self.store.save(entries)
        return str(output_file)

    def _mix_layers(self, prepared: PreparedRender) -> List[MixLayer]:
        return [MixLayer(layer_id=l.layer_id, role=l.role, source_file=r.file,
                         source_in_ms=l.source_in_ms, duration_ms=l.duration_ms,
                         start_ms=l.start_ms, gain_db=l.gain_db,
                         fade_in_ms=l.fade_in_ms, fade_out_ms=l.fade_out_ms)
                for l, r in zip(prepared.audio_plan.layers, prepared.resolved_audio)]

    def _eligible_mix_base(self, sequence_id: str, exclude_version_id: str,
                           current_s1_basis: Dict[str, Any]) -> Optional[SequenceRenderVersion]:
        """方案 A 的 base：最近 done、输出文件在、且其 basis 的 S1 子集与当前重算一致
        （clips/shots/profile/renderer 未变 → 视频比特与现渲染一致）。
        🔴 Phase 44C 修复：base 还必须是【纯 S1 渲染】（basis schema=v1、无 audio 段）。
        混音图对 base 的 [0:a] 语义 = native 音轨；v2（已混音）渲染的音轨含 layers，
        作 base 会把上一版混音整体叠加进新渲染（C 批迭代混音实测触发——F4 证据）。
        无合格 v1 base → 走既有降级路径（重建基线：视频重编码一次，诚实行为）。"""
        entry = self._require_entry(self.store.load(), sequence_id)
        for r in reversed(entry.render_versions):
            if r.version_id == exclude_version_id or r.status != RSTATUS_DONE:
                continue
            out = self._sequences_root() / sequence_id / f"{r.version_id}.mp4"
            if not out.exists() or not r.generation_basis:
                continue
            if (r.generation_basis or {}).get("schema") == "sequence_render_basis_v2":
                continue          # 混音渲染不作 base（防叠加，见上）
            if not basis_mismatched_keys(r.generation_basis, current_s1_basis):
                return r
        return None

    def _mark_failed(self, sequence_id: str, version_id: str,
                     code: str, message: str) -> None:
        """render 行落 failed（锁内读改写；sequence 已删则诚实忽略）。"""
        try:
            with _store_lock(self.project_name):
                entries = self.store.load()
                entry = self._require_entry(entries, sequence_id)
                rv = entry.get_render(version_id)
                if rv is not None and rv.status not in (RSTATUS_DONE, RSTATUS_RACED):
                    rv.status = RSTATUS_FAILED
                    rv.error = f"[{code}] {message}"
                    rv.finished_at = time.time()
                    self.store.save(entries)
        except SequenceError:
            pass

    def commit_render(self, sequence_id: str, version_id: str) -> Dict[str, Any]:
        """Phase 3：recompute 全量 basis（25.md §30）。
        - 依赖已变（clips/shots/trim/profile/renderer/audio plan/audio source）→
          sequence_dependency_changed_before_commit：raced_superseded，evidence 保留；
        - 一致 → ffprobe 校验输出（§30 24.md 机器检查）→ done；
        - 混音输出附 §21 clipping advisory（audio_peak_warning，只报告）。"""
        entries = self.store.load()
        entry = self._require_entry(entries, sequence_id)
        rv_snapshot = entry.get_render(version_id)
        if rv_snapshot is None:
            raise SequenceError("render_version_not_found",
                                f"render version 不存在: {version_id}")
        output_file = self._sequences_root() / sequence_id / f"{version_id}.mp4"
        if not output_file.exists():
            self._mark_failed(sequence_id, version_id, "commit_failed",
                              "Phase 2 输出缺失（render 中断？）")
            raise SequenceError("commit_failed", "Phase 2 输出缺失（render 中断？）")

        plan_now = self.audio_store.plan_for(sequence_id)
        plan_present = bool(plan_now and plan_now.layers)
        project = self._load_project()
        current = compute_current_render_basis(project, self.project_dir,
                                               entry.block, plan_now)
        mismatched = full_basis_mismatched_keys(rv_snapshot.generation_basis or {},
                                                current, plan_present=plan_present)
        raced = bool(mismatched)
        peak: Optional[Dict[str, Any]] = None
        if not raced:
            from .media_ops import validate_output
            result = validate_output(str(output_file),
                                     planned_total_ms=rv_snapshot.planned_duration_ms or 0,
                                     n_clips=len(entry.block.clips))
            if plan_present:
                # 函数内 import：单测经 monkeypatch 注入伪峰值（调用时绑定）
                from .audio_mix import measure_peak_db
                peak = peak_warning(measure_peak_db(str(output_file)))   # §21 advisory

        # 锁内落终态（行可能已被并发删除/修改——以锁内重读为准）
        with _store_lock(self.project_name):
            entries = self.store.load()
            entry = self._require_entry(entries, sequence_id)
            rv = entry.get_render(version_id)
            if rv is None:
                raise SequenceError("render_version_not_found",
                                    f"render version 不存在: {version_id}")
            rv.finished_at = time.time()
            if raced:
                rv.status = RSTATUS_RACED
                rv.error = ("[sequence_dependency_changed_before_commit] "
                            f"mismatched: {', '.join(mismatched)}")
            else:
                rv.status = RSTATUS_DONE
                rv.duration_ms = result["duration_ms"]
                rv.error = None
                rv.audio_peak_warning = peak
            self.store.save(entries)
        if raced:
            raise SequenceError(
                "sequence_dependency_changed_before_commit",
                f"render 窗口内依赖已变化（{', '.join(mismatched)}）——"
                f"输出保留为 evidence（{output_file.name}），不得 attach 为 CURRENT；显式重渲染")
        return {"version_id": version_id, "status": RSTATUS_DONE,
                "duration_ms": result["duration_ms"],
                "planned_duration_ms": result["planned_duration_ms"],
                "duration_delta_ms": result["duration_delta_ms"],
                "audio_peak_warning": peak}

    def select_render(self, sequence_id: str, version_id: str) -> SequenceEntry:
        """显式 select final render（§10：生成绝不自动覆盖正式结果）。
        只允许 select done 版本。"""
        with _store_lock(self.project_name):
            entries = self.store.load()
            entry = self._require_entry(entries, sequence_id)
            rv = entry.get_render(version_id)
            if rv is None:
                raise SequenceError("render_version_not_found",
                                    f"render version 不存在: {version_id}")
            if rv.status != RSTATUS_DONE:
                raise SequenceError("render_not_selectable",
                                    f"render {version_id} 状态 {rv.status}（仅 done 可 select）")
            entry.final_render = version_id
            self.store.save(entries)
            return entry

    # ============ freshness 读模型（24.md §20/§21 + 25.md §27/§28/§29）============

    def freshness(self, sequence_id: str, version_id: Optional[str] = None) -> Dict[str, Any]:
        """final render（或指定 version）的 freshness。纯读，绝不触发 render。
        plan 存在 → v2 语义（audio 段参与比较）；无 plan → S1 语义（§25 锁死）。"""
        entries = self.store.load()
        entry = self._require_entry(entries, sequence_id)
        rv = (entry.get_render(version_id)
              if version_id else entry.final_render_version())
        plan = self.audio_store.plan_for(sequence_id)
        return sequence_render_freshness(self._load_project(), entry.block, rv,
                                         plan=plan, project_dir=self.project_dir)

    def render_output_path(self, sequence_id: str, version_id: str) -> Optional[Path]:
        """render 输出文件路径（scratch review page 播放用；不存在 → None）。"""
        p = self._sequences_root() / sequence_id / f"{version_id}.mp4"
        return p if p.exists() else None
