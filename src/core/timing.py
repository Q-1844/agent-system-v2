# src/core/timing.py
# 配音前置：由真实音频时长反推视频帧数（AGNES_API_FACTS §3.4）。
# 阶段 2（Audio-First）：视频时长在提交时被 num_frames 锁死改不了，
# 所以必须先 TTS 拿真实音频时长，再反推 num_frames，避免音画逐镜错位。
from __future__ import annotations
import math
import re
from dataclasses import dataclass

BREATH_SEC   = 0.6      # 台词前后留的呼吸口
MIN_FRAMES   = 81       # ≈3.4s @24fps
MAX_FRAMES   = 441      # ≈18.375s @24fps（全局上限；720p 实测可达 441，见 max_frames_for）
SILENT_SEC   = 3.0      # 无台词镜头的默认时长
DEFAULT_FPS  = 24


@dataclass
class ShotTiming:
    num_frames: int
    frame_rate: int
    planned_seconds: float
    audio_seconds: float
    needs_split: bool          # True = 这句台词塞不进一个镜头
    suggested_splits: int      # 建议拆成几个镜头


def snap_8n1(frames: int, max_frames: int = MAX_FRAMES) -> int:
    """夹到最近的 8n+1，限制在 [MIN_FRAMES, max_frames]。AGNES_API_FACTS §3.4"""
    n = round((frames - 1) / 8)
    return max(MIN_FRAMES, min(max_frames, 8 * int(n) + 1))


def plan_shot_timing(audio_seconds: float, frame_rate: int = DEFAULT_FPS,
                     breath: float = BREATH_SEC,
                     max_frames: int = MAX_FRAMES) -> ShotTiming:
    """由真实音频时长反推视频帧数。AGNES_API_FACTS §3.4

    max_frames 默认 441（720p 实测上限）；如用其它分辨率，传 max_frames_for(w,h)。
    """
    if audio_seconds <= 0:
        target = SILENT_SEC
    else:
        target = audio_seconds + breath

    max_sec = max_frames / frame_rate           # 24fps 下 = 18.375s
    needs_split = target > max_sec
    splits = 1
    if needs_split:
        # 留 10% 余量，避免拆完后某段又恰好超限
        splits = math.ceil(target / (max_sec * 0.9))

    frames = snap_8n1(int(round(target * frame_rate)), max_frames)
    return ShotTiming(num_frames=frames, frame_rate=frame_rate,
                      planned_seconds=frames / frame_rate,
                      audio_seconds=audio_seconds,
                      needs_split=needs_split, suggested_splits=splits)


def split_dialogue(text: str, frame_rate: int = DEFAULT_FPS,
                   chars_per_sec: float = 4.0,
                   max_frames: int = MAX_FRAMES) -> list[str]:
    """把超长台词按标点贪心切成能塞进单镜的片段。
    参考开源项目 core/pipelines/manuscript_video.py 的分段策略：
    按 。？！；切句，按约 4 字/秒估算时长，贪心合并成 5–12 秒的片段。

    注意：4 字/秒只是估算，用于「切之前预判」。真正的时长一律以 TTS 实测为准。
    切完后每段仍要走一次 TTS 拿真实时长。
    """
    max_sec = (max_frames / frame_rate) * 0.85
    sentences = [s for s in re.split(r'(?<=[。？！；\.\?\!])', text) if s.strip()]
    out, buf = [], ""
    for s in sentences:
        cand = buf + s
        if len(cand) / chars_per_sec > max_sec and buf:
            out.append(buf); buf = s
        else:
            buf = cand
    if buf: out.append(buf)
    return out or [text]
