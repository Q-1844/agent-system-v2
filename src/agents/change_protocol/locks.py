# src/agents/change_protocol/locks.py
# AI-7B per-ChangeSession 锁（§89，镜像 workspace_lock）。
#
# 防两个 resume/approve 并发编译同一 plan。**不跨 LLM 调用持有**（AI-7B proposal-only）。
#
# 为何独立成模块（AI-7D D3）：`session_lock` 原定义在 `routers/agents.py`，但 `src/agents/`
# 包纪律禁止 import router（runtime.py 头部「agents 包内绝不 import router」）。D3 把
# runtime_control 接进 unified_turn（src/agents/runtime.py）需对 approve/reject/replan/
# resume_session 持同一 session_lock 防并发编译。故搬到此中性位置：router 侧 + agents
# 运行时侧均可 import（包内，纪律合规）。
from __future__ import annotations
import asyncio


# 进程级单例 dict：key = "{project}/{session_id}" -> asyncio.Lock。
# 懒构造（首次调用时在 running loop 内创建，避免无 loop 构造锁的兼容问题）。
_SESSION_LOCKS: dict = {}


def session_lock(project_name: str, session_id: str) -> "asyncio.Lock":
    key = f"{project_name}/{session_id}"
    if key not in _SESSION_LOCKS:
        _SESSION_LOCKS[key] = asyncio.Lock()
    return _SESSION_LOCKS[key]
