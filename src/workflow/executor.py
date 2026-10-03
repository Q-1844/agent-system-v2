# src/workflow/executor.py
# 顺序执行引擎（PHASE46_01 §5.1/§7）：工作流 run 是唯一的顺序调度权威。
#
# 关键机制与依据：
# - worker = asyncio task（与既有 Producer 委派同进程语义）；持久状态先落盘再派发，
#   worker 依据持久状态推进（§7.1：状态变更与事件可恢复一致）。
# - 派发窗口（R03/R04）：attempt 先落盘（dispatching）→ 调真实入口（关联写入同一次
#   AgentTask 创建的 workflow_ref）→ 重启后由 workflow_ref 找回；已结算任务可直接收养。
# - 审核（§7.2）：审核申请持久化（对象=上游 attempt 产物引用+指纹），决策 API 落地后
#   才允许下一节点就绪；驳回停止（不自动回环）。等待审核时 worker 停靠，不占制作锁
#   （锁生命周期在 _delegate_specialist 内部，turn 结束即释放）。
# - 停止（§7.5）：stop_requested 持久化 + cancel_event 协作取消；晚到结果不改终局。
# - 恢复（§7.4）：重启后运行中的 attempt 按真实情况收养/中断（needs_reconciliation），
#   已完成节点不重做；不自动派发新任务，等待显式继续。
from __future__ import annotations
import asyncio
import logging
import secrets
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import adapter
from . import models as M
from .plan import canonical_baseline
from .store import DefinitionStore, RunStore, EventLog, content_hash

log = logging.getLogger("workflow.executor")


class WorkflowError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class WorkflowEngine:
    """进程级单例。project_dir 由调用方解析（routers 侧持 project→dir 解析）。"""

    def __init__(self):
        self._workers: Dict[str, asyncio.Task] = {}   # run_id → worker task

    # ---------- 启动 run（幂等） ----------
    def start_run(self, project_dir: Path, project: str, definition: dict,
                  v: dict, plan: dict, snapshot: dict,
                  idempotency_key: str) -> dict:
        runs = RunStore(project_dir, project)
        # 幂等：同键同参数摘要 → 同一 run；同键不同参数 → 冲突（§7.3）。
        # 参数摘要对定义全文取 hash（不信任调用方自带 contentHash 的存在）。
        params_hash = content_hash({"definition": definition, "workflowId": definition.get("workflowId")})
        for row in runs.list():
            if row.get("idempotencyKey") != idempotency_key:
                continue
            existing = runs.load(row["runId"])
            if existing is None:
                continue
            if existing.get("startParamsHash") == params_hash:
                return {"run": existing, "reused": True}
            raise WorkflowError("idempotency_conflict",
                                "同幂等键但启动参数不同（拒绝复用，另用新键）")
        run_id = "run_" + secrets.token_hex(6)
        nodes_state: Dict[str, dict] = {}
        for nid in plan["order"]:
            pn = plan["nodes"][nid]
            nodes_state[nid] = {
                "nodeId": nid, "type": pn["type"], "refId": pn.get("refId"),
                "status": M.NODE_PENDING, "attempts": [], "currentAttemptId": None,
                "handoff": None, "error": None,
                "upstreamNodeId": pn.get("upstreamNodeId"),
            }
        run = {
            "runId": run_id,
            "project": project,
            "workflowId": definition.get("workflowId"),
            "status": M.RUN_RUNNING,
            "idempotencyKey": idempotency_key,
            "startParamsHash": params_hash,
            "snapshot": snapshot,
            "plan": plan,
            "nodes": nodes_state,
            "reviews": [],
            "cursor": 0,
            "stopRequested": False,
            "error": None,
        }
        run = runs.create(run)
        el = EventLog(project_dir, run_id)
        el.append(run_id, M.EV_RUN_STARTED,
                  workflowId=run.get("workflowId"),
                  textModel=snapshot.get("textModel"),
                  thinking=snapshot.get("thinking"),
                  order=plan["order"])
        return {"run": run, "reused": False}

    # ---------- worker 调度 ----------
    def drive(self, project_dir: Path, project: str, run_id: str,
              cancel_event: Optional[asyncio.Event] = None) -> bool:
        """（重新）驱动 run。已有活跃 worker → False（不重复派发）。"""
        old = self._workers.get(run_id)
        if old is not None and not old.done():
            return False
        self._workers.pop(run_id, None)
        ev = cancel_event or asyncio.Event()
        task = asyncio.ensure_future(self._drive(project_dir, project, run_id, ev))
        task._wf_cancel_event = ev
        self._workers[run_id] = task
        return True

    def worker_running(self, run_id: str) -> bool:
        t = self._workers.get(run_id)
        return t is not None and not t.done()

    def request_stop(self, project_dir: Path, project: str, run_id: str) -> dict:
        """请求停止：持久化 stopRequested + 取消在途 worker（协作式）。"""
        runs = RunStore(project_dir, project)
        run = runs.update(run_id, lambda r: r.__setitem__("stopRequested", True))
        if run is None:
            raise WorkflowError("unknown_run", f"run 不存在: {run_id}")
        EventLog(project_dir, run_id).append(run_id, M.EV_STOP_REQUESTED)
        t = self._workers.get(run_id)
        if t is not None and not t.done():
            ev = getattr(t, "_wf_cancel_event", None)
            if ev is not None:
                ev.set()
        return run

    # ---------- 主循环 ----------
    async def _drive(self, project_dir: Path, project: str, run_id: str,
                     cancel_event: asyncio.Event) -> None:
        runs = RunStore(project_dir, project)
        try:
            await self._drive_inner(project_dir, project, run_id, cancel_event, runs)
        except Exception as e:   # worker 自身异常 → run failed（不假成功）
            log.exception("workflow worker 异常 run=%s", run_id)
            try:
                runs.update(run_id, lambda r: (
                    r.__setitem__("status", M.RUN_FAILED),
                    r.__setitem__("error", f"执行器异常: {type(e).__name__}: {str(e)[:200]}")))
            except Exception:
                pass
        finally:
            self._workers.pop(run_id, None)

    async def _drive_inner(self, project_dir: Path, project: str, run_id: str,
                           cancel_event: asyncio.Event, runs: RunStore) -> None:
        el = EventLog(project_dir, run_id)
        while True:
            run = runs.load(run_id)
            if run is None:
                return
            if run.get("stopRequested"):
                self._finalize_stop(project_dir, run, runs, el)
                return
            plan = run["plan"]
            order: List[str] = plan["order"]
            cursor = int(run.get("cursor") or 0)
            if cursor >= len(order):
                self._refresh_run_status(runs, el, run)
                return
            nid = order[cursor]
            node_run = run["nodes"][nid]
            if node_run["status"] == M.NODE_SUCCESS:
                self._advance(runs, el, run, cursor + 1)
                continue
            pn = plan["nodes"][nid]

            if pn["type"] == "agent":
                done = await self._run_agent_node(project_dir, project, run, pn,
                                                  runs, el, cancel_event)
                if not done:
                    return   # 停靠（等待审核/失败/停止/需要核对）
                continue
            if pn["type"] == "human_review":
                self._open_review(project_dir, run, pn, runs, el)
                return   # worker 停靠等审核
            # 未知类型（编译已排除；防御）
            runs.update(run_id, lambda r: r["nodes"][nid].__setitem__(
                "status", M.NODE_FAILED))
            return

    # ---------- Agent 节点 ----------
    async def _run_agent_node(self, project_dir: Path, project: str, run: dict,
                              pn: dict, runs: RunStore, el: EventLog,
                              cancel_event: asyncio.Event) -> bool:
        run_id = run["runId"]
        nid = pn["nodeId"]

        node_run = run["nodes"][nid]
        # 恢复对账：上次 worker 死亡留下的在途 attempt（§7.4）
        cur = node_run.get("currentAttemptId")
        attempts = node_run.get("attempts") or []
        cur_att = next((a for a in attempts if a.get("attemptId") == cur), None)
        if cur_att is not None and cur_att.get("status") in ("dispatching", "running"):
            ok = self._reconcile_attempt(project_dir, run, nid, cur_att, runs, el)
            if not ok:
                return False   # needs_reconciliation 停靠
            run = runs.load(run_id)   # 收养成功则用最新状态继续
            if run["nodes"][nid]["status"] == M.NODE_SUCCESS:
                return True

        if cancel_event.is_set() or run.get("stopRequested"):
            self._finalize_stop(project_dir, run, runs, el)
            return False

        attempt_id = "att_" + secrets.token_hex(5)
        attempt = {
            "attemptId": attempt_id, "status": M.NODE_DISPATCHING,
            "startedAt": _now(), "endedAt": None,
            "taskRefs": [], "result": None, "error": None,
            "inputs": {"upstreamNodeId": pn.get("upstreamNodeId")},
            "outputs": None, "skillEvidence": None, "thinking": True,
        }
        # 派发意图先落盘（R03 窗口开始）
        def _begin(r):
            n = r["nodes"][nid]
            n["attempts"].append(attempt)
            n["currentAttemptId"] = attempt_id
            _set_node(n, M.NODE_DISPATCHING)
            _set_run_state(r, el, runs)
        runs.update(run_id, _begin)
        el.append(run_id, M.EV_NODE_STATE, node_id=nid,
                  from_=M.NODE_PENDING, to=M.NODE_DISPATCHING, attemptId=attempt_id)

        # event_cb：把真实工具事件写进 run 事件日志（供断线观察恢复；有界防膨胀）
        seq_guard = {"n": 0}

        def _cb(ev: dict):
            seq_guard["n"] += 1
            if seq_guard["n"] > 400:
                return
            t = ev.get("type")
            if t in ("tool.started", "tool.completed", "tool.progress"):
                el.append(run_id, f"tool.{t.split('.')[1]}", node_id=nid,
                          attemptId=attempt_id, name=ev.get("name"),
                          ok=ev.get("ok"), label=ev.get("label"),
                          phase=ev.get("phase"), status=ev.get("status"))

        run = runs.load(run_id)
        try:
            outcome = await adapter.dispatch_node(rt=_rt(), project=project,
                                                  run=run, node_plan=pn,
                                                  attempt=attempt,
                                                  event_cb=_cb,
                                                  cancel_event=cancel_event)
        except Exception as e:
            log.exception("workflow 派发异常 run=%s node=%s", run_id, nid)

            def _fail(r):
                a = _get_attempt(r, nid, attempt_id)
                a["status"] = "failed"
                a["endedAt"] = _now()
                a["error"] = f"{type(e).__name__}: {str(e)[:300]}"
                _set_node(r["nodes"][nid], M.NODE_FAILED, error=a["error"])
                _set_run_state(r, el, runs)
            runs.update(run_id, _fail)
            el.append(run_id, M.EV_NODE_FAILED, node_id=nid, attemptId=attempt_id,
                      error=str(e)[:300])
            return False

        task_result = outcome.get("task_result") or {}
        task_id = task_result.get("task_id")
        # 派发窗口关闭：关联已写入 AgentTask（workflow_ref）——记录 taskRefs（R04 找回身份）
        def _mark_running(r):
            a = _get_attempt(r, nid, attempt_id)
            a["status"] = "running"
            a["taskRefs"] = [task_id] if task_id else []
            a["session"] = outcome.get("session_id")
            _set_node(r["nodes"][nid], M.NODE_RUNNING)
            _set_run_state(r, el, runs)
        runs.update(run_id, _mark_running)
        el.append(run_id, M.EV_ATTEMPT_DISPATCH, node_id=nid, attemptId=attempt_id,
                  taskId=task_id, agent=pn.get("refId"))

        # ---- 收货校验（§5.3）----
        verdict = adapter.verify_completion(pn, task_result)
        status = outcome.get("status")
        if status == "stopped" or verdict.get("code") == "stopped":
            def _stop(r):
                a = _get_attempt(r, nid, attempt_id)
                a["status"] = "stopped"
                a["endedAt"] = _now()
                a["result"] = _result_digest(task_result)
                _set_node(r["nodes"][nid], M.NODE_STOPPED, error="任务被停止")
                _set_run_state(r, el, runs)
            runs.update(run_id, _stop)
            el.append(run_id, M.EV_ATTEMPT_RESULT, node_id=nid, attemptId=attempt_id,
                      status="stopped")
            run = runs.load(run_id)
            self._finalize_stop(project_dir, run, runs, el)
            return False

        if not verdict.get("ok"):
            def _fail(r):
                a = _get_attempt(r, nid, attempt_id)
                a["status"] = "failed"
                a["endedAt"] = _now()
                a["result"] = _result_digest(task_result)
                a["error"] = verdict.get("reason")
                _set_node(r["nodes"][nid], M.NODE_FAILED, error=verdict.get("reason"))
                _set_run_state(r, el, runs)
            runs.update(run_id, _fail)
            el.append(run_id, M.EV_NODE_FAILED, node_id=nid, attemptId=attempt_id,
                      code=verdict.get("code"), error=verdict.get("reason"))
            return False

        # ---- 成功：交接包 + 输入账本（canonical after）----
        run = runs.load(run_id)
        handoff = adapter.build_handoff(run, nid, attempt, task_result,
                                        task_result.get("summary") or "")
        canonical_after = canonical_baseline_rt(project)

        def _succeed(r):
            a = _get_attempt(r, nid, attempt_id)
            a["status"] = "success"
            a["endedAt"] = _now()
            a["result"] = _result_digest(task_result)
            a["skillEvidence"] = outcome.get("skillEvidence")
            a["inputs"]["canonicalBefore"] = (r.get("snapshot") or {}).get(
                "canonicalBaseline")
            a["outputs"] = {"canonicalAfter": canonical_after}
            n = r["nodes"][nid]
            n["handoff"] = handoff
            _set_node(n, M.NODE_SUCCESS)
            _set_run_state(r, el, runs)
        runs.update(run_id, _succeed)
        el.append(run_id, M.EV_ATTEMPT_RESULT, node_id=nid, attemptId=attempt_id,
                  status="success", taskId=task_id)
        el.append(run_id, M.EV_HANDOFF, node_id=nid, attemptId=attempt_id,
                  artifactRefs=handoff.get("artifactRefs"), brief=handoff.get("brief"))
        el.append(run_id, M.EV_NODE_SUCCESS, node_id=nid, attemptId=attempt_id)
        return True

    # ---------- 恢复对账（§7.4 / R03/R04） ----------
    def _reconcile_attempt(self, project_dir: Path, run: dict, nid: str,
                           att: dict, runs: RunStore, el: EventLog) -> bool:
        """在途 attempt 对账：按 workflow_ref 找回 AgentTask——
        已结算 → 收养其结果继续校验；仍在 running → turn 已随进程死亡 →
        needs_reconciliation（不盲重发）。返回 False=停靠。"""
        from src.agents.tasks import AgentTaskStore
        run_id = run["runId"]
        tasks = AgentTaskStore(project_dir, run.get("project") or "unknown")
        task = None
        for t in tasks.list():
            wr = (t.to_dict().get("workflow_ref") or {})
            if (wr.get("runId") == run_id and wr.get("nodeId") == nid
                    and wr.get("attemptId") == att.get("attemptId")):
                task = t
                break
        if task is None:
            def _fail(r):
                a = _get_attempt(r, nid, att["attemptId"])
                a["status"] = "failed"
                a["endedAt"] = _now()
                a["error"] = "进程中断：派发意图已记录，但未找到正式任务关联（未建立或未落盘）"
                _set_node(r["nodes"][nid], M.NODE_NEEDS_RECONCILIATION, error=a["error"])
                _set_run_state(r, el, runs)
            runs.update(run_id, _fail)
            el.append(run_id, M.EV_RECONCILE, node_id=nid, attemptId=att["attemptId"],
                      outcome="no_task")
            return False
        if task.status == "completed":
            # R04：正式任务已结算但本地状态未写完 → 收养继续
            tr = task.to_dict().get("structured_result") or {}
            pn = run["plan"]["nodes"][nid]
            verdict = adapter.verify_completion(pn, tr)
            if verdict.get("ok"):
                handoff = adapter.build_handoff(run, nid, att, tr, tr.get("summary") or "")

                def _adopt(r):
                    a = _get_attempt(r, nid, att["attemptId"])
                    a["status"] = "success"
                    a["endedAt"] = _now()
                    a["result"] = _result_digest(tr)
                    a["taskRefs"] = [task.task_id]
                    n = r["nodes"][nid]
                    n["handoff"] = handoff
                    _set_node(n, M.NODE_SUCCESS)
                    _set_run_state(r, el, runs)
                runs.update(run_id, _adopt)
                el.append(run_id, M.EV_RECONCILE, node_id=nid,
                          attemptId=att["attemptId"], outcome="adopted",
                          taskId=task.task_id)
                return True

            def _fail(r):
                a = _get_attempt(r, nid, att["attemptId"])
                a["status"] = "failed"
                a["endedAt"] = _now()
                a["result"] = _result_digest(tr)
                a["error"] = f"恢复对账：任务已结算但未通过完成校验：{verdict.get('reason')}"
                _set_node(r["nodes"][nid], M.NODE_FAILED, error=a["error"])
                _set_run_state(r, el, runs)
            runs.update(run_id, _fail)
            el.append(run_id, M.EV_RECONCILE, node_id=nid,
                      attemptId=att["attemptId"], outcome="settled_but_invalid",
                      taskId=task.task_id)
            return False
        if task.status in ("failed", "cancelled"):
            def _fail(r):
                a = _get_attempt(r, nid, att["attemptId"])
                a["status"] = "failed"
                a["endedAt"] = _now()
                a["error"] = f"恢复对账：正式任务已终态（{task.status}）：{(task.error or '')[:200]}"
                _set_node(r["nodes"][nid], M.NODE_FAILED, error=a["error"])
                _set_run_state(r, el, runs)
            runs.update(run_id, _fail)
            el.append(run_id, M.EV_RECONCILE, node_id=nid,
                      attemptId=att["attemptId"], outcome=task.status,
                      taskId=task.task_id)
            return False
        # running/pending：turn 随进程死亡，任务卡在运行态——诚实进入需要核对
        def _rec(r):
            a = _get_attempt(r, nid, att["attemptId"])
            a["status"] = "needs_reconciliation"
            a["endedAt"] = _now()
            a["taskRefs"] = [task.task_id]
            a["error"] = ("进程中断：正式任务仍为运行态（无法确认其真实进度）——"
                          f"task={task.task_id}；请人工核对后在页面显式重试")
            _set_node(r["nodes"][nid], M.NODE_NEEDS_RECONCILIATION, error=a["error"])
            _set_run_state(r, el, runs)
        runs.update(run_id, _rec)
        el.append(run_id, M.EV_RECONCILE, node_id=nid, attemptId=att["attemptId"],
                  outcome="needs_reconciliation", taskId=task.task_id)
        return False

    # ---------- 审核（§7.2） ----------
    def _open_review(self, project_dir: Path, run: dict, pn: dict,
                     runs: RunStore, el: EventLog) -> None:
        run_id = run["runId"]
        nid = pn["nodeId"]
        # 已有待决申请 → 不重复创建（幂等，重启恢复同一申请）
        existing = next((rv for rv in run.get("reviews") or []
                         if rv.get("nodeId") == nid and rv.get("status") == "pending"), None)
        if existing is not None:
            return
        upstream_id = pn.get("targetSourceNodeId") or pn.get("upstreamNodeId")
        up_node = (run.get("nodes") or {}).get(upstream_id) or {}
        artifact_refs = ((up_node.get("handoff") or {}).get("artifactRefs") or [])
        request = {
            "requestId": "rev_" + secrets.token_hex(5),
            "nodeId": nid,
            "reviewIndex": pn.get("reviewIndex"),
            "label": pn.get("label"),
            "targetNodeId": upstream_id,
            "targetAttemptId": up_node.get("currentAttemptId"),
            "targetArtifact": pn.get("targetArtifact") or "",
            "instructions": pn.get("instructions") or "",
            "reviewerLabel": pn.get("reviewer") or "",
            "artifactRefs": artifact_refs,
            "artifactFingerprint": content_hash({"refs": artifact_refs,
                                                 "attempt": up_node.get("currentAttemptId")}),
            "status": "pending",
            "createdAt": _now(),
            "decidedAt": None, "decision": None, "decisionNote": None,
            "decisionReceipt": None,
        }

        def _open(r):
            r.setdefault("reviews", []).append(request)
            n = r["nodes"][nid]
            n["currentReviewId"] = request["requestId"]
            _set_node(n, M.NODE_WAITING_HUMAN)
            _set_run_state(r, el, runs)
        runs.update(run_id, _open)
        el.append(run_id, M.EV_REVIEW_CREATED, node_id=nid,
                  requestId=request["requestId"],
                  targetNodeId=upstream_id, targetAttemptId=request["targetAttemptId"],
                  fingerprint=request["artifactFingerprint"])

    def decide_review(self, project_dir: Path, project: str, run_id: str,
                      request_id: str, decision: str, note: str = "",
                      expected_fingerprint: Optional[str] = None) -> dict:
        """审核决策（幂等）：同决策重复提交返回已处理结果；相反决策/旧对象拒绝。
        approve → 下一节点唯一就绪并驱动；reject → 驳回停止。"""
        runs = RunStore(project_dir, project)
        run = runs.load(run_id)
        if run is None:
            raise WorkflowError("unknown_run", f"run 不存在: {run_id}")
        rv = next((x for x in run.get("reviews") or []
                   if x.get("requestId") == request_id), None)
        if rv is None:
            raise WorkflowError("unknown_review", f"审核申请不存在: {request_id}")
        if rv.get("status") != "pending":
            return {"alreadyDecided": True, "decision": rv.get("decision"),
                    "requestId": request_id}
        if expected_fingerprint and expected_fingerprint != rv.get("artifactFingerprint"):
            raise WorkflowError(
                "review_object_changed",
                "审核对象已变化（待审产物指纹不一致）——请刷新后复核当前对象")
        if decision not in ("approve", "reject"):
            raise WorkflowError("bad_decision", "decision 只支持 approve/reject")
        el = EventLog(project_dir, run_id)
        receipt = {"receiptId": "rcp_" + secrets.token_hex(4),
                   "requestId": request_id, "decision": decision,
                   "decidedAt": _now(), "note": (note or "")[:500],
                   "fingerprint": rv.get("artifactFingerprint")}
        nid = rv["nodeId"]

        def _decide(r):
            for x in r.get("reviews") or []:
                if x.get("requestId") == request_id:
                    x["status"] = "decided"
                    x["decision"] = decision
                    x["decisionNote"] = (note or "")[:500]
                    x["decidedAt"] = receipt["decidedAt"]
                    x["decisionReceipt"] = receipt
            n = r["nodes"][nid]
            n["lastReviewDecision"] = decision
            if decision == "approve":
                _set_node(n, M.NODE_SUCCESS)
            else:
                _set_node(n, M.NODE_STOPPED, error=f"审核驳回：{(note or '')[:120]}")
                for other in r["nodes"].values():
                    if other["status"] == M.NODE_PENDING:
                        other["status"] = M.NODE_STOPPED
                        other["error"] = "上游审核驳回，停止派发"
            _set_run_state(r, el, runs)
        runs.update(run_id, _decide)
        el.append(run_id, M.EV_REVIEW_DECIDED, node_id=nid, requestId=request_id,
                  decision=decision, receiptId=receipt["receiptId"])
        # 注意：本方法只做状态落地，不自行驱动 worker——「通过后下一节点唯一启动」
        # 由 async 路由层在事件循环内调用 engine.drive()（同步测试/脚本上下文无运行循环，
        # ensure_future 会失败；驱动职责上移）。
        return {"alreadyDecided": False, "decision": decision, "requestId": request_id,
                "receipt": receipt}

    # ---------- 显式继续 / 重试 ----------
    def continue_run(self, project_dir: Path, project: str, run_id: str,
                     node_id: Optional[str] = None) -> dict:
        """显式继续：needs_reconciliation / failed 节点开新 attempt（旧 attempt 保留为
        真实历史）；等待审核状态不允许 continue（须走决策）。"""
        runs = RunStore(project_dir, project)
        run = runs.load(run_id)
        if run is None:
            raise WorkflowError("unknown_run", f"run 不存在: {run_id}")
        if run.get("status") == M.RUN_WAITING_HUMAN:
            raise WorkflowError("waiting_human",
                                "run 等待人工审核，请通过审核决策继续")
        if run.get("status") in M.RUN_TERMINAL:
            raise WorkflowError("run_terminal", f"run 已终态（{run.get('status')}）")
        if run.get("stopRequested"):
            raise WorkflowError("stopping", "run 已请求停止；请另起新 run")
        target = node_id or run["plan"]["order"][int(run.get("cursor") or 0)]
        node = (run.get("nodes") or {}).get(target)
        if node is None:
            raise WorkflowError("unknown_node", f"节点不存在: {target}")
        if node["status"] not in (M.NODE_NEEDS_RECONCILIATION, M.NODE_FAILED):
            raise WorkflowError("not_retryable",
                                f"节点状态 {node['status']} 不支持显式重试")

        def _retry(r):
            n = r["nodes"][target]
            n["currentAttemptId"] = None
            n["error"] = None
            _set_node(n, M.NODE_PENDING)
            _set_run_state(r, None, None)
        runs.update(run_id, _retry)
        EventLog(project_dir, run_id).append(run_id, M.EV_NODE_STATE, node_id=target,
                                             from_=node["status"], to=M.NODE_PENDING,
                                             reason="explicit_retry")
        # 驱动由 async 路由层调用 engine.drive()（与本文件 decide_review 同理）。
        return runs.load(run_id)

    # ---------- 停止终局 ----------
    def _finalize_stop(self, project_dir: Path, run: dict, runs: RunStore,
                       el: EventLog) -> None:
        run_id = run["runId"]

        def _stop(r):
            had_reject = any(
                x.get("status") == "decided" and x.get("decision") == "reject"
                for x in (r.get("reviews") or []))
            for n in (r.get("nodes") or {}).values():
                if n["status"] in (M.NODE_PENDING, M.NODE_DISPATCHING):
                    n["status"] = M.NODE_STOPPED
                    n["error"] = "流程已停止，未派发"
            for x in (r.get("reviews") or []):
                if x.get("status") == "pending":
                    x["status"] = "cancelled"
            r["status"] = M.RUN_REJECTED if had_reject else M.RUN_STOPPED
        runs.update(run_id, _stop)
        final = runs.load(run_id)
        if final is not None:
            runs.save(final)
            el.append(run_id, M.EV_RUN_STATE, status=final.get("status"))

    def _advance(self, runs: RunStore, el: EventLog, run: dict, cursor: int) -> None:
        def _adv(r):
            r["cursor"] = cursor
            _set_run_state(r, el, runs)
        runs.update(run["runId"], _adv)

    def _refresh_run_status(self, runs: RunStore, el: EventLog, run: dict) -> None:
        def _fin(r):
            _set_run_state(r, el, runs)
        runs.update(run["runId"], _fin)


# ============ 状态工具 ============
def _set_node(node: dict, status: str, error: Optional[str] = None) -> None:
    node["status"] = status
    node["statusAt"] = _now()
    if error is not None:
        node["error"] = error
    elif status in (M.NODE_SUCCESS, M.NODE_PENDING):
        node["error"] = None


def _set_run_state(run: dict, el: Optional[EventLog], runs: Optional[RunStore]) -> None:
    """run 总状态推导（§7.1）。el/runs 为 None 时跳过事件（调用方随后 save）。"""
    node_statuses = [n["status"] for n in (run.get("nodes") or {}).values()]
    pending_reviews = any(x.get("status") == "pending"
                          for x in (run.get("reviews") or []))
    reconciling = any(s == M.NODE_NEEDS_RECONCILIATION for s in node_statuses)
    failed = any(s == M.NODE_FAILED for s in node_statuses)
    stopped = any(s == M.NODE_STOPPED for s in node_statuses)
    if pending_reviews:
        new = M.RUN_WAITING_HUMAN
    elif reconciling:
        new = M.RUN_NEEDS_RECONCILIATION
    elif stopped:
        rejected = any(x.get("status") == "decided" and x.get("decision") == "reject"
                       for x in (run.get("reviews") or []))
        new = M.RUN_REJECTED if rejected else M.RUN_STOPPED
    elif failed:
        new = M.RUN_FAILED
    else:
        new = M.run_status_of(node_statuses, False, False)
    if new != run.get("status"):
        run["status"] = new
        if el is not None:
            try:
                el.append(run["runId"], M.EV_RUN_STATE, status=new)
            except Exception:
                pass


def _get_attempt(run: dict, node_id: str, attempt_id: str) -> dict:
    for a in run["nodes"][node_id]["attempts"]:
        if a.get("attemptId") == attempt_id:
            return a
    raise KeyError(attempt_id)


def _result_digest(task_result: dict) -> dict:
    """收货摘要（不复制全量 turn 结果，保留可核查要点）。"""
    return {
        "status": task_result.get("status"),
        "verified": task_result.get("verified"),
        "summary": (task_result.get("summary") or "")[:600],
        "changed_refs": task_result.get("changed_refs") or {},
        "warnings": list(task_result.get("warnings") or [])[:6],
        "n_tool_calls": task_result.get("n_tool_calls"),
        "task_id": task_result.get("task_id"),
    }


def _now() -> float:
    return time.time()


def _rt():
    """取 AgentRuntime 生产单例（真实 shot 管线）。"""
    from src.agents.runtime import get_agent_runtime
    return get_agent_runtime()


def canonical_baseline_rt(project: str) -> dict:
    from deps import get_project_manager
    from src.agents.runtime import get_agent_runtime
    pdir = get_project_manager().get_project_dir(project)
    return canonical_baseline(get_agent_runtime(), project)


# ============ 单例 ============
_engine: Optional[WorkflowEngine] = None


def get_engine() -> WorkflowEngine:
    global _engine
    if _engine is None:
        _engine = WorkflowEngine()
    return _engine


def reset_engine() -> None:
    global _engine
    _engine = None
