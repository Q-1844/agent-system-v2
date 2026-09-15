# tests/test_native_av_b1_dialogue_control.py
# Native AV B1 — Dialogue + Speaker Control（17.md §14 自动测试范围）。
#
# Commit A 范围：dialogue parser + speaker resolution + binding 模型块。
# Commit B（prompt integration / feature gate / handler 回归）在
# tests/test_native_av_b1_prompt_integration.py。
#
# 🔴 只保护本阶段改动（17.md §14）：Dialogue 原文不变 / speaker 解析规则 /
#    fail closed / binding 块 round-trip。不为测试数量扩范围。
import pytest

from src.core.models import Shot
from src.generation.dialogue_control import (
    DialogueControlError,
    build_dialogue_block,
    compile_prompt_with_native_dialogue,
    resolve_native_dialogue,
    split_dialogue_lines,
    split_speaker_prefix,
)
from src.shot_binding.models import (
    DialogueSpeakerBinding,
    DialogueSpeakerLine,
    ShotBindingDocument,
    ShotProductionBinding,
)
from src.shot_binding.store import ShotBindingStore


LANG = "Mandarin Chinese"


def _shot(dialogue="", characters=None, shot_id="s01_c01"):
    return Shot(shot_id=shot_id, scene_id="s01", index=1,
                dialogue=dialogue, characters=characters or [])


def _write_dsb(pdir, shot_id, lines, resolution="explicit"):
    """写一个只带 dialogue_speaker_binding 的 binding（B1 最小形态）。"""
    doc = ShotBindingDocument(bindings={
        shot_id: ShotProductionBinding(
            shot_id=shot_id,
            dialogue_speaker_binding=DialogueSpeakerBinding(
                lines=[DialogueSpeakerLine(index=i, speaker_character_name=s)
                       for i, s in lines],
                resolution=resolution),
        )})
    ShotBindingStore(pdir).save(doc)


# ============ Dialogue parser（确定性）============

def test_parser_split_lines_deterministic():
    text = "林晚星：「门开了吗？」\r\n\n  顾岩：「还没有。」  \n"
    assert split_dialogue_lines(text) == [
        "林晚星：「门开了吗？」", "顾岩：「还没有。」"]
    assert split_dialogue_lines(text) == split_dialogue_lines(text)
    assert split_dialogue_lines("") == []
    assert split_dialogue_lines(None) == []


def test_parser_speaker_prefix_full_and_half_width():
    assert split_speaker_prefix("小满：灯塔！灯塔灭了！") == ("小满", "灯塔！灯塔灭了！")
    assert split_speaker_prefix("Bob: Not yet.") == ("Bob", "Not yet.")
    # 无前缀 → 原行原样（含「」/（舞台指示））
    assert split_speaker_prefix("「门开了吗？」") == (None, "「门开了吗？」")
    assert split_speaker_prefix("（对着无线电）全速倒车！") == (None, "（对着无线电）全速倒车！")


# ============ Speaker 解析（§7/§8）============

def test_no_dialogue_returns_none():
    assert resolve_native_dialogue(None, _shot(dialogue=""), language=LANG) is None
    assert resolve_native_dialogue(None, _shot(dialogue="  \n  "), language=LANG) is None


def test_language_missing_fail_closed():
    with pytest.raises(DialogueControlError) as ei:
        resolve_native_dialogue(None, _shot(dialogue="我们走。", characters=["陈默"]),
                                language="")
    assert ei.value.code == "language_missing"


def test_single_character_single_auto_all_lines():
    shot = _shot(dialogue="我们现在出发。\n等一下。", characters=["陈默"])
    ctl = resolve_native_dialogue(None, shot, language=LANG)
    assert ctl.resolution == "single_auto"
    assert [ln.speaker_character_name for ln in ctl.lines] == ["陈默", "陈默"]
    assert [ln.text for ln in ctl.lines] == ["我们现在出发。", "等一下。"]
    assert ctl.silent_characters == ()


def test_single_character_conflicting_inline_prefix_rejected():
    # 单角色镜里内联前缀是别人 → 数据冲突，fail closed（不猜不纠错）
    with pytest.raises(DialogueControlError) as ei:
        resolve_native_dialogue(None, _shot(dialogue="爷爷：到家了吗？",
                                            characters=["小满"]), language=LANG)
    assert ei.value.code == "speaker_not_in_characters"


def test_single_character_self_prefix_ok():
    ctl = resolve_native_dialogue(None, _shot(dialogue="陈海锋：满舵左转十五度！",
                                              characters=["陈海锋"]), language=LANG)
    assert ctl.resolution == "explicit"   # 内联前缀=显式证据
    assert ctl.lines[0].speaker_character_name == "陈海锋"


def test_multi_character_inline_prefix_resolves():
    shot = _shot(dialogue="林晚星：「门开了吗？」\n顾岩：「还没有。」\n林晚星：「那就快一点。」",
                 characters=["林晚星", "顾岩"])
    ctl = resolve_native_dialogue(None, shot, language=LANG)
    assert ctl.resolution == "explicit"
    assert [ln.speaker_character_name for ln in ctl.lines] == ["林晚星", "顾岩", "林晚星"]
    assert [ln.text for ln in ctl.lines] == [
        "「门开了吗？」", "「还没有。」", "「那就快一点。」"]
    assert ctl.silent_characters == ()


def test_multi_character_unprefixed_line_fail_closed():
    # 真实数据形态：_p4_accept s03_c01（chars=2、无前缀、无 binding）→ 不猜
    with pytest.raises(DialogueControlError) as ei:
        resolve_native_dialogue(None, _shot(dialogue="我不会屈服！",
                                            characters=["小星", "暗影"]), language=LANG)
    assert ei.value.code == "ambiguous_speaker"


def test_multi_character_prefix_not_in_characters_rejected():
    # 真实数据形态：Replay_Gov s01_c03（前缀 陈海锋 ∉ [林晚星,小满]）
    with pytest.raises(DialogueControlError) as ei:
        resolve_native_dialogue(
            None,
            _shot(dialogue="林晚星：「这里是灯塔监听室。」\n陈海锋：「这里是渔船。」",
                  characters=["林晚星", "小满"]),
            language=LANG)
    assert ei.value.code == "speaker_not_in_characters"
    assert "陈海锋" in ei.value.detail


def test_multi_character_binding_resolves_unprefixed_lines(tmp_path):
    shot = _shot(dialogue="我不会屈服。\n那就试试看。", characters=["小星", "暗影"])
    _write_dsb(tmp_path, "s01_c01", [(0, "小星"), (1, "暗影")])
    ctl = resolve_native_dialogue(tmp_path, shot, language=LANG)
    assert ctl.resolution == "explicit"
    assert [ln.speaker_character_name for ln in ctl.lines] == ["小星", "暗影"]


def test_binding_takes_priority_over_inline_prefix(tmp_path):
    shot = _shot(dialogue="林晚星：「门开了吗？」\n顾岩：「还没有。」",
                 characters=["林晚星", "顾岩"])
    _write_dsb(tmp_path, "s01_c01", [(0, "顾岩"), (1, "林晚星")])  # 反着裁（显式优先）
    ctl = resolve_native_dialogue(tmp_path, shot, language=LANG)
    assert [ln.speaker_character_name for ln in ctl.lines] == ["顾岩", "林晚星"]


def test_binding_partial_coverage_uncovered_line_fail_closed(tmp_path):
    shot = _shot(dialogue="甲的台词。\n乙的台词。", characters=["小星", "暗影"])
    _write_dsb(tmp_path, "s01_c01", [(0, "小星")])   # 只裁第 0 句
    with pytest.raises(DialogueControlError) as ei:
        resolve_native_dialogue(tmp_path, shot, language=LANG)
    assert ei.value.code == "ambiguous_speaker"


def test_binding_speaker_not_in_characters_rejected(tmp_path):
    shot = _shot(dialogue="台词一。\n台词二。", characters=["小星", "暗影"])
    _write_dsb(tmp_path, "s01_c01", [(0, "小星"), (1, "旁白")])
    with pytest.raises(DialogueControlError) as ei:
        resolve_native_dialogue(tmp_path, shot, language=LANG)
    assert ei.value.code == "speaker_not_in_characters"


def test_binding_index_out_of_range_and_duplicate(tmp_path):
    shot = _shot(dialogue="一句台词。", characters=["小星", "暗影"])
    _write_dsb(tmp_path, "s01_c01", [(3, "小星")])
    with pytest.raises(DialogueControlError) as ei:
        resolve_native_dialogue(tmp_path, shot, language=LANG)
    assert ei.value.code == "invalid_binding"

    _write_dsb(tmp_path, "s01_c01", [(0, "小星"), (0, "暗影")])
    with pytest.raises(DialogueControlError) as ei:
        resolve_native_dialogue(tmp_path, shot, language=LANG)
    assert ei.value.code == "invalid_binding"


def test_multi_line_order_preserved():
    shot = _shot(dialogue="林晚星：A一句。\n顾岩：B一句。\n林晚星：A二句。\n顾岩：B二句。",
                 characters=["林晚星", "顾岩"])
    ctl = resolve_native_dialogue(None, shot, language=LANG)
    assert [ln.index for ln in ctl.lines] == [0, 1, 2, 3]
    assert [ln.speaker_character_name for ln in ctl.lines] == [
        "林晚星", "顾岩", "林晚星", "顾岩"]
    assert [ln.text for ln in ctl.lines] == ["A一句。", "B一句。", "A二句。", "B二句。"]


def test_silent_characters_derived_and_deterministic(tmp_path):
    shot = _shot(dialogue="林晚星：灯塔！灯塔灭了！", characters=["小满", "林晚星"])
    c1 = resolve_native_dialogue(None, shot, language=LANG)
    c2 = resolve_native_dialogue(None, shot, language=LANG)
    assert c1 == c2                                   # deterministic
    assert c1.silent_characters == ("小满",)
    assert c1.lines[0].text == "灯塔！灯塔灭了！"       # 前缀剥离，正文原样


# ============ Prompt 编译（§10）============

def test_block_verbatim_text_and_speakers():
    shot = _shot(dialogue="林晚星：「门开了吗？」\n顾岩：「还没有。」\n林晚星：「那就快一点。」",
                 characters=["林晚星", "顾岩"])
    ctl = resolve_native_dialogue(None, shot, language=LANG)
    block = build_dialogue_block(ctl)
    assert "「门开了吗？」" in block and "「还没有。」" in block and "「那就快一点。」" in block
    assert "1. 林晚星 says:" in block
    assert "2. 顾岩 replies:" in block
    assert "3. 林晚星 replies:" in block, "换人说话=replies（顾岩→林晚星）"
    assert "Do not swap the speakers" in block
    assert "\n" not in block, "单行形态（A0 已验证 prompt 形态）"


def test_block_same_speaker_consecutive_uses_then_says():
    shot = _shot(dialogue="林晚星：第一句。\n林晚星：第二句。",
                 characters=["林晚星", "顾岩"])
    block = build_dialogue_block(resolve_native_dialogue(None, shot, language=LANG))
    assert "2. 林晚星 then says:" in block


def test_block_silent_character_instruction():
    shot = _shot(dialogue="林晚星：灯塔！灯塔灭了！", characters=["小满", "林晚星"])
    block = build_dialogue_block(resolve_native_dialogue(None, shot, language=LANG))
    assert "小满 remains completely silent" in block, "单角色沉默用 remains"
    assert "no dialogue is invented for them" in block

    shot2 = _shot(dialogue="林晚星：灯塔！灯塔灭了！", characters=["小满", "顾岩", "林晚星"])
    block2 = build_dialogue_block(resolve_native_dialogue(None, shot2, language=LANG))
    assert "小满, 顾岩 remain completely silent" in block2, "多角色沉默用 remain"


def test_compile_appends_after_visual_prompt_verbatim():
    shot = _shot(dialogue="我们走。", characters=["陈默"], shot_id="s01_c02")
    shot.motion = "she turns and walks"
    visual = ("Animate this image: she turns and walks, "
              "keep character identity and outfit consistent")
    compiled = compile_prompt_with_native_dialogue(visual, None, None, shot,
                                                   language_override=LANG)
    assert compiled.startswith(visual), "现有 visual prompt 原样保持在前"
    assert compiled[len(visual):].startswith(" ")
    assert "1. 陈默 says: 我们走。" in compiled, "台词原文原样（原文无「」则不加）"


def test_compile_no_dialogue_returns_prompt_unchanged():
    shot = _shot(dialogue="", characters=["陈默"])
    visual = "Animate this image: subtle motion"
    assert compile_prompt_with_native_dialogue(
        visual, None, None, shot, language_override=LANG) == visual


def test_compile_deterministic(tmp_path):
    shot = _shot(dialogue="林晚星：「门开了吗？」\n顾岩：「还没有。」",
                 characters=["林晚星", "顾岩"])
    p1 = compile_prompt_with_native_dialogue("BASE", None, None, shot,
                                             language_override=LANG)
    p2 = compile_prompt_with_native_dialogue("BASE", None, None, shot,
                                             language_override=LANG)
    assert p1 == p2


# ============ ShotProductionBinding.dialogue_speaker_binding round-trip（§6）============

def test_binding_block_roundtrip():
    b = ShotProductionBinding(
        shot_id="s01_c01",
        dialogue_speaker_binding=DialogueSpeakerBinding(
            lines=[DialogueSpeakerLine(index=0, speaker_character_name="林晚星"),
                   DialogueSpeakerLine(index=1, speaker_character_name="顾岩")],
            resolution="explicit"))
    d = b.to_dict()
    assert d["dialogue_speaker_binding"]["lines"] == [
        {"index": 0, "speaker_character_name": "林晚星"},
        {"index": 1, "speaker_character_name": "顾岩"}]
    b2 = ShotProductionBinding.from_dict(d)
    assert b2.dialogue_speaker_binding == b.dialogue_speaker_binding


def test_binding_block_backward_compatible_old_json():
    # 旧 JSON（无该字段）→ None，不炸
    old = {"shot_id": "s01_c01", "scene_id": "s01", "production_notes": ""}
    assert ShotProductionBinding.from_dict(old).dialogue_speaker_binding is None


def test_binding_block_forward_compatible_unknown_fields_dropped():
    # 旧读取器语义：新 JSON 的未知字段被 known-field 过滤丢弃
    d = {"shot_id": "s01_c01", "future_field": {"x": 1}}
    assert ShotProductionBinding.from_dict(d).shot_id == "s01_c01"


def test_document_revision_moves_with_dialogue_block():
    b1 = ShotProductionBinding(shot_id="s01_c01", updated_at=1.0)
    b2 = ShotProductionBinding(
        shot_id="s01_c01", updated_at=1.0,
        dialogue_speaker_binding=DialogueSpeakerBinding(
            lines=[DialogueSpeakerLine(index=0, speaker_character_name="林晚星")]))
    from src.shot_binding.models import binding_revision
    assert binding_revision(b1) != binding_revision(b2), \
        "dialogue_speaker_binding 变化必须翻动 binding_revision（pre-dispatch race guard 覆盖）"


# ============ A1/A2 零依赖（17.md §14）============

def test_no_src_audio_dependency():
    # 17.md §14：B1 零依赖 A1/A2 —— 检查真实 import 语句（AST），不看注释/字符串
    import ast
    import inspect
    from src.generation import dialogue_control
    tree = ast.parse(inspect.getsource(dialogue_control))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert not a.name.startswith(("src.audio", "audio")), a.name
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert not mod.startswith(("src.audio", "audio")), mod
            assert node.level == 0 or "audio" not in mod, (node.level, mod)


def test_no_src_audio_dependency_runtime():
    # fresh 进程 import dialogue_control 链路，断言 src.audio 未被加载
    #（不能用主进程 sys.modules——全量回归时其他 audio 测试会先 import 它）
    import subprocess
    import sys
    code = ("import sys, src.generation.dialogue_control;"
            "bad = [m for m in sys.modules if m == 'src.audio' or m.startswith('src.audio.')];"
            "print('BAD:' + '|'.join(bad))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-500:]
    tail = r.stdout.strip().split("BAD:")[-1]
    assert tail == "", f"dialogue_control 链路引入了 src.audio: {tail}"
