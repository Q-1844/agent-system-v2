"""Finalize an interrupted last job using existing selected bytes, never another POST."""
import asyncio
import json
from pathlib import Path

import pytest
from tools import full_project_resume as recovery
from tools.full_project_cloud_acceptance import runtime_deadline_minutes
from tests.relstore.test_media_download_resume import snapshot
from tests.relstore.test_full_project_cloud_acceptance import setup_wrapper


def fixture(tmp_path):
    stage,cp,provenance=snapshot(tmp_path);p=stage/'projects/cloud3min_unit'
    f=p/'output/project.json';d=json.loads(f.read_text());s=d['scenes'][0]['shots'][0];s.update(shot_id='s1',final_video='v1',video={'status':'done'})
    s['video_versions'][0]['origin_run_id']='scene';f.write_text(json.dumps(d))
    full=p/'output/full_runs/full/run.json';d=json.loads(full.read_text());d.update(status='pausing',pause_requested=True);full.write_text(json.dumps(d))
    scene=p/'output/ai/autopilot_runs/scene.json';scene.parent.mkdir(parents=True);scene.write_text(json.dumps({'run_id':'scene','status':'pausing','pause_requested':True,'shots':[{'shot_id':'s1','active_job_id':'job'}]}))
    jobs={'job':{'job_id':'job','project':'cloud3min_unit','shot_id':'s1','kind':'video','state':'running','progress':100,'error':None,'error_stage':'endframe',
                 'params':{'provider_task_id':'paid1','provider_submit_state':'accepted','origin_run_id':'scene'}}}
    (stage/'cache/jobs.json').write_text(json.dumps(jobs));return stage,cp,provenance


def test_finalize_preserves_ids_budget_bytes_and_original_snapshot(tmp_path,monkeypatch):
    stage,cp,provenance=fixture(tmp_path);old=(stage/'cache/jobs.json').read_bytes();validated=[]
    monkeypatch.setattr(recovery,'validate_completed_clip',lambda path,seconds:validated.append((path.read_bytes(),seconds)))
    result=recovery.restore_snapshot(stage,tmp_path/'runner','unit',cp,provenance,finalize_only=True)
    assert result['reconciled_jobs']==['job'] and result['previous_video_seconds']==10
    assert validated==[(b'original-video',10)] and (stage/'cache/jobs.json').read_bytes()==old
    target=tmp_path/'runner/projects/cloud3min_unit';full=json.loads((target/'output/full_runs/full/run.json').read_text())
    assert full['run_id']=='full' and full['status']=='paused' and not full['pause_requested']
    job=json.loads((tmp_path/'runner/cache/jobs.json').read_text())['job']
    assert job['state']=='done' and job['params']['provider_task_id']=='paid1'
    scene=json.loads((target/'output/ai/autopilot_runs/scene.json').read_text())
    assert scene['run_id']=='scene' and scene['status']=='paused'
    assert scene['shots'][0]['active_job_id']=='job'  # native resume consumes the existing completed job


@pytest.mark.parametrize('damage',['unselected','unknown','wrong_receipt','wrong_active_job','not_committed','cancelled','truncated'])
def test_finalize_rejects_unreconciled_or_cancelled_work_before_copy(tmp_path,monkeypatch,damage):
    stage,cp,provenance=fixture(tmp_path);p=stage/'projects/cloud3min_unit'
    monkeypatch.setattr(recovery,'validate_completed_clip',lambda *a:None)
    if damage=='unknown':cp['video_attempts'][0]['state']='UNKNOWN'
    if damage=='unselected':
        f=p/'output/project.json';d=json.loads(f.read_text());d['scenes'][0]['shots'][0]['final_video']=None;f.write_text(json.dumps(d))
    if damage in ('wrong_receipt','not_committed'):
        f=stage/'cache/jobs.json';d=json.loads(f.read_text());j=d['job']
        if damage=='wrong_receipt':j['params']['provider_task_id']='other'
        else:j['error_stage']='download';j['progress']=50
        f.write_text(json.dumps(d))
    if damage in ('wrong_active_job','cancelled'):
        f=p/'output/ai/autopilot_runs/scene.json';d=json.loads(f.read_text())
        if damage=='cancelled':d['cancel_requested']=True
        else:d['shots'][0]['active_job_id']='other'
        f.write_text(json.dumps(d))
    if damage=='truncated':
        def invalid(*a):raise ValueError('corrupt clip')
        monkeypatch.setattr(recovery,'validate_completed_clip',invalid)
    with pytest.raises(ValueError):recovery.restore_snapshot(stage,tmp_path/'runner','unit',cp,provenance,finalize_only=True)
    assert not (tmp_path/'runner/projects/cloud3min_unit').exists()


def test_finalize_cannot_call_any_paid_generation_endpoint(tmp_path):
    wrapper,journal,provider=setup_wrapper(tmp_path);wrapper.finalize_only=True
    async def run():
        for method,args in [(wrapper.chat,()),(wrapper.generate_image,()),(wrapper.create_video,('never',))]:
            with pytest.raises(RuntimeError,match='finalize-only'):await method(*args)
    asyncio.run(run())
    assert provider.calls==0 and wrapper.image_calls==wrapper.text_calls==0 and wrapper.video_attempts==[]


@pytest.mark.parametrize('value,valid',[('30',True),('330',True),('331',False),('360',False),('0',False),('330.5',False),('bad',False)])
def test_driver_deadline_preserves_thirty_minute_outer_buffer(value,valid):
    assert runtime_deadline_minutes({})==330
    if valid:assert runtime_deadline_minutes({'DRIVER_TIMEOUT_MINUTES':value})==int(value)
    else:
        with pytest.raises(ValueError):runtime_deadline_minutes({'DRIVER_TIMEOUT_MINUTES':value})
