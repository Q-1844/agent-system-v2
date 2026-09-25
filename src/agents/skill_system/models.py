# src/agents/skill_system/models.py
"""Skill System V1 领域模型（40.md §四）。

两个核心对象：

- SkillPackage：Skill 本体（manifest + SKILL.md + references/）。主体是 Markdown
  自然语言工作方法，manifest 只放机器需要的少量结构化元数据——不过度 schema 化。
- AgentSkillBinding：某一个 Agent 与某一个 Skill 的关系（是否可见/角色/路由
  优先级/权威层级/激活策略/role-specific 资源）。重要度是 Agent×Skill 的关系
  属性，不是 Skill 的绝对属性（40.md §四）。

权威边界（40.md §五/§十一，硬约束，注册/加载时不变）：
- routing_priority 只用于 skill selection / ranking（这个 Skill 多容易被选择）；
- authority_tier 只用于 instruction conflict resolution（Skill 冲突时谁更权威），
  且任何 tier 都不得覆盖 System Prompt / Agent ownership / explicit user
  instruction / canonical truth / OCC / permissions / approval / truth gates /
  production safety invariants——Skill 只教 Agent 使用既有能力，不授予新权限。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# ============ 枚举（V1 刻意少量、稳定）============

# 类别（40.md §六：不要过度设计；source/trust 与 category 分离）
SKILL_CATEGORIES = ("workflow", "domain", "situation", "review_recovery")

# source = 包的来源（builtin_core=随仓库定版的黄金方法 / builtin=内置扩展 /
# user=用户上传，未来兼容，默认最低信任）
SKILL_SOURCES = ("builtin_core", "builtin", "user")

# authority_tier = 指令冲突解析层级（40.md §五）
AUTHORITY_TIERS = ("builtin_core", "builtin", "user")

# activation_mode：hinted=系统会在任务命中 trigger 时给确定性提示；
# manual=完全由 Agent 自主判断（V1 两种都在 L1 catalog 可见，加载均由 Agent 主动发起）
ACTIVATION_MODES = ("hinted", "manual")

# 合法 agent_id（与 src/agents/registry.py 的正式名册一致；V1 只给 5 个
# tool-loop Agent 绑定，但名册保持一致以支持后续扩展）
VALID_AGENT_IDS = (
    "producer", "script_writer", "character_designer", "scene_designer",
    "director", "production_planner", "shot_producer", "continuity_reviewer",
    "editor",
)

_SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_ROLE_RESOURCE_RE = re.compile(r"^references/[A-Za-z0-9_.-]+\.md$")


class SkillValidationError(ValueError):
    """manifest/SKILL.md 校验失败。Registry 对 invalid package fail closed：
    不注册、不可见、不可加载（40.md §十五 Phase A）。"""


@dataclass
class AgentSkillBinding:
    """Agent × Skill 关系（40.md §四）。"""
    agent_id: str
    skill_id: str
    enabled: bool = True
    role: str = "executor"               # coordinator / primary_executor / contributor / conditional / executor
    routing_priority: int = 50           # 0-100，越高越优先被选择（selection ranking）
    authority_tier: str = "builtin_core"  # 指令冲突解析（越低越受限：user 永不覆盖内置）
    activation_mode: str = "hinted"       # hinted / manual
    role_resource: Optional[str] = None   # references/<agent>.md，缺省从 agent_id 派生

    @staticmethod
    def from_dict(d: dict, skill_id: str) -> "AgentSkillBinding":
        agent_id = str(d.get("agent_id") or "").strip()
        if agent_id not in VALID_AGENT_IDS:
            raise SkillValidationError(
                f"binding.agent_id 非法: {agent_id!r}（合法: {VALID_AGENT_IDS}）")
        if agent_id == skill_id:
            raise SkillValidationError("binding.agent_id 不得等于 skill_id")
        enabled = bool(d.get("enabled", True))
        role = str(d.get("role") or "executor").strip()
        if not role:
            raise SkillValidationError("binding.role 不能为空")
        try:
            routing_priority = int(d.get("routing_priority", 50))
        except (TypeError, ValueError):
            raise SkillValidationError(
                f"binding.routing_priority 必须是整数: {d.get('routing_priority')!r}")
        if not (0 <= routing_priority <= 100):
            raise SkillValidationError(
                f"binding.routing_priority 越界(0-100): {routing_priority}")
        authority_tier = str(d.get("authority_tier") or "builtin_core").strip()
        if authority_tier not in AUTHORITY_TIERS:
            raise SkillValidationError(
                f"binding.authority_tier 非法: {authority_tier!r}")
        activation_mode = str(d.get("activation_mode") or "hinted").strip()
        if activation_mode not in ACTIVATION_MODES:
            raise SkillValidationError(
                f"binding.activation_mode 非法: {activation_mode!r}")
        role_resource = d.get("role_resource")
        if role_resource is not None:
            role_resource = str(role_resource).strip()
            if not _ROLE_RESOURCE_RE.match(role_resource):
                raise SkillValidationError(
                    f"binding.role_resource 非法（须形如 references/<agent>.md）: "
                    f"{role_resource!r}")
        return AgentSkillBinding(
            agent_id=agent_id, skill_id=skill_id, enabled=enabled, role=role,
            routing_priority=routing_priority, authority_tier=authority_tier,
            activation_mode=activation_mode, role_resource=role_resource,
        )

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id, "skill_id": self.skill_id,
            "enabled": self.enabled, "role": self.role,
            "routing_priority": self.routing_priority,
            "authority_tier": self.authority_tier,
            "activation_mode": self.activation_mode,
            "role_resource": self.role_resource,
        }


@dataclass
class SkillPackage:
    """Skill 本体（40.md §四）。manifest 结构化元数据 + Markdown 工作方法主体。"""
    skill_id: str
    version: str
    name: str
    description: str
    category: str = "workflow"
    source: str = "builtin_core"
    status: str = "active"                     # active / disabled（包级开关）
    triggers: List[str] = field(default_factory=list)   # 确定性 discovery 关键词
    applicability: str = ""                    # 适用情境一句话（进 L1 catalog）
    required_capabilities: dict = field(default_factory=dict)  # agent_id → [tool 名]
    depends_on: List[str] = field(default_factory=list)  # 依赖的其他 skill_id
    bindings: List[AgentSkillBinding] = field(default_factory=list)
    package_dir: Optional[Path] = None
    _validation_errors: List[str] = field(default_factory=list)

    # ---------- manifest 加载与校验（fail closed）----------

    @staticmethod
    def from_manifest_file(manifest_path: Path) -> "SkillPackage":
        """从 skill.json 构建 + 校验。任何校验失败抛 SkillValidationError，
        调用方（Registry）负责 fail closed：invalid package 不注册。"""
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise SkillValidationError(f"manifest 不是合法 JSON: {e}")
        if not isinstance(raw, dict):
            raise SkillValidationError("manifest 顶层必须是对象")
        errors: List[str] = []
        skill_id = str(raw.get("skill_id") or "").strip()
        if not _SKILL_ID_RE.match(skill_id):
            errors.append(f"skill_id 非法: {skill_id!r}")
        version = str(raw.get("version") or "").strip()
        if not _VERSION_RE.match(version):
            errors.append(f"version 须为 x.y.z: {version!r}")
        name = str(raw.get("name") or "").strip()
        if not name:
            errors.append("name 不能为空")
        description = str(raw.get("description") or "").strip()
        if not description:
            errors.append("description 不能为空")
        category = str(raw.get("category") or "workflow").strip()
        if category not in SKILL_CATEGORIES:
            errors.append(f"category 非法: {category!r}")
        source = str(raw.get("source") or "builtin_core").strip()
        if source not in SKILL_SOURCES:
            errors.append(f"source 非法: {source!r}")
        status = str(raw.get("status") or "active").strip()
        if status not in ("active", "disabled"):
            errors.append(f"status 非法: {status!r}")
        triggers = raw.get("triggers") or []
        if not isinstance(triggers, list) or \
                any(not isinstance(t, str) or not t.strip() for t in triggers):
            errors.append("triggers 必须是非空字符串数组")
        else:
            triggers = [t.strip().lower() for t in triggers if t.strip()]
        applicability = str(raw.get("applicability") or "").strip()
        rc = raw.get("required_capabilities") or {}
        if not isinstance(rc, dict) or any(
                k not in VALID_AGENT_IDS or not isinstance(v, list)
                or any(not isinstance(x, str) or not x.strip() for x in v)
                for k, v in rc.items()):
            errors.append("required_capabilities 须为 {agent_id: [tool_name]}")
        else:
            rc = {k: [str(x).strip() for x in v] for k, v in rc.items()}
        depends_on = raw.get("depends_on") or []
        if not isinstance(depends_on, list) or \
                any(not isinstance(d, str) or not _SKILL_ID_RE.match(d)
                    for d in depends_on):
            errors.append("depends_on 必须是合法 skill_id 数组")
        bindings_raw = raw.get("bindings") or []
        if not isinstance(bindings_raw, list) or not bindings_raw:
            errors.append("bindings 必须是非空数组（至少一个 Agent 绑定）")
        bindings: List[AgentSkillBinding] = []
        if isinstance(bindings_raw, list):
            for i, b in enumerate(bindings_raw):
                if not isinstance(b, dict):
                    errors.append(f"bindings[{i}] 必须是对象")
                    continue
                try:
                    bindings.append(AgentSkillBinding.from_dict(b, skill_id))
                except SkillValidationError as e:
                    errors.append(f"bindings[{i}]: {e}")
        if errors:
            raise SkillValidationError("; ".join(errors))
        pkg = SkillPackage(
            skill_id=skill_id, version=version, name=name, description=description,
            category=category, source=source, status=status, triggers=triggers,
            applicability=applicability, required_capabilities=rc,
            depends_on=[str(d) for d in depends_on], bindings=bindings,
            package_dir=manifest_path.parent,
        )
        pkg.validate_contents()
        return pkg

    def validate_contents(self) -> None:
        """SKILL.md / role resource 文件存在性校验（fail closed）。"""
        assert self.package_dir is not None
        skill_md = self.package_dir / "SKILL.md"
        if not skill_md.is_file():
            raise SkillValidationError(f"缺少 SKILL.md: {skill_md}")
        text = skill_md.read_text(encoding="utf-8").strip()
        if len(text) < 40:
            raise SkillValidationError(f"SKILL.md 内容过短（<40 字符）: {self.skill_id}")
        for b in self.bindings:
            if b.role_resource is not None:
                # 显式声明的 role_resource 必须存在（fail closed）；派生默认
                # references/<agent>.md 是可选的（不存在即无 role 专属文件）。
                p = self.package_dir / b.role_resource
                if not p.is_file():
                    raise SkillValidationError(
                        f"binding 声明的 role_resource 不存在: {p}")

    # ---------- 文件访问 ----------

    def skill_md_text(self) -> str:
        assert self.package_dir is not None
        return (self.package_dir / "SKILL.md").read_text(encoding="utf-8")

    def role_resource_path(self, binding: AgentSkillBinding) -> Optional[Path]:
        """binding 的 role-specific 资源路径（无则 None）。缺省从 agent_id 派生：
        references/<agent_id>.md——存在才算数（不强制每个 Agent 都有专属文件）。"""
        assert self.package_dir is not None
        rel = binding.role_resource or f"references/{binding.agent_id}.md"
        p = self.package_dir / rel
        return p if p.is_file() else None

    def list_resources(self) -> List[str]:
        """references/ 下全部资源（L3 清单），相对包目录路径，排序稳定。"""
        assert self.package_dir is not None
        ref_dir = self.package_dir / "references"
        if not ref_dir.is_dir():
            return []
        return sorted(
            f"references/{p.name}" for p in ref_dir.iterdir()
            if p.is_file() and p.suffix == ".md")

    def to_catalog_dict(self) -> dict:
        """L1 catalog 元数据（Agent 每轮只看到这些）。"""
        return {
            "skill_id": self.skill_id, "version": self.version,
            "name": self.name, "description": self.description,
            "category": self.category, "applicability": self.applicability,
            "triggers": self.triggers, "depends_on": self.depends_on,
        }

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id, "version": self.version, "name": self.name,
            "description": self.description, "category": self.category,
            "source": self.source, "status": self.status,
            "triggers": self.triggers, "applicability": self.applicability,
            "required_capabilities": self.required_capabilities,
            "depends_on": self.depends_on,
            "bindings": [b.to_dict() for b in self.bindings],
        }
