"""批次1：Shot 结构化字段（提示词.md §14）默认值 / 旧项目兼容 / 往返 / 多批次不破坏。

覆盖：
- 新字段全部安全默认值（Shot() 可构造，不 500）
- 旧 project.json（缺新字段）from_dict 正常加载，新字段取默认
- to_dict -> from_dict 往返保留新字段
- List 字段各自独立（非共享可变默认）
- prompt_en 仍作为首帧兼容字段
- 多批次字段（first_frame_batches 等）与选择指针不受新字段影响
- target_duration_s 默认 None（跟随配音）
"""
from src.core.models import Shot, ImageBatch, Candidate, VideoVersion


def _shot(**kw):
    return Shot(shot_id="s01_c01", scene_id="s01", index=1, **kw)


NEW_STR_FIELDS = [
    "opening_state", "camera_angle", "composition", "important_details",
    "last_frame_description", "end_state", "frame_change",
    "add_elements", "remove_elements", "end_composition", "preserve_custom",
    "camera_movement", "motion_pace", "environment_motion", "action_sequence",
    "camera_movement_detail", "transition_description", "video_preserve_custom",
    "last_frame_prompt_en", "video_prompt_en",
    "first_frame_prompt_override", "last_frame_prompt_override", "video_prompt_override",
    "first_frame_prompt_sig", "last_frame_prompt_sig", "video_prompt_sig",
]
NEW_LIST_FIELDS = ["required_elements", "preserve_elements", "video_preserve_elements"]


def test_new_fields_have_safe_defaults():
    """新字段全部空默认，Shot() 可构造。"""
    s = _shot()
    for f in NEW_STR_FIELDS:
        assert getattr(s, f) == "", f"{f} 默认应为空串"
    for f in NEW_LIST_FIELDS:
        assert getattr(s, f) == [], f"{f} 默认应为空 list"
    assert s.target_duration_s is None, "target_duration_s 默认 None（跟随配音）"


def test_legacy_project_json_loads_without_new_fields():
    """旧 project.json（缺新字段）from_dict 正常，新字段取默认。"""
    legacy = {
        "shot_id": "s01_c01", "scene_id": "s01", "index": 1,
        "description": "旧描述", "prompt_en": "old english prompt",
        "shot_size": "medium shot", "camera": "旧机位运镜", "lighting": "",
        "motion": "", "characters": ["小星"], "dialogue": "",
        "link_mode": "i2v", "seed": None,
        # 无任何新结构化字段
        "first_frame": {}, "last_frame": {}, "audio": {}, "video": {},
    }
    s = Shot.from_dict(legacy)
    assert s.description == "旧描述"
    assert s.prompt_en == "old english prompt"      # 首帧兼容字段保留
    assert s.camera == "旧机位运镜"                  # 旧字段不删
    assert s.opening_state == ""                     # 新字段默认
    assert s.required_elements == []
    assert s.target_duration_s is None
    assert s.camera_angle == ""                      # 与旧 camera 分离，独立默认


def test_new_fields_roundtrip():
    """to_dict -> from_dict 保留新字段值。"""
    s = _shot(opening_state="机甲前倾", camera_angle="低机位", composition="三分法",
              required_elements=["爆炸火光", "右臂"], target_duration_s=5.0,
              last_frame_description="结束画面", end_state="抬起右臂",
              frame_change="后仰", preserve_elements=["角色身份", "装甲"],
              camera_movement="缓慢推近", motion_pace="缓慢",
              video_preserve_elements=["角色身份", "场景布局"],
              last_frame_prompt_en="last en", video_prompt_en="video en",
              first_frame_prompt_override="ff override",
              first_frame_prompt_sig="abc123")
    d = s.to_dict()
    s2 = Shot.from_dict(d)
    assert s2.opening_state == "机甲前倾"
    assert s2.camera_angle == "低机位"
    assert s2.composition == "三分法"
    assert s2.required_elements == ["爆炸火光", "右臂"]
    assert s2.target_duration_s == 5.0
    assert s2.preserve_elements == ["角色身份", "装甲"]
    assert s2.camera_movement == "缓慢推近"
    assert s2.video_preserve_elements == ["角色身份", "场景布局"]
    assert s2.last_frame_prompt_en == "last en"
    assert s2.video_prompt_en == "video en"
    assert s2.first_frame_prompt_override == "ff override"
    assert s2.first_frame_prompt_sig == "abc123"


def test_list_fields_are_independent():
    """List 字段非共享可变默认（两个 Shot 的 required_elements 互不影响）。"""
    a = _shot()
    b = _shot()
    a.required_elements.append("x")
    assert b.required_elements == [], "List 默认值不应在实例间共享"


def test_multibatch_fields_unchanged_by_new_fields():
    """新字段不影响多批次/版本字段与选择指针。"""
    ff_batch = ImageBatch(batch_id="ff_test", kind="first_frame",
                          candidates=[Candidate(candidate_id="ff_test_1")])
    vv = VideoVersion(version_id="vv_test")
    s = _shot(first_frame_batches=[ff_batch], video_versions=[vv],
              selected_first_frame="ff_test_1", final_video="vv_test",
              opening_state="新字段也设了")
    d = s.to_dict()
    s2 = Shot.from_dict(d)
    assert len(s2.first_frame_batches) == 1
    assert s2.first_frame_batches[0].batch_id == "ff_test"
    assert s2.first_frame_batches[0].candidates[0].candidate_id == "ff_test_1"
    assert len(s2.video_versions) == 1
    assert s2.video_versions[0].version_id == "vv_test"
    assert s2.selected_first_frame == "ff_test_1"
    assert s2.final_video == "vv_test"
    assert s2.opening_state == "新字段也设了"   # 新字段与批次字段共存


def test_prompt_en_remains_first_frame_compat():
    """prompt_en 仍存在且可独立设置（首帧兼容字段，§14 规则 5）。"""
    s = _shot(prompt_en="first frame english")
    assert s.prompt_en == "first frame english"
    # 首帧 override 是独立字段，不覆盖 prompt_en
    s.first_frame_prompt_override = "manual override"
    assert s.prompt_en == "first frame english"
    assert s.first_frame_prompt_override == "manual override"
