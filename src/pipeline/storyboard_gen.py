"""
分镜生成阶段
为每个场景生成分镜图，保持角色一致性和风格统一
"""

import random
from pathlib import Path
from typing import Dict, Any, List, Optional

from ..utils.logger import setup_logger
from ..utils.file_saver import FileSaver
from ..utils.evaluator import ImageEvaluator

logger = setup_logger("storyboard_gen")


class StoryboardGenerator:
    """分镜生成器"""

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
        self.num_candidates = config["settings"]["image"].get("num_candidates", 3)
        self.threshold = config["settings"]["image"].get("quality_threshold", 7.0)

    async def generate(
        self,
        scenes: List[Dict[str, Any]],
        characters: List[Dict[str, Any]],
        style: str,
    ) -> List[Dict[str, Any]]:
        """
        为所有场景生成分镜图

        Args:
            scenes: 场景列表
            characters: 角色列表
            style: 画风描述

        Returns:
            更新后的场景列表（包含分镜图路径）
        """
        logger.info(f"开始生成分镜图: {len(scenes)}个场景")

        # 构建角色参考图映射
        char_refs = {}
        for char in characters:
            char_refs[char["name"]] = char.get("reference_images", {})

        previous_frame = None

        for scene in scenes:
            logger.info(f"生成场景{scene['id']}: {scene['description'][:30]}...")

            # 构建参考图列表
            references = []

            # 添加角色参考图
            for char_name in scene.get("characters", []):
                if char_name in char_refs:
                    front_ref = char_refs[char_name].get("front")
                    if front_ref:
                        references.append(front_ref)

            # 添加上一帧（保持风格一致）
            if previous_frame:
                references.append(previous_frame)

            # 构建提示词
            prompt = self._build_prompt(scene, style)

            # 择优生成
            best_result = await self._generate_best(prompt, references)

            if best_result:
                # 下载图片
                image_data = await self._download_image(best_result["url"])
                if image_data:
                    # 保存图片
                    filepath = self.file_saver.save_storyboard(scene["id"], image_data)
                    scene["image_path"] = str(filepath)
                    scene["score"] = best_result.get("score", {})
                    previous_frame = str(filepath)
                    logger.info(f"  场景{scene['id']}已保存: {filepath}")
                else:
                    logger.warning(f"  场景{scene['id']}下载失败")
                    scene["status"] = "failed"
            else:
                logger.warning(f"  场景{scene['id']}生成失败")
                scene["status"] = "failed"

        return scenes

    def _build_prompt(self, scene: Dict[str, Any], style: str) -> str:
        """
        构建生成提示词

        Args:
            scene: 场景信息
            style: 画风

        Returns:
            提示词
        """
        parts = []

        # 使用增强后的提示词（如果有）
        if "enhanced_prompt" in scene:
            parts.append(scene["enhanced_prompt"])
        else:
            parts.append(scene["description"])

        # 添加角色描述
        if scene.get("characters"):
            parts.append(f"featuring {', '.join(scene['characters'])}")

        # 添加镜头
        if scene.get("camera"):
            parts.append(scene["camera"])

        # 添加光照
        if scene.get("lighting"):
            parts.append(scene["lighting"])

        # 添加风格
        if style:
            parts.append(style)

        # 添加质量要求
        parts.append("masterpiece, best quality, high detail")

        return ", ".join(parts)

    async def _generate_best(
        self, prompt: str, references: List[str]
    ) -> Optional[Dict[str, Any]]:
        """
        择优生成最佳图片

        Args:
            prompt: 生成提示词
            references: 参考图列表

        Returns:
            最佳候选结果
        """
        candidates = []

        # 生成多个候选
        for i in range(self.num_candidates):
            seed = random.randint(1000, 9999)
            try:
                result = await self.agnes_client.generate_image(
                    prompt=prompt,
                    image_refs=references if references else None,
                    seed=seed,
                )
                if "data" in result and len(result["data"]) > 0:
                    url = result["data"][0].get("url")
                    if url:
                        candidates.append({"url": url, "seed": seed, "index": i})
            except Exception as e:
                logger.warning(f"  候选{i+1}生成失败: {e}")
                continue

        if not candidates:
            return None

        # 评估并选择最佳
        scored_candidates = []
        for candidate in candidates:
            try:
                score = await self.evaluator.evaluate(
                    image_url=candidate["url"],
                    criteria={"prompt": prompt, "references": references},
                )
                candidate["score"] = score
                scored_candidates.append(candidate)
            except Exception as e:
                logger.warning(f"  候选评估失败: {e}")
                continue

        if not scored_candidates:
            return candidates[0]

        # 选择得分最高的
        best = max(scored_candidates, key=lambda x: x["score"]["total"])
        logger.info(f"  最佳候选得分: {best['score']['total']}/10")

        # 检查是否达标
        if best["score"]["total"] < self.threshold:
            logger.warning(
                f"  得分未达标 ({best['score']['total']}/{self.threshold})，但继续使用"
            )

        return best

    async def _download_image(self, url: str) -> Optional[bytes]:
        """
        下载图片

        Args:
            url: 图片URL

        Returns:
            图片数据
        """
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=300)
                response.raise_for_status()
                return response.content
        except Exception as e:
            logger.error(f"图片下载失败: {e}")
            return None
