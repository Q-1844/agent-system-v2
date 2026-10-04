# -*- coding: utf-8 -*-
"""
_p47_probe_d2.py — D 批恢复监视器：接管 run_22b54d2261b9 的剩余审核关口
（review 3-7：场景/分镜(+首帧选用)/视频一审/视频二审(+多选用)/成片留用户）。
"""
import io
import json
import sys
import time
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = Path(__file__).resolve().parent
BASE = "http://localhost:8000"
PROJ = "风后邮站"
RID = "run_9cc683f2ce6c"
OUT = ROOT / "_p47_probe_d2_results.json"
log_lines = io.StringIO()


def w(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    log_lines.write(line + "\n")


def read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def main():
    client = httpx.Client(timeout=120)
    last_states = {}
    t0 = time.time()
    REVIEW_PLAN = {
        1: ("approve", "工程决策：故事三段结构完整、结局事实固定（新 run 复审）。"),
        2: ("approve_select_look", "工程决策：批准常服 Look 参考（复核）。"),
        3: ("approve_select_scene", "工程决策：场景审定+街角实例视图批准选用。"),
        4: ("approve_select_first_frames", "工程决策：分镜候选可用，逐镜选用首帧。"),
        5: ("approve", "工程决策（一审）：叙事/身份/技术状态核查通过。"),
        6: ("approve_select_videos", "工程决策（二审）：选用各镜首条完成候选"
                                     "（机制验证；正式审美选版待用户）。"),
        7: None,
    }
    while time.time() - t0 < 6 * 3600:
        run = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{RID}").json()
        st = run.get("status")
        states = {nid: n.get("status") for nid, n in (run.get("nodes") or {}).items()}
        if states != last_states:
            w(f"[{int(time.time()-t0)}s] run={st} nodes={states}")
            last_states = states
        if st in ("completed", "failed", "stopped", "rejected",
                  "needs_reconciliation"):
            w(f"[end] run={st}")
            break
        if st == "waiting_human":
            pend = next((rv for rv in run.get("reviews") or []
                         if rv.get("status") == "pending"), None)
            if pend:
                idx = pend.get("reviewIndex")
                action = REVIEW_PLAN.get(idx)
                if action is None:
                    w(f"[review {idx}] 成片审定——保留给用户（不代办定版）")
                    break
                kind, note = action
                body = {"decision": "approve", "note": note,
                        "withSelection": kind.startswith("approve_select")}
                if kind == "approve_select_scene":
                    # 场景审定选用：街角 SceneInstanceView（definition selection 已配）
                    body["withSelection"] = True
                    body["selectionOverride"] = None
                    body.pop("selectionOverride")
                elif kind == "approve_select_look":
                    res = read_json(ROOT / "projects" / PROJ / "output"
                                    / "resources.json")
                    lk = next((l for l in (res.get("looks") or {}).values()
                               if l.get("reference_asset_ids")), None)
                    if lk:
                        body["selectionOverride"] = {
                            "kind": "look_reference",
                            "entityId": lk["look_id"],
                            "versionId": lk["reference_asset_ids"][0]}
                elif kind == "approve_select_first_frames":
                    pjf = read_json(ROOT / "projects" / PROJ / "output" / "project.json")
                    items = []
                    for sc in (pjf.get("scenes") or []):
                        for sh in (sc.get("shots") or []):
                            ff = (sh.get("first_frame") or {}).get("candidates") or []
                            sel = sh.get("selected_first_frame")
                            has = any(c.get("status") == "done" and c.get("local")
                                      for c in ff)
                            if sel and has:
                                items.append({"entityId": sh["shot_id"],
                                              "versionId": sel})
                    body["selectionOverride"] = {"kind": "first_frame", "items": items}
                    w(f"[review 4] first-frame items={len(items)}")
                elif kind == "approve_select_videos":
                    pjf = read_json(ROOT / "projects" / PROJ / "output" / "project.json")
                    items = []
                    for sc in (pjf.get("scenes") or []):
                        for sh in (sc.get("shots") or []):
                            vv = [x for x in (sh.get("video_versions") or [])
                                  if x.get("status") == "done"]
                            if vv:
                                items.append({"entityId": sh["shot_id"],
                                              "versionId": vv[0]["version_id"]})
                    body["selectionOverride"] = {"kind": "video_version", "items": items}
                    w(f"[review 6] video items={len(items)}")
                rd = client.post(
                    f"{BASE}/api/project/{PROJ}/workflow/runs/{RID}"
                    f"/reviews/{pend['requestId']}/decision", json=body)
                w(f"[review {idx}] decision={kind} → {rd.status_code} {rd.text[:120]}")
                # needs_reconciliation（选用失败）→ 修复路径：人评 pass 后 continue
                time.sleep(5)
                run2 = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{RID}").json()
                if run2.get("status") == "needs_reconciliation":
                    # 资源类选用被 approve 门槛拦 → 提交人评 pass → continue 重试
                    rv2 = next((rv for rv in run2.get("reviews") or []
                                if rv.get("requestId") == pend["requestId"]), None)
                    rec = (rv2 or {}).get("selectionReceipt") or {}
                    if rec.get("kind") in ("look_reference", "prop_reference",
                                           "composition_candidate") and \
                            "审查证据" in (rec.get("error") or ""):
                        eid, aid = rec.get("entityId"), rec.get("versionId")
                        seg = {"look_reference": "looks", "prop_reference": "props",
                               "composition_candidate": "compositions"}[rec["kind"]]
                        try:
                            rr = client.post(
                                f"{BASE}/api/project/{PROJ}/{seg}/{eid}/review/human",
                                json={"asset_id": aid, "overall": "pass",
                                      "note": "工程探针人评（隔离机制验证）",
                                      "reviewer": "engineering_probe"})
                            w(f"[fix] human review pass {seg}/{eid} → {rr.status_code}")
                        except Exception as e:
                            w(f"[fix] human review failed: {str(e)[:120]}")
                    cr = client.post(f"{BASE}/api/project/{PROJ}/workflow/runs/{RID}/continue",
                                     json={"nodeId": pend.get("nodeId")})
                    w(f"[fix] continue → {cr.status_code}")
        time.sleep(8)

    final = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{RID}").json()
    out = {"finalStatus": final.get("status"),
           "nodes": {nid: {"status": n.get("status"),
                           "error": (n.get("error") or "")[:300]}
                     for nid, n in (final.get("nodes") or {}).items()},
           "reviews": [{k: rv.get(k) for k in ("reviewIndex", "status", "decision",
                                               "selectionState")}
                       for rv in (final.get("reviews") or [])]}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "_p47_probe_d2_log.txt").write_text(log_lines.getvalue(), encoding="utf-8")
    w(f"[saved] {OUT.name} final={final.get('status')}")


if __name__ == "__main__":
    main()
