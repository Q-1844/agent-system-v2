# src/shot_binding/reference_pack.py
# Shot Production Binding V1 — Phase S3B.3：ShotReferencePack Compiler。
#
# 职责（10.md §70）：回答"这个 Shot 生成时应该参考哪些 production truths、它们在语义上
# 扮演什么角色"。不回答 provider 怎么吃（无 weight/slot/model/seed/prompt——§14/§40/§41）。
#
# 三层纪律：
#   Binding = Canonical Intent（不落状态）
#   Validity = readiness projection（S3B.2，不落盘）
#   ShotReferencePack = generation projection（本模块，**derived / ephemeral / 不持久化 /
#   无 ID / 无 store 文件**——§6/§36；compile 永不 refresh、永不写回任何 canonical）。
#
# 编译入口 = service.compile_reference_pack（§3：不得跳过 S3B.2 validity——Pack 不发明
# 第二套 READY 判断）。本模块只做确定性纯变换 + 语义投影。
#
# 🔴 Determinism（§31）：无 UUID、无时钟、无 set 迭代输出；顺序 = Shot.characters
#    （creative canonical order，§32）→ binding.prop_bindings 顺序（§33）→ §34 语义序。
# 🔴 Reference identity = entity id / reference_asset_id，绝不 raw path（§30）；
#    approved id 指向不存在的 ReferenceAsset → fail closed（§29/§38）。
# 🔴 temporal 分类只用 explicit structured information（§26）；无法确定的
#    prop subject / hand_off target / 多重有效关系一律 fail closed（§17/§20/§25/§75）。
# 🔴 不修改 PropBinding canonical schema（§16/§69）——direct prop 的 subject 是
#    pack 级派生（Rule 1 action / Rule 6 单角色确定性解析），不是 canonical 字段。
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

PACK_VERSION = 1

# ---- 词表（model-neutral；§13）----
SOURCE_TYPES = ("scene_instance_view", "character_identity", "character_look",
                "composition", "prop")
PURPOSES = ("scene_context", "character_identity", "character_appearance",
            "wardrobe", "prop_identity", "spatial_relation", "action_state")
PRIORITIES = ("primary", "supporting", "context")
TEMPORAL_SCOPES = ("start", "throughout", "end", "static")

# Pack 级 fail-closed / diagnostic reason（§27；不写回 Binding、不改 readiness）
PACK_BLOCK_REASONS = (
    "binding_unbound",                 # §5 Case A
    "prop_subject_ambiguous",          # §17 Rule 4
    "multiple_effective_prop_relations",  # §20
    "action_target_unspecified",       # §25 hand_off 无 recipient 证据
    "action_semantics_ambiguous",      # §25 hand_off 无法唯一映射
    "reference_asset_unresolvable",    # §29/§38
    "binding_conflict",                # §18 defensive 重查
    "dependency_changed_during_compile",  # §37 一致性守卫
    "project_visual_profile_unspecified",  # §8 compile race fail closed
)
PACK_DIAGNOSTIC_REASONS = (
    "action_state_without_composition_evidence",  # action prop 无组合 plate 证据
    "action_temporal_unclassified",    # custom action 不做 start/end 分类（§26）
)


class PackBlockedError(Exception):
    """Pack 级 fail-closed（§27/§28）：携带结构化 issue；绝不写回 canonical。"""

    def __init__(self, reason: str, detail: Any = None):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")

    def issue(self) -> dict:
        return {"dependency_type": "pack", "dependency_id": "",
                "reason": self.reason, "detail": self.detail or {}}


# ============ Pack 数据模型（typed；ephemeral——只有 to_dict，无 store）============

@dataclass
class PackBasis:
    """§7 provenance：本 pack 基于哪个 binding 与哪组 dependency basis 编译。
    只作 reproducibility / audit / race-detection / 未来 cache key 基础——
    不是第二份 canonical truth，绝不写回 store。"""
    binding_revision: str = ""
    binding_document_revision: str = ""
    binding_readiness: str = ""
    dependency_fingerprints: Dict[str, Any] = field(default_factory=dict)
    resolved_scene_instance_view_id: Optional[str] = None
    visual_profile_fingerprint: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProfileProjection:
    """§8：当前 ProjectVisualProfile 语义字段（model-neutral）。
    无 prompt fragment / 无 provider 字段——风格→提示词的转换属未来 Prompt Adapter。"""
    render_family: str = ""
    art_direction: str = ""
    style_tags: List[str] = field(default_factory=list)
    negative_style_constraints: List[str] = field(default_factory=list)
    visual_profile_fingerprint: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SceneProjection:
    """§9/§10：S3B.2 已解析的 scene 身份 + Scene System 自有 view-relevant 语义投影。
    不重算 visibility projection（D6）；不可见状态变化不会改变本投影（D7）。"""
    scene_instance_id: str = ""
    location_view_id: str = ""
    scene_instance_view_id: Optional[str] = None
    location_name: str = ""
    location_identity_description: str = ""
    location_style_notes: str = ""
    view_label: str = ""
    camera_zone: str = ""
    look_direction: str = ""
    view_relevant_scene_state: Dict[str, Any] = field(default_factory=dict)
    reference_asset_id: Optional[str] = None   # SIV 在 Resources 注册的 approved asset

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CharacterPackEntry:
    """§11：每个 cast 角色的最小语义投影（不复制 Character database）。"""
    character_name: str = ""
    look_id: str = ""
    composition_id: Optional[str] = None
    character_description: str = ""
    look_label: str = ""
    appearance_description: str = ""
    appearance_delta: str = ""
    covered_props: List[Dict[str, Any]] = field(default_factory=list)  # composition 内关系
    look_reference_asset_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PropPackEntry:
    """§15/§17：direct PropBinding 投影。subject_character 是 pack 级派生
    （Rule 1 action / Rule 6 单角色），**不是** canonical 字段（§16）。"""
    prop_id: str = ""
    name: str = ""
    relation: str = ""
    anchor: str = ""
    visual_state_note: str = ""
    subject_character: Optional[str] = None
    reference_asset_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ActionProjection:
    """§21：现有 Action V1 原样投影（kind/subject_character/prop_id/起止态/note）。
    不扩 Timeline；不解析自然语言。"""
    kind: str = "none"
    subject_character: str = ""
    prop_id: str = ""
    start_state: str = ""
    end_state: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReferenceItem:
    """§13：统一 semantic reference item。identity = source_id / reference_asset_id；
    无 raw path、无 provider 字段、无 slot/weight。"""
    source_type: str = ""
    source_id: str = ""
    reference_asset_id: Optional[str] = None
    subject_character: Optional[str] = None
    prop_id: Optional[str] = None
    purpose: str = ""
    priority: str = ""
    temporal_scope: str = ""
    relation: Optional[str] = None
    anchor: Optional[str] = None
    semantic_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ShotReferencePack:
    """§6：model-neutral / read-only / derived / ephemeral generation projection。
    不是 canonical、无 ID、不落盘、不写回 binding。"""
    pack_version: int = PACK_VERSION
    shot_id: str = ""
    scene_id: str = ""
    basis: PackBasis = field(default_factory=PackBasis)
    visual_profile: ProfileProjection = field(default_factory=ProfileProjection)
    scene: SceneProjection = field(default_factory=SceneProjection)
    characters: List[CharacterPackEntry] = field(default_factory=list)
    props: List[PropPackEntry] = field(default_factory=list)
    action: ActionProjection = field(default_factory=ActionProjection)
    references: List[ReferenceItem] = field(default_factory=list)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pack_version": self.pack_version,
            "shot_id": self.shot_id,
            "scene_id": self.scene_id,
            "basis": self.basis.to_dict(),
            "visual_profile": self.visual_profile.to_dict(),
            "scene": self.scene.to_dict(),
            "characters": [c.to_dict() for c in self.characters],
            "props": [p.to_dict() for p in self.props],
            "action": self.action.to_dict(),
            "references": [r.to_dict() for r in self.references],
            "diagnostics": list(self.diagnostics),
        }


# ============ 确定性 approved 参考选择（§29：只走 approved canonical，绝不 glob）============

def select_approved_refs(rdoc: Any, approved_ids: List[str],
                         cover_asset_id: Optional[str] = None
                         ) -> Tuple[Optional[str], List[str], List[str]]:
    """确定性选择：(hero, extras, unresolvable)。
    - hero = cover（若 ∈ approved 且可解析）否则 approved 存储顺序第一个可解析项；
    - extras = 其余可解析项（存储顺序）；
    - unresolvable = approved 中指向不存在 ReferenceAsset 的 id（§29 fail closed 依据，
      由调用方决定 BLOCK）。
    无文件扫描、无最新文件猜测；顺序 = canonical 存储顺序。"""
    resolvable: List[str] = []
    unresolvable: List[str] = []
    for aid in (approved_ids or []):
        (resolvable if aid in rdoc.reference_assets else unresolvable).append(aid)
    hero: Optional[str] = None
    if cover_asset_id and cover_asset_id in resolvable:
        hero = cover_asset_id
    elif resolvable:
        hero = resolvable[0]
    extras = [aid for aid in resolvable if aid != hero]
    return hero, extras, unresolvable


# ============ Action-aware temporal 语义（§22-§26）============

def classify_temporal(kind: str, comp_covers_action_prop: bool) -> str:
    """Composition plate 的 temporal_scope：
    - none/static（或 comp 不涉及 action prop）→ static
    - pick_up 且 comp 覆盖 action prop → end（§23 硬规则：持物 plate 绝不能当
      start-state primary——它会暗示第一帧已经持物）
    - put_down 且 comp 覆盖 action prop → start（§24）
    - custom → static（不分类 start/end；emit diagnostic，§26）"""
    if kind in ("pick_up", "put_down") and comp_covers_action_prop:
        return "end" if kind == "pick_up" else "start"
    return "static"


# ============ 编译器主体（确定性纯变换；输入 = service 已加载的 canonical 实体）============

def _cast_of(shot: Any) -> List[str]:
    return [str(c or "").strip() for c in (shot.characters or []) if str(c or "").strip()]


def build_pack(*, shot: Any, binding: Any, rdoc: Any, sdoc: Any,
               profile: Any, profile_fingerprint: Optional[str],
               siv: Any, base_view: Any, location: Any, siv_status: Any,
               binding_revision: str, document_revision: str,
               project_dir: Any, character_descriptions: Optional[Dict[str, str]] = None
               ) -> ShotReferencePack:
    """READY Binding + 当前 canonical 实体 → ShotReferencePack（§6/§42）。
    任何 pack 级语义歧义 → PackBlockedError（§27 fail closed，绝不猜）。"""
    diagnostics: List[Dict[str, Any]] = []
    char_desc = dict(character_descriptions or {})
    if profile is None:
        # validity gate 已保证 READY；此处 None = compile race → fail closed（§8）
        raise PackBlockedError("project_visual_profile_unspecified",
                               {"hint": "compile 期间 profile 变为 unspecified"})
    # Phase49 修复（真机 2026-10-08 实证）：scene_binding=None 的合法 READY 绑定
    # （纯 Look 参考、场景事实以文字进提示词）在此前被无条件 raise 挡死——
    # 而 build_pack 内所有 siv 使用点均已条件化。改为仅当「绑定了 scene 却解析
    # 不到 SIV」（compile race / 依赖失效）时 fail closed；无场景绑定的镜头
    # siv=None 合法通过，构建纯角色参考 pack。
    if siv is None and binding.scene_binding is not None:
        raise PackBlockedError("reference_asset_unresolvable",
                               {"hint": "resolved SceneInstanceView 在 compile 期间消失"})

    cast = _cast_of(shot)
    by_name = {cb.character_name: cb for cb in binding.character_bindings}
    ordered = [by_name[name] for name in cast if name in by_name]  # §32 Shot order

    action = binding.action
    kind = action.kind if action is not None else "none"
    action_prop = (action.prop_id or "").strip() if action is not None else ""
    action_subject = (action.subject_character or "").strip() if action is not None else ""

    # ---- composition 覆盖图（按 cast 顺序遍历；输出路径不用 set 迭代）----
    comp_of: Dict[str, Any] = {}
    covered: Dict[str, List[Tuple[str, Any, Any]]] = {}
    for cb in ordered:
        if not cb.composition_id:
            continue
        comp = rdoc.compositions.get(cb.composition_id)
        if comp is None:
            # validity 刚判 READY → 此处消失 = compile race（§38 fail closed）
            raise PackBlockedError("reference_asset_unresolvable",
                                   {"composition_id": cb.composition_id,
                                    "hint": "composition 在 compile 期间消失"})
        comp_of[cb.character_name] = comp
        for b in comp.prop_bindings:
            covered.setdefault(b.prop_id, []).append((cb.character_name, comp, b))

    # ---- hand_off 唯一映射（§25：subject comp + 另一 comp 同 prop，恰两个）----
    temporal_by_comp: Dict[str, str] = {}
    handoff_classified: set = set()
    if kind == "hand_off" and action_prop:
        holders = covered.get(action_prop, [])
        if len(holders) == 2 and any(name == action_subject for name, _, _ in holders):
            for name, comp, _b in holders:
                temporal_by_comp[comp.composition_id] = \
                    "start" if name == action_subject else "end"
            handoff_classified.add(action_prop)

    # ---- §20 多重有效关系防御（hand_off 例外需严格证明）----
    for prop_id, holders in covered.items():
        if len(holders) < 2:
            continue
        if prop_id in handoff_classified:
            continue
        if kind == "hand_off" and action_prop == prop_id:
            raise PackBlockedError("action_semantics_ambiguous",
                                   {"prop_id": prop_id,
                                    "holders": [n for n, _, _ in holders],
                                    "hint": "无法唯一映射 hand_off start/end holder"})
        raise PackBlockedError("multiple_effective_prop_relations",
                               {"prop_id": prop_id,
                                "holders": [n for n, _, _ in holders],
                                "hint": "同一 prop 存在多个同时生效的组合关系"})
    if kind == "hand_off" and not handoff_classified:
        raise PackBlockedError("action_target_unspecified",
                               {"prop_id": action_prop,
                                "hint": "canonical 证据不足以唯一确定 hand_off recipient"})

    # ---- Scene 投影（复用 Scene System 自有 view-relevant 投影，§9/D6/D7）----
    scene_proj = SceneProjection(
        scene_instance_id=binding.scene_binding.scene_instance_id if binding.scene_binding else "",
        location_view_id=binding.scene_binding.location_view_id if binding.scene_binding else "",
        scene_instance_view_id=siv.scene_instance_view_id if siv is not None else None,
        location_name=location.name if location else "",
        location_identity_description=location.identity_description if location else "",
        location_style_notes=location.style_notes if location else "",
        view_label=base_view.label if base_view else "",
        camera_zone=base_view.camera_zone if base_view else "",
        look_direction=base_view.look_direction if base_view else "",
        view_relevant_scene_state=dict((siv_status or {}).get("view_relevant_scene_state") or {}),
        reference_asset_id=siv.reference_asset_id if siv is not None else None,
    )

    # ---- references（§34 语义序：scene → characters → action prop → direct props）----
    references: List[ReferenceItem] = []
    if siv is not None:
        references.append(ReferenceItem(
            source_type="scene_instance_view", source_id=siv.scene_instance_view_id,
            reference_asset_id=siv.reference_asset_id,
            purpose="scene_context", priority="primary", temporal_scope="throughout"))

    char_entries: List[CharacterPackEntry] = []
    action_prop_has_comp = False
    for cb in ordered:
        comp = comp_of.get(cb.character_name)
        comp_covers_action = bool(action_prop) and comp is not None and \
            any(b.prop_id == action_prop for b in comp.prop_bindings)
        if comp_covers_action:
            action_prop_has_comp = True
        look = rdoc.looks.get(cb.look_id)
        if look is None:
            # validity 刚判 READY → 消失 = race（§38）
            raise PackBlockedError("reference_asset_unresolvable",
                                   {"look_id": cb.look_id,
                                    "hint": "look 在 compile 期间消失"})
        hero, extras, unresolvable = select_approved_refs(
            rdoc, look.approved_reference_asset_ids, look.cover_asset_id)
        if unresolvable:
            raise PackBlockedError("reference_asset_unresolvable",
                                   {"look_id": cb.look_id,
                                    "unresolvable_asset_ids": unresolvable,
                                    "hint": "approved id 指向不存在的 ReferenceAsset"})
        if hero is None:
            raise PackBlockedError("reference_asset_unresolvable",
                                   {"look_id": cb.look_id,
                                    "hint": "look 无可解析 approved reference"})

        comp_temporal = temporal_by_comp.get(cb.composition_id) if comp else None
        comp_covers_action_flag = comp_covers_action
        if comp is not None and comp_temporal is None:
            comp_temporal = classify_temporal(kind, comp_covers_action_flag)

        # ---- Look / Composition 优先级（§12/§22-§25）----
        action_shaped = kind in ("pick_up", "put_down") and comp is not None \
            and comp_covers_action_flag
        if action_shaped:
            look_priority = "primary" if kind == "pick_up" else "supporting"
            look_temporal = "start" if kind == "pick_up" else "end"
        else:
            look_priority = "primary" if comp is None else "supporting"
            look_temporal = "static" if kind in ("none", "static", "custom") else "throughout"
        if comp is not None:
            if comp_temporal == "end":
                comp_priority = "supporting"      # §23/§42：end-state evidence
            elif comp_temporal == "start" and kind == "hand_off":
                comp_priority = "primary"         # §25 subject=start holder
            elif comp_temporal == "start":
                comp_priority = "primary"         # §24 put_down：start-state evidence
            else:
                comp_priority = "primary"         # §22.1/G1 静态组合 primary
            if kind == "custom" and comp is not None:
                comp_priority = "supporting"      # §26 custom 不做 start/end 分类
        else:
            comp_priority = None

        # ---- Character identity refs（legacy views 只读 adapter；§12 supporting）----
        from src.resources.store import legacy_character_references
        ident_temporal = "static" if kind in ("none", "static", "custom") else "throughout"
        for ref in legacy_character_references(project_dir, cb.character_name):
            references.append(ReferenceItem(
                source_type="character_identity", source_id=ref.asset_id,
                reference_asset_id=ref.asset_id, subject_character=cb.character_name,
                purpose="character_identity", priority="supporting",
                temporal_scope=ident_temporal,
                semantic_note=f"legacy view: {ref.view}"))

        # ---- Look refs ----
        for aid in ([hero] if hero else []) + extras:
            is_hero = aid == hero
            references.append(ReferenceItem(
                source_type="character_look", source_id=cb.look_id,
                reference_asset_id=aid, subject_character=cb.character_name,
                purpose="character_appearance",
                priority=look_priority if is_hero else "supporting",
                temporal_scope=look_temporal if is_hero else
                    ("static" if kind in ("none", "static", "custom") else "throughout"),
                semantic_note="approved hero" if is_hero else "extra approved angle"))

        # ---- Composition plate ----
        if comp is not None:
            plate_hero, _plate_extras, plate_unres = select_approved_refs(
                rdoc, comp.approved_reference_asset_ids, comp.cover_asset_id)
            if plate_unres:
                raise PackBlockedError("reference_asset_unresolvable",
                                       {"composition_id": comp.composition_id,
                                        "unresolvable_asset_ids": plate_unres})
            if plate_hero is None:
                raise PackBlockedError("reference_asset_unresolvable",
                                       {"composition_id": comp.composition_id,
                                        "hint": "composition 无可解析 approved plate"})
            references.append(ReferenceItem(
                source_type="composition", source_id=comp.composition_id,
                reference_asset_id=plate_hero, subject_character=cb.character_name,
                purpose="action_state" if comp_temporal in ("start", "end")
                        else "spatial_relation",
                priority=comp_priority, temporal_scope=comp_temporal,
                relation=comp.prop_bindings[0].relation if comp.prop_bindings else None,
                anchor=comp.prop_bindings[0].anchor if comp.prop_bindings else None,
                semantic_note=comp.composition_description or ""))

        char_entries.append(CharacterPackEntry(
            character_name=cb.character_name,
            look_id=cb.look_id,
            composition_id=cb.composition_id,
            character_description=char_desc.get(cb.character_name, ""),
            look_label=look.label,
            appearance_description=look.appearance_description,
            appearance_delta=look.appearance_delta,
            covered_props=[{"prop_id": b.prop_id, "relation": b.relation,
                            "anchor": b.anchor,
                            "visual_state_note": b.visual_state_note}
                           for b in comp.prop_bindings] if comp is not None else [],
            look_reference_asset_ids=([hero] if hero else []) + extras,
        ))

    # ---- action prop 的 standalone 身份证据（§19：identity supporting，不声明关系）----
    if action_prop:
        action_entity = rdoc.props.get(action_prop)
        if action_entity is None:
            raise PackBlockedError("reference_asset_unresolvable",
                                   {"prop_id": action_prop,
                                    "hint": "action.prop_id 在 compile 期间消失"})
        a_hero, a_extras, a_unres = select_approved_refs(
            rdoc, action_entity.approved_reference_asset_ids,
            action_entity.cover_asset_id)
        if a_unres:
            raise PackBlockedError("reference_asset_unresolvable",
                                   {"prop_id": action_prop,
                                    "unresolvable_asset_ids": a_unres})
        if a_hero is None:
            raise PackBlockedError("reference_asset_unresolvable",
                                   {"prop_id": action_prop,
                                    "hint": "action prop 无可解析 approved reference"})
        for aid in [a_hero] + a_extras:
            references.append(ReferenceItem(
                source_type="prop", source_id=action_prop,
                reference_asset_id=aid, prop_id=action_prop,
                purpose="prop_identity", priority="supporting",
                temporal_scope="throughout",
                semantic_note="action prop standalone identity（不声明关系——关系属组合/绑定）"))
        if not action_prop_has_comp:
            diagnostics.append({
                "reason": "action_state_without_composition_evidence",
                "detail": {"prop_id": action_prop,
                           "hint": "action prop 无 approved 组合 plate——start/end 状态缺组合级视觉证据"}})
    if kind == "custom":
        diagnostics.append({
            "reason": "action_temporal_unclassified",
            "detail": {"hint": "custom action 不做 start/end temporal 分类（§26：只用 explicit structured information）"}})

    # ---- direct props（§15/§17；binding.prop_bindings 顺序，§33）----
    prop_entries: List[PropPackEntry] = []
    for pb in binding.prop_bindings:
        subject: Optional[str]
        if action is not None and action_prop == pb.prop_id and action_subject:
            subject = action_subject                      # Rule 1
        elif len(cast) == 1:
            subject = cast[0]                             # Rule 6 单角色确定性解析
        elif len(cast) == 0:
            raise PackBlockedError("prop_subject_ambiguous",
                                   {"prop_id": pb.prop_id,
                                    "hint": "cast 为空且当前 Resources 关系词表（全部 "
                                            "CompositionAnchor 均为 character-local）无法表达 "
                                            "scene/world 放置——fail closed（§17 Rule 3/§75）"})
        else:
            raise PackBlockedError("prop_subject_ambiguous",
                                   {"prop_id": pb.prop_id, "cast": cast,
                                    "hint": "多角色 + character-local 关系 + 无 explicit subject"
                                            "（§17 Rule 4；不猜最合理者——Rule 5）"})
        prop = rdoc.props.get(pb.prop_id)
        if prop is None:
            raise PackBlockedError("reference_asset_unresolvable",
                                   {"prop_id": pb.prop_id,
                                    "hint": "prop 在 compile 期间消失"})
        p_hero, p_extras, p_unres = select_approved_refs(
            rdoc, prop.approved_reference_asset_ids, prop.cover_asset_id)
        if p_unres:
            raise PackBlockedError("reference_asset_unresolvable",
                                   {"prop_id": pb.prop_id,
                                    "unresolvable_asset_ids": p_unres})
        if p_hero is None:
            raise PackBlockedError("reference_asset_unresolvable",
                                   {"prop_id": pb.prop_id,
                                    "hint": "prop 无可解析 approved reference"})
        prop_entries.append(PropPackEntry(
            prop_id=pb.prop_id, name=prop.name, relation=pb.relation,
            anchor=pb.anchor, visual_state_note=pb.visual_state_note,
            subject_character=subject,
            reference_asset_ids=[p_hero] + p_extras))
        references.append(ReferenceItem(
            source_type="prop", source_id=pb.prop_id,
            reference_asset_id=p_hero, prop_id=pb.prop_id,
            subject_character=subject,
            purpose="prop_identity", priority="primary",
            temporal_scope="throughout",
            semantic_note=f"direct binding relation={pb.relation}@{pb.anchor}"
                          + (f" ({pb.visual_state_note})" if pb.visual_state_note else "")))
        for aid in p_extras:
            references.append(ReferenceItem(
                source_type="prop", source_id=pb.prop_id,
                reference_asset_id=aid, prop_id=pb.prop_id,
                subject_character=subject,
                purpose="prop_identity", priority="supporting",
                temporal_scope="throughout", semantic_note="extra approved angle"))

    pack = ShotReferencePack(
        shot_id=binding.shot_id,
        scene_id=binding.scene_id,
        basis=PackBasis(
            binding_revision=binding_revision,
            binding_document_revision=document_revision,
            binding_readiness="READY",
            dependency_fingerprints=_fingerprints_dict(binding),
            resolved_scene_instance_view_id=siv.scene_instance_view_id if siv is not None else None,
            visual_profile_fingerprint=profile_fingerprint,
        ),
        visual_profile=ProfileProjection(
            render_family=profile.render_family,
            art_direction=profile.art_direction,
            style_tags=list(profile.style_tags),
            negative_style_constraints=list(profile.negative_style_constraints),
            visual_profile_fingerprint=profile_fingerprint,
        ),
        scene=scene_proj,
        characters=char_entries,
        props=prop_entries,
        action=ActionProjection(
            kind=kind,
            subject_character=action_subject,
            prop_id=action_prop,
            start_state=action.start_state if action is not None else "",
            end_state=action.end_state if action is not None else "",
            note=action.note if action is not None else "",
        ),
        references=references,
        diagnostics=diagnostics,
    )
    return pack


def _fingerprints_dict(binding: Any) -> Dict[str, Any]:
    return binding.dependency_fingerprints.to_dict()

