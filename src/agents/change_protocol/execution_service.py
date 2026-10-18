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
import asyncio
import logging
from typing import List, Optional

from src.revision.models import _canonical_json, ChangeOperation, creative_source_signature
from src.revision import changeset as cs_engine   # 复用既有 preview/apply/reconcile（§23/§62）
from src.revision.changeset import ChangeSetError, preview_operations_on_project
from src.core.store import ProjectStore
from src.core.write_lock import project_write_lock
from src.core.models import Asset

from ..runtime import AgentRuntime, AgentError
from .modes import AgentExecutionMode
from .models import (UnifiedChangePlan, PlanApproval, ModuleProposal)  # noqa: F401 (ModuleProposal 留作 AI-7C-4 扩展点)
from .execution_models import (ChangeExecutionSession, ExecutionStepRecord,
                               CanonicalCommitRecord, ProductionExecutionManifest,
                               ShotProductionActions,
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


# ============ AI-7C-2 创意 replay（§97）============
def _topo_sort(steps: list) -> list:
    """§24/§25：稳定拓扑序（Kahn）。依赖 DAG 必须满足；检测到环 -> PLAN_DEPENDENCY_CYCLE。
    PlanValidator 本应已查环（§25），此为执行期兜底。"""
    by_id = {s.get("step_id", ""): s for s in steps}
    indeg = {sid: 0 for sid in by_id}
    children = {sid: [] for sid in by_id}
    for s in steps:
        sid = s.get("step_id", "")
        for dep in (s.get("depends_on_step_ids") or []):
            if dep in by_id:
                indeg[sid] += 1
                children[dep].append(sid)
    # 稳定：初始 0 入度按 steps 出现顺序
    queue = [sid for sid in by_id if indeg[sid] == 0]
    order = []
    while queue:
        sid = queue.pop(0)
        order.append(sid)
        for c in children[sid]:
            indeg[c] -= 1
            if indeg[c] == 0:
                queue.append(c)
    if len(order) != len(by_id):
        raise AgentError("PLAN_DEPENDENCY_CYCLE", "plan step DAG 存在环（§25 执行期兜底）")
    return order


def _step_record(ex: ChangeExecutionSession, step_id: str) -> dict:
    for r in ex.steps:
        if r.get("step_id") == step_id:
            return r
    return {}


def _set_step_record(ex: ChangeExecutionSession, step_id: str, **fields) -> None:
    """就地更新某 step record 的字段。"""
    for r in ex.steps:
        if r.get("step_id") == step_id:
            r.update(fields)
            return
    # 不存在则追加（兜底）
    rec = ExecutionStepRecord(step_id=step_id, status="pending").to_dict()
    rec.update(fields)
    ex.steps.append(rec)


def run_creative_replay(rt: AgentRuntime, project: str,
                        execution_id: str) -> dict:
    """§97 AI-7C-2：execution workspace + 确定性创意 replay + 最终等价校验。
    从 stage=workspace_create 推进到 canonical_commit（AI-7C-3 接力）或 blocked。

    复用既有 changeset.preview/apply（§23）：每 step 一 ChangeSet，preview→apply 到 execution
    workspace，记 ExecutionStepRecord。拓扑串行（§24，不并发写同 workspace）。重启幂等：已 applied
    step 跳过（§73）。最终 §27 等价：final working creative_source_signature == preview.projected_signature。
    **canonical 全程不动**（workspace 是隔离 staging，§22）。0 production。"""
    store = _store(rt, project)
    ex = store.get_execution(execution_id)
    if ex is None:
        raise AgentError("EXECUTION_NOT_FOUND", f"execution 不存在: {execution_id}")
    if ex.project_name != project:
        raise AgentError("EXECUTION_NOT_FOUND", "execution 不属于该项目")
    # 终态/blocked：幂等返回
    if ex.status in EXEC_TERMINAL or ex.status == "blocked":
        return {"execution": ex.to_dict(), "idempotent": True}
    # 只在 replay 相关 stage 推进
    if ex.stage not in ("workspace_create", "creative_replay", "workspace_verify"):
        return {"execution": ex.to_dict(), "idempotent": True}

    ws_store = rt._ws_store(project)
    plan = store.get_plan(ex.plan_id)
    preview = store.get_preview(ex.preview_artifact_id)
    if plan is None or preview is None:
        raise AgentError("PLAN_PREVIEW_FAILED", "plan/preview artifact 缺失，无法 replay")

    # ---------- stage: workspace_create ----------
    if ex.stage == "workspace_create":
        if not ex.execution_workspace_id:
            # §21：从当前 canonical 建隔离 execution workspace；purpose 经 name 标注 +
            # execution_workspace_id 绑定（§21：不强加 schema 字段，等效可追溯）。
            ws = ws_store.create(name=f"AI-7C exec {execution_id[:14]}")
            ex.execution_workspace_id = ws.workspace_id
        ex.status = "running"
        ex.stage = "creative_replay"
        ex.updated_at = _now()
        store.save_execution(ex)

    # ---------- stage: creative_replay ----------
    if ex.stage == "creative_replay":
        bundles_by_step = {b.get("step_id"): b for b in (preview.module_operations or [])}
        order = _topo_sort(plan.steps or [])
        for step_id in order:
            rec = _step_record(ex, step_id)
            if rec.get("status") == "applied":
                continue   # §73 重启跳过已应用 step（不 double-apply）
            bundle = bundles_by_step.get(step_id, {})
            ops = [ChangeOperation.from_dict(o) for o in (bundle.get("operations") or [])]
            ws = ws_store.load(ex.execution_workspace_id)
            rev_before = ws.revision
            cid = None
            try:
                if ops:
                    # §23：复用既有 preview（校验 revision + 分配/复用 stable id + 落盘 changeset）
                    pv = cs_engine.preview(ws_store, ws,
                                           title=f"AI-7C exec {execution_id[:10]}:{step_id}",
                                           intent="approved_plan_execution",
                                           base_workspace_revision=ws.revision,
                                           operations=ops)
                    # §23：复用既有 apply（三重校验 + crash-safe 写 working + bump revision）
                    cs_engine.apply(ws_store, ws, pv["changeset_id"])
                    cid = pv["changeset_id"]
            except ChangeSetError as cse:
                # §97：stale workspace revision / before-mismatch -> 该 step failed，execution blocked。
                # canonical 未动（workspace 隔离）。不重试、不修复（§9：不创意修复）。
                _set_step_record(ex, step_id, status="failed",
                                 workspace_revision_before=rev_before,
                                 error={"code": cse.code, "message": cse.message},
                                 completed_at=_now())
                ex.status = "blocked"
                ex.stage = "creative_replay"
                ex.blocker = {"code": cse.code, "step_id": step_id,
                              "message": cse.message}
                ex.updated_at = _now()
                store.save_execution(ex)
                return {"execution": ex.to_dict(), "blocked": True,
                        "blocker_code": cse.code}
            ws2 = ws_store.load(ex.execution_workspace_id)
            _set_step_record(ex, step_id, status="applied",
                             workspace_revision_before=rev_before,
                             workspace_revision_after=ws2.revision,
                             changeset_id=cid, completed_at=_now())
            ex.status = "running"
            ex.updated_at = _now()
            store.save_execution(ex)   # 每 step 落盘（crash 后可从 step record 续）
        ex.stage = "workspace_verify"
        ex.updated_at = _now()
        store.save_execution(ex)

    # ---------- stage: workspace_verify（§27 最终等价）----------
    if ex.stage == "workspace_verify":
        final_working = ws_store.load_working_project(ex.execution_workspace_id)
        final_sig = creative_source_signature(final_working.to_dict())
        # §27：final workspace creative signature == approved preview projected_signature
        if not preview.projected_signature or final_sig != preview.projected_signature:
            ex.status = "blocked"
            ex.stage = "workspace_verify"
            ex.blocker = {"code": "EXECUTION_PREVIEW_MISMATCH",
                          "final_signature": final_sig,
                          "expected_projected_signature": preview.projected_signature,
                          "message": "执行结果与批准预览不一致（§27），canonical 未动"}
            ex.updated_at = _now()
            store.save_execution(ex)
            return {"execution": ex.to_dict(), "blocked": True,
                    "blocker_code": "EXECUTION_PREVIEW_MISMATCH"}
        ex.stage = "canonical_commit"   # 等待 AI-7C-3 原子提交
        ex.updated_at = _now()
        store.save_execution(ex)

    return {"execution": ex.to_dict(), "idempotent": False}


def start_and_replay(rt: AgentRuntime, project: str, session_id: str,
                     plan_id: str, plan_version: int,
                     production_mode: str = "affected_production") -> dict:
    """端点入口：execute_plan（AI-7C-1 创建+revalidation）→ 若未 blocked 则 run_creative_replay
    （AI-7C-2 workspace replay + 等价校验）。幂等：重复调用对终态/已推进 execution 为 no-op。
    保持 execute_plan 自身停在 workspace_create（AI-7C-1 契约 + 单测不变）；端点在此之上接力。"""
    res = execute_plan(rt, project, session_id, plan_id, plan_version, production_mode)
    ex = res.get("execution")
    if not ex or res.get("blocked") or ex.get("status") == "blocked":
        return res   # stale/blocked：不 replay
    if ex.get("stage") in ("workspace_create", "creative_replay", "workspace_verify"):
        return run_creative_replay(rt, project, ex["execution_id"])
    return res   # 已到 canonical_commit+ ：幂等返回


# ============ AI-7C-3 原子 canonical creative commit（§98）============
_ACTIVE_RUN_STATES = ("running", "pausing", "composing", "preparing", "producing")


def _active_production(project: str) -> Optional[str]:
    """§20 best-effort：检测活跃生产流（ProjectRun/FullRun active 或活跃 video Job）。
    返回 reason str 或 None。lazy import 避免硬耦合 + 测试无生产模块时返回 None（不阻断）。
    这是 §33「commit 与 production 不产生 lost update」的 coexistence 规则：commit 前无活跃生产。"""
    try:
        from src.ai.project_autopilot import get_project_autopilot_manager
        for r in get_project_autopilot_manager().list_runs(project):
            if r.status in _ACTIVE_RUN_STATES:
                return f"ProjectProductionRun {r.run_id} ({r.status})"
    except Exception:
        pass
    try:
        from src.ai.full_project_autopilot import get_full_project_manager
        for r in get_full_project_manager().list_runs(project):
            if r.status in _ACTIVE_RUN_STATES:
                return f"FullProjectRun {r.run_id} ({r.status})"
    except Exception:
        pass
    try:
        from deps import get_job_queue
        jq = get_job_queue()
        if jq is not None:
            for j in jq.list_jobs(project=project, state="running"):
                if getattr(j, "kind", "") == "video":
                    return f"active video job {getattr(j, 'job_id', '?')}"
    except Exception:
        pass
    return None


def _reconcile_commit(ex: ChangeExecutionSession, store: ChangeProtocolStore,
                      project_dir) -> None:
    """§35 崩溃对账：execution 有 COMMIT_INTENT 但无 COMMIT_DONE。读当前 canonical creative 签名：
    - Case B: == expected_after -> 已提交 -> reconcile committed（不重 apply）
    - Case A: == before -> 未提交 -> 保持 commit_intent，caller 续提交
    - Case C: 都不匹配 -> CANONICAL_COMMIT_RECONCILIATION_CONFLICT -> blocked（不猜）
    caller 负责 save_execution。"""
    cc = ex.canonical_commit
    if not cc or cc.get("status") != "commit_intent":
        return
    cur_sig = creative_source_signature(ProjectStore(project_dir).load().to_dict())
    before_sig = cc.get("before_signature", "")
    after_sig = cc.get("expected_after_signature", "")
    if after_sig and cur_sig == after_sig:
        cc["status"] = "committed"
        cc["committed_at"] = cc.get("committed_at") or _now()
        ex.canonical_commit = cc
        if ex.production_mode == "creative_only":
            ex.stage = "done"; ex.status = "completed"; ex.production_skipped_by_user = True
        else:
            ex.stage = "character_sync"; ex.status = "running"
    elif before_sig and cur_sig == before_sig:
        return   # Case A：未提交，保持 commit_intent，caller 续提交
    else:
        ex.status = "blocked"; ex.stage = "canonical_commit"
        ex.blocker = {"code": "CANONICAL_COMMIT_RECONCILIATION_CONFLICT",
                      "current_signature": cur_sig,
                      "before_signature": before_sig,
                      "expected_after_signature": after_sig,
                      "message": "canonical 提交对账冲突（§35 Case C），不猜测"}


async def commit_canonical(rt: AgentRuntime, project: str,
                           execution_id: str) -> dict:
    """§98 AI-7C-3：原子 canonical creative commit。从 stage=canonical_commit 提交到
    done（creative_only）/character_sync（affected_production），或 blocked。

    流程（§30/§31/§32/§34/§36）：
    §35 先对账 in-flight COMMIT_INTENT（崩溃恢复）；
    §20 active-production preflight（§33 不与 production 并发写）；
    async project_write_lock 短临界区：reload latest canonical → §32 第二次 stale 校验 →
    §31 preview_operations_on_project（纯投影，保留无关 production 态 §30）→ §27 等价校验 →
    §36 canonical_before 审计快照 → §34 COMMIT_INTENT write-ahead → 原子 save → §34 COMMIT_DONE。
    不 generic workspace→canonical merge（§29）；只提交该批准 plan 的 exact operations。"""
    store = _store(rt, project)
    ex = store.get_execution(execution_id)
    if ex is None:
        raise AgentError("EXECUTION_NOT_FOUND", f"execution 不存在: {execution_id}")
    if ex.project_name != project:
        raise AgentError("EXECUTION_NOT_FOUND", "execution 不属于该项目")
    # 幂等：已提交
    cc = ex.canonical_commit
    if cc and cc.get("status") == "committed":
        # M1 加固：若 stage 被重新置 canonical_commit（如 resume 重跑 start_replay_commit
        # → run_creative_replay 把 stage 置 canonical_commit），恢复 post-commit stage，
        # 防 execution 卡在 canonical_commit 无法经 resume 推进到 character_sync。
        if ex.stage == "canonical_commit":
            ex.stage = "done" if ex.production_mode == "creative_only" else "character_sync"
            store.save_execution(ex)
        return {"execution": ex.to_dict(), "idempotent": True, "committed": True}
    if ex.status in EXEC_TERMINAL:
        return {"execution": ex.to_dict(), "idempotent": True}

    project_dir = rt._resolve_project_dir(project)

    # §35：in-flight COMMIT_INTENT 先对账（崩溃恢复）
    if cc and cc.get("status") == "commit_intent":
        _reconcile_commit(ex, store, project_dir)
        store.save_execution(ex)
        if ex.canonical_commit.get("status") == "committed":
            return {"execution": ex.to_dict(), "idempotent": True, "committed": True}  # Case B
        if ex.status == "blocked":
            return {"execution": ex.to_dict(), "blocked": True,
                    "blocker_code": "CANONICAL_COMMIT_RECONCILIATION_CONFLICT"}  # Case C
        # Case A：保持 commit_intent，续提交（fall through）

    if ex.stage != "canonical_commit":
        return {"execution": ex.to_dict(), "idempotent": True}   # 未到提交 stage

    # §20 active-production preflight（§33 coexistence 规则）
    active = _active_production(project)
    if active:
        ex.status = "blocked"; ex.stage = "canonical_commit"
        ex.blocker = {"code": "PROJECT_PRODUCTION_ACTIVE", "detail": active,
                      "message": "存在活跃生产流，提交被阻断以避免 lost update（§20/§33）"}
        store.save_execution(ex)
        return {"execution": ex.to_dict(), "blocked": True,
                "blocker_code": "PROJECT_PRODUCTION_ACTIVE"}

    plan = store.get_plan(ex.plan_id)
    preview = store.get_preview(ex.preview_artifact_id)
    if plan is None or preview is None:
        raise AgentError("PLAN_PREVIEW_FAILED", "plan/preview artifact 缺失，无法 commit")
    ops = [ChangeOperation.from_dict(o) for b in (preview.module_operations or [])
           for o in (b.get("operations") or [])]

    async with project_write_lock(project):
        # AI-7C-H §四：锁外等待期间 cancel 可能已发生——临界区内 reload-latest，
        # 终态/cancel_requested 优先，stale ex 不得把 cancelled 复活回 running。
        latest = store.get_execution(execution_id)
        if latest is None:
            raise AgentError("EXECUTION_NOT_FOUND", f"execution 不存在: {execution_id}")
        if latest.status in EXEC_TERMINAL:
            return {"execution": latest.to_dict(), "idempotent": True}
        if latest.cancel_requested:
            return {"execution": latest.to_dict(), "cancelling": True}
        ex = latest   # 锁内后续无 await，用最新对象续（atomic w.r.t. cancel）
        pstore = ProjectStore(project_dir)
        current = pstore.load()   # §30 reload LATEST canonical（含批准后完成的 production 态）
        before_sig = creative_source_signature(current.to_dict())
        # §32 第二次 stale 校验（临界区内，关 TOCTOU 窗口）
        if ex.source_signature and before_sig != ex.source_signature:
            ex.status = "blocked"; ex.stage = "canonical_commit"
            ex.blocker = {"code": "PROJECT_CHANGED_AFTER_APPROVAL",
                          "current_signature": before_sig,
                          "approved_signature": ex.source_signature,
                          "message": "canonical 在提交临界区前已变化（§32）"}
            store.save_execution(ex)
            return {"execution": ex.to_dict(), "blocked": True,
                    "blocker_code": "PROJECT_CHANGED_AFTER_APPROVAL"}
        # §31 纯投影 to LATEST canonical（ops 仅创作字段，保留无关 production 态 §30）
        try:
            projected, _diff, _impact, _warn = preview_operations_on_project(current, ops)
        except ChangeSetError as cse:
            ex.status = "blocked"; ex.stage = "canonical_commit"
            ex.blocker = {"code": "EXECUTION_PREVIEW_MISMATCH",
                          "detail": f"{cse.code}: {cse.message}",
                          "message": "canonical 提交投影失败（§31）"}
            store.save_execution(ex)
            return {"execution": ex.to_dict(), "blocked": True,
                    "blocker_code": "EXECUTION_PREVIEW_MISMATCH"}
        projected_sig = creative_source_signature(projected.to_dict())
        # §27 等价：投影后 canonical 签名 == 批准 preview projected_signature
        if not preview.projected_signature or projected_sig != preview.projected_signature:
            ex.status = "blocked"; ex.stage = "canonical_commit"
            ex.blocker = {"code": "EXECUTION_PREVIEW_MISMATCH",
                          "projected_signature": projected_sig,
                          "expected": preview.projected_signature,
                          "message": "canonical 提交结果与批准预览不一致（§27）"}
            store.save_execution(ex)
            return {"execution": ex.to_dict(), "blocked": True,
                    "blocker_code": "EXECUTION_PREVIEW_MISMATCH"}
        # §36 pre-commit 不可变审计快照
        store.save_execution_audit(execution_id, "canonical_before.json", current.to_dict())
        # §34 COMMIT_INTENT write-ahead（危险窗口：save 成功 但 COMMIT_DONE 前崩溃）
        cc_rec = CanonicalCommitRecord(
            status="commit_intent", before_signature=before_sig,
            expected_after_signature=preview.projected_signature,
            operation_digest=ex.operation_digest, commit_intent_at=_now())
        ex.canonical_commit = cc_rec.to_dict()
        ex.status = "running"; ex.stage = "canonical_commit"
        store.save_execution(ex)
        # §31 原子 canonical save（latest + exact approved creative ops）
        from src.core.store import ProjectConflictError
        try:
            pstore.save(projected, expected_signature=getattr(current, "_store_signature", None))
        except ProjectConflictError:
            ex.status = "blocked"; ex.stage = "canonical_commit"
            ex.blocker = {"code": "PROJECT_CHANGED_AFTER_APPROVAL",
                          "message": "canonical 在最终提交前变化；请重新校验"}
            cc_rec.status = "conflict"
            ex.canonical_commit = cc_rec.to_dict()
            store.save_execution(ex)
            return {"execution": ex.to_dict(), "blocked": True,
                    "blocker_code": "PROJECT_CHANGED_AFTER_APPROVAL"}
        # §34 COMMIT_DONE
        cc_rec.status = "committed"; cc_rec.committed_at = _now()
        ex.canonical_commit = cc_rec.to_dict()
        # §40/§66：compute + store production manifest（deterministic, 0 LLM）。
        # creative_only 亦算（§66 可显示 production 后果，但不执行）；affected_production 供 7C-4 sync 用。
        try:
            manifest = compute_production_manifest(ex, plan, preview, projected)
            store.save_execution_audit(execution_id, "production_manifest.json",
                                       manifest.to_dict())
            ex.production_manifest_file = "production_manifest.json"
        except Exception as e:
            logger.warning("AI-7C production manifest compute failed (exec=%s): %s",
                            execution_id, e)
        # §66/§67：creative_only -> done/completed（production skipped）；
        #          affected_production -> character_sync/running（AI-7C-4 接力）
        if ex.production_mode == "creative_only":
            ex.stage = "done"; ex.status = "completed"; ex.production_skipped_by_user = True
        else:
            ex.stage = "character_sync"; ex.status = "running"
        store.save_execution(ex)
    # AI-7D-R §10.2：创作变更已真正落定 canonical → 记录共享工作记忆（best-effort，
    # 不阻断提交流程）。让后续轮次/其它 agent 知道这次工作真的发生过（修根因 #4）。
    try:
        domains = sorted({getattr(s, "module", "") for s in (plan.steps or []) if getattr(s, "module", "")})
        rt.record_work(
            project, "change",
            f"已提交创作变更（{', '.join(domains) or '项目'}；"
            + ("仅创作，未触发生成" if ex.production_mode == "creative_only" else "将继续受影响制作") + "）",
            participants=["producer"],
            references={"change_session_id": ex.change_session_id, "execution_id": ex.execution_id},
            affected_domains=domains,
            status=("completed" if ex.production_mode == "creative_only" else "in_progress"))
    except Exception:
        pass
    return {"execution": ex.to_dict(), "committed": True, "idempotent": False}


async def start_replay_commit(rt: AgentRuntime, project: str, session_id: str,
                              plan_id: str, plan_version: int,
                              production_mode: str = "affected_production") -> dict:
    """端点入口（AI-7C-3 扩展）：start_and_replay（→ canonical_commit）→ commit_canonical（→
    done/character_sync）。幂等。creative_only 提交后即 done/completed。"""
    res = start_and_replay(rt, project, session_id, plan_id, plan_version, production_mode)
    ex = res.get("execution")
    if not ex or res.get("blocked") or ex.get("status") == "blocked":
        return res
    if ex.get("stage") == "canonical_commit":
        return await commit_canonical(rt, project, ex["execution_id"])
    return res


# ============ AI-7C-4 production impact manifest（§39/§40/§41）============
def compute_production_manifest(ex: ChangeExecutionSession, plan: UnifiedChangePlan,
                                preview, project) -> ProductionExecutionManifest:
    """§39/§41：deterministic production execution manifest（0 LLM）。从批准 operations + 提交后
    project 直接派生 shot/character actions（不依赖 impact 的 _shot_has_frame 门控——无既有
    资产的 shot 仍需生成）；impact 仅用于 scene 级 scope（affected/planner scene ids）。
    只 regen 实际被批准变更 + 依赖闭包 invalidated 的资产，不整项目重生成（§39）。

    规则（§41，按本仓库实际 op 字段）：
      update_character(description_en) -> char REGENERATE + 引用该 char 的 shot frame/video REGENERATE
      update_shot(description/characters) -> first_frame REGENERATE, last_frame REGENERATE(若 keyframes), video REGENERATE
      update_shot(target_duration_s) -> video REGENERATE
      update_scene(summary/characters) -> scene 内 shot frame/video REGENERATE
      insert_shot -> 全 generate_new
      delete_shot -> remove_from_timeline（不删历史资产 §42）
      reorder_shots -> keep（边界依赖由 7C-5 dependency 分析定）
      audio -> 现有 shot 无 dialogue op（v1），仅 insert_shot 标 generate_new；其余 keep（§48 v1 限制）"""
    impact = preview.aggregate_impact or {}
    affected_scenes = set(impact.get("affected_scene_ids", []))
    planner_scenes = set(impact.get("production_plan_scene_ids", []))
    affected_shots_scope = set(impact.get("affected_shot_ids", []))

    ops = [ChangeOperation.from_dict(o) for b in (preview.module_operations or [])
           for o in (b.get("operations") or [])]

    # ---- character actions（§43）----
    char_actions: dict = {}
    for op in ops:
        if op.op_type == "update_character" and op.character_name:
            char_actions[op.character_name] = "regenerate"
        elif op.op_type == "insert_character" and op.new_character:
            char_actions[op.new_character.name] = "generate_new"
        elif op.op_type == "delete_character" and op.character_name:
            char_actions[op.character_name] = "remove_from_timeline"
    changed_chars = {n for n, a in char_actions.items()
                     if a in ("regenerate", "generate_new")}

    shots_by_id = {sh.shot_id: sh for s in (project.scenes or []) for sh in (s.shots or [])}
    shot_actions: dict = {}

    def _ensure(sid: str) -> dict:
        if sid not in shot_actions:
            shot_actions[sid] = ShotProductionActions().to_dict()   # all keep
        return shot_actions[sid]

    for op in ops:
        if op.op_type == "insert_shot" and op.new_shot and op.new_shot.shot_id:
            shot_actions[op.new_shot.shot_id] = ShotProductionActions(
                first_frame="generate_new", audio="generate_new",
                last_frame="generate_new", video="generate_new").to_dict()
        elif op.op_type == "delete_shot" and op.shot_id:
            shot_actions[op.shot_id] = ShotProductionActions(
                first_frame="remove_from_timeline", audio="remove_from_timeline",
                last_frame="remove_from_timeline", video="remove_from_timeline").to_dict()
        elif op.op_type == "update_shot" and op.shot_id:
            a = _ensure(op.shot_id)
            sh = shots_by_id.get(op.shot_id)
            lm = getattr(sh, "link_mode", "i2v") if sh else "i2v"
            if op.description is not None or op.characters is not None:
                a["first_frame"] = "regenerate"
                a["video"] = "regenerate"
                if lm == "keyframes":
                    a["last_frame"] = "regenerate"
            if op.target_duration_s is not None:
                a["video"] = "regenerate"
        elif op.op_type == "update_character" and op.character_name in changed_chars:
            for s in (project.scenes or []):
                for sh in (s.shots or []):
                    if op.character_name in sh.characters:
                        a = _ensure(sh.shot_id)
                        a["first_frame"] = "regenerate"
                        a["video"] = "regenerate"
                        if getattr(sh, "link_mode", "i2v") == "keyframes":
                            a["last_frame"] = "regenerate"
        elif op.op_type == "update_scene" and op.scene_id:
            for s in (project.scenes or []):
                if s.scene_id == op.scene_id:
                    for sh in (s.shots or []):
                        a = _ensure(sh.shot_id)
                        # M2/§41：summary 或 characters 变更 -> scene 内 shot first_frame REGENERATE
                        # （原仅 summary 触发，漏 characters -> 角色名单变更首帧不重生成，与 §41 不符）
                        if op.summary is not None or op.characters is not None:
                            a["first_frame"] = "regenerate"
                        a["video"] = "regenerate"

    expected_video = sum(1 for a in shot_actions.values()
                         if a.get("video") in ("regenerate", "generate_new"))
    # planner/affected scenes：从 shot_actions 的 shot 所属 scene 派生（补 impact 对 update_character
    # 不级联 scene 到 production_plan_scene 的 gap），并入 impact 的 scene 级 scope。
    shot_scenes: set = set()
    for sid in shot_actions:
        for s in (project.scenes or []):
            if any(sh.shot_id == sid for sh in (s.shots or [])):
                shot_scenes.add(s.scene_id); break
    planner_scenes_final = shot_scenes | set(planner_scenes)
    affected_scenes_final = shot_scenes | affected_scenes
    return ProductionExecutionManifest(
        execution_id=ex.execution_id, project_name=ex.project_name,
        committed_source_signature=ex.source_signature,
        affected_character_ids=sorted(char_actions.keys()),
        affected_scene_ids=sorted(affected_scenes_final),
        affected_shot_ids=sorted(set(shot_actions.keys()) | affected_shots_scope),
        character_actions=char_actions,
        shot_actions=shot_actions,
        planner_scene_ids=sorted(planner_scenes_final),
        baseline_asset_refs={},                # AI-7C-5 填（冻结 baseline version IDs）
        expected_video_requests=expected_video,
        production_plan_ids={},                # AI-7C-5 填
        created_at=_now())


def get_production_manifest(rt: AgentRuntime, project: str,
                            execution_id: str) -> Optional[dict]:
    """读 execution 的 production_manifest 审计快照。"""
    d = _store(rt, project).load_execution_audit(execution_id, "production_manifest.json")
    if d is None:
        return None
    return ProductionExecutionManifest.from_dict(d).to_dict()


def _ensure_manifest(rt: AgentRuntime, project: str, ex: ChangeExecutionSession) -> Optional[dict]:
    """L4/§15：确保 production manifest 落盘并返回其 dict。
    场景：commit_canonical 的 pstore.save(行597) 成功后、manifest 计算(行605)前崩溃 ->
    重启 _reconcile_commit Case B 置 committed + stage=character_sync，但
    production_manifest_file 仍 None -> _assert_agnes_prerequisites 永久 blocked 无重算路径。
    修复：缺失且已提交时，从 已提交 canonical project + 批准 plan/preview 确定性重算
    （compute_production_manifest 纯函数 0 LLM），落盘 + 设 production_manifest_file。
    manifest 可从 (approved ops + committed project + execution) 重算 -> 不引入第二真源（§15）。"""
    store = _store(rt, project)
    manifest_d = store.load_execution_audit(ex.execution_id, "production_manifest.json")
    if manifest_d:
        if not ex.production_manifest_file:
            ex.production_manifest_file = "production_manifest.json"
            store.save_execution(ex)
        return manifest_d
    cc = ex.canonical_commit
    if not (cc and cc.get("status") == "committed"):
        return None   # 未提交，无 manifest 可重算
    plan = store.get_plan(ex.plan_id)
    preview = store.get_preview(ex.preview_artifact_id)
    project_dir = rt._resolve_project_dir(project)
    committed = ProjectStore(project_dir).load()
    if not (plan and preview):
        return None
    manifest = compute_production_manifest(ex, plan, preview, committed)
    store.save_execution_audit(ex.execution_id, "production_manifest.json", manifest.to_dict())
    ex.production_manifest_file = "production_manifest.json"
    store.save_execution(ex)
    logger.info("AI-7C L4：execution %s manifest 缺失，已重算落盘", ex.execution_id)
    return manifest.to_dict()


# ============ AI-7C-H Cancel Hotfix：guarded execution mutation + cancellation 收敛 ============
# §四/§五/§六/§七/§八/§九 不变量：取消不能复活。规则：
#   - 一旦 cancel_requested=True，普通 post-commit 代码不得推进新的执行阶段（stage/status/run）。
#   - 一旦 Execution 进入 cancelled（终态），任何后台旧 Python 对象不得把它重新保存为 running/blocked。
#   - post-commit 持久状态修改一律经 mutate_execution_guarded：project_write_lock 短临界区
#     （复用既有 per-project 锁，不新建锁）→ reload 最新 execution → 检查 → 只改本次需要的字段
#     → atomic save。绝不长生命周期函数几分钟后 store.save_execution(old_ex) 整对象覆盖。
#   - 锁不跨 Agnes/Planner/video 等长网络调用；唯一例外是 §七 run-start 临界区
#     （最终 cancel 检查 + 创建 ProductionRun + run_id 关联，全部本地磁盘操作）。
_STOP_POLL_S = 2.0            # stopping watcher 轮询间隔（测试可调）
_STOP_DEADLINE_S = 86400.0    # watcher 兜底上限
_STOP_WATCHERS: dict = {}     # execution_id -> asyncio.Task（强引用防 GC）


async def mutate_execution_guarded(rt: AgentRuntime, project: str, execution_id: str,
                                    mutator, *, policy: str = "advance"):
    """§五：最小 guarded execution mutation。锁内 reload 最新 → 策略检查 → mutator(latest)
    就地修改 → atomic save。返回 (latest, applied)：applied=False 表示被拒绝（取消/终态优先）。

    policy="advance"：普通执行推进（stage/status/新工作）。latest 已 terminal 或
    cancel_requested → 拒绝（§四：取消不能复活、不得推进新阶段）。
    policy="record"：已开始最小单元的完成记录（char sync record / scene checkpoint /
    replay step applied）。仅 latest 已 terminal → 拒绝；cancel_requested 但仍 running
    → 允许（记录真实发生的完成，§六：已开始单元自然结束，不产生新工作）。"""
    async with project_write_lock(project):
        store = _store(rt, project)
        latest = store.get_execution(execution_id)
        if latest is None:
            raise AgentError("EXECUTION_NOT_FOUND", f"execution 不存在: {execution_id}")
        if latest.status in EXEC_TERMINAL:
            return latest, False
        if policy == "advance" and latest.cancel_requested:
            return latest, False
        mutator(latest)
        latest.updated_at = _now()
        store.save_execution(latest)
        return latest, True


def _get_owned_run(project: str, run_id: Optional[str]):
    """§八：按精确 production_run_id 读 owned ProjectProductionRun（绝不「找当前项目任意
    active run」来猜）。run_id 为空/读失败 → None。"""
    if not run_id:
        return None
    try:
        from src.ai.project_autopilot import get_project_autopilot_manager
        return get_project_autopilot_manager().get_run(project, run_id)
    except Exception:
        return None


def _run_is_active(run) -> bool:
    """§八：owned run 是否仍会产生新工作（pending/running/pausing/paused/composing）。"""
    if run is None:
        return False
    try:
        from src.ai.project_autopilot import PRUN_ACTIVE
        return run.status in PRUN_ACTIVE
    except Exception:
        return False


async def _propagate_cancel_to_owned_run(project: str, run_id: Optional[str]) -> bool:
    """§八：取消传播到 owned ProductionRun——复用既有 ProjectProductionManager.request_cancel
    语义（cooperative：允许当前已提交 Agnes job 完成并保存，Scene 间立即停；资产保留不回滚）。
    幂等；不重写一套 ProductionRun cancellation。"""
    if not run_id:
        return False
    try:
        from src.ai.project_autopilot import get_project_autopilot_manager, PRUN_TERMINAL
        mgr = get_project_autopilot_manager()
        run = mgr.get_run(project, run_id)
        if run is None or run.status in PRUN_TERMINAL:
            return False
        await mgr.request_cancel(project, run_id)
        return True
    except Exception as e:   # noqa: BLE001
        logger.warning("AI-7C-H 取消传播到 ProductionRun %s 失败: %s", run_id, e)
        return False


def _inflight_other_task(execution_id: str) -> bool:
    """是否有**其他**在飞 post-commit 任务（当前任务自身的在飞不算——它正是收敛驱动者）。"""
    t = _INFLIGHT_POSTCOMMIT.get(execution_id)
    return t is not None and t is not asyncio.current_task()


async def _converge_cancel_once(rt: AgentRuntime, project: str,
                                execution_id: str) -> bool:
    """§三/§八：单次收敛尝试（不等待、不阻塞）。cancel_requested 且不再产生新工作
    （无其他在飞 post-commit 任务 + owned run 非活跃/不存在）→ 短临界区收敛 cancelled，
    返回 True。owned run 仍活跃 → 幂等传播取消后返回 False（保持「正在停止…」）。
    幂等可并发；终态/cancel 检查全部在锁内 reload-latest（§四）。"""
    store = _store(rt, project)
    ex = store.get_execution(execution_id)
    if ex is None or ex.status in EXEC_TERMINAL or not ex.cancel_requested:
        return True   # 无可收敛（已终态/未请求取消/不存在）
    if _inflight_other_task(execution_id):
        return False   # 后台任务仍在收尾，由其结束时收敛
    run = _get_owned_run(project, ex.production_run_id) if ex.production_run_id else None
    if _run_is_active(run):
        await _propagate_cancel_to_owned_run(project, ex.production_run_id)
        return False   # owned run 仍在停止中（§八：Execution 不提前宣称已取消）
    async with project_write_lock(project):
        latest = store.get_execution(execution_id)
        if (latest is None or latest.status in EXEC_TERMINAL
                or not latest.cancel_requested):
            return True
        if _inflight_other_task(execution_id):
            return False
        latest.status = "cancelled"
        latest.updated_at = _now()
        store.save_execution(latest)
        logger.info("AI-7C-H：execution %s 已收敛为 cancelled", execution_id)
        return True


def _spawn_stop_watcher(rt: AgentRuntime, project: str, execution_id: str) -> None:
    """§八：后台驱动 cancellation 收敛（不阻塞调用方）。幂等（同 execution 仅一个 watcher）。
    无运行 event loop（纯 sync 上下文）时静默跳过——由 recover/读取惰性收敛路径兜底。"""
    if execution_id in _STOP_WATCHERS:
        return

    async def _watch() -> None:
        deadline = _now() + _STOP_DEADLINE_S
        try:
            while _now() < deadline:
                if await _converge_cancel_once(rt, project, execution_id):
                    return
                await asyncio.sleep(_STOP_POLL_S)
            logger.warning("AI-7C-H stop watcher 超时（exec=%s），保持 stopping 状态",
                           execution_id)
        finally:
            _STOP_WATCHERS.pop(execution_id, None)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _STOP_WATCHERS[execution_id] = loop.create_task(_watch())


async def maybe_converge_cancellation(rt: AgentRuntime, project: str,
                                      execution_id: str) -> None:
    """§八：读取路径惰性收敛（GET execution 轮询调用）。cancel_requested 且已无在飞工作
    → 收敛 cancelled；owned run 活跃时只传播取消、立即返回（不阻塞读）。幂等。"""
    store = _store(rt, project)
    ex = store.get_execution(execution_id)
    if ex is None or ex.status in EXEC_TERMINAL or not ex.cancel_requested:
        return
    await _converge_cancel_once(rt, project, execution_id)


async def _mark_postcommit_blocked(rt: AgentRuntime, project: str, execution_id: str,
                                   code: str, message: str) -> None:
    """post-commit 失败 → blocked（guarded：取消/终态优先，不覆盖 cancelled/cancel_requested）。"""
    store = _store(rt, project)
    ex = store.get_execution(execution_id)
    if ex is None or ex.status in EXEC_TERMINAL or ex.cancel_requested:
        return   # 取消优先：由收敛路径处理，不误报受阻

    def _blk(latest):
        latest.status = "blocked"
        latest.blocker = {"code": code, "message": message}
    try:
        await mutate_execution_guarded(rt, project, execution_id, _blk, policy="advance")
    except AgentError:
        pass


# ============ AI-7C-4b character sync + §68 first-Agnes hard gate ============
def _assert_agnes_prerequisites(ex: ChangeExecutionSession, store: ChangeProtocolStore,
                                project_dir) -> None:
    """§68 first-Agnes hard gate：任何 AI-7C 触发的真 Agnes 请求（character view / audio / video）
    前置——canonical creative commit==committed + production manifest 持久化 + 冻结的提交后创作态
    未被改动（current creative_source_signature == canonical_commit.expected_after_signature）。
    失败 -> AgentError(PRODUCTION_PREREQUISITE_FAILED)，**0 Agnes 调用**（§68 负测目标）。
    （char refs ready / plans approved / ProductionRun persisted 在 7C-4b/5 各就绪点补查。）"""
    cc = ex.canonical_commit
    if not cc or cc.get("status") != "committed":
        raise AgentError("PRODUCTION_PREREQUISITE_FAILED",
                         "canonical creative commit 未完成（§68：0 Agnes）")
    if not ex.production_manifest_file:
        raise AgentError("PRODUCTION_PREREQUISITE_FAILED",
                         "production manifest 未持久化（§68：0 Agnes）")
    cur_sig = creative_source_signature(ProjectStore(project_dir).load().to_dict())
    expected_after = cc.get("expected_after_signature", "")
    if expected_after and cur_sig != expected_after:
        raise AgentError("PRODUCTION_PREREQUISITE_FAILED",
                         "canonical 提交后创作态已变化（冻结源签名不匹配，§68：0 Agnes）")


async def run_character_sync(rt: AgentRuntime, project: str, execution_id: str, *,
                             view_generator=None) -> dict:
    """§43/§44/§46/§51/§67：affected_production 的 character visual sync。creative_only 已在 commit
    终止（done），此函数对其 no-op（§47）。view_generator 为可注入 callable：``async (char_name)
    -> dict[view_name, Asset|dict]``。默认真路径（JobQueue character_views Job + chain handler）在
    §103 acceptance 接通；测试注入 fake（AI-6F fake/test 分离模式）。

    流程：§68 gate -> 读 manifest（character_actions regenerate）-> 每**未 sync** char：
    view_generator(char)->views -> project_write_lock 短临界区持久化 Character.views（views 仅 image
    refs，不改 creative sig）-> 记 CharacterSyncRecord -> stage=production_planning（AI-7C-5 接力）。
    幂等：已 sync char 跳过（重启不重复生成 §46）。Character sync 必须先于 shot production freeze（§51）。"""
    store = _store(rt, project)
    ex = store.get_execution(execution_id)
    if ex is None:
        raise AgentError("EXECUTION_NOT_FOUND", f"execution 不存在: {execution_id}")
    if ex.project_name != project:
        raise AgentError("EXECUTION_NOT_FOUND", "execution 不属于该项目")
    if ex.status in EXEC_TERMINAL or ex.status == "blocked":
        return {"execution": ex.to_dict(), "idempotent": True}
    if ex.stage != "character_sync" or ex.production_mode == "creative_only":
        return {"execution": ex.to_dict(), "idempotent": True}   # creative_only 已 done；非 sync stage
    # AI-7C-H §六：进入 sync 前已请求取消 → 不推进新工作（§四），交收敛路径
    if ex.cancel_requested:
        return {"execution": ex.to_dict(), "cancelling": True}

    project_dir = rt._resolve_project_dir(project)
    # L4/§15：先确保 manifest 落盘（commit 与 manifest 计算间崩溃 -> 缺失且已提交时从 canonical +
    # 批准 ops 确定性重算），再过 §68 gate。避免永久 blocked 无重算路径。
    manifest_d = _ensure_manifest(rt, project, ex)
    if not manifest_d:
        raise AgentError("PRODUCTION_PREREQUISITE_FAILED",
                         "manifest 不存在且无法重算，无法 character sync（§68/L4）")
    _assert_agnes_prerequisites(ex, store, project_dir)   # §68：失败 -> 0 Agnes（manifest 已就位）
    manifest = ProductionExecutionManifest.from_dict(manifest_d)
    chars_to_sync = [n for n, a in manifest.character_actions.items()
                     if a in ("regenerate", "generate_new")]
    synced = {r.get("character_name") for r in ex.character_sync}
    pending = [c for c in chars_to_sync if c not in synced]

    gen = view_generator if view_generator is not None else (
        lambda cn: _real_character_view_generator(rt, project, execution_id, cn))   # §103 default
    for char_name in pending:
        # AI-7C-H §六：每角色开始前边界检查取消（reload-latest；guard 在落盘时兜底）
        ex = store.get_execution(execution_id)
        if ex is None or ex.status in EXEC_TERMINAL:
            return {"execution": ex.to_dict(), "idempotent": True}
        if ex.cancel_requested:
            break   # 不启动下一个角色（已开始单元由下方 record 自然收尾）
        views = await gen(char_name)   # dict[view_name, Asset|dict]（已开始最小单元，自然结束 §六）
        if not views:
            raise AgentError("CHARACTER_SYNC_FAILED", f"角色 {char_name} view 生成返回空")
        # §30 reload-latest + project_write_lock 短临界区：views 仅 image refs，不改 creative sig
        async with project_write_lock(project):
            pstore = ProjectStore(project_dir)
            p = pstore.load()
            ch = next((c for c in p.characters if c.name == char_name), None)
            if ch is None:
                raise AgentError("CHARACTER_SYNC_FAILED", f"角色 {char_name} 不存在")
            ch.views = {k: (Asset(**v) if isinstance(v, dict) else v)
                        for k, v in views.items()}
            pstore.save(p)
        # AI-7C-H §五：guarded record——已开始单元的真实完成记录（cancel_requested 仍可记录；
        # 终态则拒绝），不再 stale 整对象覆盖
        def _rec(latest, _cn=char_name, _views=views):
            latest.character_sync.append({"character_name": _cn, "status": "completed",
                                          "views": sorted(_views.keys()),
                                          "completed_at": _now()})
        latest, applied = await mutate_execution_guarded(
            rt, project, execution_id, _rec, policy="record")
        if not applied:
            return {"execution": latest.to_dict(), "idempotent": True}   # 终态：停止推进
        ex = latest

    # AI-7C-H §六：进入 planning 前边界检查取消
    ex = store.get_execution(execution_id)
    if ex is None or ex.status in EXEC_TERMINAL:
        return {"execution": ex.to_dict(), "idempotent": True}
    if ex.cancel_requested:
        return {"execution": ex.to_dict(), "cancelling": True}

    def _adv(latest):
        latest.stage = "production_planning"   # AI-7C-5 接力（character refs ready，§51）
    latest, applied = await mutate_execution_guarded(
        rt, project, execution_id, _adv, policy="advance")
    if not applied:
        return {"execution": latest.to_dict(), "cancelling": True}
    return {"execution": latest.to_dict(), "character_synced": len(pending)}


# ============ AI-7C-5b affected-scene planning + targeted run（§53/§54/§59）============
async def run_production_planning(rt: AgentRuntime, project: str, execution_id: str, *,
                                  plan_creator=None, run_starter=None) -> dict:
    """§53/§54/§59：affected-scene planning + all-plan-before-production gate + targeted run。
    plan_creator: ``async (scene_id) -> {plan_id, scene_id, status, shot_ids}``（默认真路径
    plan_production_business+approve_plan_business，需 Agnes，§103 接通；测试注入 fake）。
    run_starter: ``async (manifest) -> {run_id}``（默认真 ProjectProductionManager.start_run
    带 target_scene_ids/shot_actions，§59；测试注入 fake）。

    流程：读 manifest.planner_scene_ids -> 每 scene plan_creator -> §54 all-plan gate
    （全 approved 才进 production）-> §59 创建 targeted run（target_scene_ids + shot_actions）
    -> production_run_id + planning records + stage=production_prepare。
    §57 scope-expansion 是 run-time 关切（continuity 依赖在执行期增长 -> waiting_user），此处不判。"""
    store = _store(rt, project)
    ex = store.get_execution(execution_id)
    if ex is None:
        raise AgentError("EXECUTION_NOT_FOUND", f"execution 不存在: {execution_id}")
    if ex.project_name != project:
        raise AgentError("EXECUTION_NOT_FOUND", "execution 不属于该项目")
    if ex.status in EXEC_TERMINAL or ex.status == "blocked":
        return {"execution": ex.to_dict(), "idempotent": True}
    if ex.stage != "production_planning":
        return {"execution": ex.to_dict(), "idempotent": True}
    # AI-7C-H §六：进入 planning 前已请求取消 → 不推进新工作（§四），交收敛路径
    if ex.cancel_requested:
        return {"execution": ex.to_dict(), "cancelling": True}

    manifest_d = _ensure_manifest(rt, project, ex)
    if not manifest_d:
        raise AgentError("PRODUCTION_PREREQUISITE_FAILED",
                         "manifest 不存在且无法重算，无法 planning（§68/L4）")
    manifest = ProductionExecutionManifest.from_dict(manifest_d)
    scene_ids = list(manifest.planner_scene_ids)
    if not scene_ids:
        # 无受影响 scene 需重规划：直接进 production_prepare（creative-only 已在 done 不应到此处）
        def _adv0(latest):
            latest.stage = "production_prepare"
        latest, applied = await mutate_execution_guarded(
            rt, project, execution_id, _adv0, policy="advance")
        if not applied:
            return {"execution": latest.to_dict(), "cancelling": True}
        return {"execution": latest.to_dict(), "planned_scenes": 0}

    # ---- AI-7C-H F-2：durable per-scene planning checkpoint ----
    # 每个 scene plan 实际产出（plan_creator 返回 approved）后**立即**持久化进 ex.planning
    # （scene_id + plan_id + status），再进入下一 scene。重启 re-entry 跳过已完成的 scene
    # （planner LLM 不重复调用），只续跑未完成的。不依赖内存 set（§21 原 bug：s01 完成、
    # s02 崩溃、重启后 s01 planner 重跑——终态正确但外部调用重复）。
    done_records = {p.get("scene_id"): p for p in (ex.planning or [])
                    if p.get("status") == "approved" and p.get("plan_id")}
    creator = plan_creator or (lambda sid: _real_plan_creator(rt, project, ex, sid))
    plans: dict = {}
    for sid in scene_ids:
        if sid in done_records:
            # 已持久化完成的 scene plan：completed + still valid（plan_id 引用在案）→ 复用/跳过
            plans[sid] = {"plan_id": done_records[sid].get("plan_id"),
                          "scene_id": sid, "status": "approved"}
            continue
        # AI-7C-H §六：每 Scene 开始前边界检查取消（reload-latest；guard 落盘时兜底）
        ex = store.get_execution(execution_id)
        if ex is None or ex.status in EXEC_TERMINAL:
            return {"execution": ex.to_dict(), "idempotent": True}
        if ex.cancel_requested:
            return {"execution": ex.to_dict(), "cancelling": True}   # 不规划下一个 Scene
        plan = await creator(sid)
        plans[sid] = plan
        # F-2：单 scene 完成即落盘（崩溃窗口只丢当前 scene，之前的 planner 结果可复用）。
        # AI-7C-H §五：guarded record（cancel_requested 仍可记录真实完成；终态拒绝）
        if (plan or {}).get("status") == "approved":
            def _ckpt(latest, _sid=sid, _plan=plan):
                latest.planning = list(latest.planning or []) + [
                    {"scene_id": _sid, "plan_id": (_plan or {}).get("plan_id"),
                     "status": "approved"}]
            latest, applied = await mutate_execution_guarded(
                rt, project, execution_id, _ckpt, policy="record")
            if not applied:
                return {"execution": latest.to_dict(), "idempotent": True}
            ex = latest

    # §54 all-plan-before-production gate：全 approved 才放行
    unapproved = [sid for sid, p in plans.items()
                  if (p or {}).get("status") != "approved"]
    if unapproved:
        def _blk(latest, _un=unapproved):
            latest.status = "blocked"; latest.stage = "production_planning"
            latest.blocker = {"code": "PRODUCTION_PLAN_INVALID",
                              "unapproved": _un,
                              "message": f"{len(_un)} 个 plan 未批准（§54 all-plan gate）"}
        latest, applied = await mutate_execution_guarded(
            rt, project, execution_id, _blk, policy="advance")
        if not applied:
            return {"execution": latest.to_dict(), "cancelling": True}   # 取消优先，不误报受阻
        return {"execution": latest.to_dict(), "blocked": True,
                "blocker_code": "PRODUCTION_PLAN_INVALID"}

    # §59 + AI-7C-H §七 run-start 临界区：最终 cancel 检查 + 创建 ProductionRun + run_id
    # 关联，与 cancel_execution 同一把 project_write_lock（全本地磁盘操作，本 hotfix 唯一
    # 允许的锁内 run 创建例外）。目标：cancel 先得锁 → 不创建 run（情况 A）；run starter
    # 先得锁 → run_id 关联落盘后 cancel 能精确找到 R1 并传播取消（情况 B）。绝不孤立 R1。
    starter = run_starter or (lambda m: _real_run_starter(rt, project, ex, m))
    async with project_write_lock(project):
        latest = store.get_execution(execution_id)
        if latest is None:
            raise AgentError("EXECUTION_NOT_FOUND", f"execution 不存在: {execution_id}")
        if latest.status in EXEC_TERMINAL:
            return {"execution": latest.to_dict(), "idempotent": True}
        if latest.cancel_requested:
            return {"execution": latest.to_dict(), "cancelling": True}   # 情况 A：不创建 run
        run = await starter(manifest)
        latest.production_run_id = (run or {}).get("run_id")
        # F-2：ex.planning 已按 scene 增量落盘（见上方 checkpoint），此处不再整体覆写——
        # 保持崩溃恢复语义：已记录的 scene 记录原样保留。
        latest.stage = "production_prepare"   # AI-7C 等待 production 执行（真 run 在 §103 acceptance 跑）
        latest.updated_at = _now()
        store.save_execution(latest)   # 情况 B：run_id 关联落盘后才释放锁
    return {"execution": latest.to_dict(), "planned_scenes": len(scene_ids),
            "production_run_id": latest.production_run_id}


async def _real_plan_creator(rt: AgentRuntime, project: str, ex: ChangeExecutionSession,
                             scene_id: str) -> dict:
    """真路径：plan_production_business（src.ai，async，需 Agnes Planner LLM）+ approve_plan_business
    （routers.production_planner，§55 复用既有 auto-approve 语义）。§103 acceptance 接通。
    返回 {plan_id, scene_id, status, shot_ids}。"""
    from src.ai.production_planner import plan_production_business   # async
    from routers.production_planner import approve_plan_business    # 同步
    from deps import get_agnes_client, get_project_manager
    pdir = get_project_manager().get_project_dir(project)
    project_obj = ProjectStore(pdir).load()
    scene = next((s for s in project_obj.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise AgentError("PRODUCTION_PLAN_INVALID", f"scene {scene_id} 不存在")
    plan = await plan_production_business(project_obj, scene, project, get_agnes_client())
    approve_plan_business(plan, scene)   # §55：AI-7C affected-production 等价 production 授权
    # 🔴 持久化新 plan 到 production_plans.json（否则 start_run preflight 读旧 plan → PLAN_STALE）
    from routers.production_planner import _read_plans, _write_plans
    plans = _read_plans(pdir)
    plans[scene_id] = plan
    _write_plans(pdir, plans)
    return {"plan_id": plan.plan_id, "scene_id": scene_id, "status": plan.status,
            "shot_ids": [ps.shot_id for ps in plan.shots]}


async def _real_run_starter(rt: AgentRuntime, project: str, ex: ChangeExecutionSession,
                            manifest: ProductionExecutionManifest) -> dict:
    """真路径：ProjectProductionManager.start_run 带 target_scene_ids/shot_actions（§59）。§103 接通。"""
    from src.ai.project_autopilot import get_project_autopilot_manager
    mgr = get_project_autopilot_manager()
    run = await mgr.start_run(project, respect_locked=True,
                              target_scene_ids=list(manifest.planner_scene_ids),
                              target_shot_ids=list(manifest.affected_shot_ids or []) or None,
                              shot_actions=manifest.shot_actions,
                              parent_change_execution_id=ex.execution_id)
    return {"run_id": run.run_id}


async def _real_character_view_generator(rt: AgentRuntime, project: str,
                                          execution_id: str, char_name: str) -> dict:
    """§46 真路径：提交 character_views Job（JobQueue，origin=execution_id + stage_key）+ poll 至终态。
    handler（handler_character_views，复用 routers/characters.py chain §44）由 composition root / 驱动
    注册为 JOB_HANDLERS["character_views"]。返回 {view_name: Asset}（从 Character.views 重载）。

    AI-7C-H K1：提交时从**提交后 canonical** 计算 appearance_sig（description_en + art_style 的
    确定性哈希）随 job 持久化。handler 据此区分「resume 同一生成（done views 跳过）」与
    「新批准外观修订（旧 done views 是陈旧输入，必须真实重新生成）」。"""
    import asyncio as _aio
    from ..character_views_handler import character_appearance_sig
    jq = rt._resolve_job_queue_or_none()
    if jq is None:
        raise AgentError("CHARACTER_SYNC_FAILED", "JobQueue 不可用，无法 character sync（§46）")
    pdir = rt._resolve_project_dir(project)
    stage_key = f"char_views:{execution_id}:{char_name}"
    existing = jq.find_by_origin(execution_id, stage_key)
    if existing is None:
        p = ProjectStore(pdir).load()
        ch = next((c for c in p.characters if c.name == char_name), None)
        if ch is None:
            raise AgentError("CHARACTER_SYNC_FAILED",
                             f"角色 {char_name} 不存在于 canonical（K1 提交前校验）")
        # Phase B §3.2：外观签名与 Agent 路径同式——ProjectVisualProfile 指纹参与
        # （profile 变更 ⇒ 旧 views 陈旧，须重生成；读取失败/unspecified 宽容降级为 ""）。
        from ..character_designer import _visual_profile_fingerprint_or_empty
        sig = character_appearance_sig(ch.description_en, p.style.art_style or "anime",
                                       _visual_profile_fingerprint_or_empty(pdir))
        jq.submit(project, "character_views", shot_id=None,
                  origin_run_id=execution_id, autopilot_stage_key=stage_key,
                  character_name=char_name, appearance_sig=sig)
    deadline = _now() + 600
    while _now() < deadline:
        job = jq.find_by_origin(execution_id, stage_key)
        if job is None:
            raise AgentError("CHARACTER_SYNC_FAILED", f"character_views job 未找到: {char_name}")
        if job.state in ("done", "failed", "cancelled"):
            break
        await _aio.sleep(1.0)
    job = jq.find_by_origin(execution_id, stage_key)
    if job is None or job.state != "done":
        raise AgentError("CHARACTER_SYNC_FAILED",
                         f"角色 {char_name} view 生成失败: {getattr(job,'state','?')} {getattr(job,'error','')}")
    # handler 已写 Character.views；reload 读之
    ch = next((c for c in ProjectStore(pdir).load().characters if c.name == char_name), None)
    return dict(ch.views) if ch else {}


async def cancel_execution(rt: AgentRuntime, project: str, execution_id: str) -> dict:
    """§38 + AI-7C-H cancellation 语义：取消是请求，不是瞬间完成（§三）。

    提交前（canonical 未动）-> 立即终态 cancelled（§38 安全取消）。
    提交后（committed creative 保留）-> cancel_requested=True，不再推进新工作；
    owned ProductionRun 仍活跃时取消**精确传播**到该 run（§八，run_id 关联），
    Execution 保持 running+cancel_requested（前端显示「正在停止…」），由 stop watcher /
    读取惰性收敛 / 在飞任务收尾驱动收敛到 cancelled（§八：不提前宣称已取消）。
    不意味 undo（§38）：已落盘资产保留。
    状态翻转在 project_write_lock 短临界区内 reload-latest（§四/§五：与 §七 run-start
    临界区同锁，防 stale 复活/孤立 run）。幂等。"""
    store = _store(rt, project)
    async with project_write_lock(project):
        ex = store.get_execution(execution_id)
        if ex is None:
            raise AgentError("EXECUTION_NOT_FOUND", f"execution 不存在: {execution_id}")
        if ex.project_name != project:
            raise AgentError("EXECUTION_NOT_FOUND", "execution 不属于该项目")
        if ex.status in EXEC_TERMINAL:
            return {"execution": ex.to_dict(), "idempotent": True}
        cc = ex.canonical_commit
        committed = bool(cc and cc.get("status") == "committed")
        ex.cancel_requested = True
        if not committed:
            # 提交前：安全取消，canonical 未动（§38）。立即终态。
            ex.status = "cancelled"
        # 提交后：保持 running + cancel_requested（「正在停止…」），由收敛路径处理（§三/§八）
        ex.updated_at = _now()
        store.save_execution(ex)
    if committed:
        # §八：传播取消到 owned run（幂等）+ watcher 驱动收敛；
        # 无在飞工作且 run 非活跃时 _converge_cancel_once 立即收敛 cancelled
        await _converge_cancel_once(rt, project, execution_id)
        _spawn_stop_watcher(rt, project, execution_id)
    final = store.get_execution(execution_id)
    return {"execution": (final or ex).to_dict(), "cancelled": True,
            "creative_committed": committed}


# ============ M1：post-commit 后台 kickoff（§3 polling-first，不阻塞 HTTP）============
# 进程内 in-flight 记账：execution_id -> asyncio.Task（强引用防 GC）。重启后清空=正确
# （无任务存活 -> resume 重派发；每步函数自带 stage guard + per-char/per-scene 幂等，不双写 §46/§54）。
_INFLIGHT_POSTCOMMIT: dict = {}


def _maybe_kickoff_postcommit(rt: AgentRuntime, project: str, ex) -> bool:
    """若 ex 处于 post-commit 阶段（character_sync/production_planning）且无在飞任务，
    后台 kickoff _run_postcommit_to_completion。返回是否新 kickoff（已 in-flight/终态/阻塞→False）。
    §3 polling-first：HTTP 立即返回，前端轮询 execution 状态推进。
    AI-7C-H §四/§九：cancel_requested 的 execution 不派发新 post-commit 工作——
    取消优先，由收敛路径处理（owned run 活跃时 watcher/读取惰性收敛兜底）。"""
    if ex.status in EXEC_TERMINAL or ex.status == "blocked":
        return False
    if ex.cancel_requested:
        return False   # 正在停止：不推进新工作（§四），不 kickoff
    if ex.stage not in ("character_sync", "production_planning"):
        return False
    if ex.execution_id in _INFLIGHT_POSTCOMMIT:
        return False
    task = asyncio.create_task(_run_postcommit_to_completion(rt, project, ex.execution_id))
    _INFLIGHT_POSTCOMMIT[ex.execution_id] = task
    task.add_done_callback(lambda _t, eid=ex.execution_id: _INFLIGHT_POSTCOMMIT.pop(eid, None))
    return True


async def _run_postcommit_to_completion(rt: AgentRuntime, project: str, execution_id: str) -> None:
    """后台串行跑 character_sync → production_planning → production_prepare。
    每步函数自带 stage guard + 幂等，故重启后重派发不双写。失败 -> execution blocked（保留 blocker）。
    AI-7C-H：每步前复查 cancel_requested（§六边界）；blocked 落盘经 guarded mutation
    （取消/终态优先，不覆盖 cancelled）；任务收尾时驱动取消收敛（§三/§八）。"""
    store = _store(rt, project)
    try:
        ex = store.get_execution(execution_id)
        if (ex and ex.stage == "character_sync" and ex.status not in EXEC_TERMINAL
                and ex.status != "blocked" and not ex.cancel_requested):
            await run_character_sync(rt, project, execution_id)
        ex = store.get_execution(execution_id)
        if (ex and ex.stage == "production_planning" and ex.status not in EXEC_TERMINAL
                and ex.status != "blocked" and not ex.cancel_requested):
            await run_production_planning(rt, project, execution_id)
    except AgentError as e:
        await _mark_postcommit_blocked(rt, project, execution_id, e.code, str(e)[:300])
        logger.warning("AI-7C post-commit task failed (exec=%s): %s %s", execution_id, e.code, e)
    except Exception as e:  # noqa: BLE001
        await _mark_postcommit_blocked(rt, project, execution_id,
                                       "POSTCOMMIT_TASK_FAILED", str(e)[:300])
        logger.exception("AI-7C post-commit task error (exec=%s): %s", execution_id, e)
    finally:
        # §三/§八：本任务收尾时若已请求取消且无其他在飞任务/活跃 owned run → 收敛 cancelled
        try:
            await _converge_cancel_once(rt, project, execution_id)
        except Exception:  # noqa: BLE001
            logger.exception("AI-7C-H cancel 收敛失败 (exec=%s)", execution_id)


async def resume_execution(rt: AgentRuntime, project: str, execution_id: str) -> dict:
    """§83 resume：从当前 stage 继续推进。M1 stage 分发：
      pre-commit（revalidate/workspace_create/creative_replay/workspace_verify/canonical_commit）
        → 同步 start_replay_commit（本地快操作：replay+commit）；commit 后若到 post-commit → 接续 kickoff；
      post-commit（character_sync/production_planning）→ 后台 kickoff（§3 polling-first，HTTP 不阻塞）；
      production_prepare/production/compose → 既有 ProjectProductionRun 基础设施接管（§58/§71），幂等返回。
    blocked（stale/preview-mismatch/active-production）不可 resume（§81 stale 不可 rebasing）。"""
    store = _store(rt, project)
    ex = store.get_execution(execution_id)
    if ex is None:
        raise AgentError("EXECUTION_NOT_FOUND", f"execution 不存在: {execution_id}")
    if ex.project_name != project:
        raise AgentError("EXECUTION_NOT_FOUND", "execution 不属于该项目")
    if ex.status in EXEC_TERMINAL:
        return {"execution": ex.to_dict(), "idempotent": True}
    if ex.status == "blocked":
        # §81：blocked（尤其 stale）不可 rebasing resume；返回 blocked 态供 UI 说明
        return {"execution": ex.to_dict(), "blocked": True,
                "blocker_code": (ex.blocker or {}).get("code")}
    # AI-7C-H §六：正在停止（cancel_requested 非终态）→ 不可 resume 推进新工作（§四）。
    # 尽力收敛（无在飞工作/无活跃 owned run 时直接收敛 cancelled），返回真实状态。
    if ex.cancel_requested:
        await maybe_converge_cancellation(rt, project, execution_id)
        ex2 = store.get_execution(execution_id)
        return {"execution": (ex2 or ex).to_dict(), "cancelling": True, "idempotent": True}
    # pre-commit：同步推进 create+replay+commit
    if ex.stage in ("revalidate", "workspace_create", "creative_replay",
                    "workspace_verify", "canonical_commit"):
        res = await start_replay_commit(rt, project, ex.change_session_id,
                                        ex.plan_id, ex.plan_version, ex.production_mode)
        ex2 = (res.get("execution") or {})
        # commit 后到 post-commit → 接续后台 kickoff（一次 resume 驱动到 production_prepare）
        if (not res.get("blocked") and ex2.get("status") != "blocked"
                and ex2.get("stage") in ("character_sync", "production_planning")):
            ex_obj = store.get_execution(ex2.get("execution_id") or execution_id)
            if ex_obj is not None:
                _maybe_kickoff_postcommit(rt, project, ex_obj)
        return res
    # post-commit：后台 kickoff（§3 polling-first）
    if ex.stage in ("character_sync", "production_planning"):
        kicked = _maybe_kickoff_postcommit(rt, project, ex)
        return {"execution": ex.to_dict(), "kicked_off": kicked, "idempotent": not kicked}
    # production_prepare/production/compose：既有 ProjectProductionRun 接管，幂等返回
    return {"execution": ex.to_dict(), "idempotent": True}


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
def recover_executions(project_dir, project_name: str, *, rt=None) -> int:
    """§73/§96：启动时扫该项目的 active execution（非终态），记账待恢复。
    AI-7C-1：仅发现 + 记账（workspace replay/commit/production 在后续 gate）。

    AI-7C-H（Event B）：对处于 post-commit 阶段（character_sync/production_planning）且
    非 blocked/终态 的 execution，启动即后台 kickoff `_run_postcommit_to_completion`。
    批准后的执行是已授权的生产行为（§7 批准即执行到完成；§27 后端重启用既有 durable
    恢复机制续跑），重启不应要求用户重新确认。每步函数自带 stage guard + per-char/
    per-scene 幂等（§46/F-2），进程内 `_INFLIGHT_POSTCOMMIT` 防双派发。
    rt 仅供测试注入；缺省用全局 runtime。"""
    store = ChangeProtocolStore(project_dir, project_name)
    n = 0
    kickoff_targets: List[ChangeExecutionSession] = []
    for ex in store.list_executions():
        if ex.status in EXEC_TERMINAL:
            continue
        n += 1
        # §35：in-flight COMMIT_INTENT 崩溃对账（startup discovery 一部分）
        cc = ex.canonical_commit
        if cc and cc.get("status") == "commit_intent":
            _reconcile_commit(ex, store, project_dir)
            store.save_execution(ex)
        # AI-7C-H §九：重启遇「正在停止」（cancel_requested 非终态）→ 完成取消路径，
        # 不按普通 active execution 恢复 post-commit 续跑（旧计划不复活）。
        if ex.cancel_requested and ex.status not in EXEC_TERMINAL:
            run = _get_owned_run(project_name, ex.production_run_id) if ex.production_run_id else None
            if _run_is_active(run):
                # owned run 仍活跃：继续请求/恢复其 cancellation + watcher 驱动收敛
                # （无运行 loop 时由 GET 读取惰性收敛兜底 §八）
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        _propagate_cancel_to_owned_run(project_name, ex.production_run_id))
                except RuntimeError:
                    pass
                _spawn_stop_watcher(rt, project_name, ex.execution_id)
                logger.info("AI-7C-H recover: exec=%s 正在停止（owned run %s 活跃），"
                            "继续收敛取消", ex.execution_id, ex.production_run_id)
            else:
                # 无活跃子生产：直接收敛 cancelled（§四：不复活）
                ex.status = "cancelled"; ex.updated_at = _now()
                store.save_execution(ex)
                logger.info("AI-7C-H recover: exec=%s 收敛为 cancelled（取消请求恢复）",
                            ex.execution_id)
            continue
        # Event B：post-commit 活跃执行 → 记录待 kickoff（commit 已落，续跑安全幂等）
        if (ex.status not in EXEC_TERMINAL and ex.status != "blocked"
                and ex.stage in ("character_sync", "production_planning")):
            kickoff_targets.append(ex)
        logger.info("AI-7C recover: %s exec=%s plan=%s v%s status=%s stage=%s",
                    project_name, ex.execution_id, ex.plan_id,
                    ex.plan_version, ex.status, ex.stage)
    if n:
        logger.info("AI-7C: %s 发现 %d 个 active execution 待恢复", project_name, n)
    if kickoff_targets:
        try:
            if rt is None:
                from ..runtime import get_agent_runtime
                rt = get_agent_runtime()
            for ex in kickoff_targets:
                _maybe_kickoff_postcommit(rt, project_name, ex)
            logger.info("AI-7C-H: %s kickoff %d 个 post-commit execution（启动续跑）",
                        project_name, len(kickoff_targets))
        except Exception as e:   # noqa: BLE001
            # kickoff 失败不阻断启动；用户/前端 resume 仍可续跑（幂等）
            logger.warning("AI-7C-H startup kickoff failed (%s): %s", project_name, e)
    return n
