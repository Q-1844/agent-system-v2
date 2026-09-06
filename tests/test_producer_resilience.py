"""Producer Runtime 可靠性收口测试（2026-09-06 收口 §1A/§1B，不联网）。

§1A：普通 read tool 错误（不存在/非法引用等可恢复错误）转成 tool failure 回给
     模型，不击穿整个 producer turn（真机：shot.read 不存在 shot → AgentError 抛穿
     → 整轮 failed）。
§1B：LLM provider 瞬态错误（429/500/502/503/520 族）轻量恢复——AgnesClient 流式
     重试扩展到 Agnes（另见 test_agnes.py）+ producer loop turn 级恢复 nudge
     （上限 MAX_LLM_TRANSIENT_RECOVERIES）；auth/参数类立即失败；cancel 不回退。

复用 tests/test_producer_bridge.py 夹具（真实 producer_turn 链路）。
"""
import asyncio

import pytest

from src.api.agnes_client import AgnesError
from src.core.store import ProjectStore
from src.agents import producer, turn_registry

from tests.test_producer_bridge import (
    _save_proj, _runtime, _run, _tool_call, ScriptedClient,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    turn_registry.clear()
    yield
    turn_registry.clear()


def _instant_backoff(monkeypatch):
    """瞬态恢复的退避等待替换为立即返回（测试不真睡 8-30s）。"""
    async def _fast(seconds, cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise producer._TurnStopped()
    monkeypatch.setattr(producer, "_recover_backoff", _fast)


# ============ §1A：read tool 错误不击穿 turn ============
def test_read_tool_error_recovers(tmp_path):
    """shot.read 不存在 shot → tool failure 回模型 → Producer 继续并正常收尾。"""
    _save_proj(tmp_path)
    client = ScriptedClient([
        # producer 第一步：读一个不存在的镜头（executor 真实抛 AgentError）
        (None, [_tool_call("shot.read", {"shot_id": "s99_c99"}, "p1")]),
        # 收到 tool failure 后：producer 重新列镜头并如实汇报
        (None, [_tool_call("shot.list", {"scene_id": "s01"}, "p2")]),
        ("已确认：s99_c99 不存在；当前 s01 有 2 个镜头，无需调整。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "检查 s99_c99 这个镜头"))

    # turn 正常完成（不再以 failed 收尾）
    assert res["message"]["status"] is None
    assert res["assistant_message"].startswith("已确认")
    # 失败读取如实进 trace（前端可见 ok=False），但绝不进 receipts（truth 语义不变）
    failed = [t for t in res["trace"] if t["tool"] == "shot.read"]
    assert len(failed) == 1 and failed[0]["ok"] is False
    assert "镜头不存在" in (failed[0]["summary"] or "") or res["trace"][0]["ok"] is False
    assert res["receipts"] == [] and res["intent"] == "conversation"
    # canonical 零改动
    assert ProjectStore(tmp_path).load().story_text == "完整剧本正文。"


def test_story_read_missing_scene_also_recovers(tmp_path):
    """story.read 不存在 scene 同样转 tool failure（三类引用域之一）。"""
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("story.read", {"scene_id": "s88"}, "p1")]),
        ("s88 不存在，当前只有 s01/s02。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "读一下 s88"))
    assert res["message"]["status"] is None
    assert res["trace"][0]["tool"] == "story.read" and res["trace"][0]["ok"] is False


# ============ §1B：瞬态 provider 错误轻量恢复 ============
class _FlakyLLMClient(ScriptedClient):
    """前 N 次 LLM 调用抛指定异常，之后放行给内层 ScriptedClient。"""

    def __init__(self, inner, fail_times: int, exc: Exception):
        self.inner = inner
        self.fail_times = fail_times
        self.exc = exc
        self.calls = 0
        self.last_messages = None
        self.script = inner.script
        self.calls_log = inner.calls

    def _pop(self):
        return self.inner._pop()

    async def chat_completion(self, messages, **kw):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        self.last_messages = messages
        return await self.inner.chat_completion(messages, **kw)

    async def chat_completion_stream(self, messages, **kw):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        self.last_messages = messages
        async for ev in self.inner.chat_completion_stream(messages, **kw):
            yield ev


def test_transient_429_recovers_with_nudge(tmp_path, monkeypatch):
    """429 → producer 退避恢复一次 → turn 完成；恢复 nudge 真实进入模型消息。"""
    _save_proj(tmp_path)
    _instant_backoff(monkeypatch)
    inner = ScriptedClient([("收到，继续完成汇报。", None)])
    client = _FlakyLLMClient(inner, fail_times=1,
                             exc=AgnesError("429: rate limited", status=429))
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "汇报项目状态"))

    assert client.calls == 2                       # 1 次失败 + 1 次恢复成功
    assert res["message"]["status"] is None        # 诚实完成
    # 恢复 nudge 进入模型上下文（role=user，含 HTTP 429 与"继续"指引）
    user_msgs = [m for m in client.last_messages
                 if m.get("role") == "user" and "HTTP 429" in (m.get("content") or "")]
    assert len(user_msgs) == 1 and "继续未完成的步骤" in user_msgs[0]["content"]


def test_transient_exhaustion_fails_honestly(tmp_path, monkeypatch):
    """持续 429 → 2 次恢复后如实失败（不无限重试、不假成功）。"""
    _save_proj(tmp_path)
    _instant_backoff(monkeypatch)
    client = _FlakyLLMClient(ScriptedClient([]), fail_times=99,
                             exc=AgnesError("429: rate limited", status=429))
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "做点什么"))
    assert res["message"]["status"] == "failed"
    # 初次 + MAX_LLM_TRANSIENT_RECOVERIES 次恢复 = 3 次 LLM 调用
    assert client.calls == 1 + producer.MAX_LLM_TRANSIENT_RECOVERIES
    assert "执行失败" in res["assistant_message"]


def test_5xx_also_recoverable(tmp_path, monkeypatch):
    """500 与 520 同样走恢复路径（RETRYABLE 族）。"""
    _save_proj(tmp_path)
    _instant_backoff(monkeypatch)
    inner = ScriptedClient([("好的。", None)])
    for status in (500, 520):
        c = _FlakyLLMClient(ScriptedClient([("好的。", None)]), fail_times=1,
                            exc=AgnesError(f"{status}", status=status))
        rt = _runtime(tmp_path, client=c)
        sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
        res = _run(producer.producer_turn(rt, "demo", sid, "在吗"))
        assert c.calls == 2 and res["message"]["status"] is None, f"status={status}"


def test_auth_error_fails_immediately(tmp_path, monkeypatch):
    """401/403 非 RETRYABLE：不恢复、立即诚实失败。"""
    _save_proj(tmp_path)
    _instant_backoff(monkeypatch)
    client = _FlakyLLMClient(ScriptedClient([]), fail_times=99,
                             exc=AgnesError("401: unauthorized", status=401))
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "在吗"))
    assert res["message"]["status"] == "failed"
    assert client.calls == 1                       # 没有任何恢复尝试


def test_cancel_during_recovery_backoff_stops(tmp_path):
    """恢复退避期间用户停止 → 协作停止（status=stopped），不显示完成。"""
    _save_proj(tmp_path)
    cancel = asyncio.Event()
    cancel.set()                                    # 预先置位：退避入口立即协作停止
    client = _FlakyLLMClient(ScriptedClient([("好的。", None)]), fail_times=1,
                             exc=AgnesError("429", status=429))
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "在吗", cancel_event=cancel))
    assert res["message"]["status"] == "stopped"


def test_request_level_retry_exhaustion_still_recoverable(tmp_path, monkeypatch):
    """client 层重试耗尽抛的 AgnesError 保留原 status（429）→ producer 层仍可识别恢复。"""
    from src.api.agnes_client import RETRYABLE
    # 构造：模拟 _request 耗尽（status=原错误码），producer 层据此恢复
    e = AgnesError("重试 5 次仍失败: 429: slow down", status=429)
    assert e.status in RETRYABLE
    _save_proj(tmp_path)
    _instant_backoff(monkeypatch)
    client = _FlakyLLMClient(ScriptedClient([("好的。", None)]), fail_times=1, exc=e)
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "在吗"))
    assert res["message"]["status"] is None and client.calls == 2
