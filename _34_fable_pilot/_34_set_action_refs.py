# _34_fable_pilot/_34_set_action_refs.py — 动作接力参考组（v1d）参考选择。
# 原则（用户裁定）：只接力人物动作——参考=上一镜结尾动作的已批准姿态变体；视角/构图/背景
# 按本镜自己的分镜来，不追求像素/环境连续（区别于 V1C prev_tail）。
# 走 structure_service.update_shot_fields 白名单（video_reference_asset_id 已列入 34.md Human Gate）。
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.store import ProjectStore
from src.storyboard.structure_service import update_shot_fields

PROJ_DIR = Path(__file__).resolve().parent.parent / "projects" / "Fable_Pilot_Fox_and_Grapes_V1"

# 上镜结尾动作 → 本镜参考（全部 ∈ R3 look approved_reference_assets，adapter 资格校验可过）
ACTION_RELAY_REFS = {
    "s02_c01": "ref_2ecaca2f3553",  # walk_side：s01 空镜无可接力 → 入场行走
    "s03_c01": "ref_2ecaca2f3553",  # walk_side：s02 结尾=行走中（动作接力）
    "s04_c01": "ref_c19d9a0df2ec",  # alert_gaze：s03 结尾=近景警觉凝视
    "s05_c01": "ref_a1b93821b4a6",  # crouch_ready：s04 结尾=蓄力结束态
    "s06_c01": "ref_e5c261d74890",  # sit_look_up：s05 结尾=落地（落地后坐地仰望）
    "s07_c01": "ref_e5c261d74890",  # sit_look_up：s06 结尾=坐地仰望（坐姿延续，镜内转身）
}


def main():
    store = ProjectStore(PROJ_DIR)
    for shot_id, ref in ACTION_RELAY_REFS.items():
        project = store.load()
        before = project.get_shot(shot_id).video_reference_asset_id
        result = update_shot_fields(project, shot_id, {"video_reference_asset_id": ref})
        store.save(project)
        print(f"[{shot_id}] {before} -> {ref} changed={result.get('changed')}")
    print("done: 动作接力参考已写入 6 镜")


if __name__ == "__main__":
    main()
