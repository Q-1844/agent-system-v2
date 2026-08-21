# src/core/write_lock.py
# AI-7C §33 —— per-project canonical write serialization primitive。
#
# ProjectStore 既无 per-project mutation lock / CAS / transaction（§33 审计结论）。
# 本模块引入**最小共享原语**：per-project asyncio.Lock，供 canonical creative commit
# 的短临界区串行化（§33：commit 之间不产生 lost update）。
#
# 纪律（§33）：
#   - 临界区短（load + 纯投影 apply + atomic save，无 LLM/Agnes/Planner/video polling）。
#   - 不跨 LLM/Agnes 调用持有。
#   - 既有 production 写（store.update_shot 等）不改——由 §20 active-production preflight
#     在 commit 前阻断活跃生产，避免 commit 与 production 并发写竞态（§33 gate）。
from __future__ import annotations
import asyncio
from typing import Dict

_LOCKS: Dict[str, asyncio.Lock] = {}


def project_write_lock(project_name: str) -> "asyncio.Lock":
    """返回 project 维度的 asyncio.Lock（按需创建）。canonical creative commit 用之
    串行化 load-apply-save 临界区（§33）。"""
    if project_name not in _LOCKS:
        _LOCKS[project_name] = asyncio.Lock()
    return _LOCKS[project_name]


def _clear_for_test() -> None:
    """测试用：清空 lock 字典避免跨 case 泄漏。"""
    _LOCKS.clear()
