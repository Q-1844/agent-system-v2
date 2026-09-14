# src/audio/av_sync/store.py
# Audio V1 — Phase A2：DialogueVideoSyncVersion artifact store（16.md §14/§15）。
#
# output/dialogue_video_sync.json
#   {"schema_version": 1,
#    "dialogues": {dialogue_id: {"sync_versions": [version...],
#                                "selected_sync_version_id": str|None,
#                                "approved": bool}}}
#
# output/dialogue_video_sync/{raw,production,working,evidence}/ 音视频产物。
#
# 🔴 与 A1 store 同构（复用项目 store 模式，不发明第二套并发架构）：
#    atomic_write_json + project_write_lock + load_or_none 读零副作用 + fail closed。
# 🔴 selection = 显式 selected_sync_version_id 指针（严禁 glob/mtime/newest）；
#    approval 独立（generate 不自动 approve，§15）。
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from src.core.atomic import atomic_write_json
from src.core.write_lock import project_write_lock

from .models import DIALOGUE_VIDEO_SYNC_SCHEMA_VERSION, parse_sync_version


class DialogueVideoSyncStore:
    """dialogue_video_sync.json 读写（纯 sync；OCC/临界区在 service，store 只管字节）。"""

    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.dir = Path(project_dir)
        self.path = self.dir / "output" / "dialogue_video_sync.json"
        self.project_name = project_name or self.dir.name
        self.artifact_root = self.dir / "output" / "dialogue_video_sync"

    def write_lock(self):
        return project_write_lock(self.project_name)

    def exists(self) -> bool:
        return self.path.exists()

    # ============ load ============
    def load_or_none(self) -> Optional[dict]:
        """无文件 → None（零副作用）。坏文件 / 未知 schema → ValueError（fail closed，
        不静默重置——与 A1/visual_profile 同口径）。"""
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as e:
            raise ValueError(f"dialogue_video_sync.json 读取失败：{e}") from e
        except Exception as e:
            raise ValueError(
                f"dialogue_video_sync.json 不是合法 JSON（不静默重置）：{e}") from e
        if not isinstance(raw, dict):
            raise ValueError("dialogue_video_sync.json 根必须是对象")
        v = raw.get("schema_version")
        if v != DIALOGUE_VIDEO_SYNC_SCHEMA_VERSION:
            raise ValueError(
                f"dialogue_video_sync.json schema_version={v!r} 不受支持"
                f"（当前 {DIALOGUE_VIDEO_SYNC_SCHEMA_VERSION}）——fail closed")
        if not isinstance(raw.get("dialogues"), dict):
            raise ValueError("dialogue_video_sync.json 缺少 dialogues 对象（fail closed）")
        return raw

    def load_doc(self) -> dict:
        """便捷只读入口：unspecified → 空 doc 形状（不落盘）。"""
        doc = self.load_or_none()
        if doc is None:
            return {"schema_version": DIALOGUE_VIDEO_SYNC_SCHEMA_VERSION, "dialogues": {}}
        return doc

    # ============ save ============
    def save_doc(self, doc: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, doc)

    # ============ dialogue 条目 helpers ============
    def get_entry(self, doc: dict, dialogue_id: str) -> dict:
        entry = doc.get("dialogues", {}).get(str(dialogue_id))
        if not isinstance(entry, dict):
            return {"sync_versions": [], "selected_sync_version_id": None,
                    "approved": False}
        versions = [parse_sync_version(v) for v in entry.get("sync_versions") or []]
        return {"sync_versions": versions,
                "selected_sync_version_id": entry.get("selected_sync_version_id"),
                "approved": bool(entry.get("approved", False))}

    # ============ 路径布局（§14）============
    def raw_dir(self) -> Path:
        return self.artifact_root / "raw"

    def production_dir(self) -> Path:
        return self.artifact_root / "production"

    def working_dir(self) -> Path:
        return self.artifact_root / "working"

    def evidence_dir(self) -> Path:
        return self.artifact_root / "evidence"

    @staticmethod
    def next_sync_version_id(entry: dict, dialogue_id: str) -> str:
        """sync 序号单调分配（删除不回收，身份证号纪律）。"""
        max_n = 0
        for v in entry.get("sync_versions") or []:
            vid = getattr(v, "sync_version_id", "") or ""
            if vid.startswith(f"{dialogue_id}_sync"):
                try:
                    max_n = max(max_n, int(vid.rsplit("_sync", 1)[-1]))
                except ValueError:
                    pass
        return f"{dialogue_id}_sync{max_n + 1:02d}"
