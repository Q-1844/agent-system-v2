"""End-to-end local JobQueue overlap: approved C2 plans -> project -> scene -> video.

The FakeAgnes provider gates both video POSTs until the test confirms two
independent C2 shots entered the real queue's video handlers concurrently.
No external network, quota or paid provider is used.
"""
import asyncio
import shutil
import threading
import time
import uuid

import deps
from src.core.models import Project, Scene, Shot, StyleAnchor
from src.core.store import ProjectStore
from src.ai.production_planner import sanitize_plan
from tests.test_project_autopilot import FakeAgnes, FakeRehost


class GatedAgnes(FakeAgnes):
    def __init__(self):
        super().__init__()
        self.inflight = 0
        self.peak_video = 0
        self.two_seen = threading.Event()
        self.release = threading.Event()

    async def create_video(self, prompt, **kwargs):
        self.inflight += 1
        self.peak_video = max(self.peak_video, self.inflight)
        if self.inflight >= 2:
            self.two_seen.set()
        try:
            ok = await asyncio.to_thread(self.release.wait, 35)
            if not ok:
                raise RuntimeError("video overlap gate timed out")
            return await super().create_video(prompt, **kwargs)
        finally:
            self.inflight -= 1


def test_real_c2_production_runs_two_video_jobs_together(client, tmp_path):
    from routers.production_planner import _read_plans, _write_plans
    pm = deps.get_project_manager()
    pname = "_c2_parallel_actual_" + uuid.uuid4().hex[:8]
    folder = pm.create_project(pname)
    scenes = [Scene(scene_id=f"s{i:02d}", summary=f"独立场景 {i}",
                    shots=[Shot(shot_id=f"s{i:02d}_c01", scene_id=f"s{i:02d}",
                                index=1, description="Independent cinematic landscape")])
              for i in (1, 2)]
    ProjectStore(folder).save(Project(name=pname, style=StyleAnchor(
        art_style="anime", aspect_ratio="16:9"), story_text="A quiet short film", scenes=scenes))
    plans = _read_plans(folder)
    for scene in scenes:
        plan = sanitize_plan(scene, {"shots": [{"shot_id": scene.shots[0].shot_id,
               "opening_source": "independent", "link_mode": "i2v", "reason": "no tail relay",
               "parallel_preference": "allow"}], "max_parallel_shots": 2},
               run_seed=1234, project_name=pname)
        plan.status = "approved"
        plans[scene.scene_id] = plan
    _write_plans(folder, plans)
    gated = GatedAgnes()
    old_agnes = deps.agnes_client
    old_rehost = deps._rehost_host
    # This is a real JobQueue integration test, not a test of *other* tests'
    # session-scoped queue. A private queue avoids inheriting unrelated
    # in-flight jobs and blocking reconfigure(wait_settle=True) for 2 hours.
    from src.core.job_queue import JobQueue
    old_queue = deps.get_job_queue()
    queue = JobQueue(tmp_path / "c2_video_jobs.json", deps.JOB_HANDLERS,
                     video_worker_count=2)
    client.portal.call(queue.start)
    deps.job_queue = queue
    deps.agnes_client = gated
    deps._rehost_host = FakeRehost()
    try:
        assert queue.video_worker_count == 2
        response = client.post(f"/api/project/{pname}/production/start", json={"respect_locked": True})
        assert response.status_code == 200, response.text
        run_id = response.json()["run_id"]
        assert gated.two_seen.wait(35), "C2 did not overlap two independent real video jobs"
        assert gated.peak_video >= 2
        gated.release.set()
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            run = client.get(f"/api/project/{pname}/production/run/{run_id}").json()
            if run["status"] in ("completed", "failed", "blocked", "cancelled"):
                break
            time.sleep(0.5)
        assert run["status"] == "completed", run.get("error")
        assert len(run["scenes"]) == 2
        assert all(s["status"] == "completed" for s in run["scenes"])
    finally:
        gated.release.set()
        deps.job_queue = old_queue

        async def _shutdown_isolated_queue():
            for worker in queue._tasks:
                worker.cancel()
            await asyncio.gather(*queue._tasks, return_exceptions=True)

        client.portal.call(_shutdown_isolated_queue)
        deps.agnes_client = old_agnes
        deps._rehost_host = old_rehost
        shutil.rmtree(folder, ignore_errors=True)
