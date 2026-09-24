# tests/test_job_queue_errors_37.py
# 37.md §1.2 — JobQueue 错误必须保留真实异常类型：
# str(e) 为空的异常（如 httpcore.ReadError）不得产生空 error/message，
# 至少落异常类名 + 最后到达阶段 + provider/model（handler 在 params 提供时）。
# 只改善可观测性，不改变 retry/fallback 行为。
import asyncio
from pathlib import Path

import pytest

from src.core.job_queue import Job, JobQueue


class _EmptyStrError(Exception):
    """str(e) == '' 的真实异常形态（httpcore.ReadError 同型）。"""


def _make_queue(tmp_path, handler):
    q = JobQueue(Path(tmp_path) / "jobs.json", {"video": handler})
    return q


def _submit_and_drain(q, **submit_kw):
    """submit 需运行中的事件循环（create_task 广播）——与 submit/drain 同窗运行。"""

    async def _run():
        await q.start()
        q.submit(**submit_kw)
        # 等待队列清空（worker 拉取后终态落定）
        for _ in range(200):
            if all(j.state in ("done", "failed", "cancelled")
                   for j in q.jobs.values()):
                break
            await asyncio.sleep(0.02)
        # 收尾：cancel workers（不进 shutdown 语义，直接终止）
        for t in q._tasks:
            t.cancel()
        await asyncio.gather(*q._tasks, return_exceptions=True)
    asyncio.run(_run())


def test_empty_str_exception_falls_back_to_class_name(tmp_path):
    """str(e)=='' → error/message 非空（类名兜底），error_class 记录真实类型。"""

    async def handler(job, emit):
        await emit("submit", "提交中", 10)
        raise _EmptyStrError()

    q = _make_queue(tmp_path, handler)
    _submit_and_drain(q, project="p1", kind="video", shot_id="s01",
                      provider="agnes", video_model="agnes-video-2.5-flash")
    job = next(iter(q.jobs.values()))
    assert job.state == "failed"
    assert job.error and job.error.strip(), "error 不得为空"
    assert "_EmptyStrError" in job.error
    assert job.message.startswith("失败:") and len(job.message) > len("失败:")
    assert job.error_class.endswith("_EmptyStrError")
    assert job.error_stage == "submit"
    assert job.error_provider == "agnes"
    assert job.error_model == "agnes-video-2.5-flash"


def test_normal_exception_message_preserved(tmp_path):
    """普通异常：error/message 原文保留，error_class 仍记录。"""

    async def handler(job, emit):
        raise ValueError("配额不足: video_seconds")

    q = _make_queue(tmp_path, handler)
    _submit_and_drain(q, project="p1", kind="video", shot_id="s01")
    job = next(iter(q.jobs.values()))
    assert "配额不足" in job.error
    assert "配额不足" in job.message
    assert job.error_class.endswith("ValueError")
    assert job.error_provider is None and job.error_model is None


def test_success_has_no_error_fields(tmp_path):
    """成功任务不携带错误字段。"""

    async def handler(job, emit):
        await emit("poll", "50%", 50)

    q = _make_queue(tmp_path, handler)
    _submit_and_drain(q, project="p1", kind="video", shot_id="s01",
                      provider="agnes")
    job = next(iter(q.jobs.values()))
    assert job.state == "done"
    assert job.error_class is None and job.error is None


def test_error_fields_persisted_to_disk(tmp_path):
    """错误字段随 jobs.json 持久化（重启后可追溯）。"""

    async def handler(job, emit):
        raise _EmptyStrError()

    q = _make_queue(tmp_path, handler)
    _submit_and_drain(q, project="p1", kind="video", shot_id="s01",
                      provider="agnes")
    q2 = JobQueue(Path(tmp_path) / "jobs.json", {"video": handler})
    job = next(iter(q2.jobs.values()))
    assert job.error_class.endswith("_EmptyStrError")
    assert job.error_provider == "agnes"
