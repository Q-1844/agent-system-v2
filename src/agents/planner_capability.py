# src/agents/planner_capability.py
# AI-6E production_plan.create 能力执行器（async）。
#
# 包装现有 ProductionPlannerWorkflow（经中性 plan_production_business）+ 落盘，不重写
# Planner 的 system prompt / sanitizer / derivation / stale 逻辑（§7/§9/§10）。
#
# 职责（§6/§14）：
#   scene scope 验证 → plan_production_business → 落 production_plans.json →
#   返回 AgentStructuredResult（引用真实 artifact，不建第二份 copy）。
#   不写 canonical project.json（§18）；不 approve（§17）。
#
# Import 纪律：只 import src.core / src.ai / 本包 models。不 import routers/* / deps。
#   client / project_loader / project_dir 由 AgentRuntime 注入（测试可注入 lambda）。
from __future__ import annotations
from pathlib import Path
from typing import Any, Callable, Optional

from src.ai.production_planner import (
    plan_production_business, read_plans, write_plans, is_plan_stale,
    SceneProductionPlan,
)
from .models import AgentStructuredResult, ResultStatus


def _find_scene(project, scene_id: str):
    return next((s for s in project.scenes if s.scene_id == scene_id), None)


def _plan_to_data(plan: SceneProductionPlan, stale_reason: Optional[str]) -> dict:
    """plan 的最小安全 API 表示（对齐 production_plan.inspect + 暴露 derived 字段
    证明 §10 确定性）。源真相是 production_plans.json；此 data 是只读 summary，
    不持久化第二份 authoritative copy（§14）。"""
    return {
        "scene_id": plan.scene_id,
        "plan_id": plan.plan_id,
        "plan_status": plan.status,
        "is_stale": stale_reason is not None,
        "stale_reason": stale_reason,
        "run_seed": plan.run_seed,
        "overview": plan.overview,
        "shots": [
            {"shot_id": s.shot_id,
             "opening_source": s.opening_source,
             "link_mode": s.link_mode,
             "source_shot_id": s.source_shot_id,
             "generate_first_frame": s.generate_first_frame,
             "generate_last_frame": s.generate_last_frame,
             "generate_video": s.generate_video,
             "requires_previous_video_end": s.requires_previous_video_end,
             "reason": s.reason}
            for s in plan.shots
        ],
    }


async def create_production_plan(
    client: Optional[Any],
    project_loader: Callable[[], Any],
    project_dir: Path,
    project_name: str,
    scope_type: str,
    scope_id: str,
    instruction: str,
) -> AgentStructuredResult:
    """production_plan.create 执行器（§6/§14）。

    链路：scene scope 验证 → plan_production_business（AI+sanitize+derive）→
    落 production_plans.json → AgentStructuredResult 引用 artifact。
    不 approve（§17）、不写 canonical（§18）、不执行 Shot 生成（§31）。
    """
    # §6 scene scope only
    if scope_type != "scene":
        return AgentStructuredResult(
            status=ResultStatus.FAILED.value,
            summary=f"production_plan.create 仅支持 scene 级 scope（收到 {scope_type}）。",
            warnings=[f"invalid scope_type: {scope_type}"],
            data={"scope_type": scope_type, "scope_id": scope_id})

    # load project + resolve scene
    try:
        project = project_loader()
    except Exception as e:
        return AgentStructuredResult(
            status=ResultStatus.FAILED.value,
            summary=f"项目不可用：{str(e)[:200]}",
            warnings=[str(e)[:500]])

    scene = _find_scene(project, scope_id)
    if scene is None:
        # §24-12 unknown Scene rejected cleanly
        return AgentStructuredResult(
            status=ResultStatus.FAILED.value,
            summary=f"未找到场景 {scope_id}。",
            warnings=[f"unknown scene: {scope_id}"],
            data={"scope_id": scope_id})

    # §6 scene must have Shots；§12 Shot 创意欠定 → 结构化 blocker（概念上游 Director）
    if not scene.shots:
        return AgentStructuredResult(
            status=ResultStatus.FAILED.value,
            summary=(f"场景 {scope_id} 无镜头，无法制作规划。请先让 Director 为该场景规划分镜。"),
            warnings=["scene has no shots"],
            suggested_next_actions=["委派 Director 为该场景规划镜头后再制作规划"],
            data={"scene_id": scope_id,
                  "blocker": "needs_upstream_change",
                  "upstream_owner": "director"})

    # §9/§23 Agnes 未配置 → not_configured（软失败）
    if client is None:
        return AgentStructuredResult(
            status=ResultStatus.NOT_CONFIGURED.value,
            summary="[制作规划未配置：Agnes 推理模型不可用。请在设置中配置 API Key 后重试。]",
            data={"scene_id": scope_id})

    # §7/§9 调现有 Planner workflow（AI 只 opening_source/link_mode/reason；余代码 derive）
    try:
        plan = await plan_production_business(project, scene, project_name, client)
    except ValueError as e:
        # 无 Shot 兜底（plan_production_business 内部也校验，双保险）
        return AgentStructuredResult(
            status=ResultStatus.FAILED.value,
            summary=str(e),
            warnings=[str(e)[:500]],
            data={"scene_id": scope_id})
    except Exception as e:
        # AgnesError / JSON 解析失败等
        return AgentStructuredResult(
            status=ResultStatus.FAILED.value,
            summary=f"AI 制作规划失败：{str(e)[:200]}",
            warnings=[str(e)[:500]],
            data={"scene_id": scope_id})

    # §14/§15 落盘到唯一源真相 production_plans.json（by scene_id）
    plans = read_plans(project_dir)
    plans[scope_id] = plan
    write_plans(project_dir, plans)

    # §19 stale 派生（不持久化，实时算，与现有 Planner 端点同一 is_plan_stale）
    stale_reason = is_plan_stale(plan, scene)

    # §14 AgentStructuredResult 引用真实 artifact；artifact-first handoff（§15）
    artifact_refs = [{"type": "production_plan", "id": plan.plan_id,
                      "scene_id": scope_id}]
    artifact_refs.extend({"type": "shot", "id": s.shot_id} for s in plan.shots)

    return AgentStructuredResult(
        status=ResultStatus.COMPLETED.value,
        summary=(f"场景 {scope_id} 制作计划已生成（{plan.status}）：{len(plan.shots)} 个镜头策略，"
                 f"run_seed={plan.run_seed}。请在制作计划界面审查并确认。"),
        artifact_refs=artifact_refs,
        suggested_next_actions=["在制作计划界面审查/编辑", "确认制作计划后交 Shot Producer 执行"],
        data=_plan_to_data(plan, stale_reason))
