# tests/test_ai_7c_5_composition.py
# AI-7C-5 scoped composition focused tests（§59/§64/§65）。
#
# build_composition_clips 是纯函数（不调 Agnes）——直接测之（构造 project + run + child state）。
# 覆盖：targeted run -> 受影响 shot 用 child 新版本 + unaffected shot 用 baseline final_video（§64）；
#       非 targeted -> 全 shot 需 child state（既有行为，缺则 raise）；baseline 缺失 raise。
from src.core.models import Project, Scene, Shot, Character, StyleAnchor, VideoVersion
from src.ai.project_autopilot import (ProjectProductionRun, CompositionClip,
                                       build_composition_clips, build_project_source_signature)
from src.ai.autopilot import ShotExecutionState


def _vv(vid, local=None):
    return VideoVersion(version_id=vid, local=local or f"/p/{vid}.mp4",
                        url=f"u_{vid}", seconds_returned=4.0, created_at=1.0)


def _proj():
    """2 scene 各 1 shot。s01_c01 被 regen（新 v_new + 旧 v_old final）；s02_c01 unaffected（v_base final）。"""
    s01 = Shot(shot_id="s01_c01", scene_id="s01", index=1, description="a",
               characters=["Xiaoyu"], target_duration_s=4, link_mode="i2v",
               video_versions=[_vv("v_new"), _vv("v_old")], final_video="v_old")
    s02 = Shot(shot_id="s02_c01", scene_id="s02", index=1, description="b",
               characters=["Aran"], target_duration_s=5, link_mode="i2v",
               video_versions=[_vv("v_base")], final_video="v_base")
    return Project(
        name="demo", style=StyleAnchor(),
        characters=[Character(name="Xiaoyu", description_en="g"),
                    Character(name="Aran", description_en="h")],
        scenes=[Scene(scene_id="s01", summary="s1", characters=["Xiaoyu"], shots=[s01]),
                Scene(scene_id="s02", summary="s2", characters=["Aran"], shots=[s02])],
        story_text="story")


def _run(project, *, target=None):
    r = ProjectProductionRun(run_id="prun_t", project_name="demo",
                            source_signature=build_project_source_signature(project),
                            target_scene_ids=target, created_at=1.0, updated_at=1.0)
    return r


# ============ ① targeted：新版本 + baseline 混合（§64/§65）============
def test_targeted_mixes_new_and_baseline():
    project = _proj()
    run = _run(project, target=["s01"])
    state = ShotExecutionState(index=1, shot_id="s01_c01", video_version_id="v_new")
    clips = build_composition_clips(project, run,
                                    {"s01_c01": state},
                                    {"s01_c01": "srun_1"})
    assert len(clips) == 2
    by_shot = {c.shot_id: c for c in clips}
    # s01_c01 受影响 -> child 新版本 v_new
    assert by_shot["s01_c01"].video_version_id == "v_new"
    assert by_shot["s01_c01"].local_path == "/p/v_new.mp4"
    assert by_shot["s01_c01"].scene_run_id == "srun_1"   # §69 traceability
    # s02_c01 unaffected -> baseline final_video v_base（§64）
    assert by_shot["s02_c01"].video_version_id == "v_base"
    assert by_shot["s02_c01"].local_path == "/p/v_base.mp4"
    assert by_shot["s02_c01"].scene_run_id == "baseline"


# ============ ② 非 targeted：全 shot 需 child state（既有行为）============
def test_non_targeted_requires_all_child_state():
    project = _proj()
    run = _run(project, target=None)
    state = ShotExecutionState(index=1, shot_id="s01_c01", video_version_id="v_new")
    # s02_c01 无 child state -> raise（既有行为）
    import pytest
    with pytest.raises(RuntimeError, match="s02_c01"):
        build_composition_clips(project, run, {"s01_c01": state})


# ============ ③ targeted：unaffected 无 baseline -> raise（§64）============
def test_targeted_baseline_missing_raises():
    project = _proj()
    # s02_c01 无 final_video（unaffected 但无既有完成版本）
    project.scenes[1].shots[0].final_video = None
    project.scenes[1].shots[0].video_versions = []
    run = _run(project, target=["s01"])
    state = ShotExecutionState(index=1, shot_id="s01_c01", video_version_id="v_new")
    import pytest
    with pytest.raises(RuntimeError, match="s02_c01.*baseline"):
        build_composition_clips(project, run, {"s01_c01": state})


# ============ ④ targeted：target shot 缺 child state -> 不是 baseline，是 raise（target scene 未跑）============
def test_targeted_target_shot_missing_state_raises():
    """target scene 的 shot 必须有 child state（它在 run.scenes 里跑了）；不能 fallback baseline。"""
    project = _proj()
    run = _run(project, target=["s01", "s02"])   # 两 scene 都 target
    # 只 s01_c01 有 state，s02_c01 无（但 s02 是 target -> 不能 baseline）-> raise
    state = ShotExecutionState(index=1, shot_id="s01_c01", video_version_id="v_new")
    import pytest
    with pytest.raises(RuntimeError, match="s02_c01"):
        build_composition_clips(project, run, {"s01_c01": state})


# ============ ⑤ 顺序：按 source_signature scene/shot order（§三十四）============
def test_clip_order_follows_source_signature():
    project = _proj()
    run = _run(project, target=["s01", "s02"])
    st1 = ShotExecutionState(index=1, shot_id="s01_c01", video_version_id="v_new")
    st2 = ShotExecutionState(index=1, shot_id="s02_c01", video_version_id="v_base2")
    # 给 s02 一个 v_base2 版本
    project.scenes[1].shots[0].video_versions = [_vv("v_base2"), _vv("v_base")]
    clips = build_composition_clips(project, run,
                                    {"s01_c01": st1, "s02_c01": st2})
    assert [c.shot_id for c in clips] == ["s01_c01", "s02_c01"]   # source_signature 顺序
