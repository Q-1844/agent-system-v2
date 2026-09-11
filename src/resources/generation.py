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
from .models import CharacterLook, Clothing, ReferenceAsset, clothing_revision
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
    """项目相对 → 绝对；绝对原样。必须存在为文件，否则 None。"""
    if not local:
        return None
    p = Path(local)
    if not p.is_absolute():
        p = Path(project_dir) / p
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
                             view_purpose: str) -> dict:
    """从当前 canonical 状态构造一次生成的完整事实快照 + 语义指纹。
    纯函数式：不写盘、不调网络。同状态 + 同参数 → 同 basis（确定性）。"""
    project_dir = Path(project_dir)
    character = project.get_character(look.character_name) if project is not None else None
    char_refs = select_character_identity_refs(project_dir, look.character_name) \
        if character is not None else []
    clothing = doc.clothings.get(look.clothing_id) if look.clothing_id else None
    cloth_refs = select_clothing_refs(project_dir, doc, clothing) if clothing is not None else []

    prompt = build_look_candidate_prompt(
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
        "model": {"provider": "agnes", "model_id": model_id,
                  "size_tier": size_tier, "ratio": ratio},
        "prompt": prompt,
    }
    basis["basis_fingerprint"] = fingerprint(basis)
    return basis


def recompute_basis_still_holds(project_dir, doc, project, look_id: str,
                                basis: dict) -> Tuple[bool, str]:
    """§19/§20：生成结束后重算 basis，比对语义指纹。
    look 已删除 → (False, "look_missing")；指纹不同 → (False, "basis_changed")。
    绝不 runtime 偷偷替换 basis 后强行提交。"""
    look = doc.looks.get(look_id)
    if look is None:
        return False, "look_missing"
    current = compute_generation_basis(
        project_dir, doc, project, look,
        model_id=basis["model"]["model_id"],
        size_tier=basis["model"]["size_tier"],
        ratio=basis["model"]["ratio"],
        seed=basis["seed"],
        candidate_count=basis["candidate_count"],
        view_purpose=basis["view_purpose"],
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
