# tools/relstore/github_release_adapter.py
# G3-A：真实 GitHub REST 适配器（Release 资产 + Contents 索引）。
#
# 安全纪律（G3-A：零远端写入）：
# - 读方法（GET）通过可注入 transport 工作，默认可用于只读查询；
# - 写方法（POST/PUT/PATCH/DELETE）三重门：writes_enabled=True + 非空 token + 显式 armed()，
#   缺一即抛 RemoteWriteDisabled；没有任何配置默认值能绕过；
# - token 只由 G3-B 执行时显式注入，本模块【不读 .env/os.environ/配置文件】；
# - 异常与日志一律遮蔽 Authorization、302 临时 SAS、签名参数（sig/jwt/X-Amz 等）。
from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .errors import (AssetConflict, AuthError, NetworkInterrupt, RateLimited,
                     ReceiptLost, RemoteWriteDisabled, ServerError)
from .release_adapter import AssetMeta, ReleaseAdapter, ReleaseHandle

API_BASE = "https://api.github.com"
UPLOAD_BASE = "https://uploads.github.com"
API_VERSION = "2022-11-28"
MAX_ASSET_BYTES = 2 * 1024 ** 3 - 1
ASSETS_PER_RELEASE_LIMIT = 1000
# 🔴 ink-diffusion-20261107 实锤（用户拍板 fail-loud）：~2GB release 资产 POST 201
# 返回成功回执后，GitHub 端 finalize 可【永久】卡在 state=starter（19 小时仍 starter），
# 该资产永远 404 不可下载。教训：上传回执不可作为成功依据——必须 GET 复核
# state=uploaded 才算完成；starter 资产绝不参与幂等复用（复用=再次信任假回执）。
# 配套结构测试：tests/relstore/test_relstore_07_http_adapter.py（starter 锁三件套）。
ASSET_FINALIZE_POLL_S = 10.0
ASSET_FINALIZE_TIMEOUT_S = 300.0

# ---------------- 敏感信息遮蔽 ----------------

_SAS_PARAMS = re.compile(
    r"(?i)(sig|jwt|X-Amz-Signature|X-Amz-Security-Token|Signature|token|sign|sas|skt|ske)="
    r"[^&\s\"'<>)]*")
_SAS_QUERY_KEYS = re.compile(r"(?i)(sp|sv|sr|spr|se|rscd|rsct|skoid|sktid|skt|ske|sks|skv)="
                             r"[^&\s\"'<>)]*")
_AUTH_HEADER = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+|authorization\s*[:=]\s*)"
                          r"[A-Za-z0-9_\-\.]+")
_TOKEN_LITERAL: Optional[re.Pattern] = None


def redact(text: str, *, token: str | None = None) -> str:
    """遮蔽 Authorization 头、SAS/签名查询参数与 token 字面量。"""
    if not isinstance(text, str):
        text = str(text)
    text = _AUTH_HEADER.sub(r"\1<REDACTED>", text)
    text = _SAS_PARAMS.sub(lambda m: m.group(0).split("=")[0] + "=<REDACTED>", text)
    text = _SAS_QUERY_KEYS.sub(lambda m: m.group(0).split("=")[0] + "=<REDACTED>", text)
    if token and len(token) >= 6:
        text = text.replace(token, "<TOKEN_REDACTED>")
    return text


class RedactedStr(str):
    """已经过遮蔽、可安全入日志的字符串。"""


# ---------------- transport 抽象 ----------------

@dataclass
class HttpResponse:
    status: int
    headers: dict
    content: bytes
    url: str = ""

    def json(self):
        return json.loads(self.content.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class HttpTransport:
    """transport 接口：request(method, url, headers, json_body, data, params)。"""

    def request(self, method: str, url: str, *, headers: dict | None = None,
                json_body: Any = None, data: bytes | None = None,
                params: dict | None = None,
                allow_redirects: bool = True, timeout: float = 30.0) -> HttpResponse:
        raise NotImplementedError


class MockHttpTransport(HttpTransport):
    """离线虚拟服务器：按 handler(request_dict)->MockResponse 应答，记录全部请求。
    G3-A 所有 HTTP 行为测试只通过它；零 socket（由 conftest socket 守卫再兜底）。"""

    def __init__(self, handler: Callable[[dict], HttpResponse | None] | None = None,
                 *, scripted: list | None = None):
        self.requests: list[dict] = []
        self._handler = handler
        self._scripted = list(scripted or [])

    def request(self, method, url, *, headers=None, json_body=None, data=None,
                params=None, allow_redirects=True, timeout=30.0):
        req = {"method": method.upper(), "url": url, "headers": dict(headers or {}),
               "json": json_body, "data": data, "params": dict(params or {}),
               "allow_redirects": allow_redirects}
        self.requests.append(req)
        if self._handler is not None:
            resp = self._handler(req)
            if resp is not None:
                return resp
        if self._scripted:
            return self._scripted.pop(0)
        raise AssertionError(f"MockHttpTransport 未配置应答: {method} {url}")

    @staticmethod
    def json_response(status: int, obj, *, headers: dict | None = None) -> HttpResponse:
        return HttpResponse(status, headers or {"content-type": "application/json"},
                            json.dumps(obj).encode("utf-8"))

    @staticmethod
    def bytes_response(status: int, data: bytes, *, headers: dict | None = None) -> HttpResponse:
        return HttpResponse(status, headers or {}, data)


class HttpxTransport(HttpTransport):
    """真实 transport（G3-B 显式构造；G3-A 不实例化）。httpx 惰性导入。
    不跟随外域 302 时由调用方决定；默认跟随用于匿名重下载。"""

    def __init__(self, *, verify: bool = True):
        import httpx  # 惰性：离线环境无该库也不影响 mock 测试
        self._httpx = httpx
        self._verify = verify

    def request(self, method, url, *, headers=None, json_body=None, data=None,
                params=None, allow_redirects=True, timeout=30.0):
        try:
            with self._httpx.Client(verify=self._verify, follow_redirects=allow_redirects,
                                    timeout=timeout) as c:
                r = c.request(method, url, headers=headers, json=json_body,
                              content=data, params=params)
                return HttpResponse(r.status_code, dict(r.headers), r.content,
                                    url=str(r.url))
        except Exception as e:
            raise _map_transport_error(e, url=url) from None


def _map_transport_error(e: Exception, *, url: str) -> Exception:
    name = type(e).__name__
    module = type(e).__module__ or ""
    safe_url = redact(url)
    if "Timeout" in name:
        return NetworkInterrupt(f"请求超时({name}): {safe_url}")
    if "Connect" in name or "Read" in name or "Write" in name or "Pool" in name:
        return NetworkInterrupt(f"网络黑窗({name}): {safe_url}")
    return NetworkInterrupt(f"传输失败({module}.{name}): {safe_url}")


# ---------------- 错误映射 ----------------

class UnprocessableEntity422(ServerError):
    """GitHub 422：语义冲突（如同名资产已存在）。必须先只读核实，禁止覆盖/删除。"""
    code = "unprocessable_422"
    retryable = False

    def __init__(self, msg: str = "", *, response: dict | None = None):
        super().__init__(msg)
        self.response = response or {}


class NotFound404(Exception):
    code = "not_found_404"
    retryable = False


def _api_error(status: int, body: dict, *, token: str | None, url: str):
    msg = redact(str(body.get("message", ""))[:300], token=token)
    if status in (401, 403):
        return AuthError(f"GitHub {status}: {msg or 'auth/permission'} @ {redact(url)}")
    if status == 404:
        return NotFound404(f"not found @ {redact(url)}")
    if status == 409:
        return AssetConflict(f"GitHub 409 冲突: {msg} @ {redact(url)}")
    if status == 422:
        return UnprocessableEntity422(f"GitHub 422: {msg} @ {redact(url)}", response=body)
    if status == 429:
        return RateLimited(f"GitHub 429: {msg}", retry_after=1.0)
    if 500 <= status < 600:
        return ServerError(f"GitHub {status}: {msg}")
    return ServerError(f"GitHub 意外状态 {status}: {msg}")


# ---------------- Release 适配器 ----------------

class GitHubReleaseAdapter(ReleaseAdapter):
    """真实 GitHub Release 资产适配器。

    - 读：GET release/assets/repo/tags/immutable-releases（匿名或带 token 均可）；
    - 写：默认禁用；G3-B 用 armed(owner, repo, token, transport=HttpxTransport()) 显式开启。
    """

    def __init__(self, owner: str, repo: str, transport: HttpTransport, *,
                 writes_enabled: bool = False, token: str | None = None,
                 api_base: str = API_BASE, upload_base: str = UPLOAD_BASE,
                 index_client: "GitHubContentsIndex | None" = None):
        self.owner = owner
        self.repo = repo
        self.transport = transport
        self._writes = bool(writes_enabled)
        self._token = token
        self.api_base = api_base.rstrip("/")
        self.upload_base = upload_base.rstrip("/")
        self._log: list[str] = []
        self._index = index_client

    # ---- 构造 ----
    @classmethod
    def armed(cls, owner, repo, token, transport, *, api_base=API_BASE,
              upload_base=UPLOAD_BASE, index_client=None):
        """G3-B 唯一合法开启写入的入口：必须显式 token + transport。"""
        if not token or not isinstance(token, str):
            raise RemoteWriteDisabled("armed() 需要显式一次性 token")
        return cls(owner, repo, transport, writes_enabled=True, token=token,
                   api_base=api_base, upload_base=upload_base,
                   index_client=index_client)

    # ---- 基础设施 ----
    def _headers(self, *, json_accept=True, octet=False):
        h = {"Accept": ("application/octet-stream" if octet else
                        "application/vnd.github+json"),
             "X-GitHub-Api-Version": API_VERSION,
             "User-Agent": "relstore-g3a"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _require_writes(self, what: str):
        if not (self._writes and self._token):
            raise RemoteWriteDisabled(
                f"G3-A 禁止远端写入（{what}）；须 G3-B 授权后 armed() 开启")

    def _get(self, path_or_url, *, api=True, octet=False, allow_redirects=True):
        if path_or_url.startswith("http"):
            url = path_or_url
        else:
            tail = str(path_or_url).strip("/")
            url = (f"{self.api_base}/repos/{self.owner}/{self.repo}"
                   + (f"/{tail}" if tail else ""))
        return self.transport.request(
            "GET", url, headers=self._headers(octet=octet),
            allow_redirects=allow_redirects)

    def _api(self, method, path, *, body=None, params=None):
        self._require_writes(f"{method} {path}")
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}/{path.lstrip('/')}"
        r = self.transport.request(method, url, headers=self._headers(),
                                   json_body=body, params=params)
        if r.status not in (200, 201):
            self._classify(r, url)
        return r

    def _classify(self, r: HttpResponse, url: str):
        try:
            body = r.json()
        except Exception:
            body = {"message": r.text[:200]}
        raise _api_error(r.status, body, token=self._token, url=url)

    def call_log(self) -> list:
        return list(self._log)

    # ---- 只读 ----
    def get_repo_info(self) -> dict | None:
        r = self._get("")  # /repos/{owner}/{repo}
        if r.status == 200:
            d = r.json()
            return {"default_branch": d.get("default_branch"), "size": d.get("size"),
                    "private": d.get("private"), "has_pages": d.get("has_pages"),
                    "archived": d.get("archived")}
        if r.status == 404:
            return None
        self._classify(r, "repo")

    def get_immutable_releases(self) -> dict:
        """GET /repos/{o}/{r}/immutable-releases。
        返回 {state: enabled|disabled|unknown, raw, note}。
        404/403 都不得被单点解释为‘已关闭/无权限’的最终结论。"""
        r = self._get("immutable-releases")
        if r.status == 200:
            raw = r.json()
            enabled = bool(raw.get("enabled")) if isinstance(raw, dict) else None
            return {"state": ("enabled" if enabled else "disabled"),
                    "raw": raw, "http": 200,
                    "note": "200 实查" if enabled is not None else
                            "200 但响应无 enabled 字段，按 unknown 处理"}
        if r.status == 404:
            return {"state": "unknown", "http": 404,
                    "note": "404：可能是该计划/版本不支持此端点或仓库类型不适用；"
                            "不能据此断言设置关闭，G3-B 前需二次核实（网页/文档）"}
        if r.status in (401, 403):
            return {"state": "unknown", "http": r.status,
                    "note": "401/403：匿名/当前凭据不可见该设置，不等于关闭"}
        self._classify(r, "immutable-releases")

    def list_tags(self) -> list[str]:
        r = self._get("tags?per_page=100")
        if r.status != 200:
            self._classify(r, "tags")
        return [t["name"] for t in r.json()]

    def get_release_by_tag(self, tag: str) -> ReleaseHandle | None:
        from urllib.parse import quote
        r = self._get(f"releases/tags/{quote(tag, safe='')}")
        if r.status == 404:
            return None
        if r.status != 200:
            self._classify(r, f"releases/tags/{tag}")
        d = r.json()
        return ReleaseHandle(id=str(d["id"]), tag=d["tag_name"],
                             name=d.get("name", ""))

    def get_release(self, release_id: str) -> dict | None:
        r = self._get(f"releases/{release_id}")
        if r.status == 404:
            return None
        if r.status != 200:
            self._classify(r, f"releases/{release_id}")
        return r.json()

    def list_assets(self, release_id: str) -> list[AssetMeta]:
        r = self._get(f"releases/{release_id}/assets?per_page=100")
        if r.status != 200:
            self._classify(r, f"releases/{release_id}/assets")
        out = []
        for a in r.json():
            digest = a.get("digest") or a.get("digest_sha256") or None
            if digest and digest.startswith("sha256:"):
                digest = digest.split(":", 1)[1]
            out.append(AssetMeta(id=str(a["id"]), name=a["name"],
                                 size=a["size"], sha256=digest or "",
                                 state=a.get("state", "uploaded"),
                                 release_id=release_id,
                                 release_tag=(a.get("release") or {}).get("tag_name")))
        return out

    def get_asset(self, release_id: str, name: str) -> AssetMeta | None:
        for a in self.list_assets(release_id):
            if a.name == name:
                return a
        return None

    def download_asset(self, asset_id: str) -> bytes:
        """匿名重下载（仅对已 Publish 的资产有效）：
        先经 API 元数据取 browser_download_url，再匿名跟随 302 取字节。
        302 目标是平台短期 SAS——绝不记入日志/异常（redact 兜底）。
        G3-B 修正：Draft 资产匿名不可见，须用 download_asset_authenticated。"""
        r = self._get(f"releases/assets/{asset_id}",
                      octet=False, allow_redirects=False)
        # GitHub 也支持 Accept: octet-stream 直接 302
        if r.status not in (200, 302):
            self._classify(r, f"releases/assets/{asset_id}")
        url = r.headers.get("location") or ""
        if not url:
            rel = self._get(f"releases/assets/{asset_id}")
            if rel.status != 200:
                self._classify(rel, f"releases/assets/{asset_id}")
            url = rel.json().get("browser_download_url", "")
        r2 = self.transport.request("GET", url,
                                    headers={"Accept": "application/octet-stream",
                                             "User-Agent": "relstore-g3a"},
                                    allow_redirects=True)
        if r2.status != 200:
            raise ServerError(
                f"重下载失败 HTTP {r2.status} @ {redact(url, token=self._token)}")
        return r2.content

    def download_asset_authenticated(self, asset_id: str, *,
                                     not_found_grace_seconds: float = 0.0,
                                     not_found_poll_seconds: float = 5.0) -> bytes:
        """G3-B：Draft（或私有）资产的带权限二进制下载。
        API 资产端点 + Accept: application/octet-stream + 凭据 → 302 到对象存储，
        重定向后的非 GitHub API 域【不带 Authorization】（短期 SAS，绝不入日志）。

        not_found_grace_seconds > 0 仅供上传方回读自证场景：大资产上传 200 后
        API 侧 finalize 存在传播窗口（daycarry20m-20261102 实测 1.4GB 资产
        差 ~0.7s 可见），期间 GET 404；按 not_found_poll_seconds 退避重试至
        窗口超时后才抛 NotFound404。默认 0：404 快速失败，
        resolve/唤醒/巡检等调用方语义保持不变。"""
        deadline = time.monotonic() + max(0.0, not_found_grace_seconds)
        while True:
            try:
                return self._download_asset_authenticated_once(asset_id)
            except NotFound404:
                if not not_found_grace_seconds or time.monotonic() >= deadline:
                    raise
                time.sleep(not_found_poll_seconds)

    def _download_asset_authenticated_once(self, asset_id: str) -> bytes:
        url = (f"{self.api_base}/repos/{self.owner}/{self.repo}/"
               f"releases/assets/{asset_id}")
        r = self.transport.request("GET", url, headers=self._headers(octet=True),
                                   allow_redirects=False)
        if r.status == 200:
            return r.content
        if r.status != 302:
            self._classify(r, url)
        loc = r.headers.get("location") or ""
        r2 = self.transport.request(
            "GET", loc,
            headers={"Accept": "application/octet-stream",
                     "User-Agent": "relstore-g3b"},
            allow_redirects=True)
        if r2.status != 200:
            raise ServerError(
                f"Draft 二进制下载失败 HTTP {r2.status} @ {redact(loc, token=self._token)}")
        return r2.content

    # ---- 写（默认硬禁；armed 后才可能走到 HTTP）----
    def create_release(self, tag: str, name: str = "", body: str = "", *,
                       target_commitish: str | None = None,
                       draft: bool = True) -> ReleaseHandle:
        self._require_writes("create_release")
        # 同 tag 幂等（回执丢失/重入）：已有同 tag Release → 复用，不重复创建
        existing = self.get_release_by_tag(tag)
        if existing is not None:
            self._log.append(f"create_release idempotent reuse tag={tag}")
            return existing
        payload: dict = {"tag_name": tag, "name": name, "body": body,
                         "draft": draft}
        if target_commitish:
            payload["target_commitish"] = target_commitish
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}/releases"
        r = self.transport.request("POST", url, headers=self._headers(),
                                   json_body=payload)
        if r.status not in (200, 201):
            self._classify(r, url)
        d = r.json()
        return ReleaseHandle(id=str(d["id"]), tag=d["tag_name"],
                             name=d.get("name", ""))

    def publish_release(self, release_id: str, *, make_latest: str = "false") -> None:
        """发布 Draft。G3-B 修正：make_latest 默认 "false"，不改变仓库 latest 展示。"""
        self._require_writes("publish_release")
        url = (f"{self.api_base}/repos/{self.owner}/{self.repo}/releases/"
               f"{release_id}")
        r = self.transport.request("PATCH", url, headers=self._headers(),
                                   json_body={"draft": False,
                                              "make_latest": make_latest})
        if r.status not in (200, 201):
            self._classify(r, url)

    def upload_asset(self, release_id: str, name: str, data: bytes,
                     expected_sha256: str) -> AssetMeta:
        """上传资产；422 同名冲突时【只读】核实 name+size+digest：
        同 sha 复用（幂等），异 sha AssetConflict，绝不删除/覆盖。"""
        self._require_writes("upload_asset")
        if len(data) >= MAX_ASSET_BYTES:
            raise AssetConflict("资产达到/超过 2GiB 上限，必须分卷")
        import hashlib
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_sha256:
            raise AssetConflict("调用方声明 sha 与字节不符（拒绝上传）")
        url = (f"{self.upload_base}/repos/{self.owner}/{self.repo}/releases/"
               f"{release_id}/assets")
        headers = self._headers()  # uploads 端点要求 Accept 为 JSON
        headers["Content-Type"] = "application/octet-stream"  # 资产本体类型
        # 🔴 2026-11-07 posthoc 42384612977 实锤：2GB 本体的 POST 用默认 30s
        # 超时必然 ReadTimeout（传输+处理远超 30s），客户端掐断后服务端可能
        # 留下半成品。按体积放宽：30s 基线 + 每 2MB 加 1s，900s 封顶。
        post_timeout = min(30.0 + len(data) / (2 * 1024 * 1024), 900.0)
        r = self.transport.request(
            "POST", url, headers=headers, data=data,
            params={"name": name}, timeout=post_timeout)
        if r.status == 422:
            return self._reconcile_existing_asset(release_id, name, len(data),
                                                  expected_sha256)
        if r.status not in (200, 201):
            self._classify(r, url)
        a = r.json()
        # POST 回执不可信（见模块头 🔴 注释）：立即 GET 复核 finalize。
        state = self._require_asset_finalized(str(a["id"]), name)
        return AssetMeta(id=str(a["id"]), name=a["name"], size=a["size"],
                         sha256=digest, state=state,
                         release_id=release_id)

    def _require_asset_finalized(self, asset_id: str, name: str) -> str:
        """🔴 ink-diffusion-20261107：POST 201 的 state 不足以采信，必须以
        GET /releases/assets/{id} 的 state=uploaded 为准。starter 超时（默认
        300s）→ ReceiptLost fail-loud，绝不返回假成功。短暂 404 属 finalize
        传播窗口（daycarry20m-20261102 实测 ~0.7s），继续轮询不误杀。"""
        deadline = time.monotonic() + ASSET_FINALIZE_TIMEOUT_S
        while True:
            r = self._get(f"releases/assets/{asset_id}")
            if r.status == 404:
                pass  # 传播窗口：继续等（见 docstring）
            elif r.status != 200:
                self._classify(r, f"releases/assets/{asset_id}")
            else:
                state = r.json().get("state", "uploaded")
                if state == "uploaded":
                    self._log.append(f"asset finalize confirmed: {name} "
                                     f"id={asset_id}")
                    return state
            if time.monotonic() >= deadline:
                raise ReceiptLost(
                    f"资产 POST 201 后 {int(ASSET_FINALIZE_TIMEOUT_S)}s 内 state "
                    f"仍未达 uploaded（starter 僵尸，🔴 ink-diffusion-20261107 "
                    f"实锤）：{name} asset_id={asset_id}。回执不可信，不得复用"
                    f"或删除原资产，请以新资产名重传")
            time.sleep(ASSET_FINALIZE_POLL_S)

    def _reconcile_existing_asset(self, release_id, name, size, sha):
        """422 同名：只列资产比对，不删/不覆盖。"""
        existing = self.get_asset(release_id, name)
        if existing is None:
            raise UnprocessableEntity422(
                f"422 同名冲突但列资产未找到 {name}（停止人工核实）")
        # 🔴 starter 僵尸（ink-diffusion-20261107）：state=starter 的同名资产
        # 永远 404，把它当幂等复用成功=再次踩同一个坑。拒绝，要求换名重传。
        if existing.state == "starter":
            raise AssetConflict(
                f"同名资产 state=starter（finalize 僵尸，🔴 ink-diffusion-"
                f"20261107 实锤）不可幂等复用: {name} asset_id={existing.id}；"
                f"请以新资产名重传，不得删除/覆盖原资产")
        same = existing.name == name and existing.size == size and \
            (not existing.sha256 or existing.sha256 == sha)
        if not same:
            raise AssetConflict(
                f"同名异 sha 资产已存在，拒绝覆盖: {name} "
                f"(remote size={existing.size} sha={existing.sha256[:12]}…)")
        self._log.append(f"422 reconciled idempotent reuse: {name}")
        return existing

    def upload_asset_with_recovery(self, release_id, name, data, expected_sha256):
        """上传 + 回执丢失恢复：超时/读错后先 GET 列表按 name+size+sha 收敛，
        绝不重复 POST。🔴 ink-diffusion-20261107：收敛条件必须含 state=uploaded
        ——starter 资产（finalize 僵尸）永远 404，把它当"恢复成功"等于把假回执
        又信了一遍。finalize 轮询超时（ReceiptLost）后允许再 GET 一次终核：
        已 uploaded 且同 sha → 复用（慢 finalize 最终落定）；仍 starter → 维持
        fail-loud。"""
        def _confirmed_existing():
            existing = self.get_asset(release_id, name)
            if existing is not None and existing.state == "uploaded" \
                    and existing.size == len(data) and \
                    (not existing.sha256 or existing.sha256 == expected_sha256):
                return existing
            return None

        try:
            return self.upload_asset(release_id, name, data, expected_sha256)
        except NetworkInterrupt:
            existing = _confirmed_existing()
            if existing is not None:
                self._log.append("receipt lost -> recovered existing asset")
                return existing
            raise ReceiptLost("上传后回执丢失且未能确认远端资产，停止等待人工/重入")
        except ReceiptLost:
            existing = _confirmed_existing()
            if existing is not None:
                self._log.append("finalize timeout -> later confirmed "
                                 "uploaded, idempotent reuse")
                return existing
            raise

    def publish_index(self, project_id: str, index_doc: dict) -> dict:
        """委托 B1 ContentsIndex 做 CAS 写入（索引最后发布）。未接线时明确报错。"""
        if self._index is None:
            raise NotImplementedError(
                "未接线 B1 GitHubContentsIndex；索引发布必须显式组合")
        cur = self._index.read()  # 取当前 blob sha（None=文件尚不存在）
        return self._index.write_cas(
            index_doc, None if cur is None else cur["blob_sha"],
            message=f"g3 index {project_id} r{index_doc.get('revision', '?')}")


# ---------------- B1 Contents 索引（CAS） ----------------

class GitHubContentsIndex:
    """B1 轻量索引（少量 JSON，单文件 CAS）。写默认硬禁，同适配器纪律。"""

    def __init__(self, owner: str, repo: str, branch: str, path: str,
                 transport: HttpTransport, *, writes_enabled: bool = False,
                 token: str | None = None, api_base: str = API_BASE):
        self.owner, self.repo, self.branch, self.path = owner, repo, branch, path
        self.transport = transport
        self._writes = bool(writes_enabled)
        self._token = token
        self.api_base = api_base.rstrip("/")

    @classmethod
    def armed(cls, owner, repo, branch, path, token, transport, *, api_base=API_BASE):
        if not token:
            raise RemoteWriteDisabled("armed() 需要显式一次性 token")
        return cls(owner, repo, branch, path, transport, writes_enabled=True,
                   token=token, api_base=api_base)

    def _headers(self):
        h = {"Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": API_VERSION, "User-Agent": "relstore-g3a"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _require_writes(self, what):
        if not (self._writes and self._token):
            raise RemoteWriteDisabled(f"G3-A 禁止 B1 写入（{what}）")

    def read(self) -> dict | None:
        """返回 {doc, blob_sha} 或 None（404）。匿名 GET。"""
        from urllib.parse import quote
        url = (f"{self.api_base}/repos/{self.owner}/{self.repo}/contents/"
               f"{quote(self.path, safe='/')}?ref={self.branch}")
        r = self.transport.request("GET", url, headers=self._headers())
        if r.status == 404:
            return None
        if r.status != 200:
            body = {}
            try:
                body = r.json()
            except Exception:
                pass
            raise _api_error(r.status, body, token=self._token, url=url)
        d = r.json()
        if d.get("encoding") != "base64":
            raise ServerError(f"B1 索引非 base64 编码: {d.get('encoding')}")
        doc = json.loads(base64.b64decode(d["content"]).decode("utf-8"))
        return {"doc": doc, "blob_sha": d["sha"]}

    def write_cas(self, doc: dict, expected_blob_sha: str | None,
                 *, message: str) -> dict:
        """以旧 blob sha 乐观并发写入。expected_blob_sha=None 表示文件必须尚不存在。
        409/422 → AssetConflict（调用方保留本地候选，不得覆盖重试）。"""
        self._require_writes("contents PUT")
        content = base64.b64encode(
            json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).decode("ascii")
        body: dict = {"message": message, "content": content,
                      "branch": self.branch}
        if expected_blob_sha is not None:
            body["sha"] = expected_blob_sha
        from urllib.parse import quote
        url = (f"{self.api_base}/repos/{self.owner}/{self.repo}/contents/"
               f"{quote(self.path, safe='/')}")
        r = self.transport.request("PUT", url, headers=self._headers(),
                                   json_body=body)
        if r.status in (200, 201):
            d = r.json()
            return {"ok": True, "http": r.status,
                    "new_sha": (d.get("content") or {}).get("sha"),
                    "commit": (d.get("commit") or {}).get("sha")}
        payload = {}
        try:
            payload = r.json()
        except Exception:
            pass
        if r.status in (409, 422):
            raise AssetConflict(
                f"B1 CAS 冲突 HTTP {r.status}: "
                f"{redact(str(payload.get('message', '')), token=self._token)}")
        raise _api_error(r.status, payload, token=self._token, url=url)

    def raw_put_text(self, text: str, *, message: str) -> dict:
        """写入纯文本文件（README 初始化）。仅允许文件尚不存在（无 sha）的场景；
        已存在/冲突按 409/422 停机，不覆盖。"""
        self._require_writes("raw put")
        content = base64.b64encode(text.encode("utf-8")).decode("ascii")
        from urllib.parse import quote
        url = (f"{self.api_base}/repos/{self.owner}/{self.repo}/contents/"
               f"{quote(self.path, safe='/')}")
        r = self.transport.request("PUT", url, headers=self._headers(),
                                   json_body={"message": message,
                                              "content": content,
                                              "branch": self.branch})
        if r.status in (200, 201):
            d = r.json()
            return {"ok": True, "http": r.status,
                    "new_sha": (d.get("content") or {}).get("sha"),
                    "commit": (d.get("commit") or {}).get("sha")}
        payload = {}
        try:
            payload = r.json()
        except Exception:
            pass
        raise _api_error(r.status, payload, token=self._token, url=url)

    def get_branch_head(self, branch: str | None = None) -> str | None:
        """返回分支 HEAD commit SHA；分支不存在返回 None。
        真实 GitHub 差异（G3-B 实测）：空仓 GET ref 返回 **409 "Git Repository
        is empty."**（非 404）→ 同样视为分支不存在。"""
        b = branch or self.branch
        from urllib.parse import quote
        url = (f"{self.api_base}/repos/{self.owner}/{self.repo}/"
               f"git/ref/heads/{quote(b, safe='')}")
        r = self.transport.request("GET", url, headers=self._headers())
        if r.status == 404:
            return None
        if r.status == 409:
            try:
                msg = str(r.json().get("message", ""))
            except Exception:
                msg = ""
            if "empty" in msg.lower():
                return None  # 空仓=分支不存在
        if r.status != 200:
            body = {}
            try:
                body = r.json()
            except Exception:
                pass
            raise _api_error(r.status, body, token=self._token, url=url)
        return r.json()["object"]["sha"]

    def create_branch_from(self, base_branch: str, new_branch: str) -> dict:
        """从既有分支建新分支（git data refs API）。仅测试命名域使用。"""
        self._require_writes("create branch")
        base = self.get_branch_head(base_branch)
        if base is None:
            raise ServerError(f"基分支不存在: {base_branch}")
        url = f"{self.api_base}/repos/{self.owner}/{self.repo}/git/refs"
        r = self.transport.request("POST", url, headers=self._headers(),
                                   json_body={"ref": f"refs/heads/{new_branch}",
                                              "sha": base})
        if r.status == 201:
            return {"ok": True, "http": 201, "base": base,
                    "ref": r.json().get("ref")}
        payload = {}
        try:
            payload = r.json()
        except Exception:
            pass
        raise _api_error(r.status, payload, token=self._token, url=url)


def bootstrap_default_branch_plan(*, owner: str, repo: str,
                                  readme_text: str) -> list[dict]:
    """B1 空仓最小初始化【计划】（G3-A 只设计不执行）。
    空 Contents PUT 不能自动建分支（无 HEAD）；须先 git data API 建初始提交：
    1) POST /git/trees（单个 README blob）2) POST /git/commits 3) POST /git/refs/heads/main
    或一次性 Contents API（部分账户在空仓可直接 PUT /contents/README.md 到默认分支）。
    返回可审计的最小步骤清单。"""
    return [
        {"step": 1, "method": "GET", "path": f"/repos/{owner}/{repo}",
         "purpose": "确认仓库仍为空（size=0、无 default_branch 内容），只执行一次"},
        {"step": 2, "method": "PUT",
         "path": f"/repos/{owner}/{repo}/contents/README.md",
         "body": {"message": "G3-B: B1 初始化（仅 README，无媒体）",
                  "content": "<base64 README>", "branch": "main"},
         "purpose": "最小 main 初始化；若 404/409 则降级 git data 三步，仍失败即停机"},
        {"step": 3, "method": "GET", "path": f"/repos/{owner}/{repo}/branches/main",
         "purpose": "只读确认 default branch 就绪；初始化只允许一次"},
    ]
