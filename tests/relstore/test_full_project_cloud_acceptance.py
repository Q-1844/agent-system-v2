"""Offline acceptance guards; these tests do not call a paid Provider."""
import asyncio
import copy
import json
import subprocess
from types import SimpleNamespace

import pytest

from src.core.models import Project, Scene, Shot
from src.core.store import ProjectStore
from tools.full_project_cloud_acceptance import (BoundedRealClient, validate_planned_project,
                                                validate_final_media, acceptance_credentials,
                                                make_frame_host_calibration)


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
    def __init__(self, journal, *, fail=False, quota_fail=False):
        self.journal, self.fail, self.quota_fail, self.calls = journal, fail, quota_fail, 0

    async def create_video(self, prompt, **kw):
        assert self.journal.writes[-1]["video_attempts"][-1]["state"] == "SUBMITTING"
        self.calls += 1
        if self.quota_fail:
            # 本地账本预检的既有文案形态（src/core/quota.py：AgnesError 无 status）
            raise RuntimeError("今日 video_seconds 配额已用 480/500，本次需 10")
        if self.fail:
            raise TimeoutError("submit outcome unknown")
        return {"video_id": f"video-{self.calls}"}


def setup_wrapper(tmp_path, *, duration=10, fail=False, fail_at=0, quota_fail=False):
    ProjectStore(tmp_path).save(project(duration))
    journal = Journal(fail_at=fail_at)
    provider = Provider(journal, fail=fail, quota_fail=quota_fail)
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


def test_daily_quota_rejection_rolls_back_intent_and_sets_gate(tmp_path):
    """按天接续核心纪律：额度类确定性拒绝=无收据无扣费→意图条目回滚+
    记日历闸门+封堵后续 POST；绝不像模糊失败那样留 UNKNOWN。"""
    wrapper, journal, provider = setup_wrapper(tmp_path, quota_fail=True)
    with pytest.raises(RuntimeError, match="配额已用"):
        asyncio.run(wrapper.create_video("p", duration_s=10))
    assert provider.calls == 1
    # 意图条目已回滚：账面无 SUBMITTING/UNKNOWN 残留（下跳恢复无对账负担）
    assert wrapper.video_attempts == []
    assert all(w["video_attempts"] == [] for w in journal.writes[1:])
    assert wrapper.submission_blocked is True
    assert wrapper.quota_gate_after
    assert journal.writes[-1]["quota_resume_after"] == wrapper.quota_gate_after
    # 额度暂停期间禁止再次 POST（封堵生效，不再烧供应商调用）
    from src.api.agnes_client import DailyQuotaExhausted
    with pytest.raises(DailyQuotaExhausted, match="每日额度"):
        asyncio.run(wrapper.create_video("p", duration_s=10))
    assert provider.calls == 1


def test_quota_gate_timestamp_is_beijing_midnight_margin(tmp_path):
    """闸门=下一个北京 00:10（= UTC 16:10）；格式可被 parse_gate 解析。"""
    from tools import quota_gate
    wrapper, journal, provider = setup_wrapper(tmp_path, quota_fail=True)
    with pytest.raises(RuntimeError, match="配额已用"):
        asyncio.run(wrapper.create_video("p", duration_s=10))
    parsed = quota_gate.parse_gate(wrapper.quota_gate_after)
    assert parsed is not None
    assert parsed.minute == quota_gate.RESET_MARGIN_MINUTES
    # 北京 00:10 → UTC 小时 = (0 - RESET_OFFSET_HOURS) % 24
    assert parsed.hour == (-quota_gate.RESET_OFFSET_HOURS) % 24
    assert parsed.second == 0


def test_uncertain_submit_still_keeps_unknown_discipline(tmp_path):
    """对照：非额度失败（超时/结局不明）仍走 UNKNOWN 对账，闸门不介入。"""
    wrapper, journal, provider = setup_wrapper(tmp_path, fail=True)
    with pytest.raises(TimeoutError):
        asyncio.run(wrapper.create_video("p", duration_s=10))
    assert wrapper.quota_gate_after is None
    assert journal.writes[-1]["video_attempts"][0]["state"] == "UNKNOWN"
    assert "quota_resume_after" not in journal.writes[-1]


def test_lost_known_id_persistence_receipt_blocks_a_second_post(tmp_path):
    wrapper, journal, provider = setup_wrapper(tmp_path, fail_at=2)
    with pytest.raises(RuntimeError, match="CAS"):
        asyncio.run(wrapper.create_video("p", duration_s=10))
    with pytest.raises(RuntimeError, match="unconfirmed"):
        asyncio.run(wrapper.create_video("p", duration_s=10))
    assert provider.calls == 1
    assert journal.writes[-1]["video_attempts"][0]["video_id"] == "video-1"


def test_high_execution_budget_keeps_generating_beyond_historical_240_seconds(tmp_path):
    wrapper, journal, provider = setup_wrapper(tmp_path)
    async def run():
        for i in range(26):
            await wrapper.create_video(f"p-{i}", duration_s=10)
    asyncio.run(run())
    assert wrapper.video_budget == 1_000_000_000
    assert provider.calls == 26 and len(journal.writes) == 52


def test_explicit_execution_video_budget_is_still_a_mechanical_guard(tmp_path):
    wrapper, journal, provider = setup_wrapper(tmp_path)
    wrapper.video_budget = 240
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


def test_decode_errors_cannot_pass_with_a_plausible_duration(monkeypatch):
    def run(command, **kwargs):
        if command[0] == "ffprobe":
            return SimpleNamespace(stdout=json.dumps({"format": {"duration": "200"},
                "streams": [{"codec_type": "video", "duration": "200"}]}))
        # ffmpeg can log corrupt packets and return zero unless errors are fatal.
        if "-xerror" in command:
            raise subprocess.CalledProcessError(1, command, stderr=b"corrupt frame")
        return SimpleNamespace(returncode=0, stderr=b"corrupt frame")
    monkeypatch.setattr("tools.full_project_cloud_acceptance.subprocess.run", run)
    with pytest.raises(subprocess.CalledProcessError):
        validate_final_media("damaged.mp4")


def test_archive_credentials_cannot_silently_authorize_frame_host_writes():
    environ = {"AGNES_API_KEY": "provider", "B1_ARCHIVE_TOKEN": "archive"}
    with pytest.raises(RuntimeError, match="missing FRAME_HOST_TOKEN; no media"):
        acceptance_credentials(environ)
    environ["FRAME_HOST_TOKEN"] = "frames"
    assert acceptance_credentials(environ) == ("archive", "frames")


def test_frame_host_calibration_cannot_reuse_a_previous_content_address(tmp_path):
    from PIL import Image
    from src.core.image_host import GithubPagesHost
    host = GithubPagesHost("owner", "frames", "main", "read-only")
    old = tmp_path / "old.png"
    Image.new("RGB", (8, 8), (48, 80, 110)).save(old)
    files = [old, make_frame_host_calibration(tmp_path / "one.png"),
             make_frame_host_calibration(tmp_path / "two.png")]
    paths = {host._object_path(p.read_bytes(), p.name) for p in files}
    assert len(paths) == 3
    for path in files:
        with Image.open(path) as img:
            assert img.size == (8, 8) and img.getpixel((0, 0)) == (48, 80, 110)


def test_cached_public_calibration_cannot_hide_a_read_only_credential(tmp_path, monkeypatch):
    from PIL import Image
    from src.core.image_host import GithubPagesHost
    host = GithubPagesHost("owner", "frames", "main", "read-only")
    old = tmp_path / "old.png"
    Image.new("RGB", (8, 8), (48, 80, 110)).save(old)
    cached = host._object_path(old.read_bytes(), old.name)
    writes = []
    async def remote_matches(path, data):
        return path == cached
    async def denied_put(path, data):
        writes.append(path)
        raise RuntimeError("github_pages PUT HTTP 403")
    monkeypatch.setattr(host, "_remote_matches", remote_matches)
    monkeypatch.setattr(host, "_put_with_retry", denied_put)
    fresh = make_frame_host_calibration(tmp_path / "check.png")
    with pytest.raises(RuntimeError, match="403"):
        asyncio.run(host.upload(fresh))
    assert len(writes) == 1 and writes[0] != cached


# ---------- 片长档位（200 旧档零变化 / 320 续集档） ----------

def test_film_profile_200_digest_matches_legacy_formula():
    """200 档 digest 必须与旧公式逐字节一致——旧 nonce 的 resume 链生命线。"""
    import hashlib, json as _json
    from tools.full_project_cloud_acceptance import (FILM_PROFILES, SCENES, VIDEO_BUDGET_SECONDS,
                                                    request_digest)
    legacy = hashlib.sha256(_json.dumps(
        {"scope": "full-project-180s-v1", "scenes": SCENES,
         "video_budget_s": 240}, ensure_ascii=False).encode()).hexdigest()
    assert request_digest(200, FILM_PROFILES[200]) == legacy
    assert VIDEO_BUDGET_SECONDS == 1_000_000_000  # separate execution allowance


def test_film_profile_default_is_200_and_rejects_unknown():
    from tools.full_project_cloud_acceptance import film_profile
    assert film_profile({})[0] == 200
    with pytest.raises(ValueError, match="unsupported film target"):
        film_profile({"FILM_TARGET_SECONDS": "250"})
    with pytest.raises(ValueError, match="whole seconds"):
        film_profile({"FILM_TARGET_SECONDS": "abc"})


def test_320s_sequel_profile_validates_eight_scenes():
    """320 档：八段 32 镜续集剧本过规划校验，预算=384、成片门槛=300s。"""
    from tools.full_project_cloud_acceptance import FILM_PROFILES, SEQUEL_SCENES
    profile = FILM_PROFILES[320]
    assert len(SEQUEL_SCENES) == profile["scene_count"] == 8
    proj = project()
    proj.scenes = [Scene(scene_id=f"s{i}", shots=[
        Shot(shot_id=f"s{i}_c{j}", scene_id=f"s{i}", index=j, target_duration_s=10)
        for j in range(1, 5)]) for i in range(1, 9)]
    plan = validate_planned_project(proj, profile)
    assert plan["planned_seconds"] == 320 and plan["shot_count"] == 32
    assert profile["budget"] == 384 and profile["min_final"] == 300.0


def test_320s_plan_rejected_under_200_profile_and_vice_versa():
    """档位强绑定：320s 规划过不了 200 档校验（防误配，digest 之外的第二道闸）。"""
    from tools.full_project_cloud_acceptance import FILM_PROFILES
    proj = project()
    proj.scenes = [Scene(scene_id=f"s{i}", shots=[
        Shot(shot_id=f"s{i}_c{j}", scene_id=f"s{i}", index=j, target_duration_s=10)
        for j in range(1, 5)]) for i in range(1, 9)]
    with pytest.raises(ValueError, match="acceptance requires 5 scenes"):
        validate_planned_project(proj, FILM_PROFILES[200])


def test_stage0_rebind_pairs_run_id_with_source_sha(monkeypatch):
    """r46 第三跳实测回填：跨 hop 重绑必须成对落账（github_run_id + source_sha）。
    只重绑 run id 的「半重绑」会让该跳死在身份校验时（重绑先于校验）留下
    检查点：github_run_id=本跳、source_sha=上上跳——下一跳的
    restore_snapshot 身份校验 source_sha 恒不匹配，链式恢复死锁。"""
    from tools.full_project_cloud_acceptance import _stage0_rebind_for_hop
    monkeypatch.setenv("GITHUB_RUN_ID", "41100000001")
    monkeypatch.setenv("GITHUB_SHA", "deadbeef90")
    saved = {}
    class _CP:
        def save(self, **fields):
            saved.update(fields)
    _stage0_rebind_for_hop(_CP())
    assert saved["github_run_id"] == "41100000001"
    assert saved["source_sha"] == "deadbeef90"
    assert saved["phase"] == "STAGE0_BOUND"
