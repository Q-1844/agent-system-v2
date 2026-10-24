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
- 供应商真实报错文案尚未实测（按用户要求不做正式测试，等额度真
  耗尽时自然验证）；届时只需扩充 _PROVIDER_MARKERS 一个常量。
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

# 供应商 4xx 文案的额度语义关键词（未见真实样例前的保守集合，自然验证后扩充）。
_PROVIDER_MARKERS = (
    "quota", "配额", "额度", "insufficient", "balance",
    "余额", "exceeded", "too many requests",
)
_GATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def classify_quota_rejection(error) -> bool:
    """判定异常是否为「每日额度耗尽的确定性拒绝」。

    True 意味着：供应商/本地账本明确拒绝，无任务号、无扣费，意图条目
    可以安全回滚并走按天接续。False 一律维持既有 UNKNOWN 对账纪律。
    """
    if error is None:
        return False
    # 结局不明（可能在供应商侧已受理）：绝不当作额度，走 UNKNOWN 对账。
    if error.__class__.__name__ == "VideoSubmissionUncertain":
        return False
    status = getattr(error, "status", None)
    message = str(error)
    # 本地账本预检（src/core/quota.py 既有文案，无 status 属性）。
    if status is None and message.startswith("今日") and "配额已用" in message:
        return True
    # 供应商确定性 4xx 拒绝（客户端已把 408/5xx 归为不确定）+ 额度语义。
    if isinstance(status, int) and 400 <= status < 500 and status != 408:
        lowered = message.lower()
        return any(marker in lowered for marker in _PROVIDER_MARKERS)
    return False


def next_quota_reset_utc(now=None) -> str:
    """下一个额度刷新闸门：北京 00:10，返回 UTC ISO（Z 后缀）。

    now 缺省取当前 UTC；测试注入固定时刻验证边界。
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    beijing = now + dt.timedelta(hours=RESET_OFFSET_HOURS)
    target = beijing.replace(hour=0, minute=RESET_MARGIN_MINUTES,
                             second=0, microsecond=0)
    if beijing >= target:
        target += dt.timedelta(days=1)
    # target 是「北京墙钟」标 UTC 时区的时间，减回偏移即真实 UTC 时刻。
    return (target - dt.timedelta(hours=RESET_OFFSET_HOURS)).strftime(_GATE_FORMAT)


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
    if terminal != QUOTA_TERMINAL and not gate:
        return True, "not quota gated"
    until = parse_gate(gate)
    if until is None:
        return False, f"{QUOTA_TERMINAL} without a valid {GATE_FIELD}; reconcile manually"
    now = now or dt.datetime.now(dt.timezone.utc)
    if now < until:
        return False, f"quota gate closed until {gate}"
    return True, f"quota gate open since {gate}"
