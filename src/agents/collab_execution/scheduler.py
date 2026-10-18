# R2 B2：最小确定性派发——先去掉就绪空等（03 文档 §B2）。
# - 节点依赖满足后由程序直接入现有 JobQueue，不调用 Producer/LLM 询问下一步。
# - 稳定派发键 = project_instance_id:run_id:task_id:attempt:input_signature；
#   账本唯一约束先领取，再投递原队列；"投递成功、关联 ID 未保存"间崩溃 →
#   按 origin_dispatch_key 从 JobQueue 找回，不再次制造 Job。
# - 依赖等待不占 Provider worker 槽；失败只影响自身与真实依赖它的节点。
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Dict, List, Optional

from .ledger import CollabLedger, LedgerError

log = logging.getLogger(__name__)

_DISPATCH_STATUSES = ("ready",)


def make_dispatch_key(task: dict, attempt: int = 1) -> str:
    return ":".join([
        task["project_instance_id"], task["run_id"], task["task_id"],
        str(attempt), task.get("input_signature") or ""])


class DispatchScheduler:
    """确定性派发器。job_queue 以 factory 惰性解析（避免构造期单例耦合）。"""

    def __init__(self, ledger: CollabLedger,
                 job_queue_factory: Callable[[], Optional[object]]):
        self.ledger = ledger
        self._jq_factory = job_queue_factory

    def _job_queue(self):
        return self._jq_factory()

    # ---- 登记 ----
    def register_task(self, *, task_id: str, project_instance_id: str, run_id: str,
                      kind: str, project_name: str, shot_id: Optional[str] = None,
                      payload: Optional[dict] = None,
                      depends_on: Optional[List[dict]] = None,
                      input_signature: str = "") -> dict:
        """登记依赖节点。depends_on 项：{"shot_id": "s01_c01", "require": "accepted"}
        （默认 require=accepted——跨工作域依赖默认消费已接纳产物）。
        payload 携带 project_name（真实队列仍按名投递）；实例身份单独随 Job 参数携带。"""
        deps = [dict(d) for d in (depends_on or [])]
        for d in deps:
            d.setdefault("require", "accepted")
        merged = {"project_name": project_name, **(payload or {})}
        task = self.ledger.register_task(
            task_id=task_id, project_instance_id=project_instance_id, run_id=run_id,
            kind=kind, shot_id=shot_id, payload=merged, depends_on=deps,
            input_signature=input_signature)
        if task["status"] == "ready":
            return self._claim_and_submit(task)
        return task

    # ---- 事件驱动推进 ----
    def on_event(self, evt: dict) -> List[dict]:
        """消费 promotion.accepted 事件：满足依赖的 waiting 节点 → ready → 入队。
        返回本次派发的任务列表。"""
        dispatched: List[dict] = []
        if evt.get("kind") != "promotion.accepted":
            return dispatched
        shot_id = (evt.get("payload") or {}).get("shot_id")
        if not shot_id:
            return dispatched
        for task in self.ledger.list_tasks():
            if task["status"] != "waiting_dependency":
                continue
            if not self._deps_met(task, accepted_shot={shot_id}):
                continue
            try:
                task = self.ledger.transition_task(
                    task["task_id"], to_status="ready",
                    from_statuses=("waiting_dependency",))
            except LedgerError:
                continue  # 并发下已被其它路径推进
            dispatched.append(self._claim_and_submit(task))
        return dispatched

    def _deps_met(self, task: dict, accepted_shot: Optional[str] = None) -> bool:
        """依赖是否满足。无依赖=满足；有依赖：每个 dep 的 shot 必须已有 accepted
        候选（本轮事件触发的增量判断 + 现存账本状态的全量复核）。"""
        deps = task.get("depends_on") or []
        if not deps:
            return True
        for dep in deps:
            dep_shot = dep.get("shot_id")
            if not dep_shot:
                return False
            if accepted_shot is not None and dep_shot != accepted_shot:
                # 不是本事件对应的依赖：查账本现存状态
                accepted = self.ledger.list_candidates(
                    shot_id=dep_shot, acceptance_state="accepted")
                if not accepted:
                    return False
            # 是本事件对应的依赖：事件本身即 accepted 证明
        return True

    # ---- 领取 + 投递 ----
    def _claim_and_submit(self, task: dict) -> dict:
        """账本唯一键先领取（ready→queued + dispatch_key），再投递原 JobQueue。
        JobQueue 不可用 → 保持 ready（条件未满足不算失败，等待重试）。"""
        jq = self._job_queue()
        if jq is None:
            log.warning("dispatch: JobQueue unavailable, task stays ready: %s",
                        task["task_id"])
            return task
        key = make_dispatch_key(task)
        try:
            task = self.ledger.transition_task(
                task["task_id"], to_status="queued", from_statuses=_DISPATCH_STATUSES,
                set_dispatch_key=key)
        except LedgerError:
            return self.ledger.get_task(task["task_id"])  # type: ignore[return-value]
        payload = task.get("payload") or {}
        job = jq.submit(payload.get("project_name") or "", task["kind"],
                        task.get("shot_id"),
                        origin_dispatch_key=key,
                        project_instance_id=task["project_instance_id"],
                        origin_task_id=task["task_id"],
                        origin_run_id=task["run_id"])
        # job_id 关联落账（此前的窗口由 recover 找回）
        return self.ledger.transition_task(
            task["task_id"], to_status="queued", set_job_id=job.job_id)

    # ---- 崩溃恢复 ----
    def recover(self) -> dict:
        """queued 而无 job_id 的任务：按 origin_dispatch_key 从 JobQueue 找回原 Job
        （不重复制造）；队列里确实不存在（投递前崩溃）→ 重新投递（首次创建语义）。"""
        jq = self._job_queue()
        out = {"rebound": [], "resubmitted": [], "skipped": []}
        if jq is None:
            return out
        by_key: Dict[str, dict] = {}
        try:
            for j in jq.list_jobs():
                key = (j.get("params") or {}).get("origin_dispatch_key")
                if key:
                    by_key.setdefault(key, j)
        except Exception as e:  # noqa: BLE001 —— 队列读取失败如实跳过本轮
            log.warning("dispatch recover: queue scan failed: %s", e)
            return out
        for task in self.ledger.list_tasks():
            if task["status"] != "queued" or task.get("job_id"):
                continue
            if not task.get("dispatch_key"):
                out["skipped"].append(task["task_id"])
                continue
            found = by_key.get(task["dispatch_key"])
            if found:
                self.ledger.transition_task(task["task_id"], to_status="queued",
                                            set_job_id=found["job_id"])
                out["rebound"].append(
                    {"task_id": task["task_id"], "job_id": found["job_id"]})
            else:
                try:
                    t = self.ledger.transition_task(
                        task["task_id"], to_status="ready",
                        from_statuses=("queued",))
                    self._claim_and_submit(t)
                    out["resubmitted"].append(task["task_id"])
                except LedgerError:
                    out["skipped"].append(task["task_id"])
        return out

    # ---- Job 终态观察（任务状态推进） ----
    def on_job_terminal(self, task_id: str, state: str) -> Optional[dict]:
        """Job 终态 → 任务状态推进（done/failed；cancelled 归 failed 语义由调用方定）。
        由结算层或 watch_queue 调用；重复通知不倒退（条件更新保证）。"""
        target = {"done": "done", "failed": "failed",
                  "cancelled": "cancelled"}.get(state)
        if target is None:
            return None
        try:
            return self.ledger.transition_task(
                task_id, to_status=target,
                from_statuses=("queued", "running", "ready",
                               "waiting_dependency", "blocked_decision"))
        except LedgerError:
            return None

    async def watch_queue(self, job_queue, stop_event) -> None:
        """订阅 JobQueue 广播，Job 终态时推进对应任务（origin_task_id 关联）。
        测试与未来后台服务共用；取消 stop_event 即退出。"""
        sub = job_queue.subscribe()
        try:
            while not stop_event.is_set():
                try:
                    payload = await asyncio.wait_for(sub.get(), timeout=0.5)
                except (asyncio.TimeoutError, TimeoutError):
                    continue
                if payload.get("state") in ("done", "failed", "cancelled"):
                    task_id = (payload.get("params") or {}).get("origin_task_id")
                    if task_id:
                        self.on_job_terminal(task_id, payload["state"])
        finally:
            job_queue.unsubscribe(sub)

    # ---- 失效传播 ----
    def on_candidates_invalidated(self, shot_id: str) -> List[dict]:
        """依赖 shot 的已接纳产物被否定（如用户重做）→ 相关 waiting 节点保持等待，
        已 ready/queued 的未派发节点回退 waiting（不取消已入队 Job——真实取消在
        D 批补全；本批如实在报告中说明）。"""
        reverted: List[dict] = []
        for task in self.ledger.list_tasks():
            deps = task.get("depends_on") or []
            if not any(d.get("shot_id") == shot_id for d in deps):
                continue
            if task["status"] == "ready":
                try:
                    reverted.append(self.ledger.transition_task(
                        task["task_id"], to_status="waiting_dependency",
                        from_statuses=("ready",)))
                except LedgerError:
                    pass
        return reverted
