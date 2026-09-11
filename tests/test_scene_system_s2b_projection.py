# tests/test_scene_system_s2b_projection.py
# Scene System V1 — Phase S2B：ViewRelevantSceneState 投影 + SceneInstanceView CRUD
# （§39 T7/T8/T12/T13/T14 + §2.C）。
#
# T12 visible state projection（全局态恒入；可见 anchor states 进入）
# T13 invisible anchor state excluded（south_stairs 损坏不进朝北 view 投影——§33 核心价值）
# T14 invisible prop placement excluded
# T7 create SceneInstanceView valid pair（身份 = (instance, view) 对）
# T8 mismatched Location/View rejected（§2.C location_mismatch）
# + scene_instance_not_found / location_view_not_found / pair 唯一性
import asyncio
import json
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.models import Project, Scene
from src.resources.models import Prop as ResourceProp, ResourceDocument
from src.resources.store import ResourceStore
from src.scene_system.store import SceneSystemStore
from src.scene_system.service import SceneSystemService
from src.scene_system.visual_service import SceneVisualService
from src.scene_system.state_projection import project_scene_state_for_view


def make_pdir(tmp_path: Path) -> Path:
    name = tmp_path.name
    pdir = tmp_path / name
    proj = Project(name=name)
    proj.scenes = [Scene(scene_id="s09", summary="x")]
    ProjectStore(pdir).save(proj)
    rdoc = ResourceDocument()
    for pid in ("prop_sword01", "prop_hidden01"):
        rdoc.props[pid] = ResourceProp(prop_id=pid, name=pid)
    ResourceStore(pdir, pdir.name).save(rdoc)
    return pdir


async def _seed(svc: SceneSystemService):
    async def drev():
        return svc.document_overview()["document_revision"]

    loc = (await svc.create_location(name="灯塔监听室",
                                     identity_description="circular control room",
                                     expected_document_revision=await drev()))["target"]
    a_n = (await svc.create_anchor(location_id=loc, name="north door", kind="door",
                                   expected_document_revision=await drev()))["target"]
    a_w1 = (await svc.create_anchor(location_id=loc, name="west window 01", kind="window",
                                    expected_document_revision=await drev()))["target"]
    a_w2 = (await svc.create_anchor(location_id=loc, name="west window 02", kind="window",
                                    expected_document_revision=await drev()))["target"]
    a_s = (await svc.create_anchor(location_id=loc, name="south stairs", kind="stairs",
                                   expected_document_revision=await drev()))["target"]
    loc2 = (await svc.create_location(name="码头",
                                      expected_document_revision=await drev()))["target"]
    v_other = (await svc.create_view(location_id=loc2, label="other loc view",
                                     expected_document_revision=await drev()))["target"]
    v_north = await svc.create_view(location_id=loc, label="North to South",
                                    visible_anchor_ids=[a_n, a_w1, a_w2],
                                    expected_document_revision=await drev())
    v_south = await svc.create_view(location_id=loc, label="South to North",
                                    visible_anchor_ids=[a_s],
                                    expected_document_revision=await drev())
    inst = (await svc.create_scene_instance(story_scene_id="s09", location_id=loc,
                                            expected_document_revision=await drev()))["target"]
    rp = await svc.patch_scene_state(
        inst, {"time_of_day": "night", "weather": "storm", "lighting": "red emergency",
               "environment_conditions": "floor wet",
               "anchor_states": {a_w2: {"condition": "broken"},
                                 a_s: {"condition": "damaged"}},
               "prop_placements": {"prop_sword01": {"anchor_id": a_w1, "state": "intact"},
                                   "prop_hidden01": {"anchor_id": a_s, "state": "intact"}}},
        expected_revision=svc.read_scene_instance(inst)["revision"])
    assert rp["ok"], rp
    return {"loc": loc, "loc2": loc2, "a_n": a_n, "a_w1": a_w1, "a_w2": a_w2, "a_s": a_s,
            "v_north": v_north["target"], "v_south": v_south["target"], "v_other": v_other,
            "inst": inst}


def _doc(pdir: Path):
    return SceneSystemStore(pdir, pdir.name).load()


# ============ T12/T13/T14：投影规则 ============
def test_t12_visible_state_projection(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed(svc))
    doc = _doc(pdir)
    pv = project_scene_state_for_view(
        doc.scene_instances[ids["inst"]], doc.views[ids["v_north"]],
        [a for a in doc.anchors.values() if a.location_id == ids["loc"]])
    # 全局态恒入
    assert pv["time_of_day"] == "night" and pv["weather"] == "storm"
    assert pv["lighting"] == "red emergency" and pv["environment_conditions"] == "floor wet"
    # 可见 anchor state（west window 02 broken）进入
    assert pv["anchor_states"].get(ids["a_w2"], {}).get("condition") == "broken"
    # 可见 prop placement 进入
    assert pv["prop_placements"].get("prop_sword01", {}).get("anchor_id") == ids["a_w1"]


def test_t13_invisible_anchor_state_excluded(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed(svc))
    doc = _doc(pdir)
    pv_north = project_scene_state_for_view(
        doc.scene_instances[ids["inst"]], doc.views[ids["v_north"]],
        [a for a in doc.anchors.values() if a.location_id == ids["loc"]])
    assert ids["a_s"] not in pv_north["anchor_states"], \
        "不可见 anchor（south stairs damaged）不进朝北 view 投影（§33 核心价值）"
    pv_south = project_scene_state_for_view(
        doc.scene_instances[ids["inst"]], doc.views[ids["v_south"]],
        [a for a in doc.anchors.values() if a.location_id == ids["loc"]])
    assert pv_south["anchor_states"].get(ids["a_s"], {}).get("condition") == "damaged"
    assert ids["a_w2"] not in pv_south["anchor_states"], "反向同样成立"


def test_t14_invisible_prop_placement_excluded(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed(svc))
    doc = _doc(pdir)
    pv_north = project_scene_state_for_view(
        doc.scene_instances[ids["inst"]], doc.views[ids["v_north"]],
        [a for a in doc.anchors.values() if a.location_id == ids["loc"]])
    assert "prop_hidden01" not in pv_north["prop_placements"], \
        "不可见 anchor 上的 prop placement 不进投影"
    pv_south = project_scene_state_for_view(
        doc.scene_instances[ids["inst"]], doc.views[ids["v_south"]],
        [a for a in doc.anchors.values() if a.location_id == ids["loc"]])
    assert "prop_hidden01" in pv_south["prop_placements"]
    assert "prop_sword01" not in pv_south["prop_placements"]


def test_projection_pure_and_deterministic(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed(svc))
    doc = _doc(pdir)
    anchors = [a for a in doc.anchors.values() if a.location_id == ids["loc"]]
    p1 = project_scene_state_for_view(doc.scene_instances[ids["inst"]],
                                      doc.views[ids["v_north"]], anchors)
    p2 = project_scene_state_for_view(doc.scene_instances[ids["inst"]],
                                      doc.views[ids["v_north"]], anchors)
    assert p1 == p2
    # 不改 canonical（derived projection 零副作用）
    doc2 = _doc(pdir)
    assert doc2.scene_instances[ids["inst"]].state.to_dict() == \
        doc.scene_instances[ids["inst"]].state.to_dict()


# ============ T7/T8：SceneInstanceView CRUD ============
def test_t7_create_scene_instance_view_valid_pair(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed(svc))

    async def flow():
        drev = svc.document_overview()["document_revision"]
        r = await vs.create_scene_instance_view(
            scene_instance_id=ids["inst"], location_view_id=ids["v_north"],
            label="s09 · North to South", expected_document_revision=drev)
        assert r["ok"], r
        got = vs.read_scene_instance_view(r["target"])
        assert got["scene_instance_id"] == ids["inst"]
        assert got["location_view_id"] == ids["v_north"]
        assert got["generation_status"] == "none"
        assert got["approval_status"] == "unreviewed"
        # pair 唯一（§4 稳定身份：重新生成复用对象，不新建）
        drev2 = svc.document_overview()["document_revision"]
        r2 = await vs.create_scene_instance_view(
            scene_instance_id=ids["inst"], location_view_id=ids["v_north"],
            expected_document_revision=drev2)
        assert not r2["ok"] and r2["error"] == "scene_instance_view_already_exists"
        assert r2["scene_instance_view_id"] == r["target"]
        # 同 instance 不同 view = 不同身份，可创建
        drev3 = svc.document_overview()["document_revision"]
        r3 = await vs.create_scene_instance_view(
            scene_instance_id=ids["inst"], location_view_id=ids["v_south"],
            expected_document_revision=drev3)
        assert r3["ok"], r3
        assert len(vs.list_scene_instance_views(scene_instance_id=ids["inst"])) == 2
    asyncio.run(flow())


def test_t8_mismatched_location_rejected(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed(svc))

    async def flow():
        # §2.C：instance.location_id 必须与 LocationView.location_id 相同
        drev = svc.document_overview()["document_revision"]
        r = await vs.create_scene_instance_view(
            scene_instance_id=ids["inst"], location_view_id=ids["v_other"],
            expected_document_revision=drev)
        assert not r["ok"] and r["error"] == "location_mismatch"
        # instance 不存在
        r2 = await vs.create_scene_instance_view(
            scene_instance_id="scin_missing01", location_view_id=ids["v_north"],
            expected_document_revision=drev)
        assert not r2["ok"] and r2["error"] == "scene_instance_not_found"
        # view 不存在
        r3 = await vs.create_scene_instance_view(
            scene_instance_id=ids["inst"], location_view_id="view_missing01",
            expected_document_revision=drev)
        assert not r3["ok"] and r3["error"] == "location_view_not_found"
    asyncio.run(flow())
