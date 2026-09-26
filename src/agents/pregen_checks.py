"""生成前输入语义预检（Phase 39D 修正版——图片角色分类 + 分角色检查口径）。

四类视觉输入角色（以真实 dispatch 语义为准——video_adapter.py / agnes_client.py）：
  reference_only        multi 直生镜参考槽（vref 未设 → approved Look hero）；
                        分镜图生成的 Look/场景 refs——身份/场景语义参考，
                        非 first-frame conditioning（33.md R3 配方）
  hard_start_boundary   i2v 首帧槽（selected first frame）/ keyframes first slot
  hard_end_boundary     keyframes last slot
  relay_start_boundary  multi vref=prev_tail / inherit_previous——上一镜真实
                        video_end_frame 作开场边界

分角色检查口径（P39D 用户定版）：
  reference_only        姿态词表命中 → soft 诊断 pose_reference_mismatch_soft；
                        🔴 绝不阻断/不要求补参考/不升级 major/不请用户改故事。
                        steering = prompt + negative_prompt 字段（约束生成结果，
                        不约束参考资产）。
  hard_start_boundary   站姿类参考资产被直接用作首帧 × 位移动作开场 =
                        boundary conflict（阻断级：换边界帧/换 mode/如实报告）。
  relay_*               不做姿态预判——连续性由生成后接缝测量校验。

纯只读；任何解析失败 = 无冲突（预检自身绝不阻断生成）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 位移动作关键词（镜头动作侧）——保守词表：两字起步，避免"飞快""站起"误伤
_MOTION_LOCOMOTION_KEYWORDS = (
    "飞行", "飞翔", "翱翔", "飞离", "飞过", "飞向", "飞起", "腾空", "俯冲", "盘旋",
    "滑翔", "振翅", "展翅", "掠水",
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

VIDEO_REFERENCE_RELAY = "prev_tail"   # 与 video_adapter 同哨兵


def classify_video_input_role(shot: Any,
                              relay: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """按真实 dispatch 语义分类本镜视频输入角色（纯函数，事实来自 shot 字段
    与 relay 解析结果）。返回 {generation_mode, opening_source, semantic_role,
    relay_source_shot, relay_source_version}。"""
    mode = ("keyframes" if str(getattr(shot, "link_mode", "") or "") == "keyframes"
            else "multi" if str(getattr(shot, "link_mode", "") or "") == "multi"
            else "i2v")
    role = {"generation_mode": mode,
            "opening_source": str(getattr(shot, "opening_source", "") or ""),
            "semantic_role": "reference_only",
            "relay_source_shot": None,
            "relay_source_version": None}
    if mode == "multi":
        if relay is not None:
            role["semantic_role"] = "relay_start_boundary"
            role["relay_source_shot"] = relay.get("source_shot_id")
            role["relay_source_version"] = relay.get("end_source_version")
        # multi 无 relay → 参考槽 = Look hero / 场景参考 = REFERENCE_ONLY
    elif mode == "i2v":
        role["semantic_role"] = "hard_start_boundary"
    elif mode == "keyframes":
        role["semantic_role"] = "hard_start_boundary+hard_end_boundary"
    return role


def detect_pose_reference_conflict(
        motion_texts: List[str],
        ref_views: List[Tuple[str, str]]) -> Optional[Dict[str, Any]]:
    """纯函数：动作文本 × 参考姿态描述 → 结构化冲突 dict 或 None。

    motion_texts：镜头动作语义文本（description/motion…）；
    ref_views：[(reference_asset_id, 姿态语义文本)]。
    判定：动作命中位移动作词 且 参考命中静态姿态词 → 冲突；
    参考里有非静态条目时列入 motion_matched_reference_ids（可选修复通道：
    仅在真实生成实证 pose dominance 后作为建议提出）。"""
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
        "why": ("REFERENCE_ONLY 参考为静态姿态 × 位移动作：provider 可能被参考"
                "姿态吸附（pose dominance 假设）——仅 soft 诊断，steering 走 "
                "prompt + negative_prompt，生成后按 actual tail 裁定"),
    }


def _approved_look_ref_views(pdir: Path, project_name: str,
                             shot: Any) -> List[Tuple[str, str]]:
    """BOUND 镜头 cast Looks 的 approved 参考姿态文本（只读）。无 binding /
    解析失败 → []。"""
    from src.shot_binding import ShotBindingService
    from src.resources.store import ResourceStore

    svc = ShotBindingService(Path(pdir), project_name)
    b = svc.read_binding(shot.shot_id)
    if not b:
        return []
    chars = (b.get("binding") or {}).get("character_bindings") or []
    if not chars:
        return []
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
    return ref_views


def shot_input_semantics(pdir: Any, project_name: str, project: Any,
                         shot: Any) -> Optional[Dict[str, Any]]:
    """本镜视频输入的完整语义画像（只读，供回执/trace 结构化记录）。

    返回 {shot_id, **role, input_images: [{asset_id, semantic_role}],
    pose_gate: {applied, outcome, reason}, soft_conflict?}；
    解析失败 → None（预检自身绝不阻断生成）。"""
    try:
        from src.generation.video_adapter import resolve_multi_relay_source

        role = classify_video_input_role(
            shot, resolve_multi_relay_source(project, shot))
        info: Dict[str, Any] = {"shot_id": shot.shot_id, **role,
                                "input_images": [], "pose_gate": {
                                    "applied": False, "outcome": "not_applicable",
                                    "reason": ""}}
        motion_texts = [getattr(shot, "description", "") or "",
                        getattr(shot, "motion", "") or ""]
        mode = role["generation_mode"]

        if mode == "multi":
            if role["semantic_role"] == "relay_start_boundary":
                # RELAY：参考槽 = 上一镜真实末帧；Look 不进 dispatch，不做姿态预判
                info["input_images"] = [{
                    "asset_id": f"video_end_frame:{role['relay_source_shot']}",
                    "semantic_role": "relay_start_boundary"}]
                info["pose_gate"] = {
                    "applied": False, "outcome": "relay_no_pose_gate",
                    "reason": ("relay 边界=上一镜实际视频末帧；连续性由生成后 "
                               "calibration.seam_measure 校验")}
                return info
            # REFERENCE_ONLY：Look hero 等参考进 multi 参考槽
            ref_views = _approved_look_ref_views(Path(pdir), project_name, shot)
            info["input_images"] = [{"asset_id": rid,
                                     "semantic_role": "reference_only"}
                                    for rid, _t in ref_views]
            conflict = detect_pose_reference_conflict(motion_texts, ref_views)
            if conflict is not None:
                info["soft_conflict"] = conflict
                info["pose_gate"] = {
                    "applied": True, "outcome": "soft_diagnostic",
                    "reason": ("pose_reference_mismatch_soft：reference-only "
                               "姿态词命中——不阻断，prompt+negative_prompt "
                               "steering，生成后按 actual tail 裁定")}
            else:
                info["pose_gate"] = {
                    "applied": True, "outcome": "no_conflict",
                    "reason": "reference-only 姿态词未命中"}
            return info

        # i2v / keyframes：首帧槽 = HARD_START_BOUNDARY
        boundary_url = _selected_first_frame_url(shot)
        ref_views = _approved_look_ref_views(Path(pdir), project_name, shot)
        boundary_ref_ids: List[str] = []
        if boundary_url:
            info["input_images"] = [{
                "asset_id": f"selected_first_frame:{boundary_url[:80]}",
                "semantic_role": "hard_start_boundary"}]
            # 确定性边界冲突仅一型：静态姿态参考资产被直接选作首帧
            from src.resources.store import ResourceStore
            rdoc = ResourceStore(Path(pdir), project_name).load()
            for rid, asset in (getattr(rdoc, "reference_assets", None) or {}).items():
                if boundary_url and (getattr(asset, "url", None) or "") == boundary_url:
                    boundary_ref_ids.append(rid)
        if boundary_ref_ids:
            conflict = detect_pose_reference_conflict(
                motion_texts, [(rid, "") for rid in boundary_ref_ids]
                + [(rid, t) for rid, t in ref_views if rid in boundary_ref_ids])
            if conflict is not None:
                info["boundary_conflict"] = conflict
                info["pose_gate"] = {
                    "applied": True, "outcome": "boundary_conflict",
                    "reason": ("hard_start_boundary 冲突：静态姿态参考资产被选作"
                               "硬首帧 × 位移动作开场——换边界帧/换 mode/如实报告")}
                return info
        info["pose_gate"] = {
            "applied": bool(boundary_url), "outcome": "no_conflict",
            "reason": "硬首帧非静态参考资产（或未选）——无确定性边界冲突"}
        return info
    except Exception:   # noqa: BLE001 —— 预检只读旁路，失败不阻断
        return None


def _selected_first_frame_url(shot: Any) -> str:
    sel = getattr(getattr(shot, "first_frame", None), "selected", None)
    return (getattr(sel, "url", None) or "") if sel is not None else ""


def shot_pose_reference_conflict(pdir: Any, project_name: str,
                                 shot: Any) -> Optional[Dict[str, Any]]:
    """兼容入口（P39D 前接口）：返回参考姿态冲突 dict 或 None。
    新代码请用 shot_input_semantics（角色化 + 口径分层）。"""
    try:
        ref_views = _approved_look_ref_views(Path(pdir), project_name, shot)
        if not ref_views:
            return None
        conflict = detect_pose_reference_conflict(
            [getattr(shot, "description", "") or "",
             getattr(shot, "motion", "") or ""],
            ref_views)
        if conflict is not None:
            conflict = {"shot_id": shot.shot_id, **conflict}
        return conflict
    except Exception:   # noqa: BLE001
        return None
