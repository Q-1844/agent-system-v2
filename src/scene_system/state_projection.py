# src/scene_system/state_projection.py
# Scene System V1 — Phase S2B §6-§8：ViewRelevantSceneState（纯函数 derived projection）。
#
# 🔴 这是本轮核心设计：LocationView.visible_anchor_ids 决定当前画面理论上可见哪些空间
#    区域。SceneState 投影进某个 view 时：
#      - 全局状态（time_of_day/weather/lighting/environment_conditions）永远进入；
#      - anchor_states 只取 anchor_id ∈ visible_anchor_ids 的项；
#      - prop_placements 只取 placement.anchor_id ∈ visible_anchor_ids 的项。
#    原因：不可见区域发生变化不应让当前 view 无意义 stale（S2B §33 的核心价值——
#    south_stairs 损坏不应 invalidate 朝北的 state view）。
#
# 🔴 绝对不要持久化（§8）：projection 只是 derived 视图，每次 generation/review/read
#    重算；canonical 只有一份 = SceneInstance.state。避免双真相源。
#
# 🔴 SceneState.notes 不进入投影：notes 可能含非视觉叙事评注；只有结构化状态字段
#    进入视觉 basis（有意收窄，报告 deviations 说明）。
from __future__ import annotations
from typing import Any, Dict, Iterable, List, Optional

from .models import LocationView, SceneInstance, SpatialAnchor


def project_scene_state_for_view(scene_instance: SceneInstance,
                                 location_view: LocationView,
                                 anchors: Iterable[SpatialAnchor]) -> Dict[str, Any]:
    """SceneState → ViewRelevantSceneState（纯函数；同输入逐字段同输出）。
    anchors = 该 instance 所在 location 的全部 anchors（用于解析可见集）。
    返回 dict（可直接进 basis fingerprint 的组成部分）。"""
    state = scene_instance.state
    visible = set(location_view.visible_anchor_ids or [])
    anchor_states = {
        aid: {"condition": st.condition, "note": st.note}
        for aid, st in ((k, state.anchor_states.get(k)) for k in sorted(visible))
        if st is not None
    }
    prop_placements = {
        pid: {"anchor_id": p.anchor_id, "state": p.state, "note": p.note}
        for pid, p in sorted(state.prop_placements.items())
        if p.anchor_id in visible
    }
    return {
        "time_of_day": state.time_of_day,
        "weather": state.weather,
        "lighting": state.lighting,
        "environment_conditions": state.environment_conditions,
        "anchor_states": anchor_states,
        "prop_placements": prop_placements,
    }


def visible_anchor_snapshots(anchors: Iterable[SpatialAnchor],
                             location_view: LocationView) -> List[Dict[str, Any]]:
    """可见 anchor 的语义快照（basis 用；按 anchor_id 排序保确定）。"""
    visible = set(location_view.visible_anchor_ids or [])
    return [{"anchor_id": a.anchor_id, "name": a.name, "kind": a.kind,
             "description": a.description, "semantic_zone": a.semantic_zone}
            for a in sorted((a for a in anchors if a.anchor_id in visible),
                            key=lambda a: a.anchor_id)]
