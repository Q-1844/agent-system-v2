# -*- coding: utf-8 -*-
"""第 06 轮修复③：付费提交的「未决 / 明确拒绝 / 未发出」三态纪律。

背景（run #41 实证）：
  - harness 的闸门按「全项目任一未决」封堵新提交，把一个镜头的对账债放大成
    整片停摆；被误封的镜头还凭空背上「上次付费提交结局不明」的假痕迹。
  - 供应商【明确拒绝】(4xx，≠408：没有远端任务、没有扣费) 被记成 UNKNOWN
    对账债，与 routers/shots.py 的 known_rejection 判定自相矛盾。
  - 素材 URL 拉取失败（400 source_unavailable）是明确拒绝，可安全重试。

本文件只做离线断言，不调用真实 provider。
"""
import asyncio
import copy

import pytest

from src.api.agnes_client import AgnesError, VideoSubmissionNotSent, VideoSubmissionUncertain
from src.core.media_submission_context import active_video_submission
from tools.full_project_cloud_acceptance import BoundedRealClient


class Journal:
    def __init__(self, fail_first=False):
        self.writes = []
        self.fail_first = fail_first

    def save(self, **kw):
        if self.fail_first and not self.writes:
            raise RuntimeError("lost CAS receipt")
        self.writes.append(copy.deepcopy(kw))

    @property
    def doc(self):
        return self.writes[-1] if self.writes else {}


class Provider:
    """按调用序返回结果或抛错。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def create_video(self, prompt, **kw):
        self.calls += 1
        item = self.script.pop(0) if self.script else {"video_id": f"video-{self.calls}"}
        if isinstance(item, Exception):
            raise item
        return item


def setup(tmp_path, script, journal=None):
    from src.core.models import Project, Scene, Shot
    from src.core.store import ProjectStore
    # media_gate 需要一份能通过 200 档校验的计划（5 段 × 4 镜 × 10s = 200s）。
    ProjectStore(tmp_path).save(Project(name="acceptance-test", scenes=[
        Scene(scene_id="s%d" % i, shots=[
            Shot(shot_id="s%d_c%d" % (i, j), scene_id="s%d" % i, index=j,
                 target_duration_s=10) for j in range(1, 5)])
        for i in range(1, 6)]))
    journal = journal if journal is not None else Journal()
    provider = Provider(script)
    wrapper = BoundedRealClient(provider, journal, tmp_path, tmp_path / "evidence")
    return wrapper, journal, provider


def submit_as(wrapper, shot_id, prompt="p", duration_s=10):
    """以某个逻辑镜头的身份发起一次提交（模拟 native handler 的提交意图）。"""
    token = active_video_submission.set({
        "project": wrapper.project_dir.name,
        "logical_shot_id": shot_id,
        "attempt_id": "attempt-%s" % shot_id,
        "input_signature": "sig-%s" % shot_id,
        "previous_final_video": None,
    })
    try:
        return asyncio.run(wrapper.create_video(prompt, duration_s=duration_s))
    finally:
        active_video_submission.reset(token)


# ---------------------------------------------------------------- 闸门粒度

def test_pending_submit_still_blocks_the_same_shot(tmp_path):
    """同一镜头的未决提交必须继续阻止自动重发（防重复扣费的底线不回归）。"""
    wrapper, journal, provider = setup(tmp_path, [TimeoutError("submit outcome unknown")])
    with pytest.raises(TimeoutError):
        submit_as(wrapper, "s01_c02")
    assert journal.doc["video_attempts"][0]["state"] == "UNKNOWN"
    assert journal.doc["video_attempts"][0]["logical_shot_id"] == "s01_c02"

    with pytest.raises(VideoSubmissionNotSent, match="unconfirmed"):
        submit_as(wrapper, "s01_c02")
    assert provider.calls == 1


def test_pending_submit_does_not_block_other_shots(tmp_path):
    """run #41 核心回归：一个镜头的未决提交不得停摆其它镜头。

    旧实现按「全项目任一未决」封堵：s01_c02 在 s02_c02 的提交在途时被误封，
    POST 从未发出却被记成「付费提交结局不明」，镜头永久卡死、整片失败。
    """
    wrapper, journal, provider = setup(tmp_path, [
        TimeoutError("submit outcome unknown"),
        {"video_id": "video-other"},
    ])
    with pytest.raises(TimeoutError):
        submit_as(wrapper, "s02_c02")
    # 另一个镜头照常提交（并发生产不因别人的对账债停摆）
    result = submit_as(wrapper, "s01_c02")
    assert result["video_id"] == "video-other"
    assert provider.calls == 2
    states = [a["state"] for a in journal.doc["video_attempts"]]
    assert states == ["UNKNOWN", "ACCEPTED"]


# ------------------------------------------------------- 明确拒绝 vs 结局不明

def test_explicit_provider_rejection_does_not_become_unknown_debt(tmp_path):
    """供应商明确拒绝(4xx)：没有远端任务、没有扣费 ⇒ 不背 UNKNOWN 对账债。"""
    wrapper, journal, provider = setup(tmp_path, [
        AgnesError("video API 错误 400: source_unavailable", status=400,
                   body='{"code":"source_unavailable"}'),
        {"video_id": "video-retry"},
    ])
    with pytest.raises(AgnesError):
        submit_as(wrapper, "s02_c05")
    assert wrapper.video_attempts == []          # 意图条目已回滚
    assert wrapper.submission_blocked is False   # 额度没耗尽 ⇒ 不置全局闸门
    # 明确拒绝 = 无扣费 ⇒ 该镜头可安全重发
    assert submit_as(wrapper, "s02_c05")["video_id"] == "video-retry"
    assert provider.calls == 2


def test_quota_rejection_still_rolls_back_and_sets_calendar_gate(tmp_path):
    """按天接续纪律不回归：额度类拒绝 ⇒ 回滚 + 日历闸门 + 封堵后续 POST。"""
    wrapper, journal, provider = setup(tmp_path, [
        RuntimeError("今日 video_seconds 配额已用 480/500，本次需 10")])
    with pytest.raises(RuntimeError, match="配额已用"):
        submit_as(wrapper, "s01_c01")
    assert wrapper.video_attempts == []
    assert wrapper.submission_blocked is True
    assert wrapper.quota_gate_after
    assert journal.doc["quota_resume_after"] == wrapper.quota_gate_after


def test_ambiguous_submit_keeps_unknown_receipt_without_global_freeze(tmp_path):
    """结局不明：保留 UNKNOWN 收据（绝不凭猜测丢弃），但不封堵其它镜头。"""
    wrapper, journal, provider = setup(tmp_path, [
        TimeoutError("submit outcome unknown"),
        {"video_id": "video-next"},
    ])
    with pytest.raises(TimeoutError):
        submit_as(wrapper, "s01_c02")
    assert journal.doc["video_attempts"][0]["state"] == "UNKNOWN"
    assert wrapper.submission_blocked is False      # 未决只封锁同一镜头
    assert submit_as(wrapper, "s02_c01")["video_id"] == "video-next"


def test_write_ahead_persistence_failure_means_nothing_was_sent(tmp_path):
    """写前日志没落盘 ⇒ POST 根本没发出 ⇒ 必须是「未发出」而不是「结局不明」。"""
    wrapper, journal, provider = setup(tmp_path, [{"video_id": "video-1"}],
                                       journal=Journal(fail_first=True))
    with pytest.raises(VideoSubmissionNotSent):
        submit_as(wrapper, "s01_c02")
    assert provider.calls == 0


def test_provider_explicit_rejection_classification():
    from tools.full_project_cloud_acceptance import provider_explicitly_rejected
    assert provider_explicitly_rejected(
        AgnesError("400", status=400, body='{"code":"source_unavailable"}')) is True
    assert provider_explicitly_rejected(AgnesError("400", status=400)) is True
    assert provider_explicitly_rejected(AgnesError("408", status=408)) is False
    assert provider_explicitly_rejected(AgnesError("500", status=500)) is False
    assert provider_explicitly_rejected(
        VideoSubmissionUncertain("unknown", status=None)) is False
    assert provider_explicitly_rejected(RuntimeError("boom")) is False


# ------------------------------------------------------------ 产品层：未发出

def test_not_sent_submit_resets_the_generating_marker():
    """未发出提交 ⇒ 抹掉 GENERATING 痕迹：下一次派发不得误判为「结局不明」。"""
    from src.core.models import ShotStatus, Shot
    from routers.shots import _reset_not_sent_submit

    shot = Shot(shot_id="s01_c02", scene_id="s01", index=2)
    shot.video.status = ShotStatus.GENERATING.value
    shot.video.video_id = None
    shot.video.task_id = None
    _reset_not_sent_submit(shot, shot.final_video)
    assert shot.video.status == ShotStatus.PENDING.value
    assert shot.video.video_id is None and shot.video.task_id is None


def test_not_sent_reset_keeps_a_confirmed_receipt_untouched():
    """已有 video_id（确实已提交）的镜头不得被「未发出」复位路径误伤。"""
    from src.core.models import ShotStatus, Shot
    from routers.shots import _reset_not_sent_submit

    shot = Shot(shot_id="s01_c02", scene_id="s01", index=2)
    shot.video.status = ShotStatus.GENERATING.value
    shot.video.video_id = "task_already_submitted"
    _reset_not_sent_submit(shot, shot.final_video)
    assert shot.video.status == ShotStatus.GENERATING.value
    assert shot.video.video_id == "task_already_submitted"


# ------------------------------------------------- 素材 URL 拉取失败的安全重试

def test_material_fetch_rejection_is_classified_only_for_source_unavailable():
    from routers.shots import _is_material_fetch_rejection
    assert _is_material_fetch_rejection(
        AgnesError("400", status=400, body='{"code":"source_unavailable"}')) is True
    assert _is_material_fetch_rejection(
        AgnesError("400", status=400, body='{"code":"other"}')) is False
    assert _is_material_fetch_rejection(AgnesError("429", status=429)) is False
    assert _is_material_fetch_rejection(
        VideoSubmissionUncertain("unknown", status=None)) is False


def test_material_fetch_rejection_is_retried_safely():
    """素材 URL 拉取失败（明确拒绝、无扣费）⇒ 退避后重试一次并成功。"""
    from routers.shots import _submit_with_material_retry, MATERIAL_FETCH_RETRY_SECONDS
    provider = Provider([
        AgnesError("400", status=400, body='{"code":"source_unavailable"}'),
        {"video_id": "video-ok"},
    ])

    async def run():
        import routers.shots as shots
        original = shots.MATERIAL_FETCH_RETRY_SECONDS
        shots.MATERIAL_FETCH_RETRY_SECONDS = 0
        try:
            return await _submit_with_material_retry(provider, "p", mode="keyframes")
        finally:
            shots.MATERIAL_FETCH_RETRY_SECONDS = original

    assert asyncio.run(run())["video_id"] == "video-ok"
    assert provider.calls == 2


def test_material_fetch_rejection_gives_up_after_the_retry_budget():
    """连续两次素材拉取失败 ⇒ 如实抛出（不无限重试，也不吞错）。"""
    from routers.shots import _submit_with_material_retry
    err = AgnesError("400", status=400, body='{"code":"source_unavailable"}')
    provider = Provider([err, err])

    async def run():
        import routers.shots as shots
        original = shots.MATERIAL_FETCH_RETRY_SECONDS
        shots.MATERIAL_FETCH_RETRY_SECONDS = 0
        try:
            return await _submit_with_material_retry(provider, "p", mode="keyframes")
        finally:
            shots.MATERIAL_FETCH_RETRY_SECONDS = original

    with pytest.raises(AgnesError):
        asyncio.run(run())
    assert provider.calls == 2


def test_ambiguous_submission_is_never_retried():
    """结局不明绝不自动重发（与素材拒绝的「可安全重试」严格区分）。"""
    from routers.shots import _submit_with_material_retry
    provider = Provider([VideoSubmissionUncertain("unknown"), {"video_id": "video-ok"}])

    async def run():
        return await _submit_with_material_retry(provider, "p", mode="keyframes")

    with pytest.raises(VideoSubmissionUncertain):
        asyncio.run(run())
    assert provider.calls == 1
