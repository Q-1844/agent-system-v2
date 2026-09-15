# src/core/prompt_builder.py
# 首帧 / 尾帧 / 视频 prompt 组装（提示词.md §9/§11/§13）。
# 🔴 三段独立 builder，不再共用一份 prompt_en。
#    首帧/尾帧 prompt 末尾统一拼 project.style.suffix()（视频继承自输入帧，不拼后缀）。
#    优先级：*_prompt_override > *_prompt_en（生成/兼容）> 结构字段拼接（fallback，可能中英混合）。
#    只拼接非空字段；无角色不产生角色一致性虚假要求；seed/negative 不入正向 prompt。
from __future__ import annotations
from .models import Project, Shot


def _with_suffix(base: str, suffix: str) -> str:
    """追加风格后缀一次。base 空返回 suffix；suffix 空返回 base。"""
    if not suffix:
        return base
    if not base:
        return suffix
    return f"{base}, {suffix}"


def _join(parts) -> str:
    """非空项用 ', ' 连接，去掉首尾空白。"""
    return ", ".join(p.strip() for p in parts if p and str(p).strip())


def _char_identity(project: Project, shot: Shot) -> str:
    """出场角色的英文身份描述（description_en）。无角色返回空串。
    不机械重复角色名，不生成"保持一致"虚假要求。"""
    return _join(c.description_en for c in project.characters
                 if c.name in shot.characters and c.description_en)


def build_first_frame_prompt(project: Project, shot: Shot,
                             *, style_suffix: Optional[str] = None) -> str:
    """首帧英文 prompt（提示词.md §9）。
    优先级：first_frame_prompt_override > prompt_en（兼容）> 结构字段拼接。
    顺序：角色身份 -> 起始画面 -> 主体起始状态 -> 景别 -> 机位角度 -> 光线
          -> 构图 -> 重要细节 -> 必须出现的元素 -> [风格后缀]。
    camera_angle 是新字段（与运镜分离），不回退旧 camera（旧 camera 拆分到 video 运镜）。
    style_suffix（S3C.1）：None=legacy 行为（拼 project.style.suffix()）；
    传 str 则用之（""=不拼后缀）——Binding-aware 生成路径用 Pack 的 VisualProfile
    风格指令替代 StyleAnchor（§19），creative 拼接逻辑零改动。"""
    suffix = project.style.suffix() if style_suffix is None else style_suffix
    if shot.first_frame_prompt_override:
        return _with_suffix(shot.first_frame_prompt_override.strip(), suffix)
    if shot.prompt_en:
        return _with_suffix(shot.prompt_en.strip(), suffix)
    parts = [
        _char_identity(project, shot),
        shot.description,
        shot.opening_state,
        shot.shot_size,
        shot.camera_angle,
        shot.lighting,
        shot.composition,
        shot.important_details,
        ", ".join(shot.required_elements) if shot.required_elements else "",
    ]
    return _with_suffix(_join(parts), suffix)


def build_last_frame_prompt(project: Project, shot: Shot,
                            *, style_suffix: Optional[str] = None) -> str:
    """尾帧英文 prompt（提示词.md §11）。基于已选首帧。
    优先级：last_frame_prompt_override > last_frame_prompt_en > 结构字段拼接。
    无角色时只声明基于首帧，不报错。frame_change 空时回退 motion（§14 兼容规则 7）。
    style_suffix（S3C.2）：None=legacy 行为（拼 project.style.suffix()）；
    传 str 则用之（""=不拼后缀）——Binding-aware 尾帧路径用 Pack 的 VisualProfile
    风格指令替代 StyleAnchor（同 S3C.1 首帧 seam 模式），creative 拼接逻辑零改动。"""
    suffix = project.style.suffix() if style_suffix is None else style_suffix
    if shot.last_frame_prompt_override:
        return _with_suffix(shot.last_frame_prompt_override.strip(), suffix)
    if shot.last_frame_prompt_en:
        return _with_suffix(shot.last_frame_prompt_en.strip(), suffix)
    parts = ["Based on the first reference image"]
    change = shot.frame_change or shot.motion
    if change:
        parts.append(f"after this change: {change}")
    parts.append(shot.last_frame_description)
    parts.append(shot.end_state)
    if shot.add_elements:
        parts.append(f"add: {shot.add_elements}")
    if shot.remove_elements:
        parts.append(f"remove: {shot.remove_elements}")
    parts.append(shot.end_composition)
    if shot.preserve_elements:
        parts.append("preserve: " + ", ".join(shot.preserve_elements))
    parts.append(shot.preserve_custom)
    if shot.characters:
        parts.append("keep character identity consistent with the reference images")
    return _with_suffix(_join(parts), suffix)


def build_video_prompt(shot: Shot, mode: str, *, style_suffix: str = "") -> str:
    """视频英文 prompt（提示词.md §13）。区分 i2v / keyframes（none 由 handler 映射为 i2v）。
    优先级：video_prompt_override > video_prompt_en > 结构字段拼接。
    seed/negative_prompt 不拼入正向 prompt（单独 API 参数）。不拼风格后缀（继承自输入帧）。
    无角色时不出现 face/outfit consistency。camera_movement 空时回退 camera（§14 规则 6）。
    固定镜头不自动追加 push-in/tracking（直接用字段值）。
    style_suffix（S3C.2）：默认 ""（legacy 行为逐字节不变——legacy 视频从不拼后缀）；
    Binding-aware 路径显式传 Pack 生产上下文+VisualProfile 指令（§13 seam；
    legacy StyleAnchor 本就不进入视频 prompt，无需 fallback 抑制）。"""
    prompt = _video_prompt_core(shot, mode)
    return _with_suffix(prompt, style_suffix) if style_suffix else prompt


def _video_prompt_core(shot: Shot, mode: str) -> str:
    if shot.video_prompt_override:
        return shot.video_prompt_override.strip()
    if shot.video_prompt_en:
        return shot.video_prompt_en.strip()
    motion = shot.motion or "subtle natural motion"
    cam = shot.camera_movement or shot.camera
    pace = shot.motion_pace
    env = shot.environment_motion
    seq = shot.action_sequence
    preserve = shot.video_preserve_elements
    preserve_custom = shot.video_preserve_custom
    has_char = bool(shot.characters)

    if mode == "keyframes":
        parts = ["Create a smooth cinematic transition from the first keyframe to the second keyframe"]
        parts.append(shot.transition_description or f"with this motion: {motion}")
        if env:
            parts.append(f"environment: {env}")
        if cam:
            parts.append(f"camera: {cam}")
        if pace:
            parts.append(f"pace: {pace}")
        if seq:
            # C2（22.md §7）：canonical 动作顺序不得在编译中弱化——原文 verbatim
            # 保留，只加强时序框架（绝不发明/改写动作内容，22.md §8）。
            parts.append(f"The actions must unfold in this exact order: {seq}")
        consist = (["character identity"] if has_char else []) + ["scene layout"]
        parts.append("maintain consistency in " + ", ".join(consist))
        if preserve:
            parts.append("keep stable: " + ", ".join(preserve))
        if preserve_custom:
            parts.append(preserve_custom)
        return _join(parts)

    # i2v（含 none，handler 已把 none 映射为 i2v）
    parts = [f"Animate this image: {motion}"]
    if env:
        parts.append(f"environment motion: {env}")
    if cam:
        parts.append(f"camera movement: {cam}")
    if pace:
        parts.append(f"pace: {pace}")
    if seq:
        # C2（22.md §7）：canonical 动作顺序不得在编译中弱化——原文 verbatim
        # 保留，只加强时序框架（绝不发明/改写动作内容，22.md §8）。
        parts.append(f"The actions must unfold in this exact order: {seq}")
    if preserve:
        parts.append("keep stable: " + ", ".join(preserve))
    if preserve_custom:
        parts.append(preserve_custom)
    parts.append("keep character identity and outfit consistent" if has_char
                 else "keep the overall composition and style consistent")
    return _join(parts)
