"""
进度追踪模块
持久化保存项目进度，支持断点恢复
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional


class ProgressTracker:
    """进度追踪器"""

    def __init__(self, project_dir: str):
        self.project_dir = Path(project_dir)
        self.progress_file = self.project_dir / "progress.json"
        self.data = self._load()

    def _load(self) -> dict:
        """加载进度"""
        if self.progress_file.exists():
            try:
                with open(self.progress_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return self._create_default()
        return self._create_default()

    def _create_default(self) -> dict:
        """创建默认进度结构"""
        return {
            "project_name": self.project_dir.name,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "stages": {},
        }

    def save(self):
        """保存进度"""
        self.data["updated_at"] = datetime.now().isoformat()
        self.project_dir.mkdir(parents=True, exist_ok=True)
        with open(self.progress_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def update_stage(self, stage_name: str, status: str, **kwargs):
        """
        更新阶段状态

        Args:
            stage_name: 阶段名称
            status: 状态 ('pending', 'in_progress', 'completed', 'failed')
            **kwargs: 其他数据
        """
        if stage_name not in self.data["stages"]:
            self.data["stages"][stage_name] = {}

        self.data["stages"][stage_name].update(
            {"status": status, "updated_at": datetime.now().isoformat(), **kwargs}
        )

        self.save()

    def get_stage(self, stage_name: str) -> dict:
        """获取阶段状态"""
        return self.data["stages"].get(stage_name, {})

    def get_progress(self) -> dict:
        """获取整体进度"""
        stages = self.data.get("stages", {})
        total = len(stages)
        completed = sum(1 for s in stages.values() if s.get("status") == "completed")

        return {
            "total": total,
            "completed": completed,
            "percentage": (completed / total * 100) if total > 0 else 0,
        }
