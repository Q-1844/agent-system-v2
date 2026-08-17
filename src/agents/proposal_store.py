# src/agents/proposal_store.py
# AI-6D Director Proposal 持久化（§10/§16）。按项目落盘：
#   projects/{p}/output/agents/proposals/{proposal_id}.json
# 一个 Proposal = 一次 Director 修改意图 + 编译/预览产物引用 + 状态机
# （created -> selected_previewed -> applied | dismissed | stale）。
# Proposal 创建于 Director Workspace 创建之后、首次 full preview 之后（§11）；
# 选中子集再预览只更新 selected_preview_changeset_id（§13）。
#
# Import 纪律：本文件只 import src.core.atomic + .director_proposal.DirectorProposal。
# 不 import runtime/router（orchestrator 由 runtime 注入本 store 作为依赖，单向）。
# 风格对齐 src/agents/sessions.py / tasks.py。
from __future__ import annotations
import json
import secrets
import time
from pathlib import Path
from typing import Callable, List, Optional

from src.core.atomic import atomic_write_json

from .director_proposal import DirectorProposal


class ProposalStore:
    """单个项目绑定的 Director Proposal 存储。镜像 AgentSessionStore/AgentTaskStore。"""

    def __init__(self, project_dir, project_name: str):
        self.project_dir = Path(project_dir)
        self.project_name = project_name
        self.root = self.project_dir / "output" / "agents" / "proposals"

    def _path(self, pid: str) -> Path:
        return self.root / f"{pid}.json"

    def _save(self, p: DirectorProposal) -> None:
        p.updated_at = time.time()
        atomic_write_json(self._path(p.proposal_id), p.to_dict())

    def _iter_files(self):
        if not self.root.exists():
            return
        for f in sorted(self.root.glob("*.json")):
            try:
                yield DirectorProposal.from_dict(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue

    def create(self, p: DirectorProposal) -> DirectorProposal:
        if not p.proposal_id:
            p.proposal_id = "prop_" + secrets.token_hex(6)
        p.project = p.project or self.project_name
        if not p.created_at:
            p.created_at = time.time()
        self._save(p)
        return p

    def save(self, p: DirectorProposal) -> DirectorProposal:
        """整体写回（orchestrator 流转状态时用）。不存在 proposal_id 时抛 ValueError。"""
        if not p.proposal_id:
            raise ValueError("proposal_id 缺失，无法 save")
        self._save(p)
        return p

    def get(self, pid: str) -> Optional[DirectorProposal]:
        p = self._path(pid)
        if not p.exists():
            return None
        try:
            return DirectorProposal.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def update(self, pid: str, mutator: Callable[[DirectorProposal], None]) -> Optional[DirectorProposal]:
        p = self.get(pid)
        if p is None:
            return None
        mutator(p)
        self._save(p)
        return p

    def list_by_scope(self, scope_type: str, scope_id: str) -> List[DirectorProposal]:
        out = []
        for p in self._iter_files():
            if p.scope_type == scope_type and p.scope_id == scope_id:
                out.append(p)
        out.sort(key=lambda x: x.created_at, reverse=True)   # 新→旧
        return out

    def list_active(self, scope_type: str, scope_id: str) -> Optional[DirectorProposal]:
        """当前 scope 最近一个未 dismissed/applied 的 Proposal（刷新恢复用，§16）。"""
        for p in self.list_by_scope(scope_type, scope_id):
            if p.status not in ("applied", "dismissed"):
                return p
        return None
