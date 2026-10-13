# src/scene_system/view_review.py
# Scene System V1 — Phase S2A §21-§24：LocationView 候选真实 Vision Review。
#
# 🔴 用真 Vision 能力（AgnesClient.chat_completion_vision——与 Resources review 同一
#    中性 client 方法，不复制低层 HTTP contract，不 import 任何 Agent 模块；
#    绝不让 Director Agent 判断）。
#
# 🔴 六维度（§21）：location_identity / anchor_visibility（指定 visible_anchor_ids
#    是否真的可见，逐 anchor）/ anchor_consistency（主要 Landmark 有无漂移/替换）/
#    layout_consistency（门窗控制台楼梯相对空间关系是否合理）/ view_consistency
#    （是否符合 camera_zone + look_direction）/ visual_defects。
#    本轮不加 SceneState 维度（§29：天气/损坏/时间属于下一阶段）。
#
# 🔴 overall 确定性推导（§23）：模型只给各维度观察证据；overall 由系统代码推导，
#    模型自报 overall 不采信（仅记录 model_overall）。关键维度（§23）：
#    location_identity / anchor_consistency / layout_consistency / view_consistency
#    任一 fail → 不得 PASS；anchor_visibility 有 required anchor 缺失/缺失回报
#    同样 fail（视角定义的核心承诺——有意加入 hard 集，报告中说明）。
#    维度缺失一律不判 pass（fail closed）。
#
# 🔴 审查回答"是不是同一个地点"（§22），不是"两张图像不像"：材质允许因视角改变
#    而亮度/色调不同；门数量、窗型、主要 Landmark、布局关系不能无故漂移。
#    对比策略：第一张 view = candidate vs 规格说明；后续 = candidate + 该候选生成时
#    使用的 1~2 张 approved Location references（记录在 generation basis）+ 规格说明。
#
# 🔴 审查不改变 Scene canonical（§24）：证据 append-only 到
#    output/scene_system/review_records.jsonl；canonical 零写入。
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.resources.generation import fingerprint, to_agnes_image_ref
from src.resources.look_review import _extract_json, _normalize_dim, vision_json_call

# Scene System 域自含视觉审查模型常量（同 Resources 惯例：本域自含，不 import agents）。
# S2B 起：agnes-3.0-flash（2026-09-11 探针实证：视觉+JSON 遵从 OK、延迟不劣于 2.5、
# 判定质量更好——identity 与 state 失配正确分离，2.5 会把状态失配渗入 identity 判定）。
# 🔴 冻结 Resources 域（look/prop/composition review）维持 2.5-flash 不动——切换属
# Resources 域决策，不在 Scene System 轮次内偷偷改。
VISION_MODEL = "agnes-3.0-flash"

from . import generation_records
from .camera_review import camera_expectation_block, derive_camera_view_consistency
from .generation_records import review_records_path
from .models import GenerationStatus, SceneSystemDocument, LocationView
from .store import SceneSystemStore
from .visual_basis import _anchor_snapshot, _layout_snapshot, _location_snapshot

VIEW_DIMENSIONS = ("location_identity", "anchor_visibility", "anchor_consistency",
                   "layout_consistency", "view_consistency", "visual_defects")

# §23 关键维度（任一 fail → overall 不得 PASS）
VIEW_KEY_DIMENSIONS = ("location_identity", "anchor_consistency",
                       "layout_consistency", "view_consistency")


# ============ 审查期望（当前 canonical）+ 期望指纹 ============
def build_review_expectations(doc: SceneSystemDocument, view: LocationView) -> dict:
    """从当前 canonical 构造审查期望（确定性）。含 required anchors 语义明细。"""
    location = doc.locations.get(view.location_id)
    anchors = sorted((a for a in doc.anchors.values() if a.location_id == view.location_id),
                     key=lambda a: a.anchor_id)
    layout = next((l for l in doc.layouts.values() if l.location_id == view.location_id), None)
    by_id = {a.anchor_id: a for a in anchors}
    required_anchors = []
    for aid in view.visible_anchor_ids:
        a = by_id.get(aid)
        required_anchors.append({
            "anchor_id": aid,
            "name": a.name if a else "(missing anchor)",
            "kind": a.kind if a else None,
            "description": a.description if a else "",
        })
    relations = []
    for a in anchors:
        for r in a.relations:
            if r.object_anchor_id in by_id:
                relations.append({"subject": a.name, "relation": r.relation,
                                  "object": by_id[r.object_anchor_id].name})
    return {
        "location": _location_snapshot(location) if location else None,
        "anchors": [_anchor_snapshot(a) for a in anchors],
        "layout": _layout_snapshot(layout),
        "view": {"view_id": view.view_id, "label": view.label,
                 "camera_zone": view.camera_zone, "look_direction": view.look_direction,
                 "visible_anchor_ids": list(view.visible_anchor_ids)},
        "required_anchors": required_anchors,
        "relations": relations,
    }


def expectations_fingerprint(expectations: dict, reference_asset_ids: List[str],
                             visual_profile_fingerprint: Optional[str] = None) -> str:
    """审查基准指纹：本次审查对照的期望（规格 + 实际附上的 approved 参考组 + 项目风格）。"""
    return fingerprint({"expectations": expectations,
                        "reference_ids": list(reference_asset_ids),
                        "visual_profile_fingerprint": visual_profile_fingerprint})


# ============ 确定性审查 prompt（§21/§22）============
def build_view_review_prompt(expectations: dict, image_labels: List[str],
                             style_expectation: str = "") -> str:
    """确定性审查 prompt（无时间/随机）。要求只回 JSON。
    style_expectation（P1 §15）：非空时 project_style_consistency 成为追加维度（hard）。"""
    loc = expectations.get("location") or {}
    view = expectations.get("view") or {}
    lines: list = []
    lines.append(
        "You are a strict production reference reviewer for an animation pipeline.\n"
        "You are reviewing ONE candidate environment/location image against the location "
        "specification (and, when present, approved reference images of the SAME location).\n"
        "The images accompany this message in this exact order:")
    for i, lbl in enumerate(image_labels, start=1):
        lines.append(f"IMAGE {i}: {lbl}")
    lines.append("")
    lines.append(
        f"Expected location identity: {_c(loc.get('identity_description')) or _c(loc.get('description')) or '(unspecified)'}\n"
        f"Location name: {_c(loc.get('name')) or '(unspecified)'}"
        + (f"\nStyle notes: {_c(loc.get('style_notes'))}" if _c(loc.get("style_notes")) else ""))
    lines.append("")
    lines.append("Required anchors — each must be checked individually for visibility:")
    for a in expectations.get("required_anchors", []):
        lines.append(f"- anchor_id={a.get('anchor_id')} ({_c(a.get('name'))}"
                     f"{'; ' + _c(a.get('kind')) if _c(a.get('kind')) else ''})"
                     f"{': ' + _c(a.get('description')) if _c(a.get('description')) else ''}")
    if expectations.get("relations"):
        rel = "; ".join(f"the {_c(r['subject'])} is {_c(r['relation'])} the {_c(r['object'])}"
                        for r in expectations["relations"])
        lines.append(f"Expected spatial relations: {rel}.")
    lay = expectations.get("layout")
    if lay:
        hints = []
        for p in lay.get("placements", []):
            zone = p.get("zone") or ""
            hints.append(f"{p.get('anchor_id')}: {zone or 'see coordinates'}")
        lines.append(f"Layout hints (semantic orientation only, not exact 3D): "
                     f"orientation={_c(lay.get('orientation_convention')) or '(none)'}; "
                     + "; ".join(hints) + ".")
    lines.append("")
    lines.append(
        f"Camera expectation: viewed from the {_c(view.get('camera_zone')) or '(unspecified)'} "
        f"of the space, looking toward the {_c(view.get('look_direction')) or '(unspecified)'}.")
    # C2 Work A（28.md §2.2）：机位正确性语义块——相机位置先于/独立于 anchor 可见性判断。
    lines.append("")
    lines.append(camera_expectation_block(_c(view.get("camera_zone")),
                                          _c(view.get("look_direction"))))
    lines.append("")
    n_dims = 7 if style_expectation else 6
    dim_block = (
        "IMPORTANT comparison principle: you are judging whether this candidate shows THE SAME "
        "LOCATION — not whether it looks pixel-similar to the reference images. Materials may "
        "differ in brightness/hue because the viewing angle changed. But the COUNT of doors, "
        "window types, major landmarks, and their layout relations must not drift or be "
        "substituted.\n"
        f"Check exactly these {n_dims} dimensions:\n"
        "1. location_identity — the candidate depicts the same kind of place as the expected "
        "identity description (architecture, atmosphere, scale), consistent with the approved "
        "references when present.\n"
        "2. anchor_visibility — PER REQUIRED ANCHOR: is it clearly visible and identifiable in "
        "the candidate (clearly_visible | partially | missing)? Report per anchor.\n"
        "3. anchor_consistency — the major landmarks match the specification: no substitution, "
        "no drift in their defining details (door count, window shape, console form, stairs).\n"
        "4. layout_consistency — the relative spatial arrangement of landmarks is consistent "
        "with the specification/layout hints (e.g. the door sits behind the console; windows sit "
        "on whichever side the layout specifies — judge by the stated layout, not by screen "
        "left/right).\n"
        "5. view_consistency — the viewpoint matches the expected camera zone and look "
        "direction (we are looking from where the view says, toward what it says).\n"
        "6. visual_defects — obvious defects: malformed geometry, artifacts, watermark or text, "
        "fused objects, extra unexplained structures, people/props/weather that should not be "
        "in a base location reference.\n")
    if style_expectation:
        dim_block += (
            "7. project_style_consistency — the image must belong to the project's declared "
            "visual production style/medium (hard dimension: a style mismatch must fail).\n"
            + style_expectation + "\n")
    schema = ('{"location_identity": "pass|concern|fail", "location_notes": "...",\n'
              ' "anchors": [{"anchor_id": "<must echo the expected anchor_id>",\n'
              '              "visibility": "clearly_visible|partially|missing", "notes": "..."}],\n'
              ' "anchor_consistency": "pass|concern|fail", "anchor_notes": "...",\n'
              ' "layout_consistency": "pass|concern|fail", "layout_notes": "...",\n'
              ' "camera_observed_zone": "front|side|rear|overhead|other|uncertain",\n'
              ' "camera_evidence": "...",\n'
              ' "view_consistency": "pass|concern|fail", "view_notes": "...",\n')
    if style_expectation:
        schema += (' "project_style_consistency": "pass|concern|fail", "style_notes": "...",\n')
    schema += (' "visual_defects": "pass|concern|fail", "defect_notes": "...",\n'
               ' "overall": "pass|needs_fix|uncertain", "summary": "..."}')
    lines.append(dim_block + "\n"
                 "Answer with ONLY a JSON object (no markdown fence) in exactly this schema:\n"
                 + schema)
    return "\n".join(lines)


def _c(v: Any) -> str:
    return " ".join(str(v or "").split())


# ============ 维度确定性推导（§23）============
def derive_anchor_visibility(parsed_anchors: List[dict],
                             expected_anchors: List[dict]) -> dict:
    """逐 anchor 可见性 → 维度判定。fail closed：预期 anchor 在回复中缺失 → missing；
    非法 visibility 值 → missing（不能落进可批准的不确定）。"""
    by_id = {}
    for pa in parsed_anchors or []:
        if isinstance(pa, dict) and pa.get("anchor_id"):
            by_id[str(pa["anchor_id"])] = pa
    vals: List[Optional[str]] = []
    per_anchor: List[dict] = []
    for exp in expected_anchors:
        aid = exp["anchor_id"]
        pa = by_id.get(aid)
        if pa is None:
            vis, notes = "missing", "anchor 未在审查回复中出现（fail closed）"
        else:
            raw = str(pa.get("visibility") or "").strip().lower()
            vis = raw if raw in ("clearly_visible", "partially", "missing") else "missing"
            notes = str(pa.get("notes") or "")
        # 维度子判定：missing → fail；partially → concern；clearly_visible → pass
        vals.append({"clearly_visible": "pass", "partially": "concern",
                     "missing": "fail"}[vis])
        per_anchor.append({"anchor_id": aid, "visibility": vis, "notes": notes})
    if not expected_anchors:
        dim: Optional[str] = "pass"   # view 定义没有 required anchors（vacuous 成立）
    elif any(v == "fail" for v in vals):
        dim = "fail"
    elif all(v == "pass" for v in vals):
        dim = "pass"
    else:
        dim = "uncertain"
    return {"anchor_visibility": dim, "per_anchor": per_anchor}


def derive_view_overall(dims: Dict[str, Optional[str]],
                        dimensions: tuple = VIEW_DIMENSIONS) -> str:
    """overall 确定性推导（§23）：关键维度（含 anchor_visibility 的 fail 路径）任一
    fail → needs_fix；全维度 pass → pass；其余（concern/uncertain/缺失）→ uncertain。
    dimensions 可扩展（P1 §15：profile specified 时含 project_style_consistency——hard）。"""
    vals = [dims.get(d) for d in dimensions]
    if any(v == "fail" for v in vals):
        return "needs_fix"
    if all(v == "pass" for v in vals):
        return "pass"
    return "uncertain"


# ============ 证据读取 ============
def read_view_review_records(project_dir, *, view_id: Optional[str] = None) -> List[dict]:
    return generation_records.read_records(project_dir, view_id=view_id,
                                           path=review_records_path(project_dir))


def latest_pass_review(project_dir, *, view_id: str, generation_id: str,
                       candidate_index: int) -> Optional[dict]:
    """针对 (view, generation, candidate) 的最新有效 pass 审查（approve 门槛证据）。
    只认 status=completed 且 candidate_overall=pass 的记录。"""
    for rec in reversed(read_view_review_records(project_dir, view_id=view_id)):
        if rec.get("status") != "completed" or rec.get("generation_id") != generation_id:
            continue
        for cr in rec.get("candidate_reviews") or []:
            if cr.get("candidate_index") == candidate_index and cr.get("overall") == "pass":
                return {"record": rec, "candidate_review": cr}
    return None


# ============ 审查执行 ============
async def review_location_view_candidates(project_dir, view_id: str, *,
                                          agnes_client=None) -> dict:
    """对 LocationView 当前候选（active_generation_id 的 committed 候选）执行 Vision Review。
    只读 canonical；证据 append review_records.jsonl；canonical 零写入。
    期望失败返回结构化 receipt。"""
    pdir = Path(project_dir)
    review_id = f"srev_{uuid.uuid4().hex[:12]}"
    started_at = time.time()
    store = SceneSystemStore(pdir)

    doc = store.load()
    view = doc.views.get(view_id)
    if view is None:
        return {"ok": False, "operation": "scene.review_candidates", "review_id": review_id,
                "view_id": view_id, "error": "view_not_found",
                "detail": f"LocationView 不存在: {view_id}"}
    generation_id = view.active_generation_id
    if not generation_id:
        return {"ok": False, "operation": "scene.review_candidates", "review_id": review_id,
                "view_id": view_id, "error": "no_candidates",
                "detail": "该 LocationView 没有 committed 候选（generation_status != ready；先 generate）"}
    if view.generation_status != GenerationStatus.READY.value:
        return {"ok": False, "operation": "scene.review_candidates", "review_id": review_id,
                "view_id": view_id, "error": "not_ready",
                "detail": f"当前 generation_status={view.generation_status}（需要 ready）"}

    # 该 generation 的 committed 记录（候选文件清单）
    gen_recs = [r for r in generation_records.read_records(pdir, generation_id=generation_id)
                if r.get("operation") == "scene.generate_location_view"
                and r.get("status") == "committed"]
    if not gen_recs:
        return {"ok": False, "operation": "scene.review_candidates", "review_id": review_id,
                "view_id": view_id, "error": "generation_record_missing",
                "detail": f"committed generation record 不存在: {generation_id}"}
    gen_rec = gen_recs[-1]
    candidate_files = list(gen_rec.get("candidate_files") or [])
    if not candidate_files:
        return {"ok": False, "operation": "scene.review_candidates", "review_id": review_id,
                "view_id": view_id, "error": "no_candidates",
                "detail": "generation record 无候选文件"}

    if agnes_client is None:
        from deps import get_agnes_client
        agnes_client = get_agnes_client()

    expectations = build_review_expectations(doc, view)

    # P1 §15：项目风格期望（unspecified → 该维度不进入本次审查，record 诚实标注）
    from src.visual_profile import load_profile_or_none
    from src.visual_profile.compiler import compile_project_style_review_expectation
    from src.visual_profile.models import visual_profile_fingerprint as vp_fp_fn
    visual_profile = load_profile_or_none(pdir)
    style_expectation = compile_project_style_review_expectation(visual_profile)
    all_dimensions = VIEW_DIMENSIONS + (("project_style_consistency",) if visual_profile else ())
    vp_fingerprint = vp_fp_fn(visual_profile) if visual_profile else None

    # 对比参考（§22）：该候选生成时使用的 approved references（记录在 generation basis），
    # 对当前 resources 解析出可用图。
    ref_entries = list((gen_rec.get("basis") or {}).get("selected_references") or [])
    image_labels = ["the candidate location image under review"]
    content_static: List[dict] = []
    ref_ids_used: List[str] = []
    for e in ref_entries:
        ref = to_agnes_image_ref(pdir, url=e.get("url"), local=e.get("local"))
        if ref is None:
            continue
        image_labels.append(f"approved reference of the same location "
                            f"(view {e.get('view_id')}, asset {e.get('asset_id')})")
        content_static.append({"type": "image_url", "image_url": {"url": ref}})
        ref_ids_used.append(e.get("asset_id"))

    basis_fp = expectations_fingerprint(expectations, ref_ids_used,
                                        visual_profile_fingerprint=vp_fingerprint)
    prompt = build_view_review_prompt(expectations, image_labels,
                                      style_expectation=style_expectation)

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
            # Phase53 A1（用户授权的实验解堵例外，2026-10-13）：母版视图审查与
            # instance_view_review 对齐 max_tokens=6000——Phase49 只接了 sciv 七维审查，
            # 本处漏改；thinking 长响应在默认 1600 处截断 → JSON 不平衡 → parse_failed
            # → 母版永不可批 → 全部 sciv fail-closed 阻塞（真机 run_e0f1a17c44e5 实证）。
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
        derived = derive_anchor_visibility(parsed.get("anchors") or [],
                                           expectations["required_anchors"])
        # C2 Work A（28.md §2.2）：view_consistency 确定性推导——requested camera zone
        # （词元可识别时）对照模型报告的 observed 机位；矛盾 → fail、无证据 → 不判 pass。
        cam_verdict, cam_note = derive_camera_view_consistency(
            str((expectations.get("view") or {}).get("camera_zone") or ""),
            parsed.get("camera_observed_zone"),
            _normalize_dim(parsed.get("view_consistency")))
        dims = {
            "location_identity": _normalize_dim(parsed.get("location_identity")),
            "anchor_visibility": derived["anchor_visibility"],
            "anchor_consistency": _normalize_dim(parsed.get("anchor_consistency")),
            "layout_consistency": _normalize_dim(parsed.get("layout_consistency")),
            "view_consistency": cam_verdict,
            "visual_defects": _normalize_dim(parsed.get("visual_defects")),
        }
        if visual_profile is not None:
            # P1 §15：hard 维度；unspecified → 维度不存在，不参与 overall
            dims["project_style_consistency"] = _normalize_dim(
                parsed.get("project_style_consistency"))
        result = {"candidate_index": candidate_index, "candidate_file": candidate_file,
                  "status": "reviewed",
                  "dimensions": dims,
                  "location_notes": str(parsed.get("location_notes") or ""),
                  "per_anchor": derived["per_anchor"],
                  "anchor_notes": str(parsed.get("anchor_notes") or ""),
                  "layout_notes": str(parsed.get("layout_notes") or ""),
                  # C2 Work A：机位正确性证据与确定性推导注记（requested vs observed）
                  "camera_observed_zone": str(parsed.get("camera_observed_zone") or ""),
                  "camera_evidence": str(parsed.get("camera_evidence") or ""),
                  "camera_derivation_note": cam_note,
                  "view_notes": str(parsed.get("view_notes") or ""),
                  "defect_notes": str(parsed.get("defect_notes") or ""),
                  "overall": derive_view_overall(dims, all_dimensions),
                  "model_overall": str(parsed.get("overall") or ""),
                  "summary": str(parsed.get("summary") or "")}
        if visual_profile is not None:
            # P1 §15：拍平到顶层（与 Resources review 结果同形，便于消费方统一读取）
            result["project_style_consistency"] = dims["project_style_consistency"]
            result["style_notes"] = str(parsed.get("style_notes") or "")
        return result

    for idx, f in enumerate(candidate_files):
        candidate_reviews.append(await review_one(idx, f))

    record = {
        "review_id": review_id, "operation": "scene.review_candidates",
        "location_id": view.location_id, "view_id": view_id,
        "generation_id": generation_id,
        "reviewed_basis_fingerprint": basis_fp, "model": VISION_MODEL, "prompt": prompt,
        "candidate_files": list(candidate_files),
        "reference_asset_ids_used": ref_ids_used,
        "candidate_reviews": candidate_reviews,
        # P1 §15：项目风格审查口径（unspecified → not_applicable，维度未参与）
        "visual_profile_fingerprint": vp_fingerprint,
        "project_style_consistency": ("evaluated" if visual_profile else "not_applicable"),
        "usage": usage_total,
        "started_at": started_at, "completed_at": time.time(),
        "status": "completed", "error": None,
    }
    generation_records.append_record(pdir, record, path=review_records_path(pdir))
    return {"ok": True, "operation": "scene.review_candidates", "review_id": review_id,
            "view_id": view_id, "generation_id": generation_id, "status": "completed",
            "reviewed_basis_fingerprint": basis_fp, "model": VISION_MODEL,
            "candidate_reviews": candidate_reviews, "review_record": record}
