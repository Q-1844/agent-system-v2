# src/agents/change_protocol/conflicts.py
# AI-7B 确定性 ConflictAnalyzer（§33）。提案间结构冲突检测——不靠 Producer prompt（§33）。
#
# severity（§33）：
#   hard           —— 不可同时执行（delete+modify 同实体 / 同字段不相容后值 / 删场景+改其内镜头 /
#                    script 移除角色+director 保留该角色镜头）
#   needs_review  —— 非硬但 Producer 应复核（同实体多 item 不同字段）
#   creative_choice —— 代码不能定艺术答案（§34），不经此层；由 proposal.user_decisions_needed
#                    + Producer LLM 在编译/服务层升级为 ChangeDecisionRequest。
#
# 不自动消除冲突（§32/§46）：hard 未解决 → plan 不可 preview_ready；交 Producer 定向 round 2 或 user。
from __future__ import annotations
import secrets
from typing import List

from .models import ModuleProposal, ModuleProposalItem


def _cid() -> str:
    return "cfl_" + secrets.token_hex(4)


def _eref(module: str, etype: str, eid: str) -> dict:
    return {"module": module, "entity_type": etype, "entity_id": eid}


def _entity_key(module: str, item: ModuleProposalItem) -> tuple:
    """返回 item 主目标实体 (etype, eid)，None 表示无具体实体（如 update_story_text/insert）。"""
    intent = item.intent or {}
    if module == "script":
        a = item.action
        if a == "update_story_text":
            return ("story", "story_text")
        sid = intent.get("scene_id") or intent.get("after_scene_id")
        return ("scene", sid) if sid else None
    if module == "character":
        name = intent.get("name")
        return ("character", name) if name else None
    if module == "storyboard":
        shid = intent.get("shot_id")
        if shid:
            return ("shot", shid)
        sid = intent.get("scene_id")
        return ("scene", sid) if sid else None
    return None


def _writes_field(module: str, item: ModuleProposalItem) -> tuple:
    """item 写入的 (entity_key, field_name, value) 三元组列表（用于同字段后值冲突，§33）。"""
    intent = item.intent or {}
    out = []
    if module == "script":
        if item.action == "update_story_text":
            out.append((("story", "story_text"), "new_story_text", intent.get("new_story_text")))
        elif item.action == "update_scene":
            sid = intent.get("scene_id")
            for f in ("new_summary", "characters"):
                if f in intent:
                    out.append((("scene", sid), f, intent.get(f)))
    elif module == "character":
        if item.action in ("update_character", "insert_character"):
            name = intent.get("name")
            if "description_en" in intent:
                out.append((("character", name), "description_en", intent.get("description_en")))
    elif module == "storyboard":
        if item.action == "update_shot":
            shid = intent.get("shot_id")
            for f in ("description", "characters", "target_duration_s"):
                if f in intent:
                    out.append((("shot", shid), f, intent.get(f)))
    return out


def analyze_conflicts(proposals: List[ModuleProposal], project=None) -> List[dict]:
    """§33：提案间结构冲突 → ProposalConflict[]。"""
    conflicts: List[dict] = []
    # 收集所有 item 的 (proposal_id, module, item)
    flat = []  # (proposal_id, proposal_item_id, module, item)
    for prop in proposals:
        for item_dict in prop.items:
            flat.append((prop.proposal_id, item_dict.get("item_id", ""),
                         prop.module, ModuleProposalItem.from_dict(item_dict)))

    # 1) 同实体的 delete + modify → hard (DELETE_VS_MODIFY)
    # 2) 同实体多 item 不同字段 → needs_review (OVERLAP)
    by_entity: dict = {}
    delete_scenes = set()
    for prop_id, item_id, module, item in flat:
        ek = _entity_key(module, item)
        if ek:
            by_entity.setdefault(ek, []).append((prop_id, item_id, module, item))
        # 记录被删场景
        if module == "script" and item.action == "delete_scene":
            sid = (item.intent or {}).get("scene_id")
            if sid:
                delete_scenes.add(sid)

    for ek, entries in by_entity.items():
        actions = [it.action for *_p, it in entries]
        has_delete = any(a in ("delete_scene", "delete_character", "delete_shot")
                         for a in actions)
        modifiers = [e for e in entries if e[3].action not in
                     ("delete_scene", "delete_character", "delete_shot", "insert_scene",
                      "insert_character", "insert_shot")]
        if has_delete and modifiers:
            conflicts.append({"conflict_id": _cid(), "severity": "hard",
                "proposal_item_ids": [e[1] for e in entries if e[1]],
                "entity_refs": [_eref(ek[0], ek[0], ek[1])],
                "reason_code": "DELETE_VS_MODIFY",
                "summary": f"{ek[0]} {ek[1]} 同时被删除与修改（互斥）"})
        elif len(modifiers) > 1:
            # 同实体多 modify（非字段冲突）→ needs_review
            fields = []
            for e in modifiers:
                fields += [fw[1] for fw in _writes_field(e[2], e[3])]
            if len(set(fields)) > 1 or len(fields) != len(set((f, ) for f in fields)):
                # 3) 同字段不同后值 → hard (FIELD_CONFLICT)
                fgroups = {}
                for e in modifiers:
                    for ent, f, v in _writes_field(e[2], e[3]):
                        fgroups.setdefault((ent, f), []).append((e[1], v))
                for (ent, f), writers in fgroups.items():
                    vals = [str(w[1]) for w in writers]
                    if len(set(vals)) > 1:
                        conflicts.append({"conflict_id": _cid(), "severity": "hard",
                            "proposal_item_ids": [w[0] for w in writers],
                            "entity_refs": [_eref(ent[0], ent[0], ent[1])],
                            "reason_code": "FIELD_CONFLICT",
                            "summary": f"{ent[0]} {ent[1]}.{f} 有不相容后值"})
            else:
                conflicts.append({"conflict_id": _cid(), "severity": "needs_review",
                    "proposal_item_ids": [e[1] for e in modifiers],
                    "entity_refs": [_eref(ek[0], ek[0], ek[1])],
                    "reason_code": "OVERLAP",
                    "summary": f"多 item 修改同一 {ek[0]} {ek[1]}（需复核）"})

    # 4) 删场景 + 修改其内镜头 → hard (DELETE_VS_DEPENDENT)
    if delete_scenes:
        for prop_id, item_id, module, item in flat:
            if module == "storyboard" and item.action in ("update_shot", "delete_shot", "reorder_shot"):
                sid = (item.intent or {}).get("scene_id")
                shid = (item.intent or {}).get("shot_id")
                if sid in delete_scenes:
                    conflicts.append({"conflict_id": _cid(), "severity": "hard",
                        "proposal_item_ids": [item_id],
                        "entity_refs": [_eref("shot", "shot", shid or sid),
                                         _eref("scene", "scene", sid)],
                        "reason_code": "DELETE_VS_DEPENDENT",
                        "summary": f"故事段 {sid} 被删除，但其内镜头 {shid} 被修改（互斥）"})

    # 5) CAST_CONFLICT：script 从场景 cast 移除角色 + director 在该场景某镜头保留该角色 → hard
    #   （script update_scenecharacters 减去 X；director update_shot in same scene 含 X）
    if project is not None:
        _cast_conflicts(flat, project, conflicts)
    return conflicts


def _cast_conflicts(flat, project, conflicts):
    """§32/§33：script 从 scene cast 移除角色 X，而 director 在该 scene 某镜头保留 X → hard。"""
    # script update_scene with characters not containing a previously-in-cast char
    scene_removed = {}   # scene_id -> [char names removed from cast]
    for _pid, item_id, module, item in flat:
        if module == "script" and item.action == "update_scene":
            sid = (item.intent or {}).get("scene_id")
            new_chars = (item.intent or {}).get("characters")
            if sid and new_chars is not None:
                sc = next((s for s in project.scenes if s.scene_id == sid), None)
                if sc:
                    removed = [c for c in sc.characters if c not in new_chars]
                    if removed:
                        scene_removed.setdefault(sid, []).extend(removed)
    if not scene_removed:
        return
    for _pid, item_id, module, item in flat:
        if module == "storyboard" and item.action == "update_shot":
            sid = (item.intent or {}).get("scene_id")
            chars = (item.intent or {}).get("characters")
            if sid in scene_removed and chars:
                for c in chars:
                    if c in scene_removed[sid]:
                        conflicts.append({"conflict_id": _cid(), "severity": "hard",
                            "proposal_item_ids": [item_id],
                            "entity_refs": [_eref("character", "character", c),
                                             _eref("scene", "scene", sid)],
                            "reason_code": "CAST_CONFLICT",
                            "summary": f"script 从 {sid} cast 移除 {c}，但 director 在其镜头保留 {c}"})
                        break


def has_unresolved_hard(conflicts: List[dict]) -> bool:
    return any(c["severity"] == "hard" for c in conflicts)
