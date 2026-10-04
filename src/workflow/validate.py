# src/workflow/validate.py
# 后端校验（PHASE46_01 §4.3）：后端是运行准入权威，比前端体检更严格。
# 前端 connectionRule/validateDraft 继续用于及时反馈；此处全部独立重做，
# 返回 node/edge 定位的 issue，不依赖前端结果。
#
# 覆盖 §4.3 十条最少检查；结构判断只看合法执行边，忽略坐标（§4.3.4/5）。
from __future__ import annotations
from typing import Dict, List, Optional, Set, Tuple

from .catalog import build_catalog
from .models import NODE_TYPES, WORKFLOW_AGENTS, DRAFT_SCHEMA_VERSION

EXEC_ALLOWED = {
    ("agent", "agent"), ("agent", "human_review"), ("agent", "boundary_end"),
    ("human_review", "agent"), ("human_review", "human_review"),
    ("human_review", "boundary_end"),
    ("boundary_start", "agent"), ("boundary_start", "human_review"),
}


def _issue(errors: List[dict], warnings: List[dict], level: str, code: str,
           message: str, node_id: Optional[str] = None, edge_id: Optional[str] = None):
    row = {"level": level, "code": code, "message": message,
           "nodeId": node_id, "edgeId": edge_id}
    (errors if level == "error" else warnings).append(row)


def validate_definition(definition: dict, rt, project: str) -> dict:
    """校验定义。返回 {ok, errors, warnings, nodes: {id: node}}（nodes 供编译复用）。"""
    errors: List[dict] = []
    warnings: List[dict] = []
    if not isinstance(definition, dict):
        return {"ok": False, "errors": [{"level": "error", "code": "BAD_DEFINITION",
                                          "message": "定义必须是对象", "nodeId": None, "edgeId": None}],
                "warnings": [], "nodes": {}}
    schema = definition.get("schemaVersion")
    if schema not in (DRAFT_SCHEMA_VERSION, "workflow_def_v1"):
        _issue(errors, warnings, "error", "SCHEMA_VERSION",
               f"schemaVersion 不支持: {schema!r}（支持 {DRAFT_SCHEMA_VERSION}）")
    if str(definition.get("projectId") or "") != project:
        _issue(errors, warnings, "error", "PROJECT_MISMATCH",
               f"定义归属项目 {definition.get('projectId')!r} 与目标项目 {project!r} 不一致")

    nodes: Dict[str, dict] = {}
    for n in definition.get("nodes") or []:
        nid = n.get("id")
        if not nid:
            _issue(errors, warnings, "error", "NODE_NO_ID", "存在无 id 的节点")
            continue
        if nid in nodes:
            _issue(errors, warnings, "error", "NODE_DUP_ID", f"节点 id 重复: {nid}", nid)
            continue
        if n.get("type") not in NODE_TYPES:
            _issue(errors, warnings, "error", "NODE_BAD_TYPE",
                   f"节点类型非法: {n.get('type')!r}", nid)
            continue
        nodes[nid] = n

    edges: List[dict] = []
    edge_ids: Set[str] = set()
    for e in definition.get("edges") or []:
        eid = e.get("id")
        if not eid or eid in edge_ids:
            _issue(errors, warnings, "error", "EDGE_DUP_ID",
                   f"连线 id 缺失或重复: {eid!r}", None, eid)
            continue
        edge_ids.add(eid)
        src, dst = e.get("source"), e.get("target")
        if src not in nodes or dst not in nodes:
            _issue(errors, warnings, "error", "EDGE_DANGLING",
                   f"连线端点悬空: {src}→{dst}", src if src not in nodes else dst, eid)
            continue
        edges.append(e)

    cat = build_catalog(rt, project)
    agent_cat = {a["id"]: a for a in cat["agents"]}
    skill_cat = {s["id"]: s for s in cat["skills"]}

    # ② 注册 Agent / Skill 存在且正式可用
    for nid, n in nodes.items():
        if n.get("type") == "agent":
            ref = n.get("refId")
            a = agent_cat.get(ref)
            if a is None:
                _issue(errors, warnings, "error", "AGENT_UNKNOWN",
                       f"引用的 Agent 不在正式名册: {ref!r}", nid)
            elif not a["enabled"]:
                _issue(errors, warnings, "error", "AGENT_DISABLED",
                       f"Agent 已禁用: {ref}", nid)
        elif n.get("type") == "skill":
            ref = n.get("refId")
            s = skill_cat.get(ref)
            if s is None:
                _issue(errors, warnings, "error", "SKILL_UNKNOWN",
                       f"引用的 Skill 不在正式注册表: {ref!r}", nid)
            elif s["status"] != "active":
                _issue(errors, warnings, "error", "SKILL_INACTIVE",
                       f"Skill 非正式可用状态（{s['status']}）: {ref}", nid)

    # ③ Skill 绑定线：skill→agent、适用、不重复
    bind_pairs: Set[Tuple[str, str]] = set()
    for e in edges:
        if e.get("type") != "skill_binding":
            continue
        src, dst = nodes.get(e.get("source")), nodes.get(e.get("target"))
        if not src or not dst:
            continue
        if src.get("type") != "skill" or dst.get("type") != "agent":
            _issue(errors, warnings, "error", "BIND_WRONG_TARGET",
                   "Skill 绑定线必须从 Skill 节点连到 Agent 节点", dst.get("id"), e["id"])
            continue
        s = skill_cat.get(src.get("refId"))
        a = agent_cat.get(dst.get("refId"))
        if s is not None and a is not None:
            if not a["skillBindable"]:
                _issue(errors, warnings, "error", "BIND_AGENT_UNSUPPORTED",
                       f"Skill 系统名册不含 {a['id']}（assembly 暂不可绑定）", dst["id"], e["id"])
            elif not any(b["agentId"] == a["id"] for b in s["bindings"]):
                _issue(errors, warnings, "error", "BIND_NOT_APPLICABLE",
                       f"Skill {s['id']} 的绑定名册不含 {a['id']}", dst["id"], e["id"])
        if s is not None and not e.get("skillVersion"):
            _issue(errors, warnings, "error", "BIND_NO_VERSION",
                   f"绑定线缺少 skillVersion（启动时无法核对内容）", src["id"], e["id"])
        pair = (src.get("id"), dst.get("id"))
        if pair in bind_pairs:
            _issue(errors, warnings, "error", "BIND_DUP",
                   "同一 Skill→Agent 重复绑定", dst.get("id"), e["id"])
        bind_pairs.add(pair)

    # 执行边（忽略边界节点两侧的视觉连线；§4.3：边界纯视觉，不参与执行拓扑）
    exec_edges = [e for e in edges if e.get("type") == "execution"]
    exec_graph: Dict[str, List[str]] = {nid: [] for nid in nodes}
    exec_in: Dict[str, List[str]] = {nid: [] for nid in nodes}
    for e in exec_edges:
        src, dst = nodes.get(e.get("source")), nodes.get(e.get("target"))
        if not src or not dst:
            continue
        st, dt = src["type"], dst["type"]
        if st == "skill" or dt == "skill":
            _issue(errors, warnings, "error", "EXEC_PORT_MISMATCH",
                   "Skill 无执行端口（执行线不得触达 Skill 节点）", dst.get("id"), e["id"])
            continue
        if (st, dt) not in EXEC_ALLOWED:
            _issue(errors, warnings, "error", "EXEC_PAIR_ILLEGAL",
                   f"不支持的执行连接 {st}→{dt}", dst.get("id"), e["id"])
            continue
        exec_graph[src["id"]].append(dst["id"])
        exec_in[dst["id"]].append(src["id"])

    # ④ 环检测（执行子图，含审核；边界节点入图无碍——它们出度/入度受限）
    color: Dict[str, int] = {}
    path: List[str] = []

    def dfs(nid: str) -> Optional[List[str]]:
        color[nid] = 1
        path.append(nid)
        for nxt in exec_graph.get(nid, []):
            c = color.get(nxt, 0)
            if c == 1:
                i = path.index(nxt)
                return path[i:] + [nxt]
            if c == 0:
                cyc = dfs(nxt)
                if cyc:
                    return cyc
        path.pop()
        color[nid] = 2
        return None

    for nid in nodes:
        if color.get(nid, 0) == 0:
            cyc = dfs(nid)
            if cyc:
                _issue(errors, warnings, "error", "EXEC_CYCLE",
                       "执行图存在环: " + " → ".join(cyc), cyc[0] if cyc else None)
                break

    # ⑤⑥ 单条连通顺序链（Agent/审核子图；边界纯视觉）
    core = [nid for nid, n in nodes.items()
            if n.get("type") in ("agent", "human_review")]
    multi_out = [nid for nid in core if len(exec_graph.get(nid, [])) > 1]
    multi_in = [nid for nid in core if len(exec_in.get(nid, [])) > 1]
    for nid in multi_out:
        _issue(errors, warnings, "error", "STRUCT_BRANCH",
               "首版仅支持单条顺序链：该节点有多条执行出线（分叉）", nid)
    for nid in multi_in:
        _issue(errors, warnings, "error", "STRUCT_MERGE",
               "首版仅支持单条顺序链：该节点有多条执行入线（汇合）", nid)
    agent_nodes = [nid for nid in core if nodes[nid]["type"] == "agent"]
    if agent_nodes:
        # 连通性：所有 core 节点在同一个执行连通分量
        seen: Set[str] = set()
        stack = [core[0]]
        adj: Dict[str, Set[str]] = {nid: set() for nid in core}
        for e in exec_edges:
            s, d = e.get("source"), e.get("target")
            if s in adj and d in adj:
                adj[s].add(d)
                adj[d].add(s)
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(adj.get(cur, ()) - seen)
        disconnected = [nid for nid in core if nid not in seen]
        for nid in disconnected:
            _issue(errors, warnings, "error", "STRUCT_DISCONNECTED",
                   "存在与主链不连通的 Agent/审核节点（孤立节点）", nid)
    else:
        _issue(errors, warnings, "error", "NO_AGENT",
               "流程中没有 Agent 节点（只有边界/审核不能运行）")

    # ⑦ 审核必须有上游产物来源（执行入线），连续审核合法
    for nid in core:
        if nodes[nid]["type"] == "human_review" and not exec_in.get(nid):
            _issue(errors, warnings, "error", "REVIEW_NO_UPSTREAM",
                   "审核节点没有上游产物来源（需要执行入线）", nid)

    # ⑧ Agent 节点任务目标（运行必需；缺失=可保存草稿但启动被挡——在 preflight 单列，
    #    这里作为 warning 提示，保证「添加卡片」与「可启动任务」分离）
    for nid, n in nodes.items():
        if n.get("type") == "agent":
            cfg = n.get("config") or {}
            if not (cfg.get("objective", "") or "").strip():
                _issue(errors, warnings, "warning", "AGENT_NO_OBJECTIVE",
                       "Agent 节点未填写任务目标（启动前必填）", nid)
            # Phase 47 §4.2/§4.3：completion 契约结构校验（fail closed——
            # 非法 kind/未知字段在保存时即报错，不等运行时才发现）
            comp = cfg.get("completion")
            if comp is not None:
                from .outputs import validate_completion_config
                for msg in validate_completion_config(comp):
                    _issue(errors, warnings, "error", "COMPLETION_INVALID",
                           msg, nid)
        if n.get("type") == "human_review":
            cfg = n.get("config") or {}
            if not cfg.get("targetSourceNodeId") and not cfg.get("targetArtifact"):
                _issue(errors, warnings, "warning", "REVIEW_NO_TARGET",
                       "审核节点未指定审核对象（启动前建议填写）", nid)
            # Phase 47 §4.6：selection 配置结构校验
            sel = cfg.get("selection")
            if sel is not None:
                if not isinstance(sel, dict):
                    _issue(errors, warnings, "error", "SELECTION_INVALID",
                           "selection 必须是对象 {kind, entityId, versionId?}", nid)
                elif not sel.get("kind") or not sel.get("entityId"):
                    _issue(errors, warnings, "error", "SELECTION_INVALID",
                           "selection 需要 kind 与 entityId", nid)

    # ⑨⑩ 结构外的运行时前置（批准状态、素材可读）在 preflight_for_start 处理。

    return {"ok": not errors, "errors": errors, "warnings": warnings, "nodes": nodes,
            "edges": edges, "catalog": cat}


def preflight_for_start(definition: dict, rt, project: str) -> dict:
    """启动 preflight：在 validate_definition 之上加「运行必需字段」与「输入可读」检查。
    返回 {ok, blockers: [issue], plan_hint}。blockers 非空则拒绝启动（§4.1/§4.3.8）。"""
    v = validate_definition(definition, rt, project)
    blockers = list(v["errors"])
    nodes = v["nodes"]
    cat = v["catalog"]
    agent_cat = {a["id"]: a for a in cat["agents"]}
    skill_cat = {s["id"]: s for s in cat["skills"]}

    for nid, n in nodes.items():
        if n.get("type") == "agent":
            obj = (n.get("config") or {}).get("objective", "").strip()
            if not obj:
                blockers.append({"level": "error", "code": "RUN_NO_OBJECTIVE",
                                 "message": "节点缺少任务目标（本节点要做什么、范围是什么）",
                                 "nodeId": nid, "edgeId": None})
            a = agent_cat.get(n.get("refId"))
            if a and a.get("enabled") is False:
                blockers.append({"level": "error", "code": "RUN_AGENT_DISABLED",
                                 "message": f"Agent {a['id']} 已禁用", "nodeId": nid,
                                 "edgeId": None})
    return {"ok": not blockers, "blockers": blockers}
