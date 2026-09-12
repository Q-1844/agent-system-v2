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

    def create_project(self, name: str, *, render_family: str = "",
                       art_direction: str = "") -> Path:
        """
        创建新项目：建目录 + 子目录 + 初始化空 project.json（v2）+ project_profile.json。

        重名保护：目录已存在则抛 FileExistsError（router 转 409），
        绝不覆盖已有 project.json / parsed_script.json / 生成资产。
        新项目用 Project(name) 的 dataclass 默认值（风格/画幅/schema_version/
        时间戳/next_scene_seq 全部自然生效），不在调用方手拼 JSON。

        Project Visual Profile（P1 §6）：render_family 非空时随创建写入
        output/project_profile.json（HTTP create contract 必填；工具/测试直调
        不传则不写——旧项目语义，unspecified）。
        """
        project_dir = self.base_dir / name
        if project_dir.exists():
            raise FileExistsError(f"项目已存在: {name}")

        # P1 §6：先验证 visual profile（fail fast）——校验失败绝不留下半建目录
        visual_profile_obj = None
        if render_family:
            from ..visual_profile.models import ProjectVisualProfile
            visual_profile_obj = ProjectVisualProfile(
                render_family=render_family, art_direction=art_direction)

        # 创建标准目录结构
        (project_dir / "input").mkdir(parents=True)
        (project_dir / "output" / "characters").mkdir(parents=True)
        (project_dir / "output" / "storyboards").mkdir(parents=True)
        (project_dir / "output" / "videos").mkdir(parents=True)
        (project_dir / "output" / "audio").mkdir(parents=True)
        (project_dir / "output" / "subtitles").mkdir(parents=True)
        (project_dir / "cache" / "checkpoints").mkdir(parents=True)

        # 初始化空 project.json，让新项目立即可被 v2 shot 级流程加载（不再 404）
        from ..core.models import Project
        from ..core.store import ProjectStore
        ProjectStore(project_dir).save(Project(name=name))

        # P1 §6：新项目随创建写入项目视觉定义（第一次真实设置即创建）
        if visual_profile_obj is not None:
            from ..visual_profile.store import VisualProfileStore
            VisualProfileStore(project_dir).save(visual_profile_obj)

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
