# src/storyboard/scope.py
# AI-7D-R · Task Scope 模型 + Intent Parser（确定性，第一版无 LLM）。
#
# 原则（用户定，宁少授权）：
#   - 最终权限 = Agent 固定权限 ∩ 用户意图 Scope。
#   - create 必须命中"实体数量变化"语义（新增 N 段 / 扩充到 N / 加 N 段）；
#     "增添故事段内容"只判 update（有内容语义、无数量变化）。
#   - 有歧义 → 退回固定默认（仅 read，写全关），绝不扩大。
#   - 数量能确定就提取 target_count / delta。
# 中性模块：不 import router/deps、不调 LLM——可被旧 agent runtime 复用。
from __future__ import annotations

import re
from typing import Any, Optional

# Director 固定权限：能写的故事域实体与操作。剧本/角色只读（此处不列写操作即天然挡掉）。
AGENT_FIXED_SCOPE: dict[str, dict[str, Any]] = {
    "ops": {
        "scene": {"create", "update", "delete"},
        "shot": {"create", "update", "delete"},
    },
}


# ============ 实体识别 ============
_RE_SCENE = re.compile(r"故事段|段落|scene|s\d{2}(?!_c)", re.IGNORECASE)
_RE_SHOT = re.compile(r"分镜|镜头|shot|_c\d{2}|\.c\d+", re.IGNORECASE)


# ============ 操作词 ============
# expand: 扩充到/扩展到/增加到 N
_RE_EXPAND_TO = re.compile(r"(?:扩充到|扩展到|增加到|加到)\s*([0-9]+)\s*[个段]", re.IGNORECASE)
# create（数量变化）：新增/创建/加入/添加/插入 + N(段/个) 或 一/两/几/若干 段
_RE_CREATE_QTY = re.compile(
    r"(?:新增|创建|加入|添加|插入|加)\s*"
    r"(?:([0-9]+)\s*[个段]|一(?:个|段|集))",
    re.IGNORECASE)
# 绝对目标：确保/一共/总共 N 段（不需要实体词）
_RE_ABSOLUTE_COUNT = re.compile(
    r"(?:确保|保证|一共|总共|保持|弄成|改成|要有)\s*([0-9]+)\s*[个段]",
    re.IGNORECASE)
# delete / update 词
_RE_DELETE = re.compile(r"删(?:除|掉|去)?|移除|去掉|去除", re.IGNORECASE)
_RE_UPDATE = re.compile(
    r"优化|修改|补充|完善|改写|重写|调整|润色|更新|修(?:一修|改)?|"
    r"增添|丰富.*内容|扩充.*内容", re.IGNORECASE)


def _entity_in(text: str) -> dict[str, bool]:
    return {"scene": bool(_RE_SCENE.search(text)), "shot": bool(_RE_SHOT.search(text))}


def parse_intent(text: Optional[str]) -> dict[str, Any]:
    """用户意图 → Intent Scope {ops:{entity:{create,update,delete}}, targets:{scene_count}}。
    确定性、宁少授权：无数量变化语义绝不判 create；"增添内容"算 update 不算 create；歧义退回只读。"""
    t = text or ""
    ent = _entity_in(t)
    ops: dict[str, set] = {}
    targets: dict[str, int] = {}

    # ---- 绝对数量目标（"确保一共7段"）——收集越严越好，不授予create也要记录目标 ----
    m_c = _RE_ABSOLUTE_COUNT.search(t)
    if m_c:
        targets["scene_count"] = int(m_c.group(1))

    # ---- delete：实体在场 + 删除词 ----
    if _RE_DELETE.search(t):
        for e in ("scene", "shot"):
            if ent[e]:
                ops.setdefault(e, set()).add("delete")

    # ---- update：实体在场 + 内容修改语义（含"增添内容/补充"→只 update） ----
    if _RE_UPDATE.search(t):
        for e in ("scene", "shot"):
            if ent[e]:
                ops.setdefault(e, set()).add("update")

    # ---- create：必须"实体数量变化"语义 ----
    m_expand = _RE_EXPAND_TO.search(t)
    m_q = _RE_CREATE_QTY.search(t)
    if m_expand and (ent["scene"] or ent["shot"]):
        # 扩充到 N 段：绝对是 target，且授予 create（要新增到 N）
        targets["scene_count"] = int(m_expand.group(1))
        for e in ("scene", "shot"):
            if ent[e]:
                ops.setdefault(e, set()).add("create")
    elif m_q and (ent["scene"] or ent["shot"]):
        # 新增 N 段 / 加一段：delta
        delta = int(m_q.group(1)) if m_q.group(1) else 1
        for e in ("scene", "shot"):
            if ent[e]:
                ops.setdefault(e, set()).add("create")
        targets["_scene_delta"] = delta

    return {"ops": {k: set(v) for k, v in ops.items()}, "targets": targets}


def intersect(fixed: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    """固定权限 ∩ 用户意图：逐实体逐 op 求交。用户想碰固定域之外的实体自动被挡
    （fixed 无该写操作）。targets 沿用 user（保守目标）。"""
    f_ops = fixed.get("ops", {})
    u_ops = user.get("ops", {})
    eff_ops: dict[str, set] = {}
    for entity, fset in f_ops.items():
        uset = u_ops.get(entity) or set()
        eff_ops[entity] = set(fset) & set(uset)
    return {"ops": eff_ops, "targets": dict(user.get("targets", {}))}


def resolve_effective_scope(text: Optional[str],
                            fixed: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Director 单 turn 的 effective scope。
    有歧义/无意图时 ops 为空 → agent 只能 read + 建议，不能改。"""
    fixed = fixed or AGENT_FIXED_SCOPE
    intent = parse_intent(text)
    return intersect(fixed, intent)


def can(effective: dict[str, Any], entity: str, op: str) -> bool:
    """查询 effective scope 是否允许 (entity, op)。read 永远放行。"""
    if op == "read":
        return True
    return op in (effective.get("ops", {}).get(entity) or set())