"""批次2：三个独立 prompt builder 单元测试（提示词.md §20.1 1-12/19）。

覆盖：
- 首帧：无角色/单角色/多角色、空字段不产生空标签、含景别机位光线构图、override 优先、prompt_en 兼容
- 尾帧：含 change+preserve、无角色只用首帧、有角色加身份参考、override 优先、motion 回退
- 视频：i2v 运动为主、keyframes 含过渡、无角色不出 face/outfit、override 优先、不拼风格后缀、固定镜头不追加 push-in
"""
from src.core.models import Project, Shot, Character, StyleAnchor
from src.core.prompt_builder import (
    build_first_frame_prompt, build_last_frame_prompt, build_video_prompt,
)


def _proj(chars=None, style=None):
    return Project(name="t",
                   style=style or StyleAnchor(art_style="anime", extra="high detail"),
                   characters=chars or [])


def _shot(**kw):
    return Shot(shot_id="s01_c01", scene_id="s01", index=1, **kw)


# ---------- 首帧 ----------
def test_first_frame_no_characters():
    p = _proj()
    s = _shot(description="a ruined street", shot_size="medium shot",
              camera_angle="low angle", lighting="dusk")
    prompt = build_first_frame_prompt(p, s)
    assert "ruined street" in prompt
    assert "medium shot" in prompt
    assert "low angle" in prompt
    assert "dusk" in prompt
    assert "anime" in prompt                       # 风格后缀
    assert "character" not in prompt.lower()       # 无角色不产生角色一致性要求


def test_first_frame_single_character():
    p = _proj(chars=[Character(name="Alan", description_en="15yo boy in grey cloak")])
    s = _shot(characters=["Alan"], description="street")
    prompt = build_first_frame_prompt(p, s)
    assert "15yo boy in grey cloak" in prompt
    assert "street" in prompt


def test_first_frame_multi_characters():
    p = _proj(chars=[Character(name="Alan", description_en="a boy"),
                     Character(name="Dog", description_en="a mechanical dog")])
    s = _shot(characters=["Alan", "Dog"], description="scene")
    prompt = build_first_frame_prompt(p, s)
    assert "a boy" in prompt
    assert "a mechanical dog" in prompt


def test_first_frame_empty_produces_no_empty_tags():
    p = _proj()
    s = _shot()
    prompt = build_first_frame_prompt(p, s)
    assert "none" not in prompt.lower()
    assert "undefined" not in prompt.lower()
    assert "未填写" not in prompt
    assert ", ," not in prompt


def test_first_frame_includes_size_camera_lighting_composition():
    p = _proj()
    s = _shot(shot_size="close-up", camera_angle="high angle",
              lighting="backlight", composition="rule of thirds")
    prompt = build_first_frame_prompt(p, s)
    assert "close-up" in prompt
    assert "high angle" in prompt
    assert "backlight" in prompt
    assert "rule of thirds" in prompt


def test_first_frame_override_priority():
    p = _proj()
    s = _shot(prompt_en="auto english", first_frame_prompt_override="manual override")
    assert build_first_frame_prompt(p, s) == "manual override, anime, high detail"


def test_first_frame_prompt_en_fallback():
    """旧 prompt_en 仍能作为首帧提示词（§20.1.19）。"""
    p = _proj()
    s = _shot(prompt_en="old english prompt")
    assert build_first_frame_prompt(p, s) == "old english prompt, anime, high detail"


def test_first_frame_style_suffix_once():
    p = _proj(style=StyleAnchor(art_style="anime", extra="high detail"))
    s = _shot(description="desc")
    assert build_first_frame_prompt(p, s).count("anime") == 1


# ---------- 尾帧 ----------
def test_last_frame_includes_change_and_preserve():
    p = _proj()
    s = _shot(frame_change="arm raised", preserve_elements=["identity", "outfit"])
    prompt = build_last_frame_prompt(p, s)
    assert "arm raised" in prompt
    assert "identity" in prompt
    assert "outfit" in prompt
    assert "preserve" in prompt.lower()


def test_last_frame_no_characters_only_first_frame():
    """无角色尾帧只使用首帧参考，不报错（§20.1.7）。"""
    p = _proj()
    s = _shot()
    prompt = build_last_frame_prompt(p, s)
    assert "first reference image" in prompt.lower()
    assert "character" not in prompt.lower()


def test_last_frame_with_characters():
    p = _proj(chars=[Character(name="Alan", description_en="a boy")])
    s = _shot(characters=["Alan"])
    prompt = build_last_frame_prompt(p, s)
    assert "character identity" in prompt.lower()


def test_last_frame_override_priority():
    p = _proj()
    s = _shot(last_frame_prompt_en="auto", last_frame_prompt_override="manual")
    assert build_last_frame_prompt(p, s).startswith("manual")


def test_last_frame_motion_fallback():
    """frame_change 空时回退 motion（§14 兼容规则 7）。"""
    p = _proj()
    s = _shot(motion="walks forward")
    assert "walks forward" in build_last_frame_prompt(p, s)


# ---------- 视频 ----------
def test_video_i2v_motion_focused():
    """图生视频 prompt 以运动和稳定要求为主（§20.1.10）。"""
    s = _shot(motion="arm raises", camera_movement="slow push-in", motion_pace="slow")
    prompt = build_video_prompt(s, "i2v")
    assert "arm raises" in prompt
    assert "slow push-in" in prompt
    assert "slow" in prompt


def test_video_keyframes_includes_transition():
    """keyframes prompt 包含首尾帧过渡关系（§20.1.11）。"""
    s = _shot(transition_description="from standing to blocking", characters=["Alan"])
    prompt = build_video_prompt(s, "keyframes")
    assert "transition" in prompt.lower() or "keyframe" in prompt.lower()
    assert "from standing to blocking" in prompt


def test_video_no_characters_no_face_outfit():
    """无角色视频不出现 face / outfit consistency（§20.1.12）。"""
    s = _shot()
    prompt_i2v = build_video_prompt(s, "i2v")
    prompt_kf = build_video_prompt(s, "keyframes")
    assert "face" not in prompt_i2v.lower() and "outfit" not in prompt_i2v.lower()
    assert "face" not in prompt_kf.lower() and "outfit" not in prompt_kf.lower()


def test_video_override_priority():
    s = _shot(video_prompt_en="auto", video_prompt_override="manual")
    assert build_video_prompt(s, "i2v") == "manual"


def test_video_no_style_suffix():
    """视频 prompt 不拼风格后缀（继承自输入帧）。"""
    s = _shot(motion="jump")
    prompt = build_video_prompt(s, "i2v")
    assert "anime" not in prompt
    assert "high detail" not in prompt


def test_video_fixed_camera_no_pushin():
    """固定镜头不自动追加 push-in/tracking（§13.4）。"""
    s = _shot(motion="idle", camera_movement="fixed camera")
    prompt = build_video_prompt(s, "i2v")
    assert "fixed camera" in prompt
    assert "push-in" not in prompt.lower()
    assert "tracking" not in prompt.lower()
