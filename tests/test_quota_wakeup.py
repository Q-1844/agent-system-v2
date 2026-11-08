import copy
import datetime as dt

import pytest

from tools import quota_wakeup as wake
from tools.resume_dispatch import dispatch_token


def checkpoint():
    return dict(schema_version=1, nonce='film', github_run_id='10', source_sha='source',
        request_sha256='a'*64, full_run_id='full', phase='WAITING_QUOTA',
        terminal_status='quota_exhausted', quota_resume_after='2000-01-01T00:00:00Z',
        drain_complete=True, film_target_seconds='auto', producer_video_inflight=2,
        video_attempts=[dict(state='ACCEPTED', video_id='paid', seconds=8)])


def production():
    return dict(id=10, status='completed', conclusion='failure', head_sha='source',
        repository={'full_name':wake.REPO}, path='.github/workflows/video-autoproduction-e2e.yml',
        updated_at='2026-11-02T04:22:11Z')


@pytest.fixture
def control(monkeypatch):
    doc = checkpoint();tickets = [];dispatches = []
    monkeypatch.setattr(wake, 'read_checkpoint', lambda *a:copy.deepcopy(doc))
    monkeypatch.setattr(wake, 'fetch_run', lambda *a:production())
    monkeypatch.setattr(wake.patrol, 'list_e2e_nonces', lambda *a:['film'])
    monkeypatch.setattr(wake, 'save_ticket', lambda *a:tickets.append(copy.deepcopy(a[-1])) or True)
    monkeypatch.setattr(wake.patrol, 'fetch_run', lambda *a:production())
    monkeypatch.setattr(wake.patrol, 'utc_now_ts', lambda:dt.datetime(2026,11,2,18,tzinfo=dt.timezone.utc).timestamp())
    def dispatch(*a, **kw):
        dispatches.append(kw)
        return dict(dispatched=True, status=204, inputs=kw)
    monkeypatch.setattr(wake.patrol, 'dispatch_next_hop', dispatch)
    return doc,tickets,dispatches


def test_completed_workflow_arms_exact_authoritative_film_without_cron(control):
    doc,tickets,dispatches = control
    doc['quota_resume_after'] = '2099-01-01T00:00:00Z'
    result = wake.resolve('archive','native','10')
    assert result['enabled'] and result['nonce']=='film'
    assert tickets[0]['state']=='ARMED' and tickets[0]['source_run_id']=='10'
    assert not dispatches


@pytest.mark.parametrize('change', ['owner','complete','missing_full','invalid_gate','cap'])
def test_resolve_never_arms_unrelated_terminal_or_invalid_state(control, change):
    doc,tickets,dispatches = control
    if change=='owner':doc['github_run_id']='11'
    if change=='complete':doc['phase']='COMPLETE'
    if change=='missing_full':doc['full_run_id']=None
    if change=='invalid_gate':doc['quota_resume_after']='invalid'
    if change=='cap':doc['resume_history']=[{}]*wake.MAX_AUTO_HOPS
    assert not wake.resolve('archive','native','10')['enabled']
    assert not tickets and not dispatches


@pytest.mark.parametrize('change', ['active','foreign_repo','wrong_workflow','source_sha'])
def test_resolve_requires_verified_production_identity(control,monkeypatch,change):
    doc,_,_ = control;run=production()
    if change=='active':run['status']='in_progress'
    if change=='foreign_repo':run['repository']={'full_name':'other/repo'}
    if change=='wrong_workflow':run['path']='.github/workflows/ci.yml'
    if change=='source_sha':doc['source_sha']='different'
    monkeypatch.setattr(wake,'fetch_run',lambda *a:run)
    with pytest.raises(ValueError):wake.resolve('archive','native','10','film')


def test_closed_gate_dispatches_only_another_delayed_controller(control,monkeypatch):
    doc,tickets,dispatches=control;doc['quota_resume_after']='2099-01-01T00:00:00Z'
    calls=[]
    def post(*a):calls.append(a);return dict(dispatched=True,status=204)
    monkeypatch.setattr(wake,'post_workflow_dispatch',post)
    before=copy.deepcopy(doc)
    result=wake.wake_once('archive','native','branch','film','10',timer_hops=2)
    assert result['action']=='rearm' and not dispatches
    assert calls[0][1]==wake.WORKFLOW
    assert calls[0][3]==dict(task_nonce='film',source_run_id='10',timer_hops='3',smoke_test='false')
    assert doc==before and tickets[0]['timer_hops']==3


def test_open_gate_dispatches_same_movie_via_existing_patrol_checks(control,monkeypatch):
    doc,tickets,dispatches=control
    # Reuse the actual sweep, including source liveness and dispatch receipt.
    monkeypatch.setattr(wake.patrol,'read_checkpoint',lambda *a:copy.deepcopy(doc))
    class Index:
        def read(self):return {'doc':copy.deepcopy(doc),'blob_sha':'old'}
        def write_cas(self,new,sha,**kwargs):
            assert sha=='old';doc.update(copy.deepcopy(new));return dict(ok=True,new_sha='new')
    import tools.video_task_state as state
    monkeypatch.setattr(state,'task_index',lambda *a:Index())
    before=copy.deepcopy(doc['video_attempts'])
    result=wake.wake_once('archive','native','branch','film','10')
    assert result['action']=='dispatch' and len(dispatches)==1
    assert dispatches[0]['resume_run_id']=='10' and dispatches[0]['task_nonce']=='film'
    assert dispatches[0]['target_seconds']=='auto' and dispatches[0]['media_inflight']==2
    assert doc['video_attempts']==before and doc['github_run_id']=='10'
    assert tickets[0]['state']=='FIRED'


@pytest.mark.parametrize('reason',['run alive','run query failed, treat as unknown',
                                'run recently finished (A may be dispatching)'])
def test_transient_source_uncertainty_is_checked_after_delay(control,monkeypatch,reason):
    monkeypatch.setattr(wake.patrol,'sweep_nonce',lambda *a,**kw:dict(action='skip',reason=reason))
    monkeypatch.setattr(wake,'post_workflow_dispatch',lambda *a:dict(dispatched=True,status=204))
    assert wake.wake_once('archive','native','branch','film','10')['action']=='rearm'


def test_failed_resume_dispatch_rearms_without_retrying_paid_media(control,monkeypatch):
    monkeypatch.setattr(wake.patrol,'sweep_nonce',lambda *a,**kw:dict(action='dispatch_failed'))
    monkeypatch.setattr(wake,'post_workflow_dispatch',lambda *a:dict(dispatched=True,status=204))
    assert wake.wake_once('archive','native','branch','film','10')['action']=='rearm'


def test_existing_child_receipt_stops_duplicate_controller(control,monkeypatch):
    monkeypatch.setattr(wake.patrol,'sweep_nonce',lambda *a,**kw:dict(
        action='skip',reason='dispatched recently, waiting for child run'))
    monkeypatch.setattr(wake,'post_workflow_dispatch',lambda *a:pytest.fail('duplicate alarm'))
    assert wake.wake_once('archive','native','branch','film','10')['action']=='skip'


@pytest.mark.parametrize('source,nonce,expected',[
    ('10','film','skip'), (10,'film','skip'), ('9','film','dispatch'),
    ('10','other','dispatch'), (None,None,'skip'), (None,'film','skip'), ('9',None,'dispatch'),
])
def test_inherited_dispatch_receipt_does_not_strand_a_later_owner(control,monkeypatch,source,nonce,expected):
    from types import SimpleNamespace
    import tools.video_task_state as state
    doc,_,dispatches=control
    inputs={}
    if source is not None:inputs['resume_run_id']=source
    if nonce is not None:inputs['task_nonce']=nonce
    doc['auto_resume']=dict(dispatched=True,at=wake.patrol.utc_now_ts()-60,inputs=inputs)
    monkeypatch.setattr(state,'task_index',lambda *a:None)
    monkeypatch.setattr(state,'ProductionCheckpoint',lambda *a:SimpleNamespace(save=lambda **kw:doc.update(kw)))
    old_receipts=copy.deepcopy(doc['video_attempts'])
    result=wake.wake_once('archive','native','branch','film','10')
    assert result['action']==expected and len(dispatches)==(1 if expected=='dispatch' else 0)
    assert doc['github_run_id']=='10' and doc['video_attempts']==old_receipts


def test_owner_handoff_stops_old_timer_without_mutating_receipts(control,monkeypatch):
    doc,tickets,_=control;doc['github_run_id']='11';before=copy.deepcopy(doc)
    monkeypatch.setattr(wake,'post_workflow_dispatch',lambda *a:pytest.fail('stale dispatch'))
    assert wake.wake_once('archive','native','branch','film','10')['action']=='stop'
    assert doc==before and not tickets


@pytest.mark.parametrize('age,smoke,allowed',[(10,False,False),(1190,False,False),
                                         (1200,False,True),(10,True,False),(60,True,True)])
def test_missing_environment_timer_cannot_create_a_fast_dispatch_loop(monkeypatch,age,smoke,allowed):
    created=dt.datetime(2026,11,2,tzinfo=dt.timezone.utc)
    run=dict(repository={'full_name':wake.REPO},path='.github/workflows/'+wake.WORKFLOW,
             created_at=created.isoformat())
    monkeypatch.setattr(wake,'fetch_run',lambda *a:run)
    if allowed:wake.validate_delay('native','20',smoke=smoke,now=created.timestamp()+age)
    else:
        with pytest.raises(ValueError,match='wait timer'):
            wake.validate_delay('native','20',smoke=smoke,now=created.timestamp()+age)


def test_ticket_cas_conflict_does_not_repeat_already_issued_dispatch(monkeypatch):
    monkeypatch.setattr(wake,'record_ticket',lambda *a:(_ for _ in ()).throw(RuntimeError('CAS conflict')))
    assert wake.save_ticket('archive','film','10',dict(state='ARMED')) is False


@pytest.mark.parametrize('failure',['none','after_commit','advanced_owner','completed'])
def test_ticket_cas_preserves_production_identity_and_paid_receipts(monkeypatch,failure):
    from tests.relstore.test_completed_film_registry import Index
    import tools.video_task_state as state
    doc=checkpoint();index=Index(doc);ticket=dict(state='ARMED',source_run_id='10')
    if failure=='after_commit':index.fail='after'
    if failure=='advanced_owner':index.doc['github_run_id']='11'
    if failure=='completed':index.doc['phase']='COMPLETE'
    monkeypatch.setattr(state,'task_index',lambda *a: index)
    result=wake.save_ticket('archive','film','10',ticket)
    assert result is (failure=='none')
    assert index.doc['video_attempts']==doc['video_attempts']
    assert index.doc['source_sha']==doc['source_sha']
    if failure in ('advanced_owner','completed'):
        assert 'quota_wakeup' not in index.doc and index.revision==1
    else:assert index.doc['quota_wakeup']==ticket


@pytest.mark.parametrize('env,expected',[({},''),({'GITHUB_TOKEN':'native'},'native'),
    ({'GITHUB_TOKEN':'native','RESUME_DISPATCH_TOKEN':'pat'},'pat'),
    ({'GITHUB_TOKEN':'native','RESUME_DISPATCH_TOKEN':' '},'native')])
def test_native_dispatch_token_fallback(env,expected):
    assert dispatch_token(env)==expected


@pytest.mark.parametrize('change,expected', [
    ('none','dispatch'),('not_drained','rearm'),('real_quota','rearm'),
    ('alive','rearm'),('unknown','rearm'),('cancelled','rearm'),
    ('sha_changed','rearm'),('recent_dispatch','skip'),('future_gate','rearm'),
])
def test_short_test_completion_skips_only_deadlink_grace(control,monkeypatch,change,expected):
    doc,tickets,dispatches=control
    doc.update(quota_wait_source='test_window',quota_test_policy=dict(
        schema_version=1,nonce='film',video_seconds=24,wait_minutes=2))
    source=production();source['updated_at']='2026-11-02T17:59:30Z'
    if change=='not_drained':doc['drain_complete']=False
    elif change=='real_quota':doc['quota_wait_source']='daily'
    elif change=='alive':source['status']='in_progress'
    elif change=='unknown':source=None
    elif change=='cancelled':source['conclusion']='cancelled'
    elif change=='sha_changed':source['head_sha']='other'
    elif change=='future_gate':doc['quota_resume_after']='2099-01-01T00:00:00Z'
    elif change=='recent_dispatch':doc['auto_resume']=dict(dispatched=True,at=1793642340,
        inputs=dict(task_nonce='film',resume_run_id='10'))
    monkeypatch.setattr(wake.patrol,'fetch_run',lambda *a:source)
    monkeypatch.setattr(wake,'post_workflow_dispatch',lambda *a:dict(dispatched=True,status=204))
    class Index:
        def read(self):return {'doc':copy.deepcopy(doc),'blob_sha':'old'}
        def write_cas(self,new,sha,**kwargs):
            assert sha=='old';doc.update(copy.deepcopy(new));return dict(ok=True,new_sha='new')
    import tools.video_task_state as state
    monkeypatch.setattr(state,'task_index',lambda *a:Index())
    out=wake.wake_once('archive','native','branch','film','10')
    assert out['action']==expected
    assert len(dispatches)==(expected=='dispatch')
    assert doc['video_attempts']==checkpoint()['video_attempts']
