"""A completed inline tail must reach video preparation without new generation."""
import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.models import Asset, ImageAsset, Project, Scene, Shot
from src.core.store import ProjectConflictError, ProjectStore


def test_inline_tail_is_published_and_both_selections_stay_consistent(tmp_path, monkeypatch):
    import routers.shots as routes
    store = ProjectStore(tmp_path)
    shot = Shot(shot_id='s01_c01', scene_id='s01', index=1, link_mode='keyframes',
                first_frame=ImageAsset(candidates=[Asset(url='https://frame.invalid/first', status='done')], status='done'))
    store.save(Project(name='p', scenes=[Scene(scene_id='s01', shots=[shot])]))
    calls = []
    class Provider:
        async def generate_image(self, **kw):
            assert kw['want_b64'] is True
            calls.append('generate')
            return {'b64': base64.b64encode(b'original-inline-image').decode()}
    class Host:
        async def upload(self, path):
            assert Path(path).read_bytes() == b'original-inline-image'
            calls.append('upload')
            return 'https://host.invalid/tail.png'
    monkeypatch.setattr(routes, 'get_project_manager', lambda: SimpleNamespace(get_project_dir=lambda _: tmp_path))
    monkeypatch.setattr(routes, 'get_agnes_client', Provider)
    monkeypatch.setattr(routes, 'get_rehost_host', Host)
    monkeypatch.setattr(routes, 'shot_has_visual_production_binding', lambda *a: False)
    monkeypatch.setattr(routes, 'build_last_frame_prompt', lambda *a, **kw: 'end')
    async def emit(*a): pass
    job = SimpleNamespace(project='p', shot_id='s01_c01', params={'origin_run_id': 'original'})
    async def run():
        await routes._handler_last_frame(job, emit)
        saved = store.load().get_shot(job.shot_id)
        routes.choose_last_frame_business(store, job.shot_id, saved.selected_last_frame)
        for _ in range(2):
            assert await routes._await_last_frame_url(store, job.shot_id, emit) == 'https://host.invalid/tail.png'
    asyncio.run(run())
    saved = store.load().get_shot(job.shot_id)
    assert saved.last_frame.url_ready
    assert saved.last_frame_batches[-1].candidates[0].url == saved.last_frame.selected.url
    assert calls == ['generate', 'upload']


@pytest.mark.parametrize('failure', ['upload', 'selection_changed'])
def test_failed_or_stale_upload_preserves_the_completed_tail(tmp_path, monkeypatch, failure):
    import routers.shots as routes
    local = tmp_path/'tail.png'; local.write_bytes(b'paid-image')
    store = ProjectStore(tmp_path)
    shot = Shot(shot_id='s01_c01', scene_id='s01', index=1, link_mode='keyframes',
                last_frame=ImageAsset(candidates=[Asset(local=str(local), status='done')], status='done'))
    store.save(Project(name='p', scenes=[Scene(scene_id='s01', shots=[shot])]))
    class Host:
        async def upload(self, path):
            if failure == 'upload': raise OSError('host unavailable')
            store.update_shot(shot.shot_id, lambda s: setattr(s, 'selected_last_frame', 'new-selection'))
            return 'https://host.invalid/old-tail'
    monkeypatch.setattr(routes, 'get_rehost_host', Host)
    async def emit(*a): pass
    with pytest.raises(OSError if failure == 'upload' else ProjectConflictError):
        asyncio.run(routes._await_last_frame_url(store, shot.shot_id, emit))
    saved = store.load().get_shot(shot.shot_id)
    assert saved.last_frame.ready and not saved.last_frame.url_ready
    assert local.read_bytes() == b'paid-image'
    if failure == 'selection_changed': assert saved.selected_last_frame == 'new-selection'


@pytest.mark.parametrize('params,error,stage,retried', [
    ({'video_pre_submit_failure':'last_frame_url'}, 'host unavailable', 'rehost', True),
    ({}, '等待尾帧超时（尾帧尚未生成完成），请重试', 'wait', True),
    ({'video_pre_submit_failure':'last_frame_url','provider_task_id':'paid'}, 'host unavailable', 'rehost', False),
    ({'video_pre_submit_failure':'last_frame_url','submission_intent_id':'unknown'}, 'host unavailable', 'rehost', False),
    ({'video_pre_submit_failure':'last_frame_url','dispatch_prompt':'submitted'}, 'host unavailable', 'rehost', False),
    ({}, 'provider timeout', 'submit', False),
    ({}, '等待尾帧超时（尾帧尚未生成完成），请重试', 'submit', False),
])
def test_only_proven_pre_submit_video_failure_can_retry(monkeypatch, params, error, stage, retried):
    import deps
    from src.ai.autopilot import SceneAutopilotManager
    existing = SimpleNamespace(job_id='same-job', state='failed', params=params, error=error, error_stage=stage)
    calls = []
    def retry(job_id): calls.append(job_id); return existing
    monkeypatch.setattr(deps, 'job_queue', SimpleNamespace(find_by_origin=lambda *a: existing, retry=retry))
    result = asyncio.run(SceneAutopilotManager()._enqueue_autopilot_job('p','s','video','key','original'))
    assert result is existing and bool(calls) == retried
