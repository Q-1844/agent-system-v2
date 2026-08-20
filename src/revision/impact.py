# src/revision/impact.py
# Deterministic Impact Engine（§三十一~§四十二, §八十二）。
# 纯代码，不调 LLM、不放 Vue、不让 Agent 自己猜（§三十一）。
# 回答"什么需要重新检查/重新生产"，绝不自动删除历史资产（§三十四）。保守优于精确（§八十三）。
# 产出 categorized id-lists（§三十二）+ per-item reason codes（§八十二）+ §三十三 summary。
from __future__ import annotations
from typing import List

from .models import (ImpactReport, ImpactItem, EntityRef, ChangeOperation, BeforeAfter, NewShotSpec,
                     ImpactReasonCode as RC, OpType as OT, EntityType as ET)


def _find_scene(project, scene_id):
    return next((s for s in project.scenes if s.scene_id == scene_id), None)


def _neighbor_ids(scene, shot_id) -> List[str]:
    """基于 before_project 的数组邻接（接力来源=数组邻接位置，§三十五/§三十六）。"""
    ids = [s.shot_id for s in scene.shots]
    if shot_id not in ids:
        return []
    i = ids.index(shot_id)
    out = []
    if i > 0:
        out.append(ids[i - 1])
    if i < len(ids) - 1:
        out.append(ids[i + 1])
    return out


def _all_shot_ids(scene) -> List[str]:
    return [s.shot_id for s in scene.shots] if scene else []


def _shot_has_frame(sh) -> bool:
    """AI-7B：角色变更影响——镜头是否已有首帧（决定 frame_regen 推荐）。"""
    return sh.first_frame.status == "done" or any(
        c.status == "done" for c in sh.first_frame.candidates)


def _shot_has_video(sh) -> bool:
    """AI-7B：镜头是否已有最终视频（决定 video_regen 推荐）。"""
    return bool(sh.final_video) or sh.video.status == "done"


def _add(items, et, eid, pid, reason, action):
    items.append(ImpactItem(entity=EntityRef(entity_type=et, entity_id=eid, parent_id=pid),
                            reason_code=reason, recommended_action=action))


def compute_impact(operations, before_project) -> ImpactReport:
    """对一组 operations 计算 ImpactReport。before_project 提供邻接上下文。"""
    items: List[ImpactItem] = []
    S = dict(affected_scene=set(), affected_shot=set(), prod_plan_scene=set(),
             prompt_rebuild=set(), frame_regen=set(), video_regen=set(),
             continuity_review=set(), review_scene=set(),
             added=set(), deleted=set(), modified=set(), moved=set())

    def _sc(sid):
        S["affected_scene"].add(sid); S["prod_plan_scene"].add(sid); S["review_scene"].add(sid)

    def _shot(sid):
        S["affected_shot"].add(sid)

    for op in operations:
        scene = _find_scene(before_project, op.scene_id)

        if op.op_type == OT.UPDATE_SHOT.value:
            _sc(op.scene_id); _shot(op.shot_id); S["modified"].add(op.shot_id)
            S["prompt_rebuild"].add(op.shot_id); S["frame_regen"].add(op.shot_id); S["video_regen"].add(op.shot_id)
            if op.description:
                _add(items, ET.SHOT.value, op.shot_id, op.scene_id, RC.SHOT_INTENT_CHANGED.value,
                     "prompt regeneration recommended; frame regeneration recommended; video regeneration recommended")
            if op.characters:
                _add(items, ET.SHOT.value, op.shot_id, op.scene_id, RC.SHOT_CHARACTERS_CHANGED.value,
                     "frame regeneration recommended; video regeneration recommended; continuity review")
            if op.target_duration_s:
                _add(items, ET.SHOT.value, op.shot_id, op.scene_id, RC.SHOT_DURATION_CHANGED.value,
                     "video generation parameters affected; production plan affected")
            for nid in _neighbor_ids(scene, op.shot_id):
                _add(items, ET.SHOT.value, nid, op.scene_id, RC.ADJACENCY_CHANGED.value,
                     "adjacent prompt/continuity context review")
                S["continuity_review"].add(nid); S["prompt_rebuild"].add(nid); _shot(nid)

        elif op.op_type == OT.INSERT_SHOT.value:
            nid = op.new_shot.shot_id if op.new_shot else ""
            _sc(op.scene_id)
            if nid:
                _shot(nid); S["added"].add(nid); S["prompt_rebuild"].add(nid)
                S["frame_regen"].add(nid); S["video_regen"].add(nid)
                _add(items, ET.SHOT.value, nid, op.scene_id, RC.SHOT_INSERTED.value,
                     "production required (first/last frame + video)")
            for sid in _all_shot_ids(scene):
                _add(items, ET.SHOT.value, sid, op.scene_id, RC.ADJACENCY_CHANGED.value,
                     "adjacency changed; continuity review")
                S["continuity_review"].add(sid); S["prompt_rebuild"].add(sid); _shot(sid)
            _add(items, ET.SCENE.value, op.scene_id, None, RC.CONTINUITY_CHAIN_CHANGED.value,
                 "continuity chain review")

        elif op.op_type == OT.DELETE_SHOT.value:
            _sc(op.scene_id); _shot(op.shot_id); S["deleted"].add(op.shot_id)
            _add(items, ET.SHOT.value, op.shot_id, op.scene_id, RC.SHOT_DELETED.value,
                 "removed from working structure; historical assets retained")
            for nid in _neighbor_ids(scene, op.shot_id):
                _add(items, ET.SHOT.value, nid, op.scene_id, RC.ADJACENCY_CHANGED.value,
                     "adjacency changed; continuity review")
                S["continuity_review"].add(nid); S["prompt_rebuild"].add(nid); _shot(nid)
            _add(items, ET.SCENE.value, op.scene_id, None, RC.CONTINUITY_CHAIN_CHANGED.value,
                 "continuity chain review")

        elif op.op_type == OT.REORDER_SHOTS.value:
            _sc(op.scene_id)
            for sid in (op.ordered_shot_ids or []):
                _shot(sid); S["moved"].add(sid); S["continuity_review"].add(sid); S["prompt_rebuild"].add(sid)
                _add(items, ET.SHOT.value, sid, op.scene_id, RC.ADJACENCY_CHANGED.value,
                     "position changed; continuity review")
            _add(items, ET.SCENE.value, op.scene_id, None, RC.CONTINUITY_CHAIN_CHANGED.value,
                 "continuity chain review; opening source/link context may be stale")

        elif op.op_type == OT.UPDATE_SCENE.value:
            _sc(op.scene_id)
            if op.summary:
                _add(items, ET.SCENE.value, op.scene_id, None, RC.SCENE_SUMMARY_CHANGED.value,
                     "existing storyboard review recommended; production plan affected")
            if op.characters:
                _add(items, ET.SCENE.value, op.scene_id, None, RC.SCENE_CHARACTERS_CHANGED.value,
                     "production plan affected; role compatibility review")
            # §四十一/§四十二：scene 级修改级联到该 scene 全部 shot
            for sid in _all_shot_ids(scene):
                _shot(sid); S["prompt_rebuild"].add(sid); S["continuity_review"].add(sid)
                if op.summary:
                    S["frame_regen"].add(sid); S["video_regen"].add(sid)

        # ============ AI-7B 新 op（§28）============
        elif op.op_type == OT.UPDATE_STORY_TEXT.value:
            _add(items, "story", "story_text", None, RC.STORY_TEXT_CHANGED.value,
                 "project story text changed; review story context")

        elif op.op_type == OT.INSERT_SCENE.value:
            nsid = op.new_scene.proposed_scene_id if op.new_scene else ""
            if nsid:
                S["affected_scene"].add(nsid); S["review_scene"].add(nsid)
                _add(items, ET.SCENE.value, nsid, None, RC.SCENE_INSERTED.value,
                     "new scene inserted; storyboard needed later; production plan later")

        elif op.op_type == OT.DELETE_SCENE.value:
            _sc(op.scene_id)
            _add(items, ET.SCENE.value, op.scene_id, None, RC.SCENE_DELETED.value,
                 "scene removed; historical assets retained")
            # 邻接 scene 连续性
            sids = [s.scene_id for s in before_project.scenes]
            if op.scene_id in sids:
                i = sids.index(op.scene_id)
                for nid in ([sids[i - 1]] if i > 0 else []) + ([sids[i + 1]] if i < len(sids) - 1 else []):
                    S["continuity_review"].add(nid); S["affected_scene"].add(nid)
                    _add(items, ET.SCENE.value, nid, None, RC.ADJACENCY_CHANGED.value,
                         "adjacent scene continuity review")

        elif op.op_type == OT.REORDER_SCENES.value:
            for sid in (op.ordered_scene_ids or []):
                S["affected_scene"].add(sid); S["continuity_review"].add(sid)
                _add(items, ET.SCENE.value, sid, None, RC.SCENE_REORDERED.value,
                     "scene order changed; continuity review")

        elif op.op_type == OT.UPDATE_CHARACTER.value:
            cname = op.character_name or ""
            _add(items, ET.CHARACTER.value, cname, None, RC.CHARACTER_DESCRIPTION_CHANGED.value,
                 "character description changed (visual identity); regenerate views later")
            # §30：角色外貌变更 → 该角色出场的 shot frame/video 可能需重新生成（不自动删 storyboard）
            for s in before_project.scenes:
                for sh in s.shots:
                    if cname in sh.characters:
                        _shot(sh.shot_id)
                        if _shot_has_frame(sh):
                            S["frame_regen"].add(sh.shot_id)
                        if _shot_has_video(sh):
                            S["video_regen"].add(sh.shot_id)
                        S["prompt_rebuild"].add(sh.shot_id); S["continuity_review"].add(sh.shot_id)
                        _add(items, ET.SHOT.value, sh.shot_id, s.scene_id,
                             RC.CHARACTER_DESCRIPTION_CHANGED.value,
                             "shot contains changed character; frame/video may need regeneration")

        elif op.op_type == OT.INSERT_CHARACTER.value:
            cname = op.new_character.name if op.new_character else ""
            _add(items, ET.CHARACTER.value, cname, None, RC.CHARACTER_ADDED.value,
                 "new character added; visual assets required later")

        elif op.op_type == OT.DELETE_CHARACTER.value:
            cname = op.character_name or ""
            _add(items, ET.CHARACTER.value, cname, None, RC.CHARACTER_REMOVED.value,
                 "character removed; remove/replace references; historical assets retained")
            for s in before_project.scenes:
                if cname in s.characters:
                    S["affected_scene"].add(s.scene_id)
                for sh in s.shots:
                    if cname in sh.characters:
                        _shot(sh.shot_id); S["continuity_review"].add(sh.shot_id)
                        _add(items, ET.SHOT.value, sh.shot_id, s.scene_id,
                             RC.CHARACTER_REMOVED.value,
                             "shot references removed character; must be addressed")

    summary = {
        "scenes_changed": len(S["affected_scene"]),
        "shots_added": len(S["added"]),
        "shots_deleted": len(S["deleted"]),
        "shots_modified": len(S["modified"]),
        "shots_moved": len(S["moved"]),
        "plans_to_rebuild": len(S["prod_plan_scene"]),
        "shots_recommended_for_regeneration": len(S["frame_regen"] | S["video_regen"]),
        "continuity": "needs review" if S["continuity_review"] else "ok",
        "production_plan": "needs re-planning" if S["prod_plan_scene"] else "ok",
        "existing_assets": "retained",
    }
    return ImpactReport(
        affected_scene_ids=sorted(S["affected_scene"]),
        affected_shot_ids=sorted(S["affected_shot"]),
        production_plan_scene_ids=sorted(S["prod_plan_scene"]),
        prompt_rebuild_shot_ids=sorted(S["prompt_rebuild"]),
        frame_regeneration_shot_ids=sorted(S["frame_regen"]),
        video_regeneration_shot_ids=sorted(S["video_regen"]),
        continuity_review_shot_ids=sorted(S["continuity_review"]),
        review_recommended_scene_ids=sorted(S["review_scene"]),
        items=items, summary=summary)


# ============ aggregate impact：base↔working 重算（§四十八）============
def _ops_from_diff(base, working) -> List[ChangeOperation]:
    """从 base↔working 结构 diff 反推 operations，供 aggregate impact 重算。
    不简单相加历史 impact report（§四十八：8→5 再 5→8 最终无变化）。"""
    from .diff import diff_projects
    ops: List[ChangeOperation] = []
    for d in diff_projects(base, working):
        k = d.get("kind"); ent = d.get("entity")
        if ent == "scene":
            if k == "update":
                f = d.get("field")
                if f == "summary":
                    ops.append(ChangeOperation(op_type=OT.UPDATE_SCENE.value, scene_id=d["id"],
                                               summary=BeforeAfter(d["before"], d["after"])))
                elif f == "characters":
                    ops.append(ChangeOperation(op_type=OT.UPDATE_SCENE.value, scene_id=d["id"],
                                               characters=BeforeAfter(d["before"], d["after"])))
            elif k == "reorder":
                ops.append(ChangeOperation(op_type=OT.REORDER_SHOTS.value, scene_id=d["id"],
                                           ordered_shot_ids=list(d.get("after_order") or [])))
        elif ent == "shot":
            if k == "update":
                f = d.get("field")
                ops.append(ChangeOperation(op_type=OT.UPDATE_SHOT.value, scene_id=d["parent_id"],
                                            shot_id=d["id"], **{f: BeforeAfter(d["before"], d["after"])}))
            elif k == "insert":
                a = d.get("after") or {}
                ops.append(ChangeOperation(op_type=OT.INSERT_SHOT.value, scene_id=d["parent_id"],
                                            new_shot=NewShotSpec(shot_id=d["id"], description=a.get("description", ""),
                                                                 characters=a.get("characters", []),
                                                                 target_duration_s=a.get("target_duration_s"))))
            elif k == "delete":
                ops.append(ChangeOperation(op_type=OT.DELETE_SHOT.value, scene_id=d["parent_id"], shot_id=d["id"]))
    return ops


def compute_aggregate_impact(base_project, working_project) -> ImpactReport:
    """整个 working 相对 base 的 aggregate impact——按 base vs working 重新计算（§四十八）。"""
    return compute_impact(_ops_from_diff(base_project, working_project), base_project)
