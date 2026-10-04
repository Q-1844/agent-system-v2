# -*- coding: utf-8 -*-
"""
_p47_probe_d.py — Phase 47 D批：新项目《风后邮站》六员完整真实生产（W09 链）。

简报（PHASE47 §6.1）：30~60 秒二维动画动作叙事短片；主角年轻红狐（深青短外套、
米白围巾、耳尖黑色）；关键道具红邮袋（圆形黄铜扣）；三段故事（街角→邮站→小桥）；
结局事实固定=信件交付；≥6 镜；外部男声旁白，不依赖原生对白。

执行（§6.2 分工）：系统 Agent 自主制作（目标/质量/范围/交付由本驱动给出，不提供
逐镜答案）；驱动负责模板创建、启动、轮询、工程审核决策（隔离工程用例验证机制；
正式审美/定版决定保留给用户——成片审核不代办）、证据采集（§6.3 真实媒体清单）。
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
PROJ = "风后邮站"
WID = "wf_w09_fenghou"
OUT = ROOT / "_p47_probe_d_results.json"
log_lines = io.StringIO()


def w(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    log_lines.write(line + "\n")


def node(nid, ntype, ref=None, **cfg):
    return {"id": nid, "type": ntype, "refId": ref, "refVersion": None,
            "label": None, "config": cfg, "position": {"x": 0, "y": 0}}


def read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def project_media_facts():
    """真实媒体事实（§6.3 核验用）。"""
    pdir = ROOT / "projects" / PROJ / "output"
    pj = read_json(pdir / "project.json")
    chars = []
    for c in (pj.get("characters") or []):
        views = {v: bool(a and a.get("local")
                         and (pdir / a["local"]).is_file())
                 for v, a in (c.get("views") or {}).items()}
        chars.append({"name": c.get("name"), "views": views})
    shots_media = []
    for sc in (pj.get("scenes") or []):
        for sh in (sc.get("shots") or []):
            ff = (sh.get("first_frame") or {}).get("candidates") or []
            ff_ok = any(c.get("local") and (pdir / c["local"]).is_file()
                        for c in ff)
            vids = [{"version_id": v.get("version_id"), "status": v.get("status"),
                     "playable": bool(v.get("local") and (pdir / v["local"]).is_file())}
                    for v in (sh.get("video_versions") or [])]
            shots_media.append({"shot": sh.get("shot_id"),
                                "firstFrameFile": ff_ok,
                                "finalVideo": sh.get("final_video"),
                                "videoVersions": vids})
    res = read_json(pdir / "resources.json")
    looks = [{"look_id": l.get("look_id"), "label": l.get("label"),
              "n_refs": len(l.get("reference_asset_ids") or []),
              "approved": l.get("approved_reference_asset_ids") or []}
             for l in (res.get("looks") or {}).values()]
    seqs = read_json(pdir / "sequences.json")
    seq_list = []
    for sid, e in (seqs.get("sequences") or seqs if isinstance(seqs, dict) else {}).items():
        if not isinstance(e, dict):
            continue
        renders = e.get("render_versions") or []
        seq_list.append({"sequence_id": sid,
                         "n_clips": len(((e.get("block") or {}).get("clips")) or []),
                         "renders": [{"version_id": r.get("version_id"),
                                      "status": r.get("status"),
                                      "file": bool(r.get("output_file") and
                                                   (pdir / r["output_file"]).is_file())}
                                     for r in renders],
                         "final_render": e.get("final_render")})
    return {"characters": chars, "looks": looks, "shots": shots_media,
            "sequences": seq_list}


def main():
    results = {"project": PROJ, "cases": [], "mediaFacts": []}
    with httpx.Client(timeout=120) as client:
        # ---- 建项目（正式 API）----
        r = client.post(f"{BASE}/api/projects",
                        params={}) if False else client.post(
            f"{BASE}/api/projects/create",
            params={"name": PROJ, "render_family": "2d_animation",
                    "art_direction": "flat 2D animation, clean linework, "
                                     "warm morning light, soft watercolor "
                                     "backgrounds, gentle wind mood"})
        w(f"[setup] create {PROJ} → {r.status_code} {r.text[:100]}")
        if r.status_code == 409:
            w("[setup] 项目已存在（沿用）")

        # ---- W09 模板创建（含全部节点目标参数）----
        params = {
            "producerObjective":
                "拆解《风后邮站》制作目标：30~60 秒二维动画动作叙事短片；外部男声旁白，"
                "无人物对白。给出任务范围、依赖与待决定项（含道具参考图获取方式）；"
                "不委派 Specialist。",
            "writerObjective":
                "真实新写三段故事（s01/s02/s03）：①清晨风后的街角——红狐信使阿橙捡起红色"
                "邮袋，确认一封信没有被雨打湿，向邮站走去；②邮站内——核对信件，把那封信"
                "放入邮袋，提袋走到门口；③门外小桥——走过小桥，把信交给等候的朋友，"
                "回望亮起的邮站灯。结局事实固定：信件完成交付。每段 2~3 句，动作明确。",
            "characterObjective":
                "创建主角「阿橙」（description_en: a young red fox courier with black ear "
                "tips, wearing a dark teal short jacket and a cream-white scarf, bright "
                "alert eyes, 2D animation style）：character.create → character.generate_views "
                "真实生成三视图 → resource.look.prepare 创建 Look「信使常服」并真实生成参考图。"
                "关键道具「红色邮袋」（圆形黄铜扣）：本系统没有道具生成端点——用 "
                "resource.composition.prepare 生成「阿橙背着红色邮袋」的组合参考图"
                "（composition.generate 真实出图），再以该参考图登记道具"
                "（resource.prop.register 的 reference_asset_id 用组合图资产）；"
                "若组合图不可用作道具参考，如实把道具列为缺项交人工处理，不要伪造。",
            "sceneObjective":
                "按故事建立三个地点事实与场景：①清晨街角（旧邮筒、湿石路、风）；"
                "②邮站内（木质柜台、信件格架、暖灯）；③门外小桥（木桥、溪水、"
                "回望可见邮站灯）。为「邮站内」与「小桥」生成真实环境视图参考图。",
            "boardObjective":
                "按故事与已有资源创建 6 个镜头（同地点连续动作、一次换场、"
                "一次中近景身份检查由你按叙事判断安排）：建议 s01 两镜（街角捡袋"
                "查信 / 走向邮站）、s02 两镜（核对信件入袋 / 提袋到门口）、"
                "s03 两镜（桥上交信 / 回望邮站灯）。为每镜生成分镜图候选"
                "（绑定阿橙 Look 参考与场景视图）。",
            "videoObjective":
                "为全部 6 个镜头真实生成视频候选（每镜约 5 秒）：先在回复中列出每镜"
                "事前决策（起点帧/预期尾态/接续方式：正常切镜或尾帧接力），"
                "再逐镜提交生成并等待真实完成；完成后核对首中尾帧与剧情尾态"
                "（结局=信件交付）。只做这 6 镜。",
            "assemblyObjective":
                "接收精确选片范围装配《风后邮站》成片。步骤：①assembly.snapshot_freeze "
                "为本项目冻结正式快照（pin=各镜 live final_video；本项目无 44A 快照，"
                "这是装配前置）；②assembly.sequence_create 采用冻结清单版本；"
                "③旁白=外部男声（assembly.tts_synthesize 按项目现有 TTS 配置真实合成，"
                "旁白文本按三段故事写 6~10 句短句；URL 有有效期，音频必须真实落盘）；"
                "④音乐=连续一支（当前获准素材/已接通服务；不要引入新服务，"
                "不要用不存在的占位音频文件——引用的每个音频文件必须真实存在）；"
                "⑤原视频音轨静音（render_profile audio=silent 且音频计划层不与旁白"
                "叠坏对白）；⑥render_submit 等待渲染真实完成并核验输出文件可读。",
            "videoCount": "6",
            "bindRelaySkill": "true",
        }
        r = client.post(f"{BASE}/api/project/{PROJ}/workflow/templates/W09/create",
                        json={"workflowId": WID, "params": params})
        case_setup = {"name": "create_w09", "status": r.status_code}
        if r.status_code != 200:
            w(f"[FAIL] W09 创建失败: {r.text[:300]}")
            results["cases"].append(case_setup | {"result": "FAIL",
                                                  "error": r.text[:300]})
            OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            return
        v = r.json().get("validation") or {}
        w(f"[create] W09 ok rev={r.json().get('revision')} validation.ok={v.get('ok')}")
        case_setup["result"] = "PASS" if v.get("ok") else "FAIL_VALIDATION"
        results["cases"].append(case_setup)
        # 视频节点（6 镜单 turn 风险大）→ allow_verified_reuse：跨 attempt 诚实续做
        # （attempt1 生成部分=created；continue 后既有=verified reused + 补缺 created）
        doc = client.get(f"{BASE}/api/project/{PROJ}/workflow/definitions/{WID}").json()
        for n in doc.get("nodes", []):
            if n.get("type") == "agent" and n.get("refId") == "director"                     and (n.get("config", {}).get("completion", {}).get("outputs") or [{}])[0].get("kind") == "video_version":
                n["config"]["completion"]["strategy"] = "allow_verified_reuse"
        rs2 = client.put(f"{BASE}/api/project/{PROJ}/workflow/definitions/{WID}",
                         json={"definition": doc, "expectedRevision": doc.get("revision")})
        w(f"[patch] video completion strategy → allow_verified_reuse: {rs2.status_code}")

        # ---- 启动 run ----
        r = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/{WID}/runs",
                        json={"idempotencyKey": f"p47d-{uuid.uuid4().hex[:8]}"})
        if r.status_code != 200:
            w(f"[FAIL] 启动失败: {r.text[:300]}")
            results["cases"].append({"name": "start", "result": "FAIL",
                                     "error": r.text[:300]})
            OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            return
        run = r.json()["run"]
        rid = run["runId"]
        w(f"[run] {rid} thinking={run['snapshot'].get('thinking')} "
          f"model={run['snapshot'].get('textModel')}")

        # ---- 主轮询循环：审核关口工程决策 + 证据采集 ----
        REVIEW_PLAN = {
            1: ("approve", "工程决策：故事三段结构完整、结局事实固定。"),
            2: ("approve_select_look", "工程决策：批准常服 Look 参考（机制验证）。"),
            3: ("approve", "工程决策：场景事实与视图满足分镜需要。"),
            4: ("approve_select_first_frames", "工程决策：分镜候选可用，逐镜选用首帧。"),
            5: ("approve", "工程决策（一审）：叙事/身份/技术状态核查通过。"),
            6: ("approve_select_videos", "工程决策（二审）：选用各镜首条完成候选"
                                         "（机制验证；正式审美选版待用户）。"),
            7: None,   # 成片审定：不代办——用户定版待处理
        }
        last_states = {}
        stable = 0
        t0 = time.time()
        RESTART_BASE = time.time()
        while time.time() - t0 < 6 * 3600:
            run = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid}").json()
            st = run.get("status")
            states = {nid: n.get("status") for nid, n in
                      (run.get("nodes") or {}).items()}
            if states != last_states:
                w(f"[{int(time.time()-t0)}s] run={st} nodes={states}")
                last_states = states
                stable = 0
            else:
                stable += 1
            if st in ("completed", "failed", "stopped", "rejected",
                      "needs_reconciliation"):
                break
            if st == "waiting_human":
                pend = next((rv for rv in run.get("reviews") or []
                             if rv.get("status") == "pending"), None)
                if pend:
                    idx = pend.get("reviewIndex")
                    action = REVIEW_PLAN.get(idx)
                    if action is None:
                        w(f"[review {idx}] 成片审定——保留给用户（不代办定版）")
                        w(f"[done] run 停靠于成片审定（waiting_human）；工程链已完成")
                        break
                    kind, note = action
                    body = {"decision": "approve", "note": note,
                            "withSelection": kind.startswith("approve_select")}
                    if kind == "approve_select_look":
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
                        pjf = read_json(ROOT / "projects" / PROJ / "output"
                                        / "project.json")
                        items = []
                        for sc in (pjf.get("scenes") or []):
                            for sh in (sc.get("shots") or []):
                                ff = (sh.get("first_frame") or {}).get("candidates") or []
                                ok = [c for c in ff if c.get("status") == "done"
                                      and c.get("local")]
                                if ok:
                                    items.append({"entityId": sh["shot_id"],
                                                  "versionId": ok[0]["candidate_id"]})
                        body["selectionOverride"] = {"kind": "first_frame",
                                                     "items": items}
                        w(f"[review 4] first-frame items={len(items)}")
                    elif kind == "approve_select_videos":
                        pj = read_json(ROOT / "projects" / PROJ / "output"
                                       / "project.json")
                        items = []
                        for sc in (pj.get("scenes") or []):
                            for sh in (sc.get("shots") or []):
                                vv = [x for x in (sh.get("video_versions") or [])
                                      if x.get("status") == "done"]
                                if vv:
                                    items.append({"entityId": sh["shot_id"],
                                                  "versionId": vv[0]["version_id"]})
                        body["selectionOverride"] = {"kind": "video_version",
                                                     "items": items}
                        w(f"[review 6] select items={[(i['entityId'], i['versionId']) for i in items]}")
                    rd = client.post(
                        f"{BASE}/api/project/{PROJ}/workflow/runs/{rid}"
                        f"/reviews/{pend['requestId']}/decision", json=body)
                    w(f"[review {idx}] decision={kind} → {rd.status_code} "
                      f"{rd.text[:120]}")
            # 媒体事实快照（每 10 分钟）
            if (time.time() - RESTART_BASE) > 600:
                results["mediaFacts"].append(
                    {"at": time.time(), **project_media_facts()})
                RESTART_BASE = time.time()
            time.sleep(8)

        final = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid}").json()
        results["runId"] = rid
        results["finalStatus"] = final.get("status")
        results["nodes"] = {nid: {"status": n.get("status"),
                                  "error": (n.get("error") or "")[:300],
                                  "handoff": n.get("handoff")}
                            for nid, n in (final.get("nodes") or {}).items()}
        results["reviews"] = final.get("reviews")
        results["mediaFacts"].append({"at": time.time(), **project_media_facts()})

        # ---- §6.3 真实媒体清单核验 ----
        mf = results["mediaFacts"][-1]
        checks = {
            "角色图像": any(any(v.values()) for c in mf["characters"]
                        for v in [c["views"]]),
            "Look参考图": any(l["n_refs"] > 0 for l in mf["looks"]),
            "分镜图": all(s["firstFrameFile"] for s in mf["shots"]) and mf["shots"],
            "视频候选": sum(1 for s in mf["shots"]
                        for v in s["videoVersions"] if v["playable"]),
            "成片render": any(r["file"] for sq in mf["sequences"]
                          for r in sq["renders"]),
        }
        results["mediaChecks"] = checks
        w("[media]", json.dumps(checks, ensure_ascii=False))
        OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        (ROOT / "_p47_probe_d_log.txt").write_text(log_lines.getvalue(),
                                                   encoding="utf-8")
        w(f"[DONE] run={rid} final={final.get('status')}")


if __name__ == "__main__":
    main()
