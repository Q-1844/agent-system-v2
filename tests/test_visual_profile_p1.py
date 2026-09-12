# tests/test_visual_profile_p1.py
# Project Visual Profile V1 — Phase P1 验收测试（P1 §21 T1-T23 可单测部分）。
#
# T1  old project read → unspecified          T2  read 不创建 profile 文件
# T3  create profile                          T4  update OCC
# T5  stale OCC rejected                      T6  deterministic fingerprint
# T8  photorealistic compile deterministic    T9  2d animation compile deterministic
# T10 custom art_direction compile            T11 model/provider 不进入 profile
# T12 Look generation basis 含 profile 指纹   T13 Composition basis 含 profile 指纹
# T14 Prop 生成不存在（审计结论）→ prop review 记录 style 口径替代验证
# T15 LocationView basis 含 profile           T16 SceneInstanceView basis 含 profile
# T17 profile change → stale（resources + scene 两口径）
# T18 old generation without profile → unknown（不伪 pass）
# T19 review 有 project_style_consistency     T20 style fail → overall 不能 pass
# T21 project.json bytes/schema 不变          T22 resources.json schema 不变
# T23 scene_system.json schema/ontology 不变
# fail closed：unspecified 生成拒绝 / 坏文件诚实报错 / HTTP 契约
# （T7 在 test_project_creation.py；T24-T29 为流程性证明，见报告）
import asyncio
import json
from pathlib import Path

import pytest

from src.core.models import Asset, Character, Project, ShotStatus
from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.resources.models import (ResourceDocument, RESOURCES_SCHEMA_VERSION,
                                  document_revision)
from src.resources.store import ResourceStore
from src.resources.write_service import ResourceWriteService
from src.scene_system.models import (SCENE_SYSTEM_SCHEMA_VERSION,
                                     view_revision)
from src.scene_system.store import SceneSystemStore
from src.scene_system.service import SceneSystemService
from src.scene_system.visual_service import SceneVisualService
from src.visual_profile import (RENDER_FAMILIES, ProjectVisualProfile,
                                VisualProfileService, VisualProfileStore,
                                compile_project_style_instruction,
                                load_profile_or_none, visual_profile_fingerprint)
from src.visual_profile.models import (STYLE_COMPATIBILITY_CHANGED,
                                       STYLE_COMPATIBILITY_CURRENT,
                                       STYLE_COMPATIBILITY_UNKNOWN,
                                       VisualProfileSchemaError,
                                       style_compatibility_of_basis)
from src.visual_profile.compiler import compile_project_style_review_expectation


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolated_locks():
    _clear_for_test()
    yield
    _clear_for_test()


def _bare_project(tmp_path: Path, name: str = "p1") -> Path:
    """旧项目形态：只有 project.json，无 project_profile.json。"""
    pdir = tmp_path / name
    (pdir / "output").mkdir(parents=True, exist_ok=True)
    ProjectStore(pdir).save(Project(name=name))
    return pdir


# ============ T1/T2：旧项目 unspecified + 纯读零副作用 ============

def test_t1_old_project_read_unspecified(tmp_path):
    pdir = _bare_project(tmp_path)
    assert load_profile_or_none(pdir) is None
    r = VisualProfileService(pdir, pdir.name).read()
    assert r["specified"] is False and r["visual_profile"] is None
    assert r["revision"] == "" and r["has_approved_visual_assets"] is False


def test_t2_read_does_not_create_file(tmp_path):
    pdir = _bare_project(tmp_path)
    store = VisualProfileStore(pdir)
    for _ in range(3):
        store.load_or_none()
        VisualProfileService(pdir, pdir.name).read()
    assert not store.exists(), "纯读绝不能创建 project_profile.json"


# ============ T3-T6：OCC + 指纹 ============

def test_t3_create_profile_via_service_and_project_create(tmp_path):
    pdir = _bare_project(tmp_path)
    svc = VisualProfileService(pdir, pdir.name)
    r = svc.update(render_family="2d_animation",
                   art_direction="cinematic anime", expected_revision="")
    assert r["ok"] and r["revision"] and VisualProfileStore(pdir).exists()
    back = load_profile_or_none(pdir)
    assert back.render_family == "2d_animation"

    # 新项目随创建写入（ProjectManager 路径）
    from src.utils.project_manager import ProjectManager
    base = tmp_path / "projects_base"
    base.mkdir()
    pm = ProjectManager(str(base))
    pdir2 = pm.create_project("np", render_family="stylized",
                              art_direction="ink wash")
    prof = load_profile_or_none(pdir2)
    assert prof.render_family == "stylized" and prof.art_direction == "ink wash"
    # project.json 依旧先于 profile 语义存在
    assert (pdir2 / "output" / "project.json").exists()


def test_t4_t5_occ_update_and_stale_rejected(tmp_path):
    pdir = _bare_project(tmp_path)
    svc = VisualProfileService(pdir, pdir.name)
    r1 = svc.update(render_family="2d_animation", expected_revision="")
    assert r1["ok"]
    # stale → 拒绝，不 silent retry
    r2 = svc.update(render_family="photorealistic", expected_revision="")
    assert not r2["ok"] and r2["error"] == "stale_revision"
    assert load_profile_or_none(pdir).render_family == "2d_animation"
    # 正确 revision → 更新成功，created_at 保留（时间戳不入指纹）
    old = load_profile_or_none(pdir)
    r3 = svc.update(render_family="photorealistic", style_tags=["noir"],
                    expected_revision=r1["revision"])
    assert r3["ok"]
    new = load_profile_or_none(pdir)
    assert new.created_at == old.created_at


def test_t6_deterministic_fingerprint(tmp_path):
    a = visual_profile_fingerprint(ProjectVisualProfile(
        render_family="3d_animation", art_direction="pixar-like",
        style_tags=["soft", "warm"], negative_style_constraints=["grain"]))
    b = visual_profile_fingerprint(ProjectVisualProfile(
        render_family="3d_animation", art_direction="pixar-like",
        style_tags=["soft", "warm"], negative_style_constraints=["grain"]))
    assert a == b and len(a) == 8
    c = visual_profile_fingerprint(ProjectVisualProfile(
        render_family="3d_animation", art_direction="pixar-like",
        style_tags=["warm", "soft"], negative_style_constraints=["grain"]))
    assert c != a, "style_tags 顺序属于语义（确定性保留给定顺序）"


# ============ T8-T11：style compiler ============

def test_t8_photorealistic_compile_deterministic():
    p = ProjectVisualProfile(render_family="photorealistic")
    i1 = compile_project_style_instruction(p)
    i2 = compile_project_style_instruction(p)
    assert i1 == i2 and "photorealistic live-action" in i1
    assert "Do not render as 2D/3D animation" in i1


def test_t9_2d_animation_compile_deterministic():
    p = ProjectVisualProfile(render_family="2d_animation")
    i1 = compile_project_style_instruction(p)
    i2 = compile_project_style_instruction(p)
    assert i1 == i2 and "2D animated image" in i1
    assert "Do not render as live-action photography" in i1


def test_t10_custom_art_direction_compile():
    p = ProjectVisualProfile(
        render_family="2d_animation",
        art_direction="cinematic Japanese anime, clean cel shading, detailed environment",
        style_tags=["1990s ova"],
        negative_style_constraints=["photorealistic skin texture", "3d render"])
    inst = compile_project_style_instruction(p)
    assert "Art direction: cinematic Japanese anime" in inst
    assert "Project style tags: 1990s ova" in inst
    assert "Negative style constraints (must avoid): photorealistic skin texture; 3d render" in inst
    assert inst == compile_project_style_instruction(ProjectVisualProfile(
        render_family="2d_animation",
        art_direction="cinematic Japanese anime, clean cel shading, detailed environment",
        style_tags=["1990s ova"],
        negative_style_constraints=["photorealistic skin texture", "3d render"]))
    # unspecified → 不产生空尾巴
    assert compile_project_style_review_expectation(None) == ""


def test_t11_model_fields_never_in_profile():
    d = ProjectVisualProfile(render_family="custom").to_dict()
    for banned in ("provider", "model_id", "api", "seed", "candidate_count"):
        assert banned not in d, banned
    assert set(RENDER_FAMILIES) == {
        "photorealistic", "2d_animation", "3d_animation", "stylized", "custom"}


# ============ T12/T13/T17（Resources 侧）：basis 含 profile 指纹 + 变更 stale ============

class FakeAgnes:
    models = {"image": "agnes-image-test"}

    def __init__(self, images=(b"CAND",)):
        self.images = list(images)

    async def generate_image(self, prompt, **kw):
        return {"url": None, "b64": "QUJD"}


def _resources_env(tmp_path: Path, name: str = "pa"):
    """带 Alice 三视图 + profile 的项目 + WriteService。"""
    from tests.test_resources_phase1c1 import make_project, make_view_files
    pdir = make_project(tmp_path, with_character=True)
    make_view_files(pdir)
    ws = ResourceWriteService(pdir, pdir.name)
    return pdir, ws


def test_t12_look_generation_basis_contains_profile_fingerprint(tmp_path):
    from src.resources.generation import compute_generation_basis
    from tests.test_resources_phase1c1 import _seed_look
    pdir, ws = _resources_env(tmp_path)
    look_id, _cl = run(_seed_look({"pdir": pdir, "ws": ws}))
    doc = ws.store.load()
    project = ProjectStore(pdir).load()
    look = doc.looks[look_id]
    vp = load_profile_or_none(pdir)
    basis = compute_generation_basis(
        pdir, doc, project, look, model_id="m", size_tier="1K", ratio="2:3",
        seed=None, candidate_count=1, view_purpose="front", visual_profile=vp)
    assert basis["visual_profile_fingerprint"] == visual_profile_fingerprint(vp)
    assert basis["visual_profile"]["render_family"] == "photorealistic"
    assert basis["visual_profile"] not in (None, {})
    assert "Visual production style" in basis["prompt"]
    # 指纹覆盖 prompt（style 指令进了指纹）
    basis_noprofile = compute_generation_basis(
        pdir, doc, project, look, model_id="m", size_tier="1K", ratio="2:3",
        seed=None, candidate_count=1, view_purpose="front", visual_profile=None)
    assert basis_noprofile["basis_fingerprint"] != basis["basis_fingerprint"]
    assert "visual_profile_fingerprint" not in basis_noprofile   # legacy 诚实缺省


def test_t13_composition_basis_contains_profile_fingerprint(tmp_path):
    from src.resources.generation import compute_composition_basis
    from src.resources.models import (CharacterReferenceComposition,
                                      CompositionAnchor, CompositionPropBinding,
                                      CompositionRelation, new_composition_id,
                                      new_prop_id, Prop)
    pdir, ws = _resources_env(tmp_path)
    # prop + composition（直接构造 canonical，unit 级验证 basis）
    doc = ws.store.load()
    doc.props[new_prop_id()] = Prop(prop_id="prop_x000000001", name="Wooden Sword",
                                    description="a short wooden sword")
    doc.compositions[new_composition_id()] = CharacterReferenceComposition(
        composition_id=new_composition_id(), character_name="Alice", look_id="look_missing")
    doc.reference_assets["ref_seed0000001"] = __import__(
        "src.resources.models", fromlist=["ReferenceAsset"]).ReferenceAsset(
        asset_id="ref_seed0000001", url="https://x/hero")
    look = __import__("src.resources.models", fromlist=["CharacterLook"]).CharacterLook(
        look_id="look_seed00001", character_name="Alice")
    look.approved_reference_asset_ids = ["ref_seed0000001"]
    doc.looks[look.look_id] = look
    comp = doc.compositions[sorted(doc.compositions.keys())[0]]
    comp.look_id = look.look_id
    comp.prop_bindings = [CompositionPropBinding(
        prop_id="prop_x000000001", relation=CompositionRelation.HELD.value,
        anchor=CompositionAnchor.RIGHT_HAND.value)]
    ws.store.save(doc)
    doc = ws.store.load()
    project = ProjectStore(pdir).load()
    vp = load_profile_or_none(pdir)
    basis = compute_composition_basis(
        pdir, doc, project, comp, model_id="m", size_tier="1K", ratio="2:3",
        seed=None, candidate_count=1, visual_profile=vp)
    assert basis["visual_profile_fingerprint"] == visual_profile_fingerprint(vp)
    assert "Visual production style" in basis["prompt"]


def test_t17_profile_change_makes_new_generation_stale(tmp_path):
    from src.resources.generation import compute_generation_basis, \
        recompute_basis_still_holds
    from tests.test_resources_phase1c1 import _seed_look
    pdir, ws = _resources_env(tmp_path)
    look_id, _cl = run(_seed_look({"pdir": pdir, "ws": ws}))
    doc = ws.store.load()
    project = ProjectStore(pdir).load()
    vp = load_profile_or_none(pdir)
    basis = compute_generation_basis(
        pdir, doc, project, doc.looks[look_id], model_id="m", size_tier="1K",
        ratio="2:3", seed=None, candidate_count=1, view_purpose="front",
        visual_profile=vp)
    # profile 未变 → 成立
    ok, why = recompute_basis_still_holds(pdir, ws.store.load(),
                                          ProjectStore(pdir).load(), look_id, basis)
    assert ok, why
    # profile 变更 → 明确 stale 理由（不是笼统 basis_changed）
    svc = VisualProfileService(pdir, pdir.name)
    r = svc.update(render_family="2d_animation", expected_revision=svc.read()["revision"])
    assert r["ok"]
    ok, why = recompute_basis_still_holds(pdir, ws.store.load(),
                                          ProjectStore(pdir).load(), look_id, basis)
    assert not ok and why == "project_visual_profile_changed", (ok, why)


# ============ T14：Prop 生成不存在（审计结论）→ review 口径验证 ============

def test_t14_prop_has_no_generation_but_review_records_style_scope(tmp_path):
    from src.resources.prop_review import review_prop_reference
    pdir, ws = _resources_env(tmp_path, "pp")
    r = run(ws.create_prop(name="Wooden Sword", description="a short wooden sword",
                           expected_document_revision=document_revision(ws.store.load())))
    prop_id = r["target"]
    r2 = run(ws.register_reference_asset(
        local=None, expected_document_revision=document_revision(ws.store.load()),
        url="https://x/sword"))
    r3 = run(ws.attach_prop_reference(
        prop_id, r2["target"],
        expected_revision=ws.store.load().props[prop_id].revision
        if hasattr(ws.store.load().props[prop_id], "revision") else None)
        ) if False else None
    # attach 用 prop OCC（revision 由 prop_revision 派生）
    from src.resources.models import prop_revision
    cur = prop_revision(ws.store.load().props[prop_id])
    r3 = run(ws.attach_prop_reference(prop_id, r2["target"], expected_revision=cur))
    assert r3["ok"], r3

    class FakeV:
        async def chat_completion_vision(self, msgs, *, model):
            return {"content": json.dumps({
                "prop_fidelity": "pass", "fidelity_notes": "ok",
                "visual_clarity": "pass", "clarity_notes": "ok",
                "visual_defects": "pass", "defect_notes": "ok",
                "project_style_consistency": "concern", "style_notes": "borderline",
                "overall": "pass", "summary": "ok"}), "usage": {}}

    rv = run(review_prop_reference(pdir, prop_id, agnes_client=FakeV()))
    assert rv["ok"], rv
    cr = rv["candidate_reviews"][0]
    assert cr["project_style_consistency"] == "concern"
    assert cr["overall"] == "uncertain", "concern 不判 pass（fail closed）"
    rec = rv["review_record"]
    assert rec["project_style_consistency"] == "evaluated"
    assert rec["visual_profile_fingerprint"] == visual_profile_fingerprint(
        load_profile_or_none(pdir))


# ============ T15/T16/T17（Scene 侧）============

class FakeGen:
    models = {"image": "fake-image-model"}

    async def generate_image(self, prompt, *, image_refs=None, size_tier="1K",
                             ratio="16:9", seed=None, want_b64=False):
        return {"url": None, "b64": "AAAA"}


def _scene_env(tmp_path: Path, name: str = "sc"):
    from tests.test_scene_system_s2b_generation import make_pdir, _seed
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = run(_seed(svc, pdir, pdir.name))
    return pdir, svc, vs, ids


def test_t16_scene_instance_view_generation_basis_contains_profile(tmp_path):
    from src.scene_system.store import SceneSystemStore as SS
    pdir, svc, vs, ids = _scene_env(tmp_path)
    sciv = ids["sciv"]
    gen = run(vs.generate_scene_instance_view(
        sciv, expected_revision=view_revision(SS(pdir, pdir.name).load().scene_instance_views[sciv]),
        candidate_count=1, agnes_client=FakeGen(), include_peer=False))
    assert gen["ok"], gen.get("error")
    vp = load_profile_or_none(pdir)
    assert gen["basis"]["visual_profile_fingerprint"] == visual_profile_fingerprint(vp)
    assert "Visual production style" in gen["basis"]["prompt"]
    # committed record 同样携带
    from src.scene_system import generation_records as gr
    recs = [r for r in gr.read_records(pdir, generation_id=gen["generation_id"])
            if r.get("status") == "committed"]
    assert recs and recs[-1]["basis"]["visual_profile_fingerprint"]


def test_t17b_profile_change_scene_stale_attribution(tmp_path):
    """approved 后 profile 变更 → stale_reasons 含 project_visual_profile_changed；
    legacy basis（无指纹）在已有 profile 时 → generated_before_visual_profile。"""
    from src.scene_system.store import SceneSystemStore as SS
    from src.scene_system.instance_view_basis import compute_state_view_staleness
    from src.scene_system.visual_basis import load_resources_doc_or_none
    from tests.test_scene_system_s2b1 import simulate_approval
    pdir, svc, vs, ids = _scene_env(tmp_path)
    sciv = ids["sciv"]
    gen = run(vs.generate_scene_instance_view(
        sciv, expected_revision=view_revision(SS(pdir, pdir.name).load().scene_instance_views[sciv]),
        candidate_count=1, agnes_client=FakeGen(), include_peer=False))
    assert gen["ok"], gen.get("error")
    run(simulate_approval(pdir, sciv))
    doc = SS(pdir, pdir.name).load()
    inst = doc.scene_instances[doc.scene_instance_views[sciv].scene_instance_id]
    base_view = doc.views[doc.scene_instance_views[sciv].location_view_id]
    loc = doc.locations[inst.location_id]
    rdoc = load_resources_doc_or_none(pdir)
    gb = gen["basis"]
    st = compute_state_view_staleness(pdir, doc, rdoc, inst, loc, base_view,
                                      doc.scene_instance_views[sciv], gb)
    assert st["is_stale"] is False and st["stale_reasons"] == [], st
    # profile 变更
    svc_vp = VisualProfileService(pdir, pdir.name)
    r = svc_vp.update(render_family="3d_animation",
                      expected_revision=svc_vp.read()["revision"])
    assert r["ok"]
    doc2 = SS(pdir, pdir.name).load()
    st2 = compute_state_view_staleness(pdir, doc2, rdoc, inst, loc, base_view,
                                       doc2.scene_instance_views[sciv], gb)
    assert st2["is_stale"] is True
    assert "project_visual_profile_changed" in st2["stale_reasons"], st2
    # legacy basis（无指纹）→ generated_before_visual_profile（不伪 pass）
    legacy_gb = {k: v for k, v in gb.items()
                 if k not in ("visual_profile", "visual_profile_fingerprint")}
    st3 = compute_state_view_staleness(pdir, doc2, rdoc, inst, loc, base_view,
                                       doc2.scene_instance_views[sciv], legacy_gb)
    assert "generated_before_visual_profile" in st3["stale_reasons"], st3
    # 项目从未设置 profile + legacy basis → 不因此 stale（P1 §14）
    (pdir / "output" / "project_profile.json").unlink()
    st4 = compute_state_view_staleness(pdir, SS(pdir, pdir.name).load(), rdoc,
                                       inst, loc, base_view,
                                       SS(pdir, pdir.name).load().scene_instance_views[sciv],
                                       legacy_gb)
    assert "generated_before_visual_profile" not in st4["stale_reasons"], st4


# ============ T18：legacy generation → unknown（不伪造）============

def test_t18_legacy_generation_style_compatibility_unknown():
    cur_fp = visual_profile_fingerprint(ProjectVisualProfile(render_family="stylized"))
    # legacy basis：无指纹键
    comp = style_compatibility_of_basis({"prompt": "x"}, cur_fp)
    assert comp["style_compatibility"] == STYLE_COMPATIBILITY_UNKNOWN
    assert comp["generated_before_visual_profile"] is True
    assert comp["visual_profile_fingerprint"] is None
    # 指纹一致 → current
    assert style_compatibility_of_basis(
        {"visual_profile_fingerprint": cur_fp}, cur_fp)["style_compatibility"] \
        == STYLE_COMPATIBILITY_CURRENT
    # 不一致 → project_visual_profile_changed
    assert style_compatibility_of_basis(
        {"visual_profile_fingerprint": "deadbeef"}, cur_fp)["style_compatibility"] \
        == STYLE_COMPATIBILITY_CHANGED


def test_t18b_read_look_exposes_latest_generation_style(tmp_path):
    pdir, ws = _resources_env(tmp_path, "pl")
    svc = __import__("src.resources.service", fromlist=["ResourceService"]).ResourceService(
        pdir, pdir.name)
    looks = svc.list_looks()
    # 无 generation → None（诚实缺省）
    from src.resources.service import ResourceService
    r = ResourceService(pdir, pdir.name)
    # 手造一条 committed legacy record（无 profile 键）→ unknown
    from src.resources.generation_records import append_record
    append_record(pdir, {"generation_id": "gen_legacy", "operation": "look.generate_candidates",
                         "look_id": "look_none", "status": "committed",
                         "basis": {"prompt": "old"}, "candidate_asset_ids": []})
    comp = r._latest_generation_style(look_id="look_none")
    assert comp["style_compatibility"] == STYLE_COMPATIBILITY_UNKNOWN
    assert comp["generated_before_visual_profile"] is True


# ============ T19/T20：review 维度 + hard gate ============

def test_t19_t20_look_review_style_dimension_and_hard_gate(tmp_path):
    from src.resources.look_review import (review_look_candidates,
                                           has_pass_evidence, _DIMENSIONS)
    from src.resources.models import (ApprovalState, GenerationStatus,
                                      ReferenceAsset)
    pdir, ws = _resources_env(tmp_path, "pr")
    look_id = "look_style0001"
    doc = ws.store.load()
    lk = __import__("src.resources.models", fromlist=["CharacterLook"]).CharacterLook(
        look_id=look_id, character_name="Alice")
    doc.looks[look_id] = lk
    doc.reference_assets["ref_style000001"] = ReferenceAsset(
        asset_id="ref_style000001", url="https://x/cand")
    lk.reference_asset_ids = ["ref_style000001"]
    ws.store.save(doc)

    class FakeV:
        def __init__(self, style):
            self.style = style

        async def chat_completion_vision(self, msgs, *, model):
            content = json.dumps({
                "identity_consistency": "pass", "identity_notes": "ok",
                "costume_consistency": "pass", "costume_notes": "ok",
                "visual_defects": "pass", "defect_notes": "ok",
                "project_style_consistency": self.style, "style_notes": "m",
                "overall": "pass", "summary": "ok"})
            assert "project_style_consistency" in msgs[0]["content"][0]["text"], \
                "T19：vision prompt 必须包含项目风格期望"
            return {"content": content, "usage": {}}

    # T20：style fail → overall needs_fix → approve 门槛关闭
    rv = run(review_look_candidates(pdir, look_id, agnes_client=FakeV("fail")))
    assert rv["ok"]
    cr = rv["candidate_reviews"][0]
    assert cr["project_style_consistency"] == "fail"
    assert cr["overall"] == "needs_fix", "风格不符不得 pass（hard 维度）"
    assert has_pass_evidence(pdir, look_id, "ref_style000001") is False

    # style pass → overall pass → 证据成立
    rv2 = run(review_look_candidates(pdir, look_id, agnes_client=FakeV("pass")))
    cr2 = rv2["candidate_reviews"][0]
    assert cr2["project_style_consistency"] == "pass" and cr2["overall"] == "pass"
    assert has_pass_evidence(pdir, look_id, "ref_style000001") is True
    # record 口径字段
    rec = rv2["generation_record"]
    assert rec["project_style_consistency"] == "evaluated"


def test_t19b_unspecified_profile_review_has_no_style_dimension(tmp_path):
    """profile unspecified → 维度不参与（not_applicable），旧项目不被审死（P1 §7/§14）。"""
    from src.resources.look_review import review_look_candidates
    from src.resources.models import ReferenceAsset
    pdir = _bare_project(tmp_path, "pu")
    # make_project 会写 profile——这里手动清掉以模拟旧项目
    (pdir / "output" / "project_profile.json").unlink(missing_ok=True)
    ws = ResourceWriteService(pdir, pdir.name)
    look_id = "look_oldstyle01"
    doc = ws.store.load()
    doc.looks[look_id] = __import__(
        "src.resources.models", fromlist=["CharacterLook"]).CharacterLook(
        look_id=look_id, character_name="Alice")
    doc.reference_assets["ref_oldstyle001"] = ReferenceAsset(
        asset_id="ref_oldstyle001", url="https://x/c")
    doc.looks[look_id].reference_asset_ids = ["ref_oldstyle001"]
    ws.store.save(doc)

    class FakeV:
        async def chat_completion_vision(self, msgs, *, model):
            text = msgs[0]["content"][0]["text"]
            assert "project_style_consistency" not in text
            return {"content": json.dumps({
                "identity_consistency": "pass", "costume_consistency": "pass",
                "visual_defects": "pass", "overall": "pass", "summary": "ok"}),
                "usage": {}}

    rv = run(review_look_candidates(pdir, look_id, agnes_client=FakeV()))
    assert rv["ok"]
    cr = rv["candidate_reviews"][0]
    assert "project_style_consistency" not in cr
    assert cr["overall"] == "pass"
    assert rv["generation_record"]["project_style_consistency"] == "not_applicable"


# ============ fail closed：生成路径 ============

def test_fail_closed_look_generation_without_profile(tmp_path):
    from tests.test_resources_phase1c1 import _seed_look
    pdir, ws = _resources_env(tmp_path, "pf")
    (pdir / "output" / "project_profile.json").unlink()
    look_id, _cl = run(_seed_look({"pdir": pdir, "ws": ws}))
    from src.resources.models import look_revision
    r = run(ws.generate_look_candidates(
        look_id, expected_revision=look_revision(ws.store.load().looks[look_id]),
        candidate_count=1, agnes_client=FakeAgnes()))
    assert not r["ok"] and r["error"] == "project_visual_profile_required", r
    # 不写 generating 状态、不伪造候选
    doc2 = ws.store.load()
    assert doc2.looks[look_id].generation_status == "none"


def test_fail_closed_scene_generation_without_profile(tmp_path):
    from tests.test_scene_system_s2b_generation import make_pdir, _seed
    pdir = make_pdir(tmp_path)
    (pdir / "output" / "project_profile.json").unlink()
    svc = SceneSystemService(pdir, pdir.name)
    vs = SceneVisualService(pdir, pdir.name)
    ids = run(_seed(svc, pdir, pdir.name))
    sciv = ids["sciv"]
    r = run(vs.generate_scene_instance_view(
        sciv, expected_revision=view_revision(
            SceneSystemStore(pdir, pdir.name).load().scene_instance_views[sciv]),
        candidate_count=1, agnes_client=FakeGen(), include_peer=False))
    assert not r["ok"] and r["error"] == "project_visual_profile_required", r
    v = SceneSystemStore(pdir, pdir.name).load().scene_instance_views[sciv]
    assert v.generation_status == "none"


def test_fail_closed_corrupt_profile_honest_error(tmp_path):
    pdir = _bare_project(tmp_path, "pc")
    (pdir / "output" / "project_profile.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(VisualProfileSchemaError):
        load_profile_or_none(pdir)
    # read 端点诚实上报 500（不静默当 unspecified）——service 层向上抛
    with pytest.raises(VisualProfileSchemaError):
        VisualProfileService(pdir, pdir.name).read()


# ============ HTTP 契约 ============

def test_http_visual_profile_round_trip(client, tmp_path):
    """GET unspecified → PUT 创建 → GET specified → PUT stale 409 → PUT 非法 400。"""
    from deps import projects_dir
    from pathlib import Path as P
    name = "_pvp_http"
    d = P(projects_dir) / name
    import shutil
    shutil.rmtree(d, ignore_errors=True)
    try:
        r = client.post("/api/projects/create",
                        params={"name": name, "render_family": "2d_animation",
                                "art_direction": "cel shading"})
        assert r.status_code == 200, r.text
        # GET specified
        g = client.get(f"/api/project/{name}/visual-profile")
        assert g.status_code == 200
        j = g.json()
        assert j["specified"] is True
        assert j["visual_profile"]["render_family"] == "2d_animation"
        rev = j["revision"]
        # OCC 正确更新
        u = client.put(f"/api/project/{name}/visual-profile", json={
            "render_family": "3d_animation", "expected_revision": rev})
        assert u.status_code == 200, u.text
        rev2 = u.json()["revision"]
        assert rev2 != rev
        # stale → 409
        u2 = client.put(f"/api/project/{name}/visual-profile", json={
            "render_family": "stylized", "expected_revision": rev})
        assert u2.status_code == 409
        # 非法 render_family → 400
        u3 = client.put(f"/api/project/{name}/visual-profile", json={
            "render_family": "watercolor", "expected_revision": rev2})
        assert u3.status_code == 400
        # 旧项目（无 profile）GET → unspecified（真机目录里挑一个临时旧项目）
        old = P(projects_dir) / "_pvp_old"
        shutil.rmtree(old, ignore_errors=True)
        (old / "output").mkdir(parents=True)
        try:
            g2 = client.get("/api/project/_pvp_old/visual-profile")
            assert g2.status_code == 200
            assert g2.json()["specified"] is False
            assert not (old / "output" / "project_profile.json").exists(), "GET 不得创建文件"
        finally:
            shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ============ T21-T23：冻结面不变 ============

def test_t21_project_json_bytes_unchanged_by_profile_update(tmp_path):
    pdir = _bare_project(tmp_path, "tj")
    pj = pdir / "output" / "project.json"
    before = pj.read_bytes()
    svc = VisualProfileService(pdir, pdir.name)
    assert svc.update(render_family="stylized", expected_revision="")["ok"]
    assert svc.update(render_family="2d_animation",
                      expected_revision=svc.read()["revision"])["ok"]
    assert pj.read_bytes() == before, "profile 写入不得触碰 project.json"


def test_t22_resources_schema_unchanged(tmp_path):
    assert RESOURCES_SCHEMA_VERSION == 2, "Resources schema 不因 P1 bump"
    pdir, ws = _resources_env(tmp_path, "tr")
    r = run(ws.create_prop(name="S", expected_document_revision=document_revision(ws.store.load())))
    raw = json.loads((pdir / "output" / "resources.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2
    assert "visual_profile" not in raw, "profile 不进 resources.json（独立 canonical）"


def test_t23_scene_system_schema_unchanged(tmp_path):
    assert SCENE_SYSTEM_SCHEMA_VERSION == 3, "scene_system schema 不因 P1 bump"
    from tests.test_scene_system_s2b_generation import make_pdir, _seed
    pdir = make_pdir(tmp_path)
    svc = SceneSystemService(pdir, pdir.name)
    run(_seed(svc, pdir, pdir.name))
    raw = json.loads((pdir / "output" / "scene_system.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 3
    assert "visual_profile" not in raw, "profile snapshot 进 generation/review records，不进 Scene canonical"
