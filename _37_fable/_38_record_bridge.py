# _37_fable/_38_record_bridge.py — M3B 桥 trace 落档（本地 + REST，含 _repair_plan 决策链）。
import io
import json
import sys
from pathlib import Path

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
PROJ = "Fable_Pilot_Fox_and_Grapes_V1"
TRACE = HERE / "_38_trace_run.json"

rec = json.loads(TRACE.read_text(encoding="utf-8"))
rec["M3B"] = {
    "shot_id": "s11_c04", "segment": "C", "provider_id": "agnes",
    "provider_model": "agnes-video-v2.0",
    "generation_mode": "keyframes_bridge", "duration_s": 10.0,
    "prompt_hash": "(keyframes: transition_description 编译, video_prompt_en 空)",
    "prompt_snapshot": (
        "The fox breaks into a full sprint from the shadowed treeline and bursts out of "
        "the forest into blinding sunlight; in a surge of golden light the last trees "
        "sweep past and the world opens into the bright village outskirts, the fox "
        "landing on the sunlit dirt road with a puff of dust, fences and pumpkins "
        "flashing by on both sides"),
    "reference_asset_ids": [],
    "opening_reference_type": "keyframes_first_prev_tail_last_seeded",
    "expected_tail": "狐狸落到村口土路上正面奔来，木栅栏与南瓜在两侧，森林退到背景（=M4 f60 着陆帧）",
    "end_frame_url": "https://Q-1844.github.io/agnes-frame-host/objects/80cc069c7d3c3b75.jpg",
    "end_frame_local": "projects/Fable_Pilot_Fox_and_Grapes_V1/output/frames/s11_c04_video_end.jpg",
    "mae_prev": None, "mae_bridge_tail_vs_target": 26.0,
    "mae_bridge_tail_vs_m3_tail": 59.5,
    "vision_assessment": None,
    "vision_meta": {"note": "keyframes 桥以 MAE+人工帧检代行评估"},
    "human_deviation_level": "none", "human_action": "continue",
    "human_reason": (
        "跨Location keyframes 桥成立：内部帧渐进穿越（森林冲刺→金色光爆腾跃→混合世界→"
        "村口土路），尾帧落点与 M4f60 目标同地理（栅栏/南瓜/森林背景），"
        "MAE 26.0（37A 对标 2.9 同场景 / 43.6 劫持；跨场景难度更高属合理区间）"),
    "shot_patch": {}, "prompt_rebuilt": False,
    "next_shot_id": "s12_c01",
    "result_after_correction": "M4 以 prev_tail(=桥尾帧) 重生成",
    "retry_or_adapt": "adapt",
    "choice_reason": "keyframes 桥（38.md §2.3）",
    "_repair_plan": {
        "root_cause": (
            "用户裁定：M4 跨场景虽流畅但是「森林→乡村」镜头内硬切"
            "（模型把跃出渲染成 1-2s 内的突转），不够优秀"),
        "options": [
            "A 重试 M4（同类硬切大概率复发，provider 强先验）",
            "B 接受现状（用户已否决：要测桥）",
            "C keyframes 桥 M3→M4f60 + M4 重生成（把穿越独立成镜，M4 从桥尾帧接力）"],
        "chosen": "C",
        "why": (
            "桥方法第一轮同场景实证（MAE 2.9）；本测把桥推进到跨 Location（新地面）；"
            "M4f60（落地村庄帧）作为尾槽钉死着陆状态，硬切被桥内部渐变吸收；"
            "M4 重生成后播放序 M3→桥→M4′ 为最小改动"),
        "basis_timing_discipline": (
            "refresh→compile+seed→提交，seed 后零 refresh（boundary_frame_stale 规避）")},
}
TRACE.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
run = (HERE / "_38_run_id.txt").read_text(encoding="utf-8").strip()
r = httpx.put(
    f"http://127.0.0.1:8000/api/project/{PROJ}/calibration/runs/{run}/links/M3B",
    json={"record": rec["M3B"]}, timeout=30)
print("M3B trace saved; REST link:", r.status_code)
