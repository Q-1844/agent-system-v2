# tools/relstore/fake_release_adapter.py
# 纯内存 Fake Release 适配器：离线模拟资产存储、幂等账与故障注入。
# 零 socket/httpx 依赖；G2 全部归档-恢复链路只允许通过它或 G2Disabled 运行。
from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass, field

from .errors import (AssetConflict, AuthError, IndexPublishFailed,
                     NetworkInterrupt, RateLimited, ReceiptLost, ServerError)
from .release_adapter import AssetMeta, ReleaseAdapter, ReleaseHandle


@dataclass
class _StoredAsset:
    name: str
    data: bytes
    sha256: str
    asset_id: str


class FakeReleaseAdapter(ReleaseAdapter):
    """故障通过 failures[method] 队列注入，按该方法调用次数依次消耗：
    RateLimited(retry_after) / ServerError / NetworkInterrupt / AuthError /
    "receipt_lost"（仅 get_asset：资产已在但首次查询报错）/
    "download_corrupt_once"（download 首次返回坏字节）/
    "index_fail_once"（publish_index 首次失败，模拟孤儿）。
    """

    def __init__(self, *, failures: dict | None = None, time_func=None):
        self._releases: dict[str, dict] = {}
        self._ids = itertools.count(1000)
        self._failures = failures or {}
        self._log: list[dict] = []
        self._download_corrupt: set[str] = set()
        self.published_indexes: dict[str, dict] = {}
        self.time_func = time_func

    def _log_call(self, method: str, **kw):
        self._log.append({"method": method, **kw})

    def call_log(self) -> list:
        return list(self._log)

    def _maybe_fail(self, method: str):
        q = self._failures.get(method)
        if not q:
            return
        item = q.pop(0)
        if isinstance(item, Exception):
            raise item
        kind = item[0] if isinstance(item, tuple) else item
        if kind == "receipt_lost":
            raise ReceiptLost("模拟：上传后查询回执丢失")
        if kind == "download_corrupt_once":
            self._download_corrupt.add("once")
            return  # 本次不抛错，在 download 路径产出坏字节
        if kind == "index_fail_once":
            raise IndexPublishFailed("模拟：索引发布失败（资产成孤儿）")
        raise AssertionError(f"未知故障注入: {item}")

    def create_release(self, tag: str, name: str = "", body: str = "") -> ReleaseHandle:
        self._log_call("create_release", tag=tag)
        self._maybe_fail("create_release")
        if tag in self._releases:
            return self._releases[tag]["handle"]  # 幂等：同 tag 复用
        rid = f"rel_{next(self._ids)}"
        h = ReleaseHandle(id=rid, tag=tag, name=name)
        self._releases[tag] = {"handle": h, "assets": {}}
        return h

    def upload_asset(self, release_id: str, name: str, data: bytes,
                     expected_sha256: str) -> AssetMeta:
        self._log_call("upload_asset", name=name, bytes=len(data))
        self._maybe_fail("upload_asset")
        rel = self._find_release(release_id)
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_sha256:
            raise AssetConflict("调用方声明 sha 与字节不符（拒绝写入）")
        existing = rel["assets"].get(name)
        if existing is not None:
            if existing.sha256 == digest:
                self._log[-1]["idempotent_reuse"] = True
                return self._meta(existing, rel["handle"])
            raise AssetConflict(f"同名异 sha 冲突，拒绝覆盖: {name}")
        aid = f"ast_{next(self._ids)}"
        stored = _StoredAsset(name=name, data=data, sha256=digest, asset_id=aid)
        rel["assets"][name] = stored
        return self._meta(stored, rel["handle"])

    def get_asset(self, release_id: str, name: str) -> AssetMeta | None:
        self._log_call("get_asset", name=name)
        self._maybe_fail("get_asset")
        rel = self._find_release(release_id)
        s = rel["assets"].get(name)
        return self._meta(s, rel["handle"]) if s else None

    def list_assets(self, release_id: str) -> list[AssetMeta]:
        self._log_call("list_assets")
        self._maybe_fail("list_assets")
        rel = self._find_release(release_id)
        h = rel["handle"]
        return [self._meta(s, h) for s in rel["assets"].values()]

    def download_asset(self, asset_id: str) -> bytes:
        self._log_call("download_asset", asset_id=asset_id)
        self._maybe_fail("download_asset")
        for rel in self._releases.values():
            for s in rel["assets"].values():
                if s.asset_id == asset_id:
                    if "once" in self._download_corrupt:
                        self._download_corrupt.discard("once")
                        # 翻转首字节：保证 sha 必然不符（空包除外）
                        b = bytearray(s.data)
                        if b:
                            b[0] ^= 0xFF
                        return bytes(b)
                    return s.data
        raise KeyError(asset_id)

    def publish_index(self, project_id: str, index_doc: dict) -> dict:
        self._log_call("publish_index", project_id=project_id)
        self._maybe_fail("publish_index")
        self.published_indexes[project_id] = dict(index_doc)
        return {"ok": True, "project_id": project_id}

    # ---- 测试辅助 ----
    def _find_release(self, release_id: str):
        for rel in self._releases.values():
            if rel["handle"].id == release_id:
                return rel
        raise KeyError(f"unknown release {release_id}")

    @staticmethod
    def _meta(s: _StoredAsset, h: ReleaseHandle) -> AssetMeta:
        return AssetMeta(id=s.asset_id, name=s.name, size=len(s.data),
                         sha256=s.sha256, release_id=h.id, release_tag=h.tag)

    def find_orphan_assets(self, project_id: str, asset_name: str | None = None):
        """孤儿查找：资产已上传但索引未发布。重入恢复用。"""
        indexed = {v.get("asset_id") for v in self.published_indexes.values()
                   if isinstance(v, dict)}
        out = []
        for rel in self._releases.values():
            for s in rel["assets"].values():
                if s.asset_id not in indexed and (
                        asset_name is None or s.name == asset_name):
                    out.append(self._meta(s, rel["handle"]))
        return out

    @staticmethod
    def rate_limited(retry_after: float = 0.01):
        return RateLimited("模拟 429", retry_after=retry_after)

    @staticmethod
    def server_error():
        return ServerError("模拟 5xx")

    @staticmethod
    def network_interrupt():
        return NetworkInterrupt("模拟网络中断")

    @staticmethod
    def auth_error():
        return AuthError("模拟 401/403")
