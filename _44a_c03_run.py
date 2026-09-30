# -*- coding: utf-8 -*-
"""Phase 44A — s02_c03 开门动作迭代运行器（进程内正式入口，真实 API）。

驱动方式与 _43_repair_run.py 相同（生产装配+recover 钩子+隔离队列），
证据独立落 _44a/，不污染 Phase 43 证据目录。任务文件：_44a/task_c03_iter.txt。
用法：python _44a_c03_run.py --continue sess_7efa81c69a83
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
EVROOT = BASE_DIR / "_44a"
TASK_FILE = EVROOT / "task_c03_iter.txt"


def _set_task(p: Path):
    global TASK_FILE
    TASK_FILE = p


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
    from src.core.rate_limiter import RateLimiter
    from src.core.quota import QuotaLedger
    import yaml
    _APP_CFG = yaml.safe_load((BASE_DIR / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    _agnes_cfg = _APP_CFG.get("agnes") or {}
    RateLimiter.get(_agnes_cfg.get("rpm"))
    QuotaLedger.get_default(BASE_DIR / "cache" / "quota.json", limits=_APP_CFG.get("quota"))
    import routers.shots  # noqa: F401
    from src.pipeline.controller import PipelineController
    deps.pipeline_controller = PipelineController(str(BASE_DIR / "config" / "config.yaml"))
    deps.agnes_client = deps.pipeline_controller.agnes_client
    deps.project_manager = deps.pipeline_controller.project_manager
    from src.core.job_queue import JobQueue
    q = JobQueue(BASE_DIR / "cache" / "_44a_jobs.json", deps.JOB_HANDLERS)
    deps.job_queue = q
    return deps, q


PROJECT_DIR = BASE_DIR / "projects" / PROJECT


async def run(session_id: str | None = None) -> int:
    import deps
    deps, q = _bootstrap_deps()
    await q.start()
    from services.lifecycle import recover_pending_videos
    await recover_pending_videos(queue=q)
    try:
        from src.agents.runtime import get_agent_runtime
        from src.agents.director import director_turn

        rt = get_agent_runtime()
        store = rt._session_store(PROJECT)
        if session_id:
            s = store.load(session_id)
            assert s is not None, f"会话不存在: {session_id}"
            assert s.agent_id == "director", s.agent_id
            print(f"continue session: {s.session_id}（messages={len(s.messages)}）")
        else:
            s = store.get_or_create("director", "scene",
                                    f"p44a_c03_{int(time.time())}")
            print(f"fresh session: {s.session_id}")
        sid = s.session_id
        EVROOT.mkdir(parents=True, exist_ok=True)
        n = len(list(EVROOT.glob("events_c03_*.jsonl"))) + 1
        ev_path = EVROOT / f"events_c03_{n}.jsonl"
        t0 = time.time()

        def cb(ev):
            try:
                with ev_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
                t = str(ev.get("type") or "")
                if t in ("tool.started", "tool.completed", "tool.failed"):
                    name = ev.get("name") or ev.get("tool") or ""
                    print(f"  [{int(time.time()-t0):>5}s] {t} {name} ok={ev.get('ok')} "
                          f"{str(ev.get('summary') or ev.get('error') or '')[:90]}",
                          flush=True)
            except Exception:
                pass

        task = TASK_FILE.read_text(encoding="utf-8").strip()
        TRANSIENT_SIGNS = ("500", "502", "503", "504", "do_request_failed",
                           "timeout", "connection", "stream 错误")
        result = None
        for attempt in range(1, 6):
            failure = None
            try:
                result = await director_turn(rt, PROJECT, sid, task, event_cb=cb)
                raw = str(result.get("assistant_message") or "")
                if raw.startswith("[执行失败"):
                    failure = raw
            except Exception as e:  # noqa: BLE001
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
            print(f"\n[retry {attempt}/5] turn failed（{failure[:160]}）→ {wait_s}s 后重发",
                  flush=True)
            await asyncio.sleep(wait_s)
            result = None
        assert result is not None
        dur = time.time() - t0
        (EVROOT / f"result_c03_{n}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8")
        shutil.copy2(store._path(sid), EVROOT / "session_after_c03.json")
        for name in ("project.json", "shot_production_bindings.json"):
            src = PROJECT_DIR / "output" / name
            if src.exists():
                shutil.copy2(src, EVROOT / f"after_c03_{name}")
        tools = [(t.get("tool"), t.get("ok")) for t in (result.get("trace") or [])]
        print(f"\n=== c03 turn completed in {int(dur)}s ===")
        print(f"tools ({len(tools)}):")
        for name, ok in tools:
            print(f"  - {name} ok={ok}")
        print(f"\nreply head:\n{(result.get('assistant_message') or '')[:1500]}")
        return 0
    finally:
        stop = getattr(q, "stop", None)
        if stop:
            await stop()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--continue", dest="session", default=None)
    ap.add_argument("--task", dest="task", default=None,
                    help="任务文件路径（默认 _44a/task_c03_iter.txt）")
    a = ap.parse_args()
    if a.task:
        _set_task(Path(a.task) if Path(a.task).is_absolute() else BASE_DIR / a.task)
    _load_env()
    rc = asyncio.run(run(a.session))
    sys.exit(rc)


if __name__ == "__main__":
    main()
