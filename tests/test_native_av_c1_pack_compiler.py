# tests/test_native_av_c1_pack_compiler.py
# Native AV C1-R — NativeAVGenerationPack（21.md §6/§7/§8/§17）
# + AV Prompt Compiler（21.md §18：只保留 B1 已验证 Dialogue/Speaker/Language 控制；
#   AudioEventIntent/music_policy 已按 §2/§20/§21 撤回）。
import sys
from pathlib import Path

import pytest

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))

from src.core.models import NativeAVSettings, Project, Shot
from src.generation.av_prompt_compiler import compile_native_av_prompt
from src.generation.native_av_pack import (
    NATIVE_AV_GENERATION_BASIS_SCHEMA,
    NATIVE_AV_GENERATION_PACK_SCHEMA,
    NATIVE_AV_DEPENDENCY_KEYS,
    NativeAVGenerationPack,
    build_native_av_pack,
    bound_visual_basis,
    native_av_freshness,
    unbound_visual_basis,
)

LANG = "Mandarin Chinese"


def _shot(dialogue="", characters=None, shot_id="s01_c01", **kw):
    return Shot(shot_id=shot_id, scene_id="s01", index=1,
                dialogue=dialogue, characters=characters or [], **kw)


def _project(lang=LANG):
    p = Project(name="t")
    if lang:
        p.native_av = NativeAVSettings(spoken_language=lang)
    return p


BOUND_BASIS = {"binding_revision": "abcd1234", "binding_readiness": "READY",
               "resolved_scene_instance_view_id": "siv_9", "extra": "ignored"}


def _bound_pack(shot, project=None):
    return build_native_av_pack(None, project or _project(), shot,
                                visual_route="bound",
                                visual_basis=bound_visual_basis(BOUND_BASIS),
                                provider_options={"mode": "i2v", "model": "m1",
                                                  "num_frames": 81})


def _unbound_pack(shot, project=None, mode="i2v"):
    return build_native_av_pack(None, project or _project(), shot,
                                visual_route="unbound",
                                visual_basis=unbound_visual_basis(shot, provider_mode=mode),
                                provider_options={"mode": mode, "model": "m1"})


# ============ pack（§6/§23）============

def test_pack_schema_and_determinism():
    shot = _shot(dialogue="林晚星：别过去。", characters=["林晚星"])
    p1 = _bound_pack(shot)
    p2 = _bound_pack(shot)
    assert p1.schema == NATIVE_AV_GENERATION_PACK_SCHEMA
    assert p1.to_dict() == p2.to_dict(), "同 canonical → pack 确定性（§6）"
    assert p1.generation_basis == p2.generation_basis


def test_pack_dialogue_is_derived_slice_only():
    """§8：pack.lines[].exact_text 只是 Shot.dialogue 的 derived slice。"""
    shot = _shot(dialogue="林晚星：别过去。\n顾岩：我必须去。",
                 characters=["林晚星", "顾岩"])
    pack = _bound_pack(shot)
    d = pack.dialogue
    assert d["lines"][0] == {"index": 0, "exact_text": "别过去。",
                             "speaker_character_name": "林晚星",
                             "delivery_hints": []}
    assert d["silent_characters"] == []
    assert d["spoken_language"] == LANG
    assert d["dialogue_fingerprint"] and d["speaker_map_fingerprint"]
    assert d["dialogue_fingerprint"] != d["speaker_map_fingerprint"]


def test_pack_basis_has_all_dependency_and_provider_keys():
    shot = _shot(dialogue="别过去。", characters=["林晚星"])
    basis = _bound_pack(shot).generation_basis
    assert basis["schema"] == NATIVE_AV_GENERATION_BASIS_SCHEMA
    for k in ("dialogue_fingerprint", "speaker_map_fingerprint", "spoken_language",
              "visual_basis_fingerprint", "prompt_compiler_schema"):
        assert k in basis, f"§11 依赖键缺失: {k}"
    for k in ("provider", "model", "provider_mode", "provider_options_fingerprint"):
        assert k in basis, f"§11 provider 事实键缺失: {k}"
    # 撤回键不得再出现（21.md §2/§20/§21）
    for k in ("audio_event_intent_fingerprint", "music_policy"):
        assert k not in basis, f"撤回键残留: {k}"
    assert basis["provider"] == "agnes"
    assert basis["model"] == "m1"
    assert basis["prompt_compiler_schema"] == "native_av_prompt_v3"
    assert set(NATIVE_AV_DEPENDENCY_KEYS) == {
        "dialogue_fingerprint", "speaker_map_fingerprint", "spoken_language",
        "visual_basis_fingerprint", "prompt_compiler_schema",
        "provider", "model", "provider_mode", "provider_options_fingerprint",
        "performance_intent_fingerprint", "video_prompt_creative_fingerprint"}


def test_pack_basis_provider_keys_track_options_change():
    """21.md §14：provider/model/options ∈ STALE 触发集——options 变 →
    依赖键随之变（final 诚实暴露「换模型/换参数后与当前事实不一致」）。"""
    shot = _shot(dialogue="别过去。", characters=["林晚星"])
    p1 = build_native_av_pack(None, _project(), shot, visual_route="bound",
                              visual_basis=bound_visual_basis(BOUND_BASIS),
                              provider_options={"mode": "i2v", "model": "m1"})
    p2 = build_native_av_pack(None, _project(), shot, visual_route="bound",
                              visual_basis=bound_visual_basis(BOUND_BASIS),
                              provider_options={"mode": "i2v", "model": "m2"})
    assert p1.generation_basis["model"] == "m1"
    assert p2.generation_basis["model"] == "m2"
    assert p1.generation_basis["provider_options_fingerprint"] != \
        p2.generation_basis["provider_options_fingerprint"]


def test_pack_no_dialogue_fields_and_defaults():
    """无台词：dialogue=None；不触发 language/speaker 解析（21.md §4）。"""
    shot = _shot(dialogue="")
    pack = _unbound_pack(shot)
    assert pack.dialogue is None
    b = pack.generation_basis
    assert b["dialogue_fingerprint"] is None
    assert b["spoken_language"] is None
    assert "audio_intent" not in pack.to_dict(), "撤回字段不得残留（21.md §20）"


def test_unbound_vs_bound_visual_basis_differ():
    shot = _shot(dialogue="别过去。", characters=["林晚星"])
    b_bound = _bound_pack(shot).generation_basis
    b_unbound = _unbound_pack(shot).generation_basis
    assert b_bound["visual_basis_fingerprint"] != b_unbound["visual_basis_fingerprint"]
    assert _bound_pack(shot).visual["route"] == "bound"
    assert _unbound_pack(shot).visual["route"] == "unbound"


def test_unbound_visual_basis_records_frame_identity():
    shot = _shot(dialogue="别过去。", characters=["林晚星"],
                 selected_first_frame="cand_1", selected_last_frame="cand_2",
                 link_mode="keyframes")
    vb = unbound_visual_basis(shot, provider_mode="keyframes")
    assert vb == {"route": "unbound", "first_frame": "cand_1",
                  "last_frame": "cand_2"}
    vb2 = unbound_visual_basis(shot, provider_mode="i2v")
    assert "last_frame" not in vb2, "i2v 不消费尾帧——不是 actual dependency"


# ============ compiler（§10-§15/§17）============

def test_compiler_visual_prompt_verbatim_first():
    shot = _shot(dialogue="林晚星：别过去。", characters=["林晚星"])
    pack = _bound_pack(shot)
    visual = "Animate this image: she turns, keep identity consistent"
    out = compile_native_av_prompt(pack, visual)
    assert out.startswith(visual), "§15：visual prompt 原样在前，不重排不发明动作"
    assert "1. 林晚星 says: 别过去。" in out


def test_compiler_red_line_only_dialogue_spoken():
    """§12 红线一：只有台词可以被说出来（对 B1「念 motion prompt」事故的
    compiler 级 invariant）。"""
    shot = _shot(dialogue="别过去。", characters=["林晚星"])
    out = compile_native_av_prompt(_bound_pack(shot), "V")
    assert "Only the explicitly quoted dialogue is spoken aloud" in out
    assert "do not speak, narrate, or read any visual description" in out


def test_compiler_red_line_exact_dialogue_no_rewrite():
    """§13 红线二：台词不得改写（不翻译/不润色/不补句），保留自然表演。"""
    shot = _shot(dialogue="别过去。", characters=["林晚星"])
    out = compile_native_av_prompt(_bound_pack(shot), "V")
    assert "Speak exactly the supplied dialogue" in out
    assert "do not translate, paraphrase, summarize" in out
    assert "non-verbal vocalizations are allowed" in out


def test_compiler_speaker_assignment_and_swap_guard():
    shot = _shot(dialogue="林晚星：「别进去。」\n顾岩：「我必须去。」\n林晚星：「快。」",
                 characters=["林晚星", "顾岩"])
    out = compile_native_av_prompt(_bound_pack(shot), "V")
    assert "1. 林晚星 says: 「别进去。」" in out
    assert "2. 顾岩 replies: 「我必须去。」" in out
    assert "3. 林晚星 replies: 「快。」" in out
    assert "Do not swap the speakers" in out
    assert "remains completely silent" not in out


def test_compiler_silent_character_declared():
    shot = _shot(dialogue="林晚星：别过去。", characters=["林晚星", "顾岩"])
    out = compile_native_av_prompt(_bound_pack(shot), "V")
    assert "顾岩 remains completely silent in this shot" in out


def test_compiler_stage_directions_in_non_spoken_zone():
    """§7/§11：整行指示与语气提示进 non-spoken 区并标注 not spoken，绝不进台词。"""
    shot = _shot(dialogue="（林晚星转身看向门口）\n林晚星：（低声）别过去。",
                 characters=["林晚星", "顾岩"])
    out = compile_native_av_prompt(_bound_pack(shot), "V")
    assert "Stage direction (not spoken, do not read aloud): 林晚星转身看向门口" in out
    assert "林晚星 delivery direction (not spoken): 低声" in out
    assert "1. 林晚星 says: 别过去。" in out
    assert out.index("Stage direction") < out.index("On-screen spoken dialogue"), \
        "§11 zone 顺序：non-spoken 在 spoken dialogue 之前"
    # 红线一必须在台词行之前（先约束后引用）
    assert out.index("Only the explicitly quoted dialogue") < out.index("别过去。")


def test_compiler_no_sound_instructions_ever():
    """21.md §18/§21：撤回后 prompt 不含任何声音/BGM 指令（留给 C2 实验）。
    （红线一里的 "sound-effect instruction" 是禁读清单词条，不是声音指令。）"""
    shot = _shot(dialogue="别过去。", characters=["林晚星"])
    out = compile_native_av_prompt(_bound_pack(shot), "V")
    assert "music" not in out.lower()
    assert "Shot-local sound" not in out


def test_compiler_no_zones_when_no_dialogue():
    """21.md §4：无 dialogue → prompt 逐字节不变。"""
    shot = _shot(dialogue="")
    visual = "Animate this image: subtle motion"
    assert compile_native_av_prompt(_unbound_pack(shot), visual) == visual


def test_compiler_deterministic():
    shot = _shot(dialogue="林晚星：别过去。", characters=["林晚星", "顾岩"])
    p = _bound_pack(shot)
    assert compile_native_av_prompt(p, "V") == compile_native_av_prompt(p, "V")


def test_compiler_requires_pack_type():
    with pytest.raises(TypeError):
        compile_native_av_prompt({"dialogue": None}, "V")


# ============ 21.md §17 两条路径共用同一 compiler ============

def test_bound_and_unbound_share_dialogue_zone():
    """BOUND / UNBOUND 差别只能来自 visual input source——dialogue 区逐字节一致。"""
    shot = _shot(dialogue="林晚星：别过去。", characters=["林晚星"])
    bound_out = compile_native_av_prompt(_bound_pack(shot), "BOUND_VISUAL")
    unbound_out = compile_native_av_prompt(_unbound_pack(shot), "UNBOUND_VISUAL")
    tail_b = bound_out[len("BOUND_VISUAL"):]
    tail_u = unbound_out[len("UNBOUND_VISUAL"):]
    assert tail_b == tail_u, "两条路径的 AV 语义区必须逐字节一致（21.md §17）"


# ============ freshness 读模型（21.md §14/§15 前置冒烟）============

def test_freshness_missing_without_final_video():
    shot = _shot(dialogue="别过去。", characters=["林晚星"])
    r = native_av_freshness(None, _project(), shot)
    assert r["status"] == "missing"
