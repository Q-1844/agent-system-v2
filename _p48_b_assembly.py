# -*- coding: utf-8 -*-
"""Phase48 B3：W08 装配对照 run 驱动（原音基线版 + 政策修改版）。

两轮 W08 模板 run（显式 videoVersions scope，全 12 镜）：
  B-ASM-NATIVE  基线：原生音轨全保留（无任何层）——政策"默认保留"的直接体现
  B-ASM-POLICY  修改：同素材，由 assembly Agent 按新政策逐镜分类后自主决定
                （含对不合格片段的处理提案；音乐/旁白一律不加）
中间审核（n_rv0 若存在/视频选用已由 scope 固定）按实验惯例 approve；
n_rv1 成片审定留给用户（不代办）。
用法：python _p48_b_assembly.py start|poll|report
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://localhost:8000"
PROJ = "P48_B_Audio"
ROOT = Path(__file__).parent
EVID = ROOT / "_p48_b_assembly_evidence.json"

RUNS = {
    "B-ASM-NATIVE": {
        "wid": "wf_b_asm_native",
        "objective": ("基线装配：按选片范围装配 Sequence，全部 12 镜原生音轨原样保留"
                      "（render profile audio=native，不加任何音频层）。"
                      "逐镜记录音频分类依据，完成渲染并核验可播放。"),
    },
    "B-ASM-POLICY": {
        "wid": "wf_b_asm_policy",
        "objective": ("按 Phase48 装配政策装配：全片禁止自动旁白；逐镜先分类后决定"
                      "（保留/控增益/如实标记不可用），默认保留合格原生环境声与动作声；"
                      "音乐仅按作品政策（本轮不加）。完成渲染并核验可播放。"),
    },
}


def api(method, path, payload=None, params=""):
    url = f"{BASE}{path}{params}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8")
    return json.loads(body) if body else {}


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def load_evid():
    if EVID.exists():
        return json.loads(EVID.read_text(encoding="utf-8"))
    return {"runs": {}}


def save_evid(ev):
    EVID.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")


def final_versions():
    """从 store 读 12 镜 final_video version_id（夹具准备完成后调用）。"""
    import sys as _s
    _s.path.insert(0, str(ROOT))
    from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
    from src.utils.project_manager import ProjectManager
    from src.core.store import ProjectStore
    store = ProjectStore(Path(ProjectManager("./projects").get_project_dir(PROJ)))
    project = store.load()
    out = []
    for sc in project.scenes:
        for sh in sc.shots:
            if sh.final_video:
                out.append({"shotId": sh.shot_id, "versionId": sh.final_video})
    return out


def start():
    ev = load_evid()
    vv = final_versions()
    log("final versions:", len(vv))
    if len(vv) < 12:
        log("!! 少于 12 镜 final_video，先完成 B2")
        return
    for tag, cfg in RUNS.items():
        if ev["runs"].get(tag, {}).get("runId"):
            continue
        body = {"workflowId": cfg["wid"],
                "params": {"assemblyObjective": cfg["objective"],
                           "videoVersions": json.dumps(vv, ensure_ascii=False)}}
        # W08 params.videoVersions 期望数组（模板 _w08 params.get("videoVersions")）
        body["params"]["videoVersions"] = vv
        r = api("POST", f"/api/project/{PROJ}/workflow/templates/W08/create", body)
        log("created", tag, json.dumps(r, ensure_ascii=False)[:200])
        wf = r.get("workflowId") or cfg["wid"]
        run = api("POST", f"/api/project/{PROJ}/workflow/definitions/{wf}/runs",
                  {"idempotencyKey": f"p48b-{tag.lower()}-{int(time.time())}"})
        rid = run["run"]["runId"]
        ev["runs"].setdefault(tag, {})
        ev["runs"][tag].update({"workflowId": wf, "runId": rid,
                                "started_at": time.time(),
                                "snapshot": run["run"].get("snapshot")})
        save_evid(ev)
        log("run started", tag, rid, "thinking=", run["run"].get("snapshot", {}).get("thinking"))


def poll_once(tag):
    ev = load_evid()
    rid = ev["runs"][tag]["runId"]
    r = api("GET", f"/api/project/{PROJ}/workflow/runs/{rid}")
    run = r.get("run") or r
    status = run.get("status")
    ev["runs"][tag]["last_status"] = status
    ev["runs"][tag]["nodes"] = {n["nodeId"]: {"status": n.get("status"),
                                              "error": (n.get("error") or "")[:200]}
                                for n in run.get("nodes", []) if isinstance(n, dict)}
    save_evid(ev)
    # W08 唯一审核 = n_rv1 成片审定（用户关口，B批执行书明确不代办）——
    # 全部 pending 审核保持等待，仅记录状态。
    for rv in run.get("reviews", []):
        if rv.get("status") == "pending":
            log("review pending (user gate)", tag, rv.get("nodeId"), rv.get("requestId"))
    return status


def poll():
    ev = load_evid()
    for tag in RUNS:
        if not ev["runs"].get(tag, {}).get("runId"):
            log("skip", tag, "not started")
            continue
        for _ in range(240):  # 单轮最长 ~32 分钟
            st = poll_once(tag)
            log(tag, "->", st)
            if st in ("completed", "failed", "stopped", "rejected", "needs_reconciliation"):
                break
            if st == "waiting_human":
                # 还有 pending 用户关口（成片审定）→ 停轮询
                break
            time.sleep(8)


def report():
    ev = load_evid()
    for tag, cfg in RUNS.items():
        info = ev["runs"].get(tag, {})
        rid = info.get("runId")
        if not rid:
            print(tag, "NOT STARTED")
            continue
        r = api("GET", f"/api/project/{PROJ}/workflow/runs/{rid}")
        run = r.get("run") or r
        nodes = {n["nodeId"]: n.get("status") for n in run.get("nodes", [])
                 if isinstance(n, dict)}
        reviews = {rv.get("nodeId"): rv.get("status") for rv in run.get("reviews", [])}
        print(json.dumps({"tag": tag, "runId": rid, "status": run.get("status"),
                          "nodes": nodes, "reviews": reviews},
                         ensure_ascii=False, indent=1))


if __name__ == "__main__":
    ph = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"start": start, "poll": poll, "report": report}.get(ph, report)()
