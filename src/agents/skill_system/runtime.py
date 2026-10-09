# src/agents/skill_system/runtime.py
"""Skill Runtime（40.md §七 Progressive Disclosure / §十三 可观察性）。

分层加载语义：

- L0 Agent Base System：各 Agent system prompt 的身份/权限/真相纪律（不动）。
  `catalog_block()` 追加的通用 Skill Policy 是 L0 的一小段。
- L1 Catalog Metadata：Agent 每轮只看到自己可用 Skill 的少量元数据
  （id/version/name/一句话描述/触发词），由 `catalog_block()` 渲染、
  `_build_system` 注入。
- L2 Selected Skill Instructions：判断相关后经 `skill.load` 工具加载 SKILL.md
  主体 + 该 Agent 的 role resource。
- L3 Supporting Resources：执行需要时经 `skill.read_resource` 加载其余
  references/（其他 Agent 的 role 文件、examples、failure guide）。

Discovery 三通道（廉价、确定、可追踪）：
1. L1 catalog 常驻 system（每轮可见）；
2. turn 入口确定性 trigger 匹配 → `activation_hint()` 拼进 user 消息（不落盘）；
3. Agent 在 loop 中随时 skill.list 查看目录。

Trace：`output/agents/skills/trace.jsonl`（append-only 结构化事件）+
turn 收口 `collect_and_reset()` 供 assistant meta.skills（与既有 trace/receipts
同层）。不持久化模型私有推理，只记录决策证据（40.md §十三）。

权限边界（硬约束，40.md §五/§十一）：任何 tier 的 skill 都不覆盖 System Prompt /
ownership / 用户显式指令 / canonical truth / OCC / 审批 / truth gate；skill 只教
Agent 使用既有工具面，capability 对账缺失时明确 fail/delegate。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from .models import AgentSkillBinding, SkillPackage
from .registry import SkillRegistry, get_skill_registry, BUILTIN_DIR

# ============ L0/L1 文字（catalog_block 渲染）============

_SKILL_POLICY = """【生产技能（Skill）系统】
针对特定复杂生产任务的标准工作方法以"生产技能"形式存在，不在上面的职责说明里：
- 判断当前任务与技能目录中某项相关时，【先调用 skill.load(skill_id) 获取完整工作方法，再开始执行】；返回内容包含共享工作流与你在该技能中的角色专属指令。
- skill.load / skill.list / skill.read_resource 都是只读工具，可与只读工具并行调用；加载技能本身不消耗生产额度。
- 技能只教方法、不授予新权限：与你职责硬约束（ownership/OCC/审批/truth gate）冲突时以职责硬约束为准。"""

_CATALOG_HEADER = """【你的技能目录】（按选择优先级排列；仅在与任务相关时加载）"""


def _catalog_lines(view) -> str:
    if not view:
        return ""
    lines: List[str] = [_CATALOG_HEADER]
    for i, (pkg, b) in enumerate(view, 1):
        entry = (f"{i}. {pkg.skill_id} v{pkg.version}「{pkg.name}」—— {b.role}"
                 f"（routing_priority={b.routing_priority}）")
        if pkg.applicability:
            entry += f"\n   适用：{pkg.applicability}"
        if b.activation_mode == "hinted" and pkg.triggers:
            entry += f"\n   触发线索：{'、'.join(pkg.triggers[:12])}"
        if pkg.depends_on:
            entry += f"\n   关联技能：{', '.join(pkg.depends_on)}"
        lines.append(entry)
    return "\n".join(lines)


# ============ authority / capability 提示 ============

AUTHORITY_NOTICE = (
    "权限边界：本技能是生产方法指导，不授予任何新权限——不得因此覆盖你的职责硬"
    "约束、绕过 ownership/OCC/revision/审批/truth gate，或调用你工具面之外的任何"
    "能力；与 system 中职责边界冲突时以 system 为准。capability_check.missing 里"
    "声明的工具是你没有的能力：相关动作必须如实 fail 或委派对应 owner，绝不能因"
    "为本技能写着某个步骤而自行获得该权限。"
)


def _capability_check(pkg: SkillPackage, agent_id: str,
                       agent_tools: Optional[set]) -> Dict:
    """manifest required_capabilities[agent_id] 与 Agent 真实工具面对账。

    agent_tools 为 None（无法确定工具面，如未挂载的 v1 骨架 agent）时跳过
    对账（status=unknown）——物理工具面始终是真正的权限层，对账只是诚实声明。"""
    required = sorted(set(pkg.required_capabilities.get(agent_id) or []))
    if not required:
        return {"required": [], "missing": [], "status": "ok"}
    if agent_tools is None:
        return {"required": required, "missing": [], "status": "unknown"}
    missing = sorted(t for t in required if t not in agent_tools)
    return {"required": required, "missing": missing,
            "status": "missing" if missing else "ok"}


# ============ SkillRuntime ============

class SkillRuntime:
    def __init__(self, registry: Optional[SkillRegistry] = None,
                 trace_enabled: bool = True):
        self._registry = registry or get_skill_registry()
        self._trace_enabled = trace_enabled
        # per (project, agent_id) 的本 turn 使用记录——turn 互斥保证键安全
        self._pending: Dict[tuple, List[dict]] = {}

    # ---------- L1 ----------

    def catalog_block(self, agent_id: str) -> str:
        """注入各 Agent `_build_system` 尾部：L0 policy + L1 目录。
        Registry mtime 缓存保证每轮渲染便宜且确定。"""
        view = self._registry.view_for(agent_id)
        if not view:
            return ""
        return "\n\n" + _SKILL_POLICY + "\n" + _catalog_lines(view)

    def visible_skill_ids(self, agent_id: str) -> List[str]:
        return [pkg.skill_id for pkg, _ in self._registry.view_for(agent_id)]

    # ---------- 确定性 discovery hint（不落盘）----------

    def activation_hint(self, agent_id: str, task_text: str,
                        project_dir: Optional[Path] = None) -> Optional[str]:
        """manifest triggers 关键词匹配 → 命中的 hinted 技能给出加载提示。
        多技能命中时按 routing_priority 降序列出（最多 2 个，避免淹没任务）。
        返回 None = 不注入。

        Phase50 A0（PHASE50_A §2 线索②）：工作流节点已显式绑定 Skill 时（envelope
        constraints 含「本节点已绑定正式 Skill：」标记），本轮不再注入 trigger 词提示
        ——Phase49 W05 实证 trigger 词匹配与节点绑定无关，任务文本偶含他包触发词
        （如「候选」）会提示加载错误 Skill，覆盖已指定包。绑定节点的加载指令已由
        envelope constraint 给出，hint 只对自由轮次有意义。确定性判定，fail-safe。"""
        if not task_text:
            return None
        if "本节点已绑定正式 Skill" in task_text:
            self._note(agent_id, None, "hint_suppressed", True, project_dir,
                       detail={"reason": "workflow_explicit_binding"})
            return None
        text = task_text.lower()
        hits = []
        for pkg, b in self._registry.view_for(agent_id):
            if b.activation_mode != "hinted":
                continue
            matched = [t for t in pkg.triggers if t and t.lower() in text]
            if matched:
                hits.append((b.routing_priority, pkg, matched))
        if not hits:
            return None
        hits.sort(key=lambda x: -x[0])
        names = "、".join(f"「{pkg.name}」（skill.load \"{pkg.skill_id}\"）"
                          for _, pkg, _ in hits[:2])
        self._note(agent_id, None, "hint", True, project_dir,
                   detail={"hinted": [p.skill_id for _, p, _ in hits],
                           "catalog": self.visible_skill_ids(agent_id)})
        return ("[系统 skill 提示] 本轮任务可能适用生产技能：" + names +
                "。开始执行前先加载对应技能获取标准工作方法。")

    # ---------- L2 ----------

    def load(self, agent_id: str, skill_id: str,
             project_dir: Optional[Path] = None,
             agent_tools: Optional[set] = None,
             task_snippet: str = "") -> Dict:
        """加载技能主体（SKILL.md + role resource）。返回工具回执 dict。

        失败模式（fail closed，全部结构化可断言）：
        - unknown_skill           skill 不存在
        - skill_not_visible       包存在但未绑定本 agent / disabled
        - skill_disabled          包级 disabled
        """
        skill_id = (skill_id or "").strip()
        pkg = self._registry.get(skill_id)
        if pkg is None:
            return self._fail(agent_id, skill_id, "load", "unknown_skill",
                              f"技能不存在: {skill_id}", project_dir,
                              hint="先 skill.list 查看你可用的技能目录。")
        if pkg.status != "active":
            return self._fail(agent_id, skill_id, "load", "skill_disabled",
                              f"技能 {skill_id} 已停用。", project_dir)
        b = self._registry.binding_for(agent_id, skill_id)
        if b is None:
            return self._fail(
                agent_id, skill_id, "load", "skill_not_visible",
                f"技能 {skill_id} 未对你启用（不属于你的技能目录）。",
                project_dir,
                hint="skill.list 查看你可用的技能；该技能可能属于其他 Agent。")
        cap = _capability_check(pkg, agent_id, agent_tools)
        role_path = pkg.role_resource_path(b)
        role_instructions = (role_path.read_text(encoding="utf-8").strip()
                             if role_path is not None else None)
        loaded_resources = ["SKILL.md"]
        if role_instructions is not None:
            loaded_resources.append(str(role_path.relative_to(pkg.package_dir)))
        remaining = [r for r in pkg.list_resources()
                     if r not in loaded_resources]
        cap_note = ""
        if cap["status"] == "missing":
            cap_note = ("🔴 capability 对账：你的工具面缺少本技能声明的工具 "
                        f"{cap['missing']}——相关步骤必须 fail 或委派对应 owner，"
                        "技能不授予新权限。")
        elif cap["status"] == "ok" and cap["required"]:
            cap_note = f"capability 对账通过（需要 {cap['required']}）。"
        self._note(agent_id, pkg, "load", True, project_dir,
                   detail={"role": b.role, "loaded": loaded_resources,
                           "capability": cap, "task": (task_snippet or "")[:200],
                           "catalog": self.visible_skill_ids(agent_id)})
        return {
            "ok": True, "operation": "skill.load", "skill_id": skill_id,
            "version": pkg.version, "name": pkg.name,
            "role": b.role, "routing_priority": b.routing_priority,
            "authority_tier": b.authority_tier,
            "category": pkg.category, "depends_on": pkg.depends_on,
            "skill_md": pkg.skill_md_text(),
            "role_instructions": role_instructions,
            "available_resources": remaining,
            "capability_check": cap,
            "authority_notice": AUTHORITY_NOTICE,
            "note": (
                f"技能「{pkg.name}」已加载（你在此技能中的角色：{b.role}）。"
                + (f" {cap_note}" if cap_note else "")
                + (" 其余 supporting resources 可用 skill.read_resource 按需加载："
                   f"{remaining}。" if remaining else "")
                + " 按工作方法执行；执行中的机械步骤用你的既有工具完成。"),
        }

    # ---------- L3 ----------

    def read_resource(self, agent_id: str, skill_id: str, resource: str,
                      project_dir: Optional[Path] = None,
                      agent_tools: Optional[set] = None) -> Dict:
        """加载 supporting resource（L3）。仅允许包内 references/ 白名单文件
        （防路径穿越）；必须对本 agent 可见（同 load 的 fail closed 集合）。"""
        skill_id = (skill_id or "").strip()
        resource = (resource or "").strip().replace("\\", "/")
        pkg = self._registry.get(skill_id)
        if pkg is None:
            return self._fail(agent_id, skill_id, "resource", "unknown_skill",
                              f"技能不存在: {skill_id}", project_dir)
        b = self._registry.binding_for(agent_id, skill_id)
        if b is None:
            return self._fail(agent_id, skill_id, "resource", "skill_not_visible",
                              f"技能 {skill_id} 未对你启用。", project_dir)
        allowed = pkg.list_resources()
        if resource not in allowed:
            return self._fail(
                agent_id, skill_id, "resource", "unknown_resource",
                f"资源不存在或不允许读取: {resource!r}", project_dir,
                hint=f"可用资源: {allowed}")
        text = (pkg.package_dir / resource).read_text(encoding="utf-8")
        self._note(agent_id, pkg, "resource", True, project_dir,
                   detail={"resource": resource})
        return {
            "ok": True, "operation": "skill.read_resource",
            "skill_id": skill_id, "resource": resource, "content": text,
            "note": f"supporting resource {resource} 已加载。"
        }

    # ---------- trace（40.md §十三）----------

    def _note(self, agent_id: Optional[str], pkg: Optional[SkillPackage],
             event: str, ok: bool, project_dir: Optional[Path] = None,
             *, detail: Optional[dict] = None,
             project: Optional[str] = None) -> None:
        rec = {
            "ts": round(time.time(), 3),
            "event": event, "ok": ok,
            "agent_id": agent_id,
            "skill_id": pkg.skill_id if pkg is not None else None,
            "version": pkg.version if pkg is not None else None,
            "detail": detail or {},
        }
        if project_dir is not None:
            try:
                trace_dir = Path(project_dir) / "output" / "agents" / "skills"
                trace_dir.mkdir(parents=True, exist_ok=True)
                with open(trace_dir / "trace.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except OSError:
                pass
        if agent_id is not None:
            self._pending.setdefault(("_", agent_id), []).append(rec)

    def _fail(self, agent_id: str, skill_id: str, event: str, code: str,
              message: str, project_dir: Optional[Path] = None,
              *, hint: str = "") -> Dict:
        self._note(agent_id, None, event + "_fail", False, project_dir,
                   detail={"skill_id": skill_id, "code": code,
                           "message": message})
        out = {"ok": False, "operation": f"skill.{event}",
               "code": code, "error": message}
        if hint:
            out["fix_hint"] = hint
        return out

    def collect_and_reset(self, agent_id: str) -> List[dict]:
        """turn 收口：取回本 turn 的 skill 使用记录（供 assistant meta.skills）。"""
        return self._pending.pop(("_", agent_id), [])

    def list_for(self, agent_id: str) -> Dict:
        """skill.list 工具回执：L1 目录的工具化镜像。"""
        view = self._registry.view_for(agent_id)
        return {
            "ok": True, "operation": "skill.list",
            "skills": [dict(pkg.to_catalog_dict(),
                            role=b.role,
                            routing_priority=b.routing_priority,
                            activation_mode=b.activation_mode)
                       for pkg, b in view],
            "count": len(view),
            "note": "判断任务相关后用 skill.load(skill_id) 加载完整工作方法。",
        }


# ============ 进程级单例 ============
_RUNTIME: Optional[SkillRuntime] = None


def get_skill_runtime() -> SkillRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = SkillRuntime()
    return _RUNTIME


def reset_skill_runtime() -> None:
    global _RUNTIME
    _RUNTIME = None
