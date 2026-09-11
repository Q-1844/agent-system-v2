# src/resources/shared_library.py
# Production Resources V1 — Phase 1C3：Shared Production Library 最小闭环。
#
# 🔴 独立 shared canonical：`shared_library/resources.json`（仓库根，与 saved_characters/ 平级；
#    saved_characters/ 一个字节不动，兼容入口保留）。
#    形状复用 ResourceDocument 思路 + characters 段 + meta 段（version/origin/asset_map）。
# 🔴 隔离由构造保证（Phase 1A §8.3）：copy-on-publish + copy-on-import——
#    Publish 把项目媒体**复制**进 shared_library/media/；Import 把共享媒体**复制**进项目。
#    项目只持自己的 snapshot，**Project A 的修改绝不污染 Project B / Shared**。
# 🔴 version 语义：shared 条目版本号（显式 re-publish 递增）；origin 记录来源项目/原 id。
#    不做 content-addressed storage（本轮明确不做）；media 以 shared asset id 命名，
#    republish 时同映射刷新内容（checksum 随之更新）。
# 🔴 并发：shared 文档写走 project_write_lock("__shared_library__")（进程内全局串行化）；
#    项目侧导入走该项目自己的 project_write_lock + document revision OCC。
from __future__ import annotations

import hashlib
import shutil
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.store import ProjectStore
from src.core.write_lock import project_write_lock

from .store import ResourceStore

from .models import (
    ApprovalState, CharacterLook, Clothing, Prop, ReferenceAsset,
    ResourceDocument, ResourceOrigin, ResourceSchemaError,
    document_revision,
    new_clothing_id, new_look_id, new_prop_id, new_reference_asset_id,
)

SHARED_LOCK_KEY = "__shared_library__"
SHARED_SCHEMA_VERSION = 1
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _new_schar_id() -> str:
    return f"schar_{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


# ============ SharedCharacter（共享角色条目）============
@dataclass
class SharedCharacter:
    """共享库中的角色母版。view_asset_ids: {view: shared ref asset_id}。"""
    schar_id: str
    name: str
    description_en: str = ""
    scope: str = "shared"
    view_asset_ids: Dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SharedCharacter":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ============ SharedEntryMeta（version / origin / asset 映射）============
@dataclass
class SharedEntryMeta:
    """共享条目的版本与来源元数据（与资源本体分离，不改 typed models 形状）。"""
    resource_id: str
    kind: str                                   # character|look|clothing|prop
    version: int = 1
    origin_project: str = ""
    original_id: str = ""                       # 项目内原 id；character 为原 name
    published_at: float = field(default_factory=time.time)
    asset_map: Dict[str, str] = field(default_factory=dict)   # 项目 asset_id → shared asset_id
    character_snapshot: Optional[dict] = None   # look 发布时的角色身份快照（展示/追溯用）
    history: List[dict] = field(default_factory=list)         # [{version, published_at}]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SharedEntryMeta":
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ============ SharedDocument ============
@dataclass
class SharedDocument:
    schema_version: int = SHARED_SCHEMA_VERSION
    characters: Dict[str, SharedCharacter] = field(default_factory=dict)
    looks: Dict[str, CharacterLook] = field(default_factory=dict)
    clothings: Dict[str, Clothing] = field(default_factory=dict)
    props: Dict[str, Prop] = field(default_factory=dict)
    reference_assets: Dict[str, ReferenceAsset] = field(default_factory=dict)
    meta: Dict[str, SharedEntryMeta] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "characters": {k: v.to_dict() for k, v in self.characters.items()},
            "looks": {k: v.to_dict() for k, v in self.looks.items()},
            "clothings": {k: v.to_dict() for k, v in self.clothings.items()},
            "props": {k: v.to_dict() for k, v in self.props.items()},
            "reference_assets": {k: v.to_dict() for k, v in self.reference_assets.items()},
            "meta": {k: v.to_dict() for k, v in self.meta.items()},
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "SharedDocument":
        raw = dict(raw or {})
        sv = raw.get("schema_version", SHARED_SCHEMA_VERSION)
        if sv != SHARED_SCHEMA_VERSION:
            raise ResourceSchemaError(f"unsupported shared schema_version: {sv}")
        return cls(
            schema_version=sv,
            characters={k: SharedCharacter.from_dict(v) for k, v in (raw.get("characters") or {}).items()},
            looks={k: CharacterLook.from_dict(v) for k, v in (raw.get("looks") or {}).items()},
            clothings={k: Clothing.from_dict(v) for k, v in (raw.get("clothings") or {}).items()},
            props={k: Prop.from_dict(v) for k, v in (raw.get("props") or {}).items()},
            reference_assets={k: ReferenceAsset.from_dict(v) for k, v in (raw.get("reference_assets") or {}).items()},
            meta={k: SharedEntryMeta.from_dict(v) for k, v in (raw.get("meta") or {}).items()},
        )


# ============ SharedStore（sync，对齐 ResourceStore）============
def default_shared_dir() -> Path:
    """仓库根（animation-storyboard/）下的 shared_library/。"""
    return Path(__file__).resolve().parents[2] / "shared_library"


class SharedStore:
    def __init__(self, shared_dir=None):
        self.dir = Path(shared_dir) if shared_dir else default_shared_dir()
        self.path = self.dir / "resources.json"
        self.media_dir = self.dir / "media"

    def write_lock(self):
        return project_write_lock(SHARED_LOCK_KEY)

    def load(self) -> SharedDocument:
        if not self.path.exists():
            return SharedDocument()
        try:
            raw = json_loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            raise ResourceSchemaError(f"shared_library/resources.json 损坏（不自动重置）：{e}") from e
        if not isinstance(raw, dict):
            raise ResourceSchemaError("shared_library/resources.json 顶层不是 JSON object")
        return SharedDocument.from_dict(raw)

    def save(self, doc: SharedDocument) -> None:
        from src.core.atomic import atomic_write_json
        self.dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, doc.to_dict())

    # ---- media ----
    def copy_media_in(self, source_file: Path, shared_asset_id: str) -> ReferenceAsset:
        """把项目媒体复制进 shared media（按 shared asset id 命名），返回 shared ReferenceAsset。"""
        source_file = Path(source_file)
        suffix = source_file.suffix.lower() or ".png"
        if suffix not in _IMAGE_SUFFIXES:
            raise ValueError(f"不支持的媒体扩展名: {suffix}")
        self.media_dir.mkdir(parents=True, exist_ok=True)
        dst = self.media_dir / f"{shared_asset_id}{suffix}"
        shutil.copyfile(source_file, dst)
        data = dst.read_bytes()
        rel = f"shared_library/media/{dst.name}"
        return ReferenceAsset(asset_id=shared_asset_id, scope="shared",
                              local=rel, url=None,
                              checksum=hashlib.sha256(data).hexdigest(),
                              source="published", origin=None)


    # ---- list / read（只读，无项目依赖）----
    def list_shared(self, kind: Optional[str] = None) -> List[dict]:
        sdoc = self.load()
        out: List[dict] = []

        def _meta(rid):
            m = sdoc.meta.get(rid)
            if not m:
                return {}
            return {"version": m.version, "origin_project": m.origin_project,
                    "character_snapshot": m.character_snapshot}

        for rid, c in sdoc.characters.items():
            if kind in (None, "all", "character"):
                out.append({"id": rid, "type": "character", "name": c.name,
                            "description_en": c.description_en,
                            "views": sorted(c.view_asset_ids.keys()), **_meta(rid)})
        for rid, c in sdoc.clothings.items():
            if kind in (None, "all", "clothing"):
                out.append({"id": rid, "type": "clothing", "name": c.name, **_meta(rid)})
        for rid, p in sdoc.props.items():
            if kind in (None, "all", "prop"):
                out.append({"id": rid, "type": "prop", "name": p.name, **_meta(rid)})
        for rid, l in sdoc.looks.items():
            if kind in (None, "all", "look"):
                m = sdoc.meta.get(rid)
                out.append({"id": rid, "type": "look", "name": l.label,
                            "character": (m.character_snapshot if m else None),
                            "version": (m.version if m else None),
                            "origin_project": (m.origin_project if m else None)})
        return out

    def read_shared(self, shared_id: str) -> Optional[dict]:
        sdoc = self.load()
        base = kind = None
        if shared_id in sdoc.characters:
            base, kind = sdoc.characters[shared_id].to_dict(), "character"
        elif shared_id in sdoc.looks:
            base, kind = sdoc.looks[shared_id].to_dict(), "look"
        elif shared_id in sdoc.clothings:
            base, kind = sdoc.clothings[shared_id].to_dict(), "clothing"
        elif shared_id in sdoc.props:
            base, kind = sdoc.props[shared_id].to_dict(), "prop"
        elif shared_id in sdoc.reference_assets:
            base, kind = sdoc.reference_assets[shared_id].to_dict(), "reference_asset"
        if base is None:
            return None
        m = sdoc.meta.get(shared_id)
        refs = base.get("reference_asset_ids") or []
        out = {**base, "kind": kind,
               "version": (m.version if m else None),
               "origin_project": (m.origin_project if m else None),
               "original_id": (m.original_id if m else None),
               "character_snapshot": (m.character_snapshot if m else None),
               "reference_assets": [
                   {**sdoc.reference_assets[r].to_dict(), "missing": False}
                   if r in sdoc.reference_assets else {"asset_id": r, "missing": True}
                   for r in refs]}
        if kind == "character":
            out["view_assets"] = {
                v: (sdoc.reference_assets[r].to_dict() if r in sdoc.reference_assets
                    else {"asset_id": r, "missing": True})
                for v, r in (base.get("view_asset_ids") or {}).items()}
        return out

    def read_media_bytes(self, rel_path: str) -> Optional[bytes]:
        p = self.dir.parent / rel_path   # rel 形如 shared_library/media/x.png（相对仓库根）
        if not p.is_file():
            p = self.dir / rel_path      # 兼容直接相对 shared_dir 的写法
        if not p.is_file():
            return None
        return p.read_bytes()


def json_loads(text: str):
    import json
    return json.loads(text)


# ============ SharedLibraryService ============
class SharedLibraryService:
    """Publish（project→shared）与 Import（shared→project snapshot）。

    隔离不变量：
      - publish 只**读**项目 canonical，只**写** shared_library（项目零改动）；
      - import 只**写**项目 canonical（copy-on-import，新 id + origin），shared 零改动；
      - 任何后续项目内修改都只发生在项目自己的 snapshot 上。"""

    def __init__(self, project_dir, project_name: Optional[str] = None, shared_dir=None):
        self.project_dir = Path(project_dir)
        self.project_name = project_name or self.project_dir.name
        self.store = ResourceStore(self.project_dir, self.project_name)
        self.shared = SharedStore(shared_dir)

    # ---------- publish ----------
    async def publish(self, resource_id: str) -> dict:
        """发布 look_/clothing_/prop_ 资源到共享库（character 走 publish_character）。
        只读项目 canonical；幂等 republish=同 shared id 版本+1。"""
        op = "shared.publish"
        if resource_id.startswith("look_"):
            return await self._publish_look(resource_id)
        if resource_id.startswith("clothing_"):
            return await self._publish_typed(resource_id, "clothings")
        if resource_id.startswith("prop_"):
            return await self._publish_typed(resource_id, "props")
        return {"ok": False, "operation": op, "error": "not_found",
                "detail": f"不支持的发布目标（character 用 publish_character）: {resource_id}"}

    def _find_meta_by_origin(self, doc: SharedDocument, kind: str, original_id: str) -> Optional[SharedEntryMeta]:
        for m in doc.meta.values():
            if m.kind == kind and m.origin_project == self.project_name and m.original_id == original_id:
                return m
        return None

    async def _publish_typed(self, original_id: str, section: str) -> dict:
        op = "shared.publish"
        proj_doc = self.store.load()
        bucket = getattr(proj_doc, section)
        original = bucket.get(original_id)
        if original is None:
            return {"ok": False, "operation": op, "error": "not_found",
                    "detail": f"项目资源不存在: {original_id}"}
        # 收集媒体（快照：项目文件 → shared media）
        media_sources: Dict[str, Path] = {}
        for aid in original.reference_asset_ids:
            a = proj_doc.reference_assets.get(aid)
            if a is None:
                continue
            p = Path(self.project_dir) / a.local if a.local and not Path(a.local).is_absolute() else Path(a.local or "")
            if p.is_file():
                media_sources[aid] = p
        async with self.shared.write_lock():
            sdoc = self.shared.load()
            meta = self._find_meta_by_origin(sdoc, section.rstrip("s"), original_id)
            if meta is None:
                new_id = f"{section.rstrip('s')[:2]}_{uuid.uuid4().hex[:12]}" if False else \
                    (new_clothing_id() if section == "clothings" else new_prop_id())
                # 共享新条目：scope=shared，字段来自项目 snapshot
                entry = (Clothing(**{k: v for k, v in original.to_dict().items()
                                     if k in {f for f in Clothing.__dataclass_fields__}})
                         if section == "clothings" else
                         Prop(**{k: v for k, v in original.to_dict().items()
                                 if k in {f for f in Prop.__dataclass_fields__}}))
                entry.clothing_id = entry.prop_id = new_id
                entry.scope = "shared"
                entry.origin = ResourceOrigin(source="shared_library",
                                              shared_resource_id=None, shared_version=None,
                                              imported_at=None)
                meta = SharedEntryMeta(resource_id=new_id, kind=section.rstrip("s"),
                                       version=1, origin_project=self.project_name,
                                       original_id=original_id)
                sdoc.meta[new_id] = meta
                getattr(sdoc, section)[new_id] = entry
            else:
                meta.version += 1
                entry = getattr(sdoc, section)[meta.resource_id]
                # 字段刷新（身份 id/scope/created_at 保留）
                new_fields = {k: v for k, v in original.to_dict().items()
                              if k in {f for f in type(entry).__dataclass_fields__}}
                for k, v in new_fields.items():
                    if k not in ("clothing_id", "prop_id", "created_at", "scope"):
                        setattr(entry, k, v)
                entry.updated_at = time.time()
            meta.history.append({"version": meta.version, "published_at": time.time()})
            # 媒体映射（republish 复用映射，同 shared asset id 刷新内容）
            new_ref_ids: List[str] = []
            for aid, src in media_sources.items():
                shared_aid = meta.asset_map.get(aid) or new_reference_asset_id()
                meta.asset_map[aid] = shared_aid
                ra = self.shared.copy_media_in(src, shared_aid)
                sdoc.reference_assets[shared_aid] = ra
                new_ref_ids.append(shared_aid)
            entry.reference_asset_ids = new_ref_ids
            self.shared.save(sdoc)
            # 写后重读验证
            back = self.shared.load()
            verified = meta.resource_id in getattr(back, section)
            return {"ok": True, "operation": op, "kind": section.rstrip("s"),
                    "shared_id": meta.resource_id, "version": meta.version,
                    "shared_resource_ids": new_ref_ids, "verified": verified}

    async def _publish_look(self, original_id: str) -> dict:
        op = "shared.publish"
        proj_doc = self.store.load()
        look = proj_doc.looks.get(original_id)
        if look is None:
            return {"ok": False, "operation": op, "error": "not_found",
                    "detail": f"项目 Look 不存在: {original_id}"}
        # clothing 依赖：必须先发布（显式动作链，不隐式捆绑）
        shared_clothing_id = None
        if look.clothing_id:
            async with self.shared.write_lock():
                sdoc0 = self.shared.load()
                cm = self._find_meta_by_origin(sdoc0, "clothing", look.clothing_id)
                shared_clothing_id = cm.resource_id if cm else None
            if shared_clothing_id is None:
                return {"ok": False, "operation": op, "error": "clothing_not_published",
                        "detail": f"Look 依赖的 Clothing 尚未发布（先发布 clothing_{look.clothing_id}）：{look.clothing_id}"}
        # 角色身份快照（发布时点）
        try:
            project = ProjectStore(self.project_dir).load()
        except FileNotFoundError:
            project = None
        char = project.get_character(look.character_name) if project else None
        char_snapshot = ({"name": char.name, "description_en": char.description_en or ""}
                         if char is not None else None)
        media_sources: Dict[str, Path] = {}
        for aid in look.reference_asset_ids:
            a = proj_doc.reference_assets.get(aid)
            if a is None:
                continue
            p = Path(self.project_dir) / a.local if a.local and not Path(a.local).is_absolute() else Path(a.local or "")
            if p.is_file():
                media_sources[aid] = p
        async with self.shared.write_lock():
            sdoc = self.shared.load()
            meta = self._find_meta_by_origin(sdoc, "look", original_id)
            if meta is None:
                new_id = new_look_id()
                entry = CharacterLook(**{k: v for k, v in look.to_dict().items()
                                         if k in {f for f in CharacterLook.__dataclass_fields__}})
                entry.look_id = new_id
                entry.scope = "shared"
                entry.clothing_id = shared_clothing_id
                entry.origin = ResourceOrigin(source="shared_library")
                meta = SharedEntryMeta(resource_id=new_id, kind="look", version=1,
                                       origin_project=self.project_name, original_id=original_id,
                                       character_snapshot=char_snapshot)
                sdoc.meta[new_id] = meta
                sdoc.looks[new_id] = entry
            else:
                meta.version += 1
                entry = sdoc.looks[meta.resource_id]
                new_fields = {k: v for k, v in look.to_dict().items()
                              if k in {f for f in CharacterLook.__dataclass_fields__}}
                for k, v in new_fields.items():
                    if k not in ("look_id", "clothing_id", "created_at", "scope"):
                        setattr(entry, k, v)
                entry.clothing_id = shared_clothing_id
                entry.updated_at = time.time()
                if char_snapshot:
                    meta.character_snapshot = char_snapshot
            meta.history.append({"version": meta.version, "published_at": time.time()})
            new_ref_ids: List[str] = []
            for aid, src in media_sources.items():
                shared_aid = meta.asset_map.get(aid) or new_reference_asset_id()
                meta.asset_map[aid] = shared_aid
                sdoc.reference_assets[shared_aid] = self.shared.copy_media_in(src, shared_aid)
                new_ref_ids.append(shared_aid)
            entry.reference_asset_ids = new_ref_ids
            self.shared.save(sdoc)
            back = self.shared.load()
            verified = meta.resource_id in back.looks
            return {"ok": True, "operation": op, "kind": "look",
                    "shared_id": meta.resource_id, "version": meta.version,
                    "shared_resource_ids": new_ref_ids, "verified": verified}

    async def publish_character(self, character_name: str) -> dict:
        """发布项目 Character（三视图母版）到共享库。只读项目 canonical。"""
        op = "shared.publish_character"
        try:
            project = ProjectStore(self.project_dir).load()
        except FileNotFoundError:
            project = None
        char = project.get_character(character_name) if project else None
        if char is None:
            return {"ok": False, "operation": op, "error": "character_not_found",
                    "detail": f"Character 不存在于项目 canonical: {character_name}"}
        view_files: Dict[str, Path] = {}
        for view, asset in (char.views or {}).items():
            if not asset.local:
                continue
            p = Path(asset.local)
            if not p.is_absolute():
                p = Path(self.project_dir) / p
            if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES:
                view_files[view] = p
        if not view_files:
            return {"ok": False, "operation": op, "error": "missing_character_reference",
                    "detail": f"Character {character_name} 没有可发布的视图文件"}
        async with self.shared.write_lock():
            sdoc = self.shared.load()
            meta = self._find_meta_by_origin(sdoc, "character", character_name)
            if meta is None:
                schar_id = _new_schar_id()
                entry = SharedCharacter(schar_id=schar_id, name=char.name,
                                        description_en=char.description_en or "")
                meta = SharedEntryMeta(resource_id=schar_id, kind="character", version=1,
                                       origin_project=self.project_name, original_id=character_name)
                sdoc.meta[schar_id] = meta
                sdoc.characters[schar_id] = entry
            else:
                meta.version += 1
                entry = sdoc.characters[meta.resource_id]
                entry.name = char.name
                entry.description_en = char.description_en or ""
                entry.updated_at = time.time()
            meta.history.append({"version": meta.version, "published_at": time.time()})
            view_map: Dict[str, str] = {}
            for view, src in sorted(view_files.items()):
                shared_aid = meta.asset_map.get(f"view:{view}") or new_reference_asset_id()
                meta.asset_map[f"view:{view}"] = shared_aid
                sdoc.reference_assets[shared_aid] = self.shared.copy_media_in(src, shared_aid)
                view_map[view] = shared_aid
            entry.view_asset_ids = view_map
            self.shared.save(sdoc)
            back = self.shared.load()
            verified = meta.resource_id in back.characters
            return {"ok": True, "operation": op, "kind": "character",
                    "shared_id": meta.resource_id, "version": meta.version,
                    "views": view_map, "verified": verified}

    # ---------- list / read（薄委托 SharedStore）----------

    # ---------- import ----------
    async def import_resource(self, shared_id: str, *, expected_document_revision: str) -> dict:
        """共享 → 项目 snapshot（copy-on-import：新 id、媒体复制进项目、origin 记录）。
        look 导入自动捆绑其共享 clothing（若尚未导入）；character 导入要求项目内无同名角色。"""
        op = "shared.import"
        async with self.shared.write_lock():   # 与 publish 串行读共享文档
            sdoc = self.shared.load()
            meta = sdoc.meta.get(shared_id)
            kind = None
            if shared_id in sdoc.clothings:
                kind = "clothing"
            elif shared_id in sdoc.props:
                kind = "prop"
            elif shared_id in sdoc.looks:
                kind = "look"
            elif shared_id in sdoc.characters:
                kind = "character"
            if kind is None:
                return {"ok": False, "operation": op, "error": "not_found",
                        "detail": f"共享资源不存在: {shared_id}"}
        # 项目侧导入：项目锁 + OCC
        async with self.store.write_lock():
            doc = self.store.load()
            cur = document_revision(doc)
            if expected_document_revision != cur:
                return {"ok": False, "operation": op, "error": "stale_revision",
                        "expected_revision": expected_document_revision,
                        "current_revision": cur, "current": None}
            # bundle 计划在项目文档加载后做（look 捆绑的 clothing 需查重：
            # 项目内已有 origin 指向同一共享 clothing → 不重复导入，FK 直连既有副本）
            bundle = {shared_id: kind}
            if kind == "look":
                lk = sdoc.looks[shared_id]
                if lk.clothing_id and lk.clothing_id in sdoc.clothings:
                    already = any(c.origin and c.origin.shared_resource_id == lk.clothing_id
                                  for c in doc.clothings.values())
                    if not already:
                        bundle[lk.clothing_id] = "clothing"
            if kind == "character":
                return await self._import_character(sdoc, shared_id, meta, expected_document_revision)
            # look 导入前校验角色存在
            if kind == "look":
                try:
                    project = ProjectStore(self.project_dir).load()
                except FileNotFoundError:
                    project = None
                lname = (sdoc.looks[shared_id].character_name)
                if project is None or project.get_character(lname) is None:
                    return {"ok": False, "operation": op, "error": "character_not_found",
                            "detail": f"目标项目缺少同名 Character '{lname}'（先导入/创建角色再导入 Look）"}
            imported = self._do_import(sdoc, bundle, doc)
            self.store.save(doc)
            doc_rev = document_revision(self.store.load())
            return {"ok": True, "operation": op, "kind": kind,
                    "imported_id": imported[shared_id], "shared_id": shared_id,
                    "version": meta.version if meta else None,
                    "imported_bundle": {k: v for k, v in imported.items()},
                    "document_revision_after": doc_rev}

    def _copy_media_to_project(self, sdoc: SharedDocument, shared_aid: str,
                               doc: ResourceDocument, purpose: str = "look") -> Optional[str]:
        sa = sdoc.reference_assets.get(shared_aid)
        if sa is None:
            return None
        data = self.shared.read_media_bytes(sa.local or "")
        if data is None:
            return None
        new_id = new_reference_asset_id()
        rel_dir = Path(self.project_dir) / "output" / "resources" / "imported"
        rel_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(sa.local or "x.png").suffix.lower() or ".png"
        dst = rel_dir / f"{new_id}{suffix}"
        dst.write_bytes(data)
        rel = f"output/resources/imported/{dst.name}"
        doc.reference_assets[new_id] = ReferenceAsset(
            asset_id=new_id, local=rel, url=None,
            checksum=hashlib.sha256(data).hexdigest(),
            view=sa.view, purpose=sa.purpose or purpose,
            background_type=sa.background_type,
            source="imported",
            origin=ResourceOrigin(source="shared_library",
                                  shared_resource_id=shared_aid,
                                  shared_version=None, imported_at=_now_iso()))
        return new_id

    def _do_import(self, sdoc: SharedDocument, bundle: Dict[str, str],
                   doc: ResourceDocument) -> Dict[str, str]:
        """把 bundle 中的共享条目复制进项目文档（新 id + origin）。返回 {shared_id: new_id}。
        顺序：①建 clothing/prop（空 refs）②建 look（FK 接线）③媒体复制统一回填 refs。"""
        imported: Dict[str, str] = {}
        # ① clothing / prop 本体（空 refs，媒体后填）
        for sid, k in bundle.items():
            if k == "clothing":
                sc = sdoc.clothings[sid]
                new_id = new_clothing_id()
                c = Clothing(clothing_id=new_id, **{f: getattr(sc, f) for f in Clothing.__dataclass_fields__
                                                    if f not in ("clothing_id", "created_at", "updated_at", "scope")})
                c.scope = "project"
                c.origin = ResourceOrigin(source="shared_library", shared_resource_id=sid,
                                          shared_version=None, imported_at=_now_iso())
                c.reference_asset_ids = []
                doc.clothings[new_id] = c
                imported[sid] = new_id
            elif k == "prop":
                sp = sdoc.props[sid]
                new_id = new_prop_id()
                p = Prop(prop_id=new_id, **{f: getattr(sp, f) for f in Prop.__dataclass_fields__
                                            if f not in ("prop_id", "created_at", "updated_at", "scope")})
                p.scope = "project"
                p.origin = ResourceOrigin(source="shared_library", shared_resource_id=sid,
                                          shared_version=None, imported_at=_now_iso())
                p.reference_asset_ids = []
                doc.props[new_id] = p
                imported[sid] = new_id
        # ② look 本体（FK 指向本项目副本：刚导入的或此前导入的）
        for sid, k in bundle.items():
            if k != "look":
                continue
            sl = sdoc.looks[sid]
            new_id = new_look_id()
            l = CharacterLook(look_id=new_id, **{f: getattr(sl, f) for f in CharacterLook.__dataclass_fields__
                                                 if f not in ("look_id", "created_at", "updated_at", "scope", "clothing_id")})
            l.scope = "project"
            l.reference_asset_ids = []
            if sl.clothing_id and sl.clothing_id in imported:
                l.clothing_id = imported[sl.clothing_id]
            elif sl.clothing_id:
                for c in doc.clothings.values():
                    if c.origin and c.origin.shared_resource_id == sl.clothing_id:
                        l.clothing_id = c.clothing_id
                        break
            l.origin = ResourceOrigin(source="shared_library", shared_resource_id=sid,
                                      shared_version=None, imported_at=_now_iso())
            doc.looks[new_id] = l
            imported[sid] = new_id
        # ③ 媒体复制（同 shared asset 只复制一份进项目），回填各条目 refs
        asset_map: Dict[str, str] = {}
        for sid, k in bundle.items():
            entry = (sdoc.clothings.get(sid) if k == "clothing" else
                     sdoc.props.get(sid) if k == "prop" else
                     sdoc.looks.get(sid))
            if entry is None:
                continue
            new_refs: List[str] = []
            for shared_aid in entry.reference_asset_ids:
                if shared_aid in asset_map:
                    new_refs.append(asset_map[shared_aid])
                    continue
                new_aid = self._copy_media_to_project(sdoc, shared_aid, doc)
                if new_aid:
                    asset_map[shared_aid] = new_aid
                    new_refs.append(new_aid)
            bucket = (doc.clothings if k == "clothing" else
                      doc.props if k == "prop" else doc.looks)
            bucket[imported[sid]].reference_asset_ids = new_refs
        return imported

    async def _import_character(self, sdoc: SharedDocument, shared_id: str,
                                meta: Optional[SharedEntryMeta],
                                expected_document_revision: str) -> dict:
        """导入共享角色 → 项目 Character（视图文件复制进 output/characters/，
        走 legacy views 通道立即可用；不注册项目 ReferenceAsset）。"""
        op = "shared.import_character"
        sc = sdoc.characters[shared_id]
        try:
            project = ProjectStore(self.project_dir).load()
        except FileNotFoundError:
            from src.core.models import Project
            project = Project(name=self.project_name)
        if project.get_character(sc.name) is not None:
            return {"ok": False, "operation": op, "error": "character_exists",
                    "detail": f"项目内已存在同名 Character '{sc.name}'（拒绝覆盖，先改名或删除）"}
        from src.core.models import Character, Asset, ShotStatus
        char_dir = Path(self.project_dir) / "output" / "characters"
        char_dir.mkdir(parents=True, exist_ok=True)
        views: Dict[str, Asset] = {}
        copied: Dict[str, str] = {}
        for view, shared_aid in (sc.view_asset_ids or {}).items():
            sa = sdoc.reference_assets.get(shared_aid)
            data = self.shared.read_media_bytes(sa.local or "") if sa else None
            if data is None:
                continue
            dst = char_dir / f"{sc.name}_{view}_seg.png"
            dst.write_bytes(data)
            views[view] = Asset(local=str(dst.resolve()), status=ShotStatus.DONE.value)
            copied[view] = f"output/characters/{dst.name}"
        if not views:
            return {"ok": False, "operation": op, "error": "missing_shared_media",
                    "detail": "共享角色没有可复制的视图媒体"}
        c = Character(name=sc.name, description_en=sc.description_en or "")
        c.views = views
        project.characters.append(c)
        ProjectStore(self.project_dir).save(project)   # legacy canonical 走 ProjectStore（正常创建路径）
        return {"ok": True, "operation": op, "kind": "character",
                "imported_id": sc.name, "shared_id": shared_id,
                "version": meta.version if meta else None,
                "views": copied,
                "detail": "角色视图已复制进项目（legacy views 通道）；project.json 经 ProjectStore 正常写入"}


