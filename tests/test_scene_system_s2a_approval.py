# tests/test_scene_system_s2a_approval.py
# Scene System V1 — Phase S2A §19/§25 测试（T29-T34 + cross-canonical saga）。
#
# T26 generation never auto approves（在 generation 套件已覆盖）
# T29 approve without pass rejected（review pass 双向硬门槛的另一半）
# T30 pass does not auto approve（pass 只是证据，approve 必须显式 mutation）
# T31 explicit approval success（attach + approval_status=approved + 重读验证）
# T32 Resource ReferenceAsset registered only on approval（候选/ready 阶段 resources
#    字节不变；approve 才注册；且注册的正是该候选文件）
# T33 stale approval basis does not attach Resource asset（审查后语义变化 → 拒绝；
#    B/C 之间 view 变化 → orphan asset 记录、绝不 attach、不偷删）
# T34 revoke（解除 approved；历史不删：asset/records 全保留）
import asyncio
import base64
import json
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.models import Project, Scene
from src.resources.service import ResourceService
from src.resources.store import ResourceStore
from src.resources.write_service import ResourceWriteService
from src.scene_system.models import view_revision
from src.scene_system.store import SceneSystemStore
from src.scene_system.service import SceneSystemService
from src.scene_system.visual_service import SceneVisualService
from src.scene_system import view_review, generation_records
from src.scene_system.generation_records import approval_records_path

PNG_B64 = base64.b64encode(b"\x89PNG fake-image").decode()


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


def _pass_reply(anchor_id: str) -> str:
    return json.dumps({
        "location_identity": "pass", "location_notes": "",
        "anchors": [{"anchor_id": anchor_id, "visibility": "clearly_visible", "notes": ""}],
        "anchor_consistency": "pass", "anchor_notes": "",
        "layout_consistency": "pass", "layout_notes": "",
        "view_consistency": "pass", "view_notes": "",
        "visual_defects": "pass", "defect_notes": "",
        "project_style_consistency": "pass", "style_notes": "ok",
        "overall": "pass", "summary": "ok"}, ensure_ascii=False)


def make_pdir(tmp_path: Path) -> Path:
    name = tmp_path.name
    pdir = tmp_path / name
    proj = Project(name=name)
    proj.scenes = [Scene(scene_id="s01", summary="x")]
    ProjectStore(pdir).save(proj)
    # P1：生成路径 profile-aware（fail closed on unspecified）——测试环境默认写实
    from src.visual_profile import VisualProfileService
    VisualProfileService(pdir, pdir.name).update(render_family="photorealistic",
                                                 expected_revision="")
    return pdir


def _res_doc_rev(pdir: Path) -> str:
    return ResourceService(pdir, pdir.name).document_revision()


async def _seed_generate_review_pass(svc: SceneSystemService, pdir: Path):
    """seed → generate → review（全 pass 证据）。返回 ids + 生成结果。"""
    async def drev():
        return svc.document_overview()["document_revision"]

    loc = (await svc.create_location(name="灯塔监听室",
                                     identity_description="circular control room",
                                     expected_document_revision=await drev()))["target"]
    a1 = (await svc.create_anchor(location_id=loc, name="north door", kind="door",
                                  expected_document_revision=await drev()))["target"]
    va = await svc.create_view(location_id=loc, label="hero", camera_zone="south_west",
                               look_direction="north_east", visible_anchor_ids=[a1],
                               expected_document_revision=await drev())
    vid = va["target"]
    vs = SceneVisualService(pdir, pdir.name)
    gen = await vs.generate_location_view(vid, expected_revision=va["revision"],
                                          candidate_count=1, agnes_client=FakeGen())
    assert gen["ok"], gen.get("error")
    rv = await view_review.review_location_view_candidates(
        pdir, vid, agnes_client=FakeVision(_pass_reply(a1)))
    assert rv["ok"] and rv["candidate_reviews"][0]["overall"] == "pass", rv
    return {"loc": loc, "a1": a1, "vid": vid, "gen": gen, "vs": vs}


def test_t29_approve_without_pass_rejected(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)

    async def flow():
        async def drev():
            return svc.document_overview()["document_revision"]
        loc = (await svc.create_location(name="L", expected_document_revision=await drev()))["target"]
        va = await svc.create_view(location_id=loc, label="v",
                                   expected_document_revision=await drev())
        vid = va["target"]
        vs = SceneVisualService(pdir, pdir.name)
        gen = await vs.generate_location_view(vid, expected_revision=va["revision"],
                                              candidate_count=1, agnes_client=FakeGen())
        assert gen["ok"]
        # 无任何 review → 拒绝
        r = await vs.approve_location_view(vid, candidate_index=0,
                                           expected_revision=gen["revision_after"],
                                           expected_resources_document_revision=_res_doc_rev(pdir))
        assert not r["ok"] and r["error"] == "review_pass_required", r
        # review 结论非 pass → 同样拒绝
        bad = json.dumps({"location_identity": "fail", "anchors": [],
                          "anchor_consistency": "pass", "layout_consistency": "pass",
                          "view_consistency": "pass", "visual_defects": "pass",
                          "project_style_consistency": "pass", "style_notes": "ok",
                          "overall": "needs_fix", "summary": ""}, ensure_ascii=False)
        rv = await view_review.review_location_view_candidates(
            pdir, vid, agnes_client=FakeVision(bad))
        assert rv["ok"]
        r2 = await vs.approve_location_view(vid, candidate_index=0,
                                            expected_revision=gen["revision_after"],
                                            expected_resources_document_revision=_res_doc_rev(pdir))
        assert not r2["ok"] and r2["error"] == "review_pass_required", r2

    asyncio.run(flow())


def test_t30_pass_does_not_auto_approve(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_generate_review_pass(svc, pdir))
    v = SceneSystemStore(pdir, pdir.name).load().views[ids["vid"]]
    assert v.generation_status == "ready"
    assert v.approval_status == "unreviewed", "review pass ≠ 自动 approved（显式 mutation 才算）"
    assert v.reference_asset_id is None


def test_t31_t32_explicit_approval_success_and_resource_registration(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_generate_review_pass(svc, pdir))
    vid = ids["vid"]

    # T32 前半：候选 committed + review pass 之后、approve 之前，resources.json 无新资产
    res_doc = ResourceStore(pdir, pdir.name).load()
    scene_assets = [a for a in res_doc.reference_assets.values()
                    if a.source == "scene_system"]
    assert scene_assets == [], "approve 之前绝不注册 ReferenceAsset（§17）"

    rev = view_revision(SceneSystemStore(pdir, pdir.name).load().views[vid])
    r = asyncio.run(ids["vs"].approve_location_view(
        vid, candidate_index=0, expected_revision=rev,
        expected_resources_document_revision=_res_doc_rev(pdir)))
    assert r["ok"], r.get("error")
    assert r["approval_status"] == "approved"

    # view attach
    v = SceneSystemStore(pdir, pdir.name).load().views[vid]
    assert v.approval_status == "approved" and v.reference_asset_id == r["asset_id"]
    # T32 后半：ReferenceAsset 已注册，local 正是该候选文件，checksum 真实
    a = ResourceStore(pdir, pdir.name).load().reference_assets[r["asset_id"]]
    assert a.source == "scene_system" and a.local == r["candidate_file"]
    assert a.checksum, "register_reference_asset 校验并计算真实 sha256"
    assert (pdir / a.local).is_file()
    # approval record
    recs = generation_records.read_records(pdir, path=approval_records_path(pdir))
    assert recs and recs[-1]["status"] == "approved" and recs[-1]["asset_id"] == r["asset_id"]
    # 重复 approve 拒绝
    rev2 = view_revision(SceneSystemStore(pdir, pdir.name).load().views[vid])
    r2 = asyncio.run(ids["vs"].approve_location_view(
        vid, candidate_index=0, expected_revision=rev2,
        expected_resources_document_revision=_res_doc_rev(pdir)))
    assert not r2["ok"] and r2["error"] == "already_approved"


def test_t33a_stale_approval_basis_does_not_attach(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_generate_review_pass(svc, pdir))
    vid = ids["vid"]
    # review pass 之后改变 view 定义 → 审查期望指纹失效
    async def mutate():
        rev = svc.read_view(vid)["revision"]
        await svc.update_view(vid, {"look_direction": "south"}, expected_revision=rev)
    asyncio.run(mutate())
    rev = view_revision(SceneSystemStore(pdir, pdir.name).load().views[vid])
    r = asyncio.run(ids["vs"].approve_location_view(
        vid, candidate_index=0, expected_revision=rev,
        expected_resources_document_revision=_res_doc_rev(pdir)))
    assert not r["ok"] and r["error"] == "stale_approval_basis", r
    # Resources 零新增（阶段 A 就拒绝，B 未执行）
    res_doc = ResourceStore(pdir, pdir.name).load()
    assert [a for a in res_doc.reference_assets.values() if a.source == "scene_system"] == []
    v = SceneSystemStore(pdir, pdir.name).load().views[vid]
    assert v.approval_status == "unreviewed" and v.reference_asset_id is None


def test_t33b_orphan_reference_asset_recorded_never_attached(tmp_path, monkeypatch):
    """B/C 之间 view 变化：ReferenceAsset 已注册但未绑定 → orphan 记录，绝不 attach、
    不偷删（§19：不能为了避免 orphan 牺牲 canonical 正确性）。"""
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_generate_review_pass(svc, pdir))
    vid = ids["vid"]

    orig = ResourceWriteService.register_reference_asset

    async def spy(self_rws, *a, **kw):
        # 阶段 B 进行中：另一写者改了 scene view（B→C 竞态窗口）
        st = SceneSystemStore(pdir, pdir.name)
        async with st.write_lock():
            d = st.load()
            d.views[vid].label = "mutated during saga"
            st.save(d)
        return await orig(self_rws, *a, **kw)

    monkeypatch.setattr(ResourceWriteService, "register_reference_asset", spy)
    rev = view_revision(SceneSystemStore(pdir, pdir.name).load().views[vid])
    r = asyncio.run(ids["vs"].approve_location_view(
        vid, candidate_index=0, expected_revision=rev,
        expected_resources_document_revision=_res_doc_rev(pdir)))
    assert not r["ok"] and r["error"] == "stale_approval_basis", r
    orphan_id = r.get("orphaned_asset_id")
    assert orphan_id, "orphan asset id 必须暴露"
    # Resources：资产已注册（不偷删）
    res_doc = ResourceStore(pdir, pdir.name).load()
    assert orphan_id in res_doc.reference_assets
    # Scene：绝不 attach
    v = SceneSystemStore(pdir, pdir.name).load().views[vid]
    assert v.approval_status == "unreviewed" and v.reference_asset_id is None
    # approval record：orphaned_reference_asset
    recs = generation_records.read_records(pdir, path=approval_records_path(pdir))
    assert recs[-1]["status"] == "orphaned_reference_asset" \
        and recs[-1]["asset_id"] == orphan_id


def test_t34_revoke(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_generate_review_pass(svc, pdir))
    vid = ids["vid"]
    rev = view_revision(SceneSystemStore(pdir, pdir.name).load().views[vid])
    r = asyncio.run(ids["vs"].approve_location_view(
        vid, candidate_index=0, expected_revision=rev,
        expected_resources_document_revision=_res_doc_rev(pdir)))
    assert r["ok"], r.get("error")
    n_gen = len(generation_records.read_records(pdir))
    n_review = len(view_review.read_view_review_records(pdir, view_id=vid))
    # revoke
    rev2 = view_revision(SceneSystemStore(pdir, pdir.name).load().views[vid])
    rr = asyncio.run(ids["vs"].revoke_view_approval(vid, expected_revision=rev2,
                                                    reason="E2E revoke test"))
    assert rr["ok"] and rr["approval_status"] == "rejected", rr
    v = SceneSystemStore(pdir, pdir.name).load().views[vid]
    assert v.approval_status == "rejected"
    assert v.reference_asset_id == r["asset_id"], "历史 reference_asset_id 保留（不删历史）"
    # 历史全保留：ReferenceAsset / generation / review records / approval records
    assert r["asset_id"] in ResourceStore(pdir, pdir.name).load().reference_assets
    assert len(generation_records.read_records(pdir)) >= n_gen
    assert len(view_review.read_view_review_records(pdir, view_id=vid)) >= n_review
    recs = generation_records.read_records(pdir, path=approval_records_path(pdir))
    assert recs[-1]["status"] == "revoked"
    # 未 approved 状态再 revoke → 拒绝
    rev3 = view_revision(SceneSystemStore(pdir, pdir.name).load().views[vid])
    rr2 = asyncio.run(ids["vs"].revoke_view_approval(vid, expected_revision=rev3))
    assert not rr2["ok"] and rr2["error"] == "not_approved"
