# src/agents/skill_system/__init__.py
"""Skill System V1（40.md）：Canonical Skill Registry + Agent-specific
Bindings / Views + Progressive Disclosure。

公共入口：
- registry.get_skill_registry() / reset_skill_registry()
- runtime.get_skill_runtime() / reset_skill_runtime()
- tools.SKILL_TOOLS / tools.make_skill_executors(agent_id) /
  tools.activation_hint(agent_id, content)（各 Agent 文件接线用）
"""
from .registry import get_skill_registry, reset_skill_registry  # noqa: F401
from .runtime import get_skill_runtime, reset_skill_runtime    # noqa: F401
