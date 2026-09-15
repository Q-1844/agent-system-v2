# tests/test_native_av_c1_routing_policy.py
# Native AV C1 — Commit A：routing cleanup + production policy（18.md §3/§4/§5）。
#
# 覆盖：
#   §3 dialogue metadata ≠ visual routing predicate——仅 dialogue_speaker_binding /
#      production_notes 的 binding 不触发 BOUND 路由（首帧/视频），legacy 路径可跑通；
#   §4 Controlled Dialogue 默认启用（dialogue 非空），显式 False = debug/compat
#      override，无 dialogue 行为逐字节不变；
#   §5 language production policy——request override > project.native_av.spoken_language；
#      有台词 + 皆缺 → language_missing fail closed（零 provider 调用）；
#      Project.native_av settings round-trip + 旧项目兼容 + HTTP 设置面。
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))

import deps
from src.core.models import NativeAVSettings, Project
from src.core.store import ProjectStore
from src.core.prompt_builder import build_video_prompt
from src.core.write_lock import _clear_for_test
from src.generation.dialogue_control import (
    DialogueControlError,
    resolve_dialogue_language,
)
from src.generation.image_adapter import (
    shot_has_binding,
    shot_has_visual_production_binding,
)
from src.shot_binding.models import (
    DialogueSpeakerBinding,
    DialogueSpeakerLine,
    ShotProductionBinding,
)
from src.shot_binding.store import ShotBindingStore
from routers.shots import _handler_video
from test_shot_binding_s3b1 import make_pdir               # noqa: E402
from test_shot_binding_s3c2 import (                        # noqa: E402
    FakeMediaClient,
    _load_project,
    _seed_first_frame,
    _set_video_ready,
)

LANG = "Mandarin Chinese"


class _StubPM:
    def __init__(self, pdir):
        self._pdir = pdir

    def get_project_dir(self, name):
        return self._pdir


@pytest.fixture()
def venv(tmp_path, monkeypatch):
    _clear_for_test()
    pdir = make_pdir(tmp_path)
    fake = FakeMediaClient()
    monkeypatch.setattr(deps, "project_manager", _StubPM(pdir))
    monkeypatch.setattr(deps, "agnes_client", fake)
    yield pdir, fake
    _clear_for_test()


def _set_dialogue(pdir, shot_id="s01_c01", dialogue="", characters=None):
    def mut(s):
        if dialogue:
            s.dialogue = dialogue
        if characters:
            s.characters = characters
    ProjectStore(pdir).update_shot(shot_id, mut)


def _set_project_language(pdir, lang):
    p = ProjectStore(pdir).load()
    p.native_av = NativeAVSettings(spoken_language=lang)
    ProjectStore(pdir).save(p)


def _save_dialogue_only_binding(pdir, shot_id="s01_c01", characters=None):
    """§3 反例构造：只含 dialogue_speaker_binding 的 binding（无任何视觉生产内容）。"""
    lines = []
    for i, name in enumerate(characters or []):
        lines.append(DialogueSpeakerLine(index=i, speaker_character_name=name))
    doc = ShotBindingStore(pdir).load()
    doc.bindings[shot_id] = ShotProductionBinding(
        shot_id=shot_id,
        dialogue_speaker_binding=DialogueSpeakerBinding(lines=lines,
                                                        resolution="explicit"),
        production_notes="speaker-only metadata")
    ShotBindingStore(pdir).save(doc)


def _run_video(pdir, shot_id="s01_c01", params=None):
    async def emit(*a, **k):
        pass
    job = SimpleNamespace(project=pdir.name, shot_id=shot_id,
                          params=params or {}, kind="video")
    asyncio.run(_handler_video(job, emit))


# ============ §3 routing predicate ============

def test_dialogue_only_binding_is_metadata_not_visual_routing(venv):
    pdir, _fake = venv
    _save_dialogue_only_binding(pdir, characters=["Alice"])
    assert shot_has_binding(pdir, "s01_c01"), "binding 元数据存在（key-presence 语义保留）"
    assert not shot_has_visual_production_binding(pdir, "s01_c01"), \
        "18.md §3：dialogue metadata ≠ visual routing predicate"


def test_dialogue_only_binding_video_routes_legacy_and_succeeds(venv):
    """§3 主证：仅 dialogue binding 的 shot 走视频 → legacy/UNBOUND 路径成功
    （旧行为：路由进 BOUND 链被 binding_not_ready 拦死）。"""
    pdir, fake = venv
    _seed_first_frame(pdir)
    _set_video_ready(pdir)
    _save_dialogue_only_binding(pdir, characters=["Alice"])
    _set_dialogue(pdir, dialogue="我们现在出发。")
    _set_project_language(pdir, LANG)
    _run_video(pdir)                                   # 默认 auto-on
    assert len(fake.video_calls) == 1, "legacy 路径跑通，未被 BOUND 链拦截"
    prompt = fake.video_calls[0]["prompt"]
    assert "1. Alice says: 我们现在出发。" in prompt


def test_visual_binding_still_routes_bound(tmp_path):
    """有视觉生产内容（scene_binding 视觉字段）→ visual predicate True（BOUND）。"""
    from src.shot_binding.models import SceneBinding
    _clear_for_test()
    try:
        pdir = make_pdir(tmp_path)
        doc = ShotBindingStore(pdir).load()
        doc.bindings["s01_c01"] = ShotProductionBinding(
            shot_id="s01_c01",
            scene_binding=SceneBinding(scene_instance_id="si_x",
                                       location_view_id="lv_y"))
        ShotBindingStore(pdir).save(doc)
        assert shot_has_visual_production_binding(pdir, "s01_c01")
    finally:
        _clear_for_test()


def test_notes_only_binding_not_visual(venv):
    pdir, _fake = venv
    doc = ShotBindingStore(pdir).load()
    doc.bindings["s01_c01"] = ShotProductionBinding(shot_id="s01_c01",
                                                    production_notes="notes only")
    ShotBindingStore(pdir).save(doc)
    assert shot_has_binding(pdir, "s01_c01")
    assert not shot_has_visual_production_binding(pdir, "s01_c01")


def test_no_binding_not_visual(venv):
    pdir, _fake = venv
    assert not shot_has_binding(pdir, "s01_c01")
    assert not shot_has_visual_production_binding(pdir, "s01_c01")


# ============ §4 default-on ============

def test_default_on_dialogue_compiled_without_params(venv):
    """§4：dialogue 非空 → 默认启用（无需 ?native_dialogue_control=true）。"""
    pdir, fake = venv
    _seed_first_frame(pdir)
    _set_video_ready(pdir)
    _set_dialogue(pdir, dialogue="我们现在出发。")
    _set_project_language(pdir, LANG)
    _run_video(pdir, params={"video_model": None})     # 不带 ndc 参数
    prompt = fake.video_calls[0]["prompt"]
    assert "1. Alice says: 我们现在出发。" in prompt
    assert LANG in prompt


def test_default_on_no_dialogue_unchanged(venv):
    """§4：无 dialogue → 当前 visual-only 行为逐字节不变（language 缺失也不阻塞）。"""
    pdir, fake = venv
    _seed_first_frame(pdir)
    _set_video_ready(pdir)
    _set_dialogue(pdir, dialogue="")
    _run_video(pdir)                                   # 无任何参数、项目无语言设置
    project = _load_project(pdir)
    assert fake.video_calls[0]["prompt"] == build_video_prompt(
        project.get_shot("s01_c01"), "i2v")


def test_explicit_false_override_off(venv):
    """§4：显式 False = debug/compat override——有台词+项目语言齐备也不编译。"""
    pdir, fake = venv
    _seed_first_frame(pdir)
    _set_video_ready(pdir)
    _set_dialogue(pdir, dialogue="我们现在出发。")
    _set_project_language(pdir, LANG)
    _run_video(pdir, params={"native_dialogue_control": False})
    project = _load_project(pdir)
    assert fake.video_calls[0]["prompt"] == build_video_prompt(
        project.get_shot("s01_c01"), "i2v")
    assert "我们现在出发" not in fake.video_calls[0]["prompt"]


# ============ §5 language production policy ============

def test_language_from_project_settings(venv):
    pdir, fake = venv
    _seed_first_frame(pdir)
    _set_video_ready(pdir)
    _set_dialogue(pdir, dialogue="我们现在出发。")
    _set_project_language(pdir, LANG)
    _run_video(pdir)
    assert f"spoken dialogue in {LANG}" in fake.video_calls[0]["prompt"]


def test_language_request_override_wins(venv):
    pdir, fake = venv
    _seed_first_frame(pdir)
    _set_video_ready(pdir)
    _set_dialogue(pdir, dialogue="我们现在出发。")
    _set_project_language(pdir, LANG)
    _run_video(pdir, params={"native_dialogue_language": "Cantonese Chinese"})
    prompt = fake.video_calls[0]["prompt"]
    assert "Cantonese Chinese" in prompt
    assert LANG not in prompt


def test_language_missing_fail_closed_zero_provider_calls(venv):
    """§5：有台词 + 皆缺 → language_missing fail closed（绝不恢复让 Agnes 猜）。"""
    pdir, fake = venv
    _seed_first_frame(pdir)
    _set_video_ready(pdir)
    _set_dialogue(pdir, dialogue="我们现在出发。")
    with pytest.raises(DialogueControlError) as ei:
        _run_video(pdir)
    assert ei.value.code == "language_missing"
    assert fake.video_calls == [], "fail closed → provider 调用数 = 0"


def test_no_dialogue_needs_no_language(venv):
    pdir, fake = venv
    _seed_first_frame(pdir)
    _set_video_ready(pdir)
    _set_dialogue(pdir, dialogue="")                   # 无台词：不要求语言
    _run_video(pdir)
    assert len(fake.video_calls) == 1


def test_resolve_dialogue_language_precedence_unit():
    p = Project(name="t")
    assert resolve_dialogue_language(p, "X") == "X"        # override 优先
    p.native_av = NativeAVSettings(spoken_language="Y")
    assert resolve_dialogue_language(p, "X") == "X"
    assert resolve_dialogue_language(p, None) == "Y"
    with pytest.raises(DialogueControlError) as ei:
        resolve_dialogue_language(Project(name="t"), None)
    assert ei.value.code == "language_missing"


# ============ §5 Project.native_av settings round-trip / 兼容 ============

def test_project_native_av_settings_roundtrip():
    p = Project(name="t")
    p.native_av = NativeAVSettings(spoken_language="Mandarin Chinese")
    d = p.to_dict()
    assert d["native_av"] == {"spoken_language": "Mandarin Chinese"}
    p2 = Project.from_dict(d)
    assert p2.native_av.spoken_language == "Mandarin Chinese"


def test_project_old_json_without_native_av_loads():
    """旧 project.json（无 native_av 键）→ 空 settings，不炸（§22 legacy 兼容同口径）。"""
    old = {"name": "legacy", "schema_version": 2, "scenes": []}
    p = Project.from_dict(old)
    assert p.native_av == NativeAVSettings()
    assert p.native_av.spoken_language == ""


def test_project_native_av_unknown_fields_filtered():
    p = Project.from_dict({"name": "t", "native_av": {"spoken_language": "Y",
                                                      "future_key": 1}})
    assert p.native_av.spoken_language == "Y"


# ============ §5 HTTP 设置面 ============

def test_native_av_settings_http_roundtrip(venv):
    from routers.projects import get_native_av_settings, update_native_av_settings
    pdir, _fake = venv
    got = asyncio.run(get_native_av_settings(pdir.name))
    assert got["native_av"] == {"spoken_language": ""}
    put = asyncio.run(update_native_av_settings(
        pdir.name, {"spoken_language": f"  {LANG}  "}))
    assert put == {"ok": True, "native_av": {"spoken_language": LANG}}
    got2 = asyncio.run(get_native_av_settings(pdir.name))
    assert got2["native_av"]["spoken_language"] == LANG
    p = _load_project(pdir)
    assert p.native_av.spoken_language == LANG
