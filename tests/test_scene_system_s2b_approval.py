# tests/test_scene_system_s2b_approval.py
# Scene System V1 — Phase S2B：Approval saga + derived stale read model
# （§39 T36-T41 + §30/§31/§32/§33）。
#
# T36 approve without pass rejected
# T37 explicit approve success（attach + approved_basis_fingerprint + Resources 注册）
# T38 cross-canonical saga B→C stale path（orphan 不 attach 不偷删）
# T39 revoke
# T40 approved state view derived stale detection（全局态变化 → is_stale=true + reasons）
# T41 invisible state change no stale（south stairs 变化只 stale 能看到它的 view——§33）
# + §37 lifecycle：lighting 改变 → A/B 双 stale → 只 regen A → A 不 stale B 仍 stale
import asyncio
import base64
import json
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.models import Project, Scene
from src.resources.models import Prop as ResourceProp, ResourceDocument, ReferenceAsset
from src.resources.service import ResourceService
from src.resources.store import ResourceStore
from src.resources.write_service import ResourceWriteService
from src.scene_system.models import view_revision
from src.scene_system.store import SceneSystemStore
from src.scene_system.service import SceneSystemService
from src.scene_system.visual_service import SceneVisualService
from src.scene_system import generation_records, instance_view_review as ivr
from src.scene_system.generation_records import approval_records_path

sys_path = Path(__file__).parent
import sys
sys.path.insert(0, str(sys_path))
from test_scene_system_s2b_generation import (  # noqa: E402
    make_pdir, FakeClient, add_asset, _sciv_rev,
)

PNG_B64 = base64.b64encode(b"\x89PNG s2b-approval").decode()


class FakeGen:
    models = {"image": "fake-image-model"}

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        return {"url": None, "b64": PNG_B64}


class FakeVision:
    def __init__(self, content: str):
        self.content = content

    async def chat_completion_vision(self, msgs, *, model):
        return {"content": self.content, "usage": {}}


def _pass_reply(anchor_id: str, prop_id: str) -> str:
    return json.dumps({
        "location_identity": "pass", "view_consistency": "pass",
        "anchor_consistency": "pass",
        "scene_state": {"time_of_day": "pass", "weather": "pass",
                        "environment_conditions": "pass",
                        "anchor_states": [{"anchor_id": anchor_id, "verdict": "pass",
                                           "notes": ""}], "notes": ""},
        "lighting_weather": "pass",
        "props": [{"prop_id": prop_id, "observed": "correct", "verdict": "pass",
                   "notes": ""}],
        "visual_defects": "pass",
        "project_style_consistency": "pass", "style_notes": "ok",
        "overall": "pass", "summary": "ok"},
        ensure_ascii=False)


async def _seed_two_state_views(svc: SceneSystemService, vs: SceneVisualService, pdir: Path):
    """Lighthouse 场景：A（north→south，可见 north door/w2）+ B（south→north，可见 stairs）
    各自生成+review pass（未 approve）。"""
    async def drev():
        return svc.document_overview()["document_revision"]

    loc = (await svc.create_location(name="灯塔监听室",
                                     identity_description="circular control room",
                                     expected_document_revision=await drev()))["target"]
    a_n = (await svc.create_anchor(location_id=loc, name="north door", kind="door",
                                   expected_document_revision=await drev()))["target"]
    a_w2 = (await svc.create_anchor(location_id=loc, name="west window 02", kind="window",
                                    expected_document_revision=await drev()))["target"]
    a_s = (await svc.create_anchor(location_id=loc, name="south stairs", kind="stairs",
                                   expected_document_revision=await drev()))["target"]
    v_north = await svc.create_view(location_id=loc, label="North to South",
                                    visible_anchor_ids=[a_n, a_w2],
                                    expected_document_revision=await drev())
    v_south = await svc.create_view(location_id=loc, label="South to North",
                                    visible_anchor_ids=[a_s],
                                    expected_document_revision=await drev())
    inst = (await svc.create_scene_instance(story_scene_id="s09", location_id=loc,
                                            expected_document_revision=await drev()))["target"]
    rp = await svc.patch_scene_state(
        inst, {"time_of_day": "night", "weather": "storm", "lighting": "red emergency",
               "anchor_states": {a_w2: {"condition": "broken"}},
               "prop_placements": {"prop_sword01": {"anchor_id": a_n, "state": "intact"}}},
        expected_revision=svc.read_scene_instance(inst)["revision"])
    assert rp["ok"], rp
    # 两个 base views approve（捷径 + approval records）
    for vid, asset in ((v_north["target"], "ref_baseA000001"),
                       (v_south["target"], "ref_baseB000001")):
        add_asset(pdir, asset,
                  f"output/scene_system/locations/{loc}/views/{vid}/g/c_01.png")
        st = SceneSystemStore(pdir, pdir.name)
        async with st.write_lock():
            d = st.load()
            d.views[vid].approval_status = "approved"
            d.views[vid].reference_asset_id = asset
            st.save(d)
        doc = st.load()
        fp = ivr.expectations_fingerprint(
            ivr.build_scene_instance_review_expectations(doc, doc.views[vid],
                                                         doc.scene_instances[inst],
                                                         ResourceStore(pdir, pdir.name).load()),
            [asset])
        # 用 location_view 的期望指纹（S2A check_base 用 view_review 的 build）：
        from test_scene_system_s2b_generation import approval_record_fingerprint
        fp2 = approval_record_fingerprint(pdir, doc, doc.views[vid], [asset])
        generation_records.append_record(pdir, {
            "approval_id": "apv_s", "operation": "scene.approve_location_view",
            "status": "approved", "view_id": vid, "asset_id": asset,
            "reviewed_basis_fingerprint": fp2, "completed_at": 0.0,
        }, path=approval_records_path(pdir))
    # sciv A/B + 生成 + review pass
    made = {}
    for key, vid, aid, pid in (("A", v_north["target"], a_w2, "prop_sword01"),
                               ("B", v_south["target"], a_s, "prop_sword01")):
        drev = svc.document_overview()["document_revision"]
        r = await vs.create_scene_instance_view(scene_instance_id=inst, location_view_id=vid,
                                                expected_document_revision=drev)
        assert r["ok"], r
        sciv = r["target"]
        gen = await vs.generate_scene_instance_view(
            sciv, expected_revision=_sciv_rev(pdir, sciv), candidate_count=1,
            agnes_client=FakeGen(), include_peer=False)
        assert gen["ok"], gen.get("error")
        rr = await ivr.review_scene_instance_view_candidates(
            pdir, sciv, agnes_client=FakeVision(_pass_reply(aid, pid)))
        assert rr["ok"] and rr["candidate_reviews"][0]["overall"] == "pass", rr
        made[key] = sciv
    return {"loc": loc, "a_n": a_n, "a_w2": a_w2, "a_s": a_s, "inst": inst,
            "v_north": v_north["target"], "v_south": v_south["target"],
            "A": made["A"], "B": made["B"]}


def _res_rev(pdir: Path) -> str:
    return ResourceService(pdir, pdir.name).document_revision()


def test_t36_approve_without_pass_rejected(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)

    async def flow():
        ids = await _seed_two_state_views(svc, vs, pdir)
        # B 有 pass 证据；A 的 review 证据拿掉（用 B 直接测：先撤销证据不可行——
        # 用新生成未 review 的对象：重新 generate A（覆盖指针）后 review 记录不再匹配
        gen = await vs.generate_scene_instance_view(
            ids["A"], expected_revision=_sciv_rev(pdir, ids["A"]), candidate_count=1,
            agnes_client=FakeGen(), include_peer=False)
        assert gen["ok"]
        r = await vs.approve_scene_instance_view(
            ids["A"], candidate_index=0, expected_revision=gen["revision_after"],
            expected_resources_document_revision=_res_rev(pdir))
        assert not r["ok"] and r["error"] == "review_pass_required", r.get("error")
    asyncio.run(flow())


def test_t37_explicit_approve_success(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_two_state_views(svc, vs, pdir))
    rev = _sciv_rev(pdir, ids["A"])
    r = asyncio.run(vs.approve_scene_instance_view(
        ids["A"], candidate_index=0, expected_revision=rev,
        expected_resources_document_revision=_res_rev(pdir)))
    assert r["ok"], r.get("error")
    v = SceneSystemStore(pdir, pdir.name).load().scene_instance_views[ids["A"]]
    assert v.approval_status == "approved" and v.reference_asset_id == r["asset_id"]
    assert v.approved_basis_fingerprint, "stale 检测锚点已写入"
    a = ResourceStore(pdir, pdir.name).load().reference_assets[r["asset_id"]]
    assert a.purpose == "scene_instance_view" and a.source == "scene_system"
    recs = generation_records.read_records(pdir, path=approval_records_path(pdir))
    assert recs[-1]["status"] == "approved" and recs[-1]["asset_id"] == r["asset_id"]
    # 重复 approve 拒绝
    rev2 = _sciv_rev(pdir, ids["A"])
    r2 = asyncio.run(vs.approve_scene_instance_view(
        ids["A"], candidate_index=0, expected_revision=rev2,
        expected_resources_document_revision=_res_rev(pdir)))
    assert not r2["ok"] and r2["error"] == "already_approved"


def test_t38_saga_b_to_c_stale_orphan(tmp_path, monkeypatch):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_two_state_views(svc, vs, pdir))
    orig = ResourceWriteService.register_reference_asset

    async def spy(self_rws, *a, **kw):
        st = SceneSystemStore(pdir, pdir.name)
        async with st.write_lock():
            d = st.load()
            d.scene_instance_views[ids["A"]].label = "mutated mid-saga"
            st.save(d)
        return await orig(self_rws, *a, **kw)

    monkeypatch.setattr(ResourceWriteService, "register_reference_asset", spy)
    rev = _sciv_rev(pdir, ids["A"])
    r = asyncio.run(vs.approve_scene_instance_view(
        ids["A"], candidate_index=0, expected_revision=rev,
        expected_resources_document_revision=_res_rev(pdir)))
    assert not r["ok"] and r["error"] == "stale_approval_basis"
    orphan_id = r.get("orphaned_asset_id")
    assert orphan_id and orphan_id in ResourceStore(pdir, pdir.name).load().reference_assets
    v = SceneSystemStore(pdir, pdir.name).load().scene_instance_views[ids["A"]]
    assert v.approval_status == "unreviewed" and v.reference_asset_id is None
    recs = generation_records.read_records(pdir, path=approval_records_path(pdir))
    assert recs[-1]["status"] == "orphaned_reference_asset"


def test_t39_revoke(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_two_state_views(svc, vs, pdir))
    rev = _sciv_rev(pdir, ids["A"])
    r = asyncio.run(vs.approve_scene_instance_view(
        ids["A"], candidate_index=0, expected_revision=rev,
        expected_resources_document_revision=_res_rev(pdir)))
    assert r["ok"]
    n_review = len(ivr.read_instance_view_review_records(pdir, scene_instance_view_id=ids["A"]))
    rev2 = _sciv_rev(pdir, ids["A"])
    rr = asyncio.run(vs.revoke_scene_instance_view_approval(
        ids["A"], expected_revision=rev2, reason="t39"))
    assert rr["ok"] and rr["approval_status"] == "rejected"
    v = SceneSystemStore(pdir, pdir.name).load().scene_instance_views[ids["A"]]
    assert v.approval_status == "rejected"
    assert v.reference_asset_id == r["asset_id"], "历史保留"
    assert v.approved_basis_fingerprint, "历史锚点保留"
    assert r["asset_id"] in ResourceStore(pdir, pdir.name).load().reference_assets
    assert len(ivr.read_instance_view_review_records(
        pdir, scene_instance_view_id=ids["A"])) >= n_review
    recs = generation_records.read_records(pdir, path=approval_records_path(pdir))
    assert recs[-1]["status"] == "revoked"


# ============ T40/T41：derived stale read model ============
def test_t40_global_state_change_makes_approved_views_stale(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_two_state_views(svc, vs, pdir))
    # approve A 与 B
    for key in ("A", "B"):
        rev = _sciv_rev(pdir, ids[key])
        r = asyncio.run(vs.approve_scene_instance_view(
            ids[key], candidate_index=0, expected_revision=rev,
            expected_resources_document_revision=_res_rev(pdir)))
        assert r["ok"], (key, r.get("error"))
    # approve 后未变：read is_stale=False
    st_a = vs.read_scene_instance_view_status(ids["A"])
    assert st_a["is_stale"] is False and st_a["stale_reasons"] == []
    # 改全局 lighting（red emergency → normal white）——§37
    async def mut():
        rev = svc.read_scene_instance(ids["inst"])["revision"]
        r = await svc.patch_scene_state(ids["inst"], {"lighting": "normal white"},
                                        expected_revision=rev)
        assert r["ok"]
    asyncio.run(mut())
    st_a2 = vs.read_scene_instance_view_status(ids["A"])
    st_b2 = vs.read_scene_instance_view_status(ids["B"])
    assert st_a2["is_stale"] is True, st_a2["stale_reasons"]
    assert any(r.startswith("global_state_changed") for r in st_a2["stale_reasons"])
    assert st_b2["is_stale"] is True, "能看到全局 lighting 的 view 同样 stale（§37）"
    # 只 regen A → approve 后 A 不 stale，B 仍 stale（§37 lifecycle）
    gen = asyncio.run(vs.generate_scene_instance_view(
        ids["A"], expected_revision=_sciv_rev(pdir, ids["A"]), candidate_count=1,
        agnes_client=FakeGen(), include_peer=False))
    assert gen["ok"], gen.get("error")
    rr = asyncio.run(ivr.review_scene_instance_view_candidates(
        pdir, ids["A"], agnes_client=FakeVision(_pass_reply(ids["a_w2"], "prop_sword01"))))
    assert rr["ok"] and rr["candidate_reviews"][0]["overall"] == "pass"
    rev = _sciv_rev(pdir, ids["A"])
    ap = asyncio.run(vs.approve_scene_instance_view(
        ids["A"], candidate_index=0, expected_revision=rev,
        expected_resources_document_revision=_res_rev(pdir)))
    assert ap["ok"], ap.get("error")
    st_a3 = vs.read_scene_instance_view_status(ids["A"])
    st_b3 = vs.read_scene_instance_view_status(ids["B"])
    assert st_a3["is_stale"] is False, "regen+approve 后 A 复鲜"
    assert st_b3["is_stale"] is True, "B 仍 stale（不是一改状态偷偷覆盖所有图）"


def test_t41_invisible_state_change_no_stale(tmp_path):
    """§33/§38：A 看不到 south stairs → stairs 变化只 stale B，不 stale A。"""
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_two_state_views(svc, vs, pdir))
    for key in ("A", "B"):
        rev = _sciv_rev(pdir, ids[key])
        r = asyncio.run(vs.approve_scene_instance_view(
            ids[key], candidate_index=0, expected_revision=rev,
            expected_resources_document_revision=_res_rev(pdir)))
        assert r["ok"], (key, r.get("error"))
    # 不可见变化：south stairs（只有 B 可见）intact→damaged——整 dict 替换带全量
    async def mut():
        rev = svc.read_scene_instance(ids["inst"])["revision"]
        r = await svc.patch_scene_state(
            ids["inst"], {"anchor_states": {ids["a_w2"]: {"condition": "broken"},
                                            ids["a_s"]: {"condition": "damaged"}}},
            expected_revision=rev)
        assert r["ok"]
    asyncio.run(mut())
    st_a = vs.read_scene_instance_view_status(ids["A"])
    st_b = vs.read_scene_instance_view_status(ids["B"])
    assert st_a["is_stale"] is False, f"不可见变化不 stale A（§33）：{st_a['stale_reasons']}"
    assert st_b["is_stale"] is True and "anchor_state_changed" in st_b["stale_reasons"], \
        f"能看到该 anchor 的 B 必须 stale：{st_b['stale_reasons']}"
