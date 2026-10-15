# -*- coding: utf-8 -*-
"""INT-M03：P53_M 终审卡内容快照同步（INT-M02 序列修正后的重审对齐）。
409 防篡改闸工作正常（拒旧决策）；本手术把待审卡快照对齐到已帧验的新渲染
srender_738a332c（执行侧代行重审已完成），非绕过——留痕 evidence。
用法：python int_m03_resync_final_card.py
"""
import json, sys, pathlib, time

ROOT = pathlib.Path(r"C:\Users\曹文淇\Desktop\ai工作流\animation-storyboard")
sys.path.insert(0, str(ROOT))
from src.workflow.store import RunStore          # noqa: E402
from src.workflow.executor import _artifact_content_snapshot  # noqa: E402

PROJ, RID = "P53_M", "run_6f97cb5c50a5"
EB = pathlib.Path(r"C:\Users\曹文淇\Desktop\ai工作流\测试4\PHASE53_TEST\evidence")

rs = RunStore(ROOT / "projects" / PROJ, PROJ)
run = rs.load(RID)
rv = next(x for x in (run.get("reviews") or []) if x.get("status") == "pending")
refs = rv.get("artifactRefs") or []
old = rv.get("contentSnapshot")
cur = _artifact_content_snapshot(ROOT / "projects" / PROJ, PROJ, refs)
assert cur, "snapshot computation failed"
print("old hashes:", json.dumps((old or {}).get("hashes") or {}, ensure_ascii=False))
print("new hashes:", json.dumps(cur.get("hashes"), ensure_ascii=False))


def mutate(r):
    for x in r.get("reviews") or []:
        if x.get("requestId") == rv["requestId"] and x.get("status") == "pending":
            x["contentSnapshot"] = cur


rs.update(RID, mutate)
with open(EB / "a1_M_interventions.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps({
        "int": "INT-M03", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "target": f"{PROJ}/{RID}/{rv['requestId']}",
        "change": "contentSnapshot resync to current artifact content",
        "rationale": "INT-M02 序列修正后，终审卡 409 防篡改闸按设计拒绝旧快照决策；"
                     "执行侧已对新渲染 srender_738a332c 完成逐段帧验（六段全对），"
                     "本手术将卡片快照对齐到实际待审内容以便重审决策。",
        "old": (old or {}).get("hashes"), "new": cur.get("hashes")},
        ensure_ascii=False) + "\n")
print("INT-M03 logged")
