"""AI-7D-R · 事实摘要兜底测试（修复真实 bug）。

复现：工具真实执行成功（删+建），但 Agent 自然语言回复谎称/误述（说"更新正文"或"没改"），
导致用户被误导、下一轮 Agent 也看不到真实结果。

修复：有真实变更时，在模型回复后追加【确定性事实摘要】（由 receipts + canonical 生成），
不信模型叙述。
"""
import asyncio
import json

from src.core.models import Project, Scene, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.script_writer import script_writer_turn, _scenes_token
from types import SimpleNamespace


def _proj():
    return Project(name="demo", style=StyleAnchor(), story_text="剧本。",
                   characters=[Character(name="LinXia", description_en="x")],
                   scenes=[Scene(scene_id="s01", summary="第一段", characters=["LinXia"], shots=[]),
                           Scene(scene_id="s02", summary="第二段", characters=["LinXia"], shots=[])],
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


class ScriptedClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def chat_completion(self, messages, **kw):
        self.calls.append(1)
        return self.script.pop(0) if self.script else {"content": "done", "tool_calls": None}

    async def chat(self, messages, **kw):
        return ""


# ============ 1. 删+建成功，模型谎称"更新了剧本正文"→ 事实摘要揭示真实操作 ============
def test_fact_summary_reveals_real_ops_when_model_mislabels(tmp_path):
    _save(tmp_path)
    rev0 = _rev(["s01", "s02"])
    rev1 = _rev(["s01"])   # 删 s02 后
    client = ScriptedClient([
        {"content": None, "tool_calls": [
            _tc("story.delete", {"scene_id": "s02", "confirmed": True}, "d1")]},
        {"content": None, "tool_calls": [
            _tc("story.create", {"summary": "新段", "characters": ["LinXia"],
                                 "expected_scenes_revision": rev1}, "c1")]},
        # 模型谎称"更新了剧本正文"（实际是删除+创建）
        {"content": "我已更新了剧本正文。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "删第二段加新段"))

    msg = res["assistant_message"]
    # 模型原文保留
    assert "我已更新了剧本正文" in msg
    # 事实摘要追加：揭示真实操作（删除 s02、创建 s03）
    assert "实际执行记录" in msg
    assert "删除故事段 s02" in msg
    assert "创建故事段 s03" in msg
    # 当前真实状态
    assert "当前故事段共 2 个：s01, s03" in msg
    # canonical 确实变了
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01", "s03"]


# ============ 2. 删+建成功，模型谎称"没改"→ 事实摘要纠正 ============
def test_fact_summary_corrects_model_claim_no_change(tmp_path):
    _save(tmp_path)
    rev0 = _rev(["s01", "s02"])
    rev1 = _rev(["s01"])
    client = ScriptedClient([
        {"content": None, "tool_calls": [
            _tc("story.delete", {"scene_id": "s02", "confirmed": True}, "d1")]},
        {"content": None, "tool_calls": [
            _tc("story.create", {"summary": "新段", "characters": ["LinXia"],
                                 "expected_scenes_revision": rev1}, "c1")]},
        # 模型谎称没改
        {"content": "好的，我没有做任何修改。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "删第二段加新段"))

    msg = res["assistant_message"]
    assert "实际执行记录" in msg
    assert "删除故事段 s02" in msg
    assert "创建故事段 s03" in msg
    # canonical 确实变了（模型说没改是假的）
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01", "s03"]


# ============ 3. 纯对话无变更 → 不追加事实摘要 ============
def test_no_change_no_fact_summary(tmp_path):
    _save(tmp_path)
    client = ScriptedClient([
        {"content": "你想怎么改？", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "你好"))
    assert "实际执行记录" not in res["assistant_message"]


# ============ 4. 事实摘要含当前真实数量/顺序（解决"前端/用户不知状态"）============
def test_fact_summary_includes_current_state(tmp_path):
    _save(tmp_path)
    rev0 = _rev(["s01", "s02"])
    client = ScriptedClient([
        {"content": None, "tool_calls": [
            _tc("story.create", {"summary": "第三段", "characters": ["LinXia"],
                                 "expected_scenes_revision": rev0}, "c1")]},
        {"content": "已新增。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "加一段"))
    msg = res["assistant_message"]
    assert "当前故事段共 3 个：s01, s02, s03" in msg
