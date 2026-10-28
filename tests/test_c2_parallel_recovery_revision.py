"""Negative and migration safety tests for C2's dynamic producer parallelism."""
import asyncio
from types import SimpleNamespace

import pytest

from src.ai.autopilot import SceneAutopilotManager, SceneAutopilotRun, ShotExecutionState
from src.ai.project_autopilot import (
    ProjectProductionManager, ProjectProductionRun,
    ProjectSceneExecutionState, PRUN_COMPLETED,
)
from src.ai.c2_parallel import shot_dependencies
from src.core.models import Shot
from src.core.store import ProjectStore
from tests.test_c2_dynamic_parallel import movie, plans, mount_project, source_done


def test_legacy_checkpoints_keep_old_serial_semantics():
    scene = SceneAutopilotRun.model_validate({
        "run_id": "old", "project_name": "old", "scene_id": "s01",
        "plan_id": "old", "plan_snapshot": {}, "run_seed": 0,
    })
    parent = ProjectProductionRun.model_validate({"run_id": "old", "project_name": "old"})
    assert scene.max_parallel_shots == 1
    assert parent.max_parallel_scenes == 1
    assert parent.scene_dependencies == {}
    assert plans(movie([("s01", 1)]))["s01"].max_parallel_shots == 2


def test_revision_added_shot_becomes_new_prev_tail_predecessor():
    project = movie([("s01", 2), ("s02", 1)])
    project.scenes[1].shots[0].video_reference_asset_id = "prev_tail"
    prior = plans(project)
    prior["s02"].shots[0].link_mode = "multi"
    before = shot_dependencies(project, prior)
    assert before["s02_c01"] == {"s01_c02"}
    # The additive revision is materialized in source order, then the revised
    # production plan freezes a *new* dependency graph.
    project.scenes[0].shots.append(Shot(shot_id="s01_c02b", scene_id="s01",
                                        index=3, description="inserted repair bridge"))
    after_plans = plans(project)
    after_plans["s02"].shots[0].link_mode = "multi"
    after = shot_dependencies(project, after_plans)
    assert after["s02_c01"] == {"s01_c02b"}
    assert before["s02_c01"] != after["s02_c01"]


@pytest.mark.asyncio
async def test_existing_old_tail_cannot_unblock_new_source_version(tmp_path, monkeypatch):
    project = movie([("s01", 2)])
    directory = mount_project(tmp_path, monkeypatch, project)
    plan = plans(project, openings={"s01_c02": "inherit_previous"})["s01"]
    source_done(directory, "s01_c01", "version_old")
    manager = SceneAutopilotManager()
    run = SceneAutopilotRun(run_id="new_revision", project_name=project.name,
                            scene_id="s01", plan_id=plan.plan_id,
                            plan_snapshot=plan.model_dump(), run_seed=3,
                            max_parallel_shots=2,
                            shots=[ShotExecutionState(shot_id="s01_c01", index=0,
                                status="completed", video_version_id="version_new"),
                                ShotExecutionState(shot_id="s01_c02", index=1)])
    ready = manager._ready_shots(run, ProjectStore(directory).load())
    assert not ready, "historical tail must not unblock repaired upstream media"
    source_done(directory, "s01_c01", "version_new")
    ready = manager._ready_shots(run, ProjectStore(directory).load())
    assert [s.shot_id for s in ready] == ["s01_c02"]


@pytest.mark.asyncio
async def test_resume_parent_rebinds_running_child_not_recreate(tmp_path, monkeypatch):
    project = movie([("s01", 1)])
    mount_project(tmp_path, monkeypatch, project)
    manager = ProjectProductionManager()
    run = ProjectProductionRun(run_id="prun_rebind", project_name=project.name,
                               status="running", stage="scenes",
                               max_parallel_scenes=2,
                               scenes=[ProjectSceneExecutionState(scene_id="s01", index=0,
                                         status="running", child_run_id="existing_child")])
    await manager._save_run(run)
    seen = []
    async def rebind(project_name, current, entry):
        seen.append(entry.child_run_id)
        return SimpleNamespace(run_id=entry.child_run_id)
    async def completed(project_name, child_id):
        return SimpleNamespace(run_id=child_id, status="completed", error=None)
    async def compose(project_name, run_id):
        await manager._edit_run(project_name, run_id, lambda r: setattr(r, "status", PRUN_COMPLETED))
    monkeypatch.setattr(manager, "_validate_source_for_scene", lambda *_: None)
    monkeypatch.setattr(manager, "_start_or_reuse_child", rebind)
    monkeypatch.setattr(manager, "_wait_child", completed)
    monkeypatch.setattr(manager, "_compose_project", compose)
    await asyncio.wait_for(manager._run_loop(project.name, run.run_id), timeout=5)
    assert seen == ["existing_child"]
    current = manager.get_run(project.name, run.run_id)
    assert current.scenes[0].child_run_id == "existing_child"
    assert current.status == PRUN_COMPLETED


@pytest.mark.asyncio
async def test_pause_and_cancel_propagate_to_every_live_scene(tmp_path, monkeypatch):
    project = movie([("s01", 1), ("s02", 1)])
    mount_project(tmp_path, monkeypatch, project)
    manager = ProjectProductionManager()
    run = ProjectProductionRun(run_id="prun_controls", project_name=project.name,
                               status="running", stage="scenes",
                               scenes=[ProjectSceneExecutionState(scene_id=f"s0{i}", index=i-1,
                                       status="running", child_run_id=f"c{i}") for i in (1, 2)])
    await manager._save_run(run)
    class FakeChildManager:
        def __init__(self):
            self.paused = []
            self.cancelled = []
        async def request_pause(self, name, child):
            self.paused.append(child)
        async def request_cancel(self, name, child):
            self.cancelled.append(child)
    fake = FakeChildManager()
    import src.ai.project_autopilot as module
    monkeypatch.setattr(module, "get_autopilot_manager", lambda: fake)
    await manager.request_pause(project.name, run.run_id)
    assert sorted(fake.paused) == ["c1", "c2"]
    await manager.request_cancel(project.name, run.run_id)
    assert sorted(fake.cancelled) == ["c1", "c2"]

@pytest.mark.asyncio
async def test_c2_queue_snapshot_blocks_stale_upstream_selection(tmp_path, monkeypatch):
    """Autopilot direct JobQueue path must pin the same deps as Producer REST."""
    from src.agents.producer_orchestration import assert_pinned_video_dependencies, OrchestrationError
    import deps
    project = movie([("s01", 2)])
    project_dir = mount_project(tmp_path, monkeypatch, project)
    source_done(project_dir, "s01_c01", "initial_version")
    store = ProjectStore(project_dir)
    def prepare(shot):
        shot.opening_source = "inherit_previous"
        shot.source_shot_id = "s01_c01"
        shot.source_version = "initial_version"
        shot.link_mode = "i2v"
    store.update_shot("s01_c02", prepare)

    class Queue:
        def __init__(self):
            self.jobs = []
        def find_by_origin(self, *_):
            return None
        def submit(self, name, kind, sid, **params):
            self.jobs.append(params)
            return SimpleNamespace(job_id="pinned", state="queued", params=params)
    queue = Queue()
    monkeypatch.setattr(deps, "get_job_queue", lambda: queue)
    manager = SceneAutopilotManager()
    result = await manager._enqueue_autopilot_job(project.name, "s01_c02", "video",
                                                   "stage_key", "run_id")
    assert result.job_id == "pinned"
    snapshot = queue.jobs[0]["continuity_dependency_snapshot"]
    assert snapshot["source_version"] == "initial_version"
    assert snapshot["tail_url_sha256"]
    assert_pinned_video_dependencies(store.load(), "s01_c02", snapshot)
    source_done(project_dir, "s01_c01", "new_revision_version")
    with pytest.raises(OrchestrationError):
        assert_pinned_video_dependencies(store.load(), "s01_c02", snapshot)

@pytest.mark.asyncio
async def test_producer_required_serial_blocks_independent_second_shot(tmp_path, monkeypatch):
    """An independent visual input must not override the Producer's serial choice."""
    project = movie([("s01", 2)])
    directory = mount_project(tmp_path, monkeypatch, project)
    plan = plans(project, preferences={"s01_c02": "required_serial"})["s01"]
    run = SceneAutopilotRun(run_id="serial_choice", project_name=project.name,
                            scene_id="s01", plan_id=plan.plan_id,
                            plan_snapshot=plan.model_dump(), run_seed=5,
                            max_parallel_shots=2,
                            shots=[ShotExecutionState(shot_id=f"s01_c0{i}", index=i)
                                   for i in (1, 2)])
    manager = SceneAutopilotManager()
    assert [s.shot_id for s in manager._ready_shots(run, ProjectStore(directory).load())] == ["s01_c01"]
    run.shots[0].status = "completed"
    assert [s.shot_id for s in manager._ready_shots(run, ProjectStore(directory).load())] == ["s01_c02"]


@pytest.mark.asyncio
async def test_frozen_legacy_serial_limit_prevents_overlap(tmp_path, monkeypatch):
    """A legacy frozen Run does not get a surprise second worker after upgrade."""
    from src.ai.autopilot import RUN_COMPLETED
    project = movie([("s01", 2)])
    mount_project(tmp_path, monkeypatch, project)
    plan = plans(project)["s01"]
    manager = SceneAutopilotManager()
    run = SceneAutopilotRun(run_id="serial_legacy", project_name=project.name,
                            scene_id="s01", plan_id=plan.plan_id,
                            plan_snapshot=plan.model_dump(), run_seed=3,
                            status="running",  # no max_parallel_shots; legacy default=1
                            shots=[ShotExecutionState(shot_id=f"s01_c0{i}", index=i)
                                   for i in (1, 2)])
    await manager._save_run(run)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    starts = []
    async def fake_execute(project_name, snapshot, shot_id):
        starts.append(shot_id)
        if shot_id == "s01_c01":
            first_started.set()
            await release_first.wait()
        await manager._edit_run(project_name, snapshot.run_id, lambda r: setattr(
            next(s for s in r.shots if s.shot_id == shot_id), "status", "completed"))
    monkeypatch.setattr(manager, "_execute_shot", fake_execute)
    task = asyncio.create_task(manager._run_loop(project.name, run.run_id))
    try:
        await asyncio.wait_for(first_started.wait(), timeout=5)
        await asyncio.sleep(0.15)
        assert starts == ["s01_c01"], "old single-slot checkpoint must be honored"
    finally:
        release_first.set()
        await asyncio.wait_for(task, timeout=5)
    assert starts == ["s01_c01", "s01_c02"]
    assert manager.get_run(project.name, run.run_id).status == RUN_COMPLETED

@pytest.mark.asyncio
async def test_recover_crash_window_child_created_before_parent_binding(tmp_path, monkeypatch):
    """The existing child wins even if its remaining dependencies are blocked."""
    project = movie([("s01", 1)])
    mount_project(tmp_path, monkeypatch, project)
    manager = ProjectProductionManager()
    run = ProjectProductionRun(
        run_id="prun_child_binding_gap", project_name=project.name,
        status="running", stage="scenes", max_parallel_scenes=2,
        shot_dependencies={"s01_c01": ["old_tail_not_yet_ready"]},
        scenes=[ProjectSceneExecutionState(scene_id="s01", index=0,
                                            status="pending", child_run_id=None)],
    )
    await manager._save_run(run)
    rebinding = []
    class FakeSceneManager:
        def find_by_parent_project_run(self, project_name, parent_id, scene_id):
            return SimpleNamespace(run_id="child_already_created", status="completed")
    async def start_or_reuse(project_name, parent_run, entry):
        rebinding.append(entry.scene_id)
        return SimpleNamespace(run_id="child_already_created")
    async def wait_child(project_name, child_id):
        return SimpleNamespace(run_id=child_id, status="completed", error=None)
    async def compose(project_name, run_id):
        await manager._edit_run(project_name, run_id, lambda r: setattr(r, "status", PRUN_COMPLETED))
    import src.ai.project_autopilot as module
    monkeypatch.setattr(module, "get_autopilot_manager", lambda: FakeSceneManager())
    monkeypatch.setattr(manager, "_validate_source_for_scene", lambda *_: None)
    monkeypatch.setattr(manager, "_scene_can_start", lambda *_: False)
    monkeypatch.setattr(manager, "_start_or_reuse_child", start_or_reuse)
    monkeypatch.setattr(manager, "_wait_child", wait_child)
    monkeypatch.setattr(manager, "_compose_project", compose)
    await asyncio.wait_for(manager._run_loop(project.name, run.run_id), timeout=4)
    recovered = manager.get_run(project.name, run.run_id)
    assert rebinding == ["s01"]
    assert recovered.scenes[0].child_run_id == "child_already_created"
    assert recovered.status == PRUN_COMPLETED


def test_frozen_reference_selection_change_is_detected_without_breaking_legacy(tmp_path, monkeypatch):
    """Changing a reference must not silently redraw the active C2 dependency DAG."""
    from src.ai.project_autopilot import build_project_source_signature
    project = movie([("s01", 2)])
    directory = mount_project(tmp_path, monkeypatch, project)
    manager = ProjectProductionManager()
    signature = build_project_source_signature(project)
    run = ProjectProductionRun(
        run_id="pinned", project_name=project.name,
        source_signature=signature,
        reference_asset_snapshot={"s01_c01": None, "s01_c02": None},
    )
    assert manager._validate_source_for_scene(project.name, run) is None
    # Project source signature intentionally excludes execution-only fields,
    # but the new frozen reference snapshot must close this missing gate.
    ProjectStore(directory).update_shot(
        "s01_c02", lambda sh: setattr(sh, "video_reference_asset_id", "prev_tail"))
    assert "接力" in manager._validate_source_for_scene(project.name, run)
    old = ProjectProductionRun(run_id="legacy", project_name=project.name,
                               source_signature=signature)
    assert manager._validate_source_for_scene(project.name, old) is None
