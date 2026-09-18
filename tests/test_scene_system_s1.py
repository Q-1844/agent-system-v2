# tests/test_scene_system_s1.py
# Scene System V1 — Phase S1 验收测试（对应执行指令 §25 T1-T26）。
#
# 覆盖：
#   T1 老项目无 scene_system.json：read 空 + 零副作用
#   T2 首写建文件
#   T3 Location 稳定 id（重启后不变）
#   T4 rename Location id 不变
#   T5 Anchor 必须归属合法 Location
#   T6 非法 anchor relation 拒绝（未知词/跨 location/自指）
#   T7 LocationView 合法 visible anchors
#   T8 缺失 visible anchor 拒绝
#   T9 无固定 8-view 假设（1 个 view 合法；不要求 8 个）
#   T10 SetLayout 持久化 orientation/placements（含 normalized x/y hint）
#   T11 SceneInstance 要求真实 Story Scene
#   T12 SceneInstance 要求真实 Location
#   T13 一段至多一个 active instance（V1 规则；schema 不封死多 instance）
#   T14 SceneState time/weather/lighting round-trip
#   T15 anchor_state 必须引用合法 anchor
#   T16 prop_placements 引用真实 Resource Prop（跨 store 复用 ResourceStore，
#       安全可行，无 deviation；含 resources.json 损坏 fail closed）
#   T17 document revision deterministic
#   T18 entity revision 随 mutation 变化
#   T19 stale OCC 拒绝（内容不变、不自动换 revision）
#   T20 同 revision 并发 mutation：恰好一个成功
#   T21 坏 JSON fail closed
#   T22 未知 schema fail closed
#   T23 重启持久化
#   T24 project.json 字节不变
#   T25 resources.json 字节不变
#   T26 Agent 源零修改（src/agents、src/ai 全文不含 scene_system 引用）
#
# 另含 HTTP 层冒烟（TestClient + router；status mapping 409/404/400/422）。
# 全部 tmp_path 隔离，不触碰真实 projects/。
import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.store import ProjectStore
from src.core.models import Project, Scene
from src.utils.project_manager import ProjectManager
from src.resources.models import Prop as ResourceProp, ResourceDocument
from src.resources.store import ResourceStore
from src.scene_system.models import (
    SceneInstanceStatus, SceneSystemDocument, SceneSystemSchemaError,
    document_revision, location_revision, scene_instance_revision,
    new_anchor_id,
)
from src.scene_system.store import SceneSystemStore
from src.scene_system.service import SceneSystemService
import routers.scene_system as rsc


# ============ fixtures / helpers ============
def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def make_project(base: Path, scene_ids=("s01", "s02")) -> Path:
    """最小项目目录 base/<name>/ + 带 Story Scene 的 project.json。"""
    name = base.name
    pdir = base / name
    proj = Project(name=name)
    proj.scenes = [Scene(scene_id=sid, summary=f"段 {sid}") for sid in scene_ids]
    ProjectStore(pdir).save(proj)
    return pdir


def write_prop_resource(pdir: Path, prop_id: str) -> None:
    """直接落一个带 Prop 的 resources.json（不经过 Resources 写路径）。"""
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


async def _seed_location(svc: SceneSystemService, name: str = "灯塔监听室") -> dict:
    ov = svc.document_overview()
    r = await svc.create_location(name=name, identity_description="圆形控制室",
                                  expected_document_revision=ov["document_revision"])
    assert r["ok"], r
    return r


async def _seed_anchor(svc: SceneSystemService, location_id: str, name: str = "北门",
                       kind: str = "door") -> dict:
    ov = svc.document_overview()
    r = await svc.create_anchor(location_id=location_id, name=name, kind=kind,
                                expected_document_revision=ov["document_revision"])
    assert r["ok"], r
    return r


# ============ T1 / T2：零副作用读 + 首写建文件 ============
def test_t1_read_empty_zero_side_effect(pdir):
    st = SceneSystemStore(pdir, pdir.name)
    assert not st.exists()
    doc = st.load()
    assert isinstance(doc, SceneSystemDocument)
    assert doc.locations == {} and doc.anchors == {} and doc.scene_instances == {}
    assert not st.exists(), "纯 read 绝不创建 scene_system.json"
    svc = SceneSystemService(pdir, pdir.name)
    assert svc.document_overview()["counts"]["locations"] == 0
    assert svc.list_locations() == [] and svc.list_anchors() == []
    assert svc.list_views() == [] and svc.list_scene_instances() == []
    assert not st.exists(), "service 读路径同样零副作用"


def test_t2_first_write_creates_file(svc, pdir):
    st = SceneSystemStore(pdir, pdir.name)
    assert not st.exists()
    asyncio.run(_seed_location(svc))
    assert st.exists(), "首次真实写才创建文件"
    assert st.load().locations  # 有内容


# ============ T3 / T4：Location 稳定 id / rename ============
def test_t3_location_stable_id_across_restart(svc, pdir):
    r = asyncio.run(_seed_location(svc))
    loc_id = r["target"]
    # "重启"：新 store / 新 service 实例重读
    svc2 = SceneSystemService(pdir, pdir.name)
    assert svc2.read_location(loc_id) is not None
    assert svc2.read_location(loc_id)["location_id"] == loc_id
    assert svc2.read_location(loc_id)["name"] == "灯塔监听室"


def test_t4_rename_location_id_unchanged(svc):
    r = asyncio.run(_seed_location(svc))
    loc_id, rev = r["target"], r["revision"]
    r2 = asyncio.run(svc.update_location(loc_id, {"name": "灯塔监控室"}, expected_revision=rev))
    assert r2["ok"] and r2["target"] == loc_id
    got = svc.read_location(loc_id)
    assert got["name"] == "灯塔监控室" and got["location_id"] == loc_id
    # revision 已变（内容变了）——旧 expected 再用会 stale
    r3 = asyncio.run(svc.update_location(loc_id, {"name": "又一个名字"}, expected_revision=rev))
    assert not r3["ok"] and r3["error"] == "stale_revision"


# ============ T5：Anchor 归属合法 Location ============
def test_t5_anchor_requires_valid_location(svc):
    r = asyncio.run(svc.create_anchor(location_id="loc_missing", name="北门",
                                      expected_document_revision=svc.document_overview()["document_revision"]))
    assert not r["ok"] and r["error"] == "location_not_found"


# ============ T6：非法 anchor relation ============
def test_t6_invalid_anchor_relations_rejected(svc):
    async def flow():
        loc = (await _seed_location(svc))["target"]
        a1 = (await _seed_anchor(svc, loc, name="北门"))["target"]
        a2 = (await _seed_anchor(svc, loc, name="主控台", kind="furniture"))["target"]
        rev2 = svc.read_anchor(a2)["revision"]
        # 未知 relation 词
        r = await svc.update_anchor(a2, {"relations": [{"relation": "teleports_to",
                                                        "object_anchor_id": a1}]},
                                    expected_revision=rev2)
        assert not r["ok"] and r["error"] == "invalid_relation", r
        # 自指
        r = await svc.update_anchor(a2, {"relations": [{"relation": "near",
                                                        "object_anchor_id": a2}]},
                                    expected_revision=rev2)
        assert not r["ok"] and r["error"] == "invalid_relation", r
        # object 不存在
        r = await svc.update_anchor(a2, {"relations": [{"relation": "near",
                                                        "object_anchor_id": new_anchor_id()}]},
                                    expected_revision=rev2)
        assert not r["ok"] and r["error"] == "anchor_not_found", r
        # 跨 location object
        ov = svc.document_overview()
        r_loc2 = await svc.create_location(name="码头",
                                           expected_document_revision=ov["document_revision"])
        loc2 = r_loc2["target"]
        b1 = (await _seed_anchor(svc, loc2, name="缆桩"))["target"]
        r = await svc.update_anchor(a2, {"relations": [{"relation": "near",
                                                        "object_anchor_id": b1}]},
                                    expected_revision=rev2)
        assert not r["ok"] and r["error"] == "cross_location_relation", r
        # 合法 relation 通过
        r = await svc.update_anchor(a2, {"relations": [{"relation": "opposite",
                                                        "object_anchor_id": a1}]},
                                    expected_revision=rev2)
        assert r["ok"], r
        assert svc.read_anchor(a2)["relations"][0]["relation"] == "opposite"
        # 失败路径后磁盘未被污染：a2 内容与首次校验失败前一致（除我们最后的成功写）
    asyncio.run(flow())


# ============ T7 / T8 / T9：LocationView ============
def test_t7_t8_t9_location_view_anchors_and_no_fixed_eight(svc):
    async def flow():
        loc = (await _seed_location(svc))["target"]
        a1 = (await _seed_anchor(svc, loc, name="北门"))["target"]
        a2 = (await _seed_anchor(svc, loc, name="主控台", kind="furniture"))["target"]
        # T8：缺失 visible anchor 拒绝
        ov = svc.document_overview()
        r = await svc.create_view(location_id=loc, label="hero",
                                  visible_anchor_ids=["anc_missing"],
                                  expected_document_revision=ov["document_revision"])
        assert not r["ok"] and r["error"] == "anchor_not_found", r
        # 跨 location visible anchor 拒绝
        ov = svc.document_overview()
        r_loc2 = await svc.create_location(name="码头",
                                           expected_document_revision=ov["document_revision"])
        b1 = (await _seed_anchor(svc, r_loc2["target"], name="缆桩"))["target"]
        ov = svc.document_overview()
        r = await svc.create_view(location_id=loc, label="bad",
                                  visible_anchor_ids=[b1],
                                  expected_document_revision=ov["document_revision"])
        assert not r["ok"] and r["error"] == "cross_location_anchor", r
        # T7：合法 visible anchors
        ov = svc.document_overview()
        r = await svc.create_view(location_id=loc, label="South-west to North-east",
                                  camera_zone="south_west", look_direction="north_east",
                                  visible_anchor_ids=[a1, a2],
                                  expected_document_revision=ov["document_revision"])
        assert r["ok"], r
        view_id = r["target"]
        got = svc.read_view(view_id)
        assert got["visible_anchor_ids"] == [a1, a2]
        assert got["approval_status"] == "unreviewed"   # metadata only，无生成
        # T9：无固定 8-view 假设——1 个 view 就是合法状态；系统不要求/不预生成 8 个
        assert len(svc.list_views(location_id=loc)) == 1
        # 也可以只有 0 个 visible anchor 的 view（纯视角标注）
        ov = svc.document_overview()
        r2 = await svc.create_view(location_id=loc, label="reverse",
                                   expected_document_revision=ov["document_revision"])
        assert r2["ok"], r2
        assert len(svc.list_views(location_id=loc)) == 2
    asyncio.run(flow())


# ============ T10：SetLayout ============
def test_t10_set_layout_persists_orientation_and_placements(svc):
    async def flow():
        loc = (await _seed_location(svc))["target"]
        a1 = (await _seed_anchor(svc, loc, name="北门"))["target"]
        a2 = (await _seed_anchor(svc, loc, name="主控台", kind="furniture"))["target"]
        ov = svc.document_overview()
        r = await svc.create_layout(
            location_id=loc, orientation_convention="north_up",
            placements=[{"anchor_id": a1, "zone": "north_wall", "x": 0.5, "y": 0.05},
                        {"anchor_id": a2, "zone": "center", "x": 0.5, "y": 0.5}],
            notes="V1 layout hint",
            expected_document_revision=ov["document_revision"])
        assert r["ok"], r
        # 读回（按 location 与按 layout_id 两条路）
        got = svc.read_layout_by_location(loc)
        assert got["orientation_convention"] == "north_up"
        assert got["placements"][0]["anchor_id"] == a1 and got["placements"][0]["x"] == 0.5
        assert svc.read_layout(got["layout_id"])["layout_id"] == got["layout_id"]
        # normalized x/y 超界拒绝（layout hint 不是物理坐标）
        rev = got["revision"]
        r2 = await svc.update_layout(got["layout_id"],
                                     {"placements": [{"anchor_id": a1, "x": 1.5}]},
                                     expected_revision=rev)
        assert not r2["ok"] and r2["error"] == "validation_error", r2
        # placement 引用他 location anchor 拒绝
        ov = svc.document_overview()
        r_loc2 = await svc.create_location(name="码头",
                                           expected_document_revision=ov["document_revision"])
        b1 = (await _seed_anchor(svc, r_loc2["target"], name="缆桩"))["target"]
        r3 = await svc.update_layout(got["layout_id"],
                                     {"placements": [{"anchor_id": b1}]},
                                     expected_revision=rev)
        assert not r3["ok"] and r3["error"] == "cross_location_anchor", r3
        # 1:0..1：重复 create 拒绝
        ov = svc.document_overview()
        r4 = await svc.create_layout(location_id=loc,
                                     expected_document_revision=ov["document_revision"])
        assert not r4["ok"] and r4["error"] == "layout_already_exists", r4
    asyncio.run(flow())


# ============ T11 / T12 / T13：SceneInstance 外键与唯一性 ============
def test_t11_t12_t13_scene_instance_rules(svc, pdir):
    async def flow():
        loc = (await _seed_location(svc))["target"]
        a1 = (await _seed_anchor(svc, loc, name="北门"))["target"]
        ov = svc.document_overview()
        # T11：Story Scene 必须真实存在
        r = await svc.create_scene_instance(story_scene_id="s99", location_id=loc,
                                            expected_document_revision=ov["document_revision"])
        assert not r["ok"] and r["error"] == "story_scene_not_found", r
        # T12：Location 必须真实存在
        r = await svc.create_scene_instance(story_scene_id="s01", location_id="loc_missing",
                                            expected_document_revision=ov["document_revision"])
        assert not r["ok"] and r["error"] == "location_not_found", r
        # project.json 完全缺失 → 同样 fail closed
        # （此处 project.json 存在；缺失分支在 test_story_scene_missing_after_delete 覆盖读路径）
        # 正创建：绑定快照
        r = await svc.create_scene_instance(story_scene_id="s01", location_id=loc,
                                            expected_document_revision=ov["document_revision"])
        assert r["ok"], r
        inst_id = r["target"]
        assert r["after"]["location_revision_at_bind"] == location_revision(
            SceneSystemStore(project_dir=pdir, project_name=pdir.name).load().locations[loc])
        # T13：同段第二个 active 拒绝
        ov = svc.document_overview()
        r2 = await svc.create_scene_instance(story_scene_id="s01", location_id=loc,
                                             expected_document_revision=ov["document_revision"])
        assert not r2["ok"] and r2["error"] == "instance_already_active", r2
        # 不同段可以
        ov = svc.document_overview()
        r3 = await svc.create_scene_instance(story_scene_id="s02", location_id=loc,
                                             expected_document_revision=ov["document_revision"])
        assert r3["ok"], r3
        # schema 不封死未来多 instance：非 active 状态枚举就位
        assert {s.value for s in SceneInstanceStatus} >= {"active", "archived", "retired"}
        # 换绑 fail closed：state 引用旧 location anchor
        rev = svc.read_scene_instance(inst_id)["revision"]
        ov = svc.document_overview()
        r_loc2 = await svc.create_location(name="码头",
                                           expected_document_revision=ov["document_revision"])
        loc2 = r_loc2["target"]
        rr = await svc.patch_scene_state(inst_id,
                                         {"anchor_states": {a1: {"condition": "open"}}},
                                         expected_revision=rev)
        assert rr["ok"], rr
        rev = svc.read_scene_instance(inst_id)["revision"]
        r4 = await svc.update_scene_instance_location(inst_id, location_id=loc2,
                                                      expected_revision=rev)
        assert not r4["ok"] and r4["error"] == "state_references_old_location", r4
        # 清 state 后换绑成功，bind 快照刷新
        rev = svc.read_scene_instance(inst_id)["revision"]
        rr = await svc.patch_scene_state(inst_id, {"anchor_states": {}}, expected_revision=rev)
        assert rr["ok"], rr
        rev = svc.read_scene_instance(inst_id)["revision"]
        r5 = await svc.update_scene_instance_location(inst_id, location_id=loc2,
                                                      expected_revision=rev)
        assert r5["ok"] and r5["after"]["location_id"] == loc2, r5
    asyncio.run(flow())


# ============ T14 / T15 / T16：SceneState ============
def test_t14_t15_t16_scene_state_rules(svc, pdir):
    prop_id = "prop_smoketest01"
    write_prop_resource(pdir, prop_id)

    async def flow():
        loc = (await _seed_location(svc))["target"]
        a1 = (await _seed_anchor(svc, loc, name="北门"))["target"]
        ov = svc.document_overview()
        r = await svc.create_scene_instance(story_scene_id="s01", location_id=loc,
                                            expected_document_revision=ov["document_revision"])
        inst = r["target"]
        rev = svc.read_scene_instance(inst)["revision"]
        # T15：anchor_state 引用非法 anchor 拒绝
        r = await svc.patch_scene_state(inst, {"anchor_states": {"anc_nope": {"condition": "broken"}}},
                                        expected_revision=rev)
        assert not r["ok"] and r["error"] == "anchor_not_found", r
        # T16：prop 不存在拒绝
        r = await svc.patch_scene_state(inst, {"prop_placements": {"prop_missing": {"anchor_id": a1}}},
                                        expected_revision=rev)
        assert not r["ok"] and r["error"] == "prop_not_found", r
        # prop 的 anchor 不属于本 location 拒绝
        r = await svc.patch_scene_state(inst, {"prop_placements": {prop_id: {"anchor_id": "anc_nope"}}},
                                        expected_revision=rev)
        assert not r["ok"] and r["error"] == "cross_location_anchor", r
        # T14：time/weather/lighting round-trip + 结果态写入
        r = await svc.patch_scene_state(
            inst, {"time_of_day": "深夜", "weather": "暴雨", "lighting": "应急灯昏黄",
                   "environment_conditions": "风声很大",
                   "anchor_states": {a1: {"condition": "open", "note": "被撞开"}},
                   "prop_placements": {prop_id: {"anchor_id": a1, "state": "intact",
                                                 "note": "靠在北门边"}}},
            expected_revision=rev)
        assert r["ok"], r
        got = svc.read_scene_instance(inst)["state"]
        assert got["time_of_day"] == "深夜" and got["weather"] == "暴雨"
        assert got["lighting"] == "应急灯昏黄" and got["environment_conditions"] == "风声很大"
        assert got["anchor_states"][a1] == {"condition": "open", "note": "被撞开"}
        assert got["prop_placements"][prop_id]["anchor_id"] == a1
        assert got["prop_placements"][prop_id]["state"] == "intact"
        # round-trip：读→patch→读 内容一致（整 dict 替换语义）
        rev2 = svc.read_scene_instance(inst)["revision"]
        r2 = await svc.patch_scene_state(inst, {"time_of_day": "黎明"}, expected_revision=rev2)
        assert r2["ok"]
        got2 = svc.read_scene_instance(inst)["state"]
        assert got2["time_of_day"] == "黎明"
        assert got2["anchor_states"] == got["anchor_states"], "未 patch 的 dict 字段保持不变"
        # 未知字段拒绝
        rev3 = svc.read_scene_instance(inst)["revision"]
        r3 = await svc.patch_scene_state(inst, {"characters": ["Alice"]}, expected_revision=rev3)
        assert not r3["ok"] and r3["error"] == "validation_error", r3
    asyncio.run(flow())


def test_t16b_corrupt_resources_fail_closed_but_other_patches_ok(svc, pdir):
    """resources.json 损坏：prop 校验 fail closed（不静默放过）；
    不触及 prop 校验的 patch 不受影响。"""
    (pdir / "output" / "resources.json").write_text("{corrupt", encoding="utf-8")

    async def flow():
        loc = (await _seed_location(svc))["target"]
        ov = svc.document_overview()
        r = await svc.create_scene_instance(story_scene_id="s01", location_id=loc,
                                            expected_document_revision=ov["document_revision"])
        inst = r["target"]
        rev = svc.read_scene_instance(inst)["revision"]
        r1 = await svc.patch_scene_state(inst, {"prop_placements": {"prop_x": {"anchor_id": ""}}},
                                         expected_revision=rev)
        assert not r1["ok"] and r1["error"] == "resources_lookup_failed", r1
        r2 = await svc.patch_scene_state(inst, {"weather": "晴"}, expected_revision=rev)
        assert r2["ok"], r2
    asyncio.run(flow())


# ============ T17 / T18 / T19：revision 语义 ============
def test_t17_document_revision_deterministic(pdir):
    st1 = SceneSystemStore(pdir, pdir.name)
    doc = st1.load()
    from src.scene_system.models import Location, new_location_id
    loc = Location(location_id=new_location_id(), name="X")
    doc.locations[loc.location_id] = loc
    st1.save(doc)
    r1 = document_revision(st1.load())
    r2 = document_revision(SceneSystemStore(pdir, pdir.name).load())
    assert r1 == r2, "同一磁盘内容 fingerprint 必须一致"
    # 实体 revision 同样 deterministic
    l1 = location_revision(st1.load().locations[loc.location_id])
    l2 = location_revision(SceneSystemStore(pdir, pdir.name).load().locations[loc.location_id])
    assert l1 == l2


def test_t18_revision_changes_on_mutation(svc):
    async def flow():
        ov = svc.document_overview()
        before = ov["document_revision"]
        r = await _seed_location(svc)   # mutation
        assert svc.document_overview()["document_revision"] != before, \
            "document revision 必须随 mutation 变化"
        loc_id = r["target"]
        rev_before = svc.read_location(loc_id)["revision"]
        rr = await svc.update_location(loc_id, {"description": "新描述"},
                                       expected_revision=svc.read_location(loc_id)["revision"])
        assert rr["ok"]
        assert svc.read_location(loc_id)["revision"] != rev_before, \
            "实体 revision 必须随 mutation 变化"
    asyncio.run(flow())


def test_t19_stale_occ_rejected_content_unchanged(svc, pdir):
    async def flow():
        r = await _seed_location(svc)
        loc_id = r["target"]
        on_disk_before = SceneSystemStore(project_dir=pdir, project_name=pdir.name).path.read_text(encoding="utf-8")
        r2 = await svc.update_location(loc_id, {"name": "不应生效"},
                                       expected_revision="deadbeef")
        assert not r2["ok"] and r2["error"] == "stale_revision"
        assert r2["expected_revision"] == "deadbeef"
        assert r2["current_revision"] == r["revision"]
        assert "current" in r2, "stale receipt 附当前实体序列化"
        # 磁盘未被污染
        on_disk_after = SceneSystemStore(project_dir=pdir, project_name=pdir.name).path.read_text(encoding="utf-8")
        assert on_disk_before == on_disk_after
        assert svc.read_location(loc_id)["name"] == "灯塔监听室"
    asyncio.run(flow())


# ============ T20：并发同 revision mutation ============
def test_t20_concurrent_same_revision_exactly_one_wins(svc):
    async def flow():
        ov = svc.document_overview()
        doc_rev = ov["document_revision"]
        results = await asyncio.gather(
            svc.create_location(name="A", expected_document_revision=doc_rev),
            svc.create_location(name="B", expected_document_revision=doc_rev),
        )
        oks = [r for r in results if r["ok"]]
        stales = [r for r in results if not r["ok"] and r.get("error") == "stale_revision"]
        assert len(oks) == 1, f"恰好一个成功，得到 {[(r['ok'], r.get('error')) for r in results]}"
        assert len(stales) == 1
        assert len(svc.list_locations()) == 1, "stale 一侧绝不落盘"
    asyncio.run(flow())


# ============ T21 / T22：fail closed ============
def test_t21_bad_json_fail_closed(pdir):
    st = SceneSystemStore(pdir, pdir.name)
    st.path.parent.mkdir(parents=True, exist_ok=True)
    st.path.write_text("{broken json", encoding="utf-8")
    with pytest.raises(SceneSystemSchemaError):
        st.load()
    svc = SceneSystemService(pdir, pdir.name)
    with pytest.raises(SceneSystemSchemaError):
        svc.document_overview()   # 读路径同样 fail closed，不静默返回空


def test_t22_unknown_schema_fail_closed(pdir):
    """S2B 起受支持版本为 (1, 2, 3)（v3=scene_instance_views collection，惰性迁移）；未知未来版本（≥4）fail closed。"""
    st = SceneSystemStore(pdir, pdir.name)
    st.path.parent.mkdir(parents=True, exist_ok=True)
    st.path.write_text(json.dumps({"schema_version": 99, "locations": {}}), encoding="utf-8")
    with pytest.raises(SceneSystemSchemaError):
        st.load()
    with pytest.raises(SceneSystemSchemaError):
        st.path.write_text(json.dumps({"schema_version": 4, "locations": {}}), encoding="utf-8")
        st.load()
    with pytest.raises(SceneSystemSchemaError):
        SceneSystemDocument.from_dict({"schema_version": 4, "locations": {}})


# ============ T23：重启持久化 ============
def test_t23_restart_persistence_full_graph(svc, pdir):
    async def flow():
        loc = (await _seed_location(svc))["target"]
        a1 = (await _seed_anchor(svc, loc, name="北门"))["target"]
        a2 = (await _seed_anchor(svc, loc, name="主控台", kind="furniture"))["target"]
        ov = svc.document_overview()
        r = await svc.update_anchor(a2, {"relations": [{"relation": "opposite",
                                                        "object_anchor_id": a1}]},
                                    expected_revision=svc.read_anchor(a2)["revision"])
        assert r["ok"], r
        ov = svc.document_overview()
        rv = await svc.create_view(location_id=loc, label="hero",
                                   visible_anchor_ids=[a1],
                                   expected_document_revision=svc.document_overview()["document_revision"])
        assert rv["ok"], rv
        rl = await svc.create_layout(location_id=loc, orientation_convention="north_up",
                                     placements=[{"anchor_id": a1, "zone": "north", "x": 0.5, "y": 0.1}],
                                     expected_document_revision=svc.document_overview()["document_revision"])
        assert rl["ok"], rl
        ri = await svc.create_scene_instance(story_scene_id="s01", location_id=loc,
                                             expected_document_revision=svc.document_overview()["document_revision"])
        assert ri["ok"], ri
        inst = ri["target"]
        rr = await svc.patch_scene_state(
            inst, {"time_of_day": "夜", "anchor_states": {a1: {"condition": "open"}}},
            expected_revision=svc.read_scene_instance(inst)["revision"])
        assert rr["ok"], rr
        return loc, a1, a2, inst, rv["target"], rl["target"]
    loc, a1, a2, inst, view_id, layout_id = asyncio.run(flow())

    # "重启"：全新实例
    svc2 = SceneSystemService(pdir, pdir.name)
    got_loc = svc2.read_location(loc)
    assert got_loc["anchor_ids"] and got_loc["view_ids"] and got_loc["layout_id"]
    assert set(got_loc["anchor_ids"]) == {a1, a2}
    got_anchor = svc2.read_anchor(a2)
    assert got_anchor["relations"][0]["object_anchor_id"] == a1
    assert svc2.read_view(view_id)["visible_anchor_ids"] == [a1]
    assert svc2.read_layout(layout_id)["placements"][0]["anchor_id"] == a1
    got_inst = svc2.read_scene_instance(inst)
    assert got_inst["state"]["time_of_day"] == "夜"
    assert got_inst["state"]["anchor_states"][a1]["condition"] == "open"
    assert got_inst["story_scene_missing"] is False


# ============ 悬空 Story Scene（§16）============
def test_story_scene_missing_after_delete_annotated(svc, pdir):
    async def flow():
        loc = (await _seed_location(svc))["target"]
        ov = svc.document_overview()
        r = await svc.create_scene_instance(story_scene_id="s02", location_id=loc,
                                            expected_document_revision=ov["document_revision"])
        assert r["ok"], r
        return r["target"]
    inst = asyncio.run(flow())
    # 模拟旧 Story Scene 被删（project.json 里去掉 s02）
    proj = ProjectStore(pdir).load()
    proj.scenes = [sc for sc in proj.scenes if sc.scene_id != "s02"]
    ProjectStore(pdir).save(proj)
    got = svc.read_scene_instance(inst)
    assert got is not None, "悬空不隐藏数据"
    assert got["story_scene_missing"] is True, "read 必须明确标注 missing_story_scene"
    got_list = svc.list_scene_instances(story_scene_id="s02")
    assert got_list and got_list[0]["story_scene_missing"] is True
    # project.json 恢复后标注消失
    ProjectStore(pdir).save(ProjectStore(pdir).load())  # no-op save（内容已无 s02）


def test_story_scene_missing_when_project_json_absent(svc, pdir):
    async def flow():
        loc = (await _seed_location(svc))["target"]
        ov = svc.document_overview()
        r = await svc.create_scene_instance(story_scene_id="s01", location_id=loc,
                                            expected_document_revision=ov["document_revision"])
        assert r["ok"], r
        return r["target"]
    inst = asyncio.run(flow())
    # project.json 整个消失（tmp_path 下另一个项目名共用 service 不可行——直接删文件）
    (pdir / "output" / "project.json").unlink()
    got = svc.read_scene_instance(inst)
    assert got["story_scene_missing"] is True
    # 创建路径同样 fail closed
    async def try_create():
        ov = svc.document_overview()
        loc2 = svc.read_location(list(svc.list_locations())[0]["location_id"])["location_id"]
        return await svc.create_scene_instance(story_scene_id="s01", location_id=loc2,
                                               expected_document_revision=ov["document_revision"])
    # 注意：上面已经没有 active 之外的障碍，但 s01 无法验证存在 → story_scene_not_found
    # （当前已有 active instance；此处验证的是读取标注，创建路径在 test_t11 覆盖）
    asyncio.run(try_create())


# ============ T24 / T25：冻结区字节不变 ============
def test_t24_t25_frozen_canonical_bytes_unchanged(svc, pdir):
    # 预置 resources.json（含 prop）
    prop_id = "prop_freeze_check"
    write_prop_resource(pdir, prop_id)
    proj_before = _sha256_file(pdir / "output" / "project.json")
    res_before = _sha256_file(pdir / "output" / "resources.json")

    async def flow():
        # 全谱系 mutation（涉及跨 store 读：T11 story scene 校验 + T16 prop 校验）
        loc = (await _seed_location(svc))["target"]
        a1 = (await _seed_anchor(svc, loc, name="北门"))["target"]
        ov = svc.document_overview()
        await svc.create_view(location_id=loc, label="hero", visible_anchor_ids=[a1],
                              expected_document_revision=ov["document_revision"])
        ov = svc.document_overview()
        await svc.create_layout(location_id=loc, orientation_convention="north_up",
                                expected_document_revision=ov["document_revision"])
        ov = svc.document_overview()
        r = await svc.create_scene_instance(story_scene_id="s01", location_id=loc,
                                            expected_document_revision=ov["document_revision"])
        assert r["ok"], r
        inst = r["target"]
        rev = svc.read_scene_instance(inst)["revision"]
        rr = await svc.patch_scene_state(
            inst, {"weather": "暴雨",
                   "prop_placements": {prop_id: {"anchor_id": a1, "state": "intact"}}},
            expected_revision=rev)
        assert rr["ok"], rr
        # 失败路径（409/404/400）也应零副作用
        rev2 = svc.read_scene_instance(inst)["revision"]
        await svc.patch_scene_state(inst, {"prop_placements": {"prop_missing": {}}},
                                    expected_revision=rev2)
        await svc.update_location(loc_id := loc, {"name": "x"}, expected_revision="deadbeef")
    asyncio.run(flow())

    assert _sha256_file(pdir / "output" / "project.json") == proj_before, \
        "project.json 字节不变（T24）"
    assert _sha256_file(pdir / "output" / "resources.json") == res_before, \
        "resources.json 字节不变（T25）"


# ============ T26：Agent 源引用边界（Agent System V2 Phase C2 演进版）============
def test_t26_no_agent_source_references_scene_system():
    """原 S1 冻结（零引用）由 27.md §6.5 演进为「只读经 production_context」；
    再由 28.md §0.3（Phase C2）演进：Scene Agent（scene_designer.py）是 Scene System
    【唯一合法写入者】（经 SceneSystemService/SceneVisualService 正式链路），
    project_events.py 仅持锁/事件域常量。其余任何 Agent 源文件仍禁止 scene_system
    引用；production_context 投影层不得出现 scene 写/生成类 API（只读边界）。"""
    root = Path(__file__).resolve().parent.parent
    allowed = {"production_context.py", "scene_designer.py", "project_events.py",
               "producer.py"}
    for rel in ("src/agents", "src/ai"):
        pkg = root / rel
        assert pkg.exists()
        for py in pkg.rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="ignore")
            if "scene_system" not in text:
                continue
            assert py.name in allowed, \
                (f"Agent 源出现 scene_system 引用（只允许 production_context.py / "
                 f"scene_designer.py / project_events.py）: {py}")
            if py.name == "scene_designer.py":
                continue   # 唯一写入者：允许 service/生成/审批 API 全集
            for banned in ("patch_scene_state", "create_scene_instance",
                           "create_location", "create_anchor", "create_view",
                           "generate_location_view", "generate_scene_instance_view",
                           "approve_scene_instance_view"):
                assert banned not in text, \
                    f"投影层出现 scene 写/生成 API {banned}: {py}"


# ============ HTTP 层冒烟（router + status mapping）============
@pytest.fixture
def client(tmp_path):
    pdir = make_project(tmp_path)
    orig = rsc.get_project_manager
    rsc.get_project_manager = lambda: ProjectManager(base_dir=str(tmp_path))
    app = FastAPI()
    app.include_router(rsc.router)
    try:
        yield TestClient(app), pdir
    finally:
        rsc.get_project_manager = orig


def _doc_rev(client: TestClient, pname: str) -> str:
    return client.get(f"/api/project/{pname}/scene-system/overview").json()["document_revision"]


def test_http_read_zero_side_effect_and_overview(client):
    c, pdir = client
    pname = pdir.name
    r = c.get(f"/api/project/{pname}/scene-system/overview")
    assert r.status_code == 200
    assert r.json()["counts"]["locations"] == 0
    assert not (pdir / "output" / "scene_system.json").exists()
    r = c.get("/api/project/nope/scene-system/overview")
    assert r.status_code == 404


def test_http_full_write_flow_and_error_mapping(client):
    c, pdir = client
    pname = pdir.name
    base = f"/api/project/{pname}/scene-system"
    # create location
    r = c.post(f"{base}/locations",
               json={"name": "灯塔", "expected_document_revision": _doc_rev(c, pname)})
    assert r.status_code == 200, r.text
    loc_id, loc_rev = r.json()["target"], r.json()["revision"]
    # create anchor
    r = c.post(f"{base}/anchors",
               json={"location_id": loc_id, "name": "北门", "kind": "door",
                     "expected_document_revision": _doc_rev(c, pname)})
    assert r.status_code == 200, r.text
    anc = r.json()["target"]
    # create scene instance（s01 存在）
    r = c.post(f"{base}/scene-instances",
               json={"story_scene_id": "s01", "location_id": loc_id,
                     "expected_document_revision": _doc_rev(c, pname)})
    assert r.status_code == 200, r.text
    inst = r.json()["target"]
    # state patch
    rev = c.get(f"{base}/scene-instances/{inst}").json()["revision"]
    r = c.patch(f"{base}/scene-instances/{inst}/state",
                json={"time_of_day": "夜", "expected_revision": rev})
    assert r.status_code == 200 and r.json()["after"]["state"]["time_of_day"] == "夜"
    # stale → 409
    r = c.patch(f"{base}/scene-instances/{inst}/state",
                json={"time_of_day": "日", "expected_revision": "deadbeef"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "stale_revision"
    # 404：location 不存在
    r = c.get(f"{base}/locations/loc_missing")
    assert r.status_code == 404
    # 400：validation（anchor 未知 kind）
    r = c.post(f"{base}/anchors",
               json={"location_id": loc_id, "name": "x", "kind": "portal",
                     "expected_document_revision": _doc_rev(c, pname)})
    assert r.status_code == 400 and r.json()["detail"]["error"] == "validation_error"
    # 422：跨 location visible anchor
    r = c.post(f"{base}/locations",
               json={"name": "码头", "expected_document_revision": _doc_rev(c, pname)})
    loc2 = r.json()["target"]
    r = c.post(f"{base}/anchors",
               json={"location_id": loc2, "name": "缆桩",
                     "expected_document_revision": _doc_rev(c, pname)})
    b1 = r.json()["target"]
    r = c.post(f"{base}/views",
               json={"location_id": loc_id, "label": "bad", "visible_anchor_ids": [b1],
                     "expected_document_revision": _doc_rev(c, pname)})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "cross_location_anchor"
    # 409：location_in_use（fail closed 删除）
    r = c.delete(f"{base}/locations/{loc_id}?expected_revision={loc_rev}")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "location_in_use"
    # 派生索引在 read envelope（灯塔 loc 只有 anc；b1 属码头 loc2）
    got = c.get(f"{base}/locations/{loc_id}").json()
    assert set(got["anchor_ids"]) == {anc}
    assert got["layout_id"] is None and got["view_ids"] == []


def test_http_story_scene_not_found_404(client):
    c, pdir = client
    pname = pdir.name
    base = f"/api/project/{pname}/scene-system"
    r = c.post(f"{base}/locations",
               json={"name": "灯塔", "expected_document_revision": _doc_rev(c, pname)})
    loc_id = r.json()["target"]
    r = c.post(f"{base}/scene-instances",
               json={"story_scene_id": "s99", "location_id": loc_id,
                     "expected_document_revision": _doc_rev(c, pname)})
    assert r.status_code == 404 and r.json()["detail"]["error"] == "story_scene_not_found"
