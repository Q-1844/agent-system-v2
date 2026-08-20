# src/agents/change_protocol/service.py
# AI-7B ChangeSession 编排服务（§11/§23/§76/§78/§80/§87/§90/§93/§94）。
#
# 职责：create/resume/approve/reject/request_changes/submit_decision。
# 由 composition root（routers/agents.py）调用并注入 rt——与 AI-7A
# create_change_analysis_session(rt, ...) 模式一致；方法不挂在 AgentRuntime 上。
#
# 硬不变量（§2/§50/§76/§119/L）：本服务绝不 apply、绝不写 canonical / Workspace
# working_project / Production Plan / 生成资产 / JobQueue。批准只：重载签名 → 验非 stale →
# 验计划校验 PASS + 0 blocking decision → 持久化 approval + 置状态 → STOP。
#
# 7B-1 范围：协议 + 硬边界 + 批准流 + 版本骨架 + 决策。推进机（routing→consulting→…
# →preview_ready，含真实 LLM 提案/编译器/投影）在 7B-2..4 接通；resume 当前为幂等返回 +
# stale/legacy 守卫（不谎报进度）。
from __future__ import annotations
import asyncio
import secrets
import time
from typing import List, Optional

from src.revision.models import creative_source_signature

from ..runtime import AgentRuntime, AgentError
from ..inquiry import classify_request
from .models import (ChangeRequest, ChangeSession, UnifiedChangePlan, PlanApproval,
                      ChangeDecisionRequest, SESSION_TERMINAL)
from .store import ChangeProtocolStore
from .script_proposal import run_script_proposal
from .character_proposal import run_character_proposal
from .storyboard_proposal import run_storyboard_proposal


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def _now() -> float:
    return time.time()


def _store(rt: AgentRuntime, project: str) -> ChangeProtocolStore:
    return ChangeProtocolStore(rt._resolve_project_dir(project), project)


def _current_signature(rt: AgentRuntime, project: str) -> str:
    return creative_source_signature(rt.project_intelligence(project)._project().to_dict())


def _load_session(rt: AgentRuntime, project: str, session_id: str) -> ChangeSession:
    store = _store(rt, project)
    s = store.get_session(session_id)
    if s is None:
        raise AgentError("CHANGE_SESSION_NOT_FOUND", f"change-session 不存在: {session_id}")
    return s


def _guard_legacy(session: ChangeSession) -> None:
    """§9：AI-7A 历史只读分析会话不可执行/不可推进。"""
    if session.legacy_analysis_only:
        raise AgentError("CHANGE_SESSION_STALE",
                         "AI-7A 历史只读分析会话，不可作为可执行 AI-7B proposal 推进（§9）")


# ============ create（§10/§11）============
def create_session(rt: AgentRuntime, project: str, original_user_request: str, *,
                   source: str = "team_chat",
                   source_agent_id: Optional[str] = None,
                   source_session_id: Optional[str] = None,
                   source_message_id: Optional[str] = None) -> dict:
    """创建 ChangeRequest（proposal_only）+ ChangeSession（routing），捕获 source_snapshot_signature。
    7B-1：不后台推进（推进机在 7B-2..4）。original_user_request 不可变（§10/§105）。"""
    sig = _current_signature(rt, project)
    now = _now()
    req = ChangeRequest(
        request_id=_new_id("cr"), project_name=project, source=source,
        source_agent_id=source_agent_id, source_session_id=source_session_id,
        source_message_id=source_message_id, original_user_request=original_user_request,
        source_snapshot_signature=sig, mode="proposal_only", created_at=now)
    session = ChangeSession(
        session_id=_new_id("cs"), project_name=project, request_id=req.request_id,
        status="routing", source_snapshot_signature=sig,
        schema_version=2, created_at=now, updated_at=now)
    store = _store(rt, project)
    store.save_request(req)
    store.save_session(session)
    return {"session": session.to_dict(), "request": req.to_dict()}


def get_session(rt: AgentRuntime, project: str, session_id: str) -> Optional[dict]:
    s = _store(rt, project).get_session(session_id)
    return s.to_dict() if s is not None else None


def list_sessions(rt: AgentRuntime, project: str) -> dict:
    return {"sessions": [s.to_dict() for s in _store(rt, project).list_sessions()]}


def get_plan(rt: AgentRuntime, project: str, session_id: str) -> Optional[dict]:
    store = _store(rt, project)
    plans = store.list_plans(session_id)
    if not plans:
        return None
    # 返回当前 active plan（version 最大）+ 其 preview
    plan = plans[0]
    out = plan.to_dict()
    if plan.preview_id:
        pv = store.get_preview(plan.preview_id)
        if pv is not None:
            out["preview"] = pv.to_dict()
    return out


def get_preview(rt: AgentRuntime, project: str, session_id: str) -> Optional[dict]:
    store = _store(rt, project)
    plans = store.list_plans(session_id)
    if not plans or not plans[0].preview_id:
        return None
    pv = store.get_preview(plans[0].preview_id)
    return pv.to_dict() if pv is not None else None


# ============ resume（§25/§87：幂等推进；§90：stale 守卫）============
def _has_completed_proposal(store: ChangeProtocolStore, session_id: str,
                            owner_agent_id: str, round: int) -> bool:
    """该 (agent, round) 是否已有 completed 提案（重启后不重跑，§25）。"""
    return any(p.owner_agent_id == owner_agent_id and p.round == round
               and p.status == "completed"
               for p in store.list_proposals(session_id))


async def _run_first_round_proposals(rt: AgentRuntime, project: str,
                                     session: ChangeSession,
                                     change_request: ChangeRequest,
                                     client) -> List:
    """§24/§25：分类路由 → 独立首轮提案并行 gather（persist-before-gather 在各 runtime 内）。
    已 completed 的 (agent, round) 不重跑（§25）。Director 需 scene scope，无则延后 round 2（§26，7B-3）。"""
    intel = rt.project_intelligence(project)
    proj = intel._project()
    classification = classify_request(change_request.original_user_request, proj)
    modules = classification.get("modules") or []
    store = _store(rt, project)
    sig = session.source_snapshot_signature
    scene_refs = classification.get("scene_refs", [])
    coros = []
    for agent_id in modules:
        if _has_completed_proposal(store, session.session_id, agent_id, 1):
            continue   # §25：已完成不重跑
        if agent_id == "script_writer":
            coros.append(run_script_proposal(rt, project, session.session_id,
                                              change_request, sig, round=1, client=client))
        elif agent_id == "character_designer":
            coros.append(run_character_proposal(rt, project, session.session_id,
                                                change_request, sig, round=1, client=client))
        elif agent_id == "director" and scene_refs:
            # Director scene-scoped：首轮取第一个命中场景；无 scene scope → 延后 round 2（§26）
            coros.append(run_storyboard_proposal(rt, project, session.session_id,
                                                  change_request, sig, "scene",
                                                  scene_refs[0], round=1, client=client))
    if not coros:
        return []
    results = await asyncio.gather(*coros, return_exceptions=True)
    out = []
    for r in results:
        if isinstance(r, Exception):
            # runtime 应返回 failed proposal 而非抛；此处兜底（记一条失败合成 proposal）
            mp = __import__("src.agents.change_protocol.models", fromlist=["ModuleProposal"]).ModuleProposal(
                proposal_id=_new_id("mp"), change_session_id=session.session_id,
                module="unknown", owner_agent_id="unknown", round=1, status="failed",
                blockers=[f"runtime exception: {str(r)[:200]}"],
                source_snapshot_signature=sig, created_at=_now(), updated_at=_now())
            store.save_proposal(mp)
            out.append(mp)
        else:
            out.append(r)
    return out


async def resume_session(rt: AgentRuntime, project: str, session_id: str) -> dict:
    """幂等推进会话。终端态直接返回；stale→stale；legacy→拒。
    7B-2：routing/consulting → 跑首轮提案（并行，§25）→ 全完成→analyzing_dependencies
    （7B-3 接通依赖/冲突/编译）；部分失败→blocked（§103）。已完成提案不重跑（§25/§87）。"""
    store = _store(rt, project)
    session = _load_session(rt, project, session_id)
    _guard_legacy(session)
    if session.status in SESSION_TERMINAL:
        return session.to_dict()
    # §90 stale
    if session.source_snapshot_signature:
        cur = _current_signature(rt, project)
        if cur != session.source_snapshot_signature:
            session.status = "stale"
            store.save_session(session)
            return session.to_dict()

    if session.status in ("routing", "consulting"):
        session.status = "consulting"
        store.save_session(session)
        change_request = store.get_request(session.request_id)
        if change_request is None:
            session.status = "failed"
            session.error = "关联 ChangeRequest 丢失"
            store.save_session(session)
            return session.to_dict()
        proposals = await _run_first_round_proposals(
            rt, project, session, change_request,
            client=rt._resolve_client_or_none())
        # 汇总 proposal_ids（含已完成的历史 + 本轮新增）
        all_ids = {p.proposal_id for p in store.list_proposals(session.session_id)}
        session.module_proposal_ids = sorted(all_ids)
        failed = [p for p in proposals if p.status == "failed"]
        if failed:
            session.status = "blocked"
            session.error = f"{len(failed)} 个提案失败，请恢复后重试"
            store.save_session(session)
            return session.to_dict()
        # 全完成 → 下一阶段（依赖/冲突/编译/投影在 7B-3/7B-4）
        session.status = "analyzing_dependencies"
        store.save_session(session)
        return session.to_dict()

    # analyzing_dependencies / awaiting_user_decision / ready_for_review / building_preview
    # → 7B-3/7B-4 尚未接通；幂等返回当前态，不谎报进度。
    return session.to_dict()


# ============ approve（§76/§94：批准 ≠ 执行；硬停止）============
def approve_plan(rt: AgentRuntime, project: str, session_id: str,
                 plan_id: str, plan_version: int) -> dict:
    """批准计划。只做：重载签名 → 验非 stale → 验 active 版本 + preview 存在 + 校验 PASS +
    0 blocking decision → 持久化 approval + 置 approved_for_execution → STOP。
    绝不 apply / 不写 canonical / Workspace / Production / 不触发生成（§76/§119/L）。"""
    store = _store(rt, project)
    session = _load_session(rt, project, session_id)
    _guard_legacy(session)

    # 幂等（§88）：已批准且签名一致 → 直接返回
    if session.status == "approved_for_execution":
        plan0 = store.get_plan(plan_id)
        ap = plan0.approval if plan0 else None
        if ap and ap.get("plan_version") == plan_version:
            return {"approved": True, "idempotent": True,
                    "session_id": session_id, "plan_id": plan_id,
                    "status": "approved_for_execution"}

    plan = store.get_plan(plan_id)
    if plan is None:
        raise AgentError("PLAN_NOT_READY", f"plan 不存在: {plan_id}")
    if plan.change_session_id != session_id:
        raise AgentError("PLAN_OWNER_MISMATCH", "plan 不属于该 change-session")
    # active 版本
    if session.active_plan_id != plan_id:
        raise AgentError("PLAN_VERSION_CHANGED", "非当前 active plan")
    if plan.version != plan_version:
        raise AgentError("PLAN_VERSION_CHANGED",
                         f"plan_version={plan.version} ≠ 请求 {plan_version}")
    if plan.status not in ("preview_ready", "validated", "approved_for_execution"):
        raise AgentError("PLAN_NOT_READY", f"plan 状态={plan.status}，未就绪")
    # preview 存在
    if not plan.preview_id:
        raise AgentError("PLAN_PREVIEW_FAILED", "plan 无 preview")
    preview = store.get_preview(plan.preview_id)
    if preview is None:
        raise AgentError("PLAN_PREVIEW_FAILED", "preview artifact 不存在")
    if preview.applyable is not False:   # §65 硬不变量（理论上恒 False，防御）
        raise AgentError("PLAN_PREVIEW_FAILED", "preview applyable 非 False")
    # 校验 PASS（无 error-severity issue）
    err_issues = [i for i in plan.validation_issues
                  if i.get("severity", "error") == "error"]
    if err_issues:
        raise AgentError("PLAN_VALIDATION_FAILED",
                         f"plan 存在 {len(err_issues)} 个校验错误")
    # 0 blocking decision（§47）
    blocking = [d for d in store.list_decisions(session_id)
                if d.blocking and d.status == "pending"]
    if blocking:
        raise AgentError("PLAN_USER_DECISION_REQUIRED",
                         f"{len(blocking)} 个 blocking decision 未回答")
    # §90 source signature 重检（approval 前）
    cur_sig = _current_signature(rt, project)
    if session.source_snapshot_signature and cur_sig != session.source_snapshot_signature:
        session.status = "stale"
        store.save_session(session)
        raise AgentError("PLAN_SOURCE_STALE", "项目在批准前已变化（source signature 不匹配）")
    if plan.source_snapshot_signature and cur_sig != plan.source_snapshot_signature:
        plan.status = "stale"
        store.save_plan(plan)
        session.status = "stale"
        store.save_session(session)
        raise AgentError("PLAN_SOURCE_STALE", "plan 基于的项目版本已变化")

    # 持久化 approval + 置状态（§76）—— **STOP，不 apply**
    approval = PlanApproval(
        plan_id=plan_id, plan_version=plan.version, decision="approved",
        source_snapshot_signature=cur_sig, decided_at=_now())
    plan.approval = approval.to_dict()
    plan.status = "approved_for_execution"
    store.save_plan(plan)
    session.status = "approved_for_execution"
    store.save_session(session)
    return {"approved": True, "idempotent": False,
            "session_id": session_id, "plan_id": plan_id,
            "plan_version": plan.version,
            "status": "approved_for_execution",
            "approval": approval.to_dict()}


# ============ reject（§80）============
def reject_plan(rt: AgentRuntime, project: str, session_id: str,
                plan_id: Optional[str] = None) -> dict:
    """拒绝计划：session=rejected；plan=rejected。保留历史 artifact（§80）。0 mutation。"""
    store = _store(rt, project)
    session = _load_session(rt, project, session_id)
    _guard_legacy(session)
    session.status = "rejected"
    store.save_session(session)
    if plan_id:
        plan = store.get_plan(plan_id)
        if plan is not None and plan.change_session_id == session_id:
            plan.status = "rejected"
            store.save_plan(plan)
    return {"rejected": True, "session_id": session_id, "plan_id": plan_id}


# ============ request_changes（§78：版本链，不覆写历史）============
def request_changes(rt: AgentRuntime, project: str, session_id: str,
                    feedback_message: str, plan_id: Optional[str] = None) -> dict:
    """用户请求修改 → plan version+1（§78/§79）。旧 plan=superseded（保留）；新 plan=draft
    （parent_plan_id 指向旧）。仅受影响咨询重跑在 7B-2..4；7B-1 只做版本骨架。"""
    store = _store(rt, project)
    session = _load_session(rt, project, session_id)
    _guard_legacy(session)
    cur_sig = _current_signature(rt, project)
    if session.source_snapshot_signature and cur_sig != session.source_snapshot_signature:
        session.status = "stale"
        store.save_session(session)
        raise AgentError("PLAN_SOURCE_STALE", "项目已变化，请基于当前版本重新分析")
    old_plan = None
    if plan_id:
        old_plan = store.get_plan(plan_id)
        if old_plan is None or old_plan.change_session_id != session_id:
            raise AgentError("PLAN_NOT_READY", "plan 不存在或不属于该 session")
        old_plan.status = "superseded"
        store.save_plan(old_plan)
    new_version = (old_plan.version + 1) if old_plan else (session.plan_version + 1)
    new_plan = UnifiedChangePlan(
        plan_id=_new_id("plan"), change_session_id=session_id,
        project_name=project, version=new_version,
        source_snapshot_signature=cur_sig, status="draft",
        parent_plan_id=old_plan.plan_id if old_plan else None,
        user_feedback_message_id=feedback_message or None, created_at=_now())
    store.save_plan(new_plan)
    session.active_plan_id = new_plan.plan_id
    session.plan_version = new_version
    session.status = "routing"   # 重新进入推进（7B-2..4 触发定向再咨询）
    store.save_session(session)
    return {"plan": new_plan.to_dict(), "superseded_plan_id": old_plan.plan_id if old_plan else None}


# ============ submit_decision（§93）============
def submit_decision(rt: AgentRuntime, project: str, session_id: str,
                    decision_id: str, option_id: str) -> dict:
    """回答决策（§93）：验决策属该 session + pending + option 合法 + 非 stale → 置 answered。
    仅触发受影响 proposal 重跑（7B-2..4）；7B-1 只持久化决策 + 幂等返回。"""
    store = _store(rt, project)
    session = _load_session(rt, project, session_id)
    _guard_legacy(session)
    dec = store.get_decision(decision_id)
    if dec is None:
        raise AgentError("CHANGE_SESSION_NOT_FOUND", f"decision 不存在: {decision_id}")
    if dec.change_session_id != session_id:
        raise AgentError("PLAN_REFERENCE_CONFLICT", "decision 不属于该 change-session")
    if dec.status != "pending":
        raise AgentError("PLAN_ALREADY_REJECTED", f"decision 状态={dec.status}，不可回答")
    option_ids = [o.get("option_id") for o in dec.options]
    if option_id not in option_ids:
        raise AgentError("PLAN_REFERENCE_CONFLICT",
                         f"option_id {option_id} 非法（合法: {option_ids}）")
    # stale
    if session.source_snapshot_signature:
        cur_sig = _current_signature(rt, project)
        if cur_sig != session.source_snapshot_signature:
            session.status = "stale"
            store.save_session(session)
            raise AgentError("PLAN_SOURCE_STALE", "项目已变化")
    dec.status = "answered"
    dec.selected_option_id = option_id
    store.save_decision(dec)
    session.pending_decision_ids = [d for d in session.pending_decision_ids if d != decision_id]
    store.save_session(session)
    return {"decision_id": decision_id, "status": "answered",
            "selected_option_id": option_id, "session": session.to_dict()}
