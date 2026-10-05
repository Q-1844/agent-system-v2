# -*- coding: utf-8 -*-
"""Phase48 B2：12 条样本的原生音轨分类证据（真实媒体检查）。

每条产出：
- 容器/音轨参数（ffprobe）
- 响度：ebur128 integrated LUFS + peak（dBFS）
- 波形：RMS/max/静音占比（astats）
- 语音：faster-whisper（small，本机 CPU）逐段转写 + no_speech_prob
- 分类：speech（对话/旁白按镜头意图与画面判断）/ near_silent / non_speech
音乐 vs 环境/动作声的细分超出声学可自动判定的边界 → 标注 music_uncertain，
留待人耳复核（报告中如实说明）。
用法：python _p48_b_audio_classify.py
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
PROJ = ROOT / "projects" / "P48_B_Audio"
OUT = ROOT / "_p48_b_audio_labels.json"

INTENTS = {
    "s01_c01": "action_sfx", "s01_c02": "action_sfx",
    "s02_c01": "ambience", "s02_c02": "ambience",
    "s03_c01": "music_mixed", "s03_c02": "music_mixed",
    "s04_c01": "dialogue", "s04_c02": "dialogue",
    "s05_c01": "likely_silent", "s05_c02": "likely_silent",
    "s06_c01": "narration_risk", "s06_c02": "narration_risk",
}


def ffprobe_streams(f: Path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams",
                        "-of", "json", str(f)], capture_output=True, text=True)
    d = json.loads(r.stdout or "{}")
    a = [s for s in d.get("streams", []) if s.get("codec_type") == "audio"]
    v = [s for s in d.get("streams", []) if s.get("codec_type") == "video"]
    return {
        "video": {"codec": v[0]["codec_name"], "w": v[0]["width"], "h": v[0]["height"],
                  "duration_s": float(v[0].get("duration") or 0)} if v else None,
        "audio": {"codec": a[0]["codec_name"], "sample_rate": a[0]["sample_rate"],
                  "channels": a[0]["channels"],
                  "duration_s": float(a[0].get("duration") or 0)} if a else None,
    }


def loudness(f: Path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(f),
                        "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                       capture_output=True, text=True)
    t = r.stderr
    i_lufs = re.search(r"I:\s*(-?[\d.]+)\s*LUFS", t)
    peak = re.search(r"Peak:\s*(-?[\d.]+)\s*dBFS", t)
    return {"integrated_lufs": float(i_lufs.group(1)) if i_lufs else None,
            "peak_dbfs": float(peak.group(1)) if peak else None}


def astats(f: Path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(f),
                        "-af", "astats=metadata=1:reset=0", "-f", "null", "-"],
                       capture_output=True, text=True)
    t = r.stderr
    rms = re.findall(r"RMS level dB:\s*(-?[\d.]+)", t)
    mx = re.findall(r"Max level dB:\s*(-?[\d.]+)", t)
    return {"rms_db": float(rms[-1]) if rms else None,
            "max_db": float(mx[-1]) if mx else None}


def asr(f: Path, model):
    segs, info = model.transcribe(str(f), language=None, vad_filter=False)
    out = []
    try:
        for s in segs:
            out.append({"start": round(s.start, 2), "end": round(s.end, 2),
                        "text": s.text.strip(),
                        "no_speech_prob": round(s.no_speech_prob, 3),
                        "avg_logprob": round(s.avg_logprob, 2)})
    except Exception as e:
        out.append({"error": str(e)[:200]})
    return out


def classify(metrics, asr_segs, intent):
    """规则化初分类（声学+ASR 证据）；音乐/环境细分留人耳。
    语音判定阈值收紧：whisper 对雨声/噪声会幻听（s02_c01 实测 gibberish +
    avg_logprob=-3.5），必须同时满足高置信（avg_logprob>=-1.0 且
    no_speech_prob<0.4）才判语音，否则归 uncertain_non_speech。"""
    lufs = metrics.get("loudness", {}).get("integrated_lufs")
    strong = [s for s in asr_segs
              if s.get("text") and len(s.get("text", "")) >= 2
              and s.get("no_speech_prob", 1) < 0.4
              and s.get("avg_logprob", -99) >= -1.0]
    weak = [s for s in asr_segs
            if s.get("text") and s not in strong]
    has_speech = bool(strong)
    if lufs is not None and lufs <= -45 and not has_speech:
        return "near_silent", {"reason": f"LUFS={lufs} 且无高置信语音段"}
    if has_speech:
        text_all = "。".join(s["text"] for s in strong)
        cat = "speech_dialogue_candidate" if intent == "dialogue" else "speech_unexpected"
        return cat, {"reason": "ASR 高置信语音段", "transcript": text_all}
    if weak:
        return "non_speech_sound", {"reason":
                                    f"ASR 低置信片段（疑似噪声幻听，不计语音）："
                                    f"{weak[0]['text'][:40]} logprob={weak[0]['avg_logprob']}"}
    if lufs is not None and lufs > -45:
        return "non_speech_sound", {"reason": f"LUFS={lufs}，有非语音声音"
                                              "（动作/环境/音乐待人耳细分）"}
    return "no_audio_track", {"reason": "无音轨"}


def main():
    from faster_whisper import WhisperModel
    t0 = time.time()
    model = WhisperModel("small", device="cpu", compute_type="int8")
    print("whisper small loaded", round(time.time() - t0, 1), "s", flush=True)
    result = {}
    for sid, intent in INTENTS.items():
        f = PROJ / "output" / "videos" / f"{sid}.mp4"
        if not f.exists():
            # 回退：任意版本文件
            cands = list((PROJ / "output" / "videos").glob(f"{sid}*.mp4"))
            if not cands:
                result[sid] = {"error": "file_missing"}
                continue
            f = cands[0]
        row = {"file": str(f.relative_to(ROOT)), "intent": intent,
               "sha256": __import__("hashlib").sha256(f.read_bytes()).hexdigest()[:16],
               "bytes": f.stat().st_size}
        row["streams"] = ffprobe_streams(f)
        if row["streams"]["audio"]:
            row["loudness"] = loudness(f)
            row["astats"] = astats(f)
            t1 = time.time()
            segs = asr(f, model)
            row["asr"] = {"segments": segs, "wall_s": round(time.time() - t1, 1)}
            cat, detail = classify(row, segs, intent)
        else:
            cat, detail = "no_audio_track", {"reason": "ffprobe 无音频流"}
        row["category"] = cat
        row["detail"] = detail
        result[sid] = row
        print(sid, "->", cat, json.dumps(detail, ensure_ascii=False)[:140], flush=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", OUT, "total", round(time.time() - t0, 1), "s")


if __name__ == "__main__":
    main()
