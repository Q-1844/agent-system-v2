# -*- coding: utf-8 -*-
"""Phase48 E 风格片1 续跑驱动（run_e9484ba7ad72 @ P48_E_2D）。

用法：
  python _p48_e_drive1.py status            # run 状态概览
  python _p48_e_drive1.py continue          # n_sc failed → 开新 attempt
  python _p48_e_drive1.py wait-review       # 阻塞等待下一个 pending 审核（打印详情后退出）
  python _p48_e_drive1.py approve <reqId> <note>   # 通过指定审核（先目检工件！）
  python _p48_e_drive1.py events [n]        # 最近 n 条事件

用户关口 n_rv_final 不批准——出现即停下留给用户。
"""
import json
import sys
import time
import urllib.request

BASE = "http://localhost:8000"
PROJ = "P48_E_2D"
RID = "run_e9484ba7ad72"
USER_GATE = {"n_rv_final"}
AUTO_NOTE = "E批实验中间关口自动通过（非用户关口）"


def api(method, path, payload=None, timeout=120):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8")
    return json.loads(body) if body else {}


def show(d):
    print("run status:", d.get("status"), "| cursor:", d.get("cursor"))
    for k, v in (d.get("nodes") or {}).items():
        if v.get("status") != "pending":
            print(f"  {k}: {v.get('status')} {(v.get('error') or '')[:110]}")
    for rv in d.get("reviews") or []:
        if rv.get("status") == "pending":
            print("PENDING REVIEW:", rv.get("requestId"), "node:", rv.get("nodeId"))
            print(json.dumps(rv, ensure_ascii=False, indent=1)[:2000])


def wait_review(timeout_s=3600):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            d = api("GET", f"/api/project/{PROJ}/workflow/runs/{RID}")
        except Exception as e:
            print("[warn] poll error:", e, flush=True)
            time.sleep(15)
            continue
        pend = [rv for rv in d.get("reviews") or [] if rv.get("status") == "pending"]
        st = d.get("status")
        if pend:
            show(d)
            return
        failed = {k: v for k, v in (d.get("nodes") or {}).items()
                  if v.get("status") == "failed"}
        if failed:
            print("RUN status:", st, "| FAILED nodes:", list(failed))
            for k, v in failed.items():
                print(f"  {k}: {(v.get('error') or '')[:300]}")
            return
        print(f"[{time.strftime('%H:%M:%S')}] run={st} cursor={d.get('cursor')} "
              f"no pending review, keep polling...", flush=True)
        time.sleep(20)
    print("timeout waiting for review")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        show(api("GET", f"/api/project/{PROJ}/workflow/runs/{RID}"))
    elif cmd == "continue":
        c = api("POST", f"/api/project/{PROJ}/workflow/runs/{RID}/continue",
                {"nodeId": "n_sc"})
        print("continue ->", (c.get("run") or {}).get("status"))
    elif cmd == "wait-review":
        wait_review()
    elif cmd == "approve":
        rid, note = sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else AUTO_NOTE)
        out = api("POST", f"/api/project/{PROJ}/workflow/runs/{RID}/reviews/"
                          f"{rid}/decision", {"decision": "approve", "note": note})
        print(json.dumps(out, ensure_ascii=False)[:300])
    elif cmd == "events":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 15
        evs = api("GET", f"/api/project/{PROJ}/workflow/runs/{RID}/events")
        items = (evs.get("events") or evs.get("items") or [])[-n:]
        for e in items:
            print(e.get("ts"), e.get("kind"), e.get("nodeId"), e.get("attemptId"),
                  e.get("from", ""), "->", e.get("to") or e.get("status"),
                  (e.get("error") or "")[:90])
    else:
        print(__doc__)
