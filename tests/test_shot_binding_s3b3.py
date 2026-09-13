# tests/test_shot_binding_s3b3.py
# Shot Production Binding V1 — Phase S3B.3 验收测试（10.md §45-§65 测试组 A-R/S/T + 集成）。
#
# 核心验收（§53 硬规则）：pick_up 动作下"已持物 Composition"绝不能当 start-state
# primary——必须分类为 end-state evidence；Look 才是 start character evidence。
# 全程零外部模型调用（§66）；Pack derived/ephemeral/read-only（§35/§36）。
import hashlib
import json
import sys
from pathlib import Path

import pytest

from src.core.models import Asset, Character, Project, Scene, Shot
from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.resources.models import (
    CharacterLook,
    CharacterReferenceComposition,
    CompositionPropBinding,
    Prop as ResourceProp,
    ReferenceAsset,
)
from src.resources.store import ResourceStore, legacy_character_references
from src.shot_binding.models import PropBinding
from src.scene_system.store import SceneSystemStore
from src.scene_system.service import SceneSystemService
from src.shot_binding import ShotBindingService, ShotBindingStore
from src.shot_binding.validity import NOT_READY, READY, UNBOUND
from src.visual_profile import VisualProfileService

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))
from test_shot_binding_s3b1 import (  # noqa: E402
    COMP, LOOK, LOOK_BOB, PROP, _gather, make_pdir, run, valid_payload,
)
from test_shot_binding_s3b2 import (  # noqa: E402
    _bind, _build_scene, _make_approved_siv, _regen_and_approve_siv,
)

PISTOL = "prop_pistol000001"
COMP_PISTOL = "comp_pistol000001"
COMP_BOB_PISTOL = "comp_bob_pistol01"
COMP_BOB_SWORD = "comp_bob_sword01"
COMP_CHARLIE_P = "comp_charlie_pi1"


def _sha(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def _hash_all(pdir: Path) -> dict:
    return {n: _sha(pdir / "output" / n) for n in
            ("project.json", "resources.json", "scene_system.json",
             "project_profile.json", "shot_production_bindings.json")}


def _give_plate(pdir: Path, comp_id: str, ref_id: str):
    """给 composition 一张可解析 approved plate（pack 编译要求）。"""
    rd = ResourceStore(pdir, pdir.name).load()
    rd.reference_assets[ref_id] = ReferenceAsset(
        asset_id=ref_id, url=f"http://example/{ref_id}.png")
    rd.compositions[comp_id].approved_reference_asset_ids = [ref_id]
    rd.compositions[comp_id].cover_asset_id = ref_id
    ResourceStore(pdir, pdir.name).save(rd)


def _add_pistol_with_comp(pdir: Path, comp_id: str, char: str, look: str):
    rd = ResourceStore(pdir, pdir.name).load()
    rd.reference_assets["ref_pistol00001"] = ReferenceAsset(
        asset_id="ref_pistol00001", url="http://example/pistol.png")
    rd.props[PISTOL] = ResourceProp(
        prop_id=PISTOL, approval_status="approved",
        approved_reference_asset_ids=["ref_pistol00001"])
    rd.compositions[comp_id] = CharacterReferenceComposition(
        composition_id=comp_id, character_name=char, look_id=look,
        prop_bindings=[CompositionPropBinding(prop_id=PISTOL)],
        approval_status="approved")
    rd.reference_assets[f"ref_{comp_id}"] = ReferenceAsset(
        asset_id=f"ref_{comp_id}", url=f"http://example/{comp_id}.png")
    rd.compositions[comp_id].approved_reference_asset_ids = [f"ref_{comp_id}"]
    rd.compositions[comp_id].cover_asset_id = f"ref_{comp_id}"
    ResourceStore(pdir, pdir.name).save(rd)


def _add_bob_comp_sword(pdir: Path, comp_id: str):
    rd = ResourceStore(pdir, pdir.name).load()
    rd.compositions[comp_id] = CharacterReferenceComposition(
        composition_id=comp_id, character_name="Bob", look_id="look_bob00000001",
        prop_bindings=[CompositionPropBinding(prop_id=PROP)],
        approval_status="approved")
    rd.reference_assets[f"ref_{comp_id}"] = ReferenceAsset(
        asset_id=f"ref_{comp_id}", url=f"http://example/{comp_id}.png")
    rd.compositions[comp_id].approved_reference_asset_ids = [f"ref_{comp_id}"]
    rd.compositions[comp_id].cover_asset_id = f"ref_{comp_id}"
    ResourceStore(pdir, pdir.name).save(rd)


def _add_charlie(pdir: Path, comp_id: str):
    proj = ProjectStore(pdir).load()
    proj.characters.append(Character(name="Charlie", description_en="scientist"))
    ProjectStore(pdir).save(proj)
    rd = ResourceStore(pdir, pdir.name).load()
    rd.reference_assets["ref_charlie0001"] = ReferenceAsset(
        asset_id="ref_charlie0001", url="http://example/charlie.png")
    rd.looks["look_charlie001"] = CharacterLook(
        look_id="look_charlie001", character_name="Charlie",
        approval_status="approved", approved_reference_asset_ids=["ref_charlie0001"])
    rd.compositions[comp_id] = CharacterReferenceComposition(
        composition_id=comp_id, character_name="Charlie", look_id="look_charlie001",
        prop_bindings=[CompositionPropBinding(prop_id=PISTOL)],
        approval_status="approved")
    rd.reference_assets[f"ref_{comp_id}"] = ReferenceAsset(
        asset_id=f"ref_{comp_id}", url=f"http://example/{comp_id}.png")
    rd.compositions[comp_id].approved_reference_asset_ids = [f"ref_{comp_id}"]
    rd.compositions[comp_id].cover_asset_id = f"ref_{comp_id}"
    ResourceStore(pdir, pdir.name).save(rd)


def _add_shot(pdir: Path, shot_id: str, cast: list):
    proj = ProjectStore(pdir).load()
    s = Shot(shot_id=shot_id, scene_id="s01", index=len(proj.scenes[0].shots) + 1,
             characters=cast)
    proj.scenes[0].shots.append(s)
    ProjectStore(pdir).save(proj)


@pytest.fixture()
def env(tmp_path):
    """真实跨域夹具（零外部模型）+ 默认绑定：s01_c01 → (inst, view_a) unpinned。"""
    _clear_for_test()
    pdir = make_pdir(tmp_path)
    (pdir / "output" / "scene_system.json").unlink()
    ids = run(_build_scene(pdir))
    svc = ShotBindingService(pdir)
    yield pdir, ids, svc
    _clear_for_test()


def _bind_default(svc, ids, **kw):
    from test_shot_binding_s3b2 import _bind
    return _bind(svc, inst=ids["inst"], view=ids["view_a"], **kw)


def _ref(p, **filters):
    out = [r for r in p["references"]
           if all(r.get(k) == v for k, v in filters.items())]
    return out


# ============ A. Compile Gate（§45）============

def test_a1_unbound_blocked(env):
    pdir, ids, svc = env
    r = svc.compile_reference_pack("s01_c01")
    assert r["status"] == "BLOCKED" and r["binding_readiness"] == UNBOUND
    assert r["issues"][0]["reason"] == "binding_unbound" and r["pack"] is None


def test_a2_a3_not_ready_blocked_issues_forwarded(env):
    pdir, ids, svc = env
    _bind_default(svc, ids)
    rd = ResourceStore(pdir, pdir.name).load()
    rd.looks[LOOK].approved_reference_asset_ids = []
    ResourceStore(pdir, pdir.name).save(rd)
    r = svc.compile_reference_pack("s01_c01")
    assert r["status"] == "BLOCKED" and r["pack"] is None
    reasons = {i["reason"] for i in r["issues"]}
    assert "look_not_approved" in reasons, "S3B.2 结构化 issues 必须原样转发"


def test_a4_ready_compiled(env):
    pdir, ids, svc = env
    _bind_default(svc, ids)
    r = svc.compile_reference_pack("s01_c01")
    assert r["status"] == "COMPILED" and r["pack"] is not None
    assert r["pack"]["shot_id"] == "s01_c01"


def test_a5_a6_compile_never_refreshes_or_revises(env):
    pdir, ids, svc = env
    r = _bind_default(svc, ids)
    before = svc.read_binding("s01_c01")
    svc.compile_reference_pack("s01_c01")
    after = svc.read_binding("s01_c01")
    assert before == after, "compile 不得改 binding revision / fingerprint / updated_at"


# ============ B. Read-only（§46）============

def test_b1_b4_compile_read_only_repeated(env):
    pdir, ids, svc = env
    _bind_default(svc, ids)
    h0 = _hash_all(pdir)
    p1 = svc.compile_reference_pack("s01_c01")
    p2 = svc.compile_reference_pack("s01_c01")
    h1 = _hash_all(pdir)
    assert p1["status"] == "COMPILED" and p2["status"] == "COMPILED"
    assert h0 == h1, "compile 前后五个 canonical 文件字节不变（含重复 compile）"


def test_b2_b3_no_pack_store(env):
    pdir, ids, svc = env
    _bind_default(svc, ids)
    svc.compile_reference_pack("s01_c01")
    assert not (pdir / "output" / "shot_reference_packs.json").exists()
    assert not (pdir / "output" / "reference_pack").exists()
    out_new = [p.name for p in (pdir / "output").iterdir()
               if "pack" in p.name.lower()]
    assert out_new == [], "禁止出现任何 pack store 文件/目录"


# ============ C. Determinism（§47）============

def test_c1_c4_deterministic(env):
    pdir, ids, svc = env
    _bind_default(svc, ids)
    p1 = svc.compile_reference_pack("s01_c01")["pack"]
    p2 = svc.compile_reference_pack("s01_c01")["pack"]
    assert p1 == p2, "同 canonical 连续 compile 结果一致"
    s1 = json.dumps(p1, sort_keys=True, ensure_ascii=False)
    s2 = json.dumps(p2, sort_keys=True, ensure_ascii=False)
    assert s1 == s2
    assert "compiled_at" not in s1 and "pack_id" not in s1, "无时钟/无随机 ID"


# ============ D. Scene（§48）============

def test_d1_d3_d5_unpinned_resolved_siv_in_pack(env):
    pdir, ids, svc = env
    _bind_default(svc, ids)
    p = svc.compile_reference_pack("s01_c01")["pack"]
    assert p["scene"]["scene_instance_view_id"] == ids["sciv_a"]
    scene_refs = _ref(p, source_type="scene_instance_view")
    assert len(scene_refs) == 1
    assert scene_refs[0]["source_id"] == ids["sciv_a"]
    assert scene_refs[0]["purpose"] == "scene_context"
    assert scene_refs[0]["priority"] == "primary"
    assert scene_refs[0]["temporal_scope"] == "throughout"
    st = SceneSystemStore(pdir, pdir.name).load()
    assert scene_refs[0]["reference_asset_id"] == \
        st.scene_instance_views[ids["sciv_a"]].reference_asset_id, "D5 正确 SIV 来源"


def test_d2_pinned_exact_siv(env):
    pdir, ids, svc = env
    _bind_default(svc, ids, pinned=ids["sciv_a"])
    p = svc.compile_reference_pack("s01_c01")["pack"]
    assert p["scene"]["scene_instance_view_id"] == ids["sciv_a"]


def test_d4_compile_never_pins(env):
    pdir, ids, svc = env
    _bind_default(svc, ids)
    svc.compile_reference_pack("s01_c01")
    doc = ShotBindingStore(pdir).load()
    assert doc.bindings["s01_c01"].scene_binding.pinned_scene_instance_view_id is None


def test_d6_d7_view_relevant_projection_reused(env):
    """§48 D6/D7：投影来自 Scene System（消费不重实现）；不可见状态变化不改变
    Pack 的 scene 语义。🔴 patch anchor_states 整段替换——保留 door 态。"""
    pdir, ids, svc = env
    _bind_default(svc, ids)
    p1 = svc.compile_reference_pack("s01_c01")["pack"]
    ssvc = SceneSystemService(pdir, pdir.name)
    rp = run(ssvc.patch_scene_state(
        ids["inst"],
        {"anchor_states": {ids["a_door"]: {"condition": "open"},
                           ids["a_win"]: {"condition": "broken"}}},
        expected_revision=ssvc.read_scene_instance(ids["inst"])["revision"]))
    assert rp["ok"]
    p2 = svc.compile_reference_pack("s01_c01")["pack"]
    assert p1["scene"]["view_relevant_scene_state"] == \
        p2["scene"]["view_relevant_scene_state"], "不可见变化不进 view A 投影"


# ============ E. Visual Profile（§49）============

def test_e1_e2_profile_semantics_and_fingerprint(env):
    pdir, ids, svc = env
    _bind_default(svc, ids)
    p = svc.compile_reference_pack("s01_c01")["pack"]
    assert p["visual_profile"]["render_family"] == "2d_animation"
    fp = p["basis"]["visual_profile_fingerprint"]
    assert fp and p["visual_profile"]["visual_profile_fingerprint"] == fp
    assert p["basis"]["binding_revision"] == \
        svc.read_binding("s01_c01")["binding_revision"]


def test_e3_no_prompt_output(env):
    pdir, ids, svc = env
    _bind_default(svc, ids)
    s = json.dumps(svc.compile_reference_pack("s01_c01")["pack"], ensure_ascii=False)
    for word in ("positive_prompt", "negative_prompt", "prompt_fragment",
                 "style_prompt", "character_prompt", "scene_prompt"):
        assert word not in s, f"Pack 不产生 prompt：{word}"


def test_e4_no_styleanchor_fallback(env):
    pdir, ids, svc = env
    _bind_default(svc, ids)
    s = json.dumps(svc.compile_reference_pack("s01_c01")["pack"], ensure_ascii=False)
    for word in ("art_style", "size_tier", "palette"):
        assert f'"{word}"' not in s, "不回落 legacy StyleAnchor"


def test_e5_profile_unspecified_blocked(env):
    pdir, ids, svc = env
    _bind_default(svc, ids)
    (pdir / "output" / "project_profile.json").unlink()
    r = svc.compile_reference_pack("s01_c01")
    assert r["status"] == "BLOCKED"
    assert "project_visual_profile_unspecified" in {i["reason"] for i in r["issues"]}


# ============ F. Character / Look（§50）============

def test_f1_f3_f4_look_primary_only_cast(env):
    pdir, ids, svc = env
    _bind_default(svc, ids)
    p = svc.compile_reference_pack("s01_c01")["pack"]
    assert [c["character_name"] for c in p["characters"]] == ["Alice"], "F3 只投影 cast"
    look_refs = _ref(p, source_type="character_look", subject_character="Alice")
    assert look_refs and look_refs[0]["priority"] == "primary", "F1 Look=primary"
    assert look_refs[0]["purpose"] == "character_appearance"
    assert "Bob" not in json.dumps(p), "F4 Bob 不进入 Alice 的 Pack"
    assert p["characters"][0]["look_id"] == LOOK


def test_f2_identity_refs_supporting(env):
    pdir, ids, svc = env
    proj = ProjectStore(pdir).load()
    proj.get_character("Alice").views["front"] = Asset(
        local=None, url="http://example/alice_front.png", status="done")
    ProjectStore(pdir).save(proj)
    _bind_default(svc, ids)
    p = svc.compile_reference_pack("s01_c01")["pack"]
    ident = _ref(p, source_type="character_identity", subject_character="Alice")
    assert ident and ident[0]["priority"] == "supporting"
    assert ident[0]["reference_asset_id"] == "legacy:Alice:front"


# ============ G. Composition（§51）============

def test_g1_g2_static_composition_primary_with_metadata(env):
    pdir, ids, svc = env
    _give_plate(pdir, COMP, "ref_comp_plate1")
    _bind_default(svc, ids, composition=True)
    p = svc.compile_reference_pack("s01_c01")["pack"]
    comp_refs = _ref(p, source_type="composition", subject_character="Alice")
    assert comp_refs and comp_refs[0]["priority"] == "primary"
    assert comp_refs[0]["purpose"] == "spatial_relation"
    assert comp_refs[0]["temporal_scope"] == "static"
    entry = p["characters"][0]
    assert entry["composition_id"] == COMP
    assert entry["covered_props"] == [
        {"prop_id": PROP, "relation": "held", "anchor": "right_hand",
         "visual_state_note": ""}], "G2 组合元数据正确关联"


def test_g3_standalone_prop_identity_no_relation_copy(env):
    """§19：Composition owns relation；standalone prop ref 只做 identity supporting。"""
    pdir, ids, svc = env
    _give_plate(pdir, COMP, "ref_comp_plate1")
    _add_pistol_with_comp(pdir, COMP_PISTOL, "Alice", LOOK)
    r = _bind_default(svc, ids, composition=True)
    # 动作 pick_up pistol（pistol 由 composition 覆盖 → 无 direct binding）
    rr = svc.replace_binding("s01_c01", {
        "shot_id": "s01_c01", "scene_id": "s01",
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"]},
        "character_bindings": [{"character_name": "Alice", "look_id": LOOK,
                                "composition_id": COMP_PISTOL}],
        "prop_bindings": [],
        "action": {"kind": "pick_up", "subject_character": "Alice",
                   "prop_id": PISTOL, "start_state": "empty hand",
                   "end_state": "holding pistol"},
    }, expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr
    p = svc.compile_reference_pack("s01_c01")["pack"]
    prop_refs = _ref(p, source_type="prop", prop_id=PISTOL)
    assert prop_refs, "standalone prop identity 进入 pack"
    assert prop_refs[0]["purpose"] == "prop_identity"
    assert prop_refs[0]["priority"] == "supporting"
    assert prop_refs[0]["relation"] is None and prop_refs[0]["anchor"] is None, \
        "G3 standalone ref 不复制关系真相"


def test_g4_conflict_blocked(env):
    """§18：compile gate 转发 validity 的 binding_conflict。
    制造方式（与 s3b2-e25 同构）：先合法绑定（composition 只覆盖 sword），再从
    Resources 侧给 composition 追加 lamp 覆盖 + store 态补 direct lamp binding
    （模拟"绑定之后 Resources 侧新增覆盖"）→ validity 报 binding_conflict →
    compile BLOCKED 并原样转发。"""
    pdir, ids, svc = env
    _give_plate(pdir, COMP, "ref_comp_plate1")
    _bind_default(svc, ids, composition=True)
    assert svc.compile_reference_pack("s01_c01")["status"] == "COMPILED"
    _add_lamp(pdir)
    rd = ResourceStore(pdir, pdir.name).load()
    rd.compositions[COMP].prop_bindings.append(
        CompositionPropBinding(prop_id="prop_lamp0000001"))
    ResourceStore(pdir, pdir.name).save(rd)
    from src.shot_binding.models import PropBinding as PB
    doc = ShotBindingStore(pdir).load()
    doc.bindings["s01_c01"].prop_bindings.append(
        PB(prop_id="prop_lamp0000001", relation="near", anchor="beside_character"))
    ShotBindingStore(pdir).save(doc)
    r = svc.compile_reference_pack("s01_c01")
    assert r["status"] == "BLOCKED"
    assert "binding_conflict" in {i["reason"] for i in r["issues"]}


# ============ H. Direct Prop（§52）============

def _add_lamp(pdir):
    rd = ResourceStore(pdir, pdir.name).load()
    rd.props["prop_lamp0000001"] = ResourceProp(
        prop_id="prop_lamp0000001", approval_status="approved",
        approved_reference_asset_ids=["ref_sword000001"])
    ResourceStore(pdir, pdir.name).save(rd)


def test_h1_h2_direct_prop_identity_and_vocabulary(env):
    pdir, ids, svc = env
    _add_lamp(pdir)
    _bind_default(svc, ids, props=[{"prop_id": "prop_lamp0000001",
                                    "relation": "near",
                                    "anchor": "beside_character",
                                    "visual_state_note": "on the desk"}])
    p = svc.compile_reference_pack("s01_c01")["pack"]
    assert len(p["props"]) == 1
    entry = p["props"][0]
    assert entry["relation"] == "near" and entry["anchor"] == "beside_character"
    assert entry["subject_character"] == "Alice", "单角色确定性解析（Rule 6）"
    assert entry["reference_asset_ids"], "H1 identity refs 进入 pack"
    refs = _ref(p, source_type="prop", prop_id="prop_lamp0000001")
    assert refs[0]["priority"] == "primary"


def test_h3_action_subject_resolves(env):
    pdir, ids, svc = env
    _add_lamp(pdir)
    _bind_default(svc, ids, props=[{"prop_id": "prop_lamp0000001",
                                    "relation": "near",
                                    "anchor": "beside_character"}],
                  action={"kind": "pick_up", "subject_character": "Alice",
                          "prop_id": "prop_lamp0000001",
                          "start_state": "empty", "end_state": "holding"})
    p = svc.compile_reference_pack("s01_c01")["pack"]
    assert p["props"][0]["subject_character"] == "Alice", "Rule 1 action subject"


def test_h4_h5_multi_cast_ambiguous_never_guess(env):
    pdir, ids, svc = env
    _add_shot(pdir, "s01_c03", ["Alice", "Bob"])
    _add_lamp(pdir)
    _bind(svc, shot_id="s01_c03", inst=ids["inst"], view=ids["view_a"],
          characters=[{"character_name": "Alice", "look_id": LOOK},
                      {"character_name": "Bob", "look_id": LOOK_BOB}],
          props=[{"prop_id": "prop_lamp0000001", "relation": "held",
                  "anchor": "right_hand"}])
    r = svc.compile_reference_pack("s01_c03")
    assert r["status"] == "BLOCKED"
    issue = [i for i in r["issues"] if i["reason"] == "prop_subject_ambiguous"]
    assert issue, "H4 多角色无 subject → fail closed"
    assert issue[0]["detail"].get("cast") == ["Alice", "Bob"], "H5 不猜 holder"


def test_h6_zero_cast_prop_fail_closed(env):
    """§17 Rule 3/§75：词表全为 character-local，空 cast 无法表达 scene/world 放置。"""
    pdir, ids, svc = env
    _add_shot(pdir, "s01_c04", [])
    _add_lamp(pdir)
    from test_shot_binding_s3b2 import _bind
    _bind(svc, shot_id="s01_c04", inst=ids["inst"], view=ids["view_a"],
          characters=[], props=[{"prop_id": "prop_lamp0000001",
                                 "relation": "near",
                                 "anchor": "beside_character"}])
    r = svc.compile_reference_pack("s01_c04")
    assert r["status"] == "BLOCKED"
    assert "prop_subject_ambiguous" in {i["reason"] for i in r["issues"]}


# ============ I/J. pick_up / put_down（§53/§54 核心验收）============

def _bind_pick_up(svc, ids):
    r = _bind_default(svc, ids, characters=[
        {"character_name": "Alice", "look_id": LOOK,
         "composition_id": COMP_PISTOL}])
    rr = svc.replace_binding("s01_c01", {
        "shot_id": "s01_c01", "scene_id": "s01",
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"]},
        "character_bindings": [{"character_name": "Alice", "look_id": LOOK,
                                "composition_id": COMP_PISTOL}],
        "prop_bindings": [],
        "action": {"kind": "pick_up", "subject_character": "Alice",
                   "prop_id": PISTOL, "start_state": "empty hand",
                   "end_state": "holding pistol"},
    }, expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr
    return rr


_pdir_holder = [None]


def test_i1_pick_up_composition_is_end_not_start(env):
    """§53 核心硬测试：pick_up 时持枪组合绝不能是 start primary。"""
    pdir, ids, svc = env
    _pdir_holder[0] = pdir
    _add_pistol_with_comp(pdir, COMP_PISTOL, "Alice", LOOK)
    _bind_pick_up(svc, ids)
    p = svc.compile_reference_pack("s01_c01")["pack"]
    comp_refs = _ref(p, source_type="composition", subject_character="Alice")
    assert len(comp_refs) == 1
    assert comp_refs[0]["temporal_scope"] == "end", "组合=end-state evidence（硬规则）"
    assert comp_refs[0]["priority"] == "supporting"
    assert comp_refs[0]["purpose"] == "action_state"
    look_refs = _ref(p, source_type="character_look", subject_character="Alice")
    hero = [x for x in look_refs if x["semantic_note"] == "approved hero"]
    assert hero and hero[0]["temporal_scope"] == "start", "Look=start character evidence"
    assert hero[0]["priority"] == "primary"
    prop_refs = _ref(p, source_type="prop", prop_id=PISTOL)
    assert prop_refs and prop_refs[0]["purpose"] == "prop_identity"
    assert prop_refs[0]["priority"] == "supporting"


def test_j1_put_down_composition_is_start(env):
    """§54：put_down 时持物组合是 start-state evidence，不是 end。"""
    pdir, ids, svc = env
    _add_pistol_with_comp(pdir, COMP_PISTOL, "Alice", LOOK)
    r = _bind_default(svc, ids, characters=[
        {"character_name": "Alice", "look_id": LOOK,
         "composition_id": COMP_PISTOL}])
    rr = svc.replace_binding("s01_c01", {
        "shot_id": "s01_c01", "scene_id": "s01",
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"]},
        "character_bindings": [{"character_name": "Alice", "look_id": LOOK,
                                "composition_id": COMP_PISTOL}],
        "prop_bindings": [],
        "action": {"kind": "put_down", "subject_character": "Alice",
                   "prop_id": PISTOL, "start_state": "holding pistol",
                   "end_state": "empty hand"},
    }, expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr
    p = svc.compile_reference_pack("s01_c01")["pack"]
    comp_refs = _ref(p, source_type="composition", subject_character="Alice")
    assert comp_refs[0]["temporal_scope"] == "start"
    assert comp_refs[0]["priority"] == "primary"
    look_refs = _ref(p, source_type="character_look", subject_character="Alice")
    hero = [x for x in look_refs if x["semantic_note"] == "approved hero"]
    assert hero[0]["temporal_scope"] == "end"
    assert hero[0]["priority"] == "supporting"


# ============ K. hand_off（§55）============

def test_k1_hand_off_unique_mapping_classified(env):
    pdir, ids, svc = env
    _pdir_holder[0] = pdir
    _add_pistol_with_comp(pdir, COMP_PISTOL, "Alice", LOOK)
    _add_pistol_with_comp(pdir, COMP_BOB_PISTOL, "Bob", LOOK_BOB)
    _add_shot(pdir, "s01_c05", ["Alice", "Bob"])
    from test_shot_binding_s3b2 import _bind
    r = _bind(svc, shot_id="s01_c05", inst=ids["inst"], view=ids["view_a"],
              characters=[{"character_name": "Alice", "look_id": LOOK,
                           "composition_id": COMP_PISTOL},
                          {"character_name": "Bob", "look_id": LOOK_BOB,
                           "composition_id": COMP_BOB_PISTOL}])
    rr = svc.replace_binding("s01_c05", {
        "shot_id": "s01_c05", "scene_id": "s01",
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"]},
        "character_bindings": [
            {"character_name": "Alice", "look_id": LOOK,
             "composition_id": COMP_PISTOL},
            {"character_name": "Bob", "look_id": LOOK_BOB,
             "composition_id": COMP_BOB_PISTOL}],
        "prop_bindings": [],
        "action": {"kind": "hand_off", "subject_character": "Alice",
                   "prop_id": PISTOL, "start_state": "held by Alice",
                   "end_state": "held by Bob"},
    }, expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr
    result = svc.compile_reference_pack("s01_c05")
    assert result["status"] == "COMPILED", result["issues"]
    p = result["pack"]
    a_refs = _ref(p, source_type="composition", subject_character="Alice")
    b_refs = _ref(p, source_type="composition", subject_character="Bob")
    assert a_refs[0]["temporal_scope"] == "start" and a_refs[0]["priority"] == "primary"
    assert b_refs[0]["temporal_scope"] == "end" and b_refs[0]["priority"] == "supporting"


def test_k2_hand_off_no_recipient_blocked(env):
    pdir, ids, svc = env
    _pdir_holder[0] = pdir
    _add_pistol_with_comp(pdir, COMP_PISTOL, "Alice", LOOK)
    _add_shot(pdir, "s01_c05", ["Alice", "Bob"])
    from test_shot_binding_s3b2 import _bind
    r = _bind(svc, shot_id="s01_c05", inst=ids["inst"], view=ids["view_a"],
              characters=[{"character_name": "Alice", "look_id": LOOK,
                           "composition_id": COMP_PISTOL},
                          {"character_name": "Bob", "look_id": LOOK_BOB}])
    rr = svc.replace_binding("s01_c05", {
        "shot_id": "s01_c05", "scene_id": "s01",
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"]},
        "character_bindings": [
            {"character_name": "Alice", "look_id": LOOK,
             "composition_id": COMP_PISTOL},
            {"character_name": "Bob", "look_id": LOOK_BOB}],
        "prop_bindings": [],
        "action": {"kind": "hand_off", "subject_character": "Alice",
                   "prop_id": PISTOL, "start_state": "held by Alice",
                   "end_state": "held by ?"},
    }, expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr
    result = svc.compile_reference_pack("s01_c05")
    assert result["status"] == "BLOCKED"
    assert "action_target_unspecified" in {i["reason"] for i in result["issues"]}


def test_k3_hand_off_two_receivers_ambiguous(env):
    pdir, ids, svc = env
    _pdir_holder[0] = pdir
    _add_pistol_with_comp(pdir, COMP_PISTOL, "Alice", LOOK)
    _add_pistol_with_comp(pdir, COMP_BOB_PISTOL, "Bob", LOOK_BOB)
    _add_charlie(pdir, COMP_CHARLIE_P)
    _add_shot(pdir, "s01_c05", ["Alice", "Bob", "Charlie"])
    from test_shot_binding_s3b2 import _bind
    r = _bind(svc, shot_id="s01_c05", inst=ids["inst"], view=ids["view_a"],
              characters=[{"character_name": "Alice", "look_id": LOOK,
                           "composition_id": COMP_PISTOL},
                          {"character_name": "Bob", "look_id": LOOK_BOB,
                           "composition_id": COMP_BOB_PISTOL},
                          {"character_name": "Charlie", "look_id": "look_charlie001",
                           "composition_id": COMP_CHARLIE_P}])
    rr = svc.replace_binding("s01_c05", {
        "shot_id": "s01_c05", "scene_id": "s01",
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"]},
        "character_bindings": [
            {"character_name": "Alice", "look_id": LOOK,
             "composition_id": COMP_PISTOL},
            {"character_name": "Bob", "look_id": LOOK_BOB,
             "composition_id": COMP_BOB_PISTOL},
            {"character_name": "Charlie", "look_id": "look_charlie001",
             "composition_id": COMP_CHARLIE_P}],
        "prop_bindings": [],
        "action": {"kind": "hand_off", "subject_character": "Alice",
                   "prop_id": PISTOL, "start_state": "held by Alice",
                   "end_state": "held by Bob or Charlie"},
    }, expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr
    result = svc.compile_reference_pack("s01_c05")
    assert result["status"] == "BLOCKED"
    assert "action_semantics_ambiguous" in {i["reason"] for i in result["issues"]}


# ============ L. custom Action（§56）============

def test_l1_l2_l3_custom_action_metadata_only(env):
    pdir, ids, svc = env
    _pdir_holder[0] = pdir
    _add_pistol_with_comp(pdir, COMP_PISTOL, "Alice", LOOK)
    r = _bind_default(svc, ids, characters=[
        {"character_name": "Alice", "look_id": LOOK,
         "composition_id": COMP_PISTOL}])
    rr = svc.replace_binding("s01_c01", {
        "shot_id": "s01_c01", "scene_id": "s01",
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"]},
        "character_bindings": [{"character_name": "Alice", "look_id": LOOK,
                                "composition_id": COMP_PISTOL}],
        "prop_bindings": [],
        "action": {"kind": "custom", "subject_character": "Alice",
                   "prop_id": PISTOL, "start_state": "?", "end_state": "?",
                   "note": "inspects the pistol thoughtfully"},
    }, expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr
    result = svc.compile_reference_pack("s01_c01")
    assert result["status"] == "COMPILED"
    p = result["pack"]
    assert p["action"]["kind"] == "custom"
    assert p["action"]["note"] == "inspects the pistol thoughtfully", "L3 保留语义元数据"
    comp_refs = _ref(p, source_type="composition")
    assert comp_refs[0]["temporal_scope"] == "static", "L2 不从 note 推 Timeline"
    assert any(d["reason"] == "action_temporal_unclassified" for d in p["diagnostics"])


# ============ M. Same prop multiple effective relations（§57）============

def test_m1_two_comps_same_prop_blocked(env):
    pdir, ids, svc = env
    _pdir_holder[0] = pdir
    _add_shot(pdir, "s01_c05", ["Alice", "Bob"])
    _add_bob_comp_sword(pdir, COMP_BOB_SWORD)
    _give_plate(pdir, COMP, "ref_comp_plate1")
    from test_shot_binding_s3b2 import _bind
    _bind(svc, shot_id="s01_c05", inst=ids["inst"], view=ids["view_a"],
          characters=[{"character_name": "Alice", "look_id": LOOK,
                       "composition_id": COMP},
                      {"character_name": "Bob", "look_id": LOOK_BOB,
                       "composition_id": COMP_BOB_SWORD}])
    result = svc.compile_reference_pack("s01_c05")
    assert result["status"] == "BLOCKED"
    issue = [i for i in result["issues"]
             if i["reason"] == "multiple_effective_prop_relations"]
    assert issue and issue[0]["detail"]["prop_id"] == PROP


# ============ N. Reference resolution（§58）============

def test_n1_approved_refs_resolve(env):
    pdir, ids, svc = env
    _bind_default(svc, ids)
    p = svc.compile_reference_pack("s01_c01")["pack"]
    look_items = _ref(p, source_type="character_look")
    assert all(i["reference_asset_id"] for i in look_items)
    rd = ResourceStore(pdir, pdir.name).load()
    for i in look_items:
        assert i["reference_asset_id"] in rd.reference_assets
        assert i["reference_asset_id"] in rd.looks[LOOK].approved_reference_asset_ids


def test_n2_unresolvable_blocked_no_glob(env):
    """§38/§N2：pre READY 后、材料收集时 approved id 失去 ReferenceAsset →
    pack 层 fail closed reference_asset_unresolvable（绝不 glob 兜底）。"""
    pdir, ids, svc = env
    _bind_default(svc, ids)
    original = svc._collect_pack_material

    def race(shot_id, pre):
        # 在材料收集（rdoc 加载）之前删除 approved 指向的 asset
        rd = ResourceStore(pdir, pdir.name).load()
        del rd.reference_assets["ref_alice000001"]   # look 的唯一 approved ref
        ResourceStore(pdir, pdir.name).save(rd)
        return original(shot_id, pre)

    svc._collect_pack_material = race
    r = svc.compile_reference_pack("s01_c01")
    assert r["status"] == "BLOCKED"
    assert "reference_asset_unresolvable" in {i["reason"] for i in r["issues"]}


# ============ O/P. No provider fields / no raw-path identity（§59/§60）============

def test_o1_no_provider_fields(env):
    pdir, ids, svc = env
    _give_plate(pdir, COMP, "ref_comp_plate1")
    _add_pistol_with_comp(pdir, COMP_PISTOL, "Alice", LOOK)
    _bind_default(svc, ids, composition=True)
    s = json.dumps(svc.compile_reference_pack("s01_c01")["pack"], sort_keys=True,
                   ensure_ascii=False)
    for word in ("weight", "strength", "seed", "sampler", "cfg", "provider",
                 "model_name", "slot", "controlnet", "ipadapter", "denoise"):
        assert f'"{word}"' not in s, f"禁止 provider 字段：{word}"


def test_p1_no_raw_path_identity(env):
    pdir, ids, svc = env
    _bind_default(svc, ids)
    p = svc.compile_reference_pack("s01_c01")["pack"]

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k, v
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)

    for k, v in walk(p):
        if isinstance(v, str):
            assert not v.startswith("/"), f"绝对路径不得作为 identity: {k}={v}"
            assert not v.endswith(".png"), f"文件路径不得作为 identity: {k}={v}"
            assert "output/" not in v or k == "hint", f"路径不作 identity: {k}={v}"


# ============ Q/R/S/T. Race consistency（§61-§64）============

def test_q1_race_dependency_changed_during_compile(env):
    """§61：pre READY → compile 中绑定依赖被改 → 不得返回 mixed-state Pack。"""
    pdir, ids, svc = env
    _bind_default(svc, ids)
    original = svc._collect_pack_material

    def race(shot_id, pre):
        material = original(shot_id, pre)
        rd = ResourceStore(pdir, pdir.name).load()
        rd.reference_assets["ref_alice000009"] = ReferenceAsset(
            asset_id="ref_alice000009", url="http://example/alice9.png")
        rd.looks[LOOK].approved_reference_asset_ids.append("ref_alice000009")
        ResourceStore(pdir, pdir.name).save(rd)
        return material

    svc._collect_pack_material = race
    r = svc.compile_reference_pack("s01_c01")
    assert r["status"] == "BLOCKED", r
    assert "dependency_changed_during_compile" in {i["reason"] for i in r["issues"]}


def test_r1_unrelated_mutation_still_compiles(env):
    """§62：Thread B 改 Bob Look → Alice Pack 不受影响（actual dependency only）。"""
    pdir, ids, svc = env
    _bind_default(svc, ids)
    base = svc.compile_reference_pack("s01_c01")["pack"]
    original = svc._collect_pack_material

    def race(shot_id, pre):
        material = original(shot_id, pre)
        rd = ResourceStore(pdir, pdir.name).load()
        rd.looks[LOOK_BOB].label = "Bob mutated"
        ResourceStore(pdir, pdir.name).save(rd)
        return material

    svc._collect_pack_material = race
    r = svc.compile_reference_pack("s01_c01")
    assert r["status"] == "COMPILED"
    assert r["pack"] == base, "Alice Pack 不变化"


def test_s1_race_profile_changed_blocked(env):
    pdir, ids, svc = env
    _bind_default(svc, ids)
    original = svc._collect_pack_material

    def race(shot_id, pre):
        material = original(shot_id, pre)
        cur = VisualProfileService(pdir, pdir.name).read()["revision"]
        VisualProfileService(pdir, pdir.name).update(
            render_family="stylized", expected_revision=cur)
        return material

    svc._collect_pack_material = race
    r = svc.compile_reference_pack("s01_c01")
    assert r["status"] == "BLOCKED"
    assert "dependency_changed_during_compile" in {i["reason"] for i in r["issues"]}


def test_t1_race_siv_revoked_blocked(env):
    pdir, ids, svc = env
    _bind_default(svc, ids)
    original = svc._collect_pack_material

    def race(shot_id, pre):
        material = original(shot_id, pre)
        st = SceneSystemStore(pdir, pdir.name)
        d = st.load()
        d.scene_instance_views[ids["sciv_a"]].approval_status = "rejected"
        st.save(d)
        return material

    svc._collect_pack_material = race
    r = svc.compile_reference_pack("s01_c01")
    assert r["status"] == "BLOCKED"
    assert "dependency_changed_during_compile" in {i["reason"] for i in r["issues"]} or \
        r["binding_readiness"] == NOT_READY


# ============ §65 跨域集成 ============

def test_integration_full_flow(env):
    """§65 六步全链：READY → COMPILED → 无关变更不变 → 绑定 Look 变 → BLOCKED →
    refresh → READY → COMPILED → profile 变 → 按 Scene 真实 stale 链恢复后才 COMPILED。"""
    pdir, ids, svc = env
    _pdir_holder[0] = pdir
    _add_pistol_with_comp(pdir, COMP_PISTOL, "Alice", LOOK)
    r = _bind_default(svc, ids, characters=[
        {"character_name": "Alice", "look_id": LOOK,
         "composition_id": COMP_PISTOL}])
    # Step 1+2
    assert svc.read_validity("s01_c01")["readiness"] == READY
    result = svc.compile_reference_pack("s01_c01")
    assert result["status"] == "COMPILED"
    pack1 = result["pack"]
    assert pack1["visual_profile"]["render_family"] == "2d_animation"
    assert pack1["scene"]["scene_instance_view_id"] == ids["sciv_a"]
    assert pack1["characters"][0]["look_id"] == LOOK
    assert pack1["action"]["kind"] == "none"
    assert any(x["source_type"] == "composition" for x in pack1["references"])
    # Step 3 unrelated Bob Look → Alice pack 不变
    rd = ResourceStore(pdir, pdir.name).load()
    rd.looks[LOOK_BOB].label = "Bob unrelated"
    ResourceStore(pdir, pdir.name).save(rd)
    assert svc.compile_reference_pack("s01_c01")["pack"] == pack1
    # Step 4 bound look 变 → NOT_READY + compile BLOCKED
    rd = ResourceStore(pdir, pdir.name).load()
    rd.reference_assets["ref_alice000009"] = ReferenceAsset(
        asset_id="ref_alice000009", url="http://example/alice9.png")
    rd.looks[LOOK].approved_reference_asset_ids.append("ref_alice000009")
    ResourceStore(pdir, pdir.name).save(rd)
    assert svc.read_validity("s01_c01")["readiness"] == NOT_READY
    assert svc.compile_reference_pack("s01_c01")["status"] == "BLOCKED"
    # Step 5 explicit refresh → READY → COMPILED
    rr = svc.refresh_binding("s01_c01", expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr
    assert svc.read_validity("s01_c01")["readiness"] == READY
    result2 = svc.compile_reference_pack("s01_c01")
    assert result2["status"] == "COMPILED"
    # Step 6 profile 变 → Scene 真实 stale 链：refresh 接受 basis 但 SIV stale →
    # compile BLOCKED → Scene regen+reapprove → binding refresh → COMPILED
    cur = VisualProfileService(pdir, pdir.name).read()["revision"]
    assert VisualProfileService(pdir, pdir.name).update(
        render_family="stylized", expected_revision=cur)["ok"]
    assert svc.read_validity("s01_c01")["readiness"] == NOT_READY
    rr2 = svc.refresh_binding("s01_c01", expected_binding_revision=rr["binding_revision"])
    assert rr2["ok"]
    assert svc.compile_reference_pack("s01_c01")["status"] == "BLOCKED", \
        "SIV stale（旧风格）时 compile 必须 BLOCKED"
    run(_regen_and_approve_siv(pdir, ids["sciv_a"], ids["a_door"]))
    rr3 = svc.refresh_binding("s01_c01", expected_binding_revision=rr2["binding_revision"])
    assert rr3["ok"], rr3
    result3 = svc.compile_reference_pack("s01_c01")
    assert result3["status"] == "COMPILED"
    assert result3["pack"]["visual_profile"]["render_family"] == "stylized"


# ============ §69 schema 不变 ============

def test_binding_schema_unchanged(env):
    pdir, ids, svc = env
    doc = ShotBindingStore(pdir).load()
    assert doc.schema_version == 1
    _bind_default(svc, ids)
    p = svc.compile_reference_pack("s01_c01")["pack"]
    assert p["pack_version"] == 1
    # PropBinding canonical 无 character_name 字段（§16/§69：不偷偷改 schema）
    assert "character_name" not in PropBinding.__dataclass_fields__
