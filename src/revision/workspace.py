# src/revision/workspace.py
# Workspace 存储（§四 / §五 / §十一）。
# 目录：
#   projects/{project}/workspaces/{workspace_id}/
#       workspace.json          元数据 + per-scene shot_seq 计数器（§十一 stable ID）
#       base_project.json        创建时 canonical 快照，创建后永不修改（§四）
#       working_project.json     ChangeSet 作用对象（§二：改它 != 改 canonical project.json）
#       changesets/{csid}.json   ChangeSet 持久化
# 资产规则（§四）：只引用 Project model 的 local/url/version/metadata，绝不复制 PNG/MP4 本身。
from __future__ import annotations
import json
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Callable, List, Optional

from .models import ProjectWorkspace, creative_source_signature
from ..core.models import Project
from ..core.atomic import atomic_write_json


class WorkspaceStore:
    """单个项目绑定的 workspace 存储。canonical_loader() 零参 -> Project（router 注入）。"""

    def __init__(self, project_dir, project_name: str,
                 canonical_loader: Callable[[], "Project"]):
        self.project_dir = Path(project_dir)
        self.project_name = project_name
        self._canonical_loader = canonical_loader
        self.root = self.project_dir / "workspaces"

    # ---- 路径 ----
    def _ws_dir(self, wid: str) -> Path:
        return self.root / wid

    def _workspace_json(self, wid: str) -> Path:
        return self._ws_dir(wid) / "workspace.json"

    def _base_path(self, wid: str) -> Path:
        return self._ws_dir(wid) / "base_project.json"

    def _working_path(self, wid: str) -> Path:
        return self._ws_dir(wid) / "working_project.json"

    def _changesets_dir(self, wid: str) -> Path:
        return self._ws_dir(wid) / "changesets"

    def _changeset_path(self, wid: str, cid: str) -> Path:
        return self._changesets_dir(wid) / f"{cid}.json"

    # ---- create / load / list / archive / delete ----
    def create(self, name: str) -> ProjectWorkspace:
        wid = "ws_" + secrets.token_hex(6)
        wsdir = self._ws_dir(wid)
        wsdir.mkdir(parents=True, exist_ok=True)
        self._changesets_dir(wid).mkdir(exist_ok=True)
        canonical = self._canonical_loader()
        base_dict = canonical.to_dict()
        atomic_write_json(self._base_path(wid), base_dict)       # 不可变快照（§四）
        atomic_write_json(self._working_path(wid), base_dict)    # 初始 = base
        # shot_seq 初始化：每 scene = max(已用 c 号)+1。
        # 仅此处解析一次 shot_id 位置（只为初始化计数器），之后 stable ID 只读计数器、不重编号（§十一）。
        shot_seq = {}
        for sc in canonical.scenes:
            max_n = 0
            for sh in sc.shots:
                m = re.search(r"_c(\d+)$", sh.shot_id)
                if m:
                    max_n = max(max_n, int(m.group(1)))
            shot_seq[sc.scene_id] = max_n + 1
        sig = creative_source_signature(base_dict)
        ws = ProjectWorkspace(
            workspace_id=wid, project_name=self.project_name, name=name,
            revision=0, base_project_signature=sig, working_project_signature=sig,
            base_project_file="base_project.json", working_project_file="working_project.json",
            shot_seq=shot_seq,
        )
        self._save_workspace(ws)
        return ws

    def _save_workspace(self, ws: ProjectWorkspace) -> None:
        ws.updated_at = time.time()
        atomic_write_json(self._workspace_json(ws.workspace_id), ws.to_dict())

    def save_workspace(self, ws: ProjectWorkspace) -> None:
        """公共入口（changeset apply 需要写 workspace 元数据：revision + signature）。"""
        self._save_workspace(ws)

    def load(self, wid: str) -> ProjectWorkspace:
        p = self._workspace_json(wid)
        if not p.exists():
            raise FileNotFoundError(f"workspace 不存在: {wid}")
        return ProjectWorkspace.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def list(self) -> List[ProjectWorkspace]:
        if not self.root.exists():
            return []
        out = []
        for d in sorted(self.root.iterdir()):
            jf = d / "workspace.json"
            if jf.exists():
                out.append(ProjectWorkspace.from_dict(json.loads(jf.read_text(encoding="utf-8"))))
        return out

    def archive(self, wid: str) -> ProjectWorkspace:
        ws = self.load(wid)
        ws.status = "archived"
        ws.archived_at = time.time()
        self._save_workspace(ws)
        return ws

    def delete(self, wid: str) -> None:
        wsdir = self._ws_dir(wid)
        if wsdir.exists():
            shutil.rmtree(wsdir)

    # ---- working / base project ----
    def load_working_project(self, wid: str) -> "Project":
        p = self._working_path(wid)
        if not p.exists():
            raise FileNotFoundError(f"working_project 不存在: {wid}")
        return Project.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def load_base_project(self, wid: str) -> "Project":
        p = self._base_path(wid)
        if not p.exists():
            raise FileNotFoundError(f"base_project 不存在: {wid}")
        return Project.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def write_working_project_dict(self, wid: str, project_dict: dict) -> None:
        """低层：原子写 working_project.json。仅 ChangeSet apply 流程调用（§十四）。"""
        atomic_write_json(self._working_path(wid), project_dict)

    def working_signature(self, wid: str) -> str:
        """读 working_project.json 原始 dict 算 Creative Source 签名（§六十：排除资产/updated_at；
        §十四 reconciliation 用）。"""
        p = self._working_path(wid)
        return creative_source_signature(json.loads(p.read_text(encoding="utf-8")))

    # ---- stable shot id（§十一 重点难点）----
    def allocate_shot_id(self, ws: ProjectWorkspace, scene_id: str) -> str:
        """Preview 阶段分配最终稳定 shot_id；Apply 复用同一个（§十一）。
        计数器只增不回收、不重编号。Insert 时绝不 renumber 旧 Shot。"""
        n = ws.shot_seq.get(scene_id, 1)
        sid = f"{scene_id}_c{n:02d}"
        ws.shot_seq[scene_id] = n + 1
        self._save_workspace(ws)
        return sid

    # ---- base_is_stale（§五）----
    def base_is_stale(self, ws: ProjectWorkspace) -> bool:
        """canonical 当前签名 != base_project_signature -> stale。
        动态判断，不持久化；Workspace 仍可继续编辑，不自动 rebase（§五）。"""
        cur = creative_source_signature(self._canonical_loader().to_dict())
        return cur != ws.base_project_signature

    # ---- changeset 持久化（供 changeset.py 使用）----
    def save_changeset(self, wid: str, cs_dict: dict) -> None:
        cid = cs_dict.get("changeset_id")
        if not cid:
            raise ValueError("changeset_id 缺失")
        self._changesets_dir(wid).mkdir(exist_ok=True)
        atomic_write_json(self._changeset_path(wid, cid), cs_dict)

    def load_changeset(self, wid: str, cid: str) -> dict:
        p = self._changeset_path(wid, cid)
        if not p.exists():
            raise FileNotFoundError(f"changeset 不存在: {cid}")
        return json.loads(p.read_text(encoding="utf-8"))

    def list_changesets(self, wid: str) -> List[dict]:
        d = self._changesets_dir(wid)
        if not d.exists():
            return []
        out = []
        for f in sorted(d.glob("*.json")):
            out.append(json.loads(f.read_text(encoding="utf-8")))
        return out
