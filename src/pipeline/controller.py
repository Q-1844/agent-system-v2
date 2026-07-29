"""
流水线控制器
管理整个工作流的执行，支持交互模式、自动模式、断点恢复
"""

import asyncio
from pathlib import Path
from typing import Dict, Any, Optional

import yaml

from ..api.agnes_client import AgnesClient
from ..api.mimo_client import MimoTTSClient
from ..utils.logger import setup_logger
from ..utils.progress import ProgressTracker
from ..utils.project_manager import ProjectManager
from ..utils.file_saver import FileSaver
from ..utils.evaluator import ImageEvaluator
from ..ui.terminal import TerminalUI
from .script_parser import ScriptParser
from .character_gen import CharacterGenerator
from .storyboard_gen import StoryboardGenerator
from .animation_gen import AnimationGenerator
from .tts_gen import TTSGenerator
from .composer import Composer

logger = setup_logger("pipeline_controller")


class PipelineController:
    """流水线控制器"""

    def __init__(self, config_path: str = "./config/config.yaml"):
        # 加载配置
        self.config = self._load_config(config_path)

        # 初始化组件
        self.project_manager = ProjectManager(self.config["project"]["base_dir"])
        self.agnes_client = AgnesClient(self.config["agnes"])
        self.mimo_client = MimoTTSClient(self.config["mimo"])
        self.evaluator = ImageEvaluator(self.agnes_client)
        self.ui = TerminalUI()

        # 流水线阶段
        self.stages = [
            ("剧本解析", self._stage_script_parser),
            ("角色设定", self._stage_character_gen),
            ("分镜生成", self._stage_storyboard_gen),
            ("动画生成", self._stage_animation_gen),
            ("语音合成", self._stage_tts_gen),
            ("合成输出", self._stage_composer),
        ]

    def _load_config(self, config_path: str) -> dict:
        """
        加载配置文件

        Args:
            config_path: 配置文件路径

        Returns:
            配置字典
        """
        import os

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # 处理环境变量
        if "${AGNES_API_KEY}" in str(config.get("agnes", {}).get("api_key", "")):
            api_key = os.environ.get("AGNES_API_KEY", "")
            if api_key:
                config["agnes"]["api_key"] = api_key
            else:
                logger.warning("未设置AGNES_API_KEY环境变量")

        if "${MIMO_API_KEY}" in str(config.get("mimo", {}).get("api_key", "")):
            api_key = os.environ.get("MIMO_API_KEY", "")
            if api_key:
                config["mimo"]["api_key"] = api_key
            else:
                logger.warning("未设置MIMO_API_KEY环境变量")

        return config

    async def run(
        self,
        script_path: str,
        project_name: Optional[str] = None,
        mode: str = "interactive",
    ):
        """
        运行工作流

        Args:
            script_path: 剧本文件路径
            project_name: 项目名称（可选，默认从剧本读取）
            mode: 运行模式 ('interactive', 'auto')
        """
        # 显示启动横幅
        self.ui.show_banner()

        # 创建项目
        if not project_name:
            # 从剧本文件读取项目名称
            with open(script_path, "r", encoding="utf-8") as f:
                script_data = yaml.safe_load(f)
            project_name = script_data.get("project", "未命名项目")

        project_dir = self.project_manager.create_project(project_name)
        file_saver = FileSaver(project_dir)
        progress = ProgressTracker(str(project_dir))

        logger.info(f"项目目录: {project_dir}")

        # 重置所有失败或卡住的阶段（允许重新执行）
        for stage_name, _ in self.stages:
            stage_info = progress.get_stage(stage_name)
            if stage_info.get("status") in ("failed", "in_progress"):
                progress.update_stage(stage_name, "pending")
                logger.info(f"重置阶段: {stage_name} ({stage_info.get('status')} → pending)")

        # 执行各阶段
        data = {"script_path": script_path}

        # 如果阶段1已完成，从已有文件加载数据（避免跳过后丢失数据）
        script_file = project_dir / "output" / "parsed_script.json"
        if script_file.exists():
            import json as _json
            with open(script_file, "r", encoding="utf-8") as f:
                parsed = _json.load(f)
            data.update(parsed)
            logger.info("从已有 parsed_script.json 加载数据")

        for i, (stage_name, stage_func) in enumerate(self.stages, 1):
            self.ui.show_stage_header(stage_name, i, len(self.stages))

            # 检查是否已完成
            stage_progress = progress.get_stage(stage_name)
            if stage_progress.get("status") == "completed":
                logger.info(f"阶段 {stage_name} 已完成，跳过")
                continue

            # 更新进度
            progress.update_stage(stage_name, "in_progress")

            try:
                # 执行阶段
                data = await stage_func(data, file_saver)

                # 更新进度
                progress.update_stage(stage_name, "completed")

                # 显示成功消息
                self.ui.show_success(f"{stage_name} 完成")

                # 交互模式：等待用户确认
                if mode == "interactive":
                    choice = self.ui.ask_user_choice(
                        ["继续下一阶段", "重新执行当前阶段", "暂停保存"]
                    )

                    if choice == 2:
                        # 重新执行
                        progress.update_stage(stage_name, "pending")
                        # 重新执行当前阶段
                        data = await stage_func(data, file_saver)
                        progress.update_stage(stage_name, "completed")
                    elif choice == 3:
                        # 暂停保存
                        self.ui.show_info("进度已保存，下次运行将从断点继续")
                        return

            except Exception as e:
                logger.error(f"阶段 {stage_name} 失败: {e}")
                progress.update_stage(stage_name, "failed")
                self.ui.show_error(f"{stage_name} 失败: {e}")

                if mode == "interactive":
                    choice = self.ui.ask_user_choice(["重试", "跳过", "退出"])

                    if choice == 1:
                        # 重试
                        progress.update_stage(stage_name, "pending")
                        data = await stage_func(data, file_saver)
                        progress.update_stage(stage_name, "completed")
                    elif choice == 2:
                        # 跳过
                        logger.warning(f"跳过阶段 {stage_name}")
                        continue
                    else:
                        # 退出
                        return
                else:
                    raise

        # 显示完成消息
        self.ui.show_success("工作流执行完成！")
        self.ui.show_info(f"输出目录: {project_dir / 'output'}")

    async def _stage_script_parser(
        self, data: Dict[str, Any], file_saver: FileSaver
    ) -> Dict[str, Any]:
        """
        阶段1: 剧本解析

        Args:
            data: 输入数据
            file_saver: 文件保存器

        Returns:
            输出数据
        """
        parser = ScriptParser(self.agnes_client)

        # 保留已有的场景数据（image_path, score 等）
        existing_scenes = {s["id"]: s for s in data.get("scenes", [])}

        # 解析剧本
        parsed = await parser.parse(data["script_path"])

        # 合并：新解析的场景保留已有的 image_path/score/status
        for scene in parsed.get("scenes", []):
            sid = scene.get("id")
            if sid in existing_scenes:
                old = existing_scenes[sid]
                # 保留已有的关键字段
                for key in ("image_path", "score", "audio_path", "subtitle_path", "video_path"):
                    if key in old and key not in scene:
                        scene[key] = old[key]
                # 如果已有 completed 状态，保持
                if old.get("status") == "completed":
                    scene["status"] = "completed"

        # 增强描述
        parsed = await parser.enhance_descriptions(parsed)

        # 保存解析结果
        file_saver.save_json("parsed_script.json", parsed)

        # 显示信息
        self.ui.show_character_info(parsed["characters"])
        self.ui.show_scene_info(parsed["scenes"])

        return {**data, **parsed}

    async def _stage_character_gen(
        self, data: Dict[str, Any], file_saver: FileSaver
    ) -> Dict[str, Any]:
        """
        阶段2: 角色生成

        Args:
            data: 输入数据
            file_saver: 文件保存器

        Returns:
            输出数据
        """
        # 合并 agnes 配置和项目配置（角色生成器需要 style 和 character_sheet 配置）
        char_config = {**self.config["agnes"]}
        char_config["project"] = data.get("project", {})
        generator = CharacterGenerator(
            self.agnes_client, file_saver, self.evaluator, char_config
        )

        characters = await generator.generate(data["characters"])

        return {**data, "characters": characters}

    async def _stage_storyboard_gen(
        self, data: Dict[str, Any], file_saver: FileSaver
    ) -> Dict[str, Any]:
        """
        阶段3: 分镜生成

        Args:
            data: 输入数据
            file_saver: 文件保存器

        Returns:
            输出数据
        """
        generator = StoryboardGenerator(
            self.agnes_client, file_saver, self.evaluator, self.config["agnes"]
        )

        scenes = await generator.generate(
            data["scenes"], data["characters"], data["project"]["style"]
        )

        return {**data, "scenes": scenes}

    async def _stage_animation_gen(
        self, data: Dict[str, Any], file_saver: FileSaver
    ) -> Dict[str, Any]:
        """
        阶段4: 动画生成

        Args:
            data: 输入数据
            file_saver: 文件保存器

        Returns:
            输出数据
        """
        generator = AnimationGenerator(self.agnes_client, file_saver, self.config["agnes"])

        scenes = await generator.generate(data["scenes"])

        return {**data, "scenes": scenes}

    async def _stage_tts_gen(
        self, data: Dict[str, Any], file_saver: FileSaver
    ) -> Dict[str, Any]:
        """
        阶段5: 语音合成

        Args:
            data: 输入数据
            file_saver: 文件保存器

        Returns:
            输出数据
        """
        from ..api.edge_tts_client import EdgeTTSClient
        generator = TTSGenerator(EdgeTTSClient(), file_saver)

        scenes = await generator.generate(data["scenes"])

        return {**data, "scenes": scenes}

    async def _stage_composer(
        self, data: Dict[str, Any], file_saver: FileSaver
    ) -> Dict[str, Any]:
        """
        阶段6: 合成输出

        Args:
            data: 输入数据
            file_saver: 文件保存器

        Returns:
            输出数据
        """
        composer = Composer(file_saver)

        outputs = composer.compose(
            data["project"]["name"], data["scenes"], data["characters"]
        )

        return {**data, "outputs": outputs}
