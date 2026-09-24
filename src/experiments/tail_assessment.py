# src/experiments/tail_assessment.py
# Phase 37A（37.md §3）：结构化 Vision 尾帧评估——MAE 之外的 narrative 维度证据。
# 定位与 Vision Review Tool 同纪律：
#   - Vision 只提供证据和建议（suggested_deviation），最终偏差裁定仍由人工（§3）；
#   - 单次 Vision 请求（agnes-2.5-flash，恒走主端点），JSON-only + 一次 repair；
#   - 结果是 model judgment，不写 canonical；进 Calibration Trace link 记录。
# 图片输入走本地文件 base64 data URI（视觉后端已证实形态；Pages 公网 URL 未对
# vision 端点验证过，本地优先）。
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log_name = "tail_assessment"

VISION_MODEL = "agnes-2.5-flash"

# 37.md §3 维度（至少包含；suggested_deviation 仅为建议，人工裁定另存）
DIMENSIONS = (
    "identity_consistency", "environment_consistency", "character_pose_state",
    "character_position", "prop_state", "action_endpoint", "camera_state",
    "expected_tail_match", "next_shot_readiness", "narrative_state",
    "unexpected_elements",
)

_SYSTEM = (
    "You are a storyboard continuity assessor. You will see the FINAL FRAME of a "
    "generated animation shot (and optionally the previous shot's final frame). "
    "Compare the actual final frame against the EXPECTED TAIL description and the "
    "NEXT shot's planned opening. Answer ONLY with a JSON object with exactly these "
    "keys:\n"
    + "\n".join(f'- "{k}": short English assessment string' for k in DIMENSIONS) + "\n"
    '- "suggested_deviation": one of "none", "minor", "major" (your SUGGESTION only)\n'
    '- "notes": anything visually unexpected or noteworthy\n'
    "Be literal and evidence-based: describe what IS visible, never assume intent. "
    "Return ONLY the JSON object, no markdown fence, no explanation."
)


def _image_entry(path: Optional[Path], url: Optional[str]) -> Optional[dict]:
    """本地文件 → base64 data URI（首选）；缺本地才退回公网 URL。"""
    if path and Path(path).exists():
        mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
        b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        return {"type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"}}
    if url:
        return {"type": "image_url", "image_url": {"url": url}}
    return None


def _parse_json(raw: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _validate(obj: dict) -> List[str]:
    missing = [k for k in DIMENSIONS if k not in obj]
    if "suggested_deviation" not in obj:
        missing.append("suggested_deviation")
    return missing


async def assess_tail(agnes_client, *, expected_tail: str,
                      next_shot_description: str = "",
                      prev_tail_image: Optional[dict] = None,
                      tail_image: Optional[dict] = None,
                      extra_notes: str = "") -> dict:
    """单次 Vision 评估。image_entry 用 _image_entry 的产物（text+image 混合 content）。
    返回 {assessment: {...}, meta: {model, latency_s, parse_repair_used}}。
    解析失败（含 repair 后）→ CalibrationTrace 侧如实记失败，不伪造。"""
    content: List[dict] = []
    text_bits = [f"EXPECTED TAIL (planned): {expected_tail or '(not specified)'}"]
    if next_shot_description:
        text_bits.append(f"NEXT shot planned opening: {next_shot_description}")
    if extra_notes:
        text_bits.append(f"Context notes: {extra_notes}")
    if prev_tail_image:
        text_bits.append("Image 1 = PREVIOUS shot final frame (identity anchor).")
        content.append(prev_tail_image)
    if tail_image:
        text_bits.append("Image 2 = CURRENT shot final frame (to assess).")
        content.append(tail_image)
    content.insert(0, {"type": "text", "text": "\n".join(text_bits)})
    messages = [{"role": "system", "content": _SYSTEM},
                {"role": "user", "content": content}]
    t0 = time.time()
    res = await agnes_client.chat_completion_vision(
        messages, model=VISION_MODEL, temperature=0.2, max_tokens=1600)
    raw = res.get("content") if isinstance(res, dict) else str(res)
    obj = _parse_json(raw or "")
    repaired = False
    if obj is None:
        # 一次 repair（同 Vision Review Tool §七 Option A）
        messages.append({"role": "assistant", "content": raw or ""})
        messages.append({"role": "user", "content":
                         "Your previous reply was not valid JSON. "
                         "Return ONLY the JSON object now."})
        res2 = await agnes_client.chat_completion_vision(
            messages, model=VISION_MODEL, temperature=0.0, max_tokens=1600)
        raw2 = res2.get("content") if isinstance(res2, dict) else str(res2)
        obj = _parse_json(raw2 or "")
        repaired = True
    meta = {"model": VISION_MODEL, "latency_s": round(time.time() - t0, 2),
            "parse_repair_used": repaired}
    if obj is None:
        raise ValueError(f"vision assessment 不可解析: {str(raw)[:200]}")
    missing = _validate(obj)
    if missing:
        obj["_missing_fields"] = missing   # 如实标注缺失维度，不伪造
    return {"assessment": obj, "meta": meta}
