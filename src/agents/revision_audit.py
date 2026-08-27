# src/agents/revision_audit.py
# AI-7D-R §16 Level-1 直接编辑的审计/快照（轻量 undo/checkpoint + audit trail）。
#
# 直接编辑仍走受控能力（permission + 原子 canonical 写 + 审计），不绕内核（§2/§15/§16）。
# 这里记录每次直接编辑的 before/after，供审计与（未来）撤销。非 ChangeSession/Workspace 重型
# 修订机制——Level-1 是低风险可逆文本编辑，用最小耐久记录即可。
#
# 持久化：output/agents/direct_edit_audit.jsonl（append-only，每行一条）。
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional


class DirectEditAuditStore:
    """项目级直接编辑审计（append-only JSONL）。"""

    def __init__(self, project_dir, project_name: str):
        self.project_dir = Path(project_dir)
        self.project_name = project_name
        self.root = self.project_dir / "output" / "agents"
        self.path = self.root / "direct_edit_audit.jsonl"

    def record(self, *, agent_id: str, field: str, before: Any, after: Any,
               instruction: str, message_id: Optional[str] = None) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": time.time(), "project": self.project_name, "agent_id": agent_id,
            "field": field, "instruction": (instruction or "")[:500],
            "before": before, "after": after, "message_id": message_id,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def recent(self, limit: int = 10) -> list:
        if not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            return [json.loads(ln) for ln in lines[-limit:] if ln.strip()]
        except Exception:
            return []
