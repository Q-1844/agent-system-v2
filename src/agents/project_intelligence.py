# src/agents/project_intelligence.py
# AI-7A Project Intelligence —— 只读语义项目检视层（§6 / §14~§18 / §47 / §48）。
#
# 设计不变量：
#   - 纯读：只读 Project / production_plans / AgentTaskStore / WorkspaceStore，绝不写。
#     canonical project.json 不被本层触碰（§1 / §43 红线）——与现有 7 个 *.inspect adapter 同构。
#   - 有界：每次 inspect 返回 ProjectInspectResult，带 truncated / next_cursor，
#     绝不因「Xiaoyu 出现在哪」就倒出全部镜头全文（§9 / §15）。
#   - 派生自真源：ProjectIndex 只从 canonical / revision / production / asset 真源派生，
#     可缓存可重建；**不落盘**——进程重启不依赖陈旧索引文件（§13）。
#   - 只读能力集合 + assert_read_only_capability：AI-7A Producer/Team 运行路径上的显式硬闸
#     （§43 / §96）。mutation 能力一律 permission_denied。
#   - 不造历史：历史不存在的（Script/Character Pocket / Project Checkpoint）返回
#     history_available=false（§11 / §80），绝不伪造 "Script Revision S17"。
#
# 复用（不重写、不并行造第二套真相源）：
#   - creative_source_signature（src.revision.models）—— snapshot_signature / stale 检测。
#   - SceneProductionPlan / is_plan_stale（src.ai.production_planner）—— production_state_by_scene。
#     lazy import，避免本模块 import 时拉起 production_planner → story_workflow 链。
#   - Project 模型 helpers（all_shots / get_shot / get_character）（src.core.models）。
#   - 现有 7 个 *.inspect capability adapter 不被替换；本服务是 Producer 的更丰富只读层
#     （§6：公共 Agent 能力面可更小）。
#
# Import 纪律：本模块只 import src.core.* / src.revision.models / 本包 capabilities/models。
#   AgentError 用 lazy import（assert_read_only_capability 内）以避免与 runtime.py 的循环依赖
#   （runtime.py 反向 import 本模块的 ProjectIntelligenceService）。
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.models import Project, Scene, Shot
from src.revision.models import creative_source_signature

from .capabilities import build_default_capabilities
from .models import AgentOp


# ============ 只读能力集合 + 显式硬闸（§43 / §96）============
def _classify_capabilities() -> Tuple[set, set]:
    """从 capability registry 派生：inspect-only = 只读；含 execute/retry/cancel = mutation。
    自维护——未来加能力自动归类，不靠硬编码。"""
    read_only, mutation = set(), set()
    for c in build_default_capabilities():
        ops = set(c.operations)
        if (AgentOp.EXECUTE.value in ops or AgentOp.RETRY.value in ops
                or AgentOp.CANCEL.value in ops):
            mutation.add(c.id)
        if AgentOp.INSPECT.value in ops and AgentOp.EXECUTE.value not in ops:
            read_only.add(c.id)
    return read_only, mutation


READ_ONLY_CAPABILITIES, MUTATION_CAPABILITIES = _classify_capabilities()


def is_read_only_capability(capability_id: str) -> bool:
    """是否为 AI-7A 允许在只读运行路径上调用的能力。"""
    return (capability_id in READ_ONLY_CAPABILITIES
            and capability_id not in MUTATION_CAPABILITIES)


def assert_read_only_capability(capability_id: str) -> None:
    """AI-7A Producer/Team 运行路径上的显式只读硬闸（§43 / §96）。
    mutation 能力（story.edit / character.design / storyboard.propose_change /
    production_plan.create / shot.first_frame|last_frame|video / composition.compose）
    一律 AgentError(permission_denied)。lazy import AgentError 避免与 runtime 循环依赖。"""
    if not is_read_only_capability(capability_id):
        from .runtime import AgentError   # lazy：避免 import cycle
        raise AgentError(
            "permission_denied",
            f"AI-7A 只读运行路径禁止 mutation 能力: {capability_id}")


# ============ 证据引用（§10）============
@dataclass
class ProjectEvidenceRef:
    """稳定证据引用——Producer 答案可回溯到项目实体（§10）。内部结构化元数据，
    最终自然语言答案可保持简单。"""
    module: str            # script/storyboard/character/production/asset/workspace/scene/shot/agent
    entity_type: str      # scene/shot/character/plan/video_version/changeset/task/...
    entity_id: str
    revision_id: Optional[str] = None
    artifact_type: Optional[str] = None
    artifact_id: Optional[str] = None
    label: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict = {"module": self.module, "entity_type": self.entity_type,
                   "entity_id": self.entity_id}
        if self.revision_id:
            d["revision_id"] = self.revision_id
        if self.artifact_type:
            d["artifact_type"] = self.artifact_type
        if self.artifact_id:
            d["artifact_id"] = self.artifact_id
        if self.label:
            d["label"] = self.label
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectEvidenceRef":
        d = d or {}
        return cls(module=d.get("module", ""), entity_type=d.get("entity_type", ""),
                   entity_id=d.get("entity_id", ""),
                   revision_id=d.get("revision_id"),
                   artifact_type=d.get("artifact_type"),
                   artifact_id=d.get("artifact_id"), label=d.get("label"))


def _eref(module: str, etype: str, eid: str, **kw) -> ProjectEvidenceRef:
    return ProjectEvidenceRef(module=module, entity_type=etype, entity_id=eid, **kw)


# ============ 有界检视结果（§9）============
@dataclass
class ProjectInspectResult:
    """每次 inspect 的有界结果。data 仅含当前视图需要的内容；超限 truncated=true + next_cursor。"""
    project_name: str
    view: str
    summary: str
    data: dict = field(default_factory=dict)
    evidence_refs: List[ProjectEvidenceRef] = field(default_factory=list)
    truncated: bool = False
    next_cursor: Optional[str] = None
    snapshot_signature: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name, "view": self.view,
            "summary": self.summary, "data": self.data,
            "evidence_refs": [r.to_dict() for r in self.evidence_refs],
            "truncated": self.truncated, "next_cursor": self.next_cursor,
            "snapshot_signature": self.snapshot_signature,
        }


# ============ 轻量项目索引（§12 / §13）============
@dataclass
class ProjectIndex:
    """ID + 计数 + 关系 + 轻元数据。**不含** full prompts / image binaries / video bytes /
    完整 Agent 历史（§12）。内存缓存、按签名失效、可安全重建（§13）。"""
    project_name: str
    character_to_scenes: Dict[str, List[str]] = field(default_factory=dict)
    character_to_shots: Dict[str, List[str]] = field(default_factory=dict)
    scene_to_shots: Dict[str, List[str]] = field(default_factory=dict)
    shot_to_characters: Dict[str, List[str]] = field(default_factory=dict)
    scene_order: List[str] = field(default_factory=list)
    shot_count_by_scene: Dict[str, int] = field(default_factory=dict)
    production_state_by_scene: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "character_to_scenes": {k: list(v) for k, v in self.character_to_scenes.items()},
            "character_to_shots": {k: list(v) for k, v in self.character_to_shots.items()},
            "scene_to_shots": {k: list(v) for k, v in self.scene_to_shots.items()},
            "shot_to_characters": {k: list(v) for k, v in self.shot_to_characters.items()},
            "scene_order": list(self.scene_order),
            "shot_count_by_scene": dict(self.shot_count_by_scene),
            "production_state_by_scene": dict(self.production_state_by_scene),
        }


# ============ 依赖边（§48）============
@dataclass
class DependencyEdge:
    """项目特定依赖关系。只加仓库能证明的 relation（§48）。"""
    source: ProjectEvidenceRef
    target: ProjectEvidenceRef
    relation: str

    def to_dict(self) -> dict:
        return {"source": self.source.to_dict(), "target": self.target.to_dict(),
                "relation": self.relation}


# 索引内存缓存：{project_name: (creative_source_signature, ProjectIndex)}
# §13：仅缓存、不落盘；签名变即重建；进程重启不依赖陈旧索引文件。
_INDEX_CACHE: Dict[str, Tuple[str, ProjectIndex]] = {}


def invalidate_index_cache(project_name: Optional[str] = None) -> None:
    """测试 / project 变更后清缓存。不传则清全部。"""
    if project_name is None:
        _INDEX_CACHE.clear()
    else:
        _INDEX_CACHE.pop(project_name, None)


# ============ ProjectIntelligenceService（§6 / §72）============
class ProjectIntelligenceService:
    """语义只读项目检视。Producer 的询问运行时（AI-7A 7A-2）据此增量检索上下文，
    不把整项目塞进每次 prompt（§5）。所有 callable 由 composition root 注入，测试可注入 lambda。"""

    def __init__(self, *,
                 project_name: str,
                 project_loader: Callable[[], Project],
                 plans_reader: Optional[Callable[[str, str], Optional[dict]]] = None,
                 task_store_getter: Optional[Callable[[], Any]] = None,
                 workspace_store_factory: Optional[Callable[[], Any]] = None):
        self.project_name = project_name
        self._project_loader = project_loader
        self._plans_reader = plans_reader
        self._task_store_getter = task_store_getter
        self._workspace_store_factory = workspace_store_factory

    # ---------- 基础 ----------
    def _project(self) -> Project:
        return self._project_loader()

    def _signature(self, project: Optional[Project] = None) -> str:
        return creative_source_signature((project or self._project()).to_dict())

    def _result(self, view: str, summary: str, data: dict,
                evidence_refs: Optional[List[ProjectEvidenceRef]] = None,
                truncated: bool = False, next_cursor: Optional[str] = None,
                project: Optional[Project] = None) -> ProjectInspectResult:
        return ProjectInspectResult(
            project_name=self.project_name, view=view, summary=summary, data=data,
            evidence_refs=evidence_refs or [], truncated=truncated,
            next_cursor=next_cursor, snapshot_signature=self._signature(project))

    @staticmethod
    def _page(items: list, limit: int, offset: int = 0) -> Tuple[list, bool, Optional[str]]:
        """有界切片：返回 (page, truncated, next_cursor)。"""
        page = items[offset:offset + limit]
        truncated = len(items) > offset + limit
        next_cursor = str(offset + limit) if truncated else None
        return page, truncated, next_cursor

    # ---------- 统一入口（§8）----------
    def inspect(self, view: str, *,
                entity_id: Optional[str] = None,
                scene_id: Optional[str] = None,
                shot_id: Optional[str] = None,
                character_id: Optional[str] = None,
                include_history: bool = False,
                limit: int = 50, offset: int = 0) -> ProjectInspectResult:
        """语义检视统一入口。view ∈ overview/script/characters/character_usage/
        storyboard/scene/shot/history/production/lineage/dependencies。"""
        try:
            lim = max(1, min(200, int(limit)))
        except (TypeError, ValueError):
            lim = 50
        if view == "overview":
            return self.overview(limit=lim)
        if view == "script":
            return self.script(limit=lim)
        if view == "characters":
            return self.characters(limit=lim)
        if view == "character_usage":
            return self.character_usage(character_id or entity_id or "", limit=lim)
        if view == "storyboard":
            return self.storyboard_index(limit=lim)
        if view == "scene":
            return self.scene(scene_id or entity_id or "", limit=lim, offset=offset)
        if view == "shot":
            return self.shot(shot_id or entity_id or "")
        if view == "history":
            return self.history(entity_id=entity_id, include_history=include_history, limit=lim)
        if view == "production":
            return self.production(limit=lim)
        if view == "lineage":
            return self.lineage(shot_id or entity_id or "")
        if view == "dependencies":
            return self.dependencies(entity_type=entity_id or "", entity_id=scene_id or shot_id or "")
        return self._result(view, f"未知视图: {view}",
                            {"view": view, "error": "unknown_view"})

    # ---------- overview（§14）----------
    def overview(self, limit: int = 50) -> ProjectInspectResult:
        p = self._project()
        scene_n, shot_n, char_n = len(p.scenes), len(p.all_shots()), len(p.characters)
        empty_scenes = [s.scene_id for s in p.scenes if not s.shots]
        scenes_with_shots = scene_n - len(empty_scenes)
        # production
        prod = self._production_aggregates(p)
        data = {
            "project_name": p.name, "story_present": bool(p.story_text),
            "characters": char_n, "scenes": scene_n, "shots": shot_n,
            "storyboard": {"scenes_with_shots": scenes_with_shots,
                           "empty_scenes": empty_scenes[:limit]},
            "production": prod,
        }
        # workspaces（仅当注入了 store；§14：只用存在的数据）
        ws_info = self._workspace_overview()
        if ws_info is not None:
            data["workspaces"] = ws_info
        summary = (f"项目「{p.name}」：{scene_n} 场景 / {shot_n} 镜头 / {char_n} 角色；"
                   f"已批准计划 {prod['approved_plans']}，有视频 {prod['shots_with_video']} 镜。")
        refs = [_eref("project", "project", p.name, label="canonical source")]
        refs += [_eref("scene", "scene", s.scene_id) for s in p.scenes[:limit]]
        return self._result("overview", summary, data, refs, project=p)

    # ---------- script（§4 故事问题）----------
    def script(self, limit: int = 50) -> ProjectInspectResult:
        p = self._project()
        scenes, trunc, cur = self._page(
            [{"scene_id": s.scene_id, "summary": (s.summary or "")[:120],
              "characters": list(s.characters), "shot_count": len(s.shots)}
             for s in p.scenes], limit)
        data = {"story_present": bool(p.story_text),
                "story_text_head": (p.story_text or "")[:2000],
                "scenes": scenes}
        summary = f"故事文本 {len(p.story_text or '')} 字；{len(p.scenes)} 个场景。"
        refs = [_eref("script", "scene", s["scene_id"]) for s in scenes]
        return self._result("script", summary, data, refs, trunc, cur, p)

    # ---------- characters ----------
    def characters(self, limit: int = 50) -> ProjectInspectResult:
        p = self._project()
        chars, trunc, cur = self._page(
            [{"name": c.name, "description_en": (c.description_en or "")[:120],
              "has_views": {v: bool(c.views.get(v) and (c.views[v].local or c.views[v].url))
                            for v in ("front", "side", "back")},
              "related_scene_count": sum(1 for s in p.scenes if c.name in s.characters)}
             for c in p.characters], limit)
        summary = f"{len(p.characters)} 个角色；风格 {p.style.suffix()[:60]}。"
        refs = [_eref("character", "character", c["name"]) for c in chars]
        return self._result("characters", summary, {"characters": chars},
                            refs, trunc, cur, p)

    # ---------- character_usage（§16，关键能力）----------
    def character_usage(self, character_ref: str, limit: int = 50) -> ProjectInspectResult:
        p = self._project()
        name = self._resolve_character_name(p, character_ref)
        if name is None:
            return self._result(
                "character_usage", f"未找到角色: {character_ref}",
                {"character": character_ref, "found": False,
                 "history_available": False}, project=p)
        scene_ids: List[str] = []
        shot_ids: List[str] = []
        per_scene: List[dict] = []
        for s in p.scenes:
            s_shots = [sh.shot_id for sh in s.shots if name in sh.characters]
            if name in s.characters or s_shots:
                scene_ids.append(s.scene_id)
                per_scene.append({"scene_id": s.scene_id, "shot_count": len(s_shots)})
            shot_ids.extend(s_shots)
        first_scene = scene_ids[0] if scene_ids else None
        last_scene = scene_ids[-1] if scene_ids else None
        # 分页场景列表（§16：bounded scene_ids + more）
        page, trunc, cur = self._page(per_scene, limit)
        data = {"character": name, "found": True,
                "scene_count": len(scene_ids), "shot_count": len(shot_ids),
                "first_scene": first_scene, "last_scene": last_scene,
                "scenes": page}
        summary = (f"角色「{name}」出现在 {len(scene_ids)} 个场景 / {len(shot_ids)} 个镜头"
                   f"（{first_scene} → {last_scene}）。")
        refs = [_eref("character", "character", name)]
        refs += [_eref("storyboard", "scene", sid) for sid in scene_ids[:limit]]
        return self._result("character_usage", summary, data, refs, trunc, cur, p)

    # ---------- storyboard_index（§15）----------
    def storyboard_index(self, limit: int = 50) -> ProjectInspectResult:
        p = self._project()
        rows = [{"scene_id": s.scene_id, "title": (s.title or "")[:40],
                 "shot_count": len(s.shots), "characters": list(s.characters)}
                for s in p.scenes]
        page, trunc, cur = self._page(rows, limit)
        summary = f"{len(p.scenes)} 个场景，共 {len(p.all_shots())} 个镜头。"
        refs = [_eref("storyboard", "scene", r["scene_id"]) for r in page]
        return self._result("storyboard_index", summary, {"scenes": page},
                            refs, trunc, cur, p)

    # ---------- scene（§15：单场景才回镜头明细）----------
    def scene(self, scene_id: str, limit: int = 50, offset: int = 0) -> ProjectInspectResult:
        p = self._project()
        scene = next((s for s in p.scenes if s.scene_id == scene_id), None)
        if scene is None:
            return self._result("scene", f"未找到场景: {scene_id}",
                                {"scene_id": scene_id, "found": False}, project=p)
        shots = [{"shot_id": sh.shot_id, "description": (sh.description or "")[:80],
                  "characters": list(sh.characters), "status": sh.status,
                  "target_duration_s": sh.target_duration_s,
                  "has_first_frame": self._shot_has_first_frame(sh),
                  "has_video": self._shot_has_video(sh)}
                 for sh in scene.shots]
        page, trunc, cur = self._page(shots, limit, offset)
        summary = (f"场景 {scene.scene_id}「{scene.title or (scene.summary or '')[:24]}」"
                   f"含 {len(scene.shots)} 个镜头。")
        refs = [_eref("storyboard", "scene", scene.scene_id)]
        refs += [_eref("storyboard", "shot", sh["shot_id"]) for sh in page]
        return self._result("scene", summary,
                            {"scene_id": scene.scene_id, "summary": (scene.summary or "")[:200],
                             "characters": list(scene.characters), "shots": page},
                            refs, trunc, cur, p)

    # ---------- shot ----------
    def shot(self, shot_id: str) -> ProjectInspectResult:
        p = self._project()
        sh = p.get_shot(shot_id)
        if sh is None:
            return self._result("shot", f"未找到镜头: {shot_id}",
                                {"shot_id": shot_id, "found": False}, project=p)
        data = {
            "shot_id": sh.shot_id, "scene_id": sh.scene_id,
            "description": sh.description, "characters": list(sh.characters),
            "status": sh.status, "link_mode": sh.link_mode,
            "opening_source": sh.opening_source, "source_shot_id": sh.source_shot_id,
            "target_duration_s": sh.target_duration_s,
            "first_frame_status": sh.first_frame.status,
            "last_frame_status": sh.last_frame.status,
            "video_status": sh.video.status, "audio_status": sh.audio.status,
            "selected_first_frame": sh.selected_first_frame,
            "final_video": sh.final_video,
            "first_frame_batch_count": len(sh.first_frame_batches),
            "video_version_count": len(sh.video_versions),
        }
        summary = (f"镜头 {sh.shot_id}：{(sh.description or '')[:60]}；"
                   f"首帧 {sh.first_frame.status}、视频 {sh.video.status}。")
        refs = [_eref("storyboard", "shot", sh.shot_id),
                _eref("storyboard", "scene", sh.scene_id)]
        return self._result("shot", summary, data, refs, project=p)

    # ---------- production（§17）----------
    def production(self, limit: int = 50) -> ProjectInspectResult:
        p = self._project()
        agg = self._production_aggregates(p)
        per_scene = agg.pop("per_scene", [])
        page, trunc, cur = self._page(per_scene, limit)
        # running AgentTasks（只读 task store；§17：which tasks are currently running）
        running = self._running_tasks_summary()
        data = {**agg, "per_scene": page, "running_tasks": running}
        summary = (f"已批准 {agg['approved_plans']} / 草稿 {agg['draft_plans']} / "
                   f"过期 {agg['stale_plans']} / 无计划 {agg['no_plan_scenes']} 场景；"
                   f"有首帧 {agg['shots_with_first_frame']} / 有视频 {agg['shots_with_video']} 镜。")
        refs = [_eref("production", "scene", r["scene_id"]) for r in page]
        return self._result("production", summary, data, refs, trunc, cur, p)

    # ---------- lineage（§18）----------
    def lineage(self, shot_id: str) -> ProjectInspectResult:
        p = self._project()
        sh = p.get_shot(shot_id)
        if sh is None:
            return self._result("lineage", f"未找到镜头: {shot_id}",
                                {"shot_id": shot_id, "found": False}, project=p)
        # shot → selected FirstFrame（candidate_id → batch → candidate）
        ff = self._resolve_selected_first_frame(sh)
        # shot → final VideoVersion
        vv = self._resolve_final_video(sh)
        # AgentTask → durable 执行链接（execution_ref；§18：用确切 id，不猜「大概是最新」）
        tasks = self._tasks_for_shot(shot_id)
        data = {"shot_id": sh.shot_id, "scene_id": sh.scene_id,
                "selected_first_frame": ff, "final_video_version": vv,
                "related_agent_tasks": tasks}
        summary = (f"镜头 {sh.shot_id} 血缘：首帧 {'已选 ' + (ff['candidate_id'] if ff else '无')}，"
                   f"最终视频 {'已定 ' + (vv['version_id'] if vv else '无')}，"
                   f"关联任务 {len(tasks)}。")
        refs = [_eref("storyboard", "shot", sh.shot_id)]
        if vv:
            refs.append(_eref("asset", "video_version", vv["version_id"]))
        return self._result("lineage", summary, data, refs, project=p)

    # ---------- dependencies（§48）----------
    def dependencies(self, entity_type: str, entity_id: str,
                      limit: int = 50) -> ProjectInspectResult:
        p = self._project()
        edges: List[DependencyEdge] = []
        et = (entity_type or "").lower()
        if et in ("character", ""):
            name = self._resolve_character_name(p, entity_id) if entity_id else None
            if name:
                for s in p.scenes:
                    if name in s.characters:
                        edges.append(DependencyEdge(
                            _eref("character", "character", name),
                            _eref("storyboard", "scene", s.scene_id),
                            "character_used_by_scene"))
                for sh in p.all_shots():
                    if name in sh.characters:
                        edges.append(DependencyEdge(
                            _eref("character", "character", name),
                            _eref("storyboard", "shot", sh.shot_id),
                            "character_used_by_shot"))
        elif et == "scene":
            scene = next((s for s in p.scenes if s.scene_id == entity_id), None)
            if scene:
                for sh in scene.shots:
                    edges.append(DependencyEdge(
                        _eref("storyboard", "scene", scene.scene_id),
                        _eref("storyboard", "shot", sh.shot_id),
                        "scene_contains_shot"))
                if self._scene_has_plan(scene.scene_id):
                    edges.append(DependencyEdge(
                        _eref("storyboard", "scene", scene.scene_id),
                        _eref("production", "plan", scene.scene_id),
                        "shot_covered_by_production_plan"))
        elif et == "shot":
            sh = p.get_shot(entity_id)
            if sh:
                if sh.source_shot_id:
                    edges.append(DependencyEdge(
                        _eref("storyboard", "shot", sh.shot_id),
                        _eref("storyboard", "shot", sh.source_shot_id),
                        "shot_depends_on_previous_end_frame"))
                if self._shot_has_video(sh):
                    edges.append(DependencyEdge(
                        _eref("storyboard", "shot", sh.shot_id),
                        _eref("asset", "video_version", sh.final_video or ""),
                        "video_generated_from_shot"))
                if self._scene_has_plan(sh.scene_id):
                    edges.append(DependencyEdge(
                        _eref("storyboard", "shot", sh.shot_id),
                        _eref("production", "plan", sh.scene_id),
                        "shot_covered_by_production_plan"))
        page, trunc, cur = self._page(edges, limit)
        data = {"entity_type": et or "character", "entity_id": entity_id,
                "edges": [e.to_dict() for e in page], "edge_count": len(edges)}
        summary = f"{len(edges)} 条依赖关系（{et or 'character'}: {entity_id}）。"
        return self._result("dependencies", summary, data, [], trunc, cur, p)

    # ---------- history（§11 / §80：不造假）----------
    def history(self, entity_id: Optional[str] = None,
                include_history: bool = False, limit: int = 50) -> ProjectInspectResult:
        p = self._project()
        vv_n = sum(len(sh.video_versions) for sh in p.all_shots())
        ff_n = sum(len(sh.first_frame_batches) + len(sh.last_frame_batches)
                   for sh in p.all_shots())
        plan_n = sum(1 for s in p.scenes if self._plans_reader and self._plans_reader(self.project_name, s.scene_id))
        task_n = self._task_history_count()
        ws_n, cs_n = self._workspace_history_counts()
        data = {
            "history_available": {
                "creative_workspace_revisions": ws_n > 0,
                "changeset_history": cs_n > 0,
                "image_batch_history": ff_n > 0,
                "video_version_history": vv_n > 0,
                "production_plan_history": plan_n > 0,
                "agent_task_history": task_n > 0,
                # §11 / §80：尚未实现的历史——绝不伪造
                "script_pocket": False,
                "character_pocket": False,
                "project_checkpoint": False,
            },
            "counts": {"video_versions": vv_n, "image_batches": ff_n,
                       "plans": plan_n, "agent_tasks": task_n,
                       "workspaces": ws_n, "changesets": cs_n},
        }
        summary = (f"历史：视频版本 {vv_n} / 首尾帧批次 {ff_n} / 计划 {plan_n} / "
                   f"AgentTask {task_n}。Script/Character Pocket 尚未实现。")
        return self._result("history", summary, data, [], project=p)

    # ---------- ProjectIndex（§12 / §13）----------
    def build_index(self) -> ProjectIndex:
        """构建/取缓存索引。按 creative_source_signature 缓存：签名未变复用，变了重建。
        仅内存缓存、不落盘（§13：进程重启不依赖陈旧索引文件）。"""
        p = self._project()
        sig = self._signature(p)
        cached = _INDEX_CACHE.get(self.project_name)
        if cached and cached[0] == sig:
            return cached[1]
        idx = self._build_index_from_project(p)
        _INDEX_CACHE[self.project_name] = (sig, idx)
        return idx

    def _build_index_from_project(self, p: Project) -> ProjectIndex:
        c2s: Dict[str, List[str]] = {}
        c2sh: Dict[str, List[str]] = {}
        s2sh: Dict[str, List[str]] = {}
        sh2c: Dict[str, List[str]] = {}
        order: List[str] = [s.scene_id for s in p.scenes]
        cnt: Dict[str, int] = {}
        state: Dict[str, str] = {}
        for s in p.scenes:
            cnt[s.scene_id] = len(s.shots)
            s2sh[s.scene_id] = [sh.shot_id for sh in s.shots]
            state[s.scene_id] = self._scene_plan_state(p, s)
            for sh in s.shots:
                sh2c[sh.shot_id] = list(sh.characters)
                for cn in sh.characters:
                    c2sh.setdefault(cn, []).append(sh.shot_id)
            for cn in s.characters:
                c2s.setdefault(cn, []).append(s.scene_id)
        return ProjectIndex(
            project_name=self.project_name, character_to_scenes=c2s,
            character_to_shots=c2sh, scene_to_shots=s2sh, shot_to_characters=sh2c,
            scene_order=order, shot_count_by_scene=cnt, production_state_by_scene=state)

    # ============ 私有 helpers ============
    @staticmethod
    def _resolve_character_name(p: Project, ref: str) -> Optional[str]:
        if not ref:
            return None
        exact = next((c.name for c in p.characters if c.name == ref), None)
        if exact:
            return exact
        # 大小写兜底（项目角色名一般唯一）
        low = ref.lower()
        return next((c.name for c in p.characters if c.name.lower() == low), None)

    @staticmethod
    def _shot_has_first_frame(sh: Shot) -> bool:
        return bool(sh.selected_first_frame) or sh.first_frame.ready

    @staticmethod
    def _shot_has_video(sh: Shot) -> bool:
        return bool(sh.final_video) or sh.video.status == "done"

    def _resolve_selected_first_frame(self, sh: Shot) -> Optional[dict]:
        cid = sh.selected_first_frame
        if cid:
            for b in sh.first_frame_batches:
                for c in b.candidates:
                    if c.candidate_id == cid:
                        return {"candidate_id": cid, "batch_id": b.batch_id,
                                "status": c.status, "has_local": bool(c.local),
                                "has_url": bool(c.url)}
        # legacy：first_frame.selected
        sel = sh.first_frame.selected
        if sel:
            return {"candidate_id": "legacy", "batch_id": "legacy",
                    "status": sel.status, "has_local": bool(sel.local),
                    "has_url": bool(sel.url)}
        return None

    def _resolve_final_video(self, sh: Shot) -> Optional[dict]:
        vid = sh.final_video
        if vid:
            for v in sh.video_versions:
                if v.version_id == vid:
                    return {"version_id": v.version_id, "status": v.status,
                            "video_id": v.video_id, "task_id": v.task_id,
                            "has_local": bool(v.local), "has_url": bool(v.url),
                            "first_frame_candidate_id": v.first_frame_candidate_id}
        # legacy：video Asset
        if sh.video.status == "done" and (sh.video.local or sh.video.video_id):
            return {"version_id": "legacy", "status": sh.video.status,
                    "video_id": sh.video.video_id, "task_id": sh.video.task_id,
                    "has_local": bool(sh.video.local), "has_url": bool(sh.video.url),
                    "first_frame_candidate_id": None}
        return None

    # ---- production helpers ----
    def _scene_plan_state(self, p: Project, s: Scene) -> str:
        """none / draft / approved / stale。stale 派生自 is_plan_stale（§8 不持久化）。"""
        if not self._plans_reader:
            return "none"
        plan_dict = self._plans_reader(self.project_name, s.scene_id)
        if not plan_dict:
            return "none"
        try:
            from src.ai.production_planner import SceneProductionPlan, is_plan_stale
            plan = SceneProductionPlan.model_validate(plan_dict)
            if is_plan_stale(plan, s):
                return "stale"
            return plan.status or "draft"
        except Exception:
            return plan_dict.get("status", "draft")

    def _scene_has_plan(self, scene_id: str) -> bool:
        if not self._plans_reader:
            return False
        return bool(self._plans_reader(self.project_name, scene_id))

    def _production_aggregates(self, p: Project) -> dict:
        per_scene = []
        approved = draft = stale = no_plan = 0
        for s in p.scenes:
            st = self._scene_plan_state(p, s)
            per_scene.append({"scene_id": s.scene_id, "plan_state": st,
                              "shot_count": len(s.shots)})
            if st == "approved":
                approved += 1
            elif st == "stale":
                stale += 1
            elif st == "draft":
                draft += 1
            else:
                no_plan += 1
        shots = p.all_shots()
        ff = sum(1 for sh in shots if self._shot_has_first_frame(sh))
        vid = sum(1 for sh in shots if self._shot_has_video(sh))
        return {"approved_plans": approved, "draft_plans": draft,
                "stale_plans": stale, "no_plan_scenes": no_plan,
                "shots_with_first_frame": ff, "shots_with_video": vid,
                "per_scene": per_scene}

    # ---- task store helpers（只读）----
    def _task_store(self):
        if self._task_store_getter is None:
            return None
        try:
            return self._task_store_getter()
        except Exception:
            return None

    def _running_tasks_summary(self) -> list:
        store = self._task_store()
        if store is None:
            return []
        try:
            tasks = store.list(status="running")
        except Exception:
            tasks = []
        return [{"task_id": t.task_id, "target_agent": t.target_agent,
                 "capability_id": t.capability_id, "scope_id": t.scope_id,
                 "status": t.status}
                for t in tasks[:50]]

    def _tasks_for_shot(self, shot_id: str) -> list:
        store = self._task_store()
        if store is None:
            return []
        try:
            tasks = store.list()
        except Exception:
            tasks = []
        out = []
        for t in tasks:
            if t.scope_id == shot_id:
                out.append({"task_id": t.task_id, "target_agent": t.target_agent,
                            "capability_id": t.capability_id, "status": t.status,
                            "execution_ref": t.execution_ref})
        return out[:50]

    def _task_history_count(self) -> int:
        store = self._task_store()
        if store is None:
            return 0
        try:
            return len(store.list())
        except Exception:
            return 0

    # ---- workspace helpers（只读；§14 / §11 / §48）----
    def _workspace_store(self):
        if self._workspace_store_factory is None:
            return None
        try:
            return self._workspace_store_factory()
        except Exception:
            return None

    def _workspace_overview(self) -> Optional[dict]:
        ws = self._workspace_store()
        if ws is None:
            return None
        try:
            all_ws = ws.list()
        except Exception:
            return None
        active = [w for w in all_ws if getattr(w, "status", None) == "active"]
        return {"active": len(active), "total": len(all_ws)}

    def _workspace_history_counts(self) -> Tuple[int, int]:
        ws = self._workspace_store()
        if ws is None:
            return 0, 0
        try:
            all_ws = ws.list()
        except Exception:
            return 0, 0
        cs_n = 0
        for w in all_ws:
            try:
                cs_n += len(ws.list_changesets(w.workspace_id))
            except Exception:
                pass
        return len(all_ws), cs_n
