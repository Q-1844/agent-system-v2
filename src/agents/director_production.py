# src/agents/director_production.py
# Agent System V2 Phase C1（27.md §5-§10/§13/§15）：Director 生产感知与绑定工具。
#
# 职责边界（27.md §1 冻结裁决）：
#   - Director 是 Shot owner ⇒ ShotProductionBinding 的生产意图 ownership 归 Director
#     （经正式 ShotBindingService，绝不直写 JSON）；
#   - Director 【不】创建/审批任何 Resource、不写 Scene System、不写 ProjectVisualProfile
#     ——缺前置时返回结构化 prerequisite，由 Producer 协调（§1.2/§1.3/§15）；
#   - readiness/staleness 唯一权威 = ShotBindingService.read_validity（本层零重实现）；
#   - 跨模块只读经 src.agents.production_context（共享只读层，避免 executor 互相 import）。
#
# 回执纪律：inspect 是只读工具（不进 receipts）；bind/refresh 是写工具（进 receipts/
# 单写串行/storyboard 锁域）；prerequisite 以结构化 `prerequisite_warnings` 随真实
# tool result 返回（Producer 据此派生 TaskResult.warnings，不信模型自述——§15）。
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .production_context import (approved_looks_for_characters,
                                 approved_props, binding_projection,
                                 character_identity_reference_state,
                                 read_visual_profile_projection,
                                 scene_production_projection)
from .runtime import AgentError
from .script_writer import _base_receipt, _loose_json, _parse_args


# ============ project.visual_profile.read（§5，与 Resource Agent 同 schema）============
def _execute_visual_profile_read(rt, project: str, arguments: str,
                                 instruction, message_id) -> dict:
    """Director 读项目视觉宪法（只读；无 mutation 工具）。复用共享层投影。"""
    pdir = rt._resolve_project_dir(project)
    return read_visual_profile_projection(pdir)


# ============ cast consistency（§7.1：deterministic，不做 LLM judge）============
_CAST_TEXT_FIELDS = ("description", "dialogue", "prompt_en", "first_frame_prompt_override",
                     "action_sequence", "important_details", "opening_state", "motion")


def cast_consistency(proj, shot) -> dict:
    """已建立 Character canonical 的名字明确出现在镜头文本字段、但不在 shot.characters
    → 结构化 warning。只做确定性名字匹配，绝不猜「黑衣人可能是谁」（§7.1）。"""
    declared = list(shot.characters or [])
    known = [c.name for c in (proj.characters or [])]
    texts: List[str] = []
    for f in _CAST_TEXT_FIELDS:
        v = getattr(shot, f, None)
        if isinstance(v, str) and v.strip():
            texts.append(v)
        elif isinstance(v, list):
            texts.extend(str(x) for x in v if x)
    blob = "\n".join(texts)
    mentioned = []
    for name in known:
        if name in declared:
            continue
        if len(name) >= 2 and name in blob:
            mentioned.append(name)
    missing = sorted(set(mentioned))
    return {"status": "inconsistent" if missing else "ok",
            "declared_cast": declared,
            "mentioned_known_characters": missing,
            "missing_from_cast": missing,
            "warnings": ([f"镜头文本明确提到已建立角色 {n}，但 shot.characters 未列——"
                          f"binding 只会为 declared cast 取参考，该角色会被模型即兴/克隆"
                          f"（Golden Run s06 缺陷）。请用 shot.update 补全 cast。"
                          for n in missing]
                         if missing else [])}


# ============ shot.production.inspect（§6）============
def _execute_shot_production_inspect(rt, project: str, arguments: str,
                                     instruction, message_id) -> dict:
    """一个工具返回当前 Shot 的完整生产准备摘要（§6）：Shot 信息 + VisualProfile +
    cast 相关 approved Looks + Prop 线索 + Scene 生产上下文 + Binding 状态 + cast
    一致性。复用 ShotBindingService validity（§6.6），零 readiness 重实现。只读。"""
    args = _parse_args(arguments)
    shot_id = (args.get("shot_id") or "").strip()
    if not shot_id:
        raise AgentError("bad_arguments", "缺少 shot_id")
    pdir = rt._resolve_project_dir(project)
    from src.core.store import ProjectStore
    from src.agents.director import _shot_token   # lazy（director 模块级 import 本模块，
    #                                              反向模块级 import 会成环）
    proj = ProjectStore(pdir).load()
    shot = proj.get_shot(shot_id)
    if shot is None:
        raise AgentError("not_found", f"镜头不存在: {shot_id}")

    from .production_context import profile_summary_line  # noqa: F401
    profile = read_visual_profile_projection(pdir)

    # ---- §6.3 cast 相关资源选项 ----
    declared = list(shot.characters or [])
    identity_states = [character_identity_reference_state(pdir, n) for n in declared]
    looks = approved_looks_for_characters(pdir, declared)
    looks_by_char: Dict[str, list] = {}
    for lk in looks:
        looks_by_char.setdefault(lk["character_name"], []).append(lk)
    character_options = []
    for n in declared:
        ident = next((i for i in identity_states if i["character_name"] == n), None)
        approved = looks_by_char.get(n, [])
        character_options.append({
            "character_name": n,
            "identity_reference": (ident or {}).get("identity_reference", "unknown"),
            "approved_looks": approved,
        })

    # ---- §6.4 Prop（无开放式 NLP 猜测）----
    from src.shot_binding.service import ShotBindingService
    svc = ShotBindingService(pdir, project)
    current_binding = svc.read_binding(shot_id)
    b = (current_binding or {}).get("binding")
    if b is not None and b.get("prop_bindings"):
        props_section: Dict[str, Any] = {
            "source": "current_binding",
            "options": approved_props(pdir),
            "current_binding_props": b.get("prop_bindings"),
        }
    else:
        props_section = {"source": "none", "status": "prop_requirement_unknown",
                         "note": "Shot schema 与当前 binding 无法可靠关联道具需求——"
                                 "不猜测。若该镜确实需要道具，请先准备并批准道具参考，"
                                 "再在 bind 时给出 prop_bindings。"}

    # ---- §6.5 Scene 生产上下文 ----
    scene = scene_production_projection(pdir, shot.scene_id)

    # ---- §6.6 Binding（唯一 readiness 权威）----
    bind_proj = binding_projection(pdir, project, shot_id)
    validity = bind_proj.get("validity") or {}

    # ---- §7.1 cast 一致性 ----
    cast = cast_consistency(proj, shot)

    # ---- §15 结构化 prerequisite（真实 tool result 派生，Producer 消费）----
    prerequisite_warnings: List[dict] = []
    if not profile.get("specified"):
        prerequisite_warnings.append({
            "code": "visual_profile_unspecified", "shot_id": shot_id,
            "detail": "项目未设置 ProjectVisualProfile——Look/Sciv/BOUND 生成无法进行风格锚定。",
            "required_capability": "user_configure_profile"})
    for n in declared:
        if not looks_by_char.get(n):
            prerequisite_warnings.append({
                "code": "resource_prerequisite", "shot_id": shot_id,
                "detail": f"角色 {n} 没有 approved Look（production binding 需要已批准造型）。",
                "required_capability": "resource_agent"})
    if scene.get("reason") == "scene_instance_missing":
        prerequisite_warnings.append({
            "code": "scene_production_prerequisite", "shot_id": shot_id,
            "detail": f"故事段 {shot.scene_id} 缺少 SceneInstance。",
            "required_capability": "scene_preparation"})
    elif scene.get("reason") == "scene_view_missing":
        prerequisite_warnings.append({
            "code": "scene_production_prerequisite", "shot_id": shot_id,
            "detail": f"故事段 {shot.scene_id} 的 SceneInstance 没有 approved SceneInstanceView。",
            "required_capability": "scene_preparation"})
    if cast["status"] == "inconsistent":
        prerequisite_warnings.append({
            "code": "cast_incomplete", "shot_id": shot_id,
            "detail": f"cast 缺失：{cast['missing_from_cast']}（Director 可用 shot.update 自修）。",
            "required_capability": "director_self"})

    return _base_receipt(
        "shot.production.inspect", ok=True, target=shot_id,
        before=None, after=None, revision=None, verified=True,
        shot_id=shot_id, scene_id=shot.scene_id,
        shot={"description": (shot.description or "")[:200],
              "characters": declared,
              "dialogue": (shot.dialogue or "")[:200],
              "action_sequence": (getattr(shot, "action_sequence", "") or "")[:160],
              "shot_size": getattr(shot, "shot_size", "") or "",
              "camera_angle": getattr(shot, "camera_angle", "") or "",
              "camera_movement": getattr(shot, "camera_movement", "") or "",
              "link_mode": getattr(shot, "link_mode", "") or "",
              "has_first_frame": bool(getattr(shot, "first_frame_batches", None)),
              },
        shot_revision=_shot_token(shot),
        shots_revision=None,   # 批量域指纹按需经 shot.list 获取（避免整表哈希常驻）
        visual_profile={"specified": profile.get("specified"),
                        "render_family": (profile.get("visual_profile") or {}).get(
                            "render_family") if profile.get("specified") else None,
                        "fingerprint": profile.get("fingerprint", "")},
        character_options=character_options,
        props=props_section,
        scene=scene,
        binding={"state": validity.get("binding_state"),
                 "readiness": validity.get("readiness"),
                 "binding_revision": validity.get("binding_revision"),
                 "document_revision": validity.get("document_revision"),
                 "character_bindings": (b or {}).get("character_bindings") or [],
                 "prop_bindings": (b or {}).get("prop_bindings") or [],
                 "scene_binding": (b or {}).get("scene_binding"),
                 "issues": validity.get("issues") or [],
                 "resolved_dependencies": validity.get("resolved_dependencies") or {}},
        cast_consistency=cast,
        prerequisite_warnings=prerequisite_warnings,
        note=("生产准备摘要已生成（只读）。先解决 prerequisite_warnings / cast_incomplete "
              "里 Director 自身可修的问题（shot.update 补 cast），缺 Resource/Scene 前置"
              "时如实报告（不要自己创建），都就绪后再 shot.production.bind。"))


# ============ shot.production.bind（§8）============
async def _execute_shot_production_bind(rt, project: str, arguments: str,
                                        instruction, message_id) -> dict:
    """高层 binding intent 工具（§8）：UNBOUND → create；已有 binding → replace（需
    expected_binding_revision，来自 shot.production.inspect）。cast 等集 / Look / Sciv /
    profile 校验全部复用 ShotBindingService 既有 fail-closed，零弱化。"""
    args = _parse_args(arguments)
    shot_id = (args.get("shot_id") or "").strip()
    char_bindings = _loose_json(args.get("character_bindings"))
    if not shot_id:
        raise AgentError("bad_arguments", "缺少 shot_id")
    if not isinstance(char_bindings, list):
        raise AgentError("bad_arguments",
                         "character_bindings 必须是数组"
                         "（[{character_name, look_id}, ...]；纯环境镜允许空数组"
                         "——scene-only binding，34.md §5 最小修复）")
    pdir = rt._resolve_project_dir(project)
    from src.core.store import ProjectStore
    from src.shot_binding.service import ShotBindingService
    proj = ProjectStore(pdir).load()
    shot = proj.get_shot(shot_id)
    if shot is None:
        raise AgentError("not_found", f"镜头不存在: {shot_id}")
    svc = ShotBindingService(pdir, project)
    existing = svc.read_binding(shot_id)

    payload: Dict[str, Any] = {
        "shot_id": shot_id,
        "scene_id": shot.scene_id,   # Director 不传 scene_id：以 canonical Shot 为准（防错绑）
        "character_bindings": char_bindings,
        "prop_bindings": _loose_json(args.get("prop_bindings")) or [],
        "scene_binding": _loose_json(args.get("scene_binding")),
        "action": _loose_json(args.get("action")),
        "production_notes": str(args.get("production_notes") or ""),
    }
    if existing is None:
        r = await svc.create_binding_async(payload)   # UNBOUND → create
        mode = "create"
    else:
        expected = (args.get("expected_binding_revision") or "").strip()
        if not expected:
            return _base_receipt(
                "shot.production.bind", ok=False, target=shot_id,
                before=None, after=None,
                revision=existing.get("binding_revision"), verified=False,
                error="expected_binding_revision_required", stale=False,
                binding_id=shot_id, changed_resources=[],
                op_summary="替换绑定被拒（缺少 revision）",
                note="该 Shot 已有 binding（replace 语义必须显式 revision）。请先 "
                     "shot.production.inspect 拿 binding_revision 原样回传；确认要换绑"
                     "（改动 Intent）才 replace。")
        r = await svc.replace_binding_async(shot_id, payload,
                                            expected_binding_revision=expected)
        mode = "replace"
    if not r.get("ok"):
        return _base_receipt(
            "shot.production.bind", ok=False, target=shot_id,
            before=None, after=None, revision=r.get("current_binding_revision"),
            verified=False, error=r.get("error") or "bind_failed",
            stale=r.get("error") == "stale_binding_revision",
            binding_id=shot_id, mode=mode,
            expected_binding_revision=r.get("expected_binding_revision"),
            current_binding_revision=r.get("current_binding_revision"),
            detail=(r.get("detail") or "")[:300],
            changed_resources=[],
            op_summary=f"绑定失败（{r.get('error')}）",
            note=f"绑定未写入：{r.get('detail') or r.get('error')}。按错误码处理："
                 "stale_binding_revision → 重新 inspect 拿最新 revision；cast/look/scene"
                 " 类错误 → 先修 Shot 或报告相应 prerequisite（不要绕过校验）。")

    # 绑定成功 → 立即读 readiness（真实状态随 receipt 返回，Agent 不用再 inspect 一次）
    validity = svc.read_validity(shot_id)
    binding_dict = r.get("binding") or {}
    return _base_receipt(
        "shot.production.bind", ok=True, target=shot_id,
        before=None, after=binding_dict,
        revision=r.get("binding_revision"), verified=True,
        binding_id=shot_id, mode=mode,
        binding_revision=r.get("binding_revision"),
        document_revision=r.get("document_revision"),
        character_bindings=binding_dict.get("character_bindings") or [],
        prop_bindings=binding_dict.get("prop_bindings") or [],
        scene_binding=binding_dict.get("scene_binding"),
        readiness=validity.get("readiness"),
        production_state=validity.get("binding_state"),
        issues=validity.get("issues") or [],
        resolved_dependencies=validity.get("resolved_dependencies") or {},
        changed_resources=[{"kind": "binding", "id": shot_id}],
        prerequisite_warnings=[
            {"code": "binding_not_ready", "shot_id": shot_id,
             "detail": (i.get("detail") or i.get("reason") or "")[:200],
             "required_capability": "binding_issue"}
            for i in (validity.get("issues") or [])],
        op_summary=f"Shot {shot_id} 生产绑定已{'创建' if mode == 'create' else '替换'}"
                   f"（readiness={validity.get('readiness')}）",
        note=(f"binding 已{'创建' if mode == 'create' else '替换'}"
              f"（binding_revision={r.get('binding_revision')}）。当前 readiness="
              f"{validity.get('readiness')}"
              + ("，可按 reference_policy=bound_required 进入生成。"
                 if validity.get("readiness") == "READY"
                 else "，存在未就绪项（见 issues）——逐项解决后 shot.production.refresh 或"
                      "报告相应 prerequisite，不要直接生成。")))


# ============ shot.production.refresh（§9）============
async def _execute_shot_production_refresh(rt, project: str, arguments: str,
                                           instruction, message_id) -> dict:
    """显式 refresh（§9）：只更新 dependency fingerprint snapshot；依赖真 stale
    （Look/SIV 变更、profile 不符、approved 被撤销）→ fail closed 返回真实 issues，
    绝不魔法修复。"""
    args = _parse_args(arguments)
    shot_id = (args.get("shot_id") or "").strip()
    expected = (args.get("expected_binding_revision") or "").strip()
    if not shot_id:
        raise AgentError("bad_arguments", "缺少 shot_id")
    if not expected:
        raise AgentError("bad_arguments",
                         "缺少 expected_binding_revision（先 shot.production.inspect 拿到）")
    pdir = rt._resolve_project_dir(project)
    from src.shot_binding.service import ShotBindingService
    svc = ShotBindingService(pdir, project)
    r = await svc.refresh_binding_async(shot_id, expected_binding_revision=expected)
    if not r.get("ok"):
        return _base_receipt(
            "shot.production.refresh", ok=False, target=shot_id,
            before=None, after=None, revision=r.get("current_binding_revision"),
            verified=False, error=r.get("error") or "refresh_failed",
            stale=r.get("error") == "stale_binding_revision",
            binding_id=shot_id,
            expected_binding_revision=r.get("expected_binding_revision"),
            current_binding_revision=r.get("current_binding_revision"),
            detail=(r.get("detail") or "")[:300],
            changed_resources=[],
            op_summary=f"refresh 失败（{r.get('error')}）",
            note=f"refresh 未执行：{r.get('detail') or r.get('error')}。refresh 只接受"
                 "快照过期（stale-but-valid）；依赖本身 invalid（Look/SIV 变更、审批撤销、"
                 "profile 不符）必须先修复依赖或报告 Resource/Scene prerequisite。")
    validity = svc.read_validity(shot_id)
    return _base_receipt(
        "shot.production.refresh", ok=True, target=shot_id,
        before=None, after=None, revision=r.get("binding_revision"), verified=True,
        binding_id=shot_id, binding_revision=r.get("binding_revision"),
        document_revision=r.get("document_revision"),
        readiness=validity.get("readiness"),
        production_state=validity.get("binding_state"),
        issues=validity.get("issues") or [],
        resolved_dependencies=validity.get("resolved_dependencies") or {},
        changed_resources=[{"kind": "binding", "id": shot_id}],
        op_summary=f"binding 快照已刷新（readiness={validity.get('readiness')}）",
        note=(f"快照已刷新。当前 readiness={validity.get('readiness')}"
              + ("，可进入 bound_required 生成。" if validity.get("readiness") == "READY"
                 else "；仍有未就绪项（见 issues）——这些是依赖侧问题，refresh 不再适用，"
                      "请修复依赖或报告 prerequisite。")))
