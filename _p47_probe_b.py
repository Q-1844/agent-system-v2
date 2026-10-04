# -*- coding: utf-8 -*-
"""
_p47_probe_b.py — Phase 47 B批真机探针（隔离项目 P47_B_Probe，真实模型 + 真实图像）。

覆盖（PHASE47 §4.1/§4.3/§4.6 + §9 P47-10/11/12/17/18/27/28）：
  S0  编剧真实新写两段故事（require_new 域+场景）            → P47-10
  S3  合格故事正常复用（allow_verified_reuse，reusedRefs）    → P47-17a
  S4  旧素材冒充被拒（require_new + 目标="复用不写"）        → P47-18（负例）
  S1  角色生产：character+Look 真实图像 → 审核批准 Look      → P47-11/27/28
  S2  已批准 Look 的新造型候选（版本化更新，原版保留）        → P47-12
  S5  混合任务：复用合格 Look + 补缺新 Look                  → P47-17d

所有文本调用 thinking=True（run 快照固定）；真实图像经 character.generate_views /
resource.look.prepare（turn 内等待真实返回）。隔离项目：P47_B_Probe（最小空项目）。
"""
import io
import json
import shutil
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent
BASE = "http://localhost:8000"
PROJ = "P47_B_Probe"
OUT = ROOT / "_p47_probe_b_results.json"
LOG = ROOT / "_p47_probe_b_log.txt"
log_lines = io.StringIO()

sys.path.insert(0, str(ROOT))


def w(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    log_lines.write(line + "\n")


def api(method, path, **kw):
    return httpx.request(method, BASE + path, timeout=kw.pop("timeout", 120), **kw)


# ---------- 夹具：正式 API 建项目（含 ProjectVisualProfile——资源图像生成前置） ----------
def setup_project(client):
    dst = ROOT / "projects" / PROJ
    if dst.exists():
        shutil.rmtree(dst)
    # 走正式建项目入口（P1 §6：render_family 必填；profile 由该入口初始化）。
    r = client.post(f"{BASE}/api/projects/create",
                    params={"name": PROJ, "render_family": "2d_animation",
                            "art_direction": "flat 2D animation, clean lines, "
                                             "soft watercolor backgrounds"})
    w(f"[setup] 正式建项目 {PROJ} → {r.status_code} {r.text[:120]}")
    assert r.status_code == 200, r.text


def save_def(client, d):
    r = client.put(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default",
                   json={"definition": d, "expectedRevision": None})
    if r.status_code == 409:
        cur = client.get(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default").json()
        r = client.put(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default",
                       json={"definition": d, "expectedRevision": cur.get("revision")})
    return r


def node(nid, ntype, ref=None, **cfg):
    return {"id": nid, "type": ntype, "refId": ref, "refVersion": None,
            "label": None, "config": cfg, "position": {"x": 0, "y": 0}}


def chain_def(name, nodes, skill_bindings=None):
    core = [n for n in nodes if n["type"] in ("agent", "human_review")]
    edges = []
    seq = ["nst"] + [n["id"] for n in core] + ["nen"]
    for i in range(len(seq) - 1):
        edges.append({"id": f"e{i}", "type": "execution", "source": seq[i],
                      "target": seq[i + 1], "sourcePort": "out", "targetPort": "in"})
    all_nodes = [node("nst", "boundary_start"), *nodes, node("nen", "boundary_end")]
    for i, sb in enumerate(skill_bindings or []):
        skid = f"nsk{i}"
        all_nodes.append(node(skid, "skill", sb["skillId"], label=sb["skillId"]))
        edges.append({"id": f"eb{i}", "type": "skill_binding", "source": skid,
                      "target": sb["targetNodeId"], "skillVersion": "1.3.1",
                      "sourcePort": "out", "targetPort": "in"})
    return {"schemaVersion": "workflow_draft_v1", "projectId": PROJ,
            "workflowId": "wf_default", "name": name, "status": "draft",
            "nodes": all_nodes, "edges": edges}


def start_and_poll(client, d, name, key_prefix, timeout=1800, stop_at_waiting=False):
    """保存定义 → 启动 → 轮询。stop_at_waiting=True 时在 waiting_human 停轮询
    （返回 (run, "waiting")），否则轮到终态。"""
    r = save_def(client, d)
    case = {"name": name, "save": r.status_code}
    if r.status_code != 200:
        case["result"] = "FAIL"
        case["error"] = r.text[:300]
        return case, None
    key = f"{key_prefix}-{uuid.uuid4().hex[:8]}"
    r = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/wf_default/runs",
                    json={"idempotencyKey": key})
    if r.status_code != 200:
        case["result"] = "FAIL"
        case["error"] = f"start {r.status_code}: {r.text[:300]}"
        return case, None
    run = r.json()["run"]
    case["runId"] = run["runId"]
    case["snapshotThinking"] = run.get("snapshot", {}).get("thinking")
    case["textModel"] = run.get("snapshot", {}).get("textModel")
    t0 = time.time()
    last = run
    while time.time() - t0 < timeout:
        last = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{run['runId']}").json()
        st = last.get("status")
        if st in ("completed", "failed", "stopped", "rejected",
                  "needs_reconciliation") or (stop_at_waiting and st == "waiting_human"):
            break
        time.sleep(4)
    case["runStatus"] = last.get("status")
    node_states = {nid: n.get("status") for nid, n in (last.get("nodes") or {}).items()}
    case["nodeStates"] = node_states
    case["errors"] = {nid: (n.get("error") or "")[:300]
                      for nid, n in (last.get("nodes") or {}).items() if n.get("error")}
    # 交接包与 attempt 摘要
    hands = {}
    for nid, n in (last.get("nodes") or {}).items():
        h = n.get("handoff")
        if h:
            hands[nid] = {"outputs": h.get("outputs"),
                          "reusedRefs": h.get("reusedRefs"),
                          "missingRequirements": h.get("missingRequirements")}
        for a in (n.get("attempts") or []):
            if a.get("taskRefs"):
                case.setdefault("taskRefs", []).extend(a["taskRefs"])
    case["handoffs"] = hands
    case["reviews"] = [{k: rv.get(k) for k in ("requestId", "status", "decision",
                                               "selectionRequired", "selectionState",
                                               "selectionReceipt")}
                       for rv in (last.get("reviews") or [])]
    case["_run_full"] = last
    return case, last


def read_resources():
    p = ROOT / "projects" / PROJ / "output" / "resources.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def read_project():
    p = ROOT / "projects" / PROJ / "output" / "project.json"
    return json.loads(p.read_text(encoding="utf-8"))


def main():
    results = {"project": PROJ, "startedAt": time.time(), "cases": []}
    with httpx.Client(timeout=120) as client:
        setup_project(client)
        cat = client.get(f"{BASE}/api/project/{PROJ}/workflow/catalog").json()
        w(f"[catalog] agents={len(cat.get('agents') or [])} skills={len(cat.get('skills') or [])}")

        # ============ S0 编剧真实新写两段（P47-10）============
        w("\n===== S0 编剧真实新写（require_new，2 场景）=====")
        d0 = chain_def("S0 新写", [
            node("n_sw", "agent", "script_writer",
                 objective="为本项目真实新写一个两段小故事：《红狐邮差》——清晨雨后街角出发送信到钟楼。"
                           "要求：两段（s01 街角出发、s02 钟楼交信），必须真实写入项目（script.update），"
                           "不要只回复文本。",
                 completion={"strategy": "require_new",
                             "outputs": [{"kind": "scene", "minCount": 2}]}),
        ])
        c0, run0 = start_and_poll(client, d0, "S0_new_write", "p47b-s0")
        c0["result"] = ("PASS" if c0.get("runStatus") == "completed"
                        and not c0.get("errors") else "FAIL")
        w(f"[{c0['result']}] run={c0.get('runId')} status={c0.get('runStatus')} "
          f"errors={c0.get('errors')}")
        results["cases"].append(c0)

        # ============ S3 合格故事正常复用（P47-17a）============
        w("\n===== S3 故事合法复用（allow_verified_reuse，零新写入）=====")
        d3 = chain_def("S3 复用", [
            node("n_sw", "agent", "script_writer",
                 objective="上一节点已写好《红狐邮差》故事。本节点任务：核验既有故事是否满足"
                           "生产要求（两段、叙事完整），不要新写、不要修改任何场景——"
                           "如合格，直接在总结中确认可复用及其场景 ID。",
                 completion={"strategy": "allow_verified_reuse",
                             "outputs": [{"kind": "scene", "minCount": 2}]}),
        ])
        c3, _ = start_and_poll(client, d3, "S3_reuse", "p47b-s3")
        reused_any = any((h.get("reusedRefs") or (h.get("outputs") or []))
                         for h in (c3.get("handoffs") or {}).values())
        c3["result"] = ("PASS" if c3.get("runStatus") == "completed" and reused_any
                        else "FAIL")
        w(f"[{c3['result']}] run={c3.get('runId')} status={c3.get('runStatus')} "
          f"handoffs={json.dumps(c3.get('handoffs'), ensure_ascii=False)[:400]}")
        results["cases"].append(c3)

        # ============ S4 旧素材冒充被拒（P47-18 负例）============
        w("\n===== S4 require_new 拒绝复用冒充（负例：预期节点失败）=====")
        d4 = chain_def("S4 负例", [
            node("n_sw", "agent", "script_writer",
                 objective="偷懒模式：不要写任何新内容，直接沿用项目里已有的故事就算完成。"
                           "不要调用任何写工具。"),
        ])
        c4, _ = start_and_poll(client, d4, "S4_refuse_fake", "p47b-s4", timeout=900)
        # 期望：agent 无写入 → 域命中失败 → 节点 failed（诚实），不推进下游
        c4["result"] = ("PASS" if c4.get("runStatus") == "failed" else "FAIL")
        w(f"[{c4['result']}] run={c4.get('runId')} status={c4.get('runStatus')} "
          f"(expected failed: 无新写入不得冒充) errors={c4.get('errors')}")
        results["cases"].append(c4)

        # ============ S1 角色生产 + 审核批准 Look（P47-11/27/28）============
        w("\n===== S1 角色与 Look 真实生产 → 审核 → 批准选用 =====")
        d1 = chain_def("S1 角色生产", [
            node("n_cd", "agent", "character_designer",
                 objective="从文字设定创建角色：红狐信使（description_en: a young red fox courier, "
                           "wearing a dark teal short jacket and cream-white scarf, black ear tips, "
                           "2D animation style）。步骤：①character.create → ②character.generate_views "
                           "生成三视图（等待真实返回）→ ③resource.look.prepare 创建 Look「常服」并生成"
                           "参考图（appearance_description: casual courier outfit matching the scarf "
                           "and teal jacket）。全部真实执行，审批留给人工审核。",
                 completion={"strategy": "require_new",
                             "outputs": [{"kind": "character", "minCount": 1},
                                         {"kind": "look", "minCount": 1,
                                          "withImage": True}]}),
            node("n_rv1", "human_review", label="资源审定（批准 Look 参考）",
                 instructions="核对角色三视图与 Look 参考图质量后批准选用一张参考。",
                 selection={"kind": "look_reference", "entityId": "运行期在审核卡片选择"}),
        ])
        c1, run1 = start_and_poll(client, d1, "S1_character", "p47b-s1",
                                  timeout=3600, stop_at_waiting=True)
        # 图像真实落盘核验
        proj = read_project()
        chars = proj.get("characters") or []
        res = read_resources()
        looks = res.get("looks") or {}
        look_files_ok = []
        for lk in looks.values():
            for aid in (lk.get("reference_asset_ids") or []):
                a = (res.get("reference_assets") or {}).get(aid) or {}
                loc = a.get("local")
                if loc and (ROOT / "projects" / PROJ / loc).is_file():
                    look_files_ok.append(lk.get("look_id"))
        c1["characterCreated"] = [c.get("name") for c in chars]
        c1["charViewFiles"] = [v for c in chars for v in (c.get("views") or {}).keys()]
        c1["looksWithReadableFiles"] = look_files_ok
        c1["resultWaiting"] = c1.get("runStatus") == "waiting_human"
        w(f"[wait] run={c1.get('runId')} status={c1.get('runStatus')} "
          f"chars={c1['characterCreated']} looks_ok={look_files_ok} "
          f"errors={c1.get('errors')}")
        if c1.get("runStatus") == "waiting_human":
            # 工程用例：模拟用户批准（隔离项目；正式生产由用户在卡片操作）
            rv_full = next(rv for rv in run1["reviews"] if rv["status"] == "pending")
            look_id = next(iter(looks)) if looks else None
            asset_id = ((looks.get(look_id) or {}).get("reference_asset_ids") or [None])[0]
            r = client.post(
                f"{BASE}/api/project/{PROJ}/workflow/runs/{c1['runId']}"
                f"/reviews/{rv_full['requestId']}/decision",
                json={"decision": "approve", "note": "工程探针批准：隔离项目机制验证",
                      "withSelection": True,
                      "selectionOverride": {"kind": "look_reference",
                                            "entityId": look_id,
                                            "versionId": asset_id}})
            c1["decisionStatus"] = r.status_code
            dec = r.json() if r.status_code == 200 else {}
            c1["decision"] = {k: dec.get(k) for k in ("decision", "alreadyDecided")}
            # 轮询 run 到终态（选用完成 → 推进）
            t0 = time.time()
            while time.time() - t0 < 120:
                run1 = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{c1['runId']}").json()
                if run1.get("status") in ("completed", "failed",
                                          "needs_reconciliation"):
                    break
                time.sleep(3)
            c1["runStatusAfterDecision"] = run1.get("status")
            rv_after = (run1.get("reviews") or [{}])[0]
            c1["selectionState"] = rv_after.get("selectionState")
            c1["selectionReceipt"] = rv_after.get("selectionReceipt")
            # 真实指针核验：Look approved_reference_asset_ids
            res2 = read_resources()
            lk2 = (res2.get("looks") or {}).get(look_id or "") or {}
            c1["approvedAfter"] = lk2.get("approved_reference_asset_ids")
            c1["result"] = ("PASS" if run1.get("status") == "completed"
                            and c1["selectionState"] == "done"
                            and c1["approvedAfter"] else "FAIL")
        else:
            c1["result"] = "FAIL"
        w(f"[{c1['result']}] after_decision={c1.get('runStatusAfterDecision')} "
          f"selection={c1.get('selectionState')} approved={c1.get('approvedAfter')}")
        results["cases"].append(c1)

        # ============ S2 已批准 Look 的新造型候选（P47-12）============
        w("\n===== S2 已批准 Look 的新造型（版本化，原版保留）=====")
        look_id = next(iter((read_resources().get("looks") or {})), None)
        n_looks_before = len(read_resources().get("looks") or {})
        d2 = chain_def("S2 新造型", [
            node("n_cd", "agent", "character_designer",
                 objective=f"项目里角色「红狐信使」已有经用户批准的 Look（look_id={look_id}）。"
                 "本节点：为同一角色创建一个新造型 Look「雨天信使」（appearance_description: "
                 "same red fox courier wearing a yellow raincoat, hood up）并生成参考图。"
                 "严禁修改或删除既有 Look 及其参考——只新增。",
                 completion={"strategy": "require_new",
                             "outputs": [{"kind": "look", "withImage": True,
                                          "minCount": 2}]}),
        ])
        c2, _ = start_and_poll(client, d2, "S2_new_look", "p47b-s2", timeout=3600)
        res3 = read_resources()
        looks3 = res3.get("looks") or {}
        c2["nLooksBefore"] = n_looks_before
        c2["nLooksAfter"] = len(looks3)
        c2["originalIntact"] = bool(look_id and look_id in looks3 and
                                    (looks3[look_id].get("reference_asset_ids")
                                     or (looks3[look_id].get("approved_reference_asset_ids")
                                         or [])))
        orig_lk_before = (read_resources().get("looks") or {}).get(look_id or "") or {}
        c2["originalRefs"] = orig_lk_before.get("reference_asset_ids")
        c2["result"] = ("PASS" if c2.get("runStatus") == "completed"
                        and c2["nLooksAfter"] > c2["nLooksBefore"]
                        and c2["originalIntact"] else "FAIL")
        w(f"[{c2['result']}] run={c2.get('runId')} status={c2.get('runStatus')} "
          f"looks {c2['nLooksBefore']}→{c2['nLooksAfter']} original_intact={c2['originalIntact']} "
          f"errors={c2.get('errors')}")
        results["cases"].append(c2)

        # ============ S5 混合任务：复用合格 + 补缺（P47-17d）============
        w("\n===== S5 混合：复用既有合格 Look + 新增补缺 =====")
        res4 = read_resources()
        existing_look = next(iter(res4.get("looks") or {}), None)
        d5 = chain_def("S5 混合", [
            node("n_cd", "agent", "character_designer",
                 objective=f"混合任务：项目已有合格 Look（look_id={existing_look}，已批准）——本节点"
                 "【复用】它，不要重新生成它的参考图；同时为角色「红狐信使」补一个缺失的"
                 "「夜间信使」Look（appearance_description: same fox in a dark blue night "
                 "cloak with a small lantern）并真实生成参考图。完成后汇报：复用了哪个、"
                 "新增了哪个。",
                 completion={"strategy": "allow_verified_reuse",
                             "outputs": [{"kind": "look", "withImage": True,
                                          "minCount": 3}]}),
        ])
        c5, _ = start_and_poll(client, d5, "S5_mixed", "p47b-s5", timeout=3600)
        res5 = read_resources()
        c5["nLooksFinal"] = len(res5.get("looks") or {})
        c5["result"] = ("PASS" if c5.get("runStatus") == "completed"
                        and c5["nLooksFinal"] >= 3 else "FAIL")
        w(f"[{c5['result']}] run={c5.get('runId')} status={c5.get('runStatus')} "
          f"looks={c5['nLooksFinal']} handoffs={json.dumps(c5.get('handoffs'), ensure_ascii=False)[:300]}")
        results["cases"].append(c5)

    results["finishedAt"] = time.time()
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    LOG.write_text(log_lines.getvalue(), encoding="utf-8")
    w(f"\n[saved] {OUT.name}")
    summary = {c["name"]: c.get("result") for c in results["cases"]}
    w("[SUMMARY]", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
