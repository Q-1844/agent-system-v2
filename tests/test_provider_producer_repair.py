"""Provider reviews enter the existing Producer revision loop; zero paid calls."""
import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ai.repair_revision import RepairRevisionError, validate_finding
from src.core.job_queue import JobQueue
from src.core.models import VideoAsset
from src.core.provider_errors import content_policy_rejection
from src.core.store import ProjectStore
from tests.test_c2_repair_revision_p1 import CAS, FP, blueprint, fixture_project
from tools.c2_repair_bridge import auto_repair_failed_full, build_repair_finding, validate_repaired_project
from tools.full_project_cloud_acceptance import attempt_producer_repair

REJECTION = 'Agnes API 错误 400: {"error":{"code":"content_policy_violation","message":"modify the prompt"}}'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


def failed_chain(root, queue, *, number=1, kind='first_frame', submit='rejected', code=REJECTION):
    full_id, prod_id, child_id = f'full{number}', f'prod{number}', f'scene{number}'
    key = f'autopilot:{child_id}:s01_c02:{kind}:3'
    job = queue.submit(root.name, kind, 's01_c02', origin_run_id=child_id,
                       autopilot_stage_key=key, dispatch_image_prompt=f'rejected scene {number}')
    job.state, job.error = 'failed', code
    job.error_class = 'src.api.agnes_client.AgnesError'
    if kind == 'video':
        job.params['provider_submit_state'] = submit
        if submit == 'accepted':
            job.params['provider_task_id'] = 'paid-but-provider-failed'
            job.error_class = 'src.api.agnes_client.VideoGenerationFailed'
    queue._save()
    write(root / f'output/ai/autopilot_runs/{child_id}.json', {
        'run_id': child_id, 'parent_project_run_id': prod_id, 'scene_id': 's01', 'status': 'failed',
        'shots': [{'shot_id': 's01_c02', 'status': 'failed', 'stage': kind,
                   'active_job_id': job.job_id, 'active_stage_key': key}]})
    write(root / f'output/production_runs/{prod_id}/run.json', {
        'run_id': prod_id, 'parent_full_run_id': full_id, 'status': 'failed',
        'scenes': [{'scene_id': 's01', 'status': 'failed', 'child_run_id': child_id}]})
    write(root / f'output/full_runs/{full_id}/run.json', {'run_id': full_id, 'status': 'failed'})
    return SimpleNamespace(run_id=full_id, status='failed', production_run_id=prod_id,
        error_code='PROJECT_PRODUCTION_FAILED', error='first frame failed', current_scene_id='s01'), job


class Producer:
    def __init__(self, root, *, approve=True):
        self.project_dir = root
        self.finalize_only = False
        self.quota_gate_after = None
        self.video_attempts = []
        self.calls = []
        self.approve = approve
        self.round = 0

    async def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        instruction = messages[0]['content']
        if 'deciding which specialists' in instruction:
            return json.dumps({'tasks': [{'role': 'script_writer', 'task': 'Repair the provider review finding',
                                          'skill_id': None}], 'max_parallel': 1,
                               'reason': 'local dialogue and staging need revision'})
        if 'You are Producer. Select' in instruction:
            return json.dumps({'approve': self.approve, 'option_index': 0,
                               'reason': 'preserve the story with another staging approach'})
        self.round += 1
        return json.dumps({'new_summary': f'the same story, revised staging {self.round}',
            'reason': 'address actual provider feedback', 'narrative_invariants': ['save the friend'],
            'replace_shots': [{'shot_id': 's01_c02', 'description': f'revised scene {self.round}',
                'prompt_en': f'another composition {self.round}', 'target_duration_s': 10,
                'motion': 'show the handoff', 'camera': 'wide'}], 'add_shots': []})


def prepare(tmp_path):
    root = tmp_path / 'movie'
    fixture_project(root)
    store = ProjectStore(root)
    project = store.load()
    shot = project.get_shot('s01_c02')
    shot.final_video = None
    shot.video = VideoAsset()
    store.save(project)
    for name in ('old.mp4', 'old2.mp4', 'old3.mp4'):
        (root / name).write_bytes(('preserved-' + name).encode())
    queue = JobQueue(tmp_path / 'jobs.json', {})
    return root, queue, CAS(), Producer(root)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,submit', [('first_frame', 'rejected'), ('last_frame', 'rejected'),
                                       ('video', 'rejected'), ('video', 'accepted')])
async def test_image_and_video_rejection_reaches_producer_and_preserves_receipts(tmp_path, kind, submit):
    root, queue, cp, producer = prepare(tmp_path)
    full, job = failed_chain(root, queue, kind=kind, submit=submit)
    before = asdict(ProjectStore(root).load().get_shot('s01_c01'))
    class Manager:
        async def start_run(self, name):
            return SimpleNamespace(run_id='repaired', status='pending')
    try:
        result = await auto_repair_failed_full(manager=Manager(), full=full, wrapper=producer,
            checkpoint=cp, blueprint=blueprint(), base_plan_fingerprint=FP, jobs_file=queue.state_path)
        assert result.run_id == 'repaired'
        event = cp.doc['repair_revisions'][0]
        assert event['finding']['source'] == 'provider_rejection'
        assert event['finding']['provider_feedback']['job_id'] == job.job_id
        assert 'content_policy_violation' in json.dumps(producer.calls)
        assert 'rejected scene 1' in json.dumps(producer.calls)
        after = asdict(ProjectStore(root).load().get_shot('s01_c01'))
        before['locked'] = True
        assert after == before
        assert (root / 'old.mp4').read_bytes() == b'preserved-old.mp4'
        assert job.params.get('provider_task_id') == ('paid-but-provider-failed' if submit == 'accepted' else None)
        assert cp.doc['repair_pending_finding'] is None
        assert event['approvals'][-1]['role'] == 'producer'
    finally:
        queue.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['unknown', 'live_video', 'old_child', 'other_shot', 'quota', 'network'])
async def test_noncreative_or_unconfirmed_errors_cannot_authorize_a_revision(tmp_path, damage):
    root, queue, cp, producer = prepare(tmp_path)
    full, job = failed_chain(root, queue, kind='video', submit='accepted' if damage == 'live_video' else 'rejected')
    if damage == 'unknown':
        job.params['provider_submit_state'] = 'unknown'
    elif damage == 'live_video':
        job.error_class = 'src.api.agnes_client.PollTransientError'
    elif damage == 'old_child':
        job.params['origin_run_id'] = 'unrelated'
    elif damage == 'other_shot':
        job.shot_id = 's01_c01'
    elif damage == 'quota':
        job.error = 'Agnes API 错误 429: daily quota, content_policy_violation'
    elif damage == 'network':
        job.error = 'Agnes API 错误 503: content_filter unavailable'
    queue._save()
    try:
        assert build_repair_finding(full, root, jobs_file=queue.state_path) is None
        assert not cp.doc.get('repair_revisions') and not producer.calls
    finally:
        queue.close()


@pytest.mark.asyncio
async def test_three_successive_provider_rejections_create_three_producer_revisions(tmp_path):
    root, queue, cp, producer = prepare(tmp_path)
    full, _ = failed_chain(root, queue)
    cp.doc['nonce'] = 'create-mode-movie'  # no C2/source-project identity
    class Manager:
        count = 0
        async def start_run(self, name):
            self.count += 1
            if self.count == 3:
                return SimpleNamespace(run_id='finished', status='completed')
            next_run, _ = failed_chain(root, queue, number=self.count + 1)
            return next_run
    manager = Manager()
    try:
        for _ in range(3):
            full = await attempt_producer_repair(manager=manager, full=full, wrapper=producer,
                checkpoint=cp, blueprint=blueprint(), base_plan_fingerprint=FP,
                evidence_dir=tmp_path, queue=queue, summary={}, nonce='create-mode-movie', driver_minutes=30)
        assert full.status == 'completed' and manager.count == 3
        events = cp.doc['repair_revisions']
        assert len(events) == 3
        assert events[1]['parent_revision_id'] == events[0]['revision_id']
        assert events[2]['parent_revision_id'] == events[1]['revision_id']
        assert len({e['finding']['provider_feedback']['job_id'] for e in events}) == 3
        assert 'previous_repair_decisions' in json.dumps(producer.calls)
        assert ProjectStore(root).load().get_shot('s01_c02').prompt_en == 'another composition 3'
        assert cp.doc['plan_fingerprint'] == FP
        assert not cp.doc.get('repair_pending_finding')
        assert hashlib.sha256((root / 'old.mp4').read_bytes()).hexdigest() == hashlib.sha256(b'preserved-old.mp4').hexdigest()
    finally:
        queue.close()


@pytest.mark.asyncio
async def test_missing_producer_approval_is_saved_as_automatic_repair_wait(tmp_path, monkeypatch):
    from tools import full_project_cloud_acceptance as acc
    from tools.resume_dispatch import auto_resume_allowed
    from tools.quota_gate import parse_gate
    root, queue, cp, producer = prepare(tmp_path)
    producer.approve = False
    full, _ = failed_chain(root, queue)
    producer.checkpoint = cp
    async def persist(**fields):
        cp.save(**fields)
    producer.persist = persist
    async def next_hop(*args):
        return {'dispatched': False, 'reason': 'cooldown; wakeup will arm'}
    monkeypatch.setattr(acc, 'auto_resume_next_hop', next_hop)
    class Manager:
        def get_run(self, *args):
            return full
        async def start_run(self, *args):
            raise AssertionError('unapproved media may not run')
    summary = {}
    try:
        with pytest.raises(RuntimeError, match='automatic continuation'):
            await attempt_producer_repair(manager=Manager(), full=full, wrapper=producer,
                checkpoint=cp, blueprint=blueprint(), base_plan_fingerprint=FP,
                evidence_dir=tmp_path, queue=queue, summary=summary, nonce='movie', driver_minutes=30)
        assert cp.doc['phase'] == 'INTERRUPTED'
        assert cp.doc['terminal_status'] == 'technical_wait'
        assert cp.doc['wait_reason'] == 'provider_repair' and cp.doc['drain_complete']
        assert cp.doc['repair_pending_finding']['provider_feedback']['job_id']
        assert cp.doc['repair_decision_history'] and not cp.doc.get('repair_revisions')
        assert auto_resume_allowed(cp.doc, now=parse_gate(cp.doc['resume_not_before']))[0]
    finally:
        queue.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change_duration', [False, True])
async def test_noop_repair_returns_to_producer_instead_of_regenerating_the_same_input(tmp_path, change_duration):
    from src.ai.repair_revision import RepairDecisionPending, expert_propose
    root, queue, cp, producer = prepare(tmp_path)
    full, _ = failed_chain(root, queue)
    finding = build_repair_finding(full, root, jobs_file=queue.state_path)
    project = ProjectStore(root).load()
    old = project.get_shot('s01_c02')
    class Noop:
        async def chat(self, *args, **kw):
            return json.dumps({'new_summary': 'same scene', 'reason': 'try again',
                'narrative_invariants': ['save the friend'], 'replace_shots': [{
                    'shot_id': old.shot_id, 'description': old.description, 'prompt_en': old.prompt_en,
                    'target_duration_s': old.target_duration_s - (1 if change_duration else 0),
                    'motion': old.motion, 'camera': old.camera}],
                'add_shots': []})
    try:
        with pytest.raises(RepairDecisionPending, match='must change'):
            await expert_propose(Noop(), finding, project)
    finally:
        queue.close()


@pytest.mark.asyncio
async def test_queue_keeps_provider_body_when_short_error_has_no_review_code(tmp_path):
    from src.api.agnes_client import AgnesError
    root, queue, cp, producer = prepare(tmp_path)
    full, job = failed_chain(root, queue)
    async def reject(current, emit):
        raise AgnesError('provider rejected the prompt', 400,
                         json.dumps({'padding': 'x' * 1200, 'code': 'content_policy_violation'}))
    queue.handlers['first_frame'] = reject
    try:
        job.state, job.error = 'running', None
        await queue._run_job(job, 'offline-provider')
        assert 'content_policy_violation' not in job.error
        finding = build_repair_finding(full, root, jobs_file=queue.state_path)
        assert finding and 'content_policy_violation' in finding['provider_feedback']['error']
        queue.retry(job.job_id)
        assert 'provider_content_feedback' not in job.params
    finally:
        queue.close()


@pytest.mark.asyncio
async def test_pending_decision_restores_on_another_runner_and_revises_before_media(tmp_path, monkeypatch):
    import shutil
    from tools import full_project_cloud_acceptance as acc
    root, queue, cp, producer = prepare(tmp_path)
    producer.approve = False
    full, _ = failed_chain(root, queue)
    producer.checkpoint = cp
    async def persist(**fields):
        cp.save(**fields)
    producer.persist = persist
    async def next_hop(*args):
        return {'dispatched': False, 'reason': 'cooldown'}
    monkeypatch.setattr(acc, 'auto_resume_next_hop', next_hop)
    class Manager:
        def get_run(self, *args):
            return full
        async def start_run(self, name):
            assert cp.doc['repair_revisions']
            return SimpleNamespace(run_id='new-controller', status='pending')
    try:
        with pytest.raises(RuntimeError, match='automatic continuation'):
            await attempt_producer_repair(manager=Manager(), full=full, wrapper=producer,
                checkpoint=cp, blueprint=blueprint(), base_plan_fingerprint=FP,
                evidence_dir=tmp_path, queue=queue, summary={}, nonce='movie', driver_minutes=30)
    finally:
        queue.close()
    moved = tmp_path / 'another-runner/movie'
    shutil.copytree(root, moved)
    path = moved.parent / 'jobs.json'
    shutil.copyfile(tmp_path / 'jobs.json', path)
    restored = JobQueue(path, {})
    producer2 = Producer(moved)
    try:
        result = await attempt_producer_repair(manager=Manager(), full=full, wrapper=producer2,
            checkpoint=cp, blueprint=blueprint(), base_plan_fingerprint=FP,
            evidence_dir=moved.parent, queue=restored, summary={}, nonce='movie', driver_minutes=30)
        assert result.run_id == 'new-controller'
        assert cp.doc['repair_pending_finding'] is None
        assert 'previous_repair_decisions' in json.dumps(producer2.calls)
        assert len(cp.doc['repair_collaboration_plans']) == 1  # frozen team was reused
        assert ProjectStore(moved).load().get_shot('s01_c02').prompt_en == 'another composition 1'
    finally:
        restored.close()


@pytest.mark.asyncio
async def test_other_unknown_paid_work_still_blocks_creative_edits(tmp_path):
    root, queue, cp, producer = prepare(tmp_path)
    full, _ = failed_chain(root, queue)
    cp.doc['video_attempts'] = [{'state': 'UNKNOWN', 'video_id': None}]
    try:
        with pytest.raises(RepairRevisionError, match='UNKNOWN'):
            await auto_repair_failed_full(manager=None, full=full, wrapper=producer,
                checkpoint=cp, blueprint=blueprint(), base_plan_fingerprint=FP, jobs_file=queue.state_path)
        assert not producer.calls and not cp.doc.get('repair_revisions')
    finally:
        queue.close()


@pytest.mark.parametrize('status', [408, 429, 500, 503])
def test_http_faults_do_not_become_content_reviews(status):
    from src.api.agnes_client import AgnesError, VideoSubmissionUncertain
    assert not content_policy_rejection(AgnesError('content_policy_violation', status))
    assert not content_policy_rejection(VideoSubmissionUncertain('content_policy_violation'))


def test_forged_provider_finding_without_actual_job_feedback_is_rejected():
    with pytest.raises(RepairRevisionError, match='exact rejected job'):
        validate_finding({'finding_id': 'fake', 'scene_id': 's01', 'shot_id': 's01_c01',
            'kind': 'content', 'evidence': 'some guess', 'review_id': 'guess',
            'level': 'R3', 'source': 'provider_rejection'}, scene_ids={'s01'})


@pytest.mark.asyncio
async def test_malformed_approval_remains_a_pending_creative_decision(tmp_path):
    from src.ai.repair_revision import RepairDecisionPending
    root, queue, cp, _ = prepare(tmp_path)
    full, _ = failed_chain(root, queue)
    class MissingReason(Producer):
        async def chat(self, messages, **kw):
            if 'You are Producer. Select' in messages[0]['content']:
                return json.dumps({'approve': True, 'option_index': 0, 'reason': ''})
            return await super().chat(messages, **kw)
    try:
        with pytest.raises(RepairDecisionPending, match='incomplete'):
            await auto_repair_failed_full(manager=None, full=full, wrapper=MissingReason(root),
                checkpoint=cp, blueprint=blueprint(), base_plan_fingerprint=FP, jobs_file=queue.state_path)
        assert cp.doc['repair_pending_finding'] and not cp.doc.get('repair_revisions')
    finally:
        queue.close()


@pytest.mark.asyncio
async def test_drain_deadline_keeps_review_pending_without_editing_or_abandoning(tmp_path, monkeypatch):
    from tools import full_project_cloud_acceptance as acc
    root, queue, cp, producer = prepare(tmp_path)
    full, _ = failed_chain(root, queue)
    async def persist(**fields):
        cp.save(**fields)
    producer.persist = persist
    async def drain(*a, **kw):
        return False
    async def hop(*a):
        return {'dispatched': False, 'reason': 'cooldown'}
    monkeypatch.setattr(acc, 'snapshot_settled', lambda *a: False)
    monkeypatch.setattr(acc, 'pause_and_drain', drain)
    monkeypatch.setattr(acc, 'auto_resume_next_hop', hop)
    try:
        with pytest.raises(RuntimeError, match='automatic continuation'):
            await attempt_producer_repair(manager=None, full=full, wrapper=producer,
                checkpoint=cp, blueprint=blueprint(), base_plan_fingerprint=FP,
                evidence_dir=tmp_path, queue=queue, summary={}, nonce='movie', driver_minutes=30)
        assert cp.doc['phase'] == 'INTERRUPTED' and cp.doc['wait_reason'] == 'provider_repair'
        assert cp.doc['drain_complete'] is False and cp.doc['repair_pending_finding']
        assert not producer.calls and not cp.doc.get('repair_revisions')
    finally:
        queue.close()


@pytest.mark.parametrize('stage', ['first', 'last', 'video'])
def test_native_fullproject_rejects_then_producer_revises_and_finishes(client, monkeypatch, stage):
    """Real FullProject/Scene/JobQueue/handlers/MP4 composition, fake provider."""
    import time
    import shutil
    import deps
    from src.ai import autopilot as ap
    from src.ai.full_project_autopilot import get_full_project_manager
    from src.core.models import Project, Scene, Shot, VideoVersion
    from tests.test_full_project_autopilot import FakeAgnes, _fake_mp4_bytes
    root = deps.get_project_manager().create_project('_test_provider_full_loop')
    video = root / 'output/videos/preserved.mp4'
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(_fake_mp4_bytes())
    original_hash = hashlib.sha256(video.read_bytes()).hexdigest()
    from src.ai.repair_revision import PROMPT_OVERRIDE_FIELDS
    target = Shot(shot_id='s01_c02', scene_id='s01', index=2, description='handoff',
                  target_duration_s=5)
    setattr(target, PROMPT_OVERRIDE_FIELDS[stage], 'blocked marker')
    ProjectStore(root).save(Project(name=root.name, story_text='save the friend', scenes=[
        Scene(scene_id='s01', summary='old bridge rescue', shots=[
            Shot(shot_id='s01_c01', scene_id='s01', index=1, description='arrival',
                 target_duration_s=5, locked=True, final_video='keep',
                 video=VideoAsset(status='done'),
                 video_versions=[VideoVersion(version_id='keep', video_id='prior-paid',
                    status='done', local=str(video), seconds_returned=1)]),
            target])]))
    class Fake(FakeAgnes):
        def __init__(self):
            super().__init__()
            self.rejections = self.revisions = self.video_posts = 0
            self.finalize_only = False
            self.project_dir = root
            self.video_attempts = []
            self.quota_gate_after = None

        async def chat(self, messages, **kw):
            instruction = messages[0]['content']
            if 'deciding which specialists' in instruction:
                return json.dumps({'tasks': [{'role': 'director', 'task': 'Revise the rejected scene',
                    'skill_id': None}], 'max_parallel': 1, 'reason': 'provider feedback'})
            if 'You are Producer. Select' in instruction:
                return json.dumps({'approve': True, 'option_index': 0, 'reason': 'same story, better staging'})
            if 'specialist' in instruction and 'LOCAL story/shot repair' in instruction:
                assert 'content_policy_violation' in messages[-1]['content']
                self.revisions += 1
                return json.dumps({'new_summary': 'same bridge rescue, new staging',
                    'reason': 'use provider review feedback', 'narrative_invariants': ['save the friend'],
                    'replace_shots': [{'shot_id': 's01_c02', 'description': 'clear handoff',
                        'prompt_en': 'clear handoff', 'target_duration_s': 5,
                        'prompt_overrides': {stage: 'revised bridge handoff'}}], 'add_shots': []})
            return await super().chat(messages, **kw)

        def _planner_json(self, user_msg):
            result = json.loads(super()._planner_json(user_msg))
            if stage == 'last':
                for shot in result['shots']:
                    shot['link_mode'] = 'keyframes'
            return json.dumps(result)

        async def chat_completion(self, messages, **kw):
            return {'content': 'blocked marker'}  # cheap third-batch rewrite still gets rejected

        async def generate_image(self, prompt, **kw):
            if 'blocked marker' in prompt:
                from src.api.agnes_client import AgnesError
                self.rejections += 1
                raise AgnesError(REJECTION, 400)
            return await super().generate_image(prompt, **kw)

        async def create_video(self, *args, **kw):
            if args and 'blocked marker' in args[0]:
                from src.api.agnes_client import AgnesError
                self.rejections += 1
                raise AgnesError(REJECTION, 400)
            self.video_posts += 1
            return await super().create_video(*args, **kw)
    fake = Fake()
    monkeypatch.setattr(deps, 'agnes_client', fake)
    original_sleep = asyncio.sleep
    async def fast_sleep(seconds, *args):
        return await original_sleep(min(seconds, 0.03), *args)
    monkeypatch.setattr(ap.asyncio, 'sleep', fast_sleep)
    manager = get_full_project_manager()
    cp = CAS()
    async def wait_for_stop(run_id):
        until = time.monotonic() + 30
        while time.monotonic() < until:
            run = manager.get_run(root.name, run_id)
            if run.status in ('failed', 'completed', 'blocked', 'cancelled'):
                return run
            await original_sleep(0.03)
        raise AssertionError('native controller did not settle')
    try:
        initial = client.portal.call(manager.start_run, root.name)
        failed = client.portal.call(wait_for_stop, initial.run_id)
        assert failed.status == 'failed' and fake.rejections == (2 if stage == 'video' else 3)
        replacement = client.portal.call(lambda: attempt_producer_repair(
            manager=manager, full=failed, wrapper=fake, checkpoint=cp,
            blueprint=blueprint(), base_plan_fingerprint=FP, evidence_dir=root,
            queue=deps.job_queue, summary={}, nonce='native-test', driver_minutes=30))
        final = client.portal.call(wait_for_stop, replacement.run_id)
        assert final.status == 'completed', final.error
        assert fake.revisions == 1 and fake.video_posts == 1
        assert Path(final.final_output).is_file()
        assert hashlib.sha256(video.read_bytes()).hexdigest() == original_hash
        project = ProjectStore(root).load()
        assert project.get_shot('s01_c01').final_video == 'keep'
        assert getattr(project.get_shot('s01_c02'), PROMPT_OVERRIDE_FIELDS[stage]) == 'revised bridge handoff'
        # The native prompt stage generated new prompt_en after the approved
        # revision. A later hop must recognize that exact recorded derivation.
        validate_repaired_project(root, blueprint(), cp.doc['repair_revisions'], FP)
        assert hashlib.sha256(video.read_bytes()).hexdigest() == original_hash
        current = ProjectStore(root).load()
        current.get_shot('s01_c02').prompt_en = 'unrecorded tampering'
        ProjectStore(root).save(current)
        with pytest.raises(RepairRevisionError, match='diverged'):
            validate_repaired_project(root, blueprint(), cp.doc['repair_revisions'], FP)
    finally:
        shutil.rmtree(root, ignore_errors=True)
