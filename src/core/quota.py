# src/core/quota.py
# 每日配额账本。视频按秒计，图片按张计，文本按次计。
# 落盘保存，进程重启不丢。跨天自动重置。
from __future__ import annotations
import json, threading
from datetime import date
from pathlib import Path

# 默认落盘位置（相对 cwd，即项目根）。可由调用方覆盖。
_DEFAULT_PATH = Path("cache/quota.json")
_DEFAULT_LIMITS = {"video_seconds": 300, "images": 500, "text_calls": 2000}


class QuotaLedger:
    """每日配额账本。视频按秒计，图片按张计，文本按次计。
    落盘保存，进程重启不丢。跨天自动重置。"""

    def __init__(self, path: Path, limits: dict | None = None):
        self.path = Path(path)
        # 免费用户官方未公布确切数值，这里给保守默认值，可在 config.yaml 覆盖
        self.limits = {**_DEFAULT_LIMITS, **(limits or {})}
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        today = date.today().isoformat()
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                if d.get("date") == today:
                    return d
            except Exception:
                pass
        return {"date": today, "video_seconds": 0.0, "images": 0, "text_calls": 0}

    def _flush(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    def _rollover(self):
        if self._data.get("date") != date.today().isoformat():
            self._data = {"date": date.today().isoformat(),
                          "video_seconds": 0.0, "images": 0, "text_calls": 0}

    def can(self, kind: str, amount: float = 1) -> tuple[bool, str]:
        with self._lock:
            self._rollover()
            used, cap = self._data.get(kind, 0), self.limits.get(kind, float("inf"))
            if used + amount > cap:
                return False, f"今日 {kind} 配额已用 {used:.0f}/{cap:.0f}，本次需 {amount:.0f}"
            return True, ""

    def consume(self, kind: str, amount: float = 1):
        with self._lock:
            self._rollover()
            self._data[kind] = self._data.get(kind, 0) + amount
            self._flush()

    def snapshot(self) -> dict:
        with self._lock:
            self._rollover()
            return {k: {"used": self._data.get(k, 0), "limit": v}
                    for k, v in self.limits.items()}

    # ---- 进程级单例：供 api_server 的 /api/system/limits 与 AgnesClient 共享同一实例 ----
    _default: "QuotaLedger | None" = None

    @classmethod
    def get_default(cls, path: Path | str | None = None,
                    limits: dict | None = None) -> "QuotaLedger":
        """进程级单例。首次调用时以 path/limits 创建；后续调用忽略参数，返回同一实例。
        这样 api_server 启动时配置一次，AgnesClient 与 /api/system/limits 拿到的是同一个账本。"""
        if cls._default is None:
            cls._default = cls(Path(path) if path else _DEFAULT_PATH, limits)
        return cls._default
