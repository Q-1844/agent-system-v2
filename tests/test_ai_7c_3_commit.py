# tests/test_ai_7c_3_commit.py
# AI-7C-3 Atomic Canonical Creative Commit focused Gate（§30/§31/§32/§33/§34/§35/§36/§98）。
#
# 不联网（NoCallClient：commit 路径调 Agnes 即失败）。每 case 独立 tmp_path。
# 覆盖 §98 mandatory crash tests + §33 concurrency：
#  Window A：COMMIT_INTENT 已写、canonical 未 save（current==before）-> resume commit 一次
#  Window B：canonical save 成功、COMMIT_DONE 前崩（current==expected_after）-> reconcile committed，0 二次 save
#  Window C：提交前 canonical 被改成年后无关创作态 -> §32 第二次 stale -> blocked，0 覆写
#  production-state preservation：批准后加 production 态 -> commit 保留（§30 reload-latest）
#  §33 lock 序列化：project_write_lock 串行化 + commit 持锁
#  creative_only -> done/completed；affected_production -> character_sync/running
#  幂等：重复 commit 0 二次 save；audit snapshot canonical_before.json 存在
import asyncio
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.core.write_lock import project_write_lock, _clear_for_test
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.change_protocol import execution_service as cpx
from src.agents.change_protocol.store import ChangeProtocolStore
from src.agents.change_protocol.models import (UnifiedChangePlan, PlanPreviewArtifact,
                                                PlanApproval, ModuleProposal,
                                                ModuleProposalItem)
from src.agents.change_protocol.execution_models import CanonicalCommitRecord
from src.revision.models import ChangeOperation, BeforeAfter, creative_source_signature
from src.revision.changeset import preview_operations_on_project


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
        raise AssertionError("commit 路径不应调用 Agnes（§26）")


def _make_session(rt, project, session_id, sig):
    from src.agents.change_protocol.models import ChangeSession
    store = ChangeProtocolStore(rt._resolve_project_dir(project), project)
    store.save_session(ChangeSession(session_id=session_id, project_name=project,
                                     status="approved_for_execution",
                                     source_snapshot_signature=sig, schema_version=2,
                                     created_at=1.0, updated_at=1.0))


def _seed(rt, project, session_id, sig, *, projected_sig=None):
    """approved plan：script update_story_text -> storyboard update_shot；projected 由纯核心算。"""
    store = ChangeProtocolStore(rt._resolve_project_dir(project), project)
    for pid, mod, iid in [("prop_sc", "script", "pi_sc"), ("prop_sb", "storyboard", "pi_sb")]:
        store.save_proposal(ModuleProposal(
            proposal_id=pid, change_session_id=session_id, module=mod,
            owner_agent_id=("script_writer" if mod == "script" else "director"),
            round=1, status="completed", summary=mod,
            items=[ModuleProposalItem(item_id=iid, module=mod, action="x",
                                      target_refs=[], intent={}, reason="u").to_dict()],
            source_snapshot_signature=sig, created_at=1.0, updated_at=1.0))
    op_sc = ChangeOperation(op_type="update_story_text",
                           story_text=BeforeAfter(before="once upon a time",
                                                  after="once upon a time, revised"))
    op_sb = ChangeOperation(op_type="update_shot", scene_id="s01", shot_id="s01_c01",
                            description=BeforeAfter(before="see", after="see the girl arrive"))
    ops = [op_sc, op_sb]
    source = ProjectStore(rt._resolve_project_dir(project)).load()
    candidate, _, _, _ = preview_operations_on_project(source, ops)
    real_projected = creative_source_signature(candidate.to_dict())
    steps = [{"step_id": "step_sc", "module": "script", "owner_agent_id": "script_writer",
              "plan_item_ids": ["cpi_sc"], "depends_on_step_ids": [], "parallel_group": None},
             {"step_id": "step_sb", "module": "storyboard", "owner_agent_id": "director",
              "plan_item_ids": ["cpi_sb"], "depends_on_step_ids": ["step_sc"], "parallel_group": None}]
    bundles = [{"step_id": "step_sc", "module": "script", "owner_agent_id": "script_writer",
                "proposal_item_ids": ["pi_sc"], "operations": [op_sc.to_dict()]},
               {"step_id": "step_sb", "module": "storyboard", "owner_agent_id": "director",
                "proposal_item_ids": ["pi_sb"], "operations": [op_sb.to_dict()]}]
    plan = UnifiedChangePlan(plan_id="plan_t", change_session_id=session_id,
                             project_name=project, version=1, source_snapshot_signature=sig,
                             status="approved_for_execution",
                             direct_change_items=[{"plan_item_id": "cpi_sc", "module": "script",
                                "owner_agent_id": "script_writer", "proposal_item_ids": ["pi_sc"],
                                "scope_refs": [], "summary": "s", "source": "user_requested",
                                "required": True},
                               {"plan_item_id": "cpi_sb", "module": "storyboard",
                                "owner_agent_id": "director", "proposal_item_ids": ["pi_sb"],
                                "scope_refs": [{"shot_id": "s01_c01"}], "summary": "s",
                                "source": "user_requested", "required": True}],
                             steps=steps, module_proposal_ids=["prop_sc", "prop_sb"],
                             preview_id="pv_t", created_at=1.0)
    plan.approval = PlanApproval(plan_id="plan_t", plan_version=1, decision="approved",
                                 source_snapshot_signature=sig, decided_at=2.0).to_dict()
    store.save_plan(plan)
    pv = PlanPreviewArtifact(preview_id="pv_t", change_session_id=session_id, plan_id="plan_t",
                             plan_version=1, source_snapshot_signature=sig,
                             module_operations=bundles,
                             projected_signature=(projected_sig if projected_sig is not None
                                                  else real_projected),
                             applyable=False, created_at=1.0)
    store.save_preview(pv)
    s = store.get_session(session_id)
    s.active_plan_id = "plan_t"; s.plan_version = 1; s.status = "approved_for_execution"
    store.save_session(s)
    return real_projected


def _exec(rt, project):
    return ChangeProtocolStore(rt._resolve_project_dir(project), project).find_execution("plan_t", 1)


def _canonical_bytes(tmp_path) -> bytes:
    return (tmp_path / "output" / "project.json").read_bytes()


def _setup_to_canonical_commit(tmp_path, mode="affected_production"):
    """跑 start_and_replay 到 stage=canonical_commit（workspace replay 完，未 commit）。"""
    ProjectStore(tmp_path).save(_proj())
    nc = NoCallClient()
    rt = _rt(tmp_path, client=nc)
    sig = creative_source_signature(_proj().to_dict())
    _make_session(rt, "demo", "cs_t", sig)
    projected = _seed(rt, "demo", "cs_t", sig)
    cpx.start_and_replay(rt, "demo", "cs_t", "plan_t", 1, mode)
    return rt, sig, projected, nc


# ============ happy path：affected_production -> character_sync ============
def test_commit_affected_production(tmp_path):
    rt, sig, projected, nc = _setup_to_canonical_commit(tmp_path)
    ex0 = _exec(rt, "demo")
    res = asyncio.run(cpx.commit_canonical(rt, "demo", ex0.execution_id))
    ex = res["execution"]
    assert res["committed"] is True
    assert ex["stage"] == "character_sync"   # AI-7C-4 接力
    assert ex["status"] == "running"
    cc = ex["canonical_commit"]
    assert cc["status"] == "committed"
    assert cc["before_signature"] == sig
    assert cc["expected_after_signature"] == projected
    # canonical 已是 projected 态
    assert creative_source_signature(ProjectStore(tmp_path).load().to_dict()) == projected
    # audit snapshot 存在
    assert ChangeProtocolStore(tmp_path, "demo").load_execution_audit(ex["execution_id"],
                                                                       "canonical_before.json") is not None
    assert nc.calls == 0


# ============ creative_only -> done/completed ============
def test_commit_creative_only(tmp_path):
    rt, sig, projected, nc = _setup_to_canonical_commit(tmp_path, mode="creative_only")
    ex0 = _exec(rt, "demo")
    res = asyncio.run(cpx.commit_canonical(rt, "demo", ex0.execution_id))
    ex = res["execution"]
    assert ex["stage"] == "done"
    assert ex["status"] == "completed"
    assert ex["production_skipped_by_user"] is True   # §66
    assert creative_source_signature(ProjectStore(tmp_path).load().to_dict()) == projected
    assert nc.calls == 0


# ============ 幂等：重复 commit 0 二次 save ============
def test_commit_idempotent_no_second_save(tmp_path):
    rt, sig, projected, nc = _setup_to_canonical_commit(tmp_path)
    ex0 = _exec(rt, "demo")
    asyncio.run(cpx.commit_canonical(rt, "demo", ex0.execution_id))
    after1 = _canonical_bytes(tmp_path)
    res2 = asyncio.run(cpx.commit_canonical(rt, "demo", ex0.execution_id))
    assert res2["idempotent"] is True and res2["committed"] is True
    assert _canonical_bytes(tmp_path) == after1   # 0 二次 save


# ============ Window A：COMMIT_INTENT 写了、canonical 未 save -> resume commit 一次 ============
def test_window_a_resume_commit_once(tmp_path):
    rt, sig, projected, _ = _setup_to_canonical_commit(tmp_path)
    store = ChangeProtocolStore(tmp_path, "demo")
    ex = store.find_execution("plan_t", 1)
    # 模拟：COMMIT_INTENT 已写（崩溃前），canonical 未 save（仍 == source == before）
    ex.canonical_commit = CanonicalCommitRecord(
        status="commit_intent", before_signature=sig,
        expected_after_signature=projected, operation_digest=ex.operation_digest,
        commit_intent_at=1.0).to_dict()
    ex.stage = "canonical_commit"; ex.status = "running"
    store.save_execution(ex)
    assert creative_source_signature(ProjectStore(tmp_path).load().to_dict()) == sig   # 未 save

    res = asyncio.run(cpx.commit_canonical(rt, "demo", ex.execution_id))
    assert res["committed"] is True
    # canonical 现在 == projected（save 了一次）
    assert creative_source_signature(ProjectStore(tmp_path).load().to_dict()) == projected
    assert res["execution"]["canonical_commit"]["status"] == "committed"


# ============ Window B：save 成功、COMMIT_DONE 前崩 -> reconcile，0 二次 save ============
def test_window_b_reconcile_no_second_save(tmp_path):
    rt, sig, projected, _ = _setup_to_canonical_commit(tmp_path)
    store = ChangeProtocolStore(tmp_path, "demo")
    ex = store.find_execution("plan_t", 1)
    # 模拟：save 已成功（canonical == projected），COMMIT_DONE 未写（cc.status=commit_intent）
    proj = ProjectStore(tmp_path).load()
    proj.story_text = "once upon a time, revised"
    proj.scenes[0].shots[0].description = "see the girl arrive"
    ProjectStore(tmp_path).save(proj)   # 模拟「save 成功」
    assert creative_source_signature(proj.to_dict()) == projected
    ex.canonical_commit = CanonicalCommitRecord(
        status="commit_intent", before_signature=sig,
        expected_after_signature=projected, operation_digest=ex.operation_digest,
        commit_intent_at=1.0).to_dict()
    ex.stage = "canonical_commit"; ex.status = "running"
    store.save_execution(ex)
    before_bytes = _canonical_bytes(tmp_path)   # == projected 态

    res = asyncio.run(cpx.commit_canonical(rt, "demo", ex.execution_id))
    assert res["idempotent"] is True and res["committed"] is True   # Case B reconcile
    # 0 二次 save：canonical 字节不变
    assert _canonical_bytes(tmp_path) == before_bytes
    assert res["execution"]["canonical_commit"]["status"] == "committed"


# ============ Window C：提交前 canonical 被改成年后无关创作态 -> §32 blocked，0 覆写 ============
def test_window_c_second_stale_check_blocks(tmp_path):
    rt, sig, projected, _ = _setup_to_canonical_commit(tmp_path)
    ex0 = _exec(rt, "demo")
    # 提交临界区前，canonical 被改成年后无关创作态（sig != source）
    p = ProjectStore(tmp_path).load()
    p.story_text = "TOTALLY UNRELATED creative change after approval"
    ProjectStore(tmp_path).save(p)
    unrelated_bytes = _canonical_bytes(tmp_path)

    res = asyncio.run(cpx.commit_canonical(rt, "demo", ex0.execution_id))
    assert res["blocked"] is True
    assert res["blocker_code"] == "PROJECT_CHANGED_AFTER_APPROVAL"
    # 0 覆写：canonical 仍是无关改动态
    assert _canonical_bytes(tmp_path) == unrelated_bytes
    ex = res["execution"]
    assert ex["status"] == "blocked"
    assert ex["canonical_commit"] is None or ex["canonical_commit"].get("status") != "committed"


# ============ production-state preservation（§30 reload-latest）============
def test_commit_preserves_unrelated_production_state(tmp_path):
    rt, sig, projected, _ = _setup_to_canonical_commit(tmp_path)
    ex0 = _exec(rt, "demo")
    # 批准后加 production 态（video.local，非创作字段，creative sig 不变）
    def _add_video(shot):
        shot.video.local = "/preexisting/video.mp4"
        shot.video.status = "done"
    ProjectStore(tmp_path).update_shot("s01_c01", _add_video)
    assert creative_source_signature(ProjectStore(tmp_path).load().to_dict()) == sig   # 创作 sig 不变

    res = asyncio.run(cpx.commit_canonical(rt, "demo", ex0.execution_id))
    assert res["committed"] is True
    p = ProjectStore(tmp_path).load()
    # 创作变更已提交
    assert p.scenes[0].shots[0].description == "see the girl arrive"
    assert p.story_text == "once upon a time, revised"
    # §30：无关 production 态被保留（未被旧 workspace 字节覆盖）
    assert p.scenes[0].shots[0].video.local == "/preexisting/video.mp4"
    assert p.scenes[0].shots[0].video.status == "done"


# ============ §33 lock 序列化 ============
def test_project_write_lock_serializes():
    """§33：project_write_lock 串行化两个临界区（不重叠）。commit 临界区全程 sync（无 await），
    单线程 event loop 中 load→preview→save 原子执行不与并发 sync 写交错；锁为 §33 原语 + 防御
    未来 async 临界区。"""
    _clear_for_test()
    log = []

    async def _section(label, delay):
        async with project_write_lock("P"):
            log.append(("start", label))
            await asyncio.sleep(delay)
            log.append(("end", label))

    async def _main():
        await asyncio.gather(_section("A", 0.02), _section("B", 0.02))

    asyncio.run(_main())
    # A 完全在 B 之前（或反之）：start/end 不交错
    a_start = log.index(("start", "A")); a_end = log.index(("end", "A"))
    b_start = log.index(("start", "B")); b_end = log.index(("end", "B"))
    assert (a_end < b_start) or (b_end < a_start)
