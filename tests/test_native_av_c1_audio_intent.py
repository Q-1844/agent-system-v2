# tests/test_native_av_c1_audio_intent.py
# Native AV C1 — Commit C：Shot-local Audio Event Intent（18.md §16/§17/§18）。
#
# 覆盖：
#   §16 最小结构：music_policy（none|allow_native）+ directives[]；挂在
#     ShotProductionBinding；binding_revision 随之翻动（race guard 自动覆盖）；
#   §17 默认策略：dialogue → none；无台词 → allow_native；显式 allow_native
#     抑制 no-BGM 语句；none → no-BGM 语句；
#   §18 意图语义：directives 确定性进 prompt 的 shot-local sound 区；
#   写入面：service create/replace payload 解析（fail closed）；
#   禁止面：不出现 SFXAsset/Timeline 等（§16 明确不做）。
import sys
from pathlib import Path

import pytest

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))

from src.core.models import NativeAVSettings, Project, Shot
from src.generation.av_prompt_compiler import compile_native_av_prompt
from src.generation.native_av_pack import (
    NativeAVError,
    build_native_av_pack,
    read_audio_intent,
)
from src.shot_binding.models import (
    AudioEventIntent,
    ShotProductionBinding,
    binding_revision,
)
from src.shot_binding.service import ShotBindingService

LANG = "Mandarin Chinese"


def _shot(dialogue="", characters=None, shot_id="s01_c01"):
    return Shot(shot_id=shot_id, scene_id="s01", index=1,
                dialogue=dialogue, characters=characters or [])


def _project(lang=LANG):
    p = Project(name="t")
    if lang:
        p.native_av = NativeAVSettings(spoken_language=lang)
    return p


# ============ 模型 round-trip / revision（§16）============

def test_audio_event_intent_roundtrip():
    b = ShotProductionBinding(
        shot_id="s01_c01",
        audio_event_intent=AudioEventIntent(
            music_policy="none",
            directives=["rusty door creaks as it opens", "soft rain outside"]))
    d = b.to_dict()
    assert d["audio_event_intent"] == {
        "music_policy": "none",
        "directives": ["rusty door creaks as it opens", "soft rain outside"]}
    b2 = ShotProductionBinding.from_dict(d)
    assert b2.audio_event_intent == b.audio_event_intent


def test_audio_event_intent_old_json_without_block():
    old = {"shot_id": "s01_c01", "scene_id": "s01", "production_notes": ""}
    assert ShotProductionBinding.from_dict(old).audio_event_intent is None


def test_audio_event_intent_flips_binding_revision():
    b1 = ShotProductionBinding(shot_id="s01_c01", updated_at=1.0)
    b2 = ShotProductionBinding(shot_id="s01_c01", updated_at=1.0,
                               audio_event_intent=AudioEventIntent())
    assert binding_revision(b1) != binding_revision(b2), \
        "audio_event_intent 变化必须翻动 binding_revision（§27 race guard 自动覆盖）"
    b3 = ShotProductionBinding(shot_id="s01_c01", updated_at=1.0,
                               audio_event_intent=AudioEventIntent(
                                   music_policy="allow_native"))
    assert binding_revision(b2) != binding_revision(b3), "music_policy 变化翻动 revision"


# ============ read_audio_intent（§16/§17 校验与默认）============

def test_read_audio_intent_binding_block_valid():
    shot = _shot(dialogue="别过去。", characters=["林晚星"])
    intent = read_audio_intent(None, shot, has_spoken_lines=True)
    assert intent == {"music_policy": "none", "directives": [], "source": "default"}


def test_read_audio_intent_invalid_policy_fail_closed():
    shot = _shot(dialogue="别过去。", characters=["林晚星"])
    import tempfile
    from pathlib import Path as P
    from src.shot_binding.models import ShotBindingDocument
    from src.shot_binding.store import ShotBindingStore
    from test_shot_binding_s3b1 import make_pdir
    with tempfile.TemporaryDirectory() as td:
        pdir = make_pdir(P(td))
        doc = ShotBindingDocument(bindings={
            "s01_c01": ShotProductionBinding(
                shot_id="s01_c01",
                audio_event_intent=AudioEventIntent(music_policy="epic_orchestral"))})
        ShotBindingStore(pdir).save(doc)
        with pytest.raises(NativeAVError) as ei:
            read_audio_intent(pdir, shot, has_spoken_lines=True)
        assert ei.value.code == "invalid_audio_intent"


def test_read_audio_intent_empty_directive_fail_closed():
    shot = _shot(shot_id="s01_c01")
    import tempfile
    from pathlib import Path as P
    from src.shot_binding.models import ShotBindingDocument
    from src.shot_binding.store import ShotBindingStore
    from test_shot_binding_s3b1 import make_pdir
    with tempfile.TemporaryDirectory() as td:
        pdir = make_pdir(P(td))
        doc = ShotBindingDocument(bindings={
            "s01_c01": ShotProductionBinding(
                shot_id="s01_c01",
                audio_event_intent=AudioEventIntent(directives=["ok", "  "]))})
        ShotBindingStore(pdir).save(doc)
        with pytest.raises(NativeAVError) as ei:
            read_audio_intent(pdir, shot, has_spoken_lines=False)
        assert ei.value.code == "invalid_audio_intent"


# ============ prompt 集成（§17/§18）============

def _bound_pack_with_intent(shot, pdir):
    """带 audio_event_intent 的 binding 落盘 → pack 读取（真实 store 路径）。"""
    from src.shot_binding.models import ShotBindingDocument
    from src.shot_binding.store import ShotBindingStore
    doc = ShotBindingDocument(bindings={
        "s01_c01": ShotProductionBinding(
            shot_id="s01_c01",
            audio_event_intent=AudioEventIntent(
                music_policy="none",
                directives=["rusty door creaks as it opens",
                            "light footsteps on wood"]))})
    ShotBindingStore(pdir).save(doc)
    return build_native_av_pack(
        pdir, _project(), shot,
        visual_route="bound",
        visual_basis={"route": "bound", "binding_revision": "x",
                      "binding_readiness": "READY",
                      "resolved_scene_instance_view_id": None},
        provider_options={"mode": "i2v"}), pdir


def test_directives_render_into_sound_zone_deterministic(tmp_path):
    """§18：directives 是意图短句——确定性进 shot-local sound 区，不是时间线。"""
    from test_shot_binding_s3b1 import make_pdir
    shot = _shot(dialogue="顾岩：我必须去。", characters=["顾岩"])
    pack, _pdir = _bound_pack_with_intent(shot, make_pdir(tmp_path))
    out1 = compile_native_av_prompt(pack, "V")
    out2 = compile_native_av_prompt(pack, "V")
    assert out1 == out2
    assert "No background music or musical score" in out1, "music none → no-BGM 语句"
    assert ("Shot-local sound (generate natively within the scene, not as separate "
            "audio): rusty door creaks as it opens; light footsteps on wood.") in out1
    blob = out1.lower()
    assert ".wav" not in blob and "00:0" not in blob, "绝不出现时间线/资产引用（§18）"


def test_allow_native_explicit_suppresses_no_bgm_line(tmp_path):
    """§17：allow_native 是显式 opt-in（非默认）——抑制 no-BGM 语句。"""
    from src.shot_binding.models import ShotBindingDocument
    from src.shot_binding.store import ShotBindingStore
    from test_shot_binding_s3b1 import make_pdir
    shot = _shot(dialogue="顾岩：我必须去。", characters=["顾岩"])
    pdir = make_pdir(tmp_path)
    doc = ShotBindingDocument(bindings={
        "s01_c01": ShotProductionBinding(
            shot_id="s01_c01",
            audio_event_intent=AudioEventIntent(music_policy="allow_native"))})
    ShotBindingStore(pdir).save(doc)
    pack = build_native_av_pack(
        pdir, _project(), shot,
        visual_route="bound",
        visual_basis={"route": "bound", "binding_revision": "x",
                      "binding_readiness": "READY",
                      "resolved_scene_instance_view_id": None},
        provider_options={"mode": "i2v"})
    out = compile_native_av_prompt(pack, "V")
    assert "No background music or musical score" not in out
    assert "Shot-local sound" not in out
    assert "1. 顾岩 says: 我必须去。" in out


def test_no_dialogue_default_intent_keeps_prompt_unchanged():
    """§4/§17：无台词 + 无显式 intent → allow_native → prompt 逐字节不变。"""
    shot = _shot(dialogue="")
    pack = build_native_av_pack(
        None, _project(), shot,
        visual_route="unbound",
        visual_basis={"route": "unbound", "first_frame": None},
        provider_options={"mode": "i2v"})
    assert compile_native_av_prompt(pack, "V") == "V"


def test_no_sfx_asset_types_created():
    """§16 禁止面：不创建 SFXAsset/SFXVersion/AudioStem/AudioTimeline。"""
    import src.core.models as cm
    for forbidden in ("SFXAsset", "SFXVersion", "AudioStem", "AudioTimeline",
                      "AmbienceVersion"):
        assert not hasattr(cm, forbidden), f"18.md §16 禁止创建 {forbidden}"


# ============ 写入面：service payload 解析（§16/§9 productionize）============

LOOK = "look_alice000001"     # make_pdir 预置的 approved Look


def _make_service_pdir(tmp_path):
    from test_shot_binding_s3b1 import make_pdir
    return make_pdir(tmp_path)


def _payload(**extra):
    base = {"shot_id": "s01_c01", "scene_id": "s01",
            "character_bindings": [{"character_name": "Alice", "look_id": LOOK}]}
    base.update(extra)
    return base


def test_service_payload_parses_audio_intent_and_speaker_binding(tmp_path):
    pdir = _make_service_pdir(tmp_path)
    svc = ShotBindingService(pdir, pdir.name)
    receipt = svc.create_binding(_payload(
        dialogue_speaker_binding={"lines": [{"index": 0,
                                             "speaker_character_name": "Alice"}],
                                  "resolution": "explicit"},
        audio_event_intent={"music_policy": "none",
                            "directives": ["soft rain outside the window"]}))
    assert receipt["ok"] is True
    b = receipt["binding"]
    assert b["audio_event_intent"] == {"music_policy": "none",
                                       "directives": ["soft rain outside the window"]}
    assert b["dialogue_speaker_binding"]["lines"] == [
        {"index": 0, "speaker_character_name": "Alice"}]


def test_service_payload_invalid_music_policy_fail_closed(tmp_path):
    pdir = _make_service_pdir(tmp_path)
    svc = ShotBindingService(pdir, pdir.name)
    receipt = svc.create_binding(_payload(
        audio_event_intent={"music_policy": "epic"}))
    assert receipt["ok"] is False, "fail closed：非法 music_policy 拒绝写入"
    assert receipt["error"] == "invalid_audio_intent"


def test_service_payload_invalid_speaker_line_fail_closed(tmp_path):
    pdir = _make_service_pdir(tmp_path)
    svc = ShotBindingService(pdir, pdir.name)
    receipt = svc.create_binding(_payload(
        dialogue_speaker_binding={"lines": [{"index": "zero",
                                             "speaker_character_name": "Alice"}]}))
    assert receipt["ok"] is False, "fail closed：非法 index 拒绝写入"
    assert receipt["error"] == "invalid_binding"


def test_service_replace_updates_audio_intent(tmp_path):
    pdir = _make_service_pdir(tmp_path)
    svc = ShotBindingService(pdir, pdir.name)
    r1 = svc.create_binding(_payload())
    rev1 = r1["binding_revision"]
    r2 = svc.replace_binding("s01_c01", _payload(
        audio_event_intent={"music_policy": "allow_native"}),
        expected_binding_revision=rev1)
    assert r2["ok"] is True
    assert r2["binding"]["audio_event_intent"]["music_policy"] == "allow_native"
    assert r2["binding_revision"] != rev1
