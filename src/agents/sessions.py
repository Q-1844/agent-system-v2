# src/agents/sessions.py
# AgentSession 存储（§8/§9/§26）。按项目落盘：
#   projects/{p}/output/agents/sessions/{session_id}.json  （会话元数据 + messages 历史）
# 隔离规则（§9）：按 (agent_id, scope_type, scope_id) 复用 session_id；不同 Agent 的聊天
# 历史互不混在一起。消息历史用简单 JSON persistence，不引入向量库/RAG/摘要（§24）。
# 所有写盘走 src/core/atomic.atomic_write_json（单一来源）。
from __future__ import annotations
import json
import secrets
import time
from pathlib import Path
from typing import List, Optional

from src.core.atomic import atomic_write_json

from .models import AgentSession, AgentMessage, ScopeType


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
        """同 (agent_id, scope_type, scope_id) 复用现有 session，否则新建。保证隔离。"""
        for s in self._iter_files():
            if (s.agent_id == agent_id and s.scope_type == scope_type
                    and s.scope_id == scope_id):
                return s
        sid = "sess_" + secrets.token_hex(6)
        s = AgentSession(
            session_id=sid, project=self.project_name, agent_id=agent_id,
            scope_type=scope_type, scope_id=scope_id or self.project_name)
        self._save(s)
        return s

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
        s.messages.append(msg)
        self._save(s)
        return s
