# src/agents/director_calibration.py
# Phase 39（39.md §6.1/§6.2）：Director 的连续性校准工具面——
#   calibration.seam_measure  接缝测量（上镜实际尾帧 → 本镜实际首帧，Phase38 同算法）
#   calibration.trace_read    校准 run/link 只读
#   calibration.trace_upsert  校准 link 记录/更新（含 attempt 归档，历史不覆盖）
#
# 定位：
#   - 本模块只是工具接线（schema + executor），测量本体在
#     src/storyboard/continuity_service.py（确定性，零 LLM）；trace 持久化在
#     src/experiments/calibration_trace.py（既有正式服务层，REST 同源）——
#     Agent 绝不直接拼 JSON / 改实验文件 / curl REST（39.md §22）。
#   - trace 非 canonical：trace_upsert 占一个写槽（写纪律），但回执用 trace_key
#     而非 shot_id（不参与 Producer changed_refs 的 shot 变更派生——trace 不是
#     canonical 变更）。读工具回执不设 verified=True（读不是写执行）。
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from .runtime import AgentError, AgentRuntime
from .script_writer import _base_receipt, _parse_args

from src.core.store import ProjectStore
from src.experiments.calibration_trace import (
    CalibrationTraceError, create_run, get_link, get_run, list_links,
    list_runs, upsert_link,
)
from src.storyboard import continuity_service


# ============ calibration.seam_measure ============
CALIBRATION_SEAM_TOOL = {
    "type": "function",
    "function": {
        "name": "calibration.seam_measure",
        "description": (
            "测量接缝：上一镜（播放序）实际视频尾帧 → 本镜实际视频首帧的 MAE"
            "（128 灰度同刻度，与历史校准同一算法）。只读。返回 tail_to_first_mae"
            "（接续性指标）与可选 end_to_end_mae（端点构图漂移，另一个量，禁止混写）、"
            "两侧资产 provenance、预处理版本与历史基线提示。基线：同场景正常接力≈2"
            "（近零带 1.7-2.4）；跨场景/构图切换 26-60；剧变构图 90+。"
            "注意：MAE 不是 none/minor/major 分类器，要结合语义判断。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shot_id": {"type": "string",
                            "description": "本镜镜头 id（测它与播放序上一镜之间的接缝）。"},
                "include_end_to_end": {"type": "boolean",
                                       "description": "是否同时返回 end_to_end_mae"
                                                      "（本镜尾帧就绪时才有值）。缺省 false。"},
            },
            "required": ["shot_id"],
        },
    },
}


async def _execute_seam_measure(rt: AgentRuntime, project: str, arguments: str,
                                instruction, message_id) -> dict:
    args = _parse_args(arguments)
    shot_id = (args.get("shot_id") or "").strip()
    if not shot_id:
        raise AgentError("bad_arguments", "缺少 shot_id")
    include_e2e = bool(args.get("include_end_to_end") or False)
    pdir = rt._resolve_project_dir(project)
    proj = ProjectStore(pdir).load()
    try:
        r = continuity_service.measure_seam(pdir, proj, shot_id,
                                            include_end_to_end=include_e2e)
    except continuity_service.ContinuityError as e:
        raise AgentError(e.code, e.message)
    return _base_receipt(
        "calibration.seam_measure", ok=True, target=shot_id,
        shot_id=None,   # 显式置 None：测量是只读分析，不进 shot 变更派生
        tail_to_first_mae=r["tail_to_first_mae"],
        end_to_end_mae=r["end_to_end_mae"],
        left=r["left"], right=r["right"],
        preprocessing_version=r["preprocessing_version"],
        baseline_hint=r["baseline_hint"],
        **({"end_to_end_mae_error": r["end_to_end_mae_error"]}
           if r.get("end_to_end_mae_error") else {}),
        note=(f"接缝（{r['left']['shot_id']} 尾帧 → {r['right']['shot_id']} 首帧）"
              f"tail_to_first_mae={r['tail_to_first_mae']}"
              f"（{r['preprocessing_version']}）。"
              "这是测量值不是裁定——结合叙事/情绪/状态判断 none/minor/major。"))


# ============ calibration.run_create（42.md §二缺陷①）============
# Phase 41 缺陷清单①：agent 无 run 创建工具 → trace_upsert 全部 not_found，
# 13 镜重测 trace 落档失败，只能靠执行侧脚本预建 run（外部脚手架=测试污染）。
# 本工具把 run 创建纳入正式工具面（与 REST routers/experiments.py:50 同源服务层
# calibration_trace.create_run），agent 可自主完成「建 run→写 trace→读 trace」闭环。
CALIBRATION_RUN_CREATE_TOOL = {
    "type": "function",
    "function": {
        "name": "calibration.run_create",
        "description": (
            "创建一次校准实验 run（trace 落档的前置：trace_upsert/trace_read 都"
            "需要已存在的 run_id）。story_id 填项目故事标识；title 简述本次校准"
            "目的；chain_root_reference_id 可选（链根参考资产）。run 创建后用"
            "trace_upsert 写各镜 link（同键新 attempt 用 archive_existing=true）。"
            "写工具（占一个写槽，非 canonical）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "story_id": {"type": "string",
                             "description": "故事标识（如 yudeng_guihang）。"},
                "title": {"type": "string",
                          "description": "run 标题（本次校准目的，简短）。缺省空。"},
                "chain_root_reference_id": {"type": "string",
                                            "description": "链根参考资产 id。缺省空。"},
                "notes": {"type": "string",
                          "description": "备注（冻结声明/任务书要点）。缺省空。"},
            },
            "required": ["story_id"],
        },
    },
}


async def _execute_run_create(rt: AgentRuntime, project: str, arguments: str,
                              instruction, message_id) -> dict:
    args = _parse_args(arguments)
    story_id = (args.get("story_id") or "").strip()
    if not story_id:
        raise AgentError("bad_arguments", "缺少 story_id")
    title = str(args.get("title") or "").strip()
    chain_root = str(args.get("chain_root_reference_id") or "").strip()
    notes = str(args.get("notes") or "").strip()
    pdir = rt._resolve_project_dir(project)
    run = create_run(pdir, story_id=story_id, title=title,
                     chain_root_reference_id=chain_root, notes=notes)
    return _base_receipt(
        "calibration.run_create", ok=True, target=run["run_id"],
        shot_id=None,   # 实验层创建，不进 shot 变更派生（同 trace_upsert 口径）
        run_id=run["run_id"], story_id=run["story_id"],
        created_at=run["created_at"],
        note=(f"校准 run 已创建：{run['run_id']}。后续 trace_upsert/trace_read "
              f"用此 run_id；裁定量字段要求见 trace_upsert 工具说明。"))


# ============ calibration.trace_read ============
CALIBRATION_TRACE_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "calibration.trace_read",
        "description": (
            "读校准 trace（只读）。三种粒度：不带参数=列出全部校准 run（轻量地图）；"
            "带 run_id=读该 run（story_plan/status/notes + 已有 link 键清单）；"
            "带 run_id+trace_key=读一条 link 全文（预期尾帧/MAE/vision/人工裁定/"
            "repair 决策链/历史 attempt）。裁定量最大化复用历史纪律：判镜前先读"
            "同 run 的相邻 link。"),
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string",
                           "description": "校准 run id（cal_开头）。省略=列出全部 run。"},
                "trace_key": {"type": "string",
                              "description": "link 键（通常为镜头 key，如 M3 或 s11_c04）。"
                                             "省略=读 run 概要。"},
            },
            "required": [],
        },
    },
}


async def _execute_trace_read(rt: AgentRuntime, project: str, arguments: str,
                              instruction, message_id) -> dict:
    args = _parse_args(arguments)
    run_id = (args.get("run_id") or "").strip()
    trace_key = (args.get("trace_key") or "").strip()
    pdir = rt._resolve_project_dir(project)
    try:
        if not run_id:
            runs = list_runs(pdir)
            return {"ok": True, "operation": "calibration.trace_read",
                    "runs": [{"run_id": r.get("run_id"), "title": r.get("title"),
                              "story_id": r.get("story_id"), "status": r.get("status"),
                              "created_at": r.get("created_at"),
                              "updated_at": r.get("updated_at"),
                              "story_plan_count": len(r.get("story_plan") or [])}
                             for r in runs],
                    "count": len(runs),
                    "note": "带 run_id 读单个 run；带 run_id+trace_key 读单条 link。"}
        run = get_run(pdir, run_id)
        if not trace_key:
            keys = sorted(l.get("shot_id", "") for l in list_links(pdir, run_id))
            return {"ok": True, "operation": "calibration.trace_read",
                    "run": {"run_id": run.get("run_id"), "title": run.get("title"),
                            "story_id": run.get("story_id"), "status": run.get("status"),
                            "story_frozen": run.get("story_frozen"),
                            "chain_root_reference_id": run.get("chain_root_reference_id"),
                            "notes": run.get("notes"),
                            "story_plan": run.get("story_plan") or [],
                            "link_keys": keys, "link_count": len(keys)},
                    "note": "带 trace_key 读单条 link 全文。"}
        link = get_link(pdir, run_id, trace_key)
        return {"ok": True, "operation": "calibration.trace_read",
                "run_id": run_id, "trace_key": trace_key, "link": link}
    except CalibrationTraceError as e:
        raise AgentError(e.code, str(e))


# ============ calibration.trace_upsert ============
CALIBRATION_TRACE_UPSERT_TOOL = {
    "type": "function",
    "function": {
        "name": "calibration.trace_upsert",
        "description": (
            "记录/更新一条校准 link（合并语义：顶层键覆盖、dict 深合并——vision 先记、"
            "裁定后补不丢字段）。每次裁定至少保存：expected_tail / actual_tail / "
            "tail_to_first_mae / end_to_end_mae / vision_assessment / "
            "director_visual_observation / vision_vs_director_diff / deviation_level"
            "（none/minor/major）/ root_cause / options_considered / selected_action / "
            "why / retry_or_adapt（retry/adapt）/ negative_constraint_check / "
            "cascade_check / repair 决策链全文。枚举字段传 null 可省略，绝不能传空字符串。"
            "🔴 记录同键新 attempt 前必须 archive_existing=true：把已有已裁定的旧 link "
            "归档为 {key}_a{N}（历史 attempt 不覆盖）。写工具（占一个写槽）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "校准 run id（cal_开头）。"},
                "trace_key": {"type": "string",
                              "description": "link 键（通常为镜头 key，如 M3）。"},
                "record": {
                    "type": "object",
                    "description": ("要写入的字段（合并语义，只传本次要写的）。"
                                    "枚举校验：human_deviation_level ∈ none/minor/major；"
                                    "human_action ∈ continue/add_transition/"
                                    "insert_story_beat/retry_generation/"
                                    "break_chain_and_reanchor/other；"
                                    "retry_or_adapt ∈ retry/adapt。None 可省略，"
                                    "空字符串会被拒绝。"),
                },
                "archive_existing": {
                    "type": "boolean",
                    "description": "记录前把该键已有【已裁定】的旧 link 归档为 "
                                   "{key}_a{N}（attempt 历史不覆盖）。重试/重生成"
                                   "录新 attempt 时必须传 true。缺省 false。",
                },
            },
            "required": ["run_id", "trace_key", "record"],
        },
    },
}


def _archive_adjudicated_link(pdir, run_id: str, trace_key: str) -> Optional[str]:
    """已有【已裁定】（human_deviation_level 非空）的 link 归档为 {key}_a{N}
    （N = 已有 _a 归档数 + 1）。与 _38_generate.py 的 attempt 归档同语义。
    返回归档键；无需归档返回 None。"""
    try:
        existing = get_link(pdir, run_id, trace_key)
    except CalibrationTraceError:
        return None
    if not (existing.get("human_deviation_level") or "").strip():
        return None
    prefix = f"{trace_key}_a"
    n = 1 + sum(1 for k in (str(l.get("shot_id") or "")
                            for l in list_links(pdir, run_id))
                if k.startswith(prefix))
    archive_key = f"{prefix}{n}"
    upsert_link(pdir, run_id, archive_key, existing)
    return archive_key


# ============ trace 落档时的两条软提示（用户定版：与校准 skill 绑定）============
# trace_upsert 是校准 skill 工作流的专属收尾动作——不跑这个 skill 就不会有 trace，
# 因此提示天然与 skill 绑定：不用这个 skill 做的 reference_only 镜不会收到任何警告。
# 两条都是尽力而为的软提示（绝不阻断落档、绝不改写 Agent 的 record）。

# semantic roles（VideoVersion.input_visuals，adapter 权威）→ 实际生成模式
_MODE_FROM_ROLES = [
    ("relay_start_boundary", "multi_relay"),
    ("hard_end_boundary", "keyframes_bridge"),   # 与 hard_start 同现才判桥（下方组合判断）
    ("hard_start_boundary", "i2v"),
    ("reference_only", "multi_reference"),
]


def _derive_mode_from_input_visuals(ivs: list) -> Optional[str]:
    """从视频实际消费的输入图清单推导实际生成模式（input_visuals 为权威记录）。"""
    roles = {str(iv.get("semantic_role") or "") for iv in (ivs or [])}
    if "relay_start_boundary" in roles:
        return "multi_relay"
    if "hard_start_boundary" in roles and "hard_end_boundary" in roles:
        return "keyframes_bridge"
    if "hard_start_boundary" in roles:
        return "i2v"
    if "reference_only" in roles:
        return "multi_reference"
    return None


def _trace_input_hints(pdir, trace_key: str, record: dict) -> dict:
    """两条软提示（trace_upsert 专属，best-effort）：
    1. mode 对账：record 声称的 generation_mode vs input_visuals 权威推导——
       39D t3 实证（汇报 multi 实际 i2v）。
    2. 组内 reference-only 意图提醒：段内非首镜、上镜已有视频、本镜最新视频
       以 reference_only 开场且 record 无正当理由（continuity_type=intentional_cut
       或 reference_only_note）→ 提醒确认是有意的独立镜而非漏配接力。
       记录正当理由后不再提醒（不拖慢有意的特殊处理）。"""
    out: dict = {}
    proj = ProjectStore(pdir).load()
    shot = proj.get_shot(trace_key)
    if shot is None:
        return out
    vv = next((v for v in (shot.video_versions or [])
               if v.version_id == shot.final_video), None)
    ivs = list(getattr(vv, "input_visuals", None) or []) if vv is not None else []

    # 1) mode 对账
    claimed = str(record.get("generation_mode") or "").strip()
    actual = _derive_mode_from_input_visuals(ivs)
    if claimed and actual and claimed != actual:
        out["mode_mismatch_warning"] = (
            f"record 声称 generation_mode={claimed}，但本镜最终视频实际消费的输入是 "
            f"{actual}（input_visuals 权威记录）。请核对后用正确 mode 重新 upsert——"
            "trace 是验收对象，不得保留与 dispatch 事实不符的声明。")

    # 2) 组内 reference-only 意图提醒
    if ivs:
        start_roles = {str(iv.get("semantic_role") or "") for iv in ivs}
        start_is_reference = ("reference_only" in start_roles
                              and "relay_start_boundary" not in start_roles)
        scene = next((s for s in proj.scenes
                      if any(x.shot_id == shot.shot_id for x in s.shots)), None)
        if scene is not None and start_is_reference:
            ids = [x.shot_id for x in scene.shots]
            idx = ids.index(shot.shot_id) if shot.shot_id in ids else -1
            prev_ready = idx > 0 and bool(scene.shots[idx - 1].final_video)
            justified = (str(record.get("continuity_type") or "").strip().lower()
                         == "intentional_cut"
                         or bool(str(record.get("reference_only_note") or "").strip()))
            if idx > 0 and prev_ready and not justified:
                prev_id = scene.shots[idx - 1].shot_id
                out["relay_intent_hint"] = (
                    f"本镜（{shot.shot_id}）为段内非首镜且上镜 {prev_id} 已有视频，"
                    f"但本镜最新视频以 reference_only 开场（未接力）。若这是有意的"
                    f"独立镜/hard cut，请在 trace 记 continuity_type=intentional_cut"
                    f"（或 reference_only_note 说明理由），本提醒即不再出现；"
                    f"若叙述意图实为接续，请先补 relay（vref=prev_tail）再生成。")

    # 3) 裁定完整性警告（39C/39E 两次实证：视频已生成但裁定枚举字段空置——
    #    skill 的 prose 收口条款未改变行为，这里给牙齿）
    has_video = bool(shot.final_video)
    if has_video and not str(record.get("human_deviation_level") or "").strip():
        out["adjudication_incomplete_warning"] = (
            f"镜头 {shot.shot_id} 已有最终视频，但本次 record 缺 human_deviation_level"
            f"（none/minor/major）与 human_action——裁定未落档=方法未完成。请立即"
            f"补一次 trace_upsert 写全裁定字段（档位+依据+下一步）。")
    return out


async def _execute_trace_upsert(rt: AgentRuntime, project: str, arguments: str,
                                instruction, message_id) -> dict:
    args = _parse_args(arguments)
    run_id = (args.get("run_id") or "").strip()
    trace_key = (args.get("trace_key") or "").strip()
    record = args.get("record")
    archive_existing = bool(args.get("archive_existing") or False)
    if not run_id or not trace_key:
        raise AgentError("bad_arguments", "缺少 run_id 或 trace_key")
    if not isinstance(record, dict) or not record:
        raise AgentError("bad_arguments",
                         "record 必须是非空对象（要写入的 trace 字段）")
    pdir = rt._resolve_project_dir(project)

    archived_to = None
    if archive_existing:
        archived_to = _archive_adjudicated_link(pdir, run_id, trace_key)
    try:
        merged = upsert_link(pdir, run_id, trace_key, record)
    except CalibrationTraceError as e:
        raise AgentError(e.code, str(e))
    # 软提示（skill 绑定：trace_upsert 是校准 skill 专属收尾；尽力而为，不阻断）
    hints: dict = {}
    try:
        hints = await asyncio.to_thread(_trace_input_hints, pdir, trace_key, record)
    except Exception:   # noqa: BLE001
        hints = {}
    return _base_receipt(
        "calibration.trace_upsert", ok=True,
        target=f"{run_id}/{trace_key}", verified=True, unchanged=False,
        run_id=run_id, trace_key=trace_key,
        archived_to=archived_to, updated_at=merged.get("updated_at"),
        fields_written=sorted(record.keys()),
        **hints,
        note=("trace 已记录（合并语义）"
              + (f"；旧 attempt 已归档为 {archived_to}（历史不覆盖）" if archived_to
                 else "；无历史归档（archive_existing=true 且存在已裁定旧记录时才归档）")
              + "。trace 是校准验收对象：确认记录里包含裁定理由与 repair 决策链全文。"
              + ("；⚠️ 见回执中的警告/提醒字段。" if hints else "")))


TOOL_EXECUTORS = {
    "calibration.seam_measure": _execute_seam_measure,
    "calibration.run_create": _execute_run_create,
    "calibration.trace_read": _execute_trace_read,
    "calibration.trace_upsert": _execute_trace_upsert,
}
