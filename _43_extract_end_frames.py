# -*- coding: utf-8 -*-
"""Phase 43：set-final 后补末帧提取+托管（endpoint 侧逻辑，业务函数不含）。
s02_c02 的尾帧是 s02_c03 第四轮（三参考哨兵）的接力来源，必须就绪。"""
import asyncio
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PROJ = "雨灯归航"
SHOTS = ["s01_c06", "s02_c02"]


async def main():
    import os
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    import deps
    from src.pipeline.controller import PipelineController
    deps.pipeline_controller = PipelineController(str(ROOT / "config" / "config.yaml"))
    deps.agnes_client = deps.pipeline_controller.agnes_client
    deps.project_manager = deps.pipeline_controller.project_manager
    from routers.shots import _extract_video_end_frame
    from src.core.store import ProjectStore
    pdir = deps.project_manager.get_project_dir(PROJ)
    store = ProjectStore(pdir)
    for sid in SHOTS:
        p = store.load()
        sh = p.get_shot(sid)
        vv = next((v for v in (sh.video_versions or [])
                   if v.version_id == sh.final_video), None)
        if vv is None or not vv.local:
            print(sid, "no version/local — skip")
            continue
        vfile = Path(vv.local)
        if not vfile.is_absolute():
            vfile = ROOT / vfile
        end_asset, ok = await _extract_video_end_frame(pdir, sid, str(vfile), vv.version_id)
        print(sid, "| extracted:", ok, "| end url:",
              str((end_asset.url if end_asset else ""))[:70],
              "| error:", str(getattr(end_asset, "error", None))[:200])
        if ok:
            store.update_shot(sid, lambda s, ea=end_asset, sv=vv.version_id: (
                setattr(s, "video_end_frame", ea),
                setattr(s, "video_end_source_version", sv)))

    # 复核
    p = store.load()
    for sid in SHOTS:
        sh = p.get_shot(sid)
        print(sid, "end_frame_ready =", sh.video_end_frame.url_ready,
              "| end url =", str(sh.video_end_frame.url or "")[:70],
              "| end_src_ver =", sh.video_end_source_version)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
