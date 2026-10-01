# tests/test_44c_audio_narration.py
# Phase 44C — mixer v2 扩展（ducking/loudness/base_gain/stem）+ 旁白产物仓确定性测试。
# 零 ffmpeg（图形编译为纯字符串；narration 用 Fake adapter + monkeypatch 后处理）。
import asyncio
import json

import pytest

from src.sequence.audio_mix import (
    MixLayer,
    build_audio_mix_plan,
    compile_audio_filter_graph,
    compile_stem_filter_graph,
)
from src.sequence.audio_models import (
    AUDIO_SOURCE_KIND_IMPORTED,
    SequenceAudioLayer,
    SequenceAudioPlan,
    SequenceDuckingConfig,
    SequenceLoudnessTarget,
    SequenceError,
)
from src.sequence.service import SequenceService


def _layer(role, start_ms, dur_ms=3000, gain_db=0.0, fade_in=0, fade_out=0):
    return SequenceAudioLayer(
        layer_id=f"lay_{role}_{start_ms}", role=role,
        source={"kind": AUDIO_SOURCE_KIND_IMPORTED, "file": f"audio_imports/{role}_{start_ms}.wav"},
        start_ms=start_ms, duration_ms=dur_ms, gain_db=gain_db,
        fade_in_ms=fade_in, fade_out_ms=fade_out)


def _mix_plan(plan: SequenceAudioPlan):
    layers = [MixLayer(layer_id=l.layer_id, role=l.role,
                       source_file=f"{l.layer_id}.wav",
                       source_in_ms=l.source_in_ms, duration_ms=l.duration_ms,
                       start_ms=l.start_ms, gain_db=l.gain_db,
                       fade_in_ms=l.fade_in_ms, fade_out_ms=l.fade_out_ms)
              for l in plan.layers]
    return build_audio_mix_plan(
        "base.mp4", "basev1", layers,
        base_gain_db=plan.base_gain_db,
        ducking=(plan.ducking.semantic_snapshot() if plan.ducking else None),
        loudness=(plan.loudness.semantic_snapshot() if plan.loudness else None))


# ============ v1 等价性（默认配置）============

def test_default_config_graph_is_v1_identical():
    plan = SequenceAudioPlan(sequence_id="s", layers=[_layer("music", 0)])
    g = compile_audio_filter_graph(_mix_plan(plan))
    assert g["filter_complex"] == (
        "[1:a]atrim=start=0.000000:end=3.000000,asetpts=PTS-STARTPTS,"
        "volume=0.000000dB,afade=t=in:st=0:d=0.000000,"
        "afade=t=out:st=3.000000:d=0.000000,adelay=delays=0:all=1[l1];"
        "[0:a][l1]amix=inputs=2:duration=first:normalize=0[mixed]")
    assert g["final_label"] == "[mixed]"     # 44C+：图只产中间 PCM，无 AAC out_args


# ============ ducking ============

def test_ducking_single_trigger_graph_shape():
    plan = SequenceAudioPlan(
        sequence_id="s", layers=[_layer("music", 0), _layer("narration", 4000)],
        ducking=SequenceDuckingConfig(threshold_db=-30.0, ratio=4.0,
                                      attack_ms=50, release_ms=800))
    g = compile_audio_filter_graph(_mix_plan(plan))
    # threshold dB → 线性振幅
    assert "sidechaincompress=threshold=0.031622777:ratio=4.000000:attack=50:release=800" \
        in g["filter_complex"]
    # 单 trigger：无 narrkey 合并段；键信号 = 旁白链 asplit 的 k 路
    # 🔴 44C+ 回归：旁白层必须 asplit（标签复用会让 ffmpeg 图连接错乱——
    # 实测 base 全电平泄漏、旁白丢失）
    assert "[l2]asplit=2[l2k][l2f]" in g["filter_complex"]
    assert "[l1][l2k]sidechaincompress" in g["filter_complex"]
    assert "narrkey" not in g["filter_complex"]
    # 最终 amix：base + 压低后音乐 + 旁白本体
    assert "[0:a][l1d][l2f]amix=inputs=3:duration=first:normalize=0[mixed]"         == g["filter_complex"].split(";")[-1]
    # 确定性
    g2 = compile_audio_filter_graph(_mix_plan(plan))
    assert g["filter_complex"] == g2["filter_complex"]


def test_ducking_multi_trigger_merges_key():
    plan = SequenceAudioPlan(
        sequence_id="s",
        layers=[_layer("music", 0), _layer("narration", 4000),
                _layer("narration", 20000)],
        ducking=SequenceDuckingConfig())
    g = compile_audio_filter_graph(_mix_plan(plan))
    assert "amix=inputs=2:duration=longest:normalize=0[narrkey]" in g["filter_complex"]
    assert "[l1][narrkey]sidechaincompress" in g["filter_complex"]
    # 多 trigger：每个旁白层 asplit，键走 k 路、最终混音走 f 路
    assert "[l2]asplit=2[l2k][l2f]" in g["filter_complex"]
    assert "[l3]asplit=2[l3k][l3f]" in g["filter_complex"]
    assert "[l2k][l3k]amix" in g["filter_complex"]
    assert "[l3f]amix" in g["filter_complex"].split(";")[-1]


def test_ducking_inert_without_trigger_layers():
    plan = SequenceAudioPlan(sequence_id="s", layers=[_layer("music", 0)],
                             ducking=SequenceDuckingConfig())
    g = compile_audio_filter_graph(_mix_plan(plan))
    assert "sidechaincompress" not in g["filter_complex"]
    assert "[0:a][l1]amix" in g["filter_complex"]   # 全层直通


def test_ducking_inert_without_music_layers():
    plan = SequenceAudioPlan(sequence_id="s", layers=[_layer("narration", 0)],
                             ducking=SequenceDuckingConfig())
    g = compile_audio_filter_graph(_mix_plan(plan))
    assert "sidechaincompress" not in g["filter_complex"]


def test_ducking_validation_fail_closed():
    with pytest.raises(SequenceError) as ei:
        SequenceDuckingConfig(threshold_db=-5.0)     # 超出 [-80,-6]
    assert ei.value.code == "ducking_invalid"
    with pytest.raises(SequenceError):
        SequenceDuckingConfig(ratio=25.0)
    with pytest.raises(SequenceError):
        SequenceDuckingConfig(trigger_roles=("dialogue",))   # role 白名单外


# ============ loudness / base_gain ============

def test_loudness_and_base_gain_graph():
    plan = SequenceAudioPlan(sequence_id="s", layers=[_layer("music", 0)],
                             base_gain_db=-3.5,
                             loudness=SequenceLoudnessTarget(i=-16.0, tp=-1.5, lra=11.0))
    g = compile_audio_filter_graph(_mix_plan(plan))
    assert "[0:a]volume=-3.500000dB[b0]" in g["filter_complex"]
    assert "[mixed]loudnorm=I=-16.0:TP=-1.5:LRA=11.0,aresample=48000[final]" \
        in g["filter_complex"]
    assert g["final_label"] == "[final]"     # 44C+：loudnorm 后最终标签（供 -map）


def test_loudness_validation_fail_closed():
    with pytest.raises(SequenceError) as ei:
        SequenceLoudnessTarget(i=-3.0)      # 超出 [-40,-5]
    assert ei.value.code == "loudness_invalid"


# ============ plan revision / basis 语义 ============

def test_plan_revision_tracks_mixer_config():
    base = SequenceAudioPlan(sequence_id="s", layers=[_layer("music", 0)])
    r_layers_only = base.revision
    # 旧版落盘 dict（无新字段）→ 默认值 → 同 revision（前向兼容）
    old_row = {"sequence_id": "s", "layers": [l.to_dict() for l in base.layers]}
    assert SequenceAudioPlan.from_dict(old_row).revision == r_layers_only
    # 配置变化 → revision 变化
    r_gain = SequenceAudioPlan(sequence_id="s", layers=[_layer("music", 0)],
                               base_gain_db=-3.0).revision
    r_duck = SequenceAudioPlan(sequence_id="s", layers=[_layer("music", 0)],
                               ducking=SequenceDuckingConfig()).revision
    r_loud = SequenceAudioPlan(sequence_id="s", layers=[_layer("music", 0)],
                               loudness=SequenceLoudnessTarget()).revision
    assert len({r_layers_only, r_gain, r_duck, r_loud}) == 4


def test_put_audio_plan_with_plan_config(tmp_path):
    from tests.test_44b_assembly_fixture import build_media_project
    build_media_project(tmp_path)
    svc = SequenceService(tmp_path, "p44b")
    b = svc.create_sequence(clips=[{"shot_id": "s01_c01"}])
    plan = svc.put_audio_plan(
        b.sequence_id,
        [{"layer_id": "lay_m", "role": "music",
          "source": {"kind": AUDIO_SOURCE_KIND_IMPORTED,
                     "file": "audio_imports/x.wav"},
          "start_ms": 0, "duration_ms": 1000, "gain_db": -20.0}],
        plan_config={"base_gain_db": -3.0,
                     "ducking": SequenceDuckingConfig().to_dict(),
                     "loudness": SequenceLoudnessTarget().to_dict()})
    assert plan.base_gain_db == -3.0 and plan.ducking and plan.loudness
    rev1 = plan.revision
    # OCC 生效；配置替换生效
    with pytest.raises(SequenceError) as ei:
        svc.put_audio_plan(b.sequence_id, [], expected_revision="deadbeef")
    assert ei.value.code == "revision_conflict"
    plan2 = svc.put_audio_plan(
        b.sequence_id,
        [{"layer_id": "lay_m", "role": "music",
          "source": {"kind": AUDIO_SOURCE_KIND_IMPORTED, "file": "audio_imports/x.wav"},
          "start_ms": 0, "duration_ms": 1000, "gain_db": -20.0}],
        expected_revision=rev1,
        plan_config={"base_gain_db": -3.0, "ducking": None, "loudness": None})
    assert plan2.ducking is None and plan2.loudness is None
    assert plan2.revision != rev1


# ============ stem 编译 ============

def test_stem_graphs_single_and_multi():
    plan = SequenceAudioPlan(
        sequence_id="s",
        layers=[_layer("music", 0), _layer("narration", 4000),
                _layer("narration", 20000)],
        ducking=SequenceDuckingConfig())
    gm = compile_stem_filter_graph(_mix_plan(plan), ["music"])
    assert gm["input_args"] == ["-i", "lay_music_0.wav"]
    assert gm["filter_complex"].startswith("[0:a]")   # stem 无 base：文件索引从 0 起
    assert "-map" in gm["out_args"] and "[s0]" in gm["out_args"]
    gn = compile_stem_filter_graph(_mix_plan(plan), ["narration"])
    assert len(gn["input_args"]) == 4                       # 2 层 × ("-i", file)
    assert "amix=inputs=2:duration=longest" in gn["filter_complex"]
    # stem 不含 sidechain（旁白不在轨上，音乐不压低）
    assert "sidechaincompress" not in gm["filter_complex"]
    with pytest.raises(SequenceError):
        compile_stem_filter_graph(_mix_plan(plan), ["ambience"])


# ============ 旁白产物仓 ============

class FakeAdapter:
    """脚本化 adapter：返回固定字节 + 可注入错误序列。"""
    def __init__(self, audio=b"RAWMP3", errors=None):
        self.audio = audio
        self.errors = list(errors or [])
        self.calls = 0

    async def synthesize(self, pack):
        self.calls += 1
        if self.errors:
            from src.audio.minimax_adapter import VoiceProviderError
            raise self.errors.pop(0)
        return {"audio": self.audio,
                "provider_extra_info": {"trace_id": "tr_123"},
                "request_snapshot": {"model": "m", "text": pack.text},
                "latency_ms": 123,
                "word_timestamps": [],
                "word_timestamps_note": "word_timestamps_unavailable",
                "subtitle_file_url": "",
                "trace_id": "tr_123"}


@pytest.fixture()
def fake_media(monkeypatch):
    """零 ffmpeg：normalize 写固定字节；ffprobe 返回固定规格。"""
    from src.audio import narration as N
    monkeypatch.setattr(N, "normalize_to_production_wav",
                        lambda raw, out, src_suffix=".mp3":
                        (Path(out).parent.mkdir(parents=True, exist_ok=True),
                         Path(out).write_bytes(b"WAVBYTES-" + raw),
                         {"path": str(out), "audio_spec": {}})[2])
    monkeypatch.setattr(N, "ffprobe_audio",
                        lambda p: {"duration": 3.5, "format": "wav",
                                   "sample_rate": 48000, "channels": 1})
    return N


from pathlib import Path


def test_synthesize_and_idempotent_reuse(tmp_path, fake_media):
    pdir = tmp_path
    kw = dict(cue_id="nc01", text="测试旁白一句。", voice_id="v1", speed=0.9)
    ad = FakeAdapter()
    r1 = asyncio.run(fake_media.synthesize_narration_cue(pdir, adapter=ad, **kw))
    assert r1["ok"] and not r1["cache_hit"]
    v1 = r1["version"]
    assert v1["audio_import_path"].startswith("audio_imports/narration/nc01__")
    assert v1["duration_probe"]["duration"] == 3.5
    assert v1["provider_trace_id"] == "tr_123"
    # 音频文件在盘且哈希吻合
    f = pdir / "output" / v1["audio_import_path"]
    assert f.exists() and fake_media.sha256_file(f) == v1["audio_sha256"]
    # 幂等：同 key 命中缓存，零 API 调用
    r2 = asyncio.run(fake_media.synthesize_narration_cue(pdir, adapter=ad, **kw))
    assert r2["cache_hit"] and r2["version"]["version_id"] == v1["version_id"]
    assert ad.calls == 1                                    # 仅第一次真实调用
    # 参数变化 → 新版本（不同 idempotency key）
    r3 = asyncio.run(fake_media.synthesize_narration_cue(pdir, adapter=ad,
                                                         cue_id="nc01",
                                                         text="测试旁白一句。",
                                                         voice_id="v2", speed=0.9))
    assert not r3["cache_hit"] and r3["version"]["version_id"] != v1["version_id"]
    assert ad.calls == 2


def test_synthesize_cache_invalidated_when_file_missing(tmp_path, fake_media):
    pdir = tmp_path
    kw = dict(cue_id="nc02", text="第二句。", voice_id="v1", speed=0.9)
    ad = FakeAdapter()
    r1 = asyncio.run(fake_media.synthesize_narration_cue(pdir, adapter=ad, **kw))
    (pdir / "output" / r1["version"]["audio_import_path"]).unlink()
    r2 = asyncio.run(fake_media.synthesize_narration_cue(pdir, adapter=ad, **kw))
    assert not r2["cache_hit"] and r2["version"]["version_id"] != r1["version"]["version_id"]


def test_synthesize_rejects_empty_and_overlong(tmp_path, fake_media):
    from src.audio.minimax_adapter import VoiceProviderError
    with pytest.raises(VoiceProviderError) as ei:
        asyncio.run(fake_media.synthesize_narration_cue(tmp_path, cue_id="x", text="  "))
    assert ei.value.code == "narration_invalid"
    with pytest.raises(VoiceProviderError) as ei2:
        asyncio.run(fake_media.synthesize_narration_cue(
            tmp_path, cue_id="x", text="长" * 201))
    assert ei2.value.code == "narration_invalid"


def test_store_roundtrip_and_fail_closed(tmp_path, fake_media):
    r = asyncio.run(fake_media.synthesize_narration_cue(
        tmp_path, adapter=FakeAdapter(), cue_id="nc03", text="第三句。",
        voice_id="v1", speed=0.9))
    store = fake_media.NarrationStore(tmp_path)
    vs = store.versions_of("nc03")
    assert len(vs) == 1 and vs[0].to_dict() == r["version"]
    hit = store.find_by_key("nc03", vs[0].idempotency_key)
    assert hit is not None and hit.version_id == vs[0].version_id
    assert store.find_by_key("nc03", "nope") is None
    # 坏 schema fail closed
    (tmp_path / "output" / "narration_audio.json").write_text(
        json.dumps({"schema": "bogus"}), encoding="utf-8")
    with pytest.raises(ValueError):
        store.load()


# ============ C4：供应商故障传播（narration 层；adapter 契约见 test_voice_a1_adapter）============

def test_provider_error_propagates_and_persists_nothing(tmp_path, fake_media):
    """供应商错误（配额 1008 / 无效 voice 2049 / 网络超时）→ structured 错误上抛，
    产物仓零写入（不伪造成功、不留半截状态）。"""
    from src.audio import narration as N
    from src.audio.minimax_adapter import VoiceProviderError
    for code in ("provider_quota_exceeded", "provider_invalid_voice",
                 "provider_request_failed"):
        store = N.NarrationStore(tmp_path)
        before = len(store.load())
        ad = FakeAdapter(errors=[VoiceProviderError(code, "boom", {"k": 1})])
        with pytest.raises(VoiceProviderError) as ei:
            asyncio.run(fake_media.synthesize_narration_cue(
                tmp_path, adapter=ad, cue_id="ncx", text="故障句。",
                voice_id="v1", speed=0.9))
        assert ei.value.code == code
        assert len(N.NarrationStore(tmp_path).load()) == before


def test_url_expiry_does_not_affect_reproducibility(tmp_path, fake_media):
    """C4「短 URL 过期」：本链路音频走 hex 直解码+本地落盘（raw+wav 双留证），
    产物仓不依赖任何时效 URL——重放=本地文件哈希，与 provider URL 无关。"""
    r = asyncio.run(fake_media.synthesize_narration_cue(
        tmp_path, adapter=FakeAdapter(), cue_id="ncu", text="URL 过期句。",
        voice_id="v1", speed=0.9))
    v = r["version"]
    assert v["audio_import_path"] and v["audio_sha256"]
    assert not any("url" in k.lower() for k in v.keys() if isinstance(k, str)) or         "subtitle_file_url" not in v


def test_put_plan_int_base_gain_revision_stable(tmp_path):
    """44C 回归：put 时 base_gain_db 传 int（LLM 常见）→ 落盘重载后指纹必须一致
    （曾因 int/float 指纹差异导致 verified=False 与 revision 标签漂移）。"""
    from tests.test_44b_assembly_fixture import build_media_project
    build_media_project(tmp_path)
    svc = SequenceService(tmp_path, "p44b")
    b = svc.create_sequence(clips=[{"shot_id": "s01_c01"}])
    plan = svc.put_audio_plan(
        b.sequence_id,
        [{"layer_id": "lay_m", "role": "music",
          "source": {"kind": AUDIO_SOURCE_KIND_IMPORTED, "file": "audio_imports/x.wav"},
          "start_ms": 0, "duration_ms": 1000, "gain_db": -10}],
        plan_config={"base_gain_db": -3,       # int！
                     "ducking": {"threshold_db": -30, "ratio": 4,
                                 "attack_ms": 50, "release_ms": 800},
                     "loudness": {"i": -16, "tp": -2, "lra": 11}})
    rev_in_memory = plan.revision
    plan_reloaded = svc.get_audio_plan(b.sequence_id)
    assert plan_reloaded.revision == rev_in_memory        # 落盘重载指纹一致
    assert plan_reloaded.base_gain_db == -3.0 and isinstance(plan_reloaded.base_gain_db, float)


def test_synthesize_none_params_coerced(tmp_path, fake_media):
    """LLM 显式传 voice_id/speed=null → 归一到默认（曾 float(None) TypeError）。"""
    r = asyncio.run(fake_media.synthesize_narration_cue(
        tmp_path, adapter=FakeAdapter(), cue_id="ncN",
        text="空参数归一句。", voice_id=None, speed=None))
    v = r["version"]
    assert v["voice_id"] == "Chinese (Mandarin)_Gentleman"
    assert v["speed"] == 0.9
