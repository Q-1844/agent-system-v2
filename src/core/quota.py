# src/core/quota.py
# 每日配额账本。视频按秒计，图片按张计，文本按次计。
# 落盘保存，进程重启不丢。跨天自动重置。
from __future__ import annotations
import json, threading, time, math
from datetime import datetime
from zoneinfo import ZoneInfo

def _supplier_day():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
from pathlib import Path

# 默认落盘位置（相对 cwd，即项目根）。可由调用方覆盖。
_DEFAULT_PATH = Path("cache/quota.json")
_DEFAULT_LIMITS = {"video_seconds": 500, "images": 10000, "text_calls": 50000}


class QuotaLedger:
    """每日配额账本。视频按秒计，图片按张计，文本按次计。
    落盘保存，进程重启不丢。跨天自动重置。"""

    def __init__(self, path: Path, limits: dict | None = None):
        self.path = Path(path)
        # C2 长片生产执行额度不限制创作；供应商实际日视频配额独立按天滚动
        self.limits = {**_DEFAULT_LIMITS, **(limits or {})}
        self._lock = threading.Lock()
        self._video_test_policy = None
        self._data = self._load()

    def _load(self) -> dict:
        today = _supplier_day()
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                if d.get("date") == today:
                    return d
            except Exception:
                pass
        fresh = {"date": today, "video_seconds": 0.0, "images": 0, "text_calls": 0}
        if 'd' in locals() and isinstance(d, dict) and d.get('video_test'):
            fresh['video_test'] = d['video_test']
        return fresh

    def _flush(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + '.tmp')
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _rollover(self):
        today = _supplier_day()
        if self._data.get("date") != today:
            simulation = self._data.get('video_test')
            self._data = {"date": today,
                          "video_seconds": 0.0, "images": 0, "text_calls": 0}
            if simulation:
                self._data['video_test'] = simulation

    def configure_video_test(self, policy):
        """A nonce-scoped extra ceiling; never replace/reset the real daily usage."""
        from .quota_simulation import validate_policy
        validate_policy(policy, policy.get('nonce') if isinstance(policy, dict) else None)
        with self._lock:
            existing = self._data.get('video_test')
            if existing and existing.get('policy') != policy:
                raise ValueError('video quota test policy changed across recovery')
            if existing and (type(existing.get('used')) not in (int, float)
                    or not math.isfinite(existing['used']) or existing['used'] < 0
                    or type(existing.get('cycle')) is not int or existing['cycle'] < 0
                    or (existing.get('reset_at') is not None
                        and type(existing['reset_at']) is not int)):
                raise ValueError('corrupt video quota test window')
            self._video_test_policy = dict(policy)
            if not existing:
                self._data['video_test'] = dict(policy=dict(policy), used=0,
                                                reset_at=None, cycle=0)
                self._flush()

    def _test_window(self):
        if self._video_test_policy is None:
            return None
        window = self._data.get('video_test')
        if window and window.get('reset_at') is not None and time.time() >= window['reset_at']:
            window.update(used=0, reset_at=None, cycle=window['cycle'] + 1)
            self._flush()
        return window

    def check_video(self, amount):
        """Return allowed, explanation, dated reset, source under one ledger lock."""
        with self._lock:
            self._rollover()
            used, cap = self._data.get('video_seconds', 0), self.limits['video_seconds']
            if used + amount > cap:
                return False, f'今日 video_seconds 配额已用 {used:.0f}/{cap:.0f}，本次需 {amount:.0f}', None, 'daily'
            window = self._test_window()
            if window and window['used'] + amount > window['policy']['video_seconds']:
                if window['reset_at'] is None:
                    window['reset_at'] = math.ceil(time.time()) + window['policy']['wait_minutes'] * 60
                    self._flush()
                from datetime import timezone
                gate = datetime.fromtimestamp(window['reset_at'], timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                return False, (f"测试窗口视频额度已用 {window['used']}/{window['policy']['video_seconds']}，"
                               f"本次需 {amount}"), gate, 'test_window'
            return True, '', None, None

    def video_test_snapshot(self):
        with self._lock:
            return json.loads(json.dumps(self._data.get('video_test')))

    def can(self, kind: str, amount: float = 1) -> tuple[bool, str]:
        if kind == 'video_seconds':
            allowed, reason, _, _ = self.check_video(amount)
            return allowed, reason
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
            if kind == 'video_seconds':
                window = self._test_window()
                if window:
                    window['used'] += amount
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
