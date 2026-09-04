# src/agents/producer.py
# Producer V1 Phase 1 — Specialist Bridge Core.
#
# 把 Producer 从「只读项目问答器 + 手工委派表单」升级为能自主调用 Script Writer /
# Character Designer / Director 真实 *_turn、并据上一步结果自然衔接下一步的项目级
# 总操作 Agent。Producer 始终持有用户对话控制权；不把对话 handoff 给 Specialist。
#
# === 决策理由（§5.2）：为何不复用 director._run_tool_loop 的 tools_whitelist 模式 ===
# 该模式下 zero-read gate 照常上膛，而 Producer 汇总 Task Result 时必然引用
# shot_id/scene_id（"Director 修改了 s05_c01…"）——其事实来源是 Runtime 包装的
# Task Result（比读工具更强）而非本轮读调用 → 必然误触发 zero-read nudge（与长程
# 测试已知 NON-BLOCKER "false-failure truth gate" 同族）。故 Producer 用自己的轻量
# 镜像 loop：保留 renewable window / cancel / SSE 事件 / trace / receipts(delegate
# 回执)/telemetry；【去掉】Specialist 的写域真相闸门、zero-read/zero-write nudge、
# 搜索预算（Producer 无 web.search）、批量降级。窗口常量 1024，self-check nudge
# 文本镜像 Specialist 的 _WINDOW_SELF_CHECK_NUDGE（每文件一份镜像，仓库惯例）。
# 无任何总步数/总时长上限（不设总续期次数/总步数/总墙钟硬上限）。
#
# === Authority 硬边界（§3，物理保证非提示词约束）===
# Producer 工具面里【根本不存在】story/character/shot 写工具与图/视频生成工具——
# 只有 read executors + 3 个 delegate 工具。Story→SW / Character→CD / Shot→Director
# 所有权由工具面缺失直接保证；Producer 永远不是第四个 canonical writer。
#
# Import 纪律：本文件只 import src.core.* / .models / .tasks / .project_events /
# .turn_registry / 标准库。Specialist 模块（script_writer/character_designer/director）
# 一律 lazy import（在函数内），避免本模块 import 时拖入全部 Specialist 与循环风险。
from __future__ import annotations
import asyncio
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from src.core.atomic import atomic_write_json
from src.core.store import ProjectStore
from .models import (AgentMessage, AgentTask, AgentOp, ResultStatus,
                     ScopeType, TaskStatus)
from .tasks import AgentTaskStore
from .project_events import (get_project_bus, emit_changed,
                             RESOURCE_STORY, RESOURCE_CHARACTER, RESOURCE_STORYBOARD)
from . import turn_registry

log = logging.getLogger("producer")

# ============ 常量 ============
DEFAULT_MAX_TOOL_STEPS = 1024

# 镜像 director._WINDOW_SELF_CHECK_NUDGE（每文件一份，仓库惯例；renewable window 边界注入）。
_WINDOW_SELF_CHECK_NUDGE = (
    "【系统提示·执行窗口】你已经连续执行了较长时间（当前执行窗口已用满）。请检查"
    "当前用户目标与最新的工具返回结果，然后自行判断：\n"
    "① 如果任务已经完成，正常结束并汇报结果即可；\n"
    "② 如果你确认自己正在重复相同的操作、没有取得任何新进展，并且已经没有合理的"
    "新方向，可以停止并如实说明卡点；\n"
    "③ 如果任务尚未完成且仍有明确、合理的下一步，请继续执行——不要因为执行步数"
    "而停止，系统会自动为你续接新的执行窗口。"
)

# Producer 的写工具集合 = 空（物理不持任何 canonical 写工具）。用于 _cb 锁接线判断。
PRODUCER_WRITE_OPERATIONS: set = set()

# delegate 工具名集合（长任务通道：走 event_cb + cancel_event）。
DELEGATE_TOOLS = {"script_writer.delegate", "character_designer.delegate",
                  "director.delegate"}

# agent_id → (resource, capability_id 前缀)
_SPECIALIST_RESOURCE = {
    "script_writer": RESOURCE_STORY,
    "character_designer": RESOURCE_CHARACTER,
    "director": RESOURCE_STORYBOARD,
}


# ============ Producer system prompt ============
PRODUCER_SYSTEM = """你是 AGNES 的 Producer Supervisor，项目级总协调者。你代替用户在三个专业 Agent 之间持续、可靠、自然地推进任务。

【你的身份】你不是第四个创作 Agent。你不写剧本、不设计角色、不改分镜、不生成图/视频。你是项目负责人：判断该找谁、衔接上一步结果、汇总汇报。

【三个专业 Agent 与其写入域（硬边界）】
- Script Writer（script_writer.delegate）→ 剧本正文 / 故事段结构（story_text / scenes）
- Character Designer（character_designer.delegate）→ 角色设定（characters）
- Director（director.delegate）→ 分镜（scenes[].shots）与分镜视觉资产

你的工具面里【根本没有】story/character/shot 的写工具与生成工具——这是物理保证，不是提示词约束。修改必须经对应 *.delegate 委派给 owner。

【只读工具（自己按需读，默认读轻量地图，确需判断才局部 read）】
project.inspect / story.list / story.read / script.read / character.list / character.read / shot.list / shot.read
事实自己读，专业判断问 Specialist，修改交给 owner。不要每轮全量读。

【如何委派（*.delegate 工具）】
调用时传：objective（必填，任务目标）/ situation（背景：发生了什么、用户为什么需要）/ constraints（必须遵守的约束）/ scope（作用范围）/ user_intent（用户原话）。
关键：你只告诉它「为什么找你 / 当前发生了什么 / 用户真正希望解决什么」。不要把 canonical 内容（剧本全文、分镜列表）复制进 envelope——Specialist 自己读最新 canonical。
user_intent 尽量用用户原话。

【自然衔接】上一步 delegate 返回 Task Result（status / summary / changed_refs / warnings）。据此判断下一步：
- 把上一步重要变化用一两句写进下一个 delegate 的 situation；
- Specialist 自己读 canonical，你别替它转述全文；
- 不要为了流程而调用不需要的 Agent：故事没问题就不调 SW，角色没受影响就不调 CD。

【不要固定顺序】按真实项目状态判断该找谁，可以跳过 SW/CD 直接找 Director。

【失败处理】Specialist 失败 ≠ workflow 失败。收到 failed/stopped Task Result 后自行判断：换 Agent / 回上游 / 问用户。绝不假装成功、绝不声称未发生的修改。

【真话纪律】你只能引用 Task Result 的 changed_refs 作为已发生的修改；changed_refs 为空即未改动。不要凭对话臆测进度。

【汇报】向用户汇报：你找了谁、为什么、做了什么、跳过了谁、发生了什么变化、还有什么残留 blocker。

当前 project：{project_name}（{scenes_count} 场景 / {shots_count} 镜头 / {characters_count} 角色）。
"""


def _build_producer_system(rt, project: str) -> str:
    try:
        pdir = rt._resolve_project_dir(project)
        proj = ProjectStore(pdir).load()
        n_shots = sum(len(sc.shots or []) for sc in (proj.scenes or []))
        return PRODUCER_SYSTEM.format(
            project_name=proj.name or "（未命名）",
            scenes_count=len(proj.scenes or []),
            shots_count=n_shots,
            characters_count=len(proj.characters or []))
    except Exception:
        return PRODUCER_SYSTEM.format(
            project_name=project, scenes_count="?", shots_count="?", characters_count="?")


def _refresh_producer_system(messages: list, rt, project: str) -> None:
    """每轮工具调用后刷新 system（项目元信息/计数）。镜像 director._refresh_system。"""
    try:
        if messages and messages[0].get("role") == "system":
            messages[0] = {"role": "system", "content": _build_producer_system(rt, project)}
        else:
            messages.insert(0, {"role": "system", "content": _build_producer_system(rt, project)})
    except Exception:
        pass


# ============ 工具 schema / executor ============
def _project_inspect_schema() -> dict:
    return {"type": "function", "function": {
        "name": "project.inspect",
        "description": "读取项目整体结构（场景/镜头/角色数、剧本长度）。轻量，默认先读它。",
        "parameters": {"type": "object", "properties": {}, "required": []}}}


def _delegate_schema(agent_id: str, label: str, desc: str) -> dict:
    return {"type": "function", "function": {
        "name": f"{agent_id}.delegate",
        "description": desc,
        "parameters": {
            "type": "object",
            "properties": {
                "objective": {"type": "string",
                              "description": "任务目标（必填）：希望该 Agent 完成什么"},
                "situation": {"type": "string",
                              "description": "背景情况：当前发生了什么、用户为什么需要这个、上一步谁做了什么"},
                "constraints": {"type": "array", "items": {"type": "string"},
                                 "description": "必须遵守的约束（逐条）"},
                "scope": {"type": "object",
                          "description": "作用范围（可选）：可含 scene_ids/character_names/free",
                          "properties": {
                              "scene_ids": {"type": "array", "items": {"type": "string"}},
                              "character_names": {"type": "array", "items": {"type": "string"}},
                              "free": {"type": "string"}}},
                "user_intent": {"type": "string",
                                "description": "用户原话意图（尽量用用户原话）"},
            },
            "required": ["objective"]}}}


def _build_producer_tools() -> tuple:
    """构建 (offered_tools, tool_executors)。
    read 工具 schema 从 owner 模块【过滤复用】（禁止发明第二套 schema）；executor 直接
    import owner 模块的 _execute_* 函数（签名一致 (rt, project, arguments, instruction, message_id)）。
    delegate schema 内联（Producer 专属）；executor 由 producer_turn 绑定 workflow 后注入。
    返回 (offered_tools, read_executors) —— delegate executor 不在此注入（需 workflow 闭包）。
    """
    from src.agents.script_writer import (SCRIPT_WRITER_TOOLS as _SW,
                                          _execute_story_list, _execute_story_read,
                                          _execute_script_read)
    from src.agents.character_designer import (CHARACTER_DESIGNER_TOOLS as _CD,
                                               _execute_character_list,
                                               _execute_character_read)
    from src.agents.director import (DIRECTOR_TOOLS as _DIR,
                                     _execute_shot_list, _execute_shot_read)
    sw_names = {"story.list", "story.read", "script.read"}
    cd_names = {"character.list", "character.read"}
    dir_names = {"shot.list", "shot.read"}
    read_schemas = ([t for t in _SW if t["function"]["name"] in sw_names]
                    + [t for t in _CD if t["function"]["name"] in cd_names]
                    + [t for t in _DIR if t["function"]["name"] in dir_names])
    read_executors = {
        "story.list": _execute_story_list, "story.read": _execute_story_read,
        "script.read": _execute_script_read,
        "character.list": _execute_character_list, "character.read": _execute_character_read,
        "shot.list": _execute_shot_list, "shot.read": _execute_shot_read,
    }
    delegate_schemas = [
        _delegate_schema("script_writer", "Script Writer",
                         "委派任务给 Script Writer（剧本正文 / 故事段结构）。它自己读 canonical；你只传目标/背景/约束/范围，不要复制剧本全文。"),
        _delegate_schema("character_designer", "Character Designer",
                         "委派任务给 Character Designer（角色设定 / 三视图）。它自己读 canonical；你只传目标/背景/约束/范围。"),
        _delegate_schema("director", "Director",
                         "委派任务给 Director（分镜结构 / 镜头 / 视觉资产）。它自己读 canonical；你只传目标/背景/约束/范围。"),
    ]
    offered = [_project_inspect_schema()] + read_schemas + delegate_schemas
    return offered, read_executors


_TOOL_LABELS = {
    "project.inspect": "查看项目概览",
    "story.list": "列出故事段", "story.read": "读取故事段", "script.read": "读取剧本正文",
    "character.list": "列出角色", "character.read": "读取角色",
    "shot.list": "列出镜头", "shot.read": "读取镜头",
    "script_writer.delegate": "委派 Script Writer",
    "character_designer.delegate": "委派 Character Designer",
    "director.delegate": "委派 Director",
}


def _tool_label(name: str) -> str:
    return _TOOL_LABELS.get(name, name)


def _tool_summary(name: str, obs: dict) -> str:
    if name in DELEGATE_TOOLS:
        st = obs.get("status") or ("完成" if obs.get("ok") else "失败")
        return f"委派 {obs.get('agent', '?')}：{st}"
    if obs.get("ok"):
        if obs.get("summary"):
            return obs["summary"][:120]
        return f"{_tool_label(name)}完成"
    return f"{_tool_label(name)}失败：{obs.get('error') or obs.get('code') or '失败'}"


# ============ project.inspect executor（Producer 专属，极简）============
def _execute_project_inspect(rt, project: str, arguments: str,
                              instruction, message_id) -> dict:
    try:
        pdir = rt._resolve_project_dir(project)
        proj = ProjectStore(pdir).load()
        scenes = len(proj.scenes or [])
        shots = sum(len(sc.shots or []) for sc in (proj.scenes or []))
        chars = len(proj.characters or [])
        return {"ok": True, "operation": "project.inspect", "project": proj.name,
                "scene_count": scenes, "shot_count": shots, "char_count": chars,
                "story_text_len": len(proj.story_text or ""),
                "summary": f"项目「{proj.name}」：{scenes} 场景，{shots} 镜头，{chars} 角色"}
    except Exception as e:
        return {"ok": False, "operation": "project.inspect",
                "error": f"项目读取失败: {e}"}


# ============ envelope 解析 / 渲染 ============
def _parse_json_loose(raw: str) -> dict:
    """容忍模型把 arguments 发成 JSON 字符串（双重编码）。"""
    if not raw:
        return {}
    try:
        v = json.loads(raw)
    except Exception:
        return {}
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return {}
    return v if isinstance(v, dict) else {}


def _parse_envelope(arguments: str) -> dict:
    env = _parse_json_loose(arguments)
    # 容忍字段大小写/拼写
    if "objective" not in env and "task" in env:
        env["objective"] = env["task"]
    return env


def _scope_text(scope: Optional[dict]) -> str:
    if not scope:
        return "（未指定，按需自行判断）"
    parts = []
    if scope.get("scene_ids"):
        parts.append("场景：" + ", ".join(scope["scene_ids"]))
    if scope.get("character_names"):
        parts.append("角色：" + ", ".join(scope["character_names"]))
    if scope.get("free"):
        parts.append(str(scope["free"]))
    return "；".join(parts) if parts else "（未指定，按需自行判断）"


def render_envelope(envelope: dict) -> str:
    """把 Task Envelope 渲染成 Specialist turn 的 user 消息（落进其持久 session）。
    渲染顺序：user_intent 放首位——script_writer_turn 内部 capture_brief_if_absent
    会把首条 ≥10 字符用户消息捕获为创作 brief，envelope 以用户原话开头可保证 brief
    即使被捕获也以用户话语为锚（已知可接受行为）。"""
    obj = (envelope.get("objective") or "").strip()
    sit = (envelope.get("situation") or "").strip()
    cons = envelope.get("constraints") or []
    if isinstance(cons, str):
        cons = [cons]
    ui = (envelope.get("user_intent") or "").strip()
    scope = envelope.get("scope")
    lines = ["【来自 Producer 的委派任务】"]
    if ui:
        lines.append(f"用户意图：{ui}")
    lines.append(f"任务目标：{obj or '（未明确）'}")
    lines.append(f"背景情况：{sit or '（无）'}")
    if cons:
        lines.append("必须遵守的约束：")
        for c in cons:
            lines.append(f"- {c}")
    else:
        lines.append("必须遵守的约束：（无）")
    lines.append(f"作用范围：{_scope_text(scope)}")
    return "\n".join(lines)


# ============ Task Result 包装（§6）============
def _derive_changed_refs(agent_id: str, receipts: list) -> dict:
    """只从真实 receipts 派生 changed_refs（绝不采信 LLM 自述）。
    扫描该 turn 的 receipts：scene_id/shot_id 字段 + created[].shot_id/scene_id +
    deleted.{shots,scenes} + results[].shot_id；CD 侧按 character_name。"""
    scenes: set = set()
    shots: set = set()
    characters: set = set()
    for r in receipts or []:
        if not (r.get("ok") and r.get("verified") and not r.get("unchanged")):
            continue
        if r.get("scene_id"):
            scenes.add(r["scene_id"])
        if r.get("shot_id"):
            shots.add(r["shot_id"])
        if r.get("character_name"):
            characters.add(r["character_name"])
        for c in (r.get("created") or []):
            if c.get("scene_id"):
                scenes.add(c["scene_id"])
            if c.get("shot_id"):
                shots.add(c["shot_id"])
        d = r.get("deleted") or {}
        for sid in (d.get("scenes") or []):
            scenes.add(sid)
        for sid in (d.get("shots") or []):
            shots.add(sid)
        for res in (r.get("results") or []):
            if res.get("shot_id"):
                shots.add(res["shot_id"])
    return {"scenes": sorted(scenes), "shots": sorted(shots), "characters": sorted(characters)}


def _derive_warnings(receipts: list) -> list:
    """receipts 的 ok=false 错误 + status，截前 10 条。"""
    out = []
    for r in receipts or []:
        if r.get("ok") is False:
            why = r.get("error") or r.get("code") or r.get("status") or "unknown"
            out.append(f"{r.get('operation', '?')}: {str(why)[:200]}")
            if len(out) >= 10:
                break
    return out


def _wrap_task_result(turn_result: Optional[dict], err: Optional[str],
                      task_id: str, workflow_id: str, agent_id: str,
                      sid: str) -> dict:
    """从真实 *_turn 返回包装 Task Result（§6）。verified/changed_refs 只从 receipts 派生。
    status 映射：turn message.status 为 None → completed；否则原样透传。"""
    tr = turn_result or {}
    receipts = tr.get("receipts") or []
    if err:
        status = "failed"
        summary = f"[委派执行异常: {err}]"
    else:
        msg = tr.get("message") or {}
        mstat = msg.get("status")
        status = "completed" if mstat is None else mstat  # None→completed；透传 stopped/failed/not_configured
        summary = (tr.get("assistant_message") or "").strip() or "[无回复]"
    changed = _derive_changed_refs(agent_id, receipts)
    return {
        "task_id": task_id, "workflow_id": workflow_id, "agent": agent_id,
        "status": status,
        "summary": summary[:800], "assistant_message": summary,
        "changed_refs": changed,
        "warnings": _derive_warnings(receipts),
        "verified": bool(changed["scenes"] or changed["shots"] or changed["characters"])
                    or status == "completed",
        "n_tool_calls": len(tr.get("trace") or []),
        "session_id": sid, "execution_ref": f"agent_task:{task_id}",
    }


# ============ 持久化：workflow + bridge 消息（§9）============
_PRODUCER_DIR_NAME = "producer"


def _producer_dir(pdir: Path) -> Path:
    d = pdir / "output" / "agents" / _PRODUCER_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


class ProducerWorkflowStore:
    """workflows.json：{version, workflows: {wf_id: {goal, status, root_task_id, task_ids[], ...}}}。
    V1 生命周期 per-producer-turn：turn 内首次 delegate 惰性创建，turn 结束置终态。"""

    def __init__(self, pdir: Path, project: str):
        self.path = _producer_dir(pdir) / "workflows.json"

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "workflows": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"version": 1, "workflows": {}}
        except Exception:
            return {"version": 1, "workflows": {}}

    def _save(self, data: dict) -> None:
        atomic_write_json(self.path, data or {"version": 1, "workflows": {}})

    def get_or_create_for_turn(self, goal: str) -> dict:
        data = self._load()
        wf_id = "wf_" + secrets.token_hex(6)
        wf = {"workflow_id": wf_id, "goal": (goal or "")[:200], "status": "running",
              "root_task_id": None, "task_ids": [],
              "created_at": time.time(), "updated_at": time.time()}
        data.setdefault("workflows", {})[wf_id] = wf
        self._save(data)
        return wf

    def add_task(self, wf_id: str, task_id: str) -> None:
        data = self._load()
        wf = data.get("workflows", {}).get(wf_id)
        if wf is None:
            return
        wf.setdefault("task_ids", []).append(task_id)
        if not wf.get("root_task_id"):
            wf["root_task_id"] = task_id
        wf["updated_at"] = time.time()
        self._save(data)

    def settle(self, wf_id: str, status: str) -> None:
        data = self._load()
        wf = data.get("workflows", {}).get(wf_id)
        if wf is None:
            return
        wf["status"] = status
        wf["updated_at"] = time.time()
        self._save(data)


def _append_bridge_messages(pdir: Path, project: str, rows: list) -> None:
    """bridge_messages.jsonl：每次委派两条（P→S + S→P），未来群聊直接渲染。"""
    path = _producer_dir(pdir) / "bridge_messages.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ============ delegate 执行器（§5.3，三个共用一个泛化实现）============
async def _delegate_specialist(rt, project: str, agent_id: str,
                               envelope: dict, *, event_cb, cancel_event,
                               prev_task_id: Optional[str],
                               workflow_id: str) -> dict:
    """委派给真实 Specialist *_turn。锁与 *.changed 事件在 Bridge 的 _cb 里复制
    （§5.3-⑤b）——直接调 *_turn 而不复制接线时前端所有页面都看不到锁与内容变化。"""
    # ① envelope 校验：objective 非空必填
    objective = (envelope.get("objective") or "").strip()
    if not objective:
        return {"ok": False, "operation": f"{agent_id}.delegate",
                "code": "missing_objective", "agent": agent_id,
                "error": "objective 必填（任务目标）",
                "fix_hint": "给出明确的任务目标 objective",
                "status": "failed", "task_id": None, "changed_refs": {"scenes": [], "shots": [], "characters": []}}

    # lazy import Specialist turn + write ops（避免本模块 import 时拖入）
    if agent_id == "script_writer":
        from src.agents.script_writer import script_writer_turn, WRITE_OPERATIONS
        turn_fn, writes = script_writer_turn, WRITE_OPERATIONS
    elif agent_id == "character_designer":
        from src.agents.character_designer import (character_designer_turn,
                                                   CHARACTER_WRITE_OPERATIONS)
        turn_fn, writes = character_designer_turn, CHARACTER_WRITE_OPERATIONS
    elif agent_id == "director":
        from src.agents.director import director_turn, DIRECTOR_WRITE_OPERATIONS
        turn_fn, writes = director_turn, DIRECTOR_WRITE_OPERATIONS
    else:
        return {"ok": False, "operation": "delegate", "code": "unknown_agent",
                "error": f"未知 Specialist: {agent_id}", "agent": agent_id,
                "status": "failed", "task_id": None,
                "changed_refs": {"scenes": [], "shots": [], "characters": []}}
    resource = _SPECIALIST_RESOURCE[agent_id]

    store = rt._session_store(project)
    # ② 真实 Specialist session：项目级长存会话（保持 identity/prompt/context 隔离）
    spec_session = store.get_or_create_conversation(agent_id)
    sid = spec_session.session_id

    # ③ 互斥：turn_registry.acquire（防止用户直开该 Specialist 聊天与 Bridge 并发驱动同一 session）
    if not turn_registry.acquire(project, sid, cancel_event):
        return {"ok": False, "operation": f"{agent_id}.delegate",
                "code": "specialist_busy", "agent": agent_id,
                "error": f"{agent_id} 会话已有进行中任务",
                "fix_hint": "稍后再试，或先停止该 Agent 的当前任务",
                "status": "failed", "task_id": None,
                "changed_refs": {"scenes": [], "shots": [], "characters": []}}

    # ④ AgentTask 创建（parent_task_id 链上前一 task 或留空为首 task）
    pdir = rt._resolve_project_dir(project)
    task_store = AgentTaskStore(pdir, project)
    task_id = "task_" + secrets.token_hex(6)
    task = AgentTask(
        task_id=task_id, project=project, requester_agent="producer",
        target_agent=agent_id, capability_id=f"{agent_id}.delegate",
        instruction=objective, scope_type=ScopeType.PROJECT.value, scope_id="",
        session_id=sid, operation=AgentOp.EXECUTE.value,
        parent_task_id=prev_task_id, status=TaskStatus.RUNNING.value)
    task_store.create(task)

    # ⑤ event_cb 包装 _cb（三层职责，逐条照抄 routers/agents.py 的 _cb 语义）
    bus = get_project_bus()
    state = {"locked": False}

    def _bridge_cb(ev: dict) -> None:
        t = ev.get("type")
        name = ev.get("name") or ""
        # b) 锁接线：tool.started 且 name ∈ 该 Specialist writes 且未锁 → bus.lock
        if t == "tool.started" and name in writes and not state["locked"]:
            bus.lock(project, resource, by=agent_id, session_id=sid)
            state["locked"] = True
        elif t == "tool.completed" and name in writes and ev.get("ok"):
            emit_changed(bus, project, resource, by=agent_id, session_id=sid, operation=name)
        # a) 转发 Specialist 全部 tool/reasoning/assistant 事件到 Producer SSE 流
        #    （用户在 Producer 页面能看到 Specialist 的工具时间线；step_id 冲突无害，
        #     前端按 call_id/type 渲染，这里附 agent 字段标注来源）
        if event_cb:
            fwd = dict(ev)
            fwd.setdefault("agent", agent_id)
            event_cb(fwd)

    # c) 桥级进度：delegation.started
    if event_cb:
        event_cb({"type": "tool.progress", "step_id": -1, "call_id": task_id,
                  "name": f"{agent_id}.delegate", "label": f"委派 {agent_id}",
                  "phase": "delegation.started", "objective": objective[:120],
                  "agent": agent_id})

    envelope_text = render_envelope(envelope)
    turn_result = None
    err = None
    try:
        # ⑥ result = await turn_fn(..., event_cb=_bridge_cb, cancel_event=cancel_event)
        #    cancel_event 直接透传（用户停 Producer → Specialist 协作停止）
        turn_result = await turn_fn(rt, project, sid, envelope_text,
                                    thinking=False, event_cb=_bridge_cb,
                                    cancel_event=cancel_event)
    except _TurnStopped:  # type: ignore[name-defined]  # 防御：Specialist 通常内部捕获
        err = "stopped"
    except Exception as e:
        log.exception("producer delegate %s 异常（project=%s）", agent_id, project)
        err = f"{type(e).__name__}: {e}"
    finally:
        # ⑦ finally：若已锁 bus.unlock；turn_registry.pop（异常路径也要）
        if state["locked"]:
            bus.unlock(project, resource)
            state["locked"] = False
        turn_registry.pop(project, sid)

    # ⑧ 包装 Task Result + 落盘 bridge 消息两条 + AgentTask 结算 + workflow 追加
    task_result = _wrap_task_result(turn_result, err, task_id, workflow_id, agent_id, sid)
    tr_status = task_result["status"]

    def _settle(t: AgentTask) -> None:
        t.status = (TaskStatus.COMPLETED.value if tr_status == "completed"
                    else TaskStatus.FAILED.value)  # stopped/failed/not_configured → failed（带 warning）
        if tr_status not in ("completed",):
            t.warnings = list(t.warnings) + [f"task_result_status={tr_status}"]
        t.result_summary = (task_result["summary"] or "")[:200]
        t.structured_result = task_result
        refs = []
        for sid_ in task_result["changed_refs"].get("scenes", []):
            refs.append({"type": "scene", "id": sid_})
        for sid_ in task_result["changed_refs"].get("shots", []):
            refs.append({"type": "shot", "id": sid_})
        for nm in task_result["changed_refs"].get("characters", []):
            refs.append({"type": "character", "id": nm})
        t.artifact_refs = refs
        t.error = (err or (None if tr_status == "completed" else tr_status))
        t.updated_at = time.time()

    task_store.update(task_id, _settle)

    # bridge 消息两条（P→S / S→P）
    ts = time.time()
    _append_bridge_messages(pdir, project, [
        {"ts": ts, "task_id": task_id, "workflow_id": workflow_id,
         "sender": "producer", "receiver": agent_id,
         "objective": objective, "situation": envelope.get("situation") or "",
         "constraints": envelope.get("constraints") or [],
         "scope": envelope.get("scope") or {}, "status": "dispatched"},
        {"ts": time.time(), "task_id": task_id, "sender": agent_id,
         "receiver": "producer", "status": tr_status,
         "summary": task_result["summary"][:400],
         "changed_refs": task_result["changed_refs"],
         "warnings": task_result["warnings"]},
    ])

    # c) delegation.completed
    if event_cb:
        event_cb({"type": "tool.progress", "step_id": -1, "call_id": task_id,
                  "name": f"{agent_id}.delegate", "label": f"委派 {agent_id}",
                  "phase": "delegation.completed", "status": tr_status,
                  "summary": task_result["summary"][:120], "agent": agent_id})

    # ⑨ 返回给 Producer loop 的回执 = Task Result（含 status/summary/changed_refs/
    #    warnings/task_id；ok 字段供 loop 判断；不含 trace）
    task_result["ok"] = (tr_status == "completed")
    task_result["operation"] = f"{agent_id}.delegate"
    return task_result


# _TurnStopped：lazy 引用 director 重新导出的协作停止异常（Specialist turn 与本 loop 共用）。
def _import_turn_stopped():
    from src.agents.director import _TurnStopped
    return _TurnStopped


_TurnStopped = _import_turn_stopped()  # 模块级取一次（director 不 import 本模块，无环）


# ============ Producer tool-loop（专用轻量镜像 loop，不复用 director 的）============
async def _producer_tool_loop(rt, client, project: str, messages: list,
                              instruction: str, message_id: Optional[str], *,
                              thinking: bool = False, event_cb=None,
                              cancel_event=None, state: Optional[dict] = None,
                              tool_executors: Optional[dict] = None,
                              offered_tools: Optional[list] = None) -> dict:
    """Producer 专用 tool-loop。镜像 director._run_tool_loop 的【结构】（while-True +
    renewable window / cancel / 流式 event_cb / trace / receipts），但【去掉】写域真相
    闸门 / zero-read-zero-write nudge / 搜索预算 / 批量降级（理由见文件头 §5.2）。
    receipts = delegate 回执（Task Result）。step 跨窗口单调，续期只归零 window_used。"""
    if state is None:
        state = {}
    events: list = state.setdefault("events", [])
    receipts: list = state.setdefault("receipts", [])
    trace: list = state.setdefault("trace", [])
    reasonings: list = state.setdefault("reasonings", [])
    offered = offered_tools or []

    step = -1
    window_used = 0
    window_renewals = 0
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise _TurnStopped()
        # ---- 执行窗口边界：self-check + 续期（renewable window，次数不限）----
        if window_used >= DEFAULT_MAX_TOOL_STEPS:
            window_renewals += 1
            window_used = 0
            messages.append({"role": "user", "content": _WINDOW_SELF_CHECK_NUDGE})
            _refresh_producer_system(messages, rt, project)
            log.info("producer tool-loop 窗口续期（project=%s renewals=%s total_steps=%s）",
                     project, window_renewals, step + 1)
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
                        thinking=thinking, tools=offered, tool_choice="auto"):
                    k = ev["kind"]
                    if k == "content_delta":
                        step_content_buf.append(ev["delta"])
                    elif k == "reasoning_delta":
                        if not step_reasoning_started:
                            step_reasoning_started = True
                            step_reason_t0 = time.perf_counter()
                            event_cb({"type": "reasoning.started", "step_id": step})
                        step_reasoning_buf.append(ev["delta"])
                        event_cb({"type": "reasoning.delta", "step_id": step, "delta": ev["delta"]})
                    elif k == "tool_call_pending":
                        event_cb({"type": "tool.pending", "step_id": step,
                                  "name": ev["name"], "label": _tool_label(ev["name"])})
                    elif k == "finish":
                        content = ev["content"]
                        tool_calls = ev["tool_calls"]
                if step_reasoning_started:
                    rtext = "".join(step_reasoning_buf)
                    rdur = int((time.perf_counter() - step_reason_t0) * 1000)
                    event_cb({"type": "reasoning.completed", "step_id": step, "duration_ms": rdur})
                    if rtext:
                        reasonings.append({"step": step, "text": rtext, "duration_ms": rdur})
                if not tool_calls and step_content_buf:
                    for d in step_content_buf:
                        event_cb({"type": "assistant.delta", "delta": d})
            else:
                resp = await client.chat_completion(
                    messages, temperature=0.7, max_tokens=65536,
                    tools=offered, tool_choice="auto")
                tool_calls = resp.get("tool_calls")
                content = resp.get("content")
        except httpx.ReadTimeout:
            # 不做批量降级（Producer 无批量生成）。给一次恢复 nudge，继续。
            messages.append({"role": "user", "content":
                "[系统恢复提示] 上一次模型响应超时（read timeout），本次没有形成完整工具调用。"
                "请继续未完成的步骤，不要重做已完成的部分。"})
            _refresh_producer_system(messages, rt, project)
            continue

        if not tool_calls:
            text = (content or "").strip() or "已完成。"
            return {"text": text, "events": events, "receipts": receipts,
                    "trace": trace, "reasonings": reasonings,
                    "window_renewals": window_renewals}

        messages.append({"role": "assistant", "content": content or "", "tool_calls": tool_calls})

        delegate_done_this_step = False  # 单委派串行：一步只委派一个 Agent，等结果回来再委派下一个
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            args = fn.get("arguments") or "{}"
            call_id = tc.get("id") or name

            if event_cb:
                event_cb({"type": "tool.started", "step_id": step, "call_id": call_id,
                          "name": name, "label": _tool_label(name)})

            if name in DELEGATE_TOOLS and delegate_done_this_step:
                obs = {"ok": False, "operation": name, "code": "serial_delegate",
                       "error": "本步已委派过一个 Agent。请等其返回结果后再委派下一个，"
                                "不要在同一次回复里同时委派多个 Agent。"}
            else:
                executor = (tool_executors or {}).get(name)
                if executor is None:
                    obs = {"ok": False, "operation": name, "error": f"未知工具：{name}"}
                elif name in DELEGATE_TOOLS:
                    # 长任务通道：包装 event_cb 注入 step/call 上下文（仿 director DIRECTOR_LONG_TASK_TOOLS）
                    if event_cb:
                        def _gen_cb(ev, _step=step, _cid=call_id, _name=name):
                            event_cb({"type": ev.get("type", "tool.progress"),
                                      "step_id": _step, "call_id": _cid, "name": _name,
                                      "label": _tool_label(_name),
                                      **{k: v for k, v in ev.items() if k != "type"}})
                    else:
                        _gen_cb = None
                    try:
                        obs = await executor(rt, project, args, instruction, message_id,
                                             event_cb=_gen_cb, cancel_event=cancel_event)
                    except _TurnStopped:
                        raise
                    except Exception as e:
                        log.exception("producer delegate executor 异常（%s）", name)
                        obs = {"ok": False, "operation": name,
                               "error": f"{type(e).__name__}: {e}", "agent": name.split(".")[0]}
                    delegate_done_this_step = True
                elif asyncio.iscoroutinefunction(executor):
                    obs = await executor(rt, project, args, instruction, message_id)
                else:
                    obs = executor(rt, project, args, instruction, message_id)

            ok_now = bool(obs.get("ok"))
            # delegate 回执记入账本（receipts = Task Result 列表）；read 工具不入 receipts
            if name in DELEGATE_TOOLS:
                receipts.append(obs)
                events.append({"kind": "delegation", "agent": obs.get("agent"),
                               "status": obs.get("status"), "task_id": obs.get("task_id"),
                               "summary": (obs.get("summary") or "")[:200],
                               "changed_refs": obs.get("changed_refs")})

            if event_cb:
                event_cb({"type": "tool.completed", "step_id": step, "call_id": call_id,
                          "name": name, "ok": ok_now,
                          "summary": _tool_summary(name, obs)})

            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": json.dumps(obs, ensure_ascii=False)})
            trace.append({"step": step, "tool": name, "args": args, "ok": ok_now,
                          "summary": _tool_summary(name, obs)})
    # unreachable


# ============ producer_turn（入口，镜像 director_turn 结构）============
async def producer_turn(rt, project: str, session_id: str, content: str, *,
                        thinking: bool = False, event_cb=None,
                        cancel_event=None) -> dict:
    """Producer 原生 tool-loop turn。

    返回 {assistant_message, events, message, intent, receipts, trace, window_renewals,
    session_id, agent_id, linked_task_ids, evidence_refs}（与 script_writer_turn /
    director_turn 同族契约）。thinking/event_cb 仅流式端点传入；cancel_event 协作式停止。
    Producer 始终持有用户对话控制权；Specialist turn 在 Bridge 内嵌套执行，不接管对话。"""
    from .director import _TurnStopped  # 确保与 loop 同一异常类
    store = rt._session_store(project)
    session = store.load(session_id)
    if session is None:
        from .models import AgentError
        raise AgentError("unknown_scope", f"session 不存在: {session_id}")
    if session.agent_id != "producer":
        from .models import AgentError
        raise AgentError("permission_denied", "该会话仅限 Producer")
    if not rt._is_enabled("producer"):
        from .models import AgentError
        raise AgentError("disabled_agent", "Producer 已禁用")

    # 持久化 user 消息（ Specialists 自己持久化其会话；Producer 持久化自己的）
    user_msg = AgentMessage(role="user", content=content)
    store.append_message(session_id, user_msg)
    rt._maybe_capture_preference(project, content, user_msg.message_id)
    session = store.load(session_id)

    pdir = rt._resolve_project_dir(project)
    history = [{"role": m.role, "content": m.content}
               for m in session.messages[:-1]
               if m.role in ("user", "assistant") and m.content and m.status is None]

    client = rt._resolve_client_or_none(agent_id="producer")
    assistant_text: str
    status: Optional[str] = None
    events: list = []
    receipts: list = []
    trace: list = []
    reasonings: list = []
    window_renewals = 0
    linked_task_ids: list = []

    if client is None:
        assistant_text = "[chat 未配置：Agnes 推理模型不可用，无法协调任务。请在设置中配置 API Key。]"
        status = ResultStatus.NOT_CONFIGURED.value
    else:
        system = _build_producer_system(rt, project)
        messages: list[dict] = [{"role": "system", "content": system}]
        messages += history
        messages.append({"role": "user", "content": content})

        offered, read_executors = _build_producer_tools()
        # workflow 闭包：turn 内首次 delegate 惰性创建；prev_ref 链 parent_task_id
        wf_ref: list = [None]
        prev_ref: list = [None]

        def _make_delegate_executor(agent_id: str):
            async def _exec(rt, project, arguments, instruction, message_id, *,
                            event_cb=None, cancel_event=None):
                envelope = _parse_envelope(arguments)
                envelope.setdefault("user_intent", instruction)  # 兜底用户原话
                if wf_ref[0] is None:
                    wf_store = ProducerWorkflowStore(pdir, project)
                    wf_ref[0] = wf_store.get_or_create_for_turn(instruction)
                wf = wf_ref[0]
                tr = await _delegate_specialist(
                    rt, project, agent_id, envelope,
                    event_cb=event_cb, cancel_event=cancel_event,
                    prev_task_id=prev_ref[0], workflow_id=wf["workflow_id"])
                if tr.get("task_id"):
                    prev_ref[0] = tr["task_id"]
                    ProducerWorkflowStore(pdir, project).add_task(wf["workflow_id"], tr["task_id"])
                return tr
            return _exec

        tool_executors = dict(read_executors)
        tool_executors["project.inspect"] = _execute_project_inspect
        for aid in ("script_writer", "character_designer", "director"):
            tool_executors[f"{aid}.delegate"] = _make_delegate_executor(aid)

        loop_state: dict = {}
        try:
            final = await _producer_tool_loop(
                rt, client, project, messages, content, user_msg.message_id,
                thinking=thinking, event_cb=event_cb, cancel_event=cancel_event,
                state=loop_state, tool_executors=tool_executors, offered_tools=offered)
            assistant_text = final["text"]
            events = final["events"]
            receipts = final["receipts"]
            trace = final["trace"]
            reasonings = final.get("reasonings", [])
            window_renewals = final.get("window_renewals", 0)
        except _TurnStopped:
            log.info("producer_turn 被停止（project=%s session=%s）", project, session_id)
            done = [r.get("agent") for r in loop_state.get("receipts", []) if r.get("ok")]
            done_str = ("已委派完成：" + "、".join(done)) if done else "本次没有已完成的委派"
            assistant_text = f"[任务已停止] {done_str}；其余未完成部分未执行。"[:300]
            status = ResultStatus.STOPPED.value
            events = loop_state.get("events", [])
            receipts = loop_state.get("receipts", [])
            trace = loop_state.get("trace", [])
            reasonings = loop_state.get("reasonings", [])
            window_renewals = loop_state.get("window_renewals", 0)
        except Exception as e:
            log.exception("producer_turn 工具循环异常（project=%s session=%s）", project, session_id)
            assistant_text = f"[执行失败: {type(e).__name__}: {str(e).strip()}]"[:300]
            status = ResultStatus.FAILED.value
            events = loop_state.get("events", [])
            receipts = loop_state.get("receipts", [])
            trace = loop_state.get("trace", [])
            reasonings = loop_state.get("reasonings", [])
            window_renewals = loop_state.get("window_renewals", 0)
        finally:
            # workflow 终态（per-producer-turn：turn 结束置终态）
            if wf_ref[0] is not None:
                wf_status = ("completed" if status is None
                             else "failed" if status == ResultStatus.FAILED.value
                             else "stopped" if status == ResultStatus.STOPPED.value
                             else "completed")
                ProducerWorkflowStore(pdir, project).settle(wf_ref[0]["workflow_id"], wf_status)

    linked_task_ids = [r.get("task_id") for r in receipts if r.get("task_id")]
    intent = "delegation" if receipts else "conversation"

    meta = {"intent": intent, "events": events, "evidence_refs": [],
            "receipts": receipts, "trace": trace, "reasonings": reasonings,
            "linked_task_ids": linked_task_ids, "window_renewals": window_renewals}
    asst_msg = AgentMessage(role="assistant", content=assistant_text,
                            status=status, meta=meta)
    store.append_message(session_id, asst_msg)

    return {
        "assistant_message": assistant_text,
        "events": events,
        "message": asst_msg.to_dict(),
        "intent": intent,
        "receipts": receipts,
        "trace": trace,
        "window_renewals": window_renewals,
        "session_id": session_id,
        "agent_id": "producer",
        "linked_task_ids": linked_task_ids,
        "evidence_refs": [],
    }
