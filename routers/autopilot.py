"""Scene Autopilot Executor 路由（Phase AI-4B，规格 提示词.md §三十九）。

6 个端点。Executor 是确定性执行引擎，不新增 AI Specialist。start 时后端自己读 plan / 生成 run_seed /
冻结 plan_snapshot，不让前端传（§三十九）。

  POST   /scene/{scene_id}/autopilot/start          开始自动制作（body: {respect_locked})
  GET    /scene/{scene_id}/autopilot/runs            列出本故事段历史 Run
  GET    /autopilot/run/{run_id}                     读单个 Run（Run Monitor 轮询用）
  POST   /autopilot/run/{run_id}/pause               暂停（当前 job 完成后停）
  POST   /autopilot/run/{run_id}/resume              继续（从 checkpoint）
  POST   /autopilot/run/{run_id}/cancel              停止（保留已生成资产）
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import deps
from deps import get_project_manager, logger
from src.core.store import ProjectStore
from src.ai.autopilot import get_autopilot_manager, _ExistingRunError

router = APIRouter()


class StartRequest(BaseModel):
    respect_locked: bool = True


def _find_scene(project, scene_id: str):
    return next((sc for sc in project.scenes if sc.scene_id == scene_id), None)


# ============ 1. start ============
@router.post("/api/project/{project_name}/scene/{scene_id}/autopilot/start")
async def autopilot_start(project_name: str, scene_id: str, req: StartRequest):
    """开始自动制作本故事段（§八 preflight + 创建 Run + 启动后台 Executor）。
    active Run 已存在时返回 409 + existing_run_id（前端据此跳转 Run Monitor）。"""
    try:
        mgr = get_autopilot_manager()
        run = await mgr.start_scene_run(project_name, scene_id, req.respect_locked)
        logger.info(f"Autopilot 启动: {project_name}/{scene_id} run={run.run_id} "
                    f"({len(run.shots)} 镜头, seed={run.run_seed})")
        return run.model_dump()
    except _ExistingRunError as e:
        raise HTTPException(409, detail={"message": "这个故事段已有自动制作任务。",
                                          "existing_run_id": e.existing_run_id})
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except ValueError as e:
        msg = str(e)
        if "不存在" in msg:
            raise HTTPException(404, msg)
        raise HTTPException(400, msg)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Autopilot 启动失败: {e}")
        raise HTTPException(500, str(e))


# ============ 2. 列出本故事段 Run ============
@router.get("/api/project/{project_name}/scene/{scene_id}/autopilot/runs")
async def autopilot_list_runs(project_name: str, scene_id: str):
    try:
        mgr = get_autopilot_manager()
        return [r.model_dump() for r in mgr.list_runs_for_scene(project_name, scene_id)]
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        raise HTTPException(500, str(e))


# ============ 3. 读单个 Run ============
@router.get("/api/project/{project_name}/autopilot/run/{run_id}")
async def autopilot_get_run(project_name: str, run_id: str):
    try:
        mgr = get_autopilot_manager()
        run = mgr.get_run(project_name, run_id)
        if run is None:
            raise HTTPException(404, "Run 不存在")
        # 附当前 shot 进度摘要（前端 Run Monitor 用）
        return run.model_dump()
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ============ 4. pause ============
@router.post("/api/project/{project_name}/autopilot/run/{run_id}/pause")
async def autopilot_pause(project_name: str, run_id: str):
    try:
        mgr = get_autopilot_manager()
        run = await mgr.request_pause(project_name, run_id)
        return run.model_dump()
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


# ============ 5. resume ============
@router.post("/api/project/{project_name}/autopilot/run/{run_id}/resume")
async def autopilot_resume(project_name: str, run_id: str):
    try:
        mgr = get_autopilot_manager()
        run = await mgr.resume_run(project_name, run_id)
        return run.model_dump()
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


# ============ 6. cancel ============
@router.post("/api/project/{project_name}/autopilot/run/{run_id}/cancel")
async def autopilot_cancel(project_name: str, run_id: str):
    try:
        mgr = get_autopilot_manager()
        run = await mgr.request_cancel(project_name, run_id)
        return run.model_dump()
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))
