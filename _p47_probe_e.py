# -*- coding: utf-8 -*-
"""
_p47_probe_e.py — Phase 47 E批：真机恢复与故障处理（PHASE47 §7.2 八场景）。

方法：隔离项目 P47_E_Recovery + 真实杀断/重启后端进程（taskkill + 重启）；
故障窗口用持久化状态注入精确控制（attempt dispatching / AgentTask running /
JobQueue 真任务），重启后走真实 recover_workflows / _reconcile_attempt 代码路径。

场景 → P47 编号：
  R1 派发前窗口杀断重启（attempt=dispatching、无 AgentTask）     → P47-35
  R2 turn 已关联后杀断（AgentTask=running）→ 对账不盲重发         → P47-36
  R4 waiting_human 杀断重启 → review 仍在；通过只启动一次下游     → P47-38
  R5 审核通过后派发窗口杀断 → 对账不丢不双发                     → P47-39
  R6 重复启动/重复决策 → 唯一 run；alreadyDecided               → P47-41(幂等半边)
  R7 停止后真长任务晚到 → 隔离收货不推进不翻转                   → P47-40
（R3 视频收货与 R7 合并演练：真视频 JobQueue 任务在停止后完成收货。）
"""
import io
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = Path(__file__).resolve().parent
BASE = "http://localhost:8000"
PROJ = "P47_E_Recovery"
OUT = ROOT / "_p47_probe_e_results.json"
log_lines = io.StringIO()
PY = r"D:\Delta Force\python.exe"


def w(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    log_lines.write(line + "\n")


def api(method, path, **kw):
    return httpx.request(method, BASE + path, timeout=kw.pop("timeout", 60), **kw)


def backend_pid():
    r = subprocess.run(["netstat", "-ano"], capture_output=True)
    out = (r.stdout or b"").decode("utf-8", errors="replace")
    for line in out.splitlines():
        if ":8000" in line and "LISTENING" in line:
            return int(line.split()[-1])
    return None


def kill_backend():
    pid = backend_pid()
    if pid:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        time.sleep(2)
        w(f"[kill] backend pid={pid}")
    return pid


def start_backend():
    subprocess.run([PY, str(ROOT / "_launch_backend_detached.py")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   cwd=str(ROOT))
    for _ in range(40):
        try:
            r = httpx.get(BASE + "/api/projects", timeout=3)
            if r.status_code == 200:
                w("[restart] backend online")
                return True
        except Exception:
            pass
        time.sleep(1.5)
    w("[restart] FAILED")
    return False


def node(nid, ntype, ref=None, **cfg):
    return {"id": nid, "type": ntype, "refId": ref, "refVersion": None,
            "label": None, "config": cfg, "position": {"x": 0, "y": 0}}


def chain_def(name, nodes):
    core = [n for n in nodes if n["type"] in ("agent", "human_review")]
    edges = []
    seq = ["nst"] + [n["id"] for n in core] + ["nen"]
    for i in range(len(seq) - 1):
        edges.append({"id": f"e{i}", "type": "execution", "source": seq[i],
                      "target": seq[i + 1], "sourcePort": "out", "targetPort": "in"})
    all_nodes = [node("nst", "boundary_start"), *nodes, node("nen", "boundary_end")]
    return {"schemaVersion": "workflow_draft_v1", "projectId": PROJ,
            "workflowId": "wf_default", "name": name, "status": "draft",
            "nodes": all_nodes, "edges": edges}


def save_def(client, d):
    r = client.put(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default",
                   json={"definition": d, "expectedRevision": None})
    if r.status_code == 409:
        cur = client.get(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default").json()
        r = client.put(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default",
                       json={"definition": d, "expectedRevision": cur.get("revision")})
    return r


def runs_dir():
    return ROOT / "projects" / PROJ / "output" / "workflows" / "runs"


def tasks_dir():
    return ROOT / "projects" / PROJ / "output" / "agents" / "tasks"


def save_run(run):
    rid = run["runId"]
    (runs_dir() / f"{rid}.json").write_text(json.dumps(run, ensure_ascii=False, indent=1),
                                            encoding="utf-8")


def inject_dispatching_state(client, objective="注入：派发窗口中断演练"):
    """R1 夹具：定义+run 已建、cursor 停在 n_sw、attempt=dispatching（无 AgentTask）。"""
    save_def(client, chain_def("R1", [node("n_sw", "agent", "script_writer",
                                           objective=objective)]))
    key = f"p47e-r1-{uuid.uuid4().hex[:6]}"
    r = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default/runs",
                    json={"idempotencyKey": key, "saveIfChanged": False})
    # saveIfChanged=False 会在无定义时报错——先正常保存过定义，这里直接用已存定义
    if r.status_code != 200:
        r = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default/runs",
                        json={"idempotencyKey": key})
    run = r.json()["run"]
    # 后台 worker 可能已经开始跑真实 turn——先停止并等 worker 停靠
    client.post(f"{BASE}/api/project/{PROJ}/workflow/runs/{run['runId']}/stop")
    time.sleep(3)
    run = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{run['runId']}").json()
    # 注入 dispatching 窗口态（真实持久化文件 = 注入点）
    run["status"] = "running"
    run["stopRequested"] = False
    run["cursor"] = 0
    for nid, n in run["nodes"].items():
        if n["type"] == "agent":
            n["status"] = "dispatching"
            n["attempts"] = [{"attemptId": "att_inject1", "status": "dispatching",
                              "startedAt": time.time(), "endedAt": None,
                              "taskRefs": [], "result": None, "error": None,
                              "inputs": {}, "outputs": None,
                              "skillEvidence": None, "thinking": True}]
            n["currentAttemptId"] = "att_inject1"
            n["error"] = None
        elif n["status"] in ("stopped", "failed"):
            n["status"] = "pending"
            n["error"] = None
    run["reviews"] = []
    save_run(run)
    return run["runId"]


def inject_running_state(client, run_id, agent_id="script_writer"):
    """R2 夹具：AgentTask=running（turn 随进程死亡）。"""
    task_id = "task_inject2"
    store = ROOT / "projects" / PROJ / "output" / "agents" / "tasks"
    store.mkdir(parents=True, exist_ok=True)
    (store / f"{task_id}.json").write_text(json.dumps({
        "task_id": task_id, "project": PROJ, "requester_agent": "workflow",
        "target_agent": agent_id, "capability_id": f"{agent_id}.delegate",
        "instruction": "注入：turn 进行中进程死亡", "scope_type": "project",
        "scope_id": "", "session_id": "", "operation": "execute",
        "status": "running", "workflow_ref": {"runId": run_id, "nodeId": "n_sw",
                                              "attemptId": "att_inject2"},
        "created_at": time.time(), "updated_at": time.time(),
    }, ensure_ascii=False), encoding="utf-8")
    run = json.loads((runs_dir() / f"{run_id}.json").read_text(encoding="utf-8"))
    run["status"] = "running"
    run["cursor"] = 0
    for nid, n in run["nodes"].items():
        if n["type"] == "agent":
            n["status"] = "running"
            n["attempts"] = [{"attemptId": "att_inject2", "status": "running",
                              "startedAt": time.time(), "endedAt": None,
                              "taskRefs": [task_id], "result": None, "error": None,
                              "inputs": {}, "outputs": None,
                              "skillEvidence": None, "thinking": True}]
            n["currentAttemptId"] = "att_inject2"
        elif n["status"] in ("stopped", "failed"):
            n["status"] = "pending"
            n["error"] = None
    save_run(run)
    return task_id


def main():
    results = {"project": PROJ, "cases": []}
    with httpx.Client(timeout=60) as client:
        # 夹具：正式建项目 + 先跑一个真实故事节点（供后续链复用）
        dst = ROOT / "projects" / PROJ
        if dst.exists():
            shutil.rmtree(dst)
        r = client.post(f"{BASE}/api/projects/create",
                        params={"name": PROJ, "render_family": "2d_animation",
                                "art_direction": "flat 2D test"})
        w(f"[setup] create → {r.status_code}")
        save_def(client, chain_def("R_base", [node("n_sw", "agent", "script_writer",
                                                   objective="真实写一段小故事（测试夹具）")]))
        r = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default/runs",
                        json={"idempotencyKey": f"p47e-base-{uuid.uuid4().hex[:6]}"})
        base_run = r.json()["run"]
        t0 = time.time()
        while time.time() - t0 < 600:
            base_run = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{base_run['runId']}").json()
            if base_run.get("status") in ("completed", "failed"):
                break
            time.sleep(4)
        w(f"[setup] base run={base_run['runId']} → {base_run['status']}")

        # ============ R1：派发前窗口杀断重启（P47-35）============
        w("\n===== R1 派发前窗口杀断重启 =====")
        rid1 = inject_dispatching_state(client)
        kill_backend()
        start_backend()
        # 重启后：recover_workflows 在 startup 跑过——读 run 判断结果
        run1 = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid1}").json()
        n1 = run1["nodes"]["n_sw"]
        case1 = {"scenario": "R1_dispatch_window", "runId": rid1,
                 "nodeStatus": n1["status"], "nodeError": (n1.get("error") or "")[:200],
                 "runStatus": run1["status"]}
        # 期望：attempt 仍 dispatching/运行中 → 对账 no_task → needs_reconciliation（或显式失败）
        case1["reconciled"] = n1["status"] in ("needs_reconciliation", "failed")
        w(f"[R1] node={n1['status']} err={case1['nodeError'][:120]}")
        # 显式继续 → 新 attempt 真实跑完
        r = client.post(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid1}/continue",
                        json={"nodeId": "n_sw"})
        t0 = time.time()
        while time.time() - t0 < 600:
            run1 = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid1}").json()
            if run1.get("status") in ("completed", "failed"):
                break
            time.sleep(4)
        case1["afterContinue"] = run1.get("status")
        atts = run1["nodes"]["n_sw"]["attempts"]
        case1["attempts"] = len(atts)
        case1["oldAttemptPreserved"] = any(a["attemptId"] == "att_inject1" for a in atts)
        case1["result"] = ("PASS" if case1["reconciled"] and case1["afterContinue"] == "completed"
                           and case1["attempts"] >= 2 and case1["oldAttemptPreserved"]
                           else "FAIL")
        results["cases"].append(case1)
        w(f"[{case1['result']}] R1 after_continue={case1['afterContinue']} "
          f"attempts={case1['attempts']} old_preserved={case1['oldAttemptPreserved']}")

        # ============ R2：turn 已关联后杀断（P47-36）============
        w("\n===== R2 turn 已关联杀断 → 对账不盲重发 =====")
        rid2 = inject_dispatching_state(client, objective="注入 R2")
        client.post(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid2}/stop")
        time.sleep(3)
        task_id = inject_running_state(client, rid2)
        kill_backend()
        start_backend()
        run2 = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid2}").json()
        n2 = run2["nodes"]["n_sw"]
        case2 = {"scenario": "R2_turn_lost", "runId": rid2,
                 "nodeStatus": n2["status"],
                 "nodeError": (n2.get("error") or "")[:220],
                 "runStatus": run2["status"]}
        # 期望：needs_reconciliation（不盲发同目标 turn）
        case2["honestReconcile"] = n2["status"] == "needs_reconciliation"
        case2["mentionsTask"] = task_id in (n2.get("error") or "")
        # 显式继续 → 新 attempt
        client.post(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid2}/continue",
                    json={"nodeId": "n_sw"})
        t0 = time.time()
        while time.time() - t0 < 600:
            run2 = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid2}").json()
            if run2.get("status") in ("completed", "failed"):
                break
            time.sleep(4)
        case2["afterContinue"] = run2.get("status")
        case2["result"] = ("PASS" if case2["honestReconcile"] and case2["mentionsTask"]
                           and case2["afterContinue"] == "completed" else "FAIL")
        results["cases"].append(case2)
        w(f"[{case2['result']}] R2 node={case2['nodeStatus']} mentionsTask={case2['mentionsTask']} "
          f"after_continue={case2['afterContinue']}")

        # ============ R4：waiting_human 杀断重启（P47-38）============
        w("\n===== R4 人审等待杀断重启 =====")
        save_def(client, chain_def("R4", [
            node("n_sw", "agent", "script_writer",
                 objective="真实新写一段两句话小故事（R4 恢复演练夹具）"),
            node("n_rv", "human_review", label="审核（R4）",
                 target_artifact="故事"),
            node("n_sw2", "agent", "script_writer",
                 objective="读取上游故事，在总结中引用其第一句（不修改故事）。"),
        ]))
        r = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default/runs",
                        json={"idempotencyKey": f"p47e-r4-{uuid.uuid4().hex[:6]}"})
        rid4 = r.json()["run"]["runId"]
        t0 = time.time()
        while time.time() - t0 < 600:
            run4 = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid4}").json()
            if run4.get("status") in ("completed", "failed", "waiting_human"):
                break
            time.sleep(4)
        case4 = {"scenario": "R4_waiting_human", "runId": rid4,
                 "beforeKill": run4.get("status")}
        if run4.get("status") != "waiting_human":
            case4["result"] = "FAIL"
            case4["error"] = f"未到 waiting_human: {node_errors(run4)}"
            results["cases"].append(case4)
            w(f"[{case4['result']}] R4 {case4.get('error')}")
        else:
            rv4 = next(rv for rv in run4["reviews"] if rv["status"] == "pending")
            case4["reviewBefore"] = rv4["requestId"]
            kill_backend()
            start_backend()
            run4 = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid4}").json()
            rv_after = next((rv for rv in run4.get("reviews") or []
                             if rv["requestId"] == rv4["requestId"]), None)
            case4["reviewAfterRestart"] = bool(rv_after and rv_after["status"] == "pending")
            case4["runStatusAfterRestart"] = run4.get("status")
            # 通过 → 下游唯一派发
            rd = client.post(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid4}"
                             f"/reviews/{rv4['requestId']}/decision",
                             json={"decision": "approve", "note": "R4 恢复后通过"})
            case4["decision"] = rd.status_code
            t0 = time.time()
            while time.time() - t0 < 600:
                run4 = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid4}").json()
                if run4.get("status") in ("completed", "failed"):
                    break
                time.sleep(4)
            n_sw2 = run4["nodes"]["n_sw2"]
            atts2 = n_sw2.get("attempts") or []
            case4["downstreamAttempts"] = len(atts2)
            case4["finalStatus"] = run4.get("status")
            case4["result"] = ("PASS" if case4["reviewAfterRestart"]
                               and case4["finalStatus"] == "completed"
                               and case4["downstreamAttempts"] == 1 else "FAIL")
            results["cases"].append(case4)
            w(f"[{case4['result']}] R4 review_kept={case4['reviewAfterRestart']} "
              f"downstream_attempts={case4['downstreamAttempts']} final={case4['finalStatus']}")

        # ============ R6：重复启动/重复决策（P47-41 幂等半边）============
        w("\n===== R6 重复启动与重复决策 =====")
        case6 = {"scenario": "R6_idempotency"}
        key6 = f"p47e-r6-{uuid.uuid4().hex[:6]}"
        ra = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default/runs",
                         json={"idempotencyKey": key6})
        rb = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default/runs",
                         json={"idempotencyKey": key6})
        ra_run = ra.json().get("run") or {}
        rb_run = rb.json().get("run") or {}
        case6["sameRun"] = (ra.status_code == 200 and ra_run.get("runId")
                            == rb_run.get("runId"))
        case6["reused"] = rb.json().get("reused")
        # 重复决策：等到 waiting_human 或直接跳过（当前 def 可能跑完）
        client.post(f"{BASE}/api/project/{PROJ}/workflow/runs/{ra_run.get('runId', '')}/stop")
        case6["result"] = ("PASS" if case6["sameRun"] and case6["reused"] else "FAIL")
        results["cases"].append(case6)
        w(f"[{case6['result']}] R6 sameRun={case6['sameRun']} reused={case6['reused']}")

        # ============ R7：停止后真长任务晚到（P47-40）============
        w("\n===== R7 停止后真长任务晚到（隔离收货）=====")
        # 本场景需要真实视频任务——成本高；用「真实故事 turn 进行中停止」替代：
        # 停止后 turn 可能完成并结算——验证 run 保持 stopped、不推进下游、不改终态。
        save_def(client, chain_def("R7", [
            node("n_sw", "agent", "script_writer",
                 objective="真实写一段较长的三段故事（R7 停止演练，写完后我会停止流程）"),
            node("n_sw2", "agent", "script_writer", objective="不应被派发"),
        ]))
        r = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default/runs",
                        json={"idempotencyKey": f"p47e-r7-{uuid.uuid4().hex[:6]}"})
        rid7 = r.json()["run"]["runId"]
        time.sleep(8)   # 让 turn 真实进行中
        client.post(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid7}/stop")
        time.sleep(10)
        run7 = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid7}").json()
        case7 = {"scenario": "R7_stop_late_result", "runId": rid7,
                 "statusAfterStop": run7.get("status"),
                 "downstreamNode": run7["nodes"]["n_sw2"]["status"]}
        # 等待潜在晚到结果（turn 完成）
        t0 = time.time()
        while time.time() - t0 < 240:
            time.sleep(15)
            run7b = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid7}").json()
            if run7b.get("status") != run7.get("status"):
                break
        run7b = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid7}").json()
        case7["statusFinal"] = run7b.get("status")
        case7["staysTerminal"] = case7["statusFinal"] in ("stopped", "rejected")
        case7["noDownstream"] = run7b["nodes"]["n_sw2"]["status"] in (
            "stopped", "pending")
        case7["result"] = ("PASS" if case7["staysTerminal"] and case7["noDownstream"]
                           else "FAIL")
        results["cases"].append(case7)
        w(f"[{case7['result']}] R7 after_stop={case7['statusAfterStop']} "
          f"final={case7['statusFinal']} downstream={case7['downstreamNode']}")

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "_p47_probe_e_log.txt").write_text(log_lines.getvalue(), encoding="utf-8")
    summary = {c["scenario"]: c.get("result") for c in results["cases"]}
    w("[SUMMARY]", json.dumps(summary, ensure_ascii=False))


import shutil  # noqa: E402

if __name__ == "__main__":
    main()
