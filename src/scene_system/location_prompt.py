# src/scene_system/location_prompt.py
# Scene System V1 — Phase S2A §11/§12：确定性 LocationView prompt builder。
#
# 🔴 输出目标 = clean reusable environment reference（地点基础视觉身份，S2A §2/§29）：
#    强调 same location identity / architectural consistency / stable materials /
#    stable landmark positions / requested camera zone / requested look direction /
#    required anchors visible。
#
# 🔴 不加入（本轮是 Base Location identity，不是 SceneInstance State View）：
#    人物、剧情动作、特定 SceneState（天气/暴雨/损坏/破窗）、电影动作、
#    某 Shot 的 lighting——即使 S1 SceneState 已有这些字段。
#
# 🔴 SetLayout 的角色（§12）：layout hint。normalized x/y 只编译成
#    "north-west area of the room" 这类语义方位短语；anchor.relations 编译成
#    "X opposite Y" 语义关系。绝不声称 2D normalized 坐标能投影出精确透视——
#    它是 layout hint，不是 3D renderer。
#
# 🔴 确定性（T14）：同输入 → 逐字节同输出。无时间、无随机、无 LLM 参与；
#    排序一律按稳定 id；不依赖 dict 顺序。
from __future__ import annotations
from typing import List, Optional

from .models import Location, LocationView, SetLayout, SpatialAnchor

# 最大列举的 anchor 数（防超长 prompt；超出按 id 排序截断并注明——确定性）
_MAX_STRUCTURES = 12
_MAX_RELATIONS = 8


def _clean(s: Optional[str]) -> str:
    return " ".join(str(s or "").split())


def _xy_zone_words(x: Optional[float], y: Optional[float],
                   orientation_convention: str) -> str:
    """normalized [0,1] → 语义方位短语。仅当 orientation_convention == 'north_up'
    时才把 y 轴解释为南北（x: 0=west→1=east；y: 0=north→1=south）；
    其它 convention 只给 'central area' 兜底——不做无依据的方位臆断。"""
    if orientation_convention != "north_up":
        return "central area"
    if x is None and y is None:
        return "central area"
    ew = "west" if (x is not None and x < 0.34) else ("east" if (x is not None and x > 0.66) else "central")
    ns = "north" if (y is not None and y < 0.34) else ("south" if (y is not None and y > 0.66) else "central")
    if ew == "central" and ns == "central":
        return "central area"
    if ew == "central":
        return f"{ns}ern area"
    if ns == "central":
        return f"{ew}ern area"
    return f"{ns}-{ew} area"


def build_location_view_prompt(location: Location, anchors: List[SpatialAnchor],
                               layout: Optional[SetLayout], view: LocationView,
                               *, has_references: bool = False) -> str:
    """确定性编译 LocationView 生成 prompt（S2A §11）。
    anchors = 该 location 的全部 anchors（调用方保证；本函数按 id 排序使用）。
    has_references：本次生成是否附 approved LocationView 参考图（由参考选择决定，
    与选择结果一同记录进 basis；prompt 文本随之确定性变化）。"""
    loc_name = _clean(location.name)
    identity = _clean(location.identity_description) or _clean(location.description)
    style = _clean(location.style_notes)
    visible_ids = list(view.visible_anchor_ids or [])
    by_id = {a.anchor_id: a for a in anchors}
    visible = [by_id[i] for i in sorted(visible_ids) if i in by_id]
    others = sorted((a for a in anchors if a.anchor_id not in set(visible_ids)),
                    key=lambda a: a.anchor_id)

    parts: List[str] = []
    parts.append(
        "Clean reusable environment reference image of a single location, "
        "empty and neutral: no people, no characters, no props, no weather effects, "
        "no damage, no action, no dramatic lighting; neutral even illumination; "
        "production reference quality.")

    head = f"Location: {loc_name}" if loc_name else "Location"
    parts.append(f"{head}. {_clean(identity) or 'The location as specified by its reference materials.'}"
                 + (f" Style notes: {style}." if style else ""))

    if visible:
        lines = []
        for a in visible[:_MAX_STRUCTURES]:
            kind = _clean(a.kind) or "structure"
            desc = _clean(a.description)
            lines.append(f"- {_clean(a.name) or a.anchor_id} ({kind})"
                         + (f": {desc}" if desc else ""))
        parts.append("Key fixed structures that must appear exactly as specified:\n" + "\n".join(lines))

    if others:
        names = ", ".join(_clean(a.name) or a.anchor_id for a in others[:_MAX_STRUCTURES])
        parts.append(f"Other stable structures of the same location (context): {names}.")

    # 空间关系（anchor.relations，语义层）
    rel_lines: List[str] = []
    for a in sorted(anchors, key=lambda x: x.anchor_id):
        for r in a.relations:
            obj = by_id.get(r.object_anchor_id)
            if obj is None:
                continue
            rel_lines.append(f"the {_clean(a.name) or a.anchor_id} is {_clean(r.relation)} "
                             f"the {_clean(obj.name) or obj.anchor_id}")
            if len(rel_lines) >= _MAX_RELATIONS:
                break
        if len(rel_lines) >= _MAX_RELATIONS:
            break
    if rel_lines:
        parts.append("Spatial relations: " + "; ".join(rel_lines) + ".")

    # SetLayout → 语义方位 hint（§12：hint 不是 3D 投影）
    if layout is not None and layout.placements:
        conv = _clean(layout.orientation_convention)
        ph = []
        for p in sorted(layout.placements, key=lambda q: q.anchor_id):
            a = by_id.get(p.anchor_id)
            if a is None:
                continue
            if _clean(p.zone):
                ph.append(f"the {_clean(a.name) or a.anchor_id} is in the {_clean(p.zone)}")
            else:
                ph.append(f"the {_clean(a.name) or a.anchor_id} is in the "
                          f"{_xy_zone_words(p.x, p.y, conv)}")
        if ph:
            parts.append("Approximate layout hints (semantic orientation only, not a 3D projection): "
                         + "; ".join(ph) + ".")

    cam = []
    if _clean(view.camera_zone):
        cam.append(f"viewed from the {_clean(view.camera_zone)} of the space")
    if _clean(view.look_direction):
        cam.append(f"looking toward the {_clean(view.look_direction)}")
    if cam:
        parts.append("Camera: " + ", ".join(cam) + ".")

    if visible:
        parts.append("Clearly visible in frame: "
                     + ", ".join(_clean(a.name) or a.anchor_id for a in visible) + ".")

    parts.append(
        "Maintain the same location identity: architectural consistency, stable materials, "
        "stable landmark positions matching the specification"
        + (" and the approved reference images of this same location." if has_references else "."))
    return " ".join(p.strip() for p in parts if p.strip())
