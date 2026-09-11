# src/scene_system/instance_view_basis.py
# Scene System V1 — Phase S2B §13-§16：SceneInstanceView 参考选择 + GenerationBasis。
#
# 🔴 参考策略（§15/§16，最少但足够、确定性、硬预算）：
#   1 base approved LocationView reference（强制——没有母版绝不允许 SceneState 文生图）；
#   2 optional 同 SceneInstance 已 approved 的另一张 SceneInstanceView（peer，维持同一
#     剧情时刻的天气/灯光/损坏风格；确定性选择：scene_instance_view_id 升序第一个可用项）；
#   3 可见 props 的 approved references（每 prop 最多 1 张；确定性 prop_id 升序；最多 2 张）。
#   MAX_TOTAL_VISUAL_REFS = 4 硬预算；超出预算的 prop 仍以 text constraint 表达，
#   basis 记录 prop_refs_selected / prop_refs_omitted_due_to_budget——不假装全部使用了
#   视觉输入。绝不全量喂模型。
#
# 🔴 basis 覆盖（§13）：身份 + location/view 语义快照 + base 参考资产（id+checksum）+
#    base 期望指纹 + ViewRelevantSceneState 投影 + 可见 anchor 快照 + prop 语义快照 +
#    选中 prop 参考 + peer + prompt + model/seed/count。
#    §14 stale 规则由投影+快照自然涌现：可见状态变化 → 投影变 → 指纹变 → stale；
#    不可见变化不进投影 → 不 stale（T25/§33）。prop 语义快照用语义子集
#    （prop_id/name/description）而非裸 revision——updated_at-only 触碰不误判 stale。
#
# 🔴 Prop fail closed（§9）：可见 prop 在 Resources 缺失 → prop_missing；resources.json
#    不可读 → resources_lookup_failed。绝不文字生成后假装使用了 Prop。
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.resources.generation import fingerprint, load_image_bytes, sha256_bytes, to_agnes_image_ref
from src.resources.models import ResourceDocument

from . import generation_records
from .generation_records import approval_records_path
from .instance_view_prompt import build_scene_instance_view_prompt
from .models import (
    ApprovalState, Location, LocationView, SceneInstance, SceneInstanceView,
    SceneSystemDocument, SpatialAnchor,
)
from .state_projection import project_scene_state_for_view, visible_anchor_snapshots

MAX_TOTAL_VISUAL_REFS = 4   # §15 硬预算：1 base + 1 peer + ≤2 props
MAX_STATE_VIEW_PROP_REFS = 2   # §15 分项上限：props 视觉参考最多 2 张（不吃满总预算）


class _BasisInputError(Exception):
    """basis 构造的前置 fail closed（caller 转 receipt）。"""
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _asset_entry(project_dir, rdoc: ResourceDocument, asset_id: str, role: str) -> Optional[dict]:
    a = rdoc.reference_assets.get(asset_id) if rdoc is not None else None
    if a is None:
        return None
    if to_agnes_image_ref(project_dir, url=a.url, local=a.local) is None:
        return None
    data = load_image_bytes(project_dir, a.local)
    return {"role": role, "asset_id": a.asset_id, "url": a.url, "local": a.local,
            "checksum": a.checksum or (sha256_bytes(data) if data is not None else None)}


def select_state_view_visual_refs(project_dir, doc: SceneSystemDocument,
                                  rdoc: Optional[ResourceDocument],
                                  base_view: LocationView,
                                  instance_view: SceneInstanceView,
                                  scene_instance: SceneInstance, *,
                                  include_peer: bool = True) -> dict:
    """确定性选择 SceneInstanceView 生成的视觉参考（§15/§16）。
    返回 {selected, base_location_reference, peer_state_reference,
          prop_refs_selected, prop_refs_omitted_due_to_budget,
          props_text_only, input_budget}。
    前置 fail closed：base 参考不可用 / 可见 prop 缺失 / resources 不可读。"""
    pdir = Path(project_dir)
    out: Dict[str, Any] = {
        "selected": [],
        "base_location_reference": None,
        "peer_state_reference": None,
        "prop_refs_selected": [],
        "prop_refs_omitted_due_to_budget": [],
        "props_text_only": [],
        "input_budget": {"max": MAX_TOTAL_VISUAL_REFS, "used": 0},
    }
    if rdoc is None:
        raise _BasisInputError("resources_lookup_failed",
                               "resources.json 不可读（prop/base 可用性无法判定，fail closed）")

    # 1. base approved LocationView reference（强制）
    base_entry = _asset_entry(pdir, rdoc, base_view.reference_asset_id, "base_location_view")
    if base_entry is None:
        raise _BasisInputError("base_reference_missing",
                               f"base LocationView 的 ReferenceAsset 不可读: "
                               f"{base_view.reference_asset_id}")
    out["base_location_reference"] = {k: base_entry[k] for k in
                                      ("asset_id", "checksum")}
    out["selected"].append(base_entry)

    # 2. optional peer state view（同 SceneInstance、approved、非自身、参考可读；
    #    确定性：scene_instance_view_id 升序第一个可用项）
    if include_peer:
        peers = sorted((v for v in doc.scene_instance_views.values()
                        if v.scene_instance_id == instance_view.scene_instance_id
                        and v.scene_instance_view_id != instance_view.scene_instance_view_id
                        and v.approval_status == ApprovalState.APPROVED.value
                        and v.reference_asset_id),
                       key=lambda v: v.scene_instance_view_id)
        for p in peers:
            entry = _asset_entry(pdir, rdoc, p.reference_asset_id, "peer_state_view")
            if entry is None:
                continue
            entry["scene_instance_view_id"] = p.scene_instance_view_id
            out["peer_state_reference"] = {k: entry[k] for k in
                                           ("scene_instance_view_id", "asset_id", "checksum")}
            out["selected"].append(entry)
            break

    # 3. 可见 props 的 approved references（确定性 prop_id 升序；每 prop 1 张；≤2 张）
    projection = project_scene_state_for_view(scene_instance, base_view, doc.anchors.values())
    budget_left = min(MAX_TOTAL_VISUAL_REFS - len(out["selected"]),
                      MAX_STATE_VIEW_PROP_REFS)
    visible_props = sorted(projection.get("prop_placements", {}).items())
    for pid, placement in visible_props:
        prop = rdoc.props.get(pid)
        if prop is None:
            raise _BasisInputError("prop_missing",
                                   f"可见 Prop 在 Resources 中不存在: {pid}（§9 fail closed，"
                                   f"绝不假装使用了 Prop）")
        sem = {"prop_id": pid, "name": prop.name, "description": prop.description,
               "state": placement.get("state", ""),
               "anchor_id": placement.get("anchor_id", ""),
               "anchor_name": (doc.anchors.get(placement.get("anchor_id", "")).name
                               if doc.anchors.get(placement.get("anchor_id", "")) else "")}
        approved_ids = [aid for aid in (prop.approved_reference_asset_ids or [])
                        if aid in rdoc.reference_assets]
        if budget_left <= 0:
            # 超出分项/总预算：text-only 继续（§15：不假装全部使用了视觉输入）
            sem["visual_reference_missing"] = not approved_ids
            out["props_text_only"].append(sem)
            out["prop_refs_omitted_due_to_budget"].append(
                {"prop_id": pid, "had_approved_reference": bool(approved_ids)})
            continue
        if not approved_ids:
            # §10.A：有可靠 textual identity 但无 approved visual ref → text-only，诚实记录
            sem["visual_reference_missing"] = True
            out["props_text_only"].append(sem)
            continue
        # 确定性挑 1 张：cover_asset_id ∈ approved → cover；否则 approved 第一个
        pick = (prop.cover_asset_id if prop.cover_asset_id in approved_ids
                else approved_ids[0])
        entry = _asset_entry(pdir, rdoc, pick, "prop_reference")
        if entry is None:
            sem["visual_reference_missing"] = True
            out["props_text_only"].append(sem)
            continue
        entry["prop_id"] = pid
        out["selected"].append(entry)
        out["prop_refs_selected"].append({"prop_id": pid, "asset_id": pick,
                                          "checksum": entry["checksum"]})
        budget_left -= 1

    out["input_budget"]["used"] = len(out["selected"])
    return out


def compute_scene_instance_visual_basis(project_dir, doc: SceneSystemDocument,
                                        rdoc: Optional[ResourceDocument],
                                        scene_instance: SceneInstance,
                                        base_location: Location,
                                        base_view: LocationView,
                                        instance_view: SceneInstanceView, *,
                                        model_id: str, size_tier: str, ratio: str,
                                        seed: Optional[int], candidate_count: int,
                                        ref_selection: dict,
                                        include_peer: bool = True) -> dict:
    """SceneInstanceView GenerationBasis（§13）。纯函数；同状态+同参数 → 同 basis。
    caller 在指纹之后自行追加 started_at（不入指纹）。"""
    anchors = sorted((a for a in doc.anchors.values()
                      if a.location_id == base_location.location_id),
                     key=lambda a: a.anchor_id)
    projection = project_scene_state_for_view(scene_instance, base_view, anchors)

    # prop 语义快照（可见 props；语义子集——updated_at-only 触碰不误判 stale）
    prop_semantics: List[Dict[str, Any]] = []
    for pid, placement in sorted(projection.get("prop_placements", {}).items()):
        prop = rdoc.props.get(pid) if rdoc is not None else None
        prop_semantics.append({
            "prop_id": pid,
            "name": prop.name if prop else None,
            "description": prop.description if prop else None,
            "state": placement.get("state", ""),
            "anchor_id": placement.get("anchor_id", ""),
            "anchor_name": (doc.anchors.get(placement.get("anchor_id", "")).name
                            if doc.anchors.get(placement.get("anchor_id", "")) else ""),
            "visual_reference_missing": pid not in {
                e.get("prop_id") for e in ref_selection.get("prop_refs_selected", [])},
        })

    # base 期望指纹（可重算的 base 语义基准，§13 base_location_visual_basis fingerprint）
    from .view_review import build_review_expectations, expectations_fingerprint
    base_expectations = build_review_expectations(doc, base_view)
    base_fp = expectations_fingerprint(base_expectations, [base_view.reference_asset_id])

    prompt = build_scene_instance_view_prompt(
        base_location, base_view, anchors, projection, prop_semantics,
        has_peer_reference=ref_selection.get("peer_state_reference") is not None,
        peer_note=(ref_selection.get("peer_state_reference") or {}).get(
            "scene_instance_view_id", ""))

    basis: Dict[str, Any] = {
        "scene_instance_view_id": instance_view.scene_instance_view_id,
        "scene_instance_id": scene_instance.scene_instance_id,
        "story_scene_id": scene_instance.story_scene_id,
        "location_id": base_location.location_id,
        "location_snapshot": {"location_id": base_location.location_id,
                              "name": base_location.name,
                              "description": base_location.description,
                              "identity_description": base_location.identity_description,
                              "style_notes": base_location.style_notes},
        "location_view_id": base_view.view_id,
        "location_view_snapshot": {"view_id": base_view.view_id, "label": base_view.label,
                                   "camera_zone": base_view.camera_zone,
                                   "look_direction": base_view.look_direction,
                                   "visible_anchor_ids": list(base_view.visible_anchor_ids)},
        "base_location_reference": dict(ref_selection.get("base_location_reference") or {}),
        "base_location_basis_fingerprint": base_fp,
        "view_relevant_scene_state": projection,
        "visible_anchor_snapshots": visible_anchor_snapshots(anchors, base_view),
        "prop_semantic_snapshots": prop_semantics,
        "selected_prop_references": list(ref_selection.get("prop_refs_selected", [])),
        "peer_state_reference": ref_selection.get("peer_state_reference"),
        "candidate_count": candidate_count,
        "seed": seed,
        "model": {"provider": "agnes", "model_id": model_id,
                  "size_tier": size_tier, "ratio": ratio},
        "prompt": prompt,
    }
    basis["basis_fingerprint"] = fingerprint(basis)
    return basis


def recompute_scene_instance_visual_basis_still_holds(
        project_dir, doc: SceneSystemDocument, rdoc: Optional[ResourceDocument],
        scene_instance: SceneInstance, base_location: Location, base_view: LocationView,
        instance_view: SceneInstanceView, basis: dict, *,
        include_peer: bool = True) -> Tuple[bool, str]:
    """§14：生成结束（阶段3）对当前 canonical 同参数重算 basis 并比对。
    可见状态变化 → 投影变 → stale；不可见变化不进投影 → 不 stale（§33）。
    instance_view 删除 → (False, "instance_view_missing")；投影/快照/参考变化 → basis_changed。"""
    current_view = doc.scene_instance_views.get(instance_view.scene_instance_view_id) \
        if instance_view is not None else None
    if current_view is None or scene_instance is None or base_location is None \
            or base_view is None:
        return False, "instance_view_missing"
    try:
        fresh_selection = select_state_view_visual_refs(
            project_dir, doc, rdoc, base_view, current_view, scene_instance,
            include_peer=include_peer)
    except _BasisInputError:
        return False, "basis_changed"
    current = compute_scene_instance_visual_basis(
        project_dir, doc, rdoc, scene_instance, base_location, base_view, current_view,
        model_id=basis["model"]["model_id"],
        size_tier=basis["model"]["size_tier"],
        ratio=basis["model"]["ratio"],
        seed=basis["seed"],
        candidate_count=basis["candidate_count"],
        ref_selection=fresh_selection,
        include_peer=include_peer)
    if current["basis_fingerprint"] != basis["basis_fingerprint"]:
        return False, "basis_changed"
    return True, ""
