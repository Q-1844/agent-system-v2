# src/agents/production_context.py
# Agent System V2 Phase C1 Work B（27.md §4）：共享 Production Context Reader。
#
# 目的：Director 需要读 ProjectVisualProfile / Resources / Scene System / Binding 的
# 生产事实，但【绝不 import Resource Agent 的 tool executor】（director.py 模块级
# import character_designer 执行器已是既成依赖，反向新增只读工具会加深环风险）。
# 本模块是极小的共享只读层：只做 canonical → 高信号 projection，零写入、零生成、
# 零审批；两个 Agent 都可以安全 import（只依赖 src.visual_profile / src.resources /
# src.scene_system / src.shot_binding / src.core 这些叶子域模块，不依赖任何 agents 模块）。
#
# 🔴 readiness/staleness 的唯一权威 = ShotBindingService.read_validity（复用既有
# fail-closed 读模型）——本层绝不重新实现一套 readiness 判定（27.md §6.6）。
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


# ============ ProjectVisualProfile projection（§5，与 Resource Agent 同 schema）============
def read_visual_profile_projection(pdir) -> dict:
    """ProjectVisualProfile 只读投影（高信号紧凑）。损坏 fail closed（ok=False）。
    与 character_designer.project.visual_profile.read 同 schema/行为——CD 工具委托本函数。"""
    from src.visual_profile import load_profile_or_none
    from src.visual_profile.models import (VisualProfileSchemaError,
                                           visual_profile_fingerprint,
                                           profile_revision)
    try:
        vp = load_profile_or_none(pdir)
    except VisualProfileSchemaError as e:
        return {"ok": False, "operation": "project.visual_profile.read",
                "error": "visual_profile_unreadable",
                "note": f"project_profile.json 损坏（fail closed）：{e}"}
    if vp is None:
        return {"ok": True, "operation": "project.visual_profile.read",
                "specified": False, "visual_profile": None,
                "fingerprint": "", "revision": "",
                "note": "项目未设置视觉定义。资源类视觉生成（Look/Composition）会被系统 "
                        "fail closed 拒绝；可建议用户在项目设置里配置。"}
    return {"ok": True, "operation": "project.visual_profile.read",
            "specified": True,
            "visual_profile": vp.to_dict(),
            "fingerprint": visual_profile_fingerprint(vp),
            "revision": profile_revision(vp),
            "note": "项目视觉定义即全项目风格真相：生产推理以它为准；与它冲突时指出冲突。"}


def profile_summary_line(pdir) -> str:
    """system prompt 注入的最小 profile 摘要（一行）。unspecified/损坏 → 诚实状态。"""
    proj = read_visual_profile_projection(pdir)
    if not proj.get("ok"):
        return "读取失败（project_profile.json 损坏？）"
    if not proj.get("specified"):
        return ("未设置（unspecified）。资源类视觉生成（Look/Composition）会因无风格真相 "
                "fail closed")
    vp = proj["visual_profile"]
    tags = ", ".join((vp.get("style_tags") or [])[:6]) if vp.get("style_tags") else "-"
    neg = "; ".join((vp.get("negative_style_constraints") or [])[:4]) \
        if vp.get("negative_style_constraints") else "-"
    return (f"render_family={vp.get('render_family')}；"
            f"art_direction={(vp.get('art_direction') or '-')[:80]}；"
            f"style_tags={tags}；负面约束={neg}。全文用 project.visual_profile.read")


# ============ Resource production projection（§6.3/§6.4）============
def approved_looks_for_characters(pdir, character_names: List[str]) -> List[dict]:
    """cast 相关的 approved Look 选项（§6.3：只返回当前 Shot cast 相关资源，
    不把项目所有 Look 塞进 context）。usable = approved 且有 approved reference；
    更深 staleness 由 binding validity 在 bind/refresh 时 fail-closed 判定。"""
    from src.resources.store import ResourceStore
    from src.resources.models import look_revision
    doc = ResourceStore(pdir, Path(pdir).name).load()
    wanted = {n for n in (character_names or [])}
    out: List[dict] = []
    for lk in doc.looks.values():
        if lk.character_name not in wanted:
            continue
        if lk.approval_status != "approved" or not lk.approved_reference_asset_ids:
            continue
        out.append({
            "look_id": lk.look_id,
            "character_name": lk.character_name,
            "label": lk.label,
            "approval_status": lk.approval_status,
            "generation_status": lk.generation_status,
            "approved_reference_asset_ids": list(lk.approved_reference_asset_ids),
            "clothing_id": lk.clothing_id,
            "look_revision": look_revision(lk),
            "usable": True,
        })
    return out


def character_identity_reference_state(pdir, character_name: str) -> dict:
    """角色 identity reference（legacy 三视图）可用性（§6.3）。"""
    from src.core.store import ProjectStore
    proj = ProjectStore(pdir).load()
    char = proj.get_character(character_name)
    if char is None:
        return {"character_name": character_name, "exists": False,
                "identity_reference": "missing"}
    done = [v for v, a in (char.views or {}).items()
            if a is not None and getattr(a, "status", None) == "done"]
    return {"character_name": character_name, "exists": True,
            "identity_reference": "available" if len(done) == 3 else
                                  (f"partial({len(done)}/3)" if done else "missing")}


def approved_props(pdir) -> List[dict]:
    """项目 approved Prop 选项（§6.4：只在有可靠来源时返回）。"""
    from src.resources.store import ResourceStore
    doc = ResourceStore(pdir, Path(pdir).name).load()
    out: List[dict] = []
    for p in doc.props.values():
        if p.approval_status != "approved" or not p.approved_reference_asset_ids:
            continue
        out.append({"prop_id": p.prop_id, "name": p.name,
                    "approved_reference_asset_ids": list(p.approved_reference_asset_ids),
                    "usable": True})
    return out


# ============ Scene production projection（§6.5）============
def scene_production_projection(pdir, story_scene_id: str) -> dict:
    """Story Scene → 生产场景上下文（§6.5）。无 Instance → scene_instance_missing；
    有 Instance 无 approved Sciv → scene_view_missing。approved Sciv 的可用性/staleness
    最终以 binding validity 为准（本层不重算）。"""
    from src.scene_system.store import SceneSystemStore
    doc = SceneSystemStore(pdir, Path(pdir).name).load()
    inst = next((i for i in doc.scene_instances.values()
                 if i.story_scene_id == story_scene_id and i.status == "active"), None)
    if inst is None:
        return {"story_scene_id": story_scene_id, "scene_instance": None,
                "reason": "scene_instance_missing",
                "note": "该故事段还没有 SceneInstance（场景生产准备缺失）——不能建场景 binding。"}
    location = doc.locations.get(inst.location_id)
    state = inst.state
    state_summary = {
        "time_of_day": getattr(state, "time_of_day", ""),
        "weather": getattr(state, "weather", ""),
        "lighting": getattr(state, "lighting", ""),
        "notes": (getattr(state, "notes", "") or "")[:80],
    }
    scivs: List[dict] = []
    for v in doc.scene_instance_views.values():
        if v.scene_instance_id != inst.scene_instance_id:
            continue
        if v.approval_status != "approved" or not v.reference_asset_id:
            continue
        lv = doc.views.get(v.location_view_id)
        scivs.append({
            "scene_instance_view_id": v.scene_instance_view_id,
            "location_view_id": v.location_view_id,
            "camera_zone": (lv.camera_zone if lv else "") or "",
            "look_direction": (lv.look_direction if lv else "") or "",
            "label": v.label,
            "reference_asset_id": v.reference_asset_id,
            "approval_status": v.approval_status,
        })
    out = {"story_scene_id": story_scene_id,
           "scene_instance": {"scene_instance_id": inst.scene_instance_id,
                              "location_id": inst.location_id,
                              "location_name": (location.name if location else "") or "",
                              "state_summary": state_summary},
           "approved_scene_instance_views": scivs,
           "reason": None}
    if not scivs:
        out["reason"] = "scene_view_missing"
        out["note"] = ("SceneInstance 存在但没有任何 approved SceneInstanceView"
                       "——不能建场景 binding（场景生产准备未完成）。")
    return out


# ============ Binding projection（§6.6）============
def binding_projection(pdir, project_name: Optional[str], shot_id: str) -> dict:
    """Binding 状态投影：复用 ShotBindingService.read_validity（唯一 readiness 权威）+
    read_binding（Intent 明细/revision）。UNBOUND 不是错误。"""
    from src.shot_binding.service import ShotBindingService
    svc = ShotBindingService(pdir, project_name or Path(pdir).name)
    validity = svc.read_validity(shot_id)
    binding = svc.read_binding(shot_id)
    return {"validity": validity,
            "binding": (binding or {}).get("binding"),
            "binding_revision": (binding or {}).get("binding_revision"),
            "document_revision": (binding or {}).get("document_revision")
                                 or validity.get("document_revision")}
