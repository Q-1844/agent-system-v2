# -*- coding: utf-8 -*-
"""Character Designer 真机 smoke（2026-08-30）。

专用项目 CD_SMoke（跑完手动删目录）：
  S1 聊天轮（/turn，非流式）：「你觉得沈书言目前的设定怎么样？先不要修改。」
     预期：intent=conversation、零写回执、project.json 未变。
  S2 修改轮（/turn/stream，流式）：「把沈书言的描述里加上一副圆框金丝眼镜，其他设定不要动。」
     预期：SSE 流式事件 → 期间 resource-locks 出现 character 锁 → 收尾后锁释放；
     回执 verified=true、changed=[description_en]、trace 含 character.read+character.update、
     0 次 web.search、canonical 落盘（重读 project.json 验证眼镜已加入）。
"""
import json
import sys
import time

import httpx

BASE = "http://localhost:8000"
PROJ = "CD_SMoke"
PYIO = True

c = httpx.Client(timeout=300)


def jprint(tag, obj):
    print(f"\n===== {tag} =====")
    print(json.dumps(obj, ensure_ascii=False, indent=1)[:3000])


# ---------- 0. 建项目 + 初始化数据 ----------
r = c.post(f"{BASE}/api/projects/create", params={"name": PROJ})
print("create:", r.status_code, r.text[:120] if r.status_code != 200 else "ok")

proj = {
    "name": PROJ, "schema_version": 2,
    "created_at": time.time(), "updated_at": time.time(),
    "style": {"art_style": "anime, cinematic composition", "palette": "",
              "aspect_ratio": "16:9", "size_tier": "1K", "era": "",
              "extra": "high detail, consistent art style"},
    "story_text": ("1937年春，上海。女记者沈书言在申报馆做见习记者，白天跑社会新闻，"
                   "夜里追查一桩码头走私案。主编几次警告她收手，她偏不。"),
    "characters": [{"name": "沈书言",
                    "description_en": "a young female journalist in 1930s Shanghai, short bob hair, plain grey qipao, leather satchel",
                    "views": {}}],
    "scenes": [
        {"scene_id": "s01", "title": "", "location": "申报馆", "time_of_day": "日",
         "summary": "沈书言在申报馆整理码头走私案的线索，主编警告她别碰这案子，她表面应下、暗地里继续。",
         "characters": ["沈书言"], "shots": []},
        {"scene_id": "s02", "title": "", "location": "十六铺码头", "time_of_day": "夜",
         "summary": "沈书言深夜潜入码头仓库拍下走私证据，被巡捕发现，翻墙逃脱，胶片保住了。",
         "characters": ["沈书言"], "shots": []},
    ],
    "next_scene_seq": 3,
}
import pathlib
pj = pathlib.Path("projects") / PROJ / "output" / "project.json"
pj.parent.mkdir(parents=True, exist_ok=True)
pj.write_text(json.dumps(proj, ensure_ascii=False, indent=2), encoding="utf-8")
print("seeded project.json,", pj.read_text(encoding="utf-8").count("沈书言"), "refs")

# ---------- 1. 会话 ----------
r = c.post(f"{BASE}/api/project/{PROJ}/agents/sessions",
           json={"agent_id": "character_designer", "scope_type": "project", "scope_id": PROJ})
sid = r.json()["session_id"]
print("session:", sid)

# ---------- S1 聊天轮（零写） ----------
r = c.post(f"{BASE}/api/project/{PROJ}/agents/sessions/{sid}/turn",
           json={"content": "你觉得沈书言目前的设定怎么样？先不要修改。"})
s1 = r.json()
jprint("S1 turn result", {
    "intent": s1.get("intent"),
    "assistant": s1.get("assistant_message", "")[:400],
    "meta_keys": sorted((s1.get("message") or {}).get("meta", {}).keys()),
    "write_success": (s1.get("message") or {}).get("meta", {}).get("write_success"),
    "trace": (s1.get("message") or {}).get("meta", {}).get("trace"),
})
disk = json.loads(pj.read_text(encoding="utf-8"))
s1_write_ok = disk["characters"][0]["description_en"] == proj["characters"][0]["description_en"]
print("S1 canonical unchanged:", s1_write_ok)

# ---------- S2 修改轮（流式 + 锁） ----------
events = []
locked_seen = False
before_desc = json.loads(pj.read_text(encoding="utf-8"))["characters"][0]["description_en"]

with c.stream("POST", f"{BASE}/api/project/{PROJ}/agents/sessions/{sid}/turn/stream",
              json={"content": "把沈书言的描述里加上一副圆框金丝眼镜，其他设定不要动。", "thinking": False}) as resp:
    print("stream status:", resp.status_code)
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
                if ev.get("type") == "resource.locked" and ev.get("resource") == "character":
                    locked_seen = True
                    print("  [SSE] resource.locked(character) by=", ev.get("by"))
                elif ev.get("type") == "resource.unlocked":
                    print("  [SSE] resource.unlocked")
                elif ev.get("type") == "tool.started":
                    print("  [SSE] tool.started:", ev.get("label"))
                elif ev.get("type") == "tool.completed":
                    print("  [SSE] tool.completed:", ev.get("summary"))
                elif ev.get("type") == "character.changed":
                    print("  [SSE] character.changed op=", ev.get("operation"))

# 锁对账：turn 结束后应无锁
locks = c.get(f"{BASE}/api/project/{PROJ}/resource-locks").json()["locks"]
print("locks after turn:", locks)

completed = next((e for e in events if e.get("type") == "turn.completed"), None)
if completed:
    res = completed["result"]
    meta = (res.get("message") or {}).get("meta", {})
    jprint("S2 turn result", {
        "intent": res.get("intent"),
        "assistant": res.get("assistant_message", "")[:400],
        "write_success": meta.get("write_success"),
        "receipts": meta.get("receipts"),
        "trace": meta.get("trace"),
        "search_sources": meta.get("search_sources"),
    })
    disk = json.loads(pj.read_text(encoding="utf-8"))
    after_desc = disk["characters"][0]["description_en"]
    print("\nS2 verdicts:")
    print("  verified receipt:", bool(meta.get("receipts") and meta["receipts"][0].get("ok")
                                     and meta["receipts"][0].get("verified")))
    print("  changed fields:", meta.get("receipts", [{}])[0].get("changed"))
    print("  trace has read+update:",
          any(t["tool"] == "character.read" for t in meta.get("trace", [])),
          any(t["tool"] == "character.update" for t in meta.get("trace", [])))
    print("  0 web.search:", not any(t["tool"] == "web.search" for t in meta.get("trace", [])))
    print("  glasses on disk:", ("glasses" in after_desc.lower()) or ("眼镜" in after_desc))
    print("  desc before:", before_desc)
    print("  desc after :", after_desc)
else:
    print("NO turn.completed; last events:",
          [(e.get("type"), e.get("error")) for e in events[-5:]])
print("\nlocked_seen:", locked_seen, "| events:", len(events))
