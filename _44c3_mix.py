# -*- coding: utf-8 -*-
"""Phase 44C — C3 混音渲染与量化验证（驱动器=工具验证路径；Agent 会话另有验收）。

流程：
  1) 由 _44c/cue_table.json 构建 AudioPlan（music+narration×5 + ducking + loudness）
     → put_audio_plan（OCC）到 Agent 序列 seq_a3d5b19b（B 批 manifest_v2 时间线）
  2) render_submit（方案 A：base=既有 done 渲染 video copy + 音频混音）
  3) 分轨导出（narration / music stem，WAV）
  4) 量化验证：ebur128 综合响度/真峰值（final）、A/V 时长差、峰值 advisory
证据：_44c/mix_evidence.json
用法：python _44c3_mix.py [--steps plan,render,stems,measure,silent]
"""
import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
PROJECT = "雨灯归航"
PDIR = BASE / "projects" / PROJECT
SEQ_ID = "seq_a3d5b19b"   # B 批 Agent 装配序列（manifest_v2 目标时间线）

# 混音配比（C0 §1/§3；响度实测依据见报告）：
#   旁白源 -25.4 LUFS +2dB；音乐源 -19.2 LUFS -10dB（垫底 ≈ 旁白下 4 LU）；
#   环境声 base_gain -3dB；终混 loudnorm I=-16/TP=-1.5/LRA=11（桌面/流媒体口径）
# 44C+ 重调（用户实测反馈：v1 音乐不可闻、成片"缝合感"）：
#   BGM v2（程序化作曲 -13.6 LUFS）-13.5dB → 混入 ≈-27 LUFS（旁白 -23.4 下 ~4LU，
#   ducking 期间更深）；环境声 -6dB 让位；终混 loudnorm 不变。
NARRATION_GAIN_DB = 2.0
MUSIC_GAIN_DB = -13.5
BASE_GAIN_DB = -6.0
MUSIC_FADE_IN_MS = 2500
MUSIC_FADE_OUT_MS = 5000
MUSIC_PATH = "audio_imports/music/bgm_44c_v2.wav"


def sha256_file(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def ebur128_measure(path: str) -> dict:
    """ebur128（peak=true）：综合响度/真峰值。解析失败 → 诚实缺省。"""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, timeout=600)
    err = proc.stderr.decode("utf-8", errors="replace")
    out = {}
    lines = err.splitlines()
    for i, line in enumerate(lines):
        if "Integrated loudness:" in line:
            for j in (i + 1, i + 2, i + 3):
                if "I:" in lines[j]:
                    out["i_lufs"] = float(lines[j].split("I:")[1].split("LUFS")[0].strip())
                if "LRA:" in lines[j]:
                    out["lra_lu"] = float(lines[j].split("LRA:")[1].split("LU")[0].strip())
        if "True peak:" in line and "Peak:" in line:
            pass
    for line in lines:
        if line.strip().startswith("Peak:"):
            # 形如 "Peak:   -1.2 dBFS"
            try:
                out["peak_dbfs"] = float(line.split("dBFS")[0].split(":")[-1].strip())
            except (ValueError, IndexError):
                pass
        if line.strip().startswith("True peak:"):
            # 形如 "True peak:   Peak:   -1.2 dBFS:   -1.5 dBTP"
            try:
                out["true_peak_dbtp"] = float(line.split("dBTP")[0].split(":")[-1].strip())
            except (ValueError, IndexError):
                pass
    return out or {"error": "ebur128 解析失败"}


def av_duration_delta(path: str) -> dict:
    from src.sequence.media_ops import probe_video
    pr = probe_video(path)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-select_streams", "a:0",
         "-show_entries", "stream=duration", str(path)],
        capture_output=True, text=True).stdout
    a_dur = None
    try:
        st = json.loads(out)["streams"][0]
        a_dur = float(st["duration"]) * 1000 if st.get("duration") else None
    except Exception:
        pass
    return {"container_ms": pr["duration_ms"], "audio_ms": a_dur,
            "delta_ms": (a_dur - pr["duration_ms"]) if a_dur else None}


def bootstrap_queue():
    import deps
    import yaml
    import routers.sequences  # noqa: F401
    from src.core.rate_limiter import RateLimiter
    from src.core.quota import QuotaLedger
    cfg = yaml.safe_load((BASE / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
    RateLimiter.get((cfg.get("agnes") or {}).get("rpm"))
    QuotaLedger.get_default(BASE / "cache" / "quota.json", limits=cfg.get("quota"))
    if deps.project_manager is None:
        from src.pipeline.controller import PipelineController
        deps.pipeline_controller = PipelineController(str(BASE / "config" / "config.yaml"))
        deps.agnes_client = deps.pipeline_controller.agnes_client
        deps.project_manager = deps.pipeline_controller.project_manager
    from src.core.job_queue import JobQueue
    q = JobQueue(BASE / "_44c" / "jobs_44c.json", deps.JOB_HANDLERS)
    deps.job_queue = q
    return deps, q


async def wait_render(svc, sid, vid, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        row = svc.get_entry(sid).get_render(vid)
        if row and row.status in ("done", "failed", "raced_superseded"):
            return row
        await asyncio.sleep(2)
    raise TimeoutError(f"render timeout: {vid}")


def build_layers_payload(cue_table: dict) -> list:
    layers = [{
        "layer_id": "lay_music_bed",
        "role": "music",
        "source": {"kind": "imported_audio", "file": MUSIC_PATH},
        "start_ms": 0, "duration_ms": cue_table["film_duration_ms"],
        "source_in_ms": 0, "gain_db": MUSIC_GAIN_DB,
        "fade_in_ms": MUSIC_FADE_IN_MS, "fade_out_ms": MUSIC_FADE_OUT_MS,
    }]
    for r in cue_table["narration_cues"]:
        layers.append({
            "layer_id": f"lay_{r['cue_id']}",
            "role": "narration",
            "source": {"kind": "imported_audio", "file": r["file"]},
            "start_ms": r["start_ms"], "duration_ms": r["duration_ms"],
            "source_in_ms": 0, "gain_db": NARRATION_GAIN_DB,
            "fade_in_ms": 30, "fade_out_ms": 60,
        })
    return layers


def run_stems(svc, plan, outdir: Path) -> dict:
    """分轨导出（provenance=plan revision；文件名含 revision 可追溯）。"""
    from src.sequence.audio_basis import resolve_audio_source
    from src.sequence.audio_mix import build_audio_mix_plan, compile_stem_filter_graph
    from src.sequence.audio_mix import MixLayer
    mix_layers = [MixLayer(layer_id=l.layer_id, role=l.role,
                           source_file=str(resolve_audio_source(PDIR, l.layer_id, l.source).file),
                           source_in_ms=l.source_in_ms, duration_ms=l.duration_ms,
                           start_ms=l.start_ms, gain_db=l.gain_db,
                           fade_in_ms=l.fade_in_ms, fade_out_ms=l.fade_out_ms)
                  for l in plan.layers]
    outdir.mkdir(parents=True, exist_ok=True)
    result = {}
    for name, roles in (("narration", ["narration"]), ("music", ["music"])):
        plan_ = build_audio_mix_plan("", plan.revision, mix_layers)
        g = compile_stem_filter_graph(plan_, roles)
        out = outdir / f"stem_{name}_rev{plan.revision}.wav"
        cmd = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
               + g["input_args"] + ["-filter_complex", g["filter_complex"]]
               + g["out_args"] + [str(out)])
        subprocess.run(cmd, check=True, timeout=600)
        result[name] = {"file": str(out), "sha256": sha256_file(out),
                        "plan_revision": plan.revision}
    return result


async def main(steps: list) -> int:
    from src.sequence.service import SequenceService
    from src.sequence.audio_models import SequenceDuckingConfig, SequenceLoudnessTarget
    svc = SequenceService(PDIR, PROJECT)
    evfile = BASE / "_44c" / "mix_evidence.json"
    ev = json.loads(evfile.read_text(encoding="utf-8")) if evfile.exists() else {}
    cue_table = json.loads((BASE / "_44c" / "cue_table.json").read_text(encoding="utf-8"))
    entry = svc.get_entry(SEQ_ID)
    film_dur = entry.block.clips and None
    doc = json.loads((PDIR / "output" / "assembly" / "manifest_v2.json").read_text(encoding="utf-8"))
    cue_table["film_duration_ms"] = doc["total_duration_ms"]

    if "plan" in steps:
        layers = build_layers_payload(cue_table)
        plan = svc.put_audio_plan(
            SEQ_ID, layers,
            expected_revision=(svc.get_audio_plan(SEQ_ID).revision
                               if svc.get_audio_plan(SEQ_ID) else None),
            plan_config={"base_gain_db": BASE_GAIN_DB,
                         "ducking": SequenceDuckingConfig().to_dict(),
                         "loudness": SequenceLoudnessTarget(tp=-2.0).to_dict()})
        ev["audio_plan"] = {"revision": plan.revision, "layer_count": len(plan.layers),
                            "base_gain_db": plan.base_gain_db,
                            "ducking": plan.ducking.to_dict(),
                            "loudness": plan.loudness.to_dict()}
        print(f"[plan] revision={plan.revision} layers={len(plan.layers)}")

    if "render" in steps:
        deps, q = bootstrap_queue()
        await q.start()
        rv = svc.create_render(SEQ_ID)
        job = q.submit(PROJECT, "sequence_render", None, sequence_id=SEQ_ID,
                       render_version_id=rv.version_id)
        t0 = time.time()
        row = await wait_render(svc, SEQ_ID, rv.version_id)
        ev["mixed_render"] = {"render_version_id": rv.version_id, "job_id": job.job_id,
                              "status": row.status, "seconds": round(time.time() - t0, 1),
                              "planned_ms": row.planned_duration_ms,
                              "duration_ms": row.duration_ms,
                              "output": row.output_file,
                              "audio_peak_warning": row.audio_peak_warning,
                              "base_render_version_id": row.base_render_version_id,
                              "error": row.error}
        print(f"[render] {rv.version_id} → {row.status} in {ev['mixed_render']['seconds']}s")
        stop = getattr(q, "stop", None)
        if stop:
            await stop()

    if "stems" in steps:
        plan = svc.get_audio_plan(SEQ_ID)
        stems = run_stems(svc, plan, PDIR / "output" / "assembly" / "stems")
        ev["stems"] = stems
        print(f"[stems] {json.dumps({k: v['file'] for k, v in stems.items()}, ensure_ascii=False)}")

    if "measure" in steps:
        m = ev.get("mixed_render") or {}
        out = m.get("output")
        if not out or not Path(out).exists():
            print("measure：无 done 输出可测")
            return 1
        ev["measurements"] = {"final": ebur128_measure(out),
                              "av_duration": av_duration_delta(out),
                              "target_loudness": ev["audio_plan"]["loudness"],
                              "note": "目标平台=桌面/在线流媒体（I=-16 LUFS 口径存 plan，"
                                      "非全平台通用声明）；自动测量不替代人耳验收"}
        print(f"[measure] {json.dumps(ev['measurements']['final'], ensure_ascii=False)}")
        print(f"[measure] AV delta: {ev['measurements']['av_duration']}")

    evfile.parent.mkdir(parents=True, exist_ok=True)
    evfile.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default="plan,render,stems,measure")
    a = ap.parse_args()
    sys.exit(asyncio.run(main([s.strip() for s in a.steps.split(",")])))
