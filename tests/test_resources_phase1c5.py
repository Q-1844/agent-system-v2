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

from tests.test_resources_phase1c1 import (FakeAgnes, make_project, make_view_files, run,
                                           _current_look_rev)
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


async def _seed_prop(env, *, name="Wooden Sword", description="a short wooden training sword",
                     img_name="sword_ref.png"):
    """建 prop + 绑定一张真实小图参考，返回 (prop_id, asset_id)。"""
    ws = env["ws"]
    img = env["pdir"] / "o" / img_name
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(f"PNG-REF-{img_name}".encode())
    r = await ws.create_prop(name=name, description=description,
                             expected_document_revision=document_revision(ws.store.load()))
    assert r["ok"], r
    prop_id = r["target"]
    r2 = await ws.register_reference_asset(
        local=f"o/{img_name}", view="hero", purpose="prop",
        expected_document_revision=r["document_revision_after"])
    assert r2["ok"], r2
    asset_id = r2["target"]
    r3 = await ws.attach_prop_reference(prop_id, asset_id,
                                        expected_revision=_prop_rev(env, prop_id))
    assert r3["ok"], r3
    return prop_id, asset_id


PROP_PASS_JSON = _json.dumps({
    "prop_fidelity": "pass", "fidelity_notes": "wooden training sword as described",
    "visual_clarity": "pass", "clarity_notes": "whole object readable",
    "visual_defects": "pass", "defect_notes": "none",
    "project_style_consistency": "pass", "style_notes": "ok",
    "overall": "pass", "summary": "clean prop reference"})
PROP_NEEDSFIX_JSON = _json.dumps({
    "prop_fidelity": "fail", "fidelity_notes": "this is a metal sword, not wooden",
    "visual_clarity": "pass", "clarity_notes": "ok",
    "visual_defects": "pass", "defect_notes": "none",
    "project_style_consistency": "pass", "style_notes": "ok",
    "overall": "pass",   # 模型自报 pass —— 服务端必须推导 needs_fix
    "summary": "wrong material"})
PROP_CONCERN_JSON = _json.dumps({
    "prop_fidelity": "concern", "fidelity_notes": "grip color differs",
    "visual_clarity": "pass", "clarity_notes": "ok",
    "visual_defects": "pass", "defect_notes": "none",
    "project_style_consistency": "pass", "style_notes": "ok",
    "overall": "pass", "summary": "minor concern"})


def test_prop_review_pass_flow_and_evidence(penv):
    prop_id, asset_id = run(_seed_prop(penv))
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
    prop_id, asset_id = run(_seed_prop(penv))
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
    prop_id, asset_id = run(_seed_prop(penv))
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
    prop_id, asset_id = run(_seed_prop(penv))
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
    prop_id, asset_id = run(_seed_prop(penv))
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
    prop_id, asset_id = run(_seed_prop(penv))
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
    prop_id, _ = run(_seed_prop(penv))
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


# ============ Commit C：Composition CRUD + 生成核心（§6/§8-§14）============
from src.core.write_lock import project_write_lock
from src.resources.generation import MAX_COMPOSITION_INPUT_REFS
from src.resources import generation_records as _grecs
from src.resources.composition_prompt import build_composition_prompt
from src.resources.look_review import review_look_candidates
from tests.test_resources_phase1c1 import FakeAgnes as _FA  # noqa: F401
from tests.test_resources_phase1c2 import PASS_JSON as LOOK_PASS_JSON


def _comp_rev(env, comp_id):
    from src.resources.models import composition_revision as _cr
    return _cr(env["ws"].store.load().compositions[comp_id])


async def _seed_approved_look(env, *, label="Alice · Black Coat"):
    """clothing + look + 生成 1 候选 + pass 审查 + approve → (look_id, clothing_id)。"""
    ws = env["ws"]
    r = await ws.create_clothing(name="Black Coat", description="long black wool coat",
                                 expected_document_revision=document_revision(ws.store.load()))
    assert r["ok"], r
    clothing_id = r["target"]
    r2 = await ws.create_look(character_name="Alice", label=label, clothing_id=clothing_id,
                              expected_document_revision=r["document_revision_after"])
    assert r2["ok"], r2
    look_id = r2["target"]
    r3 = await ws.generate_look_candidates(look_id, expected_revision=_current_look_rev(env, look_id),
                                           agnes_client=FakeAgnes(images=[b"LOOK-CAND"]))
    assert r3["ok"], r3
    asset_id = r3["candidate_asset_ids"][0]
    from src.resources.look_review import review_look_candidates as _rv
    rv = await _rv(env["pdir"], look_id, agnes_client=FakeVision(LOOK_PASS_JSON))
    assert rv["ok"], rv
    r4 = await ws.approve_look_reference(look_id, asset_id,
                                         expected_revision=_current_look_rev(env, look_id))
    assert r4["ok"], r4
    return look_id, clothing_id


async def _seed_approved_prop(env, *, name="Wooden Sword", description="a short wooden training sword",
                              img_name="sword_ref.png"):
    """prop + 参考图 + pass 审查 + approve → prop_id。"""
    prop_id, asset_id = await _seed_prop(env, name=name, description=description, img_name=img_name)
    from src.resources.prop_review import review_prop_reference as _rv
    rv = await _rv(env["pdir"], prop_id, agnes_client=FakeVision(PROP_PASS_JSON))
    assert rv["ok"], rv
    r = await env["ws"].approve_prop_reference(prop_id, asset_id,
                                               expected_revision=_prop_rev(env, prop_id))
    assert r["ok"], r
    return prop_id


def _bind(prop_id, relation="held", anchor="right_hand", **kw):
    return {"prop_id": prop_id, "relation": relation, "anchor": anchor, **kw}


async def _seed_composition(env, *, bindings=None, label="Alice · Black Coat · Armed"):
    """approved look + approved props + composition → composition_id。
    bindings 默认：sword right_hand + compass left_hand。"""
    ws = env["ws"]
    look_id, _ = await _seed_approved_look(env)
    sword = await _seed_approved_prop(env)
    compass = await _seed_approved_prop(env, name="Brass Compass",
                                        description="a brass navigation compass",
                                        img_name="compass_ref.png")
    if bindings is None:
        bindings = [_bind(sword), _bind(compass, anchor="left_hand")]
    r = await ws.create_composition(character_name="Alice", look_id=look_id,
                                    prop_bindings=bindings, label=label,
                                    expected_document_revision=document_revision(ws.store.load()))
    assert r["ok"], r
    return r["target"], look_id, [b["prop_id"] for b in bindings]


# ---- T1 创建（2 props）----
def test_t1_create_composition_two_props(penv):
    comp_id, look_id, _ = run(_seed_composition(penv))
    doc = penv["ws"].store.load()
    c = doc.compositions[comp_id]
    assert c.composition_id == comp_id and comp_id.startswith("comp_")
    assert c.approval_status == "unreviewed" and c.generation_status == "none"
    assert len(c.prop_bindings) == 2
    assert c.prop_bindings[0].anchor == "right_hand" and c.prop_bindings[1].anchor == "left_hand"
    assert doc.looks[look_id].approval_status == "approved"


# ---- T2 缺 Character ----
def test_t2_missing_character_rejected(penv):
    ws = penv["ws"]
    look_id, _ = run(_seed_approved_look(penv))
    r = run(ws.create_composition(character_name="Nobody", look_id=look_id,
                                  prop_bindings=[_bind("prop_x")],
                                  expected_document_revision=document_revision(ws.store.load())))
    assert r["ok"] is False and r["error"] == "character_not_found"


# ---- T3 未 approved Look 拒绝 / look 角色不匹配 ----
def test_t3_non_approved_look_rejected(penv):
    ws = penv["ws"]
    r = run(ws.create_clothing(name="Black Coat", expected_document_revision=document_revision(ws.store.load())))
    r2 = run(ws.create_look(character_name="Alice", clothing_id=r["target"],
                            expected_document_revision=r["document_revision_after"]))
    look_id = r2["target"]     # 未生成未审查 → unreviewed
    r3 = run(ws.create_composition(character_name="Alice", look_id=look_id,
                                   prop_bindings=[_bind("prop_x")],
                                   expected_document_revision=r2["document_revision_after"]))
    assert r3["ok"] is False and r3["error"] == "look_not_approved"
    # look 角色不匹配：给项目加第二个 Character "Bob"（存在，但 look 属于 Alice）
    from src.core.models import Character as _Char
    proj = ProjectStore(penv["pdir"]).load()
    proj.characters.append(_Char(name="Bob", description_en="another character"))
    ProjectStore(penv["pdir"]).save(proj)
    r4 = run(ws.create_composition(character_name="Bob", look_id=look_id,
                                   prop_bindings=[_bind("prop_x")],
                                   expected_document_revision=document_revision(ws.store.load())))
    assert r4["ok"] is False and r4["error"] == "look_character_mismatch"
    # look 不存在
    r5 = run(ws.create_composition(character_name="Alice", look_id="look_none",
                                   prop_bindings=[_bind("prop_x")],
                                   expected_document_revision=document_revision(ws.store.load())))
    assert r5["ok"] is False and r5["error"] == "look_not_found"


# ---- T4/T5 prop 缺失 / 无 approved reference ----
def test_t4_missing_prop_rejected(penv):
    ws = penv["ws"]
    look_id, _ = run(_seed_approved_look(penv))
    r = run(ws.create_composition(character_name="Alice", look_id=look_id,
                                  prop_bindings=[_bind("prop_none")],
                                  expected_document_revision=document_revision(ws.store.load())))
    assert r["ok"] is False and r["error"] == "prop_not_found"


def test_t5_prop_without_approved_ref_rejected_at_generation(penv):
    """创建时 prop 只需存在（§6）；生成时无 approved reference → prop_reference_not_approved。"""
    ws = penv["ws"]
    look_id, _ = run(_seed_approved_look(penv))
    prop_id, _ = run(_seed_prop(penv, name="Raw Sword"))     # 有参考图但未审查未批准
    r = run(ws.create_composition(character_name="Alice", look_id=look_id,
                                  prop_bindings=[_bind(prop_id)],
                                  expected_document_revision=document_revision(ws.store.load())))
    assert r["ok"], r
    comp_id = r["target"]
    g = run(ws.generate_composition_candidates(comp_id, expected_revision=_comp_rev(penv, comp_id),
                                               agnes_client=FakeAgnes()))
    assert g["ok"] is False and g["error"] == "prop_reference_not_approved", g
    assert g.get("generation_record") is None or g["generation_record"]["error"] == "prop_reference_not_approved"
    assert ws.store.load().compositions[comp_id].generation_status == "none"   # 状态未动


# ---- T6/T7 1/2 props 合法 ----
def test_t6_t7_valid_composition_counts(penv):
    ws = penv["ws"]
    look_id, _ = run(_seed_approved_look(penv))
    p1 = run(_seed_approved_prop(penv, name="P1"))
    r1 = run(ws.create_composition(character_name="Alice", look_id=look_id,
                                   prop_bindings=[_bind(p1)],
                                   expected_document_revision=document_revision(ws.store.load())))
    assert r1["ok"], r1
    p2 = run(_seed_approved_prop(penv, name="P2", img_name="p2.png"))
    p3 = run(_seed_approved_prop(penv, name="P3", img_name="p3.png"))
    r2 = run(ws.create_composition(character_name="Alice", look_id=look_id,
                                   prop_bindings=[_bind(p2, anchor="left_hand"), _bind(p3, anchor="back")],
                                   expected_document_revision=document_revision(ws.store.load())))
    assert r2["ok"], r2
    # 4 个 → 拒
    p4 = run(_seed_approved_prop(penv, name="P4", img_name="p4.png"))
    r3 = run(ws.create_composition(character_name="Alice", look_id=look_id,
                                   prop_bindings=[_bind(p1), _bind(p2), _bind(p3), _bind(p4)],
                                   expected_document_revision=document_revision(ws.store.load())))
    assert r3["ok"] is False and r3["error"] == "invalid_prop_count"
    # 0 个 → 拒
    r4 = run(ws.create_composition(character_name="Alice", look_id=look_id,
                                   prop_bindings=[],
                                   expected_document_revision=document_revision(ws.store.load())))
    assert r4["ok"] is False and r4["error"] == "invalid_prop_count"


# ---- T8 重复 prop binding / 非法 enum ----
def test_t8_duplicate_and_invalid_bindings_rejected(penv):
    ws = penv["ws"]
    look_id, _ = run(_seed_approved_look(penv))
    p1 = run(_seed_approved_prop(penv))
    rev = document_revision(ws.store.load())
    r = run(ws.create_composition(character_name="Alice", look_id=look_id,
                                  prop_bindings=[_bind(p1), _bind(p1, anchor="left_hand")],
                                  expected_document_revision=rev))
    assert r["ok"] is False and r["error"] == "duplicate_prop_binding"
    r2 = run(ws.create_composition(character_name="Alice", look_id=look_id,
                                   prop_bindings=[_bind(p1, relation="wield")],
                                   expected_document_revision=document_revision(ws.store.load())))
    assert r2["ok"] is False and r2["error"] == "invalid_binding"
    r3 = run(ws.create_composition(character_name="Alice", look_id=look_id,
                                   prop_bindings=[_bind(p1, anchor="sky")],
                                   expected_document_revision=document_revision(ws.store.load())))
    assert r3["ok"] is False and r3["error"] == "invalid_binding"


# ---- T9 rename 不变 id ----
def test_t9_composition_id_stable_on_rename(penv):
    comp_id, *_ = run(_seed_composition(penv))
    rev = _comp_rev(penv, comp_id)
    r = run(penv["ws"].update_composition(comp_id, {"label": "Renamed"},
                                          expected_revision=rev))
    assert r["ok"] and r["verified"]
    assert r["after"]["composition_id"] == comp_id
    assert r["revision"] != rev


# ---- 生成核心：三阶段 + commit ----
def test_composition_generation_commits_full_chain(penv):
    ws = penv["ws"]
    comp_id, look_id, prop_ids = run(_seed_composition(penv))
    rev = _comp_rev(penv, comp_id)
    g = run(ws.generate_composition_candidates(comp_id, expected_revision=rev,
                                               agnes_client=FakeAgnes(images=[b"COMP-CAND"])))
    assert g["ok"] and g["status"] == "committed" and g["verified"], g
    doc = ws.store.load()
    c = doc.compositions[comp_id]
    assert c.generation_status == "ready"
    assert c.approval_status == "unreviewed"                       # T19：不自动 approve
    assert c.approved_reference_asset_ids == []
    assert c.reference_asset_ids == g["candidate_asset_ids"]
    asset = doc.reference_assets[g["candidate_asset_ids"][0]]
    assert asset.source == "generated" and asset.purpose == "composition"
    assert (penv["pdir"] / asset.local).is_file()
    assert asset.local.startswith(f"output/resources/compositions/{comp_id}/generations/")
    # generation record：无 base64 + composition_id 字段
    recs = _grecs.read_records(penv["pdir"], composition_id=comp_id)
    assert len(recs) == 1 and recs[0]["operation"] == "composition.generate_candidates"
    assert recs[0]["verified"] is True and recs[0]["status"] == "committed"
    assert "data:image" not in _json.dumps(recs)
    assert "base64" not in (penv["pdir"] / "output" / "resources.json").read_text(encoding="utf-8")
    # basis 覆盖 §8 关键字段
    basis = recs[0]["basis"]
    assert basis["look_id"] == look_id and basis["composition_id"] == comp_id
    assert basis["character_identity_fingerprint"]
    assert basis["look_revision"] and len(basis["look_revision"]) == 8
    for b in basis["prop_bindings"]:
        assert b["prop_id"] in prop_ids and b["prop_revision"] and b["relation"] and b["anchor"]
        assert b["approved_reference_ids_used"] and b["reference_snapshot"][0]["sha256"]
    assert basis["selected_input_refs"] and basis["basis_fingerprint"]


# ---- prompt 语义（§11）----
def test_composition_prompt_semantics():
    kw = dict(character_description="a girl with silver hair",
              look_clothing_snapshot={"name": "Black Coat", "description": "long black wool coat",
                                      "design_traits": "high collar", "materials": ["wool"],
                                      "colors": ["#111"]},
              bindings=[{"prop_name": "Wooden Sword", "prop_description": "wooden",
                         "relation": "held", "anchor": "right_hand", "visual_state_note": ""},
                        {"prop_name": "Compass", "prop_description": "brass",
                         "relation": "held", "anchor": "left_hand", "visual_state_note": "lid open"}],
              pose_note="standing", composition_description="")
    p1 = build_composition_prompt(**kw)
    assert p1 == build_composition_prompt(**kw)                    # 确定性
    assert "RIGHT HAND" in p1 and "LEFT HAND" in p1
    assert "Wooden Sword" in p1 and "Compass" in p1
    assert "never merge, fuse or swap" in p1                       # 不融合不串位
    assert "clearly visible" in p1
    assert "pure white backdrop" in p1                             # 审核安全措辞
    assert "no story action" in p1 and "no camera movement" in p1  # 不含 Shot 语义


# ---- 参考预算（§12）----
def test_reference_budget_selection_deterministic(penv):
    ws = penv["ws"]
    # 双 look approved ref（生成两次 + 两次 pass + approve 两个）
    look_id, _ = run(_seed_approved_look(penv))
    g2 = run(ws.generate_look_candidates(look_id, expected_revision=_current_look_rev(penv, look_id),
                                         agnes_client=FakeAgnes(images=[b"SECOND-LOOK"])))
    assert g2["ok"]
    a2 = g2["candidate_asset_ids"][0]
    run(review_look_candidates(penv["pdir"], look_id, agnes_client=FakeVision(LOOK_PASS_JSON)))
    r = run(ws.approve_look_reference(look_id, a2, expected_revision=_current_look_rev(penv, look_id),
                                      set_cover=False))
    assert r["ok"]
    sword = run(_seed_approved_prop(penv))
    compass = run(_seed_approved_prop(penv, name="Compass", img_name="compass_ref.png"))
    rcomp = run(ws.create_composition(character_name="Alice", look_id=look_id,
                                      prop_bindings=[_bind(sword), _bind(compass, anchor="left_hand")],
                                      expected_document_revision=document_revision(ws.store.load())))
    comp_id = rcomp["target"]
    g = run(ws.generate_composition_candidates(comp_id, expected_revision=_comp_rev(penv, comp_id),
                                               agnes_client=FakeAgnes(images=[b"COMP"])))
    assert g["ok"], g
    basis = g["generation_record"]["basis"]
    roles = [e["role"] for e in basis["selected_input_refs"]]
    # 2 bindings + 2 approved look refs → look_hero + 2 prop_hero + 1 extra angle
    assert roles.count("look_hero") == 1 and roles.count("look_extra_angle") == 1
    assert roles.count("prop_hero") == 2
    assert basis["input_budget"] == {"max": MAX_COMPOSITION_INPUT_REFS, "used": 4}
    # 输入顺序确定性：look_hero 在前、extra 在最后
    assert roles[0] == "look_hero" and roles[-1] == "look_extra_angle"
    # 只使用 approved refs（FakeAgnes 看到的 data URI 数量 == 选择数）
    assert len(g["generation_record"]) >= 0
    # 1 binding → 无 extra angle
    r1 = run(ws.create_composition(character_name="Alice", look_id=look_id,
                                   prop_bindings=[_bind(sword)],
                                   expected_document_revision=document_revision(ws.store.load())))
    g1 = run(ws.generate_composition_candidates(r1["target"],
                                                expected_revision=_comp_rev(penv, r1["target"]),
                                                agnes_client=FakeAgnes(images=[b"COMP1"])))
    roles1 = [e["role"] for e in g1["generation_record"]["basis"]["selected_input_refs"]]
    assert roles1 == ["look_hero", "prop_hero"]
    assert MAX_COMPOSITION_INPUT_REFS == 6


# ---- T10 同 composition single-flight ----
def test_t10_same_composition_single_flight(penv):
    ws = penv["ws"]
    comp_id, *_ = run(_seed_composition(penv))
    a_in = asyncio.Event()
    release_a = asyncio.Event()

    async def hang_a(i):
        a_in.set()
        await asyncio.wait_for(release_a.wait(), 10)

    async def scenario():
        t_a = asyncio.create_task(ws.generate_composition_candidates(
            comp_id, expected_revision=_comp_rev(penv, comp_id), agnes_client=FakeAgnes(on_call=hang_a)))
        await asyncio.wait_for(a_in.wait(), 5)
        rev_now = _comp_rev(penv, comp_id)
        fake_b = FakeAgnes()
        r_b = await ws.generate_composition_candidates(comp_id, expected_revision=rev_now,
                                                       agnes_client=fake_b)
        assert ws.store.load().compositions[comp_id].generation_status == "generating"
        release_a.set()
        r_a = await t_a
        return r_b, fake_b, r_a

    r_b, fake_b, r_a = run(scenario())
    assert r_b["ok"] is False and r_b["error"] == "generation_in_progress"
    assert fake_b.calls == 0
    assert r_a["ok"] and r_a["status"] == "committed"


# ---- T11 不同 composition 并发 ----
def test_t11_different_compositions_concurrent(penv):
    ws = penv["ws"]
    comp1, *_ = run(_seed_composition(penv, label="C1"))
    comp2, *_ = run(_seed_composition(penv, label="C2"))
    a_in, b_in = asyncio.Event(), asyncio.Event()
    rel_a, rel_b = asyncio.Event(), asyncio.Event()

    async def hang_a(i):
        a_in.set()
        await asyncio.wait_for(rel_a.wait(), 10)

    async def hang_b(i):
        b_in.set()
        await asyncio.wait_for(rel_b.wait(), 10)

    async def scenario():
        t_a = asyncio.create_task(ws.generate_composition_candidates(
            comp1, expected_revision=_comp_rev(penv, comp1), agnes_client=FakeAgnes(on_call=hang_a)))
        t_b = asyncio.create_task(ws.generate_composition_candidates(
            comp2, expected_revision=_comp_rev(penv, comp2), agnes_client=FakeAgnes(on_call=hang_b)))
        await asyncio.wait_for(asyncio.gather(a_in.wait(), b_in.wait()), 5)
        rel_a.set()
        rel_b.set()
        return await asyncio.gather(t_a, t_b)

    r_a, r_b = run(scenario())
    assert r_a["ok"] and r_b["ok"]
    doc = ws.store.load()
    assert doc.compositions[comp1].generation_status == "ready"
    assert doc.compositions[comp2].generation_status == "ready"


# ---- T12 生成期间不持写锁 ----
def test_t12_provider_not_holding_write_lock(penv):
    comp_id, *_ = run(_seed_composition(penv))

    async def check_lock(i):
        lock = project_write_lock(penv["pdir"].name)
        assert lock.locked() is False, "generate_image 期间绝不持 project_write_lock"

    g = run(penv["ws"].generate_composition_candidates(
        comp_id, expected_revision=_comp_rev(penv, comp_id),
        agnes_client=FakeAgnes(on_call=check_lock)))
    assert g["ok"], g


# ---- T13/T14/T15 生成中改 Look/Prop/binding → stale 拒绝 commit ----
def test_t13_stale_look_basis_rejects_commit(penv):
    ws = penv["ws"]
    comp_id, look_id, _ = run(_seed_composition(penv))

    async def mutate(i):
        r = await ws.update_look(look_id, {"appearance_delta": "changed mid-flight"},
                                 expected_revision=_current_look_rev(penv, look_id))
        assert r["ok"], r

    g = run(ws.generate_composition_candidates(
        comp_id, expected_revision=_comp_rev(penv, comp_id),
        agnes_client=FakeAgnes(on_call=mutate)))
    assert g["ok"] is False and g["status"] == "stale_basis" and g["error"] == "stale_generation_basis"
    c = ws.store.load().compositions[comp_id]
    assert c.generation_status == "none"                # 诚实还原
    assert c.reference_asset_ids == []                  # 未 attach 旧结果
    rec = _grecs.read_records(penv["pdir"], composition_id=comp_id)
    assert rec[-1]["status"] == "stale_basis" and rec[-1]["verified"] is False
    # stale 后可重试
    g2 = run(ws.generate_composition_candidates(comp_id, expected_revision=_comp_rev(penv, comp_id),
                                                agnes_client=FakeAgnes(images=[b"RETRY"])))
    assert g2["ok"] and g2["status"] == "committed"


def test_t14_stale_prop_basis_rejects_commit(penv):
    ws = penv["ws"]
    comp_id, _, prop_ids = run(_seed_composition(penv))

    async def mutate(i):
        r = await ws.update_prop(prop_ids[0], {"description": "changed mid-flight"},
                                 expected_revision=_prop_rev(penv, prop_ids[0]))
        assert r["ok"], r

    g = run(ws.generate_composition_candidates(
        comp_id, expected_revision=_comp_rev(penv, comp_id),
        agnes_client=FakeAgnes(on_call=mutate)))
    assert g["ok"] is False and g["status"] == "stale_basis"
    assert ws.store.load().compositions[comp_id].reference_asset_ids == []


def test_t15_changed_binding_rejects_commit(penv):
    ws = penv["ws"]
    comp_id, _, prop_ids = run(_seed_composition(penv))

    async def mutate(i):
        r = await ws.update_composition(
            comp_id,
            {"prop_bindings": [_bind(prop_ids[0], anchor="left_hand"), _bind(prop_ids[1], anchor="back")]},
            expected_revision=_comp_rev(penv, comp_id))
        assert r["ok"], r

    g = run(ws.generate_composition_candidates(
        comp_id, expected_revision=_comp_rev(penv, comp_id),
        agnes_client=FakeAgnes(on_call=mutate)))
    assert g["ok"] is False and g["status"] == "stale_basis"
    assert ws.store.load().compositions[comp_id].reference_asset_ids == []


# ---- T16 cancel cleanup ----
class HangingForeverAgnes:
    def __init__(self):
        self.models = {"image": "agnes-image-test"}

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        await asyncio.Event().wait()


def test_t16_cancel_cleanup_writes_failed_and_reraises(penv):
    ws = penv["ws"]
    comp_id, *_ = run(_seed_composition(penv))

    async def scenario():
        t = asyncio.create_task(ws.generate_composition_candidates(
            comp_id, expected_revision=_comp_rev(penv, comp_id),
            agnes_client=HangingForeverAgnes()))
        await asyncio.sleep(0.2)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t                       # cancel 向上传播，不吞
        return ws.store.load().compositions[comp_id]

    c = run(scenario())
    assert c.generation_status == "failed"
    rec = _grecs.read_records(penv["pdir"], composition_id=comp_id)
    assert rec[-1]["status"] == "failed" and rec[-1]["error"] == "cancelled"
    # 终态后 single-flight 放行
    g2 = run(ws.generate_composition_candidates(comp_id, expected_revision=_comp_rev(penv, comp_id),
                                                agnes_client=FakeAgnes(images=[b"AFTER-CANCEL"])))
    assert g2["ok"] and g2["status"] == "committed"


# ---- T17/T18 startup orphan sweep + 幂等 ----
def test_t17_t18_startup_orphan_sweep_compositions(penv):
    ws = penv["ws"]
    pdir = penv["pdir"]
    comp_id, *_ = run(_seed_composition(penv))
    healthy_id, *_ = run(_seed_composition(penv, label="Healthy"))
    # 模拟死进程磁盘态：comp generating
    doc = ws.store.load()
    doc.compositions[comp_id].generation_status = "generating"
    ws.store.save(doc)
    res_path = pdir / "output" / "resources.json"

    from src.resources.recovery import recover_orphaned_generations
    r = run(recover_orphaned_generations(pdir, pdir.name))
    assert r["ok"] and r["recovered"] == [comp_id]
    assert r.get("recovered_compositions") == [comp_id]
    doc = ws.store.load()
    assert doc.compositions[comp_id].generation_status == "failed"
    assert doc.compositions[healthy_id].generation_status == "none"     # 健康者零改动
    rec = _grecs.read_records(penv["pdir"], composition_id=comp_id)
    assert rec[-1]["error"] == "orphaned_by_process_restart"
    assert rec[-1]["basis"] is None and rec[-1]["generation_id"].startswith("gen_orphan_")
    # 幂等：第二遍零写入
    raw = res_path.read_bytes()
    r2 = run(recover_orphaned_generations(pdir, pdir.name))
    assert r2["recovered"] == []
    assert res_path.read_bytes() == raw
    # 恢复后可再次生成
    g = run(ws.generate_composition_candidates(comp_id, expected_revision=_comp_rev(penv, comp_id),
                                               agnes_client=FakeAgnes(images=[b"AFTER-SWEEP"])))
    assert g["ok"] and g["status"] == "committed"


# ---- delete_prop 引用保护 ----
def test_delete_prop_with_composition_reference_blocked(penv):
    ws = penv["ws"]
    comp_id, _, prop_ids = run(_seed_composition(penv))
    r = run(ws.delete_prop(prop_ids[0], expected_revision=_prop_rev(penv, prop_ids[0])))
    assert r["ok"] is False and r["error"] == "resource_in_use"
    assert r["used_by_compositions"] == [comp_id]


# ---- T34 重启持久化 ----
def test_t34_restart_persistence(penv):
    ws = penv["ws"]
    comp_id, look_id, prop_ids = run(_seed_composition(penv))
    g = run(ws.generate_composition_candidates(comp_id, expected_revision=_comp_rev(penv, comp_id),
                                               agnes_client=FakeAgnes(images=[b"PERSIST"])))
    assert g["ok"]
    from src.resources.service import ResourceService
    svc2 = ResourceService(penv["pdir"], penv["pdir"].name)      # 全新实例（重启语义）
    read = svc2.read_composition(comp_id)
    assert read["composition_id"] == comp_id
    assert read["generation_status"] == "ready"
    assert [a["asset_id"] for a in read["reference_assets"]] == g["candidate_asset_ids"]
    assert read["look"]["look_id"] == look_id
    assert [b["prop"]["prop_id"] for b in read["prop_bindings_resolved"]] == prop_ids
    lst = svc2.list_compositions(character_name="Alice")
    assert [c["id"] for c in lst] == [comp_id]


# ---- T36 project.json 字节不变（composition 全链路）----
def test_t36_project_json_byte_unchanged(penv):
    ws = penv["ws"]
    pdir = penv["pdir"]
    pj = pdir / "output" / "project.json"
    before = pj.read_bytes()
    comp_id, _, _ = run(_seed_composition(penv))
    run(ws.generate_composition_candidates(comp_id, expected_revision=_comp_rev(penv, comp_id),
                                           agnes_client=FakeAgnes(images=[b"X"])))
    assert pj.read_bytes() == before


# ============ Commit D：Composition Visual Review + Approval（§15/§16）============
from src.resources.composition_review import (
    review_composition_candidates,
    has_composition_pass_evidence,
    latest_composition_review_for_asset,
    derive_composition_overall,
    derive_binding_dimensions,
    read_composition_review_records,
)


def _bind_review_entry(prop_id, *, object_correct="pass", holder_correct="pass",
                       anchor_correct="pass", visibility="visible", notes="ok"):
    return {"prop_id": prop_id, "object_correct": object_correct,
            "holder_correct": holder_correct, "anchor_correct": anchor_correct,
            "visibility": visibility, "notes": notes}


def _comp_review_json(*, character_identity="pass", look_consistency="pass",
                      bindings=None, visual_defects="pass", overall="pass",
                      summary="good composition"):
    return _json.dumps({
        "character_identity": character_identity, "character_notes": "ok",
        "look_consistency": look_consistency, "look_notes": "ok",
        "bindings": bindings or [],
        "project_style_consistency": "pass", "style_notes": "ok",
        "visual_defects": visual_defects, "defect_notes": "none",
        "overall": overall, "summary": summary})


def _make_comp_json(prop_ids, **kw):
    bindings = kw.pop("bindings", None) or [_bind_review_entry(p) for p in prop_ids]
    return _comp_review_json(bindings=bindings, **kw)


async def _seed_comp_with_candidate(env, *, images=(b"COMP-CAND",)):
    comp_id, look_id, prop_ids = await _seed_composition(env)
    g = await env["ws"].generate_composition_candidates(
        comp_id, expected_revision=_comp_rev(env, comp_id),
        candidate_count=len(images), agnes_client=FakeAgnes(images=list(images)))
    assert g["ok"], g
    return comp_id, list(g["candidate_asset_ids"]), prop_ids


# ---- 多图契约 + 审查通过全链路 ----
def test_composition_review_multi_image_contract_and_pass(penv):
    ws = penv["ws"]
    comp_id, asset_ids, prop_ids = run(_seed_comp_with_candidate(penv))
    res_path = penv["pdir"] / "output" / "resources.json"
    before = res_path.read_bytes()
    fv = FakeVision(_make_comp_json(prop_ids))
    r = run(review_composition_candidates(penv["pdir"], comp_id, agnes_client=fv))
    assert r["ok"] and r["status"] == "completed", r
    cr = r["candidate_reviews"][0]
    assert cr["asset_id"] == asset_ids[0] and cr["status"] == "reviewed"
    assert cr["overall"] == "pass"
    assert all(cr[d] == "pass" for d in ("character_identity", "look_consistency",
                                         "prop_fidelity", "prop_binding_correctness",
                                         "required_prop_visibility", "visual_defects"))
    # 契约面：多图输入（1 candidate + look refs + 2 prop refs）+ agnes-2.5-flash
    call = fv.calls[0]
    assert call["model"] == "agnes-2.5-flash"
    imgs = [c for c in call["messages"][0]["content"] if c["type"] == "image_url"]
    assert len(imgs) == 4, f"candidate+look+2 prop refs，实际 {len(imgs)}"
    assert imgs[0]["image_url"]["url"].startswith("data:image/png;base64,")
    text = call["messages"][0]["content"][0]["text"]
    assert "IMAGE 1" in text and "IMAGE 4" in text
    for p in prop_ids:
        assert p in text
    assert "RIGHT HAND" in text.replace("right_hand", "RIGHT HAND").upper() or "right_hand" in text
    # 证据：composition 流、无 base64、canonical 字节不变
    recs = read_composition_review_records(penv["pdir"], composition_id=comp_id)
    assert len(recs) == 1 and recs[0]["operation"] == "composition.review_candidates"
    assert "data:image" not in _json.dumps(recs)
    assert res_path.read_bytes() == before
    # read_composition latest_reviews 反映
    from src.resources.service import ResourceService
    read = ResourceService(penv["pdir"], penv["pdir"].name).read_composition(comp_id)
    assert read["latest_reviews"][asset_ids[0]]["overall"] == "pass"
    assert len(read["latest_reviews"][asset_ids[0]]["per_binding"]) == 2


# ---- T20-T24 六维度逐项 ----
def test_t20_identity_dimension_fail(penv):
    comp_id, _, prop_ids = run(_seed_comp_with_candidate(penv))
    r = run(review_composition_candidates(
        penv["pdir"], comp_id,
        agnes_client=FakeVision(_make_comp_json(prop_ids, character_identity="fail",
                                                overall="pass"))))
    cr = r["candidate_reviews"][0]
    assert cr["character_identity"] == "fail" and cr["overall"] == "needs_fix"
    assert cr["model_overall"] == "pass"      # 模型自报不采信


def test_t21_look_dimension_concern(penv):
    comp_id, _, prop_ids = run(_seed_comp_with_candidate(penv))
    r = run(review_composition_candidates(
        penv["pdir"], comp_id,
        agnes_client=FakeVision(_make_comp_json(prop_ids, look_consistency="concern"))))
    assert r["candidate_reviews"][0]["look_consistency"] == "concern"
    assert r["candidate_reviews"][0]["overall"] == "uncertain"


def test_t22_prop_fidelity_from_binding_object_correct(penv):
    comp_id, _, prop_ids = run(_seed_comp_with_candidate(penv))
    bindings = [_bind_review_entry(prop_ids[0]),
                _bind_review_entry(prop_ids[1], object_correct="fail",
                                   notes="this is a metal sword not a compass")]
    r = run(review_composition_candidates(
        penv["pdir"], comp_id,
        agnes_client=FakeVision(_make_comp_json(prop_ids, bindings=bindings))))
    cr = r["candidate_reviews"][0]
    assert cr["prop_fidelity"] == "fail"
    assert cr["overall"] == "needs_fix"
    per = {b["prop_id"]: b for b in cr["per_binding"]}
    assert per[prop_ids[1]]["object_correct"] == "fail"


def test_t23_holder_anchor_correctness_per_binding(penv):
    comp_id, _, prop_ids = run(_seed_comp_with_candidate(penv))
    # holder 错（剑浮在空中/另一人拿着）→ binding 维度 fail
    b1 = [_bind_review_entry(prop_ids[0], holder_correct="fail", notes="sword floating"),
          _bind_review_entry(prop_ids[1])]
    r = run(review_composition_candidates(penv["pdir"], comp_id,
                                          agnes_client=FakeVision(_comp_review_json(bindings=b1))))
    assert r["candidate_reviews"][0]["prop_binding_correctness"] == "fail"
    assert r["candidate_reviews"][0]["overall"] == "needs_fix"
    # anchor 错（罗盘到了右手）→ binding 维度 fail
    b2 = [_bind_review_entry(prop_ids[0]),
          _bind_review_entry(prop_ids[1], anchor_correct="fail", notes="compass in right hand")]
    r2 = run(review_composition_candidates(penv["pdir"], comp_id,
                                           agnes_client=FakeVision(_comp_review_json(bindings=b2))))
    assert r2["candidate_reviews"][0]["prop_binding_correctness"] == "fail"


def test_t24_missing_prop_detection(penv):
    comp_id, _, prop_ids = run(_seed_comp_with_candidate(penv))
    # 回复缺一个 binding（罗盘没被审到）→ fail closed
    b1 = [_bind_review_entry(prop_ids[0])]
    r = run(review_composition_candidates(penv["pdir"], comp_id,
                                          agnes_client=FakeVision(_comp_review_json(bindings=b1))))
    cr = r["candidate_reviews"][0]
    assert cr["prop_binding_correctness"] == "fail"      # 缺失 binding fail closed
    assert cr["required_prop_visibility"] == "fail"
    assert cr["overall"] == "needs_fix"
    # visibility=missing → required_prop_visibility fail
    b2 = [_bind_review_entry(prop_ids[0]),
          _bind_review_entry(prop_ids[1], visibility="missing", notes="compass absent")]
    r2 = run(review_composition_candidates(penv["pdir"], comp_id,
                                           agnes_client=FakeVision(_comp_review_json(bindings=b2))))
    assert r2["candidate_reviews"][0]["required_prop_visibility"] == "fail"
    assert r2["candidate_reviews"][0]["overall"] == "needs_fix"
    # obscured 同样 fail（required 必须清晰可见）
    b3 = [_bind_review_entry(prop_ids[0]),
          _bind_review_entry(prop_ids[1], visibility="obscured")]
    r3 = run(review_composition_candidates(penv["pdir"], comp_id,
                                           agnes_client=FakeVision(_comp_review_json(bindings=b3))))
    assert r3["candidate_reviews"][0]["required_prop_visibility"] == "fail"


def test_t25_overall_deterministic_derivation_pure():
    # 纯函数直证：任何 fail → needs_fix；全 pass → pass；concern/缺失 → uncertain
    full_pass = {d: "pass" for d in ("character_identity", "look_consistency", "prop_fidelity",
                                     "prop_binding_correctness", "required_prop_visibility",
                                     "visual_defects")}
    assert derive_composition_overall(full_pass) == "pass"
    assert derive_composition_overall({**full_pass, "visual_defects": "fail"}) == "needs_fix"
    assert derive_composition_overall({**full_pass, "look_consistency": "concern"}) == "uncertain"
    assert derive_composition_overall({k: None for k in full_pass}) == "uncertain"   # 缺失不判 pass
    # binding 派生：缺失 binding → fail closed（缺失=missing-prop 缺陷信号）
    d = derive_binding_dimensions([], [{"prop_id": "p1", "required_visibility": "required"}])
    assert d["prop_fidelity"] == "fail" and d["prop_binding_correctness"] == "fail"
    assert d["required_prop_visibility"] == "fail"
    # optional binding 不可见不拖垮 required 维度
    d2 = derive_binding_dimensions(
        [{"prop_id": "p1", "object_correct": "pass", "holder_correct": "pass",
          "anchor_correct": "pass", "visibility": "missing"}],
        [{"prop_id": "p1", "required_visibility": "optional"}])
    assert d2["required_prop_visibility"] == "pass"
    assert d2["prop_binding_correctness"] == "pass"


# ---- review 失败隔离 / entry 失败 ----
def test_composition_review_entry_failures_and_isolation(penv):
    comp_id, asset_ids, _ = run(_seed_comp_with_candidate(penv))
    # parse 失败 → uncertain，不可作为 pass 证据
    r = run(review_composition_candidates(penv["pdir"], comp_id, agnes_client=FakeVision("junk")))
    assert r["candidate_reviews"][0]["status"] == "parse_failed"
    assert not has_composition_pass_evidence(penv["pdir"], comp_id, asset_ids[0])
    # provider 错误隔离
    r2 = run(review_composition_candidates(penv["pdir"], comp_id,
                                           agnes_client=FakeVision(RuntimeError("boom"))))
    assert r2["candidate_reviews"][0]["status"] == "provider_error"
    # entry 失败族
    assert run(review_composition_candidates(penv["pdir"], "comp_none"))["error"] == "not_found"
    assert run(review_composition_candidates(penv["pdir"], comp_id,
                                             asset_ids=["ref_other"]))["error"] == "not_in_candidates"


# ---- T26-T28 approve / revoke ----
def test_t26_t27_t28_approve_gate_then_pass_then_revoke(penv):
    ws = penv["ws"]
    comp_id, asset_ids, prop_ids = run(_seed_comp_with_candidate(penv))
    asset_id = asset_ids[0]

    def rev():
        return _comp_rev(penv, comp_id)

    # ① 无证据 → 拒
    r = run(ws.approve_composition_candidate(comp_id, asset_id, expected_revision=rev()))
    assert r["ok"] is False and r["error"] == "review_not_passed"
    # ② needs_fix → 拒
    run(review_composition_candidates(
        penv["pdir"], comp_id,
        agnes_client=FakeVision(_make_comp_json(prop_ids, character_identity="fail"))))
    r = run(ws.approve_composition_candidate(comp_id, asset_id, expected_revision=rev()))
    assert r["ok"] is False and r["error"] == "review_not_passed"
    # ③ uncertain（concern）→ 拒
    run(review_composition_candidates(
        penv["pdir"], comp_id,
        agnes_client=FakeVision(_make_comp_json(prop_ids, look_consistency="concern"))))
    r = run(ws.approve_composition_candidate(comp_id, asset_id, expected_revision=rev()))
    assert r["ok"] is False and r["error"] == "review_not_passed"
    # ④ pass → 批准
    run(review_composition_candidates(penv["pdir"], comp_id,
                                      agnes_client=FakeVision(_make_comp_json(prop_ids))))
    r = run(ws.approve_composition_candidate(comp_id, asset_id, expected_revision=rev()))
    assert r["ok"] and r["verified"], r
    c = ws.store.load().compositions[comp_id]
    assert c.approved_reference_asset_ids == [asset_id]
    assert c.approval_status == "approved" and c.cover_asset_id == asset_id
    # 幂等 / not_in_candidates / OCC
    r2 = run(ws.approve_composition_candidate(comp_id, asset_id, expected_revision=rev()))
    assert r2["ok"] and r2.get("unchanged") is True
    r3 = run(ws.approve_composition_candidate(comp_id, "ref_other", expected_revision=rev()))
    assert r3["ok"] is False and r3["error"] == "not_in_candidates"
    r4 = run(ws.approve_composition_candidate(comp_id, asset_id, expected_revision="00000000"))
    assert r4["ok"] is False and r4["error"] == "stale_revision"
    # ⑤ T28 revoke
    r5 = run(ws.revoke_composition_approval(comp_id, asset_id, expected_revision=rev()))
    assert r5["ok"] and r5["verified"]
    c = ws.store.load().compositions[comp_id]
    assert c.approved_reference_asset_ids == [] and c.approval_status == "unreviewed"
    assert c.cover_asset_id is None and asset_id in c.reference_asset_ids
    r6 = run(ws.revoke_composition_approval(comp_id, asset_id, expected_revision=rev()))
    assert r6["ok"] and r6.get("unchanged") is True


# ============ Commit E：Shared Library publish/import（§17）============
from src.resources.shared_library import SharedLibraryService, SharedStore, SHARED_SCHEMA_VERSION


async def _seed_shared_ready_project(env, shared_dir):
    """A 项目：approved look + approved props + composition（候选已生成已审批）
    + 全依赖发布链（character→clothing→look→props→composition）。
    返回 dict（含 svc、各 shared id、candidate asset id）。"""
    ws = env["ws"]
    comp_id, look_id, prop_ids = await _seed_composition(env)
    g = await ws.generate_composition_candidates(
        comp_id, expected_revision=_comp_rev(env, comp_id),
        agnes_client=FakeAgnes(images=[b"SHARE-CAND"]))
    assert g["ok"], g
    cand_asset = g["candidate_asset_ids"][0]
    await review_composition_candidates(env["pdir"], comp_id,
                                        agnes_client=FakeVision(_make_comp_json(prop_ids)))
    r = await ws.approve_composition_candidate(comp_id, cand_asset,
                                               expected_revision=_comp_rev(env, comp_id))
    assert r["ok"], r
    svc = SharedLibraryService(env["pdir"], env["pdir"].name, shared_dir=shared_dir)
    r_char = await svc.publish_character("Alice")
    assert r_char["ok"], r_char
    look = ws.store.load().looks[look_id]
    r_cloth = await svc.publish(look.clothing_id)
    assert r_cloth["ok"], r_cloth
    r_look = await svc.publish(look_id)
    assert r_look["ok"], r_look
    shared_props = {}
    for pid in prop_ids:
        rp = await svc.publish(pid)
        assert rp["ok"], rp
        shared_props[pid] = rp["shared_id"]
    r_comp = await svc.publish(comp_id)
    assert r_comp["ok"], r_comp
    return {"comp_id": comp_id, "look_id": look_id, "prop_ids": prop_ids,
            "cand_asset": cand_asset, "svc": svc, "r_comp": r_comp,
            "shared_props": shared_props, "look": look}


def test_t29_publish_composition_full_dependency_chain(penv, tmp_path):
    shared_dir = tmp_path / "shared_library"
    info = run(_seed_shared_ready_project(penv, shared_dir))
    sdoc = SharedStore(shared_dir).load()
    assert sdoc.schema_version == SHARED_SCHEMA_VERSION == 2
    entry = sdoc.compositions[info["r_comp"]["shared_id"]]
    assert entry.scope == "shared"
    assert entry.look_id == info["r_comp"]["shared_look_id"]
    assert [b.prop_id for b in entry.prop_bindings] == list(info["shared_props"].values())
    assert len(entry.reference_asset_ids) == 1
    assert entry.approved_reference_asset_ids == entry.reference_asset_ids   # 发布快照已审批
    # 媒体真实复制
    sa = sdoc.reference_assets[entry.reference_asset_ids[0]]
    assert (shared_dir / "media" / Path(sa.local).name).is_file()
    # 项目 canonical 零改动（publish 只读项目）
    # （_seed_shared_ready_project 结束时的 canonical 与 publish 前一致——用 origin None 验证）
    proj_comp = penv["ws"].store.load().compositions[info["comp_id"]]
    assert proj_comp.origin is None
    # republish：同 shared id、version+1
    r2 = run(info["svc"].publish(info["comp_id"]))
    assert r2["ok"] and r2["shared_id"] == info["r_comp"]["shared_id"] and r2["version"] == 2


def test_t29b_publish_dependency_not_published(penv, tmp_path):
    shared_dir = tmp_path / "shared_library2"
    ws = penv["ws"]
    comp_id, look_id, prop_ids = run(_seed_composition(penv))
    svc = SharedLibraryService(penv["pdir"], penv["pdir"].name, shared_dir=shared_dir)
    r = run(svc.publish(comp_id))
    assert r["ok"] is False and r["error"] == "dependency_not_published"
    kinds = {m["kind"] for m in r["missing_dependencies"]}
    assert kinds == {"character", "look", "clothing", "prop"}


def test_t30_t31_import_composition_and_id_remap(penv, tmp_path):
    shared_dir = tmp_path / "shared_library"
    info = run(_seed_shared_ready_project(penv, shared_dir))
    shared_bytes_before = (shared_dir / "resources.json").read_bytes()
    # B 项目：有同名 Alice（独立目录）
    bdir = tmp_path / "projb"
    (bdir / "output").mkdir(parents=True)
    from src.core.models import Project, Character, Asset, ShotStatus
    proj = Project(name=bdir.name)
    c = Character(name="Alice", description_en="a girl with silver hair")
    c.views = {"front": Asset(local="o/alice_front_seg.png", url="https://x/front",
                              status=ShotStatus.DONE.value)}
    proj.characters = [c]
    ProjectStore(bdir).save(proj)
    # P1：profile-aware 生成测试环境默认写实
    from src.visual_profile import VisualProfileService as _VPS
    _VPS(bdir, bdir.name).update(render_family="photorealistic", expected_revision="")
    (bdir / "o").mkdir(exist_ok=True)
    (bdir / "o" / "alice_front_seg.png").write_bytes(b"PNG-B")

    svc_b = SharedLibraryService(bdir, bdir.name, shared_dir=shared_dir)
    from src.resources.store import ResourceStore as _RS
    doc_rev = document_revision(_RS(bdir, bdir.name).load())
    r = run(svc_b.import_resource(info["r_comp"]["shared_id"], expected_document_revision=doc_rev))
    assert r["ok"], r
    # T31 remap 全链
    bdoc = _RS(bdir, bdir.name).load()
    comp = bdoc.compositions[r["imported_id"]]
    assert comp.composition_id != info["r_comp"]["shared_id"]        # 新 id
    assert comp.scope == "project"
    assert comp.look_id == r["look_id"] and comp.look_id in bdoc.looks
    assert [b.prop_id for b in comp.prop_bindings] == \
        [r["prop_ids"][info["shared_props"][p]] for p in info["prop_ids"]]
    assert comp.origin.shared_resource_id == info["r_comp"]["shared_id"]
    assert comp.origin.source == "shared_library"
    # 候选媒体复制进项目且可读
    assert len(comp.reference_asset_ids) == 1
    a = bdoc.reference_assets[comp.reference_asset_ids[0]]
    assert (bdir / a.local).is_file()
    # approved/cover remap 到项目内资产（不悬空）
    assert comp.approved_reference_asset_ids == comp.reference_asset_ids
    assert comp.cover_asset_id in comp.reference_asset_ids
    assert comp.approval_status == "approved" and comp.generation_status == "ready"
    # 捆绑导入的 look：approved refs 也 remap 到 B 项目内资产（1C5 修复验证）
    blook = bdoc.looks[comp.look_id]
    assert blook.origin.shared_resource_id == info["r_comp"]["shared_look_id"]
    assert blook.approved_reference_asset_ids and \
        all(aid in bdoc.reference_assets for aid in blook.approved_reference_asset_ids)
    # 捆绑的 props 存在且 origin 指向共享母版
    for b in comp.prop_bindings:
        assert b.prop_id in bdoc.props
        assert bdoc.props[b.prop_id].origin.shared_resource_id in info["shared_props"].values()
    # shared 字节不变
    assert (shared_dir / "resources.json").read_bytes() == shared_bytes_before


def test_t30b_import_composition_without_character_rejected(penv, tmp_path):
    shared_dir = tmp_path / "shared_library3"
    info = run(_seed_shared_ready_project(penv, shared_dir))
    bdir = tmp_path / "projc"
    (bdir / "output").mkdir(parents=True)
    from src.core.models import Project
    ProjectStore(bdir).save(Project(name=bdir.name))    # 无 Alice
    from src.visual_profile import VisualProfileService as _VPS2
    _VPS2(bdir, bdir.name).update(render_family="photorealistic", expected_revision="")
    svc_b = SharedLibraryService(bdir, bdir.name, shared_dir=shared_dir)
    from src.resources.store import ResourceStore as _RS
    r = run(svc_b.import_resource(info["r_comp"]["shared_id"],
                                  expected_document_revision=document_revision(_RS(bdir, bdir.name).load())))
    assert r["ok"] is False and r["error"] == "character_not_found"
    # OCC stale
    (bdir / "output" / "resources.json").unlink(missing_ok=True)
    r2 = run(svc_b.import_resource(info["r_comp"]["shared_id"], expected_document_revision="badrev"))
    assert r2["ok"] is False and r2["error"] == "stale_revision"


def test_t32_t33_ab_isolation_and_shared_untouched(penv, tmp_path):
    shared_dir = tmp_path / "shared_library"
    info = run(_seed_shared_ready_project(penv, shared_dir))
    shared_path = shared_dir / "resources.json"
    shared_before = shared_path.read_bytes()

    def _make_project(name):
        pdir = tmp_path / name
        (pdir / "output").mkdir(parents=True)
        from src.core.models import Project, Character, Asset, ShotStatus
        proj = Project(name=name)
        c = Character(name="Alice", description_en="a girl with silver hair")
        c.views = {"front": Asset(local="o/alice_front_seg.png", url="https://x/f",
                                  status=ShotStatus.DONE.value)}
        proj.characters = [c]
        ProjectStore(pdir).save(proj)
        # P1：profile-aware 生成测试环境默认写实
        from src.visual_profile import VisualProfileService as _VPS3
        _VPS3(pdir, pdir.name).update(render_family="photorealistic",
                                      expected_revision="")
        (pdir / "o").mkdir(exist_ok=True)
        (pdir / "o" / "alice_front_seg.png").write_bytes(b"PNG")
        return pdir

    # B 导入
    bdir = _make_project("projb")
    svc_b = SharedLibraryService(bdir, bdir.name, shared_dir=shared_dir)
    from src.resources.store import ResourceStore as _RS
    rb = run(svc_b.import_resource(info["r_comp"]["shared_id"],
                                   expected_document_revision=document_revision(_RS(bdir, bdir.name).load())))
    assert rb["ok"], rb
    # C 导入（独立副本）
    cdir = _make_project("projc")
    svc_c = SharedLibraryService(cdir, cdir.name, shared_dir=shared_dir)
    rc = run(svc_c.import_resource(info["r_comp"]["shared_id"],
                                   expected_document_revision=document_revision(_RS(cdir, cdir.name).load())))
    assert rc["ok"], rc
    assert rb["imported_id"] != rc["imported_id"]      # 各自独立副本
    # A 改原版 + B 改副本 → shared 字节不变
    ws_a = penv["ws"]
    run(ws_a.update_composition(info["comp_id"], {"label": "A-edited"},
                                expected_revision=_comp_rev(penv, info["comp_id"])))
    wb = ResourceWriteService(bdir, bdir.name)
    run(wb.update_composition(rb["imported_id"], {"label": "B-edited"},
                              expected_revision=_comp_rev({"ws": wb}, rb["imported_id"])))
    assert shared_path.read_bytes() == shared_before   # T33：shared 字节级不变
    # C 的副本不受 A/B 影响（保持发布版本内容）
    cdoc = _RS(cdir, cdir.name).load()
    assert cdoc.compositions[rc["imported_id"]].label != "A-edited"
    assert cdoc.compositions[rc["imported_id"]].label != "B-edited"
    # B 再导入同一共享条目 → 仍获得共享发布版本（新副本，FK 直连既有 props/look 副本）
    rb2 = run(svc_b.import_resource(info["r_comp"]["shared_id"],
                                    expected_document_revision=document_revision(_RS(bdir, bdir.name).load())))
    assert rb2["ok"], rb2
    bdoc = _RS(bdir, bdir.name).load()
    comp2 = bdoc.compositions[rb2["imported_id"]]
    assert comp2.look_id == rb["look_id"]              # 查重：复用既有 look 副本，不重复导入


def test_shared_v1_file_lazy_upgrade(tmp_path):
    """共享库 v1 文件：可读（compositions={}），纯读零写回。"""
    shared_dir = tmp_path / "shared_libraryv1"
    shared_dir.mkdir()
    raw = {"schema_version": 1, "characters": {}, "looks": {}, "clothings": {},
           "props": {}, "reference_assets": {}, "meta": {}}
    p = shared_dir / "resources.json"
    p.write_text(_json.dumps(raw), encoding="utf-8")
    before = p.read_bytes()
    store = SharedStore(shared_dir)
    doc = store.load()
    assert doc.schema_version == SHARED_SCHEMA_VERSION == 2
    assert doc.compositions == {}
    assert p.read_bytes() == before
    with pytest.raises(ResourceSchemaError):
        (shared_dir / "bad").mkdir()
        (shared_dir / "bad" / "resources.json").write_text('{"schema_version": 7}')
        SharedStore(shared_dir / "bad").load()
