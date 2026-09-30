# -*- coding: utf-8 -*-
"""Phase 44A·s02_c03 修正轮：融合首帧 v2（门在人物身后的背景——用户基准构图）。

用户裁定（chat 2026-09-30）：早期测试镜头跟随时时门是成立的（首帧后方有门）；
目前生成的首帧后方都没门，所以自然有问题。本脚本执行：
  1) opening_source: independent → reference_previous（写 source_shot_id +
     source_version=vv_4bdf6b08，复刻 set_opening_source endpoint 语义），
     continuity 必须 ready 否则中止；
  2) first_frame_prompt_override 写起始状态构图（门在人物身后左后方背景、关闭、
     石砌门框+铁铰链；人物台前抱灯——与上镜尾帧衔接）；
  3) 经 JobQueue 生成 2 张融合首帧候选（BOUND 路径自动带 [SIV, Look, 上镜尾帧]，
     ref_layout=continuity_tail——EXPB 配方完整版）。
生成后暂停，由执行侧亲证选帧（不做选择、不生成视频）。
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

OVERRIDE = (
    "Storybook 2D animation film still, soft watercolor shading, clean lines. "
    "Inside a cozy stone-and-timber repair workshop at night. The small otter in an "
    "orange-yellow raincoat with a dark blue scarf stands behind the wooden workbench, "
    "facing the viewer, both paws holding the glowing brass lantern close to its chest "
    "on the bench in front of it. Directly BEHIND the otter, on the left background "
    "wall, the CLOSED arched wooden door with its stone arch frame, black iron hinges "
    "and round dark handle is clearly visible in full. A small four-pane window with "
    "rain streaks and dark blue night sky is set in the back wall to the right of the "
    "door. Warm amber lamplight contrasts with the cold blue night tones. The otter "
    "has a small white triangular fur patch on its left cheek. Rain streaks drift past "
    "the window outside. Exactly one door in the room, no extra furniture, no people, "
    "no second character."
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
        from src.core.models import OpeningSource, ShotStatus
        from routers.shots import continuity_status
        pdir = deps.project_manager.get_project_dir(PROJ).resolve()
        store = ProjectStore(pdir)
        proj = store.load()
        shot = proj.get_shot(TARGET)

        # 1) opening_source → reference_previous（复刻 endpoint：同场景上一镜+source_version）
        scene = next(s for s in proj.scenes if s.scene_id == shot.scene_id)
        ids = [s.shot_id for s in scene.shots]
        prev_id = ids[ids.index(TARGET) - 1]
        prev = proj.get_shot(prev_id)
        assert prev and prev.final_video and prev.video.status == ShotStatus.DONE.value, \
            "上一镜未完成——中止"
        sv = prev.final_video

        def _mut(s):
            s.opening_source = OpeningSource.REFERENCE_PREVIOUS.value
            s.source_shot_id = "s02_c02"
            s.source_version = sv
            s.link_mode = "i2v"                    # 融合路径闸门（multi 不消费硬首帧）
            s.video_reference_asset_id = None      # 清哨兵（场景锚定由融合帧承载）
        store.update_shot(TARGET, _mut)
        proj = store.load()
        status, msg = continuity_status(proj, proj.get_shot(TARGET))
        print(f"opening_source=reference_previous | source={prev_id} | src_ver={sv} "
              f"| continuity={status} {msg}")
        if status != "ready":
            print("continuity 未 ready——中止（不生成）")
            return 2

        # 2) override 写入
        def _mut2(s):
            s.first_frame_prompt_override = OVERRIDE
        store.update_shot(TARGET, _mut2)
        print(f"first_frame_prompt_override set ({len(OVERRIDE)} chars)")

        # 3) 生成 2 张融合首帧候选（同 shot.generate_images 工具的队列路径）
        marker = f"p44a_ffv2_{uuid.uuid4().hex[:8]}"
        job = q.submit(PROJ, "first_frame", TARGET, variants=2, origin_run_id=marker)
        t0 = time.time()
        while True:
            await asyncio.sleep(8)
            jj = q.get(job.job_id) if hasattr(q, "get") else None
            st = getattr(jj, "state", None)
            if st in ("done", "failed") or time.time() - t0 > 420:
                break
        st = getattr(jj, "state", None)
        print(f"first_frame job {job.job_id}: {st} in {int(time.time()-t0)}s")
        if st == "failed":
            print("error:", str(getattr(jj, "error", ""))[:300])
            return 2
        proj = store.load()
        sh = proj.get_shot(TARGET)
        b = sh.first_frame_batches[-1]
        print(f"batch {b.batch_id} status={b.status} candidates={len(b.candidates)}")
        for c in b.candidates:
            print("  ", c.candidate_id, c.url[:90] if c.url else c.local)
        print("STOP：请执行侧亲证后选择（不自动 select、不生成视频）")
        return 0
    finally:
        stop = getattr(q, "stop", None)
        if stop:
            await stop()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
