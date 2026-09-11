# tests/test_resources_phase1c5.py
# Production Resources V1 — Phase 1C5 验收测试（CharacterReferenceComposition 收口）。
#
# Commit A：schema v2 惰性迁移 + Composition typed models（本文件首批）。
# 后续 commit 追加：Prop review/approval、Composition CRUD/generation/single-flight/
# recovery、Visual Review、Approval、Shared publish/import（对应执行文档 §21 T1-T40）。
import asyncio
import json
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.resources.models import (
    RESOURCES_SCHEMA_VERSION,
    CharacterReferenceComposition,
    CompositionAnchor,
    CompositionPropBinding,
    CompositionRelation,
    RequiredVisibility,
    ResourceDocument,
    ResourceOrigin,
    ResourceSchemaError,
    composition_revision,
    document_revision,
    new_composition_id,
    new_prop_id,
    Prop,
)
from src.resources.store import ResourceStore


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolated_locks():
    _clear_for_test()
    yield
    _clear_for_test()


# ============ Commit A：schema v2 惰性迁移（§5）============

def _v1_document_raw() -> dict:
    """v1 形态文档（无 compositions key），带一个 look。"""
    return {
        "schema_version": 1,
        "looks": {"look_v1": {"look_id": "look_v1", "character_name": "Alice",
                              "label": "Alice · Black Coat"}},
        "clothings": {},
        "props": {},
        "reference_assets": {},
    }


def test_v1_file_reads_with_empty_compositions_and_no_write_back(tmp_path):
    """v1 文件可读：in-memory compositions={}；纯读绝不写回 v2（字节不变）。"""
    pdir = tmp_path / "p1"
    (pdir / "output").mkdir(parents=True)
    rs = ResourceStore(pdir, "p1")
    rs.path.write_text(json.dumps(_v1_document_raw(), ensure_ascii=False), encoding="utf-8")
    raw_before = rs.path.read_bytes()

    doc = rs.load()
    assert doc.schema_version == RESOURCES_SCHEMA_VERSION == 2   # in-memory 规范化
    assert doc.compositions == {}
    assert set(doc.looks.keys()) == {"look_v1"}                  # 旧数据完整
    # 纯读多次：磁盘字节逐字节不变（不存在"读时自动迁移"）
    for _ in range(3):
        again = rs.load()
        assert again.schema_version == 2
    assert rs.path.read_bytes() == raw_before


def test_first_mutation_on_v1_file_persists_v2(tmp_path):
    """v1 文件第一次真实 mutation 才持久化 v2（含 compositions 键）。"""
    pdir = tmp_path / "p2"
    (pdir / "output").mkdir(parents=True)
    rs = ResourceStore(pdir, "p2")
    rs.path.write_text(json.dumps(_v1_document_raw(), ensure_ascii=False), encoding="utf-8")

    doc = rs.load()
    doc.props["prop_x"] = Prop(prop_id=new_prop_id(), name="Wooden Sword")
    rs.save(doc)

    raw = json.loads(rs.path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2                     # 已持久化升级
    assert raw["compositions"] == {}                      # 新 collection 就位
    assert set(raw["looks"].keys()) == {"look_v1"}        # 旧数据无损（非破坏性迁移）


def test_v2_document_with_composition_round_trip(tmp_path):
    pdir = tmp_path / "p3"
    (pdir / "output").mkdir(parents=True)
    rs = ResourceStore(pdir, "p3")
    comp = CharacterReferenceComposition(
        composition_id=new_composition_id(), label="Alice · Black Coat · Armed",
        character_name="Alice", look_id="look_v1",
        prop_bindings=[
            CompositionPropBinding(prop_id="prop_sword", relation=CompositionRelation.HELD.value,
                                   anchor=CompositionAnchor.RIGHT_HAND.value,
                                   visual_state_note="grip firm"),
            CompositionPropBinding(prop_id="prop_compass", relation=CompositionRelation.HELD.value,
                                   anchor=CompositionAnchor.LEFT_HAND.value),
        ],
        pose_note="standing, relaxed", composition_description="production composition plate",
        origin=ResourceOrigin(source="shared_library", shared_resource_id="scomp_x",
                              shared_version=3, imported_at="2026-09-11T00:00:00"),
    )
    doc = ResourceDocument()
    doc.compositions[comp.composition_id] = comp
    rs.save(doc)

    back = rs.load()
    c2 = back.compositions[comp.composition_id]
    assert c2.prop_bindings[0].prop_id == "prop_sword"
    assert c2.prop_bindings[0].anchor == "right_hand"
    assert c2.prop_bindings[1].anchor == "left_hand"
    assert c2.origin.shared_version == 3
    assert composition_revision(c2) == composition_revision(comp)   # round-trip 内容恒等


def test_unknown_future_schema_still_fail_closed(tmp_path):
    """v2 之后未知 schema（≥3）继续 fail closed。"""
    pdir = tmp_path / "p4"
    (pdir / "output").mkdir(parents=True)
    rs = ResourceStore(pdir, "p4")
    rs.path.write_text(json.dumps({"schema_version": 3, "looks": {}}), encoding="utf-8")
    with pytest.raises(ResourceSchemaError):
        rs.load()
    # service 层同口径
    rs2 = ResourceStore(tmp_path / "p5", "p5")
    (tmp_path / "p5" / "output").mkdir(parents=True)
    rs2.path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(ResourceSchemaError):
        rs2.load()


# ============ Commit A：Composition typed models ============

def test_composition_id_and_revision_semantics():
    cid = new_composition_id()
    assert cid.startswith("comp_") and len(cid) == len("comp_") + 12
    c = CharacterReferenceComposition(composition_id=cid, character_name="Alice", look_id="look_1")
    r1 = composition_revision(c)
    assert len(r1) == 8                                   # sha256[:8]（OCC token 同口径）
    assert composition_revision(CharacterReferenceComposition.from_dict(c.to_dict())) == r1
    c.label = "renamed"                                    # 内容变 → revision 变
    assert composition_revision(c) != r1


def test_binding_enum_surface_is_finite():
    """relation/anchor/required_visibility 是有限 enum（不做万能 free JSON——§4）。"""
    assert {e.value for e in CompositionRelation} == {"held", "carried", "worn", "attached", "near"}
    assert {e.value for e in CompositionAnchor} == {
        "right_hand", "left_hand", "back", "waist", "neck", "beside_character"}
    assert {e.value for e in RequiredVisibility} == {"required", "optional"}


def test_binding_from_dict_unknown_fields_filtered():
    b = CompositionPropBinding.from_dict({"prop_id": "prop_1", "relation": "held",
                                          "anchor": "right_hand", "future_field": "x"})
    assert b.prop_id == "prop_1" and b.anchor == "right_hand"
    assert not hasattr(b, "future_field")
    # 默认值：required_visibility=required
    assert b.required_visibility == "required"
    assert b.visual_state_note == ""


def test_composition_document_revision_includes_compositions():
    """document revision 覆盖 compositions 段（OCC 语义完整）。"""
    doc = ResourceDocument()
    r1 = document_revision(doc)
    doc.compositions[new_composition_id()] = CharacterReferenceComposition(
        composition_id=new_composition_id(), character_name="Alice", look_id="look_1")
    assert document_revision(doc) != r1
