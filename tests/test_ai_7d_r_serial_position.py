"""AI-7D-R · 写工具强制串行 + position 测试。

思路①：写工具一个一个执行；同一步多个写工具只执行第1个，后续拒绝（要求等最新revision）。
只读工具可批量。方案A：story.list 返回 position（第几条）。
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
                           Scene(scene_id="s02", summary="第二段", characters=["LinXia"], shots=[]),
                           Scene(scene_id="s03", summary="第三段", characters=["LinXia"], shots=[])],
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


# ============ A1. story.list 返回 position（第几条）============
def test_story_list_has_position(tmp_path):
    from src.agents.script_writer import _execute_story_list
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    obs = _execute_story_list(rt, "demo", "{}", "u", "m")
    scenes = obs["scenes"]
    assert scenes[0]["position"] == 1 and scenes[0]["scene_id"] == "s01"
    assert scenes[1]["position"] == 2 and scenes[1]["scene_id"] == "s02"
    assert scenes[2]["position"] == 3 and scenes[2]["scene_id"] == "s03"


# ============ A2. 删除后 position 连续、scene_id 跳号 → Agent 靠 position 定位 ============
def test_position_continuous_after_delete(tmp_path):
    """删 s02 后：position=1→s01, position=2→s03（id 跳号但 position 连续）。"""
    from src.agents.script_writer import _execute_story_list, _execute_story_delete
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    _execute_story_delete(rt, "demo", json.dumps({"scene_id": "s02"}), "u", "m")
    obs = _execute_story_list(rt, "demo", "{}", "u", "m")
    assert [(s["position"], s["scene_id"]) for s in obs["scenes"]] == [(1, "s01"), (2, "s03")]


# ============ ①1. 同一步发多个写工具：只执行第1个，后续拒绝 ============
def test_multiple_writes_one_step_only_first_runs(tmp_path):
    """模型一次发 2 个 story.create，只执行第1个，第2个被拒绝（要求等 revision）。"""
    _save(tmp_path)
    rev0 = _rev(["s01", "s02", "s03"])
    client = ScriptedClient([
        # 一次发两个 create
        {"content": None, "tool_calls": [
            _tc("story.create", {"summary": "段A", "characters": ["LinXia"],
                                  "expected_scenes_revision": rev0}, "c1"),
            _tc("story.create", {"summary": "段B", "characters": ["LinXia"],
                                  "expected_scenes_revision": rev0}, "c2")]},
        # 模型看到第2个被拒 → 用新 revision 重发段B
        {"content": None, "tool_calls": [
            _tc("story.create", {"summary": "段B", "characters": ["LinXia"],
                                  "expected_scenes_revision": _rev(["s01", "s02", "s03", "s04"])},
                                 "c2b")]},
        {"content": "已建两段。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "建两段"))

    proj = ProjectStore(tmp_path).load()
    # 两段都建了（第2个重发成功）
    assert [s.scene_id for s in proj.scenes] == ["s01", "s02", "s03", "s04", "s05"]
    # trace 里第1步只有1个 ok create，另1个是 error（被拒）
    step0 = [t for t in res["trace"] if t.get("step") == 0]
    create_ok = [t for t in step0 if t["tool"] == "story.create" and t["result"] == "ok"]
    create_rej = [t for t in step0 if t["tool"] == "story.create" and t["result"] == "error"]
    assert len(create_ok) == 1
    assert len(create_rej) == 1
    assert "一个一个" in (create_rej[0]["error"] or "") or "已执行过" in (create_rej[0]["error"] or "")


# ============ ①2. 只读工具可批量（同一步多个 read 不被拒）============
def test_multiple_reads_one_step_all_run(tmp_path):
    _save(tmp_path)
    client = ScriptedClient([
        # 一次发 story.list + script.read（两个只读）
        {"content": None, "tool_calls": [
            _tc("story.list", {}, "l1"),
            _tc("script.read", {}, "r1")]},
        {"content": "已读取。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "读一下"))
    step0 = [t for t in res["trace"] if t.get("step") == 0]
    # 两个只读都成功执行
    assert any(t["tool"] == "story.list" and t["result"] == "ok" for t in step0)
    assert any(t["tool"] == "script.read" and t["result"] == "ok" for t in step0)


# ============ ①3. 一个写 + 多个读：写执行，读也执行（写只占写槽，读不受限）============
def test_one_write_plus_reads_all_run(tmp_path):
    _save(tmp_path)
    rev0 = _rev(["s01", "s02", "s03"])
    client = ScriptedClient([
        # 一步：1个 create + 1个 story.list（写+读混合）
        {"content": None, "tool_calls": [
            _tc("story.create", {"summary": "新段", "characters": ["LinXia"],
                                  "expected_scenes_revision": rev0}, "c1"),
            _tc("story.list", {}, "l1")]},
        {"content": "已建并核对。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "建一段然后核对"))
    step0 = [t for t in res["trace"] if t.get("step") == 0]
    assert any(t["tool"] == "story.create" and t["result"] == "ok" for t in step0)
    assert any(t["tool"] == "story.list" and t["result"] == "ok" for t in step0)
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01", "s02", "s03", "s04"]


# ============ ①4. 串行后不再出现批量 stale（效率提升）============
def test_serial_no_batch_stale(tmp_path):
    """串行后：模型被迫一个一个发，不应出现『同一步多个 stale』。"""
    _save(tmp_path)
    rev0 = _rev(["s01", "s02", "s03"])
    rev1 = _rev(["s01", "s02", "s03", "s04"])
    client = ScriptedClient([
        {"content": None, "tool_calls": [_tc("story.create",
            {"summary": "段A", "characters": ["LinXia"], "expected_scenes_revision": rev0}, "c1")]},
        {"content": None, "tool_calls": [_tc("story.create",
            {"summary": "段B", "characters": ["LinXia"], "expected_scenes_revision": rev1}, "c2")]},
        {"content": None, "tool_calls": [_tc("story.list", {})]},
        {"content": "已建两段。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "建两段"))
    # 没有 stale（串行后每个 create 用正确 revision）
    stale = [t for t in res["trace"] if t["result"] == "stale"]
    assert len(stale) == 0
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01", "s02", "s03", "s04", "s05"]
