# src/core/subtitle.py
# 从 edge-tts 词级时间戳生成 SRT。全片只生成一条 SRT，在最终拼接完成后整体叠加，
# 绝不"每镜一个 srt 逐段偏移"（误差会累积）。参考 reference/agnes-video-generator/core/audio/subtitle.py。
from __future__ import annotations
from typing import List, Dict, Any

MAX_LINE_CHARS   = 18      # 单行最大字数，超了折成两行
MAX_CUE_SEC      = 3.0     # 单条字幕最长时间
MAX_CUE_CHARS    = 30      # 单条字幕最大字数
BREAK_PUNCT      = "，,。.！!？?；;、"


def _fmt(ms: int) -> str:
    ms = max(0, int(ms))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _wrap(text: str) -> str:
    """长文本折成两行，优先在标点处断。"""
    if len(text) <= MAX_LINE_CHARS:
        return text
    mid = len(text) // 2
    best, bestd = mid, 10**9
    for i, ch in enumerate(text):
        if ch in BREAK_PUNCT and abs(i - mid) < bestd:
            best, bestd = i + 1, abs(i - mid)
    return text[:best].strip() + "\n" + text[best:].strip()


def words_to_cues(words: List[Dict[str, Any]],
                  offset_ms: int = 0) -> List[Dict[str, Any]]:
    """把词级时间戳贪心合并成 2–3 秒一条的字幕。
    参考：reference/agnes-video-generator/core/audio/subtitle.py"""
    cues, buf = [], []

    def flush():
        if not buf: return
        start = buf[0]["offset_ms"] + offset_ms
        end   = buf[-1]["offset_ms"] + buf[-1]["duration_ms"] + offset_ms
        cues.append({"start_ms": start, "end_ms": end,
                     "text": "".join(w["text"] for w in buf).strip()})
        buf.clear()

    for w in words:
        if buf:
            span  = (w["offset_ms"] + w["duration_ms"] - buf[0]["offset_ms"]) / 1000
            chars = sum(len(x["text"]) for x in buf) + len(w["text"])
            if span > MAX_CUE_SEC or chars > MAX_CUE_CHARS:
                flush()
        buf.append(w)
        if w["text"] and w["text"][-1] in "。！？.!?":
            flush()
    flush()
    return cues


def cues_to_srt(cues: List[Dict[str, Any]]) -> str:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{_fmt(c['start_ms'])} --> {_fmt(c['end_ms'])}\n"
                   f"{_wrap(c['text'])}\n")
    return "\n".join(out)


def build_global_srt(shots_timeline: List[Dict[str, Any]]) -> str:
    """全片一条 SRT。

    shots_timeline: [{"start_ms": 该镜头在成片中的起点, "words": [...]}, ...]

    🔴 关键：全片只生成一个 SRT，在最终拼接完成后整体叠加。
    绝对不要「每镜一个 srt，合成时逐段偏移叠加」--那样误差会累积。
    这也是开源项目 core/compositor/concatenator.py 采用的策略。"""
    all_cues = []
    for item in shots_timeline:
        all_cues.extend(words_to_cues(item["words"], offset_ms=item["start_ms"]))
    all_cues.sort(key=lambda c: c["start_ms"])
    return cues_to_srt(all_cues)
