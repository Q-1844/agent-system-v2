# tests/test_sequence_audio_basis.py
# Sequence S2 — source 解析（§9/§10/§33）、basis v2（§26）、mismatch/freshness
# 扩展（§27/§28/§29）确定性测试。probe monkeypatch 注入（零 ffmpeg）。
import json

import pytest

from src.core.models import Project, Scene, Shot, ShotStatus, VideoVersion
from src.core.store import ProjectStore
from src.sequence.audio_basis import (
    ResolvedAudioSource,
    build_audio_sections,
    build_render_basis_v2,
    compute_current_render_basis,
    full_basis_mismatched_keys,
    resolve_audio_source,
)
from src.sequence.audio_models import (
    AUDIO_SOURCE_KIND_A1,
    AUDIO_SOURCE_KIND_IMPORTED,
    SequenceAudioLayer,
    SequenceAudioPlan,
    SequenceAudioSourceRef,
)
from src.sequence.basis import (
    sequence_render_freshness,
    source_video_fingerprint,
)
from src.sequence.models import (
    RSTATUS_DONE,
    SequenceBlock,
    SequenceClip,
    SequenceRenderVersion,
    render_profile_fingerprint,
)
from src.sequence.timeline import ResolvedClip

PROBE_OK = {"duration_ms": 4000, "width": None, "height": None, "fps": None,
            "vcodec": None, "pix_fmt": None, "has_video": True, "has_audio": True,
            "acodec": "pcm_s16le", "sample_rate": 48000, "channels": 1}
PROBE_NO_AUDIO = {**PROBE_OK, "has_audio": False}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 项目：A1 store（1 dialogue 1 version）+ audio_imports 文件 + 伪 probe。"""
    project_dir = tmp_path / "proj"
    wav = project_dir / "output" / "audio_imports" / "music.wav"
    wav.parent.mkdir(parents=True)
    wav.write_bytes(b"fake-audio-bytes")
    a1wav = tmp_path / "a1_prod.wav"
    a1wav.write_bytes(b"a1-production-wav")
    (project_dir / "output" / "dialogue_audio.json").write_text(json.dumps({
        "schema_version": 1,
        "dialogues": {"s01_c01": {
            "audio_versions": [{"audio_version_id": "s01_c01_av01",
                                "production_wav": {"path": str(a1wav)}}],
            "selected_audio_version_id": "s01_c01_av01", "approved": False}},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("src.sequence.media_ops.probe_video", lambda p: dict(PROBE_OK))
    yield project_dir, wav, a1wav


def _layer(gain=0.0, start=0, duration=4000, role="music"):
    return SequenceAudioLayer(layer_id="layer_a", role=role,
                              source=SequenceAudioSourceRef(
                                  kind=AUDIO_SOURCE_KIND_IMPORTED,
                                  file="audio_imports/music.wav"),
                              start_ms=start, duration_ms=duration, gain_db=gain,
                              fade_in_ms=500, fade_out_ms=500)


def _resolved(project_dir, layer):
    """走真实解析路径（Phase 1 同款）——保证与重算侧 sha256 一致。"""
    return resolve_audio_source(project_dir, layer.layer_id, layer.source)


# ============ resolve（§10 fail closed / §33 无音频流）============

def test_resolve_imported_happy(env):
    project_dir, wav, _ = env
    r = resolve_audio_source(project_dir, "layer_a",
                             SequenceAudioSourceRef(kind=AUDIO_SOURCE_KIND_IMPORTED,
                                                    file="audio_imports/music.wav"))
    assert r.duration_ms == 4000 and r.has_audio and r.sha256
    assert r.file.endswith("music.wav")


def test_resolve_a1_happy_and_missing(env):
    project_dir, _, a1wav = env
    r = resolve_audio_source(project_dir, "layer_n",
                             SequenceAudioSourceRef(kind=AUDIO_SOURCE_KIND_A1,
                                                    dialogue_id="s01_c01",
                                                    audio_version_id="s01_c01_av01"))
    assert r.sha256  # 绝对路径 A1 production_wav 可解析
    from src.sequence.models import SequenceError
    with pytest.raises(SequenceError) as e:
        resolve_audio_source(project_dir, "l", SequenceAudioSourceRef(
            kind=AUDIO_SOURCE_KIND_A1, dialogue_id="s01_c01", audio_version_id="nope"))
    assert e.value.code == "audio_source_not_found"
    (project_dir / "output" / "dialogue_audio.json").unlink()
    with pytest.raises(SequenceError) as e2:
        resolve_audio_source(project_dir, "l", SequenceAudioSourceRef(
            kind=AUDIO_SOURCE_KIND_A1, dialogue_id="s01_c01", audio_version_id="s01_c01_av01"))
    assert e2.value.code == "audio_source_not_found"


def test_resolve_missing_file_and_no_audio_stream(env, monkeypatch):
    project_dir, wav, _ = env
    from src.sequence.models import SequenceError
    with pytest.raises(SequenceError) as e:
        resolve_audio_source(project_dir, "l", SequenceAudioSourceRef(
            kind=AUDIO_SOURCE_KIND_IMPORTED, file="audio_imports/gone.wav"))
    assert e.value.code == "audio_source_missing"
    monkeypatch.setattr("src.sequence.media_ops.probe_video",
                        lambda p: dict(PROBE_NO_AUDIO))
    with pytest.raises(SequenceError) as e2:   # §33：无音频流 FAIL CLOSED
        resolve_audio_source(project_dir, "l", SequenceAudioSourceRef(
            kind=AUDIO_SOURCE_KIND_IMPORTED, file="audio_imports/music.wav"))
    assert e2.value.code == "audio_source_has_no_audio"


# ============ basis v2（§26：S1 键保留 + audio 段）============

def test_build_v2_keeps_v1_keys_and_adds_audio(env, tmp_path):
    project_dir, _, _ = env
    plan = SequenceAudioPlan(sequence_id="seq_x", layers=[_layer()])
    blk = SequenceBlock(sequence_id="seq_x", clips=[SequenceClip(shot_id="a")])
    vfile = tmp_path / "v.mp4"      # build_render_basis 会对源文件做真实 sha256
    vfile.write_bytes(b"video-v")
    rc = [ResolvedClip(index=0, shot_id="a", version_id="v", source_file=str(vfile),
                       trim_in_ms=0, trim_out_ms=0, source_duration_ms=6000)]
    v2 = build_render_basis_v2(project_dir, blk, rc, plan, [_resolved(project_dir, plan.layers[0])])
    assert v2["schema"] == "sequence_render_basis_v2"
    for k in ("sequence_revision", "clips", "render_profile_fingerprint",
              "renderer_schema", "resolution_error"):
        assert k in v2                                    # §26：S1 键继续保留
    assert v2["audio"]["audio_plan_revision"] == plan.revision
    assert v2["audio"]["layers"][0]["source_artifact_sha256"] ==         resolve_audio_source(project_dir, "layer_a", plan.layers[0].source).sha256
    # Phase 44C：mixer schema 升 v2（默认配置下编译图与 v1 逐字节等价，仅 schema 标识升级）
    assert v2["audio"]["mixer_schema"] == "sequence_audio_mixer_v2"


# ============ mismatch 矩阵（§25/§27/§28/§29）============

def _mk_project(video_file) -> Project:
    return Project(name="p", scenes=[Scene(scene_id="s01", shots=[
        Shot(shot_id="s01_c01", scene_id="s01", index=1,
             video_versions=[VideoVersion(version_id="vv_a", local=str(video_file),
                                          status=ShotStatus.DONE.value)],
             final_video="vv_a")])])


def test_full_basis_mismatch_matrix(env, tmp_path):
    project_dir = env[0]
    blk = SequenceBlock(sequence_id="seq_x", clips=[SequenceClip(shot_id="s01_c01")])
    plan = SequenceAudioPlan(sequence_id="seq_x", layers=[_layer()])
    vfile = tmp_path / "v1.mp4"     # 录制与重算必须同一真实文件
    vfile.write_bytes(b"video-v1")
    rc = [ResolvedClip(index=0, shot_id="s01_c01", version_id="vv_a",
                       source_file=str(vfile), trim_in_ms=0, trim_out_ms=0,
                       source_duration_ms=6000)]
    v2 = build_render_basis_v2(project_dir, blk, rc, plan, [_resolved(project_dir, plan.layers[0])])
    project = _mk_project(vfile)

    # plan 在 + v2 + 无变化 → 无 mismatch
    cur = compute_current_render_basis(project, project_dir, blk, plan)
    assert full_basis_mismatched_keys(v2, cur, plan_present=True) == []

    # §27：gain 改 → plan revision + 层 gain 双键 STALE
    plan2 = SequenceAudioPlan(sequence_id="seq_x", layers=[_layer(gain=-8.0)])
    cur2 = compute_current_render_basis(project, project_dir, blk, plan2)
    m = full_basis_mismatched_keys(v2, cur2, plan_present=True)
    assert "audio.audio_plan_revision" in m and "audio.layers[0].gain_db" in m

    # §29：plan 不变、clip 语义变 → v1 键 STALE
    blk2 = SequenceBlock(sequence_id="seq_x", clips=[SequenceClip(shot_id="s01_c01",
                                                                  trim_out_ms=100)])
    cur3 = compute_current_render_basis(project, project_dir, blk2, plan)
    m3 = full_basis_mismatched_keys(v2, cur3, plan_present=True)
    assert "sequence_revision" in m3

    # §25/plan 清空：stored v2 → audio_plan_removed
    cur4 = compute_current_render_basis(project, project_dir, blk, None)
    assert full_basis_mismatched_keys(v2, cur4, plan_present=False) == ["audio_plan_removed"]

    # §27：plan 存在 + stored v1 → audio_plan_untracked
    v1_stored = {k: v for k, v in v2.items() if k not in ("schema", "audio")}
    v1_stored["schema"] = "sequence_render_basis_v1"
    assert "audio_plan_untracked" in full_basis_mismatched_keys(
        v1_stored, cur, plan_present=True)

    # layer_id 换、语义不变 → 不误报（layer_id 是句柄不比对）
    v2b = json.loads(json.dumps(v2))
    v2b["audio"]["layers"][0]["layer_id"] = "layer_renamed"
    assert full_basis_mismatched_keys(v2b, cur, plan_present=True) == []

    # §28：源内容变（同 id 同路径）→ sha256 键 STALE
    env[1].write_bytes(b"CHANGED-AUDIO-CONTENT")
    cur5 = compute_current_render_basis(project, project_dir, blk, plan)
    m5 = full_basis_mismatched_keys(v2, cur5, plan_present=True)
    assert "audio.layers[0].source_artifact_sha256" in m5


# ============ freshness 端到端（经 sequence_render_freshness）============

def test_freshness_with_plan_end_to_end(env, tmp_path):
    project_dir = env[0]
    vf = project_dir / "output" / "videos" / "s01_c01.mp4"
    vf.parent.mkdir(exist_ok=True)
    vf.write_bytes(b"video-v1")
    ProjectStore(project_dir).save(Project(name="proj", scenes=[Scene(scene_id="s01", shots=[
        Shot(shot_id="s01_c01", scene_id="s01", index=1,
             video_versions=[VideoVersion(version_id="vv_a", local=str(vf),
                                          status=ShotStatus.DONE.value)],
             final_video="vv_a")])]))
    blk = SequenceBlock(sequence_id="seq_f", clips=[SequenceClip(shot_id="s01_c01")])
    plan = SequenceAudioPlan(sequence_id="seq_f", layers=[_layer()])
    project = _mk_project(vf)
    rc = [ResolvedClip(index=0, shot_id="s01_c01", version_id="vv_a",
                       source_file=str(vf), trim_in_ms=0, trim_out_ms=0,
                       source_duration_ms=6000)]
    v2 = build_render_basis_v2(project_dir, blk, rc, plan, [_resolved(project_dir, plan.layers[0])])
    rv = SequenceRenderVersion(version_id="srender_z", sequence_id="seq_f",
                               status=RSTATUS_DONE, sequence_revision=blk.revision,
                               generation_basis=v2)

    out = sequence_render_freshness(project, blk, rv, plan=plan, project_dir=project_dir)
    assert out["status"] == "current"

    # §27：只改 gain → STALE 且 mismatched_keys 指向 gain
    plan2 = SequenceAudioPlan(sequence_id="seq_f", layers=[_layer(gain=-8.0)])
    out2 = sequence_render_freshness(project, blk, rv, plan=plan2, project_dir=project_dir)
    assert out2["status"] == "stale"
    assert "audio.layers[0].gain_db" in out2["detail"]["mismatched_keys"]

    # plan 清空 → audio_plan_removed（§25：当前语义回到纯 S1）
    out3 = sequence_render_freshness(project, blk, rv, plan=None, project_dir=project_dir)
    assert out3["status"] == "stale"
    assert "audio_plan_removed" in out3["detail"]["mismatched_keys"]

    # 无 plan + v1 render：S1 行为不变（§25 锁）
    v1_rv = SequenceRenderVersion(version_id="srender_v1", sequence_id="seq_f",
                                  status=RSTATUS_DONE, sequence_revision=blk.revision,
                                  generation_basis={"schema": "sequence_render_basis_v1",
                                                    "sequence_id": "seq_f",
                                                    "sequence_revision": blk.revision,
                                                    "clips": v2["clips"],
                                                    "render_profile_fingerprint": render_profile_fingerprint(),
                                                    "renderer_schema": "sequence_renderer_v1",
                                                    "resolution_error": None})
    out4 = sequence_render_freshness(project, blk, v1_rv, plan=None, project_dir=project_dir)
    assert out4["status"] == "current"
