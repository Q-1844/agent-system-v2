# -*- coding: utf-8 -*-
"""Phase 45.1 补充轮——同一 assembly 会话的 T3：补写提案（真实 LLM）。"""
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

T3 = """上一轮你的提案（assembly.proposal_write）连续 4 次失败：参数键名不对。请现在用正确参数补写提案：{"title": "...", "content_md": "markdown 全文"}。内容要求：时间线摘要、逐句旁白文案与落点（narr_01~narr_13）、三段音乐选材依据与 test_asset 身份声明、deep audit 量化结果、与旧推荐版 srender_fa9a2b7a 的 A/B 说明、已知瑕疵、试听方式、"select final_render 由用户执行"提示。只写提案这一个写工具。"""


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


async def run() -> int:
    from src.agents.assembly import assembly_turn
    deps, q = _bootstrap_deps()
    await q.start()
    try:
        from src.agents.runtime import get_agent_runtime
        rt = get_agent_runtime()
        sid = json.loads((EV / "session_after_45a.json").read_text(
            encoding="utf-8"))["session_id"]
        t0 = time.time()
        result = await assembly_turn(rt, PROJECT, sid, T3)
        (EV / "result_45a_3.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8")
        shutil.copy2(rt._session_store(PROJECT)._path(sid),
                     EV / "session_after_45a.json")
        print(f"=== T3 done in {int(time.time()-t0)}s ===")
        for x in result.get("receipts") or []:
            print("-", x.get("operation"), "ok=", x.get("ok"),
                  str(x.get("op_summary") or x.get("error") or "")[:120])
        print("\n" + (result.get("assistant_message") or "")[:800])
        return 0
    finally:
        stop = getattr(q, "stop", None)
        if stop:
            await stop()


if __name__ == "__main__":
    _load_env()
    sys.exit(asyncio.run(run()))
