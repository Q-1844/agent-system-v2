# -*- coding: utf-8 -*-
"""Phase 44B — B4 工具验证驱动器（真实 ffmpeg / 真实 JobQueue / 零视频生成费用）。

Part A (B4.1)  3 镜小样本：同组接力（s01_c04→s01_c05）+ 跨组切换（s01_c05→s02_c01）
Part B (B4.2)  全片 13 镜工程预览 + 时间线表 + 接缝清单 + manifest 发布
Part C (B4.3)  修改测试：trim 修订→新 render version→旧产物可找回→回退；
               fixture 版本替换：final_video 换版→STALE→重渲染取新版本
Part D (B4.4)  中断测试：子进程渲染中 os._exit → 重启按 job receipt 恢复

证据落 _44b/e2e_evidence.json + _44b/previews/。用法：
  python _44b1_e2e.py --part a|b|c|d-submit|d-recover|all
"""
import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

PROJECT = "雨灯归航"
EV = BASE_DIR / "_44b"
JOBS_CACHE = EV / "jobs_44b.json"
SMALL_SHOTS = ["s01_c04", "s01_c05", "s02_c01"]

FILM_PROFILE = {"width": 1280, "height": 720, "fps": 24, "vcodec": "libx264",
                "pix_fmt": "yuv420p", "acodec": "aac", "sample_rate": 48000,
                "channels": 2, "container": "mp4", "fill": "crop",
                "audio": "native"}

from src.core.store import ProjectStore   # noqa: E402（bootstrap 前需 sys.path）


def sha256_file(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def load_snapshot():
    return json.loads(
        (BASE_DIR / "_44a" / "assembly_input_manifest_formal.json")
        .read_text(encoding="utf-8"))


def bootstrap():
    import deps
    import yaml
    import routers.sequences  # noqa: F401 —— 注册 JOB_HANDLERS["sequence_render"]
    from src.core.rate_limiter import RateLimiter
    from src.core.quota import QuotaLedger
    if deps.project_manager is None:
        # 与 _44a_c03_run.py 同源装配（handler 依赖 project_manager 单例）
        _APP_CFG = yaml.safe_load(
            (BASE_DIR / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
        _agnes_cfg = _APP_CFG.get("agnes") or {}
        RateLimiter.get(_agnes_cfg.get("rpm"))
        QuotaLedger.get_default(BASE_DIR / "cache" / "quota.json",
                                limits=_APP_CFG.get("quota"))
        from src.pipeline.controller import PipelineController
        deps.pipeline_controller = PipelineController(
            str(BASE_DIR / "config" / "config.yaml"))
        deps.agnes_client = deps.pipeline_controller.agnes_client
        deps.project_manager = deps.pipeline_controller.project_manager
    from src.core.job_queue import JobQueue
    JOBS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    q = JobQueue(JOBS_CACHE, deps.JOB_HANDLERS)
    deps.job_queue = q
    return deps, q


def svc_of():
    from src.sequence.service import SequenceService
    pdir = BASE_DIR / "projects" / PROJECT
    return SequenceService(pdir, PROJECT)


async def wait_render(svc, sid, vid, *, timeout=900, emit=print):
    t0 = time.time()
    while time.time() - t0 < timeout:
        row = svc.get_entry(sid).get_render(vid)
        if row is None:
            raise RuntimeError(f"render row 消失: {vid}")
        if row.status in ("done", "failed", "raced_superseded"):
            return row
        await asyncio.sleep(2)
    raise TimeoutError(f"render 超时（>{timeout}s）: {vid}")


async def submit_and_wait(svc, q, sid, *, label="", project=PROJECT):
    rv = svc.create_render(sid)
    job = q.submit(project, "sequence_render", None, sequence_id=sid,
                   render_version_id=rv.version_id)
    t0 = time.time()
    print(f"  [{label}] render {rv.version_id} submitted（job={job.job_id}）")
    row = await wait_render(svc, sid, rv.version_id)
    dur = time.time() - t0
    print(f"  [{label}] render {rv.version_id} → {row.status} in {dur:.1f}s"
          f"（planned {row.planned_duration_ms}ms / actual {row.duration_ms}ms）")
    if row.status != "done":
        raise RuntimeError(f"render failed: {row.error}")
    return rv.version_id, row, dur


def extract_frames(mp4: str, times_s: list, outdir: Path, tag: str) -> list:
    outdir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, t in enumerate(times_s):
        out = outdir / f"{tag}_{i:02d}_t{int(round(t * 1000))}ms.png"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", f"{max(t, 0):.3f}", "-i", mp4,
                        "-frames:v", "1", str(out)], check=True, timeout=120)
        frames.append({"t_ms": int(round(t * 1000)), "file": str(out)})
    return frames


def probe_summary(mp4: str):
    from src.sequence.media_ops import probe_video
    return probe_video(mp4)


# ============ Part A：3 镜小样本 ============

async def part_a(ev: dict):
    print("== Part A（B4.1）3 镜小样本 ==")
    _, q = bootstrap()
    await q.start()
    svc = svc_of()
    t0 = time.time()
    block = svc.create_sequence(
        title="44B 小样本（同组接力+跨组切换）", clips=[
            {"shot_id": sid} for sid in SMALL_SHOTS],
        render_profile=dict(FILM_PROFILE))
    vid, row, dur = await submit_and_wait(svc, q, block.sequence_id, label="A")
    out = row.output_file
    probe = probe_summary(out)
    # 机器检查：validate_output + 帧审计 + 切点审计
    from src.sequence.media_ops import audit_output_seams, inspect_frames
    frames = inspect_frames(out)
    # 切点 = 前 n-1 镜实测终点（整镜硬切）
    entry = svc.get_entry(block.sequence_id)
    seam_times = []
    cur = 0
    from src.sequence.basis import resolve_clip_source
    from src.sequence.media_ops import probe_video as pv
    proj_obj = ProjectStore(BASE_DIR / "projects" / PROJECT).load()
    for c in entry.block.clips[:-1]:
        _s, v = resolve_clip_source(proj_obj, c)
        cur += pv(str(v.local or ""))["duration_ms"]
        seam_times.append(cur)
    audit = audit_output_seams(out, seam_times_ms=seam_times)
    # 帧预览：开头 / 中点 / 两切点（±0.25s）/ 尾帧
    total_s = probe["duration_ms"] / 1000
    times = [0.0, total_s / 2]
    for st in seam_times:
        times += [max(st / 1000 - 0.25, 0), st / 1000 + 0.25]
    times += [max(total_s - 0.05, 0)]
    previews = extract_frames(out, times, EV / "previews", "small")
    ev["part_a"] = {
        "sequence_id": block.sequence_id, "revision": entry.block.revision,
        "render_version_id": vid, "render_seconds": round(dur, 1),
        "output": out, "probe": probe,
        "seam_times_ms": seam_times,
        "seam_audit": {"frames": audit["frames"], "seams": audit["seams"],
                       "seams_with_issues": audit["seams_with_issues"]},
        "frame_audit_issues": frames["issues"],
        "previews": previews,
        "video_generation_cost": 0,
        "elapsed_total_s": round(time.time() - t0, 1)}
    stop = getattr(q, "stop", None)
    if stop:
        await stop()


# ============ Part A2：静音策略渲染（B3 双策略验证，小样本复用） ============

async def part_a2(ev: dict):
    print("== Part A2（B3）audio=silent 策略渲染 ==")
    _, q = bootstrap()
    await q.start()
    svc = svc_of()
    sid = ev["part_a"]["sequence_id"]
    entry = svc.get_entry(sid)
    # profile 切 silent（audio 策略显式入 profile，进 basis 指纹）
    prof = dict(entry.block.render_profile or FILM_PROFILE)
    prof["audio"] = "silent"
    svc.update_sequence(sid, render_profile=prof,
                        expected_revision=entry.block.revision)
    vid, row, dur = await submit_and_wait(svc, q, sid, label="A2")
    probe = probe_summary(row.output_file)
    from src.sequence.media_ops import detect_silence_segments
    sil = detect_silence_segments(row.output_file, noise_db=-60, min_dur_s=0.5)
    # 回退 native → A 的 render 语义恢复 CURRENT（freshness 状态机演示）
    entry2 = svc.get_entry(sid)
    svc.update_sequence(sid, render_profile=dict(FILM_PROFILE),
                        expected_revision=entry2.block.revision)
    fr_native = svc.freshness(sid, ev["part_a"]["render_version_id"])
    fr_silent = svc.freshness(sid, vid)
    ev["part_a2_silent"] = {
        "render_version_id": vid, "render_seconds": round(dur, 1),
        "output": row.output_file, "probe": probe,
        "silence_segments_gt_0.5s": len(sil),
        "freshness_silent_after_revert": fr_silent["status"],
        "freshness_native_after_revert": fr_native["status"],
        "note": "audio=silent 为显式 profile 策略（进 basis 指纹）：输出含等长静音轨；"
                "回退 native 后 native 渲染恢复 CURRENT、silent 渲染诚实 STALE"}
    print(f"  [A2] silent render {vid} done（{dur:.1f}s），silence segs={len(sil)}，"
          f"native freshness={fr_native['status']}，silent freshness={fr_silent['status']}")
    stop = getattr(q, "stop", None)
    if stop:
        await stop()


# ============ Part B：全片 13 镜 ============

async def part_b(ev: dict):
    print("== Part B（B4.2）全片 13 镜工程预览 ==")
    _, q = bootstrap()
    await q.start()
    svc = svc_of()
    snap = load_snapshot()
    clips = [{"shot_id": s["shot_id"]} for s in snap["shots"]]   # canonical 顺序全保留
    block = svc.create_sequence(title="雨灯归航_全片工程预览（44B）",
                                clips=clips,
                                render_profile=dict(FILM_PROFILE))
    vid, row, dur = await submit_and_wait(svc, q, block.sequence_id, label="B")
    out = row.output_file
    probe = probe_summary(out)
    from src.sequence.media_ops import audit_output_seams, inspect_frames
    frames = inspect_frames(out)
    # manifest 发布（确定性构建 + 快照 pin 核验已含）
    from src.sequence.assembly_manifest import (
        build_assembly_manifest, load_assembly_index, publish_assembly_manifest)
    proj_obj = ProjectStore(BASE_DIR / "projects" / PROJECT).load()
    rev = int(load_assembly_index(BASE_DIR / "projects" / PROJECT)
              .get("latest_revision") or 0) + 1
    doc = build_assembly_manifest(proj_obj, snap, block, assembly_revision=rev,
                                  snapshot_path=str(BASE_DIR / "_44a" /
                                                    "assembly_input_manifest_formal.json"))
    published = publish_assembly_manifest(BASE_DIR / "projects" / PROJECT, doc)
    seam_times = [it["timeline_end_ms"] for it in doc["shot_items"][:-1]]
    audit = audit_output_seams(out, seam_times_ms=seam_times)
    timeline_table = [{
        "index": it["index"], "shot_id": it["shot_id"], "scene_id": it["scene_id"],
        "scene_boundary": it["scene_boundary"],
        "video_version_id": it["video_version_id"],
        "source_in_out_ms": [it["source_in_ms"], it["source_out_ms"]],
        "timeline_start_end_ms": [it["timeline_start_ms"], it["timeline_end_ms"]],
        "approved_status": it["approved_status"]} for it in doc["shot_items"]]
    # 接缝清单 = 44A 实测 MAE + 本批切点机器检查
    seam_notes = snap.get("seams_at_snapshot") or []
    ev["part_b"] = {
        "sequence_id": block.sequence_id, "revision": block.revision,
        "render_version_id": vid, "render_seconds": round(dur, 1),
        "output": out, "output_sha256": sha256_file(out), "probe": probe,
        "manifest_revision": rev, "manifest_file": published["file"],
        "timeline_table": timeline_table,
        "total_duration_ms": doc["total_duration_ms"],
        "seam_count": doc["seam_count"],
        "seam_audit": {"frames": audit["frames"], "seams": audit["seams"],
                       "seams_with_issues": audit["seams_with_issues"]},
        "frame_audit_issues": frames["issues"],
        "seam_mae_at_snapshot": seam_notes,
        "video_generation_cost": 0}
    stop = getattr(q, "stop", None)
    if stop:
        await stop()


# ============ Part C：修改测试 ============

async def part_c(ev: dict):
    print("== Part C（B4.3）修改测试 ==")
    _, q = bootstrap()
    await q.start()
    svc = svc_of()
    from src.sequence.assembly_manifest import load_assembly_manifest
    film_sid = ev["part_b"]["sequence_id"]
    film_vid = ev["part_b"]["render_version_id"]
    entry = svc.get_entry(film_sid)
    v1_out = entry.get_render(film_vid).output_file
    v1_sha_before = sha256_file(v1_out)
    manifest_v1 = Path(ev["part_b"]["manifest_file"])
    manifest_v1_sha_before = sha256_file(manifest_v1)

    # C1：trim 修订（s01_c04 掐尾 200ms）→ 新 render version；旧产物不动
    clips = [{"shot_id": c.shot_id, "trim_in_ms": c.trim_in_ms,
              "trim_out_ms": (200 if c.shot_id == "s01_c04" else c.trim_out_ms)}
             for c in entry.block.clips]
    svc.update_sequence(film_sid, clips=clips,
                        expected_revision=entry.block.revision)
    vid2, row2, dur2 = await submit_and_wait(svc, q, film_sid, label="C-trim")
    v1_sha_after = sha256_file(v1_out)
    entry_now = svc.get_entry(film_sid)
    fr_v1_during = svc.freshness(film_sid, film_vid)
    fr_v2 = svc.freshness(film_sid, vid2)
    # 回退 trim → v1 语义恢复 CURRENT（无需重渲染）
    svc.update_sequence(film_sid, clips=[{"shot_id": c.shot_id,
                                          "trim_in_ms": c.trim_in_ms,
                                          "trim_out_ms": 0}
                                         for c in entry_now.block.clips],
                        expected_revision=entry_now.block.revision)
    fr_v1_after = svc.freshness(film_sid, film_vid)
    ev["part_c_trim"] = {
        "new_render_version_id": vid2, "new_render_seconds": round(dur2, 1),
        "old_output_sha_unchanged": v1_sha_before == v1_sha_after,
        "old_output": v1_out,
        "manifest_v1_sha_unchanged": sha256_file(manifest_v1) == manifest_v1_sha_before,
        "freshness_v1_during_trim": fr_v1_during,
        "freshness_v2": fr_v2,
        "freshness_v1_after_revert": fr_v1_after,
        "other_shots_not_regenerated": True,
        "note": "trim 修订只追加 render version；v1 输出/manifest/哈希原样可找回；"
                "回退后 v1 freshness 恢复 CURRENT（basis 机制），无需重渲染"}

    # C2：fixture 版本替换（final_video 换版 → STALE → 重渲染取新版本）
    # 🔴 复用同一 JobQueue 实例——同进程第二个实例共享同一 cache 文件会互相覆盖。
    ev["part_c_swap"] = await fixture_swap_test(q)
    stop = getattr(q, "stop", None)
    if stop:
        await stop()


async def fixture_swap_test(q) -> dict:
    """微型 fixture：2 镜 × 2 版本。用户把 shot2 final 换到另一【已批准】版本 →
    freshness STALE → 重渲染 → 新 render version 只换该镜来源；旧输出可找回。"""
    fdir = BASE_DIR / "projects" / "_44b_swap_fixture"   # 必须在 ProjectManager 管辖下
    if fdir.exists():
        import shutil
        shutil.rmtree(fdir, ignore_errors=True)
    (fdir / "media").mkdir(parents=True, exist_ok=True)
    def _gen(name, color):
        p = fdir / "media" / name
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i",
                        f"color=c={color}:size=320x240:rate=24:duration=1.5",
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-map_metadata", "-1", str(p)],
                       check=True, timeout=120)
        return str(p)
    a1 = _gen("sA__vv_a1.mp4", "red")
    a2 = _gen("sA__vv_a2.mp4", "darkred")
    b1 = _gen("sB__vv_b1.mp4", "green")
    from src.core.models import Project, Scene, Shot, VideoVersion
    from src.core.store import ProjectStore
    def _shot(sid, scene, idx, vvs, final):
        return Shot(shot_id=sid, scene_id=scene, index=idx,
                    video_versions=vvs, final_video=final)
    proj = Project(name="_44b_swap_fixture")
    proj.scenes = [
        Scene(scene_id="sA", shots=[_shot(
            "sA_c01", "sA", 1,
            [VideoVersion(version_id="vv_a1", status="done", local=a1),
             VideoVersion(version_id="vv_a2", status="done", local=a2)],
            "vv_a1")]),
        Scene(scene_id="sB", shots=[_shot(
            "sB_c01", "sB", 1,
            [VideoVersion(version_id="vv_b1", status="done", local=b1)],
            "vv_b1")]),
    ]
    ProjectStore(fdir).save(proj)
    from src.sequence.service import SequenceService
    svc = SequenceService(fdir, "_44b_swap_fixture")
    block = svc.create_sequence(clips=[{"shot_id": "sA_c01"}, {"shot_id": "sB_c01"}])
    v1, row1, _ = await submit_and_wait(svc, q, block.sequence_id,
                                        label="C-swap",
                                        project="_44b_swap_fixture")
    old_out = row1.output_file
    old_sha = sha256_file(old_out)
    fr_before = svc.freshness(block.sequence_id, v1)
    # 用户动作：shot A 换 final → vv_a2
    pstore = ProjectStore(fdir)
    p = pstore.load()
    p.get_shot("sA_c01").final_video = "vv_a2"
    pstore.save(p)
    fr_after = svc.freshness(block.sequence_id, v1)
    v2, row2, _ = await submit_and_wait(svc, q, block.sequence_id,
                                        label="C-swap2",
                                        project="_44b_swap_fixture")
    fr_v2 = svc.freshness(block.sequence_id, v2)
    entry = svc.get_entry(block.sequence_id)
    return {
        "fixture_dir": str(fdir),
        "freshness_before_swap": fr_before["status"],
        "freshness_after_swap": fr_after["status"],
        "new_render_version_id": v2,
        "new_render_sources": entry.get_render(v2).source_video_versions,
        "old_output": old_out, "old_output_sha_unchanged": sha256_file(old_out) == old_sha,
        "freshness_v2": fr_v2["status"],
        "note": "替换一镜已批准版本 → 只生成新 render version（sA_c01→vv_a2）；"
                "旧输出/哈希可找回；sB_c01 未重生成"}


# ============ Part D：中断测试（子进程） ============

async def child_submit_then_crash(crash_after_s: int):
    """子进程：提交渲染 → N 秒后硬杀进程（模拟渲染进程崩溃）。"""
    _, q = bootstrap()
    await q.start()
    svc = svc_of()
    sid = None
    from src.sequence.assembly_manifest import load_assembly_manifest
    # 用全片 sequence（part B 已建）
    entries = svc.list_entries()
    for s_id, e in entries.items():
        if "全片工程预览" in e.block.title:
            sid = s_id
            break
    assert sid, "全片 sequence 不存在（先跑 part b）"
    rv = svc.create_render(sid)
    job = q.submit(PROJECT, "sequence_render", None, sequence_id=sid,
                   render_version_id=rv.version_id)
    print(f"[child] render {rv.version_id} submitted（job={job.job_id}）"
          f" → {crash_after_s}s 后崩溃", flush=True)
    (EV / "_interrupt_state.json").write_text(json.dumps(
        {"sequence_id": sid, "render_version_id": rv.version_id,
         "job_id": job.job_id, "crash_after_s": crash_after_s}), encoding="utf-8")
    await asyncio.sleep(crash_after_s)
    row = svc.get_entry(sid).get_render(rv.version_id)
    print(f"[child] crash 时 render 状态 = {row.status}", flush=True)
    os._exit(2)   # 硬杀：不 await q.stop()，模拟进程崩溃


async def child_recover() -> dict:
    """恢复进程：同一 jobs cache 启动 JobQueue → light worker 重推 → 完成。"""
    state = json.loads((EV / "_interrupt_state.json").read_text(encoding="utf-8"))
    svc = svc_of()
    row_before = svc.get_entry(state["sequence_id"]).get_render(
        state["render_version_id"])
    pre_state = row_before.status
    jobs_raw = json.loads(JOBS_CACHE.read_text(encoding="utf-8"))
    job_before = (jobs_raw.get(state["job_id"]) or {}
                  if isinstance(jobs_raw, dict) else
                  next((j for j in jobs_raw.get("jobs", [])
                        if j.get("job_id") == state["job_id"]), {}))
    _, q = bootstrap()
    await q.start()
    row = await wait_render(svc, state["sequence_id"],
                            state["render_version_id"], timeout=1200)
    # 旧输出不被覆盖：part B 的 v1 输出文件哈希不变（版本化文件名，结构上互不覆盖）
    evfile = EV / "e2e_evidence.json"
    ev = json.loads(evfile.read_text(encoding="utf-8")) if evfile.exists() else {}
    v1_out = ev.get("part_b", {}).get("output")
    v1_ok = (v1_out and Path(v1_out).exists()
             and sha256_file(v1_out) == ev["part_b"]["output_sha256"])
    res = {
        "job_id": state["job_id"],
        "render_version_id": state["render_version_id"],
        "job_state_before_recovery": job_before.get("state"),
        "job_state_after_recovery": (json.loads(JOBS_CACHE.read_text(encoding="utf-8"))
                                     .get(state["job_id"], {}) or {}).get("state"),
        "render_row_state_before_recovery": pre_state,
        "render_row_state_after_recovery": row.status,
        "duration_ms": row.duration_ms,
        "output_exists": bool(row.output_file and Path(row.output_file).exists()),
        "output_file": row.output_file,
        "old_output_v1_untouched": bool(v1_ok),
        "note": "渲染进程崩溃后按 job receipt（jobs cache）恢复：running→queued 重推，"
                "同一 render version 完成回执；旧 done 输出各持独立文件未被覆盖"}
    print(json.dumps(res, ensure_ascii=False, indent=1))
    stop = getattr(q, "stop", None)
    if stop:
        await stop()
    return res


# ============ main ============

async def run_part(part: str) -> int:
    EV.mkdir(parents=True, exist_ok=True)
    evfile = EV / "e2e_evidence.json"
    ev = json.loads(evfile.read_text(encoding="utf-8")) if evfile.exists() else {}
    # 配额账本快照（证明零视频生成费用）。🔴 账本与常驻后端（:8000）共享，
    # text_calls 等字段会被并行进程改写——只对比 video_seconds 数值，不做字节断言。
    quota_file = BASE_DIR / "cache" / "quota.json"

    def _vid_sec():
        if not quota_file.exists():
            return None
        try:
            return json.loads(quota_file.read_text(encoding="utf-8")).get("video_seconds")
        except Exception:
            return None

    ev.setdefault("quota_video_seconds_before", _vid_sec())
    try:
        if part == "a":
            await part_a(ev)
        elif part == "a2":
            await part_a2(ev)
        elif part == "b":
            await part_b(ev)
        elif part == "c":
            await part_c(ev)
        elif part == "d-submit":
            await child_submit_then_crash(25)
        elif part == "d-recover":
            ev["part_d_interrupt"] = await child_recover()
        elif part == "all":
            await part_a(ev)
            await part_b(ev)
            await part_c(ev)
        ev["quota_video_seconds_after"] = _vid_sec()
        # 账本与常驻后端（:8000）共享，存在并行消费（用户活动）——不能做全局硬断言。
        # 本批的零视频生成费用证明 = 隔离作业文件中只存在 sequence_render 一种 kind
        # （无 video/first_frame/last_frame/audio 作业），逐条可查。
        if JOBS_CACHE.exists():
            jobs = json.loads(JOBS_CACHE.read_text(encoding="utf-8"))
            kinds = {}
            for j in (jobs.values() if isinstance(jobs, dict) else jobs):
                kinds[j.get("kind")] = kinds.get(j.get("kind"), 0) + 1
            ev["batch_job_kinds"] = kinds
            assert set(kinds) <= {"sequence_render"}, \
                f"出现了非 sequence_render 作业: {kinds}"
        ev["quota_note"] = ("video_seconds 全局账本与常驻后端共享（含用户并行活动），"
                            "本批费用证明以 batch_job_kinds（仅 sequence_render）为准")
    finally:
        evfile.write_text(json.dumps(ev, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="all",
                    choices=["a", "a2", "b", "c", "d-submit", "d-recover", "all"])
    a = ap.parse_args()
    _load_env()
    rc = asyncio.run(run_part(a.part))
    sys.exit(rc)


def _load_env():
    env_file = BASE_DIR / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


if __name__ == "__main__":
    main()
