# -*- coding: utf-8 -*-
"""Phase 45.1 — Assembly Agent 自主全链会话运行器（真实 Agnes LLM + 真实 MiniMax TTS）。

与 44C 终版脚本轨迹（_44c8~_44c10）的区别：本运行器只给【目标 + 用户已定创作约束】，
不给逐句旧文案、不给音乐文件名、不给任何脚本参数——Sequence、旁白文案、镜头内落点、
三段配乐选材全部由 Agent 经正式工具自主决策。证据：_45/events_45a_{1,2}.jsonl、
result_45a_{1,2}.json、session_after_45a.json、45a_run_summary.json。
用法：python _4510_agent_run.py
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

BRIEF_T1 = """【来自用户的成片任务】
目标：把《雨灯归航》当前批准的全部镜头（13 镜，约 90 秒）装配成一条【完整声音版成片候选】——视频装配、全片旁白、分场景配乐、混音、渲染、审计，全部由你经正式工具完成。

【本轮交付物要求（硬性）】项目里已有旧的 sequence/渲染/混音版本——那是此前执行侧验证的样本，只可作对照（A/B），【不得】当作本轮交付物。本轮你必须：从冻结快照出发创建【你自己的新 sequence】、创作【你自己的旁白文案】并逐句 TTS 合成、编排【你自己的 AudioPlan】、渲染出【你的新 render version】。旧版本是否更好留给用户 A/B 裁定，不能替代你的生产链。

用户已定的创作约束（必须遵守）：
1. 原始音轨静默：混音时把原生环境声压到听不见（base_gain_db=-60），不新增任何脚本生成音效。
2. 全片旁白：旁白覆盖全片约 90 秒（参考节奏：每个镜头内 1 句，句间留呼吸空隙）。旁白文案由你根据剧本/场景自主创作——风格温润克制，贴合画面情绪；每句完整落在所属镜头窗内（留出起止余量，避开镜头切点与场景转场）。声线=温润男声 Chinese (Mandarin)_Gentleman，speed=0.9。
3. 分场景配乐：每个场景使用不同的背景音乐，从项目音频资产库选择（assembly.audio_assets_read 枚举后按场景情绪挑选，场景切点处自然衔接）。资产库里的本地合成音乐是测试垫乐——你在提案里要如实标注 test_asset。
4. 事实来源：冻结快照（assembly.briefing / assembly.snapshot_read）。不得偷换未批准素材。
5. 完成判据：你的新渲染 done 且 assembly.render_status（deep_audit=true）的 audio 段【逐句旁白、逐段音乐】全部通过（不允许只看第一句）；随后导出分轨（narration/music）、发布 manifest（narration_cues/music_bed 会自动从 plan 投影）、写提案提交用户评审。最终 select final_render 由用户显式执行，你不能代替。

背景：《雨灯归航》是一个 3 幕小故事（河岸拾灯 → 修理间修灯 → 登塔归航）。用户此前已确认"温润男声 + 全片旁白 + 每场景不同音乐 + 原音静默"的声音方案。
"""

BRIEF_T2 = """【来自 Producer 的核验请求】
渲染应已完成。请：assembly.render_status（deep_audit=true）逐层核对 audio 段（13 句旁白 + 3 段音乐 + 原生泄漏 + LUFS）；若全部通过 → assembly.stem_render 导出 narration/music 分轨 → assembly.manifest_publish → assembly.proposal_write（提案含：时间线摘要、逐句旁白文案与落点、三段音乐选材依据与 test_asset 身份声明、deep audit 量化结果、已知瑕疵、试听方式、以及"select final_render 由用户执行"提示）。若有层未通过 → 修 plan 或重渲，直到达标或如实上报阻塞。
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


async def _run_turn(rt, sid, task_text, n, label):
    from src.agents.assembly import assembly_turn
    ev_path = EV / f"events_45a_{n}.jsonl"
    if ev_path.exists():
        ev_path.unlink()
    t0 = time.time()
    TRANSIENT = ("500", "502", "503", "504", "do_request_failed",
                 "timeout", "connection", "stream 错误")
    result = None
    for attempt in range(1, 6):
        failure = None
        try:
            result = await assembly_turn(rt, PROJECT, sid, task_text,
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
    (EV / f"result_45a_{n}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    print(f"\n=== agent turn {n} ({label}) done in {int(time.time()-t0)}s ===")
    for tl in result.get("trace") or []:
        print(f"  - {tl.get('tool')} {tl.get('result')}")
    print("\nreply head:\n" + (result.get("assistant_message") or "")[:1500])
    return result, time.time() - t0


async def run() -> int:
    EV.mkdir(parents=True, exist_ok=True)
    deps, q = _bootstrap_deps()
    await q.start()
    from services.lifecycle import recover_pending_videos
    await recover_pending_videos(queue=q)
    try:
        from src.agents.runtime import get_agent_runtime
        rt = get_agent_runtime()
        store = rt._session_store(PROJECT)
        s = store.get_or_create("assembly", "project",
                                f"p45_agent_{int(time.time())}")
        sid = s.session_id
        print(f"assembly session: {sid}")

        r1, d1 = await _run_turn(rt, sid, BRIEF_T1, 1, "生产")

        # 驱动侧等渲染（不改 Agent 决策——只等 JobQueue 排队完成）
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
        else:
            print("!!! T1 无 render_submit 成功回执（如实记录）")
        (EV / "45a_wait_state.json").write_text(
            json.dumps(wait_state, ensure_ascii=False, indent=1), encoding="utf-8")

        r2, d2 = await _run_turn(rt, sid, BRIEF_T2, 2, "核验交付")
        shutil.copy2(rt._session_store(PROJECT)._path(sid),
                     EV / "session_after_45a.json")
        summary = {"session_id": sid, "turn1_seconds": round(d1, 1),
                   "turn2_seconds": round(d2, 1), "render": wait_state,
                   "turn1_receipts": r1.get("receipts"),
                   "turn2_receipts": r2.get("receipts")}
        (EV / "45a_run_summary.json").write_text(
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
    _load_env()
    sys.exit(asyncio.run(run()))
