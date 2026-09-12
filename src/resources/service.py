# src/resources/service.py
# Production Resources V1 — Phase 1B read service + internal test-facing create/update.
#
# 轻量 list + 精确 read（从第一天具备，未来 Agent/UI 都能复用）。
# 🔴 Phase 1B 不接 Agent、不暴露 public mutation HTTP API（router 只读）。
# 内部 create/update 仅供测试与 Phase 1C 预留（sync，调用 sync store）。
#
# list 禁止返回：base64 / 图片二进制 / 完整 generation receipt / 巨大 prompt / 完整 review history。
# list 只返回：id / type / name|label / status / cover_asset_id / revision / relationship_summary。
# read 返回：完整 typed relationship（resolve asset_id → ReferenceAsset、look→clothing 等）。
from __future__ import annotations
from pathlib import Path
from typing import Optional, List, Dict, Any

from .store import ResourceStore
from .models import (
    CharacterLook, Clothing, Prop, ReferenceAsset, ResourceDocument,
    ResourceScope, ResourceSource, ApprovalState, GenerationStatus, ResourceOrigin,
    new_look_id, new_clothing_id, new_prop_id, new_reference_asset_id,
    look_revision, clothing_revision, prop_revision, reference_asset_revision,
    document_revision,
)
from .store import legacy_character_references as _legacy_refs


class ResourceService:
    # ---- P1 §13/§14：latest committed generation 的 style 兼容结论（computed 只读，
    #      不落盘、不 mutation；legacy record → unknown + generated_before_visual_profile）----
    def _latest_generation_style(self, **filter_kw) -> Optional[dict]:
        from .generation_records import read_records
        from src.visual_profile import load_profile_or_none
        from src.visual_profile.models import (style_compatibility_of_basis,
                                               visual_profile_fingerprint,
                                               VisualProfileSchemaError)
        recs = [r for r in read_records(self.project_dir, **filter_kw)
                if r.get("status") == "committed" and r.get("basis")]
        if not recs:
            return None
        last = recs[-1]
        try:
            vp = load_profile_or_none(self.project_dir)
            cur_fp = visual_profile_fingerprint(vp) if vp is not None else None
        except VisualProfileSchemaError:
            return {"generation_id": last.get("generation_id"),
                    "style_compatibility": "visual_profile_unreadable",
                    "generated_before_visual_profile": None,
                    "visual_profile_fingerprint": (last.get("basis") or {}).get(
                        "visual_profile_fingerprint")}
        comp = style_compatibility_of_basis(last.get("basis"), cur_fp)
        comp["generation_id"] = last.get("generation_id")
        return comp

    """项目资源只读 service + 内部测试用 typed create/update。"""

    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.project_dir = Path(project_dir)
        self.project_name = project_name or self.project_dir.name
        self.store = ResourceStore(self.project_dir, self.project_name)

    # ============ lightweight summaries（list 用）============
    @staticmethod
    def _look_summary(lk: CharacterLook) -> dict:
        return {
            "id": lk.look_id, "type": "look", "name": lk.label,
            "character_name": lk.character_name,
            "clothing_id": lk.clothing_id,
            "status": lk.approval_status,
            "generation_status": lk.generation_status,
            "cover_asset_id": lk.cover_asset_id,
            "revision": look_revision(lk),
            "relationship_summary": (
                f"character={lk.character_name}; clothing={lk.clothing_id or '-'}; "
                f"{len(lk.reference_asset_ids)} ref(s), {len(lk.approved_reference_asset_ids)} approved"),
        }

    @staticmethod
    def _clothing_summary(c: Clothing) -> dict:
        return {
            "id": c.clothing_id, "type": "clothing", "name": c.name,
            "status": c.approval_status, "cover_asset_id": c.cover_asset_id,
            "revision": clothing_revision(c),
            "relationship_summary": (
                f"{len(c.reference_asset_ids)} ref(s), {len(c.approved_reference_asset_ids)} approved"),
        }

    @staticmethod
    def _prop_summary(p: Prop) -> dict:
        return {
            "id": p.prop_id, "type": "prop", "name": p.name,
            "status": p.approval_status, "cover_asset_id": p.cover_asset_id,
            "revision": prop_revision(p),
            "relationship_summary": (
                f"{len(p.reference_asset_ids)} ref(s), {len(p.approved_reference_asset_ids)} approved"),
        }

    @staticmethod
    def _reference_asset_summary(a: ReferenceAsset) -> dict:
        return {
            "id": a.asset_id, "type": "reference_asset", "name": f"{a.view or a.asset_id}",
            "status": "done" if (a.local or a.url) else "pending",
            "cover_asset_id": None, "revision": reference_asset_revision(a),
            "relationship_summary": f"view={a.view or '-'}; purpose={a.purpose or '-'}; source={a.source}",
        }

    @staticmethod
    def _composition_summary(c) -> dict:
        from .models import composition_revision
        return {
            "id": c.composition_id, "type": "composition", "name": c.label,
            "character_name": c.character_name, "look_id": c.look_id,
            "status": c.approval_status, "generation_status": c.generation_status,
            "cover_asset_id": c.cover_asset_id, "revision": composition_revision(c),
            "relationship_summary": (
                f"character={c.character_name}; look={c.look_id}; "
                f"{len(c.prop_bindings)} prop binding(s), "
                f"{len(c.reference_asset_ids)} ref(s), {len(c.approved_reference_asset_ids)} approved"),
        }

    # ============ list（轻量地图）============
    def list_resources(self, type: Optional[str] = None) -> List[dict]:
        """统一资源列表。type ∈ look|clothing|prop|reference_asset|None(all)。
        reference_asset 默认不进 all（它是子对象，read 父对象时 resolve）；
        显式 type=reference_asset 才返回。"""
        out: List[dict] = []
        if type in (None, "all", "look"):
            out += [self._look_summary(l) for l in self.store.list_looks()]
        if type in (None, "all", "clothing"):
            out += [self._clothing_summary(c) for c in self.store.list_clothings()]
        if type in (None, "all", "prop"):
            out += [self._prop_summary(p) for p in self.store.list_props()]
        if type in (None, "all", "composition"):
            out += [self._composition_summary(c) for c in self.store.list_compositions()]
        if type == "reference_asset":
            out += [self._reference_asset_summary(a) for a in self.store.list_reference_assets()]
        return out

    def list_looks(self, character_name: Optional[str] = None) -> List[dict]:
        looks = self.store.list_looks()
        if character_name:
            looks = [l for l in looks if l.character_name == character_name]
        return [self._look_summary(l) for l in looks]

    def list_clothings(self) -> List[dict]:
        return [self._clothing_summary(c) for c in self.store.list_clothings()]

    def list_props(self) -> List[dict]:
        return [self._prop_summary(p) for p in self.store.list_props()]

    def list_compositions(self, character_name: Optional[str] = None) -> List[dict]:
        comps = self.store.list_compositions()
        if character_name:
            comps = [c for c in comps if c.character_name == character_name]
        return [self._composition_summary(c) for c in comps]

    # ============ read（完整 typed relationship）============
    def _resolve_assets(self, asset_ids: List[str]) -> List[dict]:
        """resolve asset_id → ReferenceAsset dict；缺失容错（不报错，标记 missing）。"""
        out = []
        for aid in asset_ids:
            a = self.store.get_reference_asset(aid)
            if a is not None:
                out.append({**a.to_dict(), "revision": reference_asset_revision(a), "missing": False})
            else:
                out.append({"asset_id": aid, "missing": True})
        return out

    def read_look(self, look_id: str) -> Optional[dict]:
        lk = self.store.get_look(look_id)
        if lk is None:
            return None
        clothing = None
        if lk.clothing_id:
            cl = self.store.get_clothing(lk.clothing_id)
            if cl is not None:
                clothing = {**cl.to_dict(), "revision": clothing_revision(cl)}
        # 每个候选的最近审查结论（1C2 review evidence 只读视图；证据本体在 review_records.jsonl）
        from .look_review import latest_review_for_asset
        latest_reviews = {}
        for aid in lk.reference_asset_ids:
            cr = latest_review_for_asset(self.project_dir, look_id, aid)
            if cr is not None:
                latest_reviews[aid] = {
                    "status": cr.get("status"), "overall": cr.get("overall"),
                    "identity_consistency": cr.get("identity_consistency"),
                    "costume_consistency": cr.get("costume_consistency"),
                    "visual_defects": cr.get("visual_defects"),
                    "summary": cr.get("summary"),
                }
        return {
            **lk.to_dict(),
            "revision": look_revision(lk),
            "reference_assets": self._resolve_assets(lk.reference_asset_ids),
            "approved_reference_assets": self._resolve_assets(lk.approved_reference_asset_ids),
            "cover_asset": (self._resolve_assets([lk.cover_asset_id])[0] if lk.cover_asset_id else None),
            "clothing": clothing,
            "latest_reviews": latest_reviews,
            # P1 §13：latest committed generation 的项目风格兼容结论（computed）
            "latest_generation_style": self._latest_generation_style(look_id=lk.look_id),
        }

    def read_clothing(self, clothing_id: str) -> Optional[dict]:
        c = self.store.get_clothing(clothing_id)
        if c is None:
            return None
        # 反向关系：哪些 Look 引用了该 clothing
        used_by_looks = [lk.look_id for lk in self.store.list_looks() if lk.clothing_id == clothing_id]
        return {
            **c.to_dict(),
            "revision": clothing_revision(c),
            "reference_assets": self._resolve_assets(c.reference_asset_ids),
            "approved_reference_assets": self._resolve_assets(c.approved_reference_asset_ids),
            "cover_asset": (self._resolve_assets([c.cover_asset_id])[0] if c.cover_asset_id else None),
            "used_by_looks": used_by_looks,
        }

    def read_prop(self, prop_id: str) -> Optional[dict]:
        p = self.store.get_prop(prop_id)
        if p is None:
            return None
        # 每张参考图的最近审查结论（Phase 1C5 prop review evidence 只读视图）
        from .prop_review import latest_prop_review_for_asset
        latest_reviews = {}
        for aid in p.reference_asset_ids:
            cr = latest_prop_review_for_asset(self.project_dir, prop_id, aid)
            if cr is not None:
                latest_reviews[aid] = {
                    "status": cr.get("status"), "overall": cr.get("overall"),
                    "prop_fidelity": cr.get("prop_fidelity"),
                    "visual_clarity": cr.get("visual_clarity"),
                    "visual_defects": cr.get("visual_defects"),
                    "summary": cr.get("summary"),
                }
        return {
            **p.to_dict(),
            "revision": prop_revision(p),
            "reference_assets": self._resolve_assets(p.reference_asset_ids),
            "approved_reference_assets": self._resolve_assets(p.approved_reference_asset_ids),
            "cover_asset": (self._resolve_assets([p.cover_asset_id])[0] if p.cover_asset_id else None),
            "latest_reviews": latest_reviews,
        }

    def read_reference_asset(self, asset_id: str) -> Optional[dict]:
        a = self.store.get_reference_asset(asset_id)
        if a is None:
            return None
        # 反向关系：哪些资源引用了该 asset
        used_by: List[str] = []
        for lk in self.store.list_looks():
            if asset_id in lk.reference_asset_ids or asset_id in lk.approved_reference_asset_ids:
                used_by.append(lk.look_id)
        for c in self.store.list_clothings():
            if asset_id in c.reference_asset_ids:
                used_by.append(c.clothing_id)
        for p in self.store.list_props():
            if asset_id in p.reference_asset_ids:
                used_by.append(p.prop_id)
        for c in self.store.list_compositions():
            if asset_id in c.reference_asset_ids or asset_id in c.approved_reference_asset_ids:
                used_by.append(c.composition_id)
        return {**a.to_dict(), "revision": reference_asset_revision(a), "used_by": used_by}

    def read_composition(self, composition_id: str) -> Optional[dict]:
        """读 Composition：候选/approved/cover/look/逐 binding prop 全 resolve。"""
        from .models import composition_revision
        c = self.store.get_composition(composition_id)
        if c is None:
            return None
        look = self.store.get_look(c.look_id)
        look_resolved = None
        if look is not None:
            look_resolved = {
                "look_id": look.look_id, "label": look.label,
                "character_name": look.character_name,
                "approval_status": look.approval_status,
                "approved_reference_asset_ids": list(look.approved_reference_asset_ids),
                "approved_reference_assets": self._resolve_assets(look.approved_reference_asset_ids),
                "cover_asset_id": look.cover_asset_id,
            }
        bindings = []
        for b in c.prop_bindings:
            p = self.store.get_prop(b.prop_id)
            bindings.append({
                **b.to_dict(),
                "prop": ({"prop_id": p.prop_id, "name": p.name,
                          "description": p.description,
                          "approval_status": p.approval_status,
                          "approved_reference_asset_ids": list(p.approved_reference_asset_ids),
                          "cover_asset_id": p.cover_asset_id} if p is not None
                         else {"prop_id": b.prop_id, "missing": True}),
            })
        # 每个候选的最近审查结论（1C5 六维度 review evidence 只读视图）
        from .composition_review import latest_composition_review_for_asset
        latest_reviews = {}
        for aid in c.reference_asset_ids:
            cr = latest_composition_review_for_asset(self.project_dir, composition_id, aid)
            if cr is not None:
                latest_reviews[aid] = {
                    "status": cr.get("status"), "overall": cr.get("overall"),
                    "character_identity": cr.get("character_identity"),
                    "look_consistency": cr.get("look_consistency"),
                    "prop_fidelity": cr.get("prop_fidelity"),
                    "prop_binding_correctness": cr.get("prop_binding_correctness"),
                    "required_prop_visibility": cr.get("required_prop_visibility"),
                    "visual_defects": cr.get("visual_defects"),
                    "per_binding": cr.get("per_binding"),
                    "summary": cr.get("summary"),
                }
        return {
            **c.to_dict(),
            "revision": composition_revision(c),
            "reference_assets": self._resolve_assets(c.reference_asset_ids),
            "approved_reference_assets": self._resolve_assets(c.approved_reference_asset_ids),
            "cover_asset": (self._resolve_assets([c.cover_asset_id])[0] if c.cover_asset_id else None),
            "look": look_resolved,
            "prop_bindings_resolved": bindings,
            "latest_reviews": latest_reviews,
            # P1 §13：latest committed generation 的项目风格兼容结论（computed）
            "latest_generation_style": self._latest_generation_style(
                composition_id=c.composition_id),
        }

    def read_resource(self, resource_id: str) -> Optional[dict]:
        """按 id 前缀 dispatch。"""
        if resource_id.startswith("look_"):
            return self.read_look(resource_id)
        if resource_id.startswith("clothing_"):
            return self.read_clothing(resource_id)
        if resource_id.startswith("prop_"):
            return self.read_prop(resource_id)
        if resource_id.startswith("comp_"):
            return self.read_composition(resource_id)
        if resource_id.startswith("ref_"):
            return self.read_reference_asset(resource_id)
        return None

    # ============ document revision（创建类 mutation 的 OCC 期望值来源）============
    def document_revision(self) -> str:
        """当前整个 resources document 的内容指纹。创建新资源时调用方以此作为
        expected_document_revision（Phase 1C1 §4）。"""
        return document_revision(self.store.load())

    # ============ legacy Character.views 只读 adapter（§8/§9）============
    def legacy_character_references(self, character_name: str) -> List[dict]:
        return [a.to_dict() for a in _legacy_refs(self.project_dir, character_name)]

    # ============ 内部 typed create/update（仅供测试 / Phase 1C 预留）============
    # Phase 1B 不通过 HTTP 暴露 mutation（router 只读）。这些 sync 方法供测试直接调用。
    def create_look(self, character_name: str, label: str = "", clothing_id: Optional[str] = None,
                    appearance_description: str = "", appearance_delta: str = "",
                    reference_asset_ids: Optional[List[str]] = None,
                    approved_reference_asset_ids: Optional[List[str]] = None,
                    cover_asset_id: Optional[str] = None,
                    generation_status: str = GenerationStatus.NONE.value,
                    approval_status: str = ApprovalState.UNREVIEWED.value) -> CharacterLook:
        lk = CharacterLook(look_id=new_look_id(), character_name=character_name,
                           label=label, clothing_id=clothing_id,
                           appearance_description=appearance_description, appearance_delta=appearance_delta,
                           reference_asset_ids=list(reference_asset_ids or []),
                           approved_reference_asset_ids=list(approved_reference_asset_ids or []),
                           cover_asset_id=cover_asset_id,
                           generation_status=generation_status, approval_status=approval_status)
        self.store.put_look(lk)
        return lk

    def create_clothing(self, name: str, description: str = "", design_traits: str = "",
                        materials: Optional[List[str]] = None, colors: Optional[List[str]] = None,
                        reference_asset_ids: Optional[List[str]] = None,
                        cover_asset_id: Optional[str] = None, tags: Optional[List[str]] = None) -> Clothing:
        c = Clothing(clothing_id=new_clothing_id(), name=name, description=description,
                     design_traits=design_traits, materials=list(materials or []),
                     colors=list(colors or []), reference_asset_ids=list(reference_asset_ids or []),
                     cover_asset_id=cover_asset_id, tags=list(tags or []))
        self.store.put_clothing(c)
        return c

    def create_prop(self, name: str, description: str = "",
                    reference_asset_ids: Optional[List[str]] = None,
                    cover_asset_id: Optional[str] = None, tags: Optional[List[str]] = None) -> Prop:
        p = Prop(prop_id=new_prop_id(), name=name, description=description,
                 reference_asset_ids=list(reference_asset_ids or []),
                 cover_asset_id=cover_asset_id, tags=list(tags or []))
        self.store.put_prop(p)
        return p

    def create_reference_asset(self, view: str = "", purpose: str = "", local: Optional[str] = None,
                              url: Optional[str] = None, checksum: Optional[str] = None,
                              background_type: str = "", source: str = "manual",
                              media_type: str = "image") -> ReferenceAsset:
        a = ReferenceAsset(asset_id=new_reference_asset_id(), media_type=media_type, local=local,
                           url=url, checksum=checksum, view=view, purpose=purpose,
                           background_type=background_type, source=source)
        self.store.put_reference_asset(a)
        return a

    def update_look(self, look_id: str, mutator) -> CharacterLook:
        return self.store.update_look(look_id, mutator)

    def update_clothing(self, clothing_id: str, mutator) -> Clothing:
        return self.store.update_clothing(clothing_id, mutator)

    def update_prop(self, prop_id: str, mutator) -> Prop:
        return self.store.update_prop(prop_id, mutator)

    def update_reference_asset(self, asset_id: str, mutator) -> ReferenceAsset:
        return self.store.update_reference_asset(asset_id, mutator)

    def delete_look(self, look_id: str) -> bool:
        return self.store.delete_look(look_id)

    def delete_clothing(self, clothing_id: str) -> bool:
        return self.store.delete_clothing(clothing_id)

    def delete_prop(self, prop_id: str) -> bool:
        return self.store.delete_prop(prop_id)

    def delete_reference_asset(self, asset_id: str) -> bool:
        return self.store.delete_reference_asset(asset_id)
