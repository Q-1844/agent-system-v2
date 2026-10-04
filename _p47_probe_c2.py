# -*- coding: utf-8 -*-
"""
_p47_probe_c2.py — C批补验：W03（outputs.py location_views bug 修复后）+ W04（分镜图完成）。
在既有 P47_C_Templates 项目上运行（保留 W01 故事/W02 角色/W05 视频等全部真实资产）。
"""
import io
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = Path(__file__).resolve().parent
BASE = "http://localhost:8000"
PROJ = "P47_C_Templates"
OUT = ROOT / "_p47_probe_c2_results.json"
log_lines = io.StringIO()


def w(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    log_lines.write(line + "\n")


def read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def main():
    results = {"cases": []}
    with httpx.Client(timeout=120) as client:
        # ============ W03（修复后复验）============
        w("\n===== W03 场景设计（修复后复验）=====")
        r = client.post(f"{BASE}/api/project/{PROJ}/workflow/templates/W03/create",
                        json={"workflowId": f"wf_w03_r2_{uuid.uuid4().hex[:6]}", "params": {
                            "sceneObjective":
                                "恢复场景：项目里已有两个地点与三张场景视图参考图（此前 attempt 的"
                                "真实产物）。本节点：逐一核验它们满足生产要求（地点事实完整、"
                                "视图图像真实可读），并在总结中列出核验结论与 ID；"
                                "只补真实缺失的部分，不重复创建。"}})
        case = {"tpl": "W03", "createStatus": r.status_code}
        assert r.status_code == 200, r.text[:200]
        wid = r.json()["workflowId"]
        # 恢复语义：改 completion 为 allow_verified_reuse（核验既有真实产物）
        doc = client.get(f"{BASE}/api/project/{PROJ}/workflow/definitions/{wid}").json()
        for n in doc.get("nodes", []):
            if n.get("type") == "agent" and n.get("refId") == "scene_designer":
                n["config"]["completion"]["strategy"] = "allow_verified_reuse"
        rs = client.put(f"{BASE}/api/project/{PROJ}/workflow/definitions/{wid}",
                        json={"definition": doc, "expectedRevision": doc.get("revision")})
        case["reSave"] = rs.status_code
        case["workflowId"] = wid
        key = f"p47c2-{uuid.uuid4().hex[:6]}"
        rr = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/{wid}/runs",
                         json={"idempotencyKey": key})
        run_id = rr.json()["run"]["runId"]
        case["runId"] = run_id
        t0 = time.time()
        run = None
        while time.time() - t0 < 4800:
            run = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{run_id}").json()
            if run.get("status") in ("completed", "failed", "waiting_human",
                                     "needs_reconciliation"):
                break
            time.sleep(5)
        case["runStatus"] = run.get("status")
        case["errors"] = {nid: (n.get("error") or "")[:200]
                          for nid, n in run["nodes"].items() if n.get("error")}
        # 场景事实+视图图像真实核验
        ss = read_json(ROOT / "projects" / PROJ / "output" / "scene_system.json")
        res = read_json(ROOT / "projects" / PROJ / "output" / "resources.json")
        locs = ss.get("locations") or {}
        views = ss.get("views") or {}
        view_with_img = []
        for v in views.values():
            ra = (res.get("reference_assets") or {}).get(v.get("reference_asset_id")) or {}
            loc = ra.get("local")
            if loc and (ROOT / "projects" / PROJ / loc).is_file():
                view_with_img.append(v.get("view_id"))
        case["locations"] = list(locs)
        case["viewsWithReadableImages"] = view_with_img
        if case["runStatus"] == "waiting_human":
            rv = next(rv for rv in run["reviews"] if rv["status"] == "pending")
            rd = client.post(f"{BASE}/api/project/{PROJ}/workflow/runs/{run_id}"
                             f"/reviews/{rv['requestId']}/decision",
                             json={"decision": "approve", "note": "C2 补验：场景审定通过"})
            case["decision"] = rd.status_code
            t0 = time.time()
            while time.time() - t0 < 180:
                run = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{run_id}").json()
                if run.get("status") in ("completed", "failed"):
                    break
                time.sleep(3)
            case["finalStatus"] = run.get("status")
        case["result"] = ("PASS" if case.get("finalStatus") == "completed"
                          and case["locations"] and case["viewsWithReadableImages"]
                          else "FAIL")
        results["cases"].append(case)
        w(f"[{case['result']}] W03 final={case.get('finalStatus')} "
          f"locations={case['locations']} views_img={case['viewsWithReadableImages']} "
          f"errors={case['errors']}")

        # ============ W04（分镜图完成复验）============
        w("\n===== W04 分镜准备（续：为既有镜头生成分镜图）=====")
        r = client.post(f"{BASE}/api/project/{PROJ}/workflow/templates/W04/create",
                        json={"workflowId": f"wf_w04_r2_{uuid.uuid4().hex[:6]}", "params": {
                            "boardObjective":
                                "恢复场景：项目里已有 3 个镜头且分镜图已生成（此前 attempt 的真实"
                                "产物）。本节点：逐镜核验分镜图候选真实可读（shot.read 看首帧"
                                "批次与本地文件），并核对资源绑定状态；在总结中列出每镜核验"
                                "结论（shotId+候选ID+文件可读）；只补真实缺失的部分。",
                            "shotCount": "3"}})
        case = {"tpl": "W04", "createStatus": r.status_code}
        assert r.status_code == 200, r.text[:200]
        wid = r.json()["workflowId"]
        doc = client.get(f"{BASE}/api/project/{PROJ}/workflow/definitions/{wid}").json()
        for n in doc.get("nodes", []):
            if n.get("type") == "agent" and n.get("refId") == "director":
                n["config"]["completion"]["strategy"] = "allow_verified_reuse"
        rs = client.put(f"{BASE}/api/project/{PROJ}/workflow/definitions/{wid}",
                        json={"definition": doc, "expectedRevision": doc.get("revision")})
        case["reSave"] = rs.status_code
        case["workflowId"] = wid
        key = f"p47c2-{uuid.uuid4().hex[:6]}"
        rr = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/{wid}/runs",
                         json={"idempotencyKey": key})
        run_id = rr.json()["run"]["runId"]
        case["runId"] = run_id
        t0 = time.time()
        run = None
        while time.time() - t0 < 7200:
            run = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{run_id}").json()
            if run.get("status") in ("completed", "failed", "waiting_human",
                                     "needs_reconciliation"):
                break
            time.sleep(6)
        case["runStatus"] = run.get("status")
        case["errors"] = {nid: (n.get("error") or "")[:240]
                          for nid, n in run["nodes"].items() if n.get("error")}
        pj = read_json(ROOT / "projects" / PROJ / "output" / "project.json")
        frames = {}
        for sc in (pj.get("scenes") or []):
            for sh in (sc.get("shots") or []):
                ff = sh.get("first_frame") or {}
                cands = ff.get("candidates") or []
                frames[sh.get("shot_id")] = any(
                    c.get("local") and (ROOT / "projects" / PROJ / c["local"]).is_file()
                    for c in cands)
        case["shotsWithFrameFiles"] = frames
        if case["runStatus"] == "waiting_human":
            rv = next(rv for rv in run["reviews"] if rv["status"] == "pending")
            rd = client.post(f"{BASE}/api/project/{PROJ}/workflow/runs/{run_id}"
                             f"/reviews/{rv['requestId']}/decision",
                             json={"decision": "approve", "note": "C2 补验：分镜审定通过"})
            case["decision"] = rd.status_code
            t0 = time.time()
            while time.time() - t0 < 180:
                run = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{run_id}").json()
                if run.get("status") in ("completed", "failed"):
                    break
                time.sleep(3)
            case["finalStatus"] = run.get("status")
        case["result"] = ("PASS" if case.get("finalStatus") == "completed"
                          and sum(frames.values()) >= 3 else "FAIL")
        results["cases"].append(case)
        w(f"[{case['result']}] W04 final={case.get('finalStatus')} frames={frames} "
          f"errors={case['errors']}")

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "_p47_probe_c2_log.txt").write_text(log_lines.getvalue(), encoding="utf-8")
    w("[SUMMARY]", json.dumps({c["tpl"]: c.get("result") for c in results["cases"]},
                              ensure_ascii=False))


if __name__ == "__main__":
    main()
