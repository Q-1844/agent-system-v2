# _34_fable_pilot/_34_set_v1f_groupB.py — v1f 组 B（自适应：蝴蝶从 s03 尾帧的葡萄串旁飞起）。
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.store import ProjectStore
from src.storyboard.structure_service import update_shot_fields

PROJ_DIR = Path(__file__).resolve().parent.parent / "projects" / "Fable_Pilot_Fox_and_Grapes_V1"

GROUP_B = {
    "s04_c01": {
        "description": "一只白蝴蝶从葡萄串旁飞起，狐狸视线紧紧追随，头随蝴蝶转动，好奇心大作，身体前倾",
        "camera_angle": "eye level",
        "composition": "中近景，蝴蝶飞行轨迹在狐狸与葡萄串之间",
        "target_duration_s": 8.0,
        "video_reference_asset_id": "prev_tail",  # 接力 s03 尾帧
    },
    "s05_c01": {
        "description": "蝴蝶低飞盘旋在狐狸面前，狐狸伸前爪轻轻扑击，蝴蝶灵巧躲开飞高",
        "camera_angle": "eye level",
        "composition": "中景正面，狐狸前爪扑击动作清晰",
        "target_duration_s": 8.0,
        "video_reference_asset_id": "prev_tail",  # 接力 s04 尾帧
    },
}


def main():
    store = ProjectStore(PROJ_DIR)
    for shot_id, fields in GROUP_B.items():
        project = store.load()
        result = update_shot_fields(project, shot_id, fields)
        store.save(project)
        print(f"[{shot_id}] changed={result.get('changed')}")
    print("done: v1f 组 B 配置写入")


if __name__ == "__main__":
    main()
