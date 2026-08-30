# -*- coding: utf-8 -*-
"""smoke：单镜修改 + 项目事件订阅（resource.locked / storyboard.changed / unlocked）。"""
import json
import threading
import time

import httpx

BASE = "http://localhost:8000"
PROJECT = "Director冒烟测试"

lock_events = []


def watch_events(stop: threading.Event):
    with httpx.Client(timeout=300) as c:
        with c.stream("GET", f"{BASE}/api/project/{PROJECT}/events") as resp:
            for line in resp.iter_lines():
                if stop.is_set():
                    return
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except Exception:
                    continue
                t = ev.get("type")
                if t and (t.startswith("resource.") or t == "storyboard.changed"):
                    lock_events.append((t, ev.get("resource") or ev.get("operation")))
                    print(f"[bus] {t} {ev.get('resource') or ev.get('operation')}")


def main():
    c = httpx.Client(timeout=600)
    r = c.post(f"{BASE}/api/project/{PROJECT}/agents/sessions",
               json={"agent_id": "director", "scope_type": "project", "scope_id": PROJECT})
    sid = r.json()["session_id"]

    stop = threading.Event()
    th = threading.Thread(target=watch_events, args=(stop,), daemon=True)
    th.start()
    time.sleep(0.5)

    t0 = time.time()
    with c.stream("POST", f"{BASE}/api/project/{PROJECT}/agents/sessions/{sid}/turn/stream",
                  json={"content": "把第2镜改成近景，突出她接毛巾的手。", "thinking": False}) as resp:
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
            elif t == "turn.completed":
                final = ev.get("result")
    dur = time.time() - t0
    time.sleep(1.5)
    stop.set()

    print(f"--- lock events seen: {lock_events}")
    locks = c.get(f"{BASE}/api/project/{PROJECT}/resource-locks").json().get("locks", [])
    print(f"--- locks after: {locks}")
    if final:
        meta = (final.get("message") or {}).get("meta") or {}
        print(f"--- intent: {final.get('intent')}  dur: {dur:.1f}s  "
              f"write: {meta.get('write_success')}/{meta.get('write_attempts')}")
        for rcp in meta.get("receipts", []):
            print(f"  receipt: {rcp.get('operation')} ok={rcp.get('ok')} "
                  f"verified={rcp.get('verified')} shot_id={rcp.get('shot_id')} "
                  f"changed={rcp.get('changed')} rev={rcp.get('revision')}")
        print("--- assistant ---")
        print(final.get("assistant_message", "")[:300])


if __name__ == "__main__":
    main()
