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


@pytest.mark.parametrize('platform',['posix','windows'])
def test_native_state_reconciliation_uses_path_parts_on_both_platforms(platform):
    from pathlib import PurePosixPath,PureWindowsPath
    cls=PurePosixPath if platform=='posix' else PureWindowsPath
    root=cls('/snapshot/project' if platform=='posix' else 'C:/snapshot/project')
    full={'status':'pausing','pause_requested':True}
    rewritten={root/'output/project.json':{'scenes':[{'shots':[{'shot_id':'s','final_video':'v','video':{'status':'done'},'video_versions':[{'version_id':'v','video_id':'paid','status':'done'}]}]}]},
               root/'output/full_runs/f/run.json':full}
    recovery.reconcile_finalization(root,rewritten,{},['paid'],{'run_id':'stopped'})
    assert full=={'status':'paused','pause_requested':False}


def test_clean_pause_drain_is_resumable_without_finalize(tmp_path):
    """daycarry12m 2026-11-01 续跑实测回填（run 41327008169）：干净暂停——
    quota 撞墙→drain 完成→收据全部落账（video.status=done、无 in-flight job）
    →FullRun 落 'pausing'。reconcile_accepted_work 无事可做（recovered_jobs
    为空），旧条件「finalize_only or recovered_jobs」只覆盖脏 drain，干净
    drain 反而被拒 → native FullRun is not safely resumable，次日续跑死在
    恢复门口，500s 额度白等。非 finalize 恢复必须放行干净暂停形态。"""
    stage,cp,provenance=snapshot(tmp_path)
    p=stage/'projects/cloud3min_unit'
    # 收据已落账：video_version status=done，镜头已完成（无在途 job）
    f=p/'output/project.json';d=json.loads(f.read_text())
    d['scenes'][0]['shots'][0].update(shot_id='s1',final_video='v1',
        video={'status':'done','video_id':'paid1'})
    f.write_text(json.dumps(d))
    full=p/'output/full_runs/full/run.json';d=json.loads(full.read_text())
    d.update(status='pausing',pause_requested=True);full.write_text(json.dumps(d))
    # jobs.json 空（干净 drain：无未结算提交）
    (stage/'cache/jobs.json').write_text('{}')
    result=recovery.restore_snapshot(stage,tmp_path/'runner','unit',cp,provenance)
    assert result['previous_video_seconds']==10
    target=tmp_path/'runner/projects/cloud3min_unit/output/full_runs/full/run.json'
    assert json.loads(target.read_text())['status']=='pausing'


# ---------- 🔴 硬基础设施死亡豁免（2026-11-10 real1000s run 315 实锤） ----------
# GitHub 500 硬死亡于 producing 中段、账面干净（零在途提交）——原闸防
# 「控制器可能还活着」拒绝 producing，片子死循环在恢复门口（316/317 两跳
# 39 秒被拒）。源 run 已 completed（Actions 终态）= 控制器必死 → 放行。
# 红了先怀疑自己改了不该改的。

def _producing_hard_death_snapshot(tmp_path):
    stage,cp,provenance=snapshot(tmp_path)
    p=stage/'projects/cloud3min_unit'
    f=p/'output/project.json';d=json.loads(f.read_text())
    d['scenes'][0]['shots'][0].update(shot_id='s1',final_video='v1',
        video={'status':'done','video_id':'paid1'})
    f.write_text(json.dumps(d))
    full=p/'output/full_runs/full/run.json';d=json.loads(full.read_text())
    d.update(status='producing')  # 硬死亡：无 pause_and_drain，无 pause_requested
    full.write_text(json.dumps(d))
    (stage/'cache/jobs.json').write_text('{}')  # 账面干净：零在途提交
    return stage,cp,provenance


def test_hard_infra_death_producing_resumable_when_source_completed(tmp_path,monkeypatch):
    stage,cp,provenance=_producing_hard_death_snapshot(tmp_path)
    monkeypatch.setattr(recovery,'_source_run_definitively_dead',lambda rid: True)
    result=recovery.restore_snapshot(stage,tmp_path/'runner','unit',cp,provenance)
    assert result['previous_video_seconds']==10


def test_hard_infra_death_refused_when_source_alive_or_unknown(tmp_path,monkeypatch):
    """fail-closed：源 run 存活（in_progress）或查询失败/无 token 一律拒绝。"""
    stage,cp,provenance=_producing_hard_death_snapshot(tmp_path)
    monkeypatch.setattr(recovery,'_source_run_definitively_dead',lambda rid: False)
    with pytest.raises(ValueError,match='not safely resumable'):
        recovery.restore_snapshot(stage,tmp_path/'runner','unit',cp,provenance)
    assert not (tmp_path/'runner/projects/cloud3min_unit').exists()


def test_source_run_definitively_dead_liveness_matrix(monkeypatch):
    from tools import resume_dispatch as rd
    monkeypatch.setenv('RESUME_DISPATCH_TOKEN','t')
    monkeypatch.setattr(rd,'run_liveness',lambda t,r:'completed')
    assert recovery._source_run_definitively_dead('42654346678') is True
    monkeypatch.setattr(rd,'run_liveness',lambda t,r:'in_progress')
    assert recovery._source_run_definitively_dead('42654346678') is False
    monkeypatch.setattr(rd,'run_liveness',lambda t,r:None)  # 查询失败
    assert recovery._source_run_definitively_dead('42654346678') is False
    monkeypatch.delenv('RESUME_DISPATCH_TOKEN',raising=False)  # 无 token
    monkeypatch.delenv('GITHUB_TOKEN',raising=False)
    assert recovery._source_run_definitively_dead('42654346678') is False
    monkeypatch.setenv('RESUME_DISPATCH_TOKEN','t')
    assert recovery._source_run_definitively_dead('not-a-run-id') is False


def test_dead_source_crash_recovery_branch_wired():
    """结构锁：producing+实证死亡 → §四十七三级 recover（非 resume_run）。
    顺序铁律：scene→project→full；复活失败 fail-loud。"""
    src = Path("tools/full_project_cloud_acceptance.py").read_text(encoding="utf-8")
    anchor = src.index("_resume_source_run_definitively_dead()")
    branch = src[anchor - 600:anchor + 1400]
    assert "elif restored:" in branch
    assert "existing_full.status == 'producing'" in branch
    i_scene = branch.index("get_autopilot_manager().recover_runs()")
    i_proj = branch.index("get_project_autopilot_manager().recover_runs()")
    i_full = branch.index("manager.recover_runs()")
    assert i_scene < i_proj < i_full  # §四十七 三级依序
    assert "did not revive the FullProjectRun" in branch  # fail-loud
    assert "await manager.resume_run" in branch  # fail-closed 回落原路径


def test_resume_source_dead_helper_delegates_fail_closed(monkeypatch):
    """驱动侧实证助手委托 _source_run_definitively_dead（RESUME_RUN_ID 维度）。"""
    from tools import full_project_resume as fr
    from tools.full_project_cloud_acceptance import _resume_source_run_definitively_dead
    monkeypatch.setenv("RESUME_RUN_ID", "42654346678")
    monkeypatch.setattr(fr, "_source_run_definitively_dead", lambda rid: rid == "42654346678")
    assert _resume_source_run_definitively_dead() is True
    monkeypatch.setattr(fr, "_source_run_definitively_dead", lambda rid: False)
    assert _resume_source_run_definitively_dead() is False
    monkeypatch.delenv("RESUME_RUN_ID", raising=False)  # 无源 id → fail-closed
    assert _resume_source_run_definitively_dead() is False
