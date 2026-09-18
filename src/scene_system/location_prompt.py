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

# 正交俯视机位词表（camera_zone 命中即走专用 top-down camera 句式；
# 未命中保持既有 "viewed from ... / looking toward ..." 通用句式逐字节不变，
# 以免改变存量 view 的 basis 指纹）。
_OVERHEAD_CAMERA_ZONES = {"overhead", "top_down", "top-down", "topdown", "above"}


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
                               *, has_references: bool = False,
                               reference_view_ids: Optional[List[str]] = None) -> str:
    """确定性编译 LocationView 生成 prompt（S2A §11）。
    anchors = 该 location 的全部 anchors（调用方保证；本函数按 id 排序使用）。
    has_references：本次生成是否附 approved LocationView 参考图（由参考选择决定，
    与选择结果一同记录进 basis；prompt 文本随之确定性变化）。
    reference_view_ids：本次实际参考的 view id 集合。若参考全部来自**其它** view
    （cross-view），插入"换机位构图"指令——参考图只作身份/材质/地标依据，
    构图必须来自本 view 的机位（真实项目实测：否则 i2i 把新机位锚死成参考图
    视角，"侧面"生成出与正面同构的图）。参考包含自身（同 view 重刷）时文案
    与旧版逐字节一致（重刷必须保持机位）。"""
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
        # 重名 anchor 去重：同名列出 N 次会被模型理解为 N 个实例
        # （实测：4 个重名 east_chimney anchor → 模型稳定画出双/多烟囱）。
        other_names: List[str] = []
        for a in others:
            n = _clean(a.name) or a.anchor_id
            if n not in other_names:
                other_names.append(n)
        names = ", ".join(other_names[:_MAX_STRUCTURES])
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
    cam_zone = _clean(view.camera_zone)
    look_dir = _clean(view.look_direction)
    cross_view = bool(reference_view_ids) and view.view_id not in set(reference_view_ids)
    if cam_zone.lower() in _OVERHEAD_CAMERA_ZONES:
        # 正交俯视机位：专用强指令句式（2026-09-17 实测两轮：
        # R1 通用句式把 look_direction 整句嵌进 "looking toward the ..." 产生病句，
        #    航拍先验把 "orthographic top-down" 稀释成 ~45° 斜拍，view_consistency=fail；
        # R2 中段强指令仍被航拍先验压过（高角 3/4，concern/fail）。
        # R3 对策：指令前移到 prompt 开头 + 换 "2D game map / floor plan" 正语义锚
        #    （训练数据里 map/floor-plan 天然是正俯视），并用正向描述替代否定句。）
        cam_parts = [
            "Viewpoint: nadir view looking straight down from directly overhead at a "
            "90-degree angle, as in satellite imagery or an architectural floor plan "
            "seen from above; the image is a flat vertical projection of the site: "
            "the roof plane fills the center seen from above, the fence and the path "
            "lie flat around it on the ground, and the ground fills the whole canvas "
            "to all four edges; no horizon, no sky, no oblique perspective; vertical "
            "walls and facades are not visible, only roofs and ground.",
        ]
        if look_dir:
            cam_parts.append(f"Top-down view notes: {look_dir}.")
        parts[1:1] = cam_parts   # 前移：紧随 clean-reference 引言之后，压过航拍先验
        if cross_view:
            parts.append(
                "The approved reference images show the SAME location from DIFFERENT camera "
                "positions: treat them strictly as identity, material, landmark and layout "
                "reference. Compose THIS image from the NEW camera position described above — "
                "a different physical position and a different viewing direction are REQUIRED; "
                "do NOT copy the reference camera angle or its perspective.")
    else:
        if cam_zone:
            cam.append(f"viewed from the {cam_zone} of the space")
        if look_dir:
            cam.append(f"looking toward the {look_dir}")
        if cam:
            parts.append("Camera: " + ", ".join(cam) + ".")
            if cross_view:
                parts.append(
                    "The approved reference images show the SAME location from DIFFERENT camera "
                    "positions: treat them strictly as identity, material, landmark and layout "
                    "reference. Compose THIS image from the NEW camera position described above — "
                    "a different physical position and a different viewing direction are REQUIRED; "
                    "do NOT copy the reference camera angle or its perspective.")

    if visible:
        vis_names: List[str] = []
        for a in visible:
            n = _clean(a.name) or a.anchor_id
            if n not in vis_names:
                vis_names.append(n)
        parts.append("Clearly visible in frame: " + ", ".join(vis_names) + ".")

    parts.append(
        "Maintain the same location identity: architectural consistency, stable materials, "
        "stable landmark positions matching the specification"
        + (" as seen from this new camera position (same spatial arrangement, not the same "
           "camera angle)" if cross_view else "")
        + (" and the approved reference images of this same location." if has_references else "."))
    if cam_zone.lower() in _OVERHEAD_CAMERA_ZONES:
        # R4：尾部二次强调（首尾呼应提升指令依从；仅 overhead 分支，非 overhead 逐字节不变）
        parts.append(
            "The entire image is seen straight from above, like a map lying flat on a table.")
    return " ".join(p.strip() for p in parts if p.strip())
