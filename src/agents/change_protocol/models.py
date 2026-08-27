# src/agents/change_protocol/models.py
# AI-7B Change Protocol 数据模型（§10/§11/§13/§14/§35/§36/§37/§38/§39/§65/§66/§75）。
#
# 风格对齐 src/revision/models.py + src/agents/models.py（stdlib dataclass + to_dict/from_dict
# + 字段过滤）。嵌套复杂对象存为 dict（对齐 WorkspaceChangeSet.impact_report: Optional[dict]），
# 由 service 按需用对应 dataclass.from_dict 重建。
#
# 不变量：
#   - ChangeSession.schema_version=2（AI-7B）；legacy AI-7A 会话（无 schema_version）经
#     migrations.normalize_session_dict 标 legacy_analysis_only=True，绝不静默重判为可执行（§9）。
#   - PlanPreviewArtifact.applyable 恒 False（§65）；无任何 API 可改。
#   - ModuleProposalItem.item_id / ChangePlanItem.plan_item_id 等稳定 ID 由服务端生成，LLM 不发明（§14/§50）。
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ============ 枚举常量（字符串字面量，避免 import cycle）============
# ChangeSession.status（§11）
# AI-7D-R：新增 no_action（终态）——请求经分析确认无可执行改动时的诚实结果，
# 不再伪造 A/B 方案（§10）。settled 状态对后续新变更“不可见”，新请求一律新建会话（§7/§8）。
SESSION_STATUSES = (
    "routing", "consulting", "analyzing_dependencies", "resolving_conflicts",
    "awaiting_user_decision", "synthesizing_plan", "validating_plan",
    "building_preview", "ready_for_review", "approved_for_execution",
    "rejected", "stale", "blocked", "failed", "cancelled", "no_action",
)
SESSION_TERMINAL = ("approved_for_execution", "rejected", "stale", "failed",
                    "cancelled", "no_action")
# AI-7D-R §7：已“落定”的会话——后续新变更不得并入，必须基于最新 canonical 新建会话。
# blocked 也算：它是不可继续的僵尸态，新请求应开新会话而非灌进旧会话。
SESSION_SETTLED = ("approved_for_execution", "rejected", "stale", "failed",
                   "cancelled", "no_action", "blocked")

# UnifiedChangePlan.status（§36）
PLAN_STATUSES = (
    "draft", "needs_user_decision", "validated", "preview_ready",
    "approved_for_execution", "rejected", "stale", "superseded",
)

# ModuleProposal.status（§13）
PROPOSAL_STATUSES = ("pending", "running", "completed", "failed", "superseded")

SCHEMA_VERSION_CURRENT = 2
SCHEMA_VERSION_LEGACY = 1


def _filter(d: dict, cls) -> dict:
    return {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}


# ============ ChangeRequest（§10）============
@dataclass
class ChangeRequest:
    """用户变更请求（proposal_only）。original_user_request 不可变（§10/§105）。"""
    request_id: str = ""
    project_name: str = ""
    source: str = "team_chat"            # team_chat | direct_agent_chat
    source_agent_id: Optional[str] = None
    source_session_id: Optional[str] = None
    source_message_id: Optional[str] = None
    # AI-7D D2.5：客户端幂等键（Direct Chat 双击/retry/F5 不造重复 ChangeSession）。
    # create_session 据此查重：同 client_request_id 已存在则返回既有 session。
    client_request_id: Optional[str] = None
    original_user_request: str = ""
    primary_module: Optional[str] = None
    suspected_modules: List[str] = field(default_factory=list)
    entity_refs: List[dict] = field(default_factory=list)      # ProjectEvidenceRef.to_dict()
    source_snapshot_signature: str = ""
    mode: str = "proposal_only"
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {"request_id": self.request_id, "project_name": self.project_name,
                "source": self.source, "source_agent_id": self.source_agent_id,
                "source_session_id": self.source_session_id,
                "source_message_id": self.source_message_id,
                "client_request_id": self.client_request_id,
                "original_user_request": self.original_user_request,
                "primary_module": self.primary_module,
                "suspected_modules": list(self.suspected_modules),
                "entity_refs": list(self.entity_refs),
                "source_snapshot_signature": self.source_snapshot_signature,
                "mode": self.mode, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, d: dict) -> "ChangeRequest":
        return cls(**_filter(d, cls))


# ============ ChangeSession（§11）============
@dataclass
class ChangeSession:
    """一次协调的跨模块修改讨论 = 一个持久 ChangeSession（§11）。schema_version=2。
    legacy_analysis_only=True 表示来自 AI-7A 的历史只读分析会话，不可执行（§9）。"""
    session_id: str = ""
    project_name: str = ""
    request_id: str = ""
    status: str = "routing"
    source_snapshot_signature: str = ""
    consultation_round: int = 0
    module_proposal_ids: List[str] = field(default_factory=list)
    impact_fact_ids: List[str] = field(default_factory=list)
    conflict_ids: List[str] = field(default_factory=list)
    active_plan_id: Optional[str] = None
    plan_version: int = 0
    pending_decision_ids: List[str] = field(default_factory=list)
    error: Optional[str] = None
    schema_version: int = SCHEMA_VERSION_CURRENT
    legacy_analysis_only: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    # AI-7D-R §11：用户对当前方案的最新反馈（真实传给重跑的 agent，不再塞进名不副实的
    # user_feedback_message_id 字段、也不被 original_user_request 不可变挡掉）。
    latest_feedback: Optional[str] = None
    # AI-7D-R §10：no_action 终态的自然语言原因（给用户/前端展示）。
    no_action_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {"session_id": self.session_id, "project_name": self.project_name,
                "request_id": self.request_id, "status": self.status,
                "source_snapshot_signature": self.source_snapshot_signature,
                "consultation_round": self.consultation_round,
                "module_proposal_ids": list(self.module_proposal_ids),
                "impact_fact_ids": list(self.impact_fact_ids),
                "conflict_ids": list(self.conflict_ids),
                "active_plan_id": self.active_plan_id,
                "plan_version": self.plan_version,
                "pending_decision_ids": list(self.pending_decision_ids),
                "error": self.error, "schema_version": self.schema_version,
                "legacy_analysis_only": self.legacy_analysis_only,
                "created_at": self.created_at, "updated_at": self.updated_at,
                "latest_feedback": self.latest_feedback,
                "no_action_reason": self.no_action_reason}

    @classmethod
    def from_dict(cls, d: dict) -> "ChangeSession":
        return cls(**_filter(d, cls))


# ============ ModuleProposal（§13） + ModuleProposalItem（§14）============
@dataclass
class ModuleProposalItem:
    """一个语义修改意图。item_id 服务端生成（§14）。intent 是语义 dict
    （如 update_scene/insert_scene/update_character），不含 before/revision/ChangeOperation。"""
    item_id: str = ""
    module: str = ""                     # script | character | storyboard
    action: str = ""                     # 语义动作（update_story_text/update_scene/...）
    target_refs: List[dict] = field(default_factory=list)
    intent: dict = field(default_factory=dict)
    reason: str = ""
    source: str = "user_requested"      # user_requested | downstream_impact
    required: bool = True
    depends_on_item_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"item_id": self.item_id, "module": self.module, "action": self.action,
                "target_refs": list(self.target_refs), "intent": dict(self.intent),
                "reason": self.reason, "source": self.source, "required": self.required,
                "depends_on_item_ids": list(self.depends_on_item_ids)}

    @classmethod
    def from_dict(cls, d: dict) -> "ModuleProposalItem":
        return cls(**_filter(d, cls))


@dataclass
class ModuleProposal:
    """一个模块 Owner 的结构化专业提案（§13）。durable artifact（不靠 raw prose）。"""
    proposal_id: str = ""
    change_session_id: str = ""
    module: str = ""
    owner_agent_id: str = ""
    round: int = 0
    status: str = "pending"
    summary: str = ""
    items: List[dict] = field(default_factory=list)        # ModuleProposalItem.to_dict()
    dependency_notes: List[dict] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    user_decisions_needed: List[str] = field(default_factory=list)
    evidence_refs: List[dict] = field(default_factory=list)
    source_snapshot_signature: str = ""
    agent_task_id: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {"proposal_id": self.proposal_id,
                "change_session_id": self.change_session_id, "module": self.module,
                "owner_agent_id": self.owner_agent_id, "round": self.round,
                "status": self.status, "summary": self.summary,
                "items": list(self.items), "dependency_notes": list(self.dependency_notes),
                "blockers": list(self.blockers),
                "user_decisions_needed": list(self.user_decisions_needed),
                "evidence_refs": list(self.evidence_refs),
                "source_snapshot_signature": self.source_snapshot_signature,
                "agent_task_id": self.agent_task_id,
                "created_at": self.created_at, "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, d: dict) -> "ModuleProposal":
        return cls(**_filter(d, cls))


# ============ ChangeDecisionRequest（§35） + ChangeDecisionOption ============
@dataclass
class ChangeDecisionOption:
    option_id: str = ""
    label: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {"option_id": self.option_id, "label": self.label,
                "description": self.description}

    @classmethod
    def from_dict(cls, d: dict) -> "ChangeDecisionOption":
        return cls(**_filter(d, cls))


@dataclass
class ChangeDecisionRequest:
    """需用户决策的歧义（§35）。blocking=True 必须在 preview_ready 前回答（§47）。"""
    decision_id: str = ""
    change_session_id: str = ""
    question: str = ""
    options: List[dict] = field(default_factory=list)      # ChangeDecisionOption.to_dict()
    blocking: bool = True
    status: str = "pending"             # pending | answered | cancelled
    selected_option_id: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {"decision_id": self.decision_id,
                "change_session_id": self.change_session_id,
                "question": self.question, "options": list(self.options),
                "blocking": self.blocking, "status": self.status,
                "selected_option_id": self.selected_option_id,
                "created_at": self.created_at, "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, d: dict) -> "ChangeDecisionRequest":
        return cls(**_filter(d, cls))


# ============ UnifiedChangePlan（§36） + 子结构 ============
@dataclass
class ChangePlanItem:
    """计划项引用专业提案内容（§37）。Producer 不重写专业内容，只引用 proposal_item_ids。"""
    plan_item_id: str = ""
    module: str = ""
    owner_agent_id: str = ""
    proposal_item_ids: List[str] = field(default_factory=list)
    scope_refs: List[dict] = field(default_factory=list)
    summary: str = ""
    reason: str = ""
    source: str = "user_requested"      # user_requested | downstream_impact
    required: bool = True

    def to_dict(self) -> dict:
        return {"plan_item_id": self.plan_item_id, "module": self.module,
                "owner_agent_id": self.owner_agent_id,
                "proposal_item_ids": list(self.proposal_item_ids),
                "scope_refs": list(self.scope_refs), "summary": self.summary,
                "reason": self.reason, "source": self.source, "required": self.required}

    @classmethod
    def from_dict(cls, d: dict) -> "ChangePlanItem":
        return cls(**_filter(d, cls))


@dataclass
class ChangePlanStep:
    """执行顺序（§38）。依赖 DAG 节点；execution 本身在 AI-7C，此处仅元数据。"""
    step_id: str = ""
    module: str = ""
    owner_agent_id: str = ""
    plan_item_ids: List[str] = field(default_factory=list)
    depends_on_step_ids: List[str] = field(default_factory=list)
    parallel_group: Optional[str] = None

    def to_dict(self) -> dict:
        return {"step_id": self.step_id, "module": self.module,
                "owner_agent_id": self.owner_agent_id,
                "plan_item_ids": list(self.plan_item_ids),
                "depends_on_step_ids": list(self.depends_on_step_ids),
                "parallel_group": self.parallel_group}

    @classmethod
    def from_dict(cls, d: dict) -> "ChangePlanStep":
        return cls(**_filter(d, cls))


@dataclass
class PlanDependencyEdge:
    edge_id: str = ""
    from_step_id: str = ""
    to_step_id: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {"edge_id": self.edge_id, "from_step_id": self.from_step_id,
                "to_step_id": self.to_step_id, "reason": self.reason}

    @classmethod
    def from_dict(cls, d: dict) -> "PlanDependencyEdge":
        return cls(**_filter(d, cls))


@dataclass
class PlanValidationIssue:
    code: str = ""
    message: str = ""
    severity: str = "error"              # error | warning
    refs: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                "severity": self.severity, "refs": list(self.refs)}

    @classmethod
    def from_dict(cls, d: dict) -> "PlanValidationIssue":
        return cls(**_filter(d, cls))


@dataclass
class PlanApproval:
    """批准绑定 plan_version + source_snapshot_signature（§75/§90）。"""
    plan_id: str = ""
    plan_version: int = 0
    decision: str = ""                  # approved | rejected
    source_snapshot_signature: str = ""
    decided_at: float = 0.0
    user_feedback_message_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {"plan_id": self.plan_id, "plan_version": self.plan_version,
                "decision": self.decision,
                "source_snapshot_signature": self.source_snapshot_signature,
                "decided_at": self.decided_at,
                "user_feedback_message_id": self.user_feedback_message_id}

    @classmethod
    def from_dict(cls, d: dict) -> "PlanApproval":
        return cls(**_filter(d, cls))


@dataclass
class UnifiedChangePlan:
    """Producer 编译的统一变更计划（§36）。preview_ready 后不可变；用户反馈产 version+1（§79）。"""
    plan_id: str = ""
    change_session_id: str = ""
    project_name: str = ""
    version: int = 1
    source_snapshot_signature: str = ""
    status: str = "draft"
    direct_change_items: List[dict] = field(default_factory=list)   # ChangePlanItem
    downstream_change_items: List[dict] = field(default_factory=list)
    unchanged_scope: List[dict] = field(default_factory=list)
    steps: List[dict] = field(default_factory=list)                  # ChangePlanStep
    dependency_edges: List[dict] = field(default_factory=list)       # PlanDependencyEdge
    user_decision_ids: List[str] = field(default_factory=list)
    module_proposal_ids: List[str] = field(default_factory=list)
    validation_issues: List[dict] = field(default_factory=list)      # PlanValidationIssue
    preview_id: Optional[str] = None
    approval: Optional[dict] = None                                  # PlanApproval.to_dict()
    parent_plan_id: Optional[str] = None                              # §78 版本链
    user_feedback_message_id: Optional[str] = None
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {"plan_id": self.plan_id, "change_session_id": self.change_session_id,
                "project_name": self.project_name, "version": self.version,
                "source_snapshot_signature": self.source_snapshot_signature,
                "status": self.status,
                "direct_change_items": list(self.direct_change_items),
                "downstream_change_items": list(self.downstream_change_items),
                "unchanged_scope": list(self.unchanged_scope),
                "steps": list(self.steps),
                "dependency_edges": list(self.dependency_edges),
                "user_decision_ids": list(self.user_decision_ids),
                "module_proposal_ids": list(self.module_proposal_ids),
                "validation_issues": list(self.validation_issues),
                "preview_id": self.preview_id, "approval": self.approval,
                "parent_plan_id": self.parent_plan_id,
                "user_feedback_message_id": self.user_feedback_message_id,
                "created_at": self.created_at}

    @classmethod
    def from_dict(cls, d: dict) -> "UnifiedChangePlan":
        return cls(**_filter(d, cls))


# ============ PlanPreviewArtifact（§65） + ModuleOperationBundle（§66）============
@dataclass
class ModuleOperationBundle:
    """保留 owner traceability：哪个 Agent 的哪些 proposal item 编译成了哪些 op（§66）。"""
    step_id: str = ""
    module: str = ""
    owner_agent_id: str = ""
    proposal_item_ids: List[str] = field(default_factory=list)
    operations: List[dict] = field(default_factory=list)     # ChangeOperation.to_dict()

    def to_dict(self) -> dict:
        return {"step_id": self.step_id, "module": self.module,
                "owner_agent_id": self.owner_agent_id,
                "proposal_item_ids": list(self.proposal_item_ids),
                "operations": list(self.operations)}

    @classmethod
    def from_dict(cls, d: dict) -> "ModuleOperationBundle":
        return cls(**_filter(d, cls))


@dataclass
class PlanPreviewArtifact:
    """AI-7B 预览产物（§65）。applyable 恒 False——无任何 API 可改（§76/§119/L）。"""
    preview_id: str = ""
    change_session_id: str = ""
    plan_id: str = ""
    plan_version: int = 0
    source_snapshot_signature: str = ""
    module_operations: List[dict] = field(default_factory=list)   # ModuleOperationBundle
    aggregate_diff: dict = field(default_factory=dict)
    aggregate_impact: dict = field(default_factory=dict)
    warnings: List[dict] = field(default_factory=list)
    projected_signature: str = ""
    status: str = "preview_only"
    applyable: bool = False            # §65：恒 False
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {"preview_id": self.preview_id,
                "change_session_id": self.change_session_id, "plan_id": self.plan_id,
                "plan_version": self.plan_version,
                "source_snapshot_signature": self.source_snapshot_signature,
                "module_operations": list(self.module_operations),
                "aggregate_diff": dict(self.aggregate_diff),
                "aggregate_impact": dict(self.aggregate_impact),
                "warnings": list(self.warnings),
                "projected_signature": self.projected_signature,
                "status": self.status, "applyable": self.applyable,
                "created_at": self.created_at}

    @classmethod
    def from_dict(cls, d: dict) -> "PlanPreviewArtifact":
        d = dict(d or {})
        # §65 硬不变量：强制 applyable=False，忽略任何外部写入
        d["applyable"] = False
        return cls(**_filter(d, cls))
