# src/agents/change_protocol/store.py
# AI-7B Change Protocol 持久化（§86）。按项目落盘，原子写（atomic_write_json）。
#
# 布局（镜像 AI-7A output/agents/ 约定，§86）：
#   projects/{p}/output/agents/change_protocol/{requests,sessions,proposals,
#                                                  plans,previews,decisions}/{id}.json
# 一文件一 artifact。不引入数据库（§86）；继续原子 JSON 写。
#
# Import 纪律：只 import src.core.atomic + 本包 models/migrations。不 import runtime/router。
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import List, Optional

from src.core.atomic import atomic_write_json

from . import migrations
from .models import (ChangeSession, ChangeRequest, ModuleProposal, UnifiedChangePlan,
                     PlanPreviewArtifact, ChangeDecisionRequest)


_KINDS = ("requests", "sessions", "proposals", "plans", "previews", "decisions")


class ChangeProtocolStore:
    """单个项目绑定的 AI-7B change-protocol 存储。"""

    def __init__(self, project_dir, project_name: str):
        self.project_dir = Path(project_dir)
        self.project_name = project_name
        self.root = self.project_dir / "output" / "agents" / "change_protocol"

    # ---------- 通用 ----------
    def _path(self, kind: str, oid: str) -> Path:
        return self.root / kind / f"{oid}.json"

    def _save_dict(self, kind: str, oid: str, data: dict) -> None:
        p = self._path(kind, oid)
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(p, data)

    def _load_dict(self, kind: str, oid: str) -> Optional[dict]:
        p = self._path(kind, oid)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _list_dicts(self, kind: str) -> List[dict]:
        d = self.root / kind
        if not d.exists():
            return []
        out = []
        for f in sorted(d.glob("*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return out

    # ---------- requests ----------
    def save_request(self, req: ChangeRequest) -> ChangeRequest:
        if not req.request_id:
            raise ValueError("request_id 缺失")
        self._save_dict("requests", req.request_id, req.to_dict())
        return req

    def get_request(self, rid: str) -> Optional[ChangeRequest]:
        d = self._load_dict("requests", rid)
        return ChangeRequest.from_dict(d) if d is not None else None

    # ---------- sessions ----------
    def save_session(self, session: ChangeSession) -> ChangeSession:
        if not session.session_id:
            raise ValueError("session_id 缺失")
        session.updated_at = time.time()
        # §65/§9：写盘前绝不放行 applyable（session 无此字段，但 normalize 兜底）
        self._save_dict("sessions", session.session_id, session.to_dict())
        return session

    def get_session(self, sid: str) -> Optional[ChangeSession]:
        """§9：legacy AI-7A 会话（无 schema_version）经 migrations 标 legacy_analysis_only，
        绝不静默重判为可执行 AI-7B proposal session。"""
        d = self._load_dict("sessions", sid)
        if d is None:
            return None
        return ChangeSession.from_dict(migrations.normalize_session_dict(d))

    def list_sessions(self) -> List[ChangeSession]:
        out = []
        for d in self._list_dicts("sessions"):
            out.append(ChangeSession.from_dict(migrations.normalize_session_dict(d)))
        out.sort(key=lambda s: s.updated_at, reverse=True)
        return out

    # ---------- proposals ----------
    def save_proposal(self, p: ModuleProposal) -> ModuleProposal:
        if not p.proposal_id:
            raise ValueError("proposal_id 缺失")
        p.updated_at = time.time()
        self._save_dict("proposals", p.proposal_id, p.to_dict())
        return p

    def get_proposal(self, pid: str) -> Optional[ModuleProposal]:
        d = self._load_dict("proposals", pid)
        return ModuleProposal.from_dict(d) if d is not None else None

    def list_proposals(self, session_id: Optional[str] = None) -> List[ModuleProposal]:
        out = []
        for d in self._list_dicts("proposals"):
            mp = ModuleProposal.from_dict(d)
            if session_id and mp.change_session_id != session_id:
                continue
            out.append(mp)
        out.sort(key=lambda x: x.created_at, reverse=True)
        return out

    # ---------- plans ----------
    def save_plan(self, plan: UnifiedChangePlan) -> UnifiedChangePlan:
        if not plan.plan_id:
            raise ValueError("plan_id 缺失")
        self._save_dict("plans", plan.plan_id, plan.to_dict())
        return plan

    def get_plan(self, pid: str) -> Optional[UnifiedChangePlan]:
        d = self._load_dict("plans", pid)
        return UnifiedChangePlan.from_dict(d) if d is not None else None

    def list_plans(self, session_id: Optional[str] = None) -> List[UnifiedChangePlan]:
        out = []
        for d in self._list_dicts("plans"):
            pl = UnifiedChangePlan.from_dict(d)
            if session_id and pl.change_session_id != session_id:
                continue
            out.append(pl)
        out.sort(key=lambda x: x.version, reverse=True)
        return out

    # ---------- previews ----------
    def save_preview(self, pv: PlanPreviewArtifact) -> PlanPreviewArtifact:
        if not pv.preview_id:
            raise ValueError("preview_id 缺失")
        pv.applyable = False   # §65 硬不变量兜底
        self._save_dict("previews", pv.preview_id, pv.to_dict())
        return pv

    def get_preview(self, pid: str) -> Optional[PlanPreviewArtifact]:
        d = self._load_dict("previews", pid)
        return PlanPreviewArtifact.from_dict(d) if d is not None else None

    # ---------- decisions ----------
    def save_decision(self, dec: ChangeDecisionRequest) -> ChangeDecisionRequest:
        if not dec.decision_id:
            raise ValueError("decision_id 缺失")
        dec.updated_at = time.time()
        self._save_dict("decisions", dec.decision_id, dec.to_dict())
        return dec

    def get_decision(self, did: str) -> Optional[ChangeDecisionRequest]:
        d = self._load_dict("decisions", did)
        return ChangeDecisionRequest.from_dict(d) if d is not None else None

    def list_decisions(self, session_id: Optional[str] = None) -> List[ChangeDecisionRequest]:
        out = []
        for d in self._list_dicts("decisions"):
            dec = ChangeDecisionRequest.from_dict(d)
            if session_id and dec.change_session_id != session_id:
                continue
            out.append(dec)
        out.sort(key=lambda x: x.created_at, reverse=True)
        return out
