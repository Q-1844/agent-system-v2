# -*- coding: utf-8 -*-
"""Round 03 local regression: one global token-budget policy.

Background (real Agnes, thinking=True, the repository's own Producer planning
prompt — evidence/gateC_producer_token_budget_probe.json):
    max_tokens=1500  -> the visible answer stops inside the JSON object,
                        `_parse_json` refuses it, and the Producer's real
                        decision silently degrades to `deterministic_fallback`.
    max_tokens=3000  -> first budget that returned a complete, parseable plan.

The repository used to hardcode a different small budget at every call site
(512 / 700 / 800 / 1024 / 1500 / 1600 / 2048 / 4096 / 6000), so the same silent
truncation could come back whenever someone edited one of them.  These tests
pin three things:

  1. the policy itself (pure function, no network);
  2. the CLIENT boundary — a call site cannot get a too-small budget onto the
     wire, whatever it asks for;
  3. a repository guard — no production call site may hardcode a budget below
     the safe floor, so the mistake cannot be reintroduced silently.

Measured endpoint ceiling (real calls, 2026-10-28 — evidence/
round03_max_tokens_ceiling_probe.json and evidence/
round03_max_tokens_other_routes_probe.json): Agnes text, SenseNova text and
Agnes vision all accept 65536 and reject 131072 with HTTP 400.
"""
from __future__ import annotations

import ast
import asyncio
import os
import re
from pathlib import Path

import httpx
import pytest

from src.api.agnes_client import AgnesClient
from src.api.model_budget import (
    DEFAULT_TEXT_MAX_TOKENS,
    MAX_ALLOWED_TEXT_MAX_TOKENS,
    MIN_SAFE_TEXT_MAX_TOKENS,
    resolve_text_max_tokens,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ============ 1. the policy (pure) ============

def test_default_is_the_endpoint_ceiling():
    """不传预算 = 端点能接受的极大值（实测 65536）。"""
    assert DEFAULT_TEXT_MAX_TOKENS == 65536
    assert DEFAULT_TEXT_MAX_TOKENS == MAX_ALLOWED_TEXT_MAX_TOKENS
    assert resolve_text_max_tokens(None) == 65536


def test_too_small_request_is_raised_to_the_safe_floor():
    """thinking 模式下过小的预算会把答案截断在 JSON 中间 —— 必须抬高而不是照办。"""
    assert resolve_text_max_tokens(512) == MIN_SAFE_TEXT_MAX_TOKENS
    assert resolve_text_max_tokens(1500) == MIN_SAFE_TEXT_MAX_TOKENS
    assert resolve_text_max_tokens(2048) == MIN_SAFE_TEXT_MAX_TOKENS
    assert resolve_text_max_tokens(1) == MIN_SAFE_TEXT_MAX_TOKENS


def test_explicit_safe_budget_is_respected():
    assert resolve_text_max_tokens(8192) == 8192
    assert resolve_text_max_tokens(3000) == 3000


def test_budget_above_the_endpoint_ceiling_is_clamped():
    """超过端点上限会 400，必须夹回而不是原样发出。"""
    assert resolve_text_max_tokens(131072) == 65536
    assert resolve_text_max_tokens(1048576) == 65536


def test_unusable_request_falls_back_to_the_default():
    for bad in (0, -1, "", "abc", object()):
        assert resolve_text_max_tokens(bad) == DEFAULT_TEXT_MAX_TOKENS


def test_floor_above_ceiling_is_a_configuration_error():
    with pytest.raises(ValueError):
        resolve_text_max_tokens(1000, floor=5000, ceiling=1000)


# ============ 2. the client boundary (what actually goes on the wire) ============

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
             "models": {"text": "agnes-text", "image": "mock-image",
                        "video": "mock-video"},
             "proxy": None, "rpm": None}

SSE_OK = (
    'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\n'
    'data: {"choices":[{"index":0,"delta":{"content":"hi"}}]}\n'
    'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n'
    'data: [DONE]\n'
)


def _client(handler):
    c = AgnesClient(AGNES_CFG, transport=httpx.MockTransport(handler))
    c.limiter = _NoLimiter()
    c.quota = _NoQuota()
    return c


def _json_handler(seen):
    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "ok"}}]})
    return handler


def _body(seen, index=0):
    import json as _json
    return _json.loads(seen[index].content.decode("utf-8"))


def test_chat_raises_a_too_small_budget_on_the_wire():
    seen = []
    c = _client(_json_handler(seen))
    asyncio.run(c.chat([{"role": "user", "content": "q"}], max_tokens=512))
    assert _body(seen)["max_tokens"] == MIN_SAFE_TEXT_MAX_TOKENS


def test_chat_without_a_budget_uses_the_largest_one():
    seen = []
    c = _client(_json_handler(seen))
    asyncio.run(c.chat([{"role": "user", "content": "q"}]))
    assert _body(seen)["max_tokens"] == DEFAULT_TEXT_MAX_TOKENS


def test_chat_completion_raises_a_too_small_budget_on_the_wire():
    seen = []
    c = _client(_json_handler(seen))
    asyncio.run(c.chat_completion([{"role": "user", "content": "q"}], max_tokens=700))
    assert _body(seen)["max_tokens"] == MIN_SAFE_TEXT_MAX_TOKENS


def test_chat_completion_vision_raises_a_too_small_budget_on_the_wire():
    seen = []
    c = _client(_json_handler(seen))
    asyncio.run(c.chat_completion_vision([{"role": "user", "content": "q"}],
                                         model="agnes-2.5-flash", max_tokens=300))
    assert _body(seen)["max_tokens"] == MIN_SAFE_TEXT_MAX_TOKENS


def test_chat_completion_stream_raises_a_too_small_budget_on_the_wire():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, text=SSE_OK,
                              headers={"content-type": "text/event-stream"})

    c = _client(handler)

    async def run():
        async for _ev in c.chat_completion_stream([{"role": "user", "content": "q"}],
                                                 max_tokens=512):
            pass

    asyncio.run(run())
    assert _body(seen)["max_tokens"] == MIN_SAFE_TEXT_MAX_TOKENS


def test_budget_above_the_ceiling_never_reaches_the_wire():
    seen = []
    c = _client(_json_handler(seen))
    asyncio.run(c.chat([{"role": "user", "content": "q"}], max_tokens=999999))
    assert _body(seen)["max_tokens"] == MAX_ALLOWED_TEXT_MAX_TOKENS


# ============ 3. repository guard ============

#: The policy module is the definition site; it is allowed to mention the
#: numbers it defines.
GUARD_SKIP = {"src/api/model_budget.py"}


def _production_files():
    for root in ("src", "routers"):
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in sorted(filenames):
                if not name.endswith(".py"):
                    continue
                p = Path(dirpath) / name
                rel = str(p.relative_to(REPO_ROOT)).replace("\\", "/")
                if rel in GUARD_SKIP:
                    continue
                yield rel, p


def test_no_production_call_site_hardcodes_a_budget_below_the_floor():
    """守卫：生产代码不得再写死小于安全下限的预算。

    这条测试就是「以后 GPT 或任何人忘了」的那道闸门——预算写小了会直接
    让本轮回归变红，而不是等到某次真实生产静默降级才发现。
    """
    offenders = []
    for rel, path in _production_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            offenders.append("%s: 文件无法解析 %s" % (rel, exc))
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "max_tokens":
                    continue
                if (isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, int)
                        and kw.value.value < MIN_SAFE_TEXT_MAX_TOKENS):
                    offenders.append("%s:%d max_tokens=%d（安全下限 %d）"
                                     % (rel, kw.lineno, kw.value.value,
                                        MIN_SAFE_TEXT_MAX_TOKENS))
    assert not offenders, ("生产代码里仍有低于安全下限的硬编码预算：\n  "
                            + "\n  ".join(offenders))


def test_the_policy_module_is_importable_and_self_consistent():
    assert MIN_SAFE_TEXT_MAX_TOKENS <= DEFAULT_TEXT_MAX_TOKENS
    assert DEFAULT_TEXT_MAX_TOKENS <= MAX_ALLOWED_TEXT_MAX_TOKENS
    assert resolve_text_max_tokens(MIN_SAFE_TEXT_MAX_TOKENS) == MIN_SAFE_TEXT_MAX_TOKENS


def test_producer_planning_uses_the_shared_policy():
    """Producer 规划调用必须走同一套常量，不再自带 1500/8192。"""
    from src.agents import producer_orchestration as po
    from src.api.model_budget import DEFAULT_TEXT_MAX_TOKENS as shared_default
    assert po.PRODUCER_PLANNING_MAX_TOKENS == shared_default
    assert po.PRODUCER_PLANNING_MIN_SAFE_TOKENS == MIN_SAFE_TEXT_MAX_TOKENS
