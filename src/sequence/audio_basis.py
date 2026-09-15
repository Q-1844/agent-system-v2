# src/sequence/audio_basis.py
# Sequence S2 — Audio Source 解析（25.md §9/§10/§33）、basis v2 音频段（§26）、
# 统一 mismatch 比较（§27/§28/§29 freshness 与 §30 race guard 共用）。
#
# 🔴 Audio source truth（§10）：必须由显式 source reference 解析；禁止扫描目录/
#    mtime 猜/glob/*.wav/默认取数组末位。解析不到 FAIL CLOSED。
# 🔴 源内容指纹（§28）：artifact id 不变但文件内容变 → sha256 变 → 旧 render STALE，
#    绝不只依赖文件名/id。
# 🔴 无有效音频流的源（§33）：FAIL CLOSED（audio_source_has_no_audio）——这是
#    Sequence Audio Source，不是 S1 视频兼容场景，不自动补 silence 掩盖错误。
# 🔴 A1 复用纪律（25.md §4）：a1_dialogue_audio 引用只读 DialogueAudioStore，
#    绝不修改/重构 A1；不复制 VoiceGenerationPack。
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.models import Project
from src.resources.generation import fingerprint, sha256_bytes

from .audio_models import (
    AUDIO_IMPORTS_DIR,
    AUDIO_SOURCE_KIND_A1,
    AUDIO_SOURCE_KIND_IMPORTED,
    SEQUENCE_AUDIO_MIXER_SCHEMA,
    SequenceAudioPlan,
    SequenceAudioSourceRef,
)
from .basis import build_render_basis, compute_current_basis
from .models import SequenceError
from .timeline import ResolvedClip

# repo 根（A1 store 里的 production_wav.path 是 repo 根相对或绝对）
_REPO_ROOT = Path(__file__).resolve().parents[2]


# ============ Audio source 解析（§9/§10/§33）============

@dataclass
class ResolvedAudioSource:
    """Phase 1 对单个 audio layer 源的解析结果（文件事实，全部现场探测）。"""
    layer_id: str
    ref: Dict[str, Any]           # ref.semantic_snapshot()
    file: str                     # authoritative 音频文件路径
    duration_ms: int              # ffprobe 实测（§5-3：不信任库值）
    has_audio: bool
    sha256: str                   # §28 源内容指纹


def _probe_audio_file(path: Path, ctx: str) -> ResolvedAudioSource:
    from .media_ops import probe_video   # 通用容器探测（video/audio 同一实现）
    if not path.exists() or path.stat().st_size == 0:
        raise SequenceError("audio_source_missing", f"音频源文件不存在或为空: {path}（{ctx}）")
    info = probe_video(str(path))
    if not info["has_audio"]:
        raise SequenceError(
            "audio_source_has_no_audio",
            f"音频源无有效音频流: {path}（{ctx}）——§33 FAIL CLOSED，不自动补 silence")
    return info


def resolve_audio_source(project_dir, layer_id: str,
                         ref: SequenceAudioSourceRef) -> ResolvedAudioSource:
    """artifact reference → authoritative 音频文件（fail closed）。"""
    project_dir = Path(project_dir)
    if ref.kind == AUDIO_SOURCE_KIND_A1:
        # 只读 A1 store（25.md §4：不重构 A1、不复制 pack）
        from src.audio.store import DialogueAudioStore
        doc = DialogueAudioStore(project_dir, project_dir.name).load_or_none()
        if doc is None:
            raise SequenceError("audio_source_not_found",
                                f"A1 audio store 不存在（dialogue {ref.dialogue_id}）")
        entry = (doc.get("dialogues") or {}).get(ref.dialogue_id)
        if not entry:
            raise SequenceError("audio_source_not_found",
                                f"A1 dialogue 不存在: {ref.dialogue_id}")
        version = next((v for v in (entry.get("audio_versions") or [])
                        if v.get("audio_version_id") == ref.audio_version_id), None)
        if version is None:
            raise SequenceError("audio_source_not_found",
                                f"A1 audio version 不存在: {ref.audio_version_id}"
                                f"（dialogue {ref.dialogue_id}）")
        raw_path = ((version.get("production_wav") or {}).get("path") or "")
        p = Path(raw_path)
        if not p.is_absolute():
            p = _REPO_ROOT / raw_path
        info = _probe_audio_file(p, f"a1:{ref.dialogue_id}/{ref.audio_version_id}")
    elif ref.kind == AUDIO_SOURCE_KIND_IMPORTED:
        # 规范目录守卫：只允许 output/audio_imports/ 内文件（ref 层已限，此处双保险）
        root = (project_dir / "output" / AUDIO_IMPORTS_DIR).resolve()
        p = (project_dir / "output" / ref.file).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            raise SequenceError("audio_source_not_found",
                                f"imported 路径越出 {AUDIO_IMPORTS_DIR}/: {ref.file}")
        info = _probe_audio_file(p, f"imported:{ref.file}")
    else:   # ref 构造层已挡；防御性 fail closed
        raise SequenceError("source_ref_invalid", f"未知 audio source kind: {ref.kind!r}")
    return ResolvedAudioSource(layer_id=layer_id, ref=ref.semantic_snapshot(),
                               file=str(p), duration_ms=info["duration_ms"],
                               has_audio=True, sha256=sha256_bytes(p.read_bytes()))


# ============ basis v2 音频段（25.md §26）============

def _layer_basis_entry(layer, sha256: Optional[str]) -> Dict[str, Any]:
    """单层 basis 条目：layer_id 为 provenance；比对键 = 语义 + 源指纹（见
    _LAYER_COMPARE_KEYS——layer_id 变更不误报）。"""
    return {"layer_id": layer.layer_id, **layer.semantic_snapshot(),
            "source_artifact_sha256": sha256}


# 层级比对键（actual dependencies：语义 + 源内容；layer_id 是句柄不比对）
_LAYER_COMPARE_KEYS = ("role", "source", "start_ms", "source_in_ms", "duration_ms",
                       "gain_db", "fade_in_ms", "fade_out_ms", "source_artifact_sha256")


def build_audio_sections(project_dir, plan: SequenceAudioPlan,
                         resolved: Optional[List[ResolvedAudioSource]] = None
                         ) -> Dict[str, Any]:
    """basis v2 的 audio 段（§26）。render Phase 1 传入已解析的 resolved（避免重复
    探测）；Phase 3 / freshness 传 None → 现场重解析（§27/§28）。
    解析失败不抛——诚实降级为带 resolution_error 的段（与 stored 比对即 STALE）。"""
    layers: List[Dict[str, Any]] = []
    error: Optional[str] = None
    if resolved is None:
        resolved = []
        for layer in plan.layers:
            try:
                resolved.append(resolve_audio_source(project_dir, layer.layer_id, layer.source))
            except SequenceError as e:
                error = e.code
                resolved.append(ResolvedAudioSource(
                    layer_id=layer.layer_id, ref=layer.source.semantic_snapshot(),
                    file="", duration_ms=0, has_audio=False, sha256=""))
    for layer, r in zip(plan.layers, resolved):
        layers.append(_layer_basis_entry(layer, r.sha256 if r else None))
    return {"audio_plan_revision": plan.revision, "layers": layers,
            "mixer_schema": SEQUENCE_AUDIO_MIXER_SCHEMA, "resolution_error": error}


def build_render_basis_v2(project_dir, block, resolved_clips: List[ResolvedClip],
                          plan: SequenceAudioPlan,
                          resolved_audio: List[ResolvedAudioSource]) -> Dict[str, Any]:
    """Phase 1 快照：v1 全部键（§26 继续保留）+ audio 段 + schema 升 v2。"""
    basis = build_render_basis(block, resolved_clips)
    basis["schema"] = "sequence_render_basis_v2"
    basis["audio"] = build_audio_sections(project_dir, plan, resolved_audio)
    return basis


def compute_current_render_basis(project: Project, project_dir, block,
                                 plan: Optional[SequenceAudioPlan]) -> Dict[str, Any]:
    """Phase 3 / freshness 的当前全量 basis（plan 存在 → v2 形状；否则 v1 形状）。"""
    current = compute_current_basis(project, block)
    if plan is not None and plan.layers:
        current["schema"] = "sequence_render_basis_v2"
        current["audio"] = build_audio_sections(project_dir, plan)
    return current


# ============ 统一 mismatch 比较（freshness §27/§28/§29 与 race guard §30 共用）============

def full_basis_mismatched_keys(recorded: Dict[str, Any], current: Dict[str, Any],
                               *, plan_present: bool) -> List[str]:
    """stored basis vs 当前重算全量 basis 的不一致键。
    - plan 不存在：只比 v1 键；stored 已是 v2 → audio_plan_removed（plan 被清空后
      旧混音 render 不可能仍 CURRENT——§25 当前语义 = 纯 native AV）。
    - plan 存在：v1 键 + audio 段（revision/mixer/逐层语义+源指纹）；stored 是 v1 →
      audio_plan_untracked（§27：audio plan 是新依赖，旧 render 诚实 STALE）。"""
    from .basis import basis_mismatched_keys
    mismatched = basis_mismatched_keys(recorded, current)
    recorded_schema = recorded.get("schema")
    if plan_present:
        if recorded_schema != "sequence_render_basis_v2":
            mismatched.append("audio_plan_untracked")
            return mismatched
        a_stored = recorded.get("audio") or {}
        a_cur = current.get("audio") or {}
        for key in ("audio_plan_revision", "mixer_schema"):
            if a_stored.get(key) != a_cur.get(key):
                mismatched.append(f"audio.{key}")
        s_layers = a_stored.get("layers") or []
        c_layers = a_cur.get("layers") or []
        for i in range(max(len(s_layers), len(c_layers))):
            s = s_layers[i] if i < len(s_layers) else {}
            c = c_layers[i] if i < len(c_layers) else {}
            for key in _LAYER_COMPARE_KEYS:
                if s.get(key) != c.get(key):
                    mismatched.append(f"audio.layers[{i}].{key}")
        if a_cur.get("resolution_error"):
            mismatched.append("audio.resolution_error")
    else:
        if recorded_schema == "sequence_render_basis_v2":
            mismatched.append("audio_plan_removed")
    return mismatched
