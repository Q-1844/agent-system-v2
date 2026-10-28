#!/usr/bin/env python3
"""C2 本地 60 秒验证 · Run 监控器（只读轮询，不干预调度）。

每 10s 采样一次：
  - ProjectRun 状态 / 场景状态
  - cache/jobs.json 中本项目的 job（state / started_at / finished_at / provider_task_id）
  - SceneRun（child）的镜头级状态
输出到 local_60s_evidence/run_monitor_timeline.jsonl + .json
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "local_60s_evidence"
BASE = "http://127.0.0.1:8000"
PROJECT = "c2_parallel_local60_20261027"
RUN_ID = "prun_f09e81f0d8"
JOBS = ROOT / "cache" / "jobs.json"
TERMINAL = {"completed", "failed", "blocked", "cancelled"}


def get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    t0 = time.time()
    jsonl = OUT / "run_monitor_timeline.jsonl"
    samples = []
    last_sig = None
    while True:
        rec = {"t": round(time.time(), 2), "elapsed_s": round(time.time() - t0, 1)}
        try:
            run = get(f"/api/project/{PROJECT}/production/run/{RUN_ID}")
            rec["run"] = {"status": run.get("status"), "stage": run.get("stage"),
                          "final_output": run.get("final_output"),
                          "final_duration_s": run.get("final_duration_s"),
                          "error": run.get("error"), "error_code": run.get("error_code"),
                          "scenes": {s["scene_id"]: s["status"] for s in run.get("scenes", [])},
                          "children": {s["scene_id"]: s.get("child_run_id") for s in run.get("scenes", [])}}
            # child scene run 的镜头状态
            shots = {}
            for sid, cid in (rec["run"]["children"] or {}).items():
                if not cid:
                    continue
                try:
                    child = get(f"/api/project/{PROJECT}/autopilot/run/{cid}")
                except Exception:
                    continue
                for st in child.get("shots", []):
                    shots[st["shot_id"]] = {"status": st.get("status"), "stage": st.get("stage"),
                                            "vv": st.get("video_version_id"),
                                            "warn": (st.get("warnings") or [])[-1:] }
            rec["shots"] = shots
        except Exception as e:
            rec["error"] = str(e)[:200]
        # jobs
        try:
            raw = json.loads(JOBS.read_text(encoding="utf-8"))
            data = list(raw.values()) if isinstance(raw, dict) else raw
            jb = {}
            for j in data:
                if j.get("project") != PROJECT:
                    continue
                jb[j["job_id"]] = {"kind": j.get("kind"), "shot": j.get("shot_id"),
                                   "state": j.get("state"), "started_at": j.get("started_at"),
                                   "finished_at": j.get("finished_at"),
                                   "provider_task_id": (j.get("params") or {}).get("provider_task_id"),
                                   "submit_state": (j.get("params") or {}).get("provider_submit_state"),
                                   "error": (j.get("error") or "")[:120]}
            rec["jobs"] = jb
        except Exception as e:
            rec["jobs_error"] = str(e)[:160]
        samples.append(rec)
        with jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        sig = json.dumps({k: rec.get(k) for k in ("run", "shots")}, ensure_ascii=False, sort_keys=True)
        if sig != last_sig:
            st = (rec.get("run") or {}).get("status")
            sh = rec.get("shots") or {}
            done = sum(1 for v in sh.values() if v["status"] in ("completed", "skipped_locked"))
            print(f"[{rec['elapsed_s']:>7.1f}s] run={st} shots_done={done}/12 "
                  f"videos_running="
                  f"{sum(1 for j in (rec.get('jobs') or {}).values() if j['kind']=='video' and j['state']=='running')}"
                  f" videos_done="
                  f"{sum(1 for j in (rec.get('jobs') or {}).values() if j['kind']=='video' and j['state']=='done')}",
                  flush=True)
            last_sig = sig
        if (rec.get("run") or {}).get("status") in TERMINAL:
            break
        if time.time() - t0 > 5400:
            rec["timeout"] = True
            break
        time.sleep(10)
    (OUT / "run_monitor_final.json").write_text(
        json.dumps(samples[-1], ensure_ascii=False, indent=2), encoding="utf-8")
    print("MONITOR DONE", flush=True)


if __name__ == "__main__":
    main()
