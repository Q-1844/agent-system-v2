# src/sequence/basis.py
# Sequence S1 — Source Video Truth 解析（24.md §7）、sequence_render_basis_v1（§19）、
# SequenceRenderFreshness 读模型（§20/§21）。
#
# 🔴 Source Video Truth（24.md §7）：每次 render / freshness 重算时
#       clip.shot_id → Shot.final_video → VideoVersion → authoritative source mp4
#    必须复用既有 final video truth。禁止 glob 最新 mp4 / mtime 猜最新版 /
#    默认 video_versions[-1] / 扫描 output 目录。
# 🔴 basis = actual dependencies only（24.md §19）：sequence_id/revision + ordered
#    clips（resolved version + source file fingerprint + trim）+ render profile
#    指纹 + renderer schema。不 hash 整个 project / 无关 Shot / 无关 VideoVersion /
#    timestamp / temp path / review comment / API secret。
# 🔴 freshness = 纯 derived 读模型（24.md §20）：detect → expose → explicit render
#    （§22 No Auto Render）——本模块绝不触发任何 render。
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.core.models import Project, Shot, ShotStatus, VideoVersion
from src.resources.generation import fingerprint, sha256_bytes

from .models import (
    SEQUENCE_RENDER_BASIS_SCHEMA,
    SEQUENCE_RENDERER_SCHEMA,
    SequenceBlock,
    SequenceClip,
    SequenceError,
    SequenceRenderVersion,
    render_profile_fingerprint_for,
)
from .timeline import ResolvedClip


# ============ Source Video Truth（24.md §7）============

def resolve_clip_source(project: Project, clip: SequenceClip) -> Tuple[Shot, VideoVersion]:
    """把 clip.shot_id 解析为 authoritative source VideoVersion（final video truth）。
    🔴 只认 Shot.final_video 指针；解析不到 = 明确错误（fail closed），
    绝不回落 video_versions[-1] / glob / mtime 猜测（24.md §7 禁止清单）。"""
    shot = project.get_shot(clip.shot_id)
    if shot is None:
        raise SequenceError("shot_not_found", f"clip 引用的镜头不存在: {clip.shot_id}")
    if not shot.final_video:
        raise SequenceError(
            "shot_has_no_final_video",
            f"clip {clip.shot_id}: 无 final_video（Shot 未完成视频生产）")
    version = next((v for v in (shot.video_versions or [])
                    if v.version_id == shot.final_video), None)
    if version is None:
        raise SequenceError(
            "final_video_version_missing",
            f"clip {clip.shot_id}: final_video 指针悬空（{shot.final_video} 不在 video_versions）")
    if version.status != ShotStatus.DONE.value:
        raise SequenceError(
            "final_video_not_done",
            f"clip {clip.shot_id}: final_video {version.version_id} 状态 {version.status}（未完成）")
    return shot, version


# ============ sequence_render_basis_v1（24.md §19）============

def source_video_fingerprint(path: str) -> str:
    """source mp4 文件字节指纹（sha256 全 hex）。文件变 = 指纹变 = STALE（§20）。
    几 MB 级短视频全量 hash 开销毫秒级；诚实优于 size/mtime 近似。"""
    p = Path(path)
    if not p.exists():
        raise SequenceError("source_file_missing",
                            f"source mp4 不存在: {path}（final video truth 已失效）")
    return sha256_bytes(p.read_bytes())


def build_render_basis(block: SequenceBlock, resolved: List[ResolvedClip]) -> Dict[str, Any]:
    """Phase 1 快照 basis（24.md §19 sequence_render_basis_v1）。
    actual dependencies only：只覆盖本次 render 真实消费的事实。"""
    return {
        "schema": SEQUENCE_RENDER_BASIS_SCHEMA,
        "sequence_id": block.sequence_id,
        "sequence_revision": block.revision,
        "clips": [
            {
                "shot_id": rc.shot_id,
                "resolved_video_version_id": rc.version_id,
                "source_video_fingerprint": source_video_fingerprint(rc.source_file),
                "trim_in_ms": rc.trim_in_ms,
                "trim_out_ms": rc.trim_out_ms,
            }
            for rc in resolved
        ],
        "render_profile_fingerprint": render_profile_fingerprint_for(block.effective_render_profile()),
        "renderer_schema": SEQUENCE_RENDERER_SCHEMA,
        # 与 compute_current_basis 同构（race guard 整体相等比较的前提）；
        # build 路径已解析成功，恒 None。
        "resolution_error": None,
    }


def compute_current_basis(project: Project, block: SequenceBlock) -> Dict[str, Any]:
    """重算当前 canonical 的 render 依赖键（freshness / Phase 3 race guard 共用）。
    与 build_render_basis 同构：live 解析 final_video + 现场文件指纹。
    解析/指纹失败不抛——诚实降级为带 error 标记的键（与 stored 对比即 STALE，
    24.md §20 精神：依赖已不可满足 = 旧 render 不可能仍 CURRENT）。"""
    clips: List[Dict[str, Any]] = []
    error: Optional[str] = None
    for clip in block.clips:
        try:
            _shot, version = resolve_clip_source(project, clip)
            clips.append({
                "shot_id": clip.shot_id,
                "resolved_video_version_id": version.version_id,
                "source_video_fingerprint": source_video_fingerprint(str(version.local or "")),
                "trim_in_ms": clip.trim_in_ms,
                "trim_out_ms": clip.trim_out_ms,
            })
        except SequenceError as e:
            error = e.to_dict()["code"]
            clips.append({
                "shot_id": clip.shot_id,
                "resolved_video_version_id": None,
                "source_video_fingerprint": None,
                "trim_in_ms": clip.trim_in_ms,
                "trim_out_ms": clip.trim_out_ms,
            })
    return {
        "schema": SEQUENCE_RENDER_BASIS_SCHEMA,
        "sequence_id": block.sequence_id,
        "sequence_revision": block.revision,
        "clips": clips,
        "render_profile_fingerprint": render_profile_fingerprint_for(block.effective_render_profile()),
        "renderer_schema": SEQUENCE_RENDERER_SCHEMA,
        "resolution_error": error,
    }


# ============ SequenceRenderFreshness（24.md §20/§21）============

FRESHNESS_MISSING = "missing"                    # 无 final render（或指针悬空/非 done）
FRESHNESS_CURRENT = "current"
FRESHNESS_STALE = "stale"
FRESHNESS_UNTRACKED_LEGACY = "untracked_legacy"  # render version 无 basis（诚实暴露）


def _clip_mismatches(stored_clips: List[Dict[str, Any]],
                     current_clips: List[Dict[str, Any]]) -> List[str]:
    """逐 clip 对比 resolved version / source fingerprint / trim。
    键名形如 clips[1].resolved_video_version_id（24.md §20 STALE 触发清单逐项可读）。"""
    mismatched: List[str] = []
    n = max(len(stored_clips), len(current_clips))
    for i in range(n):
        s = stored_clips[i] if i < len(stored_clips) else {}
        c = current_clips[i] if i < len(current_clips) else {}
        for key in ("resolved_video_version_id", "source_video_fingerprint",
                    "trim_in_ms", "trim_out_ms"):
            if s.get(key) != c.get(key):
                mismatched.append(f"clips[{i}].{key}")
    return mismatched


def basis_mismatched_keys(stored: Dict[str, Any], current: Dict[str, Any]) -> List[str]:
    """stored basis vs 当前重算 basis 的不一致键（freshness 与 Phase 3 race guard
    共用同一对比逻辑——24.md §20 STALE 触发清单 = §23 race 触发清单）。"""
    mismatched: List[str] = []
    for key in ("sequence_revision", "render_profile_fingerprint", "renderer_schema"):
        if stored.get(key) != current.get(key):
            mismatched.append(key)
    mismatched += _clip_mismatches(stored.get("clips") or [], current.get("clips") or [])
    if current.get("resolution_error"):
        # 当前解析已失败（如 final_video 被清空）——依赖键已不可满足，诚实 STALE
        mismatched.append("resolution_error")
    return mismatched


def sequence_render_freshness(project: Project, block: SequenceBlock,
                              render_version: Optional[SequenceRenderVersion], *,
                              plan=None, project_dir=None) -> Dict[str, Any]:
    """派生读模型（24.md §20 + 25.md §27/§28/§29）：final render 的 freshness。
    - missing：无 final render / 指针悬空 / 非 done（或调用方传入 None）；
    - untracked_legacy：render version 无 basis（诚实表示无法证明其 freshness）；
    - current：stored basis 与当前重算依赖键一致；
    - stale：v1 键（revision/clip resolved version/源指纹/trim/profile/schema）
      或 audio 段（plan revision/逐层语义/源 sha256/mixer schema）任一不一致。
    plan 为 None 或空 layers → 纯 v1 语义（25.md §25：无 AudioPlan = S1，行为锁死）；
    plan 存在而 stored 为 v1 → audio_plan_untracked STALE；plan 被清空而 stored 为
    v2 → audio_plan_removed STALE。
    无关 Shot 变化不进 basis → 仍 CURRENT（24.md §20）。
    🔴 §22 No Auto Render：纯读模型，绝不触发任何重渲染。"""
    if render_version is None:
        return {"status": FRESHNESS_MISSING, "sequence_id": block.sequence_id,
                "detail": {"reason": "no_final_render"}}
    recorded = dict(render_version.generation_basis or {})
    if not recorded:
        return {"status": FRESHNESS_UNTRACKED_LEGACY, "sequence_id": block.sequence_id,
                "detail": {"reason": "generation_basis_missing",
                           "version_id": render_version.version_id,
                           "hint": "旧 render 无 generation provenance——可继续播放/保留，"
                                   "但无法证明其 freshness（24.md §20）"}}
    # 函数内 import（避免 basis <-> audio_basis 循环依赖；S1 无 plan 路径不受影响）
    from .audio_basis import compute_current_render_basis, full_basis_mismatched_keys
    plan_present = plan is not None and bool(plan.layers)
    current = compute_current_render_basis(project, project_dir, block, plan)
    mismatched = full_basis_mismatched_keys(recorded, current, plan_present=plan_present)
    if mismatched:
        return {"status": FRESHNESS_STALE, "sequence_id": block.sequence_id,
                "detail": {"reason": "dependency_keys_mismatch",
                           "version_id": render_version.version_id,
                           "mismatched_keys": mismatched,
                           "hint": "sequence should be explicitly re-rendered"
                                   "（24.md §21：不 auto render）"}}
    return {"status": FRESHNESS_CURRENT, "sequence_id": block.sequence_id,
            "detail": {"version_id": render_version.version_id}}
