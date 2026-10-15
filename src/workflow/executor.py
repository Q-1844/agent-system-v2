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
            # Phase51 E：worker 会话结束即解除生成范围闸（start_scope/repair_scope
            # 统一生命周期）——repair 轮 scope 原先在 worker 结束后残留进程态，
            # 会错误拦截同项目后续 run 的提交；修复轮再次 continue 时由
            # _repair_rejected_run 重新设置，语义不受影响。
            try:
                from .repair_scope import clear_scope
                clear_scope(project, run_id)
            except Exception:
                pass

    async def _drive_inner(self, project_dir: Path, project: str, run_id: str,
                           cancel_event: asyncio.Event, runs: RunStore) -> None:
        el = EventLog(project_dir, run_id)
        while True:
            run = runs.load(run_id)
            if run is None:
                return
            # Phase48 E：终态 run 不得被 drive 推进——驳回后路由层仍会调用 drive，
            # 缺此守卫会把 rejected 翻回 waiting_human（重开新审核，静默撤销用户
            # 驳回；真机 P48_E_Explainer run_545cc965eab9 22:52:21 实证）。
            if run.get("status") in M.RUN_TERMINAL:
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

            # Phase 47 §4.6：决策已落、选用未执行的窗口恢复（幂等；async 执行）
            ps = node_run.get("pendingSelection")
            if (ps and ps.get("state") != "done"
                    and node_run["status"] in (M.NODE_DISPATCHING,
                                               M.NODE_NEEDS_RECONCILIATION)):
                await self._execute_pending_selection(project_dir, project,
                                                      run_id, nid, el)
                run2 = runs.load(run_id)
                if (run2["nodes"][nid]["status"] == M.NODE_SUCCESS):
                    self._advance(runs, el, run2, cursor + 1)
                    continue
                return False   # 选用仍失败 → 停靠（保留具体缺项与恢复动作）

            if pn["type"] == "agent":
                done = await self._run_agent_node(project_dir, project, run, pn,
                                                  runs, el, cancel_event)
                if not done:
                    return   # 停靠（等待审核/失败/停止/需要核对）
                continue
            if pn["type"] == "human_review":
                self._open_review(project_dir, run, pn, runs, el)
                # 协作流程 v2：autoApprove 卡在 _open_review 内已自动放行并置
                # success——worker 继续推进下一节点，不停靠等审核。
                run = runs.load(run_id)
                if run["nodes"][nid]["status"] == M.NODE_SUCCESS:
                    continue
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
            ok = await self._reconcile_attempt(project_dir, run, nid, cur_att,
                                               runs, el)
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
        # ---- Phase 47 §4.5：逐节点输入冻结（派发前解析上游输出为具体版本）----
        frozen_inputs = None
        try:
            from .outputs import freeze_node_inputs
            pdir_rt = _project_dir_of(project)
            if pdir_rt is not None:
                frozen_inputs = freeze_node_inputs(pdir_rt, project, run, pn)
        except Exception:
            log.exception("输入冻结异常 run=%s node=%s（继续派发，不阻塞）", run_id, nid)
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

        def _freeze(r):
            a = _get_attempt(r, nid, attempt_id)
            if frozen_inputs is not None:
                a["inputs"]["frozenUpstream"] = {
                    "resolvedInputs": frozen_inputs.get("resolvedInputs") or [],
                    "stale": frozen_inputs.get("stale") or [],
                    "upstreamNodeIds": frozen_inputs.get("upstreamNodeIds") or [],
                }
        runs.update(run_id, _freeze)

        # ---- Phase 47 §4.7：assembly 派发前素材守卫（fail fast，不进模型）----
        # 装配必须接收「真实可用素材」：冻结输入中含 video_version（来自上游交接
        # 包）或节点 scope 显式声明 videoVersions（合法既有素材复用）；都没有 =
        # 缺前置素材，节点失败并给出具体缺项，禁止"去项目里找最新的视频"。
        # （frozen_inputs=None = 项目事实不可读（冻结失败）——此时不误伤派发，
        #   完成校验仍由 completion.outputs 的 sequence/render 核对兜底。）
        if pn.get("refId") == "assembly" and frozen_inputs is not None:
            frozen_videos = [i for i in ((frozen_inputs or {}).get("resolvedInputs") or [])
                             if i.get("kind") == "video_version"]
            scope_videos = (((pn.get("scope") or {}).get("videoVersions")) or [])
            stale_videos = [s for s in ((frozen_inputs or {}).get("stale") or [])
                            if s.get("kind") == "video_version"]
            if not frozen_videos and not scope_videos:
                def _fail_pre(r):
                    a = _get_attempt(r, nid, attempt_id)
                    a["status"] = "failed"
                    a["endedAt"] = _now()
                    a["error"] = ("装配前置素材缺失：上游交接包中没有任何视频版本"
                                  "（video_version），节点 scope 也未显式声明 "
                                  "videoVersions——assembly 不允许自行到项目里"
                                  "找「最新视频」。请先完成视频生产与用户选片，"
                                  "或在节点 scope.videoVersions 中明确声明要装配"
                                  "的镜头版本清单。")
                    _set_node(r["nodes"][nid], M.NODE_FAILED, error=a["error"])
                    _set_run_state(r, el, runs)
                runs.update(run_id, _fail_pre)
                el.append(run_id, M.EV_NODE_FAILED, node_id=nid,
                          attemptId=attempt_id, code="assembly_no_materials",
                          error="装配前置素材缺失（无 video_version 交接）")
                return False
            if stale_videos and not scope_videos:
                def _fail_stale(r):
                    a = _get_attempt(r, nid, attempt_id)
                    a["status"] = "failed"
                    a["endedAt"] = _now()
                    a["error"] = ("装配前置素材已失效：交接包中的视频版本在派发前"
                                  f"核验时发生变化/消失（{len(stale_videos)} 项）"
                                  "——请核对上游产物后重试，不得装配失效版本。")
                    _set_node(r["nodes"][nid], M.NODE_FAILED, error=a["error"])
                    _set_run_state(r, el, runs)
                runs.update(run_id, _fail_stale)
                el.append(run_id, M.EV_NODE_FAILED, node_id=nid,
                          attemptId=attempt_id, code="assembly_stale_materials",
                          error="装配前置素材失效（hash 不一致/版本消失）")
                return False

        run = runs.load(run_id)
        try:
            outcome = await adapter.dispatch_node(rt=_rt(), project=project,
                                                  run=run, node_plan=pn,
                                                  attempt=attempt,
                                                  event_cb=_cb,
                                                  cancel_event=cancel_event,
                                                  frozen_inputs=frozen_inputs)
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

        # ---- 收货校验（§5.3 + Phase 47 §4.2 目标级核对）----
        # Phase51 A：域命中与目标级判定按「本 attempt ∪ 本 run 本节点此前 attempts
        # 的真实写入」累计（require_new 重试判死修复；P50 实证 4 次整轮重启根因）。
        run = runs.load(run_id)
        prior_refs = self._prior_changed_refs(run, nid, attempt_id)
        verdict = adapter.verify_completion(pn, task_result,
                                            pdir=project_dir, project=project,
                                            prior_changed_refs=prior_refs,
                                            window_start=self._run_window_start(run, nid))
        if not verdict.get("ok"):
            # Phase51 A：完成判定缺项但本 attempt 已提交的视频仍在本地队列在途
            # （批量提交/回合结束早于结算）→ 服务端等待结算后重判，不触发模型回合，
            # 不把「在途未收货」误判为节点失败。
            # 仅 completed 任务值得等待——status=failed/stopped 的重判不可能通过
            # （其真实成果由重试 attempt 按跨 attempt 累计收养，见 _prior_changed_refs）。
            if (task_result.get("status") or "").lower() == "completed" and \
                    await self._await_inflight_videos(project, task_result,
                                                      cancel_event=cancel_event):
                run = runs.load(run_id)
                prior_refs = self._prior_changed_refs(run, nid, attempt_id)
                verdict = adapter.verify_completion(pn, task_result,
                                                    pdir=project_dir, project=project,
                                                    prior_changed_refs=prior_refs,
                                                    window_start=self._run_window_start(run, nid))
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
        outputs_verdict = verdict.get("outputsVerdict")
        handoff = adapter.build_handoff(run, nid, attempt, task_result,
                                        task_result.get("summary") or "",
                                        verify_outputs=outputs_verdict,
                                        node_plan=pn)
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
            if outputs_verdict:
                a["outputs"]["outputsVerdict"] = {
                    "code": outputs_verdict.get("code"),
                    "created": len(outputs_verdict.get("outputs") or []),
                    "reused": len(outputs_verdict.get("reused") or []),
                }
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

    # ---------- Phase51 A：跨 attempt 真实写入汇总（require_new 重试判死修复） ----------
    @staticmethod
    def _run_window_start(run: dict, nid: str) -> Optional[float]:
        """计入窗口起点（epoch）=本节点在本 run 最早 attempt 的 startedAt——
        video_version 等媒体产物凭 shot 成员资格计入的时界。
        🔴 不用 run.createdAt：run 存储的时间戳是本地时间误标 Z 后缀（A 报告 §1
        实证），按 UTC 解析会把窗口推到未来、拒绝一切真实产出。attempt.startedAt
        为同机 epoch（time.time()），无时区歧义。"""
        sts = [a.get("startedAt")
               for a in ((run.get("nodes") or {}).get(nid) or {}).get("attempts") or []
               if isinstance(a.get("startedAt"), (int, float))]
        return min(sts) if sts else None

    @staticmethod
    def _prior_changed_refs(run: dict, nid: str,
                            current_attempt_id: Optional[str]) -> Dict[str, List[str]]:
        """本节点在本 run 此前 attempts 的真实写入汇总（来自各 attempt 收货摘要
        的 changed_refs——均为真实工具回执留档）。跨 attempt 累计的依据：
        重试/修复 attempt 的合理行为是延续+补缺；此前 attempt（含因上游错误、
        完成判定缺项而 failed 的）已真实写入的产物继续有效。只认本 run 本节点
        的任务回执；跨 run 复用仍走 allow_verified_reuse 显式语义，不在此放宽。"""
        merged: Dict[str, List[str]] = {}
        for a in ((run.get("nodes") or {}).get(nid) or {}).get("attempts") or []:
            if current_attempt_id and a.get("attemptId") == current_attempt_id:
                continue
            cr = ((a.get("result") or {}).get("changed_refs") or {})
            if not isinstance(cr, dict):
                continue
            for k, ids in cr.items():
                if not ids:
                    continue
                dst = merged.setdefault(k, [])
                for i in ids:
                    if i not in dst:
                        dst.append(i)
        return merged

    # ---------- Phase51 A：在途视频服务端等待（零 LLM 收货等待） ----------
    @staticmethod
    async def _await_inflight_videos(project: str, task_result: dict, *,
                                     poll_s: float = 5.0, max_wait_s: float = 7200.0,
                                     stall_s: float = 1500.0,
                                     cancel_event: Optional[asyncio.Event] = None) -> bool:
        """节点完成判定失败时：若本节点本 attempt 已提交镜头的视频 job 仍在本地
        队列在途（queued/running），服务端等待其终态后供调用方重判。
        P50 实证背景：导演回合可能在部分视频未结算时结束（批量提交纪律）——
        此时完成判定缺项≠节点失败，等待结算后再判，不触发任何模型回合。
        返回 True=等待期间有 job 终态化（应重判）；False=无在途或超时/停顿。
        超时与停顿上限独立：上游等待依据真实完成分布（P50 实测 150-753s/条），
        不因短时无进展杀正常 job；JobQueue 看门狗另有 10min 卡死判定。
        必须 await（asyncio.sleep）——与 JobQueue worker 同一事件循环，
        阻塞 sleep 会卡死全部视频收货。"""
        shots = set((task_result.get("changed_refs") or {}).get("shots") or [])
        if not shots:
            return False
        try:
            from deps import get_job_queue
            q = get_job_queue()
        except Exception:
            q = None
        if q is None:
            return False

        def _inflight():
            out = {}
            for j in list(getattr(q, "jobs", {}).values()):
                if (getattr(j, "project", None) != project
                        or getattr(j, "kind", None) != "video"
                        or getattr(j, "shot_id", None) not in shots):
                    continue
                if getattr(j, "state", None) in ("queued", "running"):
                    out[j.job_id] = (j.state, j.progress)
            return out

        if not _inflight():
            return False
        deadline = time.monotonic() + max_wait_s
        last_sig: Optional[list] = None
        last_change = time.monotonic()
        while time.monotonic() < deadline:
            await asyncio.sleep(poll_s)
            if cancel_event is not None and cancel_event.is_set():
                return False   # 用户停止：放弃等待，按停止语义收尾
            cur = _inflight()
            sig = sorted((jid, st, pr) for jid, (st, pr) in cur.items())
            if sig != last_sig:
                last_sig = sig
                last_change = time.monotonic()
            elif time.monotonic() - last_change > stall_s:
                return False   # 停顿守卫：状态长期无变化（诚实超时，不无限等）
            if not cur:
                return True
        return False

    # ---------- 恢复对账（§7.4 / R03/R04） ----------
    async def _reconcile_attempt(self, project_dir: Path, run: dict, nid: str,
                                 att: dict, runs: RunStore, el: EventLog) -> bool:
        """在途 attempt 对账：按 workflow_ref 找回 AgentTask——
        已结算 → 收养其结果继续校验；仍在 running → 先查本进程 turn_registry
        （占用中 = live 委派，不动，Phase48 E 防御）；未占用 → turn 已随进程
        死亡 → needs_reconciliation（不盲重发）。返回 False=停靠。
        Phase51 A：async 化——收养校验缺项但视频仍在本地队列在途时，服务端
        等待结算后重判（零 LLM），不把「在途未收货」判成 settled_but_invalid。"""
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
            prior_refs = self._prior_changed_refs(run, nid, att.get("attemptId"))
            verdict = adapter.verify_completion(pn, tr, pdir=project_dir,
                                                project=run.get("project"),
                                                prior_changed_refs=prior_refs,
                                                window_start=self._run_window_start(run, nid))
            if not verdict.get("ok") and await self._await_inflight_videos(
                    run.get("project") or "", tr):
                verdict = adapter.verify_completion(pn, tr, pdir=project_dir,
                                                    project=run.get("project"),
                                                    prior_changed_refs=prior_refs,
                                                    window_start=self._run_window_start(run, nid))
            if verdict.get("ok"):
                handoff = adapter.build_handoff(run, nid, att, tr, tr.get("summary") or "",
                                                verify_outputs=verdict.get("outputsVerdict"),
                                                node_plan=pn)

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
        if task.status == "cancelled":
            # Phase48 A06：用户停止语义（cancellation）≠ 真实失败——attempt 落
            # stopped 并保留停止来源；不把 stopped run 翻成 success，不派发下游。
            def _stopped(r):
                a = _get_attempt(r, nid, att["attemptId"])
                a["status"] = "stopped"
                a["endedAt"] = _now()
                a["taskRefs"] = [task.task_id]
                a["error"] = ("恢复对账：正式任务已按用户停止结束（cancelled）"
                              f"：task={task.task_id}；{(task.error or '')[:160]}")
                _set_node(r["nodes"][nid], M.NODE_STOPPED, error=a["error"])
                _set_run_state(r, el, runs)
            runs.update(run_id, _stopped)
            el.append(run_id, M.EV_RECONCILE, node_id=nid,
                      attemptId=att["attemptId"], outcome="cancelled",
                      taskId=task.task_id)
            return False
        if task.status == "failed":
            def _fail(r):
                a = _get_attempt(r, nid, att["attemptId"])
                a["status"] = "failed"
                a["endedAt"] = _now()
                a["error"] = f"恢复对账：正式任务已终态（failed）：{(task.error or '')[:200]}"
                _set_node(r["nodes"][nid], M.NODE_FAILED, error=a["error"])
                _set_run_state(r, el, runs)
            runs.update(run_id, _fail)
            el.append(run_id, M.EV_RECONCILE, node_id=nid,
                      attemptId=att["attemptId"], outcome="failed",
                      taskId=task.task_id)
            return False
        # running/pending：先核对本进程 turn 是否仍存活（Phase48 E 防御：
        # 真机实证 18:53 一例——worker 仍在 await 派发时另一路对账进入，把活 turn
        # 误判为"随进程死亡"）。turn_registry 占用中 = live 委派，不动（与
        # settlement._reconcile_sync_delegate 同判据）；registry 未占用 = 真
        # 中断遗留，诚实进入需要核对。
        try:
            from src.agents import turn_registry
            _proj = run.get("project") or "unknown"
            if task.session_id and turn_registry.is_active(_proj, task.session_id):
                return False
        except Exception:
            pass
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

    # ---------- 审核（§7.2 + Phase 47 §4.6） ----------
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
        # Phase 47 §4.6：候选文件「身份+内容」快照（含逐实体内容 hash）——与
        # 「选用动作造成的指针变更」分开：指纹只含产物内容，不含 selected/final 指针。
        content_snapshot = _artifact_content_snapshot(project_dir,
                                                      run.get("project") or "", artifact_refs)
        selection_cfg = pn.get("selection") or None
        selection_candidates: List[dict] = []
        if selection_cfg and not (selection_cfg.get("versionId") or "").strip():
            # 运行期才能确定 versionId 的选用目标：打开审核时解析真实候选清单，
            # 由用户在卡片上选择（不自动替用户选"最新"）。
            try:
                from .selection import resolve_selection_candidates
                selection_candidates = resolve_selection_candidates(
                    project_dir, run.get("project") or "", selection_cfg)
            except Exception:
                selection_candidates = []
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
            "contentSnapshot": content_snapshot,
            "selectionRequired": bool(selection_cfg),
            "selectionSpec": selection_cfg,
            "selectionCandidates": selection_candidates,
            "selectionState": ("pending" if selection_cfg else "none"),
            "selectionReceipt": None,
            "status": "pending",
            "createdAt": _now(),
            "decidedAt": None, "decision": None, "decisionNote": None,
            "decisionReceipt": None,
        }

        def _open(r):
            r.setdefault("reviews", []).append(request)
            n = r["nodes"][nid]
            n["currentReviewId"] = request["requestId"]
            if selection_cfg:
                n["pendingSelection"] = {"reviewId": request["requestId"],
                                         "spec": selection_cfg, "state": "pending"}
            _set_node(n, M.NODE_WAITING_HUMAN)
            _set_run_state(r, el, runs)

        # ---------- 协作流程 v2（用户 2026-10-11 定版）：autoApprove 前置卡自动放行 ----------
        # 资产冻结复用场景下，系统核验通过即继续，不停等人工；决策与选用全程事件留痕。
        # 带 selection 的卡「沿用现有选用指针」（不执行新 selection——指针已是现状）。
        if pn.get("autoApprove"):
            request["status"] = "decided"
            request["decision"] = "approve"
            request["decidedAt"] = _now()
            request["decisionNote"] = ("auto_approve: 资产冻结复用核验通过自动放行"
                                       "（协作流程 v2，三查一致性判据由终审统一执行）")
            request["decisionReceipt"] = {
                "receiptId": "rcp_" + secrets.token_hex(4),
                "requestId": request["requestId"], "decision": "approve",
                "decidedAt": request["decidedAt"], "note": request["decisionNote"],
                "auto": True}
            if selection_cfg:
                request["selectionState"] = "done"
                request["selectionReceipt"] = {
                    "ok": True, "kind": selection_cfg.get("kind"), "auto": True,
                    "note": "沿用现有选用指针（auto_approve 不执行新选用）"}

            def _auto(r):
                r.setdefault("reviews", []).append(request)
                n = r["nodes"][nid]
                n["currentReviewId"] = request["requestId"]
                n["lastReviewDecision"] = "approve"
                if selection_cfg:
                    n["pendingSelection"] = {"reviewId": request["requestId"],
                                             "spec": selection_cfg, "state": "done",
                                             "receipt": request["selectionReceipt"]}
                _set_node(n, M.NODE_SUCCESS)
                _set_run_state(r, el, runs)
            runs.update(run_id, _auto)
            el.append(run_id, M.EV_REVIEW_CREATED, node_id=nid,
                      requestId=request["requestId"],
                      targetNodeId=upstream_id, targetAttemptId=request["targetAttemptId"],
                      fingerprint=request["artifactFingerprint"],
                      selectionRequired=request["selectionRequired"],
                      autoApprove=True)
            el.append(run_id, M.EV_REVIEW_DECIDED, node_id=nid,
                      requestId=request["requestId"], decision="approve",
                      receiptId=request["decisionReceipt"]["receiptId"],
                      withSelection=bool(selection_cfg), auto=True)
            log.info("auto_approve: %s (%s) 自动放行 run=%s", nid,
                     pn.get("label"), run_id)
            return
        runs.update(run_id, _open)
        el.append(run_id, M.EV_REVIEW_CREATED, node_id=nid,
                  requestId=request["requestId"],
                  targetNodeId=upstream_id, targetAttemptId=request["targetAttemptId"],
                  fingerprint=request["artifactFingerprint"],
                  selectionRequired=request["selectionRequired"])

    def decide_review(self, project_dir: Path, project: str, run_id: str,
                      request_id: str, decision: str, note: str = "",
                      expected_fingerprint: Optional[str] = None,
                      with_selection: bool = False,
                      selection_override: Optional[dict] = None) -> dict:
        """审核决策（幂等）：同决策重复提交返回已处理结果；相反决策/旧对象拒绝。
        - approve（无 selection 配置）：检查或创作确认——不修改任何 selected/final 指针。
        - approve + selection 配置 + with_selection=True：批准并选用——用户在卡片上
          显式确认后，服务端经 selection.perform_selection 走既有用户入口同一底层
          服务完成指针变更，selectionReceipt 独立落账；选用失败 → 节点
          needs_reconciliation（决策已记录，continue 时幂等重试选用）。
        - reject：驳回停止下游（本轮无自动返工）。
        对象检查：决策时重算产物内容快照，与打开时不一致 → 409（本审核授权的
        选用指针变更不在此列——指针不在内容快照内）。"""
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
        # 内容快照复核（§4.6：无关内容/依赖变更 → 拒绝旧决策并重审）
        cur_snap = _artifact_content_snapshot(project_dir, project,
                                              rv.get("artifactRefs") or [])
        if rv.get("contentSnapshot") and cur_snap and \
                rv["contentSnapshot"].get("hashes") != cur_snap.get("hashes"):
            changed = [k for k in set(rv["contentSnapshot"]["hashes"]) | set(cur_snap["hashes"])
                       if rv["contentSnapshot"]["hashes"].get(k)
                       != cur_snap["hashes"].get(k)]
            raise WorkflowError(
                "review_object_changed",
                f"待审产物内容与打开审核时不一致（{changed[:4]}）——旧决策拒绝，请重审")
        # 选用前置检查：selection 配置了但用户未确认选用动作 → 拒绝该决策路径
        if decision == "approve" and rv.get("selectionRequired"):
            if not with_selection:
                raise WorkflowError(
                    "selection_required",
                    "本审核要求明确选用（下游依赖被批准/选用的具体版本）——"
                    "请在审核卡片确认「通过并选用」或先完成选片再审核通过")
        el = EventLog(project_dir, run_id)
        receipt = {"receiptId": "rcp_" + secrets.token_hex(4),
                   "requestId": request_id, "decision": decision,
                   "decidedAt": _now(), "note": (note or "")[:500],
                   "fingerprint": rv.get("artifactFingerprint"),
                   "withSelection": bool(with_selection and rv.get("selectionRequired"))}
        nid = rv["nodeId"]
        if decision == "approve":
            # Phase51 C：修复范围硬闸随修复通过闸门解除（下一节点不再受限）
            from .repair_scope import clear_scope
            clear_scope(project, run_id)

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
                if rv.get("selectionRequired"):
                    # 选用待执行：节点不立即 success——selection 完成后才推进
                    # （两步动作独立落账；失败时节点 needs_reconciliation）
                    ps = n.get("pendingSelection") or {}
                    ps["state"] = "pending"
                    ps["reviewId"] = request_id
                    if selection_override:
                        # override 与默认 spec 合并（kind/entityId 兜底，versionId
                        # 通常由用户在卡片上从候选中选定）
                        ps["spec"] = {**(rv.get("selectionSpec") or {}),
                                      **selection_override}
                    n["pendingSelection"] = ps
                    _set_node(n, M.NODE_DISPATCHING)   # 中间态：决策已落，选用执行中
                else:
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
                  decision=decision, receiptId=receipt["receiptId"],
                  withSelection=receipt.get("withSelection"))
        # 选用执行：决策已持久化；实际执行为 async（资源批准须在事件循环内 await
        # 同一 project 锁）——由调用方在循环内调用 execute_pending_selection，
        # 或由 drive 的恢复路径补执行（DISPATCHING+pendingSelection 幂等）。
        selection_pending = bool(decision == "approve" and rv.get("selectionRequired"))
        # 注意：本方法只做状态落地，不自行驱动 worker——「通过后下一节点唯一启动」
        # 由 async 路由层在事件循环内调用 engine.drive()（同步测试/脚本上下文无运行循环，
        # ensure_future 会失败；驱动职责上移）。
        return {"alreadyDecided": False, "decision": decision, "requestId": request_id,
                "selectionPending": selection_pending,
                "receipt": receipt}

    async def execute_pending_selection(self, project_dir: Path, project: str,
                                        run_id: str, node_id: str) -> dict:
        """（async）执行节点的待完成选用（决策 API / continue 之后由异步层调用）。"""
        el = EventLog(project_dir, run_id)
        await self._execute_pending_selection(project_dir, project, run_id,
                                              node_id, el)
        return RunStore(project_dir, project).load(run_id) or {}

    async def _execute_pending_selection(self, project_dir: Path, project: str,
                                         run_id: str, nid: str, el: EventLog) -> None:
        """执行节点 pendingSelection（幂等；async——资源批准走同一事件循环的
        project 锁）：成功 → 节点 success；失败 → needs_reconciliation + 具体缺项
        （continue 时重试）。"""
        from . import selection as _selection
        runs = RunStore(project_dir, project)
        run = runs.load(run_id)
        if run is None:
            return
        node = (run.get("nodes") or {}).get(nid) or {}
        ps = node.get("pendingSelection") or {}
        if ps.get("state") == "done":
            return
        spec = ps.get("spec") or {}
        receipt = await _selection.perform_selection_async(project_dir, project, spec)
        receipt["at"] = _now()

        def _rec(r):
            n = r["nodes"][nid]
            ps_local = n.get("pendingSelection") or {}
            ps_local["receipt"] = receipt
            for x in r.get("reviews") or []:
                if x.get("requestId") == ps_local.get("reviewId"):
                    x["selectionReceipt"] = receipt
                    x["selectionState"] = "done" if receipt.get("ok") else "failed"
            if receipt.get("ok"):
                ps_local["state"] = "done"
                n["pendingSelection"] = ps_local
                # §4.6：受本审核明确授权的选用变化成为后续依据——选用产物写入
                # 本节点交接包 outputs（disposition=selected），下游逐节点冻结
                # 即可解析到精确版本（assembly 守卫据此放行）。
                # 多选用（items）：逐 item 展开为独立输出记录（真实 entityId+
                # versionId；不能只写默认 spec 的占位符）。
                spec_sel = ps_local.get("spec") or {}
                out_recs: List[dict] = []
                if receipt.get("multi") and isinstance(receipt.get("items"), list):
                    for it in receipt["items"]:
                        if not isinstance(it, dict) or not it.get("ok"):
                            continue
                        out_recs.append({"entityId": it.get("entityId"),
                                         "versionId": it.get("versionId")})
                else:
                    out_recs.append({"entityId": spec_sel.get("entityId"),
                                     "versionId": spec_sel.get("versionId")})
                _SEL_KIND_MAP = {"look_reference": "look",
                                 "prop_reference": "prop",
                                 "composition_candidate": "composition",
                                 "scene_view": "scene_instance_view"}
                out_kind = _SEL_KIND_MAP.get(spec_sel.get("kind"),
                                             spec_sel.get("kind") or "unknown")
                _VERSIONED_KINDS = ("video_version", "sequence_render", "first_frame")
                h = n.get("handoff") or {
                    "runId": r["runId"], "nodeId": nid,
                    "attemptId": n.get("currentAttemptId"),
                    "workflowRef": {"runId": r["runId"], "nodeId": nid},
                    "artifactRefs": [], "outputs": [], "reusedRefs": [],
                    "missingRequirements": [],
                    "brief": "人工审核选用产物（selection）",
                }
                h.setdefault("outputs", [])
                for o in out_recs:
                    out_rec = {"kind": out_kind,
                               "entityId": o.get("entityId"),
                               "versionId": (o.get("versionId")
                                             if spec_sel.get("kind") in _VERSIONED_KINDS
                                             else None),
                               "approvedAssetId": (o.get("versionId")
                                                   if spec_sel.get("kind")
                                                   not in _VERSIONED_KINDS else None),
                               "disposition": "selected",
                               "reviewState": "selected",
                               "contentHash": None, "fileOk": True}
                    try:
                        from .outputs import _resolve_kind
                        rspec = {"kind": out_kind}
                        if out_kind == "video_version":
                            rspec["shotId"] = o.get("entityId")
                            rspec["id"] = o.get("versionId")
                        else:
                            rspec["id"] = o.get("versionId") or o.get("entityId")
                        matches = _resolve_kind(project_dir, project, rspec)
                        m = next((x for x in matches
                                  if x["entityId"] == o.get("entityId")
                                  and (out_kind != "video_version"
                                       or (x.get("versionId") or None)
                                       == (o.get("versionId") or None))), None)
                        if m is not None:
                            out_rec["contentHash"] = m.get("contentHash")
                            out_rec["fileOk"] = bool(m.get("fileOk"))
                    except Exception:
                        pass
                    h["outputs"].append(out_rec)
                n["handoff"] = h
                _set_node(n, M.NODE_SUCCESS)
            else:
                ps_local["state"] = "failed"
                n["pendingSelection"] = ps_local
                _set_node(n, M.NODE_NEEDS_RECONCILIATION,
                          error=f"选用未完成：{receipt.get('error')}（审核决策已记录，"
                                f"修正后通过「继续」幂等重试）")
            _set_run_state(r, el, runs)
        runs.update(run_id, _rec)
        el.append(run_id, "review.selection", node_id=nid,
                  ok=bool(receipt.get("ok")), spec=spec,
                  error=receipt.get("error"))

    # ---------- 显式继续 / 重试 ----------
    def continue_run(self, project_dir: Path, project: str, run_id: str,
                     node_id: Optional[str] = None,
                     allowed_shot_ids: Optional[List[str]] = None) -> dict:
        """显式继续：needs_reconciliation / failed 节点开新 attempt（旧 attempt 保留为
        真实历史）；等待审核状态不允许 continue（须走决策）。
        Phase48 E：节点 failed 使 run 状态推导为 failed（终态），但按本契约 failed
        节点必须可显式重试——run=failed 且目标节点可重试时放行；stopped/completed
        仍拒绝（停止语义见 A06：已停止请另起新 run）。
        Phase51 A：rejected（审核驳回）增加显式修复路径——驳回语义保持「停止下游、
        无自动返工」，但提供受约束的正式 resume：目标=被驳回审核的上游产物节点，
        重开其 attempt 并把驳回记录注入修复提示（保留合格部分、定点修复）；
        下游被停止的节点复位为待推进（其旧 attempt 从未派发，无历史损失）。
        该动作必须显式调用（不会被 drive 自动触发——Phase48 E 守卫保持）。
        Phase51 C：allowed_shot_ids 非空时启用修复范围服务端硬闸（repair_scope）——
        修复 attempt 期间清单外镜头的生成提交在预检被拒（run_36919eb5a073 实证：
        提示词范围约束对导演无效，授权 4 镜实际提交 20 镜）。"""
        runs = RunStore(project_dir, project)
        run = runs.load(run_id)
        if run is None:
            raise WorkflowError("unknown_run", f"run 不存在: {run_id}")
        if run.get("status") == M.RUN_WAITING_HUMAN:
            raise WorkflowError("waiting_human",
                                "run 等待人工审核，请通过审核决策继续")
        _status = run.get("status")
        if _status == M.RUN_REJECTED:
            return self._repair_rejected_run(project_dir, run_id, runs, node_id,
                                             allowed_shot_ids)
        if _status in M.RUN_TERMINAL and _status != M.RUN_FAILED:
            raise WorkflowError("run_terminal", f"run 已终态（{_status}）")
        if run.get("stopRequested"):
            raise WorkflowError("stopping", "run 已请求停止；请另起新 run")
        target = node_id or run["plan"]["order"][int(run.get("cursor") or 0)]
        node = (run.get("nodes") or {}).get(target)
        if node is None:
            raise WorkflowError("unknown_node", f"节点不存在: {target}")
        if node["status"] not in (M.NODE_NEEDS_RECONCILIATION, M.NODE_FAILED):
            raise WorkflowError("not_retryable",
                                f"节点状态 {node['status']} 不支持显式重试")

        # Phase 47 §4.6：选片未完成的 needs_reconciliation → 重置为派发态，
        # 由随后的 drive 恢复路径幂等重试选用（async；Agent 已真实成功，不重派）
        ps = node.get("pendingSelection")
        if (node["status"] == M.NODE_NEEDS_RECONCILIATION and ps
                and ps.get("state") != "done"):
            def _rearm(r):
                n = r["nodes"][target]
                _set_node(n, M.NODE_DISPATCHING)
                _set_run_state(r, None, None)
            runs.update(run_id, _rearm)
            EventLog(project_dir, run_id).append(
                run_id, M.EV_NODE_STATE, node_id=target,
                from_=M.NODE_NEEDS_RECONCILIATION, to=M.NODE_DISPATCHING,
                reason="selection_retry")
            return runs.load(run_id)

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

    def _repair_rejected_run(self, project_dir: Path, run_id: str, runs: RunStore,
                             node_id: Optional[str],
                             allowed_shot_ids: Optional[List[str]] = None) -> dict:
        """Phase51 A：驳回后显式修复（P50 实证：run2 驳回→9 节点停止→整 run 报废
        →被迫新建 definition 重跑 5 轮）。驳回停止语义不变；本方法是唯一合法的
        驳回后续作入口。"""
        run = runs.load(run_id)
        rejects = [x for x in (run.get("reviews") or [])
                   if x.get("status") == "decided" and x.get("decision") == "reject"]
        if not rejects:
            raise WorkflowError("no_rejection",
                                "run 为 rejected 但未找到驳回审核记录，无法定位修复目标")
        rv = rejects[-1]
        # ---------- 协作流程 v2（用户 2026-10-11 定版）：修复 3 次自动豁免跳过 ----------
        # 同一驳回审核的修复决策数（reject 计数）达上限时不再开修复 attempt：
        # 追加一张「修复豁免」审核卡并自动 approve（保留合格部分推进，事件留痕）——
        # 三查一致性由豁免 note 声明交终审/用户最终裁决，防止无限修复烧配额。
        REPAIR_LIMIT = 3
        target_node_id = node_id or rv.get("targetNodeId")
        reject_count = sum(
            1 for x in (run.get("reviews") or [])
            if x.get("status") == "decided" and x.get("decision") == "reject"
            and (x.get("targetNodeId") == target_node_id
                 or (target_node_id and x.get("nodeId") == target_node_id)))
        if reject_count >= REPAIR_LIMIT:
            el = EventLog(project_dir, run_id)
            exempt = {
                "requestId": "rev_" + secrets.token_hex(5),
                "nodeId": rv.get("nodeId") or target_node_id,
                "label": (rv.get("label") or "审核") + "（修复豁免）",
                "targetNodeId": rv.get("targetNodeId"),
                "targetAttemptId": rv.get("targetAttemptId"),
                "artifactRefs": rv.get("artifactRefs") or [],
                "artifactFingerprint": rv.get("artifactFingerprint"),
                "contentSnapshot": rv.get("contentSnapshot"),
                "selectionRequired": bool(rv.get("selectionRequired")),
                "selectionSpec": rv.get("selectionSpec"),
                "selectionCandidates": [],
                "selectionState": ("done" if rv.get("selectionRequired") else "none"),
                "selectionReceipt": ({"ok": True, "auto": True,
                                      "note": "豁免沿用既有选用"} if rv.get("selectionRequired") else None),
                "status": "decided", "decision": "approve",
                "decidedAt": _now(),
                "decisionNote": (f"repair_limit_exhausted: 修复 {reject_count} 次仍未通过，"
                                 f"自动豁免跳过（协作流程 v2）——保留合格部分推进；"
                                 f"三查一致性与最终取舍由用户裁决"),
                "decisionReceipt": {"receiptId": "rcp_" + secrets.token_hex(4),
                                    "requestId": None, "decision": "approve",
                                    "decidedAt": _now(), "auto": True,
                                    "note": "repair_limit_exhausted 豁免"},
                "createdAt": _now(), "reviewerLabel": "system",
            }
            exempt["decisionReceipt"]["requestId"] = exempt["requestId"]

            def _exempt(r):
                r.setdefault("reviews", []).append(exempt)
                n = r["nodes"][rv.get("nodeId") or target_node_id]
                n["lastReviewDecision"] = "approve"
                n["error"] = None
                if n.get("pendingSelection"):
                    n["pendingSelection"]["state"] = "done"
                    n["pendingSelection"]["receipt"] = exempt["selectionReceipt"]
                _set_node(n, M.NODE_SUCCESS)
                # 下游被停的节点复位（驳回时曾置 STOPPED）
                for other in r["nodes"].values():
                    if other["status"] == M.NODE_STOPPED and other is not n:
                        other["status"] = M.NODE_PENDING
                        other["error"] = None
                r["status"] = M.RUN_RUNNING
                _set_run_state(r, el, runs)
            runs.update(run_id, _exempt)
            el.append(run_id, M.EV_REVIEW_DECIDED, node_id=exempt["nodeId"],
                      requestId=exempt["requestId"], decision="approve",
                      receiptId=exempt["decisionReceipt"]["receiptId"], auto=True,
                      reason="repair_limit_exhausted")
            log.info("repair_limit_exhausted: run=%s node=%s reject_count=%d → 豁免跳过",
                     run_id, target_node_id, reject_count)
            return runs.load(run_id)
        target = node_id or rv.get("targetNodeId")
        if not target:
            raise WorkflowError("unknown_node",
                                "驳回审核未记录目标节点，请显式指定 node_id")
        if node_id and node_id != rv.get("targetNodeId"):
            raise WorkflowError(
                "not_retryable",
                f"rejected run 的显式重试目标必须是驳回审核的目标节点"
                f"（{rv.get('targetNodeId')}）——上游未过审时不得跳过派发下游")
        node = (run.get("nodes") or {}).get(target)
        if node is None:
            raise WorkflowError("unknown_node", f"节点不存在: {target}")

        def _repair(r):
            note = (f"审核驳回（{rv.get('label') or rv.get('nodeId') or '审核'}）："
                    f"{(rv.get('decisionNote') or '')[:300]}")
            t = r["nodes"][target]
            t["repairNote"] = note
            if allowed_shot_ids:
                # Phase54 A2：修复目标钉定——目标镜在修复 attempt 必须产出新版本
                # （outputs 检查：writeSource=prior 不计入目标镜 spec）。
                pn = ((r.get("plan") or {}).get("nodes") or {}).get(target)
                if pn is not None:
                    prev = ((pn.get("repairRequireNew") or {}).get("shotIds")) or []
                    pn["repairRequireNew"] = {
                        "shotIds": sorted({str(x) for x in allowed_shot_ids} | {str(x) for x in prev}),
                        "reason": note[:160]}
                # Phase51 C：修复范围服务端硬闸——持久化到节点（重启可恢复）+
                # 进程内注册表（director_visual 提交预检查询）。清单外镜头的
                # 生成提交会被直接拒绝（不进队列不耗配额）。
                t["repairScope"] = {"allowedShotIds": [str(x) for x in allowed_shot_ids],
                                    "reason": note[:200]}
            _set_node(t, M.NODE_PENDING)
            for nid2, n2 in r["nodes"].items():
                if nid2 != target and n2["status"] == M.NODE_STOPPED:
                    _set_node(n2, M.NODE_PENDING)
            order = r["plan"]["order"]
            r["cursor"] = order.index(target) if target in order else \
                int(r.get("cursor") or 0)
            _set_run_state(r, None, None)
        runs.update(run_id, _repair)
        if allowed_shot_ids:
            from .repair_scope import set_scope
            set_scope(str(run.get("project") or project_dir.name), allowed_shot_ids,
                      target, run_id,
                      f"repair_after_reject: {rv.get('label') or rv.get('nodeId')}")
        EventLog(project_dir, run_id).append(
            run_id, M.EV_NODE_STATE, node_id=target,
            from_=node.get("status") or M.NODE_SUCCESS, to=M.NODE_PENDING,
            reason="repair_after_reject",
            note=(f"驳回后显式修复：{rv.get('label') or rv.get('nodeId')}"
                  f" → 重开 {target}；下游复位待推进"))
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
def _artifact_content_snapshot(project_dir: Path, project: str, artifact_refs: list) -> dict:
    """审核对象内容快照（§4.6）：候选文件的「身份+内容」逐实体 hash。
    不含 selected/final 等选用指针——指针变更由选用动作产生并独立落账，
    不触发旧决策失效；其余内容/依赖变更 → 决策时 409 重审。"""
    out: Dict[str, str] = {}
    try:
        from .outputs import _resolve_kind
        for ref in artifact_refs or []:
            kind = ref.get("kind")
            rid = ref.get("id")
            if not kind or not rid:
                continue
            spec_kind = {"scene": "scene", "shot": "shot", "character": "character",
                         "sequence": "sequence", "look": "look", "prop": "prop",
                         "clothing": "clothing", "composition": "composition",
                         "reference_asset": "reference_asset",
                         "location": "location", "location_view": "location_view",
                         "scene_instance": "scene_instance",
                         "scene_instance_view": "scene_instance_view",
                         "story": "story"}.get(kind)
            if spec_kind is None:
                continue
            try:
                matches = _resolve_kind(project_dir, project,
                                        {"kind": spec_kind, "id": rid})
            except Exception:
                out[f"{kind}:{rid}"] = "unreadable"
                continue
            m = next((x for x in matches if x["entityId"] == rid), None)
            if m is None:
                out[f"{kind}:{rid}"] = "missing"
            else:
                out[f"{kind}:{rid}"] = m.get("contentHash") or "no-hash"
    except Exception:
        return {}
    return {"hashes": out, "at": _now()}


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


def _project_dir_of(project: str):
    """项目目录解析（outputs 模块读真实事实用；解析失败返回 None 不阻塞派发）。"""
    try:
        from deps import get_project_manager
        return get_project_manager().get_project_dir(project)
    except Exception:
        return None


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
