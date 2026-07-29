"""
方法二: transparent-background 背景移除
GitHub: https://github.com/plemeri/transparent-background
Stars: 3,000+
安装: pip install transparent-background

特点:
- 基于 InSPyReNet (AAAI 2023)
- 支持图片、视频、摄像头流
- 对人像、动物、物体都有效
- 边缘处理精细
"""

import io
import time
from typing import Optional, Tuple

from ..utils.logger import setup_logger

logger = setup_logger("transparent_bg_module")


class TransparentBgSegmentor:
    """transparent-background 背景移除器"""

    # 可用的前景类型
    FOREGROUND_TYPES = ["rgba", "green", "blur", "map"]

    def __init__(self, source: str = "rgba"):
        """
        初始化 transparent-background 分割器

        Args:
            source: 输出类型
                    - "rgba": 透明PNG (推荐)
                    - "green": 绿幕背景
                    - "blur": 模糊背景
                    - "map": 输出mask
        """
        self.source = source
        self._remover = None
        self._available = None

    def _check_available(self) -> bool:
        """检查是否可用"""
        if self._available is not None:
            return self._available
        try:
            from transparent_background import Remover
            self._available = True
            logger.info("transparent-background 可用")
        except ImportError:
            self._available = False
            logger.error(
                "transparent-background 未安装，请运行: pip install transparent-background"
            )
        return self._available

    def _get_remover(self):
        """延迟加载 Remover"""
        if self._remover is None:
            from transparent_background import Remover
            logger.info("正在加载 transparent-background 模型...")
            self._remover = Remover(mode="base")
            logger.info("transparent-background 模型加载完成")
        return self._remover

    def remove_background(
        self,
        image_bytes: bytes,
    ) -> Tuple[Optional[bytes], dict]:
        """
        移除背景

        Args:
            image_bytes: 输入图片字节

        Returns:
            (输出图片字节, 元数据字典)
        """
        if not self._check_available():
            return None, {
                "error": "transparent-background 未安装",
                "method": "transparent-background",
            }

        from PIL import Image

        start_time = time.time()
        metadata = {
            "method": "transparent-background",
            "source": self.source,
        }

        try:
            remover = self._get_remover()

            # 从字节加载图片
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

            # 执行背景移除
            out = remover.process(img, type=self.source)

            # 转回字节
            output_buffer = io.BytesIO()
            out.save(output_buffer, format="PNG")
            output_bytes = output_buffer.getvalue()

            elapsed = time.time() - start_time
            metadata["elapsed_seconds"] = round(elapsed, 2)
            metadata["input_size"] = len(image_bytes)
            metadata["output_size"] = len(output_bytes)
            metadata["success"] = True

            logger.info(f"transparent-background 处理完成: {elapsed:.2f}s")
            return output_bytes, metadata

        except Exception as e:
            elapsed = time.time() - start_time
            metadata["elapsed_seconds"] = round(elapsed, 2)
            metadata["error"] = str(e)
            metadata["success"] = False
            logger.error(f"transparent-background 处理失败: {e}")
            return None, metadata

    def is_available(self) -> bool:
        """检查是否可用"""
        return self._check_available()
