# src/ai/autopilot.py
"""Scene Autopilot Executor（Phase AI-4B）。

确定性执行引擎（不是 Agent 决策层）：approved Production Plan -> 按依赖逐 Shot 执行 ->
自动生成 prompt/首帧/尾帧/视频 -> 自动选候选 -> 设 final -> 提取末帧 -> 下一镜。
不重新判断 opening_source/link_mode/故事/角色（这些由 AI Director + Production Planner 完成）。

🔴 关键不变量：
  - Plan 与 Execution 分开：Run 冻结 plan_snapshot，运行中 Production Plan 文件变化不影响当前 Run（§七）。
  - 新 Run 与 Resume 区分：是否跳过阶段由 Run checkpoint（completed_stages + output ids）决定，
    不是由 Shot 当前 ready 状态决定（§五）。新 Run 强制产生新 prompt/batch/version。
  - 正式 opening_source 由 Executor 执行 Shot 时 activate（§九），不在 AI-4A approve 时写。
  - 第一版严格逐 Shot 串行（§二），但内部是 dependency scheduler（next_ready_shot），
    未来换并发 Scheduler 不重写 Executor。
  - crash safety：job 带 origin_run_id + autopilot_stage_key，重启后 find_by_origin 复用，不重复 submit（§十六）。
  - Run 文件每 run_id 一个 json（§三十二），per-run asyncio.Lock 防 concurrent 覆盖。

业务逻辑全部复用 routers/shots.py 的共享 helper（§四十一）：generate_prompt_for_stage /
activate_planned_shot / choose_first_frame_business / choose_last_frame_business /
set_final_video_business，以及 JobQueue + 现有 first/last/video handler。不绕 HTTP、不重写 Agnes 调用。
"""
from __future__ import annotations
import asyncio
import hashlib
import time
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field

from src.core.store import ProjectStore, _atomic_write_json
from src.core.models import ShotStatus
from src.ai.production_planner import SceneProductionPlan, PlannedShot, is_plan_stale
from src.ai.production_planner import choose_candidate
from src.ai.c2_parallel import (shot_dependencies, upstream_ready, validate_parallelism,
                                media_sources_for_shot, source_completed_for_run)


# ============ 状态常量 ============
# Run status（§三）
RUN_PENDING = "pending"
RUN_RUNNING = "running"
RUN_PAUSING = "pausing"
RUN_PAUSED = "paused"
RUN_BLOCKED = "blocked"
RUN_FAILED = "failed"
RUN_COMPLETED = "completed"
RUN_CANCELLED = "cancelled"
RUN_ACTIVE = {RUN_PENDING, RUN_RUNNING, RUN_PAUSING, RUN_PAUSED}   # 阻止新 Run
RUN_TERMINAL = {RUN_COMPLETED, RUN_FAILED, RUN_CANCELLED, RUN_BLOCKED}

# Shot status（§四）
SHOT_PENDING = "pending"
SHOT_WAITING = "waiting_dependency"
SHOT_RUNNING = "running"
SHOT_SKIPPED_LOCKED = "skipped_locked"
SHOT_FAILED = "failed"
SHOT_COMPLETED = "completed"
SHOT_TERMINAL = {SHOT_COMPLETED, SHOT_FAILED, SHOT_SKIPPED_LOCKED}

# Stage（§十一）
STAGE_ACTIVATE = "activate_plan"
STAGE_FIRST_PROMPT = "first_prompt"
STAGE_FIRST_FRAME = "first_frame"
STAGE_LAST_PROMPT = "last_prompt"
STAGE_LAST_FRAME = "last_frame"
STAGE_VIDEO_PROMPT = "video_prompt"
STAGE_VIDEO = "video"
STAGE_VIDEO_END = "video_end"


# ============ 数据结构 ============
class ShotExecutionState(BaseModel):
    """单个 Shot 在本 Run 内的 checkpoint（§四）。output ids 是本 Run 自己生成的。"""
    shot_id: str
    index: int
    status: str = SHOT_PENDING
    stage: Optional[str] = None
    active_job_id: Optional[str] = None
    active_stage_key: Optional[str] = None
    completed_stages: List[str] = Field(default_factory=list)   # Run checkpoint（§五）
    first_prompt: Optional[str] = None
    first_batch_id: Optional[str] = None
    selected_first_candidate_id: Optional[str] = None
    last_prompt: Optional[str] = None
    last_batch_id: Optional[str] = None
    selected_last_candidate_id: Optional[str] = None
    video_prompt: Optional[str] = None
    video_version_id: Optional[str] = None
    source_video_version_id: Optional[str] = None    # downstream 绑定用（§二十七）
    attempts: Dict[str, int] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class SceneAutopilotRun(BaseModel):
    """一次 Scene 自动制作 Run（§三）。Plan 是「准备怎么做」，Run 是「这次做到哪、生成了什么」。"""
    run_id: str
    project_name: str
    scene_id: str
    plan_id: str
    plan_snapshot: dict                        # 冻结的 approved plan（§七）
    run_seed: int                              # 本 Run 独立 seed（§六）：derive(plan_seed, run_id)
    max_parallel_shots: int = Field(default=1, ge=1, le=2)
    parent_project_run_id: Optional[str] = None   # AI-5A：父 ProjectProductionRun（None=手工点击）
    status: str = RUN_PENDING
    respect_locked: bool = True
    pause_requested: bool = False
    cancel_requested: bool = False
    current_shot_id: Optional[str] = None
    current_stage: Optional[str] = None
    shots: List[ShotExecutionState] = Field(default_factory=list)
    error: Optional[str] = None
    error_code: Optional[str] = None           # "STRUCTURE_CHANGED" 等
    created_at: float = 0.0
    updated_at: float = 0.0


# ============ 纯函数 helper ============
def derive_execution_seed(plan_seed: int, run_id: str) -> int:
    """本 Run 独立 seed（§六）：sha256(plan_seed:run_id) 取 4 字节 & 0x7fffffff。
    同一 Run 重启 seed 不变；不同 Run 得到不同选择；不依赖 Python hash()。"""
    raw = f"{plan_seed}:{run_id}".encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:4], "big") & 0x7fffffff


def build_stage_sequence(planned: PlannedShot) -> List[str]:
    """根据 PlannedShot 派生 stage 序列（§十一）。六种组合自然成立。"""
    stages = [STAGE_ACTIVATE]
    if planned.generate_first_frame:
        stages += [STAGE_FIRST_PROMPT, STAGE_FIRST_FRAME]
    if planned.generate_last_frame:
        stages += [STAGE_LAST_PROMPT, STAGE_LAST_FRAME]
    stages += [STAGE_VIDEO_PROMPT, STAGE_VIDEO, STAGE_VIDEO_END]
    return stages


def dependency_ready(project, planned: PlannedShot) -> bool:
    """runtime 依赖校验（§十）。reference/inherit -> source final_video + video_end_frame.url_ready。
    independent -> 无依赖。"""
    if not planned.requires_previous_video_end:
        return True
    source = project.get_shot(planned.source_shot_id) if planned.source_shot_id else None
    if not source:
        return False
    if not source.final_video or source.video.status != ShotStatus.DONE.value:
        return False
    if not source.video_end_frame.url_ready:
        return False
    return True


def _planned_for(run: SceneAutopilotRun, shot_id: str) -> Optional[PlannedShot]:
    """从冻结的 plan_snapshot 取某 shot 的 PlannedShot。"""
    for ps in (run.plan_snapshot.get("shots") or []):
        if ps.get("shot_id") == shot_id:
            try:
                return PlannedShot.model_validate(ps)
            except Exception:
                return None
    return None


def _structure_changed(run: SceneAutopilotRun, scene) -> Optional[str]:
    """运行中 Scene/Shot 叙事结构是否变化（§三十五）。复用 is_plan_stale 逻辑。"""
    try:
        plan = SceneProductionPlan.model_validate(run.plan_snapshot)
    except Exception:
        return "plan_snapshot 解析失败"
    return is_plan_stale(plan, scene)


# 供应商 4xx 额度语义关键词——与 tools/quota_gate._PROVIDER_MARKERS 同源
# （src 不反向 import tools，避免分层倒挂）。r46 实测样例见 quota_gate.py。
_QUOTA_MARKERS = (
    "quota", "配额", "额度", "insufficient", "balance",
    "余额", "exceeded", "too many requests", "用量上限",
)


def _provider_rejected_quota(error) -> bool:
    """持久化的 job error 字符串是否为「供应商 429 确定性额度拒绝」。

    job error 是 AgnesError 的 str（无 status 属性），形如
    "重试 5 次仍失败: 429: {\\"error\\":{\\"message\\":\\"已达到 API 用量上限…\\"}}"。
    True 意味着请求被供应商拒收：无任务号、零扣费，resume 重排是安全的；
    5xx/无状态码/无额度关键词一律 False（结局不明，§二十四 维持阻塞）。"""
    if not isinstance(error, str) or "429:" not in error:
        return False
    lowered = error.lower()
    return any(marker in lowered for marker in _QUOTA_MARKERS)


# ============ 共享 preflight（§十七/§十八：手工 Autopilot 与 Project Preflight 同一规则）============
# AI-5A 提取：手工 start_scene_run 与 Project Preflight 必须用同一套 ready 判定，
# 否则会出现「Project Preflight 说 ready -> 运行时 Scene Autopilot 说 locked invalid」。
class SceneRunPreflight(BaseModel):
    """单个 Scene 的 Autopilot 就绪检查结果。issues 用稳定 code，UI 可本地化展示。"""
    scene_id: str
    ready: bool
    shot_count: int
    plan_id: Optional[str] = None
    plan_status: Optional[str] = None
    issues: List[str] = Field(default_factory=list)   # NO_SHOTS / NO_PRODUCTION_PLAN /
    #   PLAN_NOT_APPROVED / PLAN_STALE / ACTIVE_SCENE_RUN /
    #   LOCKED_SHOT_NO_FINAL / LOCKED_SOURCE_NO_VIDEO_END / SHOT_NOT_IN_PLAN


def _locked_shot_problems(scene, plan, respect_locked: bool) -> List[tuple]:
    """遍历 Scene 所有 Shot，返回 [(shot_id, issue_code), ...]（按顺序）。
    issue_code: SHOT_NOT_IN_PLAN / LOCKED_SHOT_NO_FINAL / LOCKED_SOURCE_NO_VIDEO_END。
    手工 start 与 Project Preflight 共用，保证规则一致（§十八）。"""
    out: List[tuple] = []
    planned_by_id = {p.shot_id: p for p in plan.shots}
    for i, sh in enumerate(scene.shots):
        p = planned_by_id.get(sh.shot_id)
        if p is None:
            out.append((sh.shot_id, "SHOT_NOT_IN_PLAN"))
            continue
        if respect_locked and sh.locked:
            if not sh.final_video or sh.video.status != ShotStatus.DONE.value:
                out.append((sh.shot_id, "LOCKED_SHOT_NO_FINAL"))
            elif i + 1 < len(scene.shots):
                nxt = planned_by_id.get(scene.shots[i + 1].shot_id)
                if nxt and nxt.requires_previous_video_end and not sh.video_end_frame.url_ready:
                    out.append((sh.shot_id, "LOCKED_SOURCE_NO_VIDEO_END"))
    return out


def preflight_scene_autopilot(project, scene, plan, respect_locked: bool = True,
                              *, has_active_run: bool = False) -> SceneRunPreflight:
    """共享 preflight（§十八）。plan 可为 None（无计划）。has_active_run 由调用方查 manager 后传入
    （本函数不依赖 manager，保持纯函数可单测）。ready = 无任何 issue。"""
    issues: List[str] = []
    shot_count = len(scene.shots) if scene else 0
    if scene is None or not scene.shots:
        issues.append("NO_SHOTS")
    if plan is None:
        issues.append("NO_PRODUCTION_PLAN")
    else:
        if plan.status != "approved":
            issues.append("PLAN_NOT_APPROVED")
        if scene is not None:
            stale = is_plan_stale(plan, scene)
            if stale:
                issues.append("PLAN_STALE")
            if scene.shots and plan.shots:
                for _sid, code in _locked_shot_problems(scene, plan, respect_locked):
                    issues.append(code)
    if has_active_run:
        issues.append("ACTIVE_SCENE_RUN")
    return SceneRunPreflight(
        scene_id=scene.scene_id if scene else "",
        ready=len(issues) == 0,
        shot_count=shot_count,
        plan_id=plan.plan_id if plan else None,
        plan_status=plan.status if plan else None,
        issues=issues,
    )


# issue code -> 手工 start 时的中文错误信息（保留原文案，existing 测试依赖子串）
_ISSUE_MESSAGES = {
    "NO_SHOTS": "故事段没有镜头",
    "NO_PRODUCTION_PLAN": "没有 AI 制作计划，请先 AI 制作规划并确认",
    "PLAN_NOT_APPROVED": "制作计划尚未确认，请先在 AI 制作规划中确认",
    "PLAN_STALE": "制作计划已过期，请重新生成并确认",
}


def _preflight_issue_to_error(scene, plan, pre: SceneRunPreflight) -> str:
    """把 preflight 第一个 issue 映射成手工 start 的中文错误信息。
    locked/不在计划 类需带 shot_id，从 _locked_shot_problems 取。"""
    if not pre.issues:
        return ""
    first = pre.issues[0]
    if first in _ISSUE_MESSAGES:
        return _ISSUE_MESSAGES[first]
    if first == "ACTIVE_SCENE_RUN":
        return "故事段已有自动制作任务"   # 手工 start 走 _ExistingRunError，不会到这里
    # locked / not_in_plan：找第一个对应 shot_id
    if scene and plan:
        for sid, code in _locked_shot_problems(scene, plan, True):
            if code == first:
                if code == "SHOT_NOT_IN_PLAN":
                    return f"镜头 {sid} 不在制作计划中"
                if code == "LOCKED_SHOT_NO_FINAL":
                    return f"镜头 {sid} 已锁定但没有最终视频，请解除锁定或先完成该镜头"
                if code == "LOCKED_SOURCE_NO_VIDEO_END":
                    return f"镜头 {sid} 已锁定，但没有可用于后续接力的最终视频末帧。请解除锁定或先完成该镜头。"
    return "故事段未就绪"


# ============ Manager ============
class SceneAutopilotManager:
    """Scene Autopilot 生命周期管理 + 后台执行循环 + 恢复。"""

    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._run_tasks: Dict[str, asyncio.Task] = {}

    # ---------- 路径 / IO ----------
    def _runs_dir(self, project_name: str) -> Path:
        from deps import get_project_manager
        d = get_project_manager().get_project_dir(project_name) / "output" / "ai" / "autopilot_runs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _run_path(self, project_name: str, run_id: str) -> Path:
        return self._runs_dir(project_name) / f"{run_id}.json"

    def _lock_for(self, run_id: str) -> asyncio.Lock:
        if run_id not in self._locks:
            self._locks[run_id] = asyncio.Lock()
        return self._locks[run_id]

    def _load_run(self, project_name: str, run_id: str) -> Optional[SceneAutopilotRun]:
        p = self._run_path(project_name, run_id)
        if not p.exists():
            return None
        try:
            return SceneAutopilotRun.model_validate(
                __import__("json").loads(p.read_text(encoding="utf-8")))
        except Exception as e:
            from deps import logger
            logger.warning(f"加载 Run {run_id} 失败: {e}")
            return None

    async def _save_run(self, run: SceneAutopilotRun):
        """per-run Lock 保护原子写（§三十三）。"""
        import json as _json
        lock = self._lock_for(run.run_id)
        async with lock:
            run.updated_at = time.time()
            _atomic_write_json(self._run_path(run.project_name, run.run_id),
                               _json.loads(run.model_dump_json()))

    async def _edit_run(self, project_name: str, run_id: str, edit):
        """Read/modify/write in one lock scope; never replace another concurrent
        shot's checkpoint or a pause/cancel flag from a stale in-memory Run.
        """
        import json as _json
        async with self._lock_for(run_id):
            current = self._load_run(project_name, run_id)
            if current is None:
                raise RuntimeError(f"autopilot run {run_id} disappeared")
            edit(current)
            current.updated_at = time.time()
            _atomic_write_json(self._run_path(project_name, run_id),
                               _json.loads(current.model_dump_json()))
            return current

    async def _save_shot_state(self, run: SceneAutopilotRun,
                               state: ShotExecutionState):
        """Only this shot's fields are owned by its coroutine.  Parent flags,
        other shot states, and the current run status stay authoritative.
        """
        copy = state.model_copy(deep=True)
        def apply(latest):
            for i, old in enumerate(latest.shots):
                if old.shot_id == copy.shot_id:
                    latest.shots[i] = copy
                    break
            else:
                raise RuntimeError(f"shot {copy.shot_id} no longer belongs to this run")
            active = [st for st in latest.shots if st.status == SHOT_RUNNING]
            first = min(active, key=lambda st: st.index) if active else None
            latest.current_shot_id = first.shot_id if first else None
            latest.current_stage = first.stage if first else None
        return await self._edit_run(run.project_name, run.run_id, apply)

    def list_runs_for_scene(self, project_name: str, scene_id: str) -> List[SceneAutopilotRun]:
        out = []
        for p in sorted(self._runs_dir(project_name).glob("*.json")):
            try:
                r = SceneAutopilotRun.model_validate(
                    __import__("json").loads(p.read_text(encoding="utf-8")))
                if r.scene_id == scene_id:
                    out.append(r)
            except Exception:
                pass
        out.sort(key=lambda r: r.created_at, reverse=True)
        return out

    def get_run(self, project_name: str, run_id: str) -> Optional[SceneAutopilotRun]:
        return self._load_run(project_name, run_id)

    def find_active_run(self, project_name: str, scene_id: str) -> Optional[SceneAutopilotRun]:
        for r in self.list_runs_for_scene(project_name, scene_id):
            if r.status in RUN_ACTIVE:
                return r
        return None

    # ---------- 启动 / 恢复 / 控制 ----------
    async def start_scene_run(self, project_name: str, scene_id: str,
                              respect_locked: bool = True) -> SceneAutopilotRun:
        """preflight（§八）+ 创建 Run + 启动后台 Executor。"""
        from deps import get_project_manager
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        scene = next((s for s in project.scenes if s.scene_id == scene_id), None)
        if scene is None:
            raise ValueError(f"故事段 {scene_id} 不存在")
        if not scene.shots:
            raise ValueError("故事段没有镜头")

        # 找 approved Production Plan
        from routers.production_planner import _read_plans
        plans = _read_plans(project_dir)
        plan = plans.get(scene_id)

        # 共享 preflight（§十八）：手工 Autopilot 与 Project Preflight 同一规则。
        # active run 单独处理（须 _ExistingRunError 携带 existing_run_id）。
        pre = preflight_scene_autopilot(project, scene, plan, respect_locked, has_active_run=False)
        # 1) plan 级问题优先（与原顺序一致：plan 检查在 active 之前）
        if any(c in pre.issues for c in (
                "NO_SHOTS", "NO_PRODUCTION_PLAN", "PLAN_NOT_APPROVED", "PLAN_STALE", "SHOT_NOT_IN_PLAN")):
            raise ValueError(_preflight_issue_to_error(scene, plan, pre))
        # 2) 无同 Scene active Run（§三十四）
        active = self.find_active_run(project_name, scene_id)
        if active:
            raise _ExistingRunError(active.run_id)
        # 3) locked 问题（plan 已就绪、无 active run 后才报 locked）
        if not pre.ready:
            raise ValueError(_preflight_issue_to_error(scene, plan, pre))

        # Shot 结构与 plan snapshot 一致（plan 刚 approved 且 not stale，必然一致；双保险）
        struct = _structure_changed(SceneAutopilotRun(
            run_id="_preflight", project_name=project_name, scene_id=scene_id,
            plan_id=plan.plan_id, plan_snapshot=plan.model_dump(), run_seed=0), scene)
        if struct:
            raise ValueError(f"镜头结构与计划不一致：{struct}")

        planned_by_id = {p.shot_id: p for p in plan.shots}

        # 创建 Run + 初始化 ShotExecutionState + 保存 + 启动（与 from_snapshot 共用 _instantiate_run）
        run = self._instantiate_run(project_name, scene, plan, plan.model_dump(),
                                    respect_locked, parent_project_run_id=None)
        await self._save_run(run)
        self._launch_loop(run)
        return run

    def _instantiate_run(self, project_name: str, scene, plan: SceneProductionPlan,
                         plan_snapshot: dict, respect_locked: bool,
                         parent_project_run_id: Optional[str]) -> SceneAutopilotRun:
        """创建 SceneAutopilotRun + 初始化 ShotExecutionState（locked -> skipped_locked）。
        start_scene_run（手工，parent=None）与 start_scene_run_from_snapshot（Project child）共用。"""
        planned_by_id = {p.shot_id: p for p in plan.shots}
        run_id = f"run_{uuid.uuid4().hex[:10]}"
        run_seed = derive_execution_seed(plan.run_seed, run_id)
        run = SceneAutopilotRun(
            run_id=run_id, project_name=project_name, scene_id=scene.scene_id,
            plan_id=plan.plan_id, plan_snapshot=plan_snapshot,
            run_seed=run_seed, status=RUN_PENDING, respect_locked=respect_locked,
            max_parallel_shots=validate_parallelism(plan.max_parallel_shots),
            parent_project_run_id=parent_project_run_id,
            created_at=time.time(), updated_at=time.time(),
        )
        for i, sh in enumerate(scene.shots):
            p = planned_by_id[sh.shot_id]
            st = ShotExecutionState(shot_id=sh.shot_id, index=i, status=SHOT_PENDING)
            if respect_locked and sh.locked and sh.final_video:
                st.status = SHOT_SKIPPED_LOCKED
                st.video_version_id = sh.final_video
                st.completed_stages = build_stage_sequence(p)   # 全部跳过
            run.shots.append(st)
        return run

    def find_by_parent_project_run(self, project_name: str, parent_project_run_id: str,
                                   scene_id: str) -> Optional[SceneAutopilotRun]:
        """crash-safe child 查找（§七）：按 parent_project_run_id + scene_id 找已存在的 child Run
        （任意状态）。Project Controller 创建 child 前先调用，命中则复用，避免 backend crash 后重建第二套。"""
        for r in self.list_runs_for_scene(project_name, scene_id):
            if r.parent_project_run_id == parent_project_run_id:
                return r
        return None

    async def start_scene_run_from_snapshot(self, project_name: str, scene_id: str,
                                            plan_snapshot: dict, *,
                                            respect_locked: bool = True,
                                            parent_project_run_id: str) -> SceneAutopilotRun:
        """从冻结的 plan_snapshot 启动 child SceneRun（§十二，ProjectProductionManager 内部使用，
        不公开给前端）。不读 live production_plans.json（snapshot 是权威执行计划）。
        启动前校验当前 Scene/Shot canonical 结构与 frozen snapshot 相符：
          - 不符 -> _SourceStructureChanged（ProjectRun blocked，error_code=SOURCE_STRUCTURE_CHANGED）
          - locked 无 final / 末帧缺失 -> ValueError（ProjectRun failed）
          - 已有「非本 Project 的」active run -> _ExistingRunError（ProjectRun blocked）
        parent_project_run_id 标记父 ProjectRun（手工 start 为 None）。"""
        from deps import get_project_manager
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        scene = next((s for s in project.scenes if s.scene_id == scene_id), None)
        if scene is None:
            raise _SourceStructureChanged(f"故事段 {scene_id} 已删除")
        try:
            plan = SceneProductionPlan.model_validate(plan_snapshot)
        except Exception as e:
            raise _SourceStructureChanged(f"plan_snapshot 解析失败: {e}")
        # 结构校验：frozen plan 的 scene/shot snapshot 与当前 Scene 比对（§十三 runtime validation）
        stale = is_plan_stale(plan, scene)
        if stale:
            raise _SourceStructureChanged(stale)
        if not scene.shots:
            raise _SourceStructureChanged("故事段已无镜头")
        # locked 校验（复用共享规则，§十八）
        pre = preflight_scene_autopilot(project, scene, plan, respect_locked, has_active_run=False)
        if any(c in pre.issues for c in ("LOCKED_SHOT_NO_FINAL", "LOCKED_SOURCE_NO_VIDEO_END", "SHOT_NOT_IN_PLAN")):
            raise ValueError(_preflight_issue_to_error(scene, plan, pre))
        # active run：本 Project 自己的 child 可复用（find_by_parent 未命中时的防御）；
        #  其他 active run（手工/别的 Project）-> 冲突
        active = self.find_active_run(project_name, scene_id)
        if active and active.parent_project_run_id == parent_project_run_id:
            return active
        if active:
            raise _ExistingRunError(active.run_id)
        run = self._instantiate_run(project_name, scene, plan, plan_snapshot,
                                    respect_locked, parent_project_run_id)
        await self._save_run(run)
        self._launch_loop(run)
        return run

    def _launch_loop(self, run: SceneAutopilotRun):
        current = self._run_tasks.get(run.run_id)
        if current is not None and not current.done():
            return
        task = asyncio.create_task(self._run_loop(run.project_name, run.run_id))
        self._run_tasks[run.run_id] = task
        def finished(done):
            # An older completion callback must not remove a replacement loop.
            if self._run_tasks.get(run.run_id) is done:
                self._run_tasks.pop(run.run_id, None)
        task.add_done_callback(finished)

    async def resume_run(self, project_name: str, run_id: str) -> SceneAutopilotRun:
        def resume(latest):
            if latest.status not in (RUN_PAUSED, RUN_FAILED, RUN_BLOCKED):
                raise ValueError(f"Run 状态 {latest.status} 不可 resume")
            for state in latest.shots:
                if state.status == SHOT_FAILED:
                    state.status = SHOT_PENDING
                    state.error = None
            latest.status = RUN_RUNNING
            latest.error = latest.error_code = None
            latest.pause_requested = latest.cancel_requested = False
        run = await self._edit_run(project_name, run_id, resume)
        self._launch_loop(run)
        return run

    async def request_pause(self, project_name: str, run_id: str) -> SceneAutopilotRun:
        def pause(latest):
            if latest.status not in (RUN_RUNNING, RUN_PENDING, RUN_PAUSING):
                raise ValueError(f"Run 状态 {latest.status} 不可暂停")
            latest.pause_requested = True
            latest.status = RUN_PAUSING
        return await self._edit_run(project_name, run_id, pause)

    async def request_cancel(self, project_name: str, run_id: str) -> SceneAutopilotRun:
        def cancel(latest):
            if latest.status in RUN_TERMINAL:
                raise ValueError(f"Run 已 {latest.status}")
            if latest.status == RUN_PAUSED:
                latest.status = RUN_CANCELLED
                latest.cancel_requested = False
            else:
                latest.cancel_requested = True
        return await self._edit_run(project_name, run_id, cancel)

    async def recover_runs(self):
        """startup 恢复（§三十二）：扫描所有项目的 running/pausing Run，重新调度。"""
        from deps import get_project_manager, logger
        pm = get_project_manager()
        if not pm.base_dir.exists():
            return
        n = 0
        for proj_dir in pm.base_dir.iterdir():
            runs_dir = proj_dir / "output" / "ai" / "autopilot_runs"
            if not runs_dir.exists():
                continue
            for p in runs_dir.glob("*.json"):
                try:
                    run = SceneAutopilotRun.model_validate(
                        __import__("json").loads(p.read_text(encoding="utf-8")))
                except Exception:
                    continue
                current = self._run_tasks.get(run.run_id)
                if current is not None and not current.done():
                    continue  # preserve the live owner's requests/checkpoints
                if run.status in (RUN_RUNNING, RUN_PAUSING, RUN_PENDING):
                    # 重新调度（active job 由 JobQueue 恢复 + find_by_origin 复用）
                    run.status = RUN_RUNNING
                    await self._save_run(run)
                    self._launch_loop(run)
                    n += 1
                    logger.info(f"恢复 Autopilot Run {run.run_id} ({run.project_name}/{run.scene_id})")
        if n:
            logger.info(f"Autopilot 恢复 {n} 个 Run")

    # ---------- 执行循环 ----------
    def _ready_shots(self, run: SceneAutopilotRun, project,
                     active_ids: Optional[set] = None) -> List[ShotExecutionState]:
        """Choose independently runnable shots across the *entire* film.

        A historical ACCEPTED final is not a valid prerequisite when the
        current parent run is recreating its upstream shot. Require its fresh
        child checkpoint and media version, even across scene boundaries.
        """
        plan = SceneProductionPlan.model_validate(run.plan_snapshot)
        deps = shot_dependencies(project, {run.scene_id: plan},
                                 selected_scene_ids=[run.scene_id])
        states = {st.shot_id: st for st in run.shots}
        active_ids = active_ids or set()
        planned = {p.shot_id: p for p in plan.shots}
        parent = None
        if run.parent_project_run_id:
            from src.ai.project_autopilot import get_project_autopilot_manager
            parent = get_project_autopilot_manager().get_run(
                run.project_name, run.parent_project_run_id)
            if parent is None:
                return []
            if parent.shot_dependencies:
                deps = {st.shot_id: set(parent.shot_dependencies.get(st.shot_id, []))
                        for st in run.shots}
            if parent.reference_asset_snapshot:
                for st in run.shots:
                    ref = getattr(project.get_shot(st.shot_id),
                                  "video_reference_asset_id", None)
                    if ref != parent.reference_asset_snapshot.get(st.shot_id):
                        raise RuntimeError("frozen C2 reference selection changed during parallel run")
        ready = []
        for st in run.shots:
            if st.status in SHOT_TERMINAL or st.shot_id in active_ids:
                continue
            p = planned.get(st.shot_id)
            if p is None:
                continue
            sources = deps.get(st.shot_id, set())
            media = media_sources_for_shot(project, p, st.shot_id)
            def source_ready(source):
                if source in states:
                    upstream = states[source]
                    if upstream.status not in (SHOT_COMPLETED, SHOT_SKIPPED_LOCKED):
                        return False
                    if source in media:
                        return (bool(upstream.video_version_id) and
                                upstream_ready(project, source,
                                               expected_version=upstream.video_version_id))
                    return True
                return source_completed_for_run(
                    project, source, parent_run=parent,
                    scene_manager=self if parent is not None else None,
                    require_media=source in media)
            if all(source_ready(source) for source in sources):
                ready.append(st)
        return sorted(ready, key=lambda item: item.index)

    def _pending_external_source(self, run: SceneAutopilotRun, project) -> bool:
        """Wait only when an unfinished earlier scene can still unblock us.

        Do not busy-loop forever for missing/unselected sources, a failed
        upstream scene, or an old version after the source has completed.
        """
        if not run.parent_project_run_id:
            return False
        from src.ai.project_autopilot import get_project_autopilot_manager
        parent = get_project_autopilot_manager().get_run(
            run.project_name, run.parent_project_run_id)
        if parent is None or parent.status in ("failed", "blocked", "cancelled"):
            return False
        plan = SceneProductionPlan.model_validate(run.plan_snapshot)
        deps = shot_dependencies(project, {run.scene_id: plan},
                                 selected_scene_ids=[run.scene_id])
        mine = {st.shot_id for st in run.shots}
        source_scene = {sh.shot_id: scene.scene_id for scene in project.scenes
                        for sh in scene.shots}
        entries = {entry.scene_id: entry for entry in parent.scenes}
        for shot in run.shots:
            if shot.status in SHOT_TERMINAL:
                continue
            for source in deps.get(shot.shot_id, set()) - mine:
                entry = entries.get(source_scene.get(source))
                if entry and entry.status in ("pending", "running"):
                    return True
        return False

    def _next_ready_shot(self, run: SceneAutopilotRun, project) -> Optional[ShotExecutionState]:
        """Legacy helper: deterministic first item of the dependency-ready set."""
        ready = self._ready_shots(run, project)
        return ready[0] if ready else None

    async def _run_loop(self, project_name: str, run_id: str):
        """Actual parallel C2 Scene executor, not merely a two-worker setting.

        Only dependency-ready shots are launched concurrently. In-progress
        tasks are drained on pause/cancel/failure; no new stages are dispatched
        after a terminal or pause flag. JobQueue keeps provider POST idempotent.
        """
        from deps import logger, get_project_manager
        tasks: Dict[str, asyncio.Task] = {}
        try:
            while True:
                run = self._load_run(project_name, run_id)
                if run is None:
                    return
                # Finished coroutines may finish *before* their tasks are reaped.
                for sid, task in list(tasks.items()):
                    if task.done():
                        tasks.pop(sid)
                        if not task.cancelled() and task.exception():
                            await self._fail_shot(run, sid, str(task.exception())[:300])
                run = self._load_run(project_name, run_id)
                if run.status in RUN_TERMINAL or run.pause_requested or run.cancel_requested:
                    if tasks:
                        await asyncio.wait(tasks.values(), timeout=0.1,
                                           return_when=asyncio.FIRST_COMPLETED)
                        continue
                    if run.status in RUN_TERMINAL:
                        return
                    await self._edit_run(project_name, run_id, lambda latest: (
                        setattr(latest, "status", RUN_CANCELLED if latest.cancel_requested else RUN_PAUSED),
                        setattr(latest, "cancel_requested", False),
                        setattr(latest, "pause_requested", False),
                        setattr(latest, "current_shot_id", None),
                        setattr(latest, "current_stage", None)))
                    return

                project = ProjectStore(get_project_manager().get_project_dir(project_name)).load()
                scene = next((sc for sc in project.scenes if sc.scene_id == run.scene_id), None)
                if scene is None:
                    await self._edit_run(project_name, run_id, lambda latest: (
                        setattr(latest, "status", RUN_FAILED),
                        setattr(latest, "error", "故事段已删除")))
                    continue
                # Before launching any new shot, validate the complete frozen
                # scene structure; never post against a half-edited revision.
                ready = self._ready_shots(run, project, set(tasks))
                if ready and _structure_changed(run, scene):
                    await self._edit_run(project_name, run_id, lambda latest: (
                        setattr(latest, "status", RUN_BLOCKED),
                        setattr(latest, "error_code", "STRUCTURE_CHANGED"),
                        setattr(latest, "error", "故事段或镜头规划在自动制作期间发生了变化")))
                    continue
                slots = max(0, validate_parallelism(run.max_parallel_shots) - len(tasks))
                for candidate in ready[:slots]:
                    sid = candidate.shot_id
                    def start(latest, shot_id=sid):
                        if (latest.pause_requested or latest.cancel_requested or
                                latest.status in RUN_TERMINAL):
                            return
                        st = next(st for st in latest.shots if st.shot_id == shot_id)
                        st.status = SHOT_RUNNING
                        latest.status = RUN_RUNNING
                        current = [s for s in latest.shots if s.status == SHOT_RUNNING]
                        latest.current_shot_id = min(current, key=lambda s: s.index).shot_id
                    run = await self._edit_run(project_name, run_id, start)
                    if run.pause_requested or run.cancel_requested or run.status in RUN_TERMINAL:
                        break
                    tasks[sid] = asyncio.create_task(self._execute_shot(project_name, run, sid))
                if not tasks:
                    run = self._load_run(project_name, run_id)
                    if all(s.status in (SHOT_COMPLETED, SHOT_SKIPPED_LOCKED) for s in run.shots):
                        status, error = RUN_COMPLETED, None
                    elif self._pending_external_source(run, project):
                        # Another scene is still producing the genuine tail.
                        # Keep this child bound to its original Run ID; it may
                        # already have completed independent shots.
                        await asyncio.sleep(0.2)
                        continue
                    else:
                        status, error = RUN_BLOCKED, "无可用镜头（依赖未满足）"
                    def finish(latest):
                        latest.status = status
                        latest.error = error
                        latest.current_shot_id = latest.current_stage = None
                    await self._edit_run(project_name, run_id, finish)
                    return
                await asyncio.wait(tasks.values(), timeout=0.15,
                                   return_when=asyncio.FIRST_COMPLETED)
        except Exception as e:
            logger.exception(f"Autopilot _run_loop {run_id} 异常")
            try:
                await self._edit_run(project_name, run_id, lambda latest: (
                    setattr(latest, "status", RUN_FAILED),
                    setattr(latest, "error", f"执行循环异常: {str(e)[:400]}")))
            finally:
                # If a coordinator fails, do not abandon already-submitted jobs.
                if tasks:
                    await asyncio.gather(*tasks.values(), return_exceptions=True)

    async def _execute_shot(self, project_name: str, run: SceneAutopilotRun, shot_id: str):
        """One shot task owns only its ShotExecutionState; stages checkpoint atomically."""
        from deps import get_project_manager
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        planned = _planned_for(run, shot_id)
        if planned is None:
            await self._fail_shot(run, shot_id, "计划中无此镜头")
            return
        for stage in build_stage_sequence(planned):
            run = self._load_run(project_name, run.run_id)
            state = next((s for s in run.shots if s.shot_id == shot_id), None)
            if state is None or run.status in RUN_TERMINAL:
                return
            if stage in state.completed_stages:
                continue
            if run.pause_requested or run.cancel_requested:
                return
            state.stage = stage
            await self._save_shot_state(run, state)
            try:
                await self._execute_stage(project_name, run, state, stage, planned, store, project_dir)
                run = self._load_run(project_name, run.run_id)
                state = next((s for s in run.shots if s.shot_id == shot_id), None)
                if state is None:
                    return
                if stage not in state.completed_stages:
                    state.completed_stages.append(stage)
                await self._save_shot_state(run, state)
            except Exception as e:
                await self._fail_shot(run, shot_id, f"stage {stage} 失败: {str(e)[:300]}")
                return
        run = self._load_run(project_name, run.run_id)
        state = next((s for s in run.shots if s.shot_id == shot_id), None)
        if state is not None:
            state.status = SHOT_COMPLETED
            state.stage = None
            await self._save_shot_state(run, state)

    async def _fail_shot(self, run: SceneAutopilotRun, shot_id: str, error: str):
        """Fail exactly one shot while preserving all sibling checkpoints."""
        def fail(latest):
            state = next((s for s in latest.shots if s.shot_id == shot_id), None)
            if state is not None:
                state.status = SHOT_FAILED
                state.error = error
            if latest.status not in (RUN_CANCELLED, RUN_PAUSED):
                latest.status = RUN_FAILED
                latest.error = f"镜头 {shot_id}: {error}"
        await self._edit_run(run.project_name, run.run_id, fail)

    # ---------- stage 派发 ----------
    async def _execute_stage(self, project_name: str, run: SceneAutopilotRun,
                             state: ShotExecutionState, stage: str,
                             planned: PlannedShot, store: ProjectStore, project_dir: Path):
        from routers.shots import (generate_prompt_for_stage, activate_planned_shot,
                                   choose_first_frame_business, choose_last_frame_business,
                                   set_final_video_business, _extract_video_end_frame)

        if stage == STAGE_ACTIVATE:
            project = store.load()
            activate_planned_shot(store, project, state.shot_id, planned)
            return

        if stage == STAGE_FIRST_PROMPT:
            r = await generate_prompt_for_stage(project_name, state.shot_id, "first", persist=True)
            state.first_prompt = r["prompt"]
            return

        if stage == STAGE_FIRST_FRAME:
            await self._do_first_frame(project_name, run, state, store)
            return

        if stage == STAGE_LAST_PROMPT:
            r = await generate_prompt_for_stage(project_name, state.shot_id, "last", persist=True)
            state.last_prompt = r["prompt"]
            return

        if stage == STAGE_LAST_FRAME:
            await self._do_last_frame(project_name, run, state, store)
            return

        if stage == STAGE_VIDEO_PROMPT:
            r = await generate_prompt_for_stage(project_name, state.shot_id, "video", persist=True)
            state.video_prompt = r["prompt"]
            return

        if stage == STAGE_VIDEO:
            await self._do_video(project_name, run, state, store, project_dir)
            return

        if stage == STAGE_VIDEO_END:
            await self._do_video_end(project_name, run, state, store, project_dir)
            return

    # ---------- first / last / video / video_end 具体执行 ----------
    async def _enqueue_autopilot_job(self, project_name: str, shot_id: str, kind: str,
                                     stage_key: str, run_id: str, **params):
        """幂等入队（§十六）：先 find_by_origin，已有 queued/running/done 则复用，不重复 submit。"""
        from deps import get_job_queue
        q = get_job_queue()
        existing = q.find_by_origin(run_id, stage_key)
        if existing is not None:
            if existing.state == "cancelled":
                raise RuntimeError("stage was cancelled; explicit new authorization is required")
            if existing.state == "failed" and kind in ("first_frame", "last_frame"):
                # Explicit Run resume reuses the failed job and any image receipt.
                # Cancelled work and ambiguous paid-video submissions stay blocked.
                return q.retry(existing.job_id) or existing
            if existing.state == "failed" and kind == "video":
                receipt = getattr(existing, "params", {}) or {}
                paid = any(receipt.get(k) for k in (
                    "provider_task_id", "submission_intent_id", "dispatch_prompt"))
                # Only input preparation failed, before any paid submission.
                # The exact legacy timeout also identifies the original gate.
                tail_not_ready = (
                    getattr(existing, "error", None) == "等待尾帧超时（尾帧尚未生成完成），请重试"
                    and getattr(existing, "error_stage", None) == "wait")
                # r46 hop6（2026-10-31）实测回填：供应商 429 额度拒绝是
                # 「确定性未受理」——无任务号、零扣费，但持久化的 job error
                # 会在 resume 时原样重放（request id 都是前一天的），4 秒内
                # 判死 run，按天接续闸门重开后永远走不完最后一公里。与
                # tools/quota_gate.classify_quota_rejection 同语义（4xx 确定性
                # 拒绝 + 额度关键词），但 job error 是纯字符串无 status 属性。
                # 5xx/无状态码=结局不明，维持 §二十四 不重排纪律。
                # r46 hop7（2026-10-31 run 41160317704 实测回填）：
                # submission_intent_id / dispatch_prompt 是 handler 在 POST
                # 之前登记的意图收据，429 拒收时**未兑现**（artifact 实证该
                # job provider_task_id=None、request id 为前一日、零新请求）。
                # 把意图字段当已付费证据会让 quota_rejected 永远 False——
                # 旧错误原样重放。真付费证据 = provider_task_id（受理后回填）。
                provider_accepted = bool(receipt.get("provider_task_id"))
                quota_rejected = (not provider_accepted
                                  and _provider_rejected_quota(getattr(existing, "error", None)))
                if (receipt.get("video_pre_submit_failure") == "last_frame_url"
                        or tail_not_ready) and not paid:
                    return q.retry(existing.job_id) or existing
                if quota_rejected:
                    # 意图条目未兑现即重排是安全的（无任务号零扣费）；
                    # 遗留的未兑现 intent 条目由 B1 对账侧审计（见 C 仓
                    # handoff 文档），此处不静默清除账本状态。
                    return q.retry(existing.job_id) or existing
            return existing
        if kind == "video":
            # The C2 executor submits directly to JobQueue (not through the
            # Producer REST batch endpoint). Carry the same exact pinned tail
            # snapshot used by that endpoint into the video handler's pre-POST
            # and post-result atomic Store guards. This closes the queue-time
            # upstream selection race for parallel repair/revision runs.
            from deps import get_project_manager
            from src.agents.producer_orchestration import video_submission_dependencies
            current = ProjectStore(get_project_manager().get_project_dir(project_name)).load()
            _, blocked, snapshots = video_submission_dependencies(
                current, [{"shot_id": shot_id, "frame": "video"}])
            if blocked:
                raise RuntimeError(
                    "C2 video dependency not ready: " +
                    str(blocked[0].get("dependency_reason") or blocked[0].get("error")))
            params["continuity_dependency_snapshot"] = snapshots.get(shot_id)
        return q.submit(project_name, kind, shot_id,
                        origin_run_id=run_id, autopilot_stage_key=stage_key, **params)

    async def _wait_job(self, job_id: str):
        """等待 JobQueue job terminal（§十七）。Executor 不直接 poll Agnes。"""
        from deps import get_job_queue
        q = get_job_queue()
        while True:
            job = q.get(job_id)
            if job is None:
                raise RuntimeError(f"job {job_id} 不存在（可能被清理）")
            if job.state in ("done", "failed", "cancelled"):
                return job
            await asyncio.sleep(1.0)

    def _find_run_batch(self, shot, run_id: str, kind: str):
        """找本 Run 生成的批次（origin_run_id 过滤，取最新，§十五 不用裸 [-1]）。"""
        batches = shot.first_frame_batches if kind == "first_frame" else shot.last_frame_batches
        own = [b for b in batches if b.origin_run_id == run_id]
        return own[-1] if own else None

    async def _do_first_frame(self, project_name: str, run: SceneAutopilotRun,
                              state: ShotExecutionState, store: ProjectStore):
        """首帧：入队 first_frame job -> 等待 -> 找本 Run batch -> seeded 选择（§十四/十八/十九）。"""
        from src.core.models import ShotStatus as SS
        from routers.shots import choose_first_frame_business
        for attempt in range(1, 3):   # 最多 2 个 batch attempts（§十九）
            stage_key = f"autopilot:{run.run_id}:{state.shot_id}:first_frame:{attempt}"
            job = await self._enqueue_autopilot_job(project_name, state.shot_id, "first_frame",
                                                    stage_key, run.run_id, variants=2)
            state.active_job_id = job.job_id
            state.active_stage_key = stage_key
            await self._save_shot_state(run, state)
            job = await self._wait_job(job.job_id)
            state.active_job_id = None
            if job.state != "done":
                state.attempts["first_frame"] = attempt
                continue   # retry
            shot = store.load().get_shot(state.shot_id)
            batch = self._find_run_batch(shot, run.run_id, "first_frame")
            if not batch:
                state.attempts["first_frame"] = attempt
                continue
            successful = [c for c in batch.candidates if c.status == SS.DONE.value]
            if not successful:
                state.warnings.append(f"首帧批次 {batch.batch_id} 0 成功候选")
                state.attempts["first_frame"] = attempt
                continue
            chosen = choose_candidate(run.run_seed, state.shot_id, batch.batch_id, successful)
            # chosen 是 Candidate 对象（来自 shot batches）或 dict（来自 choose_candidate）
            cid = chosen.candidate_id if hasattr(chosen, "candidate_id") else chosen["candidate_id"]
            choose_first_frame_business(store, state.shot_id, cid)
            state.first_batch_id = batch.batch_id
            state.selected_first_candidate_id = cid
            await self._save_shot_state(run, state)
            return
        raise RuntimeError("首帧生成失败（2 次 batch 均无成功候选）")

    async def _do_last_frame(self, project_name: str, run: SceneAutopilotRun,
                             state: ShotExecutionState, store: ProjectStore):
        """尾帧：入队 last_frame job -> 等待 -> 找本 Run batch -> 选择（§二十/二十一）。"""
        from src.core.models import ShotStatus as SS
        from routers.shots import choose_last_frame_business
        for attempt in range(1, 3):
            stage_key = f"autopilot:{run.run_id}:{state.shot_id}:last_frame:{attempt}"
            job = await self._enqueue_autopilot_job(project_name, state.shot_id, "last_frame",
                                                    stage_key, run.run_id)
            state.active_job_id = job.job_id
            state.active_stage_key = stage_key
            await self._save_shot_state(run, state)
            job = await self._wait_job(job.job_id)
            state.active_job_id = None
            if job.state != "done":
                state.attempts["last_frame"] = attempt
                continue
            shot = store.load().get_shot(state.shot_id)
            batch = self._find_run_batch(shot, run.run_id, "last_frame")
            if not batch:
                state.attempts["last_frame"] = attempt
                continue
            successful = [c for c in batch.candidates if c.status == SS.DONE.value]
            if not successful:
                state.attempts["last_frame"] = attempt
                continue
            chosen = choose_candidate(run.run_seed, state.shot_id, batch.batch_id, successful)
            cid = chosen.candidate_id if hasattr(chosen, "candidate_id") else chosen["candidate_id"]
            choose_last_frame_business(store, state.shot_id, cid)
            state.last_batch_id = batch.batch_id
            state.selected_last_candidate_id = cid
            await self._save_shot_state(run, state)
            return
        raise RuntimeError("尾帧生成失败（2 次 batch 均无成功候选）")

    async def _do_video(self, project_name: str, run: SceneAutopilotRun,
                        state: ShotExecutionState, store: ProjectStore, project_dir: Path):
        """视频：入队 video job -> 等待 -> 找本 Run version -> 确认 final（§二十三/二十四/二十五）。
        video handler 已自动 set final + 提取末帧；本处确认 final 指向本 Run 版本。"""
        from routers.shots import set_final_video_business
        # 1 次 Executor-level retry（§二十四：尊重 video_id 恢复，handler 内部续轮询不重提交）
        job = None
        for attempt in range(1, 3):
            stage_key = f"autopilot:{run.run_id}:{state.shot_id}:video:{attempt}"
            # 🔴 C2 本地 60 秒验证修复（2026-10-27）：tail_host_required=False
            # -> video handler 只在本地提取末帧、不做图床托管。托管改由本镜的
            # video_end stage 按「下游是否真的消费公网尾帧」决定（见 _do_video_end）。
            # 原因：github_pages 1 PUT = 1 次 Pages 构建（实测 232s），在 video worker
            # 内托管会把视频 worker 占住一个构建周期，直接压低并行度。
            job = await self._enqueue_autopilot_job(project_name, state.shot_id, "video",
                                                    stage_key, run.run_id,
                                                    tail_host_required=False)
            state.active_job_id = job.job_id
            state.active_stage_key = stage_key
            await self._save_shot_state(run, state)
            job = await self._wait_job(job.job_id)
            state.active_job_id = None
            if job.state == "done":
                break
            state.attempts["video"] = attempt
        else:
            raise RuntimeError(f"视频生成失败: {(job.error if job else '未知')}")
        # 找本 Run 生成的版本
        shot = store.load().get_shot(state.shot_id)
        own = [v for v in shot.video_versions if v.origin_run_id == run.run_id]
        if not own:
            raise RuntimeError("视频 job 完成但未找到本 Run 生成的版本")
        vv = own[-1]
        # §二十五：确认 final 指向本 Run 版本（handler 已自动设；不一致则显式 set-final）
        if shot.final_video != vv.version_id:
            await set_final_video_business(store, project_dir, state.shot_id, vv.version_id)
        state.video_version_id = vv.version_id
        await self._save_shot_state(run, state)

    async def _do_video_end(self, project_name: str, run: SceneAutopilotRun,
                            state: ShotExecutionState, store: ProjectStore, project_dir: Path):
        """视频末帧：确认对应本 Run 版本的 video_end_frame 就绪（§二十六）。失败时按下游依赖决定 fail/warning。"""
        from routers.shots import _extract_video_end_frame
        shot = store.load().get_shot(state.shot_id)
        if not state.video_version_id:
            raise RuntimeError("无 video_version_id，无法校验末帧")
        # 已就绪且对应本 Run 版本
        if (shot.video_end_frame.url_ready
                and shot.video_end_source_version == state.video_version_id):
            return
        # 🔴 C2 本地 60 秒验证修复（2026-10-27）：先判定下游是否真的需要「公网末帧 URL」，
        # 再据此决定是否做图床托管。github_pages 图床 1 次 PUT = 1 次 Pages 构建
        # （实测 232s，构建 soft limit 10 次/小时），无下游依赖的镜头也托管会把构建数
        # 从 3 推到 12——既逼近限额，又让 9 个镜头各白等一个构建周期。
        # 依赖判定逻辑（§二十六）本身不变，只是提前求值 + 按需托管。
        idx = next((i for i, s in enumerate(run.shots) if s.shot_id == state.shot_id), -1)
        downstream_needs = False
        if idx >= 0 and idx + 1 < len(run.shots):
            nxt_planned = _planned_for(run, run.shots[idx + 1].shot_id)
            if nxt_planned and nxt_planned.requires_previous_video_end:
                downstream_needs = True
        # refresh 一次（§二十六）
        vv = next((v for v in shot.video_versions if v.version_id == state.video_version_id), None)
        if vv and vv.local and Path(vv.local).exists():
            try:
                end_asset, _ok = await _extract_video_end_frame(
                    project_dir, state.shot_id, vv.local, vv.version_id,
                    host=downstream_needs)
                store.update_shot(state.shot_id, lambda s, ea=end_asset, sv=vv.version_id: (
                    setattr(s, "video_end_frame", ea), setattr(s, "video_end_source_version", sv)))
                if end_asset.url_ready:
                    return
                if not downstream_needs and not end_asset.error:
                    # 托管按需跳过（host=False）——提取本身成功，非失败，无需告警。
                    return
            except Exception as e:
                state.warnings.append(f"末帧提取异常: {str(e)[:120]}")
        # 仍未就绪：看下游是否依赖（§二十六）
        if downstream_needs:
            raise RuntimeError("视频末帧提取失败，下游镜头依赖它")
        state.warnings.append("视频已完成，但实际末帧提取失败。后续镜头不依赖它，因此继续。")
        await self._save_shot_state(run, state)


class _ExistingRunError(Exception):
    """同 Scene 已有 active Run（§三十四）。携带 existing_run_id。"""
    def __init__(self, existing_run_id: str):
        self.existing_run_id = existing_run_id
        super().__init__(f"已有 active Run: {existing_run_id}")


class _SourceStructureChanged(Exception):
    """AI-5A：child 从 frozen plan_snapshot 启动时，当前 Scene/Shot canonical 结构与 snapshot 不符。
    Project Controller 捕获 -> ProjectRun blocked（error_code=SOURCE_STRUCTURE_CHANGED），不可 resume 旧 source。"""
    pass


# ============ 单例 ============
_manager: Optional[SceneAutopilotManager] = None

def get_autopilot_manager() -> SceneAutopilotManager:
    global _manager
    if _manager is None:
        _manager = SceneAutopilotManager()
    return _manager
