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
    """创建新项目"""
    try:
        project_dir = get_project_manager().create_project(name)
        return {"message": "项目已创建", "path": str(project_dir)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
