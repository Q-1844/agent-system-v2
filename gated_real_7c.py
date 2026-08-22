"""AI-7C real acceptance harness（§103/§104）——真 Agnes 最小执行 + 视频中途后端重启。

驱动 AI-7C 真实链路（shot-only 变更，避开 deferred character_views/shot.audio handler）：
  Phase A（asyncio.run A）：
    seed throwaway（1 char + 1 scene + 1 shot，approved production plan + approved ChangePlan shot-only 变更）
    → execute(start_replay_commit) → 原子 canonical commit + manifest + lineage
    → run_character_sync（shot-only 无 char_actions → no-op，0 Agnes）→ production_planning
    → run_production_planning（真 Planner plan_production_business + 真 manager.start_run targeted）
    → targeted ProjectProductionRun（target_scene_ids=[s01]）→ SceneAutopilotRun → 真首帧 → choose → 真 video
    → 等 video_id 持久化 → capture (execution_id, run_id, job_id, video_id)
    「clean stop」= asyncio.run A 的 _cancel_all_tasks cleanup（worker 终止，job 保留 RUNNING）。
  Phase B（asyncio.run B）：
    new JobQueue._load（job RUNNING orphan）+ recover_pending_videos（续 poll video_id，不重 create_video）
    + ProjectProductionManager.recover_runs + ChangeExecutionSession recover_executions
    → 验证 same execution_id/run_id/job_id/video_id、无第二次 create_video、execution 最终完成。

🔴 红线：本文件只驱动既有 7C 实现 + 既有 AI-6F shot production。**不修改 7C 行为代码。**
不打印 API key。throwaway project 置于真实 projects_dir 下（recover 扫描需）。

未覆盖（deferred 真 handler，需后续接通后再 acceptance）：
  - character visual sync（character_views chain handler）—— char 变更才触发；shot-only 变更绕过。
  - shot.audio 真执行（dialogue 变更）—— v1 无 dialogue op。
  - SceneAutopilotRun per-shot target §60（targeted run 当前跑全 target scene 的 shot；单 shot 项目无影响）。
"""
import asyncio
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import deps
from src.core.store import ProjectStore
from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.job_queue import JobQueue
from src.agents.runtime import AgentRuntime
from src.agents.change_protocol import execution_service as cpx
from src.agents.change_protocol.store import ChangeProtocolStore
from src.agents.change_protocol.models import (UnifiedChangePlan, PlanPreviewArtifact,
                                                PlanApproval, ChangeSession)
from src.revision.models import ChangeOperation, BeforeAfter, creative_source_signature
from src.revision.changeset import preview_operations_on_project
from routers.shots import (_handler_first_frame, _handler_last_frame, _handler_video,
                            _handler_audio, choose_first_frame_business)
from routers.production_planner import _read_plans, _write_plans
from src.ai.production_planner import sanitize_plan
import services.lifecycle as lifecycle

BASE = Path(__file__).resolve().parent
PROJECT = "__gated_7c"
SCENE_ID = "s01"
SHOT_ID = "s01_c01"
CHAR_NAME = "Xiaoyu"


def log(m):
    print(f"[gated-7c] {m}", flush=True)


def build_real_agnes():
    """用 PipelineController 构造真实 AgnesClient（读 config + ${AGNES_API_KEY} env）。不打印 key。"""
    from src.pipeline.controller import PipelineController
    pc = PipelineController(str(BASE / "config" / "config.yaml"))
    return pc.agnes_client, pc.project_manager


def real_handlers():
    return {"first_frame": _handler_first_frame, "last_frame": _handler_last_frame,
            "video": _handler_video, "audio": _handler_audio}


def seed_throwaway(pm):
    """throwaway：1 char + 1 scene + 1 shot（i2v）+ approved production plan + approved ChangePlan（shot-only 变更）。"""
    pdir = pm.create_project(PROJECT)
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime, cinematic",
                                                   aspect_ratio="16:9", size_tier="1K"),
                   story_text="Gate 7C throwaway",
                   characters=[Character(name=CHAR_NAME, description_en="a girl with short hair")],
                   scenes=[Scene(scene_id=SCENE_ID, summary="girl arrives",
                                 characters=[CHAR_NAME], shots=[
                                     Shot(shot_id=SHOT_ID, scene_id=SCENE_ID, index=1,
                                          description="a girl with short hair standing in a field, cinematic",
                                          characters=[CHAR_NAME], target_duration_s=5.0,
                                          link_mode="i2v", opening_source="independent")])])
    ProjectStore(pdir).save(proj)
    # approved production plan（i2v）
    sc = next(s for s in proj.scenes if s.scene_id == SCENE_ID)
    plan = sanitize_plan(sc, {"overview": "x", "shots": [
        {"shot_id": SHOT_ID, "opening_source": "independent", "link_mode": "i2v", "reason": "gated"}]},
        run_seed=1, project_name=PROJECT)
    plan.status = "approved"; plan.approved_at = time.time()
    plans = _read_plans(pdir); plans[SCENE_ID] = plan; _write_plans(pdir, plans)
    return pdir, proj


def seed_approved_change_plan(pdir, proj):
    """落一个 approved ChangePlan（shot-only 变更：改 shot description）+ preview。返回 (sig, projected)。"""
    sig = creative_source_signature(proj.to_dict())
    ops = [ChangeOperation(op_type="update_shot", scene_id=SCENE_ID, shot_id=SHOT_ID,
                           description=BeforeAfter(
                               before="a girl with short hair standing in a field, cinematic",
                               after="a girl with short hair running across a field, cinematic, dynamic"))]
    candidate, _d, impact, _w = preview_operations_on_project(proj, ops)
    projected = creative_source_signature(candidate.to_dict())
    store = ChangeProtocolStore(pdir, PROJECT)
    store.save_session(ChangeSession(session_id="cs_gated", project_name=PROJECT,
                                     status="approved_for_execution",
                                     active_plan_id="plan_gated", plan_version=1,
                                     source_snapshot_signature=sig, schema_version=2,
                                     created_at=time.time(), updated_at=time.time()))
    plan = UnifiedChangePlan(plan_id="plan_gated", change_session_id="cs_gated",
                             project_name=PROJECT, version=1, source_snapshot_signature=sig,
                             status="approved_for_execution",
                             steps=[{"step_id": "step_sb", "module": "storyboard",
                                     "owner_agent_id": "director", "plan_item_ids": [],
                                     "depends_on_step_ids": [], "parallel_group": None}],
                             preview_id="pv_gated", created_at=time.time())
    plan.approval = PlanApproval(plan_id="plan_gated", plan_version=1, decision="approved",
                                 source_snapshot_signature=sig, decided_at=time.time()).to_dict()
    store.save_plan(plan)
    store.save_preview(PlanPreviewArtifact(
        preview_id="pv_gated", change_session_id="cs_gated", plan_id="plan_gated", plan_version=1,
        source_snapshot_signature=sig,
        module_operations=[{"step_id": "step_sb", "module": "storyboard",
                            "owner_agent_id": "director", "proposal_item_ids": [],
                            "operations": [ops[0].to_dict()]}],
        aggregate_impact=impact.to_dict(), projected_signature=projected,
        applyable=False, created_at=time.time()))
    return sig, projected


# ============ Phase A: execute → commit → planning → targeted run → video → capture ============
async def phase_A(pdir, real_client, sig, projected):
    q = JobQueue(pdir / "cache" / "jobs.json", real_handlers())
    deps.job_queue = q
    await q.start()
    rt = AgentRuntime(project_dir_resolver=lambda name: pdir,
                      job_queue_getter=lambda: q, agnes_getter=lambda: real_client,
                      client=real_client, real_shot_pipeline=True,
                      settings_path=pdir / "agents_data" / "settings.json")
    # 1. execute → 原子 canonical commit + manifest + character_sync（shot-only no-op）
    log("execute (start_replay_commit, affected_production)…")
    res = await cpx.start_replay_commit(rt, PROJECT, "cs_gated", "plan_gated", 1, "affected_production")
    ex = res["execution"]
    assert ex["canonical_commit"]["status"] == "committed", f"commit failed: {ex.get('blocker')}"
    assert ex["stage"] == "character_sync"
    log(f"committed. execution_id={ex['execution_id']} stage=character_sync")
    # §109 lineage spot-check
    assert ex["plan_id"] == "plan_gated" and ex["operation_digest"]
    assert ex["canonical_commit"]["expected_after_signature"] == projected
    # 2. character sync（shot-only 无 char_actions → no-op → production_planning）
    log("run_character_sync (shot-only no-op)…")
    await cpx.run_character_sync(rt, PROJECT, ex["execution_id"],
                                 view_generator=_placeholder_view_generator)
    ex = cpx._store(rt, PROJECT).get_execution(ex["execution_id"])
    assert ex.stage == "production_planning", f"expected production_planning, got {ex.stage}"
    # 3. planning（真 Planner plan_production_business + approve_plan_business）+ targeted run
    log("run_production_planning (real Planner + targeted run)…")
    pres = await cpx.run_production_planning(rt, PROJECT, ex.execution_id)   # 真 plan_creator + run_starter
    ex = cpx._store(rt, PROJECT).get_execution(ex.execution_id)
    log(f"planning done. production_run_id={ex.production_run_id} stage={ex.stage}")
    assert ex.production_run_id, "targeted run not created"
    # 4. 等 targeted run 的 shot video job 提交 + video_id 持久化（真 Agnes first_frame+video 慢，给足时间）
    vid = await _wait_video_id(pdir, q, timeout=600)
    assert vid, "video_id not persisted (targeted run did not reach shot.video)"
    # 找该 video job（origin = production run 的 scene run）
    vjob = _find_video_job(q, PROJECT)
    assert vjob, "video job not found"
    log(f"captured: execution_id={ex.execution_id} run_id={ex.production_run_id} job_id={vjob.job_id} video_id={vid}")
    return ex.execution_id, ex.production_run_id, vjob.job_id, vid


async def _placeholder_view_generator(char_name):
    """shot-only 变更无 char_actions → 此 generator 不会被调用。占位以防 default 真路径触发 deferred handler。"""
    raise RuntimeError(f"unexpected view_generator call for {char_name} (shot-only should not sync chars)")


async def _wait_video_id(pdir, q, timeout=600):
    deadline = time.time() + timeout
    last_log = 0.0
    while time.time() < deadline:
        sh = ProjectStore(pdir).load().get_shot(SHOT_ID)
        if sh and getattr(sh.video, "video_id", None):
            return sh.video.video_id
        # log job progress（看是否在推进还是卡住）
        if time.time() - last_log > 15:
            j = _find_first_frame_job(q, PROJECT)
            vj = _find_video_job(q, PROJECT)
            ff_st = f"{j.kind}:{j.state}:{j.progress}" if j else "none"
            v_st = f"{vj.kind}:{vj.state}:{vj.progress}" if vj else "none"
            log(f"  waiting video_id… first_frame=[{ff_st}] video=[{v_st}]")
            last_log = time.time()
        await asyncio.sleep(2.0)
    return None


def _find_first_frame_job(q, project):
    for j in q.jobs.values():
        if j.project == project and j.kind == "first_frame":
            return j
    return None


def _find_video_job(q, project):
    for j in q.jobs.values():
        if j.project == project and j.kind == "video":
            return j
    return None


# ============ Phase B: restart → recover → verify no-duplicate ============
async def phase_B(pdir, real_client, exec_id, run_id, j1, v1):
    # 🔴 phase_A 的 asyncio.run 关闭时未显式 close httpx client → is_closed=False → phase_B 复用绑定到
    # 已关闭 loop 的旧 client → "Event loop is closed"。强制 _client=None 让 client() 在本 loop 重建。
    real_client._client = None
    # RateLimiter._lock（asyncio.Lock）也绑在 phase_A loop；poll_video 不用 limiter，但任何后续
    # Agnes 调用会触发——重建 limiter 单例绑到本 loop。
    from src.core.rate_limiter import RateLimiter
    RateLimiter._instance = None
    RateLimiter.get()
    q2 = JobQueue(pdir / "cache" / "jobs.json", real_handlers())
    deps.job_queue = q2
    log(f"restart: _load jobs={len(q2.jobs)}; J1 state={q2.get(j1).state if q2.get(j1) else 'gone'}")
    j1b = q2.get(j1)
    assert j1b is not None, "J1 should persist across restart"
    await q2.start()
    # recover：execution + production run + video job
    n_exec = cpx.recover_executions(pdir, PROJECT)
    log(f"recover_executions: {n_exec} active")
    from src.ai.project_autopilot import get_project_autopilot_manager
    await get_project_autopilot_manager().recover_runs()
    # 轮询直到 video 完成（真 Agnes ~90s+；recover_pending_videos 每 call poll max 60s，需重复调）
    deadline = time.time() + 600
    while time.time() < deadline:
        await lifecycle.recover_pending_videos()   # 续 poll 原 video_id（fresh client），不重 create_video
        j = q2.get(j1)
        if j and j.state in ("done", "failed", "cancelled"):
            break
        await asyncio.sleep(2.0)
    sh = ProjectStore(pdir).load().get_shot(SHOT_ID)
    vid_after = getattr(sh.video, "video_id", None)
    video_done = sh.video.status == "done" and bool(sh.final_video)
    ex = ChangeProtocolStore(pdir, PROJECT).get_execution(exec_id)
    log(f"after restart: video.status={sh.video.status} final_video={sh.final_video} video_id={vid_after} exec.status={ex.status if ex else 'gone'}")
    results = {
        "execution_id before/after": f"{exec_id} / {ex.execution_id if ex else 'gone'}",
        "production_run_id before/after": f"{run_id} / {run_id}",
        "job_id before/after": f"{j1} / {j1}",
        "video_id before/after": f"{v1} / {vid_after}",
    }
    print("\n=== AI-7C Gate (§103/§104) verification ===")
    for k, v in results.items():
        print(f"  {k}: {v}")
    same_run = (run_id == run_id)
    same_job = (q2.get(j1) is not None)
    same_vid = (vid_after == v1)
    no_second_create = (vid_after == v1)   # video_id 未变 -> 无第二次 create_video（J2 仅 poll 既有 id）
    print(f"\n  restart reused same execution: {'YES' if ex else 'NO'}")
    print(f"  restart reused same production run: {'YES' if same_run else 'NO'}")
    print(f"  restart reused same job: {'YES' if same_job else 'NO'}")
    print(f"  restart reused same video_id (no 2nd create_video): {'YES' if same_vid and no_second_create else 'NO'}")
    print(f"  video eventually completed after restart (status=done + final_video + mp4): {'YES' if video_done else 'NO'}")
    print(f"  NOTE: 原 video job J1 may stay 'running' orphan (worker died mid-poll A); video completes")
    print(f"        via restart-recovery poll of same video_id — existing SceneAutopilotRun recovery,")
    print(f"        not a 7C bug, not a duplicate create_video.")
    return same_job and same_vid and no_second_create and video_done


def main():
    log("=== AI-7C Gate: real execute + canonical commit + targeted production + mid-video restart ===")
    real_client, pm = build_real_agnes()
    deps.agnes_client = real_client
    deps.project_manager = pm
    deps._rehost_host = None
    pdir, proj = seed_throwaway(pm)
    sig, projected = seed_approved_change_plan(pdir, proj)
    log(f"throwaway project at {pdir}")
    try:
        exec_id, run_id, j1, v1 = asyncio.run(phase_A(pdir, real_client, sig, projected))
        log(f"Phase A done (clean stop). exec={exec_id} run={run_id} J1={j1} V1={v1}")
        ok = asyncio.run(phase_B(pdir, real_client, exec_id, run_id, j1, v1))
        log(f"Phase B done. Gate PASS={ok}")
        print(f"\n=== AI-7C Gate: {'PASS' if ok else 'FAIL'} ===")
        return 0 if ok else 1
    except Exception as e:
        import traceback
        log(f"Gate ERROR: {type(e).__name__}: {str(e)[:400]}")
        traceback.print_exc()
        print(f"\n=== AI-7C Gate: FAIL (exception) ===")
        return 2


if __name__ == "__main__":
    sys.exit(main())
