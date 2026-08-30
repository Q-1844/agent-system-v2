# -*- coding: utf-8 -*-
"""Character Designer 真机行为验证（2026-08-30）。

回答四个模型行为问题（ScriptedClient 测不了，必须真机）：
  T1 是否真参考故事设计角色（而非通用模板人物）？是否无必要 script.read 全文？
  T2 连续修改是否漂移（改衣服后脸型/年龄/身份是否莫名变化）？——3 轮连续修改，逐轮 diff。
  T3 普通讨论是否乱改角色？——3 轮讨论（含一次剧情事实冲突的身份话题），期望零写。
  T4 script.read 统计：全程出现次数与动机。

项目 CD_Behav（跑完手动删目录）。每 turn 落盘一份 JSON 快照，中断可续看。
"""
import json
import pathlib
import re
import time

import httpx

BASE = "http://localhost:8000"
PROJ = "CD_Behav"
OUT = pathlib.Path("gp_cd_behavior_results")
OUT.mkdir(exist_ok=True)

c = httpx.Client(timeout=300)

# 剧情事实清单（机器可核对）：这些词一旦在设计/后续修改中消失即为漂移
FACTS = ["28", "fisherman", "blue"]      # 年龄 / 身份 / 服装基色（第2轮会主动改衣服，之后应保持 navy）
FACTS_AFTER_T21 = ["28", "fisherman"]    # 第2轮之后的稳定事实


def jdump(tag, obj):
    (OUT / f"{tag}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- 0. 建项目 + 剧情种子（剧情事实明确） ----------
c.post(f"{BASE}/api/projects/create", params={"name": PROJ})
proj = {
    "name": PROJ, "schema_version": 2,
    "created_at": time.time(), "updated_at": time.time(),
    "style": {"art_style": "anime, cinematic composition", "palette": "",
              "aspect_ratio": "16:9", "size_tier": "1K", "era": "",
              "extra": "high detail, consistent art style"},
    "story_text": ("海边小镇太平湾。男主林远，28岁，渔民，每天天不亮就出海。"
                   "祖上三代都是渔民，他继承了父亲留下的旧渔船「海潮号」。"
                   "台风夜他冒死救起抱住漂木的女孩苏晚。"
                   "林远话不多，但答应的事一定做到，常穿一件被海风晒得发白的蓝色旧外套。"),
    "characters": [],
    "scenes": [
        {"scene_id": "s01", "title": "", "location": "太平湾码头", "time_of_day": "日",
         "summary": "林远在码头补网，村民议论几天后有台风，他决定出海前修好「海潮号」。",
         "characters": ["林远"], "shots": []},
        {"scene_id": "s02", "title": "", "location": "海上", "time_of_day": "夜",
         "summary": "台风提前来袭，林远返港途中发现抱住漂木的苏晚，冒浪把人救上船。",
         "characters": ["林远"], "shots": []},
        {"scene_id": "s03", "title": "", "location": "林远家", "time_of_day": "夜",
         "summary": "苏晚在林远家养伤，林母道谢；苏晚隐瞒了自己城里记者的身份。",
         "characters": ["林远"], "shots": []},
    ],
    "next_scene_seq": 4,
}
pj = pathlib.Path("projects") / PROJ / "output" / "project.json"
pj.parent.mkdir(parents=True, exist_ok=True)
pj.write_text(json.dumps(proj, ensure_ascii=False, indent=2), encoding="utf-8")

r = c.post(f"{BASE}/api/project/{PROJ}/agents/sessions",
           json={"agent_id": "character_designer", "scope_type": "project", "scope_id": PROJ})
sid = r.json()["session_id"]
print("session:", sid, flush=True)


def desc_now():
    p = json.loads(pj.read_text(encoding="utf-8"))
    ch = p["characters"]
    return ch[0]["description_en"] if ch else None


def fact_missing(desc, facts):
    d = (desc or "").lower()
    return [f for f in facts if f.lower() not in d]


def run_turn(tag, content):
    t0 = time.time()
    r = c.post(f"{BASE}/api/project/{PROJ}/agents/sessions/{sid}/turn", json={"content": content})
    res = r.json()
    meta = (res.get("message") or {}).get("meta", {})
    rec = {
        "turn": tag, "content": content, "seconds": round(time.time() - t0, 1),
        "intent": res.get("intent"),
        "assistant": res.get("assistant_message", ""),
        "trace": meta.get("trace", []),
        "receipts": meta.get("receipts", []),
        "write_attempts": meta.get("write_attempts", 0),
        "write_success": meta.get("write_success", 0),
        "search_sources": meta.get("search_sources", []),
        "desc_after": desc_now(),
    }
    jdump(tag, rec)
    tools = [(t["step"], t["tool"], t["target"], t["result"]) for t in rec["trace"]]
    print(f"\n[{tag}] {content}", flush=True)
    print(f"  intent={rec['intent']}  {rec['seconds']}s  write_attempts={rec['write_attempts']} "
          f"write_success={rec['write_success']}", flush=True)
    for s, tool, tgt, rlt in tools:
        print(f"    step{s} {tool} -> {tgt} ({rlt})", flush=True)
    print(f"  assistant: {rec['assistant'][:200]}", flush=True)
    if rec["desc_after"]:
        print(f"  desc: {rec['desc_after']}", flush=True)
    return rec


results = {}
start_desc = None

# ---------- T1 按故事设计男主 ----------
results["T1"] = run_turn("T1_design", "根据现在的故事，帮我设计男主。")
start_desc = results["T1"]["desc_after"]
results["T1"]["missing_facts"] = fact_missing(start_desc, ["28", "fisherman", "blue", "三代" if False else "boat"])
# T1 检查：剧情事实是否进入设计；是否无必要 script.read 全文

# ---------- T2 三轮连续修改（漂移观察） ----------
results["T2_1"] = run_turn("T2_1_clothing", "把他的外套改成深蓝色防水渔服，其它设定都不要动。")
results["T2_1"]["desc_before"] = start_desc

results["T2_2"] = run_turn("T2_2_face", "让他的脸看起来更饱经风霜一些，年龄和身份不要变。")
results["T2_2"]["desc_before"] = results["T2_1"]["desc_after"]

results["T2_3"] = run_turn("T2_3_beard", "给他加一副短胡茬。")
results["T2_3"]["desc_before"] = results["T2_2"]["desc_after"]

for k in ("T2_1", "T2_2", "T2_3"):
    results[k]["missing_facts"] = fact_missing(results[k]["desc_after"], FACTS_AFTER_T21)

# ---------- T3 三轮讨论（期望零写） ----------
results["T3_1"] = run_turn("T3_1_pipe_discuss", "你觉得给他加一个烟斗怎么样？先说说你的看法，先不要改。")
results["T3_2"] = run_turn("T3_2_decline", "我也觉得不太合适，那就先不加。")
results["T3_3"] = run_turn("T3_3_identity_discuss", "如果男主的身份从渔民改成警察，故事会不会更有张力？你怎么看？")

for k in ("T3_1", "T3_2", "T3_3"):
    results[k]["missing_facts"] = fact_missing(results[k].get("desc_after") or "", FACTS_AFTER_T21)

# ---------- 汇总 ----------
final_desc = desc_now()
summary = {
    "final_desc": final_desc,
    "script_read_turns": [k for k, v in results.items()
                          if any(t["tool"] == "script.read" for t in v["trace"])],
    "discussion_zero_write": {k: (results[k]["write_attempts"] == 0)
                              for k in ("T3_1", "T3_2", "T3_3")},
    "facts_missing_each_turn": {k: results[k].get("missing_facts")
                                for k in results if k.startswith(("T1", "T2"))},
    "turns": {k: {"intent": v["intent"], "tools": [t["tool"] for t in v["trace"]],
                  "write_attempts": v["write_attempts"], "seconds": v["seconds"]}
              for k, v in results.items()},
}
jdump("_summary", summary)
print("\n===== SUMMARY =====")
print(json.dumps(summary, ensure_ascii=False, indent=1), flush=True)
