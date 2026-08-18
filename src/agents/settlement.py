# src/agents/settlement.py
# AI-6F.0 §10/§12：AgentTask ↔ 现有 JobQueue 的 durable settlement bridge。
#
# 🔴 不变式（§11/§12）：
#   - Job/Run 是执行真相；AgentTask 是协调记录。二者分歧时从 durable 执行态对账
#     AgentTask，**不**发明独立的 Agent 执行现实（§11）。
#   - 持久态是真相（jobs.json + tasks/*.json）；SSE 只是通知（§12）。本 bridge 以
#     poll loop 对账——正确性**不**依赖内存 SSE 事件；restart 后 recover_tasks 从盘重建。
#     前端可继续用现有 /api/jobs/stream 看 job 事件；本 bridge 不建第二套 Agent SSE。
#
# 三种 reconcile 决策（§6/§10）：
#   linked Job 非终态（queued/running） → reattach：task 保持 running，刷进度快照（§18-7/8/13）
#   linked Job == done                 → settle completed（§18-5/10）
#   linked Job failed/cancelled        → settle failed（§18-6/11）
#   linked Job 不存在（被清）          → settle failed（§10 边缘，无法重建）
#   task 已终态                        → no-op（幂等 §18-9）
#
# Import 纪律：本文件只 import 本包（models/tasks）+ stdlib。project_lister /
# job_queue_getter 由 composition root（api_server）注入，不 import deps（可单测）。
from __future__ import annotations
import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .tasks import AgentTaskStore
from .models import (AgentStructuredResult, ResultStatus, TaskStatus)

log = logging.getLogger("AgentTaskSettler")

_JOB_DONE = "done"
_JOB_FAIL = {"failed", "cancelled"}
_JOB_NON_TERMINAL = {"queued", "running"}


def _progress_snapshot(ref: Dict[str, Any], job: Any) -> Dict[str, Any]:
    """从 linked Job 派生 AgentTask 进度快照（§8：确定性 production phase，不造 LLM 百分比）。"""
    state = getattr(job, "state", "unknown") if job is not None else "gone"
    return {
        "job_id": ref.get("job_id"),
        "kind": ref.get("kind"),
        "phase": ref.get("phase"),
        "shot_id": ref.get("shot_id"),
        "job_state": state,
        "progress_pct": int(getattr(job, "progress", 0) or 0) if job else 0,
        "completed_items": 1 if state == _JOB_DONE else 0,
        "total_items": 1,
        "submitted_at": ref.get("submitted_at"),
    }


def _result_for_done(ref, job) -> AgentStructuredResult:
    return AgentStructuredResult(
        status=ResultStatus.COMPLETED.value,
        summary=f"镜头 {ref.get('shot_id')} 的 {ref.get('phase')} 已完成。",
        artifact_refs=[{"type": "job", "id": ref.get("job_id")},
                        {"type": "shot", "id": ref.get("shot_id")}],
        data=_progress_snapshot(ref, job))


def _result_for_failed(ref, job, reason: str) -> AgentStructuredResult:
    err = (getattr(job, "error", None) or reason) if job else reason
    return AgentStructuredResult(
        status=ResultStatus.FAILED.value,
        summary=f"镜头 {ref.get('shot_id')} 的 {ref.get('phase')} 失败：{reason}",
        artifact_refs=[{"type": "job", "id": ref.get("job_id")}],
        warnings=[str(err)[:500]],
        data=_progress_snapshot(ref, job))


def _apply_decision(t: "AgentTask", decision: Dict[str, Any]) -> None:
    """把 reconcile 决策写回 AgentTask（mutator，由 store.update 在 reload 后调用）。
    幂等：若 task 已非 running（被别处结算），跳过（§18-9）。"""
    if t.status != TaskStatus.RUNNING.value:
        return
    if not t.execution_ref or not t.execution_ref.get("job_id"):
        return
    res: AgentStructuredResult = decision["result"]
    t.structured_result = res.to_dict()
    t.result_summary = res.summary
    t.artifact_refs = list(res.artifact_refs)
    t.warnings = list(res.warnings)
    # execution_ref 保留（§14：regeneration 是版本创建不是抹除；durable 链接历史不删）
    if res.status == ResultStatus.COMPLETED.value:
        t.status = TaskStatus.COMPLETED.value
        t.error = None
    elif res.status == ResultStatus.FAILED.value:
        t.status = TaskStatus.FAILED.value
        t.error = res.summary
    # RUNNING（reattach）：status 保持 running，仅刷进度快照


class AgentTaskSettler:
    """扫描所有项目 running AgentTask，据 linked Job 状态对账。

    project_lister: () -> List[(project_name, project_dir)]（composition root 注入）
    job_queue_getter: () -> JobQueue | None（未就绪返 None，本轮跳过）
    """

    def __init__(self,
                 project_lister: Callable[[], List[Tuple[str, Any]]],
                 job_queue_getter: Callable[[], Any],
                 poll_interval: float = 2.0):
        self._project_lister = project_lister
        self._job_queue_getter = job_queue_getter
        self._poll_interval = poll_interval
        self._loop_task: Optional[asyncio.Task] = None

    # ---------- 单 task 对账（核心，可独立单测）----------
    def reconcile_task(self, store: AgentTaskStore, task: "AgentTask") -> Optional[Dict[str, Any]]:
        """同步对账一个 running task。返回决策 dict 或 None（无可对账链接）。
        不写盘——调用方（store.update）负责原子写。保留同步签名便于单测直接驱动。"""
        ref = task.execution_ref
        if not ref or not ref.get("job_id"):
            return None
        try:
            q = self._job_queue_getter()
        except Exception:
            return None
        if q is None:
            return None
        job = q.get(ref["job_id"])
        if job is None:
            return {"result": _result_for_failed(
                ref, None, "linked Job 已不存在（被清理或丢失），无法重建执行态")}
        st = getattr(job, "state", None)
        if st == _JOB_DONE:
            return {"result": _result_for_done(ref, job)}
        if st in _JOB_FAIL:
            return {"result": _result_for_failed(ref, job, f"linked Job {st}")}
        if st in _JOB_NON_TERMINAL:
            # reattach：返回 RUNNING 结果仅刷进度；task 保持 running
            return {"result": AgentStructuredResult(
                status=ResultStatus.RUNNING.value,
                summary=f"镜头 {ref.get('shot_id')} 的 {ref.get('phase')} 进行中（job {st}）。",
                artifact_refs=[{"type": "job", "id": ref.get("job_id")},
                                {"type": "shot", "id": ref.get("shot_id")}],
                data=_progress_snapshot(ref, job))}
        # 未知状态：跳过，不误结算
        return None

    async def _reconcile_store(self, store: AgentTaskStore) -> int:
        """对账一个项目所有 running task。返回结算/刷新数。"""
        n = 0
        for t in store.list(status=TaskStatus.RUNNING.value):
            decision = self.reconcile_task(store, t)
            if decision is None:
                continue
            # store.update 重载后调 mutator 原子写（避免 list→写 之间覆盖）
            store.update(t.task_id, lambda tt, d=decision: _apply_decision(tt, d))
            n += 1
        return n

    # ---------- 启动恢复（§10）----------
    async def recover_tasks(self) -> int:
        """startup 一次性扫描所有项目 running AgentTask，reattach/settle。
        active Job 由 JobQueue 恢复（_load + start）；本处只对账 task↔job 链接。"""
        total = 0
        try:
            for name, pdir in self._project_lister():
                try:
                    store = AgentTaskStore(pdir, name)
                except Exception as e:
                    log.warning("AgentTaskStore 构造失败 %s: %s", name, e)
                    continue
                try:
                    total += await self._reconcile_store(store)
                except Exception as e:
                    log.warning("对账项目 %s 失败: %s", name, e)
        except Exception as e:
            log.error("recover_tasks 失败: %s", e)
        if total:
            log.info("AgentTask settlement 对账 %d 个 running task", total)
        return total

    # ---------- 后台 poll loop（§12 polling fallback）----------
    async def start(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            return
        self._loop_task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                await self.recover_tasks()
            except Exception as e:
                log.error("settlement poll 异常: %s", e)
            await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            self._loop_task = None


# ============ 单例（生产；api_server 用）============
_settler: Optional[AgentTaskSettler] = None


def get_settler() -> AgentTaskSettler:
    """生产单例：用 deps 注入 project_lister / job_queue_getter。
    测试**不**用此——直接构造 AgentTaskSettler(...fakes...)。"""
    global _settler
    if _settler is None:
        import deps

        def _list_projects():
            out: List[Tuple[str, Any]] = []
            try:
                pm = deps.get_project_manager()
            except Exception:
                return out
            base = getattr(pm, "base_dir", None)
            if base is None or not base.exists():
                return out
            for p in base.iterdir():
                if not p.is_dir():
                    continue
                if not (p / "output" / "project.json").exists():
                    continue
                out.append((p.name, p))
            return out

        def _get_queue():
            try:
                return deps.get_job_queue()
            except Exception:
                return None

        _settler = AgentTaskSettler(_list_projects, _get_queue)
    return _settler
