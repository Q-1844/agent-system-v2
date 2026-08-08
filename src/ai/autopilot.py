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
        task = asyncio.create_task(self._run_loop(run.project_name, run.run_id))
        self._run_tasks[run.run_id] = task
        task.add_done_callback(lambda t: self._run_tasks.pop(run.run_id, None))

    async def resume_run(self, project_name: str, run_id: str) -> SceneAutopilotRun:
        run = self._load_run(project_name, run_id)
        if run is None:
            raise ValueError("Run 不存在")
        if run.status not in (RUN_PAUSED, RUN_FAILED, RUN_BLOCKED):
            raise ValueError(f"Run 状态 {run.status} 不可 resume")
        run.status = RUN_RUNNING
        run.error = None
        run.error_code = None
        await self._save_run(run)
        self._launch_loop(run)
        return run

    async def request_pause(self, project_name: str, run_id: str) -> SceneAutopilotRun:
        run = self._load_run(project_name, run_id)
        if run is None:
            raise ValueError("Run 不存在")
        if run.status not in (RUN_RUNNING, RUN_PENDING):
            raise ValueError(f"Run 状态 {run.status} 不可暂停")
        run.pause_requested = True
        run.status = RUN_PAUSING
        await self._save_run(run)
        return run

    async def request_cancel(self, project_name: str, run_id: str) -> SceneAutopilotRun:
        run = self._load_run(project_name, run_id)
        if run is None:
            raise ValueError("Run 不存在")
        if run.status in RUN_TERMINAL:
            raise ValueError(f"Run 已 {run.status}")
        run.cancel_requested = True
        await self._save_run(run)
        return run

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
    async def _run_loop(self, project_name: str, run_id: str):
        """依赖 scheduler（§二）：从所有 dependency 已满足的 Shot 中按 index 选第一个执行。
        第一版严格串行（一次只跑一个 Shot）；未来并发只需改放行数量。"""
        from deps import logger, get_project_manager
        try:
            while True:
                run = self._load_run(project_name, run_id)
                if run is None:
                    return
                # 已终止（失败/完成/取消/blocked）-> 不再循环（_execute_shot 内 fail 后由此退出）
                if run.status in RUN_TERMINAL:
                    return
                # pause / cancel checkpoint
                if run.cancel_requested:
                    run.cancel_requested = False
                    run.status = RUN_CANCELLED
                    await self._save_run(run); return
                if run.pause_requested:
                    run.pause_requested = False
                    run.status = RUN_PAUSED
                    await self._save_run(run); return
                run.status = RUN_RUNNING

                project = ProjectStore(get_project_manager().get_project_dir(project_name)).load()
                scene = next((s for s in project.scenes if s.scene_id == run.scene_id), None)
                if scene is None:
                    run.status = RUN_FAILED; run.error = "故事段已删除"
                    await self._save_run(run); return

                state = self._next_ready_shot(run, project)
                if state is None:
                    if all(s.status in SHOT_TERMINAL for s in run.shots):
                        run.status = RUN_COMPLETED
                    else:
                        run.status = RUN_BLOCKED
                        if not run.error:
                            run.error = "无可用镜头（依赖未满足）"
                    run.current_shot_id = None; run.current_stage = None
                    await self._save_run(run); return

                # 尚未开始的 Shot：检查结构是否变化（§三十五）
                if state.status == SHOT_PENDING:
                    changed = _structure_changed(run, scene)
                    if changed:
                        run.status = RUN_BLOCKED
                        run.error_code = "STRUCTURE_CHANGED"
                        run.error = "故事段或镜头规划在自动制作期间发生了变化"
                        await self._save_run(run); return

                state.status = SHOT_RUNNING
                run.current_shot_id = state.shot_id
                await self._save_run(run)

                await self._execute_shot(project_name, run, state.shot_id)
                # 循环继续：reload run；若 _execute_shot 内 fail/blocked，下一轮顶部 RUN_TERMINAL 退出
        except Exception as e:
            logger.exception(f"Autopilot _run_loop {run_id} 异常")
            run = self._load_run(project_name, run_id)
            if run:
                run.status = RUN_FAILED
                run.error = f"执行循环异常: {str(e)[:400]}"
                await self._save_run(run)

    def _next_ready_shot(self, run: SceneAutopilotRun, project) -> Optional[ShotExecutionState]:
        """从所有 dependency 已满足的非 terminal Shot 中，按 index 选第一个（§二）。
        PENDING/WAITING 都重新校验依赖（避免 WAITING 后不再重检的 bug）。"""
        ready = []
        for st in run.shots:
            if st.status in SHOT_TERMINAL:
                continue
            planned = _planned_for(run, st.shot_id)
            if planned is None:
                continue
            if st.status == SHOT_RUNNING:
                return st   # 正在执行（resume 场景，串行下一般不出现）
            if dependency_ready(project, planned):
                st.status = SHOT_PENDING
                ready.append(st)
            else:
                st.status = SHOT_WAITING
        if not ready:
            return None
        return min(ready, key=lambda s: s.index)

    async def _execute_shot(self, project_name: str, run: SceneAutopilotRun, shot_id: str):
        """执行单个 Shot 的所有 stage（按 checkpoint 跳过已完成的，§五）。"""
        from deps import get_project_manager
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        planned = _planned_for(run, shot_id)
        if planned is None:
            await self._fail_shot(run, shot_id, "计划中无此镜头")
            return
        stages = build_stage_sequence(planned)

        for stage in stages:
            # reload 最新 run/state
            run = self._load_run(project_name, run.run_id)
            state = next((s for s in run.shots if s.shot_id == shot_id), None)
            if state is None:
                return
            # checkpoint：已完成则跳过（§五 Resume）
            if stage in state.completed_stages:
                continue
            # pause/cancel checkpoint
            if run.pause_requested or run.cancel_requested:
                return
            state.stage = stage
            run.current_stage = stage
            await self._save_run(run)
            try:
                await self._execute_stage(project_name, run, state, stage, planned, store, project_dir)
                # reload + 标记完成
                run = self._load_run(project_name, run.run_id)
                state = next((s for s in run.shots if s.shot_id == shot_id), None)
                if state is None:
                    return
                if stage not in state.completed_stages:
                    state.completed_stages.append(stage)
                await self._save_run(run)
            except Exception as e:
                await self._fail_shot(run, shot_id, f"stage {stage} 失败: {str(e)[:300]}")
                return
        # Shot 全部 stage 完成
        run = self._load_run(project_name, run.run_id)
        state = next((s for s in run.shots if s.shot_id == shot_id), None)
        if state:
            state.status = SHOT_COMPLETED
            state.stage = None
            await self._save_run(run)

    async def _fail_shot(self, run: SceneAutopilotRun, shot_id: str, error: str):
        """Shot 失败 -> Run 失败。"""
        from deps import get_project_manager
        run = self._load_run(run.project_name, run.run_id)
        state = next((s for s in run.shots if s.shot_id == shot_id), None)
        if state:
            state.status = SHOT_FAILED
            state.error = error
        run.status = RUN_FAILED
        run.error = f"镜头 {shot_id}: {error}"
        await self._save_run(run)

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
            return existing
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
            await self._save_run(run)
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
            await self._save_run(run)
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
            await self._save_run(run)
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
            await self._save_run(run)
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
            job = await self._enqueue_autopilot_job(project_name, state.shot_id, "video",
                                                    stage_key, run.run_id)
            state.active_job_id = job.job_id
            state.active_stage_key = stage_key
            await self._save_run(run)
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
        await self._save_run(run)

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
        # refresh 一次（§二十六）
        vv = next((v for v in shot.video_versions if v.version_id == state.video_version_id), None)
        if vv and vv.local and Path(vv.local).exists():
            try:
                end_asset, _ok = await _extract_video_end_frame(
                    project_dir, state.shot_id, vv.local, vv.version_id)
                store.update_shot(state.shot_id, lambda s, ea=end_asset, sv=vv.version_id: (
                    setattr(s, "video_end_frame", ea), setattr(s, "video_end_source_version", sv)))
                if end_asset.url_ready:
                    return
            except Exception as e:
                state.warnings.append(f"末帧提取异常: {str(e)[:120]}")
        # 仍未就绪：看下游是否依赖（§二十六）
        idx = next((i for i, s in enumerate(run.shots) if s.shot_id == state.shot_id), -1)
        downstream_needs = False
        if idx >= 0 and idx + 1 < len(run.shots):
            nxt_planned = _planned_for(run, run.shots[idx + 1].shot_id)
            if nxt_planned and nxt_planned.requires_previous_video_end:
                downstream_needs = True
        if downstream_needs:
            raise RuntimeError("视频末帧提取失败，下游镜头依赖它")
        state.warnings.append("视频已完成，但实际末帧提取失败。后续镜头不依赖它，因此继续。")
        await self._save_run(run)


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
