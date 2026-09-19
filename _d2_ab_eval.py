# -*- coding: utf-8 -*-
"""Phase D2 (30.md §5) — A/B 评测 + Before/After 对比 Gallery。

输入：_phase_d2_ab/ab_runs.json（harness 产出）+ baseline_index.json（Before selected）。
对每张候选跑真实 Agnes Vision 的 review_shot_core（14 维度），汇总三组：
  Before（Phase D 既有 selected，旧参考行为）/ A（full，新预算）/ B（context_only，新预算）
输出：_phase_d2_ab/AB_EVAL.json + AB_GALLERY.html（逐镜 Before|A|B 并排 + 审查摘要）。
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
AB = ROOT / "_phase_d2_ab"

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


def esc(s) -> str:
    return (str(s or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def review_line(rv: dict) -> str:
    if rv is None:
        return "<i>（未审查）</i>"
    d = rv["derived"]
    r = rv["review"]
    hard = "、".join(d["hard_fails"]) or "—"
    soft = "、".join(d["soft_fails"]) or "—"
    people = r["observed"].get("people_count")
    size_obs = r["observed"].get("shot_size_observed")
    fb = r.get("director_feedback") or []
    fb_txt = "<br>".join("• " + esc(x) for x in fb[:3])
    return (f"<b class='ov-{d['overall']}'>{d['overall']}</b> | "
            f"人形={people} | 实际景别={esc(size_obs)}<br>"
            f"hard fail: {esc(hard)}<br>soft fail: {esc(soft)}<br>{fb_txt}")


def dims_badge(rv: dict) -> str:
    if rv is None:
        return ""
    d = rv["review"]["dimensions"]
    bad = {k: v["status"] for k, v in d.items() if v["status"] == "fail"}
    if not bad:
        return ""
    return "<span class='badge'>" + " ".join(
        f"{esc(k.split('_')[0])}:{esc(v[:1])}" for k, v in bad.items()) + "</span>"


async def main() -> int:
    t0 = time.time()
    load_env()
    from src.core.store import ProjectStore
    from src.pipeline.controller import PipelineController
    from src.agents.shot_review import review_shot_core

    runs = json.loads((AB / "ab_runs.json").read_text(encoding="utf-8"))
    ctrl = PipelineController(str(ROOT / "config" / "config.yaml"))
    client = ctrl.agnes_client
    project = ProjectStore(PROJ).load()

    # 组装评审队列：每镜 Before selected + A/B 候选
    queue = []  # (shot_id, slot_label, file_path, scene_mode_label)

    def selected_file(sid: str):
        idx = json.loads((ROOT / "_phase_d2_baseline" / "baseline_index.json")
                         .read_text(encoding="utf-8"))
        for s in idx["shots"]:
            if s["shot_id"] == sid:
                return ROOT / s["selected_first_frame"]["local"]
        return None

    for sid, _why in runs["shots"]:
        bf = selected_file(sid)
        if bf and bf.exists():
            queue.append((sid, "Before", bf, "before(legacy refs)"))
    for c in runs["candidates"]:
        queue.append((c["shot_id"], f"{c['mode']}_c{c['variant']}",
                      ROOT / c["file"], c["scene_reference_mode"]))

    print(f"review queue: {len(queue)} images")
    sem = asyncio.Semaphore(CONCURRENCY)
    results = {}

    async def do_one(sid, slot, path, mode_label):
        shot = project.get_shot(sid)
        async with sem:
            try:
                rv = await review_shot_core(client, project_dir=PROJ, proj=project,
                                            shot=shot, frame="first",
                                            image_local=str(path))
                rv["scene_reference_mode"] = mode_label
            except Exception as e:
                rv = {"error": repr(e), "shot_id": sid, "frame": "first",
                      "scene_reference_mode": mode_label}
        results[(sid, slot)] = rv
        status = rv.get("derived", {}).get("overall") or f"ERR {rv.get('error','')[:60]}"
        print(f"  {sid} {slot}: {status}")

    await asyncio.gather(*[do_one(*q) for q in queue])
    serial = {f"{sid}||{slot}": rv for (sid, slot), rv in results.items()}
    (AB / "AB_EVAL.json").write_text(json.dumps(serial, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    # ---- 汇总统计（§5 判定用）----
    agg = defaultdict(lambda: {"n": 0, "overall": defaultdict(int),
                               "dim_fail": defaultdict(int)})
    for (sid, slot), rv in results.items():
        mode = "Before" if slot == "Before" else slot.rsplit("_", 1)[0]
        a = agg[mode]
        d = rv.get("derived")
        if not d:
            continue
        a["n"] += 1
        a["overall"][d["overall"]] += 1
        for k, v in (rv["review"]["dimensions"]).items():
            if v["status"] == "fail":
                a["dim_fail"][k] += 1
    summary = {m: {"n": a["n"],
                   "overall": dict(a["overall"]),
                   "dim_fail": dict(sorted(a["dim_fail"].items(),
                                           key=lambda x: -x[1]))}
               for m, a in agg.items()}
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # ---- Gallery ----
    css = """
    body{font-family:system-ui,'Microsoft YaHei';background:#111;color:#ddd;margin:20px}
    .shot{background:#1b1b1b;border-radius:10px;padding:14px;margin-bottom:26px}
    .hd{font-size:15px;color:#fff;margin-bottom:10px}
    .hd small{color:#8ab4f8}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:12px}
    .cell{background:#222;border-radius:8px;padding:8px}
    .cell img{width:100%;border-radius:6px;display:block}
    .slot{font-weight:bold;color:#ffd866;margin:4px 0}
    .ov-pass{color:#7bd87b}.ov-needs_fix{color:#ff8a80}.ov-uncertain{color:#ffd866}
    .badge{color:#f28b82;font-size:11px}
    .meta{font-size:11px;color:#9e9e9e;margin-top:4px;word-break:break-all}
    """
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style></head><body>",
             "<h2 style='color:#fff'>Phase D2 — Scene Reference A/B（Before / Mode A full / Mode B context_only）</h2>"]
    for sid, why in runs["shots"]:
        shot = project.get_shot(sid)
        parts.append(f"<div class='shot'><div class='hd'>{esc(sid)} "
                     f"<small>[{esc(shot.shot_size)} / {esc(shot.camera_angle) or '-'} / "
                     f"cast: {esc('、'.join(shot.characters)) or '无'}]</small><br>"
                     f"<small>{esc(why)} — {esc(shot.description[:80])}</small></div><div class='grid'>")
        for slot in ("Before",) + tuple(f"{m}_c{i}" for m in ("full", "context_only")
                                        for i in (1, 2)):
            rv = results.get((sid, slot))
            if slot == "Before":
                img = selected_file(sid)
                if not (img and img.exists()):
                    continue
                rel = img.relative_to(ROOT).as_posix()
                label = "Before（Phase D 既有 selected / 旧参考行为）"
            else:
                cand = next(c for c in runs["candidates"]
                            if c["shot_id"] == sid and f"{c['mode']}_c{c['variant']}" == slot)
                rel = cand["file"]
                label = f"Mode {'A' if cand['mode']=='full' else 'B'}（scene_reference_mode={cand['scene_reference_mode']}, refs={cand['n_refs']}）"
            parts.append(f"<div class='cell'><div class='slot'>{esc(label)}</div>"
                         f"<img src='../{esc(rel)}' loading='lazy'>"
                         f"<div class='meta'>{review_line(rv)}</div>{dims_badge(rv)}</div>")
        parts.append("</div></div>")
    parts.append("</body></html>")
    (AB / "AB_GALLERY.html").write_text("\n".join(parts), encoding="utf-8")
    print(f"eval done elapsed={time.time()-t0:.0f}s -> AB_EVAL.json / AB_GALLERY.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
