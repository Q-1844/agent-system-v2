"""AI-7D-R 真实性收口 · 虚假成功专项测试。

追查并锁定「用户看到『已插入/已移动/已改好』，但 canonical 实际未变化」的根因。
不联网（ScriptedClient）。区分五类：
  A. 工具没调用（模型只在自然语言里声称完成）
  B. 工具调用失败（参数错误/未知工具/异常）
  C. 写盘失败（save 未真正落盘 → 写后重读校验失败 verify_failed）
  D. stale（乐观并发拒绝，未写入）
  E. 模型误报成功（工具返回 ok=false，最终文本却称完成）

修复方式是 Runtime 结构性强制（回执账本 + 最终回复真相闸门 + 确定性兜底），
不靠增加 system prompt 规则。
"""
import asyncio
import json

import pytest

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.script_writer import script_writer_turn


def _multi_proj():
    def _sc(sid, summ):
        return Scene(scene_id=sid, summary=summ, characters=["LinXia"], shots=[])
    return Project(
        name="demo", style=StyleAnchor(), story_text="完整剧本正文。",
        characters=[Character(name="LinXia", description_en="x")],
        scenes=[_sc("s01", "第一段"), _sc("s02", "第二段"), _sc("s03", "第三段")],
        next_scene_seq=4)


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
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}


def _scenes_rev(tmp_path):
    from src.agents.script_writer import _scenes_token
    return _scenes_token(ProjectStore(tmp_path).load().scenes)


class ScriptedClient:
    """按调用顺序返回 (content, tool_calls)；记录每次请求的 messages。"""
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


# ============ A. 工具没调用，但声称已创建 → 纠偏后真正调用工具 → 真成功 ============
def test_no_tool_claim_create_then_corrective_real_call(tmp_path):
    _save(tmp_path)
    rev = _scenes_rev(tmp_path)
    client = ScriptedClient([
        # 第一轮：模型直接撒谎「已创建」，没有任何 tool_call
        ("我已经在 s01 后插入了一个新的故事段。", None),
        # 第二轮（收到 Runtime 纠偏账本后）：真正调用 story.create
        (None, [_tc("story.create",
                    {"summary": "纠偏后真正插入的段。", "characters": [],
                     "after_scene_id": "s01", "expected_scenes_revision": rev})]),
        ("已在 s01 后新增故事段 s04。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "在第一段后面加一个故事段"))

    # canonical 真的改了
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == \
        ["s01", "s04", "s02", "s03"]
    assert res["created"] is True
    assert res["receipts"] and res["receipts"][-1]["ok"] is True
    # 发了 3 次 LLM（撒谎 → 纠偏 → 真实工具+最终回复跨 2 次：tool 一次、final 一次）
    assert len(client.calls) == 3
    # 纠偏/恢复消息确实被注入（指出没真正调用工具；现在走 zero-write recovery 文案）
    joined = " ".join(m.get("content", "") for c in client.calls for m in c["messages"])
    assert ("没有发起对应的工具调用" in joined or "根本没有发起" in joined
            or "还没有发起任何写工具" in joined)


# ============ A2. 工具没调用，声称移动，纠偏后仍只嘴硬 → 确定性兜底，canonical 不变 ============
def test_no_tool_claim_move_persists_lie_gets_honest_fallback(tmp_path):
    _save(tmp_path)
    before_ids = [s.scene_id for s in ProjectStore(tmp_path).load().scenes]
    client = ScriptedClient([
        ("我已经把 s03 移到最前面了。", None),
        # zero-write recovery 后仍嘴硬
        ("是的，s03 已经在第一位了，移动完成。", None),
        # 真相闸门纠偏后还嘴硬 → 诚实兜底
        ("s03 确实已经移动完成。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把第三段移到开头"))

    # canonical 未变
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == before_ids
    assert res["moved"] is False
    assert res["created"] is False
    # 最终文本是 Runtime 确定性兜底（诚实），不是模型那句谎言
    assert "已经在第一位" not in res["assistant_message"]
    assert ("没有真正执行修改" in res["assistant_message"]
            or "没有发出对应的工具调用" in res["assistant_message"])
    # 账本记录 0 次写成功
    assert res["receipts"] == []


# ============ B. 工具调用失败（未知工具）后误报成功 → 兜底，canonical 不变 ============
def test_unknown_tool_then_false_success_gets_fallback(tmp_path):
    _save(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()
    client = ScriptedClient([
        # 调了一个不存在的工具
        (None, [_tc("story.insert", {"summary": "x"}, "cx")]),
        # 却声称成功
        ("已经帮你插入新段了。", None),
        # 纠偏后还嘴硬
        ("插入已经完成了。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "加一段"))

    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert res["created"] is False
    assert "插入已经完成" not in res["assistant_message"]
    # 账本记了这次失败的调用（被识别为「有写尝试但失败」，而非「没调用工具」）
    r0 = res["receipts"][0]
    assert r0["ok"] is False
    assert r0["operation"] == "story.insert"
    assert "未知工具" in (r0.get("error") or "")


# ============ C. 写盘失败（save 未落盘）→ verify_failed，ok=False，不记成功 ============
def test_save_not_persisted_returns_verify_failed(tmp_path, monkeypatch):
    """模拟写盘失败：把 ProjectStore.save 改成 no-op，内存改了但磁盘没变。
    写后重新读取 canonical 校验必须捕获 → ok=false / error=verify_failed。"""
    _save(tmp_path)
    # save 变 no-op：pstore.save(proj) 不真正落盘
    monkeypatch.setattr(ProjectStore, "save", lambda self, project: None)

    from src.agents.script_writer import _execute_story_create
    rt = _runtime(tmp_path, ScriptedClient([]))
    rev = _scenes_rev(tmp_path)
    obs = _execute_story_create(
        rt, "demo",
        json.dumps({"summary": "应该落盘但没落盘的段。", "characters": [],
                    "expected_scenes_revision": rev}),
        "u", "m")

    assert obs["ok"] is False
    assert obs["verified"] is False
    assert obs["error"] == "verify_failed"
    # canonical 确实没有新段
    assert len(ProjectStore(tmp_path).load().scenes) == 3


# ============ D. stale 拒绝写入后，模型误报成功 → 兜底，canonical 不变 ============
def test_stale_then_false_success_gets_fallback(tmp_path):
    _save(tmp_path)
    before_ids = [s.scene_id for s in ProjectStore(tmp_path).load().scenes]
    client = ScriptedClient([
        # 用过期指纹调用 → stale，未写入
        (None, [_tc("story.move",
                    {"scene_id": "s03", "before_scene_id": "s01",
                     "expected_scenes_revision": "deadbeef"})]),
        # 却声称移动成功
        ("s03 已经移到最前面了。", None),
        # 纠偏后仍嘴硬
        ("移动完成，s03 现在是第一段。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把第三段移到开头"))

    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == before_ids
    assert res["moved"] is False
    receipt = res["receipts"][0]
    assert receipt["ok"] is False and receipt["stale"] is True
    # 兜底文案诚实说明没生效（过期/版本）
    assert "没有真正生效" in res["assistant_message"]
    assert "s03 现在是第一段" not in res["assistant_message"]


# ============ D2. stale 后模型接受现实、诚实回复 → 不触发兜底，文本保留 ============
def test_stale_then_honest_reply_passes(tmp_path):
    _save(tmp_path)
    client = ScriptedClient([
        (None, [_tc("story.create",
                    {"summary": "基于过期版本的创建。", "characters": [],
                     "expected_scenes_revision": "deadbeef"})]),
        # 诚实：没有谎称成功
        ("故事段列表刚被改动过，这次创建没有生效。你可以让我重新读取后再创建。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "加一段"))

    assert res["created"] is False
    assert "这次创建没有生效" in res["assistant_message"]
    assert len(client.calls) == 2   # 无额外纠偏


# ============ E. 真成功 + 诚实汇报 → 不被误判，原文返回 ============
def test_genuine_success_honest_reply_not_flagged(tmp_path):
    _save(tmp_path)
    rev = _scenes_rev(tmp_path)
    client = ScriptedClient([
        (None, [_tc("story.create",
                    {"summary": "真实新增的段。", "characters": [],
                     "after_scene_id": "s01", "expected_scenes_revision": rev})]),
        ("已在 s01 之后新增故事段 s04。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "在第一段后加一段"))

    assert res["created"] is True
    assert "已在 s01 之后新增故事段 s04。" in res["assistant_message"]
    assert [s.scene_id for s in ProjectStore(tmp_path).load().scenes] == \
        ["s01", "s04", "s02", "s03"]
    # 只有 2 次 LLM 调用（tool、final），没有纠偏往返
    assert len(client.calls) == 2


# ============ 纯对话/提议措辞不应被误判为「声称成功」 ============
def test_conversation_offer_not_flagged_as_success(tmp_path):
    _save(tmp_path)
    before = (tmp_path / "output" / "project.json").read_bytes()
    # 明确写入请求（加一段）模型只澄清/提议、零写尝试 → zero-write recovery 一次；
    # 恢复后仍澄清（未声称成功）→ 放行，零落盘。
    client = ScriptedClient([
        ("你想加什么内容的故事段？我可以帮你创建并放到合适的位置。", None),
        ("你还没说要什么内容，告诉我主题我就帮你创建。", None),
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "我想加一段"))

    assert res["created"] is False
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert len(client.calls) == 2   # 澄清 + recovery 后再澄清（恢复仅此一次）


# ============ 写工具回执统一结构 ============
def test_write_receipt_has_unified_shape(tmp_path):
    _save(tmp_path)
    from src.agents.script_writer import _execute_story_move
    rt = _runtime(tmp_path, ScriptedClient([]))
    obs = _execute_story_move(
        rt, "demo",
        json.dumps({"scene_id": "s03", "before_scene_id": "s01",
                    "expected_scenes_revision": _scenes_rev(tmp_path)}),
        "u", "m")
    for key in ("ok", "operation", "target", "before", "after",
                "revision", "verified", "error"):
        assert key in obs, f"回执缺字段 {key}"
    assert obs["operation"] == "story.move"
    assert obs["ok"] is True and obs["verified"] is True
    assert obs["before"] == ["s01", "s02", "s03"]
    assert obs["after"] == ["s03", "s01", "s02"]
