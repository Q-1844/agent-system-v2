# src/agents/scene_designer.py
# Scene Agent（Scene Production Designer）— Agent System V2 Phase C2（28.md）。
#
# 身份（28.md §0.1）：第四个正式 Specialist。内部稳定 id = scene_designer；
# 用户可见名 = Scene Agent（场景设计 / Scene Production Designer）。
# 与 Director 内部 Scene Sub-Agent 严格区分（§0.2）：后者仍只做单故事段的导演/分镜
# 创作提案，不拥有 Scene System canonical；本 Agent 负责【场景生产事实与场景视觉资产】。
#
# Canonical ownership（28.md §0.3）：
#   拥有 Scene System 全部写入：Location / SpatialAnchor / SetLayout / LocationView /
#   SceneInstance / SceneState / SceneInstanceView（output/scene_system.json）。
#   全部写入经 SceneSystemService / SceneVisualService / review+approval service，
#   绝不直接写 JSON，绝不创建第二套 scene canonical。
#
# 硬边界（§0.4-§0.7，物理保证非提示词约束）：
#   - 不写 Story（Script Writer 域）；Story 不足/矛盾 → 报告问题，不偷改。
#   - 不写 Character / Clothing / Look / Prop / Composition（Resource Agent 域）；
#     可读 approved Props 供 SceneState.prop_placements 引用；缺 Prop → resource_prerequisite。
#   - 不写 Shot / ShotProductionBinding（Director 域）；只读 shot 用于按镜头需求备视角。
#   - 不写 ProjectVisualProfile（用户拥有）；全部视觉生成必须消费当前 profile。
#   - 不直接调用 AgnesClient 生成（全部经 SceneVisualService 正式链路）。
#
# 复用纪律（28.md §3）：native tool-loop / SSE / Thinking / session / work memory /
# OCC / receipts / zero-write recovery / timeout recovery / repeat-failure discipline /
# Web Search / resource lock / Producer Bridge 全部沿用既有 Specialist Runtime 结构。
# Script Writer 共享基础设施直接 import 复用（同 character_designer 惯例）；tool-loop
# 机制按场景域镜像实现——不改冻结的 script_writer.py / character_designer.py。
from __future__ import annotations
import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, Optional

import httpx

from src.core.store import ProjectStore

from .models import AgentMessage, ResultStatus
from .project_events import RESOURCE_SCENE_SYSTEM
from .runtime import AgentRuntime, AgentError
# —— Script Writer 共享基础设施（直接 import 复用，不改其文件）——
from .script_writer import (
    MAX_WEB_SEARCH,
    _TurnStopped,
    _base_receipt,
    _claims_search_success,
    _claims_success,
    _execute_script_read,
    _execute_story_list,
    _execute_story_read,
    _execute_web_search,
    _is_chat_turn,
    _is_explicit_write_request,
    _loose_json,
    _normalized_search_query,
    _parse_args,
    strip_reserved_fact_blocks,
)

log = logging.getLogger("agents.scene_designer")

# 执行窗口（renewable window，同 Resource Agent 惯例：次数不限，防单窗口失控）。
DEFAULT_MAX_TOOL_STEPS = 1024
# 真相收口：最终回复纠偏最多 1 次；仍不实则确定性诚实覆写。
MAX_CORRECTIVE_ROUNDS = 1
# 防失控：连续同一工具同一失败签名达此阈值 → 诚实停止。
MAX_REPEAT_FAIL = 3
# LLM read timeout 恢复次数（场景域无批量降级需求）。
MAX_TIMEOUT_RETRIES = 2

# 只读工具（不占写槽、可一次多个；用于「写后重读最终状态」的收尾判定）。
SCENE_READ_TOOLS = {
    "story.list", "story.read", "script.read",
    "project.visual_profile.read",
    "shot.list", "shot.read",
    "web.search",
    # C 域只读（scene.inventory / scene.production.inspect 在 Commit C 加入）
}

# 写工具 → 资源锁域（唯一权威映射；流式端点与 Producer Bridge 的锁接线都从这张表取）。
# Scene Agent 单写域：全部写工具锁 scene_system 域（§5 不锁 storyboard/resources/character/
# 整个项目）。写工具在 Commit D/E/F/G 逐个落地时加入本表。
WRITE_TOOL_DOMAIN: Dict[str, str] = {}


# ============ system ============
def _visual_profile_summary_line(pdir) -> str:
    """system 注入的最小 profile 摘要（与 production_context.profile_summary_line 同源，
    避免 agents 内部环 import——此处独立实现同语义紧凑版）。"""
    try:
        from src.visual_profile import load_profile_or_none
        vp = load_profile_or_none(pdir)
    except Exception:
        return "读取失败（project_profile.json 损坏？）"
    if vp is None:
        return ("未设置（unspecified）。注意：LocationView/SceneInstanceView 的风格锚定"
                "将缺失，建议提醒用户在项目设置里配置")
    tags = ", ".join(vp.style_tags[:6]) if vp.style_tags else "-"
    neg = "; ".join(vp.negative_style_constraints[:4]) \
        if vp.negative_style_constraints else "-"
    return (f"render_family={vp.render_family}；art_direction={(vp.art_direction or '-')[:80]}；"
            f"style_tags={tags}；负面约束={neg}。全文用 project.visual_profile.read")


def _build_system(proj, pdir=None) -> str:
    """精简 system：职责/概念层次/工具说明/项目元信息。不注入剧本/场景全文——按需用工具取。"""
    vp_summary = "（未知）"
    if pdir is not None:
        vp_summary = _visual_profile_summary_line(pdir)
    return SCENE_DESIGNER_SYSTEM.format(
        project_name=proj.name or "（未命名）",
        story_len=len(proj.story_text or ""),
        scenes_count=len(proj.scenes or []),
        characters_count=len(proj.characters or []),
        visual_profile_summary=vp_summary,
    )


SCENE_DESIGNER_SYSTEM = """你是 AGNES 的 Scene Agent（Scene Production Designer，场景设计师）——动画项目的场景生产负责人。你的工作不是写剧情，也不是设计 Shot，而是把故事中的地点和场景状态转化为可供导演稳定使用的【生产场景系统】与 approved 场景视觉参考。

你【只写你的域】：Scene System（Location / SpatialAnchor / SetLayout / LocationView / SceneInstance / SceneState / SceneInstanceView）。不修改剧本正文、不增删改故事段、不创建/修改镜头、不写角色/服装/造型/道具、不写项目视觉定义。

项目：{project_name}（剧本正文 {story_len} 字，{scenes_count} 个故事段，现有 {characters_count} 个角色）。
项目视觉定义（ProjectVisualProfile——全项目唯一风格真相，你只能读不能改）：{visual_profile_summary}

【你必须理解的概念层次（做错任何一个都是方向性错误）】
- Story Scene（故事段）= 剧情事实：发生了什么、在哪里、何时、天气/环境。来自 Script Writer，【不归你修改】。Story Scene 信息不足或互相矛盾时：如实报告问题，绝不偷偷改故事；需要改剧情时向 Producer/用户说明，交回 Script Writer。
- Location = 地点长期身份（「这是哪里」）：可跨多个 Story Scene 复用的稳定地点。Location 名是「山雨驿站」，不是「s03 暴雨中的山雨驿站」——暴雨属于 SceneState。
- SpatialAnchor / SetLayout = 这个 Location 的稳定空间结构：多视角识别、连续性、机位规划依赖的结构性锚点（east gate / west chimney / north stair / bridge arch / well 这类比「墙上一块小污渍」有价值得多）。
- LocationView = Location 从特定相机位置/方向看起来的样子：生产视觉资产（空场景、中性状态）。Location ≠ background image；LocationView ≠ Location。
- SceneInstance = 某个 Story Scene 发生在某个 Location（一个 Story Scene 至多一个 active instance）。
- SceneState = 这个地点在这一场戏的此刻状态：时间/天气/灯光/环境条件/anchor 状态/prop 摆放。SceneState ≠ Shot action（镜头内的动作过程属于 Shot/Video，不写进 SceneState）。
- SceneInstanceView = LocationView + SceneState = 导演真正可以绑定到 Shot 的场景参考。LocationView ≠ SceneInstanceView；Story Scene ≠ SceneInstance。

【不做混淆的铁律】
- 你不是 Director：不设计 Shot、不决定怎么拍；你负责「场景准备好」，导演负责「怎么拍这个场景」。
- 你不是 Resource Agent：不创建/修改任何 Prop。场景需要某道具而 Resources 不存在时：用结构化 resource_prerequisite 如实报告（story_scene_id + 缺什么 + 为什么需要 + 目标字段），由 Producer 委派 Resource Agent——绝不自己造 Prop、绝不伪造引用。
- approved 场景图 ≠ 剧情事实：参考图里画了什么不改变剧情；不要为了让图好过而改 Story 或 SceneState。
- 你没有写 Story / Character / Prop / Shot / Binding / ProjectVisualProfile 的工具：任何情况下都不要试图改它们来配合场景。

【LocationView 机位策略（诚实机位）】
- Location hero 不是最终所有视角。先建立稳定 hero / identity view，再按【真实生产需要】（Director 镜头需求、Story 地理、连续性、用户要求）创建必要视角。不要默认每个 Location 生成 8 个视角，不要为凑数把 front/back/left/right/45° 全生成。
- 一次 scene.view.prepare / scene.state_view.prepare 调用 = 一次正式生成尝试（生成约 1 分钟，耐心等真实返回）。同一机位+同一语义输入连续审查失败会被 review_retry_exhausted 拦住——必须先真正改变输入（换 camera_zone / 改 visible anchors / 改描述），不要原样重试烧图。
- 审查会严格检查机位正确性（相机位置先于 anchor 可见性）：声明 side/rear/overhead 的图若实际是正面 hero 构图会被 needs_fix。审查结论是证据：needs_fix/uncertain 绝不能当作已批准。模型确实做不到某机位时：如实报告模型边界（MODEL_LIMITATION），绝不降低标准。

【审批分档（approval_mode 的选择纪律）】
- 用户/Producer 要求「准备成可以正式拍分镜」「production-ready」「把场景准备完整」→ 用 approve_if_pass（前提：正式 vision 审查真实 pass；审查没过绝不批准）。
- 用户说「先看看这个场景设计」「生成几个候选」「先探索一下」→ 必须用 review_only，绝不自动批准。
- 撤销已批准场景资产是高风险用户决策：你没有 revoke 工具，由用户在 UI 显式操作；用户要撤时如实引导。

【Prop 摆放纪律】
- SceneState.prop_placements 必须引用真实存在的 Resources Prop（先读 resource 侧确认 id）。引用不存在的 Prop 会被系统拒绝（prop_not_found）——这时按 resource_prerequisite 如实报告，不要编造 prop id。

【读取策略：最小充分上下文，按需扩大】
- 场景准备任务的第一步：scene.production.inspect（看该故事段场景生产现状）或 scene.inventory（全局地图）。已可复用的 Location/View 一律复用，不重复创建——「同一地点不重复创建」是硬要求。
- 需要理解剧情才建场景：story.list 全局地图 → story.read 只读相关故事段。镜头需要特定视角时：shot.list / shot.read 看实际镜头需求（只读，绝不改镜头）。

【写工具必须一个一个来】（重要）：
- 所有 scene.* 写工具必须【一个一个执行】：每发一个写工具调用，等它返回最新 revision/状态后，再发下一个。同一次回复里发了多个写工具时，系统只执行第 1 个，其余会被拒绝（会用旧 revision 导致 stale）。
- 只读工具（story.list / story.read / project.visual_profile.read / shot.list / shot.read / scene.inventory / scene.production.inspect / web.search）可以一次发多个。

【工作方式】
1. 新场景准备（「把这一场的场景准备好」）：scene.production.inspect（现状）→ 缺 Location 则 scene.location.upsert 创建（复用优先！）→ scene.anchor.upsert 建结构性锚点 → 需要时 scene.layout.set → scene.view.prepare 出 hero view（审查+按分档批准）→ 按生产需要补关键视角 → scene.instance.prepare 绑定故事段 → scene.state.update 按剧情推导状态 → scene.state_view.prepare 出场景状态图（审查+批准）→ 最后 scene.production.inspect 核对 READY 并如实汇报。
2. 同一 Location 多个故事段不同状态：Location/anchors/views 全部复用；每个故事段一个 SceneInstance + 各自 SceneState + 各自 SceneInstanceView。
3. 用户要求探索：review_only，只生成不批准。
4. 任一写工具返回 stale=true：你读取后内容被改动过、未写入；用返回的最新 revision 重新判断后重试；连续 stale 说明有并发写入者，先 scene.inventory 看清现状。
5. 一次只做用户要求的修改，不顺手改无关场景。

【聊天与探讨（用户没让你改东西时）】
- 用户在提问、讨论场景设计时：直接自然语言讨论，不调用写工具；讨论中的想法【不要自动执行】。回答需要事实依据时按需调用只读工具；凭上下文能答的直接答。聊天轮【不自动搜索】。

要求：
- 汇报只依据工具【真实返回】：没看到 ok=true 就不能声称成功；审查没过绝不能说「已批准/已可用」；不要编造 id、revision、数量。
- 你向用户汇报用【中文】，只汇报本轮真实执行的操作。
"""


# ============ 工具 schema（读工具复用，不发明第二套）============
# story.list / story.read / script.read schema 从 Script Writer 过滤复用；
# project.visual_profile.read schema 从 Resource Agent 过滤复用；
# shot.list / shot.read schema 从 Director 过滤复用（director 模块级 import
# character_designer，本文件若模块级反向 import director 会成环——turn 时已完全加载，
# lazy 安全）。web.search schema 从 character_designer 复用（同一 schema 惯例）。


def _tool_surface() -> list:
    """Scene Agent 实际下发给 LLM 的工具面（turn 开始时构建一次）：
    本模块 scene.* 工具 + 复用的只读 schema（story/profile/shot/web）。
    director/character_designer 不可加载时降级基础面（Agent 仍可用）。"""
    tools: list = list(SCENE_DESIGNER_TOOLS)
    try:
        from .script_writer import SCRIPT_WRITER_TOOLS
        sw_names = {"story.list", "story.read", "script.read"}
        tools += [t for t in SCRIPT_WRITER_TOOLS if t["function"]["name"] in sw_names]
    except Exception:   # noqa: BLE001 - 防御：partial-import 降级
        pass
    try:
        from .character_designer import VISUAL_PROFILE_READ_TOOL, WEB_SEARCH_TOOL
        tools += [VISUAL_PROFILE_READ_TOOL, WEB_SEARCH_TOOL]
    except Exception:   # noqa: BLE001
        pass
    try:
        from .director import DIRECTOR_TOOLS
        names = {"shot.list", "shot.read"}
        tools += [t for t in DIRECTOR_TOOLS if t["function"]["name"] in names]
    except Exception:   # noqa: BLE001
        pass
    return tools


def _execute_shot_list_via_director(rt: AgentRuntime, project: str, arguments: str,
                                    instruction, message_id) -> dict:
    """shot.list 只读执行器（复用 Director 实现；lazy import 防环）。"""
    from .director import _execute_shot_list
    return _execute_shot_list(rt, project, arguments, instruction, message_id)


def _execute_shot_read_via_director(rt: AgentRuntime, project: str, arguments: str,
                                    instruction, message_id) -> dict:
    """shot.read 只读执行器（复用 Director 实现；lazy import 防环）。"""
    from .director import _execute_shot_read
    return _execute_shot_read(rt, project, arguments, instruction, message_id)


def _execute_visual_profile_read_via_cd(rt: AgentRuntime, project: str, arguments: str,
                                        instruction, message_id) -> dict:
    """project.visual_profile.read 只读执行器（复用 Resource Agent 实现；只读无写权）。"""
    from .character_designer import _execute_visual_profile_read
    return _execute_visual_profile_read(rt, project, arguments, instruction, message_id)


def _execute_web_search_via_sw(rt: AgentRuntime, project: str, arguments: str,
                               instruction, message_id) -> dict:
    """web.search 只读执行器（复用 Script Writer 实现；预算由本 loop 维护）。"""
    return _execute_web_search(rt, project, arguments, instruction, message_id)


# ============ 工具执行分派 ============
def _resolve_pdir(rt: AgentRuntime, project: str):
    return rt._resolve_project_dir(project)


TOOL_EXECUTORS: Dict[str, Any] = {
    "story.list": _execute_story_list,
    "story.read": _execute_story_read,
    "script.read": _execute_script_read,
    "shot.list": _execute_shot_list_via_director,
    "shot.read": _execute_shot_read_via_director,
    "project.visual_profile.read": _execute_visual_profile_read_via_cd,
    "web.search": _execute_web_search_via_sw,
}

TOOL_LABELS = {
    "story.list": "列出故事段", "story.read": "读取故事段", "script.read": "读取剧本正文",
    "shot.list": "列出镜头", "shot.read": "读取镜头",
    "project.visual_profile.read": "读取项目视觉定义",
    "web.search": "搜索资料",
}


def _tool_label(name: str) -> str:
    return TOOL_LABELS.get(name, name)


def _op_human(r: dict) -> str:
    """回执 → 人读摘要（写工具回执的 op_summary 优先）。"""
    if r.get("op_summary"):
        return str(r["op_summary"])
    if r.get("ok"):
        return f"{r.get('operation', '?')} 完成"
    return f"{r.get('operation', '?')} 失败：{r.get('error') or r.get('code') or '失败'}"


def _tool_summary(name: str, obs: dict) -> str:
    if obs.get("ok"):
        if obs.get("summary"):
            return str(obs["summary"])[:120]
        if obs.get("op_summary"):
            return str(obs["op_summary"])[:120]
        return f"{_tool_label(name)}完成"
    return f"{_tool_label(name)}失败：{obs.get('error') or obs.get('code') or '失败'}"


def _trace_entry(step: int, name: str, args: str, obs: dict) -> dict:
    return {"step": step, "tool": name, "args": (args or "")[:200], "ok": bool(obs.get("ok")),
            "summary": _tool_summary(name, obs)}


def _ledger_brief(receipts: list) -> str:
    """真实回执账本摘要（纠偏 nudge 用；只陈述事实）。"""
    if not receipts:
        return "本轮没有任何写工具真实成功。"
    return "；".join(f"{r.get('operation', '?')}→{('成功' if r.get('ok') and r.get('verified') else '未成功')}"
                    for r in receipts[-8:])


def _honest_fallback(receipts: list) -> str:
    """真相闸门纠偏次数用尽后的确定性诚实覆写（非模型文本）。"""
    real = [r for r in receipts if r.get("ok") and r.get("verified") and not r.get("unchanged")]
    if not real:
        return "本轮没有已确认成功的场景系统修改。"
    return "本轮已确认的场景系统修改：" + "；".join(
        f"{r.get('operation')}（{_op_human(r)}）" for r in real)


def _refresh_system(messages: list, rt: AgentRuntime, project: str) -> None:
    """每步工具调用后刷新 system（项目元信息/profile 摘要）。镜像 character_designer。"""
    try:
        pdir = rt._resolve_project_dir(project)
        proj = ProjectStore(pdir).load()
        sys_text = _build_system(proj, pdir)
        if messages and messages[0].get("role") == "system":
            messages[0] = {"role": "system", "content": sys_text}
        else:
            messages.insert(0, {"role": "system", "content": sys_text})
    except Exception:
        pass


# ============ C 域工具 schema 与执行器（后续 Commit 逐个落地）============
# scene.inventory / scene.production.inspect（Commit C）
# scene.location.upsert / scene.anchor.upsert / scene.layout.set（Commit D）
# scene.view.prepare（Commit E，含共享 review retry guard）
# scene.instance.prepare / scene.state.update（Commit F）
# scene.state_view.prepare（Commit G）
SCENE_DESIGNER_TOOLS: list = []


# ============ tool-loop（镜像 character_designer 结构，场景域语义）============
async def _run_tool_loop(rt: AgentRuntime, client, project: str,
                         messages: list, instruction: str,
                         message_id: Optional[str],
                         max_steps: int = DEFAULT_MAX_TOOL_STEPS,
                         thinking: bool = False,
                         event_cb=None,
                         state: Optional[dict] = None,
                         cancel_event=None,
                         tool_surface: Optional[list] = None) -> dict:
    """原生 function-calling 循环 + 执行真实性收口（结构性，非提示词）。
    场景域语义：单写串行 / 回执账本 / zero-write recovery / 重复失败护栏 /
    超时恢复 / 搜索预算与去重 / renewable window / 真相闸门（场景版事实摘要）。"""
    scene_mutated = False
    if state is None:
        state = {}
    events: list = state.setdefault("events", [])
    receipts: list = state.setdefault("receipts", [])
    write_attempts = 0
    corrective_round = 0
    zero_write_recovery_used = False
    trace: list = state.setdefault("trace", [])
    reasonings: list = state.setdefault("reasonings", [])
    tools_offered = tool_surface if tool_surface is not None else _tool_surface()
    last_read_after_write = -1
    search_count = 0
    search_sources: list = state.setdefault("search_sources", [])
    search_queries_seen: set = state.setdefault("search_queries_seen", set())
    repeat_fail_streak = 0
    last_fail_sig = None
    timeout_retries = 0

    def _on_read_timeout() -> bool:
        nonlocal timeout_retries
        if timeout_retries >= MAX_TIMEOUT_RETRIES:
            return False
        timeout_retries += 1
        nudge = (
            f"[系统恢复提示 MODEL_READ_TIMEOUT] 上一次模型响应超时被中断"
            f"（read timeout，第 {timeout_retries}/{MAX_TIMEOUT_RETRIES} 次）。"
            f"本次没有形成完整工具调用、没有新增任何提交。"
            f"请【只继续尚未完成的部分，不要重做已提交项】。"
        )
        messages.append({"role": "user", "content": nudge})
        _refresh_system(messages, rt, project)
        return True

    step = -1
    window_used = 0
    window_renewals = 0
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise _TurnStopped()
        # ---- 执行窗口边界：self-check + 续期（renewable window，次数不限）----
        if window_used >= max_steps:
            window_renewals += 1
            window_used = 0
            timeout_retries = 0
            messages.append({"role": "user", "content": _WINDOW_SELF_CHECK_NUDGE})
            _refresh_system(messages, rt, project)
            log.info("scene_designer tool-loop 执行窗口续期（project=%s renewals=%s）",
                     project, window_renewals)
        step += 1
        window_used += 1
        if cancel_event is not None and cancel_event.is_set():
            raise _TurnStopped()
        content = None
        tool_calls = None
        try:
            if event_cb:
                step_reasoning_started = False
                step_reason_t0 = 0.0
                step_reasoning_buf: list[str] = []
                step_content_buf: list[str] = []
                async for ev in client.chat_completion_stream(
                        messages, temperature=0.7, max_tokens=65536,
                        thinking=thinking, tools=tools_offered,
                        tool_choice="auto"):
                    k = ev["kind"]
                    if k == "content_delta":
                        step_content_buf.append(ev["delta"])
                    elif k == "reasoning_delta":
                        if not step_reasoning_started:
                            step_reasoning_started = True
                            step_reason_t0 = time.perf_counter()
                            event_cb({"type": "reasoning.started", "step_id": step})
                        step_reasoning_buf.append(ev["delta"])
                        event_cb({"type": "reasoning.delta", "step_id": step,
                                  "delta": ev["delta"]})
                    elif k == "tool_call_pending":
                        event_cb({"type": "tool.pending", "step_id": step,
                                  "name": ev["name"], "label": _tool_label(ev["name"])})
                    elif k == "finish":
                        content = ev["content"]
                        tool_calls = ev["tool_calls"]
                if step_reasoning_started:
                    rtext = "".join(step_reasoning_buf)
                    rdur = int((time.perf_counter() - step_reason_t0) * 1000)
                    event_cb({"type": "reasoning.completed", "step_id": step,
                              "duration_ms": rdur})
                    if rtext:
                        reasonings.append({"step": step, "text": rtext, "duration_ms": rdur})
                if not tool_calls and step_content_buf:
                    for d in step_content_buf:
                        event_cb({"type": "assistant.delta", "delta": d})
            else:
                resp = await client.chat_completion(
                    messages, temperature=0.7, max_tokens=65536,
                    tools=tools_offered, tool_choice="auto")
                tool_calls = resp.get("tool_calls")
                content = resp.get("content")
        except httpx.ReadTimeout:
            if not _on_read_timeout():
                raise
            continue

        if not tool_calls:
            text = (content or "").strip()
            # ---- 真相闸门（场景域）----
            real_changes = [r for r in receipts
                            if r.get("ok") and r.get("verified") and not r.get("unchanged")]
            chat_turn = (write_attempts == 0 and _is_chat_turn(instruction))
            false_claim = _claims_success(text) and not real_changes and not chat_turn
            real_searches = [t for t in trace if t["tool"] == "web.search" and t.get("ok")]
            search_false_claim = (_claims_search_success(text) and not real_searches
                                  and not chat_turn)
            last_write_step = max((t["step"] for t in trace
                                   if t["tool"] in WRITE_TOOL_DOMAIN), default=-1)
            reply_summarizes = bool(_SCENE_SUMMARY_RE.search(text or ""))
            needs_final_read = (real_changes and _claims_success(text)
                                and reply_summarizes
                                and last_read_after_write < last_write_step)
            if (not zero_write_recovery_used
                    and write_attempts == 0 and not real_changes
                    and _is_explicit_write_request(instruction)):
                zero_write_recovery_used = True
                log.info("scene_designer zero-write recovery 触发（project=%s）", project)
                messages.append({"role": "assistant", "content": text or ""})
                messages.append({"role": "user", "content": _ZERO_WRITE_RECOVERY_NUDGE})
                _refresh_system(messages, rt, project)
                continue
            if false_claim or search_false_claim or needs_final_read:
                if corrective_round < MAX_CORRECTIVE_ROUNDS:
                    corrective_round += 1
                    messages.append({"role": "assistant", "content": text or ""})
                    brief = _ledger_brief(receipts)
                    if search_false_claim:
                        nudge = (
                            "你上面的回复声称做过搜索/给出了搜索结果，但本轮【没有任何一次真实成功的 "
                            "web.search 工具调用】。请要么现在真正调用 web.search（每轮最多 3 次），"
                            "要么如实告诉用户：本轮没有搜索，或搜索失败、资料不足。")
                    elif needs_final_read:
                        nudge = (
                            "你做了一些修改并声称完成，但最后一次真实读取发生在最后一次写入【之前】。"
                            "收尾前请先用 scene.inventory / scene.production.inspect 读取最新"
                            "场景系统状态，然后【以这次读取的真实结果】总结你完成了什么。")
                    elif write_attempts == 0:
                        nudge = (
                            "你上面声称已经创建/修改了场景系统，但本轮你【根本没有发起对应的"
                            "工具调用】。光用文字说「已完成」不会改动任何东西。\n"
                            + brief + "\n请要么真正调用对应 scene.* 写工具完成修改，"
                            "要么如实告诉用户你没有改动、需要怎么做。")
                    else:
                        nudge = (
                            "你上面声称已经完成，但工具的真实返回显示没有任何一次写入成功确认：\n"
                            + brief + "\n请只依据上面真实的工具返回结果回答。可以用最新数据重新调用"
                            "工具重试；如果不打算重试，就如实告诉用户这次没有改动成功。")
                    messages.append({"role": "user", "content": nudge})
                    _refresh_system(messages, rt, project)
                    continue
                text = _honest_fallback(receipts)
            text = strip_reserved_fact_blocks(text)
            return {"text": text or "已完成。",
                    "scene_mutated": scene_mutated,
                    "events": events, "receipts": receipts, "trace": trace,
                    "reasonings": reasonings, "search_sources": search_sources,
                    "window_renewals": window_renewals}

        messages.append({"role": "assistant", "content": content or "",
                         "tool_calls": tool_calls})

        write_done_this_step = False   # 单写串行：一步只执行一个写操作
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            args = fn.get("arguments") or "{}"
            call_id = tc.get("id") or name

            if event_cb:
                event_cb({"type": "tool.started", "step_id": step, "call_id": call_id,
                          "name": name, "label": _tool_label(name)})

            # 写工具判定与账本分类同谓词（scene. 前缀且非只读）——串行闸门随工具
            # 逐个落地始终生效，不等 WRITE_TOOL_DOMAIN 齐备。
            is_write_tool = (name.startswith("scene.")
                             and name not in SCENE_READ_TOOLS)
            if is_write_tool and write_done_this_step:
                obs = {
                    "ok": False, "operation": name,
                    "error": "本步已执行过一个写操作。写工具必须一个一个来：等前一个完成、"
                             "拿到返回的最新 revision/状态后，再发下一个写工具调用。"
                             "不要在同一次回复里批量发多个写工具（会用旧 revision 导致 stale）。"
                             "只读工具（story.list/story.read/project.visual_profile.read/"
                             "shot.list/shot.read/scene.inventory/scene.production.inspect/"
                             "web.search）可以一次多个。",
                }
                messages.append({"role": "tool", "tool_call_id": call_id,
                                 "content": json.dumps(obs, ensure_ascii=False)})
                trace.append(_trace_entry(step, name, args, obs))
                if event_cb:
                    event_cb({"type": "tool.completed", "step_id": step,
                              "call_id": call_id, "name": name, "ok": False,
                              "summary": "本步写工具串行，已跳过"})
                continue

            executor = TOOL_EXECUTORS.get(name)
            exc_msg = None
            if (executor is not None and name == "web.search"
                    and search_count >= MAX_WEB_SEARCH):
                obs = {"ok": False, "operation": "web.search",
                       "code": "search_budget_exhausted",
                       "error": ("本轮搜索次数已达上限（3 次）。请基于已有信息与已获取的"
                                 "搜索结果完成任务，或如实告知用户资料不足。")}
            elif (executor is not None and name == "web.search"
                    and _normalized_search_query(args)
                    and _normalized_search_query(args) in search_queries_seen):
                obs = {"ok": False, "operation": "web.search",
                       "code": "duplicate_query",
                       "error": ("本 turn 已用完全相同的查询词搜索过。请换一个更具体或不同角度的"
                                 "查询词，或基于已获取的结果继续，不要重发同一查询。")}
            elif executor is None:
                obs = {"ok": False, "error": f"未知工具：{name}", "operation": name}
            else:
                if name == "web.search":
                    search_count += 1
                    _q_norm = _normalized_search_query(args)
                    if _q_norm:
                        search_queries_seen.add(_q_norm)
                try:
                    # 长任务 executor（视觉生成准备类）需要流式进度与协作取消——
                    # 以 executor 属性 _long_task=True 标注。
                    if getattr(executor, "_long_task", False):
                        obs = await executor(rt, project, args, instruction, message_id,
                                             event_cb=event_cb, cancel_event=cancel_event)
                    elif asyncio.iscoroutinefunction(executor):
                        obs = await executor(rt, project, args, instruction, message_id)
                    else:
                        obs = executor(rt, project, args, instruction, message_id)
                except AgentError as e:
                    exc_msg = e.message
                    obs = {"ok": False, "error": e.message, "code": e.code,
                           "operation": name}
                except Exception as e:
                    exc_msg = f"工具执行异常：{str(e)[:200]}"
                    obs = {"ok": False, "error": exc_msg,
                           "operation": name}

            ok_now = bool(obs.get("ok"))
            if ok_now:
                repeat_fail_streak = 0
                last_fail_sig = None
            else:
                sig = (name, obs.get("code") or obs.get("error") or "err")
                if sig == last_fail_sig:
                    repeat_fail_streak += 1
                else:
                    repeat_fail_streak = 1
                    last_fail_sig = sig
                if repeat_fail_streak >= MAX_REPEAT_FAIL:
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "content": json.dumps(obs, ensure_ascii=False)})
                    _refresh_system(messages, rt, project)
                    nudge = (f"「{name}」已连续 {repeat_fail_streak} 次以同样方式失败"
                             f"（{obs.get('error') or obs.get('code')}）。继续重复同样调用不会成功。"
                             "请改用只读工具（scene.inventory / scene.production.inspect）确认当前"
                             "真实状态，换个参数或换种做法，或如实告诉用户当前遇到的问题、"
                             "哪些已成功完成。")
                    messages.append({"role": "assistant", "content": content or ""})
                    messages.append({"role": "user", "content": nudge})
                    trace.append(_trace_entry(step, name, args, obs))
                    _refresh_system(messages, rt, project)
                    if event_cb:
                        if exc_msg:
                            event_cb({"type": "tool.failed", "step_id": step, "call_id": call_id,
                                      "name": name, "error": exc_msg})
                        else:
                            event_cb({"type": "tool.completed", "step_id": step,
                                      "call_id": call_id, "name": name, "ok": False,
                                      "summary": _tool_summary(name, obs)})
                    break

            # 写域调用记入账本（含拼错的 scene.* 未知工具：也算写尝试失败；
            # SCENE_READ_TOOLS 与 WRITE_TOOL_DOMAIN 是账本分类的唯一权威）。
            is_write_domain = (name.startswith("scene.")
                               and name not in SCENE_READ_TOOLS)
            if is_write_domain:
                write_attempts += 1
                obs.setdefault("operation", name)
                receipts.append(obs)
                write_done_this_step = True
            else:
                if ok_now and name != "web.search":
                    last_read_after_write = step
                if name == "web.search" and obs.get("ok") and obs.get("sources"):
                    for s in obs["sources"]:
                        if s and (s.get("url") or s.get("title")) \
                                and s not in search_sources:
                            search_sources.append({"title": s.get("title") or "",
                                                   "url": s.get("url") or ""})

            # 据【真实回执】设置 mutated 标志 + 事件。
            if (name in WRITE_TOOL_DOMAIN and obs.get("ok")
                    and obs.get("verified") and not obs.get("unchanged")):
                scene_mutated = True
                events.append({"kind": "scene_change",
                               "summary": obs.get("op_summary") or f"场景系统变更（{name}）"})

            if event_cb:
                if exc_msg:
                    event_cb({"type": "tool.failed", "step_id": step, "call_id": call_id,
                              "name": name, "error": exc_msg})
                else:
                    event_cb({"type": "tool.completed", "step_id": step,
                              "call_id": call_id, "name": name, "ok": bool(obs.get("ok")),
                              "summary": _tool_summary(name, obs)})
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": json.dumps(obs, ensure_ascii=False)})
            trace.append(_trace_entry(step, name, args, obs))

        _refresh_system(messages, rt, project)


# 镜像 character_designer 的窗口 nudge（每文件一份镜像，仓库惯例）。
_WINDOW_SELF_CHECK_NUDGE = (
    "【系统提示·执行窗口】你已经连续执行了较长时间（当前执行窗口已用满）。请检查"
    "当前用户目标与最新的工具返回结果，然后自行判断：\n"
    "① 如果任务已经完成，正常结束并汇报结果即可；\n"
    "② 如果你确认自己正在重复相同的操作、没有取得任何新进展，并且已经没有合理的"
    "新方向，可以停止并如实说明卡点；\n"
    "③ 如果任务尚未完成且仍有明确、合理的下一步，请继续执行——不要因为执行步数"
    "而停止，系统会自动为你续接新的执行窗口。"
)

_ZERO_WRITE_RECOVERY_NUDGE = (
    "【系统提示·零写入恢复】你的任务看起来要求对场景系统做实际修改，但你至今没有"
    "发起过任何 scene.* 写工具调用。请：①先用只读工具（scene.inventory / "
    "scene.production.inspect）确认当前真实状态；②若确有需要修改之处，用对应 scene.* "
    "写工具真正执行；③若你判断没有值得修改的问题（例如场景已可复用、已 READY），"
    "如实地向用户说明结论与依据。不要凭空声称已完成。"
)

# 回复中像在「总结场景系统现状/成果」的正则（needs_final_read 收尾判定用）。
_SCENE_SUMMARY_RE = re.compile(
    r"\d+\s*个[地点场景视 角]|场景系统|Location|LocationView|SceneInstance|场景参考|已就绪|READY")


# ============ 主 turn ============
async def scene_designer_turn(rt: AgentRuntime, project: str, session_id: str,
                              content: str, *, thinking: bool = False,
                              event_cb=None, cancel_event=None) -> dict:
    """Scene Agent 原生 tool-loop turn（镜像 character_designer_turn 契约）。

    返回 {assistant_message, scene_mutated, events, message, intent, receipts, trace,
    search_sources, session_id, agent_id}。thinking/event_cb 仅流式端点传入；
    cancel_event 协作式停止（步骤边界安全中断）。"""
    store = rt._session_store(project)
    session = store.load(session_id)
    if session is None:
        raise AgentError("unknown_scope", f"session 不存在: {session_id}")
    if session.agent_id != "scene_designer":
        raise AgentError("permission_denied", "该会话仅限 Scene Agent")
    if not rt._is_enabled("scene_designer"):
        raise AgentError("disabled_agent", "Scene Agent 已禁用")

    user_msg = AgentMessage(role="user", content=content)
    store.append_message(session_id, user_msg)
    rt._maybe_capture_preference(project, content, user_msg.message_id)
    session = store.load(session_id)

    pdir = rt._resolve_project_dir(project)
    # 会话治理（best-effort，不影响主流程）：项目首个 ≥10 字符用户消息 → 创作 brief。
    from .session_governance import capture_brief_if_absent
    capture_brief_if_absent(pdir, project, content, "scene_designer")
    history = [{"role": m.role, "content": m.content}
               for m in session.messages[:-1]
               if m.role in ("user", "assistant") and m.content and m.status is None]

    client = rt._resolve_client_or_none(agent_id="scene_designer")
    assistant_text: str
    status: Optional[str] = None
    scene_mutated = False
    events: list = []
    receipts: list = []
    reasoning: list = []
    trace: list = []
    search_sources: list = []

    if client is None:
        assistant_text = ("[chat 未配置：Agnes 推理模型不可用，无法修改场景系统。"
                          "请在设置中配置 API Key。]")
        status = ResultStatus.NOT_CONFIGURED.value
    else:
        proj = ProjectStore(pdir).load()
        system = _build_system(proj, pdir)
        mem = rt._memory_supplement(project, "scene_designer")
        if mem:
            system += "\n\n" + mem
        messages: list[dict] = [{"role": "system", "content": system}]
        messages += history
        messages.append({"role": "user", "content": content})

        loop_state: dict = {}
        try:
            final = await _run_tool_loop(rt, client, project, messages, content,
                                         user_msg.message_id,
                                         thinking=thinking, event_cb=event_cb,
                                         state=loop_state, cancel_event=cancel_event,
                                         tool_surface=_tool_surface())
            assistant_text = final["text"]
            scene_mutated = final.get("scene_mutated", False)
            events = final["events"]
            receipts = final.get("receipts", [])
            trace = final.get("trace", [])
            reasoning = final.get("reasonings", [])
            search_sources = final.get("search_sources", [])
        except _TurnStopped:
            log.info("scene_designer_turn 被停止（project=%s session=%s）",
                     project, session_id)
            assistant_text = "[任务已停止] 已完成部分见工具时间线；其余未执行。"[:300]
            status = ResultStatus.STOPPED.value
            events = loop_state.get("events", [])
            receipts = loop_state.get("receipts", [])
            trace = loop_state.get("trace", [])
            reasoning = loop_state.get("reasonings", [])
            search_sources = loop_state.get("search_sources", [])
        except Exception as e:
            log.exception("scene_designer_turn 工具循环异常（project=%s session=%s）",
                          project, session_id)
            assistant_text = f"[执行失败: {type(e).__name__}: {str(e).strip()}]"[:300]
            status = ResultStatus.FAILED.value
            events = loop_state.get("events", [])
            receipts = loop_state.get("receipts", [])
            trace = loop_state.get("trace", [])
            reasoning = loop_state.get("reasonings", [])
            search_sources = loop_state.get("search_sources", [])

    intent = "scene_change" if scene_mutated else "conversation"

    # 回执 meta 透传（scene 域字段——Producer._derive_changed_refs 只从这些真实回执
    # 字段派生 scene changed_refs；与 Resource Agent 的 resources 域同纪律）。
    receipts_meta = []
    for r in receipts:
        receipts_meta.append({
            "operation": r.get("operation"), "ok": bool(r.get("ok")),
            "verified": bool(r.get("verified")), "stale": bool(r.get("stale")),
            "unchanged": bool(r.get("unchanged")),
            "error": r.get("error"),
            "op_summary": r.get("op_summary"),
            "revision": r.get("revision"),
            "expected_revision": r.get("expected_revision"),
            "current_revision": r.get("current_revision"),
            "document_revision": r.get("document_revision"),
            "document_revision_after": r.get("document_revision_after"),
            "expected_document_revision": r.get("expected_document_revision"),
            "current_document_revision": r.get("current_document_revision"),
            # scene 域实体 id（changed_refs 派生唯一来源）
            "location_id": r.get("location_id"),
            "anchor_id": r.get("anchor_id"),
            "layout_id": r.get("layout_id"),
            "view_id": r.get("view_id"),
            "scene_instance_id": r.get("scene_instance_id"),
            "scene_instance_view_id": r.get("scene_instance_view_id"),
            "story_scene_id": r.get("story_scene_id"),
            "changed_resources": r.get("changed_resources"),
            "prerequisite_warnings": r.get("prerequisite_warnings"),
            "elapsed_s": r.get("elapsed_s"),
        })
    meta = {"intent": intent, "events": events, "evidence_refs": [],
            "receipts": receipts_meta,
            "write_attempts": len(receipts_meta),
            "write_success": sum(1 for r in receipts_meta
                                 if r["ok"] and r["verified"] and not r["unchanged"]),
            "trace": trace,
            "reasonings": reasoning,
            "search_sources": search_sources,
            "pending_delete_scenes": []}
    asst = AgentMessage(role="assistant", content=assistant_text, status=status, meta=meta)
    store.append_message(session_id, asst)

    # turn 级结构化 prerequisite（真实 tool result 派生，Producer 据 §25 路由）。
    prerequisite_warnings: list = []
    for r in receipts_meta:
        for pw in (r.get("prerequisite_warnings") or []):
            if isinstance(pw, dict) and pw not in prerequisite_warnings:
                prerequisite_warnings.append(pw)

    return {"assistant_message": assistant_text,
            "scene_mutated": scene_mutated,
            "events": events, "message": asst.to_dict(),
            "intent": intent, "receipts": receipts_meta, "trace": trace,
            "search_sources": search_sources,
            "evidence_refs": [], "linked_task_ids": [],
            "linked_change_session_id": None, "session_id": session_id,
            "agent_id": "scene_designer",
            "prerequisite_warnings": prerequisite_warnings}
