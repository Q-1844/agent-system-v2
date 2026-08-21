# src/agents/change_protocol/execution_models.py
# AI-7C Change Execution 数据模型（§10/§11/§12/§14/§34/§40）。
#
# 风格对齐 models.py（stdlib dataclass + to_dict/from_dict + _filter）。
# 不变量（§4/§10）：
#   - The plan remains a proposal artifact; ChangeExecutionSession 是执行 artifact。
#   - 不在 execution model 内复制整个 Plan/Project/ProductionRun——存引用与稳定快照。
#   - 同一 approved plan_version → 至多一个 canonical creative commit（§16）。
#   - AI-7C-1 范围：ChangeExecutionSession + ExecutionStepRecord + 操作 digest + revalidation；
#     CanonicalCommitRecord/ProductionExecutionManifest 字段预留（AI-7C-3/4 填充），AI-7C-1 恒 None。
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ============ 常量（§11/§15/§34）============
# ChangeExecutionSession.status（§11：小而稳定）
EXEC_STATUSES = (
    "pending", "running", "waiting_user", "blocked",
    "failed", "cancelled", "completed",
)
EXEC_TERMINAL = ("failed", "cancelled", "completed")
EXEC_PRE_COMMIT = ("pending", "running", "waiting_user", "blocked")  # canonical 未提交前

# ChangeExecutionSession.stage（§11：细粒度，UI 据此 + step/run 推导进度）
EXEC_STAGES = (
    "revalidate", "workspace_create", "creative_replay", "workspace_verify",
    "canonical_commit", "character_sync", "production_planning",
    "production_prepare", "production", "compose", "done",
)

# production_mode（§15：v1 仅这两种；full_project_regeneration 不默认支持）
PRODUCTION_MODES = ("creative_only", "affected_production")

# ExecutionStepRecord.status
STEP_STATUSES = ("pending", "running", "applied", "skipped", "failed")

# CanonicalCommitRecord.status（§34）
COMMIT_STATUSES = ("pending", "commit_intent", "committed", "failed")

# AI-7C 失败码（§78，仅 execution-specific；既有 AgentError code 复用）
EXEC_ERROR_CODES = (
    "PLAN_NOT_APPROVED", "PLAN_VERSION_MISMATCH", "PLAN_EXECUTION_BINDING_MISSING",
    "PROJECT_CHANGED_AFTER_APPROVAL", "PROJECT_PRODUCTION_ACTIVE",
    "EXECUTION_PREVIEW_MISMATCH", "CANONICAL_COMMIT_CONFLICT",
    "CANONICAL_COMMIT_RECONCILIATION_CONFLICT", "CHARACTER_SYNC_FAILED",
    "CHARACTER_REFERENCE_NOT_READY", "PRODUCTION_PLAN_FAILED",
    "PRODUCTION_PLAN_INVALID", "PRODUCTION_SCOPE_EXPANDED",
    "PRODUCTION_SOURCE_CHANGED", "PRODUCTION_RUN_FAILED",
)

# execution_eligibility（§4：不把 applyable 变后门；用独立 eligibility 概念）
EXEC_ELIGIBILITY = (
    "eligible", "already_started", "already_completed", "stale", "blocked",
    "invalid_approval",
)


def _filter(d: dict, cls) -> dict:
    return {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}


# ============ ExecutionStepRecord（§12）============
@dataclass
class ExecutionStepRecord:
    """单个 plan step 的执行状态。绑定 step_id → operations（§13 traceability）。

    适配（§12/§13）：本仓库 ChangeOperation 无稳定 per-op ID（to_dict 形态），
    故用 operation_count + operation_digest 做确定性绑定（step_id → digest 可复算校验），
    等价于 step_id → operation_ids → operation_digest 的可验证性。"""
    step_id: str = ""
    module: str = ""
    owner_agent_id: str = ""
    operation_count: int = 0           # 该 step 绑定的 ChangeOperation 数
    operation_digest: str = ""        # §14：该 step 操作序列的确定性 digest
    status: str = "pending"            # STEP_STATUSES
    workspace_revision_before: Optional[int] = None
    workspace_revision_after: Optional[int] = None
    changeset_id: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[dict] = None

    def to_dict(self) -> dict:
        return {"step_id": self.step_id, "module": self.module,
                "owner_agent_id": self.owner_agent_id,
                "operation_count": self.operation_count,
                "operation_digest": self.operation_digest,
                "status": self.status,
                "workspace_revision_before": self.workspace_revision_before,
                "workspace_revision_after": self.workspace_revision_after,
                "changeset_id": self.changeset_id,
                "started_at": self.started_at, "completed_at": self.completed_at,
                "error": self.error}

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionStepRecord":
        return cls(**_filter(d, cls))


# ============ CanonicalCommitRecord（§34，AI-7C-3 填充）============
@dataclass
class CanonicalCommitRecord:
    """canonical 提交的 write-ahead 记录（§34）。AI-7C-1 恒 None（占位定义）。
    COMMIT_INTENT 在 canonical save 前持久；COMMIT_DONE 在 save 后。崩溃对账用之（§35）。"""
    status: str = "pending"            # COMMIT_STATUSES
    before_signature: str = ""
    expected_after_signature: str = ""
    operation_digest: str = ""
    commit_intent_at: Optional[float] = None
    committed_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {"status": self.status,
                "before_signature": self.before_signature,
                "expected_after_signature": self.expected_after_signature,
                "operation_digest": self.operation_digest,
                "commit_intent_at": self.commit_intent_at,
                "committed_at": self.committed_at}

    @classmethod
    def from_dict(cls, d: dict) -> "CanonicalCommitRecord":
        return cls(**_filter(d, cls))


# ============ Production Action 枚举（§40）============
# 值：keep | regenerate | generate_new | remove_from_timeline
PROD_ACTION = ("keep", "regenerate", "generate_new", "remove_from_timeline")


@dataclass
class ShotProductionActions:
    """单 Shot 的 production 动作（§40）。"""
    first_frame: str = "keep"
    audio: str = "keep"
    last_frame: str = "keep"
    video: str = "keep"

    def to_dict(self) -> dict:
        return {"first_frame": self.first_frame, "audio": self.audio,
                "last_frame": self.last_frame, "video": self.video}

    @classmethod
    def from_dict(cls, d: dict) -> "ShotProductionActions":
        return cls(**_filter(d, cls))


@dataclass
class ProductionExecutionManifest:
    """§40：production 执行清单。deterministic code 算（§41 不调 LLM）。从批准 plan 的 ImpactReport
    （preview.aggregate_impact，已含 frame/video regen shot ids）+ 批准 operations（character actions）派生。
    AI-7C-4 产出；AI-7C-5 填 production_plan_ids + baseline_asset_refs。"""
    execution_id: str = ""
    project_name: str = ""
    committed_source_signature: str = ""
    affected_character_ids: List[str] = field(default_factory=list)   # character name（本仓库无 character_id）
    affected_scene_ids: List[str] = field(default_factory=list)
    affected_shot_ids: List[str] = field(default_factory=list)
    character_actions: Dict[str, str] = field(default_factory=dict)   # name -> action
    shot_actions: Dict[str, dict] = field(default_factory=dict)       # shot_id -> ShotProductionActions.to_dict()
    planner_scene_ids: List[str] = field(default_factory=list)
    baseline_asset_refs: Dict[str, dict] = field(default_factory=dict)   # AI-7C-5 填
    expected_video_requests: int = 0
    production_plan_ids: Dict[str, str] = field(default_factory=dict)     # AI-7C-5 填
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {"execution_id": self.execution_id, "project_name": self.project_name,
                "committed_source_signature": self.committed_source_signature,
                "affected_character_ids": list(self.affected_character_ids),
                "affected_scene_ids": list(self.affected_scene_ids),
                "affected_shot_ids": list(self.affected_shot_ids),
                "character_actions": dict(self.character_actions),
                "shot_actions": dict(self.shot_actions),
                "planner_scene_ids": list(self.planner_scene_ids),
                "baseline_asset_refs": dict(self.baseline_asset_refs),
                "expected_video_requests": self.expected_video_requests,
                "production_plan_ids": dict(self.production_plan_ids),
                "created_at": self.created_at}

    @classmethod
    def from_dict(cls, d: dict) -> "ProductionExecutionManifest":
        return cls(**_filter(d, cls))


# ============ ChangeExecutionSession（§10）============
@dataclass
class ChangeExecutionSession:
    """一个已批准 plan 的执行 artifact（§10）。durable，跨重启恢复（§73）。

    AI-7C-1 范围：revalidation + 创建 + 幂等 + 持久化 + startup discovery。
    stage 在 AI-7C-1 到 workspace_create 即停（AI-7C-2 接力建 workspace + replay）。
    canonical_commit/character_sync/planning/production_* 字段预留，AI-7C-1 恒 None/空。"""
    schema_version: int = 1
    execution_id: str = ""
    project_name: str = ""

    change_session_id: str = ""
    plan_id: str = ""
    plan_version: int = 0
    approval_id: str = ""             # 派生：appr_{plan_id}_v{version}（PlanApproval 无独立 id）
    source_signature: str = ""        # approved source signature（approval.source_snapshot_signature）
    preview_artifact_id: str = ""

    execution_mode: str = "approved_plan_execution"
    production_mode: str = "affected_production"

    status: str = "pending"            # EXEC_STATUSES
    stage: str = "revalidate"         # EXEC_STAGES

    execution_workspace_id: Optional[str] = None
    operation_digest: str = ""        # §14：全量批准操作序列 digest
    steps: List[dict] = field(default_factory=list)   # ExecutionStepRecord.to_dict()

    canonical_commit: Optional[dict] = None           # CanonicalCommitRecord.to_dict()

    character_sync: List[dict] = field(default_factory=list)   # AI-7C-4
    planning: List[dict] = field(default_factory=list)         # AI-7C-5

    production_manifest_file: Optional[str] = None    # AI-7C-4
    production_run_id: Optional[str] = None           # AI-7C-5

    blocker: Optional[dict] = None
    error: Optional[dict] = None
    cancel_requested: bool = False
    production_skipped_by_user: bool = False         # §66 creative_only

    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version,
                "execution_id": self.execution_id, "project_name": self.project_name,
                "change_session_id": self.change_session_id,
                "plan_id": self.plan_id, "plan_version": self.plan_version,
                "approval_id": self.approval_id, "source_signature": self.source_signature,
                "preview_artifact_id": self.preview_artifact_id,
                "execution_mode": self.execution_mode,
                "production_mode": self.production_mode,
                "status": self.status, "stage": self.stage,
                "execution_workspace_id": self.execution_workspace_id,
                "operation_digest": self.operation_digest,
                "steps": list(self.steps),
                "canonical_commit": self.canonical_commit,
                "character_sync": list(self.character_sync),
                "planning": list(self.planning),
                "production_manifest_file": self.production_manifest_file,
                "production_run_id": self.production_run_id,
                "blocker": self.blocker, "error": self.error,
                "cancel_requested": self.cancel_requested,
                "production_skipped_by_user": self.production_skipped_by_user,
                "created_at": self.created_at, "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, d: dict) -> "ChangeExecutionSession":
        return cls(**_filter(d, cls))
