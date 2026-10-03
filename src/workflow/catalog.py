# src/workflow/catalog.py
# 动态组件目录（PHASE46_01 §3.2）：由真实注册表派生——AgentRegistry（6 员）+ SkillRegistry
# （正式 Skill）+ 各 Agent 模块的真实工具面。前端 catalog.ts 的静态快照保留为离线兜底；
# 页面在线时以本目录为准（前端不得把旧 Agent/实验 Skill 当可运行项）。
from __future__ import annotations
import hashlib
from pathlib import Path
from typing import Dict, List, Optional

from deps import BASE_DIR
from src.agents.registry import agent_system_prompt
from src.agents.skill_system.registry import get_skill_registry
from .models import AGENT_EXPECTED_DOMAINS, WORKFLOW_AGENTS


def _tool_names(surface) -> List[str]:
    """从工具 schema 列表提取工具名（["shot.create", ...]）。"""
    out: List[str] = []
    for t in surface or []:
        if isinstance(t, dict):
            fn = t.get("function") or {}
            name = fn.get("name") or t.get("name")
            if name:
                out.append(name)
        elif isinstance(t, str):
            out.append(t)
    return out


def _skill_bindable(agent_id: str) -> bool:
    """Skill 绑定名册（skill_system/models.py VALID_AGENT_IDS）不含 assembly。"""
    from src.agents.skill_system.models import VALID_AGENT_IDS
    return agent_id in VALID_AGENT_IDS


def _agent_tool_surface(agent_id: str):
    """真实工具面（懒 import；与流式端点同一来源）。"""
    if agent_id == "producer":
        from src.agents.producer import _build_producer_tools, DELEGATE_TOOLS
        offered, _execs = _build_producer_tools()
        return list(offered) + [{"type": "function",
                                 "function": {"name": n}} for n in sorted(DELEGATE_TOOLS)]
    if agent_id == "script_writer":
        from src.agents.script_writer import SCRIPT_WRITER_TOOLS
        return SCRIPT_WRITER_TOOLS
    if agent_id == "character_designer":
        from src.agents.character_designer import CHARACTER_DESIGNER_TOOLS
        return CHARACTER_DESIGNER_TOOLS
    if agent_id == "scene_designer":
        from src.agents.scene_designer import _tool_surface
        return _tool_surface()
    if agent_id == "director":
        from src.agents.director import DIRECTOR_TOOLS
        return DIRECTOR_TOOLS
    if agent_id == "assembly":
        from src.agents.assembly import ASSEMBLY_READ_TOOLS, WRITE_TOOL_DOMAIN
        return [{"type": "function", "function": {"name": n}}
                for n in sorted(set(ASSEMBLY_READ_TOOLS) | set(WRITE_TOOL_DOMAIN))]
    return []


def _write_domains(agent_id: str) -> List[str]:
    """写工具 → 锁域映射的域集合（锁接线与收货校验共用同一事实源）。"""
    try:
        if agent_id == "character_designer":
            from src.agents.character_designer import WRITE_TOOL_DOMAIN
            return sorted(set(WRITE_TOOL_DOMAIN.values()))
        if agent_id == "scene_designer":
            from src.agents.scene_designer import WRITE_TOOL_DOMAIN
            return sorted(set(WRITE_TOOL_DOMAIN.values()))
        if agent_id == "assembly":
            from src.agents.assembly import WRITE_TOOL_DOMAIN
            return sorted(set(WRITE_TOOL_DOMAIN.values()))
        if agent_id == "script_writer":
            from src.agents.script_writer import WRITE_OPERATIONS
            return ["story"]
        if agent_id == "director":
            from src.agents.director import DIRECTOR_WRITE_OPERATIONS
            return ["storyboard"]
    except Exception:
        pass
    return []


def _registry_digest(agent_id: str) -> str:
    """注册内容摘要：系统提示 + 权限/范围 + 工具面名称（改动即变，可作基线快照）。"""
    a = agent_system_prompt(agent_id)
    tools = ",".join(sorted(_tool_names(_agent_tool_surface(agent_id))))
    h = hashlib.sha256((agent_id + "\x00" + a + "\x00" + tools).encode("utf-8"))
    return "sha256:" + h.hexdigest()[:16]


def build_catalog(rt, project: Optional[str] = None) -> dict:
    """构建组件目录。rt=AgentRuntime（读 enabled/profiles）。"""
    agents: List[dict] = []
    for aid in WORKFLOW_AGENTS:
        a = rt.agents.get(aid)
        if a is None:
            continue
        prof = rt.profiles.resolve_for_agent(a, rt.settings)
        enabled = rt.agents.is_enabled(aid, rt.settings)
        tools = _tool_names(_agent_tool_surface(aid))
        agents.append({
            "id": aid,
            "name": a.name,
            "role": a.role,
            "description": a.description,
            "readScopes": list(a.read_scopes),
            "writeScopes": list(a.write_scopes),
            "canDelegateTo": list(a.can_delegate_to),
            "coreTools": tools,                      # 模块自带必有（只读展示，不可勾掉）
            "optionalTools": [],                     # 扩展工具区：仅接受注册且已授权的项（v1 恒空）
            "toolCount": len(tools),
            "writeDomains": _write_domains(aid),
            "expectedArtifactDomains": list(AGENT_EXPECTED_DOMAINS.get(aid) or []),
            "skillBindable": _skill_bindable(aid),
            "registryDigest": _registry_digest(aid),
            "turnEntry": _turn_entry_name(aid),
            "enabled": bool(enabled),
            "unavailableReason": None if enabled else f"Agent 已禁用（settings）",
            "modelProfile": prof.to_dict() if prof else None,
        })

    skills: List[dict] = []
    reg = get_skill_registry()
    for sid, pkg in sorted(reg.skills().items()):
        entry = {
            "id": pkg.skill_id,
            "version": pkg.version,
            "name": pkg.name,
            "description": pkg.description,
            "applicability": pkg.applicability,
            "category": pkg.category,
            "source": pkg.source,
            "status": pkg.status,
            "dependsOn": list(pkg.depends_on or []),
            "bindings": [{"agentId": b.agent_id, "role": b.role,
                          "routingPriority": b.routing_priority,
                          "activationMode": b.activation_mode}
                         for b in pkg.bindings if b.enabled],
            "contentHash": _skill_content_hash(pkg),
            "registryError": reg.registry_errors().get(sid),
        }
        skills.append(entry)

    return {
        "catalogVersion": "workflow_catalog_v1",
        "agents": agents,
        "skills": skills,
        "notes": [
            "coreTools 来自各 Agent 模块真实工具面（与流式端点同源），核心工具必有、只读；",
            "skillBindable 按 skill_system/models.py VALID_AGENT_IDS（assembly 暂不可绑定）。",
        ],
    }


def _turn_entry_name(agent_id: str) -> str:
    return {
        "producer": "src.agents.producer.producer_turn",
        "script_writer": "src.agents.script_writer.script_writer_turn",
        "character_designer": "src.agents.character_designer.character_designer_turn",
        "scene_designer": "src.agents.scene_designer.scene_designer_turn",
        "director": "src.agents.director.director_turn",
        "assembly": "src.agents.assembly.assembly_turn",
    }.get(agent_id, "")


def _skill_content_hash(pkg) -> str:
    """Skill 内容摘要（manifest + SKILL.md + references）。快照用，可跨重启重算比对。"""
    h = hashlib.sha256()
    base: Optional[Path] = getattr(pkg, "package_dir", None)
    if base is not None and Path(base).is_dir():
        for p in sorted(Path(base).rglob("*")):
            if p.is_file() and p.suffix in (".json", ".md"):
                h.update(str(p.relative_to(base)).encode())
                try:
                    h.update(p.read_bytes())
                except Exception:
                    h.update(b"<unreadable>")
        return "sha256:" + h.hexdigest()[:24]
    return "sha256:unknown"
