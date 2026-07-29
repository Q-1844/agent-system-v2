"""
文件保存器
自动保存生成的文件到正确位置
"""

from pathlib import Path

from ..utils.logger import setup_logger

logger = setup_logger("file_saver")


class FileSaver:
    """文件保存器"""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.output_dir = project_dir / "output"

    def save_character_image(
        self, character_name: str, view: str, image_data: bytes
    ) -> Path:
        """
        保存角色参考图

        Args:
            character_name: 角色名称
            view: 视角 ('front', 'side', 'full')
            image_data: 图片数据

        Returns:
            保存的文件路径
        """
        filename = f"{character_name}_{view}.png"
        filepath = self.output_dir / "characters" / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(image_data)
        logger.info(f"角色参考图已保存: {filepath}")
        return filepath

    def save_character_sheet(self, character_name: str, image_data: bytes) -> Path:
        """
        保存角色设定图（包含多视角的完整图片）

        Args:
            character_name: 角色名称
            image_data: 图片数据

        Returns:
            保存的文件路径
        """
        filename = f"{character_name}_sheet.png"
        filepath = self.output_dir / "characters" / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(image_data)
        logger.info(f"角色设定图已保存: {filepath}")
        return filepath

    def save_storyboard(self, scene_id: int, image_data: bytes) -> Path:
        """
        保存分镜图

        Args:
            scene_id: 场景ID
            image_data: 图片数据

        Returns:
            保存的文件路径
        """
        filename = f"scene_{scene_id:02d}.png"
        filepath = self.output_dir / "storyboards" / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(image_data)
        logger.info(f"分镜图已保存: {filepath}")
        return filepath

    def save_video(self, from_scene: int, to_scene: int, video_data: bytes) -> Path:
        """
        保存过渡动画

        Args:
            from_scene: 起始场景ID
            to_scene: 结束场景ID
            video_data: 视频数据

        Returns:
            保存的文件路径
        """
        filename = f"transition_{from_scene:02d}_{to_scene:02d}.mp4"
        filepath = self.output_dir / "videos" / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(video_data)
        logger.info(f"过渡动画已保存: {filepath}")
        return filepath

    def save_audio(self, scene_id: int, audio_data: bytes) -> Path:
        """
        保存对话音频

        Args:
            scene_id: 场景ID
            audio_data: 音频数据

        Returns:
            保存的文件路径
        """
        filename = f"dialog_{scene_id:02d}.mp3"
        filepath = self.output_dir / "audio" / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(audio_data)
        logger.info(f"对话音频已保存: {filepath}")
        return filepath

    def save_subtitle(self, scene_id: int, subtitle_content: str) -> Path:
        """
        保存字幕文件

        Args:
            scene_id: 场景ID
            subtitle_content: 字幕内容 (SRT格式)

        Returns:
            保存的文件路径
        """
        filename = f"scene_{scene_id:02d}.srt"
        filepath = self.output_dir / "subtitles" / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(subtitle_content, encoding="utf-8")
        logger.info(f"字幕文件已保存: {filepath}")
        return filepath

    def save_json(self, filename: str, data: dict) -> Path:
        """
        保存JSON文件

        Args:
            filename: 文件名
            data: JSON数据

        Returns:
            保存的文件路径
        """
        import json

        filepath = self.output_dir / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"JSON文件已保存: {filepath}")
        return filepath

    def download_file(self, url: str, filepath: Path) -> Path:
        """
        下载文件

        Args:
            url: 文件URL
            filepath: 保存路径

        Returns:
            保存的文件路径
        """
        import httpx

        filepath.parent.mkdir(parents=True, exist_ok=True)

        with httpx.Client() as client:
            response = client.get(url, timeout=300)
            response.raise_for_status()
            filepath.write_bytes(response.content)

        logger.info(f"文件已下载: {filepath}")
        return filepath
