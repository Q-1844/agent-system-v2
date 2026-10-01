# -*- coding: utf-8 -*-
"""Phase 44C — C2 旁白 cue 真实合成与落点（真实 MiniMax API）。

1) 声线 A/B：同句 × 3 候选 narrator（cue_id=voice_ab_*，供评审页试听）。
2) 正式 cue nc01–nc05（主选声线）：合成 → ffprobe 实测时长 → 窗口内重排
   （不硬塞：超窗即改文案/移位并记录，绝不截断音节）。
证据：_44c/cue_table.json（逐句回执+落点+依据），音频在 output/audio_imports/narration/。
用法：python _44c2_cues.py [--ab-only|--cues-only]
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
PROJECT = "雨灯归航"
PDIR = BASE / "projects" / PROJECT

# 旁白候选（C0 §2 初稿；实测时长回来后重排落点）
# window = 所在镜的 timeline 区间（来自 manifest_v2 / shot_items）
CUES = [
    {"cue_id": "nc01", "shot_id": "s01_c01", "offset_ms": 1500,
     "text": "暴雨过后的河面，总会留下一些东西。",
     "why": "定调+主题伏笔；大远景本就安静，画面不解释来历，旁白给出叙事入口"},
    {"cue_id": "nc02", "shot_id": "s01_c06", "offset_ms": 500,
     "text": "山坡上的灯塔，很多年没人点亮过了。",
     "why": "补充画面不可得的背景信息（灯塔久未点亮）；静观收尾镜头"},
    {"cue_id": "nc03", "shot_id": "s02_c02", "offset_ms": 400,
     "text": "灯芯亮起的那一刻，像有什么被重新修好了。",
     "why": "情绪句：修好的不只是灯；点灯后的静止段落"},
    # 实测 3030ms：初始 offset 5500 超窗（end 74432 > 73902-150）→ 移到邻近空隙
    # （start=70722，楼梯中段）；同句缓存命中，零重复付费
    {"cue_id": "nc04", "shot_id": "s03_c02", "offset_ms": 4820,
     "text": "抱着灯往上走的人，其实也在往家走。",
     "why": "主题句（归航点题）；盘旋楼梯节奏最缓、环境声最弱"},
    {"cue_id": "nc05", "shot_id": "s03_c04", "offset_ms": 1800,
     "text": "灯亮了，晚归的船，就认得回家的路。",
     "why": "收束（信号—回应闭环）；结尾远景留白给音乐淡出"},
]

VOICE_AB = [
    {"ab_id": "ab_gentleman", "voice_id": "Chinese (Mandarin)_Gentleman",
     "label": "温润男声（主选）"},
    {"ab_id": "ab_chengshu", "voice_id": "female-chengshu", "label": "成熟女性音色"},
    {"ab_id": "ab_wisewomen", "voice_id": "Chinese (Mandarin)_Wise_Women",
     "label": "阅历姐姐"},
]
AB_TEXT = "山坡上的灯塔，很多年没人点亮过了。"
PRIMARY_VOICE = "Chinese (Mandarin)_Gentleman"
SPEED = 0.9

# 镜时间线（B 批 manifest_v2 shot_items；边界即切点）
SHOT_WINDOWS = {
    "s01_c01": (0, 8000), "s01_c02": (8000, 16000), "s01_c03": (16000, 24000),
    "s01_c04": (24000, 29175), "s01_c05": (29175, 34359), "s01_c06": (34359, 39543),
    "s02_c01": (39543, 47543), "s02_c02": (47543, 52718), "s02_c03": (52718, 57902),
    "s03_c01": (57902, 65902), "s03_c02": (65902, 73902), "s03_c03": (73902, 81902),
    "s03_c04": (81902, 89902),
}
SETTLE_MS = 300      # 切点后让画面落定
TAIL_GUARD_MS = 150  # 句尾距下一切点的保护


def _load_env():
    env = BASE / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


async def run(only: str) -> int:
    from src.audio.narration import synthesize_narration_cue
    from src.audio.minimax_adapter import VoiceProviderError

    ev = {}
    evfile = BASE / "_44c" / "cue_table.json"
    if evfile.exists():
        ev = json.loads(evfile.read_text(encoding="utf-8"))

    if only in ("ab", "all"):
        ab_rows = []
        for cand in VOICE_AB:
            try:
                r = await synthesize_narration_cue(
                    PDIR, cue_id=cand["ab_id"], text=AB_TEXT,
                    voice_id=cand["voice_id"], speed=SPEED)
                v = r["version"]
                ab_rows.append({"ab_id": cand["ab_id"], "label": cand["label"],
                                "voice_id": cand["voice_id"],
                                "file": v["audio_import_path"],
                                "duration_s": v["duration_probe"].get("duration_s"),
                                "cache_hit": r["cache_hit"],
                                "trace_id": v["provider_trace_id"]})
                print(f"[AB] {cand['label']} → {v['duration_probe'].get('duration_s')}s")
            except VoiceProviderError as e:
                ab_rows.append({"ab_id": cand["ab_id"], "label": cand["label"],
                                "voice_id": cand["voice_id"], "error": e.code,
                                "message": str(e)[:200]})
                print(f"[AB] {cand['label']} FAILED: {e.code}")
        ev["voice_ab"] = {"text": AB_TEXT, "samples": ab_rows}

    if only in ("cues", "all"):
        rows = []
        for c in CUES:
            try:
                r = await synthesize_narration_cue(
                    PDIR, cue_id=c["cue_id"], text=c["text"],
                    voice_id=PRIMARY_VOICE, speed=SPEED)
            except VoiceProviderError as e:
                rows.append({**c, "error": e.code, "message": str(e)[:200]})
                print(f"[cue] {c['cue_id']} FAILED: {e.code}")
                continue
            v = r["version"]
            dur_ms = int(round(float(v["duration_probe"]["duration_s"]) * 1000))
            w0, w1 = SHOT_WINDOWS[c["shot_id"]]
            start_ms = w0 + c["offset_ms"]
            end_ms = start_ms + dur_ms
            fits = (start_ms >= w0 + SETTLE_MS
                    and end_ms <= w1 - TAIL_GUARD_MS)
            rows.append({**c, "version_id": v["version_id"],
                         "voice_id": v["voice_id"], "speed": v["speed"],
                         "file": v["audio_import_path"],
                         "sha256": v["audio_sha256"],
                         "duration_ms": dur_ms,
                         "window_ms": [w0, w1],
                         "start_ms": start_ms, "end_ms": end_ms,
                         "fits_window": fits,
                         "cache_hit": r["cache_hit"],
                         "trace_id": v["provider_trace_id"]})
            print(f"[cue] {c['cue_id']} {dur_ms}ms window={w0}-{w1} "
                  f"start={start_ms} end={end_ms} fits={fits}")
        ev["narration_cues"] = rows
        # 重排检查汇总：任何超窗 → 明确列出（C2：不硬塞，改文案/移位/交用户）
        overflow = [r for r in rows if r.get("fits_window") is False]
        ev["placement_check"] = {
            "all_fit": not overflow,
            "overflow_cues": [r["cue_id"] for r in overflow],
            "rule": f"start>=镜起点+{SETTLE_MS}ms；end<=镜终点-{TAIL_GUARD_MS}ms"
                    "（不跨切点、不截断音节）"}

    evfile.parent.mkdir(parents=True, exist_ok=True)
    evfile.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"evidence → {evfile}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ab-only", dest="only", action="store_const", const="ab")
    ap.add_argument("--cues-only", dest="only", action="store_const", const="cues")
    ap.add_argument("--all", dest="only", action="store_const", const="all")
    ap.set_defaults(only="all")
    a = ap.parse_args()
    _load_env()
    sys.exit(asyncio.run(run(a.only)))


if __name__ == "__main__":
    main()
