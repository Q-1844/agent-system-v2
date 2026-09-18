# src/resources/prop_review.py
# Production Resources V1 — Phase 1C5：Prop 参考图视觉审查 + 审批证据（§7 最小闭环）。
#
# 🔴 缺口来源：Phase 1C4 的 Prop 只有 CRUD + references attach/detach，
#    approval_status 字段存在但没有任何 review/approve 路径。Composition 生成
#    只消费 approved Prop references（§7），因此 Prop 必须先具备完整的
#    ReferenceAsset → Visual Review → Approval 语义。
#
# 🔴 审查维度（§7）：prop_fidelity（是不是这个道具：形状/材质/颜色/细节与描述一致）/
#    visual_clarity（清晰可读：轮廓清楚、光照均匀、关键信息无遮挡）/
#    visual_defects（明显缺陷：畸形几何、伪影、水印文字、多余元素）。
# 🔴 overall 由三维度确定性推导（不信任模型自报 overall——与 look_review 同口径）；
#    证据持久化到 output/resources/review_records.jsonl（operation=prop.review_candidates，
#    prop_id 字段区分于 look 流）。canonical 零写入。
# 🔴 并行路径（Phase 1A §10.3 决策沿用）：直接调 AgnesClient.chat_completion_vision
#    （data URI 输入），不 import 任何 Agent 模块，不触碰 Director。
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .generation import fingerprint, to_agnes_image_ref
from .generation_records import append_record, review_records_path, read_records
from .look_prompt import _clean
from .look_review import VISION_MODEL, _extract_json, _normalize_dim, vision_json_call

_PROP_DIMENSIONS = ("prop_fidelity", "visual_clarity", "visual_defects")
# P1 §15：项目风格一致性维度（profile specified 时追加；hard——fail → overall 不得 pass）
STYLE_DIMENSION = "project_style_consistency"


def _expectations_fingerprint(*, prop_name: str, prop_description: str, prop_tags: List[str],
                              visual_profile_fingerprint: Optional[str] = None) -> str:
    """审查基准指纹：本次审查对照的道具期望（+ 项目风格，P1 §15）。"""
    return fingerprint({"prop_name": prop_name, "prop_description": prop_description,
                        "tags": list(prop_tags),
                        "visual_profile_fingerprint": visual_profile_fingerprint})


def build_prop_review_prompt(*, prop_name: str, prop_description: str = "",
                             prop_tags: Optional[List[str]] = None,
                             style_expectation: str = "") -> str:
    """确定性审查 prompt（无时间/随机）。要求只回 JSON。
    style_expectation（P1 §15）：非空时 project_style_consistency 成为第 4 个维度（hard）。"""
    tags = ", ".join(_clean(str(t)) for t in (prop_tags or []) if _clean(str(t)))
    n_dims = 4 if style_expectation else 3
    parts = [
        "You are a strict production reference reviewer for an animation pipeline.\n"
        "Review THIS candidate image of a PROP against the EXPECTED baseline below.\n\n"
        f"Expected prop: {_clean(prop_name) or '(unspecified)'}\n"
        f"Expected description: {_clean(prop_description) or '(unspecified)'}\n"
        f"Tags: {tags or '(none)'}\n\n"
        f"Check exactly these {n_dims} dimensions:\n"
        "1. prop_fidelity — the object is really the expected prop (shape, materials, colors, "
        "defining details all match the description); nothing essential is missing or replaced.\n"
        "2. visual_clarity — the prop reads clearly as a standalone production reference: clean "
        "composition, even lighting, whole object visible, silhouette and key details readable.\n"
        "3. visual_defects — obvious defects: malformed geometry, artifacts, watermark or text, "
        "unrelated objects merged into the prop, broken perspective.\n",
    ]
    if style_expectation:
        parts.append(
            "4. project_style_consistency — judge whether the PROP OBJECT ITSELF matches the "
            "project's declared visual production style/medium: its materials, shape language, "
            "textures, line work, era feel and rendering medium must visibly belong to the "
            "declared style (hard dimension: a real style mismatch must fail).\n"
            "WHAT DOES NOT COUNT AS A STYLE FAILURE: a prop production reference is EXPECTED to "
            "be presented as an isolated object on a neutral / plain / white / transparent / "
            "simple background with even lighting. That presentation format must NOT by itself "
            "cause project_style_consistency to fail — do not demand scene atmosphere, "
            "environment, dramatic lighting or full composition from a prop reference.\n"
            "Only fail when the prop object itself reads as a different medium or style family "
            "than declared (for example a photorealistic product photograph when the project "
            "expects 2D cel animation).\n"
            f"{style_expectation}\n")
    schema = ('{"prop_fidelity": "pass|concern|fail", "fidelity_notes": "...",\n'
              ' "visual_clarity": "pass|concern|fail", "clarity_notes": "...",\n'
              ' "visual_defects": "pass|concern|fail", "defect_notes": "...",\n')
    if style_expectation:
        schema += (' "project_style_consistency": "pass|concern|fail", "style_notes": "...",\n')
    schema += ' "overall": "pass|needs_fix|uncertain", "summary": "..."}'
    parts.append("Answer with ONLY a JSON object (no markdown fence) in exactly this schema:\n"
                 + schema)
    return "\n".join(parts)


def derive_prop_overall(dims: Dict[str, Optional[str]],
                        dimensions: tuple = _PROP_DIMENSIONS) -> str:
    """overall 确定性推导：任何 fail→needs_fix；全 pass→pass；其余（concern/缺失）→uncertain。
    维度缺失一律不判 pass（fail closed）。dimensions 可扩展（P1 §15 hard 维度）。"""
    vals = [dims.get(d) for d in dimensions]
    if any(v == "fail" for v in vals):
        return "needs_fix"
    if all(v == "pass" for v in vals):
        return "pass"
    return "uncertain"


# ============ 审查证据读取（approve 门槛查询）============
def read_prop_review_records(project_dir, *, prop_id: Optional[str] = None) -> List[dict]:
    return read_records(project_dir, prop_id=prop_id,
                        path=review_records_path(project_dir))


def latest_prop_review_for_asset(project_dir, prop_id: str, asset_id: str) -> Optional[dict]:
    """该 asset 在该 prop 下最近一次"实际审到它"的 per-candidate 结果；无则 None。"""
    for rec in reversed(read_prop_review_records(project_dir, prop_id=prop_id)):
        if rec.get("status") != "completed":
            continue
        for cr in rec.get("candidate_reviews") or []:
            if cr.get("asset_id") == asset_id:
                return cr
    return None


def has_prop_pass_evidence(project_dir, prop_id: str, asset_id: str) -> bool:
    """approve 门槛：该 asset 在该 prop 下最近一次审查 overall=pass。"""
    cr = latest_prop_review_for_asset(project_dir, prop_id, asset_id)
    return bool(cr) and cr.get("overall") == "pass"


# ============ 审查执行 ============
async def review_prop_reference(project_dir, prop_id: str, *,
                                asset_ids: Optional[List[str]] = None,
                                agnes_client=None) -> dict:
    """对 Prop 的参考图执行视觉审查（每候选一次 vision 调用，逐个隔离）。
    自包含：只读 canonical（不写 resources.json）；证据 append 到 review_records.jsonl。
    期望失败返回结构化 receipt。"""
    from .store import ResourceStore

    pdir = Path(project_dir)
    review_id = f"prev_{uuid.uuid4().hex[:12]}"
    started_at = time.time()

    doc = ResourceStore(pdir).load()
    prop = doc.props.get(prop_id)
    if prop is None:
        return {"ok": False, "operation": "prop.review_candidates", "review_id": review_id,
                "prop_id": prop_id, "error": "not_found", "detail": f"prop 不存在: {prop_id}"}
    if not prop.reference_asset_ids:
        return {"ok": False, "operation": "prop.review_candidates", "review_id": review_id,
                "prop_id": prop_id, "error": "no_candidates",
                "detail": "该 Prop 还没有任何参考图（先注册/绑定 reference asset）"}
    targets = list(asset_ids) if asset_ids else list(prop.reference_asset_ids)
    bad = [a for a in targets if a not in prop.reference_asset_ids]
    if bad:
        return {"ok": False, "operation": "prop.review_candidates", "review_id": review_id,
                "prop_id": prop_id, "error": "not_in_candidates",
                "detail": f"asset 不在该 Prop 的参考集中: {bad}"}

    if agnes_client is None:
        from deps import get_agnes_client   # 惰性（与 generation 同惯例）
        agnes_client = get_agnes_client()

    # P1 §15：项目风格期望（unspecified → 该维度不进入本次审查，record 诚实标注）
    from src.visual_profile import load_profile_or_none
    from src.visual_profile.compiler import compile_project_style_review_expectation
    from src.visual_profile.models import visual_profile_fingerprint
    visual_profile = load_profile_or_none(pdir)
    style_expectation = compile_project_style_review_expectation(visual_profile)
    all_dimensions = _PROP_DIMENSIONS + ((STYLE_DIMENSION,) if visual_profile else ())
    vp_fingerprint = visual_profile_fingerprint(visual_profile) if visual_profile else None

    basis_fp = _expectations_fingerprint(prop_name=prop.name,
                                         prop_description=prop.description,
                                         prop_tags=list(prop.tags),
                                         visual_profile_fingerprint=vp_fingerprint)
    prompt = build_prop_review_prompt(prop_name=prop.name, prop_description=prop.description,
                                      prop_tags=list(prop.tags),
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
                    "summary": "参考图不可读（local 文件缺失且无可用 url）"}
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
                  "prop_fidelity": dims["prop_fidelity"],
                  "fidelity_notes": str(parsed.get("fidelity_notes") or ""),
                  "visual_clarity": dims["visual_clarity"],
                  "clarity_notes": str(parsed.get("clarity_notes") or ""),
                  "visual_defects": dims["visual_defects"],
                  "defect_notes": str(parsed.get("defect_notes") or ""),
                  "overall": derive_prop_overall(dims, all_dimensions),
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
        "review_id": review_id, "operation": "prop.review_candidates", "prop_id": prop_id,
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
    append_record(pdir, record, path=review_records_path(pdir))
    return {"ok": True, "operation": "prop.review_candidates", "review_id": review_id,
            "prop_id": prop_id, "status": "completed",
            "reviewed_basis_fingerprint": basis_fp, "model": VISION_MODEL,
            "candidate_reviews": candidate_reviews, "review_record": record}
