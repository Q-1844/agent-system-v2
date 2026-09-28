# -*- coding: utf-8 -*-
"""Phase 41 修复阶段针对测试：calibration run 脚手架。

背景（系统缺口实证）：agent 无 calibration run 创建工具面入口，全量重测
13/13 完成后 trace_upsert 全部 run not_found 落档失败（result_2.json）。
按 HANDOFF 纪律 #5「建 run 属脚手架，需说明」：执行侧用官方 create_run
建 run，run_id 交任务书供 agent trace_upsert 落档。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.experiments.calibration_trace import create_run  # noqa: E402

pdir = BASE_DIR / "projects" / "雨灯归航"
run = create_run(
    pdir,
    story_id="yudeng_guihang",
    title="Phase41 修复阶段针对测试：s03_c03 大偏离修复（用户触发，桥接修复阶段测试）",
    notes=(
        "脚手架说明：本 run 由执行侧（zcode）创建——agent 工具面无 run 创建入口"
        "（Phase 41 全量重测实证缺口）。story 生成已于 09-28 全量重测前冻结；"
        "本 run 只承载 s03_c03 修复阶段测试的 trace 落档。"),
)
print(run["run_id"])
