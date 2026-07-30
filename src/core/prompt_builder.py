# src/core/prompt_builder.py
# 分镜图 / 尾帧 / 视频 prompt 组装（PHASE_3_VIDEO_PIPELINE §3.2）。
# 🔴 风格锚点全局唯一：所有 prompt 末尾统一拼 project.style.suffix()，
#    这是全片风格一致的地基。
from __future__ import annotations
from .models import Project, Shot


def build_shot_prompt(project: Project, shot: Shot) -> str:
    """组装分镜图英文 prompt。
    结构：[主体] + [场景/环境] + [风格] + [光照] + [构图] + [质量要求]。
    用户手改过 shot.prompt_en 就直接用，不覆盖。"""
    if shot.prompt_en:
        base = shot.prompt_en
    else:
        chars = ", ".join(
            c.description_en for c in project.characters
            if c.name in shot.characters and c.description_en)
        base = ", ".join(p for p in [
            shot.description, chars, shot.shot_size,
            shot.camera, shot.lighting] if p)
    suffix = project.style.suffix()
    return f"{base}, {suffix}" if base else suffix


def build_i2i_prompt(project: Project, shot: Shot) -> str:
    """图生图 prompt：必须同时说明「改什么」和「保留什么」
    （Agnes 生图文档明确的最佳实践）。"""
    return (f"{build_shot_prompt(project, shot)}. "
            f"Keep the character identity, outfit, and art style from the "
            f"reference image strictly consistent.")


def build_last_frame_prompt(project: Project, shot: Shot) -> str:
    """尾帧 = 首帧 + 动作推进后的状态。强调构图与角色不变。"""
    motion = shot.motion or "the action continues naturally"
    return (f"Same scene and same character as the reference image, "
            f"but after this motion: {motion}. "
            f"Preserve the original composition, camera angle, lighting, "
            f"character identity and outfit. {project.style.suffix()}")


def build_video_prompt(shot: Shot, mode: str) -> str:
    """视频 prompt。
    i2v 要说明「什么动、什么不动」；keyframes 要说明「两帧之间怎么过渡」。"""
    motion = shot.motion or "subtle natural motion"
    if mode == "keyframes":
        return (f"Create a smooth cinematic transition from the first keyframe "
                f"to the second keyframe. {motion}. Maintain character identity, "
                f"consistent camera angle and natural motion between frames.")
    return (f"Animate this image: {motion}. {shot.camera or 'subtle camera movement'}. "
            f"Keep the character's face, outfit and the overall composition "
            f"consistent and stable.")
