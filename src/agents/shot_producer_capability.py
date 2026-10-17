# src/agents/shot_producer_capability.py
# AI-6F.0 → AI-6F Shot Producer 长执行能力执行器（async，§6/§17）。
#
# AI-6F.0（fake durable bridge）已证明 lifecycle 语义。AI-6F 接通**真实**生成：
# 把 capability→kind 映射从 fake（agent_*）指向 routers/shots.py 的真实 JOB_HANDLERS
#（first_frame/last_frame/video），runtime/bridge/settlement 架构不变（§17 final path）。
#
# 链路（AI-6F real 形态）：
#   Producer → AgentTask(shot.first_frame/last_frame/video, execute)
#           → 本执行器（§4 Plan preflight → §16/§22 prereq → §35 activate_planned_shot →
#             §6 幂等检查 → 提交**真实** Job 到现有 JobQueue，origin_run_id=task_id 作 §10/§11 traceability）
#           → execution_ref 持久化于 AgentTask（§5）
#           → delegate 返回（task=running，HTTP 不等 Job 完成 §4）
#           → settlement bridge 据 job_id + origin_run_id 结算/reattach（§10/§12/§24）
#
# 🔴 幂等（§6/§8）：执行器先检查 task 已有 execution_ref。linked Job 仍非终态 → reattach
#   （返回 RUNNING，不重提交）。真实路径下即使 linked Job 已终态也返回 RUNNING 交 settlement
#   bridge 做 §24 artifact 校验后结算（不在此 shortcut-complete，避免绕过 §24）。fake 路径
#   保持 AI-6F.0 行为（reattach 发现终态 Job 从结果结算）。
#
# 🔴 不重写生成逻辑（§17/§21）：真实生成在 routers/shots.py 的 JOB_HANDLERS。本文件不 import
#   routers/* 的生成 handler，只在 **real 模式** 复用业务 helper（activate_planned_shot /
#   _effective_first_frame_url / _video_preflight / dependency_ready，§35「reuse 同一业务 helper」，
#   对齐 autopilot.py:681 的 lazy import 模式）。不直连 Agnes。fake 模式（real=False，默认）
#   保持 AI-6F.0 行为不变（测试用）。
#
# Import 纪律：fake 路径只 import src.core / src.ai / 本包。real 路径在函数内**惰性** import
#   routers.shots / src.ai.autopilot（避免循环 import；routers/autopilot 均不 top-level import src.agents）。
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

# AI-6F：真实 JOB_HANDLERS kind（routers/shots.py:649-650,1249 注册）。与 fake kind 字符串不冲突
#（fake 为 agent_* 前缀，JobQueue._route 视为轻任务走 light_q；真实 video 走 _video_q 串行 worker）。
REAL_KIND_FOR_CAPABILITY = {
    "shot.first_frame": "first_frame",
    "shot.last_frame": "last_frame",
    "shot.video": "video",
}


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
        # AI-6F §11/§20：透传 origin_task_id / video_id，runtime 据此持久化 execution_ref
        #（origin_task_id 作 artifact traceability；video_id 作 restart-recovery fallback 身份）。
        "origin_task_id": ref.get("origin_task_id"),
        "video_id": ref.get("video_id"),
    }


def _settled_result_from_job(ref: Dict[str, Any], job, *, phase: str) -> AgentStructuredResult:
    """linked Job 已终态 → 从既有结果结算（§6：不重提交）。fake 路径专用（real 路径交 settlement bridge）。"""
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


def _blocker(code: str, reason: str, *, scope_id: str, upstream: str = "production_planner",
             phase: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> AgentStructuredResult:
    """§4/§32 结构化 blocker：scope/plan/prereq 不满足 → FAILED + blocker_code + upstream_owner。
    不提交 Job、不调 Agnes（§16/§22）。"""
    data: Dict[str, Any] = {"scope_id": scope_id, "phase": phase,
                           "blocker_code": code, "upstream_owner": upstream, "reason": reason}
    if extra:
        data.update(extra)
    return AgentStructuredResult(
        status=ResultStatus.FAILED.value,
        summary=f"[{code}] {reason}",
        warnings=[reason[:500]],
        data=data)


# ============ AI-6F real 路径：Plan / prereq preflight（§4/§16/§22，不调 Agnes）============
def _resolve_plan(project, project_dir: Path, scope_id: str, shot):
    """§4：解析 approved Production Plan + planned shot。返回 (plan, planned, blocker_result)。
    缺 / draft / stale / planned-shot-missing → 结构化 blocker（§43）。"""
    from src.ai.production_planner import read_plans, is_plan_stale
    scene_id = getattr(shot, "scene_id", None) or scope_id.rsplit("_c", 1)[0]
    plans = read_plans(project_dir)
    plan = plans.get(scene_id)
    if plan is None:
        return None, None, _blocker("PRODUCTION_PLAN_MISSING",
            f"场景 {scene_id} 无 Production Plan", scope_id=scope_id, phase=None)
    if (getattr(plan, "status", "draft") or "draft") != "approved":
        return None, None, _blocker("PRODUCTION_PLAN_NOT_APPROVED",
            f"场景 {scene_id} Plan 状态 {plan.status}（需 approved）", scope_id=scope_id)
    scene = next((s for s in project.scenes if s.scene_id == scene_id), None)
    if scene is not None:
        stale = is_plan_stale(plan, scene)
        if stale:
            return None, None, _blocker("PRODUCTION_PLAN_STALE",
                f"场景 {scene_id} Plan 已过期（{stale}）", scope_id=scope_id)
    planned = next((p for p in (plan.shots or []) if p.shot_id == scope_id), None)
    if planned is None:
        return None, None, _blocker("PLANNED_SHOT_MISSING",
            f"Plan 中无镜头 {scope_id} 的规划条目", scope_id=scope_id)
    return plan, planned, None


def _phase_plan_check(capability_id: str, planned, scope_id: str):
    """§16：Plan 形状与 capability 相容性。first_frame 不对 inherit_previous；last_frame 仅 keyframes。"""
    if capability_id == "shot.first_frame":
        if planned.opening_source == "inherit_previous":
            return _blocker("NO_FIRST_FRAME_FOR_INHERIT",
                f"镜头 {scope_id} opening_source=inherit_previous（开场沿用来源末帧，不生成首帧）",
                scope_id=scope_id)
    elif capability_id == "shot.last_frame":
        if planned.link_mode != "keyframes":
            return _blocker("PRODUCTION_PLAN_INCOMPATIBLE",
                f"镜头 {scope_id} link_mode={planned.link_mode}（尾帧仅 keyframes 模式需要）",
                scope_id=scope_id)
    return None


def _apply_activate(project_loader: Callable[[], Any], project_dir: Path,
                    scope_id: str, planned) -> Optional[AgentStructuredResult]:
    """§35/§36：复用 routers.shots.activate_planned_shot，把 Plan 的 opening_source/link_mode/
    source_shot_id/source_version 应用到 shot（与现有 Executor 同一业务 helper，不重写、不另造定义）。
    reference/inherit 需来源 final_video + 末帧就绪（dependency_ready），不就绪 → blocker（不调 activate、不抛）。"""
    from src.core.store import ProjectStore
    from routers.shots import activate_planned_shot
    from src.ai.autopilot import dependency_ready
    if getattr(planned, "requires_previous_video_end", False):
        project = project_loader()
        if not dependency_ready(project, planned):
            return _blocker("PREFLIGHT_DEPENDENCY",
                f"镜头 {scope_id} 依赖来源镜头末帧未就绪（reference/inherit 需来源 final_video + video_end_frame）",
                scope_id=scope_id, upstream="shot_producer")
    store = ProjectStore(project_dir)
    project = project_loader()
    try:
        activate_planned_shot(store, project, scope_id, planned)
    except Exception as e:
        return _blocker("ACTIVATE_FAILED", f"应用 Plan 失败：{str(e)[:200]}",
            scope_id=scope_id, upstream="shot_producer")
    return None


def _phase_prereq_check(capability_id: str, project, shot, client, scope_id: str,
                        project_dir=None, reference_policy: str = "bound_required"
                        ) -> Optional[AgentStructuredResult]:
    """§16/§22：phase 特定生成前置（read-only）。缺 → blocker，不提交 Job、不调 Agnes。
    复用 routers/shots 的 _effective_first_frame_url/_video_preflight（§17：不重写）。"""
    if capability_id == "shot.last_frame":
        from routers.shots import _effective_first_frame_ready
        if not _effective_first_frame_ready(project, shot):
            return _blocker("PREFLIGHT_DEPENDENCY",
                f"镜头 {scope_id} 缺少已就绪首帧公网 URL（无法生成尾帧）",
                scope_id=scope_id, upstream="shot_producer")
    elif capability_id == "shot.video":
        # §22：Agent 侧仅 preflight 「明显浪费提交」的条件（eff 首帧就绪 + 未锁定）。
        # duration/frame_count/quota/keyframes-尾帧 等交由 _handler_video 内置 _video_preflight
        #（在 create_video 之前 raise → Job failed，0 真实 submissions，§22）。不在此重复 handler 逻辑（§17）。
        # Phase55 A（任务书 §3）：生产执行器走统一视频提交契约——BOUND 基线等价
        #（与 director 工具同一条合法请求路径；缺绑定/时长越界在提交前明确 blocker，
        # 不再排队后逐个失败）。
        if project_dir is not None:
            from src.generation.video_contract import (validate_video_submission,
                                                       policy_requires_binding)
            from src.shot_binding.service import ShotBindingService
            try:
                _validity = ShotBindingService(project_dir, project_dir.name).read_validity(scope_id)
            except Exception as e:   # noqa: BLE001 —— 读取失败按 UNBOUND 拒绝（fail closed）
                _validity = {"readiness": "UNBOUND",
                             "binding_state": "unbound",
                             "issues": [{"reason": "validity_unreadable",
                                         "detail": str(e)[:160]}]}
            _chk = validate_video_submission(
                shot, require_binding=policy_requires_binding(reference_policy),
                validity=_validity,
                policy=reference_policy, project=project, pdir=project_dir)
            if not _chk["ok"]:
                _v0 = _chk["violations"][0]
                return _blocker(
                    "PREFLIGHT_DEPENDENCY",
                    f"{_v0['code']}: {_v0['detail']}",
                    scope_id=scope_id, upstream="shot_producer",
                    extra={"contract_violations": _chk["violations"],
                           "fix_hint": _v0.get("fix_hint", "")})
        from routers.shots import _effective_first_frame_url
        from src.core.models import ShotStatus
        eff = _effective_first_frame_url(project, shot)
        if not eff:
            return _blocker("PREFLIGHT_DEPENDENCY",
                "视频前置不满足：开场画面未就绪（首帧未选/未生成，或来源镜头末帧未就绪）",
                scope_id=scope_id, upstream="shot_producer")
        if getattr(shot, "locked", False):
            return _blocker("PREFLIGHT_DEPENDENCY", "镜头已锁定",
                scope_id=scope_id, upstream="shot_producer")
        # keyframes 模式：尾帧显式 failed → 不提交（避免注定失败的 Job）
        if getattr(shot, "link_mode", "") == "keyframes" \
                and getattr(shot.last_frame, "status", None) == ShotStatus.FAILED.value:
            return _blocker("PREFLIGHT_DEPENDENCY", "尾帧生成失败，请重新生成尾帧",
                scope_id=scope_id, upstream="shot_producer")
    return None


async def execute_shot_production(
    client: Optional[Any],                       # real 路径用其 .quota；fake 路径不调 Agnes
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
    real: bool = False,                          # AI-6F：True=真实 JOB_HANDLERS+preflight+origin；False=fake（默认，测试）
    origin_task_id: Optional[str] = None,        # AI-6F §11：origin_run_id=task_id 作 artifact traceability
    reference_policy: Optional[str] = None,      # Phase55 A：视频参考链契约（None=按项目解析：显式 > 活动run策略 > bound_required 基线）
) -> AgentStructuredResult:
    """shot.first_frame/last_frame/video 执行器。

    real=False（默认，测试）：AI-6F.0 fake durable bridge（fake kind agent_*，不调 Agnes）。
    real=True（生产）：真实 JOB_HANDLERS（first_frame/last_frame/video）+ §4 Plan preflight +
      §16/§22 prereq + §35 activate_planned_shot + origin_run_id=origin_task_id traceability；
      Job 终态交 settlement bridge 做 §24 artifact 校验后结算（不在本处 shortcut-complete）。

    返回 AgentStructuredResult：
      status=RUNNING  → 已提交/reattach Job，delegate 保持 task=running，HTTP 返回（§4）
      status=COMPLETED→ fake 路径 reattach 发现终态 Job 直接结算（§6）
      status=FAILED   → scope/shot/plan/prereq blocker，或 reattach 发现 linked Job 已 failed
      status=NOT_CONFIGURED → JobQueue 不可用（软失败）
    """
    # §6 scope = shot
    if scope_type != "shot":
        return AgentStructuredResult(
            status=ResultStatus.FAILED.value,
            summary=f"shot 执行能力仅支持 shot 级 scope（收到 {scope_type}）。",
            warnings=[f"invalid scope_type: {scope_type}"],
            data={"scope_type": scope_type, "scope_id": scope_id})

    cap_map = REAL_KIND_FOR_CAPABILITY if real else FAKE_KIND_FOR_CAPABILITY
    if capability_id not in cap_map:
        return AgentStructuredResult(
            status=ResultStatus.NOT_IMPLEMENTED.value,
            summary=f"能力 {capability_id} 非 shot 长执行能力。",
            data={"capability_id": capability_id})
    kind = cap_map[capability_id]
    phase = PHASE_FOR_CAPABILITY[capability_id]

    # JobQueue 不可用 → 软失败
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
            # 终态
            if real:
                # §24：真实路径不在此 shortcut-complete——交 settlement bridge 据 job 终态 +
                # origin_run_id 校验 artifact 后结算（避免绕过 §24「Job done 但 artifact 缺失不得 falsely complete」）。
                # 返回 RUNNING：task 保持 running，settler 下一轮 poll 结算。
                return AgentStructuredResult(
                    status=ResultStatus.RUNNING.value,
                    summary=f"镜头 {scope_id} 的 {phase} Job 已 {st}，交 settlement bridge 结算（§24 artifact 校验）。",
                    artifact_refs=[{"type": "job", "id": existing_ref["job_id"]},
                                   {"type": "shot", "id": scope_id}],
                    data=_progress_data(existing_ref, st, existing_job))
            # fake：AI-6F.0 行为——从既有终态 Job 结算
            return _settled_result_from_job(existing_ref, existing_job, phase=phase)
        # job None（被 clear_finished 清理）→ ref 失效，落到 submit（罕见，记 warning）

    plan_warning = None
    if real:
        # ---- real: §4 Plan + §16/§22 prereq preflight（不调 Agnes）----
        # Phase55 A：参考策略解析（显式参数 > 项目级活动 run 策略 > 基线 bound_required）
        if capability_id == "shot.video":
            from src.generation.video_contract import resolve_reference_policy
            reference_policy = resolve_reference_policy(project_dir,
                                                        explicit=reference_policy)
        try:
            project = project_loader()
        except Exception as e:
            return AgentStructuredResult(
                status=ResultStatus.FAILED.value,
                summary=f"项目不可用：{str(e)[:200]}",
                warnings=[str(e)[:500]])
        shot = _find_shot(project, scope_id)
        if shot is None:
            return _blocker("SHOT_NOT_FOUND", f"未找到镜头 {scope_id}",
                scope_id=scope_id, upstream="director", phase=phase)
        _plan, planned, blk = _resolve_plan(project, project_dir, scope_id, shot)
        if blk is not None:
            blk.data["phase"] = phase
            return blk
        blk = _phase_plan_check(capability_id, planned, scope_id)
        if blk is not None:
            blk.data["phase"] = phase
            return blk
        blk = _apply_activate(project_loader, project_dir, scope_id, planned)
        if blk is not None:
            blk.data["phase"] = phase
            return blk
        # reload post-activate（activate 写了 opening_source/link_mode/source_version）
        project = project_loader()
        shot = _find_shot(project, scope_id)
        blk = _phase_prereq_check(capability_id, project, shot, client, scope_id,
                                  project_dir=project_dir,
                                  reference_policy=reference_policy)
        if blk is not None:
            blk.data["phase"] = phase
            return blk
    else:
        # ---- fake: load project + shot（§16 shot 不存在 → blocker）----
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
        # AI-6F.0 fake 路径不因缺 Plan 硬阻塞（fake 不执行真实创意策略，仅证 lifecycle）；
        # 缺 Plan 记 warning，真实接通（AI-6F real）按 §16 返回结构化 blocker。
        try:
            from src.ai.production_planner import read_plans
            scene_id = getattr(shot, "scene_id", None) or scope_id.rsplit("_c", 1)[0]
            if read_plans(project_dir).get(scene_id) is None:
                plan_warning = f"场景 {scene_id} 无 approved Production Plan（AI-6F.0 fake 继续；真实接通将 blocker）"
        except Exception as e:
            plan_warning = f"Production Plan 读取异常（fake 继续）：{str(e)[:120]}"

    # ---- 提交 Job 到现有 JobQueue ----
    if real:
        params: Dict[str, Any] = {"origin_run_id": origin_task_id}
        if capability_id == "shot.first_frame":
            params["variants"] = 2
            params["force"] = False
        if capability_id == "shot.video" and project_dir is not None:
            # Phase55 A：统一输入依据清单随 job 落盘（与 director 工具同一 manifest）。
            try:
                from src.generation.video_contract import (
                    build_input_manifest, planned_video_seconds,
                    scene_reference_consumed as _scene_ref_consumed,
                )
                from src.shot_binding.service import ShotBindingService
                _validity = ShotBindingService(project_dir, project_dir.name).read_validity(scope_id)
                _mode = ("keyframes" if shot.link_mode == "keyframes"
                         else "multi" if shot.link_mode == "multi" else "i2v")
                params["input_manifest"] = build_input_manifest(
                    project, shot, stage="video", mode=_mode, video_model=None,
                    seconds=planned_video_seconds(shot),
                    project_dir=project_dir, validity=_validity,
                    population_constraint=True,
                    scene_ref=_scene_ref_consumed(project, shot, project_dir,
                                                  validity=_validity),
                    compiled_negative_prompt=bool((shot.negative_prompt or "").strip()))
            except Exception as e:   # noqa: BLE001 —— manifest 失败不阻断提交（诚实缺失）
                params.setdefault("input_manifest_error", str(e)[:160])
    else:
        params = {"_fake_outcome": "done", "_fake_steps": 3, "_fake_step_sleep": 0.05}
        if fake_job_params:
            params.update(fake_job_params)
    # origin_task_id / origin_run_id：origin_run_id 由 handler stamp 到 batch/version（§10/§11），
    # settlement bridge 据 origin_run_id==task_id 解析 artifact（不靠「最新一项」）。
    try:
        job = job_queue.submit(project_name, kind, scope_id, **params)
    except Exception as e:
        return AgentStructuredResult(
            status=ResultStatus.FAILED.value,
            summary=f"提交 {phase} Job 失败：{str(e)[:200]}",
            warnings=[str(e)[:500]],
            data={"scope_id": scope_id, "phase": phase})

    ref: Dict[str, Any] = {
        "job_id": job.job_id,
        "kind": kind,
        "phase": phase,
        "shot_id": scope_id,
        "submitted_at": float(getattr(job, "created_at", time.time()) or time.time()),
    }
    if real and origin_task_id:
        ref["origin_task_id"] = origin_task_id
    warnings = []
    if plan_warning:
        warnings.append(plan_warning)
    data = _progress_data(ref, getattr(job, "state", "queued"), job)
    data["newly_submitted"] = True
    return AgentStructuredResult(
        status=ResultStatus.RUNNING.value,
        summary=(f"镜头 {scope_id} 的 {phase} Job 已提交（{kind}），task 保持 running 等待 Job 终态后结算。"),
        artifact_refs=[{"type": "job", "id": job.job_id},
                        {"type": "shot", "id": scope_id}],
        warnings=warnings,
        suggested_next_actions=["轮询/刷新 task 状态", "Job 完成后自动结算"],
        data=data,
    )
