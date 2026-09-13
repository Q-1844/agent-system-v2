"""AI-7D-R · Script Writer 最小 agent 循环测试（隔离，一个工具）。

不联网（FakeClient）。验证 §31 最小闭环：
  - LLM 返回工具调用 JSON → edit_story_text 真实原子写 canonical + 审计 + 工作记忆 + 诚实回复。
  - LLM 返回自然语言 → 对话回复，不落盘。
  - 空新正文 → 失败不写。
  - 权限：非 script_writer 会话拒绝。
  - 历史/上下文注入。
"""
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.script_writer_min import (
    script_writer_turn, _parse_tool_call, ToolCallParseError)
from src.agents.revision_audit import DirectEditAuditStore


def _proj():
    return Project(
        name="demo", style=StyleAnchor(), story_text="小雨走进雨里。",
        characters=[Character(name="LinXia", description_en="a young woman")],
        scenes=[Scene(scene_id="s01", summary="rain", characters=["LinXia"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="look up at rain", characters=["LinXia"],
                                  target_duration_s=5)])])


def _save_proj(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    return tmp_path


def _runtime(tmp_path, client=None, **kw):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client, **kw)

def _raise_unconfigured():
    """hermetic unconfigured（镜像生产 get_agnes_client 的 503 形态）：not_configured
    契约测试显式构造未配置环境，不依赖运行机 credential（Freeze Stabilization §5）。"""
    from fastapi import HTTPException
    raise HTTPException(503, "agnes not configured (hermetic test)")



class FakeClient:
    def __init__(self, reply):
        self._reply = reply
        self.calls = 0

    async def chat(self, messages, **kw):
        self.calls += 1
        return self._reply


def _run(coro):
    return asyncio.run(coro)


# ============ 工具调用解析 ============
def test_parse_tool_call_basic():
    tc = _parse_tool_call('{"tool":"edit_story_text","new_story_text":"新剧本。"}')
    assert tc and tc["tool"] == "edit_story_text" and tc["new_story_text"] == "新剧本。"


def test_parse_tool_call_in_code_fence():
    raw = '```json\n{"tool":"edit_story_text","new_story_text":" fenced "}\n```'
    tc = _parse_tool_call(raw)
    assert tc and tc["new_story_text"] == " fenced "


def test_parse_tool_call_none_for_prose():
    assert _parse_tool_call("好的，我来帮你看看。") is None
    assert _parse_tool_call("") is None


# ============ 工具调用 → 真实落盘 + 审计 + 工作记忆 ============
def test_tool_call_edits_story_text(tmp_path):
    _save_proj(tmp_path)
    reply = json.dumps({"tool": "edit_story_text",
                        "new_story_text": "小雨走进雨里。她抬起头，望着灰蒙蒙的天，忽然想起多年前的那个午后。"},
                       ensure_ascii=False)
    rt = _runtime(tmp_path, client=FakeClient(reply))
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把剧本延长到600字，直接改就行"))
    assert res["edited"] is True
    assert res["new_story_text"].startswith("小雨走进雨里。她抬起头")
    assert "已更新剧本正文" in res["assistant_message"]
    # canonical 真的改了
    proj = ProjectStore(tmp_path).load()
    assert proj.story_text == res["new_story_text"]
    # 审计有记录
    audit = DirectEditAuditStore(tmp_path, "demo").recent()
    assert audit and audit[-1]["before"] == "小雨走进雨里。"
    assert audit[-1]["after"] == res["new_story_text"]
    # 工作记忆有记录
    wm = rt.work_memory("demo").recent()
    assert any("剧本正文" in e["summary"] for e in wm)
    # 消息落盘（user + assistant）
    sess = rt.get_session("demo", sid)
    assert any(m["role"] == "user" for m in sess["messages"])
    assert any(m["role"] == "assistant" and "已更新" in m["content"] for m in sess["messages"])


# ============ 对话回复 → 不落盘 ============
def test_conversational_reply_no_mutation(tmp_path):
    _save_proj(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()
    rt = _runtime(tmp_path, client=FakeClient("好的，你想怎么改？"))
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "你能帮我改剧本吗"))
    assert res["edited"] is False
    assert res["assistant_message"] == "好的，你想怎么改？"
    assert (tmp_path / "output" / "project.json").read_bytes() == before   # 0 mutation


# ============ 空新正文 → 不视为工具调用，不写 ============
# _parse_tool_call 对空 new_story_text 返回 None（不构成有效工具调用），回退为对话回复；
# canonical 不被改。安全：绝不会用空字符串覆盖剧本。
def test_empty_new_text_no_mutation(tmp_path):
    _save_proj(tmp_path)
    before = ProjectStore(tmp_path).load().story_text
    rt = _runtime(tmp_path, client=FakeClient('{"tool":"edit_story_text","new_story_text":""}'))
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "改剧本"))
    assert res["edited"] is False
    assert ProjectStore(tmp_path).load().story_text == before   # 未改



# ============ 权限：非 script_writer 拒绝 ============
def test_non_script_writer_rejected(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient("ok"))
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    try:
        _run(script_writer_turn(rt, "demo", sid, "改剧本"))
        assert False, "应拒绝"
    except AgentError as e:
        assert e.code == "permission_denied"


# ============ 未配置 Agnes → not_configured，不崩 ============
def test_not_configured_soft_fail(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=None,
                  agnes_getter=_raise_unconfigured,
                  sensenova_getter=lambda: None)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "改剧本"))
    assert res["edited"] is False
    assert "未配置" in res["assistant_message"]


# ============ 输出截断检测：不能把半截工具调用当对话静默显示 ============
def test_truncated_tool_call_raises_not_silent(tmp_path):
    # JSON 被截断（正文没结尾、引号没闭合）
    truncated = '{"tool":"edit_story_text","new_story_text":"半个剧本，没有写完'
    try:
        _parse_tool_call(truncated)
        assert False, "应抛 ToolCallParseError"
    except ToolCallParseError:
        pass


def test_truncated_body_without_ending_punctuation_raises(tmp_path):
    # JSON 恰好闭合，但正文很长（>500字）却戛然而止无结束标点——典型 max_tokens 截断
    import json as _json
    body = "她走在雨里，" * 90   # 540 字、无句号
    assert len(body) > 500
    raw = _json.dumps({"tool": "edit_story_text", "new_story_text": body}, ensure_ascii=False)
    try:
        _parse_tool_call(raw)
        assert False, "应检测到长正文被截断"
    except ToolCallParseError:
        pass


def test_turn_does_not_write_when_truncated(tmp_path):
    """端到端：LLM 返回截断的工具调用时，不能落盘，要明确报错。"""
    _save_proj(tmp_path)
    truncated = '{"tool":"edit_story_text","new_story_text":"写了一半就断了'
    rt = _runtime(tmp_path, client=FakeClient(truncated))
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    before = (tmp_path / "output" / "project.json").read_bytes()
    res = _run(script_writer_turn(rt, "demo", sid, "重写长剧本"))
    assert res["edited"] is False
    assert "截断" in res["assistant_message"] or "没有写入" in res["assistant_message"]
    # 磁盘未变
    assert (tmp_path / "output" / "project.json").read_bytes() == before


def test_normal_tool_call_with_ending_punctuation_ok(tmp_path):
    """正常以句号结尾的正文应被接受（回归）。"""
    import json as _json
    raw = _json.dumps({"tool": "edit_story_text",
                       "new_story_text": "这是一个完整的新剧本。"}, ensure_ascii=False)
    obj = _parse_tool_call(raw)
    assert obj and obj["tool"] == "edit_story_text"
