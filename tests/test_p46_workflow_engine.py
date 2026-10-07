# tests/test_p46_workflow_engine.py
# Phase 46 — 工作流后端引擎单测（零 LLM / 零生成）：结构校验、编译、revision 冲突、
# 幂等启动、顺序执行、双审核、驳回停止、停止、恢复对账、Producer 派发守卫。
# 派发层 monkeypatch（adapter.dispatch_node / executor._rt / canonical_baseline），
# 真实生产链路由 F 批真机测试覆盖。
import asyncio
import json

import pytest

from src.workflow import models as M
from src.workflow.adapter import build_envelope, upstream_context, verify_completion
from src.workflow.executor import WorkflowEngine, WorkflowError
from src.workflow.store import DefinitionStore, EventLog, RunStore, RevisionConflict
from src.workflow.validate import preflight_for_start, validate_definition
from src.agents.runtime import AgentRuntime
from src.agents.tasks import AgentTaskStore
from src.agents import turn_registry


# ============ 夹具 ============
def _runtime(tmp_path):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json")


@pytest.fixture(autouse=True)
def _clean_turn_registry():
    turn_registry.clear()
    yield
    turn_registry.clear()


@pytest.fixture(autouse=True)
def _patch_heavy_io(monkeypatch, tmp_path):
    """canonical 指纹与 runtime 解析在单测中打桩（文件底座本身走真 tmp_path）。"""
    monkeypatch.setattr("src.workflow.executor.canonical_baseline",
                        lambda rt, project: {"stub": "before"})
    monkeypatch.setattr("src.workflow.executor.canonical_baseline_rt",
                        lambda project: {"stub": "after"})
    monkeypatch.setattr("src.workflow.executor._rt", lambda: object())
    yield


def _node(nid, ntype, ref=None, objective=None, **cfg):
    c = dict(cfg)
    if objective is not None:
        c["objective"] = objective
    return {"id": nid, "type": ntype, "refId": ref, "refVersion": None,
            "label": None, "config": c, "position": {"x": 0, "y": 0}}


def _edge(eid, src, dst, etype="execution", skill_version=None):
    e = {"id": eid, "type": etype, "source": src, "target": dst,
         "sourcePort": "out", "targetPort": "in"}
    if skill_version:
        e["skillVersion"] = skill_version
    return e


def _legal_chain_definition():
    """边界 → 编剧 → 审核 → 装配 → 边界（最小合法链）。"""
    nodes = [
        _node("nst", "boundary_start"),
        _node("n_sw", "agent", "script_writer", objective="写一个 10 秒小故事"),
        _node("n_rv", "human_review", label="故事审定",
              targetSourceNodeId="n_sw", targetArtifact="故事文本",
              instructions="检查叙事完整性", reviewer="制作人"),
        _node("n_asm", "agent", "assembly", objective="装配无声预览"),
        _node("nen", "boundary_end"),
    ]
    edges = [
        _edge("e1", "nst", "n_sw"),
        _edge("e2", "n_sw", "n_rv"),
        _edge("e3", "n_rv", "n_asm"),
        _edge("e4", "n_asm", "nen"),
    ]
    return {"schemaVersion": "workflow_draft_v1", "projectId": "p46t",
            "workflowId": "wf_default", "name": "测试链",
            "status": "draft", "nodes": nodes, "edges": edges}


def _ok_task_result(ref_id, domain_ids=("s01",)):
    """按 ref_id 构造「已完成且有已验证回执」的 task_result（verify 通过）。"""
    domain_key = {"script_writer": "scenes", "character_designer": "looks",
                  "scene_designer": "locations", "director": "shots",
                  "assembly": "sequences"}.get(ref_id, "scenes")
    return {"task_id": "task_x", "agent": ref_id, "status": "completed",
            "summary": "完成", "verified": True, "n_tool_calls": 3,
            "changed_refs": {domain_key: list(domain_ids)},
            "warnings": [], "session_id": "sess"}


def _make_dispatch(monkeypatch, outcomes):
    """outcomes: {refId: task_result 或 Exception}；记录调用顺序。
    Phase 47：签名跟随 adapter.dispatch_node（frozen_inputs §4.5 逐节点输入冻结）。"""
    calls = []

    async def fake_dispatch(rt, project, run, node_plan, attempt, *, event_cb,
                            cancel_event, frozen_inputs=None):
        ref = node_plan["refId"]
        calls.append(ref)
        out = outcomes.get(ref)
        if isinstance(out, Exception):
            raise out
        return {"status": out.get("status", "completed"),
                "task_result": out,
                "session_id": "sess_" + ref,
                "skillEvidence": {"loaded": []},
                "raw": {}}

    monkeypatch.setattr("src.workflow.adapter.dispatch_node", fake_dispatch)
    return calls


# ============ 校验 / 编译 ============
def test_validate_legal_chain_ok(tmp_path):
    rt = _runtime(tmp_path)
    d = _legal_chain_definition()
    v = validate_definition(d, rt, "p46t")
    assert v["ok"], v["errors"]
    from src.workflow.plan import compile_plan
    plan = compile_plan(d, v, v["catalog"])
    assert plan["order"] == ["n_sw", "n_rv", "n_asm"]
    assert plan["nodes"]["n_sw"]["objective"] == "写一个 10 秒小故事"
    assert plan["nodes"]["n_rv"]["reviewIndex"] == 1
    assert plan["nodes"]["n_asm"]["upstreamNodeId"] == "n_rv"


def test_validate_rejects_cycle_branch_merge_isolated(tmp_path):
    rt = _runtime(tmp_path)
    base = _legal_chain_definition()
    # 环：sw → asm（sw 已有 →rv；这里补 asm → sw 成环）
    d = json.loads(json.dumps(base))
    d["edges"].append(_edge("e5", "n_asm", "n_sw"))
    v = validate_definition(d, rt, "p46t")
    assert not v["ok"]
    assert any(i["code"] == "EXEC_CYCLE" for i in v["errors"])

    # 分叉：sw 两条执行出线
    d = json.loads(json.dumps(base))
    d["nodes"].append(_node("n_x", "agent", "director", objective="x"))
    d["edges"].append(_edge("e6", "n_sw", "n_x"))
    v = validate_definition(d, rt, "p46t")
    assert any(i["code"] == "STRUCT_BRANCH" for i in v["errors"])

    # 汇合：asm 两条执行入线
    d = json.loads(json.dumps(base))
    d["nodes"].append(_node("n_x", "agent", "director", objective="x"))
    d["edges"].append(_edge("e7", "n_x", "n_asm"))
    v = validate_definition(d, rt, "p46t")
    assert any(i["code"] == "STRUCT_MERGE" for i in v["errors"])

    # 孤立 Agent（不连通）
    d = json.loads(json.dumps(base))
    d["nodes"].append(_node("n_iso", "agent", "director", objective="x"))
    v = validate_definition(d, rt, "p46t")
    assert any(i["code"] == "STRUCT_DISCONNECTED" for i in v["errors"])


def test_validate_rejects_skill_to_review_and_unknown_agent(tmp_path):
    rt = _runtime(tmp_path)
    d = json.loads(json.dumps(_legal_chain_definition()))
    d["nodes"].append(_node("n_sk", "skill", "full-story-relay-calibration",
                            refVersion="1.3.1"))
    d["edges"].append(_edge("eb1", "n_sk", "n_rv", etype="skill_binding"))
    v = validate_definition(d, rt, "p46t")
    assert any(i["code"] == "BIND_WRONG_TARGET" for i in v["errors"])

    d2 = json.loads(json.dumps(_legal_chain_definition()))
    d2["nodes"][1]["refId"] = "no_such_agent"
    v2 = validate_definition(d2, rt, "p46t")
    assert any(i["code"] == "AGENT_UNKNOWN" for i in v2["errors"])


def test_preflight_blocks_missing_objective(tmp_path):
    rt = _runtime(tmp_path)
    d = json.loads(json.dumps(_legal_chain_definition()))
    d["nodes"][1]["config"]["objective"] = ""
    pre = preflight_for_start(d, rt, "p46t")
    assert not pre["ok"]
    assert any(b["code"] == "RUN_NO_OBJECTIVE" for b in pre["blockers"])


# ============ 定义保存 / revision 冲突 ============
def test_definition_store_revision_conflict(tmp_path):
    store = DefinitionStore(tmp_path, "p46t")
    doc = store.save("wf_default", _legal_chain_definition(), None)
    assert doc["revision"] == 1
    doc2 = store.save("wf_default", _legal_chain_definition(), 1)
    assert doc2["revision"] == 2
    with pytest.raises(RevisionConflict):
        store.save("wf_default", _legal_chain_definition(), 1)
    with pytest.raises(RevisionConflict):
        store.save("wf_default", _legal_chain_definition(), None)


# ============ 引擎生命周期 ============
def _start_engine(tmp_path, monkeypatch, outcomes, definition=None):
    eng = WorkflowEngine()
    calls = _make_dispatch(monkeypatch, outcomes)
    d = definition or _legal_chain_definition()
    d = dict(d)
    d["projectId"] = "p46t"
    v = validate_definition(d, _runtime(tmp_path), "p46t")
    assert v["ok"], v["errors"]
    from src.workflow.plan import compile_plan, build_start_snapshot
    plan = compile_plan(d, v, v["catalog"])
    snap = build_start_snapshot(d, _runtime(tmp_path), "p46t", v, plan)
    out = eng.start_run(tmp_path, "p46t", d, v, plan, snap, "key-1")
    return eng, out["run"], calls


def _drive_sync(eng, tmp_path, run_id, wait=3.0):
    """在单一事件循环内启动 worker 并等待其停靠（ensure_future 需要运行中的循环）。"""
    async def _go():
        eng.drive(tmp_path, "p46t", run_id)
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        while loop.time() - t0 < wait:
            t = eng._workers.get(run_id)
            if t is None or t.done():
                break
            await asyncio.sleep(0.02)
    asyncio.new_event_loop().run_until_complete(_go())


def test_chain_with_review_happy_path(tmp_path, monkeypatch):
    outcomes = {"script_writer": _ok_task_result("script_writer"),
                "assembly": _ok_task_result("assembly", ("seq_1",))}
    eng, run, calls = _start_engine(tmp_path, monkeypatch, outcomes)
    _drive_sync(eng, tmp_path, run["runId"])
    runs = RunStore(tmp_path, "p46t")
    r = runs.load(run["runId"])
    # 停靠在审核（waiting_human），编剧已成功、有交接包
    assert r["status"] == M.RUN_WAITING_HUMAN
    assert r["nodes"]["n_sw"]["status"] == M.NODE_SUCCESS
    assert r["nodes"]["n_sw"]["handoff"]["artifactRefs"][0]["id"] == "s01"
    assert len(r["reviews"]) == 1 and r["reviews"][0]["status"] == "pending"
    assert calls == ["script_writer"]   # 装配未派发（审核未过）

    # 决策通过 → 装配派发一次 → run completed
    out = eng.decide_review(tmp_path, "p46t", run["runId"],
                            r["reviews"][0]["requestId"], "approve", "通过")
    assert out["alreadyDecided"] is False
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert r["status"] == M.RUN_COMPLETED
    assert calls == ["script_writer", "assembly"]
    # 决策幂等：重复同一决策返回已处理
    out2 = eng.decide_review(tmp_path, "p46t", run["runId"],
                             r["reviews"][0]["requestId"], "approve")
    assert out2["alreadyDecided"] is True
    # 事件单调
    evs = EventLog(tmp_path, run["runId"]).read()
    seqs = [e["seq"] for e in evs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_review_reject_stops_downstream(tmp_path, monkeypatch):
    outcomes = {"script_writer": _ok_task_result("script_writer")}
    eng, run, calls = _start_engine(tmp_path, monkeypatch, outcomes)
    _drive_sync(eng, tmp_path, run["runId"])
    runs = RunStore(tmp_path, "p46t")
    r = runs.load(run["runId"])
    eng.decide_review(tmp_path, "p46t", run["runId"],
                      r["reviews"][0]["requestId"], "reject", "故事不行")
    r = runs.load(run["runId"])
    assert r["status"] == M.RUN_REJECTED
    assert r["nodes"]["n_rv"]["status"] == M.NODE_STOPPED
    assert r["nodes"]["n_asm"]["status"] == M.NODE_STOPPED
    assert calls == ["script_writer"]   # 装配从未派发


def test_two_consecutive_reviews_first_only(tmp_path, monkeypatch):
    d = _legal_chain_definition()
    d["nodes"].insert(3, _node("n_rv2", "human_review", label="二道审核",
                               targetSourceNodeId="n_rv"))
    d["edges"] = [e for e in d["edges"] if e["id"] != "e3"]
    d["edges"].append(_edge("e3a", "n_rv", "n_rv2"))
    d["edges"].append(_edge("e3b", "n_rv2", "n_asm"))
    outcomes = {"script_writer": _ok_task_result("script_writer"),
                "assembly": _ok_task_result("assembly", ("seq_1",))}
    eng, run, calls = _start_engine(tmp_path, monkeypatch, outcomes, d)
    _drive_sync(eng, tmp_path, run["runId"])
    runs = RunStore(tmp_path, "p46t")
    r = runs.load(run["runId"])
    assert len(r["reviews"]) == 1
    eng.decide_review(tmp_path, "p46t", run["runId"],
                      r["reviews"][0]["requestId"], "approve")
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    # 第二轮仍等待；下游 task 数为 0
    assert r["status"] == M.RUN_WAITING_HUMAN
    assert len(r["reviews"]) == 2
    assert r["reviews"][1]["status"] == "pending"
    assert calls == ["script_writer"]
    eng.decide_review(tmp_path, "p46t", run["runId"],
                      r["reviews"][1]["requestId"], "approve")
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert r["status"] == M.RUN_COMPLETED
    assert calls == ["script_writer", "assembly"]


def test_node_failure_no_verified_receipts(tmp_path, monkeypatch):
    bad = {"task_id": "task_y", "agent": "script_writer", "status": "completed",
           "summary": "我说完成了", "verified": False, "n_tool_calls": 0,
           "changed_refs": {"scenes": []}, "warnings": [], "session_id": "s"}
    outcomes = {"script_writer": bad}
    eng, run, calls = _start_engine(tmp_path, monkeypatch, outcomes)
    _drive_sync(eng, tmp_path, run["runId"])
    r = RunStore(tmp_path, "p46t").load(run["runId"])
    assert r["nodes"]["n_sw"]["status"] == M.NODE_FAILED
    assert r["status"] == M.RUN_FAILED
    assert "预期产物域" in (r["nodes"]["n_sw"]["error"] or "")


def test_verify_completion_producer_planning_node():
    pn = {"nodeId": "n1", "refId": "producer", "expectedDomains": []}
    tr = {"status": "completed", "summary": "计划", "changed_refs": {}}
    assert verify_completion(pn, tr)["ok"] is True
    tr_bad = {"status": "failed", "summary": "x", "changed_refs": {}}
    assert verify_completion(pn, tr_bad)["ok"] is False


def test_idempotent_start_same_key_reused_new_key_new_run(tmp_path, monkeypatch):
    outcomes = {"script_writer": _ok_task_result("script_writer")}
    eng = WorkflowEngine()
    _make_dispatch(monkeypatch, outcomes)
    d = dict(_legal_chain_definition())
    d["projectId"] = "p46t"
    v = validate_definition(d, _runtime(tmp_path), "p46t")
    from src.workflow.plan import compile_plan, build_start_snapshot
    plan = compile_plan(d, v, v["catalog"])
    snap = build_start_snapshot(d, _runtime(tmp_path), "p46t", v, plan)
    out1 = eng.start_run(tmp_path, "p46t", d, v, plan, snap, "key-A")
    out2 = eng.start_run(tmp_path, "p46t", d, v, plan, snap, "key-A")
    assert out1["run"]["runId"] == out2["run"]["runId"]
    assert out2["reused"] is True
    # 新键 → 允许新 run（幂等键只约束同键行为）
    out3 = eng.start_run(tmp_path, "p46t", d, v, plan, snap, "key-B")
    assert out3["run"]["runId"] != out1["run"]["runId"]
    assert out3["reused"] is False


def test_idempotency_same_key_different_params_conflicts(tmp_path, monkeypatch):
    outcomes = {"script_writer": _ok_task_result("script_writer")}
    eng = WorkflowEngine()
    _make_dispatch(monkeypatch, outcomes)
    d1 = dict(_legal_chain_definition())
    d1["projectId"] = "p46t"
    d2 = json.loads(json.dumps(d1))
    d2["name"] = "改了名字"
    v1 = validate_definition(d1, _runtime(tmp_path), "p46t")
    v2 = validate_definition(d2, _runtime(tmp_path), "p46t")
    from src.workflow.plan import compile_plan, build_start_snapshot
    p1 = compile_plan(d1, v1, v1["catalog"])
    p2 = compile_plan(d2, v2, v2["catalog"])
    s1 = build_start_snapshot(d1, _runtime(tmp_path), "p46t", v1, p1)
    s2 = build_start_snapshot(d2, _runtime(tmp_path), "p46t", v2, p2)
    eng.start_run(tmp_path, "p46t", d1, v1, p1, s1, "key-K")
    with pytest.raises(WorkflowError) as ei:
        eng.start_run(tmp_path, "p46t", d2, v2, p2, s2, "key-K")
    assert ei.value.code == "idempotency_conflict"


def test_stop_prevents_downstream_dispatch(tmp_path, monkeypatch):
    outcomes = {"script_writer": _ok_task_result("script_writer")}
    eng, run, calls = _start_engine(tmp_path, monkeypatch, outcomes)
    _drive_sync(eng, tmp_path, run["runId"])
    runs = RunStore(tmp_path, "p46t")
    eng.request_stop(tmp_path, "p46t", run["runId"])
    r = runs.load(run["runId"])
    assert r["stopRequested"] is True
    # 停止后审核不派发下游：决策通过也不再驱动新节点
    eng.decide_review(tmp_path, "p46t", run["runId"],
                      r["reviews"][0]["requestId"], "approve")
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert calls == ["script_writer"]
    assert r["nodes"]["n_asm"]["status"] in (M.NODE_STOPPED, M.NODE_PENDING)
    assert r["nodes"]["n_asm"]["status"] != M.NODE_SUCCESS


def test_reconcile_running_attempt_needs_reconciliation(tmp_path, monkeypatch):
    """R03/R05 诚实语义：进程重启后，在途 attempt 的正式任务仍 running →
    needs_reconciliation，不自动重发。"""
    outcomes = {"script_writer": _ok_task_result("script_writer")}
    eng, run, calls = _start_engine(tmp_path, monkeypatch, outcomes)
    runs = RunStore(tmp_path, "p46t")
    # 模拟：dispatching attempt 已落盘 + AgentTask 已建（workflow_ref 关联，状态 running）
    att = {"attemptId": "att_x", "status": "dispatching", "startedAt": 0.0,
           "endedAt": None, "taskRefs": [], "result": None, "error": None,
           "inputs": {}, "outputs": None, "skillEvidence": None, "thinking": True}
    def _seed(r):
        n = r["nodes"]["n_sw"]
        n["attempts"].append(att)
        n["currentAttemptId"] = "att_x"
    runs.update(run["runId"], _seed)
    tasks = AgentTaskStore(tmp_path, "p46t")
    from src.agents.models import AgentTask
    t = AgentTask(task_id="task_stuck", project="p46t", requester_agent="workflow",
                  target_agent="script_writer", capability_id="script_writer.delegate",
                  status="running",
                  workflow_ref={"runId": run["runId"], "nodeId": "n_sw",
                                "attemptId": "att_x"})
    tasks.create(t)
    # drive：worker 会先对账 → needs_reconciliation 停靠
    _make_dispatch(monkeypatch, outcomes)   # 重新挂 fake（engine 新建后 monkeypatch 仍在）
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert r["nodes"]["n_sw"]["status"] == M.NODE_NEEDS_RECONCILIATION
    assert r["status"] == M.RUN_NEEDS_RECONCILIATION
    assert calls == []   # 未重发


def test_reconcile_settled_task_adopted(tmp_path, monkeypatch):
    """R04：正式任务已结算但本地状态未写完 → 收养结果继续。"""
    eng, run, calls = _start_engine(tmp_path, monkeypatch, {})
    runs = RunStore(tmp_path, "p46t")
    att = {"attemptId": "att_y", "status": "running", "startedAt": 0.0,
           "endedAt": None, "taskRefs": [], "result": None, "error": None,
           "inputs": {}, "outputs": None, "skillEvidence": None, "thinking": True}
    def _seed(r):
        n = r["nodes"]["n_sw"]
        n["attempts"].append(att)
        n["currentAttemptId"] = "att_y"
    runs.update(run["runId"], _seed)
    tasks = AgentTaskStore(tmp_path, "p46t")
    from src.agents.models import AgentTask
    settled = _ok_task_result("script_writer")
    settled["task_id"] = "task_done"   # 真实链路：structured_result.task_id == AgentTask.task_id
    t = AgentTask(task_id="task_done", project="p46t", requester_agent="workflow",
                  target_agent="script_writer", capability_id="script_writer.delegate",
                  status="completed", structured_result=settled,
                  workflow_ref={"runId": run["runId"], "nodeId": "n_sw",
                                "attemptId": "att_y"})
    tasks.create(t)
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert r["nodes"]["n_sw"]["status"] == M.NODE_SUCCESS
    assert r["nodes"]["n_sw"]["handoff"]["taskRefs"] == ["task_done"]
    # 收养后继续推进到审核停靠
    assert r["status"] == M.RUN_WAITING_HUMAN
    assert calls == []   # 收养不算新派发


def test_continue_after_reconciliation_creates_new_attempt(tmp_path, monkeypatch):
    outcomes = {"script_writer": _ok_task_result("script_writer"),
                "assembly": _ok_task_result("assembly", ("seq_1",))}
    eng, run, calls = _start_engine(tmp_path, monkeypatch, {})
    runs = RunStore(tmp_path, "p46t")
    att = {"attemptId": "att_z", "status": "dispatching", "startedAt": 0.0,
           "endedAt": None, "taskRefs": [], "result": None, "error": None,
           "inputs": {}, "outputs": None, "skillEvidence": None, "thinking": True}
    def _seed(r):
        n = r["nodes"]["n_sw"]
        n["attempts"].append(att)
        n["currentAttemptId"] = "att_z"
    runs.update(run["runId"], _seed)
    calls = _make_dispatch(monkeypatch, outcomes)   # 重挂 fake：接收新 calls 列表
    # 无任务关联 → 对账失败停靠
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert r["nodes"]["n_sw"]["status"] == M.NODE_NEEDS_RECONCILIATION
    # 显式继续 → 新 attempt 真实派发
    eng.continue_run(tmp_path, "p46t", run["runId"])
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert r["nodes"]["n_sw"]["status"] == M.NODE_SUCCESS
    assert r["nodes"]["n_sw"]["handoff"]["taskRefs"] == ["task_x"]
    assert calls == ["script_writer"]
    # 旧 attempt 保留（历史不覆盖）
    assert len(r["nodes"]["n_sw"]["attempts"]) == 2
    assert r["nodes"]["n_sw"]["attempts"][0]["attemptId"] == "att_z"


def test_continue_failed_run_resets_failed_node(tmp_path, monkeypatch):
    """Phase48 E：节点 failed 使 run 推导为 failed（终态），但 docstring 契约是
    failed 节点可显式重试开新 attempt——此前 run_terminal 检查把 failed run 一律
    拒绝，该分支不可达（真机 P48_E_2D n_sc continue 409 实证）。修复后：failed
    run + failed 节点 continue 放行，旧 attempt 保留；stopped/completed 仍拒绝。"""
    bad = {"task_id": "task_y", "agent": "script_writer", "status": "completed",
           "summary": "我说完成了", "verified": False, "n_tool_calls": 0,
           "changed_refs": {"scenes": []}, "warnings": [], "session_id": "s"}
    eng, run, _ = _start_engine(tmp_path, monkeypatch, {"script_writer": bad})
    runs = RunStore(tmp_path, "p46t")
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert r["status"] == M.RUN_FAILED
    assert r["nodes"]["n_sw"]["status"] == M.NODE_FAILED
    first_att = r["nodes"]["n_sw"]["attempts"][0]["attemptId"]
    # failed run 显式继续：开新 attempt（不再 run_terminal 409）
    calls = _make_dispatch(monkeypatch, {
        "script_writer": _ok_task_result("script_writer"),
        "assembly": _ok_task_result("assembly", ("seq_1",))})
    eng.continue_run(tmp_path, "p46t", run["runId"], "n_sw")
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert r["nodes"]["n_sw"]["status"] == M.NODE_SUCCESS
    assert calls == ["script_writer"]   # 停靠中间审核，装配未派发
    # 审核通过 → 装配派发 → completed
    eng.decide_review(tmp_path, "p46t", run["runId"],
                      r["reviews"][0]["requestId"], "approve", "通过")
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert r["status"] == M.RUN_COMPLETED
    assert calls == ["script_writer", "assembly"]
    # 旧 failed attempt 保留为真实历史
    atts = r["nodes"]["n_sw"]["attempts"]
    assert len(atts) == 2
    assert atts[0]["attemptId"] == first_att
    assert atts[0]["status"] == "failed"


def test_continue_still_rejects_stopped_and_completed(tmp_path, monkeypatch):
    """A06 停止语义不受 Phase48 E failed-run 放行影响：stopped/completed 仍拒绝。"""
    outcomes = {"script_writer": _ok_task_result("script_writer"),
                "assembly": _ok_task_result("assembly", ("seq_1",))}
    eng, run, _ = _start_engine(tmp_path, monkeypatch, outcomes)
    runs = RunStore(tmp_path, "p46t")
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert r["status"] == M.RUN_WAITING_HUMAN
    eng.decide_review(tmp_path, "p46t", run["runId"],
                      r["reviews"][0]["requestId"], "approve", "通过")
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert r["status"] == M.RUN_COMPLETED
    with pytest.raises(WorkflowError) as ei:
        eng.continue_run(tmp_path, "p46t", run["runId"], "n_sw")
    assert ei.value.code == "run_terminal"


# ============ Producer 派发守卫（服务端限制） ============
def test_producer_guard_blocks_out_of_scope_delegate(tmp_path):
    """工作流上下文中 _delegate_specialist 直接拒绝越权目标：不建任务、不进 turn。"""
    from src.agents.producer import WF_DELEGATE_GUARD, _delegate_specialist
    from src.agents.models import ScopeType
    rt = _runtime(tmp_path)
    token = WF_DELEGATE_GUARD.set({"runId": "run_g", "nodeId": "n_p",
                                   "attemptId": "att_g", "allowedDelegates": []})
    try:
        out = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            _delegate_specialist(rt, "p46t", "script_writer",
                                 {"objective": "偷跑编剧"},
                                 event_cb=None, cancel_event=None,
                                 prev_task_id=None, workflow_id="wf_run:run_g"))
    finally:
        WF_DELEGATE_GUARD.reset(token)
    assert out["ok"] is False
    assert out["code"] == "workflow_scope_denied"
    tasks = AgentTaskStore(tmp_path, "p46t")
    assert tasks.list() == []   # 未创建 AgentTask


def test_envelope_contains_upstream_handoff_and_skills(tmp_path):
    run = {"runId": "run_h", "snapshot": {"definition": {"name": "链"}}}
    run["nodes"] = {
        "n_up": {"nodeId": "n_up", "refId": "script_writer",
                 "currentAttemptId": "att1",
                 "handoff": {"artifactRefs": [{"kind": "scene", "id": "s01"}],
                             "brief": "写好了"}},
        "n_d": {"nodeId": "n_d", "refId": "director", "status": "pending",
                "currentAttemptId": None, "handoff": None},
    }
    pn = {"nodeId": "n_d", "refId": "director", "objective": "导演这批镜头",
          "skills": [{"skillId": "full-story-relay-calibration", "version": "1.3.1"}],
          "upstreamNodeId": "n_up"}
    run["nodes"]["n_d"]["upstreamNodeId"] = "n_up"
    ctx = upstream_context(run, pn)
    assert ctx["upstream"]["handoff"]["artifactRefs"][0]["id"] == "s01"
    assert ctx["earlierNodes"] == []
    env = build_envelope(None, "p", run, pn)
    assert "导演这批镜头" in env["objective"]
    assert "s01" in env["situation"]
    assert any("full-story-relay-calibration" in c for c in env["constraints"])
    assert any("不要委派" not in c for c in env["constraints"][:1])


def test_envelope_retry_feedback_includes_failed_attempt_error(tmp_path):
    """Phase48 E：重试 attempt 的委派信封必须带上一轮 failed attempt 的校验错误
    （只给事实不指定做法）——否则会话复用下 Agent 以"已做过"为由不动手
    （真机 P48_E_2D n_sc attempt2 实证：无反馈 → 零写入收工）。"""
    run = {"runId": "run_r", "snapshot": {"definition": {"name": "链"}}}
    run["nodes"] = {
        "n_sc": {"nodeId": "n_sc", "refId": "scene_designer", "status": "failed",
                 "currentAttemptId": "att2",
                 "attempts": [
                     {"attemptId": "att1", "status": "failed",
                      "error": "目标级输出缺项：要求 location ×2（需 2）仅 1 个"},
                     {"attemptId": "att2", "status": "failed",
                      "error": "任务结束但未见预期产物域的已验证回执"},
                 ],
                 "handoff": None},
    }
    pn = {"nodeId": "n_sc", "refId": "scene_designer", "objective": "建立全部场景"}
    env = build_envelope(None, "p", run, pn)
    assert "重试提示" in env["situation"]
    assert "已验证回执" in env["situation"]                 # 最近一次失败的原文
    assert "要求 location ×2" not in env["situation"]       # 只带最近一次，不堆历史
    # 无失败 attempt 的节点不带重试提示
    run2 = {"runId": "run_r2", "snapshot": {"definition": {"name": "链"}},
            "nodes": {"n_sc": {"nodeId": "n_sc", "refId": "scene_designer",
                               "status": "pending", "attempts": [], "handoff": None}}}
    env2 = build_envelope(None, "p", run2, pn)
    assert "重试提示" not in env2["situation"]


def test_reconcile_spares_live_turn_in_process(tmp_path, monkeypatch):
    """Phase48 E 防御：task 仍 running 但其 turn 在本进程 turn_registry 中存活
    （live 委派）→ 对账不得判"进程死亡"停靠（真机 18:53 误判实证：活 turn
    被第二路对账误标 needs_reconciliation）。registry 未占用 → 仍诚实停靠。"""
    eng, run, calls = _start_engine(tmp_path, monkeypatch, {})
    runs = RunStore(tmp_path, "p46t")
    att = {"attemptId": "att_live", "status": "dispatching", "startedAt": 0.0,
           "endedAt": None, "taskRefs": [], "result": None, "error": None,
           "inputs": {}, "outputs": None, "skillEvidence": None, "thinking": True}

    def _seed(r):
        n = r["nodes"]["n_sw"]
        n["status"] = M.NODE_DISPATCHING
        n["attempts"].append(att)
        n["currentAttemptId"] = "att_live"

    runs.update(run["runId"], _seed)
    tasks = AgentTaskStore(tmp_path, "p46t")
    from src.agents.models import AgentTask
    t = AgentTask(task_id="task_live", project="p46t", requester_agent="workflow",
                  target_agent="script_writer", capability_id="script_writer.delegate",
                  status="running", session_id="sess_live",
                  workflow_ref={"runId": run["runId"], "nodeId": "n_sw",
                                "attemptId": "att_live"})
    tasks.create(t)
    # turn 在本进程存活（占用 registry）
    from src.agents import turn_registry
    turn_registry.acquire("p46t", "sess_live", None)
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert r["nodes"]["n_sw"]["status"] == M.NODE_DISPATCHING   # 未被停靠
    assert calls == []                                          # 也未重发
    # registry 释放后（turn 真死）→ 对账诚实 needs_reconciliation
    turn_registry.pop("p46t", "sess_live")
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert r["nodes"]["n_sw"]["status"] == M.NODE_NEEDS_RECONCILIATION


def test_drive_does_not_reopen_rejected_run(tmp_path, monkeypatch):
    """Phase48 E：驳回（rejected=终态）后路由层仍会调用 engine.drive()——缺终态守卫
    时 _drive_inner 会对 human_review 节点重开新审核，把 rejected 翻回 waiting_human
    （静默撤销用户驳回；真机 P48_E_Explainer run_545cc965eab9 22:52:21 实证）。
    修复后：终态 run 的 drive 必须为 no-op。"""
    outcomes = {"script_writer": _ok_task_result("script_writer")}
    eng, run, calls = _start_engine(tmp_path, monkeypatch, outcomes)
    runs = RunStore(tmp_path, "p46t")
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    eng.decide_review(tmp_path, "p46t", run["runId"],
                      r["reviews"][0]["requestId"], "reject", "驳回")
    r = runs.load(run["runId"])
    assert r["status"] == M.RUN_REJECTED
    n_reviews = len(r["reviews"])
    # 驳回后再次 drive（路由层真实行为）：不得重开审核/不得翻转状态
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert r["status"] == M.RUN_REJECTED
    assert len(r["reviews"]) == n_reviews          # 无新审核被创建
    assert all(x["status"] != "pending" for x in r["reviews"])
