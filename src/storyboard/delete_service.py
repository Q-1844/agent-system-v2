# src/storyboard/delete_service.py
# AI-7D-R · 确定性级联删除服务（中性模块，不归任何 agent）。
#
# 原则（与 §27 一致）：结构依赖由代码算，LLM 不决定删什么。本模块被 Director 测试弹窗
# 调用；未来用户手动删除弹窗、Character Designer 删角色复用同一套逻辑，因此「依赖说明文本」
# 由本模块统一生成——agent 转述的与用户弹窗看到的天然一致。
#
# 依赖层级：角色 → 故事段 → 镜头；剧本（story_text）豁免，无结构依赖。
#   - 删故事段 S：删 S + 其所有镜头 + 镜头产物文件 + S 的制作计划条目。不碰角色/其他段。
#   - 删镜头 B：删 B + 递推删所有以 B 为接力来源（source_shot_id）的下游镜头。不删上游
#     来源镜头。只删部分镜头时，该段制作计划「文件保留」，is_plan_stale 下次读自动标过期。
#
# 本模块不 import router/deps（agents 包纪律：src/agents/* 绝不 import router）。
# 仓库根通过 project_dir.parent.parent 推导，可注入 base_dir 覆盖（测试用）。
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from src.core.models import Project
from src.core.store import ProjectStore

_log = logging.getLogger(__name__)


class DeleteError(Exception):
    """级联删除的确定性错误。code: not_found / unsupported / production_active。"""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


# ============ 下游镜头级联（反向扫 source_shot_id，传递闭包）============
def _downstream_shot_ids(project: Project, target_shot_id: str) -> list[str]:
    """返回 [target, ...递推下游]。source_shot_id 按构造只在同段内，但防御性地全项目扫。
    not-in-to_delete 守卫防环（异常数据下不死循环）。顺序稳定（sorted），保证文本确定性。"""
    to_delete = [target_shot_id]
    frontier = {target_shot_id}
    while frontier:
        nxt = set()
        for sc in project.scenes:
            for sh in sc.shots:
                sid = sh.shot_id
                src = sh.source_shot_id
                if src and src in frontier and sid not in to_delete:
                    nxt.add(sid)
        to_delete.extend(sorted(nxt))
        frontier = nxt
    return to_delete


def _all_shot_ids_in_scene(project: Project, scene_id: str) -> list[str]:
    sc = next((s for s in project.scenes if s.scene_id == scene_id), None)
    return [sh.shot_id for sh in sc.shots] if sc else []


def _shot_has_video(sh) -> bool:
    if sh.final_video:
        return True
    if getattr(sh, "video", None) and sh.video.status == "done":
        return True
    return any(getattr(vv, "status", "") == "done" or vv.local
               for vv in (sh.video_versions or []))


# ============ 产物文件收集（覆盖旧字段 + 批次/版本新真相源）============
def _asset_locals(shot) -> list[str]:
    """收集一个镜头所有本地产物路径（去重前）。旧清理只覆盖 video/audio/legacy candidates，
    漏了 first_frame_batches/last_frame_batches/video_versions——这里全收。"""
    paths: list[str] = []

    def _add(a):
        local = getattr(a, "local", None) if a is not None else None
        if local:
            paths.append(local)

    # 旧单值字段
    _add(getattr(shot, "video", None))
    _add(getattr(shot, "audio", None))
    _add(getattr(shot, "video_end_frame", None))
    # 旧图片候选
    for img in (getattr(shot, "first_frame", None), getattr(shot, "last_frame", None)):
        if img:
            for c in (getattr(img, "candidates", None) or []):
                _add(c)
    # 新批次候选
    for batch in (*(getattr(shot, "first_frame_batches", None) or []),
                  *(getattr(shot, "last_frame_batches", None) or [])):
        for c in (getattr(batch, "candidates", None) or []):
            _add(c)
    # 视频版本
    for vv in (getattr(shot, "video_versions", None) or []):
        _add(vv)
    return paths


def _collect_asset_paths(project: Project, shot_ids: list[str]) -> list[str]:
    seen = set()
    out = []
    for sid in shot_ids:
        sh = project.get_shot(sid)
        if sh is None:
            continue
        for p in _asset_locals(sh):
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out


def _safe_unlink(raw_path: str, project_dir: Path, base_dir: Path,
                 log_ctx: str = "") -> bool:
    """安全删文件：相对路径用 base_dir 解析 → resolve → relative_to(project_dir) 护栏，
    越界只 warning 跳过。返回是否真的删了。复用 routers/scenes.py 的护栏思路。"""
    try:
        p = Path(raw_path)
        if not p.is_absolute():
            p = base_dir / p
        p = p.resolve()
        try:
            p.relative_to(project_dir.resolve())
        except ValueError:
            _log.warning("跳过清理（不在项目目录内，可能 stale 路径）%s: %s", log_ctx, p)
            return False
        if p.exists() and p.is_file():
            p.unlink()
            return True
    except Exception as e:
        _log.warning("清理产物失败 %s (%s): %s", log_ctx, raw_path, e)
    return False


# ============ 规范依赖说明文本（agent 与未来用户弹窗共用，逐字一致）============
def describe_delete_preview(prev: dict) -> str:
    et = prev.get("entity_type")
    eid = prev.get("entity_id")
    if et == "character":
        return "角色删除由 Character Designer 负责，Director 暂不支持。"
    wd = prev.get("will_delete", {})
    shot_ids = wd.get("shots", [])
    n_files = wd.get("asset_files", 0)
    detail = prev.get("shots_detail", [])
    protected = prev.get("protected_characters", [])

    lines: list[str] = []
    if et == "scene":
        title = prev.get("scene_title") or ""
        head = f"将删除故事段 {eid}"
        if title:
            head += f"「{title}」"
        lines.append(f"{head}及其全部 {len(shot_ids)} 个镜头"
                     + (f"（{ '、'.join(shot_ids) }）" if shot_ids else "")
                     + f"，共 {n_files} 个生成资产文件（视频/音频/首尾帧）。")
        if eid in (wd.get("plans") or []):
            lines.append(f"该段的制作计划 plan_{eid} 将一并删除。")
    else:  # shot
        target_detail = next((d for d in detail if not d.get("is_downstream_of_target")), None)
        downstream = [d for d in detail if d.get("is_downstream_of_target")]
        lines.append(f"将删除镜头 {eid}"
                     + (f"，并级联删除依赖其结尾画面的后续镜头：{'、'.join(d['shot_id'] for d in downstream)}"
                        if downstream else "。")
                     + f"共 {len(shot_ids)} 个镜头，{n_files} 个生成资产文件。")
        # 提示哪些下游已生成视频 / 被接力
        with_video = [d["shot_id"] for d in detail if d.get("has_video")]
        if with_video:
            lines.append(f"其中 {'、'.join(with_video)} 已生成视频，删除后不可恢复。")
        if not target_detail:
            pass
    # 共同的「不删什么」
    untouched: list[str] = []
    if protected:
        untouched.append(f"出场角色（{'、'.join(protected)}）不会被删除")
    other = prev.get("other_scenes_untouched") or []
    if other:
        untouched.append("其他故事段不受影响")
    if et == "shot":
        untouched.append("上游来源镜头不受影响")
    if et == "shot" and (wd.get("plans") or []) == []:
        untouched.append("该段制作计划文件保留，但会因镜头变化自动标记为过期")
    if untouched:
        lines.append("；".join(untouched) + "。")
    lines.append("确定删除吗？")
    return "\n".join(lines)


# ============ preview（纯读，不碰磁盘）============
def preview_delete(project: Project, entity_type: str, entity_id: str) -> dict:
    et = (entity_type or "").lower()
    result: dict[str, Any] = {"entity_type": et, "entity_id": entity_id}

    if et == "character":
        result.update({"found": True, "supported": False,
                       "text": describe_delete_preview(result)})
        return result

    # 计算要删的 shot_ids / scene_ids
    scene_ids: list[str] = []
    shot_ids: list[str] = []
    scene_title = ""
    if et == "scene":
        sc = next((s for s in project.scenes if s.scene_id == entity_id), None)
        if sc is None:
            result.update({"found": False, "will_delete": {"scenes": [], "shots": [],
                           "plans": [], "asset_files": 0}, "shots_detail": [],
                           "protected_characters": [], "other_scenes_untouched": [],
                           "text": f"故事段 {entity_id} 不存在。"})
            return result
        scene_ids = [entity_id]
        shot_ids = _all_shot_ids_in_scene(project, entity_id)
        scene_title = sc.summary or sc.title or ""
    elif et == "shot":
        if project.get_shot(entity_id) is None:
            result.update({"found": False, "will_delete": {"scenes": [], "shots": [],
                           "plans": [], "asset_files": 0}, "shots_detail": [],
                           "protected_characters": [], "other_scenes_untouched": [],
                           "text": f"镜头 {entity_id} 不存在。"})
            return result
        shot_ids = _downstream_shot_ids(project, entity_id)
    else:
        raise DeleteError("unsupported", f"不支持的实体类型: {entity_type}")

    # 出场角色 = 待删段/镜头里引用过的角色（用于「不会删除角色」声明）
    char_set: list[str] = []
    for sid in shot_ids:
        sh = project.get_shot(sid)
        if sh:
            for c in sh.characters:
                if c not in char_set:
                    char_set.append(c)
    for sid in scene_ids:
        sc = next((s for s in project.scenes if s.scene_id == sid), None)
        if sc:
            for c in sc.characters:
                if c not in char_set:
                    char_set.append(c)
    protected_characters = [c for c in char_set if project.get_character(c) is not None]

    # 谁以这些镜头为接力来源（has_continuity）——全项目扫
    referenced_by = set()
    for sc in project.scenes:
        for sh in sc.shots:
            if sh.source_shot_id and sh.source_shot_id in set(shot_ids) \
                    and sh.shot_id not in set(shot_ids):
                referenced_by.add(sh.source_shot_id)

    shots_detail = []
    for sid in shot_ids:
        sh = project.get_shot(sid)
        if sh is None:
            continue
        shots_detail.append({
            "shot_id": sid,
            "index": sh.index,
            "has_video": _shot_has_video(sh),
            "has_continuity": sid in referenced_by,
            "is_downstream_of_target": (et == "shot" and sid != entity_id),
            "description_head": (sh.description or "")[:40],
        })

    # 产物数（纯计数，不实际删）
    n_files = len(_collect_asset_paths(project, shot_ids))

    # 整段删 → 该段计划一并删；只删镜头 → 计划文件保留（自动 stale）
    plans = [entity_id] if et == "scene" else []

    other_scenes = [s.scene_id for s in project.scenes if s.scene_id not in set(scene_ids)]

    result.update({
        "found": True,
        "supported": True,
        "scene_title": scene_title,
        "will_delete": {"scenes": scene_ids, "shots": shot_ids,
                        "plans": plans, "asset_files": n_files},
        "shots_detail": shots_detail,
        "protected_characters": protected_characters,
        "other_scenes_untouched": other_scenes,
    })
    result["text"] = describe_delete_preview(result)
    return result


# ============ 内存 apply（纯内存，不落盘）============
# 返回 DeletePlan：{deleted, asset_files, plan_keys} —— 供沙箱用（apply 后校验，再统一 commit）。
def apply_delete_to_memory(project: Project, entity_type: str, entity_id: str) -> dict:
    """纯内存级联删除：只改传入的 project 对象，不落盘。
    返回 {deleted:{scenes,shots}, asset_files:[...], plan_keys:[...]}。
    执行时重算 preview（不信调用方），实体不存在抛 DeleteError。"""
    prev = preview_delete(project, entity_type, entity_id)
    if not prev.get("found"):
        raise DeleteError("not_found",
                          f"{'故事段' if entity_type == 'scene' else '镜头'} {entity_id} 不存在")
    if not prev.get("supported", True):
        raise DeleteError("unsupported", "该实体类型暂不支持删除")

    wd = prev["will_delete"]
    shot_ids = list(wd["shots"])
    scene_ids = set(wd["scenes"])
    remove_shots = set(shot_ids)
    plan_keys = list(wd.get("plans") or [])

    # 1. 先收集产物路径（改内存前）
    asset_files = _collect_asset_paths(project, shot_ids)

    # 2. 改内存
    if entity_type == "scene":
        project.scenes = [s for s in project.scenes if s.scene_id != entity_id]
    else:
        for sc in project.scenes:
            sc.shots = [sh for sh in sc.shots if sh.shot_id not in remove_shots]
            # 幸存镜头 index 重排为 1..N 连续；shot_id 不变（身份证号，删除不回收）
            for i, sh in enumerate(sc.shots, start=1):
                sh.index = i
            # 清空指向被删镜头的悬空指针
            for sh in sc.shots:
                if sh.source_shot_id in remove_shots:
                    sh.source_shot_id = None
                    sh.source_version = None
                if getattr(sh, "split_next", None) in remove_shots:
                    sh.split_next = None

    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "deleted": {"scenes": list(scene_ids), "shots": shot_ids},
        "asset_files": asset_files,
        "plan_keys": plan_keys,
        "summary": prev.get("text", ""),
    }


def apply_delete_scenes_many_to_memory(project: Project,
                                       scene_ids: list[str]) -> dict:
    """纯内存：一次性级联删除【多个故事段】（story.delete_many 的中性层）。

    原子性：先一次性校验所有 scene_id 都存在（任一不存在抛 DeleteError(not_found)，
    一个都不删），再逐个 apply 到同一 project 对象。整段删除互相独立——镜头随段走、
    source_shot_id 按构造只在段内，幸存段不会留下悬空指针——故顺序不影响结果。
    产物文件 / 制作计划聚合后由调用方【一次】 commit_disk_effects 落盘。

    scene_ids 重复项防御性去重（保持首次出现顺序）；agent 层应先把重复参数拒掉。
    返回 {deleted:{scenes,shots,plans}, asset_files, plan_keys}。"""
    ids: list[str] = []
    for raw in scene_ids or []:
        sid = (raw or "").strip()
        if not sid:
            raise DeleteError("invalid", "scene_ids 含空值")
        if sid not in ids:
            ids.append(sid)
    if not ids:
        raise DeleteError("invalid", "scene_ids 不能为空")

    existing = {s.scene_id for s in project.scenes}
    missing = [sid for sid in ids if sid not in existing]
    if missing:
        raise DeleteError("not_found", f"故事段不存在: {', '.join(missing)}")

    asset_files: list[str] = []
    seen_files: set[str] = set()
    plan_keys: list[str] = []
    deleted_shots: list[str] = []
    for sid in ids:
        plan = apply_delete_to_memory(project, "scene", sid)
        for p in plan["asset_files"]:
            if p not in seen_files:
                seen_files.add(p)
                asset_files.append(p)
        plan_keys.extend(plan["plan_keys"])
        deleted_shots.extend(plan["deleted"]["shots"])

    return {
        "deleted": {"scenes": list(ids), "shots": deleted_shots, "plans": plan_keys},
        "asset_files": asset_files,
        "plan_keys": plan_keys,
    }


# ============ 磁盘副作用 commit（写 plans / 删文件 / 取消 job）============
def commit_disk_effects(project_dir, plan_keys: list, asset_files: list,
                        base_dir: Optional[Path] = None,
                        log_ctx: str = "") -> dict:
    """把沙箱里收集的删除副作用真正落盘：移除计划 key、删资产文件、取消孤儿 job。
    项目 project.json 已由调用方通过 ProjectStore.save(working) 写入。
    返回 {removed_files, plan_removed}。"""
    project_dir = Path(project_dir)
    if base_dir is None:
        base_dir = project_dir.parent.parent
    base_dir = Path(base_dir)

    # 1. 制作计划
    plan_removed = False
    if plan_keys:
        try:
            from src.ai.production_planner import read_plans, write_plans
            plans = read_plans(project_dir)
            changed = False
            for key in plan_keys:
                if key in plans:
                    del plans[key]
                    changed = True
            if changed:
                write_plans(project_dir, plans)
                plan_removed = True
        except Exception as e:
            _log.warning("处理制作计划失败（非致命）%s: %s", plan_keys, e)

    # 2. 删文件（崩溃留孤儿无害）
    removed_files = 0
    for p in asset_files:
        if _safe_unlink(p, project_dir, base_dir, log_ctx=log_ctx or repr(plan_keys)):
            removed_files += 1

    return {"removed_files": removed_files, "plan_removed": plan_removed}


def cancel_orphan_jobs(project_name: str, surviving_shot_ids: set) -> None:
    """best-effort：取消指向已删镜头的 running/queued job（避免 worker 写回空镜头）。
    由 commit 层调用（传入存活 shot 集合）。测试环境无 deps 时静默。"""
    try:
        from deps import get_job_queue
        jq = get_job_queue()
        if jq is None:
            return
        touched = False
        for j in list(getattr(jq, "jobs", {}).values()):
            if (getattr(j, "project", None) == project_name
                    and getattr(j, "shot_id", None)
                    and j.shot_id not in surviving_shot_ids
                    and getattr(j, "state", None) in ("running", "queued")):
                j.state = "cancelled"
                j.error = "镜头已删除"
                touched = True
        if touched and hasattr(jq, "_save"):
            jq._save()
    except Exception:
        pass


# ============ execute（组合：内存 apply + 磁盘副作用，兼容旧调用/测试）============
def execute_delete(project_dir, project: Project, entity_type: str, entity_id: str,
                   base_dir: Optional[Path] = None) -> dict:
    """单步级联删除（旧语义）：内存 apply + 原子写 canonical + 磁盘副作用。
    返回 {deleted:{scenes,shots,plans}, removed_files, plan_removed, summary}。"""
    plan = apply_delete_to_memory(project, entity_type, entity_id)
    ProjectStore(Path(project_dir)).save(project)
    cancel_orphan_jobs(project.name if hasattr(project, "name") else "",
                       {sh.shot_id for sh in project.all_shots()})
    fx = commit_disk_effects(project_dir, plan["plan_keys"], plan["asset_files"],
                             base_dir, log_ctx=entity_id)
    deleted = dict(plan["deleted"])
    deleted["plans"] = plan["plan_keys"]
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "deleted": deleted,
        "removed_files": fx["removed_files"],
        "plan_removed": fx["plan_removed"],
        "summary": plan["summary"],
    }
