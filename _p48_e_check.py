# -*- coding: utf-8 -*-
"""检查后端与 P48_E_2D run 状态（崩溃恢复后）。
用法：python _p48_e_check.py [continue]"""
import json
import sys
import urllib.request

BASE = "http://localhost:8000"
PROJ = "P48_E_2D"
RID = "run_e9484ba7ad72"


def api(method, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8")
    return json.loads(body) if body else {}


def main():
    d = api("GET", f"/api/project/{PROJ}/workflow/runs/{RID}")
    print("run status:", d.get("status"))
    for k, v in d.get("nodes", {}).items():
        if v.get("status") != "pending":
            print(f"  {k}: {v.get('status')} {(v.get('error') or '')[:90]}")
    pend = [r.get("nodeId") for r in d.get("reviews", []) if r.get("status") == "pending"]
    print("pending reviews:", pend)
    if len(sys.argv) > 1 and sys.argv[1] == "continue":
        c = api("POST", f"/api/project/{PROJ}/workflow/runs/{RID}/continue",
                {"nodeId": "n_sc"})
        print("continue ->", c.get("run", {}).get("status"))


if __name__ == "__main__":
    main()
