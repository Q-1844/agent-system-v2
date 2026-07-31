"""
流水线控制器（阶段 5 瘦身：v1 六阶段逻辑已移除）

原 v1 六阶段管线（剧本解析 / 角色设定 / 分镜 / 动画 / TTS / 合成）
已被阶段 3 的 shot 级管线 + JobQueue 取代。本文件现仅作为「配置与客户端容器」，
向新管线共享三个对象：

    controller.config            # config.yaml 加载结果（含环境变量替换）
    controller.project_manager   # 项目目录管理（~40 处 shot 级 handler 在用）
    controller.agnes_client      # AgnesClient 单例（全项目共享，限流/配额/连接池复用）

🔴 阶段 5 本轮保留此容器：新管线 ~20 处引用 controller.agnes_client / .config /
.project_manager，迁移到独立单例留待阶段 5 下一轮（③ 拆分时随 agnes_client 单例化 +
deps.py 一并处理，届时本文件可整体删除）。
"""

import yaml

from ..api.agnes_client import AgnesClient
from ..utils.logger import setup_logger
from ..utils.project_manager import ProjectManager

logger = setup_logger("pipeline_controller")


class PipelineController:
    """配置与客户端容器（v1 六阶段管线已于阶段 5 移除）。"""

    def __init__(self, config_path: str = "./config/config.yaml"):
        self.config = self._load_config(config_path)
        self.project_manager = ProjectManager(self.config["project"]["base_dir"])
        self.agnes_client = AgnesClient(self.config["agnes"])

    def _load_config(self, config_path: str) -> dict:
        """加载配置文件 + 环境变量替换（AGNES_API_KEY / MIMO_API_KEY）。"""
        import os

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        if "${AGNES_API_KEY}" in str(config.get("agnes", {}).get("api_key", "")):
            api_key = os.environ.get("AGNES_API_KEY", "")
            if api_key:
                config["agnes"]["api_key"] = api_key
            else:
                logger.warning("未设置AGNES_API_KEY环境变量")

        if "${MIMO_API_KEY}" in str(config.get("mimo", {}).get("api_key", "")):
            api_key = os.environ.get("MIMO_API_KEY", "")
            if api_key:
                config["mimo"]["api_key"] = api_key
            else:
                logger.warning("未设置MIMO_API_KEY环境变量")

        return config
