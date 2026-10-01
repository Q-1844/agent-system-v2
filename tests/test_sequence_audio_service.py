# tests/test_sequence_audio_service.py
# Sequence S2 — service 层集成测试（25.md §12/§14/§22/§25/§27/§30/§33/§38/§21）。
# 媒体操作 monkeypatch（零 ffmpeg；真实 ffmpeg 在真机 E2E）。
import pytest

from src.core.models import Project, Scene, Shot, ShotStatus, VideoVersion
from src.core.store import ProjectStore
from src.sequence.audio_models import AUDIO_SOURCE_KIND_IMPORTED, SequenceAudioPlan
from src.sequence.models import (
    RSTATUS_DONE,
    RSTATUS_FAILED,
    RSTATUS_RACED,
    SequenceError,
)
from src.sequence.service import SequenceService


@pytest.fixture()
def asvc(tmp_path, monkeypatch):
    """S2 fixture：S1 素材 + audio_imports/music.wav（伪 probe 30s）+ 伪混音。"""
    project_dir = tmp_path / "proj"
    vids = project_dir / "output" / "videos"
    vids.mkdir(parents=True)
    f1 = vids / "s01_c01.mp4"; f1.write_bytes(b"v1")
    f2 = vids / "s01_c02.mp4"; f2.write_bytes(b"v2")
    imports = project_dir / "output" / "audio_imports"
    imports.mkdir(parents=True)
    wav = imports / "music.wav"; wav.write_bytes(b"music-bytes")
    ProjectStore(project_dir).save(Project(name="proj", scenes=[Scene(scene_id="s01", shots=[
        Shot(shot_id="s01_c01", scene_id="s01", index=1,
             video_versions=[VideoVersion(version_id="vv_a1", local=str(f1),
                                          status=ShotStatus.DONE.value)],
             final_video="vv_a1"),
        Shot(shot_id="s01_c02", scene_id="s01", index=2,
             video_versions=[VideoVersion(version_id="vv_b1", local=str(f2),
                                          status=ShotStatus.DONE.value)],
             final_video="vv_b1")])]))

    def fake_probe(path):
        name = str(path)
        dur = 30000 if name.endswith(".wav") else (6000 if "s01_c01" in name else 8000)
        return {"duration_ms": dur, "width": 1280, "height": 704, "fps": 24.0,
                "vcodec": "h264", "pix_fmt": "yuv420p", "has_video": True, "has_audio": True,
                "acodec": "aac", "sample_rate": 48000, "channels": 2}

    def fake_render_clip(src, dst, *, trim_in_ms, trim_out_ms, source_duration_ms,
                         has_audio, profile=None):
        from pathlib import Path as _P
        d = _P(dst); d.parent.mkdir(parents=True, exist_ok=True); d.write_bytes(b"seg")

    calls = {"concat": [], "mix_cmds": [], "peaks": []}

    def fake_concat(paths, dst):
        from pathlib import Path as _P
        d = _P(dst); d.parent.mkdir(parents=True, exist_ok=True)
        d.write_bytes(b"concat:" + str(len(paths)).encode())
        calls["concat"].append(str(dst))

    def fake_run(cmd, timeout):
        """伪 ffmpeg：混音命令（cmd[-1]=dst）写输出文件；记录命令供断言。"""
        from pathlib import Path as _P
        d = _P(cmd[-1]); d.parent.mkdir(parents=True, exist_ok=True)
        d.write_bytes(b"mixed")
        calls["mix_cmds"].append([str(c) for c in cmd])
        return ""

    def fake_validate(path, planned_total_ms, n_clips):
        return {"probe": {"duration_ms": planned_total_ms, "has_audio": True},
                "duration_ms": planned_total_ms,
                "planned_duration_ms": planned_total_ms, "duration_delta_ms": 0}

    def fake_peak(path):
        return calls["peaks"].pop() if calls["peaks"] else -12.0

    monkeypatch.setattr("src.sequence.media_ops.probe_video", fake_probe)
    monkeypatch.setattr("src.sequence.media_ops.render_clip", fake_render_clip)
    monkeypatch.setattr("src.sequence.media_ops.concat_clips", fake_concat)
    monkeypatch.setattr("src.sequence.media_ops._run", fake_run)
    monkeypatch.setattr("src.sequence.media_ops.validate_output", fake_validate)
    monkeypatch.setattr("src.sequence.audio_mix.measure_peak_db", fake_peak)
    svc = SequenceService(project_dir, project_name="proj")
    svc.calls = calls
    yield svc


def _create(svc):
    return svc.create_sequence(title="S2", clips=[{"shot_id": "s01_c01"},
                                                  {"shot_id": "s01_c02"}])


def _music_layer(gain=-18.0, start=0, duration=14000, fade_in=2000, fade_out=3000,
                 source_in=0):
    return {"role": "music",
            "source": {"kind": AUDIO_SOURCE_KIND_IMPORTED, "file": "audio_imports/music.wav"},
            "start_ms": start, "duration_ms": duration, "gain_db": gain,
            "fade_in_ms": fade_in, "fade_out_ms": fade_out, "source_in_ms": source_in}


def _full_render(svc, sid):
    rv = svc.create_render(sid)
    prep = svc.prepare_render(sid, rv.version_id)
    svc.execute_render(sid, prep.render_version.version_id, prep)
    return prep, svc.commit_render(sid, prep.render_version.version_id)


# ============ §25：无 plan = S1 语义锁死 ============

def test_no_plan_is_s1_semantics(asvc):
    blk = _create(asvc)
    rv = asvc.create_render(blk.sequence_id)
    prep = asvc.prepare_render(blk.sequence_id, rv.version_id)
    assert not prep.has_layers
    assert prep.render_version.generation_basis["schema"] == "sequence_render_basis_v1"
    assert "audio" not in prep.render_version.generation_basis
    output = asvc.execute_render(blk.sequence_id, prep.render_version.version_id, prep)
    assert asvc.calls["mix_cmds"] == []                       # 混音路径未触发
    assert len(asvc.calls["concat"]) == 1                     # S1 concat 原样
    res = asvc.commit_render(blk.sequence_id, prep.render_version.version_id)
    assert res["status"] == RSTATUS_DONE and res["audio_peak_warning"] is None
    row = asvc.get_entry(blk.sequence_id).get_render(prep.render_version.version_id)
    assert row.audio_peak_warning is None and row.base_render_version_id is None


# ============ §23 方案 A：无 base 重建 → 有 base video copy ============

def test_first_render_rebuilds_then_mix(asvc):
    blk = _create(asvc)
    asvc.put_audio_plan(blk.sequence_id, [_music_layer()])
    prep, res = _full_render(asvc, blk.sequence_id)
    assert prep.render_version.generation_basis["schema"] == "sequence_render_basis_v2"
    assert prep.render_version.generation_basis["audio"]["layers"][0]["gain_db"] == -18.0
    assert res["status"] == RSTATUS_DONE
    row = asvc.get_entry(blk.sequence_id).get_render(prep.render_version.version_id)
    assert row.base_render_version_id is None                 # 首渲染=重建路径
    assert len(asvc.calls["concat"]) == 1 and len(asvc.calls["mix_cmds"]) == 2
    # 🔴 44C+ 接管回归锁：混音必须两遍——第一遍 图→中间 PCM（filter_complex 在、
    # 无视频 map），第二遍 视频 copy + WAV→AAC（无 filter_complex）。单遍「图→AAC」
    # 实测丢延迟层（amix+adelay+定长帧编码器缺陷：多句旁白仅第一句幸存）。
    cmd_pcm, cmd_aac = asvc.calls["mix_cmds"]
    assert "-filter_complex" in cmd_pcm and "amix=inputs=2:duration=first:normalize=0" \
        in " ".join(cmd_pcm)
    assert cmd_pcm[cmd_pcm.index("-c:a") + 1] == "pcm_s16le"  # 中间产物为 PCM
    assert "0:v" not in cmd_pcm[cmd_pcm.index("-map"):]       # 不碰视频流
    assert "-filter_complex" not in cmd_aac                   # 第二遍无图
    assert cmd_aac[cmd_aac.index("-c:v") + 1] == "copy"       # 方案 A：视频流 copy
    assert cmd_aac[cmd_aac.index("-c:a") + 1] == "aac"        # 音频重编码
    assert cmd_aac[cmd_aac.index("-map") + 1] == "0:v" and \
        cmd_aac[cmd_aac.index("-map", cmd_aac.index("-map") + 1) + 1] == "1:a"
    # §10：渲染完不自动 select
    assert asvc.get_entry(blk.sequence_id).final_render is None


def test_second_render_rebuilds_when_only_mixed_base_exists(asvc):
    """Phase 44C 修复语义：混音渲染（v2 basis）不得作方案 A 的 base——[0:a] 语义 =
    native 音轨，用已混音渲染做 base 会把上一版 layers 叠加进新渲染。
    仅有混音 base 时走降级路径：重建基线（concat 再跑一次）+ 正确混音。"""
    blk = _create(asvc)
    asvc.put_audio_plan(blk.sequence_id, [_music_layer()])
    prep1, _ = _full_render(asvc, blk.sequence_id)
    vid1 = prep1.render_version.version_id
    n_mix_1, n_concat_1 = len(asvc.calls["mix_cmds"]), len(asvc.calls["concat"])
    asvc.put_audio_plan(blk.sequence_id, [_music_layer(gain=-26.0)])
    prep2, res2 = _full_render(asvc, blk.sequence_id)
    assert res2["status"] == RSTATUS_DONE
    row2 = asvc.get_entry(blk.sequence_id).get_render(prep2.render_version.version_id)
    assert row2.base_render_version_id is None              # 无合格 v1 base → 降级
    assert len(asvc.calls["concat"]) == n_concat_1 + 1       # 重建基线：新分段
    cmd = asvc.calls["mix_cmds"][n_mix_1]
    assert cmd[cmd.index("-i") + 1].endswith("base.mp4")     # 混音基线=临时重建文件


def test_third_render_uses_pure_s1_base_after_mix(asvc):
    """存在纯 S1 渲染时，后续混音一律以其为 base（视频零重编码且不叠加）。"""
    blk = _create(asvc)
    # 先渲染一次纯 S1（无 plan）→ v1 basis
    rv0 = asvc.create_render(blk.sequence_id)
    prep0 = asvc.prepare_render(blk.sequence_id, rv0.version_id)
    asvc.execute_render(blk.sequence_id, prep0.render_version.version_id, prep0)
    asvc.commit_render(blk.sequence_id, prep0.render_version.version_id)
    vid0 = prep0.render_version.version_id
    n_mix_0, n_concat_0 = len(asvc.calls["mix_cmds"]), len(asvc.calls["concat"])
    # 混音渲染 #1（base = vid0）
    asvc.put_audio_plan(blk.sequence_id, [_music_layer()])
    prep1, _ = _full_render(asvc, blk.sequence_id)
    assert asvc.get_entry(blk.sequence_id).get_render(
        prep1.render_version.version_id).base_render_version_id == vid0
    # 混音渲染 #2（gain 调整）→ base 仍 = vid0（纯 S1），绝不使用混音渲染 #1
    asvc.put_audio_plan(blk.sequence_id, [_music_layer(gain=-26.0)])
    prep2, res2 = _full_render(asvc, blk.sequence_id)
    assert res2["status"] == RSTATUS_DONE
    row2 = asvc.get_entry(blk.sequence_id).get_render(prep2.render_version.version_id)
    assert row2.base_render_version_id == vid0
    assert len(asvc.calls["concat"]) == n_concat_0           # 零新分段（base 复用 vid0）
    cmd = asvc.calls["mix_cmds"][-1]
    assert cmd[cmd.index("-i") + 1].endswith(f"{vid0}.mp4")


# ============ §12/§14/§33 越界 fail closed ============

def test_layer_start_plus_duration_beyond_sequence_fails(asvc):
    blk = _create(asvc)
    asvc.put_audio_plan(blk.sequence_id, [_music_layer(start=13000, duration=2000, fade_in=0, fade_out=0)])
    rv = asvc.create_render(blk.sequence_id)
    with pytest.raises(SequenceError) as e:
        asvc.prepare_render(blk.sequence_id, rv.version_id)
    assert e.value.code == "audio_layer_invalid" and "§12" in e.value.message
    row = asvc.get_entry(blk.sequence_id).get_render(rv.version_id)
    assert row.status == RSTATUS_FAILED


def test_layer_source_trim_beyond_source_fails(asvc):
    blk = _create(asvc)
    asvc.put_audio_plan(blk.sequence_id, [_music_layer(source_in=29000, duration=2000, fade_in=0, fade_out=0)])
    rv = asvc.create_render(blk.sequence_id)
    with pytest.raises(SequenceError) as e:
        asvc.prepare_render(blk.sequence_id, rv.version_id)
    assert e.value.code == "audio_layer_invalid" and "§14" in e.value.message


def test_source_without_audio_stream_fails_closed(asvc, monkeypatch):
    blk = _create(asvc)
    asvc.put_audio_plan(blk.sequence_id, [_music_layer()])
    orig = asvc and None
    def silent_probe(path):
        r = dict((monkeypatch and {})) if False else None
        if str(path).endswith(".wav"):
            return {"duration_ms": 30000, "width": None, "height": None, "fps": None,
                    "vcodec": None, "pix_fmt": None, "has_audio": False,
                    "acodec": None, "sample_rate": None, "channels": None}
        return {"duration_ms": 6000 if "s01_c01" in str(path) else 8000,
                "width": 1280, "height": 704, "fps": 24.0, "vcodec": "h264",
                "pix_fmt": "yuv420p", "has_video": True, "has_audio": True, "acodec": "aac",
                "sample_rate": 48000, "channels": 2}
    monkeypatch.setattr("src.sequence.media_ops.probe_video", silent_probe)
    rv = asvc.create_render(blk.sequence_id)
    with pytest.raises(SequenceError) as e:
        asvc.prepare_render(blk.sequence_id, rv.version_id)
    assert e.value.code == "audio_source_has_no_audio"        # §33 FAIL CLOSED


# ============ §30 race guard（plan 窗口内变更）============

def test_race_guard_audio_plan_changed_in_window(asvc):
    blk = _create(asvc)
    asvc.put_audio_plan(blk.sequence_id, [_music_layer()])
    rv = asvc.create_render(blk.sequence_id)
    prep = asvc.prepare_render(blk.sequence_id, rv.version_id)
    asvc.execute_render(blk.sequence_id, prep.render_version.version_id, prep)
    # 窗口内：只改 music gain（§44 同型操作）
    old = asvc.get_audio_plan(blk.sequence_id)
    asvc.put_audio_plan(blk.sequence_id, [_music_layer(gain=-30.0)])
    with pytest.raises(SequenceError) as e:
        asvc.commit_render(blk.sequence_id, prep.render_version.version_id)
    assert e.value.code == "sequence_dependency_changed_before_commit"
    row = asvc.get_entry(blk.sequence_id).get_render(prep.render_version.version_id)
    assert row.status == RSTATUS_RACED


# ============ §27 freshness + §38 OCC + §21 advisory ============

def test_freshness_and_occ_and_peak(asvc):
    blk = _create(asvc)
    asvc.put_audio_plan(blk.sequence_id, [_music_layer()])
    prep, res = _full_render(asvc, blk.sequence_id)
    vid = prep.render_version.version_id
    asvc.select_render(blk.sequence_id, vid)
    assert asvc.freshness(blk.sequence_id)["status"] == "current"
    # §27：只改 gain → STALE（canonical 变、无渲染）
    old_rev = asvc.get_audio_plan(blk.sequence_id).revision
    asvc.put_audio_plan(blk.sequence_id, [_music_layer(gain=-8.0)],
                        expected_revision=old_rev)
    assert asvc.freshness(blk.sequence_id)["status"] == "stale"
    assert any("gain_db" in k for k in
               asvc.freshness(blk.sequence_id)["detail"]["mismatched_keys"])
    # 恢复 → CURRENT
    asvc.put_audio_plan(blk.sequence_id, [_music_layer()],
                        expected_revision=asvc.get_audio_plan(blk.sequence_id).revision)
    assert asvc.freshness(blk.sequence_id)["status"] == "current"
    # §38 OCC：错 revision → conflict
    with pytest.raises(SequenceError) as e:
        asvc.put_audio_plan(blk.sequence_id, [_music_layer()], expected_revision="badbad00")
    assert e.value.code == "revision_conflict"
    # §21：峰值超阈值 → advisory 记录在 render 行
    asvc.calls["peaks"].append(-0.1)
    prep3, res3 = _full_render(asvc, blk.sequence_id)
    assert res3["audio_peak_warning"]["code"] == "audio_peak_warning"
    row3 = asvc.get_entry(blk.sequence_id).get_render(prep3.render_version.version_id)
    assert row3.audio_peak_warning["max_volume_db"] == -0.1
    assert res3["status"] == RSTATUS_DONE                     # advisory 不阻断


def test_plan_canonical_normalization(asvc):
    """空 layers = 无 plan（S1 语义恢复；§25）。"""
    blk = _create(asvc)
    asvc.put_audio_plan(blk.sequence_id, [_music_layer()])
    assert asvc.get_audio_plan(blk.sequence_id) is not None
    asvc.put_audio_plan(blk.sequence_id, [])
    assert asvc.get_audio_plan(blk.sequence_id) is None
    # plan 为 Plan 对象形状（store 独立文件）
    plan = asvc.put_audio_plan(blk.sequence_id, [_music_layer()])
    assert isinstance(plan, SequenceAudioPlan) and plan.revision
