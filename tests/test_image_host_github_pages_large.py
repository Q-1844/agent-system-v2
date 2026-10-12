# tests/test_image_host_github_pages_large.py — Phase52 回归（P5a/P5b 10-12 实证故障）。
# GitHub contents API 对 >1MB 文件的 base64 content 返回 null（仅 <1MB 支持），
# 旧实现把它当空串 sha 比对 → 恒不匹配 → 同图二次托管被误判
# "PUT 422 真冲突" fail-loud。修复：Accept: application/vnd.github.raw 直接取
# 原始字节比对（同 api.github.com 域名；download_url 的 raw.githubusercontent.com
# 在本机被 SNI 阻断，不可用）。
import asyncio

import httpx
import pytest

from src.core.image_host import GithubPagesHost

OWNER, REPO, BRANCH, TOKEN = "u1", "host-repo", "main", "t"


class FakeClient:
    def __init__(self, host):
        self._host = host

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self._host.seen_headers.append(kw.get("headers") or {})
        return await self._host._step("GET", url)

    async def put(self, url, **kw):
        return await self._host._step("PUT", url)


def make_host(script):
    host = GithubPagesHost(OWNER, REPO, BRANCH, TOKEN,
                           poll_timeout=1.0, poll_interval=0.01)
    host._script = list(script)
    host.seen_headers = []
    host._calls = []

    async def step(method, url):
        host._calls.append((method, url))
        status, body = host._script.pop(0) if host._script else (404, {})
        if isinstance(body, bytes):
            return httpx.Response(status, content=body)
        return httpx.Response(status, json=body)

    host._step = step
    host._client_factory = lambda timeout=60: FakeClient(host)
    return host


def pages_ok():
    return (200, {"x": "y"})


def test_get_first_hit_large_file_via_raw_media_type(tmp_path):
    png = b"\x89PNG large-file" * 100000          # ~1.2MB，超 base64 content 上限
    f = tmp_path / "big.png"
    f.write_bytes(png)
    # GET-first 用 raw media type 直接返回原始字节且一致 → 幂等命中，不 PUT
    host = make_host([(200, png)])
    url = asyncio.run(host.upload(str(f)))
    assert url.endswith(".png")
    assert [m for m, _ in host._calls] == ["GET"]          # 仅 GET-first，无 PUT
    assert host.seen_headers[0]["Accept"] == "application/vnd.github.raw"


def test_put_422_large_file_raw_match_idempotent(tmp_path):
    png = b"\x89PNG large2" * 150000
    f = tmp_path / "big2.png"
    f.write_bytes(png)
    # 复现 P5a 故障序列：GET-first 未命中(404) → PUT 422 → raw GET 字节一致
    # → 幂等成功（修复前此处抛"真冲突"）
    host = make_host([(404, {}), (422, {}), (200, png), pages_ok()])
    url = asyncio.run(host.upload(str(f)))
    assert url.endswith(".png")


def test_put_422_large_file_raw_bytes_differ_fails_loud(tmp_path):
    png = b"\x89PNG mine" * 100
    other = b"\x89PNG different-bytes" * 100
    f = tmp_path / "m.png"
    f.write_bytes(png)
    # raw 比对真不一致 → 仍 fail-loud，不静默放行
    host = make_host([(404, {}), (422, {}), (200, other)])
    with pytest.raises(RuntimeError, match="真冲突"):
        asyncio.run(host.upload(str(f)))
