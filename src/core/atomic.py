# src/core/atomic.py
# 原子 JSON 写入：先写临时文件再 rename，防止写入中途崩溃导致 JSON 半截损坏。
# Windows 下 os.replace 偶发 PermissionError（文件被短暂占用，如杀软扫描/IO 延迟），重试几次。
# 由 src/core/store.py 与 src/revision/* 共用——单一来源，禁止裸写 JSON（AI-6A §十四）。
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def atomic_write_json(path, data: Any) -> None:
    """原子写：先写临时文件再 rename。path 可为 str|Path。data 须可 json 序列化。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        for i in range(6):
            try:
                os.replace(tmp, path)          # Windows 上 os.replace 也是原子的
                break
            except PermissionError:
                if i == 5:
                    raise
                time.sleep(0.05 * (i + 1))
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise
