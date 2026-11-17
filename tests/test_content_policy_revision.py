"""CP-3 agent 修订（2026-11-17 用户拍板）：服务端内容驳回=审核意见。
agent 按反馈动态改写提示词；不写死平台规则；失败降级原样重试。"""
import pytest
from src.ai.content_policy_revision import revise_prompt_with_feedback


class _FakeClient:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.calls = []

    async def chat_completion(self, messages, **kw):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return {"content": self.content}


@pytest.mark.asyncio
async def test_revision_feeds_back_provider_message():
    """服务端驳回原文必须进入修订指令——agent 依据真实反馈调整。"""
    client = _FakeClient(content="a rewritten prompt")
    out = await revise_prompt_with_feedback("original prompt",
                                            "400 content_policy_violation: 暴力内容",
                                            client=client)
    assert out == "a rewritten prompt"
    sent = client.calls[0][0]["content"]
    assert "content_policy_violation" in sent
    assert "original prompt" in sent


@pytest.mark.asyncio
async def test_revision_degrades_to_original_on_failure():
    """LLM 调用失败 → 降级原提示词（重试仍发生，不比无机制差）。"""
    client = _FakeClient(error=RuntimeError("text quota"))
    out = await revise_prompt_with_feedback("keep me", "some feedback",
                                            client=client)
    assert out == "keep me"


@pytest.mark.asyncio
async def test_revision_guards_degenerate_output():
    """空回复/超长回复 → 降级原文（防指令复述与爆炸输出）。"""
    client = _FakeClient(content="   ")
    assert await revise_prompt_with_feedback("p", "f", client=client) == "p"
    client2 = _FakeClient(content="x" * 99999)
    assert await revise_prompt_with_feedback("p", "f", client=client2) == "p"


@pytest.mark.asyncio
async def test_revision_skips_without_feedback():
    """无反馈（正常批次）不调模型——不预审查原则。"""
    client = _FakeClient(content="should not be called")
    assert await revise_prompt_with_feedback("p", "", client=client) == "p"
    assert await revise_prompt_with_feedback("", "f", client=client) == ""
    assert client.calls == []
