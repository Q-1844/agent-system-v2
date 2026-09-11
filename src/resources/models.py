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


# ============ CompositionPropBinding（Phase 1C5）============
class CompositionRelation(str, Enum):
    """角色与道具的视觉关系（有限 enum，不做万能 free JSON——执行文档 §4）。
    语义 = 这张 approved composition plate 上道具应该呈现的视觉关系，
    不是剧情 ownership / 不是时间线。"""
    HELD = "held"
    CARRIED = "carried"
    WORN = "worn"
    ATTACHED = "attached"
    NEAR = "near"


class CompositionAnchor(str, Enum):
    """道具在画面上相对角色的视觉锚点（视觉关系，不是世界坐标、不是剧情 ownership）。"""
    RIGHT_HAND = "right_hand"
    LEFT_HAND = "left_hand"
    BACK = "back"
    WAIST = "waist"
    NECK = "neck"
    BESIDE_CHARACTER = "beside_character"


class RequiredVisibility(str, Enum):
    """该道具在 composition 中是否必须清晰可见（review 维度 required_prop_visibility 的依据）。"""
    REQUIRED = "required"
    OPTIONAL = "optional"


@dataclass
class CompositionPropBinding:
    """一条 角色↔道具 视觉绑定。只描述这张 approved composition 应该长什么样。
    绝不表示道具的持有时间线（那属于未来 Scene/Shot Continuity System，禁止入 Resources）。"""
    prop_id: str                                 # FK → Prop.prop_id
    relation: str = CompositionRelation.HELD.value
    anchor: str = CompositionAnchor.RIGHT_HAND.value
    visual_state_note: str = ""                  # 如 "sword sheathed" / "compass open"
    required_visibility: str = RequiredVisibility.REQUIRED.value

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Any) -> "CompositionPropBinding":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ============ CharacterReferenceComposition（Phase 1C5）============
def new_composition_id() -> str:
    return f"comp_{uuid.uuid4().hex[:12]}"


@dataclass
class CharacterReferenceComposition:
    """为后续生成一致性而提前批准的视觉组合参考（approved visual composition plate）：
    Character + Approved CharacterLook + 1~3 个 Prop + 明确的视觉关系。

    🔴 语义边界（执行文档 §3）：它是 Reference（生产参考图版），**不是**：
    剧情 / Shot 状态 / Prop ownership canonical / Scene continuity / 角色永久持有关系。
    "Alice 右手拿木剑"只意味着这张 approved plate 里她右手拿木剑。"""
    composition_id: str
    scope: str = ResourceScope.PROJECT.value
    label: str = ""                              # 显示名（rename 不变 composition_id）
    character_name: str = ""                     # FK → Character.name（身份，不可改）
    look_id: str = ""                            # FK → CharacterLook.look_id（须 approved）
    prop_bindings: List[CompositionPropBinding] = field(default_factory=list)
    pose_note: str = ""
    composition_description: str = ""
    reference_asset_ids: List[str] = field(default_factory=list)
    approved_reference_asset_ids: List[str] = field(default_factory=list)
    cover_asset_id: Optional[str] = None
    generation_status: str = GenerationStatus.NONE.value
    approval_status: str = ApprovalState.UNREVIEWED.value
    origin: Optional[ResourceOrigin] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)   # asdict 递归展开 prop_bindings 为 list[dict]
        d["origin"] = self.origin.to_dict() if self.origin else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CharacterReferenceComposition":
        d = dict(d or {})
        origin = ResourceOrigin.from_dict(d.get("origin"))
        bindings_raw = d.get("prop_bindings") or []
        known = {f for f in cls.__dataclass_fields__} - {"origin", "prop_bindings"}
        kw = {k: v for k, v in d.items() if k in known}
        return cls(origin=origin,
                   prop_bindings=[CompositionPropBinding.from_dict(b)
                                  for b in bindings_raw if isinstance(b, dict)],
                   **kw)


# ============ ResourceDocument（canonical 顶层容器）============
# schema v2（Phase 1C5）：新增顶层 canonical collection `compositions`。
# 惰性迁移契约（执行文档 §5）：
#   - v1 文件可读：in-memory compositions={}，且 in-memory schema_version 规范化为 2；
#   - 纯 read 绝不写盘（ResourceStore.load 从不 save）→ 不存在"读时自动写回 v2"；
#   - 第一次真实 mutation save 时才把 v2 持久化到磁盘（幂等、非破坏性，旧字段原样保留）；
#   - 未知 future schema（≥3）继续 fail closed（ResourceSchemaError）。
# 为什么 bump 而不是 v1 additive：v1 文件若带 compositions key，不认识该 key 的旧 runtime
#   会 load→save 静默丢弃 compositions（from_dict known-key 白名单外全部忽略）；
#   v2 让旧 runtime fail closed 大声报错而非静默丢数据——这是唯一安全方向。
RESOURCES_SCHEMA_VERSION = 2
_SUPPORTED_RESOURCE_SCHEMA_VERSIONS = (1, 2)


@dataclass
class ResourceDocument:
    """resources.json canonical 文档。
    稳定 ID keyed map（不依赖 name 作身份）。
    schema_version：v1（无 compositions）与 v2（有 compositions）均可读（惰性迁移）；
    in-memory 一律规范化为 v2；受支持范围外 → fail closed（ResourceSchemaError），
    绝不静默猜测/重置。"""
    schema_version: int = RESOURCES_SCHEMA_VERSION
    looks: Dict[str, CharacterLook] = field(default_factory=dict)
    clothings: Dict[str, Clothing] = field(default_factory=dict)
    props: Dict[str, Prop] = field(default_factory=dict)
    compositions: Dict[str, CharacterReferenceComposition] = field(default_factory=dict)
    reference_assets: Dict[str, ReferenceAsset] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "looks": {k: v.to_dict() for k, v in self.looks.items()},
            "clothings": {k: v.to_dict() for k, v in self.clothings.items()},
            "props": {k: v.to_dict() for k, v in self.props.items()},
            "compositions": {k: v.to_dict() for k, v in self.compositions.items()},
            "reference_assets": {k: v.to_dict() for k, v in self.reference_assets.items()},
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "ResourceDocument":
        raw = dict(raw or {})
        sv = raw.get("schema_version", RESOURCES_SCHEMA_VERSION)
        if sv not in _SUPPORTED_RESOURCE_SCHEMA_VERSIONS:
            raise ResourceSchemaError(
                f"unsupported resources.json schema_version: {sv} "
                f"(supported: {list(_SUPPORTED_RESOURCE_SCHEMA_VERSIONS)})")
        # v1 → in-memory 规范化为 v2（compositions 缺省 {}）；纯读路径不写盘，
        # 只有真实 mutation 的 save 才会把 v2 持久化（§5 惰性迁移契约）。
        return cls(
            schema_version=RESOURCES_SCHEMA_VERSION,
            looks={k: CharacterLook.from_dict(v) for k, v in (raw.get("looks") or {}).items()},
            clothings={k: Clothing.from_dict(v) for k, v in (raw.get("clothings") or {}).items()},
            props={k: Prop.from_dict(v) for k, v in (raw.get("props") or {}).items()},
            compositions={k: CharacterReferenceComposition.from_dict(v)
                          for k, v in (raw.get("compositions") or {}).items()},
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


def composition_revision(c: CharacterReferenceComposition) -> str:
    return hashlib.sha256(_canonical_json(c.to_dict()).encode("utf-8")).hexdigest()[:8]


def reference_asset_revision(a: ReferenceAsset) -> str:
    return hashlib.sha256(_canonical_json(a.to_dict()).encode("utf-8")).hexdigest()[:8]
