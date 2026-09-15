# tests/test_sequence_service.py
# Sequence S1 — service 三阶段 render / race guard / canonical ops 确定性测试。
# 媒体操作 monkeypatch 注入（单测零 ffmpeg——真实 ffmpeg 在真机 E2E 验证，24.md §29）。
import pytest

from src.core.models import (
    Project, Scene, Shot, ShotStatus, VideoVersion,
)
from src.core.store import ProjectStore
from src.sequence.models import (
    RSTATUS_DONE,
    RSTATUS_FAILED,
    RSTATUS_QUEUED,
    RSTATUS_RACED,
    RSTATUS_RENDERING,
    SequenceError,
)
from src.sequence.service import SequenceService


DURATIONS = {"s01_c01": 6000, "s01_c02": 8000}   # ms（§13 示例同量级）


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    """临时项目：s01_c01 两个 done 版本（切换测试用）+ s01_c02 一个；伪媒体操作。"""
    project_dir = tmp_path / "proj"
    vids = project_dir / "output" / "videos"
    vids.mkdir(parents=True)
    files = {
        "vv_a1": vids / "s01_c01_v1.mp4",
        "vv_a2": vids / "s01_c01_v2.mp4",
        "vv_b1": vids / "s01_c02_v1.mp4",
    }
    for f in files.values():
        f.write_bytes(b"fake-mp4-" + f.stem.encode())
    shots = [
        Shot(shot_id="s01_c01", scene_id="s01", index=1,
             video_versions=[
                 VideoVersion(version_id="vv_a1", local=str(files["vv_a1"]),
                              status=ShotStatus.DONE.value),
                 VideoVersion(version_id="vv_a2", local=str(files["vv_a2"]),
                              status=ShotStatus.DONE.value)],
             final_video="vv_a1"),
        Shot(shot_id="s01_c02", scene_id="s01", index=2,
             video_versions=[VideoVersion(version_id="vv_b1", local=str(files["vv_b1"]),
                                          status=ShotStatus.DONE.value)],
             final_video="vv_b1"),
    ]
    ProjectStore(project_dir).save(
        Project(name="proj", scenes=[Scene(scene_id="s01", shots=shots)]))

    # ---- 伪媒体操作（确定性；probe 时长按文件名映射）----
    def fake_probe(path):
        name = str(path)
        dur = 6000 if "s01_c01" in name else 8000
        return {"duration_ms": dur, "width": 1280, "height": 704, "fps": 24.0,
                "vcodec": "h264", "pix_fmt": "yuv420p", "has_video": True, "has_audio": True,
                "acodec": "aac", "sample_rate": 48000, "channels": 2}

    def fake_render_clip(src, dst, *, trim_in_ms, trim_out_ms, source_duration_ms,
                         has_audio, profile=None):
        from pathlib import Path as _P
        dst = _P(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"segment:" + dst.stem.encode())

    def fake_concat(paths, dst):
        from pathlib import Path as _P
        dst = _P(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"concat:" + str(len(paths)).encode())

    def fake_validate(path, planned_total_ms, n_clips):
        return {"probe": {"duration_ms": planned_total_ms, "has_audio": True},
                "duration_ms": planned_total_ms,
                "planned_duration_ms": planned_total_ms, "duration_delta_ms": 0}

    monkeypatch.setattr("src.sequence.media_ops.probe_video", fake_probe)
    monkeypatch.setattr("src.sequence.media_ops.render_clip", fake_render_clip)
    monkeypatch.setattr("src.sequence.media_ops.concat_clips", fake_concat)
    monkeypatch.setattr("src.sequence.media_ops.validate_output", fake_validate)
    yield SequenceService(project_dir, project_name="proj")
    # tmp_path 自动清理


def _create(svc, clips=None):
    return svc.create_sequence(
        title="测试序列", scene_id="s01",
        clips=clips or [{"shot_id": "s01_c01"}, {"shot_id": "s01_c02"}])


def _render_through_phase2(svc, sequence_id):
    """create → prepare → execute（Phase 1+2）。返回 (version_id, output)。"""
    rv = svc.create_render(sequence_id)
    prep = svc.prepare_render(sequence_id, rv.version_id)
    assert prep.render_version.status == RSTATUS_RENDERING
    output = svc.execute_render(sequence_id, prep.render_version.version_id, prep)
    return prep.render_version.version_id, output


# ============ canonical ops ============

def test_create_and_get(svc):
    blk = _create(svc)
    entry = svc.get_entry(blk.sequence_id)
    assert entry.block.title == "测试序列"
    assert [c.shot_id for c in entry.block.clips] == ["s01_c01", "s01_c02"]
    assert entry.render_versions == [] and entry.final_render is None


def test_create_rejects_empty_and_bad_clips(svc):
    with pytest.raises(SequenceError):
        svc.create_sequence(clips=[])
    with pytest.raises(SequenceError):
        svc.create_sequence(clips=[{"trim_in_ms": 0}])       # 缺 shot_id
    with pytest.raises(SequenceError):
        svc.create_sequence(clips=[{"shot_id": "a", "trim_in_ms": -5}])


def test_update_occ_and_display_only(svc):
    blk = _create(svc)
    with pytest.raises(SequenceError) as e:
        svc.update_sequence(blk.sequence_id, clips=[{"shot_id": "x"}],
                            expected_revision="deadbeef")
    assert e.value.code == "revision_conflict"
    r = svc.update_sequence(blk.sequence_id, title="改名",
                            expected_revision=blk.revision)
    assert r.title == "改名" and r.revision == blk.revision   # title 不动 revision
    r2 = svc.update_sequence(blk.sequence_id,
                             clips=[{"shot_id": "s01_c02"}, {"shot_id": "s01_c01"}])
    assert r2.revision != blk.revision                        # 顺序变 = revision 变


def test_delete_cleans_files(svc):
    blk = _create(svc)
    vid, _out = _render_through_phase2(svc, blk.sequence_id)
    out_file = svc.project_dir / "output" / "sequences" / blk.sequence_id / f"{vid}.mp4"
    assert out_file.exists()
    res = svc.delete_sequence(blk.sequence_id)
    assert res["deleted"] and res["removed_files"] >= 1
    assert not out_file.exists()
    with pytest.raises(SequenceError):
        svc.get_entry(blk.sequence_id)


# ============ Phase 1 prepare（§7/§12/§13/§14/§19）============

def test_prepare_snapshots_truth(svc):
    blk = _create(svc)
    rv = svc.create_render(blk.sequence_id)
    prep = svc.prepare_render(blk.sequence_id, rv.version_id)
    rv = prep.render_version
    assert rv.status == RSTATUS_RENDERING
    assert rv.planned_duration_ms == 6000 + 8000              # §13 sum(effective)
    assert rv.sequence_revision == blk.revision
    assert not prep.has_layers                                # 无 AudioPlan = S1 语义（25.md §25）
    assert rv.source_video_versions == [
        {"shot_id": "s01_c01", "version_id": "vv_a1"},
        {"shot_id": "s01_c02", "version_id": "vv_b1"}]
    assert rv.generation_basis["clips"][0]["resolved_video_version_id"] == "vv_a1"
    assert prep.timeline.total_duration_ms == 14000
    # §9：output_file 必须 Phase 1 落盘（消费方按此播放/导出，不得为 None）
    assert rv.output_file is not None
    assert rv.output_file.replace("\\", "/").endswith(
        f"output/sequences/{blk.sequence_id}/{rv.version_id}.mp4")


def test_prepare_fail_closed_no_final(svc):
    blk = _create(svc, clips=[{"shot_id": "s01_c01"}, {"shot_id": "s01_c02"}])
    # s01_c02 final 指针清空 → prepare fail closed，row 落 failed
    store = ProjectStore(svc.project_dir)
    store.update_shot("s01_c02", lambda s: setattr(s, "final_video", None))
    rv = svc.create_render(blk.sequence_id)
    with pytest.raises(SequenceError):
        svc.prepare_render(blk.sequence_id, rv.version_id)
    row = svc.get_entry(blk.sequence_id).get_render(rv.version_id)
    assert row.status == RSTATUS_FAILED and "shot_has_no_final_video" in row.error


def test_prepare_fail_closed_trim_exceeds_source(svc):
    blk = _create(svc, clips=[{"shot_id": "s01_c01", "trim_in_ms": 5000,
                               "trim_out_ms": 5000}])          # 5000+5000 >= 6000
    rv = svc.create_render(blk.sequence_id)
    with pytest.raises(SequenceError) as e:
        svc.prepare_render(blk.sequence_id, rv.version_id)
    assert e.value.code == "trim_invalid"
    row = svc.get_entry(blk.sequence_id).get_render(rv.version_id)
    assert row.status == RSTATUS_FAILED


# ============ Phase 2 execute + Phase 3 commit（§23）============

def test_execute_and_commit_happy(svc):
    blk = _create(svc)
    vid, output = _render_through_phase2(svc, blk.sequence_id)
    assert output.endswith(f"{vid}.mp4")
    res = svc.commit_render(blk.sequence_id, vid)
    assert res["status"] == RSTATUS_DONE and res["duration_ms"] == 14000
    row = svc.get_entry(blk.sequence_id).get_render(vid)
    assert row.status == RSTATUS_DONE
    # 🔴 §10：render 完成绝不自动 select——final_render 仍为 None
    assert svc.get_entry(blk.sequence_id).final_render is None


def test_race_guard_raced_superseded(svc):
    """§23：render 窗口内 B.final_video 变更 → commit 拒绝 attach，
    status=raced_superseded，输出保留为 evidence。"""
    blk = _create(svc)
    vid, output = _render_through_phase2(svc, blk.sequence_id)
    # 窗口内：切换 s01_c01 final → vv_a2（另一已有版本）
    ProjectStore(svc.project_dir).update_shot(
        "s01_c01", lambda s: setattr(s, "final_video", "vv_a2"))
    with pytest.raises(SequenceError) as e:
        svc.commit_render(blk.sequence_id, vid)
    assert e.value.code == "sequence_dependency_changed_before_commit"
    row = svc.get_entry(blk.sequence_id).get_render(vid)
    assert row.status == RSTATUS_RACED
    from pathlib import Path
    assert Path(output).exists()                              # evidence 保留
    # raced 版本不可 select
    with pytest.raises(SequenceError) as e2:
        svc.select_render(blk.sequence_id, vid)
    assert e2.value.code == "render_not_selectable"


def test_race_guard_trim_changed_in_window(svc):
    """§23：render 窗口内 sequence trim 变更 → 拒绝 attach。"""
    blk = _create(svc)
    vid, _ = _render_through_phase2(svc, blk.sequence_id)
    svc.update_sequence(blk.sequence_id, clips=[
        {"shot_id": "s01_c01", "trim_in_ms": 100}, {"shot_id": "s01_c02"}])
    with pytest.raises(SequenceError) as e:
        svc.commit_render(blk.sequence_id, vid)
    assert e.value.code == "sequence_dependency_changed_before_commit"


# ============ §10 explicit select ============

def test_select_explicit_done_only(svc):
    blk = _create(svc)
    vid, _ = _render_through_phase2(svc, blk.sequence_id)
    svc.commit_render(blk.sequence_id, vid)
    entry = svc.select_render(blk.sequence_id, vid)
    assert entry.final_render == vid
    fr = svc.get_entry(blk.sequence_id).final_render_version()
    assert fr is not None and fr.version_id == vid
    with pytest.raises(SequenceError):
        svc.select_render(blk.sequence_id, "srender_missing")
    queued = svc.create_render(blk.sequence_id)               # queued 不可 select
    with pytest.raises(SequenceError) as e:
        svc.select_render(blk.sequence_id, queued.version_id)
    assert e.value.code == "render_not_selectable"


# ============ §20/§21 freshness 读模型 ============

def test_freshness_lifecycle(svc):
    blk = _create(svc)
    assert svc.freshness(blk.sequence_id)["status"] == "missing"   # 无 render
    vid, _ = _render_through_phase2(svc, blk.sequence_id)
    svc.commit_render(blk.sequence_id, vid)
    svc.select_render(blk.sequence_id, vid)
    assert svc.freshness(blk.sequence_id)["status"] == "current"
    # §21：B.final_video 换版本 → 旧 sequence render STALE；canonical 不变
    ProjectStore(svc.project_dir).update_shot(
        "s01_c02", lambda s: setattr(s, "final_video", None))
    store = ProjectStore(svc.project_dir)
    project = store.load()
    shot = project.get_shot("s01_c02")
    shot.video_versions.append(VideoVersion(
        version_id="vv_b2", local=str(svc.project_dir / "output" / "videos" / "s01_c02_v2.mp4"),
        status=ShotStatus.DONE.value))
    shot.final_video = "vv_b2"
    (svc.project_dir / "output" / "videos" / "s01_c02_v2.mp4").write_bytes(b"new-src")
    store.save(project)
    out = svc.freshness(blk.sequence_id)
    assert out["status"] == "stale"
    assert "clips[1].resolved_video_version_id" in out["detail"]["mismatched_keys"]
    assert blk.revision == svc.get_entry(blk.sequence_id).block.revision  # canonical 不变
    # 指定 version_id 也能查（非 final 的历史 render）
    out2 = svc.freshness(blk.sequence_id, version_id=vid)
    assert out2["status"] == "stale"
