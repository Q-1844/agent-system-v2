# src/audio/voice_pack.py
# Audio V1 — Phase A1：speaker 解析 + VoiceGenerationReadiness + VoiceGenerationPack
# 编译（15.md §8/§9/§10）。
#
# 🔴 Dialogue 真相复用（15.md §3 强规则）：canonical text = Shot.dialogue 原样读取
#    （不 strip / 不改写 / 不翻译）。本项目业务模型中对话与 Shot 1:1（多句台词 =
#    split 产生新 shot、新身份证号 id），故 dialogue_id = shot_id 是稳定业务身份
#    （structure_service：id 是身份证号——插入不重排、删除不回收、move 不改 id；
#    index 只是显示号，不参与身份，非「shot_id+index 伪造」）。
#    不另建 DialogueLine 第二套真相（那需要 mirror Shot.dialogue 或大规模
#    Script Writer / Director / routers 迁移——前者违反 §3A，后者触发 STOP A）。
# 🔴 speaker 解析（derived only，canonical 是 Shot.characters，不改写它）：
#    - 显式 speaker 参数必须 ∈ shot.characters（显式人裁决，进 dialogue_fingerprint
#      与 generation_basis——它是生成依赖，不是第二真相）；
#    - 未显式时：characters 恰好 1 个 → 该角色；0 个 → no_speaker；
#      多个 → ambiguous_speaker（A1 无 speaker 字段，不猜）。
# 🔴 readiness 是纯派生（15.md §8）：绝不写 canonical status 字段作第二真相。
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from src.core.models import Project, Shot

from .models import (
    MINIMAX_PROVIDER,
    VOICE_APPROVAL_APPROVED,
    VOICE_READINESS_NOT_READY,
    VOICE_READINESS_READY,
    VOICE_READINESS_UNBOUND,
    CharacterVoiceProfile,
    VoiceGenerationPack,
    dialogue_fingerprint,
)


@dataclass
class SpeakerResolution:
    """speaker 解析结论（derived；canonical 仍是 Shot.characters）。"""
    speaker: Optional[str]      # None = 未能唯一确定
    code: str                   # resolved / explicit / no_speaker / ambiguous_speaker / invalid_speaker
    detail: str = ""


def resolve_speaker(shot: Shot, explicit_speaker: Optional[str] = None) -> SpeakerResolution:
    """从 Shot.characters 解析说话人（A1 规则见模块头）。绝不修改 Shot。"""
    chars = []
    for c in shot.characters or []:
        name = str(c or "").strip()
        if name and name not in chars:
            chars.append(name)
    if explicit_speaker:
        sp = str(explicit_speaker).strip()
        if sp not in chars:
            return SpeakerResolution(
                None, "invalid_speaker",
                f"显式 speaker {sp!r} 不在本镜出场角色 {chars} 中（不是本镜说话人）")
        return SpeakerResolution(sp, "explicit")
    if len(chars) == 1:
        return SpeakerResolution(chars[0], "resolved")
    if not chars:
        return SpeakerResolution(None, "no_speaker", "本镜没有出场角色，无法确定说话人")
    return SpeakerResolution(
        None, "ambiguous_speaker",
        f"本镜有多个出场角色 {chars} 且未显式指定说话人（A1 不猜，需显式裁决）")


@dataclass
class DialogueResolution:
    """Dialogue 解析结论（readiness 的 Dialogue 侧输入）。"""
    dialogue_id: str
    text: str                   # 原样（不改写）
    speaker_resolution: SpeakerResolution


def resolve_dialogue(project: Project, dialogue_id: str,
                     explicit_speaker: Optional[str] = None) -> DialogueResolution:
    """解析 canonical Dialogue：shot 存在性 + text 原样 + speaker。"""
    shot = project.get_shot(str(dialogue_id))
    if shot is None:
        return DialogueResolution(str(dialogue_id), "",
                                  SpeakerResolution(None, "dialogue_not_found",
                                                    f"dialogue_id 不存在: {dialogue_id}"))
    return DialogueResolution(str(dialogue_id), shot.dialogue or "",
                              resolve_speaker(shot, explicit_speaker))


def compute_readiness(project: Project,
                      profiles: Dict[str, CharacterVoiceProfile],
                      dialogue_id: str,
                      explicit_speaker: Optional[str] = None) -> dict:
    """VoiceGenerationReadiness（15.md §8，derived only）。

    - UNBOUND：speaker 角色没有 VoiceProfile；
    - NOT_READY：其余不可确定性解析（dialogue 不存在/空台词/speaker 问题/
      profile 未批准/缺 provider_voice_id/不支持的 provider）；
    - READY：Dialogue 与 approved CharacterVoiceProfile 都可确定性解析。
    """
    did = str(dialogue_id)
    base = {"dialogue_id": did, "status": VOICE_READINESS_NOT_READY,
            "code": "", "detail": "", "speaker": None,
            "voice_profile": None, "voice_profile_id": None}

    res = resolve_dialogue(project, did, explicit_speaker)
    if res.speaker_resolution.code == "dialogue_not_found":
        return {**base, "code": "dialogue_not_found",
                "detail": res.speaker_resolution.detail}
    if not res.text.strip():
        return {**base, "code": "empty_dialogue",
                "detail": "本镜 dialogue 为空（无台词）"}

    sr = res.speaker_resolution
    if sr.code in ("no_speaker", "ambiguous_speaker", "invalid_speaker"):
        return {**base, "code": sr.code, "detail": sr.detail}
    speaker = sr.speaker

    profile = next((p for p in profiles.values()
                    if p.character_name == speaker), None)
    if profile is None:
        return {**base, "status": VOICE_READINESS_UNBOUND, "code": "speaker_unbound",
                "detail": f"speaker 角色 {speaker!r} 没有 VoiceProfile", "speaker": speaker}
    if profile.approval_status != VOICE_APPROVAL_APPROVED:
        return {**base, "code": "profile_not_approved",
                "detail": f"VoiceProfile {profile.voice_profile_id} 未批准（draft）",
                "speaker": speaker,
                "voice_profile": profile.to_dict(),
                "voice_profile_id": profile.voice_profile_id}
    if not profile.provider_voice_id:
        return {**base, "code": "provider_voice_id_missing",
                "detail": f"VoiceProfile {profile.voice_profile_id} 缺少 provider_voice_id",
                "speaker": speaker,
                "voice_profile": profile.to_dict(),
                "voice_profile_id": profile.voice_profile_id}
    if profile.provider != MINIMAX_PROVIDER:
        return {**base, "code": "unsupported_provider",
                "detail": (f"A1 仅支持 provider={MINIMAX_PROVIDER}，"
                           f"profile 是 {profile.provider!r}"),
                "speaker": speaker,
                "voice_profile": profile.to_dict(),
                "voice_profile_id": profile.voice_profile_id}
    return {**base, "status": VOICE_READINESS_READY, "code": "ready",
            "speaker": speaker,
            "voice_profile": profile.to_dict(),
            "voice_profile_id": profile.voice_profile_id}


def compile_voice_pack(dialogue_resolution: DialogueResolution,
                       profile: CharacterVoiceProfile) -> VoiceGenerationPack:
    """编译 VoiceGenerationPack（15.md §9）。derived / ephemeral / 不持久化。
    同输入 → 同 pack（无 clock / 无随机 / 无临时路径）。caller 必须先确认 READY。"""
    speaker = dialogue_resolution.speaker_resolution.speaker or ""
    basis = build_generation_basis(dialogue_resolution, profile)
    return VoiceGenerationPack(
        dialogue_id=dialogue_resolution.dialogue_id,
        dialogue_fingerprint=basis["dialogue_fingerprint"],
        speaker_character_name=speaker,
        text=dialogue_resolution.text,          # 原样（§15：Adapter 不得改写）
        language="",                             # Dialogue 级 language canonical 未存在（seam）
        delivery_intent="",                      # §11：占位 seam，本阶段不解释
        language_boost=profile.language_boost,
        voice_profile_id=profile.voice_profile_id,
        voice_profile_revision=profile.revision(),
        provider_binding={
            "provider": profile.provider,
            "provider_voice_id": profile.provider_voice_id,
            "default_model": profile.default_model,
        },
        synthesis_defaults={
            "speed": profile.speed, "vol": profile.vol, "pitch": profile.pitch,
        },
        generation_basis=basis,
    )


def build_generation_basis(dialogue_resolution: DialogueResolution,
                           profile: CharacterVoiceProfile) -> dict:
    """generation_basis（15.md §10）：证明「这段声音基于哪一句 Dialogue + 哪一个
    VoiceProfile 生成」。deterministic / inspectable；无 clock / 无随机 UUID /
    无临时路径。"""
    speaker = dialogue_resolution.speaker_resolution.speaker or ""
    return {
        "schema": "voice_generation_basis_v1",
        "dialogue_id": dialogue_resolution.dialogue_id,
        "dialogue_fingerprint": dialogue_fingerprint(
            dialogue_resolution.dialogue_id, dialogue_resolution.text, speaker),
        "speaker_character_name": speaker,
        "voice_profile_id": profile.voice_profile_id,
        "voice_profile_revision": profile.revision(),
        "provider": profile.provider,
        "model": profile.default_model,
        "provider_voice_id": profile.provider_voice_id,
        "synthesis_settings_fingerprint": profile.synthesis_settings_fingerprint(),
    }


def basis_mismatches(basis: dict, expected: dict) -> list:
    """对比两个 basis 的语义键，返回不一致键列表（§21/§24 用；
    空 = 一致 = CURRENT / 可 dispatch）。"""
    keys = ("dialogue_id", "dialogue_fingerprint", "speaker_character_name",
            "voice_profile_id", "voice_profile_revision", "provider", "model",
            "provider_voice_id", "synthesis_settings_fingerprint")
    return [k for k in keys if (basis or {}).get(k) != (expected or {}).get(k)]
