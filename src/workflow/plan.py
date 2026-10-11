# src/workflow/plan.py
# 编译（PHASE46_01 §4.4/§4.5）：结构编译是确定性过程，不调用模型。
# 输出可直接审查的顺序计划（有序 node IDs + 每节点任务范围/输入来源/Skill 绑定/审核对象）
# 与启动快照（定义 hash、Agent 基线、Skill 内容 hash、模型与思考配置、canonical 基线指纹）。
#
# 启动输入账本（§4.5）：不把启动时项目 revision 永远锁死——派发前解析上游输出与所需
# canonical，记录该节点实际使用版本（executor 在每次派发时登记 canonical_reads）。
from __future__ import annotations
from typing import Dict, List, Optional, Tuple

from .store import content_hash
from .models import AGENT_EXPECTED_DOMAINS


def core_chain(v: dict) -> Tuple[List[str], Dict[str, dict], Dict[str, List[str]]]:
    """从校验结果提取 Agent/审核核心链：返回 (有序 node_ids, nodes_by_id, exec_in)。"""
    nodes = v["nodes"]
    exec_in: Dict[str, List[str]] = {nid: [] for nid in nodes}
    exec_out: Dict[str, List[str]] = {nid: [] for nid in nodes}
    for e in v.get("edges") or []:
        if e.get("type") != "execution":
            continue
        s, d = e.get("source"), e.get("target")
        if s in exec_out and d in exec_in and nodes[s]["type"] != "skill":
            exec_out[s].append(d)
            exec_in[d].append(s)
    core = [nid for nid, n in nodes.items()
            if n.get("type") in ("agent", "human_review")]
    # 起点：核心节点中无核心入边者（入边都来自边界不算）
    starts = [nid for nid in core
              if not [s for s in exec_in[nid] if nodes[s]["type"] in ("agent", "human_review")]]
    order: List[str] = []
    if starts:
        cur = starts[0]
        seen = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            order.append(cur)
            nxt = [d for d in exec_out[cur] if nodes[d]["type"] in ("agent", "human_review")]
            cur = nxt[0] if nxt else None
    return order, nodes, exec_in


def compile_plan(definition: dict, v: dict, cat: dict) -> dict:
    """编译顺序计划。调用前须 validate_definition 通过（本函数不重复结构校验）。"""
    order, nodes, exec_in = core_chain(v)
    skill_cat = {s["id"]: s for s in (cat.get("skills") or [])}
    skill_nodes = {nid: n for nid, n in nodes.items() if n["type"] == "skill"}
    # skill 节点 → 绑定到哪些 agent 节点（binding 边）
    skills_by_agent: Dict[str, List[dict]] = {}
    for e in v.get("edges") or []:
        if e.get("type") != "skill_binding":
            continue
        src, dst = nodes.get(e.get("source")), nodes.get(e.get("target"))
        if not src or not dst or src["type"] != "skill" or dst["type"] != "agent":
            continue
        s = skill_cat.get(src.get("refId")) or {}
        skills_by_agent.setdefault(dst["id"], []).append({
            "skillNodeId": src["id"],
            "skillId": src.get("refId"),
            "version": e.get("skillVersion") or s.get("version"),
            "contentHash": s.get("contentHash"),
        })

    plan_nodes: Dict[str, dict] = {}
    review_seq = 0
    for nid in order:
        n = nodes[nid]
        if n["type"] == "agent":
            cfg = n.get("config") or {}
            # Phase 47 B批：目标级完成契约随编译进入计划（completion.outputs 走
            # outputs.OUTPUT_SPEC 语义；validate 已做 fail-closed 结构校验）。
            completion = cfg.get("completion") or None
            plan_nodes[nid] = {
                "nodeId": nid, "type": "agent", "refId": n.get("refId"),
                "objective": (cfg.get("objective") or "").strip(),
                "note": cfg.get("note") or "",
                "expectedDomains": list(AGENT_EXPECTED_DOMAINS.get(n.get("refId")) or []),
                "completion": completion,
                "scope": cfg.get("scope") or None,
                "skills": skills_by_agent.get(nid, []),
                "upstreamNodeId": next(
                    (s for s in exec_in[nid]
                     if nodes[s]["type"] in ("agent", "human_review")), None),
            }
        elif n["type"] == "human_review":
            review_seq += 1
            cfg = n.get("config") or {}
            up = next((s for s in exec_in[nid]
                       if nodes[s]["type"] in ("agent", "human_review")), None)
            plan_nodes[nid] = {
                "nodeId": nid, "type": "human_review", "refId": None,
                "reviewIndex": review_seq,
                "label": n.get("label") or f"人工审核 #{review_seq}",
                "targetSourceNodeId": cfg.get("targetSourceNodeId") or up,
                "targetArtifact": cfg.get("targetArtifact") or "",
                "instructions": cfg.get("instructions") or "",
                "reviewer": cfg.get("reviewer") or "",
                # Phase 47 §4.6：审核可携带「批准并选用」目标（kind/entityId[/versionId]）；
                # 用户在审核卡片确认后由服务端走既有用户选片服务完成指针变更。
                "selection": cfg.get("selection") or None,
                # 协作流程 v2（用户 2026-10-11 定版）：前置审核卡自动放行——资产冻结
                # 复用场景下系统核验通过即继续，不再停等人工（事件全程留痕）。
                "autoApprove": bool(cfg.get("autoApprove")),
                "upstreamNodeId": up,
            }
    return {
        "planVersion": "workflow_plan_v1",
        "order": order,
        "nodes": plan_nodes,
        "reviewCount": review_seq,
        "agentCount": sum(1 for nid in order if plan_nodes[nid]["type"] == "agent"),
    }


def build_start_snapshot(definition: dict, rt, project: str, v: dict, plan: dict) -> dict:
    """启动快照（§4.5）：固定定义/注册基线/Skill 内容/模型与思考配置/canonical 基线。"""
    cat = v["catalog"]
    skill_versions: Dict[str, dict] = {}
    for nid, pn in plan["nodes"].items():
        for s in pn.get("skills") or []:
            skill_versions[s["skillId"]] = {
                "version": s.get("version"),
                "contentHash": s.get("contentHash"),
            }
    agent_baseline = {a["id"]: {"registryDigest": a["registryDigest"],
                                "enabled": a["enabled"],
                                "modelProfile": a["modelProfile"]}
                      for a in cat["agents"]}
    text_model = None
    for a in cat["agents"]:
        mp = a.get("modelProfile") or {}
        if mp.get("reasoning_model"):
            text_model = mp["reasoning_model"]
            break
    return {
        "snapshotVersion": "workflow_snapshot_v1",
        "definitionRevision": definition.get("revision"),
        "definitionHash": definition.get("contentHash"),
        "definition": definition,                      # 冻结定义全文（C12：运行中改草稿不影响 run）
        "agentBaseline": agent_baseline,
        "skillVersions": skill_versions,
        "textModel": text_model,
        "thinking": True,                              # §6：全部文本调用开启思考（固定）
        "toolPolicy": "core_tools_only",               # 核心工具必有；额外工具 v1 恒空
        "canonicalBaseline": canonical_baseline(rt, project),
    }


def canonical_baseline(rt, project: str) -> dict:
    """canonical 基线指纹（输入账本起点；不锁死内容，只记录派发时的真实状态）。"""
    import hashlib
    import json as _json
    fp: Dict[str, Optional[str]] = {}
    try:
        from src.core.store import ProjectStore
        pdir = rt._resolve_project_dir(project)
        d = ProjectStore(pdir).load()
        data = d.to_dict() if hasattr(d, "to_dict") else d
        for key in ("story_text", "characters", "scenes"):
            raw = _json.dumps(data.get(key), ensure_ascii=False, sort_keys=True)
            fp[key] = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    except Exception as e:   # 项目不可读=启动阻塞（由 preflight 层面的调用方判断）
        fp["error"] = str(e)[:200]
    try:
        pdir = rt._resolve_project_dir(project)
        rj = pdir / "output" / "resources.json"
        if rj.exists():
            raw = rj.read_bytes()
            fp["resources"] = "sha256:" + hashlib.sha256(raw).hexdigest()[:16]
    except Exception:
        pass
    return fp
