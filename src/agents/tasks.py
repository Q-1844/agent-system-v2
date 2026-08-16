# src/agents/tasks.py
# AgentTask 存储（§10/§26）。按项目落盘：
#   projects/{p}/output/agents/tasks/{task_id}.json
# AgentTask = AI 任务协调记录（≠ JobQueue 的图片/视频/Production 执行任务，§10）。
# 状态：pending → running → completed/failed/cancelled。不复杂 DAG、不 distributed engine（§24）。
# 所有写盘走 src/core/atomic.atomic_write_json。
from __future__ import annotations
import json
import time
import uuid
from pathlib import Path
from typing import Callable, List, Optional

from src.core.atomic import atomic_write_json

from .models import AgentTask, TaskStatus


class AgentTaskStore:
    """单个项目绑定的 agent task 存储。"""

    def __init__(self, project_dir, project_name: str):
        self.project_dir = Path(project_dir)
        self.project_name = project_name
        self.root = self.project_dir / "output" / "agents" / "tasks"

    def _path(self, tid: str) -> Path:
        return self.root / f"{tid}.json"

    def _save(self, t: AgentTask) -> None:
        t.updated_at = time.time()
        atomic_write_json(self._path(t.task_id), t.to_dict())

    def _iter_files(self):
        if not self.root.exists():
            return
        for f in sorted(self.root.glob("*.json")):
            try:
                yield AgentTask.from_dict(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue

    def create(self, task: AgentTask) -> AgentTask:
        if not task.task_id:
            task.task_id = "task_" + uuid.uuid4().hex[:8]
        task.project = task.project or self.project_name
        if not task.created_at:
            task.created_at = time.time()
        self._save(task)
        return task

    def get(self, tid: str) -> Optional[AgentTask]:
        p = self._path(tid)
        if not p.exists():
            return None
        try:
            return AgentTask.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def update(self, tid: str, mutator: Callable[[AgentTask], None]) -> Optional[AgentTask]:
        t = self.get(tid)
        if t is None:
            return None
        mutator(t)
        self._save(t)
        return t

    def update_task(self, task: AgentTask) -> AgentTask:
        """整体写回（runtime 流转状态时用）。"""
        self._save(task)
        return task

    def list(self, requester_agent: Optional[str] = None,
             target_agent: Optional[str] = None,
             status: Optional[str] = None) -> List[AgentTask]:
        out = []
        for t in self._iter_files():
            if requester_agent and t.requester_agent != requester_agent:
                continue
            if target_agent and t.target_agent != target_agent:
                continue
            if status and t.status != status:
                continue
            out.append(t)
        # 新→旧
        out.sort(key=lambda x: x.created_at, reverse=True)
        return out
