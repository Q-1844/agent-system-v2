# -*- coding: utf-8 -*-
"""Director Agent 真机 smoke：走 /turn/stream 端点，打印工具事件与最终回复。

用法：python gp_director_smoke.py "<user message>" [--project NAME]
观察：tool.* 事件（读/写工具与 summary）、resource.locked/unlocked（stdout 单独行）、
     最终 assistant_message、meta.receipts 概要、耗时。
"""
import json
import sys
import time

import httpx

BASE = "http://localhost:8000"
PROJECT = "Director冒烟测试"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    content = args[0] if args else "给第2段设计分镜。"
    c = httpx.Client(timeout=600)

    # 1. 幂等建 director 会话
    r = c.post(f"{BASE}/api/project/{PROJECT}/agents/sessions",
               json={"agent_id": "director", "scope_type": "project", "scope_id": PROJECT})
    r.raise_for_status()
    sid = r.json()["session_id"]

    # 2. 流式 turn
    t0 = time.time()
    lock_events = []
    with c.stream("POST", f"{BASE}/api/project/{PROJECT}/agents/sessions/{sid}/turn/stream",
                  json={"content": content, "thinking": False}) as resp:
        print(f"HTTP {resp.status_code}\n--- SSE ---")
        final = None
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            t = ev.get("type")
            if t == "tool.started":
                print(f"[tool] {ev.get('label')} ({ev.get('name')})")
            elif t == "tool.completed":
                print(f"  -> ok={ev.get('ok')} {ev.get('summary')}")
            elif t == "tool.failed":
                print(f"  -> FAILED {ev.get('error')}")
            elif t == "reasoning.completed":
                print(f"[think] {ev.get('duration_ms')}ms")
            elif t == "turn.completed":
                final = ev.get("result")
            elif t == "turn.failed":
                print("TURN FAILED:", ev.get("error"))
            elif t and t.startswith("resource."):
                lock_events.append((t, ev.get("resource")))
                print(f"[bus] {t} {ev.get('resource')}")
        dur = time.time() - t0

    # 3. 锁状态对账（结束后应为空）
    locks = c.get(f"{BASE}/api/project/{PROJECT}/resource-locks").json().get("locks", [])
    print(f"--- locks after turn: {locks}")

    if final:
        msg = final.get("message") or {}
        meta = msg.get("meta") or {}
        print(f"--- intent: {final.get('intent')}  dur: {dur:.1f}s  "
              f"write_success: {meta.get('write_success')}/{meta.get('write_attempts')}")
        for rcp in meta.get("receipts", []):
            print(f"  receipt: {rcp.get('operation')} ok={rcp.get('ok')} "
                  f"verified={rcp.get('verified')} stale={rcp.get('stale')} "
                  f"shot_id={rcp.get('shot_id')} scene_id={rcp.get('scene_id')} "
                  f"count={rcp.get('count')} changed={rcp.get('changed')} err={rcp.get('error')}")
        print("--- assistant ---")
        print(final.get("assistant_message", "")[:600])
        if "--receipt" in flags:
            print("--- canonical ---")
            proj = c.get(f"{BASE}/api/project/{PROJECT}/shots").json()
            for s in proj.get("scenes", []):
                print(s.get("scene_id"), [(sh.get("shot_id"), (sh.get("description") or "")[:20],
                                           sh.get("shot_size")) for sh in s.get("shots", [])])
    else:
        print("(no turn.completed payload)")


if __name__ == "__main__":
    main()
