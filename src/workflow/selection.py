# src/workflow/selection.py
# Phase 47 §4.6：审核「批准并选用」的服务端选片执行。
#
# 语义边界（PHASE47 §4.6，逐条对齐）：
# - 审核通过 ≠ 自动替用户选一个候选。只有审核卡片配置了 selection 目标、且用户
#   在该卡片上显式确认「通过并选用」（HTTP 决策请求=用户身份动作）时，服务端才
#   走既有用户专用入口的同一底层服务完成指针变更；模型/工作流执行器永远不获得
#   用户选择权限（决策 API 之外没有任何调用路径）。
# - 每次选用留下独立 selectionReceipt（与审核 decisionReceipt 分开记录）——
#   「选片请求成功而审核响应不明」可对账：两个动作各自落账，均确认满足条件后
#   才启动下游。
# - 选用指针变更只指向精确版本（entityId+versionId），拒绝模糊选择。
from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("workflow.selection")

SUPPORTED_KINDS = ("video_version", "sequence_render", "look_reference",
                   "prop_reference", "composition_candidate", "first_frame")


def resolve_selection_candidates(pdir: Path, project: str, spec: dict,
                                 limit: int = 12) -> list:
    """审核打开时解析选用候选清单（selectionSpec 无 versionId 时由用户在卡片上
    从真实候选中选择——不自动替用户选"最新"）。返回 [{versionId, label, ...}]。"""
    kind = (spec.get("kind") or "").strip()
    entity_id = (spec.get("entityId") or "").strip()
    try:
        if kind == "video_version":
            from src.core.store import ProjectStore
            shot = ProjectStore(pdir).load().get_shot(entity_id)
            if shot is None:
                return []
            return [{"versionId": v.version_id, "status": v.status,
                     "createdAt": v.created_at,
                     "selected": bool(shot.final_video == v.version_id)}
                    for v in shot.video_versions][:limit]
        if kind == "sequence_render":
            from src.sequence.service import SequenceService
            entry = SequenceService(pdir, project).list_entries().get(entity_id)
            if entry is None:
                return []
            return [{"versionId": r.version_id, "status": r.status,
                     "createdAt": getattr(r, "created_at", 0),
                     "selected": bool(entry.final_render == r.version_id)}
                    for r in entry.render_versions][:limit]
        if kind == "first_frame":
            from src.core.store import ProjectStore
            shot = ProjectStore(pdir).load().get_shot(entity_id)
            if shot is None or not shot.first_frame:
                return []
            return [{"versionId": c.candidate_id, "status": c.status,
                     "createdAt": c.created_at,
                     "selected": bool(shot.selected_first_frame == c.candidate_id)}
                    for c in shot.first_frame.candidates][:limit]
        if kind in ("look_reference", "prop_reference", "composition_candidate"):
            from src.resources.store import ResourceStore
            doc = ResourceStore(pdir).load()
            coll = {"look_reference": doc.looks, "prop_reference": doc.props,
                    "composition_candidate": doc.compositions}[kind]
            e = (coll or {}).get(entity_id)
            if e is None:
                return []
            approved = set(e.approved_reference_asset_ids or [])
            return [{"versionId": aid,
                      "selected": aid in approved,
                      "approved": aid in approved}
                    for aid in (e.reference_asset_ids or [])][:limit]
    except Exception as e:   # 候选解析失败不阻塞审核打开——如实空清单
        log.warning("选用候选解析失败 kind=%s entity=%s: %s", kind, entity_id, e)
        return []
    return []


def perform_selection(pdir: Path, project: str, spec: dict) -> dict:
    """执行一次精确选用。spec = {kind, entityId, versionId?}。
    返回 {ok, kind, entityId, versionId, pointer, error?}。幂等：指针已指向该版本
    时返回 ok=True（alreadySelected），不重复产生副作用。"""
    kind = (spec.get("kind") or "").strip()
    entity_id = (spec.get("entityId") or "").strip()
    version_id = (spec.get("versionId") or "").strip() or None
    if kind not in SUPPORTED_KINDS:
        return {"ok": False, "kind": kind, "entityId": entity_id,
                "error": f"selection.kind 不支持: {kind!r}（支持 {SUPPORTED_KINDS}）"}
    if not entity_id:
        return {"ok": False, "kind": kind, "error": "selection.entityId 必填"}
    try:
        if kind == "video_version":
            return _select_video_version(pdir, entity_id, version_id)
        if kind == "sequence_render":
            return _select_sequence_render(pdir, project, entity_id, version_id)
        if kind in ("look_reference", "prop_reference", "composition_candidate"):
            return _approve_reference(pdir, project, kind, entity_id, version_id)
        if kind == "first_frame":
            return _select_first_frame(pdir, entity_id, version_id)
    except Exception as e:
        log.exception("选用执行异常 project=%s spec=%s", project, spec)
        return {"ok": False, "kind": kind, "entityId": entity_id,
                "versionId": version_id,
                "error": f"{type(e).__name__}: {str(e)[:200]}"}
    return {"ok": False, "kind": kind, "error": "未实现的选用类型"}


def _select_video_version(pdir: Path, shot_id: str, version_id: Optional[str]) -> dict:
    """与 routers/shots.py set_final_video 相同的底层指针语义（final_video + 旧
    video 字段同步）。缺 versionId 时拒绝（不猜"最新版本"）。"""
    from src.core.store import ProjectStore
    if not version_id:
        return {"ok": False, "kind": "video_version", "entityId": shot_id,
                "error": "缺少 versionId（选用必须指向精确版本，不得默认最新）"}
    store = ProjectStore(pdir)
    shot = store.load().get_shot(shot_id)
    if shot is None:
        return {"ok": False, "kind": "video_version", "entityId": shot_id,
                "error": f"镜头不存在: {shot_id}"}
    vv = next((v for v in shot.video_versions if v.version_id == version_id), None)
    if vv is None:
        return {"ok": False, "kind": "video_version", "entityId": shot_id,
                "versionId": version_id, "error": f"视频版本不存在: {version_id}"}
    if shot.final_video == version_id and getattr(shot.video, "local", None) == vv.local:
        return {"ok": True, "kind": "video_version", "entityId": shot_id,
                "versionId": version_id, "pointer": "final_video",
                "alreadySelected": True}
    if vv.status != "done":
        return {"ok": False, "kind": "video_version", "entityId": shot_id,
                "versionId": version_id,
                "error": f"版本状态 {vv.status} 非 done（不可选用）"}

    def mut(s, v=vv):
        s.final_video = v.version_id
        s.video.local = v.local
        s.video.url = v.url
        s.video.status = v.status
        s.video.video_id = v.video_id
        s.video.task_id = v.task_id
        s.video.seconds_returned = v.seconds_returned
        s.video.size_returned = v.size_returned
        s.video.num_frames = v.num_frames
        s.video.frame_rate = v.frame_rate
    store.update_shot(shot_id, mut)
    return {"ok": True, "kind": "video_version", "entityId": shot_id,
            "versionId": version_id, "pointer": "final_video",
            "note": "末帧重提取由既有 set-final 语义负责（此处仅指针+字段同步）；"
                    "接力链路下游按 final_video 真实版本解析"}


def _select_sequence_render(pdir: Path, project: str, sequence_id: str,
                            version_id: Optional[str]) -> dict:
    from src.sequence.service import SequenceService
    if not version_id:
        return {"ok": False, "kind": "sequence_render", "entityId": sequence_id,
                "error": "缺少 versionId"}
    svc = SequenceService(pdir, project)
    entry = svc.select_render(sequence_id, version_id)
    return {"ok": True, "kind": "sequence_render", "entityId": sequence_id,
            "versionId": version_id, "pointer": "final_render"}


def _approve_reference(pdir: Path, project: str, kind: str, entity_id: str,
                       asset_id: Optional[str]) -> dict:
    """复用 resources 写服务的 approve（与用户 REST 入口同层；review pass 证据
    由该服务自身强制，这里不绕过）。"""
    import asyncio
    from src.resources.write_service import ResourceWriteService
    if not asset_id:
        return {"ok": False, "kind": kind, "entityId": entity_id,
                "error": "缺少 versionId（应传 reference asset_id）"}
    ws = ResourceWriteService(pdir, project)

    async def _run():
        if kind == "look_reference":
            return await ws.approve_look_reference(entity_id, asset_id)
        if kind == "prop_reference":
            return await ws.approve_prop_reference(entity_id, asset_id)
        return await ws.approve_composition_candidate(entity_id, asset_id)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    r = asyncio.run(_run()) if loop is None else loop.run_until_complete(_run())
    ok = bool(r.get("ok")) if isinstance(r, dict) else bool(r)
    out = {"ok": ok, "kind": kind, "entityId": entity_id, "versionId": asset_id,
           "pointer": "approved_reference_asset_ids"}
    if isinstance(r, dict) and not ok:
        out["error"] = str(r.get("detail") or r.get("error") or "approve 被拒绝")[:200]
    return out


def _select_first_frame(pdir: Path, shot_id: str, candidate_id: Optional[str]) -> dict:
    from src.core.store import ProjectStore
    if not candidate_id:
        return {"ok": False, "kind": "first_frame", "entityId": shot_id,
                "error": "缺少 versionId（candidate_id）"}
    store = ProjectStore(pdir)
    shot = store.load().get_shot(shot_id)
    if shot is None:
        return {"ok": False, "kind": "first_frame", "entityId": shot_id,
                "error": f"镜头不存在: {shot_id}"}
    cand_ids = [c.candidate_id for c in
                (shot.first_frame.candidates if shot.first_frame else [])]
    if candidate_id not in cand_ids:
        return {"ok": False, "kind": "first_frame", "entityId": shot_id,
                "versionId": candidate_id, "error": "候选不存在于该镜头首帧批次"}
    store.update_shot(shot_id, lambda s: setattr(s, "selected_first_frame", candidate_id))
    return {"ok": True, "kind": "first_frame", "entityId": shot_id,
            "versionId": candidate_id, "pointer": "selected_first_frame"}
