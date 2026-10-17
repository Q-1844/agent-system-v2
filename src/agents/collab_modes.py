# src/agents/collab_modes.py
# Phase55 B（任务书 §4）：协作模式注册表与 run 生命周期。
#
# 定位：模式 = 声明式运行配置，不是新 Agent 类别。所有模式共用同一套生成、
# 参考、任务、审核与统计基础（Phase55 A 统一契约）——本模块只改变
# 「谁规划、谁执行、何时委派、哪些任务可并行」，不复制任何生成代码。
#
# 五个预设（Phase54 报告 §4/§8/§10 的系统化；速度数字是观察证据，不硬编码）：
#   ① six_member          六员协作（W09 工作流模板路径，质量基线）
#   ② producer_lead       单 Producer 主执行（grant 五族，零委派纪律）
#   ③ preflight_producer  专业预铺+主执行（CD/SC 并行预铺 → producer）
#   ④ producer_escalate   主执行+困难上交（grant + 委派通道保留）
#   ⑤ combo_recommended   组合：预铺+主执行+上交（独立终审接入见 review_policy）
#
# run 语义（任务书 §4「最小配置契约」）：
#   - 启动时固定全部配置：模式定义全文 + 解析后参数 + Skill 版本 + 模型 +
#     agent 基线 + 参考策略 → snapshot（sha256）落 run 文件；改预设只影响后续 run。
#   - 参考策略写项目级 reference_policy.json（video_contract 三级解析的「活动
#     run 策略」层）——producer 模式与六员链共享同一 BOUND 基线等价契约。
#   - run 记录只做协调账本（引用 canonical，绝不替代它）；上交/审核沿用既有
#     AgentTask/审核卡/回执机制。
from __future__ import annotations
import asyncio
import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

COLLAB_MODE_SCHEMA = "collab_mode_v1"
COLLAB_RUN_SCHEMA = "collab_run_v1"
MODE_REGISTRY_VERSION = "p55_b_modes_v1"

# Phase54 实证的生产工具族（grant.py GRANT_FAMILIES 的五族全名）。
ALL_FAMILIES = ["assembly", "character_designer", "director",
                "scene_designer", "script_writer"]

_SKILL_RELAY = "full-story-relay-calibration"

# ============ producer 主执行目标模板（源自 Phase54 B1/B3 实证载荷） ============
_PRODUCER_METHOD_PREAMBLE = (
    '【工作方法】开始执行前先 skill.load "%s" 并严格遵循其制作方法'
    "（三查判据/时长配方/尾帧接续链策略均以该 Skill 正文为准）。" % _SKILL_RELAY)

_PRODUCER_STEPS = """【任务】{grant_line}请独自完成以下短片全流程生产：
① 剧本：按【故事目标】写 story {segment_count} 段（每段写清：剧情任务/起始状态/主动作/结果状态）。
② 资源：按故事新建角色，用 resource.look.prepare 完成造型参考生产（真实生成与审批）。
③ 场景：建立故事涉及的地点与各段实例状态图（SceneInstanceView）。若某视图生成反复不合格
（连续多次仍不过审），如实跳过场景锚并把决定写进最终汇报，不得悄悄跳过。
④ 分镜：逐镜绑定角色 look 与场景资源（shot.production.bind），生成首帧候选并按三查判据选定。
⑤ 视频：逐镜真实生成视频（静态 5s/动态 8s/高运动 10s；镜间用尾帧接续或 intentional cut，
由你按 Skill 判定）；每镜收货后核对回执与首中尾帧，不合格先修再交。
⑥ 装配：各段选 1 镜组成 sequence 并渲染成片候选（目标成片 {duration_hint}，横屏 16:9）。
⑦ 汇报：全部完成后给出总结——各步骤引用真实工具回执（changed_refs）、跳过项与原因、
遗留问题；不要声称未发生的操作。"""

_ESCALATE_LINE = (
    "你被项目级 scoped grant 授权直接执行全部生产工具族"
    "（script_writer/character_designer/scene_designer/director/assembly）；"
    "保留全部 *.delegate 委派通道——遇到需要专业判断的困难（内容策略反复拒绝、"
    "资产措辞过审困难、场景锚连续失败）按职责上交对应 Specialist，"
    "上交时写明任务、输入版本、所需产物与完成判定。")

_SOLO_LINE = (
    "你被项目级 scoped grant 授权直接执行全部生产工具族"
    "（script_writer/character_designer/scene_designer/director/assembly），"
    "独自完成，无需委派（委派工具仅保留兜底，正常流程不使用）。")

_CD_PREFLIGHT_TEMPLATE = """【预铺任务·角色资源】为以下短片新建角色并生产 approved Look 参考（真实生成与审批）：
{story_goal}
要求：角色装备轮廓互相明确区分，写实电影质感；每个角色至少一个 approved Look；
完成后汇报角色名/Look id/参考资产 id（引用真实回执）。"""

_SC_PREFLIGHT_TEMPLATE = """【预铺任务·场景】为以下短片建立地点与各故事段实例状态图（SceneInstanceView）：
{story_goal}
要求：Location + 各段 SceneInstance + 状态图（闸门/掩体等关键状态可辨识）；
为后续分镜提供空间锚。若某视图连续多次不过审，如实记录并继续其余视图；
完成后汇报 location/instance/sciv id（引用真实回执）。"""


def _producer_mode(mode_id: str, preset_label: str, name: str, description: str,
                   *, delegates: bool, preflight: Optional[dict],
                   grant_line: str, review_profile: str, notes: str) -> dict:
    return {
        "schema": COLLAB_MODE_SCHEMA,
        "mode_id": mode_id,
        "mode_version": "1.0.0",
        "name": name,
        "preset_label": preset_label,
        "description": description,
        "execution": {"strategy": "producer_lead"},
        "grant_families": sorted(ALL_FAMILIES),
        "delegates_enabled": delegates,
        "preflight": preflight,
        "escalation": {"enabled": delegates},
        "reference_policy": "bound_required",   # 基线等价（任务书 §3）；实验策略须显式注册并标注
        "review_policy": {"profile": review_profile},
        "concurrency": {"media_inflight": 1},   # Batch C 起可配并逐级验证
        "context_policy": {"profile": "single_session_monitored"},
        "producer_objective_template": "\n\n".join([
            _PRODUCER_METHOD_PREAMBLE,
            # grant_line 定义期烘焙（replace，不动其它运行期占位符）
            _PRODUCER_STEPS.replace("{grant_line}", grant_line),
            "【故事目标】{story_goal}"]),
        "preflight_objective_templates": {
            "character_designer": _CD_PREFLIGHT_TEMPLATE,
            "scene_designer": _SC_PREFLIGHT_TEMPLATE,
        } if preflight else {},
        "notes": notes,
    }


COLLAB_MODES: Dict[str, dict] = {
    "six_member": {
        "schema": COLLAB_MODE_SCHEMA,
        "mode_id": "six_member",
        "mode_version": "1.0.0",
        "name": "六员协作",
        "preset_label": "①",
        "description": "按已有专业分工委派（W09 工作流模板：质量基线、职责清楚、便于回归）",
        "execution": {"strategy": "workflow_template", "template_id": "W09"},
        "grant_families": [],
        "delegates_enabled": True,
        "preflight": None,
        "escalation": {"enabled": True,
                       "note": "节点间审核卡即上交通道（workflow 引擎既有语义）"},
        "reference_policy": "bound_required",
        "review_policy": {"profile": "workflow_nodes"},
        "concurrency": {"media_inflight": 1},
        "context_policy": {"profile": "per_node"},
        "notes": "必需输入：故事目标。不强制每个动作都完整遍历六员——节点编排由模板定义。",
    },
    "producer_lead": _producer_mode(
        "producer_lead", "②", "Producer 主执行",
        "单 Producer 持 scoped grant 直接执行全流程（少交接，适合计划已明确的例行制作）",
        delegates=False, preflight=None, grant_line=_SOLO_LINE,
        review_profile="self_review_plus_final",
        notes="必须具备会话接力（Batch D）与共用审核；Phase54-B1 实证 467k input 撞 "
              "524k 上限——长片必须监控预算。"),
    "preflight_producer": _producer_mode(
        "preflight_producer", "③", "专业预铺+主执行",
        "角色/场景专业任务依赖允许时并行预铺，之后 Producer 主执行（保留专业素材能力）",
        delegates=True,
        preflight={"roles": ["character_designer", "scene_designer"],
                   "parallel": True},
        grant_line=_ESCALATE_LINE,
        review_profile="self_review_plus_final",
        notes="先有稳定制作简报/故事依据，避免泛做大量无用素材；Phase54-B3 实证资产"
              "措辞能力红利（CD 过审怪物 look vs ②放弃）。"),
    "producer_escalate": _producer_mode(
        "producer_escalate", "④", "主执行+困难上交",
        "Producer 主执行，明确困难按职责交回对应 Specialist（减少常规委派，保留兜底）",
        delegates=True, preflight=None, grant_line=_ESCALATE_LINE,
        review_profile="self_review_plus_final",
        notes="「未上交」不作为质量或鲁棒性证明；上交=明确任务+输入版本+所需产物+完成判定。"),
    "combo_recommended": _producer_mode(
        "combo_recommended", "⑤", "组合：预铺+主执行+上交",
        "引用③预铺+④上交+独立终审（复测报告推荐组合；不复制另一套实现）",
        delegates=True,
        preflight={"roles": ["character_designer", "scene_designer"],
                   "parallel": True},
        grant_line=_ESCALATE_LINE,
        review_profile="independent_final_review",
        notes="Phase54 §8.3/§10 推荐组合的速度优势为观察证据；质量以六员链为锚"
              "（用户 10-16/10-17 两轮评审：四种新模式均未达基线）。"),
}


# ============ 注册表读取与校验 ============
def list_modes() -> List[dict]:
    """注册表快照（声明值，不含 run 时状态）。"""
    return [json.loads(json.dumps(m, ensure_ascii=False))
            for m in COLLAB_MODES.values()]


def get_mode(mode_id: str) -> Optional[dict]:
    return COLLAB_MODES.get(mode_id)


def validate_start(mode_id: str, params: dict) -> List[str]:
    """启动前组合校验（合法组合约束；返回错误列表，空=可启动）。"""
    mode = COLLAB_MODES.get(mode_id)
    if mode is None:
        return [f"unknown collab mode: {mode_id}（可用：{sorted(COLLAB_MODES)}）"]
    errors: List[str] = []
    params = params or {}
    if not (params.get("story_goal") or "").strip():
        errors.append("story_goal 必填（稳定制作简报/故事依据——预铺与主执行共同输入）")
    exec_strategy = mode["execution"]["strategy"]
    if exec_strategy == "workflow_template" and not mode["execution"].get("template_id"):
        errors.append("mode 配置错误：workflow_template 缺 template_id")
    if exec_strategy == "producer_lead":
        families = mode.get("grant_families") or []
        unknown = [f for f in families if f not in ALL_FAMILIES]
        if unknown:
            errors.append(f"mode 配置错误：未知 grant families {unknown}")
        preflight = mode.get("preflight")
        if preflight:
            for r in preflight.get("roles") or []:
                if r not in ("character_designer", "scene_designer"):
                    errors.append(
                        f"预铺角色 {r} 不在预铺支持清单（character_designer/"
                        "scene_designer）——避免泛做其它专业素材")
            if preflight.get("roles") and not (params.get("story_goal") or "").strip():
                errors.append("预铺模式必须提供 story_goal")
    # 参考策略：非基线策略必须显式声明为实验（本注册表暂不注册实验预设）
    if mode.get("reference_policy") not in ("bound_required", "legacy_allowed"):
        errors.append("mode 配置错误：reference_policy 非法")
    if mode.get("reference_policy") == "legacy_allowed" and not mode.get("experimental"):
        errors.append("非实验预设不得默认 legacy 参考策略（基线等价）")
    return errors


# ============ run 存储（协调账本；引用 canonical，不替代它） ============
def _runs_dir(pdir: Path) -> Path:
    return Path(pdir) / "output" / "agents" / "collab_runs"


def _run_path(pdir: Path, run_id: str) -> Path:
    return _runs_dir(pdir) / f"{run_id}.json"


def load_run(pdir: Path, run_id: str) -> Optional[dict]:
    p = _run_path(pdir, run_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_run(pdir: Path, run: dict) -> dict:
    p = _runs_dir(pdir)
    p.mkdir(parents=True, exist_ok=True)
    tmp = _run_path(pdir, run["run_id"]).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_run_path(pdir, run["run_id"]))
    return run


def list_runs(pdir: Path) -> List[dict]:
    out = []
    if not _runs_dir(pdir).exists():
        return out
    for p in sorted(_runs_dir(pdir).glob("run_*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return sorted(out, key=lambda r: -(r.get("created_at") or 0))


def _mutate_run(pdir: Path, run_id: str, fn) -> Optional[dict]:
    run = load_run(pdir, run_id)
    if run is None:
        return None
    fn(run)
    return save_run(pdir, run)


def _step(run: dict, step_id: str) -> dict:
    for s in run.get("steps") or []:
        if s["step_id"] == step_id:
            return s
    s = {"step_id": step_id, "status": "pending", "started_at": None,
         "finished_at": None, "detail": {}}
    run.setdefault("steps", []).append(s)
    return s


def _begin_step(run: dict, step_id: str, **detail) -> None:
    s = _step(run, step_id)
    s["status"] = "running"
    s["started_at"] = time.time()
    s["detail"].update(detail)


def _finish_step(run: dict, step_id: str, *, ok: bool, **detail) -> None:
    s = _step(run, step_id)
    s["status"] = "done" if ok else "failed"
    s["finished_at"] = time.time()
    s["detail"].update(detail)


# ============ 快照（任务书 §4：run 启动固定全部配置） ============
def _build_snapshot(rt, pdir: Path, project: str, mode: dict, params: dict) -> dict:
    """模式定义全文 + 解析参数 + Skill 版本 + 模型 + agent 基线。"""
    skill_versions: Dict[str, str] = {}
    try:
        from .skill_system.registry import get_skill_registry
        for sid_, pkg in (get_skill_registry().skills() or {}).items():
            skill_versions[sid_] = getattr(pkg, "version", "") or ""
    except Exception:
        skill_versions = {}
    agents = {}
    try:
        for aid in (rt.agents or {}).keys():
            agents[aid] = {"enabled": rt._is_enabled(aid)}
    except Exception:
        agents = {}
    snapshot = {
        "schema": "collab_run_snapshot_v1",
        "mode": mode,
        "params": dict(params or {}),
        "skill_versions": skill_versions,
        "agent_baseline": agents,
        "registry_version": MODE_REGISTRY_VERSION,
        "taken_at": time.time(),
    }
    return snapshot


def snapshot_hash(snapshot: dict) -> str:
    blob = json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()[:16]


# ============ 启动 ============
async def start_collab_run(rt, project: str, mode_id: str, params: dict,
                           *, granted_by: str = "operator",
                           thinking: bool = False) -> dict:
    """启动一次协作模式 run：校验 → 快照 → 参考策略落盘 → grant → 后台执行。
    立即返回 run 记录（status=running）；进度经 GET /collab-runs/{id} 查询。"""
    pdir = rt._resolve_project_dir(project)
    params = dict(params or {})
    errors = validate_start(mode_id, params)
    if errors:
        raise ValueError("；".join(errors))
    mode = COLLAB_MODES[mode_id]
    run_id = f"run_{secrets.token_hex(5)}"
    snapshot = _build_snapshot(rt, pdir, project, mode, params)
    run = {
        "schema": COLLAB_RUN_SCHEMA,
        "run_id": run_id,
        "project": project,
        "mode_id": mode_id,
        "mode_version": mode["mode_version"],
        "status": "running",
        "created_at": time.time(),
        "granted_by": granted_by,
        "snapshot_hash": snapshot_hash(snapshot),
        "snapshot": snapshot,
        "steps": [],
        "session_ids": {},
        "grant_ids": [],
        "workflow_run_id": None,
        "result": None,
    }
    # 参考策略：活动 run 层（video_contract 三级解析）——producer 模式与六员链
    # 共享 BOUND 基线等价；run 结束不自动清除（策略属于项目当前生产契约，
    # 显式经 set_project_reference_policy 变更）。
    from src.generation.video_contract import set_project_reference_policy
    set_project_reference_policy(pdir, mode["reference_policy"],
                                 source="mode_run", run_id=run_id)
    save_run(pdir, run)
    asyncio.get_running_loop().create_task(
        _execute_run(rt, pdir, project, run, thinking=thinking))
    return run


# ============ 执行 ============
async def _execute_run(rt, pdir: Path, project: str, run: dict, *,
                       thinking: bool = False) -> None:
    mode = run["snapshot"]["mode"]
    params = run["snapshot"]["params"]
    strategy = mode["execution"]["strategy"]
    try:
        if strategy == "workflow_template":
            await _run_workflow_template(rt, pdir, project, run)
        elif strategy == "producer_lead":
            await _run_producer_lead(rt, pdir, project, run, thinking=thinking)
        else:
            raise ValueError(f"unknown execution strategy: {strategy}")
        run["status"] = "completed"
        run["finished_at"] = time.time()
        save_run(pdir, run)
    except asyncio.CancelledError:
        run["status"] = "cancelled"
        run["finished_at"] = time.time()
        save_run(pdir, run)
        raise
    except Exception as e:   # noqa: BLE001 —— run 失败必须落档（不静默）
        run["status"] = "failed"
        run["finished_at"] = time.time()
        run["result"] = {"error": str(e)[:500]}
        save_run(pdir, run)


async def _run_workflow_template(rt, pdir: Path, project: str, run: dict) -> None:
    """①六员协作：W09 模板实例化 → 定义保存 → WorkflowEngine 启动（复用既有
    全部审核卡/委托/恢复机制；本执行器只做实例化与启动，不做节点调度）。"""
    from src.workflow.templates import create_from_template
    from src.workflow.store import DefinitionStore
    from src.workflow.validate import validate_definition, preflight_for_start
    from src.workflow.plan import compile_plan, build_start_snapshot
    mode = run["snapshot"]["mode"]
    params = run["snapshot"]["params"]
    _begin_step(run, "instantiate", template_id=mode["execution"]["template_id"])
    save_run(pdir, run)
    wid = f"collab_{run['run_id']}"
    definition = create_from_template(mode["execution"]["template_id"], wid,
                                      {"producerObjective":
                                       f"拆解生产目标为制作计划：{params.get('story_goal','')[:200]}"})
    definition["projectId"] = project
    definition["workflowId"] = wid
    v = validate_definition(definition, rt, project)
    if not v["ok"]:
        raise ValueError("定义校验失败：" + "；".join(v["errors"][:4]))
    DefinitionStore(pdir, project).save(wid, definition, None)
    pre = preflight_for_start(definition, rt, project)
    if not pre["ok"]:
        raise ValueError("启动被阻止：" + "；".join(
            f"{b.get('nodeId') or ''}{b.get('message')}"
            for b in pre["blockers"][:6]))
    plan = compile_plan(definition, v, v["catalog"])
    snapshot = build_start_snapshot(definition, rt, project, v, plan)
    from src.workflow.executor import get_engine
    engine = get_engine()
    out = engine.start_run(pdir, project, definition, v, plan, snapshot,
                           f"collab_{run['run_id']}")
    _finish_step(run, "instantiate", ok=True, workflow_id=wid,
                 workflow_run_id=out["run"]["runId"],
                 reused=bool(out.get("reused")))
    run["workflow_run_id"] = out["run"]["runId"]
    save_run(pdir, run)


async def _run_specialist_preflight(rt, pdir: Path, project: str, run: dict,
                                    thinking: bool) -> dict:
    """③/⑤ 预铺步：预铺角色并行各 1 turn（依赖允许=不同写域+共同 story 依据）。"""
    mode = run["snapshot"]["mode"]
    params = run["snapshot"]["params"]
    story = params.get("story_goal", "")
    roles = (mode.get("preflight") or {}).get("roles") or []
    parallel = bool((mode.get("preflight") or {}).get("parallel", True))
    templates = mode.get("preflight_objective_templates") or {}
    results: Dict[str, dict] = {}

    async def _one(role: str) -> None:
        s = rt.get_or_create_session(project, role, "project", None)
        sid = s["session_id"] if isinstance(s, dict) else getattr(s, "session_id", None)
        run["session_ids"][f"preflight_{role}"] = sid
        content = (templates.get(role) or "{story_goal}").format(story_goal=story)
        res = await rt.unified_turn(project, sid, content, thinking=thinking)
        results[role] = {"session_id": sid,
                         "assistant": (res.get("assistant_message") or "")[:400],
                         "changed_refs": res.get("changed_refs") or []}

    if parallel and len(roles) > 1:
        await asyncio.gather(*[_one(r) for r in roles])
    else:
        for r in roles:
            await _one(r)
    return results


async def _run_producer_lead(rt, pdir: Path, project: str, run: dict, *,
                             thinking: bool = False) -> None:
    """②③④⑤ 共用执行体：预铺（可选，可并行）→ scoped grant → producer 主执行 turn。
    全部复用既有 runtime/grant/session/turn 机制——本函数只做编排，不做生成。"""
    from . import grant as grant_mod
    mode = run["snapshot"]["mode"]
    params = run["snapshot"]["params"]

    # 1. 预铺（可选；依赖允许时并行）
    if mode.get("preflight"):
        _begin_step(run, "preflight",
                    roles=mode["preflight"].get("roles"),
                    parallel=mode["preflight"].get("parallel", True))
        save_run(pdir, run)
        try:
            pre = await _run_specialist_preflight(rt, pdir, project, run, thinking)
            _finish_step(run, "preflight", ok=True, results=pre)
        except Exception as e:   # noqa: BLE001 —— 预铺失败如实记档并中止（不静默跳过）
            _finish_step(run, "preflight", ok=False, error=str(e)[:300])
            raise
        save_run(pdir, run)

    # 2. scoped grant（模式配置的族；TTL 12h 与 Phase54 实证一致）
    if mode.get("grant_families"):
        _begin_step(run, "grant", families=mode["grant_families"])
        save_run(pdir, run)
        g = grant_mod.grant(pdir, "producer", mode["grant_families"],
                            reason=f"collab mode {run['mode_id']} run {run['run_id']}",
                            granted_by=run.get("granted_by") or "operator",
                            ttl_s=43200)
        run["grant_ids"].append(g["grantId"])
        _finish_step(run, "grant", ok=True, grant_id=g["grantId"],
                     expires_at=g.get("expiresAt"))
        save_run(pdir, run)

    # 3. producer 主执行（单 turn 全载荷；D-12 教训：完整目标一次下发）。
    # Phase55 D：预算接力自动化——turn 返回 handoff 时自动 relay 新会话续跑
    #（把 Phase54 人工 session-split 变成机制；上限 3 次，超限如实完成并留提示）。
    _begin_step(run, "producer_turn", delegates_enabled=mode.get("delegates_enabled"))
    save_run(pdir, run)
    s = rt.get_or_create_session(project, "producer", "project", None)
    sid = s["session_id"] if isinstance(s, dict) else getattr(s, "session_id", None)
    run["session_ids"]["producer"] = sid
    save_run(pdir, run)
    content = mode["producer_objective_template"].format(
        story_goal=params.get("story_goal", ""),
        segment_count=params.get("segment_count", "六"),
        duration_hint=params.get("duration_hint", "30-42 秒"))
    res = await rt.unified_turn(project, sid, content, thinking=thinking)
    relays = 0
    while (res or {}).get("handoff") and relays < 3:
        relays += 1
        from . import handoff as handoff_mod
        _begin_step(run, f"relay_{relays}", from_session=sid,
                    handoff_id=(res.get("handoff") or {}).get("handoff_id"))
        save_run(pdir, run)
        relayed = handoff_mod.relay_producer_session(
            rt, project, sid, reason="下一请求预算接近 provider 上限（自动接力）",
            run_id=run["run_id"])
        sid = relayed["new_session_id"]
        run["session_ids"][f"producer_relay_{relays}"] = sid
        _finish_step(run, f"relay_{relays}", ok=True,
                     to_session=sid, generation=(relayed.get("lease") or {}).get("generation"))
        _begin_step(run, f"producer_turn_relay_{relays}", session_id=sid)
        save_run(pdir, run)
        res = await rt.unified_turn(project, sid, content, thinking=thinking)
        _finish_step(run, f"producer_turn_relay_{relays}", ok=True,
                     assistant=(res.get("assistant_message") or "")[:400])
    if (res or {}).get("handoff"):
        run["result_handoff_pending"] = {
            "note": "接力 3 次后仍触发预算交接——run 如实完成并留待续跑",
            **(res.get("handoff") or {})}
    _finish_step(run, "producer_turn", ok=True,
                 assistant=(res.get("assistant_message") or "")[:800],
                 receipts=len(res.get("receipts") or []),
                 changed_refs=(res.get("changed_refs") or [])[:20],
                 relays=relays,
                 usage_totals=res.get("usage_totals"))
    run["result"] = {
        "producer_session_id": sid,
        "assistant_excerpt": (res.get("assistant_message") or "")[:2000],
        "events": len(res.get("events") or []),
        "usage_totals": res.get("usage_totals"),
    }
    save_run(pdir, run)

    # 4. 独立终审（review_policy.independent_final_review；Phase55 D 审核流水化）
    if (mode.get("review_policy") or {}).get("profile") == "independent_final_review":
        from .review_pipeline import run_independent_review
        _begin_step(run, "independent_final_review")
        save_run(pdir, run)
        try:
            rv = await run_independent_review(rt, pdir, project,
                                              reason=f"collab run {run['run_id']}")
            _finish_step(run, "independent_final_review", ok=True, **{
                k: rv.get(k) for k in ("batch_id", "shots_reviewed",
                                       "hard_error_shots", "report_path")})
            run["review"] = rv
        except Exception as e:   # noqa: BLE001 —— 审核失败如实落档（不冒充通过）
            _finish_step(run, "independent_final_review", ok=False,
                         error=str(e)[:300])
        save_run(pdir, run)


# ============ 查询与停止 ============
def cancel_run(rt, pdir: Path, run_id: str, reason: str = "") -> dict:
    """协作停止：run 标 cancelled + 置位其会话的 turn cancel_event（turn 在步骤
    边界安全停下；已完成写保留）。已终态 run 幂等返回。"""
    run = load_run(pdir, run_id)
    if run is None:
        raise ValueError(f"unknown collab run: {run_id}")
    if run["status"] in ("completed", "failed", "cancelled"):
        return {"stopped": False, "status": run["status"]}
    from . import turn_registry
    for sid in (run.get("session_ids") or {}).values():
        if sid:
            ev = turn_registry._ACTIVE.get(turn_registry.key(run["project"], sid))
            if ev is not None:
                ev.set()
    run["status"] = "cancelled"
    run["finished_at"] = time.time()
    run["result"] = {"cancel_reason": (reason or "")[:300]}
    save_run(pdir, run)
    return {"stopped": True, "status": "cancelled"}
