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
import json
import secrets
import time
from typing import List, Optional

from src.revision.models import creative_source_signature

from ..runtime import AgentRuntime, AgentError
from ..inquiry import classify_request, _pack_text
from .models import (ChangeRequest, ChangeSession, UnifiedChangePlan, PlanApproval,
                      ChangeDecisionRequest, ChangeDecisionOption, ModuleProposal,
                      ModuleProposalItem, SESSION_TERMINAL, SESSION_SETTLED)
# AI-7D D3：EXEC_TERMINAL 用于 active_workflow_snapshot 的 execution 终态过滤
#（execution_models 仅 stdlib import，无循环依赖）。
from .execution_models import EXEC_TERMINAL
from .store import ChangeProtocolStore
from .script_proposal import run_script_proposal
from .character_proposal import run_character_proposal
from .storyboard_proposal import run_storyboard_proposal
from .dependency import build_dependency_facts
from .impact import compute_proposal_impact
from .conflicts import analyze_conflicts, has_unresolved_hard
from .plan_compiler import compile_change_plan
from .plan_validator import validate as validate_plan, has_errors as has_validation_errors
from .projection import ProposalProjectionEngine
from src.revision.changeset import ChangeSetError


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
                   source_message_id: Optional[str] = None,
                   context_scope: Optional[dict] = None,
                   client_request_id: Optional[str] = None) -> dict:
    """创建 ChangeRequest（proposal_only）+ ChangeSession（routing），捕获 source_snapshot_signature。
    7B-1：不后台推进（推进机在 7B-2..4）。original_user_request 不可变（§10/§105）。

    AI-7C-H H3（澄清策略 §9）：context_scope = 调用方页面上下文
    {scene_id?, shot_id?, character_name?}。用户说「让这一段更紧张一点」时不再反问
    Which Scene——scope 与请求一并持久化（entity_refs），分类/提案路由自动合并。

    AI-7D D2.5：client_request_id 幂等——同 id 已存在则返回既有 session（Direct Chat
    双击/retry/F5 不造重复 ChangeSession）。"""
    store = _store(rt, project)
    if client_request_id:
        existing = store.get_session_by_client_request(client_request_id)
        if existing is not None:
            req = store.get_request(existing.request_id)
            return {"session": existing.to_dict(),
                    "request": req.to_dict() if req else None, "idempotent": True}
    sig = _current_signature(rt, project)
    now = _now()
    entity_refs: List[dict] = []
    cs = context_scope or {}
    if cs.get("scene_id"):
        entity_refs.append({"module": "storyboard", "entity_type": "scene",
                            "entity_id": str(cs["scene_id"])})
    if cs.get("shot_id"):
        entity_refs.append({"module": "storyboard", "entity_type": "shot",
                            "entity_id": str(cs["shot_id"])})
    if cs.get("character_name"):
        entity_refs.append({"module": "character", "entity_type": "character",
                            "entity_id": str(cs["character_name"])})
    req = ChangeRequest(
        request_id=_new_id("cr"), project_name=project, source=source,
        source_agent_id=source_agent_id, source_session_id=source_session_id,
        source_message_id=source_message_id, client_request_id=client_request_id,
        original_user_request=original_user_request,
        entity_refs=entity_refs,
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


# ============ 只读工作流快照（AI-7C-H §13：进度答案来自持久化状态）============
def _execution_snapshot(ex) -> dict:
    """单 execution 的紧凑真相：stage=制作进度权威粒度 + ProductionRun 真状态。
    AI-7D D3.1：附 production_run status（复用 execution_service._get_owned_run 同源模式，
    best-effort，0 mutation，异常→不含 production_run_status）。使「现在在生成角色图还是做
    分镜」/「还有几个镜头」接地（stage=character_sync→角色图；production_planning/production→
    分镜/镜头；production_run_status→制作流真态）。"""
    out = {
        "execution_id": ex.execution_id, "status": ex.status, "stage": ex.stage,
        "blocker_code": (ex.blocker or {}).get("code"),
        "characters_synced": [r.get("character_name") for r in (ex.character_sync or [])],
        "planned_scenes": [p.get("scene_id") for p in (ex.planning or [])],
        "production_run_id": ex.production_run_id,
    }
    if ex.production_run_id:
        try:
            from src.ai.project_autopilot import get_project_autopilot_manager
            run = get_project_autopilot_manager().get_run(ex.project_name, ex.production_run_id)
            if run is not None:
                out["production_run_status"] = run.status
        except Exception:
            pass
    return out


def active_workflow_snapshot(project_dir, project_name: str, limit: int = 5) -> List[dict]:
    """只读：当前项目活跃工作流快照（Layer B 运行时真相）。供 Producer/Agent 回答「现在做到
    哪了」+ unified_turn runtime_control 目标解析。LLM 只许引用此状态、不得凭对话历史臆测。
    0 mutation；异常返回 []（调用方安全降级）。

    AI-7D D3：execution-aware——以**非终态 execution**为首选（含 approved_for_execution
    session 下的 running execution；旧实现只列非终态 session，而 approved_for_execution 属
    SESSION_TERMINAL → 漏掉已批准+运行中的 execution，使「现在做到哪了」/「停止」在执行期
    不可见）。再补非终态 session（提案/编译期，无活跃 execution）。每条 execution 附真
    ProductionRun status（_execution_snapshot）。request 字段存 original_user_request 文本
    （供控制回复引用「已停止「扩写中段」」）。"""
    try:
        store = ChangeProtocolStore(project_dir, project_name)
        out: List[dict] = []
        seen: set = set()

        def _req_text(sess) -> str:
            if not (sess and sess.request_id):
                return ""
            req = store.get_request(sess.request_id)
            return (req.original_user_request or "") if req else ""

        # 1. 非终态 execution（含 approved session 下的运行执行）——执行期真相首选
        for ex in store.list_executions():
            if ex.status in EXEC_TERMINAL:
                continue
            sess = store.get_session(ex.change_session_id)
            out.append({
                "session_id": ex.change_session_id,
                "status": sess.status if sess else "approved_for_execution",
                "request": _req_text(sess),
                "updated_at": ex.updated_at or (sess.updated_at if sess else 0),
                "execution": _execution_snapshot(ex),
            })
            seen.add(ex.change_session_id)
        # 2. 非终态 session 且无活跃 execution（提案/编译期：routing/consulting/.../
        #    ready_for_review）——旧实现已覆盖，保留以答「方案做到哪了」
        for s in store.list_sessions():
            if s.status in SESSION_TERMINAL or s.session_id in seen:
                continue
            out.append({
                "session_id": s.session_id, "status": s.status,
                "request": _req_text(s), "updated_at": s.updated_at,
            })
        out.sort(key=lambda e: e.get("updated_at") or 0, reverse=True)
        return out[:limit]
    except Exception:
        return []


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


def _classification_with_scope(request_text: str, proj, change_request,
                                session: Optional[ChangeSession] = None) -> dict:
    """H3 §9：classify_request + 持久化 context_scope（entity_refs）合并。
    页面上下文（选中 Scene/Shot/Character）自动解析指向，Agent 不反问 Which Scene/Character。
    文本里显式写出的 ref 优先（去重合并），scope 仅补齐缺失。
    模块补充**只看 scope**（entity_refs）：文本提及角色名只是叙事引用（如「让 Aran 离开城市」
    是剧本变更），不代表要改角色设定——不据此加 character_designer（回归测试锚定该语义）。

    AI-7D-R §11：修订反馈（session.latest_feedback）拼入分类文本，使反馈真正影响路由。"""
    effective_text = request_text
    if session is not None and getattr(session, "latest_feedback", None):
        effective_text = request_text + "\n[用户对方案的修订反馈] " + session.latest_feedback
    classification = dict(classify_request(effective_text, proj))
    scope_chars, scope_scenes, scope_shots = [], [], []
    for ref in (change_request.entity_refs or []):
        et, eid = ref.get("entity_type"), ref.get("entity_id")
        if not et or not eid:
            continue
        if et == "scene":
            scope_scenes.append(eid)
            if eid not in classification.get("scene_refs", []):
                classification["scene_refs"] = classification.get("scene_refs", []) + [eid]
        elif et == "shot":
            scope_shots.append(eid)
            if eid not in classification.get("shot_refs", []):
                classification["shot_refs"] = classification.get("shot_refs", []) + [eid]
        elif et == "character":
            scope_chars.append(eid)
            if eid not in classification.get("character_refs", []):
                classification["character_refs"] = classification.get("character_refs", []) + [eid]
    # 模块补充只由 scope 驱动：character scope → 补 character_designer；scene/shot scope → 补 director
    modules = classification.get("modules") or []
    if scope_chars and "character_designer" not in modules:
        modules = modules + ["character_designer"]
    if (scope_scenes or scope_shots) and "director" not in modules:
        modules = modules + ["director"]
    classification["modules"] = modules
    return classification


async def _run_first_round_proposals(rt: AgentRuntime, project: str,
                                     session: ChangeSession,
                                     change_request: ChangeRequest,
                                     client) -> List:
    """§24/§25：分类路由 → 独立首轮提案并行 gather（persist-before-gather 在各 runtime 内）。
    已 completed 的 (agent, round) 不重跑（§25）。Director 需 scene scope，无则延后 round 2（§26，7B-3）。
    H3：分类合并持久化 context_scope（entity_refs）——页面上下文即解析指向，不反问。"""
    intel = rt.project_intelligence(project)
    proj = intel._project()
    classification = _classification_with_scope(
        change_request.original_user_request, proj, change_request, session=session)
    modules = classification.get("modules") or []
    store = _store(rt, project)
    sig = session.source_snapshot_signature
    scene_refs = classification.get("scene_refs", [])
    # AI-7D-R §11：修订反馈必须真实到达 agent。用 effective_request（原文+反馈）替换传入
    # proposal runtime 的不可变 original_user_request（不修改持久化的 ChangeRequest）。
    import dataclasses as _dc
    effective_request = change_request
    if getattr(session, "latest_feedback", None):
        effective_request = _dc.replace(
            change_request,
            original_user_request=(change_request.original_user_request
                                   + "\n[用户对方案的修订反馈] " + session.latest_feedback))
    coros = []
    for agent_id in modules:
        if _has_completed_proposal(store, session.session_id, agent_id, 1):
            continue   # §25：已完成不重跑
        if agent_id == "script_writer":
            coros.append(run_script_proposal(rt, project, session.session_id,
                                              effective_request, sig, round=1, client=client))
        elif agent_id == "character_designer":
            coros.append(run_character_proposal(rt, project, session.session_id,
                                                effective_request, sig, round=1, client=client))
        elif agent_id == "director" and scene_refs:
            # Director scene-scoped：首轮取第一个命中场景；无 scene scope → 延后 round 2（§26）
            coros.append(run_storyboard_proposal(rt, project, session.session_id,
                                                  effective_request, sig, "scene",
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


# ============ 决策选项生成（§35；Producer LLM 给两个互斥选项）============
DECISION_OPTIONS_SYSTEM = """你是 AGNES 的 Producer。用户的变更请求存在创作歧义，需用户决策。
基于请求与歧义描述，给出**恰好两个**简短、人类可读、互斥的选项（A/B）。
只返回 JSON，不要解释文字：
{"options":[{"option_id":"a","label":"...","description":"..."},{"option_id":"b","label":"...","description":"..."}]}"""


def _parse_decision_options(raw):
    from .proposal_helpers import parse_proposal_json
    obj = parse_proposal_json(raw)
    opts = obj.get("options") or []
    if not isinstance(opts, list) or len(opts) < 2:
        raise ValueError("options 需至少 2 项")
    out = []
    for o in opts[:2]:
        oid = str(o.get("option_id") or "").strip() or ("ab"[len(out)])
        out.append({"option_id": oid, "label": str(o.get("label") or "")[:120],
                    "description": str(o.get("description") or "")[:300]})
    return out


async def _producer_decision_options(client, request_text: str, decision_text: str) -> List[dict]:
    """§35：Producer 给两个互斥选项。无 client → 退化占位（A/B）。"""
    from .proposal_helpers import call_and_parse
    fallback = [
        ChangeDecisionOption(option_id="a", label="方案 A", description=decision_text[:80]).to_dict(),
        ChangeDecisionOption(option_id="b", label="方案 B", description=decision_text[:80]).to_dict()]
    if client is None:
        return fallback
    messages = [{"role": "system", "content": DECISION_OPTIONS_SYSTEM},
                {"role": "user", "content": f"用户请求：{request_text}\n歧义：{decision_text}\n只返回 JSON。"}]
    parsed, err = await call_and_parse(client, messages, _parse_decision_options)
    if err or not parsed:
        return fallback
    return parsed


async def _create_decisions_for_proposals(rt, project: str, session: ChangeSession,
                                          completed: List[ModuleProposal],
                                          request: ChangeRequest, client) -> List[ChangeDecisionRequest]:
    """§34/§35：proposal.user_decisions_needed → ChangeDecisionRequest（blocking）。幂等（不重建已有）。"""
    store = _store(rt, project)
    existing = store.list_decisions(session.session_id)
    decisions: List[ChangeDecisionRequest] = []
    for prop in completed:
        for need in prop.user_decisions_needed:
            already = next((d for d in existing if need in (d.question or "")), None)
            if already:
                decisions.append(already)
                continue
            opts = await _producer_decision_options(client, request.original_user_request, need)
            dec = ChangeDecisionRequest(
                decision_id=_new_id("dec"), change_session_id=session.session_id,
                question=need, options=opts, blocking=True, status="pending",
                created_at=_now(), updated_at=_now())
            store.save_decision(dec)
            decisions.append(dec)
    return decisions


def _director_round2_target(completed: List[ModuleProposal]) -> Optional[str]:
    """§26：Director 在 round 1 因无 scene scope 延后；Script 定稿受影响场景后，round 2 问 Director。
    返回应问的 scene_id，或 None（无需 round 2）。"""
    if any(p.module == "storyboard" for p in completed):
        return None   # Director 已参与
    for p in completed:
        if p.module == "script":
            for it_dict in p.items:
                item = ModuleProposalItem.from_dict(it_dict)
                sid = (item.intent or {}).get("scene_id") or (item.intent or {}).get("after_scene_id")
                if sid:
                    return sid
    return None


async def _run_director_round2(rt, project: str, session: ChangeSession,
                               request: ChangeRequest, completed: List[ModuleProposal], client):
    target = _director_round2_target(completed)
    if not target:
        return
    await run_storyboard_proposal(rt, project, session.session_id, request,
                                  session.source_snapshot_signature, "scene", target,
                                  round=2, client=client)


# ============ 分析 + 编译 + 校验（§23/§27/§28/§33/§41）============
def _terminate_no_action(rt: AgentRuntime, project: str, session: ChangeSession,
                          completed: List[ModuleProposal], request: ChangeRequest,
                          store: ChangeProtocolStore) -> dict:
    """AI-7D-R §10：所有 specialist 都确认没有可执行改动，也无真实待决问题 → 终态 no_action。
    不伪造 A/B 方案、不声称已修改。把诚实结论写回来源直聊会话（耐久，非前端合成），
    并记录共享工作记忆。"""
    summaries = [p.summary for p in completed if p.summary]
    reason = summaries[0] if summaries else "已检视当前项目，这条请求无需产生实际修改。"
    session.status = "no_action"
    session.no_action_reason = reason
    session.error = None
    store.save_session(session)
    # 写回来源直聊会话（best-effort），让“刚才那个不要”等在转录里有真实耐久记录
    if request.source_session_id:
        try:
            rt.append_session_message(
                project, request.source_session_id, "assistant",
                f"我核对了当前项目状态：{reason} 没有产生项目修改。",
                linked_work_ids=[session.session_id])
        except Exception:
            pass
    try:
        rt.record_work(project, "change", reason or "无可执行改动",
                       participants=[p.owner_agent_id for p in completed],
                       references={"change_session_id": session.session_id},
                       affected_domains=[], status="no_action")
    except Exception:
        pass
    return session.to_dict()


async def _analyze_and_compile(rt: AgentRuntime, project: str, session: ChangeSession) -> dict:
    """7B-3：proposals 完成后 → 依赖/impact/冲突 → 创作决策 → 定向 round 2 → 编译 plan → 校验。
    终态：awaiting_user_decision（有 blocking 决策）/ blocked（硬冲突或校验失败）/ validating_plan
    （plan validated，preview 留 7B-4）。纯代码分析 + Producer 决策 LLM（不重跑 proposal LLM）。"""
    store = _store(rt, project)
    intel = rt.project_intelligence(project)
    proj = intel._project()
    # §90 stale
    if session.source_snapshot_signature:
        cur = _current_signature(rt, project)
        if cur != session.source_snapshot_signature:
            session.status = "stale"
            store.save_session(session)
            return session.to_dict()
    request = store.get_request(session.request_id)
    if request is None:
        session.status = "failed"
        session.error = "关联 ChangeRequest 丢失"
        store.save_session(session)
        return session.to_dict()
    completed = [p for p in store.list_proposals(session.session_id) if p.status == "completed"]
    if not completed:
        session.status = "blocked"
        session.error = "无 completed 提案可编译"
        store.save_session(session)
        return session.to_dict()
    cli = rt._resolve_client_or_none()

    # §34/§35：创作歧义 → 阻塞决策（仅 pending 才等待；已答的计入 plan.user_decision_ids）
    decisions = await _create_decisions_for_proposals(rt, project, session, completed, request, cli)
    pending = [d for d in decisions if d.status == "pending"]
    if pending:
        session.pending_decision_ids = [d.decision_id for d in pending]
        session.status = "awaiting_user_decision"
        store.save_session(session)
        return session.to_dict()
    answered_decision_ids = [d.decision_id for d in decisions if d.status == "answered"]

    # AI-7D-R §10：所有提案都没有任何可执行 item，也没有真实待决问题 → 诚实的 no_action
    # 终态（不伪造 A/B 方案、不假装修改）。前端/对话据此自然回复“无需改动”。
    any_items = any((p.items or []) for p in completed)
    if not any_items and not answered_decision_ids:
        return _terminate_no_action(rt, project, session, completed, request, store)

    # §27/§28/§33：确定性事实
    dep_facts = build_dependency_facts(completed, intel)
    impact_facts = compute_proposal_impact(completed, proj)
    conflicts = analyze_conflicts(completed, proj)
    # §46：未解决硬冲突 → blocked（§26：硬不相容 → BLOCKED）
    if has_unresolved_hard(conflicts):
        session.status = "blocked"
        session.error = "存在未解决的结构硬冲突"
        store.save_session(session)
        return session.to_dict()

    # §26：定向 round 2（Director 延后且 Script 定稿了场景）
    if _director_round2_target(completed) and session.consultation_round < 2:
        session.consultation_round = 2
        session.status = "consulting"
        store.save_session(session)
        await _run_director_round2(rt, project, session, request, completed, cli)
        completed = [p for p in store.list_proposals(session.session_id) if p.status == "completed"]
        dep_facts = build_dependency_facts(completed, intel)
        impact_facts = compute_proposal_impact(completed, proj)
        conflicts = analyze_conflicts(completed, proj)
        if has_unresolved_hard(conflicts):
            session.status = "blocked"
            session.error = "存在未解决的结构硬冲突（round 2 后）"
            store.save_session(session)
            return session.to_dict()

    # §23：编译 UnifiedChangePlan（Producer 只编排，不写专业内容）
    session.status = "synthesizing_plan"
    store.save_session(session)
    version = (session.plan_version or 0) + 1
    plan = compile_change_plan(session, request, completed, dep_facts, impact_facts,
                               conflicts, decision_ids=answered_decision_ids, version=version,
                               source_signature=session.source_snapshot_signature,
                               project=proj, parent_plan_id=session.active_plan_id)
    # §41：校验
    session.status = "validating_plan"
    store.save_session(session)
    blocking_pending = len([d for d in store.list_decisions(session.session_id)
                            if d.blocking and d.status == "pending"])
    issues = validate_plan(request, session, plan, completed, proj, impact_facts,
                           conflicts, blocking_pending_decisions=blocking_pending)
    plan.validation_issues = issues
    if has_validation_errors(issues):
        plan.status = ("needs_user_decision"
                       if any(i["code"] == "PLAN_USER_DECISION_REQUIRED" for i in issues)
                       else "draft")
        store.save_plan(plan)
        session.active_plan_id = plan.plan_id
        session.plan_version = version
        session.status = "blocked"
        session.error = json.dumps(issues, ensure_ascii=False)[:500]
        store.save_session(session)
        return session.to_dict()
    # §48/§65：plan validated；applyable 恒 False（preview 留 7B-4 → preview_ready）
    plan.status = "validated"
    store.save_plan(plan)
    session.active_plan_id = plan.plan_id
    session.plan_version = version
    session.status = "validating_plan"   # plan 已 validated；7B-4 建 preview → building_preview → ready_for_review
    store.save_session(session)
    return session.to_dict()


# ============ 预览构建（§60/§62/§65；7B-4）============
def _build_preview(rt: AgentRuntime, project: str, session: ChangeSession) -> dict:
    """validated plan → 纯内存投影 → PlanPreviewArtifact（零落盘，§61/§64）→ preview_ready。
    plan.status=preview_ready / session.status=ready_for_review。投影失败→blocked。"""
    store = _store(rt, project)
    intel = rt.project_intelligence(project)
    proj = intel._project()
    # §90 stale
    if session.source_snapshot_signature:
        cur = _current_signature(rt, project)
        if cur != session.source_snapshot_signature:
            session.status = "stale"
            store.save_session(session)
            return session.to_dict()
    if not session.active_plan_id:
        session.status = "blocked"
        session.error = "无 active plan 可投影"
        store.save_session(session)
        return session.to_dict()
    plan = store.get_plan(session.active_plan_id)
    if plan is None or plan.status != "validated":
        session.status = "blocked"
        session.error = f"plan 状态={plan.status if plan else 'missing'}，需 validated"
        store.save_session(session)
        return session.to_dict()
    # §48 plan signature 重检
    cur = _current_signature(rt, project)
    if plan.source_snapshot_signature and cur != plan.source_snapshot_signature:
        plan.status = "stale"
        store.save_plan(plan)
        session.status = "stale"
        store.save_session(session)
        return session.to_dict()
    proposals = [p for p in store.list_proposals(session.session_id) if p.status == "completed"]
    session.status = "building_preview"
    store.save_session(session)
    try:
        pv = ProposalProjectionEngine().build_preview(proj, plan, proposals)
    except ChangeSetError as e:
        plan.validation_issues = list(plan.validation_issues) + [
            {"code": e.code, "message": e.message, "severity": "error"}]
        store.save_plan(plan)
        session.status = "blocked"
        session.error = f"projection failed: [{e.code}] {e.message}"
        store.save_session(session)
        return session.to_dict()
    store.save_preview(pv)
    plan.preview_id = pv.preview_id
    plan.status = "preview_ready"
    store.save_plan(plan)
    session.status = "ready_for_review"
    store.save_session(session)
    return session.to_dict()


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

    # 7B-3：依赖/impact/冲突 → 决策 → 定向 round 2 → 编译 plan → 校验
    if session.status == "analyzing_dependencies":
        return await _analyze_and_compile(rt, project, session)
    # 7B-4：validated plan → 纯内存投影 → preview_ready / ready_for_review
    if session.status == "validating_plan":
        return _build_preview(rt, project, session)
    # 用户回答决策后 → 重新分析+编译（§35：resume same ChangeSession，仅受影响 proposal 重跑在 7B-2+）
    if session.status == "awaiting_user_decision":
        blocking_pending = len([d for d in store.list_decisions(session.session_id)
                                if d.blocking and d.status == "pending"])
        if blocking_pending == 0:
            session.status = "analyzing_dependencies"
            store.save_session(session)
            return await _analyze_and_compile(rt, project, session)
        return session.to_dict()   # 仍有阻塞决策未答，等待用户

    # validating_plan(plan validated)/building_preview/ready_for_review → 7B-4 尚未接通；幂等返回
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


# ============ request_changes（§78 版本链 + AI-7D-R §7/§11 真实反馈）============
def request_changes(rt: AgentRuntime, project: str, session_id: str,
                    feedback_message: str, plan_id: Optional[str] = None) -> dict:
    """用户对**仍开放的**方案请求修改 → plan version+1（§78/§79），旧 plan=superseded。

    AI-7D-R 纠正（根因 #1）：
    - 已落定/blocked 的会话**不接受**新反馈——新变更必须基于最新 canonical 新建会话
      （SESSION_SETTLED 含 approved_for_execution/rejected/stale/failed/cancelled/no_action/blocked）。
      抛 CHANGE_SESSION_SETTLED，前端据此走新建路径，不再吞消息/撞 PLAN_SOURCE_STALE。
    - 反馈是真实修订指令：写入 session.latest_feedback，并把已有 completed 提案标记 superseded，
      使下一轮 resume 真正重跑 agent（不再“跳过已完成提案→从旧提案重编”，让反馈到达 LLM）。"""
    store = _store(rt, project)
    session = _load_session(rt, project, session_id)
    _guard_legacy(session)
    if session.status in SESSION_SETTLED:
        raise AgentError("CHANGE_SESSION_SETTLED",
                         "该变更会话已落定，新修改请基于当前项目版本新建会话")
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
    # AI-7D-R：把旧 round 的 completed 提案标 superseded，_has_completed_proposal 即不再跳过，
    # resume → _run_first_round_proposals 会用含 latest_feedback 的请求真实重跑各 agent。
    for prop in store.list_proposals(session_id):
        if prop.status == "completed":
            prop.status = "superseded"
            store.save_proposal(prop)
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
    session.latest_feedback = (feedback_message or "").strip() or None
    session.consultation_round = 1
    session.status = "routing"   # 重新进入推进（真实重跑受影响 agent）
    session.error = None
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
