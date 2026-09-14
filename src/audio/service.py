# src/audio/service.py
# Audio V1 — Phase A1：VoiceService（15.md §6/§20/§21/§23/§24/§25）。
#
# 职责：
#   - CharacterVoiceProfile CRUD + approve（OCC check-then-write，stale → reject
#     不 silent retry；与 visual_profile 同口径；sync 单写者上下文）
#   - Readiness / Pack（voice_pack 派生，derived only）
#   - 显式 generate 编排（§24 race protection：pre-build READY → compile →
#     pre-dispatch 重新确认 basis → 才 dispatch；相关依赖变化 →
#     dependency_changed_before_voice_generation，provider 调用 = 0）
#   - DialogueAudioVersion 落盘（raw + production WAV + probe，§17/§18）
#   - DialogueAudioFreshness（MISSING/CURRENT/STALE，actual-dependencies-only，
#     §21/§22）与 selection / approval（§20，显式 artifact identity，
#     严禁 filesystem newest wins）
#
# 🔴 硬规则（§23）：STALE 绝不自动 regenerate；generate 只能被显式调用。
# 🔴 生成不写 Visual 冻结域（§42）：只写 voice_profiles.json / dialogue_audio.json /
#    dialogue_audio/ 音频文件；project.json 只读。
# 🔴 选择语义：显式 generate 产生新版本并把 store 指针指到该显式 audio_version_id
#    （store 内显式 artifact identity，不是 mtime/glob）；approve 是独立的人类门。
# 🔴 profile sync 操作与 visual_profile 同约定：OCC check-then-write 由单写者上下文
#    保证（测试/驱动脚本）；async 侧（generate 落盘）持 project_write_lock 临界区。
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from src.core.store import ProjectStore

from .media_ops import (
    AudioMediaError,
    ffprobe_audio,
    normalize_to_production_wav,
    save_raw_asset,
)
from .minimax_adapter import VoiceProviderError  # structured 错误原样透传 caller（§25）
from .models import (
    MINIMAX_DEFAULT_MODEL,
    MINIMAX_PROVIDER,
    VOICE_APPROVAL_APPROVED,
    CharacterVoiceProfile,
    DialogueAudioVersion,
    VoiceSchemaError,
    dialogue_fingerprint,
)
from .store import DialogueAudioStore, VoiceProfileStore
from .voice_pack import (
    basis_mismatches,
    build_generation_basis,
    compile_voice_pack,
    compute_readiness,
    resolve_dialogue,
)


class VoiceServiceError(Exception):
    """Voice 域 structured 错误（15.md §25 错误契约 + service 确定性错误）。
    code 见各 raise 点；detail 携带安全上下文，绝不含 secret。"""

    def __init__(self, code: str, message: str, detail: Optional[dict] = None):
        self.code = code
        self.detail = dict(detail or {})
        super().__init__(f"[{code}] {message}")


class VoiceService:
    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.dir = Path(project_dir)
        self.project_name = project_name or self.dir.name
        self.project_store = ProjectStore(self.dir)
        self.profile_store = VoiceProfileStore(self.dir, self.project_name)
        self.audio_store = DialogueAudioStore(self.dir, self.project_name)

    # ==================================================================
    # ============ CharacterVoiceProfile canonical（§4/§5/§6）==========
    # ==================================================================

    def _load_project(self):
        return self.project_store.load()

    def _load_profiles(self) -> Dict[str, CharacterVoiceProfile]:
        return self.profile_store.load_profiles()

    def create_profile(self, character_name: str, provider_voice_id: str, *,
                       label: str = "", provider: str = MINIMAX_PROVIDER,
                       default_model: str = MINIMAX_DEFAULT_MODEL,
                       language_boost: str = "", speed: float = 1.0,
                       vol: float = 1.0, pitch: int = 0,
                       source_kind: str = "system") -> dict:
        """创建角色 VoiceProfile（draft）。一角色一当前声音身份（§4「当前批准
        使用的声音身份」）；重复创建 → profile_exists（走 update）。"""
        character_name = str(character_name or "").strip()
        if not character_name:
            return {"ok": False, "error": "invalid_character",
                    "detail": "character_name 不能为空"}
        project = self._load_project()
        if project.get_character(character_name) is None:
            return {"ok": False, "error": "invalid_character",
                    "detail": f"角色不存在: {character_name!r}"}
        profiles = self._load_profiles()
        if any(p.character_name == character_name for p in profiles.values()):
            return {"ok": False, "error": "profile_exists",
                    "detail": f"角色 {character_name!r} 已有 VoiceProfile（update 走 OCC）"}
        try:
            profile = CharacterVoiceProfile(
                voice_profile_id=f"vp_{character_name}",
                character_name=character_name,
                label=str(label or ""),
                provider=str(provider or MINIMAX_PROVIDER),
                provider_voice_id=str(provider_voice_id or ""),
                default_model=str(default_model or MINIMAX_DEFAULT_MODEL),
                language_boost=str(language_boost or ""),
                speed=speed, vol=vol, pitch=pitch,
                source_kind=str(source_kind or "system"),
            )
        except VoiceSchemaError as e:
            return {"ok": False, "error": "validation_error", "detail": str(e)}
        self.profile_store.save({**profiles, profile.voice_profile_id: profile})
        return {"ok": True, "voice_profile": profile.to_dict(),
                "revision": profile.revision()}

    def get_profile(self, voice_profile_id: str) -> Optional[CharacterVoiceProfile]:
        return self._load_profiles().get(str(voice_profile_id))

    def get_profile_by_character(self, character_name: str) -> Optional[CharacterVoiceProfile]:
        name = str(character_name or "").strip()
        return next((p for p in self._load_profiles().values()
                     if p.character_name == name), None)

    def list_profiles(self) -> Dict[str, dict]:
        return {pid: p.to_dict() for pid, p in self._load_profiles().items()}

    # update 白名单：语义字段（决定声音/revision）；created_at 等不可改。
    _UPDATABLE = ("provider", "provider_voice_id", "default_model",
                  "language_boost", "speed", "vol", "pitch", "label")

    def update_profile(self, voice_profile_id: str, expected_revision: str,
                       **fields) -> dict:
        """OCC update（§6 复用项目 OCC 模式）：expected_revision 与当前语义指纹
        一致才写；stale → reject，不 silent retry。"""
        op = "voice_profile.update"
        profiles = self._load_profiles()
        current = profiles.get(str(voice_profile_id))
        if current is None:
            return {"ok": False, "operation": op, "error": "not_found",
                    "detail": f"VoiceProfile 不存在: {voice_profile_id}"}
        cur_rev = current.revision()
        if str(expected_revision) != cur_rev:
            return {"ok": False, "operation": op, "error": "stale_revision",
                    "detail": "expected_revision 与当前 VoiceProfile revision 不一致",
                    "expected_revision": expected_revision,
                    "current_revision": cur_rev}
        patch: Dict[str, Any] = {}
        for k in self._UPDATABLE:
            if k in fields and fields[k] is not None:
                patch[k] = fields[k]
        if not patch:
            return {"ok": False, "operation": op, "error": "validation_error",
                    "detail": "没有可更新字段"}
        import time as _time
        try:
            candidate = CharacterVoiceProfile(
                **{**current.to_dict(), **patch,
                   "updated_at": _time.time()},
            )
        except VoiceSchemaError as e:
            return {"ok": False, "operation": op, "error": "validation_error",
                    "detail": str(e)}
        profiles[current.voice_profile_id] = candidate
        self.profile_store.save(profiles)
        return {"ok": True, "operation": op,
                "voice_profile": candidate.to_dict(),
                "revision": candidate.revision()}

    def approve_profile(self, voice_profile_id: str, expected_revision: str) -> dict:
        """批准（§4 approval_status 门）。门：provider_voice_id 必须非空。"""
        op = "voice_profile.approve"
        profiles = self._load_profiles()
        current = profiles.get(str(voice_profile_id))
        if current is None:
            return {"ok": False, "operation": op, "error": "not_found",
                    "detail": f"VoiceProfile 不存在: {voice_profile_id}"}
        if str(expected_revision) != current.revision():
            return {"ok": False, "operation": op, "error": "stale_revision",
                    "detail": "expected_revision 与当前 revision 不一致",
                    "expected_revision": expected_revision,
                    "current_revision": current.revision()}
        if not current.provider_voice_id:
            return {"ok": False, "operation": op, "error": "cannot_approve",
                    "detail": "provider_voice_id 缺失，不能批准"}
        import time as _time
        approved = CharacterVoiceProfile(
            **{**current.to_dict(), "approval_status": VOICE_APPROVAL_APPROVED,
               "updated_at": _time.time()})
        profiles[current.voice_profile_id] = approved
        self.profile_store.save(profiles)
        return {"ok": True, "operation": op,
                "voice_profile": approved.to_dict(),
                "revision": approved.revision()}

    # ==================================================================
    # ============ Readiness / Pack（§8/§9，derived only）==============
    # ==================================================================

    def readiness(self, dialogue_id: str, speaker: Optional[str] = None) -> dict:
        project = self._load_project()
        return compute_readiness(project, self._load_profiles(),
                                 str(dialogue_id), speaker)

    def compile_pack(self, dialogue_id: str,
                     speaker: Optional[str] = None) -> VoiceGenerationPack:
        r = self.readiness(dialogue_id, speaker)
        if r["status"] != "ready":
            raise VoiceServiceError(
                "voice_not_ready", f"dialogue {dialogue_id} 未就绪: {r['code']}",
                {"readiness": r})
        res = resolve_dialogue(self._load_project(), str(dialogue_id), speaker)
        profile = self.get_profile_by_character(res.speaker_resolution.speaker)
        return compile_voice_pack(res, profile)

    # ==================================================================
    # ============ 显式生成（§13/§17/§23/§24）==========================
    # ==================================================================

    async def generate(self, dialogue_id: str, adapter, *,
                       speaker: Optional[str] = None,
                       before_redispatch_hook: Optional[Callable] = None) -> dict:
        """显式生成一条 Dialogue 音频（§23：只能显式调用，绝不自动）。

        Race protection（§24）：
          1. pre-build：确认 READY；
          2. compile pack（含 basis）；
          3. pre-dispatch：重新 load canonical，重算 basis 与 pack 一致才 dispatch；
             不一致 → dependency_changed_before_voice_generation，provider 调用 = 0。
        before_redispatch_hook：测试 seam（在 pre-dispatch 校验【前】注入 canonical
        变更，生产代码绝不传）。

        落盘（§17/§18/§42）：raw provider 音频 + production WAV + ffprobe 实测；
        只写 Voice 自己的 store 与音频文件。
        """
        did = str(dialogue_id)
        # ---- pre-build：READY 才继续 ----
        r = self.readiness(did, speaker)
        if r["status"] != "ready":
            raise VoiceServiceError(
                "voice_not_ready", f"dialogue {did} 未就绪: {r['code']}",
                {"readiness": r})
        project = self._load_project()
        profiles = self._load_profiles()
        res = resolve_dialogue(project, did, speaker)
        profile = self.get_profile_by_character(res.speaker_resolution.speaker)
        pack = compile_voice_pack(res, profile)

        # ---- 测试 seam：在 pre-dispatch 校验前注入 canonical 变更 ----
        if before_redispatch_hook is not None:
            await before_redispatch_hook(pack)

        # ---- pre-dispatch：重读 canonical，重算 basis，与 pack 不一致即拒绝 ----
        project2 = self._load_project()
        profiles2 = self._load_profiles()
        res2 = resolve_dialogue(project2, did, speaker)
        profile2 = (self.get_profile_by_character(res2.speaker_resolution.speaker)
                    if res2.speaker_resolution.speaker else None)
        if (res2.speaker_resolution.speaker is None or profile2 is None
                or not res2.text.strip()):
            raise VoiceServiceError(
                "dependency_changed_before_voice_generation",
                "pre-dispatch 校验失败：Dialogue/说话人/VoiceProfile 在生成窗口内变化",
                {"dialogue_id": did, "stage": "pre_dispatch",
                 "speaker_resolution": res2.speaker_resolution.code})
        expected_basis = build_generation_basis(res2, profile2)
        mismatch = basis_mismatches(pack.generation_basis, expected_basis)
        if mismatch:
            raise VoiceServiceError(
                "dependency_changed_before_voice_generation",
                f"生成窗口内相关依赖变化（provider 调用=0）: {mismatch}",
                {"dialogue_id": did, "stage": "pre_dispatch",
                 "mismatched_keys": mismatch})

        # ---- dispatch（provider 调用不持 canonical 锁；§33 锁纪律同族）----
        # VoiceProviderError（provider_not_configured / provider_request_failed /
        # provider_quota_exceeded / provider_invalid_voice / audio_decode_failed）
        # 原样透传 caller（§25 structured contract，不做二次包装）。
        result = await adapter.synthesize(pack)
        raw_bytes = result["audio"]

        # ---- 落盘（raw → production WAV → probe → store）----
        async with self.audio_store.write_lock():
            doc = self.audio_store.load_doc()
            entry = self.audio_store.get_entry(doc, did)
            av_id = self._allocate_audio_version_id(entry, did)
            audio_root = self.dir / "output" / "dialogue_audio"
            raw_info = save_raw_asset(
                raw_bytes, audio_root / "raw" / f"{av_id}.mp3")
            try:
                wav_info = normalize_to_production_wav(
                    raw_bytes, audio_root / "production" / f"{av_id}.wav")
            except AudioMediaError as e:
                raise VoiceServiceError(e.code, e.message,
                                        {"audio_version_id": av_id}) from e
            raw_probe = ffprobe_audio(raw_info["path"])
            wav_probe = ffprobe_audio(wav_info["path"])
            version = DialogueAudioVersion(
                audio_version_id=av_id,
                dialogue_id=did,
                speaker_character_name=pack.speaker_character_name,
                voice_profile_id=pack.voice_profile_id,
                voice_profile_revision=pack.voice_profile_revision,
                provider=pack.provider_binding["provider"],
                model=pack.provider_binding["default_model"],
                provider_voice_id=pack.provider_binding["provider_voice_id"],
                generation_basis=dict(pack.generation_basis),
                provider_request_snapshot=dict(result.get("request_snapshot") or {}),
                provider_extra_info=dict(result.get("provider_extra_info") or {}),
                raw_asset={**raw_info,
                           "ffprobe": raw_probe or {"error": "ffprobe_unavailable"}},
                production_wav={**wav_info,
                                "ffprobe": wav_probe or {"error": "ffprobe_unavailable"}},
                word_timestamps=list(result.get("word_timestamps") or []),
                word_timestamps_note=str(result.get("word_timestamps_note") or ""),
                technical_probe={
                    "latency_ms": result.get("latency_ms"),
                    "raw": raw_probe or {"error": "ffprobe_unavailable"},
                    "production_wav": wav_probe or {"error": "ffprobe_unavailable"},
                },
                created_at=time.time(),
            )
            entry["audio_versions"].append(version)
            # 显式生成 = 显式把当前选择指向新版本 artifact id（store 指针，
            # 非 mtime/glob）；approve 独立人类门，换选择即回未批准。
            entry["selected_audio_version_id"] = av_id
            entry["approved"] = False
            doc.setdefault("dialogues", {})[did] = {
                "audio_versions": [v.to_dict() for v in entry["audio_versions"]],
                "selected_audio_version_id": entry["selected_audio_version_id"],
                "approved": entry["approved"],
            }
            self.audio_store.save_doc(doc)

        return {"ok": True, "audio_version_id": av_id,
                "audio_version": version.to_dict(),
                "freshness": self.freshness(did, speaker),
                "usage_characters":
                    (result.get("provider_extra_info") or {}).get("usage_characters")}

    @staticmethod
    def _allocate_audio_version_id(entry: dict, dialogue_id: str) -> str:
        """av 序号单调分配（删除不回收，身份证号纪律同 structure_service）。"""
        max_n = 0
        for v in entry.get("audio_versions") or []:
            vid = getattr(v, "audio_version_id", "") or ""
            if vid.startswith(f"{dialogue_id}_av"):
                try:
                    max_n = max(max_n, int(vid.rsplit("_av", 1)[-1]))
                except ValueError:
                    pass
        return f"{dialogue_id}_av{max_n + 1:02d}"

    # ==================================================================
    # ============ Freshness（§21/§22）/ Selection（§20）===============
    # ==================================================================

    def freshness(self, dialogue_id: str, speaker: Optional[str] = None) -> dict:
        """DialogueAudioFreshness（derived only）。actual dependencies only：
        只比对本句 Dialogue + 本 speaker 的 VoiceProfile（§22：无关角色变化绝不
        false-stale；不比整份 voice_profiles.json 文档）。"""
        did = str(dialogue_id)
        base = {"dialogue_id": did, "status": "missing", "code": "",
                "detail": "", "mismatched_keys": [],
                "selected_audio_version_id": None}
        entry = self.audio_store.get_entry(self.audio_store.load_doc(), did)
        selected_id = entry.get("selected_audio_version_id")
        if not selected_id:
            return {**base, "code": "no_selection",
                    "detail": "没有 selected audio"}
        selected = next((v for v in entry["audio_versions"]
                         if v.audio_version_id == selected_id), None)
        if selected is None:
            return {**base, "code": "selected_version_not_found",
                    "detail": f"selected audio_version 不存在: {selected_id}"}
        base["selected_audio_version_id"] = selected_id

        # 重算 expected basis：用 version 留痕的说话人裁决复现同一解析路径
        stored_speaker = (selected.speaker_character_name
                          or (speaker or "")).strip() or None
        project = self._load_project()
        res = resolve_dialogue(project, did, stored_speaker)
        if res.speaker_resolution.code in ("dialogue_not_found", "invalid_speaker"):
            return {**base, "status": "stale", "code": res.speaker_resolution.code,
                    "detail": res.speaker_resolution.detail,
                    "mismatched_keys": ["dialogue_id"]}
        if not res.text.strip():
            return {**base, "status": "stale", "code": "empty_dialogue",
                    "detail": "本镜 dialogue 已被清空",
                    "mismatched_keys": ["dialogue_fingerprint"]}
        profile = self.get_profile_by_character(res.speaker_resolution.speaker)
        if profile is None:
            return {**base, "status": "stale", "code": "profile_removed",
                    "detail": (f"speaker {res.speaker_resolution.speaker!r} 的 "
                               "VoiceProfile 已不存在"),
                    "mismatched_keys": ["voice_profile_id"]}
        expected = build_generation_basis(res, profile)
        mismatch = basis_mismatches(selected.generation_basis, expected)
        if mismatch:
            return {**base, "status": "stale", "code": "dependency_changed",
                    "detail": f"实际依赖已变化: {mismatch}",
                    "mismatched_keys": mismatch}
        return {**base, "status": "current", "code": "current", "detail": ""}

    def list_audio(self, dialogue_id: str) -> dict:
        """列出某 dialogue 的全部 audio versions + 当前选择（§20）。"""
        did = str(dialogue_id)
        entry = self.audio_store.get_entry(self.audio_store.load_doc(), did)
        selected_id = entry.get("selected_audio_version_id")
        return {
            "dialogue_id": did,
            "audio_versions": [
                {**v.to_dict(),
                 "review_status": ("selected" if v.audio_version_id == selected_id
                                   else "unselected")}
                for v in entry["audio_versions"]
            ],
            "selected_audio_version_id": selected_id,
            "approved": entry.get("approved", False),
        }

    def select_audio(self, dialogue_id: str, audio_version_id: str) -> dict:
        """显式选择版本（store 内显式 artifact identity 指针；换选择 → 回未批准）。"""
        did = str(dialogue_id)
        av = str(audio_version_id)

        async def _do():
            async with self.audio_store.write_lock():
                doc = self.audio_store.load_doc()
                entry = self.audio_store.get_entry(doc, did)
                if not any(v.audio_version_id == av for v in entry["audio_versions"]):
                    return {"ok": False, "error": "audio_version_not_found",
                            "detail": f"audio_version 不存在: {av}"}
                doc.setdefault("dialogues", {})[did] = {
                    "audio_versions": [v.to_dict() for v in entry["audio_versions"]],
                    "selected_audio_version_id": av,
                    "approved": False,
                }
                self.audio_store.save_doc(doc)
                return {"ok": True, "dialogue_id": did,
                        "selected_audio_version_id": av, "approved": False}

        return _run(_do())

    def approve_selected(self, dialogue_id: str) -> dict:
        """批准当前 selected 版本（§20 人类门；供未来 lip-sync 消费依据）。"""
        did = str(dialogue_id)

        async def _do():
            async with self.audio_store.write_lock():
                doc = self.audio_store.load_doc()
                entry = self.audio_store.get_entry(doc, did)
                if not entry.get("selected_audio_version_id"):
                    return {"ok": False, "error": "no_selection",
                            "detail": "没有 selected audio 可批准"}
                doc.setdefault("dialogues", {})[did] = {
                    "audio_versions": [v.to_dict() for v in entry["audio_versions"]],
                    "selected_audio_version_id": entry["selected_audio_version_id"],
                    "approved": True,
                }
                self.audio_store.save_doc(doc)
                return {"ok": True, "dialogue_id": did,
                        "selected_audio_version_id": entry["selected_audio_version_id"],
                        "approved": True}

        return _run(_do())


def _run(coro):
    """sync 入口跑 async 临界区（测试/驱动单写者上下文；API 层未来直接 await）。"""
    import asyncio
    return asyncio.run(coro)
