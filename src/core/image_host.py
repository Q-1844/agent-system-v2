# src/core/image_host.py
# ImageHost 抽象层：把本地图片变成 Agnes 服务端能访问的公网 URL。
# 🔴 这是阶段 1 最值钱的改动：视频接口只吃公网 URL，本地处理过的图永久失去公网身份，
# 这层接口把该风险永久隔离。默认 passthrough（直通，仅复用 Agnes 原始 URL）。
from __future__ import annotations
import abc, asyncio, base64, hashlib, json, logging, mimetypes, ssl
from pathlib import Path
from typing import Optional
import httpx

log = logging.getLogger("ImageHost")


def _system_verify():
    """系统信任库 SSLContext（Windows 存储含本机加速代理的自签根，如 SteamTools/Watt
    Toolkit——certifi 不含它们，github 域名会被本地 MITM 后验证失败 2026-09-24 实测）。
    失败回退 certifi 默认（verify=True 等价）。"""
    try:
        ctx = ssl.create_default_context()
        ctx.load_default_certs()
        return ctx
    except Exception:
        return True

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
    """构造 ImageHost。🔴 2026-09-24 起默认 github_pages（i.ibb.co SNI 级线路阻断，35.md）。
    passthrough/r2/tunnel/agnes_rehost 均不作为默认：passthrough 不能上传本地图；
    r2 未配置；tunnel URL 不稳定；agnes_rehost 不保真（仅参考图，且需 client 注入不在此构造）。
    imgbb 保留：线路恢复后 config 一行切回（回退开关）。"""
    kind = (cfg or {}).get("kind", "github_pages")
    if kind == "github_pages":
        gp = cfg.get("github_pages") or {}
        return GithubPagesHost(gp.get("owner", ""), gp.get("repo", ""),
                               gp.get("branch", "main"), gp.get("token", ""))
    if kind == "imgbb":
        return ImgbbHost(cfg.get("api_key", ""), cfg.get("expiration", 0),
                         cache_path or cfg.get("cache_path"))
    if kind == "r2":      return R2Host(cfg["account_id"], cfg["access_key"],
                                        cfg["secret_key"], cfg["bucket"], cfg["public_base"])
    if kind == "tunnel":  return TunnelHost(cfg["public_base"], cfg["serve_root"])
    if kind == "passthrough": return AgnesPassthroughHost()
    # 默认 github_pages
    gp = cfg.get("github_pages") or {}
    return GithubPagesHost(gp.get("owner", ""), gp.get("repo", ""),
                           gp.get("branch", "main"), gp.get("token", ""))


class GithubPagesHost(ImageHost):
    """GitHub Pages 图床（Contents API PUT → Pages 构建 → <owner>.github.io 直链）。
    🔴 纯托管层：MIME 无关，原字节进出（JPEG 转换是末帧业务的事，不在这里）。
    命名 objects/{sha256_hex16}.{ext}（64-bit 内容哈希，唯一命名防 CDN 缓存旧内容）。
    GET-first：内容寻址命中即直接返回（不 PUT、不烧 Pages build）；
    PUT 409/422=冲突或已存在：GET 目标解码字节 sha256 对比，匹配=幂等成功；否则退避重试 ×2 后 fail-loud。
    ⚠️ GET 返回的 sha 字段是 git blob sha（sha1("blob <len>\\0"+内容)），不能与 sha256 直接比。
    同 repo 写入串行锁（实例级 asyncio.Lock；deps 单例保证四调用点共享）。
    上传成功后轮询 Pages URL 至 200（构建实测 ~40s，上限 180s）；超时抛 PagesNotReadyError
    （PUT 已成功、URL 已建，可重试轮询）——不静默回落。
    实测依据（2026-09-23）：raw.githubusercontent.com 仅 2/10 可达弃用；github.io 路由健康；
    Pages 构建 soft limit 10 builds/hour（1 PUT=1 build）= 真实吞吐瓶颈，密集上传须批量。"""

    class PagesNotReadyError(RuntimeError):
        """PUT 已成功但 Pages 尚未构建到 200——URL 已建，可稍后重试轮询。"""

    _MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
                 "image/gif": ".gif"}

    def __init__(self, owner: str, repo: str, branch: str, token: str,
                 poll_timeout: float = 180.0, poll_interval: float = 5.0,
                 client_factory=None):
        self.owner, self.repo, self.branch, self.token = owner, repo, branch, token
        self.poll_timeout = poll_timeout
        self.poll_interval = poll_interval
        self._client_factory = client_factory or (
            lambda timeout=60: httpx.AsyncClient(timeout=timeout, verify=_system_verify()))
        self._lock = asyncio.Lock()

    # ---- helpers ----
    def _api(self, path: str) -> str:
        return f"https://api.github.com/repos/{self.owner}/{self.repo}/contents/{path}"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json"}

    def _object_path(self, data: bytes, name: str) -> str:
        digest = hashlib.sha256(data).hexdigest()[:16]
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        ext = self._MIME_EXT.get(mime, Path(name).suffix or ".bin")
        return f"objects/{digest}{ext}"

    def _pages_url(self, path: str) -> str:
        return f"https://{self.owner}.github.io/{self.repo}/{path}"

    def _client(self):
        return self._client_factory(timeout=60)

    # ---- core ----
    async def upload(self, local_path):
        p = Path(local_path)
        if not p.exists():
            raise FileNotFoundError(f"本地图片不存在: {p}")
        data = p.read_bytes()
        path = self._object_path(data, p.name)
        url = self._pages_url(path)
        async with self._lock:                     # 同 repo 写入串行（四调用点共享单例）
            # GET-first：内容寻址命中=已在线，直接返回（省一次 Pages build）
            if await self._remote_matches(path, data):
                log.info("github_pages 幂等命中: %s", path)
                return url
            await self._put_with_retry(path, data)
        await self._wait_ready(url)
        return url

    async def _remote_matches(self, path: str, data: bytes) -> bool:
        try:
            async with self._client() as c:
                # Phase52 修复（10-12 P5a/P5b 实证）：contents API 对 >1MB 文件
                # content=null（base64 仅支持 <1MB）→ 旧实现误判"真冲突"，同图
                # 二次托管必炸。改用 raw media type（Accept: vnd.github.raw）：
                # 同域名（api.github.com，本机可达；download_url 的
                # raw.githubusercontent.com 被 SNI 阻断不可用）、直接返回原始
                # 字节、官方支持到 100MB，<1MB 与 >1MB 行为统一。
                r = await c.get(self._api(path),
                                headers={**self._headers(),
                                         "Accept": "application/vnd.github.raw"},
                                params={"ref": self.branch})
            if r.status_code != 200:
                return False
            return hashlib.sha256(r.content).hexdigest() == \
                hashlib.sha256(data).hexdigest()
        except httpx.HTTPError:
            return False   # 网络错 → 视为未命中，走 PUT 路径（PUT 内还有重试）

    async def _put_with_retry(self, path: str, data: bytes):
        body = {"message": f"host {path}",
                "content": base64.b64encode(data).decode(), "branch": self.branch}
        delays = (2.0, 4.0)                            # 重试退避；PUT×3 + 409 幂等核对
        for attempt in range(3):
            try:
                async with self._client() as c:
                    r = await c.put(self._api(path), json=body, headers=self._headers())
                if r.status_code in (200, 201):
                    return
                if r.status_code in (409, 422):        # Conflict / sha required
                    if await self._remote_matches(path, data):
                        return                          # 已存在且字节一致=幂等成功
                    raise RuntimeError(
                        f"github_pages PUT {r.status_code} 真冲突（远端存在但内容不匹配）: {path}")
                if r.status_code >= 500 and attempt < 2:
                    await asyncio.sleep(delays[attempt]); continue
                raise RuntimeError(
                    f"github_pages PUT HTTP {r.status_code}: {r.text[:200]}")
            except httpx.HTTPError as e:
                if attempt < 2:
                    await asyncio.sleep(delays[attempt]); continue
                raise RuntimeError(f"github_pages PUT 网络错误: {e}") from e

    async def _wait_ready(self, url: str):
        import time as _time
        deadline = _time.monotonic() + self.poll_timeout
        async with self._client() as c:
            while _time.monotonic() < deadline:
                try:
                    r = await c.get(url)
                    if r.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass                                # 网络抖动继续轮询
                await asyncio.sleep(self.poll_interval)
        raise self.PagesNotReadyError(
            f"github_pages 上传成功但 Pages 未在 {self.poll_timeout}s 内就绪: {url}"
            f"（URL 已建，可稍后重试轮询）")
