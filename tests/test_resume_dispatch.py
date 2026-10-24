# tests/test_resume_dispatch.py
# 有界自动续跑（A 自派发 + B 巡检兜底）确定性测试：零网络、零 LLM。
# 覆盖：跳数统计、可续跑相位矩阵（FAILED+paused 特例）、跳数上限、
# 派发请求形状（URL/方法/inputs、token 不泄漏）、HTTP 错误降级、
# A 机制三分支（无 token / 超限置 AWAITING_HUMAN / 派发落账）、
# B 巡检死链判定（存活跳过 / 宽限跳过 / 近期已派发跳过 / 派发 / 查询失败跳过）。
import asyncio
import json
import urllib.error

import pytest

from tools import resume_dispatch
from tools.resume_dispatch import (
    MAX_AUTO_HOPS, auto_resume_allowed, dispatch_next_hop, hop_count)
import tools.resume_patrol as resume_patrol


class _Resp:
    def __init__(self, status=204, body=b""):
        self.status, self._body = status, body

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _doc(phase=None, terminal=None, hops=None, **extra):
    history = [{"resume_run_id": f"run{i}"} for i in range(hops or 0)]
    doc = {"phase": phase, "terminal_status": terminal, "resume_history": history}
    doc.update(extra)
    return doc


# ---------- 跳数与相位闸门 ----------

def test_hop_count_missing_history_is_zero():
    assert hop_count({}) == 0
    assert hop_count(None) == 0


def test_allowed_matrix():
    assert auto_resume_allowed(_doc(phase="RUNNING")) == (True, "ok")
    assert auto_resume_allowed(_doc(phase="INTERRUPTED"))[0] is True
    assert auto_resume_allowed(_doc(phase="PAUSING"))[0] is True
    assert auto_resume_allowed(_doc(phase="FAILED", terminal="paused"))[0] is True
    # 真实失败/终态/等待人工：不自动续跑
    for bad in (_doc(phase="FAILED", terminal="error"),
                _doc(phase="FAILED"),
                _doc(phase="COMPLETE"),
                _doc(phase="AWAITING_HUMAN"),
                _doc(phase=None)):
        assert auto_resume_allowed(bad)[0] is False


def test_hop_cap_blocks_and_flags():
    allowed, reason = auto_resume_allowed(_doc(phase="RUNNING", hops=MAX_AUTO_HOPS))
    assert allowed is False
    assert "cap" in reason


# ---------- 派发请求形状与降级 ----------

def test_dispatch_success_posts_workflow_dispatch(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["auth"] = req.headers.get("Authorization")
        seen["body"] = json.loads(req.data.decode())
        return _Resp(204)

    monkeypatch.setattr(resume_dispatch.urllib.request, "urlopen", fake_urlopen)
    result = dispatch_next_hop("tok", ref="chore/migration-readiness",
                               resume_run_id="40055563112",
                               task_nonce="film3m-x", driver_minutes=330)
    assert result["dispatched"] is True
    assert seen["url"].endswith("/actions/workflows/video-autoproduction-e2e.yml/dispatches")
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer tok"
    assert seen["body"]["ref"] == "chore/migration-readiness"
    assert seen["body"]["inputs"]["resume_run_id"] == "40055563112"
    assert seen["body"]["inputs"]["task_nonce"] == "film3m-x"
    assert seen["body"]["inputs"]["driver_timeout_minutes"] == "330"
    # token 绝不出现在返回值里
    assert "tok" not in json.dumps(result)


def test_dispatch_http_error_degrades(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "forbidden", {}, None)

    monkeypatch.setattr(resume_dispatch.urllib.request, "urlopen", fake_urlopen)
    result = dispatch_next_hop("tok", ref="b", resume_run_id="1", task_nonce="n")
    assert result["dispatched"] is False
    assert result["status"] == 403


# ---------- A 机制（auto_resume_next_hop） ----------

class _Checkpoint:
    def __init__(self, doc):
        self.doc = doc


class _Wrapper:
    def __init__(self, doc):
        self.checkpoint = _Checkpoint(doc)
        self.persisted = []

    async def persist(self, **fields):
        self.persisted.append(fields)


def _run(coro):
    return asyncio.run(coro)


def test_a_no_token_degrades_quietly(monkeypatch):
    monkeypatch.delenv("RESUME_DISPATCH_TOKEN", raising=False)
    from tools.full_project_cloud_acceptance import auto_resume_next_hop
    wrapper = _Wrapper(_doc(phase="RUNNING"))
    result = _run(auto_resume_next_hop(wrapper, "n", 330))
    assert result["dispatched"] is False
    assert "not configured" in result["reason"]
    assert wrapper.persisted == []


def test_a_hop_cap_marks_awaiting_human(monkeypatch):
    monkeypatch.setenv("RESUME_DISPATCH_TOKEN", "tok")
    from tools.full_project_cloud_acceptance import auto_resume_next_hop
    wrapper = _Wrapper(_doc(phase="FAILED", terminal="paused", hops=MAX_AUTO_HOPS))
    result = _run(auto_resume_next_hop(wrapper, "n", 330))
    assert result["dispatched"] is False
    assert "cap" in result["reason"]
    assert wrapper.persisted == [{"phase": "AWAITING_HUMAN", "error": result["reason"]}]


def test_a_dispatch_records_into_checkpoint(monkeypatch):
    monkeypatch.setenv("RESUME_DISPATCH_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REF_NAME", "chore/migration-readiness")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setattr(resume_dispatch, "dispatch_next_hop",
                        lambda *a, **k: {"dispatched": True, "status": 204})
    from tools.full_project_cloud_acceptance import auto_resume_next_hop
    wrapper = _Wrapper(_doc(phase="FAILED", terminal="paused"))
    result = _run(auto_resume_next_hop(wrapper, "film3m-x", 330))
    assert result["dispatched"] is True
    assert result["hops"] == 0
    assert wrapper.persisted == [{"auto_resume": result}]


# ---------- B 机制（sweep_nonce 死链判定） ----------

def test_b_run_alive_skips(monkeypatch):
    monkeypatch.setattr(resume_patrol, "fetch_run",
                        lambda token, run_id: {"status": "in_progress"})
    dispatched = []
    monkeypatch.setattr(resume_patrol, "dispatch_next_hop",
                        lambda *a, **k: dispatched.append(1) or {})
    out = resume_patrol.sweep_nonce(
        "n", "b1", "tok", "ref",
        _read={"doc": _doc(phase="RUNNING", github_run_id="40055563112")})
    assert out["action"] == "skip" and out["reason"] == "run alive"
    assert dispatched == []


def test_b_dead_beyond_grace_dispatches(monkeypatch):
    monkeypatch.setattr(resume_patrol, "fetch_run", lambda token, run_id: {
        "status": "completed",
        "completed_at": "2000-01-01T00:00:00Z"})  # 远古完成时间，必超宽限
    monkeypatch.setattr(resume_patrol, "dispatch_next_hop",
                        lambda token, ref, resume_run_id, task_nonce: {
                            "dispatched": True, "status": 204})
    out = resume_patrol.sweep_nonce(
        "n", "b1", "tok", "ref",
        _read={"doc": _doc(phase="RUNNING", github_run_id="40055563112",
                           request_sha256="r" * 64)})
    assert out["action"] == "dispatch"
    assert out["result"]["dispatched"] is True


def test_b_recent_dispatch_skips(monkeypatch):
    now = resume_patrol.utc_now_ts()
    doc = _doc(phase="RUNNING", github_run_id="1", request_sha256="r" * 64,
               auto_resume={"dispatched": True, "at": now - 60})
    out = resume_patrol.sweep_nonce("n", "b1", "tok", "ref", _read={"doc": doc})
    assert out["action"] == "skip"
    assert "recently" in out["reason"]


def test_b_unknown_run_state_skips(monkeypatch):
    monkeypatch.setattr(resume_patrol, "fetch_run", lambda token, run_id: None)
    out = resume_patrol.sweep_nonce(
        "n", "b1", "tok", "ref",
        _read={"doc": _doc(phase="RUNNING", github_run_id="1")})
    assert out["action"] == "skip"
    assert "unknown" in out["reason"]


def test_b_hop_cap_marks_awaiting_human(monkeypatch):
    wrote = {}

    def fake_mark(nonce, doc, token, reason):
        wrote["nonce"] = nonce
        return True

    monkeypatch.setattr(resume_patrol, "mark_awaiting_human", fake_mark)
    out = resume_patrol.sweep_nonce(
        "n", "b1", "tok", "ref",
        _read={"doc": _doc(phase="RUNNING", hops=MAX_AUTO_HOPS)})
    assert out["action"] == "awaiting_human"
    assert wrote["nonce"] == "n"
