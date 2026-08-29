"""AI-7D-R · Script Writer story.update 测试。

只测 story.update（修改已有故事段自身内容字段），不涉及批量拆分/自由多工具。
覆盖：单段成功 / 多字段 / 部分字段不覆盖其它 / 非法 scene_id/字段 / 空修改 no-op /
stale 0 mutation / canonical 回读验证 / 结构令牌不变 / 连续多段 read→update 链 /
执行真实性回执（含无 receipt 谎称成功被纠偏）。

不联网（ScriptedClient）。
"""
import asyncio
import json

import pytest

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.script_writer import (
    script_writer_turn, _scene_token, _scenes_token, SCRIPT_WRITER_TOOLS,
)
from src.agents.revision_audit import DirectEditAuditStore


def _multi_proj():
    def _sc(sid, summ, chars=None):
        return Scene(scene_id=sid, title="", location="", time_of_day="",
                     summary=summ, characters=chars or ["LinXia"], shots=[])
    return Project(
        name="demo", style=StyleAnchor(), story_text="完整剧本正文。",
        characters=[Character(name="LinXia", description_en="x"),
                    Character(name="YuShu", description_en="y")],
        scenes=[_sc("s01", "第一段"), _sc("s02", "第二段"),
                _sc("s03", "第三段"), _sc("s04", "第四段"),
                _sc("s05", "第五段")],
        next_scene_seq=6)


def _save(tmp_path):
    ProjectStore(tmp_path).save(_multi_proj())
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


def _read_scene_rev(rt, scene_id):
    """模拟 Agent：story.read 拿该段内容指纹。"""
    from src.agents.script_writer import _execute_story_read
    obs = _execute_story_read(rt, "demo", json.dumps({"scene_id": scene_id}), "u", "m")
    return obs["revision"]


class ScriptedClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def chat_completion(self, messages, **kw):
        self.calls.append({"messages": messages, "kw": kw})
        if not self.script:
            return {"content": "（脚本耗尽）", "tool_calls": None}
        content, tool_calls = self.script.pop(0)
        return {"content": content, "tool_calls": tool_calls}

    async def chat(self, messages, **kw):
        return self.script and self.script.pop(0)[0] or ""


# ============ 1. 单段单字段修改成功 + canonical 回读 + audit + 段令牌变化 ============
def test_update_single_summary_success(tmp_path):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    rev = _read_scene_rev(rt, "s02")
    new_summary = "第二段改写后的全新故事文本。"

    from src.agents.script_writer import _execute_story_update
    obs = _execute_story_update(
        rt, "demo",
        json.dumps({"scene_id": "s02", "expected_revision": rev,
                    "summary": new_summary}), "u", "m")

    assert obs["ok"] is True and obs["verified"] is True
    assert obs["scene_id"] == "s02"
    assert obs["changed"] == ["summary"]
    assert obs["before"]["summary"] == "第二段"
    assert obs["after"]["summary"] == new_summary
    assert obs["revision"] != rev   # 内容指纹变了
    # canonical 真的落盘
    proj = ProjectStore(tmp_path).load()
    s2 = next(s for s in proj.scenes if s.scene_id == "s02")
    assert s2.summary == new_summary
    # 别的字段没动
    assert s2.characters == ["LinXia"] and s2.title == ""
    # 审计记录
    audit = DirectEditAuditStore(tmp_path, "demo").recent()
    assert audit and audit[-1]["field"] == "scene"
    assert audit[-1]["before"]["scene_id"] == "s02"
    assert audit[-1]["after"]["summary"] == new_summary


# ============ 2. 多字段一起改（title/location/time_of_day/characters）============
def test_update_multiple_fields(tmp_path):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    rev = _read_scene_rev(rt, "s03")
    from src.agents.script_writer import _execute_story_update
    obs = _execute_story_update(
        rt, "demo",
        json.dumps({"scene_id": "s03", "expected_revision": rev,
                    "title": "雨夜对峙", "location": "老城钟楼",
                    "time_of_day": "夜", "characters": ["LinXia", "YuShu"]}),
        "u", "m")
    assert obs["ok"] is True
    assert set(obs["changed"]) == {"title", "location", "time_of_day", "characters"}
    s = next(s for s in ProjectStore(tmp_path).load().scenes if s.scene_id == "s03")
    assert s.title == "雨夜对峙" and s.location == "老城钟楼"
    assert s.time_of_day == "夜" and s.characters == ["LinXia", "YuShu"]
    assert s.summary == "第三段"   # 没传 summary，不动


# ============ 3. 部分字段修改不覆盖其它字段（characters 整体替换为预期语义）============
def test_partial_update_does_not_clobber(tmp_path):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    rev = _read_scene_rev(rt, "s01")
    from src.agents.script_writer import _execute_story_update
    # 只改 location
    obs = _execute_story_update(
        rt, "demo",
        json.dumps({"scene_id": "s01", "expected_revision": rev,
                    "location": "海边悬崖"}), "u", "m")
    assert obs["ok"] is True
    s = next(s for s in ProjectStore(tmp_path).load().scenes if s.scene_id == "s01")
    assert s.location == "海边悬崖"
    assert s.summary == "第一段"          # 未动
    assert s.characters == ["LinXia"]     # 未动


# ============ 4a. 非法 scene_id → not_found（不写盘）============
def test_update_nonexistent_scene(tmp_path):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    from src.agents.script_writer import _execute_story_update
    before = (tmp_path / "output" / "project.json").read_bytes()
    with pytest.raises(AgentError) as ei:
        _execute_story_update(
            rt, "demo",
            json.dumps({"scene_id": "s99", "expected_revision": "deadbeef",
                        "summary": "x"}), "u", "m")
    assert ei.value.code == "not_found"
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert not DirectEditAuditStore(tmp_path, "demo").recent()


# ============ 4b. 非法字段（scene_id 改写 / shots / story_text）→ bad_arguments ============
@pytest.mark.parametrize("payload", [
    {"scene_id": "s01", "expected_revision": "x", "shots": []},
    {"scene_id": "s01", "expected_revision": "x", "story_text": "hack"},
    {"scene_id": "s01", "expected_revision": "x", "new_scene_id": "s99"},
])
def test_update_illegal_field_rejected(tmp_path, payload):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    rev = _read_scene_rev(rt, payload["scene_id"])
    payload = {**payload, "expected_revision": rev}
    from src.agents.script_writer import _execute_story_update
    with pytest.raises(AgentError) as ei:
        _execute_story_update(rt, "demo", json.dumps(payload), "u", "m")
    assert ei.value.code == "bad_arguments"
    # 未写盘
    s = next(s for s in ProjectStore(tmp_path).load().scenes
             if s.scene_id == payload["scene_id"])
    assert s.summary != "hack"


# ============ 4c. characters 非数组 → bad_arguments ============
def test_update_characters_must_be_list(tmp_path):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    rev = _read_scene_rev(rt, "s01")
    from src.agents.script_writer import _execute_story_update
    with pytest.raises(AgentError) as ei:
        _execute_story_update(
            rt, "demo",
            json.dumps({"scene_id": "s01", "expected_revision": rev,
                        "characters": "LinXia"}), "u", "m")
    assert ei.value.code == "bad_arguments"


# ============ 4d. 缺 expected_revision → bad_arguments ============
def test_update_missing_revision_rejected(tmp_path):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    from src.agents.script_writer import _execute_story_update
    with pytest.raises(AgentError) as ei:
        _execute_story_update(
            rt, "demo", json.dumps({"scene_id": "s01", "summary": "x"}), "u", "m")
    assert ei.value.code == "bad_arguments"


# ============ 5a. 空修改（值与现值完全一致）→ no-op，ok=true/unchanged，不写盘不审计 ============
def test_update_noop_same_values(tmp_path):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    rev = _read_scene_rev(rt, "s02")
    before = (tmp_path / "output" / "project.json").read_bytes()
    from src.agents.script_writer import _execute_story_update
    obs = _execute_story_update(
        rt, "demo",
        json.dumps({"scene_id": "s02", "expected_revision": rev,
                    "summary": "第二段", "characters": ["LinXia"]}), "u", "m")
    assert obs["ok"] is True and obs["unchanged"] is True
    assert obs["changed"] == []
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert not DirectEditAuditStore(tmp_path, "demo").recent()


# ============ 5b. 空修改（没给任何内容字段）→ no-op ============
def test_update_noop_no_fields(tmp_path):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    rev = _read_scene_rev(rt, "s02")
    from src.agents.script_writer import _execute_story_update
    obs = _execute_story_update(
        rt, "demo",
        json.dumps({"scene_id": "s02", "expected_revision": rev}), "u", "m")
    assert obs["ok"] is True and obs["unchanged"] is True
    assert not DirectEditAuditStore(tmp_path, "demo").recent()


# ============ 6. stale 冲突 → 0 mutation，返回最新内容+指纹 ============
def test_update_stale_rejects_and_returns_latest(tmp_path):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    from src.agents.script_writer import _execute_story_update
    before = (tmp_path / "output" / "project.json").read_bytes()
    obs = _execute_story_update(
        rt, "demo",
        json.dumps({"scene_id": "s02", "expected_revision": "deadbeef",
                    "summary": "基于过期版本的修改"}), "u", "m")
    assert obs["ok"] is False and obs["stale"] is True
    assert obs["verified"] is False
    assert obs["error"] == "stale_revision"
    assert obs["current_revision"] == _read_scene_rev(rt, "s02")
    assert obs["current_scene"]["summary"] == "第二段"
    # canonical 未变
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert not DirectEditAuditStore(tmp_path, "demo").recent()


# ============ 7. 段内内容修改【不】改变 scenes 结构令牌（增/删/移动才变）============
def test_content_update_does_not_change_structure_token(tmp_path):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    struct_before = _scenes_token(ProjectStore(tmp_path).load().scenes)
    rev = _read_scene_rev(rt, "s02")
    from src.agents.script_writer import _execute_story_update
    _execute_story_update(
        rt, "demo",
        json.dumps({"scene_id": "s02", "expected_revision": rev,
                    "summary": "改了内容但不增删段。"}), "u", "m")
    struct_after = _scenes_token(ProjectStore(tmp_path).load().scenes)
    assert struct_before == struct_after   # 段集合结构未变
    # 段的数量/身份/顺序不变
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == \
        ["s01", "s02", "s03", "s04", "s05"]


# ============ 8. 写盘失败（save no-op）→ verify_failed，ok=false ============
def test_update_save_not_persisted_verify_failed(tmp_path, monkeypatch):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    rev = _read_scene_rev(rt, "s02")
    monkeypatch.setattr(ProjectStore, "save", lambda self, project: None)
    from src.agents.script_writer import _execute_story_update
    obs = _execute_story_update(
        rt, "demo",
        json.dumps({"scene_id": "s02", "expected_revision": rev,
                    "summary": "应该落盘但没落盘"}), "u", "m")
    assert obs["ok"] is False and obs["error"] == "verify_failed"
    assert obs["verified"] is False
    # canonical 没有被改
    assert next(s for s in ProjectStore(tmp_path).load().scenes
                if s.scene_id == "s02").summary == "第二段"


# ============ 9. tool-loop 连续修改多个故事段：read s02→update s02→read s05→update s05 ============
def test_turn_consecutive_multi_scene_updates(tmp_path):
    _save(tmp_path)
    rt = _runtime(tmp_path, None)
    # 先算出初始段指纹（模拟 read 返回）
    rev02 = _read_scene_rev(_runtime(tmp_path, None), "s02")
    rev05 = _read_scene_rev(_runtime(tmp_path, None), "s05")
    new02 = "s02 连续修改测试——第二段新内容。"
    new05 = "s05 连续修改测试——第五段新内容。"

    client = ScriptedClient([
        (None, [_tc("story.read", {"scene_id": "s02"}, "r1")]),
        (None, [_tc("story.update",
                    {"scene_id": "s02", "expected_revision": rev02,
                     "summary": new02}, "u1")]),
        (None, [_tc("story.read", {"scene_id": "s05"}, "r2")]),
        (None, [_tc("story.update",
                    {"scene_id": "s05", "expected_revision": rev05,
                     "summary": new05}, "u2")]),
        ("我已经更新了 s02 和 s05 两个故事段。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把第二段和第五段的内容分别更新"))

    assert res["scene_updated"] is True
    assert res["intent"] == "story_update"
    proj = ProjectStore(tmp_path).load()
    assert next(s for s in proj.scenes if s.scene_id == "s02").summary == new02
    assert next(s for s in proj.scenes if s.scene_id == "s05").summary == new05
    # 其它段未动
    assert next(s for s in proj.scenes if s.scene_id == "s03").summary == "第三段"
    # 两次成功回执
    ok = [r for r in res["receipts"] if r["operation"] == "story.update" and r["ok"]]
    assert len(ok) == 2
    # 真实成功 → 真相闸门不触发（最终文本保留，无纠偏往返的多余调用）
    assert "我已经更新了 s02 和 s05 两个故事段。" in res["assistant_message"]
    # 5 次 LLM 调用：r1/u1/r2/u2/final
    assert len(client.calls) == 5


# ============ 10. 执行真实性：无成功 receipt 却谎称「已修改」→ Runtime 纠偏/兜底 ============
def test_turn_false_claim_without_update_gets_fallback(tmp_path):
    _save(tmp_path)
    client = ScriptedClient([
        # 直接谎称已修改，没有任何 tool_call
        ("我已经把 s02 的内容改好了。", None),
        # zero-write recovery（明确修改请求但零写尝试）后仍嘴硬
        ("是的，s02 已经修改完成。", None),
        # 既有真相闸门纠偏后还嘴硬 → 诚实兜底
        ("s02 确实已经修改完成。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把第二段改一下"))

    assert res["scene_updated"] is False
    # canonical 未变
    assert next(s for s in ProjectStore(tmp_path).load().scenes
                if s.scene_id == "s02").summary == "第二段"
    # 最终文本是诚实兜底，不是谎言
    assert "改好了" not in res["assistant_message"]
    assert ("没有真正执行修改" in res["assistant_message"]
            or "没有发出对应的工具调用" in res["assistant_message"])


# ============ 11. 执行真实性：stale 后谎称成功 → 兜底说明未生效 ============
def test_turn_stale_then_false_claim_gets_fallback(tmp_path):
    _save(tmp_path)
    client = ScriptedClient([
        # 用过期指纹 → stale，未写入
        (None, [_tc("story.update",
                    {"scene_id": "s02", "expected_revision": "deadbeef",
                     "summary": "过期修改"}, "u1")]),
        ("s02 已经修改完成了。", None),
        ("修改完成。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "改第二段"))

    assert res["scene_updated"] is False
    assert next(s for s in ProjectStore(tmp_path).load().scenes
                if s.scene_id == "s02").summary == "第二段"
    r0 = res["receipts"][0]
    assert r0["operation"] == "story.update" and r0["stale"] is True
    assert "没有真正生效" in res["assistant_message"]


# ============ 12. 回执统一结构（operation/target/before/after/revision/verified/ok）============
def test_receipt_unified_shape(tmp_path):
    rt = _runtime(_save(tmp_path), ScriptedClient([]))
    rev = _read_scene_rev(rt, "s04")
    from src.agents.script_writer import _execute_story_update
    obs = _execute_story_update(
        rt, "demo",
        json.dumps({"scene_id": "s04", "expected_revision": rev,
                    "summary": "第四段更新。"}), "u", "m")
    for key in ("ok", "operation", "target", "before", "after",
                "revision", "verified", "error"):
        assert key in obs
    assert obs["operation"] == "story.update"
    assert obs["target"] == "scene:s04"
    assert obs["ok"] is True and obs["verified"] is True
