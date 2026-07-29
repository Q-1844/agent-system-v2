"""
Mimo TTS客户端
封装语音合成API调用
包含重试机制、返回格式验证
"""

import asyncio
import base64
from typing import Optional

import httpx

from ..utils.logger import setup_logger

logger = setup_logger("mimo_client")


class MimoTTSClient:
    """Mimo TTS客户端"""

    def __init__(self, config: dict):
        self.config = config
        self.base_url = config["base_url"]
        self.api_key = config["api_key"]
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self.max_retries = 5

    async def synthesize(
        self, text: str, voice: str = "default", speed: float = 1.0
    ) -> bytes:
        """
        语音合成

        Args:
            text: 要合成的文本
            voice: 语音类型
            speed: 语速

        Returns:
            音频数据 (bytes)

        Raises:
            Exception: API调用失败或返回格式错误
        """
        data = {
            "model": self.config["models"]["tts"],
            "messages": [
                {"role": "user", "content": text},
                {"role": "assistant", "content": ""},
            ],
        }

        async with httpx.AsyncClient() as client:
            for attempt in range(self.max_retries):
                try:
                    response = await client.post(
                        self.base_url,
                        headers=self.headers,
                        json=data,
                        timeout=120,
                    )

                    if response.status_code != 200:
                        if response.status_code == 429:
                            wait_time = 2**attempt * 3
                            logger.warning(
                                f"速率限制，等待 {wait_time}秒后重试..."
                            )
                            await asyncio.sleep(wait_time)
                            continue
                        raise Exception(
                            f"TTS API错误: {response.status_code} - {response.text}"
                        )

                    result = response.json()

                    # 验证返回格式
                    if "choices" not in result or len(result["choices"]) == 0:
                        raise Exception(f"TTS返回格式错误: {result}")

                    choice = result["choices"][0]
                    if "message" not in choice:
                        raise Exception(f"TTS返回缺少message字段: {choice}")

                    message = choice["message"]
                    if "audio" not in message:
                        raise Exception(f"TTS返回缺少audio字段: {message}")

                    audio = message["audio"]
                    if "data" not in audio:
                        raise Exception(f"TTS返回缺少audio.data字段: {audio}")

                    audio_bytes = base64.b64decode(audio["data"])
                    logger.info(f"语音合成成功: {len(audio_bytes)} 字节")
                    return audio_bytes

                except httpx.TimeoutException:
                    if attempt == self.max_retries - 1:
                        raise Exception("TTS API调用超时，已重试3次")
                    logger.warning(f"超时，正在重试 ({attempt + 1}/3)...")
                    await asyncio.sleep(2)

                except Exception as e:
                    if attempt == self.max_retries - 1:
                        raise
                    logger.warning(
                        f"请求失败: {e}，正在重试 ({attempt + 1}/{self.max_retries})..."
                    )
                    await asyncio.sleep(2)

            raise Exception("语音合成失败")
