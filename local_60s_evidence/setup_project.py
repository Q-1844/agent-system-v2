#!/usr/bin/env python3
"""C2 本地 60 秒验证 · 测试项目 + Producer 制作计划构造。

设计（3 故事段 × 4 镜头 × 5s = 60s）：
  s01 深海断崖下潜   c01‖c02 独立并行 / c03 真尾帧接续(c02) / c04 独立
  s02 银色记录匣     c01 独立 / c02 真尾帧接续(c01) / c03‖c04 独立
  s03 返回救援船     c01‖c02 独立并行 / c03 尾帧接续(c02) / c04 独立

并行样本三类齐备：
  1. 独立并行   —— 同段 c01‖c02 均 parallel_preference=allow、无媒体依赖
  2. 真接续等待 —— inherit_previous（c01→c03 / c01→c02）与 reference_previous，
                   requires_previous_video_end=True，B 必须等 A 的真实视频末帧就绪
  3. 跨段并行   —— s02/s03 的独立镜头与 s01 无媒体依赖，可在 s01 未完成时启动

只写本地 projects/ 目录，不触发任何远程写入。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.models import Project, Scene, Shot, StyleAnchor  # noqa: E402
from src.core.store import ProjectStore  # noqa: E402
from src.ai.production_planner import sanitize_plan, write_plans  # noqa: E402
from src.utils.project_manager import ProjectManager  # noqa: E402

PROJECT = "c2_parallel_local60_20261027"

STYLE = dict(
    art_style="cinematic, photorealistic underwater documentary, deep ocean blues, "
              "volumetric light beams, high detail",
    palette="deep blue, amber-orange lights, silver highlights",
    aspect_ratio="16:9",
    size_tier="1K",
)

STORY = ("一部 60 秒的水下短片：一台橙色深海潜水器沿黑色礁岩断崖下潜，"
         "在裂谷底部发现一个银色记录匣，用机械臂夹起后上浮返回水面上的救援船。"
         "全片无对白、无人脸。")

# ---------------------------------------------------------------- 故事段定义
SCENES = [
    {
        "scene_id": "s01",
        "title": "深海断崖下潜",
        "summary": "深蓝色海水中，一台小型橙色潜水器沿黑色礁岩断崖缓缓下潜，"
                   "双橙色探照灯切开水体；岩壁缝隙深处透出一点银色反光。",
        "shots": [
            ("s01_c01", "潜水器从画面上方缓缓下潜，两道橙色探照灯光柱在深蓝水中切开，"
                        "前方是黑色礁岩断崖的轮廓。",
             "independent"),
            ("s01_c02", "摄影机横移掠过黑色岩壁表面，岩壁一道缝隙深处有一小块银色金属的"
                        "反光一闪而过。",
             "independent"),
            ("s01_c03", "镜头紧接刚才那道银色反光继续向前，潜水器的探照灯逐渐照亮了半埋在"
                        "岩石缝隙里的那件银色金属物件。",
             "inherit_previous"),
            ("s01_c04", "潜水器向右转向，探照灯扫过空旷的裂谷底部，细小泥沙在光柱中缓缓漂浮。",
             "independent"),
        ],
    },
    {
        "scene_id": "s02",
        "title": "银色记录匣",
        "summary": "裂谷底部的泥沙里半掩着一个银色记录匣，潜水器的黄色机械臂夹爪将其夹起。",
        "shots": [
            ("s02_c01", "海底泥沙地面上半掩着一个银色金属记录匣，表面有磨损和细小刻痕，"
                        "探照灯从上方斜照下来。",
             "independent"),
            ("s02_c02", "镜头紧接上一镜继续，黄色机械臂的夹爪从画面上方缓缓下降，"
                        "接近泥沙中那个银色记录匣的锁扣。",
             "inherit_previous"),
            ("s02_c03", "黄色机械臂夹爪伸向泥沙中的银色金属箱体，探照灯光照亮箱体表面的磨损与刻痕。",
             "independent"),
            ("s02_c04", "银色记录匣被机械臂夹离泥沙，海底扬起一片细沙，箱体在光柱中缓缓上升。",
             "independent"),
        ],
    },
    {
        "scene_id": "s03",
        "title": "返回救援船",
        "summary": "潜水器携着记录匣加速上浮穿过海面，靠近水面上的救援船。",
        "shots": [
            ("s03_c01", "潜水器加速向上爬升，大量气泡向上涌动，画面由深蓝逐渐变亮。",
             "independent"),
            ("s03_c02", "海面在阳光下发亮，远处救援船的探照灯穿透水面照下来，船只轮廓在浪中起伏。",
             "independent"),
            ("s03_c03", "镜头从海面救援船的灯光继续，救援船的白色船体在波浪中缓慢摇晃，灯光扫过甲板。",
             "reference_previous"),
            ("s03_c04", "潜水器靠近救援船船体，救援船灯光照亮橙色潜水器的外壳，浪花拍打船身。",
             "independent"),
        ],
    },
]

TARGET_DURATION_S = 5.0


def build_project() -> Project:
    scenes = []
    for s in SCENES:
        shots = []
        for i, (shot_id, desc, _opening) in enumerate(s["shots"], start=1):
            shots.append(Shot(
                shot_id=shot_id,
                scene_id=s["scene_id"],
                index=i,
                description=desc,
                target_duration_s=TARGET_DURATION_S,
            ))
        scenes.append(Scene(scene_id=s["scene_id"], title=s["title"],
                            summary=s["summary"], shots=shots))
    return Project(name=PROJECT, style=StyleAnchor(**STYLE),
                   story_text=STORY, scenes=scenes)


def build_plan_input(scene_def: dict) -> dict:
    """Producer 决策输入：opening_source / link_mode / parallel_preference。

    link_mode 全 i2v：本轮只验证调度并行与尾帧接力，不引入首尾帧双钉的额外失败面。
    """
    return {
        "overview": f"{scene_def['title']}：4 镜，2 路并行调度 + 1 条真实尾帧接力",
        "max_parallel_shots": 2,
        "shots": [
            {
                "shot_id": shot_id,
                "opening_source": opening,
                "link_mode": "i2v",
                "parallel_preference": "allow",
                "reason": ("故事段首镜，场景建立" if opening == "independent" and i == 0
                           else "独立镜头，无媒体依赖，可与其他镜头并行"
                           if opening == "independent"
                           else "承接上一镜的真实视频末帧，必须等待上游尾帧就绪"),
            }
            for i, (shot_id, _desc, opening) in enumerate(scene_def["shots"])
        ],
    }


def main() -> int:
    # ProjectManager 默认 base_dir="./projects" 是**相对 cwd** 的；后端以代码根目录为 cwd 运行，
    # 这里显式用绝对路径，保证脚本在任何 cwd 下都写到后端真正使用的项目根。
    pm = ProjectManager(str(ROOT / "projects"))
    pm.base_dir.mkdir(parents=True, exist_ok=True)
    folder = pm.create_project(PROJECT)
    store = ProjectStore(folder)
    store.save(build_project())

    plans = {}
    for s in SCENES:
        scene = next(sc for sc in store.load().scenes if sc.scene_id == s["scene_id"])
        plan = sanitize_plan(scene, build_plan_input(s),
                             run_seed=20261027, project_name=PROJECT)
        plan.status = "approved"
        plan.approved_at = time.time()
        plan.created_at = time.time()
        plans[s["scene_id"]] = plan
    write_plans(folder, plans)

    # ---------- 校验输出 ----------
    from src.ai.c2_parallel import shot_dependencies, scene_dependencies
    project = store.load()
    deps = shot_dependencies(project, plans)
    sdeps = scene_dependencies(project, deps, [s["scene_id"] for s in SCENES])

    print(f"项目目录: {folder}")
    print(f"镜头总数: {sum(len(sc.shots) for sc in project.scenes)}")
    print(f"计划时长: {sum(len(sc.shots) for sc in project.scenes) * TARGET_DURATION_S:.0f}s")
    print("\n--- 冻结 shot DAG ---")
    for shot_id in [sh.shot_id for sc in project.scenes for sh in sc.shots]:
        p = next(p for p in plans[next(sc.scene_id for sc in project.scenes
                                      if any(s.shot_id == shot_id for s in sc.shots))].shots
                 if p.shot_id == shot_id)
        print(f"  {shot_id}: opening={p.opening_source:<18} link={p.link_mode:<9} "
              f"needs_prev_end={str(p.requires_previous_video_end):<5} "
              f"src={str(p.source_shot_id):<9} deps={sorted(deps.get(shot_id, set()))}")
    print("\n--- scene DAG ---")
    for sid, d in sdeps.items():
        print(f"  {sid}: deps={sorted(d)}")

    out = ROOT / "local_60s_evidence" / "project_plan_design.json"
    out.write_text(json.dumps({
        "project": PROJECT,
        "folder": str(folder),
        "target_duration_s": TARGET_DURATION_S,
        "total_shots": sum(len(sc.shots) for sc in project.scenes),
        "shot_dependencies": {k: sorted(v) for k, v in deps.items()},
        "scene_dependencies": {k: sorted(v) for k, v in sdeps.items()},
        "plans": {k: v.model_dump() for k, v in plans.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n设计存档: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
