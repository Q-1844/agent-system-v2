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
        }


def shot_has_binding(project_dir, shot_id: str) -> bool:
    """BOUND / UNBOUND 判定（§27 双路径开关）。
    bindings.json 损坏 → ShotBindingSchemaError 显式异常（§39：损坏不静默当 UNBOUND）。"""
    from src.shot_binding.store import ShotBindingStore
    doc = ShotBindingStore(project_dir).load()
    return str(shot_id or "").strip() in doc.bindings


# ============ temporal 过滤（§8/§13：先于 priority）============

def filter_first_frame_references(references: List[dict]) -> Tuple[List[dict], List[dict]]:
    """Step 1 temporal eligibility：start/throughout/static 保留；end 排除。
    返回 (eligible, excluded_end)。excluded_end 仅供 diagnostics——绝不因 primary
    而回流（§8 硬规则）。单次遍历保持 Pack 原确定性顺序。"""
    eligible: List[dict] = []
    excluded: List[dict] = []
    for r in references:
        if r.get("temporal_scope") in _FIRST_FRAME_ELIGIBLE_TEMPORAL:
            eligible.append(r)
        else:
            excluded.append(r)
    return eligible, excluded


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


# ============ 生产上下文文本（§21：确定性、无 LLM；§22：start 态 only）============

def _production_context_text(pack: dict, rdoc: Any) -> str:
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
            parts.append(f"{ch.get('character_name')} {cp.get('relation')} "
                         f"{pname} ({cp.get('anchor')}).")
    action = pack.get("action") or {}
    # 🔴 §22：只输出 start_state；end_state 绝不进入首帧当前态约束。
    if action.get("kind") not in (None, "", "none") and action.get("start_state"):
        parts.append(f"Action start state: {action['start_state']}.")
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


# ============ 主入口 ============

def build_bound_first_frame_input(project_dir, project: Any, shot: Any) -> BoundImageRequest:
    """BOUND shot 的首帧生成输入（§4.A/C；§27：BOUND 永不回落 legacy）。
    顺序：validity gate → compile pack（自带 pre/post 守卫）→ temporal 过滤（先于
    priority）→ 媒体解析 → prompt 组装 → pre-dispatch guard。"""
    from src.shot_binding import ShotBindingService
    from src.shot_binding.validity import READY

    project_dir = Path(project_dir)
    sid = str(shot.shot_id)
    svc = ShotBindingService(project_dir, project_dir.name)

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
    pack = compiled["pack"]

    # ---- Step 1 temporal eligibility（先于 priority，§8/§13）----
    eligible, excluded_end = filter_first_frame_references(pack.get("references") or [])

    # ---- Step 2 媒体解析（§23；required primary 失败=结构化错误，optional 降级+diagnostic）----
    from src.resources.store import ResourceStore
    rdoc = ResourceStore(project_dir, project_dir.name).load()
    image_refs: List[str] = []
    used_ids: List[str] = []
    dropped: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    roles: List[Dict[str, Any]] = []
    for item in eligible:
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
        roles.append({"reference_asset_id": item.get("reference_asset_id"),
                      "source_type": item.get("source_type"),
                      "purpose": item.get("purpose"),
                      "priority": item.get("priority"),
                      "temporal_scope": item.get("temporal_scope"),
                      "subject_character": item.get("subject_character"),
                      "prop_id": item.get("prop_id")})
    if excluded_end:
        diagnostics.append({
            "reason": "end_state_reference_excluded_from_first_frame",
            "detail": {"count": len(excluded_end),
                       "source_ids": [str(x.get("source_id")) for x in excluded_end]}})
    dropped.extend({"source_id": str(x.get("source_id")),
                    "reason": "temporal_end_excluded"} for x in excluded_end)

    # ---- continuity（Shot creative relay，Phase 6A 既有机制；append 尾部）----
    continuity_clause = ""
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
            continuity_clause = (
                ". Maintain continuity with the previous shot's ending frame: keep "
                "character identity, environment, outfit and lighting direction; "
                "change shot size, camera angle, composition and subject action as "
                "specified; do not pixel-copy the previous frame")

    # ---- prompt（§20/§21：creative builder 复用 + Pack 生产上下文 + P1 风格指令）----
    from src.core.prompt_builder import build_first_frame_prompt
    creative = build_first_frame_prompt(project, shot, style_suffix="")
    ctx = _production_context_text(pack, rdoc)
    style = _style_instruction_from_pack(pack)
    tail = " ".join(x for x in (ctx, style) if x)
    prompt = build_first_frame_prompt(project, shot, style_suffix=tail) if tail \
        else creative
    prompt = prompt + continuity_clause

    # ---- pre-dispatch race guard（§25：compile 之后的第二个时间窗）----
    basis = pack.get("basis") or {}
    _pre_dispatch_guard(svc, sid, basis)

    return BoundImageRequest(
        shot_id=sid, prompt=prompt, image_refs=image_refs,
        used_reference_ids=used_ids, dropped_optional=dropped,
        diagnostics=diagnostics, semantic_roles=roles, basis=basis)
