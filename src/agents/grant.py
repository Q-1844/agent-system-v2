# src/agents/grant.py
# Phase 54 A1：scoped grant——项目级可撤销授权（B 报告 §5 最小实现路径）。
# 设计（B 报告原文约束）：可撤销会话级授权、审计 actor/grant 来源/有效范围、
# owner 规则不破坏（canonical owner 仍按域：写操作过 OCC 与既有审批）。
# - grant 按「工具族」（=Specialist 模块工具面）授权，展开为具体工具名在合并时进行
#   （模块工具面演化不会 stale 已存授权文件）；
# - 授权不改变 canonical 域 owner 语义，只把被授权族的【生产工具执行权】并入选中
#   grant 的 agent 会话（当前仅 producer 消费）；
# - 每次授权/撤销/被授权工具执行都落 grants_audit.jsonl（actor+grant_id+工具+结果）。
# Import 纪律：只 import 标准库 + 本包（延迟 import Specialist 模块避免环）。
from __future__ import annotations
import json
import secrets
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 工具族 → (模块名, TOOLS 变量名, TOOL_EXECUTORS 变量名)
GRANT_FAMILIES = {
    "script_writer": ("src.agents.script_writer", "SCRIPT_WRITER_TOOLS", "TOOL_EXECUTORS"),
    "character_designer": ("src.agents.character_designer", "CHARACTER_DESIGNER_TOOLS", "TOOL_EXECUTORS"),
    "scene_designer": ("src.agents.scene_designer", "SCENE_DESIGNER_TOOLS", "TOOL_EXECUTORS"),
    "director": ("src.agents.director", "DIRECTOR_TOOLS", "TOOL_EXECUTORS"),
    "assembly": ("src.agents.assembly", "ASSEMBLY_TOOLS", "TOOL_EXECUTORS"),
}

# 不允许经 grant 并入的内部/通道工具（委派是协调通道；skill 是会话系统自带；
# 提案通道是 CD 内部治理——均不随族合并）。
_FAMILY_EXCLUDE = {"*.delegate", "proposal.submit"}


def _grants_path(pdir: Path) -> Path:
    return Path(pdir) / "output" / "agents" / "grants.json"


def _audit_path(pdir: Path) -> Path:
    return Path(pdir) / "output" / "agents" / "grants_audit.jsonl"


def _load(pdir: Path) -> dict:
    p = _grants_path(pdir)
    if not p.exists():
        return {"grants": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"grants": []}


def _save(pdir: Path, doc: dict) -> None:
    p = _grants_path(pdir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _audit(pdir: Path, rec: dict) -> None:
    p = _audit_path(pdir)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = dict(rec)
    rec.setdefault("ts", time.time())
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def grant(pdir: Path, agent_id: str, families: List[str], reason: str,
          granted_by: str = "operator", ttl_s: Optional[float] = None) -> dict:
    """新授权。families 必须全部是已知工具族。返回 grant 记录（含 grant_id）。"""
    unknown = [f for f in families if f not in GRANT_FAMILIES]
    if unknown:
        raise ValueError(f"unknown grant families: {unknown}")
    g = {
        "grantId": "gr_" + secrets.token_hex(4),
        "agentId": agent_id,
        "families": sorted(set(families)),
        "reason": (reason or "")[:300],
        "grantedBy": granted_by,
        "createdAt": time.time(),
        "expiresAt": (time.time() + float(ttl_s)) if ttl_s else None,
        "revokedAt": None,
    }
    doc = _load(pdir)
    doc["grants"].append(g)
    _save(pdir, doc)
    _audit(pdir, {"event": "grant", "actor": granted_by, "grantId": g["grantId"],
                  "agentId": agent_id, "families": g["families"],
                  "reason": g["reason"], "ttl_s": ttl_s})
    return g


def revoke(pdir: Path, grant_id: str, reason: str = "",
           revoked_by: str = "operator") -> bool:
    doc = _load(pdir)
    hit = None
    for g in doc["grants"]:
        if g.get("grantId") == grant_id and not g.get("revokedAt"):
            g["revokedAt"] = time.time()
            hit = g
    if hit:
        _save(pdir, doc)
        _audit(pdir, {"event": "revoke", "actor": revoked_by, "grantId": grant_id,
                      "reason": (reason or "")[:200]})
    return bool(hit)


def active(pdir: Path, agent_id: str, now: Optional[float] = None) -> List[dict]:
    """未撤销且未过期的有效授权（过期即无效，惰性不回写）。"""
    now = time.time() if now is None else now
    out = []
    for g in _load(pdir).get("grants") or []:
        if g.get("agentId") != agent_id or g.get("revokedAt"):
            continue
        exp = g.get("expiresAt")
        if exp and float(exp) < float(now):
            continue
        out.append(g)
    return out


def _family_tools(family: str) -> Tuple[List[dict], Dict[str, object]]:
    """展开工具族 → (schemas, executors)。延迟 import 避免环。"""
    import importlib
    mod_name, tools_var, exec_var = GRANT_FAMILIES[family]
    mod = importlib.import_module(mod_name)
    return getattr(mod, tools_var), getattr(mod, exec_var)


def _tool_name(schema: dict) -> str:
    return ((schema or {}).get("function") or {}).get("name") or ""


def granted_tool_face(pdir: Path, agent_id: str) -> dict:
    """合并被授权族的工具面。
    返回 {tools:[schema], executors:{name:fn}, grantIds:[...], families:[...],
          excluded:[...]}。族内排除内部/通道工具；同名工具以被授权族为准
    （当前五族工具名全域唯一）。无授权 → 空合并。"""
    grants = active(pdir, agent_id)
    families = sorted({f for g in grants for f in (g.get("families") or [])})
    tools: List[dict] = []
    executors: Dict[str, object] = {}
    excluded: List[str] = []
    for fam in families:
        fam_tools, fam_exec = _family_tools(fam)
        for sch in fam_tools or []:
            name = _tool_name(sch)
            if not name:
                continue
            if any(name.endswith(pat.lstrip("*")) or name == pat.replace("*", "")
                   for pat in _FAMILY_EXCLUDE if pat.startswith("*")) or \
               name in _FAMILY_EXCLUDE:
                excluded.append(name)
                continue
            ex = fam_exec.get(name)
            if ex is None:
                excluded.append(name + "(no-executor)")
                continue
            executors[name] = ex
            tools.append(sch)
    return {"tools": tools, "executors": executors, "grantIds": [g["grantId"] for g in grants],
            "families": families, "excluded": excluded}


def audit_execution(pdir: Path, agent_id: str, grant_ids: List[str], tool: str,
                    ok: bool, detail: str = "") -> None:
    _audit(pdir, {"event": "execution", "actor": agent_id,
                  "grantIds": grant_ids, "tool": tool, "ok": bool(ok),
                  "detail": (detail or "")[:200]})


def grant_notice(families: List[str], grant_ids: List[str]) -> str:
    """系统提示追加块：身份纪律随授权的转换（owner 纪律→自审纪律，Skill 判据不变）。"""
    if not families:
        return ""
    return (
        "\n【scoped grant 生效】本次会话经项目级授权（grantId="
        + ",".join(grant_ids) + "），你除只读与 *.delegate 外，还被授予以下工具族的"
        "直接执行权：" + "、".join(families) + "。\n"
        "- 授权期间你可以亲自执行这些域的生产工具（写操作仍走 OCC 与既有审批闸，"
        "canonical owner 语义不变）；每次被授权工具调用都落入授权审计（含 grantId）。\n"
        "- 纪律转换：被授权域的「必须委派」约束临时解除，改为「自产自审」——Skill 的"
        "三查判据/时长配方/接续策略仍全部适用，你对自己的每次写入负 owner 责任。\n"
        "- 未授权域仍必须经对应 *.delegate 委派；审计与回执如实上报，绝不虚报。")


def wrap_zero_tool_nudge(original: str, has_grants: bool) -> str:
    """零工具谎报闸门措辞适配：有授权时不再说「你没有写工具」。"""
    if not has_grants:
        return original
    return original.replace(
        "你的工具面里没有写工具——未委派即未发生，canonical 没有变化。",
        "你本次会话带有 scoped grant 授权——未调用任何被授权工具或 *.delegate，"
        "canonical 同样没有变化。")
