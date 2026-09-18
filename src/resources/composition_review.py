# src/resources/composition_review.py
# Production Resources V1 — Phase 1C5：Composition 候选真实 Vision Review（§15）。
#
# 🔴 不是"图片好不好看"：六个维度（§15）——
#    character_identity（是不是这个角色）/ look_consistency（是不是这套 approved Look）/
#    prop_fidelity（每个 prop 是不是它本身）/ prop_binding_correctness（逐 binding 检查：
#    是不是这个道具 + 是否由正确人物呈现 + 是否在正确 anchor）/ required_prop_visibility
#    （required props 是否全部清晰可见）/ visual_defects（明显缺陷）。
#
# 🔴 prop_binding_correctness 必须逐 binding 检查（§15）：不能只检查"图里有剑和罗盘"——
#    要检查"那是 Wooden Sword 本身、由这个角色、在 right_hand"。
# 🔴 审查输入是多图（§15）：candidate + approved Look reference + 每个 prop 的 approved
#    reference，图序确定性并在 prompt 中逐一标注。
# 🔴 overall 与三个派生维度（prop_fidelity / prop_binding_correctness /
#    required_prop_visibility）由逐 binding 明细**确定性推导**，不信任模型自报
#    （model 自报整体结论不采信）；维度缺失一律不判 pass（fail closed）。
# 🔴 证据持久化 review_records.jsonl（operation=composition.review_candidates，
#    composition_id 流）；canonical 零写入；不 import 任何 Agent 模块。
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .generation import fingerprint, select_composition_inputs, to_agnes_image_ref
from .generation_records import append_record, read_records, review_records_path
from .look_prompt import _clean
from .look_review import VISION_MODEL, _extract_json, _normalize_dim, vision_json_call
from .models import CharacterReferenceComposition

_COMP_DIMENSIONS = ("character_identity", "look_consistency", "prop_fidelity",
                    "prop_binding_correctness", "required_prop_visibility", "visual_defects")
# P1 §15：项目风格一致性维度（profile specified 时追加；hard——fail → overall 不得 pass）
STYLE_DIMENSION = "project_style_consistency"


def _expectations_fingerprint(*, character_description: str, look_clothing_snapshot: Optional[dict],
                              look_appearance_description: str, look_appearance_delta: str,
                              bindings: List[dict], reference_ids: List[str],
                              visual_profile_fingerprint: Optional[str] = None) -> str:
    """审查基准指纹：本次审查对照的期望（角色/Look/外观/bindings/参考图组 + 项目风格）。"""
    return fingerprint({
        "character_description": character_description,
        "look_clothing": look_clothing_snapshot,
        "appearance_description": look_appearance_description,
        "appearance_delta": look_appearance_delta,
        "bindings": bindings,
        "reference_ids": list(reference_ids),
        "visual_profile_fingerprint": visual_profile_fingerprint,
    })


def build_composition_review_prompt(*, character_description: str,
                                    look_clothing_snapshot: Optional[dict],
                                    look_appearance_description: str = "",
                                    look_appearance_delta: str = "",
                                    bindings: List[dict],
                                    image_labels: List[str],
                                    style_expectation: str = "") -> str:
    """确定性审查 prompt（无时间/随机）。要求只回 JSON。

    bindings 每项：{prop_id, prop_name, prop_description, relation, anchor,
                    visual_state_note, required_visibility}。
    image_labels：与随图顺序一一对应的说明（IMAGE 1..N）。
    style_expectation（P1 §15）：非空时 project_style_consistency 成为追加维度（hard）。"""
    cl = look_clothing_snapshot or {}
    outfit_bits = ", ".join(x for x in (
        _clean(cl.get("name") or ""), _clean(cl.get("description") or ""),
        _clean(cl.get("design_traits") or "")) if x)
    if cl.get("materials"):
        outfit_bits += f", materials: {_clean(', '.join(map(str, cl['materials'])))}"
    if cl.get("colors"):
        outfit_bits += f", colors: {_clean(', '.join(map(str, cl['colors'])))}"

    lines: list = []
    lines.append(
        "You are a strict production reference reviewer for an animation pipeline.\n"
        "You are reviewing ONE candidate composition image against approved reference images.\n"
        "The images accompany this message in this exact order:")
    for i, lbl in enumerate(image_labels, start=1):
        lines.append(f"IMAGE {i}: {lbl}")

    lines.append("")
    lines.append(
        f"Expected character identity: {_clean(character_description) or '(unspecified)'}\n"
        f"Expected outfit (the approved look): {outfit_bits or '(as in the look reference)'}\n"
        f"Expected appearance notes: {_clean(look_appearance_description) or 'none'}"
        f"{('; ' + _clean(look_appearance_delta)) if _clean(look_appearance_delta) else ''}")
    lines.append("")
    lines.append("Expected prop bindings (each must be checked individually):")
    for b in bindings:
        req = "REQUIRED clearly visible" if str(b.get("required_visibility") or "required") == "required" \
            else "optional visibility"
        note = _clean(str(b.get("visual_state_note") or ""))
        lines.append(
            f"- prop_id={b.get('prop_id')} ({_clean(str(b.get('prop_name') or ''))}: "
            f"{_clean(str(b.get('prop_description') or ''))}) — expected "
            f"{_clean(str(b.get('relation') or ''))} at {_clean(str(b.get('anchor') or ''))}"
            f"{(' (state: ' + note + ')') if note else ''}; {req}")
    lines.append("")
    n_dims = 7 if style_expectation else 6
    dim_lines = [
        f"Check exactly these {n_dims} dimensions:",
        "1. character_identity — the character in the candidate matches the expected identity "
        "(face, hairstyle, body proportions), consistent with the look reference image.",
        "2. look_consistency — the outfit matches the approved look reference (design, colors, "
        "materials, completeness).",
        "3. prop_fidelity — each expected prop is really that prop (shape, materials, colors, "
        "defining details match its approved reference), not a substituted or generic object.",
        "4. prop_binding_correctness — PER PROP BINDING: is it really the right object, presented "
        "by the character (not floating, not held by someone else), at exactly the expected "
        "anchor/position? Report per binding.",
        "5. required_prop_visibility — every prop marked REQUIRED must be clearly visible and "
        "identifiable (not obscured, not cut off, not missing).",
        "6. visual_defects — obvious defects: malformed limbs/hands/face, artifacts, watermark "
        "or text, objects merged or fused together, extra unexplained objects.",
    ]
    if style_expectation:
        dim_lines.append(
            "7. project_style_consistency — the image must belong to the project's declared "
            "visual production style/medium (hard dimension: a style mismatch must fail).")
        dim_lines.append(style_expectation)
    lines.extend(dim_lines)
    lines.append("")
    schema = ('{"character_identity": "pass|concern|fail", "character_notes": "...",\n'
              ' "look_consistency": "pass|concern|fail", "look_notes": "...",\n'
              ' "bindings": [{"prop_id": "<must echo the expected prop_id>",\n'
              '               "object_correct": "pass|concern|fail", "holder_correct": "pass|concern|fail",\n'
              '               "anchor_correct": "pass|concern|fail",\n'
              '               "visibility": "visible|obscured|missing", "notes": "..."}],\n')
    if style_expectation:
        schema += (' "project_style_consistency": "pass|concern|fail", "style_notes": "...",\n')
    schema += (' "visual_defects": "pass|concern|fail", "defect_notes": "...",\n'
               ' "overall": "pass|needs_fix|uncertain", "summary": "..."}')
    lines.append("Answer with ONLY a JSON object (no markdown fence) in exactly this schema:\n"
                 + schema)
    return "\n".join(lines)


# ============ 维度确定性推导（§15：overall 必须由维度推导）============
def _rollup(values: List[Optional[str]]) -> Optional[str]:
    """子判定聚合：全 pass→pass；有 fail **或缺失(None，fail closed)**→fail；否则 uncertain。
    binding 子判定缺失（模型未报告该 prop / 字段不可解析）按 fail 处理——
    "图里/回复里没有"本身就是 missing-prop 缺陷信号，不能落进可批准的 uncertain。"""
    if not values:
        return None
    if any(v is None or v == "fail" for v in values):
        return "fail"
    if all(v == "pass" for v in values):
        return "pass"
    return "uncertain"


def derive_composition_overall(dims: Dict[str, Optional[str]],
                               dimensions: tuple = _COMP_DIMENSIONS) -> str:
    vals = [dims.get(d) for d in dimensions]
    if any(v == "fail" for v in vals):
        return "needs_fix"
    if all(v == "pass" for v in vals):
        return "pass"
    return "uncertain"


def derive_binding_dimensions(parsed_bindings: List[dict], expected_bindings: List[dict]) -> dict:
    """从逐 binding 明细确定性推导三个派生维度。

    fail closed：预期 binding 在回复中缺失 → 该 binding 全部子判定按 fail 处理；
    子判定字段缺失/非法 → 非 pass 处理；回复中出现未知 prop_id 的条目忽略（不作为 pass 依据）。"""
    by_id = {}
    for pb in parsed_bindings or []:
        if isinstance(pb, dict) and pb.get("prop_id"):
            by_id[str(pb["prop_id"])] = pb

    object_vals: List[Optional[str]] = []
    holder_anchor_vals: List[Optional[str]] = []
    required_vis_vals: List[bool] = []
    per_binding: List[dict] = []
    for exp in expected_bindings:
        pid = exp["prop_id"]
        pb = by_id.get(pid)
        if pb is None:
            obj = holder = anchor = None
            vis = "missing"
            notes = "binding 未在审查回复中出现（fail closed）"
        else:
            obj = _normalize_dim(pb.get("object_correct"))
            holder = _normalize_dim(pb.get("holder_correct"))
            anchor = _normalize_dim(pb.get("anchor_correct"))
            raw_vis = str(pb.get("visibility") or "").strip().lower()
            vis = raw_vis if raw_vis in ("visible", "obscured", "missing") else "missing"
            notes = str(pb.get("notes") or "")
        object_vals.append(obj)
        holder_anchor_vals.append(holder)
        holder_anchor_vals.append(anchor)
        required = str(exp.get("required_visibility") or "required") == "required"
        required_vis_vals.append((vis == "visible") if required else True)
        per_binding.append({"prop_id": pid, "object_correct": obj, "holder_correct": holder,
                            "anchor_correct": anchor, "visibility": vis, "notes": notes})

    prop_fidelity = _rollup(object_vals)
    binding_correctness = _rollup(holder_anchor_vals)
    required_visibility = "pass" if all(required_vis_vals) else "fail"
    return {
        "prop_fidelity": prop_fidelity,
        "prop_binding_correctness": binding_correctness,
        "required_prop_visibility": required_visibility,
        "per_binding": per_binding,
    }


# ============ 审查证据读取 ============
def read_composition_review_records(project_dir, *, composition_id: Optional[str] = None) -> List[dict]:
    return read_records(project_dir, composition_id=composition_id,
                        path=review_records_path(project_dir))


def latest_composition_review_for_asset(project_dir, composition_id: str, asset_id: str) -> Optional[dict]:
    for rec in reversed(read_composition_review_records(project_dir, composition_id=composition_id)):
        if rec.get("status") != "completed":
            continue
        for cr in rec.get("candidate_reviews") or []:
            if cr.get("asset_id") == asset_id:
                return cr
    return None


def has_composition_pass_evidence(project_dir, composition_id: str, asset_id: str) -> bool:
    cr = latest_composition_review_for_asset(project_dir, composition_id, asset_id)
    return bool(cr) and cr.get("overall") == "pass"


# ============ 审查执行 ============
def _expectation_bundle(project_dir, doc, project, comp: CharacterReferenceComposition) -> dict:
    """审查期望（当前 canonical 状态）+ 确定性参考图组（与生成同一选择规则）。"""
    from .models import CharacterLook
    look: Optional[CharacterLook] = doc.looks.get(comp.look_id)
    character = project.get_character(comp.character_name) if project is not None else None
    character_description = (character.description_en or "") if character is not None else ""
    clothing = doc.clothings.get(look.clothing_id) if (look is not None and look.clothing_id) else None
    clothing_snapshot = ({k: list(v) if isinstance(v, list) else v for k, v in
                          {"name": clothing.name, "description": clothing.description,
                           "design_traits": clothing.design_traits,
                           "materials": clothing.materials,
                           "colors": clothing.colors}.items()} if clothing is not None else None)
    look_appearance_description = look.appearance_description if look is not None else ""
    look_appearance_delta = look.appearance_delta if look is not None else ""

    selection = select_composition_inputs(project_dir, doc, look, comp.prop_bindings) \
        if look is not None else {"selected": []}
    expected_bindings = []
    for b in comp.prop_bindings:
        prop = doc.props.get(b.prop_id)
        expected_bindings.append({
            "prop_id": b.prop_id,
            "prop_name": prop.name if prop is not None else "(missing prop)",
            "prop_description": prop.description if prop is not None else "",
            "relation": b.relation, "anchor": b.anchor,
            "visual_state_note": b.visual_state_note,
            "required_visibility": b.required_visibility,
        })
    return {
        "character_description": character_description,
        "look": look,
        "clothing_snapshot": clothing_snapshot,
        "look_appearance_description": look_appearance_description,
        "look_appearance_delta": look_appearance_delta,
        "expected_bindings": expected_bindings,
        "selected": selection["selected"],
    }


async def review_composition_candidates(project_dir, composition_id: str, *,
                                        asset_ids: Optional[List[str]] = None,
                                        agnes_client=None) -> dict:
    """对 Composition 候选执行多图 Vision Review（每候选一次调用，逐个隔离）。
    只读 canonical；证据 append 到 review_records.jsonl；期望失败返回结构化 receipt。"""
    from .store import ResourceStore
    from src.core.store import ProjectStore

    pdir = Path(project_dir)
    review_id = f"crev_{uuid.uuid4().hex[:12]}"
    started_at = time.time()

    doc = ResourceStore(pdir).load()
    try:
        project = ProjectStore(pdir).load()
    except FileNotFoundError:
        project = None
    comp = doc.compositions.get(composition_id)
    if comp is None:
        return {"ok": False, "operation": "composition.review_candidates", "review_id": review_id,
                "composition_id": composition_id, "error": "not_found",
                "detail": f"composition 不存在: {composition_id}"}
    if not comp.reference_asset_ids:
        return {"ok": False, "operation": "composition.review_candidates", "review_id": review_id,
                "composition_id": composition_id, "error": "no_candidates",
                "detail": "该 Composition 还没有任何候选图（先 generate）"}
    targets = list(asset_ids) if asset_ids else list(comp.reference_asset_ids)
    bad = [a for a in targets if a not in comp.reference_asset_ids]
    if bad:
        return {"ok": False, "operation": "composition.review_candidates", "review_id": review_id,
                "composition_id": composition_id, "error": "not_in_candidates",
                "detail": f"asset 不在该 Composition 候选集中: {bad}"}

    if agnes_client is None:
        from deps import get_agnes_client
        agnes_client = get_agnes_client()

    exp = _expectation_bundle(pdir, doc, project, comp)
    look = exp["look"]
    if look is None:
        return {"ok": False, "operation": "composition.review_candidates", "review_id": review_id,
                "composition_id": composition_id, "error": "look_not_found",
                "detail": f"Look 不存在（审查前重验证失败）: {comp.look_id}"}

    # 参考图组（确定性：IMAGE 1 = candidate；其后 look refs + prop heroes 按 selection 顺序）
    ref_entries = [e for e in exp["selected"] if e.get("role") in ("look_hero", "look_extra_angle")]
    image_labels = ["the candidate composition image under review"]
    content_static: List[dict] = []
    look_ref_ids: List[str] = []
    for e in ref_entries:
        ref = to_agnes_image_ref(pdir, url=e.get("url"), local=e.get("local"))
        if ref is None:
            continue
        image_labels.append(f"approved {e.get('role')} reference of the character look "
                            f"({e.get('asset_id')})")
        content_static.append({"type": "image_url", "image_url": {"url": ref}})
        look_ref_ids.append(e.get("asset_id"))
    for b in exp["expected_bindings"]:
        entry = next((e for e in exp["selected"]
                      if e.get("role") == "prop_hero" and e.get("prop_id") == b["prop_id"]), None)
        if entry is None:
            continue
        ref = to_agnes_image_ref(pdir, url=entry.get("url"), local=entry.get("local"))
        if ref is None:
            continue
        image_labels.append(f"approved reference of prop '{b['prop_name']}' ({b['prop_id']})")
        content_static.append({"type": "image_url", "image_url": {"url": ref}})

    # P1 §15：项目风格期望（unspecified → 该维度不进入本次审查，record 诚实标注）
    from src.visual_profile import load_profile_or_none
    from src.visual_profile.compiler import compile_project_style_review_expectation
    from src.visual_profile.models import visual_profile_fingerprint
    visual_profile = load_profile_or_none(pdir)
    style_expectation = compile_project_style_review_expectation(visual_profile)
    all_dimensions = _COMP_DIMENSIONS + ((STYLE_DIMENSION,) if visual_profile else ())
    vp_fingerprint = visual_profile_fingerprint(visual_profile) if visual_profile else None

    basis_fp = _expectations_fingerprint(
        character_description=exp["character_description"],
        look_clothing_snapshot=exp["clothing_snapshot"],
        look_appearance_description=exp["look_appearance_description"],
        look_appearance_delta=exp["look_appearance_delta"],
        bindings=exp["expected_bindings"],
        reference_ids=look_ref_ids,
        visual_profile_fingerprint=vp_fingerprint)
    prompt = build_composition_review_prompt(
        character_description=exp["character_description"],
        look_clothing_snapshot=exp["clothing_snapshot"],
        look_appearance_description=exp["look_appearance_description"],
        look_appearance_delta=exp["look_appearance_delta"],
        bindings=exp["expected_bindings"],
        image_labels=image_labels,
        style_expectation=style_expectation)

    candidate_reviews: List[dict] = []
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}

    async def review_one(asset_id: str) -> dict:
        asset = doc.reference_assets.get(asset_id)
        if asset is None:
            return {"asset_id": asset_id, "status": "image_unreadable", "overall": "uncertain",
                    "summary": "ReferenceAsset 不存在（canonical 缺失）"}
        cand_ref = to_agnes_image_ref(pdir, url=asset.url, local=asset.local)
        if cand_ref is None:
            return {"asset_id": asset_id, "status": "image_unreadable", "overall": "uncertain",
                    "summary": "候选图不可读（local 文件缺失且无可用 url）"}
        content = [{"type": "text", "text": prompt},
                   {"type": "image_url", "image_url": {"url": cand_ref}},
                   *content_static]
        msgs = [{"role": "user", "content": content}]
        try:
            res = await vision_json_call(agnes_client, msgs, model=VISION_MODEL)
        except Exception as e:
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
        raw_bindings = parsed.get("bindings") or []
        derived = derive_binding_dimensions(raw_bindings, exp["expected_bindings"])
        dims = {
            "character_identity": _normalize_dim(parsed.get("character_identity")),
            "look_consistency": _normalize_dim(parsed.get("look_consistency")),
            "prop_fidelity": derived["prop_fidelity"],
            "prop_binding_correctness": derived["prop_binding_correctness"],
            "required_prop_visibility": derived["required_prop_visibility"],
            "visual_defects": _normalize_dim(parsed.get("visual_defects")),
        }
        if visual_profile is not None:
            # P1 §15：hard 维度；unspecified → 维度不存在，不参与 overall
            dims[STYLE_DIMENSION] = _normalize_dim(parsed.get(STYLE_DIMENSION))
        result = {"asset_id": asset_id, "status": "reviewed",
                  "character_identity": dims["character_identity"],
                  "character_notes": str(parsed.get("character_notes") or ""),
                  "look_consistency": dims["look_consistency"],
                  "look_notes": str(parsed.get("look_notes") or ""),
                  "prop_fidelity": dims["prop_fidelity"],
                  "prop_binding_correctness": dims["prop_binding_correctness"],
                  "required_prop_visibility": dims["required_prop_visibility"],
                  "visual_defects": dims["visual_defects"],
                  "defect_notes": str(parsed.get("defect_notes") or ""),
                  "per_binding": derived["per_binding"],
                  "overall": derive_composition_overall(dims, all_dimensions),
                  "model_overall": str(parsed.get("overall") or ""),
                  "summary": str(parsed.get("summary") or "")}
        if visual_profile is not None:
            result[STYLE_DIMENSION] = dims[STYLE_DIMENSION]
            result["style_notes"] = str(parsed.get("style_notes") or "")
        return result

    for aid in targets:
        candidate_reviews.append(await review_one(aid))

    record = {
        "review_id": review_id, "operation": "composition.review_candidates",
        "composition_id": composition_id,
        "reviewed_basis_fingerprint": basis_fp, "model": VISION_MODEL, "prompt": prompt,
        "candidate_asset_ids": list(targets),
        "reference_asset_ids_used": look_ref_ids,
        "candidate_reviews": candidate_reviews,
        # P1 §15：项目风格审查口径（unspecified → not_applicable，维度未参与）
        "visual_profile_fingerprint": vp_fingerprint,
        "project_style_consistency": ("evaluated" if visual_profile else "not_applicable"),
        "usage": usage_total,
        "started_at": started_at, "completed_at": time.time(),
        "status": "completed", "error": None, "verified": True,
    }
    append_record(pdir, record, path=review_records_path(pdir))
    return {"ok": True, "operation": "composition.review_candidates", "review_id": review_id,
            "composition_id": composition_id, "status": "completed",
            "reviewed_basis_fingerprint": basis_fp, "model": VISION_MODEL,
            "candidate_reviews": candidate_reviews, "review_record": record}
