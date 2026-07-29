"""
Edge TTS 客户端（免费，无需 API Key / 会员）
基于微软 Edge 浏览器神经网络语音，替代过期的 Mimo TTS。
接口与 MimoTTSClient 兼容：synthesize(text, voice, speed) -> bytes
"""

import io
from typing import Optional

from ..utils.logger import setup_logger

logger = setup_logger("edge_tts_client")

# 常用中文音色
VOICE_MAP = {
    "default": "zh-CN-XiaoxiaoNeural",
    "female": "zh-CN-XiaoxiaoNeural",   # 晓晓（女声，温柔）
    "male": "zh-CN-YunxiNeural",         # 云希（男声，沉稳）
    "xiaoyi": "zh-CN-XiaoyiNeural",      # 晓伊（女声，活泼）
    "yunyang": "zh-CN-YunyangNeural",    # 云扬（男声，新闻）
    "xiaochen": "zh-CN-XiaochenNeural",  # 晓晨（女声）
}


class EdgeTTSClient:
    """Edge TTS 客户端（免费）"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        # edge-tts 无需 API Key
        try:
            import edge_tts  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False
            logger.error("未安装 edge-tts，请运行: pip install edge-tts")

    async def synthesize(
        self, text: str, voice: str = "default", speed: float = 1.0
    ) -> bytes:
        """
        语音合成

        Args:
            text: 要合成的文本
            voice: 音色（default/female/male 或直接传 zh-CN-XiaoxiaoNeural）
            speed: 语速（1.0 = 正常，1.5 = 1.5倍速）

        Returns:
            音频数据 (bytes, mp3)
        """
        if not self._available:
            raise Exception("edge-tts 未安装，请运行: pip install edge-tts")

        import edge_tts

        # 音色映射
        v = VOICE_MAP.get(voice, voice) if voice else "zh-CN-XiaoxiaoNeural"
        # speed 1.0 -> rate "+0%", 1.5 -> "+50%", 0.8 -> "-20%"
        rate_pct = int(round((speed - 1.0) * 100))
        rate = f"+{rate_pct}%" if rate_pct >= 0 else f"{rate_pct}%"

        buf = io.BytesIO()
        communicate = edge_tts.Communicate(text, v, rate=rate)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])

        audio_bytes = buf.getvalue()
        if not audio_bytes:
            raise Exception(f"Edge TTS 合成失败（空音频）: voice={v}, text={text[:30]}")

        logger.info(f"Edge TTS 合成成功: {len(audio_bytes)} 字节, voice={v}, rate={rate}")
        return audio_bytes
