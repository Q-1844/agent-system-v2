# src/agents/turn_registry.py
# 中性「活跃流式 turn」注册表（Producer V1 Phase 1 · §5.1）。
#
# 背景：agents 包禁止 import router（仓库铁律），而 Producer Bridge 需要与 router
# 共享「同 session 不可并发 turn」的互斥——否则用户直开某 Specialist 聊天与 Bridge
# 委派可能并发驱动同一 session，互相覆盖。本模块把原 routers/agents.py 的
# _ACTIVE_TURN_EVENTS 抽取为中性模块（仿 change_protocol/locks.py 先例），router
# 与 Producer Bridge 共用同一进程内 dict。
#
# 行为契约（与原 _ACTIVE_TURN_EVENTS 逐字节等价）：
#   - 进程内、非持久（后端重启即清空，turn 本就不跨重启恢复）；
#   - key = f"{project}::{sid}"；
#   - acquire：未占用→登记并返回 True；已占用→返回 False（调用方据此 409/回执 busy）；
#   - pop：无条件移除（成功/失败/停止/断开都要释放）；
#   - get：供 cancel 端点查询当前 cancel_event（协作式停止）。
#
# router 侧保留 _ACTIVE_TURN_EVENTS / _turn_key 名字作为本模块的别名（同一 dict
# 对象），既有测试（tests/test_ai_7d_r_turn_background.py 直接操作该 dict）零改动。
from __future__ import annotations
from typing import Optional

# 进程内活跃 turn 注册表：key -> cancel_event（asyncio.Event）。
_ACTIVE: dict = {}


def key(project: str, sid: str) -> str:
    return f"{project}::{sid}"


def acquire(project: str, sid: str, cancel_event) -> bool:
    """尝试占用 (project, sid) 的 turn 槽位。返回 True=本次新占用；False=已占用。
    单 asyncio 事件循环内 check-then-set 等价原子（与原 router 用法一致）。"""
    k = key(project, sid)
    if k in _ACTIVE:
        return False
    _ACTIVE[k] = cancel_event
    return True


def get(project: str, sid: str):
    """取当前 cancel_event（无则 None）。cancel 端点用。"""
    return _ACTIVE.get(key(project, sid))


def pop(project: str, sid: str) -> None:
    """释放 turn 槽位（幂等）。finally 路径无条件调用。"""
    _ACTIVE.pop(key(project, sid), None)


def is_active(project: str, sid: str) -> bool:
    return key(project, sid) in _ACTIVE


def clear() -> None:
    """测试用：清空全部活跃 turn（重置单例态）。"""
    _ACTIVE.clear()
