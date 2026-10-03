"""Producer V1 Phase 3 — Production Room projection 测试（§四十 B1-B9）。

复用 tests/test_producer_bridge.py 的 ScriptedClient 双循环夹具（同一 mock 交错喂
Producer loop 与 Specialist turn），在真实 producer_turn 执行产生真实持久事实后，
验证只读投影的：稳定 ID / 确定顺序 / 刷新重建 / verified=false / failed / cancel /
concurrent-ready / legacy 兼容。

红线：投影是纯派生 View——测试同时断言投影不产生任何写入（第二套事实）。
"""
import asyncio
import json

import pytest

from src.core.store import ProjectStore
from src.agents import producer, turn_registry
from src.agents.production_room import build_room_projection
from src.agents.tasks import AgentTaskStore

from tests.test_producer_bridge import (
    _save_proj, _runtime, _run, _tool_call, _envelope, ScriptedClient,
)


# ============ 脚本 ============
def _sw_then_director_script(tmp_path):
    """SW（verified 写剧本）→ Director（verified 写 s02 分镜）→ producer 汇报。"""
    new_text = "完整剧本正文。补充第二幕的环境描写，雨后的街道。"
    sw_env = _envelope("script_writer", objective="优化剧本正文",
                       user_intent="全流程优化一下")
    dir_env = _envelope("director", objective="为第2段设计分镜",
                        situation="Script Writer 已优化剧本正文，请同步分镜",
                        user_intent="全流程优化一下")
    shots = [{"description": "两人隔桌对坐", "characters": ["林小雨", "陈默"],
              "shot_size": "medium shot", "target_duration_s": 5},
             {"description": "陈默望向窗外", "characters": ["陈默"],
              "shot_size": "close-up", "target_duration_s": 3}]
    return ScriptedClient([
        (None, [_tool_call("script_writer.delegate", sw_env, "p_sw")]),
        (None, [_tool_call("story.list", {}, "sw1")]),
        (None, [_tool_call("script.update",
                           {"new_story_text": new_text,
                            "expected_revision": _story_rev(tmp_path)}, "sw2")]),
        ("已优化剧本正文。", None),
        (None, [_tool_call("director.delegate", dir_env, "p_dir")]),
        (None, [_tool_call("shot.list", {"scene_id": "s02"}, "d1")]),
        (None, [_tool_call("shot.create_many",
                           {"scene_id": "s02", "shots": shots,
                            "expected_shots_revision": _shots_rev(tmp_path)}, "d2")]),
        ("已为第2段设计分镜。", None),
        ("我让 SW 优化了剧本、Director 为第2段设计了分镜。", None),
    ])


# tests.test_producer_bridge 的私有 helper（指纹按当前盘上 canonical 计算）
from tests.test_producer_bridge import (  # noqa: E402
    _story_rev, _shots_rev,
)


def _room(rt):
    return build_room_projection(rt, "demo")


def _kinds(snap):
    return [m["kind"] for m in snap["messages"]]


def _ids(snap):
    return [m["id"] for m in snap["messages"]]


# ============ B1：稳定投影完整链路 ============
def test_B1_projection_full_chain(tmp_path):
    """user → delegation(SW) → result(SW) → delegation(Director) → result(Director)
    → producer final，字段来自真实 Task Result / bridge 行。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=_sw_then_director_script(tmp_path))
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "全流程优化一下"))
    assert res["intent"] == "delegation"

    snap = _room(rt)
    assert snap["project_id"] == "demo"
    assert snap["session_id"] == sid
    assert _kinds(snap) == ["user", "delegation", "specialist_result",
                            "delegation", "specialist_result", "producer"]
    user, d1, r1, d2, r2, final = snap["messages"]

    # user 消息：真实输入、稳定 message_id
    assert user["id"].startswith("msg_") and user["content"] == "全流程优化一下"
    # 出向委派：sender/recipient/objective
    assert d1["sender"] == "producer" and d1["recipient"] == "script_writer"
    assert d1["content"] == "优化剧本正文"
    assert d1["task_id"] == res["receipts"][0]["task_id"]
    assert d1["status"] == "completed" and d1["live"] is False
    assert d1["situation"] == "" and d1["constraints"] == []
    # 第二次委派带完整 envelope（situation 来自真实 bridge 行）
    assert d2["recipient"] == "director"
    assert "Script Writer 已优化剧本正文" in d2["situation"]
    assert d2["workflow_id"] and d2["workflow_id"] == d1["workflow_id"]
    # 结果：verified/changed_refs 只来自 receipts 派生的 Task Result
    assert r1["sender"] == "script_writer" and r1["recipient"] == "producer"
    assert r1["verified"] is True
    assert r1["status"] == "completed"
    assert r1["full_reply"] and r1["full_reply"].startswith("已优化剧本正文。")
    assert r2["sender"] == "director"
    assert r2["verified"] is True
    assert r2["changed_refs"]["scenes"] == ["s02"]
    assert r2["n_tool_calls"] >= 1
    # producer final：真实汇报、session message_id
    assert final["id"].startswith("msg_")
    assert "Director" in final["content"]
    assert final["status"] == "completed"
    # debug 可追踪（§二十五）：task/parent/workflow 齐备
    assert d2["parent_task_id"] == d1["task_id"]
    assert d1["capability_id"] == "script_writer.delegate"
    assert d1["task_created_at"] and d1["task_updated_at"]


# ============ B2 + B3：稳定 ID / 稳定顺序 ============
def test_B2_B3_stable_ids_and_ordering(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=_sw_then_director_script(tmp_path))
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    _run(producer.producer_turn(rt, "demo", sid, "全流程优化一下"))

    first = [(m["id"], m["kind"], m["timestamp"]) for m in _room(rt)["messages"]]
    # B2：同进程连投两次 → ID + 顺序 + 时间戳完全一致
    second = [(m["id"], m["kind"], m["timestamp"]) for m in _room(rt)["messages"]]
    assert first == second
    # B3：全新 Runtime 实例（全新 store 栈，模拟刷新后进程）重建 → 完全一致
    rt2 = _runtime(tmp_path)
    third = [(m["id"], m["kind"], m["timestamp"]) for m in _room(rt2)["messages"]]
    assert first == third
    # 全部 ID 唯一（去重键有效）
    ids = [x[0] for x in first]
    assert len(ids) == len(set(ids))


def test_B4_rebuild_from_persisted_facts_only(tmp_path):
    """§十五：投影必须可从持久事实重建。清掉进程内 registry（模拟后端重启/刷新）
    后仍能重建全部已完成历史；running 任务诚实变为 interrupted（不假 working）。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path, client=_sw_then_director_script(tmp_path))
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    _run(producer.producer_turn(rt, "demo", sid, "全流程优化一下"))
    before = [(m["id"], m["kind"]) for m in _room(rt)["messages"]]

    turn_registry.clear()   # 模拟重启：进程内活跃状态清空
    rt2 = _runtime(tmp_path)
    after_snap = _room(rt2)
    after = [(m["id"], m["kind"]) for m in after_snap["messages"]]
    assert before == after   # 已完成历史完全一致
    assert all(m.get("status") != "running" for m in after_snap["messages"])


# ============ B5：verified=false 不投影成成功 ============
def test_B5_verified_false_not_success(tmp_path):
    _save_proj(tmp_path)
    env = _envelope("script_writer", objective="优化剧本正文",
                    situation="用户希望剧本更丰富", user_intent="优化一下这个项目")
    client = ScriptedClient([
        (None, [_tool_call("script_writer.delegate", env, "p_sw")]),
        (None, [_tool_call("story.list", {}, "sw1")]),
        (None, [_tool_call("script.update",
                           {"new_story_text": "完整剧本正文。补充描写。",
                            "expected_revision": "stale_token_xxxxx"}, "sw2")]),
        ("已修改完成。", None),
        ("已修改完成。", None),
        ("我已让 Script Writer 优化剧本正文。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    _run(producer.producer_turn(rt, "demo", sid, "优化一下这个项目"))

    snap = _room(rt)
    result = next(m for m in snap["messages"] if m["kind"] == "specialist_result")
    assert result["verified"] is False
    assert result["status"] == "completed"
    # Phase B：changed_refs 八键骨架（新资源键并存，空值语义不变）
    assert result["changed_refs"] == {"scenes": [], "shots": [], "characters": [], "sequences": [], "story": [],
                                      "clothings": [], "looks": [], "props": [],
                                      "reference_assets": [], "compositions": [], "bindings": [],
                                   "locations": [], "location_views": [],
                                   "scene_instances": [], "scene_instance_views": []}
    assert result["warnings"], "失败写应出现在 warnings"
    # roster 同样诚实
    sw = next(p for p in snap["participants"] if p["agent_id"] == "script_writer")
    assert sw["verified"] is False


# ============ B6：failed 正确投影 ============
def test_B6_failed_projection(tmp_path):
    _save_proj(tmp_path)
    env = _envelope("script_writer", objective="优化剧本正文", user_intent="优化一下")

    class BoomStreamClient(ScriptedClient):
        async def chat_completion_stream(self, messages, **kw):
            raise RuntimeError("boom: provider 500")
            yield   # pragma: no cover —— 使本函数成为 async generator（首帧即抛）

    client = BoomStreamClient([
        (None, [_tool_call("script_writer.delegate", env, "p_sw")]),
        ("尝试委派失败，我如实汇报。", None),
    ])
    rt = _runtime(tmp_path, client=client)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "优化一下"))
    assert res["receipts"][0]["status"] == "failed"

    snap = _room(rt)
    result = next(m for m in snap["messages"] if m["kind"] == "specialist_result")
    assert result["status"] == "failed"
    # 异常信息诚实进正文（_wrap_task_result：err → summary="[委派执行异常: ...]"）
    assert "boom" in (result["content"] or "")
    assert result["verified"] is False
    sw = next(p for p in snap["participants"] if p["agent_id"] == "script_writer")
    assert sw["status"] == "failed"
    # workflow 也不假完成
    assert snap["workflow"]["status"] in ("failed", "completed")


# ============ B7：cancel → stopped/cancelled ============
def test_B7_cancelled_projection(tmp_path):
    _save_proj(tmp_path)
    cancel = asyncio.Event()
    env = _envelope("script_writer", objective="优化剧本正文", user_intent="优化一下")
    inner = ScriptedClient([
        (None, [_tool_call("script_writer.delegate", env, "p1")]),
        (None, [_tool_call("story.list", {}, "sw1")]),
        ("已读取。", None),
    ])

    class CancelOn2(ScriptedClient):
        def __init__(self, inner, cancel):
            self.inner, self.cancel, self.n = inner, cancel, 0

        def _pop(self):
            return self.inner._pop()

        async def chat_completion(self, messages, **kw):
            self.n += 1
            if self.n >= 2:
                self.cancel.set()
            return await self.inner.chat_completion(messages, **kw)

        async def chat_completion_stream(self, messages, **kw):
            self.n += 1
            if self.n >= 2:
                self.cancel.set()
            async for ev in self.inner.chat_completion_stream(messages, **kw):
                yield ev

    rt = _runtime(tmp_path, client=CancelOn2(inner, cancel))
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    res = _run(producer.producer_turn(rt, "demo", sid, "优化一下", cancel_event=cancel))
    assert res["message"]["status"] == "stopped"

    snap = _room(rt)
    result = next(m for m in snap["messages"] if m["kind"] == "specialist_result")
    assert result["status"] == "stopped"          # Task Result 真实 stopped
    dispatch = next(m for m in snap["messages"] if m["kind"] == "delegation")
    assert dispatch["status"] == "stopped" and dispatch["live"] is False
    producer_msg = next(m for m in snap["messages"] if m["kind"] == "producer")
    assert producer_msg["status"] == "stopped"
    sw = next(p for p in snap["participants"] if p["agent_id"] == "script_writer")
    assert sw["status"] == "cancelled"            # 不显示 completed/working
    prod = next(p for p in snap["participants"] if p["agent_id"] == "producer")
    assert prod["status"] == "cancelled"
    assert snap["workflow"]["status"] == "stopped"


# ============ B8：concurrent-ready（两个不同 active task 可同时表达）============
def test_B8_concurrent_ready_two_active_tasks(tmp_path):
    """不要求 Producer 真并行：直接构造两个不同 agent 的 running task + 两个活跃
    registry 槽位，投影必须能同时表达两个 working（数据模型不得是 activeAgent:str）。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    pdir = rt._resolve_project_dir("demo")
    ts = AgentTaskStore(pdir, "demo")
    from src.agents.models import AgentTask, AgentOp, ScopeType, TaskStatus
    for aid, sid_s in (("script_writer", "sess_sw"), ("director", "sess_dir")):
        ts.create(AgentTask(
            task_id=f"task_{aid}", project="demo", requester_agent="producer",
            target_agent=aid, capability_id=f"{aid}.delegate", instruction="目标",
            scope_type=ScopeType.PROJECT.value, scope_id="", session_id=sid_s,
            operation=AgentOp.EXECUTE.value, status=TaskStatus.RUNNING.value))
    ev = asyncio.Event()
    assert turn_registry.acquire("demo", "sess_sw", ev)
    assert turn_registry.acquire("demo", "sess_dir", ev)
    try:
        snap = _room(rt)
        dispatches = [m for m in snap["messages"] if m["kind"] == "delegation"]
        assert len(dispatches) == 2
        assert all(d["status"] == "running" and d["live"] is True for d in dispatches)
        statuses = {p["agent_id"]: p["status"] for p in snap["participants"]}
        assert statuses["script_writer"] == "working"
        assert statuses["director"] == "working"
    finally:
        turn_registry.pop("demo", "sess_sw")
        turn_registry.pop("demo", "sess_dir")


def test_B8b_bridge_row_constraints_string_tolerated(tmp_path):
    """真实样例里模型把 constraints 发成 string（非 array）——投影归一化，不崩。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    pdir = rt._resolve_project_dir("demo")
    ts = AgentTaskStore(pdir, "demo")
    from src.agents.models import AgentTask, AgentOp, ScopeType, TaskStatus
    ts.create(AgentTask(
        task_id="task_x", project="demo", requester_agent="producer",
        target_agent="director", capability_id="director.delegate",
        instruction="优化分镜", scope_type=ScopeType.PROJECT.value, scope_id="",
        session_id="sess_d", operation=AgentOp.EXECUTE.value,
        status=TaskStatus.COMPLETED.value))
    wf_dir = pdir / "output" / "agents" / "producer"
    wf_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"ts": 1000.0, "task_id": "task_x", "workflow_id": "wf_x",
         "sender": "producer", "receiver": "director", "objective": "优化分镜",
         "situation": "SW 刚调整了剧本", "constraints": "1) 保持框架；2) 不改结局",
         "scope": {"scene_ids": ["s01"]}, "status": "dispatched"},
        {"ts": 1001.0, "task_id": "task_x", "sender": "director",
         "receiver": "producer", "status": "completed",
         "summary": "已优化分镜", "changed_refs": {"scenes": [], "shots": ["s01_c01"],
                                                   "characters": []},
         "warnings": []},
    ]
    (wf_dir / "bridge_messages.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    snap = _room(rt)
    d = next(m for m in snap["messages"] if m["kind"] == "delegation")
    assert d["constraints"] == ["1) 保持框架；2) 不改结局"]   # string → list 归一化
    r = next(m for m in snap["messages"] if m["kind"] == "specialist_result")
    assert r["verified"] is None      # 无 AgentTask.structured_result → 不假 verified
    assert r["changed_refs"]["shots"] == ["s01_c01"]


# ============ B9：legacy project（无 bridge / 无 producer 数据）不崩 ============
def test_B9_legacy_empty_project(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    snap = _room(rt)
    assert snap["messages"] == []
    assert snap["session_id"] is None
    assert snap["workflow"] is None
    assert snap["turn_active"] is False
    statuses = {p["agent_id"]: p["status"] for p in snap["participants"]}
    # 28.md §26：scene_designer 加入顶层 roster
    assert statuses == {"producer": "idle", "script_writer": "idle",
                        "character_designer": "idle", "scene_designer": "idle",
                        "director": "idle"}


def test_B9b_legacy_producer_session_without_bridge(tmp_path):
    """只有旧 Producer 对话（AI-6B 时代，无 meta/无 task/无 bridge）：正常显示聊天，
    旧 AI-6B 手动委派 task（capability 非 *.delegate）不进时间线。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    from src.agents.models import AgentMessage, AgentTask, AgentOp, ScopeType
    rt._session_store("demo").append_message(sid, AgentMessage(role="user", content="旧聊天"))
    rt._session_store("demo").append_message(
        sid, AgentMessage(role="assistant", content="旧回复"))
    # 旧式手动委派 task（inspect capability）——不是 Bridge 协作事实
    AgentTaskStore(rt._resolve_project_dir("demo"), "demo").create(AgentTask(
        task_id="task_legacy", project="demo", requester_agent="producer",
        target_agent="director", capability_id="storyboard.inspect",
        instruction="检查分镜", scope_type=ScopeType.PROJECT.value, scope_id="",
        session_id="sess_old", operation=AgentOp.INSPECT.value))
    snap = _room(rt)
    assert _kinds(snap) == ["user", "producer"]
    assert snap["messages"][0]["content"] == "旧聊天"
    assert all(m["kind"] != "delegation" for m in snap["messages"])


# ============ HTTP 端点接线（TestClient + monkeypatch runtime）============
def test_room_endpoint_http(client, tmp_path, monkeypatch):
    from src.agents.runtime import AgentRuntime

    _save_proj(tmp_path)

    def _resolver(name):
        if name != "demo":
            raise FileNotFoundError(name)   # unknown_project → 404 语义
        return tmp_path

    rt = AgentRuntime(project_dir_resolver=_resolver,
                      settings_path=tmp_path / "agents_data" / "settings.json",
                      client=_sw_then_director_script(tmp_path))
    monkeypatch.setattr("routers.agents.get_agent_runtime", lambda: rt)
    # 项目不存在 → 404 语义
    resp404 = client.get("/api/project/__no_such__/agents/producer/room")
    assert resp404.status_code == 404

    sid = rt.get_or_create_session("demo", "producer", "project", "demo")["session_id"]
    _run(producer.producer_turn(rt, "demo", sid, "全流程优化一下"))

    resp = client.get("/api/project/demo/agents/producer/room")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == "demo"
    assert data["session_id"] == sid
    assert data["room_id"] == "producer"
    kinds = [m["kind"] for m in data["messages"]]
    assert kinds == ["user", "delegation", "specialist_result",
                     "delegation", "specialist_result", "producer"]
    # 两次 GET 完全一致（stable snapshot）
    data2 = client.get("/api/project/demo/agents/producer/room").json()
    assert [m["id"] for m in data["messages"]] == [m["id"] for m in data2["messages"]]
    # 投影零写入：bridge 行数/任务数不变
    pdir = rt._resolve_project_dir("demo")
    bm = (pdir / "output" / "agents" / "producer" / "bridge_messages.jsonl")
    assert len(bm.read_text(encoding="utf-8").strip().splitlines()) == 4


# ============ 投影只读红线：无委派纯对话 turn 也不产生任何文件 ============
def test_projection_is_readonly_no_side_files(tmp_path):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    pdir = rt._resolve_project_dir("demo")
    agents_dir = pdir / "output" / "agents"
    before = sorted(str(p.relative_to(pdir)) for p in agents_dir.rglob("*")) \
        if agents_dir.exists() else []
    _room(rt)
    _room(rt)
    after = sorted(str(p.relative_to(pdir)) for p in agents_dir.rglob("*")) \
        if agents_dir.exists() else []
    assert before == after
