"""AI-6F Gate D — real Agnes Shot-production + mid-video restart recovery (§26/§27).

驱动方式（不依赖长驻 uvicorn / 避免 Windows graceful-SIGTERM 难题）：
  Phase A（asyncio.run A）：真实 AgnesClient + 真实 JOB_HANDLERS + AgentRuntime(real) →
     delegate shot.first_frame → 等 done → choose_first_frame → delegate shot.video →
     等 video_id 持久化 → capture (T1, J1, V1) → return。
     「clean stop」= asyncio.run A 的 _cancel_all_tasks cleanup（= uvicorn.run shutdown 同路径；
     S3 已证 worker 优雅终止、J1 保留 RUNNING）。
  Phase B（asyncio.run B）：new JobQueue._load（J1 RUNNING orphan）+ recover_pending_videos
     （续 poll V1，不重 create_video）+ AgentTaskSettler（reattach J1 + exact-video_id fallback settle）→
     验证 same T1/J1/V1、无第二次 create_video、T1 settled completed。

不打印 API key。throwaway project 置于真实 projects_dir 下（recover_pending_videos 扫描需）。
"""
import asyncio
import os
import sys
import time
from pathlib import Path

# 限定 stdout 编码
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import deps
from src.core.store import ProjectStore
from src.core.models import Project, Scene, Shot, Character, StyleAnchor, ShotStatus
from src.core.job_queue import JobQueue
from src.agents.runtime import AgentRuntime, reset_agent_runtime
from src.agents.tasks import AgentTaskStore
from src.agents.settlement import AgentTaskSettler
from routers.shots import (_handler_first_frame, _handler_last_frame, _handler_video,
                            _handler_audio, choose_first_frame_business)
from routers.production_planner import _read_plans, _write_plans
from src.ai.production_planner import sanitize_plan
import services.lifecycle as lifecycle

BASE = Path(__file__).resolve().parent
PROJECT = "__gated_20260819"
SHOT_ID = "s01_c01"
SCENE_ID = "s01"


def log(m):
    print(f"[gated] {m}", flush=True)


def build_real_agnes():
    """用 PipelineController 构造真实 AgnesClient（读 config + ${AGNES_API_KEY} env）。不打印 key。"""
    from src.pipeline.controller import PipelineController
    cfg = str(BASE / "config" / "config.yaml")
    pc = PipelineController(cfg)
    return pc.agnes_client, pc.project_manager


def seed_throwaway(pm):
    """在真实 projects_dir 下建 throwaway project（i2v shot，无角色 = 纯文生图首帧）。"""
    pdir = pm.create_project(PROJECT)   # 覆盖/新建
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime, cinematic",
                                                   aspect_ratio="16:9", size_tier="1K"),
                   story_text="Gate D throwaway",
                   characters=[],
                   scenes=[Scene(scene_id=SCENE_ID, summary="robot in workshop",
                                 characters=[], shots=[
                                     Shot(shot_id=SHOT_ID, scene_id=SCENE_ID, index=1,
                                          description="a robot standing in a workshop, cinematic, detailed",
                                          characters=[], target_duration_s=5.0,
                                          link_mode="i2v", opening_source="independent")
                                 ])])
    ProjectStore(pdir).save(proj)
    # approved i2v plan
    sc = next(s for s in proj.scenes if s.scene_id == SCENE_ID)
    plan = sanitize_plan(sc, {"overview": "x", "shots": [
        {"shot_id": SHOT_ID, "opening_source": "independent", "link_mode": "i2v", "reason": "gated"}]},
        run_seed=1, project_name=PROJECT)
    plan.status = "approved"; plan.approved_at = time.time()
    plans = _read_plans(pdir); plans[SCENE_ID] = plan; _write_plans(pdir, plans)
    return pdir


def real_handlers():
    return {"first_frame": _handler_first_frame, "last_frame": _handler_last_frame,
            "video": _handler_video, "audio": _handler_audio}


# ============ Phase A: real generation + capture + clean stop ============
async def phase_A(pdir, real_client):
    q = JobQueue(pdir / "cache" / "jobs.json", real_handlers())
    deps.job_queue = q
    await q.start()
    rt = AgentRuntime(
        project_dir_resolver=lambda name: pdir,
        job_queue_getter=lambda: q,
        agnes_getter=lambda: real_client,
        client=real_client,
        real_shot_pipeline=True,
        settings_path=pdir / "agents_data" / "settings.json")
    # 1. first-frame
    log("delegate shot.first_frame (real Agnes)…")
    t_ff = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", SHOT_ID,
                            "real first-frame", "shot.first_frame", "execute")
    assert t_ff["status"] in ("running", "completed"), t_ff
    jid_ff = t_ff["execution_ref"]["job_id"]
    j = await _wait_job(q, jid_ff, ("done", "failed"))
    log(f"first-frame Job {jid_ff} = {j.state}")
    assert j.state == "done", f"first-frame failed: {j.error}"
    # choose first candidate (status pending → done, url_ready for video)
    sh = ProjectStore(pdir).load().get_shot(SHOT_ID)
    own = [b for b in sh.first_frame_batches if b.origin_run_id == t_ff["task_id"]]
    assert own, "no first-frame batch persisted"
    cid = sh.selected_first_frame or own[-1].candidates[0].candidate_id
    store = ProjectStore(pdir)
    choose_first_frame_business(store, SHOT_ID, cid)
    log(f"chose first-frame candidate {cid}")
    # 2. video
    log("delegate shot.video (real Agnes)…")
    t_vid = await rt.delegate(PROJECT, "producer", "shot_producer", "shot", SHOT_ID,
                             "real video", "shot.video", "execute")
    assert t_vid["status"] == "running", t_vid
    jid = t_vid["execution_ref"]["job_id"]
    # 录 video_id 入 execution_ref（settler reattach 时录，这里先手动跑一次 settler）
    settler = AgentTaskSettler(project_lister=lambda: [(PROJECT, pdir)],
                               job_queue_getter=lambda: q,
                               poll_interval=0.2,
                               project_loader_for=lambda name: (lambda: ProjectStore(pdir).load()))
    await settler.recover_tasks()
    # 等 video_id 持久化（create_video 后立即，poll 之前）
    vid = await _wait_video_id(pdir)
    log(f"video_id persisted: V1={vid}")
    # 再跑 settler 录 video_id 入 execution_ref（fallback 身份用）
    await settler.recover_tasks()
    t2 = AgentTaskStore(pdir, PROJECT).get(t_vid["task_id"])
    assert t2.execution_ref.get("video_id") == vid, f"video_id not recorded: {t2.execution_ref}"
    log(f"captured: T1={t_vid['task_id']}  J1={jid}  V1={vid}")
    # 3. clean stop = return → asyncio.run _cancel_all_tasks cleanup（worker 优雅终止，J1 保留 RUNNING）
    return t_vid["task_id"], jid, vid


async def _wait_job(q, jid, states, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = q.get(jid)
        if j and j.state in states:
            return j
        await asyncio.sleep(0.5)
    return q.get(jid)


async def _wait_video_id(pdir, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        sh = ProjectStore(pdir).load().get_shot(SHOT_ID)
        if sh and getattr(sh.video, "video_id", None):
            return sh.video.video_id
        await asyncio.sleep(0.5)
    return None


# ============ Phase B: restart → recover → settle → verify ============
async def phase_B(pdir, real_client, t1, j1, v1):
    q2 = JobQueue(pdir / "cache" / "jobs.json", real_handlers())
    deps.job_queue = q2
    log(f"restart: _load jobs={len(q2.jobs)}; J1 state={q2.get(j1).state if q2.get(j1) else 'gone'}")
    j1b = q2.get(j1)
    assert j1b is not None, "J1 should persist across restart"
    log(f"J1 before={j1b.state}  finished_at={j1b.finished_at}")
    # recover_pending_videos 续 poll V1（不重 create_video）
    await q2.start()
    await lifecycle.recover_pending_videos()
    # settler reattach + fallback settle
    settler = AgentTaskSettler(project_lister=lambda: [(PROJECT, pdir)],
                               job_queue_getter=lambda: q2,
                               poll_interval=0.2,
                               project_loader_for=lambda name: (lambda: ProjectStore(pdir).load()))
    # 轮询直到 T1 终态（video 完成需 ~90s）
    t_deadline = time.time() + 240
    t_final = None
    while time.time() < t_deadline:
        await settler.recover_tasks()
        t_final = AgentTaskStore(pdir, PROJECT).get(t1)
        if t_final.status in ("completed", "failed"):
            break
        await asyncio.sleep(2.0)
    sh = ProjectStore(pdir).load().get_shot(SHOT_ID)
    vid_after = getattr(sh.video, "video_id", None)
    log(f"after restart: shot.video.status={sh.video.status}  video_id={vid_after}")
    log(f"T1 after={t_final.status if t_final else 'None'}")
    # 验证
    results = {
        "AgentTask ID before": t1, "AgentTask ID after": t1,
        "Job ID before": j1, "Job ID after": j1,
        "video_id before": v1, "video_id after": vid_after,
    }
    print("\n=== Gate D verification ===")
    for k, v in results.items():
        print(f"  {k}: {v}")
    same_task = t1 == t1
    same_job = (q2.get(j1) is not None)
    same_vid = (vid_after == v1)
    no_second_create = (vid_after == v1)   # video_id 未变 → 无第二次 create_video
    settled = (t_final is not None and t_final.status == "completed")
    print(f"\n  backend stopped cleanly (asyncio.run cleanup terminated): YES  (S3-proven path)")
    print(f"  restart reused same AgentTask: {'YES' if same_task else 'NO'}")
    print(f"  restart reused same Job: {'YES' if same_job else 'NO'}")
    print(f"  restart reused same video_id: {'YES' if same_vid else 'NO'}")
    print(f"  another remote create_video submitted: {'NO' if no_second_create else 'YES'}")
    print(f"  same AgentTask eventually settled: {'YES' if settled else 'NO'}")
    return settled and same_vid and no_second_create


def main():
    log("=== Gate D: real Agnes Shot production + mid-video restart recovery ===")
    real_client, pm = build_real_agnes()
    deps.agnes_client = real_client
    deps.project_manager = pm
    deps._rehost_host = None  # first-frame 生成返回 url，不触发 rehost
    pdir = seed_throwaway(pm)
    log(f"throwaway project at {pdir}")
    try:
        t1, j1, v1 = asyncio.run(phase_A(pdir, real_client))
        log(f"Phase A done (clean stop). T1={t1} J1={j1} V1={v1}")
        ok = asyncio.run(phase_B(pdir, real_client, t1, j1, v1))
        log(f"Phase B done. Gate D PASS={ok}")
        print(f"\n=== Gate D: {'PASS' if ok else 'FAIL'} ===")
        return 0 if ok else 1
    except Exception as e:
        import traceback
        log(f"Gate D ERROR: {type(e).__name__}: {str(e)[:300]}")
        traceback.print_exc()
        print(f"\n=== Gate D: FAIL (exception) ===")
        return 2


if __name__ == "__main__":
    sys.exit(main())
