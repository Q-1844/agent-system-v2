# src/scene_system/models.py
# Scene System V1 — Phase S1 typed domain models.
#
# 独立 canonical companion：projects/{project}/output/scene_system.json。
# 🔴 不修改 src/core/models.py（Project/Scene/Shot）/ project.json schema。
# 🔴 不复制 Prop canonical——Prop identity 属于冻结的 Production Resources V1；
#    SceneState.prop_placements 只引用 prop_id（"木剑现在在哪里"），
#    Resources 回答"木剑是什么"。
#
# 风格对齐 src/resources/models.py（stdlib dataclass + to_dict/from_dict +
# known-field 过滤前向兼容 + sha256[:8] deterministic fingerprint 族）。
#
# ============ Phase S1 现有 Scene 审计结论（无 BLOCKER）============
# 1. 现有 Story Scene（src/core/models.py::Scene）：scene_id="s01" 是身份证号
#    （next_scene_seq 分配、删除不回收、永不改名）；location/time_of_day 是
#    创作层自由文本字段（Script Writer story.update 可写；Sub-Agent 段状态头
#    展示；prompt_builder 不消费——环境表达全靠 Shot 自由文本字段）。
# 2. 本系统的 Location 是生产空间事实实体，与 Scene.location 字符串不同层：
#    不替换旧字段、不迁移、不覆盖（同 Resources V1 对 Character 的处理方式）。
# 3. SceneInstance.story_scene_id 单向引用现有 Scene；旧 Scene 被删 → read
#    返回 missing_story_scene 标注（不静默忽略）；创建时校验存在。
#
# ============ 设计决定 ============
#   - Location 只放长期稳定空间身份（圆形控制室/北侧入口/西墙三扇窗）。
#     剧情时刻状态（今晚暴雨/灯坏了/窗户破了）属于 SceneState（§11）。
#   - Location 不持久化 anchor_ids/view_ids/layout_id：单真相源——anchors/
#     views/layouts 各自带 location_id FK，read 层派生，杜绝双向同步失真。
#     location revision 因此只反映 Location 自身内容（rename anchor 不动它）。
#   - AnchorRelation 内嵌在 anchor.relations[]（subject 隐含=所在 anchor），
#     三元组语义完整：subject=anchor_id, relation, object=object_anchor_id。
#   - SceneState 嵌入 SceneInstance（该 instance 的当前状态，唯一 state 真相源，
#     不建第二个竞争 state store）。
#   - location_revision_at_bind 是"绑定时刻的历史快照"（写一次不再更新），
#     不是当前 revision 的缓存派生值（遵守 §17 精神：不持久化会失效的缓存）。
#   - View Bank 原则（§9）：Minimum Coverage + On-demand Expansion。
#     系统不假设固定 8 方向；多少个 view 由使用者显式创建。
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, List, Dict, Any
import hashlib
import json
import time
import uuid


# ============ 异常 ============
class SceneSystemSchemaError(Exception):
    """scene_system.json schema 不受支持 / 损坏。fail closed，不静默重置。"""


# ============ 枚举 ============
class AnchorKind(str, Enum):
    """稳定空间对象/区域的初版限定枚举（§5）。不做 3D 坐标。"""
    DOOR = "door"
    WINDOW = "window"
    STAIRS = "stairs"
    FURNITURE = "furniture"
    LANDMARK = "landmark"
    ZONE = "zone"
    ENTRY = "entry"
    EXIT = "exit"
    OTHER = "other"


class ApprovalState(str, Enum):
    """审批状态（语义沿用 Resources）。S2A 起 view approval 走真实 approve/revoke 协议：
    生成成功 ≠ approved；review pass ≠ 自动 approved（显式 mutation 才算）。"""
    UNREVIEWED = "unreviewed"
    APPROVED = "approved"
    REJECTED = "rejected"


class GenerationStatus(str, Enum):
    """生成状态（独立于审批状态——两个维度绝不合并，S2A §8）。
    语义对齐 Resources：none=未生成；pending=已请求待处理；generating=生成中；
    ready=候选就绪（≠ approved）；failed=生成失败。"""
    NONE = "none"
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class SceneInstanceStatus(str, Enum):
    """SceneInstance 生命周期。V1 创建即 active；archived/retired 字段就位，
    为未来"同 Story Scene 多 instance（多 location / sequence）"留接口——
    V1 规则：同一 story_scene_id 至多一个 active instance（§10）。"""
    ACTIVE = "active"
    ARCHIVED = "archived"
    RETIRED = "retired"


# ============ ID 生成（对齐 Resources 的 <prefix>_<uuid12> 惯例）============
def new_location_id() -> str:
    return f"loc_{uuid.uuid4().hex[:12]}"


def new_anchor_id() -> str:
    return f"anc_{uuid.uuid4().hex[:12]}"


def new_layout_id() -> str:
    return f"layout_{uuid.uuid4().hex[:12]}"


def new_view_id() -> str:
    return f"view_{uuid.uuid4().hex[:12]}"


def new_scene_instance_id() -> str:
    return f"scin_{uuid.uuid4().hex[:12]}"


# ============ Anchor Relation（§6 轻量语义空间图）============
# 允许的 relation 词表（V1 有限集；不做物理引擎 / scene graph solver）。
ANCHOR_RELATIONS = {
    "north_of", "south_of", "east_of", "west_of",
    "left_of", "right_of", "adjacent_to", "opposite",
    "inside", "connected_to", "near",
}


@dataclass
class AnchorRelation:
    """一条有向空间关系：subject（所在 anchor 隐含）--relation--> object。
    relation ∈ ANCHOR_RELATIONS；object 必须是同一 location 内的 anchor。"""
    relation: str
    object_anchor_id: str
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Any) -> "AnchorRelation":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ============ SpatialAnchor（§5）============
@dataclass
class SpatialAnchor:
    """一个稳定空间对象/区域（门/窗/楼梯/家具/地标/区域）。identity = anchor_id。"""
    anchor_id: str
    location_id: str                          # FK → Location.location_id
    name: str = ""
    kind: str = AnchorKind.OTHER.value
    description: str = ""
    semantic_zone: str = ""                   # 如 north_wall / center / south_entrance
    relations: List[AnchorRelation] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SpatialAnchor":
        d = dict(d or {})
        relations_raw = d.get("relations") or []
        known = {f for f in cls.__dataclass_fields__} - {"relations"}
        kw = {k: v for k, v in d.items() if k in known}
        return cls(relations=[AnchorRelation.from_dict(r)
                              for r in relations_raw if isinstance(r, dict)],
                   **kw)


# ============ AnchorPlacement / SetLayout（§7）============
@dataclass
class AnchorPlacement:
    """layout 里一个 anchor 的粗略平面位置。
    x/y 是 normalized 0~1 layout hint（只帮 Agent/continuity 理解相对方位，
    🔴 绝不解释为真实物理厘米 / 3D transform）。zone 为语义区域名。"""
    anchor_id: str
    zone: str = ""
    x: Optional[float] = None
    y: Optional[float] = None
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Any) -> "AnchorPlacement":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class SetLayout:
    """给系统理解空间的结构化平面信息（不是漂亮背景图）。
    一个 Location 至多一个 layout（1:0..1）；layout_id 独立稳定。"""
    layout_id: str
    location_id: str                          # FK → Location.location_id
    orientation_convention: str = ""          # 如 "north_up" / "screen_north_up"
    notes: str = ""
    placements: List[AnchorPlacement] = field(default_factory=list)
    reference_asset_id: Optional[str] = None  # FK → Resources ReferenceAsset（optional）
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SetLayout":
        d = dict(d or {})
        placements_raw = d.get("placements") or []
        known = {f for f in cls.__dataclass_fields__} - {"placements"}
        kw = {k: v for k, v in d.items() if k in known}
        return cls(placements=[AnchorPlacement.from_dict(p)
                               for p in placements_raw if isinstance(p, dict)],
                   **kw)


# ============ LocationView（§8；S2A 起含视觉生成状态）============
@dataclass
class LocationView:
    """Location 的一个观察视角定义（不用 Character 式 front/side/back）。
    🔴 LocationView ≠ ReferenceAsset（S2A §3）：本实体是"从哪里往哪看、应看到哪些
    稳定结构"的空间视角定义；批准的视觉图是它指向的 Resources ReferenceAsset。
    S2A 视觉状态（schema v2 新增字段——v1 runtime 会静默丢弃它们，故 bump v2）：
    - generation_status：生成状态维度（none/pending/generating/ready/failed）
    - active_generation_id：当前候选所属 generation（commit 时写入；指向
      output/scene_system/generation_records.jsonl 中该次生成的记录）
    - reference_asset_id / approval_status：S1 已有；approval 是显式协议结果。
    生成成功 ≠ approved；review pass ≠ 自动 approved（§8/§25）。"""
    view_id: str
    location_id: str                          # FK → Location.location_id
    label: str = ""                           # 如 "South-west to North-east"
    camera_zone: str = ""                     # 如 "south_west"
    look_direction: str = ""                  # 如 "north_east"
    visible_anchor_ids: List[str] = field(default_factory=list)
    reference_asset_id: Optional[str] = None
    approval_status: str = ApprovalState.UNREVIEWED.value
    generation_status: str = GenerationStatus.NONE.value
    active_generation_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LocationView":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ============ Location（§4）============
@dataclass
class Location:
    """这个地点是谁（长期稳定空间身份）：圆形控制室/北侧入口/西墙三扇窗。
    🔴 不放剧情时刻状态（今晚暴雨/灯坏了/窗户破了/Alice 在桌边）——那属于 SceneState。
    anchor_ids/view_ids/layout_id 不持久化（单真相源派生，见文件头设计决定）。"""
    location_id: str
    name: str = ""                            # 显示名（rename 不变 location_id）
    description: str = ""
    identity_description: str = ""            # 稳定空间身份描述（进未来 prompt 的语义基底）
    style_notes: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Location":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ============ SceneState（§11）============
@dataclass
class AnchorState:
    """一个 anchor 在本 Scene 中的剧情结果状态（跨 Shot 持续成立的结果，
    不是某 Shot 内的动作过程——§12 边界）。condition 自由语义串（broken/open/…）。"""
    condition: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Any) -> "AnchorState":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class PropPlacement:
    """一个 Prop 在本 Scene 中的位置与当前状态。
    prop_id 只引用（不复制）Resources Prop canonical（§14）：
    Resources 回答"木剑是什么"，这里回答"木剑现在在哪里、什么状态"。"""
    anchor_id: str = ""                       # FK → 该 location 的 SpatialAnchor
    state: str = ""                           # 如 intact / broken / missing
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Any) -> "PropPlacement":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class SceneState:
    """这个地点在当前 Story Scene 中的剧情状态（生产时刻结构化事实）。
    与现有 Scene.time_of_day（创作层自由文本）分属两层，互不覆盖（§16）。
    保存跨 Shot 需要持续成立的结果状态；Shot 内动作过程属于 Shot/Video（§12）。
    未来 typed CharacterPlacement（§13）作为新字段加入，本版不加 characters 万能 JSON。"""
    time_of_day: str = ""
    weather: str = ""
    lighting: str = ""
    environment_conditions: str = ""          # weather 之外的环境条件（风/雪/烟…）
    anchor_states: Dict[str, AnchorState] = field(default_factory=dict)
    prop_placements: Dict[str, PropPlacement] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "time_of_day": self.time_of_day,
            "weather": self.weather,
            "lighting": self.lighting,
            "environment_conditions": self.environment_conditions,
            "anchor_states": {k: v.to_dict() for k, v in self.anchor_states.items()},
            "prop_placements": {k: v.to_dict() for k, v in self.prop_placements.items()},
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SceneState":
        d = dict(d or {})
        anchor_states_raw = d.get("anchor_states") or {}
        prop_placements_raw = d.get("prop_placements") or {}
        known = {f for f in cls.__dataclass_fields__} - {"anchor_states", "prop_placements"}
        kw = {k: v for k, v in d.items() if k in known}
        return cls(
            anchor_states={k: AnchorState.from_dict(v) for k, v in anchor_states_raw.items()
                           if isinstance(v, dict)},
            prop_placements={k: PropPlacement.from_dict(v) for k, v in prop_placements_raw.items()
                             if isinstance(v, dict)},
            **kw)


# ============ SceneInstance（§10）============
@dataclass
class SceneInstance:
    """某个 existing Story Scene 在某个 Location 中发生时的生产空间状态实例。
    story_scene_id → project.json scenes[].scene_id（创建时校验存在；旧 Scene 被删
    后 read 标注 missing_story_scene，不静默忽略）。
    V1 规则：同一 story_scene_id 至多一个 active instance（schema 不封死未来多 instance）。"""
    scene_instance_id: str
    story_scene_id: str                       # FK → 现有 Story Scene.scene_id（不修改旧 schema）
    location_id: str                          # FK → Location.location_id
    location_revision_at_bind: str = ""       # 绑定时刻 location revision 快照（历史事实，写一次不更新）
    status: str = SceneInstanceStatus.ACTIVE.value
    state: SceneState = field(default_factory=SceneState)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SceneInstance":
        d = dict(d or {})
        state = SceneState.from_dict(d.get("state") or {})
        known = {f for f in cls.__dataclass_fields__} - {"state"}
        kw = {k: v for k, v in d.items() if k in known}
        return cls(state=state, **kw)


# ============ SceneInstanceView（S2B §3）============
def new_scene_instance_view_id() -> str:
    return f"sciv_{uuid.uuid4().hex[:12]}"


@dataclass
class SceneInstanceView:
    """某个 SceneInstance 在某个 LocationView 方向上的已生成视觉状态图（身份实体）。
    🔴 身份规则（S2B §4）：一个 (scene_instance_id, location_view_id) 对至多一个本实体；
    重新生成 = 更新本对象的 generation/reference 指针（历史 records 保留），绝不新建身份。
    🔴 不重复保存 camera_zone/look_direction/visible_anchor_ids（属 LocationView，
    经 location_view_id 读取）；不复制 SceneState（唯一 canonical = SceneInstance.state；
    ViewRelevantSceneState 只是每次重算的 derived projection，绝不持久化第二份）。
    approved_basis_fingerprint：approve 时记录的语义基准指纹——read 层 stale 检测
    （is_stale/stale_reasons）用它对比当前重算值（S2B §32；derived，不自动 mutation）。"""
    scene_instance_view_id: str
    scene_instance_id: str                    # FK → SceneInstance.scene_instance_id
    location_view_id: str                     # FK → LocationView.view_id（base 视觉母版）
    label: str = ""
    generation_status: str = GenerationStatus.NONE.value
    active_generation_id: Optional[str] = None
    reference_asset_id: Optional[str] = None
    approval_status: str = ApprovalState.UNREVIEWED.value
    approved_basis_fingerprint: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SceneInstanceView":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ============ SceneSystemDocument（canonical 顶层容器，§15）============
# schema v2（Phase S2A）：LocationView 新增视觉生成状态字段（generation_status /
# active_generation_id）。
# 惰性迁移契约（同 Resources 1C5 已证明有效的原则，S2A §7）：
#   - v1 文件可读：in-memory 新字段取默认值，schema_version 规范化为 2；
#   - 纯 read 绝不写盘（load 从不 save）→ 不存在"读时自动写回 v2"；
#   - 第一次真实 mutation save 时才把 v2 持久化到磁盘（幂等、非破坏性）；
#   - 旧 S1 runtime（只支持 v1）面对 v2 文件 fail closed（SceneSystemSchemaError）
#     而非静默丢字段——为什么必须 bump 而不是 v1 additive：v1 文件若带新字段，
#     不认识该字段的旧 runtime load→save 会静默丢弃 generation_status/
#     active_generation_id（from_dict known-key 白名单外全部忽略），
#     让旧 runtime 大声报错是唯一安全方向；
#   - 未知未来版本（≥3）继续 fail closed。
SCENE_SYSTEM_SCHEMA_VERSION = 3
_SUPPORTED_SCENE_SYSTEM_SCHEMA_VERSIONS = (1, 2, 3)
# schema v3（Phase S2B）：新增顶层 canonical collection `scene_instance_views`。
# bump 理由（同 v1→v2）：v2 文件若带 scene_instance_views key，不认识该 collection 的
# 旧 v2 runtime load→save 会静默丢弃它（known-key 白名单外全部忽略）——
# 让旧 runtime fail closed 大声报错是唯一安全方向。惰性迁移契约不变：
#   v2 可读（in-memory scene_instance_views={}，规范化为 v3）；纯 read 零写回；
#   第一次真实 S2B mutation 才落盘 v3；旧 v2 runtime 面对 v3 fail closed；≥4 fail closed。


@dataclass
class SceneSystemDocument:
    """scene_system.json canonical 文档。稳定 ID keyed map（不依赖 name 作身份）。
    受支持范围外 schema_version → fail closed（SceneSystemSchemaError），
    绝不静默猜测/重置（防空间事实数据静默丢失）。"""
    schema_version: int = SCENE_SYSTEM_SCHEMA_VERSION
    locations: Dict[str, Location] = field(default_factory=dict)
    anchors: Dict[str, SpatialAnchor] = field(default_factory=dict)
    layouts: Dict[str, SetLayout] = field(default_factory=dict)
    views: Dict[str, LocationView] = field(default_factory=dict)
    scene_instances: Dict[str, SceneInstance] = field(default_factory=dict)
    scene_instance_views: Dict[str, SceneInstanceView] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "locations": {k: v.to_dict() for k, v in self.locations.items()},
            "anchors": {k: v.to_dict() for k, v in self.anchors.items()},
            "layouts": {k: v.to_dict() for k, v in self.layouts.items()},
            "views": {k: v.to_dict() for k, v in self.views.items()},
            "scene_instances": {k: v.to_dict() for k, v in self.scene_instances.items()},
            "scene_instance_views": {k: v.to_dict() for k, v in self.scene_instance_views.items()},
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "SceneSystemDocument":
        raw = dict(raw or {})
        sv = raw.get("schema_version", SCENE_SYSTEM_SCHEMA_VERSION)
        if sv not in _SUPPORTED_SCENE_SYSTEM_SCHEMA_VERSIONS:
            raise SceneSystemSchemaError(
                f"unsupported scene_system.json schema_version: {sv} "
                f"(supported: {list(_SUPPORTED_SCENE_SYSTEM_SCHEMA_VERSIONS)})")
        return cls(
            schema_version=SCENE_SYSTEM_SCHEMA_VERSION,
            locations={k: Location.from_dict(v) for k, v in (raw.get("locations") or {}).items()},
            anchors={k: SpatialAnchor.from_dict(v) for k, v in (raw.get("anchors") or {}).items()},
            layouts={k: SetLayout.from_dict(v) for k, v in (raw.get("layouts") or {}).items()},
            views={k: LocationView.from_dict(v) for k, v in (raw.get("views") or {}).items()},
            scene_instances={k: SceneInstance.from_dict(v)
                             for k, v in (raw.get("scene_instances") or {}).items()},
            scene_instance_views={k: SceneInstanceView.from_dict(v)
                                  for k, v in (raw.get("scene_instance_views") or {}).items()},
        )


# ============ deterministic content fingerprint（OCC revision 语义，§17）============
# 风格对齐 src/resources/models.py 与现有 Agent OCC token：sha256[:8]。
# service read 返回 revision，但 canonical 内不持久化任何 derived revision。
# deterministic：同一磁盘内容 → 同一 revision（T17）。
def _canonical_json(obj) -> str:
    """稳定序列化：sort_keys + ensure_ascii=False。default=str 兜底。对齐 revision/models.py。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def _rev(obj) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()[:8]


def document_revision(doc: SceneSystemDocument) -> str:
    """整个 scene_system document 的内容指纹（sha256[:8]）。"""
    return _rev(doc.to_dict())


def location_revision(location: Location) -> str:
    return _rev(location.to_dict())


def anchor_revision(anchor: SpatialAnchor) -> str:
    return _rev(anchor.to_dict())


def layout_revision(layout: SetLayout) -> str:
    return _rev(layout.to_dict())


def view_revision(view: LocationView) -> str:
    return _rev(view.to_dict())


def scene_instance_revision(instance: SceneInstance) -> str:
    return _rev(instance.to_dict())


def scene_instance_view_revision(view: "SceneInstanceView") -> str:
    return _rev(view.to_dict())
