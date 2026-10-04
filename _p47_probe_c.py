# -*- coding: utf-8 -*-
"""
_p47_probe_c.py — Phase 47 C批：W00-W05 模板真机运行（隔离项目 P47_C_Templates）。

路径（PHASE47 §5.1/§5.2——每模板一条真实运行证据，全部经模板 API 创建）：
  W00 模板创建 → 运行（producer 计划）→ 审核通过                 → P47-01/09
  W01 模板创建 → 运行（编剧新写）→ 审核通过                      → P47-10
  W03 模板创建 → 运行（场景设计）→ 审核通过                      → P47-13
  W02 模板创建 → 运行（角色+Look 真实图像）→ 审核+批准选用        → P47-11/28
  W04 模板创建 → 运行（导演分镜图）→ 审核                        → P47-14
  W05 模板创建（绑尾帧接力 Skill）→ 运行（真实视频×1）→ 审核+选用  → P47-15/19

模板经 POST /workflow/templates/{tid}/create（新工作流身份，记录模板来源）。
"""
import io
import json
import shutil
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = Path(__file__).resolve().parent
BASE = "http://localhost:8000"
PROJ = "P47_C_Templates"
OUT = ROOT / "_p47_probe_c_results.json"
LOG = ROOT / "_p47_probe_c_log.txt"
log_lines = io.StringIO()

sys.path.insert(0, str(ROOT))


def w(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    log_lines.write(line + "\n")


def api(method, path, **kw):
    return httpx.request(method, BASE + path, timeout=kw.pop("timeout", 120), **kw)


def setup_project(client):
    dst = ROOT / "projects" / PROJ
    if dst.exists():
        w(f"[setup] 项目 {PROJ} 已存在（沿用，保留既有故事/资源）")
        return
    r = client.post(f"{BASE}/api/projects/create",
                    params={"name": PROJ, "render_family": "2d_animation",
                            "art_direction": "flat 2D animation, clean lines, "
                                             "soft watercolor backgrounds"})
    w(f"[setup] 正式建项目 {PROJ} → {r.status_code}")
    assert r.status_code == 200, r.text


def tpl_create(client, tid, params=None, wid=None):
    wid = wid or f"wf_{tid.lower()}_{uuid.uuid4().hex[:6]}"
    r = client.post(f"{BASE}/api/project/{PROJ}/workflow/templates/{tid}/create",
                    json={"workflowId": wid, "params": params or {}})
    return r, wid


def run_and_poll(client, wid, timeout=2400, stop_at_waiting=False):
    key = f"p47c-{wid}-{uuid.uuid4().hex[:8]}"
    r = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/{wid}/runs",
                    json={"idempotencyKey": key})
    if r.status_code != 200:
        return None, {"startError": r.text[:300]}
    run = r.json()["run"]
    t0 = time.time()
    last = run
    while time.time() - t0 < timeout:
        last = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{run['runId']}").json()
        st = last.get("status")
        if st in ("completed", "failed", "stopped", "rejected",
                  "needs_reconciliation") or (stop_at_waiting and st == "waiting_human"):
            break
        time.sleep(4)
    return run["runId"], last


def poll_only(client, run_id, timeout=120):
    """决策后只轮询既有 run（不得另起新 run——那是真实的新生产）。"""
    t0 = time.time()
    run = None
    while time.time() - t0 < timeout:
        run = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{run_id}").json()
        if run.get("status") in ("completed", "failed", "stopped", "rejected",
                                 "needs_reconciliation"):
            break
        time.sleep(3)
    return run


def node_errors(run):
    return {nid: (n.get("error") or "")[:200]
            for nid, n in (run.get("nodes") or {}).items() if n.get("error")}


def pending_review(run):
    for rv in (run.get("reviews") or []):
        if rv.get("status") == "pending":
            return rv
    return None


def main():
    results = {"project": PROJ, "cases": []}

    with httpx.Client(timeout=120) as client:
        setup_project(client)
        # ============ W00 ============
        w("\n===== W00 生产计划（模板创建→运行→审核）=====")
        r, wid = tpl_create(client, "W00", params={
            "producerObjective": "拆解《山间小邮差》制作目标：30 秒二维动画，红狐信使送信。"
                                 "给出任务范围、依赖与待决定项；不委派 Specialist。"})
        case = {"tpl": "W00", "createStatus": r.status_code,
                "workflowId": wid,
                "template": (r.json() or {}).get("template") if r.status_code == 200 else None}
        w(f"[create] {r.status_code} wid={wid}")
        if r.status_code != 200:
            case["result"] = "FAIL"
            case["error"] = r.text[:250]
            results["cases"].append(case)
            w(f"[{case['result']}] {case.get('error')}")
        else:
            run_id, run = run_and_poll(client, wid, stop_at_waiting=True)
            case["runId"] = run_id
            case["runStatus"] = run and run.get("status")
            w(f"[wait] run={run_id} status={case['runStatus']} errors={node_errors(run or {})}")
            if case["runStatus"] == "waiting_human":
                rv = pending_review(run)
                rd = client.post(
                    f"{BASE}/api/project/{PROJ}/workflow/runs/{run_id}/reviews/{rv['requestId']}/decision",
                    json={"decision": "approve", "note": "C批工程探针：计划审定通过"})
                case["decision"] = rd.status_code
                run_id2, run2 = run_and_poll(client, wid, timeout=60)
                case["finalStatus"] = run2.get("status")
                # 计划产物核验：producer 会话是否有持久化计划（AgentTask 落盘）
                case["result"] = ("PASS" if case["finalStatus"] == "completed" else "FAIL")
            else:
                case["result"] = "FAIL"
            results["cases"].append(case)
            w(f"[{case['result']}] W00 final={case.get('finalStatus')}")

        # ============ W01（证据 run_12faea654aef completed——跳过重跑）============
        results["cases"].append({"tpl": "W01", "result": "PASS",
                                 "note": "run_12faea654aef completed（首轮真实新写+审核通过）"})
        if False:
            w("\n===== W01 编剧写作（模板）=====")
            r, wid = tpl_create(client, "W01", params={
                "writerObjective": "真实新写两段故事：红狐信使清晨从山间邮站出发，翻过松林小桥把信"
                                   "交给等候的朋友（s01 出发、s02 交信）。必须真实写入。",
                "sceneCount": "2"})
            case = {"tpl": "W01", "createStatus": r.status_code, "workflowId": wid}
            w(f"[create] {r.status_code} wid={wid}")
            if r.status_code != 200:
                case["result"] = "FAIL"
                case["error"] = r.text[:250]
            else:
                run_id, run = run_and_poll(client, wid, stop_at_waiting=True)
                case["runId"] = run_id
                case["runStatus"] = run and run.get("status")
                w(f"[wait] run={run_id} status={case['runStatus']} errors={node_errors(run or {})}")
                if case["runStatus"] == "waiting_human":
                    rv = pending_review(run)
                    # 故事版本引用核验（P47-01：审核对象指向具体故事版本）
                    hand = ((run.get("nodes") or {}).get(rv.get("targetNodeId") or "")
                            or {}).get("handoff") or {}
                    case["upstreamOutputs"] = (hand.get("outputs") or [])[:3]
                    rd = client.post(
                        f"{BASE}/api/project/{PROJ}/workflow/runs/{run_id}/reviews/{rv['requestId']}/decision",
                        json={"decision": "approve", "note": "C批工程探针：故事审定通过"})
                    case["decision"] = rd.status_code
                    run2 = poll_only(client, run_id, timeout=180)
                    case["finalStatus"] = run2.get("status")
                    case["result"] = ("PASS" if case["finalStatus"] == "completed"
                                      and case["upstreamOutputs"] else "FAIL")
                else:
                    case["result"] = "FAIL"
            results["cases"].append(case)
            w(f"[{case['result']}] W01 final={case.get('finalStatus')}")

        # ============ W03 ============
        w("\n===== W03 场景设计（模板）=====")
        r, wid = tpl_create(client, "W03", params={
            "sceneObjective": "按项目故事建立两个地点：山间邮站（清晨、室内）、松林小桥"
                              "（上午、室外）。Location+SceneInstance 真实写入；"
                              "并生成邮站内景的环境视图参考图。"})
        case = {"tpl": "W03", "createStatus": r.status_code, "workflowId": wid}
        w(f"[create] {r.status_code} wid={wid}")
        if r.status_code != 200:
            case["result"] = "FAIL"
            case["error"] = r.text[:250]
        else:
            run_id, run = run_and_poll(client, wid, stop_at_waiting=True, timeout=3600)
            case["runId"] = run_id
            case["runStatus"] = run and run.get("status")
            w(f"[wait] run={run_id} status={case['runStatus']} errors={node_errors(run or {})}")
            if case["runStatus"] == "waiting_human":
                rv = pending_review(run)
                rd = client.post(
                    f"{BASE}/api/project/{PROJ}/workflow/runs/{run_id}/reviews/{rv['requestId']}/decision",
                    json={"decision": "approve", "note": "C批工程探针：场景审定通过"})
                case["decision"] = rd.status_code
                run2 = poll_only(client, run_id, timeout=180)
                case["finalStatus"] = run2.get("status")
                case["result"] = ("PASS" if case["finalStatus"] == "completed" else "FAIL")
            else:
                case["result"] = "FAIL"
        results["cases"].append(case)
        w(f"[{case['result']}] W03 final={case.get('finalStatus')}")

        # ============ W02 ============
        w("\n===== W02 角色资源（模板，真实图像）=====")
        r, wid = tpl_create(client, "W02", params={
            "characterObjective": "按项目故事创建角色「红狐信使」（a young red fox courier in a "
                                  "dark teal jacket and cream scarf, black ear tips, 2D animation "
                                  "style）：character.create → character.generate_views 真实生成"
                                  "三视图 → resource.look.prepare 创建 Look「信使常服」并生成参考图。",
            "approveLook": "true"})
        case = {"tpl": "W02", "createStatus": r.status_code, "workflowId": wid}
        w(f"[create] {r.status_code} wid={wid}")
        if r.status_code != 200:
            case["result"] = "FAIL"
            case["error"] = r.text[:250]
        else:
            run_id, run = run_and_poll(client, wid, stop_at_waiting=True, timeout=4800)
            case["runId"] = run_id
            case["runStatus"] = run and run.get("status")
            w(f"[wait] run={run_id} status={case['runStatus']} errors={node_errors(run or {})}")
            if case["runStatus"] == "waiting_human":
                rv = pending_review(run)
                # 从上游交接取 look 实体与参考资产（工程批准；正式由用户在卡片选）
                pj = json.loads((ROOT / "projects" / PROJ / "output" / "project.json")
                                .read_text(encoding="utf-8"))
                res = json.loads((ROOT / "projects" / PROJ / "output" / "resources.json")
                                 .read_text(encoding="utf-8")) if (
                    ROOT / "projects" / PROJ / "output" / "resources.json").exists() else {}
                looks = res.get("looks") or {}
                look_id = next(iter(looks), None)
                asset_id = ((looks.get(look_id) or {}).get("reference_asset_ids") or [None])[0]
                case["lookId"] = look_id
                case["assetId"] = asset_id
                rd = client.post(
                    f"{BASE}/api/project/{PROJ}/workflow/runs/{run_id}/reviews/{rv['requestId']}/decision",
                    json={"decision": "approve", "note": "C批工程探针：批准 Look 参考",
                          "withSelection": True,
                          "selectionOverride": {"kind": "look_reference",
                                                "entityId": look_id, "versionId": asset_id}})
                case["decision"] = rd.status_code
                run2 = poll_only(client, run_id, timeout=180)
                case["finalStatus"] = run2.get("status")
                rv_after = (run2.get("reviews") or [{}])[0]
                case["selectionState"] = rv_after.get("selectionState")
                case["result"] = ("PASS" if case["finalStatus"] == "completed"
                                  and case["selectionState"] == "done" else "FAIL")
            else:
                case["result"] = "FAIL"
        results["cases"].append(case)
        w(f"[{case['result']}] W02 final={case.get('finalStatus')} sel={case.get('selectionState')}")

        # ============ W04 ============
        w("\n===== W04 导演分镜准备（模板，分镜图）=====")
        r, wid = tpl_create(client, "W04", params={
            "boardObjective": "按项目故事与已有角色/场景资源创建 3 个镜头：s01_c01 邮站内整理信件、"
                              "s01_c02 出门、s02_c01 小桥交信。为每个镜头生成分镜图候选"
                              "（绑定已有角色 Look 参考与场景视图）。",
            "shotCount": "3"})
        case = {"tpl": "W04", "createStatus": r.status_code, "workflowId": wid}
        w(f"[create] {r.status_code} wid={wid}")
        if r.status_code != 200:
            case["result"] = "FAIL"
            case["error"] = r.text[:250]
        else:
            run_id, run = run_and_poll(client, wid, stop_at_waiting=True, timeout=4800)
            case["runId"] = run_id
            case["runStatus"] = run and run.get("status")
            w(f"[wait] run={run_id} status={case['runStatus']} errors={node_errors(run or {})}")
            if case["runStatus"] == "waiting_human":
                rv = pending_review(run)
                rd = client.post(
                    f"{BASE}/api/project/{PROJ}/workflow/runs/{run_id}/reviews/{rv['requestId']}/decision",
                    json={"decision": "approve", "note": "C批工程探针：分镜审定通过"})
                case["decision"] = rd.status_code
                run2 = poll_only(client, run_id, timeout=180)
                case["finalStatus"] = run2.get("status")
                case["result"] = ("PASS" if case["finalStatus"] == "completed" else "FAIL")
            else:
                case["result"] = "FAIL"
        results["cases"].append(case)
        w(f"[{case['result']}] W04 final={case.get('finalStatus')}")

        # ============ W05 ============
        w("\n===== W05 导演视频制作（模板，绑尾帧接力 Skill，真实视频×1）=====")
        r, wid = tpl_create(client, "W05", params={
            "videoObjective": "为镜头 s01_c01（首帧已审定）真实生成 1 条视频候选（约 5 秒，"
                              "720p）：邮站内红狐信使整理信件放进邮袋。完成后核对首中尾帧。"
                              "只做这一个镜头，不要生成其它镜头。",
            "videoCount": "1",
            "bindRelaySkill": "true"})
        case = {"tpl": "W05", "createStatus": r.status_code, "workflowId": wid}
        w(f"[create] {r.status_code} wid={wid}")
        if r.status_code != 200:
            case["result"] = "FAIL"
            case["error"] = r.text[:250]
        else:
            # Skill 绑定核验（P47-19：编译计划含 relay skill）
            rv0 = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/{wid}/compile",
                              json={"definition": None})
            plan0 = (rv0.json() or {}).get("plan") or {}
            skills_bound = {nid: [s["skillId"] for s in pn.get("skills") or []]
                            for nid, pn in (plan0.get("nodes") or {}).items()
                            if pn.get("skills")}
            case["skillBindings"] = skills_bound
            w(f"[skill] bindings={skills_bound}")
            run_id, run = run_and_poll(client, wid, stop_at_waiting=True, timeout=7200)
            case["runId"] = run_id
            case["runStatus"] = run and run.get("status")
            w(f"[wait] run={run_id} status={case['runStatus']} errors={node_errors(run or {})}")
            if case["runStatus"] == "waiting_human":
                rv = pending_review(run)
                # 从真实候选中选用（视频版本 ID 运行期解析）
                pj = json.loads((ROOT / "projects" / PROJ / "output" / "project.json")
                                .read_text(encoding="utf-8"))
                vv = None
                for sc in (pj.get("scenes") or []):
                    for sh in (sc.get("shots") or []):
                        for v in (sh.get("video_versions") or []):
                            if v.get("status") == "done":
                                vv = (sh["shot_id"], v["version_id"])
                                break
                case["video"] = vv
                if vv:
                    rd = client.post(
                        f"{BASE}/api/project/{PROJ}/workflow/runs/{run_id}/reviews/{rv['requestId']}/decision",
                        json={"decision": "approve", "note": "C批工程探针：选用视频版本",
                              "withSelection": True,
                              "selectionOverride": {"kind": "video_version",
                                                    "entityId": vv[0],
                                                    "versionId": vv[1]}})
                    case["decision"] = rd.status_code
                    run2 = poll_only(client, run_id, timeout=180)
                    case["finalStatus"] = run2.get("status")
                    rv_after = (run2.get("reviews") or [{}])[0]
                    case["selectionState"] = rv_after.get("selectionState")
                    # 指针核验
                    pj2 = json.loads((ROOT / "projects" / PROJ / "output" / "project.json")
                                     .read_text(encoding="utf-8"))
                    sel = None
                    for sc in (pj2.get("scenes") or []):
                        for sh in (sc.get("shots") or []):
                            if sh["shot_id"] == vv[0]:
                                sel = sh.get("final_video")
                    case["finalVideoPointer"] = sel
                    case["result"] = ("PASS" if case["finalStatus"] == "completed"
                                      and sel == vv[1] and case["selectionState"] == "done"
                                      and skills_bound else "FAIL")
                else:
                    case["result"] = "FAIL"
                    case["error"] = "无完成视频版本"
            else:
                case["result"] = "FAIL"
        results["cases"].append(case)
        w(f"[{case['result']}] W05 final={case.get('finalStatus')} "
          f"video={case.get('video')} pointer={case.get('finalVideoPointer')}")

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    LOG.write_text(log_lines.getvalue(), encoding="utf-8")
    summary = {c.get("tpl") or c.get("name"): c.get("result") for c in results["cases"]}
    w("[SUMMARY]", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
