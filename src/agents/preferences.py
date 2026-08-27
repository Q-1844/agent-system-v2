# src/agents/preferences.py
# AI-7D-R §10.3 显式偏好记忆。
#
# 只记录用户明确陈述或清楚确认的偏好（"以后不要自动加战斗"/"角色图保持这种风格"）。
# 绝不从一次普通操作推断永久偏好（§10.3）。偏好是建议性上下文：当前明确指令 + 当前
# canonical 真值永远高于偏好（§11）。
#
# 持久化：output/agents/preferences.json（项目级；scope=project）。原子写。
# 这是 v1 最小实现——不做向量/RAG/人格学习（§85）。
from __future__ import annotations
import json
import re
import secrets
import time
from pathlib import Path
from typing import List, Optional

from src.core.atomic import atomic_write_json


# 保守的偏好触发词：只在用户明确说"以后/默认/一直/总是/不要自动/记住"等时捕获。
# 不把"这次别加战斗"当成永久偏好（没有"以后/默认"等长期性信号）。
_PREF_CUES = re.compile(
    r"(以后|往后|向来|一直|总是|默认|平时|通常|一般来说|下次起|从现在起)"
    r"|((不要|别|勿|不希望|不想)(再)?(自动|主动|随便))"
)


def looks_like_preference(text: str) -> bool:
    """启发式判断一条用户消息是否在陈述长期偏好。保守——拿不准就返回 False，
    宁可不记也不误记（§10.3：不要从一次偶然动作推断偏好）。"""
    if not text or len(text) > 200:
        return False
    t = text.strip()
    # 问句不算偏好
    if t.endswith("？") or t.endswith("?"):
        return False
    return bool(_PREF_CUES.search(t))


def _new_id() -> str:
    return f"pref_{secrets.token_hex(6)}"


class PreferenceStore:
    """项目级显式偏好。key/value 为简短文本；active=False 表示用户已撤销。"""

    def __init__(self, project_dir, project_name: str):
        self.project_dir = Path(project_dir)
        self.project_name = project_name
        self.root = self.project_dir / "output" / "agents"
        self.path = self.root / "preferences.json"

    def _load(self) -> List[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            prefs = data.get("preferences") if isinstance(data, dict) else data
            return [p for p in prefs if isinstance(p, dict)]
        except Exception:
            return []

    def _save(self, prefs: List[dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, {"project": self.project_name, "preferences": prefs})

    def add(self, key: str, value: str, *, scope: str = "project",
            source_message_id: Optional[str] = None) -> dict:
        """记录一条偏好。同 key 已存在则更新 value（并刷新 updated_at），不重复造。"""
        prefs = self._load()
        key = key.strip()[:80]
        for p in prefs:
            if p.get("key") == key and p.get("scope") == scope and p.get("active", True):
                p["value"] = value[:500]
                if source_message_id:
                    p["source_message_id"] = source_message_id
                p["updated_at"] = time.time()
                self._save(prefs)
                return p
        entry = {
            "id": _new_id(), "scope": scope, "key": key, "value": value[:500],
            "source_message_id": source_message_id, "active": True,
            "created_at": time.time(), "updated_at": time.time(),
        }
        prefs.append(entry)
        self._save(prefs)
        return entry

    def active(self) -> List[dict]:
        return [p for p in self._load() if p.get("active", True)]

    def render_for_context(self, limit: int = 6) -> str:
        """渲染给 Agent 的偏好文本（空则空串）。"""
        prefs = self.active()
        if not prefs:
            return ""
        lines = ["# 用户明确偏好（建议性；当前明确指令与项目真值优先；与当前请求冲突时忽略）"]
        for p in prefs[-limit:]:
            lines.append(f"- {p.get('key')}: {p.get('value')}")
        return "\n".join(lines)
