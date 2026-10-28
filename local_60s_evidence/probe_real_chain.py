#!/usr/bin/env python3
"""C2 本地 60 秒验证 · 真实链路连通性探针（先于正片运行）。

只做 4 件事，逐项记录耗时，用于校准真机时序：
  1. Agnes 文本对话（验证 api_key / base_url / 模型可用）
  2. Agnes 生图 1K 16:9（验证图片端点）
  3. github_pages 图床托管（验证公网 URL 可被 Agnes 侧下载）+ 计时（Pages build 成本）
  4. Agnes 视频提交 + 轮询 + 下载（验证视频端点，并测出真实生成时长）

不触发任何 GitHub Actions / Release / A 仓写入；仅可能向独立的 frame-host 素材仓上传 1 个测试图。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # 代码根目录
sys.path.insert(0, str(ROOT))

# ---- 加载 .env（与 api_server.py 同一套语义）----
_env_path = ROOT / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k and v and not os.environ.get(k):
                os.environ[k] = v

import yaml  # noqa: E402

from src.api.agnes_client import AgnesClient  # noqa: E402
from src.core.image_host import build_image_host  # noqa: E402

OUT = ROOT / "local_60s_evidence"
OUT.mkdir(parents=True, exist_ok=True)
report: dict = {"ts_start": time.time()}


def log(*a):
    print(*a, flush=True)


async def main() -> int:
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    agnes_cfg = dict(cfg["agnes"])
    agnes_cfg["api_key"] = os.environ.get("AGNES_API_KEY", "")
    if not agnes_cfg["api_key"]:
        log("FATAL: AGNES_API_KEY 未配置")
        return 2
    client = AgnesClient(agnes_cfg)

    # ---------- 1. 文本 ----------
    t0 = time.time()
    try:
        raw = await client.chat(
            [{"role": "system", "content": "你是一个只输出 JSON 的助手。"},
             {"role": "user", "content": "输出 {\"ok\":true}"}],
            json_mode=True, temperature=0.1, max_tokens=64)
        report["text"] = {"ok": True, "seconds": round(time.time() - t0, 2),
                          "sample": str(raw)[:120]}
        log(f"[1] 文本 OK  {report['text']['seconds']}s  -> {str(raw)[:80]}")
    except Exception as e:
        report["text"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
        log(f"[1] 文本 FAIL {e}")
        return 3

    # ---------- 2. 生图 ----------
    img_path = OUT / "probe_first_frame.png"
    t0 = time.time()
    try:
        res = await client.generate_image(
            prompt="cinematic underwater scene, deep blue ocean, a small orange "
                   "submersible with twin spotlights descending toward a black "
                   "rock cliff, volumetric light beams, no text, no watermark",
            size_tier="1K", ratio="16:9")
        url = res.get("url")
        if not url:
            raise RuntimeError(f"no url in response: {str(res)[:200]}")
        await client.download(url, img_path)
        report["image"] = {"ok": True, "seconds": round(time.time() - t0, 2),
                           "url": url, "local": str(img_path),
                           "bytes": img_path.stat().st_size}
        log(f"[2] 生图 OK  {report['image']['seconds']}s  {img_path.stat().st_size}B")
    except Exception as e:
        report["image"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
        log(f"[2] 生图 FAIL {e}")
        return 4

    # ---------- 3. 图床托管 ----------
    host_cfg = dict(cfg.get("image_host") or {})
    for sect in ("github_pages", "imgbb"):
        sub = host_cfg.get(sect) or {}
        for k, v in list(sub.items()):
            if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                sub[k] = os.environ.get(v[2:-1], "")
    host = build_image_host(host_cfg, cache_path=str(ROOT / "cache" / "imgbb_cache.json"))
    report["image_host_kind"] = host_cfg.get("kind")
    t0 = time.time()
    try:
        public_url = await host.upload(img_path)
        report["host"] = {"ok": True, "seconds": round(time.time() - t0, 2),
                          "kind": host_cfg.get("kind"), "public_url": public_url}
        log(f"[3] 图床({host_cfg.get('kind')}) OK  {report['host']['seconds']}s  {public_url}")
    except Exception as e:
        report["host"] = {"ok": False, "kind": host_cfg.get("kind"),
                          "seconds": round(time.time() - t0, 2),
                          "error": f"{type(e).__name__}: {e}"[:400]}
        log(f"[3] 图床 FAIL {e}")
        return 5

    # 公网可达性（模拟 Agnes 服务端拉取）
    import httpx
    try:
        async with httpx.AsyncClient(timeout=40, follow_redirects=True) as c:
            r = await c.get(public_url)
        report["host"]["public_reachable"] = (r.status_code == 200)
        report["host"]["public_status"] = r.status_code
        report["host"]["public_bytes"] = len(r.content)
        log(f"[3b] 公网可达 HTTP {r.status_code}  {len(r.content)}B")
    except Exception as e:
        report["host"]["public_reachable"] = False
        report["host"]["public_error"] = str(e)[:200]
        log(f"[3b] 公网可达 FAIL {e}")

    # ---------- 4. 视频 ----------
    t_submit = time.time()
    try:
        created = await client.create_video(
            prompt="A small orange submersible with twin spotlights drifts slowly "
                   "forward through deep blue water toward a black rock cliff, "
                   "light beams cutting through the water, slow steady motion",
            mode="i2v", image_url=public_url, duration_s=4)
        vid = created.get("video_id") or created.get("task_id") or created.get("id")
        report["video_submit"] = {"ok": True, "seconds": round(time.time() - t_submit, 2),
                                  "provider_task_id": vid, "model": created.get("model"),
                                  "seconds_requested": created.get("seconds")}
        log(f"[4] 视频已提交 {report['video_submit']['seconds']}s  task={vid}")

        async def on_prog(pct, st, waited):
            log(f"    poll {st} {pct}% waited={int(waited)}s")
        res = await client.poll_video(vid, interval=5, on_progress=on_prog,
                                      model_name=agnes_cfg["models"]["video"])
        gen = round(time.time() - t_submit, 2)
        vpath = OUT / "probe_video.mp4"
        await client.download(res["url"], vpath)
        report["video"] = {"ok": True, "submit_to_complete_s": gen,
                           "seconds_returned": res.get("seconds"),
                           "size": res.get("size"), "local": str(vpath),
                           "provider_task_id": vid}
        log(f"[4] 视频完成 提交->完成 {gen}s  时长={res.get('seconds')}s  {vpath.stat().st_size}B")
    except Exception as e:
        report["video"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:400],
                           "submit_to_complete_s": round(time.time() - t_submit, 2),
                           "provider_task_id": vid if 'vid' in dir() else None}
        log(f"[4] 视频 FAIL {e}")

    report["total_seconds"] = round(time.time() - report["ts_start"], 2)
    (OUT / "probe_real_chain.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log("=== probe summary ===")
    log(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
