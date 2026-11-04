"""第05轮 Gate B2 夹具：真实**子进程** worker（不是线程）。

由 tests/test_job_queue_process_ownership.py 以 subprocess 拉起，用于验证
JobQueue 的跨进程所有权 / 崩溃恢复 / 提交幂等语义。

🔴 本文件只使用本地**合成账本** + fake handler：
- 不指向 BASE_DIR/cache/jobs.json，不指向真实 projects/，不写生产树；
- 不发起任何网络请求，不读取任何凭据（父进程已剥离凭据类环境变量）；
- 不做任何 Git 操作。

模式：
- claim  : 装载账本（取得所有权）→ 等放行信号 → 领跑已存在的 queued job
- submit : 按 origin_run_id/stage_key **幂等**提交（已有则复用，不新建）
- hold   : 取得所有权后**保持存活**（模拟活着的 owner），供父进程做冲突/接管实验
- api    : 启动 api_server（TestClient）→ 验证所有权冲突时 fail-fast、零副作用
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".writing")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _bootstrap() -> None:
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _wait_for(path: Path, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.exists():
            return True
        time.sleep(0.02)
    return path.exists()


# ---------------------------------------------------------------- claim
async def _mode_claim(args) -> int:
    _bootstrap()
    from src.core.job_queue import JobQueue

    attempts_dir = Path(args.attempts_dir)
    attempts_dir.mkdir(parents=True, exist_ok=True)
    state = {"mode": "claim", "pid": os.getpid(), "handler_calls": 0}

    async def handler(job, emit):
        state["handler_calls"] += 1
        marker = attempts_dir / f"{job.job_id}_{os.getpid()}.json"
        started = time.time()
        _write_json(marker, {"job_id": job.job_id, "pid": os.getpid(),
                             "started_at": started, "finished_at": None})
        await emit("fake", "synthetic local handler (no provider)", 50)
        await asyncio.sleep(max(0, args.work_ms) / 1000.0)
        _write_json(marker, {"job_id": job.job_id, "pid": os.getpid(),
                             "started_at": started, "finished_at": time.time()})

    try:
        # 取得所有权（跨进程冲突 → JobQueueOwnershipError → fail closed）
        q = JobQueue(Path(args.ledger), {"video": handler}, video_worker_count=2)
    except Exception as exc:
        _write_json(Path(args.out), {
            "mode": "claim", "pid": os.getpid(), "status": "rejected_or_error",
            "error_type": type(exc).__name__, "error": str(exc)[:600],
            "handler_calls": 0,
        })
        return 23

    state["owns_ledger"] = q.owns_ledger
    if args.ready_file:
        _write_json(Path(args.ready_file), {"pid": os.getpid(), "ready": True})
    if args.go_file:
        if not _wait_for(Path(args.go_file), args.go_wait_s):
            _write_json(Path(args.out), dict(state, status="go_timeout"))
            return 4
    try:
        await q.start()
        # 档位在 start() 之后才是实际生效值（resolve_video_inflight）
        state["video_worker_count"] = q.video_worker_count
        state["video_inflight_source"] = q.video_inflight_source
        deadline = time.monotonic() + args.wait_s
        while time.monotonic() < deadline:
            if all(j.state in ("done", "failed", "cancelled") for j in q.jobs.values()):
                break
            await asyncio.sleep(0.02)
        for t in q._tasks:
            t.cancel()
        await asyncio.gather(*q._tasks, return_exceptions=True)
        state.update({"status": "finished",
                      "states": {k: v.state for k, v in q.jobs.items()},
                      "jobs": sorted(q.jobs.keys())})
    except Exception as exc:
        state.update({"status": "error", "error_type": type(exc).__name__,
                      "error": str(exc)[:600]})
    finally:
        q.close()
    _write_json(Path(args.out), state)
    return 0


# --------------------------------------------------------------- submit
async def _mode_submit(args) -> int:
    _bootstrap()
    from src.core.job_queue import JobQueue

    out = {"mode": "submit", "pid": os.getpid()}
    try:
        q = JobQueue(Path(args.ledger), {}, video_worker_count=2)
    except Exception as exc:
        out.update({"status": "rejected_or_error", "error_type": type(exc).__name__,
                    "error": str(exc)[:600], "created_job": False})
        _write_json(Path(args.out), out)
        return 23
    try:
        # 幂等：有同 origin/stage 的既有 job → 复用（不新建、不重复提交）
        existing = q.find_by_origin(args.origin_run_id, args.stage_key)
        if existing is not None:
            out.update({"status": "reused", "created_job": False,
                        "existing_job_id": existing.job_id,
                        "existing_state": existing.state,
                        "persisted_jobs": sorted(q.jobs.keys())})
        else:
            job = q.submit("round05_fake_project", "video", "s05_c99",
                           origin_run_id=args.origin_run_id,
                           autopilot_stage_key=args.stage_key)
            out.update({"status": "created", "created_job": True,
                        "job_id": job.job_id,
                        "persisted_jobs": sorted(q.jobs.keys())})
    except Exception as exc:
        out.update({"status": "error", "error_type": type(exc).__name__,
                    "error": str(exc)[:600]})
    finally:
        q.close()
    _write_json(Path(args.out), out)
    return 0


# ----------------------------------------------------------------- hold
def _mode_hold(args) -> int:
    _bootstrap()
    from src.core.job_queue import JobQueue

    out = {"mode": "hold", "pid": os.getpid()}
    try:
        q = JobQueue(Path(args.ledger), {}, video_worker_count=2)
    except Exception as exc:
        out.update({"status": "rejected_or_error", "error_type": type(exc).__name__,
                    "error": str(exc)[:600]})
        _write_json(Path(args.out), out)
        return 23
    out.update({"status": "holding", "owns_ledger": q.owns_ledger})
    _write_json(Path(args.out), out)
    deadline = time.monotonic() + args.hold_seconds
    release = Path(args.release_file) if args.release_file else None
    while time.monotonic() < deadline:
        if release is not None and release.exists():
            break
        time.sleep(0.02)
    q.close()
    out.update({"status": "released"})
    _write_json(Path(args.out), out)
    return 0


# ------------------------------------------------------------------- api
def _mode_api(args) -> int:
    _bootstrap()
    out = {"mode": "api", "pid": os.getpid(), "fake_base": args.fake_base}
    fake_base = Path(args.fake_base)
    (fake_base / "projects").mkdir(parents=True, exist_ok=True)
    (fake_base / "cache").mkdir(parents=True, exist_ok=True)
    (fake_base / "shared_library" / "media").mkdir(parents=True, exist_ok=True)
    try:
        import deps
        # 把 runtime 根指向隔离的 fake base（Gate D：与真实树完全隔离）
        deps.BASE_DIR = fake_base
        deps.projects_dir = fake_base / "projects"
        import api_server
    except Exception as exc:
        out.update({"status": "import_error", "error_type": type(exc).__name__,
                    "error": str(exc)[:600]})
        _write_json(Path(args.out), out)
        return 3
    try:
        from fastapi.testclient import TestClient
        with TestClient(api_server.app) as client:
            out.update({"status": "started",
                        "deps_job_queue": repr(getattr(deps, "job_queue", None)),
                        "conflict": getattr(deps, "job_queue_conflict", None)})
            _write_json(Path(args.out), out)
            return 0
    except Exception as exc:
        out.update({"status": "rejected_startup", "error_type": type(exc).__name__,
                    "error": str(exc)[:600],
                    "conflict": getattr(deps, "job_queue_conflict", None)})
        _write_json(Path(args.out), out)
        return 23


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", required=True,
                    choices=("claim", "submit", "hold", "api"))
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--attempts-dir", default=None)
    ap.add_argument("--work-ms", type=int, default=400)
    ap.add_argument("--wait-s", type=float, default=30.0)
    ap.add_argument("--go-wait-s", type=float, default=30.0)
    ap.add_argument("--ready-file", default=None)
    ap.add_argument("--go-file", default=None)
    ap.add_argument("--origin-run-id", default="round05_run_x")
    ap.add_argument("--stage-key", default="video:s05_c99")
    ap.add_argument("--release-file", default=None)
    ap.add_argument("--hold-seconds", type=float, default=60.0)
    ap.add_argument("--fake-base", default=None)
    args = ap.parse_args()

    if args.mode == "claim":
        return asyncio.run(_mode_claim(args))
    if args.mode == "submit":
        return asyncio.run(_mode_submit(args))
    if args.mode == "hold":
        return _mode_hold(args)
    if args.mode == "api":
        return _mode_api(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
