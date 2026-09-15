# src/generation/av_prompt_compiler.py
# Native AV C2 — AV Prompt Compiler V3（22.md §15）。
# 职责升级（在 C1-R 已验证的 Dialogue/Speaker/Language 控制之上，22.md §15）：
#   Visual identity + spatial/composition intent（visual prompt 原样，不发明动作）
#   + Ordered action performance（时序框架在 prompt_builder；本层不重复）
#   + Expression / emotional performance（§12-§14 统一表演指令——新增）
#   + B1 verified dialogue / speaker / language（逐字节保留）
# 🔴 不重新加入：AudioEventIntent / Music policy / Voice Reference（22.md §15）。
# 🔴 §16：正向表演描述优先，不堆 Do-not；每条规则对应
#    Observed failure → System fact → Compiler transformation（见报告 §10）。
#
# Prompt 红线（Compiler invariant，B1 真机 A/B 证据，21.md §18 保留）：
#   - 红线一：只有显式台词可以被说出来；
#   - 红线二：台词不得改写；
#   - speaker assignment 确定性表达 + Do not swap + silent 声明。
#
# 确定性：同 pack → 同输出（无 LLM、无随机、无时间戳）。
from __future__ import annotations

from typing import List

from src.generation.native_av_pack import (
    NATIVE_AV_PROMPT_SCHEMA,
    NativeAVGenerationPack,
)

# 红线一：只有台词可以被说出来（解决 B1 A 侧「把 motion prompt 当台词朗读」）
_ONLY_DIALOGUE_SPOKEN_GUARD = (
    "Only the explicitly quoted dialogue is spoken aloud: do not speak, narrate, "
    "or read any visual description, camera instruction, action description, "
    "character motion, sound-effect instruction, or production note.")
# 红线二：台词不得改写，保留自然表演空间
_EXACT_DIALOGUE_GUARD = (
    "Speak exactly the supplied dialogue: do not translate, paraphrase, summarize, "
    "or invent additional spoken lines; natural tone, breathing, and non-verbal "
    "vocalizations are allowed as long as the meaning of the lines is unchanged.")


def _performance_zone(pack: NativeAVGenerationPack) -> List[str]:
    """C2 §12-§14：统一表演指令（新增）。
    - 只在 Shot.performance_intent 非空时输出（§23：空 intent 零改动）；
    - 正向描述（§16）：一条指令把表情/动作/对白绑成同一场表演，
      不堆 Do-not、不孤立放关键词；
    - Observed failure → 规则来源：C1 人评 E 样本「镜头应该紧张，
      但人物表情不够紧张」（Shot.important_details 不进视频 prompt，
      且系统此前无表演意图字段——22.md §12/§13 审计结论）。"""
    perf = pack.performance or {}
    emotion = str(perf.get("emotion") or "").strip()
    intensity = str(perf.get("intensity") or "").strip()
    note = str(perf.get("note") or "").strip()
    if not (emotion or intensity or note):
        return []
    parts: List[str] = []
    bits = []
    if emotion:
        bits.append(f"emotional state: {emotion}")
    if intensity:
        bits.append(f"intensity: {intensity}")
    line = "Unified performance direction (" + "; ".join(bits) + \
           "): the facial expression, body action, and spoken dialogue are one " \
           "continuous, coherent performance — keep them at this level throughout the shot."
    parts.append(line)
    if note:
        parts.append(f"Performance note: {note}")
    return parts


def _dialogue_zone(pack: NativeAVGenerationPack) -> List[str]:
    """Zone 2 + Zone 3：non-spoken（指示/语气）+ spoken dialogue + 红线一/二。
    单行输出；B1 已验证的编号行形态原样保留。"""
    d = pack.dialogue
    if not d:
        return []
    parts: List[str] = []
    # ---- Zone 2：non-spoken action（§11）——指示与语气绝不进台词引用（§7）----
    non_spoken: List[tuple] = []
    for direction in d.get("directions") or []:
        speaker = direction.get("speaker_character_name")
        prefix = f"{speaker} stage direction" if speaker else "Stage direction"
        non_spoken.append((direction.get("index", 0),
                           f"{prefix} (not spoken, do not read aloud): "
                           f"{direction.get('text', '')}"))
    for ln in d.get("lines") or []:
        hints = ln.get("delivery_hints") or []
        if hints:
            non_spoken.append((ln.get("index", 0),
                               f"{ln.get('speaker_character_name')} delivery "
                               f"direction (not spoken): {'; '.join(hints)}"))
    non_spoken.sort(key=lambda t: (t[0], t[1]))
    parts.extend(text for _, text in non_spoken)
    # ---- Zone 3：spoken dialogue（B1 已验证单行形态：语言头 + 编号 speaker 行）----
    parts.append(f"On-screen spoken dialogue in {d.get('spoken_language', '')}:")
    parts.append(_ONLY_DIALOGUE_SPOKEN_GUARD)
    lines = d.get("lines") or []
    prev_speaker = None
    for i, ln in enumerate(lines, 1):
        speaker = ln.get("speaker_character_name", "")
        if prev_speaker is None:
            verb = "says"
        elif speaker == prev_speaker:
            verb = "then says"
        else:
            verb = "replies"
        parts.append(f"{i}. {speaker} {verb}: {ln.get('exact_text', '')}")
        prev_speaker = speaker
    silent = d.get("silent_characters") or []
    if len(silent) == 1:
        parts.append(silent[0] + " remains completely silent in this shot: this "
                     "character does not speak, and no dialogue is invented for them.")
    elif silent:
        parts.append(", ".join(silent) + " remain completely silent in this shot: "
                     "they do not speak, and no dialogue is invented for them.")
    parts.append("Keep every line assigned to its specified speaker. "
                 "Do not swap the speakers.")
    parts.append("The characters' mouths move naturally in sync with their own "
                 "spoken lines.")
    parts.append(_EXACT_DIALOGUE_GUARD)
    return parts


def compile_native_av_prompt(pack: NativeAVGenerationPack,
                             visual_prompt: str) -> str:
    """唯一编译点（21.md §17：BOUND/UNBOUND 共用）：visual prompt 原样在前
    （不重排、不发明动作），统一表演区（C2 新增，§14：与对白同场编译）+
    dialogue 语义区按序追加；整条保持单行形态（A0/B1 已验证形态）。
    🔴 无 dialogue 且表演意图为空 → 返回 visual_prompt 逐字节原样
    （21.md §4 + 22.md §23：无 dialogue、空 intent 行为不变）。"""
    if not isinstance(pack, NativeAVGenerationPack):
        raise TypeError("compile_native_av_prompt 需要 NativeAVGenerationPack")
    av_parts = _performance_zone(pack) + _dialogue_zone(pack)
    if not av_parts:
        return visual_prompt
    parts = [visual_prompt] if visual_prompt else []
    parts.extend(p for p in av_parts if p)
    return " ".join(parts)
