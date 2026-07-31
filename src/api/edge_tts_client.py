"""
Edge TTS 客户端（免费，无需 API Key / 会员 / 代理）。
基于微软 Edge 浏览器神经网络语音，替代过期的 Mimo TTS。

阶段 2（Audio-First）新增 synthesize_full：返回真实时长 + 词级时间戳（WordBoundary），
用于反推 num_frames 与生成词级 SRT。旧 synthesize（返回 bytes）保留为垫片，供
v1 管线（tts_gen）过渡使用，阶段 5 清理。
"""
from __future__ import annotations
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional

from ..utils.logger import setup_logger

logger = setup_logger("edge_tts_client")

# 音色表（含 v1 旧 key 与 v2 新 key，统一一张表）
VOICES = {
    "default":  "zh-CN-XiaoxiaoNeural",   # 晓晓 · 温柔女声
    "female":   "zh-CN-XiaoxiaoNeural",
    "male":     "zh-CN-YunxiNeural",       # 云希 · 沉稳男声
    "female2":  "zh-CN-XiaoyiNeural",      # 晓伊 · 活泼女声
    "male2":    "zh-CN-YunyangNeural",     # 云扬 · 少年音
    "xiaoyi":   "zh-CN-XiaoyiNeural",
    "yunyang":  "zh-CN-YunyangNeural",
    "xiaochen": "zh-CN-XiaochenNeural",
}
# v1 兼容别名
VOICE_MAP = VOICES


class EdgeTTSClient:
    """Edge TTS 客户端（免费）。"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        try:
            import edge_tts  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False
            logger.error("未安装 edge-tts，请运行: pip install edge-tts")

    # ---------- 内部：流式合成，同时收词级时间戳 ----------
    async def _stream(self, text: str, voice_id: str,
                      rate: str = "+0%", volume: str = "+0%"
                      ) -> tuple[bytes, List[Dict[str, Any]]]:
        import edge_tts
        # edge-tts 7.x：boundary 默认 SentenceBoundary，必须显式传 WordBoundary 才能拿词级
        comm = edge_tts.Communicate(text, voice_id, rate=rate, volume=volume,
                                    boundary="WordBoundary")
        audio = bytearray()
        words: List[Dict[str, Any]] = []
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # edge-tts 的 offset/duration 单位是 100 纳秒（ticks）
                words.append({
                    "text": chunk["text"],
                    "offset_ms": chunk["offset"] // 10_000,
                    "duration_ms": chunk["duration"] // 10_000,
                })
        if not audio:
            raise RuntimeError(f"edge-tts 返回空音频，text={text[:40]!r}")
        return bytes(audio), words

    # ---------- 阶段 2 主接口：词级时间戳 + 真实时长 ----------
    async def synthesize_full(self, text: str, *, voice: str = "default",
                              rate: str = "+0%", volume: str = "+0%",
                              out_path: str | Path) -> Dict[str, Any]:
        """合成语音并返回真实时长 + 词级时间戳。

        返回：{"path": str, "duration_ms": int,
               "words": [{"text": "你好", "offset_ms": 0, "duration_ms": 320}, ...]}
        """
        if not self._available:
            raise RuntimeError("edge-tts 未安装，请运行: pip install edge-tts")
        voice_id = VOICES.get(voice, voice)
        out_path = Path(out_path); out_path.parent.mkdir(parents=True, exist_ok=True)

        audio, words = await self._stream(text, voice_id, rate=rate, volume=volume)
        out_path.write_bytes(audio)

        duration_ms = await self._probe_duration_ms(out_path)
        # WordBoundary 只覆盖到最后一个词，尾部静音拿不到；ffprobe 失败时用词边界兜底
        if duration_ms <= 0 and words:
            last = words[-1]
            duration_ms = last["offset_ms"] + last["duration_ms"] + 200

        logger.info("TTS 完成 %s: %.2fs, %d 个词边界",
                    out_path.name, duration_ms / 1000, len(words))
        return {"path": str(out_path), "duration_ms": duration_ms, "words": words}

    async def _probe_duration_ms(self, path: Path) -> int:
        """用 ffprobe 拿准确时长。WordBoundary 只覆盖到最后一个词，
        尾部静音拿不到，所以必须 ffprobe。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _ = await proc.communicate()
            return int(float(out.decode().strip()) * 1000)
        except Exception:
            logger.warning("ffprobe 读取时长失败: %s", path)
            return 0

    @staticmethod
    def list_voices() -> List[dict]:
        return [{"id": k, "voice": v} for k, v in VOICES.items()]
