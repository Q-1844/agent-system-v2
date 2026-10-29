"""Independent post-delivery review: repair/parallel race and skill-choice coverage.

These tests use real ProjectStore / actual video handler / JobQueue but a fake
Agnes provider. They must never spend external media quota.
"""
import asyncio
import json
from copy import deepcopy

import pytest

from src.core.models import Asset, Project, Scene, Shot, VideoVersion
from src.core.store import ProjectStore
from src.generation.video_commit import video_input_signature
from src.ai.repair_revision import invalidated_dependents
from src.agents.producer_orchestration import (
    assert_pinned_video_dependencies, video_submission_dependencies,
    producer_choose_repair_collaboration, validate_repair_collaboration,
    verify_stored_collaboration, OrchestrationError,
)
from tests.test_c2_repair_revision_p1 import (
    CAS, blueprint, finding, fixture_project, commit,
)
from tests.test_director_visual import (
    PROJECT as VISUAL_PROJECT, _shot, _save_proj, visual_env,
)


def _ready_source(shot):
    shot.final_video = 'accepted_v1'
    shot.video_versions = [VideoVersion(version_id='accepted_v1', status='done', video_id='accepted_vid')]
    shot.video_end_source_version = 'accepted_v1'
    shot.video_end_frame = Asset(url='https://example.com/real-tail.png', status='done')


def _relay_project(reference='prev_tail'):
    source = Shot(shot_id='s01_c01', scene_id='s01', index=1)
    _ready_source(source)
    consumer = Shot(shot_id='s02_c01', scene_id='s02', index=1,
                    link_mode='multi', video_reference_asset_id=reference)
    return Project(name='relay', scenes=[Scene(scene_id='s01', shots=[source]),
                                         Scene(scene_id='s02', shots=[consumer])])


@pytest.mark.parametrize('sentinel', ['prev_tail', 'prev_tail_look', 'prev_tail_look_scene'])
def test_input_signature_tracks_actual_cross_scene_upstream(sentinel):
    project = _relay_project(sentinel)
    shot = project.get_shot('s02_c01')
    original = video_input_signature(project, shot)
    project.get_shot('s01_c01').video_end_frame.url = 'https://example.com/changed.png'
    assert video_input_signature(project, shot) != original
    project.get_shot('s01_c01').final_video = 'other_accepted_version'
    assert video_input_signature(project, shot) != original


def test_invalidation_follows_unstated_cross_scene_tail_dependency():
    project = _relay_project()
    assert invalidated_dependents(project, {'s01_c01'}) == ['s02_c01']
    project.get_shot('s02_c01').link_mode = 'i2v'
    assert invalidated_dependents(project, {'s01_c01'}) == []


def test_inserting_new_shot_invalidates_old_next_tail_consumer():
    a = Shot(shot_id='s01_c01', scene_id='s01', index=1)
    b = Shot(shot_id='s01_c02', scene_id='s01', index=2,
             link_mode='multi', video_reference_asset_id='prev_tail')
    c = Shot(shot_id='s02_c01', scene_id='s02', index=1,
             link_mode='multi', video_reference_asset_id='prev_tail_look')
    project = Project(name='p', scenes=[Scene(scene_id='s01', shots=[a, b]),
                                        Scene(scene_id='s02', shots=[c])])
    assert invalidated_dependents(project, {'s01_c03'}, additions=[
        {'shot_id': 's01_c03', 'after_shot_id': 's01_c01'}]) == ['s01_c02', 's02_c01']
    # Inserting before the first shot of scene 2 must invalidate that relay
    # but must not discard an unrelated accepted video in scene 1.
    assert invalidated_dependents(project, {'s02_c02'}, additions=[
        {'shot_id': 's02_c02', 'after_shot_id': '__start__'}]) == ['s02_c01']


def test_repair_commit_invalidates_implicit_tail_and_keeps_immutable_old_video(tmp_path):
    fixture_project(tmp_path)
    store = ProjectStore(tmp_path)
    project = store.load()
    dependent = project.get_shot('s01_c03')
    dependent.link_mode = 'multi'
    dependent.video_reference_asset_id = 'prev_tail'
    store.save(project)
    cp = CAS()
    ev = commit(cp, tmp_path)
    assert 's01_c03' in ev['invalidated_dependencies']
    updated = ProjectStore(tmp_path).load().get_shot('s01_c03')
    assert updated.final_video is None
    assert [v.video_id for v in updated.video_versions] == ['vid_03']
    assert ProjectStore(tmp_path).load().get_shot('s01_c01').final_video == 'v_old_01'


class SkillDecisionClient:
    def __init__(self, answer):
        self.answer = answer
        self.last_messages = None
    async def chat(self, messages, **kwargs):
        self.last_messages = messages
        return json.dumps(self.answer)


def test_producer_has_actual_skill_catalog_and_may_explicitly_decline_a_skill(tmp_path):
    project = fixture_project(tmp_path)
    client = SkillDecisionClient({'tasks': [{
        'role': 'director', 'task': 'Assess filmable alternative',
        'depends_on': [], 'parallel_preference': 'allow', 'skill_id': None,
    }], 'reason': 'Individual director judgment is sufficient', 'max_parallel': 1})
    plan = asyncio.run(producer_choose_repair_collaboration(client, finding(), project))
    ctx = json.loads(client.last_messages[1]['content'])
    assert ctx['available_skills_by_role']['director']
    assert any(s['skill_id'] == 'agnes-production-craft'
               for s in ctx['available_skills_by_role']['director'])
    assert plan['tasks'][0]['skill_id'] is None
    assert plan['tasks'][0]['skill_version'] is None
    assert verify_stored_collaboration(plan, finding(), mode_id='combo_recommended') == plan


@pytest.mark.parametrize('mutation', ['change_tail', 'change_selected_version', 'missing_tail'])
def test_queued_video_tail_snapshot_revalidated_after_changes(mutation):
    project = _relay_project()
    target = {'shot_id': 's02_c01', 'frame': 'video'}
    ready, blocked, pins = video_submission_dependencies(project, [target])
    assert ready and not blocked
    captured = deepcopy(pins['s02_c01'])
    if mutation == 'change_tail':
        project.get_shot('s01_c01').video_end_frame.url = 'https://example.com/changed.png'
    elif mutation == 'change_selected_version':
        project.get_shot('s01_c01').final_video = None
    else:
        project.get_shot('s01_c01').video_end_frame.status = 'pending'
    with pytest.raises(OrchestrationError):
        assert_pinned_video_dependencies(project, 's02_c01', captured)


def test_real_video_worker_rejects_stale_queued_tail_without_provider_post(visual_env):
    rt, queue, agnes, path = visual_env
    source, target = _shot('s01_c01'), _shot('s01_c02', idx=2)
    _ready_source(source)
    target.opening_source = 'inherit_previous'
    target.source_shot_id = source.shot_id
    target.source_version = source.final_video
    _save_proj(path, [source, target])
    project = ProjectStore(path).load()
    ready, blocked, pins = video_submission_dependencies(project, [{'shot_id': target.shot_id, 'frame': 'video'}])
    assert ready and not blocked
    async def scenario():
        job = queue.submit(VISUAL_PROJECT, 'video', target.shot_id,
                           continuity_dependency_snapshot=pins[target.shot_id])
        ProjectStore(path).update_shot(source.shot_id,
            lambda s: setattr(s.video_end_frame, 'url', 'https://example.com/new-tail.png'))
        await queue.start()
        for _ in range(250):
            if job.state in ('done', 'failed'):
                return job
            await asyncio.sleep(.01)
        raise AssertionError('queued worker did not settle')
    job = asyncio.run(scenario())
    assert job.state == 'failed' and 'dependency' in (job.error or '')
    assert 'create_video' not in agnes.calls


def test_real_video_worker_does_not_attach_if_tail_changes_while_provider_runs(visual_env):
    rt, queue, agnes, path = visual_env
    source, target = _shot('s01_c01'), _shot('s01_c02', idx=2)
    _ready_source(source)
    target.opening_source = 'inherit_previous'
    target.source_shot_id = source.shot_id
    target.source_version = source.final_video
    _save_proj(path, [source, target])
    project = ProjectStore(path).load()
    _, _, pins = video_submission_dependencies(project, [{'shot_id': target.shot_id, 'frame': 'video'}])
    entered, resume = asyncio.Event(), asyncio.Event()
    original = agnes.create_video
    async def delayed(*args, **kwargs):
        entered.set()
        await resume.wait()
        return await original(*args, **kwargs)
    agnes.create_video = delayed
    async def scenario():
        job = queue.submit(VISUAL_PROJECT, 'video', target.shot_id,
                           continuity_dependency_snapshot=pins[target.shot_id])
        await queue.start()
        await asyncio.wait_for(entered.wait(), timeout=10)
        ProjectStore(path).update_shot(source.shot_id,
            lambda s: setattr(s.video_end_frame, 'url', 'https://example.com/new-tail.png'))
        resume.set()
        for _ in range(500):
            if job.state in ('done', 'failed'):
                return job
            await asyncio.sleep(.01)
        raise AssertionError('worker did not settle')
    job = asyncio.run(scenario())
    assert job.state == 'failed'
    assert agnes.calls.count('create_video') == 1
    assert ProjectStore(path).load().get_shot(target.shot_id).final_video is None


def test_forward_cross_scene_reference_fails_closed_even_when_source_is_accepted():
    # An accepted video in a *future* scene is not a valid continuity parent.
    target = Shot(shot_id='s01_c01', scene_id='s01', index=1,
                  opening_source='inherit_previous', source_shot_id='s02_c01')
    source = Shot(shot_id='s02_c01', scene_id='s02', index=1)
    _ready_source(source)
    p = Project(name='forward', scenes=[Scene(scene_id='s01', shots=[target]),
                                        Scene(scene_id='s02', shots=[source])])
    ready, blocked, _ = video_submission_dependencies(p, [{'shot_id': target.shot_id, 'frame': 'video'}])
    assert not ready
    assert blocked[0]['dependency_reason'] == 'invalid_source_order'


def test_dual_explicit_and_tail_reference_both_contribute_to_input_fingerprint():
    old = Shot(shot_id='s01_c01', scene_id='s01', index=1)
    middle = Shot(shot_id='s01_c02', scene_id='s01', index=2)
    target = Shot(shot_id='s02_c01', scene_id='s02', index=1,
                  opening_source='reference_previous', source_shot_id=old.shot_id,
                  link_mode='multi', video_reference_asset_id='prev_tail_look')
    _ready_source(old)
    _ready_source(middle)
    p = Project(name='dual', scenes=[Scene(scene_id='s01', shots=[old, middle]),
                                     Scene(scene_id='s02', shots=[target])])
    original = video_input_signature(p, target)
    middle.video_end_frame.url = 'https://example.com/changed-tail-middle.png'
    assert video_input_signature(p, target) != original
    middle.video_end_frame.url = 'https://example.com/real-tail.png'
    old.video_end_frame.url = 'https://example.com/changed-tail-explicit.png'
    assert video_input_signature(p, target) != original


def test_multiple_start_inserts_use_real_reconciliation_order():
    # Empty scenes and chained new-shot anchors have to follow the same insert
    # order as reconcile_project_revisions, not a different flat-list shortcut.
    first = Shot(shot_id='s01_c01', scene_id='s01', index=1)
    relay = Shot(shot_id='s03_c01', scene_id='s03', index=1,
                 link_mode='multi', video_reference_asset_id='prev_tail')
    p = Project(name='multi-insert', scenes=[Scene(scene_id='s01', shots=[first]),
                                              Scene(scene_id='s02', shots=[]),
                                              Scene(scene_id='s03', shots=[relay])])
    # The original relay consumes s01_c01.  Adding two shots into the empty
    # middle scene changes the actual predecessor and must invalidate relay.
    assert invalidated_dependents(p, {'s02_c01', 's02_c02'}, additions=[
        {'shot_id': 's02_c01', 'after_shot_id': '__start__'},
        {'shot_id': 's02_c02', 'after_shot_id': 's02_c01'},
    ]) == ['s03_c01']


def test_failed_expert_dependency_skips_downstream_but_keeps_independent_option(tmp_path):
    from src.ai.repair_revision import expert_propose
    from tests.test_phase3_producer_orchestration import ParallelExperts, choice, task
    plan = validate_repair_collaboration(choice(
        task('script_writer'),
        task('director', depends_on=['script_writer']),
        task('scene_designer')), finding())
    client = ParallelExperts(error_role='script_writer')
    _, outputs = asyncio.run(expert_propose(client, finding(), fixture_project(tmp_path),
                                            collaboration_plan=plan))
    assert 'director' not in [role for role, _ in client.calls]
    skipped = next(x for x in outputs if x['role'] == 'director')
    assert skipped['skipped_due_to_failed_dependencies'] == ['script_writer']
    assert outputs[-1]['role'] == 'producer'
    assert outputs[-1]['selected_specialist'] == 'scene_designer'


def test_explicit_solo_mode_never_delegates_repair_or_invents_expert_skill(tmp_path):
    from src.ai.repair_revision import expert_propose
    from tests.test_phase3_producer_orchestration import choice, task, proposal
    project = fixture_project(tmp_path)
    planner = SkillDecisionClient({'tasks': [{'role': 'director', 'task': 'delegate'}]})
    plan = asyncio.run(producer_choose_repair_collaboration(
        planner, finding(), project, mode_id='producer_lead'))
    assert planner.last_messages is None  # no delegation-planning request
    assert len(plan['tasks']) == 1
    assert plan['tasks'][0]['role'] == 'producer'
    assert plan['tasks'][0]['skill_id'] is None
    assert plan['max_parallel'] == 1
    assert verify_stored_collaboration(plan, finding(), mode_id='producer_lead') == plan
    with pytest.raises(OrchestrationError):
        validate_repair_collaboration(choice(task('director')), finding(), mode_id='producer_lead')

    class SoloClient:
        def __init__(self):
            self.prompts = []
        async def chat(self, messages, **kwargs):
            self.prompts.append(messages[0]['content'])
            if 'You are Producer. Select' in messages[0]['content']:
                return json.dumps({'approve': True, 'option_index': 0,
                                   'reason': 'Producer accepts its own local revision'})
            return json.dumps(proposal())
    client = SoloClient()
    revised, trail = asyncio.run(expert_propose(client, finding(), project,
                                               collaboration_plan=plan))
    assert revised['new_summary'] == proposal()['new_summary']
    assert len(client.prompts) == 2
    assert trail[0]['role'] == 'producer'
    assert trail[-1]['role'] == 'producer' and trail[-1]['approved']
