# tests/test_ai_7c_7_gates.py
# AI-7C-7 consolidated final behavioral gates（§102 Cases A/C/D + creative_only + §108 permission + §109 lineage）。
# 确定性、FakeAgnes（0 真 Agnes）。真 §103/§104/§106 acceptance 需用户环境（重启后端+真 Agnes+浏览器）。
import asyncio

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.change_protocol import execution_service as cpx
from src.agents.change_protocol import modes as cp_modes
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
        raise AssertionError("7C-7 gate 路径不应调 Agnes")


def _setup(tmp_path, ops, *, module="storyboard", owner="director", step_module=None):
    """建 approved plan（给定 ops）+ session。step_module 默认 = module。"""
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
    source = ProjectStore(tmp_path).load()
    candidate, _d, impact, _w = preview_operations_on_project(source, ops)
    projected = creative_source_signature(candidate.to_dict())
    sm = step_module or module
    plan = UnifiedChangePlan(plan_id="plan_t", change_session_id="cs_t", project_name="demo",
                             version=1, source_snapshot_signature=sig,
                             status="approved_for_execution",
                             steps=[{"step_id": "step_x", "module": sm, "owner_agent_id": owner,
                                     "plan_item_ids": [], "depends_on_step_ids": [],
                                     "parallel_group": None}], preview_id="pv_t", created_at=1.0)
    plan.approval = PlanApproval(plan_id="plan_t", plan_version=1, decision="approved",
                                 source_snapshot_signature=sig, decided_at=2.0).to_dict()
    store.save_plan(plan)
    store.save_preview(PlanPreviewArtifact(
        preview_id="pv_t", change_session_id="cs_t", plan_id="plan_t", plan_version=1,
        source_snapshot_signature=sig,
        module_operations=[{"step_id": "step_x", "module": sm, "owner_agent_id": owner,
                             "proposal_item_ids": [], "operations": [o.to_dict() for o in ops]}],
        aggregate_impact=impact.to_dict(), projected_signature=projected,
        applyable=False, created_at=1.0))
    return rt, sig, projected, nc


def _exec(rt):
    return ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo").find_execution("plan_t", 1)


# ============ Case A：小 storyboard-only change（§102 A）============
def test_case_a_small_storyboard_change(tmp_path):
    ops = [ChangeOperation(op_type="update_shot", scene_id="s01", shot_id="s01_c01",
                            description=BeforeAfter(before="see", after="see the girl arrive"))]
    rt, sig, projected, nc = _setup(tmp_path, ops)
    before = (tmp_path / "output" / "project.json").read_bytes()
    asyncio.run(cpx.start_replay_commit(rt, "demo", "cs_t", "plan_t", 1, "affected_production"))
    ex = _exec(rt)
    # 原子提交一次，canonical 变
    assert (tmp_path / "output" / "project.json").read_bytes() != before
    assert ex.canonical_commit["status"] == "committed"
    assert ex.stage == "character_sync"   # affected_production
    # 历史资产保留（无 VideoVersion 删除）
    assert nc.calls == 0


# ============ Case C：appearance-only character change（§102 C）============
def test_case_c_appearance_only_char(tmp_path):
    ops = [ChangeOperation(op_type="update_character", character_name="Xiaoyu",
                            description_en=BeforeAfter(before="a girl", after="a girl black hair"))]
    rt, sig, projected, nc = _setup(tmp_path, ops, module="character", owner="character_designer")
    asyncio.run(cpx.start_replay_commit(rt, "demo", "cs_t", "plan_t", 1, "affected_production"))
    ex = _exec(rt)
    assert ex.canonical_commit["status"] == "committed"
    m = cpx.get_production_manifest(rt, "demo", ex.execution_id)
    # 仅外貌变：char regen + 引用 shot frame/video regen，audio keep（不过级联）
    assert m["character_actions"]["Xiaoyu"] == "regenerate"
    assert m["shot_actions"]["s01_c01"]["first_frame"] == "regenerate"
    assert m["shot_actions"]["s01_c01"]["video"] == "regenerate"
    assert nc.calls == 0


# ============ Case D：stale after approval（§102 D）============
def test_case_d_stale_after_approval(tmp_path):
    ops = [ChangeOperation(op_type="update_shot", scene_id="s01", shot_id="s01_c01",
                            description=BeforeAfter(before="see", after="see the girl"))]
    rt, sig, projected, _ = _setup(tmp_path, ops)
    # 批准后改 canonical 创作态
    p = ProjectStore(tmp_path).load()
    p.story_text = "tampered after approval"
    ProjectStore(tmp_path).save(p)
    before = (tmp_path / "output" / "project.json").read_bytes()
    res = asyncio.run(cpx.start_replay_commit(rt, "demo", "cs_t", "plan_t", 1, "affected_production"))
    assert res["blocked"] is True
    assert res["blocker_code"] == "PROJECT_CHANGED_AFTER_APPROVAL"
    ex = _exec(rt)
    assert ex.status == "blocked"
    assert ex.stage == "revalidate"
    # 0 mutation：canonical 未被覆盖
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert ex.execution_workspace_id is None   # stale 在 workspace_create 前 block


# ============ creative_only：0 Agnes，done（§66）============
def test_creative_only_zero_agnes(tmp_path):
    ops = [ChangeOperation(op_type="update_character", character_name="Xiaoyu",
                            description_en=BeforeAfter(before="a girl", after="a girl black hair"))]
    rt, sig, projected, nc = _setup(tmp_path, ops, module="character", owner="character_designer")
    asyncio.run(cpx.start_replay_commit(rt, "demo", "cs_t", "plan_t", 1, "creative_only"))
    ex = _exec(rt)
    assert ex.status == "completed"
    assert ex.stage == "done"
    assert ex.production_skipped_by_user is True
    assert nc.calls == 0


# ============ §108 permission：APPROVED_PLAN_EXECUTION 仅 inspect；未批准 plan 不可执行 ============
def test_permission_modes_and_unapproved(tmp_path):
    # mode 硬闸：APPROVED_PLAN_EXECUTION 仅 inspect（不授 propose/mutate）
    cp_modes.assert_mode_allowed("approved_plan_execution", "inspect")
    for op in ("propose", "execute", "retry", "mutate"):
        try:
            cp_modes.assert_mode_allowed("approved_plan_execution", op)
            assert False, f"应拒绝 approved_plan_execution/{op}"
        except AgentError as e:
            assert e.code == "permission_denied"
    # 未批准 plan 不可执行
    ops = [ChangeOperation(op_type="update_shot", scene_id="s01", shot_id="s01_c01",
                            description=BeforeAfter(before="see", after="x"))]
    rt, sig, projected, _ = _setup(tmp_path, ops)
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    plan = store.get_plan("plan_t"); plan.status = "preview_ready"; plan.approval = None
    store.save_plan(plan)
    s = store.get_session("cs_t"); s.status = "ready_for_review"; store.save_session(s)
    import pytest
    with pytest.raises(AgentError) as ei:
        cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1, "affected_production")
    assert ei.value.code == "PLAN_NOT_APPROVED"


# ============ §109 lineage：execution 可追溯到 plan/approval/operations/preview ============
def test_lineage_traceable(tmp_path):
    ops = [ChangeOperation(op_type="update_shot", scene_id="s01", shot_id="s01_c01",
                            description=BeforeAfter(before="see", after="see girl"))]
    rt, sig, projected, _ = _setup(tmp_path, ops)
    asyncio.run(cpx.start_replay_commit(rt, "demo", "cs_t", "plan_t", 1, "affected_production"))
    ex = _exec(rt)
    # lineage: execution → plan_id/version → approval_id → preview_artifact_id → operation_digest
    assert ex.plan_id == "plan_t" and ex.plan_version == 1
    assert ex.approval_id.startswith("appr_plan_t_v1")
    assert ex.preview_artifact_id == "pv_t"
    assert ex.operation_digest   # §14
    assert ex.canonical_commit["operation_digest"] == ex.operation_digest
    assert ex.canonical_commit["expected_after_signature"] == projected
    # step records bind step→operations
    assert len(ex.steps) == 1
    assert ex.steps[0]["operation_count"] == 1
    assert ex.steps[0]["operation_digest"]
