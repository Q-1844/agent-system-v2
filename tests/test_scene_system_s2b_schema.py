# tests/test_scene_system_s2b_schema.py
# Scene System V1 — Phase S2B：schema v3 迁移 + targeted preflight（§2/§5/§39 T2-T6）。
#
# T2 v2 read compatibility（含 scene_instance_views 的 v3 文件之外，v2 旧文件可读）
# T3 v2 read zero writeback
# T4 first S2B mutation v2→v3
# T5 old v2 runtime v3 fail closed
# T6 future schema fail closed
# preflight A：被 revoke 的 LocationView 绝不能作为 approved base（select_reference_views
#              与 S2B base 校验共用同一 approval 语义——approval_status==approved 才可用）
# + SceneInstanceView 实体 round-trip / revision
import asyncio
import json
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.models import Project, Scene
from src.scene_system.models import (
    SCENE_SYSTEM_SCHEMA_VERSION, SceneInstanceView, SceneSystemDocument,
    SceneSystemSchemaError, new_scene_instance_view_id, scene_instance_view_revision,
)
from src.scene_system.store import SceneSystemStore
from src.scene_system.service import SceneSystemService
from src.scene_system.visual_basis import select_reference_views
import src.scene_system.models as models_mod


def make_pdir(tmp_path: Path, schema: int = 2) -> Path:
    name = tmp_path.name
    pdir = tmp_path / name
    proj = Project(name=name)
    proj.scenes = [Scene(scene_id="s01", summary="x")]
    ProjectStore(pdir).save(proj)
    doc = {"schema_version": schema, "locations": {}, "anchors": {}, "layouts": {},
           "views": {}, "scene_instances": {}}
    if schema >= 3:
        doc["scene_instance_views"] = {}
    path = pdir / "output" / "scene_system.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return pdir


def test_t2_v2_read_compatibility(tmp_path):
    pdir = make_pdir(tmp_path, schema=2)
    st = SceneSystemStore(pdir, pdir.name)
    doc = st.load()
    assert doc.schema_version == SCENE_SYSTEM_SCHEMA_VERSION == 3
    assert doc.scene_instance_views == {}, "v2 文件读入：新 collection 空默认"


def test_t3_v2_read_zero_writeback(tmp_path):
    pdir = make_pdir(tmp_path, schema=2)
    path = pdir / "output" / "scene_system.json"
    before = path.read_bytes()
    svc = SceneSystemService(pdir, pdir.name)
    svc.document_overview()
    svc.list_locations()
    assert path.read_bytes() == before, "纯 read 绝不写回 v2 文件"
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 2


def test_t4_first_s2b_mutation_upgrades_v2_to_v3(tmp_path):
    pdir = make_pdir(tmp_path, schema=2)
    path = pdir / "output" / "scene_system.json"
    svc = SceneSystemService(pdir, pdir.name)
    ov = svc.document_overview()
    r = asyncio.run(svc.create_location(name="L", expected_document_revision=ov["document_revision"]))
    assert r["ok"], r
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == 3, "首次真实 mutation 升 v3"
    assert on_disk["scene_instance_views"] == {}
    assert on_disk["locations"], "v2 数据原样保留"


def test_t5_old_v2_runtime_facing_v3_fails_closed(tmp_path):
    v3 = {"schema_version": 3, "locations": {}, "anchors": {}, "layouts": {},
          "views": {}, "scene_instances": {}, "scene_instance_views": {}}
    supported = models_mod._SUPPORTED_SCENE_SYSTEM_SCHEMA_VERSIONS
    try:
        models_mod._SUPPORTED_SCENE_SYSTEM_SCHEMA_VERSIONS = (1, 2)
        with pytest.raises(SceneSystemSchemaError):
            SceneSystemDocument.from_dict(v3)
    finally:
        models_mod._SUPPORTED_SCENE_SYSTEM_SCHEMA_VERSIONS = supported


def test_t6_future_schema_fail_closed(tmp_path):
    with pytest.raises(SceneSystemSchemaError):
        SceneSystemDocument.from_dict({"schema_version": 4})
    pdir = make_pdir(tmp_path, schema=2)
    path = pdir / "output" / "scene_system.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schema_version"] = 9
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SceneSystemSchemaError):
        SceneSystemStore(pdir, pdir.name).load()


def test_scene_instance_view_roundtrip_and_revision():
    v = SceneInstanceView(scene_instance_view_id=new_scene_instance_view_id(),
                          scene_instance_id="scin_x", location_view_id="view_y",
                          label="s09 · north to south")
    d1 = v.to_dict()
    v2 = SceneInstanceView.from_dict(json.loads(json.dumps(d1)))
    assert v2.to_dict() == d1
    assert scene_instance_view_revision(v) == scene_instance_view_revision(v2)
    r_before = scene_instance_view_revision(v)
    v.generation_status = "ready"
    assert scene_instance_view_revision(v) != r_before


# ============ §2 preflight A：revoked LocationView 不能作为 approved base ============
def test_preflight_a_revoked_view_not_usable_as_reference(tmp_path):
    pdir = make_pdir(tmp_path, schema=3)
    svc = SceneSystemService(pdir, pdir.name)

    async def flow():
        async def drev():
            return svc.document_overview()["document_revision"]
        loc = (await svc.create_location(name="L", expected_document_revision=await drev()))["target"]
        a1 = (await svc.create_anchor(location_id=loc, name="door", kind="door",
                                      expected_document_revision=await drev()))["target"]
        va = await svc.create_view(location_id=loc, label="v", visible_anchor_ids=[a1],
                                   expected_document_revision=await drev())
        vb = await svc.create_view(location_id=loc, label="w", visible_anchor_ids=[a1],
                                   expected_document_revision=await drev())
        # 把 va 置 approved（模拟已批准），再 revoke
        st = SceneSystemStore(pdir, pdir.name)
        async with st.write_lock():
            d = st.load()
            d.views[va["target"]].approval_status = "approved"
            d.views[va["target"]].reference_asset_id = "ref_some_asset"
            st.save(d)
        # revoke（经协议）
        rev = svc.read_view(va["target"])["revision"]
        from src.scene_system.visual_service import SceneVisualService
        vs = SceneVisualService(pdir, pdir.name)
        rr = await vs.revoke_view_approval(va["target"], expected_revision=rev, reason="pf")
        assert rr["ok"] and rr["approval_status"] == "rejected"
        return va["target"], vb["target"]
    va_id, vb_id = asyncio.run(flow())
    doc = SceneSystemStore(pdir, pdir.name).load()
    sel = select_reference_views(pdir, doc, None, doc.views[vb_id],
                                 explicit_reference_view_ids=[va_id])
    assert sel.get("error") == "reference_view_not_approved", \
        "被 revoke 的 LocationView 即使 reference_asset_id 保留，也绝不作为 approved base"
    auto = select_reference_views(pdir, doc, None, doc.views[vb_id])
    assert auto["selected"] == [], "auto 选择同样跳过 revoked view"
