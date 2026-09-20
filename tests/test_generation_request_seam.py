# tests/test_generation_request_seam.py
# 31.md Phase C — provider-neutral GenerationRequest seam 验收。
#
# 覆盖：
#   Unit：to_generation_request 投影（pack_only / continuity_tail / first_frame_head /
#         video 槽位）+ compile_agnes_image_call（确定性 / 能力诚实：negative 丢弃有记录、
#         seed 结构化拒绝）+ compile_agnes_video_call（i2v/keyframes 槽位映射、data URI
#         拒绝、keyframes 缺 last 拒绝不降级）。
#   Integration：BOUND 首帧 handler 经 seam 后 FakeImageClient 收到的调用 ==
#         build_bound_first_frame_input + compile 的预期（行为等价）；continuity 布局投影。
# 全程零外部调用（Fake provider / 纯函数）。
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
    compile_agnes_image_call,
    compile_agnes_video_call,
)
from src.generation.image_adapter import BoundImageRequest, build_bound_first_frame_input
from src.generation.video_adapter import BoundVideoRequest
from src.shot_binding import ShotBindingService

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))
from test_shot_binding_s3b1 import make_pdir, run  # noqa: E402
from test_shot_binding_s3b2 import _build_scene  # noqa: E402
from test_shot_binding_s3c1 import (  # noqa: E402
    FakeImageClient,
    _bind_default,
    _load_shot,
    _run_handler,
    _StubPM,
)

URL = "https://example.com/a.png"
URL2 = "https://example.com/b.png"


# ============ Unit：image 投影 ============

def _img_req(**kw):
    base = dict(
        shot_id="s01_c01", prompt="p", image_refs=[URL, URL2],
        used_reference_ids=["asset_a", "asset_b"],
        semantic_roles=[
            {"reference_asset_id": "asset_a", "source_type": "scene_instance_view",
             "purpose": "scene_context", "priority": "primary",
             "temporal_scope": "throughout", "subject_character": None,
             "prop_id": None, "checksum": "cafe"},
            {"reference_asset_id": "asset_b", "source_type": "character_look",
             "purpose": "character_appearance", "priority": "primary",
             "temporal_scope": "static", "subject_character": "Alice",
             "prop_id": None, "checksum": None},
        ],
        basis={"binding_revision": "r1"},
    )
    base.update(kw)
    return BoundImageRequest(**base)


def test_image_projection_pack_only_parallel_zip():
    req = _img_req().to_generation_request(generation_type="first_frame",
                                           size_tier="2K", ratio="16:9")
    assert req.generation_type == "first_frame"
    assert req.prompt == "p"
    assert req.size_tier == "2K" and req.ratio == "16:9"
    assert [r.media for r in req.reference_inputs] == [URL, URL2]
    assert [r.asset_id for r in req.reference_inputs] == ["asset_a", "asset_b"]
    assert [r.semantic_role for r in req.reference_inputs] == \
        ["scene_context", "character_appearance"]
    assert req.reference_inputs[0].checksum == "cafe"
    assert req.reference_inputs[1].subject_character == "Alice"
    assert req.provenance["basis"] == {"binding_revision": "r1"}
    assert req.provenance["used_reference_ids"] == ["asset_a", "asset_b"]
    assert req.provenance["ref_layout"] == "pack_only"


def test_image_projection_continuity_tail():
    req = _img_req(image_refs=[URL, URL2, "https://example.com/prev_end.png"],
                   ref_layout="continuity_tail") \
        .to_generation_request(generation_type="first_frame")
    medias = [r.media for r in req.reference_inputs]
    assert medias == [URL, URL2, "https://example.com/prev_end.png"]
    assert req.reference_inputs[-1].source_type == "continuity_previous_end"
    assert req.reference_inputs[-1].semantic_role == "continuity"
    assert req.reference_inputs[0].asset_id == "asset_a"
    assert req.provenance["ref_layout"] == "continuity_tail"


def test_image_projection_first_frame_head():
    req = _img_req(image_refs=["https://example.com/base.png", URL],
                   ref_layout="first_frame_head") \
        .to_generation_request(generation_type="last_frame")
    head = req.reference_inputs[0]
    assert head.source_type == "first_frame_base"
    assert head.semantic_role == "composition_base"
    assert head.temporal_scope == "start"
    assert [r.media for r in req.reference_inputs] == \
        ["https://example.com/base.png", URL]
    assert req.reference_inputs[1].asset_id == "asset_a"
    assert req.provenance["ref_layout"] == "first_frame_head"


# ============ Unit：Agnes 图片编译 ============

def test_image_compile_deterministic_and_honest():
    req = _img_req().to_generation_request(size_tier="2K", ratio="16:9")
    c1 = compile_agnes_image_call(req)
    c2 = compile_agnes_image_call(req)
    assert c1 == c2, "同 req 同输出（确定性）"
    kw = c1.kwargs()
    assert kw == {"prompt": "p", "image_refs": [URL, URL2],
                  "size_tier": "2K", "ratio": "16:9"}
    assert c1.dropped == []


def test_image_compile_drops_negative_prompt_with_record():
    req = _img_req().to_generation_request()
    req.negative_prompt = "no text"
    call = compile_agnes_image_call(req)
    assert call.kwargs()["prompt"] == "p"          # 未伪造参数槽
    assert not any("negative" in k for k in call.kwargs())
    assert call.dropped and call.dropped[0]["reason"] == \
        "negative_prompt_not_supported_image_provider"


def test_image_compile_rejects_seed_structured():
    req = _img_req().to_generation_request()
    req.seed = 42
    with pytest.raises(AgnesRequestCompileError) as ei:
        compile_agnes_image_call(req)
    assert ei.value.code == "seed_not_supported_image_provider"


def test_image_compile_t2i_when_no_media():
    req = _img_req(image_refs=[], semantic_roles=[]).to_generation_request()
    call = compile_agnes_image_call(req)
    assert call.image_refs is None                  # 与 use_i2i=bool(refs) 语义一致


def test_image_compile_rejects_video_type():
    req = _img_req().to_generation_request()
    req.generation_type = "video"
    with pytest.raises(AgnesRequestCompileError):
        compile_agnes_image_call(req)


# ============ Unit：video 投影 + Agnes 视频编译 ============

def _vid_req(**kw):
    base = dict(
        shot_id="s01_c01", prompt="v", provider_mode="i2v",
        first_frame=URL, last_frame=None,
        used_reference_ids=["cand_1"],
        basis={"binding_revision": "r1"},
        generation_basis={"schema": "native_av_generation_basis_v1"},
    )
    base.update(kw)
    return BoundVideoRequest(**base)


def test_video_projection_and_i2v_slots():
    req = _vid_req().to_generation_request(model="agnes-video-2.5-flash")
    assert req.generation_type == "video"
    assert req.first_frame == URL and req.last_frame is None
    assert req.model == "agnes-video-2.5-flash"
    assert [r.source_type for r in req.reference_inputs] == ["boundary_frame"]
    assert req.reference_inputs[0].semantic_role == "first_slot"
    assert req.reference_inputs[0].media == URL
    call = compile_agnes_video_call(req)
    assert call.slots() == {"image_url": URL}
    assert call.prompt == "v"


def test_video_keyframes_slots():
    req = _vid_req(provider_mode="keyframes", first_frame=URL,
                   last_frame=URL2).to_generation_request()
    call = compile_agnes_video_call(req)
    assert call.slots() == {"image_urls": [URL, URL2]}
    assert [r.semantic_role for r in req.reference_inputs] == ["first_slot", "last_slot"]


def test_video_keyframes_missing_last_rejected_not_downgraded():
    req = _vid_req(provider_mode="keyframes", first_frame=URL,
                   last_frame=None).to_generation_request()
    with pytest.raises(AgnesRequestCompileError) as ei:
        compile_agnes_video_call(req)
    assert ei.value.code == "keyframes_slots_incomplete"


def test_video_data_uri_rejected():
    req = _vid_req(first_frame="data:image/png;base64,AAAA").to_generation_request()
    with pytest.raises(AgnesRequestCompileError) as ei:
        compile_agnes_video_call(req)
    assert ei.value.code == "data_uri_not_supported_video_provider"


def test_video_compile_rejects_image_type():
    with pytest.raises(AgnesRequestCompileError):
        compile_agnes_video_call(_img_req().to_generation_request())


# ============ Integration：handler 经 seam 行为等价 ============

@pytest.fixture()
def cenv(tmp_path, monkeypatch):
    """与 s3c1 同构夹具（跨域真实文件 + deps stub + Fake provider）。"""
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


def test_handler_first_frame_client_receives_compiled_call(cenv):
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    bound = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                          _load_shot(pdir))
    compiled = compile_agnes_image_call(bound.to_generation_request(
        generation_type="first_frame",
        size_tier=ProjectStore(pdir).load().style.size_tier or "1K",
        ratio=ProjectStore(pdir).load().style.aspect_ratio or "16:9"))
    _run_handler(pdir)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["prompt"] == compiled.prompt == bound.prompt
    assert call["image_refs"] == (compiled.image_refs or []) == bound.image_refs
    assert call["size_tier"] == compiled.size_tier
    assert call["ratio"] == compiled.ratio


def test_handler_first_frame_layout_flag_records_continuity(cenv):
    """reference_previous：ref_layout=continuity_tail；投影尾部=来源镜头末帧 media，
    provider 收到的 refs 顺序与投影完全一致。"""
    pdir, ids, svc, fake = cenv
    _bind_default(svc, ids)
    from src.core.models import Asset, OpeningSource, Shot
    store = ProjectStore(pdir)
    prev_id = "s00_c99"
    project = store.load()
    if project.get_shot(prev_id) is None:
        project.scenes[0].shots.append(
            Shot(shot_id=prev_id, scene_id=project.scenes[0].scene_id,
                 index=len(project.scenes[0].shots)))
        store.save(project)

    def _set_end(s):
        s.video_end_frame = Asset(url="https://example.com/prev_end.png",
                                  status="done")

    def _relay(cur):
        cur.opening_source = OpeningSource.REFERENCE_PREVIOUS.value
        cur.source_shot_id = prev_id

    store.update_shot(prev_id, _set_end)
    store.update_shot("s01_c01", _relay)

    bound = build_bound_first_frame_input(pdir, ProjectStore(pdir).load(),
                                          _load_shot(pdir))
    assert bound.ref_layout == "continuity_tail"
    req = bound.to_generation_request(generation_type="first_frame")
    assert req.reference_inputs[-1].source_type == "continuity_previous_end"
    assert req.reference_inputs[-1].media == "https://example.com/prev_end.png"
    assert [r.media for r in req.reference_inputs] == bound.image_refs
    _run_handler(pdir)
    assert fake.calls[0]["image_refs"] == bound.image_refs
