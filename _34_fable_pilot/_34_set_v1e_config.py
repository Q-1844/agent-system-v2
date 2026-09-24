# _34_fable_pilot/_34_set_v1e_config.py — 分组接力（v1e）配置：参考策略+时长+断链镜头描写。
# 组1 s02→s03（链根+接力）；断链 s04（重锚定 crouch_ready + 低角度仰拍 8s）；
# 组2 s05（s04 尾帧接力 10s）；断链 s06（重锚定 sit_look_up + 地面机位 8s）；组3 s07（s06 尾帧接力）。
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.store import ProjectStore
from src.storyboard.structure_service import update_shot_fields

PROJ_DIR = Path(__file__).resolve().parent.parent / "projects" / "Fable_Pilot_Fox_and_Grapes_V1"

REFS = {
    "s02_c01": "prev_tail",          # 组1 起点：链根（s01 尾帧，与 relay2 同起点）
    "s03_c01": "prev_tail",          # 组1 接力：s02 新尾帧
    "s04_c01": "ref_a1b93821b4a6",   # 断链重锚定：crouch_ready（身份权威+蓄力起始姿态）
    "s05_c01": "prev_tail",          # 组2 接力：s04 新尾帧
    "s06_c01": "ref_e5c261d74890",   # 断链重锚定：sit_look_up（身份权威+坐望起始姿态）
    "s07_c01": "prev_tail",          # 组3 接力：s06 新尾帧
}
DURATIONS = {"s04_c01": 8.0, "s05_c01": 10.0, "s06_c01": 8.0}   # 断链展开+完整跳跃弧线
ENRICH = {  # 断链镜头的新机位/构图描写（分组的「环境或其它角度配置」）
    "s04_c01": {"camera_angle": "低角度仰拍特写",
                "composition": "藤叶前景虚化，狐狸仰头紧盯葡萄串，蓄力前凝视"},
    "s06_c01": {"camera_angle": "地面机位侧面",
                "composition": "土路前景，狐狸落地坐于地面仰望葡萄串，背影半侧"},
}


def main():
    store = ProjectStore(PROJ_DIR)
    for shot_id, ref in REFS.items():
        project = store.load()
        fields = {"video_reference_asset_id": ref}
        fields.update(DURATIONS.get(shot_id, {}) and {"target_duration_s": DURATIONS[shot_id]} or {})
        fields.update(ENRICH.get(shot_id, {}))
        before = project.get_shot(shot_id).video_reference_asset_id
        result = update_shot_fields(project, shot_id, fields)
        store.save(project)
        extra = [k for k in fields if k != "video_reference_asset_id"]
        print(f"[{shot_id}] ref {before} -> {ref} | extra={extra} changed={result.get('changed')}")
    print("done: v1e 分组接力配置写入")


if __name__ == "__main__":
    main()
