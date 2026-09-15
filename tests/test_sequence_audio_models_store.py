# tests/test_sequence_audio_models_store.py
# Sequence S2 — AudioPlan/Layer/SourceRef 模型与 store 确定性测试（25.md §7-§17/§46）。
import pytest

from src.sequence.audio_models import (
    AUDIO_SOURCE_KIND_A1,
    AUDIO_SOURCE_KIND_IMPORTED,
    SequenceAudioLayer,
    SequenceAudioPlan,
    SequenceAudioSourceRef,
)
from src.sequence.audio_store import SequenceAudioPlanStore
from src.sequence.models import SequenceError


# ============ SourceRef（§9/§10）============

def test_source_ref_a1_requires_explicit_ids():
    r = SequenceAudioSourceRef(kind=AUDIO_SOURCE_KIND_A1,
                               dialogue_id="s01_c01", audio_version_id="s01_c01_av03")
    assert r.semantic_snapshot() == {"kind": "a1_dialogue_audio",
                                     "dialogue_id": "s01_c01",
                                     "audio_version_id": "s01_c01_av03"}
    with pytest.raises(SequenceError):   # §10：绝不默认 selected
        SequenceAudioSourceRef(kind=AUDIO_SOURCE_KIND_A1, dialogue_id="s01_c01")
    with pytest.raises(SequenceError):
        SequenceAudioSourceRef(kind=AUDIO_SOURCE_KIND_A1, dialogue_id="s01_c01",
                               audio_version_id="x", file="audio_imports/a.wav")


def test_source_ref_imported_path_guard():
    r = SequenceAudioSourceRef(kind=AUDIO_SOURCE_KIND_IMPORTED,
                               file="audio_imports/music_pad.wav")
    assert r.semantic_snapshot() == {"kind": "imported_audio",
                                     "file": "audio_imports/music_pad.wav"}
    for bad in ("C:/abs/path.wav", "../escape.wav", "other_dir/a.wav",
                "audio_imports/../../x.wav", "", None):
        with pytest.raises(SequenceError):
            SequenceAudioSourceRef(kind=AUDIO_SOURCE_KIND_IMPORTED, file=bad)


def test_source_ref_kind_fail_closed_and_roundtrip():
    with pytest.raises(SequenceError):
        SequenceAudioSourceRef(kind="mystery")
    d = SequenceAudioSourceRef(kind=AUDIO_SOURCE_KIND_A1,
                               dialogue_id="d", audio_version_id="v").to_dict()
    d2 = SequenceAudioSourceRef.from_dict(d)
    assert d2.dialogue_id == "d"
    d["future_field"] = 1                       # 前向兼容：未知字段过滤
    assert SequenceAudioSourceRef.from_dict(d).kind == AUDIO_SOURCE_KIND_A1
    with pytest.raises(SequenceError):
        SequenceAudioSourceRef.from_dict({"dialogue_id": "d"})   # 缺 kind


# ============ Layer（§8/§11-§17 结构校验）============

def _layer(**over):
    base = dict(layer_id="layer_a", role="music",
                source={"kind": AUDIO_SOURCE_KIND_IMPORTED,
                        "file": "audio_imports/m.wav"},
                start_ms=1000, duration_ms=5000)
    base.update(over)
    return SequenceAudioLayer(**base)


def test_layer_valid_defaults_and_role_limit():
    l = _layer()
    assert l.source_in_ms == 0 and l.gain_db == 0.0 and l.fade_in_ms == 0
    with pytest.raises(SequenceError):   # §8：role 严格限制
        _layer(role="dialogue")
    with pytest.raises(SequenceError):
        _layer(role="sfx")
    with pytest.raises(SequenceError):
        _layer(role="")


def test_layer_time_and_gain_fail_closed():
    with pytest.raises(SequenceError):
        _layer(start_ms=-1)
    with pytest.raises(SequenceError):
        _layer(source_in_ms=-100)
    with pytest.raises(SequenceError):
        _layer(duration_ms=0)
    with pytest.raises(SequenceError):
        _layer(duration_ms=1.5)
    with pytest.raises(SequenceError):
        _layer(gain_db=-61)              # 越界 fail closed（§16 防手误）
    with pytest.raises(SequenceError):
        _layer(gain_db=25)
    with pytest.raises(SequenceError):
        _layer(gain_db=True)
    with pytest.raises(SequenceError):
        _layer(fade_in_ms=-1)


def test_layer_fade_sum_bound():
    assert _layer(duration_ms=5000, fade_in_ms=2000, fade_out_ms=3000)  # 和 == duration 合法
    with pytest.raises(SequenceError):
        _layer(duration_ms=5000, fade_in_ms=2001, fade_out_ms=3000)


def test_layer_revision_semantics_exclude_layer_id():
    a = _layer(layer_id="layer_a", gain_db=-12)
    b = _layer(layer_id="layer_other", gain_db=-12)
    assert a.semantic_snapshot() == b.semantic_snapshot()   # 同语义 = 同混音
    assert _layer(gain_db=-12).semantic_snapshot() != _layer(gain_db=-20).semantic_snapshot()
    assert _layer(start_ms=1).semantic_snapshot() != _layer(start_ms=2).semantic_snapshot()


def test_layer_dict_roundtrip_autogen_id():
    d = _layer().to_dict()
    assert SequenceAudioLayer.from_dict(d).layer_id == "layer_a"
    del d["layer_id"]
    l2 = SequenceAudioLayer.from_dict(d)     # 缺 layer_id → 自动分配（导入便利）
    assert l2.layer_id.startswith("layer_")
    with pytest.raises(SequenceError):
        SequenceAudioLayer.from_dict({"role": "music"})   # 缺 source/start/duration


# ============ Plan revision（§29 独立 revision domain）============

def _plan(layers):
    return SequenceAudioPlan(sequence_id="seq_x", layers=layers)


def test_plan_revision_deterministic_and_sensitive():
    p1 = _plan([_layer(gain_db=-12), _layer(role="narration", start_ms=9000)])
    p2 = _plan([_layer(gain_db=-12), _layer(role="narration", start_ms=9000)])
    assert p1.revision == p2.revision and len(p1.revision) == 8
    assert _plan(list(reversed(p1.layers))).revision != p1.revision   # 顺序敏感
    assert _plan([_layer(gain_db=-20)]).revision != _plan([_layer(gain_db=-12)]).revision


def test_plan_dict_roundtrip():
    p = _plan([_layer(), _layer(role="ambience", start_ms=8000, duration_ms=14000,
                                gain_db=-18, fade_in_ms=1500)])
    p2 = SequenceAudioPlan.from_dict(p.to_dict())
    assert p2.revision == p.revision and len(p2.layers) == 2
    with pytest.raises(SequenceError):
        SequenceAudioPlan.from_dict({"layers": []})   # 缺 sequence_id


# ============ Store（独立 sequence_audio_plans.json）============

def test_plan_store_roundtrip(tmp_path):
    store = SequenceAudioPlanStore(tmp_path, project_name="p1")
    assert store.load_or_none() is None and store.plan_for("seq_x") is None
    plan = _plan([_layer()])
    store.save({"seq_x": plan})
    got = store.plan_for("seq_x")
    assert got.revision == plan.revision and got.layers[0].gain_db == 0.0


def test_plan_store_empty_plan_not_persisted(tmp_path):
    """空 layers 的 plan 不落盘（canonical 归一化：空 plan ≡ 无 plan）。"""
    store = SequenceAudioPlanStore(tmp_path, project_name="p1")
    store.save({"seq_x": SequenceAudioPlan(sequence_id="seq_x", layers=[])})
    assert not store.path.exists() or store.plan_for("seq_x") is None


def test_plan_store_fail_closed(tmp_path):
    store = SequenceAudioPlanStore(tmp_path, project_name="p1")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text('{"schema_version": 99, "plans": {}}', encoding="utf-8")
    with pytest.raises(SequenceError):
        store.load_or_none()
    store.path.write_text("not json", encoding="utf-8")
    with pytest.raises(SequenceError):
        store.load_or_none()
