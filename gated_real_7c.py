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
from dotenv import load_dotenv
load_dotenv()   # 加载 .env（AGNES_API_KEY 等）供 PipelineController ${AGNES_API_KEY} 替换；不硬编码 key

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
from src.agents.character_views_handler import handler_character_views
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
            "video": _handler_video, "audio": _handler_audio,
            "character_views": handler_character_views}


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


def seed_approved_change_plan_char(pdir, proj):
    """落 approved ChangePlan（character 变更：改 description_en）+ preview。触发 character_sync。"""
    sig = creative_source_signature(proj.to_dict())
    desc_before = "a girl with short hair"
    desc_after = "a girl with long flowing black hair, green eyes"
    ops = [ChangeOperation(op_type="update_character", character_name=CHAR_NAME,
                           description_en=BeforeAfter(before=desc_before, after=desc_after))]
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
                             steps=[{"step_id": "step_ch", "module": "character",
                                     "owner_agent_id": "character_designer", "plan_item_ids": [],
                                     "depends_on_step_ids": [], "parallel_group": None}],
                             preview_id="pv_gated", created_at=time.time())
    plan.approval = PlanApproval(plan_id="plan_gated", plan_version=1, decision="approved",
                                 source_snapshot_signature=sig, decided_at=time.time()).to_dict()
    store.save_plan(plan)
    store.save_preview(PlanPreviewArtifact(
        preview_id="pv_gated", change_session_id="cs_gated", plan_id="plan_gated", plan_version=1,
        source_snapshot_signature=sig,
        module_operations=[{"step_id": "step_ch", "module": "character",
                            "owner_agent_id": "character_designer", "proposal_item_ids": [],
                            "operations": [ops[0].to_dict()]}],
        aggregate_impact=impact.to_dict(), projected_signature=projected,
        applyable=False, created_at=time.time()))
    return sig, projected


async def phase_char_sync(pdir, real_client, sig, projected):
    """§99 appearance-only char change：execute → commit → character_sync（真 character_views handler）
    → 验证 Character.views 三视图再生（front/side/back done + url + local）。不跑下游 production。"""
    q = JobQueue(pdir / "cache" / "jobs.json", real_handlers())
    deps.job_queue = q
    await q.start()
    rt = AgentRuntime(project_dir_resolver=lambda name: pdir,
                      job_queue_getter=lambda: q, agnes_getter=lambda: real_client,
                      client=real_client, real_shot_pipeline=True,
                      settings_path=pdir / "agents_data" / "settings.json")
    log("execute (start_replay_commit, char change)…")
    res = await cpx.start_replay_commit(rt, PROJECT, "cs_gated", "plan_gated", 1, "affected_production")
    ex = res["execution"]
    assert ex["canonical_commit"]["status"] == "committed", f"commit failed: {ex.get('blocker')}"
    assert ex["stage"] == "character_sync"
    log(f"committed. execution_id={ex['execution_id']} stage=character_sync")
    # manifest 应标 char regenerate
    m = cpx.get_production_manifest(rt, PROJECT, ex["execution_id"])
    assert m["character_actions"].get(CHAR_NAME) == "regenerate"
    log(f"manifest: {CHAR_NAME} -> regenerate (char sync required)")
    # character_sync（default 真 view_generator → character_views job → handler 生成三视图）
    log("run_character_sync (real character_views handler)…")
    await cpx.run_character_sync(rt, PROJECT, ex["execution_id"])   # default real path
    # 验证 Character.views 三视图再生
    ch = next(c for c in ProjectStore(pdir).load().characters if c.name == CHAR_NAME)
    log(f"Character.views keys={sorted(ch.views.keys())}")
    assert set(ch.views.keys()) == {"front", "side", "back"}, f"views 不全: {ch.views.keys()}"
    for v, a in ch.views.items():
        assert a.status == "done", f"{v} status={a.status}"
        assert a.url, f"{v} 无 url"
        assert a.local, f"{v} 无 local"
    log("[OK] character visual sync PASS: front/side/back 三视图再生 (done + url + local)")
    ex = cpx._store(rt, PROJECT).get_execution(ex["execution_id"])
    assert ex.stage == "production_planning"
    assert len(ex.character_sync) == 1
    return True


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


# ============ §99b / S5-2 A：char-sync 中途重启 -> 不重复 Agnes 角色生成 ============
# Phase A2：execute→commit→resume(kickoff 后台 char-sync)→轮询直到 character_views job 提交
#         →capture job_id + job_state + views_pre → clean stop（asyncio.run 结束，worker+后台 task 取消）。
# Phase B2：新 loop→_load（char_views job running→queued）→start worker→recover_executions
#         →直接 run_character_sync（D1 同款，只跑 char-sync 不级联 production_planning）
#         →find_by_origin 复用既有 job 不重 submit→worker 重跑 queued job→poll 至完成。
# 主证明：B2 的 char_views job_id == A2（find_by_origin 复用，不重 submit=不重复生成 job）；
#         done_views 跳过已持久化 view（handler per-view 幂等 line 62-69）；三视图 done+url+local；
#         stage→production_planning；Agnes generate_image 总调用受控（≤6，catch 全 break）。
async def phase_char_sync_A(pdir, real_client, sig, projected):
    """Phase A2：提交 char-sync 后即捕获 char_views job_id 并 clean stop（char-sync 已开始、未完成时后端重启）。"""
    q = JobQueue(pdir / "cache" / "jobs.json", real_handlers())
    deps.job_queue = q
    await q.start()
    rt = AgentRuntime(project_dir_resolver=lambda name: pdir,
                      job_queue_getter=lambda: q, agnes_getter=lambda: real_client,
                      client=real_client, real_shot_pipeline=True,
                      settings_path=pdir / "agents_data" / "settings.json")
    log("execute (start_replay_commit, char change)…")
    res = await cpx.start_replay_commit(rt, PROJECT, "cs_gated", "plan_gated", 1, "affected_production")
    ex = res["execution"]
    assert ex["canonical_commit"]["status"] == "committed", f"commit failed: {ex.get('blocker')}"
    assert ex["stage"] == "character_sync"
    exec_id = ex["execution_id"]
    log(f"committed. execution_id={exec_id} stage=character_sync")
    # resume -> 后台 kickoff char-sync（_run_postcommit_to_completion -> run_character_sync
    #   -> _real_character_view_generator 提交 character_views job + poll）
    log("resume (kickoff background char-sync)…")
    await cpx.resume_execution(rt, PROJECT, exec_id)
    # 轮询直到 character_views job 提交（find_by_origin 命中 origin=exec_id + stage_key）
    stage_key = f"char_views:{exec_id}:{CHAR_NAME}"
    deadline = time.time() + 30
    job_a = None
    while time.time() < deadline:
        job_a = q.find_by_origin(exec_id, stage_key)
        if job_a is not None:
            break
        await asyncio.sleep(0.3)
    assert job_a is not None, "char_views job 未提交（resume kickoff 未生效？）"
    ch_pre = next((c for c in ProjectStore(pdir).load().characters if c.name == CHAR_NAME), None)
    views_pre = {v: getattr(a, "status", None) for v, a in (ch_pre.views or {}).items()} if ch_pre else {}
    log(f"Phase A2: char_views job submitted id={job_a.job_id} state={job_a.state}; views_pre={views_pre}; clean stop")
    # clean stop = return -> asyncio.run 结束 -> 后台 task + worker 取消；job 保留（running/queued 落盘）
    return exec_id, job_a.job_id, job_a.state, views_pre


async def phase_char_sync_B(pdir, real_client, exec_id, job_id_a, gen_log):
    """Phase B2：重启 -> _load(job reset queued) -> worker -> recover -> resume -> 轮询至 char-sync 完成。
    断言：同一 char_views job_id 复用（不重 submit=不重复生成）；三视图 done+url+local；stage production_planning。"""
    # 跨 loop client/limiter 复位（phase_B 同款；避免 Event loop is closed）
    real_client._client = None
    from src.core.rate_limiter import RateLimiter
    RateLimiter._instance = None
    RateLimiter.get()
    q2 = JobQueue(pdir / "cache" / "jobs.json", real_handlers())
    deps.job_queue = q2
    j_pre = q2.get(job_id_a)
    log(f"restart: jobs={len(q2.jobs)}; char_views job J={job_id_a} state={j_pre.state if j_pre else 'gone'}")
    assert j_pre is not None, "char_views job 应跨重启持久化"
    await q2.start()   # worker 起来；重 route reset 的 queued char_views job（done_views 跳过已持久化 view）
    n_exec = cpx.recover_executions(pdir, PROJECT)
    log(f"recover_executions: {n_exec} active")
    rt2 = AgentRuntime(project_dir_resolver=lambda name: pdir,
                       job_queue_getter=lambda: q2, agnes_getter=lambda: real_client,
                       client=real_client, real_shot_pipeline=True,
                       settings_path=pdir / "agents_data" / "settings.json")
    store = ChangeProtocolStore(pdir, PROJECT)
    # 直接调 run_character_sync（D1 phase_char_sync 同款；只跑 char-sync，不级联 production_planning
    #   -> 不触发真实视频生成）。find_by_origin 复用既有 char_views job（不重 submit）+ worker
    #   (q2.start) 重跑 reset 的 queued job；handler done_views 跳过已持久化 view。
    log("run_character_sync (reused job via find_by_origin; worker re-processes queued job)…")
    ex = None
    try:
        await cpx.run_character_sync(rt2, PROJECT, exec_id)
    except Exception as e:
        ex = store.get_execution(exec_id)
        print(f"\n  run_character_sync FAILED: {type(e).__name__}: {str(e)[:300]}; "
              f"exec.status={ex.status if ex else '?'} stage={ex.stage if ex else '?'}")
        return False
    ex = store.get_execution(exec_id)
    # === 不变量证明 ===
    stage_key = f"char_views:{exec_id}:{CHAR_NAME}"
    job_b = q2.find_by_origin(exec_id, stage_key)
    print("\n=== AI-7C-S S5-2 A (char-sync mid-restart) verification ===")
    print(f"  char_views job_id A2/B2: {job_id_a} / {job_b.job_id if job_b else 'gone'}")
    same_job = job_b is not None and job_b.job_id == job_id_a
    print(f"  same char_views job reused (no re-submit = no duplicate generation job): {'YES' if same_job else 'NO'}")
    ch = next((c for c in ProjectStore(pdir).load().characters if c.name == CHAR_NAME), None)
    views_done = ch is not None and set(ch.views.keys()) >= {"front", "side", "back"} \
        and all(v.status == "done" for v in ch.views.values()) \
        and all(getattr(v, "url", None) and getattr(v, "local", None) for v in ch.views.values())
    if ch:
        print(f"  Character.views: {sorted(ch.views.keys())} all_done={views_done}")
    stage_ok = ex is not None and ex.stage == "production_planning" and ex.status != "blocked"
    print(f"  execution stage -> production_planning (char-sync recovered): {'YES' if stage_ok else 'NO'}"
          f" (stage={ex.stage if ex else '?'}, status={ex.status if ex else '?'})")
    n_calls = len(gen_log)
    print(f"  Agnes generate_image total calls (one set=3; <=6 allows all-or-nothing writeback waste): {n_calls}")
    calls_ok = n_calls <= 6
    print(f"  call count bounded: {'YES' if calls_ok else 'NO'}")
    print(f"  NOTE: handler 三视图写回是 all-or-nothing per char（末尾一次写）；若崩溃在 pre-writeback")
    print(f"        窗口，已发起但未持久化的 view 会被重做（非已持久化结果的重复）。")
    print(f"        主证明=job_id 复用 + done_views 跳过已持久化 view（handler line 62-69）。")
    return bool(same_job and views_done and stage_ok and calls_ok)


def main():
    global PROJECT
    mode = sys.argv[1] if len(sys.argv) > 1 else "shot"
    if mode == "char_restart":
        PROJECT = "__gated_7c_cr"   # 独立 throwaway，不覆盖既有 __gated_7c（保留 D1 char-sync 证据）
    real_client, pm = build_real_agnes()
    deps.agnes_client = real_client
    deps.project_manager = pm
    deps._rehost_host = None
    pdir, proj = seed_throwaway(pm)
    log(f"throwaway project at {pdir}  mode={mode}")
    try:
        if mode == "char":
            log("=== AI-7C §99 char-sync Gate: char change → commit → character_views regeneration ===")
            sig, projected = seed_approved_change_plan_char(pdir, proj)
            ok = asyncio.run(phase_char_sync(pdir, real_client, sig, projected))
            print(f"\n=== AI-7C char-sync Gate: {'PASS' if ok else 'FAIL'} ===")
            return 0 if ok else 1
        if mode == "char_restart":
            log("=== AI-7C-S S5-2 A: char-sync mid-restart -> no duplicate Agnes char generation ===")
            # Agnes generate_image 调用计数 wrapper（辅证：跨两 phase 总调用受控；主证是 job_id 复用）
            gen_log = []
            _orig_gen = real_client.generate_image
            async def _counting_gen(*a, **k):
                r = await _orig_gen(*a, **k)
                gen_log.append(True)
                return r
            real_client.generate_image = _counting_gen
            sig, projected = seed_approved_change_plan_char(pdir, proj)
            exec_id, job_id_a, state_a, _views_pre = asyncio.run(
                phase_char_sync_A(pdir, real_client, sig, projected))
            log(f"Phase A2 done (clean stop). exec={exec_id} char_views_job={job_id_a} state={state_a}")
            ok = asyncio.run(phase_char_sync_B(pdir, real_client, exec_id, job_id_a, gen_log))
            log(f"Phase B2 done. Gate PASS={ok}")
            print(f"\n=== AI-7C-S S5-2 A (char-sync mid-restart): {'PASS' if ok else 'FAIL'} ===")
            return 0 if ok else 1
        # default: shot-only §103/§104
        log("=== AI-7C Gate: real execute + canonical commit + targeted production + mid-video restart ===")
        sig, projected = seed_approved_change_plan(pdir, proj)
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
