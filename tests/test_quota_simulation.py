"""Accelerated real-path quota cycles: retain daily charges and submission guards."""
import asyncio
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.quota import QuotaLedger
from src.core.quota_simulation import (select_policy, validate_policy, short_timer,
                                      reconcile_compressed_job_gates)
from src.core.job_queue import Job, JobQueue
from src.api.agnes_client import AgnesClient, DailyQuotaExhausted
from tools.full_project_cloud_acceptance import BoundedRealClient, auto_resume_next_hop
from tools.quota_gate import gate_status
from tools.quota_wakeup import armable
from tools.resume_dispatch import progress_backoff, auto_resume_allowed
from tests.test_day_continuation import Journal, Slots

POLICY = dict(schema_version=1, nonce='film', video_seconds=24, wait_minutes=2)
NOW = dt.datetime(2026, 11, 8, 4, tzinfo=dt.timezone.utc)


def test_policy_survives_default_inputs_without_widening_the_test_window():
    policy = select_policy('film', {}, {'DAILY_VIDEO_SECONDS':'24','QUOTA_TEST_WAIT_MINUTES':'2'}, restored=False)
    assert policy == POLICY
    assert select_policy('film', {'quota_test_policy':policy}, {}, restored=True) == policy
    assert select_policy('film', {}, {}, restored=False) is None
    with pytest.raises(ValueError, match='creating'):
        select_policy('film', {}, {'DAILY_VIDEO_SECONDS':'24','QUOTA_TEST_WAIT_MINUTES':'2'}, restored=True)
    with pytest.raises(ValueError, match='change'):
        select_policy('film', {'quota_test_policy':policy}, {'QUOTA_TEST_WAIT_MINUTES':'3'}, restored=True)


@pytest.mark.parametrize('field,value', [('nonce','other'),('video_seconds',0),('video_seconds',501),
                                        ('wait_minutes',0),('wait_minutes',61),('video_seconds',True)])
def test_malformed_or_foreign_policy_fails_closed(field,value):
    policy = {**POLICY,field:value}
    with pytest.raises(ValueError):validate_policy(policy,'film')


def test_restarting_and_rolling_test_window_does_not_reset_daily_consumption(tmp_path,monkeypatch):
    import src.core.quota as quota
    clock=[NOW.timestamp()];monkeypatch.setattr(quota.time,'time',lambda:clock[0])
    path=tmp_path/'quota.json';ledger=QuotaLedger(path);ledger.configure_video_test(POLICY)
    ledger.consume('video_seconds',24)
    ok,_,gate,source=ledger.check_video(8)
    assert not ok and source=='test_window'
    until=dt.datetime.fromisoformat(gate.replace('Z','+00:00')).timestamp()
    assert until-clock[0]==120
    clock[0]+=60
    ledger=QuotaLedger(path);ledger.configure_video_test(POLICY)
    assert ledger.check_video(8)[2]==gate  # Re-checks must not extend the reset.
    clock[0]=until
    assert ledger.can('video_seconds',8)==(True,'')
    assert ledger.video_test_snapshot()['cycle']==1
    ledger.consume('video_seconds',8)
    assert ledger.snapshot()['video_seconds']['used']==32
    assert ledger.video_test_snapshot()['used']==8
    assert QuotaLedger(path).video_test_snapshot()==ledger.video_test_snapshot()


def test_real_daily_wall_has_priority_over_any_accelerated_window(tmp_path,monkeypatch):
    import src.core.quota as quota
    clock=[NOW.timestamp()];monkeypatch.setattr(quota.time,'time',lambda:clock[0])
    ledger=QuotaLedger(tmp_path/'quota.json');ledger.configure_video_test(POLICY)
    for _ in range(21):
        if ledger.can('video_seconds',24)[0]:ledger.consume('video_seconds',24)
        else:clock[0]+=121
    ledger._data['video_seconds']=496;ledger._flush()
    clock[0]+=3600
    ok,reason,gate,source=ledger.check_video(8)
    assert not ok and '496/500' in reason and gate is None and source=='daily'
    ledger=QuotaLedger(ledger.path);ledger.configure_video_test(POLICY)
    assert ledger.snapshot()['video_seconds']['used']==496
    assert not ledger.can('video_seconds',8)[0]


def test_provider_daily_429_keeps_real_day_gate_even_during_simulation(tmp_path):
    ledger=QuotaLedger(tmp_path/'quota.json');ledger.configure_video_test(POLICY)
    seen=[]
    def reject(request):
        seen.append(request)
        return httpx.Response(429,json={'error':{'message':'daily quota exhausted'}})
    client=AgnesClient({'base_url':'https://agnes.invalid','api_key':'test',
                        'models':{'video':'agnes-video-2.5-flash'}},quota=ledger,
                       transport=httpx.MockTransport(reject));client.limiter=Slots()
    with pytest.raises(DailyQuotaExhausted) as exc:
        asyncio.run(client.create_video('p',mode='t2v',duration_s=8))
    assert exc.value.quota_source=='daily' and exc.value.resume_after.endswith('16:10:00Z')
    assert len(seen)==1 and ledger.snapshot()['video_seconds']['used']==0


def test_real_client_checks_local_wall_again_after_acquiring_rate_limit_slot(tmp_path):
    from contextlib import asynccontextmanager
    ledger=QuotaLedger(tmp_path/'quota.json');ledger.configure_video_test(POLICY)
    calls=[]
    class Limit:
        @asynccontextmanager
        async def slot(self,bucket):
            ledger.consume('video_seconds',24)
            yield
    client=AgnesClient({'base_url':'https://agnes.invalid','api_key':'test','models':{'video':'agnes-video-2.5-flash'}},quota=ledger,
                       transport=httpx.MockTransport(lambda r:calls.append(r)))
    client.limiter=Limit()
    with pytest.raises(DailyQuotaExhausted) as exc:
        asyncio.run(client.create_video('p',mode='t2v',duration_s=8))
    assert not calls and exc.value.quota_source=='test_window'


def test_short_timer_is_only_for_a_valid_explicit_test_wall():
    doc=dict(nonce='film',phase='WAITING_QUOTA',terminal_status='quota_exhausted',
             quota_test_policy=POLICY,quota_wait_source='test_window')
    assert short_timer(doc)
    assert not short_timer({**doc,'quota_wait_source':'daily'})
    assert not short_timer({**doc,'phase':'INTERRUPTED'})
    assert not short_timer({**doc,'quota_test_policy':{**POLICY,'wait_minutes':20}})
    with pytest.raises(ValueError):short_timer({**doc,'nonce':'other'})


def test_bounded_client_keeps_test_source_on_other_worker_rejection(tmp_path):
    ledger=QuotaLedger(tmp_path/'quota.json');ledger.configure_video_test(POLICY)
    journal=Journal();journal.doc['quota_test_policy']=POLICY
    wrapper=BoundedRealClient(SimpleNamespace(quota=ledger),journal,tmp_path,tmp_path/'evidence')
    wrapper.plan={}
    error=DailyQuotaExhausted('test wall',resume_after='2099-01-01T00:00:00Z',quota_source='test_window')
    async def run():
        await wrapper.pause_for_quota(error)
        with pytest.raises(DailyQuotaExhausted) as caught:await wrapper.create_video('p',duration_s=8)
        assert caught.value.quota_source=='test_window'
    asyncio.run(run())
    assert journal.doc['quota_test_window']['policy']==POLICY
    assert journal.doc['quota_wait_source']=='test_window'


def test_native_queue_crosses_two_test_windows_without_erasing_cost_or_paid_media(tmp_path,monkeypatch):
    """Native handler + real ledger/client/queue, three lifetimes, two fake resets."""
    import deps,src.core.quota as quota
    from routers.shots import _handler_video
    from tests.test_p55_recovery import FakeAgnes,FakeRehost,_FakePM,_save_proj,_mark_ff_ready,_shot,PROJECT
    from src.core.store import ProjectStore
    clock=[NOW.timestamp()];monkeypatch.setattr(quota.time,'time',lambda:clock[0])
    class Date(dt.datetime):
        @classmethod
        def now(cls,tz=None):return cls.fromtimestamp(clock[0],tz or dt.timezone.utc)
    monkeypatch.setattr(dt,'datetime',Date)
    pdir=tmp_path/PROJECT;shots=[_shot(f's01_c{i:02}',idx=i,dur=10) for i in range(1,5)]
    for shot in shots:
        shot.video.num_frames=241
        shot.video.frame_rate=24
    _save_proj(pdir,shots)
    for s in shots:_mark_ff_ready(pdir,s.shot_id)
    policy={**POLICY,'video_seconds':12}
    path=tmp_path/'quota.json';ledger=QuotaLedger(path);ledger.configure_video_test(policy)
    paid=FakeAgnes();posts=[]
    def accept(req):
        posts.append(req);return httpx.Response(200,json={'video_id':f'paid-{len(posts)}'})
    api=AgnesClient({'base_url':'https://agnes.invalid','api_key':'test','models':{'video':'agnes-video-2.5-flash'}},quota=ledger,
                    transport=httpx.MockTransport(accept));api.limiter=Slots()
    paid.create_video=api.create_video;paid.quota=ledger;paid.models=api.models
    journal=Journal();journal.doc['quota_test_policy']=policy
    wrapper=BoundedRealClient(paid,journal,pdir,tmp_path/'evidence');wrapper.plan={}
    monkeypatch.setattr(deps,'agnes_client',wrapper);monkeypatch.setattr(deps,'_rehost_host',FakeRehost())
    monkeypatch.setattr(deps,'project_manager',_FakePM(pdir))
    qpath=tmp_path/'jobs.json';queue=JobQueue(qpath,{'video':_handler_video},require_ownership=False)
    monkeypatch.setattr(deps,'job_queue',queue)
    async def execute(sid):
        job=queue.submit(PROJECT,'video',sid,origin_run_id='run-'+sid)
        job.state='running';await queue._run_job(job,'test');return job
    first=asyncio.run(execute('s01_c01'));assert first.state=='done'
    initial=ProjectStore(pdir).load().get_shot('s01_c01');version=initial.final_video
    video=next(v for v in initial.video_versions if v.version_id==version)
    before=hashlib.sha256(Path(video.local).read_bytes()).hexdigest()
    for sid in ['s01_c02','s01_c03']:
        failed=asyncio.run(execute(sid));assert failed.state=='failed'
        assert failed.params['failure_category']=='daily_quota'
        assert failed.params['quota_wait_source']=='test_window'
        count=len(posts)
        async def blocked():assert queue.retry(failed.job_id) is None
        asyncio.run(blocked());assert len(posts)==count
        until=failed.params['quota_resume_after']
        clock[0]=dt.datetime.fromisoformat(until.replace('Z','+00:00')).timestamp()
        # All actual gate checks share the fixed UTC clock; no gate field is edited.
        ledger=QuotaLedger(path);ledger.configure_video_test(policy);paid.quota=api.quota=ledger
        wrapper=BoundedRealClient(paid,journal,pdir,tmp_path/'evidence');wrapper.plan={}
        wrapper.restore_usage(journal.doc)
        monkeypatch.setattr(deps,'agnes_client',wrapper)
        queue=JobQueue(qpath,{'video':_handler_video},require_ownership=False)
        monkeypatch.setattr(deps,'job_queue',queue)
        async def resume():
            job=queue.retry(failed.job_id);assert job is not None
            job.state='running';await queue._run_job(job,'test');assert job.state=='done'
        asyncio.run(resume())
        assert len(posts)==count+1
        saved=ProjectStore(pdir).load().get_shot('s01_c01')
        assert saved.final_video==version and hashlib.sha256(Path(video.local).read_bytes()).hexdigest()==before
    assert len(posts)==3 and ledger.snapshot()['video_seconds']['used']==30
    assert ledger.video_test_snapshot()['cycle']==2
    assert len(journal.doc['video_attempts'])==3
    assert not any(i['state'] in ['SUBMITTING','UNKNOWN'] for i in journal.doc['submission_intents'])


def test_compressed_gate_reconciliation_never_releases_accepted_or_unknown_jobs(tmp_path):
    queue=JobQueue(tmp_path/'jobs.json',{},require_ownership=False)
    doc=dict(nonce='film',github_run_id='10',project_name='p',quota_resume_after='2000-01-01T00:00:00Z')
    old='2099-01-01T00:00:00Z'
    doc['quota_test_gate_override']=dict(nonce='film',source_run_id='10',old_gate=old,new_gate=doc['quota_resume_after'])
    for state in ['not_sent','accepted','unknown','rejected']:
        p=dict(failure_category='daily_quota',quota_resume_after=old,provider_submit_state=state,
               submission_intent_id=state,handler_started=True)
        if state=='accepted':p['provider_task_id']='paid'
        queue.jobs[state]=Job(state,'p','video',state='failed',params=p,error='daily quota exhausted')
    assert reconcile_compressed_job_gates(queue,doc)==1
    assert queue.jobs['not_sent'].params['quota_resume_after']==doc['quota_resume_after']
    assert all(queue.jobs[s].params['quota_resume_after']==old for s in ['accepted','unknown','rejected'])
    assert reconcile_compressed_job_gates(queue,doc)==0
    recovered={**doc,'github_run_id':'11','quota_resume_after':None,'resume_history':[{'run_id':'10'}]}
    assert reconcile_compressed_job_gates(queue,recovered)==0
    with pytest.raises(ValueError):reconcile_compressed_job_gates(queue,{**doc,'nonce':'other'})


def test_empty_hops_back_off_and_completed_media_resets_the_counter():
    doc=dict(video_attempts=[{'state':'ACCEPTED'}],submission_intents=[{'state':'ACCEPTED'}])
    for n in range(4):
        progress,until=progress_backoff(doc,now=NOW);doc['resume_progress']=progress
        assert bool(until)==(n==3)
    assert until=='2026-11-08T04:20:00Z'
    doc['submission_intents'][0]['state']='COMPLETED'
    assert progress_backoff(doc,now=NOW)[0]['empty_hops']==0


def test_backoff_is_resumable_only_after_its_timer():
    doc=dict(phase='INTERRUPTED',terminal_status='technical_wait',full_run_id='frun',
             resume_not_before='2026-11-08T04:20:00Z')
    assert not auto_resume_allowed(doc,now=NOW)[0]
    assert armable(doc)
    assert auto_resume_allowed(doc,now=NOW+dt.timedelta(minutes=20))[0]


def test_driver_empty_hop_guard_makes_no_dispatch(tmp_path,monkeypatch):
    import tools.resume_dispatch as dispatch
    journal=Journal();journal.doc.update(phase='FAILED',terminal_status='paused',resume_progress=dict(accepted=0,completed=0,empty_hops=2))
    wrapper=BoundedRealClient(SimpleNamespace(),journal,tmp_path,tmp_path/'evidence')
    monkeypatch.setattr(dispatch,'dispatch_token',lambda:'test')
    sent=[];monkeypatch.setattr(dispatch,'dispatch_next_hop',lambda *a,**kw:sent.append(kw))
    result=asyncio.run(auto_resume_next_hop(wrapper,'film',30))
    assert not result['dispatched'] and not sent
    assert journal.doc['phase']=='INTERRUPTED' and armable({**journal.doc,'full_run_id':'frun'})


def test_cached_test_metadata_does_not_activate_a_default_production_ledger(tmp_path):
    path=tmp_path/'quota.json';ledger=QuotaLedger(path);ledger.configure_video_test(POLICY)
    ledger.consume('video_seconds',24)
    assert not ledger.can('video_seconds',8)[0]
    ordinary=QuotaLedger(path)
    assert ordinary.can('video_seconds',8)[0]  # No explicit test policy in this process.
    assert ordinary.snapshot()['video_seconds']['used']==24
    ordinary.configure_video_test(POLICY)
    assert not ordinary.can('video_seconds',8)[0]


def test_corrupt_window_is_rejected_instead_of_clearing_its_usage(tmp_path):
    ledger=QuotaLedger(tmp_path/'quota.json');ledger.configure_video_test(POLICY)
    ledger._data['video_test']['used']=-10;ledger._flush()
    with pytest.raises(ValueError,match='corrupt'):
        QuotaLedger(ledger.path).configure_video_test(POLICY)


def test_compress_cli_records_original_job_gate_across_repeated_edits(monkeypatch):
    from tools import b1_test_ops as ops
    doc=dict(nonce='film',github_run_id='10',phase='WAITING_QUOTA',
             terminal_status='quota_exhausted',quota_resume_after='2099-01-01T00:00:00Z',
             video_attempts=[dict(state='ACCEPTED',video_id='keep')])
    original=copy.deepcopy(doc['video_attempts']);writes=[]
    class Index:
        def read(self):return dict(doc=copy.deepcopy(doc),blob_sha='old')
        def write_cas(self,new,sha,**kw):
            assert sha=='old';writes.append(copy.deepcopy(new));doc.update(new)
            return dict(ok=True,new_sha='new')
    monkeypatch.setattr(ops,'task_index',lambda *a:Index())
    monkeypatch.setenv('B1_ARCHIVE_TOKEN','test')
    for delay in ['2','3']:
        monkeypatch.setattr('sys.argv',['b1_test_ops','--nonce','film','--gate-minutes',delay])
        assert ops.main()==0
        override=doc['quota_test_gate_override']
        assert override['old_gate']=='2099-01-01T00:00:00Z'
        assert override['new_gate']==doc['quota_resume_after']
        assert doc['video_attempts']==original
    assert len(writes)==2


def test_slow_parallel_posts_cannot_spend_the_same_remaining_test_credit(tmp_path):
    ledger=QuotaLedger(tmp_path/'quota.json')
    ledger.configure_video_test({**POLICY,'video_seconds':12})
    seen=[]
    async def provider(request):
        seen.append(request);identity=len(seen)
        await asyncio.sleep(.01)  # Another worker can enter before the receipt.
        return httpx.Response(200,json={'video_id':f'paid-{identity}'})
    client=AgnesClient({'base_url':'https://agnes.invalid','api_key':'test',
                        'models':{'video':'agnes-video-2.5-flash'}},quota=ledger,
                       transport=httpx.MockTransport(provider));client.limiter=Slots()
    async def run():return await asyncio.gather(
        client.create_video('one',mode='t2v',duration_s=8),
        client.create_video('two',mode='t2v',duration_s=8),return_exceptions=True)
    results=asyncio.run(run())
    assert len(seen)==1
    assert sum(isinstance(r,dict) for r in results)==1
    assert sum(isinstance(r,DailyQuotaExhausted) and r.quota_source=='test_window' for r in results)==1
    assert ledger.snapshot()['video_seconds']['used']==8
    assert ledger.video_test_snapshot()['used']==8


def test_submit_accounting_lock_releases_after_explicit_rejection(tmp_path):
    ledger=QuotaLedger(tmp_path/'quota.json');ledger.configure_video_test({**POLICY,'video_seconds':12})
    seen=[]
    async def provider(request):
        seen.append(request);n=len(seen);await asyncio.sleep(.01)
        return httpx.Response(400,text='invalid request') if n==1 else httpx.Response(200,json={'video_id':'second'})
    client=AgnesClient({'base_url':'https://agnes.invalid','api_key':'test',
                        'models':{'video':'agnes-video-2.5-flash'}},quota=ledger,
                       transport=httpx.MockTransport(provider));client.limiter=Slots()
    async def run():return await asyncio.gather(
        client.create_video('bad',mode='t2v',duration_s=8),
        client.create_video('good',mode='t2v',duration_s=8),return_exceptions=True)
    results=asyncio.run(run())
    assert len(seen)==2 and isinstance(results[0],Exception) and results[1]=={'video_id':'second'}
    assert ledger.snapshot()['video_seconds']['used']==8
