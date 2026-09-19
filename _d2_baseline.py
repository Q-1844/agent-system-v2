# -*- coding: utf-8 -*-
"""Phase D2 (30.md §2) — Before baseline freeze。

对 Golden_Full_Production_V1 建 evidence index：
- 52 镜逐镜：creative intent 摘要 / selected first & last frame（candidate_id + batch +
  文件 sha256）/ 全部历史候选路径 / binding 概要 / generation_basis / 视频状态。
- 项目级：profile / looks / props / sciv / bindings 计数。
任何后续删除或改动既有差图都会被哈希检出（30.md：差图是本轮最重要 regression fixture）。
只读：不写 project canonical。输出 _phase_d2_baseline/baseline_index.json。
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJ = ROOT / "projects" / "Golden_Full_Production_V1"
OUT = ROOT / "_phase_d2_baseline"


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def asset_entry(asset) -> dict:
    local = getattr(asset, "local", None)
    entry = {"url": getattr(asset, "url", None), "local": local}
    if local:
        p = Path(local)
        if not p.is_absolute():
            p = ROOT / p
        if p.exists():
            entry["sha256"] = sha256_of(p)
            entry["bytes"] = p.stat().st_size
        else:
            entry["missing"] = True
    return entry


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from src.core.store import ProjectStore

    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    store = ProjectStore(PROJ)
    project = store.load()

    bdoc = json.loads((PROJ / "output" / "shot_production_bindings.json").read_text(encoding="utf-8"))
    bindings = bdoc.get("bindings", {})

    shots = []
    missing_files = []
    for shot in sorted(project.all_shots(), key=lambda s: (s.scene_id, s.index)):
        sb = bindings.get(shot.shot_id) or {}

        def batch_index(batches):
            out = []
            for b in batches or []:
                out.append({
                    "batch_id": b.batch_id,
                    "created_at": b.created_at,
                    "status": b.status,
                    "generation_basis": dict(getattr(b, "generation_basis", None) or {}),
                    "candidates": [
                        {"candidate_id": c.candidate_id, "local": c.local, "url": c.url}
                        for c in (b.candidates or [])
                    ],
                })
            return out

        sel_first = {}
        for b in shot.first_frame_batches or []:
            for c in b.candidates or []:
                if c.candidate_id == shot.selected_first_frame:
                    sel_first = {"candidate_id": c.candidate_id, "batch_id": b.batch_id,
                                 **asset_entry(c)}
        sel_last = {}
        for b in shot.last_frame_batches or []:
            for c in b.candidates or []:
                if c.candidate_id == shot.selected_last_frame:
                    sel_last = {"candidate_id": c.candidate_id, "batch_id": b.batch_id,
                                **asset_entry(c)}
        for tag, e in (("first", sel_first), ("last", sel_last)):
            if e.get("missing"):
                missing_files.append({"shot_id": shot.shot_id, "slot": tag, "local": e.get("local")})

        vef = asset_entry(shot.video_end_frame) if shot.video_end_frame and (
            shot.video_end_frame.local or shot.video_end_frame.url) else {}

        shots.append({
            "shot_id": shot.shot_id,
            "scene_id": shot.scene_id,
            "index": shot.index,
            "shot_size": shot.shot_size,
            "camera_angle": shot.camera_angle,
            "camera": shot.camera,
            "composition": shot.composition,
            "characters": list(shot.characters or []),
            "dialogue": shot.dialogue,
            "description": shot.description,
            "opening_state": shot.opening_state,
            "link_mode": shot.link_mode,
            "opening_source": shot.opening_source,
            "source_shot_id": shot.source_shot_id,
            "selected_first_frame": sel_first,
            "selected_last_frame": sel_last,
            "first_frame_batches": batch_index(shot.first_frame_batches),
            "last_frame_batches": batch_index(shot.last_frame_batches),
            "video_versions_count": len(shot.video_versions or []),
            "final_video": shot.final_video,
            "video_end_frame": vef,
            "binding_present": shot.shot_id in bindings,
            "binding_updated_at": sb.get("updated_at"),
        })

    ss = json.loads((PROJ / "output" / "scene_system.json").read_text(encoding="utf-8"))
    rdoc = json.loads((PROJ / "output" / "resources.json").read_text(encoding="utf-8"))
    scivs = ss.get("scene_instance_views", {})
    index = {
        "baseline": "phase_d2_before",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "project": "Golden_Full_Production_V1",
        "spec": "详细流程文件/修复阶段/30.md §2",
        "project_counts": {
            "shots": len(shots),
            "bindings": len(bindings),
            "looks": len(rdoc.get("looks", {})),
            "props": len(rdoc.get("props", {})),
            "compositions": len(rdoc.get("compositions", {})),
            "reference_assets": len(rdoc.get("reference_assets", {})),
            "scene_instance_views_total": len(scivs),
            "scene_instance_views_approved": sum(
                1 for v in scivs.values() if v.get("approval_status") == "approved"),
        },
        "shots": shots,
        "missing_selected_files": missing_files,
    }
    (OUT / "baseline_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    # 可读摘要
    lines = ["# Phase D2 Before baseline（30.md §2 冻结）", "",
             f"created_at: {index['created_at']}", ""]
    lines.append("| shot | size | cast | first(candidate) | last(candidate) | video |")
    lines.append("|---|---|---|---|---|---|")
    for s in shots:
        f1 = s["selected_first_frame"].get("candidate_id", "-")
        l1 = s["selected_last_frame"].get("candidate_id", "-")
        lines.append(f"| {s['shot_id']} | {s['shot_size']} | {'/'.join(s['characters']) or '-'} "
                     f"| {f1} | {l1} | {s['video_versions_count']} |")
    lines.append("")
    lines.append(f"counts: {json.dumps(index['project_counts'], ensure_ascii=False)}")
    if missing_files:
        lines.append("")
        lines.append(f"**MISSING selected files**: {json.dumps(missing_files, ensure_ascii=False)}")
    (OUT / "BEFORE_BASELINE.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"shots={len(shots)} bindings={len(bindings)} "
          f"sciv_approved={index['project_counts']['scene_instance_views_approved']} "
          f"missing={len(missing_files)} elapsed={time.time()-t0:.1f}s")
    print(f"-> {OUT / 'baseline_index.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
