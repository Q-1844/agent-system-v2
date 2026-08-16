"""AI-6B Multi-Agent Foundation 路由（§27）。

composition root：本文件是唯一 import routers.production_planner 的地方，把 production-plan
读取包装成 callable 注入 AgentRuntime（§修正2：src/agents/* 绝不 import router）。
  routers / composition root  →  注入 plans_reader callable
  AgentRuntime / CapabilityContext  →  透传
  production_plan.inspect adapter  →  调用（不 import router）

错误映射（§29）：chat 未配置 Agnes 不是 HTTP 错误（send_message 软失败返回 status=not_configured，
§修正3）；HTTP 错误仅用于请求/配置校验类。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import deps
from deps import get_project_manager
from src.agents.runtime import get_agent_runtime, AgentError
from routers.production_planner import _read_plans   # 唯一 import 点（composition root）

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


# ============ AgentError.code -> HTTP 状态（§29）============
_HTTP = {
    "unknown_agent": 404, "unknown_capability": 404,
    "unknown_project": 404, "unknown_scope": 404,
    "disabled_agent": 400, "permission_denied": 403,
    "unknown_model_profile": 400, "invalid_model_config": 400,
    "task_failed": 500,
}


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
    """Producer delegation 走正式 AgentTask（可追踪，§27）。同步执行 inspect capability。"""
    try:
        return get_agent_runtime().delegate(
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
