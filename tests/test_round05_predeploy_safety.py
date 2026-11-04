"""Pre-deployment guards: paid retry uncertainty and Actions pending run safety.

All Python tests use a tmp_path ledger and fake handlers. Zero real provider calls.
"""
from __future__ import annotations
import asyncio
from pathlib import Path

import pytest

from src.core.job_queue import Job, JobQueue


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    {"submission_intent_id": "s1", "handler_started": True},
    {"provider_task_id": "v1", "handler_started": True},
    {"provider_submit_state": "submitting", "handler_started": True},
    {"provider_submit_state": "unknown", "handler_started": True},
    {"provider_submit_state": "accepted", "handler_started": True},
    {},  # Legacy failed video: no durable handler/submission proof; fail closed.
    {"unattached_provider_receipt": {"provider_task_id": "v2"}},
])
async def test_manual_retry_never_requeues_paid_or_ambiguous_video(tmp_path, params):
    q = JobQueue(tmp_path / "jobs.json", {})
    try:
        job = Job(job_id="ambig", project="P", shot_id="S", kind="video",
                  state="failed", params=dict(params))
        q.jobs[job.job_id] = job
        q._save()
        before = (q.state_path.read_bytes(), job.state, dict(job.params))
        assert q.retry("ambig") is None
        assert (q.state_path.read_bytes(), job.state, job.params) == before
        assert q.retry_failed("P") == []
        assert q.jobs["ambig"].state == "failed"
    finally:
        q.close()


@pytest.mark.asyncio
async def test_confirmed_pre_submit_failure_can_retry(tmp_path):
    q = JobQueue(tmp_path / "jobs.json", {})
    try:
        job = Job(job_id="safe", project="P", shot_id="S", kind="video",
                  state="failed", params={"handler_started": True,
                                           "video_pre_submit_failure": "last_frame_url"})
        q.jobs[job.job_id] = job
        q._save()
        assert q.retry_failed("P") == ["safe"]
        assert q.jobs["safe"].state == "queued"
        # No worker was started; no handler/provider call is possible.
    finally:
        q.close()


@pytest.mark.asyncio
async def test_cancelled_unstarted_video_can_retry(tmp_path):
    q = JobQueue(tmp_path / "jobs.json", {})
    try:
        job = Job(job_id="cancelled_before_claim", project="P", shot_id="S",
                  kind="video", state="cancelled", params={})
        q.jobs[job.job_id] = job
        assert q.retry(job.job_id) is job
        assert job.state == "queued"
    finally:
        q.close()


def test_both_github_production_workflows_keep_pending_resume(tmp_path):
    root = Path(__file__).resolve().parents[1]
    from pathlib import Path as _P
    for name in ("video-autoproduction-e2e.yml", "video-patrol.yml"):
        text = (root / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "group: video-production" in text
        assert "queue: max" in text
        assert "cancel-in-progress: false" in text


@pytest.mark.asyncio
async def test_cancelled_unstarted_but_recorded_paid_intent_is_still_blocked(tmp_path):
    q = JobQueue(tmp_path / "jobs.json", {})
    try:
        job = Job(job_id="cancelled_with_intent", project="P", shot_id="S",
                  kind="video", state="cancelled", params={"submission_intent_id": "x"})
        q.jobs[job.job_id] = job
        assert q.retry(job.job_id) is None
        assert job.state == "cancelled"
    finally:
        q.close()
