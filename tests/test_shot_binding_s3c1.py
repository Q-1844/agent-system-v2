# tests/test_shot_binding_s3c1.py
# Shot Production Binding V1 — Phase S3C.1 验收测试（11.md §35-§53）。
#
# 核心：真实首帧生成路径（_handler_first_frame）消费 COMPILED ShotReferencePack；
# temporal 先于 priority（end 绝不进首帧）；BOUND+NOT_READY 绝不 legacy fallback；
# 风格唯一来源=Pack.visual_profile（绝不 StyleAnchor）；pre-dispatch race guard；
# 全程 FakeClient 零外部调用（§34/§66）。
import asyncio
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import deps
from src.core.models import Asset, OpeningSource
from src.core.prompt_builder import build_first_frame_prompt
from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.generation.image_adapter import (
    BoundGenerationError,
    build_bound_first_frame_input,
    filter_first_frame_references,
    shot_has_binding,
)
from src.resources.models import (
    CharacterLook,
    Prop as ResourceProp,
    ReferenceAsset,
)
from src.resources.store import ResourceStore
from src.scene_system.store import SceneSystemStore
from src.shot_binding import ShotBindingService, ShotBindingStore
from src.shot_binding.validity import NOT_READY, READY

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))
from test_shot_binding_s3b1 import (  # noqa: E402
    COMP, LOOK, LOOK_BOB, PROP, make_pdir, run,
)
from test_shot_binding_s3b2 import (  # noqa: E402
    _bind, _build_scene,
)
from test_shot_binding_s3b3 import (  # noqa: E402
    PISTOL, _add_pistol_with_comp, _give_plate,
)
from routers.shots import (_collect_first_frame_refs, _handler_first_frame,  # noqa: E402
                           _legacy_vp_style_suffix)

PISTOL_COMP = "comp_pistol000001"


def _sha(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def _hash_all(pdir: Path) -> dict:
    return {n: _sha(pdir / "output" / n) for n in
            ("project.json", "resources.json", "scene_system.json",
             "project_profile.json", "shot_production_bindings.json")}


class FakeImageClient:
    """§34 Fake provider：记录收到的请求（prompt/image_refs/参数）。"""
    def __init__(self):
        self.calls = []

    async def generate_image(self, *, prompt, image_refs=None, size_tier="1K",
                             ratio="16:9", **kw):
        self.calls.append({"prompt": prompt,
                           "image_refs": list(image_refs or []),
                           "size_tier": size_tier, "ratio": ratio})
        return {"url": "https://mock.agnes/out.png"}

    async def download(self, url, dest):
        Path(dest).write_bytes(b"\x89PNG fake")
        return str(dest)


class _StubPM:
    def __init__(self, pdir):
        self._pdir = pdir

    def get_project_dir(self, name):
        return self._pdir


@pytest.fixture()
def cenv(tmp_path, monkeypatch):
    """真实跨域夹具 + deps stub + Fake provider。默认绑定：c01→(inst, view_a)。"""
    _clear_for_test()
    pdir = make_pdir(tmp_path)
    (pdir / "output" / "scene_system.json").unlink()
    ids = run(_build_scene(pdir))
    svc = ShotBindingService(pdir)
    fake = FakeImageClient()
    monkeypatch.setattr(deps, "project_manager", _StubPM(pdir))
    monkeypatch.setattr(deps, "agnes_client", fake)
    yield pdir, ids, svc, fake
    _clear_for_test()


def _bind_default(svc, ids, **kw):
    return _bind(svc, inst=ids["inst"], view=ids["view_a"], **kw)


def _run_handler(pdir, shot_id="s01_c01", variants=1):
    async def emit(*a, **k):
        pass
    job = SimpleNamespace(project=pdir.name, shot_id=shot_id,
                          params={"variants": variants}, kind="first_frame")
    asyncio.run(_handler_first_frame(job, emit))


def _load_shot(pdir, shot_id="s01_c01"):
    return ProjectStore(pdir).load().get_shot(shot_id)


# ============ B. READY gate（§36）============

def test_b1_ready_compiles_and_provider_called_once(cenv):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    _run_handler(pdir)
    assert len(fake.calls) == 1
    req = fake.calls[0]
    assert "2D animated image" in req["prompt"], "P1 风格指令进入 prompt"
    assert "anime, cinematic composition" not in req["prompt"], "无 StyleAnchor 回落"
    assert req["image_refs"], "BOUND 路径携带参考图"


def test_b2_b3_not_ready_provider_never_called_no_legacy_fallback(cenv):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    rd = ResourceStore(pdir, pdir.name).load()
    rd.looks[LOOK].approved_reference_asset_ids = []   # revoke → NOT_READY
    ResourceStore(pdir, pdir.name).save(rd)
    with pytest.raises(BoundGenerationError) as ei:
        build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                      _load_shot(pdir))
    assert ei.value.code == "binding_not_ready"
    assert fake.calls == [], "provider 调用数 = 0"
    # 绝不 legacy fallback：错误路径不产生任何 legacy 参考收集副作用
    assert shot_has_binding(pdir, "s01_c01") is True


def test_q1_unbound_legacy_behavior_identical(cenv):
    pdir, ids, svc, fake = cenv
    # UNBOUND：不建 binding → legacy 链路行为逐字节。
    # （2026-09-18 P1 seam：legacy 链路消费 ProjectVisualProfile——profile specified 时
    # 期望 prompt = builder + 编译点风格后缀；unspecified 时 = 原 StyleAnchor 后缀。
    # 本测试环境 make_pdir 指定 photorealistic profile，故期望值带 profile 后缀。）
    _run_handler(pdir)
    assert len(fake.calls) == 1
    project = ProjectStore(pdir).load()
    shot = _load_shot(pdir)
    legacy_prompt = build_first_frame_prompt(project, shot,
                                             style_suffix=_legacy_vp_style_suffix(pdir))
    legacy_refs = [r["url"] for r in _collect_first_frame_refs(project, shot)]
    assert fake.calls[0]["prompt"] == legacy_prompt, "UNBOUND prompt = legacy 链路（profile specified → 编译点风格后缀）"
    assert fake.calls[0]["image_refs"] == legacy_refs
    assert shot_has_binding(pdir, "s01_c01") is False


def test_q3_ready_only_pack_path(cenv):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    project = ProjectStore(pdir).load()
    req = build_bound_first_frame_input(pdir, project, _load_shot(pdir))
    assert "anime, cinematic composition" not in req.prompt, "READY 只走 Pack path"
    assert req.basis["binding_revision"] == \
        svc.read_binding("s01_c01")["binding_revision"]


# ============ C. temporal filter（§37）============

def test_c1_temporal_before_priority():
    refs = [
        {"source_id": "a", "temporal_scope": "start", "priority": "supporting"},
        {"source_id": "b", "temporal_scope": "throughout", "priority": "primary"},
        {"source_id": "c", "temporal_scope": "static", "priority": "context"},
        {"source_id": "d", "temporal_scope": "end", "priority": "primary"},  # 关键反例
    ]
    eligible, excluded = filter_first_frame_references(refs)
    assert [r["source_id"] for r in eligible] == ["a", "b", "c"]
    assert [r["source_id"] for r in excluded] == ["d"], \
        "temporal 先于 priority：primary+end 仍被排除"


# ============ D. pick_up（§38）============

def _bind_pick_up(svc, ids):
    r = _bind_default(svc, ids, characters=[
        {"character_name": "Alice", "look_id": LOOK,
         "composition_id": PISTOL_COMP}])
    rr = svc.replace_binding("s01_c01", {
        "shot_id": "s01_c01", "scene_id": "s01",
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"]},
        "character_bindings": [{"character_name": "Alice", "look_id": LOOK,
                                "composition_id": PISTOL_COMP}],
        "prop_bindings": [],
        "action": {"kind": "pick_up", "subject_character": "Alice",
                   "prop_id": PISTOL, "start_state": "right hand empty",
                   "end_state": "right hand holding the pistol"},
    }, expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr
    return rr


def test_d1_pick_up_first_frame_excludes_holding_composition(cenv):
    """§38/§9/§22 核心硬测试。"""
    pdir, ids, svc, fake = cenv
    _add_pistol_with_comp(pdir, PISTOL_COMP, "Alice", LOOK)
    _bind_pick_up(svc, ids)
    project = ProjectStore(pdir).load()
    req = build_bound_first_frame_input(pdir, project, _load_shot(pdir))
    roles = [(r["source_type"], r["temporal_scope"]) for r in req.semantic_roles]
    assert ("composition", "end") not in roles, "持枪组合绝不进入首帧参考"
    assert ("character_look", "start") in roles, "Alice Look=start evidence 进入"
    assert ("scene_instance_view", "throughout") in roles, "场景参考进入"
    assert ("prop", "throughout") in roles, "Pistol identity 进入（provider 支持时）"
    # §22：prompt 生产上下文只含 start_state，绝不含 end_state
    assert "right hand empty" in req.prompt
    assert "right hand holding the pistol" not in req.prompt
    _run_handler(pdir)
    assert len(fake.calls) == 1
    assert "right hand holding the pistol" not in fake.calls[0]["prompt"]


# ============ E. put_down（§39）============

def test_e1_put_down_start_composition_included(cenv):
    pdir, ids, svc, fake = cenv
    _add_pistol_with_comp(pdir, PISTOL_COMP, "Alice", LOOK)
    r = _bind_default(svc, ids, characters=[
        {"character_name": "Alice", "look_id": LOOK,
         "composition_id": PISTOL_COMP}])
    rr = svc.replace_binding("s01_c01", {
        "shot_id": "s01_c01", "scene_id": "s01",
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"]},
        "character_bindings": [{"character_name": "Alice", "look_id": LOOK,
                                "composition_id": PISTOL_COMP}],
        "prop_bindings": [],
        "action": {"kind": "put_down", "subject_character": "Alice",
                   "prop_id": PISTOL, "start_state": "holding pistol",
                   "end_state": "empty hand"},
    }, expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr
    req = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                        _load_shot(pdir))
    roles = [(r["source_type"], r["temporal_scope"]) for r in req.semantic_roles]
    assert ("composition", "start") in roles, "持枪组合=start 态证据→可进首帧"
    assert ("character_look", "end") not in roles, "end-only Look 排除"
    assert "holding pistol" in req.prompt, "start_state 进入 prompt"


# ============ F. hand_off（§40）============

def _bind_handoff(svc, ids, comps):
    cast = [c for c, _, _ in comps]
    shot_id = "s01_c05"
    proj = ProjectStore(_pdir_holder[0]).load()
    if not any(s.shot_id == shot_id for s in proj.scenes[0].shots):
        proj.scenes[0].shots.append(__import__("src.core.models", fromlist=["Shot"])
                                    .Shot(shot_id=shot_id, scene_id="s01",
                                          index=9, characters=cast))
        ProjectStore(_pdir_holder[0]).save(proj)
    characters = [{"character_name": n, "look_id": l, "composition_id": c}
                  for n, l, c in comps]
    from test_shot_binding_s3b2 import _bind as bind2
    r = bind2(svc, shot_id=shot_id, inst=ids["inst"], view=ids["view_a"],
              characters=characters)
    rr = svc.replace_binding(shot_id, {
        "shot_id": shot_id, "scene_id": "s01",
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"]},
        "character_bindings": characters,
        "prop_bindings": [],
        "action": {"kind": "hand_off", "subject_character": cast[0],
                   "prop_id": PISTOL, "start_state": f"held by {cast[0]}",
                   "end_state": "held by recipient"},
    }, expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr
    return shot_id


_pdir_holder = [None]


def test_f1_hand_off_start_in_end_out(cenv):
    pdir, ids, svc, fake = cenv
    _pdir_holder[0] = pdir
    _add_pistol_with_comp(pdir, PISTOL_COMP, "Alice", LOOK)
    _add_pistol_with_comp(pdir, "comp_bob_pistol01", "Bob", LOOK_BOB)
    shot_id = _bind_handoff(svc, ids, [("Alice", LOOK, PISTOL_COMP),
                                       ("Bob", LOOK_BOB, "comp_bob_pistol01")])
    req = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                        _load_shot(pdir, shot_id))
    roles = [(r["subject_character"], r["temporal_scope"])
             for r in req.semantic_roles if r["source_type"] == "composition"]
    assert ("Alice", "start") in roles, "start 组合进入首帧"
    assert ("Bob", "end") not in roles, "end-only 组合排除"


def test_f2_hand_off_ambiguous_not_reinterpreted(cenv):
    """§40/§25：Compiler 已 BLOCK 的歧义，Adapter 不重解释。"""
    pdir, ids, svc, fake = cenv
    _pdir_holder[0] = pdir
    _add_pistol_with_comp(pdir, PISTOL_COMP, "Alice", LOOK)
    shot_id = _bind_handoff(svc, ids, [("Alice", LOOK, PISTOL_COMP),
                                       ("Bob", LOOK_BOB, None)])
    result = svc.compile_reference_pack(shot_id)
    assert result["status"] == "BLOCKED"       # Compiler 层已拒（action_target_unspecified）
    with pytest.raises(BoundGenerationError) as ei:
        build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                      _load_shot(pdir, shot_id))
    assert ei.value.code == "binding_not_ready"
    assert fake.calls == []


# ============ G. static Composition（§41）============

def test_g1_static_composition_primary_and_relation_text(cenv):
    pdir, ids, svc, fake = cenv
    _give_plate(pdir, COMP, "ref_comp_plate1")
    _bind_default(svc, ids, composition=True)
    project = ProjectStore(pdir).load()
    req = build_bound_first_frame_input(pdir, project, _load_shot(pdir))
    roles = [(r["source_type"], r["priority"], r["temporal_scope"])
             for r in req.semantic_roles]
    assert ("composition", "primary", "static") in roles, "静态组合=首帧 primary"
    assert ("character_look", "supporting", "static") in roles, "Look supporting"
    assert "held" in req.prompt and "right_hand" in req.prompt, "关系语义进入 prompt"
    # standalone sword 不带关系（composition owns relation）——本 pack 无 direct prop，
    # 故不存在 sword identity 项重复声明关系的可能（G3 已在 s3b3 钉住）


# ============ H. Scene（§42）============

def test_h1_scene_reference_in_request(cenv):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    req = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                        _load_shot(pdir))
    assert any(r["source_type"] == "scene_instance_view" for r in req.semantic_roles)
    assert req.image_refs, "场景参考真实进入 provider refs"


def test_h2_unrelated_view_change_request_unchanged(cenv):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    req1 = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                         _load_shot(pdir))
    st = SceneSystemStore(pdir, pdir.name)
    d = st.load()
    other = [v for v in d.views.values() if v.view_id != ids["view_a"]][0]
    d.views[other.view_id].label = "unrelated rename"
    st.save(d)
    req2 = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                         _load_shot(pdir))
    assert req1.to_dict() == req2.to_dict()


def test_h3_bound_siv_stale_provider_not_called(cenv):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    ssvc = SceneSystemService(pdir, pdir.name)
    rp = run(ssvc.patch_scene_state(
        ids["inst"], {"anchor_states": {ids["a_door"]: {"condition": "blocked"}}},
        expected_revision=ssvc.read_scene_instance(ids["inst"])["revision"]))
    assert rp["ok"]
    with pytest.raises(BoundGenerationError) as ei:
        build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                      _load_shot(pdir))
    assert ei.value.code == "binding_not_ready" and fake.calls == []


from src.scene_system.service import SceneSystemService  # noqa: E402


# ============ I. Visual Profile（§43）============

def test_i1_profile_overrides_conflicting_styleanchor(cenv):
    pdir, ids, svc, fake = cenv
    # 构造冲突 legacy StyleAnchor
    project = ProjectStore(pdir).load()
    project.style.art_style = "legacy oil painting"
    ProjectStore(pdir).save(project)
    _bind_default(svc, ids)
    req = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                        _load_shot(pdir))
    assert "2D animated image" in req.prompt, "服从 ProjectVisualProfile"
    assert "legacy oil painting" not in req.prompt, "完全忽略 legacy StyleAnchor"


# ============ J. Character / Look（§44）============

def test_j1_unbound_look_never_enters_request(cenv):
    pdir, ids, svc, fake = cenv
    rd = ResourceStore(pdir, pdir.name).load()
    rd.reference_assets["ref_aliceB00001"] = ReferenceAsset(
        asset_id="ref_aliceB00001", url="http://example/aliceB.png")
    rd.looks["look_aliceB0001"] = CharacterLook(
        look_id="look_aliceB0001", character_name="Alice",
        approval_status="approved", approved_reference_asset_ids=["ref_aliceB00001"])
    ResourceStore(pdir, pdir.name).save(rd)
    _bind_default(svc, ids)   # 绑定的是 LOOK_A
    req = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                        _load_shot(pdir))
    assert "ref_aliceB00001" not in req.used_reference_ids, "未绑定的 Look B 不进入"
    assert all("aliceB" not in u for u in req.image_refs)


# ============ K. Reference resolution（§45）============

def test_k1_approved_asset_resolves(cenv):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    req = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                        _load_shot(pdir))
    look_items = [r for r in req.semantic_roles if r["source_type"] == "character_look"]
    assert look_items, "look 参考进入 request"
    assert "ref_alice000001" in req.used_reference_ids, "approved ReferenceAsset 正确解析"
    assert any("http://example/alice.png" in u for u in req.image_refs)


def test_k2_primary_unresolvable_provider_not_called(cenv):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    rd = ResourceStore(pdir, pdir.name).load()
    a = rd.reference_assets["ref_alice000001"]
    a.local = str(pdir / "nonexistent" / "x.png")
    a.url = None                                   # 媒体彻底不可解析（实体仍在）
    ResourceStore(pdir, pdir.name).save(rd)
    with pytest.raises(BoundGenerationError) as ei:
        build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                      _load_shot(pdir))
    assert ei.value.code == "reference_media_unresolvable"
    assert fake.calls == [], "provider 未被调用"


def test_k4_legacy_identity_via_compatibility_resolver(cenv):
    """D2（30.md §8）语义更新：legacy identity 媒体仍可经隔离 resolver 解析，
    但人形参考预算不再把它送进 provider——每角色仅 1 张 approved look 主参考。"""
    pdir, ids, svc, fake = cenv
    proj = ProjectStore(pdir).load()
    proj.get_character("Alice").views["front"] = Asset(
        local=None, url="http://example/alice_front.png", status="done")
    ProjectStore(pdir).save(proj)
    _bind_default(svc, ids)
    req = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                        _load_shot(pdir))
    ident = [r for r in req.semantic_roles if r["source_type"] == "character_identity"]
    assert not ident, "legacy identity 不进 provider refs（D2 人形参考预算）"
    assert not any("alice_front" in u for u in req.image_refs)
    budget_drops = [d for d in req.dropped_optional
                    if d.get("reason") == "human_reference_budget"]
    assert budget_drops, "identity 裁剪逐条记录（不静默）"
    assert any("legacy:Alice:front" in str(d.get("source_id")) for d in budget_drops)


# ============ M/N. Race before dispatch（§47/§48）============

def test_m1_race_dependency_changed_before_generation(cenv, monkeypatch):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    import src.generation.image_adapter as ada
    original = ada._resolve_item_media

    def race(item, rdoc, pdir_):
        media = original(item, rdoc, pdir_)
        if item.get("source_type") == "character_look":
            # 媒体解析后、dispatch 前修改被绑 Look
            rd = ResourceStore(pdir, pdir.name).load()
            rd.reference_assets["ref_alice000009"] = ReferenceAsset(
                asset_id="ref_alice000009", url="http://example/alice9.png")
            rd.looks[LOOK].approved_reference_asset_ids.append("ref_alice000009")
            ResourceStore(pdir, pdir.name).save(rd)
        return media

    monkeypatch.setattr(ada, "_resolve_item_media", race)
    with pytest.raises(BoundGenerationError) as ei:
        build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                      _load_shot(pdir))
    assert ei.value.code == "dependency_changed_before_generation"
    assert fake.calls == []


def test_n1_unrelated_race_proceeds(cenv, monkeypatch):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    base = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                         _load_shot(pdir)).to_dict()
    import src.generation.image_adapter as ada
    original = ada._resolve_item_media

    def race(item, rdoc, pdir_):
        media = original(item, rdoc, pdir_)
        if item.get("source_type") == "character_look":
            rd = ResourceStore(pdir, pdir.name).load()
            rd.looks[LOOK_BOB].label = "Bob mutated"
            ResourceStore(pdir, pdir.name).save(rd)
        return media

    monkeypatch.setattr(ada, "_resolve_item_media", race)
    req = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                        _load_shot(pdir))
    assert req.to_dict() == base, "无关变更不误伤、request 不变"


# ============ O/P. Determinism / No mutation（§49/§50）============

def test_o1_deterministic_request(cenv):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    r1 = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                       _load_shot(pdir)).to_dict()
    r2 = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                       _load_shot(pdir)).to_dict()
    assert r1 == r2


def test_p1_no_canonical_mutation(cenv):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    h0 = _hash_all(pdir)
    build_bound_first_frame_input(pdir, ProjectStore(pdir).load(), _load_shot(pdir))
    assert _hash_all(pdir) == h0


# ============ continuity（creative relay 保留）============

def test_continuity_ref_appended_for_bound_shot(cenv):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    proj = ProjectStore(pdir).load()
    c02 = proj.get_shot("s01_c02")
    c02.video_end_frame = Asset(local=None, url="http://example/prev_end.png",
                                status="done")
    c01 = proj.get_shot("s01_c01")
    c01.opening_source = OpeningSource.REFERENCE_PREVIOUS.value
    c01.source_shot_id = "s01_c02"
    ProjectStore(pdir).save(proj)
    req = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                        _load_shot(pdir))
    assert any("http://example/prev_end.png" in u for u in req.image_refs), \
        "接力末帧作为 creative 参考追加"
    assert "Maintain continuity" in req.prompt


# ============ §32 零 canonical mutation（handler 级）============

def test_handler_no_canonical_mutation(cenv):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    h0 = _hash_all(pdir)
    _run_handler(pdir)          # 生成会写 output/frames + project.json 批次（既有结果路径）
    h1 = _hash_all(pdir)
    # 生成结果按既有行为写回 project.json/frames——但 bindings/resources/scene/profile
    # 四个生产真相文件必须字节不变（§32：不写回 Binding/Pack/审批/Scene 真相）
    for n in ("resources.json", "scene_system.json", "project_profile.json",
              "shot_production_bindings.json"):
        assert h0[n] == h1[n], f"{n} 必须字节不变"


# ============ Phase D2（30.md §6-§9）：scene_reference_mode 端到端 ============
def test_D2_build_bound_request_records_scene_mode(cenv):
    """request.scene_reference_mode 记录 resolved 结果；单角色镜头（原 5 refs：
    scene+identity×3+look）在新人形预算下只发 scene+look。"""
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    proj = ProjectStore(pdir).load()
    proj.get_character("Alice").views["front"] = Asset(
        local=None, url="http://example/alice_front.png", status="done")
    ProjectStore(pdir).save(proj)
    req = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                        _load_shot(pdir))
    assert req.scene_reference_mode == "full"
    human = [r for r in req.semantic_roles
             if r["source_type"] in ("character_look", "character_identity")]
    assert len(human) == 1 and human[0]["source_type"] == "character_look"


def test_D2_build_bound_request_context_only(cenv):
    """context_only：场景图不进 refs；场景语义行仍在 prompt（Scene: ...）。"""
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    req = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                        _load_shot(pdir),
                                        scene_reference_mode="context_only")
    assert req.scene_reference_mode == "context_only"
    scene_roles = [r for r in req.semantic_roles
                   if r["source_type"] == "scene_instance_view"]
    assert not scene_roles, "场景图不作为 conditioning 输入"
    assert "Scene:" in req.prompt, "场景语义仍经 prompt 生产上下文进入"
    assert any(d.get("reason") == "scene_reference_context_only"
               for d in req.dropped_optional)


# ============ Phase D2：shot.select_frame（Director 候选选定闭环）============
def test_D2_select_frame_executor(cenv, monkeypatch):
    """候选选定：跨批次定位候选 → 写 selected 指针 + 旧字段同步 → 写后验证。"""
    import asyncio
    import json
    import src.agents.director as dr
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    _run_handler(pdir, variants=2)          # 产生 2 候选批次并自动选第一候选
    proj = ProjectStore(pdir).load()
    shot = proj.get_shot("s01_c01")
    batches = shot.first_frame_batches
    assert len(batches[0].candidates) == 2
    target_cid = batches[0].candidates[1].candidate_id
    assert shot.selected_first_frame != target_cid

    class _StubRT:
        def _resolve_project_dir(self, project):
            return pdir
        def record_work(self, *a, **k):
            pass
    monkeypatch.setattr(dr, "_reload", lambda rt, project: ProjectStore(pdir).load())

    async def emit(*a, **k):
        pass
    receipt = asyncio.run(dr._execute_shot_select_frame(
        _StubRT(), pdir.name,
        json.dumps({"shot_id": "s01_c01", "candidate_id": target_cid,
                    "frame": "first"}), None, None))
    assert receipt["ok"] is True and receipt["verified"] is True
    assert receipt["candidate_id"] == target_cid
    disk = ProjectStore(pdir).load().get_shot("s01_c01")
    assert disk.selected_first_frame == target_cid
    # 错误候选 → 结构化失败
    with pytest.raises(dr.AgentError) as ei:
        asyncio.run(dr._execute_shot_select_frame(
            _StubRT(), pdir.name,
            json.dumps({"shot_id": "s01_c01", "candidate_id": "ff_nope_9"}),
            None, None))
    assert ei.value.code == "candidate_not_found"
