# src/revision/changeset.py
# Semantic ChangeSet 引擎：preview / apply / reject / reconcile（§九~§十五）。
#
# 不变量：
#   - Preview 绝不修改 working_project.json / workspace.revision / canonical project（§九）。
#     只持久化 changeset（+ 为 InsertShot 分配 stable shot_id 时写 workspace.shot_seq）。
#   - Apply 只接受 changeset_id，后端从持久化 changeset 重建 candidate（§二十二），不让前端重发 operations。
#   - before-value + revision 双重冲突保护（§十三）：不能仅信 revision。
#   - crash-safe apply（§十四）：write-ahead 靠 after_signature + revision 做 reconciliation，绝不重复应用。
from __future__ import annotations
import secrets
import time
from typing import List, Tuple

from .models import (WorkspaceChangeSet, ChangeOperation, ChangeSetStatus, ChangeSetStatus as CSS,
                     Actor, OpType as OT, creative_source_signature)
from .diff import changeset_diff
from .impact import compute_impact
from ..core.models import Project, Shot, Scene, Character


class ChangeSetError(Exception):
    """硬校验失败 / 冲突。code 用于 router 映射 HTTP 状态。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ============ op schema / 硬校验 ============
_VALID_OPS = {ot.value for ot in OT}

# 需 op.scene_id（指向现有 scene）的 op；其余为 project 级（update_story_text /
# insert_scene（id 在 new_scene）/ reorder_scenes（ordered_scene_ids）/ character*）。
_NEEDS_SCENE_ID = {OT.UPDATE_SCENE.value, OT.UPDATE_SHOT.value, OT.INSERT_SHOT.value,
                   OT.DELETE_SHOT.value, OT.REORDER_SHOTS.value, OT.DELETE_SCENE.value}


def _validate_op_schema(op: ChangeOperation) -> None:
    if not op.op_type or op.op_type not in _VALID_OPS:
        raise ChangeSetError("SCHEMA", f"未知 op_type: {op.op_type!r}")
    if op.op_type in _NEEDS_SCENE_ID and not op.scene_id:
        raise ChangeSetError("SCHEMA", "缺少 scene_id")
    if op.op_type in (OT.UPDATE_SHOT.value, OT.DELETE_SHOT.value) and not op.shot_id:
        raise ChangeSetError("SCHEMA", f"{op.op_type} 需要 shot_id")
    if op.op_type == OT.INSERT_SHOT.value and op.new_shot is None:
        raise ChangeSetError("SCHEMA", "insert_shot 需要 new_shot")
    if op.op_type == OT.REORDER_SHOTS.value and not op.ordered_shot_ids:
        raise ChangeSetError("SCHEMA", "reorder_shots 需要 ordered_shot_ids")
    # AI-7B 新 op schema（§52-§57）
    if op.op_type == OT.UPDATE_STORY_TEXT.value and op.story_text is None:
        raise ChangeSetError("SCHEMA", "update_story_text 需要 story_text")
    if op.op_type == OT.INSERT_SCENE.value and op.new_scene is None:
        raise ChangeSetError("SCHEMA", "insert_scene 需要 new_scene")
    if op.op_type == OT.DELETE_SCENE.value and not op.scene_id:
        raise ChangeSetError("SCHEMA", "delete_scene 需要 scene_id")
    if op.op_type == OT.REORDER_SCENES.value and not op.ordered_scene_ids:
        raise ChangeSetError("SCHEMA", "reorder_scenes 需要 ordered_scene_ids")
    if op.op_type in (OT.UPDATE_CHARACTER.value, OT.DELETE_CHARACTER.value) and not op.character_name:
        raise ChangeSetError("SCHEMA", f"{op.op_type} 需要 character_name")
    if op.op_type == OT.INSERT_CHARACTER.value and op.new_character is None:
        raise ChangeSetError("SCHEMA", "insert_character 需要 new_character")


def _final_validate(clone: Project, warnings: List[str]) -> None:
    """对最终 candidate 整体校验（§二十三：ops 一次应用到 clone 再整体验证）。
    Hard（§二十四）：引用不存在 Project Character / 非法 duration。
    Soft：CHARACTER_NOT_COVERED（scene 角色未被任何镜头使用）/ CHARACTER_NOT_IN_SCENE_CAST。
    注：shot char ∉ scene cast 在 v1 设为 soft——避免阻塞既有 cast 不完全一致的 canonical 数据（§三十一.10）。"""
    proj_names = {c.name for c in clone.characters}
    for sc in clone.scenes:
        scene_names = set(sc.characters)
        # AI-7B §32/§45：scene cast 引用不存在的项目角色 → 硬拒绝（删除角色留 forbidden ref）
        for ch in scene_names:
            if ch not in proj_names:
                raise ChangeSetError("CHAR_REF_INVALID",
                                      f"故事段 {sc.scene_id} 角色表引用不存在的项目角色 {ch}")
        used: set = set()
        for sh in sc.shots:
            used.update(sh.characters)
            for ch in sh.characters:
                if ch not in proj_names:
                    raise ChangeSetError("CHAR_REF_INVALID", f"镜头 {sh.shot_id} 引用不存在的项目角色 {ch}")
                if ch not in scene_names:
                    warnings.append(f"镜头 {sh.shot_id} 角色 {ch} 不在故事段 {sc.scene_id} 角色表（CHARACTER_NOT_IN_SCENE_CAST）")
            if sh.target_duration_s is not None and sh.target_duration_s <= 0:
                raise ChangeSetError("INVALID_DURATION", f"镜头 {sh.shot_id} 时长非法 {sh.target_duration_s}")
        if sc.shots:
            for c in scene_names:
                if c not in used:
                    warnings.append(f"故事段 {sc.scene_id} 角色 {c} 未被任何镜头使用（CHARACTER_NOT_COVERED）")


def _apply_ops_to_clone(clone: Project, operations: List[ChangeOperation]) -> Tuple[Project, List[str]]:
    """把 operations 作用到 clone（深拷贝），返回 (candidate, soft_warnings)。
    所有 before 必须与 clone 当前值匹配（§十三），否则 BEFORE_MISMATCH。
    AI-7B（§52-§57）：project 级 op（story_text / character* / reorder_scenes）不经 scene 查找。"""
    warnings: List[str] = []
    for op in operations:
        # ---- project 级 op（无需 scene 查找）----
        if op.op_type == OT.UPDATE_STORY_TEXT.value:
            if op.story_text and op.story_text.before != clone.story_text:
                raise ChangeSetError("BEFORE_MISMATCH", "story_text before 不匹配")
            clone.story_text = op.story_text.after
            continue
        if op.op_type == OT.UPDATE_CHARACTER.value:
            char = next((c for c in clone.characters if c.name == op.character_name), None)
            if char is None:
                raise ChangeSetError("NOT_FOUND", f"角色 {op.character_name} 不存在")
            if op.description_en:
                if op.description_en.before != char.description_en:
                    raise ChangeSetError("BEFORE_MISMATCH",
                                          f"角色 {op.character_name} description_en before 不匹配")
                char.description_en = op.description_en.after
            continue
        if op.op_type == OT.INSERT_CHARACTER.value:
            nc = op.new_character
            if not nc or not nc.name:
                raise ChangeSetError("SCHEMA", "insert_character 需要 new_character.name")
            if any(c.name == nc.name for c in clone.characters):
                raise ChangeSetError("DUP_CHARACTER_NAME", f"角色 {nc.name} 已存在")
            clone.characters.append(Character(name=nc.name, description_en=nc.description_en))
            continue
        if op.op_type == OT.DELETE_CHARACTER.value:
            if not any(c.name == op.character_name for c in clone.characters):
                raise ChangeSetError("NOT_FOUND", f"角色 {op.character_name} 不存在")
            clone.characters = [c for c in clone.characters if c.name != op.character_name]
            continue
        if op.op_type == OT.REORDER_SCENES.value:
            ids = list(op.ordered_scene_ids or [])
            cur = [s.scene_id for s in clone.scenes]
            if len(ids) != len(cur) or set(ids) != set(cur):
                raise ChangeSetError("REORDER_MISMATCH", "reorder_scenes IDs 与当前 scene 集合不一致")
            if len(set(ids)) != len(ids):
                raise ChangeSetError("REORDER_MISMATCH", "reorder_scenes IDs 有重复")
            by = {s.scene_id: s for s in clone.scenes}
            clone.scenes = [by[i] for i in ids]
            continue
        if op.op_type == OT.INSERT_SCENE.value:
            ns = op.new_scene
            if not ns or not ns.proposed_scene_id:
                raise ChangeSetError("SCHEMA", "insert_scene 需要 new_scene.proposed_scene_id")
            if any(s.scene_id == ns.proposed_scene_id for s in clone.scenes):
                raise ChangeSetError("DUP_SCENE_ID", f"故事段 {ns.proposed_scene_id} 已存在")
            new_scene = Scene(scene_id=ns.proposed_scene_id, summary=ns.summary,
                              characters=list(ns.characters))
            if ns.after_scene_id:
                idx = next((i for i, s in enumerate(clone.scenes)
                            if s.scene_id == ns.after_scene_id), None)
                if idx is None:
                    raise ChangeSetError("NOT_FOUND", f"after_scene_id {ns.after_scene_id} 不存在")
                clone.scenes.insert(idx + 1, new_scene)
            else:
                clone.scenes.append(new_scene)
            continue
        # ---- scene 级 op（需 scene 查找）----
        scene = next((s for s in clone.scenes if s.scene_id == op.scene_id), None)
        if scene is None:
            raise ChangeSetError("NOT_FOUND", f"故事段 {op.scene_id} 不存在")

        if op.op_type == OT.UPDATE_SCENE.value:
            if op.summary:
                if op.summary.before != scene.summary:
                    raise ChangeSetError("BEFORE_MISMATCH",
                                          f"scene {op.scene_id} summary before 不匹配")
                scene.summary = op.summary.after
            if op.characters:
                if list(op.characters.before) != list(scene.characters):
                    raise ChangeSetError("BEFORE_MISMATCH",
                                          f"scene {op.scene_id} characters before 不匹配")
                scene.characters = list(op.characters.after)

        elif op.op_type == OT.UPDATE_SHOT.value:
            shot = clone.get_shot(op.shot_id)
            if shot is None:
                raise ChangeSetError("NOT_FOUND", f"镜头 {op.shot_id} 不存在")
            if op.description:
                if op.description.before != shot.description:
                    raise ChangeSetError("BEFORE_MISMATCH",
                                          f"shot {op.shot_id} description before 不匹配")
                shot.description = op.description.after
            if op.characters:
                if list(op.characters.before) != list(shot.characters):
                    raise ChangeSetError("BEFORE_MISMATCH",
                                          f"shot {op.shot_id} characters before 不匹配")
                shot.characters = list(op.characters.after)
            if op.target_duration_s:
                if op.target_duration_s.before != shot.target_duration_s:
                    raise ChangeSetError("BEFORE_MISMATCH",
                                          f"shot {op.shot_id} target_duration_s before 不匹配")
                shot.target_duration_s = op.target_duration_s.after

        elif op.op_type == OT.INSERT_SHOT.value:
            ns = op.new_shot
            if not ns or not ns.shot_id:
                raise ChangeSetError("SCHEMA", "insert 需要 new_shot.shot_id（Preview 应已分配）")
            if clone.get_shot(ns.shot_id) is not None:
                raise ChangeSetError("DUP_SHOT_ID", f"镜头 {ns.shot_id} 已存在")
            scene.shots.append(Shot(
                shot_id=ns.shot_id, scene_id=op.scene_id, index=len(scene.shots) + 1,
                description=ns.description, characters=list(ns.characters),
                target_duration_s=ns.target_duration_s,
            ))

        elif op.op_type == OT.DELETE_SHOT.value:
            shot = clone.get_shot(op.shot_id)
            if shot is None:
                raise ChangeSetError("NOT_FOUND", f"镜头 {op.shot_id} 不存在")
            # §十七：历史资产保留，仅移出 working structure
            scene.shots = [s for s in scene.shots if s.shot_id != op.shot_id]
            if not scene.shots:
                warnings.append(f"故事段 {op.scene_id} 删除后无镜头（soft）")

        elif op.op_type == OT.DELETE_SCENE.value:
            # §17/§32：删除场景移出 working structure；其内镜头随之移出。历史资产保留。
            clone.scenes = [s for s in clone.scenes if s.scene_id != op.scene_id]
            warnings.append(f"故事段 {op.scene_id} 删除（其内镜头随之移出）")

        elif op.op_type == OT.REORDER_SHOTS.value:
            ids = list(op.ordered_shot_ids or [])
            cur = [s.shot_id for s in scene.shots]
            if len(ids) != len(cur) or set(ids) != set(cur):
                raise ChangeSetError("REORDER_MISMATCH",
                                      f"reorder IDs 与 {op.scene_id} 当前 shot 集合不一致")
            if len(set(ids)) != len(ids):
                raise ChangeSetError("REORDER_MISMATCH", "reorder IDs 有重复")
            by = {s.shot_id: s for s in scene.shots}
            scene.shots = [by[i] for i in ids]
            for i, s in enumerate(scene.shots):
                s.index = i + 1   # display order = 数组位置（§十一：不改变任何 shot_id）

    _final_validate(clone, warnings)
    return clone, warnings


# ============ 纯内存预览核心（§62）============
def preview_operations_on_project(source_project: Project,
                                   operations: List[ChangeOperation]):
    """§62：纯内存预览核心。deepcopy source → 校验 schema → apply ops → diff → impact。
    **不分配 ID**（编译器已分配 stable id）/ **不落盘** / **不建 workspace** / 不碰 canonical。
    返回 (candidate, diff, impact, warnings)。Workspace preview 与 AI-7B ProposalProjection
    都复用此核心（§62）——AI-7B 路径绝不调用本模块的 apply/persist。"""
    for op in operations:
        _validate_op_schema(op)
    clone = Project.from_dict(source_project.to_dict())
    candidate, warnings = _apply_ops_to_clone(clone, operations)
    diff = changeset_diff(source_project, candidate)
    impact = compute_impact(operations, source_project)
    return candidate, diff, impact, warnings


# ============ preview ============
def preview(ws_store, ws, title: str, intent: str,
            base_workspace_revision: int, operations: List[ChangeOperation]) -> dict:
    """Preview：校验 -> clone -> apply 到 clone -> diff -> impact -> 持久化 changeset。
    不修改 working_project.json / workspace.revision / canonical project（§九）。"""
    if base_workspace_revision != ws.revision:
        raise ChangeSetError("WORKSPACE_CHANGED",
                              f"workspace revision={ws.revision} != base={base_workspace_revision}")
    for op in operations:
        _validate_op_schema(op)

    working = ws_store.load_working_project(ws.workspace_id)
    before_sig = ws_store.working_signature(ws.workspace_id)

    # InsertShot：Preview 阶段分配最终 stable shot_id；Apply 复用同一个（§十一）
    for op in operations:
        if op.op_type == OT.INSERT_SHOT.value and op.new_shot and not op.new_shot.shot_id:
            op.new_shot.shot_id = ws_store.allocate_shot_id(ws, op.scene_id)

    # §62：复用纯内存预览核心（workspace preview = 纯核心 + ID 分配 + 落盘 changeset）
    candidate, diff, impact, warnings = preview_operations_on_project(working, operations)
    candidate_dict = candidate.to_dict()
    after_sig = creative_source_signature(candidate_dict)

    cid = "cs_" + secrets.token_hex(6)
    cs = WorkspaceChangeSet(
        changeset_id=cid, workspace_id=ws.workspace_id, project_name=ws.project_name,
        title=title, intent=intent, actor=Actor.USER.value,
        base_workspace_revision=ws.revision, status=ChangeSetStatus.READY_TO_APPLY.value,
        operations=operations, before_signature=before_sig, after_signature=after_sig,
        impact_report=impact.to_dict(),
    )
    ws_store.save_changeset(ws.workspace_id, cs.to_dict())
    return {
        "changeset_id": cid, "status": cs.status, "diff": diff,
        "impact": impact.to_dict(), "warnings": warnings,
        "base_workspace_revision": cs.base_workspace_revision,
        "after_signature": after_sig,
    }


# ============ apply ============
def _mark_status(ws_store, ws, cs: WorkspaceChangeSet, status: str) -> None:
    cs.status = status
    ws_store.save_changeset(ws.workspace_id, cs.to_dict())


def apply(ws_store, ws, changeset_id: str) -> dict:
    """Apply 只接受 changeset_id；后端从持久化 changeset 重建 candidate 并三重校验
    （revision + before_signature + re-derive after_signature）（§十三/§二十二）。crash-safe（§十四）。"""
    cs = WorkspaceChangeSet.from_dict(ws_store.load_changeset(ws.workspace_id, changeset_id))

    if cs.status == ChangeSetStatus.APPLIED.value:
        return {"applied": True, "revision": ws.revision, "changeset_id": changeset_id, "idempotent": True}
    if cs.status in (ChangeSetStatus.REJECTED.value, ChangeSetStatus.CONFLICT.value):
        raise ChangeSetError("BAD_STATUS", f"changeset status={cs.status}，不可 apply")

    # 1) revision 双重保护（§十三）
    if ws.revision != cs.base_workspace_revision:
        raise ChangeSetError("WORKSPACE_CHANGED",
                              f"workspace revision={ws.revision} != base={cs.base_workspace_revision}")

    working = ws_store.load_working_project(ws.workspace_id)
    cur_sig = ws_store.working_signature(ws.workspace_id)

    # 2) before_signature 校验（不能仅信 revision，§十三）
    if cur_sig != cs.before_signature:
        _mark_status(ws_store, ws, cs, ChangeSetStatus.CONFLICT.value)
        raise ChangeSetError("STALE", "working project 自 preview 后已变化")

    # 3) 重建 candidate + 逐字段 before 复核 + after_signature 一致
    clone = Project.from_dict(working.to_dict())
    candidate, _ = _apply_ops_to_clone(clone, cs.operations)   # before 不匹配会抛 BEFORE_MISMATCH
    candidate_sig = creative_source_signature(candidate.to_dict())
    if candidate_sig != cs.after_signature:
        _mark_status(ws_store, ws, cs, ChangeSetStatus.CONFLICT.value)
        raise ChangeSetError("CONFLICT", "re-derive candidate 与 preview after_signature 不一致")

    # 4) crash-safe apply（§十四）：先写 working，再 bump revision+sig，再标 applied
    ws_store.write_working_project_dict(ws.workspace_id, candidate.to_dict())
    ws.revision += 1
    ws.working_project_signature = candidate_sig
    ws_store.save_workspace(ws)
    cs.status = ChangeSetStatus.APPLIED.value
    cs.applied_at = time.time()
    ws_store.save_changeset(ws.workspace_id, cs.to_dict())
    return {"applied": True, "revision": ws.revision, "changeset_id": changeset_id}


# ============ reject ============
def reject(ws_store, ws, changeset_id: str) -> dict:
    cs = WorkspaceChangeSet.from_dict(ws_store.load_changeset(ws.workspace_id, changeset_id))
    cs.status = ChangeSetStatus.REJECTED.value
    ws_store.save_changeset(ws.workspace_id, cs.to_dict())
    return {"rejected": True, "changeset_id": changeset_id}


# ============ reconcile（§十四 crash recovery）============
def reconcile(ws_store, ws) -> None:
    """Workspace load 时调用。判定处于危险窗口的 changeset：
    - working_sig == after_signature 且 revision == base+1 -> 已应用（崩溃前未标）-> applied
    - working_sig == before_signature 且 revision == base    -> 未应用，仍可 apply
    - 其它 -> conflict/corruption，不猜。"""
    for cs_dict in ws_store.list_changesets(ws.workspace_id):
        cs = WorkspaceChangeSet.from_dict(cs_dict)
        if cs.status not in (ChangeSetStatus.READY_TO_APPLY.value, ChangeSetStatus.PREVIEWED.value):
            continue
        if not cs.after_signature:
            continue
        cur = ws_store.working_signature(ws.workspace_id)
        if cur == cs.after_signature and ws.revision == cs.base_workspace_revision + 1:
            cs.status = ChangeSetStatus.APPLIED.value
            if not cs.applied_at:
                cs.applied_at = time.time()
            ws_store.save_changeset(ws.workspace_id, cs.to_dict())
        elif cur == cs.before_signature and ws.revision == cs.base_workspace_revision:
            continue   # 仍 pending，安全
        else:
            cs.status = ChangeSetStatus.CONFLICT.value
            ws_store.save_changeset(ws.workspace_id, cs.to_dict())
