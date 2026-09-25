# src/agents/director_frames.py
# Phase 39（39.md §6.3/§6.5）：Director 的帧级只读工具面——
#   video.extract_frame   从镜头最终视频正式提取一帧并托管（边界帧物料的正式来源）
#   visual.sample_frames  五位采样 + 相邻帧对视觉检查（桥验收：不能只看两个端点）
#
# 定位：
#   - 机械行为（ffmpeg 提取、落盘、托管）全部在
#     src/storyboard/continuity_service.py；Agent 只表达意图。GITHUB_TOKEN 等托管
#     凭证由注入的 host（deps.get_rehost_host）持有，绝不进 Agent 上下文（39.md §6.3）。
#   - 视觉对检查走 agnes vision（与 visual.review_image 同一调用模式）：结论是证据
#     不是裁定，Director 必须自己综合判断并写入 trace。
#   - 不写 canonical：提取/采样产物落 output/frames/（媒体缓存）；回执不设
#     verified=True（读不是写执行）。
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.store import ProjectStore
from src.storyboard import continuity_service

from .runtime import AgentError, AgentRuntime
from .script_writer import _base_receipt, _parse_args
from .director_vision_review import (
    AgnesError, VISION_MODEL, _image_entry, _strip_fence,
)


# ============ video.extract_frame ============
VIDEO_EXTRACT_FRAME_TOOL = {
    "type": "function",
    "function": {
        "name": "video.extract_frame",
        "description": (
            "正式提取镜头最终视频的实际帧并托管为公网 URL（只读测量，不改镜头 canonical）。"
            "position: first（第 0 帧——后继镜实际首帧，作桥尾槽边界）| end（尾帧）| "
            "秒数。返回 local/url 与帧 provenance（来源镜头/版本/位置）。"
            "托管凭证由系统持有；托管失败 fail closed。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shot_id": {"type": "string", "description": "镜头 id。"},
                "position": {"type": "string",
                             "description": "first（缺省）| end | 秒数字符串（如 2.5）。"},
                "purpose": {"type": "string",
                            "description": "用途标记（bridge_boundary / inspection），"
                                           "进回执 provenance，便于追溯。"},
            },
            "required": ["shot_id"],
        },
    },
}


def _parse_position(raw) -> "str | float":
    p = (str(raw or "first").strip() or "first").lower()
    if p in ("first", "end"):
        return p
    try:
        return float(p)
    except ValueError:
        raise AgentError("bad_arguments",
                         f"position 只支持 first | end | 秒数，得到 {raw!r}")


async def _execute_extract_frame(rt: AgentRuntime, project: str, arguments: str,
                                 instruction, message_id) -> dict:
    args = _parse_args(arguments)
    shot_id = (args.get("shot_id") or "").strip()
    if not shot_id:
        raise AgentError("bad_arguments", "缺少 shot_id")
    position = _parse_position(args.get("position"))
    purpose = (str(args.get("purpose") or "inspection") or "inspection").strip()

    pdir = rt._resolve_project_dir(project)
    proj = ProjectStore(pdir).load()
    shot = proj.get_shot(shot_id)
    if shot is None:
        raise AgentError("not_found", f"镜头不存在: {shot_id}")
    try:
        from deps import get_rehost_host
        host = get_rehost_host()
    except Exception as e:   # noqa: BLE001
        raise AgentError("host_unavailable", f"图像托管 host 不可用: {e}")
    try:
        entry = await continuity_service.extract_shot_frame(
            pdir, proj, shot, position=position, tag="extract", host=host)
    except continuity_service.ContinuityError as e:
        raise AgentError(e.code, e.message)
    return _base_receipt(
        "video.extract_frame", ok=True, target=shot_id,
        asset={"local": entry["local"], "url": entry.get("url")},
        provenance={**entry["source"], "purpose": purpose},
        note=(f"已从镜头 {shot_id} 视频（版本 {entry['source']['version_id']}）"
              f"提取 position={entry['source']['position']} 帧并托管。"
              "作为桥尾槽边界使用时，把它交给 shot.bridge_create（服务内部自动提取"
              "并 seed，无需手工传递）。"))


# ============ visual.sample_frames ============
VISUAL_SAMPLE_FRAMES_TOOL = {
    "type": "function",
    "function": {
        "name": "visual.sample_frames",
        "description": (
            "桥/镜头视频的中间帧检查（只读）：按 beginning/early-middle/middle/"
            "late-middle/ending 五位采样最终视频，并对相邻帧对做视觉连续性检查"
            "（无跳转/中间形态跳变/identity jump/unexpected element 证据）。"
            "🔴 只看两个端点 MAE 不能宣布桥成功——本工具是逐段验收的证据来源；"
            "结论是视觉模型判断，你必须综合双侧接缝与帧对证据自己裁定并记 trace。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shot_id": {"type": "string", "description": "镜头 id（通常为桥梁镜）。"},
            },
            "required": ["shot_id"],
        },
    },
}

_PAIR_SYSTEM_PROMPT = (
    "你是视频过渡连续性检查器。给你同一视频里时间相邻的两帧（图1=较早时刻，"
    "图2=较晚时刻）。判断两帧之间的过渡是否存在不连续："
    "突然 hard cut（画面内容瞬间切换）、主体形态跳变（姿态/位置瞬移）、"
    "人物 identity 变化、unexpected element（多出人/动物/物体）。"
    "只输出 JSON：{\"discontinuity\": \"none\"|\"suspected\"|\"severe\", "
    "\"note\": \"<一句话证据>\"}。discontinuity 判据：none=自然过渡的两个时刻；"
    "suspected=可能存在跳变但证据不充分；severe=明显 hard cut/跳变/identity 跳。"
    "不确定时如实 suspected，不要编造。")


async def _pair_check(client, frames: List[dict]) -> List[dict]:
    """相邻帧对视觉检查（一次 repair 的 strict JSON，与 review 工具同模式）。"""
    evidence: List[dict] = []
    for i in range(len(frames) - 1):
        a, b = frames[i], frames[i + 1]
        entries = []
        for f in (a, b):
            e = _image_entry(Path(f["local"]), None)
            if e is None:
                raise AgentError("frame_unreadable",
                                 f"采样帧不可读: {f['local']}")
            entries.append(e)
        user = (f"视频片段（{a['label']} → {b['label']}）的相邻两帧。"
                "只输出契约 JSON。")
        msgs = [{"role": "system", "content": _PAIR_SYSTEM_PROMPT},
                {"role": "user",
                 "content": [{"type": "text", "text": user}] + entries}]
        try:
            res = await client.chat_completion_vision(
                msgs, model=VISION_MODEL, temperature=0.2, max_tokens=300)
        except AgnesError as e:
            raise AgentError("provider_error",
                             f"帧对视觉检查 provider 失败：{e}") from e
        obj = None
        try:
            obj = json.loads(_strip_fence(res.get("content") or ""))
        except Exception:
            obj = None
        if not isinstance(obj, dict) or obj.get("discontinuity") not in (
                "none", "suspected", "severe"):
            # 一次 repair；仍不合法 → 如实标 unresolved（不编造结论）
            try:
                res2 = await client.chat_completion_vision(
                    msgs + [{"role": "assistant",
                             "content": res.get("content") or ""},
                            {"role": "user",
                             "content": "你的上次输出不是契约 JSON。只重新输出一个"
                                        "符合契约的 JSON 对象，不要任何其他文字。"}],
                    model=VISION_MODEL, temperature=0.2, max_tokens=300)
                obj = json.loads(_strip_fence(res2.get("content") or ""))
            except Exception:   # noqa: BLE001
                obj = {"discontinuity": "unresolved",
                       "note": "帧对视觉检查两次输出均不符合 JSON 契约——该对无结论，"
                               "需人工目检"}
        evidence.append({
            "from": a["label"], "to": b["label"],
            "from_local": a["local"], "to_local": b["local"],
            "discontinuity": obj.get("discontinuity"),
            "note": (obj.get("note") or "")[:300],
        })
    return evidence


async def _execute_sample_frames(rt: AgentRuntime, project: str, arguments: str,
                                 instruction, message_id) -> dict:
    args = _parse_args(arguments)
    shot_id = (args.get("shot_id") or "").strip()
    if not shot_id:
        raise AgentError("bad_arguments", "缺少 shot_id")
    pdir = rt._resolve_project_dir(project)
    proj = ProjectStore(pdir).load()
    if proj.get_shot(shot_id) is None:
        raise AgentError("not_found", f"镜头不存在: {shot_id}")
    try:
        frames = continuity_service.sample_frames(pdir, proj, shot_id)
    except continuity_service.ContinuityError as e:
        raise AgentError(e.code, e.message)

    client = rt._resolve_client_or_none(agent_id="director")
    if client is None:
        raise AgentError("not_configured", "Agnes 客户端未就绪，无法执行帧对检查")
    if not hasattr(client, "chat_completion_vision"):
        raise AgentError("not_configured", "当前文本客户端不支持视觉请求")

    evidence = await _pair_check(client, frames)
    severe = [e for e in evidence if e.get("discontinuity") == "severe"]
    suspected = [e for e in evidence if e.get("discontinuity") == "suspected"]
    unresolved = [e for e in evidence if e.get("discontinuity") == "unresolved"]
    shot = proj.get_shot(shot_id)
    # Phase 39 hardening B（用户定版）：keyframes 桥的帧检证据必须落 trace——
    # 39B replay 实证 M5B_a1 误判根因即「桥内跳变无数字化证据通道」。
    bridge_trace_reminder = None
    if shot is not None and shot.link_mode == "keyframes":
        bridge_trace_reminder = (
            "该镜为 keyframes 桥——按校准纪律，把本次帧检证据（frames 清单 + "
            "pairwise_evidence 结论摘要）作为 bridge_frame_check 字段写入该桥的 "
            "calibration.trace_upsert record；只有两端 MAE、无帧检记录不得宣布桥成立。")
    return _base_receipt(
        "visual.sample_frames", ok=True, target=shot_id,
        frames=[{"label": f["label"], "at_seconds": f["at_seconds"],
                 "local": f["local"]} for f in frames],
        pairwise_evidence=evidence,
        discontinuity_summary={"severe": len(severe), "suspected": len(suspected),
                               "unresolved": len(unresolved)},
        **({"trace_reminder": bridge_trace_reminder} if bridge_trace_reminder else {}),
        note=("五位采样 + 帧对检查完成：severe={0} suspected={1} unresolved={2}"
              "（帧文件路径已列出，可交用户目检）。这是证据不是裁定——结合双侧接缝 "
              "MAE 与过渡叙事自己判断桥是否成立。").format(
                  len(severe), len(suspected), len(unresolved)))


TOOL_EXECUTORS = {
    "video.extract_frame": _execute_extract_frame,
    "visual.sample_frames": _execute_sample_frames,
}
