# tests/test_quota_gate.py
# 每日视频额度闸门（按天接续）确定性测试：零网络、零 LLM。
# 覆盖：错误分类矩阵（本地账本文案/供应商 4xx 额度语义/结局不明/契约错/
# 5xx-408/无语义 4xx）、闸门时刻边界（北京 00:10 = UTC 16:10）、
# gate_status 判定矩阵、auto_resume_allowed 日历闸门联动（相位判定先行、
# 闸门次之、跳数最后的不变式）、B 巡检对额度暂停的 skip 与闸开后拉起。
import datetime as dt

import pytest

from tools import quota_gate
from tools.resume_dispatch import MAX_AUTO_HOPS, auto_resume_allowed
import tools.resume_patrol as resume_patrol


class _Err(Exception):
    """形态对齐 src/api/agnes_client.AgnesError：message + 可选 status。"""

    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


class VideoSubmissionUncertain(Exception):
    """形态对齐 src/api/agnes_client.VideoSubmissionUncertain（按类名识别）。"""


UTC = dt.timezone.utc


def _utc(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=UTC)


def _doc(phase="FAILED", terminal="quota_exhausted", gate="2020-01-01T00:00:00Z", hops=0):
    history = [{"run_id": str(i)} for i in range(hops)]
    doc = {"phase": phase, "resume_history": history}
    if terminal is not None:
        doc["terminal_status"] = terminal
    if gate is not None:
        doc[quota_gate.GATE_FIELD] = gate
    return doc


# ---------- 错误分类矩阵 ----------

def test_local_ledger_message_is_classified():
    e = _Err("今日 video_seconds 配额已用 480/500，本次需 10")
    assert quota_gate.classify_quota_rejection(e) is True


@pytest.mark.parametrize("message", [
    "insufficient quota",
    "今日额度不足",
    # 2026-10-30 r46 实测真实文案（run 40994707518，供应商 429 原文，
    # 含 request id 形态；修复前该文案不被识别 → 误走普通 FAILED 路径）
    '429: {"error":{"code":"","message":"已达到 API 用量上限，请在 '
    '2026-10-31 00:00 之后重试。 (request id: 20261030054158288898746xBCF0xVu)",'
    '"type":"Agn',
])
@pytest.mark.parametrize("status", [402, 429])
def test_provider_4xx_quota_semantics_is_classified(message, status):
    assert quota_gate.classify_quota_rejection(_Err(message, status=status)) is True


@pytest.mark.parametrize("message", ["Too Many Requests", "HTTP 402: balance exhausted",
                                     "余额不足，请充值", "rate limit exceeded", "max tokens exceeded"])
def test_rate_limits_and_balance_are_not_daily_quota(message):
    assert not quota_gate.classify_quota_rejection(_Err(message, status=429))


def test_uncertain_outcome_is_never_quota():
    e = VideoSubmissionUncertain("video submission outcome unknown; reconcile before any new POST")
    assert quota_gate.classify_quota_rejection(e) is False


@pytest.mark.parametrize("message", [
    "2.5 契约不支持 t2v 模式",
    "需要 1-5 张公网参考图（flash 上限 5）",
    "keyframe 模式必须提供公网 image URL",
    "provider receipt has no confirmed video ID",
])
def test_contract_errors_are_not_quota(message):
    assert quota_gate.classify_quota_rejection(_Err(message)) is False
    # 同文案即使带 4xx status 也无额度语义 → 不当额度处理
    assert quota_gate.classify_quota_rejection(_Err(message, status=400)) is False


@pytest.mark.parametrize("status", [408, 500, 503])
def test_timeout_and_5xx_are_not_quota_even_with_words(status):
    assert quota_gate.classify_quota_rejection(_Err("quota exceeded", status=status)) is False


def test_plain_exception_is_not_quota():
    assert quota_gate.classify_quota_rejection(_Err("any transport hiccup")) is False


# ---------- 闸门时刻边界（北京 00:10 = UTC 16:10） ----------

@pytest.mark.parametrize("now,expected", [
    # 北京 23:52（当日闸门已过）→ 次日北京 00:10 = UTC 当日 16:10
    (_utc(2026, 10, 24, 15, 52), "2026-10-24T16:10:00Z"),
    # 北京 00:05（今日闸门未到）→ 今日北京 00:10 = UTC 16:10
    (_utc(2026, 10, 24, 16, 5), "2026-10-24T16:10:00Z"),
    # 北京 00:15（今日闸门刚过）→ 次日 = UTC 次日 16:10
    (_utc(2026, 10, 24, 16, 15), "2026-10-25T16:10:00Z"),
    # 北京 11:00（白天耗尽）→ 次日凌晨 = UTC 当日 16:10
    (_utc(2026, 10, 24, 3, 0), "2026-10-24T16:10:00Z"),
    # 恰在闸门时刻（北京 00:10 整）→ 视为已过，等次日
    (_utc(2026, 10, 24, 16, 10), "2026-10-25T16:10:00Z"),
])
def test_next_quota_reset_utc_boundary(now, expected):
    assert quota_gate.next_quota_reset_utc(now) == expected


# ---------- gate_status 判定矩阵 ----------

def test_non_quota_doc_gate_always_open():
    assert quota_gate.gate_status({})[0] is True
    assert quota_gate.gate_status(None)[0] is True


def test_gate_closed_until_future():
    future = "2999-01-01T00:00:00Z"
    open_, reason = quota_gate.gate_status(_doc(gate=future))
    assert open_ is False
    assert "closed until" in reason and future in reason


def test_gate_open_after_past():
    assert quota_gate.gate_status(_doc(gate="2020-01-01T00:00:00Z"))[0] is True


def test_quota_terminal_without_gate_goes_to_human():
    open_, reason = quota_gate.gate_status(_doc(gate=None))
    assert open_ is False
    assert "reconcile manually" in reason


def test_malformed_gate_goes_to_human():
    open_, reason = quota_gate.gate_status(_doc(gate="明天见"))
    assert open_ is False
    assert "reconcile manually" in reason


def test_deadline_pause_with_gate_field_is_also_gated():
    """额度先耗尽、又撞保险丝的复合暂停：只要有闸门字段就按天等。"""
    open_, reason = quota_gate.gate_status(
        _doc(terminal="paused", gate="2999-01-01T00:00:00Z"))
    assert open_ is False
    assert "closed until" in reason


# ---------- auto_resume_allowed 日历闸门联动 ----------

def test_quota_pause_blocked_while_gate_closed():
    allowed, reason = auto_resume_allowed(_doc(gate="2999-01-01T00:00:00Z"))
    assert allowed is False
    assert "closed until" in reason


def test_quota_pause_allowed_after_gate_opens():
    assert auto_resume_allowed(_doc(gate="2020-01-01T00:00:00Z"))[0] is True


def test_gate_precedes_hop_cap_in_priority():
    """闸门关闭+跳数触顶：先报闸门（当日不派发）；次日闸开后才报上限。"""
    allowed, reason = auto_resume_allowed(
        _doc(gate="2999-01-01T00:00:00Z", hops=MAX_AUTO_HOPS))
    assert allowed is False and "closed until" in reason


def test_quota_terminal_without_gate_never_auto_resumes():
    allowed, reason = auto_resume_allowed(_doc(gate=None))
    assert allowed is False
    assert "reconcile manually" in reason


def test_real_failure_terminal_is_still_not_resumable():
    """真实失败（terminal=failed，无闸门）不因本改动改变判定。"""
    allowed, reason = auto_resume_allowed(_doc(terminal="failed", gate=None))
    assert allowed is False
    assert "not auto-resumable" in reason


def test_deadline_pause_without_gate_is_still_resumable():
    assert auto_resume_allowed(_doc(terminal="paused", gate=None))[0] is True


# ---------- B 巡检对额度暂停的处置 ----------

def test_patrol_skips_quota_gated_nonce(monkeypatch):
    queried = []
    monkeypatch.setattr(resume_patrol, "fetch_run",
                        lambda token, run_id: queried.append(run_id) or {"status": "completed"})
    dispatched = []
    monkeypatch.setattr(resume_patrol, "dispatch_next_hop",
                        lambda *a, **k: dispatched.append(1) or {"dispatched": False})
    out = resume_patrol.sweep_nonce(
        "n", "b1", "tok", "ref",
        _read=_doc(gate="2999-01-01T00:00:00Z", hops=0) | {"github_run_id": "400"})
    assert out["action"] == "skip" and "closed until" in out["reason"]
    # 闸门未开时不做任何运行状态查询（零多余 API 调用）
    assert queried == [] and dispatched == []


def test_patrol_dispatches_after_gate_opens(monkeypatch):
    monkeypatch.setattr(resume_patrol, "fetch_run", lambda token, run_id: {
        "status": "completed",
        "completed_at": "2026-10-24T00:00:00Z",
        "updated_at": "2026-10-24T00:00:00Z"})
    captured = {}
    def fake_dispatch(token, ref, resume_run_id, task_nonce, **kw):
        captured["resume_run_id"] = resume_run_id
        captured["task_nonce"] = task_nonce
        return {"dispatched": True, "status": 204}
    monkeypatch.setattr(resume_patrol, "dispatch_next_hop", fake_dispatch)
    out = resume_patrol.sweep_nonce(
        "n", "b1", "tok", "ref",
        _read=_doc(gate="2020-01-01T00:00:00Z") | {"github_run_id": "400"})
    assert out["action"] == "dispatch"
    assert captured["resume_run_id"] == "400" and captured["task_nonce"] == "n"
