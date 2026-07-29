"""
角色生成阶段
为每个角色生成三个视角的参考图
支持两种模式:
  - "sheet": 生成一张角色设定图，然后裁剪出三个视角
  - "separate": 分别生成三个视角（相似 prompt + 不同视角关键词）
"""

import io
import random
from pathlib import Path
from typing import Dict, Any, List, Optional

from ..utils.logger import setup_logger
from ..utils.file_saver import FileSaver
from ..utils.evaluator import ImageEvaluator

logger = setup_logger("character_gen")

# PIL 导入
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    logger.warning("未安装 Pillow")


class CharacterGenerator:
    """角色生成器"""

    # 三视角名称（与前端 Tab / api_server.py 单视角接口统一为 front/side/back）
    VIEW_NAMES = ["front", "side", "back"]

    # ====== 分别生成模式的 prompt 模板 ======
    # 公共前缀（保持角色一致性）
    BASE_PROMPT = (
        "{description}, {style} illustration, "
        "single character, white background, full body, "
        "detailed character design, high quality"
    )

    # 每个视角的关键词（只描述一个视角，绝不提及其他视角）
    VIEW_PROMPTS = {
        "front": (
            "facing the camera directly, straight-on front view, "
            "symmetrical pose, looking at the viewer, "
            "character portrait"
        ),
        "side": (
            "facing left, side profile view, 90 degree angle, "
            "looking to the left, "
            "character side portrait"
        ),
        "back": (
            "back view, from behind, character facing away from camera, "
            "showing back to viewer, back of head and body visible, "
            "no face shown, full body standing pose"
        ),
    }

    # ====== 设定图模式的 prompt 模板 ======
    SHEET_PROMPT_TEMPLATE = (
        "Character reference sheet of {description}. "
        "Showing front view, side view, and back view in a horizontal row. "
        "Full body, white background, consistent proportions and style. "
        "{style} illustration, clean lines, character design sheet format, "
        "T-pose, detailed"
    )

    def __init__(
        self,
        agnes_client,
        file_saver: FileSaver,
        evaluator: ImageEvaluator,
        config: dict,
    ):
        self.agnes_client = agnes_client
        self.file_saver = file_saver
        self.evaluator = evaluator
        self.config = config

        # 配置
        image_config = config.get("settings", {}).get("image", {})
        self.default_size = image_config.get("size", "1152x768")
        self.num_candidates = image_config.get("num_candidates", 3)
        self.quality_threshold = image_config.get("quality_threshold", 7.0)

        sheet_config = config.get("settings", {}).get("character_sheet", {})
        self.sheet_size = sheet_config.get("size", "1792x1024")
        self.sheet_num_candidates = sheet_config.get("num_candidates", 2)

    async def generate(self, characters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        为所有角色生成参考图

        Args:
            characters: 角色列表

        Returns:
            更新后的角色列表（包含参考图路径）
        """
        logger.info(f"开始生成角色参考图: {len(characters)}个角色")

        for char in characters:
            logger.info(f"生成角色: {char['name']}")
            char["reference_images"] = {}

            try:
                # 使用分别生成模式
                views = await self._generate_separate_views(char)
                if views:
                    char["reference_images"] = views
                    logger.info(f"  角色 {char['name']} 完成: {list(views.keys())}")
                else:
                    logger.warning(f"  角色 {char['name']} 生成失败")

            except Exception as e:
                logger.error(f"  角色 {char['name']} 生成异常: {e}")
                continue

        return characters

    async def _generate_separate_views(
        self, char: Dict[str, Any]
    ) -> Dict[str, str]:
        """
        分别生成三个视角

        策略: 用相似的 base prompt + 不同视角关键词，保持角色一致性

        Args:
            char: 角色数据

        Returns:
            视角字典 {"front": "path", "side": "path", "full": "path"}
        """
        project_style = self.config.get("project", {}).get("style", "anime")
        base = self.BASE_PROMPT.format(
            description=char["description"],
            style=project_style or "anime",
        )

        views = {}
        for view_name in self.VIEW_NAMES:
            view_keyword = self.VIEW_PROMPTS[view_name]
            prompt = f"{base}, {view_keyword}"

            logger.info(f"  生成 {view_name} 视角...")

            # 择优生成
            best = await self._generate_best(prompt, self.num_candidates)

            if not best:
                logger.warning(f"  {view_name} 视角生成失败")
                continue

            # 下载并保存
            image_data = await self._download_image(best["url"])
            if not image_data:
                logger.warning(f"  {view_name} 视角下载失败")
                continue

            filepath = self.file_saver.save_character_image(
                char["name"], view_name, image_data
            )
            views[view_name] = str(filepath)
            logger.info(f"  {view_name} 已保存: {filepath}")

        return views

    async def _generate_best(
        self, prompt: str, num_candidates: int
    ) -> Optional[Dict[str, Any]]:
        """
        择优生成最佳图片

        Args:
            prompt: 生成提示词
            num_candidates: 候选数量

        Returns:
            最佳候选结果
        """
        candidates = []

        for i in range(num_candidates):
            seed = random.randint(1000, 9999)
            try:
                result = await self.agnes_client.generate_image(
                    prompt=prompt,
                    seed=seed,
                    size=self.default_size,
                )
                if "data" in result and len(result["data"]) > 0:
                    url = result["data"][0].get("url")
                    if url:
                        candidates.append({"url": url, "seed": seed, "index": i})
                        logger.info(f"    候选{i+1}生成成功")
            except Exception as e:
                logger.warning(f"    候选{i+1}生成失败: {e}")
                continue

        if not candidates:
            return None

        if len(candidates) == 1:
            return candidates[0]

        # 评估选最佳
        scored = []
        for c in candidates:
            try:
                score = await self.evaluator.evaluate(
                    image_url=c["url"],
                    criteria={"prompt": prompt, "references": []},
                )
                c["score"] = score
                scored.append(c)
            except Exception as e:
                logger.warning(f"    评估失败: {e}")
                continue

        if not scored:
            return candidates[0]

        best = max(scored, key=lambda x: x["score"]["total"])
        logger.info(f"    最佳得分: {best['score']['total']}/10")
        return best

    async def _download_image(self, url: str) -> Optional[bytes]:
        """下载图片"""
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=300)
                response.raise_for_status()
                return response.content
        except Exception as e:
            logger.error(f"图片下载失败: {e}")
            return None
