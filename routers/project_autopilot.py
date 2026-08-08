"""Prepared Project Autopilot 路由（Phase AI-5A）。

7 个端点。ProjectProductionManager 是项目级 Controller（顺序 Scene 编排 + 合成），
不直接碰 Agnes/JobQueue，只调 SceneAutopilotManager + composer。start 时后端自己 load +
重新 preflight（§十九），不让前端传 source/plans/run_id。

  GET   /api/project/{p}/production/preflight        项目就绪检查（§十七）
  POST  /api/project/{p}/production/start             开始完整制作（body: {respect_locked}）
  GET   /api/project/{p}/production/runs              列出历史 ProjectRun
  GET   /api/project/{p}/production/run/{run_id}      读单个 ProjectRun（Monitor 轮询）
  POST  /api/project/{p}/production/run/{run_id}/pause
  POST  /api/project/{p}/production/run/{run_id}/resume
  POST  /api/project/{p}/production/run/{run_id}/cancel
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import deps
from deps import get_project_manager, logger
from src.core.store import ProjectStore
from src.ai.project_autopilot import (
    get_project_autopilot_manager, preflight_project,
    _ExistingProjectRunError,
    PRUN_TERMINAL, PRUN_RESUMABLE,
)

router = APIRouter()


class StartRequest(BaseModel):
    respect_locked: bool = True


# ============ 1. preflight ============
@router.get("/api/project/{project_name}/production/preflight")
async def production_preflight(project_name: str, respect_locked: bool = True):
    """项目级 preflight（§十七）。复用 preflight_scene_autopilot 逐 Scene 检查（§十八 同一规则）。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        project = ProjectStore(project_dir).load()
        return preflight_project(project, project_dir,
                                 respect_locked=respect_locked, project_name=project_name).model_dump()
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        logger.error(f"production preflight 失败: {e}")
        raise HTTPException(500, str(e))


# ============ 2. start ============
@router.post("/api/project/{project_name}/production/start")
async def production_start(project_name: str, req: StartRequest):
    """开始完整制作（§十九：后端 load + 重新完整 preflight + 冻结 snapshot + 启动 Executor）。
    active ProjectRun 已存在 -> 409 + existing_run_id。"""
    try:
        mgr = get_project_autopilot_manager()
        run = await mgr.start_run(project_name, req.respect_locked)
        logger.info(f"ProjectRun 启动: {project_name} run={run.run_id} "
                    f"({len(run.scenes)} 故事段, respect_locked={req.respect_locked})")
        return run.model_dump()
    except _ExistingProjectRunError as e:
        raise HTTPException(409, detail={"message": "这个项目已有完整制作任务。",
                                          "existing_run_id": e.existing_run_id})
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"ProjectRun 启动失败: {e}")
        raise HTTPException(500, str(e))


# ============ 3. 列出 ProjectRun ============
@router.get("/api/project/{project_name}/production/runs")
async def production_list_runs(project_name: str):
    try:
        mgr = get_project_autopilot_manager()
        return [r.model_dump() for r in mgr.list_runs(project_name)]
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        raise HTTPException(500, str(e))


# ============ 4. 读单个 ProjectRun ============
@router.get("/api/project/{project_name}/production/run/{run_id}")
async def production_get_run(project_name: str, run_id: str):
    try:
        mgr = get_project_autopilot_manager()
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
@router.post("/api/project/{project_name}/production/run/{run_id}/pause")
async def production_pause(project_name: str, run_id: str):
    try:
        mgr = get_project_autopilot_manager()
        run = await mgr.request_pause(project_name, run_id)
        return run.model_dump()
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


# ============ 6. resume ============
@router.post("/api/project/{project_name}/production/run/{run_id}/resume")
async def production_resume(project_name: str, run_id: str):
    try:
        mgr = get_project_autopilot_manager()
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
@router.post("/api/project/{project_name}/production/run/{run_id}/cancel")
async def production_cancel(project_name: str, run_id: str):
    try:
        mgr = get_project_autopilot_manager()
        run = await mgr.request_cancel(project_name, run_id)
        return run.model_dump()
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))
