# src/agents/work_memory.py
# AI-7D-R §10.2 共享工作记忆（Shared Work Memory）。
#
# 项目级、结构化的工作历史——不是原始聊天记录。让后续轮次与其它 Agent 知道：
# 最近谁做了什么、结果如何、关联哪个耐久对象（ChangeSession/Execution/AgentTask）。
# 解决根因 #4：Direct Agent 会话此前对跨 agent 已完成工作零感知。
#
# 设计：append-only JSON（output/agents/work_memory.json），原子写。只存结构化事实，
# 不存模型隐藏推理，不把每句聊天都变成记忆（§10.2）。canonical project truth 永远高于
# 工作记忆（§11）——这里是"最近做过什么"的上下文，不是项目真值源。
from __future__ import annotations
import json
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.atomic import atomic_write_json


VALID_TYPES = ("task", "change", "collaboration", "decision", "completion", "blocker")


def _new_id() -> str:
    return f"wm_{secrets.token_hex(6)}"


class WorkMemoryStore:
    """单项目共享工作记忆。线程/进程安全靠 atomic_write_json（最后写胜出；R1 写入量小）。"""

    def __init__(self, project_dir, project_name: str):
        self.project_dir = Path(project_dir)
        self.project_name = project_name
        self.root = self.project_dir / "output" / "agents"
        self.path = self.root / "work_memory.json"

    def _load(self) -> List[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            entries = data.get("entries") if isinstance(data, dict) else data
            return [e for e in entries if isinstance(e, dict)]
        except Exception:
            return []

    def _save(self, entries: List[dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, {"project": self.project_name, "entries": entries})

    def add(self, entry_type: str, summary: str, *,
            participants: Optional[List[str]] = None,
            references: Optional[Dict[str, Any]] = None,
            affected_domains: Optional[List[str]] = None,
            affected_entities: Optional[List[str]] = None,
            status: str = "completed") -> dict:
        """追加一条工作记忆。返回持久化后的 entry dict。"""
        if entry_type not in VALID_TYPES:
            entry_type = "task"
        entry = {
            "id": _new_id(),
            "project": self.project_name,
            "type": entry_type,
            "summary": summary[:500],
            "participants": list(participants or []),
            "references": references or {},
            "affected_domains": list(affected_domains or []),
            "affected_entities": list(affected_entities or []),
            "status": status,
            "created_at": time.time(),
        }
        entries = self._load()
        entries.append(entry)
        # 有界保留（最近 200 条，避免无限增长；R1 不做摘要压缩）
        if len(entries) > 200:
            entries = entries[-200:]
        self._save(entries)
        return entry

    def recent(self, limit: int = 8, *, domains: Optional[List[str]] = None,
               participants: Optional[List[str]] = None) -> List[dict]:
        """取最近的工作记忆（按时间倒序）。可按域/参与者过滤。"""
        entries = self._load()
        if domains:
            dset = set(domains)
            entries = [e for e in entries if dset.intersection(e.get("affected_domains") or [])]
        if participants:
            pset = set(participants)
            entries = [e for e in entries if pset.intersection(e.get("participants") or [])]
        return list(reversed(entries[-limit:]))

    def render_for_context(self, limit: int = 6) -> str:
        """渲染成给 Agent 看的紧凑文本（空则返回空串）。不暴露内部 ID 之外的敏感信息。"""
        entries = self.recent(limit)
        if not entries:
            return ""
        lines = ["# 近期共享工作记录（结构化工作记忆，非项目真值；以当前正式项目为准）"]
        for e in entries:
            who = "、".join(e.get("participants") or []) or "—"
            dom = "/".join(e.get("affected_domains") or [])
            ref = e.get("references") or {}
            ref_bits = []
            if ref.get("change_session_id"):
                ref_bits.append("变更 " + ref["change_session_id"][:12])
            if ref.get("execution_id"):
                ref_bits.append("执行 " + ref["execution_id"][:12])
            if ref.get("agent_task_id"):
                ref_bits.append("任务 " + ref["agent_task_id"][:12])
            ref_str = f" [{', '.join(ref_bits)}]" if ref_bits else ""
            lines.append(
                f"- [{e.get('type')}/{e.get('status')}] {e.get('summary')}"
                f"（参与者: {who}）{('/' + dom) if dom else ''}{ref_str}")
        return "\n".join(lines)
