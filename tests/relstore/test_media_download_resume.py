"""Real failure regression: a CDN GET must not trigger another paid generation."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tests.mock_agnes import MockAgnes, make_agnes
from src.core.models import Project, Scene, Shot, Asset, ImageAsset
from src.core.store import ProjectStore
from tools.full_project_resume import restore_snapshot


@pytest.mark.parametrize('failure', ['connect', 'status', 'partial'])
def test_download_retries_get_atomically_without_auth(tmp_path, monkeypatch, failure):
    from src.api import agnes_client as ac
    calls = []
    async def no_sleep(_): pass
    monkeypatch.setattr(ac.asyncio, 'sleep', no_sleep)
    class Partial(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'partial'
            raise httpx.ReadError('stream broke')
    dest = tmp_path/'image.png';dest.write_bytes(b'old-valid')
    def handler(request):
        assert 'authorization' not in request.headers and request.method=='GET'
        calls.append(request)
        assert dest.read_bytes()==b'old-valid'
        if len(calls)==1:
            if failure=='connect':raise httpx.ConnectTimeout('TLS timed out',request=request)
            if failure=='status':return httpx.Response(503)
            return httpx.Response(200,stream=Partial())
        return httpx.Response(200,content=b'complete-image')
    agnes=make_agnes(MockAgnes());agnes.transport=httpx.MockTransport(handler)
    asyncio.run(agnes.download('https://cdn.invalid/signed.png',dest))
    assert len(calls)==2 and dest.read_bytes()==b'complete-image'
    assert not list(tmp_path.glob('*.part'))


@pytest.mark.parametrize('status,expected', [(404,1),(503,4)])
def test_download_failure_preserves_old_file(tmp_path,monkeypatch,status,expected):
    from src.api import agnes_client as ac
    async def no_sleep(_):pass
    monkeypatch.setattr(ac.asyncio,'sleep',no_sleep)
    calls=[]
    def handler(request):calls.append(request);return httpx.Response(status)
    agnes=make_agnes(MockAgnes());agnes.transport=httpx.MockTransport(handler)
    dest=tmp_path/'old.png';dest.write_bytes(b'keep')
    with pytest.raises(httpx.HTTPStatusError):asyncio.run(agnes.download('https://cdn.invalid/a',dest))
    assert len(calls)==expected and dest.read_bytes()==b'keep'
    assert not list(tmp_path.glob('*.part'))


@pytest.mark.parametrize('changed', ['unchanged','first_frame','model'])
def test_tail_receipt_survives_failed_download_and_new_batch(tmp_path,monkeypatch,changed):
    import routers.shots as routes
    store=ProjectStore(tmp_path)
    shot=Shot(shot_id='s01_c01',scene_id='s01',index=1,link_mode='keyframes',selected_first_frame='ff1',
              first_frame=ImageAsset(candidates=[Asset(url='https://frame.invalid/first',status='done')],status='done'))
    store.save(Project(name='p',scenes=[Scene(scene_id='s01',shots=[shot])]))
    class Provider:
        generated=0;downloads=0
        models={'image':'original'}
        async def generate_image(self,**kw):
            self.generated+=1;return {'url':f'https://cdn.invalid/image{self.generated}.png'}
        async def download(self,url,dest):
            self.downloads+=1
            if self.downloads==1:raise httpx.ConnectTimeout('CDN')
            Path(dest).parent.mkdir(parents=True,exist_ok=True);Path(dest).write_bytes(b'png')
    provider=Provider()
    monkeypatch.setattr(routes,'get_project_manager',lambda:SimpleNamespace(get_project_dir=lambda _:tmp_path))
    monkeypatch.setattr(routes,'get_agnes_client',lambda:provider)
    monkeypatch.setattr(routes,'shot_has_visual_production_binding',lambda *a:False)
    monkeypatch.setattr(routes,'build_last_frame_prompt',lambda *a,**kw:'end')
    async def emit(*a):pass
    def job():return SimpleNamespace(project='p',shot_id='s01_c01',params={'origin_run_id':'run'})
    with pytest.raises(httpx.ConnectTimeout):asyncio.run(routes._handler_last_frame(job(),emit))
    receipts=list((tmp_path/'output/image_receipts').glob('*.json'))
    assert len(receipts)==1 and json.loads(receipts[0].read_text())['url']
    assert store.load().get_shot('s01_c01').last_frame_batches==[]
    if changed=='first_frame':store.update_shot('s01_c01',lambda s:setattr(s,'selected_first_frame','ff2'))
    if changed=='model':provider.models={'image':'new'}
    asyncio.run(routes._handler_last_frame(job(),emit))
    saved=store.load().get_shot('s01_c01')
    assert provider.generated==(1 if changed=='unchanged' else 2)
    assert len(saved.last_frame_batches)==1
    assert len(list((tmp_path/'output/image_receipts').glob('*.json')))==(1 if changed=='unchanged' else 2)
    assert saved.last_frame_batches[-1].status=='done' and saved.last_frame.ready


def snapshot(tmp_path):
    stage=tmp_path/'snapshot';p=stage/'projects/cloud3min_unit'
    old='/old/checkout/projects/cloud3min_unit'
    video=p/'output/videos/one.mp4';video.parent.mkdir(parents=True);video.write_bytes(b'original-video')
    doc={'name':'cloud3min_unit','scenes':[{'shots':[{'video_versions':[{'video_id':'paid1','version_id':'v1','status':'done','local':old+'/output/videos/one.mp4'}]}]}]}
    (p/'output/project.json').write_text(json.dumps(doc))
    f=p/'output/full_runs/full/run.json';f.parent.mkdir(parents=True)
    f.write_text(json.dumps({'run_id':'full','project_name':'cloud3min_unit','status':'failed'}))
    s=stage/'e2e_run/unit/summary.json';s.parent.mkdir(parents=True)
    s.write_text(json.dumps({'nonce':'unit','github_run_id':'10','source_sha':'oldsha','project_dir':old,'full_run_id':'full'}))
    (stage/'cache').mkdir();(stage/'cache/jobs.json').write_text('{}');(stage/'cache/quota.json').write_text('{}')
    cp={'github_run_id':'10','source_sha':'oldsha','phase':'FAILED','full_run_id':'full',
        'video_attempts':[{'state':'ACCEPTED','video_id':'paid1','seconds':10}]}
    return stage,cp,{'run_id':'10','source_sha':'oldsha'}


def test_restore_keeps_completed_video_and_rebases_paths(tmp_path):
    stage,cp,provenance=snapshot(tmp_path)
    restored=restore_snapshot(stage,tmp_path/'runner','unit',cp,provenance)
    assert restored['previous_video_seconds']==10 and len(restored['reused_completed_videos'])==1
    path=tmp_path/'runner/projects/cloud3min_unit/output/project.json'
    doc=json.loads(path.read_text());local=doc['scenes'][0]['shots'][0]['video_versions'][0]['local']
    assert Path(local).read_bytes()==b'original-video' and local.startswith(str(tmp_path/'runner'))


@pytest.mark.parametrize('corruption',['wrong_run','unknown','active_controller','missing_video','path_escape'])
def test_restore_blocks_unreconciled_input_before_copy(tmp_path,corruption):
    stage,cp,provenance=snapshot(tmp_path)
    p=stage/'projects/cloud3min_unit'
    if corruption=='wrong_run':cp['github_run_id']='11'
    if corruption=='unknown':cp['video_attempts'][0]['state']='UNKNOWN'
    if corruption=='active_controller':
        f=p/'output/full_runs/full/run.json';d=json.loads(f.read_text());d['status']='producing';f.write_text(json.dumps(d))
    if corruption=='missing_video':(p/'output/videos/one.mp4').unlink()
    if corruption=='path_escape':
        f=p/'output/project.json';d=json.loads(f.read_text());d['scenes'][0]['shots'][0]['video_versions'][0]['local']='/outside.mp4';f.write_text(json.dumps(d))
    with pytest.raises(ValueError):restore_snapshot(stage,tmp_path/'runner','unit',cp,provenance)
    assert not (tmp_path/'runner/projects/cloud3min_unit').exists()


def test_restored_paid_usage_preserved_without_reimposing_240_second_stop(tmp_path):
    from tests.relstore.test_full_project_cloud_acceptance import setup_wrapper
    wrapper,journal,provider=setup_wrapper(tmp_path)
    wrapper.restore_usage({'video_attempts':[{'state':'ACCEPTED','video_id':'old1','seconds':10},
        {'state':'ACCEPTED','video_id':'old2','seconds':10}],'image_calls':4,'text_calls':15})
    async def run():
        for i in range(28):await wrapper.create_video(str(i),duration_s=10)
    asyncio.run(run())
    assert provider.calls==28 and wrapper.image_calls==4 and wrapper.text_calls==15
    assert len(wrapper.video_attempts)==30


def test_full_resume_reactivates_the_existing_project_child(monkeypatch):
    import src.ai.full_project_autopilot as full
    manager=full.FullProjectManager()
    run=full.FullProjectRun(run_id='f',project_name='p',status='failed',stage='production',production_run_id='existing')
    calls=[]
    async def resume(project,run_id):calls.append(('child',run_id))
    async def save(run):pass
    monkeypatch.setattr(full,'get_project_autopilot_manager',lambda:SimpleNamespace(
        get_run=lambda *a:SimpleNamespace(status='failed',run_id='existing'),resume_run=resume))
    monkeypatch.setattr(manager,'_load_run',lambda *a:run)
    monkeypatch.setattr(manager,'_save_run',save)
    monkeypatch.setattr(manager,'_launch_loop',lambda run:calls.append(('full',run.run_id)))
    asyncio.run(manager.resume_run('p','f'))
    assert calls==[('child','existing'),('full','f')]
    assert run.production_run_id=='existing'


@pytest.mark.parametrize('kind,state,retried',[('last_frame','failed',True),('video','failed',False)])
def test_resume_only_requeues_failed_image_stage(monkeypatch,kind,state,retried):
    import deps
    from src.ai.autopilot import SceneAutopilotManager
    existing=SimpleNamespace(job_id='j',state=state)
    calls=[]
    def retry(job_id):calls.append(job_id);return existing
    monkeypatch.setattr(deps,'job_queue',SimpleNamespace(find_by_origin=lambda *a:existing,retry=retry))
    result=asyncio.run(SceneAutopilotManager()._enqueue_autopilot_job('p','s',kind,'key','run'))
    assert result is existing and bool(calls)==retried


@pytest.mark.parametrize('corruption', ['digest','traversal','active'])
def test_artifact_download_rejects_untrusted_snapshot(tmp_path,monkeypatch,corruption):
    import hashlib,io,zipfile
    import tools.full_project_resume as recovery
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as archive:
        archive.writestr('../escape' if corruption=='traversal' else 'e2e_run/unit/summary.json','{}')
    blob=data.getvalue()
    def open_request(request,timeout):
        path=request.full_url
        if path.endswith('/actions/runs/10'):
            value={'status':'in_progress' if corruption=='active' else 'completed','conclusion':'failure','head_sha':'sha'}
        elif path.endswith('/artifacts'):
            value={'artifacts':[{'id':1,'name':'e2e-evidence','expired':False,'digest':'sha256:'+('wrong' if corruption=='digest' else hashlib.sha256(blob).hexdigest())}]}
        else:return io.BytesIO(blob)
        return io.BytesIO(json.dumps(value).encode())
    monkeypatch.setattr(recovery.urllib.request,'build_opener',lambda *a:SimpleNamespace(open=open_request))
    with pytest.raises(ValueError):recovery.download_snapshot('10','unit','read-token',tmp_path/'stage')
    assert not (tmp_path/'escape').exists()


def test_scene_resume_retries_failed_shot_without_repeating_completed_stages(monkeypatch):
    import src.ai.autopilot as scene
    manager=scene.SceneAutopilotManager()
    first=scene.ShotExecutionState(shot_id='s01_c01',index=1,status='completed',completed_stages=['video'],video_version_id='old-version')
    failed=scene.ShotExecutionState(shot_id='s01_c02',index=2,status='failed',completed_stages=['activate_plan','last_prompt'])
    run=scene.SceneAutopilotRun(run_id='original',project_name='p',scene_id='s01',plan_id='plan',
        plan_snapshot={'shots':[{'shot_id':'s01_c01','opening_source':'independent','link_mode':'i2v','generate_first_frame':True,'generate_last_frame':False,'requires_previous_video_end':False},
                                {'shot_id':'s01_c02','opening_source':'independent','link_mode':'keyframes','generate_first_frame':True,'generate_last_frame':True,'requires_previous_video_end':False}]},
        run_seed=1,status='failed',shots=[first,failed])
    async def save(run):pass
    async def atomic_edit(project_name, run_id, updater):
        # Resume edits through the per-run atomic update boundary, rather than
        # writing an entire stale checkpoint over concurrent sibling progress.
        updater(run)
        return run
    monkeypatch.setattr(manager,'_load_run',lambda *a:run)
    monkeypatch.setattr(manager,'_save_run',save)
    monkeypatch.setattr(manager,'_edit_run',atomic_edit)
    monkeypatch.setattr(manager,'_launch_loop',lambda *a:None)
    monkeypatch.setattr(scene,'dependency_ready',lambda *a:True)
    asyncio.run(manager.resume_run('p','original'))
    # The failed shot is reset to pending; dependency-ready selection is tested
    # with a fully materialized Project/Plan in test_c2_dynamic_parallel.
    assert failed.status == 'pending'
    assert first.status=='completed' and first.video_version_id=='old-version'
    assert failed.completed_stages==['activate_plan','last_prompt']


def test_cancelled_stage_cannot_be_automatically_retried(monkeypatch):
    import deps
    from src.ai.autopilot import SceneAutopilotManager
    monkeypatch.setattr(deps,'job_queue',SimpleNamespace(find_by_origin=lambda *a:SimpleNamespace(state='cancelled')))
    with pytest.raises(RuntimeError,match='cancelled'):
        asyncio.run(SceneAutopilotManager()._enqueue_autopilot_job('p','s','last_frame','key','run'))


def test_awaiting_human_checkpoint_is_operator_resumeable(tmp_path):
    """回归（2026-10-24 跳5 实弹）：MAX_AUTO_HOPS 触顶置 AWAITING_HUMAN 后，
    人工派发的恢复跳被 phase 白名单拒收（checkpoint is not a failed/interrupted
    production run）。人工 dispatch 本身即人工介入证明，两种模式都必须放行。"""
    stage,cp,provenance=snapshot(tmp_path)
    cp['phase']='AWAITING_HUMAN';cp['terminal_status']='paused'
    restored=restore_snapshot(stage,tmp_path/'runner','unit',cp,provenance)
    assert restored['previous_video_seconds']==10
    assert len(restored['reused_completed_videos'])==1


def test_awaiting_human_checkpoint_is_finalize_resumeable(tmp_path,monkeypatch):
    """AWAITING_HUMAN + finalize_only 同样放行：护栏触顶可能发生在全部镜头
    完成但未合成时，收尾跳走 finalize-only 路径。"""
    from tools import full_project_resume as recovery
    monkeypatch.setattr(recovery,'validate_completed_clip',lambda *a:None)
    stage,cp,provenance=snapshot(tmp_path)
    cp['phase']='AWAITING_HUMAN'
    # finalize 路径要求 summary 的 full_run_id 等身份一致，这里只验证 phase 闸门不再拦截
    try:
        recovery.restore_snapshot(stage,tmp_path/'runner','unit',cp,provenance,finalize_only=True)
    except ValueError as error:
        assert 'failed/interrupted' not in str(error)
