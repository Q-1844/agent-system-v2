# src/visual_profile/compiler.py
# Project Visual Profile V1 — Phase P1 §10：project_visual_style_compiler。
#
# 🔴 唯一编译点：ProjectVisualProfile → deterministic style instruction。
#    五个 prompt builder（look/composition/location/instance view/review）一律
#    消费本函数输出，**绝不在各自 builder 里复制风格文字**（P1 §10）。
#
# 🔴 确定性：同 profile → 逐字节同输出。无时间、无随机、无 LLM 参与。
#
# 🔴 分层（P1 §12）：这里只表达"全片介质/艺术方向"；Location style_notes、
#    SceneState（时间/天气/灯光/损坏）由各自域承载，不得混入本编译器。
from __future__ import annotations

from typing import Optional

from .models import RENDER_FAMILIES, ProjectVisualProfile

# render_family → 介质指令（P1 §10 示例口径：正面说"要什么"+ 负面排除异介质）
_RENDER_FAMILY_INSTRUCTION = {
    "photorealistic":
        "Visual production style: photorealistic live-action imagery. "
        "Do not render as 2D/3D animation, illustration or cartoon.",
    "2d_animation":
        "Visual production style: 2D animated image. "
        "Do not render as live-action photography or 3D rendered CGI.",
    "3d_animation":
        "Visual production style: 3D animated CGI render. "
        "Do not render as live-action photography or 2D flat illustration.",
    "stylized":
        "Visual production style: stylized illustrated artwork (intentionally "
        "non-photorealistic). Do not render as live-action photography.",
    "custom":
        "Visual production style: follow the project art direction below exactly.",
}


def render_family_instruction(render_family: str) -> str:
    if render_family not in _RENDER_FAMILY_INSTRUCTION:
        raise ValueError(f"未知 render_family: {render_family!r}（合法值 {RENDER_FAMILIES}）")
    return _RENDER_FAMILY_INSTRUCTION[render_family]


def compile_project_style_instruction(profile: ProjectVisualProfile) -> str:
    """ProjectVisualProfile → 确定性 style instruction（供生成 prompt 后缀与
    Vision Review 期望段共同消费）。同 profile → 逐字节同输出。"""
    if profile is None:
        return ""
    parts = [render_family_instruction(profile.render_family)]
    if profile.art_direction:
        parts.append(f"Art direction: {profile.art_direction}.")
    if profile.style_tags:
        parts.append("Project style tags: " + ", ".join(profile.style_tags) + ".")
    if profile.negative_style_constraints:
        parts.append("Negative style constraints (must avoid): "
                     + "; ".join(profile.negative_style_constraints) + ".")
    return " ".join(parts)


def append_style_instruction(domain_prompt: str,
                             profile: Optional[ProjectVisualProfile]) -> str:
    """域 prompt + 项目 style 后缀（后缀式，与 legacy StyleAnchor.suffix 同惯例）。
    profile None → 原样返回（unspecified 不产生空尾巴）。"""
    instruction = compile_project_style_instruction(profile) if profile is not None else ""
    instruction = instruction.strip()
    if not instruction:
        return domain_prompt
    return f"{domain_prompt} {instruction}" if domain_prompt else instruction


def compile_project_style_review_expectation(profile: Optional[ProjectVisualProfile]) -> str:
    """Vision Review 的项目风格期望段（P1 §15）。profile None → ""（unspecified 时
    project_style_consistency 不进入审查维度，不判 pass 也不判 fail）。
    与生成指令同源（render_family_instruction + art_direction + tags/negatives），
    但措辞是审查口径：不同介质 → project_style_consistency=fail（hard）。"""
    if profile is None:
        return ""
    lines = [
        "Project style expectation (dimension project_style_consistency):",
        "The project's declared visual production style is: "
        + render_family_instruction(profile.render_family),
    ]
    if profile.art_direction:
        lines.append(f"Declared art direction: {profile.art_direction}.")
    if profile.style_tags:
        lines.append("Declared style tags: " + ", ".join(profile.style_tags) + ".")
    if profile.negative_style_constraints:
        lines.append("Declared negative style constraints: "
                     + "; ".join(profile.negative_style_constraints) + ".")
    lines.append(
        "The candidate must visibly belong to this declared production style/medium. "
        "If it reads as a different medium (for example a photorealistic photograph when "
        "the project expects 2D animation), judge project_style_consistency as fail.")
    return "\n".join(lines)
