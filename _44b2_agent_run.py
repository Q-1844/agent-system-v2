# -*- coding: utf-8 -*-
"""Phase 44B — B4.5 正式 Assembly Agent 会话运行器（真实 Agnes LLM，进程内）。

两段 turn：
  T1  任务书 _44b/task_agent.txt —— briefing→草案→预演→创建→发起渲染（发起即收尾）
  驱动器等待 JobQueue 完成渲染（Agent 不空转烧步数）
  T2  任务书 _44b/task_agent2.txt —— render_status(deep_audit)→manifest_publish→proposal_write

证据：_44b/events_agent_{1,2}.jsonl + result_agent_{1,2}.json + session_after_agent.json。
用法：python _44b2_agent_run.py [--fresh]
"""
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

PROJECT = "雨灯归航"
EV = BASE_DIR / "_44b"


def _load_env():
    env_file = BASE_DIR / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _bootstrap_deps():
    import deps
    import yaml
    from src.core.rate_limiter import RateLimiter
    from src.core.quota import QuotaLedger
    _APP_CFG = yaml.safe_load(
        (BASE_DIR / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    _agnes_cfg = _APP_CFG.get("agnes") or {}
    RateLimiter.get(_agnes_cfg.get("rpm"))
    QuotaLedger.get_default(BASE_DIR / "cache" / "quota.json",
                            limits=_APP_CFG.get("quota"))
    import routers.shots      # noqa: F401
    import routers.sequences  # noqa: F401
    from src.pipeline.controller import PipelineController
    deps.pipeline_controller = PipelineController(
        str(BASE_DIR / "config" / "config.yaml"))
    deps.agnes_client = deps.pipeline_controller.agnes_client
    deps.project_manager = deps.pipeline_controller.project_manager
    from src.core.job_queue import JobQueue
    q = JobQueue(EV / "jobs_agent.json", deps.JOB_HANDLERS)
    deps.job_queue = q
    return deps, q


def _mk_cb(path, t0):
    def cb(ev):
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
            t = str(ev.get("type") or "")
            if t in ("tool.started", "tool.completed", "tool.failed"):
                name = ev.get("name") or ""
                print(f"  [{int(time.time() - t0):>5}s] {t} {name} ok={ev.get('ok')} "
                      f"{str(ev.get('summary') or ev.get('error') or '')[:90]}",
                      flush=True)
        except Exception:
            pass
    return cb


async def _run_turn(rt, sid, task_file, n,TRANSIENT_SIGNS):
    task = task_file.read_text(encoding="utf-8").strip()
    ev_path = EV / f"events_agent_{n}.jsonl"
    if ev_path.exists():
        ev_path.unlink()
    t0 = time.time()
    from src.agents.assembly import assembly_turn
    result = None
    for attempt in range(1, 6):
        failure = None
        try:
            result = await assembly_turn(rt, PROJECT, sid, task,
                                         event_cb=_mk_cb(ev_path, t0))
            raw = str(result.get("assistant_message") or "")
            if raw.startswith("[执行失败"):
                failure = raw
        except Exception as e:   # noqa: BLE001
            result = None
            failure = f"{type(e).__name__}: {e}"
        if failure is None:
            break
        transient = any(sig in failure for sig in TRANSIENT_SIGNS)
        if attempt >= 5 or not transient:
            if result is None:
                raise
            break
        wait_s = min(30 * attempt, 120)
        print(f"\n[retry {attempt}/5] turn 失败（{failure[:160]}）→ {wait_s}s 后重发",
              flush=True)
        await asyncio.sleep(wait_s)
        result = None
    assert result is not None
    dur = time.time() - t0
    (EV / f"result_agent_{n}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    tools = [(t.get("tool"), t.get("result")) for t in (result.get("trace") or [])]
    print(f"\n=== agent turn {n} completed in {int(dur)}s ===")
    for name, ok in tools:
        print(f"  - {name} {ok}")
    print(f"\nreply head:\n{(result.get('assistant_message') or '')[:1600]}")
    return result, dur


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
                                    f"p44b_agent_{int(time.time())}")
        else:
            # 复用最近一个 assembly 会话（中断续跑）
            latest = None
            sdir = BASE_DIR / "projects" / PROJECT / "output" / "agents" / "sessions"
            if sdir.exists():
                cands = sorted(sdir.glob("*.json"), key=lambda p: p.stat().st_mtime)
                for c in reversed(cands):
                    d = json.loads(c.read_text(encoding="utf-8"))
                    if d.get("agent_id") == "assembly":
                        latest = d.get("session_id")
                        break
            s = store.load(latest) if latest else None
            if s is None:
                s = store.get_or_create("assembly", "project",
                                        f"p44b_agent_{int(time.time())}")
        sid = s.session_id
        print(f"assembly session: {sid}（messages={len(s.messages)}）")

        TRANSIENT_SIGNS = ("500", "502", "503", "504", "do_request_failed",
                           "timeout", "connection", "stream 错误")
        # ---- T1：装配 + 发起渲染 ----
        r1, d1 = await _run_turn(rt, sid, EV / "task_agent.txt", 1, TRANSIENT_SIGNS)

        # ---- 驱动器等渲染完成（Agent 不空转）----
        seq_id = rvid = None
        for r in (r1.get("receipts") or []):
            if r.get("operation") == "assembly.render_submit" and r.get("ok"):
                seq_id = r.get("sequence_id")
                rvid = r.get("render_version_id")
        wait_state = {"sequence_id": seq_id, "render_version_id": rvid,
                      "final_status": None, "wait_s": None}
        if seq_id and rvid:
            from src.sequence.service import SequenceService
            svc = SequenceService(BASE_DIR / "projects" / PROJECT, PROJECT)
            t0 = time.time()
            while time.time() - t0 < 1200:
                row = svc.get_entry(seq_id).get_render(rvid)
                if row and row.status in ("done", "failed", "raced_superseded"):
                    break
                await asyncio.sleep(3)
            wait_state["final_status"] = row.status if row else "timeout"
            wait_state["wait_s"] = round(time.time() - t0, 1)
            print(f"=== render {rvid} → {wait_state['final_status']} "
                  f"（waited {wait_state['wait_s']}s）===")
        else:
            print("!!! T1 未产出 render_submit 成功回执——如实记录（Agent 验收失败证据）")
        (EV / "agent_wait_state.json").write_text(
            json.dumps(wait_state, ensure_ascii=False, indent=1), encoding="utf-8")

        # ---- T2：核验 + 发布 + 提案 ----
        r2, d2 = await _run_turn(rt, sid, EV / "task_agent2.txt", 2, TRANSIENT_SIGNS)

        store2 = rt._session_store(PROJECT)
        shutil.copy2(store2._path(sid), EV / "session_after_agent.json")
        summary = {
            "session_id": sid,
            "turn1_seconds": round(d1, 1), "turn2_seconds": round(d2, 1),
            "render": wait_state,
            "turn1_receipts": r1.get("receipts"),
            "turn2_receipts": r2.get("receipts"),
        }
        (EV / "agent_run_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n=== agent run summary ===")
        print(json.dumps({k: v for k, v in summary.items()
                          if k not in ("turn1_receipts", "turn2_receipts")},
                         ensure_ascii=False, indent=1))
        return 0
    finally:
        stop = getattr(q, "stop", None)
        if stop:
            await stop()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true")
    a = ap.parse_args()
    _load_env()
    rc = asyncio.run(run(a.fresh))
    sys.exit(rc)


if __name__ == "__main__":
    main()
