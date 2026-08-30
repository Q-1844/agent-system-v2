# src/agents/script_writer_min.py
# ⚠️ LEGACY（2026-08-30）：本模块已不再是任何正式入口——Script Writer 测试弹窗自即日起
# 走真实链路 src/agents/script_writer.py（原生 tool-loop + 全部工具 + 真相闸门）。
# 本文件仅作为历史参考与既有单测（test_ai_7d_r_script_writer_min.py）保留；
# 【不要在新增入口/测试中引用它】，也不要把它的"JSON-in-text 伪工具"模式带到其他 Agent。
#
# AI-7D-R · 最小可验证 Agent 循环（§31）：Script Writer + 一个工具。
#
# 目的：在隔离的测试弹窗里验证“LLM → 工具调用 → 真实落盘 → 诚实回复”这条最小闭环。
# 不走 ChangeSession/提案/审查/执行流水线（那是后来逐步加回的规则）。
#
# 工具：edit_story_text(new_story_text) —— 直接原子写 canonical story_text + 审计 + 工作记忆。
# 安全：permission（仅 script_writer）+ 原子写（ProjectStore.save）+ 审计/快照（DirectEditAuditStore）
# + story_text 无下游生产依赖（§34：story-only 不触发生产）。不绕内核（§2/§15/§16 Level-1）。
from __future__ import annotations
import json
from typing import Any, Optional

from src.core.store import ProjectStore

from .models import AgentMessage, ResultStatus
from .runtime import AgentRuntime, AgentError
from .revision_audit import DirectEditAuditStore


SCRIPT_WRITER_MIN_SYSTEM = """你是 AGNES 的 Script Writer，负责剧本正文（story_text）的创作与修改。

你有一个工具可以使用：
- edit_story_text：直接修改项目的剧本正文。参数 new_story_text = 修改后的【完整】剧本正文（中文正文本身，不要 markdown 标题、不要解释、不要代码块）。

行为规则：
- 当用户要求修改/延长/扩写/续写/重写/压缩剧本时，调用 edit_story_text，给出修改后的完整剧本正文。
- 当用户只是提问、闲聊、或你不确定要不要改时，直接用自然语言回复，不调用工具。
- 不要把工具调用写成散文叙述（"我会修改…"是错的）。要么真的调用工具给出新正文，要么自然对话。
- 调用工具时只输出一个 JSON 对象，不要附加任何解释文字：
  {"tool": "edit_story_text", "new_story_text": "完整的新剧本正文…"}
- 新正文必须是你基于当前剧本 + 用户要求产出的完整结果，不是片段、不是说明。

当前剧本正文：
{story_text}
"""


class ToolCallParseError(Exception):
    """回复疑似工具调用但解析失败（通常是输出被 max_tokens 截断）。
    不能把这种半截文本当普通对话显示，否则会出现"嘴上说更新了实际没落盘"。"""
    pass


def _parse_tool_call(raw: str) -> Optional[dict]:
    """从 LLM 回复里解析工具调用 JSON。容错：去代码块、找首个 JSON 对象。
    - 成功返回 dict。
    - 明确是普通对话（没有工具调用意图）返回 None。
    - 疑似工具调用但被截断/损坏 → 抛 ToolCallParseError（调用方提示重试，绝不静默当对话）。"""
    if not raw:
        return None
    text = raw.strip()
    # 去代码块
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

    def _looks_like_tool_call(s: str) -> bool:
        # 出现工具名/参数键，或大括号里带 "tool"，都说明模型想调工具
        return ("edit_story_text" in s or "new_story_text" in s
                or '"tool"' in s or "'tool'" in s)

    # 直接 parse
    try:
        obj = json.loads(text)
    except Exception:
        # 尝试截取首个 {...}
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(raw[start:end + 1])
            except Exception:
                if _looks_like_tool_call(raw):
                    raise ToolCallParseError(
                        "工具调用 JSON 不完整（可能剧本太长被截断）。请让我重新生成，"
                        "或要求我分段输出。")
                return None
        else:
            if _looks_like_tool_call(raw):
                raise ToolCallParseError(
                    "工具调用 JSON 不完整（可能剧本太长被截断）。请让我重新生成，"
                    "或要求我分段输出。")
            return None
    if not isinstance(obj, dict):
        if _looks_like_tool_call(raw):
            raise ToolCallParseError("工具调用格式不正确，请重试。")
        return None
    if obj.get("tool") == "edit_story_text":
        if not obj.get("new_story_text"):
            raise ToolCallParseError("edit_story_text 缺少 new_story_text，请重试。")
        # 启发式截断检测：JSON 完整但正文很长（>500字）却没有正常结尾标点，
        # 很可能是 max_tokens 在正文中途截断、恰好 JSON 闭合。短正文不判（误报多）。
        body = str(obj["new_story_text"])
        if len(body) > 500 and body[-1] not in "。！？.!?…\n\"'）)】」":
            raise ToolCallParseError(
                "新剧本正文较长且似乎在中途被截断（没有正常结尾）。请让我重新生成完整版本，"
                "或缩短要求/分段写。")
        return obj
    if _looks_like_tool_call(raw):
        raise ToolCallParseError("工具名无法识别，请重试。")
    return None


def _execute_edit_story_text(rt: AgentRuntime, project: str, new_text: str,
                              instruction: str, message_id: Optional[str]) -> dict:
    """执行 edit_story_text 工具：原子写 canonical + 审计 + 工作记忆。"""
    new_text = (new_text or "").strip()
    if not new_text:
        raise AgentError("edit_empty", "新剧本正文为空")
    pdir = rt._resolve_project_dir(project)
    pstore = ProjectStore(pdir)
    proj = pstore.load()
    old = proj.story_text or ""
    if new_text == old:
        return {"old_story_text": old, "new_story_text": new_text,
                "old_len": len(old), "new_len": len(new_text), "unchanged": True}
    proj.story_text = new_text
    pstore.save(proj)   # 原子 canonical 写（与 execution_service.commit_canonical 同一路径）
    DirectEditAuditStore(pdir, project).record(
        agent_id="script_writer", field="story_text", before=old, after=new_text,
        instruction=instruction, message_id=message_id)
    rt.record_work(project, "change", "Script Writer 直接编辑了剧本正文",
                   participants=["script_writer"], affected_domains=["script"],
                   references={}, status="completed")
    return {"old_story_text": old, "new_story_text": new_text,
            "old_len": len(old), "new_len": len(new_text), "unchanged": False}


async def script_writer_turn(rt: AgentRuntime, project: str, session_id: str,
                              content: str) -> dict:
    """最小 Agent 循环：构建上下文 → LLM → (工具调用 | 对话) → 执行 → 落盘 → 诚实回复。

    返回 {assistant_message, edited, new_story_text?, old_story_text?, message}。
    session 必须是 script_writer 的。"""
    store = rt._session_store(project)
    session = store.load(session_id)
    if session is None:
        raise AgentError("unknown_scope", f"session 不存在: {session_id}")
    if session.agent_id != "script_writer":
        raise AgentError("permission_denied", "测试弹窗仅限 Script Writer")
    if not rt._is_enabled("script_writer"):
        raise AgentError("disabled_agent", "Script Writer 已禁用")

    # 1. 持久化 user 消息
    user_msg = AgentMessage(role="user", content=content)
    store.append_message(session_id, user_msg)
    rt._maybe_capture_preference(project, content, user_msg.message_id)
    session = store.load(session_id)   # 重读含 user

    # 2. 上下文：当前 story_text + 近期对话历史
    pdir = rt._resolve_project_dir(project)
    proj = ProjectStore(pdir).load()
    history = [{"role": m.role, "content": m.content}
               for m in session.messages[:-1]
               if m.role in ("user", "assistant") and m.content and m.status is None]

    client = rt._resolve_client_or_none(agent_id="script_writer")
    assistant_text: str
    status: Optional[str] = None
    edited = False
    new_story_text: Optional[str] = None
    old_story_text: Optional[str] = None
    events: list = []

    if client is None:
        assistant_text = "[chat 未配置：Agnes 推理模型不可用，无法修改剧本。请在设置中配置 API Key。]"
        status = ResultStatus.NOT_CONFIGURED.value
    else:
        messages = [{"role": "system",
                     "content": SCRIPT_WRITER_MIN_SYSTEM.replace(
                         "{story_text}", proj.story_text or "（空）")}]
        # 工作记忆/偏好作为轻量建议上下文（canonical 优先）
        mem = rt._memory_supplement(project, "script_writer")
        if mem:
            messages[0]["content"] += "\n\n" + mem
        messages += history
        messages.append({"role": "user", "content": content})
        try:
            reply = await client.chat(messages, temperature=0.7, max_tokens=65536)
        except Exception as e:
            assistant_text = f"[调用失败: {str(e)[:200]}]"
            status = ResultStatus.FAILED.value
            reply = None

        if reply is not None:
            parse_error = False
            try:
                tool = _parse_tool_call(reply)
            except ToolCallParseError as e:
                # 疑似工具调用但被截断/损坏——明确报错，绝不把半截文本当成功回复
                assistant_text = f"⚠️ {str(e)} 本次没有写入任何内容，请重试。"
                status = ResultStatus.FAILED.value
                tool = None
                parse_error = True
            if tool:
                try:
                    res = _execute_edit_story_text(rt, project, tool["new_story_text"],
                                                   content, user_msg.message_id)
                    edited = True
                    new_story_text = res["new_story_text"]
                    old_story_text = res["old_story_text"]
                    if res.get("unchanged"):
                        assistant_text = "新正文与当前剧本一致，未产生变化。"
                    else:
                        assistant_text = (f"已更新剧本正文（{res['old_len']} → "
                                          f"{res['new_len']} 字）。")
                        events = [{"kind": "story_update",
                                   "summary": f"剧本正文 {res['old_len']}→{res['new_len']} 字"}]
                except AgentError as e:
                    assistant_text = f"修改失败：{e.message}"
                    status = ResultStatus.FAILED.value
            elif not parse_error:
                # 普通对话回复（tool is None，且没有截断错误）
                assistant_text = reply
            # parse_error 时 assistant_text 已在上面设为错误提示，不覆盖

    # 3. 持久化 assistant 消息
    meta = {"intent": "script_direct_edit" if edited else "conversation",
            "events": events, "evidence_refs": []}
    asst = AgentMessage(role="assistant", content=assistant_text, status=status, meta=meta)
    store.append_message(session_id, asst)

    return {"assistant_message": assistant_text, "edited": edited,
            "new_story_text": new_story_text, "old_story_text": old_story_text,
            "events": events, "message": asst.to_dict()}
