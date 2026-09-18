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
        "notes": [],   # peer_skipped_stale:{id} / peer_skipped_cycle:{id} / …（诚实观测）
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
    #    确定性：scene_instance_view_id 升序第一个可用项）。
    #    S2B.1-①：stale peer（approved 但当前语义已漂移）不得进入 generation——
    #    它会向新状态注入过期剧情状态的视觉信号；
    #    S2B.1-②：peer 依赖链回到自身（成环）的 peer 不得进入。
    if include_peer:
        peers = sorted((v for v in doc.scene_instance_views.values()
                        if v.scene_instance_id == instance_view.scene_instance_id
                        and v.scene_instance_view_id != instance_view.scene_instance_view_id
                        and v.approval_status == ApprovalState.APPROVED.value
                        and v.reference_asset_id),
                       key=lambda v: v.scene_instance_view_id)
        for p in peers:
            if not _peer_state_current(project_dir, doc, rdoc, scene_instance, p):
                out["notes"].append(f"peer_skipped_stale:{p.scene_instance_view_id}")
                continue
            if _peer_chain_reaches(project_dir, doc, p.scene_instance_view_id,
                                   instance_view.scene_instance_view_id):
                out["notes"].append(f"peer_skipped_cycle:{p.scene_instance_view_id}")
                continue
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
                                        include_peer: bool = True,
                                        visual_profile=None) -> dict:
    """SceneInstanceView GenerationBasis（§13）。纯函数；同状态+同参数 → 同 basis。
    visual_profile（P1 §12）：项目级介质/艺术方向；非 None 时 style 指令进 prompt
    后缀，snapshot + visual_profile_fingerprint 入 basis（与 Location 身份、
    SceneState 时间/天气/灯光三层不混淆）。
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

    domain_prompt = build_scene_instance_view_prompt(
        base_location, base_view, anchors, projection, prop_semantics,
        has_peer_reference=ref_selection.get("peer_state_reference") is not None,
        peer_note=(ref_selection.get("peer_state_reference") or {}).get(
            "scene_instance_view_id", ""))
    from src.visual_profile.compiler import append_style_instruction
    from src.visual_profile.models import (profile_snapshot,
                                           visual_profile_fingerprint)
    prompt = append_style_instruction(domain_prompt, visual_profile)

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
        "selection_notes": list(ref_selection.get("notes") or []),
        "include_peer": include_peer,   # 重算/溯源必须沿用原生成的 peer 策略（§14：
        # 其它 view 变化不应 stale——peer 是可选增强，后来出现的 peer 不追溯作废）
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


def basis_reference_entries(project_dir, rdoc: Optional[ResourceDocument],
                            basis: dict) -> List[dict]:
    """从 generation basis 重建生成时的参考 entries（review 对比图同源用）。
    顺序确定性：base → peer → props。不可读项跳过（诚实）。"""
    pdir = Path(project_dir)
    out: List[dict] = []

    def _entry(asset_id: Optional[str], role: str, extra: Optional[dict] = None) -> None:
        if not asset_id or rdoc is None:
            return
        a = rdoc.reference_assets.get(asset_id)
        if a is None or to_agnes_image_ref(pdir, url=a.url, local=a.local) is None:
            return
        data = load_image_bytes(pdir, a.local)
        e = {"role": role, "asset_id": a.asset_id, "url": a.url, "local": a.local,
             "sha256": sha256_bytes(data) if data is not None else None}
        if extra:
            e.update(extra)
        out.append(e)

    base_ref = (basis.get("base_location_reference") or {}).get("asset_id")
    _entry(base_ref, "base_location_view")
    peer_ref = (basis.get("peer_state_reference") or {}).get("asset_id")
    _entry(peer_ref, "peer_state_view",
           {"scene_instance_view_id": (basis.get("peer_state_reference") or {}).get(
               "scene_instance_view_id")})
    for pr in basis.get("selected_prop_references") or []:
        _entry(pr.get("asset_id"), "prop_reference", {"prop_id": pr.get("prop_id")})
    return out


# ============ S2B.1：peer 资格（stale 排除 + 成环禁止）+ no-peer basis ============
def compute_no_peer_basis(project_dir, doc: SceneSystemDocument,
                          rdoc: Optional[ResourceDocument],
                          scene_instance: SceneInstance,
                          base_location: Location, base_view: LocationView,
                          instance_view: SceneInstanceView, *,
                          model_id: str, size_tier: str, ratio: str,
                          seed: Optional[int], candidate_count: int,
                          visual_profile=None) -> dict:
    """S2B.1-③：no-peer basis（peer 是可选生成增强，不属于批准语义——§14 的
    must-stale 清单不含 peer）。approve 存其指纹为 approved_basis_fingerprint；
    read 层 staleness 用同口径重算对比。抛 _BasisInputError（caller 转 receipt）。
    visual_profile（P1 §12）随口径透传。"""
    selection = select_state_view_visual_refs(
        project_dir, doc, rdoc, base_view, instance_view, scene_instance,
        include_peer=False)
    return compute_scene_instance_visual_basis(
        project_dir, doc, rdoc, scene_instance, base_location, base_view, instance_view,
        model_id=model_id, size_tier=size_tier, ratio=ratio, seed=seed,
        candidate_count=candidate_count, ref_selection=selection, include_peer=False,
        visual_profile=visual_profile)


def _latest_committed_gen_basis(project_dir, sciv: SceneInstanceView) -> Optional[dict]:
    if not sciv.active_generation_id:
        return None
    gens = [r for r in generation_records.read_records(
        project_dir, generation_id=sciv.active_generation_id)
        if r.get("operation") == "scene_instance_view.generate"
        and r.get("status") == "committed"]
    return (gens[-1].get("basis") or {}) if gens else None


def _peer_state_current(project_dir, doc: SceneSystemDocument,
                        rdoc: Optional[ResourceDocument],
                        scene_instance: SceneInstance,
                        peer: SceneInstanceView) -> bool:
    """S2B.1-①：peer 是否"approved 且当前"——有 committed generation、有
    approved_basis_fingerprint、且 no-peer 重算指纹一致（stale peer 不得进入
    generation：它会向新状态注入过期剧情状态的视觉信号）。"""
    if peer.approval_status != ApprovalState.APPROVED.value or not peer.reference_asset_id:
        return False
    if not peer.approved_basis_fingerprint:
        return False
    gen_basis = _latest_committed_gen_basis(project_dir, peer)
    if not gen_basis:
        return False
    base_view = doc.views.get(peer.location_view_id)
    loc = doc.locations.get(scene_instance.location_id)
    if base_view is None or loc is None:
        return False
    from src.visual_profile import load_profile_or_none
    try:
        visual_profile = load_profile_or_none(project_dir)
    except Exception:   # noqa: BLE001 - profile 不可读 → 无法证明 peer 当前 → 视为不当前
        return False
    try:
        current = compute_no_peer_basis(
            project_dir, doc, rdoc, scene_instance, loc, base_view, peer,
            model_id=(gen_basis.get("model") or {}).get("model_id", ""),
            size_tier=(gen_basis.get("model") or {}).get("size_tier", "1K"),
            ratio=(gen_basis.get("model") or {}).get("ratio", "16:9"),
            seed=gen_basis.get("seed"),
            candidate_count=gen_basis.get("candidate_count", 2),
            visual_profile=visual_profile)
    except _BasisInputError:
        return False
    return current["basis_fingerprint"] == peer.approved_basis_fingerprint


def _peer_chain_reaches(project_dir, doc: SceneSystemDocument,
                        start_sciv_id: str, target_id: str, *,
                        max_depth: int = 8) -> bool:
    """S2B.1-②：从候选 peer 沿"其 generation 实际使用的 peer"依赖链走——
    回到 target（或任何环）→ True（该 peer 不可用）。committed generation record
    是唯一依赖真相源。防御性深度上限。"""
    visited = {start_sciv_id}
    cur = start_sciv_id
    for _ in range(max_depth):
        sciv = doc.scene_instance_views.get(cur)
        if sciv is None:
            return False
        gen_basis = _latest_committed_gen_basis(project_dir, sciv)
        if not gen_basis:
            return False
        nxt = (gen_basis.get("peer_state_reference") or {}).get("scene_instance_view_id")
        if not nxt:
            return False
        if nxt == target_id or nxt in visited:
            return True
        visited.add(nxt)
        cur = nxt
    return True   # 深度超限——防御性视为不可用


def compute_state_view_staleness(project_dir, doc: SceneSystemDocument,
                                 rdoc: Optional[ResourceDocument],
                                 scene_instance: SceneInstance,
                                 base_location: Location, base_view: LocationView,
                                 instance_view: SceneInstanceView,
                                 gen_basis: dict) -> dict:
    """S2B §32/§33 + S2B.1-③：approved state view 的 derived stale 检测（read 模型）。
    🔴 no-peer 口径：peer 是可选生成增强，不属于批准语义——**later peer 不追溯
    stale earlier view**（§14"其它 view 变化不应 stale"），peer_reference_changed
    不再是 stale 理由。用 no-peer 重算对比 approved_basis_fingerprint，逐组件归因
    （绝不自动 mutation——stale 是 derived fact，未来 Shot binding 必须拒绝 stale 引用）。
    P1 §13/§14 项目风格归因：profile-aware generation（basis 有
    visual_profile_fingerprint）与当前 profile 不一致 → project_visual_profile_changed；
    legacy basis（无该键）在项目已有 profile 时 → generated_before_visual_profile
    （诚实区分，不伪造"符合当前 Style"）；项目从未设置 profile → 不因此 stale。
    返回 {is_stale, stale_reasons, current_basis_fingerprint}。"""
    reasons: List[str] = []
    # ---- P1 项目风格归因（先于全量重算；profile 不可读 → 诚实标记无法验证）----
    from src.visual_profile import load_profile_or_none
    from src.visual_profile.models import visual_profile_fingerprint
    try:
        cur_profile = load_profile_or_none(project_dir)
        cur_profile_fp = visual_profile_fingerprint(cur_profile) if cur_profile else None
    except Exception:   # noqa: BLE001
        reasons.append("visual_profile_unreadable")
        cur_profile_fp = "__unreadable__"
    stored_profile_fp = gen_basis.get("visual_profile_fingerprint")
    if stored_profile_fp:
        if cur_profile_fp != stored_profile_fp and cur_profile_fp != "__unreadable__":
            reasons.append("project_visual_profile_changed")
    elif cur_profile_fp is not None and cur_profile_fp != "__unreadable__":
        reasons.append("generated_before_visual_profile")
    try:
        current = compute_no_peer_basis(
            project_dir, doc, rdoc, scene_instance, base_location, base_view,
            instance_view,
            model_id=(gen_basis.get("model") or {}).get("model_id", ""),
            size_tier=(gen_basis.get("model") or {}).get("size_tier", "1K"),
            ratio=(gen_basis.get("model") or {}).get("ratio", "16:9"),
            seed=gen_basis.get("seed"),
            candidate_count=gen_basis.get("candidate_count", 2),
            visual_profile=(cur_profile
                            if cur_profile_fp != "__unreadable__" else None))
    except _BasisInputError as e:
        return {"is_stale": True,
                "stale_reasons": reasons + [f"selection_failed:{e.code}"],
                "current_basis_fingerprint": None}
    if current["basis_fingerprint"] != instance_view.approved_basis_fingerprint:
        # 逐组件归因（顺序固定，诚实可读；peer 组件不参与——S2B.1-③）
        if current.get("location_snapshot") != gen_basis.get("location_snapshot"):
            reasons.append("location_identity_changed")
        if current.get("location_view_snapshot") != gen_basis.get("location_view_snapshot"):
            reasons.append("base_location_view_changed")
        if current.get("base_location_reference") != gen_basis.get("base_location_reference"):
            reasons.append("base_reference_changed")
        cur_state = current.get("view_relevant_scene_state") or {}
        old_state = gen_basis.get("view_relevant_scene_state") or {}
        for k in ("time_of_day", "weather", "lighting", "environment_conditions"):
            if cur_state.get(k) != old_state.get(k):
                reasons.append(f"global_state_changed:{k}")
                break
        if cur_state.get("anchor_states") != old_state.get("anchor_states"):
            reasons.append("anchor_state_changed")
        if cur_state.get("prop_placements") != old_state.get("prop_placements"):
            reasons.append("prop_placement_changed")
        if current.get("selected_prop_references") != gen_basis.get("selected_prop_references"):
            reasons.append("prop_reference_changed")
        if not reasons:
            reasons.append("basis_changed")
    return {"is_stale": bool(reasons), "stale_reasons": reasons,
            "current_basis_fingerprint": current["basis_fingerprint"]}


def recompute_scene_instance_visual_basis_still_holds(
        project_dir, doc: SceneSystemDocument, rdoc: Optional[ResourceDocument],
        scene_instance: SceneInstance, base_location: Location, base_view: LocationView,
        instance_view: SceneInstanceView, basis: dict, *,
        include_peer: bool = True) -> Tuple[bool, str]:
    """§14：生成结束（阶段3）对当前 canonical 同参数重算 basis 并比对。
    可见状态变化 → 投影变 → stale；不可见变化不进投影 → 不 stale（§33）。
    instance_view 删除 → (False, "instance_view_missing")；profile 变更 → (False,
    "project_visual_profile_changed")（P1 §13）；投影/快照/参考变化 → basis_changed。"""
    current_view = doc.scene_instance_views.get(instance_view.scene_instance_view_id) \
        if instance_view is not None else None
    if current_view is None or scene_instance is None or base_location is None \
            or base_view is None:
        return False, "instance_view_missing"
    from .visual_basis import _profile_stale_reason
    profile_reason = _profile_stale_reason(project_dir, basis)
    if profile_reason:
        return False, profile_reason
    from src.visual_profile import load_profile_or_none
    try:
        visual_profile = load_profile_or_none(project_dir)
    except Exception:   # noqa: BLE001 - 与 staleness 同口径：不可读 → 无法证明仍成立
        return False, "visual_profile_unreadable"
    try:
        fresh_selection = select_state_view_visual_refs(
            project_dir, doc, rdoc, base_view, current_view, scene_instance,
            include_peer=basis.get("include_peer", include_peer))
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
        include_peer=basis.get("include_peer", include_peer),
        visual_profile=visual_profile)
    if current["basis_fingerprint"] != basis["basis_fingerprint"]:
        return False, "basis_changed"
    return True, ""
