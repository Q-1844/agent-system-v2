# src/agents/change_protocol/plan_compiler.py
# AI-7B Producer 统一变更计划编译器（§23/§37/§38/§40）。
#
# §22/§40 绝对红线：Producer 只决定**编排**（哪些 proposal item 入计划 / 模块归属 /
# 依赖 / 步骤 DAG / 冲突取舍 / 是否需二轮），**绝不**重写专业内容——它引用 proposal_item_ids，
# 不发明 Scene/Shot/Character 内容。direct_change_items 引用 user_requested 提案 item；
# downstream_change_items 派生自 impact facts（§28）。LLM 不产出 ChangeOperation（§50）。
#
# 输出 UnifiedChangePlan（status=draft，待 validator）。
from __future__ import annotations
import json
import secrets
import time
from typing import List

from ..inquiry import MODULE_OWNER
from .models import (ChangeSession, ChangeRequest, ModuleProposal, ModuleProposalItem,
                      UnifiedChangePlan, ChangePlanItem, ChangePlanStep, PlanDependencyEdge)


def _pid() -> str:
    return "plan_" + secrets.token_hex(6)


def _piid() -> str:
    return "pi_" + secrets.token_hex(4)


def _sid() -> str:
    return "step_" + secrets.token_hex(4)


def _eid() -> str:
    return "edge_" + secrets.token_hex(4)


def _now() -> float:
    return time.time()


_IMPACT_OWNER = {
    "STORYBOARD_REVIEW_REQUIRED": "storyboard", "PRODUCTION_PLAN_STALE": "production_plan",
    "PROMPT_CONTEXT_AFFECTED": "storyboard", "CONTINUITY_REVIEW_REQUIRED": "storyboard",
    "CHARACTER_ASSETS_REVIEW_REQUIRED": "character",
    "FRAME_REGENERATION_RECOMMENDED": "production", "VIDEO_REGENERATION_RECOMMENDED": "production",
    "CHARACTER_SCENE_REFERENCES_AFFECTED": "character",
    "CHARACTER_SHOT_REFERENCES_AFFECTED": "character",
    "ADJACENCY_CHANGED": "storyboard", "CONTINUITY_CHAIN_CHANGED": "storyboard",
}


def _module_owner(module: str) -> str:
    return MODULE_OWNER.get(module, "")


def _direct_items(proposals: List[ModuleProposal]) -> List[dict]:
    """§37：引用每个 proposal item（user_requested）→ ChangePlanItem。Producer 不重写内容。"""
    out = []
    for prop in proposals:
        for item_dict in prop.items:
            item = ModuleProposalItem.from_dict(item_dict)
            out.append(ChangePlanItem(
                plan_item_id=_piid(), module=prop.module, owner_agent_id=prop.owner_agent_id,
                proposal_item_ids=[item.item_id], scope_refs=list(item.target_refs),
                summary=f"{item.action}: " + json.dumps(item.intent, ensure_ascii=False)[:140],
                reason=item.reason or item.action, source="user_requested", required=True).to_dict())
    return out


def _downstream_items(impact_facts: List[dict]) -> List[dict]:
    """§71/§28：派生自 impact facts（downstream_impact，非用户直接要求）。"""
    out = []
    for imp in impact_facts:
        module = _IMPACT_OWNER.get(imp.get("reason_code", ""), "storyboard")
        out.append(ChangePlanItem(
            plan_item_id=_piid(), module=module, owner_agent_id=_module_owner(module),
            proposal_item_ids=[], scope_refs=list(imp.get("entity_refs", [])),
            summary=imp.get("summary", ""), reason=imp.get("reason_code", ""),
            source="downstream_impact", required=False).to_dict())
    return out


def _unchanged_scope(proposals: List[ModuleProposal], project) -> List[dict]:
    """§73：明确不受影响的 scope（scenes/characters 未被任何 proposal 触及）。"""
    touched_scenes, touched_chars = set(), set()
    for prop in proposals:
        for item_dict in prop.items:
            item = ModuleProposalItem.from_dict(item_dict)
            for ref in item.target_refs:
                if ref.get("entity_type") == "scene":
                    touched_scenes.add(ref.get("entity_id"))
                elif ref.get("entity_type") == "character":
                    touched_chars.add(ref.get("entity_id"))
            # insert_scene 的 after_scene_id 也算触及
            sid = (item.intent or {}).get("scene_id") or (item.intent or {}).get("after_scene_id")
            if sid:
                touched_scenes.add(sid)
            name = (item.intent or {}).get("name")
            if name:
                touched_chars.add(name)
    out = []
    for s in project.scenes:
        if s.scene_id not in touched_scenes:
            out.append({"module": "script", "entity_type": "scene", "entity_id": s.scene_id})
    for c in project.characters:
        if c.name not in touched_chars:
            out.append({"module": "character", "entity_type": "character", "entity_id": c.name})
    return out


def _build_steps_dag(proposals: List[ModuleProposal], direct_items: List[dict]):
    """§38/§40：编排 DAG。script+character 可并行（parallel_group=1）；storyboard 依赖二者（§26）。
    Producer 只编排顺序，不发明内容。"""
    present_modules = {p.module for p in proposals}
    steps: List[dict] = []
    edges: List[dict] = []
    step_by_module: dict = {}
    # 第一层：script + character 并行
    group1 = []
    for module in ("script", "character"):
        if module in present_modules:
            ids = [d["plan_item_id"] for d in direct_items if d["module"] == module]
            step = ChangePlanStep(step_id=_sid(), module=module,
                owner_agent_id=_module_owner(module), plan_item_ids=ids,
                depends_on_step_ids=[], parallel_group="1")
            steps.append(step.to_dict())
            step_by_module[module] = step.step_id
            group1.append(step.step_id)
    # 第二层：storyboard 依赖第一层
    if "storyboard" in present_modules:
        ids = [d["plan_item_id"] for d in direct_items if d["module"] == "storyboard"]
        step = ChangePlanStep(step_id=_sid(), module="storyboard",
            owner_agent_id=_module_owner("storyboard"), plan_item_ids=ids,
            depends_on_step_ids=list(group1), parallel_group="2")
        steps.append(step.to_dict())
        step_by_module["storyboard"] = step.step_id
        for dep in group1:
            edges.append(PlanDependencyEdge(edge_id=_eid(), from_step_id=dep,
                to_step_id=step.step_id, reason="storyboard depends on finalized upstream").to_dict())
    return steps, edges


def compile_change_plan(session: ChangeSession, request: ChangeRequest,
                        proposals: List[ModuleProposal], dep_facts: List[dict],
                        impact_facts: List[dict], conflicts: List[dict],
                        decision_ids: List[str], version: int,
                        source_signature: str, project, parent_plan_id=None,
                        user_feedback_message_id=None) -> UnifiedChangePlan:
    """§23：编译 UnifiedChangePlan（draft，待 validator）。Producer 只编排，不写专业内容。"""
    direct = _direct_items(proposals)
    downstream = _downstream_items(impact_facts)
    unchanged = _unchanged_scope(proposals, project)
    steps, edges = _build_steps_dag(proposals, direct)
    return UnifiedChangePlan(
        plan_id=_pid(), change_session_id=session.session_id,
        project_name=session.project_name, version=version,
        source_snapshot_signature=source_signature, status="draft",
        direct_change_items=direct, downstream_change_items=downstream,
        unchanged_scope=unchanged, steps=steps, dependency_edges=edges,
        user_decision_ids=list(decision_ids),
        module_proposal_ids=[p.proposal_id for p in proposals],
        validation_issues=[], preview_id=None, approval=None,
        parent_plan_id=parent_plan_id, user_feedback_message_id=user_feedback_message_id,
        created_at=_now())
