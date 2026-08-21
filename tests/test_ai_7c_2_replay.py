# tests/test_ai_7c_2_replay.py
# AI-7C-2 Execution Workspace + Exact Creative Replay focused Gate（§23/§24/§25/§27/§73/§97）。
#
# 不联网（NoCallClient：replay 路径调 Agnes 即失败——确定性、0 LLM）。每 case 独立 tmp_path。
# 覆盖 §97 Gate：
#  ① 全部批准操作 apply 到 execution workspace
#  ② 结果 == AI-7B 批准 Preview（creative_source_signature 匹配，§27）
#  ③ canonical 字节/签名不变（workspace 隔离 staging，§22）
#  ④ 重启不 double-apply step（§73 跳过已 applied）
#  ⑤ replay 失败（before-mismatch）-> blocked，canonical 不动（§97 stale revision 同路径）
#  ⑥ preview 不符 -> blocked（EXECUTION_PREVIEW_MISMATCH §27）
#  ⑦ DAG 拓扑序被尊重（§25：storyboard 在 script 之后）
#  ⑧ 0 production（production_plans.json 不建、0 Agnes）
#  ⑨ start_and_replay 端点入口推进到 canonical_commit
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
from src.revision.changeset import preview_operations_on_project
from src.revision.workspace import WorkspaceStore


# ============ fixtures ============
def _proj(name="demo"):
    return Project(
        name=name, style=StyleAnchor(),
        characters=[Character(name="Xiaoyu", description_en="a girl")],
        scenes=[Scene(scene_id="s01", summary="meet", characters=["Xiaoyu"],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1,
                                  description="see", characters=["Xiaoyu"],
                                  target_duration_s=4)])],
        story_text="once upon a time")


def _rt(tmp_path, client=None):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=client)


class NoCallClient:
    def __init__(self):
        self.calls = 0

    async def chat(self, *a, **k):
        self.calls += 1
        raise AssertionError("replay 路径不应调用 Agnes（§26/§97）")


def _make_session(rt, project, session_id, sig):
    from src.agents.change_protocol.models import ChangeSession
    store = ChangeProtocolStore(rt._resolve_project_dir(project), project)
    store.save_session(ChangeSession(session_id=session_id, project_name=project,
                                     status="approved_for_execution",
                                     source_snapshot_signature=sig, schema_version=2,
                                     created_at=1.0, updated_at=1.0))


def _seed(rt, project, session_id, sig, *, projected_sig=None,
          story_before="once upon a time", story_after="once upon a time, revised",
          shot_before="see", shot_after="see the girl arrive",
          two_steps=True):
    """落 approved plan（2 step：script update_story_text -> storyboard update_shot）+
    preview（含真实 projected_signature，由 preview_operations_on_project 算出）。"""
    store = ChangeProtocolStore(rt._resolve_project_dir(project), project)
    # proposals + items
    for pid, mod, iid, action in [("prop_sc", "script", "pi_sc", "update_story_text"),
                                  ("prop_sb", "storyboard", "pi_sb", "update_shot")]:
        store.save_proposal(ModuleProposal(
            proposal_id=pid, change_session_id=session_id, module=mod,
            owner_agent_id=("script_writer" if mod == "script" else "director"),
            round=1, status="completed", summary=action,
            items=[ModuleProposalItem(item_id=iid, module=mod, action=action,
                                      target_refs=[{"scene_id": "s01", "shot_id": "s01_c01"}],
                                      intent={"shot_id": "s01_c01"}, reason="user").to_dict()],
            source_snapshot_signature=sig, created_at=1.0, updated_at=1.0))

    op_sc = ChangeOperation(op_type="update_story_text",
                           story_text=BeforeAfter(before=story_before, after=story_after))
    op_sb = ChangeOperation(op_type="update_shot", scene_id="s01", shot_id="s01_c01",
                            description=BeforeAfter(before=shot_before, after=shot_after))
    ops = [op_sc, op_sb]
    # 真实 projected signature
    source = ProjectStore(rt._resolve_project_dir(project)).load()
    candidate, _diff, _impact, _warn = preview_operations_on_project(source, ops)
    real_projected = creative_source_signature(candidate.to_dict())

    steps = [{"step_id": "step_sc", "module": "script", "owner_agent_id": "script_writer",
              "plan_item_ids": ["cpi_sc"], "depends_on_step_ids": [],
              "parallel_group": None},
             {"step_id": "step_sb", "module": "storyboard", "owner_agent_id": "director",
              "plan_item_ids": ["cpi_sb"], "depends_on_step_ids": ["step_sc"] if two_steps else [],
              "parallel_group": None}]
    bundles = [{"step_id": "step_sc", "module": "script", "owner_agent_id": "script_writer",
                "proposal_item_ids": ["pi_sc"], "operations": [op_sc.to_dict()]},
               {"step_id": "step_sb", "module": "storyboard", "owner_agent_id": "director",
                "proposal_item_ids": ["pi_sb"], "operations": [op_sb.to_dict()]}]
    if not two_steps:
        steps = [steps[0], {**steps[1], "depends_on_step_ids": []}]

    plan = UnifiedChangePlan(
        plan_id="plan_t", change_session_id=session_id, project_name=project,
        version=1, source_snapshot_signature=sig, status="approved_for_execution",
        direct_change_items=[{"plan_item_id": "cpi_sc", "module": "script",
                              "owner_agent_id": "script_writer", "proposal_item_ids": ["pi_sc"],
                              "scope_refs": [], "summary": "story", "source": "user_requested",
                              "required": True},
                             {"plan_item_id": "cpi_sb", "module": "storyboard",
                              "owner_agent_id": "director", "proposal_item_ids": ["pi_sb"],
                              "scope_refs": [{"shot_id": "s01_c01"}], "summary": "shot",
                              "source": "user_requested", "required": True}],
        steps=steps, module_proposal_ids=["prop_sc", "prop_sb"], preview_id="pv_t",
        created_at=1.0)
    plan.approval = PlanApproval(plan_id="plan_t", plan_version=1, decision="approved",
                                 source_snapshot_signature=sig, decided_at=2.0).to_dict()
    store.save_plan(plan)

    pv = PlanPreviewArtifact(
        preview_id="pv_t", change_session_id=session_id, plan_id="plan_t", plan_version=1,
        source_snapshot_signature=sig, module_operations=bundles,
        projected_signature=(projected_sig if projected_sig is not None else real_projected),
        applyable=False, created_at=1.0)
    store.save_preview(pv)

    s = store.get_session(session_id)
    s.active_plan_id = "plan_t"; s.plan_version = 1; s.status = "approved_for_execution"
    store.save_session(s)
    return plan, pv, real_projected


def _canonical_bytes(tmp_path) -> bytes:
    return (tmp_path / "output" / "project.json").read_bytes()


# ============ ① ② ③ happy path：apply + 等价 + canonical 不变 ============
def test_replay_applies_and_verifies(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    nc = NoCallClient()
    rt = _rt(tmp_path, client=nc)
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed(rt, "demo", "cs_t", sig)
    before_canonical = _canonical_bytes(tmp_path)

    cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1)   # AI-7C-1：到 workspace_create
    res = cpx.run_creative_replay(rt, "demo", _exec_id(rt, "demo"))

    ex = res["execution"]
    assert ex["stage"] == "canonical_commit"        # AI-7C-2 到此 STOP，AI-7C-3 接力
    assert all(s["status"] == "applied" for s in ex["steps"])   # ① 全 apply
    # ② 等价：final workspace sig == projected（run_creative_replay 已校验，stage 推进即通过）
    # ③ canonical 不变
    assert _canonical_bytes(tmp_path) == before_canonical
    # workspace 已建
    assert ex["execution_workspace_id"]
    wsstore = WorkspaceStore(tmp_path, "demo", lambda: ProjectStore(tmp_path).load())
    ws = wsstore.load(ex["execution_workspace_id"])
    assert ws.revision == 2   # 2 step 各 bump 1
    # ② working 已是 candidate 态（创作态确实变了，≠ 源 sig；stage 到 canonical_commit 即证等价通过）
    from src.revision.models import creative_source_signature as csig
    final = wsstore.load_working_project(ex["execution_workspace_id"])
    final_sig = csig(final.to_dict())
    assert final_sig != sig   # 创作态确实变了
    # final sig == 批准 preview 的 projected_signature（§27 等价）
    pv = ChangeProtocolStore(tmp_path, "demo").get_preview("pv_t")
    assert final_sig == pv.projected_signature
    assert nc.calls == 0   # 0 Agnes


def _exec_id(rt, project):
    return ChangeProtocolStore(rt._resolve_project_dir(project), project).find_execution("plan_t", 1).execution_id


# ============ ④ 重启不 double-apply ============
def test_replay_idempotent_no_double_apply(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed(rt, "demo", "cs_t", sig)
    cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1)
    eid = _exec_id(rt, "demo")

    r1 = cpx.run_creative_replay(rt, "demo", eid)
    wsstore = WorkspaceStore(tmp_path, "demo", lambda: ProjectStore(tmp_path).load())
    rev_after_first = wsstore.load(r1["execution"]["execution_workspace_id"]).revision
    assert r1["execution"]["stage"] == "canonical_commit"

    # 模拟重启：再调 run_creative_replay（已 applied step 跳过）
    r2 = cpx.run_creative_replay(rt, "demo", eid)
    assert r2["idempotent"] is True
    rev_after_second = wsstore.load(r2["execution"]["execution_workspace_id"]).revision
    assert rev_after_second == rev_after_first   # ④ 未 double-apply（revision 不再涨）

    # changeset 也只各一条（每 step 一条，不重复）
    assert len(wsstore.list_changesets(r1["execution"]["execution_workspace_id"])) == 2


# ============ ⑤ replay 失败 -> blocked（before-mismatch，§97 stale 同路径）============
def test_replay_before_mismatch_blocks(tmp_path):
    """ops 的 before 在 seed 时合法（projected 可算）；replay 前外部破坏 workspace working，
    使 step 的 before 与 working 不符 -> ChangeSetError -> execution blocked（canonical 不动）。"""
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed(rt, "demo", "cs_t", sig)   # 合法 ops
    cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1)
    eid = _exec_id(rt, "demo")

    # 手动建 workspace（模拟 run_creative_replay 的 workspace_create）+ 破坏 working shot desc
    wsstore = WorkspaceStore(tmp_path, "demo", lambda: ProjectStore(tmp_path).load())
    ws = wsstore.create(name="manual")
    store = ChangeProtocolStore(tmp_path, "demo")
    ex = store.get_execution(eid)
    ex.execution_workspace_id = ws.workspace_id
    ex.stage = "creative_replay"; ex.status = "running"
    store.save_execution(ex)
    wp = wsstore.load_working_project(ws.workspace_id)
    wp.scenes[0].shots[0].description = "CORRUPTED_EXTERNAL"
    wsstore.write_working_project_dict(ws.workspace_id, wp.to_dict())

    res = cpx.run_creative_replay(rt, "demo", eid)
    ex2 = res["execution"]
    assert ex2["status"] == "blocked"
    assert ex2["stage"] == "creative_replay"
    # step_sc（story_text before 仍匹配 working story_text）应 applied；step_sb before 不符 -> failed
    sc_rec = next(s for s in ex2["steps"] if s["step_id"] == "step_sc")
    sb_rec = next(s for s in ex2["steps"] if s["step_id"] == "step_sb")
    assert sc_rec["status"] == "applied"
    assert sb_rec["status"] == "failed"
    # canonical 不动（workspace 隔离，原值未变）
    assert ProjectStore(tmp_path).load().scenes[0].shots[0].description == "see"


# ============ ⑥ preview 不符 -> blocked（§27）============
def test_replay_preview_mismatch_blocks(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed(rt, "demo", "cs_t", sig, projected_sig="WRONG_PROJECTED_SIGNATURE")
    cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1)
    eid = _exec_id(rt, "demo")

    res = cpx.run_creative_replay(rt, "demo", eid)
    ex = res["execution"]
    assert ex["status"] == "blocked"
    assert ex["stage"] == "workspace_verify"
    assert ex["blocker"]["code"] == "EXECUTION_PREVIEW_MISMATCH"
    # canonical 不动（workspace verify 不碰 canonical）
    assert ProjectStore(tmp_path).load().scenes[0].shots[0].description == "see"


# ============ ⑦ DAG 拓扑序（§25：storyboard 在 script 后）============
def test_replay_respects_dag_order(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed(rt, "demo", "cs_t", sig, two_steps=True)
    cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1)
    eid = _exec_id(rt, "demo")

    res = cpx.run_creative_replay(rt, "demo", eid)
    ex = res["execution"]
    # step_sc 的 revision_before=0, after=1；step_sb before=1, after=2（依赖序）
    sc_rec = next(s for s in ex["steps"] if s["step_id"] == "step_sc")
    sb_rec = next(s for s in ex["steps"] if s["step_id"] == "step_sb")
    assert sc_rec["workspace_revision_before"] == 0
    assert sc_rec["workspace_revision_after"] == 1
    assert sb_rec["workspace_revision_before"] == 1   # 在 sc 之后
    assert sb_rec["workspace_revision_after"] == 2


# ============ ⑧ 0 production ============
def test_replay_zero_production(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    nc = NoCallClient()
    rt = _rt(tmp_path, client=nc)
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed(rt, "demo", "cs_t", sig)
    cpx.execute_plan(rt, "demo", "cs_t", "plan_t", 1)
    cpx.run_creative_replay(rt, "demo", _exec_id(rt, "demo"))
    # 无 production_plans.json / 无 autopilot_runs
    assert not (tmp_path / "output" / "ai" / "production_plans.json").exists()
    assert not (tmp_path / "output" / "ai" / "autopilot_runs").exists()
    assert nc.calls == 0


# ============ ⑨ start_and_replay 端点入口 ============
def test_start_and_replay_advances_to_canonical_commit(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed(rt, "demo", "cs_t", sig)

    res = cpx.start_and_replay(rt, "demo", "cs_t", "plan_t", 1, "affected_production")
    assert res["execution"]["stage"] == "canonical_commit"
    assert all(s["status"] == "applied" for s in res["execution"]["steps"])

    # 幂等：再调一次 -> 同 execution，no further advance
    res2 = cpx.start_and_replay(rt, "demo", "cs_t", "plan_t", 1, "affected_production")
    assert res2["execution"]["execution_id"] == res["execution"]["execution_id"]
    assert res2["execution"]["stage"] == "canonical_commit"


# ============ stale execution（approved 后项目变）-> execute blocked，不 replay ============
def test_stale_execution_does_not_replay(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    _seed(rt, "demo", "cs_t", sig)
    # 批准后改 canonical
    p = ProjectStore(tmp_path).load()
    p.story_text = "DIFFERENT after approval"
    ProjectStore(tmp_path).save(p)

    res = cpx.start_and_replay(rt, "demo", "cs_t", "plan_t", 1, "affected_production")
    assert res["blocked"] is True
    ex = res["execution"]
    assert ex["status"] == "blocked"
    assert ex["stage"] == "revalidate"
    assert ex["blocker"]["code"] == "PROJECT_CHANGED_AFTER_APPROVAL"
    # 不建 workspace（stale 在 workspace_create 前 block）
    assert ex["execution_workspace_id"] is None
