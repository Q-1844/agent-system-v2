"""
项目管理器
管理多个项目，每个项目独立目录
"""

from pathlib import Path
from typing import List

from ..utils.logger import setup_logger

logger = setup_logger("project_manager")


class ProjectManager:
    """项目管理器"""

    def __init__(self, base_dir: str = "./projects"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create_project(self, name: str) -> Path:
        """
        创建新项目

        Args:
            name: 项目名称

        Returns:
            项目目录路径
        """
        project_dir = self.base_dir / name
        project_dir.mkdir(exist_ok=True)

        # 创建标准目录结构
        (project_dir / "input").mkdir(exist_ok=True)
        (project_dir / "output" / "characters").mkdir(parents=True, exist_ok=True)
        (project_dir / "output" / "storyboards").mkdir(parents=True, exist_ok=True)
        (project_dir / "output" / "videos").mkdir(parents=True, exist_ok=True)
        (project_dir / "output" / "audio").mkdir(parents=True, exist_ok=True)
        (project_dir / "output" / "subtitles").mkdir(parents=True, exist_ok=True)
        (project_dir / "cache" / "checkpoints").mkdir(parents=True, exist_ok=True)

        logger.info(f"项目已创建: {project_dir}")
        return project_dir

    def list_projects(self) -> List[str]:
        """列出所有项目"""
        return [d.name for d in self.base_dir.iterdir() if d.is_dir()]

    def get_project_dir(self, name: str) -> Path:
        """
        获取项目目录

        Args:
            name: 项目名称

        Returns:
            项目目录路径

        Raises:
            FileNotFoundError: 项目不存在
        """
        project_dir = self.base_dir / name
        if not project_dir.exists():
            raise FileNotFoundError(f"项目不存在: {name}")
        return project_dir
