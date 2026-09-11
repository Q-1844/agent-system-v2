# src/resources/composition_prompt.py
# Production Resources V1 — Phase 1C5：CharacterReferenceComposition 候选确定性 prompt builder（§11）。
#
# 🔴 独立 builder：不修改 look_prompt.py 的稳定行为。同输入 → 同输出（无时间/随机/LLM）；
#    prompt 进 Composition GenerationBasis 与 generation record（provenance），必须可复现。
#
# 🔴 定位（§11）：clean reusable composition reference——不是 Shot。
#    强调：角色身份保持 / Look 保持 / 每个 Prop 由正确人物以正确 anchor 呈现 /
#    物体不融合不串位 / required props 全部可见 / 中性背景 / 全身或足够看到交互区域 /
#    适合作为后续 Shot reference。
#    不引入：剧情动作 / 镜头运动 / 复杂场景 / 爆炸 / 电影式构图 / Scene lighting。
#
# 内容审核注意（CLAUDE.md 实测坑）：Agnes 拒 "pure/solid white background" 组合，
# 使用已验证安全的 "pure white backdrop"（角色页与 look prompt 既有用法）。
from __future__ import annotations

_RELATION_VERB = {
    "held": "held",
    "carried": "carried",
    "worn": "worn",
    "attached": "attached",
    "near": "placed close",
}

_ANCHOR_TEXT = {
    "right_hand": "in the character's RIGHT HAND",
    "left_hand": "in the character's LEFT HAND",
    "back": "on the character's back",
    "waist": "at the character's waist",
    "neck": "around the character's neck",
    "beside_character": "beside the character",
}


def _clean(text: str) -> str:
    """折叠空白（确定性规范化；同语义输入 → 同 prompt）。"""
    return " ".join((text or "").split())


def _binding_phrase(prop_name: str, prop_description: str, relation: str,
                    anchor: str, visual_state_note: str) -> str:
    verb = _RELATION_VERB.get(relation, "held")
    anchor_text = _ANCHOR_TEXT.get(anchor, "beside the character")
    name = _clean(prop_name) or "the prop"
    desc = _clean(prop_description)
    phrase = f"{name} ({desc})" if desc else name
    line = f"The {phrase} must appear {verb} {anchor_text}"
    note = _clean(visual_state_note)
    if note:
        line += f" (state: {note})"
    return line + "."


def build_composition_prompt(*, character_description: str,
                             look_clothing_snapshot: dict | None = None,
                             look_appearance_description: str = "",
                             look_appearance_delta: str = "",
                             bindings: list | None = None,
                             pose_note: str = "",
                             composition_description: str = "") -> str:
    """构造 Composition 候选生成的确定性 prompt。

    输入全部来自 Composition GenerationBasis 快照（角色描述 / Look 服装快照 / 外观字段 /
    bindings 的 prop+relation+anchor+state / pose_note / composition_description）。
    bindings 每项：{prop_name, prop_description, relation, anchor, visual_state_note}。
    """
    char_desc = _clean(character_description) or "the character"
    cl = look_clothing_snapshot or {}
    outfit_bits = ", ".join(x for x in (
        _clean(cl.get("name") or ""), _clean(cl.get("description") or ""),
        _clean(cl.get("design_traits") or "")) if x)
    if cl.get("materials"):
        ms = _clean(", ".join(str(m) for m in cl["materials"]))
        if ms:
            outfit_bits += f", materials: {ms}"
    if cl.get("colors"):
        cs = _clean(", ".join(str(c) for c in cl["colors"]))
        if cs:
            outfit_bits += f", colors: {cs}"

    lines: list = []
    lines.append(
        "Production character composition reference illustration, single character, "
        "full body, standing pose, clean pure white backdrop."
    )
    lines.append(
        f"Character identity (match the attached approved character look reference images "
        f"faithfully; keep the face, hairstyle and body proportions exactly consistent): "
        f"{char_desc}."
    )
    if outfit_bits:
        lines.append(
            f"Outfit (exactly as in the approved look reference images, every design detail "
            f"clearly visible): {outfit_bits}."
        )
    app = _clean(look_appearance_description)
    if app:
        lines.append(f"Appearance details: {app}.")
    delta = _clean(look_appearance_delta)
    if delta:
        lines.append(f"Compared to the character's base appearance: {delta}.")

    bl = bindings or []
    if bl:
        lines.append("Props (each object must stay visually distinct and clearly identifiable; "
                     "objects must never merge, fuse or swap positions):")
        for b in bl:
            lines.append("- " + _binding_phrase(
                str(b.get("prop_name") or ""), str(b.get("prop_description") or ""),
                str(b.get("relation") or "held"), str(b.get("anchor") or "right_hand"),
                str(b.get("visual_state_note") or "")))
        required = [b for b in bl if str(b.get("required_visibility") or "required") == "required"]
        if required:
            names = ", ".join(_clean(str(b.get("prop_name") or "")) for b in required)
            lines.append(f"Every required prop ({names}) must be clearly visible in the image.")
    if _clean(pose_note):
        lines.append(f"Pose note: {_clean(pose_note)}.")
    if _clean(composition_description):
        lines.append(f"Composition intent: {_clean(composition_description)}.")

    lines.append(
        "Neutral simple studio setting, soft even lighting, no scene environment, no story "
        "action, no camera movement, no other characters, no text, no watermark, no border."
    )
    lines.append(
        "Sharp focus, high detail, full-body framing (or framing wide enough to show every "
        "prop interaction area clearly), suitable as an approved visual composition reference "
        "for downstream shot generation."
    )
    return "\n".join(lines)
