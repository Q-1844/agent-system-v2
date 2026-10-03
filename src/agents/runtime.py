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

from deps import (get_agnes_client, get_project_manager, get_job_queue,
                  get_sensenova_text_client)
from src.core.store import ProjectStore

from .models import (AgentDefinition, AgentMessage, AgentSession, AgentTask,
                     AgentStructuredResult, AgentOp, ResultStatus, ScopeType, TaskStatus)
from .registry import (Registries, agent_system_prompt)
from .capabilities import CapabilityContext, dispatch as dispatch_capability
from .context import build_context
from .sessions import AgentSessionStore
from .tasks import AgentTaskStore
from .work_memory import WorkMemoryStore
from .preferences import PreferenceStore, looks_like_preference
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
# Phase C2：scene_designer 加法式加入（勿改既有 id）。
SPECIALISTS = ["script_writer", "character_designer", "scene_designer", "director",
               "production_planner", "shot_producer", "continuity_reviewer", "editor",
               "assembly"]   # Phase 44B：Assembly Agent（基础成片）加法式加入


class AgentRuntime:
    """Agent 运行时。client/agnes_getter/plans_reader/project_dir_resolver 均可注入（测试用）。"""

    def __init__(self,
                 client: Optional[Any] = None,
                 agnes_getter: Optional[Callable[[], Any]] = None,
                 sensenova_getter: Optional[Callable[[], Any]] = None,
                 plans_reader: Optional[Callable[[str, str], Optional[dict]]] = None,
                 settings_path: Optional[Path] = None,
                 project_dir_resolver: Optional[Callable[[str], Path]] = None,
                 job_queue_getter: Optional[Callable[[], Any]] = None,
                 fake_shot_params: Optional[Dict[str, Any]] = None,
                 real_shot_pipeline: bool = False):
        self._client = client
        self._agnes_getter = agnes_getter or get_agnes_client
        # SenseNova 文本客户端 getter（2026-08-29 Agent 模型切换；未配置时返回 None）
        self._sensenova_getter = sensenova_getter or get_sensenova_text_client
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

    # AI-7D-R §10：共享工作记忆 + 显式偏好记忆（均项目级、耐久、轻量）
    def work_memory(self, project: str) -> WorkMemoryStore:
        return WorkMemoryStore(self._resolve_project_dir(project), project)

    def preference_store(self, project: str) -> PreferenceStore:
        return PreferenceStore(self._resolve_project_dir(project), project)

    def _memory_supplement(self, project: str, agent_id: str) -> str:
        """AI-7D-R §39：组装共享工作记忆 + 偏好记忆的上下文片段（不暴露隐藏推理）。
        canonical 真值优先级高于此处内容（§11）——由调用方把 canonical 放在更前面。"""
        sections = []
        try:
            wm = self.work_memory(project).render_for_context(limit=6)
            if wm:
                sections.append(wm)
        except Exception:
            pass
        try:
            pf = self.preference_store(project).render_for_context(limit=6)
            if pf:
                sections.append(pf)
        except Exception:
            pass
        return "\n\n".join(sections)

    def record_work(self, project: str, entry_type: str, summary: str, **kwargs) -> None:
        """best-effort 记录一条共享工作记忆（失败不阻断主流程）。"""
        try:
            self.work_memory(project).add(entry_type, summary, **kwargs)
        except Exception:
            pass

    def _maybe_capture_preference(self, project: str, content: str,
                                   message_id: Optional[str]) -> None:
        """AI-7D-R §10.3：保守捕获显式偏好（只在明确长期信号时，宁漏不误）。"""
        try:
            if not looks_like_preference(content):
                return
            key = content.strip()[:80]
            self.preference_store(project).add(
                key=key, value=content.strip(), scope="project",
                source_message_id=message_id)
        except Exception:
            pass

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
        # AI-7D-R §9：Direct Chat 用长存 (project, agent) Conversation；project scope 直接
        # 走稳定 conversation（迁移最近旧 project 会话，保留历史）。非 project scope 保留旧逻辑。
        if scope_type == ScopeType.PROJECT.value:
            s = self._session_store(project).get_or_create_conversation(agent_id)
        else:
            s = self._session_store(project).get_or_create(agent_id, scope_type, scope_id)
        return s.to_dict()

    def append_session_message(self, project: str, session_id: str,
                                role: str, content: str, *,
                                status: Optional[str] = None,
                                linked_change_session_id: Optional[str] = None,
                                linked_execution_id: Optional[str] = None,
                                linked_work_ids: Optional[List[str]] = None,
                                meta: Optional[dict] = None) -> Optional[dict]:
        """AI-7D-R §10.1：向会话追加一条带链接的耐久消息（供变更桥接把真实工作事件写回转录，
        不再只在前端合成）。失败/会话不存在返回 None。"""
        msg = AgentMessage(role=role, content=content, status=status, meta=meta,
                           linked_change_session_id=linked_change_session_id,
                           linked_execution_id=linked_execution_id,
                           linked_work_ids=list(linked_work_ids or []))
        s = self._session_store(project).append_message(session_id, msg)
        return s.to_dict() if s else None

    def get_session(self, project: str, sid: str) -> Optional[dict]:
        s = self._session_store(project).load(sid)
        return s.to_dict() if s else None

    # ---------- Chat（send_message，§21 软失败 not_configured）----------
    async def _produce_chat_reply(self, project: str, agent_id: str,
                                  scope_type: str, scope_id: str,
                                  session: AgentSession) -> AgentMessage:
        """构建 system+context+history → client.chat → 返回 assistant AgentMessage（不落盘）。
        AI-7D D1：从 send_message 抽出，供 unified_turn 的 conversation 分支复用，避免重复。
        chat 未配置 Agnes → 返回 status=not_configured 的 AgentMessage（软失败，§21/§修正3）。"""
        # 构建 messages（system + context + history）
        ctx_text = build_context(agent_id, project, scope_type, scope_id,
                                 self._project_loader(project), self._plans_reader)
        # AI-7C-H §13：追加持久化工作流快照（Layer B 运行时真相）——Agent 回答进度只许
        # 引用此节；未提供的状态按真话纪律回答「无法核实」。懒 import 防循环依赖。
        try:
            from .change_protocol.service import active_workflow_snapshot
            wf = active_workflow_snapshot(self._resolve_project_dir(project), project)
            if wf:
                import json as _json
                ctx_text += ("\n\n# 活跃工作流（持久化运行时真相，回答进度只引用此节）\n"
                             + _json.dumps(wf, ensure_ascii=False))
        except Exception:
            pass
        # AI-7D-R §39：共享工作记忆 + 显式偏好（建议性；canonical 与当前指令优先）
        mem = self._memory_supplement(project, agent_id)
        if mem:
            ctx_text += "\n\n" + mem
        messages = [{"role": "system",
                     "content": agent_system_prompt(agent_id) + "\n\n" + ctx_text}]
        for m in session.messages:
            if m.role in ("user", "assistant") and m.content and m.status is None:
                messages.append({"role": m.role, "content": m.content})

        # 取 Agnes client（软失败：取不到 -> not_configured，§修正3）
        client = None
        if self._client is not None:
            client = self._client
        else:
            try:
                client = self._agnes_getter()
            except Exception:
                client = None
        if client is None:
            return AgentMessage(
                role="assistant",
                content="[chat 未配置：Agnes 推理模型不可用。请在设置中配置 API Key 后重试。]",
                status=ResultStatus.NOT_CONFIGURED.value)
        try:
            text = await client.chat(messages, temperature=0.7, max_tokens=1024)
        except Exception as e:
            return AgentMessage(role="assistant",
                                content=f"[调用失败: {str(e)[:200]}]",
                                status=ResultStatus.FAILED.value)
        return AgentMessage(role="assistant", content=text)

    async def send_message(self, project: str, agent_id: str,
                           scope_type: str, scope_id: str, content: str) -> dict:
        """发消息聊天（legacy chat 路径）。chat 未配置 Agnes 是 AI-6B 正常态（软失败）：
        持久化 user + assistant(status=not_configured)，正常返回该 message，不抛、不影响现有 Workflow。
        AI-7D D1：聊天逻辑抽到 _produce_chat_reply；send_message 保留为 conversation 路径 +
        /messages wrapper 的底层。"""
        a = self._resolve_agent(agent_id)
        if not self._is_enabled(agent_id):
            raise AgentError("disabled_agent", f"Agent 已禁用: {a.name}")
        store = self._session_store(project)
        session = store.get_or_create(agent_id, scope_type, scope_id)
        store.append_message(session.session_id,
                             AgentMessage(role="user", content=content))
        session = store.load(session.session_id)   # 重读含新消息
        msg = await self._produce_chat_reply(project, agent_id, scope_type, scope_id, session)
        store.append_message(session.session_id, msg)
        return msg.to_dict()

    # ---------- AI-7D D1 · Unified Direct Chat Turn Runtime ----------
    async def unified_turn(self, project: str, sid: str, content: str,
                           *, thinking: bool = False) -> dict:
        """Direct Chat 统一 turn 入口（D1.7 单一 runtime）。按 session 取 agent+scope →
        classify_turn_intent 分流：
          conversation（含 change 降级，D2 接桥）→ _produce_chat_reply（聊天）
          project_inquiry → Producer: run_inquiry / Specialist: run_specialist_read_answer（真 inspect→接地回答）
          runtime_query → 同 inquiry 路径 + active_workflow_snapshot 注入
        返回 AgentTurnResult（D1.5）：assistant_message 是用户唯一所见自然语言；events 是真后台事件
        （非模型叙述）；evidence_refs 来自真实 inspect。user+assistant(meta) 落盘 AgentSessionStore。
        assistant_message 永不含 tool-call JSON / capability id / raw stack（D1.6）。
        Phase 46（M02）：thinking 显式开启时透传给各原生 tool-loop（ Producer 解析等；
        缺省 False 保持旧行为）。"""
        import secrets as _secrets
        store = self._session_store(project)
        session = store.load(sid)
        if session is None:
            raise AgentError("unknown_scope", f"session 不存在: {sid}")
        agent_id = session.agent_id
        scope_type = session.scope_type
        scope_id = session.scope_id
        a = self._resolve_agent(agent_id)
        if not self._is_enabled(agent_id):
            raise AgentError("disabled_agent", f"Agent 已禁用: {a.name}")

        # AI-7D-R：Script Writer 项目级 Direct Chat 走原生 function-calling tool-loop
        #（自己持久化消息、自己循环，不经过 inquiry/change 路径）。在 unified_turn 持久化 user
        # 之前委派，避免重复落盘。仅 project scope：tool-loop 编辑全局 story_text，语义属项目级；
        # scene/shot 级会话保留 D1 specialist read-answer 路径。
        if agent_id == "script_writer" and scope_type == ScopeType.PROJECT.value:
            from .script_writer import script_writer_turn
            return await script_writer_turn(self, project, sid, content, thinking=thinking)

        # 2026-08-30：Character Designer 项目级 Direct Chat 同走原生 tool-loop（只写角色
        # canonical；story/script 只读参考）。与 script_writer 同语义：在持久化 user 之前委派。
        if agent_id == "character_designer" and scope_type == ScopeType.PROJECT.value:
            from .character_designer import character_designer_turn
            return await character_designer_turn(self, project, sid, content, thinking=thinking)

        # Agent System V2 Phase C2（28.md §3/§4）：Scene Agent 项目级 Direct Chat 走原生
        # tool-loop（只写 Scene System canonical；story/shot/profile 只读参考）。
        # 与 SW/CD 同语义：在持久化 user 之前委派，避免重复落盘。
        if agent_id == "scene_designer" and scope_type == ScopeType.PROJECT.value:
            from .scene_designer import scene_designer_turn
            return await scene_designer_turn(self, project, sid, content, thinking=thinking)

        # Phase 44B：Assembly Agent 项目级 Direct Chat 走原生 tool-loop（只写成片装配域
        # canonical；Shot/Story/快照只读消费）。与 SW/CD/SD 同语义：在持久化 user 之前委派。
        if agent_id == "assembly" and scope_type == ScopeType.PROJECT.value:
            from .assembly import assembly_turn
            return await assembly_turn(self, project, sid, content, thinking=thinking)

        # Producer V1 Phase 1：Producer 项目级 Direct Chat 走原生 tool-loop（自己持久化
        # 消息、自己循环，经 *.delegate 真实调用 SW/CD/Director *_turn；不经过 inquiry
        # 只读路径）。与 SW/CD 同语义：在持久化 user 之前委派，避免重复落盘。仅 project scope。
        # 门控：原生 tool-loop 需要 function-calling client（chat_completion）。纯 chat
        # client（D1/D3 冻结契约——FakeClient 只有 .chat，驱动 inquiry/conversation/runtime_control
        # 意图分流）与未配置 client 继续走下方 D1/D3 路径；真 Agnes（具 chat_completion）才接管。
        if agent_id == "producer" and scope_type == ScopeType.PROJECT.value:
            _pclient = self._resolve_client_or_none(agent_id="producer")
            if _pclient is not None and hasattr(_pclient, "chat_completion"):
                from .producer import producer_turn
                return await producer_turn(self, project, sid, content, thinking=thinking)

        # 捕获对话历史（append 当前 user 之前的 messages）供 inquiry 路径多轮承接（D1.3 / Bug B）。
        # conversation 路径（_produce_chat_reply）自己读 session.messages 含历史；inquiry 路径需显式注入。
        history = [{"role": m.role, "content": m.content}
                   for m in session.messages
                   if m.role in ("user", "assistant") and m.content and m.status is None]

        # 1. 持久化 user 消息（一次）。AI-7D-R §10.1：append 时生成 message_id 并回填链接字段。
        user_msg = AgentMessage(role="user", content=content)
        store.append_message(sid, user_msg)
        # AI-7D-R §10.3：保守捕获显式偏好（best-effort，拿 source message_id）
        self._maybe_capture_preference(project, content, user_msg.message_id)
        session = store.load(sid)   # 重读含 user 消息，供 chat history / 不影响 inquiry pack

        # 2. 分类意图（lazy import 防循环：inquiry 顶部 import runtime）
        from .inquiry import classify_turn_intent, run_inquiry, run_specialist_read_answer
        intel = self.project_intelligence(project)
        proj = intel._project()
        ti = classify_turn_intent(content, proj)
        intent = ti["intent"]
        is_query = (intent == "runtime_query")

        # 3. 持久化工作流快照（runtime_query 注入用；AI-7C-H §13 Layer B 真相）
        workflow_state = None
        if is_query:
            try:
                from .change_protocol.service import active_workflow_snapshot
                workflow_state = active_workflow_snapshot(
                    self._resolve_project_dir(project), project)
            except Exception:
                workflow_state = None

        client_configured = self._resolve_client_or_none() is not None
        events: list = []
        evidence_refs: list = []
        inspection_calls: list = []
        assistant_text = ""
        status: Optional[str] = None
        linked_change_session_id: Optional[str] = None

        # 4. 分流
        # AI-7D D3：runtime_control（控制命令）——纯 code→action→真后态接地回复，不调 LLM
        #（控制即便 chat 未配置也可用，D3.3 真话纪律；数据来自持久化状态，无假百分比）。
        if intent == "runtime_control":
            r = await self._runtime_control_reply(
                project, sid, agent_id, content, ti.get("control"))
            assistant_text = r["assistant_text"]
            events = r["events"]
            evidence_refs = r.get("evidence_refs", [])
            status = r.get("status")
            linked_change_session_id = r.get("linked_change_session_id")
        elif intent == "conversation":
            msg = await self._produce_chat_reply(
                project, agent_id, scope_type, scope_id, session)
            assistant_text = msg.content
            status = msg.status
            # conversation 无 inspect 事件 / 无 evidence
        elif agent_id == "producer":
            # Producer 查询：复用 run_inquiry（已 build pack + 真 inspect + _producer_answer 接地）
            # run_inquiry 签名：(rt, project, user_request, *, client, max_rounds, workflow_state, force_kind)
            # force_kind="question"：unified_turn 已分类为 project_inquiry/runtime_query，强制 question 路径，
            # 避免 negated-change 文本（"先不要修改"）被 run_inquiry 内部 classify_request 误判为 change。
            result = await run_inquiry(
                self, project, content, force_kind="question",
                workflow_state=workflow_state if is_query else None,
                history=history)
            obs = (result.get("context_observability") or {})
            inspection_calls = obs.get("inspection_calls", [])
            evidence_refs = result.get("evidence_refs", [])
            assistant_text = result.get("answer", "")
            # inquiry 成功 → status=None（普通 assistant 消息，进历史供多轮承接；Bug B）。
            # AgentMessage.status 只该是 None/not_configured/failed（COMPLETED 是 AgentTask 状态，误用会
            # 被 history 过滤 m.status is None 排除，导致无上下文承接）。
            status = (ResultStatus.NOT_CONFIGURED.value if not client_configured else None)
            events = [{"kind": "project_inspect",
                       "summary": "Producer 检视：" + (", ".join(inspection_calls) or "overview"),
                       "refs": evidence_refs}] if inspection_calls else []
        else:
            # Specialist 查询：复用 run_specialist_read_answer（build_for_specialist + 真 inspect + agent_system_prompt 接地）
            r = await run_specialist_read_answer(
                self, project, agent_id, scope_type, scope_id, content,
                workflow_state=workflow_state if is_query else None,
                history=history)
            assistant_text = r.get("answer", "")
            evidence_refs = r.get("evidence_refs", [])
            inspection_calls = r.get("inspection_calls", [])
            # inquiry 成功 → status=None（进历史供多轮承接；Bug B）。not_configured/failed 保留。
            _rs = r.get("status")
            status = None if _rs == ResultStatus.COMPLETED.value else _rs
            events = [{"kind": "project_inspect",
                       "summary": f"{a.name} 检视：" + (", ".join(inspection_calls) or "module view"),
                       "refs": evidence_refs}] if inspection_calls else []

        # 5. 落盘 assistant 消息（携带 meta：intent/evidence/events，供刷新后「已查看」证据）
        meta = {"intent": intent, "evidence_refs": evidence_refs,
                "events": events, "linked_task_ids": [], "linked_change_session_id": linked_change_session_id}
        asst_msg = AgentMessage(role="assistant", content=assistant_text,
                                status=status, meta=meta)
        store.append_message(sid, asst_msg)

        return {
            "turn_id": "turn_" + _secrets.token_hex(6),
            "session_id": sid, "agent_id": agent_id,
            "intent": intent,
            "assistant_message": assistant_text,
            "events": events,
            "linked_task_ids": [],
            "linked_change_session_id": linked_change_session_id,
            "evidence_refs": evidence_refs,
            "message": asst_msg.to_dict(),
        }

    async def send_message_to_session(self, project: str, sid: str, content: str) -> dict:
        """AI-7D D1：/messages endpoint 的 deprecated wrapper——内部走 unified_turn（单一 runtime，
        D1.7）。返回 assistant AgentMessage（向后兼容既有只消费 message 的调用方）。"""
        result = await self.unified_turn(project, sid, content)
        return result["message"]

    # ---------- AI-7D D3 · runtime_control（控制命令→既有后端 action→接地回复）----------
    @staticmethod
    def _stage_zh(stage: Optional[str]) -> str:
        """execution stage → 中文（供控制回复引用真进度，D3.3：无假百分比）。"""
        return {
            "revalidate": "校验方案", "workspace_create": "准备执行工作区",
            "creative_replay": "回放创作修改", "workspace_verify": "校验执行结果",
            "canonical_commit": "提交创作修改",
            "character_sync": "同步角色参考图",
            "production_planning": "准备受影响场景的制作计划",
            "production_prepare": "准备镜头生成",
            "production": "生成镜头与视频", "compose": "合成最终视频",
            "done": "已完成",
        }.get(stage or "", "推进中")

    def _resolve_control_target(self, snapshot) -> Optional[dict]:
        """从真后态快照解析控制目标。snapshot（active_workflow_snapshot）已按 updated_at 倒序
        且只含非终态 execution + 非终态 session。优先有 execution 的条目（执行期 stop/resume
        目标）；否则最近非终态 session（approve/reject/replan 目标）。无活跃→None。"""
        for entry in snapshot or []:
            ex = entry.get("execution")
            if ex:
                return {
                    "session_id": entry.get("session_id"),
                    "execution_id": ex.get("execution_id"),
                    "request": entry.get("request") or "",
                    "session_status": entry.get("status"),
                    "execution_status": ex.get("status"),
                    "stage": ex.get("stage"),
                    "production_run_id": ex.get("production_run_id"),
                }
        for entry in snapshot or []:
            return {
                "session_id": entry.get("session_id"), "execution_id": None,
                "request": entry.get("request") or "",
                "session_status": entry.get("status"),
                "execution_status": None, "stage": None,
                "production_run_id": None,
            }
        return None

    async def _runtime_control_reply(self, project: str, sid: str, agent_id: str,
                                      content: str, control: Optional[str]) -> dict:
        """D3.2 分派：自然语言控制命令 → 既有后端 action → 真后态接地回复。
        纯 code→action→真状态→确定性中文（不调 LLM；控制即便 chat 未配置也可用）。
        返回 {assistant_text, events, evidence_refs, linked_change_session_id, status}。

        锁（correctness）：cancel/resume_execution 自带 project_write_lock，直接调；
        approve/reject/request_changes/resume_session 无内部锁 → 持 session_lock（镜像 router，
        防并发编译同一 plan；锁顺序与 router 一致/子集，无死锁）。"""
        from .change_protocol import service as cp_service
        from .change_protocol import execution_service as cp_exec
        from .change_protocol.locks import session_lock

        project_dir = self._resolve_project_dir(project)
        snapshot = cp_service.active_workflow_snapshot(project_dir, project)
        target = self._resolve_control_target(snapshot)

        if target is None:
            return {"assistant_text": "当前没有正在进行的任务可以操作。",
                    "events": [{"kind": "runtime_control", "action": control,
                                "outcome": "no_active_target", "summary": "无活跃任务"}],
                    "evidence_refs": [], "linked_change_session_id": None, "status": None}

        sid_t = target["session_id"]
        req_text = target.get("request") or ""
        ex_id = target.get("execution_id")
        stage_zh = self._stage_zh(target.get("stage"))

        def _ev(action: str, **kw) -> dict:
            kw.update({"kind": "runtime_control", "action": action,
                       "target_session_id": sid_t})
            if ex_id:
                kw["target_execution_id"] = ex_id
            return kw

        # ---- stop ----
        if control == "stop":
            if not ex_id:
                txt = (f"「{req_text}」的方案还在准备中，尚未进入执行，没有正在运行的任务可停止。"
                       f"如要放弃，可以告诉我「拒绝方案」。")
                return {"assistant_text": txt,
                        "events": [_ev("stop", outcome="no_execution", summary=txt)],
                        "evidence_refs": [], "linked_change_session_id": sid_t, "status": None}
            res = await cp_exec.cancel_execution(self, project, ex_id)
            ex = res.get("execution") or {}
            committed = res.get("creative_committed")
            if committed:
                txt = (f"已请求停止「{req_text}」。已落盘的创作修改会保留，正在收尾已开始的工作"
                       f"（{stage_zh}），不会启动新的生成。当前状态：{ex.get('status', '正在停止')}。")
            else:
                txt = (f"已停止「{req_text}」，任务尚未提交到项目，未改动任何项目内容。"
                       f"当前状态：{ex.get('status', '已取消')}。")
            return {"assistant_text": txt,
                    "events": [_ev("stop", outcome=ex.get("status"),
                                   creative_committed=committed, summary=txt)],
                    "evidence_refs": [], "linked_change_session_id": sid_t, "status": None}

        # ---- resume ----
        if control == "resume":
            if ex_id:
                res = await cp_exec.resume_execution(self, project, ex_id)
                ex = res.get("execution") or {}
                txt = (f"已恢复推进「{req_text}」。当前阶段：{self._stage_zh(ex.get('stage'))}，"
                       f"状态：{ex.get('status', '运行中')}。")
                return {"assistant_text": txt,
                        "events": [_ev("resume", outcome=ex.get("status"), summary=txt)],
                        "evidence_refs": [], "linked_change_session_id": sid_t, "status": None}
            async with session_lock(project, sid_t):
                await cp_service.resume_session(self, project, sid_t)
            s = cp_service.get_session(self, project, sid_t) or {}
            txt = f"已恢复推进「{req_text}」。当前状态：{s.get('status', '推进中')}。"
            return {"assistant_text": txt,
                    "events": [_ev("resume", outcome=s.get("status"), summary=txt)],
                    "evidence_refs": [], "linked_change_session_id": sid_t, "status": None}

        # approve/reject/replan：若目标有活跃 execution（已批准执行中）→ 不误操作运行中会话
        if control in ("approve", "reject", "replan") and ex_id:
            txt = (f"「{req_text}」的方案已批准并正在执行中（{stage_zh}），不能批准/拒绝/重规划。"
                   f"如要中止可告诉我「停止」。")
            return {"assistant_text": txt,
                    "events": [_ev(control, outcome="already_running", summary=txt)],
                    "evidence_refs": [], "linked_change_session_id": sid_t, "status": None}

        # ---- approve ----
        if control == "approve":
            plan = cp_service.get_plan(self, project, sid_t) or {}
            plan_id = plan.get("plan_id")
            version = plan.get("version")
            s_status = target.get("session_status")
            if not plan_id or s_status != "ready_for_review":
                txt = (f"「{req_text}」的方案还没准备好，暂不能批准"
                       f"（当前状态：{s_status or '准备中'}）。方案就绪后再告诉我批准。")
                return {"assistant_text": txt,
                        "events": [_ev("approve", outcome="not_ready", summary=txt)],
                        "evidence_refs": [], "linked_change_session_id": sid_t, "status": None}
            async with session_lock(project, sid_t):
                cp_service.approve_plan(self, project, sid_t, plan_id, version)
            txt = (f"已批准「{req_text}」的修改方案（版本 {version}）。进入待执行状态——"
                   f"批准不等于执行，你确认后才会真正修改项目。")
            return {"assistant_text": txt,
                    "events": [_ev("approve", plan_id=plan_id, plan_version=version,
                                   outcome="approved", summary=txt)],
                    "evidence_refs": [], "linked_change_session_id": sid_t, "status": None}

        # ---- reject ----
        if control == "reject":
            plan = cp_service.get_plan(self, project, sid_t) or {}
            plan_id = plan.get("plan_id")
            async with session_lock(project, sid_t):
                cp_service.reject_plan(self, project, sid_t, plan_id)
            txt = f"已拒绝「{req_text}」的方案，未改动任何项目内容。"
            return {"assistant_text": txt,
                    "events": [_ev("reject", plan_id=plan_id, outcome="rejected", summary=txt)],
                    "evidence_refs": [], "linked_change_session_id": sid_t, "status": None}

        # ---- replan ----
        if control == "replan":
            plan = cp_service.get_plan(self, project, sid_t) or {}
            plan_id = plan.get("plan_id")
            if not plan_id:
                txt = f"「{req_text}」当前没有方案可调整。"
                return {"assistant_text": txt,
                        "events": [_ev("replan", outcome="no_plan", summary=txt)],
                        "evidence_refs": [], "linked_change_session_id": sid_t, "status": None}
            async with session_lock(project, sid_t):
                res = cp_service.request_changes(self, project, sid_t, content, plan_id)
            new_plan = (res or {}).get("plan") or {}
            nv = new_plan.get("version")
            txt = (f"已按你的反馈重新整理方案（版本 {nv}），正在重新生成提案，"
                   f"完成后在这里给你看。")
            return {"assistant_text": txt,
                    "events": [_ev("replan", plan_id=new_plan.get("plan_id"),
                                   plan_version=nv, outcome="replanned", summary=txt)],
                    "evidence_refs": [], "linked_change_session_id": sid_t, "status": None}

        # 兜底（_classify_control 已限定 5 类，不应到这）
        return {"assistant_text": "我理解你想控制任务，但没识别出具体动作。",
                "events": [_ev(control, outcome="unhandled")],
                "evidence_refs": [], "linked_change_session_id": sid_t, "status": None}

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

    def _agent_text_provider(self, agent_id: str) -> str:
        """Agent 当前绑定 ModelProfile 的 text provider（agnes/sensenova）。
        解析失败/未绑定/旧档案无该字段 -> agnes（默认，向后兼容）。"""
        try:
            a = self._resolve_agent(agent_id)
            prof = self.profiles.resolve_for_agent(a, self.settings)
            return getattr(prof, "text_provider", None) or "agnes"
        except Exception:
            return "agnes"

    def _resolve_client_or_none(self, agent_id: Optional[str] = None):
        """取文本 client，不可用则 None（generate_proposal 会转 not_configured）。

        agent_id 传入时（2026-08-29 Agent 模型切换）：该 Agent 绑定档案的
        text_provider=sensenova 则返回 SenseNova 文本客户端（未配置回落 Agnes）；
        不传或 provider=agnes 走原共享 Agnes 客户端，行为不变。
        self._client（测试注入）优先级最高，不受档案影响。"""
        if self._client is not None:
            return self._client
        if agent_id and self._agent_text_provider(agent_id) == "sensenova":
            try:
                sn = self._sensenova_getter()
                if sn is not None:
                    return sn
            except Exception:
                pass    # SenseNova 未配置/未就绪 -> 回落 Agnes 共享客户端
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
