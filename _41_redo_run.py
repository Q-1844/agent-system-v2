# -*- coding: utf-8 -*-
"""Phase 41 — 雨灯归航全量重测运行器（进程内 director_turn 正式入口，真实 API）。

复用 Phase 40 Canary 驱动方式（_40_canary_run.py，行为验证过的正式装配）：
- 与后端相同的生产装配（deps 单例 + 真实 AgnesClient + JobQueue）；
- 独立队列状态文件 cache/_41_redo_jobs.json（与后端 cache/jobs.json 隔离）；
- 全新 director 会话（0 条历史消息，干净对话）；
- 任务文本：_41_redo/task_full_redo.txt（用户指令 2026-09-28）。

证据（同 40 纪律）：event_cb 全事件增量落盘 events.jsonl；turn 结果落
result.json；会话副本 + canonical 副本落 _41_redo/。

用法：
  python _41_redo_run.py                 # 全量重测（fresh 会话）
  python _41_redo_run.py --continue <session_id>   # 续既有会话
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
EVROOT = BASE_DIR / "_41_redo"
TASK_FILE = EVROOT / "task_full_redo.txt"


def _load_env():
    env_file = BASE_DIR / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _bootstrap_deps():
    """与 api_server.startup 相同的生产装配（队列状态文件隔离）。"""
    import deps
    from src.core.rate_limiter import RateLimiter
    from src.core.quota import QuotaLedger
    import yaml
    _APP_CFG = yaml.safe_load((BASE_DIR / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    _agnes_cfg = _APP_CFG.get("agnes") or {}
    RateLimiter.get(_agnes_cfg.get("rpm"))
    QuotaLedger.get_default(BASE_DIR / "cache" / "quota.json", limits=_APP_CFG.get("quota"))
    # 注册生成 handlers（routers.shots import 时向 deps.JOB_HANDLERS 注册 first_frame/last_frame/video）
    import routers.shots  # noqa: F401
    from src.pipeline.controller import PipelineController
    deps.pipeline_controller = PipelineController(str(BASE_DIR / "config" / "config.yaml"))
    deps.agnes_client = deps.pipeline_controller.agnes_client
    deps.project_manager = deps.pipeline_controller.project_manager
    from src.core.job_queue import JobQueue
    q = JobQueue(BASE_DIR / "cache" / "_41_redo_jobs.json", deps.JOB_HANDLERS)
    deps.job_queue = q
    return deps, q


PROJECT_DIR = BASE_DIR / "projects" / PROJECT


async def run(task: str, session_id: str | None = None) -> int:
    import deps
    deps, q = _bootstrap_deps()
    await q.start()
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
                                    f"p41_redo_{int(time.time())}")
            assert not s.messages, f"新会话非空: {len(s.messages)} 条消息"
            print(f"fresh session: {s.session_id}（messages={len(s.messages)}）")
        sid = s.session_id
        EVROOT.mkdir(parents=True, exist_ok=True)
        (EVROOT / "session_created.json").write_text(
            json.dumps(s.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")

        n = len(list(EVROOT.glob("result_*.json"))) + 1
        ev_path = EVROOT / f"events_{n}.jsonl"
        t0 = time.time()

        def cb(ev):   # director.py 同步调用 event_cb——必须是非 async 函数
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

        result = await director_turn(rt, PROJECT, sid, task, event_cb=cb)
        dur = time.time() - t0
        (EVROOT / f"result_{n}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8")
        shutil.copy2(store._path(sid), EVROOT / "session_after.json")
        for name in ("project.json", "scene_system.json",
                     "shot_production_bindings.json"):
            src = PROJECT_DIR / "output" / name
            if src.exists():
                shutil.copy2(src, EVROOT / f"after_{name}")
        tools = [(t.get("tool"), t.get("ok")) for t in (result.get("trace") or [])]
        print(f"\n=== turn completed in {int(dur)}s ===")
        print(f"tools ({len(tools)}):")
        for name, ok in tools:
            print(f"  - {name} ok={ok}")
        print(f"\nreply head:\n{(result.get('assistant_message') or '')[:1200]}")
        return 0
    finally:
        stop = getattr(q, "stop", None)
        if stop:
            await stop()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--continue", dest="session", default=None,
                    help="继续既有会话（缺省=新建干净会话）")
    a = ap.parse_args()
    _load_env()
    task = TASK_FILE.read_text(encoding="utf-8").strip()
    rc = asyncio.run(run(task, a.session))
    sys.exit(rc)


if __name__ == "__main__":
    main()
