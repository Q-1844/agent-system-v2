# -*- coding: utf-8 -*-
"""逐字保留强化后的漂移复测（CD_Behav2，跑完手动删目录）。

种子 = 上一轮真机测试 3 轮修改后的最终描述（信息量大，最能暴露漂移）。
3 轮修改：改衣服 → 改脸 → 加配饰。每轮 diff 期望【只有目标短语段变化】。
"""
import difflib
import json
import pathlib
import time

import httpx

BASE = "http://localhost:8000"
PROJ = "CD_Behav2"
OUT = pathlib.Path("gp_cd_behavior_results")
OUT.mkdir(exist_ok=True)

SEED_DESC = ("28-year-old male fisherman, lean muscular build from years of sea work, "
             "weathered face with deep wrinkles from sun and wind, short black hair slightly wind-tousled, "
             "well-groomed short stubble on jawline and upper lip, deep-set dark eyes with weathered expression, "
             "wearing a dark blue waterproof fishing suit, dark trousers, sturdy rubber boots. "
             "Simple, capable hands. Carries the quiet resilience of a third-generation fisherman from a coastal village.")

c = httpx.Client(timeout=300)
c.post(f"{BASE}/api/projects/create", params={"name": PROJ})
proj = {
    "name": PROJ, "schema_version": 2,
    "created_at": time.time(), "updated_at": time.time(),
    "style": {"art_style": "anime, cinematic composition", "palette": "",
              "aspect_ratio": "16:9", "size_tier": "1K", "era": "",
              "extra": "high detail, consistent art style"},
    "story_text": ("海边小镇太平湾。男主林远，28岁，渔民，每天天不亮就出海。祖上三代渔民，继承父亲的旧渔船「海潮号」。",
                   "台风夜他冒死救起抱住漂木的女孩苏晚。林远话不多，但答应的事一定做到，常穿一件晒得发白的蓝色旧外套。"),
    "characters": [{"name": "林远", "description_en": SEED_DESC, "views": {}}],
    "scenes": [
        {"scene_id": "s01", "title": "", "location": "太平湾码头", "time_of_day": "日",
         "summary": "林远在码头补网，村民议论几天后有台风。", "characters": ["林远"], "shots": []},
    ],
    "next_scene_seq": 2,
}
# story_text 是字符串（上面误写成 tuple 的话 json 会存数组）——规范化
if isinstance(proj["story_text"], tuple):
    proj["story_text"] = "".join(proj["story_text"])

pj = pathlib.Path("projects") / PROJ / "output" / "project.json"
pj.parent.mkdir(parents=True, exist_ok=True)
pj.write_text(json.dumps(proj, ensure_ascii=False, indent=2), encoding="utf-8")

sid = c.post(f"{BASE}/api/project/{PROJ}/agents/sessions",
             json={"agent_id": "character_designer", "scope_type": "project",
                   "scope_id": PROJ}).json()["session_id"]
print("session:", sid, flush=True)


def desc_now():
    p = json.loads(pj.read_text(encoding="utf-8"))
    return p["characters"][0]["description_en"] if p["characters"] else None


def run_turn(tag, content, before):
    t0 = time.time()
    r = c.post(f"{BASE}/api/project/{PROJ}/agents/sessions/{sid}/turn", json={"content": content})
    res = r.json()
    meta = (res.get("message") or {}).get("meta", {})
    rec = {"turn": tag, "content": content, "seconds": round(time.time() - t0, 1),
           "intent": res.get("intent"), "assistant": res.get("assistant_message", ""),
           "trace": meta.get("trace", []), "write_success": meta.get("write_success", 0),
           "desc_before": before, "desc_after": desc_now()}
    (OUT / f"D_{tag}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[{tag}] {content}  ({rec['seconds']}s, write_success={rec['write_success']})", flush=True)
    for t in rec["trace"]:
        print(f"    step{t['step']} {t['tool']} -> {t['target']} ({t['result']})", flush=True)
    print(f"  desc: {rec['desc_after']}", flush=True)
    return rec


desc = desc_now()
turns = []
for tag, content in [
    ("R1_clothing", "把他的外套改成带补丁的土黄色粗布短褂。"),
    ("R2_face", "让他的眼神更锐利一些。"),
    ("R3_accessory", "给他脖子上加一条旧毛巾。"),
]:
    rec = run_turn(tag, content, desc)
    turns.append(rec)
    desc = rec["desc_after"]

print("\n===== DIFF REPORT =====", flush=True)
ok_all = True
for rec in turns:
    b, a = rec["desc_before"], rec["desc_after"]
    bb, aa = b.split(", "), a.split(", ")
    segs = ["  ".join(bb[i1:i1+n1])[:80] + "  ->  " + " ".join(aa[i2:i2+n2])[:80]
            for tag, i1, n1, i2, n2 in difflib.SequenceMatcher(None, bb, aa).get_opcodes() if tag != "equal"]
    n = len(segs)
    print(f"\n[{rec['turn']}] 变化片段数 = {n}")
    for s in segs:
        print("   ", s)
jdump = json.dumps({"final_desc": desc, "turns": [
    {"turn": r["turn"], "intent": r["intent"], "tools": [t["tool"] for t in r["trace"]],
     "seconds": r["seconds"]} for r in turns]}, ensure_ascii=False, indent=1)
(OUT / "D_summary.json").write_text(jdump, encoding="utf-8")
print("\ndone", flush=True)
