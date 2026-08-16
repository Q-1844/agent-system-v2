# src/revision/source.py
# ProjectSourceRef + ProjectSourceStore（§六 / §七）。
# 第一版只统一 read：canonical 走现有业务 API，workspace 走 ChangeSet。
# 刻意不实现 source_store.save(ref, project)——canonical write 继续走现有业务 API，
# workspace write 必须经过 ChangeSet；未来 Agent 不能绕过 revision layer（§七）。
from __future__ import annotations
from typing import Callable

from .models import ProjectSourceRef, SourceKind
from ..core.models import Project


class ProjectSourceStore:
    """统一读取入口。canonical_loader(project_name)->Project；
    workspace_store_factory(project_name)->WorkspaceStore。由 router 注入，revision 不依赖 deps。"""

    def __init__(self,
                 canonical_loader: Callable[[str], "Project"],
                 workspace_store_factory: Callable[[str], "object"]):
        self._canonical_loader = canonical_loader
        self._workspace_store_factory = workspace_store_factory

    def load(self, ref: ProjectSourceRef) -> "Project":
        if ref.kind == SourceKind.CANONICAL.value:
            return self._canonical_loader(ref.project_name)
        if ref.kind == SourceKind.WORKSPACE.value:
            if not ref.workspace_id:
                raise ValueError("workspace source 需要 workspace_id")
            ws_store = self._workspace_store_factory(ref.project_name)
            return ws_store.load_working_project(ref.workspace_id)
        raise ValueError(f"unknown source kind: {ref.kind}")
