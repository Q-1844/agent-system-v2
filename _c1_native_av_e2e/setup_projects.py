# _c1_native_av_e2e/setup_projects.py
# Native AV C1 E2E 项目构造（18.md §32/§33；scratch 项目，幂等）。
#
# - UNBOUND 主项目 __native_av_c1_e2e：A/B/C/D/E/F/H 七样本；spoken_language 经
#   生产 Project 模型设置（§5）；首帧复用 A0 已验证资产 URL（B1 先例）；
#   E 样本带 audio_event_intent binding 块（§16；audio-intent-only binding →
#   按 §3 仍路由 UNBOUND——正是要验证的语义）。
# - BOUND 项目复用 __shot_binding_visual_final_gp（S3C.2 golden path 项目：
#   binding+approved SIV+BOUND 首帧已在盘上，幂等续跑零重烧）；只补
#   spoken_language + 台词 + 视频提交。
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from samples import (ASSET_A, ASSET_B, ASSET_C, ASSET_D, ASSET_E, ASSET_F,   # noqa: E402
                     ASSET_H, BOUND_PROJECT_NAME, BOUND_SHOT, CHARACTERS,
                     LANGUAGE, PROJECT_NAME, SHOTS)

ASSETS_JSON = ROOT / "_audio_probe" / "assets" / "assets.json"


def _seed_first_frame(shot, url: str) -> None:
    """把 A0 资产 URL 落成本镜已选首帧（等价真实「生成→选卡」后的状态）。"""
    from src.core.models import Asset, ImageAsset
    shot.first_frame = ImageAsset(
        candidates=[Asset(local="", url=url, status="done")],
        selected_index=0, status="done")


def setup_unbound() -> Path:
    from src.core.models import Character, Project, Scene, Shot
    from src.core.store import ProjectStore
    from src.shot_binding.models import (
        AudioEventIntent,
        ShotBindingDocument,
        ShotProductionBinding,
    )
    from src.shot_binding.store import ShotBindingStore

    pdir = ROOT / "projects" / PROJECT_NAME
    assets = json.loads(ASSETS_JSON.read_text(encoding="utf-8"))
    if not (pdir / "output" / "project.json").exists():
        pdir.mkdir(parents=True, exist_ok=True)
        proj = Project(name=PROJECT_NAME)
        proj.characters = [Character(name=c["name"],
                                     description_en=c["description_en"])
                           for c in CHARACTERS]
        # §5：language truth = project.native_av.spoken_language（生产字段）
        proj.native_av.spoken_language = LANGUAGE
        scenes: dict[str, Scene] = {}
        for sd in SHOTS:
            sc = scenes.setdefault(sd["scene_id"], Scene(scene_id=sd["scene_id"],
                                                         title=f"scene {sd['scene_id']}"))
            shot = Shot(shot_id=sd["shot_id"], scene_id=sd["scene_id"],
                        index=sd["index"], description=sd["description"],
                        motion=sd["motion"], characters=sd["characters"],
                        dialogue=sd["dialogue"])
            shot.video.num_frames = sd["seconds"] * 24 + 1   # 8n+1 近似（handler 内 snap）
            shot.video.frame_rate = 24
            _seed_first_frame(shot, assets[sd["asset"]]["url"])
            sc.shots.append(shot)
        proj.scenes = list(scenes.values())
        ProjectStore(pdir).save(proj)
        # E 样本：audio_event_intent binding 块（audio-intent-only，无视觉字段）
        e = next(s for s in SHOTS if s["key"] == "E")
        doc = ShotBindingDocument(bindings={
            e["shot_id"]: ShotProductionBinding(
                shot_id=e["shot_id"],
                audio_event_intent=AudioEventIntent(
                    music_policy=e["audio_intent"]["music_policy"],
                    directives=list(e["audio_intent"]["directives"])))})
        ShotBindingStore(pdir).save(doc)
        print(f"[setup] UNBOUND project created: {pdir}")
    else:
        print(f"[setup] UNBOUND project exists (idempotent): {pdir}")
    return pdir


def setup_bound() -> Path:
    """BOUND 样本 G：复用 S3C.2 golden path 项目（binding/SIV/BOUND 首帧在盘）。"""
    from src.core.models import NativeAVSettings
    from src.core.store import ProjectStore
    pdir = ROOT / "projects" / BOUND_PROJECT_NAME
    if not (pdir / "output" / "project.json").exists():
        raise RuntimeError(
            f"BOUND fixture 项目不存在: {pdir}（需先跑 _e2e_shot_binding_gp.py "
            f"产出 binding+approved SIV+BOUND 首帧）")
    store = ProjectStore(pdir)
    proj = store.load()
    changed = False
    if proj.native_av.spoken_language != LANGUAGE:
        proj.native_av = NativeAVSettings(spoken_language=LANGUAGE)
        changed = True
    shot = proj.get_shot(BOUND_SHOT["shot_id"])
    assert shot is not None, "BOUND shot 缺失"
    if shot.dialogue != BOUND_SHOT["dialogue"]:
        shot.dialogue = BOUND_SHOT["dialogue"]
        changed = True
    if changed:
        store.save(proj)
        print("[setup] BOUND project updated（spoken_language+dialogue）")
    else:
        print("[setup] BOUND project already configured (idempotent)")
    return pdir


if __name__ == "__main__":
    setup_unbound()
    setup_bound()
