# src/core/timing_ledger.py
# Phase50 A5（PHASE50_A §7）：生成链计时账本（观测件，零行为影响）。
#
# 目的：把「提交→受理→服务端完成→下载→审查」各事件以 UTC 时间戳 + 同机单调钟
# 追加写入 JSONL，供耗时归因（墙钟/累计/远端等待）与并发区间计算。
#
# 边界（如实记录，不编造）：
#   - provider_started_at / provider_completed_at 服务端不提供 → 缺失，远端等待只能
#     以 submitted→completed 合并报告，不拆「排队几秒、推理几秒」。
#   - 账本写失败绝不影响生成主链（所有调用点 fail-open）。
#   - 频率限制（RPM/429）与在途（outstanding）是两个维度；本账本只记事实事件，
#     不做限流决策。
from __future__ import annotations
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("TimingLedger")

_DEFAULT_PATH = Path("cache") / "p50_timing_ledger.jsonl"

# 同机单调时钟起点（进程内相对值，仅用于精确差值，不跨进程比较）
_MONO_ORIGIN = time.monotonic()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def ledger_path() -> Path:
    raw = (os.environ.get("AGNES_TIMING_LEDGER") or "").strip()
    return Path(raw) if raw else _DEFAULT_PATH


def record(event: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """追加一条事件。event 如 video_ready / video_submitted / video_completed /
    video_downloaded / review_recorded / concurrency_probe。失败仅告警（fail-open）。"""
    try:
        entry: Dict[str, Any] = {
            "event": event,
            "utc": _utc_now_iso(),
            "epoch": round(time.time(), 3),
            "mono": round(time.monotonic() - _MONO_ORIGIN, 3),
        }
        for k, v in fields.items():
            if v is not None:
                entry[k] = v
        p = ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False)
        # 简单追加写；单行 <64KB，Windows 文本追加原子性足够（观测件）。
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return entry
    except Exception as e:   # 绝不影响生成主链
        log.warning("timing ledger 写入失败（忽略）: %s", e)
        return None


def read_events(path: Optional[Path] = None) -> list:
    """读回全部事件（报告/计算用；文件缺失返回空）。"""
    p = Path(path) if path else ledger_path()
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out
