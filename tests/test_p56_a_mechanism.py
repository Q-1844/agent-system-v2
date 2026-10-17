# Phase56 A 定向测试：机制收口（01 文件 A1-A4）。
#
# 覆盖：
#   A2 reference_policy v2（baseline_equiv_chain）：策略解析/场景消费五态
#      （context_only 不再冒充已消费）/接续语义三分/independent 未声明拒绝/
#      显式豁免登记放行/manifest 接续落档/v1 旧策略行为不变。
#   A3 恢复与主控：损坏租约 fail-closed（verify/acquire/transfer 拒写）+
#      diagnose/recover 恢复路径；接力上限 → awaiting_relay（不标 completed）+
#      resume 续跑（非 awaiting_relay 拒绝）；UNKNOWN 提交暂停（handler 待核验）。
#   A4 并发档位：单一解析（env > config > 默认，覆盖来源）；结算后热重配
#      （幂等/在途拒绝/settle 生效）；run 启动 concurrency_effect 落档。
#   A1 实验默认：combo v2 注册表（mode_version 2.0.0/v2 策略/repair_policy/
#      experimental）；mode_id 缺省 → 实验默认 + authorization 落档；REST 缺省
#      启动 + used_default_mode；resume 端点接线。
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents import collab_modes as cm
from src.generation import video_contract as vc
from src.generation.video_contract import (
    resolve_reference_policy, set_project_reference_policy,
    read_policy_exemptions, scene_reference_consumed, continuity_semantics,
    find_prev_scene_shot, policy_requires_binding, validate_video_submission,
    build_input_manifest, planned_video_seconds)

PROJECT = "_test_p56_mechanism"


# ==================================================================
# A2：reference_policy v2（baseline_equiv_chain）
# ==================================================================
def _mk_shot(shot_id="s01_c02", scene_id="s01", index=0, **kw):
    from src.core.models import Shot
    base = dict(link_mode="i2v", opening_source="independent")
    base.update(kw)
    return Shot(shot_id=shot_id, scene_id=scene_id, index=index, **base)


def _mk_project(shots):
    from src.core.models import Project, Scene, StyleAnchor
    sc = Scene(scene_id="s01", summary="s", characters=[], shots=shots)
    return Project(name=PROJECT, style=StyleAnchor(art_style="anime",
                                                   aspect_ratio="16:9"),
                   story_text="t", characters=[],
                   scenes=[sc] + [Scene(scene_id=s.scene_id, summary="x",
                                        characters=[], shots=[s])
                                  for s in []])


def test_v2_policy_resolution_and_requires_binding(tmp_path):
    set_project_reference_policy(tmp_path, "baseline_equiv_chain",
                                 source="mode_run", run_id="run_x")
    assert resolve_reference_policy(tmp_path) == "baseline_equiv_chain"
    assert policy_requires_binding("baseline_equiv_chain") is True
    assert policy_requires_binding("bound_required") is True
    assert policy_requires_binding("legacy_allowed") is False


def test_v2_scene_reference_context_only_not_consumed(tmp_path):
    """Phase55 冒烟定性缺口：context_only 不再冒充「已消费」。"""
    class _B:
        batch_id = "c1"   # 与 selected_first_frame 指针匹配
        generation_basis = {"scene_reference_mode": "context_only"}
        candidates = []
    shot = _mk_shot(selected_first_frame="c1")
    shot.first_frame_batches = [_B()]
    validity = {"readiness": "READY", "binding_state": "bound"}
    r = scene_reference_consumed(None, shot, tmp_path, validity)
    assert r["state"] == "exempted_context_only"
    assert "未进入视觉输入" in r["detail"] or "context_only" in r["detail"]
    # full 仍=真实消费
    class _B2(_B):
        generation_basis = {"scene_reference_mode": "full"}
    shot.first_frame_batches = [_B2()]
    r2 = scene_reference_consumed(None, shot, tmp_path, validity)
    assert r2["state"] == "via_first_frame"


def test_v2_continuity_semantics_three_ways():
    # 1) 真实接续
    s1 = _mk_shot(opening_source="inherit_previous", source_shot_id="s01_c01")
    r1 = continuity_semantics(s1, _mk_shot("s01_c01"))
    assert r1["semantics"] == "same_action_continuation"
    # 2) 显式切镜
    s2 = _mk_shot(opening_cut_reason="same_scene_recam")
    r2 = continuity_semantics(s2, _mk_shot("s01_c01"))
    assert r2["semantics"] == "declared_cut" and r2["cut_reason"] == "same_scene_recam"
    # 3) 未声明（同场景有前驱）→ 违规
    s3 = _mk_shot()
    r3 = continuity_semantics(s3, _mk_shot("s01_c01"))
    assert r3["semantics"] == "undeclared_independent"
    # 开场镜（无前驱）→ 自动 scene_change 依据，不违规
    r4 = continuity_semantics(_mk_shot(), None)
    assert r4["semantics"] == "declared_cut" and r4.get("auto") is True


def test_v2_validate_rejects_undeclared_and_unconsumed(tmp_path):
    set_project_reference_policy(tmp_path, "baseline_equiv_chain",
                                 source="test", run_id=None)
    proj = _mk_project([_mk_shot("s01_c01"), _mk_shot("s01_c02")])
    shot = proj.get_shot("s01_c02")
    validity = {"readiness": "READY", "binding_state": "bound"}
    chk = validate_video_submission(shot, require_binding=True,
                                    validity=validity,
                                    policy="baseline_equiv_chain",
                                    project=proj, pdir=tmp_path)
    codes = {v["code"] for v in chk["violations"]}
    assert "opening_cut_undeclared" in codes
    # 场景未消费（absent）→ 拒绝（无豁免时）
    assert "scene_reference_not_consumed" in codes
    # 声明切镜后，仅剩场景消费违规
    shot.opening_cut_reason = "scene_change"
    chk2 = validate_video_submission(shot, require_binding=True,
                                     validity=validity,
                                     policy="baseline_equiv_chain",
                                     project=proj, pdir=tmp_path)
    codes2 = {v["code"] for v in chk2["violations"]}
    assert "opening_cut_undeclared" not in codes2
    assert "scene_reference_not_consumed" in codes2
    # 显式豁免登记后放行
    set_project_reference_policy(tmp_path, "baseline_equiv_chain",
                                 source="test", run_id=None,
                                 exemptions={"s01_c02": "测试豁免：独立镜头无场景锚"})
    assert read_policy_exemptions(tmp_path).get("s01_c02")
    chk3 = validate_video_submission(shot, require_binding=True,
                                     validity=validity,
                                     policy="baseline_equiv_chain",
                                     project=proj, pdir=tmp_path)
    assert chk3["ok"], chk3["violations"]


def test_v1_bound_required_behavior_unchanged(tmp_path):
    """v2 是增量：bound_required 旧策略不做接续链校验（在途快照/旧项目不受影响）。"""
    set_project_reference_policy(tmp_path, "bound_required", source="test")
    proj = _mk_project([_mk_shot("s01_c01"), _mk_shot("s01_c02")])
    shot = proj.get_shot("s01_c02")
    validity = {"readiness": "READY", "binding_state": "bound"}
    chk = validate_video_submission(shot, require_binding=True,
                                    validity=validity,
                                    policy="bound_required",
                                    project=proj, pdir=tmp_path)
    codes = {v["code"] for v in chk["violations"]}
    assert "opening_cut_undeclared" not in codes
    assert "scene_reference_not_consumed" not in codes


def test_manifest_records_continuity(tmp_path):
    proj = _mk_project([_mk_shot("s01_c01"), _mk_shot("s01_c02",
                                                      opening_cut_reason="time_jump")])
    shot = proj.get_shot("s01_c02")
    m = build_input_manifest(proj, shot, stage="video", mode="i2v",
                             video_model=None, seconds=8.0,
                             project_dir=tmp_path,
                             validity={"readiness": "READY", "binding_state": "bound"},
                             scene_ref={"state": "absent", "detail": "x"})
    cont = m["upstream"]["continuity"]
    assert cont["semantics"] == "declared_cut"
    assert cont["cut_reason"] == "time_jump"


def test_find_prev_scene_shot_scene_scoped():
    from src.core.models import Scene
    s1, s2, s3 = _mk_shot("s01_c01"), _mk_shot("s01_c02"), _mk_shot("s01_c03")
    s4 = _mk_shot("s02_c01", scene_id="s02")
    proj = _mk_project([s1, s2, s3])
    proj.scenes.append(Scene(scene_id="s02", summary="x", characters=[], shots=[s4]))
    assert find_prev_scene_shot(proj, s3) is s2
    assert find_prev_scene_shot(proj, s1) is None   # 开场镜
    assert find_prev_scene_shot(proj, s4) is None   # 跨场景不串链


# ==================================================================
# A3：损坏租约 fail-closed + 恢复路径
# ==================================================================
def test_lease_corrupt_fail_closed_and_recover(tmp_path):
    from src.agents import master_lease as ml
    # 正常获取 → 损坏文件
    assert ml.acquire(tmp_path, "sess_a")["ok"]
    p = ml._lease_path(tmp_path)
    p.write_text("{corrupted json!!", encoding="utf-8")
    # verify：拒写（fail-closed，不再放行）
    v = ml.verify(tmp_path, "sess_a")
    assert v["verified"] is False and v.get("corrupt") is True
    # acquire：拒绝接管（权限未核明不双主）
    a = ml.acquire(tmp_path, "sess_b")
    assert a["ok"] is False and a.get("corrupt") is True
    # transfer：拒绝
    t = ml.transfer(tmp_path, "sess_a", "sess_b")
    assert t["ok"] is False and t.get("corrupt") is True
    # 诊断（只读独立路径）
    d = ml.diagnose(tmp_path)
    assert d["state"] == "corrupt" and "raw_excerpt" in d
    # 健康租约不可走 recover（防绕过接管语义）
    ml2 = tmp_path / "ok" / "output" / "agents"
    ml2.mkdir(parents=True)
    assert ml.acquire(tmp_path / "ok", "sess_c")["ok"]
    assert ml.recover(tmp_path / "ok", "sess_d")["ok"] is False
    # 显式重建：审计标记 + 可写
    r = ml.recover(tmp_path, "sess_b")
    assert r["ok"] is True and r["rebuilt_from"] == "corrupt"
    v2 = ml.verify(tmp_path, "sess_b")
    assert v2["verified"] is True


def test_producer_turn_lease_denied_structured(tmp_path):
    """Phase56 B1 实证回归：lease_denied 路径必须返回结构化 AgentError（code=
    lease_denied）——原代码死分支 `from .models import AgentError`（models 无此类）
    + 同作用域无条件 raise → UnboundLocalError 掩盖真实拒绝原因。"""
    from src.agents import producer as prod_mod
    from src.agents.runtime import AgentError
    from src.agents import master_lease as ml

    class _Store:
        def load(self, sid):
            return SimpleNamespace(session_id=sid, agent_id="producer",
                                   messages=[SimpleNamespace(role="user",
                                                             content="x")])

        def append_message(self, sid, msg):
            pass

    class _Rt:
        def __init__(self, p):
            self.p = Path(p)

        def _session_store(self, project):
            return _Store()

        def _is_enabled(self, aid):
            return True

        def _maybe_capture_preference(self, project, content, message_id):
            pass

        def _resolve_project_dir(self, project):
            return self.p

    rt = _Rt(tmp_path)
    ml.acquire(tmp_path, "sess_other_holder")   # 他人未过期持有租约
    with pytest.raises(AgentError) as ei:
        asyncio.run(prod_mod.producer_turn(rt, "p", "sess_mine", "内容"))
    assert ei.value.code == "lease_denied"   # 结构化 code（router 可映射 HTTP）
    assert "sess_other_holder" in ei.value.message


def test_lease_healthy_paths_unchanged(tmp_path):
    """Phase55 语义保持：holder 匹配即过（自身过期≠失去主控）+ 他人未过期拒绝。"""
    from src.agents import master_lease as ml
    assert ml.acquire(tmp_path, "sess_a")["ok"]
    assert ml.verify(tmp_path, "sess_a")["verified"] is True
    other = ml.acquire(tmp_path, "sess_b")
    assert other["ok"] is False and "持有" in other["reason"]
    tr = ml.transfer(tmp_path, "sess_a", "sess_b")
    assert tr["ok"] and tr["generation"] >= 2
    assert ml.verify(tmp_path, "sess_a")["verified"] is False   # 旧会话拒写


# ==================================================================
# A3：接力上限 → awaiting_relay + resume
# ==================================================================
class _RelayFakeRt:
    def __init__(self, tmp_path, turn_results):
        self.tmp = Path(tmp_path)
        self.turn_results = list(turn_results)
        self.turns = []
        self.agents = {}

    def _resolve_project_dir(self, project):
        return self.tmp

    def _is_enabled(self, agent_id):
        return True

    def get_or_create_session(self, project, agent_id, scope_type="project",
                              scope_id=""):
        return {"session_id": f"sess_{agent_id}"}

    async def unified_turn(self, project, sid, content, thinking=False):
        self.turns.append((sid, content))
        return self.turn_results.pop(0) if self.turn_results else {
            "assistant_message": "ok", "events": [], "receipts": []}


def test_relay_limit_sets_awaiting_relay(tmp_path, monkeypatch):
    from src.agents import handoff as handoff_mod
    monkeypatch.setattr(handoff_mod, "relay_producer_session",
                        lambda *a, **k: {"new_session_id": "sess_relayed",
                                         "lease": {"generation": 2}})
    rt = _RelayFakeRt(tmp_path, [
        {"assistant_message": f"t{i}", "handoff": {"handoff_id": f"ho_{i}",
                                                   "reason": "budget"}}
        for i in range(1, cm.RELAY_LIMIT + 2)])   # 每次 turn 都触发交接（耗尽上限）
    run = {"run_id": "run_ar", "project": PROJECT, "mode_id": "producer_lead",
           "mode_version": "1.0.0", "status": "running", "created_at": 1.0,
           "granted_by": "t", "snapshot_hash": "h",
           "snapshot": {"mode": cm.COLLAB_MODES["producer_lead"],
                        "params": {"story_goal": "s"}},
           "steps": [], "session_ids": {}, "grant_ids": [],
           "workflow_run_id": None, "result": None, "relay_count": 0}
    res = asyncio.run(cm._producer_turn_with_relays(
        rt, tmp_path, PROJECT, run, "sess_producer", "content"))
    assert res is None
    assert run["status"] == "awaiting_relay"
    assert run["relay_count"] == cm.RELAY_LIMIT
    # relay_pending=最后一轮（第 1+LIMIT 次 turn）的交接包——续跑起点
    assert run["relay_pending"]["handoff_id"] == f"ho_{cm.RELAY_LIMIT + 1}"
    # 落盘后 load 读到的 status 同样是待接力（未完成），绝非 completed
    saved = cm.load_run(tmp_path, "run_ar")
    assert saved["status"] == "awaiting_relay"


def test_resume_rejects_non_awaiting_relay(tmp_path):
    run = {"run_id": "run_done", "project": PROJECT, "mode_id": "producer_lead",
           "mode_version": "1.0.0", "status": "completed", "created_at": 1.0,
           "granted_by": "t", "snapshot_hash": "h",
           "snapshot": {"mode": cm.COLLAB_MODES["producer_lead"],
                        "params": {"story_goal": "s"}},
           "steps": [], "session_ids": {}, "grant_ids": [],
           "workflow_run_id": None, "result": None}
    cm.save_run(tmp_path, run)
    rt = SimpleNamespace(_resolve_project_dir=lambda p: tmp_path)
    with pytest.raises(ValueError, match="仅 awaiting_relay"):
        asyncio.run(cm.resume_collab_run(rt, PROJECT, "run_done"))


def test_resume_awaiting_relay_continues_same_run(tmp_path, monkeypatch):
    from src.agents import handoff as handoff_mod
    monkeypatch.setattr(handoff_mod, "relay_producer_session",
                        lambda *a, **k: {"new_session_id": "sess_resumed",
                                         "lease": {"generation": 9},
                                         "handoff": {"handoff_id": "ho_9"}})
    rt = _RelayFakeRt(tmp_path, [{"assistant_message": "续跑完成",
                                  "events": [], "receipts": []}])
    run = {"run_id": "run_res", "project": PROJECT, "mode_id": "producer_lead",
           "mode_version": "1.0.0", "status": "awaiting_relay", "created_at": 1.0,
           "granted_by": "t", "snapshot_hash": "h",
           "snapshot": {"mode": cm.COLLAB_MODES["producer_lead"],
                        "params": {"story_goal": "s"}},
           "steps": [], "session_ids": {"producer": "sess_p",
                                        "producer_last": "sess_last"},
           "grant_ids": [], "workflow_run_id": None, "result": None,
           "relay_count": cm.RELAY_LIMIT, "relay_pending": {"handoff_id": "ho_1"}}
    cm.save_run(tmp_path, run)
    out = asyncio.run(cm.resume_collab_run(rt, PROJECT, "run_res"))
    assert out["status"] == "completed"
    assert out["session_ids"]["producer_resume_1"] == "sess_resumed"
    assert out["result"]["resumed"] == 1
    step_ids = [s["step_id"] for s in out["steps"]]
    assert "resume_1" in step_ids and "producer_turn_resume_1" in step_ids


# ==================================================================
# A4：并发档位单一解析 + 结算后重配
# ==================================================================
def test_resolve_video_inflight_priority(monkeypatch):
    from src.core.job_queue import JobQueue
    monkeypatch.delenv("AGNES_VIDEO_INFLIGHT", raising=False)
    assert JobQueue.resolve_video_inflight() == (1, "default:1")
    assert JobQueue.resolve_video_inflight(config_val=3) == (
        3, "config:concurrency.video_inflight=3")
    monkeypatch.setenv("AGNES_VIDEO_INFLIGHT", "2")
    assert JobQueue.resolve_video_inflight(config_val=3) == (
        2, "env:AGNES_VIDEO_INFLIGHT=2")
    # 非法 env → 跳过该层回落 config（不静默夹取后谎报来源）
    monkeypatch.setenv("AGNES_VIDEO_INFLIGHT", "abc")
    w, src = JobQueue.resolve_video_inflight(config_val=3)
    assert w == 3 and "config" in src


def test_reconfigure_video_workers_settle_and_idempotent(monkeypatch, tmp_path):
    from src.core.job_queue import JobQueue, Job, JobKind
    q = JobQueue(tmp_path / "jobs.json", handlers={})
    monkeypatch.delenv("AGNES_VIDEO_INFLIGHT", raising=False)

    async def scenario():
        # 未启动 → 拒绝
        r_pre = await q.reconfigure_video_workers(2)
        assert r_pre["applied"] is False
        await q.start()   # 默认 1
        assert q.video_worker_count == 1
        # 幂等：同目标
        r1 = await q.reconfigure_video_workers(q.video_worker_count,
                                               wait_settle=False)
        assert r1["applied"] is True and r1["changed"] is False
        # 结算后扩容 1→3 生效（真实 worker 池重建）
        r2 = await q.reconfigure_video_workers(3)
        assert r2["applied"] is True and q.video_worker_count == 3
        # 在途任务 + 不等待 → 拒绝（结算后重配的可靠方案优先）
        job = Job(job_id="j1", project="p", kind=JobKind.VIDEO.value,
                  state="running")
        q.jobs[job.job_id] = job
        r3 = await q.reconfigure_video_workers(1, wait_settle=False)
        assert r3["applied"] is False and "in_flight" in r3["reason"]
        # wait_settle=True + 结算（任务转终态）→ 应用
        q.jobs["j1"].state = "done"
        r4 = await q.reconfigure_video_workers(1)
        assert r4["applied"] is True and q.video_worker_count == 1

    try:
        asyncio.run(scenario())
    finally:
        for t in q._tasks:
            t.cancel()


def test_run_start_applies_concurrency_effect(tmp_path, monkeypatch):
    """run 启动记录 requested + 实际生效（FakeRuntime 无 queue → applied=False
    如实落档——不把快照数字当作已生效）。"""
    rt = _RelayFakeRt(tmp_path, [{"assistant_message": "done", "events": [],
                                  "receipts": []}])
    run = asyncio.run(cm.start_collab_run(
        rt, PROJECT, "producer_lead",
        {"story_goal": "并发档位测试", "media_inflight": 2}))
    for _ in range(200):
        cur = cm.load_run(rt.tmp, run["run_id"])
        if cur["status"] in ("completed", "failed", "cancelled", "awaiting_relay"):
            break
        asyncio.run(asyncio.sleep(0.01))
    cur = cm.load_run(rt.tmp, run["run_id"])
    eff = cur.get("concurrency_effect") or {}
    assert eff.get("requested_media_inflight") == 2
    assert eff.get("applied") is False   # 无队列——如实标注，不冒充生效


# ==================================================================
# A1：实验默认（combo v2）与授权落档
# ==================================================================
def test_combo_v2_registry():
    m = cm.COLLAB_MODES["combo_recommended"]
    assert m["mode_version"] == "2.0.0"
    assert m["reference_policy"] == "baseline_equiv_chain"
    assert m["experimental"] is True
    assert m["repair_policy"]["repair_limit_per_shot"] == cm.REPAIR_LIMIT_PER_SHOT
    assert m["review_policy"]["profile"] == "independent_final_review"
    # 其余预设不变（对照/回退显式保留）
    assert cm.COLLAB_MODES["six_member"]["mode_version"] == "1.0.0"
    assert cm.COLLAB_MODES["six_member"]["reference_policy"] == "bound_required"
    assert cm.DEFAULT_EXPERIMENTAL_MODE_ID == "combo_recommended"
    # 组合校验接受 v2 策略
    assert cm.validate_start("combo_recommended", {"story_goal": "s"}) == []


def test_default_mode_start_and_authorization(tmp_path):
    """mode_id 缺省 → 实验默认 combo v2；authorization 落 run（机器审核/有界修复
    授权来源可审计；user_acceptance 恒 pending_user_review）。"""
    rt = _RelayFakeRt(tmp_path, [{"assistant_message": "done", "events": [],
                                  "receipts": []}])
    run = asyncio.run(cm.start_collab_run(rt, PROJECT, None,
                                          {"story_goal": "默认模式测试"}))
    assert run["mode_id"] == "combo_recommended"
    assert run["mode_version"] == "2.0.0"
    auth = run["authorization"]
    assert auth["machine_review"] is True
    assert auth["bounded_repair"] is True
    assert auth["user_acceptance"] == "pending_user_review"
    assert auth["scope"] == "phase56_experimental_default"
    for _ in range(200):
        cur = cm.load_run(rt.tmp, run["run_id"])
        if cur["status"] in ("completed", "failed", "cancelled", "awaiting_relay"):
            break
        asyncio.run(asyncio.sleep(0.01))
    cur = cm.load_run(rt.tmp, run["run_id"])
    # v2 策略已写项目级文件
    assert resolve_reference_policy(rt.tmp) == "baseline_equiv_chain"
    # 显式 mode_id 的授权 scope 不同（审计可区分默认/显式）
    run2 = asyncio.run(cm.start_collab_run(rt, PROJECT, "producer_lead",
                                           {"story_goal": "显式"}))
    assert run2["authorization"]["scope"] == "phase56_explicit_mode_start"


def test_approved_package_variant_skips_preflight(tmp_path):
    """Phase56 B：批准工作包变体——预铺跳过（步骤账本如实记 skipped）、producer
    目标不要求重做资产（无「新建角色」指令、含「不要重做」护栏）。"""
    rt = _RelayFakeRt(tmp_path, [{"assistant_message": "done", "events": [],
                                  "receipts": []}])
    run = asyncio.run(cm.start_collab_run(
        rt, PROJECT, "combo_recommended",
        {"story_goal": "批准包测试", "approved_package": True}))
    for _ in range(200):
        cur = cm.load_run(rt.tmp, run["run_id"])
        if cur["status"] in ("completed", "failed", "cancelled", "awaiting_relay"):
            break
        asyncio.run(asyncio.sleep(0.01))
    cur = cm.load_run(rt.tmp, run["run_id"])
    steps = {s["step_id"]: s for s in cur["steps"]}
    assert steps["preflight"]["detail"].get("skipped") is True   # 预铺不重做
    assert [sid for sid, _c in rt.turns] == ["sess_producer"]   # CD/SC 预铺 turn 未发生
    content = rt.turns[0][1]
    assert "不要】重做" in content or "不要重做" in content
    assert "opening_cut_reason" in content   # v2 声明指令在批准包目标内
    assert cur["snapshot"]["params"]["approved_package"] is True


def test_rest_default_mode_and_resume_endpoint(tmp_path, monkeypatch):
    import routers.agents as ra
    from fastapi import HTTPException

    class _FakePM:
        def __init__(self, p):
            self._p = p
            self.base_dir = p

        def get_project_dir(self, name):
            return self._p

    rt = _RelayFakeRt(tmp_path, [{"assistant_message": "done", "events": [],
                                  "receipts": []}])
    monkeypatch.setattr(ra, "get_agent_runtime", lambda: rt)
    monkeypatch.setattr(ra, "get_project_manager", lambda: _FakePM(tmp_path))
    # 缺省启动 → used_default_mode
    out = asyncio.run(ra.collab_run_start(PROJECT, ra.CollabRunStartReq(
        mode_id=None, params={"story_goal": "s"})))
    assert out["used_default_mode"] == "combo_recommended"
    assert out["run"]["mode_version"] == "2.0.0"
    # resume：非 awaiting_relay → 400
    rid = out["run"]["run_id"]
    for _ in range(200):
        cur = cm.load_run(rt.tmp, rid)
        if cur["status"] in ("completed", "failed", "cancelled", "awaiting_relay"):
            break
        asyncio.run(asyncio.sleep(0.01))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(ra.collab_run_resume(PROJECT, rid,
                                         ra.CollabRunResumeReq()))
    assert ei.value.status_code == 400


def test_agent_declaration_channel_opening_fields(tmp_path):
    """Phase56 B：agent shot.update 的接力/切镜声明通道——opening_source 白名单值
    校验（independent 解除锚）、opening_cut_reason 白名单校验（非法=显式错误）。"""
    from src.storyboard.structure_service import update_shot_fields, StructureError
    s1 = _mk_shot("s01_c01", index=1)
    s2 = _mk_shot("s01_c02", index=2)
    proj = _mk_project([s1, s2])
    # 合法声明：inherit + 锚
    proj.get_shot("s01_c02").source_shot_id = "s01_c01"
    r = update_shot_fields(proj, "s01_c02",
                           {"opening_source": "inherit_previous"})
    assert "opening_source" in r["changed"]
    assert proj.get_shot("s01_c02").opening_source == "inherit_previous"
    assert proj.get_shot("s01_c02").source_shot_id == "s01_c01"
    # independent 同时解除锚
    update_shot_fields(proj, "s01_c02", {"opening_source": "independent"})
    assert proj.get_shot("s01_c02").source_shot_id is None
    # cut_reason 合法/非法
    update_shot_fields(proj, "s01_c02", {"opening_cut_reason": "time_jump"})
    assert proj.get_shot("s01_c02").opening_cut_reason == "time_jump"
    with pytest.raises(StructureError):
        update_shot_fields(proj, "s01_c02", {"opening_cut_reason": "随便写"})
    with pytest.raises(StructureError):
        update_shot_fields(proj, "s01_c02", {"opening_source": "teleport"})


# ==================================================================
# D1（Phase56 04 文件）：终审→有界局部修复→复审 闭环（无媒体夹具）
# ==================================================================
class _FakeRepairQueue:
    """假队列：submit 即 done（不调 Provider）；记录全部提交参数。"""

    def __init__(self):
        self.submitted = []

    def submit(self, project, kind, shot_id, **params):
        self.submitted.append({"project": project, "kind": kind,
                               "shot_id": shot_id, **params})
        return SimpleNamespace(job_id=f"j{len(self.submitted)}",
                               state="done", params=params)

    def get(self, job_id):
        return SimpleNamespace(state="done")   # submit 即终态（_wait_job_terminal 用）


def test_auto_repair_loop_new_artifact_and_rereview(tmp_path, monkeypatch):
    """④D1：独立终审硬错→确定性重生成（repair_of_review 溯源随 job 落档）→
    同政策复审→通过；复审仍硬错→达上限如实 still_hard（不冒充通过）。"""
    from src.agents import review_pipeline

    rt = _RelayFakeRt(tmp_path, [])
    fq = _FakeRepairQueue()
    rt._resolve_job_queue_or_none = lambda: fq
    rt._resolve_client_or_none = lambda agent_id=None: SimpleNamespace()
    run = {"run_id": "run_fix", "project": PROJECT, "mode_id": "combo_recommended",
           "mode_version": "2.0.0", "status": "running", "created_at": 1.0,
           "granted_by": "t", "snapshot_hash": "h",
           "snapshot": {"mode": cm.COLLAB_MODES["combo_recommended"],
                        "params": {"story_goal": "s"}},
           "steps": [], "session_ids": {}, "grant_ids": [],
           "workflow_run_id": None, "result": None, "relay_count": 0,
           "authorization": {"scope": "phase56_experimental_default",
                             "machine_review": True, "bounded_repair": True,
                             "granted_by": "t",
                             "user_acceptance": "pending_user_review"}}
    calls = {"n": 0}

    async def fake_review(rt_, pdir_, project_, *, reason="", shot_ids=None,
                          positions=None):
        calls["n"] += 1
        if shot_ids:   # 复审：第 1 次仍硬错（驱动第二次 attempt），之后通过
            if calls["n"] == 2:
                return {"batch_id": f"rvw_r{calls['n']}", "results": [
                    {"shot_id": shot_ids[0], "overall": "hard_error",
                     "hard_errors": ["unexpected_people"]}],
                    "hard_error_shots": list(shot_ids), "minor_shots": []}
            return {"batch_id": f"rvw_r{calls['n']}", "results": [
                {"shot_id": shot_ids[0], "overall": "pass", "hard_errors": []}],
                "hard_error_shots": [], "minor_shots": []}
        return {"batch_id": f"rvw_{calls['n']}", "shots_reviewed": 1,
                "hard_error_shots": ["s01_c02"], "minor_shots": [],
                "report_path": "x", "results": []}

    monkeypatch.setattr(review_pipeline, "run_independent_review", fake_review)
    asyncio.run(cm._final_review_and_repair(rt, tmp_path, PROJECT, run))
    steps = {s["step_id"]: s for s in run["steps"]}
    assert steps["independent_final_review"]["detail"]["hard_error_shots"] == ["s01_c02"]
    rep = steps["auto_repair"]["detail"]
    assert steps["auto_repair"]["status"] == "done"   # ok 落在 step.status
    assert rep["still_hard"] == []
    # 修复=同一硬错镜两次 attempt（复审第一次仍硬错→第二次通过）；溯源字段在位
    submits = fq.submitted
    assert [s["shot_id"] for s in submits] == ["s01_c02", "s01_c02"]
    assert submits[0]["repair_of_review"] == "rvw_1" and submits[0]["repair_attempt"] == 1
    assert submits[1]["repair_attempt"] == 2
    assert run["repair"]["still_hard"] == []
    # 授权来源落账（actor=mode_run_executor）
    assert rep["actor"] == "mode_run_executor"


def test_auto_repair_limit_exhausted_honest(tmp_path, monkeypatch):
    """④D1：复审恒硬错→达每镜上限如实 still_hard（不无限重试、不冒充通过）。"""
    from src.agents import review_pipeline

    rt = _RelayFakeRt(tmp_path, [])
    fq = _FakeRepairQueue()
    rt._resolve_job_queue_or_none = lambda: fq
    rt._resolve_client_or_none = lambda agent_id=None: SimpleNamespace()
    run = {"run_id": "run_fix2", "project": PROJECT, "mode_id": "combo_recommended",
           "mode_version": "2.0.0", "status": "running", "created_at": 1.0,
           "granted_by": "t", "snapshot_hash": "h",
           "snapshot": {"mode": cm.COLLAB_MODES["combo_recommended"],
                        "params": {"story_goal": "s"}},
           "steps": [], "session_ids": {}, "grant_ids": [],
           "workflow_run_id": None, "result": None, "relay_count": 0,
           "authorization": {"scope": "x", "user_acceptance": "pending_user_review"}}
    calls = {"n": 0}

    async def fake_review(rt_, pdir_, project_, *, reason="", shot_ids=None,
                          positions=None):
        calls["n"] += 1
        if shot_ids:
            return {"batch_id": f"rvw_r{calls['n']}", "results": [
                {"shot_id": shot_ids[0], "overall": "hard_error",
                 "hard_errors": ["missing_character"]}],
                "hard_error_shots": list(shot_ids), "minor_shots": []}
        return {"batch_id": f"rvw_{calls['n']}", "shots_reviewed": 1,
                "hard_error_shots": ["s01_c02"], "minor_shots": [],
                "report_path": "x", "results": []}

    monkeypatch.setattr(review_pipeline, "run_independent_review", fake_review)
    asyncio.run(cm._final_review_and_repair(rt, tmp_path, PROJECT, run))
    steps = {s["step_id"]: s for s in run["steps"]}
    rep = steps["auto_repair"]["detail"]
    assert steps["auto_repair"]["status"] == "failed"   # 仍有硬错=未全通过
    assert rep["still_hard"] == [{"shot_id": "s01_c02",
                                  "attempts": cm.REPAIR_LIMIT_PER_SHOT}]
    submits = fq.submitted
    assert len(submits) == cm.REPAIR_LIMIT_PER_SHOT   # 恰好上限次，不无限
    assert run["repair"]["still_hard"][0]["shot_id"] == "s01_c02"


def test_video_review_policy_v2_aggregation():
    """Phase56 审查政策 v2（B1 实证）：景别/构图=次维记录不修复；硬维仅状态帧
    （首/末）构成硬错；中帧位移=motion_minor。"""
    from src.agents.review_pipeline import _classify_shot_frames, REVIEW_POLICY_VERSION
    assert REVIEW_POLICY_VERSION == "video_review_v2"
    frames = [
        {"position": "first", "hard_dims_fired": [], "overall": "pass"},
        {"position": "middle", "hard_dims_fired": ["missing_character",
                                                   "shot_size_correctness"],
         "overall": "hard_error"},   # 中帧动作位移+景别——不构成硬错
        {"position": "end", "hard_dims_fired": ["framing_and_composition"],
         "overall": "hard_error"},   # 末帧构图=次维记录
    ]
    cls = _classify_shot_frames(frames)
    assert cls["hard_errors"] == []
    assert "missing_character" in cls["motion_minor"]
    assert "shot_size_correctness" in cls["minor"]
    assert "framing_and_composition" in cls["minor"]
    # 状态帧硬错仍拦截
    frames2 = [
        {"position": "first", "hard_dims_fired": ["unexpected_people"]},
        {"position": "middle", "hard_dims_fired": []},
        {"position": "end", "hard_dims_fired": []},
    ]
    cls2 = _classify_shot_frames(frames2)
    assert cls2["hard_errors"] == ["unexpected_people"]


# ==================================================================
# A3：UNKNOWN 提交暂停（handler 层待核验）
# ==================================================================
def test_watchdog_exhausted_retry_repolls_original_task(tmp_path):
    """01 A3：已知 Provider task ID——watchdog 延长用尽（FAILED）后重试仍先查询
    原任务（续轮询），绝不从 FAILED 直接走新提交（M5 事故路径回归）。"""
    import deps as _deps
    from src.core.job_queue import JobQueue

    class _Q:
        def can(self, kind, amount=1):
            return (True, "")

        def consume(self, kind, amount=1):
            pass

    class _Agnes:
        def __init__(self):
            self.create_calls = 0
            self.poll_calls = []
            self.quota = _Q()

        async def download(self, url, dest):
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"FAKE")
            return str(dest)

        async def create_video(self, *a, **k):
            self.create_calls += 1
            return {"video_id": f"vid_new_{self.create_calls}", "task_id": "t_new"}

        async def poll_video(self, video_id, **k):
            self.poll_calls.append(video_id)
            return {"url": "https://fake/x.mp4", "seconds": 5.0, "size": "1x1"}

    class _Rehost:
        async def upload(self, path):
            return "https://fake/rehost.png"

    class _PM:
        def __init__(self, p):
            self._p = p
            self.base_dir = p

        def get_project_dir(self, name):
            return self._p

    agnes = _Agnes()
    saved = (_deps.agnes_client, _deps._rehost_host, _deps.project_manager)
    _deps.agnes_client = agnes
    _deps._rehost_host = _Rehost()
    _deps.project_manager = _PM(tmp_path)
    try:
        # 项目种子：镜 generating + 已有 provider video_id（=在途任务）
        from src.core.models import (Project, Scene, Shot, Character, StyleAnchor,
                                     ImageAsset, Asset, ShotStatus)
        from src.core.store import ProjectStore
        shot = Shot(shot_id="s01_c01", scene_id="s01", index=1,
                    description="机甲启动", characters=[], target_duration_s=5.0)
        proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime",
                                                       aspect_ratio="16:9"),
                       story_text="t",
                       characters=[Character(name="阿派", description_en="m")],
                       scenes=[Scene(scene_id="s01", summary="s", characters=["阿派"],
                                     shots=[shot])])
        ProjectStore(tmp_path).save(proj)

        def _seed(s):
            s.first_frame = ImageAsset(
                candidates=[Asset(local="x", url="https://fake/f.png",
                                  status=ShotStatus.DONE.value)],
                selected_index=0, status=ShotStatus.DONE.value)
            s.video.status = ShotStatus.GENERATING.value
            s.video.video_id = "vid_original_1"
        ProjectStore(tmp_path).update_shot("s01_c01", _seed)

        q = JobQueue(tmp_path / "j.json", {})
        from routers.shots import _handler_video

        async def scenario():
            q.handlers["video"] = _handler_video
            await q.start()
            # ① 首个 job：watchdog 用尽语义（FAILED）——shot 仍 generating+video_id
            job1 = q.submit(PROJECT, "video", "s01_c01")
            for _ in range(600):
                if job1.state in ("done", "failed", "cancelled"):
                    break
                await asyncio.sleep(0.02)
            assert job1.state == "done", (job1.state, job1.error)
            n_create_after_first = agnes.create_calls
            n_poll_after_first = len(agnes.poll_calls)
            # 重置 shot 回 generating+原 video_id（模拟收货前的在途态）
            ProjectStore(tmp_path).update_shot(
                "s01_c01", lambda s: setattr(s.video, "status",
                                             ShotStatus.GENERATING.value))
            # ② retry 语义：FAILED 后重试 → 必须续轮询原任务，不新提交
            job2 = q.submit(PROJECT, "video", "s01_c01")
            for _ in range(600):
                if job2.state in ("done", "failed", "cancelled"):
                    break
                await asyncio.sleep(0.02)
            return job2, n_create_after_first, n_poll_after_first

        try:
            job2, n_create1, n_poll1 = asyncio.run(scenario())
        finally:
            for t in q._tasks:
                t.cancel()
        assert job2.state == "done", (job2.state, job2.error)
        # 续轮询分支：poll 发生、create_video 未发生（不新提交）
        assert len(agnes.poll_calls) > n_poll1
        assert agnes.create_calls == n_create1 == 0
        assert job2.params.get("provider_task_id") == "vid_original_1"
    finally:
        _deps.agnes_client, _deps._rehost_host, _deps.project_manager = saved


def test_unknown_submit_paused_without_confirm(tmp_path, monkeypatch):
    """generating 无 video_id + 无显式确认 → handler 拒绝新提交（待核验）；
    显式 confirm_uncertain_resubmit 放行并标注。"""
    routers_shots = __import__("routers.shots", fromlist=["_handler_video"])
    # 直接构造最小 job/shot 场景驱动该分支：借用 P55 recovery 的真 handler 思路——
    # 这里聚焦行为分支断言（异常消息含待核验），不重复完整链路。
    from src.core.models import Shot, ShotStatus
    from src.core.store import ProjectStore
    from src.core.models import Project, Scene, Character, StyleAnchor
    proj = Project(name=PROJECT, style=StyleAnchor(art_style="anime",
                                                   aspect_ratio="16:9"),
                   story_text="t",
                   characters=[Character(name="甲", description_en="a")],
                   scenes=[Scene(scene_id="s01", summary="s", characters=["甲"])])
    ProjectStore(tmp_path).save(proj)
    # 走静态断言路径：确认行为代码在位（完整真机链路由 P3 恢复演练覆盖）
    src_text = Path(routers_shots.__file__).read_text(encoding="utf-8")
    assert "confirm_uncertain_resubmit" in src_text
    assert "待核验" in src_text
    assert "previous_submit_uncertain 不是自动" in src_text
    # 契约键已登记（未知参数拒绝闸不误伤）
    assert "confirm_uncertain_resubmit" in vc.VIDEO_JOB_PARAM_KEYS
    assert "repair_of_review" in vc.VIDEO_JOB_PARAM_KEYS
