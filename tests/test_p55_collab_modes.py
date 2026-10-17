# Phase55 B 定向测试：协作模式注册表与 run 生命周期（src/agents/collab_modes.py）。
#
# 覆盖（任务书 §11.1「注册与固定配置」行）：
#   1. 注册表：五预设齐备、字段完整、validate_start 组合校验（未知模式/缺 story_goal/
#      非法预铺角色/非实验 legacy 策略拒绝）；
#   2. run 启动：快照（模式全文+skill 版本+agent 基线+sha256）落档、参考策略写项目级
#      文件、grant 创建入档、步骤账本推进；
#   3. producer_lead 家族执行编排：预铺并行（CD/SC 各 1 turn）→ grant → producer 单
#      turn 全载荷（story_goal 进目标文本）——全部复用 unified_turn（测试桩替换）；
#   4. workflow_template（六员链）：真实 W09 实例化+校验，引擎用桩验证接线；
#   5. 取消：置位 turn cancel_event + 状态落档 + 终态幂等；
#   6. REST 端点：list（含可用性缺因）/start（非法 400）/get/cancel。
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents import collab_modes as cm
from src.generation.video_contract import resolve_reference_policy

PROJECT = "_test_p55_modes"


# ============ 1. 注册表与组合校验 ============
def test_registry_has_five_presets():
    ids = set(cm.COLLAB_MODES)
    assert ids == {"six_member", "producer_lead", "preflight_producer",
                   "producer_escalate", "combo_recommended"}
    for m in cm.list_modes():
        for k in ("mode_id", "mode_version", "name", "execution", "grant_families",
                  "delegates_enabled", "reference_policy", "review_policy",
                  "concurrency", "context_policy", "notes"):
            assert k in m, f"{m.get('mode_id')} 缺 {k}"
        assert m["schema"] == cm.COLLAB_MODE_SCHEMA


def test_validate_start_errors():
    assert cm.validate_start("nope", {})  # 未知模式
    errs = cm.validate_start("producer_lead", {})
    assert any("story_goal" in e for e in errs)
    # 合法启动零错误
    assert cm.validate_start("producer_lead", {"story_goal": "山口要塞故事"}) == []
    assert cm.validate_start("preflight_producer", {"story_goal": "s"}) == []
    # 篡改预设：非实验 legacy 策略拒绝
    bad = dict(cm.COLLAB_MODES["producer_lead"])
    bad["reference_policy"] = "legacy_allowed"
    cm.COLLAB_MODES["producer_lead"] = bad
    try:
        errs = cm.validate_start("producer_lead", {"story_goal": "s"})
        assert any("legacy" in e for e in errs)
    finally:
        cm.COLLAB_MODES["producer_lead"]["reference_policy"] = "bound_required"


# ============ 2/3. producer_lead 家族：启动→预铺并行→grant→主执行 ============
class FakeRuntime:
    """unified_turn/get_or_create_session 桩：确定性、0 LLM。记录全部调用。"""

    def __init__(self, tmp_path):
        self.tmp = Path(tmp_path)
        self.turns = []            # (agent, content)
        self._turn_gate = asyncio.Event()
        self.agents = {"producer": {}, "character_designer": {}, "scene_designer": {}}

    def _resolve_project_dir(self, project):
        return self.tmp

    def _is_enabled(self, agent_id):
        return True

    def _resolve_client_or_none(self, agent_id=None):
        return SimpleNamespace(chat_completion=lambda *a, **k: {})

    def _resolve_agent(self, agent_id):
        return SimpleNamespace(name=agent_id)

    def get_or_create_session(self, project, agent_id, scope_type="project",
                              scope_id=""):
        return {"session_id": f"sess_{agent_id}"}

    async def unified_turn(self, project, sid, content, thinking=False):
        agent = sid.replace("sess_", "")
        self.turns.append((agent, content))
        return {"assistant_message": f"[{agent}] done",
                "events": [], "receipts": [{"tool": "x"}],
                "changed_refs": ["scene:s01"]}


@pytest.fixture
def fake_rt(tmp_path):
    return FakeRuntime(tmp_path)


def test_start_producer_lead_full_flow(fake_rt):
    async def scenario():
        run = await cm.start_collab_run(
            fake_rt, PROJECT, "producer_lead", {"story_goal": "灰塔信号异常故事"},
            granted_by="tester")
        # 后台任务在当前 loop 中执行——等待其完成
        for _ in range(200):
            cur = cm.load_run(fake_rt.tmp, run["run_id"])
            if cur["status"] in ("completed", "failed", "cancelled"):
                return cur
            await asyncio.sleep(0.01)
        raise AssertionError("run 未在预期时间内结束")

    cur = asyncio.run(scenario())
    assert cur["status"] == "completed", cur.get("result")
    # 快照固定：模式全文 + skill 版本 + agent 基线 + hash
    assert cur["snapshot"]["mode"]["mode_id"] == "producer_lead"
    assert cur["snapshot"]["params"]["story_goal"] == "灰塔信号异常故事"
    assert cur["snapshot_hash"].startswith("sha256:")
    # 步骤账本：grant + producer_turn 完成
    steps = {s["step_id"]: s for s in cur["steps"]}
    assert steps["grant"]["status"] == "done" and cur["grant_ids"]
    assert steps["producer_turn"]["status"] == "done"
    # 参考策略已写项目级活动 run 层
    assert resolve_reference_policy(fake_rt.tmp) == "bound_required"
    pol = json.loads((fake_rt.tmp / "output" / "agents" / "reference_policy.json")
                     .read_text(encoding="utf-8"))
    assert pol["source"] == "mode_run" and pol["run_id"] == cur["run_id"]
    # 目标载荷：故事目标进 producer turn；工作方法/Skill 指令在列
    agent, content = fake_rt.turns[-1]
    assert agent == "producer"
    assert "灰塔信号异常故事" in content
    assert "skill.load" in content and "shot.production.bind" in content


def test_start_preflight_mode_parallel_turns(fake_rt):
    async def scenario():
        run = await cm.start_collab_run(
            fake_rt, PROJECT, "preflight_producer",
            {"story_goal": "回声故事"}, granted_by="tester")
        for _ in range(200):
            cur = cm.load_run(fake_rt.tmp, run["run_id"])
            if cur["status"] in ("completed", "failed", "cancelled"):
                return cur
            await asyncio.sleep(0.01)
        raise AssertionError("run 未结束")

    cur = asyncio.run(scenario())
    assert cur["status"] == "completed", cur.get("result")
    steps = {s["step_id"]: s for s in cur["steps"]}
    assert steps["preflight"]["status"] == "done"
    # CD/SC 各 1 个预铺 turn（并行 gather），加上最后 producer turn = 3
    assert [a for a, _c in fake_rt.turns] == ["character_designer",
                                              "scene_designer", "producer"]
    # 预铺目标含故事目标
    assert "回声故事" in fake_rt.turns[0][1]
    # grant 五族
    gdoc = json.loads((fake_rt.tmp / "output" / "agents" / "grants.json")
                      .read_text(encoding="utf-8"))
    assert any(g["families"] == sorted(cm.ALL_FAMILIES) for g in gdoc["grants"])


def test_start_invalid_mode_rejected_before_side_effects(fake_rt, tmp_path):
    with pytest.raises(ValueError):
        asyncio.run(cm.start_collab_run(fake_rt, PROJECT, "nope",
                                        {"story_goal": "s"}))
    with pytest.raises(ValueError):
        asyncio.run(cm.start_collab_run(fake_rt, PROJECT, "producer_lead", {}))
    # 未产生 run 文件/参考策略/grant
    assert not (tmp_path / "output" / "agents" / "collab_runs").exists()
    assert not (tmp_path / "output" / "agents" / "reference_policy.json").exists()


# ============ 4. 六员链（workflow_template）：真实 W09 实例化+接线 ============
def test_six_member_instantiates_w09(tmp_path, monkeypatch):
    """①六员协作：真实 create_from_template(W09)+validate_definition；
    引擎 start_run 用桩验证接线（workflow 引擎本体由既有 workflow 测试覆盖）。"""
    from src.core.models import Project, Scene, Character, StyleAnchor
    from src.core.store import ProjectStore
    from src.agents.runtime import AgentRuntime
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime",
                                                   aspect_ratio="16:9"),
                   story_text="故事",
                   characters=[Character(name="阿派", description_en="a")],
                   scenes=[Scene(scene_id="s01", summary="s", characters=["阿派"])])
    ProjectStore(tmp_path).save(proj)
    rt = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                      settings_path=tmp_path / "agents_data" / "settings.json",
                      client=None)
    started = {}

    class FakeEngine:
        def start_run(self, pdir, project_name, definition, v, plan, snapshot, key):
            started["workflow_id"] = definition["workflowId"]
            started["template"] = definition["template"]
            started["snapshot_version"] = snapshot.get("snapshotVersion")
            return {"run": {"runId": "wfr_test_1"}, "reused": False}

    import src.workflow.executor as wex
    monkeypatch.setattr(wex, "get_engine", lambda: FakeEngine())
    run = {
        "schema": cm.COLLAB_RUN_SCHEMA, "run_id": "run_w09test", "project": PROJECT,
        "mode_id": "six_member", "mode_version": "1.0.0", "status": "running",
        "created_at": 1.0, "granted_by": "t", "snapshot_hash": "h",
        "snapshot": {"mode": cm.COLLAB_MODES["six_member"],
                     "params": {"story_goal": "s"}},
        "steps": [], "session_ids": {}, "grant_ids": [],
        "workflow_run_id": None, "result": None,
    }
    asyncio.run(cm._run_workflow_template(rt, tmp_path, PROJECT, run))
    assert started["workflow_id"] == "collab_run_w09test"
    assert started["template"]["templateId"] == "W09"
    assert started["snapshot_version"] == "workflow_snapshot_v1"
    assert run["workflow_run_id"] == "wfr_test_1"
    step = run["steps"][0]
    assert step["step_id"] == "instantiate" and step["status"] == "done"


# ============ 5. 取消 ============
def test_cancel_run_sets_turn_events_and_persists(fake_rt, tmp_path):
    from src.agents import turn_registry
    key = turn_registry.key(PROJECT, "sess_producer")
    ev = asyncio.Event()
    turn_registry._ACTIVE[key] = ev
    try:
        run = {"schema": cm.COLLAB_RUN_SCHEMA, "run_id": "run_cancel1",
               "project": PROJECT, "mode_id": "producer_lead",
               "mode_version": "1.0.0", "status": "running", "created_at": 1.0,
               "granted_by": "t", "snapshot_hash": "h",
               "snapshot": {"mode": {}, "params": {}}, "steps": [],
               "session_ids": {"producer": "sess_producer"}, "grant_ids": [],
               "workflow_run_id": None, "result": None}
        cm.save_run(fake_rt.tmp, run)
        out = cm.cancel_run(fake_rt, fake_rt.tmp, "run_cancel1", reason="用户停止")
        assert out["stopped"] is True
        assert ev.is_set()
        cur = cm.load_run(fake_rt.tmp, "run_cancel1")
        assert cur["status"] == "cancelled"
        assert cur["result"]["cancel_reason"] == "用户停止"
        # 终态幂等
        out2 = cm.cancel_run(fake_rt, fake_rt.tmp, "run_cancel1")
        assert out2["stopped"] is False and out2["status"] == "cancelled"
    finally:
        turn_registry._ACTIVE.pop(key, None)


# ============ 6. REST 端点 ============
def test_rest_endpoints(tmp_path, monkeypatch):
    import routers.agents as ra
    from fastapi import HTTPException

    class _FakePM:
        def __init__(self, p):
            self._p = p
            self.base_dir = p

        def get_project_dir(self, name):
            return self._p

    rt = FakeRuntime(tmp_path)
    monkeypatch.setattr(ra, "get_agent_runtime", lambda: rt)
    monkeypatch.setattr(ra, "get_project_manager", lambda: _FakePM(tmp_path))

    # list：五模式 + 可用性
    out = asyncio.run(ra.collab_modes_list(PROJECT))
    ids = {m["mode_id"]: m for m in out["modes"]}
    assert set(ids) == set(cm.COLLAB_MODES)
    assert all(m["available"] for m in out["modes"]), out["modes"][0]

    # start 非法 → 400
    with pytest.raises(HTTPException) as ei:
        asyncio.run(ra.collab_run_start(PROJECT, ra.CollabRunStartReq(
            mode_id="producer_lead", params={})))
    assert ei.value.status_code == 400

    # start 合法 → run 立即返回（后台执行中/已完成），get 可查
    started = asyncio.run(ra.collab_run_start(PROJECT, ra.CollabRunStartReq(
        mode_id="producer_lead", params={"story_goal": "s"})))
    rid = started["run"]["run_id"]
    got = asyncio.run(ra.collab_run_get(PROJECT, rid))
    assert got["run"]["run_id"] == rid
    runs = asyncio.run(ra.collab_runs_list(PROJECT))
    assert any(r["run_id"] == rid for r in runs["runs"])
    # cancel
    c = asyncio.run(ra.collab_run_cancel(PROJECT, rid, ra.CollabRunCancelReq(
        reason="x")))
    assert c["status"] in ("cancelled", "completed")   # 可能已自然完成
    # 未知 run → 404
    with pytest.raises(HTTPException) as ei2:
        asyncio.run(ra.collab_run_get(PROJECT, "run_missing"))
    assert ei2.value.status_code == 404
