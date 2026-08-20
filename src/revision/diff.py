# src/revision/diff.py
# 结构化 Diff（§十九）。后端返回结构化，不让前端临时猜两个大 JSON。
# 必须按稳定 scene_id / shot_id 对齐，不靠数组 index（§十九）。
from __future__ import annotations
from typing import List


def _shot_summary(s) -> dict:
    return {"shot_id": s.shot_id, "description": s.description,
            "characters": list(s.characters), "target_duration_s": s.target_duration_s}


def _scene_summary(s) -> dict:
    return {"scene_id": s.scene_id, "summary": s.summary, "characters": list(s.characters)}


def _diff_shots(scene_id, bs, as_) -> List[dict]:
    out = []
    b = {s.shot_id: s for s in bs.shots}
    a = {s.shot_id: s for s in as_.shots}
    for shid in sorted(set(b) | set(a)):
        bsh, ash = b.get(shid), a.get(shid)
        if bsh and not ash:
            out.append({"kind": "delete", "entity": "shot", "id": shid, "parent_id": scene_id,
                        "before": _shot_summary(bsh)})
        elif ash and not bsh:
            out.append({"kind": "insert", "entity": "shot", "id": shid, "parent_id": scene_id,
                        "after": _shot_summary(ash)})
        elif bsh and ash:
            for f in ("description", "target_duration_s"):
                if getattr(bsh, f) != getattr(ash, f):
                    out.append({"kind": "update", "entity": "shot", "id": shid, "parent_id": scene_id,
                                "field": f, "before": getattr(bsh, f), "after": getattr(ash, f)})
            if list(bsh.characters) != list(ash.characters):
                out.append({"kind": "update", "entity": "shot", "id": shid, "parent_id": scene_id,
                            "field": "characters", "before": list(bsh.characters),
                            "after": list(ash.characters)})
    bo = [s.shot_id for s in bs.shots]
    ao = [s.shot_id for s in as_.shots]
    if set(bo) == set(ao) and bo != ao:
        out.append({"kind": "reorder", "entity": "scene", "id": scene_id, "parent_id": None,
                    "before_order": bo, "after_order": ao})
    return out


def diff_projects(before, after) -> List[dict]:
    """两个 Project 的结构化 diff，按 scene_id / shot_id 稳定对齐。
    AI-7B（§67-§69）：增 story_text + character diff（additive；既有 shot/scene op 不触 story/char）。"""
    out: List[dict] = []
    # §68 Story diff
    if before.story_text != after.story_text:
        out.append({"kind": "update", "entity": "story", "id": "story_text",
                    "parent_id": None, "field": "story_text",
                    "before": before.story_text, "after": after.story_text})
    # §69 Character diff（按 name 对齐）
    bchars = {c.name: c for c in before.characters}
    achars = {c.name: c for c in after.characters}
    for name in sorted(set(bchars) | set(achars)):
        bc, ac = bchars.get(name), achars.get(name)
        if ac and not bc:
            out.append({"kind": "insert", "entity": "character", "id": name, "parent_id": None,
                        "after": {"name": name, "description_en": ac.description_en}})
        elif bc and not ac:
            out.append({"kind": "delete", "entity": "character", "id": name, "parent_id": None,
                        "before": {"name": name, "description_en": bc.description_en}})
        elif bc and ac and bc.description_en != ac.description_en:
            out.append({"kind": "update", "entity": "character", "id": name, "parent_id": None,
                        "field": "description_en", "before": bc.description_en, "after": ac.description_en})
    b = {s.scene_id: s for s in before.scenes}
    a = {s.scene_id: s for s in after.scenes}
    for sid in sorted(set(b) | set(a)):
        bs, as_ = b.get(sid), a.get(sid)
        if bs and not as_:
            out.append({"kind": "delete", "entity": "scene", "id": sid, "parent_id": None,
                        "before": _scene_summary(bs)})
            continue
        if as_ and not bs:
            out.append({"kind": "insert", "entity": "scene", "id": sid, "parent_id": None,
                        "after": _scene_summary(as_)})
            continue
        if bs.summary != as_.summary:
            out.append({"kind": "update", "entity": "scene", "id": sid, "parent_id": None,
                        "field": "summary", "before": bs.summary, "after": as_.summary})
        if list(bs.characters) != list(as_.characters):
            out.append({"kind": "update", "entity": "scene", "id": sid, "parent_id": None,
                        "field": "characters", "before": list(bs.characters),
                        "after": list(as_.characters)})
        out.extend(_diff_shots(sid, bs, as_))
    return out


def changeset_diff(before_project, after_project) -> List[dict]:
    """当前这次 proposal 改了什么（§十九 ChangeSet Diff）。"""
    return diff_projects(before_project, after_project)


def workspace_aggregate_diff(base_project, working_project) -> List[dict]:
    """整个实验版本相对正式起点改了什么（§十九 Workspace Aggregate Diff）。
    base_project.json ↔ working_project.json。"""
    return diff_projects(base_project, working_project)
