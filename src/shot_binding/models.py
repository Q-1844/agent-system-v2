# src/shot_binding/models.py
# Shot Production Binding V1 — Phase S3B.1 typed domain models。
#
# 独立 canonical companion：projects/{project}/output/shot_production_bindings.json。
# 设计基线 = SHOT_BINDING_V1_AUDIT_AND_DESIGN.md（S3A 报告 §7.2 schema v1）。
#
# 🔴 不修改 src/core/models.py（Project/Scene/Shot）/ project.json schema（S3A §7.1 裁决）。
# 🔴 binding 只存语义 ID（look_id / prop_id / composition_id / scene_instance_id /
#    view_id / sciv_id）——**绝不存 image_path / image_url / reference_image**
#    （S3A §5 结论：raw image path 不是 creative truth）。图片由 S3B.3 的
#    ShotReferencePack 在解析时从 Resources / Scene System 读取。
# 🔴 relation / anchor 词表复用冻结的 Resources CompositionRelation / CompositionAnchor
#    （src/resources/models.py）——不创建第二套关系词表（7.md S3B.1 §六）。
# 🔴 dependency_fingerprints 是 bind 时刻的**历史快照**（写一次；当前性由 S3B.2
#    read 模型重算对比；唯一更新方式=S3B.2 的显式 refresh）——不是缓存，绝不自动更新。
# 🔴 不在本阶段实现：SIV 自动解析 / validity / stale 检测 / 指纹重算 / refresh /
#    pack（S3B.2/S3B.3 范围）。
#
# 风格对齐 src/resources/models.py 与 src/scene_system/models.py
# （stdlib dataclass + to_dict/from_dict + known-field 过滤前向兼容 +
#  sha256[:8] deterministic fingerprint 族 + schema fail closed）。
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional
import hashlib
import json
import time


# ============ 异常 ============
class ShotBindingSchemaError(Exception):
    """shot_production_bindings.json 不受支持 / 损坏。fail closed，不静默重置。"""


# ============ 枚举 ============
SHOT_BINDING_SCHEMA_VERSION = 1


class BindingActionKind(str, Enum):
    """Shot 内动作的最小 V1 词表（S3A §13 / 7.md S3B.1 §九）。
    "拿起"不是 Resource；prop_id 指向 Resource；起止状态是本镜 temporal 语义——
    只进 prompt 指令（S3C），绝不回写 Look / Prop / Composition / SceneState。"""
    NONE = "none"
    STATIC = "static"
    PICK_UP = "pick_up"
    PUT_DOWN = "put_down"
    HAND_OFF = "hand_off"
    CUSTOM = "custom"


# 需要 prop_id / subject_character 的动作 kind（static/none/custom 不强制）
ACTION_KINDS_REQUIRING_PROP = ("pick_up", "put_down", "hand_off")


# ============ SceneBinding（S3A §7.2/§10）============
@dataclass
class SceneBinding:
    """一镜的空间绑定：哪个 SceneInstance + 本镜机位方向（LocationView）。
    pinned_scene_instance_view_id 为 None 时，approved SIV 的解析属于 S3B.2/S3B.3
    read 模型——S3B.1 不做自动解析（7.md S3B.1 §八）。"""
    scene_instance_id: str
    location_view_id: str
    pinned_scene_instance_view_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Any) -> "SceneBinding":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ============ CharacterBinding（S3A §8）============
@dataclass
class CharacterBinding:
    """一镜中某角色的语义绑定：Character(name FK) × Look（必填）× Composition（可选）。
    穿什么由 Look 承载（Case A/C）；人物+道具关系优先由 approved Composition plate
    承载（Case B/D）——binding 绝不复制组合的视觉真相（S3A §12）。"""
    character_name: str
    look_id: str
    composition_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Any) -> "CharacterBinding":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ============ PropBinding（S3A §9）============
@dataclass
class PropBinding:
    """未被 composition 覆盖的道具在一镜中的视觉关系声明（shot 级 temporal）。
    relation/anchor 词表 = Resources CompositionRelation / CompositionAnchor
    （模型存 string；合法值由 service 在 bind 时对 Resources enum 校验——
    与仓库惯例一致：model 层不做 enum 强校验，fail closed 收口在 service）。"""
    prop_id: str
    relation: str = "held"
    anchor: str = "right_hand"
    visual_state_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Any) -> "PropBinding":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ============ BindingAction（S3A §13 最小 V1）============
@dataclass
class BindingAction:
    """Shot 内动作意图（Case D 动作镜）。start_state / end_state 是语义文本，
    只供 S3B.3 pack 编译进 prompt 指令——绝不回写任何 Resource。
    本阶段明确不做：Timeline / ActionGraph / 动画状态机 / 多动作序列。"""
    kind: str
    subject_character: str = ""
    prop_id: str = ""
    start_state: str = ""
    end_state: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Any) -> "BindingAction":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ============ DialogueSpeakerBinding（Native AV B1：17.md §6 最小可选块）============
@dataclass
class DialogueSpeakerLine:
    """「第 index 句 → 哪个角色」（0-based，index 按非空行序计）。
    🔴 只表达句→角色映射——绝不复制台词正文（台词唯一真相=Shot.dialogue，
    17.md §5/§6）。speaker 合法性（∈ Shot.characters）由解析侧 fail-closed 校验。"""
    index: int
    speaker_character_name: str

    def to_dict(self) -> dict:
        return {"index": self.index,
                "speaker_character_name": self.speaker_character_name}

    @classmethod
    def from_dict(cls, d: Any) -> "DialogueSpeakerLine":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class DialogueSpeakerBinding:
    """多角色 Shot 的显式台词说话人绑定（可选；B1 17.md §6）。
    lines 缺行 = 该句未显式裁决（解析侧回落内联前缀/单角色 auto，无法唯一确定
    则 fail closed——绝不猜）。resolution（"single_auto" | "explicit"）是 advisory
    记录；实际解析结论由 src/generation/dialogue_control.py 派生。"""
    lines: List[DialogueSpeakerLine] = field(default_factory=list)
    resolution: str = ""

    def to_dict(self) -> dict:
        return {"lines": [ln.to_dict() for ln in self.lines],
                "resolution": self.resolution}

    @classmethod
    def from_dict(cls, d: Any) -> "DialogueSpeakerBinding":
        d = dict(d or {})
        return cls(
            lines=[DialogueSpeakerLine.from_dict(ln)
                   for ln in (d.get("lines") or []) if isinstance(ln, dict)],
            resolution=str(d.get("resolution") or ""))


# ============ DependencyFingerprints（S3A §19）============
@dataclass
class DependencyFingerprints:
    """bind 时刻的依赖指纹快照（历史事实，写一次）。
    - visual_profile：ProjectVisualProfile 语义指纹；unspecified → None（诚实缺省）。
    - characters：character_identity_fingerprint（name+description_en；views 不属于
      创作身份——复用 src/resources/generation.py 同名函数）。
    - looks / props / compositions：look_revision / prop_revision /
      composition_revision（resources sha256[:8] 指纹族）。
    - scene_instance：scene_instance_revision（含 SceneState）。
    - scene_instance_view：pinned SIV 的 approved_basis_fingerprint；未 pin → None
      （解析属 S3B.2/S3B.3，本阶段不实现）。
    🔴 指纹只覆盖 binding 实际引用的实体——"无关资源变更不 stale"由此结构保证
      （S3A §9/§19）。composition 内部的 look/prop 不重复追踪（已批准 plate 是
      Resources 域 provenance；避免过度绑定，S3A §7.2）。"""
    visual_profile: Optional[str] = None
    characters: Dict[str, str] = field(default_factory=dict)
    looks: Dict[str, str] = field(default_factory=dict)
    props: Dict[str, str] = field(default_factory=dict)
    compositions: Dict[str, str] = field(default_factory=dict)
    scene_instance: Optional[str] = None
    scene_instance_view: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Any) -> "DependencyFingerprints":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ============ ShotProductionBinding ============
@dataclass
class ShotProductionBinding:
    """一个 Shot 的生产绑定（一镜至多一个；bindings 以 shot_id 为 key）。
    Shot 本身保持创作语义（project.json 零改动）；本实体保存生产绑定。
    identity = shot_id（不新造 ID 空间——绑定依附于 Shot 的身份证号）。"""
    shot_id: str
    scene_id: str = ""                       # bind 时与 Shot.scene_id 比对（防漂移冗余）
    scene_binding: Optional[SceneBinding] = None
    character_bindings: List[CharacterBinding] = field(default_factory=list)
    prop_bindings: List[PropBinding] = field(default_factory=list)
    action: Optional[BindingAction] = None
    dialogue_speaker_binding: Optional[DialogueSpeakerBinding] = None   # B1 可选块（17.md §6）
    production_notes: str = ""
    dependency_fingerprints: DependencyFingerprints = field(default_factory=DependencyFingerprints)
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "shot_id": self.shot_id,
            "scene_id": self.scene_id,
            "scene_binding": self.scene_binding.to_dict() if self.scene_binding else None,
            "character_bindings": [cb.to_dict() for cb in self.character_bindings],
            "prop_bindings": [pb.to_dict() for pb in self.prop_bindings],
            "action": self.action.to_dict() if self.action else None,
            "dialogue_speaker_binding": (
                self.dialogue_speaker_binding.to_dict()
                if self.dialogue_speaker_binding else None),
            "production_notes": self.production_notes,
            "dependency_fingerprints": self.dependency_fingerprints.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ShotProductionBinding":
        d = dict(d or {})
        sb = d.get("scene_binding")
        action = d.get("action")
        dsb = d.get("dialogue_speaker_binding")
        fps = d.get("dependency_fingerprints")
        # "audio_event_intent" 保留在排除集：C1-R 撤回（21.md §20）前落库的
        # binding 可能带此键——读取时静默忽略（legacy 容忍），不再恢复。
        known = {f for f in cls.__dataclass_fields__} - {
            "scene_binding", "character_bindings", "prop_bindings", "action",
            "dialogue_speaker_binding", "audio_event_intent",
            "dependency_fingerprints"}
        kw = {k: v for k, v in d.items() if k in known}
        return cls(
            scene_binding=SceneBinding.from_dict(sb) if isinstance(sb, dict) else None,
            character_bindings=[CharacterBinding.from_dict(cb)
                                for cb in (d.get("character_bindings") or [])
                                if isinstance(cb, dict)],
            prop_bindings=[PropBinding.from_dict(pb)
                           for pb in (d.get("prop_bindings") or [])
                           if isinstance(pb, dict)],
            action=BindingAction.from_dict(action) if isinstance(action, dict) else None,
            dialogue_speaker_binding=(
                DialogueSpeakerBinding.from_dict(dsb)
                if isinstance(dsb, dict) else None),
            dependency_fingerprints=DependencyFingerprints.from_dict(fps),
            **kw)


# ============ ShotBindingDocument（canonical 顶层容器）============
@dataclass
class ShotBindingDocument:
    """shot_production_bindings.json canonical 文档。shot_id keyed map。
    受支持范围外 schema_version → fail closed（ShotBindingSchemaError），
    绝不静默猜测/重置（对齐 resources/scene_system/project_profile 三 precedent）。"""
    schema_version: int = SHOT_BINDING_SCHEMA_VERSION
    bindings: Dict[str, ShotProductionBinding] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "bindings": {k: v.to_dict() for k, v in self.bindings.items()},
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "ShotBindingDocument":
        raw = dict(raw or {})
        sv = raw.get("schema_version", SHOT_BINDING_SCHEMA_VERSION)
        if sv != SHOT_BINDING_SCHEMA_VERSION:
            raise ShotBindingSchemaError(
                f"unsupported shot_production_bindings.json schema_version: {sv} "
                f"(supported: {SHOT_BINDING_SCHEMA_VERSION})——fail closed，"
                f"不自动降级/重置/静默覆盖")
        return cls(
            schema_version=SHOT_BINDING_SCHEMA_VERSION,
            bindings={k: ShotProductionBinding.from_dict(v)
                      for k, v in (raw.get("bindings") or {}).items()},
        )


# ============ deterministic content fingerprint（OCC revision 语义）============
# 风格对齐 resources/scene_system：sha256[:8]；canonical 内不持久化 derived revision。
def _canonical_json(obj: Any) -> str:
    """稳定序列化：sort_keys + ensure_ascii=False。default=str 兜底。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def _rev(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()[:8]


def document_revision(doc: ShotBindingDocument) -> str:
    """整个 bindings document 的内容指纹（sha256[:8]）。"""
    return _rev(doc.to_dict())


def binding_revision(binding: ShotProductionBinding) -> str:
    """单条 binding 的内容指纹（per-binding OCC token；含时间戳——
    任何落盘变化都会翻动 revision，与 resources/scene_system 同口径）。"""
    return _rev(binding.to_dict())
