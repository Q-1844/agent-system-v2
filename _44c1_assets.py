# -*- coding: utf-8 -*-
"""Phase 44C — 本地合成器乐铺底（C0/C3 素材，确定性可复现）。

A 小调四和弦垫（Am–F–C–G，8s/和弦 ×3 循环 = 96s ≥ 全片 89.9s）：
正弦三和弦 + 微失谐双拷贝（缓慢拍频）+ 低通 + 回声空间感；和弦段内 0.8s 淡入出
（避免段间突变），整轨首尾 3s/4s 淡入淡出在 AudioPlan 层（fade_in_ms/fade_out_ms）。

素材身份：origin=locally_synthesized_test_asset；
license=self_generated_no_third_party_rights；参数快照 _44c/music_params.json。
用法：python _44c1_assets.py [--music-only]
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

PROJECT = "雨灯归航"
SR = 48000
SEG_S = 8              # 每和弦段时长（秒）
CYCLES = 3             # 和弦进行循环次数（4 和弦 × 8s = 32s/循环）
N_SEGMENTS = 4 * CYCLES   # 12 段 = 96s
# （A 小调，温暖中音区）Am → F → C → G：三和弦频率（Hz）
CHORDS = [
    ("Am", [220.00, 261.63, 329.63]),
    ("F", [174.61, 220.00, 261.63]),
    ("C", [196.00, 261.63, 329.63]),
    ("G", [196.00, 246.94, 293.66]),
]
# 每音量级：根音 0.22，三音 0.16，五音 0.12；失谐拷贝 +40%（幅度）+0.6Hz 拍频
LEVELS = [0.22, 0.16, 0.12]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def synth_segment(out: Path, freqs, idx: int) -> None:
    """单和弦段：正弦叠加 + 微失谐；0.8s 段内淡入出。确定性表达式。"""
    terms = []
    for f, lvl in zip(freqs, LEVELS):
        terms.append(f"sin(2*PI*{f:.2f}*t)*{lvl:.3f}")
        terms.append(f"sin(2*PI*{f + 0.6:.2f}*t)*{lvl * 0.4:.3f}")
    expr = "+".join(terms)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i",
           f"aevalsrc='{expr}':s={SR}:d={SEG_S}",
           "-af", "lowpass=f=1800,tremolo=f=0.22:d=0.18,"
                  f"afade=t=in:d=0.8,afade=t=out:st={SEG_S - 0.8}:d=0.8",
           "-ac", "1", "-ar", str(SR), "-map_metadata", "-1", str(out)]
    subprocess.run(cmd, check=True, timeout=120)


def build_music_bed(out_wav: Path, workdir: Path) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    segs = []
    for i in range(N_SEGMENTS):
        name, freqs = CHORDS[i % len(CHORDS)]
        seg = workdir / f"seg_{i:02d}_{name}.wav"
        synth_segment(seg, freqs, i)
        segs.append(seg)
    lst = workdir / "concat.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for s in segs:
            f.write(f"file '{s.resolve().as_posix()}'\n")
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    # concat（同构 wav）→ 空间感回声 → 立体声 → 去元数据
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-af", "aecho=0.8:0.85:60|120:0.25|0.15",
                    "-ac", "2", "-ar", str(SR), "-map_metadata", "-1",
                    "-c:a", "pcm_s16le", str(out_wav)], check=True, timeout=300)
    lst.unlink()
    return {"segments": [s.name for s in segs], "segment_seconds": SEG_S,
            "chords": [c[0] for c in CHORDS], "cycles": CYCLES,
            "sample_rate": SR, "levels": LEVELS, "detune_hz": 0.6,
            "post_fx": "lowpass=1800,tremolo=f=0.22:d=0.18,aecho=0.8:0.85:60|120:0.25|0.15",
            "origin": "locally_synthesized_test_asset",
            "license": "self_generated_no_third_party_rights"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--music-only", action="store_true")
    a = ap.parse_args()
    pdir = BASE / "projects" / PROJECT
    out_wav = pdir / "output" / "audio_imports" / "music" / "bed_44c_v1.wav"
    params = build_music_bed(out_wav, BASE / "_44c" / "_music_work")
    params["file"] = str(out_wav)
    params["sha256"] = sha256_file(out_wav)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
         str(out_wav)], capture_output=True, text=True).stdout
    params["duration"] = json.loads(probe)["format"]["duration"]
    (BASE / "_44c" / "music_params.json").write_text(
        json.dumps(params, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(params, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
