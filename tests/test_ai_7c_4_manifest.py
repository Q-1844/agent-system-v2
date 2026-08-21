# tests/test_ai_7c_4_manifest.py
# AI-7C-4 Production Impact Manifest focused tests（§39/§40/§41/§47/§66）。
#
# compute_production_manifest 是纯函数（0 LLM，§41）—— 直接测之（构造 ex/plan/preview/project）。
# 另两个 commit 集成测：creative_only / affected_production 提交后 manifest 落盘 + stage 正确。
import asyncio

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.change_protocol import execution_service as cpx
from src.agents.change_protocol.store import ChangeProtocolStore
from src.agents.change_protocol.models import (UnifiedChangePlan, PlanPreviewArtifact,
                                                PlanApproval, ChangeSession)
from src.agents.change_protocol.execution_models import ChangeExecutionSession
from src.revision.models import ChangeOperation, BeforeAfter, NewShotSpec, creative_source_signature
from src.revision.changeset import preview_operations_on_project


def _proj():
    return Project(
        name="demo", style=StyleAnchor(),
        characters=[Character(name="Xiaoyu", description_en="a girl"),
                    Character(name="Aran", description_en="a hero")],
        scenes=[Scene(scene_id="s01", summary="meet", characters=["Xiaoyu", "Aran"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="see", characters=["Xiaoyu"],
                                  target_duration_s=4, link_mode="i2v")]),
                Scene(scene_id="s02", summary="fight", characters=["Xiaoyu", "Aran"],
                      shots=[Shot(shot_id="s02_c01", scene_id="s02", index=1,
                                  description="fight", characters=["Aran"],
                                  target_duration_s=4, link_mode="keyframes")])],
        story_text="once upon a time")


def _build(source, ops):
    """构造 ex/plan/preview（含真实 impact + projected_signature）。"""
    candidate, _diff, impact, _warn = preview_operations_on_project(source, ops)
    projected = creative_source_signature(candidate.to_dict())
    sig = creative_source_signature(source.to_dict())
    ex = ChangeExecutionSession(execution_id="exec_t", project_name="demo",
                               change_session_id="cs_t", plan_id="plan_t", plan_version=1,
                               approval_id="appr_plan_t_v1", source_signature=sig,
                               preview_artifact_id="pv_t", production_mode="affected_production",
                               status="running", stage="character_sync", created_at=1.0)
    plan = UnifiedChangePlan(plan_id="plan_t", change_session_id="cs_t", project_name="demo",
                            version=1, source_snapshot_signature=sig,
                            status="approved_for_execution",
                            steps=[{"step_id": "step_x", "module": "x", "owner_agent_id": "x",
                                    "plan_item_ids": [], "depends_on_step_ids": [],
                                    "parallel_group": None}], preview_id="pv_t", created_at=1.0)
    plan.approval = PlanApproval(plan_id="plan_t", plan_version=1, decision="approved",
                                source_snapshot_signature=sig, decided_at=2.0).to_dict()
    preview = PlanPreviewArtifact(preview_id="pv_t", change_session_id="cs_t", plan_id="plan_t",
                                  plan_version=1, source_snapshot_signature=sig,
                                  module_operations=[{"step_id": "step_x", "module": "x",
                                                      "owner_agent_id": "x",
                                                      "proposal_item_ids": [],
                                                      "operations": [o.to_dict() for o in ops]}],
                                  aggregate_impact=impact.to_dict(),
                                  projected_signature=projected, applyable=False, created_at=1.0)
    return ex, plan, preview, candidate


# ============ ① character desc change（§41）============
def test_manifest_character_desc_change():
    source = _proj()
    ops = [ChangeOperation(op_type="update_character", character_name="Xiaoyu",
                           description_en=BeforeAfter(before="a girl",
                                                      after="a girl with long black hair"))]
    ex, plan, preview, candidate = _build(source, ops)
    m = cpx.compute_production_manifest(ex, plan, preview, candidate)
    # char action
    assert m.character_actions.get("Xiaoyu") == "regenerate"
    assert "Xiaoyu" in m.affected_character_ids
    # Xiaoyu 出场的 shot（s01_c01）frame/video REGENERATE（§41 + impact cascade）
    a = m.shot_actions.get("s01_c01")
    assert a is not None
    assert a["first_frame"] == "regenerate"
    assert a["video"] == "regenerate"
    assert a["audio"] == "keep"   # §48 v1：无 dialogue op，audio keep
    # Aran 出场的 shot（s02_c01）不受影响
    assert "s02_c01" not in m.shot_actions


# ============ ② shot desc change（§41）============
def test_manifest_shot_desc_change():
    source = _proj()
    ops = [ChangeOperation(op_type="update_shot", scene_id="s02", shot_id="s02_c01",
                           description=BeforeAfter(before="fight", after="epic fight"))]
    ex, plan, preview, candidate = _build(source, ops)
    m = cpx.compute_production_manifest(ex, plan, preview, candidate)
    a = m.shot_actions["s02_c01"]
    assert a["first_frame"] == "regenerate"
    assert a["video"] == "regenerate"
    assert a["last_frame"] == "regenerate"   # s02_c01 link_mode=keyframes
    assert a["audio"] == "keep"


# ============ ③ insert shot（§41）============
def test_manifest_insert_shot():
    source = _proj()
    ops = [ChangeOperation(op_type="insert_shot", scene_id="s01",
                           new_shot=NewShotSpec(shot_id="s01_c02", description="new shot",
                                                 characters=["Xiaoyu"], target_duration_s=3))]
    ex, plan, preview, candidate = _build(source, ops)
    m = cpx.compute_production_manifest(ex, plan, preview, candidate)
    a = m.shot_actions["s01_c02"]
    assert a["first_frame"] == "generate_new"
    assert a["last_frame"] == "generate_new"
    assert a["audio"] == "generate_new"
    assert a["video"] == "generate_new"
    assert m.expected_video_requests == 1


# ============ ④ delete shot（§41/§42）============
def test_manifest_delete_shot():
    source = _proj()
    ops = [ChangeOperation(op_type="delete_shot", scene_id="s02", shot_id="s02_c01")]
    ex, plan, preview, candidate = _build(source, ops)
    m = cpx.compute_production_manifest(ex, plan, preview, candidate)
    a = m.shot_actions["s02_c01"]
    assert a["video"] == "remove_from_timeline"
    assert a["first_frame"] == "remove_from_timeline"
    # §42：无新 generation
    assert m.expected_video_requests == 0


# ============ ⑤ duration change（§41）============
def test_manifest_duration_change():
    source = _proj()
    ops = [ChangeOperation(op_type="update_shot", scene_id="s01", shot_id="s01_c01",
                           target_duration_s=BeforeAfter(before=4, after=6))]
    ex, plan, preview, candidate = _build(source, ops)
    m = cpx.compute_production_manifest(ex, plan, preview, candidate)
    a = m.shot_actions["s01_c01"]
    assert a["video"] == "regenerate"


# ============ ⑥ manifest deterministic（§41 same inputs -> same output）============
def test_manifest_deterministic():
    source = _proj()
    ops = [ChangeOperation(op_type="update_character", character_name="Aran",
                           description_en=BeforeAfter(before="a hero", after="a dark hero"))]
    ex, plan, preview, candidate = _build(source, ops)
    m1 = cpx.compute_production_manifest(ex, plan, preview, candidate).to_dict()
    m2 = cpx.compute_production_manifest(ex, plan, preview, candidate).to_dict()
    # created_at 是时间戳（非确定性输入）；比较 action 字段（§41 deterministic 指动作）
    assert m1["character_actions"] == m2["character_actions"]
    assert m1["shot_actions"] == m2["shot_actions"]
    assert m1["affected_character_ids"] == m2["affected_character_ids"]
    assert m1["expected_video_requests"] == m2["expected_video_requests"]


# ============ ⑦ creative_only：manifest 落盘 + done + 0 Agnes（§47/§66）============
def _setup_committed(tmp_path, mode):
    ProjectStore(tmp_path).save(_proj())
    nc = _NoCall()
    rt = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                      settings_path=tmp_path / "agents_data" / "settings.json",
                      client=nc)
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
                                     "owner_agent_id": "character_designer",
                                     "plan_item_ids": [], "depends_on_step_ids": [],
                                     "parallel_group": None}], preview_id="pv_t", created_at=1.0)
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
    return rt, sig, projected, nc


class _NoCall:
    def __init__(self): self.calls = 0

    async def chat(self, *a, **k):
        self.calls += 1
        raise AssertionError("manifest/commit 路径不应调用 Agnes（§66）")


def test_creative_only_manifest_stored(tmp_path):
    rt, sig, projected, nc = _setup_committed(tmp_path, "creative_only")
    asyncio.run(cpx.start_replay_commit(rt, "demo", "cs_t", "plan_t", 1, "creative_only"))
    ex = ChangeProtocolStore(tmp_path, "demo").find_execution("plan_t", 1)
    assert ex.status == "completed"
    assert ex.stage == "done"
    assert ex.production_skipped_by_user is True
    assert ex.production_manifest_file == "production_manifest.json"
    m = cpx.get_production_manifest(rt, "demo", ex.execution_id)
    assert m["character_actions"]["Xiaoyu"] == "regenerate"
    assert m["shot_actions"]["s01_c01"]["video"] == "regenerate"
    assert nc.calls == 0   # 0 Agnes（§66；_NoCall 任何 chat 即 AssertionError）


def test_affected_production_manifest_stored(tmp_path):
    rt, sig, projected, nc = _setup_committed(tmp_path, "affected_production")
    asyncio.run(cpx.start_replay_commit(rt, "demo", "cs_t", "plan_t", 1, "affected_production"))
    ex = ChangeProtocolStore(tmp_path, "demo").find_execution("plan_t", 1)
    assert ex.status == "running"
    assert ex.stage == "character_sync"   # AI-7C-4 接力（character sync 未实现前停此）
    assert ex.production_manifest_file == "production_manifest.json"
    m = cpx.get_production_manifest(rt, "demo", ex.execution_id)
    assert m["character_actions"]["Xiaoyu"] == "regenerate"
    assert nc.calls == 0
