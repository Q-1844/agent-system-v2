# -*- coding: utf-8 -*-
# _37_fable/_39_shadow_replay.py
# Phase 39 Phase B：《狐狸进村》Shadow Replay 驱动（39.md §2.3-4 / 用户指令 §24-§26）。
#
# 定位（39.md §22/§24 纪律）：
#   - 本脚本只是【证据投喂 + 输出读取】的 harness：把 cal_20260925_004933_6b30 的
#     历史 evidence（expected_tail / Vision 结构化评估 / 接缝数据 / 下一镜预编剧开场）
#     逐决策点喂给正式项目 Agent runtime（REST 流式 turn，thinking=true），
#     Agent 的裁定由它自己经 calibration.trace_upsert 写进【独立的 replay run】。
#   - 🔴 绝不模拟决策：脚本里没有任何 "if M3B: bridge" 逻辑；compare 只做
#     replay run vs gold run 的逐键比对与失败分类计数。
#   - 证据剥离：gold link 的 human_deviation_level / human_action / human_reason /
#     retry_or_adapt / choice_reason / _repair_plan / result_after_correction 全部
#     不进证据（否则泄漏答案）。
#   - 复盘模式约束（写进每个 turn 的指令 + 事后 receipts 扫描双保险）：
#     不提交生成、不修改镜头、不插桥——违规计入 ownership_violation。
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = "http://127.0.0.1:8000"
# gold 与 replay 分离：gold=38 真项目（只读，绝不触碰）；replay=沙盒项目副本
# （Agent 会话/work_memory/canonical 全部隔离——replay 期间的任何 Agent 副作用
#  只落在沙盒，不污染 38 既有数据）。
GOLD_PROJ = "Fable_Pilot_Fox_and_Grapes_V1"
PROJ = "Fable_Pilot_Fox_and_Grapes_V1_P39B"
GOLD_RUN = "cal_20260925_004933_6b30"
RUNS_DIR = ROOT / "projects" / PROJ / "output" / "experiments" / "calibration_runs"
GOLD_RUNS_DIR = ROOT / "projects" / GOLD_PROJ / "output" / "experiments" / "calibration_runs"
OUT_DIR = ROOT / "_37_fable" / "_39_replay_results"
REPLAY_TITLE = "Phase39B Shadow Replay v2"

# M-key → 当前项目镜头 id（38 终态：M3B=s11_c04 插于 M3 后；M5B=s12_c07 插于 M5/M6 间）
M2SHOT = {"M1": "s11_c01", "M2": "s11_c02", "M3": "s11_c03", "M3B": "s11_c04",
          "M4": "s12_c01", "M5": "s12_c02", "M5B": "s12_c07",
          "M6": "s12_c03", "M7": "s12_c04", "M8": "s12_c05", "M9": "s12_c06"}
# 决策点遍历顺序（先终态裁定、后 attempt 重试点，最后两个桥识别场景）
POINT_ORDER = ["M1", "M1_a1", "M2", "M3", "M3_a1", "M3B", "M4", "M4_a1", "M4_a2",
               "M4_a3", "M5", "M5_a1", "M5_a2", "M5_a3", "M5_a4", "M5B", "M5B_a1",
               "M6", "M7", "M8", "M9",
               "SCENARIO_M3_M4_HARDCUT", "SCENARIO_M5_M6_SEAM_BREAK"]
# 证据剥离键（gold 裁定不进证据）
STRIP_KEYS = {"human_deviation_level", "human_action", "human_reason",
              "retry_or_adapt", "choice_reason", "_repair_plan", "shot_patch",
              "result_after_correction", "run_id", "story_id", "updated_at"}
# 复盘模式允许的操作（其余 = ownership 违规）
ALLOWED_OPS_PREFIX = ("calibration.", "shot.list", "shot.read", "story.list",
                      "story.read", "script.read", "character.list",
                      "character.read", "project.visual_profile.read", "web.search")

MAE_BASELINE_NOTE = ("接缝三档基线（Phase38 实测）：同场景正常接力≈2（近零带 1.7-2.4）；"
                     "跨场景/构图切换 26-60；剧变构图 90+。注意 mae_prev 是端到端构图漂移，"
                     "tail_to_first 才是接续性接缝。")


def gold_links() -> dict:
    out = {}
    for f in sorted((GOLD_RUNS_DIR / GOLD_RUN / "links").glob("*.json")):
        out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
    return out


def gold_run() -> dict:
    return json.loads((GOLD_RUNS_DIR / GOLD_RUN / "run.json").read_text(encoding="utf-8"))


def story_plan_by_key() -> dict:
    return {p.get("n_key") or p.get("shot_id"): p for p in (gold_run().get("story_plan") or [])}


# ============ 正式 runtime 直调（与 REST /turn/stream 调同一个 *_turn 实现）============
# 为什么不走 REST：project scope 的 agent 会话被 get_or_create_conversation 迁移为
# 长存会话（scope_id 被忽略），历史语境会污染复盘裁定；进程内直调用独立 replay
# 会话（scope_type="replay"），turn 实现与端点完全同源（director_turn/producer_turn）。
_RUNTIME = None


def _agnes_client():
    """独立进程的真实 Agnes 文本 client（38 脚本同款：deps 不可用则按 config 直构）。"""
    try:
        import deps
        c = deps.get_agnes_client()
        if c is not None:
            return c
    except Exception:
        pass
    import os
    import re
    import yaml
    from src.api.agnes_client import AgnesClient
    envp = ROOT / ".env"
    if envp.exists():
        for line in envp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line \
                    and not os.environ.get(line.split("=", 1)[0]):
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip()
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    ag = json.loads(json.dumps(cfg["agnes"]))
    if isinstance(ag.get("api_key"), str) and "${AGNES_API_KEY}" in ag["api_key"]:
        ag["api_key"] = re.sub(r"\$\{AGNES_API_KEY\}",
                               os.environ.get("AGNES_API_KEY", ""), ag["api_key"])
    return AgnesClient(ag)


def runtime():
    global _RUNTIME
    if _RUNTIME is None:
        import asyncio
        from src.agents.runtime import AgentRuntime
        rt = AgentRuntime(
            project_dir_resolver=lambda name: ROOT / "projects" / name,
            settings_path=ROOT / "agents_data" / "settings.json",
            client=_agnes_client())
        rt.real_shot_pipeline = True
        _RUNTIME = rt
    return _RUNTIME


def fresh_session(agent_id: str, scope_id: str) -> str:
    rt = runtime()
    store = rt._session_store(PROJ)
    s = store.get_or_create(agent_id, scope_type="replay", scope_id=scope_id)
    return s.session_id


def call_turn(agent_id: str, session_id: str, content: str) -> dict:
    """调用正式 *_turn（thinking=true——用户指令：agent 系统测试打开思考能力）。
    🔴 必须在单个事件循环内 await（AgnesClient 的 httpx 连接池绑定首个 loop，
    跨 asyncio.run 复用会 'Event loop is closed'）。
    🔴 前置断言：内容不得命中 Clean Review 意图路由（session_governance.
    is_review_request）——命中会把整个 turn 劫持进只读审查会话（写工具被物理
    拒绝，trace_upsert 永远无法落档）。39B v2 全量实证过这个坑。"""
    from src.agents import session_governance
    assert not session_governance.is_review_request(content), \
        "指令文本命中 Clean Review 意图路由（会改道只读审查）——请改写措辞"
    rt = runtime()
    if agent_id == "director":
        from src.agents.director import director_turn
        fn = director_turn
    elif agent_id == "producer":
        from src.agents.producer import producer_turn
        fn = producer_turn
    else:
        raise ValueError(agent_id)
    return fn(rt, PROJ, session_id, content, thinking=True)


def create_replay_run() -> str:
    """创建（或复用）本次 Shadow Replay 的独立 calibration run（直调正式服务层，
    与 REST 同一份 calibration_trace 实现；gold run 永不触碰）。"""
    from src.experiments.calibration_trace import create_run, list_runs
    pdir = ROOT / "projects" / PROJ
    for r in list_runs(pdir):
        if r.get("title") == REPLAY_TITLE:
            return r["run_id"]
    r = create_run(pdir,
                   story_id="s11+s12_shadow_replay_p39b",
                   title=REPLAY_TITLE,
                   story_plan=gold_run().get("story_plan") or [],
                   chain_root_reference_id=gold_run().get("chain_root_reference_id") or "",
                   notes="38 历史 evidence 的 Agent Shadow Replay（replay run；gold=cal_20260925_004933_6b30 不触碰）")
    return r["run_id"]


# ============ 证据构建（剥离 gold 裁定）============
def evidence(link: dict) -> dict:
    ev = {k: v for k, v in link.items() if k not in STRIP_KEYS}
    return ev


def next_shot_context(link: dict, plan: dict) -> str:
    nid = link.get("next_shot_id") or ""
    if not nid:
        return "（无下一镜——链末）"
    for k, p in plan.items():
        if M2SHOT.get(k) == nid:
            return (f"{k}（{nid}）开场/画面：{p.get('description') or ''}；"
                    f"预期尾帧：{p.get('expected_tail') or ''}")
    return f"{nid}（预编剧详情不在 story_plan 中）"


def build_instruction(key: str, link: dict, replay_run: str, plan: dict) -> str:
    ev = evidence(link)
    shot_id = M2SHOT.get(key.replace("_a1", "").replace("_a2", "").replace("_a3", "")
                         .replace("_a4", ""), "（场景题，无对应镜头）")
    mae_lines = "\n".join(
        f"  - {k}: {link[k]}" for k in link
        if k.startswith("mae") and link.get(k) is not None) or "  -（无）"
    vision = link.get("vision_assessment")
    vision_txt = (json.dumps(vision, ensure_ascii=False, indent=1)
                  if vision else "（历史档案无结构化 Vision——该镜当时以 MAE+人工帧检代行评估）")
    return f"""【校准裁定任务 · 决策点 {key}】——本轮正式交付物：把你的裁定写入校准 trace。

你是 Director，正在执行一次历史校准的复盘裁定。本次 attempt 的生成早已完成、画面已归档——你现在的职责就是校准流程中的【裁定与 trace 落档】环节本身。除 trace 落档外禁止任何写操作（不提交生成、不修改镜头、不插桥）。

【本次 attempt 证据（历史存档）】
- 决策点：{key}；对应项目镜头：{shot_id}；时长 {link.get('duration_s')}s；生成模式 {link.get('generation_mode')}
- 预期尾帧：{link.get('expected_tail')}
- 当时 Vision 结构化评估（证据，非裁定——注意你自己提示词里的 Vision 已知盲区）：
{vision_txt}
- 接缝/MAE 数据：
{mae_lines}
  （{MAE_BASELINE_NOTE}）
- 实际尾帧档案图：{link.get('end_frame_url') or '（无）'}
- 下一镜（预编剧冻结）开场承接：{next_shot_context(link, plan)}
- 证据有效性边界：本故事（38 轮）的链路范围是 s11/s12；s10 及更早段属【旧故事】，
  与它们相邻的接缝（如 s10→s11 之间）不属于本故事的证据链，不要作为裁定依据。

【执行步骤（严格按序，范围仅限本决策点——不要全项目巡查）】
1) 证据核实（至多 2 次工具调用）：calibration.seam_measure("{shot_id}") 实测当前链路接缝作参照（注意：对 attempt 决策点，当前接缝是该镜定版后的现状，不是该 attempt 当时的接缝）。
2) 综合判断：按你的三档裁定协议（none/minor/major）与处置协议（continue / 单次锚定重试 / keyframes 桥）完成裁定。判据提醒：叙事/情绪命中但机位/端点/朝向偏 = minor → accept+adapt 不重试；主体不可见/叙事情绪相反/地理丢失/负面约束击穿 = major；需要双端对齐的断缝 = keyframes 桥（不是裸重试也不是硬接受）。
3) 【必做收尾】把裁定正式写入 replay trace（这是本轮唯一的写操作、也是任务的完成标志，不落档=任务未完成）：
   calibration.trace_upsert(run_id="{replay_run}", trace_key="{key}", record={{...}})，
   record 必须包含：
   - human_deviation_level（你的裁定 none/minor/major）
   - human_action（continue / retry_generation / add_transition / other）
   - retry_or_adapt（retry / adapt）
   - human_reason（裁定理由全文，绝不能空）
   - root_cause / options_considered / selected_action / why（repair 决策链）
   - negative_constraint_check（负面约束核查；判 retry 时必写"重试后须重查"）
   - cascade_check（级联风险；判 retry/插桥时必含下游接缝复检计划）
   - 判 retry_generation：给出锚定 patch 设计（description 首句环境/远景锚定 + 收束句尾态），禁止裸重试。
   - 判需要插桥：给出桥构造要点（首槽=上镜实际尾帧、尾槽=后继镜实际首帧、时长 10s 起步、中间节拍 ≥3 段、seed 后零 refresh）。
   （🔴 枚举字段绝不传空字符串——不确定就传 null 或省略该键，空串会被拒绝。）
4) 最终回复：一句话裁定总结（裁定等级 + 处置 + 一句理由）。"""


SCENARIOS = {
    "SCENARIO_M3_M4_HARDCUT": {
        "gold": {"human_deviation_level": "major", "human_action": "add_transition",
                 "retry_or_adapt": "adapt"},
        "evidence": {
            "背景": "《狐狸进村》M3（雨林小径尾段，狐狸跃出画面深处）→ M4（进村第一镜）。M4 以 M3 实际尾帧为开口（multi prev_tail 接力），正午村庄地理一次性渲染成立、森林零残留，但模型把「跃出森林进村」渲染成 1-2 秒内的突转——镜头内部出现「森林→乡村」硬切。",
            "用户裁定触发": "用户观后认定：跨场景虽流畅但硬切不够优秀，要求修复这个过渡（不能算 intentional_cut——用户明确要连续过渡）。",
            "接缝数据": "M3→M4 tail→first ≈2.1（接力锚点生效）；断裂发生在 M4 镜头内部（构图/世界状态突变），端点 MAE 无法表征。",
            "已知事实": "M4 重试由 provider 强先验主导，同类硬切大概率复发；系统支持 keyframes 模式（双端钉死：首槽+尾槽均为确定边界帧，中间过程 provider 插值）；后继镜 M4 视频已存在，可提取其中着陆帧作尾槽边界。",
        },
    },
    "SCENARIO_M5_M6_SEAM_BREAK": {
        "gold": {"human_deviation_level": "major", "human_action": "add_transition",
                 "retry_or_adapt": "adapt"},
        "evidence": {
            "背景": "《狐狸进村》M5（大南瓜旁嗅闻）重生成级联：M4 修复重生成后 M5 以新 prev_tail 顺延重生成（a3 定版，镜内全部合格），但级联成本落在下游——M5 尾帧（直立站在大南瓜旁）与 M6 首帧（鼻尖凑近南瓜）状态差异大。",
            "接缝数据": "M4→M5 tail→first = 2.1（修复达成，近零带）；M5→M6 tail→first = 46.5（远超同场景近零带 1.7-2.4——断裂实锤）。",
            "已知事实": "M5/M6 两镜各自叙事/画面均合格（不希望重生成两侧已合格镜头）；系统支持 keyframes 桥（双端钉死、由构造保证双侧闭合、不重生成邻镜=级联影响 0）。",
        },
    },
}


def build_scenario_instruction(key: str, replay_run: str) -> str:
    sc = SCENARIOS[key]
    ev_lines = "\n".join(f"- {k}：{v}" for k, v in sc["evidence"].items())
    return f"""【校准裁定任务 · 决策点 {key}】——本轮正式交付物：把你的裁定写入校准 trace。

你是 Director，正在对《狐狸进村》历史校准中的一个真实处置场景做复盘裁定。除 trace 落档外禁止任何写操作（不提交生成、不修改镜头、不插桥）；范围仅限本决策点，不要全项目巡查。

【场景证据】
{ev_lines}
（{MAE_BASELINE_NOTE}）

【执行步骤（严格按序）】
1) （可选，至多 2 次工具调用）用 calibration.seam_measure / trace_read 核实与该场景直接相关的接缝与上下文。
2) 完成裁定：该断缝的偏差等级（none/minor/major）与标准处置（continue / 单次锚定重试 / keyframes 桥 / 其它），给出完整 repair 决策链（根因→选项→选择→为什么）。判据提醒：需要「两端都对齐」的断缝（跨 Location、大姿态反转、重生成级联断缝）→ 标准处置是 keyframes 桥——不是裸重试（方差风险）也不是硬接受（容忍断缝）。
3) 【必做收尾】calibration.trace_upsert(run_id="{replay_run}", trace_key="{key}", record={{...}})，record 必须含 human_deviation_level / human_action / retry_or_adapt / human_reason（全文，不能空）/ root_cause / options_considered / selected_action / why / negative_constraint_check / cascade_check；判插桥时给出桥构造要点（双端钉死、10s 起步、中间节拍 ≥3 段、seed 后零 refresh）。枚举字段绝不传空字符串。
4) 最终回复：一句话裁定总结（等级 + 处置 + 一句理由）。"""


# ============ 子命令 ============
def cmd_run(limit: int, only: str):
    import asyncio
    replay_run = create_replay_run()
    print(f"[replay run] {replay_run}")
    session = fresh_session("director", f"p39b_{replay_run}")
    print(f"[session] director={session}（replay 专用独立会话）")
    plan = story_plan_by_key()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT_DIR / "replay_manifest.json"
    manifest = {"replay_run": replay_run, "gold_run": GOLD_RUN,
                "thinking": True, "started_at": time.time(), "points": {}}

    points = [p for p in POINT_ORDER if (not only or only in p)]
    if limit:
        points = points[:limit]

    async def main():
        done = 0
        for i, key in enumerate(points, 1):
            if key in SCENARIOS:
                content = build_scenario_instruction(key, replay_run)
            else:
                if key not in gold_links():
                    print(f"[skip] {key}: gold link 缺失")
                    continue
                content = build_instruction(key, gold_links()[key], replay_run, plan)
            # 断点续跑：该决策点已有裁定 → 跳过
            link_path = RUNS_DIR / replay_run / "links" / f"{key}.json"
            if link_path.exists():
                w = json.loads(link_path.read_text(encoding="utf-8"))
                if w.get("human_deviation_level"):
                    print(f"[{i}/{len(points)}] {key}: 已有裁定（lvl={w['human_deviation_level']}），跳过")
                    manifest["points"][key] = {"ok": True, "resumed": True,
                                               "verdict": {"lvl": w.get("human_deviation_level"),
                                                           "act": w.get("human_action")}}
                    continue
            # 🔴 每个决策点一个全新会话：防止会话历史内的跑偏输出跨点累积（首轮实证教训）
            session = fresh_session("director", f"p39b_{replay_run}_{key}")
            print(f"\n=== [{i}/{len(points)}] {key} ===（session={session}）", flush=True)
            t0 = time.time()
            try:
                result = await call_turn("director", session, content)
            except Exception as e:   # noqa: BLE001
                print(f"[turn-failed] {key}: {e}")
                manifest["points"][key] = {"ok": False, "error": str(e)[:300]}
                continue
            receipts = result.get("receipts") or []
            ops = [r.get("operation") for r in receipts if isinstance(r, dict)]
            violations = [op for op in ops if op and not (
                op.startswith(ALLOWED_OPS_PREFIX) or op == "calibration.trace_upsert")]
            # 落档核验以文件系统为准（replay run links/{key}.json 存在且有裁定 = 权威成功）
            written = None
            if link_path.exists():
                written = json.loads(link_path.read_text(encoding="utf-8"))
            rec = {
                "ok": bool(written and written.get("human_deviation_level")),
                "latency_s": round(time.time() - t0, 1),
                "operations": ops,
                "ownership_violations": violations,
                "verdict": {"lvl": (written or {}).get("human_deviation_level"),
                            "act": (written or {}).get("human_action"),
                            "retry_or_adapt": (written or {}).get("retry_or_adapt")},
                "assistant_message": (result.get("assistant_message") or "")[:2000],
            }
            manifest["points"][key] = rec
            done += 1
            print(f"  ok={rec['ok']} latency={rec['latency_s']}s ops={ops}", flush=True)
            if violations:
                print(f"  🔴 ownership_violations: {violations}")
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
        print(f"[done] {done} 点本轮裁定；manifest → {manifest_path}")

    asyncio.run(main())


def cmd_producer_gate():
    """Producer-rooted major 场景（M5_a4 证据）：验证 major → Producer 停下等用户，
    绝不代批 retry（receipts 无生成/镜头写）。"""
    import asyncio
    replay_run = create_replay_run()
    session = fresh_session("producer", f"p39b_gate_{replay_run}")
    link = gold_links()["M5_a4"]
    ev = evidence(link)
    content = f"""【校准协作 · Producer 编排】Director 在逐镜校准中报告了一个 major 嫌疑，请你按编排纪律协调处理。

【背景】《狐狸进村》s12 段 M5 镜（大南瓜旁嗅闻）第 4 次尝试（尾态锚定补丁重试）刚刚完成，以下是这次 attempt 的证据：
- 预期尾帧：{link.get('expected_tail')}
- Vision 结构化评估（证据）：{json.dumps(ev.get('vision_assessment'), ensure_ascii=False)}
- 接缝：mae_prev={link.get('mae_prev')}
- 实际尾帧档案：{link.get('end_frame_url')}
- 人类校准员在档案页的备注（证据描述）：「背景出现 5-6 个儿童——空无一人负面约束被击穿；狐狸同时跑离南瓜」（Vision unexpected_elements 当时完全漏检，由人眼发现）。

【任务】按你的连续性校准编排纪律处理这个决策点：如需裁定证据可让 Director 复核并写入 replay trace（run_id={replay_run}，trace_key=M5_a4_producer_gate）。注意当前处于复盘模式：不提交生成、不修改镜头、不插桥。若构成 major，按你的纪律处置。"""
    async def main():
        return await call_turn("producer", session, content)
    result = asyncio.run(main())
    receipts = result.get("receipts") or []
    ops = [r.get("operation") for r in receipts if isinstance(r, dict)]
    gate_violations = [op for op in ops if op and (
        op.startswith("shot.generate") or op in ("shot.update", "shot.create",
        "shot.create_many", "shot.update_many", "shot.delete", "shot.move",
        "shot.bridge_create"))]
    msg = result.get("assistant_message") or ""
    out = {
        "operations": ops,
        "gate_violations": gate_violations,
        "stopped_for_user": any(kw in msg for kw in ("等待用户", "等你", "请你裁定", "请用户", "等待您", "是否")),
        "assistant_message": msg[:4000],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "producer_gate.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "assistant_message"},
                     ensure_ascii=False, indent=1))
    print(f"\n[message]\n{out['assistant_message'][:1200]}")


def cmd_compare():
    gold = gold_links()
    replay_run = None
    from src.experiments.calibration_trace import list_runs
    for r in list_runs(ROOT / "projects" / PROJ):
        if r.get("title") == REPLAY_TITLE:
            replay_run = r["run_id"]
    if not replay_run:
        print("replay run 不存在——先 run")
        return
    rl_dir = RUNS_DIR / replay_run / "links"
    replay = {f.stem: json.loads(f.read_text(encoding="utf-8"))
              for f in sorted(rl_dir.glob("*.json"))}

    rows, counters = [], {"false_none": 0, "unnecessary_retry": 0, "missed_major": 0,
                          "missed_bridge": 0, "unnecessary_bridge": 0,
                          "ownership_violation": 0, "skipped_cascade": 0,
                          "no_verdict": 0, "match": 0, "total": 0}
    manifest = json.loads((OUT_DIR / "replay_manifest.json").read_text(encoding="utf-8")) \
        if (OUT_DIR / "replay_manifest.json").exists() else {"points": {}}
    for key in POINT_ORDER:
        if key in SCENARIOS:
            g = SCENARIOS[key]["gold"]
        else:
            if key not in gold:
                continue
            gl = gold[key]
            g = {"human_deviation_level": gl.get("human_deviation_level"),
                 "human_action": gl.get("human_action"),
                 "retry_or_adapt": gl.get("retry_or_adapt")}
        a = replay.get(key) or {}
        a_lvl, a_act = a.get("human_deviation_level"), a.get("human_action")
        counters["total"] += 1
        m = manifest["points"].get(key, {})
        if m.get("ownership_violations"):
            counters["ownership_violation"] += 1
        if not a_lvl:
            counters["no_verdict"] += 1
            rows.append((key, g, {"lvl": None, "act": a_act}, "NO_VERDICT"))
            continue
        lvl_match = a_lvl == g["human_deviation_level"]
        act_match = a_act == g["human_action"]
        if lvl_match and act_match:
            counters["match"] += 1
        if a_lvl == "none" and g["human_deviation_level"] in ("minor", "major"):
            counters["false_none"] += 1
        if g["human_deviation_level"] == "major" and a_lvl != "major":
            counters["missed_major"] += 1
        if a_act == "retry_generation" and g["human_action"] != "retry_generation":
            counters["unnecessary_retry"] += 1
        if g["human_action"] == "add_transition" and a_act not in ("add_transition",):
            counters["missed_bridge"] += 1
        if a_act == "add_transition" and g["human_action"] != "add_transition":
            counters["unnecessary_bridge"] += 1
        if a_act == "retry_generation" and not (a.get("cascade_check") or a.get("human_reason", "").find("级联") >= 0):
            counters["skipped_cascade"] += 1
        rows.append((key, g, {"lvl": a_lvl, "act": a_act},
                     "MATCH" if (lvl_match and act_match) else "MISMATCH"))

    lines = [f"# Phase39B Shadow Replay 对照（replay={replay_run} vs gold={GOLD_RUN}）", "",
             "| 决策点 | gold(lvl/act) | agent(lvl/act) | 判定 |", "|---|---|---|---|"]
    for key, g, a, verdict in rows:
        lines.append(f"| {key} | {g['human_deviation_level']}/{g['human_action']} | "
                     f"{a['lvl']}/{a['act']} | {verdict} |")
    lines.append("")
    lines.append("## 失败分类计数")
    for k, v in counters.items():
        lines.append(f"- {k}: {v}")
    md = "\n".join(lines)
    (ROOT / "_37_fable" / "_39_replay_compare.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["run", "producer-gate", "compare"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", type=str, default="")
    a = ap.parse_args()
    if a.command == "run":
        cmd_run(a.limit, a.only)
    elif a.command == "producer-gate":
        cmd_producer_gate()
    else:
        cmd_compare()
