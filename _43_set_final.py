# -*- coding: utf-8 -*-
"""Phase 43 正式选片执行（用户裁定 2026-09-30 第三次评审）：
- s01_c06: 一轮候选 vv_5ad9192d（用户终选 1s 分拍——0.5s 分拍未胜出，对照结论以终选为准）
- s02_c02: 二轮候选 vv_1c4b7001（seed42 侧别正确版；三轮候选未被选）
走 set_final_video_business（set-final endpoint 的业务层，含末帧重提）。
"""
import asyncio
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PROJ = "雨灯归航"
PICKS = [("s01_c06", "vv_5ad9192d"), ("s02_c02", "vv_1c4b7001")]


async def main():
    import deps
    from src.pipeline.controller import PipelineController
    deps.pipeline_controller = PipelineController(str(ROOT / "config" / "config.yaml"))
    deps.agnes_client = deps.pipeline_controller.agnes_client
    deps.project_manager = deps.pipeline_controller.project_manager
    from routers.shots import set_final_video_business
    from src.core.store import ProjectStore
    pdir = deps.project_manager.get_project_dir(PROJ)
    store = ProjectStore(pdir)
    for sid, vid in PICKS:
        r = await set_final_video_business(store, pdir, sid, vid)
        print(sid, "->", vid, "| ok:", r.get("ok"), "| final:", r.get("final_video"),
              "| err:", r.get("error"))
    # 复核
    p = store.load()
    for sid, vid in PICKS:
        sh = p.get_shot(sid)
        print(sid, "final =", sh.final_video, "| end_src_ver =", sh.video_end_source_version,
              "| end_frame_ready =", sh.video_end_frame.url_ready)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
