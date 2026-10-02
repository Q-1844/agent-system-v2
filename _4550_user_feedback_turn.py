# -*- coding: utf-8 -*-
"""Phase 45.5 — 用户审片反馈轮（真实 Assembly Agent 会话）。

用户反馈（2026-10-02，看 REVIEW_45 后）：
1) Agent 版声音稍弱 → 响度对齐 44C 版口径（loudnorm 目标 I=-16/TP=-2/LRA=11）；
2) 重新生成第一句旁白（重写文案+重合成），其它句子与三段音乐不动。
全部经 Agent 正式工具执行：TTS 新版本 → AudioPlan OCC 更新 → 重渲染 → deep audit
（逐句+响度）→ manifest → 提案。证据：_45/events_45d_*.jsonl、result_45d_*.json、
45d_run_summary.json。
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

BRIEF = """【来自用户的审片反馈（两条修改指令）】
你在 45.1 生产的成片（seq_b746d7f3，最新渲染 srender_5198665b，plan rev 918ad08e）用户已审看，反馈如下，请经正式工具执行修改：

1. 【响度对齐】用户觉得成片声音整体偏弱。给 AudioPlan 补上响度目标 loudness={i:-16, tp:-2, lra:11}（与项目 44C 版同口径）。🔴 注意 plan_config 是整体替换语义：必须保留现有 base_gain_db=-60（先 audio_plan_read 读当前完整配置再写回），否则原生音会回来。
2. 【重做第一句旁白】第一句（narr_01）重新生成：你重写一句新文案（风格温润克制、贴合首镜"雨后河面/拾灯"的情绪，可与旧句意思相近但表达要新），tts_synthesize 合成新版本（cue_id 仍用 narr_01，声线/speed 不变），然后把 plan 里 narr_01 那一层的 source.file 换成新版本文件。**其它 12 句旁白与三段音乐层一律不动。**

完成后：render_submit 重渲染 → render_status（deep_audit=true）核对 audio 段（13 句逐句在场 + 响度 I 应接近 -16 LUFS）→ manifest_publish → proposal_write（简短记录本轮两项变更与新渲染 ID）。最终 select 仍由用户执行。
"""


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
            if t in ("tool.started", "tool.completed", "tool.failed"):
                print(f"  [{int(time.time()-t0):>5}s] {t} {ev.get('name') or ''} "
                      f"ok={ev.get('ok')} "
                      f"{str(ev.get('summary') or ev.get('error') or '')[:88]}",
                      flush=True)
        except Exception:
            pass
    return cb


async def _run_turn(rt, sid, text, n):
    from src.agents.assembly import assembly_turn
    ev_path = EV / f"events_45d_{n}.jsonl"
    if ev_path.exists():
        ev_path.unlink()
    t0 = time.time()
    result = await assembly_turn(rt, PROJECT, sid, text, event_cb=_mk_cb(ev_path, t0))
    (EV / f"result_45d_{n}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    print(f"\n=== agent turn {n} done in {int(time.time()-t0)}s ===")
    for x in result.get("receipts") or []:
        print("-", x.get("operation"), "ok=", x.get("ok"), "verified=", x.get("verified"),
              str(x.get("op_summary") or x.get("error") or "")[:110])
    return result, time.time() - t0


async def run() -> int:
    deps, q = _bootstrap_deps()
    await q.start()
    from services.lifecycle import recover_pending_videos
    await recover_pending_videos(queue=q)
    try:
        from src.agents.runtime import get_agent_runtime
        rt = get_agent_runtime()
        store = rt._session_store(PROJECT)
        s = store.get_or_create("assembly", "project",
                                f"p45_feedback_{int(time.time())}")
        sid = s.session_id
        print(f"assembly session: {sid}")

        r1, d1 = await _run_turn(rt, sid, BRIEF, 1)

        # 驱动侧等渲染（若 T1 已确认 done 则跳过）
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
            while time.time() - t0 < 1800:
                row = svc.get_entry(seq_id).get_render(rvid)
                if row and row.status in ("done", "failed", "raced_superseded"):
                    break
                await asyncio.sleep(3)
            wait_state["final_status"] = row.status if row else "timeout"
            wait_state["wait_s"] = round(time.time() - t0, 1)
            print(f"=== render {rvid} → {wait_state['final_status']} ===")
        need_t2 = wait_state.get("final_status") == "done" and not any(
            r.get("operation") == "assembly.manifest_publish" and r.get("ok")
            for r in (r1.get("receipts") or []))
        d2 = None
        if need_t2:
            r2, d2 = await _run_turn(
                rt, sid,
                "渲染已完成。请收尾：render_status（deep_audit=true）核对 audio 段"
                "（13 句在场+响度 I≈-16）→ manifest_publish → proposal_write（记录本轮"
                "两项变更与新渲染 ID）。", 2)
        shutil.copy2(rt._session_store(PROJECT)._path(sid),
                     EV / "session_after_45d.json")
        summary = {"session_id": sid, "turn1_seconds": round(d1, 1),
                   "turn2_seconds": round(d2, 1) if d2 else None,
                   "render": wait_state}
        (EV / "45d_run_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n=== summary ===")
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0
    finally:
        stop = getattr(q, "stop", None)
        if stop:
            await stop()


if __name__ == "__main__":
    _load_env()
    sys.exit(asyncio.run(run()))
