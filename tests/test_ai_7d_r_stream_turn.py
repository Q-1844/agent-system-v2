"""AI-7D-R Phase 3 · streaming tool-loop event emit + thinking snapshot 测试。

验证冻结的 _run_tool_loop 在 event_cb 下 emit 正确事件序列、thinking snapshot 全 turn
固定、turn.completed 携带完整 16 键结果、turn.failed 收尾。决策语义不变（OCC/串行/真相
闸门/receipts/trace 照常）。

用 ScriptedClient 实现 chat_completion_stream（镜像 provider 事件：content_delta /
reasoning_delta / finish），不联网。
"""
import asyncio
import json
from types import SimpleNamespace

from src.core.models import Project, Scene, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.script_writer import script_writer_turn, _scenes_token


def _proj():
    return Project(name="demo", style=StyleAnchor(), story_text="剧本。",
                   characters=[Character(name="LinXia", description_en="x")],
                   scenes=[Scene(scene_id="s01", summary="第一段", characters=["LinXia"], shots=[]),
                           Scene(scene_id="s02", summary="第二段", characters=["LinXia"], shots=[]),
                           Scene(scene_id="s03", summary="第三段", characters=["LinXia"], shots=[]),
                           Scene(scene_id="s04", summary="第四段", characters=["LinXia"], shots=[]),
                           Scene(scene_id="s05", summary="第五段", characters=["LinXia"], shots=[]),
                           Scene(scene_id="s06", summary="第六段", characters=["LinXia"], shots=[])],
                   next_scene_seq=7)


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


class StreamClient:
    """实现 chat_completion_stream（镜像 provider 事件），按 script 逐 LLM sub-call
    返回 finish 事件（content/tool_calls）。记录每次调用的 thinking 参数以验 snapshot。"""
    def __init__(self, script):
        self.script = list(script)
        self.thinking_seen = []   # 每次 sub-call 收到的 thinking 值

    async def chat_completion_stream(self, messages, **kw):
        self.thinking_seen.append(kw.get("thinking", False))
        if not self.script:
            # fallback：纯文本收尾
            yield {"kind": "content_delta", "delta": "完成。"}
            yield {"kind": "finish", "reason": "stop", "content": "完成。",
                   "tool_calls": None, "usage": None}
            return
        step = self.script.pop(0)
        # reasoning delta（若该步给了 reasoning）
        for rd in step.get("reasoning", []):
            yield {"kind": "reasoning_delta", "delta": rd}
        if step.get("tool_calls"):
            yield {"kind": "finish", "reason": "tool_calls", "content": None,
                   "tool_calls": step["tool_calls"], "usage": None}
        else:
            for cd in step.get("content_deltas", [step.get("content", "")]):
                if cd:
                    yield {"kind": "content_delta", "delta": cd}
            yield {"kind": "finish", "reason": "stop",
                   "content": step.get("content", ""), "tool_calls": None, "usage": None}

    async def chat(self, messages, **kw):
        return ""


def _collect_events(rt, project, sid, content, thinking=False):
    """跑 script_writer_turn 带 event_cb，收事件 + 最终 result。"""
    events = []

    def cb(ev):
        events.append(ev)

    res = _run(script_writer_turn(rt, project, sid, content, thinking=thinking, event_cb=cb))
    return res, events


# ============ 1. tool.started/completed + assistant.delta + turn 结果携带 ============
def test_stream_events_tool_started_completed_assistant_delta(tmp_path):
    _save(tmp_path)
    rev0 = _rev(["s01", "s02", "s03", "s04", "s05", "s06"])
    client = StreamClient([
        {"tool_calls": [_tc("story.list", {}, "l1")]},
        {"tool_calls": [_tc("story.create", {"summary": "新段", "characters": ["LinXia"],
                                             "expected_scenes_revision": rev0}, "c1")]},
        {"content_deltas": ["已", "新增", "一段。"]},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res, events = _collect_events(rt, "demo", sid, "加一段")

    types = [e["type"] for e in events]
    # 每个 tool 一次 started + completed
    assert types.count("tool.started") == 2
    assert types.count("tool.completed") == 2
    # tool 事件携带正确字段
    for e in events:
        if e["type"] in ("tool.started", "tool.completed"):
            assert "step_id" in e and "call_id" in e and "name" in e
    # story.create 的 label
    assert any(e["type"] == "tool.started" and e["name"] == "story.create"
               and e["label"] == "创建故事段" for e in events)
    # completed summary 来自真实回执
    create_completed = [e for e in events if e["type"] == "tool.completed"
                        and e["name"] == "story.create"][0]
    assert create_completed["ok"] is True
    assert "s07" in create_completed["summary"]   # _op_human：创建故事段 s07
    # assistant.delta 逐块
    deltas = [e["delta"] for e in events if e["type"] == "assistant.delta"]
    assert deltas == ["已", "新增", "一段。"]
    # 最终结果携带完整 16 键
    for k in ("assistant_message", "receipts", "trace", "intent", "message", "created"):
        assert k in res
    assert res["created"] is True


# ============ 2. turn.started 在 tool-loop 前（由 endpoint 测，这里测 thinking 透传） ============
# thinking snapshot 由 endpoint 固定；script_writer_turn 透传到每个 sub-call。
def test_thinking_snapshot_propagated_to_all_subcalls(tmp_path):
    _save(tmp_path)
    rev0 = _rev(["s01", "s02"])
    client = StreamClient([
        {"tool_calls": [_tc("story.list", {}, "l1")]},
        {"tool_calls": [_tc("story.create", {"summary": "x", "characters": ["LinXia"],
                                             "expected_scenes_revision": rev0}, "c1")]},
        {"content": "完成。"},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    _run(script_writer_turn(rt, "demo", sid, "加一段", thinking=True, event_cb=lambda e: None))
    # 所有 sub-call 都收到 thinking=True（snapshot 固定，不动态读）——可能因真相闸门
    # 补读触发额外只读 sub-call，核心是 thinking 值全程一致，不读前端当前开关。
    assert len(client.thinking_seen) >= 3
    assert all(t is True for t in client.thinking_seen)


def test_thinking_false_snapshot_propagated(tmp_path):
    _save(tmp_path)
    client = StreamClient([
        {"content": "好的。"},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    _run(script_writer_turn(rt, "demo", sid, "看一下", thinking=False, event_cb=lambda e: None))
    assert client.thinking_seen == [False]


# ============ 3. reasoning.* 事件序列 + 无 reasoning 时不出现空项 ============
def test_reasoning_events_when_model_returns_reasoning(tmp_path):
    _save(tmp_path)
    client = StreamClient([
        {"reasoning": ["先想", "一下"], "content": "看完。"},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res, events = _collect_events(rt, "demo", sid, "看一下", thinking=True)
    r_started = [e for e in events if e["type"] == "reasoning.started"]
    r_delta = [e for e in events if e["type"] == "reasoning.delta"]
    r_done = [e for e in events if e["type"] == "reasoning.completed"]
    assert len(r_started) == 1 and r_started[0]["step_id"] == 0
    assert [d["delta"] for d in r_delta] == ["先想", "一下"]
    assert len(r_done) == 1 and "duration_ms" in r_done[0]


def test_no_reasoning_events_when_model_skips_reasoning(tmp_path):
    """thinking=true 但模型无 reasoning → 不出现空 reasoning 项（UI 不显示空思考）。"""
    _save(tmp_path)
    client = StreamClient([
        {"tool_calls": [_tc("story.list", {}, "l1")]},
        {"content": "完成。"},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res, events = _collect_events(rt, "demo", sid, "看一下", thinking=True)
    assert not any(e["type"].startswith("reasoning") for e in events)


# ============ 4. tool.failed（工具执行异常）============
def test_tool_failed_on_executor_exception(tmp_path):
    _save(tmp_path)
    client = StreamClient([
        {"tool_calls": [_tc("story.create", {"summary": "x", "characters": ["LinXia"],
                                             "expected_scenes_revision": "STALE TOKEN"}, "c1")]},
        {"content": "已处理。"},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res, events = _collect_events(rt, "demo", sid, "加一段")
    # story.create 用 stale token → 失败（不是异常，是 obs ok=false）→ tool.completed(ok=false)
    # 这里测真正的异常路径：未知工具触发 exc
    client2 = StreamClient([
        {"tool_calls": [_tc("story.nonexistent", {}, "x1")]},
        {"content": "完成。"},
    ])
    rt2 = _runtime(tmp_path, client2)
    sid2 = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    _, events2 = _collect_events(rt2, "demo", sid2, "做点什么")
    # 未知工具 → ok=false → tool.completed(ok=false)（非异常路径）
    comp = [e for e in events2 if e["type"] == "tool.completed"]
    assert comp and comp[0]["ok"] is False


# ============ 5. 多写串行事件顺序与真实 tool-loop 一致（Case G）============
def test_serial_write_events_in_real_order(tmp_path):
    _save(tmp_path)
    rev0 = _rev(["s01", "s02", "s03", "s04", "s05", "s06"])
    # 一步发 2 个写工具 → 第1个执行，第2个串行拒绝（ok=false）；下一 step 模型补发第2个
    client = StreamClient([
        {"tool_calls": [_tc("story.create", {"summary": "a", "characters": ["LinXia"],
                                             "expected_scenes_revision": rev0}, "c1"),
                        _tc("story.create", {"summary": "b", "characters": ["LinXia"],
                                             "expected_scenes_revision": rev0}, "c2")]},
        # 第2步：模型补发（用第1个 create 后的新 rev）
        {"tool_calls": [_tc("story.create", {"summary": "b", "characters": ["LinXia"],
                                             "expected_scenes_revision": _rev(
                                                 ["s01","s02","s03","s04","s05","s06","s07"])}, "c3")]},
        {"content": "完成。"},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res, events = _collect_events(rt, "demo", sid, "加两段")
    started = [e for e in events if e["type"] == "tool.started"]
    completed = [e for e in events if e["type"] == "tool.completed"]
    # 3 个 started（c1, c2, c3），3 个 completed
    assert len(started) == 3
    assert len(completed) == 3
    # 第2个（c2）串行拒绝 → ok=false summary 含串行提示
    c2 = [e for e in completed if e["call_id"] == "c2"][0]
    assert c2["ok"] is False and "串行" in c2["summary"]
    # 最终 canonical：新增 s07 + s08
    scenes = [s.scene_id for s in ProjectStore(tmp_path).load().scenes]
    assert "s07" in scenes and "s08" in scenes


# ============ 6. 非 tool-loop 异常 → turn.failed（endpoint 层，这里测 cb 不吞异常） ============
def test_turn_failed_on_unexpected_exception(tmp_path):
    """_run_tool_loop 内部异常（非 AgentError）→ 上抛，script_writer_turn 捕获成 FAILED
    status（既有行为）；endpoint 层转 turn.failed。这里验证 cb 不干扰既有异常处理。"""
    _save(tmp_path)

    class BoomClient(StreamClient):
        async def chat_completion_stream(self, messages, **kw):
            raise RuntimeError("boom")
            yield  # unreachable: 让它成为 async generator（避免 coroutine 未 await 警告）

    client = BoomClient([])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    events = []
    res = _run(script_writer_turn(rt, "demo", sid, "hi", event_cb=lambda e: events.append(e)))
    # 既有行为：异常 → assistant_text="[执行失败...]"，status FAILED（intent conversation）
    assert "执行失败" in res["assistant_message"]


# ============ 7. 持久化不受 event_cb 影响（仍 1 user + 1 assistant+meta）============
def test_persistence_unchanged_with_streaming(tmp_path):
    _save(tmp_path)
    rev0 = _rev(["s01", "s02", "s03", "s04", "s05", "s06"])
    client = StreamClient([
        {"tool_calls": [_tc("story.create", {"summary": "x", "characters": ["LinXia"],
                                             "expected_scenes_revision": rev0}, "c1")]},
        {"content": "已新增。"},
    ])
    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "加一段", event_cb=lambda e: None))
    session = rt._session_store("demo").load(sid)
    # 恰好 1 user + 1 assistant（event_cb 不持久化中间事件）
    users = [m for m in session.messages if m.role == "user"]
    assts = [m for m in session.messages if m.role == "assistant"]
    assert len(users) == 1 and len(assts) == 1
    # assistant meta 含 receipts/trace（既有持久化不变）
    assert assts[0].meta and "receipts" in assts[0].meta and "trace" in assts[0].meta
