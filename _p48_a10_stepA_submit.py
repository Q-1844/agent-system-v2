# _p48_a10_stepA_submit.py — Phase48 A10 直接演练·进程A：真实提交+provider ID 持久化，随后被硬杀。
# 隔离：项目在 _p48_a10_root/P48_A_Recovery（独立于全部既有项目）；
# 真实 provider（api.agnes-ai.cn，agnes-video-2.5-flash，t2v 4s 720P）。
import asyncio
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


async def main():
    from src.pipeline.controller import PipelineController
    from src.core.store import ProjectStore
    from src.core.models import Project, Scene, Shot

    ctrl = PipelineController()
    client = ctrl.agnes_client

    # ① 隔离夹具项目（1 镜）
    pdir = PDIR
    proj = Project(name=PROJ)
    sh = Shot(shot_id="s01_c01", scene_id="s01", index=1,
              description="A10 恢复演练镜头：夜晚路灯落雪")
    proj.scenes = [Scene(scene_id="s01", shots=[sh])]
    ProjectStore(pdir).save(proj)

    # ② 真实提交（本演练唯一一次 provider 生成请求）
    t_submit0 = time.time()
    res = await client.create_video(
        "Quiet empty street at night, a single warm street lamp glowing, "
        "snowflakes falling slowly and gently, cinematic, calm, no people, no text.",
        mode="t2v", duration_s=4)
    t_submit1 = time.time()
    video_id = res.get("video_id") or res.get("task_id")
    assert video_id, f"提交响应缺 video_id: {res}"

    # ③ provider ID 持久化（与真实 video handler 同落点：shot.video.status=generating + video_id + model）
    model = ctrl.config["agnes"]["models"]["video"]
    ProjectStore(pdir).update_shot("s01_c01", lambda s: (
        setattr(s.video, "status", "generating"),
        setattr(s.video, "video_id", video_id),
        setattr(s.video, "task_id", res.get("task_id")),
        setattr(s.video, "model", model),
    ))

    # ④ 从磁盘重读核验持久化（跨进程证据）
    sh2 = ProjectStore(pdir).load().get_shot("s01_c01")
    assert sh2.video.status == "generating" and sh2.video.video_id == video_id
    ev = {
        "step": "A_submit",
        "pid": os.getpid(),
        "provider_submit_count": 1,
        "video_id": video_id,
        "task_id": res.get("task_id"),
        "model": model,
        "mode": "t2v",
        "duration_s": 4,
        "persisted": True,
        "persisted_shot": {"status": sh2.video.status, "video_id": sh2.video.video_id,
                           "model": sh2.video.model},
        "submit_ts": [t_submit0, t_submit1],
        "note": "进程将保持存活等待被硬杀（taskkill）；此后恢复进程只能查询同一 video_id。",
    }
    Path("_p48_a10_stepA_evidence.json").write_text(
        json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    print("A10-STEP-A-READY pid=%s video_id=%s" % (os.getpid(), video_id))
    await asyncio.sleep(1800)   # 等待被硬杀


if __name__ == "__main__":
    asyncio.run(main())
