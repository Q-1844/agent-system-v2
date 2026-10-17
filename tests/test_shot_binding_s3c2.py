# tests/test_shot_binding_s3c2.py
# Shot Production Binding V1 — Phase S3C.2 验收测试（12.md §26-§42）。
#
# 核心：真实 video / last-frame 生成路径消费 COMPILED ShotReferencePack；
# last-frame temporal 规则（end/throughout/static 进、start 排除，先于 priority）；
# boundary-frame provenance（generation_basis freshness，缺失/不一致 fail closed）；
# BOUND+NOT_READY 绝不 legacy fallback；provider capability 不伪造（i2v 无 last-frame
# 槽 → end state 仅语义 target）；pre-dispatch race guard 延伸到 video；
# 全程 Fake provider 零外部调用（§25/§34）。
import asyncio
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import deps
from src.core.models import Asset, Candidate, ImageBatch, OpeningSource
from src.core.prompt_builder import (
    build_first_frame_prompt,
    build_last_frame_prompt,
    build_video_prompt,
)
from src.core.store import ProjectStore
from src.core.write_lock import _clear_for_test
from src.generation.image_adapter import (
    BoundGenerationError,
    boundary_stale_detail,
    build_bound_first_frame_input,
    build_bound_last_frame_input,
    filter_first_frame_references,
    filter_frame_references,
    selected_boundary_batch,
)
from src.generation.video_adapter import build_bound_video_input
from src.resources.store import ResourceStore
from src.shot_binding import ShotBindingService
from src.shot_binding.validity import READY

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))
from test_shot_binding_s3b1 import (  # noqa: E402
    LOOK, LOOK_BOB, make_pdir, run,
)
from test_shot_binding_s3b2 import (  # noqa: E402
    _bind, _build_scene,
)
from test_shot_binding_s3b3 import (  # noqa: E402
    PISTOL, _add_pistol_with_comp,
)
from routers.shots import (  # noqa: E402
    _collect_first_frame_refs,
    _effective_first_frame_url,
    _handler_first_frame,
    _handler_last_frame,
    _handler_video,
    _legacy_vp_style_suffix,
    _shot_char_refs,
)

PISTOL_COMP = "comp_pistol000001"
FF_URL = "https://mock.agnes/first_frame.png"
LF_URL = "https://mock.agnes/last_frame.png"


def _sha(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def _hash_all(pdir: Path) -> dict:
    return {n: _sha(pdir / "output" / n) for n in
            ("project.json", "resources.json", "scene_system.json",
             "project_profile.json", "shot_production_bindings.json")}


class FakeMediaClient:
    """§25 Fake provider：image+video 全记录（0 真实外部调用）。"""
    quota = None

    def __init__(self):
        self.image_calls = []
        self.video_calls = []
        self.poll_calls = []

    async def generate_image(self, *, prompt, image_refs=None, size_tier="1K",
                             ratio="16:9", **kw):
        self.image_calls.append({"prompt": prompt,
                                 "image_refs": list(image_refs or []),
                                 "size_tier": size_tier, "ratio": ratio})
        return {"url": "https://mock.agnes/out.png"}

    async def create_video(self, prompt, *, mode="i2v", image_url=None,
                           image_urls=None, num_frames=121, frame_rate=24,
                           width=1312, height=736, seed=None,
                           negative_prompt="", model=None, duration_s=None, **kw):
        self.video_calls.append({
            "prompt": prompt, "mode": mode, "image_url": image_url,
            "image_urls": list(image_urls) if image_urls else None,
            "num_frames": num_frames, "frame_rate": frame_rate,
            "width": width, "height": height, "seed": seed,
            "negative_prompt": negative_prompt, "model": model,
            "duration_s": duration_s})
        return {"video_id": "vid_s3c20000001", "task_id": "task_s3c2"}

    async def poll_video(self, video_id, *, interval=5.0, max_wait=3600,
                         on_progress=None, max_consecutive_failures=5,
                         model_name=None):
        self.poll_calls.append({"video_id": video_id, "model_name": model_name})
        return {"url": "https://mock.agnes/out.mp4", "seconds": 3.0,
                "size": "1280x704", "raw": {}}

    async def download(self, url, dest):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake mp4 bytes")
        return str(dest)


class _StubPM:
    def __init__(self, pdir):
        self._pdir = pdir

    def get_project_dir(self, name):
        return self._pdir


@pytest.fixture()
def venv(tmp_path, monkeypatch):
    """真实跨域夹具 + deps stub + Fake provider。默认绑定：c01→(inst, view_a)。"""
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


def _bind_default(svc, ids, **kw):
    return _bind(svc, inst=ids["inst"], view=ids["view_a"], **kw)


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


def _bind_put_down(svc, ids):
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
    return rr


def _load_shot(pdir, shot_id="s01_c01"):
    return ProjectStore(pdir).load().get_shot(shot_id)


def _load_project(pdir):
    return ProjectStore(pdir).load()


def _current_basis(svc, shot_id="s01_c01") -> dict:
    compiled = svc.compile_reference_pack(shot_id)
    assert compiled["status"] == "COMPILED", compiled
    return dict(compiled["pack"]["basis"])


def _seed_first_frame(pdir, shot_id="s01_c01", *, basis=None, url=FF_URL,
                      candidate_id="ff_seed0001", link_mode=None):
    """seed 一个「已选择」的首帧批次（§16 selection truth：selected 指针 + 批次候选）。
    basis=None → 无 provenance（legacy 帧场景）。"""
    project = _load_project(pdir)

    def mut(s, b=None):
        if link_mode:
            s.link_mode = link_mode
        s.first_frame_batches.append(ImageBatch(
            batch_id="ffb_seed01", kind="first_frame", created_at=1.0,
            status="done",
            candidates=[Candidate(candidate_id=candidate_id, url=url,
                                  status="done")],
            generation_basis=dict(basis) if basis else {}))
        s.selected_first_frame = candidate_id
        s.first_frame.candidates = [Asset(local=None, url=url, status="done")]
        s.first_frame.selected_index = 0
        s.first_frame.status = "done"
    ProjectStore(pdir).update_shot(shot_id, mut)


def _seed_last_frame(pdir, shot_id="s01_c01", *, basis=None, url=LF_URL,
                     candidate_id="lf_seed0001"):
    project = _load_project(pdir)

    def mut(s):
        s.last_frame_batches.append(ImageBatch(
            batch_id="lfb_seed01", kind="last_frame", created_at=1.0,
            status="done",
            candidates=[Candidate(candidate_id=candidate_id, url=url,
                                  status="done")],
            generation_basis=dict(basis) if basis else {}))
        s.selected_last_frame = candidate_id
        s.last_frame.candidates = [Asset(local=None, url=url, status="done")]
        s.last_frame.selected_index = 0
        s.last_frame.status = "done"
    ProjectStore(pdir).update_shot(shot_id, mut)


def _set_video_ready(pdir, shot_id="s01_c01", *, num_frames=97, link_mode=None):
    def mut(s):
        s.video.num_frames = num_frames
        s.video.frame_rate = 24
        if link_mode:
            s.link_mode = link_mode
    ProjectStore(pdir).update_shot(shot_id, mut)


def _run_video_handler(pdir, shot_id="s01_c01"):
    async def emit(*a, **k):
        pass
    job = SimpleNamespace(project=pdir.name, shot_id=shot_id, params={},
                          kind="video")
    asyncio.run(_handler_video(job, emit))


def _run_last_frame_handler(pdir, shot_id="s01_c01"):
    async def emit(*a, **k):
        pass
    job = SimpleNamespace(project=pdir.name, shot_id=shot_id, params={},
                          kind="last_frame")
    asyncio.run(_handler_last_frame(job, emit))


def _run_first_frame_handler(pdir, shot_id="s01_c01", variants=1):
    async def emit(*a, **k):
        pass
    job = SimpleNamespace(project=pdir.name, shot_id=shot_id,
                          params={"variants": variants}, kind="first_frame")
    asyncio.run(_handler_first_frame(job, emit))


# ============ B. READY gate（§27）============

def test_b1_ready_video_adapter_and_provider_called_once(venv):
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    req = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert req.provider_mode == "i2v"
    assert req.first_frame == FF_URL
    assert req.basis["binding_revision"] == \
        svc.read_binding("s01_c01")["binding_revision"]
    _run_video_handler(pdir)
    assert len(fake.video_calls) == 1, "READY → Fake provider 恰好一次"
    assert fake.video_calls[0]["image_url"] == FF_URL


def test_b2_not_ready_video_provider_never_called(venv):
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    rd = ResourceStore(pdir, pdir.name).load()
    rd.looks[LOOK].approved_reference_asset_ids = []   # revoke → NOT_READY
    ResourceStore(pdir, pdir.name).save(rd)
    with pytest.raises(BoundGenerationError) as ei:
        _run_video_handler(pdir)
    assert ei.value.code == "binding_not_ready"
    assert fake.video_calls == [], "provider 调用数 = 0"


def test_b3_bound_stale_never_legacy_fallback(venv):
    """§21：BOUND stale → 绝不产生任何 legacy 提交（无 legacy refs/无 legacy prompt）。"""
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir)          # 无 provenance → stale（fail closed）
    _set_video_ready(pdir)
    with pytest.raises(BoundGenerationError) as ei:
        _run_video_handler(pdir)
    assert ei.value.code == "boundary_frame_stale"
    assert fake.video_calls == [], "BOUND stale 绝不 fallback legacy 提交"


def test_b4_unbound_legacy_video_behavior_identical(venv):
    pdir, ids, svc, fake = venv
    _seed_first_frame(pdir)
    _set_video_ready(pdir, num_frames=89)
    ProjectStore(pdir).update_shot("s01_c01",
                                   lambda s: (setattr(s, "seed", 42),
                                              setattr(s, "negative_prompt", "blurry")))
    _run_video_handler(pdir)
    assert len(fake.video_calls) == 1
    call = fake.video_calls[0]
    project = _load_project(pdir)
    assert call["prompt"] == build_video_prompt(project.get_shot("s01_c01"), "i2v")
    assert call["image_url"] == _effective_first_frame_url(project,
                                                           project.get_shot("s01_c01"))
    assert call["image_urls"] is None, "i2v 不伪造 last-frame 槽"
    assert call["mode"] == "i2v"
    assert call["seed"] == 42 and call["negative_prompt"] == "blurry"


# ============ C. Last-frame temporal selection（§28）============

def test_c1_last_frame_filter_temporal_before_priority():
    refs = [
        {"source_id": "a", "temporal_scope": "end", "priority": "supporting"},
        {"source_id": "b", "temporal_scope": "throughout", "priority": "primary"},
        {"source_id": "c", "temporal_scope": "static", "priority": "context"},
        {"source_id": "d", "temporal_scope": "start", "priority": "primary"},  # 关键反例
    ]
    eligible, excluded = filter_frame_references(refs, "last")
    assert [r["source_id"] for r in eligible] == ["a", "b", "c"], \
        "last: end/throughout/static 进"
    assert [r["source_id"] for r in excluded] == ["d"], \
        "temporal 先于 priority：primary+start 仍被排除"
    # first 口径的既有入口不受影响（S3C.1 镜像：start/throughout/static 进，end 排除）
    e1, x1 = filter_first_frame_references(refs)
    assert [r["source_id"] for r in e1] == ["b", "c", "d"]
    assert [r["source_id"] for r in x1] == ["a"]


def test_c2_last_frame_adapter_pick_up_temporal_roles(venv):
    pdir, ids, svc, fake = venv
    _add_pistol_with_comp(pdir, PISTOL_COMP, "Alice", LOOK)
    _bind_pick_up(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc), link_mode="keyframes")
    req = build_bound_last_frame_input(pdir, _load_project(pdir),
                                       _load_shot(pdir), first_frame_url=FF_URL)
    roles = [(r["source_type"], r["temporal_scope"]) for r in req.semantic_roles]
    assert ("composition", "end") in roles, "持枪组合=end 态证据→进尾帧"
    assert ("character_look", "start") not in roles, "Look(start) 绝不进尾帧"
    assert ("scene_instance_view", "throughout") in roles
    # §14：尾帧 prompt 只含 end_state，绝不含 start_state
    assert "right hand holding the pistol" in req.prompt
    assert "right hand empty" not in req.prompt
    assert req.image_refs[0] == FF_URL, "构图基础首帧始终第一张"
    assert any("composition" == r["source_type"] for r in req.semantic_roles)


# ============ D. pick_up 双边界（§29）============

def test_d1_pick_up_video_i2v_end_ref_never_conditions_first_slot(venv):
    """I2V-only provider：end reference 绝不污染 current/start image conditioning
    ——video 请求只有 first frame 视觉槽；end state 仅语义 target（§8）。"""
    pdir, ids, svc, fake = venv
    _add_pistol_with_comp(pdir, PISTOL_COMP, "Alice", LOOK)
    _bind_pick_up(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    req = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert req.provider_mode == "i2v"
    assert req.last_frame is None, "i2v 无 last-frame 槽（不伪造）"
    assert req.first_frame == FF_URL, "首槽=start-compatible frame"
    assert any(d["reason"] == "end_state_semantic_target_only" for d in req.diagnostics), \
        "如实报告：end state is semantic target only"
    assert "right hand empty" in req.prompt and \
        "right hand holding the pistol" in req.prompt, \
        "video 生产上下文表达 start → end motion target"


def test_d2_pick_up_keyframes_slots_correctly_mapped(venv):
    """first+last 双帧 provider（v2.0 真实 keyframes 模式）：
    first slot ← start-compatible；last slot ← end-compatible（§22）。"""
    pdir, ids, svc, fake = venv
    _add_pistol_with_comp(pdir, PISTOL_COMP, "Alice", LOOK)
    _bind_pick_up(svc, ids)
    basis = _current_basis(svc)
    _seed_first_frame(pdir, basis=basis)
    _seed_last_frame(pdir, basis=basis)
    _set_video_ready(pdir, link_mode="keyframes")
    req = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert req.provider_mode == "keyframes"
    assert req.first_frame == FF_URL and req.last_frame == LF_URL
    _run_video_handler(pdir)
    assert len(fake.video_calls) == 1
    assert fake.video_calls[0]["image_urls"] == [FF_URL, LF_URL], \
        "first/last 没放反"


# ============ E. put_down（§30）============

def test_e1_put_down_last_frame_excludes_start_only_evidence(venv):
    pdir, ids, svc, fake = venv
    _add_pistol_with_comp(pdir, PISTOL_COMP, "Alice", LOOK)
    _bind_put_down(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc), link_mode="keyframes")
    req = build_bound_last_frame_input(pdir, _load_project(pdir),
                                       _load_shot(pdir), first_frame_url=FF_URL)
    roles = [(r["source_type"], r["temporal_scope"]) for r in req.semantic_roles]
    assert ("composition", "start") not in roles, \
        "start-only 持物组合绝不作为 end current-state reference"
    assert ("character_look", "end") in roles, "end Look 进入尾帧"
    assert "empty hand" in req.prompt, "end_state 进入尾帧 prompt"
    assert "holding pistol" not in req.prompt, "start_state 绝不进入尾帧 prompt"


def test_e2_put_down_first_frame_holding_eligible_and_video_mirror(venv):
    pdir, ids, svc, fake = venv
    _add_pistol_with_comp(pdir, PISTOL_COMP, "Alice", LOOK)
    _bind_put_down(svc, ids)
    basis = _current_basis(svc)
    first_req = build_bound_first_frame_input(pdir, _load_project(pdir),
                                              _load_shot(pdir))
    roles = [(r["source_type"], r["temporal_scope"]) for r in first_req.semantic_roles]
    assert ("composition", "start") in roles, "put_down 首帧持物组合 eligible"
    assert ("character_look", "end") not in roles
    _seed_first_frame(pdir, basis=basis)
    vreq = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert "holding pistol" in vreq.prompt and "empty hand" in vreq.prompt


# ============ F. hand_off（§31）============

def _add_bob_comp_pistol(pdir):
    from src.resources.models import (
        CharacterReferenceComposition, CompositionPropBinding, ReferenceAsset)
    rd = ResourceStore(pdir, pdir.name).load()
    rd.compositions["comp_bob_pistol1"] = CharacterReferenceComposition(
        composition_id="comp_bob_pistol1", character_name="Bob", look_id=LOOK_BOB,
        prop_bindings=[CompositionPropBinding(prop_id=PISTOL)],
        approval_status="approved")
    rd.reference_assets["ref_comp_bob_pistol1"] = ReferenceAsset(
        asset_id="ref_comp_bob_pistol1", url="http://example/comp_bob_pistol1.png")
    rd.compositions["comp_bob_pistol1"].approved_reference_asset_ids = \
        ["ref_comp_bob_pistol1"]
    rd.compositions["comp_bob_pistol1"].cover_asset_id = "ref_comp_bob_pistol1"
    ResourceStore(pdir, pdir.name).save(rd)


def _add_handoff_shot(pdir):
    from src.core.models import Shot
    proj = _load_project(pdir)
    if not any(s.shot_id == "s01_c05" for s in proj.scenes[0].shots):
        proj.scenes[0].shots.append(Shot(shot_id="s01_c05", scene_id="s01",
                                         index=9, characters=["Alice", "Bob"]))
        ProjectStore(pdir).save(proj)


def _bind_handoff(svc, ids, alice_comp, bob_comp):
    _add_handoff_shot(_pdir_holder[0])
    characters = [
        {"character_name": "Alice", "look_id": LOOK, "composition_id": alice_comp},
        {"character_name": "Bob", "look_id": LOOK_BOB, "composition_id": bob_comp},
    ]
    r = _bind(svc, shot_id="s01_c05", inst=ids["inst"], view=ids["view_a"],
              characters=characters)
    rr = svc.replace_binding("s01_c05", {
        "shot_id": "s01_c05", "scene_id": "s01",
        "scene_binding": {"scene_instance_id": ids["inst"],
                          "location_view_id": ids["view_a"]},
        "character_bindings": characters,
        "prop_bindings": [],
        "action": {"kind": "hand_off", "subject_character": "Alice",
                   "prop_id": PISTOL, "start_state": "pistol held by Alice",
                   "end_state": "pistol held by Bob"},
    }, expected_binding_revision=r["binding_revision"])
    assert rr["ok"], rr


_pdir_holder = [None]


def test_f1_hand_off_start_to_first_end_to_last(venv):
    pdir, ids, svc, fake = venv
    _pdir_holder[0] = pdir
    _add_pistol_with_comp(pdir, PISTOL_COMP, "Alice", LOOK)
    _add_bob_comp_pistol(pdir)
    _bind_handoff(svc, ids, PISTOL_COMP, "comp_bob_pistol1")
    _seed_first_frame(pdir, shot_id="s01_c05", basis=_current_basis(svc, "s01_c05"),
                      link_mode="keyframes")
    shot = _load_shot(pdir, "s01_c05")
    first_req = build_bound_first_frame_input(pdir, _load_project(pdir), shot)
    f_roles = {(r["subject_character"], r["temporal_scope"])
               for r in first_req.semantic_roles if r["source_type"] == "composition"}
    assert ("Alice", "start") in f_roles, "start relation → first"
    assert ("Bob", "end") not in f_roles
    last_req = build_bound_last_frame_input(pdir, _load_project(pdir), shot,
                                            first_frame_url=FF_URL)
    l_roles = {(r["subject_character"], r["temporal_scope"])
               for r in last_req.semantic_roles if r["source_type"] == "composition"}
    assert ("Bob", "end") in l_roles, "end relation → last"
    assert ("Alice", "start") not in l_roles
    # video：start → end 双 holder 语义进 motion target
    _seed_last_frame(pdir, shot_id="s01_c05", basis=_current_basis(svc, "s01_c05"))
    _set_video_ready(pdir, "s01_c05", link_mode="keyframes")
    vreq = build_bound_video_input(pdir, _load_project(pdir),
                                   _load_shot(pdir, "s01_c05"))
    assert "pistol held by Alice" in vreq.prompt and \
        "pistol held by Bob" in vreq.prompt


def test_f2_hand_off_ambiguous_adapter_never_reinterprets(venv):
    pdir, ids, svc, fake = venv
    _pdir_holder[0] = pdir
    _add_pistol_with_comp(pdir, PISTOL_COMP, "Alice", LOOK)
    _bind_handoff(svc, ids, PISTOL_COMP, None)   # Bob 无组合 → 歧义
    assert svc.compile_reference_pack("s01_c05")["status"] == "BLOCKED"
    shot = _load_shot(pdir, "s01_c05")
    with pytest.raises(BoundGenerationError):
        build_bound_first_frame_input(pdir, _load_project(pdir), shot)
    with pytest.raises(BoundGenerationError):
        build_bound_last_frame_input(pdir, _load_project(pdir), shot,
                                     first_frame_url=FF_URL)
    with pytest.raises(BoundGenerationError):
        build_bound_video_input(pdir, _load_project(pdir), shot)
    assert fake.video_calls == [] and fake.image_calls == [], \
        "歧义 case provider 调用数 = 0"


# ============ G. static（§32）============

def test_g1_static_eligible_in_both_boundaries_and_stable(venv):
    from test_shot_binding_s3b1 import COMP
    from test_shot_binding_s3b3 import _give_plate
    pdir, ids, svc, fake = venv
    _give_plate(pdir, COMP, "ref_comp_plate1")
    _bind_default(svc, ids, composition=True)
    basis = _current_basis(svc)
    _seed_first_frame(pdir, basis=basis, link_mode="keyframes")
    shot = _load_shot(pdir)
    first_req = build_bound_first_frame_input(pdir, _load_project(pdir), shot)
    last_req = build_bound_last_frame_input(pdir, _load_project(pdir), shot,
                                            first_frame_url=FF_URL)
    assert any(r["source_type"] == "composition" and r["temporal_scope"] == "static"
               for r in first_req.semantic_roles), "static 组合 first eligible"
    assert any(r["source_type"] == "composition" and r["temporal_scope"] == "static"
               for r in last_req.semantic_roles), "static 组合 last eligible"
    # 不为首尾概念强行制造两个状态：请求稳定
    last_req2 = build_bound_last_frame_input(pdir, _load_project(pdir), shot,
                                             first_frame_url=FF_URL)
    assert last_req.to_dict() == last_req2.to_dict()


# ============ H. Scene（§33）============

def test_h1_scene_enters_boundary_and_video_production_context(venv):
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    basis = _current_basis(svc)
    _seed_first_frame(pdir, basis=basis, link_mode="keyframes")
    _seed_last_frame(pdir, basis=basis)
    shot = _load_shot(pdir)
    last_req = build_bound_last_frame_input(pdir, _load_project(pdir), shot,
                                            first_frame_url=FF_URL)
    assert any(r["source_type"] == "scene_instance_view" for r in last_req.semantic_roles), \
        "resolved SIV 进入边界帧 production context"
    assert "Scene: lab" in last_req.prompt, "场景行进入 prompt"
    vreq = build_bound_video_input(pdir, _load_project(pdir), shot)
    assert "Scene: lab" in vreq.prompt, "场景行真实进入 video prompt"
    assert any(d["reason"] == "pack_references_not_provider_consumed"
               for d in vreq.diagnostics), \
        "如实报告：provider 无 scene reference slot 的真实 mapping"


def test_h2_unrelated_view_change_video_request_unchanged(venv):
    from src.scene_system.store import SceneSystemStore
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    req1 = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    st = SceneSystemStore(pdir, pdir.name)
    d = st.load()
    other = [v for v in d.views.values() if v.view_id != ids["view_a"]][0]
    d.views[other.view_id].label = "unrelated rename"
    st.save(d)
    req2 = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert req1.to_dict() == req2.to_dict()


def test_h3_bound_siv_stale_video_provider_not_called(venv):
    from src.scene_system.service import SceneSystemService
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    ssvc = SceneSystemService(pdir, pdir.name)
    rp = run(ssvc.patch_scene_state(
        ids["inst"], {"anchor_states": {ids["a_door"]: {"condition": "blocked"}}},
        expected_revision=ssvc.read_scene_instance(ids["inst"])["revision"]))
    assert rp["ok"]
    with pytest.raises(BoundGenerationError) as ei:
        _run_video_handler(pdir)
    assert ei.value.code == "binding_not_ready"
    assert fake.video_calls == []


# ============ I. Visual Profile（§34）============

def test_i1_profile_replaces_styleanchor_in_video_and_last_frame(venv):
    pdir, ids, svc, fake = venv
    project = _load_project(pdir)
    project.style.art_style = "legacy oil painting"
    ProjectStore(pdir).save(project)
    _bind_default(svc, ids)
    basis = _current_basis(svc)
    _seed_first_frame(pdir, basis=basis, link_mode="keyframes")
    _seed_last_frame(pdir, basis=basis)
    shot = _load_shot(pdir)
    vreq = build_bound_video_input(pdir, _load_project(pdir), shot)
    assert "2D animated image" in vreq.prompt, "ProjectVisualProfile 生效"
    assert "legacy oil painting" not in vreq.prompt, "无 legacy StyleAnchor"
    lreq = build_bound_last_frame_input(pdir, _load_project(pdir), shot,
                                        first_frame_url=FF_URL)
    assert "2D animated image" in lreq.prompt
    assert "legacy oil painting" not in lreq.prompt


# ============ J. Artifact provenance（§35）============

def test_j5_handler_records_generation_basis(venv):
    """BOUND 生成路径把 Pack basis 记入 ImageBatch.generation_basis（Case C 最小修复）。"""
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _run_first_frame_handler(pdir)
    shot = _load_shot(pdir)
    batch = shot.first_frame_batches[-1]
    cur = _current_basis(svc)
    assert batch.generation_basis, "首帧批次记录 generation_basis"
    assert batch.generation_basis["binding_revision"] == cur["binding_revision"]
    assert batch.generation_basis["binding_readiness"] == "READY"
    # 尾帧
    ProjectStore(pdir).update_shot("s01_c01", lambda s: setattr(s, "link_mode",
                                                                "keyframes"))
    _run_last_frame_handler(pdir)
    shot = _load_shot(pdir)
    lbatch = shot.last_frame_batches[-1]
    assert lbatch.generation_basis["binding_revision"] == cur["binding_revision"], \
        "尾帧批次记录 generation_basis"


def test_j1_j2_j3_boundary_freshness_policies(venv):
    """J1 fresh → video allowed；J2 stale → 0 calls；J3 legacy 无 provenance → fail closed。"""
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    basis = _current_basis(svc)
    # J1 fresh
    _seed_first_frame(pdir, basis=basis)
    _set_video_ready(pdir)
    req = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert req.first_frame == FF_URL
    # J2 stale（旧 binding_revision）
    ProjectStore(pdir).update_shot("s01_c01", lambda s: (
        s.first_frame_batches.__setitem__(
            len(s.first_frame_batches) - 1,
            ImageBatch(batch_id="ffb_old1", kind="first_frame", created_at=1.0,
                       status="done",
                       candidates=[Candidate(candidate_id="ff_old1", url=FF_URL,
                                             status="done")],
                       generation_basis=dict(basis, binding_revision="deadbeef"))),
        setattr(s, "selected_first_frame", "ff_old1")))
    with pytest.raises(BoundGenerationError) as ei:
        build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert ei.value.code == "boundary_frame_stale"
    assert ei.value.detail["reason"] == "artifact_basis_mismatch"
    # J3 legacy 无 provenance
    ProjectStore(pdir).update_shot("s01_c01", lambda s: (
        s.first_frame_batches[-1].__setattr__("generation_basis", {}),))
    with pytest.raises(BoundGenerationError) as ei2:
        build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert ei2.value.code == "boundary_frame_stale"
    assert ei2.value.detail["reason"] == "artifact_basis_missing"
    assert fake.video_calls == [], "全策略下 provider 调用数 = 0"


def test_j4_last_frame_from_stale_first_frame_fail_closed(venv):
    """传递新鲜度：stale 首帧派生的尾帧继承 stale——尾帧生成 fail closed。"""
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=dict(_current_basis(svc), binding_revision="old0001"),
                      link_mode="keyframes")
    with pytest.raises(BoundGenerationError) as ei:
        _run_last_frame_handler(pdir)
    assert ei.value.code == "boundary_frame_stale"
    assert fake.image_calls == [], "provider 调用数 = 0"


def test_j6_relay_base_no_basis_diagnostic_not_block(venv):
    """inherit_previous 构图基础=来源镜头末帧（relay 物料）→ diagnostic 不阻断。"""
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, link_mode="keyframes")   # 无 basis 也不阻断 relay 场景
    project = _load_project(pdir)
    # 本镜开场来自 c02 的视频末帧
    def mut(s):
        s.opening_source = OpeningSource.INHERIT_PREVIOUS.value
        s.source_shot_id = "s01_c02"
    ProjectStore(pdir).update_shot("s01_c01", mut)
    c02 = project.get_shot("s01_c02")
    ProjectStore(pdir).update_shot("s01_c02", lambda s: setattr(
        s, "video_end_frame",
        Asset(local=None, url="https://mock.agnes/prev_end.png", status="done")))
    shot = _load_shot(pdir)
    req = build_bound_last_frame_input(pdir, _load_project(pdir), shot,
                                       first_frame_url="https://mock.agnes/prev_end.png")
    assert any(d["reason"] == "boundary_relay_no_binding_basis" for d in req.diagnostics)
    assert req.image_refs[0] == "https://mock.agnes/prev_end.png"


# ============ K. Provider capability（§36）============

def test_k1_no_invented_provider_fields(venv):
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    _run_video_handler(pdir)
    call = fake.video_calls[0]
    assert call["mode"] == "i2v" and call["image_urls"] is None, \
        "i2v 请求无伪造 last-frame/image_urls 字段"
    req = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    d = req.to_dict()
    assert "seed" not in d and "negative_prompt" not in d, \
        "Adapter contract 不发明 provider 参数（seed/negative 属 Shot 字段，handler 传递）"


# ============ M/N. Race（§38/12.md §18-20）============

def test_m1_video_race_related_mutation_blocks(venv, monkeypatch):
    import src.generation.video_adapter as vada
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    from src.resources.models import ReferenceAsset
    original = vada._style_instruction_from_pack

    def race(pack):
        # build 窗口内修改被绑 Look（依赖指纹变化 → validity NOT_READY）
        rd = ResourceStore(pdir, pdir.name).load()
        rd.reference_assets["ref_alice000009"] = ReferenceAsset(
            asset_id="ref_alice000009", url="http://example/alice9.png")
        rd.looks[LOOK].approved_reference_asset_ids.append("ref_alice000009")
        ResourceStore(pdir, pdir.name).save(rd)
        return original(pack)

    monkeypatch.setattr(vada, "_style_instruction_from_pack", race)
    with pytest.raises(BoundGenerationError) as ei:
        build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert ei.value.code == "dependency_changed_before_generation"
    assert fake.video_calls == []


def test_n1_video_race_unrelated_mutation_proceeds(venv, monkeypatch):
    import src.generation.video_adapter as vada
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    base = build_bound_video_input(pdir, _load_project(pdir),
                                   _load_shot(pdir)).to_dict()
    original = vada._style_instruction_from_pack

    def race(pack):
        rd = ResourceStore(pdir, pdir.name).load()
        rd.looks[LOOK_BOB].label = "Bob mutated"
        ResourceStore(pdir, pdir.name).save(rd)
        return original(pack)

    monkeypatch.setattr(vada, "_style_instruction_from_pack", race)
    req = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert req.to_dict() == base, "无关变更不误伤、request 不变（actual dependencies only）"


# ============ O/P. Determinism / No mutation（§39/§40）============

def test_o1_video_request_deterministic(venv):
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    r1 = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir)).to_dict()
    r2 = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir)).to_dict()
    assert r1 == r2


def test_p1_video_adapter_no_canonical_mutation(venv):
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    h0 = _hash_all(pdir)
    build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    build_bound_last_frame_input(pdir, _load_project(pdir), _load_shot(pdir),
                                 first_frame_url=FF_URL)
    assert _hash_all(pdir) == h0


def test_p2_video_handler_truth_files_byte_identical(venv):
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    h0 = _hash_all(pdir)
    _run_video_handler(pdir)
    h1 = _hash_all(pdir)
    for n in ("resources.json", "scene_system.json", "project_profile.json",
              "shot_production_bindings.json"):
        assert h0[n] == h1[n], f"{n} 必须字节不变"


# ============ Q. Legacy compatibility（§41）============

def test_q1_unbound_last_frame_legacy_identical(venv):
    pdir, ids, svc, fake = venv
    _seed_first_frame(pdir, link_mode="keyframes")
    _run_last_frame_handler(pdir)
    assert len(fake.image_calls) == 1
    project = _load_project(pdir)
    shot = project.get_shot("s01_c01")
    ff_url = _effective_first_frame_url(project, shot)
    # 2026-09-18 P1 seam：legacy 链路消费 ProjectVisualProfile（specified → 编译点风格后缀）。
    assert fake.image_calls[0]["prompt"] == build_last_frame_prompt(
        project, shot, style_suffix=_legacy_vp_style_suffix(pdir))
    assert fake.image_calls[0]["image_refs"] == [ff_url] + _shot_char_refs(project, shot)


def test_q2_unbound_video_keyframes_legacy_identical(venv):
    pdir, ids, svc, fake = venv
    _seed_first_frame(pdir)
    _seed_last_frame(pdir)
    _set_video_ready(pdir, link_mode="keyframes")
    _run_video_handler(pdir)
    assert len(fake.video_calls) == 1
    call = fake.video_calls[0]
    project = _load_project(pdir)
    shot = project.get_shot("s01_c01")
    assert call["mode"] == "keyframes"
    assert call["prompt"] == build_video_prompt(shot, "keyframes")
    assert call["image_urls"] == [_effective_first_frame_url(project, shot),
                                  shot.last_frame.selected.url]


def test_q3_not_ready_never_legacy_last_frame(venv):
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc), link_mode="keyframes")
    rd = ResourceStore(pdir, pdir.name).load()
    rd.looks[LOOK].approved_reference_asset_ids = []
    ResourceStore(pdir, pdir.name).save(rd)
    with pytest.raises(BoundGenerationError):
        _run_last_frame_handler(pdir)
    assert fake.image_calls == [], "BOUND NOT_READY 尾帧绝不 fallback legacy"


def test_q4_ready_only_pack_path_no_styleanchor(venv):
    pdir, ids, svc, fake = venv
    _bind_default(svc, ids)
    _seed_first_frame(pdir, basis=_current_basis(svc))
    _set_video_ready(pdir)
    req = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert "anime, cinematic composition" not in req.prompt, "READY 只走 Pack path"


# ============ §42 Cross-domain deterministic fixture（9 步）============

def test_cross_domain_pick_up_full_chain(venv):
    pdir, ids, svc, fake = venv
    _add_pistol_with_comp(pdir, PISTOL_COMP, "Alice", LOOK)

    # Step 1: validity READY
    _bind_pick_up(svc, ids)
    assert svc.read_validity("s01_c01")["readiness"] == READY

    # Step 2: compile pack
    compiled = svc.compile_reference_pack("s01_c01")
    assert compiled["status"] == "COMPILED"

    # Step 3: first boundary request → Alice NOT holding pistol
    project = _load_project(pdir)
    shot = _load_shot(pdir)
    first_req = build_bound_first_frame_input(pdir, project, shot)
    assert ("composition", "end") not in [(r["source_type"], r["temporal_scope"])
                                          for r in first_req.semantic_roles]
    assert "right hand empty" in first_req.prompt
    assert "right hand holding the pistol" not in first_req.prompt

    # Step 4: end boundary request → Alice holding pistol
    _run_first_frame_handler(pdir)            # 生成首帧（记录 basis）
    def mut_lm(s):
        s.link_mode = "keyframes"
    ProjectStore(pdir).update_shot("s01_c01", mut_lm)
    _run_last_frame_handler(pdir)             # 生成尾帧（构图基础首帧 fresh）
    shot = _load_shot(pdir)
    lreq_roles = [(r["source_type"], r["temporal_scope"])
                  for r in build_bound_last_frame_input(
                      pdir, _load_project(pdir), shot,
                      first_frame_url=_effective_first_frame_url(
                          _load_project(pdir), shot)).semantic_roles]
    assert ("composition", "end") in lreq_roles

    # Step 5: video request = creative motion + style + scene + look + prop + temporal action
    _set_video_ready(pdir, link_mode="keyframes")
    vreq = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert "Animate this image" in vreq.prompt or \
        vreq.prompt.startswith("Create a smooth cinematic transition"), \
        "creative motion intent 保留"
    assert "2D animated image" in vreq.prompt, "style（P1）"
    assert "Scene: lab" in vreq.prompt, "scene"
    assert "Alice" in vreq.prompt, "character/look"
    assert "Pistol" in vreq.prompt or "pistol" in vreq.prompt, "prop"
    assert "right hand empty" in vreq.prompt and \
        "right hand holding the pistol" in vreq.prompt, "correct temporal action"

    # Step 6: Fake provider called exactly once
    _run_video_handler(pdir)
    assert len(fake.video_calls) == 1

    # Step 7: 改 bound Alice Look → NOT_READY → video provider 不再调用
    rd = ResourceStore(pdir, pdir.name).load()
    from src.resources.models import ReferenceAsset
    rd.reference_assets["ref_alice000009"] = ReferenceAsset(
        asset_id="ref_alice000009", url="http://example/alice9.png")
    rd.looks[LOOK].approved_reference_asset_ids.append("ref_alice000009")
    ResourceStore(pdir, pdir.name).save(rd)
    assert svc.read_validity("s01_c01")["readiness"] == "NOT_READY"
    with pytest.raises(BoundGenerationError):
        _run_video_handler(pdir)
    assert len(fake.video_calls) == 1, "NOT_READY → provider 不调用"

    # Step 8: explicit refresh → READY；旧 boundary artifact 不得自动认定 fresh
    r = svc.refresh_binding("s01_c01", expected_binding_revision=
                            svc.read_binding("s01_c01")["binding_revision"])
    assert r["ok"], r
    assert svc.read_validity("s01_c01")["readiness"] == READY
    with pytest.raises(BoundGenerationError) as ei:
        build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert ei.value.code == "boundary_frame_stale", \
        "refresh 后旧 artifact 不自动 fresh（binding_revision 已翻动）"
    assert len(fake.video_calls) == 1

    # Step 9: 重新生成 boundary artifacts → READY → 新 video request 反映当前真相
    _run_first_frame_handler(pdir)
    _run_last_frame_handler(pdir)
    vreq2 = build_bound_video_input(pdir, _load_project(pdir), _load_shot(pdir))
    assert vreq2.basis["binding_revision"] == \
        svc.read_binding("s01_c01")["binding_revision"]
    assert vreq2.to_dict() != vreq.to_dict(), "新 video request 反映 refresh 后真相"
    _run_video_handler(pdir)
    assert len(fake.video_calls) == 2
    assert fake.video_calls[1]["image_urls"] == [
        _effective_first_frame_url(_load_project(pdir), _load_shot(pdir)),
        _load_shot(pdir).last_frame.selected.url]
