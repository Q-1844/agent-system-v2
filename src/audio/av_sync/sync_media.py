# src/audio/av_sync/sync_media.py
# Audio V1 — Phase A2：sync working artifact 构造 + production review mux
# （16.md §6/§22）。
#
# build_sync_input：[lead silence][approved production WAV][tail silence]
#   → sync_input.wav，时长 ≈ source video duration（48k/mono/s16，
#   48 samples/ms 整除无舍入）。🔴 derived working artifact——不是新的
#   DialogueAudioVersion，绝不写回 dialogue_audio.json，绝不改 approved 产物。
# mux_production_review：provider raw mp4 的视频流 copy + 正式 sync_input 音频
#   → production review mp4（review 听到的必须是我们 approved 的音频，§22；
#   避免无意义二次视频编码）。
from __future__ import annotations

import subprocess
import wave
from pathlib import Path
from typing import Any, Dict

from src.audio.media_ops import AudioMediaError, ffprobe_audio

# production WAV 统一规格（A1 §17B 同口径）
WAV_RATE, WAV_CHANNELS, WAV_WIDTH = 48000, 1, 2
SAMPLES_PER_MS = WAV_RATE // 1000        # 48 samples / ms（整除）


class SyncMediaError(Exception):
    """sync working/production 媒体构造失败（service 层转
    audio_preparation_failed / audio_normalization_failed）。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


def _read_production_wav(path: Path) -> bytes:
    """读取 approved production WAV（48k/mono/s16），返回 PCM 帧。
    规格不符 → 拒绝（production WAV 由 A1 统一产出，出现别的规格=上游异常）。"""
    with wave.open(str(path), "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        if (rate, ch, width) != (WAV_RATE, WAV_CHANNELS, WAV_WIDTH):
            raise SyncMediaError(
                "audio_preparation_failed",
                f"sync_input 源必须是 {WAV_RATE}Hz/mono/s16 production WAV，"
                f"收到 {rate}Hz/{ch}ch/{width * 8}bit（上游规格异常，不静默重采样）")
        return w.readframes(w.getnframes())


def build_sync_input(production_wav_path, out_path, *,
                     speech_start_ms: int, target_duration_ms: int) -> Dict[str, Any]:
    """构造 sync_input.wav（§6）。deterministic：同输入 → 同字节。
    start + audio > target → 拒绝（VIDEO_TOO_SHORT 的双保险，上游已 fail closed）。"""
    src = Path(production_wav_path)
    if not src.exists():
        raise SyncMediaError("audio_preparation_failed", f"production WAV 缺失: {src}")
    frames = _read_production_wav(src)
    start_n = max(0, int(speech_start_ms)) * SAMPLES_PER_MS
    target_n = max(0, int(target_duration_ms)) * SAMPLES_PER_MS
    audio_n = len(frames) // (WAV_CHANNELS * WAV_WIDTH)
    if start_n + audio_n > target_n:
        raise SyncMediaError(
            "audio_preparation_failed",
            f"audio ({audio_ms_str(audio_n)}) 放不进 target {target_duration_ms}ms"
            f"（start={speech_start_ms}ms）——video_too_short 双保险触发")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    silence = b"\x00" * (WAV_CHANNELS * WAV_WIDTH)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(WAV_CHANNELS)
        w.setsampwidth(WAV_WIDTH)
        w.setframerate(WAV_RATE)
        w.writeframes(silence * start_n)
        w.writeframes(frames)
        w.writeframes(silence * (target_n - start_n - audio_n))
    probe = ffprobe_audio(out_path)
    return {"path": str(out_path), "duration_ms": target_duration_ms,
            "spec": {"sample_rate": WAV_RATE, "channels": WAV_CHANNELS,
                     "sample_width": WAV_WIDTH},
            "speech_start_ms": int(speech_start_ms),
            "audio_duration_ms": audio_n // SAMPLES_PER_MS,
            "ffprobe": probe or {"error": "ffprobe_unavailable"}}


def audio_ms_str(frames_n: int) -> str:
    return f"{frames_n // SAMPLES_PER_MS}ms"


def mux_production_review(raw_video_path, sync_input_path, out_path) -> Dict[str, Any]:
    """production review mp4（§22）：provider raw 的视频流 copy + 正式 sync_input
    音频（AAC）。deterministic 参数；不覆盖 provider original。"""
    raw_p, in_p, out_p = Path(raw_video_path), Path(sync_input_path), Path(out_path)
    for p, label in ((raw_p, "provider raw"), (in_p, "sync_input")):
        if not p.exists():
            raise SyncMediaError("audio_normalization_failed", f"{label} 缺失: {p}")
    if out_p.exists():
        raise SyncMediaError("audio_normalization_failed",
                             f"production 输出路径已存在（id 分配异常）: {out_p.name}")
    out_p.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(raw_p), "-i", str(in_p),
           "-map", "0:v:0", "-map", "1:a:0",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(WAV_RATE),
           str(out_p)]
    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise SyncMediaError(
            "audio_normalization_failed",
            f"production mux 失败：{proc.stderr.decode('utf-8', errors='replace').strip()[:300]}")
    if not out_p.exists() or out_p.stat().st_size == 0:
        raise SyncMediaError("audio_normalization_failed", "mux 未产出文件（输出为空）")
    return {"path": str(out_p)}
