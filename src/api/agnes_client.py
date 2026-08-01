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
# 另：generate_image 兼容旧 size= 入参与 data 字段返回（垫片1/2，角色页面接口仍在用）；
# understand_text / generate_video 垫片已随阶段5③-b 删除（script_parser 改用 chat）。
from __future__ import annotations
import asyncio, logging, random
from typing import Any, Dict, List, Optional
import httpx

from ..utils.logger import setup_logger
from ..core.rate_limiter import RateLimiter
from ..core.quota import QuotaLedger

log = setup_logger("agnes_client")

# ── 垫片（向后兼容）一次性告警 ──
# 阶段 5 移除垫片前，提醒新代码不要走垫片。每个垫片名仅告警一次（进程级）。
_shim_warned: set = set()
def _shim_warn(name: str):
    if name not in _shim_warned:
        _shim_warned.add(name)
        msg = f"垫片 {name} 是向后兼容入口，将在阶段 5 移除；新代码请勿使用（见 DEPRECATED-SHIM 标记）。"
        log.warning(msg)
        import warnings as _w
        _w.warn(msg, DeprecationWarning, stacklevel=3)

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

# AGNES_API_FACTS §3.4：num_frames ≤ 441 且满足 8n+1。
# 441 是全局绝对上限；按分辨率还有更低的实际上限（视频生成算力随像素数增长）。
# 下方 max_frames_for 的分档值参考 lcy362/agnes-video-generator 实测（来自 Agnes 400 报错），
# ⚠️ 待本项目 Clash 起来后实测回填 AGNES_API_FACTS.md 确认。
ABS_MAX_FRAMES = 441
def max_frames_for(width: int, height: int) -> int:
    """该分辨率下允许的最大 num_frames。分辨率越高，允许帧数越少。
    实测（.cn，2026-07-29）：720p 档（含 1312x736、1280x720）接受 441 帧（=全局上限）。
    1080p / 480p 待实测，暂取参考值（lcy362）169 / 961。"""
    long_side = max(width or 0, height or 0)
    if long_side >= 1920:
        return 169    # 1080p 档（待实测）
    if long_side >= 854:
        return 441    # 720p 档（实测，= 全局上限）
    return 961        # 480p 档（待实测）

def snap_num_frames(target_frames: int,
                    width: int | None = None, height: int | None = None) -> int:
    """夹到最近的 8n+1。若给定 size，再夹到 max_frames_for(size) 与 441 的较小者。
    不给 size 时仅用全局上限 441（向后兼容 snap_num_frames(500)==441 这类断言）。"""
    n = round((target_frames - 1) / 8)
    v = 8 * int(n) + 1
    cap = ABS_MAX_FRAMES
    if width and height:
        cap = min(cap, max_frames_for(width, height))
    return max(81, min(cap, v))


class AgnesError(Exception):
    def __init__(self, msg, status=None, body=None):
        super().__init__(msg); self.status, self.body = status, body


class PollTransientError(AgnesError):
    """轮询的暂时性失败（网络/超时/5xx 耗尽）。调用方不应标 video.status=failed，
    应保留 video_id 让 retry/recover 续轮询，避免重提交丢掉已生成的视频。
    与「服务端明确返回 failed / video_id 404」的永久失败区分。"""


class AgnesClient:
    def __init__(self, config: dict, quota: QuotaLedger | None = None,
                 transport: Optional[httpx.BaseTransport] = None):
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
        # 测试注入用（阶段5③-c）：传 httpx.MockTransport 拦截所有请求；默认 None 走真实网络。
        # 生产路径（api_server.startup 构造 AgnesClient）不传 transport，初始化行为不变。
        # 传了 transport 时 httpx 忽略 proxy（MockTransport 不走网络）。
        self.transport = transport

    # ---------- 连接池 ----------
    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                timeout=httpx.Timeout(connect=30, read=360, write=60, pool=30),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                proxy=self.proxy, follow_redirects=True,
                transport=self.transport)
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
        # DEPRECATED-SHIM: remove in phase 5. legacy.size 与返回的 data 字段为旧调用方兼容；
        # 新代码请用 size_tier/ratio 并读 result["url"]。
        legacy_size = legacy.get("size")
        if legacy_size:
            _shim_warn("generate_image(size=...)")
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
            # DEPRECATED-SHIM: data 字段仅供旧调用方 result["data"][0]["url"]，新代码读 result["url"]
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
        num_frames = snap_num_frames(num_frames, width, height)
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

        # 提交：400 且报错含 num_frames 时自动降帧重试（分辨率越高允许帧数越少，
        # max_frames_for 的分档值待实测回填，故用 400 兜底发现真实上限）。
        frame_reductions_left = 3
        data = None
        while True:
            try:
                r = await self._request("POST", "/v1/videos", "video", json=body)
            except AgnesError as e:
                if (e.status == 400 and frame_reductions_left > 0
                        and "num_frame" in (e.body or "").lower()):
                    old_nf = body["num_frames"]
                    new_nf = snap_num_frames(int(old_nf * 0.7), width, height)
                    if new_nf >= old_nf:        # 已无法再降，直接抛
                        raise
                    body["num_frames"] = new_nf
                    seconds = new_nf / max(frame_rate, 1)
                    frame_reductions_left -= 1
                    log.warning("400 num_frames 超限（%d -> %d），降帧重试（剩 %d 次）",
                                old_nf, new_nf, frame_reductions_left)
                    continue
                raise
            data = r.json()
            break
        if self.quota: self.quota.consume("video_seconds", seconds)
        log.info("视频任务已提交 video_id=%s num_frames=%d seconds=%s size=%s",
                 data.get("video_id"), body["num_frames"], data.get("seconds"), data.get("size"))
        return data

    async def poll_video(self, video_id: str, *,
                         interval: float = 5.0, max_wait: float = 3600,
                         on_progress=None, max_consecutive_failures: int = 5) -> dict:
        """轮询直到 completed / failed。官方建议间隔 5s（AGNES_API_FACTS §3.5）。
        注意：轮询走 text 桶，不占视频桶——否则第二个任务永远提交不了。"""
        waited = 0.0
        consecutive = 0
        while waited < max_wait:
            await asyncio.sleep(interval); waited += interval
            try:
                r = await self._request("GET", "/agnesapi", "text",
                                        params={"video_id": video_id,
                                                "model_name": self.models["video"]},
                                        max_attempts=3)
            except AgnesError as e:
                if e.status is None:
                    # HTTP 层错误（网络/5xx/429 耗尽重试）-> 计数重试，不立刻放弃
                    consecutive += 1
                    log.warning("poll_video %s 网络错误（连续 %d/%d）: %s",
                                video_id[:16], consecutive, max_consecutive_failures, e)
                    if consecutive >= max_consecutive_failures:
                        raise PollTransientError(
                            f"轮询连续 {max_consecutive_failures} 次网络失败: {e}")
                    continue
                # 有 status（4xx 等，_request 已判定不可重试）-> 永久失败，立刻抛
                if e.status == 404:
                    raise AgnesError(f"video_id 不存在: {video_id}", 404)
                raise
            consecutive = 0   # 成功，重置
            d = r.json()
            st = d.get("status")
            if on_progress:
                await _maybe_await(on_progress(d.get("progress", 0), st, waited))
            if st == "completed":
                # 🔴 URL 取值 fallback：remixed_from_video_id（remix 任务）→ url → video_url。
                # 实测 .cn 节点直生成（t2v/i2v）任务 remixed_from_video_id 为 None，URL 在 url 字段。
                # （FACTS §3.6 原写"remixed_from_video_id 就是 mp4 URL"仅对 remix 任务成立，已勘误。）
                url = (d.get("remixed_from_video_id")
                       or d.get("url")
                       or d.get("video_url"))
                if not url: raise AgnesError(f"任务完成但没有视频 URL: {d}")
                return {"url": url,
                        "seconds": float(d.get("seconds") or 0),   # 🔴 回读
                        "size": d.get("size"), "raw": d}
            if st == "failed":
                raise AgnesError(f"视频生成失败: {d.get('error')}")
        raise PollTransientError(f"视频生成超时（{max_wait}s），video_id={video_id}")

    async def download(self, url: str, dest) -> str:
        from pathlib import Path
        dest = Path(dest); dest.parent.mkdir(parents=True, exist_ok=True)
        # 资产 URL（platform-outputs.agnes-ai.space）是预签名 URL，
        # 带 Authorization 头反而 401（实测），用无认证 client 下载。
        async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=30, read=600, write=60, pool=30),
                follow_redirects=True, proxy=self.proxy,
                transport=self.transport) as cli:
            async with cli.stream("GET", url) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in r.aiter_bytes(1 << 16):
                        f.write(chunk)
        return str(dest)

    # generate_image 仍保留 legacy size= 入参与 data 字段返回（垫片1/2，见上方 L166 内联
    # DEPRECATED-SHIM 标记：6 角色接口 + 3 灰色接口用 size= + result["data"][0]["url"]）。
    # 清理需改请求参数本身（size= -> size_tier/ratio、data[0].url -> result["url"]），
    # 正确性只能发真实 Agnes 请求验证（单测无法证明尺寸对），留待有 Agnes 连接时做。
    # 垫片3 understand_text 已删（script_parser 改用 chat）；
    # 垫片4 generate_video / 垫片5 edge_tts.synthesize 早已删。


async def _maybe_await(x):
    if asyncio.iscoroutine(x): return await x
    return x
