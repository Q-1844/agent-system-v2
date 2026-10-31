"""Incident r46: exercise real queue gates and native handlers across day/hop death.

All providers are local fakes/MockTransport; no paid network requests.
"""
import asyncio
import copy
import datetime as dt
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.api.agnes_client import (AgnesClient, AgnesError, DailyQuotaExhausted,
                                  PollTransientError, VideoSubmissionUncertain)
from src.core.job_queue import Job, JobQueue
from src.core.provider_errors import quota_resume_after, retry_after_seconds
from src.core.quota import QuotaLedger
from tools.full_project_cloud_acceptance import BoundedRealClient

DAILY = '已达到 API 用量上限，请在 **2026-10-31 00:00** 之后重试。 (request id: 20261030054158)'


class Journal:
    def __init__(self):
        self.doc = {}
        self.writes = []

    def save(self, **fields):
        self.doc.update(copy.deepcopy(fields))
        self.writes.append(copy.deepcopy(self.doc))


class Slots:
    @asynccontextmanager
    async def slot(self, bucket):
        yield


def http_client(tmp_path, handler):
    client = AgnesClient({'base_url': 'https://agnes.invalid', 'api_key': 'test',
                          'models': {'video': 'agnes-video-2.5-flash'}},
                         quota=QuotaLedger(tmp_path/'quota.json'),
                         transport=httpx.MockTransport(handler))
    client.limiter = Slots()
    return client


def test_daily_429_stops_after_one_post_without_sleep_or_charge(tmp_path, monkeypatch):
    calls, sleeps = [], []
    def reject(req):
        calls.append(req)
        return httpx.Response(429, json={'error': {'message': DAILY}})
    client = http_client(tmp_path, reject)
    async def sleep(seconds): sleeps.append(seconds)
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    with pytest.raises(DailyQuotaExhausted) as caught:
        asyncio.run(client.create_video('p', mode='t2v', duration_s=10))
    assert len(calls) == 1 and not sleeps
    assert DAILY in caught.value.body
    assert client.quota.snapshot()['video_seconds']['used'] == 0


def test_rpm_429_retries_with_retry_after_without_daily_gate(tmp_path, monkeypatch):
    calls, sleeps = [], []
    def respond(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(429, text='Too Many Requests', headers={'Retry-After': '3'})
        return httpx.Response(200, json={'video_id': 'accepted'})
    client = http_client(tmp_path, respond)
    async def sleep(seconds): sleeps.append(seconds)
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    assert asyncio.run(client.create_video('p', mode='t2v', duration_s=10))['video_id'] == 'accepted'
    assert len(calls) == 2 and sleeps == [3]
    assert client.quota.snapshot()['video_seconds']['used'] == 10


@pytest.mark.parametrize('status', [429, 503, 504])
def test_poll_retry_exhaustion_is_transient_and_preserves_id(tmp_path, monkeypatch, status):
    seen = []
    def fail(req):
        seen.append(req)
        return httpx.Response(status, text='temporary')
    client = http_client(tmp_path, fail)
    monkeypatch.setattr(asyncio, 'sleep', AsyncMock())
    with pytest.raises(PollTransientError):
        asyncio.run(client.poll_video('paid', interval=1, max_consecutive_failures=1))
    assert len(seen) == 3
    assert all(r.method == 'GET' and r.url.params['video_id'] == 'paid' for r in seen)


def test_provider_reset_date_and_http_date_retry_after():
    now = dt.datetime(2026, 10, 30, 12, tzinfo=dt.timezone.utc)
    error = AgnesError(DAILY, 429)
    assert quota_resume_after(error, now) == '2026-10-30T16:10:00Z'
    future = AgnesError(DAILY.replace('2026-10-31', '2026-11-02'), 429)
    assert quota_resume_after(future, now) == '2026-11-01T16:10:00Z'
    assert retry_after_seconds('Fri, 30 Oct 2026 12:01:00 GMT', now) == 60
    assert retry_after_seconds('bad', now) is None


@pytest.mark.parametrize('params', [
    {'provider_submit_state': 'unknown'}, {'provider_submit_state': 'submitting'},
    {'provider_task_id': 'paid'}, {'unattached_provider_receipt': {'provider_task_id': 'paid'}},
])
def test_stale_quota_text_cannot_override_uncertain_or_accepted_facts(tmp_path, params):
    queue = JobQueue(tmp_path/'jobs.json', {}, require_ownership=False)
    job = Job('j', 'p', 'video', state='failed', error='重试 5 次仍失败: 429: '+DAILY,
              params={'submission_intent_id': 'old', **params})
    queue.jobs[job.job_id] = job
    async def run(): assert queue.retry('j') is None
    asyncio.run(run())


def test_quota_rejection_real_queue_next_day_reaches_native_handler(tmp_path, monkeypatch):
    """Real _handler_video + real JobQueue.retry + _do_video; two process lifetimes."""
    import deps
    from routers.shots import _handler_video
    from src.ai.autopilot import SceneAutopilotManager, SceneAutopilotRun, ShotExecutionState
    from src.core.store import ProjectStore
    from tests.test_p55_recovery import (FakeAgnes, FakeRehost, _FakePM, _save_proj,
                                        _mark_ff_ready, _shot, PROJECT)
    project_dir = tmp_path/PROJECT
    _save_proj(project_dir, [_shot('s01_c01'), _shot('s01_c02', idx=2)])
    for sid in ('s01_c01', 's01_c02'): _mark_ff_ready(project_dir, sid)
    provider = FakeAgnes()
    posts = []
    async def submit(*args, **kwargs):
        posts.append(kwargs)
        if len(posts) == 2: raise AgnesError('429: '+DAILY, 429, DAILY)
        return {'video_id': 'old-paid' if len(posts) == 1 else 'new-paid'}
    provider.create_video = submit
    journal = Journal()
    wrapper = BoundedRealClient(provider, journal, project_dir, tmp_path/'evidence')
    wrapper.plan = {}  # planning is outside this production-boundary test
    monkeypatch.setattr(deps, 'agnes_client', wrapper)
    monkeypatch.setattr(deps, 'project_manager', _FakePM(project_dir))
    monkeypatch.setattr(deps, '_rehost_host', FakeRehost())
    path = tmp_path/'jobs.json'
    queue = JobQueue(path, {'video': _handler_video}, require_ownership=False)
    monkeypatch.setattr(deps, 'job_queue', queue)
    async def day_one():
        first = queue.submit(PROJECT, 'video', 's01_c01', origin_run_id='prior')
        first.state = 'running'
        await queue._run_job(first, 'test')
        assert first.state == 'done'
        job = queue.submit(PROJECT, 'video', 's01_c02', origin_run_id='r',
                           autopilot_stage_key='autopilot:r:s01_c02:video:1')
        job.attempts = 5  # persisted old scheduling count is not a creative failure budget
        job.state = 'running'
        await queue._run_job(job, 'test')
        assert job.state == 'failed' and job.params['provider_submit_state'] == 'rejected'
        assert queue.retry(job.job_id) is None  # still today
        return job.job_id
    jid = asyncio.run(day_one())
    old = ProjectStore(project_dir).load().get_shot('s01_c01')
    old_final, old_file = old.final_video, Path(old.video_versions[0].local)
    old_sha = hashlib.sha256(old_file.read_bytes()).hexdigest()
    assert journal.doc['phase'] == 'WAITING_QUOTA'
    assert [i['state'] for i in journal.doc['submission_intents']] == ['COMPLETED', 'REJECTED']
    assert len(journal.doc['video_attempts']) == 1
    # Move only the test clock facts across the day; keep IDs/media/counters intact.
    queue.jobs[jid].params['quota_resume_after'] = '2000-01-01T00:00:00Z'
    queue._save()
    journal.doc['quota_resume_after'] = '2000-01-01T00:00:00Z'
    resumed = BoundedRealClient(provider, journal, project_dir, tmp_path/'evidence')
    resumed.plan = {}
    resumed.restore_usage(journal.doc)
    queue = JobQueue(path, {'video': _handler_video}, require_ownership=False)
    monkeypatch.setattr(deps, 'job_queue', queue)
    monkeypatch.setattr(deps, 'agnes_client', resumed)
    state = ShotExecutionState(shot_id='s01_c02', index=2, attempts={'video': 2})
    run = SceneAutopilotRun(run_id='r', project_name=PROJECT, scene_id='s01',
                            plan_id='plan', plan_snapshot={}, run_seed=1, shots=[state])
    manager = SceneAutopilotManager()
    monkeypatch.setattr(manager, '_save_shot_state', AsyncMock())
    async def day_two():
        await queue.start()
        await asyncio.wait_for(manager._do_video(PROJECT, run, state, ProjectStore(project_dir), project_dir), 5)
    asyncio.run(day_two())
    assert len(posts) == 3 and queue.jobs[jid].state == 'done'
    assert queue.jobs[jid].attempts == 6
    assert queue.jobs[jid].params['submission_history'][0]['provider_submit_state'] == 'rejected'
    assert state.video_version_id
    assert len(journal.doc['video_attempts']) == 2
    assert [i['state'] for i in journal.doc['submission_intents']] == ['COMPLETED', 'REJECTED', 'COMPLETED']
    assert ProjectStore(project_dir).load().get_shot('s01_c01').final_video == old_final
    assert hashlib.sha256(old_file.read_bytes()).hexdigest() == old_sha


def test_local_quota_preflight_records_wait_without_post(tmp_path, monkeypatch):
    import deps
    from routers.shots import _handler_video
    from tests.test_p55_recovery import FakeAgnes, _FakePM, _save_proj, _mark_ff_ready, PROJECT
    from src.core.store import ProjectStore
    _save_proj(tmp_path); _mark_ff_ready(tmp_path, 's01_c01')
    provider = FakeAgnes()
    provider.quota = QuotaLedger(tmp_path/'quota.json', {'video_seconds': 5})
    provider.quota.consume('video_seconds', 5)
    journal = Journal()
    wrapper = BoundedRealClient(provider, journal, tmp_path, tmp_path/'evidence')
    monkeypatch.setattr(deps, 'agnes_client', wrapper)
    monkeypatch.setattr(deps, 'project_manager', _FakePM(tmp_path))
    job = Job('j', PROJECT, 'video', 's01_c01')
    with pytest.raises(DailyQuotaExhausted):
        asyncio.run(_handler_video(job, AsyncMock()))
    assert provider.calls == []
    assert job.params['provider_submit_state'] == 'not_sent'
    assert job.params['failure_category'] == 'daily_quota'
    assert journal.doc['phase'] == 'WAITING_QUOTA'
    assert ProjectStore(tmp_path).load().get_shot('s01_c01').video.status != 'generating'


def test_frame_rounding_does_not_refuse_the_last_whole_seconds():
    from routers.shots import _video_preflight
    from tests.test_p55_recovery import _shot
    from src.core.models import ImageAsset, Asset
    shot = _shot('s01_c01', dur=10)
    shot.video.num_frames = 241
    shot.video.frame_rate = 24
    shot.first_frame = ImageAsset(status='done', candidates=[Asset(status='done', url='https://frame')])
    amounts = []
    quota = SimpleNamespace(can=lambda kind, amount: (amounts.append(amount) or True, ''))
    assert _video_preflight(shot, quota, 241/24, True)[0]
    assert amounts == [10]


@pytest.mark.parametrize('before_receipt', [False, True])
def test_restore_accepted_inflight_is_poll_only(tmp_path, before_receipt):
    from tests.relstore.test_media_download_resume import snapshot
    from src.core.models import Project, Scene, Shot, VideoAsset
    from src.core.store import ProjectStore
    from src.generation.video_commit import video_input_signature
    from tools.full_project_resume import restore_snapshot
    stage, cp, provenance = snapshot(tmp_path)
    project_dir = stage/'projects/cloud3min_unit'
    shot = Shot(shot_id='s_c1', scene_id='s', index=1,
                video=VideoAsset(status='generating', video_id=None if before_receipt else 'paid1'))
    from src.core.models import ImageAsset, Asset
    old_frame = '/old/checkout/projects/cloud3min_unit/output/first.png'
    (project_dir/'output/first.png').write_bytes(b'original-frame')
    shot.first_frame = ImageAsset(status='done', candidates=[
        Asset(status='done', local=old_frame, url='https://frame.invalid/first.png')])
    store = ProjectStore(project_dir)
    store.save(Project(name='cloud3min_unit', scenes=[Scene(scene_id='s', shots=[shot])]))
    project = store.load()
    params = {'submission_intent_id': 'intent', 'provider_submit_state': 'submitting' if before_receipt else 'accepted',
              'dispatch_input_signature': video_input_signature(project, project.get_shot('s_c1')),
              'dispatch_final_video': None}
    if not before_receipt: params['provider_task_id'] = 'paid1'
    job = Job('j', 'cloud3min_unit', 'video', 's_c1', state='running', params=params)
    from dataclasses import asdict
    (stage/'cache/jobs.json').write_text(json.dumps({'j': asdict(job)}))
    cp['video_attempts'][0].update(attempt_id='intent', logical_shot_id='s_c1')
    cp['phase'] = 'INTERRUPTED'
    full = project_dir/'output/full_runs/full/run.json'
    data = json.loads(full.read_text()); data['status'] = 'producing'; full.write_text(json.dumps(data))
    workspace = tmp_path/'new-runner'
    result = restore_snapshot(stage, workspace, 'unit', cp, provenance)
    assert result['recovered_poll_jobs'] == ['j']
    saved = json.loads((workspace/'cache/jobs.json').read_text())['j']
    assert saved['state'] == 'queued' and saved['params']['provider_task_id'] == 'paid1'
    shot = ProjectStore(workspace/'projects/cloud3min_unit').load().get_shot('s_c1')
    assert shot.video.video_id == 'paid1' and shot.video.status == 'generating'
    rebased = ProjectStore(workspace/'projects/cloud3min_unit').load()
    assert saved['params']['dispatch_input_signature'] == video_input_signature(rebased, shot)
    assert saved['params']['recovery_original_input_signature'] == params['dispatch_input_signature']
    assert saved['params']['dispatch_input_signature'] != params['dispatch_input_signature']
    assert Path(shot.first_frame.candidates[0].local).read_bytes() == b'original-frame'

    assert json.loads((workspace/'projects/cloud3min_unit/output/full_runs/full/run.json').read_text())['status'] == 'paused'


def test_patrol_reaches_nonces_after_first_hundred_branches(monkeypatch):
    from tools import resume_patrol
    pages = []
    def branches(path, token):
        pages.append(path)
        if len(pages) == 1: return [{'name': f'archived/{i}'} for i in range(100)]
        return [{'name': 'test/e2e-live-film'}]
    monkeypatch.setattr(resume_patrol, 'b1_get', branches)
    assert resume_patrol.list_e2e_nonces('test') == ['live-film']
    assert pages[-1].endswith('&page=2')


def test_named_source_project_is_included_in_actions_evidence(tmp_path, monkeypatch):
    from tools.full_project_cloud_acceptance import write_evidence_project_output
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GITHUB_OUTPUT', str(tmp_path/'outputs'))
    write_evidence_project_output({'project_name': 'my-original-film'})
    assert (tmp_path/'outputs').read_text() == 'project_path=projects/my-original-film/\n'


def test_poll_resume_on_real_queue_never_repeats_a_paid_post(tmp_path, monkeypatch):
    import deps
    from routers.shots import _handler_video
    from tests.test_p55_recovery import FakeAgnes, FakeRehost, _FakePM, _save_proj, _mark_ff_ready, PROJECT
    _save_proj(tmp_path); _mark_ff_ready(tmp_path, 's01_c01')
    provider = FakeAgnes()
    poll = provider.poll_video
    counts = []
    async def once(*args, **kwargs):
        counts.append(args[0])
        if len(counts) == 1: raise PollTransientError('GET 503 after acceptance', 503)
        return await poll(*args, **kwargs)
    provider.poll_video = once
    monkeypatch.setattr(deps, 'agnes_client', provider)
    monkeypatch.setattr(deps, 'project_manager', _FakePM(tmp_path))
    monkeypatch.setattr(deps, '_rehost_host', FakeRehost())
    queue = JobQueue(tmp_path/'jobs.json', {'video': _handler_video}, require_ownership=False)
    async def run():
        job = queue.submit(PROJECT, 'video', 's01_c01')
        job.state = 'running'; await queue._run_job(job, 'test')
        assert job.state == 'failed' and job.params['failure_category'] == 'poll_transient'
        assert queue.retry(job.job_id) is job
        job.state = 'running'; await queue._run_job(job, 'test')
        assert job.state == 'done'
        # A replay with a confirmed task ID after delivery is idempotent, too.
        job.state = 'running'; await queue._run_job(job, 'test')
        assert job.state == 'done'
    asyncio.run(run())
    assert counts == ['vid_fake_1', 'vid_fake_1']
    assert provider.calls.count('create_video') == 1


@pytest.mark.parametrize('state,video_id', [('UNKNOWN', None), ('SUBMITTING', None),
                                           ('REJECTED', 'paid'), ('COMPLETED', 'lost')])
def test_intent_history_cannot_hide_unresolved_or_conflicting_receipts(tmp_path, state, video_id):
    wrapper = BoundedRealClient(object(), Journal(), tmp_path, tmp_path/'evidence')
    with pytest.raises(ValueError):
        wrapper.restore_usage({'video_attempts': [], 'submission_intents': [{'state': state, 'video_id': video_id}]})


@pytest.mark.parametrize('gate,expected', [('2999-01-01T00:00:00Z', 'WAITING_QUOTA'),
                                           ('2000-01-01T00:00:00Z', 'QUOTA_READY')])
def test_monitor_understands_daily_wait_after_failed_actions_run(gate, expected):
    from tests.relstore.test_full_project_watch import sample, NOW
    from tools.watch_full_project import observed_state
    config, run, cp = sample()
    run.update(status='completed', conclusion='failure')
    cp.update(phase='WAITING_QUOTA', terminal_status='quota_exhausted', quota_resume_after=gate)
    out = observed_state(config, run, cp, NOW)
    assert out['state'] == expected and out['accepted_tasks'] == 2


def test_terminal_provider_failure_keeps_paid_cost_but_closes_intent(tmp_path):
    from src.api.agnes_client import VideoGenerationFailed
    class Provider:
        async def create_video(self, *a, **kw): return {'video_id': 'failed-paid'}
        async def poll_video(self, *a, **kw): raise VideoGenerationFailed('explicit remote failed')
    journal = Journal()
    wrapper = BoundedRealClient(Provider(), journal, tmp_path, tmp_path/'evidence')
    wrapper.plan = {}
    async def run():
        await wrapper.create_video('p', duration_s=10)
        with pytest.raises(VideoGenerationFailed): await wrapper.poll_video('failed-paid')
    asyncio.run(run())
    assert journal.doc['video_attempts'][0]['provider_outcome'] == 'FAILED'
    assert journal.doc['submission_intents'][0]['state'] == 'FAILED'
    restored = BoundedRealClient(Provider(), journal, tmp_path, tmp_path/'evidence')
    restored.restore_usage(journal.doc)
    assert sum(a['seconds'] for a in restored.video_attempts) == 10


@pytest.mark.parametrize('reason', ['poll_transient', 'rate_limit', 'submission_unknown', 'not_sent'])
def test_infrastructure_failures_are_not_story_repair_requests(reason):
    from tools.full_project_cloud_acceptance import production_wait_reason
    job = Job('j', 'film', 'video', state='failed', params={'failure_category': reason})
    assert production_wait_reason(SimpleNamespace(quota_gate_after=None),
                                  SimpleNamespace(jobs={'j': job}), 'film') == reason


def test_completed_retry_does_not_leave_a_stale_infrastructure_wait():
    from tools.full_project_cloud_acceptance import production_wait_reason
    failed = Job('old', 'film', 'video', 's', state='failed', finished_at=10,
                 params={'failure_category': 'poll_transient'})
    done = Job('new', 'film', 'video', 's', state='done', finished_at=20)
    assert production_wait_reason(SimpleNamespace(quota_gate_after=None),
                                  SimpleNamespace(jobs={'old': failed, 'new': done}), 'film') is None


def test_wait_phase_cannot_resume_without_a_valid_calendar_gate():
    from tools.resume_dispatch import auto_resume_allowed
    assert not auto_resume_allowed({'phase': 'WAITING_QUOTA'})[0]
    assert retry_after_seconds('inf') is None
    assert retry_after_seconds('nan') is None


def test_cloud_initialization_defers_workers_until_after_restore(tmp_path, monkeypatch):
    import api_server
    import src.core.job_queue as jobs
    import src.resources.recovery as resources
    import src.scene_system.recovery as scenes
    from services import lifecycle
    queue = JobQueue(tmp_path/'jobs.json', {}, require_ownership=False)
    queue.start = AsyncMock()
    monkeypatch.setattr(jobs, 'JobQueue', lambda *a, **kw: queue)
    monkeypatch.setattr(api_server, 'BASE_DIR', tmp_path)
    monkeypatch.setattr(api_server, 'PipelineController', lambda *a: SimpleNamespace(
        agnes_client=object(), sensenova_text_client=None,
        project_manager=SimpleNamespace(base_dir=tmp_path)))
    monkeypatch.setattr(api_server.characters, '_init_default_characters', lambda: None)
    monkeypatch.setattr(resources, 'recover_all_projects', AsyncMock(return_value={}))
    monkeypatch.setattr(scenes, 'recover_all_projects', AsyncMock(return_value={}))
    recover = AsyncMock()
    monkeypatch.setattr(lifecycle, 'recover_pending_videos', recover)
    asyncio.run(api_server.startup(defer_production=True))
    queue.start.assert_not_awaited()
    recover.assert_not_awaited()
    assert not (tmp_path/'jobs.json').exists()


def test_cas_save_freezes_receipts_before_a_concurrent_runtime_update(tmp_path):
    import threading
    started, release = threading.Event(), threading.Event()
    snapshots = []
    class SlowJournal:
        def save(self, **fields):
            started.set()
            assert release.wait(3)
            snapshots.append(copy.deepcopy(fields))
    wrapper = BoundedRealClient(object(), SlowJournal(), tmp_path, tmp_path/'evidence')
    wrapper.video_attempts = [{'state': 'SUBMITTING', 'seconds': 10}]
    async def run():
        saving = asyncio.create_task(wrapper.persist())
        assert await asyncio.to_thread(started.wait, 3)
        wrapper.video_attempts[0].update(state='ACCEPTED', video_id='paid')
        release.set()
        await saving
    asyncio.run(run())
    assert snapshots[0]['video_attempts'] == [{'state': 'SUBMITTING', 'seconds': 10}]


def test_storage_429_after_acceptance_cannot_roll_back_a_paid_receipt(tmp_path):
    class Storage429(Exception):
        status = 429
    class LosingJournal(Journal):
        def save(self, **fields):
            super().save(**fields)
            if len(self.writes) == 2: raise Storage429('daily quota exceeded at journal service')
    provider = SimpleNamespace(create_video=AsyncMock(return_value={'video_id': 'paid'}))
    wrapper = BoundedRealClient(provider, LosingJournal(), tmp_path, tmp_path/'evidence')
    wrapper.plan = {}
    with pytest.raises(Storage429): asyncio.run(wrapper.create_video('p', duration_s=10))
    assert wrapper.video_attempts[0]['video_id'] == 'paid'
    assert wrapper.submission_intents[0]['state'] == 'ACCEPTED'
    assert wrapper.submission_blocked and wrapper.quota_gate_after is None


def test_stale_poll_error_never_reattaches_a_detached_candidate(tmp_path):
    queue = JobQueue(tmp_path/'jobs.json', {}, require_ownership=False)
    job = Job('j', 'film', 'video', 's', state='failed', params={
        'provider_task_id': 'paid', 'provider_submit_state': 'accepted',
        'failure_category': 'poll_transient', 'unattached_artifact': {'local': 'candidate.mp4'}})
    queue.jobs['j'] = job
    async def run(): assert queue.retry('j') is None
    asyncio.run(run())


def test_quota_learned_while_another_worker_waits_for_a_slot_blocks_its_post(tmp_path):
    calls = []
    async def run():
        second_waiting = asyncio.Event()
        lock = asyncio.Lock()
        class OrderedSlots:
            entries = 0
            @asynccontextmanager
            async def slot(self, bucket):
                self.entries += 1
                if self.entries == 2: second_waiting.set()
                async with lock: yield
        async def respond(request):
            calls.append(request)
            if len(calls) == 1:
                await asyncio.wait_for(second_waiting.wait(), 2)
                return httpx.Response(429, json={'error': {'message': DAILY}})
            return httpx.Response(200, json={'video_id': 'next-day'})
        client = http_client(tmp_path, respond)
        client.limiter = OrderedSlots()
        results = await asyncio.gather(client.create_video('a', mode='t2v'),
                                       client.create_video('b', mode='t2v'), return_exceptions=True)
        assert all(isinstance(e, DailyQuotaExhausted) for e in results)
        assert results[1].not_sent and len(calls) == 1
        client._video_quota_resume_after = '2000-01-01T00:00:00Z'
        assert (await client.create_video('b', mode='t2v'))['video_id'] == 'next-day'
    asyncio.run(run())
    assert len(calls) == 2
