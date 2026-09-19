# -*- coding: utf-8 -*-
"""Phase D2 — 当前 52 镜状态仪表（selected 候选 + 最近一次 review 结论 + 批次数）。"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
PROJ = ROOT / "projects" / "Golden_Full_Production_V1"


def main() -> int:
    from src.core.store import ProjectStore
    p = ProjectStore(PROJ).load()
    reviews = {}
    jl = PROJ / "output" / "agents" / "director" / "shot_reviews.jsonl"
    if jl.exists():
        for line in jl.read_text(encoding="utf-8").splitlines():
            try:
                l = json.loads(line)
            except Exception:
                continue
            sid = l.get("shot_id")
            src = str(l.get("image_source") or "")
            if src == "explicit":          # A/B 评测的候选审查，不是 selected 审查
                continue
            reviews[sid] = l               # 后写覆盖 = 最新
    now = time.time()
    rows = []
    for s in sorted(p.all_shots(), key=lambda x: (x.scene_id, x.index)):
        r = reviews.get(s.shot_id)
        d = (r or {}).get("derived") or {}
        age_min = (now - r["ts"]) / 60 if r else -1
        rows.append((s.shot_id, s.shot_size, len(s.first_frame_batches),
                     (s.selected_first_frame or "-")[-12:],
                     d.get("overall", "未审"), ",".join(d.get("hard_fails", [])) or "-",
                     f"{age_min:.0f}" if r else "-"))
    print(f"{'shot':10} {'size':20} {'b':>2} {'selected':14} {'overall':10} hard_fails {'review_age_min':>8}")
    for r in rows:
        print(f"{r[0]:10} {r[1]:20} {r[2]:2} {r[3]:14} {r[4]:10} {r[5][:44]:44} {r[6]:>8}")
    from collections import Counter
    c = Counter(r[4] for r in rows)
    print("\nsummary:", dict(c))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
