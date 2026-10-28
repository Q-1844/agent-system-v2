#!/usr/bin/env python3
"""C2 本地 60 秒验证 · 证据采集与判定（只读）。

输入：
  cache/p50_timing_ledger.jsonl   —— provider 级事件账本（video_submitted / video_completed）
  cache/jobs.json                 —— JobQueue 作业记录（含 provider_task_id / started_at / finished_at）
  projects/<p>/output/production_runs/<run_id>/run.json —— 冻结 DAG
  projects/<p>/output/project.json —— 镜头最终版本 / 末帧托管结果

输出：
  local_60s_evidence/provider_overlap_evidence.json
判定口径（与执行单一致）：
  · 只有「配置为 2」不算并行 -> 必须有真实执行区间重叠
  · 供应商侧在途重叠 = 两个 task 的 [submitted, completed] 区间真实相交
  · 接续镜头必须在来源镜头「当前版本」完成之后才提交视频任务
  · 同一镜头不得重复提交视频任务
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "local_60s_evidence"
PROJECT = "c2_parallel_local60_20261027"
RUN_ID = "prun_f09e81f0d8"

LEDGER = ROOT / "cache" / "p50_timing_ledger.jsonl"
JOBS = ROOT / "cache" / "jobs.json"
RUN_JSON = ROOT / "projects" / PROJECT / "output" / "production_runs" / RUN_ID / "run.json"
PROJ_JSON = ROOT / "projects" / PROJECT / "output" / "project.json"


def iso(epoch):
    return datetime.fromtimestamp(float(epoch), timezone.utc).astimezone().isoformat(timespec="milliseconds")


def main() -> int:
    report: dict = {"project": PROJECT, "run_id": RUN_ID}

    # ---------- 1. provider 事件账本 ----------
    events = []
    if LEDGER.exists():
        for ln in LEDGER.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln:
                try:
                    events.append(json.loads(ln))
                except Exception:
                    pass
    proj_events = [e for e in events if e.get("project") == PROJECT]
    submitted = {e["shot_id"]: e for e in proj_events if e.get("event") == "video_submitted"}
    completed = {e["shot_id"]: e for e in proj_events if e.get("event") == "video_completed"}
    downloaded = {e["shot_id"]: e for e in proj_events if e.get("event") == "video_downloaded"}

    report["provider_events"] = {
        "submitted_count": len(submitted),
        "completed_count": len(completed),
        "downloaded_count": len(downloaded),
        "shots_submitted": sorted(submitted),
    }

    # 每镜 provider 侧区间 [submitted, completed]
    intervals = []
    for shot_id, se in submitted.items():
        ce = completed.get(shot_id)
        intervals.append({
            "shot_id": shot_id,
            "provider_task_id": se.get("video_id"),
            "job_id": se.get("job_id"),
            "submitted_epoch": se["epoch"],
            "submitted_local": iso(se["epoch"]),
            "completed_epoch": ce["epoch"] if ce else None,
            "completed_local": iso(ce["epoch"]) if ce else None,
            "download_epoch": downloaded.get(shot_id, {}).get("epoch"),
            "remote_wait_s": round(ce["epoch"] - se["epoch"], 2) if ce else None,
            "seconds_requested": se.get("seconds_requested"),
        })
    intervals.sort(key=lambda x: x["submitted_epoch"])
    report["provider_intervals"] = intervals

    # ---------- 2. 供应商侧在途重叠 ----------
    overlaps = []
    for a, b in combinations(intervals, 2):
        if not a["completed_epoch"] or not b["completed_epoch"]:
            continue
        start = max(a["submitted_epoch"], b["submitted_epoch"])
        end = min(a["completed_epoch"], b["completed_epoch"])
        secs = end - start
        if secs > 0:
            overlaps.append({
                "shot_a": a["shot_id"], "shot_b": b["shot_id"],
                "task_a": a["provider_task_id"], "task_b": b["provider_task_id"],
                "overlap_seconds": round(secs, 2),
                "overlap_window_local": [iso(start), iso(end)],
            })
    overlaps.sort(key=lambda x: -x["overlap_seconds"])
    report["provider_inflight_overlaps"] = overlaps
    report["provider_concurrency_observed"] = bool(overlaps)
    report["max_provider_concurrency_hint"] = max(
        (sum(1 for i in intervals
             if i["submitted_epoch"] <= t < (i["completed_epoch"] or 0))
         for t in [x["submitted_epoch"] for x in intervals]), default=0)

    # ---------- 3. 冻结 DAG + 接续等待校验 ----------
    run = json.loads(RUN_JSON.read_text(encoding="utf-8"))
    deps = run.get("shot_dependencies") or {}
    waits = []
    for shot_id, sources in deps.items():
        for src in sources:
            se, ce = submitted.get(shot_id), completed.get(src)
            item = {"downstream": shot_id, "upstream": src,
                    "downstream_submitted_epoch": se["epoch"] if se else None,
                    "upstream_completed_epoch": ce["epoch"] if ce else None,
                    "downstream_task": se.get("video_id") if se else None,
                    "upstream_task": completed.get(src, {}).get("video_id")}
            if se and ce:
                item["waited_after_upstream_complete_s"] = round(se["epoch"] - ce["epoch"], 2)
                item["submitted_before_upstream_complete"] = se["epoch"] < ce["epoch"]
            waits.append(item)
    report["dependency_wait_checks"] = waits
    report["dependency_wait_all_clean"] = all(
        (not w.get("submitted_before_upstream_complete", False)) for w in waits)

    # ---------- 4. 重复提交检查 ----------
    sub_events = [e for e in proj_events if e.get("event") == "video_submitted"]
    counts = {}
    for e in sub_events:
        counts[e["shot_id"]] = counts.get(e["shot_id"], 0) + 1
    dup = {k: v for k, v in counts.items() if v > 1}
    report["duplicate_video_submissions"] = dup
    report["no_duplicate_submission"] = not dup
    report["distinct_provider_task_ids"] = len({e.get("video_id") for e in sub_events})

    # ---------- 5. JobQueue 侧视频 worker 区间 ----------
    jobs_raw = json.loads(JOBS.read_text(encoding="utf-8"))
    jobs = list(jobs_raw.values()) if isinstance(jobs_raw, dict) else jobs_raw
    vjobs = []
    for j in jobs:
        if j.get("project") != PROJECT or j.get("kind") != "video":
            continue
        if not j.get("started_at") or not j.get("finished_at"):
            continue
        vjobs.append({"job_id": j["job_id"], "shot_id": j.get("shot_id"),
                      "state": j.get("state"),
                      "started_epoch": j["started_at"], "finished_epoch": j["finished_at"],
                      "duration_s": round(j["finished_at"] - j["started_at"], 2),
                      "provider_task_id": (j.get("params") or {}).get("provider_task_id"),
                      "error": (j.get("error") or "")[:160]})
    vjobs.sort(key=lambda x: x["started_epoch"])
    report["video_jobs"] = vjobs
    report["video_job_count"] = len(vjobs)

    # ---------- 6. 最终产物 ----------
    final = run.get("final_output")
    final_abs = None
    if final:
        final_abs = Path(final)
        if not final_abs.is_absolute():
            final_abs = ROOT / final
    report["run_status"] = run.get("status")
    report["run_stage"] = run.get("stage")
    report["final_output"] = str(final_abs) if final_abs else None
    report["final_output_raw_in_run_json"] = final
    report["final_duration_s"] = run.get("final_duration_s")
    report["final_size_bytes"] = run.get("final_size_bytes")
    report["final_exists"] = bool(final_abs) and final_abs.exists()

    # 镜头最终版本 / 末帧托管情况
    if PROJ_JSON.exists():
        pj = json.loads(PROJ_JSON.read_text(encoding="utf-8"))
        shots = []
        for sc in pj.get("scenes", []):
            for sh in sc.get("shots", []):
                ef = sh.get("video_end_frame") or {}
                shots.append({
                    "shot_id": sh.get("shot_id"),
                    "scene_id": sc.get("scene_id"),
                    "final_video": sh.get("final_video"),
                    "video_status": (sh.get("video") or {}).get("status"),
                    "video_id": (sh.get("video") or {}).get("video_id"),
                    "seconds_returned": (sh.get("video") or {}).get("seconds_returned"),
                    "opening_source": sh.get("opening_source"),
                    "source_shot_id": sh.get("source_shot_id"),
                    "video_end_url": ef.get("url"),
                    "video_end_local": ef.get("local"),
                    "video_end_status": ef.get("status"),
                    "video_end_error": (ef.get("error") or "")[:200],
                    "video_end_source_version": sh.get("video_end_source_version"),
                    "num_video_versions": len(sh.get("video_versions") or []),
                })
        report["shots"] = shots

    (OUT / "provider_overlap_evidence.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- 控制台摘要 ----------
    print("=" * 78)
    print(f"run status      : {report.get('run_status')} / {report.get('run_stage')}")
    print(f"provider submits: {report['provider_events']['submitted_count']}  "
          f"completed: {report['provider_events']['completed_count']}  "
          f"distinct task ids: {report['distinct_provider_task_ids']}")
    print(f"duplicate submits: {dup or 'none'}")
    print(f"provider inflight overlap pairs: {len(overlaps)}")
    for o in overlaps[:6]:
        print(f"   {o['shot_a']} || {o['shot_b']}  overlap={o['overlap_seconds']}s  "
              f"window={o['overlap_window_local'][0]} -> {o['overlap_window_local'][1]}")
    print(f"dependency wait checks: {len(waits)}  all_clean={report['dependency_wait_all_clean']}")
    for w in waits:
        print(f"   {w['downstream']} <- {w['upstream']}: "
              f"waited_after_upstream_complete={w.get('waited_after_upstream_complete_s')}s "
              f"early_submit={w.get('submitted_before_upstream_complete')}")
    print(f"final_output    : {report['final_output']}")
    print(f"final_duration_s: {report['final_duration_s']}  exists={report['final_exists']}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
