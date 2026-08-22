# tests/test_ai_7c_s3_manifest.py
# AI-7C-S S3 回归测试（§21）。
#   M2：update_scene 的 characters 变更（无 summary）也触发 shot first_frame REGENERATE（§41）。
#   L4：commit 与 manifest 计算间崩溃 -> production_manifest_file 缺失 -> _ensure_manifest 从
#       已提交 canonical + 批准 ops 确定性重算 + 落盘（不引入第二真源，§15），避免永久 blocked。
import asyncio
import os

from src.core.models import Project, Scene, Shot, Character, StyleAnchor, Asset
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime
from src.agents.change_protocol import execution_service as cpx
from src.agents.change_protocol.store import ChangeProtocolStore
from src.agents.change_protocol.models import (UnifiedChangePlan, PlanPreviewArtifact,
                                                PlanApproval, ChangeSession)
from src.agents.change_protocol.execution_models import ChangeExecutionSession
from src.revision.models import ChangeOperation, BeforeAfter, creative_source_signature
from src.revision.changeset import preview_operations_on_project


# ============ M2：update_scene characters -> first_frame REGENERATE ============
def test_m2_update_scene_characters_triggers_first_frame(tmp_path):
    """M2/§41：update_scene 改 characters（无 summary）-> scene 内 shot first_frame REGENERATE。
    回归：原代码仅 op.summary 触发 first_frame，漏 characters -> 角色名单变更首帧不重生成。"""
    project = Project(
        name="d", style=StyleAnchor(),
        scenes=[Scene(scene_id="s01", summary="meet", characters=["A"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  characters=["A"], link_mode="i2v")])])
    # update_scene 改 characters（无 summary）—— 角色名单变更
    op = ChangeOperation(op_type="update_scene", scene_id="s01",
                         characters=BeforeAfter(before=["A"], after=["A", "B"]))
    preview = PlanPreviewArtifact(
        preview_id="pv", change_session_id="cs", plan_id="p", plan_version=1,
        source_snapshot_signature="sig",
        module_operations=[{"step_id": "st", "operations": [op.to_dict()]}],
        aggregate_impact={"affected_scene_ids": ["s01"], "production_plan_scene_ids": [],
                          "affected_shot_ids": []},
        projected_signature="sig", applyable=False, created_at=1.0)
    plan = UnifiedChangePlan(plan_id="p", change_session_id="cs", project_name="d", version=1,
                             source_snapshot_signature="sig", status="approved_for_execution",
                             steps=[{"step_id": "st"}], preview_id="pv", created_at=1.0)
    ex = ChangeExecutionSession(execution_id="e", project_name="d", plan_id="p", plan_version=1,
                                source_signature="sig", production_mode="affected_production")
    manifest = cpx.compute_production_manifest(ex, plan, preview, project)
    sa = manifest.shot_actions.get("s01_c01", {})
    assert sa.get("first_frame") == "regenerate", \
        f"M2：update_scene characters 变更应触发 first_frame regen，得 {sa}"
    assert sa.get("video") == "regenerate"


def test_m2_update_scene_summary_still_triggers_first_frame(tmp_path):
    """M2 回归保护：summary 变更仍触发 first_frame（修复未误伤原 summary 路径）。"""
    project = Project(
        name="d", style=StyleAnchor(),
        scenes=[Scene(scene_id="s01", summary="meet", characters=["A"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  characters=["A"], link_mode="i2v")])])
    op = ChangeOperation(op_type="update_scene", scene_id="s01",
                         summary=BeforeAfter(before="meet", after="encounter"))
    preview = PlanPreviewArtifact(
        preview_id="pv", change_session_id="cs", plan_id="p", plan_version=1,
        source_snapshot_signature="sig",
        module_operations=[{"step_id": "st", "operations": [op.to_dict()]}],
        aggregate_impact={"affected_scene_ids": ["s01"], "production_plan_scene_ids": [],
                          "affected_shot_ids": []},
        projected_signature="sig", applyable=False, created_at=1.0)
    plan = UnifiedChangePlan(plan_id="p", change_session_id="cs", project_name="d", version=1,
                             source_snapshot_signature="sig", status="approved_for_execution",
                             steps=[{"step_id": "st"}], preview_id="pv", created_at=1.0)
    ex = ChangeExecutionSession(execution_id="e", project_name="d", plan_id="p", plan_version=1,
                                source_signature="sig", production_mode="affected_production")
    manifest = cpx.compute_production_manifest(ex, plan, preview, project)
    sa = manifest.shot_actions.get("s01_c01", {})
    assert sa.get("first_frame") == "regenerate"
    assert sa.get("video") == "regenerate"


# ============ L4：manifest 缺失且已提交 -> 重算 ============
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
    async def chat(self, *a, **k):
        raise AssertionError("此路径不应调 Agnes")


def _seed_and_commit(tmp_path, mode="affected_production"):
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
    return rt


def _exec(rt):
    return ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo").find_execution("plan_t", 1)


def test_l4_manifest_recomputed_when_missing_after_commit(tmp_path):
    """L4/§15：commit 与 manifest 计算间崩溃 -> production_manifest_file 缺失 ->
    _ensure_manifest 从已提交 canonical + 批准 plan/preview 确定性重算 + 落盘 + 设字段。
    回归：原代码 _assert_agnes_prerequisites 查 `not production_manifest_file` -> 永久 blocked 无重算。"""
    rt = _seed_and_commit(tmp_path)
    ex0 = _exec(rt)
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    audit_p = store.root / "execution_audit" / ex0.execution_id / "production_manifest.json"
    assert audit_p.exists(), "预置：commit 应已落盘 manifest"
    # 模拟崩溃：删 manifest 文件 + 清 production_manifest_file
    os.remove(audit_p)
    ex = store.get_execution(ex0.execution_id)
    ex.production_manifest_file = None
    store.save_execution(ex)

    # L4：_ensure_manifest 应重算（committed -> 从 canonical + 批准 ops）
    manifest_d = cpx._ensure_manifest(rt, "demo", ex)
    assert manifest_d is not None, "L4：manifest 缺失且已提交应重算"
    assert manifest_d.get("execution_id") == ex0.execution_id
    ex2 = store.get_execution(ex0.execution_id)
    assert ex2.production_manifest_file == "production_manifest.json"
    assert audit_p.exists(), "L4：重算后应重新落盘"


def test_l4_ensure_manifest_no_recompute_when_not_committed(tmp_path):
    """L4：未提交时 _ensure_manifest 不重算（返回 None），不伪造 manifest。"""
    rt = _seed_and_commit(tmp_path)
    ex0 = _exec(rt)
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    # 强制清 committed 状态 + 删 manifest（模拟未提交却误入 sync）
    audit_p = store.root / "execution_audit" / ex0.execution_id / "production_manifest.json"
    if audit_p.exists():
        os.remove(audit_p)
    ex = store.get_execution(ex0.execution_id)
    ex.canonical_commit = None
    ex.production_manifest_file = None
    store.save_execution(ex)
    manifest_d = cpx._ensure_manifest(rt, "demo", ex)
    assert manifest_d is None, "L4：未提交时不应重算/伪造 manifest"
