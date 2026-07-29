"""
方法三: remove.bg API 背景移除
官网: https://www.remove.bg/api
安装: pip install removebg

特点:
- 商业级API，效果最好
- 无需本地GPU
- 需要API Key (注册送50次免费额度)
- 对人像抠图效果极佳
"""

import io
import os
import time
import tempfile
from typing import Optional, Tuple

from ..utils.logger import setup_logger

logger = setup_logger("removebg_api_module")


class RemoveBgApiSegmentor:
    """remove.bg API 背景移除器"""

    def __init__(self, api_key: Optional[str] = None):
        """
        初始化 remove.bg 分割器

        Args:
            api_key: remove.bg API Key
                     如不传入，从环境变量 REMOVEBG_API_KEY 读取
        """
        self.api_key = api_key or os.environ.get("REMOVEBG_API_KEY", "")
        self._available = None

    def _check_available(self) -> bool:
        """检查是否可用"""
        if self._available is not None:
            return self._available

        if not self.api_key:
            self._available = False
            logger.error(
                "remove.bg API Key 未配置。"
                "请设置环境变量 REMOVEBG_API_KEY 或在初始化时传入 api_key。"
                "注册地址: https://www.remove.bg/api"
            )
            return False

        try:
            from removebg import RemoveBg
            self._available = True
            logger.info("removebg SDK 可用")
        except ImportError:
            self._available = False
            logger.error("removebg SDK 未安装，请运行: pip install removebg")
        return self._available

    def remove_background(
        self,
        image_bytes: bytes,
        size: str = "auto",
        bg_color: str = "",
    ) -> Tuple[Optional[bytes], dict]:
        """
        移除背景

        Args:
            image_bytes: 输入图片字节
            size: 输出尺寸 "auto" / "small" / "regular" / "medium" / "hd" / "4k"
            bg_color: 背景颜色 (如 "#FFFFFF" 填白)，留空为透明

        Returns:
            (输出图片字节, 元数据字典)
        """
        if not self._check_available():
            return None, {
                "error": "remove.bg 未配置",
                "method": "remove.bg",
            }

        from removebg import RemoveBg

        start_time = time.time()
        metadata = {
            "method": "remove.bg",
            "size": size,
            "bg_color": bg_color or "transparent",
        }

        try:
            rmbg = RemoveBg(self.api_key, "error.log")

            # removebg SDK 需要文件路径，写入临时文件
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_in:
                tmp_in.write(image_bytes)
                tmp_in_path = tmp_in.name

            output_path = tmp_in_path.replace(".png", "_nobg.png")

            rmbg.remove_background_from_img_file(
                tmp_in_path,
                size=size,
                bg_color=bg_color if bg_color else None,
            )

            # 读取输出
            if os.path.exists(output_path):
                with open(output_path, "rb") as f:
                    output_bytes = f.read()
                os.unlink(output_path)
            else:
                raise Exception("remove.bg 未生成输出文件")

            # 清理临时文件
            if os.path.exists(tmp_in_path):
                os.unlink(tmp_in_path)

            elapsed = time.time() - start_time
            metadata["elapsed_seconds"] = round(elapsed, 2)
            metadata["input_size"] = len(image_bytes)
            metadata["output_size"] = len(output_bytes)
            metadata["success"] = True

            logger.info(f"remove.bg 处理完成: {elapsed:.2f}s")
            return output_bytes, metadata

        except Exception as e:
            elapsed = time.time() - start_time
            metadata["elapsed_seconds"] = round(elapsed, 2)
            metadata["error"] = str(e)
            metadata["success"] = False
            logger.error(f"remove.bg 处理失败: {e}")
            return None, metadata

    def is_available(self) -> bool:
        """检查是否可用"""
        return self._check_available()
