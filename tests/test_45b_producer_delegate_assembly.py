# tests/test_45b_producer_delegate_assembly.py
# Phase 45.2 — Producer→Assembly 受约束委派（零 ffmpeg、零 LLM）。
# 覆盖：第 5 个 delegate 工具面、registry 授权、_delegate_specialist assembly 分支
# （父子任务/锁接线/changed_refs.sequences/assembly_evidence/bridge 消息）、
# sync-delegate 中断恢复（settlement）、No Auto Select（无 select 工具）。
import asyncio
import json

import pytest

from src.agents.producer import (
    DELEGATE_TOOLS,
    _build_producer_tools,
    _delegate_specialist,
    _derive_changed_refs,
    _wrap_task_result,
)
from src.agents.registry import Registries
from src.agents.runtime import AgentRuntime
from src.agents.settlement import AgentTaskSettler
from src.agents.tasks import AgentTaskStore
from src.agents import turn_registry
from src.core.models import Project, Scene, Shot, VideoVersion
from src.core.store import ProjectStore

from tests.test_44b_assembly_fixture import (
    build_media_project,
    build_snapshot_manifest,
    write_snapshot_file,
)


def _runtime(tmp_path, client=None, queue=None):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client,
                        job_queue_getter=(lambda: queue) if queue else None)


@pytest.fixture(autouse=True)
def _fake_ffmpeg_probe(monkeypatch):
    from tests.test_44b_assembly_fixture import fake_probe
    monkeypatch.setattr("src.sequence.media_ops.probe_video", fake_probe)


@pytest.fixture(autouse=True)
def _clean_turn_registry():
    turn_registry.clear()
    yield
    turn_registry.clear()


# ============ 工具面 / registry ============

def test_assembly_delegate_in_tool_surface():
    offered, executors = _build_producer_tools()
    names = {t["function"]["name"] for t in offered}
    assert "assembly.delegate" in names
    assert "assembly.sequence_read" in names
    assert "assembly.render_status" in names
    # delegate executor 由 producer_turn 以 workflow 闭包注入（不在 _build_producer_tools 返回值）
    assert "assembly.delegate" not in executors
    assert "assembly.sequence_read" in executors
    assert "assembly.render_status" in executors
    # 只读工具不下发写面
    assert not any(n.startswith("assembly.sequence_create") or
                   n.startswith("assembly.audio_plan_put") for n in names)


def test_delegate_tools_count_and_registry_authorization():
    assert DELEGATE_TOOLS == {
        "script_writer.delegate", "character_designer.delegate",
        "scene_designer.delegate", "director.delegate", "assembly.delegate"}
    reg = Registries()
    assert reg.agents.can_delegate("producer", "assembly") is True
    # Specialist 之间禁止互相委派（既有纪律）
    assert reg.agents.can_delegate("assembly", "director") is False


# ============ delegate 执行器（assembly 分支）============

def _receipt(op, **extra):
    return {"ok": True, "operation": op, "verified": True, **extra}


def _setup_project(tmp_path):
    files = build_media_project(tmp_path)
    snap = build_snapshot_manifest(files)
    write_snapshot_file(tmp_path, snap)
    return files


def test_delegate_assembly_happy_path(tmp_path, monkeypatch):
    _setup_project(tmp_path)
    proj_name = "p45d"
    ProjectStore(tmp_path).save(Project(name=proj_name))

    receipts = [
        _receipt("assembly.sequence_create", sequence_id="seq_new1",
                 revision="revA", clip_count=3, target="seq_new1"),
        _receipt("assembly.audio_plan_put", sequence_id="seq_new1",
                 revision="planE", target="seq_new1"),
        _receipt("assembly.render_submit", sequence_id="seq_new1",
                 render_version_id="srender_n1", job_id="job_9",
                 target="seq_new1"),
        _receipt("assembly.manifest_publish", sequence_id="seq_new1",
                 assembly_revision=7, audio_plan_revision="planE",
                 target="seq_new1"),
        _receipt("assembly.render_status", sequence_id="seq_new1",
                 deep_audit={"audio": {"passed": True, "failures": []}}),
    ]

    async def fake_assembly_turn(rt, project, sid, content, *, thinking=False,
                                 event_cb=None, cancel_event=None):
        # 模拟 assembly 工具期间的锁事件（流经 bridge）
        if event_cb:
            event_cb({"type": "tool.started", "name": "assembly.sequence_create",
                      "step_id": 1, "call_id": "c1"})
            event_cb({"type": "tool.completed", "name": "assembly.sequence_create",
                      "ok": True, "step_id": 1, "call_id": "c1"})
        return {"assistant_message": "成片候选已产出", "message": {"status": None},
                "receipts": receipts, "trace": [{"tool": "x"}] * 3,
                "events": [], "intent": "execution"}

    monkeypatch.setattr("src.agents.assembly.assembly_turn", fake_assembly_turn)

    rt = _runtime(tmp_path)
    events = []

    async def run():
        return await _delegate_specialist(
            rt, proj_name, "assembly",
            {"objective": "装配全片成片并配全片旁白",
             "constraints": ["原音静默", "分场景配乐"],
             "scope": {}, "situation": "用户要求成片", "user_intent": "做成片"},
            event_cb=events.append, cancel_event=None,
            prev_task_id="task_parent1", workflow_id="wf_1")

    tr = asyncio.run(run())

    assert tr["status"] == "completed" and tr["verified"] is True
    assert tr["ok"] is True
    assert tr["changed_refs"]["sequences"] == ["seq_new1"]
    assert tr["assembly_evidence"] == {
        "sequence_ids": ["seq_new1"], "render_version_id": "srender_n1",
        "render_job_id": "job_9", "audio_plan_revision": "planE",
        "manifest_revision": 7, "audio_audit_passed": True}

    # 父子任务持久化：parent_task_id 链 + 结算
    store = AgentTaskStore(tmp_path, proj_name)
    t = store.get(tr["task_id"])
    assert t is not None and t.parent_task_id == "task_parent1"
    assert t.status == "completed" and t.target_agent == "assembly"
    assert t.capability_id == "assembly.delegate"
    assert {"type": "sequence", "id": "seq_new1"} in t.artifact_refs

    # 子会话由真实 turn fn 落盘（此处 fake 不写）；委托方校验会话 id 一致
    sess_store = rt._session_store(proj_name)
    sess = sess_store.load(t.session_id)
    assert sess is None or sess.agent_id == "assembly"

    # bridge 消息两条
    bridge = (tmp_path / "output" / "agents" / "producer" /
              "bridge_messages.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(bridge) == 2
    assert json.loads(bridge[0])["receiver"] == "assembly"
    assert json.loads(bridge[1])["changed_refs"]["sequences"] == ["seq_new1"]

    # 锁事件流经 bridge（sequence 域 locked）
    kinds = [e.get("type") for e in events]
    assert "tool.started" in kinds


def test_delegate_assembly_lock_released_on_failure(tmp_path, monkeypatch):
    _setup_project(tmp_path)
    proj_name = "p45dl"
    ProjectStore(tmp_path).save(Project(name=proj_name))

    async def boom(*a, **kw):
        raise RuntimeError("child exploded")

    monkeypatch.setattr("src.agents.assembly.assembly_turn", boom)
    rt = _runtime(tmp_path)
    tr = asyncio.run(_delegate_specialist(
        rt, proj_name, "assembly", {"objective": "x"}, event_cb=None,
        cancel_event=None, prev_task_id=None, workflow_id="wf_2"))
    assert tr["status"] == "failed"
    assert "child exploded" in tr["summary"]
    # 会话槽位已释放
    sess_store = rt._session_store(proj_name)
    sid = sess_store.load(
        AgentTaskStore(tmp_path, proj_name).get(tr["task_id"]).session_id).session_id
    assert turn_registry.is_active(proj_name, sid) is False


def test_delegate_assembly_missing_objective(tmp_path):
    rt = _runtime(tmp_path)
    tr = asyncio.run(_delegate_specialist(
        rt, "p45x", "assembly", {"objective": " "}, event_cb=None,
        cancel_event=None, prev_task_id=None, workflow_id="wf_3"))
    assert tr["status"] == "failed" and tr["code"] == "missing_objective"
    assert tr["changed_refs"]["sequences"] == []


def test_delegate_assembly_concurrent_second_gets_busy(tmp_path, monkeypatch):
    """45.3 并发：两个协调任务同时驱动同一 assembly 会话 → 第二个结构化 busy，
    不静默竞争同一会话。"""
    import asyncio as _aio
    _setup_project(tmp_path)
    proj_name = "p45cc"
    ProjectStore(tmp_path).save(Project(name=proj_name))
    release = _aio.Event()
    started = _aio.Event()

    async def slow_turn(rt, project, sid, content, *, thinking=False,
                        event_cb=None, cancel_event=None):
        started.set()
        await release.wait()
        return {"assistant_message": "ok", "message": {"status": None},
                "receipts": [], "trace": []}

    monkeypatch.setattr("src.agents.assembly.assembly_turn", slow_turn)
    rt = _runtime(tmp_path)

    async def scenario():
        first = _aio.create_task(_delegate_specialist(
            rt, proj_name, "assembly", {"objective": "任务一"},
            event_cb=None, cancel_event=None, prev_task_id=None,
            workflow_id="wf_a"))
        await _aio.wait_for(started.wait(), 5)
        second = await _delegate_specialist(
            rt, proj_name, "assembly", {"objective": "任务二"},
            event_cb=None, cancel_event=None, prev_task_id=None,
            workflow_id="wf_b")
        release.set()
        return await first, second

    first_res, second_res = asyncio.run(scenario())
    assert first_res["status"] == "completed"
    assert second_res["status"] == "failed"
    assert second_res["code"] == "specialist_busy"


# ============ changed_refs / wrap 纯派生 ============

def test_derive_changed_refs_assembly_only():
    refs = _derive_changed_refs("assembly", [
        _receipt("assembly.sequence_create", sequence_id="seq_a"),
        _receipt("assembly.render_status", sequence_id="seq_b"),   # 只读不记
        _receipt("assembly.tts_synthesize", cue_id="nd01"),        # 无 sequence_id
        {"ok": True, "operation": "assembly.sequence_update", "verified": False,
         "sequence_id": "seq_c"},                                  # 未 verified 不记
    ])
    assert refs["sequences"] == ["seq_a"]
    assert refs["scenes"] == [] and refs["shots"] == []


def test_wrap_task_result_no_select_overreach():
    tr = _wrap_task_result(
        {"assistant_message": "done", "message": {"status": None},
         "receipts": [_receipt("assembly.proposal_write", target="proposal_v1.md",
                               proposal_file="proposal_v1.md")],
         "trace": []},
        None, "task_z", "wf_z", "assembly", "sess_z")
    assert tr["verified"] is True
    assert "select" not in json.dumps(tr["assembly_evidence"])
    assert tr["assembly_evidence"]["sequence_ids"] == []


# ============ settlement：sync-delegate 中断恢复 ============

def _mk_task(store, tmp_path, *, status="running", session_id="sess_child",
             project="p45s"):
    from src.agents.models import AgentTask, AgentOp, ScopeType
    t = AgentTask(task_id="task_s1", project=project,
                  requester_agent="producer", target_agent="assembly",
                  capability_id="assembly.delegate",
                  instruction="装配成片", scope_type=ScopeType.PROJECT.value,
                  scope_id="", session_id=session_id,
                  operation=AgentOp.EXECUTE.value, status=status)
    store.create(t)
    return t


def test_settlement_interrupted_delegate_marked_failed(tmp_path):
    proj = "p45s"
    ProjectStore(tmp_path).save(Project(name=proj))
    store = AgentTaskStore(tmp_path, proj)
    _mk_task(store, tmp_path)
    settler = AgentTaskSettler(lambda: [], lambda: None)
    # Phase 47 boot 守卫：中断判定只对【上个 boot】遗留 task 生效——
    # 把任务 created_at 回溯到 settler boot 之前，模拟重启遗留。
    import time as _t
    store.update("task_s1", lambda tt: setattr(tt, "created_at",
                                              _t.time() - (settler._boot_wall - _t.time()) - 60))
    task = store.list(status="running")[0]
    decision = settler.reconcile_task(store, task)
    assert decision is not None and decision["sync_interrupted"] is True
    store.update("task_s1", lambda tt, d=decision: _apply(tt, d))
    t = store.get("task_s1")
    assert t.status == "failed"
    assert any(w.startswith("interrupted_by_backend_restart")
               for w in (t.structured_result.get("warnings") or []))


def _apply(t, d):
    from src.agents.settlement import _apply_decision
    _apply_decision(t, d)


def test_settlement_live_delegate_untouched(tmp_path):
    proj = "p45sl"
    ProjectStore(tmp_path).save(Project(name=proj))
    store = AgentTaskStore(tmp_path, proj)
    _mk_task(store, tmp_path, session_id="sess_live", project=proj)
    turn_registry.acquire(proj, "sess_live", None)   # 子会话运行中
    settler = AgentTaskSettler(lambda: [], lambda: None)
    task = store.list(status="running")[0]
    assert settler.reconcile_task(store, task) is None
    t = store.get("task_s1")
    assert t.status == "running"


def test_settlement_jobless_done_task_untouched(tmp_path):
    # 非 running 的历史 task 不在对账范围
    proj = "p45sc"
    ProjectStore(tmp_path).save(Project(name=proj))
    store = AgentTaskStore(tmp_path, proj)
    _mk_task(store, tmp_path, status="completed", project=proj)
    settler = AgentTaskSettler(lambda: [], lambda: None)
    assert store.list(status="running") == []
    assert settler.reconcile_task(store, store.get("task_s1")) is None
