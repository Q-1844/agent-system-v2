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


def _cap_provider_input_images(eligible, *, reserved_slots=0, dropped=None):
    dropped = dropped if dropped is not None else []
    cap = max(0, MAX_PROVIDER_INPUT_IMAGES - int(reserved_slots))
    if len(eligible) <= cap:
        return eligible
    keep_idx = set()
    primaries = [i for i, it in enumerate(eligible)
                 if str(it.get("priority") or "") == "primary"]
    for i in primaries[:cap]:
        keep_idx.add(i)
    for i, _it in enumerate(eligible):
        if len(keep_idx) >= cap:
            break
        if i not in keep_idx:
            keep_idx.add(i)
    for i, it in enumerate(eligible):
        if i not in keep_idx:
            dropped.append({"source_id": str(it.get("source_id")),
                            "reference_asset_id": str(it.get("reference_asset_id") or ""),
                            "reason": "provider_input_image_cap",
                            "detail": {"cap": cap, "eligible": len(eligible)}})
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
    return image_refs, used_ids, dropped, diagnostics, roles


# ============ 主入口 ============

def build_bound_first_frame_input(project_dir, project: Any, shot: Any) -> BoundImageRequest:
    """BOUND shot 的首帧生成输入（§4.A/C；§27：BOUND 永不回落 legacy）。
    顺序：validity gate → compile pack（自带 pre/post 守卫）→ temporal 过滤（先于
    priority）→ 媒体解析 → prompt 组装 → pre-dispatch guard。"""
    from src.shot_binding import ShotBindingService

    project_dir = Path(project_dir)
    sid = str(shot.shot_id)
    svc = ShotBindingService(project_dir, project_dir.name)
    pack = _gate_ready_pack(svc, sid)

    # ---- Step 1 temporal eligibility（先于 priority，§8/§13）----
    eligible, excluded_end = filter_first_frame_references(pack.get("references") or [])
    _cap_dropped: list = []
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
    ctx = _production_context_text(pack, rdoc, "first")
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


def build_bound_last_frame_input(project_dir, project: Any, shot: Any, *,
                                 first_frame_url: str) -> BoundImageRequest:
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
        diagnostics=diagnostics, semantic_roles=roles, basis=basis)
