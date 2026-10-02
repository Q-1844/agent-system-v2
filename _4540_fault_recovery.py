# -*- coding: utf-8 -*-
"""Phase 45.3 — 真机故障注入：委派进行中硬杀进程 → 重启后任务恢复。

phase=inject：启动 Producer→assembly 委派（真实 LLM，小目标），检测到子 Agent
首个工具事件后 os._exit(2) 硬杀（模拟后端崩溃；AgentTask 停在 running、无 execution_ref）。
phase=recover：新进程启动，AgentTaskSettler.recover_tasks() 对账 → 期望该 task 被
诚实结算为 failed(interrupted_by_backend_restart)；child session 消息保留在盘。
"""
import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
PROJECT = "雨灯归航"
EV = BASE / "_45"

GOAL = """请委派 Assembly Agent 做一件小事：把当前成片现状（seq_b746d7f3 的最新渲染与审计结论）写成一份提案（assembly.proposal_write）提交用户评审。不要做其它任何修改、不要渲染。"""


def _load_env():
    env = BASE / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _bootstrap():
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
    q = JobQueue(EV / "jobs_45_fault.json", deps.JOB_HANDLERS)
    deps.job_queue = q
    return deps, q


def _watchdog_kill(path: Path, delay_cap: float = 150.0):
    """子 Agent 首个工具事件出现后 12s 硬杀；封顶 150s。"""
    def _watch():
        t0 = time.time()
        seen = False
        while time.time() - t0 < delay_cap:
            try:
                if path.exists():
                    for line in path.read_text(encoding="utf-8").splitlines():
                        if '"agent": "assembly"' in line and "tool.started" in line \
                                and "assembly.delegate" not in line:
                            seen = True
                            break
            except OSError:
                pass
            if seen:
                time.sleep(12)
                print("[fault] child tool activity detected → os._exit(2)", flush=True)
                sys.stdout.flush()
                os._exit(2)
            time.sleep(1.0)
        print("[fault] watchdog timeout → kill anyway", flush=True)
        sys.stdout.flush()
        os._exit(2)
    threading.Thread(target=_watch, daemon=True).start()


async def phase_inject() -> int:
    from src.agents.runtime import get_agent_runtime
    from src.agents.producer import producer_turn
    deps, q = await _bootstrap_start()
    try:
        rt = get_agent_runtime()
        store = rt._session_store(PROJECT)
        s = store.get_or_create("producer", "project", f"p45_fault_{int(time.time())}")
        ev = EV / "events_45c_inject.jsonl"
        if ev.exists():
            ev.unlink()
        _watchdog_kill(ev)
        print(f"[inject] producer session {s.session_id} — delegating (will be killed mid-child)",
              flush=True)
        result = await producer_turn(rt, PROJECT, s.session_id, GOAL,
                                     event_cb=_mk_cb(ev))
        print("[inject] UNEXPECTED: turn completed before kill:",
              str(result.get("assistant_message"))[:120], flush=True)
        return 1
    finally:
        stop = getattr(q, "stop", None)
        if stop:
            await stop()


def _mk_cb(path):
    def cb(ev):
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass
    return cb


async def _bootstrap_start():
    deps, q = _bootstrap()
    await q.start()
    return deps, q


async def phase_recover() -> int:
    deps, q = await _bootstrap_start()
    try:
        from src.agents.runtime import get_agent_runtime
        from src.agents.settlement import AgentTaskSettler
        from src.agents.tasks import AgentTaskStore
        rt = get_agent_runtime()
        pdir = rt._resolve_project_dir(PROJECT)
        store = AgentTaskStore(pdir, PROJECT)
        running_before = [(t.task_id, t.status) for t in store.list(status="running")]
        print(f"[recover] running tasks before: {running_before}", flush=True)
        settler = AgentTaskSettler(
            project_lister=lambda: [(PROJECT, pdir)],
            job_queue_getter=lambda: q)
        n = await settler.recover_tasks()
        print(f"[recover] settled {n} task(s)", flush=True)
        evidence = {}
        for tid, _ in running_before:
            t = store.get(tid)
            evidence[tid] = {
                "status": t.status,
                "error": (t.error or "")[:160],
                "warnings": (t.structured_result or {}).get("warnings"),
                "data": (t.structured_result or {}).get("data"),
                "session_id": t.session_id,
            }
            sess = rt._session_store(PROJECT).load(t.session_id) if t.session_id else None
            evidence[tid]["child_session_messages"] = len(sess.messages) if sess else 0
            evidence[tid]["child_session_tools"] = sum(
                1 for m in (sess.messages if sess else []) if m.role == "tool")
        (EV / "45c_recovery_evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=False, indent=1))
        return 0
    finally:
        stop = getattr(q, "stop", None)
        if stop:
            await stop()


if __name__ == "__main__":
    _load_env()
    phase = sys.argv[1] if len(sys.argv) > 1 else ""
    if phase == "inject":
        sys.exit(asyncio.run(phase_inject()))
    elif phase == "recover":
        sys.exit(asyncio.run(phase_recover()))
    else:
        print("usage: _4540_fault_recovery.py inject|recover")
        sys.exit(2)
