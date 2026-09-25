# src/agents/director_bridge.py
# Phase 39（39.md §6.4）：shot.bridge_create——keyframes 接续桥的 Director 工具。
#
# 定位：Agent 只表达意图（锚点/过渡文本/时长/描述）；构造七步（插镜→relay→keyframes
# 字段→binding→refresh→compile basis→seed→提交）的确定性时序全部在
# src/storyboard/bridge_service.py（内部复用正式服务，绝不旁路；seed 后零 refresh，
# 违反 fail closed：boundary_frame_stale）。本文件只做：参数校验 → 调服务 → 等待
# 生成终态（长任务，进度/取消）→ 写后重读 canonical 验证（origin marker 精确匹配）
# → 回执。托管 host（GITHUB_TOKEN 持有者）由 deps 注入，绝不进 Agent 上下文。
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Optional

from src.core.store import ProjectStore
from src.storyboard import bridge_service

from .runtime import AgentError, AgentRuntime
from .script_writer import _base_receipt, _parse_args

# 等待语义对齐 director_visual：视频串行 worker 1 RPM，总超时不取消 job（留队列
# 自行落盘）；桥单 job，超时给到与单视频一致的水平。
BRIDGE_TOTAL_TIMEOUT_S = 2400
END_FRAME_WAIT_S = 180
POLL_INTERVAL_S = 2.0


def _progress(event_cb, message: str, *, pct: Optional[int] = None) -> None:
    if event_cb is None:
        return
    ev = {"type": "tool.progress", "message": message}
    if pct is not None:
        ev["pct"] = pct
    event_cb(ev)


SHOT_BRIDGE_CREATE_TOOL = {
    "type": "function",
    "function": {
        "name": "shot.bridge_create",
        "description": (
            "在 after_shot_id 之后插入一座 keyframes 接续桥并提交生成（长任务）。"
            "桥=双端钉死的接缝修复标准手段：首槽=锚点实际尾帧（inherit_previous），"
            "尾槽=后继镜实际首帧（服务自动提取+托管+seed）。约束：duration_s 固定 "
            "10s 起步（<10 被拒绝）；transition_description 必须包含明确中间节拍"
            "（≥3 段，如：低头→嗅沿瓜皮→绕半步→鼻贴瓜皮）。构造时序（refresh→"
            "compile basis→seed→提交）由后端服务强制；seed 后任何 binding refresh "
            "都会 fail closed（boundary_frame_stale），绝不重新 seed。前置：锚点尾帧"
            "就绪、后继镜已有完成视频。完成后用 calibration.seam_measure（双侧）+ "
            "visual.sample_frames（中间逐段）验收；桥不重生成邻镜。写工具（长任务，"
            "占一个写槽）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "after_shot_id": {"type": "string",
                                  "description": "锚点镜头 id——桥插在它之后、原后继镜之前。"},
                "transition_description": {
                    "type": "string",
                    "description": "过渡描述：必须写明中间节拍（A→B→C→…→endpoint），"
                                   "给 provider 插值铺路径；两点一线会被拒绝。"},
                "duration_s": {"type": "number",
                               "description": "桥时长（秒），固定 10s 起步，不按静态配方缩短。"},
                "description": {"type": "string",
                                "description": "桥镜的画面描述（省略则用过渡描述）。"},
                "characters": {"type": "array", "items": {"type": "string"},
                               "description": "桥镜出场角色（省略则继承锚点）。"},
                "wait": {"type": "boolean",
                         "description": "是否等待生成完成（缺省 true；false=只构造+提交，"
                                        "返回 job_id 后用 shot.list 跟踪）。"},
            },
            "required": ["after_shot_id", "transition_description", "duration_s"],
        },
    },
}


async def _wait_job(job, q, *, event_cb, cancel_event, timeout_s: float) -> str:
    """轮询单 job 到终态。返回终态语义：done/failed/cancelled/timeout/cancel_requested。"""
    deadline = time.time() + timeout_s
    while True:
        if cancel_event is not None and cancel_event.is_set():
            if job.state in ("queued", "running"):
                try:
                    q.cancel(job.job_id, force=True)
                except Exception:   # noqa: BLE001
                    pass
            return "cancel_requested"
        if job.state in ("done", "failed", "cancelled"):
            return job.state
        if time.time() > deadline:
            return "timeout"
        _progress(event_cb, f"桥视频生成中（job {job.job_id}，串行 1 RPM）…",
                  pct=None)
        await asyncio.sleep(POLL_INTERVAL_S)


async def _wait_end_frame(pdir: Path, project: str, shot_id: str,
                          timeout_s: float) -> bool:
    """有界等待生成管线自动提取的末帧就绪（桥尾→后继首接缝的输入）。
    超时不失败（handler 会继续完成），只是提醒稍后重测。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        shot = ProjectStore(pdir).load().get_shot(shot_id)
        if shot is not None and shot.video_end_frame.url_ready:
            return True
        await asyncio.sleep(5.0)
    return False


async def _execute_bridge_create(rt: AgentRuntime, project: str, arguments: str,
                                 instruction, message_id,
                                 event_cb=None, cancel_event=None) -> dict:
    args = _parse_args(arguments)
    after_shot_id = (args.get("after_shot_id") or "").strip()
    transition = str(args.get("transition_description") or "").strip()
    duration_s = args.get("duration_s")
    wait = args.get("wait") is not False
    if not after_shot_id:
        raise AgentError("bad_arguments", "缺少 after_shot_id")
    if not transition:
        raise AgentError("bad_arguments",
                         "缺少 transition_description（桥必须给中间节拍铺路径）")

    # 提交前的确定性校验前移（时长/节拍）——不达标不烧任何机械步骤
    bridge_service.validate_bridge_request(duration_s, transition)

    q = rt._resolve_job_queue_or_none()
    if q is None:
        raise AgentError("job_queue_unavailable",
                         "任务队列不可用（服务未就绪），无法提交桥生成")
    try:
        from deps import get_rehost_host
        host = get_rehost_host()
    except Exception as e:   # noqa: BLE001
        raise AgentError("host_unavailable", f"图像托管 host 不可用: {e}")

    pdir = rt._resolve_project_dir(project)
    _progress(event_cb, "构造 keyframes 桥（提取边界帧→插镜→binding→seed）…", pct=5)
    try:
        receipt = await bridge_service.build_keyframes_bridge(
            pdir, project,
            after_shot_id=after_shot_id,
            transition_description=transition,
            duration_s=duration_s,
            description=(str(args.get("description") or "").strip() or None),
            characters=(args.get("characters") if isinstance(
                args.get("characters"), list) else None),
            host=host, job_queue=q, submit=True)
    except bridge_service.BridgeError as e:
        raise AgentError(e.code, e.message)
    except Exception as e:   # StructureError 等确定性失败原样转结构化错误
        code = getattr(e, "code", "") or "bridge_build_failed"
        raise AgentError(str(code), str(e))

    if receipt.get("already_exists"):
        return _base_receipt(
            "shot.bridge_create", ok=True, target=receipt["bridge_shot_id"],
            shot_id=receipt["bridge_shot_id"], verified=True, unchanged=True,
            **{"already_exists": True},
            note=receipt.get("note") or "该位置已存在 keyframes 桥（幂等，未重复构造）。")

    bridge_id = receipt["bridge_shot_id"]
    marker = receipt.get("origin_run_id") or ""
    job_id = receipt.get("job_id")
    verified = False
    end_frame_ready = False
    timed_out = False
    if wait and job_id:
        live = q.get(job_id)
        if live is None:
            # JobQueue 接口差异兜底：直接用 submit 返回的 job 对象不可得时，
            # 不等待，如实返回 submitted 状态。
            wait = False
        else:
            _progress(event_cb, f"桥 {bridge_id} 已提交（job {job_id}），等待生成…",
                      pct=10)
            state = await _wait_job(live, q, event_cb=event_cb,
                                    cancel_event=cancel_event,
                                    timeout_s=BRIDGE_TOTAL_TIMEOUT_S)
            if state == "done":
                on_disk = ProjectStore(pdir).load()
                bshot = on_disk.get_shot(bridge_id)
                vv = next((v for v in (bshot.video_versions or [])
                           if v.origin_run_id == marker and v.local
                           and Path(v.local).exists()), None) if bshot else None
                verified = vv is not None
                _progress(event_cb, "桥生成完成，等待末帧自动提取…", pct=95)
                end_frame_ready = await _wait_end_frame(
                    pdir, project, bridge_id, END_FRAME_WAIT_S)
            elif state == "timeout":
                timed_out = True
            elif state in ("cancel_requested", "cancelled"):
                return _base_receipt(
                    "shot.bridge_create", ok=False, target=bridge_id,
                    shot_id=bridge_id, verified=False,
                    job_id=job_id, status=state,
                    error="已取消（用户停止）——桥结构与 binding 已落盘，生成任务已取消",
                    **receipt)
            else:   # failed
                return _base_receipt(
                    "shot.bridge_create", ok=False, target=bridge_id,
                    shot_id=bridge_id, verified=False,
                    job_id=job_id, status=state,
                    error=(getattr(live, "error", None) or "桥生成失败")[:300],
                    **receipt)

    rt.record_work(project, "change",
                   f"Director 构造 keyframes 接续桥 {bridge_id}"
                   f"（{after_shot_id} → {receipt.get('next_shot_id')}）",
                   participants=["director"], affected_domains=["storyboard"],
                   references={"shot_id": bridge_id}, status="completed")
    return _base_receipt(
        "shot.bridge_create", ok=True, target=bridge_id, shot_id=bridge_id,
        verified=verified, unchanged=False,
        job_id=job_id, timed_out=timed_out,
        bridge=receipt,
        **({"end_frame_ready": end_frame_ready} if wait else {}),
        note=("桥已构造并提交（插镜/relay/binding/seed 凭据见 bridge 字段）。"
              + ("生成已完成且资产落盘已确认；" if verified else
                 ("生成仍在后台继续（超时不取消），完成后自行落盘；" if timed_out
                  else "未等待生成完成，可用 shot.list 跟踪；"))
              + "验收：双侧 seam（calibration.seam_measure）进近零带 + "
                "visual.sample_frames 中间逐段无跳转；桥不重生成邻镜。"))


TOOL_EXECUTORS = {
    "shot.bridge_create": _execute_bridge_create,
}
