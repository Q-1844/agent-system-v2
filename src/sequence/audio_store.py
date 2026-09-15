# src/sequence/audio_store.py
# Sequence S2 — output/sequence_audio_plans.json 读写（25.md §7/§29）。
#
# 🔴 独立文件、不进 sequences.json（§29：SequenceBlock 与 AudioPlan 是两个
#    revision domain——物理分离让 S1 store 零改动、两域互不污染；A1 双 store
#    先例：voice_profiles.json / dialogue_audio.json）。
# 🔴 复用项目既有 store 模式（S1 store 同构）：atomic_write_json + 同 project_name
#    write_lock 族 + schema fail-closed。空 layers 的 plan 不落盘（canonical 归一化：
#    「空 plan」≡「无 plan」，避免双真相——S2 语义由 service 层保证）。
# 🔴 并发：与 S1 store 同一 per-project threading.Lock 族（service 层共用
#    _store_lock；事件循环 + JobQueue worker 线程双上下文）。
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from src.core.atomic import atomic_write_json
from src.core.write_lock import project_write_lock

from .audio_models import (
    SEQUENCE_AUDIO_PLAN_STORE_SCHEMA_VERSION,
    SequenceAudioPlan,
    SequenceError,
)


class SequenceAudioPlanStore:
    """sequence_audio_plans.json 读写（纯 sync；临界区由 service 层锁串行化）。

    文档形状：
    {
      "schema_version": 1,
      "plans": { "<sequence_id>": {...SequenceAudioPlan...} }
    }
    """

    def __init__(self, project_dir, project_name: Optional[str] = None):
        self.dir = Path(project_dir)
        self.path = self.dir / "output" / "sequence_audio_plans.json"
        self.project_name = project_name or self.dir.name

    def write_lock(self):
        """A1/S1 同族 project write lock（router async 上下文可用）。"""
        return project_write_lock(self.project_name)

    def exists(self) -> bool:
        return self.path.exists()

    # ============ load ============
    def load_or_none(self) -> Optional[Dict[str, SequenceAudioPlan]]:
        """无文件 → None（零副作用）。坏文件 / 未知 schema → fail closed。"""
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as e:
            raise SequenceError("store_read_failed",
                                f"sequence_audio_plans.json 读取失败：{e}") from e
        except Exception as e:
            raise SequenceError(
                "store_corrupt",
                f"sequence_audio_plans.json 不是合法 JSON（不静默重置）：{e}") from e
        if not isinstance(raw, dict):
            raise SequenceError("store_corrupt", "sequence_audio_plans.json 根必须是对象")
        v = raw.get("schema_version")
        if v != SEQUENCE_AUDIO_PLAN_STORE_SCHEMA_VERSION:
            raise SequenceError(
                "schema_unsupported",
                f"sequence_audio_plans.json schema_version={v!r} 不受支持"
                f"（当前 {SEQUENCE_AUDIO_PLAN_STORE_SCHEMA_VERSION}）——fail closed")
        plans_raw = raw.get("plans")
        if not isinstance(plans_raw, dict):
            raise SequenceError("store_corrupt",
                                "sequence_audio_plans.json 缺少 plans 对象（fail closed）")
        return {sid: SequenceAudioPlan.from_dict(p) for sid, p in plans_raw.items()}

    def load(self) -> Dict[str, SequenceAudioPlan]:
        doc = self.load_or_none()
        return doc if doc is not None else {}

    def plan_for(self, sequence_id: str) -> Optional[SequenceAudioPlan]:
        """读某 sequence 的 plan（None = 无 plan → S1 语义，25.md §25）。"""
        return self.load().get(sequence_id)

    # ============ save ============
    def save(self, plans: Dict[str, SequenceAudioPlan]) -> None:
        """整文档原子写。空 layers 的 plan 不落盘（归一化，见模块头）。"""
        persisted = {sid: p for sid, p in plans.items() if p.layers}
        doc = {
            "schema_version": SEQUENCE_AUDIO_PLAN_STORE_SCHEMA_VERSION,
            "plans": {sid: p.to_dict() for sid, p in persisted.items()},
        }
        atomic_write_json(self.path, doc)
