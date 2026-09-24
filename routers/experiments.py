# routers/experiments.py
# Phase 37A（37.md §2/§16）：Calibration Trace 的正式 REST 契约。
# 实验编排可用脚本跑，但记录能力必须是正式 backend contract（不进 one-off script）；
# 前端 UI 本阶段不做（§0/§16）。存储为非 canonical：output/experiments/calibration_runs/。
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import deps

from src.experiments import calibration_trace as trace
from src.experiments.calibration_trace import CalibrationTraceError

router = APIRouter(prefix="/api/project/{project_name}/calibration", tags=["experiments"])


def _project_dir(project_name: str) -> Path:
    try:
        return deps.get_project_manager().get_project_dir(project_name)
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")


def _err(e: CalibrationTraceError):
    status = 404 if e.code == "not_found" else 400
    raise HTTPException(status, str(e))


class CreateRunRequest(BaseModel):
    story_id: str
    title: str = ""
    story_plan: Optional[List[dict]] = None   # 冻结故事：每镜 {shot_id,description,expected_tail,duration_s,...}
    chain_root_reference_id: str = ""          # 37.md §8：链根追踪
    notes: str = ""


class UpdateRunRequest(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None


class LinkRecordRequest(BaseModel):
    record: Dict[str, Any] = {}


@router.post("/runs")
async def create_calibration_run(project_name: str, req: CreateRunRequest):
    """创建一次校准 run（故事冻结声明入档）。"""
    try:
        run = trace.create_run(_project_dir(project_name), story_id=req.story_id,
                               title=req.title, story_plan=req.story_plan,
                               chain_root_reference_id=req.chain_root_reference_id,
                               notes=req.notes)
        return run
    except CalibrationTraceError as e:
        _err(e)


@router.get("/runs")
async def list_calibration_runs(project_name: str):
    return {"runs": trace.list_runs(_project_dir(project_name))}


@router.get("/runs/{run_id}")
async def get_calibration_run(project_name: str, run_id: str):
    try:
        run = trace.get_run(_project_dir(project_name), run_id)
        run["links"] = trace.list_links(_project_dir(project_name), run_id)
        return run
    except CalibrationTraceError as e:
        _err(e)


@router.patch("/runs/{run_id}")
async def update_calibration_run(project_name: str, run_id: str,
                                 req: UpdateRunRequest):
    try:
        patch = {k: v for k, v in req.model_dump().items() if v is not None}
        return trace.update_run(_project_dir(project_name), run_id, patch)
    except CalibrationTraceError as e:
        _err(e)


@router.put("/runs/{run_id}/links/{shot_id}")
async def upsert_calibration_link(project_name: str, run_id: str,
                                  shot_id: str, req: LinkRecordRequest):
    """upsert 一条 link 记录（merge：vision/human/timing 可分次补齐）。"""
    try:
        return trace.upsert_link(_project_dir(project_name), run_id, shot_id,
                                 req.record)
    except CalibrationTraceError as e:
        _err(e)


@router.get("/runs/{run_id}/links/{shot_id}")
async def get_calibration_link(project_name: str, run_id: str, shot_id: str):
    try:
        return trace.get_link(_project_dir(project_name), run_id, shot_id)
    except CalibrationTraceError as e:
        _err(e)
