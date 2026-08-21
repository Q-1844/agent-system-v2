# tests/test_ai_7c_1_execution.py
# AI-7C-1 Execution Protocol + Revalidation focused Gate（§7/§10/§13/§14/§15/§16/§17/§18/§73/§96）。
#
# 不联网（NoCallClient：execute/revalidation 路径调 Agnes 即失败）。每 case 独立 tmp_path。
# 覆盖 §96 Gate：
#  ① approved fresh plan -> execution created（pending, stage=workspace_create）
#  ② POST twice -> same execution（幂等 §15/§16）
#  ③ unapproved plan -> PLAN_NOT_APPROVED
#  ④ wrong version -> PLAN_VERSION_MISMATCH
#  ⑤ stale project -> blocked（PROJECT_CHANGED_AFTER_APPROVAL §18/§81）
#  ⑥ 0 mutation：canonical/Workspace/Production/Agnes 全不变（§96/§107）
#  ⑦ operation digest 确定性 + step 绑定（§13/§14）
#  ⑧ execution_eligibility（§4）
#  ⑨ legacy 会话不可执行（§9）
#  ⑩ startup discovery（§73/§96）
#  ⑪ creative_only / 非法 production_mode
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.change_protocol import execution_service as cpx
from src.agents.change_protocol.store import ChangeProtocolStore
from src.agents.change_protocol.models import (UnifiedChangePlan, PlanPreviewArtifact,
                                                PlanApproval, ModuleProposal,
                                                ModuleProposalItem)
from src.revision.models import ChangeOperation, BeforeAfter, creative_source_signature


# ============ fixtures / helpers ============
def _proj(name="demo"):
    return Project(
        name=name, style=StyleAnchor(),
        characters=[Character(name="Xiaoyu", description_en="a girl"),
                    Character(name="Aran", description_en="a hero")],
        scenes=[Scene(scene_id="s01", summary="meet", characters=["Xiaoyu", "Aran"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="see", characters=["Xiaoyu"],
                                  target_duration_s=4)])],
        story_text="once upon a time")


def _rt(tmp_path, client=None, per_project=False):
    resolver = (lambda name: tmp_path / name) if per_project else (lambda name: tmp_path)
    return AgentRuntime(project_dir_resolver=resolver,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client)


class NoCallClient:
    """execute/revalidation 路径不得调用 Agnes（§96/§107）。任何 chat -> AssertionError。"""
    def __init__(self):
        self.calls = 0

    async def chat(self, *a, **k):
        self.calls += 1
        raise AssertionError("execute/revalidation 路径不应调用 Agnes（§96/§107）")


def _seed_approved_plan(rt, project, session_id, sig, *, plan_id="plan_t",
                       plan_version=1, production_mode_ok=True,
                       with_binding=True, with_proposal_items=True,
                       approval_sig=None):
    """落一个 approved_for_execution plan + preview（含 module_operations 绑定）+ proposal，
    供 execute 测试。approval_sig 默认 = sig（批准时项目签名）。"""
    store = ChangeProtocolStore(rt._resolve_project_dir(project), project)
    ap_sig = approval_sig if approval_sig is not None else sig

    # proposal + items（§17：referenced proposal items 仍存在）
    item_id = "pi_sb_1"
    if with_proposal_items:
        prop = ModuleProposal(
            proposal_id="prop_sb", change_session_id=session_id, module="storyboard",
            owner_agent_id="director", round=1, status="completed",
            summary="change shot desc",
            items=[ModuleProposalItem(
                item_id=item_id, module="storyboard", action="update_shot",
                target_refs=[{"scene_id": "s01", "shot_id": "s01_c01"}],
                intent={"shot_id": "s01_c01", "description": "see the girl arrive"},
                reason="user requested").to_dict()],
            source_snapshot_signature=sig, created_at=1.0, updated_at=1.0)
        store.save_proposal(prop)

    # operation + bundle（§13：step_id -> operations 绑定）
    op = ChangeOperation(op_type="update_shot", scene_id="s01", shot_id="s01_c01",
                         description=BeforeAfter(before="see",
                                                after="see the girl arrive"))
    bundle = {"step_id": "step_sb", "module": "storyboard",
              "owner_agent_id": "director",
              "proposal_item_ids": [item_id] if with_proposal_items else [],
              "operations": [op.to_dict()] if with_binding else []}

    plan = UnifiedChangePlan(
        plan_id=plan_id, change_session_id=session_id, project_name=project,
        version=plan_version, source_snapshot_signature=sig,
        status="approved_for_execution",
        direct_change_items=[{"plan_item_id": "cpi_sb", "module": "storyboard",
                              "owner_agent_id": "director",
                              "proposal_item_ids": [item_id],
                              "scope_refs": [{"shot_id": "s01_c01"}],
                              "summary": "change shot desc", "source": "user_requested",
                              "required": True}],
        steps=[{"step_id": "step_sb", "module": "storyboard",
                "owner_agent_id": "director", "plan_item_ids": ["cpi_sb"],
                "depends_on_step_ids": [], "parallel_group": None}],
        module_proposal_ids=["prop_sb"] if with_proposal_items else [],
        preview_id="pv_t", created_at=1.0)
    plan.approval = PlanApproval(
        plan_id=plan_id, plan_version=plan_version, decision="approved",
        source_snapshot_signature=ap_sig, decided_at=2.0).to_dict()
    store.save_plan(plan)

    pv = PlanPreviewArtifact(
        preview_id="pv_t", change_session_id=session_id, plan_id=plan_id,
        plan_version=plan_version, source_snapshot_signature=sig,
        module_operations=[bundle] if with_binding else [],
        projected_signature=sig + "_projected",   # 非空（§17 完整性）
        applyable=False, created_at=1.0)
    store.save_preview(pv)

    s = store.get_session(session_id)
    s.active_plan_id = plan_id
    s.plan_version = plan_version
    s.status = "approved_for_execution"
    s.source_snapshot_signature = sig
    store.save_session(s)
    return plan, pv


def _make_session(rt, project, session_id, sig):
    """落一个 approved_for_execution ChangeSession（经 store 直接）。"""
    from src.agents.change_protocol.models import ChangeSession
    store = ChangeProtocolStore(rt._resolve_project_dir(project), project)
    s = ChangeSession(session_id=session_id, project_name=project,
                      status="approved_for_execution",
                      source_snapshot_signature=sig, schema_version=2,
                      created_at=1.0, updated_at=1.0)
    store.save_session(s)
    return s


def _canonical_bytes(tmp_path) -> bytes:
    return (tmp_path / "output" / "project.json").read_bytes()


# ============ ① approved fresh plan -> execution created ============
def test_execute_creates_pending_execution(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    nc = NoCallClient()
    rt = _rt(tmp_path, client=nc)
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed_approved_plan(rt, "demo", "cs_t", sig)

    res = cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1, "affected_production")
    assert res["idempotent"] is False and res["blocked"] is False
    ex = res["execution"]
    assert ex["status"] == "pending"
    assert ex["stage"] == "workspace_create"   # AI-7C-1 到此 STOP
    assert ex["plan_id"] == "plan_t" and ex["plan_version"] == 1
    assert ex["execution_mode"] == "approved_plan_execution"
    assert ex["source_signature"] == sig
    assert ex["production_mode"] == "affected_production"
    assert ex["operation_digest"]                 # §14 非空
    assert len(ex["steps"]) == 1
    assert ex["steps"][0]["step_id"] == "step_sb"
    assert ex["steps"][0]["operation_count"] == 1
    assert ex["steps"][0]["operation_digest"]     # §14 per-step
    assert ex["canonical_commit"] is None         # AI-7C-3 才填
    assert ex["execution_workspace_id"] is None   # AI-7C-2 才建
    assert nc.calls == 0                          # 0 Agnes（NoCallClient 任何 chat 即 AssertionError）


# ============ ② POST twice -> same execution（幂等）============
def test_execute_idempotent_same_execution(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed_approved_plan(rt, "demo", "cs_t", sig)

    r1 = cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1)
    eid = r1["execution"]["execution_id"]
    r2 = cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1)
    assert r2["idempotent"] is True
    assert r2["execution"]["execution_id"] == eid   # 同一 execution
    # 持久化跨"重启"：新 store 仍能找到
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    assert store.find_execution("plan_t", 1).execution_id == eid


# ============ ③ unapproved plan -> PLAN_NOT_APPROVED ============
def test_execute_unapproved_rejected(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    # plan 状态仍是 preview_ready（未批准）
    _seed_approved_plan(rt, "demo", "cs_t", sig)
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    plan = store.get_plan("plan_t")
    plan.status = "preview_ready"
    plan.approval = None
    store.save_plan(plan)
    s = store.get_session("cs_t"); s.status = "ready_for_review"; store.save_session(s)

    import pytest
    with pytest.raises(AgentError) as ei:
        cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1)
    assert ei.value.code == "PLAN_NOT_APPROVED"
    assert store.find_execution("plan_t", 1) is None   # 0 execution 创建


# ============ ④ wrong version -> PLAN_VERSION_MISMATCH ============
def test_execute_wrong_version_rejected(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed_approved_plan(rt, "demo", "cs_t", sig, plan_version=1)
    import pytest
    with pytest.raises(AgentError) as ei:
        cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 2)   # 请求 v2，实际 v1
    assert ei.value.code == "PLAN_VERSION_MISMATCH"


# ============ ⑤ stale project -> blocked（§18/§81）============
def test_execute_stale_project_blocked(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed_approved_plan(rt, "demo", "cs_t", sig)

    # 批准后修改 canonical（使 current signature != approval signature）
    p = ProjectStore(tmp_path).load()
    p.scenes[0].shots[0].description = "DIFFERENT description after approval"
    ProjectStore(tmp_path).save(p)

    res = cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1)
    assert res["blocked"] is True
    ex = res["execution"]
    assert ex["status"] == "blocked"
    assert ex["stage"] == "revalidate"
    assert ex["blocker"]["code"] == "PROJECT_CHANGED_AFTER_APPROVAL"
    # canonical 未被 execute 改动（仍是修改后的 DIFFERENT）
    p2 = ProjectStore(tmp_path).load()
    assert p2.scenes[0].shots[0].description == "DIFFERENT description after approval"

    # 幂等：再 execute 同一 stale plan -> 返回同一 blocked execution（§81）
    res2 = cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1)
    assert res2["idempotent"] is True
    assert res2["execution"]["execution_id"] == ex["execution_id"]


# ============ ⑥ 0 mutation（§96/§107）============
def test_execute_zero_mutation(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    nc = NoCallClient()
    rt = _rt(tmp_path, client=nc)
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed_approved_plan(rt, "demo", "cs_t", sig)
    before_canonical = _canonical_bytes(tmp_path)

    cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1)

    # canonical 字节不变
    assert _canonical_bytes(tmp_path) == before_canonical
    # 0 workspace 文件（execution_workspace_id None，未建 workspace）
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    ex = store.find_execution("plan_t", 1)
    assert ex.execution_workspace_id is None
    # 0 production_plans.json 变化（文件不存在或未改）
    pp = tmp_path / "output" / "ai" / "production_plans.json"
    assert not pp.exists()
    # 0 Agnes（NoCallClient 任何 chat 即 AssertionError）
    assert nc.calls == 0


# ============ ⑦ operation digest 确定性 + step 绑定（§13/§14）============
def test_operation_digest_deterministic(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed_approved_plan(rt, "demo", "cs_t", sig)

    res = cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1)
    ex = res["execution"]
    # 重算 digest 应一致（执行操作 == 预览操作，§14）
    bundle = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo").get_preview("pv_t").module_operations[0]
    import hashlib
    from src.revision.models import _canonical_json
    expected = hashlib.sha256(_canonical_json(bundle["operations"]).encode()).hexdigest()
    assert ex["operation_digest"] == expected
    assert ex["steps"][0]["operation_digest"] == expected   # 单 step 全量 == step digest


def test_binding_missing_rejected(tmp_path):
    """§13：step 无对应 operation bundle -> PLAN_EXECUTION_BINDING_MISSING。"""
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed_approved_plan(rt, "demo", "cs_t", sig)
    # 删 preview 的 bundle（使 step 无绑定）
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    pv = store.get_preview("pv_t")
    pv.module_operations = []
    store.save_preview(pv)
    import pytest
    with pytest.raises(AgentError) as ei:
        cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1)
    assert ei.value.code == "PLAN_EXECUTION_BINDING_MISSING"


# ============ ⑧ execution_eligibility（§4）============
def test_eligibility_states(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed_approved_plan(rt, "demo", "cs_t", sig)

    # 无 execution + 已批准 + 未 stale -> eligible
    el = cpx.execution_eligibility(rt, "demo", "cs_t", "plan_t", 1)
    assert el["eligibility"] == "eligible"

    # execute 后 -> already_started
    cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1)
    el2 = cpx.execution_eligibility(rt, "demo", "cs_t", "plan_t", 1)
    assert el2["eligibility"] == "already_started"

    # stale -> stale
    p = ProjectStore(tmp_path).load()
    p.scenes[0].shots[0].description = "changed"
    ProjectStore(tmp_path).save(p)
    # 新 session/plan 用于 stale 判定（无 execution）
    _make_session(rt, "demo", "cs_t2", sig)
    _seed_approved_plan(rt, "demo", "cs_t2", sig, plan_id="plan_t2")
    el3 = cpx.execution_eligibility(rt, "demo", "cs_t2", "plan_t2", 1)
    assert el3["eligibility"] == "stale"

    # invalid_approval：未批准 plan
    _make_session(rt, "demo", "cs_t3", sig)
    _seed_approved_plan(rt, "demo", "cs_t3", sig, plan_id="plan_t3")
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    pl = store.get_plan("plan_t3"); pl.status = "preview_ready"; pl.approval = None
    store.save_plan(pl)
    el4 = cpx.execution_eligibility(rt, "demo", "cs_t3", "plan_t3", 1)
    assert el4["eligibility"] == "invalid_approval"


# ============ ⑨ legacy 会话不可执行（§9）============
def test_legacy_session_not_executable(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    # 构造 AI-7A legacy 会话
    legacy = {"session_id": "cs_legacy", "project_name": "demo", "status": "ready",
              "request": {"original_user_request": "改头发"},
              "consultations": [], "plan_draft_id": "plan_old", "plan_draft": {},
              "created_at": 1.0, "updated_at": 1.0}
    p = tmp_path / "output" / "agents" / "change_protocol" / "sessions" / "cs_legacy.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    import pytest
    with pytest.raises(AgentError) as ei:
        cpx.execute_plan(rt, "demo", "cs_legacy", "plan_old", 1)
    assert ei.value.code == "CHANGE_SESSION_STALE"


# ============ ⑩ startup discovery（§73/§96）============
def test_recover_executions_finds_active(tmp_path, monkeypatch):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed_approved_plan(rt, "demo", "cs_t", sig)
    cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1)

    # recover_executions（per-project 签名）发现 1 个 active execution
    n = cpx.recover_executions(tmp_path, "demo")
    assert n == 1

    # 终态 execution 不计数
    store = ChangeProtocolStore(tmp_path, "demo")
    ex = store.find_execution("plan_t", 1)
    ex.status = "completed"
    store.save_execution(ex)
    assert cpx.recover_executions(tmp_path, "demo") == 0


# ============ ⑪ creative_only / 非法 production_mode ============
def test_creative_only_mode(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed_approved_plan(rt, "demo", "cs_t", sig)

    res = cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1, "creative_only")
    ex = res["execution"]
    assert ex["production_mode"] == "creative_only"
    assert ex["status"] == "pending"   # AI-7C-1 仍 pending（creative_only 实际停在 commit，AI-7C-4）


def test_invalid_production_mode_rejected(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed_approved_plan(rt, "demo", "cs_t", sig)
    import pytest
    with pytest.raises(AgentError) as ei:
        cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1, "full_project_regeneration")
    assert ei.value.code == "PLAN_NOT_APPROVED"   # 非法 mode 复用拒绝码
