"""AI-6F Gate D 前置：JobQueue clean-shutdown cancellation 调查 + 最小修复验证。

证明两个 cancellation 事件被正确区分（用户决策 #2 语义）：
  A. force-cancel 一个 Job（cancel(force=True)）→ Job 走既有 cancelled 语义，**worker 存活**（可处理 J2）。
  B. worker 自身被 shutdown cancel（asyncio.run/uvicorn `_cancel_all_tasks`）→ **worker 终止**，loop 退出，无 hang。

不调真 Agnes。blocking handler 用 `asyncio.Event().wait()` 模拟 long video poll。

背景（调查结论）：原 `_worker` 的 `except asyncio.CancelledError: pass`（job_queue.py:227）吞掉 worker 自身的
shutdown cancel，worker 回到 `await q.get()` 阻塞，`asyncio.run`/uvicorn 的 `_cancel_all_tasks` gather 永挂——
影响 production（uvicorn.run→asyncio.run 同路径）。修复：worker 用 `current_task().cancelling()>0` 区分自身 shutdown
cancel（re-raise 终止）vs inner job force-cancel（既有语义继续）；`_run_job` 用 `job.state==RUNNING` 区分 shutdown
中断（保留 RUNNING 供 restart reattach+recover，§20）vs force-cancel/watchdog（CANCELLED/FAILED）。
"""
import asyncio
import threading
import time as _t

import pytest

from src.core.job_queue import JobQueue


def _new_queue(tmp_path, handlers):
    return JobQueue(tmp_path / "jobs.json", handlers)


async def _wait_state(q, job_id, state, timeout=5.0):
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        j = q.get(job_id)
        if j and j.state == state:
            return j
        await asyncio.sleep(0.02)
    return q.get(job_id)


def test_S1_force_cancel_job_keeps_worker_alive(tmp_path):
    """A: force-cancel 一个 running Job → J1 cancelled（既有语义）；worker 存活，J2 可被 pick up。"""
    entered = []

    async def _blocking(job, emit):
        entered.append(job.job_id)
        await asyncio.Event().wait()   # block until cancelled

    q = _new_queue(tmp_path, {"first_frame": _blocking})

    async def scenario():
        await q.start()
        j1 = q.submit("p", "first_frame", "s01_c01")
        while not entered:                       # worker at await task (J1 running, blocked)
            await asyncio.sleep(0.02)
        assert q.get(j1.job_id).state == "running"
        assert q.cancel(j1.job_id, force=True) is True
        j = await _wait_state(q, j1.job_id, "cancelled")
        assert j.state == "cancelled", "force-cancel 应使 J1 走既有 cancelled 语义"
        # worker 仍存活：submit J2，应被 pick up（worker 回到 await q.get）
        j2 = q.submit("p", "first_frame", "s02_c01")
        while j2.job_id not in entered:
            await asyncio.sleep(0.02)
        assert q.get(j2.job_id).state == "running", "worker 存活，J2 可运行"

    asyncio.run(scenario())   # cleanup: J2 mid-block → fix#1 worker re-raises → terminates (no hang)


def test_S2_idle_worker_shutdown_terminates(tmp_path):
    """B (idle): worker 在 await q.get()（无 job）时 shutdown → worker 终止，asyncio.run 正常退出。"""

    async def _handler(job, emit):
        pass

    q = _new_queue(tmp_path, {"first_frame": _handler})

    async def scenario():
        await q.start()   # workers idle (no jobs) → all at await q.get()

    asyncio.run(scenario())   # returns => no hang (idle worker cancel propagates at q.get, no try)


def test_S3_shutdown_while_mid_job_terminates(tmp_path):
    """B (mid-job, 关键): worker 在 await running Job task（handler blocked on simulated video poll）时
    shutdown → worker **不吞** shutdown CancelledError → 终止；asyncio.run cleanup 完成；无 hang；无遗留 pending task。

    thread + join deadline：超时 = FAIL（不当作 PASS）。"""
    entered = []

    async def _blocking(job, emit):
        entered.append(job.job_id)
        await asyncio.Event().wait()   # simulated long video poll

    async def scenario():
        q = _new_queue(tmp_path, {"video": _blocking})
        await q.start()
        q.submit("p", "video", "s01_c01")
        while not entered:                     # worker now at `await task`（J1 in blocking poll）
            await asyncio.sleep(0.02)
        # return → asyncio.run cleanup: _cancel_all_tasks cancels worker (mid await task) + inner.
        #   修复前：worker 吞 CancelledError → 回 q.get → gather 永挂。
        #   修复后：cancelling()>0 → re-raise → worker 终止；inner cancelled（state==RUNNING → 保留 RUNNING）。

    err = []

    def _run():
        try:
            asyncio.run(scenario())
        except BaseException as e:
            err.append(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(15.0)   # 15s hard deadline
    assert not t.is_alive(), "asyncio.run cleanup hung — worker did not terminate on shutdown (fix#1 broken)"
    assert not err, f"scenario raised: {err}"
    # fix#2: J1 kept RUNNING (not cancelled) on shutdown → restart reattaches + recover resumes
    assert entered, "handler never entered"
    q2 = _new_queue(tmp_path, {"video": _blocking})
    j1 = q2.get(entered[0])
    assert j1 is not None and j1.state == "queued", \
        "新协议尚无提交意图/任务ID的任务可重排队；handler必须在POST前持久化意图"
    assert j1.finished_at is None, "RUNNING（shutdown 中断）Job 不应记 finished_at"
