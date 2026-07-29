# src/api/agnes_client.py  （阶段 1 整文件重写）
# 重写要点（AGNES_API_FACTS 为唯一真相源）：
#   1. 共享 httpx 连接池
#   2. 全错误码指数退避：408/429/500/502/503/504/520/522/524
#   3. 图生图 image 双位置 fallback（extra_body -> 顶层），成功位置进程内缓存
#   4. response_format 只放 extra_body
#   5. num_frames 8n+1 夹取 + ≤441 校验
#   6. 视频轮询 5s，回读 seconds/size
#   7. 接入 RateLimiter + QuotaLedger
#   8. size 用档位 + ratio
# 另：保留旧方法 understand_text / generate_video 作为向后兼容垫片，
# generate_image 兼容旧 size= 入参与 data 字段返回，确保现有调用方与旧 HTTP 接口不中断。
from __future__ import annotations
import asyncio, logging, random
from typing import Any, Dict, List, Optional
import httpx

from ..utils.logger import setup_logger
from ..core.rate_limiter import RateLimiter
from ..core.quota import QuotaLedger

log = setup_logger("agnes_client")

RETRYABLE = {408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}

# AGNES_API_FACTS §2.3
SIZE_TIERS = {
    "1K": {"1:1": "1024x1024", "3:4": "864x1152", "4:3": "1152x864",
           "16:9": "1312x736", "9:16": "736x1312", "2:3": "832x1248",
           "3:2": "1248x832", "21:9": "1568x672"},
    "2K": {"1:1": "2048x2048", "16:9": "2624x1472", "9:16": "1472x2624"},
}

def resolve_size(tier: str, ratio: str) -> str:
    return SIZE_TIERS.get(tier, SIZE_TIERS["1K"]).get(ratio, "1312x736")

def snap_num_frames(target_frames: int) -> int:
    """夹到最近的 8n+1，并限制在 [81, 441]。AGNES_API_FACTS §3.4"""
    n = round((target_frames - 1) / 8)
    v = 8 * int(n) + 1
    return max(81, min(441, v))


class AgnesError(Exception):
    def __init__(self, msg, status=None, body=None):
        super().__init__(msg); self.status, self.body = status, body


class AgnesClient:
    def __init__(self, config: dict, quota: QuotaLedger | None = None):
        self.cfg      = config
        self.base_url = config["base_url"].rstrip("/")
        self.api_key  = config["api_key"]
        self.models   = config["models"]
        self.proxy    = config.get("proxy") or None
        self.limiter  = RateLimiter.get(config.get("rpm"))
        # 共享进程级配额单例：api_server 启动时会先用 config 初始化它，此处复用
        self.quota    = quota or QuotaLedger.get_default()
        self._client: Optional[httpx.AsyncClient] = None
        self._img_pos: Optional[str] = None      # "extra" | "top"，双位置探测结果缓存

    # ---------- 连接池 ----------
    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                timeout=httpx.Timeout(connect=30, read=360, write=60, pool=30),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                proxy=self.proxy, follow_redirects=True)
        return self._client

    async def aclose(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ---------- 带退避的请求 ----------
    async def _request(self, method: str, url: str, bucket: str,
                       max_attempts: int = 5, **kw) -> httpx.Response:
        last = None
        for attempt in range(max_attempts):
            async with self.limiter.slot(bucket):
                try:
                    c = await self.client()
                    r = await c.request(method, url, **kw)
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last = e
                    log.warning("[%s] 网络异常 %s（第 %d/%d 次）", bucket, e,
                                attempt + 1, max_attempts)
                else:
                    if r.status_code < 400:
                        return r
                    if r.status_code not in RETRYABLE:
                        raise AgnesError(
                            f"Agnes API 错误 {r.status_code}: {r.text[:400]}",
                            r.status_code, r.text)
                    last = AgnesError(f"{r.status_code}: {r.text[:200]}", r.status_code, r.text)
                    log.warning("[%s] 可重试错误 %d（第 %d/%d 次）", bucket,
                                r.status_code, attempt + 1, max_attempts)
            # 退避在限流槽之外做，避免占着令牌睡觉
            backoff = min(60, (2 ** attempt) * 3) + random.uniform(0, 2)
            await asyncio.sleep(backoff)
        raise AgnesError(f"重试 {max_attempts} 次仍失败: {last}")

    # ---------- 文本 ----------
    async def chat(self, messages: List[dict], *, temperature=0.7,
                   max_tokens=2048, thinking=False, json_mode=False) -> str:
        if self.quota:
            ok, why = self.quota.can("text_calls", 1)
            if not ok: raise AgnesError(why)
        body: Dict[str, Any] = {"model": self.models["text"], "messages": messages,
                                "temperature": temperature, "max_tokens": max_tokens}
        if thinking:
            body["chat_template_kwargs"] = {"enable_thinking": True}
        r = await self._request("POST", "/v1/chat/completions", "text", json=body)
        if self.quota: self.quota.consume("text_calls", 1)
        text = r.json()["choices"][0]["message"]["content"]
        if json_mode:
            text = text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"): text = text[4:]
            return text.strip()
        return text

    # ---------- 图片 ----------
    async def generate_image(self, prompt: str, *,
                             image_refs: Optional[List[str]] = None,
                             size_tier: str = "1K", ratio: str = "16:9",
                             seed: Optional[int] = None,
                             want_b64: bool = False,
                             **legacy) -> dict:
        """返回 {"url": str|None, "b64": str|None, "data": [...]}。
        image_refs 支持公网 URL 或 data:image/png;base64,...
        legacy.size 兼容旧调用方（直接透传，服务端会归一化）；
        返回额外带 data 字段，兼容旧代码 result["data"][0]["url"]。"""
        if self.quota:
            ok, why = self.quota.can("images", 1)
            if not ok: raise AgnesError(why)

        bucket = f"image_{size_tier.lower()}"
        # 旧调用方传 size=... 时透传（FACTS §2.3：服务端会归一化）；否则用档位
        legacy_size = legacy.get("size")
        size = legacy_size if legacy_size else resolve_size(size_tier, ratio)
        is_i2i = bool(image_refs)

        def build(pos: str) -> dict:
            b: Dict[str, Any] = {"model": self.models["image"],
                                 "prompt": prompt, "size": size}
            eb: Dict[str, Any] = {}
            # 🔴 response_format 只能在 extra_body（AGNES_API_FACTS §2.2 坑1）
            if is_i2i:
                eb["response_format"] = "b64_json" if want_b64 else "url"
                if pos == "extra": eb["image"] = image_refs
                else:              b["image"]  = image_refs
            else:
                if want_b64: b["return_base64"] = True
                else:        eb["response_format"] = "url"
            if seed is not None: b["seed"] = seed
            if eb: b["extra_body"] = eb
            return b

        positions = ["extra", "top"] if is_i2i else ["extra"]
        if self._img_pos in positions:
            positions = [self._img_pos] + [p for p in positions if p != self._img_pos]

        last_err = None
        for pos in positions:
            try:
                r = await self._request("POST", "/v1/images/generations",
                                        bucket, json=build(pos))
            except AgnesError as e:
                # 只有 400 才试另一个位置——其它错误换位置也没用
                if e.status == 400 and pos != positions[-1]:
                    log.warning("图生图 image 放在 %s 返回 400，尝试另一位置", pos)
                    last_err = e; continue
                raise
            if is_i2i:
                self._img_pos = pos
                log.info("图生图 image 位置确定为: %s", pos)
            if self.quota: self.quota.consume("images", 1)
            d = r.json()["data"][0]
            return {"url": d.get("url"), "b64": d.get("b64_json"), "data": [d]}
        raise last_err or AgnesError("图片生成失败")

    # ---------- 视频 ----------
    async def create_video(self, prompt: str, *,
                           mode: str = "i2v",
                           image_url: Optional[str] = None,
                           image_urls: Optional[List[str]] = None,
                           num_frames: int = 121, frame_rate: int = 24,
                           width: int = 1312, height: int = 736,
                           seed: Optional[int] = None,
                           negative_prompt: str = "") -> dict:
        """提交视频任务。mode: 't2v' | 'i2v' | 'keyframes' | 'multi'
        返回创建响应（含 video_id / task_id / seconds / size）。"""
        num_frames = snap_num_frames(num_frames)
        seconds = num_frames / max(frame_rate, 1)
        if self.quota:
            ok, why = self.quota.can("video_seconds", seconds)
            if not ok: raise AgnesError(why)

        body: Dict[str, Any] = {"model": self.models["video"], "prompt": prompt,
                                "num_frames": num_frames, "frame_rate": frame_rate,
                                "width": width, "height": height}
        if seed is not None: body["seed"] = seed
        if negative_prompt:  body["negative_prompt"] = negative_prompt

        if mode == "i2v":
            if not image_url: raise AgnesError("i2v 模式必须提供 image_url（公网 URL）")
            body["image"] = image_url                      # 顶层字符串
        elif mode == "keyframes":
            if not image_urls or len(image_urls) < 2:
                raise AgnesError("keyframes 模式需要至少 2 张图（首帧+尾帧）")
            body["extra_body"] = {"image": image_urls, "mode": "keyframes"}
        elif mode == "multi":
            body["extra_body"] = {"image": image_urls or []}
        # t2v 不传图

        r = await self._request("POST", "/v1/videos", "video", json=body)
        data = r.json()
        if self.quota: self.quota.consume("video_seconds", seconds)
        log.info("视频任务已提交 video_id=%s seconds=%s size=%s",
                 data.get("video_id"), data.get("seconds"), data.get("size"))
        return data

    async def poll_video(self, video_id: str, *,
                         interval: float = 5.0, max_wait: float = 3600,
                         on_progress=None) -> dict:
        """轮询直到 completed / failed。官方建议间隔 5s（AGNES_API_FACTS §3.5）。
        注意：轮询走 text 桶，不占视频桶——否则第二个任务永远提交不了。"""
        waited = 0.0
        while waited < max_wait:
            await asyncio.sleep(interval); waited += interval
            r = await self._request("GET", "/agnesapi", "text",
                                    params={"video_id": video_id,
                                            "model_name": self.models["video"]},
                                    max_attempts=3)
            d = r.json()
            st = d.get("status")
            if on_progress:
                await _maybe_await(on_progress(d.get("progress", 0), st, waited))
            if st == "completed":
                url = d.get("remixed_from_video_id")     # 🔴 这就是 mp4 URL
                if not url: raise AgnesError(f"任务完成但没有视频 URL: {d}")
                return {"url": url,
                        "seconds": float(d.get("seconds") or 0),   # 🔴 回读
                        "size": d.get("size"), "raw": d}
            if st == "failed":
                raise AgnesError(f"视频生成失败: {d.get('error')}")
        raise AgnesError(f"视频生成超时（{max_wait}s），video_id={video_id}")

    async def download(self, url: str, dest) -> str:
        from pathlib import Path
        dest = Path(dest); dest.parent.mkdir(parents=True, exist_ok=True)
        c = await self.client()
        async with c.stream("GET", url) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in r.aiter_bytes(1 << 16):
                    f.write(chunk)
        return str(dest)

    # ===================== 向后兼容垫片（旧调用方仍可用） =====================
    async def understand_text(self, prompt: str, **kwargs) -> str:
        """【向后兼容】旧名，等价于 chat。evaluator / script_parser 仍在用。"""
        return await self.chat(
            [{"role": "user", "content": prompt}],
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 4096),
        )

    async def generate_video(self, prompt: str, image_refs: List[str],
                             mode: str = "keyframes", **kwargs) -> dict:
        """【向后兼容】旧名：提交 + 轮询一步到位，返回旧形状 dict。
        animation_gen 仍在用：result["status" / "remixed_from_video_id" / "seconds"]。
        旧 mode: "keyframes" / "ti2vid"。image_refs 为 URL 列表（旧代码传本地路径，
        阶段 3 会用 ImageHost 修正）。"""
        num_frames = kwargs.get("num_frames", 121)
        frame_rate = kwargs.get("frame_rate", 24)
        new_mode = "keyframes" if mode == "keyframes" else "i2v"
        created = await self.create_video(
            prompt, mode=new_mode,
            image_urls=image_refs if new_mode == "keyframes" else None,
            image_url=(image_refs[0] if image_refs else None) if new_mode == "i2v" else None,
            num_frames=num_frames, frame_rate=frame_rate,
        )
        video_id = created.get("video_id") or created.get("task_id") or created.get("id")
        if not video_id:
            raise AgnesError(f"视频任务创建无 video_id: {created}")
        polled = await self.poll_video(video_id)
        # 返回旧形状：status / remixed_from_video_id / seconds / size
        return {
            "status": "completed",
            "remixed_from_video_id": polled["url"],
            "seconds": polled["seconds"],
            "size": polled["size"],
            "video_id": video_id,
        }


async def _maybe_await(x):
    if asyncio.iscoroutine(x): return await x
    return x
