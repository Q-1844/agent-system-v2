# tests/test_native_av_c1_dialogue_stage_directions.py
# Native AV C1 — Commit B：§7 stage directions 生产硬化（18.md §7）。
#
# 规则（确定性，绝不用 LLM 解释字符串）：
#   - 整行括号（全角/半角，可带 speaker 前缀）→ non_spoken_direction，
#     绝不放进 spoken dialogue；
#   - speaker 前缀 + 行首括号 + 正文 → delivery hint（非台词）+ spoken text；
#   - 句中/句尾/嵌套括号 → ambiguous_dialogue_markup fail closed（不生成）；
#   - binding index 空间 = 原始非空行序（B1 兼容）；binding 指向指示行 →
#     invalid_binding fail closed；
#   - 全部行都是指示 → 无 spoken 台词 → resolve 返回 None。
import sys
from pathlib import Path

import pytest

sys_path = Path(__file__).parent
if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))

from src.core.models import Shot
from src.generation.dialogue_control import (
    DialogueControlError,
    classify_dialogue_body,
    resolve_native_dialogue,
)

LANG = "Mandarin Chinese"


def _shot(dialogue="", characters=None, shot_id="s01_c01"):
    return Shot(shot_id=shot_id, scene_id="s01", index=1,
                dialogue=dialogue, characters=characters or [])


# ============ classify_dialogue_body（§7 确定性分类）============

def test_full_line_parenthetical_is_direction():
    assert classify_dialogue_body("（林晚星转身看向门口）", line_ref="#0")[0] == "direction"
    assert classify_dialogue_body("(Gu Yan pauses)", line_ref="#1")[0] == "direction"
    kind, hints, text = classify_dialogue_body("（低声）", line_ref="#2")
    assert kind == "direction" and hints == ()
    assert text == "低声"


def test_leading_parenthetical_is_delivery_hint():
    kind, hints, text = classify_dialogue_body("（低声）别过去。", line_ref="#0")
    assert kind == "spoken" and hints == ("低声",) and text == "别过去。"
    # 半角括号
    kind, hints, text = classify_dialogue_body("(whispering) Don't go.", line_ref="#1")
    assert kind == "spoken" and hints == ("whispering",) and text == "Don't go."
    # 连续多个行首提示
    kind, hints, text = classify_dialogue_body("（低声）（颤抖）别过去。", line_ref="#2")
    assert kind == "spoken" and hints == ("低声", "颤抖") and text == "别过去。"


def test_mid_or_trailing_paren_is_ambiguous_fail_closed():
    with pytest.raises(DialogueControlError) as ei:
        classify_dialogue_body("别过去（低声）。", line_ref="#0")
    assert ei.value.code == "ambiguous_dialogue_markup"
    with pytest.raises(DialogueControlError):
        classify_dialogue_body("我说的（这件事）很重要。", line_ref="#1")
    with pytest.raises(DialogueControlError):
        classify_dialogue_body("（嵌套（括号））", line_ref="#2")


# ============ resolve 集成（指示行不进 spoken）============

def test_direction_line_excluded_from_spoken_lines():
    ctl = resolve_native_dialogue(None, _shot(
        dialogue="（林晚星转身看向门口）\n林晚星：别过去。",
        characters=["林晚星"]), language=LANG)
    assert ctl is not None
    assert len(ctl.lines) == 1, "整行指示不进 spoken lines"
    assert ctl.lines[0].text == "别过去。"
    assert ctl.lines[0].index == 1, "index = 原始非空行序（指示行占用 index 空间，B1 兼容）"
    assert len(ctl.directions) == 1
    assert ctl.directions[0].text == "林晚星转身看向门口"
    assert ctl.directions[0].speaker_character_name is None


def test_direction_with_speaker_prefix_attributed():
    ctl = resolve_native_dialogue(None, _shot(
        dialogue="顾岩：（转身看向门口）\n顾岩：我必须去。",
        characters=["顾岩"]), language=LANG)
    assert len(ctl.lines) == 1
    assert ctl.lines[0].text == "我必须去。"
    assert ctl.directions[0].speaker_character_name == "顾岩"


def test_prefix_hint_then_spoken_remainder():
    """行首括号 + 非空余文 = spoken（hint 只是修饰）——「……」属角色发声余韵。"""
    ctl = resolve_native_dialogue(None, _shot(
        dialogue="顾岩：（停顿）……", characters=["顾岩"]), language=LANG)
    assert len(ctl.lines) == 1
    assert ctl.lines[0].text == "……"
    assert ctl.lines[0].delivery_hints == ("停顿",)


def test_prefix_hint_split_not_read_aloud():
    """18.md §7 主证例：林晚星：（低声）别过去。→ hint=低声、spoken=别过去。"""
    ctl = resolve_native_dialogue(None, _shot(
        dialogue="林晚星：（低声）别过去。", characters=["林晚星"]), language=LANG)
    assert ctl.lines[0].text == "别过去。"
    assert ctl.lines[0].delivery_hints == ("低声",)
    assert "低声" not in ctl.lines[0].text, "提示词绝不混入台词正文"


def test_ambiguous_markup_fail_closed():
    with pytest.raises(DialogueControlError) as ei:
        resolve_native_dialogue(None, _shot(
            dialogue="林晚星：别过去（低声）。", characters=["林晚星"]),
            language=LANG)
    assert ei.value.code == "ambiguous_dialogue_markup"


def test_binding_index_pointing_to_direction_fail_closed():
    """binding index 空间 = 原始非空行序；指向指示行 = 索引错位 → fail closed。"""
    pdir = None
    from src.shot_binding.models import (
        DialogueSpeakerBinding,
        DialogueSpeakerLine,
        ShotBindingDocument,
        ShotProductionBinding,
    )
    from src.shot_binding.store import ShotBindingStore
    import tempfile
    from test_shot_binding_s3b1 import make_pdir, run
    with tempfile.TemporaryDirectory() as td:
        from pathlib import Path
        pdir = make_pdir(Path(td))
        doc = ShotBindingDocument(bindings={
            "s01_c01": ShotProductionBinding(
                shot_id="s01_c01",
                dialogue_speaker_binding=DialogueSpeakerBinding(
                    lines=[DialogueSpeakerLine(index=0,
                                               speaker_character_name="林晚星")]))})
        ShotBindingStore(pdir).save(doc)
        shot = _shot(dialogue="（转身）\n林晚星：别过去。", characters=["林晚星"])
        with pytest.raises(DialogueControlError) as ei:
            resolve_native_dialogue(pdir, shot, language=LANG)
        assert ei.value.code == "invalid_binding"


def test_binding_resolution_still_applies_after_direction_lines():
    """指示行占 index 空间，但 spoken 行的 binding 裁决不受影响（index 对齐 raw 行）。"""
    from src.shot_binding.models import (
        DialogueSpeakerBinding,
        DialogueSpeakerLine,
        ShotBindingDocument,
        ShotProductionBinding,
    )
    from src.shot_binding.store import ShotBindingStore
    from test_shot_binding_s3b1 import make_pdir
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        from pathlib import Path
        pdir = make_pdir(Path(td))
        doc = ShotBindingDocument(bindings={
            "s01_c01": ShotProductionBinding(
                shot_id="s01_c01",
                dialogue_speaker_binding=DialogueSpeakerBinding(
                    lines=[DialogueSpeakerLine(index=1,
                                               speaker_character_name="林晚星"),
                           DialogueSpeakerLine(index=2,
                                               speaker_character_name="顾岩")]))})
        ShotBindingStore(pdir).save(doc)
        ctl = resolve_native_dialogue(pdir, _shot(
            dialogue="（转身）\n别过去。\n我必须去。",
            characters=["林晚星", "顾岩"]), language=LANG)
        # raw 行 0 = direction；行 1/2 由 binding 裁决
        assert ctl.directions[0].index == 0
        assert [ln.speaker_character_name for ln in ctl.lines] == ["林晚星", "顾岩"]
        assert [ln.index for ln in ctl.lines] == [1, 2]
        # 无 binding（另一项目目录）时同一 dialogue → 行 1/2 无法唯一确定 → fail closed
        import tempfile as _tf
        with _tf.TemporaryDirectory() as td2:
            pdir2 = make_pdir(Path(td2))
            with pytest.raises(DialogueControlError) as ei:
                resolve_native_dialogue(pdir2, _shot(
                    dialogue="（转身）\n别过去。\n我必须去。",
                    characters=["林晚星", "顾岩"]), language=LANG)
            assert ei.value.code == "ambiguous_speaker"


def test_all_directions_resolves_to_none():
    ctl = resolve_native_dialogue(None, _shot(
        dialogue="（转身离开）\n（灯光渐暗）", characters=["林晚星"]), language=LANG)
    assert ctl is None, "全部行都是指示 → 无 spoken 台词 → 按 no-dialogue 处理"


def test_directions_do_not_affect_silent_characters():
    ctl = resolve_native_dialogue(None, _shot(
        dialogue="林晚星：别过去。\n（顾岩摇头）",
        characters=["林晚星", "顾岩"]), language=LANG)
    assert list(ctl.silent_characters) == ["顾岩"]
    assert len(ctl.directions) == 1
