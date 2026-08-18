"""AI 制作规划路由（Phase AI-4A，规格 提示词.md）。

5 个端点 + 计划 IO（production_plans.json by scene_id）+ stale 检查 + approve（只改 plan.status，
不写正式 shot.opening_source，§六/§二十）。AI 只返回 opening_source/link_mode/reason；
sanitize + derive 由 src/ai/production_planner.py 普通代码完成。

  POST   /scene/{scene_id}/production-plan/plan      AI 规划，落计划（run_seed 生成）
  GET    /scene/{scene_id}/production-plan            读当前计划（含 is_stale 派生标志）
  PUT    /scene/{scene_id}/production-plan            保存用户编辑（opening_source/link_mode），后端重算派生字段
  DELETE /scene/{scene_id}/production-plan            删计划
  POST   /scene/{scene_id}/production-plan/approve    确认制作计划（status=approved，不写 Shot）

计划落 projects/{p}/output/ai/production_plans.json，按 scene_id 存。
stale = scene_summary_snapshot + scene_characters_snapshot + shot_structure_snapshot 比对（§八/§二十二）。
"""
import json
import time
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import deps
from deps import get_project_manager, logger, get_agnes_client
from src.core.store import ProjectStore
from src.api.agnes_client import AgnesError
from src.ai.production_planner import (
    ProductionPlannerWorkflow, sanitize_plan, rederive_from_edits,
    is_plan_stale, new_run_seed, SceneProductionPlan,
    plan_production_business, read_plans as _read_plans,
    write_plans as _write_plans, plans_path as _plans_path,
)

router = APIRouter()


# ============ 计划文件 IO ============
# _plans_path / _read_plans / _write_plans 已抽至 src/ai/production_planner.py
#（plans_path / read_plans / write_plans），本文件经顶部 import alias 复用——
# router 与 AI-6E Agent capability 共用同一实现，不复制 IO 逻辑（AI-6E §8）。


def _find_scene(project, scene_id: str):
    return next((sc for sc in project.scenes if sc.scene_id == scene_id), None)


def _plan_response(plan: SceneProductionPlan, scene) -> dict:
    """返回 plan + 派生 stale 标志（不持久化 stale，每次 GET 实时算）。"""
    stale_reason = is_plan_stale(plan, scene)
    d = plan.model_dump()
    d["is_stale"] = stale_reason is not None
    d["stale_reason"] = stale_reason
    return d


# ============ 请求模型 ============
class PlannedShotInput(BaseModel):
    """用户编辑单个镜头策略（只 opening_source/link_mode/reason，其余派生）。"""
    shot_id: str
    opening_source: str
    link_mode: str
    reason: str = ""


class SavePlanRequest(BaseModel):
    overview: str = ""
    shots: List[PlannedShotInput] = Field(default_factory=list)


# ============ business helper ============
# plan_production_business 已抽至 src/ai/production_planner.py（AI-6E §8：router 与
# Agent capability 共用同一实现，不复制业务逻辑）。本文件经顶部 import alias 复用。
# approve_plan_business 仍留此（Agent 不调 approve，§17；仅 router 端用）。


def approve_plan_business(plan: SceneProductionPlan, scene) -> SceneProductionPlan:
    """确认制作计划（§二十/§十七）。stale 校验 + status=approved + approved_at。
    🔴 不写 shot.opening_source / link_mode / source_shot_id（§六：plan 是未来执行意图，
    正式 opening_source 由 AI-4B Executor 在执行时应用）。不落盘（落盘由调用方决定）。
    raises ValueError（无镜头 / stale）。"""
    if not plan.shots:
        raise ValueError("计划中没有镜头。")
    stale_reason = is_plan_stale(plan, scene)
    if stale_reason:
        raise ValueError(f"制作计划已过期（{stale_reason}），请重新生成制作计划。")
    plan.status = "approved"
    plan.approved_at = time.time()
    return plan


# ============ 1. AI 规划 ============
@router.post("/api/project/{project_name}/scene/{scene_id}/production-plan/plan")
async def plan_production(project_name: str, scene_id: str):
    """AI 为一个故事段的 Shot[] 制定制作规划，落计划。可重复调用（重新规划，覆盖计划）。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        scene = _find_scene(project, scene_id)
        if scene is None:
            raise HTTPException(404, f"故事段 {scene_id} 不存在")
        client = get_agnes_client()
        try:
            plan = await plan_production_business(project, scene, project_name, client)
        except ValueError as e:
            raise HTTPException(400, str(e))
        plans = _read_plans(project_dir)
        plans[scene_id] = plan
        _write_plans(project_dir, plans)
        logger.info(f"AI 制作规划: {project_name}/{scene_id}, {len(plan.shots)} 镜头, "
                    f"run_seed={plan.run_seed}")
        return _plan_response(plan, scene)
    except AgnesError as e:
        raise HTTPException(502, f"AI 服务错误: {e}")
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(502, f"AI 返回解析失败: {e}")
    except Exception as e:
        logger.error(f"AI 制作规划失败: {e}")
        raise HTTPException(500, str(e))


# ============ 2. 读当前计划 ============
@router.get("/api/project/{project_name}/scene/{scene_id}/production-plan")
async def get_production_plan(project_name: str, scene_id: str):
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        scene = _find_scene(project, scene_id)
        if scene is None:
            raise HTTPException(404, f"故事段 {scene_id} 不存在")
        plans = _read_plans(project_dir)
        plan = plans.get(scene_id)
        if plan is None:
            raise HTTPException(404, "没有 AI 制作计划")
        return _plan_response(plan, scene)
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ============ 3. 保存用户编辑（重算派生字段）============
@router.put("/api/project/{project_name}/scene/{scene_id}/production-plan")
async def save_production_plan(project_name: str, scene_id: str, req: SavePlanRequest):
    """保存用户对 opening_source/link_mode 的编辑。后端重算 source_shot_id /
    requires_previous_video_end / generate_first_frame / generate_last_frame（§十八）。
    source_shot_id 不让用户选：reference/inherit 自动 = 紧邻上一镜。
    若 Scene 已变（stale）-> 409，必须重新规划。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        scene = _find_scene(project, scene_id)
        if scene is None:
            raise HTTPException(404, f"故事段 {scene_id} 不存在")
        plans = _read_plans(project_dir)
        plan = plans.get(scene_id)
        if plan is None:
            raise HTTPException(404, "没有 AI 制作计划，无法保存。请先 AI 制作规划。")

        stale_reason = is_plan_stale(plan, scene)
        if stale_reason:
            raise HTTPException(409, f"制作计划已过期（{stale_reason}），请重新生成制作计划。")

        rederive_from_edits(scene, plan, [s.model_dump() for s in req.shots])
        plan.overview = req.overview
        _write_plans(project_dir, plans)
        return _plan_response(plan, scene)
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存制作计划失败: {e}")
        raise HTTPException(500, str(e))


# ============ 4. 删计划 ============
@router.delete("/api/project/{project_name}/scene/{scene_id}/production-plan")
async def delete_production_plan(project_name: str, scene_id: str):
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        plans = _read_plans(project_dir)
        if scene_id in plans:
            del plans[scene_id]
            _write_plans(project_dir, plans)
        return {"ok": True}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        raise HTTPException(500, str(e))


# ============ 5. 确认制作计划 ============
@router.post("/api/project/{project_name}/scene/{scene_id}/production-plan/approve")
async def approve_production_plan(project_name: str, scene_id: str):
    """确认制作计划（§二十）。plan.status = approved，保存独立 AI 文件。
    🔴 不写 shot.opening_source / shot.link_mode / shot.source_shot_id（§六）：
    正式 opening_source 由 AI-4B Executor 在真正执行当前 Shot 时应用。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        scene = _find_scene(project, scene_id)
        if scene is None:
            raise HTTPException(404, f"故事段 {scene_id} 不存在")
        plans = _read_plans(project_dir)
        plan = plans.get(scene_id)
        if plan is None:
            raise HTTPException(404, "没有 AI 制作计划，请先 AI 制作规划。")

        # 业务（stale / status=approved / approved_at）走共享 helper（§六十四）
        try:
            approve_plan_business(plan, scene)
        except ValueError as e:
            msg = str(e)
            if "过期" in msg:
                raise HTTPException(409, msg)
            raise HTTPException(400, msg)
        _write_plans(project_dir, plans)

        # 🔴 关键不变量：approve 不动 project.json。校验 Shot.opening_source 仍是默认 independent。
        # （不写 source_shot_id / source_version / link_mode。AI-4B Executor 负责在执行时生效。）
        logger.info(f"AI 制作计划已确认: {project_name}/{scene_id}, {len(plan.shots)} 镜头, "
                    f"run_seed={plan.run_seed}（未写入正式 Shot）")
        return _plan_response(plan, scene)
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"确认制作计划失败: {e}")
        raise HTTPException(500, str(e))
