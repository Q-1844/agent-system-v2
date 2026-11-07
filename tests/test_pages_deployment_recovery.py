"""A failed Pages deployment must heal or retain recoverable paid work."""
import asyncio
import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.image_host import GithubPagesHost
from src.core.models import Asset, Project, Scene, Shot, VideoAsset, VideoVersion
from src.core.store import ProjectStore
from src.core.media_publication import (record_publication_wait, pending_publication,
    publication_wait_path, resolve_publication_wait)


class Pages:
    def __init__(self, *, status='errored', branch='main', build_type='legacy', post=201, marker=403, lose_marker_response=False, source_path='/'):
        self.status,self.branch,self.build_type,self.post=status,branch,build_type,post
        self.source_path=source_path
        self.requests=[];self.published=False;self.created='2026-11-06T20:26:07Z'
        self.marker,self.lose_marker_response=marker,lose_marker_response
        self.markers={};self.marker_commits=0
    async def respond(self,request):
        self.requests.append(request)
        if request.url.host=='u.github.io':
            assert 'authorization' not in request.headers
            return httpx.Response(200 if self.published else 404,content=b'image')
        assert request.headers['authorization']=='Bearer test'
        if '/contents/recovery/' in request.url.path:
            if request.method=='GET':
                return httpx.Response(200,content=self.markers[request.url.path]) if request.url.path in self.markers else httpx.Response(404)
            if request.url.path in self.markers:return httpx.Response(409)
            if self.marker==201:
                self.markers[request.url.path]=base64.b64decode(json.loads(request.content)['content'])
                self.marker_commits+=1;self.published=True
                if self.lose_marker_response:
                    self.lose_marker_response=False;raise httpx.ReadTimeout('commit response lost')
            return httpx.Response(self.marker)
        if request.url.path.endswith('/builds/latest'):
            return httpx.Response(200,json={'status':self.status,'commit':'source',
                'created_at':self.created,'updated_at':self.created})
        if request.method=='POST':
            if self.post=='timeout':raise httpx.ReadTimeout('response lost')
            if self.post==201:self.published=True
            return httpx.Response(self.post,json={'status':'queued'})
        return httpx.Response(200,json={'build_type':self.build_type,'source':{'branch':self.branch,'path':self.source_path}})
    def host(self,**kw):
        return GithubPagesHost('u','repo','main','test',poll_timeout=.03,poll_interval=.001,
            repair_after=0,repair_interval=0,client_factory=lambda **_:httpx.AsyncClient(
                transport=httpx.MockTransport(self.respond)),**kw)
    def posts(self):return [r for r in self.requests if r.method=='POST']


def test_failed_deployment_rebuilds_and_verifies_public_url():
    pages=Pages();host=pages.host();url=host._pages_url('objects/paid.jpg')
    asyncio.run(host.ensure_published(url))
    assert len(pages.posts())==1
    assert pages.requests[-1].url==url and pages.published
    assert not any(r.method=='PUT' for r in pages.requests)


@pytest.mark.parametrize('changes',[{'status':'building'},{'status':'queued'},
    {'status':'built'},{'branch':'another'},{'build_type':'workflow'},{'source_path':'/docs'}])
def test_repair_cannot_restart_active_or_unrelated_pages(changes):
    pages=Pages(**changes)
    asyncio.run(pages.host()._repair_failed_deployment())
    assert pages.posts()==[]


@pytest.mark.parametrize('post',[403,500,'timeout'])
def test_unconfirmed_repair_never_means_ready_and_does_not_repeat(post):
    pages=Pages(post=post);host=pages.host()
    with pytest.raises(GithubPagesHost.PagesNotReadyError):
        asyncio.run(host.ensure_published(host._pages_url('objects/paid.jpg')))
    assert len(pages.posts())==1 and not pages.published


def test_parallel_waiters_share_one_rebuild_for_one_failed_deployment():
    pages=Pages(post='timeout');host=pages.host()
    async def repair():await asyncio.gather(*(host._repair_failed_deployment() for _ in range(5)))
    asyncio.run(repair())
    assert len(pages.posts())==1
    pages.created='2026-11-07T01:00:00Z'
    asyncio.run(host._repair_failed_deployment())
    assert len(pages.posts())==2  # a different failed deployment may be repaired


@pytest.mark.parametrize('failure',['forbidden','malformed','transport'])
def test_unavailable_control_api_keeps_waiting_without_writes(failure):
    pages=Pages();original=pages.respond
    async def broken(request):
        if request.url.path.endswith('/builds/latest'):
            if failure=='forbidden':return httpx.Response(403)
            if failure=='malformed':return httpx.Response(200,json=[])
            raise httpx.ConnectTimeout('control API offline')
        return await original(request)
    host=GithubPagesHost('u','repo','main','test',client_factory=lambda **_:httpx.AsyncClient(
        transport=httpx.MockTransport(broken)))
    asyncio.run(host._repair_failed_deployment())
    assert not pages.posts()


def chain(tmp_path):
    video=tmp_path/'video.mp4';video.write_bytes(b'already-paid')
    tail=tmp_path/'tail.jpg';tail.write_bytes(b'extracted-tail')
    shot=Shot(shot_id='s01_c01',scene_id='s01',index=1,final_video='v1',
        video=VideoAsset(local=str(video),status='done',video_id='paid'),
        video_versions=[VideoVersion(version_id='v1',video_id='paid',local=str(video),status='done')],
        video_end_source_version='v1',video_end_frame=Asset(local=str(tail),status='done',
            error='deployment timed out'))
    ProjectStore(tmp_path).save(Project(name='film',scenes=[Scene(scene_id='s01',shots=[shot])]))
    record_publication_wait(tmp_path,shot.shot_id,'v1',str(tail),'deployment timed out')
    docs={
      'output/full_runs/full/run.json':{'run_id':'full','project_name':'film','status':'failed','production_run_id':'production'},
      'output/production_runs/production/run.json':{'run_id':'production','parent_full_run_id':'full','status':'failed',
        'scenes':[{'scene_id':'s01','status':'failed','child_run_id':'child'}]},
      'output/ai/autopilot_runs/child.json':{'run_id':'child','parent_project_run_id':'production','status':'failed',
        'shots':[{'shot_id':'s01_c01','status':'failed','stage':'video_end','video_version_id':'v1'}]}}
    for name,doc in docs.items():
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(doc))
    cp=SimpleNamespace(doc={'nonce':'unit','request_sha256':'digest','full_run_id':'full',
        'github_run_id':'12','phase':'FAILED','film_target_seconds':'auto',
        'video_attempts':[{'state':'ACCEPTED','video_id':'paid','seconds':8}]})
    wrapper=SimpleNamespace(project_dir=tmp_path,checkpoint=cp,quota_gate_after=None,
        video_attempts=cp.doc['video_attempts'],film_target_seconds='auto')
    async def persist(**fields):cp.doc.update(copy.deepcopy(fields))
    wrapper.persist=persist
    return wrapper,docs


def test_publication_wait_survives_native_project_roundtrip_without_changing_assets(tmp_path):
    wrapper,_=chain(tmp_path)
    asset=ProjectStore(tmp_path).load().get_shot('s01_c01').video_end_frame
    assert pending_publication(tmp_path,'s01_c01','v1',asset.local)
    assert set(Asset.__dataclass_fields__)=={'local','url','status','error','updated_at'}
    from tools.full_project_cloud_acceptance import production_wait_reason
    assert production_wait_reason(wrapper,SimpleNamespace(jobs={}), 'film')=='asset_publication'


@pytest.mark.parametrize('change',['old_run','wrong_parent','wrong_version','content','completed','missing_child','mixed_failure'])
def test_stale_or_content_failures_cannot_be_automatically_reclassified(tmp_path,change):
    wrapper,docs=chain(tmp_path)
    if change=='old_run':wrapper.checkpoint.doc['full_run_id']='other'
    elif change=='wrong_parent':docs['output/ai/autopilot_runs/child.json']['parent_project_run_id']='other'
    elif change=='wrong_version':docs['output/ai/autopilot_runs/child.json']['shots'][0]['video_version_id']='other'
    elif change=='content':
        path=publication_wait_path(tmp_path,'s01_c01','v1');doc=json.loads(path.read_text());doc['category']='CONTENT_REJECTED';path.write_text(json.dumps(doc))
    elif change=='completed':docs['output/production_runs/production/run.json']['scenes'][0]['status']='completed'
    elif change=='missing_child':docs['output/production_runs/production/run.json']['scenes'][0]['child_run_id']='missing'
    else:docs['output/ai/autopilot_runs/child.json']['shots'].append({'shot_id':'unknown','status':'failed','stage':'video'})
    for p,doc in docs.items():(tmp_path/p).write_text(json.dumps(doc))
    from tools.full_project_cloud_acceptance import production_wait_reason
    assert production_wait_reason(wrapper,SimpleNamespace(jobs={}), 'film') is None


def test_real_drain_checkpoint_wakeup_and_auto_dispatch_preserve_paid_video(tmp_path,monkeypatch):
    from src.core.job_queue import JobQueue
    from tools import resume_dispatch as dispatch
    from tools.full_project_cloud_acceptance import (production_wait_reason,record_technical_wait,auto_resume_next_hop)
    from tools.quota_wakeup import armable
    wrapper,_=chain(tmp_path);before=(tmp_path/'video.mp4').read_bytes()
    full=SimpleNamespace(run_id='full',status='failed',error='publication pending')
    manager=SimpleNamespace(get_run=lambda *a:full,pause_children_for_drain=AsyncMock())
    queue=JobQueue(tmp_path/'jobs.json',{},require_ownership=False)
    dispatched=[]
    monkeypatch.setenv('RESUME_DISPATCH_TOKEN','test');monkeypatch.setenv('GITHUB_RUN_ID','12')
    monkeypatch.setenv('GITHUB_REF_NAME','main')
    monkeypatch.setattr(dispatch,'dispatch_next_hop',lambda *a,**kw:dispatched.append(kw) or
        {'dispatched':True,'status':204,'inputs':{'resume_run_id':kw['resume_run_id'],'task_nonce':kw['task_nonce']}})
    async def run():
        reason=production_wait_reason(wrapper,queue,'film')
        assert await record_technical_wait(manager,'film',full,wrapper,queue,reason)
        assert wrapper.checkpoint.doc['phase']=='INTERRUPTED'
        assert wrapper.checkpoint.doc['terminal_status']=='technical_wait'
        assert wrapper.checkpoint.doc['drain_complete'] is True and armable(wrapper.checkpoint.doc)
        await auto_resume_next_hop(wrapper,'unit',330)
    asyncio.run(run())
    assert len(dispatched)==1 and dispatched[0]['resume_run_id']=='12'
    assert wrapper.video_attempts==[{'state':'ACCEPTED','video_id':'paid','seconds':8}]
    assert (tmp_path/'video.mp4').read_bytes()==before and queue.jobs=={}


def test_pre_submit_pages_failure_is_infrastructure_and_finished_retry_clears_it():
    from tools.full_project_cloud_acceptance import production_wait_reason
    old=SimpleNamespace(project='film',kind='video',shot_id='s',state='failed',params={},
        error_class='src.core.image_host.GithubPagesHost.PagesNotReadyError',created_at=1,finished_at=2)
    wrapper=SimpleNamespace(quota_gate_after=None)
    assert production_wait_reason(wrapper,SimpleNamespace(jobs={'old':old}),'film')=='asset_publication'
    done=SimpleNamespace(project='film',kind='video',shot_id='s',state='done',params={},created_at=3,finished_at=4)
    assert production_wait_reason(wrapper,SimpleNamespace(jobs={'old':old,'done':done}),'film') is None


@pytest.mark.parametrize('transient',[True,False])
def test_real_extraction_marks_only_pages_timeout_as_publication_wait(tmp_path,monkeypatch,transient):
    import routers.shots as routes
    from PIL import Image
    def extract(cmd,**kw):
        Image.new('RGB',(16,16),'navy').save(cmd[-1]);return SimpleNamespace(returncode=0)
    class Host:
        async def upload(self,path):
            raise (GithubPagesHost.PagesNotReadyError('Pages timed out') if transient
                   else PermissionError('credentials invalid'))
    monkeypatch.setattr('subprocess.run',extract)
    monkeypatch.setattr(routes,'get_rehost_host',Host)
    asset,ok=asyncio.run(routes._extract_video_end_frame(tmp_path,'s01_c01','paid.mp4','v1'))
    assert not ok and Path(asset.local).is_file()
    assert pending_publication(tmp_path,'s01_c01','v1',asset.local) is transient


def test_retry_exhaustion_retains_typed_wait_and_existing_video(tmp_path,monkeypatch):
    import routers.shots as routes
    import src.ai.autopilot as ap
    wrapper,_=chain(tmp_path);store=ProjectStore(tmp_path);calls=[];sleeps=[]
    state=ap.ShotExecutionState(shot_id='s01_c01',index=1,video_version_id='v1')
    run=SimpleNamespace(shots=[state,ap.ShotExecutionState(shot_id='s01_c02',index=2)])
    manager=ap.SceneAutopilotManager()
    monkeypatch.setattr(ap,'_planned_for',lambda *a:SimpleNamespace(requires_previous_video_end=True))
    monkeypatch.setattr(manager,'_save_shot_state',AsyncMock())
    async def extract(*a,**kw):
        calls.append(kw)
        record_publication_wait(tmp_path,'s01_c01','v1',str(tmp_path/'tail.jpg'),'Pages failed')
        return Asset(local=str(tmp_path/'tail.jpg'),status='done',error='Pages failed'),False
    async def sleep(seconds):sleeps.append(seconds)
    monkeypatch.setattr(routes,'_extract_video_end_frame',extract)
    monkeypatch.setattr(ap.asyncio,'sleep',sleep)
    with pytest.raises(RuntimeError,match='Pages failed'):
        asyncio.run(manager._do_video_end('film',run,state,store,tmp_path))
    assert len(calls)==4 and sleeps==[120,120,1200]
    from tools.full_project_cloud_acceptance import production_wait_reason
    assert production_wait_reason(wrapper,SimpleNamespace(jobs={}), 'film')=='asset_publication'
    assert (tmp_path/'video.mp4').read_bytes()==b'already-paid'


def test_missing_paid_file_cannot_enter_publication_resume(tmp_path):
    wrapper,_=chain(tmp_path);(tmp_path/'video.mp4').unlink()
    from tools.full_project_cloud_acceptance import production_wait_reason
    assert production_wait_reason(wrapper,SimpleNamespace(jobs={}), 'film') is None


def test_publication_diagnostics_cannot_invalidate_creative_input_signature(tmp_path):
    from src.generation.video_commit import video_input_signature
    from src.core.models import ImageAsset,Character
    wrapper,_=chain(tmp_path);store=ProjectStore(tmp_path);project=store.load()
    previous=project.get_shot('s01_c01')
    shot=Shot(shot_id='s01_c02',scene_id='s01',index=2,opening_source='inherit_previous',
        source_shot_id=previous.shot_id,characters=['hero'],first_frame=ImageAsset(
            candidates=[Asset(url='https://frame.invalid/a',status='done')],status='done'))
    project.characters=[Character(name='hero',views={'front':Asset(url='https://frame.invalid/hero',status='done')})]
    project.scenes[0].shots.append(shot)
    before=video_input_signature(project,shot)
    project_bytes=(tmp_path/'output/project.json').read_bytes()
    record_publication_wait(tmp_path,'s01_c01','v1',str(tmp_path/'tail.jpg'),'changed diagnostic')
    assert (tmp_path/'output/project.json').read_bytes()==project_bytes
    assert video_input_signature(project,shot)==before
    previous.video_end_frame.url='https://frame.invalid/new-actual-image'
    assert video_input_signature(project,shot)!=before
    shot.performance_intent={'error_code':'creative-cue-one'}
    changed=video_input_signature(project,shot)
    shot.performance_intent['error_code']='creative-cue-two'
    assert video_input_signature(project,shot)!=changed


@pytest.mark.parametrize('change',['hash','version','category','escape','changed_bytes','malformed'])
def test_publication_ledger_rejects_tampered_or_unrelated_evidence(tmp_path,change):
    wrapper,_=chain(tmp_path);path=publication_wait_path(tmp_path,'s01_c01','v1')
    doc=json.loads(path.read_text())
    if change=='hash':doc['sha256']='wrong'
    elif change=='version':doc['video_version_id']='other'
    elif change=='category':doc['category']='CONTENT_REJECTED'
    elif change=='escape':doc['local']='../../outside.jpg'
    elif change=='changed_bytes':(tmp_path/'tail.jpg').write_bytes(b'new-frame')
    elif change=='malformed':doc=[]
    path.write_text(json.dumps(doc))
    assert not pending_publication(tmp_path,'s01_c01','v1',str(tmp_path/'tail.jpg'))


def test_resolved_publication_wait_does_not_clear_other_video_versions(tmp_path):
    wrapper,_=chain(tmp_path)
    record_publication_wait(tmp_path,'s01_c01','v2',str(tmp_path/'tail.jpg'),'new version waiting')
    resolve_publication_wait(tmp_path,'s01_c01','v1')
    assert not pending_publication(tmp_path,'s01_c01','v1',str(tmp_path/'tail.jpg'))
    assert pending_publication(tmp_path,'s01_c01','v2',str(tmp_path/'tail.jpg'))


def test_contents_permission_recovers_when_pages_rebuild_api_is_forbidden():
    pages=Pages(post=403,marker=201);host=pages.host()
    asyncio.run(host.ensure_published(host._pages_url('objects/paid.jpg')))
    assert len(pages.posts())==1 and pages.marker_commits==1 and pages.published
    writes=[r for r in pages.requests if r.method=='PUT']
    assert len(writes)==1 and '/contents/recovery/pages-' in writes[0].url.path
    body=json.loads(writes[0].content);assert body['branch']=='main' and 'sha' not in body
    marker=json.loads(base64.b64decode(body['content']))
    assert marker['commit']=='source' and marker['reason']=='retry_pages_publication'


def test_marker_is_remote_idempotent_across_separate_host_instances():
    pages=Pages(post=403,marker=201)
    async def repair():await asyncio.gather(pages.host()._repair_failed_deployment(),pages.host()._repair_failed_deployment())
    asyncio.run(repair())
    assert pages.marker_commits==1 and len(pages.markers)==1


def test_lost_marker_response_does_not_create_a_second_commit(monkeypatch):
    pages=Pages(post=403,marker=201,lose_marker_response=True)
    monkeypatch.setattr(asyncio,'sleep',AsyncMock())
    asyncio.run(pages.host()._repair_failed_deployment())
    assert pages.marker_commits==1
    assert len([r for r in pages.requests if r.method=='PUT'])==2


def test_probe_identity_requires_a_fresh_permission_write():
    pages=Pages(post=403,marker=201);host=pages.host()
    build={'commit':'source','created_at':pages.created,'updated_at':pages.created}
    async def probe():
        assert await host.request_rebuild(build,probe_id='12-1')=='contents_commit'
        assert await host.request_rebuild(build,probe_id='12-1')=='contents_existing'
        assert await host.request_rebuild(build,probe_id='12-2')=='contents_commit'
    asyncio.run(probe())
    assert pages.marker_commits==2
