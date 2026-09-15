# src/generation/dialogue_control.py
# Native AV B1 — Dialogue + Speaker Control（17.md §6-§12）。
#
# 职责：把 Shot.dialogue（唯一台词真相）+ speaker 绑定解析成一段**确定性**的
# On-screen Dialogue prompt 段，追加在现有 visual prompt 之后。
#
# 🔴 第一原则（17.md）：
#   - §3/§11 现有 Visual V1 保持原样：本模块只影响 compiled prompt content，
#     不改 Agnes request shape（无 audios / audio_url / reference mode）；
#   - §5 台词文本 = Shot.dialogue 原始切片——不改写/总结/润色/翻译/补词；
#     仅剥离「角色名：」speaker 元数据前缀（该前缀是"谁说"，由 §6 块或内联
#     证据表达，不属于台词正文；「」引号属原文，保留）；
#   - §7 多角色且无法唯一确定 → fail closed（绝不默认第一个角色/猜脸/猜性别/
#     LLM 猜/静默继续）；单角色 → single_auto（与 A1 resolve_speaker 哲学一致）；
#   - §8 speaker 必须 ∈ Shot.characters，否则 reject + 结构化诊断——
#     绝不自动加角色、绝不静默纠错名字；
#   - §9 derived / ephemeral：NativeDialogueControl 不持久化、无业务 ID、
#     不是第二真相（Dialogue 真相仍是 Shot.dialogue，Binding 不复制正文）；
#   - §12 feature gate：调用方（handler/adapter）显式 opt-in；gate 关闭时
#     本模块根本不被调用，现有行为逐字节不变。
#   - §14 A1/A2 零依赖：本模块不 import src.audio。
#
# language（§10）：B1 不建语言系统——由调用方显式传入（缺省/空 → fail closed），
# 来源在报告中记录（本轮 E2E="Mandarin Chinese"，项目创作语料的既成事实）。
#
# prompt 形态（§10）：单行、空格拼接、「」原样引用——A0 探针（D/J 组）已验证
# 的 prompt 形态是单行 + 内联「」引用，B1 不引入未验证的多行 prompt 形态。
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


# ============ 异常（fail closed，结构化诊断）============
class DialogueControlError(Exception):
    """Native dialogue control 解析失败（17.md §7.2/§8 fail closed）。
    code：language_missing / ambiguous_speaker / speaker_not_in_characters /
    invalid_binding。绝不静默继续生成。"""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"[{code}] {detail}")

    def to_dict(self) -> dict:
        return {"code": self.code, "detail": self.detail}


# ============ derived / ephemeral 控制对象（§9：不持久化、无业务 ID）============
@dataclass(frozen=True)
class DialogueLineControl:
    """一句台词的解析结论。text = Shot.dialogue 原始切片（仅剥离 speaker 前缀）。"""
    index: int                       # 0-based，按非空行序
    text: str                        # 原样切片（含「」）
    speaker_character_name: str      # 已验证 ∈ Shot.characters


@dataclass(frozen=True)
class NativeDialogueControl:
    """一镜的 dialogue 控制结论（derived only）。"""
    shot_id: str
    language: str                    # 调用方显式传入（§10）
    lines: Tuple[DialogueLineControl, ...]
    silent_characters: Tuple[str, ...]   # Shot.characters 中本镜不说话的角色
    resolution: str                  # "single_auto" | "explicit"


# ============ 确定性 dialogue parser（§7）============
# 「角色名：台词」前缀（全角/半角冒号）。name=\S{1,20}? 非贪婪到第一个冒号——
# 与 Director 既有约定一致（director.py:291「可带说话人，例如：小铃：…」）。
_SPEAKER_PREFIX_RE = re.compile(r"^(?P<name>\S{1,20}?)[：:]\s*(?P<rest>\S.*)$", re.DOTALL)


def split_dialogue_lines(text: str) -> List[str]:
    """Shot.dialogue → 非空行列表（\n/\r\n 分隔，逐行 strip）。确定性。"""
    if not text:
        return []
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    return [ln.strip() for ln in normalized.split("\n") if ln.strip()]


def split_speaker_prefix(line: str) -> Tuple[Optional[str], str]:
    """「角色名：台词」→ (角色名, 台词切片)；无前缀 → (None, 原行)。
    台词切片 = 冒号后原样（含「」，仅去首尾空白）——不碰正文。"""
    m = _SPEAKER_PREFIX_RE.match(line)
    if m:
        return m.group("name"), m.group("rest").strip()
    return None, line


def _normalized_characters(shot) -> List[str]:
    """Shot.characters 规范化（strip + 去空 + 保序去重）——与 A1
    voice_pack.resolve_speaker 同口径。绝不改写 canonical。"""
    chars: List[str] = []
    for c in (shot.characters or []):
        name = str(c or "").strip()
        if name and name not in chars:
            chars.append(name)
    return chars


# ============ 显式 binding 块校验（§6 块 → index→speaker map）============
def _binding_speaker_map(project_dir, shot_id: str,
                         n_lines: int, characters: List[str]) -> dict:
    """读 ShotProductionBinding.dialogue_speaker_binding（可选块）。
    - binding 不存在 / 块不存在 / lines 空 → {}（视为未提供，回落内联证据）；
    - 结构非法（index 非整数 / 越界 / 重复 / speaker 非法）→ fail closed；
    - bindings.json 损坏 → ShotBindingSchemaError 向上传播（与 shot_has_binding
      §39 同口径：损坏不静默当 UNBOUND）。"""
    if not project_dir:
        return {}   # 无 binding 上下文（显式语义，非错误）——只回落内联证据/auto
    from src.shot_binding.store import ShotBindingStore
    binding = ShotBindingStore(project_dir).load().bindings.get(str(shot_id or "").strip())
    if binding is None or binding.dialogue_speaker_binding is None:
        return {}
    dsb = binding.dialogue_speaker_binding
    if not dsb.lines:
        return {}
    mapping: dict = {}
    for ln in dsb.lines:
        idx = ln.index
        if isinstance(idx, bool) or not isinstance(idx, int):
            raise DialogueControlError(
                "invalid_binding",
                f"dialogue_speaker_binding.lines[].index 必须是整数，得到 {idx!r}")
        if not (0 <= idx < n_lines):
            raise DialogueControlError(
                "invalid_binding",
                f"dialogue_speaker_binding index {idx} 越界（本镜共 {n_lines} 句台词）")
        speaker = str(ln.speaker_character_name or "").strip()
        if not speaker:
            raise DialogueControlError(
                "invalid_binding",
                f"dialogue_speaker_binding index {idx} speaker_character_name 为空")
        if idx in mapping:
            raise DialogueControlError(
                "invalid_binding",
                f"dialogue_speaker_binding index {idx} 重复裁决（{mapping[idx]!r} vs {speaker!r}）")
        mapping[idx] = speaker
    for idx, speaker in mapping.items():
        if speaker not in characters:
            raise DialogueControlError(
                "speaker_not_in_characters",
                f"dialogue_speaker_binding 第 {idx} 句 speaker {speaker!r} 不在 "
                f"Shot.characters {characters} 中（17.md §8：reject，不自动加角色/不纠错）")
    return mapping


# ============ speaker 解析（§7/§8）============
def resolve_native_dialogue(project_dir, shot, *,
                            language: str) -> Optional[NativeDialogueControl]:
    """解析一镜的 native dialogue control。
    - 无非空台词行 → None（无 block，现有行为不变）；
    - language 空 → language_missing（§10：显式 caller 输入，不猜）；
    - speaker 无法唯一确定（ambiguous_speaker）/ speaker ∉ Shot.characters
      （speaker_not_in_characters）/ binding 块非法（invalid_binding）→ fail closed。
    binding 显式裁决优先（§7.2）；其余句：单角色 single_auto；多角色读内联
    「角色名：」前缀（解析辅助证据）；仍无法唯一确定 → fail closed。"""
    lang = str(language or "").strip()
    if not lang:
        raise DialogueControlError(
            "language_missing",
            "native dialogue control 需要显式 language（17.md §10：B1 不建语言系统，"
            "由 caller/test 显式输入并记录来源）")

    raw_lines = split_dialogue_lines(getattr(shot, "dialogue", "") or "")
    if not raw_lines:
        return None

    characters = _normalized_characters(shot)
    binding_map = _binding_speaker_map(project_dir, getattr(shot, "shot_id", ""),
                                       len(raw_lines), characters)

    resolved: List[DialogueLineControl] = []
    used_binding = False
    used_prefix = False
    for idx, raw in enumerate(raw_lines):
        if idx in binding_map:
            speaker = binding_map[idx]
            text = raw
            used_binding = True
        else:
            name, text = split_speaker_prefix(raw)
            if name is not None:
                if name not in characters:
                    raise DialogueControlError(
                        "speaker_not_in_characters",
                        f"第 {idx} 句内联 speaker {name!r} 不在 Shot.characters "
                        f"{characters} 中（17.md §8：reject，不自动加角色/不纠错）")
                speaker = name
                used_prefix = True
            elif len(characters) == 1:
                speaker = characters[0]          # single_auto（§7.1）
            else:
                raise DialogueControlError(
                    "ambiguous_speaker",
                    f"第 {idx} 句（{raw[:40]!r}…）在多角色镜 {characters} 中无法唯一确定 "
                    f"speaker：无 dialogue_speaker_binding 裁决、无内联「角色名：」前缀"
                    f"（17.md §7.2：不猜，fail closed）")
        resolved.append(DialogueLineControl(index=idx, text=text,
                                            speaker_character_name=speaker))

    speakers = []
    for ln in resolved:
        if ln.speaker_character_name not in speakers:
            speakers.append(ln.speaker_character_name)
    silent = tuple(c for c in characters if c not in speakers)
    resolution = ("single_auto"
                  if (not used_binding and not used_prefix and len(characters) == 1)
                  else "explicit")
    return NativeDialogueControl(
        shot_id=str(getattr(shot, "shot_id", "")),
        language=lang,
        lines=tuple(resolved),
        silent_characters=silent,
        resolution=resolution)


# ============ prompt 编译（§10）============
def build_dialogue_block(control: NativeDialogueControl) -> str:
    """确定性编译 On-screen Dialogue prompt 段（单行，§10 + A0 已验证形态）。
    台词文本原样（含「」）；speaker 来自 resolved binding；不添加任何风格形容。"""
    parts = [f"On-screen spoken dialogue in {control.language}:"]
    prev_speaker: Optional[str] = None
    for i, ln in enumerate(control.lines, 1):
        if prev_speaker is None:
            verb = "says"
        elif ln.speaker_character_name == prev_speaker:
            verb = "then says"
        else:
            verb = "replies"
        parts.append(f"{i}. {ln.speaker_character_name} {verb}: {ln.text}")
        prev_speaker = ln.speaker_character_name
    if control.silent_characters:
        if len(control.silent_characters) == 1:
            parts.append(
                control.silent_characters[0]
                + " remains completely silent in this shot: this character does "
                  "not speak, and no dialogue is invented for them.")
        else:
            parts.append(
                ", ".join(control.silent_characters)
                + " remain completely silent in this shot: they do not speak, "
                  "and no dialogue is invented for them.")
    parts.append("Keep every line assigned to its specified speaker. "
                 "Do not swap the speakers and do not invent any additional dialogue.")
    parts.append("The characters' mouths move naturally in sync with their own spoken lines.")
    return " ".join(parts)


def append_dialogue_block(prompt: str, control: NativeDialogueControl) -> str:
    """唯一拼装点（§10）：visual prompt 原样在前，dialogue block 单空格追加在后
    ——整条 prompt 维持 A0 已验证的单行形态。"""
    block = build_dialogue_block(control)
    return f"{prompt} {block}" if prompt else block


def compile_prompt_with_native_dialogue(prompt: str, project_dir, shot, *,
                                        language: str) -> str:
    """flag-on 编译入口（§10）：现有 visual prompt 保持原样，dialogue block
    追加在后。无台词 → 原样返回（§14：无 dialogue 行为不变）。
    fail closed 时抛 DialogueControlError，调用方不得静默继续。"""
    control = resolve_native_dialogue(project_dir, shot, language=language)
    if control is None:
        return prompt
    return append_dialogue_block(prompt, control)
