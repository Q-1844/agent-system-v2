"""AI-7D-R · 项目级实时事件总线测试。

覆盖 ProjectEventBus（发布订阅 / 资源锁幂等 / 锁快照 replay / 慢消费者丢事件）
+ /events SSE 端点（首帧 snapshot、后续事件帧、断开退订）。
"""
import asyncio
import json

import pytest

from src.agents.project_events import (
    ProjectEventBus, get_project_bus, reset_project_bus, RESOURCE_STORY)


@pytest.fixture(autouse=True)
def _fresh_bus():
    reset_project_bus()
    yield
    reset_project_bus()


def _drain_nowait(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except asyncio.QueueEmpty:
            return out


def test_pubsub_per_project_isolation():
    bus = ProjectEventBus()
    qa = bus.subscribe("projA")
    qb = bus.subscribe("projB")
    bus.publish("projA", {"type": "story.changed"})
    a = _drain_nowait(qa)
    b = _drain_nowait(qb)
    assert len(a) == 1 and a[0]["type"] == "story.changed"
    assert a[0]["project"] == "projA" and "at" in a[0]
    assert b == []   # 项目隔离：B 收不到 A 的事件


def test_unsubscribe_stops_events():
    bus = ProjectEventBus()
    q = bus.subscribe("p")
    bus.unsubscribe("p", q)
    bus.publish("p", {"type": "story.changed"})
    assert _drain_nowait(q) == []


def test_lock_unlock_idempotent_and_events():
    bus = ProjectEventBus()
    q = bus.subscribe("p")
    # 首次锁定 → True + locked 事件
    assert bus.lock("p", RESOURCE_STORY, by="script_writer", session_id="s1") is True
    assert bus.is_locked("p", RESOURCE_STORY) is True
    assert bus.is_locked("p") is True
    # 重复锁 → 幂等 False，不重复发事件
    assert bus.lock("p", RESOURCE_STORY, by="script_writer", session_id="s1") is False
    # 解锁 → True + unlocked 事件
    assert bus.unlock("p", RESOURCE_STORY) is True
    assert bus.is_locked("p", RESOURCE_STORY) is False
    # 重复解锁 → False
    assert bus.unlock("p", RESOURCE_STORY) is False

    evs = _drain_nowait(q)
    types = [e["type"] for e in evs]
    assert types == ["resource.locked", "resource.unlocked"]
    assert evs[0]["resource"] == RESOURCE_STORY and evs[0]["by"] == "script_writer"


def test_snapshot_replays_current_locks():
    bus = ProjectEventBus()
    # 锁定后【新】订阅者的快照应包含该锁
    bus.lock("p", RESOURCE_STORY, by="script_writer", session_id="s9")
    snap = bus.snapshot("p")
    assert len(snap) == 1
    assert snap[0]["type"] == "resource.locked"
    assert snap[0]["resource"] == RESOURCE_STORY
    assert snap[0]["session_id"] == "s9"
    # 解锁后快照为空
    bus.unlock("p", RESOURCE_STORY)
    assert bus.snapshot("p") == []


def test_slow_consumer_drops_oldest_not_blocking():
    """队列满时丢最旧事件腾位置，publish 不阻塞、不抛。"""
    bus = ProjectEventBus()
    q = bus.subscribe("p")   # maxsize=256
    for i in range(300):
        bus.publish("p", {"type": "story.changed", "seq": i})
    got = _drain_nowait(q)
    assert len(got) <= 256           # 没无限堆积
    assert got[-1]["seq"] == 299     # 最新事件保住
    assert got[0]["seq"] > 0         # 最旧的被丢


def test_get_bus_singleton():
    assert get_project_bus() is get_project_bus()


# ---------- /events SSE 端点 ----------
class _FakeRequest:
    """is_disconnected 前 before 次返回 False，之后 True（控制 gen 循环退出，
    避免走到 15s 超时心跳）。"""
    def __init__(self, before_disconnect=1):
        self._n = 0
        self._before = before_disconnect

    async def is_disconnected(self):
        self._n += 1
        return self._n > self._before


def test_events_endpoint_snapshot_then_event():
    from routers.agents import project_events
    bus = get_project_bus()
    # 先锁资源（模拟打开页面前 turn 已在跑）
    bus.lock("demo", RESOURCE_STORY, by="script_writer", session_id="s1")

    req = _FakeRequest(before_disconnect=1)

    async def run():
        resp = await project_events(req, "demo")   # 函数体在此订阅总线
        # 推一个变更事件（端点已订阅，能收到）
        bus.emit_story_changed("demo", by="script_writer", operation="story.create_many")
        frames = []
        async for chunk in resp.body_iterator:
            frames.append(chunk)
        return frames

    frames = asyncio.run(run())
    text = "".join(frames)
    # 首帧 snapshot 含已锁资源
    assert '"type": "snapshot"' in text
    assert "resource.locked" in text
    # 后续 story.changed 帧
    assert "story.changed" in text
    assert "story.create_many" in text
    # 断开后端点已退订（总线里 demo 无订阅者）
    assert bus._subs.get("demo") in (None, [])
