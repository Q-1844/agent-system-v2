# src/generation/av_prompt_compiler.py
# Native AV C1 — Audio-Visual Prompt Compiler V1（18.md §10-§15）。
#
# 职责：把 NativeAVGenerationPack 的四类语义事实编译成一条 Agnes 已验证的
# 单行 prompt（§11：不要求多行格式，重要的是语义隔离）：
#   1. Visual / performance intent —— 现有 visual prompt 原样（§15：不发明动作）
#   2. Non-spoken action —— §7 舞台指示 + 语气提示（绝不进台词引用）
#   3. Spoken dialogue —— B1 已验证的编号 speaker 行 + 红线
#   4. Native sound environment —— music policy + shot-local 声音意图
#
# 🔴 Prompt 红线（Compiler invariant，不是某个 sample 的临时 patch）：
#   - §12 第一条：只有显式台词可以被说出来——visual/camera/action/motion/SFX/
#     production note 一律不得朗读（B1 A 侧已实测「念提示词」事故）；
#   - §13 第二条：台词不得改写——不翻译/不润色/不总结/不补句/不加词
#     （自然语气/呼吸/非语言发声可由表演自然产生，不走向机械 TTS）；
#   - §14 speaker assignment 确定性表达 + Do not swap + silent 声明（B1 已证有效）；
#   - §17 music_policy=none → 显式 no-BGM 语句（去随机 BGM、保留 diegetic 声）。
#
# 确定性：同 pack → 同输出（无 LLM、无随机、无时间戳）。
from __future__ import annotations

from typing import List

from src.generation.native_av_pack import (
    NATIVE_AV_PROMPT_SCHEMA,
    NativeAVGenerationPack,
)

# §12：只有台词可以被说出来（红线一——解决 B1 A 侧「把 motion prompt 当台词朗读」）
_ONLY_DIALOGUE_SPOKEN_GUARD = (
    "Only the explicitly quoted dialogue is spoken aloud: do not speak, narrate, "
    "or read any visual description, camera instruction, action description, "
    "character motion, sound-effect instruction, or production note.")
# §13：台词不得改写（红线二），保留自然表演空间
_EXACT_DIALOGUE_GUARD = (
    "Speak exactly the supplied dialogue: do not translate, paraphrase, summarize, "
    "or invent additional spoken lines; natural tone, breathing, and non-verbal "
    "vocalizations are allowed as long as the meaning of the lines is unchanged.")
# §17：music_policy=none（解决 B1 人评「对白镜自动出现背景音乐」）
_NO_BGM_GUARD = (
    "No background music or musical score: use only natural dialogue, diegetic "
    "sounds, and appropriate environmental ambience.")


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


def _sound_zone(pack: NativeAVGenerationPack) -> List[str]:
    """Zone 4：native sound environment（§16/§17/§18——意图，不是手工时间线）。"""
    intent = pack.audio_intent or {}
    parts: List[str] = []
    if intent.get("music_policy") == "none":
        parts.append(_NO_BGM_GUARD)
    directives = [str(d) for d in (intent.get("directives") or []) if str(d).strip()]
    if directives:
        parts.append("Shot-local sound (generate natively within the scene, not as "
                     "separate audio): " + "; ".join(directives) + ".")
    return parts


def compile_native_av_prompt(pack: NativeAVGenerationPack,
                             visual_prompt: str) -> str:
    """唯一编译点（§10/§21）：visual prompt 原样在前（§15：不重排、不发明动作），
    AV 语义区按 §11 顺序追加；整条保持单行形态（A0/B1 已验证形态）。
    🔴 无 dialogue 且 sound zone 为空 → 返回 visual_prompt 逐字节原样
    （§4：无 dialogue 的 visual-only 行为不变）。"""
    if not isinstance(pack, NativeAVGenerationPack):
        raise TypeError("compile_native_av_prompt 需要 NativeAVGenerationPack")
    av_parts = _dialogue_zone(pack) + _sound_zone(pack)
    if not av_parts:
        return visual_prompt
    parts = [visual_prompt] if visual_prompt else []
    parts.extend(p for p in av_parts if p)
    return " ".join(parts)
