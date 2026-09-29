# -*- coding: utf-8 -*-
"""EXP4 manifest 生成（定版评审页数据替换）。跑完 _probe_p42_exp4.py 后执行。"""
import json, os

OUT = "_video25_probe/exp4"
M = {
    "round": "exp4",
    "round_title": "EXP4 · 定版配方批量试产（2026-09-29）",
    "round_date": "2026-09-29",
    "intro": ("<b>定版配方</b>：crop 根帧（头肩裁剪 Look）→ i2v 硬首帧 → 新尾帧 → "
              "【新尾帧+Look 双参考】接力下一镜；s01_c04 已补铜灯道具绑定。本轮把配方铺满 "
              "s01 偏差链（c04→c05→c06，两种子）+ s02 链补量（定版候选根 s1/s5 各一条链）。"
              "判据沿用：水彩 / 白斑左颊 / 兜帽向下 / 小巧可爱 / 灯样式一致 / 动作密度"
              "（跑步镜头少加多余动作）。"),
    "image_groups": [],
    "video_groups": [
        {
            "id": "chainA",
            "title": "A · 链 A：s01 偏差链全量试产（c04→c05→c06 每步双参考接力）",
            "note": "每条链 3 条视频：c04（crop_prop_s7 根 i2v）→ c05（接力）→ c06（接力）。两种子并排。",
            "videos": [
                {"name": f"链 A 种子 {seed}",
                 "video_srcs": [
                     {"label": "s01_c04（prop 根 i2v）", "src": f"exp4/chainA_s{seed}_s01_c04.mp4"},
                     {"label": "s01_c05（双参考接力）", "src": f"exp4/chainA_s{seed}_s01_c05.mp4"},
                     {"label": "s01_c06（双参考接力）", "src": f"exp4/chainA_s{seed}_s01_c06.mp4"},
                 ],
                 "thumbs": [f"exp4/chainA_s{seed}_s01_c05_fb.jpg", f"exp4/chainA_s{seed}_s01_c05_fe.jpg",
                            f"exp4/chainA_s{seed}_s01_c06_fb.jpg", f"exp4/chainA_s{seed}_s01_c06_fe.jpg"]}
                for seed in (42, 7)
            ],
        },
        {
            "id": "chainB",
            "title": "B · 链 B：s02 链补量（定版候选根 s1 / s5 → s02_c03）",
            "note": "与链 4（s42 根，上一轮）并排——三选一终选 s02_c02 根帧。",
            "videos": [
                {"name": f"链 B · 根帧=s02_c02_crop_{s}", "desc": "对照链 4（s42 根）",
                 "video_srcs": [
                     {"label": "s02_c02（根 i2v）", "src": f"exp4/chainB_{s}_s02_c02.mp4"},
                     {"label": "s02_c03（双参考接力）", "src": f"exp4/chainB_{s}_s02_c03.mp4"},
                 ],
                 "thumbs": [f"exp4/chainB_{s}_s02_c03_fb.jpg", f"exp4/chainB_{s}_s02_c03_fe.jpg"]}
                for s in ("s1", "s5")
            ],
        },
    ],
    "questions": [
        {"id": "q1", "title": "问题 1：s01 链试产（A 区两条链）的端到端评价？",
         "why": "定版配方铺满 s01 偏差链——重点：c04→c05→c06 三镜之间人物/画风/灯是否稳定，动作是否自然（动作密度判据）。",
         "options": ["种子 42 好", "种子 7 好", "两条都可用", "还不够（写原因）"],
         "placeholder": "逐镜评语：c04=…；c05=…；c06=…"},
        {"id": "q2", "title": "问题 2：s02_c02 根帧终选（s1 / s5 / s42 三选一或组合）？",
         "why": "链 4（s42）、链 B（s1/s5）并排。终选后该镜正式修复走此根帧。",
         "options": ["s1", "s5", "s42（链 4）", "另选/组合（写明）"],
         "placeholder": "补充（可空）"},
        {"id": "q3", "title": "问题 3：s02_c03 接力样本的画风/身份回正是否达标？",
         "why": "所有新链的 s02_c03 都是双参考接力——评估偏差风格是否回正到位，能否正式替换现役偏差版。",
         "options": ["达标，可正式替换", "接近但还差一点（写差异）", "不达标"],
         "placeholder": "补充（可空）"},
        {"id": "q4", "title": "问题 4：其他意见 / 下一轮想测什么",
         "why": "自由填写。",
         "options": [],
         "placeholder": "自由填写（可空）"},
    ],
}

json.dump(M, open("_video25_probe/manifest.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("manifest.json -> EXP4, videos:", len(M["video_groups"]))
