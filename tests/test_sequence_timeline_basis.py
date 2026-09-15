# tests/test_sequence_timeline_basis.py
# Sequence S1 — timeline 数学（24.md §13）、Source Video Truth（§7）、
# sequence_render_basis_v1（§19）、freshness 状态机（§20/§21）确定性测试。
import hashlib

import pytest

from src.core.models import (
    Project, Scene, Shot, ShotStatus, VideoVersion,
)
from src.sequence.basis import (
    build_render_basis,
    compute_current_basis,
    resolve_clip_source,
    sequence_render_freshness,
    source_video_fingerprint,
)
from src.sequence.models import (
    RSTATUS_DONE,
    SequenceBlock,
    SequenceClip,
    SequenceError,
    SequenceRenderVersion,
)
from src.sequence.timeline import build_timeline_plan, validate_trims_vs_source, ResolvedClip


# ============ fixture：内存 Project（不落盘、不碰真实项目）============

def _mk_project(tmp_path) -> Project:
    """两镜两版本：s01_c01 有 2 个 done 版本（供切换 final 测试），s01_c02 一个。"""
    f1a = tmp_path / "s01_c01_v1.mp4"
    f1b = tmp_path / "s01_c01_v2.mp4"
    f2 = tmp_path / "s01_c02_v1.mp4"
    f1a.write_bytes(b"video-bytes-v1")
    f1b.write_bytes(b"video-bytes-v2")
    f2.write_bytes(b"video-bytes-b1")
    vv1a = VideoVersion(version_id="vv_a1", local=str(f1a), status=ShotStatus.DONE.value)
    vv1b = VideoVersion(version_id="vv_a2", local=str(f1b), status=ShotStatus.DONE.value)
    vv2 = VideoVersion(version_id="vv_b1", local=str(f2), status=ShotStatus.DONE.value)
    s1 = Shot(shot_id="s01_c01", scene_id="s01", index=1,
              video_versions=[vv1a, vv1b], final_video="vv_a1")
    s2 = Shot(shot_id="s01_c02", scene_id="s01", index=2,
              video_versions=[vv2], final_video="vv_b1")
    return Project(name="p", scenes=[Scene(scene_id="s01", shots=[s1, s2])])


# ============ §12 trim 验证（fail closed）============

def test_validate_trims_happy():
    assert validate_trims_vs_source(6000, 0, 500, shot_id="x") == 5500
    assert validate_trims_vs_source(8000, 500, 500, shot_id="x") == 7000


def test_validate_trims_fail_closed():
    with pytest.raises(SequenceError) as ei:
        validate_trims_vs_source(6000, 3000, 3000, shot_id="x")   # == dur
    assert ei.value.code == "trim_invalid"
    with pytest.raises(SequenceError) as ei2:
        validate_trims_vs_source(6000, 3000, 3001, shot_id="x")   # > dur
    assert ei2.value.code == "trim_invalid"
    with pytest.raises(SequenceError) as ei3:
        validate_trims_vs_source(0, 0, 0, shot_id="x")
    assert ei3.value.code == "source_unreadable"


# ============ §13 timeline 数学（规格示例逐字验证）============

def test_timeline_plan_spec_example():
    # 24.md §13 示例：clip0 6000ms trim 0/500 → 5500 @0→5500；
    #                clip1 8000ms trim 500/500 → 7000 @5500→12500；总长 12500
    resolved = [
        ResolvedClip(index=0, shot_id="a", version_id="v0", source_file="f0.mp4",
                     trim_in_ms=0, trim_out_ms=500, source_duration_ms=6000),
        ResolvedClip(index=1, shot_id="b", version_id="v1", source_file="f1.mp4",
                     trim_in_ms=500, trim_out_ms=500, source_duration_ms=8000),
    ]
    plan = build_timeline_plan(resolved)
    assert plan.clips[0].effective_ms == 5500
    assert (plan.clips[0].timeline_start_ms, plan.clips[0].timeline_end_ms) == (0, 5500)
    assert plan.clips[1].effective_ms == 7000
    assert (plan.clips[1].timeline_start_ms, plan.clips[1].timeline_end_ms) == (5500, 12500)
    assert plan.total_duration_ms == 12500            # sum(effective)，无 overlap


def test_timeline_plan_deterministic():
    resolved = [ResolvedClip(index=0, shot_id="a", version_id="v", source_file="f",
                             trim_in_ms=10, trim_out_ms=20, source_duration_ms=1000)]
    assert build_timeline_plan(resolved).to_dict() == build_timeline_plan(resolved).to_dict()


# ============ §7 Source Video Truth（final_video 唯一真相；禁止回落）============

def test_resolve_clip_source_happy(tmp_path):
    project = _mk_project(tmp_path)
    shot, version = resolve_clip_source(project, SequenceClip(shot_id="s01_c01"))
    assert shot.shot_id == "s01_c01" and version.version_id == "vv_a1"


def test_resolve_clip_source_fail_closed(tmp_path):
    project = _mk_project(tmp_path)
    with pytest.raises(SequenceError) as e:
        resolve_clip_source(project, SequenceClip(shot_id="s09_c09"))
    assert e.value.code == "shot_not_found"
    project.scenes[0].shots[0].final_video = None
    with pytest.raises(SequenceError) as e2:
        resolve_clip_source(project, SequenceClip(shot_id="s01_c01"))
    assert e2.value.code == "shot_has_no_final_video"
    project.scenes[0].shots[0].final_video = "vv_missing"
    with pytest.raises(SequenceError) as e3:
        resolve_clip_source(project, SequenceClip(shot_id="s01_c01"))
    assert e3.value.code == "final_video_version_missing"
    project.scenes[0].shots[0].final_video = "vv_a1"
    project.scenes[0].shots[0].video_versions[0].status = ShotStatus.GENERATING.value
    with pytest.raises(SequenceError) as e4:
        resolve_clip_source(project, SequenceClip(shot_id="s01_c01"))
    assert e4.value.code == "final_video_not_done"


# ============ §19 basis ============

def test_source_video_fingerprint_is_file_sha256(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"abc")
    assert source_video_fingerprint(str(f)) == hashlib.sha256(b"abc").hexdigest()
    f.write_bytes(b"abd")                              # 文件变 → 指纹变（§20）
    assert source_video_fingerprint(str(f)) != hashlib.sha256(b"abc").hexdigest()
    with pytest.raises(SequenceError) as e:
        source_video_fingerprint(str(tmp_path / "missing.mp4"))
    assert e.value.code == "source_file_missing"


def test_build_render_basis_keys(tmp_path):
    project = _mk_project(tmp_path)
    blk = SequenceBlock(sequence_id="seq_b", clips=[
        SequenceClip(shot_id="s01_c01", trim_in_ms=100),
        SequenceClip(shot_id="s01_c02")])
    resolved = [
        ResolvedClip(index=0, shot_id="s01_c01", version_id="vv_a1",
                     source_file=str(tmp_path / "s01_c01_v1.mp4"),
                     trim_in_ms=100, trim_out_ms=0, source_duration_ms=5000),
        ResolvedClip(index=1, shot_id="s01_c02", version_id="vv_b1",
                     source_file=str(tmp_path / "s01_c02_v1.mp4"),
                     trim_in_ms=0, trim_out_ms=0, source_duration_ms=5000),
    ]
    basis = build_render_basis(blk, resolved)
    assert basis["schema"] == "sequence_render_basis_v1"
    assert basis["sequence_id"] == "seq_b"
    assert basis["sequence_revision"] == blk.revision
    assert basis["renderer_schema"] == "sequence_renderer_v1"
    assert len(basis["render_profile_fingerprint"]) == 8
    assert basis["clips"][0]["resolved_video_version_id"] == "vv_a1"
    assert basis["clips"][0]["source_video_fingerprint"] == hashlib.sha256(
        b"video-bytes-v1").hexdigest()
    assert basis["clips"][0]["trim_in_ms"] == 100
    # actual dependencies only：无 timestamp / temp path / project 全量
    assert "created_at" not in basis and "project" not in basis


# ============ §20/§21 freshness 状态机 ============

def _prepared_block_and_render(tmp_path):
    """构建 block + 按「当时」状态生成 done render version（带 basis）。"""
    project = _mk_project(tmp_path)
    blk = SequenceBlock(sequence_id="seq_f", clips=[
        SequenceClip(shot_id="s01_c01"), SequenceClip(shot_id="s01_c02")])
    resolved = [
        ResolvedClip(index=0, shot_id="s01_c01", version_id="vv_a1",
                     source_file=str(tmp_path / "s01_c01_v1.mp4"),
                     trim_in_ms=0, trim_out_ms=0, source_duration_ms=5000),
        ResolvedClip(index=1, shot_id="s01_c02", version_id="vv_b1",
                     source_file=str(tmp_path / "s01_c02_v1.mp4"),
                     trim_in_ms=0, trim_out_ms=0, source_duration_ms=5000),
    ]
    rv = SequenceRenderVersion(version_id="srender_f1", sequence_id=blk.sequence_id,
                               status=RSTATUS_DONE,
                               sequence_revision=blk.revision,
                               generation_basis=build_render_basis(blk, resolved))
    return project, blk, rv


def test_freshness_current(tmp_path):
    project, blk, rv = _prepared_block_and_render(tmp_path)
    out = sequence_render_freshness(project, blk, rv)
    assert out["status"] == "current"


def test_freshness_missing_and_untracked_legacy(tmp_path):
    project, blk, _ = _prepared_block_and_render(tmp_path)
    assert sequence_render_freshness(project, blk, None)["status"] == "missing"
    legacy = SequenceRenderVersion(version_id="srender_l", sequence_id=blk.sequence_id,
                                   status=RSTATUS_DONE, generation_basis={})
    assert sequence_render_freshness(project, blk, legacy)["status"] == "untracked_legacy"


def test_freshness_stale_on_final_video_switch(tmp_path):
    """24.md §21：B.final_video v→v' —— canonical 不变、旧 render STALE。"""
    project, blk, rv = _prepared_block_and_render(tmp_path)
    project.scenes[0].shots[0].final_video = "vv_a2"           # 切到另一个已有版本
    out = sequence_render_freshness(project, blk, rv)
    assert out["status"] == "stale"
    assert "clips[0].resolved_video_version_id" in out["detail"]["mismatched_keys"]
    assert "clips[0].source_video_fingerprint" in out["detail"]["mismatched_keys"]
    assert "sequence_revision" not in out["detail"]["mismatched_keys"]  # canonical 未变


def test_freshness_stale_on_trim_and_reorder(tmp_path):
    project, blk, rv = _prepared_block_and_render(tmp_path)
    blk.clips[0].trim_in_ms = 250                              # trim 变 → STALE
    out = sequence_render_freshness(project, blk, rv)
    assert out["status"] == "stale"
    assert "sequence_revision" in out["detail"]["mismatched_keys"]
    blk2 = SequenceBlock(sequence_id=blk.sequence_id, clips=list(reversed(blk.clips)))
    out2 = sequence_render_freshness(project, blk2, rv)        # 顺序变 → STALE
    assert out2["status"] == "stale"
    assert "sequence_revision" in out2["detail"]["mismatched_keys"]


def test_freshness_stale_on_source_file_change(tmp_path):
    project, blk, rv = _prepared_block_and_render(tmp_path)
    (tmp_path / "s01_c02_v1.mp4").write_bytes(b"video-bytes-b1-CHANGED")  # 源文件被替换
    out = sequence_render_freshness(project, blk, rv)
    assert out["status"] == "stale"
    assert "clips[1].source_video_fingerprint" in out["detail"]["mismatched_keys"]


def test_freshness_unrelated_shot_change_stays_current(tmp_path):
    """24.md §20：无关 Shot 变化 → CURRENT（actual deps only）。"""
    project, blk, rv = _prepared_block_and_render(tmp_path)
    extra = Shot(shot_id="s01_c03", scene_id="s01", index=3,
                 video_versions=[VideoVersion(version_id="vv_x", local="x.mp4",
                                              status=ShotStatus.DONE.value)],
                 final_video="vv_x", dialogue="无关镜头台词改了")
    project.scenes[0].shots.append(extra)
    assert sequence_render_freshness(project, blk, rv)["status"] == "current"


def test_freshness_stale_on_unresolvable_source(tmp_path):
    project, blk, rv = _prepared_block_and_render(tmp_path)
    project.scenes[0].shots[0].final_video = None              # 依赖被清空
    out = sequence_render_freshness(project, blk, rv)
    assert out["status"] == "stale"
    assert "resolution_error" in out["detail"]["mismatched_keys"]


def test_compute_current_basis_matches_build(tmp_path):
    """Phase 1 快照与 Phase 3/freshness 重算同构（race guard 前提）。"""
    project, blk, rv = _prepared_block_and_render(tmp_path)
    current = compute_current_basis(project, blk)
    assert current == rv.generation_basis
    assert current.get("resolution_error") is None
