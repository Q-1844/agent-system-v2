"""提交生成前的确定性预检（Phase 39D / HANDOFF §3.3——prose 不够的地方给牙齿）。

prose 纪律（skill 包 full-story-relay-calibration §0）告诉 Agent 该怎么做；
本模块把两次实证事故（night_cloak、P39C s01：静态站姿身份参考 × 飞行类镜头
文本 → 模型把主体拽回参考姿态，镜内硬切 major）变成确定性检查：

**prompt-reference 姿态冲突预检**：BOUND 镜头 cast Look 的 approved 参考姿态
语义（ReferenceAsset.view / Look.label）与镜头动作语义（description/motion）
逐项对照——静态姿态参考 × 位移动作 = 结构化冲突。接线在
director_visual._execute_generation：bound_required 图片生成下直接阻断提交；
其余路径附结构化 warning 进回执（skill §0 要求收到预警停下处置）。

纯只读；任何解析失败/无 binding = 无冲突（预检自身绝不阻断生成）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 位移动作关键词（镜头动作侧）——保守词表：两字起步，避免"飞快""站起"误伤
_MOTION_LOCOMOTION_KEYWORDS = (
    "飞行", "飞翔", "翱翔", "飞离", "飞过", "飞向", "飞起", "腾空", "俯冲", "盘旋",
    "奔跑", "飞奔", "疾驰", "跳跃", "跃过", "跃起", "腾跃",
    "游动", "游泳", "游过", "潜游", "窜过", "掠过",
    "flying", "soaring", "gliding", "flapping", "hovering",
    "running", "sprinting", "galloping",
    "jumping", "leaping", "swimming", "diving",
)

# 静态姿态关键词（参考资产侧：ReferenceAsset.view / Look.label / 语义描述）
_STATIC_POSE_KEYWORDS = (
    "站姿", "站立", "端立", "伫立", "静立", "直立", "坐姿", "盘坐", "蹲坐", "栖息",
    "standing", "sitting", "perched",
)


def detect_pose_reference_conflict(
        motion_texts: List[str],
        ref_views: List[Tuple[str, str]]) -> Optional[Dict[str, Any]]:
    """纯函数：动作文本 × 参考姿态描述 → 结构化冲突 dict 或 None。

    motion_texts：镜头动作语义文本（description/motion…）；
    ref_views：[(reference_asset_id, 姿态语义文本)]。
    判定：动作命中位移动作词 且 参考命中静态姿态词 → 冲突；
    参考里有非静态条目时列入 motion_matched_reference_ids（38 walk_side
    先例的修复通道：用 video_reference_asset_id 指向动作匹配参考）。"""
    motion_blob = " ".join(t for t in (motion_texts or []) if t).lower()
    if not motion_blob:
        return None
    motion_hits = [k for k in _MOTION_LOCOMOTION_KEYWORDS if k in motion_blob]
    if not motion_hits:
        return None
    static_hits: Dict[str, List[str]] = {}
    non_static_ids: List[str] = []
    for rid, text in (ref_views or []):
        blob = (text or "").lower()
        if not blob:
            continue
        hits = [k for k in _STATIC_POSE_KEYWORDS if k in blob]
        if hits:
            static_hits[rid] = hits
        else:
            non_static_ids.append(rid)
    if not static_hits:
        return None
    return {
        "matched_motion_keywords": motion_hits,
        "static_reference_ids": sorted(static_hits),
        "static_pose_keywords": {r: v for r, v in static_hits.items()},
        "motion_matched_reference_ids": non_static_ids,
        "why": ("静态姿态身份参考 × 位移动作：provider 倾向把主体拽回参考姿态"
                "（镜内硬切，P39C s01 / night_cloak 实证）"),
    }


def shot_pose_reference_conflict(pdir: Any, project_name: str,
                                 shot: Any) -> Optional[Dict[str, Any]]:
    """BOUND 镜头的姿态冲突解析（只读）。无 binding / 无 approved 参考 /
    任何解析失败 → None（预检自身绝不阻断生成）。"""
    try:
        from src.shot_binding import ShotBindingService
        from src.resources.store import ResourceStore

        svc = ShotBindingService(Path(pdir), project_name)
        b = svc.read_binding(shot.shot_id)
        if not b:
            return None
        chars = (b.get("binding") or {}).get("character_bindings") or []
        if not chars:
            return None
        rdoc = ResourceStore(pdir, project_name).load()
        ref_views: List[Tuple[str, str]] = []
        for cb in chars:
            look = (getattr(rdoc, "looks", None) or {}).get(cb.get("look_id"))
            if look is None:
                continue
            for aid in (getattr(look, "approved_reference_asset_ids", None) or []):
                asset = (getattr(rdoc, "reference_assets", None) or {}).get(aid)
                if asset is None:
                    continue
                text = " ".join(filter(None, [
                    getattr(asset, "view", "") or "",
                    getattr(look, "label", "") or "",
                ]))
                ref_views.append((aid, text))
        if not ref_views:
            return None
        conflict = detect_pose_reference_conflict(
            [getattr(shot, "description", "") or "",
             getattr(shot, "motion", "") or ""],
            ref_views)
        if conflict is not None:
            conflict = {"shot_id": shot.shot_id,
                        "characters": [c.get("character_name")
                                       for c in chars if c.get("character_name")],
                        **conflict}
        return conflict
    except Exception:   # noqa: BLE001 —— 预检只读旁路，失败不阻断
        return None
