# src/agents/runtime.py
# AI-6B AgentRuntime（薄层，§15）。负责：
#   resolve agent/session/model profile · build context · permission check ·
#   dispatch capability · create/update AgentTask · return structured result ·
#   send_message（chat，软失败 not_configured）· delegate（同步，仅 Producer）。
#
# 不建立 reflection/planning/critic/self-correction/debate/recursive loop（§15/§24）。
#
# Import 纪律（§修正2）：本文件只 import deps(getter) / src.core.* / 本包。
#   plans_reader 由 composition root（routers/agents.py）注入——agents 包内绝不 import router。
# 单例：模块级 _runtime + get_agent_runtime()，镜像 src/ai/autopilot.py::get_autopilot_manager
# （AgentRuntime 自包含，无需 startup 赋值、无 recover_runs，v1 任务同步）。
from __future__ import annotations
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from deps import get_agnes_client, get_project_manager
from src.core.store import ProjectStore

from .models import (AgentDefinition, AgentMessage, AgentSession, AgentTask,
                     AgentStructuredResult, AgentOp, ResultStatus, ScopeType, TaskStatus)
from .registry import (Registries, agent_system_prompt)
from .capabilities import CapabilityContext, dispatch as dispatch_capability
from .context import build_context
from .sessions import AgentSessionStore
from .tasks import AgentTaskStore


class AgentError(Exception):
    """Agent runtime 错误。code 由 router 映射 HTTP。"""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


# 全部 specialist（§12：specialist→specialist 禁止，因为它们的 can_delegate_to 都为空）
SPECIALISTS = ["script_writer", "character_designer", "director", "production_planner",
               "shot_producer", "continuity_reviewer", "editor"]


class AgentRuntime:
    """Agent 运行时。client/agnes_getter/plans_reader/project_dir_resolver 均可注入（测试用）。"""

    def __init__(self,
                 client: Optional[Any] = None,
                 agnes_getter: Optional[Callable[[], Any]] = None,
                 plans_reader: Optional[Callable[[str, str], Optional[dict]]] = None,
                 settings_path: Optional[Path] = None,
                 project_dir_resolver: Optional[Callable[[str], Path]] = None):
        self._client = client
        self._agnes_getter = agnes_getter or get_agnes_client
        self._plans_reader = plans_reader
        self._project_dir_resolver = project_dir_resolver or (
            lambda name: get_project_manager().get_project_dir(name))
        self._reg = Registries(settings_path)

    # ---------- 基础访问 ----------
    def set_plans_reader(self, reader: Optional[Callable[[str, str], Optional[dict]]]) -> None:
        """composition root（routers/agents.py）注入 production-plan 读取 callable。
        agents 包内绝不 import router；只 router 侧 import production_planner 并提供此 callable。"""
        self._plans_reader = reader
    @property
    def agents(self):
        return self._reg.agents

    @property
    def capabilities(self):
        return self._reg.capabilities

    @property
    def profiles(self):
        return self._reg.profiles

    @property
    def settings(self):
        return self._reg.settings

    def _resolve_project_dir(self, project: str) -> Path:
        try:
            return self._project_dir_resolver(project)
        except FileNotFoundError:
            raise AgentError("unknown_project", f"项目不存在: {project}")
        except AgentError:
            raise
        except Exception as e:
            raise AgentError("unknown_project", f"项目不可用: {e}")

    def _session_store(self, project: str) -> AgentSessionStore:
        return AgentSessionStore(self._resolve_project_dir(project), project)

    def _task_store(self, project: str) -> AgentTaskStore:
        return AgentTaskStore(self._resolve_project_dir(project), project)

    def _project_loader(self, project: str) -> Callable[[], "Project"]:   # type: ignore[name-defined]
        pd = self._resolve_project_dir(project)
        return lambda: ProjectStore(pd).load()

    def _resolve_agent(self, agent_id: str) -> AgentDefinition:
        a = self.agents.get(agent_id)
        if a is None:
            raise AgentError("unknown_agent", f"未知 Agent: {agent_id}")
        return a

    def _is_enabled(self, agent_id: str) -> bool:
        return self.agents.is_enabled(agent_id, self.settings)

    # ---------- Agent / Capability / Profile 查询 ----------
    def list_agents(self) -> List[dict]:
        out = []
        for a in self.agents.list():
            prof = self.profiles.resolve_for_agent(a, self.settings)
            out.append({
                "id": a.id, "name": a.name, "description": a.description,
                "role": a.role, "read_scopes": a.read_scopes, "write_scopes": a.write_scopes,
                "can_delegate_to": a.can_delegate_to,
                "model_profile": prof.to_dict() if prof else None,
                "enabled": self._is_enabled(a.id),
            })
        return out

    def get_agent(self, agent_id: str) -> Optional[dict]:
        a = self.agents.get(agent_id)
        if a is None:
            return None
        prof = self.profiles.resolve_for_agent(a, self.settings)
        return {"id": a.id, "name": a.name, "description": a.description, "role": a.role,
                "read_scopes": a.read_scopes, "write_scopes": a.write_scopes,
                "can_delegate_to": a.can_delegate_to,
                "model_profile": prof.to_dict() if prof else None,
                "enabled": self._is_enabled(a.id)}

    def list_capabilities(self, agent_id: str) -> List[dict]:
        self._resolve_agent(agent_id)   # 校验存在
        out = []
        for c in self.capabilities.list_for_agent(agent_id):
            out.append({**c.to_dict(),
                        "can_execute": self.capabilities.can_execute(
                            agent_id, c.id, c.operations[0] if c.operations else AgentOp.INSPECT.value)})
        return out

    def list_profiles(self) -> List[dict]:
        return [p.to_dict() for p in self.profiles.list()]

    # ---------- Settings ----------
    def get_settings(self) -> dict:
        return self.settings.load()

    def update_agent_setting(self, agent_id: str,
                             profile_id: Optional[str] = None,
                             enabled: Optional[bool] = None) -> dict:
        if not self.agents.exists(agent_id):
            raise AgentError("unknown_agent", f"未知 Agent: {agent_id}")
        if profile_id is not None:
            prof = self.profiles.get(profile_id)
            if prof is None:
                raise AgentError("unknown_model_profile", f"未知 ModelProfile: {profile_id}")
            try:
                self.profiles.validate(prof)
            except ValueError as e:
                raise AgentError("invalid_model_config", str(e))
        return self.settings.update_agent(agent_id, profile_id, enabled)

    # ---------- Sessions ----------
    def list_sessions(self, project: str) -> List[dict]:
        return [s.to_dict() for s in self._session_store(project).list()]

    def get_or_create_session(self, project: str, agent_id: str,
                              scope_type: str = ScopeType.PROJECT.value,
                              scope_id: str = "") -> dict:
        a = self._resolve_agent(agent_id)
        if not self._is_enabled(agent_id):
            raise AgentError("disabled_agent", f"Agent 已禁用: {a.name}")
        s = self._session_store(project).get_or_create(agent_id, scope_type, scope_id)
        return s.to_dict()

    def get_session(self, project: str, sid: str) -> Optional[dict]:
        s = self._session_store(project).load(sid)
        return s.to_dict() if s else None

    # ---------- Chat（send_message，§21 软失败 not_configured）----------
    async def send_message(self, project: str, agent_id: str,
                           scope_type: str, scope_id: str, content: str) -> dict:
        """发消息聊天。chat 未配置 Agnes 是 AI-6B 正常态（软失败）：
        持久化 user + assistant(status=not_configured)，正常返回该 message，不抛、不影响现有 Workflow。"""
        a = self._resolve_agent(agent_id)
        if not self._is_enabled(agent_id):
            raise AgentError("disabled_agent", f"Agent 已禁用: {a.name}")
        store = self._session_store(project)
        session = store.get_or_create(agent_id, scope_type, scope_id)
        # 1. 持久化 user 消息
        store.append_message(session.session_id,
                             AgentMessage(role="user", content=content))
        session = store.load(session.session_id)   # 重读含新消息

        # 2. 构建 messages（system + context + history）
        ctx_text = build_context(agent_id, project, scope_type, scope_id,
                                 self._project_loader(project), self._plans_reader)
        messages = [{"role": "system",
                     "content": agent_system_prompt(agent_id) + "\n\n" + ctx_text}]
        for m in session.messages:
            if m.role in ("user", "assistant") and m.content and m.status is None:
                messages.append({"role": m.role, "content": m.content})

        # 3. 取 Agnes client（软失败：取不到 -> not_configured，§修正3）
        client = None
        if self._client is not None:
            client = self._client
        else:
            try:
                client = self._agnes_getter()
            except Exception:
                client = None
        if client is None:
            msg = AgentMessage(role="assistant",
                               content="[chat 未配置：Agnes 推理模型不可用。请在设置中配置 API Key 后重试。]",
                               status=ResultStatus.NOT_CONFIGURED.value)
            store.append_message(session.session_id, msg)
            return msg.to_dict()

        # 4. 调 LLM
        try:
            text = await client.chat(messages, temperature=0.7, max_tokens=1024)
        except Exception as e:
            msg = AgentMessage(role="assistant",
                               content=f"[调用失败: {str(e)[:200]}]",
                               status=ResultStatus.FAILED.value)
            store.append_message(session.session_id, msg)
            return msg.to_dict()
        msg = AgentMessage(role="assistant", content=text)
        store.append_message(session.session_id, msg)
        return msg.to_dict()

    async def send_message_to_session(self, project: str, sid: str, content: str) -> dict:
        """messages endpoint 用：按 session_id 取 agent+scope，再 send_message。"""
        s = self._session_store(project).load(sid)
        if s is None:
            raise AgentError("unknown_scope", f"session 不存在: {sid}")
        return await self.send_message(project, s.agent_id, s.scope_type, s.scope_id, content)

    # ---------- Tasks ----------
    def list_tasks(self, project: str, requester_agent: Optional[str] = None,
                   target_agent: Optional[str] = None,
                   status: Optional[str] = None) -> List[dict]:
        return [t.to_dict() for t in
                self._task_store(project).list(requester_agent, target_agent, status)]

    def get_task(self, project: str, tid: str) -> Optional[dict]:
        t = self._task_store(project).get(tid)
        return t.to_dict() if t else None

    def delegate(self, project: str, requester_agent: str, target_agent: str,
                 scope_type: str, scope_id: str, instruction: str,
                 capability_id: str, operation: str = AgentOp.INSPECT.value) -> dict:
        """Producer → Specialist 委派（同步，§11/§12）。
        specialist→specialist 禁止：只有 producer 的 can_delegate_to 非空。"""
        requester = self._resolve_agent(requester_agent)
        # §12：requester 必须能把任务委派给 target
        if not self.agents.can_delegate(requester_agent, target_agent):
            raise AgentError("permission_denied",
                             f"{requester.name} 无权委派给 {target_agent}（specialist→specialist 禁止）")
        target = self._resolve_agent(target_agent)   # unknown_agent
        if not self._is_enabled(target_agent):
            raise AgentError("disabled_agent", f"Agent 已禁用: {target.name}")
        cap = self.capabilities.get(capability_id)
        if cap is None:
            raise AgentError("unknown_capability", f"未知能力: {capability_id}")
        # §修正1：五重权限校验
        if not self.capabilities.can_execute(target_agent, capability_id, operation):
            raise AgentError("permission_denied",
                             f"{target.name} 无权执行 {capability_id}/{operation}")
        # 创建 task（pending）—— target 的 session
        session = self._session_store(project).get_or_create(
            target_agent, scope_type, scope_id)
        store = self._task_store(project)
        import time as _t
        now = _t.time()
        task = AgentTask(
            task_id="", project=project,
            requester_agent=requester_agent, target_agent=target_agent,
            capability_id=capability_id, instruction=instruction,
            scope_type=scope_type, scope_id=scope_id,
            session_id=session.session_id, operation=operation,
            status=TaskStatus.PENDING.value, created_at=now, updated_at=now)
        task = store.create(task)

        # running
        task.status = TaskStatus.RUNNING.value
        store.update_task(task)

        # dispatch
        ctx = CapabilityContext(
            project=project, scope_type=scope_type, scope_id=scope_id,
            project_loader=self._project_loader(project),
            plans_reader=self._plans_reader)
        result = dispatch_capability(capability_id, ctx)

        # 结算
        task.structured_result = result.to_dict()
        task.result_summary = result.summary
        task.artifact_refs = list(result.artifact_refs)
        task.warnings = list(result.warnings)
        task.suggested_next_actions = list(result.suggested_next_actions)
        if result.status == ResultStatus.FAILED.value:
            task.status = TaskStatus.FAILED.value
            task.error = result.summary
        elif result.status == ResultStatus.NOT_IMPLEMENTED.value:
            task.status = TaskStatus.FAILED.value
            task.error = f"能力未接通: {capability_id}"
        else:
            task.status = TaskStatus.COMPLETED.value
        store.update_task(task)
        return task.to_dict()


# ============ 单例（镜像 src/ai/autopilot.py）============
_runtime: Optional[AgentRuntime] = None


def get_agent_runtime() -> AgentRuntime:
    global _runtime
    if _runtime is None:
        _runtime = AgentRuntime()
    return _runtime


def reset_agent_runtime() -> None:
    """测试用：重置单例。"""
    global _runtime
    _runtime = None
