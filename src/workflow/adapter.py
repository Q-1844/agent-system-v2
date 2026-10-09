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
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import (NODE_SUCCESS, NODE_FAILED, NODE_STOPPED,
                     NODE_NEEDS_RECONCILIATION)

log = logging.getLogger("workflow.adapter")

SPECIALISTS = ["script_writer", "character_designer", "scene_designer",
               "director", "assembly"]


# ============ 交接包（§5.3/§4.4 数据交接）============
def build_handoff(run: dict, node_id: str, attempt: dict, task_result: dict,
                  summary: str, verify_outputs: Optional[dict] = None,
                  node_plan: Optional[dict] = None) -> dict:
    """从真实收货结果构造交接包（Phase 47 v2 语义）：
    - workflowRef：run/节点/attempt 关联（§4.4 交接以运行上下文为单位）；
    - outputs：verify_outputs 的 created/reused 输出记录（kind/entityId/versionId/
      disposition/contentHash/reviewState）——具体范围与版本，不是"去项目里找最新"；
    - reusedRefs 与 changedRefs 分开（§4.3：复用不混写为新产物）；
    - missingRequirements：目标级缺项（诚实缺项，不假成功）。"""
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
    outputs: List[dict] = []
    reused_refs: List[dict] = []
    missing: List[dict] = []
    if verify_outputs:
        for o in verify_outputs.get("outputs") or []:
            outputs.append({**o, "disposition": "created"})
        for o in verify_outputs.get("reused") or []:
            reused_refs.append({**o, "disposition": "reused"})
            outputs.append({k: o.get(k) for k in
                            ("kind", "entityId", "versionId", "contentHash",
                             "reviewState", "fileOk")} | {"disposition": "reused"})
        missing = list(verify_outputs.get("missing") or [])
    scope = (node_plan or {}).get("scope") or None
    return {
        "runId": run["runId"],
        "nodeId": node_id,
        "attemptId": attempt.get("attemptId"),
        "workflowRef": {"runId": run["runId"], "nodeId": node_id,
                        "attemptId": attempt.get("attemptId")},
        "scope": scope,
        "taskRefs": [task_result.get("task_id")] if task_result.get("task_id") else [],
        "receiptSummary": {
            "verified": bool(task_result.get("verified")),
            "n_tool_calls": task_result.get("n_tool_calls"),
            "warnings": list(task_result.get("warnings") or [])[:6],
        },
        "artifactRefs": artifact_refs,
        "outputs": outputs,
        "reusedRefs": reused_refs,
        "missingRequirements": missing,
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


def build_envelope(rt, project: str, run: dict, node_plan: dict,
                   frozen_inputs: Optional[dict] = None) -> dict:
    """构造委派 envelope（objective/situation/constraints/scope/user_intent，
    与 producer.py 委派 envelope 同构）。

    Phase 47 §4.5：frozen_inputs = freeze_node_inputs() 的结果（派发前服务端解析并
    冻结的上游输出版本），写进 situation 作为本节点明确输入依据；§4.7：assembly
    节点只允许采用冻结清单内的视频版本，禁止"找项目里最新的视频"。"""
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
    if frozen_inputs:
        fi = json.dumps(frozen_inputs.get("resolvedInputs") or [], ensure_ascii=False)[:1600]
        sit_parts.append(f"本节点冻结输入（服务端派发前核验的版本清单）：{fi}")
        if frozen_inputs.get("stale"):
            sit_parts.append("注意：下列上游产物在派发前核验时已失效（不得采用）："
                             + json.dumps(frozen_inputs["stale"], ensure_ascii=False)[:600])
    # Phase48 E：重试 attempt 的失败事实反馈——引擎 continue 开新 attempt 后，
    # 会话复用会让 Agent 以"已做过"为由不动手；而服务端完成标准（completion）
    # 不出现在 objective 里。把上次失败原因如实告诉它（只给事实，不指定做法）。
    prev_err = _last_failed_attempt_error(run, node_plan.get("nodeId"))
    if prev_err:
        sit_parts.append(
            "重试提示：本节点此前 attempt 未通过服务端完成校验：" + prev_err[:300]
            + " —— 请先用读工具核对项目内真实状态（不要凭会话记忆认定已完成），"
              "再补齐缺失产物；完成判定仍以真实工具回执为准。")
    # Phase51 A：审核驳回后的显式修复（continue_run rejected 路径落 repairNote）——
    # 修复目标=驳回指出的问题；合格部分保留，不做无差别重做。
    repair_note = str(((run.get("nodes") or {}).get(node_plan.get("nodeId")) or {})
                      .get("repairNote") or "")
    if repair_note:
        sit_parts.append(
            "修复提示：本节点此前交付曾被审核驳回：" + repair_note[:300]
            + " —— 请针对驳回指出的问题定点修复，保留合格部分（不要无差别重做"
              "全部产物）；完成后正常交付，完成判定仍以真实工具回执为准。")
    constraints = [
        "只在本节点职责与任务范围内工作；完成判定以真实工具回执为准，不是对话回复。",
        "完成本节点后用一段话总结你实际写入/产出的内容（含 ID），不要声称未执行的操作。",
    ]
    if frozen_inputs and ref_id == "assembly":
        constraints.append(
            "装配素材边界（服务端强制）：本节点只允许采用冻结输入清单中列出的视频版本"
            "（entityId+versionId），以及节点目标明确声明的补充依赖；"
            "禁止用「项目里最新的视频」代替交接清单，禁止采用清单外或已失效版本。")
    elif frozen_inputs:
        constraints.append(
            "输入边界：采用上游产物时以冻结输入清单所列版本为准；需要清单外的素材时"
            "先在总结中声明补充依赖及其引用，不得暗中切换到其他版本。")
    for s in node_plan.get("skills") or []:
        constraints.append(f"本节点已绑定正式 Skill：{s.get('skillId')} v{s.get('version')}"
                           f"（适用时用 skill.load 加载并遵循其方法）。")
    if ref_id == "producer":
        constraints.append(
            "工作流上下文：本节点只做制作计划/交接准备，不要委派任何 Specialist"
            "——后续专业节点由工作流顺序执行层派发（系统将在服务端拦截越权委派）。")
    comp_cfg = node_plan.get("completion") or {}
    _spec_kinds = {o.get("kind") for o in (comp_cfg.get("outputs") or [])}
    if ref_id == "director" and "video_version" in _spec_kinds:
        # Phase51 A（P50 实证：委派间隙空闲 2765s——导演"提交→等结算→再委派"的
        # 回合节拍把并发收益吃回）。运行层收货与排队已就绪：独立镜头立即连续提交。
        constraints.append(
            "视频提交纪律（运行层已就绪）：彼此独立（无接力端点依赖）的镜头请立即"
            "连续提交，不必等待此前提交结算——本地队列按在途上限自动排队、按 RPM "
            "自动节流；仅当下游镜头以上游真实保留端点为输入（接力依赖）时才等待"
            "上游完成。当前可提交的镜头全部提交完毕并总结后再结束回合；服务端会在"
            "回合结束后等待在途视频结算并重判完成，不会因提前结束而误判失败。")
    if ref_id == "character_designer":
        # Phase 47 §4.1：目标与工具约束错配的最小引导（不是放宽 OCC）——
        # 道具没有参考图时如实列为缺项，不要让它阻塞角色/Look 主目标。
        constraints.append(
            "范围纪律：角色/Look/参考图可用你的图像生成工具真实产出；"
            "Prop 登记必须有用户提供的参考图——目标要求道具但没有可用参考时，"
            "在总结中把它列为缺项（missingRequirements）交付人工处理，"
            "不要反复重试也不要伪造参考。")
    envelope = {
        "objective": objective,
        "situation": "\n".join(sit_parts),
        "constraints": constraints,
        "scope": {"free": f"workflow_node:{node_plan.get('nodeId')}"},
        "user_intent": (run.get("snapshot", {}).get("definition") or {}).get("name") or "",
        "workflow_ctx": {"runId": ctx["runId"], "nodeId": node_plan.get("nodeId")},
    }
    return envelope


def _last_failed_attempt_error(run: dict, node_id: Optional[str]) -> str:
    """节点最近一次 failed attempt 的校验错误（无则空串）。用于重试反馈。"""
    if not node_id:
        return ""
    node = (run.get("nodes") or {}).get(node_id) or {}
    for att in reversed(node.get("attempts") or []):
        if att.get("status") == "failed" and att.get("error"):
            return str(att["error"])
    return ""


# ============ 收货校验（§5.3 节点成功条件 + Phase 47 §4.2 目标级核对）============
def verify_completion(node_plan: dict, task_result: dict, *,
                      pdir=None, project: Optional[str] = None,
                      prior_changed_refs: Optional[Dict[str, List[str]]] = None,
                      window_start: Optional[float] = None) -> dict:
    """节点成功校验：turn 真实结果 + 必要 receipt + 产物在预期域。返回
    {ok, reason, code}。工具 accepted/LLM 结束/任务已提交都不单独算成功。

    Phase 47 §4.2：节点配置了 completion.outputs 时，域命中只算底线——再按目标
    逐项核对真实项目事实（存在/图像/审批/可播放），缺项如实返回（不假成功）；
    §4.3：completion.strategy=allow_verified_reuse 时允许服务端核验过的复用。

    Phase51 A（require_new 重试判死修复）：prior_changed_refs = 本节点本 run 此前
    attempts 的真实写入汇总（服务端收货留档）。域命中底线与目标级判定均按
    「本 attempt ∪ 此前 attempts」累计——重试 attempt 延续既有真实成果+补缺项；
    回执之外的旧素材仍不算（诚实性不变）。"""
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
    prior = prior_changed_refs or {}
    got = {k for k, v in changed.items() if v}
    prior_got = {k for k, v in prior.items() if v}
    comp = node_plan.get("completion") or {}
    strategy = comp.get("strategy") or "require_new"
    if ref_id == "producer":
        # 计划/协调节点：无 canonical 写域要求；但守卫下不应有真实委派
        return {"ok": True, "code": "planning_node", "reason": "计划节点完成（无 canonical 写入要求）"}
    if not expected & got:
        # §4.3：allow_verified_reuse 节点允许零新写入（纯核验复用）——由目标级
        # 输出核对决定成败；require_new 以域命中为底线，但按「本 attempt ∪
        # 此前 attempts」累计（Phase51 A：重试 attempt 延续既有真实写入）。
        if not (expected & prior_got):
            if not (comp.get("outputs") and strategy == "allow_verified_reuse"):
                return {"ok": False, "code": "no_expected_artifacts",
                        "reason": (f"任务结束但未见预期产物域的已验证回执"
                                   f"（预期 {sorted(expected)}，本 attempt 实际变更 "
                                   f"{sorted(got) or '无'}，此前 attempts 变更 "
                                   f"{sorted(prior_got) or '无'}）——节点不能算成功")}
    # ---- Phase 47 §4.2：目标级输出核对（completion.outputs 存在时）----
    if comp.get("outputs"):
        if pdir is None or project is None:
            # 派发方未提供项目事实路径（旧调用点）——保守：不做目标级判定，
            # 保留域命中结论（不静默放宽，也不在无事实时乱判失败）。
            return {"ok": True, "code": "domain_hit",
                    "reason": "域命中（目标级核对跳过：无项目事实上下文）"}
        from . import outputs as _outputs
        try:
            ov = _outputs.verify_node_outputs(Path(pdir), project, node_plan, changed,
                                              prior_changed=prior,
                                              window_start=window_start)
        except Exception as e:
            log.exception("目标级输出核对异常 node=%s", node_plan.get("nodeId"))
            return {"ok": False, "code": "outputs_verify_error",
                    "reason": f"目标级输出核对异常：{type(e).__name__}: {str(e)[:200]}"}
        if not ov.get("ok"):
            det = "；".join(m.get("reason", "") for m in (ov.get("missing") or [])[:4])
            return {"ok": False, "code": "outputs_missing",
                    "reason": f"目标级输出缺项：{det}",
                    "outputsVerdict": ov}
        return {"ok": True, "code": "outputs_verified", "reason": ov.get("reason"),
                "outputsVerdict": ov}
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
                        attempt: dict, *, event_cb, cancel_event,
                        frozen_inputs: Optional[dict] = None) -> dict:
    """派发一个 Agent 节点并等待真实结果。返回归一化结果：
    {status, task_result, session_id, skillEvidence, error}。
    status ∈ completed / failed / stopped / not_configured。
    frozen_inputs：executor 派发前冻结的上游输出版本（§4.5），进 envelope。"""
    from src.agents.producer import (WF_DELEGATE_GUARD, producer_turn,
                                     _delegate_specialist, render_envelope)
    ref_id = node_plan.get("refId")
    envelope = build_envelope(rt, project, run, node_plan, frozen_inputs)
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
