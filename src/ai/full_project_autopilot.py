# src/ai/full_project_autopilot.py
"""Full Project Autopilot / AI 完整制作（Phase AI-5B）。

最外层 Controller：用户已有 Project + Scene[] + Project Characters，但允许缺 Shot / 缺 Plan /
draft / stale。点击一次 [✨ AI 完整制作] -> 自动补齐所有缺失（Director/Planner）-> 全项目准备完成 ->
冻结 -> 调用现有 ProjectProductionManager（AI-5A）-> 全部 SceneAutopilotRun -> compose -> final.mp4。
全程不等待 Director/Planner/候选/Scene 确认。

🔴 分层（§一，不扩 ProjectProductionManager 让它同时负责 AI Director）：
  FullProjectManager -> Preparation（Director/Planner 补齐）
                  -> ProjectProductionManager（AI-5A，已准备项目 -> 多 SceneRun -> composition）
                  -> SceneAutopilotManager（AI-4B，一个 Scene 的真实生成）
                  -> JobQueue
  FullProjectManager 是确定性 Controller，不是开放式 Agent（§六十五）。真正需要语义判断时只调用
  已存在的 DirectorWorkflow / ProductionPlannerWorkflow；不问 Agnes「下一步做什么」。

🔴 执行顺序（§二）：Phase A Preparation（所有 Scene prepare -> 全 ready）-> Phase B Production
  （冻结 -> ProjectProductionRun -> 全部 SceneRun -> compose）。prepare 是快速文本 AI 阶段，
  production 是昂贵长任务阶段。Scene 7 的 Director/Planner 失败应在任何视频生产前发现。

🔴 已有 Shot 红线（§六）：scene.shots 非空时 AI-5B 绝不调 AI Director 改 Shot。只有 scene.shots==[]
  才允许 Director 创建 Shot。已有 Shot -> 只检查 Production Plan。

🔴 fresh draft 复用（§七/§十五）：fresh Director Draft（Scene 无 Shot）-> 自动采用；fresh Planner
  Draft -> 自动 approve。只有缺失/stale/invalid 才重新请求 AI。「一键自动完成剩余工作」而非
  「一键把用户之前做的所有工作重新做一遍」（§五十八）。

🔴 crash safety：
  - Director write-ahead（§十一）：Director result -> 保存到 Preparation Manifest（director_result_saved）
    -> atomic save -> 再 apply 到 project.json。
  - Director apply 后 crash reconciliation（§十二）：resume 时若 director_result_saved 但未 director_applied，
    比对当前 scene.shots 与 expected_director_plan；匹配->director_applied；不匹配->blocked(EXTERNAL_STRUCTURE_CHANGED)。
  - expected_source_signature（§二十四）：FullRun 自己的 Director apply 是合法 canonical 修改 -> 更新 expected；
    用户外部改 Scene -> 非预期 -> blocked(EXTERNAL_SOURCE_CHANGED)。
  - Production Plan fingerprint（§二十六）：plan action 前比对；外部改 -> blocked(PRODUCTION_PLAN_CHANGED_EXTERNALLY)。
  - Production child crash window（§四十二）：find_by_parent_full_run 防重复 ProjectProductionRun。

Run 目录（§二十三）：projects/{p}/output/full_runs/{run_id}/{run.json, initial_source_snapshot.json,
  preparation_manifest.json}。final.mp4 不复制（§六十），final_output 指向 production_runs/{child}/final.mp4。
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field

from src.core.store import ProjectStore, _atomic_write_json
from src.ai.project_autopilot import (
    get_project_autopilot_manager, build_project_source_signature,
    PRUN_PENDING as PROD_PENDING, PRUN_RUNNING as PROD_RUNNING, PRUN_PAUSED as PROD_PAUSED,
    PRUN_FAILED as PROD_FAILED, PRUN_BLOCKED as PROD_BLOCKED, PRUN_COMPOSING as PROD_COMPOSING,
    PRUN_COMPLETED as PROD_COMPLETED, PRUN_CANCELLED as PROD_CANCELLED,
    PRUN_ACTIVE as PROD_ACTIVE, PRUN_TERMINAL as PROD_TERMINAL,
    _ExistingProjectRunError,
)
from src.ai.autopilot import (
    get_autopilot_manager, preflight_scene_autopilot,
)
from src.ai.production_planner import SceneProductionPlan, is_plan_stale
from src.ai.director import coverage_missing, DirectorDraft
from routers.director import (
    plan_scene_business, apply_director_draft_business, _read_drafts, _check_stale,
    DirectorStaleError, DirectorHasShotsError, DirectorCoverageError, DirectorBusinessError,
)
from routers.production_planner import (
    plan_production_business, approve_plan_business, _read_plans, _write_plans,
)
from src.api.agnes_client import AgnesError


# ============ 状态常量（§二十）============
# FullProjectRun status
FRUN_PENDING = "pending"
FRUN_PREPARING = "preparing"
FRUN_PAUSING = "pausing"
FRUN_PAUSED = "paused"
FRUN_PRODUCING = "producing"
FRUN_FAILED = "failed"
FRUN_BLOCKED = "blocked"
FRUN_COMPLETED = "completed"
FRUN_CANCELLED = "cancelled"
FRUN_ACTIVE = {FRUN_PENDING, FRUN_PREPARING, FRUN_PAUSING, FRUN_PAUSED, FRUN_PRODUCING}
FRUN_TERMINAL = {FRUN_COMPLETED, FRUN_FAILED, FRUN_CANCELLED, FRUN_BLOCKED}
FRUN_RESUMABLE = {FRUN_PAUSED, FRUN_FAILED}   # blocked 不可 resume（§三十五）

# stage
FSTAGE_INSPECT = "inspect"
FSTAGE_PREPARE = "prepare"
FSTAGE_FINALIZE_PREPARATION = "finalize_preparation"
FSTAGE_PRODUCTION = "production"
FSTAGE_DONE = "done"

# FullProjectSceneState status
FSCENE_PENDING = "pending"
FSCENE_PREPARING = "preparing"
FSCENE_READY = "ready"
FSCENE_FAILED = "failed"
FSCENE_BLOCKED = "blocked"

# FullProjectSceneState action（§二十一）
FACT_INSPECT = "inspect"
FACT_DIRECTOR_PLAN = "director_plan"
FACT_DIRECTOR_APPLY = "director_apply"
FACT_PLANNER_PLAN = "planner_plan"
FACT_PLANNER_APPROVE = "planner_approve"
FACT_DONE = "done"

# completed_actions checkpoint keys（§三十三）
CK_DIRECTOR_PLAN = "director_plan"
CK_DIRECTOR_RESULT_SAVED = "director_result_saved"
CK_DIRECTOR_APPLIED = "director_applied"
CK_PLANNER_PLAN = "planner_plan"
CK_PLANNER_RESULT_SAVED = "planner_result_saved"
CK_PLANNER_APPROVED = "planner_approved"

# Scene 分类（§五）
CLS_READY = "READY"
CLS_NEED_DIRECTOR = "NEED_DIRECTOR"
CLS_NEED_PRODUCTION_PLAN = "NEED_PRODUCTION_PLAN"
CLS_BLOCKED = "BLOCKED"

# error_code（§六十七）
ERR_NO_SCENES = "NO_SCENES"
ERR_MISSING_PROJECT_CHARACTER = "MISSING_PROJECT_CHARACTER"
ERR_DIRECTOR_FAILED = "DIRECTOR_FAILED"
ERR_PLANNER_FAILED = "PLANNER_FAILED"
ERR_EXTERNAL_SOURCE_CHANGED = "EXTERNAL_SOURCE_CHANGED"
ERR_PRODUCTION_PLAN_CHANGED_EXTERNALLY = "PRODUCTION_PLAN_CHANGED_EXTERNALLY"
ERR_DIRECTOR_APPLY_CONFLICT = "DIRECTOR_APPLY_CONFLICT"
ERR_CHILD_RUN_CONFLICT = "CHILD_RUN_CONFLICT"
ERR_PROJECT_PRODUCTION_FAILED = "PROJECT_PRODUCTION_FAILED"

# retry budget（§六十八）
DIRECTOR_REQUEST_RETRIES = 1   # 正常 AI 请求失败最多 1 retry
DIRECTOR_REPAIR_BUDGET = 1     # coverage invalid 最多 1 repair（§八）
PLANNER_REQUEST_RETRIES = 1


# ============ 数据结构（§二十/§二十一/§二十二）============
class FullProjectSceneState(BaseModel):
    """单个 Scene 在 FullProjectRun 内的准备状态（run.json 高频更新，§二十一）。
    不放 AI proposal 全量（那些在 PreparationManifest，§二十二）。"""
    scene_id: str
    index: int
    initial_state: str = CLS_READY          # 启动时 classify 结果
    status: str = FSCENE_PENDING
    action: Optional[str] = None
    used_existing_director_draft: bool = False
    created_shot_ids: List[str] = Field(default_factory=list)
    used_existing_production_plan: bool = False
    production_plan_id: Optional[str] = None
    production_plan_source: Optional[str] = None   # existing_approved|existing_draft_auto_approved|generated_by_full_run
    expected_plan_fingerprint: Optional[str] = None   # §二十六
    completed_actions: List[str] = Field(default_factory=list)
    attempts: Dict[str, int] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class FullProjectRun(BaseModel):
    """Full Project Run（§二十）。跨 Workflow 长期自动化任务记录。"""
    run_id: str
    project_name: str
    status: str = FRUN_PENDING
    stage: str = FSTAGE_INSPECT
    respect_locked: bool = True
    pause_requested: bool = False
    cancel_requested: bool = False
    current_scene_id: Optional[str] = None
    current_action: Optional[str] = None
    scenes: List[FullProjectSceneState] = Field(default_factory=list)
    expected_source_signature: Dict[str, Any] = Field(default_factory=dict)
    preparation_manifest_file: Optional[str] = None
    initial_source_snapshot_file: Optional[str] = None
    production_run_id: Optional[str] = None
    final_output: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0


class PreparationSceneRecord(BaseModel):
    """Preparation Manifest 中单个 Scene 的详细记录（§二十二）。放 AI proposal 全量，
    不进 run.json（避免高频更新文件膨胀）。回答「这次一键制作到底自动帮我补了什么」。"""
    scene_id: str
    initial_state: str = CLS_READY
    had_shots: bool = False
    initial_plan_state: Optional[str] = None       # none|draft|approved|stale
    director_source: Optional[str] = None          # existing_draft|generated_by_full_run|none
    director_plan_snapshot: Optional[dict] = None  # write-ahead（§十一）+ reconciliation（§十二）
    created_shot_ids: List[str] = Field(default_factory=list)
    production_plan_source: Optional[str] = None
    production_plan_id: Optional[str] = None
    production_plan_snapshot: Optional[dict] = None
    warnings: List[str] = Field(default_factory=list)


class PreparationManifest(BaseModel):
    full_run_id: str
    scenes: List[PreparationSceneRecord] = Field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0


# ============ Inspect 结果（§二十七）============
class FullProjectSceneInspect(BaseModel):
    scene_id: str
    state: str                                  # READY/NEED_DIRECTOR/NEED_PRODUCTION_PLAN/BLOCKED
    shot_count: int = 0
    has_valid_director_draft: bool = False      # NEED_DIRECTOR 时是否已有 fresh draft（§七）
    plan_state: Optional[str] = None            # none|draft|approved|stale
    detail: str = ""


class FullProjectInspect(BaseModel):
    ready: bool                                 # 无 blocker（warnings 不影响 ready）
    scene_count: int
    shot_count: int
    estimated_new_shots: Optional[int] = None   # 始终 None（§五十二：Director 没跑不能预估）
    scenes: List[FullProjectSceneInspect] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)


# ============ 纯函数 ============
def _plan_state_of(plan: Optional[SceneProductionPlan], scene) -> str:
    """plan 当前状态标签（inspect/manifest 用）。none/draft/approved/stale。"""
    if plan is None:
        return "none"
    if is_plan_stale(plan, scene):
        return "stale"
    return plan.status   # draft|approved


def classify_scene(project, scene, plan: Optional[SceneProductionPlan],
                   drafts: Dict[str, DirectorDraft]) -> tuple[str, str]:
    """Scene 准备状态分类（§五 Case 1-6）。返回 (state, detail)。
    BLOCKED 仅用于：Scene.characters 引用了 Project.characters 中不存在的角色（MISSING_PROJECT_CHARACTER，
    §四）。其他结构性问题由 preflight_scene_autopilot 在 final validation 兜底。"""
    # MISSING_PROJECT_CHARACTER（§四：必须 blocker，不让 Full Run 到图片阶段才失败）
    for cname in scene.characters:
        if project.get_character(cname) is None:
            return CLS_BLOCKED, f"角色 {cname} 不在项目角色中"
    if not scene.shots:
        return CLS_NEED_DIRECTOR, "尚无镜头，AI 将设计分镜"
    if plan is None:
        return CLS_NEED_PRODUCTION_PLAN, "AI 将生成制作计划"
    if is_plan_stale(plan, scene):
        return CLS_NEED_PRODUCTION_PLAN, "制作计划已过期，AI 将重新生成"
    if plan.status != "approved":
        return CLS_NEED_PRODUCTION_PLAN, "将采用当前未确认制作计划"
    return CLS_READY, "已准备"


def _has_valid_director_draft(drafts: Dict[str, DirectorDraft], scene) -> bool:
    """fresh Director Draft 是否可复用（§七）：存在 + 对应当前 scene summary/characters + coverage 满足。"""
    draft = drafts.get(scene.scene_id)
    if draft is None or not draft.shots:
        return False
    if _check_stale(draft, scene) is not None:
        return False
    if coverage_missing(draft):
        return False
    return True


def build_plan_fingerprint(plan: SceneProductionPlan) -> str:
    """Production Plan fingerprint（§二十六）。hash(plan_id, scene_summary_snapshot,
    scene_characters_snapshot, shot_structure_snapshot, shots, status)。检测 FullRun 准备过程中
    用户在 Planner Studio 修改当前 Plan。"""
    payload = json.dumps({
        "plan_id": plan.plan_id,
        "scene_summary_snapshot": plan.scene_summary_snapshot,
        "scene_characters_snapshot": list(plan.scene_characters_snapshot),
        "shot_structure_snapshot": plan.shot_structure_snapshot,
        "shots": [s.model_dump() for s in plan.shots],
        "status": plan.status,
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _director_plan_matches_shots(draft_snapshot: dict, scene) -> bool:
    """§十二 reconciliation：比较 expected_director_plan 与当前 scene.shots。
    至少比较 Shot 数量/顺序/description/characters/target_duration_s。"""
    shots_plan = (draft_snapshot or {}).get("shots") or []
    if len(shots_plan) != len(scene.shots):
        return False
    for plan_shot, actual in zip(shots_plan, scene.shots):
        if (plan_shot.get("intent") or "") != (actual.description or ""):
            return False
        if list(plan_shot.get("characters") or []) != list(actual.characters):
            return False
        p_dur = plan_shot.get("target_duration_s")
        a_dur = actual.target_duration_s
        try:
            if p_dur is None or a_dur is None:
                if p_dur is not a_dur:
                    return False
            elif abs(float(p_dur) - float(a_dur)) > 0.5:
                return False
        except (TypeError, ValueError):
            return False
    return True


def inspect_project(project, project_dir: Path, project_name: str) -> FullProjectInspect:
    """纯代码 Inspect（§二十七/§二十八，不调 AI）。返回各 Scene 分类 + warnings + blockers。
    blockers: NO_SCENES / MISSING_PROJECT_CHARACTER / ACTIVE_*_RUN（active run 冲突，§二十九）。
    warnings: CHARACTER_WITHOUT_VIEWS（text-only 可继续，§四/§五十三）。"""
    scenes_inspect: List[FullProjectSceneInspect] = []
    blockers: List[str] = []
    warnings: List[str] = []
    total_shots = 0

    if not project.scenes:
        blockers.append(ERR_NO_SCENES)
        return FullProjectInspect(ready=False, scene_count=0, shot_count=0,
                                  scenes=[], warnings=warnings, blockers=blockers)

    plans = _read_plans(project_dir)
    drafts = _read_drafts(project_dir)
    scene_mgr = get_autopilot_manager()
    prod_mgr = get_project_autopilot_manager()

    # active run 冲突（§二十九）
    if prod_mgr.find_active_project_run(project_name) is not None:
        blockers.append("ACTIVE_PROJECT_PRODUCTION_RUN")
    if _find_active_full_run(project_name) is not None:
        blockers.append("ACTIVE_FULL_PROJECT_RUN")

    for sc in project.scenes:
        plan = plans.get(sc.scene_id)
        state, detail = classify_scene(project, sc, plan, drafts)
        # active SceneAutopilotRun -> blocker（§二十八）
        if scene_mgr.find_active_run(project_name, sc.scene_id) is not None:
            blockers.append(f"ACTIVE_SCENE_AUTOPILOT_RUN:{sc.scene_id}")
        # MISSING_PROJECT_CHARACTER 已在 classify 内判 BLOCKED，同步进 blockers
        if state == CLS_BLOCKED:
            blockers.append(f"{ERR_MISSING_PROJECT_CHARACTER}:{sc.scene_id}")
        # CHARACTER_WITHOUT_VIEWS warning（§四：text-only 可继续）
        for cname in sc.characters:
            c = project.get_character(cname)
            if c is not None and not c.views:
                warnings.append(f"CHARACTER_WITHOUT_VIEWS:{cname}")
                break
        scenes_inspect.append(FullProjectSceneInspect(
            scene_id=sc.scene_id, state=state, shot_count=len(sc.shots),
            has_valid_director_draft=_has_valid_director_draft(drafts, sc),
            plan_state=_plan_state_of(plan, sc), detail=detail))
        total_shots += len(sc.shots)

    ready = len(blockers) == 0
    return FullProjectInspect(ready=ready, scene_count=len(project.scenes),
                              shot_count=total_shots, estimated_new_shots=None,
                              scenes=scenes_inspect, warnings=warnings, blockers=blockers)


# ============ Manager ============
class _ExistingFullRunError(Exception):
    """项目已有 active FullProjectRun（§二十九）。携带 existing_run_id。"""
    def __init__(self, existing_run_id: str):
        self.existing_run_id = existing_run_id
        super().__init__(f"已有 AI 完整制作任务: {existing_run_id}")


def _find_active_full_run(project_name: str) -> Optional[FullProjectRun]:
    """inspect 用：找 active FullProjectRun（不阻塞 inspect 本身，仅作 blocker 提示）。"""
    mgr = get_full_project_manager()
    for r in mgr.list_runs(project_name):
        if r.status in FRUN_ACTIVE:
            return r
    return None


class FullProjectManager:
    """Full Project 生命周期：Preparation（Director/Planner 补齐）-> Production（委托 ProjectProductionManager）。"""

    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._run_tasks: Dict[str, asyncio.Task] = {}

    # ---------- 路径 / IO ----------
    def _runs_root(self, project_name: str) -> Path:
        from deps import get_project_manager
        d = get_project_manager().get_project_dir(project_name) / "output" / "full_runs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _run_dir(self, project_name: str, run_id: str) -> Path:
        d = self._runs_root(project_name) / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _run_path(self, project_name: str, run_id: str) -> Path:
        return self._run_dir(project_name, run_id) / "run.json"

    def _manifest_path(self, project_name: str, run_id: str) -> Path:
        return self._run_dir(project_name, run_id) / "preparation_manifest.json"

    def _lock_for(self, run_id: str) -> asyncio.Lock:
        if run_id not in self._locks:
            self._locks[run_id] = asyncio.Lock()
        return self._locks[run_id]

    def _load_run(self, project_name: str, run_id: str) -> Optional[FullProjectRun]:
        p = self._run_path(project_name, run_id)
        if not p.exists():
            return None
        try:
            return FullProjectRun.model_validate(json.loads(p.read_text(encoding="utf-8")))
        except Exception as e:
            from deps import logger
            logger.warning(f"加载 FullProjectRun {run_id} 失败: {e}")
            return None

    async def _save_run(self, run: FullProjectRun):
        lock = self._lock_for(run.run_id)
        async with lock:
            run.updated_at = time.time()
            _atomic_write_json(self._run_path(run.project_name, run.run_id),
                               json.loads(run.model_dump_json()))

    def list_runs(self, project_name: str) -> List[FullProjectRun]:
        out = []
        root = self._runs_root(project_name)
        for d in sorted(root.iterdir()):
            p = d / "run.json"
            if not p.exists():
                continue
            try:
                out.append(FullProjectRun.model_validate(json.loads(p.read_text(encoding="utf-8"))))
            except Exception:
                pass
        out.sort(key=lambda r: r.created_at, reverse=True)
        return out

    def get_run(self, project_name: str, run_id: str) -> Optional[FullProjectRun]:
        return self._load_run(project_name, run_id)

    def find_active_full_run(self, project_name: str) -> Optional[FullProjectRun]:
        for r in self.list_runs(project_name):
            if r.status in FRUN_ACTIVE:
                return r
        return None

    # ---------- manifest IO ----------
    def _load_manifest(self, project_name: str, run_id: str) -> PreparationManifest:
        p = self._manifest_path(project_name, run_id)
        if not p.exists():
            return PreparationManifest(full_run_id=run_id, created_at=time.time(), updated_at=time.time())
        try:
            return PreparationManifest.model_validate(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return PreparationManifest(full_run_id=run_id, created_at=time.time(), updated_at=time.time())

    def _save_manifest(self, project_name: str, run_id: str, manifest: PreparationManifest):
        manifest.updated_at = time.time()
        _atomic_write_json(self._manifest_path(project_name, run_id),
                           json.loads(manifest.model_dump_json()))

    def _manifest_record(self, manifest: PreparationManifest, scene_id: str) -> PreparationSceneRecord:
        for r in manifest.scenes:
            if r.scene_id == scene_id:
                return r
        rec = PreparationSceneRecord(scene_id=scene_id)
        manifest.scenes.append(rec)
        return rec

    # ---------- project/scene helpers ----------
    def _load_project(self, project_name: str):
        from deps import get_project_manager
        return ProjectStore(get_project_manager().get_project_dir(project_name)).load()

    def _find_scene(self, project, scene_id: str):
        return next((s for s in project.scenes if s.scene_id == scene_id), None)

    def _current_signature(self, project_name: str) -> Dict[str, Any]:
        return build_project_source_signature(self._load_project(project_name))

    def _scene_state(self, run: FullProjectRun, scene_id: str) -> Optional[FullProjectSceneState]:
        return next((s for s in run.scenes if s.scene_id == scene_id), None)

    def _add_action(self, entry: FullProjectSceneState, action: str):
        if action not in entry.completed_actions:
            entry.completed_actions.append(action)

    def _pause_or_cancel(self, project_name: str, run_id: str) -> Optional[str]:
        """reload run 检查并发 pause/cancel 请求（endpoint 可能已设置）。返回 'pause'/'cancel'/None。"""
        run = self._load_run(project_name, run_id)
        if run is None:
            return "cancel"
        if run.cancel_requested:
            return "cancel"
        if run.pause_requested:
            return "pause"
        return None

    # ---------- 启动 / 恢复 / 控制 ----------
    async def inspect(self, project_name: str) -> FullProjectInspect:
        """GET inspect（§二十七）：纯代码扫描，不调 AI，不创建 Run。"""
        from deps import get_project_manager
        project_dir = get_project_manager().get_project_dir(project_name)
        project = ProjectStore(project_dir).load()
        return inspect_project(project, project_dir, project_name)

    async def start_run(self, project_name: str, respect_locked: bool = True) -> FullProjectRun:
        """§三十 start 流程：load -> inspect -> 确认无 blocker -> 建 run dir -> 保存 initial_source_snapshot
        + initial expected_source_signature -> 初始化 scene states -> atomic save -> 调度 Preparation loop。
        不信任前端 inspect（§三十：后端重新 load + inspect）。"""
        from deps import get_project_manager, logger
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()

        # 一个项目不允许同时存在 active FullProjectRun / ProjectProductionRun / SceneAutopilotRun（§二十九）
        active = self.find_active_full_run(project_name)
        if active:
            raise _ExistingFullRunError(active.run_id)
        if get_project_autopilot_manager().find_active_project_run(project_name) is not None:
            raise ValueError("项目已有进行中的完整影片制作任务（AI-5A），请先暂停或停止")

        insp = inspect_project(project, project_dir, project_name)
        if insp.blockers:
            raise ValueError("存在阻塞项，无法开始：" + "; ".join(insp.blockers))

        signature = build_project_source_signature(project)
        run_id = f"frun_{uuid.uuid4().hex[:10]}"
        run = FullProjectRun(
            run_id=run_id, project_name=project_name, status=FRUN_PENDING,
            stage=FSTAGE_INSPECT, respect_locked=respect_locked,
            expected_source_signature=signature,
            created_at=time.time(), updated_at=time.time(),
        )
        # 初始化 scene states + manifest records
        plans = _read_plans(project_dir)
        drafts = _read_drafts(project_dir)
        manifest = PreparationManifest(full_run_id=run_id, created_at=time.time(), updated_at=time.time())
        for i, sc in enumerate(project.scenes):
            plan = plans.get(sc.scene_id)
            state, _ = classify_scene(project, sc, plan, drafts)
            run.scenes.append(FullProjectSceneState(
                scene_id=sc.scene_id, index=i, initial_state=state, status=FSCENE_PENDING))
            rec = self._manifest_record(manifest, sc.scene_id)
            rec.initial_state = state
            rec.had_shots = bool(sc.shots)
            rec.initial_plan_state = _plan_state_of(plan, sc)
            # CHARACTER_WITHOUT_VIEWS warning 落 manifest（§五十三）
            for cname in sc.characters:
                c = project.get_character(cname)
                if c is not None and not c.views:
                    rec.warnings.append(f"CHARACTER_WITHOUT_VIEWS:{cname}")
                    run.scenes[-1].warnings.append(f"角色 {cname} 尚无三视图，本次仍会使用文字设定生成")
                    break

        run_dir = self._run_dir(project_name, run_id)
        snap_path = run_dir / "initial_source_snapshot.json"
        _atomic_write_json(snap_path, signature)
        run.initial_source_snapshot_file = str(snap_path)
        self._save_manifest(project_name, run_id, manifest)
        run.preparation_manifest_file = str(self._manifest_path(project_name, run_id))
        await self._save_run(run)
        logger.info(f"FullProjectRun 启动: {project_name} run={run_id} "
                    f"({len(run.scenes)} 故事段, {insp.shot_count} 镜头)")
        self._launch_loop(run)
        return run

    def _launch_loop(self, run: FullProjectRun):
        task = asyncio.create_task(self._run_loop(run.project_name, run.run_id))
        self._run_tasks[run.run_id] = task
        task.add_done_callback(lambda t: self._run_tasks.pop(run.run_id, None))

    async def resume_run(self, project_name: str, run_id: str) -> FullProjectRun:
        """Resume 同 FullProjectRun（§三十四：不重新处理已 READY 的 Scene / 已 apply 的 Director /
        已 approve 的 Plan，从失败 Scene 继续）。blocked 不可 resume（§三十五）。
        production 阶段 resume -> resume existing ProjectProductionRun（§四十五，不创建新 child）。"""
        run = self._load_run(project_name, run_id)
        if run is None:
            raise ValueError("Run 不存在")
        if run.status not in FRUN_RESUMABLE:
            raise ValueError(f"Run 状态 {run.status} 不可 resume")
        run.status = FRUN_PREPARING if run.stage in (FSTAGE_INSPECT, FSTAGE_PREPARE,
                                                     FSTAGE_FINALIZE_PREPARATION) else FRUN_PRODUCING
        run.error = None
        run.error_code = None
        await self._save_run(run)
        self._launch_loop(run)
        return run

    async def request_pause(self, project_name: str, run_id: str) -> FullProjectRun:
        """§三十六/§四十五：prepare 阶段 -> 设 pause_requested（loop 在下一 checkpoint 暂停，AI 已发出的
        请求允许返回后保存 proposal 再暂停）；production 阶段 -> 传给 ProjectProductionRun child。"""
        run = self._load_run(project_name, run_id)
        if run is None:
            raise ValueError("Run 不存在")
        if run.status in FRUN_TERMINAL:
            raise ValueError(f"Run 已 {run.status}")
        run.pause_requested = True
        if run.status != FRUN_PAUSING:
            run.status = FRUN_PAUSING
        await self._save_run(run)
        # production 阶段：传给 child
        if run.stage == FSTAGE_PRODUCTION and run.production_run_id:
            try:
                await get_project_autopilot_manager().request_pause(project_name, run.production_run_id)
            except Exception:
                pass
        return run

    async def request_cancel(self, project_name: str, run_id: str) -> FullProjectRun:
        """§三十七/§四十六：prepare -> checkpoint 后停止（不回滚，已创建 Shot/Plan 保留）；
        production -> request_cancel(child)，已有 SceneRun/图片/视频/final partial 全部按 AI-5A 规则保留。"""
        run = self._load_run(project_name, run_id)
        if run is None:
            raise ValueError("Run 不存在")
        if run.status in FRUN_TERMINAL:
            raise ValueError(f"Run 已 {run.status}")
        run.cancel_requested = True
        await self._save_run(run)
        if run.stage == FSTAGE_PRODUCTION and run.production_run_id:
            try:
                await get_project_autopilot_manager().request_cancel(project_name, run.production_run_id)
            except Exception:
                pass
        return run

    async def recover_runs(self):
        """startup 恢复（§四十七/§四十八）：扫描所有项目的 active FullProjectRun。
        🔴 必须在 ProjectProductionManager.recover_runs() 之后执行（FullProject 最外层，恢复时需看到
        production child 正确状态，§四十七）。pause_requested->PAUSED；cancel_requested->CANCELLED；
        否则重新调度。"""
        from deps import get_project_manager, logger
        pm = get_project_manager()
        if not pm.base_dir.exists():
            return
        n = 0
        for proj_dir in pm.base_dir.iterdir():
            root = proj_dir / "output" / "full_runs"
            if not root.exists():
                continue
            for d in root.iterdir():
                p = d / "run.json"
                if not p.exists():
                    continue
                try:
                    run = FullProjectRun.model_validate(json.loads(p.read_text(encoding="utf-8")))
                except Exception:
                    continue
                if run.status not in (FRUN_PREPARING, FRUN_PAUSING, FRUN_PRODUCING, FRUN_PENDING):
                    continue
                if run.pause_requested:
                    run.pause_requested = False
                    run.status = FRUN_PAUSED
                    await self._save_run(run)
                    logger.info(f"恢复 FullProjectRun {run.run_id} -> paused")
                elif run.cancel_requested:
                    run.cancel_requested = False
                    run.status = FRUN_CANCELLED
                    await self._save_run(run)
                    logger.info(f"恢复 FullProjectRun {run.run_id} -> cancelled")
                else:
                    run.status = FRUN_PREPARING if run.stage in (FSTAGE_INSPECT, FSTAGE_PREPARE,
                                                                 FSTAGE_FINALIZE_PREPARATION) else FRUN_PRODUCING
                    await self._save_run(run)
                    self._launch_loop(run)
                    n += 1
                    logger.info(f"恢复 FullProjectRun {run.run_id} ({run.project_name})")
        if n:
            logger.info(f"FullProjectRun 恢复 {n} 个")

    # ---------- 执行循环 ----------
    async def _run_loop(self, project_name: str, run_id: str):
        """stage 分发（§三十二 prepare 串行 / §四十 finalize -> handoff / §四十四 wait production child）。"""
        from deps import logger
        try:
            while True:
                run = self._load_run(project_name, run_id)
                if run is None:
                    return
                # cancel/pause checkpoint
                if run.cancel_requested:
                    run.cancel_requested = False
                    run.status = FRUN_CANCELLED
                    await self._save_run(run); return
                if run.pause_requested:
                    run.pause_requested = False
                    run.status = FRUN_PAUSED
                    await self._save_run(run); return

                if run.stage in (FSTAGE_PRODUCTION, FSTAGE_DONE):
                    await self._handle_production(project_name, run_id); return

                # prepare / finalize_preparation
                run.status = FRUN_PREPARING
                if run.stage == FSTAGE_INSPECT:
                    run.stage = FSTAGE_PREPARE
                await self._save_run(run)

                entry = self._next_scene_to_prepare(run)
                if entry is None:
                    # 所有 Scene ready -> finalize -> handoff（§三十八/§四十）
                    run.stage = FSTAGE_FINALIZE_PREPARATION
                    run.current_scene_id = None
                    run.current_action = None
                    await self._save_run(run)
                    await self._handoff_to_production(project_name, run_id)
                    return

                run.current_scene_id = entry.scene_id
                await self._save_run(run)
                ok = await self._prepare_scene(project_name, run_id, entry.scene_id)
                if not ok:
                    return   # paused/failed/blocked/cancelled
                # loop 继续，reload 找下一个 Scene
        except Exception as e:
            logger.exception(f"FullProjectRun _run_loop {run_id} 异常")
            run = self._load_run(project_name, run_id)
            if run:
                run.status = FRUN_FAILED
                run.error = f"执行循环异常: {str(e)[:400]}"
                await self._save_run(run)

    def _next_scene_to_prepare(self, run: FullProjectRun) -> Optional[FullProjectSceneState]:
        """第一个未 ready 且未 blocked 的 Scene（按 canonical 顺序，§二十三/§三十一）。"""
        for entry in run.scenes:
            if entry.status in (FSCENE_PENDING, FSCENE_PREPARING, FSCENE_FAILED):
                return entry
        return None

    # ---------- 单 Scene 准备（§十四：Director->apply->Planner->approve->READY）----------
    async def _prepare_scene(self, project_name: str, run_id: str,
                             scene_id: str) -> bool:
        """完整 prepare 一个 Scene。每 side effect 前后持久 checkpoint（§三十三）。
        返回 False 表示 paused/failed/blocked/cancelled（loop 应退出）。"""
        run = self._load_run(project_name, run_id)
        entry = self._scene_state(run, scene_id)
        if entry is None:
            return False
        if entry.status == FSCENE_READY:
            return True
        entry.status = FSCENE_PREPARING
        await self._save_run(run)
        completed = set(entry.completed_actions)

        try:
            # === Director 阶段（§六：已有 Shot 绝不调 Director）===
            project = self._load_project(project_name)
            scene = self._find_scene(project, scene_id)
            if scene is None:
                await self._fail_scene(run_id, entry, ERR_EXTERNAL_SOURCE_CHANGED,
                                       f"故事段 {scene_id} 已删除"); return False

            if not scene.shots and CK_DIRECTOR_APPLIED not in completed:
                # 需要 Director
                if CK_DIRECTOR_RESULT_SAVED not in completed:
                    entry.action = FACT_DIRECTOR_PLAN
                    run.current_action = FACT_DIRECTOR_PLAN
                    await self._save_run(run)
                    draft = await self._produce_director_draft(project_name, run_id, entry, project, scene)
                    if draft is None:
                        return False   # paused/failed
                # crash reconciliation（§十二）：director_result_saved 但未 applied
                if CK_DIRECTOR_APPLIED not in completed:
                    entry.action = FACT_DIRECTOR_APPLY
                    run.current_action = FACT_DIRECTOR_APPLY
                    await self._save_run(run)
                    if not await self._apply_director(project_name, run_id, entry, scene_id):
                        return False   # blocked/failed
                # reload project（Director apply 改了 canonical）
                project = self._load_project(project_name)
                scene = self._find_scene(project, scene_id)
            elif scene.shots and CK_DIRECTOR_APPLIED not in completed and CK_DIRECTOR_RESULT_SAVED in completed:
                # 已有 Shot 但本 Run 未标 director_applied（可能 crash 在 apply 后）-> reconcile
                if not await self._reconcile_director(project_name, run_id, entry, scene_id):
                    return False

            # === Planner 阶段（§十五）===
            if CK_PLANNER_APPROVED not in completed:
                if not await self._ensure_production_plan(project_name, run_id, entry, scene):
                    return False   # blocked/failed

            # 全部完成 -> READY（🔴 必须保存同一 run 对象，不能 _save_run(_load_run(...)) 否则丢失修改）
            run_ready = self._load_run(project_name, run_id)
            entry = self._scene_state(run_ready, scene_id)
            entry.status = FSCENE_READY
            entry.action = FACT_DONE
            entry.error = None
            await self._save_run(run_ready)
            return True
        except DirectorBusinessError as e:
            await self._fail_scene_named(project_name, run_id, entry, ERR_DIRECTOR_FAILED, str(e)); return False
        except Exception as e:
            from deps import logger
            logger.exception(f"prepare scene {scene_id} 异常")
            await self._fail_scene_named(project_name, run_id, entry, ERR_DIRECTOR_FAILED, str(e)[:400]); return False

    # ---------- Director ----------
    async def _produce_director_draft(self, project_name: str, run_id: str,
                                      entry: FullProjectSceneState, project, scene) -> Optional[DirectorDraft]:
        """产生合法 Director draft（§七/§八）：优先复用 fresh draft -> 否则 plan_scene_business +
        coverage repair（最多 1 次）。成功后 write-ahead 保存到 manifest（director_result_saved，§十一）。
        返回 None 表示 paused/failed。"""
        from deps import get_agnes_client, logger
        manifest = self._load_manifest(project_name, run_id)
        rec = self._manifest_record(manifest, entry.scene_id)

        # §七：优先复用 fresh valid draft
        drafts = _read_drafts(get_project_dir_safe(project_name))
        draft = drafts.get(entry.scene_id)
        if draft is not None and _check_stale(draft, scene) is None and not coverage_missing(draft) and draft.shots:
            entry.used_existing_director_draft = True
            rec.director_source = "existing_draft"
            logger.info(f"FullRun {run_id} 故事段 {entry.scene_id} 复用现有 Director draft")
        else:
            # 生成（plan_scene_business 内部已含 JSON parse retry）
            client = get_agnes_client()
            draft = await self._call_director_with_retry(project_name, run_id, entry, project, scene, "", client)
            if draft is None:
                return None   # paused/failed
            # coverage 校验（§八）：invalid 最多 1 repair
            missing = coverage_missing(draft)
            if missing:
                repair_note = (f"角色 {('、'.join(missing))} 尚未安排到任何镜头，"
                               "请重新规划并确保所有 Scene characters 至少出现在一个 Shot 中。")
                logger.info(f"FullRun {run_id} 故事段 {entry.scene_id} coverage 缺失 {missing}，请求 repair")
                draft = await self._call_director_with_retry(project_name, run_id, entry, project, scene,
                                                             repair_note, client, is_repair=True)
                if draft is None:
                    return None
                if coverage_missing(draft):
                    await self._fail_scene_named(project_name, run_id, entry, ERR_DIRECTOR_FAILED,
                                                 f"故事段 {entry.scene_id} 角色覆盖校验仍不通过")
                    return None
            rec.director_source = "generated_by_full_run"
            # 落盘 draft（让 DirectorStudio / 后续 apply 能读到；与手工 plan 行为一致）
            drafts = _read_drafts(get_project_dir_safe(project_name))
            draft.created_at = time.time()
            drafts[entry.scene_id] = draft
            _write_drafts_safe(project_name, drafts)

        # write-ahead（§十一）：保存 proposal snapshot 到 manifest + 标 director_result_saved
        rec.director_plan_snapshot = draft.model_dump()
        self._save_manifest(project_name, run_id, manifest)
        run_wh = self._load_run(project_name, run_id)
        entry = self._scene_state(run_wh, entry.scene_id)
        self._add_action(entry, CK_DIRECTOR_PLAN)
        self._add_action(entry, CK_DIRECTOR_RESULT_SAVED)
        await self._save_run(run_wh)

        # pause checkpoint（§三十六：AI 已返回 + proposal 已存 -> 可暂停，不继续 apply）
        pc = self._pause_or_cancel(project_name, run_id)
        if pc == "cancel":
            await self._do_cancel(project_name, run_id); return None
        if pc == "pause":
            await self._do_pause(project_name, run_id); return None
        return draft

    async def _call_director_with_retry(self, project_name: str, run_id: str,
                                        entry: FullProjectSceneState, project, scene,
                                        director_note: str, client, *,
                                        is_repair: bool = False) -> Optional[DirectorDraft]:
        """调 plan_scene_business，正常失败最多 1 retry（§六十八）。repair 不另加 retry budget。"""
        from deps import logger
        key = "director_repair" if is_repair else "director_plan"
        entry.attempts[key] = entry.attempts.get(key, 0)
        max_tries = 1 + (0 if is_repair else DIRECTOR_REQUEST_RETRIES)
        last_err = None
        for _ in range(max_tries):
            entry.attempts[key] += 1
            try:
                draft = await plan_scene_business(project, scene, director_note, client)
                return draft
            except AgnesError as e:
                last_err = f"AI 服务错误: {e}"
            except Exception as e:
                last_err = str(e)[:300]
            # pause/cancel 中途检查
            pc = self._pause_or_cancel(project_name, run_id)
            if pc:
                if pc == "cancel":
                    await self._do_cancel(project_name, run_id)
                else:
                    await self._do_pause(project_name, run_id)
                return None
        await self._fail_scene_named(project_name, run_id, entry, ERR_DIRECTOR_FAILED,
                                     f"故事段 {entry.scene_id} AI 分镜失败：{last_err}")
        return None

    async def _apply_director(self, project_name: str, run_id: str,
                              entry: FullProjectSceneState, scene_id: str) -> bool:
        """apply Director draft -> 创建 Shot[]（§十/§十一/§二十五）。
        顺序：validate expected_source -> write-ahead（已存 manifest）-> apply_director_draft_business ->
        reload + rebuild signature + 更新 expected_source_signature（合法 canonical 修改）-> director_applied。
        返回 False 表示 blocked/failed。"""
        from deps import get_project_manager, logger
        # validate expected_source（§二十四）
        changed = self._source_changed(project_name, run_id)
        if changed:
            await self._block_run(project_name, run_id, ERR_EXTERNAL_SOURCE_CHANGED, changed); return False

        manifest = self._load_manifest(project_name, run_id)
        rec = self._manifest_record(manifest, entry.scene_id)
        snap = rec.director_plan_snapshot
        if not snap:
            await self._fail_scene_named(project_name, run_id, entry, ERR_DIRECTOR_FAILED,
                                         f"故事段 {scene_id} 无 director_plan_snapshot"); return False
        try:
            draft = DirectorDraft.model_validate(snap)
        except Exception as e:
            await self._fail_scene_named(project_name, run_id, entry, ERR_DIRECTOR_FAILED,
                                         f"director_plan_snapshot 解析失败: {e}"); return False

        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        scene = self._find_scene(project, scene_id)
        if scene is None:
            await self._block_run(project_name, run_id, ERR_EXTERNAL_SOURCE_CHANGED,
                                  f"故事段 {scene_id} 已删除"); return False

        try:
            new_shots = apply_director_draft_business(store, project, scene_id, draft)
        except DirectorHasShotsError:
            # scene 已有 Shot（可能 crash 后重启 / 外部创建）-> reconcile（§十二）
            return await self._reconcile_director(project_name, run_id, entry, scene_id)
        except DirectorStaleError as e:
            await self._block_run(project_name, run_id, ERR_EXTERNAL_SOURCE_CHANGED,
                                  f"故事段 {scene_id} 草稿 stale: {e}"); return False
        except DirectorCoverageError as e:
            await self._fail_scene_named(project_name, run_id, entry, ERR_DIRECTOR_FAILED,
                                         f"故事段 {scene_id} 角色覆盖: {e}"); return False
        except ValueError as e:
            await self._fail_scene_named(project_name, run_id, entry, ERR_DIRECTOR_FAILED,
                                         f"故事段 {scene_id} apply: {e}"); return False

        # apply 成功：更新 expected_source_signature（§二十五：Director apply 是合法 canonical 修改）
        rec.created_shot_ids = [sh.shot_id for sh in new_shots]
        self._save_manifest(project_name, run_id, manifest)
        run = self._load_run(project_name, run_id)
        entry = self._scene_state(run, entry.scene_id)
        entry.created_shot_ids = list(rec.created_shot_ids)
        self._add_action(entry, CK_DIRECTOR_APPLIED)
        run.expected_source_signature = self._current_signature(project_name)
        await self._save_run(run)
        logger.info(f"FullRun {run_id} 故事段 {scene_id} Director 已应用，创建 {len(new_shots)} 镜头")
        return True

    async def _reconcile_director(self, project_name: str, run_id: str,
                                  entry: FullProjectSceneState, scene_id: str) -> bool:
        """§十二：director_result_saved 但未 director_applied，且 scene.shots 已存在。
        比对当前 scene.shots 与 expected_director_plan：匹配->director_applied；不匹配->blocked。"""
        from deps import logger
        manifest = self._load_manifest(project_name, run_id)
        rec = self._manifest_record(manifest, entry.scene_id)
        snap = rec.director_plan_snapshot
        project = self._load_project(project_name)
        scene = self._find_scene(project, scene_id)
        if scene is None or not snap:
            await self._block_run(project_name, run_id, ERR_EXTERNAL_SOURCE_CHANGED,
                                  f"故事段 {scene_id} 已删除或无 director snapshot"); return False
        if _director_plan_matches_shots(snap, scene):
            # 上一轮 apply 已成功
            run = self._load_run(project_name, run_id)
            entry = self._scene_state(run, scene_id)
            entry.created_shot_ids = [sh.shot_id for sh in scene.shots]
            self._add_action(entry, CK_DIRECTOR_APPLIED)
            run.expected_source_signature = self._current_signature(project_name)
            await self._save_run(run)
            logger.info(f"FullRun {run_id} 故事段 {scene_id} reconcile：Shot 已存在且匹配，标 director_applied")
            return True
        await self._block_run(project_name, run_id, ERR_DIRECTOR_APPLY_CONFLICT,
                              f"故事段 {scene_id} 已有镜头但与 AI 分镜方案不匹配（外部结构变化）")
        return False

    # ---------- Production Planner ----------
    async def _ensure_production_plan(self, project_name: str, run_id: str,
                                      entry: FullProjectSceneState, scene) -> bool:
        """§十五：approved+fresh->保留；draft+fresh->auto approve；stale/无->生成。
        §二十六 fingerprint 防并发修改。返回 False 表示 blocked/failed。"""
        from deps import get_project_manager, logger
        project_dir = get_project_manager().get_project_dir(project_name)
        plans = _read_plans(project_dir)
        plan = plans.get(entry.scene_id)
        stale = is_plan_stale(plan, scene) if plan else None

        if plan is not None and plan.status == "approved" and not stale:
            # existing approved fresh -> 保留（§五十八）
            run = self._load_run(project_name, run_id)
            entry = self._scene_state(run, entry.scene_id)
            entry.used_existing_production_plan = True
            entry.production_plan_id = plan.plan_id
            entry.production_plan_source = "existing_approved"
            entry.expected_plan_fingerprint = build_plan_fingerprint(plan)
            self._add_action(entry, CK_PLANNER_APPROVED)
            await self._save_run(run)
            self._record_plan_in_manifest(project_name, run_id, entry.scene_id, plan, "existing_approved")
            logger.info(f"FullRun {run_id} 故事段 {entry.scene_id} 复用现有 approved Plan")
            return True

        if plan is not None and plan.status == "draft" and not stale:
            # existing draft fresh -> auto approve（§十五：不丢用户手工编辑）
            return await self._approve_existing_draft(project_name, run_id, entry, scene, plan)

        # 无 plan / stale -> 生成
        return await self._generate_and_approve_plan(project_name, run_id, entry, scene)

    async def _approve_existing_draft(self, project_name: str, run_id: str,
                                      entry: FullProjectSceneState, scene, plan) -> bool:
        """§十五：fresh draft auto-approve。§二十六 fingerprint 校验防并发修改。"""
        from deps import get_project_manager, logger
        # fingerprint 校验：当前 plan 与本 Run 预期一致（首次 expected=None，记录之）
        cur_fp = build_plan_fingerprint(plan)
        if entry.expected_plan_fingerprint and cur_fp != entry.expected_plan_fingerprint:
            await self._block_run(project_name, run_id, ERR_PRODUCTION_PLAN_CHANGED_EXTERNALLY,
                                  f"故事段 {entry.scene_id} 制作计划被外部修改"); return False
        try:
            approve_plan_business(plan, scene)
        except ValueError as e:
            await self._fail_scene_named(project_name, run_id, entry, ERR_PLANNER_FAILED,
                                         f"故事段 {entry.scene_id} approve: {e}"); return False
        project_dir = get_project_manager().get_project_dir(project_name)
        plans = _read_plans(project_dir)
        plans[entry.scene_id] = plan
        _write_plans(project_dir, plans)
        run = self._load_run(project_name, run_id)
        entry = self._scene_state(run, entry.scene_id)
        entry.production_plan_id = plan.plan_id
        entry.production_plan_source = "existing_draft_auto_approved"
        entry.expected_plan_fingerprint = build_plan_fingerprint(plan)
        self._add_action(entry, CK_PLANNER_RESULT_SAVED)
        self._add_action(entry, CK_PLANNER_APPROVED)
        await self._save_run(run)
        self._record_plan_in_manifest(project_name, run_id, entry.scene_id, plan,
                                      "existing_draft_auto_approved")
        logger.info(f"FullRun {run_id} 故事段 {entry.scene_id} auto-approve 现有 draft Plan")
        # pause checkpoint
        pc = self._pause_or_cancel(project_name, run_id)
        if pc == "cancel":
            await self._do_cancel(project_name, run_id); return False
        if pc == "pause":
            await self._do_pause(project_name, run_id); return False
        return True

    async def _generate_and_approve_plan(self, project_name: str, run_id: str,
                                         entry: FullProjectSceneState, scene) -> bool:
        """§十六/§十七：生成新 Plan + sanitize + fingerprint + approve。含 planner_result_saved checkpoint。
        失败最多 1 retry（§六十八）。"""
        from deps import get_agnes_client, get_project_manager, logger
        completed = set(entry.completed_actions)

        if CK_PLANNER_RESULT_SAVED not in completed:
            run = self._load_run(project_name, run_id)
            entry = self._scene_state(run, entry.scene_id)
            entry.action = FACT_PLANNER_PLAN
            run.current_action = FACT_PLANNER_PLAN
            await self._save_run(run)
            client = get_agnes_client()
            plan = await self._call_planner_with_retry(project_name, run_id, entry, scene, client)
            if plan is None:
                return False   # paused/failed
            # 持久化 plan + 记录 fingerprint（planner_result_saved）
            project_dir = get_project_manager().get_project_dir(project_name)
            plans = _read_plans(project_dir)
            plans[entry.scene_id] = plan
            _write_plans(project_dir, plans)
            run = self._load_run(project_name, run_id)
            entry = self._scene_state(run, entry.scene_id)
            entry.expected_plan_fingerprint = build_plan_fingerprint(plan)
            entry.production_plan_id = plan.plan_id
            self._add_action(entry, CK_PLANNER_PLAN)
            self._add_action(entry, CK_PLANNER_RESULT_SAVED)
            await self._save_run(run)
            self._record_plan_in_manifest(project_name, run_id, entry.scene_id, plan, "generated_by_full_run")
            # pause checkpoint（§三十六）
            pc = self._pause_or_cancel(project_name, run_id)
            if pc == "cancel":
                await self._do_cancel(project_name, run_id); return False
            if pc == "pause":
                await self._do_pause(project_name, run_id); return False

        # approve
        if CK_PLANNER_APPROVED not in completed:
            run = self._load_run(project_name, run_id)
            entry = self._scene_state(run, entry.scene_id)
            entry.action = FACT_PLANNER_APPROVE
            run.current_action = FACT_PLANNER_APPROVE
            await self._save_run(run)
            project_dir = get_project_manager().get_project_dir(project_name)
            plans = _read_plans(project_dir)
            plan = plans.get(entry.scene_id)
            if plan is None:
                await self._fail_scene_named(project_name, run_id, entry, ERR_PLANNER_FAILED,
                                             f"故事段 {entry.scene_id} plan 丢失"); return False
            # fingerprint 校验（§二十六）
            cur_fp = build_plan_fingerprint(plan)
            if entry.expected_plan_fingerprint and cur_fp != entry.expected_plan_fingerprint:
                await self._block_run(project_name, run_id, ERR_PRODUCTION_PLAN_CHANGED_EXTERNALLY,
                                      f"故事段 {entry.scene_id} 制作计划被外部修改"); return False
            try:
                approve_plan_business(plan, scene)
            except ValueError as e:
                await self._fail_scene_named(project_name, run_id, entry, ERR_PLANNER_FAILED,
                                             f"故事段 {entry.scene_id} approve: {e}"); return False
            plans[entry.scene_id] = plan
            _write_plans(project_dir, plans)
            run = self._load_run(project_name, run_id)
            entry = self._scene_state(run, entry.scene_id)
            entry.production_plan_source = "generated_by_full_run"
            entry.expected_plan_fingerprint = build_plan_fingerprint(plan)
            self._add_action(entry, CK_PLANNER_APPROVED)
            await self._save_run(run)
            logger.info(f"FullRun {run_id} 故事段 {entry.scene_id} Planner 已生成并 approve")
        return True

    async def _call_planner_with_retry(self, project_name: str, run_id: str,
                                       entry: FullProjectSceneState, scene, client) -> Optional[SceneProductionPlan]:
        """调 plan_production_business，失败最多 1 retry（§六十八）。"""
        from deps import get_project_manager, logger
        entry.attempts["planner_plan"] = entry.attempts.get("planner_plan", 0)
        last_err = None
        for _ in range(1 + PLANNER_REQUEST_RETRIES):
            entry.attempts["planner_plan"] += 1
            try:
                plan = await plan_production_business(self._load_project(project_name), scene,
                                                      project_name, client)
                return plan
            except AgnesError as e:
                last_err = f"AI 服务错误: {e}"
            except Exception as e:
                last_err = str(e)[:300]
            pc = self._pause_or_cancel(project_name, run_id)
            if pc:
                if pc == "cancel":
                    await self._do_cancel(project_name, run_id)
                else:
                    await self._do_pause(project_name, run_id)
                return None
        await self._fail_scene_named(project_name, run_id, entry, ERR_PLANNER_FAILED,
                                     f"故事段 {entry.scene_id} AI 制作规划失败：{last_err}")
        return None

    def _record_plan_in_manifest(self, project_name: str, run_id: str, scene_id: str,
                                 plan: SceneProductionPlan, source: str):
        manifest = self._load_manifest(project_name, run_id)
        rec = self._manifest_record(manifest, scene_id)
        rec.production_plan_source = source
        rec.production_plan_id = plan.plan_id
        rec.production_plan_snapshot = plan.model_dump()
        self._save_manifest(project_name, run_id, manifest)

    # ---------- source / 状态变更 helpers ----------
    def _source_changed(self, project_name: str, run_id: str) -> Optional[str]:
        """比对当前 source signature 与 expected_source_signature（§二十四）。返回 stale 原因（None=未变）。"""
        run = self._load_run(project_name, run_id)
        current = self._current_signature(project_name)
        if current != run.expected_source_signature:
            return ("项目的故事、角色或镜头规划在 AI 完整制作期间发生了变化。"
                    "当前任务已安全停止。请确认最新内容后重新开始一次 AI 完整制作。")
        return None

    async def _fail_scene_named(self, project_name: str, run_id: str,
                                entry: FullProjectSceneState, error_code: str, error: str):
        """标 Scene failed + Run failed（preparation 失败，§六十七 带 scene+action）。"""
        from deps import logger
        run = self._load_run(project_name, run_id)
        entry = self._scene_state(run, entry.scene_id)
        entry.status = FSCENE_FAILED
        entry.error = error
        run.status = FRUN_FAILED
        run.stage = FSTAGE_PREPARE
        run.error_code = error_code
        run.error = error
        run.current_scene_id = entry.scene_id
        run.current_action = entry.action
        await self._save_run(run)
        logger.error(f"FullRun {run_id} 故事段 {entry.scene_id} 失败: {error_code} {error}")

    async def _block_run(self, project_name: str, run_id: str, error_code: str, error: str):
        """标 Run blocked（§三十五：canonical source/plan 被外部改变，不可普通 resume）。"""
        from deps import logger
        run = self._load_run(project_name, run_id)
        entry = self._scene_state(run, run.current_scene_id) if run.current_scene_id else None
        if entry is not None:
            entry.status = FSCENE_BLOCKED
            entry.error = error
        run.status = FRUN_BLOCKED
        run.error_code = error_code
        run.error = error
        await self._save_run(run)
        logger.warning(f"FullRun {run_id} blocked: {error_code} {error}")

    async def _do_pause(self, project_name: str, run_id: str):
        run = self._load_run(project_name, run_id)
        run.pause_requested = False
        run.status = FRUN_PAUSED
        await self._save_run(run)

    async def _do_cancel(self, project_name: str, run_id: str):
        run = self._load_run(project_name, run_id)
        run.cancel_requested = False
        run.status = FRUN_CANCELLED
        await self._save_run(run)

    # ---------- Finalize + Handoff to Production（§三十八~§四十三）----------
    async def _handoff_to_production(self, project_name: str, run_id: str):
        """§三十八/§四十/§四十二：final validation（复用 preflight_scene_autopilot）->
        find_by_parent_full_run 防重复 child -> ProjectProductionManager.start_run(parent_full_run_id) ->
        bind production_run_id -> _handle_production 等待 child。"""
        from deps import logger
        run = self._load_run(project_name, run_id)
        if run is None:
            return
        # §三十八 final validation：复用 preflight_scene_autopilot（不写第三套 readiness 规则，§三十九）
        project = self._load_project(project_name)
        project_dir = _project_dir_of(project_name)
        plans = _read_plans(project_dir)
        scene_mgr = get_autopilot_manager()
        for sc in project.scenes:
            plan = plans.get(sc.scene_id)
            has_active = scene_mgr.find_active_run(project_name, sc.scene_id) is not None
            pre = preflight_scene_autopilot(project, sc, plan, run.respect_locked, has_active_run=has_active)
            if not pre.ready:
                await self._block_run(project_name, run_id, ERR_EXTERNAL_SOURCE_CHANGED,
                                      f"故事段 {sc.scene_id} 最终校验未通过：{','.join(pre.issues)}"); return

        # source signature 最终校验
        changed = self._source_changed(project_name, run_id)
        if changed:
            await self._block_run(project_name, run_id, ERR_EXTERNAL_SOURCE_CHANGED, changed); return

        # crash-safe：find_by_parent_full_run（§四十二）防重复 production child
        prod_mgr = get_project_autopilot_manager()
        existing_child = prod_mgr.find_by_parent_full_run(project_name, run_id)
        if existing_child is not None:
            child = existing_child
            logger.info(f"FullRun {run_id} 复用现有 production child {child.run_id}")
        else:
            try:
                child = await prod_mgr.start_run(project_name, run.respect_locked,
                                                 parent_full_run_id=run_id)
            except _ExistingProjectRunError as e:
                await self._block_run(project_name, run_id, ERR_CHILD_RUN_CONFLICT,
                                      f"项目已有完整制作任务: {e.existing_run_id}"); return
            except ValueError as e:
                await self._fail_run(project_name, run_id, ERR_PROJECT_PRODUCTION_FAILED, str(e)); return

        run = self._load_run(project_name, run_id)
        run.production_run_id = child.run_id
        run.stage = FSTAGE_PRODUCTION
        run.status = FRUN_PRODUCING
        run.current_scene_id = None
        run.current_action = None
        await self._save_run(run)
        logger.info(f"FullRun {run_id} 交接 production child {child.run_id}")
        await self._handle_production(project_name, run_id)

    async def _fail_run(self, project_name: str, run_id: str, error_code: str, error: str):
        run = self._load_run(project_name, run_id)
        run.status = FRUN_FAILED
        run.error_code = error_code
        run.error = error
        await self._save_run(run)

    async def _handle_production(self, project_name: str, run_id: str):
        """§四十四：等待 production child terminal，映射状态到 FullRun。
        child paused/failed/blocked -> 不创建新 child；resume 时复用同 child（§六十一/§六十九）。"""
        from deps import logger
        run = self._load_run(project_name, run_id)
        if run is None:
            return
        if run.status == FRUN_COMPLETED:
            return
        # crash 恢复：production_run_id 缺失 -> find_by_parent_full_run 重新绑定（§四十二/§四十八）
        if not run.production_run_id:
            prod_mgr = get_project_autopilot_manager()
            child = prod_mgr.find_by_parent_full_run(project_name, run_id)
            if child is None:
                await self._fail_run(project_name, run_id, ERR_PROJECT_PRODUCTION_FAILED,
                                     "production child 不存在且无法找回"); return
            run.production_run_id = child.run_id
            run.status = FRUN_PRODUCING
            await self._save_run(run)
        run.status = FRUN_PRODUCING
        await self._save_run(run)

        prod_mgr = get_project_autopilot_manager()
        while True:
            # cancel/pause checkpoint
            run = self._load_run(project_name, run_id)
            if run.cancel_requested:
                run.cancel_requested = False
                run.status = FRUN_CANCELLED
                await self._save_run(run); return
            if run.pause_requested:
                # 传给 child（§四十五）
                try:
                    await prod_mgr.request_pause(project_name, run.production_run_id)
                except Exception:
                    pass
                run.pause_requested = False
                run.status = FRUN_PAUSED
                await self._save_run(run); return

            child = prod_mgr.get_run(project_name, run.production_run_id)
            if child is None:
                await self._fail_run(project_name, run_id, ERR_PROJECT_PRODUCTION_FAILED,
                                     "production child 丢失"); return
            if child.status in PROD_TERMINAL or child.status == PROD_PAUSED:
                break
            await asyncio.sleep(2.0)

        # 映射 child 状态（§四十四）
        run = self._load_run(project_name, run_id)
        if child.status == PROD_COMPLETED:
            run.status = FRUN_COMPLETED
            run.stage = FSTAGE_DONE
            run.final_output = child.final_output   # §六十：不复制，指向 child final.mp4
            run.error = None; run.error_code = None
            logger.info(f"FullRun {run_id} 完成 -> {child.final_output}")
        elif child.status == PROD_PAUSED:
            run.status = FRUN_PAUSED
        elif child.status == PROD_FAILED:
            run.status = FRUN_FAILED
            run.error_code = ERR_PROJECT_PRODUCTION_FAILED
            run.error = f"影片制作失败：{child.error or ''}"
        elif child.status == PROD_BLOCKED:
            run.status = FRUN_BLOCKED
            run.error_code = child.error_code or ERR_CHILD_RUN_CONFLICT
            run.error = f"影片制作被阻止：{child.error or ''}"
        elif child.status == PROD_CANCELLED:
            run.status = FRUN_CANCELLED
        await self._save_run(run)


# ============ 模块级 helper（避免循环 import）============
def _project_dir_of(project_name: str) -> Path:
    from deps import get_project_manager
    return get_project_manager().get_project_dir(project_name)


def get_project_dir_safe(project_name: str) -> Path:
    return _project_dir_of(project_name)


def _write_drafts_safe(project_name: str, drafts: dict):
    from routers.director import _write_drafts
    _write_drafts(_project_dir_of(project_name), drafts)


# ============ 单例 ============
_manager: Optional[FullProjectManager] = None

def get_full_project_manager() -> FullProjectManager:
    global _manager
    if _manager is None:
        _manager = FullProjectManager()
    return _manager
