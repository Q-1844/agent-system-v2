# src/agents/models.py
# AI-6B Multi-Agent Foundation —— 数据模型层。
# AgentDefinition / CapabilityDefinition / ModelProfile / AgentSession / AgentTask /
# AgentMessage / AgentStructuredResult 的 dataclass 定义。
# 风格对齐 src/revision/models.py（stdlib dataclass + to_dict/from_dict + 字段过滤）。
#
# 设计不变量：
#   Agent = 决策层；不写 canonical project（§18/§19）。AI-6B v1 只接通 read-only inspect，
#   edit/execute 能力注册权限但 adapter 返回 not_implemented（预留）。
#   allowed_capabilities 是真实授权检查的一环，不是展示元数据（can_execute 校验）。
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional, List, Dict
import time


# ============ 枚举 ============
class TaskStatus(str, Enum):
    PENDING   = "pending"
    RUNNING    = "running"
    COMPLETED  = "completed"
    FAILED     = "failed"
    CANCELLED  = "cancelled"


class ScopeType(str, Enum):
    PROJECT = "project"
    SCENE   = "scene"
    SHOT    = "shot"


class AgentOp(str, Enum):
    INSPECT = "inspect"
    EXECUTE = "execute"
    RETRY   = "retry"
    CANCEL  = "cancel"
    PROPOSE = "propose"   # AI-7B：产出语义修改提案（不落盘、不应用）。READ_ONLY 拒、PROPOSAL_ONLY 放。


class CapabilityCategory(str, Enum):
    PROJECT         = "project"
    STORY           = "story"
    CHARACTER      = "character"
    SCENE           = "scene"     # Agent System V2 Phase C2：Scene Agent（场景生产系统）
    STORYBOARD      = "storyboard"
    PRODUCTION_PLAN = "production_plan"
    SHOT            = "shot"
    REVIEW          = "review"
    COMPOSITION     = "composition"
    SEQUENCE        = "sequence"   # Phase 44B：成片装配域（SequenceBlock/manifest/proposal）


class RiskLevel(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"


class ResultStatus(str, Enum):
    COMPLETED        = "completed"
    NOT_CONFIGURED   = "not_configured"     # chat 未配置 Agnes（v1 软失败）
    NOT_IMPLEMENTED  = "not_implemented"    # 能力注册权限但 v1 未接通
    FAILED           = "failed"
    STOPPED          = "stopped"      # AI-7D-R：turn 被用户/系统中途停止（已完成的写保留）
    # AI-6F.0：长执行能力（shot.first_frame/last_frame/video）提交 durable Job 后的「已启动、未结算」
    # 信号。仅用于 AgentStructuredResult.status 透传给 delegate（§4：HTTP 先返回，task 保持 running，
    # 由 settlement bridge 在 Job 终态后结算）。**不**是 AgentTask.status（§9：task 持久状态仍
    # pending/running/completed/failed）。structured_result.status="running" 表示进度快照「进行中」。
    RUNNING          = "running"


# ============ AgentDefinition（§4）============
@dataclass
class AgentDefinition:
    """一个固定 Agent 的定义。allowed_capabilities 纳入真实授权检查（can_execute）。"""
    id: str
    name: str
    description: str                       # 一句话职责
    role: str                               # 角色定位（总协调/剧本/角色/导演/规划/制作/审查/剪辑）
    allowed_capabilities: List[str] = field(default_factory=list)
    read_scopes: List[str] = field(default_factory=list)
    write_scopes: List[str] = field(default_factory=list)
    can_delegate_to: List[str] = field(default_factory=list)
    default_model_profile: str = "text_default"
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "role": self.role,
            "allowed_capabilities": list(self.allowed_capabilities),
            "read_scopes": list(self.read_scopes),
            "write_scopes": list(self.write_scopes),
            "can_delegate_to": list(self.can_delegate_to),
            "default_model_profile": self.default_model_profile,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentDefinition":
        d = d or {}
        return cls(
            id=d.get("id", ""), name=d.get("name", ""),
            description=d.get("description", ""), role=d.get("role", ""),
            allowed_capabilities=list(d.get("allowed_capabilities") or []),
            read_scopes=list(d.get("read_scopes") or []),
            write_scopes=list(d.get("write_scopes") or []),
            can_delegate_to=list(d.get("can_delegate_to") or []),
            default_model_profile=d.get("default_model_profile", "text_default"),
            enabled=d.get("enabled", True),
        )


# ============ CapabilityDefinition（§5）============
@dataclass
class CapabilityDefinition:
    """一个能力。owner_agent 主责；allowed_agents 可用此能力的 agent 列表。
    implemented=True 表示 v1 已接通 adapter；False 表示注册权限但预留（返回 not_implemented）。"""
    id: str
    name: str
    description: str
    category: str                            # CapabilityCategory 值
    owner_agent: str
    allowed_agents: List[str] = field(default_factory=list)
    operations: List[str] = field(default_factory=list)
    risk_level: str = RiskLevel.LOW.value
    implemented: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "category": self.category, "owner_agent": self.owner_agent,
            "allowed_agents": list(self.allowed_agents),
            "operations": list(self.operations),
            "risk_level": self.risk_level, "implemented": self.implemented,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CapabilityDefinition":
        d = d or {}
        return cls(
            id=d.get("id", ""), name=d.get("name", ""),
            description=d.get("description", ""),
            category=d.get("category", ""),
            owner_agent=d.get("owner_agent", ""),
            allowed_agents=list(d.get("allowed_agents") or []),
            operations=list(d.get("operations") or []),
            risk_level=d.get("risk_level", RiskLevel.LOW.value),
            implemented=d.get("implemented", False),
        )


# ============ ModelProfile（§13）============
@dataclass
class ModelProfile:
    """逻辑模型画像。只存逻辑模型 id / account alias；API Key 继续走环境配置（§14）。
    Image Model / Video Model ≠ Agent，只是 Agent 可用的工具模型。
    text_provider（2026-08-29 Agent 模型切换）：该档案的文本 provider（agnes/sensenova），
    runtime 据此为 Agent 选择文本客户端；None=agnes（默认，向后兼容旧持久化档案）。"""
    id: str
    name: str
    reasoning_model: Optional[str] = None
    fallback_reasoning_model: Optional[str] = None
    image_model: Optional[str] = None
    video_model: Optional[str] = None
    account_policy: str = "auto"            # auto / agnes_a / agnes_b
    text_provider: Optional[str] = None     # agnes（默认）/ sensenova

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name,
            "reasoning_model": self.reasoning_model,
            "fallback_reasoning_model": self.fallback_reasoning_model,
            "image_model": self.image_model, "video_model": self.video_model,
            "account_policy": self.account_policy,
            "text_provider": self.text_provider,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModelProfile":
        d = d or {}
        return cls(
            id=d.get("id", ""), name=d.get("name", ""),
            reasoning_model=d.get("reasoning_model"),
            fallback_reasoning_model=d.get("fallback_reasoning_model"),
            image_model=d.get("image_model"),
            video_model=d.get("video_model"),
            account_policy=d.get("account_policy", "auto"),
            text_provider=d.get("text_provider"),
        )


# ============ AgentMessage（§8 / AI-7D-R §10.1）============
@dataclass
class AgentMessage:
    """一条对话消息。status 承载 not_configured / not_implemented 等结构化状态（§21 软失败）。

    AI-7D D1：meta 承载 unified_turn 的结构化真相（intent/evidence_refs/events/
    linked_task_ids/linked_change_session_id）。None=普通聊天消息（向后兼容旧持久化）。
    UI 只许据 meta 的真实 backend 记录表示「已查看/正在查看」（D1.4：模型说"我应 inspect"
    ≠ inspect 发生；只有 meta.evidence_refs/events 存在才算真查看）。

    AI-7D-R §10.1：消息成为长存 Conversation 的一等记录——message_id 服务端生成，
    conversation_id/agent_id 标识归属，linked_* 把消息与真实工作对象关联（不再只塞 meta）。
    旧持久化消息（无这些字段）from_dict 时回填，向后兼容。"""
    role: str                                # user / assistant / system
    content: str
    status: Optional[str] = None             # None=正常；not_configured / not_implemented
    created_at: float = field(default_factory=time.time)
    meta: Optional[Dict[str, Any]] = None    # AI-7D D1：unified_turn 结构化真相
    # AI-7D-R §10.1：Conversation 一等字段
    message_id: Optional[str] = None
    conversation_id: Optional[str] = None
    agent_id: Optional[str] = None           # 消息归属的 agent（user 消息也记录当前对话 agent）
    linked_work_ids: List[str] = field(default_factory=list)
    linked_change_session_id: Optional[str] = None
    linked_execution_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content,
                "status": self.status, "created_at": self.created_at,
                "meta": self.meta,
                "message_id": self.message_id, "conversation_id": self.conversation_id,
                "agent_id": self.agent_id,
                "linked_work_ids": list(self.linked_work_ids),
                "linked_change_session_id": self.linked_change_session_id,
                "linked_execution_id": self.linked_execution_id}

    @classmethod
    def from_dict(cls, d: dict) -> "AgentMessage":
        d = d or {}
        meta = d.get("meta")
        # 向后兼容：旧消息的 linked_change_session_id 只在 meta 里 → 提到一等字段
        linked_cs = d.get("linked_change_session_id")
        if linked_cs is None and isinstance(meta, dict):
            linked_cs = meta.get("linked_change_session_id")
        return cls(role=d.get("role", "user"), content=d.get("content", ""),
                   status=d.get("status"), created_at=d.get("created_at", time.time()),
                   meta=meta,
                   message_id=d.get("message_id"),
                   conversation_id=d.get("conversation_id"),
                   agent_id=d.get("agent_id"),
                   linked_work_ids=list(d.get("linked_work_ids") or []),
                   linked_change_session_id=linked_cs,
                   linked_execution_id=d.get("linked_execution_id"))


# ============ AgentSession（§8/§9 / AI-7D-R §9 长存 Conversation）============
@dataclass
class AgentSession:
    """一个 Agent 的独立对话会话。

    AI-7D-R §9：Direct Conversation 长存于 (project, agent_id)，不再随页面 scope 切换而重建。
    conversation_id 标识该长存对话（同 project 同 agent 唯一）；scope_type/scope_id 保留为
    PROJECT 级（页面/选中实体上下文是 per-turn 元数据，不再是会话身份）。旧会话按 (agent,scope)
    键控，sessions.get_or_create_conversation 会迁移最近一条到长存 conversation。"""
    session_id: str
    project: str
    agent_id: str
    scope_type: str = ScopeType.PROJECT.value
    scope_id: str = ""
    messages: List[AgentMessage] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    conversation_id: Optional[str] = None    # AI-7D-R §9：长存对话标识

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id, "project": self.project,
            "agent_id": self.agent_id, "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at, "updated_at": self.updated_at,
            "conversation_id": self.conversation_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentSession":
        d = d or {}
        msgs = [AgentMessage.from_dict(m) for m in (d.get("messages") or [])]
        return cls(
            session_id=d.get("session_id", ""), project=d.get("project", ""),
            agent_id=d.get("agent_id", ""),
            scope_type=d.get("scope_type", ScopeType.PROJECT.value),
            scope_id=d.get("scope_id", ""),
            messages=msgs,
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            conversation_id=d.get("conversation_id"),
        )


# ============ AgentStructuredResult（§28）============
@dataclass
class AgentStructuredResult:
    """Specialist 返回给 Producer 的结构化结果（不只是自由文本，§28）。
    data 承载 demo payload（如 scene_id / issues，§11）。"""
    status: str = ResultStatus.COMPLETED.value
    summary: str = ""
    artifact_refs: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    suggested_next_actions: List[str] = field(default_factory=list)
    data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status, "summary": self.summary,
            "artifact_refs": list(self.artifact_refs),
            "warnings": list(self.warnings),
            "suggested_next_actions": list(self.suggested_next_actions),
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentStructuredResult":
        d = d or {}
        return cls(
            status=d.get("status", ResultStatus.COMPLETED.value),
            summary=d.get("summary", ""),
            artifact_refs=list(d.get("artifact_refs") or []),
            warnings=list(d.get("warnings") or []),
            suggested_next_actions=list(d.get("suggested_next_actions") or []),
            data=d.get("data"),
        )


# ============ AgentTask（§10/§11）============
@dataclass
class AgentTask:
    """AI 任务协调记录（≠ JobQueue 的图片/视频/Production 执行任务，§10）。
    Producer delegation 走正式 AgentTask，可追踪（§27）。

    AI-6F.0（§5）：execution_ref 是指向**已存在的 durable 执行**（JobQueue.Job /
    ProductionRun）的最小持久链接。语义要求：后端重启后，Runtime 能据此确定哪个**已存在**
    的 Job/Run 属于本 task——从而 reattach 而非重复 submit（§6 幂等）。
    形状：{"job_id", "kind", "phase", "shot_id", "submitted_at"}。
    这是「最小兼容扩展」——一个 optional dict 字段，**不**新增 ProductionRun 类型、
    **不**重写 AgentTask 持久化 schema（§3/§5/§21）。AgentTask=协调记录，Job/Run=执行记录，
    二者链接不复制（§11）。
    """
    task_id: str
    project: str
    requester_agent: str                     # 谁发起（v1 仅 producer）
    target_agent: str                        # 交给谁
    capability_id: str
    instruction: str = ""
    scope_type: str = ScopeType.PROJECT.value
    scope_id: str = ""
    session_id: str = ""
    operation: str = AgentOp.INSPECT.value
    parent_task_id: Optional[str] = None
    status: str = TaskStatus.PENDING.value
    result_summary: str = ""
    artifact_refs: List[Dict[str, Any]] = field(default_factory=list)
    structured_result: Optional[Dict[str, Any]] = None
    # AI-6F.0 §5：durable 执行链接。None=非长执行 task（inspect / production_plan.create
    # 等同步结算能力）；非 None=已提交 Job，settlement bridge 据 job_id 结算/reattach。
    execution_ref: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)
    suggested_next_actions: List[str] = field(default_factory=list)
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Phase 46：工作流运行关联（最小兼容扩展，同 execution_ref 先例）。None=非工作流派发。
    # 形状 {"runId","nodeId","attemptId","allowedDelegates"?}——R04 找回同任务、
    # 防止工作流重复派发的 correlation 身份。
    workflow_ref: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "project": self.project,
            "requester_agent": self.requester_agent,
            "target_agent": self.target_agent,
            "capability_id": self.capability_id,
            "instruction": self.instruction,
            "scope_type": self.scope_type, "scope_id": self.scope_id,
            "session_id": self.session_id, "operation": self.operation,
            "parent_task_id": self.parent_task_id, "status": self.status,
            "result_summary": self.result_summary,
            "artifact_refs": list(self.artifact_refs),
            "structured_result": self.structured_result,
            "execution_ref": self.execution_ref,
            "warnings": list(self.warnings),
            "suggested_next_actions": list(self.suggested_next_actions),
            "error": self.error,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "workflow_ref": self.workflow_ref,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentTask":
        d = d or {}
        return cls(
            task_id=d.get("task_id", ""), project=d.get("project", ""),
            requester_agent=d.get("requester_agent", ""),
            target_agent=d.get("target_agent", ""),
            capability_id=d.get("capability_id", ""),
            instruction=d.get("instruction", ""),
            scope_type=d.get("scope_type", ScopeType.PROJECT.value),
            scope_id=d.get("scope_id", ""),
            session_id=d.get("session_id", ""),
            operation=d.get("operation", AgentOp.INSPECT.value),
            parent_task_id=d.get("parent_task_id"),
            status=d.get("status", TaskStatus.PENDING.value),
            result_summary=d.get("result_summary", ""),
            artifact_refs=list(d.get("artifact_refs") or []),
            structured_result=d.get("structured_result"),
            execution_ref=d.get("execution_ref"),
            warnings=list(d.get("warnings") or []),
            suggested_next_actions=list(d.get("suggested_next_actions") or []),
            error=d.get("error"),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            workflow_ref=d.get("workflow_ref"),
        )
