"""Actual C2 controller scheduling tests, with deterministic in-process workers.

These tests assert real task overlap and mid-execution checkpoints, not just
configuration values or a JobQueue worker-count setting.  No provider calls.
"""
import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import deps
from src.ai.autopilot import (
    SceneAutopilotManager, SceneAutopilotRun, ShotExecutionState, SHOT_COMPLETED,
    SHOT_RUNNING, RUN_COMPLETED, RUN_PAUSED,
)
from src.ai.project_autopilot import (
    ProjectProductionManager, ProjectProductionRun, ProjectSceneExecutionState,
    PSCENE_COMPLETED, PSCENE_PENDING, PRUN_COMPLETED,
)
from src.ai.production_planner import sanitize_plan
from src.ai.c2_parallel import (
    shot_dependencies, scene_dependencies, C2DependencyError,
)
from src.core.models import Project, Scene, Shot, StyleAnchor, Asset, VideoVersion
from src.core.store import ProjectStore


def movie(names):
    scenes = []
    for scene, count in names:
        scenes.append(Scene(scene_id=scene, summary=scene,
                            shots=[Shot(shot_id=f"{scene}_c{i:02d}", scene_id=scene,
                                        index=i, description=f"shot {i}")
                                   for i in range(1, count + 1)]))
    return Project(name="c2_parallel_test", style=StyleAnchor(art_style="anime"),
                   scenes=scenes)


def plans(project, preferences=None, openings=None):
    preferences, openings = preferences or {}, openings or {}
    result = {}
    for sc in project.scenes:
        raw = {"shots": [{"shot_id": shot.shot_id,
                          "opening_source": openings.get(shot.shot_id, "independent"),
                          "link_mode": "i2v", "reason": "Producer intent",
                          "parallel_preference": preferences.get(shot.shot_id, "allow")}
                         for shot in sc.shots]}
        plan = sanitize_plan(sc, raw, run_seed=40, project_name=project.name)
        plan.status = "approved"
        result[sc.scene_id] = plan
    return result


def mount_project(tmp_path, monkeypatch, project):
    project_dir = tmp_path / project.name
    (project_dir / "output").mkdir(parents=True, exist_ok=True)
    ProjectStore(project_dir).save(project)
    pm = SimpleNamespace(get_project_dir=lambda _: project_dir)
    monkeypatch.setattr(deps, "get_project_manager", lambda: pm)
    return project_dir


def source_done(project_dir, shot_id, version):
    def update(shot):
        shot.final_video = version
        shot.video.status = "done"
        shot.video_end_frame = Asset(status="done", url=f"https://host/{version}.png")
        shot.video_end_source_version = version
        shot.video_versions.append(VideoVersion(version_id=version, status="done", local="fixture.mp4"))
    ProjectStore(project_dir).update_shot(shot_id, update)


def test_film_dag_producer_choice_and_cross_scene_relay():
    project = movie([("s01", 2), ("s02", 2)])
    p = plans(project, preferences={"s02_c01": "required_serial"},
              openings={"s01_c02": "inherit_previous"})
    graph = shot_dependencies(project, p)
    assert graph["s01_c01"] == set()
    assert graph["s01_c02"] == {"s01_c01"}
    assert graph["s02_c01"] == {"s01_c02"}
    assert graph["s02_c02"] == set()
    collapsed = scene_dependencies(project, graph, ["s01", "s02"])
    assert collapsed["s02"] == {"s01"}
    project.scenes[1].shots[0].video_reference_asset_id = "prev_tail"
    p["s02"].shots[0].link_mode = "multi"
    assert shot_dependencies(project, p)["s02_c01"] == {"s01_c02"}


def test_reject_future_or_missing_video_source():
    project = movie([("s01", 2)])
    p = plans(project)
    p["s01"].shots[0].source_shot_id = "s01_c02"
    p["s01"].shots[0].requires_previous_video_end = True
    with pytest.raises(C2DependencyError, match="forward"):
        shot_dependencies(project, p)
    p["s01"].shots[0].source_shot_id = "missing"
    with pytest.raises(C2DependencyError, match="unknown"):
        shot_dependencies(project, p)


@pytest.mark.asyncio
async def test_two_independent_shots_really_overlap_with_a_tail_gate(tmp_path, monkeypatch):
    project = movie([("s01", 3)])
    pdir = mount_project(tmp_path, monkeypatch, project)
    plan = plans(project, openings={"s01_c03": "inherit_previous"})["s01"]
    manager = SceneAutopilotManager()
    run = SceneAutopilotRun(run_id=f"run_{uuid.uuid4().hex[:8]}",
                            project_name=project.name, scene_id="s01",
                            plan_id=plan.plan_id, plan_snapshot=plan.model_dump(),
                            run_seed=3, max_parallel_shots=2,
                            shots=[ShotExecutionState(shot_id=shot.shot_id, index=i)
                                   for i, shot in enumerate(project.scenes[0].shots)])
    await manager._save_run(run)
    open_first = asyncio.Event()
    open_second = asyncio.Event()
    first_pair = asyncio.Event()
    started = set()
    max_overlap = 0
    active = set()

    async def fake_execute(name, snapshot, shot_id):
        nonlocal max_overlap
        started.add(shot_id)
        active.add(shot_id)
        max_overlap = max(max_overlap, len(active))
        if len(started) >= 2:
            first_pair.set()
        if shot_id == "s01_c01":
            await open_first.wait()
        elif shot_id == "s01_c02":
            await open_second.wait()
        if shot_id == "s01_c02":
            source_done(pdir, shot_id, "version_new_c02")
        def mark_finished(latest):
            state = next(s for s in latest.shots if s.shot_id == shot_id)
            state.status = SHOT_COMPLETED
            if shot_id == "s01_c02":
                state.video_version_id = "version_new_c02"
        await manager._edit_run(name, snapshot.run_id, mark_finished)
        active.discard(shot_id)

    monkeypatch.setattr(manager, "_execute_shot", fake_execute)
    task = asyncio.create_task(manager._run_loop(project.name, run.run_id))
    await asyncio.wait_for(first_pair.wait(), timeout=5)
    assert started == {"s01_c01", "s01_c02"}
    assert max_overlap == 2
    saved = manager.get_run(project.name, run.run_id)
    assert sum(st.status == SHOT_RUNNING for st in saved.shots) == 2
    assert saved.shots[2].status != SHOT_RUNNING
    open_first.set()
    await asyncio.sleep(0.1)
    assert "s01_c03" not in started, "downstream must wait for *new* video end"
    open_second.set()
    await asyncio.wait_for(task, timeout=5)
    assert manager.get_run(project.name, run.run_id).status == RUN_COMPLETED
    assert "s01_c03" in started


@pytest.mark.asyncio
async def test_resume_orphan_running_shot_reuses_old_run_id(tmp_path, monkeypatch):
    project = movie([("s01", 1)])
    mount_project(tmp_path, monkeypatch, project)
    plan = plans(project)["s01"]
    manager = SceneAutopilotManager()
    run = SceneAutopilotRun(run_id="run_orphan", project_name=project.name,
                            scene_id="s01", plan_id=plan.plan_id,
                            plan_snapshot=plan.model_dump(), run_seed=40,
                            status="running",
                            shots=[ShotExecutionState(shot_id="s01_c01", index=0,
                                                      status=SHOT_RUNNING,
                                                      completed_stages=["activate_plan"])])
    await manager._save_run(run)
    starts = []
    async def run_shot(name, snap, sid):
        starts.append(snap.run_id)
        await manager._edit_run(name, snap.run_id, lambda latest: setattr(
            latest.shots[0], "status", SHOT_COMPLETED))
    monkeypatch.setattr(manager, "_execute_shot", run_shot)
    await asyncio.wait_for(manager._run_loop(project.name, run.run_id), timeout=5)
    assert starts == [run.run_id]
    assert manager.get_run(project.name, run.run_id).status == RUN_COMPLETED


@pytest.mark.asyncio
async def test_atomic_sibling_checkpoints_and_pause_flag(tmp_path, monkeypatch):
    project = movie([("s01", 2)])
    mount_project(tmp_path, monkeypatch, project)
    plan = plans(project)["s01"]
    manager = SceneAutopilotManager()
    run = SceneAutopilotRun(run_id="run_atomic", project_name=project.name,
                            scene_id="s01", plan_id=plan.plan_id,
                            plan_snapshot=plan.model_dump(), run_seed=40,
                            status="running",
                            shots=[ShotExecutionState(shot_id=shot.shot_id, index=i,
                                                      status=SHOT_RUNNING)
                                   for i, shot in enumerate(project.scenes[0].shots)])
    await manager._save_run(run)
    older_a = manager.get_run(project.name, run.run_id)
    older_b = manager.get_run(project.name, run.run_id)
    older_a.shots[0].completed_stages.append("video")
    older_b.shots[1].completed_stages.append("first_frame")
    await asyncio.gather(manager._save_shot_state(older_a, older_a.shots[0]),
                         manager._save_shot_state(older_b, older_b.shots[1]),
                         manager.request_pause(project.name, run.run_id))
    current = manager.get_run(project.name, run.run_id)
    assert current.shots[0].completed_stages == ["video"]
    assert current.shots[1].completed_stages == ["first_frame"]
    assert current.pause_requested


@pytest.mark.asyncio
async def test_project_starts_cross_scene_independent_jobs_before_waiting(tmp_path, monkeypatch):
    project = movie([("s01", 1), ("s02", 1), ("s03", 1)])
    mount_project(tmp_path, monkeypatch, project)
    manager = ProjectProductionManager()
    run = ProjectProductionRun(run_id="prun_parallel", project_name=project.name,
                               stage="scenes", status="running",
                               max_parallel_scenes=2,
                               scene_dependencies={"s01": [], "s02": [], "s03": ["s02"]},
                               scenes=[ProjectSceneExecutionState(scene_id=sc.scene_id, index=i)
                                       for i, sc in enumerate(project.scenes)])
    await manager._save_run(run)
    monkeypatch.setattr(manager, "_validate_source_for_scene", lambda *_: None)
    started = []
    first_pair = asyncio.Event()
    allow_s02 = asyncio.Event()
    allow_s01 = asyncio.Event()
    async def start_child(name, snapshot, entry):
        started.append(entry.scene_id)
        if len(started) >= 2:
            first_pair.set()
        return SimpleNamespace(run_id=f"child_{entry.scene_id}")
    async def await_child(name, child_id):
        if child_id == "child_s01":
            await allow_s01.wait()
        elif child_id == "child_s02":
            await allow_s02.wait()
        return SimpleNamespace(run_id=child_id, status="completed", error=None)
    async def compose(name, rid):
        await manager._edit_run(name, rid, lambda latest: setattr(latest, "status", PRUN_COMPLETED))
    monkeypatch.setattr(manager, "_start_or_reuse_child", start_child)
    monkeypatch.setattr(manager, "_wait_child", await_child)
    monkeypatch.setattr(manager, "_compose_project", compose)
    task = asyncio.create_task(manager._run_loop(project.name, run.run_id))
    await asyncio.wait_for(first_pair.wait(), timeout=5)
    assert set(started) == {"s01", "s02"}
    assert "s03" not in started
    allow_s01.set()
    await asyncio.sleep(0.15)
    assert "s03" not in started, "scene 3 waits for scene 2, not scene 1"
    allow_s02.set()
    await asyncio.wait_for(task, timeout=5)
    assert started == ["s01", "s02", "s03"]
    assert manager.get_run(project.name, run.run_id).status == PRUN_COMPLETED
