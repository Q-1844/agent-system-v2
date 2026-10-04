# -*- coding: utf-8 -*-
"""
_p47_probe_b2.py — Phase 47 B批补验：S1（审核批准选用，async 修复后）+ S2（新造型版本化）。

前置：_p47_probe_b.py 已产出角色三视图与 Look 参考图（S1 图像部分 PASS）。
本探针在既有 P47_B_Probe 项目上补完：
  S1b 审核「通过并选用」Look 参考 → approved_reference_asset_ids 真实落盘
  S2b 已批准 Look 的新造型候选（require_new ×1；原版保留核验）
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
PROJ = "P47_B_Probe"
OUT = ROOT / "_p47_probe_b2_results.json"
log_lines = io.StringIO()


def w(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    log_lines.write(line + "\n")


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


def read_resources():
    p = ROOT / "projects" / PROJ / "output" / "resources.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def main():
    results = {"cases": []}
    with httpx.Client(timeout=120) as client:
        res = read_resources()
        looks = res.get("looks") or {}
        look_id = next(iter(looks), None)
        asset_id = ((looks.get(look_id) or {}).get("reference_asset_ids") or [None])[0]
        w(f"[pre] looks={list(looks)} look_id={look_id} asset={asset_id}")
        assert look_id and asset_id, "前置 Look 不存在——先跑 _p47_probe_b.py"

        # ============ S1b 审核「通过并选用」Look 参考 ============
        w("\n===== S1b 审核通过并选用（Look 参考；async 修复后）=====")
        d1 = chain_def("S1b 审核选用", [
            node("n_dummy", "agent", "script_writer",
                 objective="读取项目里的既有两段故事并在总结中确认其完整（不修改任何内容）。",
                 completion={"strategy": "allow_verified_reuse",
                             "outputs": [{"kind": "scene", "minCount": 2}]}),
            node("n_rv1", "human_review", label="资源审定（批准 Look 参考）",
                 instructions="批准红狐信使常服 Look 的参考图。",
                 selection={"kind": "look_reference", "entityId": look_id,
                            "versionId": asset_id}),
        ])
        r = save_def(client, d1)
        assert r.status_code == 200, r.text[:300]
        r = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default/runs",
                        json={"idempotencyKey": f"p47b2-{uuid.uuid4().hex[:6]}"})
        rid = r.json()["run"]["runId"]
        t0 = time.time()
        run = None
        while time.time() - t0 < 600:
            run = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid}").json()
            if run.get("status") in ("completed", "failed", "waiting_human"):
                break
            time.sleep(4)
        case = {"name": "S1b_approve_select", "runId": rid,
                "waitStatus": run.get("status")}
        w(f"[wait] status={run.get('status')} errors="
          f"{ {nid: (n.get('error') or '')[:120] for nid, n in run['nodes'].items() if n.get('error')} }")
        if run.get("status") == "waiting_human":
            rv = next(rv for rv in run["reviews"] if rv["status"] == "pending")
            rd = client.post(
                f"{BASE}/api/project/{PROJ}/workflow/runs/{rid}"
                f"/reviews/{rv['requestId']}/decision",
                json={"decision": "approve", "note": "工程探针批准（async 修复后）",
                      "withSelection": True,
                      "selectionOverride": {"kind": "look_reference",
                                            "entityId": look_id,
                                            "versionId": asset_id}})
            case["decisionStatus"] = rd.status_code
            t0 = time.time()
            while time.time() - t0 < 120:
                run = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid}").json()
                if run.get("status") in ("completed", "failed", "needs_reconciliation"):
                    break
                time.sleep(3)
            rv2 = (run.get("reviews") or [{}])[0]
            case["finalStatus"] = run.get("status")
            case["selectionState"] = rv2.get("selectionState")
            case["selectionReceipt"] = rv2.get("selectionReceipt")
            res2 = read_resources()
            lk2 = (res2.get("looks") or {}).get(look_id) or {}
            case["approvedAfter"] = lk2.get("approved_reference_asset_ids")
            case["result"] = ("PASS" if case["finalStatus"] == "completed"
                              and case["selectionState"] == "done"
                              and asset_id in (case["approvedAfter"] or []) else "FAIL")
        else:
            case["result"] = "FAIL"
        results["cases"].append(case)
        w(f"[{case['result']}] S1b final={case.get('finalStatus')} "
          f"sel={case.get('selectionState')} approved={case.get('approvedAfter')}")

        # ============ S2b 已批准 Look 的新造型（require_new ×1，原版保留）============
        w("\n===== S2b 已批准 Look 的新造型候选 =====")
        read_resources_before = read_resources()
        n_before = len(read_resources_before.get("looks") or {})
        d2 = chain_def("S2b 新造型", [
            node("n_cd", "agent", "character_designer",
                 objective=f"项目里角色「红狐信使」已有多个 Look（含 look_id={look_id}）。"
                           "本节点：为同一角色创建一个新造型 Look「雪天信使」"
                           "（appearance_description: same red fox courier wearing a thick "
                           "scarf and snow-dusted cape, light snowfall）并真实生成参考图。"
                           "严禁修改或删除既有 Look 及其参考——只新增。",
                 completion={"strategy": "require_new",
                             "outputs": [{"kind": "look", "withImage": True,
                                          "minCount": 1}]}),
        ])
        r = save_def(client, d2)
        assert r.status_code == 200, r.text[:300]
        r = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default/runs",
                        json={"idempotencyKey": f"p47b2-{uuid.uuid4().hex[:6]}"})
        rid2 = r.json()["run"]["runId"]
        t0 = time.time()
        while time.time() - t0 < 3600:
            run2 = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid2}").json()
            if run2.get("status") in ("completed", "failed", "waiting_human",
                                      "needs_reconciliation"):
                break
            time.sleep(5)
        res3 = read_resources()
        looks3 = res3.get("looks") or {}
        case2 = {"name": "S2b_new_look_variant", "runId": rid2,
                 "runStatus": run2.get("status"),
                 "nLooksBefore": n_before, "nLooksAfter": len(looks3),
                 "errors": {nid: (n.get("error") or "")[:200]
                            for nid, n in run2["nodes"].items() if n.get("error")}}
        # 原版保留核验：approved 指针未被破坏
        lk_orig = looks3.get(look_id) or {}
        refs_before = ((read_resources_before.get("looks") or {}).get(look_id) or {}).get(
            "reference_asset_ids")
        case2["originalApprovedIntact"] = (
            lk_orig.get("reference_asset_ids") == refs_before and bool(refs_before))
        new_looks = [l for l in looks3 if l != look_id]
        case2["newLooksWithFiles"] = []
        for l in new_looks:
            for aid in (looks3[l].get("reference_asset_ids") or []):
                a = (res3.get("reference_assets") or {}).get(aid) or {}
                loc = a.get("local")
                if loc and (ROOT / "projects" / PROJ / loc).is_file():
                    case2["newLooksWithFiles"].append(l)
        case2["result"] = ("PASS" if run2.get("status") == "completed"
                           and case2["nLooksAfter"] > case2["nLooksBefore"]
                           and case2["originalApprovedIntact"]
                           and case2["newLooksWithFiles"] else "FAIL")
        results["cases"].append(case2)
        w(f"[{case2['result']}] S2b looks {n_before}→{len(looks3)} "
          f"orig_intact={case2['originalApprovedIntact']} "
          f"new_with_files={case2['newLooksWithFiles']} errors={case2['errors']}")

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "_p47_probe_b2_log.txt").write_text(log_lines.getvalue(), encoding="utf-8")
    w("[SUMMARY]", json.dumps({c["name"]: c.get("result") for c in results["cases"]},
                              ensure_ascii=False))


if __name__ == "__main__":
    main()
