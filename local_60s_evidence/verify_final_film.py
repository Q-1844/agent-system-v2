#!/usr/bin/env python3
"""C2 本地 60 秒验证 · 成片校验（只读 + 抽帧）。

  1. ffprobe：时长 / 编码 / 分辨率 / 帧率 / 帧数
  2. 按 composition_manifest 的分镜顺序，在每镜中点抽 1 帧，拼成联系表（contact sheet）
  3. 核对：成片分镜数、每镜时长、顺序是否与冻结签名一致、有无漏镜/重复镜
输出：local_60s_evidence/final_verification.json + contact_sheet.jpg
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "local_60s_evidence"
PROJECT = "c2_parallel_local60_20261027"
RUN_ID = "prun_f09e81f0d8"
RUNDIR = ROOT / "projects" / PROJECT / "output" / "production_runs" / RUN_ID


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout, r.stderr


def main() -> int:
    run_json = json.loads((RUNDIR / "run.json").read_text(encoding="utf-8"))
    final = run_json.get("final_output")
    if final and not Path(final).is_absolute():
        # run.json 里的 final_output 是相对代码根目录的路径
        final = str(ROOT / final)
    if not final or not Path(final).exists():
        print(f"FAIL: 成片不存在: {final}")
        return 2
    final_p = Path(final)
    report = {"final_output": str(final_p), "bytes": final_p.stat().st_size}

    # ---------- ffprobe ----------
    rc, so, se = run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                      "-of", "json", str(final_p)])
    if rc != 0:
        print("FAIL ffprobe:", se[:400])
        return 3
    probe = json.loads(so)
    report["format"] = {k: probe["format"].get(k) for k in
                        ("format_name", "duration", "size", "bit_rate", "nb_streams")}
    report["streams"] = []
    for st in probe.get("streams", []):
        report["streams"].append({
            "index": st.get("index"), "codec_type": st.get("codec_type"),
            "codec_name": st.get("codec_name"), "profile": st.get("profile"),
            "width": st.get("width"), "height": st.get("height"),
            "r_frame_rate": st.get("r_frame_rate"), "avg_frame_rate": st.get("avg_frame_rate"),
            "nb_frames": st.get("nb_frames"), "duration": st.get("duration"),
            "pix_fmt": st.get("pix_fmt"), "level": st.get("level"),
        })
    dur = float(probe["format"]["duration"])
    report["duration_s"] = round(dur, 3)

    # ---------- manifest 分镜顺序 ----------
    man_p = RUNDIR / "composition_manifest.json"
    manifest = json.loads(man_p.read_text(encoding="utf-8")) if man_p.exists() else {}
    clips = manifest.get("clips") or []
    report["manifest_clip_count"] = len(clips)
    report["manifest_clips"] = [{"scene_id": c.get("scene_id"), "shot_id": c.get("shot_id"),
                                 "duration_s": c.get("duration_s"),
                                 "video_version_id": c.get("video_version_id"),
                                 "local": c.get("local")} for c in clips]

    # ---------- 每镜抽帧 ----------
    frames_dir = OUT / "final_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    timeline = []
    t = 0.0
    for i, c in enumerate(clips):
        d = float(c.get("duration_s") or 0)
        mid = t + d / 2.0
        fp = frames_dir / f"{i:02d}_{c.get('shot_id')}.jpg"
        rc, so, se = run(["ffmpeg", "-y", "-ss", f"{mid:.3f}", "-i", str(final_p),
                          "-frames:v", "1", "-q:v", "3", str(fp)])
        timeline.append({"index": i, "shot_id": c.get("shot_id"), "scene_id": c.get("scene_id"),
                         "start_s": round(t, 3), "end_s": round(t + d, 3),
                         "duration_s": round(d, 3), "sample_at_s": round(mid, 3),
                         "frame": str(fp) if fp.exists() else None,
                         "extracted": fp.exists()})
        t += d
    report["shot_timeline"] = timeline
    report["sum_clip_duration_s"] = round(t, 3)
    report["duration_matches_clips"] = abs(dur - t) <= 1.5

    # 镜头顺序核对：与冻结 source_signature 的 scene/shot 顺序比对
    sig = run_json.get("source_signature") or {}
    expected = [sh.get("shot_id") for sc in sig.get("scenes", []) for sh in sc.get("shots", [])]
    actual = [c.get("shot_id") for c in clips]
    report["expected_order"] = expected
    report["actual_order"] = actual
    report["order_matches"] = expected == actual
    report["missing_shots"] = [s for s in expected if s not in actual]
    report["duplicated_shots"] = [s for s in set(actual) if actual.count(s) > 1]

    # ---------- 联系表 ----------
    sheet = OUT / "contact_sheet.jpg"
    if timeline:
        inputs = []
        for e in timeline:
            if e["frame"]:
                inputs += ["-i", e["frame"]]
        n = len([e for e in timeline if e["frame"]])
        if n:
            cols = 4
            tw, th = 480, 270
            rows = (n + cols - 1) // cols
            filt = "".join(f"[{i}:v]scale={tw}:{th}:force_original_aspect_ratio=disable,"
                           f"pad={tw}:{th}:0:0:black,setsar=1[t{i}];" for i in range(n))
            filt += "".join(f"[t{i}]" for i in range(n))
            layout = "|".join(f"{(i % cols) * tw}_{(i // cols) * th}" for i in range(n))
            fc = run(["ffmpeg", "-y", *inputs, "-filter_complex",
                      filt + f"xstack=inputs={n}:layout={layout}:fill=black",
                      "-frames:v", "1", str(sheet)])
            report["contact_sheet"] = str(sheet) if sheet.exists() else None
            report["contact_sheet_layout"] = {"cols": cols, "rows": rows, "tile": f"{tw}x{th}"}

    (OUT / "final_verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in
                      ("duration_s", "sum_clip_duration_s", "duration_matches_clips",
                       "manifest_clip_count", "order_matches", "missing_shots",
                       "duplicated_shots", "bytes")},
                     ensure_ascii=False, indent=2))
    print("streams:", json.dumps(report["streams"], ensure_ascii=False))
    print("contact_sheet:", report.get("contact_sheet"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
