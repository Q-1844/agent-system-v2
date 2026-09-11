# tests/test_scene_system_s2a_schema.py
# Scene System V1 — Phase S2A §7：schema v2 惰性迁移测试（T2-T5）。
#
# T2 v1 兼容：v1 文件可读，新字段取默认值，in-memory 规范化为 v2
# T3 v1 纯 read 零写回：load 绝不改磁盘
# T4 首次真实 mutation v1→v2 落盘
# T5 未知 schema（≥3）fail closed
# T6 旧 S1 runtime（只支持 v1）面对 v2 文件 fail closed（不静默丢字段）
# T7 视觉状态字段参与 view revision（generation_status 变更 → revision 变化）
# T8 视觉状态字段不可经 update_view 手工 patch（协议管理）
import asyncio
import json
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.models import Project, Scene
from src.scene_system.models import (
    SCENE_SYSTEM_SCHEMA_VERSION, GenerationStatus, SceneSystemDocument,
    SceneSystemSchemaError, view_revision,
)
from src.scene_system.store import SceneSystemStore
from src.scene_system.service import SceneSystemService
import src.scene_system.models as models_mod


def make_project(base: Path) -> Path:
    name = base.name
    pdir = base / name
    proj = Project(name=name)
    proj.scenes = [Scene(scene_id="s01", summary="段")]
    ProjectStore(pdir).save(proj)
    return pdir


def write_v1_file(pdir: Path) -> dict:
    """手造一个合法 v1 scene_system.json（含 location/view，无 v2 视觉状态字段）。"""
    doc = {
        "schema_version": 1,
        "locations": {"loc_v1test000001": {
            "location_id": "loc_v1test000001", "name": "灯塔监听室",
            "description": "", "identity_description": "圆形控制室", "style_notes": "",
            "created_at": 100.0, "updated_at": 100.0}},
        "anchors": {}, "layouts": {},
        "views": {"view_v1test00001": {
            "view_id": "view_v1test00001", "location_id": "loc_v1test000001",
            "label": "hero", "camera_zone": "south_west", "look_direction": "north_east",
            "visible_anchor_ids": [], "reference_asset_id": None,
            "approval_status": "unreviewed", "created_at": 100.0, "updated_at": 100.0}},
        "scene_instances": {},
    }
    path = pdir / "output" / "scene_system.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


@pytest.fixture
def pdir(tmp_path) -> Path:
    return make_project(tmp_path)


def test_t2_v1_file_loads_with_defaults_normalized(pdir):
    write_v1_file(pdir)
    st = SceneSystemStore(pdir, pdir.name)
    doc = st.load()
    # v1 数据完整
    assert doc.locations["loc_v1test000001"].name == "灯塔监听室"
    v = doc.views["view_v1test00001"]
    assert v.label == "hero" and v.approval_status == "unreviewed"
    # v2 新字段取默认值，in-memory 规范化为 v2
    assert v.generation_status == GenerationStatus.NONE.value
    assert v.active_generation_id is None
    # S2B 起受支持最新版本为 v3（scene_instance_views collection）；in-memory 规范化为 v3
    assert doc.schema_version == SCENE_SYSTEM_SCHEMA_VERSION == 3


def test_t3_v1_pure_read_zero_writeback(pdir):
    write_v1_file(pdir)
    path = pdir / "output" / "scene_system.json"
    before = path.read_bytes()
    svc = SceneSystemService(pdir, pdir.name)
    svc.read_location("loc_v1test000001")
    svc.list_locations()
    svc.document_overview()
    assert path.read_bytes() == before, "纯 read 绝不写回（v1 文件字节不变）"
    # 文件仍是 v1
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_t4_first_mutation_upgrades_v1_to_v2(pdir):
    write_v1_file(pdir)
    path = pdir / "output" / "scene_system.json"
    svc = SceneSystemService(pdir, pdir.name)
    # 一次真实 mutation（update_location）
    loc = svc.read_location("loc_v1test000001")
    r = asyncio.run(svc.update_location("loc_v1test000001", {"description": "新的"},
                                        expected_revision=loc["revision"]))
    assert r["ok"], r
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] >= 2, "首次真实 mutation 安全升级（S2B 起 v1 文件首 mutation 直接升到当前 v3）"
    assert on_disk["schema_version"] == 3, "首次真实 mutation 安全升级到当前版本 v3"
    # 旧数据原样保留（非破坏性）
    assert on_disk["locations"]["loc_v1test000001"]["name"] == "灯塔监听室"
    # v2 view 字段持久化时带默认值
    assert on_disk["views"]["view_v1test00001"]["generation_status"] == "none"


def test_t5_unknown_future_schema_fail_closed(pdir):
    write_v1_file(pdir)
    path = pdir / "output" / "scene_system.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schema_version"] = 4   # S2B 起 3 为受支持版本；未知未来版本=≥4
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SceneSystemSchemaError):
        SceneSystemStore(pdir, pdir.name).load()
    with pytest.raises(SceneSystemSchemaError):
        SceneSystemDocument.from_dict({"schema_version": 99})


def test_t6_old_s1_runtime_facing_v2_fails_closed(pdir):
    """旧 S1 runtime 只支持 v1：面对 v2 文件必须大声报错，而非 load→save 静默丢
    generation_status/active_generation_id。模拟方式：把支持版本表收窄到 (1,)。"""
    v2 = {"schema_version": 2, "locations": {}, "anchors": {}, "layouts": {},
          "views": {}, "scene_instances": {}}
    supported = models_mod._SUPPORTED_SCENE_SYSTEM_SCHEMA_VERSIONS
    try:
        models_mod._SUPPORTED_SCENE_SYSTEM_SCHEMA_VERSIONS = (1,)
        with pytest.raises(SceneSystemSchemaError):
            SceneSystemDocument.from_dict(v2)
    finally:
        models_mod._SUPPORTED_SCENE_SYSTEM_SCHEMA_VERSIONS = supported


def test_t7_visual_state_in_view_revision(pdir):
    write_v1_file(pdir)
    svc = SceneSystemService(pdir, pdir.name)
    doc = SceneSystemStore(pdir, pdir.name).load()
    v = doc.views["view_v1test00001"]
    rev_none = view_revision(v)
    v.generation_status = GenerationStatus.READY.value
    v.active_generation_id = "gen_abc123"
    assert view_revision(v) != rev_none, "视觉状态是 view 内容的一部分（进 revision）"


def test_t8_visual_state_fields_not_manually_patchable(pdir):
    write_v1_file(pdir)
    svc = SceneSystemService(pdir, pdir.name)
    rev = svc.read_location("loc_v1test000001")  # warm
    async def flow():
        doc = SceneSystemStore(pdir, pdir.name).load()
        vrev = view_revision(doc.views["view_v1test00001"])
        for bad in ({"generation_status": "ready"},
                    {"active_generation_id": "gen_x"},
                    {"reference_asset_id": "ref_x"},
                    {"approval_status": "approved"}):
            r = await svc.update_view("view_v1test00001", bad, expected_revision=vrev)
            assert not r["ok"] and r["error"] == "validation_error", (bad, r)
        # 定义字段仍可改
        r2 = await svc.update_view("view_v1test00001", {"label": "hero establishing"},
                                   expected_revision=vrev)
        assert r2["ok"], r2
    asyncio.run(flow())
