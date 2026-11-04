# src/ai/project_production_inspect.py
"""只读 ProjectProductionInspect adapter（PR A）。

复用 FullProjectManager 侧的纯代码 inspect_project（§二十七/§二十八，
不调 AI、不写盘），叠加为 producer 规划契约的 ProjectSnapshot 投影。

纪律：
- 绝不写盘、绝不触达付费操作、绝不新建 canonical —— 只是读；
- 项目不存在 / 存储缺失时 fail closed（返回带 blockers 的快照，不抛异常、
  不臆造状态）；
- 证据字段与 inspect_project 的分类语义一一对应，不二次解释。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.ai.full_project_autopilot import inspect_project
from src.core.store import ProjectStore

BLOCKER_PROJECT_DIR_MISSING = "PROJECT_DIR_MISSING"
BLOCKER_PROJECT_STORE_MISSING = "PROJECT_STORE_MISSING"


@dataclass(frozen=True)
class SceneSummary:
    """单场只读摘要（分类语义来自 inspect_project，原样保留）。"""

    scene_id: str
    summary: str
    state: str
    shot_count: int
    characters: List[str] = field(default_factory=list)
    has_valid_director_draft: Optional[bool] = None
    plan_state: Optional[str] = None
    detail: Optional[str] = None

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "scene_id": self.scene_id, "summary": self.summary,
            "state": self.state, "shot_count": self.shot_count,
            "characters": list(self.characters),
            "has_valid_director_draft": self.has_valid_director_draft,
            "plan_state": self.plan_state, "detail": self.detail,
        }


@dataclass(frozen=True)
class ProjectSnapshot:
    """规划决策用的项目状态只读投影。"""

    project_name: str
    exists: bool
    ready: bool = False
    scene_count: int = 0
    shot_count: int = 0
    scenes: List[SceneSummary] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_evidence(self) -> Dict[str, Any]:
        return {
            "project_name": self.project_name, "exists": self.exists,
            "ready": self.ready, "scene_count": self.scene_count,
            "shot_count": self.shot_count,
            "scenes": [s.to_evidence() for s in self.scenes],
            "blockers": list(self.blockers), "warnings": list(self.warnings),
        }


class ProjectProductionInspect:
    """只读检视入口：给 Producer 规划前回答"这个项目现在到底什么样"。

    本类没有任何写方法（物理只读）。所有异常都被收敛为
    fail-closed 的 blockers 快照。
    """

    def __init__(self, project_name: str, project_dir: Path):
        object.__setattr__(self, "project_name", project_name)
        object.__setattr__(self, "project_dir", Path(project_dir))
        object.__setattr__(self, "_frozen", True)

    # ---- 只读快照 ----

    def snapshot(self) -> ProjectSnapshot:
        if not self.project_dir.is_dir():
            return ProjectSnapshot(
                project_name=self.project_name, exists=False,
                blockers=[BLOCKER_PROJECT_DIR_MISSING])
        try:
            project = ProjectStore(self.project_dir).load()
        except FileNotFoundError:
            # 目录在但项目数据未落盘：同样视为"项目不存在"（fail closed），
            # blockers 保留 STORE 与 DIR 两种原因的区分。
            return ProjectSnapshot(
                project_name=self.project_name, exists=False,
                blockers=[BLOCKER_PROJECT_STORE_MISSING])
        inspected = inspect_project(project, self.project_dir,
                                     self.project_name)
        scenes: List[SceneSummary] = []
        by_id = {sc.scene_id: sc for sc in project.scenes}
        for rec in inspected.scenes:
            sc = by_id.get(rec.scene_id)
            scenes.append(SceneSummary(
                scene_id=rec.scene_id,
                summary=getattr(sc, "summary", "") if sc is not None else "",
                state=rec.state,
                shot_count=rec.shot_count,
                characters=list(getattr(sc, "characters", []))
                if sc is not None else [],
                has_valid_director_draft=rec.has_valid_director_draft,
                plan_state=rec.plan_state,
                detail=rec.detail,
            ))
        return ProjectSnapshot(
            project_name=self.project_name, exists=True,
            ready=bool(inspected.ready),
            scene_count=inspected.scene_count,
            shot_count=inspected.shot_count,
            scenes=scenes,
            blockers=list(inspected.blockers),
            warnings=list(inspected.warnings),
        )

    # 明确声明：无写接口（文档性守卫，防运行期改字段；init 用 object.__setattr__）
    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError(
                "ProjectProductionInspect 是只读 adapter，禁止运行期改字段")
        super().__setattr__(name, value)
