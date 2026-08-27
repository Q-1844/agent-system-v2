"""AI-7D-R · 删除方案 B 测试（用户明确要删 → 直接删，不再两阶段预览）。

方案 B 后：story.delete(scene_id) 不传 confirmed = 直接删除；不再有「预览→问确认→confirmed=true」
两阶段，从根上消除 Agent「用文字假装预览却没发工具调用」导致确认后无法删除的问题。
事实摘要兜底呈现真实删除结果。

不联网（ScriptedClient）。
"""
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.script_writer import script_writer_turn
from src.agents.revision_audit import DirectEditAuditStore


def _proj():
    s01 = Scene(scene_id="s01", summary="第一段", characters=["LinXia"], shots=[])
    s02 = Scene(scene_id="s02", summary="第二段", characters=["LinXia"],
                shots=[Shot(shot_id="s02_c01", scene_id="s02", index=1,
                            description="镜头", characters=["LinXia"], target_duration_s=3)])
    s03 = Scene(scene_id="s03", summary="第三段", characters=["LinXia"], shots=[])
    return Project(name="demo", style=StyleAnchor(), story_text="剧本。",
                   characters=[Character(name="LinXia", description_en="x")],
                   scenes=[s01, s02, s03], next_scene_seq=4)


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


class ScriptedClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def chat_completion(self, messages, **kw):
        self.calls.append(1)
        return self.script.pop(0) if self.script else {"content": "done", "tool_calls": None}

    async def chat(self, messages, **kw):
        return ""


# ============ 1. 方案 B 核心：用户说删 → Agent 直接删（单阶段）============
def test_user_says_delete_agent_directly_deletes(tmp_path):
    _save(tmp_path)
    client = ScriptedClient([
        {"content": None, "tool_calls": [_tc("story.delete", {"scene_id": "s03"}, "d1")]},
        {"content": "已删除 s03。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "删掉第三段"))

    assert res.get("deleted") is True
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01", "s02"]
    # 事实摘要呈现真实操作
    assert "删除故事段 s03" in res["assistant_message"]


# ============ 2. 删带镜头的段 → 级联删镜头，事实摘要呈现 ============
def test_delete_cascades_shots_and_summarizes(tmp_path):
    _save(tmp_path)
    client = ScriptedClient([
        {"content": None, "tool_calls": [_tc("story.delete", {"scene_id": "s02"}, "d1")]},
        {"content": "已删除 s02。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "删掉第二段"))

    assert res.get("deleted") is True
    proj = ProjectStore(tmp_path).load()
    assert [s.scene_id for s in proj.scenes] == ["s01", "s03"]
    assert "s02_c01" not in [sh.shot_id for s in proj.scenes for sh in s.shots]
    # 事实摘要含级联镜头
    assert "删除故事段 s02" in res["assistant_message"]
    assert "当前故事段共 2 个：s01, s03" in res["assistant_message"]


# ============ 3. 删两段 → 串行：一个一个删（思路①）============
def test_delete_two_scenes_serial(tmp_path):
    """思路①：写工具串行。模型若一次发2个delete，只删第1个；第2个被拒需重发。
    模型配合重发后两段都删。"""
    _save(tmp_path)
    client = ScriptedClient([
        # 一次发2个 delete → 只执行第1个
        {"content": None, "tool_calls": [
            _tc("story.delete", {"scene_id": "s02"}, "d1"),
            _tc("story.delete", {"scene_id": "s03"}, "d2")]},
        # 第2个被拒 → 重发
        {"content": None, "tool_calls": [
            _tc("story.delete", {"scene_id": "s03"}, "d2b")]},
        {"content": "已删除 s02 和 s03。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "删掉最后两段"))

    assert res.get("deleted") is True
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01"]
    assert "删除故事段 s02" in res["assistant_message"]
    assert "删除故事段 s03" in res["assistant_message"]
    assert "当前故事段共 1 个：s01" in res["assistant_message"]


# ============ 4. 删完再建（删+增组合）→ 事实摘要呈现真实操作 ============
def test_delete_then_create_shows_both_in_summary(tmp_path):
    _save(tmp_path)
    from src.agents.script_writer import _scenes_token
    from types import SimpleNamespace
    rev0 = _scenes_token([SimpleNamespace(scene_id="s01"),
                          SimpleNamespace(scene_id="s02"),
                          SimpleNamespace(scene_id="s03")])
    rev1 = _scenes_token([SimpleNamespace(scene_id="s01"),
                          SimpleNamespace(scene_id="s03")])  # 删 s02 后
    client = ScriptedClient([
        {"content": None, "tool_calls": [_tc("story.delete", {"scene_id": "s02"}, "d1")]},
        {"content": None, "tool_calls": [_tc("story.create",
                    {"summary": "新段", "characters": ["LinXia"],
                     "expected_scenes_revision": rev1}, "c1")]},
        {"content": "已删 s02 并新增。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "删第二段加新段"))

    proj = ProjectStore(tmp_path).load()
    assert [s.scene_id for s in proj.scenes] == ["s01", "s03", "s04"]
    # 事实摘要含删除和创建
    assert "删除故事段 s02" in res["assistant_message"]
    assert "创建故事段 s04" in res["assistant_message"]


# ============ 5. 不存在的段 → not_found，事实摘要不追加（无成功变更）============
def test_delete_nonexistent_no_summary(tmp_path):
    _save(tmp_path)
    client = ScriptedClient([
        {"content": None, "tool_calls": [_tc("story.delete", {"scene_id": "s99"}, "d1")]},
        {"content": "故事段不存在。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "删掉不存在的段"))

    assert res.get("deleted") is False
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01", "s02", "s03"]
    # 失败的删除不追加事实摘要（无 verified 成功变更）
    assert "实际执行记录" not in res["assistant_message"]
