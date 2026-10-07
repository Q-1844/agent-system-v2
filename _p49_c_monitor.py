# -*- coding: utf-8 -*-
"""Phase49 C 批 run 监控:轮询状态,发现 pending review / 终态即打印退出。
用法: python _p49_c_monitor.py <project> <run_id> [timeout_s]
"""
import json
import sys
import time
import urllib.request

PROJ, RUN = sys.argv[1], sys.argv[2]
TIMEOUT = int(sys.argv[3]) if len(sys.argv) > 3 else 1800
BASE = "http://127.0.0.1:8000"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.load(r)


t0 = time.time()
last_state = None
while time.time() - t0 < TIMEOUT:
    try:
        d = get(f"/api/project/{PROJ}/workflow/runs/{RUN}")
    except Exception as e:
        print("poll error:", e)
        time.sleep(10)
        continue
    run = d.get("run") or d
    st = run.get("status")
    reviews = run.get("reviews") or []
    pending = [rv for rv in reviews if rv.get("status") == "pending"]
    sig = (st, len(pending), tuple(rv.get("requestId") for rv in pending))
    if sig != last_state:
        print(f"[{time.strftime('%H:%M:%S')}] status={st} pending_reviews={len(pending)}")
        last_state = sig
    if pending:
        print(json.dumps(pending, ensure_ascii=False, indent=1)[:3000])
        sys.exit(0)
    if st in ("completed", "failed", "stopped", "cancelled"):
        nodes = run.get("nodes") or []
        print("final status:", st)
        for n in nodes:
            if isinstance(n, dict):
                print(" ", n.get("nodeId") or n.get("id"), n.get("status"),
                      str(n.get("error") or "")[:120])
            else:
                print(" ", n)
        sys.exit(0)
    time.sleep(12)
print("TIMEOUT (still running)")
