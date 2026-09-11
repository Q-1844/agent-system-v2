# tests/test_scene_system_s2a_basis.py
# Scene System V1 — Phase S2A §9-§12 测试（T11-T14 + basis 指纹覆盖）。
#
# T11 第一张 View 可无 prior approved reference 生成（选择返回空 + notes）
# T12 后续 View 参考选择确定性（同状态两次选择结果一致；交集降序+id 升序）
# T13 参考预算 enforced（MAX_VIEW_REFERENCE_REFS=2；显式超预算拒绝）
# T14 prompt deterministic（同输入逐字节一致；含 view 方位/required anchors/无剧情约束）
# + location_visual_basis_fingerprint 覆盖 Location/Anchor/Layout/View 变化（§9）
import asyncio
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.models import Project, Scene
from src.scene_system.service import SceneSystemService
from src.scene_system.store import SceneSystemStore
from src.scene_system.visual_basis import (
    MAX_VIEW_REFERENCE_REFS, compute_location_visual_basis,
    recompute_location_visual_basis_still_holds, select_reference_views)
from src.scene_system.location_prompt import build_location_view_prompt
from src.resources.models import Prop as ResourceProp, ResourceDocument, ReferenceAsset
from src.resources.store import ResourceStore


def make_project(base: Path) -> Path:
    name = base.name
    pdir = base / name
    proj = Project(name=name)
    proj.scenes = [Scene(scene_id="s01", summary="x")]
    ProjectStore(pdir).save(proj)
    return pdir


@pytest.fixture
def pdir(tmp_path) -> Path:
    return make_project(tmp_path)


@pytest.fixture
def svc(pdir) -> SceneSystemService:
    return SceneSystemService(pdir, pdir.name)


async def _seed(svc: SceneSystemService, pdir: Path):
    async def drev():
        return svc.document_overview()["document_revision"]

    r = await svc.create_location(name="灯塔监听室",
                                  identity_description="circular control room, curved console bank",
                                  expected_document_revision=await drev())
    loc = r["target"]
    a = {}
    for nm, kind in (("north door", "door"), ("main console", "furniture"),
                     ("west window 01", "window"), ("south stairs", "stairs")):
        a[nm] = (await svc.create_anchor(location_id=loc, name=nm, kind=kind,
                                         expected_document_revision=await drev()))["target"]
    await svc.create_layout(location_id=loc, orientation_convention="north_up",
                            placements=[{"anchor_id": a["main console"], "zone": "center"}],
                            expected_document_revision=await drev())
    va = await svc.create_view(location_id=loc, label="SW to NE", camera_zone="south_west",
                               look_direction="north_east",
                               visible_anchor_ids=[a["north door"], a["main console"],
                                                   a["west window 01"]],
                               expected_document_revision=await drev())
    vb = await svc.create_view(location_id=loc, label="N to S", camera_zone="north_entrance",
                               look_direction="south",
                               visible_anchor_ids=[a["north door"], a["south stairs"]],
                               expected_document_revision=await drev())
    return {"loc": loc, "a": a, "va": va["target"], "vb": vb["target"]}


def _load(pdir):
    doc = SceneSystemStore(pdir, pdir.name).load()
    rdoc = ResourceStore(pdir, pdir.name).load()
    return doc, rdoc


def _fake_approved_resource(pdir: Path, asset_id: str, local_rel: str) -> None:
    """给 resources.json 放一个带 local 文件的 ReferenceAsset + 视觉文件本体。"""
    f = pdir / local_rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-bytes")
    doc = ResourceStore(pdir, pdir.name).load()
    doc.reference_assets[asset_id] = ReferenceAsset(
        asset_id=asset_id, local=local_rel, url=None, checksum="x" * 8,
        view="hero", purpose="location_view", source="scene_system")
    ResourceStore(pdir, pdir.name).save(doc)


def _approve_view(svc: SceneSystemService, pdir: Path, view_id: str, asset_id: str):
    """测试捷径：直接把 view 置为 approved（绕过生成/审批协议——协议测试在后续
    套件；本文件只测选择/指纹的确定性）。"""
    async def flow():
        # 用底层 store 直改（不走 update_view——视觉状态字段协议管理）
        st = SceneSystemStore(pdir, pdir.name)
        async with st.write_lock():
            doc = st.load()
            v = doc.views[view_id]
            v.approval_status = "approved"
            v.reference_asset_id = asset_id
            st.save(doc)
    asyncio.run(flow())


# ============ T11：第一张 View 无 prior approved ============
def test_t11_first_view_generates_without_prior_approved(svc, pdir):
    ids = asyncio.run(_seed(svc, pdir))
    doc, rdoc = _load(pdir)
    sel = select_reference_views(pdir, doc, rdoc, doc.views[ids["va"]])
    assert sel["selected"] == [] and sel["reference_view_ids"] == []
    assert "no_approved_reference_available" in sel["notes"]
    b = compute_location_visual_basis(pdir, doc, rdoc, doc.locations[ids["loc"]],
                                      doc.views[ids["va"]], model_id="m", size_tier="1K",
                                      ratio="16:9", seed=None, candidate_count=2,
                                      reference_selection=sel)
    assert b["selected_references"] == [] and b["basis_fingerprint"]
    ok, why = recompute_location_visual_basis_still_holds(pdir, doc, rdoc,
                                                          doc.views[ids["va"]], b)
    assert ok, why


# ============ T12：后续 View 参考选择确定性 ============
def test_t12_reference_selection_deterministic(svc, pdir):
    ids = asyncio.run(_seed(svc, pdir))
    _fake_approved_resource(pdir, "ref_view_a_000001",
                            f"output/scene_system/locations/{ids['loc']}/views/{ids['va']}/generations/gen_a/candidate_01.png")
    _approve_view(svc, pdir, ids["va"], "ref_view_a_000001")
    doc, rdoc = _load(pdir)
    # auto：与 vb 的可见交集（north door）→ va 入选
    s1 = select_reference_views(pdir, doc, rdoc, doc.views[ids["vb"]])
    s2 = select_reference_views(pdir, doc, rdoc, doc.views[ids["vb"]])
    assert s1["reference_view_ids"] == [ids["va"]] == s2["reference_view_ids"], (s1, s2)
    assert s1["input_budget"]["used"] == 1
    # 第二张规则：vb 的 required anchors（north door, south stairs）中
    # va 只覆盖 north door → 有 uncovered；但只有一张候选 → 不补第二张
    assert "second_reference_added_for_uncovered_anchors" not in s1["notes"]
    # 显式模式：指定 va → 同样结果
    s3 = select_reference_views(pdir, doc, rdoc, doc.views[ids["vb"]],
                                explicit_reference_view_ids=[ids["va"]])
    assert s3["mode"] == "explicit" and s3["reference_view_ids"] == [ids["va"]]
    # 显式指定未 approved 的 view → 拒绝
    s4 = select_reference_views(pdir, doc, rdoc, doc.views[ids["vb"]],
                                explicit_reference_view_ids=[ids["vb"]])
    assert s4.get("error") == "reference_view_not_approved"


# ============ T13：参考预算 enforced ============
def test_t13_reference_budget_enforced(svc, pdir):
    ids = asyncio.run(_seed(svc, pdir))
    assert MAX_VIEW_REFERENCE_REFS == 2
    # 造 3 个 approved views，显式要 3 张 → 超预算拒绝
    refs = []
    for i, vid in enumerate((ids["va"], ids["vb"])):
        asset_id = f"ref_view_x{i:06d}"
        _fake_approved_resource(pdir, asset_id,
                                f"output/scene_system/locations/{ids['loc']}/views/{vid}/generations/gen_x{i}/candidate_01.png")
        _approve_view(svc, pdir, vid, asset_id)
        refs.append((vid, asset_id))
    # 第三个 view 也 approve
    async def third():
        async def drev():
            return svc.document_overview()["document_revision"]
        vc = await svc.create_view(location_id=ids["loc"], label="third",
                                   visible_anchor_ids=[ids["a"]["main console"]],
                                   expected_document_revision=await drev())
        asset_id = "ref_view_x2"
        _fake_approved_resource(pdir, asset_id,
                                f"output/scene_system/locations/{ids['loc']}/views/{vc['target']}/generations/gen_x2/candidate_01.png")
        st = SceneSystemStore(pdir, pdir.name)
        async with st.write_lock():
            d = st.load()
            d.views[vc["target"]].approval_status = "approved"
            d.views[vc["target"]].reference_asset_id = asset_id
            st.save(d)
        return vc["target"]
    vc_id = asyncio.run(third())
    doc, rdoc = _load(pdir)
    over = select_reference_views(pdir, doc, rdoc, doc.views[ids["vb"]],
                                  explicit_reference_view_ids=[ids["va"], ids["vb"], vc_id])
    assert over.get("error") == "reference_budget_exceeded"
    # auto 模式永不超预算
    auto = select_reference_views(pdir, doc, rdoc, doc.views[ids["vb"]])
    assert len(auto["selected"]) <= 2


# ============ T14：prompt deterministic ============
def test_t14_prompt_deterministic_and_content(svc, pdir):
    ids = asyncio.run(_seed(svc, pdir))
    doc, _ = _load(pdir)
    loc_obj = doc.locations[ids["loc"]]
    view_obj = doc.views[ids["va"]]
    anchors = sorted((a for a in doc.anchors.values() if a.location_id == ids["loc"]),
                     key=lambda a: a.anchor_id)
    layout = next(iter(doc.layouts.values()))
    p1 = build_location_view_prompt(loc_obj, anchors, layout, view_obj, has_references=False)
    p2 = build_location_view_prompt(loc_obj, anchors, layout, view_obj, has_references=False)
    assert p1 == p2, "同输入逐字节一致"
    # 内容关键点：方位 + required anchors + 无剧情中性约束
    assert "south_west" in p1 and "north_east" in p1
    assert "north door" in p1 and "main console" in p1 and "west window 01" in p1
    assert "no people" in p1 and "no weather effects" in p1 and "no damage" in p1
    assert "layout hint" in p1.lower() or "semantic orientation only" in p1
    # has_references=True 文本差异（确定性两态）
    p3 = build_location_view_prompt(loc_obj, anchors, layout, view_obj, has_references=True)
    assert p3 != p1 and "approved reference images" in p3


# ============ §9：指纹覆盖四类变化 ============
def test_basis_fingerprint_covers_all_semantic_changes(svc, pdir):
    ids = asyncio.run(_seed(svc, pdir))
    doc, rdoc = _load(pdir)
    va = doc.views[ids["va"]]

    async def mutate(mutator):
        await mutator()

    def basis_now():
        d, r = _load(pdir)
        return compute_location_visual_basis(
            pdir, d, r, d.locations[ids["loc"]], d.views[ids["va"]],
            model_id="m", size_tier="1K", ratio="16:9", seed=42, candidate_count=2,
            reference_selection=select_reference_views(pdir, d, r, d.views[ids["va"]]))

    b0 = basis_now()

    async def mut_anchor():
        rev = svc.read_anchor(ids["a"]["main console"])["revision"]
        await svc.update_anchor(ids["a"]["main console"], {"description": "brass and dials"},
                                expected_revision=rev)
    asyncio.run(mut_anchor())
    # anchor 内容变化 → 旧 basis 不再成立
    d1, r1 = _load(pdir)
    ok1, why1 = recompute_location_visual_basis_still_holds(pdir, d1, r1, d1.views[ids["va"]], b0)
    assert not ok1 and why1 == "basis_changed"

    async def mut_view():
        rev = svc.read_view(ids["va"])["revision"]
        await svc.update_view(ids["va"], {"look_direction": "north"},
                              expected_revision=rev)
    asyncio.run(mut_view())
    b1 = basis_now()
    d2, r2 = _load(pdir)
    ok2, why2 = recompute_location_visual_basis_still_holds(pdir, d2, r2, d2.views[ids["va"]], b1)
    assert ok2, why2   # b1 是对当前状态的 basis → holds

    async def mut_layout():
        lay = next(iter(SceneSystemStore(pdir, pdir.name).load().layouts.values()))
        await svc.update_layout(lay.layout_id, {"notes": "updated"},
                                expected_revision=svc.read_layout(lay.layout_id)["revision"])
    asyncio.run(mut_layout())
    d3, r3 = _load(pdir)
    ok3, why3 = recompute_location_visual_basis_still_holds(pdir, d3, r3, d3.views[ids["va"]], b1)
    assert not ok3 and why3 == "basis_changed"

    async def mut_location():
        rev = svc.read_location(ids["loc"])["revision"]
        await svc.update_location(ids["loc"], {"style_notes": "muted coastal palette"},
                                  expected_revision=rev)
    asyncio.run(mut_location())
    d4, r4 = _load(pdir)
    ok4, why4 = recompute_location_visual_basis_still_holds(pdir, d4, r4, d4.views[ids["va"]], b1)
    assert not ok4 and why4 == "basis_changed"
