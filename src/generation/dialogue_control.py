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
    invalid_binding / ambiguous_dialogue_markup。绝不静默继续生成。"""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"[{code}] {detail}")

    def to_dict(self) -> dict:
        return {"code": self.code, "detail": self.detail}


# ============ derived / ephemeral 控制对象（§9：不持久化、无业务 ID）============
@dataclass(frozen=True)
class DialogueLineControl:
    """一句台词的解析结论。text = Shot.dialogue 原始切片（仅剥离 speaker 前缀与
    C1 §7 舞台指示标记）。index = 原始非空行序（binding index 空间，B1 兼容）。"""
    index: int                       # 0-based，按非空行序（含指示行占用）
    text: str                        # 原样切片（含「」，不含 speaker 前缀/括号指示）
    speaker_character_name: str      # 已验证 ∈ Shot.characters
    delivery_hints: Tuple[str, ...] = ()   # §7：行首（语气/表演提示），非台词


@dataclass(frozen=True)
class DialogueDirection:
    """§7：整行舞台指示（non_spoken_direction）——绝不进入 spoken dialogue。"""
    index: int                       # 原始非空行序
    text: str                        # 括号内原文（确定性切片）
    speaker_character_name: Optional[str] = None   # 行带 speaker 前缀时的归属（不做 ∈ 校验——指示不是台词）


@dataclass(frozen=True)
class NativeDialogueControl:
    """一镜的 dialogue 控制结论（derived only）。"""
    shot_id: str
    language: str                    # 调用方显式传入（§10）
    lines: Tuple[DialogueLineControl, ...]
    silent_characters: Tuple[str, ...]   # Shot.characters 中本镜不说话的角色
    resolution: str                  # "single_auto" | "explicit"
    directions: Tuple[DialogueDirection, ...] = ()   # §7 整行舞台指示（C1）


# ============ 确定性 dialogue parser（§7 + C1 §7 stage directions）============
# 「角色名：台词」前缀（全角/半角冒号）。name=\S{1,20}? 非贪婪到第一个冒号——
# 与 Director 既有约定一致（director.py:291「可带说话人，例如：小铃：…」）。
_SPEAKER_PREFIX_RE = re.compile(r"^(?P<name>\S{1,100}?)[：:]\s*(?P<rest>\S.*)$", re.DOTALL)

# §7 舞台指示（确定性规则，不用 LLM 解释字符串）：
#   - 括号组：全角（…）或半角 (...)，内容不含任何括号字符（嵌套 → 无法唯一解析）；
#   - 整行指示：剥 speaker 前缀后整个 body 是一个括号组；
#   - 行首提示：一个或多个连续括号组后仍有正文 → delivery hint + spoken text；
#   - 其余任何位置出现括号（句中/句尾/嵌套）→ ambiguous_dialogue_markup fail closed。
_PAREN_GROUP_RE = re.compile(r"^(?P<group>（[^（）()]*）|\([^()]*\))")
_FULL_PAREN_BODY_RE = re.compile(r"^(?P<groups>(?:（[^（）()]*）|\([^()]*\))+)$")
_HAS_PAREN_RE = re.compile(r"[（(]|[）)]")


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


def classify_dialogue_body(body: str, *, line_ref: str) -> Tuple[str, Tuple[str, ...], str]:
    """C1 §7：把一行 body 确定性分类为（kind, hints, text）。
    - kind="direction"：整行是括号组 → non_spoken_direction；text = 括号内文字
      （多组以空格连接）；
    - kind="spoken"：行首连续括号组 = delivery hints，text = 其余 spoken 正文
      （无括号 → 无 hint 原样）；
    - 句中/句尾/嵌套括号等无法唯一解析的格式 → DialogueControlError
      ambiguous_dialogue_markup（18.md §7：fail closed 或不生成，不用 LLM 临时解释）。"""
    body = body.strip()
    if not body:
        return "spoken", (), ""
    m_full = _FULL_PAREN_BODY_RE.match(body)
    if m_full:
        inners = [body[m.start():m.end()][1:-1].strip()
                  for m in re.finditer(r"（[^（）()]*）|\([^()]*\)", body)]
        return "direction", (), " ".join(s for s in inners if s)
    hints: List[str] = []
    rest = body
    m = _PAREN_GROUP_RE.match(rest)
    while m:
        inner = rest[m.start("group") + 1:m.end("group") - 1].strip()
        if inner:
            hints.append(inner)
        rest = rest[m.end("group"):].lstrip()
        m = _PAREN_GROUP_RE.match(rest)
    if _HAS_PAREN_RE.search(rest):
        raise DialogueControlError(
            "ambiguous_dialogue_markup",
            f"台词行 {line_ref} 的括号标记无法唯一解析（句中/句尾/嵌套括号）：{body!r}"
            f"（18.md §7：spoken content 与 non-spoken direction 必须可确定性切分——"
            f"整行括号=舞台指示；行首括号=语气提示；其余格式 fail closed）")
    return "spoken", tuple(hints), rest


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
def resolve_dialogue_language(project, override: Optional[str]) -> str:
    """C1 §5：language production policy 的唯一 precedence 实现。
    显式 request override > project.native_av.spoken_language；两者皆缺 →
    language_missing fail closed（绝不恢复成让 provider 猜——B1 人评已证伪：
    猜语言产生严重生产错误）。由调用方在确认 Shot.dialogue 非空之后调用。"""
    lang = str(override or "").strip()
    if lang:
        return lang
    settings = getattr(project, "native_av", None)
    lang = str(getattr(settings, "spoken_language", "") or "").strip()
    if lang:
        return lang
    raise DialogueControlError(
        "language_missing",
        "native dialogue control 需要可确定的对白语言：显式 request override 与 "
        "project.native_av.spoken_language 均缺失（18.md §5：fail closed，"
        "绝不恢复成让 Agnes 自己猜语言）")


def resolve_native_dialogue(project_dir, shot, *,
                            language: str) -> Optional[NativeDialogueControl]:
    """解析一镜的 native dialogue control。
    - 无非空台词行 / 全部行都是舞台指示 → None（无 block，现有行为不变；C1 §4：
      language 只对有台词的 shot 有意义——先查台词再查 language）；
    - language 空 → language_missing（§5：不猜）；
    - 括号标记无法唯一解析（ambiguous_dialogue_markup）/ speaker 无法唯一确定
      （ambiguous_speaker）/ speaker ∉ Shot.characters（speaker_not_in_characters）/
      binding 块非法（invalid_binding，含指向指示行）→ fail closed。
    每行先分类（§7：整行括号=direction、行首括号=delivery hint、其余=spoken），
    spoken 行再做 speaker 解析：binding 显式裁决优先（§7.2）；其余句：单角色
    single_auto；多角色读内联「角色名：」前缀（解析辅助证据）；无法唯一确定 →
    fail closed。directions（整行指示）随 control 派生返回，绝不进 spoken。"""
    raw_lines = split_dialogue_lines(getattr(shot, "dialogue", "") or "")
    if not raw_lines:
        return None

    lang = str(language or "").strip()
    if not lang:
        raise DialogueControlError(
            "language_missing",
            "native dialogue control 需要显式 language（18.md §5：fail closed，不猜）")

    characters = _normalized_characters(shot)
    binding_map = _binding_speaker_map(project_dir, getattr(shot, "shot_id", ""),
                                       len(raw_lines), characters)

    resolved: List[DialogueLineControl] = []
    directions: List[DialogueDirection] = []
    used_binding = False
    used_prefix = False
    for idx, raw in enumerate(raw_lines):
        name, body = split_speaker_prefix(raw)
        # Deterministic metadata normalization: exact cast name plus trailing
        # parenthesized delivery hints. Never fuzzy-match or rewrite dialogue.
        if name is not None and name not in characters:
            annotated = re.fullmatch(r"(?P<speaker>[^（）()]+?)(?P<hints>(?:（[^（）()]*）|\([^()]*\))+)", name)
            if annotated and annotated.group("speaker").strip() in characters:
                name = annotated.group("speaker").strip()
                body = annotated.group("hints") + body
        if name is None and re.fullmatch(r"本镜(?:无对白|无台词)(?:（[^（）()]*）|\([^()]*\))?[。.]?", body):
            kind, hints, text = "direction", (), body
        else:
            kind, hints, text = classify_dialogue_body(body, line_ref=f"#{idx}")
        if kind == "direction":
            # §7：整行舞台指示——绝不放进 spoken dialogue。binding 裁决指向
            # 指示行 = 索引空间错位（binding index 按 raw 非空行计），fail closed。
            if idx in binding_map:
                raise DialogueControlError(
                    "invalid_binding",
                    f"dialogue_speaker_binding index {idx} 指向舞台指示行（{raw[:30]!r}）"
                    f"——binding 只裁决 spoken 台词行")
            directions.append(DialogueDirection(index=idx, text=text,
                                                speaker_character_name=name))
            continue
        if not text:
            raise DialogueControlError(
                "ambiguous_dialogue_markup",
                f"台词行 #{idx} 分类为 spoken 但正文为空：{raw!r}")
        if idx in binding_map:
            # 显式裁决优先（§7.2）；内联前缀（若有）按 speaker 元数据剥离——
            # 即便与裁决矛盾也以 binding 为准（显式 > 内联证据，不猜不一致来源）
            speaker = binding_map[idx]
            used_binding = True
        elif name is not None:
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
                                            speaker_character_name=speaker,
                                            delivery_hints=hints))

    if not resolved:
        # 全部行都是舞台指示 → 本镜无可念台词，按无 dialogue 处理（§4/§7：
        # 指示绝不进 spoken dialogue，也不构成对白镜）
        return None

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
        resolution=resolution,
        directions=tuple(directions))


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


def compile_prompt_with_native_dialogue(prompt: str, project_dir, project, shot, *,
                                        language_override: Optional[str] = None) -> str:
    """C1 生产编译入口（§4/§5）：dialogue 非空 → 默认启用 Native Dialogue Control
    （§4 默认语义）；language = resolve_dialogue_language precedence（§5）。
    无台词 → prompt 原样返回（§14：无 dialogue 行为不变——不触发 language 检查）。
    fail closed 时抛 DialogueControlError，调用方不得静默继续。"""
    if not split_dialogue_lines(getattr(shot, "dialogue", "") or ""):
        return prompt
    lang = resolve_dialogue_language(project, language_override)
    control = resolve_native_dialogue(project_dir, shot, language=lang)
    if control is None:
        return prompt
    return append_dialogue_block(prompt, control)


def diagnose_dialogue_batch(project_dir, project, shot_ids):
    """Return every deterministic dialogue error before enqueuing any media.

    Compiles ephemeral prompts only; canonical dialogue and cast are unchanged.
    """
    errors = {}
    for sid in dict.fromkeys(shot_ids):
        shot = project.get_shot(sid)
        if shot is None:
            continue
        try:
            compile_prompt_with_native_dialogue("", project_dir, project, shot)
        except DialogueControlError as exc:
            errors[sid] = exc.to_dict()
        except Exception as exc:
            errors[sid] = {"code": "dialogue_compile_failed", "detail": str(exc)[:400]}
    return errors
