# src/sequence/service.py
# Sequence S1 — 业务服务（24.md §5/§7/§10/§12-§14/§21-§23）。
#
# 职责：
#   canonical ops        create/update/delete/list/get（独立 sequences.json）
#   render 三阶段（§23）：
#     Phase 1 prepare    snapshot sequence + source video versions + basis
#                        （§7 源真相解析 + §14 ffprobe + §12 trim 验证 + §13 plan
#                        + §19 basis 快照）——零 provider 调用，只有本地 ffprobe/hash
#     Phase 2 execute    ffmpeg render（不持锁；在线程中跑）
#     Phase 3 commit     recompute basis；期间 shot final_video / sequence order /
#                        trim / 源文件已变 → sequence_dependency_changed_before_commit，
#                        不 attach 为 CURRENT，输出保留为 evidence（§23）
#
# 🔴 并发模型：sequences.json 的读改写临界区用 per-project threading.Lock 串行化——
#    调用方既有事件循环（router）也有 JobQueue worker 线程（asyncio.Lock 不可用，
#    src/core/write_lock.py 是 asyncio 族；本锁只管本 store 文件，短临界区、无 ffmpeg/
#    provider 持锁）。probe/hash/ffmpeg 等长工作一律在锁外。
# 🔴 No Auto Render（§22）：detect → expose → explicit render。本模块绝不自动执行
#    ffmpeg；Shot final_video 修改绝不偷偷重建 Sequence（freshness 纯读模型暴露）。
# 🔴 No Auto Select（§10）：render 只 append version；final_render 指针只能显式 select。
# 🔴 Source Video Truth（§7）：shot_id → Shot.final_video → VideoVersion → mp4，
#    绝不回落 versions[-1] / glob / mtime。
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.core.models import Project
from src.core.store import ProjectStore

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
)
from .store import SequenceStore
from .timeline import ResolvedClip, SequenceTimelinePlan, build_timeline_plan

# per-project sequences.json 临界区锁（事件循环 + worker 线程共用）
_STORE_LOCKS: Dict[str, threading.Lock] = {}


def _store_lock(project_name: str) -> threading.Lock:
    if project_name not in _STORE_LOCKS:
        _STORE_LOCKS[project_name] = threading.Lock()
    return _STORE_LOCKS[project_name]


def _clear_locks_for_test() -> None:
    _STORE_LOCKS.clear()


class SequenceService:
    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.project_dir = Path(project_dir)
        self.project_name = project_name or self.project_dir.name
        self.store = SequenceStore(self.project_dir, self.project_name)

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
                        clips: List[Dict[str, Any]]) -> SequenceBlock:
        """创建 SequenceBlock。clips 结构非法 → fail closed（不自动修正）。"""
        block = SequenceBlock(
            sequence_id=SequenceBlock.new_sequence_id(),
            clips=[SequenceClip.from_dict(c) for c in (clips or [])],
            title=str(title or ""),
            scene_id=scene_id)
        with _store_lock(self.project_name):
            entries = self.store.load()
            entries[block.sequence_id] = SequenceEntry(block=block)
            self.store.save(entries)
        return block

    def update_sequence(self, sequence_id: str, *, title: Optional[str] = None,
                        scene_id: Optional[str] = None, clips: Optional[List[Dict[str, Any]]] = None,
                        expected_revision: Optional[str] = None) -> SequenceBlock:
        """更新 canonical。clips 传 None = 不改（只改 title/scene_id）；
        传 list = 整体替换（顺序即播放顺序）。expected_revision 提供 OCC 门：
        与当前 revision 不符 → revision_conflict（不静默覆盖他人修改）。"""
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
            entry.block.touch()
            self.store.save(entries)
            return entry.block

    def delete_sequence(self, sequence_id: str) -> dict:
        """删除 sequence（canonical + render versions + 输出文件）。
        🔴 只删本项目 output/sequences/<sequence_id>/ 内文件（scenes.py 删除清理同护栏）。"""
        with _store_lock(self.project_name):
            entries = self.store.load()
            self._require_entry(entries, sequence_id)
            del entries[sequence_id]
            self.store.save(entries)
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

    # ============ render 三阶段（24.md §23）============

    def create_render(self, sequence_id: str) -> SequenceRenderVersion:
        """受理一次显式 render：append 一条 queued render version（§10 追加式）。
        不自动 select、不触发 ffmpeg——ffmpeg 在 handler 的 Phase 2。"""
        with _store_lock(self.project_name):
            entries = self.store.load()
            entry = self._require_entry(entries, sequence_id)
            rv = SequenceRenderVersion(version_id=SequenceRenderVersion.new_version_id(),
                                       sequence_id=sequence_id, status=RSTATUS_QUEUED)
            entry.render_versions.append(rv)
            self.store.save(entries)
            return rv

    def prepare_render(self, sequence_id: str, version_id: str
                       ) -> Tuple[SequenceRenderVersion, SequenceTimelinePlan]:
        """Phase 1：snapshot sequence + source video versions + basis（§23 Phase 1）。
        零 provider 调用；fail closed 点：§7 解析 / §14 probe / §12 trim 验证。
        probe/hash 等长工作在锁外；行更新在锁内。"""
        # 读块（无锁读——快照的核对在 Phase 3：canonical 若中途变化即 race）
        block = self._require_entry(self.store.load(), sequence_id).block
        try:
            project = self._load_project()
            # 函数内 import：单测经 monkeypatch(src.sequence.media_ops.probe_video)
            # 注入伪探测——调用时绑定保证 patch 生效
            from .media_ops import probe_video
            resolved: List[ResolvedClip] = []
            for i, clip in enumerate(block.clips):
                _shot, version = resolve_clip_source(project, clip)
                probe = probe_video(str(version.local or ""))
                resolved.append(ResolvedClip(
                    index=i, shot_id=clip.shot_id, version_id=version.version_id,
                    source_file=str(version.local), trim_in_ms=clip.trim_in_ms,
                    trim_out_ms=clip.trim_out_ms,
                    source_duration_ms=probe["duration_ms"],          # §14 文件实测
                    has_audio=bool(probe["has_audio"])))
            plan = build_timeline_plan(resolved)                      # §13 + §12 验证
            basis = build_render_basis(block, resolved)               # §19 快照
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
            rv.source_video_versions = [
                {"shot_id": rc.shot_id, "version_id": rc.version_id} for rc in resolved]
            rv.planned_duration_ms = plan.total_duration_ms
            rv.status = RSTATUS_RENDERING
            rv.error = None
            self.store.save(entries)
        return rv, plan

    def execute_render(self, sequence_id: str, version_id: str,
                       plan: SequenceTimelinePlan) -> str:
        """Phase 2：ffmpeg render——不持锁（§23）。trim+归一化逐 clip，
        concat hard cut（§11）。返回最终输出路径。"""
        self._require_entry(self.store.load(), sequence_id).get_render(version_id)
        from .media_ops import concat_clips, render_clip
        version_dir = self._sequences_root() / sequence_id
        work_dir = version_dir / "_work" / version_id
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            segments: List[str] = []
            for i, tc in enumerate(plan.clips):
                seg = work_dir / f"seg_{i:03d}_{tc.shot_id}.mp4"
                render_clip(tc.source_file, str(seg),
                            trim_in_ms=tc.trim_in_ms, trim_out_ms=tc.trim_out_ms,
                            source_duration_ms=tc.source_duration_ms,
                            has_audio=bool(tc.has_audio))   # probe 实测随 plan 传递
                segments.append(str(seg))
            output_file = version_dir / f"{version_id}.mp4"
            concat_clips(segments, str(output_file))
        except SequenceError as e:
            self._mark_failed(sequence_id, version_id, e.code, e.message)
            raise
        except Exception as e:
            self._mark_failed(sequence_id, version_id, "render_failed", str(e))
            raise
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)   # 中间段不作为 evidence 保留
        return str(version_dir / f"{version_id}.mp4")

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
        """Phase 3：recompute basis（§23）。
        - 依赖已变 → sequence_dependency_changed_before_commit：status=raced_superseded，
          输出保留为 evidence，不得 attach 为 CURRENT；
        - 一致 → ffprobe 校验输出（§30 机器检查）→ status=done。
        🔴 绝不自动 select（§10）。"""
        # 锁外完成 recompute + 输出校验（决策依据全部只读）
        entry = self._require_entry(self.store.load(), sequence_id)
        rv_snapshot = entry.get_render(version_id)
        if rv_snapshot is None:
            raise SequenceError("render_version_not_found",
                                f"render version 不存在: {version_id}")
        output_file = self._sequences_root() / sequence_id / f"{version_id}.mp4"
        if not output_file.exists():
            self._mark_failed(sequence_id, version_id, "commit_failed",
                              "Phase 2 输出缺失（render 中断？）")
            raise SequenceError("commit_failed", "Phase 2 输出缺失（render 中断？）")

        project = self._load_project()
        current = compute_current_basis(project, entry.block)
        mismatched = basis_mismatched_keys(rv_snapshot.generation_basis or {}, current)
        raced = bool(mismatched)
        if not raced:
            from .media_ops import validate_output
            result = validate_output(str(output_file),
                                     planned_total_ms=rv_snapshot.planned_duration_ms or 0,
                                     n_clips=len(entry.block.clips))

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
            self.store.save(entries)
        if raced:
            raise SequenceError(
                "sequence_dependency_changed_before_commit",
                f"render 窗口内依赖已变化（{', '.join(mismatched)}）——"
                f"输出保留为 evidence（{output_file.name}），不得 attach 为 CURRENT；显式重渲染")
        return {"version_id": version_id, "status": RSTATUS_DONE,
                "duration_ms": result["duration_ms"],
                "planned_duration_ms": result["planned_duration_ms"],
                "duration_delta_ms": result["duration_delta_ms"]}

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

    # ============ freshness 读模型（24.md §20/§21）============

    def freshness(self, sequence_id: str, version_id: Optional[str] = None) -> Dict[str, Any]:
        """final render（或指定 version）的 freshness。纯读，绝不触发 render（§22）。"""
        entries = self.store.load()
        entry = self._require_entry(entries, sequence_id)
        rv = (entry.get_render(version_id)
              if version_id else entry.final_render_version())
        return sequence_render_freshness(self._load_project(), entry.block, rv)

    def render_output_path(self, sequence_id: str, version_id: str) -> Optional[Path]:
        """render 输出文件路径（scratch review page 播放用；不存在 → None）。"""
        p = self._sequences_root() / sequence_id / f"{version_id}.mp4"
        return p if p.exists() else None
