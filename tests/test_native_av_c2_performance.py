# tests/test_native_av_c2_performance.py
# Native AV C2 — Shot Performance & Visual Consistency（22.md §23 自动测试重点）。
# 只保护真实新增能力：
#   action order preserved / no invented action
#   continuity constraint deterministic / unrelated prop 不入 prompt
#   performance intent deterministic / 空 intent 零改动
#   dialogue/speaker/language 输出不变
#   BOUND/UNBOUND 共用 compiler
#   schema v3 → 正确 STALE；无关 canonical 变化不误报
import sys
from pathlib import Path

import pytest

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))

from src.core.models import (                       # noqa: E402
    NativeAVSettings, PerformanceIntent, Project, Shot)
from src.core.prompt_builder import build_video_prompt   # noqa: E402
from src.generation.av_prompt_compiler import compile_native_av_prompt  # noqa: E402
from src.generation.image_adapter import _production_context_text  # noqa: E402
from src.generation.native_av_pack import (         # noqa: E402
    NATIVE_AV_DEPENDENCY_KEYS,
    NATIVE_AV_PROMPT_SCHEMA,
    build_native_av_pack,
    bound_visual_basis,
    compute_current_dependency_keys,
    native_av_freshness,
    unbound_visual_basis,
)

LANG = "Mandarin Chinese"
ORDER_FRAME = "The actions must unfold in this exact order:"


def _shot(dialogue="", characters=None, shot_id="s01_c01", **kw):
    return Shot(shot_id=shot_id, scene_id="s01", index=1,
                dialogue=dialogue, characters=characters or [], **kw)


def _project(lang=LANG):
    p = Project(name="t")
    if lang:
        p.native_av = NativeAVSettings(spoken_language=lang)
    return p


def _pack(shot, performance=None):
    """直接构造 pack（performance 覆盖注入——pack.performance 是派生投影）。"""
    from dataclasses import replace
    base = build_native_av_pack(
        None, _project(), shot, visual_route="unbound",
        visual_basis=unbound_visual_basis(shot, provider_mode="i2v"),
        provider_options={"mode": "i2v", "model": "m1"})
    return replace(base, performance=performance)


# ============ §6/§7/§8 Action Sequence Control ============

SEQ = "1. 林晚星沿梯架向上攀爬 2. 爬到中段回头看向顾岩 3. 顾岩在下方扶稳梯架"


def test_action_order_preserved_i2v():
    """§7：canonical 顺序 verbatim 保留 + 明确时序框架。"""
    shot = _shot(motion="攀爬", action_sequence=SEQ, characters=["林晚星"])
    out = build_video_prompt(shot, "i2v")
    assert ORDER_FRAME in out
    assert SEQ in out, "顺序文本必须逐字保留（不弱化不改写）"
    assert out.index(ORDER_FRAME) < out.index(SEQ) < out.index("keep character identity")


def test_action_order_preserved_keyframes():
    shot = _shot(motion="攀爬", action_sequence=SEQ, characters=["林晚星"])
    out = build_video_prompt(shot, "keyframes")
    assert ORDER_FRAME in out and SEQ in out


def test_no_invented_action_when_sequence_absent():
    """§8：canonical 未表达顺序 → 不得发明任何顺序/动作文本。"""
    shot = _shot(motion="林晚星攀在梯架上回头")
    for mode in ("i2v", "keyframes"):
        out = build_video_prompt(shot, mode)
        assert ORDER_FRAME not in out
        assert "exact order" not in out


def test_no_invented_action_from_compiler():
    """§8：compiler 不发明动作——dialogue 区/表演区只含 canonical 事实。"""
    shot = _shot(dialogue="林晚星：别过去。", characters=["林晚星"])
    out = compile_native_av_prompt(_pack(shot), "V")
    tail = out[len("V"):]
    assert "walks to" not in tail and "turns her head" not in tail, \
        "compiler 不得添加 canonical 之外的动作动词"


# ============ §9-§11 镜内连续性（production context 编译）============

def _pack_with_props(action_note="", direct_props=None, covered_props=None):
    return {
        "scene": {"location_name": "Old Office"},
        "characters": [{"character_name": "Alice", "look_label": "Field Explorer",
                        "appearance_description": "auburn braided hair",
                        "covered_props": covered_props or []}],
        "props": direct_props or [],
        "action": {"kind": "pick_up", "subject_character": "Alice",
                   "prop_id": "prop_1",
                   "start_state": "Alice right hand empty",
                   "end_state": "Alice right hand holding the pistol",
                   "note": action_note},
    }


class _RDoc:
    props = {}


def test_continuity_compiled_for_video_target():
    """§9-§10：prop 关系/锚点/状态要求 + action.note 进 video prompt（此前丢弃）。"""
    pack = _pack_with_props(
        action_note="the pistol stays in the right hand, muzzle must not flip",
        direct_props=[{"prop_id": "prop_9", "name": "Lantern", "relation": "held",
                       "anchor": "left_hand",
                       "visual_state_note": "lantern stays lit",
                       "subject_character": "Alice"}],
        covered_props=[{"prop_id": "prop_1", "relation": "held",
                        "anchor": "right_hand",
                        "visual_state_note": "muzzle points forward"}])
    out = _production_context_text(pack, _RDoc(), "video")
    assert "Alice held prop_1 (right_hand)." in out, "covered prop 关系照旧编译"
    assert "Keep this consistent throughout the shot: muzzle points forward." in out, \
        "covered_prop.visual_state_note 此前被丢弃——现在编译（仅 video）"
    assert "Alice held Lantern (left_hand)." in out, "direct PropBinding 此前整体未编译"
    assert "Keep this consistent throughout the shot: lantern stays lit." in out
    assert "Important continuity: the pistol stays in the right hand, " \
           "muzzle must not flip." in out


def test_continuity_absent_for_first_last_targets():
    """连续性是镜内过程约束——first/last target 语义保持 C2 前逐字节不变。"""
    pack = _pack_with_props(
        action_note="keep the pistol oriented",
        direct_props=[{"prop_id": "p9", "name": "Lantern", "relation": "held",
                       "anchor": "left_hand", "visual_state_note": "lit",
                       "subject_character": "Alice"}],
        covered_props=[{"prop_id": "prop_1", "relation": "held",
                        "anchor": "right_hand", "visual_state_note": "muzzle forward"}])
    for target in ("first", "last"):
        out = _production_context_text(pack, _RDoc(), target)
        assert "Keep this consistent" not in out
        assert "Important continuity" not in out
        assert "Lantern" not in out, "direct props 不进 first/last（保持原行为）"
    # start/end state 行为不变
    first = _production_context_text(pack, _RDoc(), "first")
    assert "Action start state:" in first and "Action end state:" not in first
    last = _production_context_text(pack, _RDoc(), "last")
    assert "Action end state:" in last and "Action start state:" not in last


def test_unrelated_prop_not_in_prompt():
    """§23：pack 未含的道具绝不出现（actual dependencies only）。"""
    out = _production_context_text(_pack_with_props(), _RDoc(), "video")
    assert "Lantern" not in out and "Important continuity" not in out


def test_continuity_deterministic():
    pack = _pack_with_props(action_note="keep oriented")
    assert (_production_context_text(pack, _RDoc(), "video")
            == _production_context_text(pack, _RDoc(), "video"))


# ============ §12-§14 表演意图 ============

def test_performance_intent_deterministic_and_unified():
    """§14：统一表演指令——表情/动作/对白绑成同一场表演，在台词区之前。"""
    shot = _shot(dialogue="林晚星：别过去。", characters=["林晚星"])
    perf = {"emotion": "tense", "intensity": "high", "note": ""}
    out = compile_native_av_prompt(_pack(shot, performance=perf), "V")
    assert "Unified performance direction (emotional state: tense; intensity: high)" in out
    assert "one continuous, coherent performance" in out
    assert out.index("Unified performance direction") < out.index("On-screen spoken dialogue")


def test_performance_note_compiled():
    shot = _shot(dialogue="林晚星：别过去。", characters=["林晚星"])
    perf = {"emotion": "", "intensity": "", "note": "eyes widen before speaking"}
    out = compile_native_av_prompt(_pack(shot, performance=perf), "V")
    assert "Performance note: eyes widen before speaking" in out


def test_empty_performance_intent_no_change():
    """§23：空/缺省 intent → 输出与既有语义逐字节一致（dialogue 区不变）。"""
    shot = _shot(dialogue="林晚星：别过去。", characters=["林晚星"])
    baseline = compile_native_av_prompt(_pack(shot, performance=None), "V")
    for perf in (None, {}, {"emotion": "", "intensity": "", "note": ""}):
        assert compile_native_av_prompt(_pack(shot, performance=perf), "V") == baseline


def test_no_dialogue_with_performance_intent():
    """无台词 + 表演意图 → visual + 表演区（新能力）；无台词无 intent → 原样。"""
    shot = _shot(dialogue="")
    visual = "Animate this image: subtle motion"
    assert compile_native_av_prompt(_pack(shot, performance=None), visual) == visual
    out = compile_native_av_prompt(
        _pack(shot, performance={"emotion": "tense", "intensity": "high"}), visual)
    assert out.startswith(visual)
    assert "Unified performance direction" in out


def test_performance_from_shot_canonical_flows_to_pack():
    """Shot.performance_intent → pack.performance 派生（全空= None）。"""
    shot = _shot(performance_intent=PerformanceIntent(
        emotion="tense", intensity="high"))
    pack = build_native_av_pack(None, _project(), shot, visual_route="unbound",
                                visual_basis=unbound_visual_basis(shot, provider_mode="i2v"),
                                provider_options={"mode": "i2v"})
    assert pack.performance == {"emotion": "tense", "intensity": "high", "note": ""}
    empty = _shot(performance_intent=PerformanceIntent())
    pack2 = build_native_av_pack(None, _project(), empty, visual_route="unbound",
                                 visual_basis=unbound_visual_basis(empty, provider_mode="i2v"),
                                 provider_options={"mode": "i2v"})
    assert pack2.performance is None


# ============ §23 basis / freshness / 共用 compiler ============

def test_bound_unbound_share_compiler_with_performance():
    shot = _shot(dialogue="林晚星：别过去。", characters=["林晚星"])
    perf = {"emotion": "tense", "intensity": "high"}
    bound_out = compile_native_av_prompt(_pack(shot, performance=perf), "B")
    unbound_out = compile_native_av_prompt(_pack(shot, performance=perf), "U")
    assert bound_out[len("B"):] == unbound_out[len("U"):], "两路 AV 语义区逐字节一致"


def test_schema_v3_marks_old_versions_stale(tmp_path):
    """§23：compiler schema v2→v3 → 存量 basis 经既有机制诚实 STALE。"""
    shot = _shot(dialogue="别过去。", characters=["林晚星"])
    keys = compute_current_dependency_keys(tmp_path, _project(), shot)
    assert keys["prompt_compiler_schema"] == NATIVE_AV_PROMPT_SCHEMA == "native_av_prompt_v3"
    stale_keys = dict(keys, prompt_compiler_schema="native_av_prompt_v2")
    mismatched = [k for k in NATIVE_AV_DEPENDENCY_KEYS
                  if stale_keys.get(k) != keys.get(k)]
    assert mismatched == ["prompt_compiler_schema"]


def test_performance_intent_in_basis_and_dependency_keys(tmp_path):
    shot = _shot(dialogue="别过去。", characters=["林晚星"],
                 performance_intent=PerformanceIntent(emotion="tense"))
    pack = build_native_av_pack(None, _project(), shot, visual_route="unbound",
                                visual_basis=unbound_visual_basis(shot, provider_mode="i2v"),
                                provider_options={"mode": "i2v"})
    b = pack.generation_basis
    assert b["performance_intent_fingerprint"]
    keys = compute_current_dependency_keys(tmp_path, _project(), shot)
    assert keys["performance_intent_fingerprint"] == b["performance_intent_fingerprint"]
    # intent 变 → 键变（STALE 诚实暴露）
    shot2 = _shot(dialogue="别过去。", characters=["林晚星"],
                  performance_intent=PerformanceIntent(emotion="calm"))
    keys2 = compute_current_dependency_keys(tmp_path, _project(), shot2)
    assert keys2["performance_intent_fingerprint"] != keys["performance_intent_fingerprint"]
    # 无 intent → None（旧版本无此键不误报）
    bare = _shot(dialogue="别过去。", characters=["林晚星"])
    assert compute_current_dependency_keys(tmp_path, _project(), bare)[
        "performance_intent_fingerprint"] is None


def test_freshness_stale_on_performance_intent_change(tmp_path, monkeypatch):
    """§23：performance intent 变 → STALE（actual dependency）。"""
    from types import SimpleNamespace
    import deps as _deps
    from src.core.store import ProjectStore as PS
    from src.core.write_lock import _clear_for_test
    from test_shot_binding_s3b1 import make_pdir
    from test_shot_binding_s3c2 import (FakeMediaClient, _load_project,
                                        _seed_first_frame, _set_video_ready)
    _clear_for_test()
    try:
        pdir = make_pdir(tmp_path)

        class _StubPM:
            def get_project_dir(self, name):
                return pdir
        monkeypatch.setattr(_deps, "project_manager", _StubPM())
        monkeypatch.setattr(_deps, "agnes_client", FakeMediaClient())
        _seed_first_frame(pdir)
        _set_video_ready(pdir)

        def mut(s):
            s.dialogue = "我们现在出发。"
            s.characters = ["林晚星"]
        PS(pdir).update_shot("s01_c01", mut)
        p = PS(pdir).load()
        p.native_av = NativeAVSettings(spoken_language=LANG)
        PS(pdir).save(p)
        shot = PS(pdir).load().get_shot("s01_c01")
        # 构造 stored basis（无 intent 时代）vs 当前（有 intent）
        from src.generation.native_av_pack import (
            NativeAVGenerationPack, compute_current_dependency_keys as _cc,
            native_av_pre_commit_check)
        stored = dict(_cc(pdir, PS(pdir).load(), shot))
        stored["performance_intent_fingerprint"] = "stale_fp"
        PS(pdir).update_shot(
            "s01_c01",
            lambda s: setattr(s, "performance_intent",
                              PerformanceIntent(emotion="tense", intensity="high")))
        shot2 = PS(pdir).load().get_shot("s01_c01")
        with pytest.raises(Exception) as ei:
            native_av_pre_commit_check(pdir, PS(pdir).load(), shot2, stored,
                                       provider_options={"mode": "i2v", "model": None,
                                                         "num_frames": 81,
                                                         "frame_rate": 24,
                                                         "width": 1280, "height": 704,
                                                         "seed": None,
                                                         "negative_prompt": None})
        assert "performance_intent_fingerprint" in ei.value.detail["mismatched_keys"]
    finally:
        _clear_for_test()


def test_creative_fingerprint_tracks_action_sequence(tmp_path):
    """C2：action_sequence 变 → video_prompt_creative_fingerprint 变
    （否则旧 final 在顺序变更后谎报 CURRENT）；无关字段不误报。"""
    shot = _shot(dialogue="别过去。", characters=["林晚星"])
    keys0 = compute_current_dependency_keys(tmp_path, _project(), shot)
    shot_seq = _shot(dialogue="别过去。", characters=["林晚星"], action_sequence=SEQ)
    keys1 = compute_current_dependency_keys(tmp_path, _project(), shot_seq)
    assert keys1["video_prompt_creative_fingerprint"] !=         keys0["video_prompt_creative_fingerprint"]
    # 无关字段（note/description——不进视频 prompt）不误报
    shot_unrel = _shot(dialogue="别过去。", characters=["林晚星"],
                       note="无关备注", description="首帧描述变化")
    keys2 = compute_current_dependency_keys(tmp_path, _project(), shot_unrel)
    assert keys2["video_prompt_creative_fingerprint"] ==         keys0["video_prompt_creative_fingerprint"]
