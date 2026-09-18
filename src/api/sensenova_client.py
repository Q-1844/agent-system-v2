# src/api/sensenova_client.py
# SenseNova 图片客户端（2026-09-17 全链路验收轮接入）：
#   - OpenAI 兼容 POST /v1/images/generations（token.sensenova.cn）
#   - 模型 sensenova-u1.5-lite；b64_json 返回；watermark=false（公测免费去水印）
#   - duck-type 对齐 AgnesClient.generate_image 签名：可在所有已注入 agnes_client
#     参数的生成函数（visual_service / write_service）里作为 provider 覆盖传入，
#     prompt builder / basis / records / review / approve 全链路复用，零架构改动。
# 实测能力边界（_probe_sensenova_caps.py，2026-09-17）：
#   - 非方形 size（1312x736 等 Agnes 1K 档像素串）→ 接受
#   - seed 整数 → 接受（不保证跨调用可复现）
#   - i2i：顶层 image 数组（data URI 与公网 URL 均探测接受；公网 URL 由本客户端
#     预下载转 data URI，避免服务端拉取失败）
from __future__ import annotations
import asyncio, base64, logging, time
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx

from .agnes_client import resolve_size

log = logging.getLogger("sensenova_client")


class SensenovaImageError(Exception):
    def __init__(self, msg, status=None, body=None):
        super().__init__(msg)
        self.status, self.body = status, body


class SensenovaImageClient:
    """SenseNova 图片 provider。签名对齐 AgnesClient.generate_image。"""

    def __init__(self, api_key: str, *,
                 model: str = "sensenova-u1.5-lite",
                 base_url: str = "https://token.sensenova.cn",
                 timeout: float = 300.0):
        self.api_key = api_key
        # 调用方（visual_service/write_service）读 models["image"] 记录进 basis
        self.models: Dict[str, str] = {"image": model}
        self.base_url = base_url.rstrip("/")
        self.proxy = None          # 国内直连
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._last_call_ts = 0.0   # 自限速：相邻图片请求最小间隔

    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                timeout=httpx.Timeout(connect=30, read=self._timeout,
                                      write=120, pool=30),
                follow_redirects=True)
        return self._client

    async def aclose(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ---------- refs 归一化：优先转 data URI；下载失败重试后降级直传原 URL ----------
    async def _normalize_refs(self, image_refs: Optional[List[str]]) -> List[str]:
        out: List[str] = []
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30, read=120,
                                                          write=30, pool=30),
                                     follow_redirects=True) as dl:
            for ref in image_refs or []:
                if ref.startswith("data:"):
                    out.append(ref)
                    continue
                data = None
                for attempt in range(3):   # 瞬态网络错误重试（BOUND 尾帧 refs 多，实测会闪断）
                    try:
                        r = await dl.get(ref)
                        if r.status_code < 400:
                            data = r.content
                        break
                    except httpx.HTTPError as e:
                        log.warning("refs 下载失败（第 %d/3 次）%s: %s",
                                    attempt + 1, ref[:80], e)
                        if attempt < 2:
                            await asyncio.sleep(2 * (attempt + 1))
                if data:
                    out.append("data:image/png;base64,"
                               + base64.b64encode(data).decode("ascii"))
                else:
                    # 降级：直传原 URL（服务端可自行拉取公网图）
                    log.warning("refs 下载重试耗尽，降级直传 URL: %s", ref[:80])
                    out.append(ref)
        return out

    # ---------- 生成 ----------
    async def generate_image(self, prompt: str, *,
                             image_refs: Optional[List[str]] = None,
                             size_tier: str = "1K", ratio: str = "16:9",
                             seed: Optional[int] = None,
                             want_b64: bool = False) -> dict:
        """返回 {"url": None, "b64": str}。sensenova 恒回 b64（无公网 URL）。"""
        # 自限速（官方 RPM 未知，实测连续调用约 20-25s/张，保守 3s 间隔）
        wait = 3.0 - (time.monotonic() - self._last_call_ts)
        if wait > 0:
            await asyncio.sleep(wait)

        body: Dict[str, Any] = {
            "model": self.models["image"],
            "prompt": prompt,
            "n": 1,
            "size": resolve_size(size_tier, ratio),
            "output_format": "png",
            "response_format": "b64_json" if want_b64 or True else "url",
            "watermark": False,
        }
        if image_refs:
            body["image"] = await self._normalize_refs(image_refs)
        if seed is not None:
            body["seed"] = int(seed)

        c = await self.client()
        self._last_call_ts = time.monotonic()
        r = await c.post("/v1/images/generations", json=body)
        if r.status_code >= 400:
            raise SensenovaImageError(
                f"SenseNova API 错误 {r.status_code}: {r.text[:400]}",
                r.status_code, r.text)
        data = (r.json().get("data") or [{}])[0]
        b64 = data.get("b64_json")
        url = data.get("url")
        if not b64 and not url:
            raise SensenovaImageError("SenseNova 未返回图片数据（b64/url 均空）")
        log.info("sensenova image ok size=%s i2i=%s b64=%s",
                 body["size"], bool(image_refs), bool(b64))
        return {"url": url, "b64": b64}

    # ---------- duck-type 兼容（handler/服务可能调用） ----------
    async def download(self, url: str, dest) -> str:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30, read=600,
                                                          write=60, pool=30),
                                     follow_redirects=True) as cli:
            async with cli.stream("GET", url) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in r.aiter_bytes(1 << 16):
                        f.write(chunk)
        return str(dest)
