"""Renewable Execution Window 测试（不联网，ScriptedClient）。

验证 _run_tool_loop 的 renewable window 机制（director / script_writer /
character_designer 三镜像统一由本文件 + 各 native 测试文件的 B 场景镜像共同验证）：
  A  三文件 DEFAULT_MAX_TOOL_STEPS == 1024（窗口大小，非硬停）。
  B  窗口用满 → 注入 self-check nudge → 续期 → 继续读工具 → 纯文本正常结束；
     nudge 恰好注入一次，window_renewals == 1，返回文本 == 模型原话。
  C  self-check 后模型选择停止（纯文本）→ 正常返回（非异常 / 非 STOPPED）。
  E  cancel_event 仍在步骤边界生效（renewable window 不破坏协作式停止）。
  G  subagent instance 不再定义 INSTANCE_WALL_CLOCK_S 常量；INSTANCE_MAX_STEPS == 1024。

设计要点：instruction 用含「如何」的问句 → chat_turn=True，写域真相闸门双保险绕过；
读工具（shot.list）反复调用幂等无害、零 receipts，故 _append_fact_summary 原样返回，
最终文本可精确相等断言。
"""
import threading

import pytest

from tests.test_director_agent import (
    _save_proj, _runtime, _run, _tool_call, ScriptedClient as DirectorClient,
)
from src.agents.director import (
    _run_tool_loop as director_loop,
    DEFAULT_MAX_TOOL_STEPS as DIRECTOR_MAX_STEPS,
    _TurnStopped,
)
from src.agents.script_writer import DEFAULT_MAX_TOOL_STEPS as SW_MAX_STEPS
from src.agents.character_designer import DEFAULT_MAX_TOOL_STEPS as CD_MAX_STEPS


_WINDOW_NUDGE_MARKER = "【系统提示·执行窗口】"
_FINAL_TEXT_COMPLETE = "当前状态已确认，无需进一步操作。"
_FINAL_TEXT_STOP = "我确认在重复相同操作且无新进展，停止并说明卡点。"


def _messages(instruction: str):
    """_run_tool_loop 直调用的最小 messages：system 占位（loop 内 _refresh_system 会
    用 _build_system(proj) 覆写）+ user instruction。"""
    return [{"role": "system", "content": "你是分镜导演。"},
            {"role": "user", "content": instruction}]


def _read_script(n_tool_turns: int, final_text: str):
    """前 n 步全部 shot.list（只读、幂等、零 receipts），最后一条纯文本结束。"""
    return ([(None, [_tool_call("shot.list", {"scene_id": "s01"})])] * n_tool_turns
            + [(final_text, None)])


def _nudge_count(call_entry: dict) -> int:
    msgs = call_entry["messages"]
    return sum(1 for m in msgs
               if m.get("role") == "user"
               and _WINDOW_NUDGE_MARKER in (m.get("content") or ""))


# ============ A：三文件窗口常量 == 1024 ============
def test_window_constants():
    assert DIRECTOR_MAX_STEPS == 1024
    assert SW_MAX_STEPS == 1024
    assert CD_MAX_STEPS == 1024


# ============ B：窗口续期后正常完成 ============
def test_window_renewal_then_complete(tmp_path):
    """max_steps=3；3 步 shot.list 用满窗口 → 续期注入 nudge → 第 4 次调用看到
    nudge → 继续 shot.list → 纯文本正常结束。"""
    _save_proj(tmp_path)
    client = DirectorClient(_read_script(n_tool_turns=4, final_text=_FINAL_TEXT_COMPLETE))
    rt = _runtime(tmp_path, client=client)
    instruction = "看看 s01 当前的分镜情况如何？"   # 含「如何」→ chat_turn
    res = _run(director_loop(rt, client, "demo", _messages(instruction),
                            instruction, None, max_steps=3))
    assert len(client.calls) == 5
    # 第 4 次调用（index 3）恰好一条 self-check nudge user 消息
    assert _nudge_count(client.calls[3]) == 1
    assert res["window_renewals"] == 1
    assert res["text"] == _FINAL_TEXT_COMPLETE


# ============ C：self-check 后模型选择停止 ============
def test_window_self_check_stop(tmp_path):
    """max_steps=3；3 步 shot.list → 续期 nudge → 模型选择停止（纯文本）→ 正常返回，
    不是 _TurnStopped / 异常 / STOPPED。"""
    _save_proj(tmp_path)
    client = DirectorClient(_read_script(n_tool_turns=3, final_text=_FINAL_TEXT_STOP))
    rt = _runtime(tmp_path, client=client)
    instruction = "看看 s01 当前的分镜情况如何？"
    res = _run(director_loop(rt, client, "demo", _messages(instruction),
                             instruction, None, max_steps=3))
    assert len(client.calls) == 4
    assert _nudge_count(client.calls[3]) == 1
    assert res["window_renewals"] == 1
    # 正常返回：模型原话原样返回（非预算耗尽文案、非异常）
    assert res["text"] == _FINAL_TEXT_STOP


# ============ E：cancel 仍在步骤边界生效 ============
def test_cancel_still_works(tmp_path):
    """max_steps=3 + 长脚本；包装 client 在第 2 次调用后置位 cancel_event →
    下一轮步骤边界的 cancel 检查抛 _TurnStopped。renewable window 不破坏协作停止。"""
    _save_proj(tmp_path)
    base = DirectorClient([(None, [_tool_call("shot.list", {"scene_id": "s01"})])] * 5)
    cancel = threading.Event()

    class _CancelOnSecondCall:
        """转发 chat_completion；第 2 次返回后置位 cancel_event。"""

        def __init__(self, inner):
            self.inner = inner

        async def chat_completion(self, messages, **kw):
            res = await self.inner.chat_completion(messages, **kw)
            if len(self.inner.calls) == 2:
                cancel.set()
            return res

    client = _CancelOnSecondCall(base)
    rt = _runtime(tmp_path, client=client)
    instruction = "看看 s01 当前的分镜情况如何？"
    with pytest.raises(_TurnStopped):
        _run(director_loop(rt, client, "demo", _messages(instruction),
                           instruction, None, max_steps=3, cancel_event=cancel))


# ============ G：subagent instance 不再硬停墙钟 ============
def test_scene_agent_no_wall_clock():
    """rev3：INSTANCE_WALL_CLOCK_S 常量已删除（不再作为实例硬停）；
    INSTANCE_MAX_STEPS 改为 renewable window（默认 1024）。"""
    from src.agents.subagent import instance as instance_mod
    assert not hasattr(instance_mod, "INSTANCE_WALL_CLOCK_S")
    assert instance_mod.INSTANCE_MAX_STEPS == 1024
