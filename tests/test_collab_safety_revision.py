"""Offline fault cases for the source-handoff safety repair (no Provider calls)."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import multiprocessing

import pytest
from src.core.models import Project, Scene, Shot, VideoAsset, VideoVersion
from src.core.store import ProjectStore, ProjectConflictError
from src.agents import review_pipeline as rp, collab_modes as cm, master_lease as ml


def make_project(pdir, count=1):
    shots=[]
    for i in range(count):
        sid=f's01_c{i+1:02d}';path=pdir/f'{sid}_old.mp4';path.write_bytes(b'old')
        vv=VideoVersion(version_id=f'old_{i}',status='done',local=str(path),video_id=f'old_provider_{i}')
        shots.append(Shot(shot_id=sid,scene_id='s01',index=i+1,
                          video=VideoAsset(status='done',local=str(path)),
                          video_versions=[vv],final_video=vv.version_id))
    ProjectStore(pdir).save(Project(name='p',scenes=[Scene(scene_id='s01',shots=shots)]))
    return ProjectStore(pdir)


def mock_review(monkeypatch):
    monkeypatch.setattr(rp,'extract_shot_frame_local',lambda p,proj,s,**kw:{
        'local':'frame.jpg','source':{'version_id':s.final_video}})
    async def good(*a,**kw):
        return {'review':{'dimensions':{d:{'status':'pass'} for d in rp.VIDEO_HARD_DIMS}},
                'derived':{'overall':'pass'}}
    monkeypatch.setattr(rp,'review_shot_core',good)
    return SimpleNamespace(_resolve_client_or_none=lambda **kw:object())


def test_store_rejects_stale_whole_project(tmp_path):
    store=make_project(tmp_path)
    a,b=store.load(),store.load()
    a.story_text='A';store.save(a)
    b.story_text='B'
    with pytest.raises(ProjectConflictError):store.save(b)
    assert store.load().story_text=='A'


def test_store_threads_do_not_lose_updates(tmp_path):
    make_project(tmp_path)
    def add(_):ProjectStore(tmp_path).update_shot('s01_c01',lambda s:setattr(s,'index',s.index+1))
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(add,range(40)))
    assert ProjectStore(tmp_path).load().get_shot('s01_c01').index==41


def _lease_race(pdir,barrier,queue,sid):
    barrier.wait();queue.put(ml.acquire(Path(pdir),sid,now=100)['ok'])


def test_lease_cross_process_unique_holder(tmp_path):
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);queue=ctx.Queue()
    ps=[ctx.Process(target=_lease_race,args=(str(tmp_path),barrier,queue,f's{i}')) for i in range(2)]
    for p in ps:p.start()
    values=[queue.get(timeout=15) for _ in ps]
    for p in ps:p.join(15);assert p.exitcode==0
    assert sorted(values)==[False,True]


def test_lease_missing_and_old_generation_denied(tmp_path):
    assert not ml.verify(tmp_path,'s')['verified']
    a=ml.acquire(tmp_path,'s',now=1,ttl_s=1)
    b=ml.acquire(tmp_path,'s',now=3)
    assert b['generation']>a['generation']
    assert not ml.verify(tmp_path,'s',expected_generation=a['generation'])['verified']


@pytest.mark.parametrize('failure',['vision','frame','missing_dimensions'])
def test_review_failure_cannot_pass(tmp_path,monkeypatch,failure):
    make_project(tmp_path);rt=mock_review(monkeypatch)
    if failure=='frame':
        def fail(*a,**kw):raise RuntimeError('no frame')
        monkeypatch.setattr(rp,'extract_shot_frame_local',fail)
    else:
        async def fail(*a,**kw):
            if failure=='vision':raise RuntimeError('vision unavailable')
            return {'review':{'dimensions':{}},'derived':{'overall':'pass'}}
        monkeypatch.setattr(rp,'review_shot_core',fail)
    rv=asyncio.run(rp.run_independent_review(rt,tmp_path,'p'))
    assert rv['incomplete_shots']==['s01_c01'] and not rv['machine_passed']


def test_review_missing_requested_shot_and_empty_selection(tmp_path,monkeypatch):
    make_project(tmp_path);rt=mock_review(monkeypatch)
    rv=asyncio.run(rp.run_independent_review(rt,tmp_path,'p',shot_ids=['missing']))
    assert rv['incomplete_shots']==['missing']
    rv=asyncio.run(rp.run_independent_review(rt,tmp_path,'p',shot_ids=[]))
    assert rv['results']==[] and not rv['machine_passed']


def test_review_bounded_overlap_and_failure_isolation(tmp_path,monkeypatch):
    make_project(tmp_path,3);rt=mock_review(monkeypatch);active=0;peak=0
    async def vision(*a,**kw):
        nonlocal active,peak
        active+=1;peak=max(peak,active)
        try:
            await asyncio.sleep(.005)
            if kw['shot'].shot_id=='s01_c02':raise RuntimeError('one shot failed')
            return {'review':{'dimensions':{d:{'status':'pass'} for d in rp.VIDEO_HARD_DIMS}},'derived':{'overall':'pass'}}
        finally:active-=1
    monkeypatch.setattr(rp,'review_shot_core',vision)
    rv=asyncio.run(rp.run_independent_review(rt,tmp_path,'p',max_concurrency=2))
    assert peak==2 and rv['incomplete_shots']==['s01_c02']
    assert [e['overall'] for e in rv['results']]==['pass','incomplete','pass']


def test_review_selection_change_is_stale(tmp_path,monkeypatch):
    store=make_project(tmp_path);rt=mock_review(monkeypatch);changed=False
    async def vision(*a,**kw):
        nonlocal changed
        if not changed:
            changed=True
            store.update_shot('s01_c01',lambda s:setattr(s,'final_video','new'))
        return {'review':{'dimensions':{d:{'status':'pass'} for d in rp.VIDEO_HARD_DIMS}},'derived':{'overall':'pass'}}
    monkeypatch.setattr(rp,'review_shot_core',vision)
    rv=asyncio.run(rp.run_independent_review(rt,tmp_path,'p'))
    assert rv['results'][0]['version_id']=='old_0' and rv['results'][0]['overall']=='stale'


def run_record():
    return {'run_id':'r','project':'p','snapshot':{'mode':cm.COLLAB_MODES['combo_recommended']},
            'steps':[],'authorization':{'scope':'test'}}


@pytest.mark.parametrize('state',['failed','cancelled','done'])
def test_failed_or_no_new_repair_never_reviews_old(tmp_path,monkeypatch,state):
    make_project(tmp_path);calls=[]
    async def review(*a,**kw):
        calls.append(kw)
        return {'batch_id':'b','shots_reviewed':1,'results':[{'shot_id':'s01_c01',
                'version_id':'old_0','overall':'hard_error','hard_errors':['anatomy_artifact']}],
                'hard_error_shots':['s01_c01'],'incomplete_shots':[]}
    monkeypatch.setattr(rp,'run_independent_review',review)
    class Queue:
        def submit(self,*a,**kw):return SimpleNamespace(job_id='j',state=state)
        def get(self,j):return SimpleNamespace(state=state)
    rt=SimpleNamespace(_resolve_job_queue_or_none=lambda:Queue(),_resolve_client_or_none=lambda **kw:None)
    run=run_record();asyncio.run(cm._final_review_and_repair(rt,tmp_path,'p',run))
    assert len(calls)==1
    assert run['machine_acceptance']['status']=='needs_attention'
    assert cm._completed_status(run)=='completed_with_issues'


def test_identity_failure_requests_strategy_without_reroll(tmp_path,monkeypatch):
    make_project(tmp_path)
    async def review(*a,**kw):return {'batch_id':'b','results':[{'shot_id':'s01_c01',
        'version_id':'old_0','overall':'hard_error','hard_errors':['missing_character']}],
        'hard_error_shots':['s01_c01']}
    monkeypatch.setattr(rp,'run_independent_review',review)
    def no_submit(*a,**kw):raise AssertionError('blind reroll')
    rt=SimpleNamespace(_resolve_job_queue_or_none=lambda:SimpleNamespace(submit=no_submit))
    run=run_record();asyncio.run(cm._final_review_and_repair(rt,tmp_path,'p',run))
    assert run['decision_requests'][0]['type']=='repair_strategy_required'


def test_corrupt_queue_preserves_original(tmp_path):
    from src.core.job_queue import JobQueue
    path=tmp_path/'jobs.json';path.write_bytes(b'{broken')
    with pytest.raises(RuntimeError,match='recovery required'):JobQueue(path,{})
    assert path.read_bytes()==b'{broken'


def test_explicit_video_pointer_never_falls_back(tmp_path):
    from src.storyboard.continuity_service import resolve_final_video_file
    old=tmp_path/'legacy.mp4';old.write_bytes(b'old')
    shot=Shot(shot_id='s',scene_id='s',index=1,final_video='missing',video=VideoAsset(local=str(old)))
    assert resolve_final_video_file(shot) is None


@pytest.mark.parametrize('field,value',[('scene_reference_mode','bad'),('human_reference_mode','bad')])
def test_first_frame_enum_rejects_before_queue(field,value):
    from routers.shots import FirstFrameRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):FirstFrameRequest(**{field:value})


@pytest.fixture
def video_env(tmp_path,monkeypatch):
    import deps
    from tests.test_p55_recovery import FakeAgnes,FakeRehost,_FakePM,_save_proj,_mark_ff_ready
    fake=FakeAgnes();_save_proj(tmp_path);_mark_ff_ready(tmp_path,'s01_c01')
    monkeypatch.setattr(deps,'agnes_client',fake)
    monkeypatch.setattr(deps,'_rehost_host',FakeRehost())
    monkeypatch.setattr(deps,'project_manager',_FakePM(tmp_path))
    return fake,ProjectStore(tmp_path)


async def silent_emit(*a,**kw):pass


def video_job():
    return SimpleNamespace(project='_test_p55_recovery',shot_id='s01_c01',job_id='j',params={})


def test_real_handler_immutable_and_duplicate_keeps_selection(video_env):
    from routers.shots import _handler_video
    import hashlib
    fake,store=video_env
    asyncio.run(_handler_video(video_job(),silent_emit))
    first=store.load().get_shot('s01_c01').video_versions[0]
    checksum=hashlib.sha256(Path(first.local).read_bytes()).hexdigest()
    fake.video_id='vid_2'
    asyncio.run(_handler_video(video_job(),silent_emit))
    shot=store.load().get_shot('s01_c01');second=shot.video_versions[-1]
    assert first.local!=second.local and shot.final_video==second.version_id
    assert hashlib.sha256(Path(first.local).read_bytes()).hexdigest()==checksum
    fake.video_id=first.video_id
    asyncio.run(_handler_video(video_job(),silent_emit))
    shot=store.load().get_shot('s01_c01')
    assert len(shot.video_versions)==2 and shot.final_video==second.version_id
    assert shot.video.local==second.local and shot.video.status=='done'


def test_real_handler_input_changes_during_download_not_attached(video_env):
    from routers.shots import _handler_video
    fake,store=video_env;download=fake.download
    async def race(url,dest):
        await download(url,dest)
        store.update_shot('s01_c01',lambda s:setattr(s,'description','changed during download'))
    fake.download=race;job=video_job()
    with pytest.raises(RuntimeError,match='inputs changed'):
        asyncio.run(_handler_video(job,silent_emit))
    assert store.load().get_shot('s01_c01').video_versions==[]
    assert Path(job.params['unattached_artifact']['local']).exists()


def test_unknown_submit_is_durable_and_new_job_does_not_post(video_env):
    from routers.shots import _handler_video
    import httpx
    fake,store=video_env;calls=[]
    async def lost(*a,**kw):calls.append(1);raise httpx.ReadError('accepted, response lost')
    fake.create_video=lost;job=video_job()
    with pytest.raises(httpx.ReadError):asyncio.run(_handler_video(job,silent_emit))
    shot=store.load().get_shot('s01_c01')
    assert shot.video.status=='generating' and not shot.video.video_id
    assert job.params['submission_intent_id'] and job.params['provider_submit_state']=='unknown'
    with pytest.raises(RuntimeError,match='待核验'):
        asyncio.run(_handler_video(video_job(),silent_emit))
    assert len(calls)==1


@pytest.mark.parametrize('fault',['transport','server','invalid_receipt'])
def test_client_never_reposts_ambiguous_video(fault):
    import httpx
    from contextlib import asynccontextmanager
    from src.api.agnes_client import AgnesClient,VideoSubmissionUncertain
    from tests.test_p55_recovery import _FakeQuota
    calls=[]
    def respond(request):
        calls.append(request)
        if fault=='transport':raise httpx.ReadError('lost response',request=request)
        if fault=='server':return httpx.Response(503,text='unknown')
        return httpx.Response(200,text='not json')
    client=AgnesClient({'base_url':'https://fake.invalid','api_key':'dummy',
        'models':{'video':'agnes-video-2.5-flash'}},quota=_FakeQuota(),transport=httpx.MockTransport(respond))
    @asynccontextmanager
    async def slot(bucket):yield
    client.limiter=SimpleNamespace(slot=slot)
    async def scenario():
        try:
            with pytest.raises(VideoSubmissionUncertain):
                await client.create_video('p',image_url='https://fake.invalid/f.png')
        finally:await client.aclose()
    asyncio.run(scenario());assert len(calls)==1


def test_cancel_worker_before_handler_requeues_claim(tmp_path):
    from src.core.job_queue import JobQueue
    async def scenario():
        async def handler(*a):await asyncio.sleep(10)
        q=JobQueue(tmp_path/'jobs.json',{'video':handler})
        # Avoid submit's detached broadcast so this window is controlled.
        from src.core.job_queue import Job
        job=Job('j','p','video');q.jobs['j']=job;q._video_q.put_nowait('j')
        async def broadcast(_):asyncio.current_task().cancel();await asyncio.sleep(0)
        q._broadcast=broadcast
        worker=asyncio.create_task(q._worker(q._video_q,'video-worker-test'))
        await asyncio.gather(worker,return_exceptions=True)
        assert not q._running_tasks
        recovered=JobQueue(tmp_path/'jobs.json',{'video':handler})
        assert recovered.get('j').state=='queued'
    asyncio.run(scenario())


def test_live_review_overlaps_execution_and_final_reuses_receipt(tmp_path,monkeypatch):
    make_project(tmp_path);rt=mock_review(monkeypatch);calls=[]
    async def scenario():
        entered=asyncio.Event();release=asyncio.Event()
        async def vision(*a,**kw):
            calls.append(kw['shot'].shot_id);entered.set();await release.wait()
            return {'review':{'dimensions':{d:{'status':'pass'} for d in rp.VIDEO_HARD_DIMS}},'derived':{'overall':'pass'}}
        monkeypatch.setattr(rp,'review_shot_core',vision)
        live=rp.LiveReviewSession(rt,tmp_path,'p',interval_s=.01);live.start()
        await asyncio.wait_for(entered.wait(),1)
        # Producer/executor can advance while visual inference is in flight.
        progress=[]
        async def production():progress.append('independent task advanced')
        await production();assert progress and any(not t.done() for t in live.tasks)
        release.set();await live.close()
        assert len(calls)==3 and live.cache['s01_c01']['overall']=='pass'
        final=await rp.run_independent_review(rt,tmp_path,'p',cached_results=live.cache)
        assert len(calls)==3 and final['machine_passed'] and final['results'][0]['cache_reused']
    asyncio.run(scenario())


def test_review_cache_not_reused_after_input_change(tmp_path,monkeypatch):
    store=make_project(tmp_path);rt=mock_review(monkeypatch)
    first=asyncio.run(rp.run_independent_review(rt,tmp_path,'p'))
    entry=first['results'][0];entry['review_policy_version']=rp.REVIEW_POLICY_VERSION
    store.update_shot('s01_c01',lambda s:setattr(s,'description','new required action'))
    calls=[]
    async def vision(*a,**kw):
        calls.append(1)
        return {'review':{'dimensions':{d:{'status':'pass'} for d in rp.VIDEO_HARD_DIMS}},'derived':{'overall':'pass'}}
    monkeypatch.setattr(rp,'review_shot_core',vision)
    final=asyncio.run(rp.run_independent_review(rt,tmp_path,'p',cached_results={'s01_c01':entry}))
    assert len(calls)==3 and not final['results'][0].get('cache_reused')


def test_missing_project_read_never_creates_a_ghost_project(tmp_path):
    root=tmp_path/'not-created';store=ProjectStore(root)
    with pytest.raises(FileNotFoundError):store.load()
    with pytest.raises(FileNotFoundError):store.update_shot('missing',lambda s:None)
    assert not root.exists()


def test_dialogue_annotations_compile_without_fuzzy_cast_changes(tmp_path):
    from src.generation.dialogue_control import resolve_native_dialogue,DialogueControlError
    shot=Shot(shot_id='s',scene_id='s',index=1,characters=['安保负责人','博士'],
              dialogue='安保负责人（右侧，低声）：立即封锁大门。')
    original=shot.dialogue
    result=resolve_native_dialogue(tmp_path,shot,language='Mandarin Chinese')
    assert result.lines[0].speaker_character_name=='安保负责人'
    assert result.lines[0].text=='立即封锁大门。'
    assert result.lines[0].delivery_hints==('右侧，低声',) and shot.dialogue==original
    shot.dialogue='安保负责（右侧）：封门。'
    with pytest.raises(DialogueControlError,match='speaker_not_in_characters'):
        resolve_native_dialogue(tmp_path,shot,language='Mandarin Chinese')


def test_explicit_silent_marker_is_not_spoken(tmp_path):
    from src.generation.dialogue_control import resolve_native_dialogue
    shot=Shot(shot_id='s',scene_id='s',index=1,characters=['博士'],dialogue='本镜无对白（仅警报声）')
    assert resolve_native_dialogue(tmp_path,shot,language='Mandarin Chinese') is None
    shot.dialogue='博士：本镜无对白'
    assert resolve_native_dialogue(tmp_path,shot,language='Mandarin Chinese').lines[0].text=='本镜无对白'


def test_dialogue_batch_reports_all_invalid_shots_without_mutation(tmp_path):
    from src.generation.dialogue_control import diagnose_dialogue_batch
    store=make_project(tmp_path,2);project=store.load();project.native_av.spoken_language='Mandarin Chinese'
    for shot in project.all_shots():shot.characters=['博士'];shot.dialogue='陌生人：开门。'
    errors=diagnose_dialogue_batch(tmp_path,project,['s01_c01','s01_c02'])
    assert set(errors)=={'s01_c01','s01_c02'}
    assert all(e['code']=='speaker_not_in_characters' for e in errors.values())
    assert all(s.dialogue=='陌生人：开门。' for s in project.all_shots())


def test_review_final_read_failure_invalidates_completed_verdict(tmp_path,monkeypatch):
    make_project(tmp_path);rt=mock_review(monkeypatch)
    original=ProjectStore.load;calls=0
    def load(self):
        nonlocal calls
        calls+=1
        if calls==2:raise OSError('project unreadable after visual verdict')
        return original(self)
    monkeypatch.setattr(ProjectStore,'load',load)
    rv=asyncio.run(rp.run_independent_review(rt,tmp_path,'p'))
    assert not rv['machine_passed'] and rv['incomplete_shots']==['s01_c01']
    assert 'final_validation' in rv['results'][0]['error']


def test_review_cache_revalidated_against_latest_project(tmp_path,monkeypatch):
    store=make_project(tmp_path);rt=mock_review(monkeypatch)
    first=asyncio.run(rp.run_independent_review(rt,tmp_path,'p'))
    cached=first['results'][0];cached['review_policy_version']=rp.REVIEW_POLICY_VERSION
    original=ProjectStore.load;changed=False
    def load(self):
        nonlocal changed
        snapshot=original(self)
        if not changed:
            changed=True
            store.update_shot('s01_c01',lambda s:setattr(s,'description','changed after snapshot'))
        return snapshot
    monkeypatch.setattr(ProjectStore,'load',load)
    rv=asyncio.run(rp.run_independent_review(rt,tmp_path,'p',cached_results={'s01_c01':cached}))
    assert rv['results'][0]['cache_reused'] and rv['results'][0]['overall']=='stale'
    assert not rv['machine_passed']


def test_review_early_shot_revalidated_after_other_shot_finishes(tmp_path,monkeypatch):
    store=make_project(tmp_path,2);rt=mock_review(monkeypatch)
    async def scenario():
        first_finished=asyncio.Event();calls={}
        async def vision(*a,**kw):
            sid=kw['shot'].shot_id;calls[sid]=calls.get(sid,0)+1
            if sid=='s01_c01' and calls[sid]==3:first_finished.set()
            if sid=='s01_c02' and calls[sid]==1:
                await first_finished.wait()
                store.update_shot('s01_c01',lambda s:setattr(s,'description','edited while other review pending'))
            return {'review':{'dimensions':{d:{'status':'pass'} for d in rp.VIDEO_HARD_DIMS}},'derived':{'overall':'pass'}}
        monkeypatch.setattr(rp,'review_shot_core',vision)
        return await rp.run_independent_review(rt,tmp_path,'p')
    rv=asyncio.run(scenario())
    assert [r['overall'] for r in rv['results']]==['stale','pass'] and not rv['machine_passed']


def test_input_edit_before_submit_rejects_stale_compiled_request(video_env):
    from routers.shots import _handler_video
    fake,store=video_env;changed=False
    async def emit(stage,*a):
        nonlocal changed
        if stage=='submit' and not changed:
            changed=True
            store.update_shot('s01_c01',lambda s:setattr(s,'description','changed immediately before POST'))
    with pytest.raises(RuntimeError,match='before submit'):
        asyncio.run(_handler_video(video_job(),emit))
    assert 'create_video' not in fake.calls
    assert store.load().get_shot('s01_c01').video.status!='generating'


def add_selected_alternative(store):
    def mutate(s):
        s.video_versions.append(VideoVersion(version_id='user_chosen',status='done',
            local='user.mp4',url='https://fake.invalid/user.mp4',video_id='user_provider'))
        s.final_video='user_chosen';s.video.mark_done('user.mp4','https://fake.invalid/user.mp4')
        s.video.video_id='user_provider'
    store.update_shot('s01_c01',mutate)


def test_user_selection_during_download_is_not_overwritten(video_env):
    from routers.shots import _handler_video
    fake,store=video_env;download=fake.download;job=video_job()
    async def race(url,dest):
        await download(url,dest);add_selected_alternative(store)
    fake.download=race
    with pytest.raises(RuntimeError,match='selection changed'):
        asyncio.run(_handler_video(job,silent_emit))
    shot=store.load().get_shot('s01_c01')
    assert shot.final_video=='user_chosen' and shot.video.video_id=='user_provider'
    assert len(shot.video_versions)==1 and Path(job.params['unattached_artifact']['local']).exists()


def test_user_selection_during_submit_keeps_known_receipt_without_overwrite(video_env):
    from routers.shots import _handler_video
    fake,store=video_env;create=fake.create_video;job=video_job();durable=[]
    async def race(*a,**kw):
        result=await create(*a,**kw);add_selected_alternative(store);return result
    async def emit(stage,*a):
        if stage=='provider_receipt':durable.append(dict(job.params))
    fake.create_video=race
    with pytest.raises(RuntimeError,match='receipt retained unattached'):
        asyncio.run(_handler_video(job,emit))
    shot=store.load().get_shot('s01_c01')
    assert shot.final_video=='user_chosen' and shot.video.video_id=='user_provider' and shot.video.status=='done'
    assert durable[0]['provider_task_id']==fake.video_id
    assert job.params['unattached_provider_receipt']['provider_task_id']==fake.video_id
    assert fake.calls==['create_video']


@pytest.mark.parametrize('settled_state',['failed','cancelled','detached'])
def test_recovery_preserves_basis_and_does_not_reattach_rejected_results(tmp_path,settled_state):
    from services.lifecycle import recover_pending_videos
    from src.core.job_queue import JobQueue,Job
    from tests.test_p55_recovery import FakeAgnes
    pdir=tmp_path/'projects'/'p';pdir.mkdir(parents=True);store=make_project(pdir)
    store.update_shot('s01_c01',lambda s:(setattr(s.video,'status','generating'),setattr(s.video,'video_id','accepted')))
    q=JobQueue(tmp_path/'jobs.json',{'video':silent_emit})
    original=Job('original','p','video',shot_id='s01_c01',state='failed',created_at=1,
        params={'provider_task_id':'accepted','dispatch_input_signature':'original_basis',
                'origin_run_id':'parent_a'})
    if settled_state=='cancelled':original.state='cancelled'
    if settled_state=='detached':original.params['unattached_artifact']={'local':'retained.mp4'}
    newer=Job('newer','p','video',shot_id='s01_c01',state='queued',created_at=2,params={'origin_run_id':'parent_b'})
    q.jobs={'original':original,'newer':newer};q._save()
    asyncio.run(recover_pending_videos(queue=q,client=FakeAgnes(),projects_root=pdir.parent,handlers={'video':silent_emit}))
    assert len(q.jobs)==2 and newer.params=={'origin_run_id':'parent_b'}
    assert original.params['dispatch_input_signature']=='original_basis' and original.params['origin_run_id']=='parent_a'
    if settled_state=='failed':
        assert original.state=='queued' and q._video_q.get_nowait()=='original'
    else:
        assert q._video_q.empty() and original.state==('cancelled' if settled_state=='cancelled' else 'failed')


def test_legacy_recovery_selection_change_does_not_relabel_current_video(tmp_path):
    from services.lifecycle import recover_pending_videos
    from tests.test_p55_recovery import FakeAgnes
    pdir=tmp_path/'projects'/'p';pdir.mkdir(parents=True);store=make_project(pdir)
    store.update_shot('s01_c01',lambda s:(setattr(s.video,'status','generating'),setattr(s.video,'video_id','accepted')))
    fake=FakeAgnes();download=fake.download
    async def race(url,dest):
        await download(url,dest);add_selected_alternative(store)
    fake.download=race
    asyncio.run(recover_pending_videos(client=fake,projects_root=pdir.parent,handlers={}))
    shot=store.load().get_shot('s01_c01')
    assert shot.final_video=='user_chosen' and shot.video.status=='done' and shot.video.video_id=='user_provider'


@pytest.mark.parametrize('failure_stage',['submission','poll'])
def test_late_provider_failure_cannot_mark_user_selected_video_failed(video_env,failure_stage):
    from routers.shots import _handler_video
    from src.api.agnes_client import AgnesError
    fake,store=video_env
    async def failure(*a,**kw):
        add_selected_alternative(store)
        raise AgnesError('explicit provider rejection',status=400)
    if failure_stage=='submission':fake.create_video=failure
    else:fake.poll_video=failure
    with pytest.raises(AgnesError):asyncio.run(_handler_video(video_job(),silent_emit))
    shot=store.load().get_shot('s01_c01')
    assert shot.final_video=='user_chosen' and shot.video.video_id=='user_provider' and shot.video.status=='done'
