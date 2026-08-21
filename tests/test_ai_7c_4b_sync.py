# tests/test_ai_7c_4b_sync.py
# AI-7C-4b character sync orchestration + §68 first-Agnes hard gate focused tests（§43/§45/§46/§47/§51/§68/§99）。
#
# run_character_sync 用 injectable fake view_generator 测（AI-6F fake/test 分离模式；真 JobQueue+chain
# handler 路径在 §103 acceptance 接通）。不联网（0 真 Agnes）。
import asyncio

from src.core.models import Project, Scene, Shot, Character, StyleAnchor, Asset
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


def _rt(tmp_path):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json",
                        client=_NoCall())


class _NoCall:
    def __init__(self): self.calls = 0

    async def chat(self, *a, **k):
        self.calls += 1
        raise AssertionError("character sync 路径不应调 Agnes（§68）")


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


def _fake_view_generator():
    """deterministic fake：返回 3 view Asset（不调 Agnes，§46 fake/test 分离）。"""
    calls = []

    async def gen(char_name):
        calls.append(char_name)
        return {v: Asset(url=f"https://fake/{char_name}/{v}.png", status="done")
                for v in ("front", "side", "back")}
    return gen, calls


def _exec(rt):
    return ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo").find_execution("plan_t", 1)


# ============ ① character sync：views 持久化 + record + stage ============
def test_character_sync_persists_views(tmp_path):
    rt, sig, projected, nc = _seed_and_commit(tmp_path)
    ex0 = _exec(rt)
    assert ex0.stage == "character_sync"
    gen, calls = _fake_view_generator()
    res = asyncio.run(cpx.run_character_sync(rt, "demo", ex0.execution_id, view_generator=gen))
    ex = res["execution"]
    assert ex["stage"] == "production_planning"   # AI-7C-5 接力
    assert res["character_synced"] == 1
    assert calls == ["Xiaoyu"]
    # Character.views 已持久化（3 view，done）
    ch = next(c for c in ProjectStore(tmp_path).load().characters if c.name == "Xiaoyu")
    assert set(ch.views.keys()) == {"front", "side", "back"}
    assert all(v.status == "done" for v in ch.views.values())
    # CharacterSyncRecord 记录
    assert len(ex["character_sync"]) == 1
    assert ex["character_sync"][0]["character_name"] == "Xiaoyu"
    assert nc.calls == 0   # 0 真 Agnes


# ============ ② 幂等：重启不重复生成（§46）============
def test_character_sync_idempotent_no_regen(tmp_path):
    rt, sig, projected, _ = _seed_and_commit(tmp_path)
    ex0 = _exec(rt)
    gen, calls = _fake_view_generator()
    asyncio.run(cpx.run_character_sync(rt, "demo", ex0.execution_id, view_generator=gen))
    calls_after_first = list(calls)
    # 再调（模拟重启 resume）：已 sync char 跳过
    res2 = asyncio.run(cpx.run_character_sync(rt, "demo", ex0.execution_id, view_generator=gen))
    assert res2["idempotent"] is True
    assert calls == calls_after_first   # 0 新调用
    assert res2["execution"]["stage"] == "production_planning"


# ============ ③ §68 gate：未提交 -> 0 Agnes（PRODUCTION_PREREQUISITE_FAILED）============
def test_gate_not_committed_blocks(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path)
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
    # 只 replay 到 canonical_commit（未 commit），再模拟「corrupt 到 character_sync 但无 commit 记录」
    cpx.start_and_replay(rt, "demo", "cs_t", "plan_t", 1, "affected_production")
    ex0 = _exec(rt)
    assert ex0.stage == "canonical_commit"
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    ex0.stage = "character_sync"; ex0.canonical_commit = None   # 防御场景：到 sync 但未提交
    store.save_execution(ex0)
    gen, calls = _fake_view_generator()
    import pytest
    with pytest.raises(AgentError) as ei:
        asyncio.run(cpx.run_character_sync(rt, "demo", ex0.execution_id, view_generator=gen))
    assert ei.value.code == "PRODUCTION_PREREQUISITE_FAILED"
    assert calls == []   # §68：0 view 生成（0 Agnes）


# ============ ④ §68 gate：提交后 canonical 被改 -> 0 Agnes ============
def test_gate_frozen_source_changed_blocks(tmp_path):
    rt, sig, projected, _ = _seed_and_commit(tmp_path)
    ex0 = _exec(rt)
    # 提交后再改 canonical 创作态（冻结源签名不匹配）
    p = ProjectStore(tmp_path).load()
    p.story_text = "tampered after commit"
    ProjectStore(tmp_path).save(p)
    gen, calls = _fake_view_generator()
    import pytest
    with pytest.raises(AgentError) as ei:
        asyncio.run(cpx.run_character_sync(rt, "demo", ex0.execution_id, view_generator=gen))
    assert ei.value.code == "PRODUCTION_PREREQUISITE_FAILED"
    assert calls == []   # 0 Agnes


# ============ ⑤ creative_only：character_sync no-op（§47，已 done）============
def test_creative_only_no_character_sync(tmp_path):
    rt, sig, projected, _ = _seed_and_commit(tmp_path, "creative_only")
    ex0 = _exec(rt)
    assert ex0.stage == "done"   # creative_only 已在 commit 终止
    gen, calls = _fake_view_generator()
    res = asyncio.run(cpx.run_character_sync(rt, "demo", ex0.execution_id, view_generator=gen))
    assert res["idempotent"] is True
    assert calls == []   # §47：creative_only 不 regen views


# ============ ⑥ capabilities 注册（§45/§49）============
def test_capabilities_registered():
    from src.agents.registry import Registries
    r = Registries()
    assert r.capabilities.can_execute("character_designer", "character.views.generate", "execute")
    assert r.capabilities.can_execute("shot_producer", "shot.audio", "execute")
    # retry/cancel 亦授权
    assert r.capabilities.can_execute("shot_producer", "shot.audio", "retry")
    assert r.capabilities.can_execute("character_designer", "character.views.generate", "cancel")


# ============ ⑦ §99 appearance-only：char change -> manifest 标 char regen + shots regen ============
def test_appearance_only_char_change_manifest(tmp_path):
    """§99 Case C：仅改角色外貌 -> char REGENERATE + 引用 shot frame/video REGENERATE，audio KEEP。
    creative_only 仍 0 Agnes（已 7C-4a 验证）；affected_production 走 character_sync（① 已验证）。"""
    rt, sig, projected, _ = _seed_and_commit(tmp_path)
    ex0 = _exec(rt)
    m = cpx.get_production_manifest(rt, "demo", ex0.execution_id)
    assert m["character_actions"]["Xiaoyu"] == "regenerate"
    assert m["shot_actions"]["s01_c01"]["first_frame"] == "regenerate"
    assert m["shot_actions"]["s01_c01"]["video"] == "regenerate"
    assert m["shot_actions"]["s01_c01"]["audio"] == "keep"   # 仅外貌变，audio keep
