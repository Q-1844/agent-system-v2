# src/scene_system/instance_view_prompt.py
# Scene System V1 — Phase S2B §17/§18：SceneInstanceView 确定性 prompt builder。
#
# 🔴 六 SECTION（§17）：Preserve Base Location Identity / Requested View / Scene State /
#    Visible Anchor State Changes / Visible Prop Placements / Negative Constraints。
#    不修改 S2A Location base prompt 的稳定行为（独立 builder）。
#
# 🔴 Location identity 优先级（§18）：SceneState 是"状态变化"，不能重新设计 Location。
#    weather=storm 只能改变窗外天气/光线/水迹，不能把圆形控制室变成长方形仓库。
#
# 🔴 本轮无人物（§11）：即使 Story Scene.characters 非空也不加入——prompt 明确 no people。
#
# 🔴 确定性（同 S2A）：无时间/随机/LLM；排序按稳定 id；同输入逐字节同输出（T15）。
from __future__ import annotations
from typing import Any, Dict, List, Optional

from .models import Location, LocationView, SpatialAnchor


def _c(v: Any) -> str:
    return " ".join(str(v or "").split())


def build_scene_instance_view_prompt(location: Location,
                                     base_view: LocationView,
                                     anchors: List[SpatialAnchor],
                                     projection: Dict[str, Any],
                                     prop_semantics: List[Dict[str, Any]],
                                     *, has_peer_reference: bool = False,
                                     peer_note: str = "") -> str:
    """SceneInstanceView 生成 prompt（确定性）。
    projection = project_scene_state_for_view 的输出（ViewRelevantSceneState）。
    prop_semantics = [{prop_id, name, description, state, anchor_id, anchor_name,
                       visual_reference_missing}]（调用方构造；确定性排序后使用）。"""
    by_id = {a.anchor_id: a for a in anchors}
    parts: List[str] = []

    # SECTION 1 — Preserve Base Location Identity（§18：身份高于 SceneState 自由发挥）
    identity = _c(location.identity_description) or _c(location.description)
    parts.append(
        f"Preserve the approved {_c(location.name) or 'location'} exactly: {identity or 'as shown in the base reference image'}."
        + (f" Style: {_c(location.style_notes)}." if _c(location.style_notes) else ""))
    parts.append(
        "This is the SAME location as the base reference image. Do not change the "
        "architecture. Do not add, remove or relocate doors, windows, major landmarks "
        "or structures.")

    # SECTION 2 — Requested View
    cam = []
    if _c(base_view.camera_zone):
        cam.append(f"viewed from the {_c(base_view.camera_zone)} of the space")
    if _c(base_view.look_direction):
        cam.append(f"looking toward the {_c(base_view.look_direction)}")
    parts.append("Camera: " + (", ".join(cam) if cam else "same viewpoint as the base reference image") + ".")

    # SECTION 3 — Scene State（全局态）。🔴 显式覆盖指令：base 参考是中性日光态，
    # 模型倾向复制 base 光照——state 要求必须显式声明 override（E2E 实证弱点）。
    state_bits = [_c(projection.get(k)) for k in
                  ("time_of_day", "weather", "lighting", "environment_conditions")]
    state_bits = [b for b in state_bits if b]
    if state_bits:
        parts.append("Current scene state (OVERRIDE the base reference's daylight and "
                     "neutral lighting where the state differs): "
                     + ", ".join(state_bits) + ".")
    else:
        parts.append("Current scene state: unchanged from the base reference (neutral state).")

    # SECTION 4 — Visible Anchor State Changes（§19：正确 anchor 被改变）。
    # 🔴 附 anchor 的语义位置描述（如 "the middle of the three west windows"）——
    # E2E 实证：模型会把破损渲染在错误的山墙上（w01 而非 w02），仅凭 id/名字
    # 无法绑定到正确的那一扇；description 中的方位词是唯一位置锚。
    changed: List[str] = []
    unchanged_note: List[str] = []
    anchor_states = projection.get("anchor_states") or {}
    for aid in sorted(anchor_states):
        a = by_id.get(aid)
        name = _c(a.name) if a else aid
        desc = _c(a.description) if a else ""
        st = anchor_states[aid]
        line = f"{name.upper()} is {_c(st.get('condition')).upper()}"
        if _c(st.get("note")):
            line += f" ({_c(st['note'])})"
        if desc:
            line += f" — {desc}"
        changed.append(line + ".")
    for a in sorted((x for x in anchors if x.anchor_id in set(base_view.visible_anchor_ids or [])
                     and x.anchor_id not in set(anchor_states)), key=lambda x: x.anchor_id):
        unchanged_note.append(f"{_c(a.name) or a.anchor_id} keeps its base appearance")
    if changed:
        parts.append("Visible anchor state changes: " + " ".join(changed))
    if unchanged_note:
        parts.append("Anchor states with no specified change (keep exactly as in the base "
                     "reference): " + "; ".join(unchanged_note) + ".")

    # SECTION 5 — Visible Prop Placements
    if prop_semantics:
        prop_lines = []
        for p in sorted(prop_semantics, key=lambda q: q["prop_id"]):
            name = _c(p.get("name")) or p["prop_id"]
            desc = _c(p.get("description"))
            anchor_name = _c(p.get("anchor_name"))
            state = _c(p.get("state")) or "present"
            line = f"{name}" + (f" ({desc})" if desc else "") + f" — {state}"
            if anchor_name:
                line += f", located near the {anchor_name}"
            if p.get("visual_reference_missing"):
                line += " (no approved visual reference; render from this text description faithfully)"
            prop_lines.append(line + ".")
        parts.append("Visible prop placements: " + " ".join(prop_lines))

    # peer state view（§15：同一剧情时刻的其它方向 approved state view）
    if has_peer_reference:
        parts.append("A second reference image shows the SAME story moment of this location "
                     "from another direction: match its weather, lighting and damage style"
                     + (f" ({_c(peer_note)})." if _c(peer_note) else "."))

    # SECTION 6 — Negative Constraints
    parts.append(
        "No people, no characters, no animals. Do not add action or movement. "
        "The scene state changes conditions (light, weather, damage, objects), NOT the "
        "location identity: keep room shape, architecture, materials and all major "
        "landmarks exactly as in the base reference. Do not add or remove structures.")
    return " ".join(p.strip() for p in parts if p.strip())
