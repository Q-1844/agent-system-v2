# src/storyboard/bridge_service.py
# Phase 39（39.md §6.4/§15-§20）：keyframes 接续桥的高层正式操作——把 38 两座桥
# （_38_bridge_m3b.py / _38_bridge_m5b.py）实证的七步 script 手术收进 backend service。
#
# 定位（39.md §36）：Agent 只表达意图（"在这里插一座桥，过渡文本是X，时长Y"）；
# 机械正确性（插镜位次、relay、binding、basis 时序、boundary seed、提交）由本服务
# 的事务顺序保证。内部全部复用既有正式服务，绝不旁路：
#   insert_shots_after / update_shot_relay / update_shot_fields（structure_service）
#   ShotBindingService.create/refresh/compile_reference_pack（shot_binding）
#   continuity_service.extract_shot_frame + 注入的 image host（帧提取/托管）
#   JobQueue.submit（生成提交，与 REST POST /shot/{id}/video 同一队列）
#
# 时序纪律（39.md §18，违反=fail closed）：
#   binding refresh → compile basis → seed ending boundary → submit
#   seed 之后零 refresh——本服务在提交前重查 binding_revision 与 seed basis 一致，
#   不一致抛 boundary_frame_stale（绝不偷偷重新 seed）。
#   （生成路径的 BOUND boundary freshness gate（video_adapter.boundary_stale_detail）
#   是第二道独立防线；本服务的预提交守卫把同一语义提前到提交前。）
#
# 验收纪律（不在本服务内）：桥完成后由 Director 用 calibration.seam_measure（双侧）
# + visual.sample_frames（中间逐段）验收——本服务只保证构造正确。
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.models import ImageBatch, Candidate
from src.core.store import ProjectStore
from src.storyboard import continuity_service
from src.storyboard.structure_service import (
    StructureError, insert_shots_after, update_shot_fields, update_shot_relay,
)

BRIDGE_MIN_DURATION_S = 10.0   # 39.md §17：桥时长固定 10s 起步（M5B a1 5s 硬切实证教训）
SEED_PROMPT_TAG = "[bridge seed]"
# 中间节拍分隔符（39.md §17：transition_description 必须给中间节拍；M5B 用 →，
# M3B 用 -；不允许「两点一线」描述）。
_BEAT_SEPARATORS = ("→", "->", "、", "，", ",", "；", ";", "。", "\n", "-")


class BridgeError(Exception):
    """结构化失败：code 供 agent tool/REST 转 4xx 与结构化回执。"""

    def __init__(self, code: str, message: str, **detail):
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(f"[{code}] {message}")


def count_transition_beats(transition_description: str) -> int:
    """中间节拍数：按分隔符切分后的非空段数。≥3 才算有铺路节拍
    （A → B → endpoint 最少 3 段）。"""
    text = str(transition_description or "").strip()
    if not text:
        return 0
    import re
    parts = [p.strip() for p in re.split("|".join(_BEAT_SEPARATORS), text) if p.strip()]
    return len(parts)


def validate_bridge_request(duration_s: float, transition_description: str) -> None:
    """确定性验收前移：时长 10s 起步 + 中间节拍 ≥3。违反抛 BridgeError。"""
    try:
        d = float(duration_s)
    except (TypeError, ValueError):
        raise BridgeError("invalid", f"duration_s 非法: {duration_s!r}")
    if d < BRIDGE_MIN_DURATION_S:
        raise BridgeError(
            "bridge_duration_below_minimum",
            f"桥时长固定 {BRIDGE_MIN_DURATION_S:.0f}s 起步（收到 {d}s）——短时长没有 "
            f"morph 空间，provider 会在两钉点之间直接硬切（M5B a1 5s 实证教训）",
            duration_s=d, minimum=BRIDGE_MIN_DURATION_S)
    beats = count_transition_beats(transition_description)
    if beats < 3:
        raise BridgeError(
            "bridge_transition_beats_missing",
            f"transition_description 必须包含明确中间节拍（≥3 段，如：低头→嗅沿瓜皮→"
            f"绕半步→鼻贴瓜皮），得到 {beats} 段——两点一线的描述会诱发切变"
            f"（M5B a2 10s+节拍定版实证）",
            beats=beats)


def seed_bridge_boundary(store: ProjectStore, shot_id: str, *,
                         candidate_local: str, candidate_url: str,
                         generation_basis: Dict[str, Any],
                         prompt_snapshot: str = "") -> Dict[str, Any]:
    """尾边界 seed 的正式化（38.md §2.3 步骤 5 的 script 手术 → backend service）：
    last_frame_batches 增批（candidates=[后继首帧], generation_basis=basis）
    + selected_last_frame 指针 + last_frame.mark_done。幂等由调用方（build 流程
    的 already_exists 分支）保证。返回 batch/candidate 凭据。"""
    now = time.time()
    cand_id = f"cand_bridge_lf_{uuid.uuid4().hex[:6]}"
    batch_id = f"batch_bridge_lf_{uuid.uuid4().hex[:6]}"
    batch = ImageBatch(
        batch_id=batch_id, kind="last_frame", created_at=now, status="ready",
        prompt_snapshot=prompt_snapshot or f"{SEED_PROMPT_TAG} next-shot first frame",
        candidates=[Candidate(candidate_id=cand_id, local=str(candidate_local),
                              url=candidate_url, status="done", created_at=now)],
        generation_basis=dict(generation_basis or {}))

    def mut(s):
        s.last_frame_batches.append(batch)
        s.selected_last_frame = cand_id
        s.last_frame.mark_done(local=str(candidate_local), url=candidate_url)

    store.update_shot(shot_id, mut)
    return {"batch_id": batch_id, "candidate_id": cand_id,
            "local": str(candidate_local), "url": candidate_url,
            "generation_basis": dict(generation_basis or {})}


def ensure_boundary_fresh(project_dir: Path | str, project_name: str,
                          shot_id: str, seed_basis: Dict[str, Any]) -> None:
    """seed 后零 refresh 的防线（39.md §6.4/§18）：seed basis 里记的 binding_revision
    若已被后续 refresh 顶掉 → boundary_frame_stale，fail closed，绝不重新 seed。
    UNBOUND（basis 无 binding_revision）不适用本守卫（生成路径本就无 basis 门槛）。"""
    expected_rev = str((seed_basis or {}).get("binding_revision") or "")
    if not expected_rev:
        return
    from src.shot_binding.service import ShotBindingService
    svc = ShotBindingService(Path(project_dir), project_name)
    cur = svc.read_binding(shot_id) or {}
    cur_rev = str(cur.get("binding_revision") or "")
    if cur_rev != expected_rev:
        raise BridgeError(
            "boundary_frame_stale",
            f"桥 {shot_id} 的尾边界 seed 后 binding 已被 refresh"
            f"（seed basis={expected_rev}，当前={cur_rev}）——seed 作废风险，"
            "fail closed：请重建桥（重新走 refresh→compile→seed→submit），绝不静默重新 seed",
            seed_binding_revision=expected_rev, current_binding_revision=cur_rev)


async def build_keyframes_bridge(
    project_dir: Path | str, project_name: str, *,
    after_shot_id: str, transition_description: str, duration_s: float,
    description: Optional[str] = None, characters: Optional[List[str]] = None,
    host=None, job_queue=None, submit: bool = True,
) -> Dict[str, Any]:
    """构造 keyframes 接续桥（七步事务）：
      1. 校验（时长/节拍/锚点/后继视频/锚点尾帧就绪）
      2. 提取后继镜实际首帧并托管（continuity_service + 注入 host）
      3. insert_shots_after 插入桥梁镜（位次=锚点之后）
      4. update_shot_relay(桥, 锚点) + update_shot_fields(keyframes/过渡文本/时长)
         + opening_source=inherit_previous + video_prompt_en=""（与 REST PUT 同口径）
      5. binding 创建（复制锚点 binding intent）+ refresh（OCC）
      6. compile_reference_pack → basis → seed 尾边界（后继首帧）
      7. 预提交 boundary_frame_stale 守卫 → JobQueue 提交（origin_run_id marker）
    返回完整 receipt；任一步失败 fail closed（已插镜的失败会如实说明当前状态）。"""
    pdir = Path(project_dir)
    validate_bridge_request(duration_s, transition_description)

    store = ProjectStore(pdir)
    project = store.load()
    anchor = project.get_shot(after_shot_id)
    if anchor is None:
        raise BridgeError("shot_not_found", f"锚点镜头不存在: {after_shot_id}")
    scene = next((s for s in project.scenes if s.scene_id == anchor.scene_id), None)
    if scene is None:
        raise BridgeError("internal", f"锚点所在故事段缺失: {anchor.scene_id}")
    ids = [sh.shot_id for sh in scene.shots]
    if after_shot_id not in ids:
        raise BridgeError("internal", f"锚点不在其故事段镜头列表中: {after_shot_id}")
    anchor_idx = ids.index(after_shot_id)

    # ---- 幂等：锚点的紧随后继已是接力锚点的 keyframes 桥 → 不重复构造 ----
    if anchor_idx + 1 < len(scene.shots):
        succ = scene.shots[anchor_idx + 1]
        if succ.link_mode == "keyframes" and succ.source_shot_id == after_shot_id:
            return {
                "ok": True, "operation": "bridge.create",
                "already_exists": True, "bridge_shot_id": succ.shot_id,
                "anchor_shot_id": after_shot_id, "scene_id": scene.scene_id,
                "note": f"锚点 {after_shot_id} 之后已存在 keyframes 桥 {succ.shot_id}，"
                        "未重复构造（幂等）。",
            }

    # ---- 锚点尾帧（首槽边界）就绪 ----
    from src.core.models import ShotStatus
    tail = anchor.video_end_frame
    if not (tail.status == ShotStatus.DONE.value and tail.url):
        raise BridgeError(
            "anchor_tail_not_ready",
            f"锚点 {after_shot_id} 的实际尾帧未就绪（video_end_frame 无公网 URL）"
            "——桥首槽需要它作开场边界；先等锚点视频完成/末帧托管成功")

    # ---- 后继镜（尾槽边界来源）：必须有最终视频 ----
    if anchor_idx + 1 >= len(scene.shots):
        raise BridgeError(
            "bridge_end_boundary_missing",
            f"锚点 {after_shot_id} 之后没有后继镜头——桥必须双端钉死（尾槽=后继镜实际首帧），"
            "没有后继就没有尾边界")
    nxt = scene.shots[anchor_idx + 1]
    nxt_video = continuity_service.resolve_final_video_file(nxt)
    if nxt_video is None:
        raise BridgeError(
            "next_shot_video_missing",
            f"后继镜 {nxt.shot_id} 没有可用的最终视频文件——无法提取其实际首帧作尾边界；"
            "先后继镜生成完成，或改用其它修复策略（如顺延重生成）")
    if host is None:
        raise BridgeError("host_unavailable",
                          "图像托管 host 不可用——尾槽边界帧必须托管为公网 URL 才能进生成接口")
    if submit and job_queue is None:
        raise BridgeError("job_queue_unavailable",
                          "任务队列不可用，无法提交桥生成（服务未就绪）")

    # ---- 步骤 2：提取后继镜实际首帧 + 托管 ----
    boundary = await continuity_service.extract_shot_frame(
        pdir, project, nxt, position="first", tag="bridge_boundary", host=host)
    landing_url = boundary.get("url")
    if not landing_url:
        raise BridgeError("frame_hosting_failed",
                          "后继镜首帧托管未返回公网 URL（生成接口只吃公网 URL）")

    # ---- 步骤 3：插镜 ----
    bridge_description = (description or "").strip() or transition_description.strip()
    new_shots = insert_shots_after(
        project, scene.scene_id, after_shot_id,
        [{"description": bridge_description,
          "characters": list(characters if characters is not None
                             else anchor.characters),
          "target_duration_s": float(duration_s)}])
    bridge = new_shots[0]
    bridge_id = bridge.shot_id
    store.save(project)

    # ---- 步骤 4：relay + keyframes 字段（与 REST PUT /shot 同口径）----
    update_shot_relay(project, bridge_id, after_shot_id)
    update_shot_fields(project, bridge_id, {
        "link_mode": "keyframes",
        "transition_description": transition_description.strip(),
        "target_duration_s": float(duration_s),
    })
    project.get_shot(bridge_id).opening_source = "inherit_previous"
    project.get_shot(bridge_id).video_prompt_en = ""
    store.save(project)

    # ---- 步骤 5：binding 创建（复制锚点 intent）+ refresh ----
    from src.shot_binding.service import ShotBindingService
    svc = ShotBindingService(pdir, project_name)
    binding_info: Dict[str, Any] = {"created": False, "refreshed": False,
                                    "binding_revision": None}
    anchor_binding = svc.read_binding(after_shot_id)
    if anchor_binding and anchor_binding.get("binding"):
        b = anchor_binding["binding"]
        payload = {
            "shot_id": bridge_id,
            "scene_id": anchor.scene_id,
            "scene_binding": b.get("scene_binding"),
            "character_bindings": list(b.get("character_bindings") or []),
            "prop_bindings": list(b.get("prop_bindings") or []),
            "production_notes": f"keyframes bridge after {after_shot_id}",
        }
        existing = svc.read_binding(bridge_id)
        if not (existing and existing.get("binding")):
            created = await svc.create_binding_async(payload)
            if not created.get("ok"):
                raise BridgeError("binding_create_failed",
                                  f"桥 binding 创建失败: {created.get('error')}"
                                  f"—— {created.get('detail', '')[:200]}")
            binding_info["created"] = True
        cur = svc.read_binding(bridge_id) or {}
        rev = str(cur.get("binding_revision") or "")
        refreshed = await svc.refresh_binding_async(
            bridge_id, expected_binding_revision=rev)
        if not refreshed.get("ok"):
            raise BridgeError("binding_refresh_failed",
                              f"桥 binding refresh 失败: {refreshed.get('error')}"
                              f"—— {refreshed.get('detail', '')[:200]}")
        binding_info["refreshed"] = True
        binding_info["binding_revision"] = refreshed.get("binding_revision")

    # ---- 步骤 6：compile basis + seed ----
    if binding_info["binding_revision"]:
        compiled = svc.compile_reference_pack(bridge_id)
        if compiled.get("status") != "COMPILED":
            raise BridgeError(
                "binding_not_ready",
                f"桥 binding 编译未就绪: {compiled.get('status')}"
                f"—— {[i.get('reason') for i in (compiled.get('issues') or [])][:6]}",
                issues=[
                    {"reason": i.get("reason"), "detail": str(i.get("detail") or "")[:160]}
                    for i in (compiled.get("issues") or [])][:6])
        basis = dict((compiled.get("pack") or {}).get("basis") or {})
    else:
        basis = {"binding_readiness": "UNBOUND"}
    seed = seed_bridge_boundary(
        store, bridge_id,
        candidate_local=boundary["local"], candidate_url=landing_url,
        generation_basis=basis,
        prompt_snapshot=f"{SEED_PROMPT_TAG} {nxt.shot_id} first frame "
                        f"({Path(boundary['local']).name})")

    # ---- 步骤 7 前置守卫：seed 后零 refresh（fail closed，绝不重新 seed）----
    ensure_boundary_fresh(pdir, project_name, bridge_id, basis)

    # ---- 步骤 7：提交生成 ----
    receipt: Dict[str, Any] = {
        "ok": True, "operation": "bridge.create",
        "already_exists": False,
        "bridge_shot_id": bridge_id, "anchor_shot_id": after_shot_id,
        "next_shot_id": nxt.shot_id, "scene_id": scene.scene_id,
        "duration_s": float(duration_s),
        "transition_beats": count_transition_beats(transition_description),
        "boundary": {
            "first_slot": {"source_shot_id": after_shot_id,
                           "kind": "video_end_frame", "url": tail.url},
            "last_slot": {"source_shot_id": nxt.shot_id, "kind": "video_first_frame",
                          "local": boundary["local"], "url": landing_url,
                          "source_version": boundary["source"]["version_id"]},
        },
        "binding": binding_info,
        "seed": seed,
        "discipline": {
            "sequence": "refresh → compile basis → seed → submit",
            "seed_after_refresh": True,
            "stale_guard": "boundary_frame_stale（提交前重查 seed basis；绝不重新 seed）",
        },
        "validation_hint": (
            "生成完成后验收：calibration.seam_measure(桥) 与 calibration.seam_measure"
            "(后继镜) 双侧接缝进近零带（1.7-2.4）+ visual.sample_frames(桥) 中间逐段"
            "检查无跳转。"),
    }
    if submit:
        marker = f"bridge_{uuid.uuid4().hex[:8]}"
        job = job_queue.submit(project_name, "video", bridge_id,
                               origin_run_id=marker)
        receipt["job_id"] = job.job_id
        receipt["origin_run_id"] = marker
    return receipt
