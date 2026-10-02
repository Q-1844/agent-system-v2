# src/sequence/audio_audit.py
# Phase 45.1 — 混音成片的逐层音频落窗审计（确定性，无 LLM）。
#
# 背景（PHASE44_C_REPORT §9.3 教训）：单遍「混音图→AAC」曾把除首句外的全部延迟层
# 丢进 AAC，而"只查第一句/只听开头"恰好通过。本模块把当年一次性脚本
# （_44c10_mix_full.py）的验收判据升为正式工具面：对 AudioPlan 的每一层取
# 「源 WAV（仓内 sha256 已知）与终混对应时间窗」的 Pearson 相关 + 分窗电平，
# 不允许"仅首句通过"掩盖整片缺失。
#
# 判据（与 44C 实测分布一致，advisory 汇总 + passed 布尔）：
#   narration 单窗（cue 落窗）corr ≥ 0.5 且窗电平 > -55 dBFS；
#   music/ambience 三探针窗（避开 fade）corr ≥ 0.5；
#   base_gain_db ≤ -50 时，无层活动间隙窗视为"原生静默"抽查（> -50 dBFS 判泄漏）；
#   loudness 用 ebur128 实测（I/TP/LRA 只报告，达标与否交上游定值方）。
# 🔴 本模块不做任何审美判断，不写 canonical——只产生证据；PCM 解码经 ffmpeg，
#    相关计算纯 Python（16kHz mono，src 不引入 numpy 依赖）。
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import SequenceError

AUDIT_SAMPLE_RATE = 16000
CORRELATION_PASS = 0.5
SILENT_WINDOW_DB = -55.0
NATIVE_SILENT_DB = -50.0          # base_gain ≤ 此值时做间隙窗静默抽查
PROBE_WINDOW_MS = 2000            # music 探针窗长（上限）
MIN_GAP_PROBE_MS = 800            # 间隙探针最短窗长（低于此不作为探针）
N_MUSIC_PROBES = 3
GAP_WINDOW_MS = 500               # 原生静默抽查窗长


def _run_bytes(cmd: List[str], *, timeout: int = 60) -> bytes:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise SequenceError("media_op_timeout", f"音频审计超时: {Path(cmd[-1]).name}") from e
    except OSError as e:
        raise SequenceError("media_op_failed", f"ffmpeg 启动失败: {e}") from e
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace")
        raise SequenceError("media_op_failed", f"ffmpeg 解码失败: {err.strip()[-300:]}")
    return proc.stdout


def _decode_segment(path: str, start_ms: int, duration_ms: int) -> List[float]:
    """解码 [start_ms, start_ms+duration_ms) 为 16kHz mono float 列表（-1..1）。
    源越界时 ffmpeg 返回短样本——调用方对长度差做截断对齐。"""
    dur_s = max(duration_ms, 1) / 1000.0
    start_s = max(start_ms, 0) / 1000.0
    out = _run_bytes(["ffmpeg", "-v", "error", "-ss", f"{start_s:.3f}",
                      "-t", f"{dur_s:.3f}", "-i", str(path),
                      "-ac", "1", "-ar", str(AUDIT_SAMPLE_RATE),
                      "-f", "s16le", "-"])
    n = len(out) // 2
    if n <= 0:
        return []
    samples: List[float] = []
    b = out
    for i in range(0, n * 2, 2):
        v = b[i] | (b[i + 1] << 8)
        if v >= 32768:
            v -= 65536
        samples.append(v / 32768.0)
    return samples


def _pearson(a: List[float], b: List[float]) -> Optional[float]:
    n = min(len(a), len(b))
    if n < 64:
        return None
    sa = a[:n]
    sb = b[:n]
    ma = sum(sa) / n
    mb = sum(sb) / n
    num = 0.0
    da = 0.0
    dbv = 0.0
    for x, y in zip(sa, sb):
        dx = x - ma
        dy = y - mb
        num += dx * dy
        da += dx * dx
        dbv += dy * dy
    if da <= 1e-12 or dbv <= 1e-12:
        return None
    return num / math.sqrt(da * dbv)


def _rms_db(samples: List[float]) -> Optional[float]:
    if not samples:
        return None
    acc = 0.0
    for v in samples:
        acc += v * v
    rms = math.sqrt(acc / len(samples))
    if rms <= 1e-9:
        return -120.0
    return 20.0 * math.log10(rms)


def measure_loudness(path: str) -> Dict[str, Optional[float]]:
    """ebur128 实测（Summary 段）：I (LUFS) / True peak (dBTP) / LRA (LU)。
    ebur128 把报告写 stderr——用 _run_bytes 拿 stdout 会丢，故独立子进程取 stderr。"""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-nostats", "-i", str(path), "-af",
             "ebur128=peak=true", "-f", "null", "-"],
            capture_output=True, timeout=120)
        stderr = proc.stderr.decode("utf-8", errors="replace")
    except (subprocess.TimeoutExpired, OSError) as e:
        raise SequenceError("media_op_failed", f"ebur128 测量失败: {e}") from e
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace")
        raise SequenceError("media_op_failed", f"ebur128 失败: {err.strip()[-300:]}")
    out: Dict[str, Optional[float]] = {"integrated_lufs": None,
                                       "true_peak": None,
                                       "true_peak_unit": None,
                                       "lra_lu": None}
    # ebur128 流式行（t: … I: …）每帧都在打 I/LRA——只有 Summary 段的才是全片值，
    # 取最后一次匹配（Summary 行在输出末尾）。部分构建只打 dBFS 峰值不打 dBTP。
    i_all = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", stderr)
    if i_all:
        out["integrated_lufs"] = float(i_all[-1])
    tp = re.findall(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*(dBTP|dBFS)", stderr)
    if tp:
        out["true_peak"] = float(tp[-1][0])
        out["true_peak_unit"] = tp[-1][1]
    lra_all = re.findall(r"LRA:\s*(-?\d+(?:\.\d+)?)\s*LU\b", stderr)
    if lra_all:
        out["lra_lu"] = float(lra_all[-1])
    return out


def _layer_windows(layer: Dict[str, Any], total_ms: int) -> List[Dict[str, int]]:
    """该层在时间线上的有效窗（裁到片长内）。"""
    start = int(layer.get("start_ms") or 0)
    dur = int(layer.get("duration_ms") or 0)
    end = min(start + dur, total_ms)
    if end - start < 300:
        return []
    return [{"start_ms": start, "end_ms": end}]


def _merge_busy(windows: List[List[int]]) -> List[List[int]]:
    out: List[List[int]] = []
    for s, e in sorted(windows):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def _music_probe_windows(layer: Dict[str, Any], total_ms: int,
                         busy_windows: Optional[List[List[int]]] = None
                         ) -> List[Dict[str, int]]:
    """音乐/环境层探针窗：优先取【无旁白重叠】的窗——ducking 会按旁白包络压低
    音乐电平（44C §4），重叠窗的相关性天然失真（真机实测 0.15-0.37，间隙窗
    0.998）。无间隙可用时退化为均匀窗并在结果中如实标注。"""
    start = int(layer.get("start_ms") or 0)
    end = min(start + int(layer.get("duration_ms") or 0), total_ms)
    fade_in = int(layer.get("fade_in_ms") or 0)
    fade_out = int(layer.get("fade_out_ms") or 0)
    lo = start + max(fade_in, 500)
    hi = end - max(fade_out, 500)
    if hi - lo < PROBE_WINDOW_MS:
        lo, hi = start, end
    if hi - lo < 300:
        return []
    span = hi - lo - PROBE_WINDOW_MS
    uniform = [{"start_ms": int(lo + span * i / (N_MUSIC_PROBES - 1)),
                "end_ms": int(lo + span * i / (N_MUSIC_PROBES - 1)) + PROBE_WINDOW_MS}
               for i in range(N_MUSIC_PROBES)] if span > 0 else \
        [{"start_ms": lo, "end_ms": lo + PROBE_WINDOW_MS}]
    if not busy_windows:
        return uniform
    busy = _merge_busy(busy_windows)
    gaps: List[List[int]] = []
    cursor = lo
    for s, e in busy:
        if e <= lo or s >= hi:
            continue
        s2, e2 = max(s, lo), min(e, hi)
        if s2 - cursor >= MIN_GAP_PROBE_MS:
            gaps.append([cursor, s2])
        cursor = max(cursor, e2)
    if hi - cursor >= MIN_GAP_PROBE_MS:
        gaps.append([cursor, hi])
    if not gaps:
        return uniform          # 无可用间隙：均匀窗 + probe_basis 如实标注
    # 间隙内取最多 3 个探针窗：窗长=间隙长（上限 PROBE_WINDOW_MS，下限已由
    # MIN_GAP_PROBE_MS 保证）——旁白密集段（如 44C s2 修理间）间隙常短于 2s。
    probes: List[Dict[str, int]] = []
    for gi, (gs, ge) in enumerate(gaps[:N_MUSIC_PROBES]):
        plen = min(ge - gs, PROBE_WINDOW_MS)
        center = (gs + ge) // 2
        s = max(gs, min(center - plen // 2, ge - plen))
        probes.append({"start_ms": int(s), "end_ms": int(s + plen)})
    probes.sort(key=lambda w: w["start_ms"])
    return probes


def _source_window(layer: Dict[str, Any], win: Dict[str, int],
                   project_output_dir: Path) -> List[float]:
    src = layer.get("source_file") or ""
    p = (project_output_dir / src)
    if not p.is_file():
        raise SequenceError("audio_audit_invalid_source",
                            f"层 {layer.get('layer_id')} 源文件不存在: {src}")
    src_in = int(layer.get("source_in_ms") or 0)
    # 源内偏移 = 窗在层内的时间 + 层在源内的入点
    off = (win["start_ms"] - int(layer.get("start_ms") or 0)) + src_in
    dur = win["end_ms"] - win["start_ms"]
    return _decode_segment(str(p), off, dur)


def audit_audio_render(*, mix_file: str, layers: List[Dict[str, Any]],
                       project_output_dir: Path,
                       base_gain_db: Optional[float] = None,
                       mix_duration_ms: Optional[int] = None,
                       render_version_id: Optional[str] = None,
                       shot_boundaries_ms: Optional[List[int]] = None
                       ) -> Dict[str, Any]:
    """对一份混音成片做逐层落窗审计。layers=AudioPlan 层 dict 列表
    （layer_id/role/source_file/start_ms/duration_ms/source_in_ms/gain_db/fade_*）。
    shot_boundaries_ms：镜头切点（内部）；旁白层窗跨越切点时在该层记 advisory
    （是否可接受属创作口径，不判 fail——45.5 教训：越窗要有确定性证据可看）。
    返回证据 dict；结构性错误抛 SequenceError（fail closed），内容不达标进 failures。"""
    if mix_duration_ms is None:
        from .media_ops import probe_video
        mix_duration_ms = int(probe_video(mix_file).get("duration_ms") or 0)
    failures: List[str] = []
    narration_rows: List[Dict[str, Any]] = []
    music_rows: List[Dict[str, Any]] = []
    # 音乐探针的 busy 集 = 旁白层窗（ducking 键信号；无旁白层则全片无 busy）
    narration_busy = [[int(l.get("start_ms") or 0),
                       int(l.get("start_ms") or 0)
                       + int(l.get("duration_ms") or 0)]
                      for l in (layers or []) if l.get("role") == "narration"]

    for layer in layers or []:
        lid = layer.get("layer_id") or "?"
        role = layer.get("role") or ""
        if role == "narration":
            wins = _layer_windows(layer, mix_duration_ms)
            probe_basis = "cue_window"
        else:
            wins = _music_probe_windows(layer, mix_duration_ms,
                                        busy_windows=narration_busy)
            probe_basis = ("gap_windows" if narration_busy
                           else "uniform_windows")
        if not wins:
            failures.append(f"[{lid}] 层窗口越界或过短（start={layer.get('start_ms')}"
                            f" dur={layer.get('duration_ms')}）")
            continue
        probes: List[Dict[str, Any]] = []
        corrs: List[float] = []
        for win in wins:
            mix_seg = _decode_segment(mix_file, win["start_ms"],
                                      win["end_ms"] - win["start_ms"])
            src_seg = _source_window(layer, win, project_output_dir)
            corr = _pearson(mix_seg, src_seg)
            lvl = _rms_db(mix_seg)
            probes.append({"start_ms": win["start_ms"], "end_ms": win["end_ms"],
                           "correlation": (round(corr, 3) if corr is not None else None),
                           "window_rms_db": (round(lvl, 1) if lvl is not None else None)})
            if corr is not None:
                corrs.append(corr)
        min_corr = min(corrs) if corrs else None
        row: Dict[str, Any] = {"layer_id": lid, "role": role,
                               "probe_basis": probe_basis,
                               "windows": probes, "min_correlation": min_corr}
        if role == "narration" and shot_boundaries_ms:
            s_l = int(layer.get("start_ms") or 0)
            e_l = s_l + int(layer.get("duration_ms") or 0)
            crossed = [b for b in shot_boundaries_ms if s_l < b < e_l]
            if crossed:
                row["crosses_shot_cut_ms"] = crossed
                row["advisory"] = ("旁白窗跨越镜头切点——是否可接受属创作口径；"
                                   "若项目纪律要求逐句落镜，请改短/移位该句")
        # 🔴 判定按【每个窗】逐一对齐：静音窗的相关性是 None（不可伪装通过）——
        #    44C §9.3 的"仅首句在场"教训在窗粒度的等价缺陷。
        all_corr_ok = all(
            w["correlation"] is not None
            and w["correlation"] >= CORRELATION_PASS for w in probes)
        if role == "narration":
            first = probes[0] if probes else {}
            row["window_rms_db"] = first.get("window_rms_db")
            ok = all_corr_ok and (row["window_rms_db"] is None
                                  or row["window_rms_db"] > SILENT_WINDOW_DB)
        else:
            ok = all_corr_ok
        row["pass"] = bool(ok)
        if not ok:
            failures.append(
                f"[{lid}] {'旁白' if role == 'narration' else '音乐/环境'}层落窗证据不足"
                f"（min_corr={min_corr}）")
        (narration_rows if role == "narration" else music_rows).append(row)

    # 原生音泄漏抽查：base 静默策略下，无层活动间隙窗应接近数字静音
    native_leakage: Dict[str, Any] = {"checked": False}
    if base_gain_db is not None and base_gain_db <= NATIVE_SILENT_DB:
        busy: List[List[int]] = []
        for layer in layers or []:
            s = int(layer.get("start_ms") or 0)
            busy.append([s, min(s + int(layer.get("duration_ms") or 0),
                                mix_duration_ms)])
        busy.sort()
        gaps: List[List[int]] = []
        cursor = 0
        for s, e in busy:
            if s - cursor >= GAP_WINDOW_MS:
                gaps.append([cursor + 100, cursor + 100 + GAP_WINDOW_MS])
            cursor = max(cursor, e)
        gap_rows: List[Dict[str, Any]] = []
        worst = None
        for gs, ge in gaps[:4]:
            seg = _decode_segment(mix_file, gs, ge - gs)
            lvl = _rms_db(seg)
            lvl_r = round(lvl, 1) if lvl is not None else None
            gap_rows.append({"start_ms": gs, "rms_db": lvl_r})
            if lvl is not None and (worst is None or lvl > worst):
                worst = lvl
        native_leakage = {"checked": True, "base_gain_db": base_gain_db,
                          "gap_windows": gap_rows,
                          "max_gap_rms_db": (round(worst, 1) if worst is not None
                                             else None),
                          "pass": worst is None or worst <= NATIVE_SILENT_DB}
        if native_leakage["pass"] is False:
            failures.append("[native] base 静默策略下间隙窗电平 > -50 dBFS（疑似原生音泄漏）")

    loud = measure_loudness(mix_file)
    return {"schema": "audio_audit_v1",
            "render_version_id": render_version_id,
            "mix_duration_ms": mix_duration_ms,
            "loudness": loud,
            "narration_layers": narration_rows,
            "music_layers": music_rows,
            "native_leakage": native_leakage,
            "thresholds": {"correlation_pass": CORRELATION_PASS,
                           "silent_window_db": SILENT_WINDOW_DB,
                           "native_silent_db": NATIVE_SILENT_DB},
            "passed": not failures,
            "failures": failures}
