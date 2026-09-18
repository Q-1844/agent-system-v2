# src/scene_system/visual_basis.py
# Scene System V1 — Phase S2A §9/§10：LocationView GenerationBasis + 视觉语义指纹
# + approved 参考确定性选择。
#
# 🔴 location_visual_basis_fingerprint（§9）：Location 自己的 revision 不覆盖
#    Anchor / Layout / View——生成语义指纹必须覆盖真正会影响视觉结果的
#    Location 身份 + 全部相关 Anchors（含 relations）+ SetLayout + View 定义
#    + 实际选中的 approved 参考（含内容 sha256）+ prompt + model/seed/count。
#    故意不含：时间戳字段、状态机字段（generation_status/approval_status——
#    无害写不误判 stale）、location_revision 裸值（同 Resources 1C5 教训）。
#    生成结束后同参数重算比对，不同 → stale_generation_basis，拒绝 commit。
#
# 🔴 参考选择（§10，Minimum but sufficient）：
#    - 第一张 approved view 之前：无参考，纯 identity+anchors+layout+prompt 生成。
#    - 之后：优先 1 张最相关 approved LocationView；仅当目标 view 的 required
#      anchors 未被第一张覆盖时补第 2 张（能补足结构信息的）。
#    - 绝不把所有 approved view 无脑全发。
#    - 预算 MAX_VIEW_REFERENCE_REFS = 2（T13 enforced）。
#    - 选择确定性：显式 reference_view_ids（校验后按给定顺序）；或自动规则
#      （可见 anchor 交集降序 → view_id 升序）。选择全量记录进 basis。
#
# 纯函数式：不写盘、不调网络、不持锁（caller 在锁内调用）。
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.resources.generation import fingerprint, to_agnes_image_ref
from src.resources.models import ResourceDocument
from src.resources.store import ResourceStore

from .location_prompt import build_location_view_prompt
from .models import (
    ApprovalState, Location, LocationView, SceneSystemDocument, SetLayout,
    SpatialAnchor,
)

MAX_VIEW_REFERENCE_REFS = 2   # 硬预算上限（§10：最少但足够；T13）


def _location_snapshot(loc: Location) -> dict:
    """Location 身份快照（§9：name/description/identity_description/style_notes；
    不含时间戳/状态字段）。"""
    return {"location_id": loc.location_id, "name": loc.name,
            "description": loc.description,
            "identity_description": loc.identity_description,
            "style_notes": loc.style_notes}


def _anchor_snapshot(a: SpatialAnchor) -> dict:
    """Anchor 快照（含 relations；不含时间戳）。"""
    return {"anchor_id": a.anchor_id, "name": a.name, "kind": a.kind,
            "description": a.description, "semantic_zone": a.semantic_zone,
            "relations": [{"relation": r.relation, "object_anchor_id": r.object_anchor_id,
                           "note": r.note} for r in sorted(a.relations, key=lambda x: x.relation)]}


def _layout_snapshot(l: Optional[SetLayout]) -> Optional[dict]:
    if l is None:
        return None
    return {"layout_id": l.layout_id, "orientation_convention": l.orientation_convention,
            "notes": l.notes,
            "placements": [{"anchor_id": p.anchor_id, "zone": p.zone, "x": p.x, "y": p.y,
                            "note": p.note} for p in l.placements],
            "reference_asset_id": l.reference_asset_id}


def _view_snapshot(v: LocationView) -> dict:
    """View 定义快照（§9：camera_zone/look_direction/visible_anchor_ids/label）。"""
    return {"view_id": v.view_id, "label": v.label, "camera_zone": v.camera_zone,
            "look_direction": v.look_direction,
            "visible_anchor_ids": list(v.visible_anchor_ids)}


def load_resources_doc_or_none(pdir) -> Optional[ResourceDocument]:
    """只读加载 resources.json。损坏 → None（caller 据此做诚实处理，不静默当空）。"""
    try:
        return ResourceStore(pdir).load()
    except Exception:
        return None


# ============ approved 参考候选与确定性选择（§10）============
def _approved_reference_candidates(doc: SceneSystemDocument, rdoc: Optional[ResourceDocument],
                                   location_id: str) -> List[LocationView]:
    """同 location、已 approved 且带 reference_asset_id 的 views（view_id 升序）。"""
    return sorted((v for v in doc.views.values()
                   if v.location_id == location_id
                   and v.approval_status == ApprovalState.APPROVED.value
                   and v.reference_asset_id),
                  key=lambda v: v.view_id)


def _ref_entry_for_asset(project_dir, rdoc: ResourceDocument, asset_id: str) -> Optional[dict]:
    """approved ReferenceAsset → basis entry（含内容 sha256）；缺失/不可用 → None。"""
    a = rdoc.reference_assets.get(asset_id)
    if a is None:
        return None
    if to_agnes_image_ref(project_dir, url=a.url, local=a.local) is None:
        return None
    from src.resources.generation import load_image_bytes, sha256_bytes
    data = load_image_bytes(project_dir, a.local)
    return {"view_id": None, "asset_id": a.asset_id, "url": a.url, "local": a.local,
            "sha256": sha256_bytes(data) if data is not None else None,
            "role": "approved_view_reference"}


def select_reference_views(project_dir, doc: SceneSystemDocument,
                           rdoc: Optional[ResourceDocument], target: LocationView, *,
                           explicit_reference_view_ids: Optional[List[str]] = None) -> dict:
    """确定性选择本次生成使用的 approved LocationView 参考（§10）。

    返回 {selected: [entry], reference_view_ids, input_budget, notes}。
    - 有 approved 候选但 resources.json 不可读 → resources_lookup_failed（fail closed，
      不静默退化为无参考生成——那会产出与已批准视觉身份脱节的图）。
    - explicit：逐个校验（存在/approved/同 location/参考可用），超预算拒绝。
    - auto：交集降序 → view_id 升序取第 1；仅当其 visible 集未覆盖目标 required
      anchors 时补第 2 张（能补足最多未覆盖 anchors 的；同 view_id 升序决胜）。
    纯函数；选择结果全量记录进 basis。"""
    pdir = Path(project_dir)
    out = {"selected": [], "reference_view_ids": [],
           "mode": "explicit" if explicit_reference_view_ids else "auto",
           "explicit_ids": list(explicit_reference_view_ids or []),
           "input_budget": {"max": MAX_VIEW_REFERENCE_REFS, "used": 0}, "notes": []}
    candidates = _approved_reference_candidates(doc, rdoc, target.location_id)

    if explicit_reference_view_ids:
        # 显式请求：逐个诚实校验（revoked/未批准/不可用都要具体报错，不被空候选 early-return 掩盖）
        if rdoc is None and candidates:
            return {**out, "error": "resources_lookup_failed",
                    "detail": "approved 参考候选存在但 resources.json 不可读（fail closed）"}
        usable: List[Tuple[LocationView, dict]] = []
        if rdoc is not None:
            for cv in candidates:
                entry = _ref_entry_for_asset(pdir, rdoc, cv.reference_asset_id)
                if entry is not None:
                    usable.append((cv, entry))
        by_id = {cv.view_id: (cv, e) for cv, e in usable}
        approved_ids = {cv.view_id for cv in candidates}
        seen: List[str] = []
        for vid in explicit_reference_view_ids:
            if len(seen) >= MAX_VIEW_REFERENCE_REFS:
                return {**out, "error": "reference_budget_exceeded",
                        "detail": f"参考预算上限 {MAX_VIEW_REFERENCE_REFS}（§10 最少但足够）"}
            if vid in seen:
                continue
            if vid not in approved_ids:
                return {**out, "error": "reference_view_not_approved",
                        "detail": (f"view {vid} 不是本 location 已 approved 的参考候选"
                                   f"（revoked/未批准皆不可作 base）")}
            if vid not in by_id:
                return {**out, "error": "reference_not_usable",
                        "detail": f"view {vid} 的 approved ReferenceAsset 不可读/不可用"}
            cv, e = by_id[vid]
            seen.append(vid)
            out["selected"].append({**e, "view_id": cv.view_id})
        out["reference_view_ids"] = [e["view_id"] for e in out["selected"]]
        out["input_budget"]["used"] = len(out["selected"])
        return out

    # auto 路径
    if not candidates:
        out["notes"].append("no_approved_reference_available")
        return out
    if rdoc is None:
        out["error"] = "resources_lookup_failed"
        out["notes"].append("approved references exist but resources.json unreadable")
        return out
    usable: List[Tuple[LocationView, dict]] = []
    for cv in candidates:
        entry = _ref_entry_for_asset(pdir, rdoc, cv.reference_asset_id)
        if entry is not None:
            usable.append((cv, entry))

    target_set = set(target.visible_anchor_ids or [])
    ranked = sorted(usable, key=lambda ce: (-len(target_set & set(ce[0].visible_anchor_ids)),
                                            ce[0].view_id))
    if ranked:
        first_cv, first_e = ranked[0]
        out["selected"].append({**first_e, "view_id": first_cv.view_id})
        covered = set(first_cv.visible_anchor_ids) & target_set
        uncovered = target_set - covered
        if uncovered:
            # 第二张：对未覆盖 anchors 贡献最大者（并列 view_id 升序）
            second = sorted(((cv, e) for cv, e in ranked[1:]
                             if set(cv.visible_anchor_ids) & uncovered),
                            key=lambda ce: (-len(set(ce[0].visible_anchor_ids) & uncovered),
                                            ce[0].view_id))
            if second:
                cv2, e2 = second[0]
                out["selected"].append({**e2, "view_id": cv2.view_id})
                out["notes"].append("second_reference_added_for_uncovered_anchors")

    out["reference_view_ids"] = [e["view_id"] for e in out["selected"]]
    out["input_budget"]["used"] = len(out["selected"])
    return out


# ============ GenerationBasis（§9）============
def compute_location_visual_basis(project_dir, doc: SceneSystemDocument,
                                  rdoc: Optional[ResourceDocument],
                                  location: Location, view: LocationView, *,
                                  model_id: str, size_tier: str, ratio: str,
                                  seed: Optional[int], candidate_count: int,
                                  reference_selection: dict,
                                  visual_profile=None) -> dict:
    """从当前 canonical 状态构造一次 LocationView 生成的完整事实快照 + 语义指纹。
    同状态 + 同参数 → 同 basis（确定性）。纯函数：不写盘、不调网络。
    reference_selection = select_reference_views 的成功结果（含 mode/explicit_ids——
    重算时按同模式对当前状态重选，参考变化 → stale，同 Resources 语义）。
    visual_profile（P1 §12）：项目级介质/艺术方向（非 Location 身份、非 SceneState）；
    非 None 时编译后的 style 指令进 prompt 后缀，snapshot + fingerprint 入 basis。
    caller 在指纹计算之后自行追加 started_at（不入指纹）。"""
    anchors = sorted((a for a in doc.anchors.values() if a.location_id == location.location_id),
                     key=lambda a: a.anchor_id)
    layout = next((l for l in doc.layouts.values() if l.location_id == location.location_id), None)

    domain_prompt = build_location_view_prompt(
        location, anchors, layout, view,
        has_references=bool(reference_selection.get("selected")),
        reference_view_ids=list(reference_selection.get("reference_view_ids") or []))
    from src.visual_profile.compiler import append_style_instruction
    from src.visual_profile.models import (profile_snapshot,
                                           visual_profile_fingerprint)
    prompt = append_style_instruction(domain_prompt, visual_profile)

    basis: Dict[str, Any] = {
        "view_id": view.view_id,
        "location_id": location.location_id,
        "location_snapshot": _location_snapshot(location),
        "anchors_snapshot": [_anchor_snapshot(a) for a in anchors],
        "layout_snapshot": _layout_snapshot(layout),
        "view_snapshot": _view_snapshot(view),
        "reference_selection_mode": reference_selection.get("mode", "auto"),
        "explicit_reference_view_ids": list(reference_selection.get("explicit_ids") or []),
        "selected_references": [{k: e.get(k) for k in ("role", "view_id", "asset_id",
                                                        "url", "local", "sha256")}
                                 for e in reference_selection.get("selected", [])],
        "reference_view_ids": list(reference_selection.get("reference_view_ids", [])),
        "input_budget": dict(reference_selection.get("input_budget") or {}),
        "candidate_count": candidate_count,
        "seed": seed,
        "model": {"provider": ("sensenova" if str(model_id).startswith("sensenova")
                               else "agnes"),
                  "model_id": model_id,
                  "size_tier": size_tier, "ratio": ratio},
        "prompt": prompt,
    }
    if visual_profile is not None:
        basis["visual_profile"] = profile_snapshot(visual_profile)
        basis["visual_profile_fingerprint"] = visual_profile_fingerprint(visual_profile)
    basis["basis_fingerprint"] = fingerprint(basis)
    return basis


def _profile_stale_reason(project_dir, basis: dict) -> Optional[str]:
    """P1 §13：basis 存有 visual_profile_fingerprint 且与当前 profile 不一致 →
    project_visual_profile_changed。legacy basis（无该键）→ None（P1 §14 兼容语义）。"""
    stored = basis.get("visual_profile_fingerprint")
    if not stored:
        return None
    from src.visual_profile import load_profile_or_none
    from src.visual_profile.models import visual_profile_fingerprint
    profile = load_profile_or_none(project_dir)
    current_fp = visual_profile_fingerprint(profile) if profile is not None else None
    if current_fp != stored:
        return "project_visual_profile_changed"
    return None


def recompute_location_visual_basis_still_holds(project_dir, doc: SceneSystemDocument,
                                                rdoc: Optional[ResourceDocument],
                                                view: LocationView, basis: dict
                                                ) -> Tuple[bool, str]:
    """§9/§13 阶段3：生成结束后【对当前 canonical 同参数重算】basis，比对语义指纹。
    参考按原模式（auto/explicit）对当前状态重选——approved 参考图变化/失效 → 指纹不同
    → stale（同 Resources 语义：recompute 用当前状态）。
    view 已删除 → (False, "view_missing")；profile 变更 → (False,
    "project_visual_profile_changed")（P1 §13）；selection 失败/其余指纹不同
    → (False, "basis_changed")。绝不 runtime 偷偷替换 basis 后强行提交。"""
    location = doc.locations.get(view.location_id) if view is not None else None
    if view is None or location is None:
        return False, "view_missing"
    profile_reason = _profile_stale_reason(project_dir, basis)
    if profile_reason:
        return False, profile_reason
    from src.visual_profile import load_profile_or_none
    mode = basis.get("reference_selection_mode", "auto")
    explicit_ids = basis.get("explicit_reference_view_ids") or []
    fresh_selection = select_reference_views(
        project_dir, doc, rdoc, view,
        explicit_reference_view_ids=explicit_ids if mode == "explicit" else None)
    if fresh_selection.get("error"):
        return False, "basis_changed"
    current = compute_location_visual_basis(
        project_dir, doc, rdoc, location, view,
        model_id=basis["model"]["model_id"],
        size_tier=basis["model"]["size_tier"],
        ratio=basis["model"]["ratio"],
        seed=basis["seed"],
        candidate_count=basis["candidate_count"],
        reference_selection=fresh_selection,
        visual_profile=load_profile_or_none(project_dir))
    if current["basis_fingerprint"] != basis["basis_fingerprint"]:
        return False, "basis_changed"
    return True, ""
