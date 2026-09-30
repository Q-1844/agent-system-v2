# tests/test_44b_render_profile.py
# Phase 44B — per-sequence render profile（加法式扩展）确定性测试（零 ffmpeg）。
# 覆盖：validate_render_profile 全分支 / SequenceBlock 往返+旧行兼容 / effective profile
# 与 basis 指纹（覆盖=新指纹；None=与旧全局指纹逐字节一致）/ fill 滤镜链 / service
# create+update（含清除覆盖）+ AudioPlan 混音规格守卫。
import pytest

from src.core.models import Project
from src.core.store import ProjectStore
from src.sequence.basis import build_render_basis, compute_current_basis
from src.sequence.media_ops import _scale_pad_fps
from src.sequence.models import (
    SEQUENCE_RENDER_PROFILE,
    SequenceBlock,
    SequenceError,
    render_profile_fingerprint,
    render_profile_fingerprint_for,
    validate_render_profile,
)
from src.sequence.service import SequenceService

from tests.test_44b_assembly_fixture import build_media_project


def _valid_profile(**over):
    base = {"width": 1280, "height": 720, "fps": 24, "vcodec": "libx264",
            "pix_fmt": "yuv420p", "acodec": "aac", "sample_rate": 48000,
            "channels": 2, "container": "mp4", "fill": "crop", "audio": "native"}
    base.update(over)
    return base


# ============ validate_render_profile ============

def test_profile_valid_roundtrip():
    p = validate_render_profile(_valid_profile())
    assert p["width"] == 1280 and p["fill"] == "crop" and p["audio"] == "native"


def test_profile_optional_keys_defaultable():
    p = validate_render_profile({k: v for k, v in _valid_profile().items()
                                 if k not in ("fill", "audio")})
    assert "fill" not in p and "audio" not in p


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(width=1281),                       # 奇数宽
    lambda p: p.update(height=721),                       # 奇数高
    lambda p: p.update(vcodec="hevc"),                    # 白名单外
    lambda p: p.update(fill="blur"),                      # fill 非法
    lambda p: p.update(audio="dolby"),                    # audio 非法
    lambda p: p.update(channels=6),
    lambda p: p.update(fps=0),
    lambda p: p.update(width=-1280),
    lambda p: p.update(width=True),                       # bool 是 int 子类，显式排除
    lambda p: p.update(extra_key=1),                      # 未知键
])
def test_profile_invalid_fail_closed(mutate):
    p = _valid_profile()
    mutate(p)
    with pytest.raises(SequenceError) as ei:
        validate_render_profile(p)
    assert ei.value.code == "profile_invalid"


def test_profile_missing_base_key_fail_closed():
    p = _valid_profile()
    del p["sample_rate"]
    with pytest.raises(SequenceError) as ei:
        validate_render_profile(p)
    assert "sample_rate" in ei.value.message


def test_profile_not_dict_fail_closed():
    with pytest.raises(SequenceError):
        validate_render_profile("1280x720")


# ============ SequenceBlock 集成 ============

def test_block_profile_none_effective_is_global_identity():
    """None → 全局 profile 本体（basis 指纹与旧实现逐字节一致，S1 语义不变）。"""
    b = SequenceBlock(sequence_id="seq_x", clips=[{"shot_id": "s01_c01"}])
    assert b.render_profile is None
    assert b.effective_render_profile() is SEQUENCE_RENDER_PROFILE
    assert render_profile_fingerprint_for(
        b.effective_render_profile()) == render_profile_fingerprint()


def test_block_profile_validated_on_init_and_roundtrip():
    b = SequenceBlock(sequence_id="seq_x", clips=[{"shot_id": "s01_c01"}],
                      render_profile=_valid_profile())
    d = b.to_dict()
    assert d["render_profile"]["fill"] == "crop"
    b2 = SequenceBlock.from_dict(d)
    assert b2.render_profile == b.render_profile
    # 坏 profile 在 __post_init__ 即拒
    bad = _valid_profile()
    bad["vcodec"] = "mpeg4"
    with pytest.raises(SequenceError):
        SequenceBlock(sequence_id="seq_y", clips=[{"shot_id": "s01_c01"}],
                      render_profile=bad)


def test_block_old_row_without_field_still_loads():
    """旧 sequences.json 行（无 render_profile 键）→ None（known-field 兼容）。"""
    old_row = {"sequence_id": "seq_old", "clips": [{"shot_id": "s01_c01"}],
               "title": "", "scene_id": None,
               "created_at": 1.0, "updated_at": 1.0}
    b = SequenceBlock.from_dict(old_row)
    assert b.render_profile is None


# ============ basis 指纹 ============

def test_basis_fingerprint_tracks_override(tmp_path):
    files = build_media_project(tmp_path)
    proj = ProjectStore(tmp_path).load()
    b_plain = SequenceBlock(sequence_id="seq_a", clips=[{"shot_id": "s01_c01"}])
    b_over = SequenceBlock(sequence_id="seq_a", clips=[{"shot_id": "s01_c01"}],
                           render_profile=_valid_profile())
    from src.sequence.timeline import ResolvedClip
    resolved = [ResolvedClip(index=0, shot_id="s01_c01", version_id="vv_s01c01",
                             source_file=files["s01_c01"]["path"],
                             trim_in_ms=0, trim_out_ms=0,
                             source_duration_ms=8000, has_audio=True)]
    fp_plain = build_render_basis(b_plain, resolved)["render_profile_fingerprint"]
    fp_over = build_render_basis(b_over, resolved)["render_profile_fingerprint"]
    assert fp_plain == render_profile_fingerprint()
    assert fp_over != fp_plain
    # compute_current 与 build 同构（race guard 前提）
    assert compute_current_basis(proj, b_over)["render_profile_fingerprint"] == fp_over


# ============ fill 滤镜链 ============

def test_vf_pad_default_and_crop():
    prof = {"width": 1280, "height": 720, "fps": 24}
    vf = _scale_pad_fps(prof)
    assert "force_original_aspect_ratio=decrease" in vf and "pad=" in vf
    vf_c = _scale_pad_fps({**prof, "fill": "crop"})
    assert "force_original_aspect_ratio=increase" in vf_c
    assert "crop=1280:720" in vf_c and "pad=" not in vf_c


# ============ service 集成 ============

def test_service_create_update_clear_profile(tmp_path):
    build_media_project(tmp_path)
    svc = SequenceService(tmp_path, "p44b")
    b = svc.create_sequence(clips=[{"shot_id": "s01_c01"}],
                            render_profile=_valid_profile())
    assert b.render_profile["fill"] == "crop"
    # 更新：改 profile
    b2 = svc.update_sequence(b.sequence_id,
                             render_profile=_valid_profile(fill="pad"))
    assert b2.render_profile["fill"] == "pad"
    # 更新：空 dict = 清除覆盖回全局
    b3 = svc.update_sequence(b.sequence_id, render_profile={},
                             expected_revision=b2.revision)
    assert b3.render_profile is None
    assert b3.effective_render_profile() is SEQUENCE_RENDER_PROFILE
    # 更新：坏 profile fail closed（canonical 不变）
    bad = _valid_profile()
    bad["height"] = 721
    with pytest.raises(SequenceError):
        svc.update_sequence(b.sequence_id, render_profile=bad)
    assert svc.get_entry(b.sequence_id).block.render_profile is None


def test_service_rejects_audio_mix_with_mismatched_profile(tmp_path, monkeypatch):
    """AudioPlan 混音要求 48k/2 —— profile 覆盖背离 → prepare fail closed。"""
    build_media_project(tmp_path)
    # prepare 会先 probe 源（dummy 字节非真视频）——注入伪探测以隔离本用例关注点
    monkeypatch.setattr("src.sequence.media_ops.probe_video",
                        lambda path: {"duration_ms": 8000, "has_video": True,
                                      "width": 1280, "height": 720, "fps": 24.0,
                                      "vcodec": "h264", "pix_fmt": "yuv420p",
                                      "has_audio": True, "acodec": "aac"})
    svc = SequenceService(tmp_path, "p44b")
    b = svc.create_sequence(clips=[{"shot_id": "s01_c01"}],
                            render_profile=_valid_profile(sample_rate=44100))
    from src.sequence.audio_models import (SequenceAudioLayer, SequenceAudioPlan,
                                           SequenceAudioSourceRef)
    from src.sequence.audio_store import SequenceAudioPlanStore
    plan = SequenceAudioPlan(sequence_id=b.sequence_id)
    plan.layers = [SequenceAudioLayer(
        layer_id="lay_1", role="music",
        source=SequenceAudioSourceRef.from_dict(
            {"kind": "imported_audio", "file": "audio_imports/x.wav"}),
        start_ms=0, duration_ms=1000)]
    st = SequenceAudioPlanStore(tmp_path, "p44b")
    plans = st.load()
    plans[b.sequence_id] = plan
    st.save(plans)
    rv = svc.create_render(b.sequence_id)
    with pytest.raises(SequenceError) as ei:
        svc.prepare_render(b.sequence_id, rv.version_id)
    assert ei.value.code == "profile_invalid"
    entry = svc.get_entry(b.sequence_id)
    assert entry.get_render(rv.version_id).status == "failed"
