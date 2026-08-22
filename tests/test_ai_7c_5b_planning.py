# tests/test_ai_7c_5b_planning.py
# AI-7C-5b affected-scene planning + all-plan gate + targeted run focused tests（§53/§54/§59）。
#
# run_production_planning 用 injectable plan_creator + run_starter 测（AI-6F fake/test 分离）。
import asyncio

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
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
        raise AssertionError("planning 路径不应调 Agnes")


def _seed_and_commit(tmp_path):
    """建 approved plan（update_char）+ commit -> character_sync（manifest 已含 planner_scene_ids=[s01]）。"""
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
                           description_en=BeforeAfter(before="a girl", after="a girl black hair"))]
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
    asyncio.run(cpx.start_replay_commit(rt, "demo", "cs_t", "plan_t", 1, "affected_production"))
    return rt, sig, projected, nc


def _exec(rt):
    return ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo").find_execution("plan_t", 1)


async def _fake_view_gen(char_name):
    from src.core.models import Asset
    return {v: Asset(url=f"https://fake/{char_name}/{v}.png", status="done")
            for v in ("front", "side", "back")}


def _at_production_planning(tmp_path):
    rt, sig, projected, nc = _seed_and_commit(tmp_path)
    ex0 = _exec(rt)
    asyncio.run(cpx.run_character_sync(rt, "demo", ex0.execution_id, view_generator=_fake_view_gen))
    ex = _exec(rt)
    assert ex.stage == "production_planning"
    return rt, ex, nc


def _fake_plan_creator(approved=True, shot_ids=None):
    calls = []

    async def gen(scene_id):
        calls.append(scene_id)
        return {"plan_id": f"plan_{scene_id}", "scene_id": scene_id,
                "status": "approved" if approved else "draft",
                "shot_ids": shot_ids or ["s01_c01"]}
    return gen, calls


def _fake_run_starter():
    calls = []

    async def starter(manifest):
        calls.append(list(manifest.planner_scene_ids))
        return {"run_id": "prun_fake"}
    return starter, calls


# ============ ① happy：plan + all-approved gate + targeted run + stage ============
def test_planning_creates_targeted_run(tmp_path):
    rt, ex0, nc = _at_production_planning(tmp_path)
    m = cpx.get_production_manifest(rt, "demo", ex0.execution_id)
    assert m["planner_scene_ids"] == ["s01"]   # char change -> 受影响 scene s01
    pc, pc_calls = _fake_plan_creator()
    rs, rs_calls = _fake_run_starter()
    res = asyncio.run(cpx.run_production_planning(
        rt, "demo", ex0.execution_id, plan_creator=pc, run_starter=rs))
    ex = res["execution"]
    assert ex["stage"] == "production_prepare"
    assert ex["production_run_id"] == "prun_fake"
    assert pc_calls == ["s01"]
    assert rs_calls == [["s01"]]   # targeted run with planner_scene_ids
    assert len(ex["planning"]) == 1
    assert ex["planning"][0]["scene_id"] == "s01"
    assert nc.calls == 0   # 0 真 Agnes


# ============ ② all-plan gate：未批准 -> blocked，0 run（§54）============
def test_all_plan_gate_blocks(tmp_path):
    rt, ex0, _ = _at_production_planning(tmp_path)
    pc, _ = _fake_plan_creator(approved=False)
    rs, rs_calls = _fake_run_starter()
    import pytest
    res = asyncio.run(cpx.run_production_planning(
        rt, "demo", ex0.execution_id, plan_creator=pc, run_starter=rs))
    assert res["blocked"] is True
    assert res["blocker_code"] == "PRODUCTION_PLAN_INVALID"
    assert rs_calls == []   # §54：未全批准 -> 0 run 创建
    ex = _exec(rt)
    assert ex.status == "blocked"
    assert ex.stage == "production_planning"


# ============ ③ 幂等：再调 no-op（已到 production_prepare）============
def test_planning_idempotent(tmp_path):
    rt, ex0, _ = _at_production_planning(tmp_path)
    pc, _ = _fake_plan_creator()
    rs, rs_calls = _fake_run_starter()
    asyncio.run(cpx.run_production_planning(
        rt, "demo", ex0.execution_id, plan_creator=pc, run_starter=rs))
    res2 = asyncio.run(cpx.run_production_planning(
        rt, "demo", ex0.execution_id, plan_creator=pc, run_starter=rs))
    assert res2["idempotent"] is True
    assert len(rs_calls) == 1   # 未二次创建 run


# ============ ④ L4/§15：committed 缺 manifest -> 重算（非 block）============
def test_planning_no_manifest_recomputes_when_committed(tmp_path):
    """L4：committed execution 缺 manifest -> run_production_planning 经 _ensure_manifest 从
    已提交 canonical + 批准 ops 确定性重算（不 block）。原 test 期望 PRODUCTION_PREREQUISITE_FAILED，
    L4 后改为重算（§15：manifest 可从 approved ops + committed project 重算，避免第二真源）。"""
    rt, ex0, _ = _at_production_planning(tmp_path)
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    audit_p = store.root / "execution_audit" / ex0.execution_id / "production_manifest.json"
    assert audit_p.exists()
    audit_p.unlink()   # 删 manifest 模拟 commit 与 manifest 计算间崩溃
    ex = store.get_execution(ex0.execution_id)
    ex.production_manifest_file = None
    store.save_execution(ex)
    pc, _ = _fake_plan_creator()
    rs, _ = _fake_run_starter()
    res = asyncio.run(cpx.run_production_planning(
        rt, "demo", ex0.execution_id, plan_creator=pc, run_starter=rs))
    assert not res.get("blocked", False), "L4：committed 缺 manifest 应重算而非 block"
    ex2 = _exec(rt)
    assert ex2.production_manifest_file == "production_manifest.json"   # 重算后字段复位
    assert ex2.stage == "production_prepare"


# ============ ⑤ 非 production_planning stage -> idempotent no-op ============
def test_planning_wrong_stage_noop(tmp_path):
    rt, sig, projected, _ = _seed_and_commit(tmp_path)
    ex0 = _exec(rt)
    assert ex0.stage == "character_sync"   # 未跑 character sync
    pc, pc_calls = _fake_plan_creator()
    res = asyncio.run(cpx.run_production_planning(
        rt, "demo", ex0.execution_id, plan_creator=pc))
    assert res["idempotent"] is True
    assert pc_calls == []   # 未 planning
