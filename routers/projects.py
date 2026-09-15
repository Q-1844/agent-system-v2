"""项目路由：列出 / 创建项目（PHASE_5 §5.2 projects 组）+ Project Visual Profile（P1 §20）。

P1 契约：
- create 必须收到明确 render_family（P1 §6：不静默默认 photorealistic）；
  art_direction optional。
- GET visual-profile：旧项目（无 project_profile.json）→ specified=false，
  纯读不创建文件（P1 §5）。
- PUT visual-profile：expected_revision OCC，stale → 409，不 silent retry（P1 §8）。
"""
from fastapi import APIRouter, HTTPException

from deps import get_project_manager
from src.visual_profile import VisualProfileService
from src.visual_profile.models import VisualProfileSchemaError

router = APIRouter()


@router.get("/api/projects")
async def list_projects():
    """列出所有项目"""
    try:
        projects = get_project_manager().list_projects()
        return {"projects": projects}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/projects/create")
async def create_project(name: str, render_family: str, art_direction: str = ""):
    """创建新项目（建目录 + 初始化空 project.json + 写 project_profile.json）。
    重名返回 409。render_family 必填（P1 §6：明确选择，不静默默认）。"""
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="项目名不能为空")
    if not render_family or not render_family.strip():
        raise HTTPException(status_code=400,
                            detail="render_family 必填（project_visual_profile_required："
                                   "新项目必须明确选择视觉介质，不静默默认）")
    name = name.strip()
    try:
        project_dir = get_project_manager().create_project(
            name, render_family=render_family.strip(), art_direction=art_direction or "")
        return {"success": True, "name": name, "message": "项目已创建", "path": str(project_dir)}
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"项目已存在: {name}")
    except VisualProfileSchemaError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ Project Visual Profile（P1 §20）============

def _visual_profile_service(project_name: str) -> VisualProfileService:
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_name}")
    return VisualProfileService(project_dir, project_name)


@router.get("/api/project/{project}/visual-profile")
async def get_visual_profile(project: str):
    """读项目视觉定义。旧项目 → {specified: false}（纯读不创建文件，P1 §5）。"""
    try:
        return _visual_profile_service(project).read()
    except VisualProfileSchemaError as e:
        # 坏文件诚实上报（不静默当 unspecified——那会伪装掉 fail closed 语义）
        raise HTTPException(status_code=500, detail=f"project_profile.json 损坏: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/project/{project}/visual-profile")
async def update_visual_profile(project: str, body: dict):
    """更新项目视觉定义。expected_revision OCC；unspecified 项目首次写入传 ""。
    修改不自动重新生成/删除/revoke 已有资产（P1 §9，derived stale）。"""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body 必须是 JSON 对象")
    try:
        receipt = await _visual_profile_service(project).update_async(
            render_family=body.get("render_family") or "",
            art_direction=body.get("art_direction") or "",
            style_tags=body.get("style_tags"),
            negative_style_constraints=body.get("negative_style_constraints"),
            expected_revision=str(body.get("expected_revision") or ""),
        )
    except VisualProfileSchemaError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not receipt.get("ok"):
        status = 409 if receipt.get("error") == "stale_revision" else 400
        raise HTTPException(status_code=status, detail=receipt)
    return receipt


# ============ Native AV 生产设置（C1 18.md §5：spoken_language = language truth）============
@router.get("/api/project/{project}/native-av-settings")
async def get_native_av_settings(project: str):
    """读项目 Native AV 生产设置。任何项目恒可读（缺省 = 空 settings，
    对白生成时按 §5 fail closed 提示需先设置）。"""
    try:
        from src.core.store import ProjectStore
        project_dir = get_project_manager().get_project_dir(project)
        p = ProjectStore(project_dir).load()
        from dataclasses import asdict
        return {"ok": True, "native_av": asdict(p.native_av)}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/project/{project}/native-av-settings")
async def update_native_av_settings(project: str, body: dict):
    """更新项目 Native AV 生产设置（最小面：spoken_language）。
    spoken_language 为空字符串 = 清除（清除后有台词 shot 生成将按 §5 fail closed）。
    修改不自动重新生成任何资产（18.md §26：detect → inform → explicit regenerate）。"""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body 必须是 JSON 对象")
    if "spoken_language" not in body:
        raise HTTPException(status_code=400, detail="body 必须包含 spoken_language")
    try:
        from src.core.store import ProjectStore
        from src.core.models import NativeAVSettings
        project_dir = get_project_manager().get_project_dir(project)
        store = ProjectStore(project_dir)
        p = store.load()
        p.native_av = NativeAVSettings(
            spoken_language=str(body.get("spoken_language") or "").strip())
        store.save(p)
        return {"ok": True, "native_av": {
            "spoken_language": p.native_av.spoken_language}}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
