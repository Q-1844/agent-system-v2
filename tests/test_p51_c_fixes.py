# tests/test_p51_c_fixes.py
# Phase51 C（用户裁定四修复）定向验证（零 LLM / 零生成）：
#   F1 修复范围服务端硬闸（repair_scope：授权 4 镜实际提交 20 镜实证后的代码强制）
#   F3 DNS 熔断（连续解析失败 → 冷却等待，不烧 attempts）
#   F4 启动前干跑（审核卡 0 候选/规格脱节类问题前置暴露）
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import routers.shots as RS
from src.workflow import models as M
from src.workflow.executor import WorkflowEngine, WorkflowError
from src.workflow.repair_scope import (set_scope, clear_scope, get_scope,
                                       check_submission, restore_from_run)
from src.workflow.store import RunStore

_sys_path = Path(__file__).parent
if str(_sys_path) not in sys.path:
    sys.path.insert(0, str(_sys_path))


# ============ F1：修复范围硬闸 ============
@pytest.fixture(autouse=True)
def _clean_scope():
    clear_scope("p51f")
    clear_scope("p51f2")
    yield
    clear_scope("p51f")
    clear_scope("p51f2")


def test_f1_scope_gate_blocks_out_of_list():
    set_scope("p51f", ["s01_c04", "s04_c01"], "n_dir_video", "run_x", "驳回修复")
    ok, _ = check_submission("p51f", "s01_c04")
    assert ok
    ok2, why = check_submission("p51f", "s01_c01")
    assert not ok2 and "repair_scope_excluded" in why and "s01_c04" in why
    # 无 scope 项目不受影响
    ok3, _ = check_submission("p51g_none", "s01_c01")
    assert ok3
    # 无 shot_id 的生成不适用
    ok4, _ = check_submission("p51f", None)
    assert ok4


def test_f1_approve_clears_scope(tmp_path, monkeypatch):
    """驳回→repair（scope 设置）→修复后再审 approve→scope 解除。"""
    set_scope("p51f", ["s01_c04"], "n_sw", "run_r", "驳回修复")
    assert get_scope("p51f") is not None
    # 模拟审核 approve 的清除路径（decide_review 内部调用）
    from src.workflow.repair_scope import clear_scope
    clear_scope("p51f", "run_r")
    assert get_scope("p51f") is None


def test_f1_continue_rejected_persists_scope(tmp_path, monkeypatch):
    """continue_run(rejected, allowedShotIds) → 节点 repairScope 持久化 + 注册表激活。"""
    from src.agents.runtime import AgentRuntime

    def runtime():
        return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                            settings_path=tmp_path / "agents_data" / "settings.json")

    monkeypatch.setattr("src.workflow.executor.canonical_baseline",
                        lambda rt, project: {"stub": "b"})
    monkeypatch.setattr("src.workflow.executor.canonical_baseline_rt",
                        lambda project: {"stub": "a"})
    monkeypatch.setattr("src.workflow.executor._rt", lambda: object())

    import test_p51_a_efficiency as T
    d = T._chain_sw_plain()
    d["projectId"] = "p51f"
    v = T.validate_definition(d, runtime(), "p51f")
    assert v["ok"], v["errors"]
    from src.workflow.plan import compile_plan, build_start_snapshot
    plan = compile_plan(d, v, v["catalog"])
    snap = build_start_snapshot(d, runtime(), "p51f", v, plan)
    eng = WorkflowEngine()
    calls = T._make_dispatch(monkeypatch,
                             {"script_writer": [T._ok_task_result("script_writer", "scenes", ["s01"])],
                              "assembly": [T._ok_task_result("assembly", "sequences", ["seq_1"])]})
    out = eng.start_run(tmp_path, "p51f", d, v, plan, snap, "key-f1")
    rid = out["run"]["runId"]

    def drive():
        async def _go():
            eng.drive(tmp_path, "p51f", rid)
            loop = asyncio.get_event_loop()
            import time as _t
            t0 = loop.time()
            while loop.time() - t0 < 5:
                t = eng._workers.get(rid)
                if t is None or t.done():
                    break
                await asyncio.sleep(0.02)
        asyncio.new_event_loop().run_until_complete(_go())

    drive()
    r = RunStore(tmp_path, "p51f").load(rid)
    eng.decide_review(tmp_path, "p51f", rid, r["reviews"][0]["requestId"],
                      "reject", "大副 Look 需返工")
    r = RunStore(tmp_path, "p51f").load(rid)
    assert r["status"] == M.RUN_REJECTED
    # 带范围修复
    eng.continue_run(tmp_path, "p51f", rid, allowed_shot_ids=["s01_c04", "s05_c01"])
    r = RunStore(tmp_path, "p51f").load(rid)
    assert r["nodes"]["n_sw"].get("repairScope", {}).get("allowedShotIds") == \
        ["s01_c04", "s05_c01"]
    assert get_scope("p51f") is not None
    # 范围内放行/范围外拒绝
    assert check_submission("p51f", "s01_c04")[0]
    assert not check_submission("p51f", "s01_c01")[0]
    # 审核重新打开并 approve → scope 解除
    drive()
    r = RunStore(tmp_path, "p51f").load(rid)
    assert r["status"] == M.RUN_WAITING_HUMAN
    eng.decide_review(tmp_path, "p51f", rid,
                      next(x for x in r["reviews"] if x["status"] == "pending")["requestId"],
                      "approve", "修复通过")
    assert get_scope("p51f") is None


def test_f1_restore_from_run_record():
    """后端重启恢复：run 节点 repairScope → 注册表重注册；终态 run 不恢复。"""
    run = {"runId": "run_z", "status": "running",
           "nodes": {"n_dir_video": {"status": "pending", "repairScope": {
               "allowedShotIds": ["s04_c04", "s05_c03"], "reason": "attractor"}}}}
    n = restore_from_run("p51f2", run)
    assert n == 1
    assert check_submission("p51f2", "s01_c01")[0] is False
    assert check_submission("p51f2", "s04_c04")[0] is True
    clear_scope("p51f2")
    # 终态 run 不恢复
    run["status"] = "completed"
    assert restore_from_run("p51f2", run) == 0


def test_e1_start_scope_set_and_restore():
    """Phase51 E：启动级生成范围闸——run.startScope 持久化 + 重启恢复 +
    活性 repairScope 优先。"""
    # 1) start scope（模拟 start 路由设置：进程注册表 + run 记录）
    set_scope("p51e", ["s01_c01", "s01_c02"], "n_dir_video", "run_e1",
              "start_scope")
    assert check_submission("p51e", "s01_c01")[0] is True
    ok, why = check_submission("p51e", "s03_c01")
    assert not ok and "repair_scope_excluded" in why and "start_scope" in why
    # 2) 重启恢复：run.startScope → 注册表重注册
    clear_scope("p51e")
    run = {"runId": "run_e1", "status": "running", "startScope": {
        "allowedShotIds": ["s01_c01", "s01_c02"], "nodeId": "n_dir_video",
        "reason": "start_scope"}, "nodes": {}}
    assert restore_from_run("p51e", run) == 1
    assert check_submission("p51e", "s01_c01")[0] is True
    assert check_submission("p51e", "s03_c01")[0] is False
    clear_scope("p51e")
    # 3) 活性 repairScope 优先于 startScope（修复轮更严清单胜出）
    run2 = {"runId": "run_e1", "status": "running", "startScope": {
        "allowedShotIds": ["s01_c01", "s01_c02"], "nodeId": "n_dir_video",
        "reason": "start_scope"},
        "nodes": {"n_dir_video": {"status": "pending", "repairScope": {
            "allowedShotIds": ["s04_c04"], "reason": "repair_after_reject"}}}}
    assert restore_from_run("p51e", run2) == 1
    assert check_submission("p51e", "s04_c04")[0] is True
    assert check_submission("p51e", "s01_c01")[0] is False
    clear_scope("p51e")
    # 4) 终态 run 不恢复
    run["status"] = "completed"
    assert restore_from_run("p51e", run) == 0
    assert check_submission("p51e", "s01_c01")[0] is True


def test_e1_drive_finally_clears_scope(monkeypatch):
    """worker 会话结束（含异常路径）解除 scope——不残留进程态拦截后续 run。"""
    set_scope("p51e", ["s01_c01"], "n_dir_video", "run_e2", "start_scope")
    assert get_scope("p51e") is not None
    import src.workflow.executor as EX

    class _Boom(Exception):
        pass

    engine = WorkflowEngine.__new__(WorkflowEngine)   # 不走完整 __init__
    engine._workers = {}
    async def _inner(*a, **k):
        raise _Boom("worker 崩溃")
    monkeypatch.setattr(WorkflowEngine, "_drive_inner", _inner)
    asyncio.run(engine._drive(Path("nowhere"), "p51e", "run_e2", None))
    assert get_scope("p51e") is None


# ============ F3：DNS 熔断 ============
def test_f3_dns_breaker_trips_and_resets(monkeypatch):
    RS._dns_fail_state.update({"n": 0, "last": 0.0})
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(RS.asyncio, "sleep", fake_sleep)

    async def fake_emit(stage, msg, pct):
        pass

    async def scenario():
        for i in range(RS.DNS_BREAK_THRESHOLD):
            RS._dns_classify("ConnectError: [Errno 11001] getaddrinfo failed")
        assert RS._dns_fail_state["n"] >= RS.DNS_BREAK_THRESHOLD
        await RS._dns_maybe_break(fake_emit, "s01_c01")
        assert len(sleeps) == 1 and sleeps[0] == RS.DNS_BREAK_COOLDOWN_S
        assert RS._dns_fail_state["n"] == 0   # 冷却后清零
        # 成功清零
        RS._dns_classify("getaddrinfo failed")
        RS._dns_classify("")
        assert RS._dns_fail_state["n"] == 0
        # 非解析类错误不累计
        RS._dns_classify("provider 500 boom")
        assert RS._dns_fail_state["n"] == 0

    asyncio.new_event_loop().run_until_complete(scenario())


def test_f3_dns_no_break_below_threshold(monkeypatch):
    RS._dns_fail_state.update({"n": 0, "last": 0.0})
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(RS.asyncio, "sleep", fake_sleep)

    async def fake_emit(stage, msg, pct):
        pass

    async def scenario():
        RS._dns_classify("getaddrinfo failed")
        await RS._dns_maybe_break(fake_emit, "s01_c01")
        assert sleeps == []   # 未达阈值不触发

    asyncio.new_event_loop().run_until_complete(scenario())


# ============ F4：干跑 ============
def test_f4_dry_run_reports_selection_and_specs(tmp_path):
    """干跑：完成规格现状核对 + 选用候选解析——0 候选类问题前置暴露。"""
    import test_p51_a_efficiency as T
    from src.workflow.plan import compile_plan
    from src.workflow.validate import validate_definition
    from src.agents.runtime import AgentRuntime
    from src.workflow.dry_run import dry_run

    T._make_project(tmp_path, scenes=("s01",))
    rt = AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                      settings_path=tmp_path / "agents_data" / "settings.json")
    d = T._chain_sw_spec()   # n_sw completion: scene×2 require_new；带 n_rv 审核
    d["projectId"] = "p51t"
    v = validate_definition(d, rt, "p51t")
    plan = compile_plan(d, v, v["catalog"])
    out = dry_run(tmp_path, "p51t", plan)
    by = {n["nodeId"]: n for n in out["nodes"]}
    # n_sw：项目只有 1 场景、规格要 2 → will_need_work（缺 1 项）
    assert by["n_sw"]["status"] == "will_need_work"
    # n_rv：无选用 → 普通审核门
    assert by["n_rv"]["status"] == "review_gate"


# ============ 协作流程 v2（用户 2026-10-11 定版） ============
def _v2_chain(auto=True):
    import test_p51_a_efficiency as T
    nodes = [
        T._node("nst", "boundary_start"),
        T._node("n_sw", "agent", "script_writer", objective="写故事"),
        T._node("n_rv", "human_review", label="故事审定",
                targetSourceNodeId="n_sw", reviewer="制作人", autoApprove=auto),
        T._node("n_asm", "agent", "assembly", objective="装配",
                scope={"videoVersions": [{"kind": "video_version",
                                          "entityId": "s01_c01",
                                          "versionId": "vv1"}]}),
        T._node("nen", "boundary_end"),
    ]
    edges = [T._edge("e1", "nst", "n_sw"), T._edge("e2", "n_sw", "n_rv"),
             T._edge("e3", "n_rv", "n_asm"), T._edge("e4", "n_asm", "nen")]
    return {"schemaVersion": "workflow_draft_v1", "projectId": "p51v2",
            "workflowId": "wf_p51v2", "name": "P51v2 链",
            "status": "draft", "nodes": nodes, "edges": edges}


def test_v2_auto_approve_front_card(tmp_path, monkeypatch):
    """协作流程 v2：human_review config.autoApprove=true → 卡片自动放行
    （decided approve + 节点 success + run 不进 waiting_human），事件留痕。"""
    from src.agents.runtime import AgentRuntime

    def runtime():
        return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                            settings_path=tmp_path / "agents_data" / "settings.json")
    monkeypatch.setattr("src.workflow.executor.canonical_baseline",
                        lambda rt, project: {"stub": "b"})
    monkeypatch.setattr("src.workflow.executor.canonical_baseline_rt",
                        lambda project: {"stub": "a"})
    monkeypatch.setattr("src.workflow.executor._rt", lambda: object())
    import test_p51_a_efficiency as T
    d = _v2_chain(auto=True)
    d["projectId"] = "p51v2a"
    v = T.validate_definition(d, runtime(), "p51v2a")
    assert v["ok"], v["errors"]
    from src.workflow.plan import compile_plan, build_start_snapshot
    plan = compile_plan(d, v, v["catalog"])
    assert plan["nodes"]["n_rv"]["autoApprove"] is True
    snap = build_start_snapshot(d, runtime(), "p51v2a", v, plan)
    eng = WorkflowEngine()
    T._make_dispatch(monkeypatch,
                     {"script_writer": [T._ok_task_result("script_writer", "scenes", ["s01"])],
                      "assembly": [T._ok_task_result("assembly", "sequences", ["seq_1"])]})
    out = eng.start_run(tmp_path, "p51v2a", d, v, plan, snap, "key-v2a")
    rid = out["run"]["runId"]

    async def _go():
        eng.drive(tmp_path, "p51v2a", rid)
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        while loop.time() - t0 < 5:
            t = eng._workers.get(rid)
            if t is None or t.done():
                break
            await asyncio.sleep(0.02)
    asyncio.new_event_loop().run_until_complete(_go())

    r = RunStore(tmp_path, "p51v2a").load(rid)
    rv = next(x for x in r["reviews"] if x["nodeId"] == "n_rv")
    assert rv["status"] == "decided" and rv["decision"] == "approve"
    assert rv["decisionReceipt"]["auto"] is True
    assert r["nodes"]["n_rv"]["status"] == M.NODE_SUCCESS
    # 全链推进不因 auto 卡停等（asm 已被驱动）
    assert r["nodes"]["n_asm"]["status"] in (M.NODE_SUCCESS, M.NODE_DISPATCHING)


def test_v2_repair_limit_exhausted(tmp_path, monkeypatch):
    """协作流程 v2：同一节点 3 次 reject 后 continue_run → 追加修复豁免卡
    （自动 approve）+ run 回 running + 下游复位，不再开修复 attempt。"""
    from src.agents.runtime import AgentRuntime

    def runtime():
        return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                            settings_path=tmp_path / "agents_data" / "settings.json")
    monkeypatch.setattr("src.workflow.executor.canonical_baseline",
                        lambda rt, project: {"stub": "b"})
    monkeypatch.setattr("src.workflow.executor.canonical_baseline_rt",
                        lambda project: {"stub": "a"})
    monkeypatch.setattr("src.workflow.executor._rt", lambda: object())
    import test_p51_a_efficiency as T
    d = _v2_chain(auto=False)
    d["projectId"] = "p51v2b"
    v = T.validate_definition(d, runtime(), "p51v2b")
    from src.workflow.plan import compile_plan, build_start_snapshot
    plan = compile_plan(d, v, v["catalog"])
    snap = build_start_snapshot(d, runtime(), "p51v2b", v, plan)
    eng = WorkflowEngine()
    T._make_dispatch(monkeypatch,
                     {"script_writer": [T._ok_task_result("script_writer", "scenes", ["s01"])],
                      "assembly": [T._ok_task_result("assembly", "sequences", ["seq_1"])]})
    out = eng.start_run(tmp_path, "p51v2b", d, v, plan, snap, "key-v2b")
    rid = out["run"]["runId"]

    async def _go():
        eng.drive(tmp_path, "p51v2b", rid)
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        while loop.time() - t0 < 5:
            t = eng._workers.get(rid)
            if t is None or t.done():
                break
            await asyncio.sleep(0.02)
    asyncio.new_event_loop().run_until_complete(_go())

    r = RunStore(tmp_path, "p51v2b").load(rid)
    # 显式状态机：waiting_human→reject；rejected→continue（修复或豁免）；running→drive 等待
    rejects_done = 0

    async def _drive_wait():
        eng.drive(tmp_path, "p51v2b", rid)
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        while loop.time() - t0 < 6:
            t = eng._workers.get(rid)
            if t is None or t.done():
                break
            await asyncio.sleep(0.02)

    for _ in range(10):
        r = RunStore(tmp_path, "p51v2b").load(rid)
        if any("修复豁免" in (x.get("label") or "") for x in r["reviews"]):
            break
        if r["status"] == M.RUN_REJECTED:
            eng.continue_run(tmp_path, "p51v2b", rid)
            asyncio.new_event_loop().run_until_complete(_drive_wait())
        elif r["status"] == M.RUN_WAITING_HUMAN:
            pending = next(x for x in r["reviews"] if x["status"] == "pending")
            rejects_done += 1
            eng.decide_review(tmp_path, "p51v2b", rid, pending["requestId"],
                              "reject", f"第{rejects_done}次驳回")
        else:
            asyncio.new_event_loop().run_until_complete(_drive_wait())
    assert rejects_done == 3, f"应恰好 3 次 reject 后触发豁免，实际 {rejects_done}"
    r = RunStore(tmp_path, "p51v2b").load(rid)
    exempt = [x for x in r["reviews"] if "修复豁免" in (x.get("label") or "")]
    assert exempt, "第 3 次 reject 后 continue 应触发豁免卡"
    assert exempt[0]["decision"] == "approve" and exempt[0]["decisionReceipt"]["auto"]
    assert r["status"] in (M.RUN_RUNNING, M.RUN_COMPLETED, M.RUN_WAITING_HUMAN)
    assert r["nodes"]["n_rv"]["status"] == M.NODE_SUCCESS
