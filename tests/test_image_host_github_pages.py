# tests/test_image_host_github_pages.py — 35.md §5：GithubPagesHost 单测（全 mock，零网络）。
# 覆盖：201 成功/GET-first 幂等/409 字节核对/真冲突 fail-loud/轮询超时语义/字节恒等（无静默
# JPEG 化）/串行锁（仅计量 API 写路径）/build_image_host 分派。
import asyncio
import base64
import hashlib

import httpx
import pytest

from src.core.image_host import GithubPagesHost, build_image_host

OWNER, REPO, BRANCH, TOKEN = "u1", "host-repo", "main", "t"


class FakeClient:
    """脚本化假 httpx.AsyncClient：脚本队列挂在 host 上（跨请求实例共享）；
    并发计量只针对 api.github.com 请求（串行锁保护的是写路径，Pages 轮询不占锁）。"""

    def __init__(self, host):
        self._host = host

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        return await self._host._step("GET", url)

    async def put(self, url, **kw):
        return await self._host._step("PUT", url)


def make_host(script, **kw):
    host = GithubPagesHost(OWNER, REPO, BRANCH, TOKEN,
                           poll_timeout=kw.pop("poll_timeout", 1.0),
                           poll_interval=kw.pop("poll_interval", 0.01), **kw)
    host._script = list(script)          # 共享队列：[(status, json_dict), ...]
    host._calls = []                     # [("GET"/"PUT", url), ...]
    host._api_inflight = 0
    host._api_max_inflight = 0

    async def step(method, url):
        is_api = "api.github.com" in url
        host._calls.append((method, url))
        if is_api:
            host._api_inflight += 1
            host._api_max_inflight = max(host._api_max_inflight, host._api_inflight)
        try:
            status, body = host._script.pop(0) if host._script else (404, {})
            if method == "PUT":
                await asyncio.sleep(0.02)   # 放大窗口，让并发违规可测
            # Phase52：bytes body = raw 响应（download_url 大文件比对用）
            if isinstance(body, bytes):
                return httpx.Response(status, content=body)
            return httpx.Response(status, json=body)
        finally:
            if is_api:
                host._api_inflight -= 1

    host._step = step
    host._client_factory = lambda timeout=60: FakeClient(host)
    return host


def b64(data: bytes) -> dict:
    return {"content": base64.b64encode(data).decode()}


def pages_ok():
    return (200, {"x": "y"})             # Pages URL 轮询只看状态码


def test_put_201_success_returns_pages_url_with_sha256_16(tmp_path):
    png = b"\x89PNG fake-bytes" * 100
    f = tmp_path / "a.png"; f.write_bytes(png)
    host = make_host([(404, {}), (201, {}), pages_ok()])   # GET-first 未命中→PUT→就绪
    url = asyncio.run(host.upload(str(f)))
    digest = hashlib.sha256(png).hexdigest()[:16]
    assert url == f"https://{OWNER}.github.io/{REPO}/objects/{digest}.png"
    assert [m for m, _ in host._calls] == ["GET", "PUT", "GET"]


def test_get_first_idempotent_hit_skips_put(tmp_path):
    png = b"\x89PNG same" * 10
    f = tmp_path / "a.png"; f.write_bytes(png)
    host = make_host([(200, png)])                    # GET 命中(raw media type 字节一致) → 不 PUT 不烧 build
    url = asyncio.run(host.upload(str(f)))
    assert url.endswith(".png")
    assert [m for m, _ in host._calls] == ["GET"]          # 只有 GET-first，无 PUT


def test_put_409_remote_matches_is_idempotent(tmp_path):
    png = b"\x89PNG c" * 7
    f = tmp_path / "a.png"; f.write_bytes(png)
    # PUT 撞 409（并发窗口）→ GET 核对字节一致 → 幂等成功 → Pages 就绪
    host = make_host([(404, {}), (409, {}), (200, png), pages_ok()])
    url = asyncio.run(host.upload(str(f)))
    assert url.endswith(".png")


def test_put_409_remote_missing_fails_loud(tmp_path):
    png = b"\x89PNG d" * 7
    f = tmp_path / "a.png"; f.write_bytes(png)
    # 409 → GET 无文件 → 真冲突 fail-loud（冲突不重试 PUT，避免覆盖他人内容）
    host = make_host([(404, {}), (409, {}), (404, {})])
    with pytest.raises(RuntimeError, match="真冲突"):
        asyncio.run(host.upload(str(f)))


def test_poll_timeout_raises_pages_not_ready(tmp_path):
    png = b"\x89PNG e" * 7
    f = tmp_path / "a.png"; f.write_bytes(png)
    # PUT 成功但 Pages 永不 200 → PagesNotReadyError（PUT 已成功的独立语义）
    host = make_host([(404, {}), (201, {}), (404, {}), (404, {}), (404, {}), (404, {})],
                     poll_timeout=0.03, poll_interval=0.01)
    with pytest.raises(GithubPagesHost.PagesNotReadyError):
        asyncio.run(host.upload(str(f)))


def test_host_is_mime_agnostic_bytes_identical(tmp_path):
    # 🔴 纯托管层：原字节进出，扩展名按 MIME 推断，Host 不做任何格式转换
    for name, data, suffix in [("a.png", b"png-bytes-here", ".png"),
                               ("b.jpg", b"jpeg-bytes-here", ".jpg"),
                               ("c.webp", b"webp-bytes-here", ".webp")]:
        f = tmp_path / name; f.write_bytes(data)
        host = make_host([(404, {}), (201, {}), pages_ok()])
        url = asyncio.run(host.upload(str(f)))
        assert url == (f"https://{OWNER}.github.io/{REPO}/objects/"
                       + hashlib.sha256(data).hexdigest()[:16] + suffix)


def test_serial_lock_no_concurrent_api_writes(tmp_path):
    # 同实例并发上传 → api 写路径（GET-first/PUT）必须串行（四调用点共享单例的前提）
    files = []
    for i in range(1, 5):
        f = tmp_path / f"f{i}.png"; f.write_bytes(b"x" * i); files.append(str(f))
    script = []
    for _ in files:
        script += [(404, {}), (201, {}), pages_ok()]
    host = make_host(script)

    async def run_all():
        await asyncio.gather(*[host.upload(p) for p in files])

    asyncio.run(run_all())
    assert host._api_max_inflight == 1                     # 任意时刻仅一个 api 请求


def test_build_image_host_dispatch():
    h = build_image_host({"kind": "github_pages",
                          "github_pages": {"owner": "o", "repo": "r",
                                           "branch": "main", "token": "t"}})
    assert isinstance(h, GithubPagesHost)
    h2 = build_image_host({"kind": "imgbb", "api_key": "k"})
    assert type(h2).__name__ == "ImgbbHost"                # 回退开关仍在
    h3 = build_image_host({})                              # 缺省=github_pages
    assert isinstance(h3, GithubPagesHost)
