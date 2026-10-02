# -*- coding: utf-8 -*-
"""Phase 45.4 — 独立集成运行：全新 Producer 会话从用户入口发起成片目标。

Producer 只拿到【目标 + 用户已定创作约束】（不给工具顺序、不给旧文案、不给脚本参数）；
由 Producer 自行决定委派 Assembly 并组装 envelope。委派经正式 `assembly.delegate`
通道（AgentTask 父子链 + bridge + assembly_evidence 回执）。
证据：_45/events_45b_1.jsonl、result_45b_1.json、producer_session_after_45b.json、
assembly_session_after_45b.json、45b_run_summary.json。
用法：python _4520_producer_run.py
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
EV = BASE / "_45"

GOAL = """把《雨灯归航》做成一条可以审看的完整成片（含声音），推进到"候选待审"状态给我。

我对声音的要求（之前定过的）：原始音轨要静默（生成视频自带的背景音是缺陷，不要进成片）；旁白要覆盖全片，风格温润克制，按剧情节奏安排，不要只配几句；每个场景用不同的背景音乐，从项目现有的音频资产里选，不要脚本生成音效；旁白声线用温润男声（Chinese (Mandarin)_Gentleman，speed 0.9）。素材是什么身份就如实标注（本地测试垫乐就写测试垫乐）。做完告诉我看片/试听入口和还差什么。注意：最终定版（select final_render）由我自己来做。"""


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
    q = JobQueue(EV / "jobs_45.json", deps.JOB_HANDLERS)
    deps.job_queue = q
    return deps, q


def _mk_cb(path, t0):
    def cb(ev):
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
            t = str(ev.get("type") or "")
            if t in ("tool.started", "tool.completed", "tool.failed",
                     "tool.progress"):
                print(f"  [{int(time.time()-t0):>5}s] {t} "
                      f"{ev.get('agent') or ''}{ev.get('name') or ''} "
                      f"ok={ev.get('ok')} "
                      f"{str(ev.get('summary') or ev.get('phase') or ev.get('error') or '')[:80]}",
                      flush=True)
        except Exception:
            pass
    return cb


async def run() -> int:
    EV.mkdir(parents=True, exist_ok=True)
    deps, q = _bootstrap_deps()
    await q.start()
    from services.lifecycle import recover_pending_videos
    await recover_pending_videos(queue=q)
    try:
        from src.agents.runtime import get_agent_runtime
        from src.agents.producer import producer_turn
        rt = get_agent_runtime()
        store = rt._session_store(PROJECT)
        s = store.get_or_create("producer", "project",
                                f"p45_prod_{int(time.time())}")
        sid = s.session_id
        print(f"producer session: {sid}")

        ev_path = EV / "events_45b_1.jsonl"
        if ev_path.exists():
            ev_path.unlink()
        t0 = time.time()
        TRANSIENT = ("500", "502", "503", "504", "do_request_failed",
                     "timeout", "connection", "stream 错误")
        result = None
        for attempt in range(1, 4):
            failure = None
            try:
                result = await producer_turn(rt, PROJECT, sid, GOAL,
                                             event_cb=_mk_cb(ev_path, t0))
                if str(result.get("assistant_message") or "").startswith("[执行失败"):
                    failure = result["assistant_message"]
            except Exception as e:   # noqa: BLE001
                result = None
                failure = f"{type(e).__name__}: {e}"
            if failure is None:
                break
            if attempt >= 4 or not any(x in failure for x in TRANSIENT):
                if result is None:
                    raise
                break
            wait_s = min(30 * attempt, 120)
            print(f"[retry {attempt}/4] {failure[:150]} → {wait_s}s", flush=True)
            await asyncio.sleep(wait_s)
            result = None
        assert result is not None
        (EV / "result_45b_1.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8")

        # 双方会话快照 + 任务链快照
        shutil.copy2(rt._session_store(PROJECT)._path(sid),
                     EV / "producer_session_after_45b.json")
        asm_sessions = sorted((BASE / "projects" / PROJECT / "output" /
                               "agents" / "sessions").glob("sess_*.json"),
                              key=lambda p: p.stat().st_mtime)
        if asm_sessions:
            latest = [p for p in asm_sessions
                      if json.loads(p.read_text(encoding="utf-8"))
                      .get("agent_id") == "assembly"][-1]
            shutil.copy2(latest, EV / "assembly_session_after_45b.json")
        tasks_dir = BASE / "projects" / PROJECT / "output" / "agents" / "tasks"
        task_dump = {}
        if tasks_dir.is_dir():
            for p in sorted(tasks_dir.glob("task_*.json"),
                            key=lambda p: p.stat().st_mtime)[-8:]:
                task_dump[p.stem] = json.loads(
                    p.read_text(encoding="utf-8"))
        (EV / "45b_task_chain.json").write_text(
            json.dumps(task_dump, ensure_ascii=False, indent=1),
            encoding="utf-8")

        print(f"\n=== producer turn done in {int(time.time()-t0)}s ===")
        print("linked_task_ids:", result.get("linked_task_ids"))
        for tl in result.get("trace") or []:
            print(f"  - {tl.get('tool')} {tl.get('result')}")
        print("\nreply:\n" + (result.get("assistant_message") or "")[:2000])
        return 0
    finally:
        stop = getattr(q, "stop", None)
        if stop:
            await stop()


if __name__ == "__main__":
    _load_env()
    sys.exit(asyncio.run(run()))
