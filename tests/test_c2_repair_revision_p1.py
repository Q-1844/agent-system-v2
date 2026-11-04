"""P1/P2 repair integration: real ProjectStore, fake CAS/Agnes, zero media POST."""
import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ai.repair_revision import (
    RepairRevisionError, append_approved_revision, digest, effective_summaries,
    reconcile_project_revisions, validate_proposal, validate_finding,
    expert_propose, assert_quiescent,
)
from tools.c2_repair_bridge import (
    effective_media_profile, validate_repaired_project,
    build_repair_finding, auto_repair_failed_full,
)
from src.core.models import Project, Scene, Shot, VideoVersion, VideoAsset
from src.core.store import ProjectStore

FP = 'base_stage0_fingerprint_original'


class CAS:
    def __init__(self):
        self.doc = {'plan_fingerprint': FP, 'video_attempts': []}
        self.writes = []

    def save(self, **fields):
        candidate = deepcopy({**self.doc, **fields})
        self.doc = candidate
        self.writes.append(candidate)


def fixture_project(path, *, dependent=False):
    first = Shot(shot_id='s01_c01', scene_id='s01', index=1,
                 description='A looks at the bridge', prompt_en='a man sees a bridge',
                 target_duration_s=6, final_video='v_old_01', locked=False,
                 video=VideoAsset(status='done'),
                 video_versions=[VideoVersion(version_id='v_old_01', video_id='vid_01',
                                              status='done', local=str(path/'old.mp4'))])
    failed = Shot(shot_id='s01_c02', scene_id='s01', index=2,
                  description='a too complex leap', prompt_en='leap grab turn in one',
                  target_duration_s=10, final_video='v_old_02',
                  video=VideoAsset(status='done'),
                  video_versions=[VideoVersion(version_id='v_old_02', video_id='vid_02',
                                               status='done', local=str(path/'old2.mp4'))])
    third = Shot(shot_id='s01_c03', scene_id='s01', index=3,
                 description='friend is saved', prompt_en='friend saved',
                 target_duration_s=6, final_video='v_old_03',
                 video=VideoAsset(status='done'),
                 source_shot_id='s01_c02' if dependent else None,
                 video_versions=[VideoVersion(version_id='v_old_03', video_id='vid_03',
                                              status='done', local=str(path/'old3.mp4'))])
    proj = Project(name=path.name, story_text='save the friend',
                   scenes=[Scene(scene_id='s01', summary='old bridge rescue',
                                 shots=[first, failed, third])])
    ProjectStore(path).save(proj)
    return proj


def blueprint():
    return {'scenes': [{'scene_id': 's01', 'summary': 'old bridge rescue'}],
            'execution_bounds': {'video_seconds': 240}}


def finding(**kwargs):
    return {'finding_id': 'bridge_review_01', 'scene_id': 's01',
            'shot_id': 's01_c02', 'kind': 'action_contact', 'source': 'verified_quality_review',
            'media_evidence': 'first/middle/end frames with timestamps',
            'evidence': '00:04 hand never touches rope; 00:07 other person remains stranded',
            'review_id': 'review_001', 'level': 'R3', **kwargs}


def proposal():
    return {
        'scene_id': 's01', 'new_summary': 'Hero uses rope to rescue friend; same outcome',
        'reason': 'split unstable leap into observable grasp and rope handoff',
        'narrative_invariants': ['Friend must be rescued', 'same bridge and identity'],
        'replace_shots': [{'shot_id': 's01_c02', 'description': 'anchor the rope',
                           'prompt_en': 'rope fixed to a sturdy pillar',
                           'target_duration_s': 6, 'camera': 'close up', 'motion': 'rope tightens'}],
        'add_shots': [{'shot_id': 's01_c04', 'after_shot_id': 's01_c02',
                       'description': 'friend holds rope', 'prompt_en': 'friend grabs rope',
                       'target_duration_s': 6, 'camera': 'medium', 'motion': 'hold steady'}]
    }


def commit(cp, tmp_path, **kwargs):
    return append_approved_revision(cp, base_plan_fingerprint=FP, blueprint=blueprint(),
                                    finding=finding(), proposal=proposal(),
                                    approvals=[{'role': 'director', 'approved': False},
                                               {'role': 'producer', 'approved': True, 'reason': 'causality'}],
                                    project_dir=tmp_path, jobs_file=tmp_path/'absent.json', **kwargs)


def test_repair_is_append_only_preserves_all_previous_video_receipts(tmp_path):
    fixture_project(tmp_path, dependent=True)
    cp = CAS()
    cp.doc['video_attempts'] = [{'state': 'ACCEPTED', 'video_id': f'vid_0{i}'} for i in range(1, 4)]
    ev = commit(cp, tmp_path)
    assert len(cp.doc['repair_revisions']) == 1
    assert cp.doc['plan_fingerprint'] == FP
    assert ev['invalidated_dependencies'] == ['s01_c03']
    assert ev['preserved_media_versions'] == [{'shot_id': 's01_c01', 'video_version': 'v_old_01'}]
    assert len(cp.writes) == 2
    assert cp.writes[0]['repair_phase'] == 'COMMITTED_PENDING_REPLAY'
    project = ProjectStore(tmp_path).load()
    assert [s.shot_id for s in project.scenes[0].shots] == ['s01_c01', 's01_c02', 's01_c04', 's01_c03']
    assert project.get_shot('s01_c01').final_video == 'v_old_01'
    assert project.get_shot('s01_c02').final_video is None
    assert project.get_shot('s01_c03').final_video is None
    assert {v.video_id for sh in project.all_shots() for v in sh.video_versions} == {'vid_01', 'vid_02', 'vid_03'}
    assert cp.doc['repair_phase'] == 'APPLIED'
    assert ev['revision_id'].startswith('repair_')
    assert effective_summaries(blueprint(), [ev], FP)['s01'] == proposal()['new_summary']


def test_replay_after_success_must_not_discard_newly_generated_video(tmp_path):
    fixture_project(tmp_path)
    cp = CAS()
    ev = commit(cp, tmp_path)
    store = ProjectStore(tmp_path)
    project = store.load()
    fixed = project.get_shot('s01_c02')
    fixed.final_video = 'v_new_04'
    fixed.video_versions.append(VideoVersion(version_id='v_new_04', status='done', video_id='vid_04'))
    store.save(project)
    validate_repaired_project(tmp_path, blueprint(), [ev], FP)
    again = ProjectStore(tmp_path).load().get_shot('s01_c02')
    assert again.final_video == 'v_new_04'
    assert [v.video_id for v in again.video_versions] == ['vid_02', 'vid_04']


def test_crash_after_cas_before_project_write_replays_exact_edit(tmp_path, monkeypatch):
    fixture_project(tmp_path)
    cp = CAS()
    from src.ai import repair_revision as mod
    original = mod.reconcile_project_revisions
    def fail(*args, **kwargs):
        raise OSError('crash injected immediately after CAS')
    monkeypatch.setattr(mod, 'reconcile_project_revisions', fail)
    with pytest.raises(OSError, match='crash injected'):
        commit(cp, tmp_path)
    assert cp.doc['repair_phase'] == 'COMMITTED_PENDING_REPLAY'
    assert ProjectStore(tmp_path).load().scenes[0].summary == 'old bridge rescue'
    monkeypatch.setattr(mod, 'reconcile_project_revisions', original)
    result = validate_repaired_project(tmp_path, blueprint(), cp.doc['repair_revisions'], FP)
    assert result['applied_revision_ids'] == [cp.doc['repair_revisions'][0]['revision_id']]
    assert ProjectStore(tmp_path).load().scenes[0].summary == proposal()['new_summary']


def test_idempotent_replay_without_marker_in_final_state_is_safe(tmp_path):
    fixture_project(tmp_path)
    cp = CAS()
    commit(cp, tmp_path)
    (tmp_path/'output/ai/repair_effective_plan.json').unlink()
    # Source data was saved before marker; reconcile must not clear it twice.
    validate_repaired_project(tmp_path, blueprint(), cp.doc['repair_revisions'], FP)
    assert len(ProjectStore(tmp_path).load().scenes[0].shots) == 4


def test_refuse_unknown_paid_receipt_and_running_controller(tmp_path):
    fixture_project(tmp_path)
    cp = CAS()
    cp.doc['video_attempts'] = [{'state': 'UNKNOWN', 'video_id': None}]
    with pytest.raises(RepairRevisionError, match='UNKNOWN'):
        commit(cp, tmp_path)
    cp.doc['video_attempts'] = []
    p = tmp_path/'output/full_runs/frun_1/run.json'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"status":"producing"}', encoding='utf8')
    with pytest.raises(RepairRevisionError, match='active'):
        commit(cp, tmp_path)
    assert not cp.doc.get('repair_revisions')


def test_reject_no_producer_approval_cross_scene_and_duplicate_shot_id(tmp_path):
    proj = fixture_project(tmp_path)
    cp = CAS()
    with pytest.raises(RepairRevisionError, match='Producer'):
        append_approved_revision(cp, base_plan_fingerprint=FP, blueprint=blueprint(),
                                 finding=finding(), proposal=proposal(), approvals=[{'role': 'director'}],
                                 project_dir=tmp_path, jobs_file=tmp_path/'absent.json')
    p = proposal()
    p['add_shots'][0]['shot_id'] = 's02_c01'
    with pytest.raises(RepairRevisionError, match='does not belong'):
        validate_proposal(p, finding(), proj)
    p = proposal()
    p['add_shots'][0]['shot_id'] = 's01_c01'
    with pytest.raises(RepairRevisionError):
        validate_proposal(p, finding(), proj)


def test_quality_evidence_is_required_and_no_automatic_review_change(tmp_path):
    fixture_project(tmp_path)
    f = finding()
    f.pop('media_evidence')
    with pytest.raises(RepairRevisionError, match='frame/timecode'):
        validate_finding(f, scene_ids={'s01'})
    class Completed:
        status = 'completed'
        error_code = None
        run_id = 'completed1'
    assert build_repair_finding(Completed(), tmp_path) is None


def test_effective_profile_accepts_new_shot_count_and_soft_duration(tmp_path):
    fixture_project(tmp_path)
    cp = CAS()
    commit(cp, tmp_path)
    base = {'scene_count': 1, 'shot_min': 3, 'shot_max': 3,
            'planned_min': 21, 'planned_max': 22, 'min_final': 19, 'budget': 240}
    profile = effective_media_profile(base, ProjectStore(tmp_path).load(), cp.doc['repair_revisions'])
    assert profile['shot_max'] == 4
    assert profile['planned_min'] <= 24 <= profile['planned_max']
    assert profile['min_final'] == base['min_final']
    assert base['shot_max'] == 3


class ExpertClient:
    def __init__(self, *, approve=True):
        self.calls = []
        self.approve = approve

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if 'You are Producer' in messages[0]['content']:
            return json.dumps({'approve': self.approve, 'option_index': 0, 'reason': 'safe repair'})
        return json.dumps(proposal())


def test_expert_and_producer_workflow_selects_valid_revision(tmp_path):
    proj = fixture_project(tmp_path)
    client = ExpertClient()
    draft, approvals = asyncio.run(expert_propose(client, finding(), proj,
                                                   roles=['director', 'script_writer', 'character_designer']))
    assert len(client.calls) == 4
    assert all(kwargs['thinking'] for _, kwargs in client.calls)
    assert draft['new_summary'] == proposal()['new_summary']
    assert approvals[-1]['role'] == 'producer' and approvals[-1]['approved']
    with pytest.raises(RepairRevisionError, match='did not approve'):
        asyncio.run(expert_propose(ExpertClient(approve=False), finding(), proj))


def test_fullrun_failure_creates_committed_repair_and_new_controller(tmp_path):
    fixture_project(tmp_path)
    cp = CAS()
    cp.doc['video_attempts'] = [{'state': 'ACCEPTED', 'video_id': 'vid_01'}]
    failed = SimpleNamespace(run_id='frun_fail1', status='failed',
                             error_code='DIRECTOR_FAILED', current_scene_id='s01',
                             scenes=[SimpleNamespace(scene_id='s01', status='failed',
                                                     error='Director cannot make leap', index=0)])
    controller_file = tmp_path/'output/full_runs/frun_fail1/run.json'
    controller_file.parent.mkdir(parents=True, exist_ok=True)
    controller_file.write_text('{"status":"failed"}', encoding='utf8')
    class Manager:
        starts = 0
        async def start_run(self, project_name):
            self.starts += 1
            p = ProjectStore(tmp_path).load()
            assert p.get_shot('s01_c01').locked and p.get_shot('s01_c01').final_video
            assert not p.get_shot('s01_c02').locked and not p.get_shot('s01_c02').final_video
            return SimpleNamespace(run_id='frun_repair1', status='pending')
    wrapper = ExpertClient()
    wrapper.finalize_only = False
    wrapper.project_dir = tmp_path
    manager = Manager()
    result = asyncio.run(auto_repair_failed_full(manager=manager, full=failed,
                    wrapper=wrapper, checkpoint=cp, blueprint=blueprint(),
                    base_plan_fingerprint=FP))
    assert result.run_id == 'frun_repair1'
    assert manager.starts == 1
    assert cp.doc['repair_active_full_run_id'] == 'frun_repair1'
    assert cp.doc['repair_phase'] == 'RESTARTED'
    assert len(cp.doc['repair_preserved_media'][0]['shots']) == 2
    assert cp.doc['repair_revisions'][0]['finding']['source'] == 'director_failure'
    # Reusing the same finding must not launch another new run.
    assert asyncio.run(auto_repair_failed_full(manager=manager, full=failed,
                wrapper=wrapper, checkpoint=cp, blueprint=blueprint(),
                base_plan_fingerprint=FP)) is None


def test_invalid_findings_do_not_edit_project_and_no_quality_hallucination(tmp_path):
    fixture_project(tmp_path)
    class Fail:
        status = 'failed'
        error_code = 'PROJECT_PRODUCTION_FAILED'
        run_id = 'frun_other'
    assert build_repair_finding(Fail(), tmp_path) is None
    assert ProjectStore(tmp_path).load().scenes[0].summary == 'old bridge rescue'


def test_revision_lineage_and_content_hash_detect_tampering(tmp_path):
    fixture_project(tmp_path)
    cp = CAS()
    event = commit(cp, tmp_path)
    tampered = deepcopy(event)
    tampered['proposal']['reason'] = 'tampered after approval'
    with pytest.raises(RepairRevisionError, match='hash'):
        effective_summaries(blueprint(), [tampered], FP)
    with pytest.raises(RepairRevisionError, match='lineage'):
        effective_summaries(blueprint(), [event, event], FP)


def test_second_repair_has_parent_lineage_and_clears_old_controller(tmp_path):
    fixture_project(tmp_path)
    cp = CAS()
    first = commit(cp, tmp_path)
    cp.save(repair_phase='RESTARTED', repair_active_full_run_id='frun_previous_repair')
    second_finding = {
        **finding(), 'finding_id': 'bridge_review_02', 'review_id': 'new_quality_review',
        'evidence': '00:06 grasp still missing after the first patch'}
    second_proposal = {
        **proposal(), 'new_summary': 'Rescue using a winch and stable handoff',
        'reason': 'Revised rope handoff still misses contact; use a closer shot',
        'add_shots': [],
        'replace_shots': [{**proposal()['replace_shots'][0],
                           'description': 'winch grabs rope securely',
                           'prompt_en': 'mechanical winch locks the rope'}],
    }
    other = append_approved_revision(cp, base_plan_fingerprint=FP,
                blueprint=blueprint(), finding=second_finding,
                proposal=second_proposal,
                approvals=[{'role': 'producer', 'approved': True, 'reason': 'stable causality'}],
                project_dir=tmp_path, jobs_file=tmp_path/'absent.json')
    assert len(cp.doc['repair_revisions']) == 2
    assert other['parent_revision_id'] == first['revision_id']
    assert cp.doc['repair_active_full_run_id'] is None
    assert cp.doc['plan_fingerprint'] == FP
    assert len(ProjectStore(tmp_path).load().scenes[0].shots) == 4
    assert ProjectStore(tmp_path).load().get_shot('s01_c01').final_video == 'v_old_01'
    assert validate_repaired_project(tmp_path, blueprint(), cp.doc['repair_revisions'], FP)['applied_revision_ids'] == [
        first['revision_id'], other['revision_id']]


def test_empty_director_scene_can_commit_safe_new_shot(tmp_path):
    ProjectStore(tmp_path).save(Project(name=tmp_path.name,
        scenes=[Scene(scene_id='s01', summary='old bridge rescue', shots=[])],
        story_text='rescue with physical action'))
    cp = CAS()
    first = proposal()
    first['replace_shots'] = []
    first['add_shots'] = [{**first['add_shots'][0], 'after_shot_id': '__start__'}]
    finding1 = {**finding(), 'shot_id': None, 'source': 'director_failure',
                'review_id': 'frun_no_shots'}
    ev = append_approved_revision(cp, base_plan_fingerprint=FP, blueprint=blueprint(),
          finding=finding1, proposal=first,
          approvals=[{'role': 'producer', 'approved': True, 'reason': 'can generate'}],
          project_dir=tmp_path, jobs_file=tmp_path/'absent.json')
    project = ProjectStore(tmp_path).load()
    assert project.scenes[0].shots[0].shot_id == 's01_c04'
    assert project.scenes[0].shots[0].index == 1
    assert validate_repaired_project(tmp_path, blueprint(), [ev], FP)['applied_revision_ids'] == [ev['revision_id']]


def test_repair_video_post_intent_binds_shot_and_revision_before_submission(tmp_path):
    from src.core.media_submission_context import active_video_submission
    from tools.full_project_cloud_acceptance import BoundedRealClient
    fixture_project(tmp_path)
    cp = CAS()
    ev = commit(cp, tmp_path)
    class FakeVideo:
        calls = 0
        async def create_video(self, prompt, **kw):
            self.calls += 1
            attempt = cp.doc['video_attempts'][-1]
            assert attempt['state'] == 'SUBMITTING'
            assert attempt['revision_id'] == ev['revision_id']
            assert attempt['logical_shot_id'] == 's01_c02'
            assert attempt['immutable_input_digest']
            assert attempt['attempt_id'] == 'job_intent_002'
            return {'video_id': 'confirmed_revised_video_id'}
    client = FakeVideo()
    wrapper = BoundedRealClient(client, cp, tmp_path, tmp_path, finalize_only=False,
               video_budget=1_000_000_000, image_budget=10000,
               text_budget=50000, film_target_seconds='auto')
    wrapper.plan = {'shot_count': 4, 'planned_seconds': 24}
    native = {'project': tmp_path.name, 'logical_shot_id': 's01_c02',
              'attempt_id': 'job_intent_002', 'input_signature': 'frozen_native_input',
              'previous_final_video': None}
    token = active_video_submission.set(native)
    try:
        result = asyncio.run(wrapper.create_video('revised rope shot', mode='t2v', duration_s=6))
    finally:
        active_video_submission.reset(token)
    assert result['video_id'] == 'confirmed_revised_video_id'
    assert client.calls == 1
    assert cp.doc['video_attempts'][-1]['state'] == 'ACCEPTED'
    assert cp.doc['video_attempts'][-1]['revision_id'] == ev['revision_id']
    with pytest.raises(RuntimeError, match='lacks native shot intent'):
        asyncio.run(wrapper.create_video('unbound repaired shot', mode='t2v', duration_s=6))
    assert client.calls == 1


def test_no_revision_does_not_modify_unrepaired_project_sidecars(tmp_path):
    fixture_project(tmp_path)
    validate_repaired_project(tmp_path, blueprint(), [], FP)
    assert not (tmp_path/'output/ai/repair_effective_plan.json').exists()


def test_finding_shot_must_appear_in_the_revision(tmp_path):
    project = fixture_project(tmp_path)
    draft = proposal()
    draft['replace_shots'] = []
    with pytest.raises(RepairRevisionError, match='failing shot'):
        validate_proposal(draft, finding(), project)


def test_real_snapshot_restore_replays_cas_revision_without_losing_paid_videos(tmp_path):
    from tools.full_project_resume import restore_snapshot
    pname = 'cloud3min_unit'
    old_root = f'/old/checkout/projects/{pname}'
    stage = tmp_path / 'snapshot'
    original = stage / 'projects' / pname
    project = fixture_project(original)
    store = ProjectStore(original)
    for sh in project.all_shots():
        for v in sh.video_versions:
            rel = f'output/videos/{v.video_id}.mp4'
            path = original / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('ORIGINAL:' + v.video_id).encode('ascii'))
            v.local = old_root + '/' + rel
    store.save(project)
    native_path = original / 'output/full_runs/frun_original/run.json'
    native_path.parent.mkdir(parents=True, exist_ok=True)
    native_path.write_text(json.dumps({'run_id': 'frun_original', 'project_name': pname,
                                       'status': 'failed'}), encoding='utf8')
    summary_path = stage / 'e2e_run/unit/summary.json'
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps({'nonce': 'unit', 'github_run_id': '777',
            'source_sha': 'original_sha', 'project_name': pname,
            'project_dir': old_root, 'full_run_id': 'frun_original'}), encoding='utf8')
    cache = stage / 'cache'
    cache.mkdir()
    (cache / 'jobs.json').write_text('{}', encoding='utf8')
    (cache / 'quota.json').write_text('{}', encoding='utf8')

    # CAS says repair committed, but the older artifact still only has the
    # original frozen scene. The new runner replays without new Agnes posts.
    other = tmp_path / 'editable_source'
    fixture_project(other)
    checkpoint = CAS()
    commit(checkpoint, other)
    cp = {**checkpoint.doc, 'github_run_id': '777', 'source_sha': 'original_sha',
          'phase': 'FAILED', 'full_run_id': 'frun_original',
          'film_target_seconds': 'auto', 'authorized_video_seconds': 240,
          'execution_video_authorization': 1_000_000_000,
          'video_attempts': [
              {'state': 'ACCEPTED', 'video_id': f'vid_0{i}', 'seconds': 6}
              for i in range(1, 4)]}
    runner = tmp_path / 'runner'
    result = restore_snapshot(stage, runner, 'unit', cp, {'run_id': '777', 'source_sha': 'original_sha'})
    target = runner / 'projects' / pname
    assert result['reused_completed_videos'] and len(result['reused_completed_videos']) == 3
    assert ProjectStore(target).load().scenes[0].summary == blueprint()['scenes'][0]['summary']
    overlay = validate_repaired_project(target, blueprint(), cp['repair_revisions'], FP)
    assert overlay['applied_revision_ids'] == [cp['repair_revisions'][0]['revision_id']]
    repaired = ProjectStore(target).load()
    assert repaired.get_shot('s01_c01').final_video == 'v_old_01'
    assert repaired.get_shot('s01_c02').final_video is None
    assert len(repaired.all_shots()) == 4
    assert {v.video_id for sh in repaired.all_shots() for v in sh.video_versions} == {
        'vid_01', 'vid_02', 'vid_03'}
    for sh in repaired.all_shots():
        for v in sh.video_versions:
            assert Path(v.local).read_bytes() == ('ORIGINAL:' + v.video_id).encode('ascii')


def test_revised_dialogue_invalidates_only_affected_audio(tmp_path):
    from src.core.models import Character, AudioAsset
    p = fixture_project(tmp_path)
    p.characters = [Character(name='Hero', description_en='rescuer')]
    p.scenes[0].characters = ['Hero']
    target = p.get_shot('s01_c02')
    target.dialogue = 'jump now'
    target.characters = ['Hero']
    target.audio = AudioAsset(status='done')
    ProjectStore(tmp_path).save(p)
    rev = proposal()
    rev['replace_shots'][0]['dialogue'] = 'hold the rope'
    rev['replace_shots'][0]['characters'] = ['Hero']
    rev['add_shots'][0]['characters'] = ['Hero']
    cp = CAS()
    append_approved_revision(cp, base_plan_fingerprint=FP, blueprint=blueprint(),
        finding=finding(), proposal=rev,
        approvals=[{'role': 'producer', 'approved': True, 'reason': 'causality'}],
        project_dir=tmp_path, jobs_file=tmp_path/'absent.json')
    repaired = ProjectStore(tmp_path).load()
    assert repaired.get_shot('s01_c02').dialogue == 'hold the rope'
    assert repaired.get_shot('s01_c02').audio.status != 'done'
    assert repaired.get_shot('s01_c04').characters == ['Hero']
    assert repaired.get_shot('s01_c01').final_video == 'v_old_01'


def test_approved_repair_starts_real_native_full_project_controller(tmp_path, monkeypatch):
    import deps
    from src.ai.full_project_autopilot import FullProjectManager
    from tools.c2_repair_bridge import lock_preserved_shots
    # This is the actual native Manager.start_run and inspect path, not a
    # replacement mock. Only the async media loop is suppressed (zero POST).
    fixture_project(tmp_path)
    cp = CAS()
    ev = commit(cp, tmp_path)
    held = lock_preserved_shots(tmp_path, {'s01_c02', 's01_c04'})
    assert {e['shot_id'] for e in held} == {'s01_c01', 's01_c03'}
    monkeypatch.setattr(deps, 'project_manager', SimpleNamespace(
        get_project_dir=lambda name: tmp_path))
    manager = FullProjectManager()
    monkeypatch.setattr(manager, '_launch_loop', lambda run: None)
    run = asyncio.run(manager.start_run(tmp_path.name))
    assert run.status == 'pending' and run.run_id.startswith('frun_')
    assert run.expected_source_signature
    assert manager.get_run(tmp_path.name, run.run_id).run_id == run.run_id
    assert ProjectStore(tmp_path).load().get_shot('s01_c01').locked
    assert not ProjectStore(tmp_path).load().get_shot('s01_c02').locked
    assert cp.doc['repair_revisions'][0]['revision_id'] == ev['revision_id']


def test_replay_never_recovers_an_invalidated_old_tail_as_selected(tmp_path):
    fixture_project(tmp_path, dependent=True)
    cp = CAS()
    event = commit(cp, tmp_path)
    assert event['invalidated_previous_versions'] == [
        {'shot_id': 's01_c03', 'final_video': 'v_old_03'}]
    store = ProjectStore(tmp_path)
    project = store.load()
    project.get_shot('s01_c03').final_video = 'v_old_03'  # corrupt selection
    store.save(project)
    (tmp_path/'output/ai/repair_effective_plan.json').unlink()
    with pytest.raises(RepairRevisionError, match='partial project revision'):
        validate_repaired_project(tmp_path, blueprint(), cp.doc['repair_revisions'], FP)


def test_committed_overlay_marker_does_not_hide_corrupt_reselected_media(tmp_path):
    fixture_project(tmp_path, dependent=True)
    cp = CAS()
    commit(cp, tmp_path)
    project = ProjectStore(tmp_path).load()
    project.get_shot('s01_c03').final_video = 'v_old_03'
    ProjectStore(tmp_path).save(project)
    # This time the marker itself is present and claims every event applied.
    assert (tmp_path / 'output/ai/repair_effective_plan.json').is_file()
    with pytest.raises(RepairRevisionError, match='diverged from committed plan'):
        validate_repaired_project(tmp_path, blueprint(), cp.doc['repair_revisions'], FP)


def test_repaired_scene_budget_widens_only_derived_soft_windows(tmp_path):
    from src.ai.full_project_autopilot import read_scene_budgets
    fixture_project(tmp_path)
    original = {'schema_version': 1, 'plan_fingerprint': FP,
                'scene_budgets': [{'scene_id': 's01', 'shot_min': 3, 'shot_max': 3,
                                   'min_s': 20, 'max_s': 23,
                                   'per_shot_min_s': 4, 'per_shot_max_s': 11}]}
    file = tmp_path / 'output/ai/scene_budgets.json'
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(original, ensure_ascii=False), encoding='utf8')
    before = file.read_bytes()
    cp = CAS()
    commit(cp, tmp_path)
    assert file.read_bytes() == before  # original frozen budget untouched
    effective = read_scene_budgets(tmp_path)['s01']
    assert effective['shot_min'] == 3 and effective['shot_max'] == 4
    assert effective['min_s'] == 20 and effective['max_s'] == 24
    effective_overlay = json.loads((tmp_path/'output/ai/repair_effective_plan.json').read_text())
    assert effective_overlay['scene_budget_overrides']['s01']['revision_shot_count'] == 4
    assert ProjectStore(tmp_path).load().scenes[0].summary == proposal()['new_summary']


def test_verified_visual_review_can_revise_a_technically_completed_fullrun(tmp_path):
    fixture_project(tmp_path)
    cp = CAS()
    controller = SimpleNamespace(run_id='frun_technically_complete', status='completed',
                                 error_code=None, current_scene_id=None, scenes=[])
    source = tmp_path/'output/ai/repair_findings.json'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps({**finding(),
        'trigger_full_run_id': controller.run_id}), encoding='utf8')
    run_path = tmp_path/'output/full_runs/frun_technically_complete/run.json'
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text('{"status":"completed"}', encoding='utf8')

    class Controller:
        async def start_run(self, name):
            return SimpleNamespace(run_id='frun_new_from_review', status='pending')

    client = ExpertClient()
    client.finalize_only = False
    client.project_dir = tmp_path
    new = asyncio.run(auto_repair_failed_full(manager=Controller(), full=controller,
        wrapper=client, checkpoint=cp, blueprint=blueprint(),
        base_plan_fingerprint=FP))
    assert new.run_id == 'frun_new_from_review'
    assert cp.doc['repair_revisions'][0]['finding']['source'] == 'verified_quality_review'
    assert ProjectStore(tmp_path).load().get_shot('s01_c02').final_video is None
