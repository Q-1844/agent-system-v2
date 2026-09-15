# _c1_native_av_e2e/regen_bound_frames.py
# 样本 G 前置：BOUND 边界帧显式重新生成（18.md §17 语义——boundary frame 相对
# 当前 binding revision 过期 → 显式 regenerate，绝不 auto refresh）。
# 走生产 _handler_first_frame / _handler_last_frame（真实 Agnes，variants=1），
# 生成后按用户选卡语义 select 新候选，再交 run_e2e.py 跑 BOUND 视频。
from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "_b1_dialogue_probe"))

import importlib.util as _ilu                                # noqa: E402
_spec = _ilu.spec_from_file_location("c1_run_e2e",
                                     Path(__file__).resolve().parent / "run_e2e.py")
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
boot = _mod.boot
from samples import BOUND_PROJECT_NAME, BOUND_SHOT           # noqa: E402


async def main():
    import deps
    from routers.shots import _handler_first_frame, _handler_last_frame
    from src.core.store import ProjectStore

    agnes = boot()
    pdir = deps.project_manager.get_project_dir(BOUND_PROJECT_NAME)
    store = ProjectStore(pdir)
    shot_id = BOUND_SHOT["shot_id"]

    async def emit(stage, msg, pct):
        print(f"  [{stage}] {str(msg)[:120]}", flush=True)

    # ---- first frame（BOUND 路径：validity gate → pack → adapter → refs）----
    await _handler_first_frame(SimpleNamespace(project=BOUND_PROJECT_NAME,
                                               shot_id=shot_id,
                                               params={"variants": 1},
                                               kind="first_frame"), emit)
    def _sel_ff(s):
        batch = s.first_frame_batches[-1]
        s.selected_first_frame = batch.candidates[-1].candidate_id
        s.first_frame.selected_index = len(batch.candidates) - 1
        s.first_frame.status = "pending"       # 等用户选卡语义（本脚本代选）
    store.update_shot(shot_id, _sel_ff)
    print("first frame regenerated + selected", flush=True)

    # ---- last frame（keyframes；构图基础=新首帧）----
    await _handler_last_frame(SimpleNamespace(project=BOUND_PROJECT_NAME,
                                              shot_id=shot_id,
                                              params={"variants": 1},
                                              kind="last_frame"), emit)
    def _sel_lf(s):
        batch = s.last_frame_batches[-1]
        s.selected_last_frame = batch.candidates[-1].candidate_id
        s.last_frame.selected_index = len(batch.candidates) - 1
        s.last_frame.status = "pending"
    store.update_shot(shot_id, _sel_lf)
    print("last frame regenerated + selected", flush=True)

    from src.core.store import ProjectStore as PS
    shot = PS(pdir).load().get_shot(shot_id)
    print(f"selected ff={shot.selected_first_frame} lf={shot.selected_last_frame}",
          flush=True)
    await agnes.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
