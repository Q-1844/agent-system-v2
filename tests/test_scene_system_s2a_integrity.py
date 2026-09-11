# tests/test_scene_system_s2a_integrity.py
# Scene System V1 — Phase S2A §6：S1 integrity preflight（小型完整性测试）。
#
# A/B/C. 删除仍被 LocationView.visible_anchor_ids / SetLayout.placements /
#        SceneState（anchor_states + prop_placements）引用的 Anchor：
#        不能静默制造悬空引用 → anchor_in_use（fail closed，磁盘零污染）。
# D. SceneState 引用的 Prop 在 Resources 中被删 → read 诚实暴露 missing_props，
#    不 crash、不假装存在；resources.json 损坏 → props_lookup_unavailable（诚实不确定）。
# E. PATCH 语义：anchor_states/prop_placements 为整 dict 替换（传入即整体替换，
#    未传不动）；字符串字段 partial。文档语义与实现一致。
#
# （S1 已有 zero-side-effect/OCC/schema 测试不重复；本文件只覆盖 §6 指定的完整性缺口。）
import asyncio
import json
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.models import Project, Scene
from src.resources.models import Prop as ResourceProp, ResourceDocument
from src.resources.store import ResourceStore
from src.scene_system.service import SceneSystemService


def make_project(base: Path, scene_ids=("s01",)) -> Path:
    name = base.name
    pdir = base / name
    proj = Project(name=name)
    proj.scenes = [Scene(scene_id=sid, summary=f"段 {sid}") for sid in scene_ids]
    ProjectStore(pdir).save(proj)
    return pdir


def write_prop_resource(pdir: Path, prop_id: str) -> None:
    prop = ResourceProp(prop_id=prop_id, name="木剑")
    doc = ResourceDocument()
    doc.props[prop_id] = prop
    ResourceStore(pdir, pdir.name).save(doc)


@pytest.fixture
def pdir(tmp_path) -> Path:
    return make_project(tmp_path)


@pytest.fixture
def svc(pdir) -> SceneSystemService:
    return SceneSystemService(pdir, pdir.name)


async def _seed_full_graph(svc: SceneSystemService, pdir: Path, prop_id: str) -> dict:
    """location + 3 anchors + view + layout + instance（anchor_states/prop_placements 引用锚点）。"""
    write_prop_resource(pdir, prop_id)
    async def doc_rev():
        return svc.document_overview()["document_revision"]

    r = await svc.create_location(name="灯塔监听室", expected_document_revision=await doc_rev())
    loc = r["target"]
    a_view = (await svc.create_anchor(location_id=loc, name="北门", kind="door",
                                      expected_document_revision=await doc_rev()))["target"]
    a_layout = (await svc.create_anchor(location_id=loc, name="主控台", kind="furniture",
                                        expected_document_revision=await doc_rev()))["target"]
    a_state = (await svc.create_anchor(location_id=loc, name="西窗", kind="window",
                                       expected_document_revision=await doc_rev()))["target"]
    rv = await svc.create_view(location_id=loc, label="hero", visible_anchor_ids=[a_view],
                               expected_document_revision=await doc_rev())
    assert rv["ok"], rv
    rl = await svc.create_layout(location_id=loc, orientation_convention="north_up",
                                 placements=[{"anchor_id": a_layout, "zone": "center"}],
                                 expected_document_revision=await doc_rev())
    assert rl["ok"], rl
    ri = await svc.create_scene_instance(story_scene_id="s01", location_id=loc,
                                         expected_document_revision=await doc_rev())
    assert ri["ok"], ri
    inst = ri["target"]
    rr = await svc.patch_scene_state(
        inst, {"anchor_states": {a_state: {"condition": "intact"}},
               "prop_placements": {prop_id: {"anchor_id": a_state, "state": "intact"}}},
        expected_revision=svc.read_scene_instance(inst)["revision"])
    assert rr["ok"], rr
    return {"loc": loc, "a_view": a_view, "a_layout": a_layout, "a_state": a_state,
            "inst": inst}


def _scene_json_bytes(pdir: Path) -> bytes:
    return (pdir / "output" / "scene_system.json").read_bytes()


# ============ A/B/C：被引用 Anchor 删除 fail closed ============
def test_a_anchor_referenced_by_view_cannot_delete(svc, pdir):
    ids = asyncio.run(_seed_full_graph(svc, pdir, "prop_integrity01"))
    rev = svc.read_anchor(ids["a_view"])["revision"]
    before = _scene_json_bytes(pdir)
    r = asyncio.run(svc.delete_anchor(ids["a_view"], expected_revision=rev))
    assert not r["ok"] and r["error"] == "anchor_in_use", r
    assert "view_visible_anchor_ids" in (r.get("used_by") or {})
    assert _scene_json_bytes(pdir) == before, "失败路径磁盘零污染"


def test_b_anchor_referenced_by_layout_cannot_delete(svc, pdir):
    ids = asyncio.run(_seed_full_graph(svc, pdir, "prop_integrity01"))
    rev = svc.read_anchor(ids["a_layout"])["revision"]
    r = asyncio.run(svc.delete_anchor(ids["a_layout"], expected_revision=rev))
    assert not r["ok"] and r["error"] == "anchor_in_use", r
    assert "layout_placements" in (r.get("used_by") or {})


def test_c_anchor_referenced_by_scene_state_cannot_delete(svc, pdir):
    ids = asyncio.run(_seed_full_graph(svc, pdir, "prop_integrity01"))
    # anchor_states 引用
    rev = svc.read_anchor(ids["a_state"])["revision"]
    r = asyncio.run(svc.delete_anchor(ids["a_state"], expected_revision=rev))
    assert not r["ok"] and r["error"] == "anchor_in_use", r
    assert "scene_instance_state" in (r.get("used_by") or {})
    # prop_placements 的 anchor_id 引用：换一个未被 anchor_states 引用的位置引用
    async def place_prop_on_clean_anchor():
        # a_view 只被 view 引用（layout/state 未引用它）；把 prop 放到 a_view 上
        inst_rev = svc.read_scene_instance(ids["inst"])["revision"]
        rr = await svc.patch_scene_state(
            ids["inst"],
            {"anchor_states": {ids["a_state"]: {"condition": "intact"}},
             "prop_placements": {"prop_integrity01": {"anchor_id": ids["a_view"],
                                                      "state": "intact"}}},
            expected_revision=inst_rev)
        assert rr["ok"], rr
        return svc.read_anchor(ids["a_view"])["revision"]
    rev2 = asyncio.run(place_prop_on_clean_anchor())
    r2 = asyncio.run(svc.delete_anchor(ids["a_view"], expected_revision=rev2))
    assert not r2["ok"] and r2["error"] == "anchor_in_use", r2
    used = " ".join(r2.get("used_by", {}).get("scene_instance_state", []))
    assert "prop_placements" in used, r2


# ============ D：Prop 在 Resources 中被删 → read 诚实暴露 ============
def test_d_missing_prop_annotated_on_read(svc, pdir):
    ids = asyncio.run(_seed_full_graph(svc, pdir, "prop_doomed01"))
    # 前置：prop 存在 → 无 missing
    got = svc.read_scene_instance(ids["inst"])
    assert got["missing_props"] == [] and got["props_lookup_unavailable"] is False
    # 从 resources.json 删除该 Prop（直接改 Resources canonical）
    doc = ResourceStore(pdir, pdir.name).load()
    del doc.props["prop_doomed01"]
    ResourceStore(pdir, pdir.name).save(doc)
    # read：诚实标注 missing_props，不 crash、不假装存在
    got2 = svc.read_scene_instance(ids["inst"])
    assert got2["missing_props"] == ["prop_doomed01"], got2
    assert got2["props_lookup_unavailable"] is False
    # list 同样标注
    listed = svc.list_scene_instances(story_scene_id="s01")
    assert listed and listed[0]["missing_props"] == ["prop_doomed01"]
    # SceneState 数据本身不被悄悄清除（诚实暴露，不静默改写 canonical）
    assert "prop_doomed01" in listed[0]["state"]["prop_placements"]


def test_d2_corrupt_resources_read_annotates_unavailable_not_crash(svc, pdir):
    ids = asyncio.run(_seed_full_graph(svc, pdir, "prop_integrity02"))
    (pdir / "output" / "resources.json").write_text("{corrupt", encoding="utf-8")
    got = svc.read_scene_instance(ids["inst"])   # 不抛异常
    assert got["missing_props"] == []
    assert got["props_lookup_unavailable"] is True, "损坏时诚实标注不确定，不假装存在/缺失"


# ============ E：PATCH 整 dict 替换语义 ============
def test_e_dict_fields_whole_replacement_semantics(svc, pdir):
    ids = asyncio.run(_seed_full_graph(svc, pdir, "prop_integrity03"))
    inst = ids["inst"]
    # 当前 anchor_states={a_state}，prop_placements={prop_integrity03}
    got = svc.read_scene_instance(inst)["state"]
    assert set(got["anchor_states"]) == {ids["a_state"]}
    # patch anchor_states 传另一个 anchor 的 state → 整 dict 替换（旧的消失）
    async def flow():
        rev = svc.read_scene_instance(inst)["revision"]
        r = await svc.patch_scene_state(
            inst, {"anchor_states": {ids["a_layout"]: {"condition": "open"}}},
            expected_revision=rev)
        assert r["ok"], r
    asyncio.run(flow())
    got2 = svc.read_scene_instance(inst)["state"]
    assert set(got2["anchor_states"]) == {ids["a_layout"]}, \
        "整 dict 替换语义：传入 dict 整体替换，不是 per-key merge"
    # 未传的 dict 字段保持不变
    assert set(got2["prop_placements"]) == {"prop_integrity03"}
    # 字符串字段 partial：未传字段保持
    assert got2["time_of_day"] == got["time_of_day"]
