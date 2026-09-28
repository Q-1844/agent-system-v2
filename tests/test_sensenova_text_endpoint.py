# -*- coding: utf-8 -*-
"""SenseNova 接入（2026-08-29）provider 层适配测试。

覆盖：
1. SSE 解析器 reasoning 字段容错（SenseNova 6.8 实测字段名 `reasoning`，Agnes 为
   `reasoning_content`；2026-08-29 probe results/03_flash_stream_scan.json delta_keys）
2. text_endpoint 注入后文本三入口走 SenseNova endpoint（URL / model / 认证 / thinking 参数映射）
3. SenseNova key 文件热切换（SENSENOVA_KEY_FILE 内容优先于配置 key，改文件即生效）
4. Agnes 原行为回归（text_endpoint=None 时 URL / model / thinking 参数 / 认证完全不变）
5. PipelineController text_provider 选择（sensenova 构造 / agnes 缺省 / 配置不完整回退）

probe 实测依据（Desktop/sensenova-tools/results/）：
  - thinking 默认开启；开关参数 thinking: {"type": "enabled"|"disabled"|"adaptive"}
  - 流式 delta.reasoning；usage 尾块；[DONE] 终结
"""
import asyncio
import json

import httpx
import pytest

from src.api.agnes_client import AgnesClient, _parse_sse_events
from src.pipeline.controller import PipelineController


@pytest.fixture(autouse=True)
def _isolate_key_file(tmp_path, monkeypatch):
    """隔离 key 热切换：默认把 SENSENOVA_KEY_FILE 指向不存在的路径,
    防止开发机真实的 ~/.sensenova/active_key.txt（切换按钮产物）影响断言。
    需要真实热切换语义的用例自行 setenv 覆盖。"""
    monkeypatch.setenv("SENSENOVA_KEY_FILE", str(tmp_path / "no_such_key_file"))


def _line(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False)


def _events(lines):
    async def _alines():
        for l in lines:
            yield l

    async def _collect():
        out = []
        async for ev in _parse_sse_events(_alines()):
            out.append(ev)
        return out
    return asyncio.run(_collect())


# 与 tests/mock_agnes.py 相同的 no-op stub（限流睡觉/配额污染进程单例）
class _NoLimiter:
    def slot(self, bucket):
        class _Slot:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
        return _Slot()


class _NoQuota:
    def can(self, *a, **kw):
        return True, ""

    def consume(self, *a, **kw):
        pass


AGNES_CFG = {"base_url": "https://mock.agnes", "api_key": "agnes-key",
             "models": {"text": "agnes-text", "image": "mock-image", "video": "mock-video"},
             "proxy": None, "rpm": None}

SENSENOVA_EP = {"base_url": "https://mock.sensenova",
                "api_key": "cfg-key",
                "model": "sensenova-6.8-flash-lite", "provider": "sensenova"}


def make_client(handler, text_ep=None):
    c = AgnesClient(AGNES_CFG, transport=httpx.MockTransport(handler),
                    text_endpoint=text_ep)
    c.limiter = _NoLimiter()
    c.quota = _NoQuota()
    return c


SSE_OK = (
    'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\n'
    'data: {"choices":[{"index":0,"delta":{"reasoning":"想"}}]}\n'
    'data: {"choices":[{"index":0,"delta":{"content":"hi"}}]}\n'
    'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n'
    'data: {"choices":[{"index":0,"delta":{}}],"usage":{"completion_tokens":2,"prompt_tokens":3,"total_tokens":5,"completion_tokens_details":{"reasoning_tokens":1}}}\n'
    'data: [DONE]\n'
)


def _collect_stream(c, **kw):
    async def run():
        out = []
        async for ev in c.chat_completion_stream([{"role": "user", "content": "q"}], **kw):
            out.append(ev)
        return out
    return asyncio.run(run())


# ============ 1. 解析器：SenseNova reasoning 字段 -> reasoning_delta ============
def test_parser_sensenova_reasoning_field():
    lines = [
        _line({"choices": [{"index": 0, "delta": {"role": "assistant"}}]}),
        _line({"choices": [{"index": 0, "delta": {"reasoning": "先想"}}]}),
        _line({"choices": [{"index": 0, "delta": {"reasoning": "再答"}}]}),
        _line({"choices": [{"index": 0, "delta": {"content": "42"}}]}),
        _line({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        _line({"choices": [{"index": 0, "delta": {}}], "usage": {
            "completion_tokens": 3, "prompt_tokens": 5, "total_tokens": 8,
            "completion_tokens_details": {"reasoning_tokens": 2}}}),
        "data: [DONE]",
    ]
    evs = _events(lines)
    assert [e["delta"] for e in evs if e["kind"] == "reasoning_delta"] == ["先想", "再答"]
    assert [e["delta"] for e in evs if e["kind"] == "content_delta"] == ["42"]
    finish = evs[-1]
    assert finish["content"] == "42"
    assert finish["usage"]["completion_tokens_details"]["reasoning_tokens"] == 2


# ============ 2. 流式：text_endpoint 注入后走 SenseNova endpoint ============
def test_stream_routes_to_sensenova_endpoint():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, text=SSE_OK,
                              headers={"content-type": "text/event-stream"})

    c = make_client(handler, text_ep=SENSENOVA_EP)
    out = _collect_stream(c)

    req = seen[0]
    assert str(req.url) == "https://mock.sensenova/v1/chat/completions"
    assert req.headers["Authorization"] == "Bearer cfg-key"
    body = json.loads(req.content)
    assert body["model"] == "sensenova-6.8-flash-lite"
    # 🔴 SenseNova thinking 默认开启（probe 实测），False 必须显式 disabled
    assert body["thinking"] == {"type": "disabled"}
    assert "chat_template_kwargs" not in body
    # 事件契约与 Agnes 完全一致（reasoning 字段已在解析器统一）
    assert any(e["kind"] == "reasoning_delta" and e["delta"] == "想" for e in out)
    assert any(e["kind"] == "content_delta" and e["delta"] == "hi" for e in out)
    assert out[-1]["kind"] == "finish" and out[-1]["content"] == "hi"
    assert out[-1]["usage"]["total_tokens"] == 5


def test_stream_sensenova_thinking_on_enabled():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, text=SSE_OK,
                              headers={"content-type": "text/event-stream"})

    c = make_client(handler, text_ep=SENSENOVA_EP)
    _collect_stream(c, thinking=True)
    body = json.loads(seen[0].content)
    assert body["thinking"] == {"type": "enabled"}


def test_stream_sensenova_tools_passthrough():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, text=SSE_OK,
                              headers={"content-type": "text/event-stream"})

    c = make_client(handler, text_ep=SENSENOVA_EP)
    tools = [{"type": "function", "function": {"name": "story.list", "parameters": {}}}]
    _collect_stream(c, tools=tools, tool_choice="auto")
    body = json.loads(seen[0].content)
    assert body["tools"] == tools
    assert body["tool_choice"] == "auto"


# ============ 3. 非流式：路由 + tool_calls 形状 ============
def test_chat_completion_routes_to_sensenova():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={
            "choices": [{"message": {
                "role": "assistant", "content": None,
                "reasoning": "（模型思考文本）",
                "tool_calls": [{"id": "call_x", "type": "function",
                                "function": {"name": "story.list",
                                             "arguments": "{\"limit\":3}"}}]},
                "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                      "completion_tokens_details": {"reasoning_tokens": 3}},
        })

    c = make_client(handler, text_ep=SENSENOVA_EP)
    resp = asyncio.run(c.chat_completion(
        [{"role": "user", "content": "q"}],
        tools=[{"type": "function", "function": {"name": "story.list",
                                                 "parameters": {}}}],
        tool_choice="auto"))

    req = seen[0]
    assert str(req.url) == "https://mock.sensenova/v1/chat/completions"
    body = json.loads(req.content)
    assert body["model"] == "sensenova-6.8-flash-lite"
    assert body["thinking"] == {"type": "disabled"}
    # 返回形状与 Agnes 一致：content / tool_calls / role（reasoning 不上浮，与非流式原有行为一致）
    assert resp["role"] == "assistant"
    assert resp["content"] is None
    assert resp["tool_calls"][0]["id"] == "call_x"
    assert resp["tool_calls"][0]["function"]["name"] == "story.list"


def test_chat_routes_to_sensenova():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "ok",
                                     "reasoning": "hmm"}}]})

    c = make_client(handler, text_ep=SENSENOVA_EP)
    text = asyncio.run(c.chat([{"role": "user", "content": "hi"}], thinking=False))
    assert text == "ok"
    assert str(seen[0].url) == "https://mock.sensenova/v1/chat/completions"
    assert json.loads(seen[0].content)["thinking"] == {"type": "disabled"}


# ============ 4. key 文件热切换 ============
def test_sensenova_key_file_hotswap(tmp_path, monkeypatch):
    kf = tmp_path / "active_key.txt"
    kf.write_text("file-key-B\n", encoding="utf-8")
    monkeypatch.setenv("SENSENOVA_KEY_FILE", str(kf))
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, text=SSE_OK,
                              headers={"content-type": "text/event-stream"})

    c = make_client(handler, text_ep=SENSENOVA_EP)
    _collect_stream(c)
    assert seen[0].headers["Authorization"] == "Bearer file-key-B"

    # 改文件内容 -> 同一 client 实例下一次请求即生效（热切换，无需重启）
    kf.write_text("file-key-A", encoding="utf-8")
    _collect_stream(c)
    assert seen[1].headers["Authorization"] == "Bearer file-key-A"

    # 文件清空 -> 回落配置 key
    kf.write_text("   \n", encoding="utf-8")
    _collect_stream(c)
    assert seen[2].headers["Authorization"] == "Bearer cfg-key"


def test_sensenova_key_fallback_to_config_when_no_file(tmp_path, monkeypatch):
    # 无 SENSENOVA_KEY_FILE 且默认路径（$HOME/.sensenova/active_key.txt）不存在 -> 配置 key
    monkeypatch.delenv("SENSENOVA_KEY_FILE", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))   # Path.home() 在 Windows 读 USERPROFILE
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, text=SSE_OK,
                              headers={"content-type": "text/event-stream"})

    c = make_client(handler, text_ep=SENSENOVA_EP)
    _collect_stream(c)
    assert seen[0].headers["Authorization"] == "Bearer cfg-key"


# ============ 5. Agnes 原行为回归（text_endpoint=None） ============
def test_agnes_default_unchanged_thinking_and_routing():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    c = make_client(handler, text_ep=None)

    asyncio.run(c.chat_completion([{"role": "user", "content": "q"}], thinking=True))
    body_on = json.loads(seen[0].content)
    assert str(seen[0].url) == "https://mock.agnes/v1/chat/completions"
    assert body_on["model"] == "agnes-text"
    assert body_on["chat_template_kwargs"] == {"enable_thinking": True}
    assert "thinking" not in body_on
    assert seen[0].headers["Authorization"] == "Bearer agnes-key"

    asyncio.run(c.chat_completion([{"role": "user", "content": "q"}], thinking=False))
    body_off = json.loads(seen[1].content)
    assert "thinking" not in body_off
    assert "chat_template_kwargs" not in body_off


def test_image_still_main_endpoint_with_text_ep():
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path == "/v1/images/generations":
            return httpx.Response(200, json={"data": [{"url": "https://mock.agnes/i.png",
                                                       "b64_json": None}]})
        return httpx.Response(404, text="unmocked")

    c = make_client(handler, text_ep=SENSENOVA_EP)
    out = asyncio.run(c.generate_image("a cat"))
    assert out == {"url": "https://mock.agnes/i.png", "b64": None}
    assert seen[0].url.host == "mock.agnes"      # 图片仍走主 endpoint（Agnes）
    assert seen[0].headers["Authorization"] == "Bearer agnes-key"


def test_sensenova_non200_error_mentions_provider():
    def handler(req):
        return httpx.Response(400, text='{"error":{"message":"bad"}}')

    c = make_client(handler, text_ep=SENSENOVA_EP)
    try:
        asyncio.run(c.chat_completion([{"role": "user", "content": "q"}]))
        assert False, "should raise"
    except Exception as e:
        assert "SenseNova API 错误 400" in str(e)


def test_sensenova_stream_retry_on_500_then_success():
    """SenseNova 实测（probe round2/3）：tool calling 偶发 500 engine is not available。
    流式在未产出事件前对 RETRYABLE 状态重试（text_ep 注入时 3 次尝试）。"""
    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(500, text='{"error":{"message":"engine is not '
                                           'available temporarily"}}')
        return httpx.Response(200, text=SSE_OK,
                              headers={"content-type": "text/event-stream"})

    c = make_client(handler, text_ep=SENSENOVA_EP)
    out = _collect_stream(c)
    assert len(calls) == 2                       # 首次 500 重试后成功
    assert out[-1]["kind"] == "finish" and out[-1]["content"] == "hi"


def test_agnes_stream_retryable_retries_then_raises():
    """Agnes（无 text_ep）流式遇 RETRYABLE（500 族）→ 有限重试（3 次尝试）后抛。
    2026-09-06 收口 §1B：流式重试从 SenseNova 专属扩展到 Agnes 主路径
    （真机：producer turn 因 429/500 直接 [执行失败]）；成功路径行为不变。"""
    import src.api.agnes_client as ac
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(500, text="boom")

    c = make_client(handler, text_ep=None)
    orig_sleep = ac.asyncio.sleep

    async def _no_sleep(s):
        pass
    ac.asyncio.sleep = _no_sleep
    try:
        try:
            _collect_stream(c)
            assert False, "should raise"
        except Exception as e:
            assert "500" in str(e)
        assert len(calls) == 5        # 42.md 缺陷③：流式 5 次尝试（对齐非流式 _request 耐心）
    finally:
        ac.asyncio.sleep = orig_sleep


def test_agnes_stream_non_retryable_single_attempt():
    """非 RETRYABLE（401 auth 等）仍然立即失败：单次尝试、不重试。"""
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(401, text="unauthorized")

    c = make_client(handler, text_ep=None)
    try:
        _collect_stream(c)
        assert False, "should raise"
    except Exception as e:
        assert "401" in str(e)
    assert len(calls) == 1


# ============ 6. PipelineController sensenova endpoint / 双客户端接线 ============
def _controller_with(config: dict) -> PipelineController:
    ctl = PipelineController.__new__(PipelineController)   # 只测选择逻辑，不做文件 IO
    ctl.config = config
    return ctl


FULL_SN = {"base_url": "https://token.sensenova.cn", "api_key": "k",
           "models": {"text": "sensenova-6.8-flash-lite"}}


def test_sensenova_endpoint_built_from_complete_block():
    ep = _controller_with({"sensenova": dict(FULL_SN)})._sensenova_text_endpoint()
    assert ep == {"base_url": "https://token.sensenova.cn", "api_key": "k",
                  "model": "sensenova-6.8-flash-lite", "provider": "sensenova"}


def test_sensenova_endpoint_none_when_incomplete():
    no_key = {"sensenova": {"base_url": FULL_SN["base_url"], "models": {"text": "m"}}}
    assert _controller_with(no_key)._sensenova_text_endpoint() is None
    unsubst = {"sensenova": {**FULL_SN, "api_key": "${SENSENOVA_API_KEY}"}}
    assert _controller_with(unsubst)._sensenova_text_endpoint() is None
    assert _controller_with({})._sensenova_text_endpoint() is None


def test_config_text_provider_normalized():
    f = _controller_with({})._config_text_provider
    assert f() == "agnes"                      # 缺省 = Agnes（2026-08-29 默认）
    assert _controller_with({"text_provider": "sensenova"})._config_text_provider() == "sensenova"
    assert _controller_with({"text_provider": "wat"})._config_text_provider() == "agnes"


def test_controller_wires_dual_clients(tmp_path, monkeypatch):
    """真实构造：默认 text_provider=agnes -> 主客户端文本走 Agnes、SenseNova 客户端
    常备可用；text_provider=sensenova -> 主客户端也走 SenseNova。"""
    monkeypatch.setenv("SENSENOVA_API_KEY", "env-key")
    monkeypatch.setenv("AGNES_API_KEY", "agnes-env-key")
    base_cfg = ("agnes:\n  base_url: https://mock.agnes\n  api_key: ${AGNES_API_KEY}\n"
                "  models: {text: agnes-2.5-flash, image: i, video: v}\n"
                "sensenova:\n  base_url: https://token.sensenova.cn\n"
                "  api_key: ${SENSENOVA_API_KEY}\n"
                "  models: {text: sensenova-6.8-flash-lite}\n"
                f"project:\n  base_dir: {tmp_path.as_posix()}/projects\n")
    p = tmp_path / "config.yaml"
    p.write_text(base_cfg, encoding="utf-8")

    ctl = PipelineController(str(p))
    assert ctl.agnes_client._text_ep is None                      # 默认主路由 = Agnes
    sn = ctl.sensenova_text_client
    assert sn is not None and sn._text_ep["model"] == "sensenova-6.8-flash-lite"

    p.write_text(base_cfg + "text_provider: sensenova\n", encoding="utf-8")
    ctl2 = PipelineController(str(p))
    assert ctl2.agnes_client._text_ep["model"] == "sensenova-6.8-flash-lite"


def test_load_config_substitutes_sensenova_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SENSENOVA_API_KEY", "env-key")
    p = tmp_path / "config.yaml"
    p.write_text(
        "agnes:\n  api_key: ${AGNES_API_KEY}\n"
        "sensenova:\n  api_key: ${SENSENOVA_API_KEY}\n"
        "project:\n  base_dir: ./projects\n",
        encoding="utf-8")
    ctl = PipelineController.__new__(PipelineController)
    cfg = ctl._load_config(str(p))
    assert cfg["sensenova"]["api_key"] == "env-key"
