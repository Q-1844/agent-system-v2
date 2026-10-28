#!/usr/bin/env python3
"""C2 本地 60 秒验证 · 一次性收尾（等终态 -> 并行审计 -> 证据采集 -> 成片校验）。

只做一次长等待（轮询 15s），不干预调度。终态后依次执行：
  1. c2_parallel_jobs_audit.py（执行单同包工具）
  2. collect_evidence.py（provider 在途重叠 / 接续等待 / 重复提交）
  3. verify_final_film.py（ffprobe + 分镜抽帧 + 顺序核对）
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "local_60s_evidence"
TESTROOT = Path(r"C:\Users\曹文淇\Desktop\ai工作流\测试25")
BASE = "http://127.0.0.1:8000"
PROJECT = "c2_parallel_local60_20261027"
RUN_ID = "prun_f09e81f0d8"
RUNDIR = ROOT / "projects" / PROJECT / "output" / "production_runs" / RUN_ID
TERMINAL = {"completed", "failed", "blocked", "cancelled"}


def get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    print(f"$ {' '.join(str(c) for c in cmd)}\n  rc={r.returncode}\n{r.stdout}\n{r.stderr}\n",
          flush=True)
    return r.returncode


def main():
    t0 = time.time()
    status = None
    while time.time() - t0 < 2400:
        try:
            run = get(f"/api/project/{PROJECT}/production/run/{RUN_ID}")
            status = run.get("status")
            if status in TERMINAL:
                print(f"[finalize] run terminal: {status} @ {time.strftime('%H:%M:%S')}", flush=True)
                break
        except Exception as e:
            print(f"[finalize] poll error: {e}", flush=True)
        time.sleep(15)
    else:
        print("[finalize] timeout waiting for terminal state", flush=True)

    # 1) 执行单同包并行审计工具
    sh([sys.executable, str(TESTROOT / "c2_parallel_jobs_audit.py"),
        "--jobs", str(ROOT / "cache" / "jobs.json"),
        "--run", str(RUNDIR / "run.json"),
        "--out", str(OUT / "parallel_audit.json")])
    # 1b) 工具自检
    sh([sys.executable, str(TESTROOT / "c2_parallel_jobs_audit.py"), "--self-test"])

    # 2) provider 级证据
    sh([sys.executable, str(OUT / "collect_evidence.py")])

    # 3) 成片校验
    sh([sys.executable, str(OUT / "verify_final_film.py")])

    print("[finalize] ALL DONE", flush=True)


if __name__ == "__main__":
    main()
