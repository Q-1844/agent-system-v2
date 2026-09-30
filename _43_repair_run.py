# -*- coding: utf-8 -*-
"""Phase 43 — 《雨灯归航》正式修复轮运行器（进程内正式入口，真实 API）。

复用 Phase 41 驱动方式（_41_redo_run.py，行为验证过的生产装配）：
- 与后端相同的生产装配（deps 单例 + 真实 AgnesClient + JobQueue + recover 钩子）；
- 独立队列状态文件 cache/_43_repair_jobs.json（与后端隔离）；
- 全新会话（干净对话）；任务文本 _43_repair/task_*.txt；
- 证据：events_*.jsonl（全事件）/ result_*.json / session_after.json /
  after_*.json（canonical 快照）。

子命令：
  python _43_repair_run.py --mode scene    # Scene Agent 会话：灯室内部 SIV（判据 9）
  python _43_repair_run.py --mode repair   # Director 全新会话：修复主链（Skill v1.2 判据）
  python _43_repair_run.py --mode repair --continue <sid>
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
EVROOT = BASE_DIR / "_43_repair"


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
    import routers.shots  # noqa: F401  生成 handlers 注册
    from src.pipeline.controller import PipelineController
    deps.pipeline_controller = PipelineController(str(BASE_DIR / "config" / "config.yaml"))
    deps.agnes_client = deps.pipeline_controller.agnes_client
    deps.project_manager = deps.pipeline_controller.project_manager
    from src.core.job_queue import JobQueue
    q = JobQueue(BASE_DIR / "cache" / "_43_repair_jobs.json", deps.JOB_HANDLERS)
    deps.job_queue = q
    return deps, q


PROJECT_DIR = BASE_DIR / "projects" / PROJECT


async def run(mode: str, session_id: str | None = None) -> int:
    import deps
    deps, q = _bootstrap_deps()
    await q.start()
    from services.lifecycle import recover_pending_videos
    await recover_pending_videos(queue=q)
    try:
        from src.agents.runtime import get_agent_runtime

        if mode == "scene":
            from src.agents.scene_designer import scene_designer_turn
            agent_id, turn_fn, task_file = "scene_designer", scene_designer_turn, "task_scene.txt"
        elif mode == "repair":
            from src.agents.director import director_turn
            agent_id, turn_fn, task_file = "director", director_turn, "task_repair.txt"
        else:
            raise ValueError(f"未知 mode: {mode}")

        rt = get_agent_runtime()
        store = rt._session_store(PROJECT)
        if session_id:
            s = store.load(session_id)
            assert s is not None, f"会话不存在: {session_id}"
            assert s.agent_id == agent_id, s.agent_id
            print(f"continue session: {s.session_id}（messages={len(s.messages)}）")
        else:
            s = store.get_or_create(agent_id, "scene",
                                    f"p43_{mode}_{int(time.time())}")
            assert not s.messages, f"新会话非空: {len(s.messages)} 条消息"
            print(f"fresh session: {s.session_id}（agent={agent_id}）")
        sid = s.session_id
        EVROOT.mkdir(parents=True, exist_ok=True)
        (EVROOT / f"session_created_{mode}.json").write_text(
            json.dumps(s.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")

        n = len(list(EVROOT.glob(f"result_{mode}_*.json"))) + 1
        ev_path = EVROOT / f"events_{mode}_{n}.jsonl"
        t0 = time.time()

        def cb(ev):   # 同步回调
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

        task = (EVROOT / task_file).read_text(encoding="utf-8").strip()

        # 上游瞬时失败重试（同 41：同 session 重发同任务，resume 语义幂等）
        TRANSIENT_SIGNS = ("500", "502", "503", "504", "do_request_failed",
                           "timeout", "connection", "stream 错误")
        result = None
        for attempt in range(1, 6):
            failure = None
            try:
                result = await turn_fn(rt, PROJECT, sid, task, event_cb=cb)
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
        (EVROOT / f"result_{mode}_{n}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8")
        shutil.copy2(store._path(sid), EVROOT / f"session_after_{mode}.json")
        for name in ("project.json", "scene_system.json",
                     "shot_production_bindings.json", "resources.json"):
            src = PROJECT_DIR / "output" / name
            if src.exists():
                shutil.copy2(src, EVROOT / f"after_{mode}_{name}")
        tools = [(t.get("tool"), t.get("ok")) for t in (result.get("trace") or [])]
        print(f"\n=== {mode} turn completed in {int(dur)}s ===")
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
    ap.add_argument("--mode", choices=["scene", "repair"], required=True)
    ap.add_argument("--continue", dest="session", default=None)
    a = ap.parse_args()
    _load_env()
    rc = asyncio.run(run(a.mode, a.session))
    sys.exit(rc)


if __name__ == "__main__":
    main()
