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
