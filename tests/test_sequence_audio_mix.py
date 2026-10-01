# tests/test_sequence_audio_mix.py
# Sequence S2 — AudioMixPlan 纯派生 + ffmpeg graph 编译确定性测试（25.md §31/§32/§21）。
import pytest

from src.sequence.audio_mix import (
    PEAK_WARNING_THRESHOLD_DB,
    AudioMixPlan,
    MixLayer,
    build_audio_mix_plan,
    compile_audio_filter_graph,
    parse_peak_db,
    peak_warning,
)
from src.sequence.models import SequenceError


def _layer(i, **over):
    base = dict(layer_id=f"layer_{i}", role="music", source_file=f"src_{i}.wav",
                source_in_ms=0, duration_ms=8000, start_ms=0,
                gain_db=-12.0, fade_in_ms=2000, fade_out_ms=3000)
    base.update(over)
    return MixLayer(**base)


def test_mix_plan_deterministic():
    a = build_audio_mix_plan("base.mp4", "srender_a", [_layer(1), _layer(2)])
    b = build_audio_mix_plan("base.mp4", "srender_a", [_layer(1), _layer(2)])
    assert a.to_dict() == b.to_dict()                    # §32 deterministic
    with pytest.raises(SequenceError):
        build_audio_mix_plan("base.mp4", "srender_a",
                             [_layer(1, fade_in_ms=5000, fade_out_ms=6000)])


def test_graph_zero_layers_is_passthrough_mix():
    """0 层也走同一编译器（amix inputs=1）——语义上等价 base 直通。"""
    g = compile_audio_filter_graph(build_audio_mix_plan("base.mp4", "srender_a", []))
    assert g["input_args"] == []
    assert g["filter_complex"] == "[0:a]amix=inputs=1:duration=first:normalize=0[mixed]"
    assert g["final_label"] == "[mixed]"    # 44C+：图只产中间 PCM（两遍渲染第一遍）


def test_graph_encodes_layer_semantics():
    plan = build_audio_mix_plan("base.mp4", "srender_a", [
        _layer(1, source_in_ms=12000, duration_ms=22800, start_ms=0,
               gain_db=-18.0, fade_in_ms=2000, fade_out_ms=3000),
        _layer(2, role="narration", start_ms=4200, duration_ms=5300,
               gain_db=-3.0, fade_in_ms=0, fade_out_ms=0),
    ])
    g = compile_audio_filter_graph(plan)
    assert g["input_args"] == ["-i", "src_1.wav", "-i", "src_2.wav"]
    fc = g["filter_complex"]
    # 层 1：源内 12s 起用 22.8s，-18dB，2s 淡入 / 3s 淡出，从 0ms 开始
    assert "[1:a]atrim=start=12.000000:end=34.800000" in fc
    assert "volume=-18.000000dB" in fc
    assert "afade=t=in:st=0:d=2.000000" in fc
    assert "afade=t=out:st=19.800000:d=3.000000" in fc   # 22.8-3=19.8（层自身时间轴）
    assert "adelay=delays=0:all=1" in fc
    # 层 2：4200ms 定位；无淡入淡出（d=0 afade 恒等）
    assert "[2:a]atrim=start=0.000000:end=5.300000" in fc
    assert "volume=-3.000000dB" in fc
    assert "adelay=delays=4200:all=1" in fc
    # amix：base + 2 层，normalize=0（不衰减），时长以 base 为准
    assert "[0:a][l1][l2]amix=inputs=3:duration=first:normalize=0[mixed]" in fc
    # 44C+：视频流 copy 断言移至 service 两遍渲染回归锁（单遍图→AAC 丢延迟层）


def test_graph_deterministic_same_plan_same_string():
    mk = lambda: compile_audio_filter_graph(build_audio_mix_plan(
        "base.mp4", "srender_a", [_layer(1), _layer(2, start_ms=8000)]))
    assert mk() == mk()


def test_parse_peak_and_warning():
    stderr = "[Parsed_volumedetect_0 @ x] max_volume: -18.1 dB\nmean_volume: -30.0 dB"
    assert parse_peak_db(stderr) == -18.1
    assert parse_peak_db("nothing here") is None
    assert peak_warning(-18.1) is None                       # 安全 → None
    w = peak_warning(-0.1)                                    # §21：只报告不处理
    assert w and w["code"] == "audio_peak_warning" and w["max_volume_db"] == -0.1
    assert peak_warning(None) is None
    assert PEAK_WARNING_THRESHOLD_DB == -0.3
