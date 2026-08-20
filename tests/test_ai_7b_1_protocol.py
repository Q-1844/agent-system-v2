# tests/test_ai_7b_1_protocol.py
# AI-7B-1 协议 + 硬运行边界 focused Gate（§6/§9/§76/§87/§88/§90/§91/§119/L）。
#
# 不联网（NoCallClient：批准路径调 Agnes 即失败）。每 case 独立 tmp_path。
# 覆盖：① mode 硬闸 ② legacy AI-7A 会话只读不可执行（§9） ③ 持久化跨重启
# ④ 批准绑定 plan_version+signature；stale→409/0 mutation（§90/§116） ⑤ 批准硬停止
# 负测（canonical/Workspace/Plan/Job 不变、0 Agnes、applyable 恒 False）（§119/L）
# ⑥ 项目隔离（§91） ⑦ 重启恢复 in-flight→blocked、routing 稳定（§87） ⑧ 批准幂等（§88）。
import json

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.core.store import ProjectStore
from src.agents.runtime import AgentRuntime, AgentError
from src.agents.change_protocol import AgentExecutionMode, assert_mode_allowed
from src.agents.change_protocol import service as cps
from src.agents.change_protocol.store import ChangeProtocolStore
from src.agents.change_protocol import migrations
from src.agents.change_protocol.models import (
    UnifiedChangePlan, PlanPreviewArtifact, ChangeDecisionRequest, ChangeDecisionOption)


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
    """批准/协议路径不得调用 Agnes（§119/L）。任何 chat -> AssertionError。"""
    def __init__(self):
        self.calls = 0

    async def chat(self, *a, **k):
        self.calls += 1
        raise AssertionError("协议/批准路径不应调用 Agnes（§119/L）")


def _seed_preview_ready_plan(rt, project, session_id, sig, plan_version=1,
                             plan_status="preview_ready"):
    """7B-1：编译器/投影在 7B-2..4。本 helper 直接落一个 preview_ready plan + preview，
    供批准流测试（approve_plan 只读这些 artifact，不依赖编译器）。"""
    store = ChangeProtocolStore(rt._resolve_project_dir(project), project)
    plan = UnifiedChangePlan(
        plan_id="plan_t", change_session_id=session_id, project_name=project,
        version=plan_version, source_snapshot_signature=sig, status=plan_status,
        preview_id="pv_t", created_at=1.0)
    store.save_plan(plan)
    pv = PlanPreviewArtifact(
        preview_id="pv_t", change_session_id=session_id, plan_id="plan_t",
        plan_version=plan_version, source_snapshot_signature=sig, applyable=False,
        created_at=1.0)
    store.save_preview(pv)
    s = store.get_session(session_id)
    s.active_plan_id = plan.plan_id
    s.plan_version = plan_version
    s.status = "ready_for_review"
    store.save_session(s)
    return plan


# ============ ① mode 硬闸（§6）============
def test_mode_guard_code_not_prompt():
    """§6：守卫是代码。READ_ONLY 仅 inspect；PROPOSAL_ONLY inspect+propose。"""
    # allowed
    assert_mode_allowed(AgentExecutionMode.READ_ONLY.value, "inspect")
    assert_mode_allowed(AgentExecutionMode.PROPOSAL_ONLY.value, "inspect")
    assert_mode_allowed(AgentExecutionMode.PROPOSAL_ONLY.value, "propose")
    # rejected
    for mode, op in [("read_only", "propose"), ("proposal_only", "execute"),
                     ("proposal_only", "retry"), ("proposal_only", "cancel"),
                     ("proposal_only", "mutate"), ("proposal_only", "produce")]:
        try:
            assert_mode_allowed(mode, op)
            assert False, f"应拒绝 {mode}/{op}"
        except AgentError as e:
            assert e.code == "permission_denied"
    # propose caps 经 can_execute 授权
    from src.agents.registry import Registries
    r = Registries()
    assert r.capabilities.can_execute("script_writer", "story.propose_change", "propose")
    assert r.capabilities.can_execute("character_designer", "character.propose_change", "propose")
    # READ_ONLY 路径的 assert_read_only_capability 仍拒 propose cap（propose 非 inspect）
    from src.agents.project_intelligence import is_read_only_capability
    assert is_read_only_capability("story.propose_change") is False
    assert is_read_only_capability("character.propose_change") is False
    assert is_read_only_capability("storyboard.propose_change") is False  # 仍是 mutation


# ============ ② legacy AI-7A 会话只读不可执行（§9）============
def test_legacy_session_readonly_not_executable(tmp_path):
    """§9：AI-7A 历史会话（无 schema_version）→ legacy_analysis_only=True，不可推进/批准。"""
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path)
    # 构造一个 AI-7A 风格的 legacy 会话 dict（无 schema_version，有 plan_draft_id），
    # 直接落进 change_protocol/sessions/ 模拟误存（或未来迁移入）。
    legacy = {"session_id": "cs_legacy", "project_name": "demo", "status": "ready",
              "request": {"original_user_request": "改头发"},
              "consultations": [], "plan_draft_id": "plan_old", "plan_draft": {},
              "created_at": 1.0, "updated_at": 1.0}
    p = tmp_path / "output" / "agents" / "change_protocol" / "sessions" / "cs_legacy.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    # 迁移识别
    assert migrations.is_legacy_analysis_session(legacy) is True
    norm = migrations.normalize_session_dict(legacy)
    assert norm["schema_version"] == 1 and norm["legacy_analysis_only"] is True

    # store 加载 → legacy_analysis_only=True
    store = ChangeProtocolStore(tmp_path, "demo")
    s = store.get_session("cs_legacy")
    assert s.legacy_analysis_only is True
    assert s.schema_version == 1

    # resume/approve 拒（§9 不可执行）。resume_session 现为 async（7B-2 起跑提案）。
    import asyncio
    import pytest
    with pytest.raises(AgentError) as ei:
        asyncio.run(cps.resume_session(rt, "demo", "cs_legacy"))
    assert ei.value.code == "CHANGE_SESSION_STALE"
    with pytest.raises(AgentError):
        cps.approve_plan(rt, "demo", "cs_legacy", "plan_old", 1)


# ============ ③ 持久化跨重启（§87）============
def test_persistence_survives_restart(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path)
    out = cps.create_session(rt, "demo", "把 Xiaoyu 头发改长并修改结局")
    sid = out["session"]["session_id"]
    sig = out["session"]["source_snapshot_signature"]
    assert out["session"]["schema_version"] == 2
    assert out["request"]["mode"] == "proposal_only"

    # 模拟重启：新 store 实例加载
    rt2 = _rt(tmp_path)
    s = cps.get_session(rt2, "demo", sid)
    assert s is not None
    assert s["session_id"] == sid
    assert s["source_snapshot_signature"] == sig
    assert s["status"] == "routing"   # 7B-1：推进机未接通，会话留 routing
    assert s["legacy_analysis_only"] is False


# ============ ④ 批准绑定 version+signature；stale→409/0 mutation（§90/§116）============
def test_approval_stale_rejected(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    out = cps.create_session(rt, "demo", "改结局")
    sid = out["session"]["session_id"]
    sig = out["session"]["source_snapshot_signature"]
    _seed_preview_ready_plan(rt, "demo", sid, sig)

    # canonical 变化（经既有合法路径）→ sig 变（sig A→B）
    proj = ProjectStore(tmp_path).load()
    proj.story_text = "a completely different story"
    ProjectStore(tmp_path).save(proj)
    mutated = (tmp_path / "output" / "project.json").read_bytes()   # 变化后的真值

    import pytest
    with pytest.raises(AgentError) as ei:
        cps.approve_plan(rt, "demo", sid, "plan_t", 1)
    assert ei.value.code == "PLAN_SOURCE_STALE"
    # stale 后 session=stale；批准路径不得在 mutated 之外进一步改动 canonical，也不建 Workspace
    s = cps.get_session(rt, "demo", sid)
    assert s["status"] == "stale"
    assert (tmp_path / "output" / "project.json").read_bytes() == mutated
    assert not (tmp_path / "workspaces").exists()


# ============ ⑤ 批准硬停止负测（§119/L）============
def test_approval_hard_stop_no_mutation(tmp_path):
    """§119/L：批准 → canonical/Workspace/Plan/Job 全不变；0 Agnes 调用；applyable 恒 False。"""
    ProjectStore(tmp_path).save(_proj())
    client = NoCallClient()
    rt = _rt(tmp_path, client=client)
    out = cps.create_session(rt, "demo", "改结局")
    sid = out["session"]["session_id"]
    sig = out["session"]["source_snapshot_signature"]
    _seed_preview_ready_plan(rt, "demo", sid, sig)

    before = (tmp_path / "output" / "project.json").read_bytes()
    res = cps.approve_plan(rt, "demo", sid, "plan_t", 1)

    assert res["approved"] is True
    assert res["status"] == "approved_for_execution"
    s = cps.get_session(rt, "demo", sid)
    assert s["status"] == "approved_for_execution"
    # approval 绑定 plan_version + signature
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    plan = store.get_plan("plan_t")
    assert plan.status == "approved_for_execution"
    assert plan.approval["plan_version"] == 1
    assert plan.approval["source_snapshot_signature"] == sig
    assert plan.approval["decision"] == "approved"
    # applyable 恒 False
    assert store.get_preview("pv_t").applyable is False
    # 0 mutation：canonical / Workspace / Production Plan / JobQueue 全未碰
    assert (tmp_path / "output" / "project.json").read_bytes() == before
    assert not (tmp_path / "workspaces").exists()
    assert not (tmp_path / "output" / "ai" / "production_plans.json").exists()
    assert not (tmp_path / "cache" / "jobs.json").exists()
    # 0 Agnes 调用
    assert client.calls == 0


# ============ ⑥ 项目隔离（§91）============
def test_project_isolation(tmp_path):
    for n in ("A", "B"):
        ProjectStore(tmp_path / n).save(_proj(n))
    rt = _rt(tmp_path, per_project=True)
    out_a = cps.create_session(rt, "A", "改 A")
    sid_a = out_a["session"]["session_id"]
    # B 解析不到 A 的 session
    assert cps.get_session(rt, "B", sid_a) is None
    import pytest
    with pytest.raises(AgentError) as ei:
        cps.approve_plan(rt, "B", sid_a, "plan_t", 1)
    assert ei.value.code == "CHANGE_SESSION_NOT_FOUND"
    # A 的 store 不含 B 的 plan/session
    store_b = ChangeProtocolStore(tmp_path / "B", "B")
    assert store_b.get_session(sid_a) is None


# ============ ⑦ 重启恢复（§87）============
def test_restart_recovery_inflight_blocked(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path)
    out = cps.create_session(rt, "demo", "改结局")
    sid = out["session"]["session_id"]
    sig = out["session"]["source_snapshot_signature"]

    # routing 稳态 → recover 不动
    n = migrations.recover_change_protocol(tmp_path, "demo")
    assert n == 0
    assert cps.get_session(rt, "demo", sid)["status"] == "routing"

    # 模拟推进中断：手动置 consulting（in-flight），加一个 running proposal
    store = ChangeProtocolStore(tmp_path, "demo")
    s = store.get_session(sid)
    s.status = "consulting"
    s.module_proposal_ids = ["prop_x"]
    store.save_session(s)
    from src.agents.change_protocol.models import ModuleProposal
    store.save_proposal(ModuleProposal(
        proposal_id="prop_x", change_session_id=sid, module="script",
        owner_agent_id="script_writer", round=1, status="running",
        source_snapshot_signature=sig, created_at=1.0, updated_at=1.0))

    n2 = migrations.recover_change_protocol(tmp_path, "demo")
    assert n2 == 1
    s2 = store.get_session(sid)
    assert s2.status == "blocked"
    assert store.get_proposal("prop_x").status == "failed"   # stuck-running → failed，不重跑 LLM


# ============ ⑧ 批准幂等（§88）============
def test_approval_idempotent(tmp_path):
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path, client=NoCallClient())
    out = cps.create_session(rt, "demo", "改结局")
    sid = out["session"]["session_id"]
    sig = out["session"]["source_snapshot_signature"]
    _seed_preview_ready_plan(rt, "demo", sid, sig)

    r1 = cps.approve_plan(rt, "demo", sid, "plan_t", 1)
    assert r1["approved"] is True and r1["idempotent"] is False
    # 双击 / 重发 → 幂等，不二次批准
    r2 = cps.approve_plan(rt, "demo", sid, "plan_t", 1)
    assert r2["approved"] is True and r2["idempotent"] is True
    # 旧版本号 → 版本变化
    import pytest
    with pytest.raises(AgentError) as ei:
        cps.approve_plan(rt, "demo", sid, "plan_t", 2)
    assert ei.value.code == "PLAN_VERSION_CHANGED"


# ============ ⑨ 计划版本链 + 决策（§78/§93）骨架 ============
def test_request_changes_version_chain(tmp_path):
    """§78：反馈 → plan v2，旧 plan=superseded（保留历史）。"""
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path)
    out = cps.create_session(rt, "demo", "改结局")
    sid = out["session"]["session_id"]
    sig = out["session"]["source_snapshot_signature"]
    _seed_preview_ready_plan(rt, "demo", sid, sig)

    res = cps.request_changes(rt, "demo", sid, "不要改 Scene 55", "plan_t")
    new_plan = res["plan"]
    assert new_plan["version"] == 2
    assert new_plan["parent_plan_id"] == "plan_t"
    assert new_plan["status"] == "draft"
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    assert store.get_plan("plan_t").status == "superseded"   # 旧版保留为历史
    s = store.get_session(sid)
    assert s.active_plan_id == new_plan["plan_id"]
    assert s.plan_version == 2


def test_submit_decision_persists(tmp_path):
    """§93：决策验证属该 session + pending + option 合法 → answered。"""
    ProjectStore(tmp_path).save(_proj())
    rt = _rt(tmp_path)
    out = cps.create_session(rt, "demo", "更悲壮的结局")
    sid = out["session"]["session_id"]
    store = ChangeProtocolStore(rt._resolve_project_dir("demo"), "demo")
    dec = ChangeDecisionRequest(
        decision_id="dec_t", change_session_id=sid,
        question="结局方向？",
        options=[ChangeDecisionOption(option_id="a", label="存活").to_dict(),
                 ChangeDecisionOption(option_id="b", label="牺牲").to_dict()],
        blocking=True, status="pending", created_at=1.0, updated_at=1.0)
    store.save_decision(dec)
    s = store.get_session(sid)
    s.pending_decision_ids = ["dec_t"]
    store.save_session(s)

    res = cps.submit_decision(rt, "demo", sid, "dec_t", "a")
    assert res["status"] == "answered"
    assert res["selected_option_id"] == "a"
    d = store.get_decision("dec_t")
    assert d.status == "answered" and d.selected_option_id == "a"
    assert "dec_t" not in store.get_session(sid).pending_decision_ids
    # 非法 option / 跨 session / 已回答 → 拒
    import pytest
    with pytest.raises(AgentError):
        cps.submit_decision(rt, "demo", sid, "dec_t", "zzz")
    with pytest.raises(AgentError):
        cps.submit_decision(rt, "demo", sid, "dec_t", "b")   # 已 answered


# ============ ⑩ router 接线 + 失败码 HTTP 映射（§92/§101；无副作用）============
def test_router_routes_wired_and_error_mapping(client):
    """证明 change-session 路由已注册 + 失败码→HTTP 映射生效，且不向磁盘写入。
    全 happy-path HTTP 回归留到 7B-5/7B-6（含手动浏览器验收）。"""
    # 未知项目 → 404（unknown_project 映射）；不写盘
    r = client.get("/api/project/__no_such_project_7b1__/change-sessions")
    assert r.status_code == 404
    r2 = client.post("/api/project/__no_such_project_7b1__/change-sessions",
                     json={"request": "改结局"})
    assert r2.status_code == 404
    # approve 未知 session（经 unknown_project 短路 404，不触达 store）
    r3 = client.post("/api/project/__no_such_project_7b1__/change-sessions/sid/approve",
                     json={"plan_id": "p", "plan_version": 1})
    assert r3.status_code == 404
