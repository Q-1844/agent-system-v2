"""AI-6A Creative Workspace / ChangeSet 路由（§二十~§二十二）。
修改 Workspace != 修改 canonical project（§二）。前端绝不允许 PUT working_project.json——
所有写都必须经 POST /changeset/preview -> /apply。
"""
import asyncio
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import deps
from deps import get_project_manager
from src.core.store import ProjectStore
from src.revision.workspace import WorkspaceStore
from src.revision import changeset as cs_engine
from src.revision.changeset import ChangeSetError
from src.revision.models import ChangeOperation
from src.revision.diff import workspace_aggregate_diff
from src.revision.impact import compute_aggregate_impact

router = APIRouter()


# ChangeSetError.code -> HTTP 状态
_HTTP = {
    "WORKSPACE_CHANGED": 409, "STALE": 409, "CONFLICT": 409, "BEFORE_MISMATCH": 409,
    "BAD_STATUS": 409, "ALREADY_APPLIED": 409,
    "SCHEMA": 400, "DUP_SHOT_ID": 400, "REORDER_MISMATCH": 400,
    "CHAR_REF_INVALID": 400, "INVALID_DURATION": 400,
    "NOT_FOUND": 404,
}


def _raise(e: ChangeSetError):
    raise HTTPException(_HTTP.get(e.code, 400), f"[{e.code}] {e.message}")


def _store(project_name: str) -> WorkspaceStore:
    """每个请求构建一个 project 绑定的 WorkspaceStore。"""
    pd = get_project_manager().get_project_dir(project_name)
    return WorkspaceStore(pd, project_name, lambda: ProjectStore(pd).load())


# §三十：每个 workspace 一个 asyncio.Lock，避免 preview/apply/delete 同时写
_LOCKS: dict = {}


def workspace_lock(project_name: str, workspace_id: str) -> asyncio.Lock:
    """每个 workspace 一把 asyncio.Lock（§三十）。导出供 director proposal 路由复用同一锁对象，
    避免 preview/apply/delete 与 director 的 preview-selected/apply 在同一 workspace 上并发写。"""
    key = f"{project_name}/{workspace_id}"
    if key not in _LOCKS:
        _LOCKS[key] = asyncio.Lock()
    return _LOCKS[key]


class CreateWorkspaceReq(BaseModel):
    name: str


class PreviewReq(BaseModel):
    title: str = ""
    intent: str = ""
    base_workspace_revision: int
    operations: List[dict]   # 原始 op dict，后端用 ChangeOperation.from_dict 重建（§二十二：不信任前端）


# ==================== Workspace CRUD ====================
@router.post("/api/project/{project_name}/workspaces")
async def create_workspace(project_name: str, req: CreateWorkspaceReq):
    try:
        return _store(project_name).create(req.name).to_dict()
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except ChangeSetError as e:
        _raise(e)


@router.get("/api/project/{project_name}/workspaces")
async def list_workspaces(project_name: str):
    st = _store(project_name)
    out = []
    for w in st.list():
        d = w.to_dict()
        d["base_is_stale"] = st.base_is_stale(w)
        out.append(d)
    return out


@router.get("/api/project/{project_name}/workspace/{workspace_id}")
async def get_workspace(project_name: str, workspace_id: str):
    try:
        st = _store(project_name)
        ws = st.load(workspace_id)
        cs_engine.reconcile(st, ws)            # load 时 reconcile（§十四 crash recovery）
        d = ws.to_dict()
        d["base_is_stale"] = st.base_is_stale(ws)
        return d
    except FileNotFoundError:
        raise HTTPException(404, "workspace 不存在")


@router.get("/api/project/{project_name}/workspace/{workspace_id}/project")
async def get_workspace_project(project_name: str, workspace_id: str):
    """返回 working_project（供 Studio 渲染）。读 working_project.json。"""
    try:
        return _store(project_name).load_working_project(workspace_id).to_dict()
    except FileNotFoundError:
        raise HTTPException(404, "workspace 不存在")


@router.post("/api/project/{project_name}/workspace/{workspace_id}/archive")
async def archive_workspace(project_name: str, workspace_id: str):
    try:
        return _store(project_name).archive(workspace_id).to_dict()
    except FileNotFoundError:
        raise HTTPException(404, "workspace 不存在")


@router.delete("/api/project/{project_name}/workspace/{workspace_id}")
async def delete_workspace(project_name: str, workspace_id: str):
    async with workspace_lock(project_name, workspace_id):
        _store(project_name).delete(workspace_id)
    return {"deleted": True, "workspace_id": workspace_id}


# ==================== Diff / Impact ====================
@router.get("/api/project/{project_name}/workspace/{workspace_id}/diff")
async def workspace_diff(project_name: str, workspace_id: str):
    """Workspace Aggregate Diff：base_project.json ↔ working_project.json（§十九）。"""
    try:
        st = _store(project_name)
        base = st.load_base_project(workspace_id)
        work = st.load_working_project(workspace_id)
        return {"diff": workspace_aggregate_diff(base, work)}
    except FileNotFoundError:
        raise HTTPException(404, "workspace 不存在")


@router.get("/api/project/{project_name}/workspace/{workspace_id}/impact")
async def workspace_impact(project_name: str, workspace_id: str):
    """整个 working 相对 base 的 aggregate impact——按 base vs working **重算**（§四十八）。"""
    try:
        st = _store(project_name)
        base = st.load_base_project(workspace_id)
        work = st.load_working_project(workspace_id)
        impact = compute_aggregate_impact(base, work)
        return {"diff": workspace_aggregate_diff(base, work), "impact": impact.to_dict()}
    except FileNotFoundError:
        raise HTTPException(404, "workspace 不存在")


# ==================== ChangeSet preview / apply / reject / list ====================
@router.post("/api/project/{project_name}/workspace/{workspace_id}/changeset/preview")
async def preview_changeset(project_name: str, workspace_id: str, req: PreviewReq):
    """Preview：校验 -> clone -> apply 到 clone -> diff + impact -> 持久化 changeset（不写 working）。"""
    try:
        st = _store(project_name)
        async with workspace_lock(project_name, workspace_id):
            ws = st.load(workspace_id)
            cs_engine.reconcile(st, ws)
            ops = [ChangeOperation.from_dict(o) for o in req.operations]
            return cs_engine.preview(st, ws, req.title, req.intent, req.base_workspace_revision, ops)
    except FileNotFoundError:
        raise HTTPException(404, "workspace 不存在")
    except ChangeSetError as e:
        _raise(e)


@router.post("/api/project/{project_name}/workspace/{workspace_id}/changeset/{changeset_id}/apply")
async def apply_changeset(project_name: str, workspace_id: str, changeset_id: str):
    """Apply 只接受 changeset_id（§二十二）；后端重建 candidate + 三重校验 + crash-safe（§十四）。
    返回 workspace_revision + aggregate diff/impact summary（§七十二）。"""
    try:
        st = _store(project_name)
        async with workspace_lock(project_name, workspace_id):
            ws = st.load(workspace_id)
            res = cs_engine.apply(st, ws, changeset_id)
        # §七十二：apply 后返回 aggregate diff/impact summary，前端立即更新
        base = st.load_base_project(workspace_id)
        work = st.load_working_project(workspace_id)
        diff = workspace_aggregate_diff(base, work)
        impact = compute_aggregate_impact(base, work)
        return {**res, "workspace_revision": res.get("revision"),
                "workspace_diff_summary": {"change_count": len(diff)},
                "aggregate_impact_summary": impact.summary}
    except FileNotFoundError:
        raise HTTPException(404, "workspace/changeset 不存在")
    except ChangeSetError as e:
        _raise(e)


@router.post("/api/project/{project_name}/workspace/{workspace_id}/changeset/{changeset_id}/reject")
async def reject_changeset(project_name: str, workspace_id: str, changeset_id: str):
    try:
        st = _store(project_name)
        async with workspace_lock(project_name, workspace_id):
            ws = st.load(workspace_id)
            return cs_engine.reject(st, ws, changeset_id)
    except FileNotFoundError:
        raise HTTPException(404, "workspace/changeset 不存在")
    except ChangeSetError as e:
        _raise(e)


@router.get("/api/project/{project_name}/workspace/{workspace_id}/changesets")
async def list_changesets(project_name: str, workspace_id: str):
    try:
        st = _store(project_name)
        ws = st.load(workspace_id)
        cs_engine.reconcile(st, ws)
        return st.list_changesets(workspace_id)
    except FileNotFoundError:
        raise HTTPException(404, "workspace 不存在")
