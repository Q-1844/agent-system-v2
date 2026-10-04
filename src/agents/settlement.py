# src/agents/settlement.py
# AI-6F.0 §10/§12 + AI-6F §10/§24：AgentTask ↔ 现有 JobQueue 的 durable settlement bridge。
#
# 🔴 不变式（§11/§12）：
#   - Job/Run 是执行真相；AgentTask 是协调记录。二者分歧时从 durable 执行态对账
#     AgentTask，**不**发明独立的 Agent 执行现实（§11）。
#   - 持久态是真相（jobs.json + tasks/*.json）；SSE 只是通知（§12）。本 bridge 以
#     poll loop 对账——正确性**不**依赖内存 SSE 事件；restart 后 recover_tasks 从盘重建。
#     前端可继续用现有 /api/jobs/stream 看 job 事件；本 bridge 不建第二套 Agent SSE。
#
# AI-6F §10/§24 artifact 校验（仅 real kind + 注入 project_loader_for 时启用）：
#   - Job done 且 kind 为 first_frame/last_frame/video → 必须解析到对应持久化 artifact
#     （ImageBatch/VideoVersion，**按 origin_run_id==task_id 精确匹配**，§10「不用裸 [-1]」），
#     否则视为执行完整性错误，**不** falsely complete（§24）。
#   - video restart-recovery fallback（§20/§27）：当 recovery 路径未创建带 origin_run_id 的
#     VideoVersion 时，仅当 **exact video_id 匹配**（execution_ref.video_id == shot.video.video_id）
#     且 shot.video 已达 done、artifact 已真实持久化，才允许 settlement。绝不按 status /
#     shot_id /「最新一项」猜测身份（用户决策 2）。无法精确证明 → 不 falsely complete。
#   - fake kind（agent_*）或未注入 project_loader_for → 保持 AI-6F.0 行为（按 job 终态结算，不校验 artifact）。
#
# 三种 reconcile 决策（§6/§10）：
#   linked Job 非终态（queued/running） → reattach：task 保持 running，刷进度快照（§18-7/8/13）
#                                            video：记录 video_id 入 execution_ref（fallback 身份用）
#                                            video recovery：exact video_id + shot.video done → 可 settle
#   linked Job == done                 → settle completed（real kind 需 artifact 校验，§24）
#   linked Job failed/cancelled        → settle failed（§18-6/11）
#   linked Job 不存在（被清）          → settle failed（§10 边缘，无法重建）
#   task 已终态                        → no-op（幂等 §18-9）
#
# Import 纪律：本文件只 import 本包（models/tasks）+ stdlib。project_lister /
# job_queue_getter / project_loader_for 由 composition root（api_server）注入，不 import deps（可单测）。
from __future__ import annotations
import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .tasks import AgentTaskStore
from .models import (AgentStructuredResult, ResultStatus, TaskStatus)

log = logging.getLogger("AgentTaskSettler")

_JOB_DONE = "done"
_JOB_FAIL = {"failed", "cancelled"}
_JOB_NON_TERMINAL = {"queued", "running"}

# AI-6F §10：real JOB_HANDLERS kind（routers/shots.py）。fake kind（agent_*）见 _FAKE_KINDS。
_REAL_KINDS = {"first_frame", "last_frame", "video"}
_FAKE_KINDS = {"agent_first_frame", "agent_last_frame", "agent_video"}


def _progress_snapshot(ref: Dict[str, Any], job: Any) -> Dict[str, Any]:
    """从 linked Job 派生 AgentTask 进度快照（§8：确定性 production phase，不造 LLM 百分比）。"""
    state = getattr(job, "state", "unknown") if job is not None else "gone"
    return {
        "job_id": ref.get("job_id"),
        "kind": ref.get("kind"),
        "phase": ref.get("phase"),
        "shot_id": ref.get("shot_id"),
        "job_state": state,
        "progress_pct": int(getattr(job, "progress", 0) or 0) if job else 0,
        "completed_items": 1 if state == _JOB_DONE else 0,
        "total_items": 1,
        "submitted_at": ref.get("submitted_at"),
    }


def _result_for_done(ref, job) -> AgentStructuredResult:
    return AgentStructuredResult(
        status=ResultStatus.COMPLETED.value,
        summary=f"镜头 {ref.get('shot_id')} 的 {ref.get('phase')} 已完成。",
        artifact_refs=[{"type": "job", "id": ref.get("job_id")},
                        {"type": "shot", "id": ref.get("shot_id")}],
        data=_progress_snapshot(ref, job))


def _result_for_failed(ref, job, reason: str) -> AgentStructuredResult:
    err = (getattr(job, "error", None) or reason) if job else reason
    return AgentStructuredResult(
        status=ResultStatus.FAILED.value,
        summary=f"镜头 {ref.get('shot_id')} 的 {ref.get('phase')} 失败：{reason}",
        artifact_refs=[{"type": "job", "id": ref.get("job_id")}],
        warnings=[str(err)[:500]],
        data=_progress_snapshot(ref, job))


# ============ AI-6F §10/§24 artifact 解析（real kind）============
def _origin_of(ref: Dict[str, Any], task) -> Optional[str]:
    """本 AgentTask 的 origin key = execution_ref.origin_task_id（executor 装配）→ 回退 task.task_id。
    handler 把 origin_run_id（=提交时传的 task_id）stamp 到 batch/version，故二者相等。"""
    return ref.get("origin_task_id") or getattr(task, "task_id", None)


def _resolve_image_artifact(shot, kind: str, origin: str) -> Optional[Dict[str, Any]]:
    """first_frame/last_frame：按 origin_run_id==origin 找批次（§10 不用裸 [-1]）。
    返回 {batch_id, candidate_id, batch_status, refs} 或 None。"""
    is_ff = kind == "first_frame"
    batches = shot.first_frame_batches if is_ff else shot.last_frame_batches
    own = [b for b in (batches or []) if getattr(b, "origin_run_id", None) == origin]
    if not own:
        return None
    batch = own[-1]   # origin 已锁定到本 task，own 通常长度 1（§6 幂等：同 task 同 Job 一批次）
    sel = getattr(shot, "selected_first_frame" if is_ff else "selected_last_frame", None)
    cands = getattr(batch, "candidates", []) or []
    candidate_id = sel or (cands[0].candidate_id if cands else None)
    refs = [{"type": "shot", "id": getattr(shot, "shot_id", ref_shot_id(shot))},
            {"type": "batch", "id": getattr(batch, "batch_id", None)}]
    if candidate_id:
        refs.append({"type": "candidate", "id": candidate_id})
    return {"batch_id": getattr(batch, "batch_id", None), "candidate_id": candidate_id,
            "batch_status": getattr(batch, "status", None), "refs": refs}


def ref_shot_id(shot):
    return getattr(shot, "shot_id", "")


def _resolve_video_artifact(shot, ref: Dict[str, Any], origin: str) -> Optional[Dict[str, Any]]:
    """video：优先按 origin_run_id==origin 找 VideoVersion（正常路径，§10）。
    找不到（restart-recovery 路径未建带 origin 的 version）→ exact video_id fallback
    （execution_ref.video_id == shot.video.video_id 且 shot.video.done，§20/§27）。
    无法精确证明身份 → None（§24 不 falsely complete）。"""
    own = [v for v in (shot.video_versions or []) if getattr(v, "origin_run_id", None) == origin]
    if own:
        vv = own[-1]
        refs = [{"type": "shot", "id": getattr(shot, "shot_id", "")},
                {"type": "video_version", "id": getattr(vv, "version_id", None)},
                {"type": "job", "id": ref.get("job_id")}]
        return {"version_id": getattr(vv, "version_id", None),
                "video_id": getattr(vv, "video_id", None),
                "local": getattr(vv, "local", None), "url": getattr(vv, "url", None),
                "seconds_returned": getattr(vv, "seconds_returned", None),
                "settled_via": "origin_version", "refs": refs}
    # fallback：exact video_id 匹配（execution_ref 录的 video_id == shot.video.video_id）
    expected_vid = ref.get("video_id")
    actual_vid = getattr(shot.video, "video_id", None) if hasattr(shot, "video") else None
    if expected_vid and actual_vid and expected_vid == actual_vid \
            and getattr(shot.video, "status", None) == "done" \
            and getattr(shot.video, "local", None):
        refs = [{"type": "shot", "id": getattr(shot, "shot_id", "")},
                {"type": "video", "id": actual_vid},
                {"type": "job", "id": ref.get("job_id")}]
        return {"video_id": actual_vid, "version_id": getattr(shot, "final_video", None),
                "local": getattr(shot.video, "local", None), "url": getattr(shot.video, "url", None),
                "seconds_returned": getattr(shot.video, "seconds_returned", None),
                "settled_via": "video_id_fallback", "refs": refs}
    return None


def _done_result_with_artifact(ref, job, art: Dict[str, Any]) -> AgentStructuredResult:
    data = _progress_snapshot(ref, job)
    data.update({k: v for k, v in art.items() if k != "refs"})
    return AgentStructuredResult(
        status=ResultStatus.COMPLETED.value,
        summary=f"镜头 {ref.get('shot_id')} 的 {ref.get('phase')} 已完成（artifact 已校验，settled_via={art.get('settled_via','origin')}）。",
        artifact_refs=list(art["refs"]),
        data=data)


def _apply_decision(t: "AgentTask", decision: Dict[str, Any]) -> None:
    """把 reconcile 决策写回 AgentTask（mutator，由 store.update 在 reload 后调用）。
    幂等：若 task 已非 running（被别处结算），跳过（§18-9）。"""
    if t.status != TaskStatus.RUNNING.value:
        return
    # Phase 45.2：sync-delegate 中断结算不需要 execution_ref（无 durable job）。
    if not decision.get("sync_interrupted"):
        if not t.execution_ref or not t.execution_ref.get("job_id"):
            return
    # video_id 录制（reattach 时从 shot.video.video_id 读到，fallback 身份用）
    rec_vid = decision.get("record_video_id")
    if rec_vid and not t.execution_ref.get("video_id"):
        t.execution_ref["video_id"] = rec_vid
    res: AgentStructuredResult = decision["result"]
    t.structured_result = res.to_dict()
    t.result_summary = res.summary
    t.artifact_refs = list(res.artifact_refs)
    t.warnings = list(res.warnings)
    # execution_ref 保留（§14：regeneration 是版本创建不是抹除；durable 链接历史不删）
    if res.status == ResultStatus.COMPLETED.value:
        t.status = TaskStatus.COMPLETED.value
        t.error = None
    elif res.status == ResultStatus.FAILED.value:
        t.status = TaskStatus.FAILED.value
        t.error = res.summary
    # RUNNING（reattach）：status 保持 running，仅刷进度快照（+ 可能已录 video_id）


class AgentTaskSettler:
    """扫描所有项目 running AgentTask，据 linked Job 状态对账。

    project_lister: () -> List[(project_name, project_dir)]（composition root 注入）
    job_queue_getter: () -> JobQueue | None（未就绪返 None，本轮跳过）
    project_loader_for: Optional[(project_name) -> (() -> Project) | None]（AI-6F §10/§24 artifact 校验用；
        None=不校验，保持 AI-6F.0 行为）
    """

    def __init__(self,
                 project_lister: Callable[[], List[Tuple[str, Any]]],
                 job_queue_getter: Callable[[], Any],
                 poll_interval: float = 2.0,
                 project_loader_for: Optional[Callable[[str], Optional[Callable[[], Any]]]] = None):
        self._project_lister = project_lister
        self._job_queue_getter = job_queue_getter
        self._poll_interval = poll_interval
        self._project_loader_for = project_loader_for
        self._loop_task: Optional[asyncio.Task] = None
        # Phase 47 修复（真机排障：turn 尾释放 registry 与任务落账之间的轮询窗口内，
        # 本 boot 的活任务被误判为"重启遗留"）——记录本 boot 起始墙钟，同步委派
        # 中断判定只对【上个 boot】遗留的 running task 生效。
        self._boot_wall = time.time()

    # ---------- 单 task 对账（核心，可独立单测）----------
    def _loader_for(self, project_name: str):
        if self._project_loader_for is None:
            return None
        try:
            return self._project_loader_for(project_name)
        except Exception:
            return None

    def reconcile_task(self, store: AgentTaskStore, task: "AgentTask") -> Optional[Dict[str, Any]]:
        """同步对账一个 running task。返回决策 dict 或 None（无可对账链接）。
        不写盘——调用方（store.update）负责原子写。保留同步签名便于单测直接驱动。"""
        ref = task.execution_ref
        if not ref or not ref.get("job_id"):
            return self._reconcile_sync_delegate(task)
        try:
            q = self._job_queue_getter()
        except Exception:
            return None
        if q is None:
            return None
        job = q.get(ref["job_id"])
        if job is None:
            return {"result": _result_for_failed(
                ref, None, "linked Job 已不存在（被清理或丢失），无法重建执行态")}
        st = getattr(job, "state", None)
        kind = ref.get("kind") or getattr(job, "kind", None)
        loader = self._loader_for(task.project) if kind in _REAL_KINDS else None

        if st == _JOB_DONE:
            # §24：real kind 终态 → artifact 校验
            if loader is not None and kind in _REAL_KINDS:
                res = self._resolve_done_artifact(ref, job, task, loader)
                if res is not None:
                    return {"result": res, "record_video_id": None}
                # artifact 缺失 → 执行完整性错误（不 falsely complete，§24）
                return {"result": _result_for_failed(
                    ref, job, f"Job done 但 {kind} artifact 未解析到"
                    f"（origin_task_id={_origin_of(ref, task)}）—— 执行完整性错误，不重生成")}
            # fake kind 或无 loader → AI-6F.0 行为（按 job 终态结算）
            return {"result": _result_for_done(ref, job)}
        if st in _JOB_FAIL:
            return {"result": _result_for_failed(ref, job, f"linked Job {st}")}
        if st in _JOB_NON_TERMINAL:
            decision: Dict[str, Any] = {"record_video_id": None}
            if loader is not None and kind == "video":
                # 录 video_id（fallback 身份用）+ recovery fallback（exact video_id + shot.video done）
                rec, fallback = self._video_reattach(ref, task, loader)
                decision["record_video_id"] = rec
                if fallback is not None:
                    decision["result"] = fallback
                    return decision
            # reattach：返回 RUNNING 结果仅刷进度；task 保持 running
            decision["result"] = AgentStructuredResult(
                status=ResultStatus.RUNNING.value,
                summary=f"镜头 {ref.get('shot_id')} 的 {ref.get('phase')} 进行中（job {st}）。",
                artifact_refs=[{"type": "job", "id": ref.get("job_id")},
                                {"type": "shot", "id": ref.get("shot_id")}],
                data=_progress_snapshot(ref, job))
            return decision
        # 未知状态：跳过，不误结算
        return None

    def _reconcile_sync_delegate(self, task: "AgentTask") -> Optional[Dict[str, Any]]:
        """Phase 45.2：sync-delegate 型 running task（*.delegate 委派，child turn 在
        本进程内执行，无 durable job）。判据=其子会话是否仍被 turn_registry 占用：
        占用中 → 正在运行的 live 委派，不动；未占用 → 中断遗留（后端重启/崩溃，
        turn_registry 重启即清空）→ 诚实 failed（不永久 running，也不假装完成）。
        child session 的消息/工具轨迹已在盘，续做=Producer 发起新委派任务。"""
        if task.status != TaskStatus.RUNNING.value:
            return None       # 防御：reconcile 只对 running task（调用方过滤，双保险）
        # 本 boot 创建的 task 不判"重启中断"——它的结算由其委派自身负责
        # （registry 释放与落账之间的轮询窗口不是崩溃）。跨 boot 遗留才走中断判定。
        created_at = getattr(task, "created_at", None)
        if isinstance(created_at, (int, float)) and created_at >= self._boot_wall - 5.0:
            return None
        from . import turn_registry
        sid = task.session_id or ""
        if sid and turn_registry.is_active(task.project, sid):
            return None
        return {"sync_interrupted": True, "record_video_id": None,
                "result": AgentStructuredResult(
                    status=ResultStatus.FAILED.value,
                    summary=(f"委派任务中断：后端重启/崩溃时 {task.target_agent} "
                             f"子会话未完成（session={sid or '未知'}）；"
                             f"已完成部分见该会话轨迹，续做请重新委派。"),
                    artifact_refs=([{"type": "session", "id": sid}] if sid else []),
                    warnings=[f"interrupted_by_backend_restart:{task.capability_id}"],
                    data={"code": "interrupted_by_backend_restart",
                          "target_agent": task.target_agent,
                          "capability_id": task.capability_id,
                          "session_id": sid})}

    def _resolve_done_artifact(self, ref, job, task, loader) -> Optional[AgentStructuredResult]:
        """§24：Job done → 解析持久化 artifact（origin 精确匹配；video 含 exact-video_id fallback）。"""
        try:
            project = loader()
        except Exception:
            return None
        shot = project.get_shot(ref.get("shot_id")) if hasattr(project, "get_shot") else None
        if shot is None:
            return None
        kind = ref.get("kind")
        origin = _origin_of(ref, task)
        if origin is None:
            return None
        if kind == "video":
            art = _resolve_video_artifact(shot, ref, origin)
        else:
            art = _resolve_image_artifact(shot, kind, origin)
        if art is None:
            return None
        return _done_result_with_artifact(ref, job, art)

    def _video_reattach(self, ref, task, loader) -> Tuple[Optional[str], Optional[AgentStructuredResult]]:
        """video 非终态 reattach：录 video_id（shot.video.video_id）+ recovery fallback。
        返回 (record_video_id, fallback_result_or_None)。fallback 仅在 exact video_id 匹配 +
        shot.video.done 时 settle completed（§20/§27 restart-recovery）。"""
        try:
            project = loader()
        except Exception:
            return None, None
        shot = project.get_shot(ref.get("shot_id")) if hasattr(project, "get_shot") else None
        if shot is None or not hasattr(shot, "video"):
            return None, None
        actual_vid = getattr(shot.video, "video_id", None)
        # 录 video_id（若未录且 job 已提交了 video_id）
        rec = actual_vid if (actual_vid and not ref.get("video_id")) else None
        # recovery fallback：exact video_id 匹配。expected_vid 必须是**先前 poll 录入**的
        # execution_ref.video_id（不取 shot.video.video_id 当 expected——否则循环匹配，无法证明身份）。
        expected_vid = ref.get("video_id")
        if expected_vid and actual_vid and expected_vid == actual_vid \
                and getattr(shot.video, "status", None) == "done" \
                and getattr(shot.video, "local", None):
            art = _resolve_video_artifact(shot, ref, _origin_of(ref, task))
            if art is None:
                # 无 origin-version + exact video_id 匹配 → legacy fallback refs
                refs = [{"type": "shot", "id": getattr(shot, "shot_id", "")},
                        {"type": "video", "id": actual_vid},
                        {"type": "job", "id": ref.get("job_id")}]
                art = {"video_id": actual_vid, "version_id": getattr(shot, "final_video", None),
                       "local": getattr(shot.video, "local", None), "url": getattr(shot.video, "url", None),
                       "seconds_returned": getattr(shot.video, "seconds_returned", None),
                       "settled_via": "video_id_fallback", "refs": refs}
            # fallback 用 job snapshot（job 非终态，但 video 已 done via recovery）
            snap = {"job_id": ref.get("job_id"), "kind": ref.get("kind"), "phase": ref.get("phase"),
                    "shot_id": ref.get("shot_id"), "job_state": "done",
                    "progress_pct": 100, "completed_items": 1, "total_items": 1,
                    "submitted_at": ref.get("submitted_at")}
            data = dict(snap)
            data.update({k: v for k, v in art.items() if k != "refs"})
            return rec, AgentStructuredResult(
                status=ResultStatus.COMPLETED.value,
                summary=f"镜头 {ref.get('shot_id')} 视频已完成（restart-recovery fallback，exact video_id 匹配，settled_via=video_id_fallback）。",
                artifact_refs=list(art["refs"]), data=data)
        return rec, None

    async def _reconcile_store(self, store: AgentTaskStore) -> int:
        """对账一个项目所有 running task。返回结算/刷新数。"""
        n = 0
        for t in store.list(status=TaskStatus.RUNNING.value):
            decision = self.reconcile_task(store, t)
            if decision is None:
                continue
            # store.update 重载后调 mutator 原子写（避免 list→写 之间覆盖）
            store.update(t.task_id, lambda tt, d=decision: _apply_decision(tt, d))
            n += 1
        return n

    # ---------- 启动恢复（§10）----------
    async def recover_tasks(self) -> int:
        """startup 一次性扫描所有项目 running AgentTask，reattach/settle。
        active Job 由 JobQueue 恢复（_load + start）；本处只对账 task↔job 链接。"""
        total = 0
        try:
            for name, pdir in self._project_lister():
                try:
                    store = AgentTaskStore(pdir, name)
                except Exception as e:
                    log.warning("AgentTaskStore 构造失败 %s: %s", name, e)
                    continue
                try:
                    total += await self._reconcile_store(store)
                except Exception as e:
                    log.warning("对账项目 %s 失败: %s", name, e)
        except Exception as e:
            log.error("recover_tasks 失败: %s", e)
        if total:
            log.info("AgentTask settlement 对账 %d 个 running task", total)
        return total

    # ---------- 后台 poll loop（§12 polling fallback）----------
    async def start(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            return
        self._loop_task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                await self.recover_tasks()
            except Exception as e:
                log.error("settlement poll 异常: %s", e)
            await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            self._loop_task = None


# ============ 单例（生产；api_server 用）============
_settler: Optional[AgentTaskSettler] = None


def get_settler() -> AgentTaskSettler:
    """生产单例：用 deps 注入 project_lister / job_queue_getter / project_loader_for。
    测试**不**用此——直接构造 AgentTaskSettler(...fakes...)。"""
    global _settler
    if _settler is None:
        import deps

        def _list_projects():
            out: List[Tuple[str, Any]] = []
            try:
                pm = deps.get_project_manager()
            except Exception:
                return out
            base = getattr(pm, "base_dir", None)
            if base is None or not base.exists():
                return out
            for p in base.iterdir():
                if not p.is_dir():
                    continue
                if not (p / "output" / "project.json").exists():
                    continue
                out.append((p.name, p))
            return out

        def _get_queue():
            try:
                return deps.get_job_queue()
            except Exception:
                return None

        def _project_loader_for(project_name: str):
            # AI-6F §10/§24：返回 () -> Project（惰性，每次 reconcile 读最新盘），不可用返 None
            try:
                pm = deps.get_project_manager()
                pdir = pm.get_project_dir(project_name)
            except Exception:
                return None
            if not (pdir / "output" / "project.json").exists():
                return None
            from src.core.store import ProjectStore
            return lambda: ProjectStore(pdir).load()

        _settler = AgentTaskSettler(_list_projects, _get_queue,
                                    project_loader_for=_project_loader_for)
    return _settler
