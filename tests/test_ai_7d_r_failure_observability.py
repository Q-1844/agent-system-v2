"""AI-7D-R 失败可观测性专项测试。

背景（2026-08-28 用户手测 10 段 x300 字任务）：turn 中途异常后
  assistant 持久化只有 "[执行失败: ]"（str(e) 为空的异常，如 TimeoutError），
  且 trace/receipts/events 全被清空 -> 无法事后诊断跑到第几步、哪些写已真实落盘。

修复（script_writer.py）：
  1. except 记 log.exception（完整 traceback 进日志）；
  2. 失败文本带异常类型名：[执行失败: {type}: {str}]，str 空也不至于全空；
  3. _run_tool_loop 的 events/receipts/trace 走共享 state 容器，
     异常时 script_writer_turn 保留已执行的部分（写已落盘可见），不再清空。

不联网（ScriptedClient 抛异常复现）。核心决策语义（OCC/串行/真相闸门）零改动。
"""
import asyncio
import json

import pytest

from src.core.models import Project, Scene, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.script_writer import script_writer_turn, _scenes_token


def _proj():
    def _sc(sid, summ):
        return Scene(scene_id=sid, summary=summ, characters=["LinXia"], shots=[])
    return Project(
        name="demo", style=StyleAnchor(), story_text="完整剧本正文。",
        characters=[Character(name="LinXia", description_en="x")],
        scenes=[_sc("s01", "第一段"), _sc("s02", "第二段"), _sc("s03", "第三段")],
        next_scene_seq=4)


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
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}


class ExplodingClient:
    """第 1 次正常返回一个写 tool_call；第 2 次抛指定异常（复现网络断/服务端错误）。"""

    def __init__(self, exc, first_tool_call):
        self.exc = exc
        self.first_tool_call = first_tool_call
        self.calls = 0

    async def chat_completion(self, messages, **kw):
        self.calls += 1
        if self.calls == 1:
            return {"content": None, "tool_calls": self.first_tool_call}
        raise self.exc


def _mk_client(tmp_path, exc):
    rev = _scenes_token(ProjectStore(tmp_path).load().scenes)
    return ExplodingClient(exc, [_tc(
        "story.update",
        {"scene_id": "s01", "expected_revision": None, "summary": "中途已落盘的修改"},
        # expected_revision 会被下面按真实 revision 填；先占位
    )]), rev


# ============ F1. 空描述异常（str(e)==""）：类型名仍可见 + partial 保留 ============
def test_failure_empty_str_exception_keeps_type_and_partial(tmp_path):
    _save(tmp_path)
    # 用真实段 revision 让第一步写真实落盘
    from src.agents.script_writer import _scene_token
    s01 = next(s for s in ProjectStore(tmp_path).load().scenes if s.scene_id == "s01")
    rev = _scene_token(s01)
    client = ExplodingClient(
        Exception(),   # 空描述：str(e) == ""
        [_tc("story.update", {"scene_id": "s01", "expected_revision": rev,
                              "summary": "中途已落盘的修改"})])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把第一段改一下"))

    # turn 失败，但失败文本带异常类型名（不再是无信息的空括号）
    assert res["message"]["status"] == "failed"
    assert "Exception" in res["message"]["content"]
    assert "执行失败" in res["message"]["content"]
    # partial 保留：第一步写工具的真实回执与轨迹都在
    assert res["trace"] and res["trace"][0]["tool"] == "story.update"
    assert res["receipts"] and res["receipts"][0]["ok"] is True
    assert res["receipts"][0]["verified"] is True
    # canonical 真的写了（中途那次写已落盘，用户可据此知道部分完成）
    s01_after = next(s for s in ProjectStore(tmp_path).load().scenes
                     if s.scene_id == "s01")
    assert s01_after.summary == "中途已落盘的修改"


# ============ F2. TimeoutError（str 空）同理：类型名可见 ============
def test_failure_timeout_error_named_in_text(tmp_path):
    _save(tmp_path)
    client = ExplodingClient(TimeoutError(), None)
    client.first_tool_call = None
    rt = _runtime(tmp_path, client)

    class _FirstCallOnly(ExplodingClient):
        async def chat_completion(self, messages, **kw):
            raise TimeoutError()   # 第一次 LLM 调用就超时

    rt2 = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                       settings_path=tmp_path / "agents_data" / "settings.json",
                       client=_FirstCallOnly(TimeoutError(), None))
    sid = rt2.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt2, "demo", sid, "继续"))

    assert res["message"]["status"] == "failed"
    assert "TimeoutError" in res["message"]["content"]
    # 第一步就失败：无任何写（canonical 未动、receipts 空）
    assert res["receipts"] == []
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == \
        ["s01", "s02", "s03"]


# ============ F3. 失败后日志确实记录（log.exception 被调用） ============
def test_failure_logs_traceback(tmp_path, caplog):
    import logging
    _save(tmp_path)

    class _Boom:
        async def chat_completion(self, messages, **kw):
            raise RuntimeError("network gone")

    rt = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                      settings_path=tmp_path / "agents_data" / "settings.json",
                      client=_Boom())
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    with caplog.at_level(logging.ERROR, logger="agents.script_writer"):
        _run(script_writer_turn(rt, "demo", sid, "继续"))
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "工具循环异常" in joined
    assert any(r.exc_info for r in caplog.records if r.name == "agents.script_writer")


# ============ F4. meta 同样带 partial（持久化 message.meta 可事后诊断） ============
def test_failure_meta_carries_partial_receipts(tmp_path):
    _save(tmp_path)
    from src.agents.script_writer import _scene_token
    s01 = next(s for s in ProjectStore(tmp_path).load().scenes if s.scene_id == "s01")
    rev = _scene_token(s01)

    class _OneWriteThenBoom:
        def __init__(self):
            self.calls = 0

        async def chat_completion(self, messages, **kw):
            self.calls += 1
            if self.calls == 1:
                return {"content": None, "tool_calls": [_tc(
                    "story.update", {"scene_id": "s01", "expected_revision": rev,
                                     "summary": "写后崩溃前落盘"})]}
            raise ConnectionResetError("模拟网络中断")

    rt = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                      settings_path=tmp_path / "agents_data" / "settings.json",
                      client=_OneWriteThenBoom())
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "改第一段"))

    # ConnectionResetError 描述非空：类型名+描述都进文本
    assert "ConnectionResetError" in res["message"]["content"]
    # meta 里的 receipts 保留（持久化后下一轮/前端可见真实写回执）
    meta = res["message"]["meta"] or {}
    assert meta.get("receipts") and meta["receipts"][0]["ok"] is True
    assert meta.get("trace") and meta["trace"][0]["tool"] == "story.update"
