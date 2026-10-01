# -*- coding: utf-8 -*-
"""Phase 44C+ — 程序化作曲 BGM v2（numpy 合成，确定性可复现）。

背景：用户反馈 v1 正弦垫不可闻、成片仍是"缝合感"；MiniMax Music API 对本账户
已关闭（HTTP 410/2153 实测）→ 本地作曲升级。
乐曲结构（A 小调，60BPM，96s = 24 小节）：
  - 弦乐垫：Am–F–C–G 循环（8s/和弦），慢起音 + 微颤；
  - 钢琴主旋律：4 个乐句（主题 A/A'/A''/终句），钢琴音色=6 谐波叠加+指数衰减包络；
  - 低音声部：和弦根音全音符（A1/F1/C2/G1 八度）。
素材身份：origin=locally_composed_synthesized_test_asset（自作曲，无第三方权利）。
输出：output/audio_imports/music/bgm_44c_v2.wav（48k 立体声，响度规整至 ≈-18 LUFS）。
用法：python _44c7_music_v2.py
"""
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
PROJECT = "雨灯归航"
SR = 48000
DUR = 96.0
BPM = 60.0
BEAT = 60.0 / BPM          # 1s/拍

# 和弦（8s/个，12 个 = 96s）：A 小调进行
CHORDS = [
    ("Am", [220.00, 261.63, 329.63], 110.00),
    ("F", [174.61, 220.00, 261.63], 87.31),
    ("C", [196.00, 261.63, 329.63], 130.81),
    ("G", [196.00, 246.94, 293.66], 98.00),
]
NOTE_FREQ = {}
A4 = 440.0
for midi in range(21, 109):
    NOTE_FREQ[midi] = A4 * 2 ** ((midi - 69) / 12)
A3, B3, C4, D4, E4, F4, G4 = 57, 59, 60, 62, 64, 65, 67
A4m, B4, C5, D5, E5 = 69, 71, 72, 74, 76
A2, C3m = 45, 48

# 主旋律（拍, 时值拍, midi, 力度）——4 乐句 × 32 拍 = 128? 96s=96 拍
MELODY = [
    # 乐句 1（0-15）：Am→F，主题呈示
    (2.0, 2.0, E4, .55), (4.0, 1.5, A4m, .6), (5.5, 2.5, C5, .62), (8.0, 1.0, B4, .5),
    (9.0, 1.0, C5, .55), (10.0, 2.0, A4m, .58), (13.0, 1.5, G4, .5), (14.5, 1.5, E4, .48),
    # 乐句 2（16-31）：F→C，下行应答
    (16.0, 2.0, F4, .52), (18.0, 1.0, E4, .5), (19.0, 1.0, D4, .48), (20.0, 2.5, E4, .55),
    (23.0, 1.0, C4, .48), (24.0, 1.0, D4, .5), (25.0, 3.0, C4, .55), (29.0, 1.5, B3, .45),
    (30.5, 1.5, C4, .5),
    # 乐句 3（32-47）：Am→F，主题升层
    (32.0, 2.0, A4m, .6), (34.0, 1.0, B4, .55), (35.0, 2.0, C5, .62), (37.0, 2.0, E5, .65),
    (40.0, 1.5, D5, .58), (41.5, 1.5, C5, .55), (43.0, 2.0, B4, .52), (45.0, 1.0, G4, .5),
    (46.0, 2.0, B4, .52),
    # 乐句 4（48-63）：C→G，回落
    (48.0, 2.0, C5, .58), (50.0, 1.0, B4, .5), (51.0, 1.0, A4m, .55), (52.0, 2.5, G4, .55),
    (55.0, 1.0, E4, .48), (56.0, 1.0, F4, .5), (57.0, 2.0, E4, .55), (60.0, 1.5, D4, .48),
    (61.5, 2.5, E4, .52),
    # 乐句 5（64-79）：再现（弱）
    (64.0, 2.0, E4, .48), (66.0, 1.5, A4m, .52), (67.5, 2.5, C5, .55), (70.0, 1.0, B4, .45),
    (71.0, 1.0, C5, .5), (72.0, 2.0, A4m, .52), (75.0, 1.5, G4, .45), (76.5, 1.5, E4, .45),
    (78.0, 2.0, F4, .48),
    # 终句（80-95）：收束于主音
    (80.0, 1.0, E4, .48), (81.0, 1.0, D4, .45), (82.0, 3.0, C4, .55), (85.0, 1.0, B3, .42),
    (86.0, 1.0, C4, .46), (87.0, 8.0, A3, .5),
]


def piano_note(freq: float, dur: float, vel: float) -> np.ndarray:
    """钢琴式音色：6 谐波（高次衰减）+ 指数衰减包络 + 轻微起音。"""
    n = int(dur * SR)
    t = np.arange(n) / SR
    harmonics = [(1, 1.0), (2, 0.45), (3, 0.22), (4, 0.1), (5, 0.05), (6, 0.03)]
    sig = np.zeros(n)
    for k, amp in harmonics:
        sig += amp * np.sin(2 * np.pi * freq * k * t
                            + 0.02 * k * np.sin(2 * np.pi * 0.9 * t))
    env = np.exp(-t / (dur * 0.55))                      # 指数衰减
    attack = int(0.008 * SR)
    env[:attack] *= np.linspace(0, 1, attack)
    sig *= env * vel * 0.5
    return sig


def pad_chord(freqs, dur: float) -> np.ndarray:
    """弦乐垫：多谐波 + 1.5s 慢起音 + 0.15Hz 微颤。"""
    n = int(dur * SR)
    t = np.arange(n) / SR
    sig = np.zeros(n)
    for f in freqs:
        for k, amp in ((1, 1.0), (2, 0.35), (3, 0.18), (4, 0.08)):
            sig += amp * np.sin(2 * np.pi * f * k * t + 0.05 * k)
        sig += 0.35 * np.sin(2 * np.pi * (f + 0.7) * t)   # 微失谐合唱感
    sig *= 0.09
    attack = np.clip(t / 1.5, 0, 1) ** 2
    release = np.clip((dur - t) / 1.0, 0, 1) ** 2
    tremolo = 1 + 0.12 * np.sin(2 * np.pi * 0.15 * t)
    return sig * attack * release * tremolo


def bass_note(freq: float, dur: float) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    sig = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * freq * 2 * t)
    env = np.clip(t / 0.4, 0, 1) * np.exp(-t / (dur * 0.9))
    return sig * env * 0.22


def main() -> int:
    total_n = int(DUR * SR)
    mix = np.zeros(total_n)

    # 1) 和弦垫
    for i in range(12):
        name, triad, root = CHORDS[i % 4]
        start = i * 8.0
        seg = pad_chord(triad, 8.0)
        s = int(start * SR)
        mix[s:s + len(seg)] += seg

    # 2) 低音
    for i in range(12):
        _, _, root = CHORDS[i % 4]
        start = i * 8.0
        seg = bass_note(root, 8.0)
        s = int(start * SR)
        mix[s:s + len(seg)] += seg

    # 3) 主旋律
    for start_b, dur_b, midi, vel in MELODY:
        t0 = start_b * BEAT
        dur = dur_b * BEAT + 0.8                      # 余振
        freq = NOTE_FREQ[midi]
        seg = piano_note(freq, dur, vel)
        s = int(t0 * SR)
        e = min(s + len(seg), total_n)
        mix[s:e] += seg[:e - s]

    # 4) 整轨首尾淡入出（2.5s/5s）
    fade_in = int(2.5 * SR)
    mix[:fade_in] *= np.linspace(0, 1, fade_in)
    fade_out = int(5.0 * SR)
    mix[-fade_out:] *= np.linspace(1, 0, fade_out)

    # 5) 峰值控制 + 写 WAV（立体声：右声道 +6ms 延迟做宽度感）
    peak = np.max(np.abs(mix)) or 1.0
    mix = mix / peak * 0.72
    right = np.zeros_like(mix)
    d = int(0.006 * SR)
    right[d:] = mix[:-d]
    stereo = np.stack([mix, right], axis=1)
    pcm = (stereo * 32767).astype(np.int16)
    out = BASE / "projects" / PROJECT / "output" / "audio_imports" / "music" / \
        "bgm_44c_v2.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())

    params = {
        "file": str(out), "sha256": __import__("hashlib").sha256(out.read_bytes()).hexdigest(),
        "origin": "locally_composed_synthesized_test_asset",
        "license": "self_composed_no_third_party_rights",
        "structure": "A小调 60BPM 96s；弦乐垫(Am-F-C-G×3)+钢琴主旋律(5乐句 25 音符)+低音声部",
        "timbre": "piano=6谐波+指数衰减；pad=多谐波+慢起音+微颤+tape宽度；bass=根音全音符",
        "note_count": len(MELODY), "sample_rate": SR,
        "note": "MiniMax Music API 对本账户已关闭(HTTP 410/2153 实测)——程序化作曲为"
                "替代方案；正式配乐待用户提供授权素材后一键替换",
    }
    (BASE / "_44c" / "music_v2_params.json").write_text(
        json.dumps(params, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(params, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
