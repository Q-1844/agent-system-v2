# tests/test_ai_7c_6_cancel.py
# AI-7C-6 cancel + resume 语义 focused tests（§38/§81/§83）。
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
        raise AssertionError("cancel/resume 路径不应调 Agnes")


def _setup(tmp_path, mode="affected_production"):
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
    return rt, sig, projected, nc


def _exec(rt):
    return ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo").find_execution("plan_t", 1)


# ============ ① 提交前 cancel：cancelled，canonical 不动（§38）============
def test_cancel_pre_commit(tmp_path):
    rt, sig, projected, _ = _setup(tmp_path)
    cpx.start_and_replay(rt, "demo", "cs_t", "plan_t", 1, "affected_production")  # → canonical_commit 未提交
    ex0 = _exec(rt)
    assert ex0.stage == "canonical_commit"
    assert ex0.canonical_commit is None
    before_bytes = (tmp_path / "output" / "project.json").read_bytes()

    res = asyncio.run(cpx.cancel_execution(rt, "demo", ex0.execution_id))   # AI-7C-H：async 化
    assert res["cancelled"] is True
    assert res["creative_committed"] is False
    assert res["execution"]["status"] == "cancelled"
    assert res["execution"]["cancel_requested"] is True
    # canonical 未动
    assert (tmp_path / "output" / "project.json").read_bytes() == before_bytes


# ============ ② 提交后 cancel：creative 保留，creative_committed=True（§38）============
def test_cancel_post_commit(tmp_path):
    rt, sig, projected, _ = _setup(tmp_path)
    asyncio.run(cpx.start_replay_commit(rt, "demo", "cs_t", "plan_t", 1, "affected_production"))
    ex0 = _exec(rt)
    assert ex0.canonical_commit["status"] == "committed"   # 已提交
    committed_bytes = (tmp_path / "output" / "project.json").read_bytes()

    res = asyncio.run(cpx.cancel_execution(rt, "demo", ex0.execution_id))   # AI-7C-H：async 化
    assert res["creative_committed"] is True
    # 创作变更保留（canonical 不回滚）
    assert (tmp_path / "output" / "project.json").read_bytes() == committed_bytes


# ============ ③ 终态 cancel：幂等 no-op ============
def test_cancel_terminal_idempotent(tmp_path):
    rt, sig, projected, _ = _setup(tmp_path)
    asyncio.run(cpx.start_replay_commit(rt, "demo", "cs_t", "plan_t", 1, "creative_only"))
    ex0 = _exec(rt)
    assert ex0.status == "completed"
    res = asyncio.run(cpx.cancel_execution(rt, "demo", ex0.execution_id))   # AI-7C-H：async 化
    assert res["idempotent"] is True


# ============ ④ resume：blocked -> 返回 blocked（§81 不 rebasing）============
def test_resume_blocked_returns_blocked(tmp_path):
    rt, sig, projected, _ = _setup(tmp_path)
    asyncio.run(cpx.start_replay_commit(rt, "demo", "cs_t", "plan_t", 1, "affected_production"))
    ex0 = _exec(rt)
    # 手动置 blocked（模拟 stale/preview-mismatch）
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    ex0.status = "blocked"; ex0.blocker = {"code": "PROJECT_CHANGED_AFTER_APPROVAL"}
    store.save_execution(ex0)
    res = asyncio.run(cpx.resume_execution(rt, "demo", ex0.execution_id))
    assert res["blocked"] is True
    assert res["blocker_code"] == "PROJECT_CHANGED_AFTER_APPROVAL"


# ============ ⑤ resume：终态 -> 幂等 ============
def test_resume_terminal_idempotent(tmp_path):
    rt, sig, projected, _ = _setup(tmp_path)
    asyncio.run(cpx.start_replay_commit(rt, "demo", "cs_t", "plan_t", 1, "creative_only"))
    ex0 = _exec(rt)
    assert ex0.status == "completed"
    res = asyncio.run(cpx.resume_execution(rt, "demo", ex0.execution_id))
    assert res["idempotent"] is True
