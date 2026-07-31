# src/core/image_host.py
# ImageHost 抽象层：把本地图片变成 Agnes 服务端能访问的公网 URL。
# 🔴 这是阶段 1 最值钱的改动：视频接口只吃公网 URL，本地处理过的图永久失去公网身份，
# 这层接口把该风险永久隔离。默认 passthrough（直通，仅复用 Agnes 原始 URL）。
from __future__ import annotations
import abc, base64, hashlib, json, logging, mimetypes
from pathlib import Path
from typing import Optional
import httpx

log = logging.getLogger("ImageHost")

class ImageHost(abc.ABC):
    """把本地图片变成 Agnes 服务端能访问的公网 URL。"""

    @abc.abstractmethod
    async def upload(self, local_path: str | Path) -> str: ...

    async def ensure_url(self, local_path: Optional[str],
                         known_url: Optional[str] = None) -> str:
        """优先复用已有 URL；已有 URL 不可达时重新上传。"""
        if known_url and await self._alive(known_url):
            return known_url
        if not local_path:
            raise ValueError("没有本地文件也没有可用 URL，无法生成公网地址")
        return await self.upload(local_path)

    async def _alive(self, url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
                r = await c.head(url)
                if r.status_code == 405:                # 有些存储不支持 HEAD
                    r = await c.get(url, headers={"Range": "bytes=0-0"})
                return r.status_code < 400
        except Exception:
            return False


class AgnesPassthroughHost(ImageHost):
    """默认实现：不上传，只复用 Agnes 生成图时返回的 URL。
    仅当图片**未经任何本地修改**时可用。修过的图会在这里抛错，这是**故意的**--
    让问题在这一层暴露，而不是在视频接口返回一个看不懂的 400。"""

    async def upload(self, local_path):
        raise RuntimeError(
            "当前使用 AgnesPassthroughHost（直通模式），无法为本地文件生成公网 URL。\n"
            "这张图可能经过了本地处理（抠图/裁剪/手动修改），已失去 Agnes 原始 URL。\n"
            "解决办法（三选一）：\n"
            "  1) 在设置里切换 image_host 为 r2 / imgbb / tunnel\n"
            "  2) 重新用 Agnes 生成这张图（不做本地处理）\n"
            "  3) 用 Agnes 图生图跑一次『保持构图不变』把它洗回一个公网 URL"
        )


class ImgbbHost(ImageHost):
    """imgbb 免费图床。上传原字节，100% 保真（不改比例/不重采样）。🔴 实测唯一可用方案（2026-07-31）。
    按文件 sha1 哈希缓存：同一张图重试多次只上传一次（缓存 cache/imgbb_cache.json）。
    🔴 上传失败抛错，不静默回落--调用方必须明示报错给用户（用户以为托管旧图实则换图，比报错更糟）。
    imgbb 图是拿到链接即可访问的公开图。"""
    def __init__(self, api_key: str, expiration: int = 0, cache_path=None):
        self.api_key, self.expiration = api_key, expiration
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache = self._load_cache()

    def _load_cache(self):
        if not self.cache_path or not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_cache(self):
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    async def upload(self, local_path):
        p = Path(local_path)
        if not p.exists():
            raise FileNotFoundError(f"本地图片不存在: {local_path}")
        data = p.read_bytes()
        digest = hashlib.sha1(data).hexdigest()
        if digest in self._cache:
            log.info("imgbb 命中缓存: %s (digest=%s)", p.name, digest[:12])
            return self._cache[digest]
        b64 = base64.b64encode(data).decode()
        params = {"key": self.api_key}
        if self.expiration:
            params["expiration"] = str(self.expiration)
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post("https://api.imgbb.com/1/upload",
                             params=params, data={"image": b64})
            if r.status_code != 200:
                raise RuntimeError(f"imgbb 上传失败 HTTP {r.status_code}: {r.text[:300]}")
            url = (r.json().get("data") or {}).get("url")
            if not url:
                raise RuntimeError(f"imgbb 上传响应无 url: {r.text[:300]}")
        self._cache[digest] = url
        self._save_cache()
        log.info("imgbb 上传成功: %s -> %s (digest=%s)", p.name, url, digest[:12])
        return url


class R2Host(ImageHost):
    """Cloudflare R2（S3 兼容）。免费额度大，适合长期项目。需要 boto3。"""
    def __init__(self, account_id, access_key, secret_key, bucket, public_base):
        import boto3
        self.bucket, self.public_base = bucket, public_base.rstrip("/")
        self.s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            region_name="auto")

    async def upload(self, local_path):
        import asyncio
        p = Path(local_path)
        digest = hashlib.sha1(p.read_bytes()).hexdigest()[:16]
        key = f"agnes/{digest}{p.suffix}"
        ctype = mimetypes.guess_type(p.name)[0] or "image/png"
        await asyncio.to_thread(
            self.s3.upload_file, str(p), self.bucket, key,
            ExtraArgs={"ContentType": ctype})
        return f"{self.public_base}/{key}"


class TunnelHost(ImageHost):
    """本地 cloudflared 隧道。零成本、零注册，但隧道断了 URL 就失效。
    🔴 未启用（降级调试用）：quick tunnel 每次重启换新域名，URL 持久化进 project.json 后
    隧道重启则全变死链，与现有"崩溃恢复续用 URL"设计冲突。仅供本机临时调试，不做默认。"""
    def __init__(self, public_base: str, serve_root: str | Path):
        self.public_base = public_base.rstrip("/")
        self.serve_root = Path(serve_root).resolve()

    async def upload(self, local_path):
        p = Path(local_path).resolve()
        rel = p.relative_to(self.serve_root).as_posix()
        return f"{self.public_base}/{rel}"


class AgnesRehostHost(ImageHost):
    """用 Agnes 图生图『保持原图不变』把本地图洗成公网 URL（零配置图床）。

    复用 AgnesClient.generate_image 走限流/退避/档位；size_tier+ratio 保持宽高比
    （非 reference 实现的 1024x1024——那会把 16:9 图洗成正方形，破坏构图）。

    🔴 保真：i2i 'keep as is' 服务端仍会重采样，非逐像素一致。用途是把 v1 迁移的
    本地首帧（local 有 / url 空）补出公网 URL 送进视频接口，让用户不丢当初满意的画面；
    不用于需要像素级保真的场景。保真度实测见 AGNES_API_FACTS §3.3.1。

    依赖注入：构造时接收一个带 generate_image(...) 的 client（AgnesClient），
    core 层不直接 import api 层。build_image_host 不构造它（无 client），
    由 api_server 的 get_rehost_host() 持有单例。
    """
    def __init__(self, client, size_tier: str = "1K", ratio: str = "16:9"):
        self.client = client
        self.size_tier = size_tier
        self.ratio = ratio

    async def upload(self, local_path) -> str:
        p = Path(local_path)
        if not p.exists():
            raise FileNotFoundError(f"本地图片不存在: {local_path}")
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        b64 = f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()
        res = await self.client.generate_image(
            prompt="Keep the image exactly as it is, do not modify anything, "
                   "same composition and aspect ratio",
            image_refs=[b64], size_tier=self.size_tier, ratio=self.ratio)
        url = res.get("url")
        if not url:
            raise RuntimeError("Agnes 重新托管未返回公网 URL（i2i keep-as-is 失败）")
        log.info("agnes_rehost 上传成功: %s -> %s", p.name, url[:80])
        return url


def build_image_host(cfg: dict, cache_path=None) -> ImageHost:
    """构造 ImageHost。🔴 默认 imgbb（2026-07-31 实测唯一可用方案，Agnes 服务端能下载 imgbb URL）。
    passthrough/r2/tunnel/agnes_rehost 均不作为默认：passthrough 不能上传本地图；
    r2 未配置；tunnel URL 不稳定；agnes_rehost 不保真（仅参考图，且需 client 注入不在此构造）。"""
    kind = (cfg or {}).get("kind", "imgbb")
    if kind == "imgbb":
        return ImgbbHost(cfg.get("api_key", ""), cfg.get("expiration", 0),
                         cache_path or cfg.get("cache_path"))
    if kind == "r2":      return R2Host(cfg["account_id"], cfg["access_key"],
                                        cfg["secret_key"], cfg["bucket"], cfg["public_base"])
    if kind == "tunnel":  return TunnelHost(cfg["public_base"], cfg["serve_root"])
    if kind == "passthrough": return AgnesPassthroughHost()
    # 默认 imgbb（唯一可用方案）
    return ImgbbHost(cfg.get("api_key", ""), cfg.get("expiration", 0),
                     cache_path or cfg.get("cache_path"))
