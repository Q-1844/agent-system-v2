"""Offline acceptance guards; these tests do not call a paid Provider."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from src.core.models import Project, Scene, Shot
from src.core.store import ProjectStore
from tools.full_project_cloud_acceptance import (BoundedRealClient, validate_planned_project,
                                                validate_final_media, acceptance_credentials)


def project(duration=10):
    return Project(name="acceptance-test", scenes=[
        Scene(scene_id=f"s{i}", shots=[Shot(shot_id=f"s{i}_c{j}", scene_id=f"s{i}",
                                          index=j, target_duration_s=duration) for j in range(1, 5)])
        for i in range(1, 6)])


def test_long_project_is_measured_from_distinct_shots():
    assert validate_planned_project(project())["planned_seconds"] == 200


@pytest.mark.parametrize("duration", [3, 5, 9, 12, float("nan")])
def test_short_or_out_of_contract_plan_is_rejected(duration):
    with pytest.raises(ValueError):
        validate_planned_project(project(duration))


class Journal:
    def __init__(self, *, fail_at=0):
        self.writes = []
        self.fail_at = fail_at

    def save(self, **kw):
        self.writes.append(copy.deepcopy(kw))
        if len(self.writes) == self.fail_at:
            raise RuntimeError("lost CAS receipt")


class Provider:
    def __init__(self, journal, *, fail=False):
        self.journal, self.fail, self.calls = journal, fail, 0

    async def create_video(self, prompt, **kw):
        assert self.journal.writes[-1]["video_attempts"][-1]["state"] == "SUBMITTING"
        self.calls += 1
        if self.fail:
            raise TimeoutError("submit outcome unknown")
        return {"video_id": f"video-{self.calls}"}


def setup_wrapper(tmp_path, *, duration=10, fail=False, fail_at=0):
    ProjectStore(tmp_path).save(project(duration))
    journal = Journal(fail_at=fail_at)
    provider = Provider(journal, fail=fail)
    return BoundedRealClient(provider, journal, tmp_path, tmp_path / "evidence"), journal, provider


def test_write_ahead_failure_makes_zero_paid_calls(tmp_path):
    wrapper, journal, provider = setup_wrapper(tmp_path, fail_at=1)
    with pytest.raises(RuntimeError, match="CAS"):
        asyncio.run(wrapper.create_video("p", duration_s=10))
    assert provider.calls == 0
    with pytest.raises(RuntimeError, match="unconfirmed"):
        asyncio.run(wrapper.create_video("p", duration_s=10))


def test_ambiguous_submit_cannot_be_automatically_repeated(tmp_path):
    wrapper, journal, provider = setup_wrapper(tmp_path, fail=True)
    with pytest.raises(TimeoutError):
        asyncio.run(wrapper.create_video("p", duration_s=10))
    with pytest.raises(RuntimeError, match="unconfirmed"):
        asyncio.run(wrapper.create_video("p", duration_s=10))
    assert provider.calls == 1
    assert journal.writes[-1]["video_attempts"][0]["state"] == "UNKNOWN"


def test_lost_known_id_persistence_receipt_blocks_a_second_post(tmp_path):
    wrapper, journal, provider = setup_wrapper(tmp_path, fail_at=2)
    with pytest.raises(RuntimeError, match="CAS"):
        asyncio.run(wrapper.create_video("p", duration_s=10))
    with pytest.raises(RuntimeError, match="unconfirmed"):
        asyncio.run(wrapper.create_video("p", duration_s=10))
    assert provider.calls == 1
    assert journal.writes[-1]["video_attempts"][0]["video_id"] == "video-1"


def test_240_second_budget_counts_every_attempt(tmp_path):
    wrapper, journal, provider = setup_wrapper(tmp_path)
    async def run():
        for i in range(24):
            await wrapper.create_video(f"p-{i}", duration_s=10)
        with pytest.raises(RuntimeError, match="budget"):
            await wrapper.create_video("extra", duration_s=10)
    asyncio.run(run())
    assert provider.calls == 24


def test_short_plan_cannot_make_a_paid_call(tmp_path):
    wrapper, journal, provider = setup_wrapper(tmp_path, duration=5)
    with pytest.raises(ValueError, match="192-220"):
        asyncio.run(wrapper.create_video("p", duration_s=10))
    assert provider.calls == 0 and journal.writes == []


@pytest.mark.parametrize("container,video,attached", [
    (200, 5, False), (200, None, False), (200, "nan", False),
    (200, "inf", False), ("nan", 200, False), ("inf", 200, False),
    (200, 200, True),
])
def test_audio_or_cover_art_cannot_satisfy_film_duration(monkeypatch, container, video, attached):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=json.dumps({"format": {"duration": str(container)},
            "streams": [{"codec_type": "video", "duration": str(video) if video is not None else "nan",
                         "disposition": {"attached_pic": int(attached)}}, {"codec_type": "audio"}]}))
    monkeypatch.setattr("tools.full_project_cloud_acceptance.subprocess.run", run)
    with pytest.raises(ValueError):
        validate_final_media("final.mp4")
    assert len(calls) == 1  # Never proceed to decode/archival after invalid timing.


def test_real_video_duration_and_full_decode_are_both_required(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=json.dumps({"format": {"duration": "205"},
            "streams": [{"codec_type": "video", "duration": "200", "codec_name": "h264"}]}))
    monkeypatch.setattr("tools.full_project_cloud_acceptance.subprocess.run", run)
    result = validate_final_media("final.mp4")
    assert result["duration_s"] == 200 and result["container_duration_s"] == 205
    assert result["full_decode_ok"] and calls[1][0] == "ffmpeg"


def test_archive_credentials_cannot_silently_authorize_frame_host_writes():
    environ = {"AGNES_API_KEY": "provider", "B1_ARCHIVE_TOKEN": "archive"}
    with pytest.raises(RuntimeError, match="missing FRAME_HOST_TOKEN; no media"):
        acceptance_credentials(environ)
    environ["FRAME_HOST_TOKEN"] = "frames"
    assert acceptance_credentials(environ) == ("archive", "frames")
