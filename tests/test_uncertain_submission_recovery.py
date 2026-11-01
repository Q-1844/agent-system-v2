"""Stopped-snapshot recovery requires explicit, auditable acceptance of uncertainty."""
import asyncio
import copy
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.job_queue import Job, JobQueue
from src.core.models import Project, Scene, Shot, VideoAsset, VideoVersion, ImageAsset, Asset
from src.core.store import ProjectStore
from src.core.submission_recovery import AUTHORIZED, validate_attempts
from src.generation.video_commit import video_input_signature
from tools.full_project_cloud_acceptance import BoundedRealClient, submission_recovery_options
from tools.full_project_resume import restore_snapshot
from tools.submission_reconciliation import recovered_checkpoint, reconcile_snapshot_submissions
from tests.test_day_continuation import Journal


@pytest.fixture
def stopped(tmp_path):
    from tests.relstore.test_media_download_resume import snapshot
    stage, cp, provenance = snapshot(tmp_path)
    pname = 'cloud3min_unit'
    project_dir = stage/'projects'/pname
    old = '/old/checkout/projects/'+pname
    (project_dir/'output/first.png').write_bytes(b'original frame')
    done = Shot(shot_id='s01_c01', scene_id='s01', index=1, final_video='v1',
                video=VideoAsset(status='done', video_id='paid1', local=old+'/output/videos/one.mp4'),
                video_versions=[VideoVersion(version_id='v1', video_id='paid1', status='done',
                    local=old+'/output/videos/one.mp4', seconds_returned=10)])
    pending = Shot(shot_id='s01_c02', scene_id='s01', index=2, target_duration_s=8,
                   video=VideoAsset(status='generating', num_frames=193, frame_rate=24),
                   first_frame=ImageAsset(status='done', candidates=[Asset(status='done',
                       local=old+'/output/first.png', url='https://frame.invalid/first.png')]))
    store = ProjectStore(project_dir)
    store.save(Project(name=pname, scenes=[Scene(scene_id='s01', shots=[done, pending])]))
    proj = store.load()
    job = Job('j', pname, 'video', 's01_c02', state='running', attempts=2,
              params={'submission_intent_id': 'uncertain-attempt', 'provider_submit_state': 'submitting',
                      'handler_started': True, 'origin_run_id': 'scene',
                      'autopilot_stage_key': 'autopilot:scene:s01_c02:video:1',
                      'dispatch_input_signature': video_input_signature(proj, proj.get_shot('s01_c02')),
                      'dispatch_final_video': None, 'tail_host_required': False})
    cp.update(nonce='unit', request_sha256='d'*64, project_name=pname, phase='STAGE0_BOUND',
              terminal_status='paused', drain_complete=False)
    cp['video_attempts'].append(dict(state='SUBMITTING', seconds=8, intent_id='uncertain-intent',
        attempt_id='uncertain-attempt', logical_shot_id='s01_c02', immutable_input_digest='e'*64,
        prompt_sha256='f'*64, ordinal=2, started_at='2026-11-01T17:18:17Z'))
    cp['submission_intents'] = copy.deepcopy(cp['video_attempts'])
    jobs = {'j': asdict(job)}
    (stage/'cache/jobs.json').write_text(json.dumps(jobs))
    for rel, rid in [('production_runs/project/run.json', 'project'),
                     ('ai/autopilot_runs/scene.json', 'scene')]:
        p = project_dir/'output'/rel;p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(dict(run_id=rid, status='running', pause_requested=True)))
    return stage, cp, provenance, jobs, store


def restore(stopped, tmp_path, *, confirm=False, **kwargs):
    stage, cp, provenance, *_ = stopped
    return restore_snapshot(stage, tmp_path/'runner', 'unit', cp, provenance,
        confirm_uncertain_submissions=confirm, authorization_actor='operator', authorization_run_id='200', **kwargs)


@pytest.mark.parametrize('state', ['SUBMITTING', 'UNKNOWN'])
def test_missing_task_id_needs_explicit_permission(stopped, tmp_path, state):
    stage, cp, _, _, _ = stopped
    cp['video_attempts'][-1]['state'] = state
    before = copy.deepcopy(cp)
    with pytest.raises(ValueError, match='explicit confirm'):
        restore(stopped, tmp_path)
    assert cp == before and not (tmp_path/'runner/projects').exists()


def test_permission_closes_original_risk_without_deleting_or_billing_it_as_free(stopped, tmp_path):
    stage, cp, _, _, _ = stopped
    original = copy.deepcopy(cp)
    result = restore(stopped, tmp_path, confirm=True)
    effective = recovered_checkpoint(cp, result)
    assert cp == original
    assert result['authorized_resubmit_jobs'] == ['j']
    assert result['previous_video_seconds'] == 10 and result['possible_unconfirmed_seconds'] == 8
    assert effective['video_attempts'][0] == cp['video_attempts'][0]
    uncertain = effective['video_attempts'][-1]
    assert uncertain['state'] == AUTHORIZED and uncertain['provider_outcome'] == 'UNKNOWN'
    assert uncertain['possible_charge_seconds'] == 8 and not uncertain.get('video_id')
    assert sum(a['seconds'] for a in effective['video_attempts']) == 18
    assert len(validate_attempts(effective['video_attempts'], nonce='unit', request_sha256='d'*64)) == 1
    job = json.loads((tmp_path/'runner/cache/jobs.json').read_text())['j']
    assert job['state'] == 'failed' and job['params']['confirm_uncertain_resubmit'] is True
    for p in (tmp_path/'runner/projects/cloud3min_unit/output').glob('**/run.json'):
        doc = json.loads(p.read_text());assert doc['status'] in ('paused', 'failed') and not doc.get('pause_requested')


@pytest.mark.parametrize('confirm', [False, True])
def test_known_provider_receipt_always_wins_over_permission(stopped, tmp_path, confirm):
    stage, cp, _, jobs, _ = stopped
    jobs['j']['params'].update(provider_task_id='paid2', provider_submit_state='accepted')
    (stage/'cache/jobs.json').write_text(json.dumps(jobs))
    result = restore(stopped, tmp_path, confirm=confirm)
    assert result['recovered_poll_jobs'] == ['j'] and result['authorized_resubmit_jobs'] == []
    effective = recovered_checkpoint(cp, result)
    assert [a['state'] for a in effective['video_attempts']] == ['ACCEPTED', 'ACCEPTED']
    assert effective['video_attempts'][-1]['video_id'] == 'paid2'


def test_proven_not_sent_does_not_need_duplicate_charge_permission(stopped, tmp_path):
    stage, cp, _, jobs, _ = stopped
    jobs['j']['params']['provider_submit_state'] = 'not_sent'
    (stage/'cache/jobs.json').write_text(json.dumps(jobs))
    result = restore(stopped, tmp_path)
    effective = recovered_checkpoint(cp, result)
    assert len(effective['video_attempts']) == 1
    assert effective['submission_intents'][-1]['state'] == 'NOT_SENT'
    assert result['possible_unconfirmed_seconds'] == 0


@pytest.mark.parametrize('change', ['cancelled', 'other_shot', 'inputs', 'selection', 'duplicate_id'])
def test_permission_does_not_bypass_identity_selection_cancellation_or_duplicates(stopped, tmp_path, change):
    stage, cp, _, jobs, store = stopped
    if change == 'cancelled':jobs['j']['state'] = 'cancelled'
    if change == 'other_shot':jobs['j']['shot_id'] = 's01_c01'
    if change == 'inputs':store.update_shot('s01_c02', lambda s:setattr(s, 'description', 'changed'))
    if change == 'selection':store.update_shot('s01_c02', lambda s:setattr(s, 'final_video', 'different'))
    if change == 'duplicate_id':jobs['j']['params']['provider_task_id'] = 'paid1'
    (stage/'cache/jobs.json').write_text(json.dumps(jobs))
    with pytest.raises(ValueError):restore(stopped, tmp_path, confirm=True)
    assert not (tmp_path/'runner/projects').exists()


def test_policy_is_scoped_to_one_task_and_survives_future_hops(stopped):
    _, cp, provenance, jobs, store = stopped
    first, _, _ = reconcile_snapshot_submissions(cp, store.load().to_dict(), jobs, 'unit', provenance,
        confirm=True, actor='operator', authorization_run_id='200')
    pending = dict(first['video_attempts'][-1], state='UNKNOWN', attempt_id='second-attempt', intent_id='second-intent')
    pending.pop('resubmit_authorization')
    first['video_attempts'].append(pending)
    first['submission_intents'].append(copy.deepcopy(pending))
    jobs['j']['params'].update(submission_intent_id='second-attempt', provider_submit_state='unknown')
    second, _, ids = reconcile_snapshot_submissions(first, store.load().to_dict(), jobs, 'unit', provenance)
    assert ids == ['j']
    assert second['submission_recovery_policy'] == first['submission_recovery_policy']
    assert sum(a['state'] == AUTHORIZED for a in second['video_attempts']) == 2
    with pytest.raises(ValueError, match='different/invalid task'):
        reconcile_snapshot_submissions(first, store.load().to_dict(), jobs, 'other', provenance)


def test_tampered_closed_intent_cannot_hide_unknown(stopped, tmp_path):
    result = restore(stopped, tmp_path, confirm=True)
    effective = recovered_checkpoint(stopped[1], result)
    effective['video_attempts'][-1]['resubmit_authorization']['risk_accepted'] = False
    with pytest.raises(ValueError):
        BoundedRealClient(object(), Journal(), tmp_path, tmp_path/'evidence').restore_usage(effective)


def test_finalize_only_cannot_authorize_new_generation(stopped, tmp_path):
    with pytest.raises(ValueError, match='finalize-only'):
        restore(stopped, tmp_path, confirm=True, finalize_only=True)


@pytest.mark.parametrize('value', ['1', 'yes', 'falsee'])
def test_dispatch_confirmation_is_strict_boolean(value):
    with pytest.raises(ValueError):submission_recovery_options({'CONFIRM_UNCERTAIN_SUBMISSIONS':value})


def test_dispatch_confirmation_requires_an_existing_resume_source():
    with pytest.raises(ValueError):submission_recovery_options({'CONFIRM_UNCERTAIN_SUBMISSIONS':'true'})
    assert submission_recovery_options({}) == {}


def test_real_native_queue_and_handler_replace_one_unknown_and_keep_completed_bytes(stopped, tmp_path, monkeypatch):
    import deps
    import routers.shots as routes
    from src.ai.autopilot import SceneAutopilotManager, SceneAutopilotRun, ShotExecutionState
    from tests.test_p55_recovery import FakeAgnes, FakeRehost, _FakePM
    result = restore(stopped, tmp_path, confirm=True)
    effective = recovered_checkpoint(stopped[1], result)
    pdir = tmp_path/'runner/projects/cloud3min_unit'
    store = ProjectStore(pdir)
    first = store.load().get_shot('s01_c01')
    before = hashlib.sha256(Path(first.video_versions[0].local).read_bytes()).hexdigest()
    provider = FakeAgnes()
    wrapper = BoundedRealClient(provider, Journal(), pdir, tmp_path/'evidence');wrapper.plan = {}
    wrapper.restore_usage(effective)
    monkeypatch.setattr(deps, 'agnes_client', wrapper)
    monkeypatch.setattr(deps, 'project_manager', _FakePM(pdir))
    monkeypatch.setattr(deps, '_rehost_host', FakeRehost())
    monkeypatch.setattr(routes, '_extract_video_end_frame', AsyncMock(return_value=(None, True)))
    queue = JobQueue(tmp_path/'runner/cache/jobs.json', {'video':routes._handler_video}, require_ownership=False)
    monkeypatch.setattr(deps, 'job_queue', queue)
    manager = SceneAutopilotManager()
    monkeypatch.setattr(manager, '_save_shot_state', AsyncMock())
    state = ShotExecutionState(shot_id='s01_c02', index=2, attempts={'video':5})
    run = SceneAutopilotRun(run_id='scene', project_name='cloud3min_unit', scene_id='s01',
                            plan_id='plan', plan_snapshot={}, run_seed=1, shots=[state])
    async def execute():
        await queue.start()
        await asyncio.wait_for(manager._do_video('cloud3min_unit', run, state, store, pdir), 5)
    asyncio.run(execute())
    assert provider.calls.count('create_video') == 1 and queue.jobs['j'].state == 'done'
    assert queue.jobs['j'].params['submission_history'][-1]['provider_submit_state'] == 'resubmit_authorized'
    assert 'confirm_uncertain_resubmit' not in queue.jobs['j'].params
    assert wrapper.video_attempts[1]['state'] == AUTHORIZED
    assert wrapper.video_attempts[-1]['resubmission_of'] == 'uncertain-attempt'
    assert sum(a['state'] == 'ACCEPTED' for a in wrapper.video_attempts) == 2
    current = store.load()
    assert current.get_shot('s01_c02').video_versions[-1].previous_submit_uncertain is True
    assert current.get_shot('s01_c01').final_video == 'v1'
    assert hashlib.sha256(Path(first.video_versions[0].local).read_bytes()).hexdigest() == before


def test_paused_parent_drains_inflight_post_before_a_snapshot_can_settle(tmp_path, monkeypatch):
    from tools import full_project_cloud_acceptance as driver
    from tests.relstore.test_acceptance_deadline_drain import settled_project
    pdir, completed, queue = settled_project(tmp_path)
    job = SimpleNamespace(project='p', state='running')
    queue.jobs['inflight'] = job
    waiting, receipt = asyncio.Event(), asyncio.Event()
    class Provider:
        async def create_video(self, *args, **kwargs):
            waiting.set()
            await receipt.wait()
            return {'video_id':'inflight-paid'}
    wrapper = BoundedRealClient(Provider(), Journal(), pdir, tmp_path/'evidence');wrapper.plan={}
    wrapper.video_attempts = copy.deepcopy(completed)
    pauses = AsyncMock(side_effect=AssertionError('already-paused parent must not be re-paused'))
    manager = SimpleNamespace(request_pause=pauses, get_run=lambda *a:SimpleNamespace(status='paused'))
    original_sleep = asyncio.sleep
    clock = [0]
    async def tick(seconds):
        clock[0] += seconds
        receipt.set()
        await original_sleep(.01)
    async def land():
        await wrapper.create_video('p', duration_s=8)
        path = pdir/'output/inflight.mp4';path.write_bytes(b'landed')
        ProjectStore(pdir).update_shot('s1', lambda s:s.video_versions.append(
            VideoVersion(version_id='v2', video_id='inflight-paid', status='done', local=str(path))))
        job.state='done'
    async def run():
        inflight = asyncio.create_task(land())
        await waiting.wait()
        assert wrapper.video_attempts[-1]['state']=='SUBMITTING'
        assert not driver.snapshot_settled(pdir, wrapper.video_attempts, queue)
        result=await driver.pause_and_drain(manager,'p','full',wrapper,queue,grace_seconds=9,reason='paused')
        await inflight
        return result
    monkeypatch.setattr(driver.asyncio,'sleep',tick)
    assert asyncio.run(run())
    pauses.assert_not_awaited()
    assert wrapper.checkpoint.doc['drain_complete'] and wrapper.checkpoint.doc['terminal_status']=='paused'
    assert not wrapper.checkpoint.doc['deadline_reached']
    assert len([a for a in wrapper.video_attempts if a['state']=='ACCEPTED'])==2


def test_failed_restore_cannot_claim_or_pollute_source_identity(monkeypatch):
    from tools import full_project_cloud_acceptance as driver
    from tools.resume_dispatch import auto_resume_allowed
    cp=Journal();cp.doc=dict(phase='WAITING_QUOTA',terminal_status='quota_exhausted',
                            quota_resume_after='2000-01-01T00:00:00Z',github_run_id='10',source_sha='old')
    before=copy.deepcopy(cp.doc)
    monkeypatch.setenv('RESUME_RUN_ID','10');monkeypatch.setenv('GITHUB_RUN_ID','20');monkeypatch.setenv('GITHUB_SHA','new')
    driver._stage0_rebind_for_hop(cp)
    assert cp.doc==before and not cp.writes and auto_resume_allowed(cp.doc)[0]


def test_patrol_can_recover_a_legacy_polluted_checkpoint_but_not_new_stage0():
    from tools.resume_dispatch import auto_resume_allowed
    doc=dict(phase='STAGE0_BOUND',terminal_status='paused',full_run_id='full',
             video_attempts=[dict(state='ACCEPTED',video_id='paid',seconds=8)])
    assert auto_resume_allowed(doc)[0]
    assert not auto_resume_allowed({'phase':'STAGE0_BOUND'})[0]
    assert not auto_resume_allowed(dict(doc,terminal_status='failed'))[0]


def test_policy_missing_from_financial_authorization_is_not_accepted(stopped,tmp_path):
    result=restore(stopped,tmp_path,confirm=True)
    effective=recovered_checkpoint(stopped[1],result)
    effective.pop('submission_recovery_policy')
    with pytest.raises(ValueError,match='policy'):
        BoundedRealClient(object(),Journal(),tmp_path,tmp_path/'evidence').restore_usage(effective)


def test_stopped_fullrun_still_pauses_live_production_children(monkeypatch):
    import src.ai.full_project_autopilot as full
    parent = SimpleNamespace(status='paused', stage=full.FSTAGE_PRODUCTION,
                             production_run_id='project', pause_requested=False)
    child = SimpleNamespace(status='running')
    child_manager = SimpleNamespace(get_run=lambda *a: child, request_pause=AsyncMock())
    manager = full.FullProjectManager()
    monkeypatch.setattr(manager, '_load_run', lambda *a: parent)
    monkeypatch.setattr(manager, '_save_run', AsyncMock(side_effect=AssertionError('parent must stay stopped')))
    monkeypatch.setattr(full, 'get_project_autopilot_manager', lambda: child_manager)
    asyncio.run(manager.pause_children_for_drain('p', 'full'))
    child_manager.request_pause.assert_awaited_once_with('p', 'project')
    assert parent.status == 'paused' and not parent.pause_requested
    child.status = 'paused'
    asyncio.run(manager.pause_children_for_drain('p', 'full'))
    assert child_manager.request_pause.await_count == 1


@pytest.mark.parametrize('outcome', ['not_sent', 'rejected', 'unknown'])
def test_replacement_permission_is_reusable_only_after_proven_non_acceptance(stopped, tmp_path, outcome):
    from src.api.agnes_client import VideoSubmissionNotSent, AgnesError
    from src.core.media_submission_context import active_video_submission
    result = restore(stopped, tmp_path, confirm=True)
    effective = recovered_checkpoint(stopped[1], result)
    auth = effective['video_attempts'][-1]['resubmit_authorization']
    class Provider:
        calls = 0
        async def create_video(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                if outcome == 'not_sent':
                    raise VideoSubmissionNotSent('preflight stopped before HTTP')
                if outcome == 'rejected':
                    raise AgnesError('invalid request', 400)
                raise TimeoutError('POST response lost')
            return {'video_id': 'new-paid'}
    provider = Provider()
    pdir = tmp_path/'runner/projects/cloud3min_unit'
    wrapper = BoundedRealClient(provider, Journal(), pdir, tmp_path/'evidence');wrapper.plan = {}
    wrapper.restore_usage(effective)
    async def attempt():
        token = active_video_submission.set(dict(project=pdir.name, logical_shot_id='s01_c02',
            attempt_id='new-attempt', input_signature='native-signature', previous_final_video=None,
            resubmit_authorization=auth))
        try:
            with pytest.raises((VideoSubmissionNotSent, AgnesError, TimeoutError)):
                await wrapper.create_video('replacement', duration_s=8)
            if outcome == 'unknown':
                with pytest.raises(VideoSubmissionNotSent):
                    await wrapper.create_video('replacement', duration_s=8)
            else:
                await wrapper.create_video('replacement', duration_s=8)
                with pytest.raises(VideoSubmissionNotSent, match='consumed'):
                    await wrapper.create_video('replacement', duration_s=8)
        finally:
            active_video_submission.reset(token)
    asyncio.run(attempt())
    assert provider.calls == (1 if outcome == 'unknown' else 2)
    assert wrapper.video_attempts[1]['state'] == AUTHORIZED
    assert wrapper.video_attempts[1]['possible_charge_seconds'] == 8
    assert wrapper.video_attempts[-1]['state'] == ('UNKNOWN' if outcome == 'unknown' else 'ACCEPTED')


@pytest.mark.parametrize('invalid', ['true', 'false', 1, None])
def test_restore_requires_boolean_confirmation(stopped, tmp_path, invalid):
    with pytest.raises(ValueError, match='boolean'):
        restore(stopped, tmp_path, confirm=invalid)


def test_positive_receipt_in_durable_history_wins_over_missing_native_response(stopped, tmp_path):
    stopped[1]['submission_intents'][-1].update(state='ACCEPTED', video_id='paid2')
    result = restore(stopped, tmp_path)
    assert result['recovered_poll_jobs'] == ['j'] and not result['authorized_resubmit_jobs']


def test_artifact_contradicts_not_sent_proof_and_must_not_be_dropped(stopped, tmp_path):
    stage, _, _, jobs, _ = stopped
    jobs['j']['params'].update(provider_submit_state='not_sent', unattached_artifact={'local':'video.mp4'})
    (stage/'cache/jobs.json').write_text(json.dumps(jobs))
    with pytest.raises(ValueError, match='artifact'):
        restore(stopped, tmp_path, confirm=True)


def test_monitor_distinguishes_authorized_possible_cost_from_confirmed_receipts(stopped, tmp_path):
    from tests.relstore.test_full_project_watch import sample, NOW
    from tools.watch_full_project import observed_state
    result = restore(stopped, tmp_path, confirm=True)
    effective = recovered_checkpoint(stopped[1], result)
    config, run, cp = sample()
    config.update(nonce='unit', expected_request_sha256='d'*64, preserved_video_ids=['paid1'])
    cp.update(nonce='unit', request_sha256='d'*64, video_attempts=effective['video_attempts'],
              submission_recovery_policy=effective['submission_recovery_policy'])
    observation = observed_state(config, run, cp, NOW)
    assert observation['state'] == 'ACTIVE' and observation['accepted_tasks'] == 1
    assert observation['submitted_seconds'] == 18 and observation['possible_unconfirmed_seconds'] == 8
    cp.pop('submission_recovery_policy')
    with pytest.raises(ValueError, match='policy'):
        observed_state(config, run, cp, NOW)
