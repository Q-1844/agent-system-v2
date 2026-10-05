# -*- coding: utf-8 -*-
"""_p47_probe_e_r2.py — R2 定向补演练（索引维护修复后的 turn 遗留对账）。"""
import json
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
PY = r"D:\Delta Force\python.exe"


def node(nid, ntype, ref=None, **cfg):
    return {"id": nid, "type": ntype, "refId": ref, "refVersion": None,
            "label": None, "config": cfg, "position": {"x": 0, "y": 0}}


def save_run_and_index(run):
    rid = run["runId"]
    rp = (ROOT / "projects" / PROJ / "output" / "workflows" / "runs" / f"{rid}.json")
    rp.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
    idx_path = rp.parent / "runs_index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {}
    row = idx.get(rid) or {"runId": rid}
    row["status"] = run.get("status")
    row["updatedAt"] = run.get("updatedAt")
    idx[rid] = row
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    with httpx.Client(timeout=60) as client:
        d = {"schemaVersion": "workflow_draft_v1", "projectId": PROJ,
             "workflowId": "wf_default", "name": "R2r", "status": "draft",
             "nodes": [node("nst", "boundary_start"),
                       node("n_sw", "agent", "script_writer",
                            objective="注入 R2r"),
                       node("nen", "boundary_end")],
             "edges": [{"id": "e1", "type": "execution", "source": "nst", "target": "n_sw"},
                       {"id": "e2", "type": "execution", "source": "n_sw", "target": "nen"}]}
        r = client.put(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default",
                       json={"definition": d, "expectedRevision": None})
        if r.status_code == 409:
            cur = client.get(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default").json()
            r = client.put(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default",
                           json={"definition": d, "expectedRevision": cur.get("revision")})
        assert r.status_code == 200
        key = f"p47e-r2r-{uuid.uuid4().hex[:6]}"
        run = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default/runs",
                          json={"idempotencyKey": key}).json()["run"]
        rid = run["runId"]
        client.post(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid}/stop")
        time.sleep(3)
        # 注入：AgentTask running（turn 随进程死亡）+ run running（含索引）
        task_id = "task_r2r_inject"
        tdir = ROOT / "projects" / PROJ / "output" / "agents" / "tasks"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / f"{task_id}.json").write_text(json.dumps({
            "task_id": task_id, "project": PROJ, "requester_agent": "workflow",
            "target_agent": "script_writer", "capability_id": "script_writer.delegate",
            "instruction": "注入 R2r", "scope_type": "project", "scope_id": "",
            "session_id": "", "operation": "execute", "status": "running",
            "workflow_ref": {"runId": rid, "nodeId": "n_sw",
                             "attemptId": "att_r2r"},
            "created_at": time.time() - 3600, "updated_at": time.time() - 3600,
        }, ensure_ascii=False), encoding="utf-8")
        run = json.loads((ROOT / "projects" / PROJ / "output" / "workflows" / "runs"
                          / f"{rid}.json").read_text(encoding="utf-8"))
        run["status"] = "running"
        run["stopRequested"] = False
        run["cursor"] = 0
        for nid, n in run["nodes"].items():
            if n["type"] == "agent":
                n["status"] = "running"
                n["attempts"] = [{"attemptId": "att_r2r", "status": "running",
                                  "startedAt": time.time(), "endedAt": None,
                                  "taskRefs": [task_id], "result": None,
                                  "error": None, "inputs": {}, "outputs": None,
                                  "skillEvidence": None, "thinking": True}]
                n["currentAttemptId"] = "att_r2r"
                n["error"] = None
            elif n["status"] in ("stopped", "failed"):
                n["status"] = "pending"
                n["error"] = None
        run["reviews"] = []
        save_run_and_index(run)
        print(f"[inject] run={rid} task={task_id}")

        # 杀断 + 重启
        out = subprocess.run(["netstat", "-ano"], capture_output=True)
        for line in (out.stdout or b"").decode("utf-8", errors="replace").splitlines():
            if ":8000" in line and "LISTENING" in line:
                pid = line.split()[-1]
                subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
                print(f"[kill] {pid}")
                break
        time.sleep(2)
        subprocess.run([PY, str(ROOT / "_launch_backend_detached.py")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       cwd=str(ROOT))
        for _ in range(40):
            try:
                if httpx.get(BASE + "/api/projects", timeout=3).status_code == 200:
                    print("[restart] online")
                    break
            except Exception:
                pass
            time.sleep(1.5)
        time.sleep(3)
        run = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid}").json()
        n = run["nodes"]["n_sw"]
        print("[R2r] node:", n["status"], "| err:", (n.get("error") or "")[:200])
        recon = n["status"] == "needs_reconciliation" and task_id in (n.get("error") or "")
        print("[R2r] honest_reconcile:", recon)
        # continue → 新 attempt 真实完成
        client.post(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid}/continue",
                    json={"nodeId": "n_sw"})
        t0 = time.time()
        while time.time() - t0 < 600:
            run = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid}").json()
            if run.get("status") in ("completed", "failed"):
                break
            time.sleep(4)
        atts = run["nodes"]["n_sw"]["attempts"]
        print("[R2r] after_continue:", run.get("status"),
              "| attempts:", [(a["attemptId"], a["status"]) for a in atts])
        verdict = ("PASS" if recon and run.get("status") == "completed"
                   and any(a["attemptId"] == "att_r2r" for a in atts) else
                   "PASS(recon-only)" if recon else "FAIL")
        print("[R2r VERDICT]", verdict)


if __name__ == "__main__":
    main()
