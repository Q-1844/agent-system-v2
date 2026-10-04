# -*- coding: utf-8 -*-
"""
_p47_probe_c3.py — C批补验（三）：W06 诊断修复 / W07 端点桥接 / W08 装配。

夹具（全部来自 P47_C_Templates 的真实产物）：
  W06: s01_c01 视频 vv_2855ff25 —— W05 运行中 visual.sample_frames 检出
       suspected=1（beginning→early_middle 纸张过渡压缩）= 真实已知问题输入。
  W07: s01_c02（邮站内出门）→ s02_c01（小桥交信）跨地点连续过渡 =
       合法桥接场景（两个端点各自合格：分镜图真实生成）。
  W08: s01_c01 已选用版本 vv_2855ff25（final_video 指针真实存在）= 显式复用输入。
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
OUT = ROOT / "_p47_probe_c3_results.json"
log_lines = io.StringIO()


def w(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    log_lines.write(line + "\n")


def read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def tpl_run(client, tid, params, label, poll_timeout=7200, completion_patch=None,
            decision_note="C3 探针：审定通过", selection_fn=None):
    case = {"tpl": tid, "label": label}
    r = client.post(f"{BASE}/api/project/{PROJ}/workflow/templates/{tid}/create",
                    json={"workflowId": f"wf_{tid.lower()}_{uuid.uuid4().hex[:6]}",
                          "params": params})
    case["createStatus"] = r.status_code
    if r.status_code != 200:
        case["result"] = "FAIL"
        case["error"] = r.text[:250]
        return case
    wid = r.json()["workflowId"]
    case["workflowId"] = wid
    if completion_patch:
        doc = client.get(f"{BASE}/api/project/{PROJ}/workflow/definitions/{wid}").json()
        for n in doc.get("nodes", []):
            if n.get("type") == "agent" and completion_patch.get("refId") == n.get("refId"):
                n["config"]["completion"] = completion_patch["completion"]
        rs = client.put(f"{BASE}/api/project/{PROJ}/workflow/definitions/{wid}",
                        json={"definition": doc, "expectedRevision": doc.get("revision")})
        case["reSave"] = rs.status_code
    rr = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/{wid}/runs",
                     json={"idempotencyKey": f"p47c3-{uuid.uuid4().hex[:6]}"})
    if rr.status_code != 200:
        case["result"] = "FAIL"
        case["error"] = f"start: {rr.text[:200]}"
        return case
    run_id = rr.json()["run"]["runId"]
    case["runId"] = run_id
    t0 = time.time()
    run = None
    while time.time() - t0 < poll_timeout:
        run = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{run_id}").json()
        st = run.get("status")
        if st in ("completed", "failed", "waiting_human", "needs_reconciliation"):
            break
        time.sleep(6)
    case["runStatus"] = run.get("status")
    case["errors"] = {nid: (n.get("error") or "")[:240]
                      for nid, n in run["nodes"].items() if n.get("error")}
    if case["runStatus"] == "waiting_human":
        rv = next((rv for rv in run.get("reviews") or [] if rv["status"] == "pending"),
                  None)
        body = {"decision": "approve", "note": decision_note}
        if selection_fn:
            ov = selection_fn()
            if ov:
                body["withSelection"] = True
                body["selectionOverride"] = ov
        rd = client.post(f"{BASE}/api/project/{PROJ}/workflow/runs/{run_id}"
                         f"/reviews/{rv['requestId']}/decision", json=body)
        case["decision"] = rd.status_code
        t0 = time.time()
        while time.time() - t0 < 300:
            run = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{run_id}").json()
            if run.get("status") in ("completed", "failed", "needs_reconciliation"):
                break
            time.sleep(4)
        case["finalStatus"] = run.get("status")
        rv2 = next((rv for rv in run.get("reviews") or []
                    if rv["requestId"] == rv["requestId"]), None) if run.get("reviews") else None
    case["finalStatus"] = case.get("finalStatus") or run.get("status")
    case["result"] = ("PASS" if case["finalStatus"] == "completed" else "FAIL")
    return case


def main():
    results = {"cases": []}
    with httpx.Client(timeout=120) as client:
        pj = read_json(ROOT / "projects" / PROJ / "output" / "project.json")
        vv_map = {}
        for sc in (pj.get("scenes") or []):
            for sh in (sc.get("shots") or []):
                for v in (sh.get("video_versions") or []):
                    if v.get("status") == "done":
                        vv_map[sh["shot_id"]] = v["version_id"]
        w(f"[pre] done videos: {vv_map}")

        # ============ W06 诊断修复（真实疑似问题输入）============
        w("\n===== W06 导演诊断与修复（s01_c01 纸张过渡疑似问题）=====")
        c = tpl_run(client, "W06", {
            "fixObjective":
                "已知问题：镜头 s01_c01 的视频候选 vv_2855ff25 在 W05 生成时的"
                " visual.sample_frames 检出 suspected=1（beginning→early_middle 纸张过渡"
                "压缩）。本节点：①读取该候选与 trace 判断第一处偏离（视频过程/根帧/"
                "资源绑定）；②保留合格部分，做当前工具支持的局部处理或重生成；"
                "③交付新修复候选并给出前后对比说明；④原版本保留，不改变其 selected 指针。",
        }, "W06 修复 s01_c01",
            completion_patch={"refId": "director",
                              "completion": {"strategy": "allow_verified_reuse",
                                             "outputs": [{"kind": "video_version",
                                                          "playable": True,
                                                          "minCount": 2}]}})
        # 修复后项目里该镜应有 ≥2 个视频版本（原版+新候选）
        pj2 = read_json(ROOT / "projects" / PROJ / "output" / "project.json")
        n_versions = {}
        for sc in (pj2.get("scenes") or []):
            for sh in (sc.get("shots") or []):
                n_versions[sh["shot_id"]] = len(sh.get("video_versions") or [])
        c["videoVersionsAfter"] = n_versions
        c["result"] = ("PASS" if c["result"] == "PASS"
                       and n_versions.get("s01_c01", 0) >= 2 else "FAIL")
        results["cases"].append(c)
        w(f"[{c['result']}] W06 final={c.get('finalStatus')} versions={n_versions} "
          f"errors={c.get('errors')}")

        # ============ W07 有效端点桥接（s01_c02 → s02_c01 跨地点过渡）============
        w("\n===== W07 有效端点桥接 =====")
        c = tpl_run(client, "W07", {
            "bridgeObjective":
                "桥接场景：s01_c02（邮站内，提袋走到门口）与 s02_c01（门外小桥，走向朋友）"
                "两个端点各自合格需要保留，但两镜之间需要「开门出门走到桥上」的连续过渡"
                "（业务上必须连续，非硬切）。按 keyframes 接续桥 Skill 的方法："
                "双端证据核实 → shot.bridge_create 插桥梁镜 → 等待桥视频真实生成 → "
                "双侧 seam 验收与帧检 → trace 落档。桥接候选不自动加入任何 Sequence。",
        }, "W07 桥接 s01_c02→s02_c01",
            completion_patch={"refId": "director",
                              "completion": {"strategy": "allow_verified_reuse",
                                             "outputs": [{"kind": "video_version",
                                                          "playable": True,
                                                          "minCount": 1}]}})
        results["cases"].append(c)
        w(f"[{c['result']}] W07 final={c.get('finalStatus')} errors={c.get('errors')}")

        # ============ W08 现有视频装配（显式复用已选版本）============
        w("\n===== W08 现有视频装配（显式 videoVersions 声明）=====")
        sel_vv = vv_map.get("s01_c01")
        params = {
            "assemblyObjective":
                "装配测试 Sequence《C3 装配夹具》：只包含镜头 s01_c01 的已选用版本"
                "（服务端会核对采用的版本）。无声拼接（不加旁白不加音乐，音轨静音），"
                "完成渲染并核验输出文件可读。",
            "videoVersions": json.dumps([{"shotId": "s01_c01",
                                          "versionId": sel_vv}]) if sel_vv else None,
        }
        if not params["videoVersions"]:
            del params["videoVersions"]
        c = tpl_run(client, "W08", params, "W08 装配夹具",
                    completion_patch={"refId": "assembly",
                                      "completion": {"strategy": "require_new",
                                                     "outputs": [{"kind": "sequence",
                                                                  "withCompletedRender": True,
                                                                  "minCount": 1}]}})
        # 真实成片核验
        seqs = read_json(ROOT / "projects" / PROJ / "output" / "sequences.json")
        seq_facts = []
        for sid, e in (seqs.get("sequences") or {}).items():
            for rv in (e.get("render_versions") or []):
                of = rv.get("output_file")
                ok = bool(of and (ROOT / of).is_file()) if of else False
                seq_facts.append({"sequence": sid, "render": rv.get("version_id"),
                                  "status": rv.get("status"), "file": ok,
                                  "path": of})
        c["sequenceFacts"] = seq_facts[-3:]
        c["result"] = ("PASS" if c["result"] == "PASS"
                       and any(f["file"] and f["status"] == "done"
                               for f in seq_facts) else "FAIL")
        results["cases"].append(c)
        w(f"[{c['result']}] W08 final={c.get('finalStatus')} "
          f"seqs={json.dumps(c.get('sequenceFacts'), ensure_ascii=False)[:300]}")

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "_p47_probe_c3_log.txt").write_text(log_lines.getvalue(), encoding="utf-8")
    w("[SUMMARY]", json.dumps({c["tpl"]: c.get("result") for c in results["cases"]},
                              ensure_ascii=False))


if __name__ == "__main__":
    main()
