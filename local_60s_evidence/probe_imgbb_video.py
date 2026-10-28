#!/usr/bin/env python3
"""C2 本地 60 秒验证 · 探针 2：图床备选（imgbb）可达性 + 视频端点真机时序。

背景：探针 1 实测 github_pages 单次 Pages 构建 232s > 代码硬编码 180s 等待上限
      -> 尾帧托管必然抛 PagesNotReadyError，inherit_previous 接力断链。
本探针验证：
  A. imgbb 上传 + 公网可达
  B. Agnes 服务端能否拉到该 URL（用真实视频提交反证——视频成功即 Agnes 取到了图）
  C. 真实视频生成耗时（用于判断 RPM=1 下两路在途能否真实重叠）
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

import httpx  # noqa: E402
import yaml  # noqa: E402

from src.api.agnes_client import AgnesClient  # noqa: E402
from src.core.image_host import ImgbbHost  # noqa: E402

OUT = ROOT / "local_60s_evidence"
OUT.mkdir(parents=True, exist_ok=True)
report: dict = {}


def log(*a):
    print(*a, flush=True)


async def main() -> int:
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    agnes_cfg = dict(cfg["agnes"])
    agnes_cfg["api_key"] = os.environ.get("AGNES_API_KEY", "")
    client = AgnesClient(agnes_cfg)

    png = OUT / "probe_first_frame.png"
    imgbb_key = os.environ.get("IMGBB_API_KEY", "")
    if not imgbb_key:
        log("IMGBB_API_KEY 未配置")
        return 2

    # ---- A. imgbb 上传 ----
    host = ImgbbHost(imgbb_key, expiration=0, cache_path=str(ROOT / "cache" / "imgbb_cache.json"))
    t0 = time.time()
    try:
        url = await host.upload(png)
        report["imgbb"] = {"ok": True, "seconds": round(time.time() - t0, 2), "url": url}
        log(f"[A] imgbb 上传 OK {report['imgbb']['seconds']}s -> {url}")
    except Exception as e:
        report["imgbb"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
        log(f"[A] imgbb 上传 FAIL {e}")
        return 3

    # 公网可达（本机）
    try:
        async with httpx.AsyncClient(timeout=40, follow_redirects=True) as c:
            r = await c.get(url)
        report["imgbb"]["public_status"] = r.status_code
        report["imgbb"]["public_bytes"] = len(r.content)
        report["imgbb"]["public_reachable"] = r.status_code == 200
        log(f"[A2] imgbb 公网 HTTP {r.status_code} {len(r.content)}B")
    except Exception as e:
        report["imgbb"]["public_reachable"] = False
        report["imgbb"]["public_error"] = str(e)[:200]
        log(f"[A2] imgbb 公网 FAIL {e}")

    # ---- B/C. 真实视频提交（Agnes 必须能拉到上面这个 URL）----
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
        report["video_submit"] = {"ok": True, "provider_task_id": vid,
                                  "submit_seconds": round(submit_at - t_submit, 2),
                                  "response": {k: v for k, v in created.items()
                                               if k in ("model", "seconds", "status", "size")}}
        log(f"[B] 视频已提交 {report['video_submit']['submit_seconds']}s task={vid} resp={created}")

        async def on_prog(pct, st, waited):
            log(f"    poll {st} {pct}% waited={int(waited)}s")
        res = await client.poll_video(vid, interval=5, on_progress=on_prog,
                                      model_name=agnes_cfg["models"]["video"])
        done_at = time.time()
        vpath = OUT / "probe_video.mp4"
        await client.download(res["url"], vpath)
        report["video"] = {"ok": True,
                           "submit_to_complete_s": round(done_at - submit_at, 2),
                           "total_s": round(done_at - t_submit, 2),
                           "seconds_returned": res.get("seconds"),
                           "size": res.get("size"),
                           "local": str(vpath),
                           "provider_task_id": vid}
        log(f"[C] 视频完成 提交->完成 {report['video']['submit_to_complete_s']}s "
            f"时长={res.get('seconds')}s {vpath.stat().st_size}B")
    except Exception as e:
        report["video"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:500],
                           "provider_task_id": vid,
                           "elapsed_s": round(time.time() - t_submit, 2)}
        log(f"[B/C] 视频 FAIL {e}")

    (OUT / "probe_imgbb_video.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log("=== summary ===")
    log(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
