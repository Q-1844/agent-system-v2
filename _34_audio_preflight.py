# _34_audio_preflight.py — 34.md §3 Audio Preflight（机器信号分析）。
# 目标样本：F004 / F008 / F013（calibration R3 胜出样本真实 MP4）。
# 我（AI）无真实听觉——本脚本只做可复现的信号级证据：音频流存在性/编解码、
# 响度轮廓、谱平坦度（音乐=低平坦度持续谐波 vs 环境=宽带噪声）、谐波峰组织、
# 瞬态密度（脚步/落地候选）。结论分级记录进 PROGRESS，人耳抽听页另交付。
import json, subprocess, sys, io
from pathlib import Path
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
VID = Path("_33_fox_calibration/videos")
OUT = Path("_34_fable_pilot/audio_preflight.json")
FF = "ffmpeg"; FP = "ffprobe"

def probe(p):
    r = subprocess.run([FP, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(p)],
                       capture_output=True, text=True)
    return json.loads(r.stdout or "{}")

def wav_mono(p, out):
    subprocess.run([FF, "-y", "-v", "error", "-i", str(p), "-vn", "-ac", "1", "-ar", "16000",
                    "-f", "wav", str(out)], capture_output=True, text=True, check=True)

def analyze(wav):
    import wave
    w = wave.open(str(wav), "rb")
    sr = w.getframerate(); n = w.getnframes()
    x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64) / 32768.0
    w.close()
    if len(x) < sr // 2:
        return {"error": "too_short"}
    # STFT: 1024 window, 50% hop
    win = np.hanning(1024); hop = 512
    frames = [x[i:i+1024] * win for i in range(0, len(x) - 1024, hop)]
    spec = np.abs(np.array([np.fft.rfft(f) for f in frames]))  # [t, freq]
    freqs = np.fft.rfftfreq(1024, 1 / sr)
    # 响度轮廓（dBFS，0.5s 粒度）
    rms = np.sqrt((spec ** 2).mean(axis=1) / 1024) + 1e-12
    # 谱平坦度（几何均值/算术均值；0=纯音 1=白噪声），只看 80-5000Hz（人声/音乐/环境主体带）
    band = (freqs >= 80) & (freqs <= 5000)
    sb = spec[:, band] + 1e-12
    flatness = np.exp(np.log(sb).mean(axis=1)) / sb.mean(axis=1)
    # 谐波峰组织：每帧最强峰显著度（峰/中位数），音乐持续段通常 >8 且稳定
    peak_ratio = sb.max(axis=1) / (np.median(sb, axis=1) + 1e-12)
    # 瞬态（onset）密度：相邻帧能量上跳 >6dB 的次数/s（脚步/落地候选）
    de = 20 * np.log10(rms[1:] / (rms[:-1] + 1e-12))
    onsets = int((de > 6).sum())
    dur = len(x) / sr
    t = dur / max(len(rms), 1)
    # 音乐可疑度：低平坦度(<0.25) 且 高峰显著(>8) 的帧占比（持续谐波组织）
    musical = float(((flatness < 0.25) & (peak_ratio > 8)).mean())
    silent = float((20 * np.log10(rms + 1e-12) < -55).mean())
    return {
        "duration_s": round(dur, 3), "rms_dbfs_mean": round(float(20*np.log10(rms.mean()+1e-12)), 1),
        "rms_dbfs_p90": round(float(20*np.log10(np.percentile(rms, 90)+1e-12)), 1),
        "flatness_mean": round(float(flatness.mean()), 3),
        "flatness_p25": round(float(np.percentile(flatness, 25)), 3),
        "peak_ratio_p90": round(float(np.percentile(peak_ratio, 90)), 1),
        "musical_frame_ratio": round(musical, 3),
        "onsets_per_s": round(onsets / dur, 2),
        "silent_frame_ratio": round(silent, 3),
        "loudness_tail_to_head": round(float(20*np.log10((rms[len(rms)//2:].mean()+1e-12)/(rms[:len(rms)//2].mean()+1e-12))), 1),
    }

result = {}
for eid in ("F004", "F008", "F013"):
    mp4 = next(VID.glob(f"{eid}_*.mp4"), None)
    if mp4 is None:
        result[eid] = {"error": "mp4_not_found"}; continue
    info = probe(mp4)
    streams = info.get("streams") or []
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    a = audio[0] if audio else None
    entry = {
        "file": mp4.name,
        "audio_stream": None if not a else {
            "codec": a.get("codec_name"), "sample_rate": a.get("sample_rate"),
            "channels": a.get("channels"), "duration": a.get("duration")},
    }
    if a:
        wav = Path(f"_34_fable_pilot/{eid}.wav")
        wav_mono(mp4, wav)
        entry["signal"] = analyze(wav)
    result[eid] = entry
    print(json.dumps({eid: entry}, ensure_ascii=False, indent=1))

OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
print("saved:", OUT)
