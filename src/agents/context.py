# src/agents/context.py
# Project Context Builder（§17）。根据 Agent 类型提供最小必要上下文，不让每个 Agent
# 自己随意扫描整个项目。第一版不做 token optimizer，只保持职责隔离。
#
# Import 纪律：只 import src.core.models / 本包。project_loader 由 runtime 注入（零参 -> Project）。
from __future__ import annotations
from typing import Callable, Optional

from ..core.models import Project


def _scene_of(p: Project, scope_id: str):
    return next((s for s in p.scenes if s.scene_id == scope_id), None)


def build_context(agent_id: str, project: str, scope_type: str, scope_id: str,
                  project_loader: Callable[[], Project],
                  plans_reader: Optional[Callable] = None) -> str:
    """按 agent 类型构建最小必要上下文文本（拼进 system prompt）。"""
    try:
        p = project_loader()
    except Exception as e:
        return (f"[项目上下文不可用: {str(e)[:120]}]\n"
                f"当前 project={project}, scope={scope_type}:{scope_id}")

    lines = [f"# 当前上下文", f"project: {p.name}", f"scope: {scope_type}:{scope_id}"]
    sh = None
    sc = None
    if scope_type == "scene":
        sc = _scene_of(p, scope_id)
    elif scope_type == "shot":
        sh = p.get_shot(scope_id)
        if sh:
            sc = _scene_of(p, sh.scene_id)

    if agent_id == "producer":
        lines.append(f"场景数 {len(p.scenes)} / 镜头数 {len(p.all_shots())} / 角色数 {len(p.characters)}")
        lines.append("可委派 Specialist: script_writer/character_designer/scene_designer/director/production_planner/shot_producer/continuity_reviewer/editor")
    elif agent_id == "script_writer":
        lines.append(f"故事文本({len(p.story_text)}字): {p.story_text[:500]}")
        for s in p.scenes:
            lines.append(f"  场景 {s.scene_id}: {s.summary[:60]} | 角色 {s.characters}")
    elif agent_id == "character_designer":
        for c in p.characters:
            views = [v for v in ("front", "side", "back") if c.views.get(v) and (c.views[v].local or c.views[v].url)]
            lines.append(f"  角色 {c.name}: {c.description_en[:60]} | 已有视图 {views or '无'}")
        lines.append(f"风格: {p.style.suffix()[:80]}")
    elif agent_id == "director":
        if sc:
            lines.append(f"场景 {sc.scene_id}「{sc.title or sc.summary[:24]}」: {sc.summary}")
            lines.append(f"  角色: {sc.characters}")
            for s in sc.shots:
                lines.append(f"  镜头 {s.shot_id}: {s.description[:60]} | 角色 {s.characters} | {s.target_duration_s}s | {s.status}")
        else:
            for s in p.scenes:
                lines.append(f"  场景 {s.scene_id}: {len(s.shots)} 镜头")
    elif agent_id == "production_planner":
        if sc:
            for s in sc.shots:
                lines.append(f"  镜头 {s.shot_id}: {s.description[:50]} | opening={s.opening_source} link={s.link_mode}")
        if plans_reader:
            try:
                plan = plans_reader(project, sc.scene_id if sc else scope_id)
                lines.append(f"制作计划: {'有' if plan else '无'}" + (f" 状态={plan.get('status')}" if plan else ""))
            except Exception:
                pass
    elif agent_id == "shot_producer":
        if sh:
            lines.append(f"镜头 {sh.shot_id}: {sh.description}")
            lines.append(f"  首帧={sh.first_frame.status} 尾帧={sh.last_frame.status} 视频={sh.video.status} 音频={sh.audio.status}")
            lines.append(f"  opening={sh.opening_source} link={sh.link_mode}")
    elif agent_id == "continuity_reviewer":
        if sh:
            lines.append(f"镜头 {sh.shot_id}: {sh.description[:60]} | 状态={sh.status}")
            lines.append(f"  首帧={sh.first_frame.status} 视频={sh.video.status} final={sh.final_video}")
    elif agent_id == "editor":
        for s in p.all_shots():
            lines.append(f"  镜头 {s.shot_id}: final={s.final_video} 视频={s.video.status}")
    else:
        lines.append(f"场景数 {len(p.scenes)} / 镜头数 {len(p.all_shots())}")
    return "\n".join(lines)
