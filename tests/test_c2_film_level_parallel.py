"""Film-wide C2 scheduling: scenes are *containers*, not implicit barriers.

Proofs use the genuine project + scene coordinator and persisted checkpoints.
Only the paid image/video stages are replaced by deterministic fake workers.
"""
import asyncio
from pathlib import Path

import pytest

import src.ai.project_autopilot as project_api
from src.ai.autopilot import SceneAutopilotManager, SHOT_COMPLETED
from src.ai.project_autopilot import ProjectProductionManager, PRUN_COMPLETED
from src.ai.production_planner import SceneProductionPlan
from src.core.store import ProjectStore
from tests.test_c2_dynamic_parallel import movie, plans, mount_project, source_done


@pytest.mark.asyncio
async def test_independent_later_scene_shot_overlaps_while_tail_shot_waits(tmp_path, monkeypatch):
    """B2 can run alongside A1 even while B1 needs A1's new actual tail."""
    from routers.production_planner import _write_plans
    project = movie([("s01", 1), ("s02", 2)])
    # A stale old ACCEPTED source must never unblock a new downstream shot.
    project.scenes[1].shots[0].video_reference_asset_id = "prev_tail"
    root = mount_project(tmp_path, monkeypatch, project)
    source_done(root, "s01_c01", "old_accepted")
    all_plans = plans(project)
    all_plans["s02"].shots[0].link_mode = "multi"
    _write_plans(root, all_plans)

    parent = ProjectProductionManager()
    scene = SceneAutopilotManager()
    monkeypatch.setattr(project_api, "get_autopilot_manager", lambda: scene)
    monkeypatch.setattr(project_api, "get_project_autopilot_manager", lambda: parent)
    upstream_started = asyncio.Event()
    independent_started = asyncio.Event()
    dependent_started = asyncio.Event()
    let_upstream_finish = asyncio.Event()
    started = []

    async def fake_execute(project_name, child, shot_id):
        started.append(shot_id)
        if shot_id == "s01_c01":
            upstream_started.set()
            await let_upstream_finish.wait()
            source_done(root, shot_id, "new_accepted")
        if shot_id == "s02_c02":
            independent_started.set()
        if shot_id == "s02_c01":
            dependent_started.set()
            assert ProjectStore(root).load().get_shot("s01_c01").final_video == "new_accepted"
        def finish(snapshot):
            state = next(st for st in snapshot.shots if st.shot_id == shot_id)
            state.status = SHOT_COMPLETED
            state.video_version_id = "new_accepted" if shot_id == "s01_c01" else "v_" + shot_id
        await scene._edit_run(project_name, child.run_id, finish)

    async def fake_compose(project_name, run_id):
        await parent._edit_run(project_name, run_id,
                               lambda r: setattr(r, "status", PRUN_COMPLETED))

    monkeypatch.setattr(scene, "_execute_shot", fake_execute)
    monkeypatch.setattr(parent, "_compose_project", fake_compose)
    run = await parent.start_run(project.name, max_parallel_scenes=2)
    try:
        await asyncio.wait_for(upstream_started.wait(), 6)
        await asyncio.wait_for(independent_started.wait(), 6)
        assert "s02_c01" not in started, "Old ACCEPTED source bypassed the new tail gate"
        assert {"s01_c01", "s02_c02"} <= set(started)
        saved = parent.get_run(project.name, run.run_id)
        assert len([e for e in saved.scenes if e.child_run_id]) == 2
        let_upstream_finish.set()
        await asyncio.wait_for(dependent_started.wait(), 6)
        await asyncio.wait_for(parent._run_tasks[run.run_id], 6)
        assert parent.get_run(project.name, run.run_id).status == PRUN_COMPLETED
    finally:
        let_upstream_finish.set()
        for task in list(parent._run_tasks.values()) + list(scene._run_tasks.values()):
            if not task.done():
                task.cancel()
        await asyncio.gather(*parent._run_tasks.values(), *scene._run_tasks.values(),
                             return_exceptions=True)


@pytest.mark.asyncio
async def test_cross_scene_tail_can_start_before_whole_upstream_scene_finishes(tmp_path, monkeypatch):
    """A1 is complete, A2 still runs: B1 may consume A1 without waiting for A2."""
    from routers.production_planner import _write_plans
    project = movie([("s01", 2), ("s02", 1)])
    root = mount_project(tmp_path, monkeypatch, project)
    all_plans = plans(project)
    dep = all_plans["s02"].shots[0]
    dep.opening_source = "reference_previous"
    dep.source_shot_id = "s01_c01"
    dep.requires_previous_video_end = True
    _write_plans(root, all_plans)

    parent = ProjectProductionManager()
    scene = SceneAutopilotManager()
    monkeypatch.setattr(project_api, "get_autopilot_manager", lambda: scene)
    monkeypatch.setattr(project_api, "get_project_autopilot_manager", lambda: parent)
    pending_a2 = asyncio.Event()
    a2_started = asyncio.Event()
    b1_started = asyncio.Event()
    async def fake_execute(project_name, child, shot_id):
        if shot_id == "s01_c02":
            a2_started.set()
            await pending_a2.wait()
        if shot_id == "s01_c01":
            source_done(root, shot_id, "new_a1")
        if shot_id == "s02_c01":
            assert not pending_a2.is_set(), "cross-scene launch waited for irrelevant A2"
            b1_started.set()
        def finish(snapshot):
            state = next(st for st in snapshot.shots if st.shot_id == shot_id)
            state.status = SHOT_COMPLETED
            state.video_version_id = "new_a1" if shot_id == "s01_c01" else "v_" + shot_id
        await scene._edit_run(project_name, child.run_id, finish)
    async def fake_compose(project_name, run_id):
        await parent._edit_run(project_name, run_id,
                               lambda r: setattr(r, "status", PRUN_COMPLETED))
    monkeypatch.setattr(scene, "_execute_shot", fake_execute)
    monkeypatch.setattr(parent, "_compose_project", fake_compose)
    run = await parent.start_run(project.name, max_parallel_scenes=2)
    try:
        await asyncio.wait_for(a2_started.wait(), 6)
        await asyncio.wait_for(b1_started.wait(), 6)
        assert parent.get_run(project.name, run.run_id).scenes[0].status != "completed"
        pending_a2.set()
        await asyncio.wait_for(parent._run_tasks[run.run_id], 6)
        assert parent.get_run(project.name, run.run_id).status == PRUN_COMPLETED
    finally:
        pending_a2.set()
        for task in list(parent._run_tasks.values()) + list(scene._run_tasks.values()):
            if not task.done():
                task.cancel()
        await asyncio.gather(*parent._run_tasks.values(), *scene._run_tasks.values(),
                             return_exceptions=True)

@pytest.mark.asyncio
async def test_failed_upstream_does_not_leave_dependent_scene_waiting_forever(tmp_path, monkeypatch):
    """A fails; B may keep its independent work, but its relay cannot hang."""
    from routers.production_planner import _write_plans
    project = movie([("s01", 1), ("s02", 2)])
    project.scenes[1].shots[0].video_reference_asset_id = "prev_tail"
    root = mount_project(tmp_path, monkeypatch, project)
    plan_by_scene = plans(project)
    plan_by_scene["s02"].shots[0].link_mode = "multi"
    _write_plans(root, plan_by_scene)

    parent = ProjectProductionManager()
    scene = SceneAutopilotManager()
    monkeypatch.setattr(project_api, "get_autopilot_manager", lambda: scene)
    monkeypatch.setattr(project_api, "get_project_autopilot_manager", lambda: parent)
    independent_finished = asyncio.Event()
    async def fake_execute(project_name, child, shot_id):
        if shot_id == "s01_c01":
            await scene._fail_shot(child, shot_id, "injected permanent failure")
            return
        if shot_id == "s02_c01":
            raise AssertionError("Failed upstream must never release this shot")
        if shot_id == "s02_c02":
            independent_finished.set()
        def finish(snapshot):
            state = next(st for st in snapshot.shots if st.shot_id == shot_id)
            state.status = SHOT_COMPLETED
            state.video_version_id = "v_" + shot_id
        await scene._edit_run(project_name, child.run_id, finish)
    monkeypatch.setattr(scene, "_execute_shot", fake_execute)
    run = await parent.start_run(project.name, max_parallel_scenes=2)
    try:
        await asyncio.wait_for(parent._run_tasks[run.run_id], 8)
        saved = parent.get_run(project.name, run.run_id)
        assert saved.status == "failed"
        assert saved.scenes[0].status == "failed"
        assert saved.scenes[1].status == "blocked"
        assert independent_finished.is_set()
    finally:
        for task in list(parent._run_tasks.values()) + list(scene._run_tasks.values()):
            if not task.done():
                task.cancel()
        await asyncio.gather(*parent._run_tasks.values(), *scene._run_tasks.values(),
                             return_exceptions=True)
