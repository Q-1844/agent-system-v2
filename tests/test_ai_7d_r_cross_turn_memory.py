"""AI-7D-R · 跨轮记忆连续性验证（不改 agent 行为，只锁定结构性属性）。

用户要求保证：下一轮 agent 对话能看到上一轮真实执行的工具调用记录，
且记忆「干净但完善」——不把原始 tool 消息 / tool_calls 灌进 history（避免
长上下文污染），但上一轮的【实际执行记录】(操作+结果+当前状态) 必须随
assistant content 跨轮可见。

本测试锁定三件事，任一被未来改动破坏即报红：
  1. 第1轮真实写变更 → 持久化的 assistant content 含【实际执行记录】(✓操作+当前状态)。
  2. 第2轮发给模型的 messages 的 history 里，含上一轮 assistant 的执行记录
     → 下一轮 agent 看得到上轮做了什么（跨轮记忆未断）。
  3. history 干净：不含 role="tool" 的原始工具结果消息，history 的 assistant
     消息也不带 tool_calls 字段——只有最终 content（清亮，不污染）。
"""
import asyncio
import json
from types import SimpleNamespace

from src.core.models import Project, Scene, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.script_writer import script_writer_turn, _scenes_token


def _proj():
    return Project(name="demo", style=StyleAnchor(), story_text="剧本。",
                   characters=[Character(name="LinXia", description_en="x")],
                   scenes=[Scene(scene_id="s01", summary="第一段",
                                 characters=["LinXia"], shots=[]),
                           Scene(scene_id="s02", summary="第二段",
                                 characters=["LinXia"], shots=[])],
                   next_scene_seq=3)


def _save(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    return tmp_path


def _runtime(tmp_path, client):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client)


def _run(coro):
    return asyncio.run(coro)


def _tc(name, arguments, call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(arguments, ensure_ascii=False)}}


def _rev(ids):
    return _scenes_token([SimpleNamespace(scene_id=i) for i in ids])


class CapturingClient:
    """记录每次 chat_completion 收到的 messages（跨轮验证用）。"""
    def __init__(self, script):
        self.script = list(script)
        self.captured = []

    async def chat_completion(self, messages, **kw):
        # 浅拷贝每条 dict 快照，避免后续原地修改影响断言
        self.captured.append([{**m} for m in messages])
        return self.script.pop(0) if self.script else {"content": "好的。", "tool_calls": None}

    async def chat(self, messages, **kw):
        return ""


def test_cross_turn_memory_carries_execution_record(tmp_path):
    """第1轮删+建（模型谎称没改→事实摘要纠正），第2轮收到的 history 必含上轮执行记录。"""
    _save(tmp_path)
    rev0 = _rev(["s01", "s02"])
    rev1 = _rev(["s01"])   # 删 s02 后的结构指纹
    client = CapturingClient([
        # 第1轮 step1: 删 s02
        {"content": None, "tool_calls": [
            _tc("story.delete", {"scene_id": "s02", "confirmed": True}, "d1")]},
        # 第1轮 step2: 建 s03（用删后的 revision）
        {"content": None, "tool_calls": [
            _tc("story.create", {"summary": "新段", "characters": ["LinXia"],
                                 "expected_scenes_revision": rev1}, "c1")]},
        # 第1轮 step3: 模型谎称"没改" → 触发事实摘要追加（_claims_success 不命中否定句，无纠偏轮）
        {"content": "好的，我没有做任何修改。", "tool_calls": None},
        # 第2轮: 只回一句（不含完成态动词，不触发真相闸门纠偏）
        {"content": "好的，我了解了。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]

    # —— 第1轮 ——
    r1 = _run(script_writer_turn(rt, "demo", sid, "删第二段加新段"))
    msg1 = r1["assistant_message"]
    # (1) 持久化的 assistant content 含执行记录：操作 + 当前状态
    assert "实际执行记录" in msg1, "第1轮无事实摘要——执行记录未落进 content"
    assert "删除故事段 s02" in msg1 and "创建故事段 s03" in msg1
    assert "当前故事段共 2 个：s01, s03" in msg1, "事实摘要缺当前真实状态"
    # canonical 确实变了
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01", "s03"]

    # —— 第2轮 ——
    _run(script_writer_turn(rt, "demo", sid, "接着上轮继续"))
    # 第2轮唯一一次 chat_completion 收到的 messages = [system, history..., user2]
    last_msgs = client.captured[-1]
    asst_history = [m for m in last_msgs if m["role"] == "assistant"]

    # (2) 跨轮可见：下一轮 history 里含上一轮 assistant 的执行记录
    assert any("实际执行记录" in (m.get("content") or "") for m in asst_history), \
        "下一轮 agent 看不到上一轮执行记录——跨轮记忆断了"
    assert any("删除故事段 s02" in (m.get("content") or "") for m in asst_history)
    assert any("当前故事段共" in (m.get("content") or "") for m in asst_history), \
        "下一轮看不到上轮的当前状态快照"

    # (3) 干净：history 不灌原始 tool 消息，也不灌 assistant 的 tool_calls
    assert not any(m["role"] == "tool" for m in last_msgs), \
        "history 不应含原始 tool 结果消息（会污染长上下文）"
    assert not any("tool_calls" in m for m in asst_history), \
        "history 的 assistant 消息不应带 tool_calls 字段（只该有最终 content）"
