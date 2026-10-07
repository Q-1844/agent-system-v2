# src/scene_system/instance_view_review.py
# Scene System V1 — Phase S2B §25-§29：SceneInstanceView Vision Review。
#
# 🔴 真 Vision（AgnesClient.chat_completion_vision，scene_system 域 VISION_MODEL
#    =agnes-3.0-flash；不 import Agent 模块，绝不让 Director Agent 判断）。
#
# 🔴 七维度（§25）：location_identity / view_consistency / anchor_consistency /
#    scene_state_consistency（§26 逐项 evidence）/ lighting_weather_consistency /
#    prop_placement_consistency（§27 逐 Prop evidence）/ visual_defects。
#    不加 character 维度（本轮没人）。
#
# 🔴 hard 集（§28）：location_identity / view_consistency / anchor_consistency /
#    scene_state_consistency 恒 hard；prop_placement_consistency 在存在可见 props 时 hard；
#    lighting_weather_consistency 在 SceneState 给了非空期望（time/weather/env 或
#    anchor_states）时 hard。任一 hard fail → overall != pass；缺失维度 fail closed
#    → uncertain（不判 pass）。模型自报 overall 只记录不采信。
#
# 🔴 §19：anchor description 中明确的 structural counts（如 three west windows）作为
#    explicit structural expectation 进入 prompt。
# 🔴 §29：证据 append-only review_records.jsonl，review_type=scene_instance_view；
#    记录 expected_state_projection；review 零改 canonical。
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.resources.generation import fingerprint, to_agnes_image_ref
from src.resources.look_review import _extract_json, _normalize_dim, vision_json_call

from . import generation_records
from .camera_review import camera_expectation_block, derive_camera_view_consistency
from .generation_records import review_records_path
from .models import (
    GenerationStatus, LocationView, SceneInstance, SceneInstanceView,
    SceneSystemDocument,
)
from .store import SceneSystemStore
from .state_projection import project_scene_state_for_view, visible_anchor_snapshots
from .view_review import VISION_MODEL, _c

SIV_DIMENSIONS = ("location_identity", "view_consistency", "anchor_consistency",
                  "scene_state_consistency", "lighting_weather_consistency",
                  "prop_placement_consistency", "visual_defects")
SIV_HARD_DIMENSIONS = ("location_identity", "view_consistency", "anchor_consistency",
                       "scene_state_consistency")
# P1 §15：项目风格一致性维度（profile specified 时追加，恒 hard）
STYLE_DIMENSION = "project_style_consistency"


# ============ 审查期望（当前 canonical）+ 期望指纹 ============
def build_scene_instance_review_expectations(doc: SceneSystemDocument,
                                             base_view: LocationView,
                                             scene_instance: SceneInstance,
                                             rdoc) -> dict:
    """审查期望：view 定义 + ViewRelevantSceneState 投影 + 可见 anchor 语义快照 +
    可见 prop 语义期望（§26/§27 逐项核验的依据）。"""
    location = doc.locations.get(scene_instance.location_id)
    anchors = [a for a in doc.anchors.values() if a.location_id == scene_instance.location_id]
    projection = project_scene_state_for_view(scene_instance, base_view, anchors)
    prop_expectations = []
    if rdoc is not None:
        for pid, placement in sorted(projection.get("prop_placements", {}).items()):
            prop = rdoc.props.get(pid)
            anchor = doc.anchors.get(placement.get("anchor_id", ""))
            prop_expectations.append({
                "prop_id": pid,
                "name": prop.name if prop else "(missing prop)",
                "description": prop.description if prop else "",
                "expected_state": placement.get("state", ""),
                "expected_anchor": placement.get("anchor_id", ""),
                "expected_anchor_name": anchor.name if anchor else "",
            })
    return {
        "review_type": "scene_instance_view",
        "location": {"location_id": location.location_id if location else None,
                     "name": location.name if location else "",
                     "identity_description": location.identity_description if location else "",
                     "style_notes": location.style_notes if location else ""},
        "view": {"view_id": base_view.view_id, "label": base_view.label,
                 "camera_zone": base_view.camera_zone,
                 "look_direction": base_view.look_direction,
                 "visible_anchor_ids": list(base_view.visible_anchor_ids)},
        "scene_state": projection,
        "anchor_snapshots": visible_anchor_snapshots(anchors, base_view),
        "prop_expectations": prop_expectations,
        "story_scene_id": scene_instance.story_scene_id,
    }


def expectations_fingerprint(expectations: dict, reference_asset_ids: List[str],
                             visual_profile_fingerprint: Optional[str] = None) -> str:
    return fingerprint({"expectations": expectations,
                        "reference_ids": list(reference_asset_ids),
                        "visual_profile_fingerprint": visual_profile_fingerprint})


# ============ 确定性审查 prompt（§19/§26/§27）============
def build_scene_instance_review_prompt(expectations: dict,
                                       image_labels: List[str],
                                       style_expectation: str = "") -> str:
    """style_expectation（P1 §15）：非空时 project_style_consistency 成为追加维度（hard）。"""
    loc = expectations.get("location") or {}
    view = expectations.get("view") or {}
    state = expectations.get("scene_state") or {}
    lines: list = []
    lines.append(
        "You are a strict production reference reviewer for an animation pipeline.\n"
        "You are reviewing ONE candidate image: a LOCATION at a specific STORY MOMENT "
        "(scene state view), derived from an approved base location reference.\n"
        "The images accompany this message in this exact order:")
    for i, lbl in enumerate(image_labels, start=1):
        lines.append(f"IMAGE {i}: {lbl}")
    lines.append("")
    lines.append(
        f"Expected location identity: {_c(loc.get('identity_description')) or '(unspecified)'}\n"
        f"Location name: {_c(loc.get('name')) or '(unspecified)'}"
        + (f"\nStyle notes: {_c(loc.get('style_notes'))}" if _c(loc.get("style_notes")) else ""))
    lines.append(
        f"Expected viewpoint: viewed from the {_c(view.get('camera_zone')) or '(base)'} "
        f"looking toward the {_c(view.get('look_direction')) or '(base)'}.")
    # C2 Work A（28.md §2.3）：SceneInstanceView 必须继承 base view 的机位——状态变化后
    # 相机不得漂回 hero/front。同一机位语义块 + observed 机位报告。
    lines.append("")
    lines.append(camera_expectation_block(_c(view.get("camera_zone")),
                                          _c(view.get("look_direction"))))
    lines.append("")
    lines.append(
        "IMPORTANT: the scene state changes CONDITIONS (light, weather, damage, objects), "
        "never the location identity. Judge whether this is the SAME room in the required "
        "state — not whether it looks identical to the base reference.")
    lines.append("")
    lines.append("Expected scene state (check EACH item individually):")
    for k in ("time_of_day", "weather", "lighting", "environment_conditions"):
        v = _c(state.get(k))
        if v:
            lines.append(f"- {k}: {v}")
    anchor_states = state.get("anchor_states") or {}
    if anchor_states:
        lines.append("Expected per-anchor state changes (each must be checked individually — "
                     "the change must be on the CORRECT anchor, not its neighbour):")
        for aid in sorted(anchor_states):
            meta = next((s for s in expectations.get("anchor_snapshots", [])
                         if s.get("anchor_id") == aid), None)
            name = _c((meta or {}).get("name")) or aid
            desc = _c((meta or {}).get("description"))
            st = anchor_states[aid]
            lines.append(f"- anchor_id={aid} ({name}"
                         + (f"; structural expectation: {desc}" if desc else "")
                         + f"): expected condition = {_c(st.get('condition')) or '(unspecified)'}")
    lines.append("Anchors with no specified change must keep their base appearance "
                 "(including any structural counts mentioned in their descriptions).")
    props = expectations.get("prop_expectations") or []
    if props:
        lines.append("Expected prop placements (each must be checked individually — the "
                     "correct object, near the expected anchor, in the expected state):")
        for p in props:
            lines.append(f"- prop_id={p['prop_id']} ({_c(p.get('name'))}"
                         + (f": {_c(p.get('description'))}" if _c(p.get("description")) else "")
                         + f") — expected state: {_c(p.get('expected_state')) or 'present'}"
                         + f", near anchor: {_c(p.get('expected_anchor_name')) or p.get('expected_anchor')}")
    lines.append("")
    n_dims = 8 if style_expectation else 7
    dim_block = (
        f"Check exactly these {n_dims} dimensions:\n"
        "1. location_identity — same location as the identity description and the base "
        "reference (architecture, scale, atmosphere unchanged by the scene state).\n"
        "2. view_consistency — viewpoint matches the expected camera zone / look direction.\n"
        "3. anchor_consistency — all major landmarks still present and undrifted; structural "
        "counts (e.g. number of windows/doors) still correct.\n"
        "4. scene_state_consistency — EACH expected state item holds (time of day, weather, "
        "environment conditions, and EVERY per-anchor state change on the correct anchor). "
        "Report per item.\n"
        "5. lighting_weather_consistency — the lighting and weather visible in the image "
        "match the expected state (only meaningful when an expectation is given).\n"
        "6. prop_placement_consistency — EACH expected prop is the correct object, near the "
        "expected anchor, in the expected state (correct | wrong_place | missing | "
        "substituted). Report per prop.\n"
        "7. visual_defects — obvious defects: malformed geometry, artifacts, watermark or "
        "text, fused objects, AND any PEOPLE (this is an empty-scene plate: people are a "
        "defect here).\n")
    if style_expectation:
        dim_block += (
            "8. project_style_consistency — the image must belong to the project's declared "
            "visual production style/medium (hard dimension: a style mismatch must fail).\n"
            + style_expectation + "\n")
    schema = ('{"location_identity": "pass|concern|fail", "location_notes": "...",\n'
              ' "camera_observed_zone": "front|side|rear|overhead|other|uncertain",\n'
              ' "camera_evidence": "...",\n'
              ' "view_consistency": "pass|concern|fail", "view_notes": "...",\n'
              ' "anchor_consistency": "pass|concern|fail", "anchor_notes": "...",\n'
              ' "scene_state": {"time_of_day": "pass|concern|fail",\n'
              '                 "weather": "pass|concern|fail",\n'
              '                 "environment_conditions": "pass|concern|fail",\n'
              '                 "anchor_states": [{"anchor_id": "<echo expected id>",\n'
              '                                    "verdict": "pass|concern|fail", "notes": "..."}],\n'
              '                 "notes": "..."},\n'
              ' "lighting_weather": "pass|concern|fail", "lighting_notes": "...",\n'
              ' "props": [{"prop_id": "<echo expected id>",\n'
              '            "observed": "correct|wrong_place|missing|substituted",\n'
              '            "verdict": "pass|concern|fail", "notes": "..."}],\n')
    if style_expectation:
        schema += (' "project_style_consistency": "pass|concern|fail", "style_notes": "...",\n')
    schema += (' "visual_defects": "pass|concern|fail", "defect_notes": "...",\n'
               ' "overall": "pass|needs_fix|uncertain", "summary": "..."}')
    lines.append(dim_block + "\n"
                 "Answer with ONLY a JSON object (no markdown fence) in exactly this schema:\n"
                 + schema)
    return "\n".join(lines)


# ============ 维度确定性推导（§26/§27/§28）============
def _rollup_dim(values: List[Optional[str]]) -> Optional[str]:
    if not values:
        return "pass"
    if any(v is None or v == "fail" for v in values):
        return "fail"
    if all(v == "pass" for v in values):
        return "pass"
    return "uncertain"


def derive_scene_state_dimension(parsed_scene_state: Optional[dict],
                                 expectations: dict) -> dict:
    """scene_state_consistency 逐项推导（§26 fail closed：预期项缺失回报 → fail；
    未给期望的项不核验）。"""
    state = expectations.get("scene_state") or {}
    parsed = parsed_scene_state or {}
    vals: List[Optional[str]] = []
    per_item: List[dict] = []
    for k in ("time_of_day", "weather", "environment_conditions"):
        expected = _c(state.get(k))
        if not expected:
            continue
        v = _normalize_dim(parsed.get(k))
        vals.append(v)
        per_item.append({"item": k, "expected": expected, "verdict": v or "fail"})
    exp_anchor_states = state.get("anchor_states") or {}
    by_id = {}
    for pa in parsed.get("anchor_states") or []:
        if isinstance(pa, dict) and pa.get("anchor_id"):
            by_id[str(pa["anchor_id"])] = pa
    for aid in sorted(exp_anchor_states):
        pa = by_id.get(aid)
        if pa is None:
            v = None
            notes = "anchor state 未在审查回复中出现（fail closed）"
        else:
            v = _normalize_dim(pa.get("verdict"))
            notes = str(pa.get("notes") or "")
        vals.append(v)
        per_item.append({"item": f"anchor_state:{aid}",
                         "expected": _c(exp_anchor_states[aid].get("condition")),
                         "verdict": v or "fail", "notes": notes})
    dim = _rollup_dim(vals) if vals else "pass"
    return {"scene_state_consistency": dim, "scene_state_items": per_item}


def derive_prop_dimension(parsed_props: Any, expectations: dict) -> dict:
    """prop_placement_consistency 逐 Prop 推导（§27 fail closed：预期 prop 缺失回报 →
    fail）。无可见 props → pass（vacuous，非 hard）。"""
    exp_props = expectations.get("prop_expectations") or []
    if not exp_props:
        return {"prop_placement_consistency": "pass", "per_prop": []}
    by_id = {}
    for pp in (parsed_props or []):
        if isinstance(pp, dict) and pp.get("prop_id"):
            by_id[str(pp["prop_id"])] = pp
    vals: List[Optional[str]] = []
    per_prop: List[dict] = []
    for p in exp_props:
        pid = p["prop_id"]
        pp = by_id.get(pid)
        if pp is None:
            v = None
            observed = "missing"
            notes = "prop 未在审查回复中出现（fail closed）"
        else:
            v = _normalize_dim(pp.get("verdict"))
            raw_obs = str(pp.get("observed") or "").strip().lower()
            observed = raw_obs if raw_obs in ("correct", "wrong_place", "missing",
                                              "substituted") else "missing"
            notes = str(pp.get("notes") or "")
        vals.append(v)
        per_prop.append({"prop_id": pid, "expected_state": _c(p.get("expected_state")),
                         "expected_anchor": _c(p.get("expected_anchor_name")),
                         "observed": observed, "verdict": v or "fail", "notes": notes})
    return {"prop_placement_consistency": _rollup_dim(vals), "per_prop": per_prop}


def derive_scene_instance_overall(dims: Dict[str, Optional[str]], *,
                                  has_visible_props: bool,
                                  has_state_expectation: bool,
                                  dimensions: tuple = SIV_DIMENSIONS) -> str:
    """overall 确定性推导（§28）：任一 hard fail → needs_fix；全维度 pass → pass；
    其余 → uncertain。dimensions 可扩展（P1 §15：profile specified 时含
    project_style_consistency——恒 hard，风格不符不得 approval）。"""
    hard = set(SIV_HARD_DIMENSIONS)
    if has_visible_props:
        hard.add("prop_placement_consistency")
    if has_state_expectation:
        hard.add("lighting_weather_consistency")
    vals = [dims.get(d) for d in dimensions]
    if any(v == "fail" and d in hard for d, v in zip(dimensions, vals)):
        return "needs_fix"
    if all(v == "pass" for v in vals):
        return "pass"
    return "uncertain"


# ============ 证据读取 ============
def read_instance_view_review_records(project_dir, *,
                                      scene_instance_view_id: Optional[str] = None) -> List[dict]:
    recs = generation_records.read_records(project_dir, path=review_records_path(project_dir))
    if scene_instance_view_id is None:
        return recs
    return [r for r in recs
            if r.get("operation") == "scene_instance_view.review_candidates"
            and r.get("scene_instance_view_id") == scene_instance_view_id]


def latest_pass_review_for_instance_view(project_dir, *, scene_instance_view_id: str,
                                         generation_id: str,
                                         candidate_index: int) -> Optional[dict]:
    """针对 (sciv, generation, candidate) 的最新有效 pass 审查（approve 门槛证据）。"""
    for rec in reversed(read_instance_view_review_records(
            project_dir, scene_instance_view_id=scene_instance_view_id)):
        if rec.get("status") != "completed" or rec.get("generation_id") != generation_id:
            continue
        for cr in rec.get("candidate_reviews") or []:
            if cr.get("candidate_index") == candidate_index and cr.get("overall") == "pass":
                return {"record": rec, "candidate_review": cr}
    return None


# ============ 审查执行 ============
async def review_scene_instance_view_candidates(project_dir, scene_instance_view_id: str, *,
                                                agnes_client=None) -> dict:
    """对 SceneInstanceView 当前 committed 候选执行七维度 Vision Review。
    只读 canonical；证据 append review_records.jsonl；canonical 零写入。"""
    pdir = Path(project_dir)
    review_id = f"sirev_{uuid.uuid4().hex[:12]}"
    started_at = time.time()
    store = SceneSystemStore(pdir)

    doc = store.load()
    sciv = doc.scene_instance_views.get(scene_instance_view_id)
    if sciv is None:
        return {"ok": False, "operation": "scene_instance_view.review_candidates",
                "review_id": review_id, "scene_instance_view_id": scene_instance_view_id,
                "error": "instance_view_not_found",
                "detail": f"SceneInstanceView 不存在: {scene_instance_view_id}"}
    generation_id = sciv.active_generation_id
    if not generation_id or sciv.generation_status != GenerationStatus.READY.value:
        return {"ok": False, "operation": "scene_instance_view.review_candidates",
                "review_id": review_id, "scene_instance_view_id": scene_instance_view_id,
                "error": "no_candidates",
                "detail": "没有 committed 候选（generation_status != ready；先 generate）"}
    gen_recs = [r for r in generation_records.read_records(pdir, generation_id=generation_id)
                if r.get("operation") == "scene_instance_view.generate"
                and r.get("status") == "committed"]
    if not gen_recs:
        return {"ok": False, "operation": "scene_instance_view.review_candidates",
                "review_id": review_id, "scene_instance_view_id": scene_instance_view_id,
                "error": "generation_record_missing",
                "detail": f"committed generation record 不存在: {generation_id}"}
    gen_rec = gen_recs[-1]
    candidate_files = list(gen_rec.get("candidate_files") or [])
    if not candidate_files:
        return {"ok": False, "operation": "scene_instance_view.review_candidates",
                "review_id": review_id, "scene_instance_view_id": scene_instance_view_id,
                "error": "no_candidates", "detail": "generation record 无候选文件"}
    scene_instance = doc.scene_instances.get(sciv.scene_instance_id)
    base_view = doc.views.get(sciv.location_view_id)
    if scene_instance is None or base_view is None:
        return {"ok": False, "operation": "scene_instance_view.review_candidates",
                "review_id": review_id, "scene_instance_view_id": scene_instance_view_id,
                "error": "canonical_changed",
                "detail": "SceneInstance 或 base LocationView 已不存在"}

    if agnes_client is None:
        from deps import get_agnes_client
        agnes_client = get_agnes_client()

    from .visual_basis import load_resources_doc_or_none
    rdoc = load_resources_doc_or_none(pdir)
    expectations = build_scene_instance_review_expectations(doc, base_view, scene_instance,
                                                            rdoc)
    # 对比图（§22 同源）：该候选生成时使用的 refs（base/peer/prop——从 basis 分字段重建）
    from .instance_view_basis import basis_reference_entries
    ref_entries = basis_reference_entries(pdir, rdoc, gen_rec.get("basis") or {})
    image_labels = ["the candidate scene state image under review"]
    content_static: List[dict] = []
    ref_ids_used: List[str] = []
    for e in ref_entries:
        ref = to_agnes_image_ref(pdir, url=e.get("url"), local=e.get("local"))
        if ref is None:
            continue
        role = e.get("role", "reference")
        if role == "base_location_view":
            lbl = "approved base location reference (the same location, neutral state)"
        elif role == "peer_state_view":
            lbl = "approved peer scene state view of the SAME story moment (another direction)"
        elif role == "prop_reference":
            lbl = f"approved reference of prop {e.get('prop_id')}"
        else:
            lbl = f"approved reference ({role})"
        image_labels.append(lbl)
        content_static.append({"type": "image_url", "image_url": {"url": ref}})
        ref_ids_used.append(e.get("asset_id"))

    # P1 §15：项目风格期望（unspecified → 该维度不进入本次审查，record 诚实标注）
    from src.visual_profile import load_profile_or_none
    from src.visual_profile.compiler import compile_project_style_review_expectation
    from src.visual_profile.models import visual_profile_fingerprint as vp_fp_fn
    visual_profile = load_profile_or_none(pdir)
    style_expectation = compile_project_style_review_expectation(visual_profile)
    all_dimensions = SIV_DIMENSIONS + ((STYLE_DIMENSION,) if visual_profile else ())
    vp_fingerprint = vp_fp_fn(visual_profile) if visual_profile else None

    basis_fp = expectations_fingerprint(expectations, ref_ids_used,
                                        visual_profile_fingerprint=vp_fingerprint)
    prompt = build_scene_instance_review_prompt(expectations, image_labels,
                                                style_expectation=style_expectation)
    has_visible_props = bool(expectations.get("prop_expectations"))
    proj = expectations.get("scene_state") or {}
    has_state_exp = bool(_c(proj.get("time_of_day")) or _c(proj.get("weather"))
                         or _c(proj.get("environment_conditions"))
                         or proj.get("anchor_states"))

    candidate_reviews: List[dict] = []
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}

    async def review_one(candidate_index: int, candidate_file: str) -> dict:
        cand_ref = to_agnes_image_ref(pdir, url=None, local=candidate_file)
        if cand_ref is None:
            return {"candidate_index": candidate_index, "candidate_file": candidate_file,
                    "status": "image_unreadable", "overall": "uncertain",
                    "summary": "候选图不可读（本地文件缺失）"}
        content = [{"type": "text", "text": prompt},
                   {"type": "image_url", "image_url": {"url": cand_ref}},
                   *content_static]
        msgs = [{"role": "user", "content": content}]
        try:
            res = await vision_json_call(agnes_client, msgs, model=VISION_MODEL,
                                         max_tokens=6000)
        except Exception as e:
            return {"candidate_index": candidate_index, "candidate_file": candidate_file,
                    "status": "provider_error", "overall": "uncertain",
                    "summary": f"{type(e).__name__}: {e}"}
        u = (res or {}).get("usage") or {}
        usage_total["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
        usage_total["completion_tokens"] += int(u.get("completion_tokens") or 0)
        parsed = _extract_json((res or {}).get("content"))
        if parsed is None:
            return {"candidate_index": candidate_index, "candidate_file": candidate_file,
                    "status": "parse_failed", "overall": "uncertain",
                    "summary": "vision 回复无法解析为 JSON（fail closed，不可作为 pass 证据）",
                    "raw_head": ((res or {}).get("content") or "")[:200]}
        ss = derive_scene_state_dimension(parsed.get("scene_state"), expectations)
        pd = derive_prop_dimension(parsed.get("props"), expectations)
        # C2 Work A（28.md §2.3）：view_consistency 确定性推导——base view 机位 vs
        # 模型报告的 observed 机位（状态变化后相机漂回 hero → fail）。
        cam_verdict, cam_note = derive_camera_view_consistency(
            str((expectations.get("view") or {}).get("camera_zone") or ""),
            parsed.get("camera_observed_zone"),
            _normalize_dim(parsed.get("view_consistency")))
        dims = {
            "location_identity": _normalize_dim(parsed.get("location_identity")),
            "view_consistency": cam_verdict,
            "anchor_consistency": _normalize_dim(parsed.get("anchor_consistency")),
            "scene_state_consistency": ss["scene_state_consistency"],
            "lighting_weather_consistency": _normalize_dim(parsed.get("lighting_weather")),
            "prop_placement_consistency": pd["prop_placement_consistency"],
            "visual_defects": _normalize_dim(parsed.get("visual_defects")),
        }
        if visual_profile is not None:
            # P1 §15：hard 维度；unspecified → 维度不存在，不参与 overall
            dims[STYLE_DIMENSION] = _normalize_dim(parsed.get(STYLE_DIMENSION))
        result = {"candidate_index": candidate_index, "candidate_file": candidate_file,
                  "status": "reviewed",
                  "dimensions": dims,
                  "location_notes": str(parsed.get("location_notes") or ""),
                  "camera_observed_zone": str(parsed.get("camera_observed_zone") or ""),
                  "camera_evidence": str(parsed.get("camera_evidence") or ""),
                  "camera_derivation_note": cam_note,
                  "view_notes": str(parsed.get("view_notes") or ""),
                  "anchor_notes": str(parsed.get("anchor_notes") or ""),
                  "scene_state_items": ss["scene_state_items"],
                  "scene_state_notes": str((parsed.get("scene_state") or {}).get("notes") or ""),
                  "lighting_notes": str(parsed.get("lighting_notes") or ""),
                  "per_prop": pd["per_prop"],
                  "defect_notes": str(parsed.get("defect_notes") or ""),
                  "overall": derive_scene_instance_overall(
                      dims, has_visible_props=has_visible_props,
                      has_state_expectation=has_state_exp,
                      dimensions=all_dimensions),
                  "model_overall": str(parsed.get("overall") or ""),
                  "summary": str(parsed.get("summary") or "")}
        if visual_profile is not None:
            result[STYLE_DIMENSION] = dims[STYLE_DIMENSION]
            result["style_notes"] = str(parsed.get("style_notes") or "")
        return result

    for idx, f in enumerate(candidate_files):
        candidate_reviews.append(await review_one(idx, f))

    record = {
        "review_id": review_id, "operation": "scene_instance_view.review_candidates",
        "review_type": "scene_instance_view",
        "scene_instance_view_id": scene_instance_view_id,
        "scene_instance_id": sciv.scene_instance_id,
        "location_view_id": sciv.location_view_id,
        "generation_id": generation_id,
        "reviewed_basis_fingerprint": basis_fp, "model": VISION_MODEL, "prompt": prompt,
        "candidate_files": list(candidate_files),
        "reference_asset_ids_used": ref_ids_used,
        "expected_state_projection": expectations.get("scene_state"),
        "candidate_reviews": candidate_reviews,
        # P1 §15：项目风格审查口径（unspecified → not_applicable，维度未参与）
        "visual_profile_fingerprint": vp_fingerprint,
        "project_style_consistency": ("evaluated" if visual_profile else "not_applicable"),
        "usage": usage_total,
        "started_at": started_at, "completed_at": time.time(),
        "status": "completed", "error": None,
    }
    generation_records.append_record(pdir, record, path=review_records_path(pdir))
    return {"ok": True, "operation": "scene_instance_view.review_candidates",
            "review_id": review_id, "scene_instance_view_id": scene_instance_view_id,
            "generation_id": generation_id, "status": "completed",
            "reviewed_basis_fingerprint": basis_fp, "model": VISION_MODEL,
            "candidate_reviews": candidate_reviews, "review_record": record}
