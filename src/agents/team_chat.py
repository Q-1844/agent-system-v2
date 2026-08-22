# src/agents/team_chat.py
# AI-7A Team Group Chat —— 项目级团队群聊持久化（§22~§24 / §54~§57 / §76 / §78~§79）。
#
# 设计不变量：
#   - 项目隔离：thread_id 由 project_name 派生（team:{project_name} 语义，§23）；store 按
#     project_dir 隔离，A/B 项目互不可见（§78）。后端 lookup 强制按 project_name 解析目录。
#   - 持久化即真相：TeamThread/TeamMessage 落盘（atomic_write_json），不靠 Vue 内存/SSE
#     （§24/§54/§77）。polling-first：前端轮询 GET messages；**不新增 realtime bus**（用户决策）。
#   - 转录 ≠ 模型上下文：TeamThread 是用户所见；ProjectContextPack 是 Agent 单次 run 所见
#     （§25）。POST 不把整段转录塞进 run_inquiry——run_inquiry 自己按需取有界上下文。
#   - 幂等（§56）：refresh/reconnect/GET 不造新消息；startup 恢复不重跑、不造 dup。
#   - 不重跑 LLM（§57）：startup 对中断的 pending 只标 system「请重提」，不自动重发模型调用。
#   - 不持久化 free agent-to-agent chat（§59/§36）：producer 答案/分析卡来自持久化结构化事件
#     （run_inquiry 结果 + AgentTask）。
from __future__ import annotations
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from src.core.atomic import atomic_write_json

from .inquiry import run_inquiry, classify_request


def _plan_card_data(sess: dict) -> dict:
    """把 UnifiedChangePlanDraft 归一为 analysis_card 的 card_data（供 TeamChatPanel §60 卡渲染）。
    §38：显式 change_operations=[]，0 可执行操作。"""
    pd = sess.get("plan_draft") or {}

    def _items(rows):
        return [{"summary": r.get("summary", ""),
                 "owner_agent": r.get("owner_agent_id") or r.get("owner_agent", ""),
                 "module": r.get("module", "")}
                for r in (rows or [])]
    return {
        "direct_changes": _items(pd.get("direct_changes")),
        "downstream_changes": _items(pd.get("downstream_changes")),
        "unchanged": pd.get("unchanged_scope") or [],
        "user_decisions_needed": pd.get("user_decisions_needed") or [],
        "execution_ready": pd.get("execution_ready", False),
        "change_operations": [],          # §38：0 可执行操作
        "formal": True,
        "session_id": sess.get("session_id"),
        "status": sess.get("status"),
    }


# ============ 模型（§24）============
@dataclass
class TeamThread:
    """一个项目一个 team thread（§23：team:{project_name}）。"""
    thread_id: str
    project_name: str
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {"thread_id": self.thread_id, "project_name": self.project_name,
                "created_at": self.created_at, "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, d: dict) -> "TeamThread":
        d = d or {}
        return cls(thread_id=d.get("thread_id", ""), project_name=d.get("project_name", ""),
                   created_at=d.get("created_at", 0.0), updated_at=d.get("updated_at", 0.0))


@dataclass
class TeamMessage:
    """team 消息。message_type：user/agent/summary_card/analysis_card/system（§24）。
    pending_inquiry：user 消息触发 run_inquiry 后置 True，run 完成由 finalize 清除——
    用于 startup 中断检测（§54/§57）。message_id/thread_id 留空时由 store 填充。"""
    message_id: str = ""
    thread_id: str = ""
    message_type: str = "user"
    agent_id: Optional[str] = None
    text: Optional[str] = None
    source_session_id: Optional[str] = None
    source_task_id: Optional[str] = None
    source_change_session_id: Optional[str] = None
    card_data: Optional[dict] = None
    pending_inquiry: bool = False
    status: Optional[str] = None        # processing | completed | failed（agent/card 消息）
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {"message_id": self.message_id, "thread_id": self.thread_id,
                "message_type": self.message_type, "agent_id": self.agent_id,
                "text": self.text, "source_session_id": self.source_session_id,
                "source_task_id": self.source_task_id,
                "source_change_session_id": self.source_change_session_id,
                "card_data": self.card_data, "pending_inquiry": self.pending_inquiry,
                "status": self.status, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, d: dict) -> "TeamMessage":
        d = d or {}
        return cls(message_id=d.get("message_id", ""), thread_id=d.get("thread_id", ""),
                   message_type=d.get("message_type", "user"),
                   agent_id=d.get("agent_id"), text=d.get("text"),
                   source_session_id=d.get("source_session_id"),
                   source_task_id=d.get("source_task_id"),
                   source_change_session_id=d.get("source_change_session_id"),
                   card_data=d.get("card_data"),
                   pending_inquiry=d.get("pending_inquiry", False),
                   status=d.get("status"), created_at=d.get("created_at", 0.0))


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


# ============ TeamChatStore（§24 持久化，镜像 AgentSessionStore 风格）============
class TeamChatStore:
    """单项目 team-chat 持久化。thread_id 由 project_name 确定性派生（§23），无需列举即可定位。"""

    def __init__(self, project_dir, project_name: str):
        self.project_name = project_name
        self.dir = Path(project_dir) / "output" / "agents" / "team_threads"
        self.thread_id = "thread_" + hashlib.sha256(project_name.encode("utf-8")).hexdigest()[:12]
        self.path = self.dir / f"{self.thread_id}.json"

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        import json
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, data)

    def get_or_create_thread(self) -> dict:
        """幂等：存在则读，不存在则建空 thread。刷新/重连不造 dup（§56）。"""
        data = self._read()
        if not data or not data.get("thread"):
            now = time.time()
            thread = TeamThread(thread_id=self.thread_id, project_name=self.project_name,
                                created_at=now, updated_at=now)
            data = {"thread": thread.to_dict(), "messages": []}
            self._write(data)
        return data

    def list_messages(self) -> List[dict]:
        data = self.get_or_create_thread()
        return data.get("messages", [])

    def append_message(self, msg: TeamMessage) -> TeamMessage:
        """read-modify-write append。atomic_write_json 保证读者见旧或见新、不损坏。"""
        if not msg.message_id:
            msg.message_id = _new_id("msg")
        msg.thread_id = self.thread_id
        if not msg.created_at:
            msg.created_at = time.time()
        data = self.get_or_create_thread()
        data["messages"].append(msg.to_dict())
        data["thread"]["updated_at"] = msg.created_at
        self._write(data)
        return msg

    def finalize_inquiry(self, user_message_id: str, producer_message: TeamMessage) -> TeamMessage:
        """run_inquiry 完成后：append producer 消息 + 清除 user 消息 pending 标记（一次原子写）。"""
        data = self.get_or_create_thread()
        for m in data["messages"]:
            if m.get("message_id") == user_message_id:
                m["pending_inquiry"] = False
        if not producer_message.message_id:
            producer_message.message_id = _new_id("msg")
        producer_message.thread_id = self.thread_id
        if not producer_message.created_at:
            producer_message.created_at = time.time()
        data["messages"].append(producer_message.to_dict())
        data["thread"]["updated_at"] = producer_message.created_at
        self._write(data)
        return producer_message

    def mark_pending_interrupted(self, user_message_id: str, reason: str) -> None:
        """startup 恢复：pending 的 user 消息 + 一条 system「请重提」消息，不重跑 LLM（§57）。"""
        sys_msg = TeamMessage(message_type="system",
                              text=f"[分析因后端重启中断：{reason}。请重新提问以重新分析。]",
                              status="failed")
        self.finalize_inquiry(user_message_id, sys_msg)

    def list_pending_inquiries(self) -> List[dict]:
        data = self.get_or_create_thread()
        return [m for m in data.get("messages", []) if m.get("pending_inquiry")]


# ============ run_inquiry → 持久化（§76：HTTP 不长挂；后台 task 写结果）============
async def run_team_inquiry_and_persist(rt, project: str, user_message_id: str,
                                        user_request: str, *, client=None) -> None:
    """后台 task：分流——问题→run_inquiry 文本答案；变更→正式 ChangeAnalysisSession
    （持久化/可恢复/stale-aware/经校验，§31/§37），plan_draft 归一为 analysis_card 落盘。
    失败→system 消息。绝不抛到外面（后台 task 无人 catch）。"""
    store = TeamChatStore(rt._resolve_project_dir(project), project)
    try:
        proj = rt.project_intelligence(project)._project()
        kind = classify_request(user_request, proj)["kind"]
        if kind == "change":
            from .change_analysis import (create_change_analysis_session,
                                           continue_change_analysis)
            sess = await create_change_analysis_session(
                rt, project, user_request, background=False, source="team_chat")
            sess = await continue_change_analysis(rt, project, sess["session_id"])
            if sess["status"] == "ready" and sess.get("plan_draft"):
                msg = TeamMessage(
                    message_type="analysis_card", agent_id="producer",
                    text="变更分析已就绪（analysis-only，可恢复/未应用）",
                    card_data=_plan_card_data(sess),
                    source_change_session_id=sess["session_id"], status="completed")
            else:
                msg = TeamMessage(
                    message_type="system", agent_id=None,
                    text=f"正式变更分析状态：{sess['status']}"
                         f"{' · ' + sess['error'][:160] if sess.get('error') else ''}",
                    source_change_session_id=sess["session_id"],
                    status="failed" if sess["status"] in ("stale", "failed") else sess["status"])
        else:
            # AI-7C-H §13：注入持久化工作流快照——进度类问题从 Layer B 真相作答
            workflow_state = None
            try:
                from .change_protocol.service import active_workflow_snapshot
                workflow_state = active_workflow_snapshot(
                    rt._resolve_project_dir(project), project)
            except Exception:
                workflow_state = None
            result = await run_inquiry(rt, project, user_request, client=client,
                                       workflow_state=workflow_state)
            if result.get("kind") == "question":
                msg = TeamMessage(message_type="agent", agent_id="producer",
                                  text=result.get("answer", ""), status="completed")
            else:   # 理论不达（change 已分流），兜底
                msg = TeamMessage(message_type="analysis_card", agent_id="producer",
                                  text="变更分析已就绪（analysis-only，未应用）",
                                  card_data=result.get("analysis"), status="completed")
    except Exception as e:   # 任何异常 → system 消息，清 pending
        store.mark_pending_interrupted(user_message_id, f"运行异常: {str(e)[:200]}")
        return
    store.finalize_inquiry(user_message_id, msg)


def recover_team_chat(project_dir, project_name: str) -> int:
    """startup 恢复（§54/§57）：扫 pending user 消息，标中断、不重跑。
    返回处理数。镜像 lifecycle.recover_pending_videos 的 per-project 风格。"""
    try:
        store = TeamChatStore(project_dir, project_name)
        pending = store.list_pending_inquiries()
    except Exception:
        return 0
    for m in pending:
        try:
            store.mark_pending_interrupted(m["message_id"], "后端重启")
        except Exception:
            pass
    return len(pending)
