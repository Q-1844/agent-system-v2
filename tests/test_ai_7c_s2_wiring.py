# tests/test_ai_7c_s2_wiring.py
# AI-7C-S S2 回归测试（§21：修复须留 failure-mode regression）。
#   L1：character_views handler 生产注册（routers.agents import 后 JOB_HANDLERS 含之）。
#   M1：resume_execution stage 分发——post-commit 后台 kickoff + 链 character_sync→planning；
#       in-flight 幂等（不双派发）；blocked 不 resume；production 阶段幂等。
# 不联网（0 真 Agnes）：run_character_sync/run_production_planning monkeypatch 为 fake（测分发 wiring，
# 非生成器本身——B 级真路径 proof 在 S5）。
import asyncio

import pytest

from src.core.models import Project, Scene, Shot, Character, StyleAnchor, Asset
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.change_protocol import execution_service as cpx
from src.agents.change_protocol.store import ChangeProtocolStore
from src.agents.change_protocol.models import (UnifiedChangePlan, PlanPreviewArtifact,
                                                PlanApproval, ChangeSession)
from src.revision.models import ChangeOperation, BeforeAfter, creative_source_signature
from src.revision.changeset import preview_operations_on_project


def _proj():
    return Project(
        name="demo", style=StyleAnchor(),
        characters=[Character(name="Xiaoyu", description_en="a girl")],
        scenes=[Scene(scene_id="s01", summary="meet", characters=["Xiaoyu"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="see", characters=["Xiaoyu"],
                                  target_duration_s=4, link_mode="i2v")])],
        story_text="once upon a time")


class _NoCall:
    def __init__(self): self.calls = 0

    async def chat(self, *a, **k):
        self.calls += 1
        raise AssertionError("此路径不应调 Agnes")


def _seed_and_commit(tmp_path, mode="affected_production"):
    """建 approved plan（update_character）+ session，跑 start_replay_commit 到 character_sync（committed+manifest）。"""
    ProjectStore(tmp_path).save(_proj())
    nc = _NoCall()
    rt = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                      settings_path=tmp_path / "agents_data" / "settings.json", client=nc)
    sig = creative_source_signature(_proj().to_dict())
    store = ChangeProtocolStore(tmp_path, "demo")
    store.save_session(ChangeSession(session_id="cs_t", project_name="demo",
                                     status="approved_for_execution",
                                     active_plan_id="plan_t", plan_version=1,
                                     source_snapshot_signature=sig, schema_version=2,
                                     created_at=1.0, updated_at=1.0))
    ops = [ChangeOperation(op_type="update_character", character_name="Xiaoyu",
                           description_en=BeforeAfter(before="a girl",
                                                      after="a girl with long black hair"))]
    source = ProjectStore(tmp_path).load()
    candidate, _d, impact, _w = preview_operations_on_project(source, ops)
    projected = creative_source_signature(candidate.to_dict())
    plan = UnifiedChangePlan(plan_id="plan_t", change_session_id="cs_t", project_name="demo",
                             version=1, source_snapshot_signature=sig,
                             status="approved_for_execution",
                             steps=[{"step_id": "step_x", "module": "character",
                                     "owner_agent_id": "character_designer", "plan_item_ids": [],
                                     "depends_on_step_ids": [], "parallel_group": None}],
                             preview_id="pv_t", created_at=1.0)
    plan.approval = PlanApproval(plan_id="plan_t", plan_version=1, decision="approved",
                                 source_snapshot_signature=sig, decided_at=2.0).to_dict()
    store.save_plan(plan)
    store.save_preview(PlanPreviewArtifact(
        preview_id="pv_t", change_session_id="cs_t", plan_id="plan_t", plan_version=1,
        source_snapshot_signature=sig,
        module_operations=[{"step_id": "step_x", "module": "character",
                             "owner_agent_id": "character_designer", "proposal_item_ids": [],
                             "operations": [ops[0].to_dict()]}],
        aggregate_impact=impact.to_dict(), projected_signature=projected,
        applyable=False, created_at=1.0))
    asyncio.run(cpx.start_replay_commit(rt, "demo", "cs_t", "plan_t", 1, mode))
    return rt, sig, projected, nc


def _exec(rt):
    return ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo").find_execution("plan_t", 1)


def _set_stage(rt, execution_id, stage, status="running"):
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    ex = store.get_execution(execution_id)
    ex.stage = stage; ex.status = status; ex.updated_at = cpx._now()
    store.save_execution(ex)
    return ex


# in-flight 占位 sentinel（避免 asyncio.Future() 在无 running loop 时构造不稳）
_SENTINEL = object()


# ============ L1：character_views handler 生产注册 ============
def test_character_views_handler_registered_in_production():
    """L1/§10：生产 composition root 必须注册 character_views handler。
    证据：import routers.agents（生产接线）后 JOB_HANDLERS 含 'character_views' 且为真 handler。"""
    import deps
    import routers.agents  # noqa: F401  触发 composition root 注册
    from src.agents.character_views_handler import handler_character_views
    assert "character_views" in deps.JOB_HANDLERS, "character_views handler 未在生产注册（L1）"
    assert deps.JOB_HANDLERS["character_views"] is handler_character_views


# ============ M1：resume_execution stage 分发 ============
def test_resume_kicks_off_postcommit_and_chains(tmp_path, monkeypatch):
    """M1：character_sync 阶段 resume -> 后台 kickoff -> 链 character_sync→production_planning→production_prepare。
    回归 H1/M1：post-commit 阶段经 resume 端点可达（之前 no-op past commit）。"""
    rt, _sig, _proj_sig, _nc = _seed_and_commit(tmp_path)
    ex0 = _exec(rt)
    assert ex0.stage == "character_sync"

    sync_calls, plan_calls = [], []

    async def fake_sync(_rt, proj, eid):
        sync_calls.append(eid)
        store = ChangeProtocolStore(_rt._resolve_project_dir(proj), proj)
        ex = store.get_execution(eid)
        ex.stage = "production_planning"; ex.updated_at = cpx._now(); store.save_execution(ex)

    async def fake_plan(_rt, proj, eid):
        plan_calls.append(eid)
        store = ChangeProtocolStore(_rt._resolve_project_dir(proj), proj)
        ex = store.get_execution(eid)
        ex.stage = "production_prepare"; ex.status = "running"; ex.updated_at = cpx._now(); store.save_execution(ex)

    monkeypatch.setattr(cpx, "run_character_sync", fake_sync)
    monkeypatch.setattr(cpx, "run_production_planning", fake_plan)

    async def _do():
        res = await cpx.resume_execution(rt, "demo", ex0.execution_id)
        assert res["kicked_off"] is True
        task = cpx._INFLIGHT_POSTCOMMIT.get(ex0.execution_id)
        if task is not None:
            await task  # 同 loop 内 await 后台任务完成
            await asyncio.sleep(0); await asyncio.sleep(0)  # 让 done_callback 清理 in-flight 记账
        return res

    asyncio.run(_do())
    assert sync_calls == [ex0.execution_id], "run_character_sync 未被后台任务调用"
    assert plan_calls == [ex0.execution_id], "run_production_planning 未被链式调用"
    ex_final = _exec(rt)
    assert ex_final.stage == "production_prepare", f"链未推进到 production_prepare（{ex_final.stage}）"
    assert ex0.execution_id not in cpx._INFLIGHT_POSTCOMMIT, "完成后未清理 in-flight 记账"


def test_resume_postcommit_idempotent_when_inflight(tmp_path, monkeypatch):
    """M1/§13：in-flight 期间重复 resume 不双派发（durable record + in-memory set 双重幂等）。"""
    rt, _sig, _ps, _nc = _seed_and_commit(tmp_path)
    ex0 = _exec(rt)

    # 占位 in-flight（模拟任务跑中）
    cpx._INFLIGHT_POSTCOMMIT[ex0.execution_id] = _SENTINEL  # 占位：模拟任务跑中（永不自然完成）
    try:
        res = asyncio.run(cpx.resume_execution(rt, "demo", ex0.execution_id))
        assert res["kicked_off"] is False, "in-flight 期间不应再派发任务"
        assert res["idempotent"] is True
    finally:
        cpx._INFLIGHT_POSTCOMMIT.pop(ex0.execution_id, None)


def test_resume_blocked_does_not_kickoff(tmp_path):
    """M1/§81：blocked execution resume 不 kickoff（返回 blocked 态供 UI 说明）。"""
    rt, _sig, _ps, _nc = _seed_and_commit(tmp_path)
    ex0 = _exec(rt)
    # 用 fresh ex 单次写 stage/status/blocker（避免 stale 对象覆盖回 running）
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    ex = store.get_execution(ex0.execution_id)
    ex.stage = "character_sync"; ex.status = "blocked"
    ex.blocker = {"code": "PROJECT_CHANGED_AFTER_APPROVAL", "message": "stale"}
    ex.updated_at = cpx._now()
    store.save_execution(ex)

    res = asyncio.run(cpx.resume_execution(rt, "demo", ex0.execution_id))
    assert res.get("blocked") is True
    assert res.get("blocker_code") == "PROJECT_CHANGED_AFTER_APPROVAL"
    assert ex0.execution_id not in cpx._INFLIGHT_POSTCOMMIT, "blocked 不应派发后台任务"


def test_resume_production_stage_idempotent(tmp_path):
    """M1：production_prepare/production/compose 阶段由既有 ProjectProductionRun 接管，resume 幂等返回。"""
    rt, _sig, _ps, _nc = _seed_and_commit(tmp_path)
    ex0 = _exec(rt)
    _set_stage(rt, ex0.execution_id, "production_prepare", status="running")

    res = asyncio.run(cpx.resume_execution(rt, "demo", ex0.execution_id))
    assert res["idempotent"] is True
    assert res.get("kicked_off", False) is False
    assert ex0.execution_id not in cpx._INFLIGHT_POSTCOMMIT


def test_resume_precommit_advances_via_start_replay_commit(tmp_path):
    """M1：pre-commit 阶段 resume -> 同步 start_replay_commit 推进到 character_sync（再接续 kickoff）。
    覆盖 restart-mid-recovery 场景：execution 卡在 workspace_create 时 resume 重新推进。"""
    rt, _sig, _ps, _nc = _seed_and_commit(tmp_path)
    ex0 = _exec(rt)
    # 回退到 pre-commit（模拟重启时卡在 replay 前）
    _set_stage(rt, ex0.execution_id, "workspace_create", status="running")
    # resume pre-commit 分支会调 start_replay_commit（已 committed -> 幂等到 character_sync），
    # 然后接续 _maybe_kickoff_postcommit（character_sync 阶段）。占位防真 sync 跑：
    cpx._INFLIGHT_POSTCOMMIT[ex0.execution_id] = _SENTINEL
    try:
        res = asyncio.run(cpx.resume_execution(rt, "demo", ex0.execution_id))
        ex_after = _exec(rt)
        # start_replay_commit 幂等推进到 character_sync（committed），再 kickoff（占位 in-flight 阻止真派发）
        assert ex_after.stage in ("character_sync", "production_planning"), ex_after.stage
        assert "execution" in res
    finally:
        cpx._INFLIGHT_POSTCOMMIT.pop(ex0.execution_id, None)
