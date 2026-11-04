"""Recovery must not create a second controller for an already live run."""
import asyncio

import pytest

from src.ai.full_project_autopilot import FullProjectManager, FullProjectRun
from src.ai.project_autopilot import ProjectProductionManager, ProjectProductionRun
from src.ai.autopilot import SceneAutopilotManager, SceneAutopilotRun
from src.utils.project_manager import ProjectManager


@pytest.fixture(params=['full', 'project', 'scene'])
def manager_run(request, tmp_path, monkeypatch):
    import deps
    pm = ProjectManager(str(tmp_path / 'projects'))
    pm.create_project('p')
    monkeypatch.setattr(deps, 'project_manager', pm)
    if request.param == 'full':
        return FullProjectManager(), FullProjectRun(run_id='r', project_name='p', status='preparing')
    if request.param == 'project':
        return ProjectProductionManager(), ProjectProductionRun(run_id='r', project_name='p', status='running')
    return SceneAutopilotManager(), SceneAutopilotRun(run_id='r', project_name='p', scene_id='s01',
        plan_id='plan', plan_snapshot={}, run_seed=1, status='running')


def test_recovery_does_not_relaunch_or_rewrite_a_live_run(manager_run, monkeypatch):
    manager, run = manager_run
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def controller(*args):
            calls.append(args)
            entered.set()
            await release.wait()
        monkeypatch.setattr(manager, '_run_loop', controller)
        await manager._save_run(run)
        manager._launch_loop(run)
        await entered.wait()
        original = manager._run_tasks[run.run_id]
        # A recovery scan may arrive while a fresh controller is awaiting an AI
        # response. It must not clear requests or overwrite its source checkpoint.
        run.pause_requested = True
        await manager._save_run(run)
        before = manager._run_path(run.project_name, run.run_id).read_bytes()
        try:
            await manager.recover_runs()
            manager._launch_loop(run)
            await asyncio.sleep(0)
            assert manager._run_tasks[run.run_id] is original
            assert len(calls) == 1
            assert manager._run_path(run.project_name, run.run_id).read_bytes() == before
        finally:
            release.set()
            await asyncio.gather(*list(manager._run_tasks.values()), return_exceptions=True)
            await original
    asyncio.run(scenario())


def test_old_controller_completion_cannot_untrack_its_replacement(manager_run, monkeypatch):
    manager, run = manager_run
    async def scenario():
        release = asyncio.Event()
        calls = 0
        async def controller(*args):
            nonlocal calls
            calls += 1
            if calls > 1:
                await release.wait()
        monkeypatch.setattr(manager, '_run_loop', controller)
        manager._launch_loop(run)
        original = manager._run_tasks[run.run_id]
        await asyncio.sleep(0)  # original finished; its done callback is queued
        assert original.done()
        manager._launch_loop(run)
        replacement = manager._run_tasks[run.run_id]
        assert replacement is not original
        try:
            await asyncio.sleep(0)  # old callback must preserve the new owner
            assert manager._run_tasks[run.run_id] is replacement
        finally:
            release.set()
            await replacement
    asyncio.run(scenario())
