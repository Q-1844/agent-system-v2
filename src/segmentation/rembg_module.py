"""
方法一: rembg 背景移除
GitHub: https://github.com/danielgatis/rembg
Stars: 17,000+
安装: pip install rembg[gpu]  (GPU加速) 或 pip install rembg (CPU)

特点:
- 纯本地运行，无需API
- 基于 U2-Net / ISNet 等模型
- 对动漫/插画风格效果好
- 支持多种模型: u2net, isnet-general-use, silueta, isnet-anime
"""

import io
import time
from typing import Optional, Tuple

from ..utils.logger import setup_logger

logger = setup_logger("rembg_module")


class RembgSegmentor:
    """rembg 背景移除器"""

    # 可用模型列表
    AVAILABLE_MODELS = [
        "u2net",              # 通用，效果最稳定
        "u2net_human_seg",    # 人像专用
        "isnet-general-use",  # 通用，边缘更精细
        "isnet-anime",        # 动漫专用！推荐动漫风格使用
        "silueta",            # 轻量，速度快
    ]

    def __init__(self, model_name: str = "isnet-anime"):
        """
        初始化 rembg 分割器

        Args:
            model_name: 使用的模型名称
                        推荐: isnet-anime (动漫) / u2net (通用)
        """
        self.model_name = model_name
        self._session = None
        self._available = None

    def _check_available(self) -> bool:
        """检查 rembg 是否可用"""
        if self._available is not None:
            return self._available
        try:
            from rembg import remove
            self._available = True
            logger.info(f"rembg 可用 (模型: {self.model_name})")
        except ImportError:
            self._available = False
            logger.error("rembg 未安装，请运行: pip install rembg[gpu]")
        return self._available

    def _get_session(self):
        """延迟加载模型 session"""
        if self._session is None:
            from rembg import new_session
            logger.info(f"正在加载 rembg 模型: {self.model_name} ...")
            self._session = new_session(self.model_name)
            logger.info("rembg 模型加载完成")
        return self._session

    def remove_background(
        self,
        image_bytes: bytes,
        alpha_matting: bool = False,
        alpha_matting_foreground_threshold: int = 240,
        alpha_matting_background_threshold: int = 10,
        alpha_matting_erode_size: int = 10,
    ) -> Tuple[Optional[bytes], dict]:
        """
        移除背景

        Args:
            image_bytes: 输入图片的字节数据
            alpha_matting: 是否启用 alpha matting (边缘更精细但更慢)
            alpha_matting_foreground_threshold: 前景阈值
            alpha_matting_background_threshold: 背景阈值
            alpha_matting_erode_size: 腐蚀大小

        Returns:
            (输出图片字节, 元数据字典)
        """
        if not self._check_available():
            return None, {"error": "rembg 未安装", "method": "rembg"}

        from rembg import remove

        start_time = time.time()
        metadata = {
            "method": "rembg",
            "model": self.model_name,
            "alpha_matting": alpha_matting,
        }

        try:
            session = self._get_session()

            output_bytes = remove(
                image_bytes,
                session=session,
                alpha_matting=alpha_matting,
                alpha_matting_foreground_threshold=alpha_matting_foreground_threshold,
                alpha_matting_background_threshold=alpha_matting_background_threshold,
                alpha_matting_erode_size=alpha_matting_erode_size,
            )

            elapsed = time.time() - start_time
            metadata["elapsed_seconds"] = round(elapsed, 2)
            metadata["input_size"] = len(image_bytes)
            metadata["output_size"] = len(output_bytes)
            metadata["success"] = True

            logger.info(f"rembg 处理完成: {elapsed:.2f}s")
            return output_bytes, metadata

        except Exception as e:
            elapsed = time.time() - start_time
            metadata["elapsed_seconds"] = round(elapsed, 2)
            metadata["error"] = str(e)
            metadata["success"] = False
            logger.error(f"rembg 处理失败: {e}")
            return None, metadata

    @staticmethod
    def list_models() -> list:
        """返回可用模型列表"""
        return RembgSegmentor.AVAILABLE_MODELS

    def is_available(self) -> bool:
        """检查是否可用"""
        return self._check_available()
