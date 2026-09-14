# src/audio/store.py
# Audio V1 — Phase A1：两个 canonical store（15.md §6/§19）。
#
#   output/voice_profiles.json   CharacterVoiceProfile canonical（§6）
#   output/dialogue_audio.json   DialogueAudioVersion artifact store（§19）
#
# 🔴 复用项目既有 store 模式（与 visual_profile 同构，15.md §6「不要发明第二套
#    concurrency architecture」）：
#    - 原子写 = src/core/atomic.atomic_write_json（单一来源，禁止裸写 JSON）
#    - 串行化 = src/core/write_lock.project_write_lock（同 project_name key，
#      与 project.json / resources.json / project_profile.json 写路径同锁族）
#    - 读零副作用：无文件 → load_or_none 返回 None（纯读绝不创建文件）
#    - 坏 JSON / 未知 schema → VoiceSchemaError（fail closed，不静默重置）
#    - service 层做 OCC check-then-write，store 只管字节（visual_profile 同分工）
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from src.core.atomic import atomic_write_json
from src.core.write_lock import project_write_lock

from .models import (
    DIALOGUE_AUDIO_SCHEMA_VERSION,
    VOICE_PROFILE_SCHEMA_VERSION,
    CharacterVoiceProfile,
    DialogueAudioVersion,
    VoiceSchemaError,
    parse_audio_version,
    parse_voice_profile,
)


class VoiceProfileStore:
    """voice_profiles.json 读写（纯 sync；OCC 判定在 service，store 只管字节）。"""

    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.dir = Path(project_dir)
        self.path = self.dir / "output" / "voice_profiles.json"
        self.project_name = project_name or self.dir.name

    def write_lock(self):
        return project_write_lock(self.project_name)

    def exists(self) -> bool:
        return self.path.exists()

    # ============ load ============
    def load_or_none(self) -> Optional[Dict[str, CharacterVoiceProfile]]:
        """无文件 → None（零副作用，纯读绝不创建）。
        坏文件 → VoiceSchemaError（诚实，不静默当「无 profile」）。"""
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as e:
            raise VoiceSchemaError(f"voice_profiles.json 读取失败：{e}") from e
        except Exception as e:
            raise VoiceSchemaError(
                f"voice_profiles.json 不是合法 JSON（不静默重置）：{e}") from e
        if not isinstance(raw, dict):
            raise VoiceSchemaError("voice_profiles.json 根必须是对象")
        v = raw.get("schema_version")
        if v != VOICE_PROFILE_SCHEMA_VERSION:
            raise VoiceSchemaError(
                f"voice_profiles.json schema_version={v!r} 不受支持"
                f"（当前 {VOICE_PROFILE_SCHEMA_VERSION}）——fail closed")
        profiles_raw = raw.get("profiles")
        if not isinstance(profiles_raw, dict):
            raise VoiceSchemaError("voice_profiles.json 缺少 profiles 对象（fail closed）")
        return {pid: parse_voice_profile(p) for pid, p in profiles_raw.items()}

    def load_profiles(self) -> Dict[str, CharacterVoiceProfile]:
        """便捷只读入口：unspecified → {}。坏文件向上抛 VoiceSchemaError。"""
        doc = self.load_or_none()
        return doc if doc is not None else {}

    # ============ save ============
    def save(self, profiles: Dict[str, CharacterVoiceProfile]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, {
            "schema_version": VOICE_PROFILE_SCHEMA_VERSION,
            "profiles": {pid: p.to_dict() for pid, p in profiles.items()},
        })


class DialogueAudioStore:
    """dialogue_audio.json 读写（生成 artifact metadata；不是 Dialogue canonical）。"""

    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.dir = Path(project_dir)
        self.path = self.dir / "output" / "dialogue_audio.json"
        self.project_name = project_name or self.dir.name

    def write_lock(self):
        return project_write_lock(self.project_name)

    def exists(self) -> bool:
        return self.path.exists()

    # ============ load ============
    def load_or_none(self) -> Optional[dict]:
        """无文件 → None。坏文件 / 未知 schema → VoiceSchemaError。
        doc 形状：{"schema_version": 1,
                   "dialogues": {dialogue_id: {"audio_versions": [version_dict...],
                                               "selected_audio_version_id": str|None,
                                               "approved": bool}}}"""
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as e:
            raise VoiceSchemaError(f"dialogue_audio.json 读取失败：{e}") from e
        except Exception as e:
            raise VoiceSchemaError(
                f"dialogue_audio.json 不是合法 JSON（不静默重置）：{e}") from e
        if not isinstance(raw, dict):
            raise VoiceSchemaError("dialogue_audio.json 根必须是对象")
        v = raw.get("schema_version")
        if v != DIALOGUE_AUDIO_SCHEMA_VERSION:
            raise VoiceSchemaError(
                f"dialogue_audio.json schema_version={v!r} 不受支持"
                f"（当前 {DIALOGUE_AUDIO_SCHEMA_VERSION}）——fail closed")
        dialogues = raw.get("dialogues")
        if not isinstance(dialogues, dict):
            raise VoiceSchemaError("dialogue_audio.json 缺少 dialogues 对象（fail closed）")
        return raw

    def load_doc(self) -> dict:
        """便捷只读入口：unspecified → 空 doc 形状（不落盘）。"""
        doc = self.load_or_none()
        if doc is None:
            return {"schema_version": DIALOGUE_AUDIO_SCHEMA_VERSION, "dialogues": {}}
        return doc

    # ============ save ============
    def save_doc(self, doc: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, doc)

    # ============ dialogue 条目 helpers ============
    @staticmethod
    def get_entry(doc: dict, dialogue_id: str) -> dict:
        """返回某 dialogue 的条目（versions / selected 指针 / approved）。
        不存在 → 空条目（不落盘）。"""
        entry = doc.get("dialogues", {}).get(dialogue_id)
        if not isinstance(entry, dict):
            return {"audio_versions": [], "selected_audio_version_id": None,
                    "approved": False}
        versions = [parse_audio_version(v) for v in entry.get("audio_versions") or []]
        return {"audio_versions": versions,
                "selected_audio_version_id": entry.get("selected_audio_version_id"),
                "approved": bool(entry.get("approved", False))}
