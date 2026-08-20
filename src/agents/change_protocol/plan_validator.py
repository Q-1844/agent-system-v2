# src/agents/change_protocol/plan_validator.py
# AI-7B 确定性 ChangePlanValidator（§41-§49 + §39 DAG cycle）。
#
# 校验（每个返回 PlanValidationIssue dict {code, message, severity: error|warning}）：
#   §42 request coverage  — 无 direct item 且无 decision → PLAN_REQUEST_NOT_COVERED
#   §43 module ownership  — direct item owner 必须匹配 MODULE_OWNER；proposal_item_ids 必须来自对应模块提案
#   §44 entity existence  — update/delete 的 target 必须存在于 source project
#   §45 dependency cover  — delete_character 留下禁止引用 → PLAN_REFERENCE_CONFLICT（§32）
#   §46 conflict cover   — 未解决 hard conflict → PLAN_UNRESOLVED_CONFLICT
#   §47 user decision     — 阻塞决策未答 → PLAN_USER_DECISION_REQUIRED
#   §48 source signature  — plan 签名 ≠ session 签名 → PLAN_STALE
#   §49 lock/protected    — 触及 locked shot → LOCKED_SCOPE_REQUIRES_USER_DECISION
#   §39 DAG cycle         — PLAN_DEPENDENCY_CYCLE
from __future__ import annotations
from typing import List

from ..inquiry import MODULE_OWNER
from .models import (ChangeSession, ChangeRequest, UnifiedChangePlan,
                      ModuleProposal, ModuleProposalItem)


def _issue(code, message, severity="error"):
    return {"code": code, "message": message, "severity": severity}


def _find_cycle(steps: List[dict]) -> bool:
    """§39：DAG 环检测（小本地函数，不引入 workflow framework）。"""
    by_id = {s["step_id"]: s for s in steps}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {sid: WHITE for sid in by_id}

    def dfs(sid):
        color[sid] = GRAY
        for dep in by_id[sid].get("depends_on_step_ids", []):
            if dep not in color:
                continue
            if color[dep] == GRAY:
                return True
            if color[dep] == WHITE and dfs(dep):
                return True
        color[sid] = BLACK
        return False

    return any(color[sid] == WHITE and dfs(sid) for sid in by_id)


def validate(request: ChangeRequest, session: ChangeSession, plan: UnifiedChangePlan,
             proposals: List[ModuleProposal], project, impact_facts: List[dict],
             conflicts: List[dict], blocking_pending_decisions: int = 0) -> List[dict]:
    issues: List[dict] = []

    # §42 request coverage
    if not plan.direct_change_items and not plan.user_decision_ids:
        issues.append(_issue("PLAN_REQUEST_NOT_COVERED",
            "请求未产生任何 direct 修改项或决策——覆盖失败"))

    # proposal item index: item_id -> (module, owner)
    item_index = {}
    for p in proposals:
        for it in p.items:
            item_index[it.get("item_id", "")] = (p.module, p.owner_agent_id)

    # §43 module ownership + §44 entity existence
    scene_ids = {s.scene_id for s in project.scenes}
    char_names = {c.name for c in project.characters}
    shot_ids = {sh.shot_id for sh in project.all_shots()}
    for cpi in plan.direct_change_items:
        module = cpi.get("module", "")
        owner = cpi.get("owner_agent_id", "")
        expected_owner = MODULE_OWNER.get(module)
        if not expected_owner:
            issues.append(_issue("UNKNOWN_MODULE", f"未知模块: {module}"))
        elif owner != expected_owner:
            issues.append(_issue("PLAN_OWNER_MISMATCH",
                f"{module} item owner={owner}，应为 {expected_owner}（Producer 不可替写专业内容）"))
        # proposal_item_ids 必须来自对应模块提案
        for piid in cpi.get("proposal_item_ids", []):
            src = item_index.get(piid)
            if src is None:
                issues.append(_issue("PLAN_ENTITY_NOT_FOUND",
                    f"plan item 引用不存在的 proposal item: {piid}"))
            elif src[0] != module or src[1] != owner:
                issues.append(_issue("PLAN_OWNER_MISMATCH",
                    f"proposal item {piid} 属于 {src[0]}/{src[1]}，不匹配 {module}/{owner}"))
        # §44 entity existence（update/delete 的 target 必须存在）
        for ref in cpi.get("scope_refs", []):
            et = ref.get("entity_type")
            eid = ref.get("entity_id")
            if not eid:
                continue
            if et == "scene" and eid not in scene_ids:
                issues.append(_issue("PLAN_ENTITY_NOT_FOUND", f"故事段 {eid} 不存在于 source"))
            elif et == "character" and eid not in char_names:
                issues.append(_issue("PLAN_ENTITY_NOT_FOUND", f"角色 {eid} 不存在于 source"))
            elif et == "shot" and eid not in shot_ids:
                issues.append(_issue("PLAN_ENTITY_NOT_FOUND", f"镜头 {eid} 不存在于 source"))

    # §45 dependency coverage：delete_character 留下禁止引用（§32）
    for p in proposals:
        for it_dict in p.items:
            item = ModuleProposalItem.from_dict(it_dict)
            if item.action == "delete_character":
                name = (item.intent or {}).get("name", "")
                ref_scenes = [s.scene_id for s in project.scenes if name in s.characters]
                ref_shots = [sh.shot_id for sh in project.all_shots() if name in sh.characters]
                if ref_scenes or ref_shots:
                    # 是否有其它 proposal item 移除这些引用？查 script update_scene 排除 name / director update_shot 排除
                    removed = _refs_removed(proposals, name)
                    if not (removed["scenes"] >= set(ref_scenes) and removed["shots"] >= set(ref_shots)):
                        issues.append(_issue("PLAN_REFERENCE_CONFLICT",
                            f"删除角色 {name} 时仍被 {len(ref_scenes)} 场景/{len(ref_shots)} 镜头引用（§32）"))

    # §46 conflict coverage
    if any(c.get("severity") == "hard" for c in conflicts):
        issues.append(_issue("PLAN_UNRESOLVED_CONFLICT",
            "存在未解决的结构硬冲突，不可进入 preview_ready"))

    # §47 user decision
    if blocking_pending_decisions > 0:
        issues.append(_issue("PLAN_USER_DECISION_REQUIRED",
            f"{blocking_pending_decisions} 个阻塞决策未回答"))

    # §48 source signature
    if (session.source_snapshot_signature and plan.source_snapshot_signature
            and plan.source_snapshot_signature != session.source_snapshot_signature):
        issues.append(_issue("PLAN_STALE", "plan 签名与 session 起始签名不一致"))

    # §49 lock/protected
    for p in proposals:
        for it_dict in p.items:
            item = ModuleProposalItem.from_dict(it_dict)
            if item.module == "storyboard":
                shid = (item.intent or {}).get("shot_id")
                if shid:
                    sh = project.get_shot(shid)
                    if sh and getattr(sh, "locked", False):
                        issues.append(_issue("LOCKED_SCOPE_REQUIRES_USER_DECISION",
                            f"镜头 {shid} 已锁定（需用户决策方可由后续执行阶段修改）", "warning"))

    # §39 DAG cycle
    if _find_cycle(plan.steps):
        issues.append(_issue("PLAN_DEPENDENCY_CYCLE", "plan 步骤依赖 DAG 存在环"))

    return issues


def _refs_removed(proposals, name) -> dict:
    """其它 proposal item 是否移除了 name 的引用（script update_scene 排除 / director update_shot 排除）。"""
    removed_scenes, removed_shots = set(), set()
    for p in proposals:
        for it_dict in p.items:
            item = ModuleProposalItem.from_dict(it_dict)
            if item.action == "update_scene" and name not in ((item.intent or {}).get("characters") or []):
                removed_scenes.add((item.intent or {}).get("scene_id"))
            elif item.action == "delete_scene":
                removed_scenes.add((item.intent or {}).get("scene_id"))
            elif item.action == "update_shot" and name not in ((item.intent or {}).get("characters") or []):
                removed_shots.add((item.intent or {}).get("shot_id"))
            elif item.action == "delete_shot":
                removed_shots.add((item.intent or {}).get("shot_id"))
    return {"scenes": {s for s in removed_scenes if s}, "shots": {s for s in removed_shots if s}}


def has_errors(issues: List[dict]) -> bool:
    return any(i.get("severity", "error") == "error" for i in issues)
