# tools/relstore/release_adapter.py
# Release 资产存储适配层（抽象接口）。
# G2 纪律：本模块【不含可用的真实上传/下载网络路径】——
#   真实写操作一律 RemoteWriteDisabled；所有测试走 FakeReleaseAdapter（本地内存）。
#   G3 获用户授权后才在此文件另实现 GitHubReleaseAdapter，且必须带 PAT 最小权限。
from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class AssetMeta:
    id: str
    name: str
    size: int
    sha256: str
    state: str = "uploaded"
    release_id: str | None = None
    release_tag: str | None = None


@dataclass
class ReleaseHandle:
    id: str
    tag: str
    name: str = ""


class ReleaseAdapter(abc.ABC):
    """归档目标存储的最小接口。幂等语义由实现保证：
    - 同名 + 同 sha256 → 复用既有资产（不重复制造）
    - 同名 + 异 sha256 → AssetConflict（绝不覆盖）
    """

    @abc.abstractmethod
    def create_release(self, tag: str, name: str = "", body: str = "") -> ReleaseHandle: ...

    @abc.abstractmethod
    def upload_asset(self, release_id: str, name: str, data: bytes,
                     expected_sha256: str) -> AssetMeta: ...

    @abc.abstractmethod
    def get_asset(self, release_id: str, name: str) -> AssetMeta | None: ...

    @abc.abstractmethod
    def download_asset(self, asset_id: str) -> bytes: ...

    @abc.abstractmethod
    def list_assets(self, release_id: str) -> list[AssetMeta]: ...

    @abc.abstractmethod
    def publish_index(self, project_id: str, index_doc: dict) -> dict:
        """B1 权威索引最后发布（模拟）；返回发布结果。"""

    @abc.abstractmethod
    def call_log(self) -> list:
        """调用记录（测试用于断言零真实写、顺序与重试次数）。"""


class G2DisabledReleaseAdapter(ReleaseAdapter):
    """G2 唯一允许随代码存在的"真实"适配器：所有接口方法硬失败。

    保证即使生产代码误接线到本类，也不可能产生任何网络/远端副作用。
    """

    def _disabled(self):
        from .errors import RemoteWriteDisabled
        raise RemoteWriteDisabled(
            "G2 批次禁止任何真实 Release 网络路径（本地隔离开发）")

    def create_release(self, *a, **k): self._disabled()
    def upload_asset(self, *a, **k): self._disabled()
    def get_asset(self, *a, **k): self._disabled()
    def download_asset(self, *a, **k): self._disabled()
    def list_assets(self, *a, **k): self._disabled()
    def publish_index(self, *a, **k): self._disabled()
    def call_log(self) -> list: return []
