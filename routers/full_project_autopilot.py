"""Full Project Autopilot 路由（Phase AI-5B / AI 完整制作）。

7 个端点。FullProjectManager 是最外层 Controller（Preparation 补齐 + 委托 ProjectProductionManager）。
不直接碰 Agnes/JobQueue；Agnes 只在 DirectorWorkflow/ProductionPlannerWorkflow 内部使用（§六十五）。
start 时后端自己 load + inspect（§三十，不信任前端 inspect），不让前端传 Scene/draft/plan/signature。

  GET   /api/project/{p}/full-production/inspect        纯代码扫描项目准备情况（§二十七）
  POST  /api/project/{p}/full-production/start           开始 AI 完整制作（body: {respect_locked}）
  GET   /api/project/{p}/full-production/runs            列出历史 FullProjectRun
  GET   /api/project/{p}/full-production/run/{run_id}    读单个 FullProjectRun（Monitor 轮询）
  POST  /api/project/{p}/full-production/run/{run_id}/pause
  POST  /api/project/{p}/full-production/run/{run_id}/resume
  POST  /api/project/{p}/full-production/run/{run_id}/cancel
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import deps
from deps import get_project_manager, logger
from src.core.store import ProjectStore
from src.ai.full_project_autopilot import (
    get_full_project_manager, _ExistingFullRunError,
    FRUN_TERMINAL, FRUN_RESUMABLE,
)

router = APIRouter()


class StartRequest(BaseModel):
    respect_locked: bool = True


# ============ 1. inspect ============
@router.get("/api/project/{project_name}/full-production/inspect")
async def full_production_inspect(project_name: str):
    """纯代码 Inspect（§二十七，不调 AI，不创建 Run）。返回各 Scene 分类 + warnings + blockers。"""
    try:
        mgr = get_full_project_manager()
        return (await mgr.inspect(project_name)).model_dump()
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        logger.error(f"full-production inspect 失败: {e}")
        raise HTTPException(500, str(e))


# ============ 2. start ============
@router.post("/api/project/{project_name}/full-production/start")
async def full_production_start(project_name: str, req: StartRequest):
    """开始 AI 完整制作（§三十：后端 load + inspect + 确认无 blocker + 冻结 source snapshot + 启动）。
    active FullProjectRun 已存在 -> 409 + existing_run_id（§二十九，不自动取消旧 Run）。"""
    try:
        mgr = get_full_project_manager()
        run = await mgr.start_run(project_name, req.respect_locked)
        logger.info(f"FullProjectRun 启动: {project_name} run={run.run_id} "
                    f"({len(run.scenes)} 故事段, respect_locked={req.respect_locked})")
        return run.model_dump()
    except _ExistingFullRunError as e:
        raise HTTPException(409, detail={"message": "这个项目已有 AI 完整制作任务。",
                                         "existing_run_id": e.existing_run_id})
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"FullProjectRun 启动失败: {e}")
        raise HTTPException(500, str(e))


# ============ 3. 列出 FullProjectRun ============
@router.get("/api/project/{project_name}/full-production/runs")
async def full_production_list_runs(project_name: str):
    try:
        mgr = get_full_project_manager()
        return [r.model_dump() for r in mgr.list_runs(project_name)]
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        raise HTTPException(500, str(e))


# ============ 4. 读单个 FullProjectRun ============
@router.get("/api/project/{project_name}/full-production/run/{run_id}")
async def full_production_get_run(project_name: str, run_id: str):
    try:
        mgr = get_full_project_manager()
        run = mgr.get_run(project_name, run_id)
        if run is None:
            raise HTTPException(404, "Run 不存在")
        return run.model_dump()
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ============ 5. pause ============
@router.post("/api/project/{project_name}/full-production/run/{run_id}/pause")
async def full_production_pause(project_name: str, run_id: str):
    try:
        mgr = get_full_project_manager()
        run = await mgr.request_pause(project_name, run_id)
        return run.model_dump()
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


# ============ 6. resume ============
@router.post("/api/project/{project_name}/full-production/run/{run_id}/resume")
async def full_production_resume(project_name: str, run_id: str):
    try:
        mgr = get_full_project_manager()
        run = await mgr.resume_run(project_name, run_id)
        return run.model_dump()
    except ValueError as e:
        msg = str(e)
        if "不可 resume" in msg:
            raise HTTPException(400, msg)
        raise HTTPException(404, msg)
    except Exception as e:
        raise HTTPException(500, str(e))


# ============ 7. cancel ============
@router.post("/api/project/{project_name}/full-production/run/{run_id}/cancel")
async def full_production_cancel(project_name: str, run_id: str):
    try:
        mgr = get_full_project_manager()
        run = await mgr.request_cancel(project_name, run_id)
        return run.model_dump()
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))
