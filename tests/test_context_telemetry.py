"""Compaction 遥测（改动四，仅观察项）mock 测试（不联网）。

明确不做任何基于上下文长度的压缩/摘要机制（实验否决：10 轮后 history 仅
~11k chars，阈值从未接近；有损摘要本身可能成为新失真源）。只验证每 turn
落盘一行结构化遥测：project / agent / session / turn_id / history_chars /
system_chars / tool_io_chars / 时间戳。
"""
import json

from tests.test_director_agent import (
    _save_proj, _runtime, _run, _tool_call, _shots_rev, ScriptedClient,
)


def _director():
    from src.agents.director import director_turn
    return director_turn


def _telemetry_lines(tmp_path):
    path = tmp_path / "output" / "agents" / "telemetry" / "context_size.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_chat_turn_telemetry_line(tmp_path):
    """纯聊天 turn：一行遥测，history=0、tool_io=0、system>0。"""
    _save_proj(tmp_path)
    client = ScriptedClient([("慢节奏长镜头更合适。", None)])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    res = _run(_director()(rt, "demo", sid, "你觉得这段适合长镜头还是快剪？"))
    lines = _telemetry_lines(tmp_path)
    assert len(lines) == 1
    line = lines[0]
    assert line["project"] == "demo" and line["agent"] == "director"
    assert line["session"] == sid and line["turn_id"]
    assert line["history_chars"] == 0          # 首轮无历史
    assert line["system_chars"] > 0
    assert line["tool_io_chars"] == 0
    assert line["ts"] > 0
    assert res["intent"] == "conversation"


def test_write_turn_telemetry_accumulates(tmp_path):
    """写 turn：tool I/O（工具参数+回执）计入估算；第二轮 history 增长。"""
    _save_proj(tmp_path)
    rev = _shots_rev(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.list", {"scene_id": "s02"})]),
        (None, [_tool_call("shot.create",
                           {"scene_id": "s02", "description": "风铃特写，浅景深",
                            "expected_shots_revision": rev})]),
        ("已创建镜头 s02_c01。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    _run(_director()(rt, "demo", sid, "在段尾补一个风铃特写"))
    lines = _telemetry_lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["tool_io_chars"] > 0       # shot.list 回执 + create 回执

    # 第二轮（探讨）：history 含第一轮 user/assistant
    rt._client = ScriptedClient([("节奏可以，先这样。", None)])
    _run(_director()(rt, "demo", sid, "现在整体节奏你觉得怎么样？"))
    lines = _telemetry_lines(tmp_path)
    assert len(lines) == 2
    assert lines[1]["history_chars"] > 0
    rt._client = None


def test_review_turn_telemetry(tmp_path):
    """Clean Review turn：审查会话也落一行（session 为一次性 review session）。"""
    _save_proj(tmp_path)
    client = ScriptedClient([
        (None, [_tool_call("shot.list", {})]),
        ("审查完成，无重大问题。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "director", "project", "demo")["session_id"]
    _run(_director()(rt, "demo", sid, "全片检查一下连续性"))
    lines = _telemetry_lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["session"] != sid          # 一次性审查会话，非主会话
    assert lines[0]["turn_id"].startswith("review_")
    assert lines[0]["tool_io_chars"] > 0
