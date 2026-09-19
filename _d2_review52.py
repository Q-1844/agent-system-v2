# -*- coding: utf-8 -*-
"""Phase D2 (30.md §13/§18) — 52 镜 Before 全量 review_shot + fixture 真机验证。

对 52 镜 selected first frame 逐镜跑真实 Agnes Vision 的 review_shot_core（14 维度，
deterministic overall），产出 §18 质量矩阵；同时对 _phase_d2_fixtures/ 的已知差图
fixture 跑真实审查，把 confirmed_verdict 写回 registry（30.md §13：保存经真实
Agnes Vision 确认过的 fixture verdict，供 deterministic derivation 单测）。

输出：_phase_d2_audit/review52_before.json + REVIEW52_MATRIX.md
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
PROJ = ROOT / "projects" / "Golden_Full_Production_V1"
OUT = ROOT / "_phase_d2_audit"

CONCURRENCY = 3


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        import os
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


async def main() -> int:
    t0 = time.time()
    load_env()
    from src.core.store import ProjectStore
    from src.pipeline.controller import PipelineController
    from src.agents.shot_review import review_shot_core

    ctrl = PipelineController(str(ROOT / "config" / "config.yaml"))
    client = ctrl.agnes_client
    project = ProjectStore(PROJ).load()

    results = {}
    sem = asyncio.Semaphore(CONCURRENCY)

    async def do_shot(shot):
        async with sem:
            try:
                rv = await review_shot_core(client, project_dir=PROJ, proj=project,
                                            shot=shot, frame="first")
                rv["scene_reference_mode"] = "before"
            except Exception as e:
                rv = {"error": repr(e), "shot_id": shot.shot_id}
        results[shot.shot_id] = rv
        d = rv.get("derived") or {}
        print(f"  {shot.shot_id}: {d.get('overall', 'ERR ' + rv.get('error', '')[:50])}"
              f" {d.get('hard_fails', '')}", flush=True)

    shots = sorted(project.all_shots(), key=lambda s: (s.scene_id, s.index))
    print(f"reviewing {len(shots)} selected first frames ...")
    await asyncio.gather(*[do_shot(s) for s in shots])

    # ---- fixture 真机验证 ----
    fix_path = ROOT / "_phase_d2_fixtures" / "regression_fixtures.json"
    reg = json.loads(fix_path.read_text(encoding="utf-8"))
    print("fixture verification ...")
    for fx in reg["fixtures"]:
        sid = fx["origin"]["shot_id"]
        shot = project.get_shot(sid)
        img = ROOT / "_phase_d2_fixtures" / fx["file"]
        try:
            rv = await review_shot_core(client, project_dir=PROJ, proj=project,
                                        shot=shot, frame="first",
                                        image_local=str(img))
            verdict = {"overall": rv["derived"]["overall"],
                       "hard_fails": rv["derived"]["hard_fails"],
                       "soft_fails": rv["derived"]["soft_fails"],
                       "observed": rv["review"]["observed"],
                       "feedback": rv["review"]["director_feedback"]}
            fx["confirmed_verdict"] = verdict
            print(f"  {fx['id']}: {verdict['overall']} hard={verdict['hard_fails']}")
        except Exception as e:
            fx["confirmed_verdict"] = {"error": repr(e)}
            print(f"  {fx['id']}: ERR {repr(e)[:80]}")
    fix_path.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")

    (OUT / "review52_before.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- §18 质量矩阵 ----
    def classify(rv, shot):
        d = rv.get("derived")
        if not d:
            return "REVIEW_ERROR"
        if d["overall"] == "pass":
            return "PASS"
        hf = d["hard_fails"]
        if "duplicate_identity" in hf or "unexpected_people" in hf:
            return "NEEDS_CAST_FIX"
        if "shot_size_correctness" in hf or "framing_and_composition" in hf:
            return "NEEDS_REFRAME"
        if any(k in hf for k in ("missing_character", "anatomy_artifact")):
            return "NEEDS_REGENERATE"
        if d["soft_fails"] or d["uncertains"]:
            return "NEEDS_REVIEW"
        return d["overall"].upper()

    matrix = []
    stats = defaultdict(list)
    for s in shots:
        rv = results.get(s.shot_id) or {}
        cat = classify(rv, s)
        stats[cat].append(s.shot_id)
        d = rv.get("derived") or {}
        matrix.append({"shot_id": s.shot_id, "shot_size": s.shot_size,
                       "cast": list(s.characters or []), "category": cat,
                       "overall": d.get("overall"),
                       "hard_fails": d.get("hard_fails", []),
                       "soft_fails": d.get("soft_fails", []),
                       "uncertains": d.get("uncertains", []),
                       "feedback": (rv.get("review") or {}).get("director_feedback", [])})
    lines = ["# Phase D2 §18 — 52 镜 Before 质量矩阵（review_shot 真机）", "",
             f"审查时间：{time.strftime('%Y-%m-%d %H:%M')}；对象=各镜当前 selected first frame", ""]
    for cat in ("PASS", "NEEDS_REFRAME", "NEEDS_CAST_FIX", "NEEDS_REGENERATE",
                "NEEDS_REVIEW", "REVIEW_ERROR"):
        ids = stats.get(cat, [])
        if ids:
            lines.append(f"- **{cat}**: {len(ids)} — {' '.join(ids)}")
    lines += ["", "| shot | size | 类别 | hard fail | soft fail | 反馈摘要 |", "|---|---|---|---|---|---|"]
    for m in matrix:
        fb = (m["feedback"] or [""])[0][:60]
        lines.append(f"| {m['shot_id']} | {m['shot_size']} | {m['category']} | "
                     f"{', '.join(m['hard_fails']) or '-'} | {', '.join(m['soft_fails']) or '-'} | {fb} |")
    (OUT / "REVIEW52_MATRIX.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: len(v) for k, v in stats.items()}, ensure_ascii=False))
    print(f"elapsed={time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
