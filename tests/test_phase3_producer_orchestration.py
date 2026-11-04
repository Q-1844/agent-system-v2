"""Phase 3 deterministic edge-case and integration tests. Never call real Agnes."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents import collab_modes as cm
from src.agents.producer_orchestration import (
    DEFAULT_MODE, OrchestrationError, fallback_repair_collaboration,
    producer_choose_repair_collaboration, validate_repair_collaboration,
    verify_stored_collaboration, video_submission_dependencies,
)
from src.ai.repair_revision import expert_propose, RepairRevisionError
from src.core.models import Asset, Project, Scene, Shot, VideoVersion
from src.core.store import ProjectStore
from tests.test_c2_repair_revision_p1 import (
    CAS, ExpertClient, blueprint, finding, fixture_project, proposal,
)
from tests.test_director_visual import visual_env

from tools.c2_repair_bridge import auto_repair_failed_full


#: Real Agnes evidence (2026-10-28, thinking=True): a 1500-token planning
#: budget produced a truncated JSON answer; 3000 was the first budget that
#: returned a complete, parseable plan.  The floor is asserted here rather
#: than imported, so this regression test also runs against the old source.
PRODUCER_PLANNING_MIN_SAFE_TOKENS = 3000


def task(role, **kw):
    return {"role": role, "task": f"Fix {role} evidence", "depends_on": [],
            "parallel_preference": "allow", **kw}


def choice(*tasks, **kw):
    return {"tasks": list(tasks), "reason": "Evidence requires targeted consultation", "max_parallel": 2, **kw}


def test_all_five_modes_unchanged():
    assert set(cm.COLLAB_MODES) == {"six_member", "producer_lead", "preflight_producer",
                                    "producer_escalate", "combo_recommended"}
    assert cm.DEFAULT_EXPERIMENTAL_MODE_ID == DEFAULT_MODE


def test_real_bound_skills_and_digest_are_recorded():
    plan = validate_repair_collaboration(choice(task('director'), task('script_writer')),
                                         finding())
    assert plan['schema'] == 'producer_collaboration_v1'
    assert plan['mode_id'] == 'combo_recommended'
    assert plan['tasks'][0]['skill_id'] == 'agnes-production-craft'
    assert plan['tasks'][1]['skill_id'] == 'full-story-relay-calibration'
    assert all(t['skill_version'] for t in plan['tasks'])
    assert len(plan['plan_digest']) == 64
    assert verify_stored_collaboration(plan, finding(), mode_id=DEFAULT_MODE) == plan


@pytest.mark.parametrize('bad', [
    {}, {'tasks': []}, choice(task('producer')),
    choice(task('director'), task('director')),
    choice(task('director', depends_on=['unknown'])),
    choice(task('director', depends_on=['director'])),
    choice(task('director', parallel_preference='unlimited')),
    choice(task('director', task='')),
    choice(task('director', skill_id='made-up-skill')),
    choice(task('director', skill_id='producer-project-planning')),
    choice(task('script_writer', skill_id='agnes-production-craft')),
    choice(task('director'), max_parallel=500),
    choice(task('director'), max_parallel=True),
    choice(task('director'), reason=''),
])
def test_reject_invalid_model_collaboration_without_new_privileges(bad):
    with pytest.raises(OrchestrationError):
        validate_repair_collaboration(bad, finding())


def test_reject_cyclic_dependencies_and_unrecognized_mode():
    with pytest.raises(OrchestrationError, match='cycle'):
        validate_repair_collaboration(choice(task('director', depends_on=['script_writer']),
                                             task('script_writer', depends_on=['director'])), finding())
    with pytest.raises(OrchestrationError, match='unknown collaboration mode'):
        validate_repair_collaboration(choice(task('director')), finding(), mode_id='made-up')


def test_saved_plan_rejects_tampered_skill_and_changed_finding():
    plan = validate_repair_collaboration(choice(task('director')), finding())
    altered = deepcopy(plan)
    altered['tasks'][0]['skill_version'] = '999.0.0'
    with pytest.raises(OrchestrationError, match='changed'):
        verify_stored_collaboration(altered, finding(), mode_id=DEFAULT_MODE)
    other = dict(finding()); other['evidence'] = 'different video evidence'
    with pytest.raises(OrchestrationError, match='match'):
        verify_stored_collaboration(plan, other, mode_id=DEFAULT_MODE)
    with pytest.raises(OrchestrationError):
        verify_stored_collaboration(plan, finding(), mode_id='producer_lead')


def test_default_r3_one_expert_r4_two_experts():
    r3 = fallback_repair_collaboration(finding())
    f4 = dict(finding()); f4['level'] = 'R4'
    r4 = fallback_repair_collaboration(f4)
    assert [t['role'] for t in r3['tasks']] == ['director']
    assert [t['role'] for t in r4['tasks']] == ['script_writer', 'director']
    assert r3['source'] == 'deterministic_fallback'


class PlannerClient:
    def __init__(self, payload):
        self.payload = payload
        self.kwargs = None

    async def chat(self, messages, **kwargs):
        self.kwargs = kwargs
        return self.payload


def test_producer_selects_only_needed_expert(tmp_path):
    proj = fixture_project(tmp_path)
    client = PlannerClient(json.dumps(choice(task('scene_designer'))))
    p = asyncio.run(producer_choose_repair_collaboration(client, finding(), proj))
    assert [t['role'] for t in p['tasks']] == ['scene_designer']
    assert p['source'] == 'producer' and client.kwargs['thinking'] is True


@pytest.mark.parametrize('bad', ['not-json', '{}', '{"tasks":[]}', '{"tasks":[{"role":"evil"}]}'])
def test_bad_producer_answer_fallback_is_audited(tmp_path, bad):
    proj = fixture_project(tmp_path)
    p = asyncio.run(producer_choose_repair_collaboration(PlannerClient(bad), finding(), proj))
    assert p['source'] == 'deterministic_fallback'
    assert 'fallback' in p['reason']


class TimeoutPlanner:
    async def chat(self, *_args, **_kwargs):
        raise RuntimeError('provider temporarily unavailable')


def test_planning_technical_error_does_not_turn_into_content_failure(tmp_path):
    p = asyncio.run(producer_choose_repair_collaboration(TimeoutPlanner(), finding(), fixture_project(tmp_path)))
    assert p['source'] == 'deterministic_fallback'
    assert p['tasks'][0]['role'] == 'director'


class ParallelExperts:
    def __init__(self, *, error_role=None):
        self.active = self.max_active = 0
        self.calls = []
        self.error_role = error_role

    async def chat(self, messages, **_kw):
        text = messages[0]['content']
        if 'You are Producer. Select' in text:
            return json.dumps({'approve': True, 'option_index': 0, 'reason': 'preserves causality'})
        role = next((r for r in ('director', 'script_writer', 'character_designer', 'scene_designer')
                     if f'You are the {r} specialist' in text), None)
        assert role is not None
        self.calls.append((role, messages))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(.015)
        self.active -= 1
        if role == self.error_role:
            raise ConnectionError('single expert failure')
        return json.dumps(proposal())


def test_independent_experts_really_overlap_and_producer_approves(tmp_path):
    proj = fixture_project(tmp_path)
    plan = validate_repair_collaboration(choice(task('director'), task('script_writer')),
                                         finding())
    client = ParallelExperts()
    selected, evidence = asyncio.run(expert_propose(client, finding(), proj, collaboration_plan=plan))
    assert client.max_active == 2
    assert selected['new_summary'] == proposal()['new_summary']
    assert evidence[-1]['role'] == 'producer'
    assert all('skill_id' in e for e in evidence[:-1])
    assert 'Validated role-bound method' in client.calls[0][1][0]['content']


def test_dependent_expert_never_starts_before_predecessor(tmp_path):
    plan = validate_repair_collaboration(choice(task('script_writer'),
                                             task('director', depends_on=['script_writer'])), finding())
    client = ParallelExperts()
    asyncio.run(expert_propose(client, finding(), fixture_project(tmp_path),
                               collaboration_plan=plan))
    assert client.max_active == 1
    assert [r for r, _ in client.calls] == ['script_writer', 'director']
    assert 'prior_expert_opinions' in json.loads(client.calls[-1][1][1]['content'])


def test_required_serial_preference_honored_even_when_independent(tmp_path):
    plan = validate_repair_collaboration(choice(task('director', parallel_preference='required_serial'),
                                             task('script_writer')), finding())
    client = ParallelExperts()
    asyncio.run(expert_propose(client, finding(), fixture_project(tmp_path),
                               collaboration_plan=plan))
    assert client.max_active == 1


def test_one_specialist_fails_other_succeeds_no_fabricated_agreement(tmp_path):
    plan = validate_repair_collaboration(choice(task('director'), task('script_writer')),
                                         finding())
    client = ParallelExperts(error_role='director')
    _, outputs = asyncio.run(expert_propose(client, finding(), fixture_project(tmp_path),
                                            collaboration_plan=plan))
    assert outputs[0]['role'] == 'director' and 'ConnectionError' in outputs[0]['error']
    assert outputs[-1]['selected_specialist'] == 'script_writer'


def test_all_specialists_fail_must_not_invent_approval(tmp_path):
    plan = validate_repair_collaboration(choice(task('director')), finding())
    with pytest.raises(RepairRevisionError, match='no specialist'):
        asyncio.run(expert_propose(ParallelExperts(error_role='director'), finding(),
                                   fixture_project(tmp_path), collaboration_plan=plan))


def _video_project(*, origin='inherit_previous', upstream_final='v1', tail_version='v1',
                   downstream_version='v1'):
    source = Shot(shot_id='s01_c01', scene_id='s01', index=1,
                  final_video=upstream_final,
                  video_end_source_version=tail_version,
                  video_end_frame=Asset(local='tail.png', url='https://example.com/tail.png', status='done'),
                  video_versions=[VideoVersion(version_id='v1', status='done', video_id='v_a'),
                                  VideoVersion(version_id='v2', status='done', video_id='v_b')])
    second = Shot(shot_id='s01_c02', scene_id='s01', index=2,
                  opening_source=origin, source_shot_id='s01_c01', source_version=downstream_version)
    separate = Shot(shot_id='s02_c01', scene_id='s02', index=1)
    return Project(name='p', scenes=[Scene(scene_id='s01', shots=[source, second]),
                                     Scene(scene_id='s02', shots=[separate])])


def _req(*ids):
    return [{'shot_id': i, 'frame': 'video'} for i in ids]


@pytest.mark.parametrize('opening', ['inherit_previous', 'reference_previous'])
def test_real_selected_version_tail_can_release_dependent_video(opening):
    ready, blocked, deps = video_submission_dependencies(_video_project(origin=opening),
                                                           _req('s01_c02'))
    assert [x['shot_id'] for x in ready] == ['s01_c02']
    assert not blocked
    assert deps['s01_c02']['source_version'] == 'v1'


@pytest.mark.parametrize('issue,override', [
    ('upstream_video_not_accepted', {'upstream_final': None}),
    ('upstream_tail_version_stale', {'upstream_final': 'v2', 'tail_version': 'v1', 'downstream_version': 'v2'}),
    ('downstream_source_version_stale', {'downstream_version': 'v2'}),
])
def test_actual_video_dependency_violations_block_provider_post(issue, override):
    _, blocked, _ = video_submission_dependencies(_video_project(**override), _req('s01_c02'))
    assert blocked[0]['dependency_reason'] == issue
    assert blocked[0]['status'] == 'continuity_dependency_unready'


def test_tail_missing_after_video_accepted_is_not_ready():
    project = _video_project()
    project.get_shot('s01_c01').video_end_frame = Asset(status='pending')
    _, blocked, _ = video_submission_dependencies(project, _req('s01_c02'))
    assert blocked[0]['dependency_reason'] == 'upstream_real_tail_unavailable'


def test_parallel_batch_must_not_reuse_old_parent_version():
    ready, blocked, _ = video_submission_dependencies(_video_project(),
                            _req('s01_c01', 's01_c02', 's02_c01'))
    assert [r['shot_id'] for r in ready] == ['s01_c01', 's02_c01']
    assert blocked[0]['dependency_reason'] == 'upstream_in_same_unsettled_batch'


def test_same_person_different_independent_scenes_can_parallel():
    p = _video_project()
    p.get_shot('s01_c01').characters = ['same actor']
    p.get_shot('s02_c01').characters = ['same actor']
    r, b, d = video_submission_dependencies(p, _req('s01_c01', 's02_c01'))
    assert len(r) == 2 and not b and not d


def test_invalid_and_cyclic_source_id_do_not_submit():
    p = _video_project()
    p.get_shot('s01_c02').source_shot_id = 's01_c02'
    _, blocked, _ = video_submission_dependencies(p, _req('s01_c02'))
    assert blocked[0]['dependency_reason'] == 'missing_or_self_source'
    p.get_shot('s01_c02').source_shot_id = 's02_c01'
    _, blocked, _ = video_submission_dependencies(p, _req('s01_c02'))
    assert blocked[0]['dependency_reason'] == 'invalid_source_order'


def test_invalid_parallel_preference_fails_closed():
    with pytest.raises(OrchestrationError):
        video_submission_dependencies(_video_project(), _req('s01_c01'), preference='unbounded')


def test_collab_start_override_cannot_escape_mode_or_duplicate_role():
    assert cm.validate_start('combo_recommended', {'story_goal': 'story', 'preflight_roles': []}) == []
    assert cm.validate_start('combo_recommended', {'story_goal': 'story',
                          'preflight_roles': ['script_writer']})
    assert cm.validate_start('producer_lead', {'story_goal': 'story',
                          'preflight_roles': ['director']})
    assert cm.validate_start('combo_recommended', {'story_goal': 'story',
                          'preflight_roles': ['scene_designer','scene_designer']})
    assert cm.validate_start('combo_recommended', {'story_goal': 'story',
                          'specialist_parallel_preference': 'max'})


def test_explicit_preflight_skip_and_mode_snapshot(tmp_path):
    from tests.test_p55_collab_modes import FakeRuntime, PROJECT
    rt = FakeRuntime(tmp_path)
    async def scenario():
        run = await cm.start_collab_run(rt, PROJECT, 'combo_recommended',
                           {'story_goal': 'short scene', 'preflight_roles': []})
        for _ in range(250):
            cur = cm.load_run(tmp_path, run['run_id'])
            if cur['status'] in ('completed','failed','completed_with_issues'):
                return cur
            await asyncio.sleep(.01)
        raise AssertionError('timed out')
    run = asyncio.run(scenario())
    assert run['snapshot']['params']['preflight_roles'] == []
    assert run['collaboration_decision_policy']['specialist_preflight_roles'] == []
    assert [role for role,_ in rt.turns] == ['producer']
    assert 'Producer 自由协作' in rt.turns[-1][1]


def test_media_job_queue_two_independent_jobs_overlap(tmp_path, monkeypatch):
    from src.core.job_queue import JobQueue
    monkeypatch.delenv('AGNES_VIDEO_INFLIGHT', raising=False)
    state = {'current': 0, 'max': 0}
    async def handler(job, emit):
        state['current'] += 1
        state['max'] = max(state['max'], state['current'])
        await asyncio.sleep(.035)
        state['current'] -= 1
    async def scenario():
        q = JobQueue(tmp_path / 'jobs.json', {'video': handler}, video_worker_count=2)
        await q.start()
        assert q.video_worker_count == 2
        q.submit('p', 'video', 's01_c01')
        q.submit('p', 'video', 's02_c01')
        for _ in range(100):
            if all(j.state == 'done' for j in q.jobs.values()):
                break
            await asyncio.sleep(.01)
        assert all(j.state == 'done' for j in q.jobs.values())
        for worker in q._tasks:
            worker.cancel()
        await asyncio.gather(*q._tasks, return_exceptions=True)
    asyncio.run(scenario())
    assert state['max'] == 2


def test_resume_dispatch_preserves_video_worker_snapshot(monkeypatch):
    from tools.resume_dispatch import dispatch_next_hop
    import tools.resume_dispatch as mod
    captured = {}
    class Response:
        status = 204
        def __enter__(self): return self
        def __exit__(self,*_): return False
    def fake(req, timeout=None):
        captured.update(json.loads(req.data.decode())['inputs'])
        return Response()
    monkeypatch.setattr(mod.urllib.request, 'urlopen', fake)
    assert dispatch_next_hop('fake', 'main', '123', 'nonce', media_inflight=2)['dispatched']
    assert captured['media_inflight'] == '2'
    assert dispatch_next_hop('fake','main','123','nonce', media_inflight=9)['dispatched'] is False


def test_workflow_inflight_is_parameterized_not_forced(monkeypatch):
    import yaml
    wf = yaml.safe_load((Path(__file__).resolve().parents[1] /
                        '.github/workflows/video-autoproduction-e2e.yml').read_text('utf-8'))
    dispatch = wf.get('on', wf.get(True, {}))['workflow_dispatch']
    assert dispatch['inputs']['media_inflight']['default'] == '2'
    script_env = wf['jobs']['e2e']['steps'][-1].get('env')
    # Actual workflow may have steps after the production step; search by key.
    envs = [step['env'] for step in wf['jobs']['e2e']['steps'] if 'env' in step]
    assert any(str(e.get('AGNES_VIDEO_INFLIGHT')) == '${{ inputs.media_inflight }}' for e in envs)


def test_c2_collaboration_plan_written_before_revision_and_preserves_accepted(tmp_path):
    fixture_project(tmp_path)
    cp = CAS(); cp.doc['video_attempts'] = [{'state': 'ACCEPTED', 'video_id': 'v_old'}]
    cp.doc['producer_collab_mode_id'] = DEFAULT_MODE
    full = SimpleNamespace(run_id='frun_fail', status='failed', error_code='DIRECTOR_FAILED',
                           current_scene_id='s01', scenes=[SimpleNamespace(
                               scene_id='s01', status='failed', error='Unfilmable leap')])
    class Controller:
        async def start_run(self, project_name):
            assert cp.doc['repair_collaboration_plans']
            assert cp.doc['repair_revisions']
            return SimpleNamespace(run_id='frun_restarted')
    client = ExpertClient(); client.finalize_only = False; client.project_dir = tmp_path
    result = asyncio.run(auto_repair_failed_full(manager=Controller(), full=full,
        wrapper=client, checkpoint=cp, blueprint=blueprint(),
        base_plan_fingerprint=cp.doc['plan_fingerprint']))
    assert result.run_id == 'frun_restarted'
    assert cp.doc['video_attempts'] == [{'state': 'ACCEPTED', 'video_id': 'v_old'}]
    assert cp.doc['repair_revisions'][0]['approvals'][0]['role'] == 'collaboration_plan'
    assert 'repair_collaboration_plans' in cp.writes[0]
    assert cp.doc['plan_fingerprint'] == 'base_stage0_fingerprint_original'


def test_crash_after_team_plan_reuses_same_decision_after_resume(tmp_path, monkeypatch):
    fixture_project(tmp_path)
    cp = CAS()
    full = SimpleNamespace(run_id='frun_fail', status='failed', error_code='DIRECTOR_FAILED',
                           current_scene_id='s01', scenes=[SimpleNamespace(
                               scene_id='s01', status='failed', error='Unfilmable leap')])
    class Controller:
        async def start_run(self, project_name):
            return SimpleNamespace(run_id='frun_resume')
    client = ExpertClient(); client.finalize_only = False; client.project_dir = tmp_path
    import tools.c2_repair_bridge as bridge
    original = bridge.expert_propose
    async def crash(*a, **k):
        raise OSError('injected power loss after team decision')
    monkeypatch.setattr(bridge, 'expert_propose', crash)
    with pytest.raises(OSError, match='power loss'):
        asyncio.run(auto_repair_failed_full(manager=Controller(), full=full,
            wrapper=client, checkpoint=cp, blueprint=blueprint(),
            base_plan_fingerprint=cp.doc['plan_fingerprint']))
    pinned = deepcopy(cp.doc['repair_collaboration_plans'])
    monkeypatch.setattr(bridge, 'expert_propose', original)
    result = asyncio.run(auto_repair_failed_full(manager=Controller(), full=full,
        wrapper=client, checkpoint=cp, blueprint=blueprint(),
        base_plan_fingerprint=cp.doc['plan_fingerprint']))
    assert result.run_id == 'frun_resume'
    assert cp.doc['repair_collaboration_plans'] == pinned
    assert len(cp.doc['repair_revisions']) == 1

# -------- Deep regression hardening: native relay, duplicates, hop identity --------

@pytest.mark.parametrize('reference', ['prev_tail', 'prev_tail_look', 'prev_tail_look_scene'])
def test_cross_scene_prev_tail_requires_finalized_real_media(reference):
    source = Shot(shot_id='s01_c01', scene_id='s01', index=1,
                  final_video='v1', video_end_source_version='v1',
                  video_end_frame=Asset(url='https://example.org/actual-tail.png', status='done'),
                  video_versions=[VideoVersion(version_id='v1', status='done')])
    dependent = Shot(shot_id='s02_c01', scene_id='s02', index=1,
                     opening_source='independent', link_mode='multi', video_reference_asset_id=reference)
    project = Project(name='relay', scenes=[Scene(scene_id='s01', shots=[source]),
                                              Scene(scene_id='s02', shots=[dependent])])
    ready, blocked, deps = video_submission_dependencies(project, _req('s02_c01'))
    assert len(ready) == 1 and not blocked
    assert deps['s02_c01']['reference_tail']['source_shot_id'] == 's01_c01'
    assert len(deps['s02_c01']['reference_tail']['tail_url_sha256']) == 64
    # Producer can queue independent shots, but cannot queue the relay before
    # the true selected media settles, even if it is a different scene.
    ready, blocked, _ = video_submission_dependencies(project, _req('s01_c01', 's02_c01'))
    assert [x['shot_id'] for x in ready] == ['s01_c01']
    assert blocked[0]['dependency_type'] == 'reference_tail'
    assert blocked[0]['dependency_reason'] == 'upstream_in_same_unsettled_batch'
    source.video_end_frame = Asset(status='pending')
    ready, blocked, _ = video_submission_dependencies(project, _req('s02_c01'))
    assert not ready and blocked[0]['dependency_reason'] == 'upstream_real_tail_unavailable'


def test_prev_tail_first_shot_cannot_fallback_to_unrelated_reference():
    first = Shot(shot_id='s01_c01', scene_id='s01', index=1,
                 link_mode='multi', video_reference_asset_id='prev_tail')
    proj = Project(name='bad', scenes=[Scene(scene_id='s01', shots=[first])])
    _, blocked, _ = video_submission_dependencies(proj, _req('s01_c01'))
    assert blocked[0]['dependency_reason'] == 'missing_reference_source'


def test_rehosted_tail_url_changes_dependency_digest():
    proj = _video_project()
    _, blocked, first = video_submission_dependencies(proj, _req('s01_c02'))
    assert not blocked
    proj.get_shot('s01_c01').video_end_frame.url = 'https://example.org/rehosted.png'
    _, blocked, later = video_submission_dependencies(proj, _req('s01_c02'))
    assert not blocked
    assert first != later


def test_active_video_job_blocks_second_same_shot_without_spending_provider_quota(tmp_path):
    from src.agents.producer_orchestration import existing_active_video_job
    from src.core.job_queue import JobQueue
    async def scenario():
        q = JobQueue(tmp_path / 'jobs.json', {})
        a = q.submit('p', 'video', 's01_c01')
        assert existing_active_video_job(q, 'p', 's01_c01') == a.job_id
        assert existing_active_video_job(q, 'p', 's01_c02') is None
        assert existing_active_video_job(q, 'other', 's01_c01') is None
        a.state = 'done'
        assert existing_active_video_job(q, 'p', 's01_c01') is None
    asyncio.run(scenario())


def test_native_producer_tool_rejects_duplicate_active_video_job(visual_env):
    from tests.test_director_visual import PROJECT as VPROJECT
    from tests.test_director_visual import _save_proj, _shot, _mark_first_frame_ready
    from src.agents.director_visual import _execute_shot_generate_videos
    rt, q, agnes, pdir = visual_env
    _save_proj(pdir, [_shot('s01_c01')])
    _mark_first_frame_ready(pdir, 's01_c01')
    async def scenario():
        job = q.submit(VPROJECT, 'video', 's01_c01')
        result = await _execute_shot_generate_videos(rt, VPROJECT,
            json.dumps({'shot_ids': ['s01_c01'], 'reference_policy': 'legacy_allowed'}),
            'generate', 'trace')
        return result, job
    result, job = asyncio.run(scenario())
    assert result['ok'] is False
    assert any(r.get('status') == 'shot_video_already_in_flight' for r in result['results'])
    assert len(q.jobs) == 1
    assert 'create_video' not in agnes.calls


def test_rest_endpoint_rejects_duplicate_active_video_job(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from src.core.job_queue import JobQueue
    from tests.test_director_visual import PROJECT as VPROJECT
    from tests.test_director_visual import _save_proj, _shot, _mark_first_frame_ready
    import routers.shots as router
    _save_proj(tmp_path, [_shot('s01_c01')])
    _mark_first_frame_ready(tmp_path, 's01_c01')
    pm = SimpleNamespace(get_project_dir=lambda _: tmp_path)
    monkeypatch.setattr(router, 'get_project_manager', lambda: pm)
    async def scenario():
        q = JobQueue(tmp_path / 'jobs.json', {})
        job = q.submit(VPROJECT, 'video', 's01_c01')
        monkeypatch.setattr(router, 'get_job_queue', lambda: q)
        with pytest.raises(HTTPException) as caught:
            await router.shot_video(VPROJECT, 's01_c01')
        return q, job, caught.value
    q, job, error = asyncio.run(scenario())
    assert error.status_code == 409
    assert error.detail['existing_job_id'] == job.job_id
    assert len(q.jobs) == 1


def test_c1_c2_hop_media_inflight_stays_frozen(monkeypatch):
    from tools.full_project_cloud_acceptance import _frozen_media_inflight
    monkeypatch.setenv('AGNES_VIDEO_INFLIGHT', '2')
    assert _frozen_media_inflight({}) == 2
    assert _frozen_media_inflight({'video_attempts': [{'state': 'ACCEPTED'}]}) == 1
    assert _frozen_media_inflight({'producer_video_inflight': 1}) == 1
    assert _frozen_media_inflight({'producer_video_inflight': 2}) == 2
    with pytest.raises(RuntimeError, match='invalid frozen'):
        _frozen_media_inflight({'producer_video_inflight': 200})
    monkeypatch.setenv('AGNES_VIDEO_INFLIGHT', 'unsafe')
    with pytest.raises(RuntimeError, match='must be an integer'):
        _frozen_media_inflight({})


def test_queue_reconfiguration_must_report_actual_applied_setting():
    from tools.full_project_cloud_acceptance import _apply_frozen_media_inflight
    class FakeQ:
        video_worker_count = 1
        def __init__(self, result):
            self.result = result
        async def reconfigure_video_workers(self, n, *, wait_settle):
            assert n == 2 and wait_settle is False
            return self.result
    asyncio.run(_apply_frozen_media_inflight(FakeQ({'applied': True}), 2))
    with pytest.raises(RuntimeError, match='pending reconciliation'):
        asyncio.run(_apply_frozen_media_inflight(FakeQ({'applied': False, 'reason': 'busy'}), 2))


def test_stale_prev_tail_flag_on_non_multi_mode_is_not_invented_dependency():
    first = Shot(shot_id='s01_c01', scene_id='s01', index=1,
                 link_mode='i2v', video_reference_asset_id='prev_tail')
    proj = Project(name='i2v', scenes=[Scene(scene_id='s01', shots=[first])])
    ready, blocked, deps = video_submission_dependencies(proj, _req('s01_c01'))
    assert ready and not blocked and deps == {}

@pytest.mark.parametrize('preference,maximum', [
    ('allow', 2), ('required_serial', 1), ('prefer_serial', 1),
])
def test_actual_native_video_tool_respects_parallel_preference(
        visual_env, monkeypatch, preference, maximum):
    """End-to-end through real director tool, queue and fake video handlers.

    No real provider quota; inspect handler overlap, not merely the request
    receipt or WorkerCount setting.
    """
    from tests.test_director_visual import PROJECT as VPROJECT
    from tests.test_director_visual import _save_proj, _shot, _mark_first_frame_ready
    from src.agents.director_visual import _execute_shot_generate_videos
    rt, q, agnes, pdir = visual_env
    monkeypatch.delenv('AGNES_VIDEO_INFLIGHT', raising=False)
    q._video_worker_count_override = 2
    _save_proj(pdir, [_shot('s01_c01'), _shot('s01_c02', idx=2)])
    for sid in ('s01_c01', 's01_c02'):
        _mark_first_frame_ready(pdir, sid)
    original_handler = q.handlers['video']
    counters = {'active': 0, 'maximum': 0}

    async def observed_handler(job, emit):
        counters['active'] += 1
        counters['maximum'] = max(counters['maximum'], counters['active'])
        try:
            await asyncio.sleep(.06)
            return await original_handler(job, emit)
        finally:
            counters['active'] -= 1

    q.handlers['video'] = observed_handler

    async def scenario():
        await q.start()
        assert q.video_worker_count == 2
        return await _execute_shot_generate_videos(
            rt, VPROJECT,
            json.dumps({'shot_ids': ['s01_c01', 's01_c02'],
                        'parallel_preference': preference,
                        'reference_policy': 'legacy_allowed'}),
            'record evidence', 'trace')

    result = asyncio.run(scenario())
    assert result['count'] == 2
    assert result['done'] == 2, result
    assert counters['maximum'] == maximum
    assert agnes.calls.count('create_video') == 2


# ---------------------------------------------------------------------------
# Round 03 local regression: the Producer planning call must survive thinking
# mode.  Real Agnes evidence (2026-10-28): with max_tokens=1500 the visible
# answer was cut off inside the JSON object, `_parse_json` refused it, and the
# Producer's real decision was silently replaced by `deterministic_fallback`.
# ---------------------------------------------------------------------------


class PlanningClient(ExpertClient):
    """Returns a valid collaboration plan for the planning call."""

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if 'deciding which specialists' in messages[0]['content']:
            return json.dumps(choice(task('director')))
        return json.dumps(proposal())


class TruncatedPlanningClient(ExpertClient):
    """Mimics the real truncated answer produced by a too-small token budget."""

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if 'deciding which specialists' in messages[0]['content']:
            return ('{"tasks": [{"role": "director", "task": "R3 local repair for '
                    's04_c01-s04_c02: decide the shot structure so the three '
                    'beats are each visually legible within the 2-shot')
        return json.dumps(proposal())


def test_producer_planning_budget_survives_thinking_mode(tmp_path):
    project = fixture_project(tmp_path)
    client = PlanningClient()
    plan = asyncio.run(producer_choose_repair_collaboration(
        client, finding(), project))
    assert client.calls, 'the Producer planning call must reach the model'
    messages, kwargs = client.calls[0]
    assert kwargs['thinking'] is True
    assert kwargs['max_tokens'] >= PRODUCER_PLANNING_MIN_SAFE_TOKENS, (
        'the planning answer must not be budgeted so small that thinking mode '
        'truncates its own JSON')
    assert plan['source'] == 'producer'
    assert plan['tasks'][0]['role'] == 'director'


def test_truncated_planning_answer_falls_back_and_is_logged(tmp_path, caplog):
    project = fixture_project(tmp_path)
    client = TruncatedPlanningClient()
    with caplog.at_level('WARNING', logger='src.agents.producer_orchestration'):
        plan = asyncio.run(producer_choose_repair_collaboration(
            client, finding(), project))
    assert plan['source'] == 'deterministic_fallback'
    assert 'fell back' in caplog.text
    assert 'safe fallback after' in plan['reason']