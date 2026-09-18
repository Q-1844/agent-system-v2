# src/resources/look_prompt.py
# Production Resources V1 — Phase 1C1 Work C：资源域确定性 prompt builder。
#
# 🔴 目标（§15）：生成 clean reusable production reference（角色身份稳定 / 服装完整可读 /
#    无遮挡 / 设计信息清楚 / 适合作为后续 image+video reference）。
#    不引入 Shot 剧情 / cinematic camera motion / 复杂环境故事动作。
#
# 🔴 确定性：同输入 → 同输出（无时间/随机/LLM 参与）。prompt 会进 GenerationBasis
#    与 generation record（provenance），必须可复现。
#
# 内容审核注意（CLAUDE.md 实测坑）：Agnes 拒 "pure/solid white background" 组合，
# 使用已验证安全的 "pure white backdrop"（角色页既有用法）。


def _clean(text: str) -> str:
    """折叠空白（确定性规范化；同语义输入 → 同 prompt）。"""
    return " ".join((text or "").split())


def _outfit_phrase(clothing_name: str = "", clothing_description: str = "",
                   design_traits: str = "", materials=None, colors=None) -> str:
    parts: list = []
    if clothing_name:
        parts.append(f'"{_clean(clothing_name)}"')
    if clothing_description:
        parts.append(_clean(clothing_description))
    if design_traits:
        parts.append(_clean(design_traits))
    if materials:
        ms = _clean(", ".join(str(m) for m in materials))
        if ms:
            parts.append(f"materials: {ms}")
    if colors:
        cs = _clean(", ".join(str(c) for c in colors))
        if cs:
            parts.append(f"colors: {cs}")
    return ", ".join(parts)


def build_look_candidate_prompt(*, character_description: str,
                                clothing_name: str = "",
                                clothing_description: str = "",
                                clothing_design_traits: str = "",
                                clothing_materials=None,
                                clothing_colors=None,
                                appearance_description: str = "",
                                appearance_delta: str = "",
                                view_purpose: str = "") -> str:
    """构造 CharacterLook 候选生成的确定性 prompt。

    输入全部来自 GenerationBasis 快照（角色描述 / 服装描述 / 外观差异 / 目标视角），
    不做任何 LLM 调用、不读盘、不依赖环境——同 basis 必然同 prompt。
    """
    char_desc = _clean(character_description) or "the character"
    view = _clean(view_purpose).lower()
    view_line = (f"shown from the {view} angle" if view
                 else "front-facing view")

    lines: list = []

    lines.append(
        f"Production character reference illustration, single character, full body "
        f"head-to-toe with both feet visible in frame, standing in a neutral relaxed pose, "
        f"{view_line}."
    )
    lines.append(
        f"Character identity (match the attached reference images faithfully; keep the "
        f"face, hairstyle and body proportions exactly consistent): {char_desc}."
    )

    outfit = _outfit_phrase(clothing_name, clothing_description, clothing_design_traits,
                            clothing_materials, clothing_colors)
    if outfit:
        lines.append(
            f"Outfit (complete, exactly as specified, every design detail clearly visible): {outfit}."
        )

    app = _clean(appearance_description)
    if app:
        lines.append(f"Appearance details: {app}.")

    delta = _clean(appearance_delta)
    if delta:
        lines.append(f"Compared to the character's base appearance: {delta}.")

    lines.append(
        "Clean pure white backdrop, soft even studio lighting, no props, no other "
        "characters, no text, no watermark, no border."
    )
    lines.append(
        "Sharp focus, high detail, complete head-to-toe visibility with no occlusion, "
        "clear and readable design information, suitable as a reusable production "
        "reference for image and video generation."
    )

    return "\n".join(lines)
