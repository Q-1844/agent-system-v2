# -*- coding: utf-8 -*-
"""能力探针：硬首帧（i2v→2.5 keyframe）下模型能否在一段视频内完成空间转移。

用户质疑：固定首帧 ≠ 后续画面被锁死——镜头应可跟随人物移动切换场景
（例：楼梯间→二楼灯室）。已有反证只覆盖 reference 模式（语义参考锁空间，
s03_c03 修复实证）；硬首帧只钉起点，本探针实测是否可换空间。

首帧：s03_c02 中段楼梯间实帧（rehost 公网）。8s，Token Plan 免费档。
"""
import asyncio
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

OUT = BASE_DIR / "_tmp" / "scene_transition_probe"
START_FRAME = BASE_DIR / "_tmp" / "probe_start_stair.png"
PROMPT = (
    "Low-angle shot inside a stone lighthouse stairwell at night. The otter "
    "character in orange raincoat and blue scarf holds a glowing lantern and "
    "climbs the spiral stone stairs upward. The camera follows the otter from "
    "behind as it ascends, then passes through an open doorway at the top of "
    "the stairs and enters a round lamp room: enclosed circular wooden room "
    "with a large arched round window showing the night river below, a metal "
    "lamp pedestal in the center. The otter walks toward the lamp pedestal. "
    "Scene changes from stairwell to lamp room during the shot.")


def _load_env():
    env_file = BASE_DIR / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


async def main() -> int:
    _load_env()
    import deps
    from src.api.agnes_client import AgnesClient
    import yaml
    cfg = yaml.safe_load((BASE_DIR / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    ag = dict(cfg.get("agnes") or {})
    for k, v in list(ag.items()):  # controller 同款 ${VAR} env 替换
        if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
            ag[k] = os.environ.get(v[2:-1], "")
    client = AgnesClient(ag)
    OUT.mkdir(parents=True, exist_ok=True)

    host = deps.get_rehost_host()
    url = await host.upload(str(START_FRAME))
    print("start frame url:", str(url)[:90], flush=True)

    t0 = time.time()
    res = await client.create_video(prompt=PROMPT, mode="i2v",
                                    image_url=url, duration_s=8,
                                    model="agnes-video-2.5-flash")
    vid = res.get("video_id") or res.get("task_id") or ""
    print("submitted:", vid[:60], flush=True)
    r = await client.poll_video(vid, interval=5, max_wait=420,
                                model_name="agnes-video-2.5-flash")
    dest = str(OUT / "probe.mp4")
    await client.download(r["url"], dest)
    print(f"done in {int(time.time()-t0)}s -> {dest}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
