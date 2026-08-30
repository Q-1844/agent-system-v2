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

from typing import Optional

from ..api.agnes_client import AgnesClient
from ..utils.logger import setup_logger
from ..utils.project_manager import ProjectManager

logger = setup_logger("pipeline_controller")


class PipelineController:
    """配置与客户端容器（v1 六阶段管线已于阶段 5 移除）。"""

    def __init__(self, config_path: str = "./config/config.yaml"):
        self.config = self._load_config(config_path)
        self.project_manager = ProjectManager(self.config["project"]["base_dir"])
        # 文本 provider 分层（2026-08-29 Agent 模型切换）：
        # 1) config.text_provider = 全局文本默认路由（当前 agnes），决定主客户端
        #    agnes_client 的 text_endpoint；图片/视频不受影响。
        # 2) sensenova 配置块完整时，常备一个独立 SenseNova 文本客户端
        #    sensenova_text_client——runtime 按 Agent 绑定的 ModelProfile.text_provider
        #    为 script_writer 在两者间选择（Agent 输入区开关，下一次 turn 起生效）。
        # provider 差异均在 client 层适配，业务层/Agent 不感知；无自动路由/fallback。
        sn_ep = self._sensenova_text_endpoint()
        main_ep = sn_ep if self._config_text_provider() == "sensenova" else None
        if main_ep is not None:
            logger.info("全局文本默认 provider = SenseNova（model=%s）", main_ep["model"])
        else:
            logger.info("全局文本默认 provider = Agnes（model=%s）",
                        self.config["agnes"]["models"]["text"])
        self.agnes_client = AgnesClient(self.config["agnes"], text_endpoint=main_ep)
        self.sensenova_text_client = (AgnesClient(self.config["agnes"],
                                                  text_endpoint=sn_ep)
                                      if sn_ep else None)

    def _config_text_provider(self) -> str:
        tp = str(self.config.get("text_provider") or "agnes").strip()
        return tp if tp in ("agnes", "sensenova") else "agnes"

    def _sensenova_text_endpoint(self) -> Optional[dict]:
        """sensenova 配置块 -> 文本 endpoint 注入参数；块不完整/无 key 返回 None。
        只看 sensenova 块本身（与全局默认 text_provider 解耦）。"""
        sn = self.config.get("sensenova") or {}
        api_key = str(sn.get("api_key") or "")
        base_url, model = sn.get("base_url"), (sn.get("models") or {}).get("text")
        if not api_key or "${" in api_key or not base_url or not model:
            logger.warning("sensenova 配置不完整（base_url/api_key/models.text），"
                           "SenseNova 文本客户端不可用")
            return None
        return {"base_url": str(base_url).rstrip("/"), "api_key": api_key,
                "model": str(model), "provider": "sensenova"}

    def _load_config(self, config_path: str) -> dict:
        """加载配置文件 + 环境变量替换（AGNES_API_KEY / MIMO_API_KEY / SENSENOVA_API_KEY）。"""
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

        if "${SENSENOVA_API_KEY}" in str(config.get("sensenova", {}).get("api_key", "")):
            api_key = os.environ.get("SENSENOVA_API_KEY", "")
            if api_key:
                config["sensenova"]["api_key"] = api_key
            else:
                logger.warning("未设置SENSENOVA_API_KEY环境变量")

        return config
