# src/core/image_host.py
# ImageHost 抽象层：把本地图片变成 Agnes 服务端能访问的公网 URL。
# 🔴 这是阶段 1 最值钱的改动：视频接口只吃公网 URL，本地处理过的图永久失去公网身份，
# 这层接口把该风险永久隔离。默认 passthrough（直通，仅复用 Agnes 原始 URL）。
from __future__ import annotations
import abc, base64, hashlib, logging, mimetypes
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
    """imgbb 免费图床。注册即得 key，无需信用卡。适合个人项目。"""
    def __init__(self, api_key: str, expiration: int = 0):
        self.api_key, self.expiration = api_key, expiration

    async def upload(self, local_path):
        p = Path(local_path)
        b64 = base64.b64encode(p.read_bytes()).decode()
        params = {"key": self.api_key}
        if self.expiration:
            params["expiration"] = str(self.expiration)
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post("https://api.imgbb.com/1/upload",
                             params=params, data={"image": b64})
            r.raise_for_status()
            url = r.json()["data"]["url"]
        log.info("imgbb 上传成功: %s -> %s", p.name, url)
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
    适合调试，不适合长跑。"""
    def __init__(self, public_base: str, serve_root: str | Path):
        self.public_base = public_base.rstrip("/")
        self.serve_root = Path(serve_root).resolve()

    async def upload(self, local_path):
        p = Path(local_path).resolve()
        rel = p.relative_to(self.serve_root).as_posix()
        return f"{self.public_base}/{rel}"


def build_image_host(cfg: dict) -> ImageHost:
    kind = (cfg or {}).get("kind", "passthrough")
    if kind == "imgbb":   return ImgbbHost(cfg["api_key"], cfg.get("expiration", 0))
    if kind == "r2":      return R2Host(cfg["account_id"], cfg["access_key"],
                                        cfg["secret_key"], cfg["bucket"], cfg["public_base"])
    if kind == "tunnel":  return TunnelHost(cfg["public_base"], cfg["serve_root"])
    return AgnesPassthroughHost()
