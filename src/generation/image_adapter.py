# src/generation/image_adapter.py
# Shot Production Binding V1 — Phase S3C.1：Image / First-frame Generation Adapter。
#
# 职责（11.md §1）：把 COMPILED 的 ShotReferencePack + Shot creative intent 转换成
# 当前真实图片 provider（AgnesClient.generate_image）的输入。**不重新选择生产真相**——
# 用哪个 Look/Composition/Prop/SIV/Profile 全部由 Binding→Validity→Pack 决定，本模块只消费。
#
# 12 条冻结原则的落点：
#   1. first-frame temporal rule（§8/§13，本阶段最重要）：temporal_scope ∈
#      {start, throughout, static} 才可作为首帧当前态参考；**end 一律排除，且先于
#      priority**——priority=primary + temporal=end 仍不得进入首帧。
#   2. pick_up（§9）：持物 Composition（end）被排除 → 首帧参考 = Look(start) +
#      Scene(throughout) + Pistol identity(throughout)；prompt 生产上下文只含
#      action.start_state，绝不含 end_state（§22）。
#   3. put_down（§10）：持物 Composition（start）可作为首帧 primary；Look(end) 被排除。
#   4. hand_off（§11）：start 组合进、end-only 组合排除；Pack 已 BLOCK 的歧义不重解释。
#   5. custom（§12）：不解析自然语言；Pack 给什么 temporal 消费什么；
#      action_temporal_unclassified 下传为 diagnostic，不升级 blocking。
#   6. 风格唯一来源 = pack.visual_profile（经 P1 单一编译点
#      compile_project_style_instruction——负面约束以 "must avoid" 文本进入 prompt，
#      与 Resources/Scene 生成同口径）；**绝不 fallback StyleAnchor**（§19）。
#      size_tier/aspect_ratio 是画幅几何参数，继续用现有 generation config 来源
#      （handler 现行为），不是风格回落（§6）。
#   7. 媒体解析（§23）只走权威 owner：ReferenceAsset→Resources；SIV reference→
#      其注册的 approved asset；character_identity（legacy 合成 id）→ 隔离的
#      compatibility resolver。**绝不 glob/文件名猜测/路径拼接**（§56）。
#   8. pre-dispatch race guard（§25）：provider dispatch 前重读 validity，与 Pack basis
#      比对（readiness/binding revision/document revision/resolved SIV——语义同 S3B.3
#      post-guard）；不一致 → dependency_changed_before_generation，provider 不调用。
#   9. 零 canonical mutation（§32）：adapter 只读；生成结果写回仍走 handler 既有路径。
#  10. Determinism（§30）：无随机/无时钟/set 迭代输出；顺序 = Pack references 确定性序。
#
# Provider capability（§14，来自真实代码 AGNES_API_FACTS/agnes_client）：image_refs 为
# 列表、无文档化数量上限、不支持 negative_prompt 参数（负面约束走 P1 风格指令文本）、
# 不支持 seed（图片）、无 character/scene/style slot 概念 → 全部 eligible 参考按 Pack
# 确定性顺序进入 image_refs；无容量截断逻辑（不虚构 capacity，§46）。
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# first-frame 允许的 temporal scopes（§8；end 排除且先于 priority）
_FIRST_FRAME_ELIGIBLE_TEMPORAL = ("start", "throughout", "static")

# ============ Shot-level Scene Reference Policy (Phase D2, 30.md §6/§7) ============
# 三态：full=场景图作为 conditioning；context_only=场景语义仍走 prompt 生产上下文
# （_production_context_text 的 Scene 行），但场景图不进 image_refs；auto=按景别/
# 机位的确定性导演策略映射。
# 🔴 默认值翻转（full→auto）依据 30.md §4 A/B 真机证据（64 候选 × 16 镜 × Agnes 2K）：
#   1. 场景图主导构图只在「意图空间≠plate 空间」时爆发（s17_c02 内景 ECU + 外景庭院
#      plate → A 组渲染成 WS 全景；B 组显著改善）；
#   2. 紧景别（MCU/CU/ECU/OTS/insert）两组都偏宽，但 B 组不劣于 A 且解除 plate 冲突
#      → 紧景别默认 context_only；
#   3. MS 组 A 的景别距离 0.67 优于 B 1.60，场景身份 fail B 14/32 vs A 3/32、道具
#      保真 B 15/32 vs A 8/32 → MS/WS/EWS 默认 full（场景图在宽/中景有益无害）。
SCENE_REFERENCE_MODES = ("full", "context_only", "auto")
DEFAULT_SCENE_REFERENCE_MODE = "auto"

# human_reference_mode（D2 §5.2 无参考实验后新增）：keep=每角色 1 张 look；
# drop=人形参考全不进 provider（identity 走 prompt 文本）；auto=context_only 时 drop。
# crop（31.md C4/G4）：每角色 1 张 look 的**确定性头肩裁剪派生**（identity_crop.py，
# adapter 级派生物不落 canonical）——identity 信号保留、全身构图锚解除；auto 映射
# 是否从 drop 翻到 crop 由真机 A/B 证据决定（31.md §19 证据门控，本 commit 不翻）。
HUMAN_REFERENCE_MODES = ("keep", "drop", "crop", "auto")
DEFAULT_HUMAN_REFERENCE_MODE = "auto"


class BoundGenerationError(Exception):
    """Binding-aware 生成路径的结构化失败（§33）。
    code ∈ {binding_not_ready, reference_media_unresolvable,
    dependency_changed_before_generation}；绝不伪装成 provider 500，
    也绝不让 NOT_READY 回落 legacy。"""

    def __init__(self, code: str, detail: Any = None):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")

    def summary(self) -> str:
        d = self.detail if isinstance(self.detail, dict) else {"detail": self.detail}
        issues = d.get("issues") or []
        if issues:
            reasons = ", ".join(sorted({str(i.get("reason")) for i in issues}))
            return f"{self.code} [{reasons}]"
        hint = str(d.get("hint") or "")
        return f"{self.code} {hint}".strip()


@dataclass
class BoundImageRequest:
    """Adapter 结果（§7）：ephemeral、无业务 ID、不落盘、不写回任何 canonical。"""
    shot_id: str = ""
    prompt: str = ""
    image_refs: List[str] = field(default_factory=list)
    used_reference_ids: List[str] = field(default_factory=list)
    dropped_optional: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    semantic_roles: List[Dict[str, Any]] = field(default_factory=list)  # §29 观测
    basis: Dict[str, Any] = field(default_factory=dict)
    scene_reference_mode: str = DEFAULT_SCENE_REFERENCE_MODE  # D2：resolved 结果
    human_reference_mode: str = DEFAULT_HUMAN_REFERENCE_MODE  # D2 §5.2：resolved 结果
    # refs 布局语义（31.md C1 seam：image_refs 与 semantic_roles 平行段的特例项位置）——
    # pack_only=逐项对应；continuity_tail=末尾多一条 reference_previous 末帧（creative
    # relay，无 pack role）；first_frame_head=头部多一条尾帧构图基础（已选首帧，无 pack role）。
    ref_layout: str = "pack_only"
    # Phase 3 provenance 补全：continuity_tail 布局下，实际发送的尾帧身份
    # （来源镜头/来源视频版本/不可变 URL）。此前只记 ref_layout 标志，持久
    # receipt（used_reference_ids/sent_ref_sequence）不含这条临时尾帧——审计问
    # "图片请求里到底发了哪张尾帧"时无法回答。
    continuity_ref: Optional[Dict[str, Any]] = None
    # 31.md C5：本请求是否启用人数排除子句（provenance 可追溯；默认关）
    population_constraint: bool = False

    def to_dict(self) -> dict:
        return {
            "shot_id": self.shot_id,
            "prompt": self.prompt,
            "image_refs": list(self.image_refs),
            "used_reference_ids": list(self.used_reference_ids),
            "dropped_optional": list(self.dropped_optional),
            "diagnostics": list(self.diagnostics),
            "semantic_roles": list(self.semantic_roles),
            "basis": dict(self.basis),
            "scene_reference_mode": self.scene_reference_mode,
            "human_reference_mode": self.human_reference_mode,
            "ref_layout": self.ref_layout,
            "continuity_ref": dict(self.continuity_ref) if self.continuity_ref else None,
            "population_constraint": self.population_constraint,
        }

    def to_generation_request(self, *, generation_type: str = "first_frame",
                              size_tier: str = "", ratio: str = "",
                              model: Optional[str] = None):
        """provider-neutral 投影（31.md §10）：BoundImageRequest → GenerationRequest。
        generation_type 由调用方按构建器显式声明（first_frame/last_frame——本 dataclass
        同时服务两者）。纯投影不重解释：media/顺序/取舍全部来自本 request（image_refs 与
        semantic_roles 平行段 + ref_layout 特例项）；checksum 取自解析时的
        ReferenceAsset（roles 可携带）；derivation 由预算/裁剪层在 roles 中标注。"""
        from src.generation.request import GenerationRequest, ReferenceInput
        refs = list(self.image_refs)
        roles = list(self.semantic_roles)
        inputs: List[ReferenceInput] = []
        if self.ref_layout == "first_frame_head" and refs:
            inputs.append(ReferenceInput(
                source_type="first_frame_base", semantic_role="composition_base",
                temporal_scope="start", media=refs[0]))
            refs = refs[1:]
        if self.ref_layout == "continuity_tail" and refs and len(refs) > len(roles):
            tail = refs[-1]
            inputs.extend(ReferenceInput(
                asset_id=str(r.get("reference_asset_id") or r.get("source_id") or ""),
                source_type=str(r.get("source_type") or ""),
                semantic_role=str(r.get("purpose") or ""),
                priority=str(r.get("priority") or ""),
                temporal_scope=str(r.get("temporal_scope") or ""),
                subject_character=r.get("subject_character"),
                prop_id=r.get("prop_id"),
                media=m, checksum=r.get("checksum"),
                derivation=r.get("derivation"))
                for r, m in zip(roles, refs[:-1]))
            inputs.append(ReferenceInput(
                source_type="continuity_previous_end", semantic_role="continuity",
                temporal_scope="start", media=tail))
        else:
            inputs.extend(ReferenceInput(
                asset_id=str(r.get("reference_asset_id") or r.get("source_id") or ""),
                source_type=str(r.get("source_type") or ""),
                semantic_role=str(r.get("purpose") or ""),
                priority=str(r.get("priority") or ""),
                temporal_scope=str(r.get("temporal_scope") or ""),
                subject_character=r.get("subject_character"),
                prop_id=r.get("prop_id"),
                media=m, checksum=r.get("checksum"),
                derivation=r.get("derivation"))
                for r, m in zip(roles, refs))
        return GenerationRequest(
            generation_type=generation_type, shot_id=self.shot_id,
            prompt=self.prompt, reference_inputs=inputs,
            ratio=ratio, size_tier=size_tier, model=model,
            diagnostics=list(self.diagnostics),
            provenance={
                "basis": dict(self.basis),
                "used_reference_ids": list(self.used_reference_ids),
                "dropped_optional": list(self.dropped_optional),
                "scene_reference_mode": self.scene_reference_mode,
                "human_reference_mode": self.human_reference_mode,
                "ref_layout": self.ref_layout,
                **({"continuity_tail": dict(self.continuity_ref)}
                   if self.continuity_ref else {}),
            })


def shot_has_binding(project_dir, shot_id: str) -> bool:
    """「本镜存在任何 binding 元数据」（key-presence，保留 B1 语义供诊断）。
    🔴 C1 18.md §3：生成路由不再使用本判定——dialogue_speaker_binding /
    audio_event_intent / production_notes 等非视觉元数据的存在 ≠ 拥有完整
    Visual Production Binding。视频/首尾帧 BOUND 路由用
    shot_has_visual_production_binding。bindings.json 损坏 →
    ShotBindingSchemaError 显式异常（§39：损坏不静默当 UNBOUND）。"""
    from src.shot_binding.store import ShotBindingStore
    doc = ShotBindingStore(project_dir).load()
    return str(shot_id or "").strip() in doc.bindings


def shot_has_visual_production_binding(project_dir, shot_id: str) -> bool:
    """C1 18.md §3：BOUND / UNBOUND 路由判定（首帧/尾帧/视频统一）。
    只有 binding 携带**视觉生产内容**（scene_binding / character_bindings /
    prop_bindings / action 任一）才算 Visual Production Binding；
    仅 dialogue_speaker_binding / audio_event_intent / production_notes 的
    binding 不触发 BOUND 路由（否则会路由进 BOUND 链后因视觉 binding 不完整
    被 binding_not_ready 拦死）。dialogue metadata ≠ visual routing predicate。
    bindings.json 损坏 → ShotBindingSchemaError 显式异常（与 shot_has_binding
    同口径：损坏不静默当 UNBOUND）。"""
    from src.shot_binding.store import ShotBindingStore
    doc = ShotBindingStore(project_dir).load()
    binding = doc.bindings.get(str(shot_id or "").strip())
    if binding is None:
        return False
    return bool(
        binding.scene_binding is not None
        or binding.character_bindings
        or binding.prop_bindings
        or binding.action is not None
    )


# ============ temporal 过滤（§8/§13：先于 priority）============
# S3C.2 §6：last-frame 的 temporal rule 与 first-frame 镜像对称——
#   first: start/throughout/static 进、end 排除；last: end/throughout/static 进、
#   start 排除。两者都是 temporal eligibility 先于 priority（primary 不得覆盖）。
_FRAME_ELIGIBLE_TEMPORAL = {
    "first": ("start", "throughout", "static"),
    "last": ("end", "throughout", "static"),
}


def filter_frame_references(references: List[dict],
                            temporal_target: str) -> Tuple[List[dict], List[dict]]:
    """Step 1 temporal eligibility（S3C.2 §6 泛化）：temporal_target ∈ {first, last}。
    返回 (eligible, excluded)。excluded 仅供 diagnostics——绝不因 primary 而回流。
    单次遍历保持 Pack 原确定性顺序。"""
    allowed = _FRAME_ELIGIBLE_TEMPORAL[temporal_target]
    eligible: List[dict] = []
    excluded: List[dict] = []
    for r in references:
        if r.get("temporal_scope") in allowed:
            eligible.append(r)
        else:
            excluded.append(r)
    return eligible, excluded


def filter_first_frame_references(references: List[dict]) -> Tuple[List[dict], List[dict]]:
    """S3C.1 既有入口（first-frame 口径），保留名兼容；等价于
    filter_frame_references(references, "first")。"""
    return filter_frame_references(references, "first")


# ============ 媒体解析（§23：权威 owner；§24 required/optional 分明）============

def _resolve_item_media(item: dict, rdoc: Any, project_dir: Path
                        ) -> Optional[str]:
    """单个 ReferenceItem → provider 媒体（公网 URL 或 data URI）。
    - composition/character_look/prop/scene_instance_view：reference_asset_id →
      Resources ReferenceAsset（approval saga 注册的权威记录）→ to_agnes_image_ref
      （local 可读→data URI；否则 url）。
    - character_identity：legacy 合成 id（legacy:{name}:{view}）→ 隔离 compatibility
      resolver（src.resources.store.legacy_character_references 只读 adapter）。
    解析不出 → None（由调用方按 required/optional 分流）。"""
    from src.resources.generation import to_agnes_image_ref
    source_type = item.get("source_type")
    asset_id = item.get("reference_asset_id")
    if source_type == "character_identity":
        from src.resources.store import legacy_character_references
        synthetic = str(asset_id or "")
        parts = synthetic.split(":", 2)
        if len(parts) != 3 or parts[0] != "legacy":
            return None
        for ref in legacy_character_references(project_dir, parts[1]):
            if ref.asset_id == synthetic:
                return to_agnes_image_ref(project_dir, url=ref.url, local=ref.local)
        return None
    if not asset_id or asset_id not in rdoc.reference_assets:
        return None
    asset = rdoc.reference_assets[asset_id]
    return to_agnes_image_ref(project_dir, url=asset.url, local=asset.local)


# ============ 生产上下文文本（§21：确定性、无 LLM；§14：显式 temporal_target）============

def _production_context_text(pack: dict, rdoc: Any,
                             temporal_target: str = "first") -> str:
    """Pack 生产上下文 → prompt 片段。temporal_target 显式传递（S3C.2 §14：
    不靠调用方手工删字符串）——
      first: 只含 action.start_state（🔴 end_state 绝不进入首帧，§22）；
      last:  只含 action.end_state（🔴 start_state 绝不进入尾帧，§14）；
      video: start_state + end_state 两行（motion 从 start 到 end，§14 允许）。
    Scene/角色/组合关系行对三个 target 相同。"""
    parts: List[str] = []
    scene = pack.get("scene") or {}
    state = scene.get("view_relevant_scene_state") or {}
    state_bits = [str(state.get(k)) for k in
                  ("time_of_day", "weather", "lighting", "environment_conditions")
                  if state.get(k)]
    if scene.get("location_name") or state_bits:
        seg = f"Scene: {scene.get('location_name') or 'the established location'}"
        if state_bits:
            seg += f" ({', '.join(state_bits)})"
        parts.append(seg + ".")
    for ch in pack.get("characters") or []:
        seg = str(ch.get("character_name") or "")
        if ch.get("look_label"):
            seg += f" wearing {ch['look_label']}"
        if ch.get("appearance_description"):
            seg += f" — {ch['appearance_description']}"
        if seg.strip():
            parts.append(seg + ".")
        for cp in ch.get("covered_props") or []:
            prop = rdoc.props.get(cp.get("prop_id"))
            pname = (prop.name if prop else "") or str(cp.get("prop_id"))
            seg = f"{ch.get('character_name')} {cp.get('relation')} " \
                  f"{pname} ({cp.get('anchor')})."
            # C2（22.md §9-§10）：镜内连续性要求此前在 pack 中但被丢弃——编译它。
            # 只进 video target（连续性是镜内过程约束；first/last 帧语义不变）。
            note = str(cp.get("visual_state_note") or "").strip().rstrip(".")
            if note and temporal_target == "video":
                seg += f" Keep this consistent throughout the shot: {note}."
            parts.append(seg)
    # ---- C2（22.md §9-§10）：direct PropBinding 投影此前整体未编译（§5.B 丢失）——
    #      关系/锚点/镜内状态要求进 video prompt。first/last target 保持原行为。----
    if temporal_target == "video":
        for p in pack.get("props") or []:
            pname = str(p.get("name") or "") or str(p.get("prop_id") or "")
            seg = f"{p.get('subject_character') or 'the scene'} " \
                  f"{p.get('relation') or 'held'} {pname} " \
                  f"({p.get('anchor') or 'in hand'})."
            note = str(p.get("visual_state_note") or "").strip().rstrip(".")
            if note:
                seg += f" Keep this consistent throughout the shot: {note}."
            parts.append(seg)
    action = pack.get("action") or {}
    has_action = action.get("kind") not in (None, "", "none")
    # 🔴 first：只输出 start_state；end_state 绝不进入首帧当前态约束（§22）。
    if temporal_target in ("first", "video") and has_action and action.get("start_state"):
        parts.append(f"Action start state: {action['start_state']}.")
    # 🔴 last：只输出 end_state；start_state 绝不进入尾帧当前态约束（S3C.2 §14）。
    if temporal_target in ("last", "video") and has_action and action.get("end_state"):
        parts.append(f"Action end state: {action['end_state']}.")
    # C2（22.md §10）：action.note = 导演写的镜内连续性要求（如道具朝向）——
    # 此前在 pack 中但被丢弃。只进 video target（过程约束）。
    if temporal_target == "video" and has_action:
        anote = str(action.get("note") or "").strip().rstrip(".")
        if anote:
            parts.append(f"Important continuity: {anote}.")
    return " ".join(p.strip() for p in parts if p and p.strip())


def _style_instruction_from_pack(pack: dict) -> str:
    """P1 单一编译点复用：pack.visual_profile 语义 → 确定性 style instruction
    （含 negative_style_constraints 的 must avoid 文本）。绝不读 StyleAnchor。"""
    from src.visual_profile.compiler import compile_project_style_instruction
    from src.visual_profile.models import ProjectVisualProfile
    vp = pack.get("visual_profile") or {}
    profile = ProjectVisualProfile(
        render_family=str(vp.get("render_family") or ""),
        art_direction=str(vp.get("art_direction") or ""),
        style_tags=list(vp.get("style_tags") or []),
        negative_style_constraints=list(vp.get("negative_style_constraints") or []),
    )
    return compile_project_style_instruction(profile)


# ============ pre-dispatch race guard（§25）============

def _pre_dispatch_guard(svc: Any, shot_id: str, basis: dict) -> None:
    """provider dispatch 前轻量守卫：重读 validity，与 Pack basis 对齐比较
    （语义复用 S3B.3 post-guard 口径；不复制指纹引擎）。不一致 → provider 不调用。"""
    post = svc.read_validity(shot_id)
    aligned = (
        post["readiness"] == "READY"
        and post["binding_revision"] == basis.get("binding_revision")
        and post["document_revision"] == basis.get("binding_document_revision")
        and post["resolved_dependencies"].get("scene_instance_view_id")
        == basis.get("resolved_scene_instance_view_id")
    )
    if not aligned:
        raise BoundGenerationError("dependency_changed_before_generation",
                                   {"shot_id": shot_id,
                                    "post_readiness": post["readiness"],
                                    "issues": post["issues"],
                                    "hint": "Pack basis 与当前 canonical 不再对齐——"
                                            "显式重试整个 generation（不 auto refresh）"})


def post_generation_basis_guard(svc: Any, shot_id: str, basis: dict) -> None:
    """31.md §13 阶段3（Phase C2）：provider 返回后、canonical commit 前复验 Pack
    basis——生成窗口内生产依赖已变 → 候选不得 commit（绝不把旧 basis 结果冒充成
    当前有效 candidate；文件保留为 diagnostic artifact，批次不落库，显式重新生成）。
    对比键 = readiness + binding_revision + resolved SIV（actual-deps-only，与
    _BOUNDARY_COMPARE_KEYS 同口径：document_revision 不比——别的 shot 的 binding
    refresh 不得误伤本镜 commit；依赖内容变化 → validity NOT_READY → readiness 键
    已覆盖）。"""
    post = svc.read_validity(shot_id)
    aligned = (
        post["readiness"] == "READY"
        and post["binding_revision"] == basis.get("binding_revision")
        and post["resolved_dependencies"].get("scene_instance_view_id")
        == basis.get("resolved_scene_instance_view_id")
    )
    if not aligned:
        raise BoundGenerationError(
            "dependency_changed_before_commit",
            {"shot_id": shot_id,
             "post_readiness": post["readiness"],
             "issues": post["issues"],
             "recorded_basis": {"binding_revision": basis.get("binding_revision"),
                                "resolved_scene_instance_view_id":
                                    basis.get("resolved_scene_instance_view_id")},
             "hint": "provider 生成窗口内生产依赖已变化——候选文件保留为 diagnostic "
                     "artifact，批次不 commit；显式重新生成（不 auto refresh）"})


# ============ boundary-frame freshness（S3C.2 §1 Case C / §17）============
# 现有 ImageBatch 历史上无 provenance（Case C 审计结论）——"Binding 现在 READY"≠
# "磁盘上的旧 frame fresh"（T1 生成→T2 依赖变 stale→T3 refresh 后旧帧仍在盘上）。
# 最小修复（generation-result metadata）：BOUND 生成路径把生成时 Pack basis 记入
# ImageBatch.generation_basis；消费方（last-frame / video）dispatch 前比对——
# 缺失/不一致 → 结构化失败，绝不静默当作 fresh，绝不 auto regenerate。

def selected_boundary_batch(shot: Any, slot: str) -> Tuple[Any, Any]:
    """authoritative selection truth（§16）：selected_{first,last}_frame 指针 →
    (owning batch, candidate)。绝不 glob / files[-1] / mtime / 文件名猜测。
    找不到 → (None, None)。"""
    cid = shot.selected_first_frame if slot == "first" else shot.selected_last_frame
    if not cid:
        return None, None
    batches = shot.first_frame_batches if slot == "first" else shot.last_frame_batches
    for b in batches or []:
        for c in b.candidates or []:
            if c.candidate_id == cid:
                return b, c
    return None, None


# 对比键：binding_revision（本镜 binding 内容——refresh 翻动它）+ resolved SIV。
# document_revision 覆盖整个 bindings 文档——别的 shot refresh 会误伤本镜 fresh
# frame（违反 actual-dependencies-only），故不比；依赖内容变化 → validity NOT_READY
# （gate 已挡）→ 显式 refresh → binding_revision 翻动——两键经传递覆盖全部依赖维度。
_BOUNDARY_COMPARE_KEYS = ("binding_revision", "resolved_scene_instance_view_id")


def boundary_stale_detail(batch: Any, pack_basis: dict) -> Optional[dict]:
    """§17 boundary artifact freshness。batch.generation_basis（生成时 Pack basis
    快照）vs 当前 pack basis。None = fresh；dict = stale detail。
    missing basis（legacy / binding 前生成）→ fail closed（artifact_basis_missing），
    绝不当作 fresh（J3）。"""
    batch_id = getattr(batch, "batch_id", "")
    recorded = dict(getattr(batch, "generation_basis", None) or {})
    if not recorded:
        return {"reason": "artifact_basis_missing", "batch_id": batch_id,
                "hint": "boundary frame 无 generation provenance（legacy 或 binding 前"
                        "生成）——显式重新生成，绝不静默当作 fresh"}
    if str(recorded.get("binding_readiness") or "") != "READY":
        return {"reason": "artifact_basis_mismatch", "batch_id": batch_id,
                "field": "binding_readiness",
                "hint": "provenance 记录的生成时 binding 非 READY——非合法 BOUND 产物"}
    for k in _BOUNDARY_COMPARE_KEYS:
        if recorded.get(k) != (pack_basis or {}).get(k):
            return {"reason": "artifact_basis_mismatch", "batch_id": batch_id,
                    "field": k, "recorded": recorded.get(k),
                    "current": (pack_basis or {}).get(k),
                    "hint": "boundary frame 基于旧 basis 生成——显式重新生成"
                            "（绝不 auto regenerate）"}
    return None


# ============ 共享内核（S3C.2 §23：小步提取，不做 Universal Adapter 框架）============

def _gate_ready_pack(svc: Any, sid: str) -> dict:
    """validity gate → compile pack（自带 pre/post 守卫）→ pack dict。
    BLOCKED → 结构化错误（BOUND 绝不回落 legacy）。"""
    from src.shot_binding.validity import READY
    validity = svc.read_validity(sid)
    if validity["readiness"] != READY:
        raise BoundGenerationError("binding_not_ready",
                                   {"shot_id": sid, "readiness": validity["readiness"],
                                    "issues": validity["issues"],
                                    "hint": "BOUND shot 绝不 fallback legacy（§27）"})
    compiled = svc.compile_reference_pack(sid)
    if compiled["status"] != "COMPILED":
        reasons = {str(i.get("reason")) for i in compiled.get("issues") or []}
        if "dependency_changed_during_compile" in reasons:
            raise BoundGenerationError("dependency_changed_before_generation",
                                       {"shot_id": sid, "issues": compiled["issues"]})
        raise BoundGenerationError("binding_not_ready",
                                   {"shot_id": sid, "issues": compiled["issues"],
                                    "hint": "pack 编译被拒（语义歧义/依赖失效）"})
    return compiled["pack"]


# ============ Provider input image cap (Phase D #35 real-machine fix) ============
# Agnes i2i cap: at most 6 input images (400 invalid_request, Golden Run Batch 1
# evidence -- multi-character shot Pack can reach 9~13 refs). Deterministic
# selection: keep all primary (truncate in original order if over cap), fill
# remaining slots in original order; dropped items recorded with
# reason=provider_input_image_cap, never silent.
MAX_PROVIDER_INPUT_IMAGES = 6


# ============ Shot-level Scene Reference Policy (Phase D2, 30.md §6/§7) ============
# （常量定义见文件头部；此处为解析与预算逻辑。）

# auto 映射（A/B 后定稿）：EWS/WS/MS→full（场景图在宽/中景有益：景别距离 0.67 vs
# 1.60、场景身份 fail 3/32 vs 14/32）；MCU/CU/ECU/OTS/insert→context_only（解除
# plate 构图冲突）。纯确定性，输入只有 shot 字段。
_AUTO_CONTEXT_ONLY_SIZE_KEYS = ("close-up", "closeup", "extreme close", "medium close")
_AUTO_FULL_SIZE_KEYS = ("extreme wide", "wide shot", "establishing")
_AUTO_CONTEXT_ONLY_ANGLE_KEYS = ("ots", "over-the-shoulder", "over the shoulder",
                                 "insert", "detail")


def resolve_scene_reference_mode(mode: Optional[str], shot: Any) -> str:
    """None→默认（A/B 证据后=auto）；显式 full/context_only 直通；auto→确定性导演映射。
    未知值 fail closed（ValueError）。"""
    m = (str(mode or "").strip().lower() or DEFAULT_SCENE_REFERENCE_MODE)
    if m in ("full", "context_only"):
        return m
    if m != "auto":
        raise ValueError(f"scene_reference_mode 非法: {mode!r}（允许 {'/'.join(SCENE_REFERENCE_MODES)}）")
    size = (getattr(shot, "shot_size", "") or "").lower()
    angle_comp = " ".join([(getattr(shot, "camera_angle", "") or ""),
                           (getattr(shot, "composition", "") or "")]).lower()
    if any(k in angle_comp for k in _AUTO_CONTEXT_ONLY_ANGLE_KEYS):
        return "context_only"
    if any(k in size for k in _AUTO_CONTEXT_ONLY_SIZE_KEYS):
        return "context_only"
    if any(k in size for k in _AUTO_FULL_SIZE_KEYS):
        return "full"
    return "full"              # MS 及未识别景别：A/B 证据=full（场景图对 MS 有益）


# ============ Human Reference Budget (Phase D2, 30.md §8/§9) ============
# Golden Run 审计实证（D2 §3）：每角色 pack 内 1 look + 3 legacy identity 视图；
# 单角色 5 refs ≤ cap 6 → 4 张人形全发 → 克隆人/幻觉路人根因。Commit J 只是超限缓解。
# D2 预算原则（30.md §8）：同一个真实角色，不要无意义同时发送多张人形 reference——
#   - legacy identity（front/side/back）一律不进 provider（reason=human_reference_budget）；
#   - 每个出场角色只保 pack 序第一张 character_look（extras 同 reason 裁剪）；
#   - 场景图按 resolve_scene_reference_mode 决定 0/1 张；
#   - cap(6) 仍作最终护栏（多人 + 多道具极端情形）。
#
# human_reference_mode（D2 §5.2 无参考实验后新增）：
#   keep=发每角色 1 张 look（WS/MS 默认）；drop=人形参考全不进 provider（identity 走
#   prompt 文本）；auto=scene_reference_mode 解析为 context_only（紧景别）时自动 drop。
#   依据：无参考实验（4 紧景别镜 ×2，Agnes 2K）——observed 尺寸从 WS/EWS 收敛到
#   MCU/MS、人数从 5~11 收敛到 1~2。全身白底立绘参考会把构图锚定在「全身+环境」。
# （常量 HUMAN_REFERENCE_MODES / DEFAULT_HUMAN_REFERENCE_MODE 定义在文件头部。）

_TIGHT_SIZE_KEYS = ("extreme close", "close-up", "closeup")


def resolve_human_reference_mode(mode: Optional[str], shot: Any,
                                 resolved_scene_mode: str) -> str:
    """auto：scene 解析为 context_only 时按依据分两路——

    - **景别驱动的紧景别（CU/ECU/MCU 家族，_AUTO_CONTEXT_ONLY_SIZE_KEYS）→
      crop**（Phase 42 定版：同 binding 三臂对照 P3——相对 drop 构图不变、身份
      显著变好；相对 keep 无全身立绘构图锚；EXP2 图像矩阵+EXP3 多种子用户逐图
      评审确认 crop=跨镜最稳配方。identity_crop 派生失败诚实回落源媒体并记
      diagnostic，绝不 silent）。31.md §19 悬案就此落定。
    - **角度驱动的 context_only（OTS/insert/detail，景别本身不紧）→ drop**
      （无 crop 实证；insert/detail 主体常为道具——人形参考反而引入人物风险。
      例外情况，Skill 决策入口与 references 有档）。

    scene 非 context_only → keep。显式 keep/drop/crop 直通；未知值 ValueError。"""
    m = (str(mode or "").strip().lower() or DEFAULT_HUMAN_REFERENCE_MODE)
    if m in ("keep", "drop", "crop"):
        return m
    if m != "auto":
        raise ValueError(f"human_reference_mode 非法: {mode!r}"
                         f"（允许 {'/'.join(HUMAN_REFERENCE_MODES)}）")
    if resolved_scene_mode != "context_only":
        return "keep"
    size = (getattr(shot, "shot_size", "") or "").lower()
    if any(k in size for k in _AUTO_CONTEXT_ONLY_SIZE_KEYS):
        return "crop"
    return "drop"


def resolve_identity_crop_method(shot: Any) -> str:
    """crop 档的确定性方法映射（输入只有 shot 字段）：extreme close → face（面部
    特写档）；其余紧景别 → bust（头肩档）。纯确定性，与 scene mode 同风格。"""
    size = (getattr(shot, "shot_size", "") or "").lower()
    return "face" if "extreme close" in size else "bust"


def _apply_reference_budget(eligible, *, shot, scene_reference_mode,
                            dropped=None, human_reference_mode=None):
    """确定性预算层：temporal 过滤后、cap/媒体解析前执行。
    返回 (kept, resolved_scene_mode)。所有裁剪逐条记 dropped，绝不静默。"""
    dropped = dropped if dropped is not None else []
    resolved = resolve_scene_reference_mode(scene_reference_mode, shot)
    resolved_human = resolve_human_reference_mode(human_reference_mode, shot,
                                                  resolved)
    kept: list = []
    seen_look_char: set = set()
    for it in eligible:
        st = str(it.get("source_type") or "")
        char = it.get("subject_character") or None
        if st == "scene_instance_view" and resolved == "context_only":
            dropped.append({"source_id": str(it.get("source_id")),
                            "reference_asset_id": str(it.get("reference_asset_id") or ""),
                            "reason": "scene_reference_context_only",
                            "detail": {"scene_reference_mode": resolved}})
            continue
        if st == "character_identity":
            dropped.append({"source_id": str(it.get("source_id")),
                            "reference_asset_id": str(it.get("reference_asset_id") or ""),
                            "reason": "human_reference_budget",
                            "detail": {"subject_character": char,
                                       "hint": "legacy 身份视图不进 provider（每角色 1 张 "
                                               "approved look 主参考）"}})
            continue
        if st == "character_look":
            if resolved_human == "drop":
                dropped.append({"source_id": str(it.get("source_id")),
                                "reference_asset_id": str(it.get("reference_asset_id") or ""),
                                "reason": "human_reference_drop",
                                "detail": {"subject_character": char,
                                           "hint": "紧景别无参考模式：identity 由 prompt "
                                                   "文本承载（D2 §5.2 实验证据）"}})
                continue
            if char in seen_look_char:
                dropped.append({"source_id": str(it.get("source_id")),
                                "reference_asset_id": str(it.get("reference_asset_id") or ""),
                                "reason": "human_reference_budget",
                                "detail": {"subject_character": char,
                                           "hint": "每角色仅 1 张人形参考（look 主参考已选）"}})
                continue
            seen_look_char.add(char)
            if resolved_human == "crop":
                # 31.md C4：crop 档 = 保留的 look 主参考改为头肩裁剪派生（媒体解析层
                # 消费 derived_crop；派生失败回落源媒体并记 diagnostic）。
                it = dict(it)
                it["derived_crop"] = resolve_identity_crop_method(shot)
        kept.append(it)
    return kept, resolved


def _cap_provider_input_images(eligible, *, reserved_slots=0, dropped=None):
    """确定性选择 + 反克隆策略（Phase D §35 真机修订）。

    Golden Run 抽检实证：多人物镜头把同角色多视角（identity front/side/back +
    look extras）全部送入 provider 时，Agnes 把每张人形参考渲染成画面里的一个
    人物——克隆人 / 幻觉路人。修订选择策略：
    - 场景参考（scene_instance_view）与 composition plate 恒保；
    - 每个出场角色只保 1 张 character_look 主参考（第一个）；
    - 单角色镜头：允许 identity 多视角 / look extras 按原序填充剩余槽位
      （增强身份一致性；单角色无克隆歧义，C2 §33 实证）；
    - ≥2 角色：不填充任何 identity 多视角 / 同角色 extras——宁缺勿滥。
    被裁剪项逐条记 dropped（reason=provider_input_image_cap），绝不静默。"""
    dropped = dropped if dropped is not None else []
    cap = max(0, MAX_PROVIDER_INPUT_IMAGES - int(reserved_slots))
    if len(eligible) <= cap:
        return eligible

    def _char_of(it):
        return it.get("subject_character") or None

    chars = []
    for it in eligible:
        c = _char_of(it)
        if c and c not in chars:
            chars.append(c)

    keep_idx = set()
    # 1) 场景与 composition 参考（非角色 subject）
    for i, it in enumerate(eligible):
        if _char_of(it) is None and str(it.get("source_type") or "") in (
                "scene_instance_view", "composition"):
            keep_idx.add(i)
    # 2) 每角色第一张 character_look
    for c in chars:
        for i, it in enumerate(eligible):
            if _char_of(it) == c and str(it.get("source_type") or "") == "character_look":
                keep_idx.add(i)
                break
    # 3) 填充：仅单角色镜头允许（identity 多视角 / extras 按原序）
    if len(chars) <= 1:
        for i, _it in enumerate(eligible):
            if len(keep_idx) >= cap:
                break
            if i not in keep_idx:
                keep_idx.add(i)
    # 截断到 cap（保持原序优先级）
    keep_idx = set(sorted(keep_idx)[:cap])
    for i, it in enumerate(eligible):
        if i not in keep_idx:
            dropped.append({"source_id": str(it.get("source_id")),
                            "reference_asset_id": str(it.get("reference_asset_id") or ""),
                            "reason": "provider_input_image_cap",
                            "detail": {"cap": cap, "eligible": len(eligible),
                                       "distinct_characters": len(chars)}})
    return [it for i, it in enumerate(eligible) if i in keep_idx]


def _resolve_media_loop(eligible: List[dict], rdoc: Any, project_dir: Path
                        ) -> Tuple[List[str], List[str], List[Dict[str, Any]],
                                   List[Dict[str, Any]], List[Dict[str, Any]]]:
    """媒体解析（§23；required primary 失败=结构化错误，optional 降级+diagnostic）。
    返回 (image_refs, used_ids, dropped, diagnostics, roles)。"""
    image_refs: List[str] = []
    used_ids: List[str] = []
    dropped: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    roles: List[Dict[str, Any]] = []
    for item in eligible:
        media = None
        derivation_note: Optional[Dict[str, Any]] = None
        crop_method = item.get("derived_crop")
        if crop_method:
            # 31.md C4：identity crop 派生媒体（失败 → 回落源媒体 + diagnostic，
            # 绝不 silent）。派生记录进 roles → provenance → ReferenceInput。
            asset = rdoc.reference_assets.get(item.get("reference_asset_id")) \
                if item.get("reference_asset_id") else None
            if asset is not None:
                from src.generation.identity_crop import resolve_identity_crop_media
                derived = resolve_identity_crop_media(
                    project_dir, url=asset.url, local=asset.local,
                    method=str(crop_method))
                if derived is not None:
                    media = derived["media"]
                    derivation_note = dict(derived["derivation"])
                else:
                    diagnostics.append({
                        "reason": "identity_crop_derivation_failed",
                        "detail": {"reference_asset_id": item.get("reference_asset_id"),
                                   "method": str(crop_method),
                                   "hint": "裁剪派生失败——回落源参考媒体（绝不 silent）"}})
        if media is None:
            media = _resolve_item_media(item, rdoc, project_dir)
        if media is None:
            if item.get("priority") == "primary":
                raise BoundGenerationError(
                    "reference_media_unresolvable",
                    {"reference_asset_id": item.get("reference_asset_id"),
                     "source_id": item.get("source_id"),
                     "source_type": item.get("source_type"),
                     "hint": "required primary 参考媒体无法解析——provider 未调用"})
            dropped.append({"reference_asset_id": item.get("reference_asset_id"),
                            "source_id": item.get("source_id"),
                            "reason": "media_unresolvable_optional"})
            continue
        image_refs.append(media)
        used_ids.append(str(item.get("reference_asset_id") or item.get("source_id") or ""))
        # checksum（31.md §12：能记录则记录）——ReferenceAsset 内容指纹（可空，legacy 项无）
        asset = rdoc.reference_assets.get(item.get("reference_asset_id")) \
            if item.get("reference_asset_id") else None
        roles.append({"reference_asset_id": item.get("reference_asset_id"),
                      "source_type": item.get("source_type"),
                      "purpose": item.get("purpose"),
                      "priority": item.get("priority"),
                      "temporal_scope": item.get("temporal_scope"),
                      "subject_character": item.get("subject_character"),
                      "prop_id": item.get("prop_id"),
                      "checksum": getattr(asset, "checksum", None),
                      "derivation": derivation_note})
    return image_refs, used_ids, dropped, diagnostics, roles


# ============ 主入口 ============

def _population_constraint_clause(shot: Any) -> str:
    """31.md C5（G4 杠杆 2，确定性、参数门控）：人数排除语义——针对 D2 残余的
    「偶发幻觉人群」（含 s09_c01 EWS 单人镜渲染 9 人案例）。人数 = shot.characters
    确定性计数；不解释剧情（场景是否该有路人属创作意图，由提交方决定是否启用）。
    只进 BOUND prompt tail；默认关（证据门控，与 crop 同纪律）。"""
    names = [str(c or "").strip() for c in (getattr(shot, "characters", None) or [])
             if str(c or "").strip()]
    n = len(names)
    if n <= 0:
        return ""
    if n == 1:
        return ("Population constraint: exactly one person appears in this frame; "
                "no bystanders, no background crowd, no extra unnamed people.")
    return (f"Population constraint: exactly {n} people appear in this frame — the "
            f"characters described above; no other people, no background crowd.")


def apply_population_clause(prompt: str, shot: Any, *, enabled: bool) -> str:
    """Phase51 B：把确定性人数排除子句追加到生成 prompt tail（首帧/视频层对称）。
    enabled=False 或镜头无角色（子句为空）→ 原样返回（不做无意义拼接）。
    子句语义见 _population_constraint_clause（31.md C5；E2 对照实验实证宽景缺员
    由该子句改善：无条款 0/2 vs 有条款 2/2）。"""
    if not enabled:
        return prompt
    clause = _population_constraint_clause(shot)
    if not clause:
        return prompt
    return (prompt or "").rstrip() + " " + clause


def build_bound_first_frame_input(project_dir, project: Any, shot: Any, *,
                                  scene_reference_mode: Optional[str] = None,
                                  human_reference_mode: Optional[str] = None,
                                  population_constraint: bool = False
                                  ) -> BoundImageRequest:
    """BOUND shot 的首帧生成输入（§4.A/C；§27：BOUND 永不回落 legacy）。
    顺序：validity gate → compile pack（自带 pre/post 守卫）→ temporal 过滤（先于
    priority）→ reference budget（D2：scene mode + 人形预算）→ cap 护栏 → 媒体解析 →
    prompt 组装 → pre-dispatch guard。
    population_constraint（31.md C5）：True → prompt tail 追加确定性人数排除子句。"""
    from src.shot_binding import ShotBindingService

    project_dir = Path(project_dir)
    sid = str(shot.shot_id)
    svc = ShotBindingService(project_dir, project_dir.name)
    pack = _gate_ready_pack(svc, sid)

    # ---- Step 1 temporal eligibility（先于 priority，§8/§13）----
    eligible, excluded_end = filter_first_frame_references(pack.get("references") or [])
    _cap_dropped: list = []
    resolved_human_mode: str = DEFAULT_HUMAN_REFERENCE_MODE
    # ---- Step 1b reference budget（Phase D2：scene mode + 每角色 1 张人形）----
    eligible, resolved_scene_mode = _apply_reference_budget(
        eligible, shot=shot, scene_reference_mode=scene_reference_mode,
        dropped=_cap_dropped, human_reference_mode=human_reference_mode)
    resolved_human_mode = resolve_human_reference_mode(
        human_reference_mode, shot, resolved_scene_mode)
    eligible = _cap_provider_input_images(eligible, reserved_slots=0,
                                          dropped=_cap_dropped)

    # ---- Step 2 媒体解析（§23）----
    from src.resources.store import ResourceStore
    rdoc = ResourceStore(project_dir, project_dir.name).load()
    image_refs, used_ids, dropped, diagnostics, roles = \
        _resolve_media_loop(eligible, rdoc, project_dir)
    dropped.extend(_cap_dropped)   # Phase D: provider input cap drops
    if excluded_end:
        diagnostics.append({
            "reason": "end_state_reference_excluded_from_first_frame",
            "detail": {"count": len(excluded_end),
                       "source_ids": [str(x.get("source_id")) for x in excluded_end]}})
    dropped.extend({"source_id": str(x.get("source_id")),
                    "reason": "temporal_end_excluded"} for x in excluded_end)

    # ---- continuity（Shot creative relay，Phase 6A 既有机制；append 尾部）----
    continuity_clause = ""
    continuity_media_used = False
    continuity_ref: Optional[Dict[str, Any]] = None
    from src.core.models import OpeningSource
    if shot.opening_source == OpeningSource.REFERENCE_PREVIOUS.value and shot.source_shot_id:
        prev = project.get_shot(shot.source_shot_id)
        end = prev.video_end_frame if prev else None
        media = None
        if end is not None:
            from src.resources.generation import to_agnes_image_ref
            media = to_agnes_image_ref(project_dir, url=end.url, local=end.local)
        if media:
            image_refs.append(media)
            continuity_media_used = True
            # Phase 3：持久 provenance——这条尾帧不在 pack roles 里，此前只留
            # ref_layout 标志；现在把来源镜头/来源视频版本/不可变 URL 记全。
            continuity_ref = {
                "source_shot_id": shot.source_shot_id,
                "end_source_version": getattr(prev, "video_end_source_version", None)
                if prev is not None else None,
                "url": (getattr(end, "url", None) or None) if end is not None else None,
                "media_is_data_uri": bool(str(media).startswith("data:"))}
            continuity_clause = (
                ". Maintain continuity with the previous shot's ending frame: keep "
                "character identity, environment, outfit and lighting direction; "
                "change shot size, camera angle, composition and subject action as "
                "specified; do not pixel-copy the previous frame")

    # ---- prompt（§20/§21：creative builder 复用 + Pack 生产上下文 + P1 风格指令）----
    from src.core.prompt_builder import build_first_frame_prompt
    creative = build_first_frame_prompt(project, shot, style_suffix="")
    ctx = _production_context_text(pack, rdoc, "first")
    style = _style_instruction_from_pack(pack)
    population = _population_constraint_clause(shot) if population_constraint else ""
    tail = " ".join(x for x in (ctx, style, population) if x)
    prompt = build_first_frame_prompt(project, shot, style_suffix=tail) if tail \
        else creative
    prompt = prompt + continuity_clause

    # ---- pre-dispatch race guard（§25：compile 之后的第二个时间窗）----
    basis = pack.get("basis") or {}
    _pre_dispatch_guard(svc, sid, basis)

    return BoundImageRequest(
        shot_id=sid, prompt=prompt, image_refs=image_refs,
        used_reference_ids=used_ids, dropped_optional=dropped,
        diagnostics=diagnostics, semantic_roles=roles, basis=basis,
        scene_reference_mode=resolved_scene_mode,
        human_reference_mode=resolved_human_mode,
        ref_layout="continuity_tail" if continuity_media_used else "pack_only",
        continuity_ref=continuity_ref,
        population_constraint=bool(population_constraint))


def build_bound_last_frame_input(project_dir, project: Any, shot: Any, *,
                                 first_frame_url: str,
                                 scene_reference_mode: Optional[str] = None,
                                 human_reference_mode: Optional[str] = None
                                 ) -> BoundImageRequest:
    """BOUND shot 的尾帧生成输入（S3C.2 §3.A）。last-frame temporal rule（§6）：
    end/throughout/static 进、**start 排除且先于 priority**。
    refs = [已选首帧 URL（构图基础，creative 机制，保持第一位——提示词.md §11）]
         + Pack last-eligible 参考。
    🔴 构图基础首帧必须 fresh（§17）：stale 首帧派生的尾帧继承 stale——否则本尾帧
    落盘的 generation_basis 就失去意义（传递新鲜度）。inherit_previous 的构图基础
    = 来源镜头视频末帧（creative relay 物料，同 S3C.1 continuity 口径）→ diagnostic
    不阻断。"""
    from src.shot_binding import ShotBindingService
    from src.core.models import OpeningSource

    project_dir = Path(project_dir)
    sid = str(shot.shot_id)
    svc = ShotBindingService(project_dir, project_dir.name)
    pack = _gate_ready_pack(svc, sid)
    basis = pack.get("basis") or {}
    diagnostics: List[Dict[str, Any]] = []

    # ---- 构图基础首帧 freshness（§17；relay 物料除外）----
    relay_base = (shot.opening_source == OpeningSource.INHERIT_PREVIOUS.value
                  and shot.source_shot_id)
    if relay_base:
        diagnostics.append({
            "reason": "boundary_relay_no_binding_basis",
            "detail": {"slot": "first",
                       "hint": "尾帧构图基础=来源镜头视频末帧（inherit_previous relay "
                               "物料，非本镜 Binding 产物）——freshness 由既有接力机制管"}})
    else:
        fbatch, _fcand = selected_boundary_batch(shot, "first")
        if fbatch is None:
            raise BoundGenerationError(
                "reference_media_unresolvable",
                {"boundary_slot": "first",
                 "hint": "尾帧构图基础=已选首帧，但 selected_first_frame 无法解析到批次"})
        detail = boundary_stale_detail(fbatch, basis)
        if detail:
            raise BoundGenerationError("boundary_frame_stale", detail)

    # ---- temporal eligibility：end/throughout/static 进、start 排除（先于 priority）----
    eligible, excluded_start = filter_frame_references(
        pack.get("references") or [], "last")

    # ---- 媒体解析 ----
    from src.resources.store import ResourceStore
    rdoc = ResourceStore(project_dir, project_dir.name).load()
    _cap_dropped2: list = []
    # ---- reference budget（Phase D2：scene mode + 每角色 1 张人形）----
    eligible, resolved_scene_mode = _apply_reference_budget(
        eligible, shot=shot, scene_reference_mode=scene_reference_mode,
        dropped=_cap_dropped2, human_reference_mode=human_reference_mode)
    eligible = _cap_provider_input_images(
        eligible, reserved_slots=1, dropped=_cap_dropped2)   # Phase D: +1 = 首帧构图基础
    refs, used_ids, dropped, diag2, roles = _resolve_media_loop(eligible, rdoc, project_dir)
    diagnostics.extend(diag2)
    dropped.extend(_cap_dropped2)
    if excluded_start:
        diagnostics.append({
            "reason": "start_state_reference_excluded_from_last_frame",
            "detail": {"count": len(excluded_start),
                       "source_ids": [str(x.get("source_id")) for x in excluded_start]}})
    dropped.extend({"source_id": str(x.get("source_id")),
                    "reason": "temporal_start_excluded"} for x in excluded_start)
    # 首帧（构图基础）始终第一张，不让参考图取代主构图参考（提示词.md §11 语义）
    image_refs = [first_frame_url] + refs

    # ---- prompt（creative builder 复用 + Pack 生产上下文 last 口径 + P1 风格指令）----
    from src.core.prompt_builder import build_last_frame_prompt
    creative = build_last_frame_prompt(project, shot, style_suffix="")
    ctx = _production_context_text(pack, rdoc, "last")
    style = _style_instruction_from_pack(pack)
    tail = " ".join(x for x in (ctx, style) if x)
    prompt = build_last_frame_prompt(project, shot, style_suffix=tail) if tail \
        else creative

    # ---- pre-dispatch race guard（§25）----
    _pre_dispatch_guard(svc, sid, basis)

    return BoundImageRequest(
        shot_id=sid, prompt=prompt, image_refs=image_refs,
        used_reference_ids=used_ids, dropped_optional=dropped,
        diagnostics=diagnostics, semantic_roles=roles, basis=basis,
        scene_reference_mode=resolved_scene_mode,
        human_reference_mode=resolve_human_reference_mode(
            human_reference_mode, shot, resolved_scene_mode),
        ref_layout="first_frame_head")
