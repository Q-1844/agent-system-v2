# src/agents/director_visual.py
# Director 视觉生产工具（2026-08-31）：shot.generate_images / shot.generate_videos。
#
# 定位：Agent 只负责【决定生成什么时候发生】（选哪些 shot / 首帧还是尾帧 / 是否够格
# 生成视频），不重新实现任何图片/视频生成算法——生成本体完全复用既有成熟 JobQueue
# 管线（routers/shots.py 的 _handler_first_frame/_handler_last_frame/_handler_video，
# 含 prompt 拼装、角色参考图/接力参考图收集、Agnes 限流桶、每日配额、视频串行 worker、
# 看门狗、崩溃恢复、imgbb 托管、末帧提取）。本文件只做四件事：
#   1. 参数校验与【提交前】结构预检（锁/keyframes/首帧就绪——不达标不提交，省配额）；
#   2. 提交 job 并等待终态（轮询 JobQueue，向 turn 流发 tool.progress 进度）；
#   3. 响应协作取消（用户停止 → force-cancel 未完成 job，已完成资产保留在 canonical）；
#   4. 写后重读 canonical 验证：按 origin_run_id 精确匹配本批生成的
#      ImageBatch / VideoVersion + 本地文件存在（确定性依据，不用模型自报）。
#
# 长任务语义（对齐 character.generate_views 样板）：
#   - 图片批：轻任务 worker 并发 3（image_1k 20 RPM 限流下安全），总超时 1200s；
#   - 视频批：视频串行 worker（1 RPM 现实约束），总超时 2400s；
#   - 部分成功：单个失败不让整批作废；receipt ok=任一成功，verified=所有【已完成】
#     分项都经 canonical+文件验证（诚实语义，让真相闸门正确放行部分成功的如实汇报）；
#   - 总超时不取消 job：未完成项如实标 timeout，job 留在队列继续跑完并自行落盘
#     （与用户从 REST 提交生成完全同语义）；用户停止（cancel）才 force-cancel。
#
# 图片/视频内容绝不进模型上下文：obs 只含 canonical 路径与状态（前端用路径渲染）。
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Optional

from ..core.store import ProjectStore
from .revision_audit import DirectEditAuditStore
from .runtime import AgentError, AgentRuntime
from .script_writer import _base_receipt, _loose_json, _parse_args

# 批量上限：图片批对齐文本批量规模（10 镜/批，轻任务并发 3）；视频受串行 worker +
# 1 RPM 现实约束（每个约 70s~数分钟），一批最多 4 个。
MAX_IMAGE_BATCH = 10
MAX_VIDEO_BATCH = 4
# 总超时：图片单张约 15~40s（AgnesClient 重试上限/看门狗 600s 封顶），10 张并发 3
# 约 2~5 分钟，超时给到 20 分钟覆盖病态重试；视频串行每个约 70s~3 分钟（poll 上限
# 3600s），4 个一批给到 40 分钟。超时后未完成 job 留队列继续跑（见文件头）。
IMAGES_TOTAL_TIMEOUT_S = 1200
VIDEOS_TOTAL_TIMEOUT_S = 2400
POLL_INTERVAL_S = 2.0

_FRAME_LABEL = {"first": "首帧", "last": "尾帧", "video": "视频"}


def _progress(event_cb, message: str, *, done: int = 0, total: int = 0,
              pct: Optional[int] = None) -> None:
    """向 turn 流发 tool.progress（loop 侧包装器注入 step_id/call_id/name）。"""
    if event_cb is None:
        return
    ev = {"type": "tool.progress", "message": message, "done": done, "total": total}
    if pct is not None:
        ev["pct"] = pct
    event_cb(ev)


def _effective_first_frame_url(proj, shot) -> str:
    """与 routers/shots.py 同语义的 effective 首帧公网 URL（只读复制，避免 import router）。
    inherit_previous -> 来源镜头末帧 url；其余 -> 本镜已选首帧候选 url。"""
    from ..core.models import OpeningSource
    if shot.opening_source == OpeningSource.INHERIT_PREVIOUS.value and shot.source_shot_id:
        prev = proj.get_shot(shot.source_shot_id)
        if prev and prev.video_end_frame.url_ready:
            return prev.video_end_frame.url or ""
        return ""
    sel = shot.first_frame.selected
    return (sel.url if sel else "") or ""


def _parse_image_targets(args: dict) -> list:
    """targets: [{shot_id, frame: first|last}]。宽容解析（_loose_json），返回规范 target 列表。"""
    raw = _loose_json(args.get("targets"))
    if not isinstance(raw, list) or not raw:
        raise AgentError("bad_arguments",
                         "targets 必须是非空数组，每项 {shot_id, frame}（frame: first|last，"
                         "单个镜头也放进数组传一项）")
    if len(raw) > MAX_IMAGE_BATCH:
        raise AgentError("bad_arguments",
                         f"单批最多 {MAX_IMAGE_BATCH} 个图片生成目标（收到 {len(raw)}）。"
                         "分多批提交。")
    targets = []
    seen = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not str(item.get("shot_id") or "").strip():
            raise AgentError("bad_arguments", f"targets[{i}] 缺少 shot_id")
        sid = str(item["shot_id"]).strip()
        frame = str(item.get("frame") or "first").strip().lower()
        if frame not in ("first", "last"):
            raise AgentError("bad_arguments",
                             f"targets[{i}] 的 frame 只支持 first|last（收到 {frame}）")
        key = (sid, frame)
        if key in seen:
            raise AgentError("bad_arguments",
                             f"targets 中 ({sid}, {frame}) 重复：同一镜头同一帧一批只出现一次")
        seen.add(key)
        targets.append({"shot_id": sid, "frame": frame})
    return targets


def _parse_video_targets(args: dict) -> list:
    """shot_ids: 非空字符串数组（≤4）。"""
    raw = _loose_json(args.get("shot_ids"))
    if not isinstance(raw, list) or not raw:
        raise AgentError("bad_arguments",
                         "shot_ids 必须是非空数组（单个镜头也传一项）")
    if len(raw) > MAX_VIDEO_BATCH:
        raise AgentError("bad_arguments",
                         f"单批最多 {MAX_VIDEO_BATCH} 个视频（视频串行生成，收到 {len(raw)}）。"
                         "分多批提交，或先只做关键镜头。")
    targets = []
    seen = set()
    for i, v in enumerate(raw):
        sid = str(v or "").strip()
        if not sid:
            raise AgentError("bad_arguments", f"shot_ids[{i}] 为空")
        if sid in seen:
            raise AgentError("bad_arguments", f"shot_ids 含重复镜头：{sid}")
        seen.add(sid)
        targets.append({"shot_id": sid, "frame": "video"})
    return targets


def _precheck_targets(proj, targets: list, *, kind: str, force: bool) -> tuple[list, list]:
    """提交前结构预检。返回 (submittable, blocked)。不达标的【不提交】（不烧配额），
    以结构化分项返回原因，让 Director 自己决定下一步（如先生成首帧）。"""
    submittable, blocked = [], []
    pending_first_ids = {t["shot_id"] for t in targets if t["frame"] == "first"}
    for t in targets:
        shot = proj.get_shot(t["shot_id"])
        if shot is None:
            blocked.append({**t, "status": "not_found", "ok": False, "verified": False,
                            "error": f"镜头 {t['shot_id']} 不存在"})
            continue
        if kind == "image":
            if shot.locked and not force:
                blocked.append({**t, "status": "locked", "ok": False, "verified": False,
                                "error": "镜头已锁定（锁定的镜头默认跳过生成）"})
                continue
            if t["frame"] == "last":
                if shot.link_mode != "keyframes":
                    blocked.append({**t, "status": "blocked", "ok": False, "verified": False,
                                    "error": f"仅 keyframes 模式需要尾帧（当前 {shot.link_mode}）"})
                    continue
                if not _effective_first_frame_url(proj, shot) and t["shot_id"] not in pending_first_ids:
                    blocked.append({**t, "status": "blocked", "ok": False, "verified": False,
                                    "error": "首帧未就绪：先生成并选择首帧，再生成尾帧"})
                    continue
        else:  # video
            if shot.locked:
                blocked.append({**t, "status": "locked", "ok": False, "verified": False,
                                "error": "镜头已锁定"})
                continue
            if str(getattr(shot, "link_mode", "") or "") == "multi":
                # multi 不消费 boundary frames（video_adapter：参考即视觉输入，
                # 非 first-frame conditioning）——首帧不是前置（39D 用户定版：
                # 首镜无分镜直生，角色+大致场景到位即可，严格限制伤首镜效果）。
                # relay 媒体就绪在此把关（缺失 fail closed 与 adapter 同口径）。
                if str(getattr(shot, "video_reference_asset_id", "") or "") == "prev_tail":
                    from src.generation.video_adapter import resolve_multi_relay_source
                    if resolve_multi_relay_source(proj, shot) is None:
                        blocked.append({**t, "status": "blocked", "ok": False,
                                        "verified": False,
                                        "error": ("relay 未就绪：vref=prev_tail 需要"
                                                  "上一镜实际视频末帧（先完成上一镜"
                                                  "生成与末帧提取）")})
                        continue
                submittable.append(t)
                continue
            if not _effective_first_frame_url(proj, shot):
                blocked.append({**t, "status": "blocked", "ok": False, "verified": False,
                                "error": ("开场画面未就绪：i2v/keyframes 模式需要先生成"
                                          "并选择首帧（inherit 接力镜头需来源镜头末帧就绪）")})
                continue
        submittable.append(t)
    return submittable, blocked


def _verify_one(on_disk, shot_id: str, frame: str, marker: str) -> Optional[dict]:
    """写后重读 canonical：按 origin_run_id==marker 精确找本批生成的资产 + 本地文件存在。
    返回 {local, url, seconds?}；未确认返回 None。"""
    shot = on_disk.get_shot(shot_id)
    if shot is None:
        return None
    if frame == "video":
        for vv in (shot.video_versions or []):
            if vv.origin_run_id == marker and vv.local and Path(vv.local).exists():
                return {"local": vv.local, "url": vv.url or "",
                        "seconds": vv.seconds_returned}
        return None
    kind = "first_frame" if frame == "first" else "last_frame"
    batches = shot.first_frame_batches if kind == "first_frame" else shot.last_frame_batches
    for b in (batches or []):
        if b.origin_run_id != marker or b.kind != kind:
            continue
        for c in (b.candidates or []):
            if c.local and Path(c.local).exists():
                return {"local": c.local, "url": c.url or ""}
    return None


async def _execute_generation(op: str, rt: AgentRuntime, project: str, arguments: str,
                              instruction: str, message_id: Optional[str],
                              event_cb=None, cancel_event=None) -> dict:
    """shot.generate_images / shot.generate_videos 共用实现：
    校验 → 预检 → 提交 JobQueue → 轮询等待（进度/取消/总超时）→ 重读 canonical 验证 → 回执。"""
    args = _parse_args(arguments)
    is_image = op == "shot.generate_images"
    variants = 1
    force = False
    video_model = None
    reference_policy = "legacy_allowed"
    if is_image:
        targets = _parse_image_targets(args)
        variants = max(1, min(2, int(args.get("variants") or 1)))
        force = bool(args.get("force", False))
        reference_policy = (str(args.get("reference_policy") or "legacy_allowed").strip()
                            or "legacy_allowed")
        if reference_policy not in ("legacy_allowed", "bound_required"):
            raise AgentError("bad_arguments",
                             f"reference_policy 必须是 legacy_allowed | bound_required，"
                             f"得到: {reference_policy}")
        # Phase D2（30.md §6）：场景参考策略透传到生成 handler（省略=None→adapter 默认 full）
        scene_reference_mode = (str(args.get("scene_reference_mode") or "").strip().lower()
                                or None)
        if scene_reference_mode is not None and scene_reference_mode not in (
                "full", "context_only", "auto"):
            raise AgentError("bad_arguments",
                             f"scene_reference_mode 必须是 full | context_only | auto，"
                             f"得到: {scene_reference_mode}")
        # D2 §5.2：人形参考策略（auto=紧景别自动不发 look 参考，identity 走 prompt 文本）
        human_reference_mode = (str(args.get("human_reference_mode") or "").strip().lower()
                                or None)
        if human_reference_mode is not None and human_reference_mode not in (
                "keep", "drop", "auto"):
            raise AgentError("bad_arguments",
                             f"human_reference_mode 必须是 keep | drop | auto，"
                             f"得到: {human_reference_mode}")
        # Phase51 B（E2 实证）：人数纪律——默认 true（确定性人数排除子句进
        # prompt tail，按 shot.characters 计数；E2 对照：无条款 0/2 vs 有条款 2/2）。
        # 创作意图含额外人物时导演显式传 false（schema 已写明默认）。
        population_constraint = bool(args.get("population_constraint", True))
        total_timeout = IMAGES_TOTAL_TIMEOUT_S
        asset_noun = "分镜图"
    else:
        targets = _parse_video_targets(args)
        total_timeout = VIDEOS_TOTAL_TIMEOUT_S
        asset_noun = "视频"
        # Phase51 B：视频层人数纪律（与首帧层同参数对称；E2 实证 + P50 用户观察
        # 「宽/中景 i2v 船员消失」）。默认 true，创作意图含额外人物时显式 false。
        population_constraint = bool(args.get("population_constraint", True))
        # Phase55 A（任务书 §3）：视频统一提交契约——参考策略解析（显式参数 >
        # 项目级活动 run 策略 > 默认 bound_required 基线等价：六员链连续性=BOUND
        # 参考链；无绑定视频退化为首帧单图 i2v，场景/Look 不进参考链=Phase54
        # 场景漂移根因，不静默降级）。legacy_allowed 为显式逃生口（回执/manifest
        # 记录，不与基线对照混算）。
        raw_reference_policy = (str(args.get("reference_policy") or "").strip() or None)
        # 视频模型（2026-09-28：v2.0 已删除，agnes-video-2.5-flash 为唯一模型——
        # config 默认，无需显式选择。对 config 实际配置校验（client 不可用时退回
        # 已知默认集合，保证拒绝语义确定）。
        raw_model = str(args.get("video_model") or "").strip()
        if raw_model:
            allowed: set = set()
            try:
                _client = rt._resolve_client_or_none(agent_id="director")
                if _client is not None:
                    allowed = {v for v in (_client.models or {}).values()
                               if isinstance(v, str)}
            except Exception:
                allowed = set()
            if not allowed:
                allowed = {"agnes-video-2.5-flash"}
            if raw_model not in allowed:
                raise AgentError("bad_arguments",
                                 f"未知视频模型 {raw_model}；可选：{sorted(allowed)}")
            video_model = raw_model

    q = rt._resolve_job_queue_or_none()
    if q is None:
        raise AgentError("job_queue_unavailable",
                         "任务队列不可用（服务未就绪），无法生成视觉资产")

    pdir = rt._resolve_project_dir(project)
    if not is_image:
        from src.generation.video_contract import resolve_reference_policy
        reference_policy = resolve_reference_policy(pdir, explicit=raw_reference_policy)
    proj = ProjectStore(pdir).load()
    submittable, blocked = _precheck_targets(proj, targets,
                                             kind="image" if is_image else "video",
                                             force=force)

    # ---- Phase51 C：修复范围服务端硬闸（run_36919eb5a073 实证：提示词范围约束
    #      对导演无效，授权 4 镜实际提交 20 镜）——清单外镜头在预检直接拒绝，
    #      不进队列不耗配额。scope 由 continue_run(rejected, allowedShotIds) 设置。----
    if submittable:
        from src.workflow.repair_scope import check_submission
        scope_blocked: list = []
        scope_ok: list = []
        for t in submittable:
            ok, why = check_submission(project, t.get("shot_id"))
            (scope_ok if ok else scope_blocked).append(
                t if ok else {**t, "status": "repair_scope_excluded", "ok": False,
                              "verified": False, "error": "repair_scope_excluded",
                              "fix_hint": why})
        if scope_blocked:
            blocked = blocked + scope_blocked
            submittable = scope_ok
            if not submittable:
                return _base_receipt(
                    op, ok=False, target=",".join(t["shot_id"] for t in scope_blocked),
                    revision=None, verified=False, results=scope_blocked,
                    count=len(scope_blocked), done=0, failed=len(scope_blocked),
                    cancelled=False, timed_out=False,
                    note="全部目标被修复范围硬闸拒绝（repair_scope_excluded）——"
                         "本轮修复只允许提交清单内镜头；清单外既有合格候选已计入"
                         "完成判定，无需重新生成。")

    # ---- Phase C1（27.md §11.3）：bound_required 参考策略——提交前绑定门槛 ----
    # UNBOUND → binding_required（绝不静默 legacy）；NOT_READY → 结构化 issues（§13：
    # 回执必须携带 production_state/issue codes/hints，Agent 能据此行动）；READY → 放行。
    if is_image and reference_policy == "bound_required" and submittable:
        from src.shot_binding.service import ShotBindingService
        svc = ShotBindingService(pdir, project)
        policy_blocked: list = []
        allowed: list = []
        for t in submittable:
            v = svc.read_validity(t["shot_id"])
            readiness = v.get("readiness")
            if readiness == "READY":
                allowed.append(t)
                continue
            issues = v.get("issues") or []
            if readiness == "UNBOUND":
                err = "binding_required"
                hint = ("该镜头没有生产绑定（ ShotProductionBinding）——bound_required "
                        "策略禁止静默回落 legacy 参考链。先 shot.production.inspect 看"
                        "缺什么（approved Look/场景视图），再 shot.production.bind。")
                state = "unbound"
            else:
                err = "binding_not_ready"
                hint = ("生产绑定存在但未就绪（见 issues）。逐项解决依赖问题后 "
                        "shot.production.refresh 或报告相应 prerequisite；不要原样重试生成。")
                state = "not_ready"
            policy_blocked.append({
                **t, "status": err, "ok": False, "verified": False,
                "error": err,
                "production_state": state,
                "binding_revision": v.get("binding_revision"),
                "issues": [{"reason": i.get("reason"),
                            "detail": str(i.get("detail") or "")[:160]}
                           for i in issues][:6],
                "missing_stale_dependencies": [
                    (i.get("ref") or i.get("kind") or "") for i in issues
                    if i.get("reason") not in (None, "ok")][:6],
                "fix_hint": hint,
            })
        submittable = allowed
        blocked = blocked + policy_blocked

    # ---- Phase55 A（任务书 §3）：视频统一提交契约——入队前合法性编译与校验 ----
    # 三个入口（director 工具/producer capability/REST）共用 src/generation/
    # video_contract.py：时长契约、multi 需 binding、BOUND 基线等价（默认）、
    # negative_prompt 编译、未知参数拒绝；每个放行目标生成统一输入依据清单
    # （input_manifest，随 job.params 落盘并并入 VideoVersion.generation_basis）。
    video_contract_state: dict = {}   # shot_id -> {manifest, scene_reference}
    if not is_image and submittable:
        from src.generation.video_contract import (
            build_input_manifest,
            policy_requires_binding,
            scene_reference_consumed as _scene_ref_consumed,
            validate_video_submission,
        )
        from src.shot_binding.service import ShotBindingService
        _vc_svc = ShotBindingService(pdir, project)
        _vc_require_binding = policy_requires_binding(reference_policy)
        _vc_allowed: list = []
        for t in submittable:
            _vc_shot = proj.get_shot(t["shot_id"])
            if _vc_shot is None:   # _precheck_targets 已拦，防御性放行
                _vc_allowed.append(t)
                continue
            _vc_validity = _vc_svc.read_validity(t["shot_id"])
            _vc_chk = validate_video_submission(
                _vc_shot, require_binding=_vc_require_binding,
                validity=_vc_validity,
                policy=reference_policy, project=proj, pdir=pdir)
            if not _vc_chk["ok"]:
                for _viol in _vc_chk["violations"]:
                    blocked.append({
                        **t, "status": _viol["code"], "ok": False, "verified": False,
                        "error": _viol["code"],
                        "detail": _viol["detail"], "fix_hint": _viol["fix_hint"],
                        **({"issues": _viol["issues"]} if "issues" in _viol else {}),
                        "reference_policy": reference_policy,
                        "binding_revision": _vc_validity.get("binding_revision"),
                    })
                continue
            _vc_mode = ("keyframes" if _vc_shot.link_mode == "keyframes"
                        else "multi" if _vc_shot.link_mode == "multi" else "i2v")
            # v2 路径 validate 内已算 scene_ref——复用，不重复判定（v1 路径回退原逻辑）
            _vc_sref = _vc_chk.get("scene_reference_consumed") or \
                _scene_ref_consumed(proj, _vc_shot, pdir, validity=_vc_validity)
            from src.generation.video_contract import planned_video_seconds
            video_contract_state[t["shot_id"]] = {
                "manifest": build_input_manifest(
                    proj, _vc_shot, stage="video", mode=_vc_mode,
                    video_model=video_model,
                    seconds=planned_video_seconds(_vc_shot),
                    project_dir=pdir, validity=_vc_validity,
                    population_constraint=population_constraint,
                    scene_ref=_vc_sref,
                    compiled_negative_prompt=bool(_vc_chk["compiled_negative_prompt"])),
                "scene_reference": _vc_sref,
            }
            _vc_allowed.append(t)
        submittable = _vc_allowed

    # ---- Phase 39D 修正（用户定版）：输入语义分类 + 分角色检查口径 ----
    # reference_only（multi 参考槽 / 分镜图 refs）：姿态命中只记 soft 诊断
    # （pose_reference_mismatch_soft）——绝不阻断/不要求补参考/不升级 major/
    # 不请用户改故事；relay 边界不做姿态预判（生成后接缝校验）；仅硬首帧槽
    # 的确定性边界冲突阻断。预检只读，自身失败绝不阻断生成。
    from src.agents.pregen_checks import shot_input_semantics
    reference_roles: list = []
    pose_soft: list = []
    still_ok: list = []
    # 39F（41.md §10）：fail-open 保持——预检自身异常不阻断生成，但绝不静默
    # 冒充「无冲突」。pregen_check_unavailable 只表示「本轮未完成确定性预检」，
    # 绝不表示「已证明没有冲突」。
    pregen_unavailable: list = []
    for t in submittable:
        sem = None
        try:
            _shot_obj = proj.get_shot(t["shot_id"])
            if _shot_obj is not None:
                sem = shot_input_semantics(pdir, pdir.name, proj, _shot_obj)
        except Exception:
            sem = None
        if sem is None:
            still_ok.append(t)
            pregen_unavailable.append({
                "shot_id": t["shot_id"], "frame": t.get("frame"),
                "diagnostic": "pregen_check_unavailable",
                "hint": "本轮未完成确定性输入预检（fail-open 放行，不阻断生成）"
                        "——这不表示已证明无冲突"})
            continue
        reference_roles.append(sem)
        if sem.get("boundary_conflict") is not None:
            blocked.append({
                **t, "status": "hard_start_boundary_conflict", "ok": False,
                "verified": False, "error": "hard_start_boundary_conflict",
                "boundary_conflict": sem["boundary_conflict"],
                "pose_gate": sem.get("pose_gate"),
                "fix_hint": ("硬首帧边界与 opening 状态冲突（静态姿态参考资产被"
                             "选作首帧 × 位移动作开场）。重新选择/生成符合 opening "
                             "的首帧，或改用不需要硬首帧的 mode（multi 直生），"
                             "或如实报告 provider 限制。不得因此改写故事。")})
            continue
        if sem.get("soft_conflict") is not None:
            pose_soft.append({"shot_id": t["shot_id"],
                              "diagnostic": "pose_reference_mismatch_soft",
                              **sem["soft_conflict"]})
        still_ok.append(t)
    submittable = still_ok

    # 提交：origin_run_id = 每目标唯一 marker（ImageBatch/VideoVersion 记录同值，
    # 写后验证按它精确匹配；JobQueue.find_by_origin 也可据此查询）。
    jobs: list = []   # (target, job, marker)
    for t in submittable:
        marker = f"dgv_{uuid.uuid4().hex[:8]}"
        kind = {"first": "first_frame", "last": "last_frame", "video": "video"}[t["frame"]]
        extra = {"origin_run_id": marker}
        if is_image:
            extra.update(variants=variants, force=force)
            if scene_reference_mode:
                extra["scene_reference_mode"] = scene_reference_mode
            if human_reference_mode:
                extra["human_reference_mode"] = human_reference_mode
            extra["population_constraint"] = population_constraint
        elif video_model:
            extra["video_model"] = video_model
        extra["population_constraint"] = population_constraint
        if not is_image and t["shot_id"] in video_contract_state:
            extra["input_manifest"] = video_contract_state[t["shot_id"]]["manifest"]
        job = q.submit(project, kind, t["shot_id"], **extra)
        jobs.append((t, job, marker))

    total = len(targets)
    if not jobs:
        policy_hint = ("；bound_required 策略下 binding_required/not_ready 项请先检查生产"
                       "前置（inspect→bind），不要降级 legacy"
                       if is_image and reference_policy == "bound_required" else "")
        return _base_receipt(
            op, ok=False, target=",".join(t["shot_id"] for t in targets),
            revision=None, verified=False,
            results=blocked, count=total, done=0, failed=total,
            cancelled=False, timed_out=False,
            reference_policy=reference_policy if is_image else None,
            **({"variants": variants} if is_image else {}),
            pregen_unavailable_diagnostics=pregen_unavailable,
            note=f"没有可提交的生成目标（{total} 项全部被预检拦截），未消耗生成额度。"
                 "请根据各项 error/fix_hint 先补齐前置条件（如先生成首帧、补生产绑定）"
                 + policy_hint + "。")

    _progress(event_cb, f"已提交 {len(jobs)} 个{asset_noun}生成任务，开始等待完成…",
              done=0, total=total, pct=0)

    started = time.time()
    deadline = started + total_timeout
    cancelled = timed_out = False
    while True:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            for _, job, _m in jobs:
                if job.state in ("queued", "running"):
                    try:
                        q.cancel(job.job_id, force=True)
                    except Exception:
                        pass
            break
        n_terminal = 0
        n_running = 0
        running_sid = ""
        for _t, job, _m in jobs:
            if job.state in ("done", "failed", "cancelled"):
                n_terminal += 1
            elif job.state == "running":
                n_running += 1
                running_sid = job.shot_id
        if n_terminal >= len(jobs):
            break
        if time.time() > deadline:
            timed_out = True   # 不取消：未完成 job 留队列继续跑，自行落盘
            break
        n_ok = sum(1 for _t, job, _m in jobs if job.state == "done")
        pct = int(n_ok / len(jobs) * 100)
        if is_image:
            msg = f"正在生成分镜图… 已完成 {n_ok}/{len(jobs)}"
            if n_running:
                msg += f"，进行中 {n_running}"
            _progress(event_cb, msg, done=n_ok, total=len(jobs), pct=pct)
        else:
            lead = (f"正在生成 {running_sid} 视频…" if running_sid
                    else "视频排队中（串行 worker，1 RPM）…")
            _progress(event_cb, f"{lead} 已完成 {n_ok}/{len(jobs)}",
                      done=n_ok, total=len(jobs), pct=pct)
        await asyncio.sleep(POLL_INTERVAL_S)

    # 分项状态（job.state 是 live 对象，终态由 handler/queue 原位更新）
    results: list = list(blocked)
    for t, job, marker in jobs:
        st = job.state
        base = {**t, "job_id": job.job_id, "origin_run_id": marker, "status": st,
                "ok": False, "verified": False, "local": None, "url": None}
        if st == "done":
            results.append(base)
        elif st == "failed":
            results.append({**base, "error": (job.error or "生成失败")[:300]})
        else:  # cancelled / queued / running（后两者 = 超时或取消时仍未完成）
            if cancelled:
                results.append({**base, "status": "cancelled",
                                "error": "已取消（用户停止），已完成的其他资产保留"})
            elif timed_out:
                results.append({**base, "status": "timeout",
                                "error": (f"总超时（>{total_timeout}s）仍在后台继续，"
                                          "完成后会自行写入分镜数据，可稍后用 shot.list 查看")})
            else:
                results.append({**base, "error": f"任务异常终态：{st}"})

    # 写后重读 canonical：done 分项按 marker 精确验证（批次/版本存在 + 本地文件存在）
    on_disk = ProjectStore(pdir).load()
    for r in results:
        if r.get("status") != "done":
            continue
        marker = next((m for tt, _j, m in jobs
                       if tt["shot_id"] == r["shot_id"] and tt["frame"] == r["frame"]), None)
        v = _verify_one(on_disk, r["shot_id"], r["frame"], marker) if marker else None
        if v is None:
            r["error"] = "job 已完成但重读 canonical 未确认资产落盘（文件缺失）"
        else:
            r["ok"] = True
            r["verified"] = True
            r["local"] = v["local"]
            r["url"] = v.get("url")
            if r["frame"] == "video":
                r["seconds"] = v.get("seconds")

    done_items = [r for r in results if r.get("ok") and r.get("verified")]
    # 39F：预检不可用的目标，逐项如实带标记（非阻断、纯信息）
    _pregen_unavail_ids = {(u["shot_id"], u.get("frame")) for u in pregen_unavailable}
    for r in results:
        if (r.get("shot_id"), r.get("frame")) in _pregen_unavail_ids:
            r["pregen_check"] = "unavailable"
    elapsed = round(time.time() - started, 1)
    ok = bool(done_items)
    # verified 语义：所有【报告完成】的分项都经 canonical+文件验证（无假 done）。
    # 部分成功时 ok=True 且 verified=True —— 失败分项在 results/note 里如实列出。
    verified = ok and all(r.get("verified") for r in results if r.get("status") == "done")

    if done_items:
        n_fail = total - len(done_items)
        summary_tail = (f"，{n_fail} 个未完成" if n_fail else "")
        DirectEditAuditStore(pdir, project).record(
            agent_id="director",
            field="generate_images" if is_image else "generate_videos",
            before=None,
            after={"targets": [{"shot_id": r["shot_id"], "frame": r["frame"]}
                               for r in done_items]},
            instruction=instruction, message_id=message_id)
        rt.record_work(project, "change",
                       f"Director 生成了 {len(done_items)} 个{asset_noun}{summary_tail}",
                       participants=["director"], affected_domains=["storyboard"],
                       references={"shot_ids": sorted({r["shot_id"] for r in done_items})},
                       status="completed")

    parts = []
    for r in results:
        tag = f"{r['shot_id']}{_FRAME_LABEL.get(r['frame'], r['frame'])}"
        if r.get("ok"):
            parts.append(f"{tag} ✓")
        else:
            parts.append(f"{tag} ✗（{r.get('error') or r.get('status')}）")
    note = "；".join(parts) + f"。耗时 {elapsed}s。"
    if cancelled:
        note += " 生成已取消（用户停止），已完成资产已保留在分镜数据中。"
    elif timed_out:
        note += (" 已达本工具总超时，未完成项仍在后台继续（完成后会自行写入分镜数据），"
                 "可稍后用 shot.list 查看最新状态。")
    if is_image and done_items:
        note += " 首帧候选已自动选中第一张（可在分镜页更换）；图片请用户在 UI 中查看验收。"
    if pose_soft:
        note += (" 提示：" + "；".join(
            f"{w['shot_id']} 参考姿态 soft 风险（pose_reference_mismatch_soft，"
            f"{'/'.join(w['matched_motion_keywords'][:2])}）" for w in pose_soft)
            + "——参考为身份语义不约束首帧姿态，由 prompt+negative_prompt "
              "steering，生成后按 actual tail 裁定。")
    if pregen_unavailable:
        note += (" 提示：" + "、".join(u["shot_id"] for u in pregen_unavailable)
                 + f" 共 {len(pregen_unavailable)} 项本轮未完成确定性输入预检"
                   "（pregen_check_unavailable，fail-open 放行）——这不表示已证明无冲突。")

    return _base_receipt(
        op, ok=ok, target=",".join(t["shot_id"] for t in targets),
        revision=None, verified=verified,
        results=results, count=total, done=len(done_items),
        failed=total - len(done_items),
        cancelled=cancelled, timed_out=timed_out,
        reference_policy=reference_policy if is_image else None,
        **({"variants": variants} if is_image else {}),
        reference_roles=reference_roles,
        pose_soft_diagnostics=pose_soft,
        pregen_unavailable_diagnostics=pregen_unavailable,
        elapsed_s=elapsed, note=note)


async def _execute_shot_generate_images(rt: AgentRuntime, project: str, arguments: str,
                                        instruction: str, message_id: Optional[str],
                                        event_cb=None, cancel_event=None) -> dict:
    """shot.generate_images：批量为镜头生成首帧/尾帧图片（复用 first_frame/last_frame
    JobQueue handler）。长任务：总超时 IMAGES_TOTAL_TIMEOUT_S，响应协作取消（已完成保留），
    部分成功不整批作废；obs 不含图片内容，只含 canonical 路径。"""
    return await _execute_generation(
        "shot.generate_images", rt, project, arguments, instruction, message_id,
        event_cb=event_cb, cancel_event=cancel_event)


async def _execute_shot_generate_videos(rt: AgentRuntime, project: str, arguments: str,
                                        instruction: str, message_id: Optional[str],
                                        event_cb=None, cancel_event=None) -> dict:
    """shot.generate_videos：批量为镜头生成视频（复用 video JobQueue handler，串行 worker）。
    前置：effective 首帧就绪（i2v 用本镜首帧，inherit 接力用来源末帧）。长任务语义同上。"""
    return await _execute_generation(
        "shot.generate_videos", rt, project, arguments, instruction, message_id,
        event_cb=event_cb, cancel_event=cancel_event)


# ============ 工具 schema（标准 OpenAI function 格式）============
SHOT_GENERATE_IMAGES_TOOL = {
    "type": "function",
    "function": {
        "name": "shot.generate_images",
        "description": (
            "批量为镜头生成分镜图（首帧/尾帧）。复用项目既有图片生成管线：自动拼提示词、"
            "带出场角色的三视图参考图与接力参考图，生成后写入镜头数据并在分镜页展示"
            "（图片内容不会返回给你，成功与否以工具返回的 canonical 路径为准）。"
            "targets 每项 {shot_id, frame}：frame=first 生成首帧；frame=last 生成尾帧"
            "（仅 keyframes 模式，且须先有就绪首帧）。单个镜头也传一项。"
            "视频依赖首帧：要先给某镜生成视频，先把它的 frame=first 生成好。"
            "每次调用都会生成新候选（自然 reroll）；批内并发生成、整批等待，"
            f"单批最多 {MAX_IMAGE_BATCH} 项，很慢（每张几十秒），不要为同一镜头反复重发。"),
        "parameters": {
            "type": "object",
            "properties": {
                "targets": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_IMAGE_BATCH,
                    "items": {
                        "type": "object",
                        "properties": {
                            "shot_id": {"type": "string",
                                        "description": "镜头 id（如 s08_c03）。"},
                            "frame": {"type": "string", "enum": ["first", "last"],
                                      "description": "first=首帧（默认）；last=尾帧（仅 keyframes，需首帧就绪）。"},
                        },
                        "required": ["shot_id"],
                    },
                },
                "variants": {"type": "integer", "enum": [1, 2],
                             "description": "每个首帧生成几个候选（默认 1；2 会多花一次生成额度，用户想挑图时才用）。"},
                "force": {"type": "boolean",
                          "description": "镜头被锁定时是否仍生成（默认 false）。"},
                "reference_policy": {"type": "string", "enum": ["legacy_allowed", "bound_required"],
                                     "description": "参考策略（Phase C1）：bound_required=正式生产——"
                                                    "必须已有 READY 生产绑定，UNBOUND/NOT_READY 会被拒绝"
                                                    "（绝不静默回落 legacy）；legacy_allowed=旧行为"
                                                    "（未绑定的镜头走 legacy 参考链）。省略=legacy_allowed。"},
                "human_reference_mode": {"type": "string", "enum": ["keep", "drop", "auto"],
                                         "description": "人形参考策略（D2）：keep=每角色 1 张 look "
                                                        "参考；drop=不发人形参考（identity 走提示词"
                                                        "文本——紧景别推荐，实验证据：构图遵从显著改善）；"
                                                        "auto=紧景别(context_only)自动 drop，其余 keep。"
                                                        "省略=auto。"},
                "scene_reference_mode": {"type": "string", "enum": ["full", "context_only", "auto"],
                                         "description": "场景参考策略（Phase D2）：full=场景图作为生图参考"
                                                        "（空间身份最强，适合大远景/全景确立空间）；"
                                                        "context_only=不发场景图，场景语义仍进提示词"
                                                        "（近景/特写/OTS/insert 推荐——防止场景图把构图"
                                                        "拉宽）；auto=按景别确定性映射（EWS/WS→full，"
                                                        "MCU/CU/ECU/OTS/insert/MS→context_only）。"
                                                        "省略=full（Phase D 行为）。"},
                "population_constraint": {"type": "boolean",
                                          "description": "人数纪律（Phase51 B，E2 实证）：true=按"
                                                         " shot.characters 确定性人数排除子句进生成"
                                                         " prompt tail（宽/中景防船员缺员/防意外第三人；"
                                                         "对照实验：无条款 0/2 双人齐全 vs 有条款 2/2）。"
                                                         "默认 true；镜头确需路人/人群等额外人物时"
                                                         "显式传 false（创作意图由你判断）。"},
            },
            "required": ["targets"],
        },
    },
}

SHOT_GENERATE_VIDEOS_TOOL = {
    "type": "function",
    "function": {
        "name": "shot.generate_videos",
        "description": (
            "批量为镜头生成视频（复用项目既有视频管线：串行 worker、1 RPM，每个约 1~3 分钟）。"
            "前置条件（不满足的项会被结构化拒绝、不消耗额度）：该镜头的有效开场画面就绪——"
            "普通镜头用本镜已生成并选择的首帧；inherit 接力镜头用来源镜头的末帧"
            "（不要破坏接力关系，也不要替接力镜头生成本镜首帧）。视频内容不返回给你，"
            "成功与否以工具返回的 canonical 路径为准。单个镜头也传一项。"
            f"单批最多 {MAX_VIDEO_BATCH} 个；keyframes 模式会自动用尾帧做双帧过渡。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shot_ids": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_VIDEO_BATCH,
                    "items": {"type": "string"},
                    "description": f"要生成视频的镜头 id 数组（1~{MAX_VIDEO_BATCH} 个）。",
                },
                "video_model": {
                    "type": "string",
                    "enum": ["agnes-video-2.5-flash"],
                    "description": (
                        "视频模型（可选，缺省 = 项目默认 agnes-video-2.5-flash——"
                        "2026-09-28 起 v2.0 已删除，本参数通常无需传）。"
                        "4-12 秒 720P，Token Plan 500 秒/天。"),
                },
                "population_constraint": {
                    "type": "boolean",
                    "description": (
                        "人数纪律（Phase51 B，E2 实证）：true=按 shot.characters 确定性"
                        "人数排除子句进视频 prompt tail（防 i2v 过程中船员缺员/意外第三人；"
                        "首帧层同参数对称）。默认 true；镜头确需路人/人群时显式传 false。"),
                },
                "reference_policy": {
                    "type": "string",
                    "enum": ["bound_required", "legacy_allowed"],
                    "description": (
                        "参考链契约（Phase55 A）：bound_required（默认）=要求本镜已建立"
                        "生产绑定（BOUND）——场景/角色参考真实进入视频参考链，基线等价"
                        "策略；legacy_allowed=显式放弃参考链（退化为首帧单图 i2v，"
                        "场景漂移风险自负，回执与 manifest 如实标注）。"),
                },
            },
            "required": ["shot_ids"],
        },
    },
}

SHOT_GENERATE_TOOL_NAMES = {"shot.generate_images", "shot.generate_videos"}
