"""项目路由：列出 / 创建项目（PHASE_5 §5.2 projects 组）。"""
from fastapi import APIRouter, HTTPException

from deps import get_project_manager

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
async def create_project(name: str):
    """创建新项目（建目录 + 初始化空 project.json）。重名返回 409。"""
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="项目名不能为空")
    name = name.strip()
    try:
        project_dir = get_project_manager().create_project(name)
        return {"success": True, "name": name, "message": "项目已创建", "path": str(project_dir)}
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"项目已存在: {name}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
