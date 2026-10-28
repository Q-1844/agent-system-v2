#!/usr/bin/env python3
"""C2 本地 60 秒验证 · 探针 3：视频端点 + 真实生成时长 + github.io 公网可达性。

用探针 1 已上传并确认 200 的 github_pages URL 作为首帧输入：
  · 若视频成功 -> Agnes 服务端能拉到 github.io 图（尾帧接力链路可行的前提）
  · 记录 submit->complete 真实耗时 -> 用于判断 RPM=1 下两路在途能否真实重叠
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k and v and not os.environ.get(k):
            os.environ[k] = v

import yaml  # noqa: E402

from src.api.agnes_client import AgnesClient  # noqa: E402

OUT = ROOT / "local_60s_evidence"
FRAMES = [
    # 探针 1 的图（已确认 github.io 200）
    "https://q-1844.github.io/agnes-frame-host/objects/2242daf836e9d0a2.png",
]


def log(*a):
    print(*a, flush=True)


async def main() -> int:
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    agnes_cfg = dict(cfg["agnes"])
    agnes_cfg["api_key"] = os.environ.get("AGNES_API_KEY", "")
    client = AgnesClient(agnes_cfg)
    report: dict = {"ts": time.time()}

    for url in FRAMES:
        t_submit = time.time()
        vid = None
        try:
            created = await client.create_video(
                prompt="A small orange submersible with twin spotlights drifts slowly forward "
                       "through deep blue water toward a black rock cliff, light beams cutting "
                       "through the water, slow steady cinematic motion, no text",
                mode="i2v", image_url=url, duration_s=5)
            vid = created.get("video_id") or created.get("task_id") or created.get("id")
            submit_at = time.time()
            log(f"提交完成 {submit_at - t_submit:.2f}s task={vid} resp={created}")

            async def on_prog(pct, st, waited):
                log(f"  poll {st} {pct}% waited={int(waited)}s")
            res = await client.poll_video(vid, interval=5, on_progress=on_prog,
                                          model_name=agnes_cfg["models"]["video"])
            done_at = time.time()
            vpath = OUT / "probe_video.mp4"
            await client.download(res["url"], vpath)
            report["video"] = {
                "ok": True,
                "provider_task_id": vid,
                "submit_seconds": round(submit_at - t_submit, 2),
                "submit_to_complete_s": round(done_at - submit_at, 2),
                "seconds_returned": res.get("seconds"),
                "size": res.get("size"),
                "local": str(vpath),
                "bytes": vpath.stat().st_size,
            }
            log(f"完成 提交->完成 {report['video']['submit_to_complete_s']}s "
                f"时长={res.get('seconds')}s {vpath.stat().st_size}B")
        except Exception as e:
            report["video"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:500],
                               "provider_task_id": vid,
                               "elapsed_s": round(time.time() - t_submit, 2)}
            log(f"FAIL {e}")

    (OUT / "probe_video_timing.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
