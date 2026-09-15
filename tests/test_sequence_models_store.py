# tests/test_sequence_models_store.py
# Sequence S1 — models/store 确定性测试（24.md §32：truth/ordering/trim/version 纪律）。
import pytest

from src.sequence.models import (
    SEQUENCE_RENDER_PROFILE,
    RSTATUS_DONE,
    RSTATUS_QUEUED,
    SequenceBlock,
    SequenceClip,
    SequenceEntry,
    SequenceError,
    SequenceRenderVersion,
    render_profile_fingerprint,
)
from src.sequence.store import SequenceStore


# ============ SequenceClip（24.md §12 结构校验）============

def test_clip_valid_and_defaults():
    c = SequenceClip(shot_id="s01_c01")
    assert c.shot_id == "s01_c01" and c.trim_in_ms == 0 and c.trim_out_ms == 0


def test_clip_rejects_empty_shot_id():
    with pytest.raises(SequenceError):
        SequenceClip(shot_id="  ")


def test_clip_rejects_negative_and_nonint_trim():
    with pytest.raises(SequenceError):
        SequenceClip(shot_id="s01_c01", trim_in_ms=-1)
    with pytest.raises(SequenceError):
        SequenceClip(shot_id="s01_c01", trim_out_ms=1.5)
    with pytest.raises(SequenceError):
        SequenceClip(shot_id="s01_c01", trim_in_ms=True)   # bool 显式排除
    with pytest.raises(SequenceError):
        SequenceClip(shot_id="s01_c01", trim_in_ms="100")


def test_clip_dict_roundtrip_and_fail_closed():
    d = {"shot_id": "s01_c01", "trim_in_ms": 100, "trim_out_ms": 200}
    c = SequenceClip.from_dict(d)
    assert (c.shot_id, c.trim_in_ms, c.trim_out_ms) == ("s01_c01", 100, 200)
    with pytest.raises(SequenceError):
        SequenceClip.from_dict({"trim_in_ms": 0})          # 缺 shot_id
    with pytest.raises(SequenceError):
        SequenceClip.from_dict("not-a-dict")


# ============ SequenceBlock（24.md §5/§6 + revision 语义）============

def _block(clips, title="t", scene_id=None) -> SequenceBlock:
    return SequenceBlock(sequence_id="seq_test0001",
                         clips=[SequenceClip(**c) for c in clips],
                         title=title, scene_id=scene_id)


def test_block_rejects_empty_clips():
    with pytest.raises(SequenceError):
        SequenceBlock(sequence_id="seq_test0001", clips=[])


def test_revision_deterministic_and_order_sensitive():
    a = _block([{"shot_id": "a"}, {"shot_id": "b"}])
    b = _block([{"shot_id": "a"}, {"shot_id": "b"}])
    assert a.revision == b.revision and len(a.revision) == 8
    swapped = _block([{"shot_id": "b"}, {"shot_id": "a"}])
    assert swapped.revision != a.revision            # 顺序变化 = revision 变（§20）


def test_revision_ignores_display_metadata():
    a = _block([{"shot_id": "a", "trim_in_ms": 100}], title="t1")
    b = _block([{"shot_id": "a", "trim_in_ms": 100}], title="t2", scene_id="s01")
    assert a.revision == b.revision                  # title/scene_id 不进 revision


def test_revision_covers_trim():
    a = _block([{"shot_id": "a", "trim_in_ms": 0}])
    b = _block([{"shot_id": "a", "trim_in_ms": 100}])
    assert a.revision != b.revision


def test_block_dict_roundtrip():
    blk = _block([{"shot_id": "a", "trim_in_ms": 10, "trim_out_ms": 20}, {"shot_id": "b"}],
                 title="测试序列", scene_id="s01")
    blk2 = SequenceBlock.from_dict(blk.to_dict())
    assert blk2.revision == blk.revision
    assert blk2.clips[0].trim_out_ms == 20
    assert blk2.title == "测试序列"
    with pytest.raises(SequenceError):
        SequenceBlock.from_dict({"clips": []})       # 缺 sequence_id
    with pytest.raises(SequenceError):
        SequenceBlock.from_dict({"sequence_id": "seq_x", "clips": []})


# ============ SequenceRenderVersion / Entry ============

def test_render_version_status_validated():
    rv = SequenceRenderVersion(version_id="srender_a1", sequence_id="seq_test0001")
    assert rv.status == RSTATUS_QUEUED
    with pytest.raises(SequenceError):
        SequenceRenderVersion(version_id="srender_a1", sequence_id="seq_test0001",
                              status="bogus")
    with pytest.raises(SequenceError):
        SequenceRenderVersion(version_id="", sequence_id="seq_test0001")


def test_render_version_known_field_filter():
    rv = SequenceRenderVersion(version_id="srender_a1", sequence_id="seq_test0001").to_dict()
    rv["unknown_future_field"] = "x"                 # 前向兼容：未知字段过滤不炸
    rv2 = SequenceRenderVersion.from_dict(rv)
    assert rv2.version_id == "srender_a1"
    with pytest.raises(SequenceError):
        SequenceRenderVersion.from_dict({"sequence_id": "seq_x"})   # 缺 version_id


def test_entry_final_render_pointer_semantics():
    blk = _block([{"shot_id": "a"}])
    entry = SequenceEntry(block=blk)
    assert entry.final_render_version() is None      # 无指针 → None
    done = SequenceRenderVersion(version_id="srender_d1", sequence_id=blk.sequence_id,
                                 status=RSTATUS_DONE)
    entry.render_versions.append(done)
    entry.final_render = "srender_d1"
    assert entry.final_render_version() is done
    # 指针悬空 / 指向非 done → 诚实 None（防御）
    entry.final_render = "srender_missing"
    assert entry.final_render_version() is None
    entry.final_render = "srender_q"
    entry.render_versions.append(SequenceRenderVersion(
        version_id="srender_q", sequence_id=blk.sequence_id, status=RSTATUS_QUEUED))
    assert entry.final_render_version() is None


def test_render_profile_constant_and_fingerprint():
    # §16：当前生产现实实测规格——profile 常量钉死
    assert SEQUENCE_RENDER_PROFILE["width"] == 1280
    assert SEQUENCE_RENDER_PROFILE["height"] == 704
    assert SEQUENCE_RENDER_PROFILE["fps"] == 24
    assert SEQUENCE_RENDER_PROFILE["pix_fmt"] == "yuv420p"
    assert SEQUENCE_RENDER_PROFILE["sample_rate"] == 48000
    assert SEQUENCE_RENDER_PROFILE["channels"] == 2
    fp = render_profile_fingerprint()
    assert fp == render_profile_fingerprint() and len(fp) == 8


# ============ SequenceStore（独立 sequences.json；fail closed）============

def test_store_roundtrip(tmp_path):
    store = SequenceStore(tmp_path, project_name="p1")
    assert store.load_or_none() is None              # 纯读零副作用，不创建文件
    assert not store.path.exists()
    blk = _block([{"shot_id": "a", "trim_in_ms": 5}])
    entry = SequenceEntry(block=blk)
    entry.render_versions.append(SequenceRenderVersion(
        version_id="srender_d1", sequence_id=blk.sequence_id, status=RSTATUS_DONE))
    entry.final_render = "srender_d1"
    store.save({blk.sequence_id: entry})
    loaded = store.load()
    assert set(loaded.keys()) == {"seq_test0001"}
    e = loaded["seq_test0001"]
    assert e.block.revision == blk.revision
    assert e.final_render == "srender_d1"
    assert e.final_render_version().status == RSTATUS_DONE


def test_store_fail_closed_on_bad_schema(tmp_path):
    store = SequenceStore(tmp_path, project_name="p1")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text('{"schema_version": 99, "sequences": {}}', encoding="utf-8")
    with pytest.raises(SequenceError):
        store.load_or_none()
    store.path.write_text("not json", encoding="utf-8")
    with pytest.raises(SequenceError):
        store.load_or_none()
    store.path.write_text('{"schema_version": 1}', encoding="utf-8")
    with pytest.raises(SequenceError):
        store.load_or_none()                          # 缺 sequences 对象
