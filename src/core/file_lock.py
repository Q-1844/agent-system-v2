"""Reentrant local-process and cross-process lock for short file transactions.

All participants must use the same lock path. Never hold this across an await,
model call or download. The lock file is kept in place (unlinking breaks locks).
"""
from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time

_registry_guard = threading.Lock()
_locks = {}
_held = threading.local()


@contextmanager
def file_transaction(path: Path, *, timeout: float = 30.0):
    path = Path(path).resolve()
    key = str(path)
    with _registry_guard:
        lock = _locks.setdefault(key, threading.RLock())
    if not lock.acquire(timeout=timeout):
        raise TimeoutError(f"file transaction busy: {path.name}")
    held = getattr(_held, "paths", None)
    if held is None:
        held = _held.paths = set()
    stream = None
    acquired = False
    nested = key in held
    try:
        if not nested:
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = path.open("a+b")
            if os.name == "nt" and path.stat().st_size == 0:
                stream.write(b"\0")
                stream.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    import errno
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"file transaction busy: {path.name}") from exc
                    time.sleep(0.01)
            held.add(key)
        yield
    finally:
        try:
            if acquired:
                held.discard(key)
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            if stream is not None:
                stream.close()
            lock.release()
