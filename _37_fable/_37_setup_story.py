# _37_fable/_37_setup_story.py — 冻结故事落库：新建故事段 s09/s10 + 9 镜 + 预编剧字段。
# 36.md §4.5 口径：新故事段创建用 structure_service 脚本级操作（正式工具面属阶段 C）。
# 幂等：s09 已存在则跳过创建（重跑安全）。
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.store import ProjectStore
from src.core.models import Shot, ImageAsset
from src.storyboard.structure_service import insert_scenes, update_shot_fields

PROJ_DIR = Path(__file__).resolve().parent.parent / "projects" / "Fable_Pilot_Fox_and_Grapes_V1"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _37_story import SHOTS, SEGMENTS  # noqa: E402


def main():
    store = ProjectStore(PROJ_DIR)
    project = store.load()
    existing = {s.scene_id for s in project.scenes}
    print("existing scenes:", sorted(existing))

    seg_ids = {}
    if "s09" in existing and "s10" in existing:
        seg_ids = {"A": "s09", "B": "s10"}
        print("segments already exist, reuse")
    else:
        new = insert_scenes(project, specs=[
            {"summary": SEGMENTS[0]["summary"], "characters": ["狐狸"]},
            {"summary": SEGMENTS[1]["summary"], "characters": ["狐狸"]}])
        store.save(project)
        seg_ids = {"A": new[0].scene_id, "B": new[1].scene_id}
        print("created segments:", seg_ids)
    assert seg_ids["A"] == "s09" and seg_ids["B"] == "s10", \
        f"段 id 与冻结剧本不符: {seg_ids}（预期 s09/s10）——请人工核对后再跑"

    # 建镜头（幂等：按 shot_id 存在性跳过）
    for pdir in (PROJ_DIR,):
        project = store.load()
        by_seg = {"A": [], "B": []}
        for spec in SHOTS:
            by_seg[spec["segment"]].append(spec)
        for seg_key, specs in by_seg.items():
            scene = next(s for s in project.scenes if s.scene_id == seg_ids[seg_key])
            have = {sh.shot_id for sh in scene.shots}
            for i, spec in enumerate(specs, start=1):
                sid = f"{seg_ids[seg_key]}_c{i:02d}"
                if sid in have:
                    print(f"[{sid}] exists, skip create")
                    continue
                scene.shots.append(Shot(shot_id=sid, scene_id=seg_ids[seg_key],
                                        index=len(scene.shots) + 1,
                                        characters=["狐狸"], first_frame=ImageAsset(),
                                        last_frame=ImageAsset()))
                print(f"[{sid}] created")
            # index 重排
            for j, sh in enumerate(scene.shots, start=1):
                sh.index = j
        store.save(project)

    # 写预编剧字段（update_shot_fields：invalidation/receipt 同口径）
    # 镜号映射：段内从 c01 重新计数（N5 是段 B 第 1 镜 = s10_c01）
    seg_counter = {"A": 0, "B": 0}
    sid_of = {}
    for spec in SHOTS:
        seg_counter[spec["segment"]] += 1
        sid_of[spec["shot"]] = f"{seg_ids[spec['segment']]}_c{seg_counter[spec['segment']]:02d}"
    for spec in SHOTS:
        sid = sid_of[spec["shot"]]
        project = store.load()
        result = update_shot_fields(project, sid, spec["fields"])
        store.save(project)
        print(f"[{spec['shot']} -> {sid}] fields changed={result.get('changed')} "
              f"invalidated={result.get('prompts_invalidated')}")
    print("done: 冻结故事落库完成（s09=段A 4镜 / s10=段B 5镜）")


if __name__ == "__main__":
    main()
