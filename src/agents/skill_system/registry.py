# src/agents/skill_system/registry.py
"""Canonical Skill Registry（40.md §三）。

- Shared Skill Registry → Skill Packages → AgentSkillBinding → Agent-specific
  Skill Catalog / View。同一个 Skill 只有唯一 canonical package，绝不复制。
- V1 源：`src/agents/skill_system/builtin/`（仓库内置）。user-upload 未来在此层
  增加第二个 source 目录 + trust 校验（40.md §十四：默认最低 tier、不得提升权限）。
- fail closed：manifest 校验失败的包【不注册、不可见、不可加载】；错误汇总进
  registry_errors() 供测试与诊断。
- 进程级单例 + mtime 缓存：catalog 渲染每 turn 便宜且确定（40.md §七）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import AgentSkillBinding, SkillPackage, SkillValidationError

log = logging.getLogger("agents.skills.registry")

BUILTIN_DIR = Path(__file__).parent / "builtin"
# 候选包目录（Phase49）：与 builtin 并列的第二 source；候选身份独立（source=builtin
# + 版本独立），不覆盖、不替换任何 builtin 正式包。目录不存在时行为与旧版完全一致。
CANDIDATE_DIR = Path(__file__).parent / "candidate"


class SkillRegistry:
    def __init__(self, source_dirs: Optional[List[Path]] = None):
        # 显式传入 source_dirs（测试用临时目录）；缺省扫描 builtin/。
        # 缺省时若存在 candidate/ 则并入扫描（Phase49 候选接入点）；显式注入
        # source_dirs 的调用方（测试隔离）不自动并入，保持沙盒纯净。
        dirs = [Path(p) for p in (source_dirs or [BUILTIN_DIR])]
        if not source_dirs and CANDIDATE_DIR.is_dir() and CANDIDATE_DIR not in dirs:
            dirs.append(CANDIDATE_DIR)
        self._source_dirs = dirs
        self._skills: Dict[str, SkillPackage] = {}
        self._errors: Dict[str, str] = {}
        self._loaded_sig: Optional[tuple] = None
        self.reload()

    # ---------- 加载 ----------

    def reload(self) -> None:
        """扫描 source 目录并重建注册表。无效包 fail closed（错误记录不注册）。
        mtime 签名缓存：未变化时 `_ensure_fresh` 零成本。"""
        skills: Dict[str, SkillPackage] = {}
        errors: Dict[str, str] = {}
        for src in self._source_dirs:
            if not src.is_dir():
                continue
            for manifest in sorted(src.glob("*/skill.json")):
                pkg_dir = manifest.parent
                try:
                    pkg = SkillPackage.from_manifest_file(manifest)
                except SkillValidationError as e:
                    errors[pkg_dir.name] = str(e)
                    log.warning("skill 包校验失败（不注册）: %s -> %s", pkg_dir.name, e)
                    continue
                if pkg.skill_id in skills:
                    # 冲突 fail closed：后加载的同 id 包拒绝，先注册者胜出
                    errors[pkg.skill_id] = f"重复 skill_id（目录 {pkg_dir.name} 被拒绝）"
                    log.warning("重复 skill_id，拒绝后到包: %s (%s)", pkg.skill_id, pkg_dir)
                    continue
                skills[pkg.skill_id] = pkg
        self._skills = skills
        self._errors = errors
        self._loaded_sig = self._mtime_sig()

    def _mtime_sig(self) -> tuple:
        sig: list = []
        for src in self._source_dirs:
            for manifest in sorted(src.glob("*/skill.json")):
                try:
                    sig.append((str(manifest), manifest.stat().st_mtime))
                except OSError:
                    continue
        return tuple(sig)

    def _ensure_fresh(self) -> None:
        if self._loaded_sig != self._mtime_sig():
            self.reload()

    # ---------- 查询 ----------

    def skills(self) -> Dict[str, SkillPackage]:
        self._ensure_fresh()
        return dict(self._skills)

    def get(self, skill_id: str) -> Optional[SkillPackage]:
        self._ensure_fresh()
        return self._skills.get(skill_id)

    def registry_errors(self) -> Dict[str, str]:
        self._ensure_fresh()
        return dict(self._errors)

    # ---------- binding / per-agent view（40.md §三/§四）----------

    def binding_for(self, agent_id: str, skill_id: str) -> Optional[AgentSkillBinding]:
        """Agent×Skill 关系。未绑定 / binding disabled / 包 disabled → None
        （progressive disclosure 的可见性边界）。"""
        self._ensure_fresh()
        pkg = self._skills.get(skill_id)
        if pkg is None or pkg.status != "active":
            return None
        for b in pkg.bindings:
            if b.agent_id == agent_id and b.enabled:
                return b
        return None

    def view_for(self, agent_id: str) -> List[Tuple[SkillPackage, AgentSkillBinding]]:
        """Agent-specific Skill Catalog（L1 的数据源）：仅含 enabled 包 +
        enabled binding，按 routing_priority 降序（40.md §五：routing 只管选择
        排序，与 authority 无关）。"""
        self._ensure_fresh()
        out: List[Tuple[SkillPackage, AgentSkillBinding]] = []
        for pkg in self._skills.values():
            if pkg.status != "active":
                continue
            for b in pkg.bindings:
                if b.agent_id == agent_id and b.enabled:
                    out.append((pkg, b))
                    break
        out.sort(key=lambda t: (-t[1].routing_priority, t[0].skill_id))
        return out


# ============ 进程级单例 ============
_REGISTRY: Optional[SkillRegistry] = None


def get_skill_registry() -> SkillRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = SkillRegistry()
    return _REGISTRY


def reset_skill_registry() -> None:
    """测试用：清空单例（下次 get 重建，测试可注入临时 source 目录）。"""
    global _REGISTRY
    _REGISTRY = None
