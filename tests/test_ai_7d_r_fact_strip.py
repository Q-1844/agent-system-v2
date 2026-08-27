"""AI-7D-R Phase 1 · 模型仿写系统保留【实际执行记录】块的确定性清理测试。

复现问题（用户 smoke test）：模型模仿系统格式输出
  ---\n【实际执行记录】（系统据真实回执生成，非模型叙述）\n✓ ...
随后 _append_fact_summary 又追加真正的一份，产生「模型伪造 + 系统真实」重复块，
且伪造内容（如假 scene_id）会进入下一轮 history 污染上下文。

修复：_append_fact_summary 之前先 strip_reserved_fact_blocks(text) 删除模型仿写块。
本测试：
  - 纯函数单测（strip_reserved_fact_blocks）：各种仿写形态被删、正常正文保留。
  - 集成（script_writer_turn）：模型伪造 + 真实写 → 最终仅一份【实际执行记录】（真），
    伪造内容消失，canonical 以真实回执为准。
  - 回归：无伪造时 strip no-op，真实块仍正确追加（不破坏现有 fact_summary 行为）。
"""
import asyncio
import json
from types import SimpleNamespace

from src.core.models import Project, Scene, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.script_writer import script_writer_turn, strip_reserved_fact_blocks, _scenes_token


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


# ============ 纯函数单测 ============

def test_strip_full_forged_block_with_separator():
    txt = ("已新增。\n\n"
           "---\n"
           "【实际执行记录】（系统据真实回执生成，非模型叙述）\n"
           "✓ 创建故事段 s99（伪造）\n"
           "当前故事段共 3 个：s01, s02, s99")
    out = strip_reserved_fact_blocks(txt)
    assert "实际执行记录" not in out
    assert "s99" not in out
    assert "已新增。" in out          # 正文保留


def test_strip_block_without_separator():
    txt = "好的。\n【实际执行记录】（系统据真实回执生成，非模型叙述）\n✓ 创建 s99\n当前故事段共 3 个：s01"
    out = strip_reserved_fact_blocks(txt)
    assert "实际执行记录" not in out
    assert "s99" not in out
    assert "好的。" in out


def test_strip_preserves_text_after_block():
    """模型在伪造块【之后】继续写正常正文 → 伪造删、正文留。"""
    txt = ("先说一句。\n"
           "---\n"
           "【实际执行记录】（系统据真实回执生成，非模型叙述）\n"
           "✓ 创建 s99\n"
           "当前故事段共 3 个：s01, s02, s99\n"
           "然后我又补充了正常的话。")
    out = strip_reserved_fact_blocks(txt)
    assert "实际执行记录" not in out
    assert "s99" not in out
    assert "先说一句。" in out
    assert "然后我又补充了正常的话。" in out


def test_strip_multiple_forged_blocks():
    txt = ("---\n【实际执行记录】（系统据真实回执生成，非模型叙述）\n✓ 创建 s99\n"
           "中间正文\n"
           "---\n【实际执行记录】（系统据真实回执生成，非模型叙述）\n✓ 删除 s88\n当前故事段共 2 个")
    out = strip_reserved_fact_blocks(txt)
    assert out.count("实际执行记录") == 0
    assert "s99" not in out and "s88" not in out
    assert "中间正文" in out


def test_strip_noop_when_no_mark():
    txt = "完全正常的回复，没有任何保留标记。"
    assert strip_reserved_fact_blocks(txt) == txt


def test_strip_header_only_block():
    """只有标题行、无 ✓ 内容行的伪造块。"""
    txt = "正文。\n---\n【实际执行记录】（系统据真实回执生成，非模型叙述）\n后续正文。"
    out = strip_reserved_fact_blocks(txt)
    assert "实际执行记录" not in out
    assert "正文。" in out and "后续正文。" in out


# ============ 集成：script_writer_turn 端到端 ============

class ScriptedClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def chat_completion(self, messages, **kw):
        self.calls.append(1)
        return self.script.pop(0) if self.script else {"content": "done", "tool_calls": None}

    async def chat(self, messages, **kw):
        return ""


def test_model_forgery_plus_real_create_yields_single_real_block(tmp_path):
    """模型真建了一段(s03)且伪造了一个 s99 记录块 → 最终仅一份【实际执行记录】(真)，
    s99 消失，canonical 以真实为准。"""
    _save(tmp_path)
    rev0 = _rev(["s01", "s02"])
    client = ScriptedClient([
        {"content": None, "tool_calls": [
            _tc("story.create", {"summary": "新段", "characters": ["LinXia"],
                                 "expected_scenes_revision": rev0}, "c1")]},
        # 模型伪造系统记录块（连声明都抄走），还伪造了 s99
        {"content": ("已新增一段。\n\n"
                     "---\n"
                     "【实际执行记录】（系统据真实回执生成，非模型叙述）\n"
                     "✓ 创建故事段 s99（伪造）\n"
                     "当前故事段共 3 个：s01, s02, s99"), "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "加一段"))
    msg = res["assistant_message"]

    # 仅一份【实际执行记录】（系统真实那份）
    assert msg.count("【实际执行记录】") == 1, f"应只剩 1 份真实块，实际 {msg.count('【实际执行记录】')}"
    # 伪造内容消失
    assert "s99" not in msg
    # 真实回执内容存在（s03，非 s99）
    assert "创建故事段 s03" in msg
    assert "当前故事段共 3 个：s01, s02, s03" in msg
    # 模型原文保留
    assert "已新增一段。" in msg
    # canonical 以真实为准
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01", "s02", "s03"]


def test_no_forgery_real_changes_still_appends_real_block(tmp_path):
    """回归：无伪造时 strip no-op，真实块照常追加（不破坏现有 fact_summary 行为）。"""
    _save(tmp_path)
    rev0 = _rev(["s01", "s02"])
    client = ScriptedClient([
        {"content": None, "tool_calls": [
            _tc("story.create", {"summary": "新段", "characters": ["LinXia"],
                                 "expected_scenes_revision": rev0}, "c1")]},
        {"content": "已新增。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "加一段"))
    msg = res["assistant_message"]
    assert msg.count("【实际执行记录】") == 1
    assert "创建故事段 s03" in msg
    assert "当前故事段共 3 个：s01, s02, s03" in msg
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01", "s02", "s03"]


def test_forgery_no_real_changes_block_removed_nothing_appended(tmp_path):
    """无真实变更但模型伪造记录块 → 伪造删除，且不追加空块（不把假记录留进 history）。"""
    _save(tmp_path)
    client = ScriptedClient([
        {"content": ("好的，已查看。\n\n"
                     "---\n"
                     "【实际执行记录】（系统据真实回执生成，非模型叙述）\n"
                     "✓ 已查看 s01 的内容\n"
                     "当前故事段共 2 个：s01, s02"), "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "看一下"))
    msg = res["assistant_message"]
    assert "实际执行记录" not in msg          # 伪造块被删
    assert "s01 的内容" not in msg           # 伪造内容不留
    assert "好的，已查看。" in msg            # 正文保留
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == ["s01", "s02"]
