"""AI-7D D1 · Unified Direct Chat Turn Runtime 测试。

engine 级：直接驱动 AgentRuntime.unified_turn + 注入 FakeClient（不联网、不调真 Agnes）。
验证 D1 三意图分流（conversation / project_inquiry / runtime_query）+ change 降级 +
not_configured 软失败 + /messages wrapper 等价。匹配 tests/test_agents.py 风格（_runtime/
_save_proj/FakeClient）+ tests/test_ai_7a_inquiry.py 风格（inquiry 路由断言）。

核心断言（D1.4/D1.5/D1.6）：
- project_inquiry/runtime_query → evidence_refs 非空（真 inspect 发生，非模型叙述）；
- assistant_message 是模型自然语言（FakeClient reply），无 tool-call JSON；
- conversation → events=[] / evidence_refs=[]（无 inspect）；
- change → D1 降为 conversation，**不建** ChangeSession（unified_turn 不触 cp_service）；
- not_configured → status=not_configured，不假装已查（但 inquiry 的 inspect 仍发生 → evidence_refs 非空）。
"""
import asyncio

import pytest

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.models import ResultStatus
from src.agents.runtime import AgentRuntime


# ============ 辅助（镜像 test_agents.py）============
def _proj():
    """3 场景 1 shot + Character LinXia（让'多少场'类查询有结构可读）。"""
    return Project(
        name="demo", style=StyleAnchor(),
        characters=[Character(name="LinXia", description_en="a young woman")],
        scenes=[
            Scene(scene_id="s01", summary="rain", characters=["LinXia"],
                  shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                              description="look up at rain",
                              characters=["LinXia"], target_duration_s=5)]),
            Scene(scene_id="s02", summary="meeting", characters=["LinXia"], shots=[]),
            Scene(scene_id="s03", summary="escape", characters=["LinXia"], shots=[]),
        ])


def _save_proj(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    return tmp_path


def _runtime(tmp_path, **kw):
    kw.setdefault("settings_path", tmp_path / "agents_data" / "settings.json")
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path, **kw)


class FakeClient:
    """注入用假 LLM client（不联网）。reply 可带场景数模拟接地回答。"""
    def __init__(self, reply: str = "ok"):
        self._reply = reply

    async def chat(self, messages, **kw):
        return self._reply


class RecordingFakeClient:
    """记录每次 chat 的 messages，供断言历史注入（Bug B）。"""
    def __init__(self, reply: str = "ok"):
        self._reply = reply
        self.calls = []   # 每次 chat 的 messages 副本

    async def chat(self, messages, **kw):
        self.calls.append([{"role": m["role"], "content": m["content"]} for m in messages])
        return self._reply


def _raise503():
    from fastapi import HTTPException
    raise HTTPException(503, "not ready")


def _session_id(rt, project, agent_id, scope_type="project", scope_id=""):
    return rt.get_or_create_session(project, agent_id, scope_type, scope_id)["session_id"]


def _run(coro):
    return asyncio.run(coro)


# ============ 1. conversation：纯闲聊，无 inspect ============
def test_conversation_trivial(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient(reply="你好呀"))
    sid = _session_id(rt, "demo", "producer")
    res = _run(rt.unified_turn("demo", sid, "你好"))
    assert res["intent"] == "conversation"
    assert res["assistant_message"] == "你好呀"
    assert res["evidence_refs"] == []
    assert res["events"] == []
    assert res["linked_change_session_id"] is None


# ============ 2. Producer project_inquiry：真 inspect，接地回答，无 ChangeSession ============
def test_producer_project_inquiry(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient(reply="现在共有 3 场。"))
    sid = _session_id(rt, "demo", "producer")
    # D1-A gate 消息：含"先不要修改"（否定 change）→ 应路由 project_inquiry，非 conversation
    res = _run(rt.unified_turn("demo", sid, "现在剧本有多少场？先不要修改。"))
    assert res["intent"] == "project_inquiry"
    assert res["agent_id"] == "producer"
    assert res["assistant_message"] == "现在共有 3 场。"
    # 真 inspect 发生 → evidence_refs 非空
    assert res["evidence_refs"], "project_inquiry 必须有真实 inspect evidence_refs"
    assert res["events"], "project_inquiry 必须有真后台 inspect 事件"
    # 无 ChangeSession（D1 不桥接 change；unified_turn 不触 cp_service）
    assert res["linked_change_session_id"] is None
    # 无 tool-call JSON 泄漏（assistant_message 是纯自然语言）
    assert "{" not in res["assistant_message"]


# ============ 3. Specialist read answer：保持 agent，真 script context，不自动改 ============
def test_specialist_read_answer(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient(reply="中段 s02 没有镜头，所以显得短。"))
    sid = _session_id(rt, "demo", "script_writer", "scene", "s01")
    # D1-B gate 消息：含"先别改"（否定 change）→ project_inquiry，走 specialist_read_answer
    res = _run(rt.unified_turn("demo", sid, "先读一下现在的剧本，中段为什么显得短？先别改。"))
    assert res["intent"] == "project_inquiry"
    assert res["agent_id"] == "script_writer"   # 保持 Script Writer，不切 producer
    assert res["assistant_message"] == "中段 s02 没有镜头，所以显得短。"
    assert res["evidence_refs"], "specialist_read_answer 必须有真实 inspect evidence_refs"
    assert res["linked_change_session_id"] is None   # 不自动修改


# ============ 4. runtime_query：进度词 → runtime_query，workflow_state 注入路径 ============
def test_runtime_query(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient(reply="目前没有进行中的任务。"))
    sid = _session_id(rt, "demo", "producer")
    res = _run(rt.unified_turn("demo", sid, "现在做到哪了？"))
    assert res["intent"] == "runtime_query"
    assert res["agent_id"] == "producer"
    # runtime_query 走 run_inquiry（question 路径 + workflow_state 注入）→ 仍有 inspect evidence
    assert res["evidence_refs"]


# ============ 5. change（无否定）→ D1 降为 conversation，不建 ChangeSession ============
def test_change_falls_to_conversation(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient(reply="这需要走变更流程。"))
    sid = _session_id(rt, "demo", "producer")
    res = _run(rt.unified_turn("demo", sid, "把结局改成大团圆"))
    assert res["intent"] == "conversation"   # D1 降级（D2 接桥）
    assert res["linked_change_session_id"] is None   # 不建 ChangeSession
    assert res["assistant_message"] == "这需要走变更流程。"


# ============ 6. not_configured：不假装已查（但 inquiry 的 inspect 仍发生）============
def test_not_configured_conversation(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=None, agnes_getter=_raise503)
    sid = _session_id(rt, "demo", "producer")
    res = _run(rt.unified_turn("demo", sid, "你好"))
    assert res["intent"] == "conversation"
    assert res["message"]["status"] == ResultStatus.NOT_CONFIGURED.value
    assert "未配置" in res["assistant_message"]


def test_not_configured_inquiry(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=None, agnes_getter=_raise503)
    sid = _session_id(rt, "demo", "producer")
    res = _run(rt.unified_turn("demo", sid, "现在剧本有多少场？先不要修改。"))
    # 即便 Agnes 未配置，inquiry 的真 inspect 仍发生 → evidence_refs 非空（D1.4：inspect 发生 ≠ 模型说）
    assert res["evidence_refs"], "未配置也应已真 inspect（evidence_refs 非空）"
    assert res["message"]["status"] == ResultStatus.NOT_CONFIGURED.value
    assert "未配置" in res["assistant_message"]   # 不假装已查


# ============ 7. /messages wrapper 等价 unified_turn.assistant_message ============
def test_messages_wrapper_equivalence(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient(reply="hi"))
    sid = _session_id(rt, "demo", "producer")
    msg = _run(rt.send_message_to_session("demo", sid, "你好"))
    res = _run(rt.unified_turn("demo", sid, "你好"))
    # wrapper 返回的 assistant message == unified_turn 的 assistant_message
    assert msg["content"] == res["assistant_message"] == "hi"
    # wrapper 走同一 unified runtime（单一 runtime，D1.7）
    assert msg["meta"]["intent"] == res["intent"] == "conversation"


# ============ 8. 持久化：user + assistant(meta) 落盘，刷新可恢复 ============
def test_persistence_meta_survives_reload(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=FakeClient(reply="共 3 场。"))
    sid = _session_id(rt, "demo", "producer")
    _run(rt.unified_turn("demo", sid, "现在剧本有多少场？先不要修改。"))
    # 新 runtime（同目录）重载 session → assistant 消息含 meta.evidence_refs（刷新后'已查看'证据）
    rt2 = _runtime(tmp_path)
    s = rt2.get_session("demo", sid)
    assert s is not None
    asst = [m for m in s["messages"] if m["role"] == "assistant"]
    assert asst and asst[-1]["meta"] is not None
    assert asst[-1]["meta"]["intent"] == "project_inquiry"
    assert asst[-1]["meta"]["evidence_refs"]


# ============ 9. 对话历史注入（Bug B 修复：inquiry 多轮上下文承接）============
def test_inquiry_passes_conversation_history(tmp_path):
    """AI-7D D1 Bug B：inquiry 路径须把前轮 user+assistant 注入 messages，使多轮有上下文承接。
    FakeClient 记录 messages；断言第 2 轮的 messages 含第 1 轮的 user+assistant。"""
    _save_proj(tmp_path)
    cli = RecordingFakeClient(reply="第一轮答案。")
    rt = _runtime(tmp_path, client=cli)
    sid = _session_id(rt, "demo", "producer")
    # 第 1 轮（project_inquiry，history 空）
    r1 = _run(rt.unified_turn("demo", sid, "现在剧本有多少场？先不要修改。"))
    assert r1["assistant_message"] == "第一轮答案。"
    assert len(cli.calls) == 1
    # 第 2 轮（runtime_query「现在做到哪了」→ run_inquiry + history；无 change 动词避免降为 conversation）
    cli._reply = "第二轮承接前文。"
    r2 = _run(rt.unified_turn("demo", sid, "现在做到哪了？"))
    assert len(cli.calls) == 2
    msg2 = cli.calls[1]
    contents = [m["content"] for m in msg2]
    # 第 2 轮的 messages 必含第 1 轮的 user + assistant（上下文承接，Bug B）
    assert any("现在剧本有多少场" in c for c in contents), "第 2 轮未注入第 1 轮 user 历史"
    assert any("第一轮答案" in c for c in contents), "第 2 轮未注入第 1 轮 assistant 历史"
    # 第 2 轮 user 指令含「不要输出工具调用语法」（Bug A prompt 修复）
    assert any("工具调用语法" in c for c in contents), "第 2 轮 user 指令缺禁工具语法条款"
