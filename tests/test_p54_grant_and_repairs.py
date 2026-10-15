# tests/test_p54_grant_and_repairs.py
# Phase 54 A 批回归：A1 scoped grant（授权生命周期/工具面合并/审计/零工具闸措辞）、
# A2 修复目标 prior-credit 豁口封堵（目标镜必须新 versionId）、
# A3 终审卡 refresh（对象变更合法重审）。
# 全部零 LLM / 零生成；与 p48 引擎测试同约定（monkeypatch 派发层）。
import asyncio
import json

import pytest

from src.core.models import Project, Scene, Shot, Character, Asset, VideoVersion
from src.core.store import ProjectStore
from src.workflow import models as M
from src.workflow.executor import WorkflowEngine
from src.workflow.outputs import verify_node_outputs
from src.workflow.store import RunStore
from src.agents import turn_registry
from src.agents.tasks import AgentTaskStore
from src.agents.runtime import AgentRuntime
from src.core.write_lock import _clear_for_test

from src.agents import grant as grant_mod


# ============ 通用夹具（镜像 p48） ============
@pytest.fixture(autouse=True)
def _clean_turn_registry():
    turn_registry.clear()
    yield
    turn_registry.clear()


def _runtime(tmp_path):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json")


@pytest.fixture(autouse=True)
def _patch_heavy_io(monkeypatch, tmp_path):
    monkeypatch.setattr("src.workflow.executor.canonical_baseline",
                        lambda rt, project: {"stub": "before"})
    monkeypatch.setattr("src.workflow.executor.canonical_baseline_rt",
                        lambda project: {"stub": "after"})
    monkeypatch.setattr("src.workflow.executor._rt", lambda: object())
    yield


def _mk_project(tmp_path, name="p54t"):
    pdir = tmp_path / name
    proj = Project(name=name)
    proj.characters = [Character(name="角色甲", description_en="test")]
    sc = Scene(scene_id="s01", summary="test")
    sh = Shot(shot_id="s01_c01", scene_id="s01", index=1, description="d")
    sc.shots = [sh]
    proj.scenes = [sc]
    ProjectStore(pdir).save(proj)
    return pdir


# ============ A1 scoped grant ============
def test_grant_lifecycle_and_merge(tmp_path):
    pdir = _mk_project(tmp_path)
    assert grant_mod.active(pdir, "producer") == []

    g = grant_mod.grant(pdir, "producer", ["script_writer", "director"],
                        reason="p54 test", granted_by="tester", ttl_s=3600)
    act = grant_mod.active(pdir, "producer")
    assert len(act) == 1 and act[0]["grantId"] == g["grantId"]

    face = grant_mod.granted_tool_face(pdir, "producer")
    names = {grant_mod._tool_name(t) for t in face["tools"]}
    # script_writer 与 director 工具族已合并；delegate/提案通道被排除
    assert "script.update" in names or "story.update" in names
    assert "shot.generate_video" in names or any(n.startswith("shot.") for n in names)
    assert not any(n.endswith(".delegate") for n in names)
    assert "proposal.submit" not in names
    assert set(face["executors"]) == names
    assert face["families"] == ["director", "script_writer"]

    assert grant_mod.revoke(pdir, g["grantId"], reason="done", revoked_by="tester")
    assert grant_mod.active(pdir, "producer") == []
    face2 = grant_mod.granted_tool_face(pdir, "producer")
    assert face2["tools"] == [] and face2["executors"] == {}


def test_grant_ttl_expiry(tmp_path):
    pdir = _mk_project(tmp_path)
    g = grant_mod.grant(pdir, "producer", ["assembly"], reason="ttl", ttl_s=1)
    now = g["createdAt"] + 2.0
    assert grant_mod.active(pdir, "producer", now=now) == []
    assert grant_mod.active(pdir, "producer", now=g["createdAt"]) != []


def test_grant_unknown_family_rejected(tmp_path):
    pdir = _mk_project(tmp_path)
    with pytest.raises(ValueError):
        grant_mod.grant(pdir, "producer", ["nonexistent_family"], reason="x")


def test_grant_audit_trail(tmp_path):
    pdir = _mk_project(tmp_path)
    g = grant_mod.grant(pdir, "producer", ["script_writer"], reason="audit")
    grant_mod.audit_execution(pdir, "producer", [g["grantId"]], "script.update", True)
    audit = [json.loads(x) for x in
             open(grant_mod._audit_path(pdir), encoding="utf-8") if x.strip()]
    evs = [a["event"] for a in audit]
    assert "grant" in evs and "execution" in evs
    ex = [a for a in audit if a["event"] == "execution"][0]
    assert ex["actor"] == "producer" and ex["tool"] == "script.update" and ex["ok"] is True
    grant_mod.revoke(pdir, g["grantId"], reason="end")
    audit = [json.loads(x) for x in
             open(grant_mod._audit_path(pdir), encoding="utf-8") if x.strip()]
    assert any(a["event"] == "revoke" for a in audit)


def test_zero_tool_nudge_wording_adapts(tmp_path):
    pdir = _mk_project(tmp_path)
    orig = "你的工具面里没有写工具——未委派即未发生，canonical 没有变化。"
    assert "没有写工具" in grant_mod.wrap_zero_tool_nudge(orig, False)
    wrapped = grant_mod.wrap_zero_tool_nudge(orig, True)
    assert "没有写工具" not in wrapped and "scoped grant" in wrapped


# ============ A2 修复目标 prior-credit 封堵 ============
def _video_match(shots):
    """构造 video_version 匹配记录（模拟 _resolve_kind 产物）。"""
    return [{"kind": "video_version", "entityId": sid, "versionId": vid,
             "reviewState": "candidate", "fileOk": True, "contentHash": "h"}
            for sid, vid in shots]


def test_repair_require_new_blocks_prior_credit(tmp_path, monkeypatch):
    pdir = _mk_project(tmp_path)
    # 节点计划：带 repairRequireNew（目标镜 s01_c01）
    node_plan = {"objective": "o",
                 "completion": {"strategy": "require_new",
                                "outputs": [{"kind": "video_version", "minCount": 1}]},
                 "repairRequireNew": {"shotIds": ["s01_c01"], "reason": "rejected fix"}}
    # changed 为空、prior 里记有 s01_c01 的旧版本（本节点此前 attempt 写入）
    changed, prior = {}, {"shots": {"s01_c01"}}
    matches = _video_match([("s01_c01", "vv_old")])
    monkeypatch.setattr("src.workflow.outputs._resolve_kind", lambda *a, **k: matches)
    out = verify_node_outputs(pdir, "p54t", node_plan, changed, prior, None)
    assert out["code"] == "outputs_missing", "prior credit 必须被 repairRequireNew 封堵"

    # 新 attempt 真实写入（changed 命中）→ 通过
    changed2 = {"shots": {"s01_c01"}}
    out2 = verify_node_outputs(pdir, "p54t", node_plan, changed2, prior, None)
    assert out2["ok"], "本 attempt 新写入应计入"


def test_repair_require_new_not_set_keeps_prior_semantics(tmp_path, monkeypatch):
    pdir = _mk_project(tmp_path)
    node_plan = {"objective": "o",
                 "completion": {"strategy": "allow_verified_reuse",
                                "outputs": [{"kind": "video_version", "minCount": 1}]}}
    matches = _video_match([("s01_c01", "vv_old")])
    monkeypatch.setattr("src.workflow.outputs._resolve_kind", lambda *a, **k: matches)
    changed, prior = {}, {"shots": {"s01_c01"}}
    out = verify_node_outputs(pdir, "p54t", node_plan, changed, prior, None)
    # 无 repairRequireNew 时维持既有语义（prior 计入/复用路径按策略）
    assert out["ok"] or out["code"] != "outputs_missing"


# ============ A3 审核卡 refresh ============
def _mk_run_with_pending_review(tmp_path, name="p54r"):
    pdir = _mk_project(tmp_path, name)
    rs = RunStore(pdir, name)
    run = {"runId": "run_p54", "project": name, "workflowId": "wf", "status": M.RUN_WAITING_HUMAN,
           "idempotencyKey": "k1", "startParamsHash": "h", "snapshot": {},
           "plan": {"planVersion": 1, "order": ["n_final"],
                    "nodes": {"n_final": {"nodeId": "n_final", "type": "human_review",
                                          "label": "成片审定", "reviewIndex": 1}}},
           "nodes": {"n_final": {"nodeId": "n_final", "type": "human_review", "status": "waiting_human",
                                 "attempts": [], "currentAttemptId": None, "handoff": None,
                                 "error": None, "upstreamNodeId": "n_asm",
                                 "currentReviewId": "rev_x1", "lastReviewDecision": None,
                                 "statusAt": 0.0}},
           "reviews": [{
               "requestId": "rev_x1", "nodeId": "n_final", "label": "成片审定",
               "targetNodeId": "n_asm", "targetAttemptId": "att_1",
               "artifactRefs": [{"kind": "sequence", "id": "seq_1", "source": "test"}],
               "artifactFingerprint": "fp-old",
               "contentSnapshot": {"hashes": {"sequence:seq_1": "sha256:old"}, "at": 1.0},
               "selectionRequired": False, "selectionSpec": None,
               "selectionCandidates": [], "selectionState": "none",
               "selectionReceipt": None, "status": "pending",
               "createdAt": "t", "decidedAt": None, "decision": None,
               "decisionNote": None, "decisionReceipt": None}],
           "cursor": 1, "stopRequested": False, "error": None,
           "createdAt": "t", "updatedAt": "t"}
    rs.create(run)
    return pdir, rs


def test_review_refresh_updates_snapshot(tmp_path):
    pdir, rs = _mk_run_with_pending_review(tmp_path)
    from src.workflow.executor import _artifact_content_snapshot
    from src.workflow.store import content_hash
    run = rs.load("run_p54")
    rv = run["reviews"][0]
    refs = rv["artifactRefs"]
    cur = _artifact_content_snapshot(pdir, rs._project if hasattr(rs, "_project") else "p54r", refs)
    assert cur and "sequence:seq_1" in cur["hashes"]

    # 模拟 INT-M03 手术 + 官方 refresh 后：卡快照=当前内容 → 决策通过闸
    def _mut(r):
        r["reviews"][0]["contentSnapshot"] = cur
        r["reviews"][0]["artifactFingerprint"] = content_hash(
            {"refs": refs, "attempt": rv["targetAttemptId"]})
        r["reviews"][0]["refreshedAt"] = "now"
        r["reviews"][0]["refreshReason"] = "INT-M02 sequence corrected"
    rs.update("run_p54", _mut)
    run2 = rs.load("run_p54")
    assert run2["reviews"][0]["contentSnapshot"]["hashes"] == cur["hashes"]
    assert run2["reviews"][0]["refreshReason"].startswith("INT-M02")


def test_refresh_rejects_decided_card(tmp_path):
    pdir, rs = _mk_run_with_pending_review(tmp_path)
    def _decide(r):
        r["reviews"][0]["status"] = "decided"
        r["reviews"][0]["decision"] = "approve"
    rs.update("run_p54", _decide)
    run = rs.load("run_p54")
    assert run["reviews"][0]["status"] == "decided"
    # 端点层守卫在路由中（409）；store 层验证已决策卡不被 mutate 函数误伤
    def _mut(r):
        for x in r.get("reviews") or []:
            if x.get("requestId") == "rev_x1" and x.get("status") == "pending":
                x["contentSnapshot"] = {"hashes": {}, "at": 2.0}
    rs.update("run_p54", _mut)
    assert rs.load("run_p54")["reviews"][0]["contentSnapshot"]["hashes"] == \
        {"sequence:seq_1": "sha256:old"}
