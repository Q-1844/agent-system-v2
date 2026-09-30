# -*- coding: utf-8 -*-
"""盲测夹具评审页 manifest：榛果 Look 4 候选 + prop + 2 场景视图（全部待用户批准）。"""
import io
import json
import shutil
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parent
PROJ = ROOT / "projects" / "晨露快递"
OUT = ROOT / "_43_blind"
MEDIA = OUT / "review_media"
MEDIA.mkdir(parents=True, exist_ok=True)


def copy_img(src: Path, name: str) -> str:
    dst = MEDIA / name
    if not dst.exists():
        shutil.copy2(src, dst)
    return f"review_media/{name}"


look_dir = PROJ / "output" / "resources" / "looks" / "look_1a5486946002" / "generations"
looks = []
names = {"gen_0675e0109d2f": "候选 1", "gen_21edc4ab070b": "候选 2",
         "gen_240c2b373f57": "候选 3", "gen_f2bc742d508b": "候选 4"}
for gid, label in names.items():
    f = look_dir / gid / "candidate_01.png"
    if f.exists():
        looks.append({"name": f"榛果 Look {label}", "src": copy_img(f, f"look_{label}.png"),
                      "note": "vision 审查意见：背包形态需人工裁定"})

sciv_files = [
    ("晨雾草原 hero 视图",
     PROJ / "output/scene_system/locations/loc_0dc50580ccc8/views/view_50fb09fed6a0/generations/gen_aef8ab287f73/candidate_01.png"),
    ("老橡树 hero 视图（含树洞小木门）",
     PROJ / "output/scene_system/locations/loc_39c02f687e2b/views/view_87e2664b1a11/generations/gen_8cfb565caec4/candidate_01.png"),
]
scenes = [{"name": n, "src": copy_img(f, f"scene_{i}.png"), "note": "正式 vision 审查已过，复核美术方向"}
          for i, (n, f) in enumerate(sciv_files) if f.exists()]

prop_f = PROJ / "input" / "prop_dew_letter.png"
prop_group = [{"name": "露珠信（道具参考）", "src": copy_img(prop_f, "prop_dew.png"), "note": "已登记批准（prop_c52862d43233）"}] \
    if prop_f.exists() else []

manifest = {
    "round": "fixture43",
    "round_title": "Phase 43 盲测夹具 · 《晨露快递》资产批准（2026-09-30）",
    "round_date": "2026-09-30",
    "intro": "<b>盲测夹具资产批准</b>：这些是给盲测 Agent 的项目状态（批准后才冻结进入盲测）。"
             "Look 审查连续 4 轮 needs_fix（背包形态问题）——候选 4 的邮差包形态最贴描述；"
             "你也可以选「去掉背包重新生成」。点卡片循环 未评→✅批准→❌不可用，备注逐条落盘。",
    "image_groups": [
        {"id": "look", "title": "角色 榛果 hero Look（4 候选，vision 审查未过=背包被画成蓬松背包）", "images": looks},
        {"id": "scenes", "title": "场景 hero 视图（2 个 location，正式 vision 审查已过）", "images": scenes},
        {"id": "prop", "title": "道具参考", "images": prop_group},
    ],
    "video_groups": [],
    "questions": [
        {"id": "q_look", "title": "选哪张作为榛果的 hero Look？（⭐ 标注）或选「去掉背包重新生成」",
         "why": "Look 是盲测的身份锚资产，必须用户批准后冻结"},
        {"id": "q_scene", "title": "两张场景视图可接受吗？（正式审查已过，复核美术方向）",
         "why": "盲测的场景基线"},
    ],
}

(OUT / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print("fixture manifest written; looks:", len(looks), "scenes:", len(scenes))
