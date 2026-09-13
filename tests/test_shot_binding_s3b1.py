# tests/test_shot_binding_s3b1.py
# Shot Production Binding V1 — Phase S3B.1 验收测试（7.md §十八 31 项 + 词表/action 补充）。
#
#  1 model round-trip            2 canonical JSON round-trip     3 schema fail closed
#  4 create binding              5 replace binding               6 delete binding
#  7 expected revision mismatch  8 concurrent write / lock       9 atomic write
# 10 missing Character          11 missing Look                  12 Look character mismatch
# 13 Look without approved ref  14 missing Prop                  15 Prop without approved ref
# 16 missing Composition        17 Composition character mismatch 18 Composition Look mismatch
# 19 Composition not approved   20 SceneInstance missing         21 SceneInstance inactive
# 22 story_scene mismatch       23 LocationView missing/foreign  24 pinned SIV mismatch(×3)
# 25 cast mismatch              26 Composition/direct Prop 冲突   27 Shot 删除留 orphan
# 28 load 不创建文件            29-31 三 canonical 字节级不变
# 补充：invalid_relation / invalid_anchor / action 校验组 / UNBOUND read=None /
#       无 scene_binding 合法 / pinned 指纹快照 / scene_id mismatch
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from src.core.models import Character, Project, Scene, Shot
from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.resources.models import (
    CharacterLook,
    CharacterReferenceComposition,
    CompositionPropBinding,
    CompositionRelation,
    CompositionAnchor,
    Prop as ResourceProp,
    ReferenceAsset,
    ResourceDocument,
)
from src.resources.store import ResourceStore
from src.scene_system.models import (
    Location,
    LocationView,
    SceneInstance,
    SceneInstanceView,
    SceneSystemDocument,
)
from src.scene_system.store import SceneSystemStore
from src.shot_binding import (
    BindingActionKind,
    ShotBindingDocument,
    ShotBindingSchemaError,
    ShotBindingService,
    ShotBindingStore,
    ShotProductionBinding,
    binding_revision,
    document_revision,
)
from src.visual_profile import (
    VisualProfileService,
    load_profile_or_none,
    visual_profile_fingerprint,
)

L = "loc_aaaaaaaaa001"
V = "view_aaaaaaaaa001"
I = "scin_aaaaaaaa001"
SIV = "sciv_aaaaaaaa001"
LOOK = "look_alice000001"
LOOK_BOB = "look_bob00000001"
LOOK_NOAPPR = "look_noappr00001"
PROP = "prop_sword000001"
PROP_NOAPPR = "prop_noappr00001"
COMP = "comp_alice000001"


def run(coro):
    return asyncio.run(coro)


async def _gather(*aws):
    return await asyncio.gather(*aws)


@pytest.fixture(autouse=True)
def _isolated_locks():
    _clear_for_test()
    yield
    _clear_for_test()


def make_pdir(tmp_path: Path) -> Path:
    name = tmp_path.name
    pdir = tmp_path / name
    # ---- project.json：Alice/Bob + s01（c01=[Alice]，c02=[Alice,Ghost]）----
    proj = Project(name=name)
    proj.characters = [Character(name="Alice", description_en="brave explorer"),
                       Character(name="Bob", description_en="clock tinkerer")]
    s1 = Scene(scene_id="s01", title="lab", characters=["Alice"])
    s1.shots = [Shot(shot_id="s01_c01", scene_id="s01", index=1, characters=["Alice"]),
                Shot(shot_id="s01_c02", scene_id="s01", index=2,
                     characters=["Alice", "Ghost"])]
    proj.scenes = [s1]
    ProjectStore(pdir).save(proj)
    # ---- resources.json：approved Look/Prop/Composition + 反例实体 ----
    rdoc = ResourceDocument()
    rdoc.reference_assets["ref_alice000001"] = ReferenceAsset(
        asset_id="ref_alice000001", url="http://example/alice.png")
    rdoc.reference_assets["ref_bob00000001"] = ReferenceAsset(
        asset_id="ref_bob00000001", url="http://example/bob.png")
    rdoc.reference_assets["ref_sword000001"] = ReferenceAsset(
        asset_id="ref_sword000001", url="http://example/sword.png")
    rdoc.looks[LOOK] = CharacterLook(
        look_id=LOOK, character_name="Alice", approval_status="approved",
        approved_reference_asset_ids=["ref_alice000001"])
    rdoc.looks[LOOK_BOB] = CharacterLook(
        look_id=LOOK_BOB, character_name="Bob", approval_status="approved",
        approved_reference_asset_ids=["ref_bob00000001"])
    rdoc.looks[LOOK_NOAPPR] = CharacterLook(
        look_id=LOOK_NOAPPR, character_name="Alice", approval_status="approved",
        approved_reference_asset_ids=[])
    rdoc.props[PROP] = ResourceProp(
        prop_id=PROP, approval_status="approved",
        approved_reference_asset_ids=["ref_sword000001"])
    rdoc.props[PROP_NOAPPR] = ResourceProp(
        prop_id=PROP_NOAPPR, approval_status="approved",
        approved_reference_asset_ids=[])
    rdoc.compositions[COMP] = CharacterReferenceComposition(
        composition_id=COMP, character_name="Alice", look_id=LOOK,
        prop_bindings=[CompositionPropBinding(prop_id=PROP)],
        approval_status="approved")
    rdoc.compositions["comp_noappr00001"] = CharacterReferenceComposition(
        composition_id="comp_noappr00001", character_name="Alice", look_id=LOOK,
        prop_bindings=[], approval_status="unreviewed")
    ResourceStore(pdir, name).save(rdoc)
    # ---- scene_system.json：location/view/instance/SIV + 反例实体 ----
    sdoc = SceneSystemDocument()
    sdoc.locations[L] = Location(location_id=L, name="lab")
    sdoc.locations["loc_other000001"] = Location(location_id="loc_other000001")
    sdoc.views[V] = LocationView(view_id=V, location_id=L)
    sdoc.views["view_otherloc0001"] = LocationView(view_id="view_otherloc0001",
                                                   location_id="loc_other000001")
    sdoc.scene_instances[I] = SceneInstance(scene_instance_id=I, story_scene_id="s01",
                                            location_id=L)
    sdoc.scene_instances["scin_archived0001"] = SceneInstance(
        scene_instance_id="scin_archived0001", story_scene_id="s01",
        location_id=L, status="archived")
    sdoc.scene_instances["scin_wrongscene1"] = SceneInstance(
        scene_instance_id="scin_wrongscene1", story_scene_id="s02", location_id=L)
    sdoc.scene_instance_views[SIV] = SceneInstanceView(
        scene_instance_view_id=SIV, scene_instance_id=I, location_view_id=V,
        approval_status="approved", approved_basis_fingerprint="abcd1234")
    sdoc.scene_instance_views["sciv_wronginst01"] = SceneInstanceView(
        scene_instance_view_id="sciv_wronginst01", scene_instance_id="scin_wrongscene1",
        location_view_id=V, approval_status="approved",
        approved_basis_fingerprint="11111111")
    sdoc.scene_instance_views["sciv_wrongview1"] = SceneInstanceView(
        scene_instance_view_id="sciv_wrongview1", scene_instance_id=I,
        location_view_id="view_otherloc0001", approval_status="approved",
        approved_basis_fingerprint="22222222")
    sdoc.scene_instance_views["sciv_noappr0001"] = SceneInstanceView(
        scene_instance_view_id="sciv_noappr0001", scene_instance_id=I,
        location_view_id=V, approval_status="unreviewed")
    SceneSystemStore(pdir, name).save(sdoc)
    # ---- project_profile.json（指定 → 指纹快照非 None）----
    VisualProfileService(pdir, name).update(render_family="2d_animation",
                                            expected_revision="")
    return pdir


def valid_payload() -> dict:
    return {
        "shot_id": "s01_c01",
        "scene_id": "s01",
        "scene_binding": {"scene_instance_id": I, "location_view_id": V},
        "character_bindings": [{"character_name": "Alice", "look_id": LOOK}],
        "prop_bindings": [],
        "production_notes": "",
    }


def payload_with_composition() -> dict:
    p = valid_payload()
    p["character_bindings"] = [{"character_name": "Alice", "look_id": LOOK,
                                "composition_id": COMP}]
    return p


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ============ 1/2 model & canonical JSON round-trip ============

def test_01_model_round_trip():
    p = payload_with_composition()
    p["scene_binding"]["pinned_scene_instance_view_id"] = SIV
    b = ShotProductionBinding.from_dict(p)
    d1 = b.to_dict()
    b2 = ShotProductionBinding.from_dict(d1)
    assert b2.to_dict() == d1
    assert b2.character_bindings[0].composition_id == COMP
    assert b2.scene_binding.pinned_scene_instance_view_id == SIV
    assert b2.dependency_fingerprints.visual_profile is None  # payload 未含 → 默认空


def test_02_canonical_json_round_trip_deterministic():
    b = ShotProductionBinding.from_dict(valid_payload())
    doc = ShotBindingDocument(bindings={"s01_c01": b})
    r1 = document_revision(doc)
    # 同内容重建 → 同 revision（canonical sort_keys，deterministic）
    d2 = ShotBindingDocument.from_dict(json.loads(json.dumps(doc.to_dict())))
    assert document_revision(d2) == r1
    assert binding_revision(ShotProductionBinding.from_dict(b.to_dict())) == \
        binding_revision(b)


# ============ 3 schema fail closed ============

def test_03_schema_fail_closed(tmp_path):
    pdir = make_pdir(tmp_path)
    p = ShotBindingStore(pdir).path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema_version": 2, "bindings": {}}), encoding="utf-8")
    with pytest.raises(ShotBindingSchemaError):
        ShotBindingStore(pdir).load()
    with pytest.raises(ShotBindingSchemaError):
        ShotBindingService(pdir).read_document()
    # 坏 JSON 同样 fail closed，不静默重置
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ShotBindingSchemaError):
        ShotBindingStore(pdir).load()


# ============ 4/5/6 CRUD ============

def test_04_create_binding(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = ShotBindingService(pdir)
    r = svc.create_binding(valid_payload())
    assert r["ok"], r
    assert r["binding"]["shot_id"] == "s01_c01"
    fps = r["binding"]["dependency_fingerprints"]
    assert fps["visual_profile"], "profile 已指定 → 指纹快照非空"
    assert set(fps["characters"]) == {"Alice"}
    assert set(fps["looks"]) == {LOOK}
    assert fps["scene_instance"]
    assert fps["scene_instance_view"] is None, "未 pin SIV → 不解析，快照诚实为 None"
    assert ShotBindingStore(pdir).exists()
    back = svc.read_binding("s01_c01")
    assert back["binding_revision"] == r["binding_revision"]
    # 指纹快照值 == 当前实体指纹（bind 时刻一致性 sanity）
    from src.resources.models import look_revision
    assert fps["looks"][LOOK] == look_revision(ResourceStore(pdir).load().looks[LOOK])
    assert fps["visual_profile"] == visual_profile_fingerprint(load_profile_or_none(pdir))


def test_04b_create_with_pinned_siv_snapshots_fingerprint(tmp_path):
    pdir = make_pdir(tmp_path)
    p = valid_payload()
    p["scene_binding"]["pinned_scene_instance_view_id"] = SIV
    r = ShotBindingService(pdir).create_binding(p)
    assert r["ok"], r
    assert r["binding"]["dependency_fingerprints"]["scene_instance_view"] == "abcd1234"


def test_04c_create_without_scene_binding_ok(tmp_path):
    pdir = make_pdir(tmp_path)
    p = valid_payload()
    p.pop("scene_binding")
    r = ShotBindingService(pdir).create_binding(p)
    assert r["ok"], r
    fps = r["binding"]["dependency_fingerprints"]
    assert fps["scene_instance"] is None and fps["scene_instance_view"] is None


def test_05_replace_binding(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = ShotBindingService(pdir)
    r1 = svc.create_binding(valid_payload())
    assert r1["ok"]
    created_at = r1["binding"]["created_at"]
    p2 = payload_with_composition()          # 合法替换：换成 composition 绑定
    r2 = svc.replace_binding("s01_c01", p2,
                             expected_binding_revision=r1["binding_revision"])
    assert r2["ok"], r2
    assert r2["binding"]["character_bindings"][0]["composition_id"] == COMP
    assert r2["binding"]["created_at"] == created_at, "replace 保留 created_at"
    assert r2["binding_revision"] != r1["binding_revision"]
    assert set(r2["binding"]["dependency_fingerprints"]["compositions"]) == {COMP}


def test_06_delete_binding(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = ShotBindingService(pdir)
    r1 = svc.create_binding(valid_payload())
    r2 = svc.delete_binding("s01_c01", expected_binding_revision=r1["binding_revision"])
    assert r2["ok"], r2
    assert svc.read_binding("s01_c01") is None
    assert ShotBindingStore(pdir).exists(), "delete 后文件仍在（空 doc 合法）"


def test_07_expected_revision_mismatch(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = ShotBindingService(pdir)
    r1 = svc.create_binding(valid_payload())
    assert r1["ok"]
    bad = "deadbeef"
    r2 = svc.replace_binding("s01_c01", payload_with_composition(),
                             expected_binding_revision=bad)
    assert not r2["ok"] and r2["error"] == "stale_binding_revision"
    assert r2["current_binding_revision"] == r1["binding_revision"]
    r3 = svc.delete_binding("s01_c01", expected_binding_revision=bad)
    assert not r3["ok"] and r3["error"] == "stale_binding_revision"
    # 拒绝后内容未被改动（不 silent retry）
    assert svc.read_binding("s01_c01")["binding_revision"] == r1["binding_revision"]


def test_08_concurrent_write_lock(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = ShotBindingService(pdir)
    # 并发 create 同一 shot：恰好一个成功，一个 binding_already_exists（锁内 absence check）
    rs = run(_gather(svc.create_binding_async(valid_payload()),
                     svc.create_binding_async(valid_payload())))
    assert sorted(r["ok"] for r in rs) == [False, True]
    assert "binding_already_exists" in {r.get("error") for r in rs}
    # 并发 replace 携带同一 expected：恰好一个成功，一个 stale（per-binding OCC）
    cur = svc.read_binding("s01_c01")["binding_revision"]
    rs2 = run(_gather(
        svc.replace_binding_async("s01_c01", payload_with_composition(),
                                  expected_binding_revision=cur),
        svc.replace_binding_async("s01_c01", payload_with_composition(),
                                  expected_binding_revision=cur)))
    assert sorted(r["ok"] for r in rs2) == [False, True]
    assert {r.get("error") for r in rs2} == {None, "stale_binding_revision"}


def test_09_atomic_write(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = ShotBindingService(pdir)
    assert svc.create_binding(valid_payload())["ok"]
    path = ShotBindingStore(pdir).path
    data = json.loads(path.read_text(encoding="utf-8"))  # 完整合法 JSON
    assert data["schema_version"] == 1 and "s01_c01" in data["bindings"]
    assert not list(path.parent.glob("*.tmp")), "原子写不留临时文件"


# ============ 10-19 Resources 侧 fail closed ============

def test_10_missing_character(tmp_path):
    pdir = make_pdir(tmp_path)
    p = valid_payload()
    p["shot_id"] = "s01_c02"           # shot.characters = [Alice, Ghost]
    p["character_bindings"] = [
        {"character_name": "Alice", "look_id": LOOK},
        {"character_name": "Ghost", "look_id": LOOK_NOAPPR}]
    r = ShotBindingService(pdir).create_binding(p)
    assert not r["ok"] and r["error"] == "character_missing"


def test_11_missing_look(tmp_path):
    pdir = make_pdir(tmp_path)
    p = valid_payload()
    p["character_bindings"] = [{"character_name": "Alice", "look_id": "look_nope"}]
    r = ShotBindingService(pdir).create_binding(p)
    assert not r["ok"] and r["error"] == "look_missing"


def test_12_look_character_mismatch(tmp_path):
    pdir = make_pdir(tmp_path)
    p = valid_payload()
    p["character_bindings"] = [{"character_name": "Alice", "look_id": LOOK_BOB}]
    r = ShotBindingService(pdir).create_binding(p)
    assert not r["ok"] and r["error"] == "look_character_mismatch"


def test_13_look_without_approved_reference(tmp_path):
    pdir = make_pdir(tmp_path)
    p = valid_payload()
    p["character_bindings"] = [{"character_name": "Alice", "look_id": LOOK_NOAPPR}]
    r = ShotBindingService(pdir).create_binding(p)
    assert not r["ok"] and r["error"] == "look_not_approved"


def test_14_missing_prop(tmp_path):
    pdir = make_pdir(tmp_path)
    p = valid_payload()
    p["prop_bindings"] = [{"prop_id": "prop_nope", "relation": "held",
                           "anchor": "right_hand"}]
    r = ShotBindingService(pdir).create_binding(p)
    assert not r["ok"] and r["error"] == "prop_missing"


def test_15_prop_without_approved_reference(tmp_path):
    pdir = make_pdir(tmp_path)
    p = valid_payload()
    p["prop_bindings"] = [{"prop_id": PROP_NOAPPR, "relation": "held",
                           "anchor": "right_hand"}]
    r = ShotBindingService(pdir).create_binding(p)
    assert not r["ok"] and r["error"] == "prop_not_approved"


def test_16_missing_composition(tmp_path):
    pdir = make_pdir(tmp_path)
    p = valid_payload()
    p["character_bindings"] = [{"character_name": "Alice", "look_id": LOOK,
                                "composition_id": "comp_nope"}]
    r = ShotBindingService(pdir).create_binding(p)
    assert not r["ok"] and r["error"] == "composition_missing"


def test_17_composition_character_mismatch(tmp_path):
    pdir = make_pdir(tmp_path)
    rdoc = ResourceStore(pdir).load()
    rdoc.compositions["comp_bob000000001"] = CharacterReferenceComposition(
        composition_id="comp_bob000000001", character_name="Bob", look_id=LOOK_BOB,
        approval_status="approved")
    ResourceStore(pdir).save(rdoc)
    p = valid_payload()
    p["character_bindings"] = [{"character_name": "Alice", "look_id": LOOK,
                                "composition_id": "comp_bob000000001"}]
    r = ShotBindingService(pdir).create_binding(p)
    assert not r["ok"] and r["error"] == "composition_character_mismatch"


def test_18_composition_look_mismatch(tmp_path):
    pdir = make_pdir(tmp_path)
    # 合法的 Alice 第二 Look（能穿过 look 校验），composition 的 look_id 是 LOOK → 不一致
    rdoc = ResourceStore(pdir).load()
    rdoc.reference_assets["ref_alice000002"] = ReferenceAsset(
        asset_id="ref_alice000002", url="http://example/alice2.png")
    rdoc.looks["look_alice000002"] = CharacterLook(
        look_id="look_alice000002", character_name="Alice",
        approval_status="approved", approved_reference_asset_ids=["ref_alice000002"])
    ResourceStore(pdir).save(rdoc)
    p = valid_payload()
    p["character_bindings"] = [{"character_name": "Alice",
                                "look_id": "look_alice000002",
                                "composition_id": COMP}]
    r = ShotBindingService(pdir).create_binding(p)
    assert not r["ok"] and r["error"] == "composition_look_mismatch"


def test_19_composition_not_approved(tmp_path):
    pdir = make_pdir(tmp_path)
    p = valid_payload()
    p["character_bindings"] = [{"character_name": "Alice", "look_id": LOOK,
                                "composition_id": "comp_noappr00001"}]
    r = ShotBindingService(pdir).create_binding(p)
    assert not r["ok"] and r["error"] == "composition_not_approved"


# ============ 20-24 Scene 侧 fail closed ============

def test_20_scene_instance_missing(tmp_path):
    pdir = make_pdir(tmp_path)
    p = valid_payload()
    p["scene_binding"] = {"scene_instance_id": "scin_nope", "location_view_id": V}
    r = ShotBindingService(pdir).create_binding(p)
    assert not r["ok"] and r["error"] == "scene_instance_missing"


def test_21_scene_instance_inactive(tmp_path):
    pdir = make_pdir(tmp_path)
    p = valid_payload()
    p["scene_binding"] = {"scene_instance_id": "scin_archived0001",
                          "location_view_id": V}
    r = ShotBindingService(pdir).create_binding(p)
    assert not r["ok"] and r["error"] == "scene_instance_not_active"


def test_22_story_scene_mismatch(tmp_path):
    pdir = make_pdir(tmp_path)
    p = valid_payload()
    p["scene_binding"] = {"scene_instance_id": "scin_wrongscene1",
                          "location_view_id": V}
    r = ShotBindingService(pdir).create_binding(p)
    assert not r["ok"] and r["error"] == "story_scene_mismatch"


def test_23_location_view_missing_and_foreign(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = ShotBindingService(pdir)
    p = valid_payload()
    p["scene_binding"] = {"scene_instance_id": I, "location_view_id": "view_nope"}
    r = svc.create_binding(p)
    assert not r["ok"] and r["error"] == "location_view_missing"
    p["scene_binding"] = {"scene_instance_id": I,
                          "location_view_id": "view_otherloc0001"}
    r2 = svc.create_binding(p)
    assert not r2["ok"] and r2["error"] == "location_view_not_in_location"


def test_24_pinned_siv_mismatch(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = ShotBindingService(pdir)
    for sciv, err in (("sciv_wronginst01", "scene_instance_view_instance_mismatch"),
                      ("sciv_wrongview1", "scene_instance_view_view_mismatch"),
                      ("sciv_noappr0001", "scene_instance_view_not_approved")):
        p = valid_payload()
        p["scene_binding"] = {"scene_instance_id": I, "location_view_id": V,
                              "pinned_scene_instance_view_id": sciv}
        r = svc.create_binding(p)
        assert not r["ok"] and r["error"] == err, (sciv, r)


# ============ 25/26 cast & conflict ============

def test_25_cast_mismatch(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = ShotBindingService(pdir)
    # 少绑：shot s01_c02 = [Alice, Ghost]，binding 只覆盖 Ghost
    p = valid_payload()
    p["shot_id"] = "s01_c02"
    p["character_bindings"] = [{"character_name": "Ghost", "look_id": LOOK_NOAPPR}]
    r = svc.create_binding(p)
    assert not r["ok"] and r["error"] == "cast_mismatch"
    # 多绑：Bob 不在 shot s01_c01 的 characters
    p2 = valid_payload()
    p2["character_bindings"] = [
        {"character_name": "Alice", "look_id": LOOK},
        {"character_name": "Bob", "look_id": LOOK_BOB}]
    r2 = svc.create_binding(p2)
    assert not r2["ok"] and r2["error"] == "cast_mismatch"


def test_26_composition_direct_prop_conflict(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = ShotBindingService(pdir)
    # §七硬规则：composition 已覆盖 sword，direct prop 再声明 sword → fail closed
    p = payload_with_composition()
    p["prop_bindings"] = [{"prop_id": PROP, "relation": "held", "anchor": "left_hand"}]
    r = svc.create_binding(p)
    assert not r["ok"] and r["error"] == "binding_conflict"
    # 非冲突路径：direct prop 不在 composition 覆盖集内 → 合法
    rdoc = ResourceStore(pdir).load()
    rdoc.props["prop_lamp0000001"] = ResourceProp(
        prop_id="prop_lamp0000001", approval_status="approved",
        approved_reference_asset_ids=["ref_sword000001"])
    ResourceStore(pdir).save(rdoc)
    p2 = payload_with_composition()
    p2["prop_bindings"] = [{"prop_id": "prop_lamp0000001", "relation": "near",
                            "anchor": "beside_character"}]
    r2 = svc.create_binding(p2)
    assert r2["ok"], r2


# ============ 27 orphan ============

def test_27_shot_deletion_leaves_orphan_binding(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = ShotBindingService(pdir)
    assert svc.create_binding(valid_payload())["ok"]
    # 删除 shot（模拟用户/Agent 删除镜：直接改 project.json——S3B.1 无 GC）
    proj = ProjectStore(pdir).load()
    proj.scenes[0].shots = [s for s in proj.scenes[0].shots if s.shot_id != "s01_c01"]
    ProjectStore(pdir).save(proj)
    # binding 不自动删除；read 原样返回（orphan 标记属 S3B.2）
    back = svc.read_binding("s01_c01")
    assert back is not None and back["binding"]["shot_id"] == "s01_c01"
    # 重新 bind 同 shot → 锁内先撞 binding_already_exists（不覆盖 orphan，不偷删）
    r = svc.create_binding(valid_payload())
    assert not r["ok"] and r["error"] == "binding_already_exists"


# ============ 28 load 零副作用 ============

def test_28_load_does_not_create_file(tmp_path):
    pdir = make_pdir(tmp_path)
    store = ShotBindingStore(pdir)
    svc = ShotBindingService(pdir)
    for _ in range(3):
        assert store.load().bindings == {}
        assert svc.read_document()["bindings"] == {}
        assert svc.read_binding("s01_c01") is None
    assert not store.exists(), "纯读绝不能创建 shot_production_bindings.json"


# ============ 29/30/31 冻结 canonical 字节级不变 ============

def test_29_30_31_other_canonicals_byte_unchanged(tmp_path):
    pdir = make_pdir(tmp_path)
    pj = pdir / "output" / "project.json"
    rj = pdir / "output" / "resources.json"
    sj = pdir / "output" / "scene_system.json"
    h0 = (_sha(pj), _sha(rj), _sha(sj))
    svc = ShotBindingService(pdir)
    r1 = svc.create_binding(payload_with_composition())
    assert r1["ok"]
    r2 = svc.replace_binding("s01_c01", valid_payload(),
                             expected_binding_revision=r1["binding_revision"])
    assert r2["ok"]
    r3 = svc.delete_binding("s01_c01", expected_binding_revision=r2["binding_revision"])
    assert r3["ok"]
    assert svc.read_document()["bindings"] == {}
    assert (_sha(pj), _sha(rj), _sha(sj)) == h0, \
        "binding 全生命周期不得改动 project.json / resources.json / scene_system.json"


# ============ 补充：词表复用 & action 校验 ============

def test_x1_invalid_relation_and_anchor_rejected(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = ShotBindingService(pdir)
    p = valid_payload()
    p["prop_bindings"] = [{"prop_id": PROP, "relation": "wielded",
                           "anchor": "right_hand"}]
    r = svc.create_binding(p)
    assert not r["ok"] and r["error"] == "invalid_relation"
    p["prop_bindings"] = [{"prop_id": PROP, "relation": "held", "anchor": "teeth"}]
    r2 = svc.create_binding(p)
    assert not r2["ok"] and r2["error"] == "invalid_anchor"
    # 词表值 = Resources enum 值（不创建第二套词表）
    p["prop_bindings"] = [{"prop_id": PROP,
                           "relation": CompositionRelation.CARRIED.value,
                           "anchor": CompositionAnchor.LEFT_HAND.value}]
    r3 = svc.create_binding(p)
    assert r3["ok"], r3


def test_x2_action_validation(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = ShotBindingService(pdir)
    assert {k.value for k in BindingActionKind} == \
        {"none", "static", "pick_up", "put_down", "hand_off", "custom"}
    p = valid_payload()
    p["action"] = {"kind": "explode"}
    assert svc.create_binding(p)["error"] == "invalid_action_kind"
    p["action"] = {"kind": "pick_up", "subject_character": "Alice"}
    assert svc.create_binding(p)["error"] == "action_prop_required"
    p["action"] = {"kind": "pick_up", "prop_id": PROP}
    assert svc.create_binding(p)["error"] == "action_subject_required"
    p["action"] = {"kind": "pick_up", "prop_id": PROP, "subject_character": "Ghost"}
    assert svc.create_binding(p)["error"] == "action_subject_unknown"
    p["action"] = {"kind": "pick_up", "prop_id": "prop_nope",
                   "subject_character": "Alice"}
    assert svc.create_binding(p)["error"] == "action_prop_missing"
    # 合法 pick_up（prop FK 存在即可；approved 与否属 S3B.2 validity）
    p["action"] = {"kind": "pick_up", "prop_id": PROP,
                   "subject_character": "Alice",
                   "start_state": "right hand empty",
                   "end_state": "right hand holding the wooden sword"}
    r = svc.create_binding(p)
    assert r["ok"], r
    assert r["binding"]["action"]["kind"] == "pick_up"


def test_x3_unbound_read_is_not_error(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = ShotBindingService(pdir)
    assert svc.read_binding("s01_c01") is None          # UNBOUND ≠ error
    assert svc.read_document()["bindings"] == {}
    # replace/delete 不存在的 binding → binding_not_found（不是异常）
    assert svc.replace_binding("s01_c01", valid_payload(),
                               expected_binding_revision="x")["error"] == "binding_not_found"
    assert svc.delete_binding("s01_c01",
                              expected_binding_revision="x")["error"] == "binding_not_found"


def test_x4_scene_id_mismatch_and_shot_missing(tmp_path):
    pdir = make_pdir(tmp_path)
    svc = ShotBindingService(pdir)
    p = valid_payload()
    p["scene_id"] = "s02"
    r = svc.create_binding(p)
    assert not r["ok"] and r["error"] == "scene_mismatch"
    p2 = valid_payload()
    p2["shot_id"] = "s09_c01"
    r2 = svc.create_binding(p2)
    assert not r2["ok"] and r2["error"] == "shot_missing"
