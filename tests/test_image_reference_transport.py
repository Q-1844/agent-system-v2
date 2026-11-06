"""Saved image bytes and unpublished Pages URLs in cross-day recovery."""
import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src.core.models import Asset, Character, ImageAsset, Project, Scene, Shot
from src.core.store import ProjectStore


def _fixture(tmp_path, inherited):
    first = tmp_path/'first.jpg'; first.write_bytes(b'original-first-bytes')
    person = tmp_path/'person.png'; person.write_bytes(b'original-person-bytes')
    asset = Asset(local=str(first), url='https://host.invalid/unpublished.jpg', status='done')
    source = Shot(shot_id='s01_c01',scene_id='s01',index=1,video_end_frame=asset)
    target = Shot(shot_id='s01_c02',scene_id='s01',index=2,link_mode='keyframes',characters=['hero'])
    if inherited:
        target.opening_source='inherit_previous';target.source_shot_id=source.shot_id
    else:
        target.first_frame=ImageAsset(candidates=[asset],status='done')
        target.selected_first_frame='ff1'
    project=Project(name='p',scenes=[Scene(scene_id='s01',shots=[source,target])],
        characters=[Character(name='hero',views={'front':Asset(local=str(person),url='https://cdn.invalid/person',status='done')})])
    ProjectStore(tmp_path).save(project)
    return project,target,first,person


@pytest.mark.parametrize('inherited',[False,True])
def test_local_image_transport_preserves_order_urls_and_exact_bytes(tmp_path,inherited):
    from routers.shots import _local_image_generation_refs
    project,shot,first,person=_fixture(tmp_path,inherited)
    logical=['https://host.invalid/unpublished.jpg','https://cdn.invalid/person',
             'https://external.invalid/url-only','data:image/png;base64,eA==']
    sent=_local_image_generation_refs(tmp_path,project,shot,logical)
    for value,path,mime in zip(sent[:2],[first,person],['image/jpeg','image/png']):
        assert value.startswith(f'data:{mime};base64,')
        assert base64.b64decode(value.split(',',1)[1])==path.read_bytes()
    assert sent[2:]==logical[2:]
    assert logical[0].startswith('https:')
    assert 'data:image' not in (tmp_path/'output/project.json').read_text()
    first.unlink()
    assert _local_image_generation_refs(tmp_path,project,shot,logical)[0]==logical[0]


@pytest.mark.parametrize('inherited',[False,True])
def test_last_frame_uses_local_inputs_and_replays_existing_receipt(tmp_path,monkeypatch,inherited):
    import routers.shots as routes
    project,shot,first,person=_fixture(tmp_path,inherited)
    calls=[]
    class Provider:
        models={'image':'same-model'}
        downloads=0
        async def generate_image(self,**kw):
            calls.append(kw)
            assert all(r.startswith('data:image/') for r in kw['image_refs'])
            return {'url':'https://cdn.invalid/accepted-tail'}
        async def download(self,url,dest):
            self.downloads+=1
            if self.downloads==1:raise httpx.ConnectTimeout('download interrupted')
            dest.write_bytes(b'completed-tail')
    provider=Provider()
    monkeypatch.setattr(routes,'get_project_manager',lambda:SimpleNamespace(get_project_dir=lambda _:tmp_path))
    monkeypatch.setattr(routes,'get_agnes_client',lambda:provider)
    monkeypatch.setattr(routes,'shot_has_visual_production_binding',lambda *a:False)
    monkeypatch.setattr(routes,'build_last_frame_prompt',lambda *a,**k:'end')
    async def emit(*a):pass
    def job():return SimpleNamespace(project='p',shot_id=shot.shot_id,params={'origin_run_id':'run'})
    with pytest.raises(httpx.ConnectTimeout):asyncio.run(routes._handler_last_frame(job(),emit))
    # Losing optional local copies changes only transport, not the logical input
    # digest. An accepted receipt must still be downloaded, never regenerated.
    first.unlink();person.unlink()
    asyncio.run(routes._handler_last_frame(job(),emit))
    assert len(calls)==1
    saved=ProjectStore(tmp_path).load().get_shot(shot.shot_id)
    assert saved.last_frame.ready and len(saved.last_frame_batches)==1
    receipts=list((tmp_path/'output/image_receipts').glob('*.json'))
    assert len(receipts)==1 and 'data:image' not in receipts[0].read_text()


def test_unpublished_video_input_stops_before_post_or_submission_intent(tmp_path,monkeypatch):
    import deps
    from routers.shots import _handler_video
    from src.core.image_host import GithubPagesHost
    from src.core.job_queue import Job
    from tests.test_p55_recovery import FakeAgnes, _FakePM, _save_proj, _mark_ff_ready, PROJECT
    _save_proj(tmp_path);_mark_ff_ready(tmp_path,'s01_c01')
    provider=FakeAgnes();seen=[]
    class PendingHost(GithubPagesHost):
        async def ensure_published(self,url):
            seen.append(url)
            raise self.PagesNotReadyError('still 404')
    monkeypatch.setattr(deps,'project_manager',_FakePM(tmp_path))
    monkeypatch.setattr(deps,'agnes_client',provider)
    monkeypatch.setattr(deps,'_rehost_host',PendingHost('u','r','main','t'))
    job=Job('j',PROJECT,'video',shot_id='s01_c01')
    async def emit(*a):pass
    with pytest.raises(GithubPagesHost.PagesNotReadyError):asyncio.run(_handler_video(job,emit))
    assert seen==['https://fake.agnes/first.png'] and provider.calls==[]
    assert job.params['video_pre_submit_failure']=='image_publication'
    assert not any(job.params.get(k) for k in ['submission_intent_id','provider_task_id','dispatch_prompt'])
    assert ProjectStore(tmp_path).load().get_shot(job.shot_id).video.status!='generating'


def test_publication_failure_is_retryable_only_before_paid_submission(monkeypatch):
    import deps
    from src.ai.autopilot import SceneAutopilotManager
    calls=[]
    existing=SimpleNamespace(job_id='j',state='failed',params={'video_pre_submit_failure':'image_publication'},error='404',error_stage='rehost')
    monkeypatch.setattr(deps,'job_queue',SimpleNamespace(find_by_origin=lambda *a:existing,retry=lambda j:calls.append(j) or existing))
    manager=SceneAutopilotManager()
    assert asyncio.run(manager._enqueue_autopilot_job('p','s','video','key','run')) is existing
    assert calls==['j']
    calls.clear();existing.params['submission_intent_id']='unknown'
    assert asyncio.run(manager._enqueue_autopilot_job('p','s','video','key','run')) is existing
    assert calls==[]
