# -*- coding: utf-8 -*-
"""Phase 44A·s02_c03 融合首帧视频生成（执行侧机械步骤接管）。

背景：Agent 两轮未完成 link_mode multi→i2v 翻转（multi 模式构建不消费 boundary
frames——video_adapter.py:489 注释原文），导致前两支视频在无首帧状态下直生。
决策已定（选 ff_1e20bdb8_1 融合首帧），本脚本只做机械执行：
  1) link_mode: multi → i2v（BOUND 构建由此消费 selected_first_frame 硬边界）；
  2) dry-run build_bound_video_input 验证首帧解析+freshness（不提交不落库）；
  3) 经同一 JobQueue（cache/_44a_jobs.json）提交 2 支视频（kind=video，
     与 shot.generate_videos 工具同参：origin_run_id 标记）并等待完成。
不执行 set-final；配额真实口径 +10s。
"""
import asyncio
import io
import json
import sys
import time
import uuid
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
PROJ = "雨灯归航"
SID = "s02_c02"          # 来源镜（描述依赖，仅记录）
TARGET = "s02_c03"


async def main() -> int:
    import os
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    import deps
    from src.core.rate_limiter import RateLimiter
    from src.core.quota import QuotaLedger
    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    RateLimiter.get((cfg.get("agnes") or {}).get("rpm"))
    QuotaLedger.get_default(ROOT / "cache" / "quota.json", limits=cfg.get("quota"))
    import routers.shots  # noqa: F401
    from src.pipeline.controller import PipelineController
    deps.pipeline_controller = PipelineController(str(ROOT / "config" / "config.yaml"))
    deps.agnes_client = deps.pipeline_controller.agnes_client
    deps.project_manager = deps.pipeline_controller.project_manager
    from src.core.job_queue import JobQueue
    q = JobQueue(ROOT / "cache" / "_44a_jobs.json", deps.JOB_HANDLERS)
    deps.job_queue = q
    await q.start()
    from services.lifecycle import recover_pending_videos
    await recover_pending_videos(queue=q)
    try:
        from src.core.store import ProjectStore
        pdir = deps.project_manager.get_project_dir(PROJ).resolve()
        store = ProjectStore(pdir)

        # 1) link_mode 翻转（唯一的 fused 路径闸门）
        def _mut(s):
            s.link_mode = "i2v"
        store.update_shot(TARGET, _mut)
        proj = store.load()
        sh = proj.get_shot(TARGET)
        print(f"link_mode -> {sh.link_mode} | selected_first_frame={sh.selected_first_frame}")

        # 2) dry-run 构建验证（首帧解析 + freshness，不提交）
        from src.generation.video_adapter import build_bound_video_input
        bv = build_bound_video_input(pdir, store.load(), sh)
        print(f"dry-run: provider_mode={bv.provider_mode} | first_frame={bv.first_frame[:80]}")
        if not bv.first_frame:
            print("首帧未解析——中止（不提交）")
            return 2
        from src.core.models import Shot as _S  # noqa: F401  确认融合帧 URL 即选定候选
        sel_batch, sel_cand = None, None
        for b in sh.first_frame_batches or []:
            for c in b.candidates or []:
                if c.candidate_id == sh.selected_first_frame:
                    sel_batch, sel_cand = b, c
        assert sel_cand is not None and sel_cand.url == bv.first_frame, \
            "构建首帧 ≠ 选定融合候选——selection truth 失效，中止"

        # 3) 提交 2 支视频（与 shot.generate_videos 工具同参）
        marker = f"p44a_fused_{uuid.uuid4().hex[:8]}"
        jobs = [q.submit(PROJ, "video", TARGET, origin_run_id=marker) for _ in range(2)]
        t0 = time.time()
        done_ids = set()
        while len(done_ids) < len(jobs) and time.time() - t0 < 1500:
            await asyncio.sleep(10)
            for j in jobs:
                jj = q.get(j.job_id) if hasattr(q, "get") else None
                st = getattr(jj, "state", None)
                if st == "done" and j.job_id not in done_ids:
                    done_ids.add(j.job_id)
                    print(f"  [{int(time.time()-t0)}s] job {j.job_id} done")
                elif st == "failed" and j.job_id not in done_ids:
                    done_ids.add(j.job_id)
                    err = str(getattr(jj, "error", ""))[:200]
                    print(f"  [{int(time.time()-t0)}s] job {j.job_id} FAILED: {err}")
        print(f"jobs settled: {len(done_ids)}/{len(jobs)} in {int(time.time()-t0)}s")

        # 复核
        proj = store.load()
        sh = proj.get_shot(TARGET)
        print("final now:", sh.final_video)
        for v in (sh.video_versions or [])[-2:]:
            print(" ", v.version_id, "| task:", v.task_id)
        return 0
    finally:
        stop = getattr(q, "stop", None)
        if stop:
            await stop()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
