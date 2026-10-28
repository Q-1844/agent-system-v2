"""C1: selected local-only video can be reused only while its file exists.

This exercises a common real-world gap after copying project trees or losing
local media.  It never contacts Agnes or changes a production project.
"""
from pathlib import Path

from src.ai.incremental_planner import build_incremental_workplan, validate_asset_set
from types import SimpleNamespace
from src.core.models import Project, Scene, Shot, StyleAnchor, VideoVersion
from src.core.store import ProjectStore


def _project(tmp_path: Path, local: str | None, url: str = ""):
    name = "c1_missing_media"
    directory = tmp_path / name
    directory.mkdir(parents=True)
    version = VideoVersion(version_id="v_done", video_id="task_done", status="done",
                           local=local, url=url)
    project = Project(name=name, style=StyleAnchor(), story_text="local-only media",
                      scenes=[Scene(scene_id="s01", summary="test", shots=[Shot(
                          shot_id="s01_c01", scene_id="s01", index=1,
                          target_duration_s=5, final_video="v_done",
                          video_versions=[version])])])
    ProjectStore(directory).save(project)
    return name, directory


def _workplan(name, directory):
    snapshot = SimpleNamespace(project_name=name, scenes=[SimpleNamespace(scene_id="s01", state="NEED_VIDEO")])
    return build_incremental_workplan(snapshot, "请补齐所有缺失镜头", "complete_project", directory)


def test_missing_local_only_file_is_a_c1_gap(tmp_path):
    name, directory = _project(tmp_path, "output/videos/now_gone.mp4")
    plan = _workplan(name, directory)
    assert [x["target"] for x in plan["generate_missing"]] == ["s01_c01"]
    assert plan["reuse_assets"] == []
    assert plan["scene_targets"] == ["s01"]
    assert validate_asset_set(directory)["complete"] is False


def test_present_local_only_file_can_still_be_reused(tmp_path):
    name, directory = _project(tmp_path, "output/videos/real.mp4")
    media = directory / "output" / "videos" / "real.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"nonempty-test-fixture")
    plan = _workplan(name, directory)
    assert plan["generate_missing"] == []
    assert [x["target"] for x in plan["reuse_assets"]] == ["s01_c01"]
    assert plan["reuse_assets"][0]["evidence"]["verified_local"] is True


def test_remote_fallback_is_not_mistaken_for_missing(tmp_path):
    name, directory = _project(tmp_path, "output/videos/now_gone.mp4", "https://provider.example/v.mp4")
    plan = _workplan(name, directory)
    assert plan["generate_missing"] == []
    assert [x["target"] for x in plan["reuse_assets"]] == ["s01_c01"]
    assert plan["reuse_assets"][0]["evidence"]["verified_local"] is False
    assert validate_asset_set(directory, require_local_verified=True)["complete"] is False


def test_zero_byte_local_only_file_is_not_reusable(tmp_path):
    name, directory = _project(tmp_path, "output/videos/empty.mp4")
    media = directory / "output" / "videos" / "empty.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"")
    plan = _workplan(name, directory)
    assert [x["target"] for x in plan["generate_missing"]] == ["s01_c01"]
    assert plan["reuse_assets"] == []


def test_local_file_outside_project_cannot_be_reused(tmp_path):
    stray = tmp_path / "outside.mp4"
    stray.write_bytes(b"nonempty-outside")
    name, directory = _project(tmp_path, str(stray))
    plan = _workplan(name, directory)
    assert [x["target"] for x in plan["generate_missing"]] == ["s01_c01"]
    assert plan["reuse_assets"] == []
