"""AI-7D-R · zero-write recovery 极窄兜底测试。

用户明确要求实际写入、但本轮零写工具尝试时，Runtime 注入【一次】恢复提示让模型
真正执行；恢复后仍零写则不重复触发，交给既有真相闸门/诚实逻辑收尾。
不联网（ScriptedClient）。覆盖：
  1. 假成功（零工具声称已改）→ recovery → 真正 story.update 成功
  2. 零工具只发表看法 → recovery → 真正执行工具
  3. 探讨句（要不要改）→ 不触发
  4. 「那就按刚才说的改吧」→ 识别为写入请求，零工具 → recovery
  5. 正常 read→update → 不触发
  6. 写工具 stale/失败（write_attempts>0）→ 不触发 zero-write recovery
  7. recovery 后仍零工具 → 只恢复一次，最终诚实兜底
  8. 普通聊天/解释/回顾 → 行为不变
"""
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.script_writer import (
    script_writer_turn, _scene_token, _scenes_token)


def _proj():
    def _sc(sid, summ, tod="", loc=""):
        return Scene(scene_id=sid, summary=summ, characters=["LinXia"],
                     time_of_day=tod, location=loc, shots=[])
    return Project(
        name="demo", style=StyleAnchor(), story_text="完整剧本正文。",
        characters=[Character(name="LinXia", description_en="x")],
        scenes=[_sc("s01", "第一段", tod="夜"),
                _sc("s02", "第二段", tod="夜"),
                _sc("s03", "第三段", tod="夜"),
                _sc("s04", "第四段", tod="夜")],
        next_scene_seq=5)


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


def _s02_rev(tmp_path):
    return _scene_token(next(s for s in ProjectStore(tmp_path).load().scenes if s.scene_id == "s02"))


def _scenes_rev(tmp_path):
    return _scenes_token(ProjectStore(tmp_path).load().scenes)


def _joined(client):
    return " ".join(m.get("content", "") or "" for c in client.calls for m in c["messages"])


def _recovery_nudge_count(client):
    """最终一次 LLM 调用的上下文里，recovery nudge 消息只应有 1 条（持久在 history 中，
    不随调用次数重复注入）。"""
    msgs = client.calls[-1]["messages"] if client.calls else []
    return sum(1 for m in msgs
               if m.get("role") == "user" and RECOVERY_MARK in (m.get("content") or ""))


RECOVERY_MARK = "还没有发起任何写工具"


# ============ 1. 假成功（零工具声称已改）→ recovery → 真正 story.update ============
def test_fake_success_then_recovery_does_real_update(tmp_path):
    _save(tmp_path)
    rev = _s02_rev(tmp_path)
    client = ScriptedClient([
        ("已把第 2 段的时段改成「清晨」了。", None),                       # 零工具假成功
        (None, [_tc("story.read", {"scene_id": "s02"})]),                 # recovery 后先读
        (None, [_tc("story.update", {"scene_id": "s02", "expected_revision": rev,
                                     "time_of_day": "清晨"})]),           # 真正写入
        ("s02 的时段已改为清晨。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把第2段的时段改成清晨"))

    assert res["scene_updated"] is True
    sc = {s.scene_id: s for s in ProjectStore(tmp_path).load().scenes}
    assert sc["s02"].time_of_day == "清晨"
    assert _recovery_nudge_count(client) == 1        # recovery 触发且仅一次
    assert len(client.calls) == 4


# ============ 2. 零工具只发表看法（没声称完成）→ 同样 recovery → 真正执行 ============
def test_discuss_only_then_recovery_executes(tmp_path):
    _save(tmp_path)
    rev = _s02_rev(tmp_path)
    client = ScriptedClient([
        ("我觉得 s02 这个时段改成清晨会更有氛围，这样比较合理。", None),   # 只评论没动手
        (None, [_tc("story.read", {"scene_id": "s02"})]),
        (None, [_tc("story.update", {"scene_id": "s02", "expected_revision": rev,
                                     "time_of_day": "清晨"})]),
        ("已按你的要求把 s02 时段改为清晨。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把第 2 段时段改成清晨，只改这一段"))

    assert res["scene_updated"] is True
    assert {s.scene_id: s for s in ProjectStore(tmp_path).load().scenes}["s02"].time_of_day == "清晨"
    assert _recovery_nudge_count(client) == 1


# ============ 3. 探讨句「要不要改」→ 不触发 recovery ============
def test_discussion_question_no_recovery(tmp_path):
    _save(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()
    client = ScriptedClient([
        ("我觉得可以改得更有氛围一点，比如加一段雨夜环境描写，但你说了算。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid,
                                  "你认为要不要把第2段改得更有氛围一点？"))

    assert res["scene_updated"] is False
    assert _recovery_nudge_count(client) == 0
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert len(client.calls) == 1                  # 无额外往返


# ============ 4. 「那就按刚才说的改吧」→ 识别为写入请求，零工具 → recovery ============
def test_terse_confirmation_is_write_request(tmp_path):
    _save(tmp_path)
    rev = _scenes_rev(tmp_path)
    client = ScriptedClient([
        ("好的，这就给你加上。", None),                                    # 零工具
        (None, [_tc("story.list", {})]),
        (None, [_tc("story.create",
                    {"summary": "按上轮讨论新增的过渡段。", "characters": [],
                     "expected_scenes_revision": rev})]),
        ("已在末尾新增过渡段 s05。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "那就按刚才说的改吧。"))

    assert res["created"] is True
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == \
        ["s01", "s02", "s03", "s04", "s05"]
    assert _recovery_nudge_count(client) == 1


# ============ 5. 正常 read→update → 不触发 recovery ============
def test_normal_read_update_no_recovery(tmp_path):
    _save(tmp_path)
    rev = _s02_rev(tmp_path)
    client = ScriptedClient([
        (None, [_tc("story.read", {"scene_id": "s02"})]),
        (None, [_tc("story.update", {"scene_id": "s02", "expected_revision": rev,
                                     "location": "老旧公寓楼下"})]),
        ("s02 的地点已改为老旧公寓楼下。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把 s02 的地点改成老旧公寓楼下"))

    assert res["scene_updated"] is True
    assert _recovery_nudge_count(client) == 0       # 正常路径无 recovery
    assert len(client.calls) == 3


# ============ 6. 写工具 stale 失败（write_attempts>0）→ 不触发 zero-write recovery ============
def test_failed_write_attempt_no_recovery(tmp_path):
    _save(tmp_path)
    client = ScriptedClient([
        (None, [_tc("story.update", {"scene_id": "s02", "expected_revision": "deadbeef",
                                     "time_of_day": "清晨"})]),   # stale
        ("s02 的时段已经改好了。", None),                            # 却声称成功
        ("s02 的时段已经改好了。", None),                            # 纠偏后仍嘴硬
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把第2段时段改成清晨"))

    assert res["scene_updated"] is False
    # zero-write recovery 不应触发（已经尝试过写工具，走既有 stale/闸门路径）
    assert _recovery_nudge_count(client) == 0
    # 既有真相闸门兜底文案生效
    assert "没有真正" in res["assistant_message"] or "没有生效" in res["assistant_message"]


# ============ 7. recovery 后仍零工具 → 只恢复一次，最终诚实兜底 ============
def test_recovery_only_once_then_honest_fallback(tmp_path):
    _save(tmp_path)
    before_ids = [s.scene_id for s in ProjectStore(tmp_path).load().scenes]
    client = ScriptedClient([
        ("已把第 2 段时段改成清晨。", None),              # 第1次零工具假成功 → recovery
        ("s02 已经修改完成。", None),                     # 恢复后仍零工具嘴硬 → 既有闸门纠偏
        ("s02 确实已经修改完成了。", None),               # 纠偏后还嘴硬 → 诚实兜底
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把第2段的时段改成清晨"))

    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == before_ids
    assert res["scene_updated"] is False
    # recovery 提示只注入一次（最终上下文里恰有 1 条 nudge 消息）
    assert _recovery_nudge_count(client) == 1
    # 最终是诚实兜底，不是谎言
    assert "已经修改完成" not in res["assistant_message"]


# ============ 8. 普通聊天/解释/回顾 → 行为不变，无 recovery ============
def test_chat_turns_unchanged(tmp_path):
    _save(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()
    client = ScriptedClient([
        ("结尾节奏偏快，s10 收得太急，可以考虑拆成两段。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid,
                                  "不用修改，你觉得现在结尾的节奏快不快？说说看法。"))

    assert res["intent"] == "conversation"
    assert "结尾节奏偏快" in res["assistant_message"]
    assert _recovery_nudge_count(client) == 0
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert len(client.calls) == 1
