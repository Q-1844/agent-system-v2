# _37_fable/_38_record_bridge_m5b.py — M5B 桥 trace 落档（本地 + REST，含 _repair_plan）。
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
rec["M5B"] = {
    "shot_id": "s12_c07", "segment": "D", "provider_id": "agnes",
    "provider_model": "agnes-video-v2.0",
    "generation_mode": "keyframes_bridge", "duration_s": 5.0,
    "prompt_hash": "(keyframes: transition_description 编译, video_prompt_en 空)",
    "prompt_snapshot": (
        "The fox lowers its front body and leans in toward the big orange pumpkin, "
        "pressing its nose close to the pumpkin's ridged skin to sniff it carefully, "
        "tail stretched low and body crouched; the camera holds close as the fox's nose "
        "finally hovers just above the pumpkin surface"),
    "reference_asset_ids": [],
    "opening_reference_type": "keyframes_first_prev_tail_last_seeded",
    "expected_tail": "狐狸压低前身、鼻尖凑近大南瓜（=M6 首帧着陆目标）",
    "end_frame_url": "https://Q-1844.github.io/agnes-frame-host/objects/0c8e0500c15fbfdf.jpg",
    "end_frame_local": "projects/Fable_Pilot_Fox_and_Grapes_V1/output/frames/s12_c07_video_end.jpg",
    "mae_prev": None,
    "seam_m5_tail_to_bridge_first": 1.9,
    "seam_bridge_tail_to_m6_first": 2.4,
    "vision_assessment": None,
    "vision_meta": {"note": "keyframes 桥以 seam MAE+人工帧检代行评估"},
    "human_deviation_level": "none", "human_action": "continue",
    "human_reason": (
        "M5→M6 接缝桥成立：双端钉死（首槽=M5尾帧 尾槽=M6首帧），双侧接缝 1.9/2.4 近零闭合；"
        "内部过渡帧检正确（直立→压低→凑近→鼻贴南瓜皮）。时长 5s=静态配方（本桥动作小，"
        "偏离 §2.3 的 10s 模板并已记录理由：尾帧钉死保证收束不截断）"),
    "shot_patch": {}, "prompt_rebuilt": False,
    "next_shot_id": "s12_c03",
    "result_after_correction": "M6 保持原片不重生成（其首帧已被桥尾帧对齐）",
    "retry_or_adapt": "adapt",
    "choice_reason": "keyframes 桥（38.md §2.3）——接缝修复标准手段",
    "_repair_plan": {
        "root_cause": (
            "M5 重生成（a3 定版直立站姿收尾）使 M5→M6 接缝 46.5（M6 首帧仍是旧 M5a2 "
            "鼻贴南瓜状态）——重生成级联的下游断缝"),
        "options": [
            "A 接受 46.5（切近级，但用户要求接缝一致）",
            "B 重试 M5 钉嗅闻尾态（a4 已发生 children 事故，重试方差危险且不保证端点）",
            "C keyframes 桥 M5→M6首帧（双端钉死：首帧尾帧确定、中间过程插值）"],
        "chosen": "C",
        "why": (
            "用户方法论修正：桥是系统唯一「双端边界确定」的生成模式（普通 relay 只钉开场、"
            "尾帧自由落点；i2v 只钉首帧）——接缝修复的正解，不是与 retry/accept 并列的选项。"
            "桥不重生成 M5/M6（零级联风险、M7 叶落鼻尖等既有成果零风险），双侧接缝由构造保证闭合"),
        "basis_timing_discipline": "refresh→compile+seed→提交，seed 后零 refresh"},
}
TRACE.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
run = (HERE / "_38_run_id.txt").read_text(encoding="utf-8").strip()
r = httpx.put(
    f"http://127.0.0.1:8000/api/project/{PROJ}/calibration/runs/{run}/links/M5B",
    json={"record": rec["M5B"]}, timeout=30)
print("M5B trace saved; REST link:", r.status_code)
