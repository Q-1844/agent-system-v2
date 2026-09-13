# src/shot_binding/validity.py
# Shot Production Binding V1 — Phase S3B.2：Dependency Resolution + Freshness +
# Derived Validity read model + Explicit Refresh 的共享纯逻辑。
#
# 四条纪律（7.md S3B.2 §末）：
#   1. Binding 是 Intent，不是状态缓存——本模块绝不向 binding 写入 status/valid 字段。
#   2. Validity 是 Derived Projection——每次 read 从当前 canonical 实时派生，不落盘。
#   3. Read 永不 refresh——compare(snapshot, current) 只产生 NOT_READY，绝不回写 snapshot。
#   4. 只有 Explicit Refresh（service.refresh_binding）接受新 fingerprint basis。
# 第五条：只追踪 binding 实际引用的依赖（per-entity 指纹），无关资源变化不可能进入
#   检查集——无 whole-document revision、无 project-global modified_at（§7）。
#
# 指纹全部复用各域既有 helper（§9 零新发明）：
#   character_identity_fingerprint（resources/generation，name+description_en；views 不算身份）
#   look_revision / prop_revision / composition_revision（resources/models）
#   scene_instance_revision（scene_system/models——⚠ provenance only，见 §18 deviation）
#   approved_basis_fingerprint + is_stale verdict（scene_system 自有 read model，经
#   SceneVisualService.read_scene_instance_view_status 消费——不重新实现投影算法）
#   visual_profile_fingerprint（visual_profile/models，语义子集不含时间戳）
#
# 🔴 §18 粒度 deviation（对 S3A §19 表 scene_instance 行的修正，报告 §13 记录）：
#   S3B.1 snapshot 的 scene_instance = 整实例 revision（含整个 SceneState），粒度粗于
#   view-relevant 投影——若参与比对，"SIV 不可见的状态变化"会 false stale（违反 §K66）。
#   裁决：该字段保留为 bind/refresh 时刻 provenance（schema v1 不动），**不参与 freshness
#   比对**；场景 freshness = 结构校验 + resolved SIV 的 Scene System verdict + SIV 指纹比对。
#
# 🔴 确定性（§33）：check 顺序固定；实体按 binding 数组序遍历；read model 无时间戳。
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    DependencyFingerprints,
    SceneBinding,
    ShotBindingDocument,
    ShotProductionBinding,
    binding_revision,
    document_revision,
)
from .store import ShotBindingStore

READY = "READY"
NOT_READY = "NOT_READY"
UNBOUND = "UNBOUND"

# Scene System identity 规则（S2B §4）：一个 (scene_instance_id, location_view_id) 对
# 至多一个 SceneInstanceView 实体。unpinned 解析即按该对唯一化——多个实体 = 数据异常，
# fail closed（§15 Case B：不猜"取第一个/最新"）。


def _s(value: Any) -> str:
    return str(value or "").strip()


def _issue(dependency_type: str, dependency_id: str, reason: str,
           **detail: Any) -> dict:
    """结构化 issue（§5）：机器可读，不只一句字符串。reason 词表 = S3B.1 create 错误码
    ∪ {fingerprint_mismatch, stale_dependency, project_visual_profile_unspecified,
    scene_instance_view_ambiguous, fingerprint_unavailable, orphan_binding,
    binding_conflict, project_unreadable, resources_unreadable, scene_system_unreadable,
    visual_profile_unreadable}（存在等价码即复用，不造第二套）。"""
    return {"dependency_type": dependency_type,
            "dependency_id": dependency_id,
            "reason": reason,
            "detail": dict(detail)}


def has_usable_approved(rdoc: Any, approved_ids: List[str]) -> bool:
    """至少一个 approved id 真实指向存在的 ReferenceAsset（指向已删除 asset 不算可用）。
    （S3B.1 service 同名私有逻辑的共享化；Resources 无更细的公开 stale/revoked verdict
    ——P1 §22.3：Resources 无 approved-basis 锚点，不越权发明，见报告 §14。）"""
    return any(aid in rdoc.reference_assets for aid in (approved_ids or []))


# ============ SceneInstanceView 解析（§15/§16）============

def resolve_scene_instance_view(sdoc: Any, scene_binding: SceneBinding) -> Tuple[Optional[Any], Optional[dict]]:
    """unpinned 解析（pinned 由调用方直接按 id 取实体并做既有校验）。
    按 (scene_instance_id, location_view_id) 对取唯一实体（Scene System 身份规则）：
    - 无实体 → scene_instance_view_missing
    - 实体存在但未 approved → scene_instance_view_not_approved
    - 多个实体（数据异常）→ scene_instance_view_ambiguous（fail closed，不猜）
    返回 (sciv, issue)；成功时 issue=None。绝不把解析结果写回 binding（§16）。"""
    pair = [v for v in sdoc.scene_instance_views.values()
            if v.scene_instance_id == scene_binding.scene_instance_id
            and v.location_view_id == scene_binding.location_view_id]
    if not pair:
        return None, _issue(
            "scene_instance_view", f"{scene_binding.scene_instance_id}/{scene_binding.location_view_id}",
            "scene_instance_view_missing",
            hint="该方向无 SceneInstanceView（先在 Scene System 生成并批准）")
    if len(pair) > 1:
        return None, _issue(
            "scene_instance_view", f"{scene_binding.scene_instance_id}/{scene_binding.location_view_id}",
            "scene_instance_view_ambiguous",
            hint=f"同一 (instance, view) 对出现 {len(pair)} 个实体（违反 Scene System 身份规则，数据异常）")
    sciv = pair[0]
    if sciv.approval_status != "approved":
        return None, _issue(
            "scene_instance_view", sciv.scene_instance_view_id,
            "scene_instance_view_not_approved",
            hint=f"SceneInstanceView 当前状态 {sciv.approval_status}")
    return sciv, None


def siv_freshness_issue(project_dir, project_name: str, sciv: Any,
                        snapshot_fingerprint: Optional[str], *,
                        compare_snapshot: bool,
                        include_staleness: bool = True) -> Optional[dict]:
    """§17 SIV freshness：完全消费 Scene System 自有 verdict，不重实现投影。
    - approved 但无 approved_basis_fingerprint → fingerprint_unavailable（§32 fail closed）
    - snapshot 已记录 basis（pinned 恒比；unpinned 在 refresh 记录后比）且 ≠ 当前 →
      fingerprint_mismatch（SIV 被重新生成并重新批准）
    - Scene System read model 判 is_stale → stale_dependency（stale_reasons 原样透传）
    compare_snapshot=False（unpinned 且 snapshot 为 None，S3B.1 legacy 语义）时跳过比对。
    include_staleness=False（refresh 路径专用）：§22 的 refresh 拒绝清单是硬损坏
    （missing/revoked/unapproved/inactive/pinned-missing/unspecified/cast/conflict），
    **不含 SIV stale**——"stale but currently valid" 正是 refresh 存在的意义；
    refresh 后 validity 诚实重报 stale，不会假 READY（Scene 侧修复 + 再次 refresh 才 READY）。"""
    if sciv.approval_status != "approved":
        return _issue("scene_instance_view", sciv.scene_instance_view_id,
                      "scene_instance_view_not_approved",
                      hint=f"SceneInstanceView 当前状态 {sciv.approval_status}")
    current_fp = sciv.approved_basis_fingerprint
    if not current_fp:
        return _issue("scene_instance_view", sciv.scene_instance_view_id,
                      "fingerprint_unavailable",
                      hint="approved SIV 缺 approved_basis_fingerprint（审批状态无法建立）")
    if compare_snapshot and snapshot_fingerprint != current_fp:
        return _issue("scene_instance_view", sciv.scene_instance_view_id,
                      "fingerprint_mismatch",
                      expected_fingerprint=snapshot_fingerprint,
                      current_fingerprint=current_fp,
                      hint="SIV 自快照以来被重新生成/重新批准")
    if not include_staleness:
        return None
    from src.scene_system.visual_service import SceneVisualService
    status = SceneVisualService(Path(project_dir), project_name) \
        .read_scene_instance_view_status(sciv.scene_instance_view_id)
    if status is None:
        return _issue("scene_instance_view", sciv.scene_instance_view_id,
                      "scene_instance_view_missing",
                      hint="Scene System read model 无法返回该 SIV 状态")
    if status.get("is_stale"):
        return _issue("scene_instance_view", sciv.scene_instance_view_id,
                      "stale_dependency",
                      stale_reasons=list(status.get("stale_reasons") or []),
                      hint="Scene System verdict：approved basis 与当前语义状态不一致")
    return None


# ============ 指纹计算（create/replace/refresh 共享；S3B.1 行为保持）============

def compute_fingerprints(project_dir, project, rdoc, char_bindings, prop_bindings,
                         scene_binding, scene_instance_rev,
                         sciv_fingerprint) -> DependencyFingerprints:
    """当前依赖指纹快照（bind 时刻 / refresh 时刻一次性计算）。
    与 S3B.1 service._compute_fingerprints 逐字节同行为（create/replace 路径回归保护）；
    S3B.2 refresh 传入已解析的 sciv_fingerprint（unpinned 也会记录，§24）。"""
    from src.resources.generation import character_identity_fingerprint
    from src.resources.models import (composition_revision, look_revision,
                                      prop_revision)
    from src.visual_profile import load_profile_or_none
    from src.visual_profile.models import visual_profile_fingerprint

    chars: Dict[str, str] = {}
    for cb in char_bindings:
        c = project.get_character(cb.character_name)
        fp = character_identity_fingerprint(c)
        if fp:
            chars[cb.character_name] = fp
    looks = {cb.look_id: look_revision(rdoc.looks[cb.look_id])
             for cb in char_bindings if cb.look_id in rdoc.looks}
    props = {pb.prop_id: prop_revision(rdoc.props[pb.prop_id])
             for pb in prop_bindings if pb.prop_id in rdoc.props}
    comps = {cb.composition_id: composition_revision(rdoc.compositions[cb.composition_id])
             for cb in char_bindings
             if cb.composition_id and cb.composition_id in rdoc.compositions}

    profile_fp = None
    try:
        profile = load_profile_or_none(project_dir)
    except Exception as e:   # noqa: BLE001 —— 坏 profile 文件诚实拒绝（不静默当 unspecified）
        from .service import _ValidationError
        raise _ValidationError("visual_profile_unreadable",
                               f"project_profile.json 不可读: {e}") from e
    if profile is not None:
        profile_fp = visual_profile_fingerprint(profile)

    return DependencyFingerprints(
        visual_profile=profile_fp,
        characters=chars,
        looks=looks,
        props=props,
        compositions=comps,
        scene_instance=scene_instance_rev,
        scene_instance_view=sciv_fingerprint,
    )


def fingerprint_compare_checks(binding: ShotProductionBinding,
                               current: DependencyFingerprints) -> List[dict]:
    """§8 snapshot vs current 逐依赖比对（仅结构检查已通过的实体；缺失实体由结构
    检查负责）。🔴 scene_instance 不比对（§18 deviation，provenance only）。
    snapshot 缺项（None）≠ current 值 → fingerprint_mismatch（诚实 fail closed；
    例外：unpinned 的 scene_instance_view 快照 None = S3B.1"未记录 basis"语义，
    由调用方以 compare_snapshot=False 表达，不进入本函数）。"""
    checks: List[dict] = []
    snap = binding.dependency_fingerprints
    for name, fp in snap.characters.items():
        cur = current.characters.get(name)
        (checks.append(_issue("character", name, "ok")) if snap.characters.get(name) == cur
         else checks.append(_issue("character", name, "fingerprint_mismatch",
                                   expected_fingerprint=snap.characters.get(name),
                                   current_fingerprint=cur)))
    for lid, fp in snap.looks.items():
        cur = current.looks.get(lid)
        (checks.append(_issue("character_look", lid, "ok")) if fp == cur
         else checks.append(_issue("character_look", lid, "fingerprint_mismatch",
                                   expected_fingerprint=fp, current_fingerprint=cur)))
    for pid, fp in snap.props.items():
        cur = current.props.get(pid)
        (checks.append(_issue("prop", pid, "ok")) if fp == cur
         else checks.append(_issue("prop", pid, "fingerprint_mismatch",
                                   expected_fingerprint=fp, current_fingerprint=cur)))
    for cid, fp in snap.compositions.items():
        cur = current.compositions.get(cid)
        (checks.append(_issue("composition", cid, "ok")) if fp == cur
         else checks.append(_issue("composition", cid, "fingerprint_mismatch",
                                   expected_fingerprint=fp, current_fingerprint=cur)))
    if snap.visual_profile != current.visual_profile:
        checks.append(_issue("project_visual_profile", "project_profile",
                             "fingerprint_mismatch",
                             expected_fingerprint=snap.visual_profile,
                             current_fingerprint=current.visual_profile))
    else:
        checks.append(_issue("project_visual_profile", "project_profile", "ok"))
    # scene_instance（provenance）与 scene_instance_view（由 siv_freshness_issue 专门
    # 处理 pinned/unpinned 语义）不在此比对。
    return checks


# ============ Derived Validity read model（§4/§6，纯读零副作用）============

def compute_binding_validity(project_dir, project_name: Optional[str], shot_id: str) -> dict:
    """回答唯一问题（§1）：这个 binding 现在是否仍可信、是否具备进入生成的条件。
    read → derive → return；绝不 fix/refresh/写盘（§3）。未知 schema / 坏 JSON 按
    store 规则显式异常（§32：损坏 canonical 不伪装成业务 NOT_READY）。"""
    project_dir = Path(project_dir)
    store = ShotBindingStore(project_dir, project_name)
    doc: ShotBindingDocument = store.load()
    sid = _s(shot_id)
    b = doc.bindings.get(sid)

    if b is None:
        # UNBOUND（§4/§26）：不报错、不创建文件、不猜 binding。
        # legacy = read-model metadata（unbound 且 Shot 已有生成产物；S3A §7.3）。
        legacy = False
        try:
            from src.core.store import ProjectStore
            shot = ProjectStore(project_dir).load().get_shot(sid)
            legacy = bool(shot) and bool(shot.first_frame_batches
                                         or shot.last_frame_batches
                                         or shot.video_versions)
        except Exception:   # noqa: BLE001 —— metadata 尽力而为，不影响 UNBOUND 语义
            legacy = False
        return {"shot_id": sid, "binding_state": "unbound", "legacy": legacy,
                "readiness": UNBOUND, "binding_revision": None,
                "document_revision": document_revision(doc),
                "resolved_dependencies": {}, "dependency_checks": [], "issues": []}

    checks: List[dict] = []
    resolved: Dict[str, Any] = {"scene_instance_view_id": None}

    def finalize() -> dict:
        issues = [c for c in checks if c["reason"] != "ok"]
        return {"shot_id": sid, "binding_state": "bound", "legacy": False,
                "readiness": READY if not issues else NOT_READY,
                "binding_revision": binding_revision(b),
                "document_revision": document_revision(doc),
                "resolved_dependencies": resolved,
                "dependency_checks": checks,
                "issues": issues}

    # ---- canonical 加载（任一不可读 → fail closed，§32）----
    from src.core.store import ProjectStore
    from src.resources.store import ResourceStore
    from src.scene_system.store import SceneSystemStore
    try:
        project = ProjectStore(project_dir).load()
    except Exception as e:   # noqa: BLE001
        checks.append(_issue("project", sid, "project_unreadable", error=str(e)))
        return finalize()
    try:
        rdoc = ResourceStore(project_dir, project_name or project_dir.name).load()
    except Exception as e:   # noqa: BLE001
        checks.append(_issue("resources", sid, "resources_unreadable", error=str(e)))
        return finalize()
    try:
        sdoc = SceneSystemStore(project_dir, project_name or project_dir.name).load()
    except Exception as e:   # noqa: BLE001
        checks.append(_issue("scene_system", sid, "scene_system_unreadable", error=str(e)))
        return finalize()

    profile = None
    profile_unreadable = False
    from src.visual_profile import load_profile_or_none
    try:
        profile = load_profile_or_none(project_dir)
    except Exception:   # noqa: BLE001 —— 记 issue，继续其余检查（SIV verdict 内部同样诚实标记）
        profile_unreadable = True

    # ---- §6 Binding 自身与当前 Shot 重新校验（不假设"曾经合法现在也合法"）----
    shot = project.get_shot(sid)
    if shot is None:
        # §25 orphan：不 delete / 不 GC / 不自动归档 / 不修改 binding。
        checks.append(_issue("shot", sid, "orphan_binding",
                             hint="binding 存在但对应 Shot 已被删除（保留，不自动清理）"))
        return finalize()
    if b.scene_id != shot.scene_id:
        checks.append(_issue("binding", sid, "scene_mismatch",
                             binding_scene_id=b.scene_id, shot_scene_id=shot.scene_id))
    else:
        checks.append(_issue("binding", sid, "ok"))
    binding_names = {cb.character_name for cb in b.character_bindings}
    shot_names = {str(c or "").strip() for c in (shot.characters or [])
                  if str(c or "").strip()}
    if binding_names != shot_names:
        checks.append(_issue("cast", sid, "cast_mismatch",
                             binding_cast=sorted(binding_names),
                             shot_cast=sorted(shot_names)))
    else:
        checks.append(_issue("cast", sid, "ok"))

    # ---- Character / CharacterLook（§10/§11：exists ≠ approved ≠ fresh）----
    for cb in b.character_bindings:
        ch = project.get_character(cb.character_name)
        if ch is None:
            checks.append(_issue("character", cb.character_name, "character_missing"))
        else:
            from src.resources.generation import character_identity_fingerprint
            snap_c = b.dependency_fingerprints.characters.get(cb.character_name)
            cur_c = character_identity_fingerprint(ch)
            if snap_c == cur_c:
                checks.append(_issue("character", cb.character_name, "ok"))
            else:
                checks.append(_issue("character", cb.character_name,
                                     "fingerprint_mismatch",
                                     expected_fingerprint=snap_c,
                                     current_fingerprint=cur_c))
        look = rdoc.looks.get(cb.look_id)
        if look is None:
            checks.append(_issue("character_look", cb.look_id, "look_missing"))
            continue
        if look.character_name != cb.character_name:
            checks.append(_issue("character_look", cb.look_id,
                                 "look_character_mismatch",
                                 look_character=look.character_name,
                                 binding_character=cb.character_name))
            continue
        if not has_usable_approved(rdoc, look.approved_reference_asset_ids):
            checks.append(_issue("character_look", cb.look_id, "look_not_approved",
                                 hint="无可用 approved reference（含被 revoke 情形）"))
            continue
        from src.resources.models import look_revision
        snap_l = b.dependency_fingerprints.looks.get(cb.look_id)
        cur_l = look_revision(look)
        if snap_l == cur_l:
            checks.append(_issue("character_look", cb.look_id, "ok"))
        else:
            checks.append(_issue("character_look", cb.look_id, "fingerprint_mismatch",
                                 expected_fingerprint=snap_l, current_fingerprint=cur_l))

    # ---- Composition（§12：只消费 Resources 当前公开事实，不发明 nested revoke policy）----
    for cb in b.character_bindings:
        if not cb.composition_id:
            continue
        comp = rdoc.compositions.get(cb.composition_id)
        if comp is None:
            checks.append(_issue("composition", cb.composition_id, "composition_missing"))
            continue
        if comp.character_name != cb.character_name:
            checks.append(_issue("composition", cb.composition_id,
                                 "composition_character_mismatch"))
            continue
        if comp.look_id != cb.look_id:
            checks.append(_issue("composition", cb.composition_id,
                                 "composition_look_mismatch",
                                 composition_look_id=comp.look_id,
                                 binding_look_id=cb.look_id))
            continue
        if comp.approval_status != "approved":
            checks.append(_issue("composition", cb.composition_id,
                                 "composition_not_approved"))
            continue
        from src.resources.models import composition_revision
        snap_c = b.dependency_fingerprints.compositions.get(cb.composition_id)
        cur_c = composition_revision(comp)
        if snap_c == cur_c:
            checks.append(_issue("composition", cb.composition_id, "ok"))
        else:
            checks.append(_issue("composition", cb.composition_id,
                                 "fingerprint_mismatch",
                                 expected_fingerprint=snap_c, current_fingerprint=cur_c))

    # ---- Direct Prop（§13）----
    for pb in b.prop_bindings:
        prop = rdoc.props.get(pb.prop_id)
        if prop is None:
            checks.append(_issue("prop", pb.prop_id, "prop_missing"))
            continue
        if not has_usable_approved(rdoc, prop.approved_reference_asset_ids):
            checks.append(_issue("prop", pb.prop_id, "prop_not_approved",
                                 hint="无可用 approved reference（含被 revoke 情形）"))
            continue
        from src.resources.models import prop_revision
        snap_p = b.dependency_fingerprints.props.get(pb.prop_id)
        cur_p = prop_revision(prop)
        if snap_p == cur_p:
            checks.append(_issue("prop", pb.prop_id, "ok"))
        else:
            checks.append(_issue("prop", pb.prop_id, "fingerprint_mismatch",
                                 expected_fingerprint=snap_p, current_fingerprint=cur_p))

    # ---- §7 硬规则复验（Resources canonical 后续可能变化，不假设 create 时查过）----
    covered: set = set()
    for cb in b.character_bindings:
        if not cb.composition_id:
            continue
        comp = rdoc.compositions.get(cb.composition_id)
        if comp is not None and comp.approval_status == "approved":
            covered |= {x.prop_id for x in comp.prop_bindings}
    direct = {pb.prop_id for pb in b.prop_bindings}
    conflict = sorted(direct & covered)
    if conflict:
        checks.append(_issue("prop", ",".join(conflict), "binding_conflict",
                             hint="prop 同时被 composition 覆盖与 direct prop_bindings 声明"))
    else:
        checks.append(_issue("prop_conflict", sid, "ok"))

    # ---- Scene（§14 结构 + §15/§17/§18 解析与 freshness）----
    sb = b.scene_binding
    if sb is None:
        checks.append(_issue("scene_binding", sid, "ok", hint="无场景绑定（合法）"))
    else:
        inst = sdoc.scene_instances.get(sb.scene_instance_id)
        if inst is None:
            checks.append(_issue("scene_instance", sb.scene_instance_id,
                                 "scene_instance_missing"))
        elif inst.status != "active":
            checks.append(_issue("scene_instance", sb.scene_instance_id,
                                 "scene_instance_not_active", status=inst.status))
        elif inst.story_scene_id != shot.scene_id:
            checks.append(_issue("scene_instance", sb.scene_instance_id,
                                 "story_scene_mismatch",
                                 instance_story_scene_id=inst.story_scene_id,
                                 shot_scene_id=shot.scene_id))
        else:
            checks.append(_issue("scene_instance", sb.scene_instance_id, "ok"))
        view = sdoc.views.get(sb.location_view_id)
        if view is None:
            checks.append(_issue("location_view", sb.location_view_id,
                                 "location_view_missing"))
        elif inst is not None and view.location_id != inst.location_id:
            checks.append(_issue("location_view", sb.location_view_id,
                                 "location_view_not_in_location",
                                 view_location_id=view.location_id,
                                 instance_location_id=inst.location_id))
        else:
            checks.append(_issue("location_view", sb.location_view_id, "ok"))
        # SIV 解析（pinned 精确取用；unpinned 按对唯一解析）
        sciv = None
        if inst is not None and view is not None and inst.status == "active" \
                and view.location_id == inst.location_id:
            if sb.pinned_scene_instance_view_id:
                sciv = sdoc.scene_instance_views.get(sb.pinned_scene_instance_view_id)
                if sciv is None:
                    checks.append(_issue("scene_instance_view",
                                         sb.pinned_scene_instance_view_id,
                                         "scene_instance_view_missing"))
                elif sciv.scene_instance_id != sb.scene_instance_id:
                    checks.append(_issue("scene_instance_view",
                                         sciv.scene_instance_view_id,
                                         "scene_instance_view_instance_mismatch"))
                elif sciv.location_view_id != sb.location_view_id:
                    checks.append(_issue("scene_instance_view",
                                         sciv.scene_instance_view_id,
                                         "scene_instance_view_view_mismatch"))
            else:
                sciv, issue = resolve_scene_instance_view(sdoc, sb)
                if issue is not None:
                    checks.append(issue)
                    sciv = None
            if sciv is not None:
                resolved["scene_instance_view_id"] = sciv.scene_instance_view_id
                snap_v = b.dependency_fingerprints.scene_instance_view
                # pinned 恒比对快照；unpinned 仅在快照已记录 basis 时比对
                # （None = S3B.1 legacy"未记录"，不伪造 mismatch——§24 语义）。
                compare = bool(sb.pinned_scene_instance_view_id) or snap_v is not None
                fresh_issue = siv_freshness_issue(project_dir,
                                                  project_name or project_dir.name,
                                                  sciv, snap_v, compare_snapshot=compare)
                checks.append(fresh_issue if fresh_issue is not None
                              else _issue("scene_instance_view",
                                          sciv.scene_instance_view_id, "ok"))

    # ---- ProjectVisualProfile（§19：required dependency）----
    if profile_unreadable:
        checks.append(_issue("project_visual_profile", "project_profile",
                             "visual_profile_unreadable",
                             hint="project_profile.json 不可读，无法证明风格 currency"))
    elif profile is None:
        checks.append(_issue("project_visual_profile", "project_profile",
                             "project_visual_profile_unspecified",
                             hint="项目未设置 ProjectVisualProfile（bound shot 必需）"))
    else:
        from src.visual_profile.models import visual_profile_fingerprint
        snap_p = b.dependency_fingerprints.visual_profile
        cur_p = visual_profile_fingerprint(profile)
        if snap_p == cur_p:
            checks.append(_issue("project_visual_profile", "project_profile", "ok"))
        else:
            checks.append(_issue("project_visual_profile", "project_profile",
                                 "fingerprint_mismatch",
                                 expected_fingerprint=snap_p,
                                 current_fingerprint=cur_p))

    return finalize()
