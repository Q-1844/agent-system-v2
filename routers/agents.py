"""AI-6B Multi-Agent Foundation 路由（§27）。

composition root：本文件是唯一 import routers.production_planner 的地方，把 production-plan
读取包装成 callable 注入 AgentRuntime（§修正2：src/agents/* 绝不 import router）。
  routers / composition root  →  注入 plans_reader callable
  AgentRuntime / CapabilityContext  →  透传
  production_plan.inspect adapter  →  调用（不 import router）

错误映射（§29）：chat 未配置 Agnes 不是 HTTP 错误（send_message 软失败返回 status=not_configured，
§修正3）；HTTP 错误仅用于请求/配置校验类。
"""
from typing import List, Optional
import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import deps
from deps import get_project_manager, JOB_HANDLERS
from src.agents.runtime import get_agent_runtime, AgentError
from src.agents.fake_shot_handlers import all_fake_handlers
from src.agents.team_chat import (TeamChatStore, TeamMessage,
                                   run_team_inquiry_and_persist)
from src.agents.change_analysis import (ChangeAnalysisStore,
                                          create_change_analysis_session,
                                          continue_change_analysis)
from src.agents.change_protocol import service as cp_service
from src.agents.change_protocol import execution_service as cp_exec
from routers.production_planner import _read_plans   # 唯一 import 点（composition root）
from routers.workspaces import workspace_lock        # 复用同一 per-workspace 锁（AI-6D）

router = APIRouter()


# ============ composition root：注入 production-plan 读取 callable ============
def _plans_reader(project_name: str, scene_id: str) -> Optional[dict]:
    """(project, scene_id) -> plan dict or None。供 production_plan.inspect adapter 调用。"""
    try:
        pd = get_project_manager().get_project_dir(project_name)
    except Exception:
        return None
    plans = _read_plans(pd)   # {scene_id: SceneProductionPlan}
    plan = plans.get(scene_id)
    if plan is None:
        return None
    return plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()


# 启动期注入（get_agent_runtime() 构造无副作用，见 runtime.py）
get_agent_runtime().set_plans_reader(_plans_reader)


# ============ AI-6F.0 composition root：注册 fake shot handler 到现有 JobQueue ============
# §7：Shot Producer 长执行能力用 deterministic fake handler（不调 Agnes、不写真实资产）。
# 注册到 deps.JOB_HANDLERS（可变 dict），供 JobQueue 在 start 后 dispatch agent_* kind。
# kind 前缀 "agent_" 与真实 "first_frame"/"last_frame"/"video" 不冲突——真实 handler 仍在
# routers/shots.py 注册，本处仅补 fake 三键。AI-6F 接通真实生成后移除此注册。
for _kind, _fn in all_fake_handlers().items():
    JOB_HANDLERS.setdefault(_kind, _fn)

# AI-7C-S §10/L1：注册 character_views 真实 handler 到生产 JobQueue。
# 此前仅 gated_real_7c.py:73 注册；生产 composition root 未注册 → _real_character_view_generator
# 提交的 character_views job 无 handler → RuntimeError → CHARACTER_SYNC_FAILED（L1）。
# handler 复用既有 routers.characters chain（§44 prompt+segmentor），不另造生成系统（§12）。
from src.agents.character_views_handler import handler_character_views
JOB_HANDLERS.setdefault("character_views", handler_character_views)


# ============ AgentError.code -> HTTP 状态（§29）============
_HTTP = {
    "unknown_agent": 404, "unknown_capability": 404,
    "unknown_project": 404, "unknown_scope": 404,
    "disabled_agent": 400, "permission_denied": 403,
    "unknown_model_profile": 400, "invalid_model_config": 400,
    "task_failed": 500,
    # AI-6D Director Proposal
    "unknown_proposal": 404, "bad_scope": 404,
    "not_configured": 503, "agnes_failed": 502,
    "invalid_output": 422, "proposal_failed": 500,
    # ChangeSetError code 透传（generate_proposal/preview/apply，§17）
    "SCHEMA": 400, "DUP_SHOT_ID": 400, "REORDER_MISMATCH": 400,
    "CHAR_REF_INVALID": 400, "INVALID_DURATION": 400, "NOT_FOUND": 404,
    "WORKSPACE_CHANGED": 409, "STALE": 409, "CONFLICT": 409,
    "BEFORE_MISMATCH": 409, "BAD_STATUS": 409,
    # AI-7B Change Protocol 失败码（§101）
    "CHANGE_SESSION_NOT_FOUND": 404, "CHANGE_SESSION_STALE": 409,
    "MODULE_PROPOSAL_FAILED": 500, "MODULE_PROPOSAL_INVALID": 422,
    "PROPOSAL_OUTPUT_INVALID": 422, "PROPOSAL_REPAIR_FAILED": 422,
    "UNSUPPORTED_CHANGE_INTENT": 422,
    "PLAN_REQUEST_NOT_COVERED": 422, "PLAN_OWNER_MISMATCH": 409,
    "PLAN_DEPENDENCY_CYCLE": 422, "PLAN_UNRESOLVED_CONFLICT": 409,
    "PLAN_USER_DECISION_REQUIRED": 409, "PLAN_ENTITY_NOT_FOUND": 404,
    "PLAN_REFERENCE_CONFLICT": 409, "PLAN_LOCK_CONFLICT": 409,
    "PLAN_SOURCE_STALE": 409, "PLAN_VALIDATION_FAILED": 422,
    "PLAN_PREVIEW_FAILED": 422, "PLAN_VERSION_CHANGED": 409,
    "PLAN_NOT_READY": 409, "PLAN_ALREADY_REJECTED": 409,
    "PLAN_APPROVAL_STALE": 409,
    # AI-7C Execution 失败码（§78）
    "PLAN_NOT_APPROVED": 409, "PLAN_VERSION_MISMATCH": 409,
    "PLAN_EXECUTION_BINDING_MISSING": 422,
    "PROJECT_CHANGED_AFTER_APPROVAL": 409,
    "EXECUTION_NOT_FOUND": 404, "EXECUTION_PREVIEW_MISMATCH": 409,
    "PLAN_DEPENDENCY_CYCLE": 422,
    "PROJECT_PRODUCTION_ACTIVE": 409,
    "CANONICAL_COMMIT_RECONCILIATION_CONFLICT": 409,
    "PRODUCTION_PREREQUISITE_FAILED": 409, "CHARACTER_SYNC_FAILED": 500,
    "PRODUCTION_PLAN_INVALID": 422, "PRODUCTION_SCOPE_EXPANDED": 409,
}


# ============ AI-7B per-ChangeSession 锁（§89，镜像 workspace_lock）============
# 防两个 resume/approve 并发编译同一 plan。**不跨 LLM 调用持有**（AI-7B proposal-only）。
_SESSION_LOCKS: dict = {}


def session_lock(project_name: str, session_id: str) -> "asyncio.Lock":
    key = f"{project_name}/{session_id}"
    if key not in _SESSION_LOCKS:
        _SESSION_LOCKS[key] = asyncio.Lock()
    return _SESSION_LOCKS[key]


def _raise(e: AgentError):
    raise HTTPException(_HTTP.get(e.code, 400), f"[{e.code}] {e.message}")


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except AgentError as e:
        _raise(e)


# ============ 请求模型 ============
class CreateSessionReq(BaseModel):
    agent_id: str
    scope_type: str = "project"
    scope_id: str = ""


class SendMessageReq(BaseModel):
    content: str


class DelegateReq(BaseModel):
    requester_agent: str = "producer"
    target_agent: str
    scope_type: str = "scene"
    scope_id: str = ""
    instruction: str = ""
    capability_id: str
    operation: str = "inspect"


class AgentSettingReq(BaseModel):
    agent_id: str
    model_profile_id: Optional[str] = None
    enabled: Optional[bool] = None


# ==================== Agent / Capability / Profile ====================
@router.get("/api/agents")
async def list_agents():
    return {"agents": get_agent_runtime().list_agents()}


@router.get("/api/agents/profiles")
async def list_profiles():
    return {"profiles": get_agent_runtime().list_profiles()}


@router.get("/api/agents/{agent_id}/capabilities")
async def agent_capabilities(agent_id: str):
    _safe(lambda: get_agent_runtime()._resolve_agent(agent_id))
    return {"agent_id": agent_id, "capabilities": get_agent_runtime().list_capabilities(agent_id)}


# ==================== Settings ====================
@router.get("/api/agents/settings")
async def get_agent_settings():
    return get_agent_runtime().get_settings()


@router.put("/api/agents/settings")
async def update_agent_settings(req: AgentSettingReq):
    return _safe(get_agent_runtime().update_agent_setting,
                 req.agent_id, req.model_profile_id, req.enabled)


# ==================== Sessions ====================
@router.get("/api/project/{project_name}/agents/sessions")
async def list_sessions(project_name: str):
    try:
        return {"sessions": get_agent_runtime().list_sessions(project_name)}
    except AgentError as e:
        _raise(e)


@router.post("/api/project/{project_name}/agents/sessions")
async def create_session(project_name: str, req: CreateSessionReq):
    try:
        return get_agent_runtime().get_or_create_session(
            project_name, req.agent_id, req.scope_type, req.scope_id)
    except AgentError as e:
        _raise(e)


@router.get("/api/project/{project_name}/agents/sessions/{sid}")
async def get_session(project_name: str, sid: str):
    s = get_agent_runtime().get_session(project_name, sid)
    if s is None:
        raise HTTPException(404, "session 不存在")
    return s


@router.post("/api/project/{project_name}/agents/sessions/{sid}/messages")
async def send_message(project_name: str, sid: str, req: SendMessageReq):
    """聊天。chat 未配置 Agnes -> 200 + assistant(status=not_configured)（§修正3，不转 503）。"""
    try:
        return await get_agent_runtime().send_message_to_session(project_name, sid, req.content)
    except AgentError as e:
        _raise(e)


# ==================== Tasks（Producer delegation）====================
@router.get("/api/project/{project_name}/agents/tasks")
async def list_tasks(project_name: str,
                     requester_agent: Optional[str] = None,
                     target_agent: Optional[str] = None,
                     status: Optional[str] = None):
    try:
        return {"tasks": get_agent_runtime().list_tasks(
            project_name, requester_agent, target_agent, status)}
    except AgentError as e:
        _raise(e)


@router.post("/api/project/{project_name}/agents/tasks")
async def create_task(project_name: str, req: DelegateReq):
    """Producer delegation 走正式 AgentTask（可追踪，§27）。
    inspect 同步执行；production_plan.create 走 async 执行分支（§11，await 真实 Planner workflow）。"""
    try:
        return await get_agent_runtime().delegate(
            project_name, req.requester_agent, req.target_agent,
            req.scope_type, req.scope_id, req.instruction,
            req.capability_id, req.operation)
    except AgentError as e:
        _raise(e)


@router.get("/api/project/{project_name}/agents/tasks/{tid}")
async def get_task(project_name: str, tid: str):
    t = get_agent_runtime().get_task(project_name, tid)
    if t is None:
        raise HTTPException(404, "task 不存在")
    return t


# ==================== AI-7A Project Intelligence（只读语义检视，§6/§75）====================
@router.get("/api/project/{project_name}/project-intelligence")
async def project_intelligence(project_name: str,
                               view: str,
                               entity_id: Optional[str] = None,
                               scene_id: Optional[str] = None,
                               shot_id: Optional[str] = None,
                               character_id: Optional[str] = None,
                               include_history: bool = False,
                               limit: int = 50,
                               offset: int = 0):
    """只读项目智能检视（轮询友好）。view 决定语义视图；返回有界 ProjectInspectResult。
    纯读——不写 canonical、不建 Workspace/ChangeSet、不触发生产（§1/§43）。"""
    try:
        return get_agent_runtime().project_intelligence(project_name).inspect(
            view, entity_id=entity_id, scene_id=scene_id, shot_id=shot_id,
            character_id=character_id, include_history=include_history,
            limit=limit, offset=offset).to_dict()
    except AgentError as e:
        _raise(e)


# ==================== AI-7A Team Group Chat（项目级团队群聊，§22/§24/§75/§76）====================
class TeamSendReq(BaseModel):
    content: str


@router.get("/api/project/{project_name}/team-chat/thread")
async def team_chat_thread(project_name: str):
    """取（幂等创建）项目 team thread + 全部消息。轮询用（§56 刷新不造 dup）。"""
    rt = get_agent_runtime()
    try:
        store = TeamChatStore(rt._resolve_project_dir(project_name), project_name)
    except AgentError as e:
        _raise(e)
    return store.get_or_create_thread()


@router.post("/api/project/{project_name}/team-chat/messages")
async def team_chat_send(project_name: str, req: TeamSendReq):
    """发用户消息 → 持久化（pending_inquiry=True）→ 后台跑 run_inquiry，HTTP 立即返回。
    结果落盘后前端轮询 GET messages 见 producer 回复（§76 不长挂）。"""
    rt = get_agent_runtime()
    try:
        pd = rt._resolve_project_dir(project_name)
    except AgentError as e:
        _raise(e)
    store = TeamChatStore(pd, project_name)
    store.get_or_create_thread()
    user_msg = store.append_message(
        TeamMessage(message_type="user", text=req.content, pending_inquiry=True))
    asyncio.create_task(run_team_inquiry_and_persist(
        rt, project_name, user_msg.message_id, req.content))
    return {"message": user_msg.to_dict(), "status": "processing"}


@router.get("/api/project/{project_name}/team-chat/messages")
async def team_chat_messages(project_name: str):
    """轮询：取最新消息（persistence 即真相，§77）。"""
    rt = get_agent_runtime()
    try:
        store = TeamChatStore(rt._resolve_project_dir(project_name), project_name)
    except AgentError as e:
        _raise(e)
    return {"messages": store.list_messages()}


# ==================== AI-7A Change Analysis Session（§31/§37/§42/§49/§55，analysis-only）====================
class CreateChangeAnalysisReq(BaseModel):
    request: str
    source: str = "team_chat"
    source_agent_id: Optional[str] = None
    source_session_id: Optional[str] = None


@router.post("/api/project/{project_name}/change-analysis")
async def create_change_analysis(project_name: str, req: CreateChangeAnalysisReq):
    """创建变更分析会话（analyzing）+ 后台推进（§76 不长挂）。execution_ready 恒 False。"""
    rt = get_agent_runtime()
    try:
        rt._resolve_project_dir(project_name)
    except AgentError as e:
        _raise(e)
    return create_change_analysis_session(
        rt, project_name, req.request, source=req.source,
        source_agent_id=req.source_agent_id, source_session_id=req.source_session_id)


@router.get("/api/project/{project_name}/change-analysis")
async def list_change_analysis(project_name: str):
    rt = get_agent_runtime()
    try:
        pd = rt._resolve_project_dir(project_name)
    except AgentError as e:
        _raise(e)
    return {"sessions": ChangeAnalysisStore(pd, project_name).list_sessions()}


@router.get("/api/project/{project_name}/change-analysis/{session_id}")
async def get_change_analysis(project_name: str, session_id: str):
    rt = get_agent_runtime()
    try:
        pd = rt._resolve_project_dir(project_name)
    except AgentError as e:
        _raise(e)
    s = ChangeAnalysisStore(pd, project_name).get_session(session_id)
    if s is None:
        raise HTTPException(404, "会话不存在")
    return s


@router.post("/api/project/{project_name}/change-analysis/{session_id}/resume")
async def resume_change_analysis(project_name: str, session_id: str):
    """显式恢复（§55/§57：不自动重跑，用户触发）。幂等推进：保留 completed，重跑 pending。"""
    rt = get_agent_runtime()
    try:
        return await continue_change_analysis(rt, project_name, session_id)
    except AgentError as e:
        _raise(e)


# ==================== Director Proposals（AI-6D storyboard.propose_change）====================
class ProposeReq(BaseModel):
    agent_id: str = "director"
    scope_type: str           # shot | scene
    scope_id: str
    instruction: str = ""


class PreviewSelectedReq(BaseModel):
    selected_item_ids: List[str] = []


class ApplyProposalReq(BaseModel):
    changeset_id: str


def _proposal_workspace_id(project_name: str, proposal_id: str) -> str:
    """取 proposal 的 workspace_id（用于加锁）。proposal 不存在 -> 404。"""
    p = get_agent_runtime().get_director_proposal(project_name, proposal_id)
    if p is None:
        raise HTTPException(404, "proposal 不存在")
    wid = p.get("workspace_id")
    if not wid:
        raise HTTPException(409, "proposal 无关联 workspace")
    return wid


@router.post("/api/project/{project_name}/agents/director/proposals")
async def create_director_proposal(project_name: str, req: ProposeReq):
    """生成 Director 修改提案（§11：ctx→Agnes→parse+validate→create workspace→compile→AI-6A preview→persist）。"""
    try:
        return await get_agent_runtime().propose_storyboard_change(
            project_name, req.agent_id, req.scope_type, req.scope_id, req.instruction)
    except AgentError as e:
        _raise(e)


@router.get("/api/project/{project_name}/agents/director/proposals/{proposal_id}")
async def get_director_proposal(project_name: str, proposal_id: str):
    """刷新恢复（§16）：重建 currentProposal（含存储的 preview + base_is_stale）。"""
    p = get_agent_runtime().get_director_proposal(project_name, proposal_id)
    if p is None:
        raise HTTPException(404, "proposal 不存在")
    return p


@router.get("/api/project/{project_name}/agents/director/proposals")
async def list_director_proposals(project_name: str, scope_type: str, scope_id: str):
    return {"proposals": get_agent_runtime().list_director_proposals(
        project_name, scope_type, scope_id)}


@router.post("/api/project/{project_name}/agents/director/proposals/{proposal_id}/preview-selected")
async def preview_selected_proposal(project_name: str, proposal_id: str,
                                     req: PreviewSelectedReq):
    """选择性子集再预览（§13/§14）：重编译被选 item → AI-6A 全新 preview。复用同一 workspace 锁。"""
    wid = _proposal_workspace_id(project_name, proposal_id)
    try:
        async with workspace_lock(project_name, wid):
            return await get_agent_runtime().preview_selected_subset(
                project_name, proposal_id, req.selected_item_ids)
    except AgentError as e:
        _raise(e)


@router.post("/api/project/{project_name}/agents/director/proposals/{proposal_id}/apply")
async def apply_director_proposal(project_name: str, proposal_id: str,
                                   req: ApplyProposalReq):
    """应用选中 ChangeSet（§13：按 changeset_id，复用 cs_engine.apply）。复用同一 workspace 锁。"""
    wid = _proposal_workspace_id(project_name, proposal_id)
    try:
        async with workspace_lock(project_name, wid):
            return await get_agent_runtime().apply_director_proposal(
                project_name, proposal_id, req.changeset_id)
    except AgentError as e:
        _raise(e)


@router.post("/api/project/{project_name}/agents/director/proposals/{proposal_id}/dismiss")
async def dismiss_director_proposal(project_name: str, proposal_id: str):
    try:
        return get_agent_runtime().dismiss_director_proposal(project_name, proposal_id)
    except AgentError as e:
        _raise(e)


# ==================== AI-7B Change Sessions（proposal-only，§92）====================
# 路由风格对齐 AI-7A change-analysis（kebab-case，/api/project/{p}/change-sessions）。
# 硬边界（§2/§76/§119/L）：本组端点绝不 apply、不写 canonical/Workspace/Production、
# 不触发生成。批准 = 持久化确认 + 置 approved_for_execution → STOP。
class CreateChangeSessionReq(BaseModel):
    request: str
    source: str = "team_chat"
    source_agent_id: Optional[str] = None
    source_session_id: Optional[str] = None
    source_message_id: Optional[str] = None


class ApprovePlanReq(BaseModel):
    plan_id: str
    plan_version: int


class DecisionReq(BaseModel):
    decision_id: str
    option_id: str


class PlanFeedbackReq(BaseModel):
    feedback: str
    plan_id: Optional[str] = None


class RejectPlanReq(BaseModel):
    plan_id: Optional[str] = None


@router.post("/api/project/{project_name}/change-sessions")
async def create_change_session(project_name: str, req: CreateChangeSessionReq):
    """创建 ChangeRequest（proposal_only）+ ChangeSession（routing）+ 捕获 source 签名。
    7B-1：不后台推进（推进机 7B-2..4 接通）。"""
    rt = get_agent_runtime()
    try:
        rt._resolve_project_dir(project_name)   # 校验项目存在
    except AgentError as e:
        _raise(e)
    return cp_service.create_session(
        rt, project_name, req.request, source=req.source,
        source_agent_id=req.source_agent_id, source_session_id=req.source_session_id,
        source_message_id=req.source_message_id)


@router.get("/api/project/{project_name}/change-sessions")
async def list_change_sessions(project_name: str):
    rt = get_agent_runtime()
    try:
        return cp_service.list_sessions(rt, project_name)
    except AgentError as e:
        _raise(e)


@router.get("/api/project/{project_name}/change-sessions/{session_id}")
async def get_change_session(project_name: str, session_id: str):
    rt = get_agent_runtime()
    try:
        s = cp_service.get_session(rt, project_name, session_id)
    except AgentError as e:
        _raise(e)
    if s is None:
        raise HTTPException(404, "change-session 不存在")
    return s


@router.post("/api/project/{project_name}/change-sessions/{session_id}/resume")
async def resume_change_session(project_name: str, session_id: str):
    """幂等推进（§87）。终端态直接返回；stale→stale；legacy→拒。7B-2：跑首轮提案。"""
    rt = get_agent_runtime()
    try:
        async with session_lock(project_name, session_id):
            return await cp_service.resume_session(rt, project_name, session_id)
    except AgentError as e:
        _raise(e)


@router.get("/api/project/{project_name}/change-sessions/{session_id}/plan")
async def get_change_session_plan(project_name: str, session_id: str):
    rt = get_agent_runtime()
    try:
        plan = cp_service.get_plan(rt, project_name, session_id)
    except AgentError as e:
        _raise(e)
    if plan is None:
        raise HTTPException(404, "该 change-session 暂无 plan")
    return plan


@router.get("/api/project/{project_name}/change-sessions/{session_id}/preview")
async def get_change_session_preview(project_name: str, session_id: str):
    rt = get_agent_runtime()
    try:
        pv = cp_service.get_preview(rt, project_name, session_id)
    except AgentError as e:
        _raise(e)
    if pv is None:
        raise HTTPException(404, "该 change-session 暂无 preview")
    return pv


@router.post("/api/project/{project_name}/change-sessions/{session_id}/decision")
async def submit_change_decision(project_name: str, session_id: str, req: DecisionReq):
    """回答决策（§93）。验属该 session + pending + option 合法 + 非 stale → answered。"""
    rt = get_agent_runtime()
    try:
        async with session_lock(project_name, session_id):
            return cp_service.submit_decision(
                rt, project_name, session_id, req.decision_id, req.option_id)
    except AgentError as e:
        _raise(e)


@router.post("/api/project/{project_name}/change-sessions/{session_id}/plan-feedback")
async def change_session_feedback(project_name: str, session_id: str, req: PlanFeedbackReq):
    """用户请求修改 → plan version+1（§78）。旧 plan=superseded；新 plan=draft。
    受影响咨询重跑在 7B-2..4；7B-1 仅版本骨架。"""
    rt = get_agent_runtime()
    try:
        async with session_lock(project_name, session_id):
            return cp_service.request_changes(
                rt, project_name, session_id, req.feedback, req.plan_id)
    except AgentError as e:
        _raise(e)


@router.post("/api/project/{project_name}/change-sessions/{session_id}/approve")
async def approve_change_session(project_name: str, session_id: str, req: ApprovePlanReq):
    """批准计划（§76/§94）。只持久化确认 + 置 approved_for_execution → STOP。
    绝不 apply / 不写 canonical·Workspace·Production / 不触发生成（§119/L 负测目标）。"""
    rt = get_agent_runtime()
    try:
        async with session_lock(project_name, session_id):
            return cp_service.approve_plan(
                rt, project_name, session_id, req.plan_id, req.plan_version)
    except AgentError as e:
        _raise(e)


@router.post("/api/project/{project_name}/change-sessions/{session_id}/reject")
async def reject_change_session(project_name: str, session_id: str, req: RejectPlanReq):
    """拒绝计划（§80）。session=rejected；历史 artifact 保留。0 mutation。"""
    rt = get_agent_runtime()
    try:
        async with session_lock(project_name, session_id):
            return cp_service.reject_plan(rt, project_name, session_id, req.plan_id)
    except AgentError as e:
        _raise(e)


# ==================== AI-7C Execution（approved-plan execution，§15/§83/§96）====================
# execute ≠ apply（§4）：不翻 applyable 后门；用独立 ChangeExecutionSession + execution_eligibility。
# AI-7C-1 范围：创建 + revalidation + 幂等 + 持久化 + stale 阻断。0 Workspace Apply / 0 canonical
# mutation / 0 Production Plan / 0 Agnes。execute 不自动跑 workspace/commit/production（后续 gate）。
class ExecutePlanReq(BaseModel):
    plan_id: str
    plan_version: int
    production_mode: str = "affected_production"   # creative_only | affected_production（§15）


@router.post("/api/project/{project_name}/change-sessions/{session_id}/execute")
async def execute_change_plan(project_name: str, session_id: str, req: ExecutePlanReq):
    """启动已批准 plan 的执行（§15）。幂等：同一 (plan_id, plan_version) -> 同一 execution。
    AI-7C-1 create+revalidation -> AI-7C-2 workspace replay + 等价校验 -> AI-7C-3 原子 canonical
    creative commit -> done（creative_only）/character_sync（affected_production）。
    revalidation 失败/stale/preview 不符/活跃生产 -> blocked；canonical 至多提交一次（§16/§34）。"""
    rt = get_agent_runtime()
    try:
        async with session_lock(project_name, session_id):
            return await cp_exec.start_replay_commit(
                rt, project_name, session_id, req.plan_id, req.plan_version,
                req.production_mode)
    except AgentError as e:
        _raise(e)


@router.get("/api/project/{project_name}/change-sessions/{session_id}/executions")
async def list_change_executions(project_name: str, session_id: str):
    """列出该 change-session 的 executions（§83）。polling-first 真相源。"""
    rt = get_agent_runtime()
    try:
        return cp_exec.list_executions(rt, project_name, session_id)
    except AgentError as e:
        _raise(e)


@router.get("/api/project/{project_name}/change-sessions/{session_id}/executions/{execution_id}")
async def get_change_execution(project_name: str, session_id: str, execution_id: str):
    """取单个 execution（§83）。refresh 恢复用：前端据此 + production_run_id 恢复进度。"""
    rt = get_agent_runtime()
    try:
        ex = cp_exec.get_execution(rt, project_name, session_id, execution_id)
    except AgentError as e:
        _raise(e)
    if ex is None:
        raise HTTPException(404, "execution 不存在")
    return ex


@router.get("/api/project/{project_name}/change-sessions/{session_id}/executions/{execution_id}/manifest")
async def get_execution_manifest(project_name: str, session_id: str, execution_id: str):
    """§40：execution 的 production manifest（deterministic asset actions）。UI 据此展示
    受影响 char/scene/shot + regen 动作（creative_only 亦落盘以显示后果 §66）。"""
    rt = get_agent_runtime()
    try:
        m = cp_exec.get_production_manifest(rt, project_name, execution_id)
    except AgentError as e:
        _raise(e)
    if m is None:
        raise HTTPException(404, "manifest 不存在")
    return m


@router.post("/api/project/{project_name}/change-sessions/{session_id}/executions/{execution_id}/resume")
async def resume_change_execution(project_name: str, session_id: str, execution_id: str):
    """§83 resume：从当前 stage 推进（幂等；blocked 不可 rebasing resume §81）。"""
    rt = get_agent_runtime()
    try:
        async with session_lock(project_name, session_id):
            return await cp_exec.resume_execution(rt, project_name, execution_id)
    except AgentError as e:
        _raise(e)


@router.post("/api/project/{project_name}/change-sessions/{session_id}/executions/{execution_id}/cancel")
async def cancel_change_execution(project_name: str, session_id: str, execution_id: str):
    """§38 cancel：提交前 -> cancelled（canonical 不动）；提交后 -> creative 保留 + 停后续 production。"""
    rt = get_agent_runtime()
    try:
        async with session_lock(project_name, session_id):
            return cp_exec.cancel_execution(rt, project_name, execution_id)
    except AgentError as e:
        _raise(e)


@router.get("/api/project/{project_name}/change-sessions/{session_id}/plans/{plan_version}/eligibility")
async def get_execution_eligibility(project_name: str, session_id: str,
                                    plan_version: int, plan_id: str):
    """§4：execution eligibility（eligible/already_started/already_completed/stale/blocked/
    invalid_approval）。只读，供 UI 决定 Execute 按钮态。不翻 applyable 后门。"""
    rt = get_agent_runtime()
    try:
        return cp_exec.execution_eligibility(rt, project_name, session_id,
                                             plan_id, plan_version)
    except AgentError as e:
        _raise(e)
