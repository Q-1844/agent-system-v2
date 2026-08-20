# src/agents/change_protocol/impact.py
# AI-7B 提案级确定性 ImpactFacts（§28/§29/§30）。**区别于** revision.compute_impact
# （后者作用于编译后的 ChangeOperation，7B-4 预览复用）；本模块从**语义** ModuleProposalItem
# + project 结构推导「若执行会发生什么影响」。纯代码，不调 LLM（§27）。
#
# §30 关键：角色外貌变更**不**自动触发 Storyboard 结构重写；现有帧/视频**可能**需重新生成
# （hedged），历史资产保留（§72）。删除角色时其 Scene/Shot 引用变为 forbidden（§32）。
from __future__ import annotations
import secrets
from typing import List

from .models import ModuleProposal, ModuleProposalItem


def _ifid() -> str:
    return "imp_" + secrets.token_hex(4)


def _eref(module: str, etype: str, eid: str) -> dict:
    return {"module": module, "entity_type": etype, "entity_id": eid}


def _shot_has_frame(sh) -> bool:
    return sh.first_frame.status == "done" or any(
        c.status == "done" for c in sh.first_frame.candidates)


def _shot_has_video(sh) -> bool:
    return bool(sh.final_video) or sh.video.status == "done"


def _scene_shots(project, scene_id):
    sc = next((s for s in project.scenes if s.scene_id == scene_id), None)
    return list(sc.shots) if sc else []


def _char_scenes_shots(project, name):
    """角色出现的 scenes + shots（确定性，§27）。"""
    scenes, shots = [], []
    for s in project.scenes:
        s_shots = [sh for sh in s.shots if name in sh.characters]
        if name in s.characters or s_shots:
            scenes.append(s.scene_id)
            shots.extend(sh.shot_id for sh in s_shots)
    return scenes, shots, [next((sh for s in project.scenes for sh in s.shots if sh.shot_id == sid), None) for sid in shots]


def _add(facts, seen, reason_code, entity_refs, summary, action=""):
    key = (reason_code, tuple(sorted((r["entity_type"], r["entity_id"]) for r in entity_refs)))
    if key in seen:
        return
    seen.add(key)
    facts.append({"fact_id": _ifid(), "reason_code": reason_code,
                  "entity_refs": entity_refs, "summary": summary,
                  "recommended_action": action})


def _impact_script_item(item: ModuleProposalItem, project, facts, seen):
    a = item.action
    if a == "update_story_text":
        _add(facts, seen, "STORY_TEXT_CHANGED",
              [_eref("script", "story", "story_text")], "项目故事文本将变更", "review story context")
        return
    sid = item.intent.get("scene_id") or item.intent.get("after_scene_id") or ""
    if a == "update_scene":
        _add(facts, seen, "SCENE_CONTENT_CHANGED", [_eref("scene", "scene", sid)],
             f"故事段 {sid} 内容变更", "review storyboard + plan")
        _scene_review_facts(project, sid, facts, seen)
    elif a == "insert_scene":
        _add(facts, seen, "SCENE_INSERTED", [_eref("scene", "scene", sid)],
             f"在 {sid} 后插入新故事段", "new scene needs storyboard later")
    elif a == "delete_scene":
        _add(facts, seen, "SCENE_DELETED", [_eref("scene", "scene", sid)],
             f"故事段 {sid} 删除", "scene shots removed; review continuity")
        _scene_review_facts(project, sid, facts, seen)
    elif a == "reorder_scenes":
        _add(facts, seen, "SCENE_REORDERED", [_eref("scene", "scene", sid)],
             "故事段顺序变更", "continuity review")
        _add(facts, seen, "CONTINUITY_REVIEW_REQUIRED", [_eref("scene", "scene", sid)],
             "邻接关系变化", "review adjacency")


def _scene_review_facts(project, scene_id, facts, seen):
    shots = _scene_shots(project, scene_id)
    if shots:
        _add(facts, seen, "STORYBOARD_REVIEW_REQUIRED",
              [_eref("scene", "scene", scene_id)],
              f"故事段 {scene_id} 的 {len(shots)} 个镜头需复审", "review storyboard")
        _add(facts, seen, "PRODUCTION_PLAN_STALE",
              [_eref("scene", "scene", scene_id)],
              f"故事段 {scene_id} 的制作计划可能过期", "review production plan")
        _add(facts, seen, "PROMPT_CONTEXT_AFFECTED",
              [_eref("shot", "scene", scene_id)],
              "受影响镜头的提示词上下文可能需重建", "rebuild prompts")
        _add(facts, seen, "CONTINUITY_REVIEW_REQUIRED",
              [_eref("scene", "scene", scene_id)], "连续性需复审", "review continuity")


def _impact_character_item(item: ModuleProposalItem, project, facts, seen):
    a = item.action
    name = item.intent.get("name", "")
    scenes, shot_ids, shot_objs = _char_scenes_shots(project, name)
    if a == "update_character":
        _add(facts, seen, "CHARACTER_DESCRIPTION_CHANGED",
              [_eref("character", "character", name)],
              f"角色 {name} 文字视觉设定变更", "update description_en only")
        # §30：Scene 结构不变（不强制 storyboard 重写）
        if scenes:
            _add(facts, seen, "CHARACTER_SCENE_REFERENCES_AFFECTED",
                  [_eref("character", "character", name)],
                  f"{name} 出现的 {len(scenes)} 个场景结构仍有效（不自动重写）", "no structural rewrite")
        if shot_ids:
            _add(facts, seen, "CHARACTER_SHOT_REFERENCES_AFFECTED",
                  [_eref("character", "character", name)],
                  f"{len(shot_ids)} 个镜头引用 {name}", "review shots")
        _add(facts, seen, "CHARACTER_ASSETS_REVIEW_REQUIRED",
              [_eref("character", "character", name)],
              f"{name} 的现有角色图可能视觉陈旧", "regenerate character views later")
        ff = [sh.shot_id for sh in shot_objs if sh and _shot_has_frame(sh)]
        vv = [sh.shot_id for sh in shot_objs if sh and _shot_has_video(sh)]
        if ff:
            _add(facts, seen, "FRAME_REGENERATION_RECOMMENDED",
                  [_eref("character", "character", name)],
                  f"{len(ff)} 个含 {name} 的镜头首帧可能需重新生成", "regenerate frames later")
        if vv:
            _add(facts, seen, "VIDEO_REGENERATION_RECOMMENDED",
                  [_eref("character", "character", name)],
                  f"{len(vv)} 个含 {name} 的视频可能不再匹配新外观", "regenerate videos later")
    elif a == "insert_character":
        _add(facts, seen, "CHARACTER_ADDED",
              [_eref("character", "character", name)],
              f"新增角色 {name}", "new visual assets required later")
    elif a == "delete_character":
        _add(facts, seen, "CHARACTER_REMOVED",
              [_eref("character", "character", name)],
              f"删除角色 {name}", "remove/replace references first (§32)")
        if scenes or shot_ids:
            _add(facts, seen, "CHARACTER_SCENE_REFERENCES_AFFECTED",
                  [_eref("character", "character", name)],
                  f"{len(scenes)} 场景 / {len(shot_ids)} 镜头引用 {name}（删除前必须处理）",
                  "remove/replace references before delete")
        _add(facts, seen, "CHARACTER_ASSETS_REVIEW_REQUIRED",
              [_eref("character", "character", name)],
              f"{name} 的历史资产保留为历史", "preserve historical assets")


def _impact_storyboard_item(item: ModuleProposalItem, project, facts, seen):
    a = item.action
    shid = item.intent.get("shot_id") or ""
    sid = item.intent.get("scene_id") or ""
    if a == "update_shot":
        if "description" in item.intent:
            _add(facts, seen, "SHOT_INTENT_CHANGED", [_eref("shot", "shot", shid)],
                 f"镜头 {shid} 描述变更", "rebuild prompt + regenerate")
        if "characters" in item.intent:
            _add(facts, seen, "SHOT_CHARACTERS_CHANGED", [_eref("shot", "shot", shid)],
                 f"镜头 {shid} 角色变更", "review")
        if "target_duration_s" in item.intent:
            _add(facts, seen, "SHOT_DURATION_CHANGED", [_eref("shot", "shot", shid)],
                 f"镜头 {shid} 时长变更", "re-derive num_frames")
    elif a == "insert_shot":
        _add(facts, seen, "SHOT_INSERTED", [_eref("scene", "scene", sid)],
             f"故事段 {sid} 插入新镜头", "new shot needs production later")
    elif a == "delete_shot":
        _add(facts, seen, "SHOT_DELETED", [_eref("shot", "shot", shid)],
             f"镜头 {shid} 删除", "historical assets preserved")
    elif a == "reorder_shot":
        _add(facts, seen, "SHOT_REORDERED", [_eref("scene", "scene", sid)],
             "镜头顺序变更", "review adjacency")
        _add(facts, seen, "ADJACENCY_CHANGED", [_eref("scene", "scene", sid)],
             "邻接关系变化", "review continuity")
    if shid or sid:
        _add(facts, seen, "CONTINUITY_REVIEW_REQUIRED",
              [_eref("scene", "scene", sid or shid)],
              "连续性需复审", "review continuity")
        _add(facts, seen, "PRODUCTION_PLAN_STALE",
              [_eref("scene", "scene", sid)],
              "受影响制作计划可能过期", "review production plan")


def compute_proposal_impact(proposals: List[ModuleProposal], project) -> List[dict]:
    """§28：从语义 proposal items + project 推导 ImpactFact[]。确定性代码，不调 LLM。"""
    facts: List[dict] = []
    seen = set()
    for prop in proposals:
        for item_dict in prop.items:
            item = ModuleProposalItem.from_dict(item_dict)
            if prop.module == "script":
                _impact_script_item(item, project, facts, seen)
            elif prop.module == "character":
                _impact_character_item(item, project, facts, seen)
            elif prop.module == "storyboard":
                _impact_storyboard_item(item, project, facts, seen)
    return facts
