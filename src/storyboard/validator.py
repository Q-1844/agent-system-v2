# src/storyboard/validator.py
# AI-7D-R · 沙箱自动校验（确定性，无 LLM）。
#
# 输入 base（canonical）与 working（agent 内存副本）+ effective scope。
# 输出 ValidationError[]：权限越界 / 数量目标 / 结构完整·数据合法。
# 中性模块：不 import router/deps、不调 LLM——可被旧 agent runtime 复用。
from __future__ import annotations

from typing import Any, Optional

from src.core.models import Project
# 复用 revision 的创作结构签名做"内容是否变更"判定（含段/镜顺序，见 models.py）。
from src.revision.models import creative_source_signature


class ValidationError:
    def __init__(self, code: str, message: str, entity_ref: Optional[str] = None):
        self.code = code
        self.message = message
        self.entity_ref = entity_ref or ""

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "entity_ref": self.entity_ref}

    def __repr__(self):
        return f"[{self.code}] {self.message}"


def _scene_ids(p: Project) -> dict[str, str]:
    return {sc.scene_id: sc.summary for sc in p.scenes}


def validate_project(base: Project, working: Project,
                     scope: dict[str, Any]) -> list[ValidationError]:
    """三轴校验。返回空 = 通过。"""
    errors: list[ValidationError] = []
    ops = scope.get("ops", {}) or {}
    targets = scope.get("targets", {}) or {}
    scene_ops = ops.get("scene") or set()
    shot_ops = ops.get("shot") or set()

    # ============ 1. 权限越界 ============
    base_scene_ids = _scene_ids(base)
    working_scene_ids = _scene_ids(working)
    base_shot_ids = {sh.shot_id for sh in base.all_shots()}
    working_shot_ids = {sh.shot_id for sh in working.all_shots()}

    # 新增场景
    new_scenes = [sid for sid in working_scene_ids if sid not in base_scene_ids]
    if new_scenes and "create" not in scene_ops:
        errors.append(ValidationError(
            "SCOPE_CREATE", f"新增了故事段 {','.join(new_scenes)}，但本任务不允许新增。可删除它们或先明确要求新增。",
            ",".join(new_scenes)))
    # 删除场景
    gone_scenes = [sid for sid in base_scene_ids if sid not in working_scene_ids]
    if gone_scenes and "delete" not in scene_ops:
        errors.append(ValidationError(
            "SCOPE_DELETE", f"删除了故事段 {','.join(gone_scenes)}，但本任务不允许删除。可恢复它们或先明确要求删除。",
            ",".join(gone_scenes)))
    # 新增镜头
    new_shots = working_shot_ids - base_shot_ids
    if new_shots and "create" not in shot_ops:
        errors.append(ValidationError(
            "SCOPE_CREATE", f"新增了镜头 {','.join(sorted(new_shots))}，但本任务不允许新增。可删除它们或先明确要求新增。",
            ",".join(sorted(new_shots))))
    # 删除镜头
    gone_shots = base_shot_ids - working_shot_ids
    if gone_shots and "delete" not in shot_ops:
        errors.append(ValidationError(
            "SCOPE_DELETE", f"删除了镜头 {','.join(sorted(gone_shots))}，但本任务不允许删除。可恢复它们或先明确要求删除。",
            ",".join(sorted(gone_shots))))

    # 内容变更（同 id 实体的创作字段 diff）
    changed_scenes = [
        sid for sid in base_scene_ids
        if sid in working_scene_ids and base_scene_ids[sid] != working_scene_ids[sid]]
    base_sig = creative_source_signature(base.to_dict())
    work_sig = creative_source_signature(working.to_dict())
    content_changed = base_sig != work_sig
    if changed_scenes and "update" not in scene_ops:
        errors.append(ValidationError(
            "SCOPE_UPDATE",
            f"修改了故事段 {','.join(changed_scenes)} 的内容，但本任务不允许修改。可恢复原内容或先明确要求修改。",
            ",".join(changed_scenes)))
    if content_changed and "update" not in shot_ops:
        # 镜级内容/结构变更（段摘要没变但签名变了）→ 报更新越界
        # 排除仅新增/删除实体（已在 create/delete 报过），只报真正的"改内容"
        only_internal_change = not (changed_scenes or new_scenes or gone_scenes
                                    or new_shots or gone_shots)
        # 镜级结构变化（shots 数量/内容变了）也会让签名变，但看起来像"段没变"
        base_by_id = {sc.scene_id: sc for sc in base.scenes}
        shot_count_changed = any(
            len((base_by_id.get(w.scene_id) or w).shots) != len(w.shots)
            for w in working.scenes if w.scene_id in base_scene_ids)
        if only_internal_change or shot_count_changed:
            errors.append(ValidationError(
                "SCOPE_UPDATE",
                "修改了镜头的内容/结构，但本任务不允许修改镜头。可恢复原状或先明确要求。"))

    # ============ 2. 数量目标 ============
    if "scene_count" in targets:
        want = int(targets["scene_count"])
        got = len(working.scenes)
        if got != want:
            errors.append(ValidationError(
                "TARGET_SCENE_COUNT",
                f"目标 {want} 个故事段，当前 {got} 个。"
                + (f"多余的是：{','.join(working_scene_ids.keys())}" if got > want else "")
                + "。请调整数量后重试。"))
    delta = targets.get("_scene_delta")
    if delta is not None:
        want = len(base.scenes) + int(delta)
        got = len(working.scenes)
        if got != want:
            errors.append(ValidationError(
                "TARGET_SCENE_COUNT",
                f"要求新增 {delta} 个故事段（目标共 {want} 个，当前 {got} 个）。请调整。"))

    # ============ 3. 结构完整 / 数据合法 ============
    all_scenes = working.scenes
    ids = [sc.scene_id for sc in all_scenes]
    if len(ids) != len(set(ids)):
        dup = [sid for sid in set(ids) if ids.count(sid) > 1]
        errors.append(ValidationError(
            "DUP_SCENE_ID", f"故事段 id 重复：{','.join(dup)}。请改为唯一 id。", ",".join(dup)))

    shot_ids_global: list[str] = []
    for sc in all_scenes:
        if not (sc.summary or "").strip():
            errors.append(ValidationError("EMPTY_SUMMARY",
                                          f"故事段 {sc.scene_id} 的概要为空，请补充内容。", sc.scene_id))
        shot_ids_global.extend(sh.shot_id for sh in sc.shots)
        # 悬空 source_shot_id / split_next
        for sh in sc.shots:
            for ref in (sh.source_shot_id, getattr(sh, "split_next", None)):
                if ref and ref not in shot_ids_global:
                    errors.append(ValidationError(
                        "DANGLING_REF",
                        f"镜头 {sh.shot_id} 引用了不存在的镜头 {ref}，请修复接力/拆分引用。",
                        sh.shot_id))
            if sh.target_duration_s is not None and sh.target_duration_s <= 0:
                errors.append(ValidationError(
                    "BAD_DURATION", f"镜头 {sh.shot_id} 时长非法：{sh.target_duration_s}。",
                    sh.shot_id))
    if len(shot_ids_global) != len(set(shot_ids_global)):
        dup = [s for s in set(shot_ids_global) if shot_ids_global.count(s) > 1]
        errors.append(ValidationError("DUP_SHOT_ID", f"镜头 id 重复：{','.join(dup)}。", ",".join(dup)))

    # 角色合法性
    valid_names = {c.name for c in working.characters}
    for sc in all_scenes:
        for name in sc.characters:
            if valid_names and name not in valid_names:
                errors.append(ValidationError(
                    "UNKNOWN_CHAR", f"故事段 {sc.scene_id} 引用了不存在的角色 {name}。",
                    sc.scene_id))
        for sh in sc.shots:
            for name in sh.characters:
                if valid_names and name not in valid_names:
                    errors.append(ValidationError(
                        "UNKNOWN_CHAR", f"镜头 {sh.shot_id} 引用了不存在的角色 {name}。", sh.shot_id))

    return errors


def validate_to_dicts(errors: list[ValidationError]) -> list[dict]:
    return [e.to_dict() for e in errors]