# src/core/job_queue_ownership.py
# 🔴 跨进程账本所有权（Round05 Gate B）。
#
# 问题（第 04 轮 D1 实证）：cache/jobs.json 是**文件**账本。JobQueue._load() +
# _save() 的原子写只保证「单次写不撕裂」，**不保证「同一账本同一时刻只有一个进程在领任务」**。
# 两个进程各自 load 同一份 queued job → 各自领走 → 同一 job 执行两次
# （video kind = 重复付费生成；第 04 轮 D1 的假片段污染即同树并发跑 pytest 所致）。
#
# 既有 src/core/file_lock.py 是**短事务锁**（跨 await 不持有）——它保证单文件写不撕裂，
# 但**不解决**「谁拥有这个账本」。本模块提供的是**进程生命周期的单写者所有权**：
# - 身份 = **规范化账本路径**（不同账本互不阻塞——不误用全局大锁）；
# - 用**操作系统文件锁**（Windows: msvcrt.locking / POSIX: fcntl.flock）持有
#   **独立、稳定的 owner lock 文件**（与账本同目录、名字派生、与进程无关）；
# - 锁**由进程持续持有**（不是写账本瞬间的短锁）→ 进程被 kill 后由 OS 自动释放，
#   无需人工删除 lockfile，也不会因「心跳迟到」抢走正在等待真实供应商的任务；
# - 冲突时 **fail closed**：抛 JobQueueOwnershipError——调用方不得写账本、不得领任务、
#   不得启动任何恢复/结算/handler。
#
# 同一进程内多次构造同一账本的 JobQueue 视为**同进程重入**（一个进程 = 一个 owner），
# 保持既有单进程模型与既有测试语义不变。
#
# Windows 注意（沿用 file_lock.py 的实证结论）：LockFile 允许锁定 EOF 之后的字节，
# 因此**不得**为「让文件够长」而预先写入字节——否则另一个进程可能在 stat 与写之间
# 抢到该字节。本模块因此不写任何内容，只锁 byte 0。
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, List

__all__ = [
    "JobQueueOwnershipError",
    "LedgerOwnership",
    "acquire_ownership",
    "release_ownership",
    "canonical_ledger_path",
    "owner_lock_path",
    "owned_ledger_paths",
]


class JobQueueOwnershipError(RuntimeError):
    """另一个进程已拥有同一账本——本进程必须 fail closed（不写、不领、不恢复）。"""


def canonical_ledger_path(state_path) -> Path:
    """账本身份：规范化（解析符号链接/junction + 绝对化）后的路径。"""
    p = Path(state_path)
    try:
        return p.resolve(strict=False)
    except OSError:
        return Path(os.path.abspath(str(p)))


def owner_lock_path(state_path) -> Path:
    """owner lock 路径：与账本同目录、名字派生且稳定（不同账本 → 不同锁 → 不互相阻塞）。"""
    p = Path(state_path)
    return p.parent / (p.name + ".owner.lock")


class LedgerOwnership:
    """一个账本在本进程内的所有权句柄。"""

    __slots__ = ("ledger_path", "lock_path", "pid", "acquired_at",
                 "_stream", "_refs", "_released")

    def __init__(self, ledger_path: Path, lock_path: Path, stream):
        self.ledger_path = ledger_path
        self.lock_path = lock_path
        self.pid = os.getpid()
        self.acquired_at = time.time()
        self._stream = stream
        self._refs = 1
        self._released = False

    @property
    def released(self) -> bool:
        return self._released

    def acquire_extra_ref(self) -> "LedgerOwnership":
        """同进程重入：引用计数 +1（不重复向 OS 加锁）。"""
        if self._released:
            raise JobQueueOwnershipError(
                f"JobQueue owner lock already released for ledger {self.ledger_path}")
        self._refs += 1
        return self

    def release(self) -> None:
        """释放所有权（进程内）。OS 锁随 fd 关闭释放；进程被 kill 时由 OS 自动释放。"""
        if self._released:
            return
        if self._refs > 0:
            self._refs -= 1
        if self._refs > 0:
            return          # 仍有同进程引用持有该锁——不得提前关闭 fd
        self._released = True
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                _unlock_stream(stream)
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass


# 进程级注册表：key = 规范化账本路径
_REGISTRY: Dict[str, LedgerOwnership] = {}


def owned_ledger_paths() -> List[str]:
    """当前进程已拥有的账本路径列表（诊断/证据用）。"""
    return sorted(_REGISTRY.keys())


def _lock_stream(stream) -> None:
    """非阻塞排他锁。冲突时抛 OSError（errno EACCES/EAGAIN/EDEADLK）。"""
    if os.name == "nt":
        import msvcrt
        stream.seek(0)
        # LK_NBLCK：立即失败，不等待——所有权冲突必须 fail closed 而不是排队。
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_stream(stream) -> None:
    if os.name == "nt":
        import msvcrt
        try:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


def acquire_ownership(state_path, *, purpose: str = "job_queue") -> LedgerOwnership:
    """取得账本所有权。

    - 同进程重入：直接返回既有句柄（引用计数 +1）。
    - 跨进程冲突：抛 JobQueueOwnershipError（fail closed，绝不静默降级为「照常启动」）。
    """
    ledger = canonical_ledger_path(state_path)
    key = str(ledger)
    existing = _REGISTRY.get(key)
    if existing is not None:
        if existing.released:
            _REGISTRY.pop(key, None)
        else:
            return existing.acquire_extra_ref()

    lock_path = owner_lock_path(state_path)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise JobQueueOwnershipError(
            f"JobQueue owner lock path unusable (ledger={key}, lock_file={lock_path}): {e}"
        ) from e

    try:
        stream = open(lock_path, "a+b")
    except OSError as e:
        raise JobQueueOwnershipError(
            f"JobQueue owner lock file cannot be opened (ledger={key}, lock_file={lock_path}): {e}"
        ) from e

    try:
        _lock_stream(stream)
    except OSError as e:
        try:
            stream.close()
        except OSError:
            pass
        raise JobQueueOwnershipError(
            "JobQueue owner lock conflict: another process already owns this ledger "
            f"(ledger={key}, lock_file={lock_path}, purpose={purpose}, "
            "holder_pid_unknown). This process must fail closed: "
            "no ledger write, no job claim, no recovery, no handler execution."
        ) from e

    handle = LedgerOwnership(ledger, lock_path, stream)
    _REGISTRY[key] = handle
    return handle


def release_ownership(state_path) -> bool:
    """释放本进程对某账本的所有权（引用计数归零时真正关闭 fd → OS 释放锁）。"""
    ledger = canonical_ledger_path(state_path)
    key = str(ledger)
    handle = _REGISTRY.get(key)
    if handle is None:
        return False
    handle.release()
    if handle.released and _REGISTRY.get(key) is handle:
        _REGISTRY.pop(key, None)
    return True
