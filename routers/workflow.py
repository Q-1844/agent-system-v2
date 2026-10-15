# routers/workflow.py
# Phase 46：可视化工作流后端 API（PHASE46_01 §8.1 语义表落地）。
# 所有查询/保存/校验都不派发生产任务；只有 start/approve/continue 推动执行。
# 错误响应区分结构/版本/权限/前置素材/未知提交，不以 200 包裹全部错误。
from __future__ import annotations
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from deps import get_project_manager
from src.agents.runtime import get_agent_runtime, AgentError
from src.workflow import models as M
from src.workflow.catalog import build_catalog
from src.workflow.executor import WorkflowEngine, WorkflowError, get_engine
from src.workflow.plan import build_start_snapshot, compile_plan
from src.workflow.store import DefinitionStore, EventLog, RevisionConflict, RunStore
from src.workflow.validate import preflight_for_start, validate_definition

log = logging.getLogger("routers.workflow")
router = APIRouter()

_HTTP = {
    "unknown_project": 404, "unknown_run": 404, "unknown_review": 404,
    "unknown_node": 404, "unknown_workflow": 404,
    "revision_conflict": 409, "idempotency_conflict": 409,
    "waiting_human": 409, "run_terminal": 409, "stopping": 409,
    "not_retryable": 409, "review_object_changed": 409, "bad_decision": 400,
    "invalid_definition": 422, "start_blocked": 422, "disabled_agent": 400,
    "selection_required": 409, "outputs_missing": 422,
}


def _pdir(project_name: str) -> Path:
    try:
        return get_project_manager().get_project_dir(project_name)
    except FileNotFoundError:
        raise HTTPException(404, f"项目不存在: {project_name}")


def _err(e: Exception):
    if isinstance(e, WorkflowError):
        raise HTTPException(_HTTP.get(e.code, 400), f"[{e.code}] {e}")
    if isinstance(e, AgentError):
        raise HTTPException(_HTTP.get(e.code, 400), f"[{e.code}] {e.message}")
    if isinstance(e, RevisionConflict):
        raise HTTPException(409, f"[revision_conflict] 当前服务端 revision={e.current}：{e}")
    raise HTTPException(500, f"[internal] {type(e).__name__}: {str(e)[:300]}")


# ============ 请求模型 ============
class SaveDefinitionReq(BaseModel):
    definition: Optional[dict] = None          # 缺省=用已保存定义（validate/compile）
    expectedRevision: Optional[int] = None


class StartRunReq(BaseModel):
    definition: Optional[dict] = None          # 缺省=用已保存定义
    idempotencyKey: str
    expectedRevision: Optional[int] = None
    saveIfChanged: bool = True                 # 带定义时先保存（走 revision 冲突保护）
    allowedShotIds: Optional[List[str]] = None  # Phase51 E：生成范围闸（run 全程清单外视频提交预检拒绝；语义同 repair_scope，防导演越界重生成烧配额）


class ReviewDecisionReq(BaseModel):
    decision: str                              # approve | reject
    note: str = ""
    expectedFingerprint: Optional[str] = None
    # Phase 47 §4.6：批准并选用——用户在审核卡片显式确认后才置 true；
    # 服务端经 selection.perform_selection 走既有用户入口同一底层服务。
    withSelection: bool = False
    selectionOverride: Optional[dict] = None   # 覆盖默认 selection 目标（必须精确版本）


class ContinueReq(BaseModel):
    nodeId: Optional[str] = None
    # Phase51 C：修复范围服务端硬闸——rejected 修复时传入允许生成的镜头清单，
    # 清单外镜头的生成提交在 director_visual 预检被拒（不进队列不耗配额）。
    allowedShotIds: Optional[List[str]] = None


# ============ A批：动态组件目录 ============
@router.get("/api/project/{project_name}/workflow/catalog")
async def workflow_catalog(project_name: str):
    try:
        rt = get_agent_runtime()
        _pdir(project_name)   # 项目存在性
        return build_catalog(rt, project_name)
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


# ============ Phase 47 C批：模板（从模板新建走保存/校验同一路径） ============
@router.get("/api/project/{project_name}/workflow/templates")
async def workflow_templates(project_name: str):
    try:
        from src.workflow.templates import TEMPLATE_VERSION, list_templates
        _pdir(project_name)
        return {"templates": list_templates(), "templateVersion": TEMPLATE_VERSION}
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


class TemplateCreateReq(BaseModel):
    workflowId: str
    params: Optional[dict] = None


@router.post("/api/project/{project_name}/workflow/templates/{template_id}/create")
async def workflow_template_create(project_name: str, template_id: str,
                                   req: TemplateCreateReq):
    """从模板复制产生新工作流定义并保存（revision 1）。
    保存走与手工搭建相同的校验路径（错误草稿可存、启动必挡）；
    definition.template 记录来源模板与版本。"""
    try:
        from src.workflow.templates import create_from_template
        pdir = _pdir(project_name)
        wid = (req.workflowId or "").strip()
        if not wid:
            raise WorkflowError("invalid_definition", "workflowId 必填")
        definition = create_from_template(template_id, wid, req.params)
        definition["projectId"] = project_name
        v = validate_definition(definition, get_agent_runtime(), project_name)
        doc = DefinitionStore(pdir, project_name).save(wid, definition, None)
        return {"workflowId": wid, "revision": doc["revision"],
                "contentHash": doc["contentHash"],
                "template": definition.get("template"),
                "validation": {"ok": v["ok"], "errors": v["errors"],
                               "warnings": v["warnings"]},
                "definition": doc}
    except HTTPException:
        raise
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        _err(e)


# ============ B批：定义保存/读取（revision 冲突保护） ============
@router.get("/api/project/{project_name}/workflow/definitions/{workflow_id}")
async def get_definition(project_name: str, workflow_id: str):
    try:
        store = DefinitionStore(_pdir(project_name), project_name)
        doc = store.load(workflow_id)
        if doc is None:
            raise HTTPException(404, f"工作流定义不存在: {workflow_id}")
        return doc
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


@router.put("/api/project/{project_name}/workflow/definitions/{workflow_id}")
async def save_definition(project_name: str, workflow_id: str, req: SaveDefinitionReq):
    """保存定义。expectedRevision=None=首次创建。返回 {revision, updatedAt, contentHash}。
    保存前做后端结构校验（错误仍允许保存草稿，但响应携带 issues——与前端一致：
    草稿可存，启动必挡）。"""
    try:
        pdir = _pdir(project_name)
        store = DefinitionStore(pdir, project_name)
        definition = dict(req.definition or {})
        definition["projectId"] = project_name
        definition["workflowId"] = workflow_id
        v = validate_definition(definition, get_agent_runtime(), project_name)
        doc = store.save(workflow_id, definition, req.expectedRevision)
        return {"workflowId": workflow_id, "revision": doc["revision"],
                "updatedAt": doc["updatedAt"], "contentHash": doc["contentHash"],
                "validation": {"ok": v["ok"], "errors": v["errors"],
                               "warnings": v["warnings"]}}
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


# ============ B批：校验 / 编译（无执行副作用） ============
@router.post("/api/project/{project_name}/workflow/definitions/{workflow_id}/dry_run")
async def dry_run_definition(project_name: str, workflow_id: str):
    """Phase51 C：启动前干跑（零 LLM / 零生成 / 只读）——逐节点回答
    「若现在派发会发生什么」：完成规格现状核对、选用候选解析、装配素材可用性。
    把 run_36919eb5a073 类的"开跑后才撞"问题（审核卡 0 候选/规格脱节）前置暴露。"""
    try:
        pdir = _pdir(project_name)
        definition = DefinitionStore(pdir, project_name).load(workflow_id)
        if definition is None:
            raise HTTPException(404, f"工作流定义不存在: {workflow_id}")
        definition = dict(definition)
        definition["projectId"] = project_name
        definition["workflowId"] = workflow_id
        rt = get_agent_runtime()
        v = validate_definition(definition, rt, project_name)
        if not v.get("ok"):
            return {"ok": False, "stage": "validate",
                    "errors": v.get("errors", []), "nodes": []}
        from src.workflow.plan import compile_plan
        plan = compile_plan(definition, v, v["catalog"])
        from src.workflow.dry_run import dry_run
        out = dry_run(pdir, project_name, plan)
        return out
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


@router.post("/api/project/{project_name}/workflow/definitions/{workflow_id}/validate")
async def validate_workflow(project_name: str, workflow_id: str,
                            req: SaveDefinitionReq):
    try:
        definition = req.definition
        if definition is None:
            definition = DefinitionStore(_pdir(project_name), project_name).load(workflow_id)
            if definition is None:
                raise HTTPException(404, f"工作流定义不存在: {workflow_id}")
        v = validate_definition(definition, get_agent_runtime(), project_name)
        return {"ok": v["ok"], "errors": v["errors"], "warnings": v["warnings"]}
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


@router.post("/api/project/{project_name}/workflow/definitions/{workflow_id}/compile")
async def compile_workflow(project_name: str, workflow_id: str,
                           req: SaveDefinitionReq):
    """编译顺序计划（确定性，无模型调用，无副作用）。结构非法时返回 blockers。"""
    try:
        definition = req.definition
        if definition is None:
            definition = DefinitionStore(_pdir(project_name), project_name).load(workflow_id)
            if definition is None:
                raise HTTPException(404, f"工作流定义不存在: {workflow_id}")
        definition = dict(definition)
        definition["projectId"] = project_name
        definition["workflowId"] = workflow_id
        v = validate_definition(definition, get_agent_runtime(), project_name)
        if not v["ok"]:
            return {"ok": False, "plan": None, "blockers": v["errors"]}
        plan = compile_plan(definition, v, v["catalog"])
        return {"ok": True, "plan": plan, "blockers": []}
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


# ============ D批：启动 run（幂等键；尽快返回已持久化 run） ============
@router.post("/api/project/{project_name}/workflow/definitions/{workflow_id}/runs")
async def start_run(project_name: str, workflow_id: str, req: StartRunReq):
    try:
        pdir = _pdir(project_name)
        rt = get_agent_runtime()
        dstore = DefinitionStore(pdir, project_name)
        definition = req.definition
        if definition is not None and req.saveIfChanged:
            definition = dict(definition)
            definition["projectId"] = project_name
            definition["workflowId"] = workflow_id
            v0 = validate_definition(definition, rt, project_name)
            definition = dstore.save(workflow_id, definition, req.expectedRevision)
        else:
            definition = dstore.load(workflow_id)
            if definition is None:
                raise WorkflowError("unknown_workflow",
                                    f"工作流定义不存在且请求未携带定义: {workflow_id}")
        definition = dict(definition)
        definition["projectId"] = project_name
        definition["workflowId"] = workflow_id

        # 启动 preflight（§4.3.8/§4.1：缺目标/禁用 Agent 等具体缺项）
        pre = preflight_for_start(definition, rt, project_name)
        if not pre["ok"]:
            raise WorkflowError("start_blocked",
                                "启动被阻止：" + "；".join(
                                    f"{b.get('nodeId') or ''}{b.get('message')}"
                                    for b in pre["blockers"][:6]))
        v = validate_definition(definition, rt, project_name)
        plan = compile_plan(definition, v, v["catalog"])
        snapshot = build_start_snapshot(definition, rt, project_name, v, plan)
        engine = get_engine()
        out = engine.start_run(pdir, project_name, definition, v, plan, snapshot,
                               req.idempotencyKey)
        run = out["run"]
        # Phase51 E：启动级生成范围闸——同 repair_scope 硬闸代码路径（导演提交
        # 预检 check_submission），run 全程约束生成只落在清单内镜头（C-T03 att3/att4
        # 两次实证提示词范围约束对导演无效）。worker 退出时由 executor 统一解除。
        if req.allowedShotIds and not out["reused"]:
            from src.workflow.repair_scope import set_scope
            video_node = next((n["id"] for n in definition.get("nodes") or []
                               if n.get("type") == "agent" and n.get("refId") == "director"
                               and (n.get("config") or {}).get("completion", {})
                               .get("strategy") == "require_new"), None)
            set_scope(project_name, req.allowedShotIds, video_node or "n_dir_video",
                      run["runId"], reason="start_scope")
            # 持久化到 run 记录（服务重启后 restore_from_run 重注册）
            RunStore(pdir, project_name).update(
                run["runId"],
                lambda r: r.__setitem__("startScope", {
                    "allowedShotIds": [str(x) for x in req.allowedShotIds],
                    "nodeId": video_node, "reason": "start_scope"}))
            EventLog(pdir, run["runId"]).append(
                run["runId"], M.EV_SCOPE_SET, nodeId=video_node,
                allowedShotIds=[str(x) for x in req.allowedShotIds],
                reason="start_scope")
            log.info("start_run scope SET run=%s n_allowed=%d",
                     run["runId"], len(req.allowedShotIds))
        # 尽快返回已持久化 run；执行由后台 worker 推进（启动响应不等模型）
        engine.drive(pdir, project_name, run["runId"])
        return {"run": _project_run(run), "reused": out["reused"]}
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


# ============ D批：查询 run / 事件 ============
def _project_run(run: dict) -> dict:
    """run 投影：全量返回（单链规模小；前端运行视图直接消费）。"""
    return run


@router.get("/api/project/{project_name}/workflow/runs")
async def list_runs(project_name: str, status: Optional[str] = None):
    try:
        runs = RunStore(_pdir(project_name), project_name)
        return {"runs": runs.list(status)}
    except Exception as e:
        _err(e)


@router.get("/api/project/{project_name}/workflow/runs/{run_id}")
async def get_run(project_name: str, run_id: str):
    try:
        run = RunStore(_pdir(project_name), project_name).load(run_id)
        if run is None:
            raise HTTPException(404, f"run 不存在: {run_id}")
        run = dict(run)
        run["workerRunning"] = get_engine().worker_running(run_id)
        return run
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


@router.get("/api/project/{project_name}/workflow/runs/{run_id}/events")
async def get_run_events(project_name: str, run_id: str, after: int = 0,
                         limit: int = 500):
    try:
        _pdir(project_name)
        ev = EventLog(_pdir(project_name), run_id).read(after=after, limit=limit)
        return {"events": ev, "lastSeq": ev[-1]["seq"] if ev else after}
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


# ============ D批：审核决策 / 显式继续 / 停止 ============
@router.post("/api/project/{project_name}/workflow/runs/{run_id}/reviews/{request_id}/decision")
async def review_decision(project_name: str, run_id: str, request_id: str,
                          req: ReviewDecisionReq):
    try:
        engine = get_engine()
        pdir = _pdir(project_name)
        out = engine.decide_review(pdir, project_name, run_id,
                                   request_id, req.decision, req.note,
                                   req.expectedFingerprint,
                                   with_selection=req.withSelection,
                                   selection_override=req.selectionOverride)
        # 通过并选用 → 服务端立即执行选用（事件循环内 await 同一 project 锥；
        # 失败时节点 needs_reconciliation，可用 continue 幂等重试）
        if out.get("selectionPending"):
            nid = next((rv["nodeId"] for rv in (RunStore(pdir, project_name)
                                                .load(run_id).get("reviews") or [])
                        if rv.get("requestId") == request_id), None)
            if nid:
                await engine.execute_pending_selection(pdir, project_name,
                                                       run_id, nid)
        # 通过 → 下一节点唯一启动（在事件循环内驱动；驳回已停止派发，驱动无害）
        if not out.get("alreadyDecided"):
            engine.drive(pdir, project_name, run_id)
        run = RunStore(pdir, project_name).load(run_id)
        return {**out, "run": run}
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


@router.post("/api/project/{project_name}/workflow/runs/{run_id}/continue")
async def continue_run(project_name: str, run_id: str, req: ContinueReq):
    try:
        engine = get_engine()
        pdir = _pdir(project_name)
        run = engine.continue_run(pdir, project_name, run_id, req.nodeId,
                                  allowed_shot_ids=req.allowedShotIds)
        engine.drive(pdir, project_name, run_id)
        return {"run": run}
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


@router.post("/api/project/{project_name}/workflow/runs/{run_id}/stop")
async def stop_run(project_name: str, run_id: str):
    try:
        run = get_engine().request_stop(_pdir(project_name), project_name, run_id)
        return {"run": run, "note": "已请求停止：不再派发下游；在途任务按真实能力处理"
                                     "（本版本 turn 协作取消，已提交的生成任务可能仍会完成）"}
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


# ============ 启动恢复（api_server startup 调用） ============
async def recover_workflows() -> dict:
    """启动恢复（§7.4）：扫描各项目 workflow runs，运行中/需要核对的 run
    重新对账（不自动派发新节点——需要显式继续；审核等待保持等待）。"""
    pm = get_project_manager()
    out = {"scanned": 0, "reconciled": 0, "errors": []}
    try:
        projects = pm.list_projects() if hasattr(pm, "list_projects") else []
    except Exception as e:
        out["errors"].append(f"list_projects: {e}")
        return out
    for name in projects:
        try:
            pdir = pm.get_project_dir(name)
            runs = RunStore(pdir, name)
            active = runs.active_run_ids()
            out["scanned"] += len(active)
            for rid in active:
                run = runs.load(rid)
                if run is None:
                    continue
                el = EventLog(pdir, rid)
                engine = get_engine()
                # 对账每个节点的在途 attempt（不派发新任务）
                for nid, node in (run.get("nodes") or {}).items():
                    cur = node.get("currentAttemptId")
                    att = next((a for a in node.get("attempts") or []
                                if a.get("attemptId") == cur), None)
                    if att and att.get("status") in ("dispatching", "running"):
                        await engine._reconcile_attempt(pdir, run, nid, att, runs, el)
                        out["reconciled"] += 1
                # Phase51 C：修复范围硬闸的进程内注册表恢复（节点 repairScope）
                from src.workflow.repair_scope import restore_from_run
                out["repair_scope_restored"] = out.get("repair_scope_restored", 0) + \
                    restore_from_run(name, runs.load(rid))
                runs.save(runs.load(rid) or run)
        except Exception as e:
            out["errors"].append(f"{name}: {type(e).__name__}: {e}")
    return out


# ============ Phase 54 A3：审核卡重开（对象变更后的合法重审路径）============
class ReviewRefreshReq(BaseModel):
    reason: str


@router.post("/api/project/{project_name}/workflow/runs/{run_id}/reviews/{request_id}/refresh")
async def refresh_review(project_name: str, run_id: str, request_id: str,
                         req: ReviewRefreshReq):
    """重开待审卡：重算产物内容快照+指纹（INT-M03 实证缺口——409 防篡改闸拒绝旧
    快照决策是正确设计，但对象合法变更后无官方重审入口）。仅在卡 pending 时允许；
    reason 必填并落事件留痕；已决策卡不可刷新（决策不可改写）。"""
    try:
        engine = get_engine()
        pdir = _pdir(project_name)
        from src.workflow.store import RunStore, _now_iso
        from src.workflow.executor import _artifact_content_snapshot
        from src.workflow.store import content_hash
        rs = RunStore(pdir, project_name)
        run = rs.load(run_id)
        if run is None:
            raise HTTPException(404, f"run 不存在: {run_id}")
        rv = next((x for x in (run.get("reviews") or [])
                   if x.get("requestId") == request_id), None)
        if rv is None:
            raise HTTPException(404, f"审核申请不存在: {request_id}")
        if rv.get("status") != "pending":
            raise HTTPException(409, f"审核卡状态 {rv.get('status')} 不可刷新"
                                     "（已决策卡不可改写）")
        if not (req.reason or "").strip():
            raise HTTPException(422, "refresh 必须附 reason（留痕）")
        refs = rv.get("artifactRefs") or []
        cur = _artifact_content_snapshot(pdir, project_name, refs)
        old_hashes = ((rv.get("contentSnapshot") or {}).get("hashes") or {})
        new_fp = content_hash({"refs": refs,
                               "attempt": rv.get("targetAttemptId")})

        def _mut(r):
            for x in r.get("reviews") or []:
                if x.get("requestId") == request_id and x.get("status") == "pending":
                    x["contentSnapshot"] = cur
                    x["artifactFingerprint"] = new_fp
                    x["refreshedAt"] = _now_iso()
                    x["refreshReason"] = (req.reason or "")[:300]
        rs.update(run_id, _mut)
        from src.workflow.store import EventLog
        EventLog(pdir, run_id).append(
            run_id, "review.refreshed", nodeId=rv.get("nodeId"),
            requestId=request_id, reason=(req.reason or "")[:200],
            oldHashes=sorted(old_hashes.values())[:2],
            newHashes=sorted((cur or {}).get("hashes", {}).values())[:2])
        out = rs.load(run_id)
        rv2 = next((x for x in (out.get("reviews") or [])
                    if x.get("requestId") == request_id), {})
        return {"requestId": request_id, "refreshed": True,
                "contentSnapshot": rv2.get("contentSnapshot"),
                "artifactFingerprint": rv2.get("artifactFingerprint")}
    except HTTPException:
        raise
    except WorkflowError as e:
        _err(e)
    except Exception as e:
        _err(e)
