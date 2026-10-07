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
# P1 §15：项目风格一致性维度（profile specified 时追加；hard——fail → overall 不得 pass）
STYLE_DIMENSION = "project_style_consistency"


def _expectations_fingerprint(*, character_description: str,
                              clothing_snapshot: Optional[dict],
                              appearance_description: str, appearance_delta: str,
                              view_purpose: str,
                              visual_profile_fingerprint: Optional[str] = None) -> str:
    """审查基准指纹：本次审查所对照的期望（角色/服装/外观/视角 + 项目风格，P1 §15）。
    与生成 basis 指纹口径独立——审查的是'当前期望'，记录指纹即可追溯。"""
    return fingerprint({
        "character_description": character_description,
        "clothing": clothing_snapshot,
        "appearance_description": appearance_description,
        "appearance_delta": appearance_delta,
        "view_purpose": view_purpose,
        "visual_profile_fingerprint": visual_profile_fingerprint,
    })


def build_review_prompt(*, character_description: str,
                        clothing_snapshot: Optional[dict],
                        appearance_description: str = "",
                        appearance_delta: str = "",
                        view_purpose: str = "",
                        style_expectation: str = "") -> str:
    """确定性审查 prompt（无时间/随机）。要求只回 JSON。
    style_expectation（P1 §15）：visual_profile.compiler 输出的项目风格期望段；
    非空时 project_style_consistency 成为第 4 个维度（hard）。"""
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

    n_dims = 4 if style_expectation else 3
    parts = [
        "You are a strict production reference reviewer for an animation pipeline.\n"
        "Review THIS candidate image against the EXPECTED baseline below.\n\n"
        f"Expected character identity: {_clean(character_description) or '(unspecified)'}\n"
        f"Expected outfit: {expected_outfit}\n"
        f"Expected appearance notes: {_clean(appearance_description) or 'none'}"
        f"{('; ' + _clean(appearance_delta)) if _clean(appearance_delta) else ''}\n"
        f"Generation view intent: {view}\n\n"
        f"Check exactly these {n_dims} dimensions:\n"
        "1. identity_consistency — the character matches the expected identity "
        "(face, hairstyle, body proportions); suitable as a reusable production reference.\n"
        "2. costume_consistency — the outfit matches the expected clothing description "
        "(design, colors, materials, completeness).\n"
        "3. visual_defects — obvious visual defects: malformed limbs/hands/face, artifacts, "
        "watermark or text, accidental occlusion of key design information, wrong framing "
        "(must read as a full-body production reference).\n",
    ]
    if style_expectation:
        parts.append(
            f"4. project_style_consistency — the image must belong to the project's declared "
            f"visual production style/medium (hard dimension: a style mismatch must fail).\n"
            f"{style_expectation}\n")
    schema = ('{"identity_consistency": "pass|concern|fail", "identity_notes": "...",\n'
              ' "costume_consistency": "pass|concern|fail", "costume_notes": "...",\n'
              ' "visual_defects": "pass|concern|fail", "defect_notes": "...",\n')
    if style_expectation:
        schema += (' "project_style_consistency": "pass|concern|fail", "style_notes": "...",\n')
    schema += ' "overall": "pass|needs_fix|uncertain", "summary": "..."}'
    parts.append("Answer with ONLY a JSON object (no markdown fence) in exactly this schema:\n"
                 + schema)
    return "\n".join(parts)


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


async def vision_json_call(agnes_client, msgs: list, *, model: str,
                           max_attempts: int = 2, max_tokens: int = 1600) -> dict:
    """Vision 调用 + 宽容 JSON 解析稳定性 wrapper（P1 修复，2026-09-18）。
    首次回复无法解析为 JSON 时，追加『只输出 JSON』硬提醒重试一次；
    两次都失败返回首次结果（调用方按 uncertain 诚实记录，不伪造）。
    实测背景：偶发 vision 回复夹带散文导致 parse_failed → uncertain，
    重试即可恢复（验收轮 A hero sensenova 首轮即此情形）。
    Phase49：max_tokens 可透传——长维度 JSON（如 scene_instance_view 七维审查）
    在默认 1600 处被截断导致 JSON 不平衡 → parse_failed（真机 2026-10-08 实证）。"""
    res = await agnes_client.chat_completion_vision(msgs, model=model,
                                                    max_tokens=max_tokens)
    if _extract_json((res or {}).get("content")) is not None:
        return res
    import copy as _copy
    retry_msgs = _copy.deepcopy(msgs)
    try:
        for part in retry_msgs[0]["content"]:
            if isinstance(part, dict) and part.get("type") == "text":
                part["text"] += ("\n\nIMPORTANT: your previous reply could not be parsed as JSON. "
                                 "Reply again with ONLY the raw JSON object itself — no prose, "
                                 "no markdown fences, no explanation before or after.")
                break
    except Exception:
        pass
    try:
        res2 = await agnes_client.chat_completion_vision(retry_msgs, model=model,
                                                         max_tokens=max_tokens)
        if _extract_json((res2 or {}).get("content")) is not None:
            return res2
    except Exception:
        pass
    return res


def derive_overall(dims: Dict[str, Optional[str]],
                   dimensions: tuple = _DIMENSIONS) -> str:
    """overall 确定性推导：任何 fail→needs_fix；全 pass→pass；其余（concern/缺失）→uncertain。
    dimensions 可扩展（P1 §15：profile specified 时含 project_style_consistency——hard）。"""
    vals = [dims.get(d) for d in dimensions]
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


def record_human_review(project_dir, look_id: str, asset_id: str, *, overall: str,
                        note: str, reviewer: str = "user") -> dict:
    """人评审查证据（34.md §16/§18：真实的人评是最高审查层级）。把用户对某张
    候选的显式裁定作为一条 completed review record 落盘（append-only，与机器
    vision 审查同一证据通道）；approve 闸门逻辑不变（仍走 has_pass_evidence）。
    - overall ∈ pass | fail | uncertain（诚实落盘——不限于 pass 才能记录）；
    - note 必须非空（无理由的人评 = 不可追溯，拒绝落盘）；
    - model="human"，reviewer 记录裁定来源。绝不覆盖/改写既有机器审查记录。"""
    if overall not in ("pass", "fail", "uncertain"):
        return {"ok": False, "error": "invalid_overall",
                "detail": "overall 必须是 pass | fail | uncertain"}
    if not str(note or "").strip():
        return {"ok": False, "error": "note_required",
                "detail": "人评必须附裁定理由（无理由 = 不可追溯，不落盘）"}
    review_id = f"hr_{uuid.uuid4().hex[:12]}"
    record = {
        "review_id": review_id, "operation": "look.human_review", "look_id": look_id,
        "model": "human", "reviewer": str(reviewer or "user"),
        "candidate_asset_ids": [asset_id],
        "candidate_reviews": [{
            "asset_id": asset_id, "overall": overall,
            "summary": str(note).strip(),
        }],
        "project_style_consistency": "human_judgment",
        "started_at": time.time(), "completed_at": time.time(),
        "status": "completed", "error": None, "verified": True,
    }
    from .generation_records import append_record
    append_record(project_dir, record, path=review_records_path(project_dir))
    return {"ok": True, "operation": "look.human_review", "review_id": review_id,
            "look_id": look_id, "asset_id": asset_id, "overall": overall,
            "status": "completed"}


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
    # P1 §15：项目风格期望（unspecified → 该维度不进入本次审查，record 诚实标注）
    from src.visual_profile import load_profile_or_none
    from src.visual_profile.compiler import compile_project_style_review_expectation
    from src.visual_profile.models import visual_profile_fingerprint
    visual_profile = load_profile_or_none(pdir)   # 坏文件向上抛（router 转诚实错误）
    style_expectation = compile_project_style_review_expectation(visual_profile)
    all_dimensions = _DIMENSIONS + ((STYLE_DIMENSION,) if visual_profile else ())
    vp_fingerprint = visual_profile_fingerprint(visual_profile) if visual_profile else None
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
        appearance_delta=look.appearance_delta, view_purpose=view_purpose,
        visual_profile_fingerprint=vp_fingerprint)
    prompt = build_review_prompt(character_description=character_description,
                                 clothing_snapshot=clothing_snapshot,
                                 appearance_description=look.appearance_description,
                                 appearance_delta=look.appearance_delta,
                                 view_purpose=view_purpose,
                                 style_expectation=style_expectation)

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
            res = await vision_json_call(agnes_client, msgs, model=VISION_MODEL)
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
        dims = {d: _normalize_dim(parsed.get(d)) for d in all_dimensions}
        result = {"asset_id": asset_id, "status": "reviewed",
                  "identity_consistency": dims["identity_consistency"],
                  "identity_notes": str(parsed.get("identity_notes") or ""),
                  "costume_consistency": dims["costume_consistency"],
                  "costume_notes": str(parsed.get("costume_notes") or ""),
                  "visual_defects": dims["visual_defects"],
                  "defect_notes": str(parsed.get("defect_notes") or ""),
                  "overall": derive_overall(dims, all_dimensions),
                  "model_overall": str(parsed.get("overall") or ""),
                  "summary": str(parsed.get("summary") or "")}
        if visual_profile is not None:
            # P1 §15：hard 维度结论进 per-candidate 结果；unspecified → 维度不存在
            result[STYLE_DIMENSION] = dims[STYLE_DIMENSION]
            result["style_notes"] = str(parsed.get("style_notes") or "")
        return result

    for aid in targets:
        candidate_reviews.append(await review_one(aid))

    record = {
        "review_id": review_id, "operation": "look.review_candidates", "look_id": look_id,
        "reviewed_basis_fingerprint": basis_fp, "model": VISION_MODEL, "prompt": prompt,
        "candidate_asset_ids": list(targets),
        "candidate_reviews": candidate_reviews,
        # P1 §15：项目风格审查口径（unspecified → not_applicable，维度未参与）
        "visual_profile_fingerprint": vp_fingerprint,
        "project_style_consistency": ("evaluated" if visual_profile else "not_applicable"),
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
