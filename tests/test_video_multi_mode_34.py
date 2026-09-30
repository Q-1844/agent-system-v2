# tests/test_video_multi_mode_34.py
# 34.md §13 — LinkMode.MULTI（单语义参考直生）最小接线验收。
#
# 背景与边界（34.md §1/§2/§5/§13/§27）：
#   - 33.md calibration 胜出配方 = R3 参考格式 + multi 单参考直生（无首帧 conditioning）；
#     生产 handler 此前只有 i2v/keyframes → 「正式 pipeline 无法正确消费 R3」属
#     34.md §27 System blocker 类型 → 按 §5 允许的最小修复，经既有 GenerationRequest
#     seam（31.md §10）接线，Binding/Pack 零改动。
#   - multi 仅 BOUND 可用（参考由 Binding 决定，34.md §12）；UNBOUND fail closed。
#   - v2.0 ti2vid 单图上限（32.md 7×400 实证）→ 编译层恰好 1 参考结构化校验。
#
# 覆盖：prompt_builder multi 分支 / BoundVideoRequest multi 投影 / compile multi
# 结构化校验 / BOUND adapter 参考解析（look hero 优先→场景回退）/ handler multi
# 端到端（Fake provider）/ UNBOUND multi fail closed。全程零真实外部调用。
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import deps
from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.generation.agnes_adapter import (
    AgnesRequestCompileError,
    compile_agnes_video_call,
)
from src.generation.video_adapter import (
    BoundGenerationError,
    BoundVideoRequest,
    build_bound_video_input,
)
from src.shot_binding import ShotBindingService

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))
from test_shot_binding_s3b1 import (  # noqa: E402
    LOOK, make_pdir, run,
)
from test_shot_binding_s3b2 import (  # noqa: E402
    _bind, _build_scene,
)
from test_shot_binding_s3c2 import (  # noqa: E402
    FakeMediaClient, _load_project, _load_shot, _set_video_ready,
    _StubPM, _run_video_handler,
)
from src.resources.store import ResourceStore  # noqa: E402

URL = "http://example/alice.png"


# ============ prompt_builder multi 分支 ============

def _shot(**kw):
    from src.core.models import Shot
    s = Shot(shot_id="s01_c01", scene_id="s01", index=1)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_prompt_multi_branch_action_first_no_animate_prefix():
    from src.core.prompt_builder import _video_prompt_core
    s = _shot(motion="The fox walks along the vineyard path.",
              camera="low angle tracking shot", motion_pace="unhurried",
              environment_motion="leaves sway gently")
    p = _video_prompt_core(s, "multi")
    assert "Animate this image" not in p
    assert "Create a smooth cinematic transition" not in p
    assert "The fox walks along the vineyard path." in p
    assert "camera: low angle tracking shot" in p
    assert "pace: unhurried" in p
    assert "environment: leaves sway gently" in p


def test_prompt_multi_action_sequence_verbatim_and_override_precedence():
    from src.core.prompt_builder import _video_prompt_core
    s = _shot(motion="walk", action_sequence="crouch, leap, land")
    p = _video_prompt_core(s, "multi")
    assert "The actions must unfold in this exact order: crouch, leap, land" in p
    s2 = _shot(motion="walk", video_prompt_override="Custom override text")
    assert _video_prompt_core(s2, "multi") == "Custom override text"


# ============ 投影与编译（multi 槽位契约）============

def test_multi_projection_video_reference_not_first_frame():
    req = BoundVideoRequest(shot_id="s01_c01", prompt="p",
                            provider_mode="multi", video_reference=URL,
                            used_reference_ids=["ref_alice000001"]
                            ).to_generation_request(model=None)
    assert req.first_frame is None and req.last_frame is None
    assert [r.source_type for r in req.reference_inputs] == ["video_reference"]
    assert req.reference_inputs[0].semantic_role == "multi_reference"
    assert req.reference_inputs[0].temporal_scope == "throughout"
    call = compile_agnes_video_call(req)
    assert call.slots() == {"image_urls": [URL]}


def test_multi_compile_reference_bounds_1_to_5():
    """2026-09-28：multi 参考 1-5 个（2.5-flash reference images 上限 5，官方文档
    核读）——0 或 >5 结构化拒绝；单图（prev_tail）与多图（Look+尾帧）均合法。"""
    def _req(refs):
        from src.generation.request import GenerationRequest, ReferenceInput
        return GenerationRequest(
            generation_type="video", shot_id="s01_c01", prompt="p",
            reference_inputs=[ReferenceInput(source_type="video_reference",
                                             media=u) for u in refs],
            provenance={"provider_mode": "multi"})

    with pytest.raises(AgnesRequestCompileError) as ei:
        compile_agnes_video_call(_req([]))
    assert ei.value.code == "multi_reference_slot_invalid"

    call1 = compile_agnes_video_call(_req([URL]))
    assert call1.slots() == {"image_urls": [URL]}
    five = [f"http://example/{i}.png" for i in range(5)]
    call5 = compile_agnes_video_call(_req(five))
    assert call5.slots() == {"image_urls": five}

    with pytest.raises(AgnesRequestCompileError) as ei:
        compile_agnes_video_call(_req([f"http://example/{i}.png"
                                       for i in range(6)]))
    assert ei.value.code == "multi_reference_slot_invalid"

    with pytest.raises(AgnesRequestCompileError) as ei:
        compile_agnes_video_call(_req(["data:image/png;base64,AAAA"]))
    assert ei.value.code == "data_uri_not_supported_video_provider"

    with pytest.raises(AgnesRequestCompileError) as ei:
        compile_agnes_video_call(BoundVideoRequest(
            shot_id="s01_c01", prompt="p", provider_mode="multi",
            video_reference=URL, first_frame="http://example/ff.png"
        ).to_generation_request())
    assert ei.value.code == "multi_slots_conflict"


# ============ BOUND adapter / handler（跨域真实夹具）============

@pytest.fixture()
def venv(tmp_path, monkeypatch):
    _clear_for_test()
    pdir = make_pdir(tmp_path)
    (pdir / "output" / "scene_system.json").unlink()
    ids = run(_build_scene(pdir))
    svc = ShotBindingService(pdir)
    fake = FakeMediaClient()
    monkeypatch.setattr(deps, "project_manager", _StubPM(pdir))
    monkeypatch.setattr(deps, "agnes_client", fake)
    yield pdir, ids, svc, fake
    _clear_for_test()


def test_bound_multi_resolves_look_hero_reference(venv):
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _set_video_ready(pdir, link_mode="multi")
    req = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert req.provider_mode == "multi"
    assert req.first_frame == "" and req.last_frame is None
    # look hero（approved 首项）= multi 单参考的确定性落点
    assert req.video_reference == URL
    assert req.used_reference_ids == ["ref_alice000001"]
    reasons = [d["reason"] for d in req.diagnostics]
    assert "multi_reference_direct" in reasons
    assert req.semantic_roles[0]["slot"] == "multi_reference"
    assert req.semantic_roles[0]["reference_asset_id"] == "ref_alice000001"


def test_bound_multi_handler_end_to_end_fake_provider(venv):
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _set_video_ready(pdir, link_mode="multi")
    _run_video_handler(pdir)
    assert len(fake.video_calls) == 1
    call = fake.video_calls[0]
    assert call["mode"] == "multi"
    assert call["image_urls"] == [URL]
    assert call["image_url"] is None
    # provenance：generation_basis 记录 multi provider_mode
    project = _load_project(pdir)
    shot = project.get_shot("s01_c01")
    versions = list(shot.video_versions or [])
    assert versions and versions[-1].generation_basis.get("provider_mode") == "multi"


def test_bound_multi_preflight_skips_first_frame_requirement(venv):
    """multi 不消费首帧——未生成/未选首帧也能通过 preflight（34.md §13：
    视觉输入=BOUND 参考而非首帧）。"""
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _set_video_ready(pdir, link_mode="multi")
    # 未 _seed_first_frame：首帧无 selected
    shot = _load_shot(pdir)
    assert not shot.first_frame.url_ready
    _run_video_handler(pdir)
    assert len(fake.video_calls) == 1


def test_unbound_multi_fail_closed(venv):
    """UNBOUND 无从解析参考 → fail closed（不静默回落首帧 legacy）。"""
    pdir, ids, svc, fake = venv
    _set_video_ready(pdir, link_mode="multi")
    with pytest.raises(RuntimeError) as ei:
        _run_video_handler(pdir)
    assert "binding" in str(ei.value)
    assert fake.video_calls == []


def test_freshness_read_model_multi_mode_mapping(venv):
    """freshness 读模型的默认 provider 投影 = multi（否则重算键谎报 i2v → STALE）。"""
    from src.generation.native_av_pack import _default_provider_options
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _set_video_ready(pdir, link_mode="multi")
    opts = _default_provider_options(_load_project(pdir), _load_shot(pdir))
    assert opts["mode"] == "multi"


def test_scene_only_binding_multi_uses_scene_reference(venv):
    """34.md §13：纯环境镜（cast 空）scene-only binding → multi 参考走已批准
    SceneInstanceView 资产（34.md §5 最小修复：bind 工具允许空 character_bindings）。
    场景资产需有公网 URL（视频接口约束；local-only 由 rehost 通路补齐）。"""
    pdir, ids, svc, fake = venv
    # 清空 shot cast（纯环境镜）+ 注册带 URL 的场景资产并挂到 SIV（模拟 rehost 后状态）
    ProjectStore(pdir).update_shot("s01_c01", lambda s: setattr(s, "characters", []))
    rd = ResourceStore(pdir, pdir.name).load()
    from src.resources.models import ReferenceAsset
    rd.reference_assets["ref_sciv0000001"] = ReferenceAsset(
        asset_id="ref_sciv0000001", url="http://example/sciv.png")
    ResourceStore(pdir, pdir.name).save(rd)
    _link_sciv_asset(pdir, ids, "ref_sciv0000001")
    r0 = _bind(svc, inst=ids["inst"], view=ids["view_a"], characters=[])
    assert r0["ok"], r0
    _set_video_ready(pdir, link_mode="multi")
    req = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert req.provider_mode == "multi"
    # 无 cast → 参考回退到场景参考（SIV 注册资产），而非 fox look
    assert req.video_reference == "http://example/sciv.png"
    assert req.semantic_roles[0]["source_type"] == "scene_instance_view"
    _run_video_handler(pdir)
    assert fake.video_calls[0]["image_urls"] == ["http://example/sciv.png"]


def _scene_ref_asset_id(pdir):
    """读 binding 的场景视图注册资产 id（scene_binding.reference_asset_id）。"""
    import json as _json
    b = _json.loads((pdir / "output" / "shot_production_bindings.json")
                    .read_text(encoding="utf-8"))
    bd = (b.get("bindings") or {}).get("s01_c01") or {}
    return (bd.get("scene_binding") or {}).get("reference_asset_id")


def _link_sciv_asset(pdir, ids, asset_id):
    """把 SIV 的 reference_asset_id 指到给定资产（真实系统由 scene approval 注册）。"""
    import json as _json
    p = pdir / "output" / "scene_system.json"
    d = _json.loads(p.read_text(encoding="utf-8"))
    for sciv in (d.get("scene_instance_views") or {}).values():
        if sciv.get("scene_instance_id") == ids["inst"]:
            sciv["reference_asset_id"] = asset_id
    p.write_text(_json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def test_multi_relay_prev_tail_reference(venv):
    """34.md V1C：video_reference_asset_id="prev_tail" → multi 参考=影片顺序
    上一镜的实际视频末帧（Phase 6A 提取+托管）；provenance 记 prev_tail_relay；
    freshness 对称（build 侧与重算侧同指纹）。"""
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    # 夹具只有 s01——补建影片顺序第二镜 s02_c01（relay 的目标镜）
    from src.core.models import Scene, Shot
    proj = ProjectStore(pdir).load()
    proj.scenes[0].shots.append(Shot(shot_id="s02_c01", scene_id="s01", index=2))
    ProjectStore(pdir).save(proj)
    r_s2 = _bind(svc, shot_id="s02_c01", inst=ids["inst"], view=ids["view_a"],
                 characters=[])
    assert r_s2["ok"], r_s2
    # s01 末帧（模拟 Phase 6A 提取+托管产物）
    from src.core.models import Asset, ShotStatus
    # 影片顺序：夹具场景含 s01_c01(0), s01_c02(1) + 追加的 s02_c01(2)
    # → relay 前一镜 = s01_c02（跨镜解析按 film order，非 shot_id 数字）
    def _mut(s):
        s.video_end_frame = Asset(url="http://example/s01_tail.png",
                                  status=ShotStatus.DONE.value)
        s.video_end_source_version = "vv_prev000001"
    ProjectStore(pdir).update_shot("s01_c02", _mut)
    def _mut2(s):
        s.video_reference_asset_id = "prev_tail"
        s.link_mode = "multi"
        s.video.num_frames = 73
        s.video.frame_rate = 24
    ProjectStore(pdir).update_shot("s02_c01", _mut2)
    project = _load_project(pdir)
    shot = project.get_shot("s02_c01")
    req = build_bound_video_input(pdir, project, shot)
    assert req.provider_mode == "multi"
    assert req.video_reference == "http://example/s01_tail.png"
    assert req.first_frame == "" and req.last_frame is None
    reasons = [d["reason"] for d in req.diagnostics]
    assert "multi_relay_reference" in reasons
    assert req.semantic_roles[0]["source_type"] == "prev_tail_relay"
    assert req.semantic_roles[0]["source_shot_id"] == "s01_c02"
    assert req.used_reference_ids == ["relay:s01_c02:vv_prev000001"]
    # freshness 对称：build 的 visual_basis_fingerprint == 重算侧
    from src.generation.native_av_pack import compute_current_dependency_keys
    keys = compute_current_dependency_keys(pdir, project, shot)
    assert req.generation_basis["visual_basis_fingerprint"] == \
        keys["visual_basis_fingerprint"]


def test_multi_relay_prev_missing_end_frame_falls_back(venv):
    """上一镜无末帧 → relay 解析 None → 回落确定性 look hero 规则（不中断）。"""
    pdir, ids, svc, fake = venv
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    from src.core.models import Shot
    proj = ProjectStore(pdir).load()
    proj.scenes[0].shots.append(Shot(shot_id="s02_c01", scene_id="s01", index=2))
    ProjectStore(pdir).save(proj)
    r_s2 = _bind(svc, shot_id="s02_c01", inst=ids["inst"], view=ids["view_a"],
                 characters=[])
    assert r_s2["ok"], r_s2
    # 场景参考回退需要 SIV 资产带公网 URL（同 scene-only 测试手法）
    from src.resources.models import ReferenceAsset
    rd = ResourceStore(pdir, pdir.name).load()
    rd.reference_assets["ref_sciv0000001"] = ReferenceAsset(
        asset_id="ref_sciv0000001", url="http://example/sciv.png")
    ResourceStore(pdir, pdir.name).save(rd)
    _link_sciv_asset(pdir, ids, "ref_sciv0000001")
    def _mut2(s):
        s.video_reference_asset_id = "prev_tail"
        s.link_mode = "multi"
        s.video.num_frames = 73
        s.video.frame_rate = 24
    ProjectStore(pdir).update_shot("s02_c01", _mut2)
    req = build_bound_video_input(pdir, _load_project(pdir),
                                  _load_project(pdir).get_shot("s02_c01"))
    # s01_c02 无 video_end_frame → relay=None → 空 cast 镜回落场景参考（确定性规则）
    assert req.video_reference == "http://example/sciv.png"
    assert req.semantic_roles[0]["source_type"] == "scene_instance_view"


def test_multi_relay_cross_scene_resolution():
    """跨场景接力解析（Pilot 实际形态：每段 1 镜）——上一镜=上一场景最后一镜。
    V1C 首跑实证 bug 回归：同场景 idx>0 找 prev → 每段 1 镜时永远 None →
    静默回落 hero。resolver 按影片顺序展平解析（完整 build 路径由同场景
    relay 测试覆盖）。"""
    from src.core.models import Project, Scene, Shot, Asset, ShotStatus
    from src.generation.video_adapter import resolve_multi_relay_source
    proj = Project(name="p")
    s1 = Scene(scene_id="s01", shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1)])
    s2 = Scene(scene_id="s02", shots=[Shot(shot_id="s02_c01", scene_id="s02", index=1)])
    proj.scenes = [s1, s2]
    s1.shots[0].video_end_frame = Asset(url="http://example/s01_tail.png",
                                        status=ShotStatus.DONE.value)
    s1.shots[0].video_end_source_version = "vv_prev000001"
    target = s2.shots[0]
    target.video_reference_asset_id = "prev_tail"
    r = resolve_multi_relay_source(proj, target)
    assert r is not None
    assert r["source_shot_id"] == "s01_c01"
    assert r["url"] == "http://example/s01_tail.png"
    # 非 relay / 首镜无上一镜 → None
    target.video_reference_asset_id = None
    assert resolve_multi_relay_source(proj, target) is None
    first = s1.shots[0]
    first.video_reference_asset_id = "prev_tail"
    assert resolve_multi_relay_source(proj, first) is None




# ============ 42.md §二缺陷⑤：Look+尾帧双参考（哨兵 prev_tail_look）============

def test_multi_relay_identity_dual_reference(venv):
    """42.md 缺陷⑤：vref=prev_tail_look → multi 参考=[上镜尾帧, 本镜 hero Look]
    双参考；roles/used_ids 含 character_look；prompt 带 <Picture 1/2> 指称；
    编译 image_urls 顺序=尾帧在前；freshness 对称（identity 键两侧同源）。"""
    pdir, ids, svc, fake = venv
    from src.core.models import Scene, Shot, Asset, ShotStatus
    _bind(svc, inst=ids["inst"], view=ids["view_a"])   # 默认 cast=Alice+LOOK
    proj = ProjectStore(pdir).load()
    proj.scenes[0].shots.append(Shot(shot_id="s02_c01", scene_id="s01", index=2,
                                     characters=["Alice"]))
    ProjectStore(pdir).save(proj)
    r_s2 = _bind(svc, shot_id="s02_c01", inst=ids["inst"], view=ids["view_a"])
    assert r_s2["ok"], r_s2
    def _mut(s):
        s.video_end_frame = Asset(url="http://example/s01_tail.png",
                                  status=ShotStatus.DONE.value)
        s.video_end_source_version = "vv_prev000001"
    ProjectStore(pdir).update_shot("s01_c02", _mut)
    def _mut2(s):
        s.video_reference_asset_id = "prev_tail_look"
        s.link_mode = "multi"
        s.video.num_frames = 73
        s.video.frame_rate = 24
    ProjectStore(pdir).update_shot("s02_c01", _mut2)
    project = _load_project(pdir)
    shot = project.get_shot("s02_c01")
    req = build_bound_video_input(pdir, project, shot)
    assert req.provider_mode == "multi"
    assert req.video_reference == "http://example/s01_tail.png"
    assert req.identity_reference is not None
    assert req.identity_reference["url"] == "http://example/alice.png"
    assert req.identity_reference["reference_asset_id"] == "ref_alice000001"
    assert [r["source_type"] for r in req.semantic_roles] == \
        ["prev_tail_relay", "character_look"]
    assert req.used_reference_ids == ["relay:s01_c02:vv_prev000001",
                                      "ref_alice000001"]
    reasons = [d["reason"] for d in req.diagnostics]
    assert "relay_identity_reference_applied" in reasons
    # <Picture N> 指称只出现在双参考路径
    assert "<Picture 1>" in req.prompt and "<Picture 2>" in req.prompt
    # 投影：两个 reference_inputs，尾帧在前
    gen = req.to_generation_request(model=None)
    assert [r.source_type for r in gen.reference_inputs] == \
        ["video_reference", "character_look"]
    call = compile_agnes_video_call(gen)
    assert call.slots() == {"image_urls": ["http://example/s01_tail.png",
                                           "http://example/alice.png"]}
    # freshness 对称：identity 键两侧同源
    from src.generation.native_av_pack import compute_current_dependency_keys
    keys = compute_current_dependency_keys(pdir, project, shot)
    assert req.generation_basis["visual_basis_fingerprint"] == \
        keys["visual_basis_fingerprint"]


def test_multi_relay_identity_fingerprint_differs_from_plain_relay(venv):
    """prev_tail 与 prev_tail_look 的 visual 指纹必须不同（identity 键参与）。"""
    pdir, ids, svc, fake = venv
    from src.core.models import Scene, Shot, Asset, ShotStatus
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    proj = ProjectStore(pdir).load()
    proj.scenes[0].shots.append(Shot(shot_id="s02_c01", scene_id="s01", index=2,
                                     characters=["Alice"]))
    ProjectStore(pdir).save(proj)
    assert _bind(svc, shot_id="s02_c01", inst=ids["inst"],
                 view=ids["view_a"])["ok"]
    ProjectStore(pdir).update_shot("s01_c02", lambda s: (
        setattr(s.video_end_frame, "url", "http://example/s01_tail.png"),
        setattr(s.video_end_frame, "status", ShotStatus.DONE.value),
        setattr(s, "video_end_source_version", "vv_prev000001")))
    fps = {}
    for sentinel in ("prev_tail", "prev_tail_look"):
        ProjectStore(pdir).update_shot("s02_c01", lambda s, sn=sentinel: (
            setattr(s, "video_reference_asset_id", sn),
            setattr(s, "link_mode", "multi"),
            setattr(s.video, "num_frames", 73),
            setattr(s.video, "frame_rate", 24)))
        project = _load_project(pdir)
        shot = project.get_shot("s02_c01")
        req = build_bound_video_input(pdir, project, shot)
        fps[sentinel] = req.generation_basis["visual_basis_fingerprint"]
    assert fps["prev_tail"] != fps["prev_tail_look"]


def test_multi_relay_identity_fail_closed_without_look(venv):
    """哨兵已设但本镜 Binding 无 character_look → 结构化拒绝（不静默跳过）。"""
    pdir, ids, svc, fake = venv
    from src.core.models import Scene, Shot, Asset, ShotStatus
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    proj = ProjectStore(pdir).load()
    proj.scenes[0].shots.append(Shot(shot_id="s02_c01", scene_id="s01", index=2))
    ProjectStore(pdir).save(proj)
    assert _bind(svc, shot_id="s02_c01", inst=ids["inst"], view=ids["view_a"],
                 characters=[])["ok"]
    ProjectStore(pdir).update_shot("s01_c02", lambda s: (
        setattr(s.video_end_frame, "url", "http://example/s01_tail.png"),
        setattr(s.video_end_frame, "status", ShotStatus.DONE.value),
        setattr(s, "video_end_source_version", "vv_prev000001")))
    ProjectStore(pdir).update_shot("s02_c01", lambda s: (
        setattr(s, "video_reference_asset_id", "prev_tail_look"),
        setattr(s, "link_mode", "multi")))
    project = _load_project(pdir)
    shot = project.get_shot("s02_c01")
    with pytest.raises(BoundGenerationError) as ei:
        build_bound_video_input(pdir, project, shot)
    assert ei.value.code == "relay_identity_reference_unresolvable"


def test_relay_identity_sentinel_pure_function():
    """resolve_multi_relay_source：prev_tail_look → identity=True；prev_tail →
    identity=False；未设 → None（与 345 测试同夹具手法）。"""
    from src.generation.video_adapter import resolve_multi_relay_source
    from src.core.models import Project, Scene, Shot, Asset, ShotStatus
    proj = Project(name="p")
    s1 = Scene(scene_id="s01", shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1)])
    s2 = Scene(scene_id="s02", shots=[Shot(shot_id="s02_c01", scene_id="s02", index=1)])
    proj.scenes = [s1, s2]
    s1.shots[0].video_end_frame = Asset(url="http://example/s01_tail.png",
                                        status=ShotStatus.DONE.value)
    target = s2.shots[0]
    target.video_reference_asset_id = "prev_tail_look"
    r = resolve_multi_relay_source(proj, target)
    assert r is not None and r["identity"] is True
    target.video_reference_asset_id = "prev_tail"
    assert resolve_multi_relay_source(proj, target)["identity"] is False
    target.video_reference_asset_id = None
    assert resolve_multi_relay_source(proj, target) is None


# ============ 43.md §二：尾帧+Look+场景三参考（哨兵 prev_tail_look_scene）============

def _give_siv_public_url(pdir):
    """测试 SIV 资产无公网 URL（FakeClient b64）——注入公网 URL 以过视频槽约束。"""
    from src.resources.store import ResourceStore
    import json as _json
    ss = _json.loads((pdir / "output" / "scene_system.json").read_text(encoding="utf-8"))
    rdoc = ResourceStore(pdir, pdir.name).load()
    for siv in (ss.get("scene_instance_views") or {}).values():
        rid = siv.get("reference_asset_id")
        if rid and rid in rdoc.reference_assets and not rdoc.reference_assets[rid].url:
            rdoc.reference_assets[rid].url = "http://example/siv.png"
    ResourceStore(pdir, pdir.name).save(rdoc)


def test_multi_relay_scene_triple_reference(venv):
    """43.md §二：vref=prev_tail_look_scene → 三参考=[尾帧, Look, SIV]；
    roles/used_ids 含 scene_instance_view；prompt 带 <Picture 1/2/3>；投影+
    编译 image_urls 顺序=尾帧→Look→SIV；freshness 对称（scene 键两侧同源）。"""
    pdir, ids, svc, fake = venv
    from src.core.models import Scene, Shot, Asset, ShotStatus
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _give_siv_public_url(pdir)
    proj = ProjectStore(pdir).load()
    proj.scenes[0].shots.append(Shot(shot_id="s02_c01", scene_id="s01", index=2,
                                     characters=["Alice"]))
    ProjectStore(pdir).save(proj)
    assert _bind(svc, shot_id="s02_c01", inst=ids["inst"],
                 view=ids["view_a"])["ok"]
    ProjectStore(pdir).update_shot("s01_c02", lambda s: (
        setattr(s.video_end_frame, "url", "http://example/s01_tail.png"),
        setattr(s.video_end_frame, "status", ShotStatus.DONE.value),
        setattr(s, "video_end_source_version", "vv_prev000001")))
    ProjectStore(pdir).update_shot("s02_c01", lambda s: (
        setattr(s, "video_reference_asset_id", "prev_tail_look_scene"),
        setattr(s, "link_mode", "multi"),
        setattr(s.video, "num_frames", 73),
        setattr(s.video, "frame_rate", 24)))
    project = _load_project(pdir)
    shot = project.get_shot("s02_c01")
    req = build_bound_video_input(pdir, project, shot)
    assert req.identity_reference is not None
    assert req.scene_reference is not None
    assert req.scene_reference["url"] == "http://example/siv.png"
    assert [r["source_type"] for r in req.semantic_roles] == \
        ["prev_tail_relay", "character_look", "scene_instance_view"]
    assert req.used_reference_ids[-1] == req.scene_reference["reference_asset_id"]
    assert "<Picture 1>" in req.prompt and "<Picture 2>" in req.prompt \
        and "<Picture 3>" in req.prompt
    gen = req.to_generation_request(model=None)
    assert [r.source_type for r in gen.reference_inputs] == \
        ["video_reference", "character_look", "scene_instance_view"]
    call = compile_agnes_video_call(gen)
    assert call.slots() == {"image_urls": ["http://example/s01_tail.png",
                                           "http://example/alice.png",
                                           "http://example/siv.png"]}
    from src.generation.native_av_pack import compute_current_dependency_keys
    keys = compute_current_dependency_keys(pdir, project, shot)
    assert req.generation_basis["visual_basis_fingerprint"] == \
        keys["visual_basis_fingerprint"]


def test_multi_relay_scene_fingerprint_differs_from_dual(venv):
    """prev_tail_look 与 prev_tail_look_scene 的 visual 指纹必须不同（scene 键）。"""
    pdir, ids, svc, fake = venv
    from src.core.models import Scene, Shot
    _bind(svc, inst=ids["inst"], view=ids["view_a"])
    _give_siv_public_url(pdir)
    proj = ProjectStore(pdir).load()
    proj.scenes[0].shots.append(Shot(shot_id="s02_c01", scene_id="s01", index=2,
                                     characters=["Alice"]))
    ProjectStore(pdir).save(proj)
    assert _bind(svc, shot_id="s02_c01", inst=ids["inst"],
                 view=ids["view_a"])["ok"]
    ProjectStore(pdir).update_shot("s01_c02", lambda s: (
        setattr(s.video_end_frame, "url", "http://example/s01_tail.png"),
        setattr(s, "video_end_source_version", "vv_prev000001")))
    fps = {}
    for sentinel in ("prev_tail_look", "prev_tail_look_scene"):
        ProjectStore(pdir).update_shot("s02_c01", lambda s, sn=sentinel: (
            setattr(s, "video_reference_asset_id", sn),
            setattr(s, "link_mode", "multi"),
            setattr(s.video, "num_frames", 73),
            setattr(s.video, "frame_rate", 24)))
        project = _load_project(pdir)
        shot = project.get_shot("s02_c01")
        req = build_bound_video_input(pdir, project, shot)
        fps[sentinel] = req.generation_basis["visual_basis_fingerprint"]
    assert fps["prev_tail_look"] != fps["prev_tail_look_scene"]


def test_relay_scene_sentinel_pure_function():
    """prev_tail_look_scene → identity=True + scene=True（双开关同真）。"""
    from src.generation.video_adapter import resolve_multi_relay_source
    from src.core.models import Project, Scene, Shot, Asset, ShotStatus
    proj = Project(name="p")
    s1 = Scene(scene_id="s01", shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1)])
    s2 = Scene(scene_id="s02", shots=[Shot(shot_id="s02_c01", scene_id="s02", index=1)])
    proj.scenes = [s1, s2]
    s1.shots[0].video_end_frame = Asset(url="http://example/s01_tail.png",
                                        status=ShotStatus.DONE.value)
    target = s2.shots[0]
    target.video_reference_asset_id = "prev_tail_look_scene"
    r = resolve_multi_relay_source(proj, target)
    assert r is not None and r["identity"] is True and r["scene"] is True


def test_relay_scene_reference_resolver_fail_closed():
    """pack 无 SIV / 媒体不可解析 → 结构化拒绝（不静默跳过场景锚定）。"""
    from src.generation.video_adapter import _resolve_relay_scene_reference
    with pytest.raises(BoundGenerationError) as ei:
        _resolve_relay_scene_reference({"references": []}, object(), [])
    assert ei.value.code == "relay_scene_reference_unresolvable"
