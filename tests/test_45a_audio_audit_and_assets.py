# tests/test_45a_audio_audit_and_assets.py
# Phase 45.1 — 音频落窗审计 / 资产库发现 / narration sequence 算窗 / manifest 声音投影。
# 全部确定性（零 ffmpeg、零 LLM）：审计解码器与 ffprobe 用 monkeypatch 注入。
import json
import math

import pytest

from src.agents.assembly import (
    _execute_audio_assets_read,
    _execute_manifest_publish,
    _execute_narration_read,
    _execute_render_status,
    _execute_sequence_create,
)
from src.agents.runtime import AgentRuntime
from src.sequence.audio_audit import (
    NATIVE_SILENT_DB,
    _pearson,
    audit_audio_render,
)
from src.sequence.assembly_manifest import (
    load_assembly_manifest,
    validate_assembly_manifest,
)
from src.sequence.service import SequenceService

from tests.test_44b_assembly_fixture import (
    build_media_project,
    build_snapshot_manifest,
    fake_probe,
    write_snapshot_file,
)


def _runtime(tmp_path, client=None, queue=None):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client,
                        job_queue_getter=(lambda: queue) if queue else None)


@pytest.fixture(autouse=True)
def _fake_ffmpeg_probe(monkeypatch):
    monkeypatch.setattr("src.sequence.media_ops.probe_video", fake_probe)


# ============ audio_audit 纯逻辑 ============

def test_pearson_basic():
    a = [math.sin(i * 0.01) for i in range(2000)]
    assert _pearson(a, list(a)) == pytest.approx(1.0, abs=1e-6)
    b = [math.sin(i * 0.01 + math.pi / 2) for i in range(2000)]
    assert abs(_pearson(a, b)) < 0.2
    assert _pearson(a, [0.0] * 2000) is None      # 常数序列 → None（不伪装通过）
    assert _pearson(a, a[:10]) is None            # 样本不足 → None


class _FakeDecoder:
    """按 (path, start_ms) 决定返回内容的伪解码器。
    windows: {(path, start_ms): "tone" | "zero"}；缺省 tone。"""

    def __init__(self, plan=None, default="tone"):
        self.plan = plan or {}
        self.default = default
        self.calls = []

    def __call__(self, path, start_ms, duration_ms):
        self.calls.append((path, start_ms, duration_ms))
        mode = self.plan.get((str(path), int(start_ms)), self.default)
        if mode == "zero":
            return [0.0] * 1600
        return [math.sin(i * 0.05) for i in range(1600)]


def _layers():
    return [
        {"layer_id": "lay_n1", "role": "narration",
         "source_file": "audio_imports/narration/nd01__a.wav",
         "start_ms": 1000, "duration_ms": 3000, "source_in_ms": 0,
         "gain_db": 2.0, "fade_in_ms": 0, "fade_out_ms": 0},
        {"layer_id": "lay_n2", "role": "narration",
         "source_file": "audio_imports/narration/nd02__b.wav",
         "start_ms": 10000, "duration_ms": 3000, "source_in_ms": 0,
         "gain_db": 2.0, "fade_in_ms": 0, "fade_out_ms": 0},
        {"layer_id": "lay_m1", "role": "music",
         "source_file": "audio_imports/music/bgm.wav",
         "start_ms": 0, "duration_ms": 16000, "source_in_ms": 0,
         "gain_db": -13.5, "fade_in_ms": 2000, "fade_out_ms": 2000},
    ]


def _sources(tmp_path):
    """审计 fail-closed 前置检查要求源文件真实存在（内容无关——解码被伪注入）。"""
    for rel in ("audio_imports/narration/nd01__a.wav",
                "audio_imports/narration/nd02__b.wav",
                "audio_imports/music/bgm.wav"):
        p = tmp_path / "output" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"DUMMY")
    return tmp_path / "output"


def _audit(tmp_path, monkeypatch, *, dec, base_gain_db=0.0,
           mix_duration_ms=16000, layers=None):
    monkeypatch.setattr("src.sequence.audio_audit._decode_segment", dec)
    monkeypatch.setattr("src.sequence.audio_audit.measure_loudness",
                        lambda p: {"integrated_lufs": -16.0,
                                   "true_peak_dbtp": -1.8, "lra_lu": 4.0})
    return audit_audio_render(mix_file="mix.mp4", layers=layers or _layers(),
                              project_output_dir=_sources(tmp_path),
                              base_gain_db=base_gain_db,
                              mix_duration_ms=mix_duration_ms,
                              render_version_id="srender_x")


def test_audit_all_present_passes(tmp_path, monkeypatch):
    dec = _FakeDecoder()
    out = _audit(tmp_path, monkeypatch, dec=dec, base_gain_db=-60.0)
    assert out["passed"] is True, out["failures"]
    assert len(out["narration_layers"]) == 2
    assert out["loudness"]["integrated_lufs"] == -16.0
    assert out["native_leakage"]["checked"] is True
    # 旁白窗与源对齐：层内偏移计入 source_in
    calls = {(p, s) for p, s, _ in dec.calls}
    assert (str(_sources(tmp_path) / "audio_imports/narration/nd01__a.wav"),
            0) in calls


def test_audit_missing_second_narration_fails(tmp_path, monkeypatch):
    # 44C §9.3 教训锁定：第二句缺失必须被抓到（"仅首句在场"=失败）
    dec = _FakeDecoder(plan={("mix.mp4", 10000): "zero"})
    out = _audit(tmp_path, monkeypatch, dec=dec)
    assert out["passed"] is False
    assert any("lay_n2" in f for f in out["failures"])
    assert out["native_leakage"]["checked"] is False   # base_gain 0 → 不查静默


def test_audit_music_probe_window_gap_fails(tmp_path, monkeypatch):
    # 无旁白 busy → 均匀探针窗（2000/7000/12000）；中间窗静音 → fail
    music_only = [l for l in _layers() if l["role"] == "music"]
    dec = _FakeDecoder(plan={("mix.mp4", 7000): "zero"})
    out = _audit(tmp_path, monkeypatch, dec=dec, layers=music_only)
    assert out["passed"] is False
    assert any("lay_m1" in f for f in out["failures"])
    assert out["music_layers"][0]["probe_basis"] == "uniform_windows"


def test_audit_music_probes_prefer_gap_windows(tmp_path, monkeypatch):
    # 有旁白 busy → 探针窗必须落在无旁白间隙（ducking 会失真重叠窗）
    narration_only = [l for l in _layers() if l["role"] == "narration"]
    music = [l for l in _layers() if l["role"] == "music"]
    music[0] = dict(music[0], start_ms=0, duration_ms=16000,
                    fade_in_ms=500, fade_out_ms=500)
    from src.sequence.audio_audit import _music_probe_windows
    busy = [[l["start_ms"], l["start_ms"] + l["duration_ms"]]
            for l in narration_only]
    wins = _music_probe_windows(music[0], 16000, busy_windows=busy)
    assert wins and len(wins) <= 3
    for w in wins:
        for bs, be in [[1000, 4000], [10000, 13000]]:
            assert w["end_ms"] <= bs or w["start_ms"] >= be, (w, bs, be)


def test_audit_native_leakage_detected(tmp_path, monkeypatch):
    # base 静默策略下，无层活动间隙窗电平高 → 原生音泄漏
    # （只放旁白层：n1 [1000,4000) / n2 [10000,13000)，其余为间隙）
    narration_only = [l for l in _layers() if l["role"] == "narration"]
    dec = _FakeDecoder(plan={("mix.mp4", 5100): "tone"})   # n1-n2 之间的间隙窗
    out = _audit(tmp_path, monkeypatch, dec=dec, base_gain_db=-60.0,
                 layers=narration_only)
    assert out["native_leakage"]["pass"] is False
    assert any("泄漏" in f for f in out["failures"])


def test_audit_native_quiet_gap_passes(tmp_path, monkeypatch):
    narration_only = [l for l in _layers() if l["role"] == "narration"]
    dec = _FakeDecoder(default="zero")   # 间隙窗=数字静音 → 不判泄漏
    out = _audit(tmp_path, monkeypatch, dec=dec, base_gain_db=-60.0,
                 layers=narration_only)
    assert out["native_leakage"]["pass"] is True


def test_audit_layer_out_of_bounds_fails(tmp_path, monkeypatch):
    dec = _FakeDecoder()
    layers = _layers()
    layers[0]["start_ms"] = 17000
    layers[0]["duration_ms"] = 2000            # 17000 > 片长 16000 → 窗越界
    monkeypatch.setattr("src.sequence.audio_audit._decode_segment", dec)
    monkeypatch.setattr("src.sequence.audio_audit.measure_loudness",
                        lambda p: {"integrated_lufs": None,
                                   "true_peak_dbtp": None, "lra_lu": None})
    out = audit_audio_render(mix_file="mix.mp4", layers=layers,
                             project_output_dir=_sources(tmp_path),
                             base_gain_db=0.0, mix_duration_ms=16000)
    assert out["passed"] is False
    assert any("越界" in f for f in out["failures"])


def test_audit_missing_source_file_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr("src.sequence.audio_audit.measure_loudness",
                        lambda p: {"integrated_lufs": None,
                                   "true_peak_dbtp": None, "lra_lu": None})
    layers = [{"layer_id": "lay_n1", "role": "narration",
               "source_file": "audio_imports/narration/ghost.wav",
               "start_ms": 0, "duration_ms": 2000, "source_in_ms": 0,
               "gain_db": 0, "fade_in_ms": 0, "fade_out_ms": 0}]
    from src.sequence.models import SequenceError
    with pytest.raises(SequenceError):
        audit_audio_render(mix_file="mix.mp4", layers=layers,
                           project_output_dir=tmp_path, base_gain_db=0.0,
                           mix_duration_ms=8000)


# ============ 资产库发现 ============

def _write_assets_lib(tmp_path, with_meta=True):
    lib = tmp_path / "output" / "audio_imports" / "music"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "bgm_a.wav").write_bytes(b"AAAA")
    (lib / "bgm_b.wav").write_bytes(b"BBBB")
    (tmp_path / "output" / "audio_imports" / "narration").mkdir(exist_ok=True)
    (tmp_path / "output" / "audio_imports" / "narration" / "nd01.wav") \
        .write_bytes(b"NNNN")
    if with_meta:
        meta = {"schema": "audio_assets_meta_v1", "assets": {
            "audio_imports/music/bgm_a.wav": {
                "origin": "locally_composed_synthesized_test_asset",
                "license": "self_composed_no_third_party_rights"}}}
        (tmp_path / "output" / "audio_imports" / "assets_meta.json") \
            .write_text(json.dumps(meta), encoding="utf-8")


def test_audio_assets_read_lists_and_identifies(tmp_path):
    _write_assets_lib(tmp_path)
    rt = _runtime(tmp_path)
    out = _execute_audio_assets_read(rt, "p45", "{}", "t", "m1")
    assert out["ok"] and out["asset_count"] == 3
    by_file = {a["file"]: a for a in out["assets"]}
    assert by_file["audio_imports/music/bgm_a.wav"]["identity"]["origin"] == \
        "locally_composed_synthesized_test_asset"
    assert by_file["audio_imports/music/bgm_b.wav"]["identity"]["registered"] is False
    assert by_file["audio_imports/music/bgm_a.wav"]["duration_ms"] == 8000  # fake_probe


def test_audio_assets_read_survives_corrupt_meta(tmp_path):
    _write_assets_lib(tmp_path, with_meta=False)
    (tmp_path / "output" / "audio_imports" / "assets_meta.json") \
        .write_text("{broken", encoding="utf-8")
    rt = _runtime(tmp_path)
    out = _execute_audio_assets_read(rt, "p45", "{}", "t", "m1")
    assert out["ok"] and out["asset_count"] == 3
    assert all(a["identity"].get("registered") is False for a in out["assets"])


# ============ narration_read 按 sequence 算窗 ============

def _setup(tmp_path):
    files = build_media_project(tmp_path)
    snap = build_snapshot_manifest(files)
    write_snapshot_file(tmp_path, snap)
    return files


def test_narration_read_sequence_windows_from_canonical(tmp_path):
    _setup(tmp_path)
    rt = _runtime(tmp_path)
    svc = SequenceService(tmp_path, "p45")
    block = svc.create_sequence(clips=[{"shot_id": "s01_c01"}, {"shot_id": "s02_c01"}])
    out = _execute_narration_read(
        rt, "p45", json.dumps({"sequence_id": block.sequence_id}), "t", "m1")
    assert out["ok"] is True
    assert out["window_source"] == f"sequence_canonical:{block.sequence_id}"
    wins = out["timeline_windows"]
    assert [(w["start_ms"], w["end_ms"]) for w in wins] == \
        [(0, 8000), (8000, 16000)]
    assert wins[1]["scene_boundary"] is True
    assert wins[1]["scene_id"] == "s02"


def test_narration_read_bad_sequence_fails(tmp_path):
    _setup(tmp_path)
    rt = _runtime(tmp_path)
    out = _execute_narration_read(rt, "p45",
                                  json.dumps({"sequence_id": "seq_ghost"}),
                                  "t", "m1")
    assert out["ok"] is False


# ============ manifest 声音投影 ============

def _make_done_render(tmp_path, sid):
    svc = SequenceService(tmp_path, "p45")
    rv = svc.create_render(sid)
    out_dir = tmp_path / "output" / "sequences" / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{rv.version_id}.mp4").write_bytes(b"FAKEMP4")
    # 盖当前 basis 章（真实渲染由 prepare/commit 写入；伪造行走同一条计算路径）
    from src.sequence.audio_basis import compute_current_render_basis
    from src.core.store import ProjectStore
    plan_now = svc.get_audio_plan(sid)
    basis = compute_current_render_basis(ProjectStore(tmp_path).load(), tmp_path,
                                         svc.get_entry(sid).block, plan_now)
    entries = svc.store.load()
    row = entries[sid].get_render(rv.version_id)
    row.generation_basis = basis
    row.status = "done"
    row.output_file = str(out_dir / f"{rv.version_id}.mp4")
    entries[sid].render_versions = [
        row if r.version_id == rv.version_id else r
        for r in entries[sid].render_versions]
    svc.store.save(entries)
    return rv.version_id


def _put_plan(tmp_path, sid):
    # plan 引用的源文件必须真实存在（basis 的 audio 段会解析源并指纹）
    for rel in ("audio_imports/narration/nd01__nver_x.wav",
                "audio_imports/music/bgm_a.wav"):
        f = tmp_path / "output" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"PCM" + rel.encode())
    svc = SequenceService(tmp_path, "p45")
    svc.put_audio_plan(sid, layers_payload=[
        {"layer_id": "lay_n1", "role": "narration",
         "source": {"kind": "imported_audio",
                    "file": "audio_imports/narration/nd01__nver_x.wav"},
         "start_ms": 500, "duration_ms": 3000, "gain_db": 2.0},
        {"layer_id": "lay_m1", "role": "music",
         "source": {"kind": "imported_audio",
                    "file": "audio_imports/music/bgm_a.wav"},
         "start_ms": 0, "duration_ms": 16000, "gain_db": -13.5,
         "fade_in_ms": 2000, "fade_out_ms": 2000},
    ], plan_config={"base_gain_db": -60.0})
    return svc.get_audio_plan(sid)


def _put_narration_store(tmp_path):
    out = tmp_path / "output"
    out.mkdir(exist_ok=True)
    (out / "narration_audio.json").write_text(json.dumps({
        "schema": "narration_audio_v1",
        "cues": {"nd01": {"versions": [{
            "version_id": "nver_x", "text": "雨后的河面。",
            "audio_import_path": "audio_imports/narration/nd01__nver_x.wav",
            "audio_sha256": "a" * 64}]}}}, ensure_ascii=False), encoding="utf-8")


def test_manifest_publish_projects_music_and_narration(tmp_path):
    _setup(tmp_path)
    rt = _runtime(tmp_path)
    svc = SequenceService(tmp_path, "p45")
    block = svc.create_sequence(
        clips=[{"shot_id": "s01_c01"}, {"shot_id": "s01_c02"}, {"shot_id": "s02_c01"}])
    sid = block.sequence_id
    _put_plan(tmp_path, sid)          # plan 先落 → 渲染 basis 含当前 plan
    _make_done_render(tmp_path, sid)
    _put_narration_store(tmp_path)
    r = _execute_manifest_publish(rt, "p45",
                                  json.dumps({"sequence_id": sid}), "t", "m1")
    assert r["ok"] is True, r
    assert r["narration_cue_source"] == "audio_plan_projection"
    assert r["narration_cue_count"] == 1 and r["music_layer_count"] == 1
    assert r["audio_plan_revision"]
    doc = load_assembly_manifest(tmp_path)
    validate_assembly_manifest(doc)
    assert doc["music_bed"]["audio_plan_revision"] == r["audio_plan_revision"]
    assert doc["music_bed"]["base_gain_db"] == -60.0
    cue = doc["narration_cues"][0]
    assert cue["text"] == "雨后的河面。" and cue["narration_version_id"] == "nver_x"
    assert len(doc["music_bed"]["layers"]) == 1


def test_manifest_publish_agent_cue_override(tmp_path):
    _setup(tmp_path)
    rt = _runtime(tmp_path)
    svc = SequenceService(tmp_path, "p45")
    block = svc.create_sequence(clips=[{"shot_id": "s01_c01"}])
    sid = block.sequence_id
    _put_plan(tmp_path, sid)
    _make_done_render(tmp_path, sid)
    _put_narration_store(tmp_path)
    r = _execute_manifest_publish(
        rt, "p45",
        json.dumps({"sequence_id": sid,
                    "narration_cues": [{"cue_id": "nd01", "text": "自定义",
                                        "start_ms": 500, "end_ms": 3000}]}),
        "t", "m1")
    assert r["ok"] is True
    assert r["narration_cue_source"] == "agent"
    doc = load_assembly_manifest(tmp_path)
    assert doc["narration_cues"][0]["text"] == "自定义"
    assert doc["music_bed"]["audio_plan_revision"] == r["audio_plan_revision"]


def test_manifest_publish_no_plan_keeps_v1_semantics(tmp_path):
    _setup(tmp_path)
    rt = _runtime(tmp_path)
    svc = SequenceService(tmp_path, "p45")
    block = svc.create_sequence(clips=[{"shot_id": "s01_c01"}])
    sid = block.sequence_id
    _make_done_render(tmp_path, sid)
    r = _execute_manifest_publish(rt, "p45",
                                  json.dumps({"sequence_id": sid}), "t", "m1")
    assert r["ok"] is True
    assert r["narration_cue_source"] == "none"
    doc = load_assembly_manifest(tmp_path)
    assert doc["music_bed"] is None and doc["narration_cues"] == []


# ============ render_status deep_audit audio 接线 ============

def test_render_status_deep_audit_includes_audio(tmp_path, monkeypatch):
    monkeypatch.setattr("src.sequence.media_ops.audit_output_seams",
                        lambda path, seam_times_ms, fps=24.0: {"frames": {}})
    _setup(tmp_path)
    rt = _runtime(tmp_path)
    svc = SequenceService(tmp_path, "p45")
    block = svc.create_sequence(clips=[{"shot_id": "s01_c01"}])
    sid = block.sequence_id
    vid = _make_done_render(tmp_path, sid)
    _put_plan(tmp_path, sid)
    captured = {}

    def _fake_audit(**kw):
        captured.update(kw)
        return {"schema": "audio_audit_v1", "passed": True, "failures": []}

    monkeypatch.setattr("src.sequence.audio_audit.audit_audio_render", _fake_audit)
    out = _execute_render_status(rt, "p45",
                                 json.dumps({"sequence_id": sid, "deep_audit": True}),
                                 "t", "m1")
    assert out["deep_audit"]["audio"]["passed"] is True
    assert captured["render_version_id"] == vid
    assert captured["base_gain_db"] == -60.0
    assert {l["layer_id"] for l in captured["layers"]} == {"lay_n1", "lay_m1"}
    assert captured["layers"][0]["source_file"].startswith("audio_imports/")


def test_render_status_no_plan_skips_audio(tmp_path, monkeypatch):
    monkeypatch.setattr("src.sequence.media_ops.audit_output_seams",
                        lambda path, seam_times_ms, fps=24.0: {"frames": {}})
    _setup(tmp_path)
    rt = _runtime(tmp_path)
    svc = SequenceService(tmp_path, "p45")
    block = svc.create_sequence(clips=[{"shot_id": "s01_c01"}])
    _make_done_render(tmp_path, block.sequence_id)
    out = _execute_render_status(rt, "p45",
                                 json.dumps({"sequence_id": block.sequence_id,
                                             "deep_audit": True}), "t", "m1")
    assert "audio" not in out["deep_audit"]


def test_audit_failure_surfaces_not_swallows(tmp_path, monkeypatch):
    monkeypatch.setattr("src.sequence.media_ops.audit_output_seams",
                        lambda path, seam_times_ms, fps=24.0: {"frames": {}})
    _setup(tmp_path)
    rt = _runtime(tmp_path)
    svc = SequenceService(tmp_path, "p45")
    block = svc.create_sequence(clips=[{"shot_id": "s01_c01"}])
    _make_done_render(tmp_path, block.sequence_id)
    _put_plan(tmp_path, block.sequence_id)

    def _boom(**kw):
        raise RuntimeError("decoder exploded")

    monkeypatch.setattr("src.sequence.audio_audit.audit_audio_render", _boom)
    out = _execute_render_status(rt, "p45",
                                 json.dumps({"sequence_id": block.sequence_id,
                                             "deep_audit": True}), "t", "m1")
    assert out["deep_audit"]["audio"]["passed"] is False
    assert "RuntimeError" in out["deep_audit"]["audio"]["error"]


def test_native_silent_threshold_constant():
    assert NATIVE_SILENT_DB == -50.0


# ============ 45.5 加固：越窗 advisory / manifest 谱系守卫 ============

def test_audit_flags_narration_crossing_shot_cut(tmp_path, monkeypatch):
    # 旁白窗 [500, 8511) 跨 8000ms 切点 → advisory（不判 fail，证据可看）
    dec = _FakeDecoder()
    monkeypatch.setattr("src.sequence.audio_audit._decode_segment", dec)
    monkeypatch.setattr("src.sequence.audio_audit.measure_loudness",
                        lambda p: {"integrated_lufs": None,
                                   "true_peak": None,
                                   "true_peak_unit": None, "lra_lu": None})
    layers = [{"layer_id": "lay_n1", "role": "narration",
               "source_file": "audio_imports/narration/nd01__a.wav",
               "start_ms": 500, "duration_ms": 8011, "source_in_ms": 0,
               "gain_db": 0, "fade_in_ms": 0, "fade_out_ms": 0}]
    out = audit_audio_render(mix_file="mix.mp4", layers=layers,
                             project_output_dir=_sources(tmp_path),
                             base_gain_db=0.0, mix_duration_ms=16000,
                             shot_boundaries_ms=[8000])
    row = out["narration_layers"][0]
    assert row["crosses_shot_cut_ms"] == [8000]
    assert "advisory" in row
    # 不进 failures（创作口径，不判 fail）
    assert out["passed"] is True


def test_audit_no_advisory_when_within_shot(tmp_path, monkeypatch):
    dec = _FakeDecoder()
    monkeypatch.setattr("src.sequence.audio_audit._decode_segment", dec)
    monkeypatch.setattr("src.sequence.audio_audit.measure_loudness",
                        lambda p: {"integrated_lufs": None,
                                   "true_peak": None,
                                   "true_peak_unit": None, "lra_lu": None})
    layers = [{"layer_id": "lay_n1", "role": "narration",
               "source_file": "audio_imports/narration/nd01__a.wav",
               "start_ms": 500, "duration_ms": 3000, "source_in_ms": 0,
               "gain_db": 0, "fade_in_ms": 0, "fade_out_ms": 0}]
    out = audit_audio_render(mix_file="mix.mp4", layers=layers,
                             project_output_dir=_sources(tmp_path),
                             base_gain_db=0.0, mix_duration_ms=16000,
                             shot_boundaries_ms=[8000])
    assert "crosses_shot_cut_ms" not in out["narration_layers"][0]


def test_manifest_publish_refuses_when_render_stale_vs_plan(tmp_path, monkeypatch):
    from src.agents.assembly import _execute_manifest_publish
    from src.agents.runtime import AgentRuntime as _RT
    _setup(tmp_path)
    rt = _RT(project_dir_resolver=lambda name: tmp_path,
             settings_path=tmp_path / "agents_data" / "settings.json",
             client=None, job_queue_getter=None)
    svc = SequenceService(tmp_path, "p45")
    block = svc.create_sequence(clips=[{"shot_id": "s01_c01"}])
    sid = block.sequence_id
    _make_done_render(tmp_path, sid)
    _put_plan(tmp_path, sid)          # 渲染后才写 plan → done 渲染对 plan 已 stale
    _put_narration_store(tmp_path)
    r = _execute_manifest_publish(rt, "p45",
                                  json.dumps({"sequence_id": sid}), "t", "m1")
    assert r["ok"] is False and r["code"] == "render_stale_vs_plan"
    assert "render_submit" in r["error"]


def test_audio_plan_put_warns_on_truncated_narration(tmp_path, monkeypatch):
    # 45.5 教训：narration 层 duration 短于源文件 / 带淡出 → 回执显式告警
    from src.agents.assembly import _execute_audio_plan_put
    _setup(tmp_path)
    rt = _runtime(tmp_path)
    svc = SequenceService(tmp_path, "p45")
    block = svc.create_sequence(clips=[{"shot_id": "s01_c01"}])
    _put_plan(tmp_path, block.sequence_id)   # 现有 plan：narr01 层 dur=3000（源 8000）
    r = _execute_audio_plan_put(
        rt, "p45",
        json.dumps({"sequence_id": block.sequence_id,
                    "layers": [
                        {"layer_id": "lay_n1", "role": "narration",
                         "source": {"kind": "imported_audio",
                                    "file": "audio_imports/narration/nd01__nver_x.wav"},
                         "start_ms": 500, "duration_ms": 3000,
                         "gain_db": 0.0, "fade_in_ms": 0, "fade_out_ms": 0}],
                    "plan_config": {"base_gain_db": -60.0},
                    "expected_revision": _put_plan(tmp_path, block.sequence_id).revision}),
        "t", "m1")
    assert r["ok"] is True, r
    warns = " ".join(r["narration_slice_warnings"])
    assert "会被截断" in warns and "5000ms" in warns   # 8000-3000
