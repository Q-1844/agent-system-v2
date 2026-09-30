# -*- coding: utf-8 -*-
"""Phase 44A·s02_c03 路线甲：镜头跟随·盲区揭露（用户建议的首选机制）。

用户裁定：早期测试的镜头跟随是成立的——未知区域可以自由创建；上一轮的失败是门
出现在「已经照到的墙上」（凭空）。本路线把行走方向明确指向首帧右侧盲区：人物朝
画面右侧房间深处走，镜头向右跟随平移，拱形门随镜头移动在新显露的墙面上出现
（合理揭露），然后完成开门动作。

状态恢复（回到第 5 轮哨兵形态）：
  link_mode multi + video_reference_asset_id=prev_tail_look_scene +
  opening_source=independent（保留 source_shot_id 记录邻接）。
首帧=c02 尾帧接力（画面无门、场景零变动——本路线的前提）。
生成 2 支视频（multi 三参考：尾帧+Look+SIV）。
"""
import asyncio
import io
import sys
import time
import uuid
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
PROJ = "雨灯归航"
TARGET = "s02_c03"

DESC_V3 = (
    "阿獭从修理间工作台抱起发亮的铜灯，转身走向画面右侧的房间深处——那是开场画面"
    "没有照到的方向，镜头向右跟随平移。随着人物前行，拱形原木门（石砌门框+黑色铁铰链"
    "+圆形门把手）随镜头移动自然显露在人物面前，门是关着的。阿獭在门前停步，伸出爪子"
    "握住门把手，用力拉开门——门外深蓝夜色涌入，远处山坡上灯塔剪影清晰可见。阿獭抱灯"
    "停在敞开的门口面向门外，左脸颊白色三角毛纹可见。雨丝可接受。铜灯亮着，灯光照亮"
    "阿獭的毛发，与门外冷蓝夜色形成暖冷对比。除随镜头移动新显露的这一扇门外，不出现"
    "第二扇门或新增道具。"
)


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
        from src.core.models import OpeningSource
        pdir = deps.project_manager.get_project_dir(PROJ).resolve()
        store = ProjectStore(pdir)

        def _mut(s):
            s.link_mode = "multi"
            s.video_reference_asset_id = "prev_tail_look_scene"
            s.opening_source = OpeningSource.INDEPENDENT.value
            s.source_version = None            # 路线甲无硬接力依赖（哨兵动态解析尾帧）
            s.first_frame_prompt_override = None   # 防泄漏（视频提示词与之无关，双保险）
            s.description = DESC_V3
        store.update_shot(TARGET, _mut)
        proj = store.load()
        sh = proj.get_shot(TARGET)
        print(f"state: link_mode={sh.link_mode} sentinel={sh.video_reference_asset_id} "
              f"opening={sh.opening_source} | description v3 set（{len(DESC_V3)} chars）")

        marker = f"p44a_follow_{uuid.uuid4().hex[:8]}"
        jobs = [q.submit(PROJ, "video", TARGET, origin_run_id=marker) for _ in range(2)]
        t0 = time.time()
        done = set()
        while len(done) < len(jobs) and time.time() - t0 < 1500:
            await asyncio.sleep(12)
            for j in jobs:
                if j.job_id in done:
                    continue
                jj = q.get(j.job_id) if hasattr(q, "get") else None
                st = getattr(jj, "state", None)
                if st in ("done", "failed"):
                    done.add(j.job_id)
                    print(f"  [{int(time.time()-t0)}s] job {j.job_id} {st}"
                          + ("" if st == "done" else f": {str(getattr(jj,'error',''))[:160]}"))
        print(f"jobs settled {len(done)}/{len(jobs)} in {int(time.time()-t0)}s")
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
