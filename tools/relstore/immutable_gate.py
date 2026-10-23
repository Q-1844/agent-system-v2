# tools/relstore/immutable_gate.py
# G3-B 修正：Immutable Releases 状态不能由匿名 401 推断。
# 评估口径（get_immutable_releases 返回）：
#   http=200 + enabled=true  -> enabled
#   http=200 + enabled=false -> disabled
#   其余（401/403/404/异常）  -> unknown
# 纪律：unknown 时，若所有者已在网页 Settings 亲自确认（attested），可采信该裁定
# （00 修正文件明示的两条合法证据路径之一：所有者网页 Settings 或 Administration:read）；
# 既无 API 确认也无所有者裁定 → 远端写入前必须停机。
from __future__ import annotations

from .errors import ImmutableStateUnknown


def evaluate(immutable_result: dict | None) -> str:
    """把 get_immutable_releases() 的结果归一为 enabled/disabled/unknown。"""
    if not isinstance(immutable_result, dict):
        return "unknown"
    if immutable_result.get("http") == 200:
        raw = immutable_result.get("raw")
        if isinstance(raw, dict) and isinstance(raw.get("enabled"), bool):
            return "enabled" if raw["enabled"] else "disabled"
    return "unknown"


def assert_writable(immutable_result: dict | None,
                    attested: str | None = None) -> str:
    """远端写入前的状态闸。

    - API 能确认（200）→ 用 API 结果；
    - API unknown（401/403/404/None）且 attested ∈ {enabled, disabled}
      → 采信所有者 Settings 裁定（证据记录调用方负责）；
    - 两者皆无 → ImmutableStateUnknown（停机）。
    enabled 时调用方必须走 draft 全传完→publish 顺序。
    """
    state = evaluate(immutable_result)
    if state == "unknown":
        if attested in ("enabled", "disabled"):
            return attested  # 所有者 Settings 裁定（合法证据路径，来源需记录）
        raise ImmutableStateUnknown(
            "Immutable Releases 状态 UNKNOWN（匿名/当前凭据不可见）且无所有者"
            " Settings 裁定——写入前须复核")
    return state
