# src/resources/look_review.py
# Production Resources V1 — Phase 1C2：CharacterLook 候选视觉审查（独立 review evidence）。
#
# 🔴 职责：对 Look 候选图做真实的维度检查（人物身份一致 / 服装一致 / 明显视觉缺陷），
#    结构化 verdict 持久化到 output/resources/review_records.jsonl（append-only，
#    与 generation_records.jsonl 对称的独立证据流）。**不写 resources.json canonical**——
#    canonical 只被 Approval 这种显式 mutation 改动。
# 🔴 review pass ≠ approved（双向）：审查通过不产生任何 approved 状态；
#    approve 端点（write_service）反过来要求该 asset 存在 overall=pass 的审查证据。
# 🔴 并行路径（Phase 1A §10.3 决策）：直接调 AgnesClient.chat_completion_vision
#    （data URI 已实证 5/5；imgbb URL 恒败勿用），不 import 任何 Agent 模块，
#    不触碰 Director/visual.review_image。
# 🔴 overall 由三个维度**确定性推导**（任何 fail→needs_fix；全 pass→pass；否则 uncertain），
#    不信任模型自报的 overall 字段（只留档 model_overall）——approval 门槛可审计。
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .generation import (canonical_json, fingerprint, load_image_bytes,
                         resolve_local_file, to_agnes_image_ref)
from .generation_records import review_records_path
from .look_prompt import _clean
from .models import CharacterLook, Clothing

VISION_MODEL = "agnes-2.5-flash"   # 与 Director 视觉审查同源（本域自含常量，不 import agents）

_DIMENSIONS = ("identity_consistency", "costume_consistency", "visual_defects")


def _expectations_fingerprint(*, character_description: str,
                              clothing_snapshot: Optional[dict],
                              appearance_description: str, appearance_delta: str,
                              view_purpose: str) -> str:
    """审查基准指纹：本次审查所对照的期望（角色/服装/外观/视角）。
    与生成 basis 指纹口径独立——审查的是'当前期望'，记录指纹即可追溯。"""
    return fingerprint({
        "character_description": character_description,
        "clothing": clothing_snapshot,
        "appearance_description": appearance_description,
        "appearance_delta": appearance_delta,
        "view_purpose": view_purpose,
    })


def build_review_prompt(*, character_description: str,
                        clothing_snapshot: Optional[dict],
                        appearance_description: str = "",
                        appearance_delta: str = "",
                        view_purpose: str = "") -> str:
    """确定性审查 prompt（无时间/随机）。要求只回 JSON。"""
    cl = clothing_snapshot or {}
    outfit_bits = ", ".join(x for x in (
        _clean(cl.get("name") or ""), _clean(cl.get("description") or ""),
        _clean(cl.get("design_traits") or "")) if x)
    if cl.get("materials"):
        outfit_bits += f", materials: {_clean(', '.join(map(str, cl['materials'])))}"
    if cl.get("colors"):
        outfit_bits += f", colors: {_clean(', '.join(map(str, cl['colors'])))}"
    expected_outfit = outfit_bits or "no specific outfit requirement"
    view = _clean(view_purpose).lower() or "front-facing full body reference"

    return (
        "You are a strict production reference reviewer for an animation pipeline.\n"
        "Review THIS candidate image against the EXPECTED baseline below.\n\n"
        f"Expected character identity: {_clean(character_description) or '(unspecified)'}\n"
        f"Expected outfit: {expected_outfit}\n"
        f"Expected appearance notes: {_clean(appearance_description) or 'none'}"
        f"{('; ' + _clean(appearance_delta)) if _clean(appearance_delta) else ''}\n"
        f"Generation view intent: {view}\n\n"
        "Check exactly these three dimensions:\n"
        "1. identity_consistency — the character matches the expected identity "
        "(face, hairstyle, body proportions); suitable as a reusable production reference.\n"
        "2. costume_consistency — the outfit matches the expected clothing description "
        "(design, colors, materials, completeness).\n"
        "3. visual_defects — obvious visual defects: malformed limbs/hands/face, artifacts, "
        "watermark or text, accidental occlusion of key design information, wrong framing "
        "(must read as a full-body production reference).\n\n"
        "Answer with ONLY a JSON object (no markdown fence) in exactly this schema:\n"
        '{"identity_consistency": "pass|concern|fail", "identity_notes": "...",\n'
        ' "costume_consistency": "pass|concern|fail", "costume_notes": "...",\n'
        ' "visual_defects": "pass|concern|fail", "defect_notes": "...",\n'
        ' "overall": "pass|needs_fix|uncertain", "summary": "..."}'
    )


def _extract_json(text: str) -> Optional[dict]:
    """宽容 JSON 提取：剥 code fence → 找首个平衡 {...} → json.loads。失败返回 None。"""
    if not isinstance(text, str):
        return None
    t = re.sub(r"```(?:json)?", "", text).strip().strip("`")
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(t[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except Exception:
                    return None
    return None


def _normalize_dim(v: Any) -> Optional[str]:
    s = str(v or "").strip().lower()
    return s if s in ("pass", "concern", "fail") else None


def derive_overall(dims: Dict[str, Optional[str]]) -> str:
    """overall 确定性推导：任何 fail→needs_fix；全 pass→pass；其余（concern/缺失）→uncertain。"""
    vals = [dims.get(d) for d in _DIMENSIONS]
    if any(v == "fail" for v in vals):
        return "needs_fix"
    if all(v == "pass" for v in vals):
        return "pass"
    return "uncertain"


# ============ 审查证据读取 ============
def read_review_records(project_dir, *, look_id: Optional[str] = None,
                        limit: Optional[int] = None) -> List[dict]:
    from .generation_records import read_records
    return read_records(project_dir, look_id=look_id, limit=limit,
                        path=review_records_path(project_dir))


def latest_review_for_asset(project_dir, look_id: str, asset_id: str) -> Optional[dict]:
    """该 asset 在该 look 下最近一次"实际审到它"的 per-candidate 结果；无则 None。"""
    for rec in reversed(read_review_records(project_dir, look_id=look_id)):
        for cr in rec.get("candidate_reviews") or []:
            if cr.get("asset_id") == asset_id and rec.get("status") == "completed":
                return cr
    return None


def has_pass_evidence(project_dir, look_id: str, asset_id: str) -> bool:
    """approve 门槛：该 asset 在该 look 下存在 overall=pass 的审查证据（最近一次）。"""
    cr = latest_review_for_asset(project_dir, look_id, asset_id)
    return bool(cr) and cr.get("overall") == "pass"


# ============ 审查执行 ============
async def review_look_candidates(project_dir, look_id: str, *,
                                 asset_ids: Optional[List[str]] = None,
                                 view_purpose: str = "",
                                 agnes_client=None) -> dict:
    """对 Look 的候选图执行视觉审查（每候选一次 vision 调用，逐个隔离）。
    自包含：内部读 canonical（只读，不写 resources.json）；证据 append 到
    review_records.jsonl。期望失败返回结构化 receipt。"""
    from .store import ResourceStore
    from src.core.store import ProjectStore

    pdir = Path(project_dir)
    review_id = f"rev_{uuid.uuid4().hex[:12]}"
    started_at = time.time()

    doc = ResourceStore(pdir).load()
    try:
        project = ProjectStore(pdir).load()
    except FileNotFoundError:
        project = None
    look: Optional[CharacterLook] = doc.looks.get(look_id)
    if look is None:
        return {"ok": False, "operation": "look.review_candidates", "review_id": review_id,
                "look_id": look_id, "error": "not_found", "detail": f"look 不存在: {look_id}"}
    if not look.reference_asset_ids:
        return {"ok": False, "operation": "look.review_candidates", "review_id": review_id,
                "look_id": look_id, "error": "no_candidates",
                "detail": "该 Look 还没有任何候选图（先 generate）"}
    targets = list(asset_ids) if asset_ids else list(look.reference_asset_ids)
    bad = [a for a in targets if a not in look.reference_asset_ids]
    if bad:
        return {"ok": False, "operation": "look.review_candidates", "review_id": review_id,
                "look_id": look_id, "error": "not_in_candidates",
                "detail": f"asset 不在该 Look 候选集中: {bad}"}

    if agnes_client is None:
        from deps import get_agnes_client   # 惰性（与 generation 同惯例）
        agnes_client = get_agnes_client()

    # 期望基准（当前 canonical 状态）
    character = project.get_character(look.character_name) if project is not None else None
    character_description = (character.description_en or "") if character is not None else ""
    clothing: Optional[Clothing] = doc.clothings.get(look.clothing_id) if look.clothing_id else None
    clothing_snapshot = ({k: list(v) if isinstance(v, list) else v for k, v in
                          {"name": clothing.name, "description": clothing.description,
                           "design_traits": clothing.design_traits,
                           "materials": clothing.materials,
                           "colors": clothing.colors}.items()} if clothing is not None else None)
    basis_fp = _expectations_fingerprint(
        character_description=character_description, clothing_snapshot=clothing_snapshot,
        appearance_description=look.appearance_description,
        appearance_delta=look.appearance_delta, view_purpose=view_purpose)
    prompt = build_review_prompt(character_description=character_description,
                                 clothing_snapshot=clothing_snapshot,
                                 appearance_description=look.appearance_description,
                                 appearance_delta=look.appearance_delta,
                                 view_purpose=view_purpose)

    candidate_reviews: List[dict] = []
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}

    async def review_one(asset_id: str) -> dict:
        asset = doc.reference_assets.get(asset_id)
        if asset is None:
            return {"asset_id": asset_id, "status": "image_unreadable", "overall": "uncertain",
                    "summary": "ReferenceAsset 不存在（canonical 缺失）"}
        image_ref = to_agnes_image_ref(pdir, url=asset.url, local=asset.local)
        if image_ref is None:
            return {"asset_id": asset_id, "status": "image_unreadable", "overall": "uncertain",
                    "summary": "候选图不可读（local 文件缺失且无可用 url）"}
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_ref}},
        ]}]
        try:
            res = await agnes_client.chat_completion_vision(msgs, model=VISION_MODEL)
        except Exception as e:   # 单候选 provider 错误隔离
            return {"asset_id": asset_id, "status": "provider_error", "overall": "uncertain",
                    "summary": f"{type(e).__name__}: {e}"}
        u = (res or {}).get("usage") or {}
        usage_total["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
        usage_total["completion_tokens"] += int(u.get("completion_tokens") or 0)
        parsed = _extract_json((res or {}).get("content"))
        if parsed is None:
            return {"asset_id": asset_id, "status": "parse_failed", "overall": "uncertain",
                    "summary": "vision 回复无法解析为 JSON（fail closed，不可作为 pass 证据）",
                    "raw_head": ((res or {}).get("content") or "")[:200]}
        dims = {d: _normalize_dim(parsed.get(d)) for d in _DIMENSIONS}
        return {"asset_id": asset_id, "status": "reviewed",
                "identity_consistency": dims["identity_consistency"],
                "identity_notes": str(parsed.get("identity_notes") or ""),
                "costume_consistency": dims["costume_consistency"],
                "costume_notes": str(parsed.get("costume_notes") or ""),
                "visual_defects": dims["visual_defects"],
                "defect_notes": str(parsed.get("defect_notes") or ""),
                "overall": derive_overall(dims),
                "model_overall": str(parsed.get("overall") or ""),
                "summary": str(parsed.get("summary") or "")}

    for aid in targets:
        candidate_reviews.append(await review_one(aid))

    record = {
        "review_id": review_id, "operation": "look.review_candidates", "look_id": look_id,
        "reviewed_basis_fingerprint": basis_fp, "model": VISION_MODEL, "prompt": prompt,
        "candidate_asset_ids": list(targets),
        "candidate_reviews": candidate_reviews,
        "usage": usage_total,
        "started_at": started_at, "completed_at": time.time(),
        "status": "completed", "error": None, "verified": True,
    }
    from .generation_records import append_record
    append_record(pdir, record, path=review_records_path(pdir))
    return {"ok": True, "operation": "look.review_candidates", "review_id": review_id,
            "look_id": look_id, "status": "completed",
            "reviewed_basis_fingerprint": basis_fp, "model": VISION_MODEL,
            "candidate_reviews": candidate_reviews, "generation_record": record}
