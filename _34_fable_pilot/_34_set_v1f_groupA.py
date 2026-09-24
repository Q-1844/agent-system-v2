# _34_fable_pilot/_34_set_v1f_groupA.py — v1f《狐狸与蝴蝶》组 A 配置（自适应编剧：B/C 组看尾帧后再写）。
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.store import ProjectStore
from src.storyboard.structure_service import update_shot_fields

PROJ_DIR = Path(__file__).resolve().parent.parent / "projects" / "Fable_Pilot_Fox_and_Grapes_V1"

GROUP_A = {
    "s02_c01": {
        "description": "清晨葡萄园，狐狸沿土路从画外走入，慢步巡视，尾巴轻摆，四处打量",
        "camera_angle": "eye level",
        "composition": "全景侧面跟拍，土路纵深，藤架在两侧",
        "target_duration_s": 5.0,
        "video_reference_asset_id": "ref_2ecaca2f3553",  # walk_side：身份锚（新故事无链根）
    },
    "s03_c01": {
        "description": "狐狸忽然停下，耳朵竖起，目光锁定——一只白蝴蝶落在前面的野花上",
        "camera_angle": "eye level",
        "composition": "中景，狐狸与前景野花蝴蝶同框",
        "target_duration_s": 5.0,
        "video_reference_asset_id": "prev_tail",  # 接力 s02 尾帧
    },
}


def main():
    store = ProjectStore(PROJ_DIR)
    for shot_id, fields in GROUP_A.items():
        project = store.load()
        result = update_shot_fields(project, shot_id, fields)
        store.save(project)
        print(f"[{shot_id}] changed={result.get('changed')}")
    print("done: v1f 组 A 配置写入")


if __name__ == "__main__":
    main()
