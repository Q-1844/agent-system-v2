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
# —— Skill System V1（40.md）：生产技能工具面（只读，不占写槽）——
from .skill_system.tools import (
    SKILL_TOOLS,
    activation_hint as _skill_activation_hint,
    make_skill_executors,
)
from .skill_system.runtime import get_skill_runtime as _get_skill_runtime

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
    "scene.inventory", "scene.production.inspect",   # Commit C（28.md §8/§9）
    "resource.inventory", "resource.read",           # §0.6：Prop id 定位（只读）
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
    base = SCENE_DESIGNER_SYSTEM.format(
        project_name=proj.name or "（未命名）",
        story_len=len(proj.story_text or ""),
        scenes_count=len(proj.scenes or []),
        characters_count=len(proj.characters or []),
        visual_profile_summary=vp_summary,
    )
    # Skill System V1（40.md §七）：L1 catalog 常驻尾部（只看到自己的 Skill view）
    base += _get_skill_runtime().catalog_block("scene_designer")
    return base


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
        from .character_designer import (RESOURCE_INVENTORY_TOOL, RESOURCE_READ_TOOL,
                                         VISUAL_PROFILE_READ_TOOL, WEB_SEARCH_TOOL)
        # §0.6：Scene Agent 可读生产资源（定位 Prop id 供 prop_placements 引用）——
        # 只读复用 Resource Agent 的 inventory/read schema，无任何写工具。
        tools += [RESOURCE_INVENTORY_TOOL, RESOURCE_READ_TOOL,
                  VISUAL_PROFILE_READ_TOOL, WEB_SEARCH_TOOL]
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


def _execute_resource_inventory_via_cd(rt: AgentRuntime, project: str, arguments: str,
                                       instruction, message_id) -> dict:
    """resource.inventory 只读执行器（§0.6：定位 Prop id；复用 Resource Agent 实现）。"""
    from .character_designer import _execute_resource_inventory
    return _execute_resource_inventory(rt, project, arguments, instruction, message_id)


def _execute_resource_read_via_cd(rt: AgentRuntime, project: str, arguments: str,
                                  instruction, message_id) -> dict:
    """resource.read 只读执行器（§0.6：读 Prop 详情确认可用性；只读无写权）。"""
    from .character_designer import _execute_resource_read
    return _execute_resource_read(rt, project, arguments, instruction, message_id)


def _execute_web_search_via_sw(rt: AgentRuntime, project: str, arguments: str,
                               instruction, message_id) -> dict:
    """web.search 只读执行器（复用 Script Writer 实现；预算由本 loop 维护）。"""
    return _execute_web_search(rt, project, arguments, instruction, message_id)


# ============ 工具执行分派 ============
def _resolve_pdir(rt: AgentRuntime, project: str):
    return rt._resolve_project_dir(project)


def _svc(pdir, project: str):
    """SceneSystemService（每次调用新建——service 无状态，store 自带文件锁）。"""
    from src.scene_system.service import SceneSystemService
    return SceneSystemService(pdir, project)


def _visual_svc(pdir, project: str):
    """SceneVisualService（生成/审查/审批正式链路包装）。"""
    from src.scene_system.visual_service import SceneVisualService
    return SceneVisualService(pdir, project)


TOOL_EXECUTORS: Dict[str, Any] = {
    "story.list": _execute_story_list,
    "story.read": _execute_story_read,
    "script.read": _execute_script_read,
    "shot.list": _execute_shot_list_via_director,
    "shot.read": _execute_shot_read_via_director,
    "project.visual_profile.read": _execute_visual_profile_read_via_cd,
    "resource.inventory": _execute_resource_inventory_via_cd,
    "resource.read": _execute_resource_read_via_cd,
    "web.search": _execute_web_search_via_sw,
}

TOOL_LABELS = {
    "story.list": "列出故事段", "story.read": "读取故事段", "script.read": "读取剧本正文",
    "shot.list": "列出镜头", "shot.read": "读取镜头",
    "project.visual_profile.read": "读取项目视觉定义",
    "resource.inventory": "列出生产资源（定位 Prop id）",
    "resource.read": "读取生产资源详情",
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


# ============ C 域只读工具（28.md §8/§9，Commit C）============
SCENE_INVENTORY_TOOL = {
    "type": "function",
    "function": {
        "name": "scene.inventory",
        "description": ("列出项目 Scene System（场景系统）轻量地图：Location（地点）/"
                        "SceneInstance（场景实例）/已批准 LocationView 与 "
                        "SceneInstanceView 数量/stale 概况 + 当前 document_revision。"
                        "开销很小，不返回图片/prompt/历史。"
                        "场景任务【第一步】用它定位与防重复创建（同一地点绝不重复建）。"
                        "filter：all（默认）/location/instance。只读工具。"),
        "parameters": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string", "enum": ["all", "location", "instance"],
                    "description": "列出范围（默认 all）。",
                },
            },
        },
    },
}

SCENE_PRODUCTION_INSPECT_TOOL = {
    "type": "function",
    "function": {
        "name": "scene.production.inspect",
        "description": ("读取一个故事段（story_scene_id）的完整场景生产准备摘要："
                        "剧情输入（地点提示/时间/角色/剧情事实）+ 场景系统现状"
                        "（SceneInstance/Location/anchors/layout/SceneState/LocationViews/"
                        "SceneInstanceViews 及 stale）+ 项目视觉定义摘要 + 资源依赖"
                        "（prop 引用是否缺失/失效）+ 生产状态判定"
                        "（NO_LOCATION/INSTANCE_MISSING/LOCATION_INCOMPLETE/"
                        "BASE_VIEW_MISSING/STATE_INCOMPLETE/SCIV_MISSING/SCIV_STALE/READY）。"
                        "场景准备任务的【第一步】；只读工具。"),
        "parameters": {
            "type": "object",
            "properties": {
                "story_scene_id": {"type": "string",
                                   "description": "故事段 id（来自 story.list，如 s01）。"},
            },
            "required": ["story_scene_id"],
        },
    },
}


def _scene_system_read(pdir):
    """SceneSystemStore 只读加载（轻量；service 的 OCC/锁只在 mutation 用）。"""
    from src.scene_system.store import SceneSystemStore
    return SceneSystemStore(pdir).load()


def _sciv_status_map(pdir, project: str, doc) -> Dict[str, dict]:
    """全部 SceneInstanceView 的 read 模型状态（复用 SceneVisualService.read_scene_
    instance_view_status——stale/readiness 唯一权威，绝不重实现 freshness 规则）。
    单个失败降级为 unknown（诚实标注，不阻塞整体）。"""
    from src.scene_system.visual_service import SceneVisualService
    vs = SceneVisualService(pdir, project)
    out: Dict[str, dict] = {}
    for v in doc.scene_instance_views.values():
        try:
            st = vs.read_scene_instance_view_status(v.scene_instance_view_id)
        except Exception as e:   # noqa: BLE001 - 单个失败不拖垮 inventory/inspect
            st = {"scene_instance_view_id": v.scene_instance_view_id,
                  "is_stale": None, "stale_reasons": [],
                  "error": f"status_unavailable:{type(e).__name__}"}
        out[v.scene_instance_view_id] = st or {}
    return out


def _execute_scene_inventory(rt: AgentRuntime, project: str, arguments: str,
                             instruction, message_id) -> dict:
    """scene.inventory（28.md §8）：compact 列出 Locations / SceneInstances /
    approved views 数量 / stale 概况 / document_revision。filter: all|location|instance。"""
    args = _parse_args(arguments)
    flt = str(args.get("filter") or "all").strip().lower() or "all"
    if flt not in ("all", "location", "instance"):
        return {"ok": False, "operation": "scene.inventory",
                "error": "validation_error", "detail": "filter 只支持 all|location|instance"}
    pdir = rt._resolve_project_dir(project)
    doc = _scene_system_read(pdir)
    sciv_status = _sciv_status_map(pdir, project, doc)

    loc_rows = []
    if flt in ("all", "location"):
        for loc in sorted(doc.locations.values(), key=lambda x: x.location_id):
            anchors = [a for a in doc.anchors.values() if a.location_id == loc.location_id]
            views = [v for v in doc.views.values() if v.location_id == loc.location_id]
            approved_views = [v for v in views
                              if v.approval_status == "approved" and v.reference_asset_id]
            loc_rows.append({
                "location_id": loc.location_id, "name": loc.name,
                "identity": (loc.identity_description or loc.description or "")[:80],
                "anchors": len(anchors),
                "views": len(views),
                "approved_views": len(approved_views),
                "view_labels": [v.label or v.view_id for v in
                                sorted(views, key=lambda x: x.view_id)][:8],
            })

    inst_rows = []
    if flt in ("all", "instance"):
        for inst in sorted(doc.scene_instances.values(),
                           key=lambda x: x.scene_instance_id):
            loc = doc.locations.get(inst.location_id)
            scivs = [v for v in doc.scene_instance_views.values()
                     if v.scene_instance_id == inst.scene_instance_id]
            approved = [v for v in scivs
                        if v.approval_status == "approved" and v.reference_asset_id]
            stale = [v.scene_instance_view_id for v in approved
                     if sciv_status.get(v.scene_instance_view_id, {}).get("is_stale")]
            state = inst.state
            inst_rows.append({
                "scene_instance_id": inst.scene_instance_id,
                "story_scene_id": inst.story_scene_id,
                "location_id": inst.location_id,
                "location_name": loc.name if loc else "(missing location)",
                "status": inst.status,
                "state_summary": {
                    "time_of_day": state.time_of_day or "",
                    "weather": state.weather or "",
                    "lighting": (state.lighting or "")[:40],
                },
                "prop_placements": len(state.prop_placements),
                "scivs": len(scivs),
                "approved_scivs": len(approved),
                "stale_scivs": stale,
                "missing_story_scene": inst.story_scene_id not in {
                    s.scene_id for s in ProjectStore(pdir).load().scenes or []},
            })

    n_views = sum(1 for v in doc.views.values()
                  if v.approval_status == "approved" and v.reference_asset_id)
    n_scivs = sum(1 for v in doc.scene_instance_views.values()
                  if v.approval_status == "approved" and v.reference_asset_id)
    n_stale = sum(1 for st in sciv_status.values() if st.get("is_stale"))
    from src.scene_system.models import document_revision as _doc_rev
    return {"ok": True, "operation": "scene.inventory", "filter": flt,
            "document_revision": _doc_rev(doc),
            "summary": (f"场景系统：{len(doc.locations)} 地点 / {len(doc.scene_instances)} "
                        f"实例 / {n_views} approved LocationView / {n_scivs} approved "
                        f"SceneInstanceView / {n_stale} stale"),
            "locations": loc_rows,
            "scene_instances": inst_rows,
            "totals": {"locations": len(doc.locations),
                       "anchors": len(doc.anchors),
                       "views": len(doc.views),
                       "approved_views": n_views,
                       "scene_instances": len(doc.scene_instances),
                       "approved_scivs": n_scivs,
                       "stale_scivs": n_stale}}


_PRODUCTION_STATUSES = ("NO_LOCATION", "LOCATION_INCOMPLETE", "INSTANCE_MISSING",
                        "BASE_VIEW_MISSING", "STATE_INCOMPLETE", "SCIV_MISSING",
                        "SCIV_STALE", "READY")


def _derive_production_status(doc, inst, location) -> tuple:
    """生产状态判定（28.md §9 阶梯；staleness 复用 sciv read 模型，不重实现）。"""
    if inst is None:
        if not doc.locations:
            return "NO_LOCATION", "场景系统还没有任何 Location——先创建地点。"
        return "INSTANCE_MISSING", ("场景系统已有 Location，但该故事段还没有 active "
                                    "SceneInstance——复用现有地点或建新地点后绑定。")
    if location is None:
        return "LOCATION_INCOMPLETE", ("SceneInstance 绑定的 Location 不存在"
                                       "（数据不一致，先修复绑定）。")
    anchors = [a for a in doc.anchors.values() if a.location_id == location.location_id]
    if not anchors:
        return "LOCATION_INCOMPLETE", ("Location 还没有任何 SpatialAnchor——多视角识别与"
                                       "机位规划缺空间结构，先建结构性锚点。")
    views = [v for v in doc.views.values() if v.location_id == location.location_id]
    approved_views = [v for v in views
                      if v.approval_status == "approved" and v.reference_asset_id]
    if not approved_views:
        return "BASE_VIEW_MISSING", ("Location 没有 approved LocationView——"
                                     "先生成并批准基础视角。")
    state = inst.state
    state_empty = not (state.time_of_day or state.weather or state.lighting
                       or state.environment_conditions or state.anchor_states
                       or state.prop_placements)
    if state_empty:
        return "STATE_INCOMPLETE", ("SceneState 完全为空——按剧情推导该场戏的时间/天气/"
                                    "灯光/anchor 状态/prop 摆放。")
    scivs = [v for v in doc.scene_instance_views.values()
             if v.scene_instance_id == inst.scene_instance_id]
    approved_scivs = [v for v in scivs
                      if v.approval_status == "approved" and v.reference_asset_id]
    if not approved_scivs:
        return "SCIV_MISSING", ("有基础视角与状态，但没有 approved SceneInstanceView——"
                                "场景状态图还没生成/批准。")
    return None, ""   # 具体 READY/SCIV_STALE 由调用方结合 stale 判定


def _execute_scene_production_inspect(rt: AgentRuntime, project: str, arguments: str,
                                      instruction, message_id) -> dict:
    """scene.production.inspect（28.md §9）：一个工具返回该故事段的完整场景生产准备
    摘要。复用 SceneVisualService stale read 模型与共享 VisualProfile 投影，零重实现。"""
    args = _parse_args(arguments)
    story_scene_id = str(args.get("story_scene_id") or "").strip()
    if not story_scene_id:
        raise AgentError("bad_arguments", "缺少 story_scene_id")
    pdir = rt._resolve_project_dir(project)
    proj = ProjectStore(pdir).load()
    scene = next((s for s in (proj.scenes or []) if s.scene_id == story_scene_id), None)
    if scene is None:
        return {"ok": False, "operation": "scene.production.inspect",
                "error": "story_scene_not_found",
                "detail": f"故事段不存在: {story_scene_id}"}
    doc = _scene_system_read(pdir)
    sciv_status = _sciv_status_map(pdir, project, doc)

    # ---- Story input（剧情事实，只读）----
    story_input = {
        "story_scene_id": scene.scene_id,
        "title": scene.title or "",
        "summary": (scene.summary or "")[:400],
        "location_hint": scene.location or "",
        "time_of_day": scene.time_of_day or "",
        # weather/environment 无独立字段——只存在于剧情文本中，需要时读 summary 判断
        "characters": list(scene.characters or []),
        "shot_count": len(scene.shots or []),
        "note": ("weather/environment 等环境事实如未在上方字段中，需从剧情文本（summary）"
                 "自行判断；Story 本身不归 Scene Agent 修改。"),
    }

    # ---- Scene System 现状 ----
    inst = next((i for i in doc.scene_instances.values()
                 if i.story_scene_id == story_scene_id and i.status == "active"), None)
    location = doc.locations.get(inst.location_id) if inst else None
    anchors = []
    layout = None
    views = []
    state_read = None
    if inst is not None:
        if location is not None:
            anchors = [{"anchor_id": a.anchor_id, "name": a.name, "kind": a.kind,
                        "description": (a.description or "")[:100],
                        "semantic_zone": a.semantic_zone or ""}
                       for a in sorted((a for a in doc.anchors.values()
                                        if a.location_id == location.location_id),
                                       key=lambda x: x.anchor_id)]
            lay = next((l for l in doc.layouts.values()
                        if l.location_id == location.location_id), None)
            if lay is not None:
                layout = {"layout_id": lay.layout_id,
                          "orientation_convention": lay.orientation_convention,
                          "placements": [{"anchor_id": p.anchor_id, "zone": p.zone,
                                          "x": p.x, "y": p.y}
                                         for p in lay.placements]}
        for v in sorted((v for v in doc.views.values()
                         if v.location_id == (location.location_id if location else "")),
                        key=lambda x: x.view_id):
            views.append({
                "view_id": v.view_id, "label": v.label,
                "camera_zone": v.camera_zone, "look_direction": v.look_direction,
                "visible_anchor_ids": list(v.visible_anchor_ids),
                "generation_status": v.generation_status,
                "approval_status": v.approval_status,
                "usable": bool(v.approval_status == "approved" and v.reference_asset_id),
            })
        st = inst.state
        state_read = {
            "time_of_day": st.time_of_day, "weather": st.weather,
            "lighting": st.lighting,
            "environment_conditions": st.environment_conditions,
            "notes": (st.notes or "")[:120],
            "anchor_states": {k: v.condition for k, v in st.anchor_states.items()},
            "prop_placements": {pid: {"anchor_id": pp.anchor_id, "state": pp.state}
                                for pid, pp in st.prop_placements.items()},
        }
    scene_system = {
        "scene_instance": ({"scene_instance_id": inst.scene_instance_id,
                            "location_id": inst.location_id,
                            "location_name": location.name if location else "",
                            "status": inst.status} if inst else None),
        "location": ({"location_id": location.location_id, "name": location.name,
                      "identity": (location.identity_description
                                   or location.description or "")[:160],
                      "style_notes": location.style_notes or ""} if location else None),
        "anchors": anchors,
        "layout": layout,
        "scene_state": state_read,
        "location_views": views,
        "scene_instance_views": [
            {"scene_instance_view_id": st_["scene_instance_view_id"],
             "location_view_id": st_.get("location_view_id", ""),
             "approval_status": st_.get("approval_status", ""),
             "is_stale": st_.get("is_stale"),
             "stale_reasons": st_.get("stale_reasons") or [],
             "has_pass_review_evidence": st_.get("has_pass_review_evidence"),
             "usable": bool(st_.get("approval_status") == "approved"
                            and not st_.get("is_stale"))}
            for st_ in sciv_status.values()
            if st_.get("scene_instance_id") == inst.scene_instance_id
        ] if inst is not None else [],
    }

    # ---- VisualProfile（复用 C1 共享只读层）----
    from .production_context import read_visual_profile_projection
    profile_proj = read_visual_profile_projection(pdir)
    profile_compact = {
        "specified": profile_proj.get("specified"),
        "fingerprint": profile_proj.get("fingerprint", ""),
        "summary": (profile_proj.get("visual_profile") or {}).get("render_family")
                   if profile_proj.get("specified") else None,
    }

    # ---- Resource dependencies（§9/§15：prop 引用核验，零越权）----
    prop_deps: list = []
    missing_props: list = []
    if inst is not None and inst.state.prop_placements:
        from src.scene_system.visual_basis import load_resources_doc_or_none
        rdoc = load_resources_doc_or_none(pdir)
        for pid, pp in sorted(inst.state.prop_placements.items()):
            prop = rdoc.props.get(pid) if rdoc else None
            if prop is None:
                missing_props.append({
                    "prop_id": pid, "reason": "prop_not_found",
                    "detail": "Resources 中不存在该 Prop",
                    "target_field": f"SceneState.prop_placements[{pid}]",
                })
            elif prop.approval_status != "approved":
                missing_props.append({
                    "prop_id": pid, "reason": "prop_not_approved",
                    "detail": "Prop 存在但没有 approved 参考图（生产不可用）",
                    "target_field": f"SceneState.prop_placements[{pid}]",
                })
            prop_deps.append({
                "prop_id": pid, "anchor_id": pp.anchor_id, "state": pp.state,
                "found": prop is not None,
                "approval_status": prop.approval_status if prop else None,
                "prop_name": prop.name if prop else None,
            })
    resource_dependencies = {"prop_dependencies": prop_deps,
                             "missing_or_invalid_props": missing_props}

    # ---- Production status（阶梯判定 + stale）----
    status, reason = _derive_production_status(doc, inst, location)
    if status is None:
        approved_scivs = [v for v in doc.scene_instance_views.values()
                          if v.scene_instance_id == inst.scene_instance_id
                          and v.approval_status == "approved" and v.reference_asset_id]
        if any(sciv_status.get(v.scene_instance_view_id, {}).get("is_stale")
               for v in approved_scivs):
            status = "SCIV_STALE"
            reason = ("approved SceneInstanceView 已 stale（SceneState/base view 变化后"
                      "未重新生成）——重新生成对应视角。")
        else:
            status = "READY"
            reason = "场景生产事实与 approved 场景参考齐备，可供 Director 建场景 binding。"

    return {"ok": True, "operation": "scene.production.inspect",
            "story_scene_id": story_scene_id,
            "story_input": story_input,
            "scene_system": scene_system,
            "visual_profile": profile_compact,
            "resource_dependencies": resource_dependencies,
            "production_status": status,
            "status_reason": reason,
            "summary": f"{story_scene_id} 场景生产状态：{status}（{reason[:80]}）",
            "note": ("只读摘要。按 production_status 推进：复用优先，绝不重复创建 Location；"
                     "缺 Prop 时报告 resource_prerequisite，不自己创建。")}


# ============ D 域写工具（28.md §10，Commit D）：Location / Anchor / Layout ============
SCENE_LOCATION_UPSERT_TOOL = {
    "type": "function",
    "function": {
        "name": "scene.location.upsert",
        "description": ("创建或更新 Location（地点长期身份）。不传 location_id = 创建"
                        "（expected_document_revision 传 scene.inventory 看到的 "
                        "document_revision）；传 location_id = 更新（expected_revision "
                        "传该 location 的 revision）。"
                        "🔴 Location 是【可跨多个故事段复用的地点身份】（「山雨驿站」），"
                        "不是某一幕的样子（「s03 暴雨中的山雨驿站」——暴雨属于 "
                        "SceneState）。创建前必须 scene.inventory 确认可复用的地点，"
                        "同一地点绝不重复创建；rename 不改变 location_id。"
                        "一次调用只做一个写操作。"),
        "parameters": {
            "type": "object",
            "properties": {
                "location_id": {"type": "string",
                                "description": "要更新的 Location id（更新时必传；创建时省略）。"},
                "name": {"type": "string",
                         "description": "地点显示名（如 山雨驿站 / 灯塔监听室）。"},
                "identity_description": {"type": "string",
                                         "description": "稳定空间身份描述（英文优先，进视觉生成"
                                                        "prompt 的语义基底：建筑/规模/氛围/材质）。"},
                "description": {"type": "string", "description": "地点补充描述。"},
                "style_notes": {"type": "string", "description": "地点风格备注（时代/地域/材质感）。"},
                "expected_document_revision": {"type": "string",
                                               "description": "创建时必传：scene.inventory 的 document_revision。"},
                "expected_revision": {"type": "string",
                                      "description": "更新时必传：该 Location 当前 revision。"},
            },
            "required": [],
        },
    },
}

SCENE_ANCHOR_UPSERT_TOOL = {
    "type": "function",
    "function": {
        "name": "scene.anchor.upsert",
        "description": ("创建或更新 SpatialAnchor（稳定空间锚点）。创建：location_id + name"
                        "（+ expected_document_revision）；更新：anchor_id + expected_revision。"
                        "🔴 优先建【真正有助于多视角识别/连续性/机位规划】的结构性锚点"
                        "（east gate / west chimney / north stair / bridge arch / well），"
                        "不要建几十个装饰性小锚点。relation 的 object_anchor_id 必须是同 "
                        "Location 已存在的 anchor。kind 只能是 door/window/stairs/furniture/"
                        "landmark/zone/entry/exit/other。一次调用只做一个写操作。"),
        "parameters": {
            "type": "object",
            "properties": {
                "location_id": {"type": "string", "description": "所属 Location id（创建时必传）。"},
                "anchor_id": {"type": "string",
                              "description": "要更新的 anchor id（更新时必传；创建时省略）。"},
                "name": {"type": "string", "description": "锚点名（如 east gate / 主楼梯）。"},
                "kind": {"type": "string",
                         "enum": ["door", "window", "stairs", "furniture", "landmark",
                                  "zone", "entry", "exit", "other"],
                         "description": "锚点类型。"},
                "description": {"type": "string",
                                "description": "视觉结构描述（数量/形状/位置特征会进审查期望）。"},
                "semantic_zone": {"type": "string",
                                  "description": "语义方位（如 north_wall / center / south_entrance）。"},
                "relations": {"type": "array",
                              "items": {"type": "object", "properties": {
                                  "object_anchor_id": {"type": "string"},
                                  "relation": {"type": "string",
                                               "description": "如 opposite / beside / above / behind"}},
                                  "required": ["object_anchor_id", "relation"]},
                              "description": "与同 Location 其它 anchor 的空间关系（创建时可选）。"},
                "expected_document_revision": {"type": "string",
                                               "description": "创建时必传：document_revision。"},
                "expected_revision": {"type": "string",
                                      "description": "更新时必传：该 anchor 当前 revision。"},
            },
            "required": [],
        },
    },
}

SCENE_LAYOUT_SET_TOOL = {
    "type": "function",
    "function": {
        "name": "scene.layout.set",
        "description": ("设置一个 Location 的 SetLayout（normalized 2D 平面布局提示）。"
                        "一个 Location 至多一个 layout：已有则更新（需该 layout 的 "
                        "expected_revision），没有则创建（需 expected_document_revision）。"
                        "🔴 layout 是空间关系提示/连续性辅助（语义方位），不是物理精确 CAD——"
                        "x/y 取 0~1 归一化近似值即可，不要制造毫米级假精度。"
                        "orientation_convention 用 north_up 时 y 轴=南北（0=北）。"
                        "一次调用只做一个写操作。"),
        "parameters": {
            "type": "object",
            "properties": {
                "location_id": {"type": "string", "description": "Location id（必传）。"},
                "orientation_convention": {"type": "string",
                                           "description": "如 north_up（默认建议）。"},
                "notes": {"type": "string", "description": "布局备注。"},
                "placements": {"type": "array",
                               "items": {"type": "object", "properties": {
                                   "anchor_id": {"type": "string"},
                                   "zone": {"type": "string",
                                            "description": "语义方位（north-west area 等）"},
                                   "x": {"type": "number", "description": "0~1 归一化"},
                                   "y": {"type": "number", "description": "0~1 归一化"}},
                                   "required": ["anchor_id"]},
                               "description": "各 anchor 的平面位置（anchor 必须属于该 Location）。"},
                "expected_document_revision": {"type": "string",
                                               "description": "创建时必传：document_revision。"},
                "expected_revision": {"type": "string",
                                      "description": "更新已有 layout 时必传：该 layout 的 revision。"},
            },
            "required": ["location_id"],
        },
    },
}


def _scene_write_receipt(r: dict, op: str, *, kind: str, entity_id: str) -> dict:
    """service receipt → Agent 写回执（补 changed_resources 等 Producer 派生字段；
    真实回执透传，绝不美化失败）。scene service 的 stale 以 error=stale_revision
    表达——规范化为 stale 布尔（与 Resource Agent 回执契约一致）。"""
    out = dict(r)
    out["operation"] = op
    if out.get("error") == "stale_revision":
        out["stale"] = True
    if r.get("ok") and r.get("verified") and not r.get("unchanged"):
        out["changed_resources"] = [{"kind": kind, "id": entity_id}]
    else:
        out["changed_resources"] = []
    return out


async def _execute_scene_location_upsert(rt: AgentRuntime, project: str, arguments: str,
                                         instruction, message_id) -> dict:
    """scene.location.upsert（28.md §10.1）：Location identity 创建/更新（OCC）。
    更新路径 name 可选（只改要改的字段）。"""
    args = _parse_args(arguments)
    pdir = rt._resolve_project_dir(project)
    svc = _svc(pdir, project)
    location_id = str(args.get("location_id") or "").strip()
    if location_id:
        expected = str(args.get("expected_revision") or "").strip()
        if not expected:
            return {"ok": False, "operation": "scene.location.upsert",
                    "error": "expected_revision_required",
                    "detail": "更新 Location 必须带 expected_revision（先 scene.inventory/"
                              "scene.production.inspect 拿该 location 的 revision）",
                    "location_id": location_id}
        patch = {k: args[k] for k in ("name", "description", "identity_description",
                                      "style_notes") if args.get(k) is not None}
        r = await svc.update_location(location_id, patch, expected_revision=expected)
        r = _scene_write_receipt(r, "scene.location.upsert", kind="location",
                                 entity_id=location_id)
        r.setdefault("location_id", location_id)
        return r
    name = str(args.get("name") or "").strip()
    if not name:
        return {"ok": False, "operation": "scene.location.upsert",
                "error": "validation_error", "detail": "创建 Location 需要 name"}
    r = await svc.create_location(
        name=name,
        description=str(args.get("description") or ""),
        identity_description=str(args.get("identity_description") or ""),
        style_notes=str(args.get("style_notes") or ""),
        expected_document_revision=str(args.get("expected_document_revision") or ""))
    r = _scene_write_receipt(r, "scene.location.upsert", kind="location",
                             entity_id=str(r.get("target") or ""))
    if r.get("ok"):
        r["location_id"] = r.get("target")
    return r


async def _execute_scene_anchor_upsert(rt: AgentRuntime, project: str, arguments: str,
                                       instruction, message_id) -> dict:
    """scene.anchor.upsert（28.md §10.2）：SpatialAnchor 创建/更新（OCC + 关系校验）。
    更新路径 name 可选（只改要改的字段）。"""
    args = _parse_args(arguments)
    pdir = rt._resolve_project_dir(project)
    svc = _svc(pdir, project)
    anchor_id = str(args.get("anchor_id") or "").strip()
    relations = args.get("relations")
    if relations is not None and not isinstance(relations, list):
        return {"ok": False, "operation": "scene.anchor.upsert",
                "error": "validation_error", "detail": "relations 必须是数组"}
    if anchor_id:
        expected = str(args.get("expected_revision") or "").strip()
        if not expected:
            return {"ok": False, "operation": "scene.anchor.upsert",
                    "error": "expected_revision_required",
                    "detail": "更新 anchor 必须带 expected_revision",
                    "anchor_id": anchor_id}
        patch: Dict[str, Any] = {}
        for k in ("name", "kind", "description", "semantic_zone"):
            if args.get(k) is not None:
                patch[k] = args[k]
        if relations is not None:
            patch["relations"] = relations
        r = await svc.update_anchor(anchor_id, patch, expected_revision=expected)
        r = _scene_write_receipt(r, "scene.anchor.upsert", kind="anchor",
                                 entity_id=anchor_id)
        r.setdefault("anchor_id", anchor_id)
        if r.get("ok"):
            r["location_id"] = (r.get("after") or {}).get("location_id") \
                if isinstance(r.get("after"), dict) else None
        return r
    name = str(args.get("name") or "").strip()
    if not name:
        return {"ok": False, "operation": "scene.anchor.upsert",
                "error": "validation_error", "detail": "创建 anchor 需要 name"}
    location_id = str(args.get("location_id") or "").strip()
    if not location_id:
        return {"ok": False, "operation": "scene.anchor.upsert",
                "error": "validation_error", "detail": "创建 anchor 需要 location_id"}
    r = await svc.create_anchor(
        location_id=location_id, name=name,
        kind=str(args.get("kind") or "other"),
        description=str(args.get("description") or ""),
        semantic_zone=str(args.get("semantic_zone") or ""),
        relations=relations,
        expected_document_revision=str(args.get("expected_document_revision") or ""))
    r = _scene_write_receipt(r, "scene.anchor.upsert", kind="anchor",
                             entity_id=str(r.get("target") or ""))
    if r.get("ok"):
        r["anchor_id"] = r.get("target")
        r["location_id"] = location_id
    return r


async def _execute_scene_layout_set(rt: AgentRuntime, project: str, arguments: str,
                                    instruction, message_id) -> dict:
    """scene.layout.set（28.md §10.3）：SetLayout upsert（一 location 至多一个 layout）。"""
    args = _parse_args(arguments)
    location_id = str(args.get("location_id") or "").strip()
    if not location_id:
        return {"ok": False, "operation": "scene.layout.set",
                "error": "validation_error", "detail": "location_id 必填"}
    pdir = rt._resolve_project_dir(project)
    svc = _svc(pdir, project)
    placements = args.get("placements")
    if placements is not None and not isinstance(placements, list):
        return {"ok": False, "operation": "scene.layout.set",
                "error": "validation_error", "detail": "placements 必须是数组"}
    existing = svc.read_layout_by_location(location_id)
    if existing is not None:
        expected = str(args.get("expected_revision") or "").strip()
        if not expected:
            return {"ok": False, "operation": "scene.layout.set",
                    "error": "expected_revision_required",
                    "detail": (f"该 Location 已有 layout（{existing['layout_id']}）；"
                               "更新必须带 expected_revision"),
                    "layout_id": existing["layout_id"],
                    "location_id": location_id,
                    "current_layout": existing}
        patch = {"orientation_convention": str(args.get("orientation_convention") or ""),
                 "notes": str(args.get("notes") or "")}
        if placements is not None:
            patch["placements"] = placements
        r = await svc.update_layout(existing["layout_id"], patch,
                                    expected_revision=expected)
        r = _scene_write_receipt(r, "scene.layout.set", kind="layout",
                                 entity_id=existing["layout_id"])
        r.setdefault("layout_id", existing["layout_id"])
        r["location_id"] = location_id
        return r
    r = await svc.create_layout(
        location_id=location_id,
        orientation_convention=str(args.get("orientation_convention") or ""),
        notes=str(args.get("notes") or ""),
        placements=placements,
        expected_document_revision=str(args.get("expected_document_revision") or ""))
    r = _scene_write_receipt(r, "scene.layout.set", kind="layout",
                             entity_id=str(r.get("target") or ""))
    if r.get("ok"):
        r["layout_id"] = r.get("target")
        r["location_id"] = location_id
    return r


# ============ E 域写工具（28.md §11/§12/§17/§18，Commit E）：scene.view.prepare ============
SCENE_VIEW_PREPARE_TOOL = {
    "type": "function",
    "function": {
        "name": "scene.view.prepare",
        "description": ("高层 LocationView 生产准备（一次调用 = 一次正式生成尝试，约 1 分钟）："
                        "建/改 view 定义 → 生成候选图 → 正式 vision 审查 → "
                        "approval_mode=approve_if_pass 且审查 pass 时自动批准。"
                        "不传 view_id = 新建视角（expected_document_revision 必传）；"
                        "传 view_id = 更新定义后重新生成（expected_revision 传该 view 的 "
                        "revision）。机位纪律：先建 hero/identity view，再按【真实生产需要】"
                        "补视角（镜头需求/Story 地理/连续性），绝不为凑数生成 8 视角。"
                        "同一机位+同一语义输入连续 needs_fix 会被 review_retry_exhausted "
                        "拦住——先真正改变输入（换 camera_zone/改 visible anchors/改描述）。"
                        "审查严格检查机位正确性：声明 side/rear/overhead 但实际是正面 hero "
                        "构图会被 needs_fix；模型确实做不到时如实报告模型边界，不要原样重试。"),
        "parameters": {
            "type": "object",
            "properties": {
                "location_id": {"type": "string", "description": "Location id（必传）。"},
                "view_id": {"type": "string",
                            "description": "要更新/重生成的 LocationView id（更新时传；新建省略）。"},
                "label": {"type": "string", "description": "视角名（如 hero / east side / plan）。"},
                "camera_zone": {"type": "string",
                                "description": "相机位置（front/side/rear/overhead 或 "
                                               "south_west 等）；overhead=top-down 正俯视。"},
                "look_direction": {"type": "string", "description": "看向（north_east / site center 等）。"},
                "visible_anchor_ids": {"type": "array", "items": {"type": "string"},
                                       "description": "该视角必须可见的 anchor id 列表（审查逐个核验）。"},
                "expected_document_revision": {"type": "string",
                                               "description": "新建 view 时必传：document_revision。"},
                "expected_revision": {"type": "string",
                                      "description": "更新已有 view 时必传：该 view 当前 revision。"},
                "candidate_count": {"type": "integer",
                                    "description": "候选图数量 1~2（默认 1；省图）。"},
                "approval_mode": {"type": "string",
                                  "enum": ["review_only", "approve_if_pass"],
                                  "description": "review_only=只生成审查不批准（探索）；"
                                                 "approve_if_pass=审查真实 pass 时自动批准（production-ready）。"},
            },
            "required": ["location_id", "camera_zone", "approval_mode"],
        },
    },
}


def _view_prepare_progress(event_cb, phase: str, summary: str) -> None:
    if event_cb:
        event_cb({"type": "tool.progress", "phase": phase, "summary": summary[:160]})


async def _execute_scene_view_prepare(rt: AgentRuntime, project: str, arguments: str,
                                      instruction, message_id, *,
                                      event_cb=None, cancel_event=None) -> dict:
    """scene.view.prepare（28.md §11）：LocationView 高层准备。内部链路：
    create/update view（正式 service，OCC）→ generate（一次正式生成尝试）→
    vision review → optional approve（approve_if_pass 且真实 pass）。
    复用共享 review retry guard（同 view + 同语义 basis 连续 needs_fix ≥3 → 拦截）。"""
    args = _parse_args(arguments)
    location_id = str(args.get("location_id") or "").strip()
    approval_mode = str(args.get("approval_mode") or "review_only").strip()
    # Phase D §35：camera_zone 必填（空机位 view 会让 Sciv 审查期望退化为 "(base)"
    # 乱码 → 审查 churn；机位是 view 的核心生产承诺，fail closed）
    if not str(args.get("camera_zone") or "").strip() and not str(args.get("view_id") or "").strip():
        return {"ok": False, "operation": "scene.view.prepare",
                "error": "camera_zone_required",
                "detail": "新建 LocationView 必须传 camera_zone（如 front/side/rear/"
                          "overhead/interior_hall……该 view 的相机物理位置）。"
                          "机位是审查与绑定链的核心承诺，不接受空值。"}
    if not location_id:
        return {"ok": False, "operation": "scene.view.prepare",
                "error": "validation_error", "detail": "location_id 必填"}
    if approval_mode not in ("review_only", "approve_if_pass"):
        return {"ok": False, "operation": "scene.view.prepare",
                "error": "validation_error", "detail": "approval_mode 只支持 "
                                                       "review_only|approve_if_pass"}
    pdir = rt._resolve_project_dir(project)
    vs = _visual_svc(pdir, project)
    svc = _svc(pdir, project)

    view_id = str(args.get("view_id") or "").strip()
    cam_zone = str(args.get("camera_zone") or "").strip()
    look_dir = str(args.get("look_direction") or "").strip()
    label = str(args.get("label") or "").strip()
    visible = args.get("visible_anchor_ids")
    if visible is not None and not isinstance(visible, list):
        return {"ok": False, "operation": "scene.view.prepare",
                "error": "validation_error", "detail": "visible_anchor_ids 必须是数组"}
    started = time.time()

    # ---- 共享 retry guard（28.md §18）：key = 稳定 view_id（定义落地后判定，
    # 建定义本身零烧图）；basis = view 意图字段，真实改任一项 → 新 basis 放行。
    from .review_retry_guard import (review_basis_sig, review_retry_check,
                                     review_retry_record)
    basis_sig = review_basis_sig(location_id=location_id, camera_zone=cam_zone,
                                 look_direction=look_dir, label=label,
                                 visible_anchor_ids=sorted(visible or []),
                                 intent="location_view")
    # ---- ① 建/改 view 定义（OCC）----
    if view_id:
        expected = str(args.get("expected_revision") or "").strip()
        if not expected:
            return {"ok": False, "operation": "scene.view.prepare",
                    "error": "expected_revision_required",
                    "detail": "更新已有 view 必须带 expected_revision（先 "
                              "scene.production.inspect 拿该 view 的 revision）",
                    "view_id": view_id, "location_id": location_id}
        patch: Dict[str, Any] = {}
        for k, v in (("label", label), ("camera_zone", cam_zone),
                     ("look_direction", look_dir)):
            if v:
                patch[k] = v
        if visible is not None:
            patch["visible_anchor_ids"] = visible
        _view_prepare_progress(event_cb, "view.update", f"更新 view 定义 {view_id}")
        # 更新路径：guard 在 update 之前——被拦截时不消耗 OCC revision。
        blocked = review_retry_check(project, message_id, view_id, basis_sig,
                                     "scene.view.prepare",
                                     change_hints=(
                                         "①修改 camera_zone / look_direction / visible "
                                         "anchors / 描述（任一真实语义变化）；②更换 reference "
                                         "策略；③解决 prompt-reference 冲突（必要时向用户说明"
                                         "并请其选择）；④或停止并如实向用户报告模型边界"
                                         "（MODEL_LIMITATION）/blocker。"))
        if blocked is not None:
            blocked["view_id"] = view_id
            blocked["location_id"] = location_id
            blocked["note"] = ("view 定义未变；拦截的是重复生成。"
                               + (blocked.get("note") or ""))
            return blocked
        r_def = await svc.update_view(view_id, patch, expected_revision=expected)
        if not r_def.get("ok"):
            out = _scene_write_receipt(r_def, "scene.view.prepare",
                                       kind="location_view", entity_id=view_id)
            out["view_id"] = view_id
            out["location_id"] = location_id
            return out
        view_revision = r_def.get("revision")
    else:
        expected_doc = str(args.get("expected_document_revision") or "").strip()
        if not expected_doc:
            return {"ok": False, "operation": "scene.view.prepare",
                    "error": "expected_document_revision_required",
                    "detail": "新建 view 必须带 expected_document_revision（"
                              "scene.inventory 的 document_revision）",
                    "location_id": location_id}
        _view_prepare_progress(event_cb, "view.create",
                               f"创建 view 定义（{cam_zone or 'default'}）")
        r_def = await svc.create_view(
            location_id=location_id, label=label, camera_zone=cam_zone,
            look_direction=look_dir, visible_anchor_ids=visible,
            expected_document_revision=expected_doc)
        if not r_def.get("ok"):
            out = _scene_write_receipt(r_def, "scene.view.prepare",
                                       kind="location_view",
                                       entity_id=str(r_def.get("target") or ""))
            out["location_id"] = location_id
            return out
        view_id = r_def.get("target")
        view_revision = r_def.get("revision")

    # ---- ② 生成（一次正式生成尝试）----
    candidate_count = args.get("candidate_count") or 1
    try:
        candidate_count = int(candidate_count)
    except (TypeError, ValueError):
        candidate_count = 1
    _view_prepare_progress(event_cb, "view.generate",
                           f"生成候选图 ×{candidate_count}（{view_id}，约 1 分钟）")
    # Phase D §2：图片质量档跟随项目生产设置（project.style.size_tier；缺省 1K）
    _proj_size_tier = (ProjectStore(pdir).load().style.size_tier or "1K")
    r_gen = await vs.generate_location_view(
        view_id, expected_revision=view_revision,
        candidate_count=max(1, min(candidate_count, 2)),
        size_tier=_proj_size_tier)
    if not r_gen.get("ok"):
        out = _scene_write_receipt(r_gen, "scene.view.prepare",
                                   kind="location_view", entity_id=view_id)
        out.update({"view_id": view_id, "location_id": location_id,
                    "stage": "generate"})
        return out
    view_revision = r_gen.get("revision_after") or view_revision

    # ---- ③ 正式 vision review ----
    _view_prepare_progress(event_cb, "view.review", "vision 审查候选图")
    r_review = await vs.review_location_view_candidates(view_id)
    candidates = [c for c in (r_review.get("candidate_reviews") or [])
                  if c.get("status") == "reviewed"]
    best = None
    for c in candidates:
        if c.get("overall") == "pass":
            best = c
            break
    if best is None and candidates:
        best = candidates[0]
    review_overall = (best or {}).get("overall")

    # ---- ④ 共享 guard 记账（needs_fix 计数 / pass 清零）----
    review_retry_record(project, message_id, view_id, basis_sig, review_overall)

    # ---- ⑤ optional approve（显式 mutation；审查 pass ≠ 自动批准）----
    approved = False
    approve_error = None
    if approval_mode == "approve_if_pass" and review_overall == "pass" and best:
        from src.resources.service import ResourceService
        res_rev = ResourceService(pdir, project).document_revision()
        _view_prepare_progress(event_cb, "view.approve", "审查 pass → 批准")
        r_ap = await vs.approve_location_view(
            view_id, candidate_index=best["candidate_index"],
            expected_revision=view_revision,
            expected_resources_document_revision=res_rev)
        if r_ap.get("ok"):
            approved = True
            view_revision = r_ap.get("revision") or view_revision
        else:
            approve_error = {"error": r_ap.get("error"),
                             "detail": (r_ap.get("detail") or "")[:200]}

    verified_any = bool((r_def.get("ok") and r_def.get("verified"))
                        or (r_gen.get("ok") and r_gen.get("verified")) or approved)
    out = {"ok": True, "operation": "scene.view.prepare",
           "view_id": view_id, "location_id": location_id,
           "generation_id": r_gen.get("generation_id"),
           "review_overall": review_overall,
           "candidate_reviews": [
               {"candidate_index": c.get("candidate_index"),
                "overall": c.get("overall"),
                "view_consistency": (c.get("dimensions") or {}).get("view_consistency"),
                "camera_observed_zone": c.get("camera_observed_zone"),
                "camera_derivation_note": c.get("camera_derivation_note"),
                "summary": (c.get("summary") or "")[:200]}
               for c in candidates],
           "approval_mode": approval_mode,
           "approved": approved,
           "approve_error": approve_error,
           "stale": bool(r_gen.get("stale")),
           "unchanged": False,
           "verified": verified_any,
           "revision": view_revision,
           "document_revision_after": r_gen.get("document_revision_after")
                                      or r_def.get("document_revision_after"),
           "changed_resources": ([{"kind": "location_view", "id": view_id}]
                                 if verified_any else []),
           "elapsed_s": round(time.time() - started, 1),
           "op_summary": (f"LocationView {view_id} 生成完成（审查={review_overall}"
                          f"{'，已批准' if approved else ''}）"),
           "note": (
               "审查未 pass：候选未批准。按 candidate_reviews 的真实原因调整输入"
               "（换机位/改 anchors/改描述）后再试；不要原样重试。"
               if (not approved and review_overall not in ("pass", None)) else
               "review_only 模式：候选未经批准，Director 不可消费；用户看过后显式"
               "批准（UI）或以 approve_if_pass 重新生成。"
               if not approved else
               "view 已生成并批准（approved reference 可被 Director 消费）。")}
    return out


# ============ F 域写工具（28.md §13/§14/§15，Commit F）：Instance / State ============
SCENE_INSTANCE_PREPARE_TOOL = {
    "type": "function",
    "function": {
        "name": "scene.instance.prepare",
        "description": ("建立 Story Scene → SceneInstance → Location 绑定（V1 规则："
                        "一个故事段至多一个 active instance）。成功返回 instance_id 与"
                        "revision。若该故事段已有 active instance：系统拒绝并返回现有 "
                        "instance id——你应先 scene.production.inspect 读取现状再决定"
                        "【复用】（换 State 而不是重建）或向用户/Producer 说明，绝不静默"
                        "创建第二个。location_id 必须真实存在。一次调用只做一个写操作。"),
        "parameters": {
            "type": "object",
            "properties": {
                "story_scene_id": {"type": "string",
                                   "description": "故事段 id（来自 story.list，如 s01）。"},
                "location_id": {"type": "string", "description": "Location id。"},
                "expected_document_revision": {"type": "string",
                                               "description": "必传：document_revision。"},
            },
            "required": ["story_scene_id", "location_id", "expected_document_revision"],
        },
    },
}

SCENE_STATE_UPDATE_TOOL = {
    "type": "function",
    "function": {
        "name": "scene.state.update",
        "description": ("更新一个 SceneInstance 的 SceneState（该地点在这场戏的此刻状态）"
                        "：time_of_day / weather / lighting / environment_conditions / "
                        "notes / anchor_states（{anchor_id: {condition, note}}，anchor "
                        "必须属于该 location）/ prop_placements（{prop_id: {anchor_id, "
                        "state, note}}，prop_id 必须是真实存在的 Resources Prop）。"
                        "🔴 剧情只写「夜晚」时你可以设计合理 lighting，但【不要】无理由"
                        "改成「暴雨」——不创造影响剧情的事实；只按 Story 推导。"
                        "🔴 prop 引用不存在的 Prop 会被拒（prop_not_found）并返回结构化 "
                        "resource_prerequisite——如实上报 Producer 委派 Resource Agent，"
                        "绝不伪造 prop id。整 dict 替换语义（先 inspect 读现状再 patch）。"
                        "一次调用只做一个写操作。"),
        "parameters": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string", "description": "SceneInstance id。"},
                "time_of_day": {"type": "string", "description": "如 day / night / dusk。"},
                "weather": {"type": "string", "description": "如 clear / heavy_rain / snow。"},
                "lighting": {"type": "string", "description": "灯光描述（如 warm lanterns）。"},
                "environment_conditions": {"type": "string",
                                           "description": "weather 之外的环境条件（风/雪/烟…）。"},
                "notes": {"type": "string", "description": "状态备注。"},
                "anchor_states": {"type": "object",
                                  "description": "{anchor_id: {condition, note}}",
                                  "additionalProperties": {"type": "object"}},
                "prop_placements": {"type": "object",
                                    "description": "{prop_id: {anchor_id, state, note}}",
                                    "additionalProperties": {"type": "object"}},
                "expected_revision": {"type": "string",
                                      "description": "必传：该 instance 当前 revision。"},
            },
            "required": ["instance_id", "expected_revision"],
        },
    },
}


SCENE_INSTANCE_DELETE_TOOL = {
    "type": "function",
    "function": {
        "name": "scene.instance.delete",
        "description": ("删除一个 SceneInstance（连同其嵌入的 SceneState；Phase D §35："
                        "把服务层既有 delete_scene_instance 能力接入 Agent 工具面）。"
                        "用途：清理悬空实例——其 story_scene_id 已不在当前 Story canonical "
                        "中（故事段被删除/重组）。正在被 approved Sciv 或 Shot binding 引用的"
                        "实例不要删；先 scene.production.inspect 确认悬空再删。一次调用只删一个。"),
        "parameters": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string", "description": "SceneInstance id（scin_xxx）。"},
                "expected_revision": {"type": "string",
                                      "description": "必传：该 instance 当前 revision。"},
            },
            "required": ["instance_id", "expected_revision"],
        },
    },
}


async def _execute_scene_instance_delete(rt: AgentRuntime, project: str, arguments: str,
                                         instruction, message_id) -> dict:
    """scene.instance.delete（Phase D §35）：删除 SceneInstance（连同嵌入 SceneState）。
    服务层 delete_scene_instance 接线：OCC 保护、真实落盘校验、回执与其它写工具同构。
    纯删除 mutation：不生成、不改 Story/Resources。"""
    args = _parse_args(arguments)
    instance_id = str(args.get("instance_id") or "").strip()
    expected = str(args.get("expected_revision") or "").strip()
    if not instance_id:
        return {"ok": False, "operation": "scene.instance.delete",
                "error": "validation_error", "detail": "instance_id 必填"}
    if not expected:
        return {"ok": False, "operation": "scene.instance.delete",
                "error": "expected_revision_required",
                "detail": "必传 expected_revision（先 scene.production.inspect 拿该 "
                          "instance 的 revision）"}
    pdir = rt._resolve_project_dir(project)
    svc = _svc(pdir, project)
    r = await svc.delete_scene_instance(instance_id, expected_revision=expected)
    out = _scene_write_receipt(r, "scene.instance.delete", kind="scene_instance",
                               entity_id=instance_id)
    if not r.get("ok"):
        out["instance_id"] = instance_id
    return out


async def _execute_scene_instance_prepare(rt: AgentRuntime, project: str, arguments: str,
                                          instruction, message_id) -> dict:
    """scene.instance.prepare（28.md §13）：Story Scene → SceneInstance → Location。
    不静默创建第二个 instance（service one-active 校验，零弱化）；已存在时返回现有
    instance 供 Agent 决定复用。"""
    args = _parse_args(arguments)
    story_scene_id = str(args.get("story_scene_id") or "").strip()
    location_id = str(args.get("location_id") or "").strip()
    expected_doc = str(args.get("expected_document_revision") or "").strip()
    if not story_scene_id or not location_id:
        return {"ok": False, "operation": "scene.instance.prepare",
                "error": "validation_error",
                "detail": "story_scene_id 与 location_id 必填"}
    if not expected_doc:
        return {"ok": False, "operation": "scene.instance.prepare",
                "error": "expected_document_revision_required",
                "detail": "必传 expected_document_revision（scene.inventory 的 "
                          "document_revision）"}
    pdir = rt._resolve_project_dir(project)
    svc = _svc(pdir, project)
    r = await svc.create_scene_instance(story_scene_id=story_scene_id,
                                        location_id=location_id,
                                        expected_document_revision=expected_doc)
    out = _scene_write_receipt(r, "scene.instance.prepare", kind="scene_instance",
                               entity_id=str(r.get("target") or ""))
    out["story_scene_id"] = story_scene_id
    if out.get("ok"):
        out["scene_instance_id"] = out.get("target")
        out["op_summary"] = (f"故事段 {story_scene_id} 已绑定 Location {location_id}"
                             f"（instance={out['scene_instance_id']}）")
    elif out.get("error") == "instance_already_active":
        # §13：不静默创建第二个——把现有 instance 交给 Agent 决定复用
        existing = out.get("existing_scene_instance_ids") or []
        out["op_summary"] = (f"故事段 {story_scene_id} 已有 active instance "
                             f"（{', '.join(existing)}）——请 inspect 后决定复用，"
                             "不要重复创建")
        out["note"] = ("该故事段已绑定场景。复用现有 instance（scene.state.update 改状态/"
                       "scene.instance.prepare 不需要重跑）；确需换绑 Location 时向用户"
                       "说明理由，绝不静默重建。")
    return out


async def _execute_scene_state_update(rt: AgentRuntime, project: str, arguments: str,
                                      instruction, message_id) -> dict:
    """scene.state.update（28.md §14/§15）：SceneState patch（整 dict 替换语义）。
    缺 Prop → 结构化 resource_prerequisite（story_scene_id + 缺什么 + 为什么 + 目标字段），
    零 Prop 越权创建、零伪造引用。"""
    args = _parse_args(arguments)
    instance_id = str(args.get("instance_id") or "").strip()
    expected = str(args.get("expected_revision") or "").strip()
    if not instance_id:
        return {"ok": False, "operation": "scene.state.update",
                "error": "validation_error", "detail": "instance_id 必填"}
    if not expected:
        return {"ok": False, "operation": "scene.state.update",
                "error": "expected_revision_required",
                "detail": "必传 expected_revision（先 scene.production.inspect 拿该 "
                          "instance 的 revision）"}
    patch: Dict[str, Any] = {}
    for k in ("time_of_day", "weather", "lighting", "environment_conditions", "notes"):
        if args.get(k) is not None:
            patch[k] = str(args[k])
    for k in ("anchor_states", "prop_placements"):
        if args.get(k) is not None:
            if not isinstance(args[k], dict):
                return {"ok": False, "operation": "scene.state.update",
                        "error": "validation_error", "detail": f"{k} 必须是对象"}
            patch[k] = args[k]
    pdir = rt._resolve_project_dir(project)
    svc = _svc(pdir, project)
    r = await svc.patch_scene_state(instance_id, patch, expected_revision=expected)
    out = _scene_write_receipt(r, "scene.state.update", kind="scene_instance",
                               entity_id=instance_id)
    out["scene_instance_id"] = instance_id
    if out.get("error") == "prop_not_found" and not out.get("ok"):
        # §15：结构化 resource_prerequisite（Producer 据 §25 路由 Resource Agent）
        missing = []
        props = patch.get("prop_placements") or {}
        story_scene_id = ""
        try:
            _inst = _scene_system_read(pdir).scene_instances.get(instance_id)
            if _inst is not None:
                story_scene_id = _inst.story_scene_id
        except Exception:   # noqa: BLE001 - 域信息尽力补全，不影响主回执
            pass
        for pid, placement in props.items():
            detail = str(out.get("detail") or "")
            if pid in detail or len(props) == 1:
                missing.append({
                    "prop_id": pid,
                    "requirement": {
                        "anchor_id": (placement or {}).get("anchor_id", ""),
                        "state": (placement or {}).get("state", ""),
                        "note": (placement or {}).get("note", ""),
                    },
                    "why": "SceneState.prop_placements 引用的 Prop 在 Resources 中不存在",
                    "target_field": f"SceneState.prop_placements[{pid}]",
                })
        out["prerequisite_warnings"] = [{
            "code": "resource_prerequisite",
            "story_scene_id": story_scene_id,
            "detail": f"prop_placements 引用不存在的 Prop: {sorted(props)}；"
                      f"由 Producer 委派 Resource Agent 登记 Prop 后重试。",
            "required_capability": "resource_agent",
            "missing_props": missing,
        }]
        out["missing_props"] = missing
        out["note"] = ("Prop 不存在，本次 state 更新未写入。按 resource_prerequisite 如实"
                       "上报（不要伪造 prop id、不要自己创建 Prop）；Producer 会委派 "
                       "Resource Agent。")
    return out


# ============ G 域写工具（28.md §16，Commit G）：scene.state_view.prepare ============
SCENE_STATE_VIEW_PREPARE_TOOL = {
    "type": "function",
    "function": {
        "name": "scene.state_view.prepare",
        "description": ("高层 SceneInstanceView 生产准备（一次调用 = 一次正式生成尝试）："
                        "LocationView（base 视觉母版）+ 当前 SceneState + 项目视觉定义 + "
                        "相关 Prop 参考 → 生成该故事段此刻的场景状态图 → 正式 vision 审查 "
                        "→ approval_mode=approve_if_pass 且真实 pass 时自动批准。"
                        "不传 scene_instance_view_id = 新建（expected_document_revision "
                        "必传；同一 (instance, base view) 对只能有一个 Sciv，已存在会被拒"
                        "并返回现有 id——复用它重新生成）；传 id = 重新生成（"
                        "expected_revision 传该 Sciv 的 revision）。前置：base "
                        "LocationView 必须 approved（系统 fail closed 校验）。"
                        "SceneState 改变后原 Sciv 会 stale——改状态后记得重新生成。"
                        "同一语义输入连续 needs_fix 会被 review_retry_exhausted 拦住。"),
        "parameters": {
            "type": "object",
            "properties": {
                "scene_instance_id": {"type": "string", "description": "SceneInstance id（必传）。"},
                "base_location_view_id": {"type": "string",
                                          "description": "base LocationView id（必须已 approved）。"},
                "scene_instance_view_id": {"type": "string",
                                           "description": "已有 Sciv id（重新生成时传；新建省略）。"},
                "label": {"type": "string", "description": "视角备注名。"},
                "expected_document_revision": {"type": "string",
                                               "description": "新建时必传：document_revision。"},
                "expected_revision": {"type": "string",
                                      "description": "重新生成时必传：该 Sciv 当前 revision。"},
                "candidate_count": {"type": "integer", "description": "候选图数量 1~2（默认 1）。"},
                "approval_mode": {"type": "string",
                                  "enum": ["review_only", "approve_if_pass"],
                                  "description": "review_only=只生成审查；approve_if_pass=真实 pass 自动批准。"},
            },
            "required": ["scene_instance_id", "base_location_view_id", "approval_mode"],
        },
    },
}


async def _execute_scene_state_view_prepare(rt: AgentRuntime, project: str, arguments: str,
                                            instruction, message_id, *,
                                            event_cb=None, cancel_event=None) -> dict:
    """scene.state_view.prepare（28.md §16）：SceneInstanceView 高层准备。
    内部链路（全走正式 service，Agent 不拼 provider request）：
    create Sciv（或复用已有）→ generate（base approved fail-closed + SceneState + props
    + profile 由 service 编译）→ vision review → optional approve。"""
    args = _parse_args(arguments)
    scene_instance_id = str(args.get("scene_instance_id") or "").strip()
    base_view_id = str(args.get("base_location_view_id") or "").strip()
    sciv_id = str(args.get("scene_instance_view_id") or "").strip()
    approval_mode = str(args.get("approval_mode") or "review_only").strip()
    label = str(args.get("label") or "").strip()
    if not scene_instance_id or not base_view_id:
        return {"ok": False, "operation": "scene.state_view.prepare",
                "error": "validation_error",
                "detail": "scene_instance_id 与 base_location_view_id 必填"}
    if approval_mode not in ("review_only", "approve_if_pass"):
        return {"ok": False, "operation": "scene.state_view.prepare",
                "error": "validation_error",
                "detail": "approval_mode 只支持 review_only|approve_if_pass"}
    pdir = rt._resolve_project_dir(project)
    vs = _visual_svc(pdir, project)
    started = time.time()

    def _prog(phase: str, summary: str) -> None:
        if event_cb:
            event_cb({"type": "tool.progress", "phase": phase, "summary": summary[:160]})

    from .review_retry_guard import (review_basis_sig, review_retry_check,
                                     review_retry_record)
    # basis：base view + instance（SceneState 嵌入 instance → instance revision 即状态
    # 指纹——状态真改变 → 新 basis 放行；同状态重试 → 计数）。
    try:
        _inst = _scene_system_read(pdir).scene_instances.get(scene_instance_id)
        from src.scene_system.models import scene_instance_revision
        inst_revision = (scene_instance_revision(_inst)
                         if _inst is not None else "")
    except Exception:   # noqa: BLE001
        inst_revision = ""
    basis_sig = review_basis_sig(scene_instance_id=scene_instance_id,
                                 base_location_view_id=base_view_id,
                                 label=label, instance_revision=inst_revision,
                                 intent="scene_instance_view")
    try:
        if sciv_id:
            expected = str(args.get("expected_revision") or "").strip()
            if not expected:
                return {"ok": False, "operation": "scene.state_view.prepare",
                        "error": "expected_revision_required",
                        "detail": "重新生成必须带 expected_revision（先 "
                                  "scene.production.inspect 拿该 Sciv 的 revision）",
                        "scene_instance_view_id": sciv_id,
                        "scene_instance_id": scene_instance_id}
            blocked = review_retry_check(
                project, message_id, sciv_id, basis_sig,
                "scene.state_view.prepare",
                change_hints=(
                    "①先 scene.state.update 真正改变 SceneState（天气/时间/anchor 状态/"
                    "prop）；②更换 base LocationView；③修改 anchor/prop 描述后再改状态；"
                    "④或停止并如实向用户报告模型边界（MODEL_LIMITATION）。"))
            if blocked is not None:
                blocked["scene_instance_view_id"] = sciv_id
                blocked["scene_instance_id"] = scene_instance_id
                blocked["note"] = ("Sciv 未变；拦截的是重复生成。"
                                   + (blocked.get("note") or ""))
                return blocked
        else:
            expected_doc = str(args.get("expected_document_revision") or "").strip()
            if not expected_doc:
                return {"ok": False, "operation": "scene.state_view.prepare",
                        "error": "expected_document_revision_required",
                        "detail": "新建 Sciv 必须带 expected_document_revision",
                        "scene_instance_id": scene_instance_id}
            _prog("sciv.create", "创建 SceneInstanceView")
            r_def = await vs.create_scene_instance_view(
                scene_instance_id=scene_instance_id,
                location_view_id=base_view_id, label=label,
                expected_document_revision=expected_doc)
            if not r_def.get("ok"):
                out = _scene_write_receipt(r_def, "scene.state_view.prepare",
                                           kind="scene_instance_view",
                                           entity_id=str(r_def.get("target")
                                                         or r_def.get(
                                                             "scene_instance_view_id")
                                                         or ""))
                out["scene_instance_id"] = scene_instance_id
                if out.get("error") == "scene_instance_view_already_exists":
                    out["scene_instance_view_id"] = out.get("scene_instance_view_id") \
                        or r_def.get("scene_instance_view_id")
                    out["note"] = ("该 (instance, base view) 对已有 Sciv（稳定身份）。"
                                   "复用返回的 scene_instance_view_id 重新生成（带其 "
                                   "revision），不要新建。")
                return out
            sciv_id = r_def.get("target")
            # 新建路径：guard 在生成前（key=sciv_id 稳定）
            blocked = review_retry_check(
                project, message_id, sciv_id, basis_sig,
                "scene.state_view.prepare",
                change_hints=(
                    "①先 scene.state.update 真正改变 SceneState；②更换 base "
                    "LocationView；③或停止并如实向用户报告模型边界。"))
            if blocked is not None:
                blocked["scene_instance_view_id"] = sciv_id
                blocked["scene_instance_id"] = scene_instance_id
                return blocked

        # ---- 生成（一次正式生成尝试；base approved/props/profile 由 service fail-closed）----
        candidate_count = args.get("candidate_count") or 1
        try:
            candidate_count = int(candidate_count)
        except (TypeError, ValueError):
            candidate_count = 1
        # generate 前取该 sciv 当前 revision（新建后为初始 revision）
        doc_now = _scene_system_read(pdir)
        sciv_now = doc_now.scene_instance_views.get(sciv_id)
        if sciv_now is None:
            return {"ok": False, "operation": "scene.state_view.prepare",
                    "error": "sciv_not_found", "detail": f"Sciv 不存在: {sciv_id}",
                    "scene_instance_view_id": sciv_id}
        from src.scene_system.models import scene_instance_view_revision
        _prog("sciv.generate", f"生成场景状态候选图 ×{max(1, min(candidate_count, 2))}"
                               "（约 1 分钟）")
        # Phase D §2：图片质量档跟随项目生产设置（project.style.size_tier；缺省 1K）
        _sciv_size_tier = (ProjectStore(pdir).load().style.size_tier or "1K")
        r_gen = await vs.generate_scene_instance_view(
            sciv_id, expected_revision=scene_instance_view_revision(sciv_now),
            candidate_count=max(1, min(candidate_count, 2)),
            size_tier=_sciv_size_tier)
        if not r_gen.get("ok"):
            out = _scene_write_receipt(r_gen, "scene.state_view.prepare",
                                       kind="scene_instance_view", entity_id=sciv_id)
            out.update({"scene_instance_view_id": sciv_id,
                        "scene_instance_id": scene_instance_id,
                        "stage": "generate"})
            if out.get("error") == "prop_missing":
                out.setdefault("prerequisite_warnings", [{
                    "code": "resource_prerequisite",
                    "story_scene_id": _inst.story_scene_id if _inst else "",
                    "detail": "可见 anchor 需要 Prop 参考但缺失——委派 Resource Agent。",
                    "required_capability": "resource_agent"}])
            return out
        # ---- 审查 ----
        _prog("sciv.review", "vision 审查场景状态候选图")
        from src.scene_system.instance_view_review import \
            review_scene_instance_view_candidates as _sciv_review
        r_review = await _sciv_review(pdir, sciv_id)
        candidates = [c for c in (r_review.get("candidate_reviews") or [])
                      if c.get("status") == "reviewed"]
        best = next((c for c in candidates if c.get("overall") == "pass"), None)
        if best is None and candidates:
            best = candidates[0]
        review_overall = (best or {}).get("overall")
        review_retry_record(project, message_id, sciv_id, basis_sig, review_overall)

        # ---- optional approve ----
        approved = False
        approve_error = None
        doc_gen = _scene_system_read(pdir)
        sciv_gen = doc_gen.scene_instance_views.get(sciv_id)
        if approval_mode == "approve_if_pass" and review_overall == "pass" and best \
                and sciv_gen is not None:
            from src.resources.service import ResourceService
            res_rev = ResourceService(pdir, project).document_revision()
            _prog("sciv.approve", "审查 pass → 批准")
            r_ap = await vs.approve_scene_instance_view(
                sciv_id, candidate_index=best["candidate_index"],
                expected_revision=scene_instance_view_revision(sciv_gen),
                expected_resources_document_revision=res_rev)
            if r_ap.get("ok"):
                approved = True
            else:
                approve_error = {"error": r_ap.get("error"),
                                 "detail": (r_ap.get("detail") or "")[:200]}

        verified_any = bool((r_gen.get("ok") and r_gen.get("verified")) or approved)
        doc_final = _scene_system_read(pdir)
        sciv_final = doc_final.scene_instance_views.get(sciv_id)
        from src.scene_system.models import scene_instance_view_revision
        return {"ok": True, "operation": "scene.state_view.prepare",
                "scene_instance_view_id": sciv_id,
                "scene_instance_id": scene_instance_id,
                "base_location_view_id": base_view_id,
                "revision": (scene_instance_view_revision(sciv_final)
                             if sciv_final is not None else None),
                "generation_id": r_gen.get("generation_id"),
                "review_overall": review_overall,
                "candidate_reviews": [
                    {"candidate_index": c.get("candidate_index"),
                     "overall": c.get("overall"),
                     "view_consistency": (c.get("dimensions") or {}).get("view_consistency"),
                     "camera_observed_zone": c.get("camera_observed_zone"),
                     "scene_state_items": c.get("scene_state_items"),
                     "per_prop": c.get("per_prop"),
                     "summary": (c.get("summary") or "")[:200]}
                    for c in candidates],
                "approval_mode": approval_mode,
                "approved": approved,
                "approve_error": approve_error,
                "verified": verified_any,
                "changed_resources": ([{"kind": "scene_instance_view", "id": sciv_id}]
                                      if verified_any else []),
                "elapsed_s": round(time.time() - started, 1),
                "op_summary": (f"SceneInstanceView {sciv_id} 生成完成"
                               f"（审查={review_overall}"
                               f"{'，已批准' if approved else ''}）"),
                "note": (
                    "审查未 pass：候选未批准。按 candidate_reviews 的真实原因（逐状态项/"
                    "逐 prop）先修正 SceneState 或输入再试；不要原样重试。"
                    if (not approved and review_overall not in ("pass", None)) else
                    "review_only 模式：候选未经批准，Director 不可消费；用户显式批准（UI）"
                    "或以 approve_if_pass 重新生成。"
                    if not approved else
                    "Sciv 已生成并批准（approved 场景参考可被 Director 消费/绑定）。")}
    finally:
        pass


# ============ D-G 域写工具 schema 与执行器（后续 Commit 逐个落地）============
SCENE_DESIGNER_TOOLS: list = [SCENE_INVENTORY_TOOL, SCENE_PRODUCTION_INSPECT_TOOL,
                              SCENE_LOCATION_UPSERT_TOOL, SCENE_ANCHOR_UPSERT_TOOL,
                              SCENE_LAYOUT_SET_TOOL, SCENE_VIEW_PREPARE_TOOL,
                              SCENE_INSTANCE_PREPARE_TOOL, SCENE_INSTANCE_DELETE_TOOL,
                              SCENE_STATE_UPDATE_TOOL,
                              SCENE_STATE_VIEW_PREPARE_TOOL,
                              # Skill System V1（40.md）：生产技能发现/加载（只读）
                              *SKILL_TOOLS]

TOOL_EXECUTORS["scene.inventory"] = _execute_scene_inventory
TOOL_EXECUTORS["scene.production.inspect"] = _execute_scene_production_inspect
TOOL_EXECUTORS["scene.location.upsert"] = _execute_scene_location_upsert
TOOL_EXECUTORS["scene.anchor.upsert"] = _execute_scene_anchor_upsert
TOOL_EXECUTORS["scene.layout.set"] = _execute_scene_layout_set
TOOL_EXECUTORS["scene.view.prepare"] = _execute_scene_view_prepare
TOOL_EXECUTORS["scene.instance.prepare"] = _execute_scene_instance_prepare
TOOL_EXECUTORS["scene.instance.delete"] = _execute_scene_instance_delete
TOOL_EXECUTORS["scene.state.update"] = _execute_scene_state_update
TOOL_EXECUTORS["scene.state_view.prepare"] = _execute_scene_state_view_prepare
# Skill System V1（40.md）：skill.list/load/read_resource（只读）
TOOL_EXECUTORS.update(make_skill_executors("scene_designer"))
TOOL_LABELS["scene.inventory"] = "列出场景系统地图"
TOOL_LABELS["scene.production.inspect"] = "检查场景生产准备"
TOOL_LABELS["scene.location.upsert"] = "创建/更新地点"
TOOL_LABELS["scene.anchor.upsert"] = "创建/更新空间锚点"
TOOL_LABELS["scene.layout.set"] = "设置平面布局"
TOOL_LABELS["scene.view.prepare"] = "准备 LocationView（生成+审查+按档批准）"
TOOL_LABELS["scene.instance.prepare"] = "绑定故事段与地点"
TOOL_LABELS["scene.state.update"] = "更新场景状态"
TOOL_LABELS["scene.state_view.prepare"] = "准备 SceneInstanceView（生成+审查+按档批准）"
# 长任务 executor 标注（tool-loop 据此传 event_cb/cancel_event）
_execute_scene_view_prepare._long_task = True
_execute_scene_state_view_prepare._long_task = True

# 写工具 → 锁域（28.md §5：单写域 scene_system）
WRITE_TOOL_DOMAIN.update({
    "scene.location.upsert": RESOURCE_SCENE_SYSTEM,
    "scene.anchor.upsert": RESOURCE_SCENE_SYSTEM,
    "scene.layout.set": RESOURCE_SCENE_SYSTEM,
    "scene.view.prepare": RESOURCE_SCENE_SYSTEM,
    "scene.instance.prepare": RESOURCE_SCENE_SYSTEM,
    "scene.state.update": RESOURCE_SCENE_SYSTEM,
    "scene.state_view.prepare": RESOURCE_SCENE_SYSTEM,
})


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
            # 场景域加固（C2 真机 §32 实证）：指令常含「不要修改故事和分镜」类否定式
            # 约束——_CHAT_TURN_RE 的「不要修改」会把它误判为聊天轮而解除闸门武装。
            # 显式动作词存在时一律视为执行轮（宁可误武装，不可漏放幻觉）。
            real_changes = [r for r in receipts
                            if r.get("ok") and r.get("verified") and not r.get("unchanged")]
            chat_turn = (write_attempts == 0 and _is_chat_turn(instruction)
                         and not _SCENE_EXPLICIT_ACTION_RE.search(instruction or ""))
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
            if (is_write_tool and executor is not None
                    and not (str(fn.get("arguments") or "").strip().strip('"'))):
                # 空参数写调用：给可行动的错误（模型偶发把 arguments 发空——
                # 单独指出参数载体，避免模型在 validation_error 上空转）。
                obs = {"ok": False, "operation": name, "code": "empty_arguments",
                       "error": ("工具调用的 arguments 为空。请把参数以 JSON 对象写在 "
                                 f"arguments 字段里再调用 {name}，例如："
                                 "{{\"story_scene_id\": \"s02\", \"location_id\": \"loc_xxx\", "
                                 "\"expected_document_revision\": \"<当前 revision>\"}}"
                                 "（以该工具 schema 的必填字段为准）。")}
            elif (executor is not None and name == "web.search"
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

# 显式场景动作词（真相闸门武装：指令含任一 → 非聊天轮，即使带「不要修改…」否定约束）。
_SCENE_EXPLICIT_ACTION_RE = re.compile(
    r"准备|更新|创建|生成|绑定|摆放|放进|登记|批准|重建|补充|完善|清理|修改场景|"
    r"scene\.(location|anchor|layout|view|instance|state|state_view)\.(upsert|set|prepare|update)")


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
        # Skill System V1（40.md §七）：turn 入口确定性 trigger hint（不落盘）
        _skill_hint = _skill_activation_hint("scene_designer", content, pdir)
        messages.append({"role": "user", "content":
                         (_skill_hint + "\n\n" + content) if _skill_hint else content})

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
            "detail": (str(r.get("detail"))[:200] if r.get("detail") else None),
            "code": r.get("code"),
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
            # Skill System V1（40.md §十三）：本 turn 技能激活/加载证据
            "skills": _get_skill_runtime().collect_and_reset("scene_designer"),
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
