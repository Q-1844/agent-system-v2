# _p48_a10_stepB_recover.py — Phase48 A10 直接演练·进程B：真实恢复（查询同一 provider job，绝不重提交）。
# 复用 lifecycle.recover_pending_videos 的参数化窗口（42.md §二缺陷②：进程内驱动器传
# 自己的 queue/handlers 获得同等恢复钩子）——真实 provider 轮询 + 真实媒体落盘 + 真实持久化。
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

_env_path = Path(".env")
if _env_path.is_file():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

ROOT = Path("_p48_a10_root")
PROJ = "P48_A_Recovery"
PDIR = ROOT / PROJ

evidence = {
    "step": "B_recover",
    "pid": os.getpid(),
    "provider_create_video_calls": 0,   # 恢复进程必须为 0（重提交=失败）
    "queue_submit_count": 0,            # 本地队列续轮询再入队次数（非 provider 提交）
    "polled_video_ids": [],
    "events": [],
}


async def main():
    from src.pipeline.controller import PipelineController
    from src.core.store import ProjectStore
    from src.core.job_queue import JobQueue
    import services.lifecycle as lifecycle

    ctrl = PipelineController()
    client = ctrl.agnes_client

    # 防重提交哨兵：包住 create_video——恢复路径任何调用都会计数并留证
    orig_create = client.create_video

    async def _counting_create(*a, **kw):
        evidence["provider_create_video_calls"] += 1
        evidence["events"].append({"ts": time.time(),
                                   "event": "FORBIDDEN_create_video_called",
                                   "args": str(a)[:120]})
        return await orig_create(*a, **kw)

    client.create_video = _counting_create

    # ① 前置：确认持久化在杀进程后仍然成立
    store = ProjectStore(PDIR)
    sh = store.load().get_shot("s01_c01")
    assert sh.video.status == "generating" and sh.video.video_id, "前置失败：provider ID 未持久化"
    vid_before = sh.video.video_id
    evidence["video_id_from_persistence"] = vid_before
    evidence["model_from_persistence"] = getattr(sh.video, "model", None)
    t_recover_start = time.time()

    # ② 真实续收货 handler（镜像真实 video handler 的轮询续跑段：同一 video_id → poll → download → mark_done）
    async def _recover_video_handler(job, emit):
        evidence["queue_submit_count"] += 1
        s = ProjectStore(PDIR).load().get_shot(job.shot_id)
        vid = s.video.video_id
        model = getattr(s.video, "model", None)
        evidence["polled_video_ids"].append(vid)
        assert vid == vid_before, "恢复必须查询同一 provider job ID"
        await emit("running", f"恢复续轮询同一 provider job {vid[:16]}…（不重提交）", 10)
        res = await client.poll_video(vid, interval=5, max_wait=1200, model_name=model)
        dest = PDIR / "output" / "videos" / f"{job.shot_id}.mp4"
        await client.download(res["url"], dest)
        data = dest.read_bytes()
        ProjectStore(PDIR).update_shot(job.shot_id, lambda s2, d=str(dest), r=res: (
            s2.video.mark_done(d, r["url"]),
            setattr(s2.video, "seconds_returned", r["seconds"]),
            setattr(s2.video, "size_returned", r["size"]),
        ))
        await emit("done", f"{job.shot_id} 恢复收货完成 {res['seconds']}s/{res['size']}", 100)

    queue = JobQueue(ROOT / "_p48_a10_jobs.json", {"video": _recover_video_handler})
    await queue.start()

    # ③ 真实恢复入口（与生产 startup 同函数；参数化窗口传隔离 queue/handlers/root）
    rec = await lifecycle.recover_pending_videos(
        queue=queue, client=client, projects_root=ROOT,
        handlers={"video": _recover_video_handler})
    evidence["recover_return"] = str(rec)[:200]

    # ④ 等队列收货完成（恢复先 poll 60s，未完成→重新入队由 handler 续轮询）
    deadline = time.time() + 1500
    while time.time() < deadline:
        sh2 = ProjectStore(PDIR).load().get_shot("s01_c01")
        if sh2.video.status == "done" and sh2.video.local:
            break
        await asyncio.sleep(5)
    sh2 = ProjectStore(PDIR).load().get_shot("s01_c01")
    t_done = time.time()
    media = Path(sh2.video.local) if sh2.video.local else None
    ok = bool(media and media.is_file() and media.stat().st_size > 0)
    media_sha = hashlib.sha256(media.read_bytes()).hexdigest() if ok else None
    evidence.update({
        "final_shot_status": sh2.video.status,
        "same_video_id": (sh2.video.video_id == vid_before),
        "media_local": str(media) if media else None,
        "media_exists": ok,
        "media_sha256": media_sha,
        "media_bytes": media.stat().st_size if ok else 0,
        "seconds_returned": sh2.video.seconds_returned,
        "size_returned": sh2.video.size_returned,
        "recovery_submit_never_called_provider":
            evidence["provider_create_video_calls"] == 0,
        "t_recover_start": t_recover_start,
        "t_media_done": t_done,
        "wall_s": round(t_done - t_recover_start, 1),
    })
    Path("_p48_a10_stepB_evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: evidence[k] for k in (
        "final_shot_status", "same_video_id", "media_exists",
        "recovery_submit_never_called_provider", "wall_s",
        "queue_submit_count")}, ensure_ascii=False, indent=1))
    for t in queue._tasks:
        t.cancel()
    await asyncio.gather(*queue._tasks, return_exceptions=True)
    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
