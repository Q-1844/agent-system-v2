# src/agents/change_protocol/compilers.py
# AI-7B 确定性提案编译器（§50/§52-§57）。语义 ModuleProposalItem → ChangeOperation[]。
#
# §50 红线：LLM 只产出语义意图；before 值 / 稳定 ID / revision 全部由本编译器从 source
# 确定性产出（绝不取自 LLM）。storyboard 复用 director_proposal.compile_items_pure（§21）。
# 编译产物仅用于 ProposalProjectionEngine 的纯内存预览（§60/§61），不落盘、不应用。
from __future__ import annotations
from typing import Callable, List

from src.revision.models import (ChangeOperation, BeforeAfter, NewSceneSpec,
                                   NewCharacterSpec, OpType as OT)
from ..director_proposal import compile_items_pure, DirectorProposalItem, ProposalGenerationError
from .models import ModuleProposal, ModuleProposalItem


def _scene_snapshot(scene) -> dict:
    return {"scene_id": scene.scene_id, "summary": scene.summary,
            "characters": list(scene.characters),
            "shot_ids": [sh.shot_id for sh in scene.shots]}


def _move_after(order, moving_id, anchor_id):
    """复用 Director 的 _move_after 语义（move moving after anchor；anchor None=移到首）。"""
    from ..director_proposal import _move_after as _ma
    return _ma(order, moving_id, anchor_id)


def compile_script_items(items, source_project, scene_alloc_fn: Callable[[], str]) -> List[ChangeOperation]:
    """§17/§52/§53：script 语义 items → ChangeOperation[]。before 读 source；insert_scene 用
    scene_alloc_fn() 分配稳定 proposed_scene_id（§54，复用 next_scene_seq 语义）。"""
    ops: List[ChangeOperation] = []
    for item_dict in items:
        item = ModuleProposalItem.from_dict(item_dict)
        a = item.action
        if a == "update_story_text":
            ops.append(ChangeOperation(
                op_type=OT.UPDATE_STORY_TEXT.value,
                story_text=BeforeAfter(before=source_project.story_text,
                                       after=item.intent.get("new_story_text", ""))))
        elif a == "update_scene":
            sid = item.intent.get("scene_id", "")
            sc = next((s for s in source_project.scenes if s.scene_id == sid), None)
            if sc is None:
                raise ProposalGenerationError("compile_error", f"故事段 {sid} 不存在")
            summary_ba = chars_ba = None
            if "new_summary" in item.intent:
                summary_ba = BeforeAfter(before=sc.summary, after=item.intent["new_summary"])
            if "characters" in item.intent:
                chars_ba = BeforeAfter(before=list(sc.characters), after=list(item.intent["characters"]))
            ops.append(ChangeOperation(op_type=OT.UPDATE_SCENE.value, scene_id=sid,
                                        summary=summary_ba, characters=chars_ba))
        elif a == "insert_scene":
            after = item.intent.get("after_scene_id")
            sid = scene_alloc_fn()
            ops.append(ChangeOperation(op_type=OT.INSERT_SCENE.value,
                new_scene=NewSceneSpec(after_scene_id=after, proposed_scene_id=sid,
                    summary=item.intent.get("summary", ""),
                    characters=list(item.intent.get("characters", [])))))
        elif a == "delete_scene":
            sid = item.intent.get("scene_id", "")
            sc = next((s for s in source_project.scenes if s.scene_id == sid), None)
            ops.append(ChangeOperation(op_type=OT.DELETE_SCENE.value, scene_id=sid,
                                        before=_scene_snapshot(sc) if sc else None))
        elif a == "reorder_scenes":
            moving = item.intent.get("scene_id")
            anchor = item.intent.get("after_scene_id")
            cur = [s.scene_id for s in source_project.scenes]
            final = _move_after(cur, moving, anchor)
            ops.append(ChangeOperation(op_type=OT.REORDER_SCENES.value,
                                        ordered_scene_ids=final))
    return ops


def compile_character_items(items, source_project) -> List[ChangeOperation]:
    """§19/§55-§57：character 语义 items → ChangeOperation[]。before 读 source。
    update_character 只改 description_en（§20，不碰 views/assets）。"""
    ops: List[ChangeOperation] = []
    for item_dict in items:
        item = ModuleProposalItem.from_dict(item_dict)
        a = item.action
        name = item.intent.get("name", "")
        if a == "update_character":
            char = next((c for c in source_project.characters if c.name == name), None)
            if char is None:
                raise ProposalGenerationError("compile_error", f"角色 {name} 不存在")
            desc_ba = None
            if "description_en" in item.intent:
                desc_ba = BeforeAfter(before=char.description_en,
                                      after=item.intent["description_en"])
            ops.append(ChangeOperation(op_type=OT.UPDATE_CHARACTER.value,
                                        character_name=name, description_en=desc_ba))
        elif a == "insert_character":
            ops.append(ChangeOperation(op_type=OT.INSERT_CHARACTER.value,
                new_character=NewCharacterSpec(name=name,
                    description_en=item.intent.get("description_en", ""))))
        elif a == "delete_character":
            char = next((c for c in source_project.characters if c.name == name), None)
            ops.append(ChangeOperation(op_type=OT.DELETE_CHARACTER.value,
                character_name=name,
                before={"name": name, "description_en": char.description_en} if char else None))
    return ops


def compile_storyboard_items(items, source_project, shot_alloc_fn: Callable[[str], str]) -> List[ChangeOperation]:
    """§21/§58：storyboard 语义 items → ChangeOperation[]。复用 director_proposal.compile_items_pure
    （注入纯 shot_alloc_fn，不需 ws_store）。before 读 source；insert_shot 由 alloc_fn 分配稳定 id。"""
    ditems: List[DirectorProposalItem] = []
    for item_dict in items:
        item = ModuleProposalItem.from_dict(item_dict)
        ditems.append(DirectorProposalItem(
            item_id=item.item_id, action=item.action,
            scene_id=item.intent.get("scene_id", ""),
            shot_id=item.intent.get("shot_id"),
            description=item.intent.get("description"),
            characters=item.intent.get("characters"),
            target_duration_s=item.intent.get("target_duration_s"),
            after_shot_id=item.intent.get("after_shot_id"),
            reason=item.reason))
    return compile_items_pure(ditems, source_project, shot_alloc_fn)
