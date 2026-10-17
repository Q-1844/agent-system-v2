# src/agents/sessions.py
# AgentSession 存储（§8/§9/§26 / AI-7D-R §9/§10.1）。按项目落盘：
#   projects/{p}/output/agents/sessions/{session_id}.json  （会话元数据 + messages 历史）
#
# AI-7D-R：Direct Conversation 长存于 (project, agent_id)，不随页面 scope 切换重建。
# get_or_create_conversation 返回该 agent 的 project 级会话（conversation_id 稳定）。
# 页面/选中实体上下文是 per-turn 元数据，不再作为会话身份键（§9）。消息历史用简单 JSON
# persistence，不引入向量库/RAG/摘要（§24/§85）。所有写盘走 src/core/atomic.atomic_write_json。
from __future__ import annotations
import json
import secrets
import time
from pathlib import Path
from typing import List, Optional

from src.core.atomic import atomic_write_json

from .models import AgentSession, AgentMessage, ScopeType


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


class AgentSessionStore:
    """单个项目绑定的 agent session 存储。"""

    def __init__(self, project_dir, project_name: str):
        self.project_dir = Path(project_dir)
        self.project_name = project_name
        self.root = self.project_dir / "output" / "agents" / "sessions"

    def _path(self, sid: str) -> Path:
        return self.root / f"{sid}.json"

    def _save(self, s: AgentSession) -> None:
        s.updated_at = time.time()
        atomic_write_json(self._path(s.session_id), s.to_dict())

    def _iter_files(self):
        if not self.root.exists():
            return
        for f in sorted(self.root.glob("*.json")):
            try:
                yield AgentSession.from_dict(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue

    def get_or_create(self, agent_id: str, scope_type: str = ScopeType.PROJECT.value,
                      scope_id: str = "") -> AgentSession:
        """同 (agent_id, scope_type, scope_id) 复用现有 session，否则新建。保证隔离。
        AI-7D-R：保留给旧调用方/特殊 scope；Direct Chat 应优先用 get_or_create_conversation。"""
        for s in self._iter_files():
            if (s.agent_id == agent_id and s.scope_type == scope_type
                    and s.scope_id == scope_id):
                return s
        sid = _new_id("sess")
        s = AgentSession(
            session_id=sid, project=self.project_name, agent_id=agent_id,
            scope_type=scope_type, scope_id=scope_id or self.project_name)
        self._save(s)
        return s

    def get_or_create_conversation(self, agent_id: str) -> AgentSession:
        """AI-7D-R §9：返回该 agent 在本项目的长存 Direct Conversation（project 级，稳定
        conversation_id）。若已有带 conversation_id 的会话则复用；否则把该 agent 最近的
        project 级旧会话迁移为长存会话（保留历史，不丢上下文）；都没有则新建。"""
        candidates: List[AgentSession] = []
        for s in self._iter_files():
            if s.agent_id != agent_id:
                continue
            if s.conversation_id:
                return s   # 已存在的长存会话
            if s.scope_type == ScopeType.PROJECT.value:
                candidates.append(s)
        if candidates:
            # 迁移最近一条 project 级旧会话：赋 conversation_id + 回填消息字段
            s = max(candidates, key=lambda x: x.updated_at)
            if not s.conversation_id:
                s.conversation_id = "conv_" + secrets.token_hex(6)
                s.scope_type = ScopeType.PROJECT.value
                s.scope_id = self.project_name
                self._backfill_message_ids(s, agent_id, s.conversation_id)
                self._save(s)
            return s
        sid = _new_id("sess")
        conv_id = "conv_" + secrets.token_hex(6)
        s = AgentSession(
            session_id=sid, project=self.project_name, agent_id=agent_id,
            scope_type=ScopeType.PROJECT.value, scope_id=self.project_name,
            conversation_id=conv_id)
        self._save(s)
        return s

    def create_fresh_conversation(self, agent_id: str) -> AgentSession:
        """Phase55 D 会话接力：强制新建 project 级长存会话（干净上下文 + 新
        conversation_id）。此后 get_or_create_conversation 复用本会话（接力点成为
        新历史起点）；旧会话文件保留归档（不删除、不归档移动）。"""
        sid = _new_id("sess")
        conv_id = "conv_" + secrets.token_hex(6)
        s = AgentSession(
            session_id=sid, project=self.project_name, agent_id=agent_id,
            scope_type=ScopeType.PROJECT.value, scope_id=self.project_name,
            conversation_id=conv_id)
        self._save(s)
        return s

    def create_ephemeral(self, agent_id: str, scope_type: str, scope_id: str) -> AgentSession:
        """一次性会话（会话治理：Clean Review 审查实例等）：每次调用必新建、不复用、
        用完即弃。scope_type 用调用方给的专用值（如 "review"），不进入
        get_or_create / get_or_create_conversation 的复用键，不影响长存会话语义。"""
        sid = _new_id("sess")
        s = AgentSession(session_id=sid, project=self.project_name, agent_id=agent_id,
                         scope_type=scope_type, scope_id=scope_id or self.project_name)
        self._save(s)
        return s

    def save(self, s: AgentSession) -> None:
        """公开保存入口（会话治理 re-grounding 需要整体重写 messages，append_message
        做不到）。语义与 _save 相同。"""
        self._save(s)

    @staticmethod
    def _backfill_message_ids(s: AgentSession, agent_id: str, conv_id: str) -> None:
        """旧持久化消息没有 message_id/conversation_id/agent_id；原地补齐（保留 meta）。"""
        for m in s.messages:
            if not m.message_id:
                m.message_id = "msg_" + secrets.token_hex(6)
            if not m.conversation_id:
                m.conversation_id = conv_id
            if not m.agent_id:
                m.agent_id = agent_id

    def load(self, sid: str) -> Optional[AgentSession]:
        p = self._path(sid)
        if not p.exists():
            return None
        try:
            return AgentSession.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def list(self) -> List[AgentSession]:
        return list(self._iter_files())

    def append_message(self, sid: str, msg: AgentMessage) -> Optional[AgentSession]:
        s = self.load(sid)
        if s is None:
            return None
        if not msg.message_id:
            msg.message_id = _new_id("msg")
        if not msg.conversation_id and s.conversation_id:
            msg.conversation_id = s.conversation_id
        if not msg.agent_id:
            msg.agent_id = s.agent_id
        s.messages.append(msg)
        self._save(s)
        return s
