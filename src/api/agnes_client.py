"""
Agnes API客户端
封装文本理解、图片生成、视频生成的API调用
包含重试机制、超时处理、速率限制处理
"""

import asyncio
from typing import Optional, List, Dict, Any

import httpx

from ..utils.logger import setup_logger

logger = setup_logger("agnes_client")


class AgnesClient:
    """Agnes API客户端"""

    def __init__(self, config: dict):
        self.config = config
        self.base_url = config["base_url"]
        self.api_key = config["api_key"]
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self.max_retries = config["settings"]["image"].get("max_retries", 5)
        self.timeout = config["settings"]["image"].get("timeout", 300)
        # Agnes 在 Cloudflare 后，国内需代理；从 config.proxy 读取，留空则直连
        self.proxy = config.get("proxy") or None

    async def generate_image(
        self,
        prompt: str,
        image_refs: Optional[List[str]] = None,
        seed: Optional[int] = None,
        **kwargs,
    ) -> dict:
        """
        生成图片

        Args:
            prompt: 生成提示词
            image_refs: 参考图URL列表
            seed: 随机种子
            **kwargs: 其他参数

        Returns:
            包含图片URL的字典

        Raises:
            Exception: API调用失败
        """
        data = {
            "model": self.config["models"]["image"],
            "prompt": prompt,
            "size": kwargs.get("size", self.config["settings"]["image"]["size"]),
            "extra_body": {"response_format": "url"},
        }

        if image_refs:
            data["extra_body"]["image"] = image_refs

        if seed:
            data["seed"] = seed

        async with httpx.AsyncClient(proxy=self.proxy) as client:
            for attempt in range(self.max_retries):
                try:
                    response = await client.post(
                        f"{self.base_url}/v1/images/generations",
                        headers=self.headers,
                        json=data,
                        timeout=self.timeout,
                    )

                    if response.status_code == 200:
                        result = response.json()
                        # 验证返回格式
                        if "data" in result and len(result["data"]) > 0:
                            url = result["data"][0].get("url")
                            if url:
                                logger.info(f"图片生成成功: {url[:50]}...")
                                return result
                            else:
                                raise Exception(f"返回数据中没有URL: {result}")
                        else:
                            raise Exception(f"返回格式错误: {result}")

                    elif response.status_code == 429:
                        wait_time = 2**attempt * 5
                        logger.warning(f"速率限制，等待 {wait_time}秒后重试...")
                        await asyncio.sleep(wait_time)
                    else:
                        raise Exception(
                            f"API错误: {response.status_code} - {response.text}"
                        )

                except httpx.TimeoutException:
                    if attempt == self.max_retries - 1:
                        raise Exception(
                            f"API调用超时，已重试{self.max_retries}次"
                        )
                    logger.warning(
                        f"超时，正在重试 ({attempt + 1}/{self.max_retries})..."
                    )
                    await asyncio.sleep(2)

                except Exception as e:
                    if attempt == self.max_retries - 1:
                        raise
                    logger.warning(f"请求失败: {e}，正在重试 ({attempt + 1}/{self.max_retries})...")
                    await asyncio.sleep(2)

            raise Exception("图片生成失败")

    async def generate_video(
        self,
        prompt: str,
        image_refs: List[str],
        mode: str = "keyframes",
        **kwargs,
    ) -> dict:
        """
        生成视频

        Args:
            prompt: 生成提示词
            image_refs: 参考图URL列表
            mode: 生成模式 ('keyframes' 或 'ti2vid')
            **kwargs: 其他参数

        Returns:
            包含视频URL的字典

        Raises:
            Exception: API调用失败或超时
        """
        data = {
            "model": self.config["models"]["video"],
            "prompt": prompt,
            "extra_body": {"image": image_refs, "mode": mode},
            "num_frames": kwargs.get(
                "num_frames", self.config["settings"]["video"]["num_frames"]
            ),
            "frame_rate": kwargs.get(
                "frame_rate", self.config["settings"]["video"]["frame_rate"]
            ),
        }

        max_retries = self.config["settings"]["video"].get(
            "max_polling_retries", 180
        )
        polling_interval = self.config["settings"]["video"]["polling_interval"]

        async with httpx.AsyncClient(proxy=self.proxy) as client:
            # 创建任务
            try:
                response = await client.post(
                    f"{self.base_url}/v1/videos",
                    headers=self.headers,
                    json=data,
                    timeout=120,
                )

                if response.status_code != 200:
                    raise Exception(
                        f"创建视频任务失败: {response.status_code} - {response.text}"
                    )

                task_data = response.json()
                video_id = task_data.get("video_id")

                if not video_id:
                    raise Exception(f"返回数据中没有video_id: {task_data}")

                logger.info(f"视频任务已创建: {video_id}")

            except httpx.TimeoutException:
                raise Exception("创建视频任务超时")

            # 轮询等待完成
            for retry_count in range(max_retries):
                await asyncio.sleep(polling_interval)

                try:
                    result = await client.get(
                        f"{self.base_url}/agnesapi?video_id={video_id}",
                        headers=self.headers,
                        timeout=60,
                    )
                    status_data = result.json()

                    if status_data["status"] == "completed":
                        video_url = status_data.get("remixed_from_video_id")
                        if video_url:
                            logger.info(f"视频生成完成: {video_url[:50]}...")
                        else:
                            logger.warning("视频完成但没有URL")
                        return status_data

                    elif status_data["status"] == "failed":
                        error_msg = status_data.get("error", "未知错误")
                        raise Exception(f"视频生成失败: {error_msg}")

                    # 显示进度 (每30秒显示一次)
                    if retry_count % 6 == 0:
                        elapsed = (retry_count + 1) * polling_interval
                        logger.info(
                            f"等待中... 已等待 {elapsed}秒 ({elapsed // 60}分{elapsed % 60}秒)"
                        )

                except httpx.TimeoutException:
                    logger.warning("轮询超时，继续等待...")
                    continue

            raise Exception(
                f"视频生成超时: 超过最大等待时间 {max_retries * polling_interval}秒"
            )

    async def understand_text(self, prompt: str, **kwargs) -> str:
        """
        文本理解（用于剧本解析等）

        Args:
            prompt: 输入文本
            **kwargs: 其他参数

        Returns:
            模型回复文本

        Raises:
            Exception: API调用失败
        """
        data = {
            "model": self.config["models"]["text"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 4096),
        }

        async with httpx.AsyncClient(proxy=self.proxy) as client:
            for attempt in range(self.max_retries):
                try:
                    response = await client.post(
                        f"{self.base_url}/v1/chat/completions",
                        headers=self.headers,
                        json=data,
                        timeout=self.timeout,
                    )

                    if response.status_code == 200:
                        result = response.json()
                        content = result["choices"][0]["message"]["content"]
                        logger.info(f"文本理解成功: {content[:50]}...")
                        return content

                    elif response.status_code == 429:
                        wait_time = 2**attempt * 5
                        logger.warning(f"速率限制，等待 {wait_time}秒后重试...")
                        await asyncio.sleep(wait_time)
                    else:
                        raise Exception(
                            f"API错误: {response.status_code} - {response.text}"
                        )

                except httpx.TimeoutException:
                    if attempt == self.max_retries - 1:
                        raise Exception(
                            f"API调用超时，已重试{self.max_retries}次"
                        )
                    logger.warning(
                        f"超时，正在重试 ({attempt + 1}/{self.max_retries})..."
                    )
                    await asyncio.sleep(2)

            raise Exception("文本理解失败")
