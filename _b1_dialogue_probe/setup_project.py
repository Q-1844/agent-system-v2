# _b1_dialogue_probe/setup_project.py
# 创建 B1 E2E 项目 __native_av_b1_e2e（真实 ProjectStore 落盘，scratch 项目）。
# 幂等：项目已存在则跳过（保留已生成数据）。只写 projects/ 下的 scratch 项目，
# 不触碰任何生产/既有项目。
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.models import Character, Project, Scene, Shot  # noqa: E402
from src.core.store import ProjectStore  # noqa: E402

from samples import CHARACTERS, SHOTS  # noqa: E402


def main() -> int:
    pdir = Path(__file__).resolve().parent.parent / "projects" / "__native_av_b1_e2e"
    if (pdir / "output" / "project.json").exists():
        print(f"project exists, skip: {pdir}")
        return 0
    proj = Project(name=pdir.name)
    proj.characters = [Character(name=c["name"], description_en=c["description_en"])
                       for c in CHARACTERS]
    scenes: dict[str, Scene] = {}
    for sd in SHOTS:
        sc = scenes.setdefault(sd["scene_id"],
                               Scene(scene_id=sd["scene_id"],
                                     title=f"scene {sd['scene_id']}"))
        shot = Shot(shot_id=sd["shot_id"], scene_id=sd["scene_id"],
                    index=sd["index"], description=sd["description"],
                    motion=sd["motion"], characters=sd["characters"],
                    dialogue=sd["dialogue"])
        shot.video.num_frames = sd["num_frames"]
        shot.video.frame_rate = 24
        sc.shots.append(shot)
    proj.scenes = list(scenes.values())
    ProjectStore(pdir).save(proj)
    print(f"project created: {pdir}")
    for sd in SHOTS:
        print(f"  {sd['shot_id']} chars={sd['characters']} dialogue={sd['dialogue']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
