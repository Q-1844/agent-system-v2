# _34_fable_pilot/_34_set_v1f_groupC.py — v1f 组 C（自适应：狐狸蹲伏仰望姿态接目送蝴蝶）。
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.store import ProjectStore
from src.storyboard.structure_service import update_shot_fields

PROJ_DIR = Path(__file__).resolve().parent.parent / "projects" / "Fable_Pilot_Fox_and_Grapes_V1"

GROUP_C = {
    "s06_c01": {
        "description": "狐狸保持蹲坐仰望，目送白蝴蝶飞远消失在藤架上空，尾巴缓缓摆动，满足地眯起眼",
        "camera_angle": "eye level",
        "composition": "中景侧面，狐狸蹲坐前景，藤架天空留白",
        "target_duration_s": 8.0,
        "video_reference_asset_id": "prev_tail",  # 接力 s05 尾帧
    },
    "s07_c01": {
        "description": "狐狸起身转身，慢悠悠沿土路走出画面，尾巴轻晃，心满意足",
        "camera_angle": "eye level",
        "composition": "全景，狐狸背影渐远，土路纵深",
        "target_duration_s": 5.0,
        "video_reference_asset_id": "prev_tail",  # 接力 s06 尾帧
    },
}


def main():
    store = ProjectStore(PROJ_DIR)
    for shot_id, fields in GROUP_C.items():
        project = store.load()
        result = update_shot_fields(project, shot_id, fields)
        store.save(project)
        print(f"[{shot_id}] changed={result.get('changed')}")
    print("done: v1f 组 C 配置写入")


if __name__ == "__main__":
    main()
