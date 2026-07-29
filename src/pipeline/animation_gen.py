"""
动画生成阶段
在相邻分镜之间生成过渡动画
"""

from typing import Dict, Any, List, Optional

from ..utils.logger import setup_logger
from ..utils.file_saver import FileSaver

logger = setup_logger("animation_gen")


class AnimationGenerator:
    """动画生成器"""

    def __init__(self, agnes_client, file_saver: FileSaver, config: dict):
        self.agnes_client = agnes_client
        self.file_saver = file_saver
        self.config = config

    async def generate(self, scenes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        为相邻场景生成过渡动画

        Args:
            scenes: 场景列表（需要包含image_path）

        Returns:
            更新后的场景列表（包含video_path）
        """
        logger.info(f"开始生成过渡动画: {len(scenes) - 1}个过渡")

        # 过滤出有图片的场景
        valid_scenes = [s for s in scenes if s.get("image_path")]

        if len(valid_scenes) < 2:
            logger.warning("有效场景不足，无法生成过渡动画")
            return scenes

        transitions = []

        for i in range(len(valid_scenes) - 1):
            from_scene = valid_scenes[i]
            to_scene = valid_scenes[i + 1]

            logger.info(
                f"生成过渡: 场景{from_scene['id']} → 场景{to_scene['id']}"
            )

            # 构建提示词
            prompt = self._build_transition_prompt(from_scene, to_scene)

            # 获取参考图
            image_refs = [from_scene["image_path"], to_scene["image_path"]]

            try:
                # 调用视频生成API
                result = await self.agnes_client.generate_video(
                    prompt=prompt, image_refs=image_refs, mode="keyframes"
                )

                if result.get("status") == "completed":
                    video_url = result.get("remixed_from_video_id")
                    if video_url:
                        # 下载视频
                        video_data = await self._download_video(video_url)
                        if video_data:
                            # 保存视频
                            filepath = self.file_saver.save_video(
                                from_scene["id"], to_scene["id"], video_data
                            )
                            transition = {
                                "from_scene": from_scene["id"],
                                "to_scene": to_scene["id"],
                                "video_path": str(filepath),
                                "duration": result.get("seconds", "5.0"),
                            }
                            transitions.append(transition)
                            logger.info(f"  过渡动画已保存: {filepath}")
                        else:
                            logger.warning(f"  视频下载失败")
                    else:
                        logger.warning(f"  视频URL为空")
                else:
                    logger.warning(f"  视频生成失败: {result.get('status')}")

            except Exception as e:
                logger.error(f"  过渡动画生成失败: {e}")
                continue

        # 将过渡信息添加到场景中
        for scene in scenes:
            scene["transitions"] = {
                "to_next": None,
                "from_prev": None,
            }

        for transition in transitions:
            for scene in scenes:
                if scene["id"] == transition["from_scene"]:
                    scene["transitions"]["to_next"] = transition["video_path"]
                if scene["id"] == transition["to_scene"]:
                    scene["transitions"]["from_prev"] = transition["video_path"]

        logger.info(f"过渡动画生成完成: {len(transitions)}/{len(valid_scenes) - 1}")

        return scenes

    def _build_transition_prompt(
        self, from_scene: Dict[str, Any], to_scene: Dict[str, Any]
    ) -> str:
        """
        构建过渡动画提示词

        Args:
            from_scene: 起始场景
            to_scene: 结束场景

        Returns:
            提示词
        """
        parts = [
            "Smooth cinematic transition between two keyframes",
            "maintaining visual consistency",
            "natural camera movement",
            "consistent character identity",
        ]

        # 添加场景描述
        if from_scene.get("motion"):
            parts.append(f"starting with {from_scene['motion']}")
        if to_scene.get("motion"):
            parts.append(f"ending with {to_scene['motion']}")

        return ", ".join(parts)

    async def _download_video(self, url: str) -> Optional[bytes]:
        """
        下载视频

        Args:
            url: 视频URL

        Returns:
            视频数据
        """
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=300)
                response.raise_for_status()
                return response.content
        except Exception as e:
            logger.error(f"视频下载失败: {e}")
            return None
