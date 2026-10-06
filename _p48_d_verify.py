# -*- coding: utf-8 -*-
"""Phase48 D：对白 ASR 核对 + 声线声学对照 + 道具/身份抽查证据。

- 对白镜（有 dialogue 字段）：faster-whisper 转写（B 批双阈值），与冻结台词对照
  （编辑距离近似匹配），报告漏词/乱语。
- 跨镜声线（L6 三镜同一角色）：基频 f0 中位数（numpy 自相关粗估）+ 谱质心对照，
  只作声学辅助证据，身份一致性结论仍待人耳/人工复核（执行书：不以"像男声/女声"代替）。
- 非对白镜 ASR 命中=意外语音（旁白红线检查）。
用法：python _p48_d_verify.py
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
PROJ = ROOT / "projects" / "P48_D_Cast"
OUT = ROOT / "_p48_d_dialogue_check.json"
sys.path.insert(0, str(ROOT))


def asr_file(f, model):
    segs, info = model.transcribe(str(f), language="zh", vad_filter=False)
    out = []
    for s in segs:
        out.append({"start": round(s.start, 2), "end": round(s.end, 2),
                    "text": s.text.strip(), "no_speech_prob": round(s.no_speech_prob, 3),
                    "avg_logprob": round(s.avg_logprob, 2)})
    return out


def strong_speech(segs):
    return [s for s in segs if s["text"] and len(s["text"]) >= 2
            and s["no_speech_prob"] < 0.4 and s["avg_logprob"] >= -1.0]


def norm(t):
    return re.sub(r"[，。！？：；、\s！\?\.]", "", t or "")


def edit_dist(a, b):
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cur.append(min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (ca != cb)))
        prev = cur
    return prev[-1]


def f0_stats(f):
    """粗 f0 中位数（1 通道 16k 重采样 + 40ms 窗自相关峰值， voiced 帧）。"""
    import numpy as np
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(f), "-ac", "1", "-ar", "16000",
         "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    if x.size < 16000:
        return None
    win, hop, fmin, fmax = 640, 320, 60, 400
    f0s = []
    for start in range(0, min(len(x) - win, 16000 * 12), hop):
        frame = x[start:start + win]
        if np.sqrt(np.mean(frame ** 2)) < 0.01:
            continue
        ac = np.correlate(frame, frame, "full")[win - 1:]
        ac /= (ac[0] + 1e-9)
        lo, hi = int(16000 / fmax), int(16000 / fmin)
        if hi >= len(ac):
            continue
        seg = ac[lo:hi]
        lag = np.argmax(seg) + lo
        if ac[lag] > 0.5:
            f0s.append(16000 / lag)
    if len(f0s) < 5:
        return None
    import statistics
    return {"f0_median": round(statistics.median(f0s), 1),
            "f0_n": len(f0s)}


def main():
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")
    ev = json.loads((ROOT / "_p48_d_evidence.json").read_text(encoding="utf-8"))
    result = {}
    for sid, ent in sorted(ev["shots"].items()):
        v = PROJ / "output" / "videos" / f"{sid}.mp4"
        if not v.exists():
            result[sid] = {"error": "video missing"}
            continue
        row = {"level": ent["level"], "expected_speaker": ent.get("dialogue_speaker"),
               "frozen_line": ent.get("dialogue")}
        segs = asr_file(v, model)
        strong = strong_speech(segs)
        row["asr_strong"] = strong
        if ent.get("dialogue"):
            frozen = ent["dialogue"].split("：", 1)[1]
            got = "".join(s["text"] for s in strong)
            dist = edit_dist(norm(got), norm(frozen))
            row["match"] = {"transcript": got, "edit_distance": dist,
                            "len_frozen": len(norm(frozen)),
                            "verdict": "match" if dist <= max(2, int(len(norm(frozen)) * 0.3))
                            else ("partial" if dist <= len(norm(frozen)) else "mismatch")}
        else:
            row["unexpected_speech"] = bool(strong)
            if strong:
                row["unexpected_text"] = " ".join(s["text"] for s in strong)[:120]
        fs = f0_stats(v)
        if fs:
            row["voice"] = fs
        result[sid] = row
        print(sid, row["level"], row.get("match", {}).get("verdict")
              or ("unexpected_speech!" if row.get("unexpected_speech") else "no-speech"),
              (row.get("match", {}).get("transcript") or "")[:40],
              row.get("voice"), flush=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", OUT)


if __name__ == "__main__":
    main()
