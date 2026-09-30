# src/sequence/media_ops.py
# Sequence S1 — 媒体操作（24.md §18）：职责保持窄——
#
#   probe_video        ffprobe 真实容器探测（§14：所有 source duration/spec 从文件读取，
#                      禁止按 Shot.target_duration_s / provider request duration /
#                      数据库历史值猜）
#   render_clip        单 clip 一遍完成 trim + 归一化（§12 trim 语义 + §16 profile；
#                      AV 一起 trim，§15 原生音频是 source 的组成部分，只 trim/concat
#                      AV together，绝不提取/替换/重生成声音）
#   concat_clips       hard cut 拼接（§11 concat demuxer stream copy——片段已同构）
#   validate_output    输出机器校验（§30：流存在 / duration≈计划 / AV 不漂移；
#                      黑帧/静音诊断 advisory）
#
# 🔴 不放 music generation / audio design / AI reasoning / Shot canonical logic（§18）。
# ffmpeg/ffprobe 从 PATH 取（与 src/core/store.py / src/audio/media_ops.py 同口径）。
# 🔴 ffprobe JSON 解码必须显式 utf-8+replace（中文路径 GBK 陷阱，audio/media_ops 实测先例）。
# 🔴 归一化配方复用 VideoComposer._make_manifest_segment 已验证参数
#    （scale/pad/setsar/fps + libx264 yuv420p + aac 48000 stereo；无音轨补等长 silence）。
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import SEQUENCE_RENDER_PROFILE, SequenceError


def _run(cmd: List[str], *, timeout: int) -> str:
    """subprocess 包装：失败 → SequenceError（带 stderr 尾部诊断，不伪造结果）。"""
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise SequenceError("media_op_timeout",
                            f"媒体操作超时（>{timeout}s）: {Path(cmd[-1]).name if cmd else ''}") from e
    except OSError as e:
        raise SequenceError("media_op_failed", f"ffmpeg/ffprobe 启动失败: {e}") from e
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace")
        raise SequenceError("media_op_failed", f"{cmd[0]} 失败: {err.strip()[-400:]}")
    return proc.stdout.decode("utf-8", errors="replace")


def _run_diag(cmd: List[str], *, timeout: int) -> str:
    """诊断滤镜专用（blackdetect/silencedetect 把报告写到 stderr）。
    🔴 44B 修复：旧实现复用 _run（返回 stdout）→ advisory 恒为空列表
    （S1/S2「0 黑帧/0 静音」结论属于空断言）。stdout/stderr 均非空才有效。"""
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise SequenceError("media_op_timeout",
                            f"媒体操作超时（>{timeout}s）: {Path(cmd[-1]).name if cmd else ''}") from e
    except OSError as e:
        raise SequenceError("media_op_failed", f"ffmpeg/ffprobe 启动失败: {e}") from e
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace")
        raise SequenceError("media_op_failed", f"{cmd[0]} 失败: {err.strip()[-400:]}")
    return proc.stderr.decode("utf-8", errors="replace")


# ============ probe（24.md §14）============

def probe_video(path: str) -> Dict[str, Any]:
    """ffprobe 真实媒体事实。文件不存在 / 解析失败 → fail closed。
    🔴 S2 起：纯音频文件（无视频流）合法——返回 has_video=False（S1 视频源与
    S2 混音输出对视频流的要求由各自消费点强制：prepare/validate_output）。"""
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        raise SequenceError("source_unreadable", f"媒体文件不存在或为空: {path}")
    out = _run(["ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(p)], timeout=30)
    try:
        info = json.loads(out or "{}")
    except Exception as e:
        raise SequenceError("probe_failed", f"ffprobe 输出解析失败: {e}") from e
    streams = info.get("streams") or []
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    astream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    def _int(v: Any) -> Optional[int]:
        return int(v) if str(v or "").isdigit() else None

    fmt = info.get("format") or {}
    duration_src = fmt.get("duration") or (vstream or {}).get("duration") \
        or (astream or {}).get("duration")
    try:
        duration_ms = round(float(duration_src) * 1000)
    except (TypeError, ValueError):
        raise SequenceError("probe_failed", f"无法读取 duration: {path}")
    # r_frame_rate 形如 "24/1"
    fps = None
    rate = str((vstream or {}).get("r_frame_rate") or "")
    if "/" in rate:
        num, _, den = rate.partition("/")
        try:
            fps = round(float(num) / float(den), 3)
        except (TypeError, ValueError, ZeroDivisionError):
            fps = None
    return {
        "duration_ms": duration_ms,
        "has_video": vstream is not None,
        "width": _int((vstream or {}).get("width")),
        "height": _int((vstream or {}).get("height")),
        "fps": fps,
        "vcodec": vstream.get("codec_name") if vstream else None,
        "pix_fmt": vstream.get("pix_fmt") if vstream else None,
        "has_audio": astream is not None,
        "acodec": astream.get("codec_name") if astream else None,
        "sample_rate": _int(astream.get("sample_rate")) if astream else None,
        "channels": _int(astream.get("channels")) if astream else None,
    }


# ============ trim + normalize（24.md §12/§15/§16）============

def _scale_pad_fps(profile: Dict[str, Any]) -> str:
    """归一化滤镜链。默认 fill=pad：与 VideoComposer._make_manifest_segment 同口径
    （scale force_original_aspect_ratio=decrease + 黑边 pad，画面比例严格保持、零像素损失）。
    Phase 44B 增 fill=crop（显式入 profile 并进 basis 指纹）：scale increase + 居中裁切，
    满幅无黑边、画面比例同样严格保持——混合代际素材（如 11 镜 720 + 2 镜 704）时，
    pad 会在少数镜上下加可见黑边，crop 以极小边缘裁切换满幅一致；两者取舍由片方
    在 profile 显式声明（报告说明），绝不静默混用。"""
    w, h, fps = profile["width"], profile["height"], profile["fps"]
    fill = profile.get("fill", "pad")
    if fill == "crop":
        return (f"scale={w}:{h}:force_original_aspect_ratio=increase:force_divisible_by=2,"
                f"crop={w}:{h},setsar=1,fps={fps}")
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}")


def render_clip(src: str, dst: str, *, trim_in_ms: int, trim_out_ms: int,
                source_duration_ms: int,
                has_audio: bool,
                profile: Optional[Dict[str, Any]] = None) -> None:
    """单 clip：AV together 一遍完成 trim + 归一化（重编码，输出 concat 兼容）。
    trim 语义（§12）：in=掐头 / out=去尾；-ss 输入侧快 seek（重编码下帧精确），
    -t effective 限输出时长；音视频同参数裁切，AV sync 由同窗保证（§15 不碰声音内容）。
    无音轨源 → anullsrc 补等长 silence（§16 同构是 concat 前提；compose 先例）。"""
    profile = profile or SEQUENCE_RENDER_PROFILE
    src_p, dst_p = Path(src), Path(dst)
    if not src_p.exists():
        raise SequenceError("source_file_missing", f"source mp4 不存在: {src}")
    effective_ms = source_duration_ms - trim_in_ms - trim_out_ms
    if effective_ms <= 0:
        raise SequenceError("trim_invalid",
                            f"effective_ms={effective_ms}（trim 非法，本应在 Phase 1 拦截）")
    dst_p.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{trim_in_ms / 1000:.3f}", "-i", str(src_p),
           "-t", f"{effective_ms / 1000:.3f}"]
    if has_audio:
        cmd += ["-vf", _scale_pad_fps(profile),
                "-c:v", profile["vcodec"], "-pix_fmt", profile["pix_fmt"],
                "-c:a", profile["acodec"], "-ar", str(profile["sample_rate"]),
                "-ac", str(profile["channels"]),
                "-map_metadata", "-1", str(dst_p)]
    else:
        # 无音轨：补等长 silence（-shortest 以限定的输出时长为准）
        cmd += ["-f", "lavfi", "-i",
                f"anullsrc=r={profile['sample_rate']}:cl="
                + ("stereo" if profile["channels"] == 2 else "mono"),
                "-vf", _scale_pad_fps(profile),
                "-map", "0:v", "-map", "1:a",
                "-c:v", profile["vcodec"], "-pix_fmt", profile["pix_fmt"],
                "-c:a", profile["acodec"], "-ar", str(profile["sample_rate"]),
                "-ac", str(profile["channels"]),
                "-shortest", "-map_metadata", "-1", str(dst_p)]
    _run(cmd, timeout=600)
    if not dst_p.exists() or dst_p.stat().st_size == 0:
        raise SequenceError("render_failed", f"clip 输出为空: {dst_p.name}")


# ============ concat（24.md §11 hard cut）============

def concat_clips(segment_paths: List[str], dst: str) -> None:
    """hard cut 拼接：concat demuxer + stream copy（片段已同构 profile）。
    S1 无 transition overlap（§11）——顺序即 timeline 顺序。"""
    if not segment_paths:
        raise SequenceError("render_failed", "没有可拼接的片段")
    dst_p = Path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    list_file = dst_p.parent / f"{dst_p.stem}_concat.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in segment_paths:
            f.write(f"file '{Path(p).absolute().as_posix()}'\n")
    try:
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
              "-f", "concat", "-safe", "0", "-i", str(list_file),
              "-c", "copy", "-map_metadata", "-1", str(dst_p)], timeout=600)
    finally:
        try:
            list_file.unlink()
        except OSError:
            pass
    if not dst_p.exists() or dst_p.stat().st_size == 0:
        raise SequenceError("render_failed", f"拼接输出为空: {dst}")


# ============ validate output（24.md §30 机器检查）============

# 逐 clip 时长容差：1 视频帧（41.7ms@24fps）+ 1 AAC 帧（21.3ms@48kHz）+ 容器取整余量。
PER_CLIP_TOLERANCE_MS = 120
# 总时长容差 = 逐 clip 容差之和（S1 无 overlap，误差不叠加放大）。
TOTAL_TOLERANCE_MS_PER_CLIP = PER_CLIP_TOLERANCE_MS
# AV sync 容差：音视频流时长差（concat 层面漂移检测）。
AV_DELTA_TOLERANCE_MS = 150


def validate_output(path: str, planned_total_ms: int, n_clips: int) -> Dict[str, Any]:
    """输出机器校验（§30）：视频流/音频流存在、总 duration ≈ 计划、AV 不漂移。
    硬失败（output_invalid）只针对结构性错误；黑帧/静音属内容诊断，见
    detect_black_segments / detect_silence_segments（advisory，不在此 fail）。"""
    info = probe_video(path)
    problems: List[str] = []
    if not info.get("has_video", False):
        problems.append("输出无视频流")
    if not info["has_audio"]:
        problems.append("输出无音频流（源均应有 native audio，§15）")
    tolerance = TOTAL_TOLERANCE_MS_PER_CLIP * max(n_clips, 1)
    delta = info["duration_ms"] - planned_total_ms
    if abs(delta) > tolerance:
        problems.append(
            f"duration 偏差 {delta:+d}ms 超容差（actual {info['duration_ms']}ms vs "
            f"planned {planned_total_ms}ms，tolerance ±{tolerance}ms）")
    # AV sync：v/a 流时长差（probe_video 只有整体 duration，这里再取音频流时长）
    out = _run(["ffprobe", "-v", "error", "-print_format", "json",
                "-select_streams", "a", "-show_entries",
                "stream=duration", "-of", "json", str(path)], timeout=30)
    try:
        astreams = (json.loads(out or "{}").get("streams") or [])
        a_dur_ms = round(float(astreams[0]["duration"]) * 1000) if astreams and astreams[0].get("duration") else None
    except Exception:
        a_dur_ms = None
    if a_dur_ms is not None:
        av_delta = info["duration_ms"] - a_dur_ms
        if abs(av_delta) > AV_DELTA_TOLERANCE_MS:
            problems.append(f"AV sync 漂移 {av_delta:+d}ms（video {info['duration_ms']}ms "
                            f"vs audio {a_dur_ms}ms）")
    if problems:
        raise SequenceError("output_invalid", "；".join(problems))
    return {"probe": info, "duration_ms": info["duration_ms"],
            "planned_duration_ms": planned_total_ms, "duration_delta_ms": delta}


def detect_black_segments(path: str, *, min_dur_s: float = 0.5) -> List[Dict[str, Any]]:
    """advisory：blackdetect 全黑片段（§30「无意外黑帧」诊断）。
    深色场景可能误报——只记录不 fail，供人评参考。"""
    out = _run_diag(["ffmpeg", "-hide_banner", "-nostats",
                     "-i", str(path), "-vf",
                     f"blackdetect=d={min_dur_s}:pix_th=0.10",
                     "-an", "-f", "null", "-"], timeout=600)
    return _parse_diag(out, "black_start")


def detect_silence_segments(path: str, *, noise_db: int = -50, min_dur_s: float = 1.0
                            ) -> List[Dict[str, Any]]:
    """advisory：silencedetect 静音片段（§30「无意外静音」诊断）。只记录不 fail。"""
    out = _run_diag(["ffmpeg", "-hide_banner", "-nostats",
                     "-i", str(path), "-af",
                     f"silencedetect=noise={noise_db}dB:d={min_dur_s}",
                     "-vn", "-f", "null", "-"], timeout=600)
    return _parse_diag(out, "silence_start")


def _parse_diag(ffmpeg_stderr_out: str, start_key: str) -> List[Dict[str, Any]]:
    """解析 blackdetect/silencedetect 的 stderr 报告。
    🔴 44B 修复两处 S1 遗留缺陷：①报告写 stderr 而旧代码读 stdout；②实际输出形如
    `silence_start: 0`（冒号后有空格）且 silencedetect 的 start/end 在不同行——
    旧解析恒为空。现按行累积：start 开新段，end/duration 并入当前段，两种滤镜
    （blackdetect 单行三键 / silencedetect 跨行）统一处理。"""
    import re
    segs: List[Dict[str, Any]] = []
    end_key = start_key.replace("start", "end")
    dur_key = start_key.replace("start", "duration")
    keys = (start_key, end_key, dur_key)
    pat = re.compile(
        r"(" + "|".join(re.escape(k) for k in keys) + r"):\s*([-\d.]+)")
    cur: Dict[str, Any] = {}

    def _flush() -> None:
        nonlocal cur
        if cur:
            segs.append(cur)
            cur = {}

    for line in ffmpeg_stderr_out.splitlines():
        if not any(f"{k}:" in line for k in keys):
            continue
        found_start = False
        for m in pat.finditer(line):
            key, val = m.group(1), m.group(2)
            try:
                v = round(float(val), 3)
            except ValueError:
                continue
            if key == start_key:
                if not found_start:
                    _flush()          # 上一段闭合（silencedetect 跨行场景）
                    found_start = True
            cur[key] = v
    _flush()
    return segs


# ============ Phase 44B：帧级审计与短切点检查（B3）============
# 44B 任务书 B3：「短切点检查黑帧、重复/缺帧、PTS 单调性和时长误差」。
# 全部 advisory/结构化报告——不替代 validate_output 的硬校验，也不自动修片。

def _parse_framemd5(path: str) -> Any:
    """framemd5 全片走查（解码级 md5，零重编码）。返回 (timebase, rows)，
    rows = [{dts, pts, dur, hash}]（文件序）。中文路径 GBK 陷阱由 _run 显式 utf-8 覆盖。"""
    out = _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
                "-map", "0:v:0", "-f", "framemd5", "-"], timeout=900)
    tb: Optional[float] = None
    rows: List[Dict[str, Any]] = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("#tb"):
            # 形如 "#tb 0: 1/24"
            try:
                num, _, den = line.split(":", 1)[1].strip().partition("/")
                tb = float(num) / float(den)
            except (ValueError, ZeroDivisionError):
                tb = None
            continue
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        try:
            rows.append({"dts": int(parts[1]), "pts": int(parts[2]),
                         "dur": int(parts[3]), "hash": parts[5]})
        except ValueError:
            continue
    if not rows:
        raise SequenceError("probe_failed", f"framemd5 无帧数据: {path}")
    return tb, rows


def inspect_frames(path: str) -> Dict[str, Any]:
    """逐帧走查（framemd5 解码级）：PTS 单调性、连续重复帧（冻结嫌疑）、
    缺帧（pts 序列空洞）、帧数 vs 时长误差。零 re-encode（解码后 md5）。
    返回结构化事实 + issues 清单；帧级问题属 advisory（拼接待人评）。"""
    tb, rows = _parse_framemd5(path)
    issues: List[str] = []

    # PTS 单调性
    pts_vals = [r["pts"] for r in rows]
    pts_monotonic = all(b > a for a, b in zip(pts_vals, pts_vals[1:]))
    if not pts_monotonic:
        bad = next((i for i in range(1, len(pts_vals))
                    if pts_vals[i] <= pts_vals[i - 1]), None)
        issues.append(f"PTS 非单调（frame[{bad}] pts={pts_vals[bad]} "
                      f"<= prev={pts_vals[bad - 1]}）")

    # 缺帧：pts 集合空洞（期望 min..max 连续）。concat stream-copy 输出常见
    # 统一起始偏移（首帧 pts>0，如 muxer edit list 处理）——偏移本身非缺陷，
    # 只记录 start_pts；区间内空洞才算缺帧。
    pset = set(pts_vals)
    start_pts = min(pset)
    expected_count = max(pset) - start_pts + 1
    missing = expected_count - len(pset)
    if missing > 0:
        gaps = sorted(set(range(start_pts, max(pset) + 1)) - pset)
        issues.append(f"缺帧 {missing} 帧（pts 空洞示例: {gaps[:5]}）")

    # 连续重复帧（冻结嫌疑；生成视频逐帧有噪声，bit 级同帧即异常）
    dup_runs: List[Dict[str, Any]] = []
    run_start = 0
    for i in range(1, len(rows) + 1):
        if i < len(rows) and rows[i]["hash"] == rows[run_start]["hash"]:
            continue
        n = i - run_start
        if n >= 2:
            dup_runs.append({"start_pts": rows[run_start]["pts"],
                             "end_pts": rows[i - 1]["pts"], "count": n})
            issues.append(f"重复帧 {n} 连（pts {rows[run_start]['pts']}.."
                          f"{rows[i - 1]['pts']}，冻结/卡帧嫌疑）")
        run_start = i

    frame_dur_ms = (1.0 / tb * 1000) if tb else None
    return {"frame_count": len(rows),
            "timebase": tb,
            "frame_interval_ms": round(frame_dur_ms, 3) if frame_dur_ms else None,
            "start_pts": start_pts,
            "pts_monotonic": pts_monotonic,
            "missing_frames": missing,
            "dup_runs": dup_runs,
            "duration_from_frames_ms": round(expected_count * (frame_dur_ms or 0)),
            "issues": issues}


def audit_output_seams(path: str, seam_times_ms: List[int], *, fps: float = 24.0,
                       window_ms: int = 500,
                       black_segments: Optional[List[Dict[str, Any]]] = None,
                       silence_segments: Optional[List[Dict[str, Any]]] = None
                       ) -> Dict[str, Any]:
    """短切点机器检查（B3）：对每个接缝时间点检查 ±window 内——
    ① 黑帧（blackdetect 段与切点窗相交）；② 帧连续性（无 >1.5 帧间隔的空洞、
    无重复帧）；③ PTS 全片单调；④ 帧数 vs 时长误差。black/silence 段可外部传入
    （全片一次 detect 后复用）；不传则现场检测。advisory——只报告，不 fail。"""
    info = inspect_frames(path)
    if black_segments is None:
        black_segments = detect_black_segments(path)
    if silence_segments is None:
        silence_segments = detect_silence_segments(path)
    frame_int_ms = 1000.0 / fps
    tb, frames_raw = _parse_framemd5(path)
    tb = tb or (1.0 / fps)
    frames = [{"pts": r["pts"], "hash": r["hash"]} for r in frames_raw]
    seam_reports: List[Dict[str, Any]] = []
    for seam in seam_times_ms:
        w0 = (seam - window_ms) / 1000.0
        w1 = (seam + window_ms) / 1000.0
        win = [f for f in frames if w0 <= f["pts"] * tb <= w1]
        seam_issues: List[str] = []
        # 帧空洞：相邻 pts 间隔 > 1.5 帧间隔
        for a, b in zip(win, win[1:]):
            gap_ms = (b["pts"] - a["pts"]) * tb * 1000
            if gap_ms > frame_int_ms * 1.5 + 1:
                seam_issues.append(f"切点窗内帧空洞 {gap_ms:.0f}ms"
                                   f"（pts {a['pts']}→{b['pts']}）")
        # 重复帧
        for a, b in zip(win, win[1:]):
            if a["hash"] == b["hash"]:
                seam_issues.append(f"切点窗内重复帧（pts {b['pts']}）")
        # 黑帧相交
        for seg in black_segments:
            bs = seg.get("black_start", 0.0) * 1000
            be = seg.get("black_end", 0.0) * 1000
            if be >= w0 * 1000 and bs <= w1 * 1000:
                seam_issues.append(f"切点窗内黑帧 {bs:.0f}..{be:.0f}ms")
        seam_reports.append({
            "seam_ms": seam,
            "frames_in_window": len(win),
            "issues": seam_issues,
        })
    return {"path": str(path),
            "frames": {k: info[k] for k in ("frame_count", "pts_monotonic",
                                            "missing_frames", "duration_from_frames_ms")},
            "dup_runs": info["dup_runs"],
            "issues": info["issues"],
            "black_segments": black_segments,
            "silence_segments": silence_segments,
            "seams": seam_reports,
            "seams_with_issues": sum(1 for s in seam_reports if s["issues"])}
