# src/ai/project_autopilot.py
"""Prepared Project Autopilot（Phase AI-5A）。

项目级 Controller：approved Production Plan 的所有 Scene -> 顺序逐 Scene 自动制作（复用
SceneAutopilotManager）-> Composition Manifest -> ffmpeg 合成一条完整影片。

🔴 分层边界（§四十七/§四十八）：
  ProjectProductionManager -> SceneAutopilotManager -> JobQueue -> AgnesClient
  ProjectProductionManager 不直接碰 AgnesClient / JobQueue，只管理 child SceneRun + compose。
  不通过 HTTP 调 Scene Autopilot，直接调内部 service API。

🔴 两个核心不变量：
  1. source 冻结（§九/§十/§十四）：Run start 时冻结 source_snapshot + 所有 approved Plan snapshot。
     运行中 project.json 变化不影响当前 Run；进入下一 Scene 前比对 source signature，
     变化 -> blocked（SOURCE_STRUCTURE_CHANGED），不偷偷采用新 source。
  2. Run consistency（§三十三/§三十六/§三十九）：compose 用 child ShotExecutionState.video_version_id
     构建 Composition Manifest，不读当前 shot.final_video。一条成片可混合「本 Run 新视频 + locked 旧视频」。

🔴 crash safety（§七/§三十一）：parent-child 创建防 duplication。
  Project Controller 创建 child 前先 find_by_parent_project_run；backend crash 后恢复时
  通过 parent_project_run_id + scene_id 重新绑定 child，不创建第二套。
"""
from __future__ import annotations
import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field

from src.core.store import ProjectStore, _atomic_write_json
from src.core.models import ShotStatus
from src.ai.production_planner import is_plan_stale, SceneProductionPlan
from src.ai.c2_parallel import (shot_dependencies, scene_dependencies,
                                validate_parallelism, C2DependencyError, source_completed_for_run,
                                media_sources_for_shot)
from src.ai.autopilot import (
    get_autopilot_manager, preflight_scene_autopilot, SceneRunPreflight,
    SceneAutopilotRun,
    RUN_PENDING, RUN_RUNNING, RUN_PAUSED, RUN_PAUSING,
    RUN_COMPLETED, RUN_FAILED, RUN_BLOCKED, RUN_CANCELLED,
    RUN_ACTIVE, RUN_TERMINAL,
    _ExistingRunError, _SourceStructureChanged,
)


# ============ 状态常量（§四）============
# ProjectRun status
PRUN_PENDING = "pending"
PRUN_RUNNING = "running"
PRUN_PAUSING = "pausing"
PRUN_PAUSED = "paused"
PRUN_FAILED = "failed"
PRUN_BLOCKED = "blocked"
PRUN_COMPOSING = "composing"
PRUN_COMPLETED = "completed"
PRUN_CANCELLED = "cancelled"
PRUN_ACTIVE = {PRUN_PENDING, PRUN_RUNNING, PRUN_PAUSING, PRUN_PAUSED, PRUN_COMPOSING}   # 阻止新 Run
PRUN_TERMINAL = {PRUN_COMPLETED, PRUN_FAILED, PRUN_CANCELLED, PRUN_BLOCKED}
PRUN_RESUMABLE = {PRUN_PAUSED, PRUN_FAILED}   # blocked 不可 resume（source 变化，§二十九）

# stage
PSTAGE_PREFLIGHT = "preflight"
PSTAGE_SCENES = "scenes"
PSTAGE_COMPOSE = "compose"
PSTAGE_DONE = "done"

# ProjectSceneExecutionState status（§五）
PSCENE_PENDING = "pending"
PSCENE_RUNNING = "running"
PSCENE_PAUSED = "paused"
PSCENE_FAILED = "failed"
PSCENE_BLOCKED = "blocked"
PSCENE_COMPLETED = "completed"
PSCENE_CANCELLED = "cancelled"

# error_code
ERR_SOURCE_CHANGED = "SOURCE_STRUCTURE_CHANGED"
ERR_COMPOSE_FAILED = "COMPOSE_FAILED"


# ============ 数据结构 ============
class ProjectSceneExecutionState(BaseModel):
    """单个 Scene 在 ProjectRun 内的父级执行状态（§五）。只跟踪 child Run 状态，
    不复制 Shot checkpoint（那些在 SceneAutopilotRun 内）。"""
    scene_id: str
    index: int
    status: str = PSCENE_PENDING
    plan_id: Optional[str] = None
    child_run_id: Optional[str] = None
    shot_count: int = 0
    error: Optional[str] = None


class ProjectProductionRun(BaseModel):
    """项目级制作 Run（§四）。"""
    run_id: str
    project_name: str
    parent_full_run_id: Optional[str] = None   # AI-5B：父 FullProjectRun（None=手工 AI-5A 制作）
    status: str = PRUN_PENDING
    stage: str = PSTAGE_PREFLIGHT
    respect_locked: bool = True
    max_parallel_scenes: int = Field(default=1, ge=1, le=2)
    scene_dependencies: Dict[str, List[str]] = Field(default_factory=dict)
    # Full-film, per-shot edges: permits an independent shot in scene B to
    # start before scene A completes, even if another B shot relays A's tail.
    shot_dependencies: Dict[str, List[str]] = Field(default_factory=dict)
    # Frozen continuity/reference selections used by the DAG. Old runs use
    # their original signature contract (empty map = no extra validation).
    reference_asset_snapshot: Dict[str, Optional[str]] = Field(default_factory=dict)
    pause_requested: bool = False
    cancel_requested: bool = False
    current_scene_id: Optional[str] = None
    current_child_run_id: Optional[str] = None
    scenes: List[ProjectSceneExecutionState] = Field(default_factory=list)
    source_signature: Dict[str, Any] = Field(default_factory=dict)   # 冻结的 source signature（§十四），校验用
    source_snapshot_file: Optional[str] = None
    plans_snapshot_file: Optional[str] = None
    composition_manifest_file: Optional[str] = None
    final_output: Optional[str] = None
    final_duration_s: Optional[float] = None
    final_size_bytes: Optional[int] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    # AI-7C §59 targeted run：None = 既有全项目行为（向后兼容）；设值 = 只跑受影响闭包。
    target_scene_ids: Optional[List[str]] = None
    target_shot_ids: Optional[List[str]] = None
    shot_actions: Optional[Dict[str, Dict[str, Any]]] = None   # shot_id -> {first_frame,audio,last_frame,video}（manifest）
    parent_change_execution_id: Optional[str] = None          # §69 traceability
    created_at: float = 0.0
    updated_at: float = 0.0


class CompositionClip(BaseModel):
    """Manifest 中的单个镜头片段（§三十四）。video_version_id 来自 child ShotExecutionState，
    不是当前 shot.final_video（Run consistency，§三十六）。"""
    scene_id: str
    shot_id: str
    scene_run_id: str
    video_version_id: str
    local_path: Optional[str] = None
    url: Optional[str] = None
    duration_s: Optional[float] = None


class CompositionManifest(BaseModel):
    project_run_id: str
    clips: List[CompositionClip] = Field(default_factory=list)
    created_at: float = 0.0


# ============ AI-7C §64/§65 composition clips（pure，可单测）============
def _baseline_clip_for_shot(project, scene_id: str, shot_id: str) -> Optional[CompositionClip]:
    """§64：unaffected shot 的 frozen baseline clip——用当前 shot.final_video version_id
    （不读 child Run；unaffected shot 未重生成，沿用既有 final 版本）。无 final_video -> None。"""
    sh = project.get_shot(shot_id) if hasattr(project, "get_shot") else None
    if sh is None or not sh.final_video:
        return None
    vv = next((v for v in (sh.video_versions or [])
               if v.version_id == sh.final_video), None)
    if vv is None or not vv.local:
        return None
    return CompositionClip(scene_id=scene_id, shot_id=shot_id, scene_run_id="baseline",
                           video_version_id=sh.final_video, local_path=vv.local,
                           url=vv.url, duration_s=vv.seconds_returned)


def build_composition_clips(project, run: "ProjectProductionRun",
                            child_state_by_shot: Dict[str, Any],
                            child_run_id_by_shot: Optional[Dict[str, str]] = None
                            ) -> List[CompositionClip]:
    """§三十四/§64/§65：按 source_signature 顺序为**全部** project shot 构建 clip。
    - 受影响 shot（child_state_by_shot[shot_id].video_version_id 存在）：用 child 新 regen 版本（§三十六）。
    - targeted run（run.target_scene_ids 非空）下的 unaffected shot：用 shot.final_video baseline（§64 frozen）。
    - 非 targeted run：所有 shot 必须有 child state（既有行为，缺则 raise）。
    child_state_by_shot: {shot_id: ShotExecutionState}；child_run_id_by_shot: {shot_id: child_run_id}（traceability §69）。"""
    clips: List[CompositionClip] = []
    targeted = bool(run.target_scene_ids)
    target_scene_set = set(run.target_scene_ids or []) if targeted else set()
    target_shot_set = set(run.target_shot_ids or []) if run.target_shot_ids else None
    rid_by_shot = child_run_id_by_shot or {}
    for snap_scene in run.source_signature.get("scenes", []):
        scene_id = snap_scene.get("scene_id", "")
        for sh_dict in snap_scene.get("shots", []):
            shot_id = sh_dict.get("shot_id", "")
            state = child_state_by_shot.get(shot_id)
            # 该 shot 是否「target」（child 应跑过）vs「unaffected」（用 baseline）
            is_target = (scene_id in target_scene_set) or (target_shot_set and shot_id in target_shot_set)
            if state is not None and getattr(state, "video_version_id", None):
                vid = state.video_version_id
                sh = project.get_shot(shot_id) if hasattr(project, "get_shot") else None
                vv = next((v for v in (sh.video_versions or [])
                           if v.version_id == vid), None) if sh else None
                if vv is None or not vv.local:
                    raise RuntimeError(f"镜头 {shot_id} 版本 {vid} 不在视频历史")
                clips.append(CompositionClip(
                    scene_id=scene_id, shot_id=shot_id,
                    scene_run_id=rid_by_shot.get(shot_id, ""),
                    video_version_id=vid, local_path=vv.local, url=vv.url,
                    duration_s=vv.seconds_returned))
            elif targeted and not is_target:
                # unaffected shot（不在 target scene/shot 集合）-> baseline final_video（§64）
                bc = _baseline_clip_for_shot(project, scene_id, shot_id)
                if bc is None:
                    raise RuntimeError(f"镜头 {shot_id} 无 baseline final_video（unaffected shot 未完成，§64）")
                clips.append(bc)
            elif targeted and is_target:
                # target shot 却无 child state：child 应已跑过该 shot，缺则异常（不 fallback baseline）
                raise RuntimeError(f"镜头 {shot_id} 是 target shot 但 child Run 无视频版本")
            else:
                raise RuntimeError(f"镜头 {shot_id} 在 child Run 中无视频版本")
    return clips


# ============ Preflight 结果（§十七）============
class ScenePreflightResult(BaseModel):
    scene_id: str
    ready: bool
    shot_count: int
    plan_id: Optional[str] = None
    plan_status: Optional[str] = None
    issues: List[str] = Field(default_factory=list)


class ProjectProductionPreflight(BaseModel):
    ready: bool
    scene_count: int
    shot_count: int
    locked_shot_count: int
    scenes: List[ScenePreflightResult] = Field(default_factory=list)


# ============ source signature / snapshot（§九/§十四）============
def build_project_source_signature(project) -> Dict[str, Any]:
    """冻结影响完整 Run 一致性的创作输入（§十四）。只含创作母版，不含生成状态
    （prompt/批次/video version/video_end_frame/opening_source/source_version/link_mode/
    source_shot_id/JobQueue/updated_at 都排除——这些会被 Autopilot 自己修改，不能让自己的
    生成行为把 ProjectRun 判 stale）。"""
    return {
        "project_name": project.name,
        "story_text": project.story_text or "",
        "style": {
            "art_style": project.style.art_style,
            "palette": project.style.palette,
            "aspect_ratio": project.style.aspect_ratio,
            "size_tier": project.style.size_tier,
            "era": project.style.era,
            "extra": project.style.extra,
        },
        "scenes": [
            {
                "scene_id": sc.scene_id,
                "summary": sc.summary or "",
                "characters": list(sc.characters),
                "shots": [
                    {"shot_id": sh.shot_id, "description": sh.description or "",
                     "characters": list(sh.characters),
                     "target_duration_s": sh.target_duration_s,
                     "locked": bool(sh.locked)}
                    for sh in sc.shots
                ],
            }
            for sc in project.scenes
        ],
        "characters": [
            {"name": c.name,
             "description_en": c.description_en or "",
             "views": {k: {"local": v.local, "url": v.url, "status": v.status}
                       for k, v in c.views.items()}}
            for c in project.characters
        ],
    }


def preflight_project(project, project_dir: Path, *, respect_locked: bool = True,
                      project_name: Optional[str] = None,
                      target_scene_ids: Optional[List[str]] = None) -> ProjectProductionPreflight:
    """项目级 preflight（§十七）。复用 preflight_scene_autopilot 逐 Scene 检查（§十八 同一规则）。
    project_name 用于查 active SceneRun（find_active_run 需项目目录名）。
    target_scene_ids（M4/§53）：targeted run 只 preflight 受影响 scene——unaffected scene 用 baseline
    合成（§64）不参与重生成，其 plan/readiness 不作 gate。否则 unaffected 未规划 scene -> NO_PRODUCTION_PLAN
    -> ready=False -> targeted run 在 plan-freeze 前就崩（M4 原修复不够）。None=检查全部（既有行为）。"""
    from routers.production_planner import _read_plans
    scene_mgr = get_autopilot_manager()
    pname = project_name or project.name
    plans = _read_plans(project_dir)
    target_set = set(target_scene_ids) if target_scene_ids else None
    scenes_to_check = [sc for sc in project.scenes
                       if (target_set is None or sc.scene_id in target_set)]
    scene_results: List[ScenePreflightResult] = []
    total_shots = 0
    locked_total = 0
    for sc in scenes_to_check:
        plan = plans.get(sc.scene_id)
        has_active = scene_mgr.find_active_run(pname, sc.scene_id) is not None
        pre = preflight_scene_autopilot(project, sc, plan, respect_locked, has_active_run=has_active)
        scene_results.append(ScenePreflightResult(
            scene_id=sc.scene_id, ready=pre.ready, shot_count=pre.shot_count,
            plan_id=pre.plan_id, plan_status=pre.plan_status, issues=pre.issues))
        total_shots += len(sc.shots)
        locked_total += sum(1 for sh in sc.shots if sh.locked)
    ready = bool(scene_results) and all(s.ready for s in scene_results)
    return ProjectProductionPreflight(
        ready=ready, scene_count=len(scenes_to_check),
        shot_count=total_shots, locked_shot_count=locked_total, scenes=scene_results)


# ============ Manager ============
class _ExistingProjectRunError(Exception):
    """项目已有 active ProjectRun（§二十）。携带 existing_run_id。"""
    def __init__(self, existing_run_id: str):
        self.existing_run_id = existing_run_id
        super().__init__(f"已有完整制作任务: {existing_run_id}")


class ProjectProductionManager:
    """项目级制作生命周期 + 有依赖图的 Scene 并行编排 + 合成。"""

    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._run_tasks: Dict[str, asyncio.Task] = {}

    # ---------- 路径 / IO ----------
    def _runs_root(self, project_name: str) -> Path:
        from deps import get_project_manager
        d = get_project_manager().get_project_dir(project_name) / "output" / "production_runs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _run_dir(self, project_name: str, run_id: str) -> Path:
        d = self._runs_root(project_name) / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _run_path(self, project_name: str, run_id: str) -> Path:
        return self._run_dir(project_name, run_id) / "run.json"

    def _manifest_path(self, run: ProjectProductionRun) -> Path:
        return self._run_dir(run.project_name, run.run_id) / "composition_manifest.json"

    def _lock_for(self, run_id: str) -> asyncio.Lock:
        if run_id not in self._locks:
            self._locks[run_id] = asyncio.Lock()
        return self._locks[run_id]

    def _load_run(self, project_name: str, run_id: str) -> Optional[ProjectProductionRun]:
        p = self._run_path(project_name, run_id)
        if not p.exists():
            return None
        try:
            return ProjectProductionRun.model_validate(json.loads(p.read_text(encoding="utf-8")))
        except Exception as e:
            from deps import logger
            logger.warning(f"加载 ProjectRun {run_id} 失败: {e}")
            return None

    async def _save_run(self, run: ProjectProductionRun):
        """per-run Lock 保护原子写（§三十二）。"""
        async with self._lock_for(run.run_id):
            self._write_run(run)

    def _write_run(self, run: ProjectProductionRun):
        """原子写（caller 已持 per-run lock 时直接落盘；asyncio.Lock 不可重入，
        锁内临界区用本方法而非 _save_run，AI-7C-H closure）。"""
        run.updated_at = time.time()
        _atomic_write_json(self._run_path(run.project_name, run.run_id),
                           json.loads(run.model_dump_json()))

    def list_runs(self, project_name: str) -> List[ProjectProductionRun]:
        out = []
        root = self._runs_root(project_name)
        for d in sorted(root.iterdir()):
            p = d / "run.json"
            if not p.exists():
                continue
            try:
                out.append(ProjectProductionRun.model_validate(json.loads(p.read_text(encoding="utf-8"))))
            except Exception:
                pass
        out.sort(key=lambda r: r.created_at, reverse=True)
        return out

    def get_run(self, project_name: str, run_id: str) -> Optional[ProjectProductionRun]:
        return self._load_run(project_name, run_id)

    def find_active_project_run(self, project_name: str) -> Optional[ProjectProductionRun]:
        for r in self.list_runs(project_name):
            if r.status in PRUN_ACTIVE:
                return r
        return None

    def find_by_parent_full_run(self, project_name: str,
                                full_run_id: str) -> Optional[ProjectProductionRun]:
        """crash-safe child 查找（§四十一/§四十二）：按 parent_full_run_id 找已存在的 ProjectProductionRun
        （任意状态）。FullProjectManager 创建 production child 前先调用，命中则复用，避免 backend crash
        后重建第二套 child（full_run.production_run_id 未保存的窗口）。"""
        for r in self.list_runs(project_name):
            if r.parent_full_run_id == full_run_id:
                return r
        return None

    async def _edit_run(self, project_name: str, run_id: str, edit):
        """Atomic run-level edit: concurrent pause/cancel and child completions
        cannot overwrite one another's checkpoint fields.
        """
        async with self._lock_for(run_id):
            latest = self._load_run(project_name, run_id)
            if latest is None:
                raise RuntimeError(f"missing production run {run_id}")
            edit(latest)
            self._write_run(latest)
            return latest

    # ---------- manifest IO ----------
    def _load_manifest(self, run: ProjectProductionRun) -> Optional[CompositionManifest]:
        p = self._manifest_path(run)
        if not p.exists():
            return None
        try:
            return CompositionManifest.model_validate(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def _save_manifest(self, run: ProjectProductionRun, manifest: CompositionManifest):
        _atomic_write_json(self._manifest_path(run), json.loads(manifest.model_dump_json()))

    # ---------- 启动 / 恢复 / 控制 ----------
    async def start_run(self, project_name: str, respect_locked: bool = True,
                        parent_full_run_id: Optional[str] = None, *,
                        target_scene_ids: Optional[List[str]] = None,
                        target_shot_ids: Optional[List[str]] = None,
                        shot_actions: Optional[Dict[str, Dict[str, Any]]] = None,
                        parent_change_execution_id: Optional[str] = None,
                        max_parallel_scenes: int = 2) -> ProjectProductionRun:
        """§十九 start 流程：load -> 检查无 active ProjectRun -> project preflight ->
        冻结 source snapshot + plans snapshot -> 建 run dir -> 写 run.json -> 启动 Executor。
        后端自己 load，不信任前端 preflight（§十九）。
        parent_full_run_id：AI-5B FullProjectManager 传入（手工 AI-5A 制作为 None，§四十一）。
        AI-7C §59：target_scene_ids 非空 = targeted run（只跑受影响 scene 闭包，unaffected shot 用
        baseline final_video 合成 §64）。None = 既有全项目行为（向后兼容）。"""
        from deps import get_project_manager, logger
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        if not project.scenes:
            raise ValueError("项目没有故事段，无法完整制作")

        # 一个项目第一版只允许一个 active ProjectRun（§二十）
        active = self.find_active_project_run(project_name)
        if active:
            raise _ExistingProjectRunError(active.run_id)

        # project preflight（§十九：start 时后端重新完整检查一次）。M4/§53：targeted run 只查 scenes_to_run
        pre = preflight_project(project, project_dir, respect_locked=respect_locked,
                                project_name=project_name,
                                target_scene_ids=target_scene_ids)
        if not pre.ready:
            problems = "; ".join(f"{s.scene_id}({','.join(s.issues)})"
                                 for s in pre.scenes if not s.ready)
            raise ValueError(f"项目未准备完成：{problems}")

        # 冻结 source snapshot + signature（§九/§十四）
        signature = build_project_source_signature(project)

        # AI-7C §59：targeted run 只跑受影响 scene（unaffected scene 不进 run.scenes，合成用 baseline）
        scenes_to_run = project.scenes
        if target_scene_ids is not None:
            tset = set(target_scene_ids)
            scenes_to_run = [sc for sc in project.scenes if sc.scene_id in tset]
            if not scenes_to_run:
                raise ValueError(f"target_scene_ids {target_scene_ids} 无匹配场景")

        # 冻结 approved Plan（§十一）。M4/§53：targeted run 只需 scenes_to_run 的 plan
        # （affected scenes only）；unaffected scene 无 plan 不阻断 targeted run。非 targeted 仍冻结全部（既有行为）。
        from routers.production_planner import _read_plans
        plans = _read_plans(project_dir)
        plans_snapshot: Dict[str, dict] = {}
        for sc in scenes_to_run:
            plan = plans.get(sc.scene_id)
            if plan is None:
                raise ValueError(f"故事段 {sc.scene_id} 无 approved Plan")
            plans_snapshot[sc.scene_id] = plan.model_dump()

        # Freeze the dependency DAG with approved Producer intent.  Scene
        # concurrency is not equivalent to media worker concurrency: upstream
        # actual end-frame dependencies are represented as cross-scene edges.
        max_parallel_scenes = validate_parallelism(max_parallel_scenes)
        try:
            all_deps = shot_dependencies(project, plans,
                                         selected_scene_ids=[sc.scene_id for sc in scenes_to_run])
            scene_deps = scene_dependencies(project, all_deps,
                                            [sc.scene_id for sc in scenes_to_run])
        except C2DependencyError as exc:
            raise ValueError(f"C2 parallel dependency plan invalid: {exc}") from exc

        # 创建 Run
        run_id = f"prun_{uuid.uuid4().hex[:10]}"
        run = ProjectProductionRun(
            run_id=run_id, project_name=project_name, parent_full_run_id=parent_full_run_id,
            status=PRUN_PENDING, stage=PSTAGE_PREFLIGHT, respect_locked=respect_locked,
            source_signature=signature,
            max_parallel_scenes=max_parallel_scenes,
            scene_dependencies={sid: sorted(deps) for sid, deps in scene_deps.items()},
            shot_dependencies={sid: sorted(deps) for sid, deps in all_deps.items()},
            reference_asset_snapshot={sh.shot_id: getattr(sh, "video_reference_asset_id", None)
                                      for sc in project.scenes for sh in sc.shots},
            target_scene_ids=target_scene_ids, target_shot_ids=target_shot_ids,
            shot_actions=shot_actions, parent_change_execution_id=parent_change_execution_id,
            created_at=time.time(), updated_at=time.time(),
        )
        for i, sc in enumerate(scenes_to_run):
            plan = plans.get(sc.scene_id)
            run.scenes.append(ProjectSceneExecutionState(
                scene_id=sc.scene_id, index=i, status=PSCENE_PENDING,
                plan_id=plan.plan_id if plan else None,
                shot_count=len(sc.shots)))

        # 写 source_snapshot.json + production_plans.json + run.json
        run_dir = self._run_dir(project_name, run_id)
        snap_path = run_dir / "source_snapshot.json"
        _atomic_write_json(snap_path, signature)
        plans_path = run_dir / "production_plans.json"
        _atomic_write_json(plans_path, plans_snapshot)
        run.source_snapshot_file = str(snap_path)
        run.plans_snapshot_file = str(plans_path)
        await self._save_run(run)
        logger.info(f"ProjectRun 启动: {project_name} run={run_id} "
                    f"({len(run.scenes)} 故事段, {pre.shot_count} 镜头)")
        self._launch_loop(run)
        return run

    def _launch_loop(self, run: ProjectProductionRun):
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

    async def resume_run(self, project_name: str, run_id: str) -> ProjectProductionRun:
        """Resume 同 ProjectRun / 同 source snapshot / 同 plans snapshot（§二十七）。
        blocked 不可 resume（source 变化，§二十九）。
        AI-7C-H closure：per-run lock 内 reload-latest 复查 resumable——
        request_cancel 可将 paused 直接转终态 cancelled，终态不被 resume 复活。"""
        async with self._lock_for(run_id):
            run = self._load_run(project_name, run_id)
            if run is None:
                raise ValueError("Run 不存在")
            if run.status not in PRUN_RESUMABLE:
                raise ValueError(f"Run 状态 {run.status} 不可 resume")
            # Parent resume must re-queue *all* paused children. Their IDs
            # remain bound to existing child runs, so they resume at their
            # own saved media stages without duplicating any provider POST.
            # A raw PSCENE_PAUSED marker here would otherwise be mistaken
            # for a fresh independent pause request by the DAG coordinator.
            for entry in run.scenes:
                if entry.status == PSCENE_PAUSED:
                    entry.status = PSCENE_PENDING
            run.pause_requested = run.cancel_requested = False
            run.status = PRUN_RUNNING
            run.error = None
            run.error_code = None
            self._write_run(run)
        self._launch_loop(run)
        return run

    async def request_pause(self, project_name: str, run_id: str) -> ProjectProductionRun:
        """Pause every active child; never just the legacy first UI child."""
        def mark(latest):
            if latest.status not in (PRUN_RUNNING, PRUN_PENDING, PRUN_PAUSING):
                raise ValueError(f"Run 状态 {latest.status} 不可暂停")
            latest.pause_requested = True
            latest.status = PRUN_PAUSING
        run = await self._edit_run(project_name, run_id, mark)
        manager = get_autopilot_manager()
        for entry in run.scenes:
            if entry.child_run_id and entry.status == PSCENE_RUNNING:
                try:
                    await manager.request_pause(project_name, entry.child_run_id)
                except ValueError:
                    pass
        return run

    async def request_cancel(self, project_name: str, run_id: str) -> ProjectProductionRun:
        """Cooperatively cancel all running scenes, or atomically cancel paused."""
        def mark(latest):
            if latest.status in PRUN_TERMINAL:
                raise ValueError(f"Run 已 {latest.status}")
            if latest.status == PRUN_PAUSED:
                latest.cancel_requested = False
                latest.status = PRUN_CANCELLED
            else:
                latest.cancel_requested = True
        run = await self._edit_run(project_name, run_id, mark)
        if run.status == PRUN_CANCELLED:
            return run
        manager = get_autopilot_manager()
        for entry in run.scenes:
            if entry.child_run_id and entry.status == PSCENE_RUNNING:
                try:
                    await manager.request_cancel(project_name, entry.child_run_id)
                except ValueError:
                    pass
        return run

    async def recover_runs(self):
        """startup 恢复（§三十/§三十一）：扫描所有项目的 running/pausing/composing/pending Run。
        pause_requested -> PAUSED；cancel_requested -> CANCELLED；否则重新调度。
        AI-7C-H closure：先对账 paused —— paused + cancel_requested -> cancelled
        （复用 request_cancel 的直接终态路径；普通 paused 保持 paused，绝不自动 resume）。
        必须在 SceneAutopilotManager.recover_runs() 之后执行（Project 恢复时需要看到 child 正确状态）。"""
        from deps import get_project_manager, logger
        pm = get_project_manager()
        if not pm.base_dir.exists():
            return
        n = 0
        for proj_dir in pm.base_dir.iterdir():
            root = proj_dir / "output" / "production_runs"
            if not root.exists():
                continue
            for d in root.iterdir():
                p = d / "run.json"
                if not p.exists():
                    continue
                try:
                    run = ProjectProductionRun.model_validate(json.loads(p.read_text(encoding="utf-8")))
                except Exception:
                    continue
                current = self._run_tasks.get(run.run_id)
                if current is not None and not current.done():
                    continue  # preserve the live owner's requests/checkpoints
                if run.status == PRUN_PAUSED:
                    # AI-7C-H closure：paused+cancel_requested 在此对账为 cancelled
                    # （旧数据/崩溃窗口持久化的组合态，无 loop 可消费该请求）。
                    if run.cancel_requested:
                        try:
                            await self.request_cancel(run.project_name, run.run_id)
                        except ValueError:
                            pass   # 竞态下已转 terminal/被恢复 active，既有路径继续收敛
                    continue
                if run.status not in (PRUN_RUNNING, PRUN_PAUSING, PRUN_COMPOSING, PRUN_PENDING):
                    continue
                self._launch_loop(run)
                n += 1
                logger.info(f"恢复 ProjectRun {run.run_id} ({run.project_name})")
        if n:
            logger.info(f"ProjectRun 恢复 {n} 个")

    # ---------- 执行循环（§二十二）----------
    async def _run_loop(self, project_name: str, run_id: str):
        """Dynamic scene DAG executor, shared by FullProject/C2 and manual runs.

        An independent later scene may launch before an earlier scene finishes.
        Continuity and Producer-declared required-serial edges gate launch.
        All task/child IDs are persisted before the coordinator awaits them.
        """
        from deps import logger
        tasks: Dict[str, asyncio.Task] = {}
        try:
            while True:
                run = self._load_run(project_name, run_id)
                if run is None:
                    return
                for sid, task in list(tasks.items()):
                    if task.done():
                        tasks.pop(sid)
                        if not task.cancelled() and task.exception():
                            msg = str(task.exception())[:300]
                            def mark_failed(latest, scene_id=sid, error=msg):
                                e = next(e for e in latest.scenes if e.scene_id == scene_id)
                                e.status, e.error = PSCENE_FAILED, error
                            await self._edit_run(project_name, run_id, mark_failed)
                        elif not task.cancelled():
                            child = task.result()
                            def complete(latest, scene_id=sid, completed=child):
                                entry = next(e for e in latest.scenes if e.scene_id == scene_id)
                                entry.child_run_id = completed.run_id
                                entry.status = self._scene_status_from_child(completed)
                                if completed.status in (RUN_FAILED, RUN_BLOCKED):
                                    entry.error = completed.error
                                active = [e for e in latest.scenes if e.status == PSCENE_RUNNING]
                                first = min(active, key=lambda e: e.index) if active else None
                                latest.current_scene_id = first.scene_id if first else None
                                latest.current_child_run_id = first.child_run_id if first else None
                            await self._edit_run(project_name, run_id, complete)
                run = self._load_run(project_name, run_id)
                if run.status in PRUN_TERMINAL:
                    if tasks:
                        await asyncio.wait(tasks.values(), timeout=0.1,
                                           return_when=asyncio.FIRST_COMPLETED)
                        continue
                    return
                # Propagate pause/cancel to ALL child scenes, including those
                # rebound after restart. Do not abandon a paid in-flight job.
                if run.pause_requested or run.cancel_requested:
                    for entry in run.scenes:
                        if entry.status != PSCENE_RUNNING or not entry.child_run_id:
                            continue
                        child = get_autopilot_manager().get_run(project_name, entry.child_run_id)
                        if child is None or child.status in RUN_TERMINAL or child.status == RUN_PAUSED:
                            continue
                        try:
                            if run.cancel_requested:
                                await get_autopilot_manager().request_cancel(project_name, entry.child_run_id)
                            elif not child.pause_requested:
                                await get_autopilot_manager().request_pause(project_name, entry.child_run_id)
                        except ValueError:
                            pass
                    if tasks:
                        await asyncio.wait(tasks.values(), timeout=0.15,
                                           return_when=asyncio.FIRST_COMPLETED)
                        continue
                    def settle(latest):
                        latest.status = PRUN_CANCELLED if latest.cancel_requested else PRUN_PAUSED
                        latest.cancel_requested = latest.pause_requested = False
                        latest.current_scene_id = latest.current_child_run_id = None
                    await self._edit_run(project_name, run_id, settle)
                    return
                # Failed sibling: keep the successful independent results, but
                # never launch more work after detecting a real failure.
                bad = next((e for e in run.scenes if e.status in
                            (PSCENE_FAILED, PSCENE_BLOCKED, PSCENE_CANCELLED)), None)
                if bad and not tasks:
                    def fail(latest):
                        latest.status = {
                            PSCENE_FAILED: PRUN_FAILED,
                            PSCENE_BLOCKED: PRUN_BLOCKED,
                            PSCENE_CANCELLED: PRUN_CANCELLED,
                        }[bad.status]
                        latest.error = f"故事段 {bad.scene_id}: {bad.error or bad.status}"
                        if bad.status == PSCENE_BLOCKED:
                            latest.error_code = ERR_SOURCE_CHANGED
                    await self._edit_run(project_name, run_id, fail)
                    return
                if run.stage in (PSTAGE_COMPOSE, PSTAGE_DONE):
                    if tasks:
                        raise RuntimeError("cannot compose with active child scenes")
                    await self._compose_project(project_name, run_id)
                    return

                if all(e.status == PSCENE_COMPLETED for e in run.scenes):
                    # Validate source immediately before composition too. An
                    # external edit during already-running scenes is not safe.
                    changed = self._validate_source_for_scene(project_name, run)
                    if changed:
                        await self._edit_run(project_name, run_id, lambda latest: (
                            setattr(latest, "status", PRUN_BLOCKED),
                            setattr(latest, "error_code", ERR_SOURCE_CHANGED),
                            setattr(latest, "error", changed)))
                        return
                    await self._edit_run(project_name, run_id, lambda latest: (
                        setattr(latest, "stage", PSTAGE_COMPOSE)))
                    continue

                paused_child = any(e.status == PSCENE_PAUSED for e in run.scenes)
                if paused_child and not run.pause_requested:
                    # A scene was paused independently (rather than via the
                    # parent endpoint). Pause sibling scenes and the parent,
                    # never auto-resume the paused child without authorization.
                    await self._edit_run(project_name, run_id, lambda latest:
                                         setattr(latest, "pause_requested", True))
                    continue
                if not bad:
                    changed = self._validate_source_for_scene(project_name, run)
                    if changed:
                        if tasks:
                            # Do not start new work, but let posted jobs settle
                            # before final blocked status; source is already stale.
                            await asyncio.wait(tasks.values(), timeout=0.15,
                                               return_when=asyncio.FIRST_COMPLETED)
                            continue
                        await self._edit_run(project_name, run_id, lambda latest: (
                            setattr(latest, "status", PRUN_BLOCKED),
                            setattr(latest, "error_code", ERR_SOURCE_CHANGED),
                            setattr(latest, "error", changed)))
                        return
                    slots = max(0, validate_parallelism(run.max_parallel_scenes) - len(tasks))
                    for entry in sorted(run.scenes, key=lambda e: e.index):
                        if slots <= 0 or run.pause_requested or run.cancel_requested:
                            break
                        if entry.status in (PSCENE_COMPLETED, PSCENE_BLOCKED, PSCENE_CANCELLED) or entry.scene_id in tasks:
                            continue
                        # A *scene-wide* edge is too conservative: one B shot
                        # may need A's tail while another B shot is independent.
                        # Only launch when at least one shot in B is runnable,
                        # so waiting scenes cannot consume both scheduler slots.
                        # Older frozen runs have no shot DAG; preserve their
                        # original scene-level serial semantics.
                        if not entry.child_run_id:
                            # Crash window: a child can be committed but the
                            # parent binding not yet checkpointed. Rebind that
                            # existing child even when none of its *remaining*
                            # shots is currently ready, or resume could stall.
                            existing_child = get_autopilot_manager().find_by_parent_project_run(
                                project_name, run.run_id, entry.scene_id)
                            if existing_child is None and not self._scene_can_start(
                                    project_name, run, entry):
                                continue
                        try:
                            child = await self._start_or_reuse_child(project_name, run, entry)
                        except (_SourceStructureChanged, _ExistingRunError) as e:
                            kind = PSCENE_BLOCKED
                            msg = str(e)[:300]
                        except Exception as e:
                            kind = PSCENE_FAILED
                            msg = str(e)[:300]
                        else:
                            kind, msg = PSCENE_RUNNING, None
                        def bind(latest, scene_id=entry.scene_id, child_run=child if kind == PSCENE_RUNNING else None,
                                 status=kind, error=msg):
                            e = next(e for e in latest.scenes if e.scene_id == scene_id)
                            e.status, e.error = status, error
                            if child_run:
                                e.child_run_id = child_run.run_id
                                if not latest.current_scene_id:
                                    latest.current_scene_id = scene_id
                                    latest.current_child_run_id = child_run.run_id
                            latest.status, latest.stage = PRUN_RUNNING, PSTAGE_SCENES
                        run = await self._edit_run(project_name, run_id, bind)
                        if kind != PSCENE_RUNNING:
                            break
                        tasks[entry.scene_id] = asyncio.create_task(self._wait_child(project_name, child.run_id))
                        slots -= 1
                if not tasks:
                    run = self._load_run(project_name, run_id)
                    if any(e.status in (PSCENE_FAILED, PSCENE_BLOCKED, PSCENE_CANCELLED)
                           for e in run.scenes):
                        continue
                    # Missing dependency or lost child must block, never spin
                    # forever or create two children for the same scene.
                    await self._edit_run(project_name, run_id, lambda latest: (
                        setattr(latest, "status", PRUN_BLOCKED),
                        setattr(latest, "error", "场景依赖尚未就绪，无法继续调度")))
                    return
                await asyncio.wait(tasks.values(), timeout=0.2,
                                   return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            # Backend process crash or test-injected coordinator loss: child
            # IDs/JobQueue receipts are persisted; the recovery loop rebinds.
            raise
        except Exception as e:
            logger.exception(f"ProjectRun _run_loop {run_id} 异常")
            try:
                await self._edit_run(project_name, run_id, lambda latest: (
                    setattr(latest, "status", PRUN_FAILED),
                    setattr(latest, "error", f"执行循环异常: {str(e)[:400]}")))
            finally:
                if tasks:
                    await asyncio.gather(*tasks.values(), return_exceptions=True)

    def _next_incomplete_scene(self, run: ProjectProductionRun) -> Optional[ProjectSceneExecutionState]:
        """第一个未完成的 Scene（pending/running/paused/failed）。blocked 不重试（source 变化）。"""
        for entry in run.scenes:
            if entry.status in (PSCENE_PENDING, PSCENE_RUNNING, PSCENE_PAUSED, PSCENE_FAILED):
                return entry
        return None

    def _scene_status_from_child(self, child: SceneAutopilotRun) -> str:
        return {
            RUN_COMPLETED: PSCENE_COMPLETED,
            RUN_PAUSED: PSCENE_PAUSED,
            RUN_FAILED: PSCENE_FAILED,
            RUN_BLOCKED: PSCENE_BLOCKED,
            RUN_CANCELLED: PSCENE_CANCELLED,
        }.get(child.status, PSCENE_FAILED)

    def _validate_source_for_scene(self, project_name: str, run: ProjectProductionRun) -> Optional[str]:
        """进入下一 Scene 前比对当前 project source signature 与冻结 signature（§十三/§十五）。
        返回 stale 原因（None=未变化）。覆盖 story/style/所有 Scene 结构/所有 Character views。"""
        from deps import get_project_manager
        project = ProjectStore(get_project_manager().get_project_dir(project_name)).load()
        current = build_project_source_signature(project)
        if current != run.source_signature:
            return ("项目的故事、角色或镜头规划在完整制作期间发生了变化。"
                    "当前制作任务已安全停止。请确认最新制作计划后重新开始一次完整制作。")
        if run.reference_asset_snapshot:
            actual_refs = {sh.shot_id: getattr(sh, "video_reference_asset_id", None)
                           for scene in project.scenes for sh in scene.shots}
            if actual_refs != run.reference_asset_snapshot:
                return "镜头视频参考或尾帧接力源在冻结后改变，必须重新确认依赖计划"
        return None

    def _scene_can_start(self, project_name: str, run: ProjectProductionRun,
                         entry: ProjectSceneExecutionState) -> bool:
        """Select a scene if it contains a currently runnable *shot*.

        With two scene slots we must not reserve a slot for an entirely
        dependency-blocked scene. A scene that has an independent shot may
        start while another of its shots waits for a cross-scene video tail.
        """
        if not run.shot_dependencies:
            done = {e.scene_id for e in run.scenes if e.status == PSCENE_COMPLETED}
            return set(run.scene_dependencies.get(entry.scene_id, [])) <= done
        from deps import get_project_manager
        project = ProjectStore(get_project_manager().get_project_dir(project_name)).load()
        scene = next((s for s in project.scenes if s.scene_id == entry.scene_id), None)
        plan_data = self._frozen_plan_for_scene(run, entry.scene_id)
        if scene is None or not plan_data:
            return False
        plan = SceneProductionPlan.model_validate(plan_data)
        planned_by_id = {p.shot_id: p for p in plan.shots}
        scene_manager = get_autopilot_manager()
        for shot in scene.shots:
            deps = run.shot_dependencies.get(shot.shot_id, [])
            planned = planned_by_id.get(shot.shot_id)
            if planned is None:
                continue
            media = media_sources_for_shot(project, planned, shot.shot_id)
            if all(source_completed_for_run(
                    project, source, parent_run=run, scene_manager=scene_manager,
                    require_media=source in media) for source in deps):
                return True
        return False

    async def _start_or_reuse_child(self, project_name: str, run: ProjectProductionRun,
                                    entry: ProjectSceneExecutionState) -> SceneAutopilotRun:
        """crash-safe 创建/复用 child（§七）：先 find_by_parent_project_run，命中则复用
        （paused/failed -> resume），否则从 frozen plan snapshot 创建新 child。"""
        scene_mgr = get_autopilot_manager()
        existing = scene_mgr.find_by_parent_project_run(project_name, run.run_id, entry.scene_id)
        if existing is not None:
            if existing.status in (RUN_PAUSED, RUN_FAILED):
                try:
                    existing = await scene_mgr.resume_run(project_name, existing.run_id)
                except Exception as e:
                    from deps import logger
                    logger.warning(f"恢复 child {existing.run_id} 失败: {e}")
            return existing
        plan_snapshot = self._frozen_plan_for_scene(run, entry.scene_id)
        if plan_snapshot is None:
            raise _SourceStructureChanged(f"冻结计划中无故事段 {entry.scene_id}")
        return await scene_mgr.start_scene_run_from_snapshot(
            project_name, entry.scene_id, plan_snapshot,
            respect_locked=run.respect_locked, parent_project_run_id=run.run_id)

    def _frozen_plan_for_scene(self, run: ProjectProductionRun, scene_id: str) -> Optional[dict]:
        """从 production_plans.json（冻结）取某 scene 的 plan snapshot。"""
        if not run.plans_snapshot_file:
            return None
        p = Path(run.plans_snapshot_file)
        if not p.exists():
            return None
        try:
            plans = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        return plans.get(scene_id)

    async def _wait_child(self, project_name: str, child_run_id: str) -> SceneAutopilotRun:
        """轮询 child Run 直到 terminal 或 paused（§二十七）。Executor 不直接干预 child 内部。"""
        scene_mgr = get_autopilot_manager()
        while True:
            child = scene_mgr.get_run(project_name, child_run_id)
            if child is None:
                raise RuntimeError(f"child run {child_run_id} 不存在")
            if child.status in RUN_TERMINAL or child.status == RUN_PAUSED:
                return child
            await asyncio.sleep(2.0)

    # ---------- Composition Manifest + compose（§三十三~§四十五）----------
    def _build_composition_manifest(self, project_name: str,
                                    run: ProjectProductionRun) -> CompositionManifest:
        """所有 Scene child completed 后，构建 Manifest（§三十四/§64/§65）。
        顺序：source_signature scene/shot order。targeted run：受影响 shot 用 child 新版本 +
        unaffected shot 用 baseline final_video（§64）。🔴 用 child state.video_version_id，
        不读当前 shot.final_video（§三十六 Run consistency）；unaffected baseline 是 frozen 既有版本。"""
        from deps import get_project_manager
        scene_mgr = get_autopilot_manager()
        project = ProjectStore(get_project_manager().get_project_dir(project_name)).load()
        # 解析各 child 的 ShotExecutionState（target scenes 的 child）
        child_state_by_shot: Dict[str, Any] = {}
        child_run_id_by_shot: Dict[str, str] = {}
        for entry in run.scenes:
            if entry.status != PSCENE_COMPLETED:
                raise RuntimeError(f"故事段 {entry.scene_id} 未完成（{entry.status}），无法合成")
            child = scene_mgr.get_run(project_name, entry.child_run_id) if entry.child_run_id else None
            if child is None:
                raise RuntimeError(f"故事段 {entry.scene_id} 的子 Run 不存在")
            for st in child.shots:
                child_state_by_shot[st.shot_id] = st
                child_run_id_by_shot[st.shot_id] = child.run_id
        clips = build_composition_clips(project, run, child_state_by_shot, child_run_id_by_shot)
        return CompositionManifest(project_run_id=run.run_id, clips=clips, created_at=time.time())

    async def _compose_project(self, project_name: str, run_id: str):
        """所有 Scene 完成 -> 构建 manifest -> 校验 -> 落盘 -> compose（§三十七/§四十二/§四十三）。
        compose 失败 -> failed + stage=compose + COMPOSE_FAILED，Resume 只重试 compose。"""
        from deps import logger
        run = self._load_run(project_name, run_id)
        if run is None:
            return
        if run.status == PRUN_COMPLETED:
            return
        run.status = PRUN_COMPOSING
        run.stage = PSTAGE_COMPOSE
        await self._save_run(run)
        try:
            # manifest：已存在则复用（§四十三 resume 不重建），否则构建+落盘（§三十七）
            manifest = self._load_manifest(run)
            if manifest is None:
                manifest = self._build_composition_manifest(project_name, run)
                self._save_manifest(run, manifest)
            run.composition_manifest_file = str(self._manifest_path(run))
            await self._save_run(run)
            # compose（线程内跑 ffmpeg，不阻塞事件循环）
            final_path = await asyncio.to_thread(self._compose_sync, project_name, run, manifest)
            run = self._load_run(project_name, run_id)
            run.final_output = str(final_path)
            run.final_duration_s = self._probe_duration(final_path)
            run.final_size_bytes = final_path.stat().st_size if final_path.exists() else None
            run.status = PRUN_COMPLETED
            run.stage = PSTAGE_DONE
            run.error = None
            run.error_code = None
            await self._save_run(run)
            logger.info(f"ProjectRun 合成完成: {run.run_id} -> {final_path}")
        except Exception as e:
            logger.exception(f"ProjectRun compose 失败 {run_id}")
            run = self._load_run(project_name, run_id)
            run.status = PRUN_FAILED
            run.stage = PSTAGE_COMPOSE
            run.error_code = ERR_COMPOSE_FAILED
            run.error = f"完整影片合成失败: {str(e)[:400]}"
            await self._save_run(run)

    def _compose_sync(self, project_name: str, run: ProjectProductionRun,
                      manifest: CompositionManifest) -> Path:
        """复用 VideoComposer.compose_video_versions（§三十八），传入 Manifest 的精确 clip。
        Composer 只合成这些 clip，不读 shot.final_video（§三十九），不改任何 Shot（§四十四）。"""
        from src.pipeline.video_composer import VideoComposer
        from src.utils.file_saver import FileSaver
        from deps import get_project_manager
        project_dir = get_project_manager().get_project_dir(project_name)
        composer = VideoComposer(FileSaver(project_dir))
        output_path = self._run_dir(project_name, run.run_id) / "final.mp4"
        width, height, fps = self._compose_dims(project_name)
        return composer.compose_video_versions(manifest.clips, output_path,
                                               width=width, height=height, fps=fps)

    def _compose_dims(self, project_name: str):
        """按项目画幅选合成尺寸（16:9 -> 1280x720）。"""
        from deps import get_project_manager
        project = ProjectStore(get_project_manager().get_project_dir(project_name)).load()
        ratio = project.style.aspect_ratio or "16:9"
        if ratio == "9:16":
            return 720, 1280, 24
        if ratio == "1:1":
            return 720, 720, 24
        return 1280, 720, 24

    @staticmethod
    def _probe_duration(path: Path) -> Optional[float]:
        """ffprobe 探最终成片时长（秒）。失败返回 None。"""
        try:
            import subprocess
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=15)
            return float(r.stdout.strip()) if r.stdout.strip() else None
        except Exception:
            return None


# ============ 单例 ============
_manager: Optional[ProjectProductionManager] = None

def get_project_autopilot_manager() -> ProjectProductionManager:
    global _manager
    if _manager is None:
        _manager = ProjectProductionManager()
    return _manager
