# src/agents/skill_system/tools.py
"""Skill 工具面（模块化挂载，模式与 director_calibration.py 一致）。

三个只读工具：
- skill.list()                    列出本 Agent 可见技能目录（L1 工具化镜像）
- skill.load(skill_id)            L2：加载 SKILL.md + 本 Agent role 专属指令
- skill.read_resource(skill_id, resource)  L3：按需加载 supporting resource

接线方式（各 Agent 文件内，见 mount_skill_tools 工厂说明）：executor 通过
`make_skill_executors(agent_id)` 绑定 agent_id（现有 executor 签名
(rt, project, arguments, instruction, message_id) 无 agent 维度，闭包注入）。

权限模型（40.md §十一，结构性成立）：skill.* 是只读工具——加载文本不产生任何
canonical 写、不占写槽；真实权限层是各 Agent 物理工具面，skill 无法改变它。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Tuple

from ..runtime import AgentError, AgentRuntime
from .runtime import get_skill_runtime


def _parse_args(arguments: str) -> dict:
    """工具参数 JSON 解析（本地实现，避免反向 import 各 Agent 模块造成环）。"""
    try:
        return json.loads(arguments) if arguments else {}
    except Exception as e:
        raise AgentError("bad_arguments", f"工具参数不是合法 JSON：{e}")

SKILL_LIST_TOOL = {
    "type": "function",
    "function": {
        "name": "skill.list",
        "description": (
            "列出你当前可用的生产技能目录（只读）。每项含 skill_id / 名称 / 一句话"
            "适用情境 / 触发线索。判断任务与某技能相关后，先 skill.load 加载完整"
            "工作方法再执行。"),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

SKILL_LOAD_TOOL = {
    "type": "function",
    "function": {
        "name": "skill.load",
        "description": (
            "加载一个生产技能的完整工作方法（只读）。返回：共享工作流（SKILL.md）+"
            "你在该技能中的角色专属指令 + 权限边界声明 + capability 对账 + 其余"
            "supporting resources 清单。系统提示某任务适用技能、或你判断任务与"
            "技能目录某项相关时，【先加载再执行】。加载本身不消耗生产额度。"),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_id": {"type": "string",
                             "description": "技能 id（skill.list / 技能目录中的 skill_id）"},
            },
            "required": ["skill_id"],
        },
    },
}

SKILL_READ_RESOURCE_TOOL = {
    "type": "function",
    "function": {
        "name": "skill.read_resource",
        "description": (
            "读取某技能的一个 supporting resource（只读，L3）。资源名来自 "
            "skill.load 返回的 available_resources（如其他角色的协作指令、"
            "失败案例、参考标准）。仅在你需要该资源时加载，不要默认全读。"),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_id": {"type": "string",
                             "description": "技能 id。"},
                "resource": {"type": "string",
                             "description": ("资源相对路径，如 references/producer.md；"
                                             "必须来自 available_resources 清单。")},
            },
            "required": ["skill_id", "resource"],
        },
    },
}

SKILL_TOOLS = [SKILL_LIST_TOOL, SKILL_LOAD_TOOL, SKILL_READ_RESOURCE_TOOL]
SKILL_TOOL_NAMES = frozenset(
    t["function"]["name"] for t in SKILL_TOOLS)  # skill.list / skill.load / skill.read_resource


def agent_tool_names(agent_id: str) -> Optional[set]:
    """各 Agent 真实工具面名册（capability 对账用）。lazy import 避免循环依赖；
    未知 agent 返回 None（对账跳过，物理工具面仍是真权限层）。"""
    try:
        if agent_id == "director":
            from ..director import DIRECTOR_TOOLS
            return {t["function"]["name"] for t in DIRECTOR_TOOLS}
        if agent_id == "script_writer":
            from ..script_writer import SCRIPT_WRITER_TOOLS
            return {t["function"]["name"] for t in SCRIPT_WRITER_TOOLS}
        if agent_id == "character_designer":
            from ..character_designer import CHARACTER_DESIGNER_TOOLS
            return {t["function"]["name"] for t in CHARACTER_DESIGNER_TOOLS}
        if agent_id == "scene_designer":
            from ..scene_designer import SCENE_DESIGNER_TOOLS
            return {t["function"]["name"] for t in SCENE_DESIGNER_TOOLS}
        if agent_id == "producer":
            from ..producer import _build_producer_tools
            offered, _ = _build_producer_tools()
            return {t["function"]["name"] for t in offered}
    except Exception:
        return None
    return None


def _resolve_pdir(rt: AgentRuntime, project: str) -> Optional[Path]:
    try:
        return rt._resolve_project_dir(project)
    except Exception:
        return None


def make_skill_executors(agent_id: str) -> Dict[str, object]:
    """构建绑定 agent_id 的 executor 表。各 Agent 文件接线示例：

        from .skill_system.tools import SKILL_TOOLS, make_skill_executors
        _SKILL_EXECUTORS = make_skill_executors("director")
        DIRECTOR_TOOLS = DIRECTOR_TOOLS + SKILL_TOOLS        # 工具面追加
        TOOL_EXECUTORS.update(_SKILL_EXECUTORS)              # executor 合并

    （与 director_calibration 的接线模式完全一致。）
    """
    srt = get_skill_runtime()

    async def _execute_skill_list(rt: AgentRuntime, project: str,
                                  arguments: str, instruction, message_id) -> dict:
        return srt.list_for(agent_id)

    async def _execute_skill_load(rt: AgentRuntime, project: str,
                                  arguments: str, instruction, message_id) -> dict:
        args = _parse_args(arguments)
        skill_id = (args.get("skill_id") or "").strip()
        if not skill_id:
            raise AgentError("bad_arguments", "缺少 skill_id")
        pdir = _resolve_pdir(rt, project)
        result = srt.load(
            agent_id, skill_id, project_dir=pdir,
            agent_tools=agent_tool_names(agent_id),
            task_snippet=str(instruction or "")[:200])
        if not result.get("ok"):
            # fail closed 结构化失败——不 raise（AgentError 会被上转成通用错误，
            # 丢失 code）；skill 不可加载是预期分支不是系统故障。
            return result
        return result

    async def _execute_skill_read_resource(rt: AgentRuntime, project: str,
                                           arguments: str, instruction,
                                           message_id) -> dict:
        args = _parse_args(arguments)
        skill_id = (args.get("skill_id") or "").strip()
        resource = (args.get("resource") or "").strip()
        if not skill_id or not resource:
            raise AgentError("bad_arguments", "缺少 skill_id 或 resource")
        pdir = _resolve_pdir(rt, project)
        result = srt.read_resource(agent_id, skill_id, resource, project_dir=pdir,
                                   agent_tools=None)
        return result

    return {
        "skill.list": _execute_skill_list,
        "skill.load": _execute_skill_load,
        "skill.read_resource": _execute_skill_read_resource,
    }


# ============ turn 入口辅助（discovery hint；不落盘）============

def activation_hint(agent_id: str, content: str,
                    project_dir: Optional[Path] = None) -> Optional[str]:
    """turn 入口确定性 hint（cheap/确定/可 trace）。Agent 接线示例：

        hint = skill_system.tools.activation_hint("director", content, pdir)
        final_content = (hint + "\\n\\n" + content) if hint else content
        # messages.append({"role": "user", "content": final_content})
        # ——session 落盘的仍是原 content（user_msg 在 turn 开头已 append）。

    注意：本函数不修改 messages/session，只返回提示文本。"""
    return get_skill_runtime().activation_hint(agent_id, content, project_dir)
