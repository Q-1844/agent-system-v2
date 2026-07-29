"""
语音合成阶段
为有对话的场景生成语音和字幕
"""

from typing import Dict, Any, List

from ..utils.logger import setup_logger
from ..utils.file_saver import FileSaver

logger = setup_logger("tts_gen")


class TTSGenerator:
    """语音合成生成器"""

    def __init__(self, mimo_client, file_saver: FileSaver):
        self.mimo_client = mimo_client
        self.file_saver = file_saver

    async def generate(self, scenes: List[Dict[str, Any]], voice: str = "default", speed: float = 1.0) -> List[Dict[str, Any]]:
        """
        为有对话的场景生成语音

        Args:
            scenes: 场景列表
            voice: 语音类型
            speed: 语速

        Returns:
            更新后的场景列表（包含audio_path和subtitle_path）
        """
        # 过滤出有对话的场景
        scenes_with_dialogue = [s for s in scenes if s.get("dialogue")]

        logger.info(f"开始生成语音: {len(scenes_with_dialogue)}个场景有对话")

        for scene in scenes_with_dialogue:
            logger.info(f"生成场景{scene['id']}的语音: {scene['dialogue'][:20]}...")

            try:
                # 生成语音
                audio_data = await self.mimo_client.synthesize(text=scene["dialogue"], voice=voice, speed=speed)

                # 保存音频
                audio_path = self.file_saver.save_audio(scene["id"], audio_data)
                scene["audio_path"] = str(audio_path)

                # 生成字幕
                subtitle_content = self._generate_subtitle(
                    scene_id=scene["id"],
                    text=scene["dialogue"],
                    duration=5.0,  # 默认5秒
                )
                subtitle_path = self.file_saver.save_subtitle(
                    scene["id"], subtitle_content
                )
                scene["subtitle_path"] = str(subtitle_path)

                logger.info(f"  语音已保存: {audio_path}")

            except Exception as e:
                logger.error(f"  语音生成失败: {e}")
                scene["audio_path"] = None
                scene["subtitle_path"] = None

        logger.info("语音合成完成")
        return scenes

    def _generate_subtitle(
        self, scene_id: int, text: str, duration: float
    ) -> str:
        """
        生成SRT字幕

        Args:
            scene_id: 场景ID
            text: 字幕文本
            duration: 持续时间（秒）

        Returns:
            SRT格式字幕内容
        """
        # 计算时间戳
        start_time = 0.0
        end_time = duration

        # 格式化时间
        start_str = self._format_time(start_time)
        end_str = self._format_time(end_time)

        # 生成SRT
        srt = f"""1
{start_str} --> {end_str}
{text}
"""
        return srt

    def _format_time(self, seconds: float) -> str:
        """
        格式化时间为SRT格式

        Args:
            seconds: 秒数

        Returns:
            格式化的时间字符串 (HH:MM:SS,mmm)
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)

        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
