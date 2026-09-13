"""AI-7D D3 · Runtime Query / Control Truth 测试。

engine 级：直接驱动 AgentRuntime.unified_turn + monkeypatch 控制函数边界
（cp_exec.cancel/resume_execution、cp_service.approve/reject/request_changes/resume_session/
get_plan、active_workflow_snapshot）→ 不联网、不调真 Agnes、不写真 canonical/ChangeSession。

核心断言（D3.2/D3.3）：
- 控制命令（停止/恢复/批准/拒绝/重规划）→ 调既有后端 action（非 LLM 臆造）；
- 回复是确定性接地自然语言（引用真 status/stage，无假百分比，无 tool-call JSON）；
- 目标解析从真后态快照（active_workflow_snapshot）来；无活跃任务→真话「没有正在进行的任务」；
- query guard：「如果我现在停止会停掉什么？」→ runtime_query（不执行停止），D3 gate 第 3 题；
- approve 未 ready / 目标已执行中 → 真话引导，不误操作；
- 锁：会话类动作（approve/reject/replan/resume_session）经 session_lock（镜像 router）；
  执行类（cancel/resume_execution）直调（自带 project_write_lock）。

镜像 tests/test_ai_7d_d1_unified_turn.py 风格（_runtime/_save_proj/_session_id/_run）。
"""
import asyncio

import pytest

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents import inquiry
from src.agents.change_protocol import service as cp_service
from src.agents.change_protocol import execution_service as cp_exec


# ============ 辅助（镜像 D1 测试）============
def _proj():
    return Project(
        name="demo", style=StyleAnchor(),
        characters=[Character(name="LinXia", description_en="a young woman")],
        scenes=[Scene(scene_id="s01", summary="rain", characters=["LinXia"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="look up", characters=["LinXia"],
                                  target_duration_s=5)])])


def _save_proj(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    return tmp_path


def _raise_unconfigured():
    """hermetic unconfigured（镜像生产 get_agnes_client 的 503 形态）：引擎级控制
    测试的意图分类与接地回复不依赖运行机 credential 状态（Freeze Stabilization
    §5：测试显式制造 NOT_CONFIGURED，杜绝 chunk 内 startup 泄漏改道真 Agnes）。"""
    from fastapi import HTTPException
    raise HTTPException(503, "agnes not configured (hermetic test)")


def _runtime(tmp_path, **kw):
    kw.setdefault("settings_path", tmp_path / "agents_data" / "settings.json")
    kw.setdefault("agnes_getter", _raise_unconfigured)
    kw.setdefault("sensenova_getter", lambda: None)
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path, **kw)


def _session_id(rt, project, agent_id="producer", scope_type="project", scope_id=""):
    return rt.get_or_create_session(project, agent_id, scope_type, scope_id)["session_id"]


def _run(coro):
    return asyncio.run(coro)


class CallLog:
    """记录各控制函数被调用的参数，供断言。"""
    def __init__(self):
        self.cancel = []
        self.resume_exec = []
        self.approve = []
        self.reject = []
        self.replan = []
        self.resume_session = []
        self.get_plan = []


def _patch_control(monkeypatch, log: CallLog, *, snapshot, plan=None,
                   cancel_res=None, resume_exec_res=None,
                   approve_res=None, replan_res=None):
    """monkeypatch runtime_control 调用的所有函数边界。返回 None。"""

    def _snapshot(_pd, _pn, limit=5):
        return snapshot

    async def _cancel(rt, project, execution_id):
        log.cancel.append((project, execution_id))
        return cancel_res or {"execution": {"execution_id": execution_id,
                                            "status": "cancelled", "stage": "character_sync"},
                              "cancelled": True, "creative_committed": True}

    async def _resume_exec(rt, project, execution_id):
        log.resume_exec.append((project, execution_id))
        return resume_exec_res or {"execution": {"execution_id": execution_id,
                                                 "status": "running", "stage": "character_sync"}}

    def _get_plan(rt, project, session_id):
        log.get_plan.append((project, session_id))
        return plan

    def _approve(rt, project, session_id, plan_id, plan_version):
        log.approve.append((project, session_id, plan_id, plan_version))
        return approve_res or {"approved": True, "session_id": session_id,
                               "plan_id": plan_id, "plan_version": plan_version,
                               "status": "approved_for_execution"}

    def _reject(rt, project, session_id, plan_id):
        log.reject.append((project, session_id, plan_id))
        return {"rejected": True, "session_id": session_id, "plan_id": plan_id}

    def _replan(rt, project, session_id, feedback, plan_id):
        log.replan.append((project, session_id, feedback, plan_id))
        return replan_res or {"plan": {"plan_id": "plan_new", "version": 2},
                              "superseded_plan_id": plan_id}

    async def _resume_session(rt, project, session_id):
        log.resume_session.append((project, session_id))
        return {"session_id": session_id, "status": "consulting"}

    monkeypatch.setattr(cp_service, "active_workflow_snapshot", _snapshot)
    monkeypatch.setattr(cp_exec, "cancel_execution", _cancel)
    monkeypatch.setattr(cp_exec, "resume_execution", _resume_exec)
    monkeypatch.setattr(cp_service, "get_plan", _get_plan)
    monkeypatch.setattr(cp_service, "approve_plan", _approve)
    monkeypatch.setattr(cp_service, "reject_plan", _reject)
    monkeypatch.setattr(cp_service, "request_changes", _replan)
    monkeypatch.setattr(cp_service, "resume_session", _resume_session)


# ============ 1. 意图分类（含 query guard 防误触）============
def test_classify_control_routing():
    def _i(txt):
        ti = inquiry.classify_turn_intent(txt)
        return ti["intent"], ti.get("control")

    assert _i("停止当前任务") == ("runtime_control", "stop")
    assert _i("取消刚才那个") == ("runtime_control", "stop")
    assert _i("批准这个方案") == ("runtime_control", "approve")
    assert _i("拒绝") == ("runtime_control", "reject")
    assert _i("重新规划") == ("runtime_control", "replan")
    assert _i("恢复") == ("runtime_control", "resume")
    # D3 gate 第 3 题：假设问句 → runtime_query（不执行停止）
    assert _i("如果我现在停止，会停掉什么？")[0] == "runtime_query"
    # bare「继续」不误判（"继续聊聊"→ conversation）
    assert _i("继续聊聊")[0] in ("conversation", "project_inquiry")
    # 进度查询仍走 runtime_query
    assert _i("现在做到哪了？")[0] == "runtime_query"


def test_classify_no_d1_regression():
    """控制正则不误判 D1/D2 fixture。"""
    assert inquiry.classify_turn_intent("现在剧本有多少场？先不要修改。")["intent"] == "project_inquiry"
    assert inquiry.classify_turn_intent("把结局改成大团圆")["intent"] == "conversation"
    assert inquiry.classify_turn_intent("你好")["intent"] == "conversation"


# ============ 2. stop：活跃 execution → cancel_execution 调用 + 接地回复 ============
def test_control_stop_active_execution(tmp_path, monkeypatch):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    sid = _session_id(rt, "demo", "producer")
    log = CallLog()
    snap = [{"session_id": "cs_t1", "status": "approved_for_execution",
             "request": "扩写中段", "updated_at": 2.0,
             "execution": {"execution_id": "exec_t1", "status": "running",
                           "stage": "character_sync", "production_run_id": None,
                           "characters_synced": [], "planned_scenes": []}}]
    _patch_control(monkeypatch, log, snapshot=snap)

    res = _run(rt.unified_turn("demo", sid, "停止当前任务"))
    assert res["intent"] == "runtime_control"
    assert log.cancel == [("demo", "exec_t1")]   # 真后端 action 被调一次
    msg = res["assistant_message"]
    assert "扩写中段" in msg          # 引用真请求
    assert "停止" in msg or "收尾" in msg
    assert "%" not in msg             # 无假百分比
    assert "{" not in msg             # 无 tool-call JSON 泄漏
    assert res["linked_change_session_id"] == "cs_t1"
    ev = res["events"][0]
    assert ev["kind"] == "runtime_control" and ev["action"] == "stop"
    assert ev["target_execution_id"] == "exec_t1"


# ============ 3. resume：活跃 execution → resume_execution ============
def test_control_resume_execution(tmp_path, monkeypatch):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    sid = _session_id(rt, "demo", "producer")
    log = CallLog()
    snap = [{"session_id": "cs_t2", "status": "approved_for_execution",
             "request": "改角色", "updated_at": 2.0,
             "execution": {"execution_id": "exec_t2", "status": "blocked",
                           "stage": "character_sync", "production_run_id": None,
                           "characters_synced": [], "planned_scenes": []}}]
    _patch_control(monkeypatch, log, snapshot=snap)
    res = _run(rt.unified_turn("demo", sid, "恢复"))
    assert res["intent"] == "runtime_control"
    assert log.resume_exec == [("demo", "exec_t2")]
    assert "改角色" in res["assistant_message"]
    assert res["linked_change_session_id"] == "cs_t2"


# ============ 4. resume：无 execution 但 session 活跃 → resume_session（持锁）============
def test_control_resume_session(tmp_path, monkeypatch):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    sid = _session_id(rt, "demo", "producer")
    log = CallLog()
    snap = [{"session_id": "cs_t3", "status": "routing",
             "request": "扩写", "updated_at": 2.0}]
    _patch_control(monkeypatch, log, snapshot=snap)
    res = _run(rt.unified_turn("demo", sid, "恢复推进"))
    assert log.resume_session == [("demo", "cs_t3")]
    assert log.resume_exec == []   # 无 execution
    assert "扩写" in res["assistant_message"]


# ============ 5. approve：ready_for_review + plan → approve_plan（持锁）============
def test_control_approve_ready(tmp_path, monkeypatch):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    sid = _session_id(rt, "demo", "producer")
    log = CallLog()
    snap = [{"session_id": "cs_t4", "status": "ready_for_review",
             "request": "扩写中段", "updated_at": 2.0}]
    plan = {"plan_id": "plan_t4", "version": 1}
    _patch_control(monkeypatch, log, snapshot=snap, plan=plan)
    res = _run(rt.unified_turn("demo", sid, "批准这个方案"))
    assert log.approve == [("demo", "cs_t4", "plan_t4", 1)]
    msg = res["assistant_message"]
    assert "扩写中段" in msg and "版本 1" in msg
    assert "批准不等于执行" in msg   # §76 硬停止真话


def test_control_approve_not_ready(tmp_path, monkeypatch):
    """方案未 ready → 真话引导，approve_plan 不被调。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    sid = _session_id(rt, "demo", "producer")
    log = CallLog()
    snap = [{"session_id": "cs_t5", "status": "analyzing_dependencies",
             "request": "扩写", "updated_at": 2.0}]
    _patch_control(monkeypatch, log, snapshot=snap, plan={"plan_id": "p", "version": 1})
    res = _run(rt.unified_turn("demo", sid, "批准这个方案"))
    assert log.approve == []   # 未调真 action
    assert "还没准备好" in res["assistant_message"]


def test_control_approve_already_running(tmp_path, monkeypatch):
    """目标有活跃 execution（已批准执行中）→ 不误批准，真话引导停止。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    sid = _session_id(rt, "demo", "producer")
    log = CallLog()
    snap = [{"session_id": "cs_t6", "status": "approved_for_execution",
             "request": "扩写", "updated_at": 2.0,
             "execution": {"execution_id": "exec_t6", "status": "running",
                           "stage": "production", "production_run_id": None,
                           "characters_synced": [], "planned_scenes": []}}]
    _patch_control(monkeypatch, log, snapshot=snap, plan={"plan_id": "p", "version": 1})
    res = _run(rt.unified_turn("demo", sid, "批准"))
    assert log.approve == []
    assert "已批准" in res["assistant_message"] or "执行中" in res["assistant_message"]


# ============ 6. reject / replan ============
def test_control_reject(tmp_path, monkeypatch):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    sid = _session_id(rt, "demo", "producer")
    log = CallLog()
    snap = [{"session_id": "cs_t7", "status": "ready_for_review",
             "request": "扩写", "updated_at": 2.0}]
    _patch_control(monkeypatch, log, snapshot=snap, plan={"plan_id": "plan_t7", "version": 1})
    res = _run(rt.unified_turn("demo", sid, "拒绝这个方案"))
    assert log.reject == [("demo", "cs_t7", "plan_t7")]
    assert "拒绝" in res["assistant_message"] and "未改动" in res["assistant_message"]


def test_control_replan(tmp_path, monkeypatch):
    """replan → request_changes(content 作为 feedback, plan_id)，版本+1。"""
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    sid = _session_id(rt, "demo", "producer")
    log = CallLog()
    snap = [{"session_id": "cs_t8", "status": "ready_for_review",
             "request": "扩写", "updated_at": 2.0}]
    _patch_control(monkeypatch, log, snapshot=snap, plan={"plan_id": "plan_t8", "version": 1})
    res = _run(rt.unified_turn("demo", sid, "重新规划，第二场不要加新角色"))
    assert log.replan == [("demo", "cs_t8", "重新规划，第二场不要加新角色", "plan_t8")]
    assert "版本 2" in res["assistant_message"]   # 新 plan version


# ============ 7. 无活跃任务 → 真话，无 action 调用 ============
def test_control_no_active_target(tmp_path, monkeypatch):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    sid = _session_id(rt, "demo", "producer")
    log = CallLog()
    _patch_control(monkeypatch, log, snapshot=[])
    res = _run(rt.unified_turn("demo", sid, "停止"))
    assert res["intent"] == "runtime_control"
    assert "没有正在进行的任务" in res["assistant_message"]
    assert log.cancel == [] and log.approve == []   # 无 action
    assert res["linked_change_session_id"] is None


# ============ 8. stop 无 execution 但有 session → 真话，不虚构取消 ============
def test_control_stop_no_execution_but_session(tmp_path, monkeypatch):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    sid = _session_id(rt, "demo", "producer")
    log = CallLog()
    snap = [{"session_id": "cs_t9", "status": "ready_for_review",
             "request": "扩写", "updated_at": 2.0}]
    _patch_control(monkeypatch, log, snapshot=snap)
    res = _run(rt.unified_turn("demo", sid, "停止"))
    assert log.cancel == []   # 无 execution 可停，不调 cancel
    assert "准备中" in res["assistant_message"] or "尚未进入执行" in res["assistant_message"]


# ============ 9. 持久化：control assistant 消息含 meta.intent=runtime_control ============
def test_control_persists_meta(tmp_path, monkeypatch):
    _save_proj(tmp_path)
    rt = _runtime(tmp_path)
    sid = _session_id(rt, "demo", "producer")
    log = CallLog()
    snap = [{"session_id": "cs_t10", "status": "approved_for_execution",
             "request": "扩写", "updated_at": 2.0,
             "execution": {"execution_id": "exec_t10", "status": "running",
                           "stage": "character_sync", "production_run_id": None,
                           "characters_synced": [], "planned_scenes": []}}]
    _patch_control(monkeypatch, log, snapshot=snap)
    _run(rt.unified_turn("demo", sid, "停止"))
    rt2 = _runtime(tmp_path)
    s = rt2.get_session("demo", sid)
    asst = [m for m in s["messages"] if m["role"] == "assistant"]
    assert asst and asst[-1]["meta"]["intent"] == "runtime_control"
