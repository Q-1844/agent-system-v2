# src/resources/generation.py
# Production Resources V1 — Phase 1C1 Work C：GenerationBasis + 参考图选择 + 生成辅助。
#
# 🔴 GenerationBasis 是一次生成的事实快照（§11）：以后必须能回答"当时具体使用的是哪一个
#    Alice 状态、哪一个 Black Coat revision、哪几张参考图"。不只是 "Alice + Black Coat"。
#
# 🔴 语义指纹（§19 stale-basis 保护的核心）：
#    basis_fingerprint = sha256(canonical_json(basis))[:8]，覆盖**生成语义子集**：
#    角色身份指纹 + 描述快照 + 实际使用的参考图（路径/URL/内容 sha256）+ clothing 内容
#    revision + appearance 字段 + prompt + model/size/ratio + seed/candidate_count/view_purpose。
#    生成结束后用同一参数重算，指纹不同 → stale_generation_basis，拒绝 commit。
#    故意**不含**裸 look_revision（含 updated_at/reference ids——label 改名等无害写会被误判）。
#
# 🔴 data URI 只存在于内存（送 Agnes），绝不写 resources.json（§13）。
#    参考 URL/local/sha256 进 basis；base64 不进任何持久化。
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .look_prompt import build_look_candidate_prompt
from .composition_prompt import build_composition_prompt
from .models import (CharacterLook, Clothing, ReferenceAsset,
                     clothing_revision, look_revision, prop_revision)
from .store import legacy_character_references

# legacy Asset.local 既可能是项目相对路径（Phase 1B 样本）也可能是绝对路径（既有 handler/router 落盘）。
# 统一解析规则：先按项目相对路径试，再按绝对路径试。
_DATA_URI_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".webp": "image/webp", ".bmp": "image/bmp"}


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def fingerprint(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()[:8]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ============ 本地文件解析 / 读取 ============
def resolve_local_file(project_dir, local: Optional[str]) -> Optional[Path]:
    """项目相对 → 绝对；绝对原样。必须存在为文件，否则 None。
    相对路径的基准回退：project_dir → project_dir.parent → CWD。
    🔴 背景（真实项目实测发现）：character bind 端点（routers/characters.py）落盘的
    views.local 是相对**后端工作目录**的路径（projects/{name}/output/characters/…），
    而 project_dir 是 output 目录——只按 project_dir 解析必然 miss，导致 Look 生成
    missing_character_reference。多基准回退对已可解析的输入（绝对/相对 project_dir）
    行为逐字节不变，只增加回退。"""
    if not local:
        return None
    p = Path(local)
    if not p.is_absolute():
        bases = [Path(project_dir), Path(project_dir).parent, Path.cwd()]
        for base in bases:
            cand = base / p
            if cand.is_file():
                return cand
        return None
    return p if p.is_file() else None


def load_image_bytes(project_dir, local: Optional[str]) -> Optional[bytes]:
    p = resolve_local_file(project_dir, local)
    if p is None:
        return None
    try:
        return p.read_bytes()
    except OSError:
        return None


def to_data_uri(project_dir, local: Optional[str]) -> Optional[str]:
    """local 图片 → data URI（仅内存使用，绝不持久化 base64）。"""
    data = load_image_bytes(project_dir, local)
    if data is None:
        return None
    p = resolve_local_file(project_dir, local) or Path(local or "")
    mime = _DATA_URI_MIME.get(p.suffix.lower()) or mimetypes.guess_type(str(p))[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def to_agnes_image_ref(project_dir, *, url: Optional[str], local: Optional[str]) -> Optional[str]:
    """选取送 Agnes 的参考形式（§13）：local 可读 → data URI（内容可验证的产物优先）；
    否则公网 URL；都不可用 → None。"""
    if local:
        uri = to_data_uri(project_dir, local)
        if uri:
            return uri
    return url or None


# ============ 参考图选择（确定性顺序，§12/§13）============
def _ref_entry(*, view: str, asset_id: str, url: Optional[str], local: Optional[str],
               project_dir) -> dict:
    data = load_image_bytes(project_dir, local)
    return {
        "view": view or "",
        "asset_id": asset_id,
        "url": url,
        "local": local,
        "sha256": sha256_bytes(data) if data is not None else None,
    }


def select_character_identity_refs(project_dir, character_name: str) -> List[dict]:
    """Character 身份参考（§12）：复用 legacy_character_references，固定顺序
    front → side → back（adapter 已保证）；只保留实际可用项（url 或可读 local）。
    可用性判定与确定性顺序是 basis 的一部分——不依赖 dict 随机顺序。"""
    refs = legacy_character_references(project_dir, character_name)
    out: List[dict] = []
    for r in refs:   # adapter 固定 front/side/back 顺序
        e = _ref_entry(view=r.view, asset_id=r.asset_id, url=r.url, local=r.local,
                       project_dir=project_dir)
        if to_agnes_image_ref(project_dir, url=r.url, local=r.local) is not None:
            out.append(e)
    return out


def select_clothing_refs(project_dir, doc, clothing: Clothing) -> List[dict]:
    """Clothing 参考（§13）：按 reference_asset_ids 存储顺序（确定性）选取可用项。"""
    out: List[dict] = []
    for aid in clothing.reference_asset_ids:
        a: Optional[ReferenceAsset] = doc.reference_assets.get(aid)
        if a is None:
            continue
        e = _ref_entry(view=a.view, asset_id=a.asset_id, url=a.url, local=a.local,
                       project_dir=project_dir)
        if to_agnes_image_ref(project_dir, url=a.url, local=a.local) is not None:
            out.append(e)
    return out


def agnes_image_inputs(project_dir, refs: List[dict]) -> List[str]:
    """basis 条目 → Agnes image_refs（data URI 优先，内存态）。"""
    out: List[str] = []
    for e in refs:
        ref = to_agnes_image_ref(project_dir, url=e.get("url"), local=e.get("local"))
        if ref:
            out.append(ref)
    return out


# ============ GenerationBasis（§11）============
def character_identity_fingerprint(character) -> Optional[str]:
    """Character 创作身份指纹 = name + description_en（Phase 1A §5/§18.B 结论：
    views 不属于创作身份）。resources 域自含实现，不 import revision/agents。"""
    if character is None:
        return None
    return fingerprint({"name": character.name,
                        "description_en": character.description_en or ""})


def _clothing_snapshot(c: Clothing) -> dict:
    return {
        "clothing_id": c.clothing_id,
        "name": c.name,
        "description": c.description,
        "design_traits": c.design_traits,
        "materials": list(c.materials),
        "colors": list(c.colors),
    }


def compute_generation_basis(project_dir, doc, project, look: CharacterLook, *,
                             model_id: str, size_tier: str, ratio: str,
                             seed: Optional[int], candidate_count: int,
                             view_purpose: str,
                             visual_profile=None) -> dict:
    """从当前 canonical 状态构造一次生成的完整事实快照 + 语义指纹。
    纯函数式：不写盘、不调网络。同状态 + 同参数 → 同 basis（确定性）。
    visual_profile（P1 §11）：ProjectVisualProfile 或 None；非 None 时编译后的
    项目 style 指令进 prompt 后缀，snapshot + visual_profile_fingerprint 入 basis
    （profile 变更 → 指纹变 → stale，recompute 报 project_visual_profile_changed）。"""
    project_dir = Path(project_dir)
    character = project.get_character(look.character_name) if project is not None else None
    char_refs = select_character_identity_refs(project_dir, look.character_name) \
        if character is not None else []
    clothing = doc.clothings.get(look.clothing_id) if look.clothing_id else None
    cloth_refs = select_clothing_refs(project_dir, doc, clothing) if clothing is not None else []

    domain_prompt = build_look_candidate_prompt(
        character_description=(character.description_en or "") if character is not None else "",
        clothing_name=(clothing.name if clothing is not None else ""),
        clothing_description=(clothing.description if clothing is not None else ""),
        clothing_design_traits=(clothing.design_traits if clothing is not None else ""),
        clothing_materials=(list(clothing.materials) if clothing is not None else None),
        clothing_colors=(list(clothing.colors) if clothing is not None else None),
        appearance_description=look.appearance_description,
        appearance_delta=look.appearance_delta,
        view_purpose=view_purpose,
    )
    from src.visual_profile.compiler import append_style_instruction
    from src.visual_profile.models import (profile_snapshot,
                                           visual_profile_fingerprint)
    prompt = append_style_instruction(domain_prompt, visual_profile)

    basis: Dict[str, Any] = {
        "look_id": look.look_id,
        "character_name": look.character_name,
        "character_identity_fingerprint": character_identity_fingerprint(character),
        "character_description_snapshot": (character.description_en or "") if character is not None else None,
        "character_reference_snapshot": char_refs,          # 实际使用的 legacy views（含内容 sha256）
        "clothing_id": look.clothing_id,
        "clothing_revision": clothing_revision(clothing) if clothing is not None else None,
        "clothing_description_snapshot": _clothing_snapshot(clothing) if clothing is not None else None,
        "clothing_reference_asset_ids_used": [e["asset_id"] for e in cloth_refs],
        "clothing_reference_snapshot": cloth_refs,
        "appearance_description": look.appearance_description,
        "appearance_delta": look.appearance_delta,
        "view_purpose": view_purpose or "",
        "candidate_count": candidate_count,
        "seed": seed,
        "model": {"provider": ("sensenova" if str(model_id).startswith("sensenova")
                               else "agnes"),
                  "model_id": model_id,
                  "size_tier": size_tier, "ratio": ratio},
        "prompt": prompt,
    }
    if visual_profile is not None:
        basis["visual_profile"] = profile_snapshot(visual_profile)
        basis["visual_profile_fingerprint"] = visual_profile_fingerprint(visual_profile)
    basis["basis_fingerprint"] = fingerprint(basis)
    return basis


def _profile_stale_reason(project_dir, basis: dict) -> Optional[str]:
    """P1 §13：basis 存有 visual_profile_fingerprint 且与当前 profile 不一致 →
    明确 stale 理由 project_visual_profile_changed。legacy basis（无该键）→ None
    （P1 §14：不伪造、不自动全局 stale，兼容语义由 read 层 unknown 报告）。"""
    stored = basis.get("visual_profile_fingerprint")
    if not stored:
        return None
    from src.visual_profile import load_profile_or_none
    from src.visual_profile.models import visual_profile_fingerprint
    profile = load_profile_or_none(project_dir)
    current_fp = visual_profile_fingerprint(profile) if profile is not None else None
    if current_fp != stored:
        return "project_visual_profile_changed"
    return None


def recompute_basis_still_holds(project_dir, doc, project, look_id: str,
                                basis: dict) -> Tuple[bool, str]:
    """§19/§20：生成结束后重算 basis，比对语义指纹。
    look 已删除 → (False, "look_missing")；profile 变更 → (False,
    "project_visual_profile_changed")（P1 §13，先于全量重算给明确理由）；
    其余指纹不同 → (False, "basis_changed")。
    绝不 runtime 偷偷替换 basis 后强行提交。"""
    look = doc.looks.get(look_id)
    if look is None:
        return False, "look_missing"
    profile_reason = _profile_stale_reason(project_dir, basis)
    if profile_reason:
        return False, profile_reason
    from src.visual_profile import load_profile_or_none
    current = compute_generation_basis(
        project_dir, doc, project, look,
        model_id=basis["model"]["model_id"],
        size_tier=basis["model"]["size_tier"],
        ratio=basis["model"]["ratio"],
        seed=basis["seed"],
        candidate_count=basis["candidate_count"],
        view_purpose=basis["view_purpose"],
        visual_profile=load_profile_or_none(project_dir),
    )
    if current["basis_fingerprint"] != basis["basis_fingerprint"]:
        return False, "basis_changed"
    return True, ""


# ============ 候选图落盘（§16/§17）============
def save_candidate_files(project_dir, look_id: str, generation_id: str,
                         images: List[bytes]) -> List[str]:
    """原始生成图（不做 rembg、不替换透明 PNG）落盘到统一新路径：
    output/resources/looks/{look_id}/generations/{generation_id}/candidate_NN.png
    与 display label 解耦：rename Look 不动磁盘目录。返回项目相对 posix 路径。"""
    root = Path(project_dir) / "output" / "resources" / "looks" / look_id / "generations" / generation_id
    root.mkdir(parents=True, exist_ok=True)
    out: List[str] = []
    for i, data in enumerate(images, start=1):
        name = f"candidate_{i:02d}.png"
        (root / name).write_bytes(data)
        out.append(f"output/resources/looks/{look_id}/generations/{generation_id}/{name}")
    return out


# ============ Composition 参考图确定性选择 + 硬预算（§12）============
MAX_COMPOSITION_INPUT_REFS = 6   # 硬输入预算上限（§12）；正常选择最大 1+1+3=5，此值为防御上限


def _approved_entry(project_dir, doc, asset_id: str) -> Optional[dict]:
    """approved asset → basis entry（含内容 sha256）；asset 缺失返回 None。"""
    a = doc.reference_assets.get(asset_id)
    if a is None:
        return None
    return _ref_entry(view=a.view, asset_id=a.asset_id, url=a.url, local=a.local,
                      project_dir=project_dir)


def _usable(project_dir, entry: dict) -> bool:
    return to_agnes_image_ref(project_dir, url=entry.get("url"), local=entry.get("local")) is not None


def _pick_hero(entry_ids: List[str], cover_asset_id: Optional[str]) -> Optional[str]:
    """hero 规则（确定性）：cover_asset_id ∈ 集合 → cover；否则集合第一个。"""
    if not entry_ids:
        return None
    if cover_asset_id and cover_asset_id in entry_ids:
        return cover_asset_id
    return entry_ids[0]


def select_composition_inputs(project_dir, doc, look, bindings) -> dict:
    """Composition 参考图确定性最小选择（§12，最少但足够）：

    1. Look approved hero（1 张）：cover_asset_id ∈ approved → cover，否则 approved 第一个；
    2. 每 binding 的 Prop approved hero（各 1 张）：同 hero 规则（binding 顺序）；
    3. 额外 Look angle（≤1 张）：仅当 bindings ≥ 2 且 approved 有第二个（多道具组合
       注意力分散风险更高 → 双 Look 锚定），取 approved 顺序中第一个非 hero 项。

    优先级截断：look hero + prop heroes 优先保留；超预算先截 extra Look angle。
    selection 全量记录进 basis（§12）。纯函数，不写盘不调网络。"""
    look_approved = [aid for aid in look.approved_reference_asset_ids
                     if aid in doc.reference_assets]
    selected: List[dict] = []
    look_ids_used: List[str] = []

    hero_id = _pick_hero(look_approved, look.cover_asset_id)
    if hero_id:
        e = _approved_entry(project_dir, doc, hero_id)
        if e is not None:
            e = {**e, "role": "look_hero"}
            selected.append(e)
            look_ids_used.append(hero_id)

    prop_section: List[dict] = []
    prop_ids_used: List[str] = []
    for b in bindings:
        prop = doc.props.get(b.prop_id)
        approved = [aid for aid in (prop.approved_reference_asset_ids if prop else [])
                    if aid in doc.reference_assets]
        pid = _pick_hero(approved, prop.cover_asset_id if prop else None)
        entry = _approved_entry(project_dir, doc, pid) if pid else None
        if entry is not None:
            entry = {**entry, "role": "prop_hero", "prop_id": b.prop_id}
            selected.append(entry)
            prop_ids_used.append(pid)
        prop_section.append({
            "prop_id": b.prop_id,
            "approved_reference_ids_used": ([pid] if pid else []),
            "reference_entry": entry,
        })

    # 额外 Look angle：bindings ≥ 2 且存在第二个 approved look ref
    extra_entry = None
    if len(bindings) >= 2:
        rest = [aid for aid in look_approved if aid != hero_id]
        if rest:
            e = _approved_entry(project_dir, doc, rest[0])
            if e is not None:
                extra_entry = {**e, "role": "look_extra_angle"}

    # 硬预算截断（defensive：正常 1+3+1=5 ≤ 6）
    if len(selected) + (1 if extra_entry else 0) > MAX_COMPOSITION_INPUT_REFS:
        extra_entry = None   # extra 最先截

    if extra_entry is not None:
        selected.append(extra_entry)
        look_ids_used.append(extra_entry["asset_id"])

    return {
        "selected": selected,
        "look_reference_ids_used": look_ids_used,
        "prop_section": prop_section,
        "extra_look_angle_included": extra_entry is not None,
        "input_budget": {"max": MAX_COMPOSITION_INPUT_REFS, "used": len(selected)},
    }


# ============ Composition GenerationBasis（§8）============
def _binding_section(project_dir, doc, b, prop_section_by_id: Dict[str, dict]) -> dict:
    """单条 binding 的 basis 段：prop 内容快照 + 关系 + 实际使用的 approved refs（含 checksum）。"""
    prop = doc.props.get(b.prop_id)
    section = prop_section_by_id.get(b.prop_id) or {}
    entry = section.get("reference_entry")
    return {
        "prop_id": b.prop_id,
        "prop_revision": prop_revision(prop) if prop is not None else None,
        "prop_name": prop.name if prop is not None else None,
        "prop_description_snapshot": prop.description if prop is not None else None,
        "relation": b.relation,
        "anchor": b.anchor,
        "visual_state_note": b.visual_state_note,
        "required_visibility": b.required_visibility,
        "approved_reference_ids_used": list(section.get("approved_reference_ids_used") or []),
        "reference_snapshot": ([{k: entry[k] for k in ("view", "asset_id", "url", "local", "sha256")}]
                               if entry else []),
    }


def compute_composition_basis(project_dir, doc, project, composition, *,
                              model_id: str, size_tier: str, ratio: str,
                              seed: Optional[int], candidate_count: int,
                              visual_profile=None) -> dict:
    """从当前 canonical 状态构造一次 composition 生成的事实快照 + 语义指纹。

    语义指纹覆盖（§8）：composition 语义内容（不含 label/状态机/时间戳字段——display
    label 改名等无害写不误判 stale）+ naked look_revision（look 不被 composition 流改动，
    显式要求"Look 更新 → stale"）+ 每 prop 的 naked prop_revision + 实际使用的参考图
    （ids + 内容 sha256）+ prompt/model/seed/count。生成结束后同参数重算比对。
    纯函数式：不写盘、不调网络。caller 在指纹计算之后自行追加 started_at（不入指纹）。
    visual_profile（P1 §11）：非 None 时项目 style 指令进 prompt 后缀，
    snapshot + visual_profile_fingerprint 入 basis。"""
    project_dir = Path(project_dir)
    look = doc.looks.get(composition.look_id)
    character = project.get_character(composition.character_name) if project is not None else None

    selection = select_composition_inputs(project_dir, doc, look, composition.prop_bindings)
    section_by_id = {s["prop_id"]: s for s in selection["prop_section"]}
    clothing = doc.clothings.get(look.clothing_id) if (look is not None and look.clothing_id) else None
    binding_sections = [_binding_section(project_dir, doc, b, section_by_id)
                        for b in composition.prop_bindings]

    domain_prompt = build_composition_prompt(
        character_description=(character.description_en or "") if character is not None else "",
        look_clothing_snapshot=_clothing_snapshot(clothing) if clothing is not None else None,
        look_appearance_description=(look.appearance_description if look is not None else ""),
        look_appearance_delta=(look.appearance_delta if look is not None else ""),
        bindings=[{"prop_name": s["prop_name"],
                   "prop_description": (s["prop_description_snapshot"] or ""),
                   "relation": s["relation"], "anchor": s["anchor"],
                   "visual_state_note": s["visual_state_note"],
                   "required_visibility": s["required_visibility"]}
                  for s in binding_sections],
        pose_note=composition.pose_note,
        composition_description=composition.composition_description,
    )
    from src.visual_profile.compiler import append_style_instruction
    from src.visual_profile.models import (profile_snapshot,
                                           visual_profile_fingerprint)
    prompt = append_style_instruction(domain_prompt, visual_profile)

    basis: Dict[str, Any] = {
        "composition_id": composition.composition_id,
        "character_name": composition.character_name,
        "character_identity_fingerprint": character_identity_fingerprint(character),
        "character_description_snapshot": (character.description_en or "") if character is not None else None,
        "look_id": composition.look_id,
        "look_revision": look_revision(look) if look is not None else None,
        "look_reference_ids_used": selection["look_reference_ids_used"],
        "look_reference_snapshot": [{k: e[k] for k in ("view", "asset_id", "url", "local", "sha256")}
                                    for e in selection["selected"]
                                    if e.get("role") in ("look_hero", "look_extra_angle")],
        "prop_bindings": binding_sections,
        "pose_note": composition.pose_note,
        "composition_description": composition.composition_description,
        "selected_input_refs": [{k: e.get(k) for k in ("role", "prop_id", "view", "asset_id",
                                                       "url", "local", "sha256")}
                                for e in selection["selected"]],
        "input_budget": selection["input_budget"],
        "candidate_count": candidate_count,
        "seed": seed,
        "model": {"provider": ("sensenova" if str(model_id).startswith("sensenova")
                               else "agnes"),
                  "model_id": model_id,
                  "size_tier": size_tier, "ratio": ratio},
        "prompt": prompt,
    }
    if visual_profile is not None:
        basis["visual_profile"] = profile_snapshot(visual_profile)
        basis["visual_profile_fingerprint"] = visual_profile_fingerprint(visual_profile)
    basis["basis_fingerprint"] = fingerprint(basis)
    return basis


def recompute_composition_basis_still_holds(project_dir, doc, project, composition_id: str,
                                            basis: dict) -> Tuple[bool, str]:
    """§8：生成结束后重算 basis，比对语义指纹。
    composition 已删除 → (False, "composition_missing")；profile 变更 → (False,
    "project_visual_profile_changed")（P1 §13）；其余指纹不同 → (False, "basis_changed")。
    绝不 runtime 偷偷替换 basis 后强行提交。"""
    composition = doc.compositions.get(composition_id)
    if composition is None:
        return False, "composition_missing"
    profile_reason = _profile_stale_reason(project_dir, basis)
    if profile_reason:
        return False, profile_reason
    from src.visual_profile import load_profile_or_none
    current = compute_composition_basis(
        project_dir, doc, project, composition,
        model_id=basis["model"]["model_id"],
        size_tier=basis["model"]["size_tier"],
        ratio=basis["model"]["ratio"],
        seed=basis["seed"],
        candidate_count=basis["candidate_count"],
        visual_profile=load_profile_or_none(project_dir),
    )
    if current["basis_fingerprint"] != basis["basis_fingerprint"]:
        return False, "basis_changed"
    return True, ""


# ============ Composition 候选图落盘（§13）============
def save_composition_candidate_files(project_dir, composition_id: str, generation_id: str,
                                     images: List[bytes]) -> List[str]:
    """原始生成图落盘到统一路径（§13）：
    output/resources/compositions/{composition_id}/generations/{generation_id}/candidate_NN.png
    与 label/Character name 解耦：rename 不动磁盘目录。不做 rembg。"""
    root = (Path(project_dir) / "output" / "resources" / "compositions" / composition_id
            / "generations" / generation_id)
    root.mkdir(parents=True, exist_ok=True)
    out: List[str] = []
    for i, data in enumerate(images, start=1):
        name = f"candidate_{i:02d}.png"
        (root / name).write_bytes(data)
        out.append(f"output/resources/compositions/{composition_id}/generations/{generation_id}/{name}")
    return out
