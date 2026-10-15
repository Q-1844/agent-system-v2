# -*- coding: utf-8 -*-
"""INT-F01：P53_F n_sc 完成契约 location minCount 2→1（夹具矛盾修正，D-05 先例）。
依据：F 场景目标文本自述单区域「山口要塞外区域」；场景 agent attempt2 已交付
1 location（5 结构锚点）+3 approved LocationViews+sciv 批准链，设计自洽。
留痕：evidence/a1_F_interventions.jsonl。用法：python int_f01_fix_sc_contract.py
"""
import json, sys, pathlib, time

ROOT = pathlib.Path(r"C:\Users\曹文淇\Desktop\ai工作流\animation-storyboard")
sys.path.insert(0, str(ROOT))
from src.workflow.store import RunStore   # noqa: E402

PROJ, RID, NODE = "P53_F", "run_a11b72ab20f6", "n_sc"
EB = pathlib.Path(r"C:\Users\曹文淇\Desktop\ai工作流\测试4\PHASE53_TEST\evidence")

rs = RunStore(ROOT / "projects" / PROJ, PROJ)


def mutate(run):
    n = run["plan"]["nodes"][NODE]
    outs = n["completion"]["outputs"]
    for o in outs:
        if o.get("kind") == "location":
            o["_before"] = o.get("minCount")
            o["minCount"] = 1


after = rs.update(RID, mutate)
assert after, "update failed"
o = next(x for x in after["plan"]["nodes"][NODE]["completion"]["outputs"]
         if x.get("kind") == "location")
print("location minCount now:", o["minCount"], "before:", o.pop("_before", None))
EB.mkdir(parents=True, exist_ok=True)
with open(EB / "a1_F_interventions.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps({
        "int": "INT-F01", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "target": f"{PROJ}/{RID}/{NODE}", "change": "location minCount 2->1",
        "rationale": "夹具矛盾（D-05 同类）：场景目标自述单区域，契约却要求×2；"
                     "attempt2 已交付自洽单地点设计（5锚点+3视图approved）。"
                     "执行侧修正契约对齐目标文本，不重派agent重做已批准视图。",
        "before": 2, "after": 1}, ensure_ascii=False) + "\n")
print("INT-F01 logged")
