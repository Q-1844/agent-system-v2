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


# ============ Commit B：Prop Reference Review + Approval（§7 最小闭环）============
import json as _json

from src.resources.service import ResourceService
from src.resources.write_service import ResourceWriteService
from src.resources.prop_review import (
    review_prop_reference,
    has_prop_pass_evidence,
    build_prop_review_prompt,
    derive_prop_overall,
    read_prop_review_records,
)

from tests.test_resources_phase1c1 import FakeAgnes, make_project, make_view_files, run
from tests.test_resources_phase1c2 import FakeVision


@pytest.fixture
def penv(tmp_path):
    """带 Alice 的项目 + WriteService + document revision（Prop/Composition 测试用）。"""
    pdir = make_project(tmp_path, with_character=True)
    make_view_files(pdir)
    ws = ResourceWriteService(pdir, pdir.name)
    return {"pdir": pdir, "ws": ws, "doc_rev": document_revision(ws.store.load())}


def _prop_rev(env, prop_id):
    from src.resources.models import prop_revision
    return prop_revision(env["ws"].store.load().props[prop_id])


def _seed_prop(env, *, name="Wooden Sword", description="a short wooden training sword",
               img_name="sword_ref.png"):
    """建 prop + 绑定一张真实小图参考，返回 (prop_id, asset_id)。"""
    ws = env["ws"]
    img = env["pdir"] / "o" / img_name
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(f"PNG-REF-{img_name}".encode())
    r = run(ws.create_prop(name=name, description=description,
                           expected_document_revision=document_revision(ws.store.load())))
    assert r["ok"], r
    prop_id = r["target"]
    r2 = run(ws.register_reference_asset(
        local=f"o/{img_name}", view="hero", purpose="prop",
        expected_document_revision=r["document_revision_after"]))
    assert r2["ok"], r2
    asset_id = r2["target"]
    r3 = run(ws.attach_prop_reference(prop_id, asset_id,
                                      expected_revision=_prop_rev(env, prop_id)))
    assert r3["ok"], r3
    return prop_id, asset_id


PROP_PASS_JSON = _json.dumps({
    "prop_fidelity": "pass", "fidelity_notes": "wooden training sword as described",
    "visual_clarity": "pass", "clarity_notes": "whole object readable",
    "visual_defects": "pass", "defect_notes": "none",
    "overall": "pass", "summary": "clean prop reference"})
PROP_NEEDSFIX_JSON = _json.dumps({
    "prop_fidelity": "fail", "fidelity_notes": "this is a metal sword, not wooden",
    "visual_clarity": "pass", "clarity_notes": "ok",
    "visual_defects": "pass", "defect_notes": "none",
    "overall": "pass",   # 模型自报 pass —— 服务端必须推导 needs_fix
    "summary": "wrong material"})
PROP_CONCERN_JSON = _json.dumps({
    "prop_fidelity": "concern", "fidelity_notes": "grip color differs",
    "visual_clarity": "pass", "clarity_notes": "ok",
    "visual_defects": "pass", "defect_notes": "none",
    "overall": "pass", "summary": "minor concern"})


def test_prop_review_pass_flow_and_evidence(penv):
    prop_id, asset_id = _seed_prop(penv)
    res_path = penv["pdir"] / "output" / "resources.json"
    before = res_path.read_bytes()
    fv = FakeVision(PROP_PASS_JSON)
    r = run(review_prop_reference(penv["pdir"], prop_id, agnes_client=fv))
    assert r["ok"] and r["status"] == "completed", r
    cr = r["candidate_reviews"][0]
    assert cr["asset_id"] == asset_id and cr["status"] == "reviewed"
    assert (cr["prop_fidelity"], cr["visual_clarity"], cr["visual_defects"]) == ("pass", "pass", "pass")
    assert cr["overall"] == "pass"
    # vision 契约面：显式 agnes-2.5-flash + data URI 输入 + prompt 含道具期望
    assert fv.calls[0]["model"] == "agnes-2.5-flash"
    img_part = [c for c in fv.calls[0]["messages"][0]["content"] if c["type"] == "image_url"]
    assert img_part and img_part[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "Wooden Sword" in fv.calls[0]["messages"][0]["content"][0]["text"]
    # 证据落盘（prop_id 流）、无 base64、canonical 字节不变（review 不写 canonical）
    recs = read_prop_review_records(penv["pdir"], prop_id=prop_id)
    assert len(recs) == 1 and recs[0]["operation"] == "prop.review_candidates"
    assert recs[0]["candidate_reviews"][0]["overall"] == "pass"
    assert "data:image" not in _json.dumps(recs)
    assert res_path.read_bytes() == before


def test_prop_review_overall_deterministic_not_trusting_model(penv):
    prop_id, asset_id = _seed_prop(penv)
    r = run(review_prop_reference(penv["pdir"], prop_id,
                                  agnes_client=FakeVision(PROP_NEEDSFIX_JSON)))
    cr = r["candidate_reviews"][0]
    assert cr["overall"] == "needs_fix" and cr["model_overall"] == "pass"
    r2 = run(review_prop_reference(penv["pdir"], prop_id,
                                   agnes_client=FakeVision(PROP_CONCERN_JSON)))
    assert r2["candidate_reviews"][0]["overall"] == "uncertain"
    # 纯函数直证
    assert derive_prop_overall({"prop_fidelity": "fail", "visual_clarity": "pass",
                                "visual_defects": "pass"}) == "needs_fix"
    assert derive_prop_overall({"prop_fidelity": None, "visual_clarity": "pass",
                                "visual_defects": "pass"}) == "uncertain"   # 缺失不判 pass
    assert build_prop_review_prompt(prop_name="X") == build_prop_review_prompt(prop_name="X")


def test_prop_review_isolation_and_fail_closed(penv):
    prop_id, asset_id = _seed_prop(penv)
    # fence 包裹 → 可解析
    r = run(review_prop_reference(penv["pdir"], prop_id,
                                  agnes_client=FakeVision("```json\n" + PROP_PASS_JSON + "\n```")))
    assert r["candidate_reviews"][0]["overall"] == "pass"
    # 垃圾回复 → parse_failed（fail closed，不可作为 pass 证据）
    r2 = run(review_prop_reference(penv["pdir"], prop_id, agnes_client=FakeVision("not json")))
    assert r2["candidate_reviews"][0]["status"] == "parse_failed"
    assert r2["candidate_reviews"][0]["overall"] == "uncertain"
    assert not has_prop_pass_evidence(penv["pdir"], prop_id, asset_id)
    # provider 异常 → 单候选隔离
    r3 = run(review_prop_reference(penv["pdir"], prop_id,
                                   agnes_client=FakeVision(RuntimeError("vision boom"))))
    assert r3["candidate_reviews"][0]["status"] == "provider_error"
    # entry 失败族
    assert run(review_prop_reference(penv["pdir"], "prop_none"))["error"] == "not_found"
    ws = penv["ws"]
    r_empty = run(ws.create_prop(name="Empty", expected_document_revision=document_revision(ws.store.load())))
    assert run(review_prop_reference(penv["pdir"], r_empty["target"]))["error"] == "no_candidates"
    assert run(review_prop_reference(penv["pdir"], prop_id,
                                     asset_ids=["ref_other"]))["error"] == "not_in_candidates"


def test_prop_approve_gate_three_states_then_pass(penv):
    ws = penv["ws"]
    prop_id, asset_id = _seed_prop(penv)
    # ① 无证据 → 拒
    r = run(ws.approve_prop_reference(prop_id, asset_id, expected_revision=_prop_rev(penv, prop_id)))
    assert r["ok"] is False and r["error"] == "review_not_passed"
    # ② needs_fix → 拒
    run(review_prop_reference(penv["pdir"], prop_id, agnes_client=FakeVision(PROP_NEEDSFIX_JSON)))
    r = run(ws.approve_prop_reference(prop_id, asset_id, expected_revision=_prop_rev(penv, prop_id)))
    assert r["ok"] is False and r["error"] == "review_not_passed"
    # ③ uncertain（concern）→ 拒
    run(review_prop_reference(penv["pdir"], prop_id, agnes_client=FakeVision(PROP_CONCERN_JSON)))
    r = run(ws.approve_prop_reference(prop_id, asset_id, expected_revision=_prop_rev(penv, prop_id)))
    assert r["ok"] is False and r["error"] == "review_not_passed"
    # ④ pass → 批准（approved ids + 状态 + 自动封面 + verified）
    run(review_prop_reference(penv["pdir"], prop_id, agnes_client=FakeVision(PROP_PASS_JSON)))
    r = run(ws.approve_prop_reference(prop_id, asset_id, expected_revision=_prop_rev(penv, prop_id)))
    assert r["ok"] and r["verified"], r
    p = ws.store.load().props[prop_id]
    assert p.approved_reference_asset_ids == [asset_id]
    assert p.approval_status == "approved" and p.cover_asset_id == asset_id
    # 幂等
    r2 = run(ws.approve_prop_reference(prop_id, asset_id, expected_revision=_prop_rev(penv, prop_id)))
    assert r2["ok"] and r2.get("unchanged") is True
    # not_in_candidates / OCC
    r3 = run(ws.approve_prop_reference(prop_id, "ref_other", expected_revision=_prop_rev(penv, prop_id)))
    assert r3["ok"] is False and r3["error"] == "not_in_candidates"
    r4 = run(ws.approve_prop_reference(prop_id, asset_id, expected_revision="00000000"))
    assert r4["ok"] is False and r4["error"] == "stale_revision"


def test_prop_revoke_semantics(penv):
    ws = penv["ws"]
    prop_id, asset_id = _seed_prop(penv)
    run(review_prop_reference(penv["pdir"], prop_id, agnes_client=FakeVision(PROP_PASS_JSON)))
    run(ws.approve_prop_reference(prop_id, asset_id, expected_revision=_prop_rev(penv, prop_id)))
    r = run(ws.revoke_prop_approval(prop_id, asset_id, expected_revision=_prop_rev(penv, prop_id)))
    assert r["ok"] and r["verified"]
    p = ws.store.load().props[prop_id]
    assert p.approved_reference_asset_ids == []                  # approved 清空
    assert p.approval_status == "unreviewed"                     # 状态回退
    assert p.cover_asset_id is None                              # cover 清空
    assert asset_id in p.reference_asset_ids                     # 候选保留
    r2 = run(ws.revoke_prop_approval(prop_id, asset_id, expected_revision=_prop_rev(penv, prop_id)))
    assert r2["ok"] and r2.get("unchanged") is True              # 幂等


def test_prop_detach_cleans_approved_and_cover(penv):
    """detach 扩展（1C5）：approved ⊆ candidates 不变式——解绑同步清理 approved/cover。"""
    ws = penv["ws"]
    prop_id, asset_id = _seed_prop(penv)
    run(review_prop_reference(penv["pdir"], prop_id, agnes_client=FakeVision(PROP_PASS_JSON)))
    run(ws.approve_prop_reference(prop_id, asset_id, expected_revision=_prop_rev(penv, prop_id)))
    r = run(ws.detach_prop_reference(prop_id, asset_id, expected_revision=_prop_rev(penv, prop_id)))
    assert r["ok"] and r["verified"]
    p = ws.store.load().props[prop_id]
    assert asset_id not in p.reference_asset_ids
    assert asset_id not in p.approved_reference_asset_ids        # 不允许悬空审批
    assert p.cover_asset_id is None
    assert p.approval_status == "unreviewed"


def test_prop_read_latest_reviews(penv):
    prop_id, _ = _seed_prop(penv)
    run(review_prop_reference(penv["pdir"], prop_id, agnes_client=FakeVision(PROP_PASS_JSON)))
    svc = ResourceService(penv["pdir"], penv["pdir"].name)
    read = svc.read_prop(prop_id)
    asset_id = list(read["latest_reviews"].keys())[0]
    assert read["latest_reviews"][asset_id]["overall"] == "pass"
    assert read["latest_reviews"][asset_id]["prop_fidelity"] == "pass"


def test_prop_review_approve_http_contract(penv, tmp_path):
    """HTTP 契约：创建 prop→approve 409（无证据）→（service 层注入 pass 证据）→approve 200→read 反映。"""
    import shutil

    import routers.resources as rres
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.utils.project_manager import ProjectManager

    pdir = penv["pdir"]
    pmroot = tmp_path / "pmroot"
    pmroot.mkdir(exist_ok=True)
    shutil.copytree(pdir, pmroot / pdir.name, dirs_exist_ok=True)
    orig = rres.get_project_manager
    rres.get_project_manager = lambda: ProjectManager(base_dir=str(pmroot))
    app = FastAPI()
    app.include_router(rres.router)
    c = TestClient(app)
    try:
        name = pdir.name
        doc_rev = c.get(f"/api/project/{name}/resources").json()["document_revision"]
        rp = c.post(f"/api/project/{name}/props",
                    json={"name": "Compass", "expected_document_revision": doc_rev})
        assert rp.status_code == 200, rp.text
        prop_id = rp.json()["target"]
        img = pmroot / name / "o" / "compass_ref.png"   # HTTP 服务操作的是 pmroot 副本目录
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(b"PNG-COMPASS")
        doc_rev = c.get(f"/api/project/{name}/resources").json()["document_revision"]
        rr = c.post(f"/api/project/{name}/reference-assets",
                    json={"local": "o/compass_ref.png", "expected_document_revision": doc_rev})
        assert rr.status_code == 200, rr.text
        asset_id = rr.json()["target"]
        ra = c.post(f"/api/project/{name}/props/{prop_id}/references",
                    json={"asset_id": asset_id,
                          "expected_revision": c.get(f"/api/project/{name}/props/{prop_id}").json()["revision"]})
        assert ra.status_code == 200, ra.text
        prop_rev = c.get(f"/api/project/{name}/props/{prop_id}").json()["revision"]
        # 无证据 approve → 409
        r409 = c.post(f"/api/project/{name}/props/{prop_id}/approve",
                      json={"asset_id": asset_id, "expected_revision": prop_rev})
        assert r409.status_code == 409
        assert r409.json()["detail"]["error"] == "review_not_passed"
        # service 层注入 pass 证据（fake vision）→ HTTP approve 200
        run(review_prop_reference(pmroot / name, prop_id, agnes_client=FakeVision(PROP_PASS_JSON)))
        prop_rev = c.get(f"/api/project/{name}/props/{prop_id}").json()["revision"]
        r200 = c.post(f"/api/project/{name}/props/{prop_id}/approve",
                      json={"asset_id": asset_id, "expected_revision": prop_rev})
        assert r200.status_code == 200 and r200.json()["verified"] is True, r200.text
        read = c.get(f"/api/project/{name}/props/{prop_id}").json()
        assert read["approval_status"] == "approved"
        assert read["latest_reviews"][asset_id]["overall"] == "pass"
    finally:
        rres.get_project_manager = orig
