# src/agents/shot_producer_capability.py
# AI-6F.0 Shot Producer 长执行能力执行器（async，§6/§17）。
#
# 链路（§17 final path 的 AI-6F.0 形态——「existing business helper」暂为 deterministic
# fake handler，其余路径全真实）：
#   Producer → AgentTask(shot.first_frame/last_frame/video, execute)
#           → 本执行器（幂等检查 → 提交 fake Job 到 **现有** JobQueue）
#           → execution_ref 持久化于 AgentTask（§5）
#           → delegate 返回（task=running，HTTP 不等 Job 完成 §4）
#           → settlement bridge 据 job_id 结算（§10/§12）
#
# 🔴 幂等（§6）：执行器**先**检查 task 已有的 execution_ref。若 linked Job 仍非终态 →
#   reattach（返回 RUNNING，**不**重提交）；若已终态 → 从既有结果结算（**不**重提交）；
#   若 Job 已消失（被清理）→ 落到 submit（罕见，ref 失效）。覆盖 HTTP retry / browser
#   retry / backend restart / settlement retry / SSE reconnect 全部场景——同一 AgentTask
#   绝不重复提交同一 production operation。
#
# 🔴 不重写生成逻辑（§17/§21）：fake handler 在 fake_shot_handlers.py；真实生成在
#   routers/shots.py 的 JOB_HANDLERS。本文件不 import routers/*，不直连 Agnes。
#
# Import 纪律：只 import src.core / src.ai / 本包 models + fake_shot_handlers。不 import
#   routers/* / deps。client / project_loader / project_dir / job_queue 由 AgentRuntime 注入。
from __future__ import annotations
import time
from pathlib import Path
from typing import Any, Callable, Optional, Dict

from .models import AgentStructuredResult, ResultStatus
from .fake_shot_handlers import FAKE_KIND_FOR_CAPABILITY, PHASE_FOR_CAPABILITY

# JobState 终态集合（对齐 src/core/job_queue.py JobState，避免 import 产生 agents→core.job_queue
# 的硬依赖——本处只需字符串常量）
_JOB_TERMINAL_DONE = "done"
_JOB_TERMINAL_FAIL = {"failed", "cancelled"}
_JOB_NON_TERMINAL = {"queued", "running"}


def _find_shot(project, shot_id: str):
    return project.get_shot(shot_id) if hasattr(project, "get_shot") else None


def _progress_data(ref: Dict[str, Any], job_state: str, job=None) -> Dict[str, Any]:
    """从 durable 执行态派生进度快照（§8：确定性 production phase，**不**造 LLM 百分比）。
    单 Shot 单 Job：completed_items 0/1 由 job 终态决定；progress_pct 取 job.progress。"""
    return {
        "job_id": ref.get("job_id"),
        "kind": ref.get("kind"),
        "phase": ref.get("phase"),
        "shot_id": ref.get("shot_id"),
        "job_state": job_state,
        "progress_pct": int(getattr(job, "progress", 0) or 0) if job else 0,
        "completed_items": 1 if job_state == _JOB_TERMINAL_DONE else 0,
        "total_items": 1,
        "submitted_at": ref.get("submitted_at"),
    }


def _settled_result_from_job(ref: Dict[str, Any], job, *, phase: str) -> AgentStructuredResult:
    """linked Job 已终态 → 从既有结果结算（§6：不重提交）。"""
    state = job.state
    data = _progress_data(ref, state, job)
    if state == _JOB_TERMINAL_DONE:
        return AgentStructuredResult(
            status=ResultStatus.COMPLETED.value,
            summary=f"镜头 {ref.get('shot_id')} 的 {phase} Job 已完成（reattach 发现既有终态，未重提交）。",
            artifact_refs=[{"type": "job", "id": ref.get("job_id")},
                           {"type": "shot", "id": ref.get("shot_id")}],
            data=data,
        )
    # failed / cancelled
    return AgentStructuredResult(
        status=ResultStatus.FAILED.value,
        summary=f"镜头 {ref.get('shot_id')} 的 {phase} Job 已 {state}（reattach 发现既有终态，未重提交）。",
        artifact_refs=[{"type": "job", "id": ref.get("job_id")}],
        warnings=[f"job {state}: {getattr(job, 'error', '') or ''}"[:500]],
        data=data,
    )


async def execute_shot_production(
    client: Optional[Any],                       # fake 路径不调 Agnes；保留签名供 AI-6F 真实接通
    project_loader: Callable[[], Any],
    project_dir: Path,
    project_name: str,
    scope_type: str,
    scope_id: str,
    instruction: str,
    capability_id: str,
    job_queue: Any,
    existing_ref: Optional[Dict[str, Any]] = None,
    fake_job_params: Optional[Dict[str, Any]] = None,
) -> AgentStructuredResult:
    """shot.first_frame/last_frame/video 执行器（AI-6F.0 fake durable bridge）。

    返回 AgentStructuredResult：
      status=RUNNING  → 已提交（或 reattach）Job，delegate 保持 task=running，HTTP 返回（§4）
      status=COMPLETED→ reattach 发现 linked Job 已 done，可直接结算（§6/§18-10）
      status=FAILED   → scope/shot 无效 blocker，或 reattach 发现 linked Job 已 failed（§6/§18-11）
      status=NOT_CONFIGURED → JobQueue 不可用（软失败）
    """
    # §6 scope = shot
    if scope_type != "shot":
        return AgentStructuredResult(
            status=ResultStatus.FAILED.value,
            summary=f"shot 执行能力仅支持 shot 级 scope（收到 {scope_type}）。",
            warnings=[f"invalid scope_type: {scope_type}"],
            data={"scope_type": scope_type, "scope_id": scope_id})

    if capability_id not in FAKE_KIND_FOR_CAPABILITY:
        return AgentStructuredResult(
            status=ResultStatus.NOT_IMPLEMENTED.value,
            summary=f"能力 {capability_id} 非 shot 长执行能力。",
            data={"capability_id": capability_id})

    fake_kind = FAKE_KIND_FOR_CAPABILITY[capability_id]
    phase = PHASE_FOR_CAPABILITY[capability_id]

    # JobQueue 不可用 → 软失败（对齐 runtime _resolve_client_or_none 语义）
    if job_queue is None:
        return AgentStructuredResult(
            status=ResultStatus.NOT_CONFIGURED.value,
            summary="[shot 执行未就绪：JobQueue 不可用（服务启动中）]",
            data={"scope_id": scope_id, "phase": phase})

    # §6 幂等：先检查 task 已有 execution_ref（reattach / settle-from-existing，不重提交）
    if existing_ref and existing_ref.get("job_id"):
        existing_job = job_queue.get(existing_ref["job_id"])
        if existing_job is not None:
            st = getattr(existing_job, "state", None)
            if st in _JOB_NON_TERMINAL:
                # reattach：返回 RUNNING + 刷新进度，**不** submit（§6/§18-7/8）
                return AgentStructuredResult(
                    status=ResultStatus.RUNNING.value,
                    summary=f"镜头 {scope_id} 的 {phase} Job 进行中（{st}），已 reattach，未重提交。",
                    artifact_refs=[{"type": "job", "id": existing_ref["job_id"]},
                                   {"type": "shot", "id": scope_id}],
                    data=_progress_data(existing_ref, st, existing_job))
            # 终态 → 从既有结果结算（§6/§18-10/11）
            return _settled_result_from_job(existing_ref, existing_job, phase=phase)
        # job None（被 clear_finished 清理）→ ref 失效，落到 submit（罕见，记 warning）

    # ---- load project + resolve shot（§16：shot 不存在 → blocker）----
    try:
        project = project_loader()
    except Exception as e:
        return AgentStructuredResult(
            status=ResultStatus.FAILED.value,
            summary=f"项目不可用：{str(e)[:200]}",
            warnings=[str(e)[:500]])

    shot = _find_shot(project, scope_id)
    if shot is None:
        return AgentStructuredResult(
            status=ResultStatus.FAILED.value,
            summary=f"未找到镜头 {scope_id}。",
            warnings=[f"unknown shot: {scope_id}"],
            data={"scope_id": scope_id,
                  "blocker": "needs_upstream_change",
                  "upstream_owner": "director"})

    # §17 minimum infra：读取 approved Production Plan（wired，证明后续接通点）。
    # AI-6F.0 fake 路径**不**因缺 Plan 硬阻塞（fake 不执行真实创意策略，仅证 lifecycle）；
    # 缺 Plan 记 warning，真实接通（AI-6F）将按 shot_producer 契约 §16 返回结构化 blocker。
    plan_warning = None
    try:
        from src.ai.production_planner import read_plans
        plans = read_plans(project_dir)
        # shot 所属 scene_id
        scene_id = getattr(shot, "scene_id", None) or scope_id.rsplit("_c", 1)[0]
        plan = plans.get(scene_id)
        if plan is None:
            plan_warning = f"场景 {scene_id} 无 approved Production Plan（AI-6F.0 fake 继续；真实接通将 blocker）"
    except Exception as e:
        plan_warning = f"Production Plan 读取异常（fake 继续）：{str(e)[:120]}"

    # ---- 提交 fake Job 到现有 JobQueue（§7 deterministic fake handler）----
    params: Dict[str, Any] = {"_fake_outcome": "done", "_fake_steps": 3, "_fake_step_sleep": 0.05}
    if fake_job_params:
        params.update(fake_job_params)
    # origin_task_id / agent_stage_key 仅作日志/未来 find_by_origin 用；主链接是 execution_ref.job_id（§5）
    try:
        job = job_queue.submit(project_name, fake_kind, scope_id, **params)
    except Exception as e:
        return AgentStructuredResult(
            status=ResultStatus.FAILED.value,
            summary=f"提交 {phase} Job 失败：{str(e)[:200]}",
            warnings=[str(e)[:500]],
            data={"scope_id": scope_id, "phase": phase})

    ref = {
        "job_id": job.job_id,
        "kind": fake_kind,
        "phase": phase,
        "shot_id": scope_id,
        "submitted_at": float(getattr(job, "created_at", time.time()) or time.time()),
    }
    warnings = []
    if plan_warning:
        warnings.append(plan_warning)
    data = _progress_data(ref, getattr(job, "state", "queued"), job)
    data["newly_submitted"] = True
    return AgentStructuredResult(
        status=ResultStatus.RUNNING.value,
        summary=(f"镜头 {scope_id} 的 {phase} Job 已提交（{fake_kind}），task 保持 running 等待 Job 终态后结算。"),
        artifact_refs=[{"type": "job", "id": job.job_id},
                        {"type": "shot", "id": scope_id}],
        warnings=warnings,
        suggested_next_actions=["轮询/刷新 task 状态", "Job 完成后自动结算"],
        data=data,
    )
