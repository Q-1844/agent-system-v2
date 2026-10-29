"""A completed inline tail must reach video preparation without new generation."""
import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.models import Asset, ImageAsset, Project, Scene, Shot
from src.core.store import ProjectConflictError, ProjectStore


def _write_png(path, w=192, h=108, seed=0):
    """Real decodable PNG（随机噪声——PNG 不可压缩，保证后续 JPEG q88 显著更小）。
    尾帧补托管入口 2026-10-29 起对 .png 做 PNG→JPEG q88 转换，假字节 fixture
    会撞 fail-loud 转换错误。"""
    import os as _os
    from PIL import Image as _I
    rnd = _os.urandom(w * h * 3)
    img = _I.frombytes('RGB', (w, h), rnd)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, 'PNG')
    return path


def test_inline_tail_is_published_and_both_selections_stay_consistent(tmp_path, monkeypatch):
    import routers.shots as routes
    store = ProjectStore(tmp_path)
    shot = Shot(shot_id='s01_c01', scene_id='s01', index=1, link_mode='keyframes',
                first_frame=ImageAsset(candidates=[Asset(url='https://frame.invalid/first', status='done')], status='done'))
    store.save(Project(name='p', scenes=[Scene(scene_id='s01', shots=[shot])]))
    calls = []
    real_png_bytes = _write_png(tmp_path / 'seed.png').read_bytes()
    class Provider:
        async def generate_image(self, **kw):
            assert kw['want_b64'] is True
            calls.append('generate')
            return {'b64': base64.b64encode(real_png_bytes).decode()}
    class Host:
        async def upload(self, path):
            # 补托管入口对 .png 先做 PNG→JPEG q88——上传载体必须是
            # .hosted.jpg（体积压缩）且可解码，原 PNG 字节不再直传。
            assert path.endswith('.hosted.jpg'), path
            from PIL import Image as _I
            _I.open(path).verify()
            assert Path(path).stat().st_size < len(real_png_bytes)
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
    local = tmp_path/'tail.png'; _write_png(local)
    original_bytes = local.read_bytes()
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
    assert local.read_bytes() == original_bytes
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

# Round 02 real C2 sequel regression: Pages can take minutes to publish a
# selected tail. Other shots finish meanwhile and legitimately change the
# project signature. The tail update must rebase while retaining input identity.


def _round02_tail_fixture(tmp_path):
    local = tmp_path / 'paid-tail.png'
    _write_png(local)
    tail = Shot(
        shot_id='s01_c01', scene_id='s01', index=1, link_mode='keyframes',
        last_frame=ImageAsset(
            candidates=[Asset(local=str(local), status='done')], status='done'))
    unrelated = Shot(shot_id='s01_c02', scene_id='s01', index=2)
    store = ProjectStore(tmp_path)
    store.save(Project(name='round02', scenes=[Scene(scene_id='s01',
                                                   shots=[tail, unrelated])]))
    return store, local


def test_tail_host_rebases_over_other_worker_without_clobber(tmp_path, monkeypatch):
    import routers.shots as routes
    store, local = _round02_tail_fixture(tmp_path)
    class Host:
        async def upload(self, path):
            # PNG→JPEG q88 后上传载体是 .hosted.jpg，且源于原 PNG 同目录同 stem
            assert path == str(local).replace('.png', '.hosted.jpg')
            # The slow host yields long enough for another worker to commit.
            store.update_shot('s01_c02',
                              lambda s: setattr(s, 'description', 'completed in parallel'))
            return 'https://host.invalid/correct.png'
    monkeypatch.setattr(routes, 'get_rehost_host', Host)
    async def emit(*args): pass
    result = asyncio.run(routes._await_last_frame_url(store, 's01_c01', emit))
    saved = store.load()
    tail = saved.get_shot('s01_c01')
    assert result == 'https://host.invalid/correct.png'
    assert tail.last_frame.selected.url == result
    assert tail.last_frame_batches[-1].candidates[0].url == result
    assert saved.get_shot('s01_c02').description == 'completed in parallel'


def test_tail_rehost_compresses_png_before_upload(tmp_path, monkeypatch):
    """run #41/#42 根因回归：尾帧补托管必须走 PNG→JPEG q88（35.md 同款纪律），
    不能直传原始 PNG——github.io 对 Agnes 侧 ~25KB/s，1.27MB PNG 下载 50s+ 必超时。"""
    import routers.shots as routes
    local = _write_png(tmp_path / 'tail.png', w=320, h=180)  # 噪声 PNG ≈ 170KB
    png_bytes = local.read_bytes()
    store = ProjectStore(tmp_path)
    shot = Shot(shot_id='s01_c01', scene_id='s01', index=1, link_mode='keyframes',
                last_frame=ImageAsset(
                    candidates=[Asset(local=str(local), status='done')], status='done'))
    store.save(Project(name='p', scenes=[Scene(scene_id='s01', shots=[shot])]))
    seen = {}
    class Host:
        async def upload(self, path):
            seen['path'] = path
            seen['bytes'] = Path(path).read_bytes()
            return 'https://host.invalid/tail.jpg'
    monkeypatch.setattr(routes, 'get_rehost_host', Host)
    async def emit(*a): pass
    url = asyncio.run(routes._await_last_frame_url(store, shot.shot_id, emit))
    assert url == 'https://host.invalid/tail.jpg'
    # 上传载体：JPEG、可解码、体积显著小于原 PNG（q88 + 噪声不可压缩）
    assert seen['path'].endswith('.hosted.jpg')
    from PIL import Image as _I
    with _I.open(seen['path']) as im:
        assert im.format == 'JPEG' and im.size == (320, 180)
    assert len(seen['bytes']) < len(png_bytes) * 0.6
    # 原 PNG 证据不动；守卫 digest 锁的一直是原 PNG
    assert local.read_bytes() == png_bytes


def test_tail_host_preserves_other_worker_public_url(tmp_path, monkeypatch):
    import routers.shots as routes
    store, local = _round02_tail_fixture(tmp_path)
    class Host:
        async def upload(self, path):
            def publish_elsewhere(s):
                s.last_frame.selected.url = 'https://host.invalid/already-done.png'
                for batch in s.last_frame_batches:
                    for item in batch.candidates:
                        item.url = 'https://host.invalid/already-done.png'
            store.update_shot('s01_c01', publish_elsewhere)
            return 'https://host.invalid/redundant.png'
    monkeypatch.setattr(routes, 'get_rehost_host', Host)
    async def emit(*args): pass
    result = asyncio.run(routes._await_last_frame_url(store, 's01_c01', emit))
    tail = store.load().get_shot('s01_c01')
    assert result == 'https://host.invalid/already-done.png'
    assert tail.last_frame.selected.url == result
    assert tail.last_frame_batches[-1].candidates[0].url == result


@pytest.mark.parametrize('change', ['local_path', 'local_bytes', 'lock', 'status'])
def test_tail_host_rejects_superseded_selection_after_upload(tmp_path, monkeypatch, change):
    import routers.shots as routes
    store, local = _round02_tail_fixture(tmp_path)
    replacement = tmp_path / 'other-tail.png'
    _write_png(replacement)
    class Host:
        async def upload(self, path):
            if change == 'local_path':
                store.update_shot('s01_c01', lambda s: setattr(
                    s.last_frame.selected, 'local', str(replacement)))
            elif change == 'local_bytes':
                local.write_bytes(b'changed-payload')  # digest guard: bytes change, not format
            elif change == 'lock':
                store.update_shot('s01_c01', lambda s: setattr(s, 'locked', True))
            else:
                store.update_shot('s01_c01', lambda s: setattr(
                    s.last_frame, 'status', 'failed'))
            return 'https://host.invalid/stale.png'
    monkeypatch.setattr(routes, 'get_rehost_host', Host)
    async def emit(*args): pass
    with pytest.raises(ProjectConflictError):
        asyncio.run(routes._await_last_frame_url(store, 's01_c01', emit))
    tail = store.load().get_shot('s01_c01')
    assert not tail.last_frame.selected.url
    assert all(not candidate.url for batch in tail.last_frame_batches
               for candidate in batch.candidates)


def _round02_first_frame_fixture(tmp_path):
    local = tmp_path / 'paid-first.png'
    local.write_bytes(b'stable-first-frame')
    first = Shot(shot_id='s01_c01', scene_id='s01', index=1,
                 first_frame=ImageAsset(
                     candidates=[Asset(local=str(local), status='done')], status='done'))
    unrelated = Shot(shot_id='s01_c02', scene_id='s01', index=2)
    store = ProjectStore(tmp_path)
    store.save(Project(name='round02', scenes=[Scene(scene_id='s01',
                                                  shots=[first, unrelated])]))
    shot = store.load().get_shot('s01_c01')
    import hashlib
    args = {
        'selected_id': shot.selected_first_frame,
        'selected_index': shot.first_frame.selected_index,
        'source_local': str(local),
        'source_digest': hashlib.sha256(local.read_bytes()).digest(),
        'locked': shot.locked,
        'url': 'https://host.invalid/uploaded-first.png',
    }
    return store, local, args


def test_first_frame_host_rebases_without_erasing_parallel_shot(tmp_path):
    import routers.shots as routes
    store, local, args = _round02_first_frame_fixture(tmp_path)
    store.update_shot('s01_c02',
                      lambda s: setattr(s, 'description', 'unrelated parallel finish'))
    saved = routes._commit_hosted_first_frame_url(store, 's01_c01', **args)
    assert saved.first_frame.selected.url == args['url']
    assert saved.first_frame_batches[-1].candidates[0].url == args['url']
    assert store.load().get_shot('s01_c02').description == 'unrelated parallel finish'


@pytest.mark.parametrize('change', ['selection', 'path', 'bytes', 'locked'])
def test_first_frame_host_rejects_invalidated_input(tmp_path, change):
    import routers.shots as routes
    store, local, args = _round02_first_frame_fixture(tmp_path)
    if change == 'selection':
        store.update_shot('s01_c01',
                          lambda s: setattr(s, 'selected_first_frame', 'new-candidate'))
    elif change == 'path':
        alternate = tmp_path / 'new-first.png'
        alternate.write_bytes(b'another-image')
        store.update_shot('s01_c01',
                          lambda s: setattr(s.first_frame.selected, 'local', str(alternate)))
    elif change == 'bytes':
        local.write_bytes(b'altered-source-after-upload')
    else:
        store.update_shot('s01_c01', lambda s: setattr(s, 'locked', True))
    with pytest.raises(ProjectConflictError):
        routes._commit_hosted_first_frame_url(store, 's01_c01', **args)
    saved = store.load().get_shot('s01_c01')
    assert not saved.first_frame.selected.url
