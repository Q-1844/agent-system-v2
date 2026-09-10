# src/resources/models.py
# Production Resources V1 — Phase 1B typed domain models.
#
# 独立 canonical companion：resources.json（projects/{project}/output/resources.json）。
# 🔴 不修改 src/core/models.py::Asset。legacy Asset 逐字段、逐字节行为不变。
# 🔴 不修改 Project dataclass / project.json schema / schema_version=2 / migrate_v1_to_v2。
#
# 风格对齐 src/core/models.py（stdlib dataclass + to_dict/from_dict + known-field 过滤前向兼容）。
# revision 指纹风格对齐现有 Agent OCC token（sha256[:8]，如 _shot_token/_scenes_token）。
#
# 设计原则（Phase 1A 冻结 + 本轮修正 A/B）：
#   - 稳定 ID keyed map（look_/clothing_/prop_/ref_<uuid12>），ID 与 display name 解耦。
#   - Character 例外：继续 character_name FK（现有 Character 无 character_id，本阶段不改）。
#   - generation_status 与 approval_status 分两个维度（不混入单状态机）。
#   - Look/Clothing/Prop 只存 asset_id 引用，ReferenceAsset 全文档只存一份。
#   - scope 是资源属性（不是资源类型）；origin/lineage 字段就位（Phase 1D Shared Library 用）。
#   - 不存 base64；不靠路径命名推断身份。
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, List, Dict, Any
import hashlib
import json
import time
import uuid


# ============ 异常 ============
class ResourceSchemaError(Exception):
    """resources.json schema 不受支持 / 损坏。fail closed，不静默重置。"""


# ============ 枚举 ============
class ResourceScope(str, Enum):
    """资源所属域。Phase 1B 全部 project；Phase 1D 接 Shared Library 时出现 shared。"""
    PROJECT = "project"
    SHARED = "shared"


class ResourceSource(str, Enum):
    """资源/资产来源 provenance（origin.source 字段值）。
    Phase 1B 全 local；Phase 1D 出现 shared_library/imported。"""
    LOCAL = "local"
    SHARED_LIBRARY = "shared_library"
    IMPORTED = "imported"


class ApprovalState(str, Enum):
    """审批状态（独立于生成状态）。"""
    UNREVIEWED = "unreviewed"
    APPROVED = "approved"
    REJECTED = "rejected"


class GenerationStatus(str, Enum):
    """生成状态（独立于审批状态）。
    none=未生成；pending=已请求待生成；generating=生成中；ready=候选就绪；failed=生成失败。"""
    NONE = "none"
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


# ============ ID 生成（对齐 job_id 的 uuid4().hex[:12] 惯例）============
def new_reference_asset_id() -> str:
    return f"ref_{uuid.uuid4().hex[:12]}"


def new_look_id() -> str:
    return f"look_{uuid.uuid4().hex[:12]}"


def new_clothing_id() -> str:
    return f"clothing_{uuid.uuid4().hex[:12]}"


def new_prop_id() -> str:
    return f"prop_{uuid.uuid4().hex[:12]}"


# ============ ResourceOrigin / Lineage ============
@dataclass
class ResourceOrigin:
    """资源来源 lineage。Phase 1B 字段就位、默认 LOCAL（source=local，无 shared 指针）。
    Phase 1D Shared Library import 时填 shared_resource_id/shared_version/imported_at。
    项目内修改不写回 Shared Library（隔离由 ResourceStore scope + copy-on-import 保证）。"""
    source: str = ResourceSource.LOCAL.value
    shared_resource_id: Optional[str] = None
    shared_version: Optional[int] = None
    imported_at: Optional[str] = None   # ISO 字符串（Phase 1D）

    def to_dict(self) -> dict:
        return {"source": self.source, "shared_resource_id": self.shared_resource_id,
                "shared_version": self.shared_version, "imported_at": self.imported_at}

    @classmethod
    def from_dict(cls, d: Any) -> Optional["ResourceOrigin"]:
        # None 保持 None（round-trip 一致：origin=None 序列化为 null，反序列化仍 None）。
        if d is None:
            return None
        if not isinstance(d, dict):
            return None
        return cls(source=d.get("source", ResourceSource.LOCAL.value),
                   shared_resource_id=d.get("shared_resource_id"),
                   shared_version=d.get("shared_version"),
                   imported_at=d.get("imported_at"))


# ============ ReferenceAsset ============
@dataclass
class ReferenceAsset:
    """统一视觉引用对象。Production Resources 域 typed object（不复用/不修改 legacy Asset）。
    identity = asset_id；local/url 只是数据，不靠路径推断身份。
    Phase 1B 不存 base64、不存 generation receipt（最小化）。
    checksum 字段就位（Phase 1D content-addressed dedupe 用；Phase 1B 可空）。"""
    asset_id: str
    scope: str = ResourceScope.PROJECT.value
    media_type: str = "image"                 # Phase 1B 仅 image
    local: Optional[str] = None                # 本地路径
    url: Optional[str] = None                  # 公网 URL
    checksum: Optional[str] = None             # sha256 hex（content-addressed，Phase 1D）
    view: str = ""                             # front/side/back/hero/3-4/...（free string, typed str）
    purpose: str = ""                          # identity/look/turnaround/...
    background_type: str = ""                  # white/transparent/...
    source: str = "manual"                    # 生成 provenance：manual/agnes/legacy/...
    origin: Optional[ResourceOrigin] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["origin"] = self.origin.to_dict() if self.origin else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ReferenceAsset":
        d = dict(d or {})
        origin = ResourceOrigin.from_dict(d.get("origin"))
        known = {f for f in cls.__dataclass_fields__} - {"origin"}
        kw = {k: v for k, v in d.items() if k in known}
        return cls(origin=origin, **kw)


# ============ 公共关系摘要（service list 用，轻量）============
def _relationship_summary(reference_asset_ids: List[str],
                          approved_ids: List[str]) -> str:
    n = len(reference_asset_ids)
    na = len(approved_ids)
    return f"{n} ref(s), {na} approved"


# ============ CharacterLook ============
@dataclass
class CharacterLook:
    """某个 Character 在某套稳定服装/外观状态下的完整视觉形态。
    identity = look_id（独立稳定，不靠 character_name+clothing_id 临时组合）。
    character_name 是 FK → Character.name（name-based，本阶段不改 Character）。
    只存 asset_id 引用，ReferenceAsset 在文档级只存一份。"""
    look_id: str
    character_name: str                       # FK → Character.name
    scope: str = ResourceScope.PROJECT.value
    label: str = ""                           # 显示名（rename label 不变 look_id）
    clothing_id: Optional[str] = None         # FK → Clothing.clothing_id
    appearance_description: str = ""
    appearance_delta: str = ""                # 相对 base/character 的外观差异
    reference_asset_ids: List[str] = field(default_factory=list)
    approved_reference_asset_ids: List[str] = field(default_factory=list)
    cover_asset_id: Optional[str] = None
    generation_status: str = GenerationStatus.NONE.value
    approval_status: str = ApprovalState.UNREVIEWED.value
    origin: Optional[ResourceOrigin] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["origin"] = self.origin.to_dict() if self.origin else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CharacterLook":
        d = dict(d or {})
        origin = ResourceOrigin.from_dict(d.get("origin"))
        known = {f for f in cls.__dataclass_fields__} - {"origin"}
        kw = {k: v for k, v in d.items() if k in known}
        return cls(origin=origin, **kw)


# ============ Clothing ============
@dataclass
class Clothing:
    """独立、可复用服装设计资源。identity = clothing_id。"""
    clothing_id: str
    scope: str = ResourceScope.PROJECT.value
    name: str = ""                            # 显示名（rename 不变 id）
    description: str = ""
    design_traits: str = ""
    materials: List[str] = field(default_factory=list)
    colors: List[str] = field(default_factory=list)
    reference_asset_ids: List[str] = field(default_factory=list)
    approved_reference_asset_ids: List[str] = field(default_factory=list)
    cover_asset_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    approval_status: str = ApprovalState.UNREVIEWED.value
    origin: Optional[ResourceOrigin] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["origin"] = self.origin.to_dict() if self.origin else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Clothing":
        d = dict(d or {})
        origin = ResourceOrigin.from_dict(d.get("origin"))
        known = {f for f in cls.__dataclass_fields__} - {"origin"}
        kw = {k: v for k, v in d.items() if k in known}
        return cls(origin=origin, **kw)


# ============ Prop ============
@dataclass
class Prop:
    """独立 typed 道具资源。identity = prop_id。Phase 1B 最小。
    预留扩展（不实现、不封死）：holder/damage timeline/variant graph/continuity engine。"""
    prop_id: str
    scope: str = ResourceScope.PROJECT.value
    name: str = ""
    description: str = ""
    reference_asset_ids: List[str] = field(default_factory=list)
    approved_reference_asset_ids: List[str] = field(default_factory=list)
    cover_asset_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    approval_status: str = ApprovalState.UNREVIEWED.value
    origin: Optional[ResourceOrigin] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["origin"] = self.origin.to_dict() if self.origin else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Prop":
        d = dict(d or {})
        origin = ResourceOrigin.from_dict(d.get("origin"))
        known = {f for f in cls.__dataclass_fields__} - {"origin"}
        kw = {k: v for k, v in d.items() if k in known}
        return cls(origin=origin, **kw)


# ============ ResourceDocument（canonical 顶层容器）============
RESOURCES_SCHEMA_VERSION = 1


@dataclass
class ResourceDocument:
    """resources.json canonical 文档。
    稳定 ID keyed map（不依赖 name 作身份）。
    schema_version 受支持范围外 → fail closed（ResourceSchemaError），绝不静默猜测/重置。"""
    schema_version: int = RESOURCES_SCHEMA_VERSION
    looks: Dict[str, CharacterLook] = field(default_factory=dict)
    clothings: Dict[str, Clothing] = field(default_factory=dict)
    props: Dict[str, Prop] = field(default_factory=dict)
    reference_assets: Dict[str, ReferenceAsset] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "looks": {k: v.to_dict() for k, v in self.looks.items()},
            "clothings": {k: v.to_dict() for k, v in self.clothings.items()},
            "props": {k: v.to_dict() for k, v in self.props.items()},
            "reference_assets": {k: v.to_dict() for k, v in self.reference_assets.items()},
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "ResourceDocument":
        raw = dict(raw or {})
        sv = raw.get("schema_version", RESOURCES_SCHEMA_VERSION)
        if sv != RESOURCES_SCHEMA_VERSION:
            raise ResourceSchemaError(
                f"unsupported resources.json schema_version: {sv} (expected {RESOURCES_SCHEMA_VERSION})")
        return cls(
            schema_version=sv,
            looks={k: CharacterLook.from_dict(v) for k, v in (raw.get("looks") or {}).items()},
            clothings={k: Clothing.from_dict(v) for k, v in (raw.get("clothings") or {}).items()},
            props={k: Prop.from_dict(v) for k, v in (raw.get("props") or {}).items()},
            reference_assets={k: ReferenceAsset.from_dict(v) for k, v in (raw.get("reference_assets") or {}).items()},
        )


# ============ deterministic content fingerprint（OCC revision 语义）============
# 风格对齐现有 Agent OCC token：sha256[:8]（_shot_token/_scenes_token 同口径）。
# service read 返回 revision，但 canonical 内不持久化此 derived revision。
# 注意：与 Phase 1D Shared Library 的 shared_version 是不同概念，不混淆。
def _canonical_json(obj) -> str:
    """稳定序列化：sort_keys + ensure_ascii=False。default=str 兜底。对齐 revision/models.py。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def document_revision(doc: ResourceDocument) -> str:
    """整个 resources document 的内容指纹（sha256[:8]）。"""
    return hashlib.sha256(_canonical_json(doc.to_dict()).encode("utf-8")).hexdigest()[:8]


def look_revision(look: CharacterLook) -> str:
    return hashlib.sha256(_canonical_json(look.to_dict()).encode("utf-8")).hexdigest()[:8]


def clothing_revision(c: Clothing) -> str:
    return hashlib.sha256(_canonical_json(c.to_dict()).encode("utf-8")).hexdigest()[:8]


def prop_revision(p: Prop) -> str:
    return hashlib.sha256(_canonical_json(p.to_dict()).encode("utf-8")).hexdigest()[:8]


def reference_asset_revision(a: ReferenceAsset) -> str:
    return hashlib.sha256(_canonical_json(a.to_dict()).encode("utf-8")).hexdigest()[:8]
