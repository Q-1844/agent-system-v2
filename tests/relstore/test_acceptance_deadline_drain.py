"""A parent paused flag must not truncate an accepted provider task."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from src.core.models import Project,Scene,Shot,VideoVersion
from src.core.store import ProjectStore
from tools import full_project_cloud_acceptance as driver


def settled_project(tmp_path):
    p=tmp_path/'p';v=p/'output/video.mp4';v.parent.mkdir(parents=True);v.write_bytes(b'completed-video')
    ProjectStore(p).save(Project(name='p',scenes=[Scene(scene_id='s',shots=[Shot(shot_id='s1',scene_id='s',index=1,
        video_versions=[VideoVersion(version_id='v',video_id='paid',status='done',local=str(v))])])]))
    for f in ['full_runs/full/run.json','production_runs/project/run.json','ai/autopilot_runs/scene.json']:
        path=p/'output'/f;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps({'status':'paused'}))
    return p,[{'state':'ACCEPTED','video_id':'paid','seconds':10}],SimpleNamespace(jobs={})


@pytest.mark.parametrize('damage',['active_child','active_job','unknown_id','missing_file','duplicate_version'])
def test_parent_paused_cannot_hide_unsettled_work(tmp_path,damage):
    p,attempts,queue=settled_project(tmp_path)
    assert driver.snapshot_settled(p,attempts,queue)
    if damage=='active_child':(p/'output/ai/autopilot_runs/scene.json').write_text('{"status":"running"}')
    if damage=='active_job':queue.jobs['j']=SimpleNamespace(project='p',state='running')
    if damage=='unknown_id':attempts[0]['state']='UNKNOWN'
    if damage=='missing_file':(p/'output/video.mp4').unlink()
    if damage=='duplicate_version':
        store=ProjectStore(p);store.update_shot('s1',lambda s:s.video_versions.append(s.video_versions[0]))
    assert not driver.snapshot_settled(p,attempts,queue)


@pytest.mark.parametrize('finish', [True,False])
def test_deadline_allows_bounded_drain_and_never_authorizes_new_media(tmp_path,monkeypatch,finish):
    p,attempts,queue=settled_project(tmp_path);queue.jobs['j']=SimpleNamespace(project='p',state='running')
    clock=[0.0];writes=[];paused=[]
    async def pause(*a):paused.append(a)
    manager=SimpleNamespace(request_pause=pause,get_run=lambda *a:SimpleNamespace(status='paused'))
    async def persist(**kw):writes.append(kw)
    wrapper=SimpleNamespace(project_dir=p,video_attempts=attempts,persist=persist,submission_blocked=False)
    async def tick(seconds):
        clock[0]+=seconds
        assert wrapper.submission_blocked
        if finish:queue.jobs['j'].state='done'
    monkeypatch.setattr(driver.time,'monotonic',lambda:clock[0]);monkeypatch.setattr(driver.asyncio,'sleep',tick)
    complete=asyncio.run(driver.pause_and_drain(manager,'p','original',wrapper,queue,grace_seconds=6))
    assert paused==[('p','original')] and complete==finish and wrapper.submission_blocked
    assert writes[-1]['drain_complete']==finish
    assert writes[-1]['phase']==('FAILED' if finish else 'INTERRUPTED')


def test_deadline_gate_blocks_images_before_another_paid_call(tmp_path):
    from tests.relstore.test_full_project_cloud_acceptance import setup_wrapper
    wrapper,journal,provider=setup_wrapper(tmp_path);wrapper.submission_blocked=True
    with pytest.raises(RuntimeError,match='paused'):
        asyncio.run(wrapper.generate_image(prompt='must not call provider'))
    assert wrapper.image_calls==0 and provider.calls==0
