# _p48_a_build_ledger.py — Phase48 A批：可重算候选账本（只读真实项目事实，不推算历史）。
# 范围：Phase47 四部整链影片（风后邮站/风铃山道/夜邮刺猬/山雀夜航）+ P47_C_Templates 的
# W05/W06/W07 真实视频产物。缺记录的字段如实 null（不捏造合格率）。
import hashlib
import json
import time
from pathlib import Path

OUT = {}

FILMS = [("风后邮站", "run_9cc683f2ce6c"), ("风铃山道", "run_181eff42300e"),
         ("夜邮刺猬", "run_13e741c1c934"), ("山雀夜航", "run_fb5e2c27eac8")]


def _file_facts(p):
    try:
        if p.is_file() and p.stat().st_size > 0:
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            return {"file_ok": True, "bytes": p.stat().st_size,
                    "sha256": h.hexdigest()}
    except OSError:
        pass
    return {"file_ok": False, "bytes": 0, "sha256": None}


def _run_attempt_verdicts(run_path):
    try:
        run = json.load(open(run_path, encoding="utf-8"))
    except Exception:
        return []
    out = []
    for nid, n in (run.get("nodes") or {}).items():
        if n.get("type") != "agent":
            continue
        for att in n.get("attempts") or []:
            ov = ((att.get("outputs") or {}).get("outputsVerdict") or {})
            out.append({"node": nid, "attempt": att.get("attemptId"),
                        "status": att.get("status"),
                        "outputs_code": ov.get("code"),
                        "created": ov.get("created"), "reused": ov.get("reused"),
                        "error_head": (att.get("error") or "")[:160] or None})
    return out


def _handoff_selections(run_path):
    try:
        run = json.load(open(run_path, encoding="utf-8"))
    except Exception:
        return []
    sels = []
    for nid, n in (run.get("nodes") or {}).items():
        ps = n.get("pendingSelection") or {}
        if ps.get("state") == "done" and ps.get("receipt"):
            rc = ps["receipt"]
            sels.append({"node": nid, "kind": rc.get("kind"),
                         "ok": rc.get("ok"),
                         "items": rc.get("items") or None,
                         "entityId": rc.get("entityId"),
                         "versionId": rc.get("versionId")})
    return sels


def _narration_facts(pdir):
    out = {"policy": "Phase47 纪律：成片旁白=TTS（MiniMax speech-2.8-turbo），原视频音轨静音；BGM=外部配乐（四片均含）",
           "cues": None, "provider_model": None}
    nf = pdir / "output" / "narration_audio.json"
    if nf.is_file():
        try:
            d = json.loads(nf.read_text(encoding="utf-8"))
            cues = d.get("cues") or {}
            out["cues"] = len(cues)
            for c in cues.values():
                for v in c.get("versions") or []:
                    out["provider_model"] = f"{v.get('provider')}/{v.get('model')}"
                    break
                if out["provider_model"]:
                    break
        except Exception:
            pass
    return out


ledger = {"schema": "phase48_candidate_ledger_v1",
          "built_at": time.time(),
          "source": "只读真实项目文件（project.json/sequences.json/workflow runs/媒体 hash）",
          "first_pass_definition": {
              "首次候选": "某镜头在其所属 workflow run 的首次 attempt 窗口内产生的第一个视频版本",
              "合格": "可播放（本地文件可读）且（有 visual review pass 回执 或 用户选用为 final_video）——两者缺一按未知计",
              "分母注记": "仅对 run 记录完整、时间窗可对齐的镜头计首次合格率；记录缺失者标 unknown，不从成片数倒推"},
          "films": [], "c3_fixtures": [], "unknowns": []}

for proj_name, run_id in FILMS:
    pdir = Path("projects") / proj_name
    pj = json.loads((pdir / "output" / "project.json").read_text(encoding="utf-8"))
    run_path = pdir / "output" / "workflows" / "runs" / f"{run_id}.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    # attempt 窗口（用于把视频版本对齐到 run）
    windows = []
    for nid, n in (run.get("nodes") or {}).items():
        for att in n.get("attempts") or []:
            if att.get("startedAt") and att.get("endedAt"):
                windows.append((nid, att["startedAt"], att["endedAt"]))
    seqs = json.loads((pdir / "output" / "sequences.json").read_text(encoding="utf-8"))
    seqs = seqs.get("sequences", seqs)
    shots_out = []
    for sc in pj.get("scenes", []):
        for sh in sc.get("shots") or []:
            vvs = []
            first_in_run = None
            for i, vv in enumerate(sh.get("video_versions") or []):
                ff = _file_facts(pdir.parent / vv["local"]) if vv.get("local") else {"file_ok": False}
                # local 形如 projects\\{p}\\output\\videos\\x.mp4（仓库根相对）
                lp = Path(vv["local"])
                if not lp.is_absolute():
                    cand = Path(".") / lp
                    ff = _file_facts(cand) if cand.is_file() else ff
                in_run = any(nid for nid, s, e in windows
                             if s <= (vv.get("created_at") or 0) <= e)
                if in_run and first_in_run is None:
                    first_in_run = vv.get("version_id")
                vvs.append({
                    "version_id": vv.get("version_id"),
                    "created_at": vv.get("created_at"),
                    "model": vv.get("model"),
                    "provider_task_id": vv.get("video_id") or vv.get("task_id"),
                    "file_ok": ff["file_ok"], "bytes": ff["bytes"],
                    "sha256": ff["sha256"],
                    "seconds_returned": vv.get("seconds_returned"),
                    "size_returned": vv.get("size_returned"),
                    "origin_agent_run": vv.get("origin_run_id"),
                    "aligned_workflow_node": in_run,
                    "is_first_candidate_of_run": (in_run and vv.get("version_id") == first_in_run),
                    "selected_final": (sh.get("final_video") == vv.get("version_id")),
                    "previous_submit_uncertain": bool(vv.get("previous_submit_uncertain")),
                })
            shots_out.append({"shot": sh.get("shot_id"),
                              "final_video": sh.get("final_video"),
                              "n_versions": len(vvs), "versions": vvs})
    renders = []
    final_render = None
    for sid, entry in seqs.items():
        if not isinstance(entry, dict):
            continue
        for rv in entry.get("render_versions", []):
            lp = Path(rv["output_file"]) if rv.get("output_file") else None
            ff = _file_facts(lp) if lp else {"file_ok": False}
            renders.append({"sequence": sid, "version_id": rv.get("version_id"),
                            "status": rv.get("status"), **ff,
                            "is_final": (entry.get("final_render") == rv.get("version_id"))})
            if entry.get("final_render") == rv.get("version_id"):
                final_render = rv.get("version_id")
    ledger["films"].append({
        "project": proj_name, "workflow_run": run_id,
        "run_status": run.get("status"),
        "final_review_state": next(
            (r.get("status") for r in run.get("reviews", [])
             if r.get("nodeId") == "n_rv_final"), None),
        "final_render_user_selected": final_render,
        "attempt_verdicts": _run_attempt_verdicts(run_path),
        "selection_receipts": _handoff_selections(run_path),
        "narration": _narration_facts(pdir),
        "shots": shots_out, "renders": renders,
    })

# ---- C3 夹具（W05 生成 / W06 修复 / W07 桥接）----
pj = json.loads(Path("projects/P47_C_Templates/output/project.json").read_text(encoding="utf-8"))
for sc in pj.get("scenes", []):
    for sh in sc.get("shots") or []:
        for vv in sh.get("video_versions") or []:
            lp = Path(vv["local"]) if vv.get("local") else None
            ff = _file_facts(lp) if lp else {"file_ok": False}
            ledger["c3_fixtures"].append({
                "project": "P47_C_Templates", "shot": sh.get("shot_id"),
                "version_id": vv.get("version_id"),
                "created_at": vv.get("created_at"),
                "model": vv.get("model"),
                "provider_task_id": vv.get("video_id") or vv.get("task_id"),
                "file_ok": ff["file_ok"], "bytes": ff["bytes"],
                "origin_agent_run": vv.get("origin_run_id"),
                "selected_final": (sh.get("final_video") == vv.get("version_id")),
            })
w06_run = Path("projects/P47_C_Templates/output/workflows/runs/run_7f30add8a220.json")
w07_run = Path("projects/P47_C_Templates/output/workflows/runs/run_10e21ba7f1cb.json")
ledger["repair_targets"] = {
    "W06_run_7f30add8a220": {
        "problem_version": "vv_2855ff25",
        "problem": "visual.sample_frames suspected=1（beginning→early_middle 纸张过渡压缩）",
        "repair_candidate": "vv_47dd2ea1",
        "attempt_verdicts": _run_attempt_verdicts(w06_run)},
    "W07_run_10e21ba7f1cb": {
        "endpoints": ["s01_c02", "s02_c01"],
        "bridge_candidate": "vv_53d832b2",
        "attempt_verdicts": _run_attempt_verdicts(w07_run)},
}
# 已知未知项（A03：不捏造）
ledger["unknowns"] = [
    "各版本逐条 visual.review_shot 明细回执未随版本持久化（在 attempt 事件流内）——逐版本合格判定标 unknown，仅 attempt 级 verdict 可重算",
    "Phase47 早期（B/C 批）部分探针 run 无完整 attempt 记录——不计入首次合格率分母",
    "用户对四部成片的审美验收未发生（成片审定 pending）——不写 PASS",
]
Path("docs/workflow/PHASE48_A_CANDIDATE_LEDGER.json").write_text(
    json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
n_films = len(ledger["films"])
n_vv = sum(len(f["shots"]) for f in ledger["films"])
n_ver = sum(s["n_versions"] for f in ledger["films"] for s in f["shots"])
sel = sum(1 for f in ledger["films"] for s in f["shots"] for v in s["versions"] if v["selected_final"])
print(f"ledger built: films={n_films} shots={n_vv} video_versions={n_ver} selected_final={sel} c3={len(ledger['c3_fixtures'])}")
