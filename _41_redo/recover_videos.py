# -*- coding: utf-8 -*-
"""Phase 41 重测配套：手动触发 lifecycle.recover_pending_videos（driver 未挂该钩子）。

对 雨灯归航 中 video.status==generating 且有 video_id 的镜头续轮询收货
（不重提交、不重扣配额）。只读诊断 + 官方恢复路径，不绕过正式管线。
"""
import asyncio
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from _41_redo_run import _load_env, _bootstrap_deps, PROJECT  # noqa: E402

_load_env()


async def main() -> int:
    import deps
    deps, q = _bootstrap_deps()
    await q.start()
    try:
        from services.lifecycle import recover_pending_videos
        await recover_pending_videos()

        from src.core.store import ProjectStore
        pdir = Path(deps.project_manager.get_project_dir(PROJECT))
        store = ProjectStore(pdir)
        deadline = time.time() + 480
        while time.time() < deadline:
            p = store.load()
            gens = [sh.shot_id for sh in p.all_shots()
                    if getattr(sh.video, "status", "") in ("generating", "queued")]
            if not gens:
                break
            print(f"[wait] still generating/queued: {gens}", flush=True)
            await asyncio.sleep(15)

        p = store.load()
        print("=== final video status (雨灯归航) ===", flush=True)
        for sh in p.all_shots():
            v = sh.video
            vid = (getattr(v, "video_id", "") or "")[:36]
            print(f"  {sh.shot_id}: status={getattr(v, 'status', '')} "
                  f"video_id={vid}", flush=True)
        return 0
    finally:
        stop = getattr(q, "stop", None)
        if stop:
            await stop()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
