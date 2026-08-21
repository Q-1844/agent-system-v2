# src/agents/change_protocol/execution_service.py
# AI-7C ChangeExecutionSession 编排服务（§7/§10/§13/§14/§15/§16/§17/§18/§73/§96）。
#
# 职责：execute_plan（创建 + 幂等 + revalidation + 操作绑定/digest + 持久化 + stale 阻断）、
# get/list execution、execution_eligibility（§4）、recover_executions（§73 startup discovery）。
#
# 硬不变量（§96 Gate）：AI-7C-1 绝不 Workspace Apply / 不写 canonical / 不改 Production Plan /
# 不触发生成。execute 只：重载已批准 artifact → 全量 revalidation → 绑定操作 → 比 source 签名 →
# 持久化 execution（pending 或 blocked）→ STOP。revalidation 与 stale 失败均 0 mutation。
#
# 复用 service.py 的共享 helper（_store/_current_signature/_load_session/_guard_legacy/_new_id/_now），
# 不在 AgentRuntime 上挂方法（对齐 AI-7A/7B composition-root 模式）。
from __future__ import annotations
import logging
from typing import List, Optional

from src.revision.models import _canonical_json

from ..runtime import AgentRuntime, AgentError
from .modes import AgentExecutionMode
from .models import (UnifiedChangePlan, PlanApproval, ModuleProposal)  # noqa: F401 (ModuleProposal 留作 AI-7C-4 扩展点)
from .execution_models import (ChangeExecutionSession, ExecutionStepRecord,
                               EXEC_STATUSES, EXEC_TERMINAL, PRODUCTION_MODES,
                               EXEC_ELIGIBILITY)  # noqa: F401 (EXEC_ELIGIBILITY 留作 §4 扩展点)
from .store import ChangeProtocolStore
from .service import (_store, _current_signature, _load_session, _guard_legacy,
                      _new_id, _now)

import hashlib

logger = logging.getLogger(__name__)


# ============ 内部 helper ============
def _approval_id(plan_id: str, plan_version: int) -> str:
    """PlanApproval 无独立 id 字段（§5：code is authority）。派生稳定 id 做幂等键/可追溯。
    approval 与 plan_version 1:1（§16），故 (plan_id, plan_version) 唯一标识批准。"""
    return f"appr_{plan_id}_v{plan_version}"


def _ops_digest(ops: list) -> str:
    """§14：单组操作序列的确定性 digest。ChangeOperation.to_dict() 已归一（无时间戳、
    跳过 None 字段）；_canonical_json sort_keys 保证键序稳定。不依赖 LLM。"""
    return hashlib.sha256(_canonical_json(list(ops or [])).encode("utf-8")).hexdigest()


def _overall_operation_digest(module_operations: list) -> str:
    """§14：全量批准操作序列 digest（按 step 顺序展平）。证明 执行操作 == 预览操作。"""
    flat = []
    for bundle in (module_operations or []):
        for op in (bundle.get("operations") or []):
            flat.append(op)
    return _ops_digest(flat)


def _bind_step_records(plan: UnifiedChangePlan, preview) -> List[dict]:
    """§12/§13：step_id → operations 确定性绑定。复用 PlanPreviewArtifact.module_operations
    （ModuleOperationBundle，已含 step_id+proposal_item_ids+operations）——绑定层已存在，不重建。
    返回 ExecutionStepRecord.to_dict() 列表。"""
    bundles_by_step = {b.get("step_id"): b for b in (preview.module_operations or [])}
    out = []
    for step in (plan.steps or []):
        sid = step.get("step_id", "")
        bundle = bundles_by_step.get(sid)
        ops = (bundle.get("operations") or []) if bundle else []
        out.append(ExecutionStepRecord(
            step_id=sid, module=step.get("module", ""),
            owner_agent_id=step.get("owner_agent_id", ""),
            operation_count=len(ops), operation_digest=_ops_digest(ops),
            status="pending").to_dict())
    return out


def _collect_proposal_item_ids(plan: UnifiedChangePlan, preview) -> set:
    """收集 plan items + bundle 引用的所有 proposal_item_id（§17：referenced items 仍存在）。"""
    ids = set()
    for cpi in (plan.direct_change_items or []) + (plan.downstream_change_items or []):
        for pid in (cpi.get("proposal_item_ids") or []):
            ids.add(pid)
    for bundle in (preview.module_operations or []):
        for pid in (bundle.get("proposal_item_ids") or []):
            ids.add(pid)
    return ids


def _existing_proposal_item_ids(store: ChangeProtocolStore, session_id: str) -> set:
    ids = set()
    for prop in store.list_proposals(session_id):
        for it in (prop.items or []):
            iid = it.get("item_id")
            if iid:
                ids.add(iid)
    return ids


# ============ revalidation（§17）============
def _revalidate_for_execution(store: ChangeProtocolStore, session,
                              session_id: str, plan_id: str,
                              plan_version: int):
    """§17：reload + 全量校验已批准 artifact。任何 precondition 失败 -> AgentError（0 mutation）。
    返回 (plan, approval, preview)。stale（项目批准后变化）的判定在 execute_plan 做（-> blocked）。"""
    # 1. plan 存在 + 属该 session
    plan = store.get_plan(plan_id)
    if plan is None:
        raise AgentError("PLAN_NOT_APPROVED", f"plan 不存在: {plan_id}")
    if plan.change_session_id != session_id:
        raise AgentError("PLAN_OWNER_MISMATCH", "plan 不属于该 change-session")
    # 2. active + 精确 version
    if session.active_plan_id != plan_id:
        raise AgentError("PLAN_VERSION_MISMATCH", "非当前 active plan")
    if plan.version != plan_version:
        raise AgentError("PLAN_VERSION_MISMATCH",
                         f"plan_version={plan.version} ≠ 请求 {plan_version}")
    # 3. 已批准（session + plan 双重）
    if session.status != "approved_for_execution" or plan.status != "approved_for_execution":
        raise AgentError("PLAN_NOT_APPROVED",
                         f"plan/session 未批准（session={session.status}, plan={plan.status}）")
    # 4. PlanApproval 存在 + 属该精确 version + decision=approved
    ap_d = plan.approval
    if not ap_d:
        raise AgentError("PLAN_NOT_APPROVED", "plan 无 approval 记录")
    approval = PlanApproval.from_dict(ap_d)
    if approval.decision != "approved":
        raise AgentError("PLAN_NOT_APPROVED", f"approval.decision={approval.decision}")
    if approval.plan_version != plan.version:
        raise AgentError("PLAN_VERSION_MISMATCH",
                         f"approval.plan_version={approval.plan_version} ≠ {plan.version}")
    # 5. preview artifact 存在 + applyable 恒 False + 属该 version
    if not plan.preview_id:
        raise AgentError("PLAN_PREVIEW_FAILED", "plan 无 preview")
    preview = store.get_preview(plan.preview_id)
    if preview is None:
        raise AgentError("PLAN_PREVIEW_FAILED", "preview artifact 不存在")
    if preview.applyable is not False:   # §65 硬不变量防御
        raise AgentError("PLAN_PREVIEW_FAILED", "preview applyable 非 False")
    if preview.plan_version != plan.version:
        raise AgentError("PLAN_VERSION_MISMATCH",
                         f"preview.plan_version={preview.plan_version} ≠ {plan.version}")
    # 6. PlanValidator 仍 PASS（无 error-severity issue）
    err_issues = [i for i in (plan.validation_issues or [])
                  if i.get("severity", "error") == "error"]
    if err_issues:
        raise AgentError("PLAN_VALIDATION_FAILED",
                         f"plan 存在 {len(err_issues)} 个校验错误")
    # 7. 0 blocking user decision（§17/§47）
    blocking = [d for d in store.list_decisions(session_id)
                if d.blocking and d.status == "pending"]
    if blocking:
        raise AgentError("PLAN_USER_DECISION_REQUIRED",
                         f"{len(blocking)} 个 blocking decision 未回答")
    # 8. operation binding 完整（§13/§17）：每个 plan step 都有对应 bundle
    bundles_by_step = {b.get("step_id") for b in (preview.module_operations or [])}
    missing_steps = [s.get("step_id", "") for s in (plan.steps or [])
                     if s.get("step_id", "") not in bundles_by_step]
    if missing_steps:
        raise AgentError("PLAN_EXECUTION_BINDING_MISSING",
                         f"{len(missing_steps)} 个 step 无 operation bundle: {missing_steps}")
    # 9. preview artifact 完整性（§17）：projected_signature 存在
    if not preview.projected_signature:
        raise AgentError("PLAN_PREVIEW_FAILED", "preview 无 projected_signature")
    # 10. 所有 referenced proposal item 仍存在（§17）
    referenced = _collect_proposal_item_ids(plan, preview)
    if referenced:
        existing = _existing_proposal_item_ids(store, session_id)
        missing = referenced - existing
        if missing:
            raise AgentError("PLAN_EXECUTION_BINDING_MISSING",
                             f"{len(missing)} 个 proposal item 已不存在: {sorted(missing)}")
    return plan, approval, preview


# ============ execute（§7/§15/§16/§17/§18/§96）============
def execute_plan(rt: AgentRuntime, project: str, session_id: str,
                 plan_id: str, plan_version: int,
                 production_mode: str = "affected_production") -> dict:
    """启动已批准 plan 的执行。AI-7C-1：创建 ChangeExecutionSession + revalidation + 绑定/digest
    + stale 阻断 + 持久化 -> STOP。0 Workspace Apply / 0 canonical mutation / 0 Production / 0 Agnes。

    幂等（§15/§16）：同一 (plan_id, plan_version) 已有 execution -> 返回之，不新建。
    stale（§18）：项目批准后变化 -> 创建 execution 但置 blocked（§81：不可 rebasing resume），
    canonical 不动。"""
    if production_mode not in PRODUCTION_MODES:
        raise AgentError("PLAN_NOT_APPROVED",
                         f"production_mode={production_mode!r} 不支持（仅 {list(PRODUCTION_MODES)}）")
    store = _store(rt, project)
    session = _load_session(rt, project, session_id)
    _guard_legacy(session)

    # 幂等（§15）：已存在 execution -> 直接返回
    existing = store.find_execution(plan_id, plan_version)
    if existing is not None:
        return {"execution": existing.to_dict(), "idempotent": True,
                "session_id": session_id, "plan_id": plan_id,
                "plan_version": plan_version}

    # §17 revalidation：全量校验已批准 artifact（失败 -> AgentError，0 mutation）
    plan, approval, preview = _revalidate_for_execution(
        store, session, session_id, plan_id, plan_version)

    # §14 操作 digest + §12/§13 step 绑定
    overall_digest = _overall_operation_digest(preview.module_operations)
    step_records = _bind_step_records(plan, preview)

    now = _now()
    execution = ChangeExecutionSession(
        schema_version=1, execution_id=_new_id("exec"), project_name=project,
        change_session_id=session_id, plan_id=plan_id, plan_version=plan_version,
        approval_id=_approval_id(plan_id, plan_version),
        source_signature=approval.source_snapshot_signature,
        preview_artifact_id=preview.preview_id,
        execution_mode=AgentExecutionMode.APPROVED_PLAN_EXECUTION.value,
        production_mode=production_mode,
        status="pending", stage="workspace_create",
        operation_digest=overall_digest, steps=step_records,
        created_at=now, updated_at=now)

    # §17/§18/§19 stale 检查：当前 creative source signature vs 批准时 source signature。
    # 用 creative_source_signature（排除生成资产，§19：视频完成不必然使创作 plan 失效）。
    cur_sig = _current_signature(rt, project)
    if approval.source_snapshot_signature and cur_sig != approval.source_snapshot_signature:
        # §18：不 auto-rebase、不重算 preview、不改 before-values。stale plan -> blocked。
        execution.status = "blocked"
        execution.stage = "revalidate"
        execution.blocker = {"code": "PROJECT_CHANGED_AFTER_APPROVAL",
                             "current_signature": cur_sig,
                             "approved_signature": approval.source_snapshot_signature,
                             "message": "项目在批准后已变化，需重新提案/审批（§18）"}
        store.save_execution(execution)
        return {"execution": execution.to_dict(), "blocked": True,
                "idempotent": False, "session_id": session_id,
                "plan_id": plan_id, "plan_version": plan_version,
                "blocker_code": "PROJECT_CHANGED_AFTER_APPROVAL"}

    # revalidation 通过。stage=workspace_create，等待 AI-7C-2 接力建 workspace + replay。
    # AI-7C-1 到此 STOP：不建 workspace、不 apply、不 commit。
    store.save_execution(execution)
    return {"execution": execution.to_dict(), "blocked": False,
            "idempotent": False, "session_id": session_id,
            "plan_id": plan_id, "plan_version": plan_version,
            "status": "pending", "stage": "workspace_create"}


# ============ 查询 ============
def get_execution(rt: AgentRuntime, project: str, session_id: str,
                  execution_id: str) -> Optional[dict]:
    store = _store(rt, project)
    ex = store.get_execution(execution_id)
    if ex is None:
        return None
    if ex.change_session_id != session_id:
        return None   # §91 项目/session 隔离
    return ex.to_dict()


def list_executions(rt: AgentRuntime, project: str, session_id: str) -> dict:
    return {"executions": [e.to_dict() for e in _store(rt, project).list_executions(session_id)]}


def execution_eligibility(rt: AgentRuntime, project: str, session_id: str,
                          plan_id: str, plan_version: int) -> dict:
    """§4：独立 eligibility 概念（不翻 applyable 后门）。只读，0 mutation。供 UI 决定 Execute 按钮态。
    返回 {eligibility, execution?, reason?}。"""
    store = _store(rt, project)
    session = store.get_session(session_id)
    if session is None:
        return {"eligibility": "invalid_approval", "reason": "change-session 不存在"}
    # 已有 execution -> 据 status 判
    existing = store.find_execution(plan_id, plan_version)
    if existing is not None:
        if existing.status == "completed":
            return {"eligibility": "already_completed", "execution": existing.to_dict()}
        if existing.status == "blocked":
            return {"eligibility": "blocked", "execution": existing.to_dict(),
                    "blocker_code": (existing.blocker or {}).get("code")}
        if existing.status in ("failed", "cancelled"):
            return {"eligibility": "blocked", "execution": existing.to_dict(),
                    "reason": f"execution {existing.status}"}
        # pending/running/waiting_user -> already_started
        return {"eligibility": "already_started", "execution": existing.to_dict()}
    # 无 execution：验批准态
    plan = store.get_plan(plan_id)
    if plan is None or plan.change_session_id != session_id:
        return {"eligibility": "invalid_approval", "reason": "plan 不存在或不属于该 session"}
    if (plan.version != plan_version or plan.status != "approved_for_execution"
            or not plan.approval):
        return {"eligibility": "invalid_approval",
                "reason": f"plan 未批准（status={plan.status}, version={plan.version}）"}
    approval = PlanApproval.from_dict(plan.approval)
    if approval.decision != "approved":
        return {"eligibility": "invalid_approval", "reason": "approval.decision != approved"}
    # stale 检查
    cur_sig = _current_signature(rt, project)
    if approval.source_snapshot_signature and cur_sig != approval.source_snapshot_signature:
        return {"eligibility": "stale",
                "reason": "项目在批准后已变化（PROJECT_CHANGED_AFTER_APPROVAL）"}
    return {"eligibility": "eligible"}


# ============ startup discovery（§73/§96）============
def recover_executions(project_dir, project_name: str) -> int:
    """§73/§96：启动时扫该项目的 active execution（非终态），记账待恢复。
    AI-7C-1：仅发现 + 记账，不自动推进（workspace replay/commit/production 在后续 gate）。
    返回 active execution 数（对齐 recover_team_chat/recover_change_protocol 计数签名）。"""
    store = ChangeProtocolStore(project_dir, project_name)
    n = 0
    for ex in store.list_executions():
        if ex.status in EXEC_TERMINAL:
            continue
        n += 1
        logger.info("AI-7C recover: %s exec=%s plan=%s v%s status=%s stage=%s",
                    project_name, ex.execution_id, ex.plan_id,
                    ex.plan_version, ex.status, ex.stage)
    if n:
        logger.info("AI-7C: %s 发现 %d 个 active execution 待恢复", project_name, n)
    return n
