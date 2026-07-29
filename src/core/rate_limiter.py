# src/core/rate_limiter.py
# 全局令牌桶限流器（进程级单例）。分桶：text / image_1k / image_2k / video。
# 桶容量 = 1（不允许突发），因为视频 1 RPM 突发就是 429。
# 参考 lcy362/agnes-video-generator (MIT), core/api/rate_limiter.py 的令牌桶写法；
# 但本项目按 AGNES_API_FACTS §4.1 分桶（视频 1 RPM / 图片 20 RPM 分开），更精细。
from __future__ import annotations
import asyncio, time, logging
from dataclasses import dataclass
from typing import Dict

log = logging.getLogger("RateLimiter")

# 依据 AGNES_API_FACTS §4.1（免费/默认用户的"实际 RPM"），再乘 0.85 保险系数
SAFETY = 0.85
DEFAULT_RPM = {
    "text":     20,
    "image_1k": 20,
    "image_2k": 10,
    "image_3k": 1,
    "image_4k": 1,
    "video":    1,     # 🔴 就是 1，不要试图调大
}

@dataclass
class _Bucket:
    rpm: float
    tokens: float
    last: float
    capacity: float = 1.0     # 不允许突发

class RateLimiter:
    """全局令牌桶限流器（进程级单例）。

    用法：
        async with limiter.slot("video"):
            resp = await client.post(...)
    """
    _instance: "RateLimiter | None" = None

    def __init__(self, rpm_map: Dict[str, int] | None = None):
        cfg = {**DEFAULT_RPM, **(rpm_map or {})}
        now = time.monotonic()
        self._buckets = {
            k: _Bucket(rpm=max(v * SAFETY, 0.1), tokens=1.0, last=now)
            for k, v in cfg.items()
        }
        self._lock = asyncio.Lock()
        self._waiting: Dict[str, int] = {k: 0 for k in cfg}

    @classmethod
    def get(cls, rpm_map=None) -> "RateLimiter":
        if cls._instance is None:
            cls._instance = cls(rpm_map)
        return cls._instance

    def _refill(self, b: _Bucket):
        now = time.monotonic()
        b.tokens = min(b.capacity, b.tokens + (now - b.last) * (b.rpm / 60.0))
        b.last = now

    async def acquire(self, bucket: str):
        b = self._buckets.get(bucket)
        if b is None:
            log.warning("未知限流桶 %s，按 text 处理", bucket)
            b = self._buckets["text"]
        self._waiting[bucket] = self._waiting.get(bucket, 0) + 1
        try:
            while True:
                async with self._lock:
                    self._refill(b)
                    if b.tokens >= 1.0:
                        b.tokens -= 1.0
                        return
                    need = (1.0 - b.tokens) / (b.rpm / 60.0)
                wait = min(max(need, 0.05), 5.0)   # 分片等待，便于取消
                log.info("[%s] 限流等待 %.1fs（队列中 %d）", bucket, need, self._waiting[bucket])
                await asyncio.sleep(wait)
        finally:
            self._waiting[bucket] -= 1

    def estimate_wait(self, bucket: str) -> float:
        """给前端排队面板用。返回预计等待秒数（不消耗令牌）。"""
        b = self._buckets.get(bucket)
        if not b:
            return 0.0
        now = time.monotonic()
        tokens = min(b.capacity, b.tokens + (now - b.last) * (b.rpm / 60.0))
        ahead = max(self._waiting.get(bucket, 0), 0)
        per = 60.0 / b.rpm
        return max(0.0, (1.0 - tokens) * per) + ahead * per

    def snapshot(self) -> dict:
        """供 /api/system/limits 接口返回。"""
        return {k: {"rpm": round(b.rpm, 2),
                    "waiting": self._waiting.get(k, 0),
                    "eta_s": round(self.estimate_wait(k), 1)}
                for k, b in self._buckets.items()}

    def slot(self, bucket: str):
        limiter = self
        class _Ctx:
            async def __aenter__(self):
                await limiter.acquire(bucket)
            async def __aexit__(self, *a):
                return False
        return _Ctx()
