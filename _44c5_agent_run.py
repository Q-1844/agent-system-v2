# -*- coding: utf-8 -*-
"""Phase 44C — 正式 Assembly Agent 声音会话运行器（真实 Agnes LLM + 真实 MiniMax TTS）。

T1 旁白设计→TTS→AudioPlan→发起渲染；驱动器等 JobQueue；T2 核验→分轨→manifest→提案。
证据：_44c/events_cagent_{1,2}.jsonl、result_cagent_{1,2}.json、
session_after_cagent.json、cagent_run_summary.json。
用法：python _44c5_agent_run.py [--fresh]
"""
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
PROJECT = "雨灯归航"
EV = BASE / "_44c"


def _load_env():
    env = BASE / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _bootstrap_deps():
    import deps
    import yaml
    from src.core.rate_limiter import RateLimiter
    from src.core.quota import QuotaLedger
    cfg = yaml.safe_load((BASE / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    RateLimiter.get((cfg.get("agnes") or {}).get("rpm"))
    QuotaLedger.get_default(BASE / "cache" / "quota.json", limits=cfg.get("quota"))
    import routers.shots      # noqa: F401
    import routers.sequences  # noqa: F401
    from src.pipeline.controller import PipelineController
    deps.pipeline_controller = PipelineController(str(BASE / "config" / "config.yaml"))
    deps.agnes_client = deps.pipeline_controller.agnes_client
    deps.project_manager = deps.pipeline_controller.project_manager
    from src.core.job_queue import JobQueue
    q = JobQueue(EV / "jobs_cagent.json", deps.JOB_HANDLERS)
    deps.job_queue = q
    return deps, q


def _mk_cb(path, t0):
    def cb(ev):
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
            t = str(ev.get("type") or "")
            if t in ("tool.started", "tool.completed", "tool.failed"):
                print(f"  [{int(time.time()-t0):>5}s] {t} {ev.get('name') or ''} "
                      f"ok={ev.get('ok')} "
                      f"{str(ev.get('summary') or ev.get('error') or '')[:88]}",
                      flush=True)
        except Exception:
            pass
    return cb


async def _run_turn(rt, sid, task_file, n):
    from src.agents.assembly import assembly_turn
    task = task_file.read_text(encoding="utf-8").strip()
    ev_path = EV / f"events_cagent_{n}.jsonl"
    if ev_path.exists():
        ev_path.unlink()
    t0 = time.time()
    TRANSIENT = ("500", "502", "503", "504", "do_request_failed",
                 "timeout", "connection", "stream 错误")
    result = None
    for attempt in range(1, 6):
        failure = None
        try:
            result = await assembly_turn(rt, PROJECT, sid, task,
                                         event_cb=_mk_cb(ev_path, t0))
            if str(result.get("assistant_message") or "").startswith("[执行失败"):
                failure = result["assistant_message"]
        except Exception as e:   # noqa: BLE001
            result = None
            failure = f"{type(e).__name__}: {e}"
        if failure is None:
            break
        if attempt >= 5 or not any(s in failure for s in TRANSIENT):
            if result is None:
                raise
            break
        wait_s = min(30 * attempt, 120)
        print(f"[retry {attempt}/5] {failure[:150]} → {wait_s}s", flush=True)
        await asyncio.sleep(wait_s)
        result = None
    assert result is not None
    (EV / f"result_cagent_{n}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    print(f"\n=== agent turn {n} done in {int(time.time()-t0)}s ===")
    for tl in result.get("trace") or []:
        print(f"  - {tl.get('tool')} {tl.get('result')}")
    print("\nreply head:\n" + (result.get("assistant_message") or "")[:1500])
    return result, time.time() - t0


async def run(fresh: bool) -> int:
    EV.mkdir(parents=True, exist_ok=True)
    deps, q = _bootstrap_deps()
    await q.start()
    from services.lifecycle import recover_pending_videos
    await recover_pending_videos(queue=q)
    try:
        from src.agents.runtime import get_agent_runtime
        rt = get_agent_runtime()
        store = rt._session_store(PROJECT)
        if fresh:
            s = store.get_or_create("assembly", "project",
                                    f"p44c_agent_{int(time.time())}")
        else:
            s = store.get_or_create("assembly", "project",
                                    f"p44c_agent_{int(time.time())}")
        sid = s.session_id
        print(f"assembly session: {sid}")

        r1, d1 = await _run_turn(rt, sid, EV / "task_c_agent.txt", 1)

        seq_id = rvid = None
        for r in (r1.get("receipts") or []):
            if r.get("operation") == "assembly.render_submit" and r.get("ok"):
                seq_id, rvid = r.get("sequence_id"), r.get("render_version_id")
        wait_state = {"sequence_id": seq_id, "render_version_id": rvid,
                      "final_status": None, "wait_s": None}
        if seq_id and rvid:
            from src.sequence.service import SequenceService
            svc = SequenceService(BASE / "projects" / PROJECT, PROJECT)
            t0 = time.time()
            row = None
            while time.time() - t0 < 1200:
                row = svc.get_entry(seq_id).get_render(rvid)
                if row and row.status in ("done", "failed", "raced_superseded"):
                    break
                await asyncio.sleep(3)
            wait_state["final_status"] = row.status if row else "timeout"
            wait_state["wait_s"] = round(time.time() - t0, 1)
            print(f"=== render {rvid} → {wait_state['final_status']} ===")
        else:
            print("!!! T1 无 render_submit 成功回执（如实记录）")
        (EV / "cagent_wait_state.json").write_text(
            json.dumps(wait_state, ensure_ascii=False, indent=1), encoding="utf-8")

        r2, d2 = await _run_turn(rt, sid, EV / "task_c_agent2.txt", 2)
        shutil.copy2(rt._session_store(PROJECT)._path(sid),
                     EV / "session_after_cagent.json")
        summary = {"session_id": sid, "turn1_seconds": round(d1, 1),
                   "turn2_seconds": round(d2, 1), "render": wait_state,
                   "turn1_receipts": r1.get("receipts"),
                   "turn2_receipts": r2.get("receipts")}
        (EV / "cagent_run_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n=== summary ===")
        print(json.dumps({k: v for k, v in summary.items()
                          if not k.endswith("receipts")},
                         ensure_ascii=False, indent=1))
        return 0
    finally:
        stop = getattr(q, "stop", None)
        if stop:
            await stop()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true")
    a = ap.parse_args()
    _load_env()
    sys.exit(asyncio.run(run(a.fresh)))
