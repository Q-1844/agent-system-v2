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

from deps import get_agnes_client, get_project_manager, get_job_queue
from src.core.store import ProjectStore

from .models import (AgentDefinition, AgentMessage, AgentSession, AgentTask,
                     AgentStructuredResult, AgentOp, ResultStatus, ScopeType, TaskStatus)
from .registry import (Registries, agent_system_prompt)
from .capabilities import CapabilityContext, dispatch as dispatch_capability
from .context import build_context
from .sessions import AgentSessionStore
from .tasks import AgentTaskStore
from .proposal_store import ProposalStore
from .project_intelligence import ProjectIntelligenceService
from .director_proposal import (
    generate_proposal, preview_selected, apply_proposal, proposal_to_api,
    ProposalGenerationError, ProposalStatus,
)
from .planner_capability import create_production_plan as _create_production_plan
from .shot_producer_capability import execute_shot_production
from src.revision.workspace import WorkspaceStore
from src.revision.changeset import ChangeSetError


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
                 project_dir_resolver: Optional[Callable[[str], Path]] = None,
                 job_queue_getter: Optional[Callable[[], Any]] = None,
                 fake_shot_params: Optional[Dict[str, Any]] = None,
                 real_shot_pipeline: bool = False):
        self._client = client
        self._agnes_getter = agnes_getter or get_agnes_client
        self._plans_reader = plans_reader
        self._project_dir_resolver = project_dir_resolver or (
            lambda name: get_project_manager().get_project_dir(name))
        # AI-6F.0 §17：Shot Producer 长执行能力通过现有 JobQueue。getter 注入（测试用 lambda）。
        self._job_queue_getter = job_queue_getter or (lambda: get_job_queue())
        # AI-6F.0 §7：fake handler 行为可控（_fake_outcome/steps/sleep）。real=False（默认，测试）
        # 走 fake durable bridge；production（get_agent_runtime）置 real_shot_pipeline=True 走真实 JOB_HANDLERS。
        # fake_shot_params 仅 fake 路径用，保留以维持 AI-6F.0 测试不变（§7：fake kinds test-only）。
        self._fake_shot_params = fake_shot_params
        # AI-6F：True=真实生成（first_frame/last_frame/video JOB_HANDLERS + Plan preflight + origin traceability）；
        # False=fake durable bridge（AI-6F.0，测试默认）。
        self._real_shot_pipeline = real_shot_pipeline
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

    async def delegate(self, project: str, requester_agent: str, target_agent: str,
                       scope_type: str, scope_id: str, instruction: str,
                       capability_id: str, operation: str = AgentOp.INSPECT.value) -> dict:
        """Producer → Specialist 委派（§11/§12）。
        inspect 能力同步 dispatch；production_plan.create 等 async 能力走 _execute_* 分支
        （§11：需 await 真实 AI/IO）。specialist→specialist 禁止：只有 producer 的 can_delegate_to 非空。"""
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

        # dispatch（§11：async 能力走 _execute_* 分支 await 真实 AI/IO；inspect 走同步 dispatch）
        ctx = CapabilityContext(
            project=project, scope_type=scope_type, scope_id=scope_id,
            project_loader=self._project_loader(project),
            plans_reader=self._plans_reader)
        if capability_id in self._LONGRUNNING_CAPABILITIES:
            # AI-6F.0 §4/§6/§17：shot 长执行——提交 durable Job，HTTP 先返回（不等 Job 完成）。
            # settlement bridge（settlement.py）在 Job 终态后对账 AgentTask。
            task = await self._delegate_long_running(
                store, task, ctx, project, scope_type, scope_id, instruction, capability_id)
            return task.to_dict()
        if capability_id in self._ASYNC_CAPABILITIES:
            result = await self._execute_async_capability(
                capability_id, ctx, project, scope_type, scope_id, instruction)
        else:
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
        elif result.status == ResultStatus.NOT_CONFIGURED.value:
            # §9/§23 async 能力软失败（Agnes 未配置）：task FAILED，error 明示 not_configured
            task.status = TaskStatus.FAILED.value
            task.error = "能力未配置：Agnes 推理模型不可用"
        else:
            task.status = TaskStatus.COMPLETED.value
        store.update_task(task)
        return task.to_dict()

    # ---------- async capability 执行（§11）----------
    _ASYNC_CAPABILITIES = {"production_plan.create"}

    # AI-6F.0 §17：Shot Producer 长执行能力（提交 durable Job，HTTP 先返回，settlement 对账）。
    # 与 _ASYNC_CAPABILITIES 区别：前者 await 真实 IO 后同步结算；本类提交后**立即**返回 running。
    _LONGRUNNING_CAPABILITIES = {"shot.first_frame", "shot.last_frame", "shot.video"}

    async def _execute_async_capability(self, capability_id: str, ctx: "CapabilityContext",
                                        project: str, scope_type: str, scope_id: str,
                                        instruction: str) -> AgentStructuredResult:
        """§11 async 能力分支。按 capability_id 派发到对应 _execute_* 方法。
        未来其他 async 能力（shot 执行等）在此扩展，不扩 sync _ADAPTERS。"""
        if capability_id == "production_plan.create":
            return await self._execute_production_plan_create(
                ctx, project, scope_type, scope_id, instruction)
        return AgentStructuredResult(
            status=ResultStatus.NOT_IMPLEMENTED.value,
            summary=f"能力 {capability_id} 未接通 async 执行。")

    async def _delegate_long_running(self, store: "AgentTaskStore", task: "AgentTask",
                                      ctx: "CapabilityContext", project: str,
                                      scope_type: str, scope_id: str, instruction: str,
                                      capability_id: str) -> "AgentTask":
        """AI-6F.0 §4/§6/§17：Shot Producer 长执行分支。
        提交 durable fake Job（幂等：先查 task.execution_ref，已存在则 reattach/settle 不重提交）→
        把 execution_ref 写回 AgentTask → task 保持 running → 立即返回（HTTP 不等 Job 完成）。
        真实结算由 settlement bridge（settlement.py）在 Job 终态后对账；本处仅处理
        submit / reattach / settle-from-existing 三种即时结果。"""
        job_queue = self._resolve_job_queue_or_none()
        result = await execute_shot_production(
            client=self._resolve_client_or_none(),
            project_loader=self._project_loader(project),
            project_dir=self._resolve_project_dir(project),
            project_name=project, scope_type=scope_type, scope_id=scope_id,
            instruction=instruction, capability_id=capability_id,
            job_queue=job_queue,
            existing_ref=task.execution_ref,
            fake_job_params=self._fake_shot_params,
            real=self._real_shot_pipeline,
            origin_task_id=task.task_id)

        task.structured_result = result.to_dict()
        task.result_summary = result.summary
        task.artifact_refs = list(result.artifact_refs)
        task.warnings = list(result.warnings)
        if result.suggested_next_actions:
            task.suggested_next_actions = list(result.suggested_next_actions)

        if result.status == ResultStatus.RUNNING.value:
            # §4/§6：已提交/reattach linked Job → execution_ref 落盘 + task 保持 running。
            # data 已含 job_id；execution_ref 取 result.data 字段（executor 已装配）。
            data = result.data or {}
            if data.get("job_id"):
                task.execution_ref = {
                    "job_id": data["job_id"],
                    "kind": data.get("kind"),
                    "phase": data.get("phase"),
                    "shot_id": data.get("shot_id") or scope_id,
                    "submitted_at": data.get("submitted_at"),
                }
                # AI-6F §11/§20：持久化 origin_task_id（artifact traceability）+ video_id
                #（restart-recovery fallback 身份）；仅在有值时写入，避免污染 fake 路径 execution_ref。
                if data.get("origin_task_id"):
                    task.execution_ref["origin_task_id"] = data["origin_task_id"]
                if data.get("video_id"):
                    task.execution_ref["video_id"] = data["video_id"]
            # status 保持 running（pending→running 已在 delegate 设）
        elif result.status == ResultStatus.COMPLETED.value:
            # §6/§18-10：reattach 发现 linked Job 已 done → 直接结算 completed（不重提交）
            task.status = TaskStatus.COMPLETED.value
            task.error = None
        elif result.status == ResultStatus.NOT_CONFIGURED.value:
            # §9/§23 软失败（JobQueue 不可用）：task FAILED
            task.status = TaskStatus.FAILED.value
            task.error = result.summary
        elif result.status == ResultStatus.NOT_IMPLEMENTED.value:
            task.status = TaskStatus.FAILED.value
            task.error = f"能力未接通: {capability_id}"
        else:  # FAILED：scope/shot 无效 blocker 或 reattach 发现 linked Job 已 failed（§18-11）
            task.status = TaskStatus.FAILED.value
            task.error = result.summary
        store.update_task(task)
        return task

    async def _execute_production_plan_create(self, ctx: "CapabilityContext", project: str,
                                              scope_type: str, scope_id: str,
                                              instruction: str) -> AgentStructuredResult:
        """§6/§11/§14 production_plan.create：包装现有 Planner workflow（plan_production_business
        + 落盘），返回引用真实 production_plans.json artifact 的 AgentStructuredResult。
        不 approve（§17）、不写 canonical（§18）、不执行 Shot 生成（§31）。
        instruction 持久化于 AgentTask，v1 不注入 Planner prompt 以保持现有 schema 不变（§9/§23）。"""
        client = self._resolve_client_or_none()
        project_dir = self._resolve_project_dir(project)
        return await _create_production_plan(
            client, ctx.project_loader, project_dir, project,
            scope_type, scope_id, instruction)


    # ---------- Director Proposal（storyboard.propose_change，AI-6D）----------
    def _check_director_permission(self, agent_id: str) -> None:
        """§3：复用既有 Capability Registry + Agent 权限——Director 专用端点不绕过权限。"""
        a = self._resolve_agent(agent_id)
        if not self._is_enabled(agent_id):
            raise AgentError("disabled_agent", f"Agent 已禁用: {a.name}")
        if not self.capabilities.can_execute(agent_id, "storyboard.propose_change",
                                              AgentOp.EXECUTE.value):
            raise AgentError("permission_denied",
                             f"{a.name} 无权执行 storyboard.propose_change/execute")

    def _resolve_client_or_none(self):
        """取 Agnes client，不可用则 None（generate_proposal 会转 not_configured）。"""
        if self._client is not None:
            return self._client
        try:
            return self._agnes_getter()
        except Exception:
            return None

    def _resolve_job_queue_or_none(self):
        """AI-6F.0 §17：取现有 JobQueue，不可用则 None（shot 执行器转 not_configured 软失败）。
       SHOT 生产不阻塞 HTTP——即便队列未就绪也返回 task FAILED(not_configured) 而非抛 503。"""
        try:
            return self._job_queue_getter()
        except Exception:
            return None

    def _ws_store(self, project: str) -> WorkspaceStore:
        pd = self._resolve_project_dir(project)
        return WorkspaceStore(pd, project, self._project_loader(project))

    def _proposal_store(self, project: str) -> ProposalStore:
        return ProposalStore(self._resolve_project_dir(project), project)

    # ---------- AI-7A Project Intelligence（只读语义检视，§6）----------
    def project_intelligence(self, project: str) -> ProjectIntelligenceService:
        """构造只读 ProjectIntelligenceService，注入现有 resolver（project_loader /
        plans_reader / task_store / workspace_store）。镜像 _ws_store / _task_store 模式。
        service 纯读，绝不写 canonical project.json。"""
        return ProjectIntelligenceService(
            project_name=project,
            project_loader=self._project_loader(project),
            plans_reader=self._plans_reader,
            task_store_getter=lambda: self._task_store(project),
            workspace_store_factory=lambda: self._ws_store(project))

    async def propose_storyboard_change(self, project: str, agent_id: str,
                                        scope_type: str, scope_id: str,
                                        instruction: str) -> dict:
        """生成 Director Proposal（§11）：ctx→Agnes→parse+validate→create workspace→
        compile→AI-6A preview→persist。失败抛 AgentError（含 ChangeSetError code）。"""
        self._check_director_permission(agent_id)
        ws_store = self._ws_store(project)
        pstore = self._proposal_store(project)
        client = self._resolve_client_or_none()
        try:
            proposal, _pv = await generate_proposal(
                client, self._project_loader(project), ws_store, pstore,
                scope_type, scope_id, instruction)
        except ProposalGenerationError as e:
            raise AgentError(e.code or "proposal_failed", e.message)
        ws = ws_store.load(proposal.workspace_id)
        return proposal_to_api(proposal, ws_store.base_is_stale(ws))

    def get_director_proposal(self, project: str, proposal_id: str) -> Optional[dict]:
        """刷新恢复（§16）：从持久化 Proposal 重建 currentProposal（含存储的 preview）。"""
        pstore = self._proposal_store(project)
        proposal = pstore.get(proposal_id)
        if proposal is None:
            return None
        ws_store = self._ws_store(project)
        try:
            ws = ws_store.load(proposal.workspace_id)
            stale = ws_store.base_is_stale(ws)
        except Exception:
            stale = False
        return proposal_to_api(proposal, stale)

    def list_director_proposals(self, project: str, scope_type: str,
                                scope_id: str) -> List[dict]:
        """某 scope 最近的 Proposal（轻量，不含 changeset/base_is_stale）。"""
        pstore = self._proposal_store(project)
        return [p.to_dict() for p in pstore.list_by_scope(scope_type, scope_id)]

    async def preview_selected_subset(self, project: str, proposal_id: str,
                                       selected_item_ids: List[str]) -> dict:
        """选择性子集再预览（§13/§14）：重编译被选 item → AI-6A 新 preview。"""
        pstore = self._proposal_store(project)
        proposal = pstore.get(proposal_id)
        if proposal is None:
            raise AgentError("unknown_proposal", f"proposal 不存在: {proposal_id}")
        ws_store = self._ws_store(project)
        try:
            return preview_selected(proposal, selected_item_ids, ws_store, pstore)
        except ChangeSetError as e:
            raise AgentError(e.code, f"[{e.code}] {e.message}")

    async def apply_director_proposal(self, project: str, proposal_id: str,
                                       changeset_id: str) -> dict:
        """应用选中 ChangeSet（§13：按 changeset_id，复用 cs_engine.apply）。"""
        pstore = self._proposal_store(project)
        proposal = pstore.get(proposal_id)
        if proposal is None:
            raise AgentError("unknown_proposal", f"proposal 不存在: {proposal_id}")
        ws_store = self._ws_store(project)
        try:
            res = apply_proposal(proposal, changeset_id, ws_store, pstore)
        except ChangeSetError as e:
            raise AgentError(e.code, f"[{e.code}] {e.message}")
        return res

    def dismiss_director_proposal(self, project: str, proposal_id: str) -> dict:
        pstore = self._proposal_store(project)
        proposal = pstore.get(proposal_id)
        if proposal is None:
            raise AgentError("unknown_proposal", f"proposal 不存在: {proposal_id}")
        proposal.status = ProposalStatus.DISMISSED.value
        pstore.save(proposal)
        return {"dismissed": True, "proposal_id": proposal_id}


# ============ 单例（镜像 src/ai/autopilot.py）============
_runtime: Optional[AgentRuntime] = None


def get_agent_runtime() -> AgentRuntime:
    global _runtime
    if _runtime is None:
        # AI-6F：生产单例走真实 shot 生成管线（first_frame/last_frame/video JOB_HANDLERS + Plan
        # preflight + origin traceability）。测试直接构造 AgentRuntime(...) 不传 real_shot_pipeline
        # → 默认 False（fake durable bridge，AI-6F.0 测试不变）。
        _runtime = AgentRuntime(real_shot_pipeline=True)
    return _runtime


def reset_agent_runtime() -> None:
    """测试用：重置单例。"""
    global _runtime
    _runtime = None
