"""
人物分割模块
提供三种背景移除方法供测试对比
"""

from .rembg_module import RembgSegmentor
from .transparent_bg_module import TransparentBgSegmentor
from .removebg_api_module import RemoveBgApiSegmentor

__all__ = ["RembgSegmentor", "TransparentBgSegmentor", "RemoveBgApiSegmentor"]
