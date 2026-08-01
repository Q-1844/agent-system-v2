"""
剧本解析阶段
解析YAML剧本文件，提取角色和场景信息
"""

import json
from pathlib import Path
from typing import Dict, Any, List

import yaml

from ..utils.logger import setup_logger

logger = setup_logger("script_parser")


class ScriptParser:
    """剧本解析器"""

    def __init__(self, agnes_client):
        self.agnes_client = agnes_client

    async def parse(self, script_path: str) -> Dict[str, Any]:
        """
        解析剧本文件

        Args:
            script_path: 剧本文件路径

        Returns:
            解析结果字典，包含:
                - project: 项目信息
                - characters: 角色列表
                - scenes: 场景列表
        """
        logger.info(f"开始解析剧本: {script_path}")

        # 读取YAML文件
        with open(script_path, "r", encoding="utf-8") as f:
            script_data = yaml.safe_load(f)

        # 验证必需字段
        self._validate_script(script_data)

        # 提取信息
        result = {
            "project": {
                "name": script_data.get("project", "未命名项目"),
                "style": script_data.get("style", ""),
                "size": script_data.get("size", "1152x768"),
            },
            "characters": script_data.get("characters", []),
            "scenes": script_data.get("scenes", []),
        }

        # 为每个场景添加默认状态
        for scene in result["scenes"]:
            scene["status"] = "pending"

        logger.info(
            f"剧本解析完成: {len(result['characters'])}个角色, {len(result['scenes'])}个场景"
        )

        return result

    def _validate_script(self, script_data: Dict[str, Any]):
        """
        验证剧本格式

        Args:
            script_data: 剧本数据

        Raises:
            ValueError: 格式不正确
        """
        if not isinstance(script_data, dict):
            raise ValueError("剧本文件格式错误: 应为YAML字典")

        if "scenes" not in script_data:
            raise ValueError("剧本文件缺少scenes字段")

        scenes = script_data["scenes"]
        if not isinstance(scenes, list) or len(scenes) == 0:
            raise ValueError("scenes字段应为非空列表")

        for i, scene in enumerate(scenes):
            if "id" not in scene:
                raise ValueError(f"场景{i+1}缺少id字段")
            if "description" not in scene:
                raise ValueError(f"场景{i+1}缺少description字段")

    async def enhance_descriptions(
        self, parsed_script: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        使用AI增强场景描述

        Args:
            parsed_script: 解析后的剧本

        Returns:
            增强后的剧本
        """
        logger.info("开始增强场景描述...")

        style = parsed_script["project"]["style"]

        for scene in parsed_script["scenes"]:
            # 生成英文描述（用于API调用）
            prompt = f"""请将以下中文场景描述转换为详细的英文图片生成提示词。

场景描述: {scene['description']}
角色: {', '.join(scene.get('characters', []))}
镜头: {scene.get('camera', '')}
光照: {scene.get('lighting', '')}
动作: {scene.get('motion', '')}
画风: {style}

请生成一个完整的英文提示词，包含主体、动作、场景、镜头、光照、风格等要素。
只返回提示词，不要添加其他文字。"""

            try:
                enhanced = await self.agnes_client.chat(
                    [{"role": "user", "content": prompt}],
                    temperature=0.7, max_tokens=4096,
                )
                scene["enhanced_prompt"] = enhanced.strip()
                logger.info(f"场景{scene['id']}描述已增强")
            except Exception as e:
                logger.warning(f"场景{scene['id']}描述增强失败: {e}")
                # 使用原始描述作为后备
                scene["enhanced_prompt"] = scene["description"]

        return parsed_script
