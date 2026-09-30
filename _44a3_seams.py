# -*- coding: utf-8 -*-
"""Phase 44A：12 个相邻剪辑点接缝测量（回拨+修复后的 canonical 现状）。"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
PROJ = "雨灯归航"


def main():
    import deps
    from src.pipeline.controller import PipelineController
    deps.pipeline_controller = PipelineController(str(ROOT / "config" / "config.yaml"))
    deps.agnes_client = deps.pipeline_controller.agnes_client
    deps.project_manager = deps.pipeline_controller.project_manager
    from src.core.store import ProjectStore
    from src.storyboard.continuity_service import measure_seam, _flat_shots
    pdir = deps.project_manager.get_project_dir(PROJ).resolve()
    store = ProjectStore(pdir)
    proj = store.load()
    order = [s.shot_id for s in _flat_shots(proj)]
    print("play order:", order)
    out = []
    for i in range(1, len(order)):
        sid = order[i]
        try:
            r = measure_seam(pdir, proj, sid, include_end_to_end=True)
            mae = r["tail_to_first_mae"]
            e2e = r.get("end_to_end_mae")
            print(f"{order[i-1]} -> {sid}: seam_mae={mae} end_to_end={e2e}")
            out.append({"from": order[i - 1], "to": sid,
                        "seam_mae": mae, "end_to_end_mae": e2e,
                        "left_src_ver": r["left"]["source_version"],
                        "right_src_ver": r["right"]["source_version"]})
        except Exception as e:
            print(f"{order[i-1]} -> {sid}: ERROR {type(e).__name__}: {e}")
            out.append({"from": order[i - 1], "to": sid, "error": str(e)})
    (ROOT / "_44a" / "a2_seams.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved -> _44a/a2_seams.json")


if __name__ == "__main__":
    main()
