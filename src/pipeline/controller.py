"""
流水线控制器（阶段 5 瘦身：v1 六阶段逻辑已移除）

原 v1 六阶段管线（剧本解析 / 角色设定 / 分镜 / 动画 / TTS / 合成）
已被阶段 3 的 shot 级管线 + JobQueue 取代。本文件现仅作为「配置与客户端工厂」：
api_server.startup 调用一次构造，再把 agnes_client / project_manager 拆到 deps 单例
（get_agnes_client / get_project_manager），各 router 不再经 controller 访问。

    controller.config            # config.yaml 加载结果（含 ${AGNES_API_KEY} 环境变量替换）
    controller.project_manager   # 项目目录管理（已拆到 deps.project_manager）
    controller.agnes_client      # AgnesClient 单例（已拆到 deps.agnes_client）

🔴 阶段5③-b 已把 42 处 controller.X 引用迁到 deps getter；本文件保留为工厂（构造逻辑 +
env 替换不动——单测无法证明 env 替换正确，故不搬构造）。整体删除留待垫片1/2 清理轮：
那时改请求参数 size= -> size_tier/ratio、data[0].url -> result["url"]，构造可一并迁入
deps，并发真实 Agnes 请求验证（含生成图尺寸是否符合角色页预期比例）。
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
