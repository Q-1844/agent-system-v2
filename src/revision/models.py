# src/revision/models.py
# AI-6A Project Revision Foundation —— 数据模型层。
# Creative Workspace / ProjectSourceRef / Semantic ChangeSet / Impact 的 dataclass 定义。
# 风格对齐 src/core/models.py（stdlib dataclass + to_dict/from_dict）。
#
# 设计不变量（§二）：修改 Workspace != 修改 canonical project。
#   canonical write -> 继续走现有业务 API；workspace write -> 必须经过 ChangeSet（§七）。
# 所有写盘走 src/core/atomic.atomic_write_json（§十四），禁止裸写 JSON。
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional, List, Dict
import hashlib
import json
import time


# ============ 枚举 ============
class SourceKind(str, Enum):
    CANONICAL = "canonical"
    WORKSPACE = "workspace"


class ChangeSetStatus(str, Enum):
    # 生命周期（§九）：draft -> previewed -> ready_to_apply -> applied | rejected | conflict
    DRAFT = "draft"
    PREVIEWED = "previewed"
    READY_TO_APPLY = "ready_to_apply"
    APPLIED = "applied"
    REJECTED = "rejected"
    CONFLICT = "conflict"


class Actor(str, Enum):
    USER = "user"
    SYSTEM = "system"
    AGENT = "agent"   # 预留：AI-6A 第一版不产生 actor=agent（§八）


class OpType(str, Enum):
    # 第一版 operations（§十）
    UPDATE_SCENE = "update_scene"
    UPDATE_SHOT = "update_shot"
    INSERT_SHOT = "insert_shot"
    DELETE_SHOT = "delete_shot"
    REORDER_SHOTS = "reorder_shots"
    # AI-7B 扩展（§52-§57）：Story / Scene / Character 语义操作
    UPDATE_STORY_TEXT = "update_story_text"
    INSERT_SCENE = "insert_scene"
    DELETE_SCENE = "delete_scene"
    REORDER_SCENES = "reorder_scenes"
    UPDATE_CHARACTER = "update_character"
    INSERT_CHARACTER = "insert_character"
    DELETE_CHARACTER = "delete_character"


class EntityType(str, Enum):
    SCENE = "scene"
    SHOT = "shot"
    CHARACTER = "character"


class ImpactReasonCode(str, Enum):
    # §十六：至少考虑这些 reason code
    SCENE_SUMMARY_CHANGED = "SCENE_SUMMARY_CHANGED"
    SCENE_CHARACTERS_CHANGED = "SCENE_CHARACTERS_CHANGED"
    SHOT_INTENT_CHANGED = "SHOT_INTENT_CHANGED"          # description 变
    SHOT_CHARACTERS_CHANGED = "SHOT_CHARACTERS_CHANGED"
    SHOT_DURATION_CHANGED = "SHOT_DURATION_CHANGED"
    SHOT_INSERTED = "SHOT_INSERTED"
    SHOT_DELETED = "SHOT_DELETED"
    SHOT_REORDERED = "SHOT_REORDERED"
    ADJACENCY_CHANGED = "ADJACENCY_CHANGED"
    CONTINUITY_CHAIN_CHANGED = "CONTINUITY_CHAIN_CHANGED"
    # AI-7B 扩展（§28）：Story / Scene / Character
    STORY_TEXT_CHANGED = "STORY_TEXT_CHANGED"
    SCENE_INSERTED = "SCENE_INSERTED"
    SCENE_DELETED = "SCENE_DELETED"
    SCENE_REORDERED = "SCENE_REORDERED"
    CHARACTER_ADDED = "CHARACTER_ADDED"
    CHARACTER_DESCRIPTION_CHANGED = "CHARACTER_DESCRIPTION_CHANGED"
    CHARACTER_REMOVED = "CHARACTER_REMOVED"


# ============ before / after ============
@dataclass
class BeforeAfter:
    """单个字段的新旧值。Apply 时用 before 重新核对（§十三），不能仅信 revision。"""
    before: Any = None
    after: Any = None


@dataclass
class NewShotSpec:
    """InsertShot 的新镜头规格（仅创作字段，不含 production，§十）。
    Preview 阶段分配最终稳定 shot_id；Apply 复用同一个（§十一重点难点）。"""
    shot_id: str = ""                 # Preview 分配的最终稳定 ID（workspace 计数器产出）
    description: str = ""
    characters: List[str] = field(default_factory=list)
    target_duration_s: Optional[float] = None


@dataclass
class NewSceneSpec:
    """AI-7B InsertScene 的新故事段规格（§53）。proposed_scene_id 由确定性分配器产出
    （复用 Project.next_scene_seq，§54；不回收、不重编号）。"""
    after_scene_id: Optional[str] = None   # 插在哪个现有段之后；None=加到末尾
    proposed_scene_id: str = ""
    summary: str = ""
    characters: List[str] = field(default_factory=list)


@dataclass
class NewCharacterSpec:
    """AI-7B InsertCharacter 的新角色规格（§56）。name 即身份（无 character_id）。"""
    name: str = ""
    description_en: str = ""


@dataclass
class ChangeOperation:
    """语义操作（不使用 raw JSON Patch，§八）。单 dataclass + op_type 判别。
    update_scene/update_shot 用 BeforeAfter 字段；insert/delete/reorder 用结构 payload。
    AI-7B（§52-§57）扩展：update_story_text / insert_scene / delete_scene / reorder_scenes /
    update_character / insert_character / delete_character。"""
    op_type: str                       # OpType 值
    scene_id: str = ""
    shot_id: Optional[str] = None
    # update_scene / update_shot 的 before/after（§十）
    summary: Optional[BeforeAfter] = None
    characters: Optional[BeforeAfter] = None
    description: Optional[BeforeAfter] = None
    target_duration_s: Optional[BeforeAfter] = None
    # insert_shot
    new_shot: Optional[NewShotSpec] = None
    # reorder_shots（§十二：用 ordered_shot_ids，不存 from_index/to_index）
    ordered_shot_ids: Optional[List[str]] = None
    # AI-7B 新字段
    story_text: Optional[BeforeAfter] = None          # update_story_text
    new_scene: Optional[NewSceneSpec] = None          # insert_scene
    ordered_scene_ids: Optional[List[str]] = None      # reorder_scenes
    character_name: Optional[str] = None             # update_character / delete_character
    description_en: Optional[BeforeAfter] = None      # update_character
    new_character: Optional[NewCharacterSpec] = None  # insert_character
    before: Optional[dict] = None                    # delete_scene / delete_character 快照

    def to_dict(self) -> dict:
        d: dict = {}
        for k in ("op_type", "scene_id", "shot_id", "ordered_shot_ids",
                  "ordered_scene_ids", "character_name", "before"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        for k in ("summary", "characters", "description", "target_duration_s",
                  "story_text", "description_en"):
            v = getattr(self, k)
            if v is not None:
                d[k] = {"before": v.before, "after": v.after}
        if self.new_shot is not None:
            d["new_shot"] = asdict(self.new_shot)
        if self.new_scene is not None:
            d["new_scene"] = asdict(self.new_scene)
        if self.new_character is not None:
            d["new_character"] = asdict(self.new_character)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ChangeOperation":
        d = dict(d or {})

        def _ba(key: str) -> Optional[BeforeAfter]:
            v = d.get(key)
            if isinstance(v, dict):
                return BeforeAfter(before=v.get("before"), after=v.get("after"))
            return None

        def _spec(key, spec_cls):
            v = d.get(key)
            return (spec_cls(**{k: val for k, val in v.items() if k in spec_cls.__dataclass_fields__})
                    if isinstance(v, dict) else None)

        osi = d.get("ordered_shot_ids")
        osci = d.get("ordered_scene_ids")
        return cls(
            op_type=d.get("op_type") or d.get("type") or "",
            scene_id=d.get("scene_id", ""),
            shot_id=d.get("shot_id"),
            summary=_ba("summary"),
            characters=_ba("characters"),
            description=_ba("description"),
            target_duration_s=_ba("target_duration_s"),
            new_shot=_spec("new_shot", NewShotSpec),
            ordered_shot_ids=list(osi) if osi is not None else None,
            story_text=_ba("story_text"),
            new_scene=_spec("new_scene", NewSceneSpec),
            ordered_scene_ids=list(osci) if osci is not None else None,
            character_name=d.get("character_name"),
            description_en=_ba("description_en"),
            new_character=_spec("new_character", NewCharacterSpec),
            before=d.get("before"),
        )


# ============ ProjectSourceRef（§六）============
@dataclass
class ProjectSourceRef:
    """稳定源引用。canonical 走现有业务 API；workspace 走 ChangeSet。
    ProjectSourceStore 第一版只统一 read，不统一 arbitrary write（§七）。"""
    project_name: str
    kind: str = SourceKind.CANONICAL.value
    workspace_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {"project_name": self.project_name, "kind": self.kind,
                "workspace_id": self.workspace_id}

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectSourceRef":
        d = d or {}
        return cls(project_name=d.get("project_name", ""),
                   kind=d.get("kind", SourceKind.CANONICAL.value),
                   workspace_id=d.get("workspace_id"))


# ============ ProjectWorkspace（§五）============
@dataclass
class ProjectWorkspace:
    """Creative Workspace。base_project.json 创建后永不修改（表示最初基于什么正式项目）；
    working_project.json 是 ChangeSet 作用对象。base_is_stale 动态判断不持久化（§五）。"""
    workspace_id: str
    project_name: str
    name: str = ""
    status: str = "active"             # active | archived
    revision: int = 0
    base_project_signature: str = ""
    working_project_signature: str = ""
    base_project_file: str = ""
    working_project_file: str = ""
    # per-scene shot-id 计数器（§十一 stable ID）——workspace 自带，不动 Scene 模型，
    # 创建时初始化为 max(已用 c 号)+1，只增不回收、不重编号。
    shot_seq: Dict[str, int] = field(default_factory=dict)   # {scene_id: next_c_num}
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    archived_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "project_name": self.project_name,
            "name": self.name,
            "status": self.status,
            "revision": self.revision,
            "base_project_signature": self.base_project_signature,
            "working_project_signature": self.working_project_signature,
            "base_project_file": self.base_project_file,
            "working_project_file": self.working_project_file,
            "shot_seq": dict(self.shot_seq),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived_at": self.archived_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectWorkspace":
        d = d or {}
        return cls(
            workspace_id=d.get("workspace_id", ""),
            project_name=d.get("project_name", ""),
            name=d.get("name", ""),
            status=d.get("status", "active"),
            revision=d.get("revision", 0),
            base_project_signature=d.get("base_project_signature", ""),
            working_project_signature=d.get("working_project_signature", ""),
            base_project_file=d.get("base_project_file", ""),
            working_project_file=d.get("working_project_file", ""),
            shot_seq=dict(d.get("shot_seq") or {}),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            archived_at=d.get("archived_at"),
        )


# ============ Impact（§十六）============
@dataclass
class EntityRef:
    """Impact 影响的实体引用。parent_id：shot -> scene_id。"""
    entity_type: str          # EntityType 值
    entity_id: str
    parent_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {"entity_type": self.entity_type, "entity_id": self.entity_id,
                "parent_id": self.parent_id}


@dataclass
class ImpactItem:
    """单条影响：实体 + 原因码 + 建议动作（不自动删除资产，§十七）。"""
    entity: EntityRef
    reason_code: str                # ImpactReasonCode 值
    recommended_action: str = ""    # e.g. "frame regeneration recommended"

    def to_dict(self) -> dict:
        return {"entity": self.entity.to_dict(), "reason_code": self.reason_code,
                "recommended_action": self.recommended_action}


@dataclass
class ImpactReport:
    """一次影响报告（deterministic code，不调 LLM，§三十一/§三十二/§八十二）。
    categorized id-lists（§三十二）+ per-item reason codes（§八十二）+ summary 计数（§三十三）。
    历史资产绝不自动删除（§三十四）。"""
    # categorized id-lists（§三十二）
    affected_scene_ids: List[str] = field(default_factory=list)
    affected_shot_ids: List[str] = field(default_factory=list)
    production_plan_scene_ids: List[str] = field(default_factory=list)
    prompt_rebuild_shot_ids: List[str] = field(default_factory=list)
    frame_regeneration_shot_ids: List[str] = field(default_factory=list)
    video_regeneration_shot_ids: List[str] = field(default_factory=list)
    continuity_review_shot_ids: List[str] = field(default_factory=list)
    review_recommended_scene_ids: List[str] = field(default_factory=list)
    # per-item reason codes（§八十二）——UI/Agent/debug 共用同一规则
    items: List[ImpactItem] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)   # §三十三 计数

    def to_dict(self) -> dict:
        return {
            "affected_scene_ids": list(self.affected_scene_ids),
            "affected_shot_ids": list(self.affected_shot_ids),
            "production_plan_scene_ids": list(self.production_plan_scene_ids),
            "prompt_rebuild_shot_ids": list(self.prompt_rebuild_shot_ids),
            "frame_regeneration_shot_ids": list(self.frame_regeneration_shot_ids),
            "video_regeneration_shot_ids": list(self.video_regeneration_shot_ids),
            "continuity_review_shot_ids": list(self.continuity_review_shot_ids),
            "review_recommended_scene_ids": list(self.review_recommended_scene_ids),
            "items": [i.to_dict() for i in self.items],
            "warnings": list(self.warnings),
            "summary": dict(self.summary),
        }


# ============ WorkspaceChangeSet（§八）============
@dataclass
class WorkspaceChangeSet:
    """Semantic ChangeSet。Preview 产生 candidate + before/after 签名；
    Apply 校验 revision + before + signature 后才写盘（§十三/§十四）。"""
    changeset_id: str
    workspace_id: str
    project_name: str
    title: str = ""
    intent: str = ""
    actor: str = Actor.USER.value
    base_workspace_revision: int = 0
    status: str = ChangeSetStatus.DRAFT.value
    operations: List[ChangeOperation] = field(default_factory=list)
    before_signature: str = ""
    after_signature: Optional[str] = None
    impact_report: Optional[dict] = None      # ImpactReport.to_dict() 或 None
    created_at: float = field(default_factory=time.time)
    applied_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "changeset_id": self.changeset_id,
            "workspace_id": self.workspace_id,
            "project_name": self.project_name,
            "title": self.title,
            "intent": self.intent,
            "actor": self.actor,
            "base_workspace_revision": self.base_workspace_revision,
            "status": self.status,
            "operations": [op.to_dict() for op in self.operations],
            "before_signature": self.before_signature,
            "after_signature": self.after_signature,
            "impact_report": self.impact_report,
            "created_at": self.created_at,
            "applied_at": self.applied_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WorkspaceChangeSet":
        d = d or {}
        return cls(
            changeset_id=d.get("changeset_id", ""),
            workspace_id=d.get("workspace_id", ""),
            project_name=d.get("project_name", ""),
            title=d.get("title", ""),
            intent=d.get("intent", ""),
            actor=d.get("actor", Actor.USER.value),
            base_workspace_revision=d.get("base_workspace_revision", 0),
            status=d.get("status", ChangeSetStatus.DRAFT.value),
            operations=[ChangeOperation.from_dict(o) for o in d.get("operations", [])],
            before_signature=d.get("before_signature", ""),
            after_signature=d.get("after_signature"),
            impact_report=d.get("impact_report"),
            created_at=d.get("created_at", time.time()),
            applied_at=d.get("applied_at"),
        )


# ============ 签名 ============
def _canonical_json(obj) -> str:
    """稳定序列化：sort_keys + ensure_ascii=False。default=str 兜底不可序列化对象。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def project_signature(data) -> str:
    """项目签名：sha256(canonical json)。
    data 接受 dict（序列化形态，推荐）或带 to_dict 的对象（Project/Scene/Shot）或 dataclass。
    用于 base_is_stale（§五）与 crash reconciliation（§十四）。
    建议传写盘用的 dict（Project.to_dict() 产出），保证 round-trip 一致。"""
    if hasattr(data, "to_dict"):
        data = data.to_dict()
    elif hasattr(data, "__dataclass_fields__"):
        data = asdict(data)
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def creative_source_signature(data) -> str:
    """Creative Source 签名（§六十/§六十一）：只覆盖创作结构，**排除生成资产 / updated_at / JobQueue**。
    含 story_text/style、characters(name/desc_en 身份)、scenes(scene_id/summary/characters)、
    shots(shot_id/description/characters/target_duration_s **+ 数组顺序**)。
    shot 顺序进入签名：A B C 与 A C B 是不同 Storyboard（§六十一）。
    base_is_stale 与 changeset before/after 签名必须用它，而非完整 JSON hash。

    AI-7C-H F-1：Character.views（front/side/back 生成资产）是**生产写回的派生态**，不属于
    创作语义身份——从签名中剔除。否则已批准执行的 character sync 合法写回会改变签名，
    重启 re-entry 误报 PROJECT_CHANGED_AFTER_APPROVAL / PRODUCTION_PREREQUISITE_FAILED。
    真实创作突变（如 description_en 变化）仍改变签名、仍触发 stale 保护。"""
    if hasattr(data, "to_dict"):
        data = data.to_dict()
    elif hasattr(data, "__dataclass_fields__"):
        data = asdict(data)
    style = data.get("style") or {}
    src = {
        "story_text": data.get("story_text", ""),
        "style": {k: style.get(k, "") for k in
                  ("art_style", "palette", "aspect_ratio", "size_tier", "era", "extra")},
        "characters": sorted(
            [{"name": c.get("name", ""), "description_en": c.get("description_en", "")}
             for c in (data.get("characters") or [])],
            key=lambda x: x["name"]),
        "scenes": [
            {"scene_id": s.get("scene_id", ""), "summary": s.get("summary", ""),
             "characters": list(s.get("characters") or []),
             "shots": [
                 {"shot_id": sh.get("shot_id", ""), "description": sh.get("description", ""),
                  "characters": list(sh.get("characters") or []),
                  "target_duration_s": sh.get("target_duration_s")}
                 for sh in (s.get("shots") or [])   # 数组顺序保留（§六十一）
             ]}
            for s in (data.get("scenes") or [])   # scene 顺序保留
        ],
    }
    return hashlib.sha256(_canonical_json(src).encode("utf-8")).hexdigest()
