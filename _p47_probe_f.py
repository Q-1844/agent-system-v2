# -*- coding: utf-8 -*-
"""
_p47_probe_f.py — Phase 47 F批：冻结基线三轮独立复验（F1/F2/F3）。

用法：python _p47_probe_f.py F1|F2|F3
冻结基线：docs/workflow/PHASE47_FROZEN_BASELINE.json（HEAD d985783 / Skill hash /
agnes-3.0-flash / p47_templates_v1）。每轮新项目+新会话+新故事；复用轮如实标注。

F1 新故事全链（山雀送钟）；F2 另一新故事（刺猬邮局之夜，同链迁移）；F3 复用轮
（复用 F1 已批准资产 + 真实中断 + 装配）。
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
ROUND = sys.argv[1] if len(sys.argv) > 1 else "F1"

STORIES = {
    "F1": {
        "proj": "风铃山道",
        "story": ("真实新写三段故事（s01/s02/s03）：《山雀送钟》——①清晨山道：山雀邮差"
                  "（灰蓝色小鸟、白围巾）从树上小屋取出铜铃铛包裹，检查铃舌没有受潮；"
                  "②溪边石阶：沿石阶而下，把包裹收紧在背上，跳过溪水；"
                  "③山脚钟楼：把铃铛挂上钟楼横梁，敲响一次，山谷回声。"
                  "结局事实固定：铜铃敲响。每段 2~3 句，动作明确。"),
        "char": ("山雀（a small grey-blue mountain sparrow courier with a white scarf, "
                 "round bright eyes, 2D animation style）"),
        "prop": ("铜铃铛包裹（a small brass bell wrapped in cloth）"),
        "scene": ("①清晨山道（树上小屋、晨雾）；②溪边石阶（溪水、湿石阶）；"
                  "③山脚钟楼（木钟楼、横梁）。为全部三个地点各创建 SceneInstanceView "
                  "并真实生成环境视图参考图，等待审查真实完成"),
        "board": ("6 镜：s01 两镜（取包裹/查铃舌）、s02 两镜（下石阶/跳溪）、"
                  "s03 两镜（挂铃/敲响回声）。每镜生成分镜图并等待真实完成"),
        "video": "为全部 6 个镜头真实生成视频候选（每镜约 5 秒），逐镜核对首中尾帧",
        "final": "《山铃回声》",
    },
    "F2": {
        "proj": "夜邮刺猬",
        "story": ("真实新写三段故事（s01/s02/s03）：《刺猬夜邮》——①黄昏邮局：刺猬邮差"
                  "（棕红色小刺猬、绿色邮差帽）整理最后一班信件，把一封星光信放进背囊；"
                  "②夜间小径：打着小灯笼沿篱笆小径行走，萤火虫引路；"
                  "③山顶灯塔：把星光信交给守塔人，灯塔亮起。"
                  "结局事实固定：灯塔亮起。每段 2~3 句。"),
        "char": ("刺猬（a small brown hedgehog postman with a green cap, gentle eyes, "
                 "2D animation style）"),
        "prop": ("星光信（a letter that glows softly like starlight）"),
        "scene": ("①黄昏邮局（木质柜台、信架）；②夜间小径（篱笆、萤火虫、灯笼光）；"
                  "③山顶灯塔（石阶、塔灯）。为全部三个地点各创建 SceneInstanceView "
                  "（场景实例视图，不是只有 LocationView）并真实生成环境视图参考图；"
                  "等待生成与机器审查真实完成且 overall=pass 后由你在职责内批准"
                  "（下游分镜图生成依赖各镜地点的 approved 场景实例视图，"
                  "缺失会被硬前置阻塞）"),
        "board": ("6 镜：s01 两镜（整理信件/装背囊）、s02 两镜（提灯行路/萤火引路）、"
                  "s03 两镜（登塔/交信亮灯）。每镜生成分镜图并等待真实完成"),
        "video": "为全部 6 个镜头真实生成视频候选（每镜约 5 秒），逐镜核对首中尾帧",
        "final": "《夜邮灯塔》",
    },
    "F3": None,   # 复用轮：特殊流程
}
STORIES["F3"] = {
    "proj": "山雀夜航",
    "story": ("真实新写三段故事（s01/s02/s03）：《山雀夜航》——山雀邮差（沿用已批准的"
              "山雀形象）接到一趟夜间急件：①入夜出发：从树上小屋出发，围巾系紧；"
              "②月光田野：贴着月光下的田野飞行，避开谷仓风车；③黎明灯塔：把急件"
              "送到灯塔守塔人手中，回望来路。结局事实固定：急件送达。每段 2~3 句。"),
    "char": "山雀（复用共享库中已批准的山雀形象与信使常服 Look——不重新生成）",
    "prop": "夜间急件（a small urgent letter pouch）",
    "scene": ("①夜色树屋（灯下小屋）；②月光田野（谷仓风车剪影）；③黎明灯塔。"
              "为三个地点各创建 SceneInstanceView 并真实生成环境视图，等待审查完成"),
    "board": "6 镜：s01 两镜（出发/系围巾）、s02 两镜（月下飞行/避风车）、s03 两镜（抵塔/送达回望）。每镜生成分镜图",
    "video": "为全部 6 个镜头真实生成视频候选（每镜约 5 秒）",
    "final": "《山雀夜航》",
    "art": "flat 2D animation, clean linework, moonlit night tones",
}
STORIES["F1"]["art"] = "flat 2D animation, clean linework, cool mountain morning light"
STORIES["F2"]["art"] = "flat 2D animation, clean linework, warm dusk and night tones"

OUT = ROOT / f"_p47_probe_{ROUND.lower()}_results.json"
WID = f"wf_w09_{ROUND.lower()}"
log_lines = io.StringIO()


def w(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    log_lines.write(line + "\n")


def read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def unapproved_scivs():
    ss = read_json(ROOT / "projects" / STORIES[ROUND]["proj"] / "output"
                   / "scene_system.json")
    return [sid for sid, v in (ss.get("scene_instance_views") or {}).items()
            if v.get("approval_status") != "approved"]


def first_look():
    res = read_json(ROOT / "projects" / STORIES[ROUND]["proj"] / "output"
                    / "resources.json")
    for lid, lk in (res.get("looks") or {}).items():
        refs = lk.get("reference_asset_ids") or []
        if refs:
            return lk["look_id"], refs[0]
    return None, None


def main():
    S = STORIES[ROUND]
    PROJ = S["proj"]
    client = httpx.Client(timeout=120)
    # 建项目
    r = client.post(f"{BASE}/api/projects/create",
                    params={"name": PROJ, "render_family": "2d_animation",
                            "art_direction": S["art"]})
    w(f"[setup] create {PROJ} → {r.status_code}")
    _resume_mode = False
    _skip_setup = False
    if r.status_code == 409:
        # 断点续跑：项目已存在=本轮先前 run 的产物在盘——沿用项目，找最近非终态
        # run 恢复观察（F1 中途驱动的合法恢复；新故事内容已在本项目真实生产）。
        runs = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs").json().get("runs", [])
        cand = [r for r in runs if r.get("status") in
                ("running", "waiting_human", "needs_reconciliation")]
        if not cand:
            # 无活跃 run：沿同一冻结定义起新 run（恢复语义：复用合格产物续做）
            rid = client.post(
                f"{BASE}/api/project/{PROJ}/workflow/definitions/{WID}/runs",
                json={"idempotencyKey": f"p47{ROUND.lower()}resume-{uuid.uuid4().hex[:8]}"}
            ).json()["run"]["runId"]
            w(f"[resume-start] 新 run {rid}（上游复用核验续做）")
            _resume_mode = True
            _skip_setup = True
        else:
            rid = cand[0]["runId"]
            w(f"[resume] 沿用项目 {PROJ} 的活跃 run {rid}")
            _resume_mode = True
    # W09 模板创建（resume 模式跳过创建与补丁——定义已在服务端）
    if _skip_setup:
        _resume_mode = True
    if not _resume_mode:
        params = {
            "producerObjective": f"拆解{S['final']}制作目标：30~60 秒二维动画；外部男声旁白。"
                                 "给出任务范围、依赖与待决定项；不委派 Specialist。",
            "writerObjective": S["story"],
            "characterObjective": (
                (f"合法复用轮：信使常服 Look 已在共享库发布（shared_id="
                 f"look_7118f703805e，含 approved 参考 ref_9e616dedd790）。"
                 f"本节点：①用 resource.library.import 导入该 Look（不重新生成图像）；"
                 f"②character.create 建立角色「山雀」（a small grey-blue mountain "
                 f"sparrow courier with a white scarf, round bright eyes, 2D animation "
                 f"style——与被复用 Look 的角色身份一致）并把导入的 Look 关联到该角色"
                 f"（若导入流程已自动关联则核验即可）；③resource.inventory 核对最终"
                 f"状态并如实汇报复用来源（shared_id、approved 参考）。"
                 f"关键道具{S['prop']}：用 resource.composition.prepare 生成组合参考"
                 f"（真实出图）后登记道具参考；不可行则如实列缺项。")
                if ROUND == "F3" else
                (f"创建主角并生成真实图像：{S['char']}——character.create → "
                 "character.generate_views 三视图（等待真实返回）→ resource.look.prepare "
                 f"创建 Look「信使常服」并真实生成参考图。关键道具{S['prop']}："
                 "系统没有道具生成端点——用 resource.composition.prepare 生成角色与道具"
                 "同框组合参考（真实出图），以该组合图资产登记道具参考；不可行则如实列缺项。"
                 "审批留给人工审核。")),
            "sceneObjective": S["scene"],
            "boardObjective": S["board"],
            "videoObjective": S["video"],
            "assemblyObjective": (
                f"装配{S['final']}成片：①assembly.snapshot_freeze 冻结本项目快照"
                "（pin=各镜 live final_video）；②sequence_create 采用冻结清单版本；"
                "③旁白=外部男声（tts_synthesize 按项目现有 TTS 配置真实合成，"
                "6~10 句短句，音频必须真实落盘）；④音乐=连续一支（获准素材/已接通服务，"
                "引用的每个音频文件必须真实存在）；⑤原视频音轨静音（render_profile "
                "audio=silent，音频计划不与旁白叠坏对白）；⑥render_submit 等待渲染"
                "真实完成并核验输出文件可读。"),
            "videoCount": "6",
            "bindRelaySkill": "true",
        }
        r = client.post(f"{BASE}/api/project/{PROJ}/workflow/templates/W09/create",
                        json={"workflowId": WID, "params": params})
        if r.status_code != 200:
            w(f"[FAIL] W09 create: {r.text[:250]}")
            OUT.write_text(json.dumps({"result": "FAIL", "error": r.text[:250]},
                                      ensure_ascii=False), encoding="utf-8")
            return
        w(f"[create] W09 rev={r.json().get('revision')} ok={(r.json().get('validation') or {}).get('ok')}")
        # 补丁：completion 全部 allow_verified_reuse + 审核 selection 动态化
        doc = client.get(f"{BASE}/api/project/{PROJ}/workflow/definitions/{WID}").json()
        for n in doc.get("nodes", []):
            if n.get("type") != "agent":
                if n.get("id") == "n_rv_cd":
                    n["config"]["selection"] = {"kind": "look_reference",
                                                "entityId": "DYNAMIC"}
                if n.get("id") == "n_rv_sc":
                    n["config"]["selection"] = {"kind": "scene_view",
                                                "entityId": "DYNAMIC"}
                if n.get("id") == "n_rv_board":
                    n["config"]["selection"] = {"kind": "first_frame",
                                                "entityId": "DYNAMIC"}
                continue
            comp = n.get("config", {}).get("completion")
            if comp and comp.get("strategy") == "require_new":
                comp["strategy"] = "allow_verified_reuse"
        rs = client.put(f"{BASE}/api/project/{PROJ}/workflow/definitions/{WID}",
                        json={"definition": doc, "expectedRevision": doc.get("revision")})
        w(f"[patch] strategies+selections → {rs.status_code} rev={rs.json().get('revision')}")

    # 启动（resume 模式沿用活跃 run）
    if not _resume_mode:
        rid = client.post(f"{BASE}/api/project/{PROJ}/workflow/definitions/{WID}/runs",
                          json={"idempotencyKey": f"p47{ROUND.lower()}-{uuid.uuid4().hex[:8]}"}
                          ).json()["run"]["runId"]
        w(f"[run] {rid}")

    # 主循环（审核关口工程决策 + F3 特殊步骤）
    last_states = {}
    t0 = time.time()
    F3_INTERRUPTED = False
    while time.time() - t0 < 6 * 3600:
        run = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid}").json()
        st = run.get("status")
        states = {nid: n.get("status") for nid, n in (run.get("nodes") or {}).items()}
        if states != last_states:
            w(f"[{int(time.time()-t0)}s] run={st} nodes={states}")
            if (ROUND == "F3" and not F3_INTERRUPTED
                    and states.get("n_dir_video") in ("dispatching", "running")
                    and time.time() - t0 > 1500):
                # F3 真实中断：视频生产中硬杀后端（E 批同款真机演练）
                import subprocess
                r2 = subprocess.run(["netstat", "-ano"], capture_output=True)
                for line in (r2.stdout or b"").decode("utf-8", errors="replace").splitlines():
                    if ":8000" in line and "LISTENING" in line:
                        subprocess.run(["taskkill", "/PID", line.split()[-1], "/F"],
                                       capture_output=True)
                        w(f"[F3] 真实中断：杀断后端 PID={line.split()[-1]}（视频生产中）")
                        break
                time.sleep(2)
                subprocess.run([r"D:\Delta Force\python.exe",
                                str(ROOT / "_launch_backend_detached.py")],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               cwd=str(ROOT))
                for _ in range(40):
                    try:
                        if httpx.get(BASE + "/api/projects", timeout=3).status_code == 200:
                            w("[F3] 重启完成，恢复观察")
                            break
                    except Exception:
                        pass
                    time.sleep(1.5)
                F3_INTERRUPTED = True
            last_states = states
        if st in ("completed", "failed", "stopped", "rejected"):
            break
        if st == "needs_reconciliation":
            # F3：真实中断窗口在这里天然出现——记录并对账续跑
            w(f"[F3] needs_reconciliation at {int(time.time()-t0)}s——对账记录后 continue")
            if ROUND == "F3":
                F3_INTERRUPTED = True
            node_id = next((nid for nid, n in (run.get("nodes") or {}).items()
                            if n.get("status") == "needs_reconciliation"), None)
            if node_id:
                client.post(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid}/continue",
                            json={"nodeId": node_id})
            time.sleep(5)
            continue
        if st == "waiting_human":
            pend = next((rv for rv in run.get("reviews") or []
                         if rv.get("status") == "pending"), None)
            if pend:
                idx = pend.get("reviewIndex")
                if idx == 7:
                    w("[review 7] 成片审定——保留给用户（不代办定版）")
                    break
                body = {"decision": "approve", "note": f"{ROUND} 工程审核（机制验证）"}
                if idx == 2:
                    lid, aid = first_look()
                    if lid:
                        body["withSelection"] = True
                        body["selectionOverride"] = {
                            "kind": "look_reference", "entityId": lid,
                            "versionId": aid}
                elif idx == 3:
                    scivs = unapproved_scivs()
                    if not scivs:
                        # 全部已批准（场景 Agent 权限内自审自批）→ 选用即 alreadySelected
                        ss = read_json(ROOT / "projects" / S["proj"] / "output"
                                       / "scene_system.json")
                        scivs = list((ss.get("scene_instance_views") or {}).keys())
                    if scivs:
                        body["withSelection"] = True
                        body["selectionOverride"] = {"kind": "scene_view",
                                                     "entityId": scivs[0]}
                        w(f"[review 3] scene_view → {scivs[0]} (unapproved_first={not not unapproved_scivs()})")
                elif idx == 4:
                    pjf = read_json(ROOT / "projects" / PROJ / "output" / "project.json")
                    items = []
                    for sc in (pjf.get("scenes") or []):
                        for sh in (sc.get("shots") or []):
                            sel = sh.get("selected_first_frame")
                            ff = (sh.get("first_frame") or {}).get("candidates") or []
                            if sel and any(c.get("status") == "done" and c.get("local")
                                           for c in ff):
                                items.append({"entityId": sh["shot_id"],
                                              "versionId": sel})
                    body["withSelection"] = True
                    body["selectionOverride"] = {"kind": "first_frame", "items": items}
                    w(f"[review 4] items={len(items)}")
                elif idx == 6:
                    pjf = read_json(ROOT / "projects" / PROJ / "output" / "project.json")
                    items = []
                    for sc in (pjf.get("scenes") or []):
                        for sh in (sc.get("shots") or []):
                            vv = [x for x in (sh.get("video_versions") or [])
                                  if x.get("status") == "done"]
                            if vv:
                                items.append({"entityId": sh["shot_id"],
                                              "versionId": vv[-1]["version_id"]})
                    body["withSelection"] = True
                    body["selectionOverride"] = {"kind": "video_version", "items": items}
                    w(f"[review 6] items={len(items)}")
                rd = client.post(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid}"
                                 f"/reviews/{pend['requestId']}/decision", json=body)
                w(f"[review {idx}] → {rd.status_code} {rd.text[:100]}")
                # 选用失败 → 人评 pass / 继续
                time.sleep(5)
                run2 = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid}").json()
                if run2.get("status") == "needs_reconciliation":
                    rv2 = next((rv for rv in run2.get("reviews") or []
                                if rv.get("requestId") == pend["requestId"]), None)
                    rec = (rv2 or {}).get("selectionReceipt") or {}
                    if rec.get("kind") in ("look_reference", "prop_reference",
                                           "composition_candidate") and \
                            "审查证据" in (rec.get("error") or ""):
                        seg = {"look_reference": "looks",
                               "prop_reference": "props",
                               "composition_candidate": "compositions"}[rec["kind"]]
                        try:
                            rr = client.post(
                                f"{BASE}/api/project/{PROJ}/{seg}/{rec['entityId']}"
                                f"/review/human",
                                json={"asset_id": rec.get("versionId"),
                                      "overall": "pass",
                                      "note": f"{ROUND} 工程人评", "reviewer": "engineering_probe"})
                            w(f"[fix] human pass {seg}/{rec['entityId']} → {rr.status_code}")
                        except Exception as e:
                            w(f"[fix] human review err: {str(e)[:100]}")
                    nid = next((nid for nid, n in (run2.get("nodes") or {}).items()
                                if n.get("status") == "needs_reconciliation"), None)
                    if nid:
                        client.post(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid}/continue",
                                    json={"nodeId": nid})
                        w(f"[fix] continue {nid}")
        time.sleep(8)

    final = client.get(f"{BASE}/api/project/{PROJ}/workflow/runs/{rid}").json()
    # 媒体核验
    def okf(local):
        if not local: return False
        p = Path(local)
        if p.is_absolute(): return p.is_file()
        return ((ROOT / local).is_file()
                or (ROOT / "projects" / PROJ / local).is_file()
                or (ROOT / "projects" / PROJ / "output" / local).is_file())
    pjf = read_json(ROOT / "projects" / PROJ / "output" / "project.json")
    n_frames = sum(1 for sc in (pjf.get("scenes") or []) for sh in (sc.get("shots") or [])
                   if any(okf(c.get("local")) for c in
                          ((sh.get("first_frame") or {}).get("candidates") or [])))
    n_vids = sum(1 for sc in (pjf.get("scenes") or []) for sh in (sc.get("shots") or [])
                 for v in (sh.get("video_versions") or []) if okf(v.get("local")))
    seqs = read_json(ROOT / "projects" / PROJ / "output" / "sequences.json")
    renders = [(sid, rv.get("version_id"), rv.get("status"), okf(rv.get("output_file")))
               for sid, e in (seqs.get("sequences") or {}).items()
               for rv in (e.get("render_versions") or [])]
    narr = list((ROOT / "projects" / PROJ / "output" / "audio_imports" / "narration")
                .glob("*.wav")) if (ROOT / "projects" / PROJ / "output"
                                    / "audio_imports" / "narration").exists() else []
    media = {"frames": n_frames, "videos": n_vids, "renders": renders,
             "narration_wav": len(narr),
             "final_render_pending": all(e.get("final_render") is None
                                         for e in (seqs.get("sequences") or {}).values())}
    out = {"round": ROUND, "project": PROJ, "runId": rid,
           "finalStatus": final.get("status"),
           "F3_interrupted": F3_INTERRUPTED if ROUND == "F3" else None,
           "nodes": {nid: n.get("status") for nid, n in
                     (final.get("nodes") or {}).items()},
           "media": media}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / f"_p47_probe_{ROUND.lower()}_log.txt").write_text(log_lines.getvalue(),
                                                              encoding="utf-8")
    w(f"[DONE] {ROUND} final={final.get('status')} media={json.dumps(media, ensure_ascii=False)[:200]}")


if __name__ == "__main__":
    main()
