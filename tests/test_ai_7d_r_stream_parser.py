"""AI-7D-R Phase 2 · Agnes streaming parser 测试。

测 `_parse_sse_events`（纯 async 解析器，无网络）+ `chat_completion_stream`（MockTransport 集成）。
据 probe 实测格式（见 AI-7D-R_STREAM_PROBE.md）：
  - content delta / reasoning_content delta / tool_calls 分片 / 单块 tool_calls /
    usage-only 尾块 / [DONE] / 畸形行。
"""
import asyncio
import json

import httpx

from src.api.agnes_client import AgnesClient, _parse_sse_events


def _line(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False)


def _events(lines):
    """跑 _parse_sse_events 收集所有事件（纯解析，无网络）。"""
    async def _alines():
        for l in lines:
            yield l

    async def _collect():
        out = []
        async for ev in _parse_sse_events(_alines()):
            out.append(ev)
        return out
    return asyncio.run(_collect())


# ============ 1. 纯文本流 ============
def test_parser_text_delta_and_finish():
    lines = [
        _line({"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]}),
        _line({"choices": [{"index": 0, "delta": {"content": "1"}}]}),
        _line({"choices": [{"index": 0, "delta": {"content": "2"}}]}),
        _line({"choices": [{"index": 0, "delta": {"content": "3"}}]}),
        _line({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        _line({"choices": [{"index": 0, "delta": {}}], "usage": {
            "completion_tokens": 3, "prompt_tokens": 10, "total_tokens": 13,
            "completion_tokens_details": {"reasoning_tokens": 0}}}),
        "data: [DONE]",
    ]
    evs = _events(lines)
    deltas = [e for e in evs if e["kind"] == "content_delta"]
    assert [d["delta"] for d in deltas] == ["1", "2", "3"]   # 首块 content:"" 不当 delta
    finish = evs[-1]
    assert finish["kind"] == "finish"
    assert finish["reason"] == "stop"
    assert finish["content"] == "123"
    assert finish["tool_calls"] is None
    assert finish["usage"]["completion_tokens"] == 3


# ============ 2. 分片 tool arguments ============
def test_parser_fragmented_tool_arguments():
    lines = [
        _line({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"id": "call_1", "function": {"arguments": "", "name": "get_weather"},
             "type": "function", "index": 0}]}}]}),
        _line({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"function": {"arguments": "{"}, "type": "function", "index": 0}]}}]}),
        _line({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"function": {"arguments": "\"city\": \"北京\""}, "type": "function", "index": 0}]}}]}),
        _line({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"function": {"arguments": "}"}, "type": "function", "index": 0}]}}]}),
        _line({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]
    evs = _events(lines)
    finish = evs[-1]
    assert finish["reason"] == "tool_calls"
    assert finish["content"] is None
    tc = finish["tool_calls"]
    assert len(tc) == 1
    assert tc[0]["id"] == "call_1"
    assert tc[0]["function"]["name"] == "get_weather"
    assert json.loads(tc[0]["function"]["arguments"]) == {"city": "北京"}


# ============ 3. 单块完整 tool call（整 tool_calls 一次返回）============
def test_parser_single_chunk_tool_call():
    lines = [
        _line({"choices": [{"index": 0, "delta": {"content": "", "role": "assistant",
            "tool_calls": [{"id": "call_1", "function": {
                "arguments": "{\"city\":\"北京\"}", "name": "get_weather"},
                "type": "function", "index": 0}]}}]}),
        _line({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]
    evs = _events(lines)
    finish = evs[-1]
    assert finish["reason"] == "tool_calls"
    tc = finish["tool_calls"]
    assert tc[0]["id"] == "call_1"
    assert tc[0]["function"]["name"] == "get_weather"
    assert json.loads(tc[0]["function"]["arguments"]) == {"city": "北京"}


# ============ 4. reasoning_content ============
def test_parser_reasoning_content():
    lines = [
        _line({"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]}),
        _line({"choices": [{"index": 0, "delta": {"reasoning_content": "先算"}}]}),
        _line({"choices": [{"index": 0, "delta": {"reasoning_content": "乘法"}}]}),
        _line({"choices": [{"index": 0, "delta": {"content": "391"}}]}),
        _line({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        _line({"choices": [{"index": 0, "delta": {}}], "usage": {
            "completion_tokens": 5,
            "completion_tokens_details": {"reasoning_tokens": 2}}}),
        "data: [DONE]",
    ]
    evs = _events(lines)
    r = [e for e in evs if e["kind"] == "reasoning_delta"]
    assert [d["delta"] for d in r] == ["先算", "乘法"]
    c = [e for e in evs if e["kind"] == "content_delta"]
    assert [d["delta"] for d in c] == ["391"]
    finish = evs[-1]
    assert finish["content"] == "391"
    assert finish["usage"]["completion_tokens_details"]["reasoning_tokens"] == 2


# ============ 5. usage-only 尾块（空 delta + usage）============
def test_parser_usage_only_chunk():
    lines = [
        _line({"choices": [{"index": 0, "delta": {"content": "hi"}}]}),
        _line({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        _line({"choices": [{"index": 0, "delta": {}}], "usage": {
            "completion_tokens": 1, "prompt_tokens": 5, "total_tokens": 6}}),
        "data: [DONE]",
    ]
    evs = _events(lines)
    # 空 delta 不产生 spurious content_delta
    assert [e for e in evs if e["kind"] == "content_delta"] == [
        {"kind": "content_delta", "delta": "hi"}]
    assert evs[-1]["usage"]["total_tokens"] == 6


# ============ 6. [DONE] 终结 ============
def test_parser_done_terminator():
    lines = [
        _line({"choices": [{"index": 0, "delta": {"content": "x"}}]}),
        _line({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    evs = _events(lines)
    assert evs[-1]["kind"] == "finish"
    assert evs[-1]["reason"] == "stop"


# ============ 7. 畸形/半截 SSE 行容错 ============
def test_parser_malformed_lines_skipped():
    lines = [
        ": ping",                # 注释行
        "event: ping",           # event 行
        "data: {not json",       # 畸形 JSON
        "data: ",                # 空 data
        _line({"choices": [{"index": 0, "delta": {"content": "ok"}}]}),
        _line({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    evs = _events(lines)
    assert [e for e in evs if e["kind"] == "content_delta"] == [
        {"kind": "content_delta", "delta": "ok"}]
    assert evs[-1]["kind"] == "finish"
    assert evs[-1]["content"] == "ok"


# ============ 8. 多 tool_call（不同 index）============
def test_parser_multiple_tool_calls():
    lines = [
        _line({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"id": "call_a", "function": {"arguments": "", "name": "story.list"},
             "type": "function", "index": 0}]}}]}),
        _line({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"function": {"arguments": "{}"}, "type": "function", "index": 0}]}}]}),
        _line({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"id": "call_b", "function": {"arguments": "", "name": "script.read"},
             "type": "function", "index": 1}]}}]}),
        _line({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"function": {"arguments": "{}"}, "type": "function", "index": 1}]}}]}),
        _line({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]
    evs = _events(lines)
    tc = evs[-1]["tool_calls"]
    assert len(tc) == 2
    assert tc[0]["function"]["name"] == "story.list"
    assert tc[1]["function"]["name"] == "script.read"
    assert tc[0]["id"] == "call_a" and tc[1]["id"] == "call_b"


# ============ 9. 集成：chat_completion_stream via MockTransport ============
def test_chat_completion_stream_via_mock():
    sse = (
        'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}\n'
        'data: {"choices":[{"index":0,"delta":{"content":"hi"}}]}\n'
        'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n'
        'data: {"choices":[{"index":0,"delta":{}}],"usage":{"completion_tokens":1,"prompt_tokens":2,"total_tokens":3}}\n'
        'data: [DONE]\n'
    )

    def handler(req):
        return httpx.Response(200, text=sse,
                              headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    cfg = {"base_url": "https://x.test", "api_key": "k",
           "models": {"text": "m"}, "proxy": None}
    c = AgnesClient(cfg, transport=transport)
    out = []

    async def run():
        async for ev in c.chat_completion_stream([{"role": "user", "content": "hi"}]):
            out.append(ev)
    asyncio.run(run())
    assert any(e["kind"] == "content_delta" and e["delta"] == "hi" for e in out)
    assert out[-1]["kind"] == "finish"
    assert out[-1]["content"] == "hi"
    assert out[-1]["usage"]["total_tokens"] == 3


def test_chat_completion_stream_tool_call_via_mock():
    sse = (
        'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":"","tool_calls":[{"id":"call_1","function":{"arguments":"","name":"get_weather"},"type":"function","index":0}]}}]}\n'
        'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"function":{"arguments":"{\\"city\\":\\"北京\\"}"},"type":"function","index":0}]}}]}\n'
        'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n'
        'data: [DONE]\n'
    )

    def handler(req):
        return httpx.Response(200, text=sse,
                              headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    cfg = {"base_url": "https://x.test", "api_key": "k",
           "models": {"text": "m"}, "proxy": None}
    c = AgnesClient(cfg, transport=transport)
    out = []

    async def run():
        async for ev in c.chat_completion_stream([{"role": "user", "content": "q"}],
                                                  tools=[{"type": "function",
                                                          "function": {"name": "get_weather",
                                                                       "parameters": {}}}]):
            out.append(ev)
    asyncio.run(run())
    finish = out[-1]
    assert finish["reason"] == "tool_calls"
    assert json.loads(finish["tool_calls"][0]["function"]["arguments"]) == {"city": "北京"}


def test_chat_completion_stream_reasoning_via_mock():
    sse = (
        'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}\n'
        'data: {"choices":[{"index":0,"delta":{"reasoning_content":"想"}}]}\n'
        'data: {"choices":[{"index":0,"delta":{"reasoning_content":"一下"}}]}\n'
        'data: {"choices":[{"index":0,"delta":{"content":"ok"}}]}\n'
        'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n'
        'data: [DONE]\n'
    )

    def handler(req):
        return httpx.Response(200, text=sse,
                              headers={"content-type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    cfg = {"base_url": "https://x.test", "api_key": "k",
           "models": {"text": "m"}, "proxy": None}
    c = AgnesClient(cfg, transport=transport)
    out = []

    async def run():
        async for ev in c.chat_completion_stream([{"role": "user", "content": "q"}],
                                                  thinking=True):
            out.append(ev)
    asyncio.run(run())
    assert [e["delta"] for e in out if e["kind"] == "reasoning_delta"] == ["想", "一下"]
    assert out[-1]["content"] == "ok"


# ============ 9. tool_call_pending：工具名一出现就提前通知（参数仍在生成） ============
def test_parser_tool_call_pending_on_first_name_fragment():
    """分片流：name 在首片出现即发 tool_call_pending，且每个 index 只发一次。"""
    lines = [
        _line({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"id": "call_1", "function": {"arguments": "", "name": "story.create_many"},
             "type": "function", "index": 0}]}}]}),
        _line({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"function": {"arguments": "{\"scenes\":"}, "type": "function", "index": 0}]}}]}),
        _line({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"function": {"arguments": "[{\"summary\":\"x\"}]}"}, "type": "function", "index": 0}]}}]}),
        _line({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]
    evs = _events(lines)
    pending = [e for e in evs if e["kind"] == "tool_call_pending"]
    assert len(pending) == 1            # 同名 index 只发一次
    assert pending[0]["name"] == "story.create_many"
    assert pending[0]["index"] == 0
    # pending 出现在 finish 之前
    assert evs.index(pending[0]) < evs.index([e for e in evs if e["kind"] == "finish"][0])
    # finish 的聚合结果不受影响
    finish = evs[-1]
    assert finish["tool_calls"][0]["function"]["name"] == "story.create_many"


def test_parser_tool_call_pending_single_chunk_and_multi_index():
    """单块完整 tool_calls 也发 pending；一步多工具各发一次（按 index）。"""
    lines = [
        _line({"choices": [{"index": 0, "delta": {"content": "", "role": "assistant",
            "tool_calls": [
                {"id": "c1", "function": {"arguments": "{}", "name": "story.list"},
                 "type": "function", "index": 0},
                {"id": "c2", "function": {"arguments": "{}", "name": "script.read"},
                 "type": "function", "index": 1}]}}]}),
        _line({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]
    evs = _events(lines)
    pending = sorted((e["index"], e["name"]) for e in evs if e["kind"] == "tool_call_pending")
    assert pending == [(0, "story.list"), (1, "script.read")]
