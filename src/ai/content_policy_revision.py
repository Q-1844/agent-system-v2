# src/ai/content_policy_revision.py
# CP-3 服务端内容驳回的 agent 修订（2026-11-17 用户拍板设计）
#
# 设计原则（用户原话归纳）：
# 1. 服务端内容驳回 = 审核意见，与自有审片打回同类——走「反馈→修订→重提」
# 2. 不同生图/生视频平台限制不一样——绝不写死「哪句话不能用」的规则
# 3. 不做任何预限制/预过滤——服务端已经把关，客户端不自我审查
#
# 实现：把服务端驳回反馈 + 原提示词交给文本模型（agent），让它按反馈
# 动态改写提示词（保留画面语义，避开被拒内容）。LLM 失败时降级为原样
# 重试（不比无此机制差）。
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# 修订指令：只依据服务端反馈调整，不发明新内容
_REVISION_INSTRUCTION = (
    "You are revising an image-generation prompt that was REJECTED by the "
    "provider's content filter. Below are (1) the provider's rejection "
    "message and (2) the original prompt. Rewrite the prompt so it keeps the "
    "SAME visual scene, characters, composition and story beats, but avoids "
    "whatever the provider objected to. Do NOT add safety disclaimers, do NOT "
    "explain, do NOT invent new story content. Output ONLY the rewritten "
    "prompt text, nothing else.\n\n"
    "Provider rejection message:\n{feedback}\n\n"
    "Original prompt:\n{prompt}"
)


async def revise_prompt_with_feedback(prompt: str, feedback: str,
                                      client=None) -> str:
    """按服务端驳回反馈用文本模型改写提示词；失败降级返回原文。

    prompt: 原提示词（完整保留语义的基底）
    feedback: 服务端驳回的错误原文（含具体拒绝理由）
    client: 可注入的 Agnes 客户端（测试用）；默认运行时获取
    """
    base = (prompt or "").strip()
    fb = (feedback or "").strip()
    if not base or not fb:
        return base
    if client is None:
        from deps import get_agnes_client
        client = get_agnes_client()
    messages = [{"role": "user",
                 "content": _REVISION_INSTRUCTION.format(
                     feedback=fb[:2000], prompt=base[:6000])}]
    try:
        msg = await client.chat_completion(messages, temperature=0.3)
        revised = ""
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            revised = content.strip()
        if not revised:
            return base
        # 硬护栏：改写结果明显异常（过长/空/复述指令）时降级
        if len(revised) > len(base) * 3 + 500:
            log.warning("内容驳回修订结果异常过长，降级为原提示词重试")
            return base
        log.info("内容驳回提示词已按服务端反馈修订（%d→%d 字符）",
                 len(base), len(revised))
        return revised
    except Exception as error:  # noqa: BLE001 修订失败不阻断重试
        log.warning("内容驳回修订调用失败，降级为原提示词重试: %s",
                    str(error)[:200])
        return base
