# -*- coding: utf-8 -*-
"""character.generate_views 真机端到端 smoke（CD_GV 项目，跑完手动删）。

流程：Agent 根据故事创建角色 → 用户要求生成三视图（真实 Agnes 链式生成 + 抠图）。
断言：流式 views.progress 事件；receipt verified；图片文件落盘；meta.generated_views
带 local 路径（前端窗口渲染依据）；resource.locked(character) 期间锁生效、结束释放。
"""
import json
import pathlib
import time

import httpx

BASE = "http://localhost:8000"
PROJ = "CD_GV"
c = httpx.Client(timeout=900)

c.post(f"{BASE}/api/projects/create", params={"name": PROJ})
proj = {
    "name": PROJ, "schema_version": 2,
    "created_at": time.time(), "updated_at": time.time(),
    "style": {"art_style": "anime, cinematic composition", "palette": "",
              "aspect_ratio": "16:9", "size_tier": "1K", "era": "",
              "extra": "high detail, consistent art style"},
    "story_text": "海边小镇太平湾。男主林远，28岁，渔民，祖上三代渔民，台风夜救起落水的女孩苏晚。他话不多但守信，常穿晒得发白的蓝色旧外套。",
    "characters": [],
    "scenes": [
        {"scene_id": "s01", "title": "", "location": "太平湾码头", "time_of_day": "日",
         "summary": "林远在码头补网，村民议论几天后有台风。", "characters": ["林远"], "shots": []},
    ],
    "next_scene_seq": 2,
}
pj = pathlib.Path("projects") / PROJ / "output" / "project.json"
pj.parent.mkdir(parents=True, exist_ok=True)
pj.write_text(json.dumps(proj, ensure_ascii=False, indent=2), encoding="utf-8")

sid = c.post(f"{BASE}/api/project/{PROJ}/agents/sessions",
             json={"agent_id": "character_designer", "scope_type": "project",
                   "scope_id": PROJ}).json()["session_id"]
print("session:", sid, flush=True)


def stream_turn(tag, content):
    t0 = time.time()
    events = []
    with c.stream("POST", f"{BASE}/api/project/{PROJ}/agents/sessions/{sid}/turn/stream",
                  json={"content": content, "thinking": False}) as resp:
        print(f"[{tag}] stream {resp.status_code}", flush=True)
        buf = ""
        for chunk in resp.iter_text():
            buf += chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                for line in frame.split("\n"):
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except Exception:
                        continue
                    events.append(ev)
                    t = ev.get("type")
                    if t == "tool.started":
                        print(f"  tool.started: {ev.get('label')}", flush=True)
                    elif t == "views.progress":
                        print(f"  views.progress: {ev.get('character_name')} {ev.get('view')} {ev.get('pct')}%", flush=True)
                    elif t == "tool.completed":
                        print(f"  tool.completed: {ev.get('summary')}", flush=True)
                    elif t == "resource.locked":
                        print(f"  [lock] {ev.get('resource')} by={ev.get('by')}", flush=True)
                    elif t == "resource.unlocked":
                        print(f"  [unlock] {ev.get('resource')}", flush=True)
    print(f"[{tag}] done in {round(time.time()-t0,1)}s", flush=True)
    return events


# T1：创建角色
ev1 = stream_turn("T1_create", "根据现在的故事，创建男主林远的角色设定。")
completed1 = next((e for e in ev1 if e.get("type") == "turn.completed"), None)
print("T1 intent:", (completed1 or {}).get("result", {}).get("intent"), flush=True)

# T2：生成三视图（真实 Agnes 链式生成）
ev2 = stream_turn("T2_views", "很好，就按这个设定给林远生成三视图。")
completed2 = next((e for e in ev2 if e.get("type") == "turn.completed"), None)
if completed2:
    res = completed2["result"]
    meta = (res.get("message") or {}).get("meta", {})
    print("\nT2 intent:", res.get("intent"), "| write_success:", meta.get("write_success"), flush=True)
    print("trace:", [(t["step"], t["tool"], t["target"], t["result"]) for t in meta.get("trace", [])], flush=True)
    print("receipts:", [(r.get("operation"), r.get("ok"), r.get("verified"), r.get("elapsed_s")) for r in meta.get("receipts", [])], flush=True)
    gv = meta.get("generated_views", [])
    print("generated_views:", [(g["character_name"], g["view"], pathlib.Path(g["path"]).name) for g in gv], flush=True)
    for g in gv:
        print("   file exists:", pathlib.Path(g["path"]).exists(), g["path"], flush=True)
    disk = json.loads(pj.read_text(encoding="utf-8"))
    ch = disk["characters"][0]
    print("canonical views:", {v: {"status": a.get("status"), "local": bool(a.get("local"))}
                                for v, a in (ch.get("views") or {}).items()}, flush=True)
    print("assistant:", res.get("assistant_message", "")[:260], flush=True)
else:
    print("NO turn.completed:", [(e.get("type"), e.get("error")) for e in ev2[-4:]], flush=True)

locks = c.get(f"{BASE}/api/project/{PROJ}/resource-locks").json()["locks"]
print("locks after:", locks, flush=True)
