# src/workflow/adapter.py
# 六个 Agent 的统一生命周期适配（PHASE46_01 §5.2）：工作流执行层只做
# 「解析输入 → 建立尝试 → 调真实入口 → 收货校验 → 形成交接包」，不重新实现
# 任何专业 Agent 的制作策略，不直接写 canonical。
#
# 派发通道（§5.1 复用委派底座）：
#   - 五个 Specialist：producer._delegate_specialist（同一套 AgentTask/锁/changed 事件/
#     turn_registry 互斥/bridge 记录），requester_agent="workflow"，workflow_ref 关联。
#   - Producer：producer_turn 直调 + WF_DELEGATE_GUARD 服务端守卫（工作流上下文中
#     不允许委派 Specialist，后续节点由顺序执行层派发）。
# 全部文本调用 thinking=True（§6；快照固化于 run.snapshot.thinking）。
from __future__ import annotations
import json
import logging
from typing import Any, Dict, List, Optional

from .models import (NODE_SUCCESS, NODE_FAILED, NODE_STOPPED,
                     NODE_NEEDS_RECONCILIATION)

log = logging.getLogger("workflow.adapter")

SPECIALISTS = ["script_writer", "character_designer", "scene_designer",
               "director", "assembly"]


# ============ 交接包（§5.3 数据交接）============
def build_handoff(run: dict, node_id: str, attempt: dict, task_result: dict,
                  summary: str) -> dict:
    """从真实收货结果构造交接包。字段映射现有结构：
    task_refs=AgentTask ID；artifact_refs 由 changed_refs（真实回执派生）+ 类型映射。"""
    changed = task_result.get("changed_refs") or {}
    kind_map = [("scenes", "scene"), ("shots", "shot"), ("characters", "character"),
                ("sequences", "sequence"), ("looks", "look"), ("props", "prop"),
                ("clothings", "clothing"), ("compositions", "composition"),
                ("reference_assets", "reference_asset"), ("bindings", "binding"),
                ("locations", "location"), ("location_views", "location_view"),
                ("scene_instances", "scene_instance"),
                ("scene_instance_views", "scene_instance_view"),
                ("story", "story")]
    artifact_refs = []
    for key, kind in kind_map:
        for rid in changed.get(key) or []:
            artifact_refs.append({"kind": kind, "id": rid,
                                  "source": "changed_refs(ok+verified+非unchanged)"})
    return {
        "runId": run["runId"],
        "nodeId": node_id,
        "attemptId": attempt.get("attemptId"),
        "taskRefs": [task_result.get("task_id")] if task_result.get("task_id") else [],
        "receiptSummary": {
            "verified": bool(task_result.get("verified")),
            "n_tool_calls": task_result.get("n_tool_calls"),
            "warnings": list(task_result.get("warnings") or [])[:6],
        },
        "artifactRefs": artifact_refs,
        "brief": (summary or "")[:400],
    }


def upstream_context(run: dict, node_plan: dict) -> dict:
    """派发前的输入解析（§4.5 逐节点输入账本）：上游交接包 + 更早节点产物索引。"""
    upstream_id = node_plan.get("upstreamNodeId")
    upstream = (run.get("nodes") or {}).get(upstream_id) if upstream_id else None
    ctx = {"runId": run["runId"], "workflowName":
           ((run.get("snapshot", {}).get("definition") or {}).get("name") or "")}
    if upstream:
        ctx["upstream"] = {
            "nodeId": upstream_id,
            "refId": upstream.get("refId"),
            "attemptId": upstream.get("currentAttemptId"),
            "handoff": upstream.get("handoff") or {},
        }
        # 更早节点的产物索引（单链：沿 handoff 链收集，供"更早节点的故事/Look"读取）
        earlier: List[dict] = []
        seen = {upstream_id}
        cur = upstream.get("upstreamNodeId")
        while cur and cur not in seen:
            seen.add(cur)
            n = (run.get("nodes") or {}).get(cur) or {}
            if n.get("handoff"):
                earlier.append({"nodeId": cur, "refId": n.get("refId"),
                                "artifactRefs": (n["handoff"].get("artifactRefs") or []),
                                "brief": (n["handoff"].get("brief") or "")[:160]})
            cur = n.get("upstreamNodeId")
        ctx["earlierNodes"] = earlier
    return ctx


def build_envelope(rt, project: str, run: dict, node_plan: dict) -> dict:
    """构造委派 envelope（objective/situation/constraints/scope/user_intent，
    与 producer.py 委派 envelope 同构）。"""
    ref_id = node_plan.get("refId")
    objective = node_plan.get("objective") or ""
    ctx = upstream_context(run, node_plan)
    # 输入账本：派发时记录上游引用（canonical 实际版本在 executor 派发前后登记指纹）
    sit_parts = [f"工作流「{ctx['workflowName']}」（run {ctx['runId']}）的节点任务。"]
    if ctx.get("upstream"):
        up = ctx["upstream"]
        sit_parts.append("上游节点已完成并交接："
                         + json.dumps(up["handoff"], ensure_ascii=False)[:1200])
        if ctx.get("earlierNodes"):
            sit_parts.append("更早节点产物索引："
                             + json.dumps(ctx["earlierNodes"], ensure_ascii=False)[:800])
        sit_parts.append("上游引用是接续依据；正式制作前请用你的读工具核对项目内真实"
                         " canonical（story/characters/scenes/shots/sequences），"
                         "以上游交接与项目事实共同为准。")
    constraints = [
        "只在本节点职责与任务范围内工作；完成判定以真实工具回执为准，不是对话回复。",
        "完成本节点后用一段话总结你实际写入/产出的内容（含 ID），不要声称未执行的操作。",
    ]
    for s in node_plan.get("skills") or []:
        constraints.append(f"本节点已绑定正式 Skill：{s.get('skillId')} v{s.get('version')}"
                           f"（适用时用 skill.load 加载并遵循其方法）。")
    if ref_id == "producer":
        constraints.append(
            "工作流上下文：本节点只做制作计划/交接准备，不要委派任何 Specialist"
            "——后续专业节点由工作流顺序执行层派发（系统将在服务端拦截越权委派）。")
    envelope = {
        "objective": objective,
        "situation": "\n".join(sit_parts),
        "constraints": constraints,
        "scope": {"free": f"workflow_node:{node_plan.get('nodeId')}"},
        "user_intent": (run.get("snapshot", {}).get("definition") or {}).get("name") or "",
        "workflow_ctx": {"runId": ctx["runId"], "nodeId": node_plan.get("nodeId")},
    }
    return envelope


# ============ 收货校验（§5.3 节点成功条件）============
def verify_completion(node_plan: dict, task_result: dict) -> dict:
    """节点成功校验：turn 真实结果 + 必要 receipt + 产物在预期域。返回
    {ok, reason, code}。工具 accepted/LLM 结束/任务已提交都不单独算成功。"""
    status = (task_result.get("status") or "").lower()
    if status == "stopped":
        return {"ok": False, "code": "stopped", "reason": "任务被停止"}
    if status in ("failed", "not_configured"):
        return {"ok": False, "code": status,
                "reason": task_result.get("summary") or f"任务结束状态={status}"}
    if status != "completed":
        return {"ok": False, "code": f"unexpected_status:{status}",
                "reason": f"任务结束状态异常: {status}"}
    ref_id = node_plan.get("refId")
    expected = set(node_plan.get("expectedDomains") or [])
    changed = task_result.get("changed_refs") or {}
    got = {k for k, v in changed.items() if v}
    if ref_id == "producer":
        # 计划/协调节点：无 canonical 写域要求；但守卫下不应有真实委派
        return {"ok": True, "code": "planning_node", "reason": "计划节点完成（无 canonical 写入要求）"}
    if not expected & got:
        return {"ok": False, "code": "no_expected_artifacts",
                "reason": (f"任务结束但未见预期产物域的已验证回执"
                           f"（预期 {sorted(expected)}，实际变更 {sorted(got) or '无'}）——"
                           f"节点不能算成功")}
    return {"ok": True, "code": "verified", "reason": "预期域存在 ok+verified 回执"}


def last_skill_evidence(rt, project: str, session_id: str) -> Optional[dict]:
    """从 Specialist 持久会话的最后一条 assistant 消息 meta.skills 提取加载证据
    （不重实现 skill runtime；证据以持久化消息为准）。"""
    try:
        store = rt._session_store(project)
        s = store.load(session_id)
        if not s or not s.messages:
            return None
        for m in reversed(s.messages):
            if m.role == "assistant" and isinstance(m.meta, dict) and m.meta.get("skills"):
                return m.meta.get("skills")
        return None
    except Exception:
        return None


# ============ 派发 ============
async def dispatch_node(rt, project: str, run: dict, node_plan: dict,
                        attempt: dict, *, event_cb, cancel_event) -> dict:
    """派发一个 Agent 节点并等待真实结果。返回归一化结果：
    {status, task_result, session_id, skillEvidence, error}。
    status ∈ completed / failed / stopped / not_configured。"""
    from src.agents.producer import (WF_DELEGATE_GUARD, producer_turn,
                                     _delegate_specialist, render_envelope)
    ref_id = node_plan.get("refId")
    envelope = build_envelope(rt, project, run, node_plan)
    envelope_text = render_envelope(envelope)
    run_id = run["runId"]
    node_id = node_plan["nodeId"]
    attempt_id = attempt.get("attemptId")

    if ref_id == "producer":
        # Producer 节点：直调 producer_turn + 服务端守卫（allowedDelegates=[]）
        store = rt._session_store(project)
        session = store.get_or_create_conversation("producer")
        sid = session.session_id
        token = WF_DELEGATE_GUARD.set({
            "runId": run_id, "nodeId": node_id, "attemptId": attempt_id,
            "allowedDelegates": []})
        try:
            result = await producer_turn(rt, project, sid, envelope_text,
                                         thinking=True, event_cb=event_cb,
                                         cancel_event=cancel_event)
        finally:
            WF_DELEGATE_GUARD.reset(token)
        msg = result.get("message") or {}
        status = (msg.get("status") or "completed")
        guard_hits = [r for r in (result.get("receipts") or [])
                      if r.get("code") == "workflow_scope_denied"]
        task_result = {
            "task_id": None,
            "agent": "producer",
            "status": status,
            "summary": result.get("assistant_message") or "",
            "changed_refs": {},
            "warnings": (["producer 节点出现被拦截的越权委派："
                          + json.dumps(g.get("error") or "", ensure_ascii=False)[:160]
                          for g in guard_hits[:3]] if guard_hits else []),
            "verified": True,
            "n_tool_calls": len(result.get("trace") or []),
            "session_id": sid,
        }
        skill_ev = (result.get("message", {}).get("meta") or {}).get("skills")
        return {"status": status, "task_result": task_result,
                "session_id": sid, "skillEvidence": skill_ev,
                "raw": {"linked_task_ids": result.get("linked_task_ids") or []}}

    # Specialist：复用委派底座（AgentTask 由 _delegate_specialist 创建并结算）
    wf_ref = {"runId": run_id, "nodeId": node_id, "attemptId": attempt_id}
    task_result = await _delegate_specialist(
        rt, project, ref_id, envelope,
        event_cb=event_cb, cancel_event=cancel_event,
        prev_task_id=None,
        workflow_id=f"wf_run:{run_id}",
        thinking=True,
        requester_agent="workflow",
        workflow_ref=wf_ref)
    sid = task_result.get("session_id") or ""
    skill_ev = last_skill_evidence(rt, project, sid) if sid else None
    status = task_result.get("status") or "failed"
    return {"status": status, "task_result": task_result,
            "session_id": sid, "skillEvidence": skill_ev, "raw": {}}
