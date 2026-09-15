# src/sequence/store.py
# Sequence S1 — output/sequences.json 读写（24.md §5/§9/§10/§24）。
#
# 🔴 复用项目既有 store 模式（与 src/audio/store.py 同构，15.md §6「不要发明第二套
#    concurrency architecture」）：
#    - 原子写 = src/core/atomic.atomic_write_json（单一来源，禁止裸写 JSON）
#    - 串行化 = src/core/write_lock.project_write_lock（同 project_name key，
#      与 project.json / dialogue_audio.json / resources.json 写路径同锁族）
#    - 读零副作用：无文件 → load_or_none 返回 None（纯读绝不创建文件）
#    - 坏 JSON / 未知 schema → SequenceError（fail closed，不静默重置）
#    - service 层做业务校验与 OCC，store 只管字节（A1 同分工）
#
# 独立文件、不进 project.json（24.md §2 独立模块：快速修改数据结构/快速调整 API；
# 先例：voice_profiles.json / dialogue_audio.json / resources.json 独立 store）。
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from src.core.atomic import atomic_write_json
from src.core.write_lock import project_write_lock

from .models import (
    SEQUENCE_STORE_SCHEMA_VERSION,
    SequenceEntry,
    SequenceError,
)


class SequenceStore:
    """sequences.json 读写（纯 sync；短临界区由调用方用 write_lock 串行化）。

    文档形状：
    {
      "schema_version": 1,
      "sequences": {
        "<sequence_id>": {"block": {...}, "render_versions": [...], "final_render": null}
      }
    }
    """

    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.dir = Path(project_dir)
        self.path = self.dir / "output" / "sequences.json"
        self.project_name = project_name or self.dir.name

    def write_lock(self):
        return project_write_lock(self.project_name)

    def exists(self) -> bool:
        return self.path.exists()

    # ============ load ============
    def load_or_none(self) -> Optional[Dict[str, SequenceEntry]]:
        """无文件 → None（零副作用，纯读绝不创建）。
        坏文件 / 未知 schema → SequenceError（诚实，不静默当「无 sequence」）。"""
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as e:
            raise SequenceError("store_read_failed", f"sequences.json 读取失败：{e}") from e
        except Exception as e:
            raise SequenceError(
                "store_corrupt", f"sequences.json 不是合法 JSON（不静默重置）：{e}") from e
        if not isinstance(raw, dict):
            raise SequenceError("store_corrupt", "sequences.json 根必须是对象")
        v = raw.get("schema_version")
        if v != SEQUENCE_STORE_SCHEMA_VERSION:
            raise SequenceError(
                "schema_unsupported",
                f"sequences.json schema_version={v!r} 不受支持"
                f"（当前 {SEQUENCE_STORE_SCHEMA_VERSION}）——fail closed，不静默迁移")
        seqs_raw = raw.get("sequences")
        if not isinstance(seqs_raw, dict):
            raise SequenceError("store_corrupt", "sequences.json 缺少 sequences 对象（fail closed）")
        return {sid: SequenceEntry.from_dict(e) for sid, e in seqs_raw.items()}

    def load(self) -> Dict[str, SequenceEntry]:
        """便捷只读入口：无文件 → {}。坏文件向上抛 SequenceError。"""
        doc = self.load_or_none()
        return doc if doc is not None else {}

    # ============ save ============
    def save(self, entries: Dict[str, SequenceEntry]) -> None:
        """整文档原子写。调用方必须在 project_write_lock 临界区内完成
        load → mutate → save（短临界区，绝不跨 ffmpeg/provider 持锁，24.md §23）。"""
        doc = {
            "schema_version": SEQUENCE_STORE_SCHEMA_VERSION,
            "sequences": {sid: e.to_dict() for sid, e in entries.items()},
        }
        atomic_write_json(self.path, doc)
