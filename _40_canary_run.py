# -*- coding: utf-8 -*-
"""Phase 40 — System Agent Canary 运行器（进程内 director_turn 正式入口，真实 API）。

与后端相同的生产装配（deps 单例 + 真实 AgnesClient + JobQueue），但：
- 独立队列状态文件 cache/canary_jobs.json（与后端 cache/jobs.json 隔离；
  Canary 运行期间后端保持空闲，不发起任何生成）；
- 全新 director 会话：AgentSessionStore.get_or_create("director","scene",
  canary_p40_<tag>_<ts>)——0 条历史消息，纯干净对话（40.md §1）；
- 任务文本只用 40.md §1 授权的高层生产目标；不注入 39F 结论。

证据（40.md §13）：event_cb 全事件增量落盘 events.jsonl；turn 结果
（assistant_message + receipts + trace）落 result.json；会话副本 + canonical 副本。

用法：
  python _40_canary_run.py --tag abc --task-file _40_canary/tasks/canary_abc.txt
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

PROJECT = "Crane_Lake_P40A"
EVROOT = BASE_DIR / "_40_canary"


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
    q = JobQueue(BASE_DIR / "cache" / "canary_jobs.json", deps.JOB_HANDLERS)
    deps.job_queue = q
    return deps, q


async def run(tag: str, task: str, out: Path, session_id: str | None = None,
              inject: str | None = None) -> int:
    import deps
    deps, q = _bootstrap_deps()
    if inject == "pregen":
        # Canary 8：安全注入预检异常——shot_input_semantics 必然抛错。
        # 期望：fail-open（生成不阻断）+ 回执带 pregen_check_unavailable +
        # Agent 不得解释成「已证明无冲突」也不得硬阻断。
        import src.agents.pregen_checks as _pc
        def _boom(*a, **k):
            raise RuntimeError("canary injected pregen failure")
        _pc.shot_input_semantics = _boom
        print("[inject] pregen_checks.shot_input_semantics -> raise")
    await q.start()
    # 42.md §二缺陷②：驱动器恢复钩子（与 _41_redo_run 同理由）。
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
            # 全新干净会话（非 project scope 走 get_or_create 新建路径；scope 仅隔离元数据）
            s = store.get_or_create("director", "scene", f"canary_p40_{tag}_{int(time.time())}")
            assert not s.messages, f"新会话非空: {len(s.messages)} 条消息"
            print(f"fresh session: {s.session_id}（messages={len(s.messages)}）")
        sid = s.session_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "session_created.json").write_text(
            json.dumps(s.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")

        n = len(list(out.glob("result_*.json"))) + 1
        ev_path = out / f"events_{n}.jsonl"
        t0 = time.time()

        def cb(ev):   # director.py 同步调用 event_cb——必须是非 async 函数
            try:
                with ev_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
                t = str(ev.get("type") or "")
                if t in ("tool.started", "tool.completed", "tool.failed"):
                    name = ev.get("name") or ev.get("tool") or ""
                    print(f"  [{int(time.time()-t0):>5}s] {t} {name} ok={ev.get('ok')} {str(ev.get('summary') or ev.get('error') or '')[:80]}", flush=True)
            except Exception:
                pass

        result = await director_turn(rt, PROJECT, sid, task, event_cb=cb)
        dur = time.time() - t0
        (out / f"result_{n}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        # 会话副本 + canonical 副本（turn 后事实）
        shutil.copy2(store._path(sid), out / "session_after.json")
        for name in ("project.json", "scene_system.json", "shot_production_bindings.json"):
            src = PROJECT_DIR / "output" / name
            if src.exists():
                shutil.copy2(src, out / f"after_{name}")
        tools = [(t.get("tool"), t.get("ok")) for t in (result.get("trace") or [])]
        print(f"\n=== turn completed in {int(dur)}s ===")
        print(f"tools ({len(tools)}):")
        for name, ok in tools:
            print(f"  - {name} ok={ok}")
        print(f"\nreply head:\n{(result.get('assistant_message') or '')[:800]}")
        return 0
    finally:
        await q.stop() if hasattr(q, "stop") else None


PROJECT_DIR = BASE_DIR / "projects" / PROJECT


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--task-file", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--session", default=None, help="继续既有会话（缺省=新建干净会话）")
    ap.add_argument("--inject", default=None, choices=["pregen"])
    a = ap.parse_args()
    _load_env()
    task = Path(a.task_file).read_text(encoding="utf-8").strip()
    out = Path(a.out) if a.out else EVROOT / f"canary_{a.tag}"
    rc = asyncio.run(run(a.tag, task, out, a.session, a.inject))
    sys.exit(rc)


if __name__ == "__main__":
    main()
