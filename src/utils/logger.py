"""
日志工具模块
提供统一的日志记录功能，支持文件和控制台输出
"""

import logging
from datetime import datetime
from pathlib import Path


def setup_logger(name: str, log_dir: str = "./logs") -> logging.Logger:
    """
    设置日志记录器

    Args:
        name: 日志记录器名称
        log_dir: 日志目录

    Returns:
        配置好的日志记录器
    """
    # 创建日志目录
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    # 创建日志记录器
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 避免重复添加处理器
    if logger.handlers:
        return logger

    # 文件处理器 - 记录所有级别
    log_file = f"{log_dir}/{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)

    # 控制台处理器 - 只显示INFO及以上（强制UTF-8编码）
    import sys
    console_handler = logging.StreamHandler(stream=sys.stderr)
    console_handler.setLevel(logging.INFO)

    # 格式化器
    file_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    file_handler.setFormatter(file_formatter)
    console_handler.setFormatter(console_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger
