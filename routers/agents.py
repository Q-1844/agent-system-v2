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
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import deps
from deps import get_project_manager, JOB_HANDLERS
from src.agents.runtime import get_agent_runtime, AgentError
from src.agents.project_events import get_project_bus
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
# AI-7D D3：session_lock 搬到 src/agents/change_protocol/locks.py（中性模块，router + agents
# 运行时均可 import；agents 包纪律禁止 import router，故 runtime.py 不能从本文件取锁）。
from src.agents.change_protocol.locks import session_lock


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
    # Phase 46（M02）：非流式 turn 也支持显式开启思考（工作流助手 Producer 解析传 true；
    # 缺省 False 保持旧行为）。
    thinking: bool = False


class StreamTurnReq(BaseModel):
    content: str
    thinking: bool = False


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
    """聊天（deprecated wrapper，AI-7D D1：内部走 unified_turn 单一 runtime）。返回 assistant
    AgentMessage。chat 未配置 Agnes -> 200 + assistant(status=not_configured)（§修正3，不转 503）。"""
    try:
        return await get_agent_runtime().send_message_to_session(project_name, sid, req.content)
    except AgentError as e:
        _raise(e)


@router.post("/api/project/{project_name}/agents/sessions/{sid}/turn")
async def agent_turn(project_name: str, sid: str, req: SendMessageReq):
    """AI-7D D1 · 统一 Direct Chat turn。返回 AgentTurnResult（intent + assistant_message +
    events + evidence_refs）。assistant_message 是用户唯一所见自然语言；events 是真后台事件
    （非模型叙述）。conversation/project_inquiry/runtime_query 三意图经既有真实运行时；
    creative_change/runtime_control 列入 TurnIntent 枚举但 D2/D3 实现。"""
    try:
        return await get_agent_runtime().unified_turn(project_name, sid, req.content,
                                                      thinking=req.thinking)
    except AgentError as e:
        _raise(e)


# AI-7D-R：活跃流式 turn 注册表（进程内）。key=f"{project}::{sid}" → cancel_event。
# 用途：①同一会话 turn 串行互斥（并发请求 409）；②后台续跑时的停止端点置位 cancel_event。
# turn 任务在客户端断开后仍继续跑完（消息持久化、资源事件走总线），故这里必须独立于连接。
# AI-Producer-V1：活跃 turn 注册表抽取为中性的 src/agents/turn_registry（router 与
# Producer Bridge 共用同一进程内 dict，防止用户直开 Specialist 聊天与 Bridge 委派
# 并发驱动同一 session）。_ACTIVE_TURN_EVENTS / _turn_key 保留为别名（同一 dict 对象 /
# 同一函数），既有用法与既有测试（tests/test_ai_7d_r_turn_background.py 直接操作该 dict
# 与 _turn_key）零改动——行为逐字节等价，仅挪位置 + 改 import。
from src.agents import turn_registry
_ACTIVE_TURN_EVENTS = turn_registry._ACTIVE
_turn_key = turn_registry.key


def _put_terminal_event(q: asyncio.Queue, ev) -> None:
    """终端事件（turn.completed/turn.failed/SENTINEL）入队：队列满则先清空
    unconsumed 事件再入队，保证消费者必收终端信号。否则 put_nowait 遇 QueueFull
    静默丢失 → 消费者永挂在 q.get()、看门狗误触发、driver 漏收 turn.completed。
    终端时刻丢弃未消费的细粒度事件（progress/assistant.delta）安全——消费者只要
    终端信号。与 _cb 的 overflow drop 同语义但保证不丢终端。模块级以便单测。"""
    try:
        q.put_nowait(ev)
    except asyncio.QueueFull:
        try:
            while True:
                q.get_nowait()
        except Exception:
            pass
        try:
            q.put_nowait(ev)
        except Exception:
            pass


@router.post("/api/project/{project_name}/agents/sessions/{sid}/turn/cancel")
async def cancel_agent_turn(project_name: str, sid: str):
    """协作式停止进行中的流式 turn：置位 cancel_event，agent 在步骤边界安全停下
    （已完成的写保留，未完成不执行），turn 按 STOPPED 诚实收尾并发 resource.unlocked。"""
    ev = _ACTIVE_TURN_EVENTS.get(_turn_key(project_name, sid))
    if ev is None:
        return {"stopped": False, "message": "没有正在执行的任务"}
    ev.set()
    return {"stopped": True}


@router.post("/api/project/{project_name}/agents/sessions/{sid}/turn/stream")
async def agent_turn_stream(project_name: str, sid: str, req: StreamTurnReq):
    """AI-7D-R · Agent 流式 Direct Chat turn（/turn 不动，新接口）。

    2026-08-30 起支持三个具备直接编辑工具的 Agent（各自 tool-loop，互不改写对方写入域）：
      script_writer        → 写 story_text/scenes，资源锁 story，变更事件 story.changed
      character_designer   → 写 characters，资源锁 character，变更事件 character.changed
      director             → 写 scenes[].shots（分镜域），资源锁 storyboard，变更事件 storyboard.changed

    发送瞬间把 req.thinking snapshot 固定为本 turn 配置。逐 AgentStreamEvent SSE forward：
      turn.started → reasoning.* / tool.* / assistant.delta → turn.completed(完整结果) /
      turn.failed。持久化语义不变（一条 user + 一条 assistant+meta）。

    AI-7D-R 后台续跑：客户端断开（切走/关页/刷新）【不取消】turn 任务——agent 继续在
    后台跑完，消息照常持久化。细粒度流式事件仅在有观看连接时入队（断开后不堆积）；
    资源事件（resource.locked/*.changed/resource.unlocked）始终发项目总线，让任何
    打开该项目的页面（如剧本/角色模块）实时看到锁与内容变化。"""
    rt = get_agent_runtime()
    store = rt._session_store(project_name)
    session = store.load(sid)
    if session is None:
        _raise(AgentError("unknown_scope", f"session 不存在: {sid}"))
    # 每个 Agent 的流式配置：turn 函数（lazy import）+ 写工具集合 + 锁资源 + 变更事件发射器
    if session.agent_id == "script_writer":
        from src.agents.script_writer import script_writer_turn, WRITE_OPERATIONS
        from src.agents.project_events import RESOURCE_STORY, emit_changed
        _turn_fn, _writes, _resource, _emit = (
            script_writer_turn, WRITE_OPERATIONS, RESOURCE_STORY, emit_changed)
        _write_domain_map = None   # 单写域 Agent：走 _writes/_resource 旧路径
    elif session.agent_id == "character_designer":
        from src.agents.character_designer import (character_designer_turn,
                                                   WRITE_TOOL_DOMAIN)
        from src.agents.project_events import emit_changed
        # Phase B：Resource Agent 双写域（character + resources）——锁按工具查
        # WRITE_TOOL_DOMAIN 逐域挂（_writes 仍给旧单域路径兼容；_resource 不再用于该分支）。
        _turn_fn, _writes, _resource, _emit = (
            character_designer_turn, frozenset(WRITE_TOOL_DOMAIN), None, emit_changed)
        _write_domain_map = WRITE_TOOL_DOMAIN
    elif session.agent_id == "scene_designer":
        # Agent System V2 Phase C2（28.md §5）：Scene Agent 单写域 scene_system——
        # 锁按工具查 WRITE_TOOL_DOMAIN 挂（当前工具面随 Commit D-G 逐个入表）。
        from src.agents.scene_designer import scene_designer_turn, WRITE_TOOL_DOMAIN
        from src.agents.project_events import emit_changed
        _turn_fn, _writes, _resource, _emit = (
            scene_designer_turn, frozenset(WRITE_TOOL_DOMAIN), None, emit_changed)
        _write_domain_map = WRITE_TOOL_DOMAIN
    elif session.agent_id == "director":
        from src.agents.director import director_turn, DIRECTOR_WRITE_OPERATIONS
        from src.agents.project_events import RESOURCE_STORYBOARD, emit_changed
        _turn_fn, _writes, _resource, _emit = (
            director_turn, DIRECTOR_WRITE_OPERATIONS, RESOURCE_STORYBOARD, emit_changed)
        _write_domain_map = None
    elif session.agent_id == "assembly":
        # Phase 44B：Assembly Agent 单写域 sequence——锁按工具查 WRITE_TOOL_DOMAIN 挂。
        from src.agents.assembly import assembly_turn, WRITE_TOOL_DOMAIN
        from src.agents.project_events import emit_changed
        _turn_fn, _writes, _resource, _emit = (
            assembly_turn, frozenset(WRITE_TOOL_DOMAIN), None, emit_changed)
        _write_domain_map = WRITE_TOOL_DOMAIN
    elif session.agent_id == "producer":
        # Producer V1 Phase 1：流式端点支持 Producer tool-loop。Producer 自身不锁任何资源
        # （_writes 空集）——锁由 Bridge 内 Specialist 接线（_cb 复制 §5.3-⑤b 语义）负责；
        # 若传 delegate 名会双重锁/错域。_emit 不用（同因）。互斥 409 / cancel / SSE / 后台
        # 续跑复用既有机制。Producer 的 _cb 仅转发事件到 SSE（含 Specialist 经 Bridge 转发
        # 的 tool 时间线）；锁/emit 在 Bridge _cb 里按 Specialist 的 writes/resource 接。
        from src.agents.producer import producer_turn
        _turn_fn, _writes, _resource, _emit = (
            producer_turn, set(), None, lambda *a, **k: None)
        _write_domain_map = None
    else:
        _raise(AgentError("permission_denied",
                          "流式 turn 仅支持 Script Writer / Character Designer / Scene Agent / "
                          "Director / Assembly Agent / Producer 会话"))
    from src.agents.models import ScopeType
    from src.agents.project_events import get_project_bus
    if session.scope_type != ScopeType.PROJECT.value:
        _raise(AgentError("unsupported_scope", "流式 turn 仅支持 project scope"))
    agent_id = session.agent_id

    # 同会话互斥：上一个 turn 还在跑（含后台续跑）→ 409，不并发改同一项目
    key = _turn_key(project_name, sid)
    if key in _ACTIVE_TURN_EVENTS:
        raise HTTPException(status_code=409,
                            detail="该会话已有任务正在执行，请等待完成或先停止")
    cancel_event = asyncio.Event()
    _ACTIVE_TURN_EVENTS[key] = cancel_event

    thinking_fixed = bool(req.thinking)   # snapshot：本 turn 全程不变
    bus = get_project_bus()

    async def event_gen():
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        SENTINEL = object()
        state = {"connected": True, "locked": False, "locked_doms": set()}

        def _lock_emit(dom: str, start: bool, ok: bool, op: str) -> None:
            # Phase B：按域锁/发事件（character_designer 双写域；其它 Agent 单域旧路径）。
            if start and dom not in state["locked_doms"]:
                bus.lock(project_name, dom, by=agent_id, session_id=sid)
                state["locked_doms"].add(dom)
                state["locked"] = True
            elif not start and ok:
                _emit(bus, project_name, dom, by=agent_id, session_id=sid, operation=op)

        def _cb(ev):
            # 同 loop 同线程同步操作（agent turn 与 consumer 共享同一事件循环）。
            t = ev.get("type")
            name = ev.get("name") or ""
            # 资源事件 → 项目总线（始终发，后台跑也要让其它页面看到锁/变更）
            if _write_domain_map is not None:
                dom = _write_domain_map.get(name)
                if dom is not None:
                    if t == "tool.started":
                        _lock_emit(dom, True, False, name)
                    elif t == "tool.completed":
                        _lock_emit(dom, False, bool(ev.get("ok")), name)
            elif t == "tool.started" and name in _writes and not state["locked"]:
                bus.lock(project_name, _resource, by=agent_id, session_id=sid)
                state["locked"] = True
            elif t == "tool.completed" and name in _writes and ev.get("ok"):
                _emit(bus, project_name, _resource, by=agent_id,
                      session_id=sid, operation=name)
            # 细粒度流式事件 → 仅在有观看连接时入队（断开后后台跑不堆积）
            if state["connected"]:
                try:
                    q.put_nowait(ev)
                except asyncio.QueueFull:
                    try:
                        q.get_nowait()
                        q.put_nowait(ev)
                    except Exception:
                        pass

        def _put_terminal(ev):
            _put_terminal_event(q, ev)

        async def _run():
            try:
                result = await _turn_fn(
                    rt, project_name, sid, req.content,
                    thinking=thinking_fixed, event_cb=_cb, cancel_event=cancel_event)
                if state["connected"]:
                    _put_terminal({"type": "turn.completed", "result": result})
            except AgentError as e:
                if state["connected"]:
                    _put_terminal({"type": "turn.failed", "error": e.message})
            except Exception as e:
                if state["connected"]:
                    _put_terminal({"type": "turn.failed", "error": str(e)[:300]})
            finally:
                # 资源锁释放（成功/失败/停止/断开都要释放；Phase B 按域逐个释放）。
                # 🔴 用 _resource（当前 Agent 的写入域资源），不能用任何分支里的局部 import 名
                #（character_designer 分支没有 RESOURCE_STORY 绑定，闭包会 NameError →
                # 锁泄漏 + SSE 流不收尾）。
                if state["locked"]:
                    if state["locked_doms"]:
                        for _dom in list(state["locked_doms"]):
                            bus.unlock(project_name, _dom)
                        state["locked_doms"].clear()
                    else:
                        bus.unlock(project_name, _resource)
                    state["locked"] = False
                _ACTIVE_TURN_EVENTS.pop(key, None)
                if state["connected"]:
                    _put_terminal(SENTINEL)

        task = asyncio.ensure_future(_run())
        # turn.started（thinking snapshot 在这里固定宣告）
        yield "data: " + json.dumps({"type": "turn.started", "thinking": thinking_fixed},
                                    ensure_ascii=False) + "\n\n"
        try:
            while True:
                ev = await q.get()
                if ev is SENTINEL:
                    break
                yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
        finally:
            # 客户端断开：标记不再观看，但【不取消】task——turn 在后台继续跑完，
            # 资源事件继续走总线，消息照常持久化。task 由事件循环持有直到完成。
            state["connected"] = False
            if task.done():
                return

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.get("/api/project/{project_name}/events")
async def project_events(request: Request, project_name: str):
    """AI-7D-R · 项目级实时事件 SSE（EventSource 友好，GET）。

    一条连接推送该项目的资源锁与内容变更事件，供任意页面订阅（Agent 后台跑任务时
    用户切到剧本/其它模块也能实时看到）：
      连接首帧 snapshot {type:"snapshot", locks:[...]} —— 当前已锁资源 replay
        （打开页面时 turn 可能已在跑）；随后逐帧 resource.locked / resource.unlocked
        / story.changed。15s 心跳 `: keepalive` 防代理超时断流。
    Agent 写入路径只 publish 到总线、绝不等待前端，前端断开不影响后台任务。"""
    bus = get_project_bus()
    sub = bus.subscribe(project_name)

    async def gen():
        try:
            snap = {"type": "snapshot", "locks": bus.snapshot(project_name)}
            yield "data: " + json.dumps(snap, ensure_ascii=False) + "\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(sub.get(), timeout=15)
                    yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"   # 注释帧：EventSource 自动忽略
        finally:
            bus.unsubscribe(project_name, sub)

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/project/{project_name}/resource-locks")
async def project_resource_locks(project_name: str):
    """AI-7D-R · 资源锁【权威状态】只读查询（SSE 对账兜底）。

    SSE 是一次一事件的推送通道，个别 resource.unlocked 事件可能因连接静默中断/
    后端重启/半开连接而丢失，导致前端卡在旧的锁定态。前端在锁定期定时（+回前台）
    拉本接口对账：返回的是后端总线里【此刻真实持有】的锁（与 SSE 首帧 snapshot 同源），
    以此为准——空 locks 即解锁，不依赖那条可能已丢的 unlock 事件。"""
    bus = get_project_bus()
    return {"locks": bus.snapshot(project_name)}


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


# ==================== Producer V1 Phase 3 · Production Room 投影 ====================
@router.get("/api/project/{project_name}/agents/producer/room")
async def production_room(project_name: str):
    """Production Room 群聊投影（只读 GET）。

    把已有持久事实——Producer session 消息 + bridge_messages.jsonl + AgentTask
    （.delegate）+ workflows.json——投影成群聊 UI 需要的稳定 DTO：
      - 消息稳定 ID（session message_id / task:{task_id}:dispatch|result），
        snapshot 与 SSE live 用同一 ID 去重，不依赖内容相等；
      - 确定性顺序（持久化时间戳 + kind 次序 + id），刷新 N 次顺序一致；
      - 状态只来自真实事实（AgentTask/turn_registry/session status），不伪造。
    本端点是纯派生 View：零写入、无缓存、不成为第二套 authoritative state。"""
    from src.agents.production_room import build_room_projection
    rt = get_agent_runtime()
    try:
        return build_room_projection(rt, project_name)
    except AgentError as e:
        _raise(e)


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
    # AI-7C-H H3：调用方页面上下文（选中 Scene/Shot/Character）——持久化进 entity_refs，
    # 分类/提案路由自动合并，Agent 不反问 Which Scene/Character（§9）。
    context_scope: Optional[dict] = None
    # AI-7D D2.5：客户端幂等键（Direct Chat 双击/retry/F5 不造重复 ChangeSession）。
    client_request_id: Optional[str] = None


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
        source_message_id=req.source_message_id,
        context_scope=req.context_scope,
        client_request_id=req.client_request_id)


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
    """取单个 execution（§83）。refresh 恢复用：前端据此 + production_run_id 恢复进度。
    AI-7C-H §八：读取路径惰性收敛取消——cancel_requested 且已无在飞工作/活跃 owned run
    时收敛为 cancelled（前端轮询此端点，驱动「正在停止…」→「已取消」推进）。"""
    rt = get_agent_runtime()
    try:
        ex = cp_exec.get_execution(rt, project_name, session_id, execution_id)
    except AgentError as e:
        _raise(e)
    if ex is None:
        raise HTTPException(404, "execution 不存在")
    if ex.get("cancel_requested") and ex.get("status") not in ("cancelled", "completed", "failed"):
        await cp_exec.maybe_converge_cancellation(rt, project_name, execution_id)
        ex = cp_exec.get_execution(rt, project_name, session_id, execution_id) or ex
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
    """§38 cancel：提交前 -> cancelled（canonical 不动）；提交后 -> creative 保留 + 停后续
    production（AI-7C-H：取消传播到 owned ProductionRun，running+cancel_requested=「正在停止…」，
    由 watcher/读取惰性收敛到 cancelled）。"""
    rt = get_agent_runtime()
    try:
        async with session_lock(project_name, session_id):
            return await cp_exec.cancel_execution(rt, project_name, execution_id)
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


# ==================== Script Writer 测试弹窗 ====================
# 2026-08-30：改走【真实链路】script_writer.script_writer_turn（原生 tool-loop + 全部
# story/web.search 工具 + 真相闸门）。此前走 script_writer_min（JSON-in-text 旧 runner），
# 与主会话共用 session 却用不同 system prompt，测的不是真实路径、还会污染会话历史。
# script_writer_min 保留为 legacy 参考（见其模块头标记），不再作为任何正式入口。
class ScriptWriterTestReq(BaseModel):
    content: str


@router.post("/api/project/{project_name}/agents/script-writer-test/turn")
async def script_writer_test_turn(project_name: str, req: ScriptWriterTestReq):
    """Script Writer 测试弹窗：真实 tool-loop turn（与主对话完全同链路）。
    自动取/建 script_writer 的长存 project 会话。返回 {assistant_message, edited,
    new_story_text?, message, ...}（new_story_text 供弹窗展示改后正文，edited 时从盘读）。"""
    from src.agents.script_writer import script_writer_turn
    rt = get_agent_runtime()
    try:
        sid = rt.get_or_create_session(project_name, "script_writer",
                                       "project", project_name)["session_id"]
        result = await script_writer_turn(rt, project_name, sid, req.content)
        if result.get("edited"):
            from src.core.store import ProjectStore
            pdir = rt._resolve_project_dir(project_name)
            result["new_story_text"] = ProjectStore(pdir).load().story_text or ""
        return result
    except AgentError as e:
        _raise(e)


@router.get("/api/project/{project_name}/agents/script-writer-test/session")
async def script_writer_test_session(project_name: str):
    """取 Script Writer 测试弹窗的长存会话（含历史消息，F5 恢复用）。"""
    rt = get_agent_runtime()
    try:
        return rt.get_or_create_session(project_name, "script_writer",
                                        "project", project_name)
    except AgentError as e:
        _raise(e)


# ==================== AI-7D-R · Director 最小测试弹窗（隔离，增/删故事段与镜头）====================
class DirectorTestReq(BaseModel):
    content: str


@router.post("/api/project/{project_name}/agents/director-test/turn")
async def director_test_turn(project_name: str, req: DirectorTestReq):
    """Director 测试弹窗：add_scenes/add_shots（追加，add-only）+ delete_scene/delete_shot
    （依赖感知级联删除，confirmed 两阶段：先返回依赖说明，用户确认后执行）。"""
    from src.agents.director_min import director_turn
    rt = get_agent_runtime()
    try:
        sid = rt.get_or_create_session(project_name, "director",
                                       "project", project_name)["session_id"]
        return await director_turn(rt, project_name, sid, req.content)
    except AgentError as e:
        _raise(e)


@router.get("/api/project/{project_name}/agents/director-test/session")
async def director_test_session(project_name: str):
    """取 Director 测试弹窗的长存会话（含历史消息，F5 恢复用）。"""
    rt = get_agent_runtime()
    try:
        return rt.get_or_create_session(project_name, "director",
                                        "project", project_name)
    except AgentError as e:
        _raise(e)



# ============ Phase 54 A1：scoped grant（项目级可撤销授权）============
class ScopedGrantReq(BaseModel):
    families: List[str]
    reason: str = ""
    granted_by: str = "operator"
    ttl_s: Optional[float] = None


class ScopedGrantRevokeReq(BaseModel):
    reason: str = ""
    revoked_by: str = "operator"


def _pdir_of(project_name: str):
    from pathlib import Path
    return Path(get_project_manager().get_project_dir(project_name))


@router.post("/api/project/{project_name}/agents/{agent_id}/grant")
async def agent_scoped_grant(project_name: str, agent_id: str, req: ScopedGrantReq):
    """新授权：把工具族（=Specialist 模块工具面）的直接执行权授予 agent（当前生产者消费）。
    审计落 output/agents/grants_audit.jsonl；owner/OCC 语义不变。"""
    from src.agents import grant as grant_mod
    try:
        g = grant_mod.grant(_pdir_of(project_name), agent_id, req.families,
                            req.reason, req.granted_by, req.ttl_s)
        return {"grant": g, "active": grant_mod.active(_pdir_of(project_name), agent_id)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/project/{project_name}/agents/{agent_id}/grant/{grant_id}/revoke")
async def agent_scoped_grant_revoke(project_name: str, agent_id: str, grant_id: str,
                                    req: ScopedGrantRevokeReq):
    from src.agents import grant as grant_mod
    ok = grant_mod.revoke(_pdir_of(project_name), grant_id, req.reason, req.revoked_by)
    if not ok:
        raise HTTPException(status_code=404, detail=f"grant 不存在或已撤销: {grant_id}")
    return {"revoked": grant_id,
            "active": grant_mod.active(_pdir_of(project_name), agent_id)}


@router.get("/api/project/{project_name}/agents/{agent_id}/grant")
async def agent_scoped_grant_list(project_name: str, agent_id: str):
    from src.agents import grant as grant_mod
    return {"active": grant_mod.active(_pdir_of(project_name), agent_id),
            "families": sorted(grant_mod.GRANT_FAMILIES.keys())}


# ==================== Phase55 B：协作模式注册表 ====================
class CollabRunStartReq(BaseModel):
    # Phase56 A1：mode_id 可选——缺省=实验默认（combo_recommended v2，
    # DEFAULT_EXPERIMENTAL_MODE_ID）；显式传 ID 的旧路径不变（六员等仍是
    # 合法显式选择/回退）。
    mode_id: Optional[str] = None
    params: dict = {}
    granted_by: str = "operator"
    thinking: bool = False


class CollabRunCancelReq(BaseModel):
    reason: str = ""


def _collab_availability(rt, project_name: str, mode: dict) -> tuple:
    """可用性与具体缺因（任务书 §4：尚未具备能力的预设标记不可用并给具体原因）。"""
    reasons: list = []
    try:
        rt._resolve_agent("producer")
        if not rt._is_enabled("producer"):
            reasons.append("producer 已禁用")
    except Exception:
        reasons.append("producer 不在注册表")
    if mode["execution"]["strategy"] == "producer_lead":
        client = rt._resolve_client_or_none(agent_id="producer")
        if client is None or not hasattr(client, "chat_completion"):
            reasons.append("文本模型不可用（chat_completion 未配置）")
        for aid in mode.get("grant_families") or []:
            try:
                if not rt._is_enabled(aid):
                    reasons.append(f"{aid} 已禁用（grant 族含其工具面）")
            except Exception:
                reasons.append(f"{aid} 不在注册表")
        for role in ((mode.get("preflight") or {}).get("roles") or []):
            try:
                if not rt._is_enabled(role):
                    reasons.append(f"预铺角色 {role} 已禁用")
            except Exception:
                reasons.append(f"预铺角色 {role} 不在注册表")
    else:
        tid = mode["execution"].get("template_id")
        try:
            from src.workflow.templates import create_from_template
            create_from_template(tid, "__avail_probe__", {})
        except Exception as e:
            reasons.append(f"模板 {tid} 不可用: {str(e)[:120]}")
    return (len(reasons) == 0, reasons)


@router.get("/api/project/{project_name}/collab-modes")
async def collab_modes_list(project_name: str):
    """协作模式注册表 + 可用性（含具体缺因）。"""
    rt = get_agent_runtime()
    from src.agents.collab_modes import list_modes
    from src.generation.video_contract import resolve_reference_policy
    pdir = _pdir_of(project_name)
    out = []
    for m in list_modes():
        ok, reasons = _collab_availability(rt, project_name, m)
        out.append({**m, "available": ok, "unavailable_reasons": reasons,
                    "active_reference_policy": resolve_reference_policy(pdir)})
    return {"modes": out}


@router.post("/api/project/{project_name}/collab-runs")
async def collab_run_start(project_name: str, req: CollabRunStartReq):
    """启动协作模式 run（快照固定配置→参考策略→grant→后台执行）。
    mode_id 缺省=实验默认（Phase56 A1）。立即返回 run 记录；非法组合 400（具体原因）。"""
    rt = get_agent_runtime()
    from src.agents import collab_modes as cm
    try:
        run = await cm.start_collab_run(rt, project_name, req.mode_id, req.params,
                                        granted_by=req.granted_by,
                                        thinking=req.thinking)
        return {"run": run,
                **({"used_default_mode": cm.DEFAULT_EXPERIMENTAL_MODE_ID}
                   if not (req.mode_id or "").strip() else {})}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/project/{project_name}/collab-runs")
async def collab_runs_list(project_name: str):
    from src.agents import collab_modes as cm
    return {"runs": cm.list_runs(_pdir_of(project_name))}


@router.get("/api/project/{project_name}/collab-runs/{run_id}")
async def collab_run_get(project_name: str, run_id: str):
    from src.agents import collab_modes as cm
    run = cm.load_run(_pdir_of(project_name), run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"collab run 不存在: {run_id}")
    return {"run": run}


@router.post("/api/project/{project_name}/collab-runs/{run_id}/cancel")
async def collab_run_cancel(project_name: str, run_id: str, req: CollabRunCancelReq):
    from src.agents import collab_modes as cm
    try:
        return cm.cancel_run(get_agent_runtime(), _pdir_of(project_name),
                             run_id, req.reason)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class CollabRunResumeReq(BaseModel):
    thinking: bool = False


@router.post("/api/project/{project_name}/collab-runs/{run_id}/resume")
async def collab_run_resume(project_name: str, run_id: str,
                            req: CollabRunResumeReq):
    """续跑 awaiting_relay 的 run（Phase56 A3/D2：自动接力上限后待接力的
    未完成 run；同一逻辑 run 授权续接+交接包恢复，非新建 run）。"""
    from src.agents import collab_modes as cm
    try:
        run = await cm.resume_collab_run(get_agent_runtime(), project_name,
                                         run_id, thinking=req.thinking)
        return {"run": run}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==================== Phase55 D：会话接力（手动触发） ====================
class RelayReq(BaseModel):
    reason: str = "手动会话接力"


@router.post("/api/project/{project_name}/agents/sessions/{sid}/relay")
async def relay_producer_session(project_name: str, sid: str, req: RelayReq):
    """Producer 会话手动接力：交接包落盘 → 新会话 → 主控租约移交。
    在途媒体由任务系统继续管理（不重发）；旧会话写路径被租约拒绝。"""
    rt = get_agent_runtime()
    from src.agents import handoff as handoff_mod
    try:
        out = handoff_mod.relay_producer_session(rt, project_name, sid,
                                                 reason=req.reason)
        return out
    except AgentError as e:
        _raise(e)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
