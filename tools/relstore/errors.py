# tools/relstore/errors.py
# G2-LOCAL-RELSTORE：归档/恢复域错误分类（本地隔离开发，零网络依赖）。
from __future__ import annotations


class RelstoreError(Exception):
    """relstore 域错误基类。code 为机器可读分类。"""
    code = "relstore_error"

    def __init__(self, msg: str = "", *, retryable: bool | None = None):
        super().__init__(msg or self.code)
        if retryable is not None:
            self.retryable = retryable


# ---- 归档包安全/格式 ----
class UnsafeArchive(RelstoreError):
    code = "unsafe_archive"
    retryable = False


class UnsupportedCompression(UnsafeArchive):
    """G2 只接受未压缩 tar（tar-pax）；gz/xz/zst/zip 明确拒绝，不调系统命令绕过。"""
    code = "unsupported_compression"


class CorruptArchive(UnsafeArchive):
    code = "corrupt_archive"


# ---- manifest ----
class ManifestError(RelstoreError):
    code = "manifest_error"
    retryable = False


class ForbiddenManifestField(ManifestError):
    code = "forbidden_manifest_field"


# ---- 哈希/字节 ----
class HashMismatch(RelstoreError):
    code = "hash_mismatch"
    retryable = False


class ByteCountMismatch(RelstoreError):
    code = "byte_count_mismatch"
    retryable = False


# ---- 依赖/可恢复性 ----
class MissingRequiredDependency(RelstoreError):
    """缺必需依赖（skill 实体/原始 input/不可变指针），不得标记 fully restorable。"""
    code = "missing_required_dependency"
    retryable = False


# ---- 源版本 ----
class SourceRevisionChanged(RelstoreError):
    """打包/上传期间源项目 revision 变化。旧包永不得成为删除新副本的许可。"""
    code = "source_revision_changed"
    retryable = False


# ---- Release 适配层 ----
class AdapterError(RelstoreError):
    code = "adapter_error"


class RateLimited(AdapterError):
    code = "rate_limited_429"
    retryable = True

    def __init__(self, msg: str = "", *, retry_after: float = 1.0):
        super().__init__(msg)
        self.retry_after = retry_after


class ServerError(AdapterError):
    code = "server_error_5xx"
    retryable = True


class NetworkInterrupt(AdapterError):
    code = "network_interrupt"
    retryable = True


class AuthError(AdapterError):
    """401/403：停止/报告，禁止盲重试。"""
    code = "auth_error_401_403"
    retryable = False


class AssetConflict(AdapterError):
    """同名资产不同 sha：CONFLICT，绝不覆盖。"""
    code = "asset_conflict"
    retryable = False


class ReceiptLost(AdapterError):
    """上传可能成功但回执丢失：按幂等账重入核对，禁止重复制造资产。"""
    code = "receipt_lost"
    retryable = True


class OrphanAsset(RelstoreError):
    """资产已上传但索引发布失败；保留源件，登记孤儿等待重入收养。"""
    code = "orphan_asset"


class IndexPublishFailed(RelstoreError):
    code = "index_publish_failed"
    retryable = True


class InsufficientSpace(RelstoreError):
    code = "insufficient_space"
    retryable = False


class RemoteWriteDisabled(RelstoreError):
    """G2 硬禁用：真实 Release 写路径在本批不可用。"""
    code = "remote_write_disabled_g2"
    retryable = False


class IllegalRename(RelstoreError):
    code = "illegal_rename"
    retryable = False


class ImmutableStateUnknown(RelstoreError):
    """Immutable Releases 状态无法确认（G3-B 修正：匿名 401≠关闭）。
    远端写入前必须由 Administration:read 或所有者 Settings 确认；UNKNOWN 即停机。"""
    code = "immutable_state_unknown"
    retryable = False
