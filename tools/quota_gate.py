#!/usr/bin/env python3
"""每日视频额度闸门（按天接续）。

供应商视频额度按天刷新（用户口径：500s/天，北京零点刷新；图片/文本额度
实际不可耗尽）。额度耗尽不是生产失败，而是「可预期的干净暂停」：

  提交被明确拒绝 → 意图条目安全回滚（无任务号、无扣费）→ 检查点落
  terminal_status=quota_exhausted + quota_resume_after=<下一个闸门时刻>
  → A 自派发与 B 巡检共用 auto_resume_allowed 的日历闸门判定 →
  闸门开启（下一个北京 00:10）后由巡检自动拉起下一跳。

判定纪律（宁保守不冒进）：
- 只有「确定被拒」才当额度处理：本地账本预检文案、或供应商 4xx
  （非 408）+ 额度语义关键词。VideoSubmissionUncertain（结局不明）
  一律走既有 UNKNOWN 对账纪律，绝不凭猜测回滚收据。
- 错误分类与 reset 时间解析统一由 src/core/provider_errors.py 提供；
  r46 实测的「已达到 API 用量上限」与普通 RPM 429 分开处理。
- 闸门时刻按北京 00:10 计算（UTC+8 固定偏移，中国无夏令时）；若实测
  供应商按 UTC 零点刷新，改 RESET_OFFSET_HOURS=0 即可。

本模块只依赖标准库，tools 层共用（driver / resume_dispatch / patrol），
不反向依赖 src 运行时模块。
"""
from __future__ import annotations

import datetime as dt

# 检查点字段名（与 wrapper.persist 的 CAS 字段合并约定一致）
QUOTA_TERMINAL = "quota_exhausted"
GATE_FIELD = "quota_resume_after"

# 北京时间固定偏移（无夏令时）；边界留 10 分钟余量避开零点抖动。
RESET_OFFSET_HOURS = 8
RESET_MARGIN_MINUTES = 10

# HTTP client, queue and patrol share one classification/reset policy.
from src.core.provider_errors import daily_quota_rejection, quota_resume_after
_GATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def classify_quota_rejection(error) -> bool:
    return daily_quota_rejection(error)


def next_quota_reset_utc(now=None, error=None) -> str:
    return quota_resume_after(error, now)


def parse_gate(value):
    """解析闸门时间戳为 aware UTC datetime；非法返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return dt.datetime.strptime(value.strip(), _GATE_FORMAT).replace(
            tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def gate_status(doc, now=None):
    """检查点日历闸门判定：返回 (open, reason)。

    非额度暂停（无闸门字段）恒开；额度暂停必须带合法闸门时间戳——
    缺失/畸形按「留人工」处理，绝不盲目派发。
    """
    doc = doc or {}
    gate = doc.get(GATE_FIELD)
    terminal = doc.get("terminal_status")
    if terminal != QUOTA_TERMINAL and doc.get("phase") != "WAITING_QUOTA" and not gate:
        return True, "not quota gated"
    until = parse_gate(gate)
    if until is None:
        return False, f"{QUOTA_TERMINAL} without a valid {GATE_FIELD}; reconcile manually"
    now = now or dt.datetime.now(dt.timezone.utc)
    if now < until:
        return False, f"quota gate closed until {gate}"
    return True, f"quota gate open since {gate}"
