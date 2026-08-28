"""AI-7D-R 长批量生成/修改超时恢复 · 专项测试。

复现并锁定：create_many / update_many 大批次生成时 LLM ReadTimeout 超时，
不原样重发大请求，而是降级当前 turn 的 batch hint（5→2→1），用 user nudge
反馈结构化恢复信息（committed_items / recommended_max_batch_size / retry_count），
让 Agent 以更小批次继续剩余任务。最多恢复 2 次。

规则（用户钦定）：
- 单次 read timeout 360s（agnes_client，不改）
- 仅捕获 httpx.ReadTimeout（OCC stale / bad_args 不走此恢复）
- 超时发生在 LLM 响应未形成完整 tool_call 时，无真实 tool_call_id，
  故不构造假 tool message，用 user nudge 反馈
- 已成功批次保留为 checkpoint（committed_items 反馈，不靠 OCC 防重）
- create_many 不依赖 OCC 防重：超时恢复后不得重复创建已提交项
- 最多恢复 2 次；第 3 次仍超时 → 诚实结束并报告未完成
- turn 结束降级状态清零

不联网（TimeoutScripted client）。
"""
import asyncio
import json

import httpx
import pytest

from src.core.models import Project, Scene, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.script_writer import script_writer_turn, _scenes_token, _scenes_content_token


RAISE = object()   # 脚本占位：本次 chat_completion 抛 httpx.ReadTimeout


def _proj(n_scenes=5, start_seq=None):
    def _sc(sid, summ):
        return Scene(scene_id=sid, summary=summ, characters=["LinXia"], shots=[])
    sids = [f"s0{i}" for i in range(1, n_scenes + 1)]
    return Project(
        name="demo", style=StyleAnchor(), story_text="完整剧本正文。",
        characters=[Character(name="LinXia", description_en="x")],
        scenes=[_sc(sid, f"第{sid}段原文") for sid in sids],
        next_scene_seq=start_seq or (n_scenes + 1))


def _save(tmp_path, n_scenes=5, start_seq=None):
    ProjectStore(tmp_path).save(_proj(n_scenes, start_seq))
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


def _um(updates, rev):
    return _tc("story.update_many", {"updates": updates, "expected_revision": rev})


def _cm(scenes, rev):
    return _tc("story.create_many",
               {"scenes": scenes, "expected_scenes_revision": rev})


class TimeoutScripted:
    """可控脚本客户端。脚本项：RAISE（抛 ReadTimeout）/ callable(messages)->dict
    / (content, tool_calls) 元组 / dict。记录每次请求的 messages 引用。
    注意：turn 内 messages 是同一个被反复 append 的 list，故 self.calls 里存的
    都是同一 list 的引用——查注入总数只需看 calls[-1]（最终累计状态）。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def chat_completion(self, messages, **kw):
        self.calls.append(messages)
        if not self.script:
            return {"content": "（脚本耗尽）", "tool_calls": None}
        item = self.script.pop(0)
        if item is RAISE:
            raise httpx.ReadTimeout("")   # 空消息复现真实超时（str(e) 为空）
        if callable(item):
            return item(messages)
        if isinstance(item, tuple):        # (content, tool_calls) 简写
            return {"content": item[0], "tool_calls": item[1]}
        return item   # dict


def _content_rev(tmp_path):
    return _scenes_content_token(ProjectStore(tmp_path).load().scenes)


def _struct_rev(tmp_path):
    return _scenes_token(ProjectStore(tmp_path).load().scenes)


def _nudge_messages(calls):
    """取 turn 最终消息列表里的 user 消息文本（找 timeout nudge）。
    calls 里全是同一 messages list 的引用，calls[-1] 即最终累计状态，
    每条注入 nudge 在其中恰好出现一次。"""
    assert calls, "没有任何 LLM 调用"
    return [m.get("content", "") for m in calls[-1]
            if m.get("role") == "user"]


# ============ T1. 5 段 update_many 超时 → 降级 → 2+2+1 完成 ============
def test_update_many_timeout_downgrade_to_2_2_1(tmp_path):
    _save(tmp_path, n_scenes=5)
    items = [{"scene_id": f"s0{i}", "summary": f"新{i}"} for i in range(1, 6)]

    def um_batch(lo, hi):
        return lambda m: {"content": "", "tool_calls": [_um(items[lo:hi], _content_rev(tmp_path))]}

    client = TimeoutScripted([
        RAISE,                       # 本要发 5 段 um，超时
        um_batch(0, 2),              # 降级后发 2 段
        um_batch(2, 4),              # 再发 2 段
        um_batch(4, 5),              # 再发 1 段
        {"content": "已将 5 段全部改完。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "把 5 段都改了"))

    # 5 段全部落盘
    scenes = {s.scene_id: s.summary for s in ProjectStore(tmp_path).load().scenes}
    assert scenes == {f"s0{i}": f"新{i}" for i in range(1, 6)}
    # 3 批成功 update_many receipts
    um_receipts = [r for r in res["receipts"] if r.get("operation") == "story.update_many" and r["ok"]]
    assert len(um_receipts) == 3
    # nudge 注入：含 MODEL_READ_TIMEOUT + recommended ≤2
    nudges = [n for n in _nudge_messages(client.calls) if "MODEL_READ_TIMEOUT" in n]
    assert len(nudges) == 1
    assert "≤2" in nudges[0] or "≤ 2" in nudges[0]
    assert "没有新增任何提交" in nudges[0]
    # 调用次数：1 超时 + 3 工具 + 1 最终 = 5
    assert len(client.calls) == 5


# ============ T2. create_many 第一批成功 → 第二批超时 → 不重做前一批 ============
def test_create_many_checkpoint_no_redo_committed(tmp_path):
    _save(tmp_path, n_scenes=6, start_seq=7)
    s_new = [{"summary": f"新段{i}", "characters": []} for i in range(1, 6)]  # 拟建 5 段 s07-s11

    def cm_batch(lo, hi):
        return lambda m: {"content": "", "tool_calls": [_cm(s_new[lo:hi], _struct_rev(tmp_path))]}

    client = TimeoutScripted([
        cm_batch(0, 2),    # 建 s07,s08 成功
        RAISE,             # 本要建剩余 s09,s10,s11，超时
        cm_batch(2, 5),    # 降级后建剩余 s09,s10,s11
        {"content": "已创建 5 段。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "新建 5 段"))

    # canonical = 原 6 + 新 5 = 11，且 s07/s08 不重复
    ids = [s.scene_id for s in ProjectStore(tmp_path).load().scenes]
    assert ids == ["s01", "s02", "s03", "s04", "s05", "s06",
                   "s07", "s08", "s09", "s10", "s11"]
    assert ids.count("s07") == 1 and ids.count("s08") == 1
    # nudge 在第二批超时后注入，committed_items 含 s07,s08
    nudges = [n for n in _nudge_messages(client.calls) if "MODEL_READ_TIMEOUT" in n]
    assert len(nudges) == 1
    assert "s07" in nudges[0] and "s08" in nudges[0]
    assert "不要重做已提交项" in nudges[0]


# ============ T3. 半截 streamed tool JSON 不落盘、不续接 ============
def test_partial_toolcall_json_not_committed(tmp_path):
    _save(tmp_path, n_scenes=3)
    before_ids = [s.scene_id for s in ProjectStore(tmp_path).load().scenes]
    # arguments 是半截 JSON（流断聚合到一半，缺 ]}} 闭合，json.loads 会失败）
    partial_args = '{"updates":[{"scene_id":"s01","summary":"half"'
    partial_tc = {"id": "c1", "type": "function",
                  "function": {"name": "story.update_many",
                               "arguments": partial_args}}
    client = TimeoutScripted([
        {"content": "", "tool_calls": [partial_tc]},
        {"content": "好的，已完成。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "改一下"))

    # canonical 未变（半截不落盘）
    after = ProjectStore(tmp_path).load()
    assert [s.scene_id for s in after.scenes] == before_ids
    assert [s.summary for s in after.scenes] == [f"第{s.scene_id}段原文" for s in after.scenes]
    # 无任何 ok=True 写 receipt
    assert not any(r.get("ok") for r in res["receipts"])
    # 没触发 timeout 恢复（半截走 obs error 路径，不是 ReadTimeout）
    assert not any("MODEL_READ_TIMEOUT" in n for n in _nudge_messages(client.calls))


# ============ T4. 连续 2 次超时降级 5→2→1，第 3 次超时 → 诚实结束 ============
def test_three_timeouts_honest_failure(tmp_path):
    _save(tmp_path, n_scenes=5)
    client = TimeoutScripted([RAISE, RAISE, RAISE])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "改 5 段"))

    # turn 失败
    assert res["message"]["status"] == "failed"
    assert "ReadTimeout" in res["message"]["content"]
    # canonical 未动（3 次超时都没落盘）
    assert [s.summary for s in ProjectStore(tmp_path).load().scenes] == \
        [f"第{s0}段原文" for s0 in ("s01", "s02", "s03", "s04", "s05")]
    # 2 次 timeout nudge（5→2, 2→1），第 3 次 re-raise 不再 nudge
    nudges = [n for n in _nudge_messages(client.calls) if "MODEL_READ_TIMEOUT" in n]
    assert len(nudges) == 2
    assert "≤2" in nudges[0] or "≤ 2" in nudges[0]
    assert "≤1" in nudges[1] or "≤ 1" in nudges[1]


# ============ T5. OCC stale / bad_args 不触发 timeout retry ============
def test_stale_not_trigger_timeout_retry(tmp_path):
    _save(tmp_path, n_scenes=3)
    # 用错误 expected_revision 触发 stale（非 ReadTimeout）
    stale_tc = _um([{"scene_id": "s01", "summary": "改"}], "deadbeef")
    client = TimeoutScripted([
        ("", [stale_tc]),
        {"content": "好的。", "tool_calls": None},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "改第一段"))

    # stale 走原 obs 路径，receipts 有 1 条 stale（不是 timeout）
    assert not any("MODEL_READ_TIMEOUT" in n for n in _nudge_messages(client.calls))
    stale_r = [r for r in res["receipts"] if r.get("stale")]
    assert len(stale_r) == 1
    # canonical 未变
    assert [s.summary for s in ProjectStore(tmp_path).load().scenes] == \
        [f"第{s0}段原文" for s0 in ("s01", "s02", "s03")]
