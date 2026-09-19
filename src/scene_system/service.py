# src/scene_system/service.py
# Scene System V1 — Phase S1 统一 service（读 + 写）。
#
# 🔴 职责边界（对齐 Resources：ResourceStore 纯存储 / WriteService lock+OCC+validation）：
#   读方法：sync、无锁、零副作用（Store.load 从不写盘）。
#   写方法：async、持 project_write_lock 短临界区（load→validate→OCC→mutate→save→verify）。
#   router 只做 HTTP mapping；业务校验全在本层。
#
# 🔴 OCC 规则（对齐 Resources §4）：
#   - 更新/删除单个实体：expected_revision = 当前实体 revision（deterministic 指纹）。
#   - 创建新实体：expected_document_revision = 当前 document revision。
#   - stale → 不写入、不自动替换 revision、不 silent retry，返回结构化 stale receipt。
#   - Runtime 不自动换 revision 重试。
#
# 🔴 fail closed 原则（§16）：
#   - SceneInstance.story_scene_id 必须引用真实 Story Scene（ProjectStore 校验）；
#     旧 Scene 被删后 read 标注 story_scene_missing（不静默忽略），create 拒绝。
#   - 删除被引用实体（anchor 被 relation/view/layout/state 引用、location 有从属实体）
#     → 409 in_use，不做级联 sweep（范围留给下一阶段）。
#
# 跨 store 校验（同锁内，纯文件读、无网络，临界区仍短）：
#   - ProjectStore（project.json）：story_scene_id 存在性。
#   - ResourceStore（resources.json）：prop_placements 的 prop_id 存在性（§14：
#     Prop identity 属 Resources，本系统只引用不复制；resources.json 损坏 →
#     结构化 resources_lookup_failed，不静默放过）。
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.store import ProjectStore

from .models import (
    ANCHOR_RELATIONS,
    AnchorKind,
    AnchorPlacement,
    AnchorRelation,
    AnchorState,
    Location,
    LocationView,
    PropPlacement,
    SceneInstance,
    SceneInstanceStatus,
    SceneState,
    SceneSystemDocument,
    SceneSystemSchemaError,
    SetLayout,
    SpatialAnchor,
    anchor_revision,
    document_revision,
    layout_revision,
    location_revision,
    new_anchor_id,
    new_layout_id,
    new_location_id,
    new_scene_instance_id,
    new_view_id,
    scene_instance_revision,
    view_revision,
)
from .store import SceneSystemStore


def _type_name(v: Any) -> str:
    return type(v).__name__


class SceneSystemService:
    """项目 Scene System 统一 service（Phase S1）。

    读：sync 无锁零副作用。写：async + project_write_lock 短临界区 + OCC receipt。
    期望失败一律返回 receipt（ok=False + error 码），不抛异常；
    只有意外内部错误（磁盘 IO / schema 损坏）向上抛。
    """

    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.project_dir = Path(project_dir)
        self.project_name = project_name or self.project_dir.name
        self.store = SceneSystemStore(self.project_dir, self.project_name)

    # ============ receipt（中性，字段兼容 Resources receipt 语义）============
    @staticmethod
    def _receipt(operation: str, *, ok: bool, target: Any = None,
                 before: Any = None, after: Any = None,
                 revision: Optional[str] = None, verified: bool = False,
                 error: Optional[str] = None, **extra) -> dict:
        rec: Dict[str, Any] = {
            "ok": ok, "operation": operation, "target": target,
            "before": before, "after": after, "revision": revision,
            "verified": verified, "error": error,
        }
        rec.update(extra)
        return rec

    @staticmethod
    def _stale_receipt(operation: str, expected: str, current: str,
                       current_dict: Any = None) -> dict:
        """固定 stale 形状。不自动替换 revision、不 silent retry。"""
        return {
            "ok": False, "operation": operation, "error": "stale_revision",
            "expected_revision": expected, "current_revision": current,
            "current": current_dict,
        }

    @staticmethod
    def _require_str(value: Any, field: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} 必须是 string，得到 {_type_name(value)}")
        return value

    # ============ 保存后重读验证（truth gate，对齐 Resources）============
    def _verify_after_save(self) -> str:
        """save 后从磁盘重读，返回 document revision（同时确认文件真实落盘可读）。"""
        reloaded = self.store.load()   # 每次从磁盘读（Store 无缓存）
        return document_revision(reloaded)

    # ============ 派生视图（单真相源：anchor_ids/view_ids/layout_id 不持久化）============
    @staticmethod
    def _location_derived(doc: SceneSystemDocument, location_id: str) -> dict:
        anchor_ids = sorted(a.anchor_id for a in doc.anchors.values()
                            if a.location_id == location_id)
        view_ids = sorted(v.view_id for v in doc.views.values()
                          if v.location_id == location_id)
        layout_id = next((l.layout_id for l in doc.layouts.values()
                          if l.location_id == location_id), None)
        return {"anchor_ids": anchor_ids, "view_ids": view_ids, "layout_id": layout_id}

    def _location_read(self, doc: SceneSystemDocument, loc: Location) -> dict:
        d = loc.to_dict()
        d["revision"] = location_revision(loc)
        d.update(self._location_derived(doc, loc.location_id))
        return d

    # ==================================================================
    # 读 API（sync，无锁，零副作用）
    # ==================================================================
    def document_overview(self) -> dict:
        doc = self.store.load()
        return {
            "schema_version": doc.schema_version,
            "document_revision": document_revision(doc),
            "counts": {
                "locations": len(doc.locations),
                "anchors": len(doc.anchors),
                "layouts": len(doc.layouts),
                "views": len(doc.views),
                "scene_instances": len(doc.scene_instances),
            },
        }

    def read_location(self, location_id: str) -> Optional[dict]:
        doc = self.store.load()
        loc = doc.locations.get(location_id)
        if loc is None:
            return None
        return self._location_read(doc, loc)

    def list_locations(self) -> List[dict]:
        doc = self.store.load()
        return [self._location_read(doc, loc) for loc in doc.locations.values()]

    def read_anchor(self, anchor_id: str) -> Optional[dict]:
        doc = self.store.load()
        a = doc.anchors.get(anchor_id)
        if a is None:
            return None
        d = a.to_dict()
        d["revision"] = anchor_revision(a)
        return d

    def list_anchors(self, location_id: Optional[str] = None) -> List[dict]:
        doc = self.store.load()
        return [self._anchor_read(a) for a in doc.anchors.values()
                if location_id is None or a.location_id == location_id]

    @staticmethod
    def _anchor_read(a: SpatialAnchor) -> dict:
        d = a.to_dict()
        d["revision"] = anchor_revision(a)
        return d

    def read_layout(self, layout_id: str) -> Optional[dict]:
        doc = self.store.load()
        l = doc.layouts.get(layout_id)
        return self._layout_read(l) if l else None

    def read_layout_by_location(self, location_id: str) -> Optional[dict]:
        doc = self.store.load()
        l = next((x for x in doc.layouts.values() if x.location_id == location_id), None)
        return self._layout_read(l) if l else None

    @staticmethod
    def _layout_read(l: SetLayout) -> dict:
        d = l.to_dict()
        d["revision"] = layout_revision(l)
        return d

    def read_view(self, view_id: str) -> Optional[dict]:
        doc = self.store.load()
        v = doc.views.get(view_id)
        return self._view_read(v) if v else None

    def list_views(self, location_id: Optional[str] = None) -> List[dict]:
        doc = self.store.load()
        return [self._view_read(v) for v in doc.views.values()
                if location_id is None or v.location_id == location_id]

    @staticmethod
    def _view_read(v: LocationView) -> dict:
        d = v.to_dict()
        d["revision"] = view_revision(v)
        return d

    # ==================================================================
    # 校验 helpers（业务规则；在锁内调用——依赖当前文档状态）
    # ==================================================================
    @staticmethod
    def _require_location(doc: SceneSystemDocument, location_id: str) -> Location:
        loc = doc.locations.get(location_id)
        if loc is None:
            raise _NotFoundError(f"Location 不存在: {location_id}", "location_not_found")
        return loc

    def _location_anchor_ids(self, doc: SceneSystemDocument, location_id: str) -> set:
        return {a.anchor_id for a in doc.anchors.values() if a.location_id == location_id}

    def _validate_relations(self, doc: SceneSystemDocument, location_id: str,
                            relations: List[AnchorRelation], subject_anchor_id: str) -> None:
        """校验 anchor.relations（§6）：relation ∈ 词表、object 存在、同 location、不自指。"""
        loc_anchors = self._location_anchor_ids(doc, location_id)
        for r in relations:
            if not isinstance(r.relation, str) or r.relation not in ANCHOR_RELATIONS:
                raise _ValidationError(
                    f"非法 spatial relation: {r.relation!r}（允许: {sorted(ANCHOR_RELATIONS)}）",
                    "invalid_relation")
            if r.object_anchor_id == subject_anchor_id:
                raise _ValidationError("anchor 不能与自身建立 relation（自指）",
                                       "invalid_relation")
            if r.object_anchor_id not in doc.anchors:
                raise _NotFoundError(
                    f"relation object anchor 不存在: {r.object_anchor_id}", "anchor_not_found")
            if r.object_anchor_id not in loc_anchors:
                raise _ValidationError(
                    f"relation object anchor {r.object_anchor_id} 不属于该 location（跨 location "
                    f"空间关系 V1 不支持；跨地点用 Location 级叙事描述表达）",
                    "cross_location_relation")

    def _validate_visible_anchors(self, doc: SceneSystemDocument, location_id: str,
                                  visible_anchor_ids: List[str]) -> None:
        """校验 LocationView.visible_anchor_ids（T8）：全部存在且属于该 location。"""
        loc_anchors = self._location_anchor_ids(doc, location_id)
        for aid in visible_anchor_ids:
            if aid not in doc.anchors:
                raise _NotFoundError(f"visible anchor 不存在: {aid}", "anchor_not_found")
            if aid not in loc_anchors:
                raise _ValidationError(
                    f"visible anchor {aid} 不属于该 location", "cross_location_anchor")

    def _validate_placements(self, doc: SceneSystemDocument, location_id: str,
                             placements: List[AnchorPlacement]) -> None:
        """校验 SetLayout.placements：anchor 存在且同 location；x/y ∈ [0,1] layout hint。"""
        loc_anchors = self._location_anchor_ids(doc, location_id)
        seen = set()
        for p in placements:
            if p.anchor_id in seen:
                raise _ValidationError(f"placement 重复 anchor: {p.anchor_id}", "validation_error")
            seen.add(p.anchor_id)
            if p.anchor_id not in doc.anchors:
                raise _NotFoundError(f"placement anchor 不存在: {p.anchor_id}", "anchor_not_found")
            if p.anchor_id not in loc_anchors:
                raise _ValidationError(
                    f"placement anchor {p.anchor_id} 不属于该 location", "cross_location_anchor")
            for axis in ("x", "y"):
                v = getattr(p, axis)
                if v is not None:
                    if not isinstance(v, (int, float)) or isinstance(v, bool):
                        raise _ValidationError(f"placement {axis} 必须是 number", "validation_error")
                    if not (0.0 <= float(v) <= 1.0):
                        raise _ValidationError(
                            f"placement {axis}={v} 超出 normalized [0,1]（layout hint，非物理坐标）",
                            "validation_error")

    # ==================================================================
    # Location CRUD（§4；rename=改 name，location_id 恒不变）
    # ==================================================================
    async def create_location(self, *, name: str, description: str = "",
                              identity_description: str = "", style_notes: str = "",
                              expected_document_revision: str = "") -> dict:
        op = "scene.create_location"
        try:
            name = self._require_str(name, "name")
            if not name.strip():
                raise ValueError("name 不能为空")
        except ValueError as e:
            return self._receipt(op, ok=False, error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            cur = document_revision(doc)
            if expected_document_revision != cur:
                return self._stale_receipt(op, expected_document_revision, cur)
            loc = Location(location_id=new_location_id(), name=name,
                           description=description or "",
                           identity_description=identity_description or "",
                           style_notes=style_notes or "")
            doc.locations[loc.location_id] = loc
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().locations.get(loc.location_id)
            verified = reloaded is not None and location_revision(reloaded) == location_revision(loc)
            return self._receipt(op, ok=True, target=loc.location_id,
                                 before=None, after=self._location_read(self.store.load(), loc),
                                 revision=location_revision(loc), verified=verified,
                                 document_revision_after=doc_rev)

    async def update_location(self, location_id: str, patch: Dict[str, Any], *,
                              expected_revision: str) -> dict:
        op = "scene.update_location"
        _FIELDS = {"name", "description", "identity_description", "style_notes"}
        try:
            location_id = self._require_str(location_id, "location_id")
            unknown = set(patch) - _FIELDS
            if unknown:
                raise ValueError(f"未知/不可变字段: {sorted(unknown)}（可改: {sorted(_FIELDS)}；"
                                 f"location_id 身份恒不变）")
            if "name" in patch and (not isinstance(patch["name"], str) or not patch["name"].strip()):
                raise ValueError("name 必须是非空 string（rename 不变 location_id）")
        except ValueError as e:
            return self._receipt(op, ok=False, target=location_id,
                                 error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            loc = doc.locations.get(location_id)
            if loc is None:
                return self._receipt(op, ok=False, target=location_id, error="location_not_found",
                                     detail=f"Location 不存在: {location_id}")
            cur = location_revision(loc)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, loc.to_dict())
            before = loc.to_dict()
            for f in _FIELDS:
                if f in patch and patch[f] is not None:
                    setattr(loc, f, patch[f])
            loc.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().locations.get(location_id)
            verified = reloaded is not None and location_revision(reloaded) == location_revision(loc)
            return self._receipt(op, ok=True, target=location_id,
                                 before=before, after=self._location_read(self.store.load(), loc),
                                 revision=location_revision(loc), verified=verified,
                                 document_revision_after=doc_rev)

    async def delete_location(self, location_id: str, *, expected_revision: str) -> dict:
        """删除 Location。fail closed：仍有 anchors/views/layouts/scene_instances 从属
        → location_in_use（无级联 sweep，范围留给下一阶段；§16 先 fail closed）。"""
        op = "scene.delete_location"
        async with self.store.write_lock():
            doc = self.store.load()
            loc = doc.locations.get(location_id)
            if loc is None:
                return self._receipt(op, ok=False, target=location_id, error="location_not_found",
                                     detail=f"Location 不存在: {location_id}")
            cur = location_revision(loc)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, loc.to_dict())
            derived = self._location_derived(doc, location_id)
            n_inst = sum(1 for i in doc.scene_instances.values() if i.location_id == location_id)
            if derived["anchor_ids"] or derived["view_ids"] or derived["layout_id"] or n_inst:
                return self._receipt(
                    op, ok=False, target=location_id, error="location_in_use",
                    detail=(f"Location 仍有从属实体（无级联删除，先逐个清理）: "
                            f"anchors={len(derived['anchor_ids'])}, views={len(derived['view_ids'])}, "
                            f"layout={'yes' if derived['layout_id'] else 'no'}, "
                            f"scene_instances={n_inst}"),
                    used_by={"anchor_ids": derived["anchor_ids"], "view_ids": derived["view_ids"],
                             "layout_id": derived["layout_id"],
                             "scene_instance_ids": sorted(i.scene_instance_id for i in
                                                          doc.scene_instances.values()
                                                          if i.location_id == location_id)})
            before = loc.to_dict()
            del doc.locations[location_id]
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            return self._receipt(op, ok=True, target=location_id, before=before, after=None,
                                 revision=None, verified=True, document_revision_after=doc_rev)

    # ==================================================================
    # Anchor CRUD（§5/§6）
    # ==================================================================
    async def create_anchor(self, *, location_id: str, name: str,
                            kind: str = AnchorKind.OTHER.value, description: str = "",
                            semantic_zone: str = "",
                            relations: Optional[List[dict]] = None,
                            expected_document_revision: str = "") -> dict:
        op = "scene.create_anchor"
        rel_objs: List[AnchorRelation] = []
        try:
            location_id = self._require_str(location_id, "location_id")
            name = self._require_str(name, "name")
            if not name.strip():
                raise ValueError("name 不能为空")
            if kind not in {k.value for k in AnchorKind}:
                raise ValueError(f"非法 anchor kind: {kind!r}（允许: {[k.value for k in AnchorKind]}）")
            if relations:
                if not isinstance(relations, list):
                    raise ValueError("relations 必须是 list")
                rel_objs = [AnchorRelation.from_dict(r) for r in relations]
        except ValueError as e:
            return self._receipt(op, ok=False, error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            try:
                self._require_location(doc, location_id)
            except _NotFoundError as e:
                return self._receipt(op, ok=False, error=e.code, detail=str(e))
            cur = document_revision(doc)
            if expected_document_revision != cur:
                return self._stale_receipt(op, expected_document_revision, cur)
            a = SpatialAnchor(anchor_id=new_anchor_id(), location_id=location_id,
                              name=name, kind=kind, description=description or "",
                              semantic_zone=semantic_zone or "", relations=rel_objs)
            try:
                # relation object 校验需要 anchor 先在文档里（同 location 检查含自身存在）
                doc.anchors[a.anchor_id] = a
                self._validate_relations(doc, location_id, rel_objs, a.anchor_id)
            except (_NotFoundError, _ValidationError) as e:
                del doc.anchors[a.anchor_id]   # 回滚内存（未 save，磁盘无痕）
                return self._receipt(op, ok=False, error=e.code, detail=str(e))
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().anchors.get(a.anchor_id)
            verified = reloaded is not None and anchor_revision(reloaded) == anchor_revision(a)
            return self._receipt(op, ok=True, target=a.anchor_id,
                                 before=None, after=self._anchor_read(a),
                                 revision=anchor_revision(a), verified=verified,
                                 document_revision_after=doc_rev)

    async def update_anchor(self, anchor_id: str, patch: Dict[str, Any], *,
                            expected_revision: str) -> dict:
        op = "scene.update_anchor"
        _FIELDS = {"name", "kind", "description", "semantic_zone", "relations"}
        rel_objs: List[AnchorRelation] = []
        try:
            anchor_id = self._require_str(anchor_id, "anchor_id")
            unknown = set(patch) - _FIELDS
            if unknown:
                raise ValueError(f"未知/不可变字段: {sorted(unknown)}（可改: {sorted(_FIELDS)}；"
                                 f"anchor_id/location_id 身份恒不变）")
            if "kind" in patch and patch["kind"] not in {k.value for k in AnchorKind}:
                raise ValueError(f"非法 anchor kind: {patch['kind']!r}")
            if "name" in patch and (not isinstance(patch["name"], str) or not patch["name"].strip()):
                raise ValueError("name 必须是非空 string")
            if "relations" in patch:
                if not isinstance(patch["relations"], list):
                    raise ValueError("relations 必须是 list")
                rel_objs = [AnchorRelation.from_dict(r) for r in patch["relations"]]
        except ValueError as e:
            return self._receipt(op, ok=False, target=anchor_id,
                                 error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            a = doc.anchors.get(anchor_id)
            if a is None:
                return self._receipt(op, ok=False, target=anchor_id, error="anchor_not_found",
                                     detail=f"Anchor 不存在: {anchor_id}")
            cur = anchor_revision(a)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, a.to_dict())
            before = a.to_dict()   # 任何 mutation 之前快照（receipt 语义）
            if "relations" in patch:
                old_relations = a.relations
                try:
                    a.relations = rel_objs   # 先挂上再校验（同 create 的自指/同 location 检查）
                    self._validate_relations(doc, a.location_id, rel_objs, a.anchor_id)
                except (_NotFoundError, _ValidationError) as e:
                    a.relations = old_relations   # 还原内存（未 save）
                    return self._receipt(op, ok=False, error=e.code, detail=str(e))
            for f in ("name", "kind", "description", "semantic_zone"):
                if f in patch and patch[f] is not None:
                    setattr(a, f, patch[f])
            a.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().anchors.get(anchor_id)
            verified = reloaded is not None and anchor_revision(reloaded) == anchor_revision(a)
            return self._receipt(op, ok=True, target=anchor_id,
                                 before=before, after=self._anchor_read(a),
                                 revision=anchor_revision(a), verified=verified,
                                 document_revision_after=doc_rev)

    async def delete_anchor(self, anchor_id: str, *, expected_revision: str) -> dict:
        """删除 Anchor。fail closed（§16）：被其他 anchor relations / views
        visible_anchor_ids / layouts placements / scene_instance state 引用 → anchor_in_use。"""
        op = "scene.delete_anchor"
        async with self.store.write_lock():
            doc = self.store.load()
            a = doc.anchors.get(anchor_id)
            if a is None:
                return self._receipt(op, ok=False, target=anchor_id, error="anchor_not_found",
                                     detail=f"Anchor 不存在: {anchor_id}")
            cur = anchor_revision(a)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, a.to_dict())
            used_by: Dict[str, List[str]] = {}
            for other in doc.anchors.values():
                if other.anchor_id != anchor_id and any(
                        r.object_anchor_id == anchor_id for r in other.relations):
                    used_by.setdefault("anchor_relations", []).append(other.anchor_id)
            for v in doc.views.values():
                if anchor_id in v.visible_anchor_ids:
                    used_by.setdefault("view_visible_anchor_ids", []).append(v.view_id)
            for l in doc.layouts.values():
                if any(p.anchor_id == anchor_id for p in l.placements):
                    used_by.setdefault("layout_placements", []).append(l.layout_id)
            for i in doc.scene_instances.values():
                refs = []
                if anchor_id in i.state.anchor_states:
                    refs.append(f"anchor_states:{anchor_id}")
                if any(pp.anchor_id == anchor_id for pp in i.state.prop_placements.values()):
                    refs.append(f"prop_placements:{anchor_id}")
                if refs:
                    used_by.setdefault("scene_instance_state", []).append(
                        f"{i.scene_instance_id}({','.join(refs)})")
            if used_by:
                return self._receipt(op, ok=False, target=anchor_id, error="anchor_in_use",
                                     detail=f"Anchor 仍被引用（无级联清理，先逐个解除）: {used_by}",
                                     used_by=used_by)
            before = a.to_dict()
            del doc.anchors[anchor_id]
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            return self._receipt(op, ok=True, target=anchor_id, before=before, after=None,
                                 revision=None, verified=True, document_revision_after=doc_rev)

    # ==================================================================
    # SetLayout（§7；一个 Location 至多一个 layout）
    # ==================================================================
    async def create_layout(self, *, location_id: str, orientation_convention: str = "",
                            notes: str = "", placements: Optional[List[dict]] = None,
                            reference_asset_id: Optional[str] = None,
                            expected_document_revision: str = "") -> dict:
        op = "scene.create_layout"
        placement_objs: List[AnchorPlacement] = []
        try:
            location_id = self._require_str(location_id, "location_id")
            if placements:
                if not isinstance(placements, list):
                    raise ValueError("placements 必须是 list")
                placement_objs = [AnchorPlacement.from_dict(p) for p in placements]
            if reference_asset_id is not None and not isinstance(reference_asset_id, str):
                raise ValueError("reference_asset_id 必须是 string 或 null")
        except ValueError as e:
            return self._receipt(op, ok=False, error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            try:
                self._require_location(doc, location_id)
            except _NotFoundError as e:
                return self._receipt(op, ok=False, error=e.code, detail=str(e))
            if any(l.location_id == location_id for l in doc.layouts.values()):
                return self._receipt(op, ok=False, target=location_id, error="layout_already_exists",
                                     detail=f"该 Location 已有 SetLayout（1:0..1）；用 update_layout 修改")
            try:
                self._validate_placements(doc, location_id, placement_objs)
            except (_NotFoundError, _ValidationError) as e:
                return self._receipt(op, ok=False, error=e.code, detail=str(e))
            cur = document_revision(doc)
            if expected_document_revision != cur:
                return self._stale_receipt(op, expected_document_revision, cur)
            l = SetLayout(layout_id=new_layout_id(), location_id=location_id,
                          orientation_convention=orientation_convention or "",
                          notes=notes or "", placements=placement_objs,
                          reference_asset_id=reference_asset_id)
            doc.layouts[l.layout_id] = l
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().layouts.get(l.layout_id)
            verified = reloaded is not None and layout_revision(reloaded) == layout_revision(l)
            return self._receipt(op, ok=True, target=l.layout_id,
                                 before=None, after=self._layout_read(l),
                                 revision=layout_revision(l), verified=verified,
                                 document_revision_after=doc_rev)

    async def update_layout(self, layout_id: str, patch: Dict[str, Any], *,
                            expected_revision: str) -> dict:
        op = "scene.update_layout"
        _FIELDS = {"orientation_convention", "notes", "placements", "reference_asset_id"}
        placement_objs: List[AnchorPlacement] = []
        try:
            layout_id = self._require_str(layout_id, "layout_id")
            unknown = set(patch) - _FIELDS
            if unknown:
                raise ValueError(f"未知/不可变字段: {sorted(unknown)}（可改: {sorted(_FIELDS)}；"
                                 f"layout_id/location_id 身份恒不变）")
            if "placements" in patch:
                if not isinstance(patch["placements"], list):
                    raise ValueError("placements 必须是 list")
                placement_objs = [AnchorPlacement.from_dict(p) for p in patch["placements"]]
            if "reference_asset_id" in patch and \
                    patch["reference_asset_id"] is not None and \
                    not isinstance(patch["reference_asset_id"], str):
                raise ValueError("reference_asset_id 必须是 string 或 null")
        except ValueError as e:
            return self._receipt(op, ok=False, target=layout_id,
                                 error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            l = doc.layouts.get(layout_id)
            if l is None:
                return self._receipt(op, ok=False, target=layout_id, error="layout_not_found",
                                     detail=f"SetLayout 不存在: {layout_id}")
            cur = layout_revision(l)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, l.to_dict())
            if "placements" in patch:
                try:
                    self._validate_placements(doc, l.location_id, placement_objs)
                except (_NotFoundError, _ValidationError) as e:
                    return self._receipt(op, ok=False, error=e.code, detail=str(e))
            before = l.to_dict()
            for f in ("orientation_convention", "notes", "reference_asset_id"):
                if f in patch and patch[f] is not None:
                    setattr(l, f, patch[f])
            if "placements" in patch:
                l.placements = placement_objs
            l.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().layouts.get(layout_id)
            verified = reloaded is not None and layout_revision(reloaded) == layout_revision(l)
            return self._receipt(op, ok=True, target=layout_id,
                                 before=before, after=self._layout_read(l),
                                 revision=layout_revision(l), verified=verified,
                                 document_revision_after=doc_rev)

    async def delete_layout(self, layout_id: str, *, expected_revision: str) -> dict:
        op = "scene.delete_layout"
        async with self.store.write_lock():
            doc = self.store.load()
            l = doc.layouts.get(layout_id)
            if l is None:
                return self._receipt(op, ok=False, target=layout_id, error="layout_not_found",
                                     detail=f"SetLayout 不存在: {layout_id}")
            cur = layout_revision(l)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, l.to_dict())
            before = l.to_dict()
            del doc.layouts[layout_id]
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            return self._receipt(op, ok=True, target=layout_id, before=before, after=None,
                                 revision=None, verified=True, document_revision_after=doc_rev)

    # ==================================================================
    # LocationView CRUD（§8/§9；metadata only，无生成；无固定 8-view 假设）
    # ==================================================================
    async def create_view(self, *, location_id: str, label: str = "",
                          camera_zone: str = "", look_direction: str = "",
                          visible_anchor_ids: Optional[List[str]] = None,
                          reference_asset_id: Optional[str] = None,
                          expected_document_revision: str = "") -> dict:
        op = "scene.create_view"
        try:
            location_id = self._require_str(location_id, "location_id")
            camera_zone = self._require_str(camera_zone, "camera_zone")
            if visible_anchor_ids is not None and not isinstance(visible_anchor_ids, list):
                raise ValueError("visible_anchor_ids 必须是 list")
            if reference_asset_id is not None and not isinstance(reference_asset_id, str):
                raise ValueError("reference_asset_id 必须是 string 或 null")
        except ValueError as e:
            return self._receipt(op, ok=False, error="validation_error", detail=str(e))
        visible = list(visible_anchor_ids or [])
        async with self.store.write_lock():
            doc = self.store.load()
            try:
                self._require_location(doc, location_id)
            except _NotFoundError as e:
                return self._receipt(op, ok=False, error=e.code, detail=str(e))
            try:
                self._validate_visible_anchors(doc, location_id, visible)
            except (_NotFoundError, _ValidationError) as e:
                return self._receipt(op, ok=False, error=e.code, detail=str(e))
            cur = document_revision(doc)
            if expected_document_revision != cur:
                return self._stale_receipt(op, expected_document_revision, cur)
            v = LocationView(view_id=new_view_id(), location_id=location_id,
                             label=label or "", camera_zone=camera_zone or "",
                             look_direction=look_direction or "",
                             visible_anchor_ids=visible,
                             reference_asset_id=reference_asset_id)
            doc.views[v.view_id] = v
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().views.get(v.view_id)
            verified = reloaded is not None and view_revision(reloaded) == view_revision(v)
            return self._receipt(op, ok=True, target=v.view_id,
                                 before=None, after=self._view_read(v),
                                 revision=view_revision(v), verified=verified,
                                 document_revision_after=doc_rev)

    async def update_view(self, view_id: str, patch: Dict[str, Any], *,
                          expected_revision: str) -> dict:
        op = "scene.update_view"
        # S2A §7/§8/§25：视觉状态字段（generation_status/active_generation_id/
        # reference_asset_id/approval_status）是协议管理字段——只能经
        # generate/review/approve/revoke 协议变更，不允许手工 patch 绕过审批证据链。
        _FIELDS = {"label", "camera_zone", "look_direction", "visible_anchor_ids"}
        try:
            view_id = self._require_str(view_id, "view_id")
            unknown = set(patch) - _FIELDS
            if unknown:
                raise ValueError(
                    f"未知/不可变字段: {sorted(unknown)}（可改: {sorted(_FIELDS)}；"
                    f"view_id/location_id 身份恒不变；视觉状态字段 generation_status/"
                    f"active_generation_id/reference_asset_id/approval_status 由 "
                    f"generate/approve/revoke 协议管理，不可手工 patch）")
            if "visible_anchor_ids" in patch and \
                    not isinstance(patch["visible_anchor_ids"], list):
                raise ValueError("visible_anchor_ids 必须是 list")
        except ValueError as e:
            return self._receipt(op, ok=False, target=view_id,
                                 error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            v = doc.views.get(view_id)
            if v is None:
                return self._receipt(op, ok=False, target=view_id, error="view_not_found",
                                     detail=f"LocationView 不存在: {view_id}")
            cur = view_revision(v)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, v.to_dict())
            if "visible_anchor_ids" in patch:
                try:
                    self._validate_visible_anchors(doc, v.location_id,
                                                   list(patch["visible_anchor_ids"] or []))
                except (_NotFoundError, _ValidationError) as e:
                    return self._receipt(op, ok=False, error=e.code, detail=str(e))
            before = v.to_dict()
            for f in ("label", "camera_zone", "look_direction"):
                if f in patch and patch[f] is not None:
                    setattr(v, f, patch[f])
            if "visible_anchor_ids" in patch:
                v.visible_anchor_ids = list(patch["visible_anchor_ids"] or [])
            v.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().views.get(view_id)
            verified = reloaded is not None and view_revision(reloaded) == view_revision(v)
            return self._receipt(op, ok=True, target=view_id,
                                 before=before, after=self._view_read(v),
                                 revision=view_revision(v), verified=verified,
                                 document_revision_after=doc_rev)

    async def delete_view(self, view_id: str, *, expected_revision: str) -> dict:
        op = "scene.delete_view"
        async with self.store.write_lock():
            doc = self.store.load()
            v = doc.views.get(view_id)
            if v is None:
                return self._receipt(op, ok=False, target=view_id, error="view_not_found",
                                     detail=f"LocationView 不存在: {view_id}")
            cur = view_revision(v)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, v.to_dict())
            before = v.to_dict()
            del doc.views[view_id]
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            return self._receipt(op, ok=True, target=view_id, before=before, after=None,
                                 revision=None, verified=True, document_revision_after=doc_rev)

    # ==================================================================
    # SceneInstance / SceneState（§10/§11/§12）
    # ==================================================================
    # 跨 store 校验（同锁内、纯文件读、无网络）：
    #   - story_scene_id → ProjectStore（project.json）；project.json 不存在/段不在
    #     → story_scene_not_found（fail closed，不静默）。
    #   - prop_placements 的 prop_id → ResourceStore（resources.json）；identity 属
    #     Resources（§14），本系统只引用不复制；resources.json 损坏 → resources_lookup_failed。
    def _story_scene_exists(self, story_scene_id: str) -> bool:
        """现有 Story Scene 是否存在（read 也用：悬空标注 story_scene_missing）。"""
        try:
            project = ProjectStore(self.project_dir).load()
        except FileNotFoundError:
            return False
        return any(sc.scene_id == story_scene_id for sc in project.scenes)

    def _lookup_props(self) -> Dict[str, Any]:
        """Resources props keyed map（只读引用校验用）。损坏 → _ResourcesLookupError。"""
        from src.resources.store import ResourceStore
        from src.resources.models import ResourceSchemaError
        try:
            return ResourceStore(self.project_dir, self.project_name).load().props
        except ResourceSchemaError as e:
            raise _ResourcesLookupError(str(e)) from e

    @staticmethod
    def _instance_read(doc: SceneSystemDocument, inst: SceneInstance, *,
                       story_scene_missing: Optional[bool] = None,
                       prop_annotation: Optional[dict] = None) -> dict:
        d = inst.to_dict()
        d["revision"] = scene_instance_revision(inst)
        if story_scene_missing is not None:
            d["story_scene_missing"] = story_scene_missing
        if prop_annotation is not None:
            d.update(prop_annotation)
        return d

    def _prop_reference_annotation(self, state: SceneState) -> dict:
        """S2A §6D：SceneState.prop_placements 引用的 Prop 在 Resources 中被删后，
        read 必须诚实暴露（不 crash、不假装存在）：
        - missing_props：可证明缺失（resources 可读且 prop 不在）——诚实标注；
        - props_lookup_unavailable：resources.json 损坏无法判定——诚实标注不确定，
          绝不静默当作存在或缺失（写路径已 fail closed，读路径只暴露不阻断）。"""
        ids = list(state.prop_placements.keys())
        if not ids:
            return {"missing_props": [], "props_lookup_unavailable": False}
        try:
            props = self._lookup_props()
        except _ResourcesLookupError:
            return {"missing_props": [], "props_lookup_unavailable": True}
        return {"missing_props": [p for p in ids if p not in props],
                "props_lookup_unavailable": False}

    def read_scene_instance(self, instance_id: str) -> Optional[dict]:
        doc = self.store.load()
        inst = doc.scene_instances.get(instance_id)
        if inst is None:
            return None
        # 悬空检测（§16）：旧 Story Scene 被删 → 明确标注，不静默忽略
        missing = not self._story_scene_exists(inst.story_scene_id)
        return self._instance_read(doc, inst, story_scene_missing=missing,
                                   prop_annotation=self._prop_reference_annotation(inst.state))

    def list_scene_instances(self, story_scene_id: Optional[str] = None,
                             location_id: Optional[str] = None) -> List[dict]:
        doc = self.store.load()
        out = []
        for inst in doc.scene_instances.values():
            if story_scene_id is not None and inst.story_scene_id != story_scene_id:
                continue
            if location_id is not None and inst.location_id != location_id:
                continue
            missing = not self._story_scene_exists(inst.story_scene_id)
            out.append(self._instance_read(doc, inst, story_scene_missing=missing,
                                           prop_annotation=self._prop_reference_annotation(inst.state)))
        return out

    async def create_scene_instance(self, *, story_scene_id: str, location_id: str,
                                    expected_document_revision: str = "") -> dict:
        """创建 SceneInstance。T11/T12：story_scene 与 location 必须真实存在；
        T13：同一 story_scene 至多一个 active instance。绑定时刻快照
        location_revision_at_bind（历史事实，之后不随 location 更新）。"""
        op = "scene.create_scene_instance"
        try:
            story_scene_id = self._require_str(story_scene_id, "story_scene_id")
            location_id = self._require_str(location_id, "location_id")
        except ValueError as e:
            return self._receipt(op, ok=False, error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            loc = doc.locations.get(location_id)
            if loc is None:
                return self._receipt(op, ok=False, error="location_not_found",
                                     detail=f"Location 不存在: {location_id}")
            if not self._story_scene_exists(story_scene_id):
                return self._receipt(
                    op, ok=False, error="story_scene_not_found",
                    detail=(f"Story Scene 不存在于项目 canonical: {story_scene_id}"
                            f"（project.json 不存在或 scene_id 无效；不静默创建）"),
                    story_scene_id=story_scene_id)
            existing_active = [i for i in doc.scene_instances.values()
                               if i.story_scene_id == story_scene_id
                               and i.status == SceneInstanceStatus.ACTIVE.value]
            if existing_active:
                return self._receipt(
                    op, ok=False, error="instance_already_active",
                    detail=(f"Story Scene {story_scene_id} 已有 active SceneInstance "
                            f"（V1 一段至多一个 active；先 archive/retire 或删除旧的）"),
                    existing_scene_instance_ids=sorted(
                        i.scene_instance_id for i in existing_active))
            cur = document_revision(doc)
            if expected_document_revision != cur:
                return self._stale_receipt(op, expected_document_revision, cur)
            inst = SceneInstance(scene_instance_id=new_scene_instance_id(),
                                 story_scene_id=story_scene_id,
                                 location_id=location_id,
                                 location_revision_at_bind=location_revision(loc))
            doc.scene_instances[inst.scene_instance_id] = inst
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().scene_instances.get(inst.scene_instance_id)
            verified = (reloaded is not None and
                        scene_instance_revision(reloaded) == scene_instance_revision(inst))
            return self._receipt(op, ok=True, target=inst.scene_instance_id,
                                 before=None,
                                 after=self._instance_read(self.store.load(), inst,
                                                           story_scene_missing=False),
                                 revision=scene_instance_revision(inst), verified=verified,
                                 document_revision_after=doc_rev)

    async def update_scene_instance_location(self, instance_id: str, *, location_id: str,
                                             expected_revision: str) -> dict:
        """换绑 Location（罕见操作）。fail closed：现有 state 的 anchor 引用不属于
        新 location → state_references_old_location（先清 state 再换绑）。"""
        op = "scene.update_scene_instance_location"
        try:
            location_id = self._require_str(location_id, "location_id")
        except ValueError as e:
            return self._receipt(op, ok=False, target=instance_id,
                                 error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            inst = doc.scene_instances.get(instance_id)
            if inst is None:
                return self._receipt(op, ok=False, target=instance_id, error="instance_not_found",
                                     detail=f"SceneInstance 不存在: {instance_id}")
            cur = scene_instance_revision(inst)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, inst.to_dict())
            new_loc = doc.locations.get(location_id)
            if new_loc is None:
                return self._receipt(op, ok=False, error="location_not_found",
                                     detail=f"Location 不存在: {location_id}")
            new_anchors = self._location_anchor_ids(doc, location_id)
            stale_refs = ([k for k in inst.state.anchor_states if k not in new_anchors]
                          + [pid for pid, pp in inst.state.prop_placements.items()
                             if pp.anchor_id not in new_anchors])
            if stale_refs:
                return self._receipt(
                    op, ok=False, target=instance_id, error="state_references_old_location",
                    detail=(f"SceneState 引用旧 location 的 anchor（先清空/修正 state 再换绑）: "
                            f"{sorted(set(stale_refs))}"),
                    stale_anchor_refs=sorted(set(stale_refs)))
            before = inst.to_dict()
            inst.location_id = location_id
            inst.location_revision_at_bind = location_revision(new_loc)
            inst.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().scene_instances.get(instance_id)
            verified = (reloaded is not None and
                        scene_instance_revision(reloaded) == scene_instance_revision(inst))
            return self._receipt(op, ok=True, target=instance_id,
                                 before=before,
                                 after=self._instance_read(self.store.load(), inst,
                                                           story_scene_missing=False),
                                 revision=scene_instance_revision(inst), verified=verified,
                                 document_revision_after=doc_rev)

    async def delete_scene_instance(self, instance_id: str, *, expected_revision: str) -> dict:
        """删除 SceneInstance（连同其 SceneState——state 嵌入 instance，无独立真相源）。"""
        op = "scene.delete_scene_instance"
        async with self.store.write_lock():
            doc = self.store.load()
            inst = doc.scene_instances.get(instance_id)
            if inst is None:
                return self._receipt(op, ok=False, target=instance_id, error="instance_not_found",
                                     detail=f"SceneInstance 不存在: {instance_id}")
            cur = scene_instance_revision(inst)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, inst.to_dict())
            before = inst.to_dict()
            del doc.scene_instances[instance_id]
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            return self._receipt(op, ok=True, target=instance_id, before=before, after=None,
                                 revision=None, verified=True, document_revision_after=doc_rev)

    async def patch_scene_state(self, instance_id: str, patch: Dict[str, Any], *,
                                expected_revision: str) -> dict:
        """SceneState partial patch（T14 round-trip / T15 / T16 校验点）。
        - 字符串字段：time_of_day/weather/lighting/environment_conditions/notes。
        - anchor_states / prop_placements：整 dict 替换（read-patch-write + OCC 保障
          并发安全；不做 per-key 部分合并——语义更简单可预测）。
        - anchor_states key 必须是该 instance location 的合法 anchor（T15）。
        - prop_placements 的 prop_id 必须存在于 Resources（T16）；anchor_id 必须合法。
        - 只保存跨 Shot 持续成立的结果状态；Shot 内动作过程属于 Shot/Video（§12）。"""
        op = "scene.patch_scene_state"
        _STR_FIELDS = {"time_of_day", "weather", "lighting", "environment_conditions", "notes"}
        try:
            instance_id = self._require_str(instance_id, "instance_id")
            unknown = set(patch) - _STR_FIELDS - {"anchor_states", "prop_placements"}
            if unknown:
                raise ValueError(f"未知字段: {sorted(unknown)}（可 patch: "
                                 f"{sorted(_STR_FIELDS)} + anchor_states + prop_placements）")
            for f in _STR_FIELDS:
                if f in patch and not isinstance(patch[f], str):
                    raise ValueError(f"{f} 必须是 string")
            anchor_states_raw = patch.get("anchor_states")
            if anchor_states_raw is not None:
                if not isinstance(anchor_states_raw, dict):
                    raise ValueError("anchor_states 必须是 {anchor_id: {condition, note}} dict")
            prop_placements_raw = patch.get("prop_placements")
            if prop_placements_raw is not None:
                if not isinstance(prop_placements_raw, dict):
                    raise ValueError(
                        "prop_placements 必须是 {prop_id: {anchor_id, state, note}} dict")
        except ValueError as e:
            return self._receipt(op, ok=False, target=instance_id,
                                 error="validation_error", detail=str(e))
        async with self.store.write_lock():
            doc = self.store.load()
            inst = doc.scene_instances.get(instance_id)
            if inst is None:
                return self._receipt(op, ok=False, target=instance_id, error="instance_not_found",
                                     detail=f"SceneInstance 不存在: {instance_id}")
            cur = scene_instance_revision(inst)
            if expected_revision != cur:
                return self._stale_receipt(op, expected_revision, cur, inst.to_dict())
            loc_anchors = self._location_anchor_ids(doc, inst.location_id)
            new_anchor_states: Optional[Dict[str, AnchorState]] = None
            new_prop_placements: Optional[Dict[str, PropPlacement]] = None
            if anchor_states_raw is not None:
                new_anchor_states = {}
                for k, v in anchor_states_raw.items():
                    if k not in doc.anchors:
                        return self._receipt(
                            op, ok=False, target=instance_id, error="anchor_not_found",
                            detail=f"anchor_states 引用不存在的 anchor: {k}")
                    if k not in loc_anchors:
                        return self._receipt(
                            op, ok=False, target=instance_id, error="cross_location_anchor",
                            detail=f"anchor_states 引用的 anchor {k} 不属于该 instance 的 location")
                    new_anchor_states[k] = AnchorState.from_dict(v)
            if prop_placements_raw is not None:
                try:
                    props = self._lookup_props()
                except _ResourcesLookupError as e:
                    return self._receipt(
                        op, ok=False, target=instance_id, error="resources_lookup_failed",
                        detail=f"resources.json 不可读（fail closed，不校验不放过）: {e}")
                new_prop_placements = {}
                for pid, v in prop_placements_raw.items():
                    if pid not in props:
                        return self._receipt(
                            op, ok=False, target=instance_id, error="prop_not_found",
                            detail=f"prop_placements 引用不存在的 Resource Prop: {pid}"
                                   f"（Prop identity 属 Production Resources V1，只引用不复制）")
                    pp = PropPlacement.from_dict(v)
                    if pp.anchor_id and pp.anchor_id not in loc_anchors:
                        return self._receipt(
                            op, ok=False, target=instance_id, error="cross_location_anchor",
                            detail=f"prop {pid} 的 anchor {pp.anchor_id} 不属于该 instance 的 location")
                    new_prop_placements[pid] = pp
            before = inst.to_dict()
            for f in _STR_FIELDS:
                if f in patch:
                    setattr(inst.state, f, patch[f])
            if new_anchor_states is not None:
                inst.state.anchor_states = new_anchor_states
            if new_prop_placements is not None:
                inst.state.prop_placements = new_prop_placements
            inst.updated_at = time.time()
            self.store.save(doc)
            doc_rev = self._verify_after_save()
            reloaded = self.store.load().scene_instances.get(instance_id)
            verified = (reloaded is not None and
                        scene_instance_revision(reloaded) == scene_instance_revision(inst))
            return self._receipt(op, ok=True, target=instance_id,
                                 before=before,
                                 after=self._instance_read(self.store.load(), inst,
                                                           story_scene_missing=False),
                                 revision=scene_instance_revision(inst), verified=verified,
                                 document_revision_after=doc_rev)


# ============ service 内部控制流异常（不逃逸出 service；转为 receipt）============
class _NotFoundError(Exception):
    def __init__(self, detail: str, code: str):
        super().__init__(detail)
        self.code = code


class _ValidationError(Exception):
    def __init__(self, detail: str, code: str = "validation_error"):
        super().__init__(detail)
        self.code = code


class _ResourcesLookupError(Exception):
    """resources.json 不可读（损坏）。prop 引用校验 fail closed 时转 receipt。"""

