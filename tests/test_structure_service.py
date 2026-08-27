"""AI-7D-R · 共享结构服务 src.storyboard.structure_service 单元测试。

纯内存操作（不存盘、不联网）；验证任意位置插入、id 分配（身份证号不重排）、
index 重排、原地修改、未知锚点拒绝、角色过滤由调用方做（本服务只存传入值）。
"""
import pytest

from src.core.models import Project, Scene, Shot, Character, StyleAnchor
from src.storyboard.structure_service import (
    insert_scenes_after, insert_shots_after,
    update_scene_fields, update_shot_fields, update_shots_batch,
    read_shot, read_scene,
    allocate_scene_id, allocate_shot_id, renumber_shot_indices,
    StructureError,
)


def _proj():
    return Project(
        name="demo", style=StyleAnchor(), story_text="雨",
        characters=[Character(name="LinXia", description_en="x")],
        scenes=[
            Scene(scene_id="s01", summary="段一", characters=["LinXia"],
                  shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1, description="a")]),
            Scene(scene_id="s02", summary="段二", characters=["LinXia"],
                  shots=[Shot(shot_id="s02_c01", scene_id="s02", index=1, description="b"),
                         Shot(shot_id="s02_c02", scene_id="s02", index=2, description="c")]),
        ],
        next_scene_seq=3,
    )


# ---------- 1. insert scene after 锚点 ----------
def test_insert_scenes_after_anchor_position_and_ids():
    p = _proj()
    new = insert_scenes_after(p, "s01", [{"summary": "新段", "characters": []}])
    assert len(new) == 1 and new[0].scene_id == "s03"
    assert [s.scene_id for s in p.scenes] == ["s01", "s03", "s02"]   # 插到 s01 后
    assert p.next_scene_seq == 4
    # 已有 id 不动
    assert p.scenes[0].scene_id == "s01" and p.scenes[2].scene_id == "s02"


def test_insert_scenes_no_anchor_appends():
    p = _proj()
    new = insert_scenes_after(p, None, [{"summary": "尾", "characters": []}])
    assert new[0].scene_id == "s03"
    assert [s.scene_id for s in p.scenes] == ["s01", "s02", "s03"]


def test_insert_scenes_bad_anchor():
    p = _proj()
    with pytest.raises(StructureError) as ei:
        insert_scenes_after(p, "s99", [{"summary": "x"}])
    assert ei.value.code == "not_found"


# ---------- 3/4. insert shot after 锚点 ----------
def test_insert_shots_after_anchor_position_index_and_ids():
    p = _proj()
    new = insert_shots_after(p, "s02", "s02_c01",
                             [{"description": "新镜", "characters": ["LinXia"],
                               "target_duration_s": 5}])
    assert len(new) == 1 and new[0].shot_id == "s02_c03"   # 不回收 c 号
    s02 = next(s for s in p.scenes if s.scene_id == "s02")
    assert [sh.shot_id for sh in s02.shots] == ["s02_c01", "s02_c03", "s02_c02"]
    assert [sh.index for sh in s02.shots] == [1, 2, 3]      # 重排连续
    # 已有 shot_id 不重号
    assert s02.shots[0].shot_id == "s02_c01"


def test_insert_shots_no_anchor_appends_index_continues():
    p = _proj()
    new = insert_shots_after(p, "s02", None,
                             [{"description": "x", "characters": [], "target_duration_s": 3}])
    assert new[0].shot_id == "s02_c03"
    s02 = next(s for s in p.scenes if s.scene_id == "s02")
    assert [sh.shot_id for sh in s02.shots] == ["s02_c01", "s02_c02", "s02_c03"]
    assert [sh.index for sh in s02.shots] == [1, 2, 3]


# ---------- 镜头间插入的接力依赖约束 ----------
def _chain_proj():
    """s01: c01 ← c02 ← c03（接力链）。"""
    return Project(
        name="d", style=StyleAnchor(),
        scenes=[Scene(scene_id="s01", summary="x", shots=[
            Shot(shot_id="s01_c01", scene_id="s01", index=1, description="a"),
            Shot(shot_id="s01_c02", scene_id="s01", index=2, description="b",
                 opening_source="inherit_previous", source_shot_id="s01_c01"),
            Shot(shot_id="s01_c03", scene_id="s01", index=3, description="c",
                 opening_source="inherit_previous", source_shot_id="s01_c02"),
        ])])


def test_insert_between_continuity_pair_blocked():
    p = _chain_proj()
    before = [sh.shot_id for sh in p.scenes[0].shots]
    # c02 接力 c01，不能在 c01 后（c01 与 c02 之间）插入
    with pytest.raises(StructureError) as ei:
        insert_shots_after(p, "s01", "s01_c01", [{"description": "x"}])
    assert ei.value.code == "dependency_block"
    assert [sh.shot_id for sh in p.scenes[0].shots] == before   # 未插入


def test_insert_after_chain_end_allowed():
    p = _chain_proj()
    new = insert_shots_after(p, "s01", "s01_c03", [{"description": "x"}])
    assert new[0].shot_id == "s01_c04"
    assert [sh.shot_id for sh in p.scenes[0].shots] == ["s01_c01", "s01_c02", "s01_c03", "s01_c04"]


def test_insert_between_independent_shots_allowed():
    p = _proj()   # s02 的 c01/c02 都是 independent
    new = insert_shots_after(p, "s02", "s02_c01", [{"description": "x"}])
    assert new[0].shot_id == "s02_c03"
    assert [sh.shot_id for sh in p.scenes[0].shots if p.scenes[0].scene_id == "s02"] or True
    s02 = next(s for s in p.scenes if s.scene_id == "s02")
    assert [sh.shot_id for sh in s02.shots] == ["s02_c01", "s02_c03", "s02_c02"]


def test_scene_insert_always_allowed_no_continuity_constraint():
    p = _chain_proj()
    # 段之间没有接力依赖，任意位置可插
    new = insert_scenes_after(p, "s01", [{"summary": "新段"}])
    assert [s.scene_id for s in p.scenes] == ["s01", new[0].scene_id]


# ---------- 只读查询 ----------
def test_read_shot_returns_creative_fields():
    p = _chain_proj()
    d = read_shot(p, "s01_c02")
    assert d["shot_id"] == "s01_c02" and d["target_duration_s"] is None
    assert d["opening_source"] == "inherit_previous"
    assert d["source_shot_id"] == "s01_c01"
    # 不含产物/内部字段
    assert "first_frame" not in d and "prompt_en" not in d and "video_versions" not in d


def test_read_shot_unknown_raises():
    p = _chain_proj()
    with pytest.raises(StructureError):
        read_shot(p, "s99_c01")


def test_read_scene_includes_all_shots():
    p = _chain_proj()
    d = read_scene(p, "s01")
    assert d["scene_id"] == "s01" and len(d["shots"]) == 3
    assert d["shots"][1]["shot_id"] == "s01_c02"
    with pytest.raises(StructureError):
        read_scene(p, "s99")


def test_insert_shots_bad_scene_or_anchor():
    p = _proj()
    with pytest.raises(StructureError):
        insert_shots_after(p, "s99", None, [{"description": "x"}])
    with pytest.raises(StructureError):
        insert_shots_after(p, "s02", "s99_c01", [{"description": "x"}])


# ---------- 5. update scene ----------
def test_update_scene_fields_only_provided():
    p = _proj()
    res = update_scene_fields(p, "s01", summary="改了")
    assert res["changed"] == ["summary"]
    s = next(x for x in p.scenes if x.scene_id == "s01")
    assert s.summary == "改了" and s.characters == ["LinXia"]   # 角色没传→不动
    update_scene_fields(p, "s01", characters=[])
    assert s.characters == []


# ---------- 6. update shot + duration 兜底 ----------
def test_update_shot_fields_and_duration_coerce():
    p = _proj()
    res = update_shot_fields(p, "s02_c02", target_duration_s=8)
    assert res["changed"] == ["target_duration_s"]
    sh = p.get_shot("s02_c02")
    assert sh.target_duration_s == 8 and sh.description == "c"   # 描述没传→不动
    # 非法值兜底 5
    update_shot_fields(p, "s02_c02", target_duration_s=-3)
    assert sh.target_duration_s == 5.0
    update_shot_fields(p, "s02_c02", target_duration_s="abc")
    assert sh.target_duration_s == 5.0


def test_update_unknown_entities():
    p = _proj()
    with pytest.raises(StructureError):
        update_scene_fields(p, "s99", summary="x")
    with pytest.raises(StructureError):
        update_shot_fields(p, "s99_c01", description="x")


# ---------- 9. 插入/修改不碰其他段/角色 ----------
def test_insert_and_update_do_not_touch_other():
    p = _proj()
    insert_scenes_after(p, "s01", [{"summary": "新", "characters": ["LinXia"]}])
    update_shot_fields(p, "s02_c01", description="改b")
    assert [c.name for c in p.characters] == ["LinXia"]
    # s01 原镜头、s02 未指定字段都在
    assert p.scenes[0].shots[0].shot_id == "s01_c01"
    assert p.get_shot("s02_c02").description == "c"


def test_allocate_ids_monotonic():
    p = _proj()
    assert allocate_scene_id(p) == "s03" and p.next_scene_seq == 4
    s01 = p.scenes[0]
    assert allocate_shot_id(s01) == "s01_c02"   # s01 现在只有 c01


def test_renumber_shot_indices():
    p = _proj()
    s02 = next(s for s in p.scenes if s.scene_id == "s02")
    s02.shots = [s02.shots[1], s02.shots[0]]   # 故意颠倒
    renumber_shot_indices(s02)
    assert [sh.index for sh in s02.shots] == [1, 2]


# ---------- 结构化拍摄字段写入 ----------
def test_update_shot_structured_fields():
    p = _proj()
    res = update_shot_fields(p, "s02_c01", {
        "shot_size": "close-up", "camera_angle": "低机位",
        "composition": "居中", "camera_movement": "缓慢推镜",
        "lighting": "侧逆光", "motion": "手指抚叶",
        "important_details": "怀旧、温柔", "unknown_field": "应被忽略",
    })
    sh = p.get_shot("s02_c01")
    assert sh.shot_size == "close-up" and sh.camera_movement == "缓慢推镜"
    assert sh.important_details == "怀旧、温柔"
    assert "unknown_field" not in res["changed"]
    assert not hasattr(sh, "unknown_field") or getattr(sh, "unknown_field", None) is None


def test_update_shot_fields_kwargs_compat():
    p = _proj()
    # 旧的关键字调用仍兼容
    res = update_shot_fields(p, "s02_c02", description="改", target_duration_s=10)
    assert set(res["changed"]) == {"description", "target_duration_s"}
    assert p.get_shot("s02_c02").target_duration_s == 10


def test_update_shots_batch():
    p = _proj()
    res = update_shots_batch(p, "s02", [
        {"shot_id": "s02_c01", "shot_size": "close-up", "lighting": "自然光"},
        {"shot_id": "s02_c02", "camera_movement": "跟随平移", "target_duration_s": 8},
    ])
    assert res["count"] == 2
    assert p.get_shot("s02_c01").shot_size == "close-up"
    assert p.get_shot("s02_c02").camera_movement == "跟随平移"
    assert p.get_shot("s02_c02").target_duration_s == 8


def test_update_shots_batch_rejects_foreign_shot():
    p = _proj()
    # s01_c01 不属于 s02
    with pytest.raises(StructureError):
        update_shots_batch(p, "s02", [{"shot_id": "s01_c01", "description": "x"}])
    with pytest.raises(StructureError):
        update_shots_batch(p, "s99", [])


# ---------- 改了影响画面的字段 → 旧自动 prompt 失效 ----------
def test_update_clears_stale_auto_prompts():
    p = _proj()
    sh = p.get_shot("s02_c01")
    sh.prompt_en = "old first frame english"
    sh.video_prompt_en = "old video english"
    sh.last_frame_prompt_en = "old last frame english"
    # 机位影响首帧、运动影响视频+尾帧
    res = update_shot_fields(p, "s02_c01", {"camera_angle": "低机位", "motion": "手抚叶"})
    assert res["prompts_invalidated"].get("first_frame") == "cleared"
    assert res["prompts_invalidated"].get("video") == "cleared"
    assert sh.prompt_en == "" and sh.video_prompt_en == ""


def test_update_keeps_manual_override():
    p = _proj()
    sh = p.get_shot("s02_c01")
    sh.first_frame_prompt_override = "my custom prompt"
    sh.prompt_en = "old auto"
    res = update_shot_fields(p, "s02_c01", {"lighting": "侧逆光"})
    assert res["prompts_invalidated"].get("first_frame") == "manual_override"
    assert sh.first_frame_prompt_override == "my custom prompt"   # 不动
    assert sh.prompt_en == "old auto"                            # 也不清（override 优先）


def test_duration_only_does_not_invalidate_prompts():
    p = _proj()
    sh = p.get_shot("s02_c01")
    sh.prompt_en = "keep me"
    res = update_shot_fields(p, "s02_c01", {"target_duration_s": 10})
    assert res["prompts_invalidated"] == {}
    assert sh.prompt_en == "keep me"


def test_batch_propagates_prompt_invalidation():
    p = _proj()
    p.get_shot("s02_c01").prompt_en = "old"
    res = update_shots_batch(p, "s02", [
        {"shot_id": "s02_c01", "camera_angle": "low"},
        {"shot_id": "s02_c02", "target_duration_s": 8},
    ])
    inv = res["prompts_invalidated"]
    assert inv["s02_c01"].get("first_frame") == "cleared"
    assert "s02_c02" not in inv   # 只改时长不清 prompt
