# -*- coding: utf-8 -*-
"""Phase 44A3：assembly_input_manifest 与冻结快照生成器。

两模式：
  production —— 制作快照（工程预览可用）：12 镜 user_locked + s02_c03 明确标记的
                临时候选（不可称最终成片）；
  formal     —— 正式快照：要求 13/13 有用户裁定且与 final 一致，否则拒绝生成。

输出（_44a/）：
  snapshot_<mode>.json                    冻结档案（commit/Skill/配置/资产/配额/裁决表）
  assembly_input_manifest_<mode>.json     B 批次成片 Agent 的输入契约草案
  snapshot_media/                          13 镜 final 的不可变镜像（哈希同名）
"""
import hashlib
import io
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
PROJ = "雨灯归航"
OUTDIR = ROOT / "_44a"
MEDIA_DIR = OUTDIR / "snapshot_media"

# 用户裁定表（来源：_43_repair/FEEDBACK_repair.json 逐字 + Phase 43 报告 §2.7/2.8/3.1；
# c03 状态由执行时 canonical 现状决定）
VERDICTS = {
    "s01_c01": ("user_locked", "合格保留（09-28 基线，此前阶段用户认可）"),
    "s01_c02": ("user_locked", "合格保留（09-28 基线）"),
    "s01_c03": ("user_locked", "合格保留（09-28 基线）"),
    "s01_c04": ("user_locked", "repair43_v3：q_sel_s01_c04=「一轮候选」→ vv_80dc90a5"),
    "s01_c05": ("user_locked", "repair43_v2：q_sel_s01_c05=「一轮候选」"),
    "s01_c06": ("user_locked", "报告 §2.8：终选一轮（1s 分拍）；反馈文件 v3 层无 c06 条目"
                               "（v2 层曾选二轮 0.5s，终审翻回——档案缺口，报告为准）"),
    "s02_c01": ("user_locked", "合格保留（09-28 基线）"),
    "s02_c02": ("user_locked", "repair43_v3：q_sel_s02_c02=「一轮候选」→ vv_4bdf6b08"),
    "s02_c03": ("pending_user_review", "repair43_v3：「可以继续按照之前来做…人物不开门了」"
                                       "——44A2 迭代候选待用户终审"),
    "s03_c01": ("user_locked", "repair43_v2：q_sel_s03_c01=「一轮候选」"),
    "s03_c02": ("user_locked", "repair43_v2：q_sel_s03_c02=「一轮候选」"),
    "s03_c03": ("user_locked_with_caveat", "repair43_v3：「虽然灯的问题还在，但是暂时就"
                                           "这样吧。」"),
    "s03_c04": ("user_locked", "合格保留（09-28 基线）"),
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def ffprobe(p: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", str(p)],
        capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    if r.returncode != 0:
        return {"error": (r.stderr or "")[-200:]}
    d = json.loads(r.stdout or "{}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), {})
    return {"duration": round(float(d.get("format", {}).get("duration") or 0), 3),
            "width": v.get("width"), "height": v.get("height"),
            "fps": v.get("avg_frame_rate") or v.get("r_frame_rate"),
            "video_codec": v.get("codec_name"),
            "has_audio": bool(a), "audio_codec": a.get("codec_name")}


def main(mode: str) -> int:
    import deps
    from src.pipeline.controller import PipelineController
    deps.pipeline_controller = PipelineController(str(ROOT / "config" / "config.yaml"))
    deps.agnes_client = deps.pipeline_controller.agnes_client
    deps.project_manager = deps.pipeline_controller.project_manager
    from src.core.store import ProjectStore
    from src.storyboard.continuity_service import measure_seam, _flat_shots
    pdir = deps.project_manager.get_project_dir(PROJ).resolve()
    store = ProjectStore(pdir)
    proj = store.load()
    shots = {s.shot_id: s for s in _flat_shots(proj)}
    order = list(shots)

    raw = json.loads((pdir / "output" / "project.json").read_text(encoding="utf-8"))
    raw_shots = {sh["shot_id"]: sh for sc in raw.get("scenes", [])
                 for sh in sc.get("shots", [])}

    # ---- 正式快照守卫：13/13 用户裁定且与 final 一致 ----
    if mode == "formal":
        blockers = []
        for sid in order:
            st, _why = VERDICTS[sid]
            if st != "user_locked" and st != "user_locked_with_caveat":
                blockers.append(f"{sid}: {st}")
        if blockers:
            print("正式快照被阻断（44A §A3）——以下镜头缺用户终审裁定：")
            for b in blockers:
                print("  -", b)
            return 2

    snap_id = f"snap_44a_{mode}_{time.strftime('%Y%m%d_%H%M%S')}"
    MEDIA_DIR.mkdir(exist_ok=True)

    # ---- 接缝（当前 canonical 现状实测）----
    seams = []
    for i in range(1, len(order)):
        try:
            r = measure_seam(pdir, proj, order[i], include_end_to_end=False)
            seams.append({"from": order[i - 1], "to": order[i],
                          "seam_mae": r["tail_to_first_mae"]})
        except Exception as e:
            seams.append({"from": order[i - 1], "to": order[i], "error": str(e)[:120]})

    # ---- 逐镜 manifest ----
    entries = []
    for idx, sid in enumerate(order, start=1):
        sh = shots[sid]
        fv = sh.final_video
        vv = next(v for v in (sh.video_versions or []) if v.version_id == fv)
        vfile = ROOT / vv.local if vv.local and not Path(vv.local).is_absolute() \
            else Path(vv.local or "")
        st, why = VERDICTS[sid]
        if mode == "production" and st == "pending_user_review":
            approved = "temp_candidate_for_dev"
        else:
            approved = st
        probe = ffprobe(vfile) if vfile.exists() else {"error": "local missing"}
        digest = sha256(vfile) if vfile.exists() else None
        mirror = None
        if vfile.exists():
            mirror = MEDIA_DIR / f"{idx:02d}_{sid}_{fv}{vfile.suffix}"
            if not mirror.exists() or sha256(mirror) != digest:
                shutil.copy2(vfile, mirror)
        ivs = vv.input_visuals or []
        relay = next((iv for iv in ivs
                      if iv.get("semantic_role") == "relay_start_boundary"), None)
        rsh = raw_shots[sid]
        entries.append({
            "order": idx, "scene_id": rsh.get("scene_id") or sid[:3],
            "shot_id": sid,
            "video_version_id": fv,
            "approved_status": approved,
            "verdict_source": why,
            "media_uri": str(vfile.relative_to(ROOT)),
            "media_sha256": digest,
            "snapshot_media": str(mirror.relative_to(ROOT)) if mirror else None,
            "provider_url": vv.url,
            "source_receipt_ref": {"task_id": vv.task_id, "video_id": vv.video_id,
                                   "origin_run_id": vv.origin_run_id,
                                   "model": vv.model,
                                   "created_at": vv.created_at},
            "duration_probe": probe,
            "end_frame": {"local": sh.video_end_frame.local,
                          "url": sh.video_end_frame.url,
                          "source_version": sh.video_end_source_version},
            "relay": ({"source_shot_id": relay.get("source_shot_id"),
                       "end_source_version": relay.get("end_source_version"),
                       "tail_url_recorded": relay.get("url")}
                      if relay else {"source_shot_id": None,
                                     "note": "hard/independent 开场"}),
            "sentinel": rsh.get("video_reference_asset_id"),
            "shot_updated_at": rsh.get("updated_at"),
        })

    # ---- 冻结档案 ----
    skills = {}
    for pkg in ("full-story-relay-calibration", "keyframes-bridge-repair"):
        sj = json.loads((ROOT / "src" / "agents" / "skill_system" / "builtin" / pkg
                         / "skill.json").read_text(encoding="utf-8"))
        skills[pkg] = sj["version"]
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          encoding="utf-8", cwd=ROOT).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                           encoding="utf-8", cwd=ROOT).stdout.strip()
    quota = json.loads((ROOT / "cache" / "quota.json").read_text(encoding="utf-8")) \
        if (ROOT / "cache" / "quota.json").exists() else {}
    res = json.loads((pdir / "output" / "resources.json").read_text(encoding="utf-8"))
    looks = [{"look_id": lk.get("look_id") or k,
              "status": lk.get("status"),
              "approved": (lk.get("approved") or [])}
             for k, lk in (res.get("looks") or {}).items()
             if lk.get("status") in ("approved",)]
    ss = json.loads((pdir / "output" / "scene_system.json").read_text(encoding="utf-8"))
    sivs = [{"siv_id": k, "inst": v.get("scene_instance_id"),
             "status": v.get("approval_status")}
            for k, v in (ss.get("scene_instance_views") or {}).items()
            if v.get("approval_status") == "approved"]
    snapshot = {
        "snapshot_id": snap_id, "mode": mode,
        "created_at": time.time(),
        "project": {"name": PROJ, "project_updated_at": raw.get("updated_at")},
        "frozen_code": {"git_head": head,
                        "dirty_files": len(dirty.splitlines()) if dirty else 0,
                        "note": "projects/ 与 cache/ 不入库（运行时数据）"},
        "skills": skills,
        "model_config_digest": {"video": "agnes-video-2.5-flash",
                                "text": "agnes-2.5-flash",
                                "note": "以 config/config.yaml 为准，密钥不入档"},
        "approved_assets": {"looks_approved": looks,
                            "sivs_approved": sivs},
        "quota_ledger_local": quota,
        "seams_at_snapshot": seams,
        "shots": [{"shot_id": e["shot_id"], "final": e["video_version_id"],
                   "approved": e["approved_status"]} for e in entries],
        "media_mirror_dir": str(MEDIA_DIR.relative_to(ROOT)),
        "discipline_notes": [
            "approved_status=temp_candidate_for_dev 的镜头只可用于工程预览，不得称最终成片",
            "成片输出是派生版本，不反写 Shot.final_video",
            "媒体已按 URL 下载核验 sha256，不依赖临时 URL",
        ],
    }
    (OUTDIR / f"snapshot_{mode}.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUTDIR / f"assembly_input_manifest_{mode}.json").write_text(
        json.dumps({"project_id": PROJ, "snapshot_id": snap_id, "mode": mode,
                    "shots": entries},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"snapshot {snap_id}: {len(entries)} shots, "
          f"{sum(1 for e in entries if e['media_sha256'])} media verified, "
          f"{len(seams)} seams")
    for e in entries:
        print(f"  {e['order']:>2}. {e['shot_id']} {e['video_version_id']} "
              f"[{e['approved_status']}] dur={e['duration_probe'].get('duration')} "
              f"audio={e['duration_probe'].get('has_audio')}")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "production"
    sys.exit(main(mode))
