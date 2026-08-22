# src/agents/change_analysis.py
# AI-7A Change Analysis Session + analysis-only Unified Plan Draft（§30~§32 / §37 / §42 / §49 / §55 / §74）。
#
# 7A-2 的 run_inquiry 是轻量询问引擎；本模块是**持久化、可恢复、stale-aware、经校验**的正式变更分析会话。
#
# 设计不变量：
#   - analysis-only：UnifiedChangePlanDraft.execution_ready 恒 False；0 ChangeOperation（§37/§38）。
#     ReadOnlyPlanValidator 强制校验（§42）。
#   - stale：session.project_signature_at_start（creative_source_signature）≠ 当前签名 → status=stale，
#     不作为当前计划呈现（§49）。
#   - 重启恢复（§55/§57）：保留 completed 咨询；running 但 linked AgentTask 卡住 → 标 failed（中断），
#     **不自动重跑 LLM**（§57 no infinite loop）；用户显式 POST /resume 才 continue。
#   - persist-before-gather（§34）：consultation 先落 running + agent_task_id 再 gather，独立结算。
#   - 复用不重写：inquiry 的 classify_request / ProjectContextBuilder / _create_consultation_task /
#     _consult_llm / _settle_consultation_task / _producer_synthesize_analysis；AgentTaskStore。
#   - 不触发 mutation workflow：只读 inspect cap（assert_read_only_capability）；canonical 不变。
from __future__ import annotations
import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.atomic import atomic_write_json
from src.revision.models import creative_source_signature

from .inquiry import (classify_request, ProjectContextBuilder, ConsultationResult,
                      _create_consultation_task, _consult_llm, _settle_consultation_task,
                      _producer_synthesize_analysis, _AGENT_INSPECT_CAP, _module_for_agent,
                      assert_read_only_capability, MODULE_OWNER, normalize_module)
from .runtime import AgentRuntime, AgentError


# 真终态：不再被 continue 推进。blocked **非终态**——§55/§57 显式 /resume 可重跑 failed 咨询。
TERMINAL = {"ready", "failed", "cancelled", "stale"}
MAX_ATTEMPTS = 3   # §57：单咨询最多重跑次数，超过即永久 failed，防 startup/恢复无限重跑 LLM


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def _now() -> float:
    return time.time()


# ============ 模型（§30/§31/§32/§37）============
@dataclass
class ChangeRequestDraft:
    """显式非可执行（§30：'Draft' 不可误为执行对象）。"""
    request_id: str = ""
    project_name: str = ""
    source: str = "team_chat"            # team_chat | direct_agent_chat
    source_agent_id: Optional[str] = None
    source_session_id: Optional[str] = None
    original_user_request: str = ""
    primary_module: Optional[str] = None
    suspected_modules: List[str] = field(default_factory=list)
    entity_refs: List[dict] = field(default_factory=list)
    snapshot_signature: Optional[str] = None
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id, "project_name": self.project_name,
            "source": self.source, "source_agent_id": self.source_agent_id,
            "source_session_id": self.source_session_id,
            "original_user_request": self.original_user_request,
            "primary_module": self.primary_module,
            "suspected_modules": list(self.suspected_modules),
            "entity_refs": list(self.entity_refs),
            "snapshot_signature": self.snapshot_signature,
            "created_at": self.created_at}


@dataclass
class ModuleConsultation:
    """咨询记录（§32）。linked AgentTask（agent_task_id）供重启对账。attempt_count 防 §57 无限重跑。"""
    consultation_id: str = ""
    change_session_id: str = ""
    agent_id: str = ""
    module: str = ""
    question: str = ""
    context_refs: List[dict] = field(default_factory=list)
    status: str = "pending"              # pending | running | completed | failed
    agent_task_id: Optional[str] = None
    result_summary: Optional[str] = None
    direct_implications: List[str] = field(default_factory=list)
    downstream_facts: List[str] = field(default_factory=list)
    user_decisions_needed: List[str] = field(default_factory=list)
    attempt_count: int = 0
    last_error: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {"consultation_id": self.consultation_id,
                "change_session_id": self.change_session_id,
                "agent_id": self.agent_id, "module": self.module,
                "question": self.question, "context_refs": list(self.context_refs),
                "status": self.status, "agent_task_id": self.agent_task_id,
                "result_summary": self.result_summary,
                "direct_implications": list(self.direct_implications),
                "downstream_facts": list(self.downstream_facts),
                "user_decisions_needed": list(self.user_decisions_needed),
                "attempt_count": self.attempt_count, "last_error": self.last_error,
                "created_at": self.created_at, "updated_at": self.updated_at}


@dataclass
class UnifiedChangePlanDraft:
    """analysis-only 统一计划草稿（§37）。execution_ready 恒 False；无 ChangeOperation（§38）。"""
    plan_draft_id: str = ""
    project_name: str = ""
    change_session_id: str = ""
    status: str = "analysis_only"       # 恒 analysis_only
    direct_changes: List[dict] = field(default_factory=list)    # ChangePlanDraftItem dict
    downstream_changes: List[dict] = field(default_factory=list)
    unchanged_scope: List[dict] = field(default_factory=list)
    dependency_edges: List[dict] = field(default_factory=list)
    user_decisions_needed: List[str] = field(default_factory=list)
    consulted_agents: List[str] = field(default_factory=list)
    source_snapshot_signature: Optional[str] = None
    execution_ready: bool = False       # §37：恒 False

    def to_dict(self) -> dict:
        return {"plan_draft_id": self.plan_draft_id, "project_name": self.project_name,
                "change_session_id": self.change_session_id, "status": self.status,
                "direct_changes": list(self.direct_changes),
                "downstream_changes": list(self.downstream_changes),
                "unchanged_scope": list(self.unchanged_scope),
                "dependency_edges": list(self.dependency_edges),
                "user_decisions_needed": list(self.user_decisions_needed),
                "consulted_agents": list(self.consulted_agents),
                "source_snapshot_signature": self.source_snapshot_signature,
                "execution_ready": self.execution_ready}


# session 存为 dict（含 request/consultations/plan_draft），便于演进。ChangeAnalysisStore 读写 dict。
def _new_session(project_name: str, request: ChangeRequestDraft, sig: str) -> dict:
    sid = _new_id("cs")
    now = _now()
    return {"session_id": sid, "project_name": project_name,
            "request": request.to_dict(), "status": "analyzing",
            "project_signature_at_start": sig,
            "consultations": [], "plan_draft_id": None, "plan_draft": None,
            "error": None, "created_at": now, "updated_at": now}


# ============ Store（§54 持久化，镜像 AgentSessionStore 风格）============
class ChangeAnalysisStore:
    """单项目 change-analysis 持久化。一文件一会话（含 consultations + plan_draft）。"""

    def __init__(self, project_dir, project_name: str):
        self.project_name = project_name
        self.dir = Path(project_dir) / "output" / "agents" / "change_analysis"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.dir / f"{session_id}.json"

    def save_session(self, session: dict) -> dict:
        session["updated_at"] = _now()
        atomic_write_json(self._path(session["session_id"]), session)
        return session

    def get_session(self, session_id: str) -> Optional[dict]:
        p = self._path(session_id)
        if not p.exists():
            return None
        import json as _json
        return _json.loads(p.read_text(encoding="utf-8"))

    def list_sessions(self) -> List[dict]:
        out = []
        for f in sorted(self.dir.glob("*.json")):
            try:
                import json as _json
                out.append(_json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        out.sort(key=lambda s: s.get("updated_at", 0), reverse=True)
        return out


# ============ ReadOnlyPlanValidator（§42）============
_OP_KEYS = ("op", "op_type", "update_scene", "update_shot", "insert_shot",
            "delete_shot", "reorder_shots", "after", "before", "operations")


class ReadOnlyPlanValidator:
    """确定性 analysis 校验器（§42）。只读、analysis-only、无可执行 op。"""

    def validate(self, draft: dict, project_state: Optional[dict] = None,
                 session: Optional[dict] = None) -> List[dict]:
        issues: List[dict] = []
        if draft.get("execution_ready") is not False:
            issues.append({"code": "EXECUTABLE",
                           "message": "execution_ready 必须为 False（AI-7A analysis-only）"})
        if draft.get("status") != "analysis_only":
            issues.append({"code": "BAD_STATUS", "message": "status 必须为 analysis_only"})
        for item in (draft.get("direct_changes") or []) + (draft.get("downstream_changes") or []):
            for bad in _OP_KEYS:
                if bad in item:
                    issues.append({"code": "CHANGE_OPERATION",
                                   "message": f"plan item 含可执行 op 字段: {bad}"})
            src = item.get("source") or item.get("reason")
            if src and src not in ("user_requested", "downstream_impact"):
                issues.append({"code": "BAD_CATEGORY",
                               "message": f"direct/downstream 分类无效: {src}"})
            mod = normalize_module(item.get("module", ""))
            if mod and mod != item.get("module"):
                item["module"] = mod   # 归一修正（characters→character 等）
            if mod and mod not in MODULE_OWNER:
                issues.append({"code": "UNKNOWN_MODULE", "message": f"未知模块: {mod}"})
        for a in (draft.get("consulted_agents") or []):
            if a not in MODULE_OWNER.values():
                issues.append({"code": "UNKNOWN_OWNER", "message": f"未知 owner agent: {a}"})
        return issues


# ============ Runner（§74 continue_change_analysis）============
async def _run_pending_consultations(rt: AgentRuntime, project: str, session: dict) -> None:
    """persist-before-gather（§34）：每 consultation 先落 running+agent_task_id，再 gather LLM，
    独立结算。结果回填 ModuleConsultation。"""
    store = ChangeAnalysisStore(rt._resolve_project_dir(project), project)
    cli = rt._resolve_client_or_none()
    intel = rt.project_intelligence(project)
    request = session["request"]["original_user_request"]
    classification = classify_request(request, intel._project())
    pack = ProjectContextBuilder(intel).build_for_producer(
        request=request, classification=classification)
    pending = [c for c in session["consultations"]
               if c["status"] in ("pending", "failed")
               and c.get("attempt_count", 0) < MAX_ATTEMPTS]
    if not pending:
        return
    prepared = []
    for c in pending:
        cap = _AGENT_INSPECT_CAP.get(c["agent_id"])
        if not cap:
            c["status"] = "failed"; c["last_error"] = "无 inspect 能力映射"; continue
        assert_read_only_capability(cap)
        task = _create_consultation_task(rt, project, c["agent_id"], c["question"], cap)
        c["status"] = "running"; c["agent_task_id"] = task.task_id
        c["attempt_count"] = c.get("attempt_count", 0) + 1; c["updated_at"] = _now()
        prepared.append((c, task))
    store.save_session(session)   # §34：gather 前先 persist running+task_id

    if cli is None:
        for c, task in prepared:
            c["status"] = "failed"; c["last_error"] = "Agnes 未配置"
            _settle_consultation_task(rt, project, task, ConsultationResult(
                status="blocked", summary="Agnes 未配置", owner_module=c["module"],
                owner_agent=c["agent_id"]), failed=True)
        store.save_session(session)
        return

    results = await asyncio.gather(*[
        _consult_llm(rt, project, task, c["agent_id"], c["question"], pack, cli)
        for c, task in prepared
    ], return_exceptions=True)
    for (c, task), res in zip(prepared, results):
        if isinstance(res, Exception):
            c["status"] = "failed"; c["last_error"] = str(res)[:200]
            _settle_consultation_task(rt, project, task, ConsultationResult(
                status="blocked", summary=str(res)[:200], owner_module=c["module"],
                owner_agent=c["agent_id"]), failed=True)
        else:
            failed = res.status == "blocked"
            c["status"] = "failed" if failed else "completed"
            c["result_summary"] = res.summary
            c["direct_implications"] = [str(x) for x in res.direct_implications][:50]
            c["downstream_facts"] = [str(x) for x in res.downstream_facts][:50]
            c["user_decisions_needed"] = [str(x) for x in res.user_decisions_needed][:20]
            if failed:
                c["last_error"] = res.summary[:200]
            _settle_consultation_task(rt, project, task, res, failed=failed)
    store.save_session(session)


def _consultations_to_results(session: dict) -> List[ConsultationResult]:
    """从持久化 ModuleConsultation 重建 ConsultationResult 供综合（§46）。"""
    out = []
    for c in session["consultations"]:
        if c["status"] != "completed":
            continue
        out.append(ConsultationResult(
            status="completed", summary=c.get("result_summary", ""),
            owner_module=c.get("module", _module_for_agent(c["agent_id"])),
            owner_agent=c["agent_id"],
            direct_implications=list(c.get("direct_implications", [])),
            downstream_facts=list(c.get("downstream_facts", [])),
            user_decisions_needed=list(c.get("user_decisions_needed", [])),
            agent_task_id=c.get("agent_task_id")))
    return out


def _wrap_plan_draft(session: dict, analysis: dict) -> UnifiedChangePlanDraft:
    def _items(rows, src):
        return [{"module": normalize_module(r.get("module", "")),
                 "owner_agent_id": r.get("owner_agent", ""),
                 "scope_refs": [], "summary": r.get("summary", ""),
                 "reason": r.get("reason", src), "source": src}
                for r in (rows or [])]
    return UnifiedChangePlanDraft(
        plan_draft_id=_new_id("plan"), project_name=session["project_name"],
        change_session_id=session["session_id"], status="analysis_only",
        direct_changes=_items(analysis.get("direct_changes"), "user_requested"),
        downstream_changes=_items(analysis.get("downstream_changes"), "downstream_impact"),
        unchanged_scope=[{"module": normalize_module(u.get("module", "")),
                          "entity_id": u.get("entity_id", "")}
                         for u in (analysis.get("unchanged") or []) if isinstance(u, dict)],
        user_decisions_needed=analysis.get("user_decisions_needed", []),
        consulted_agents=[c["agent_id"] for c in session["consultations"]
                          if c["status"] == "completed"],
        source_snapshot_signature=session.get("project_signature_at_start"))


async def continue_change_analysis(rt: AgentRuntime, project: str, session_id: str) -> dict:
    """幂等推进会话（§74）。终端态直接返回；stale→stale；跑 pending 咨询；失败→blocked；
    全完成→综合→校验→ready。"""
    store = ChangeAnalysisStore(rt._resolve_project_dir(project), project)
    session = store.get_session(session_id)
    if session is None:
        raise AgentError("unknown_scope", f"change-analysis 会话不存在: {session_id}")
    if session["status"] in TERMINAL:
        return session

    # §49 stale 检测
    cur_sig = creative_source_signature(rt.project_intelligence(project)._project().to_dict())
    if session.get("project_signature_at_start") and cur_sig != session["project_signature_at_start"]:
        session["status"] = "stale"
        return store.save_session(session)

    # 首次：分类 + 建 pending consultations
    if not session["consultations"]:
        intel = rt.project_intelligence(project)
        classification = classify_request(session["request"]["original_user_request"],
                                          intel._project())
        modules = classification["modules"] or ["director"]
        now = _now()
        session["consultations"] = [ModuleConsultation(
            consultation_id=_new_id("mc"), change_session_id=session_id,
            agent_id=m, module=_module_for_agent(m),
            question=_build_question(session["request"]["original_user_request"], m),
            status="pending", created_at=now, updated_at=now).to_dict()
            for m in modules]
        session["status"] = "consulting"
        store.save_session(session)

    # 跑 pending（persist-before-gather + 独立结算）
    await _run_pending_consultations(rt, project, session)
    session = store.get_session(session_id)

    # blocked？
    if any(c["status"] == "failed" for c in session["consultations"]):
        session["status"] = "blocked"
        session["error"] = "部分咨询失败，请恢复后重试"
        return store.save_session(session)

    # 综合
    session["status"] = "synthesizing"
    store.save_session(session)
    intel = rt.project_intelligence(project)
    request = session["request"]["original_user_request"]
    classification = classify_request(request, intel._project())
    pack = ProjectContextBuilder(intel).build_for_producer(request=request, classification=classification)
    cli = rt._resolve_client_or_none()
    results = _consultations_to_results(session)
    analysis = await _producer_synthesize_analysis(rt, project, request, pack, results, cli)
    draft = _wrap_plan_draft(session, analysis)
    issues = ReadOnlyPlanValidator().validate(draft.to_dict())
    if issues:
        session["status"] = "failed"
        session["error"] = json.dumps(issues, ensure_ascii=False)
        return store.save_session(session)
    session["plan_draft"] = draft.to_dict()
    session["plan_draft_id"] = draft.plan_draft_id
    session["status"] = "ready"
    return store.save_session(session)


def _build_question(user_request: str, agent_id: str) -> str:
    module = _module_for_agent(agent_id)
    return (f"用户变更请求：「{user_request}」\n请从「{module}」模块角度回答：① 直接需改什么；"
            f"② 哪些下游区域可能变陈旧；③ 是否有阻塞或需用户决策。只返回 JSON。")


async def create_change_analysis_session(rt: AgentRuntime, project: str,
                                         original_user_request: str, *,
                                         source: str = "team_chat",
                                         source_agent_id: Optional[str] = None,
                                         source_session_id: Optional[str] = None,
                                         background: bool = True) -> dict:
    """创建会话（analyzing）+ 落 project_signature_at_start + 后台推进（§76 不长挂）。"""
    intel = rt.project_intelligence(project)
    sig = creative_source_signature(intel._project().to_dict())
    req = ChangeRequestDraft(
        request_id=_new_id("cr"), project_name=project, source=source,
        source_agent_id=source_agent_id, source_session_id=source_session_id,
        original_user_request=original_user_request, snapshot_signature=sig, created_at=_now())
    session = _new_session(project, req, sig)
    store = ChangeAnalysisStore(rt._resolve_project_dir(project), project)
    store.save_session(session)
    if background:
        asyncio.create_task(continue_change_analysis(rt, project, session["session_id"]))
    return session


# ============ 重启恢复（§55/§57：保留 completed，running 卡住→failed，不自动重跑 LLM）============
def recover_change_analysis(project_dir, project_name: str) -> int:
    """startup 恢复：扫非终端会话，把 stuck running 咨询标 failed（中断），
    会话若有中断 → blocked。不自动重跑 LLM（§57）；用户显式 POST /resume 才继续。
    返回处理会话数。"""
    try:
        store = ChangeAnalysisStore(project_dir, project_name)
        sessions = store.list_sessions()
    except Exception:
        return 0
    n = 0
    for s in sessions:
        if s.get("status") in TERMINAL:
            continue
        touched = False
        for c in s.get("consultations", []):
            if c.get("status") == "running":
                c["status"] = "failed"
                c["last_error"] = "中断（后端重启）"
                c["updated_at"] = _now()
                touched = True
        if touched:
            s["status"] = "blocked"
            s["error"] = "分析因后端重启中断，请恢复后重试"
            store.save_session(s)
            n += 1
    return n
