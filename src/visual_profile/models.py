# src/visual_profile/models.py
# Project Visual Profile V1 — Phase P1 §3/§4/§8：typed canonical 模型 + 语义指纹。
#
# 🔴 render_family enum（P1 §4）：photorealistic / 2d_animation / 3d_animation /
#    stylized / custom 五值。不用 realistic/animation 二值（2D 与 3D 必须区分）；
#    也不再扩成几十种 style enum——具体风格进 art_direction / style_tags。
#
# 🔴 语义指纹（P1 §8）：visual_profile_fingerprint = sha256(canonical_json(
#    语义子集))[:8]，只覆盖 render_family / art_direction / style_tags /
#    negative_style_constraints，**不含时间戳**（同 OCC sha256[:8] 指纹族）。
#    revision 即该指纹：update expected_revision stale → reject，不 silent retry。
#
# 🔴 确定性：style_tags / negative_style_constraints 保留用户给定顺序（去首尾空白、
#    丢空项）；指纹基于 canonical_json（sort_keys），同语义 → 同指纹。
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

RENDER_FAMILIES = ("photorealistic", "2d_animation", "3d_animation",
                   "stylized", "custom")
VISUAL_PROFILE_SCHEMA_VERSION = 1

# read 模型 style 兼容语义（P1 §13/§14）
STYLE_COMPATIBILITY_UNKNOWN = "unknown"
STYLE_COMPATIBILITY_CURRENT = "current"
STYLE_COMPATIBILITY_CHANGED = "project_visual_profile_changed"


class VisualProfileSchemaError(Exception):
    """project_profile.json 坏 JSON / 字段缺失 / schema 未知 → 明确错误，绝不静默重置。"""


def _clean_list(items: Any) -> List[str]:
    if items is None:
        return []
    if not isinstance(items, list):
        raise VisualProfileSchemaError("style_tags / negative_style_constraints 必须是列表")
    out: List[str] = []
    for it in items:
        s = str(it or "").strip()
        if s:
            out.append(s)
    return out


@dataclass
class ProjectVisualProfile:
    """项目级视觉定义 canonical（P1 §3）。不含 provider/model/seed 等模型实现字段。"""
    render_family: str
    art_direction: str = ""
    style_tags: List[str] = field(default_factory=list)
    negative_style_constraints: List[str] = field(default_factory=list)
    schema_version: int = VISUAL_PROFILE_SCHEMA_VERSION
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.render_family not in RENDER_FAMILIES:
            raise VisualProfileSchemaError(
                f"render_family 必须是 {RENDER_FAMILIES} 之一，收到: {self.render_family!r}")
        self.art_direction = str(self.art_direction or "").strip()
        self.style_tags = _clean_list(self.style_tags)
        self.negative_style_constraints = _clean_list(self.negative_style_constraints)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "render_family": self.render_family,
            "art_direction": self.art_direction,
            "style_tags": list(self.style_tags),
            "negative_style_constraints": list(self.negative_style_constraints),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def profile_snapshot(profile: ProjectVisualProfile) -> dict:
    """语义子集快照（进 GenerationBasis / 指纹；不含时间戳与 schema_version）。"""
    return {
        "render_family": profile.render_family,
        "art_direction": profile.art_direction,
        "style_tags": list(profile.style_tags),
        "negative_style_constraints": list(profile.negative_style_constraints),
    }


def visual_profile_fingerprint(profile: ProjectVisualProfile) -> str:
    """确定性语义指纹（P1 §8）：sha256(canonical_json(snapshot))[:8]。不含时间戳。"""
    payload = json.dumps(profile_snapshot(profile), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def profile_revision(profile: ProjectVisualProfile) -> str:
    """OCC revision = 语义指纹（同族；时间戳无关）。"""
    return visual_profile_fingerprint(profile)


def parse_visual_profile(raw: dict) -> ProjectVisualProfile:
    """从落盘 dict 解析。schema 未知 / render_family 缺失或非法 → fail closed。"""
    if not isinstance(raw, dict):
        raise VisualProfileSchemaError("project_profile.json 根必须是对象")
    v = raw.get("schema_version")
    if v != VISUAL_PROFILE_SCHEMA_VERSION:
        raise VisualProfileSchemaError(
            f"project_profile.json schema_version={v!r} 不受支持（当前 "
            f"{VISUAL_PROFILE_SCHEMA_VERSION}）——fail closed，不静默迁移")
    if "render_family" not in raw:
        raise VisualProfileSchemaError("project_profile.json 缺少 render_family（fail closed）")
    created = raw.get("created_at")
    updated = raw.get("updated_at")
    return ProjectVisualProfile(
        render_family=str(raw.get("render_family") or ""),
        art_direction=str(raw.get("art_direction") or ""),
        style_tags=list(raw.get("style_tags") or []),
        negative_style_constraints=list(raw.get("negative_style_constraints") or []),
        schema_version=VISUAL_PROFILE_SCHEMA_VERSION,
        created_at=float(created) if isinstance(created, (int, float)) else time.time(),
        updated_at=float(updated) if isinstance(updated, (int, float)) else time.time(),
    )


# ============ read 模型 style 兼容语义（P1 §13/§14）============
def style_compatibility_of_basis(basis: Optional[dict],
                                 current_fingerprint: Optional[str]) -> dict:
    """generation record basis → style 兼容结论（computed，绝不落盘、绝不 mutation）。

    - basis 缺 visual_profile_fingerprint（本阶段前的 legacy generation）：
      style_compatibility=unknown + generated_before_visual_profile=True——
      绝不伪造"它符合当前 Style"（P1 §14）。
    - 指纹一致 → current；不一致（含 current_fingerprint=None 但 basis 有指纹）→
      project_visual_profile_changed。
    """
    stored = (basis or {}).get("visual_profile_fingerprint")
    if not stored:
        return {"style_compatibility": STYLE_COMPATIBILITY_UNKNOWN,
                "generated_before_visual_profile": True,
                "visual_profile_fingerprint": None}
    if current_fingerprint is not None and stored == current_fingerprint:
        return {"style_compatibility": STYLE_COMPATIBILITY_CURRENT,
                "generated_before_visual_profile": False,
                "visual_profile_fingerprint": stored}
    return {"style_compatibility": STYLE_COMPATIBILITY_CHANGED,
            "generated_before_visual_profile": False,
            "visual_profile_fingerprint": stored}
