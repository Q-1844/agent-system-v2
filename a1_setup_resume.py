# -*- coding: utf-8 -*-
"""P53 setup 续接：定义编辑（autoApprove 前置4卡 + Skill v1.6.0 双绑定）→ PUT → compile → start。
前提：a1_setup_story.py 已完成步骤 1-2（项目/语言/模板）。CWD 无关（绝对路径）。
用法：python a1_setup_resume.py F|M
"""
import json, sys, urllib.request, pathlib

BASE = "http://127.0.0.1:8000"
RELAY_VER = "1.6.0"
SB = pathlib.Path(__file__).resolve().parent          # animation-storyboard/
STORY_KEY = sys.argv[1]
PROJ = {"I": "P53_I", "F": "P53_F", "M": "P53_M"}[STORY_KEY] + ""
WID = f"{PROJ}_W53"
DF = SB / "projects" / PROJ / "output" / "workflows" / "definitions" / f"{WID}.json"

AUTO_FRONT = ["n_rv_sw", "n_rv_cd", "n_rv_sc", "n_rv_board"]


def req(path, body=None, method="GET", timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data,
                               headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.load(resp)


d = json.load(open(DF, encoding="utf-8"))
for n in d["nodes"]:
    if n.get("type") == "human_review" and n.get("id") in AUTO_FRONT:
        n.setdefault("config", {})["autoApprove"] = True
sn = next((n for n in d["nodes"] if n.get("type") == "skill"), None)
targets = {e.get("target") for e in d.get("edges", []) if e.get("type") == "skill_binding"}
if sn and "n_dir_board" not in targets:
    d["edges"].append({"id": "e_skill_board", "type": "skill_binding",
                       "source": sn["id"], "target": "n_dir_board",
                       "skillVersion": RELAY_VER})
for e in d.get("edges", []):
    if e.get("type") == "skill_binding":
        e["skillVersion"] = RELAY_VER
r = req(f"/api/project/{PROJ}/workflow/definitions/{WID}",
        {"definition": d, "expectedRevision": d["revision"]}, "PUT")
print(f"PUT ok={r.get('validation', {}).get('ok')}")
r = req(f"/api/project/{PROJ}/workflow/definitions/{WID}/compile", {}, "POST")
print(f"compile ok={r.get('ok')}")
r = req(f"/api/project/{PROJ}/workflow/definitions/{WID}/runs",
        {"idempotencyKey": f"p53_{STORY_KEY}_run1"}, "POST")
run = r.get("run") or {}
print(f"run: {run.get('runId')} status: {run.get('status')}")
eb = pathlib.Path(__file__).resolve().parents[1] / "PHASE53_TEST" / "evidence"
eb.mkdir(parents=True, exist_ok=True)
(eb / f"a1_{STORY_KEY}_setup_log.json").write_text(
    json.dumps({"story": STORY_KEY, "project": PROJ, "workflow": WID,
                "runId": run.get("runId"), "skill": ["full-story-relay-calibration", RELAY_VER],
                "resumed": True}, ensure_ascii=False, indent=1), encoding="utf-8")
print("evidence log written")
