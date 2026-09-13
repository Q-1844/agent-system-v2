# tests/test_shot_binding_s3b2.py
# Shot Production Binding V1 — Phase S3B.2 验收测试（8.md §34 A-K 66 项 + §41 并发 +
# §42 原子性 + §44 跨域集成）。
#
# 核心语义：Binding=Intent（不落状态）/ Validity=Derived Projection（不落盘）/
# Read 永不 refresh / 只有 Explicit Refresh 接受新 basis / 只追踪真实依赖防 false stale。
#
# 夹具：real_pdir 在 make_pdir（project+resources+profile）之上，删除静态 scene_system.json，
# 经真实 Scene System 服务链（FakeClient/FakeVision，零外部模型）重建：
# location(2 anchors) + view_a(可见 door) + view_b(可见 window) + instance(s01) + 状态 +
# base views approved + SIV A 生成→审查 pass→批准（真实 saga，approved_basis_fingerprint 可重算）。
import hashlib
import sys
from pathlib import Path

import pytest

from src.core.models import Scene
from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.resources.models import (
    CharacterLook,
    Prop as ResourceProp,
    ReferenceAsset,
)
from src.resources.models import document_revision as rdoc_revision
from src.resources.store import ResourceStore
from src.scene_system import generation_records, instance_view_review as ivr
from src.scene_system.generation_records import approval_records_path
from src.scene_system.models import SceneInstance, SceneInstanceView
from src.scene_system.store import SceneSystemStore
from src.scene_system.service import SceneSystemService
from src.scene_system.visual_service import SceneVisualService
from src.shot_binding import ShotBindingService, ShotBindingStore
from src.shot_binding.validity import NOT_READY, READY, UNBOUND
from src.visual_profile import VisualProfileService

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))
from test_shot_binding_s3b1 import (  # noqa: E402
    COMP, LOOK, LOOK_BOB, PROP, PROP_NOAPPR, _gather, make_pdir, run, valid_payload,
)
from test_scene_system_s2b_approval import FakeVision, _pass_reply  # noqa: E402
from test_scene_system_s2b_generation import FakeClient, add_asset, _sciv_rev  # noqa: E402


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _hash_all(pdir: Path) -> dict:
    out = {}
    for name in ("project.json", "resources.json", "scene_system.json",
                 "project_profile.json", "shot_production_bindings.json"):
        p = pdir / "output" / name
        out[name] = _sha(p) if p.exists() else None
    return out


# ============ 真实跨域夹具（零外部模型）============

async def _approve_base_view(pdir: Path, vid: str):
    """base view 批准捷径（镜像 test_scene_system_s2b_approval._seed_two_state_views）。"""
    asset = f"ref_base_{vid}"
    add_asset(pdir, asset,
              f"output/scene_system/locations/x/views/{vid}/g/base_01.png")
    st = SceneSystemStore(pdir, pdir.name)
    async with st.write_lock():
        d = st.load()
        d.views[vid].approval_status = "approved"
        d.views[vid].reference_asset_id = asset
        st.save(d)
    from test_scene_system_s2b_generation import approval_record_fingerprint
    doc = st.load()
    fp2 = approval_record_fingerprint(pdir, doc, doc.views[vid], [asset])
    generation_records.append_record(pdir, {
        "approval_id": "apv_base", "operation": "scene.approve_location_view",
        "status": "approved", "view_id": vid, "asset_id": asset,
        "reviewed_basis_fingerprint": fp2, "completed_at": 0.0,
    }, path=approval_records_path(pdir))


async def _make_approved_siv(pdir: Path, svc: SceneSystemService,
                             vs: SceneVisualService, inst: str, view_id: str,
                             anchor_id: str) -> str:
    """SIV 全链：create → generate(FakeClient) → review pass(FakeVision) → approve(saga)。"""
    r = await vs.create_scene_instance_view(
        scene_instance_id=inst, location_view_id=view_id,
        expected_document_revision=svc.document_overview()["document_revision"])
    assert r["ok"], r
    sciv = r["target"]
    gen = await vs.generate_scene_instance_view(
        sciv, expected_revision=_sciv_rev(pdir, sciv), candidate_count=1,
        agnes_client=FakeClient(), include_peer=False)
    assert gen["ok"], gen.get("error")
    rr = await ivr.review_scene_instance_view_candidates(
        pdir, sciv, agnes_client=FakeVision(_pass_reply(anchor_id, PROP)))
    assert rr["ok"] and rr["candidate_reviews"][0]["overall"] == "pass", rr
    ap = await vs.approve_scene_instance_view(
        sciv, candidate_index=0, expected_revision=_sciv_rev(pdir, sciv),
        expected_resources_document_revision=rdoc_revision(
            ResourceStore(pdir, pdir.name).load()))
    assert ap["ok"], ap
    return sciv


async def _build_scene(pdir: Path) -> dict:
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)

    async def drev():
        return svc.document_overview()["document_revision"]

    loc = (await svc.create_location(
        name="lab", identity_description="small rectangular lab with one steel "
        "door and one west window",
        expected_document_revision=await drev()))["target"]
    a_door = (await svc.create_anchor(location_id=loc, name="north door", kind="door",
                                      expected_document_revision=await drev()))["target"]
    a_win = (await svc.create_anchor(location_id=loc, name="west window", kind="window",
                                     expected_document_revision=await drev()))["target"]
    view_a = (await svc.create_view(location_id=loc, label="door side",
                                    visible_anchor_ids=[a_door],
                                    expected_document_revision=await drev()))["target"]
    view_b = (await svc.create_view(location_id=loc, label="window side",
                                    visible_anchor_ids=[a_win],
                                    expected_document_revision=await drev()))["target"]
    inst = (await svc.create_scene_instance(story_scene_id="s01", location_id=loc,
                                            expected_document_revision=await drev()))["target"]
    rp = await svc.patch_scene_state(
        inst, {"time_of_day": "night", "weather": "rain",
               "anchor_states": {a_door: {"condition": "open"}},
               "prop_placements": {PROP: {"anchor_id": a_door, "state": "intact"}}},
        expected_revision=svc.read_scene_instance(inst)["revision"])
    assert rp["ok"], rp
    await _approve_base_view(pdir, view_a)
    await _approve_base_view(pdir, view_b)
    sciv_a = await _make_approved_siv(pdir, svc, vs, inst, view_a, a_door)
    return {"loc": loc, "a_door": a_door, "a_win": a_win, "view_a": view_a,
            "view_b": view_b, "inst": inst, "sciv_a": sciv_a}


def _bind(svc: ShotBindingService, *, shot_id="s01_c01", scene_id="s01",
          inst=None, view=None, pinned=None, characters=None, props=None,
          composition=False, action=None) -> dict:
    p = {"shot_id": shot_id, "scene_id": scene_id,
         "scene_binding": (None if inst is None else
                           {"scene_instance_id": inst, "location_view_id": view}),
         "character_bindings": (characters if characters is not None else
                                [{"character_name": "Alice", "look_id": LOOK}]),
         "prop_bindings": props or [], "production_notes": ""}
    if inst is not None and pinned:
        p["scene_binding"]["pinned_scene_instance_view_id"] = pinned
    if composition:
        p["character_bindings"][0]["composition_id"] = COMP
    if action:
        p["action"] = action
    r = svc.create_binding(p)
    assert r["ok"], r
    return r


def _reasons(v: dict) -> set:
    return {i["reason"] for i in v["issues"]}


@pytest.fixture()
def real_pdir(tmp_path):
    """真实跨域夹具：project+resources+profile（make_pdir）+ 真实 Scene System 链
    （SIV A approved，view_b 无 SIV 供 missing 测试）。"""
    _clear_for_test()
    pdir = make_pdir(tmp_path)
    (pdir / "output" / "scene_system.json").unlink()
    ids = run(_build_scene(pdir))
    yield pdir, ids
    _clear_for_test()


@pytest.fixture()
def bound(real_pdir):
    """默认绑定：s01_c01 → (inst, view_a) unpinned，Alice+LOOK。"""
    pdir, ids = real_pdir
    svc = ShotBindingService(pdir)
    r = _bind(svc, inst=ids["inst"], view=ids["view_a"])
    return pdir, ids, svc, r


# ============ A. Read semantics ============

def test_a1_no_binding_unbound(tmp_path):
    pdir = make_pdir(tmp_path)
    v = ShotBindingService(pdir).read_validity("s01_c01")
    assert v["readiness"] == UNBOUND and v["binding_state"] == "unbound"
    assert v["issues"] == [] and v["binding_revision"] is None


def test_a2_no_binding_read_does_not_create_file(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = ShotBindingService(pdir)
    for _ in range(3):
        svc.read_validity("s01_c01")
    assert not ShotBindingStore(pdir).exists()


def test_a3_valid_bound_shot_ready(bound):
    pdir, ids, svc, r = bound
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == READY, v["issues"]
    assert v["resolved_dependencies"]["scene_instance_view_id"] == ids["sciv_a"]
    assert v["issues"] == []


def test_a4_repeated_read_deterministic(bound):
    pdir, ids, svc, r = bound
    v1 = svc.read_validity("s01_c01")
    v2 = svc.read_validity("s01_c01")
    assert v1 == v2, "同一 binding + 同一 canonical → read model 逐字段确定"


def test_a5_a8_read_never_mutates_any_canonical(bound):
    """§3/§46：validity read 前后五文件字节不变（binding/resources/scene/profile/project）。"""
    pdir, ids, svc, r = bound
    h0 = _hash_all(pdir)
    for _ in range(3):
        svc.read_validity("s01_c01")
    assert _hash_all(pdir) == h0
    # resolved SIV 不得写回 pin（§16）
    doc = ShotBindingStore(pdir).load()
    assert doc.bindings["s01_c01"].scene_binding.pinned_scene_instance_view_id is None


# ============ B. Orphan / Shot drift ============

def test_b9_b10_orphan_not_ready_and_never_gc(bound):
    pdir, ids, svc, r = bound
    proj = ProjectStore(pdir).load()
    proj.scenes[0].shots = [s for s in proj.scenes[0].shots if s.shot_id != "s01_c01"]
    ProjectStore(pdir).save(proj)
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "orphan_binding" in _reasons(v)
    # 不 GC：binding 文件原样保留、read_binding 仍返回
    h = _sha(ShotBindingStore(pdir).path)
    svc.read_validity("s01_c01")
    assert _sha(ShotBindingStore(pdir).path) == h
    assert svc.read_binding("s01_c01") is not None
    # refresh orphan → 拒绝（§25）
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert not rr["ok"] and rr["error"] == "orphan_binding"


def test_b11_shot_cast_changed(bound):
    pdir, ids, svc, r = bound
    proj = ProjectStore(pdir).load()
    proj.get_shot("s01_c01").characters = ["Alice", "Bob"]
    ProjectStore(pdir).save(proj)
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "cast_mismatch" in _reasons(v)


def test_b12_shot_scene_changed(bound):
    pdir, ids, svc, r = bound
    proj = ProjectStore(pdir).load()
    proj.scenes.append(Scene(scene_id="s02", title="elsewhere", characters=["Alice"]))
    proj.get_shot("s01_c01").scene_id = "s02"
    ProjectStore(pdir).save(proj)
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "scene_mismatch" in _reasons(v)


def test_b13_instance_story_scene_mismatch(bound):
    pdir, ids, svc, r = bound
    st = SceneSystemStore(pdir, pdir.name)
    d = st.load()
    d.scene_instances[ids["inst"]].story_scene_id = "s09"
    st.save(d)
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "story_scene_mismatch" in _reasons(v)


# ============ C. Look ============

def test_c14_look_missing(bound):
    pdir, ids, svc, r = bound
    rd = ResourceStore(pdir, pdir.name).load()
    del rd.looks[LOOK]
    ResourceStore(pdir, pdir.name).save(rd)
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "look_missing" in _reasons(v)


def test_c15_look_no_longer_approved(bound):
    pdir, ids, svc, r = bound
    rd = ResourceStore(pdir, pdir.name).load()
    rd.looks[LOOK].approved_reference_asset_ids = []   # revoke 语义 = approved 集合清空
    ResourceStore(pdir, pdir.name).save(rd)
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "look_not_approved" in _reasons(v)


def test_c16_look_fingerprint_changed(bound):
    pdir, ids, svc, r = bound
    rd = ResourceStore(pdir, pdir.name).load()
    rd.reference_assets["ref_alice000009"] = ReferenceAsset(
        asset_id="ref_alice000009", url="http://example/alice9.png")
    rd.looks[LOOK].approved_reference_asset_ids.append("ref_alice000009")
    ResourceStore(pdir, pdir.name).save(rd)
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "fingerprint_mismatch" in _reasons(v)
    look_issue = [i for i in v["issues"]
                  if i["dependency_type"] == "character_look"][0]
    assert look_issue["detail"]["expected_fingerprint"] is not None
    assert look_issue["detail"]["current_fingerprint"] is not None
    # §20/§47：stale but currently valid → refresh 接受新 basis
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr
    assert svc.read_validity("s01_c01")["readiness"] == READY


def test_c17_unrelated_look_change_no_stale(bound):
    """§K62：Bob 的 Look 变化绝不能 stale Alice 的 Shot。"""
    pdir, ids, svc, r = bound
    assert svc.read_validity("s01_c01")["readiness"] == READY
    rd = ResourceStore(pdir, pdir.name).load()
    rd.reference_assets["ref_bob00000002"] = ReferenceAsset(
        asset_id="ref_bob00000002", url="http://example/bob2.png")
    rd.looks[LOOK_BOB].approved_reference_asset_ids.append("ref_bob00000002")
    rd.looks[LOOK_BOB].label = "Bob Winter"
    ResourceStore(pdir, pdir.name).save(rd)
    assert svc.read_validity("s01_c01")["readiness"] == READY


# ============ D. Prop ============

def test_d18_d20_prop_missing_unapproved_stale(bound):
    pdir, ids, svc, r = bound
    # 先给 shot 加直接 prop 绑定（lamp：approved、未被 composition 覆盖）
    rd = ResourceStore(pdir, pdir.name).load()
    rd.props["prop_lamp0000001"] = ResourceProp(
        prop_id="prop_lamp0000001", approval_status="approved",
        approved_reference_asset_ids=["ref_sword000001"])
    ResourceStore(pdir, pdir.name).save(rd)
    rr = svc.replace_binding(
        "s01_c01",
        {**valid_payload(),
         "scene_binding": {"scene_instance_id": ids["inst"],
                           "location_view_id": ids["view_a"]},
         "prop_bindings": [{"prop_id": "prop_lamp0000001", "relation": "near",
                            "anchor": "beside_character"}]},
        expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr
    rev = rr["binding_revision"]
    assert svc.read_validity("s01_c01")["readiness"] == READY
    # 18 missing
    rd = ResourceStore(pdir, pdir.name).load()
    del rd.props["prop_lamp0000001"]
    ResourceStore(pdir, pdir.name).save(rd)
    v = svc.read_validity("s01_c01")
    assert "prop_missing" in _reasons(v) and v["readiness"] == NOT_READY
    # 19 unapproved（重建后清空 approved）
    rd = ResourceStore(pdir, pdir.name).load()
    rd.props["prop_lamp0000001"] = ResourceProp(prop_id="prop_lamp0000001",
                                                approval_status="approved",
                                                approved_reference_asset_ids=[])
    ResourceStore(pdir, pdir.name).save(rd)
    assert "prop_not_approved" in _reasons(svc.read_validity("s01_c01"))
    # 21 unrelated prop changed → 仍 READY（§K64：先恢复 lamp 到 fresh 态）
    rd = ResourceStore(pdir, pdir.name).load()
    rd.props["prop_lamp0000001"].approved_reference_asset_ids = ["ref_sword000001"]
    ResourceStore(pdir, pdir.name).save(rd)
    rf = svc.refresh_binding("s01_c01", expected_binding_revision=rev)
    assert rf["ok"], rf
    rev = rf["binding_revision"]
    rd = ResourceStore(pdir, pdir.name).load()
    rd.props[PROP_NOAPPR].name = "renamed unrelated"
    ResourceStore(pdir, pdir.name).save(rd)
    assert svc.read_validity("s01_c01")["readiness"] == READY
    # 20 fingerprint changed → NOT_READY
    rd = ResourceStore(pdir, pdir.name).load()
    rd.props["prop_lamp0000001"].tags = ["changed"]
    ResourceStore(pdir, pdir.name).save(rd)
    v = svc.read_validity("s01_c01")
    assert "fingerprint_mismatch" in _reasons(v) and v["readiness"] == NOT_READY


# ============ E. Composition ============

def _bind_with_comp(svc, ids):
    r = _bind(svc, inst=ids["inst"], view=ids["view_a"], composition=True)
    return r


def test_e22_e23_e24_composition_missing_unapproved_stale(real_pdir):
    pdir, ids = real_pdir
    svc = ShotBindingService(pdir)
    r = _bind_with_comp(svc, ids)
    assert svc.read_validity("s01_c01")["readiness"] == READY
    # 22 missing
    rd = ResourceStore(pdir, pdir.name).load()
    del rd.compositions[COMP]
    ResourceStore(pdir, pdir.name).save(rd)
    assert "composition_missing" in _reasons(svc.read_validity("s01_c01"))
    # 23 not approved（重建 + 翻状态）
    rd = ResourceStore(pdir, pdir.name).load()
    from src.resources.models import CharacterReferenceComposition, CompositionPropBinding
    rd.compositions[COMP] = CharacterReferenceComposition(
        composition_id=COMP, character_name="Alice", look_id=LOOK,
        prop_bindings=[CompositionPropBinding(prop_id=PROP)],
        approval_status="unreviewed")
    ResourceStore(pdir, pdir.name).save(rd)
    assert "composition_not_approved" in _reasons(svc.read_validity("s01_c01"))
    # 24 fingerprint changed（approved 但内容变化）
    rd = ResourceStore(pdir, pdir.name).load()
    rd.compositions[COMP].approval_status = "approved"
    rd.compositions[COMP].pose_note = "adjusted"
    ResourceStore(pdir, pdir.name).save(rd)
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "fingerprint_mismatch" in _reasons(v)
    assert any(i["dependency_type"] == "composition" and
               i["reason"] == "fingerprint_mismatch" for i in v["issues"])


def test_e25_conflict_recheck_at_validity(real_pdir):
    """§13：conflict 规则在 validity read 也要重查（Resources 后续可能变化）。"""
    pdir, ids = real_pdir
    svc = ShotBindingService(pdir)
    # composition(Alice, look, [PROP]) + direct lamp → bind 时合法
    rd = ResourceStore(pdir, pdir.name).load()
    rd.props["prop_lamp0000001"] = ResourceProp(
        prop_id="prop_lamp0000001", approval_status="approved",
        approved_reference_asset_ids=["ref_sword000001"])
    ResourceStore(pdir, pdir.name).save(rd)
    r = _bind(svc, inst=ids["inst"], view=ids["view_a"], composition=True,
              props=[{"prop_id": "prop_lamp0000001", "relation": "near",
                      "anchor": "beside_character"}])
    assert svc.read_validity("s01_c01")["readiness"] == READY
    # 之后 composition 追加覆盖 lamp（Resources 侧编辑）→ conflict 重现 → NOT_READY
    rd = ResourceStore(pdir, pdir.name).load()
    from src.resources.models import CompositionPropBinding
    rd.compositions[COMP].prop_bindings.append(
        CompositionPropBinding(prop_id="prop_lamp0000001"))
    ResourceStore(pdir, pdir.name).save(rd)
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "binding_conflict" in _reasons(v)
    # refresh 同样拒绝（§22 invalid 不可 refresh）
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert not rr["ok"] and rr["error"] == "binding_conflict"


def test_e26_no_invented_recursive_revoke_policy(real_pdir):
    """§12 边界：composition 内部 look 被 revoke 时，Shot Binding 不自行把 composition
    判死——look 依赖报 look_not_approved，composition 自身按 Resources 公开事实（仍
    approved）报 ok。nested revoke policy 属 Resources，不在本层发明。"""
    pdir, ids = real_pdir
    svc = ShotBindingService(pdir)
    _bind_with_comp(svc, ids)
    rd = ResourceStore(pdir, pdir.name).load()
    rd.looks[LOOK].approved_reference_asset_ids = []
    ResourceStore(pdir, pdir.name).save(rd)
    v = svc.read_validity("s01_c01")
    comp_checks = [c for c in v["dependency_checks"]
                   if c["dependency_type"] == "composition"]
    assert comp_checks and all(c["reason"] == "ok" for c in comp_checks)
    assert "look_not_approved" in _reasons(v)


# ============ F. Scene（结构 + pinned）============

def _pinned_bind(svc, ids, shot_id="s01_c01"):
    return _bind(svc, shot_id=shot_id, inst=ids["inst"], view=ids["view_a"],
                 pinned=ids["sciv_a"])


def test_f27_f29_scene_structural_missing(real_pdir):
    pdir, ids = real_pdir
    svc = ShotBindingService(pdir)
    r = _bind(svc, inst=ids["inst"], view=ids["view_a"])
    # 27 instance missing（canonical 后续被删 → validity 层重新发现）
    st = SceneSystemStore(pdir, pdir.name)
    d = st.load()
    d.scene_instances.pop(ids["inst"])
    st.save(d)
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "scene_instance_missing" in _reasons(v)
    # 29 view missing（恢复 instance 后删 view；SIV 解析随之短路）
    d = st.load()
    d.scene_instances[ids["inst"]] = _restore_instance(pdir, ids)
    d.views.pop(ids["view_a"])
    d.scene_instance_views.pop(ids["sciv_a"])
    st.save(d)
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "location_view_missing" in _reasons(v)


def _restore_instance(pdir: Path, ids: dict):
    from src.scene_system.models import SceneInstance
    return SceneInstance(scene_instance_id=ids["inst"], story_scene_id="s01",
                         location_id=ids["loc"])


def test_f28_scene_instance_inactive(bound):
    pdir, ids, svc, r = bound
    st = SceneSystemStore(pdir, pdir.name)
    d = st.load()
    d.scene_instances[ids["inst"]].status = "archived"
    st.save(d)
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "scene_instance_not_active" in _reasons(v)


def test_f30_f31_f32_pinned_siv_structural_mismatch(real_pdir):
    pdir, ids = real_pdir
    svc = ShotBindingService(pdir)
    _pinned_bind(svc, ids)
    # 注入异常实体（foreign instance / wrong view）
    st = SceneSystemStore(pdir, pdir.name)
    d = st.load()
    d.scene_instance_views["sciv_foreign0001"] = SceneInstanceView(
        scene_instance_view_id="sciv_foreign0001",
        scene_instance_id="scin_other00001", location_view_id=ids["view_a"],
        approval_status="approved", approved_basis_fingerprint="aaaa0001")
    d.scene_instance_views["sciv_wrongview1"] = SceneInstanceView(
        scene_instance_view_id="sciv_wrongview1", scene_instance_id=ids["inst"],
        location_view_id=ids["view_b"], approval_status="approved",
        approved_basis_fingerprint="bbbb0002")
    st.save(d)
    for pinned_id, reason in (
            ("sciv_nope000001", "scene_instance_view_missing"),
            ("sciv_foreign0001", "scene_instance_view_instance_mismatch"),
            ("sciv_wrongview1", "scene_instance_view_view_mismatch")):
        doc = ShotBindingStore(pdir).load()
        doc.bindings["s01_c01"].scene_binding.pinned_scene_instance_view_id = pinned_id
        ShotBindingStore(pdir).save(doc)
        v = svc.read_validity("s01_c01")
        assert v["readiness"] == NOT_READY and reason in _reasons(v), (pinned_id, v)


def test_f33_pinned_siv_stale(real_pdir):
    """§17：Scene System verdict 消费——可见状态变化 → SIV stale → NOT_READY。
    §22：stale-but-valid 可 refresh（不掩盖：validity 之后仍诚实报 stale）。"""
    pdir, ids = real_pdir
    svc = ShotBindingService(pdir)
    r = _pinned_bind(svc, ids)
    assert svc.read_validity("s01_c01")["readiness"] == READY
    # 改 door 状态（view A 可见）→ SIV A stale
    ssvc = SceneSystemService(pdir, pdir.name)
    rp = run(ssvc.patch_scene_state(
        ids["inst"], {"anchor_states": {ids["a_door"]: {"condition": "blocked"}}},
        expected_revision=ssvc.read_scene_instance(ids["inst"])["revision"]))
    assert rp["ok"], rp
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "stale_dependency" in _reasons(v)
    stale = [i for i in v["issues"] if i["reason"] == "stale_dependency"][0]
    assert stale["detail"]["stale_reasons"], "透传 Scene System 细粒度归因"
    # refresh 不被 stale 阻塞（stale-but-valid），但绝不假 READY：
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr
    v2 = svc.read_validity("s01_c01")
    assert v2["readiness"] == NOT_READY and "stale_dependency" in _reasons(v2)


def test_f34_valid_pinned_siv_ready(real_pdir):
    pdir, ids = real_pdir
    svc = ShotBindingService(pdir)
    _pinned_bind(svc, ids)
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == READY, v["issues"]
    assert v["resolved_dependencies"]["scene_instance_view_id"] == ids["sciv_a"]


# ============ G. Unpinned SIV ============

def test_g35_unpinned_resolves_single_valid(bound):
    pdir, ids, svc, r = bound
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == READY
    assert v["resolved_dependencies"]["scene_instance_view_id"] == ids["sciv_a"]
    # 复用 Scene System 身份规则（(instance, view) 唯一实体）——无 Shot 私有选择策略


def test_g36_unpinned_no_valid_siv(real_pdir):
    pdir, ids = real_pdir
    svc = ShotBindingService(pdir)
    _bind(svc, inst=ids["inst"], view=ids["view_b"])   # view_b 无 SIV
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "scene_instance_view_missing" in _reasons(v)


def test_g37_unpinned_ambiguous_fail_closed(bound):
    """§15 Case B：同对多实体=数据异常 → scene_instance_view_ambiguous（不猜）。"""
    pdir, ids, svc, r = bound
    st = SceneSystemStore(pdir, pdir.name)
    d = st.load()
    d.scene_instance_views["sciv_dup00000001"] = SceneInstanceView(
        scene_instance_view_id="sciv_dup00000001", scene_instance_id=ids["inst"],
        location_view_id=ids["view_a"], approval_status="approved",
        approved_basis_fingerprint="ffff0001")
    st.save(d)
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "scene_instance_view_ambiguous" in _reasons(v)
    assert v["resolved_dependencies"]["scene_instance_view_id"] is None


def test_g38_read_never_pins(bound):
    pdir, ids, svc, r = bound
    svc.read_validity("s01_c01")
    doc = ShotBindingStore(pdir).load()
    assert doc.bindings["s01_c01"].scene_binding.pinned_scene_instance_view_id is None


def test_g39_unrelated_siv_change_no_stale(real_pdir):
    """§K/G39：另一方向 SIV 的生成/批准/stale 不影响本镜。"""
    pdir, ids = real_pdir
    svc = ShotBindingService(pdir)
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    assert svc.read_validity("s01_c01")["readiness"] == READY
    # 生成并批准 view B 的 SIV（unrelated SIV 出现）
    run(_make_approved_siv(pdir, SceneSystemService(pdir, pdir.name),
                           SceneVisualService(pdir, pdir.name),
                           ids["inst"], ids["view_b"], ids["a_win"]))
    assert svc.read_validity("s01_c01")["readiness"] == READY
    # B 方向可见状态变化（保留 door 态——patch 是整段替换语义）→ B stale，A 仍 READY
    ssvc = SceneSystemService(pdir, pdir.name)
    rp = run(ssvc.patch_scene_state(
        ids["inst"],
        {"anchor_states": {ids["a_door"]: {"condition": "open"},
                           ids["a_win"]: {"condition": "broken"}}},
        expected_revision=ssvc.read_scene_instance(ids["inst"])["revision"]))
    assert rp["ok"]
    assert svc.read_validity("s01_c01")["readiness"] == READY


# ============ H. View-relevant state（§18 粒度核心证据）============

def test_h40_visible_state_change_stales(bound):
    pdir, ids, svc, r = bound
    ssvc = SceneSystemService(pdir, pdir.name)
    rp = run(ssvc.patch_scene_state(
        ids["inst"], {"anchor_states": {ids["a_door"]: {"condition": "blocked"}}},
        expected_revision=ssvc.read_scene_instance(ids["inst"])["revision"]))
    assert rp["ok"]
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "stale_dependency" in _reasons(v)


def test_h41_invisible_state_change_does_not_stale(bound):
    """§18/§K66：对 bound SIV 不可见的状态变化不得 stale（Scene System view-relevant
    投影 + binding 不比对粗粒度 scene_instance 指纹）。
    🔴 patch_scene_state 的 anchor_states 是整段替换语义——须保留 door 态。"""
    pdir, ids, svc, r = bound
    ssvc = SceneSystemService(pdir, pdir.name)
    rp = run(ssvc.patch_scene_state(
        ids["inst"],
        {"anchor_states": {ids["a_door"]: {"condition": "open"},
                           ids["a_win"]: {"condition": "broken"}}},
        expected_revision=ssvc.read_scene_instance(ids["inst"])["revision"]))
    assert rp["ok"]
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == READY, v["issues"]


def test_h42_unrelated_location_view_change_no_stale(bound):
    pdir, ids, svc, r = bound
    st = SceneSystemStore(pdir, pdir.name)
    d = st.load()
    d.views[ids["view_b"]].label = "renamed unrelated"
    st.save(d)
    assert svc.read_validity("s01_c01")["readiness"] == READY


# ============ I. ProjectVisualProfile ============

def test_i43_specified_profile_valid(bound):
    pdir, ids, svc, r = bound
    v = svc.read_validity("s01_c01")
    prof_checks = [c for c in v["dependency_checks"]
                   if c["dependency_type"] == "project_visual_profile"]
    assert prof_checks and prof_checks[0]["reason"] == "ok"
    assert v["readiness"] == READY


async def _regen_and_approve_siv(pdir: Path, sciv_id: str, anchor_id: str):
    """Scene 侧恢复链：base view 重批准（P1 同口径：profile 变化后 base view 的批准
    语义一并 stale——check_base_location_view 与 review/approve 同口径，2026-09-13
    Golden Path blocker 修正）→ SIV revoke → regen（同实体）→ review pass →
    approve（新 basis）。"""
    vs = SceneVisualService(pdir, pdir.name)
    _sciv = SceneSystemStore(pdir, pdir.name).load().scene_instance_views[sciv_id]
    await _approve_base_view(pdir, _sciv.location_view_id)
    ap = await vs.revoke_scene_instance_view_approval(
        sciv_id, expected_revision=_sciv_rev(pdir, sciv_id), reason="profile changed")
    assert ap["ok"], ap
    gen = await vs.generate_scene_instance_view(
        sciv_id, expected_revision=_sciv_rev(pdir, sciv_id), candidate_count=1,
        agnes_client=FakeClient(), include_peer=False)
    assert gen["ok"], gen.get("error")
    rr = await ivr.review_scene_instance_view_candidates(
        pdir, sciv_id, agnes_client=FakeVision(_pass_reply(anchor_id, PROP)))
    assert rr["ok"] and rr["candidate_reviews"][0]["overall"] == "pass", rr
    ap2 = await vs.approve_scene_instance_view(
        sciv_id, candidate_index=0, expected_revision=_sciv_rev(pdir, sciv_id),
        expected_resources_document_revision=rdoc_revision(
            ResourceStore(pdir, pdir.name).load()))
    assert ap2["ok"], ap2


def test_i44_profile_fingerprint_changed(bound):
    """Profile 变化 → NOT_READY（binding 指纹 + SIV stale 双路径）。完整诚实恢复链：
    refresh 接受新 profile basis → validity 仍 NOT_READY（Scene verdict：旧风格 SIV
    stale）→ Scene 侧 regen+reapprove → validity 报 fingerprint_mismatch（SIV 新锚点）
    → 再次 refresh → READY。binding 层绝不掩盖 Scene verdict（§17）。"""
    pdir, ids, svc, r = bound
    cur = VisualProfileService(pdir, pdir.name).read()["revision"]
    rp = VisualProfileService(pdir, pdir.name).update(
        render_family="stylized", art_direction="ink wash", expected_revision=cur)
    assert rp["ok"]
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY
    prof = [i for i in v["issues"]
            if i["dependency_type"] == "project_visual_profile"][0]
    assert prof["reason"] == "fingerprint_mismatch"
    # refresh 接受新风格 basis（profile 维度修复）
    rr = svc.refresh_binding("s01_c01",
                             expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr
    # 但 SIV 仍被 Scene System 判 stale（旧风格图）→ 诚实 NOT_READY，绝不假 READY
    v2 = svc.read_validity("s01_c01")
    assert v2["readiness"] == NOT_READY and "stale_dependency" in _reasons(v2)
    assert "project_visual_profile_changed" in \
        [i for i in v2["issues"] if i["reason"] == "stale_dependency"][0]["detail"]["stale_reasons"]
    # Scene 侧恢复：regen + reapprove（新风格下的 SIV，新锚点）
    run(_regen_and_approve_siv(pdir, ids["sciv_a"], ids["a_door"]))
    # binding 层：SIV 新锚点 ≠ 快照 → fingerprint_mismatch → 再次 refresh → READY
    v3 = svc.read_validity("s01_c01")
    assert "fingerprint_mismatch" in _reasons(v3) or "stale_dependency" in _reasons(v3)
    rr2 = svc.refresh_binding("s01_c01", expected_binding_revision=rr["binding_revision"])
    assert rr2["ok"], rr2
    assert svc.read_validity("s01_c01")["readiness"] == READY


def test_i45_profile_unspecified_not_ready(bound):
    pdir, ids, svc, r = bound
    (pdir / "output" / "project_profile.json").unlink()
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY
    assert "project_visual_profile_unspecified" in _reasons(v)
    # refresh 同样拒绝（不 fallback default）
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert not rr["ok"] and rr["error"] == "project_visual_profile_unspecified"


def test_i46_unrelated_project_metadata_no_stale(bound):
    pdir, ids, svc, r = bound
    proj = ProjectStore(pdir).load()
    proj.story_text = (proj.story_text or "") + " extra chapter notes"
    proj.get_shot("s01_c01").note = "director scribble"
    ProjectStore(pdir).save(proj)
    assert svc.read_validity("s01_c01")["readiness"] == READY


# ============ J. Explicit Refresh ============

def _make_look_stale_but_valid(pdir):
    rd = ResourceStore(pdir, pdir.name).load()
    rd.reference_assets["ref_alice000009"] = ReferenceAsset(
        asset_id="ref_alice000009", url="http://example/alice9.png")
    rd.looks[LOOK].approved_reference_asset_ids.append("ref_alice000009")
    ResourceStore(pdir, pdir.name).save(rd)


def test_j47_j48_j49_j61_refresh_stale_but_valid(bound):
    """§22 合法 refresh 全链：stale→refresh→snapshot 更新→revision 翻动→READY。"""
    pdir, ids, svc, r = bound
    _make_look_stale_but_valid(pdir)
    assert svc.read_validity("s01_c01")["readiness"] == NOT_READY
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr
    assert rr["binding_revision"] != r["binding_revision"]          # 49 OCC 翻动
    assert rr["document_revision"] != r["document_revision"]
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == READY                                   # 48/61
    from src.resources.models import look_revision
    rd = ResourceStore(pdir, pdir.name).load()
    assert v["binding_revision"] == rr["binding_revision"]
    doc = ShotBindingStore(pdir).load()
    assert doc.bindings["s01_c01"].dependency_fingerprints.looks[LOOK] == \
        look_revision(rd.looks[LOOK])                                # 48 snapshot=current


def test_j50_j51_refresh_requires_expected_revision(bound):
    pdir, ids, svc, r = bound
    rr = svc.refresh_binding("s01_c01", expected_binding_revision="")
    assert not rr["ok"] and rr["error"] == "stale_binding_revision"
    h = _sha(ShotBindingStore(pdir).path)
    rr2 = svc.refresh_binding("s01_c01", expected_binding_revision="deadbeef")
    assert not rr2["ok"] and rr2["error"] == "stale_binding_revision"
    assert _sha(ShotBindingStore(pdir).path) == h, "拒绝后字节不变"


def test_j52_j42_refresh_failure_atomic(bound):
    """§42：refresh 中任何失败 → binding canonical 完整，无部分指纹写入。"""
    pdir, ids, svc, r = bound
    h0 = _sha(ShotBindingStore(pdir).path)
    # 依赖失效（look 消失）→ refresh 失败
    rd = ResourceStore(pdir, pdir.name).load()
    del rd.looks[LOOK]
    ResourceStore(pdir, pdir.name).save(rd)
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert not rr["ok"] and rr["error"] == "look_missing"
    assert _sha(ShotBindingStore(pdir).path) == h0, "失败零写入（原子性）"
    # OCC stale 同样零写入
    rd = ResourceStore(pdir, pdir.name).load()
    rd.looks[LOOK] = CharacterLook(look_id=LOOK, character_name="Alice",
                                   approval_status="approved",
                                   approved_reference_asset_ids=["ref_alice000001"])
    ResourceStore(pdir, pdir.name).save(rd)
    rr2 = svc.refresh_binding("s01_c01", expected_binding_revision="deadbeef")
    assert not rr2["ok"]
    assert _sha(ShotBindingStore(pdir).path) == h0


def test_j53_refresh_preserves_intent_exactly(bound):
    pdir, ids, svc, r = bound
    before = r["binding"]
    _make_look_stale_but_valid(pdir)
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert rr["ok"]
    after = rr["binding"]
    intent_keys = ("shot_id", "scene_id", "scene_binding", "character_bindings",
                   "prop_bindings", "action", "production_notes", "created_at")
    for k in intent_keys:
        assert before[k] == after[k], f"refresh 不得改动 Intent 字段: {k}"


def test_j54_j63_refresh_does_not_pin_and_records_basis(bound):
    """§24：unpinned refresh 接受当前解析结果进快照，但绝不写回 pin。"""
    pdir, ids, svc, r = bound
    assert r["binding"]["dependency_fingerprints"]["scene_instance_view"] is None
    _make_look_stale_but_valid(pdir)
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert rr["ok"]
    doc = ShotBindingStore(pdir).load()
    b = doc.bindings["s01_c01"]
    assert b.scene_binding.pinned_scene_instance_view_id is None      # 54
    from src.scene_system.models import view_revision
    sciv_fp = SceneSystemStore(pdir, pdir.name).load() \
        .scene_instance_views[ids["sciv_a"]].approved_basis_fingerprint
    assert b.dependency_fingerprints.scene_instance_view == sciv_fp   # 63
    assert svc.read_validity("s01_c01")["readiness"] == READY


def test_j55_j56_refresh_rejects_missing_and_unapproved(bound):
    pdir, ids, svc, r = bound
    rd = ResourceStore(pdir, pdir.name).load()
    del rd.looks[LOOK]
    ResourceStore(pdir, pdir.name).save(rd)
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert not rr["ok"] and rr["error"] == "look_missing"              # 55
    rd = ResourceStore(pdir, pdir.name).load()
    rd.looks[LOOK] = CharacterLook(look_id=LOOK, character_name="Alice",
                                   approval_status="approved",
                                   approved_reference_asset_ids=[])
    ResourceStore(pdir, pdir.name).save(rd)
    rr2 = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert not rr2["ok"] and rr2["error"] == "look_not_approved"       # 56


def test_j57_refresh_rejects_profile_unspecified(bound):
    pdir, ids, svc, r = bound
    (pdir / "output" / "project_profile.json").unlink()
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert not rr["ok"] and rr["error"] == "project_visual_profile_unspecified"


def test_j58_refresh_rejects_orphan(bound):
    pdir, ids, svc, r = bound
    proj = ProjectStore(pdir).load()
    proj.scenes[0].shots = [s for s in proj.scenes[0].shots if s.shot_id != "s01_c01"]
    ProjectStore(pdir).save(proj)
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert not rr["ok"] and rr["error"] == "orphan_binding"


def test_j59_refresh_rejects_cast_mismatch(bound):
    pdir, ids, svc, r = bound
    proj = ProjectStore(pdir).load()
    proj.get_shot("s01_c01").characters = ["Alice", "Bob"]
    ProjectStore(pdir).save(proj)
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert not rr["ok"] and rr["error"] == "cast_mismatch"


def test_j60_refresh_rejects_conflict(real_pdir):
    pdir, ids = real_pdir
    svc = ShotBindingService(pdir)
    rd = ResourceStore(pdir, pdir.name).load()
    rd.props["prop_lamp0000001"] = ResourceProp(
        prop_id="prop_lamp0000001", approval_status="approved",
        approved_reference_asset_ids=["ref_sword000001"])
    ResourceStore(pdir, pdir.name).save(rd)
    r = _bind(svc, inst=ids["inst"], view=ids["view_a"], composition=True,
              props=[{"prop_id": "prop_lamp0000001", "relation": "near",
                      "anchor": "beside_character"}])
    rd = ResourceStore(pdir, pdir.name).load()
    from src.resources.models import CompositionPropBinding
    rd.compositions[COMP].prop_bindings.append(
        CompositionPropBinding(prop_id="prop_lamp0000001"))
    ResourceStore(pdir, pdir.name).save(rd)
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert not rr["ok"] and rr["error"] == "binding_conflict"


def test_j_refresh_unpinned_siv_missing_rejected(real_pdir):
    """§21/§22：unpinned 解析失败（无 approved SIV）→ refresh 拒绝。"""
    pdir, ids = real_pdir
    svc = ShotBindingService(pdir)
    r = _bind(svc, inst=ids["inst"], view=ids["view_b"])   # view_b 无 SIV
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert not rr["ok"] and rr["error"] == "scene_instance_view_missing"


def test_j_refresh_siv_reapproved_fingerprint_mismatch_then_ready(bound):
    """pinned SIV 锚点变化（等价 regen+reapprove）→ fingerprint_mismatch。
    模拟态下 Scene verdict 仍 stale（伪造锚点 ≠ committed gen basis）→ refresh 可过
    （stale-but-valid）但 validity 诚实保持 NOT_READY——绝不假 READY。"""
    pdir, ids, svc, r = bound
    doc = ShotBindingStore(pdir).load()
    doc.bindings["s01_c01"].scene_binding.pinned_scene_instance_view_id = ids["sciv_a"]
    ShotBindingStore(pdir).save(doc)
    cur_rev = svc.read_binding("s01_c01")["binding_revision"]   # store 改动后取当前 revision
    st = SceneSystemStore(pdir, pdir.name)
    d = st.load()
    d.scene_instance_views[ids["sciv_a"]].approved_basis_fingerprint = "newfp0001"
    st.save(d)
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "fingerprint_mismatch" in _reasons(v)
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=cur_rev)
    assert rr["ok"], rr
    doc2 = ShotBindingStore(pdir).load()
    assert doc2.bindings["s01_c01"].dependency_fingerprints.scene_instance_view == "newfp0001"
    v2 = svc.read_validity("s01_c01")
    assert v2["readiness"] == NOT_READY and "stale_dependency" in _reasons(v2)


def test_j41_concurrent_refresh_exactly_one_success(bound):
    """§41：同 revision 双 refresh 并发 → 恰一成功，另一 stale 拒绝（不绕开 S3B.1 OCC）。"""
    pdir, ids, svc, r = bound
    _make_look_stale_but_valid(pdir)
    rs = run(_gather(
        svc.refresh_binding_async("s01_c01", expected_binding_revision=r["binding_revision"]),
        svc.refresh_binding_async("s01_c01", expected_binding_revision=r["binding_revision"])))
    assert sorted(x["ok"] for x in rs) == [False, True]
    assert {x.get("error") for x in rs} == {None, "stale_binding_revision"}


# ============ K. Cross-domain false-positive protection ============

def test_k62_66_false_stale_protection_suite(bound):
    """§K 专门回归组：五类无关变化一个都不能 stale 本镜。"""
    pdir, ids, svc, r = bound
    assert svc.read_validity("s01_c01")["readiness"] == READY
    # 62 unrelated CharacterLook（Bob）
    rd = ResourceStore(pdir, pdir.name).load()
    rd.looks[LOOK_BOB].description_en = "rewritten"
    ResourceStore(pdir, pdir.name).save(rd)
    assert svc.read_validity("s01_c01")["readiness"] == READY
    # 63 unrelated Composition（unreviewed 的另一 comp）
    rd = ResourceStore(pdir, pdir.name).load()
    if "comp_noappr00001" in rd.compositions:
        rd.compositions["comp_noappr00001"].pose_note = "x"
    ResourceStore(pdir, pdir.name).save(rd)
    assert svc.read_validity("s01_c01")["readiness"] == READY
    # 64 unrelated Prop
    rd = ResourceStore(pdir, pdir.name).load()
    rd.props[PROP_NOAPPR].tags = ["unrelated"]
    ResourceStore(pdir, pdir.name).save(rd)
    assert svc.read_validity("s01_c01")["readiness"] == READY
    # 65 unrelated LocationView（view_b label）
    st = SceneSystemStore(pdir, pdir.name)
    d = st.load()
    d.views[ids["view_b"]].label = "unrelated rename"
    st.save(d)
    assert svc.read_validity("s01_c01")["readiness"] == READY
    # 66 invisible Scene state（window 只对 view_b 可见；保留 door 态——替换语义）
    ssvc = SceneSystemService(pdir, pdir.name)
    rp = run(ssvc.patch_scene_state(
        ids["inst"],
        {"anchor_states": {ids["a_door"]: {"condition": "open"},
                           ids["a_win"]: {"condition": "cracked"}}},
        expected_revision=ssvc.read_scene_instance(ids["inst"])["revision"]))
    assert rp["ok"]
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == READY, v["issues"]


# ============ §44 跨域集成（真链路一次走通）============

def test_integration_full_cross_domain_flow(real_pdir):
    """§44 Resources + Scene System + Visual Profile + Shot Binding 全连接证明：
    READY → look 变 → NOT_READY → refresh → READY → 无关 Bob Look → 仍 READY →
    Profile 变 → NOT_READY → refresh（接受新风格 basis）→ 仍 NOT_READY（Scene verdict：
    旧风格 SIV stale，binding 不掩盖）→ Scene 侧 regen+reapprove → refresh → READY →
    可见状态变 → NOT_READY。"""
    pdir, ids = real_pdir
    svc = ShotBindingService(pdir)
    r = _bind(svc, inst=ids["inst"], view=ids["view_a"], composition=True)
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == READY, v["issues"]
    # 1 bound look 变
    _make_look_stale_but_valid(pdir)
    assert svc.read_validity("s01_c01")["readiness"] == NOT_READY
    rev = r["binding_revision"]
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=rev)
    assert rr["ok"]
    rev = rr["binding_revision"]
    assert svc.read_validity("s01_c01")["readiness"] == READY
    # 2 unrelated Bob look
    rd = ResourceStore(pdir, pdir.name).load()
    rd.looks[LOOK_BOB].label = "Bob changed"
    ResourceStore(pdir, pdir.name).save(rd)
    assert svc.read_validity("s01_c01")["readiness"] == READY
    # 3 profile 变
    cur = VisualProfileService(pdir, pdir.name).read()["revision"]
    rp = VisualProfileService(pdir, pdir.name).update(
        render_family="photorealistic", expected_revision=cur)
    assert rp["ok"]
    assert svc.read_validity("s01_c01")["readiness"] == NOT_READY
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=rev)
    assert rr["ok"], rr
    rev = rr["binding_revision"]
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "stale_dependency" in _reasons(v)
    # Scene 侧恢复：新风格下 regen + reapprove
    run(_regen_and_approve_siv(pdir, ids["sciv_a"], ids["a_door"]))
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=rev)
    assert rr["ok"], rr
    rev = rr["binding_revision"]
    assert svc.read_validity("s01_c01")["readiness"] == READY
    # 4 相关可见状态变 → SIV stale → NOT_READY（refresh 不掩盖，仍 NOT_READY）
    ssvc = SceneSystemService(pdir, pdir.name)
    rp2 = run(ssvc.patch_scene_state(
        ids["inst"], {"anchor_states": {ids["a_door"]: {"condition": "blocked"}}},
        expected_revision=ssvc.read_scene_instance(ids["inst"])["revision"]))
    assert rp2["ok"]
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "stale_dependency" in _reasons(v)
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=rev)
    assert rr["ok"]
    v = svc.read_validity("s01_c01")
    assert v["readiness"] == NOT_READY and "stale_dependency" in _reasons(v)


# ============ §32 损坏 canonical 不伪装成业务 NOT_READY ============

def test_corrupt_bindings_file_raises_not_not_ready(bound):
    pdir, ids, svc, r = bound
    p = ShotBindingStore(pdir).path
    p.write_text("{corrupt", encoding="utf-8")
    with pytest.raises(Exception):
        svc.read_validity("s01_c01")
