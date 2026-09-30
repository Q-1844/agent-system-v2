# tests/test_44b_assembly_manifest.py
# Phase 44B — AssemblyManifest 构建器/不变式/只追加落盘确定性测试（零 ffmpeg）。
# 最小不变式（44B B1）：source_out>source_in 且不超源时长；时间线确定；版本/哈希/
# 快照完全匹配；媒体丢失/换料 → 可定位错误。
import pytest

from src.core.store import ProjectStore
from src.sequence.assembly_manifest import (
    build_assembly_manifest,
    load_assembly_index,
    load_assembly_manifest,
    publish_assembly_manifest,
    validate_assembly_manifest,
)
from src.sequence.models import SequenceBlock, SequenceError

from tests.test_44b_assembly_fixture import (
    build_media_project,
    build_snapshot_manifest,
    fake_probe,
)


def _build(tmp_path, mode="formal"):
    files = build_media_project(tmp_path)
    snap = build_snapshot_manifest(files, mode=mode)
    proj = ProjectStore(tmp_path).load()
    block = SequenceBlock(sequence_id="seq_t", clips=[
        {"shot_id": "s01_c01"}, {"shot_id": "s01_c02"}, {"shot_id": "s02_c01"}])
    return files, snap, proj, block


# ============ 构建器：确定性 + 时间线 ============

def test_build_deterministic_timeline(tmp_path):
    _, snap, proj, block = _build(tmp_path)
    d1 = build_assembly_manifest(proj, snap, block, assembly_revision=1,
                                 probe_fn=fake_probe)
    d2 = build_assembly_manifest(proj, snap, block, assembly_revision=1,
                                 probe_fn=fake_probe)
    assert d1 == d2   # 同输入同输出（含 doc_fingerprint）
    items = d1["shot_items"]
    assert [it["shot_id"] for it in items] == ["s01_c01", "s01_c02", "s02_c01"]
    # 全整镜：source 0..8000，cursor 单调累加
    for it in items:
        assert it["source_in_ms"] == 0 and it["source_out_ms"] == 8000
    assert [it["timeline_start_ms"] for it in items] == [0, 8000, 16000]
    assert [it["timeline_end_ms"] for it in items] == [8000, 16000, 24000]
    assert d1["total_duration_ms"] == 24000
    # 跨组切点标记：第 3 项 s01→s02 边界
    assert [it["scene_boundary"] for it in items] == [False, False, True]
    # 剪接点 = shot_items 数 - 1；transition 恒硬切
    assert d1["seam_count"] == 2
    assert all(it["transition"] == "cut" for it in items)
    # C 批次占位契约
    assert d1["narration_cues"] == [] and d1["music_bed"] is None
    assert d1["audio_strategy"] == "native"
    # pin 三元组逐镜在档
    it0 = items[0]
    assert it0["video_version_id"] == "vv_s01c01"
    assert len(it0["media_sha256"]) == 64
    assert it0["approved_status"] == "user_locked"


def test_build_with_trim_and_fingerprint_change(tmp_path):
    files, snap, proj, block = _build(tmp_path)
    block.clips[0].trim_in_ms = 500
    block.clips[2].trim_out_ms = 200
    d = build_assembly_manifest(proj, snap, block, assembly_revision=1,
                                probe_fn=fake_probe)
    it0, _, it2 = d["shot_items"]
    assert it0["source_in_ms"] == 500 and it0["timeline_start_ms"] == 0
    assert it2["source_out_ms"] == 7800
    assert it2["timeline_start_ms"] == 15500
    assert d["total_duration_ms"] == 23300
    assert d["doc_fingerprint"]


# ============ 不变式 fail closed（带 shot_id 定位）============

def test_version_mismatch_fail_closed(tmp_path):
    files, snap, proj, block = _build(tmp_path)
    snap["shots"][0]["video_version_id"] = "vv_other"
    with pytest.raises(SequenceError) as ei:
        build_assembly_manifest(proj, snap, block, assembly_revision=1,
                                probe_fn=fake_probe)
    assert ei.value.code == "version_mismatch"
    assert "s01_c01" in ei.value.message


def test_hash_mismatch_fail_closed(tmp_path):
    files, snap, proj, block = _build(tmp_path)
    snap["shots"][1]["media_sha256"] = "0" * 64
    with pytest.raises(SequenceError) as ei:
        build_assembly_manifest(proj, snap, block, assembly_revision=1,
                                probe_fn=fake_probe)
    assert ei.value.code == "hash_mismatch"
    assert "s01_c02" in ei.value.message


def test_shot_not_in_snapshot_fail_closed(tmp_path):
    files, snap, proj, _ = _build(tmp_path)
    rogue = SequenceBlock(sequence_id="seq_r", clips=[
        {"shot_id": "s01_c01"}, {"shot_id": "s01_c01"},
        {"shot_id": "s02_c01"}])
    with pytest.raises(SequenceError) as ei:
        build_assembly_manifest(proj, snap, rogue, assembly_revision=1,
                                probe_fn=fake_probe)
    assert ei.value.code == "manifest_invalid"   # 重复 shot_id


def test_media_missing_fail_closed(tmp_path):
    files, snap, proj, block = _build(tmp_path)
    # 源文件被删（媒体丢失 → 可定位错误，不偷换）
    import os
    os.unlink(files["s02_c01"]["path"])
    with pytest.raises(SequenceError) as ei:
        build_assembly_manifest(proj, snap, block, assembly_revision=1,
                                probe_fn=fake_probe)
    assert ei.value.code == "source_unreadable"
    assert "s02_c01" in ei.value.message


def test_trim_worse_than_source_fail_closed(tmp_path):
    _, snap, proj, block = _build(tmp_path)
    block.clips[0].trim_in_ms = 7900
    block.clips[0].trim_out_ms = 500
    with pytest.raises(SequenceError) as ei:
        build_assembly_manifest(proj, snap, block, assembly_revision=1,
                                probe_fn=fake_probe)
    assert ei.value.code in ("trim_invalid",)


# ============ 校验/篡改防护 ============

def test_validate_and_tamper_detection(tmp_path):
    _, snap, proj, block = _build(tmp_path)
    d = build_assembly_manifest(proj, snap, block, assembly_revision=1,
                                probe_fn=fake_probe)
    validate_assembly_manifest(d)
    tampered = dict(d)
    tampered["total_duration_ms"] = 999999
    with pytest.raises(SequenceError) as ei:
        validate_assembly_manifest(tampered)
    assert ei.value.code == "manifest_tampered"
    # 时间线断链检测（绕过指纹改写）
    forged = dict(d)
    forged["shot_items"] = [dict(it) for it in d["shot_items"]]
    forged["shot_items"][1] = dict(forged["shot_items"][1],
                                   timeline_start_ms=12345)
    forged["doc_fingerprint"] = d["doc_fingerprint"]
    with pytest.raises(SequenceError) as ei2:
        validate_assembly_manifest(forged)
    assert ei2.value.code == "manifest_tampered"


# ============ 只追加落盘 ============

def test_publish_append_only_and_load(tmp_path):
    _, snap, proj, block = _build(tmp_path)
    d1 = build_assembly_manifest(proj, snap, block, assembly_revision=1,
                                 probe_fn=fake_probe)
    r1 = publish_assembly_manifest(tmp_path, d1)
    assert r1["index"]["latest_revision"] == 1
    # 重复内容拒绝空转版本
    with pytest.raises(SequenceError) as ei:
        publish_assembly_manifest(tmp_path,
                                  build_assembly_manifest(
                                      proj, snap, block, assembly_revision=2,
                                      probe_fn=fake_probe))
    assert ei.value.code == "manifest_revision_conflict"
    # 改 canonical → 新内容可发布 v2
    block.clips[0].trim_out_ms = 100
    d2 = build_assembly_manifest(proj, snap, block, assembly_revision=2,
                                 probe_fn=fake_probe)
    publish_assembly_manifest(tmp_path, d2)
    assert load_assembly_index(tmp_path)["latest_revision"] == 2
    # 乱序/覆盖拒绝
    with pytest.raises(SequenceError):
        publish_assembly_manifest(tmp_path, d1)   # v1 已存在 → conflict
    # 读取：最新 + 指定版
    assert load_assembly_manifest(tmp_path)["assembly_revision"] == 2
    assert load_assembly_manifest(tmp_path, 1)["assembly_revision"] == 1
    with pytest.raises(SequenceError):
        load_assembly_manifest(tmp_path, 7)
    # 未发布过 → 最新读取报 not_found
    empty = tmp_path / "empty_proj"
    empty.mkdir()
    with pytest.raises(SequenceError) as ei2:
        load_assembly_manifest(empty)
    assert ei2.value.code == "manifest_not_found"


def test_engineering_mode_recorded(tmp_path):
    """快照 mode=production → manifest 记录工程预览口径（不伪装正式）。"""
    _, snap, proj, block = _build(tmp_path, mode="production")
    d = build_assembly_manifest(proj, snap, block, assembly_revision=1,
                                probe_fn=fake_probe)
    assert d["snapshot_mode"] == "production"
