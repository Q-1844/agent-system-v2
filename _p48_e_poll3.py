# -*- coding: utf-8 -*-
"""Phase48 E 多项目 run 轮询：三条 run 任一出现 pending 审核/失败节点即打印详情退出。
用法：python _p48_e_poll3.py [timeout_s]"""
import json
import sys
import time
import urllib.request

BASE = "http://localhost:8000"
WATCH = [
    ("P48_E_Watercolor", "run_36463ddeb487"),
    ("P48_E_Realistic", "run_bbb7a89a480d"),
    ("P48_E_Explainer", "run_1f8898ce81b7"),
    ("P48_E_Watercolor", "run_3acbba944ea2"),
]


def get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=60) as r:
        return json.loads(r.read() or b"{}")


def main(timeout_s=7200):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        lines = []
        hit = False
        for p, rid in WATCH:
            try:
                d = get(f"/api/project/{p}/workflow/runs/{rid}")
            except Exception as e:
                lines.append(f"{p}: poll error {e}")
                continue
            st = d.get("status")
            pend = [rv for rv in d.get("reviews") or [] if rv.get("status") == "pending"]
            failed = {k: v for k, v in (d.get("nodes") or {}).items()
                      if v.get("status") == "failed"}
            needrec = {k: v for k, v in (d.get("nodes") or {}).items()
                       if v.get("status") == "needs_reconciliation"}
            done = sum(1 for v in (d.get("nodes") or {}).values()
                       if v.get("status") == "success")
            tag = f"{p} [{st}] ok={done}"
            if pend:
                tag += f" PENDING_REVIEW={[(r.get('requestId'), r.get('nodeId')) for r in pend]}"
                hit = True
            if failed:
                tag += f" FAILED={ {k: (v.get('error') or '')[:80] for k, v in failed.items()} }"
                hit = True
            if needrec:
                tag += f" NEED_REC={list(needrec)}"
                hit = True
            lines.append(tag)
        print(f"[{time.strftime('%H:%M:%S')}] " + " || ".join(lines), flush=True)
        if hit:
            return
        time.sleep(30)
    print("timeout")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 7200)
