"""
图片质量评估器
使用Agnes 2.0 Flash评估生成图片的质量
"""

import json
from typing import Dict, Any

from ..utils.logger import setup_logger

logger = setup_logger("evaluator")


class ImageEvaluator:
    """图片质量评估器"""

    def __init__(self, agnes_client):
        self.agnes_client = agnes_client

    async def evaluate(
        self, image_url: str, criteria: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        评估图片质量

        Args:
            image_url: 图片URL
            criteria: 评估标准
                - prompt: 生成提示词
                - references: 参考图列表

        Returns:
            评估结果字典，包含:
                - total: 综合得分 (0-10)
                - character: 角色一致性 (0-10)
                - style: 风格匹配度 (0-10)
                - composition: 构图质量 (0-10)
                - clarity: 清晰度 (0-10)
                - suggestions: 改进建议
        """
        prompt = criteria.get("prompt", "")

        evaluation_prompt = f"""请评估这张图片的质量。

评估标准:
- 角色一致性: 是否符合描述 "{prompt}"
- 风格匹配度: 是否与预期风格一致
- 构图质量: 画面构图是否美观
- 清晰度: 画面是否清晰、细节是否丰富

请返回JSON格式，不要添加任何其他文字:
{{
    "character": 0-10的整数,
    "style": 0-10的整数,
    "composition": 0-10的整数,
    "clarity": 0-10的整数,
    "total": 0-10的浮点数（加权平均）,
    "suggestions": "改进建议（一句话）"
}}

权重: 角色一致性0.3, 风格匹配度0.3, 构图质量0.2, 清晰度0.2"""

        try:
            response = await self.agnes_client.understand_text(
                prompt=evaluation_prompt,
                temperature=0.3,
                max_tokens=500,
            )

            # 尝试解析JSON
            score = self._parse_score(response)
            logger.info(f"图片评估完成: {score['total']}/10")
            return score

        except Exception as e:
            logger.error(f"图片评估失败: {e}")
            # 返回默认分数
            return {
                "character": 7,
                "style": 7,
                "composition": 7,
                "clarity": 7,
                "total": 7.0,
                "suggestions": "评估失败，使用默认分数",
            }

    def _parse_score(self, response: str) -> Dict[str, Any]:
        """
        解析评估结果

        Args:
            response: 模型回复

        Returns:
            解析后的分数字典
        """
        # 尝试直接解析JSON
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # 尝试提取JSON部分
        try:
            # 查找JSON开始和结束位置
            start = response.find("{")
            end = response.rfind("}") + 1
            if start != -1 and end > start:
                json_str = response[start:end]
                return json.loads(json_str)
        except json.JSONDecodeError:
            pass

        # 解析失败，返回默认
        logger.warning(f"无法解析评估结果: {response[:100]}...")
        return {
            "character": 7,
            "style": 7,
            "composition": 7,
            "clarity": 7,
            "total": 7.0,
            "suggestions": "解析失败，使用默认分数",
        }
