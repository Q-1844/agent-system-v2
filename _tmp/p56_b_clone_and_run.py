# Phase56 B：批准包克隆与基准 run 驱动（测试设定基础设施——取证与触发，不代写 canonical）。
#
# 用法：
#   python _tmp/p56_b_clone_and_run.py clone <dst_name>            # 从冻结源克隆并清视频侧
#   python _tmp/p56_b_clone_and_run.py run <proj> <inflight>       # 启动 combo v2 run 并轮询到终态
#   python _tmp/p56_b_clone_and_run.py evidence <proj> <run_id>    # 导出单 run 时间线证据
#
# 克隆语义（02 文件）：从同一批准快照新克隆；被测视频输出/选版指针/审核状态/
# 已完成标记不继承；基础参考（Looks/场景/绑定/首帧）只读复制。
import json
import shutil
import sys
import time
from pathlib import Path

import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SRC = "P56_P1_TESTIMONY"
API = "http://127.0.0.1:8000"

import os
SRC = os.environ.get("P56_CLONE_SRC", SRC)   # P2 包克隆：P56_CLONE_SRC=P56_P2_SIGNAL


def api(method, path, payload=None, timeout=120):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{API}{path}", data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def clone_project(dst_name: str) -> Path:
    src = ROOT / "projects" / SRC
    dst = ROOT / "projects" / dst_name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    pj = dst / "output" / "project.json"
    d = json.loads(pj.read_text(encoding="utf-8"))
    d["name"] = dst_name
    n_cleared = 0
    for sc in d.get("scenes", []):
        for sh in sc.get("shots", []):
            sh["video_versions"] = []
            sh["final_video"] = None
            sh["selected_last_frame"] = None
            v = sh.get("video") or {}
            v.update({"status": "pending", "video_id": None, "task_id": None,
                      "local": None, "url": None, "error": None,
                      "seconds_returned": None, "size_returned": None})
            v.pop("progress", None)   # VideoAsset 无此字段——B1 实证：多余键=全 canonical 读炸
            sh["video"] = v
            sh["video_end_frame"] = {"status": "pending"}
            sh["video_end_source_version"] = None
            n_cleared += 1
    pj.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    for sub in ("videos", "sequences", "audio", "subtitles", "full_runs", "production_runs"):
        p = dst / "output" / sub
        if p.exists():
            shutil.rmtree(p)
    ag = dst / "output" / "agents"
    for sub in ("collab_runs", "review_pipeline", "handoffs", "usage", "sessions",
                "change_analysis"):
        p = ag / sub
        if p.exists():
            shutil.rmtree(p)
    for f in ("reference_policy.json", "P56_freeze_manifest.json", "work_memory.json",
              "direct_edit_audit.jsonl", "creative_brief.json",
              "master_lease.json",   # Phase56 B1 实证：源项目租约不清 → 新会话 lease_denied
              "grants.json"):        # 授权状态属源 run——克隆 run 启动时重新 grant
        p = ag / f
        if p.exists():
            p.unlink()
    print(f"clone ok: {dst_name}（{n_cleared} 镜视频侧已清；首帧/绑定/Look/场景保留）")
    return dst


def start_run(project: str, inflight: int):
    r = api("POST", f"/api/project/{project}/collab-runs", {
        "mode_id": "combo_recommended",
        "params": {
            "story_goal": "P1《断电前的证词》批准工作包视频生产（Phase56 B 基准 run，"
                          "冻结清单 output/agents 冻结源 eb6c58ea0bcf）",
            "approved_package": True,
            "media_inflight": int(inflight),
            "duration_hint": "30-42 秒",
        },
        "granted_by": "phase56_b_benchmark",
    })
    run = r["run"]
    print(f"run started: {run['run_id']} | mode={run['mode_id']} v{run['mode_version']} "
          f"| inflight={inflight} | used_default={r.get('used_default_mode')}")
    return run["run_id"]


def wait_run(project: str, run_id: str, poll_s: float = 15.0, max_s: float = 5400.0):
    t0 = time.time()
    last_steps = ""
    while time.time() - t0 < max_s:
        try:
            run = api("GET", f"/api/project/{project}/collab-runs/{run_id}")["run"]
        except Exception as e:
            print(f"[{int(time.time()-t0)}s] poll error: {e}")
            time.sleep(poll_s)
            continue
        steps = "; ".join(f"{s['step_id']}:{s['status']}" for s in run.get("steps") or [])
        if steps != last_steps:
            print(f"[{int(time.time()-t0)}s] {run['status']} | {steps}")
            last_steps = steps
        if run["status"] in ("completed", "failed", "cancelled", "awaiting_relay"):
            return run
        time.sleep(poll_s)
    raise TimeoutError(f"run {run_id} 未在 {max_s}s 内到终态")


def export_evidence(project: str, run_id: str):
    run = api("GET", f"/api/project/{project}/collab-runs/{run_id}")["run"]
    jobs = json.loads((ROOT / "cache" / "jobs.json").read_text(encoding="utf-8"))
    t0 = run.get("created_at") or 0
    media = []
    for j in jobs.values():
        if j.get("project") == project and j.get("kind") == "video" \
                and j.get("created_at", 0) >= t0:   # 只取本 run 窗口（jobs.json 全局累计）
            media.append({
                "job_id": j["job_id"], "shot_id": j.get("shot_id"),
                "state": j["state"], "created_at": j["created_at"],
                "started_at": j.get("started_at"), "finished_at": j.get("finished_at"),
                "provider_task_id": (j.get("params") or {}).get("provider_task_id"),
                "origin_run_id": (j.get("params") or {}).get("origin_run_id"),
                "repair_of_review": (j.get("params") or {}).get("repair_of_review"),
                "repair_attempt": (j.get("params") or {}).get("repair_attempt"),
                "error": (j.get("error") or "")[:160],
            })
    media.sort(key=lambda x: x["created_at"])
    out = {
        "schema": "p56_b_run_evidence_v1",
        "project": project, "run_id": run_id,
        "mode_id": run.get("mode_id"), "mode_version": run.get("mode_version"),
        "snapshot_hash": run.get("snapshot_hash"),
        "status": run.get("status"),
        "created_at": run.get("created_at"), "finished_at": run.get("finished_at"),
        "concurrency_effect": run.get("concurrency_effect"),
        "authorization": run.get("authorization"),
        "steps": run.get("steps"),
        "review": run.get("review"), "repair": run.get("repair"),
        "relay_count": run.get("relay_count"),
        "result": (run.get("result") or {}),
        "media_jobs": media,
    }
    p = ROOT / "_tmp" / f"p56_b_evidence_{project}_{run_id}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("evidence ->", p)
    ce = out.get("concurrency_effect") or {}
    print(f"status={out['status']} | inflight requested={ce.get('requested_media_inflight')} "
          f"applied={ce.get('applied')} workers={ce.get('workers')} src={str(ce.get('source'))[:40]}")
    print(f"video jobs: {len(media)} | states: {[m['state'] for m in media]}")
    return out


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "clone":
        clone_project(sys.argv[2])
    elif cmd == "run":
        rid = start_run(sys.argv[2], int(sys.argv[3]))
        print("RUN_ID:", rid)
        run = wait_run(sys.argv[2], rid)
        print("FINAL:", run["status"])
        export_evidence(sys.argv[2], rid)
    elif cmd == "evidence":
        export_evidence(sys.argv[2], sys.argv[3])
