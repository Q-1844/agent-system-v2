"""AI-7D-R · turn 后台续跑 / 资源锁事件 / 协作式停止 / 并发互斥 测试。

覆盖：
  1. 协作式取消：cancel_event 置位后在【步骤边界】安全停止（已完成的写保留），
     turn 按 STOPPED 诚实收尾。
  2. HTTP /turn/stream：写工具触发 resource.locked → story.changed →
     finally resource.unlocked（发布到项目总线）。
  3. /turn/cancel 端点：置位注册的 cancel_event；无活跃任务时 stopped=False。
  4. 并发互斥：同会话已有活跃 turn 时，再发 /turn/stream 返回 409。
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from src.core.models import Project, Scene, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import get_agent_runtime, reset_agent_runtime
from src.agents.script_writer import script_writer_turn, _scenes_token
from src.agents.project_events import get_project_bus, reset_project_bus, RESOURCE_STORY
import routers.agents as agents_router


def _proj():
    return Project(name="demo", style=StyleAnchor(), story_text="剧本。",
                   characters=[Character(name="LinXia", description_en="x")],
                   scenes=[Scene(scene_id=f"s0{i}", summary=f"第{i}段",
                                 characters=["LinXia"], shots=[]) for i in range(1, 5)],
                   next_scene_seq=5)


def _save(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    return tmp_path


def _runtime(tmp_path, client):
    from src.agents.runtime import AgentRuntime
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


class _StreamClient:
    """按脚本逐 LLM sub-call 产出 finish（镜像 provider 事件）。
    hook：每次调用前先执行（用于在第一步设置 cancel_event）。"""
    def __init__(self, script, hook=None):
        self.script = list(script)
        self.hook = hook

    async def chat_completion_stream(self, messages, **kw):
        if self.hook:
            self.hook()
            self.hook = None
        if not self.script:
            yield {"kind": "finish", "reason": "stop", "content": "完成。",
                   "tool_calls": None, "usage": None}
            return
        step = self.script.pop(0)
        if step.get("tool_calls"):
            yield {"kind": "finish", "reason": "tool_calls", "content": None,
                   "tool_calls": step["tool_calls"], "usage": None}
        else:
            yield {"kind": "finish", "reason": "stop",
                   "content": step.get("content", "完成。"), "tool_calls": None,
                   "usage": None}

    async def chat_completion(self, messages, **kw):
        return {"content": "完成。", "tool_calls": None}


# ============ 1. 协作式取消（turn 层，确定性） ============
def test_cancel_stops_at_step_boundary_keeps_writes(tmp_path):
    _save(tmp_path)
    rev0 = _rev(["s01", "s02", "s03", "s04"])
    cancel_event = asyncio.Event()

    client = _StreamClient(
        [{"tool_calls": [_tc("story.create",
                             {"summary": "停止前已建段", "characters": ["LinXia"],
                              "expected_scenes_revision": rev0}, "c1")]},
         {"tool_calls": [_tc("story.list", {}, "l9")]}],   # 第二步，取消后不应到达
        # 第一次 LLM 调用前就置位取消 → 第一步工具执行完，第二步顶停止
        hook=lambda: cancel_event.set())

    rt = _runtime(tmp_path, client)
    sid = rt.get_or_create_session("demo", "script_writer", "project", "demo")["session_id"]
    res = _run(script_writer_turn(rt, "demo", sid, "加一段",
                                  event_cb=lambda e: None, cancel_event=cancel_event))

    # STOPPED 收尾
    assert res["message"]["status"] == "stopped"
    assert "停止" in res["assistant_message"]
    # 第一步的写已真实落盘保留
    ids = [s.scene_id for s in ProjectStore(tmp_path).load().scenes]
    assert "s05" in ids
    # 只发了 1 次 LLM 调用（第二步被步骤边界的取消拦下，没到达）
    assert len(client.script) == 1


# ============ HTTP 层：资源事件 / cancel 端点 / 互斥 ============
@pytest.fixture
def http_ctx(tmp_path, client):
    """把单例 runtime 指向 tmp + scripted client；重置总线与注册表。返回 client + project。"""
    reset_project_bus()
    agents_router._ACTIVE_TURN_EVENTS.clear()
    reset_agent_runtime()
    rt = get_agent_runtime()

    project = "demo"
    _save(tmp_path)
    rt._project_dir_resolver = lambda name: tmp_path
    sc = _StreamClient([
        {"tool_calls": [_tc("story.list", {}, "l1")]},
        {"tool_calls": [_tc("story.create",
                            {"summary": "HTTP事件段", "characters": ["LinXia"],
                             "expected_scenes_revision": _rev(["s01", "s02", "s03", "s04"])},
                            "c1")]},
        {"content": "已新增一段。"},
    ])
    rt._client = sc

    bus = get_project_bus()
    published = []
    orig_pub = bus.publish

    def rec(proj, ev):
        published.append(ev.get("type"))
        return orig_pub(proj, ev)
    bus.publish = rec

    # 建会话
    r = client.post(f"/api/project/{project}/agents/sessions",
                    json={"agent_id": "script_writer", "scope_type": "project",
                          "scope_id": project})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    yield client, project, sid, bus, published

    agents_router._ACTIVE_TURN_EVENTS.clear()
    reset_project_bus()


def test_stream_emits_lock_change_unlock(http_ctx):
    client, project, sid, bus, published = http_ctx
    with client.stream("POST",
                       f"/api/project/{project}/agents/sessions/{sid}/turn/stream",
                       json={"content": "加一段", "thinking": False}) as resp:
        body = b"".join(resp.iter_bytes())
    text = body.decode("utf-8", "replace")
    assert "turn.completed" in text
    # 写工具生命周期 → 总线事件顺序
    assert "resource.locked" in published
    assert "story.changed" in published
    assert "resource.unlocked" in published
    assert published.index("resource.locked") < published.index("story.changed") < published.index("resource.unlocked")
    # turn 结束后锁已释放
    assert bus.is_locked(project, RESOURCE_STORY) is False
    # 注册表里 turn 已注销
    assert agents_router._turn_key(project, sid) not in agents_router._ACTIVE_TURN_EVENTS


def test_cancel_endpoint_sets_event(http_ctx):
    client, project, sid, bus, published = http_ctx
    import asyncio as _aio
    ev = _aio.Event()
    agents_router._ACTIVE_TURN_EVENTS[agents_router._turn_key(project, sid)] = ev
    r = client.post(f"/api/project/{project}/agents/sessions/{sid}/turn/cancel")
    assert r.status_code == 200 and r.json()["stopped"] is True
    assert ev.is_set()
    # 无活跃任务 → stopped=False
    agents_router._ACTIVE_TURN_EVENTS.clear()
    r2 = client.post(f"/api/project/{project}/agents/sessions/sess_nonexistent/turn/cancel")
    assert r2.json()["stopped"] is False


def test_concurrent_turn_mutex_409(http_ctx):
    client, project, sid, bus, published = http_ctx
    import asyncio as _aio
    # 预置一个活跃 turn（模拟上一个还在后台跑）
    agents_router._ACTIVE_TURN_EVENTS[agents_router._turn_key(project, sid)] = _aio.Event()
    r = client.post(f"/api/project/{project}/agents/sessions/{sid}/turn/stream",
                    json={"content": "又来一个", "thinking": False})
    assert r.status_code == 409


def test_resource_locks_endpoint_authoritative(http_ctx):
    """GET /resource-locks 返回总线此刻真实持有的锁（SSE 对账兜底）：
    无 turn 时空；锁定时返回 story；解锁后回空。"""
    client, project, sid, bus, published = http_ctx
    # 无活跃锁
    r = client.get(f"/api/project/{project}/resource-locks")
    assert r.status_code == 200
    assert [l.get("resource") for l in r.json()["locks"]] == []

    # 手动置锁（模拟 Agent 写工具进行中）
    bus.lock(project, RESOURCE_STORY, by="script_writer", session_id=sid)
    r = client.get(f"/api/project/{project}/resource-locks")
    locks = r.json()["locks"]
    assert [l.get("resource") for l in locks] == [RESOURCE_STORY]
    assert locks[0]["by"] == "script_writer"

    # 解锁后回空（即前端对账看到的“真相”：SSE unlock 事件丢了也能据此清锁）
    bus.unlock(project, RESOURCE_STORY)
    r = client.get(f"/api/project/{project}/resource-locks")
    assert [l.get("resource") for l in r.json()["locks"]] == []


def test_resource_locks_empty_after_turn(http_ctx):
    """完整跑一个写 turn 后，权威锁状态必须为空（前端对账兜底的最终态）。"""
    client, project, sid, bus, published = http_ctx
    with client.stream("POST",
                       f"/api/project/{project}/agents/sessions/{sid}/turn/stream",
                       json={"content": "加一段", "thinking": False}) as resp:
        b"".join(resp.iter_bytes())
    r = client.get(f"/api/project/{project}/resource-locks")
    assert r.status_code == 200
    assert r.json()["locks"] == []


# ============ 终端事件可靠性（QueueFull 不丢终端信号）============
def test_put_terminal_event_drains_full_queue():
    """队列已满时，_put_terminal_event 先清空 unconsumed 再入队——终端事件不丢。

    旧 put_nowait 行为：QueueFull 静默丢 → 消费者永挂 q.get()。"""
    q = asyncio.Queue(maxsize=2)
    q.put_nowait({"type": "tool.progress", "message": "p1"})
    q.put_nowait({"type": "tool.progress", "message": "p2"})   # 队列满
    terminal = {"type": "turn.completed", "result": {"ok": True}}
    agents_router._put_terminal_event(q, terminal)             # 不抛 QueueFull
    drained = []
    while not q.empty():
        drained.append(q.get_nowait())
    assert terminal in drained            # 终端事件存活
    assert drained[-1] == terminal        # 且在队尾（消费者最后收到）


def test_put_terminal_event_not_full_passthrough():
    """队列未满时，终端事件直接入队。"""
    q = asyncio.Queue(maxsize=4)
    terminal = {"type": "turn.failed", "error": "boom"}
    agents_router._put_terminal_event(q, terminal)
    assert q.get_nowait() == terminal


def test_turn_failed_emits_terminal(http_ctx, monkeypatch):
    """_turn_fn 抛异常时，turn.failed 终端事件必达 + 流正常收尾 + 锁释放。

    覆盖 SSE _run 的 except Exception → _put_terminal(turn.failed) 路径
    （此前正常/取消路径已测，turn.failed 路径未测）。"""
    client, project, sid, bus, published = http_ctx

    async def _boom(*a, **kw):
        raise RuntimeError("provider boom")
    # SSE 端点在函数内 from src.agents.script_writer import script_writer_turn，
    # monkeypatch 模块属性后该 import 取到 patched 版本 → _turn_fn 抛 → turn.failed
    monkeypatch.setattr("src.agents.script_writer.script_writer_turn", _boom)

    with client.stream("POST",
                       f"/api/project/{project}/agents/sessions/{sid}/turn/stream",
                       json={"content": "加一段", "thinking": False}) as resp:
        body = b"".join(resp.iter_bytes())
    text = body.decode("utf-8", "replace")
    assert "turn.failed" in text
    assert "provider boom" in text
    # 流收尾：注册表注销（消费者不挂）
    assert agents_router._turn_key(project, sid) not in agents_router._ACTIVE_TURN_EVENTS
    # 失败路径也释放锁
    assert bus.is_locked(project, RESOURCE_STORY) is False
