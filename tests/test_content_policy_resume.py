"""Exercise feedback retries through the durable queue and production wrapper."""
import asyncio
import json

import pytest

from src.ai.autopilot import SceneAutopilotManager
from src.ai.content_policy_revision import revise_prompt_with_feedback
from src.core.job_queue import JobQueue
from tools.full_project_cloud_acceptance import BoundedRealClient


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["first_frame", "last_frame"])
@pytest.mark.parametrize("old_feedback", [None, "previous hop rejection"])
async def test_restored_image_retry_persists_current_feedback(tmp_path, monkeypatch,
                                                            kind, old_feedback):
    import deps

    path = tmp_path / "jobs.json"
    old = JobQueue(path, {})
    job = old.submit("p", kind, "s03_c09", origin_run_id="run",
                     autopilot_stage_key="third", variants=2)
    job.state = "failed"
    job.error = "400 content_policy_violation"
    if old_feedback:
        job.params["content_policy_feedback"] = old_feedback
    old._save()
    old.close()

    seen = []

    class Client:
        async def chat_completion(self, messages, **kwargs):
            seen.append(messages[0]["content"])
            return {"content": "the revised scene"}

    async def handler(retried, emit):
        assert await revise_prompt_with_feedback(
            "original scene", retried.params["content_policy_feedback"],
            client=Client()) == "the revised scene"

    queue = JobQueue(path, {kind: handler})
    monkeypatch.setattr(deps, "job_queue", queue)
    try:
        await queue.start()
        retried = await SceneAutopilotManager._enqueue_autopilot_job(
            None, "p", "s03_c09", kind, "third", "run",
            content_policy_feedback="current provider rejection", variants=2)
        assert retried.job_id == job.job_id
        assert json.loads(path.read_text())[job.job_id]["params"][
            "content_policy_feedback"] == "current provider rejection"
        for _ in range(100):
            if retried.state in ("done", "failed"):
                break
            await asyncio.sleep(0.01)
        assert retried.state == "done"
        assert len(seen) == 1 and "current provider rejection" in seen[0]
        assert len(queue.jobs) == 1
    finally:
        for task in queue._tasks:
            task.cancel()
        await asyncio.gather(*queue._tasks, return_exceptions=True)
        queue.close()


class Journal:
    def __init__(self, fail=False):
        self.doc = {}
        self.writes = []
        self.fail = fail

    def save(self, **fields):
        if self.fail:
            raise RuntimeError("CAS failed")
        self.writes.append(fields)


class Provider:
    def __init__(self):
        self.calls = 0

    async def chat_completion(self, *args, **kwargs):
        self.calls += 1
        return {"content": "revised"}


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["finalize", "budget", "CAS"])
async def test_revision_completion_cannot_bypass_production_guards(tmp_path, blocked):
    provider = Provider()
    journal = Journal(fail=blocked == "CAS")
    wrapper = BoundedRealClient(provider, journal, tmp_path, tmp_path,
                                finalize_only=blocked == "finalize",
                                text_budget=0 if blocked == "budget" else 2,
                                film_target_seconds="auto")
    with pytest.raises(RuntimeError):
        await wrapper.chat_completion([{"role": "user", "content": "revise"}])
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_completion_usage_is_durable_and_shared_across_hops(tmp_path):
    journal = Journal()
    provider = Provider()
    wrapper = BoundedRealClient(provider, journal, tmp_path, tmp_path,
                                text_budget=2, stage0_text_reserved=1,
                                film_target_seconds="auto")
    assert await wrapper.chat_completion([]) == {"content": "revised"}
    assert journal.writes[-1]["text_calls"] == 1
    restored = BoundedRealClient(provider, journal, tmp_path, tmp_path,
                                 text_budget=2, stage0_text_reserved=1,
                                 film_target_seconds="auto")
    restored.restore_usage(journal.writes[-1])
    with pytest.raises(RuntimeError, match="text-call budget"):
        await restored.chat_completion([])
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_client_lookup_failure_uses_revision_fallback(monkeypatch):
    import deps

    def unavailable():
        raise RuntimeError("client unavailable")

    monkeypatch.setattr(deps, "get_agnes_client", unavailable)
    assert await revise_prompt_with_feedback("original", "provider feedback") == "original"
