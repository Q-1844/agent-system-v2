# -*- coding: utf-8 -*-
"""CD_GV 第二次 T2 重测：「给林远生成三视图」（修复 recovery 词表 + prompt 后）。
角色与三视图已存在（sig 相同）→ 预期：模型直接调 character.generate_views →
handler 跳过重复生成（幂等）→ verify 通过 → receipt verified + generated_views 带路径。
"""
import json
import pathlib
import time

import httpx

BASE = "http://localhost:8000"
PROJ = "CD_GV"
c = httpx.Client(timeout=900)

sid = c.post(f"{BASE}/api/project/{PROJ}/agents/sessions",
             json={"agent_id": "character_designer", "scope_type": "project",
                   "scope_id": PROJ}).json()["session_id"]

t0 = time.time()
events = []
with c.stream("POST", f"{BASE}/api/project/{PROJ}/agents/sessions/{sid}/turn/stream",
              json={"content": "给林远生成三视图。", "thinking": False}) as resp:
    print("stream:", resp.status_code, flush=True)
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
print("elapsed:", round(time.time() - t0, 1), "s", flush=True)

completed = next((e for e in events if e.get("type") == "turn.completed"), None)
if completed:
    res = completed["result"]
    meta = (res.get("message") or {}).get("meta", {})
    print("intent:", res.get("intent"), "| write_success:", meta.get("write_success"), flush=True)
    print("trace:", [(t["step"], t["tool"], t["target"], t["result"]) for t in meta.get("trace", [])], flush=True)
    print("receipts:", [(r.get("operation"), r.get("ok"), r.get("verified"), r.get("elapsed_s"))
                        for r in meta.get("receipts", [])], flush=True)
    gv = meta.get("generated_views", [])
    print("generated_views:", [(g["character_name"], g["view"]) for g in gv], flush=True)
    all_exist = all(pathlib.Path(g["path"]).exists() for g in gv)
    print("all image files exist:", all_exist, flush=True)
    print("assistant:", res.get("assistant_message", "")[:250], flush=True)
else:
    print("NO turn.completed:", [(e.get("type"), e.get("error")) for e in events[-4:]], flush=True)
locks = c.get(f"{BASE}/api/project/{PROJ}/resource-locks").json()["locks"]
print("locks after:", locks, flush=True)
