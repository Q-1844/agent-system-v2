# tests/test_scene_system_s2b_basis.py
# Scene System V1 — Phase S2B：prompt / 参考选择 / GenerationBasis / §14 stale 规则
# （§39 T15-T18 + §14 专项 basis 层验证；T16-T27 的生成链路部分在 generation 套件）。
#
# T15 prompt deterministic + 六 SECTION 结构
# T16 base Location reference mandatory（无母版绝不文生图）
# T17 peer state ref selection deterministic
# T18 visual ref budget（MAX_TOTAL_VISUAL_REFS=4；visible props > 2 → omitted 诚实记录）
# §14：可见 anchor/prop/全局态变化 → basis_changed；不可见变化 → 不 stale；
#      prop 语义变化 → stale；updated_at-only 触碰 → 不 stale
import asyncio
from pathlib import Path

import pytest

from src.core.store import ProjectStore
from src.core.models import Project, Scene
from src.resources.models import Prop as ResourceProp, ResourceDocument, ReferenceAsset
from src.resources.store import ResourceStore
from src.scene_system.service import SceneSystemService
from src.scene_system.store import SceneSystemStore
from src.scene_system.visual_service import SceneVisualService
from src.visual_profile import load_profile_or_none
from src.scene_system.instance_view_basis import (
    MAX_TOTAL_VISUAL_REFS, _BasisInputError, compute_scene_instance_visual_basis,
    recompute_scene_instance_visual_basis_still_holds, select_state_view_visual_refs)
from src.scene_system.instance_view_prompt import build_scene_instance_view_prompt


def make_pdir(tmp_path: Path) -> Path:
    name = tmp_path.name
    pdir = tmp_path / name
    proj = Project(name=name)
    proj.scenes = [Scene(scene_id="s09", summary="x")]
    ProjectStore(pdir).save(proj)
    rdoc = ResourceDocument()
    for pid in ("prop_sword01", "prop_compass02", "prop_lamp03", "prop_hidden01"):
        rdoc.props[pid] = ResourceProp(prop_id=pid, name=pid)
    ResourceStore(pdir, pdir.name).save(rdoc)
    # P1：生成路径 profile-aware（fail closed on unspecified）——测试环境默认写实
    from src.visual_profile import VisualProfileService
    VisualProfileService(pdir, pdir.name).update(render_family="photorealistic",
                                                 expected_revision="")
    return pdir


def add_asset(pdir: Path, asset_id: str, local_rel: str):
    f = pdir / local_rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\x89PNG fake-bytes-" + asset_id.encode())
    rdoc = ResourceStore(pdir, pdir.name).load()
    rdoc.reference_assets[asset_id] = ReferenceAsset(
        asset_id=asset_id, local=local_rel, checksum="c" * 8, source="scene_system")
    ResourceStore(pdir, pdir.name).save(rdoc)


def approve_prop_ref(pdir: Path, prop_id: str, asset_id: str, cover: bool = False):
    rdoc = ResourceStore(pdir, pdir.name).load()
    rdoc.props[prop_id].approved_reference_asset_ids.append(asset_id)
    if cover:
        rdoc.props[prop_id].cover_asset_id = asset_id
    ResourceStore(pdir, pdir.name).save(rdoc)


async def _seed_full(svc: SceneSystemService, pdir: Path, *, n_visible_props: int = 1):
    """location + anchors + base approved view + instance（状态：night/storm/broken w2/sword）。"""
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
    v_north = await svc.create_view(location_id=loc, label="North to South",
                                    visible_anchor_ids=[a_n, a_w1, a_w2],
                                    expected_document_revision=await drev())
    v_south = await svc.create_view(location_id=loc, label="South to North",
                                    visible_anchor_ids=[a_s],
                                    expected_document_revision=await drev())
    inst = (await svc.create_scene_instance(story_scene_id="s09", location_id=loc,
                                            expected_document_revision=await drev()))["target"]
    props = {}
    for i in range(n_visible_props):
        pid = f"prop_sword01" if i == 0 else (f"prop_compass02" if i == 1 else f"prop_lamp03")
        props[pid] = a_w1
    pp = {pid: {"anchor_id": aid, "state": "intact"} for pid, aid in props.items()}
    rp = await svc.patch_scene_state(
        inst, {"time_of_day": "night", "weather": "storm", "lighting": "red emergency",
               "anchor_states": {a_w2: {"condition": "broken"},
                                 a_s: {"condition": "damaged"}},
               "prop_placements": pp},
        expected_revision=svc.read_scene_instance(inst)["revision"])
    assert rp["ok"], rp
    return {"loc": loc, "a_n": a_n, "a_w1": a_w1, "a_w2": a_w2, "a_s": a_s,
            "v_north": v_north["target"], "v_south": v_south["target"], "inst": inst,
            "visible_props": list(props)}


def _approve_base_view(svc: SceneSystemService, pdir: Path, view_id: str, asset_id: str):
    """测试捷径：经 approval 协议造 approved base（含 approval record 供 §2.B 校验）。"""
    async def flow():
        st = SceneSystemStore(pdir, pdir.name)
        async with st.write_lock():
            d = st.load()
            d.views[view_id].approval_status = "approved"
            d.views[view_id].reference_asset_id = asset_id
            st.save(d)
        # 补 approval record（§2.B base 审批证据检查需要）
        from src.scene_system.generation_records import approval_records_path
        from src.scene_system import generation_records
        from test_scene_system_s2b_generation import approval_record_fingerprint
        doc = st.load()
        fp = approval_record_fingerprint(pdir, doc, doc.views[view_id], [asset_id])
        generation_records.append_record(pdir, {
            "approval_id": "apv_test", "operation": "scene.approve_location_view",
            "status": "approved", "view_id": view_id, "asset_id": asset_id,
            "reviewed_basis_fingerprint": fp, "completed_at": 0.0,
        }, path=approval_records_path(pdir))
    asyncio.run(flow())


def _sciv_rev(pdir: Path, sciv_id: str) -> str:
    from src.scene_system.models import view_revision
    return view_revision(SceneSystemStore(pdir, pdir.name).load()
                         .scene_instance_views[sciv_id])


async def _simulate_sciv_approval(pdir: Path, sciv_id: str):
    """S2B.1 口径的批准捷径：no-peer 指纹锚点 + 真实可读资产（peer 资格检查用）。"""
    from src.scene_system.instance_view_basis import compute_no_peer_basis
    from src.resources.store import ResourceStore as RS
    from src.scene_system import generation_records
    st = SceneSystemStore(pdir, pdir.name)
    doc = st.load()
    sciv = doc.scene_instance_views[sciv_id]
    inst = doc.scene_instances[sciv.scene_instance_id]
    loc = doc.locations[inst.location_id]
    bv = doc.views[sciv.location_view_id]
    gens = [r for r in generation_records.read_records(pdir, generation_id=sciv.active_generation_id)
            if r.get("operation") == "scene_instance_view.generate" and r.get("status") == "committed"]
    gb = gens[-1].get("basis") or {}
    from src.visual_profile import load_profile_or_none as _lpon
    fp = compute_no_peer_basis(
        pdir, doc, RS(pdir, pdir.name).load(), inst, loc, bv, sciv,
        model_id=(gb.get("model") or {}).get("model_id", ""),
        size_tier=(gb.get("model") or {}).get("size_tier", "1K"),
        ratio=(gb.get("model") or {}).get("ratio", "16:9"),
        seed=gb.get("seed"), candidate_count=gb.get("candidate_count", 2),
        visual_profile=_lpon(pdir))["basis_fingerprint"]
    asset_id = f"ref_sim_{sciv_id[-8:]}"
    local = f"output/scene_system/scene_instances/sim/{sciv_id}/c_01.png"
    f = pdir / local
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\x89PNG sim-" + sciv_id.encode())
    rdoc = RS(pdir, pdir.name).load()
    rdoc.reference_assets[asset_id] = ReferenceAsset(asset_id=asset_id, local=local,
                                                     checksum="c" * 8, source="scene_system")
    RS(pdir, pdir.name).save(rdoc)
    async with st.write_lock():
        d = st.load()
        v = d.scene_instance_views[sciv_id]
        v.approval_status = "approved"
        v.approved_basis_fingerprint = fp
        v.reference_asset_id = asset_id
        st.save(d)
    return fp


def _load(pdir: Path):
    st = SceneSystemStore(pdir, pdir.name)
    doc = st.load()
    rdoc = ResourceStore(pdir, pdir.name).load()
    return doc, rdoc, st


# ============ T15：prompt deterministic + 六 SECTION ============
def test_t15_prompt_deterministic_and_sections(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_full(svc, pdir))
    doc, rdoc, _ = _load(pdir)
    loc = doc.locations[ids["loc"]]
    bv = doc.views[ids["v_north"]]
    inst = doc.scene_instances[ids["inst"]]
    anchors = [a for a in doc.anchors.values() if a.location_id == ids["loc"]]
    from src.scene_system.state_projection import project_scene_state_for_view
    proj = project_scene_state_for_view(inst, bv, anchors)
    prop_sem = [{"prop_id": "prop_sword01", "name": "Wooden Sword", "description": "",
                 "state": "intact", "anchor_id": ids["a_w1"], "anchor_name": "west window 01",
                 "visual_reference_missing": True}]
    p1 = build_scene_instance_view_prompt(loc, bv, anchors, proj, prop_sem)
    p2 = build_scene_instance_view_prompt(loc, bv, anchors, proj, prop_sem)
    assert p1 == p2, "同输入逐字节一致（T15）"
    low = p1.lower()
    # 六 SECTION 关键内容
    assert "preserve the approved" in low and "do not change the architecture" in low
    assert "camera:" in low and "north" in low
    assert "override the base reference" in low, "SECTION 3 显式覆盖指令（E2E 实证）"
    assert "night, storm, red emergency" in low
    assert "WEST WINDOW 02 is BROKEN" in p1   # 正确 anchor 被点名（§19）
    assert "WEST WINDOW 01" not in p1.split("Visible anchor state changes")[1].split("Anchor states")[0], \
        "window_01 不被误报为 broken"
    assert "wooden sword" in low and "no people" in low
    # 不可见 anchor（south stairs damaged）不进 prompt（投影已过滤）
    assert "damaged" not in low or "south stairs" not in low


# ============ T16：base reference mandatory ============
def test_t16_base_location_ref_mandatory(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_full(svc, pdir))
    doc, rdoc, _ = _load(pdir)
    bv = doc.views[ids["v_north"]]
    inst = doc.scene_instances[ids["inst"]]
    # base 无 approved reference_asset_id → fail closed
    with pytest.raises(_BasisInputError) as ei:
        select_state_view_visual_refs(pdir, doc, rdoc, bv, None, inst)
    assert ei.value.code in ("base_reference_missing", "location_view_not_approved")
    # base 有 ref 但资产不可读 → base_reference_missing
    from dataclasses import replace
    bv_missing = replace(bv, reference_asset_id="ref_missing01", approval_status="approved")
    with pytest.raises(_BasisInputError) as ei2:
        select_state_view_visual_refs(pdir, doc, rdoc, bv_missing, None, inst)
    assert ei2.value.code == "base_reference_missing"


# ============ T17：peer selection deterministic ============
def test_t17_peer_selection_deterministic(tmp_path):
    """S2B.1 起 peer 资格 = approved + committed generation + no-peer 指纹当前
    （stale/未生成 peer 不进 generation）——peers 走真实 generate 链。"""
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_full(svc, pdir))
    add_asset(pdir, "ref_base0000001",
              "output/scene_system/locations/x/views/base/generations/g/candidate_01.png")
    _approve_base_view(svc, pdir, ids["v_north"], "ref_base0000001")

    class _Gen:
        models = {"image": "fake-image-model"}

        async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                                 ratio="16:9", seed=None, want_b64=False):
            return {"url": None, "b64": "f2b1fake"}

    # 第三个 LocationView（peer B 的 base——pair 唯一性要求 peers 各用不同 view）
    async def third_view():
        drev = svc.document_overview()["document_revision"]
        r = await svc.create_view(location_id=ids["loc"], label="East view",
                                  visible_anchor_ids=[ids["a_w1"]],
                                  expected_document_revision=drev)
        return r["target"]
    v_east = asyncio.run(third_view())
    # peer 生成的 base 门槛：v_south / v_east 也需 approved（捷径 + approval records）
    for vid, ref in ((ids["v_south"], "ref_baseS0000001"), (v_east, "ref_baseE0000001")):
        add_asset(pdir, ref,
                  f"output/scene_system/locations/x/views/{vid}/g/candidate_01.png")
        _approve_base_view(svc, pdir, vid, ref)

    async def make_peers():
        made = []
        for vid in (ids["v_south"], v_east):
            drev = svc.document_overview()["document_revision"]
            r = await vs.create_scene_instance_view(
                scene_instance_id=ids["inst"], location_view_id=vid,
                label="peer", expected_document_revision=drev)
            assert r["ok"], r
            gen = await vs.generate_scene_instance_view(
                r["target"], expected_revision=_sciv_rev(pdir, r["target"]),
                candidate_count=1, agnes_client=_Gen(), include_peer=False)
            assert gen["ok"], gen.get("error")
            await _simulate_sciv_approval(pdir, r["target"])
            made.append(r["target"])
        return made
    peers = asyncio.run(make_peers())
    doc, rdoc, _ = _load(pdir)
    # 目标 = peers[1]（以它为"自身"，第一个 peer 成为候选——验证自身排除 + 确定性排序）
    bv = doc.views[ids["v_north"]]
    inst = doc.scene_instances[ids["inst"]]
    target_like = doc.scene_instance_views[peers[1]]
    sel1 = select_state_view_visual_refs(pdir, doc, rdoc, bv, target_like, inst)
    sel2 = select_state_view_visual_refs(pdir, doc, rdoc, bv, target_like, inst)
    assert sel1["peer_state_reference"] == sel2["peer_state_reference"], "确定性"
    assert sel1["peer_state_reference"]["scene_instance_view_id"] == peers[0], \
        "peer = sciv id 升序第一个可用项（自身排除）"
    assert sel1["selected"][0]["role"] == "base_location_view"
    assert sel1["input_budget"]["used"] == 2   # base + peer（本 seed 无 prop ref）


# ============ T18：budget ============
def test_t18_visual_ref_budget(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_full(svc, pdir, n_visible_props=3))
    add_asset(pdir, "ref_base0000001",
              "output/scene_system/locations/x/views/base/generations/g/candidate_01.png")
    for i, pid in enumerate(ids["visible_props"]):
        aid = f"ref_prop{i:07d}"
        add_asset(pdir, aid, f"output/scene_system/props/{pid}/g/candidate_01.png")
        approve_prop_ref(pdir, pid, aid)
    _approve_base_view(svc, pdir, ids["v_north"], "ref_base0000001")
    doc, rdoc, _ = _load(pdir)
    bv = doc.views[ids["v_north"]]
    inst = doc.scene_instances[ids["inst"]]
    async def mk():
        drev = svc.document_overview()["document_revision"]
        r = await vs.create_scene_instance_view(scene_instance_id=ids["inst"],
                                                location_view_id=ids["v_north"],
                                                expected_document_revision=drev)
        return r["target"]
    sciv_id = asyncio.run(mk())
    doc, rdoc, _ = _load(pdir)
    sciv = doc.scene_instance_views[sciv_id]
    sel = select_state_view_visual_refs(pdir, doc, rdoc, bv, sciv, inst)
    assert MAX_TOTAL_VISUAL_REFS == 4
    assert len(sel["selected"]) == 3, "1 base + 0 peer + 2 props = 3（4 是硬上限非目标）"
    assert len(sel["prop_refs_selected"]) == 2
    assert len(sel["prop_refs_omitted_due_to_budget"]) == 1, "第三个 prop 诚实记录 omitted"
    assert sel["prop_refs_omitted_due_to_budget"][0]["had_approved_reference"] is True
    # 确定性
    sel2 = select_state_view_visual_refs(pdir, doc, rdoc, bv, sciv, inst)
    assert [e["asset_id"] for e in sel["selected"]] == [e["asset_id"] for e in sel2["selected"]]


def test_t18b_prop_without_approved_ref_is_text_only(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_full(svc, pdir, n_visible_props=1))
    add_asset(pdir, "ref_base0000001",
              "output/scene_system/locations/x/views/base/generations/g/candidate_01.png")
    _approve_base_view(svc, pdir, ids["v_north"], "ref_base0000001")
    doc, rdoc, _ = _load(pdir)
    bv = doc.views[ids["v_north"]]
    inst = doc.scene_instances[ids["inst"]]
    # 可见 prop 无 approved ref → text-only + visual_reference_missing，不全局失败（§10.A）
    sel = select_state_view_visual_refs(pdir, doc, rdoc, bv, None, inst)
    assert len(sel["selected"]) == 1
    assert sel["prop_refs_selected"] == []
    assert sel["props_text_only"] and sel["props_text_only"][0]["visual_reference_missing"] is True


def test_t18c_missing_visible_prop_fail_closed(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    ids = asyncio.run(_seed_full(svc, pdir, n_visible_props=1))
    add_asset(pdir, "ref_base0000001",
              "output/scene_system/locations/x/views/base/generations/g/candidate_01.png")
    _approve_base_view(svc, pdir, ids["v_north"], "ref_base0000001")
    doc, rdoc, _ = _load(pdir)
    # 从 Resources 删除可见 prop → §9 fail closed
    rdoc2 = ResourceStore(pdir, pdir.name).load()
    del rdoc2.props["prop_sword01"]
    ResourceStore(pdir, pdir.name).save(rdoc2)
    bv = doc.views[ids["v_north"]]
    inst = doc.scene_instances[ids["inst"]]
    with pytest.raises(_BasisInputError) as ei:
        select_state_view_visual_refs(pdir, doc, rdoc2, bv, None, inst)
    assert ei.value.code == "prop_missing"


# ============ §14：stale 规则（basis 层） ============
def test_s14_stale_rules_matrix(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = asyncio.run(_seed_full(svc, pdir, n_visible_props=1))
    add_asset(pdir, "ref_base0000001",
              "output/scene_system/locations/x/views/base/generations/g/candidate_01.png")
    add_asset(pdir, "ref_prop0000001", "output/scene_system/props/sword/g/candidate_01.png")
    approve_prop_ref(pdir, "prop_sword01", "ref_prop0000001")
    _approve_base_view(svc, pdir, ids["v_north"], "ref_base0000001")

    async def mk():
        drev = svc.document_overview()["document_revision"]
        r = await vs.create_scene_instance_view(scene_instance_id=ids["inst"],
                                                location_view_id=ids["v_north"],
                                                expected_document_revision=drev)
        return r["target"]
    sciv_id = asyncio.run(mk())

    def basis_now():
        doc, rdoc, _ = _load(pdir)
        return (doc, rdoc, compute_scene_instance_visual_basis(
            pdir, doc, rdoc, doc.scene_instances[ids["inst"]],
            doc.locations[ids["loc"]], doc.views[ids["v_north"]],
            doc.scene_instance_views[sciv_id],
            model_id="m", size_tier="1K", ratio="16:9", seed=42, candidate_count=2,
            visual_profile=load_profile_or_none(pdir),
            ref_selection=select_state_view_visual_refs(
                pdir, doc, rdoc, doc.views[ids["v_north"]],
                doc.scene_instance_views[sciv_id], doc.scene_instances[ids["inst"]])))

    def holds(b):
        doc, rdoc, _ = _load(pdir)
        return recompute_scene_instance_visual_basis_still_holds(
            pdir, doc, rdoc, doc.scene_instances[ids["inst"]],
            doc.locations[ids["loc"]], doc.views[ids["v_north"]],
            doc.scene_instance_views[sciv_id], b)

    doc0, rdoc0, b0 = basis_now()
    ok, why = holds(b0)
    assert ok, why

    # 可见 anchor state 变化 → stale（T24 的 basis 层）
    async def mut_visible():
        rev = svc.read_scene_instance(ids["inst"])["revision"]
        # 🔴 anchor_states 是整 dict 替换：mutation 必须带全量（保留其它可见状态）
        await svc.patch_scene_state(ids["inst"],
                                    {"anchor_states": {ids["a_w2"]: {"condition": "shattered"},
                                                       ids["a_s"]: {"condition": "damaged"}}},
                                    expected_revision=rev)
    asyncio.run(mut_visible())
    ok, why = holds(b0)
    assert not ok and why == "basis_changed"

    # 不可见 anchor state 变化 → 不 stale（T25/§33 核心）
    doc1, rdoc1, b1 = basis_now()
    async def mut_invisible():
        rev = svc.read_scene_instance(ids["inst"])["revision"]
        await svc.patch_scene_state(ids["inst"],
                                    {"anchor_states": {ids["a_w2"]: {"condition": "shattered"},
                                                       ids["a_s"]: {"condition": "worse"}}},
                                    expected_revision=rev)
    asyncio.run(mut_invisible())
    ok, why = holds(b1)
    assert ok, f"不可见 anchor 变化绝不 stale 朝北 view（§33）：{why}"

    # 全局态变化 → stale（T23 的 basis 层）
    async def mut_global():
        rev = svc.read_scene_instance(ids["inst"])["revision"]
        await svc.patch_scene_state(ids["inst"], {"lighting": "normal white"},
                                    expected_revision=rev)
    asyncio.run(mut_global())
    doc2, rdoc2, b2 = basis_now()
    ok2, _ = holds(b2)
    assert ok2
    async def mut_global2():
        rev = svc.read_scene_instance(ids["inst"])["revision"]
        await svc.patch_scene_state(ids["inst"], {"weather": "clear"},
                                    expected_revision=rev)
    asyncio.run(mut_global2())
    ok, why = holds(b2)
    assert not ok and why == "basis_changed"

    # 不可见 prop placement 变化 → 不 stale
    doc3, rdoc3, b3 = basis_now()
    async def mut_invisible_prop():
        rev = svc.read_scene_instance(ids["inst"])["revision"]
        await svc.patch_scene_state(ids["inst"],
                                    {"prop_placements": {"prop_sword01": {"anchor_id": ids["a_w1"], "state": "intact"},
                                                         "prop_hidden01": {"anchor_id": ids["a_s"], "state": "moved"}}},
                                    expected_revision=rev)
    asyncio.run(mut_invisible_prop())
    ok, why = holds(b3)
    assert ok, f"不可见 prop 变化不 stale：{why}"

    # 可见 prop placement 变化 → stale（T26 的 basis 层）
    async def mut_visible_prop():
        rev = svc.read_scene_instance(ids["inst"])["revision"]
        await svc.patch_scene_state(ids["inst"],
                                    {"prop_placements": {"prop_sword01": {"anchor_id": ids["a_w1"], "state": "broken"},
                                                         "prop_hidden01": {"anchor_id": ids["a_s"], "state": "moved"}}},
                                    expected_revision=rev)
    asyncio.run(mut_visible_prop())
    ok, why = holds(b3)
    assert not ok and why == "basis_changed"

    # prop 语义（name）变化 → stale；updated_at-only 触碰 → 不 stale
    doc4, rdoc4, b4 = basis_now()
    rdoc5 = ResourceStore(pdir, pdir.name).load()
    rdoc5.props["prop_sword01"].name = "Old Wooden Sword"
    ResourceStore(pdir, pdir.name).save(rdoc5)
    ok, why = holds(b4)
    assert not ok and why == "basis_changed"
    doc6, rdoc6, b6 = basis_now()
    rdoc7 = ResourceStore(pdir, pdir.name).load()
    rdoc7.props["prop_hidden01"].name = "renamed invisible prop"
    ResourceStore(pdir, pdir.name).save(rdoc7)
    ok, why = holds(b6)
    assert ok, f"不可见 prop 语义变化不 stale：{why}"

    # base view 定义变化 → stale；location identity 变化 → stale
    doc7, rdoc7, b7 = basis_now()
    async def mut_view():
        rev = svc.read_view(ids["v_north"])["revision"]
        await svc.update_view(ids["v_north"], {"look_direction": "south east"},
                              expected_revision=rev)
    asyncio.run(mut_view())
    ok, why = holds(b7)
    assert not ok and why == "basis_changed"
    doc8, rdoc8, b8 = basis_now()
    async def mut_loc():
        rev = svc.read_location(ids["loc"])["revision"]
        await svc.update_location(ids["loc"], {"identity_description": "changed"},
                                  expected_revision=rev)
    asyncio.run(mut_loc())
    ok, why = holds(b8)
    assert not ok and why == "basis_changed"
