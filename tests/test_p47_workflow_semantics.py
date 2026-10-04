# tests/test_p47_workflow_semantics.py
# Phase 47 B批 — 工作流目标级完成校验 / 合法复用 / 交接包 v2 / 审核选用语义 单测
# （零 LLM / 零生成；项目事实走真实 tmp_path 文件底座；派发层 monkeypatch，
#  真机生产与真机恢复由 PHASE47 D/E 批真机用例覆盖）。
import asyncio
import json
import shutil
from pathlib import Path

import pytest

from src.workflow import models as M
from src.workflow.adapter import build_handoff, verify_completion
from src.workflow.executor import WorkflowEngine, WorkflowError
from src.workflow.outputs import (freeze_node_inputs, validate_completion_config,
                                  verify_node_outputs)
from src.workflow.selection import perform_selection
from src.workflow.store import EventLog, RunStore
from src.workflow.validate import validate_definition
from src.agents.runtime import AgentRuntime
from src.agents import turn_registry


# ============ 夹具 ============
def _runtime(tmp_path):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json")


@pytest.fixture(autouse=True)
def _clean_turn_registry():
    turn_registry.clear()
    yield
    turn_registry.clear()


@pytest.fixture(autouse=True)
def _patch_heavy_io(monkeypatch, tmp_path):
    monkeypatch.setattr("src.workflow.executor.canonical_baseline",
                        lambda rt, project: {"stub": "before"})
    monkeypatch.setattr("src.workflow.executor.canonical_baseline_rt",
                        lambda project: {"stub": "after"})
    monkeypatch.setattr("src.workflow.executor._rt", lambda: object())
    # 项目事实冻结走真实 tmp_path（§4.5/§4.7 语义在本套件内真实生效）
    monkeypatch.setattr("src.workflow.executor._project_dir_of",
                        lambda project: tmp_path)
    yield


def _make_project(tmp_path: Path, *, with_shot_video=False, with_look_ref=False,
                  with_char_image=False) -> Path:
    """合成最小项目事实（直接建在 tmp_path，与引擎 project_dir 一致）：
    project.json（场景 s01 + 镜头 s01_c01 [+视频版本 vv1]）
    + resources.json（look look_1 [+可读参考图]）。"""
    from src.core.models import Project, Scene, Shot, VideoVersion, Character
    pdir = tmp_path
    (pdir / "output").mkdir(parents=True, exist_ok=True)
    p = Project(name="proj")
    sc = Scene(scene_id="s01", summary="测试场景")
    sh = Shot(shot_id="s01_c01", scene_id="s01", index=0)
    if with_shot_video:
        media = pdir / "output" / "videos"
        media.mkdir(parents=True, exist_ok=True)
        f = media / "vv1.mp4"
        f.write_bytes(b"0" * 128)
        vv = VideoVersion(version_id="vv1", status="done", local=str(f))
        sh.video_versions = [vv]
        sc.shots = [sh]
    else:
        sc.shots = [sh]
    p.scenes = [sc]
    if with_char_image:
        ch = Character(name="红狐")
        img = pdir / "output" / "characters"
        img.mkdir(parents=True, exist_ok=True)
        f2 = img / "front.png"
        f2.write_bytes(b"png")
        from src.core.models import Asset
        ch.views = {"front": Asset(local=str(f2), status="done")}
        p.characters = [ch]
    (pdir / "output" / "project.json").write_text(
        json.dumps(p.to_dict(), ensure_ascii=False), encoding="utf-8")

    if with_look_ref:
        from src.resources.models import CharacterLook, ReferenceAsset
        from src.resources.store import ResourceStore
        img = pdir / "output" / "resources"
        img.mkdir(parents=True, exist_ok=True)
        ref_file = img / "look1_ref.png"
        ref_file.write_bytes(b"png-bytes")
        doc = ResourceStore(pdir).load()
        doc.reference_assets["ra_1"] = ReferenceAsset(
            asset_id="ra_1", local=str(ref_file))
        doc.looks["look_1"] = CharacterLook(
            look_id="look_1", character_name="红狐", label="常服",
            reference_asset_ids=["ra_1"])
        ResourceStore(pdir).save(doc)
    return pdir


# ============ completion 配置校验 ============
def test_completion_config_validation():
    assert validate_completion_config(None) == []
    assert validate_completion_config({"strategy": "bogus"}) != []
    errs = validate_completion_config({"outputs": [{"kind": "nope"}]})
    assert any("kind" in e for e in errs)
    # video_version 允许跨镜头计数型（无 shotId）；非法布尔仍拦截
    errs3 = validate_completion_config(
        {"outputs": [{"kind": "look", "unknown_field": 1}]})
    assert any("未知字段" in e for e in errs3)
    errs4 = validate_completion_config(
        {"outputs": [{"kind": "video_version", "playable": "yes"}]})
    assert any("playable" in e for e in errs4)


# ============ 目标级完成校验（§4.2/§4.3） ============
def test_require_new_rejects_existing_entity(tmp_path):
    pdir = _make_project(tmp_path, with_look_ref=True)
    plan = {"refId": "character_designer",
            "completion": {"strategy": "require_new",
                           "outputs": [{"kind": "look", "id": "look_1",
                                        "withImage": True}]}}
    # 本 attempt 无写入（changed_refs 无 looks）→ 不得以旧素材冒充新产物
    v = verify_node_outputs(pdir, "proj", plan, {})
    assert not v["ok"] and v["code"] == "outputs_missing"
    assert "allow_verified_reuse" in v["missing"][0]["reason"] or \
           "无对应新写入" in v["missing"][0]["reason"]


def test_allow_verified_reuse_passes_with_reused_record(tmp_path):
    pdir = _make_project(tmp_path, with_look_ref=True)
    plan = {"refId": "character_designer",
            "completion": {"strategy": "allow_verified_reuse",
                           "outputs": [{"kind": "look", "id": "look_1",
                                        "withImage": True}]}}
    v = verify_node_outputs(pdir, "proj", plan, {})
    assert v["ok"], v
    assert len(v["reused"]) == 1
    r = v["reused"][0]
    assert r["entityId"] == "look_1" and r["disposition"] == "reused"
    assert r["contentHash"] and r["contentHash"].startswith("sha256:")


def test_mixed_task_reuses_qualified_and_requires_new_missing(tmp_path):
    """混合任务：复用合格 Look（spec.allowReuse），同时要求新 Look（无 allowReuse）
    → 复用通过、新产物缺项如实报告。"""
    pdir = _make_project(tmp_path, with_look_ref=True)
    plan = {"refId": "character_designer",
            "completion": {"strategy": "require_new", "outputs": [
                {"kind": "look", "id": "look_1", "withImage": True,
                 "allowReuse": True},
                {"kind": "look", "id": "look_new", "withImage": True}]}}
    v = verify_node_outputs(pdir, "proj", plan, {})
    assert not v["ok"]
    assert len(v["reused"]) == 1 and v["reused"][0]["entityId"] == "look_1"
    assert len(v["missing"]) == 1 and v["missing"][0]["kind"] == "look"


def test_with_image_flag_requires_readable_file(tmp_path):
    pdir = _make_project(tmp_path, with_char_image=True)
    ok_plan = {"refId": "character_designer",
               "completion": {"outputs": [{"kind": "character", "id": "红狐",
                                           "withImage": True}]}}
    v1 = verify_node_outputs(pdir, "proj", ok_plan,
                             {"characters": ["红狐"]})
    assert v1["ok"] and len(v1["outputs"]) == 1
    # 无图角色：文件缺失 → withImage 不满足
    from src.core.store import ProjectStore
    store = ProjectStore(pdir)
    store.load()  # 触发存在
    plan2 = {"refId": "character_designer",
             "completion": {"outputs": [{"kind": "character", "id": "蓝鲸",
                                         "withImage": True}]}}
    v2 = verify_node_outputs(pdir, "proj", plan2, {"characters": ["蓝鲸"]})
    assert not v2["ok"]


def test_video_version_playable_check(tmp_path):
    pdir = _make_project(tmp_path, with_shot_video=True)
    plan = {"refId": "director",
            "completion": {"outputs": [{"kind": "video_version",
                                        "shotId": "s01_c01", "id": "vv1",
                                        "playable": True}]}}
    v = verify_node_outputs(pdir, "proj", plan, {"shots": ["s01_c01"]})
    assert v["ok"] and v["outputs"][0]["versionId"] == "vv1"
    assert v["outputs"][0]["reviewState"] == "candidate"  # 未 set-final


def test_sequence_completed_render_check(tmp_path):
    from src.sequence.service import SequenceService
    pdir = _make_project(tmp_path, with_shot_video=True)
    svc = SequenceService(pdir, "proj")
    svc.create_sequence(title="成片", clips=[{"shot_id": "s01_c01"}])
    entries = svc.list_entries()
    sid = next(iter(entries))
    plan = {"refId": "assembly",
            "completion": {"outputs": [{"kind": "sequence", "id": sid,
                                        "withCompletedRender": True}]}}
    v1 = verify_node_outputs(pdir, "proj", plan, {"sequences": [sid]})
    assert not v1["ok"]   # 无完成 render → 缺项诚实
    # 汇报为缺项原因包含质量标记与计数（不模糊说"失败请重试"）
    flat = json.dumps(v1["missing"], ensure_ascii=False)
    assert "withCompletedRender" in flat and "0 个" in flat


# ============ verify_completion 集成 ============
def test_verify_completion_with_outputs_integration(tmp_path):
    pdir = _make_project(tmp_path, with_look_ref=True)
    plan = {"refId": "character_designer",
            "completion": {"strategy": "allow_verified_reuse",
                           "outputs": [{"kind": "look", "id": "look_1",
                                        "withImage": True}]}}
    tr = {"status": "completed", "verified": True,
          "changed_refs": {"looks": []}}
    v1 = verify_completion(plan, tr, pdir=pdir, project="proj")
    assert v1["ok"] and v1["code"] == "outputs_verified"
    # 域命中底线仍生效：无任何预期域回执 → 先失败
    plan_no_hit = {"refId": "character_designer", "expectedDomains": ["looks"],
                   "completion": {"outputs": [{"kind": "look", "id": "look_9"}]}}
    tr2 = {"status": "completed", "verified": True, "changed_refs": {"shots": ["x"]}}
    v2 = verify_completion(plan_no_hit, tr2, pdir=pdir, project="proj")
    assert not v2["ok"] and v2["code"] == "no_expected_artifacts"


# ============ 交接包 v2（§4.4） ============
def test_handoff_v2_structure():
    run = {"runId": "run_x", "nodes": {}}
    attempt = {"attemptId": "att_x"}
    task_result = {"task_id": "task_1", "status": "completed", "verified": True,
                   "n_tool_calls": 2, "warnings": [], "summary": "完成",
                   "changed_refs": {"looks": ["look_1"]}}
    verify_out = {"outputs": [{"kind": "look", "entityId": "look_1",
                               "contentHash": "sha256:aa",
                               "reviewState": "candidate", "fileOk": True}],
                  "reused": [], "missing": [{"kind": "prop", "reason": "无参考"}]}
    h = build_handoff(run, "n1", attempt, task_result, "完成",
                      verify_outputs=verify_out, node_plan={"scope": {"shotIds": ["s01_c01"]}})
    assert h["workflowRef"] == {"runId": "run_x", "nodeId": "n1",
                                "attemptId": "att_x"}
    assert h["scope"] == {"shotIds": ["s01_c01"]}
    assert h["outputs"][0]["disposition"] == "created"
    assert h["reusedRefs"] == []
    assert h["missingRequirements"][0]["kind"] == "prop"
    # 复用条目同时出现在 outputs（disposition=reused）与 reusedRefs
    verify_out2 = {"outputs": [], "missing": [],
                   "reused": [{"kind": "look", "entityId": "look_1",
                               "contentHash": "sha256:aa", "fileOk": True}]}
    h2 = build_handoff(run, "n1", attempt, task_result, "完成",
                       verify_outputs=verify_out2)
    assert h2["reusedRefs"][0]["disposition"] == "reused"
    assert h2["outputs"][0]["disposition"] == "reused"


# ============ 逐节点输入冻结（§4.5） ============
def test_freeze_node_inputs_detects_stale(tmp_path):
    pdir = _make_project(tmp_path, with_look_ref=True)
    from src.resources.store import ResourceStore
    doc = ResourceStore(pdir).load()
    look = doc.looks["look_1"]
    from src.workflow.outputs import _resolve_kind
    m = _resolve_kind(pdir, "proj", {"kind": "look", "id": "look_1"})[0]
    run = {"nodes": {"n_up": {
        "upstreamNodeId": None,
        "handoff": {"outputs": [{"kind": "look", "entityId": "look_1",
                                 "versionId": None,
                                 "contentHash": m["contentHash"],
                                 "reviewState": "candidate",
                                 "disposition": "created"}]}}},
        "nodes2": None}
    pn = {"nodeId": "n_cur", "upstreamNodeId": "n_up"}
    f1 = freeze_node_inputs(pdir, "proj", run, pn)
    assert len(f1["resolvedInputs"]) == 1 and not f1["stale"]
    # 上游实体内容变化 → 冻结核验报 stale
    doc2 = ResourceStore(pdir).load()
    doc2.looks["look_1"].label = "改名了"
    ResourceStore(pdir).save(doc2)
    f2 = freeze_node_inputs(pdir, "proj", run, pn)
    assert f2["stale"] and "hash" in f2["stale"][0]["staleReason"]


# ============ 审核「批准并选用」（§4.6） ============
def _node(nid, ntype, ref=None, objective=None, **cfg):
    c = dict(cfg)
    if objective is not None:
        c["objective"] = objective
    return {"id": nid, "type": ntype, "refId": ref, "refVersion": None,
            "label": None, "config": c, "position": {"x": 0, "y": 0}}


def _edge(eid, src, dst, etype="execution", skill_version=None):
    e = {"id": eid, "type": etype, "source": src, "target": dst,
         "sourcePort": "out", "targetPort": "in"}
    if skill_version:
        e["skillVersion"] = skill_version
    return e


def _selection_chain_definition(selection_cfg):
    """编剧 → 审核(选用视频版本) → 装配。"""
    nodes = [
        _node("nst", "boundary_start"),
        _node("n_sw", "agent", "script_writer", objective="写故事"),
        _node("n_rv", "human_review", label="视频选用审核",
              targetSourceNodeId="n_sw", targetArtifact="视频候选",
              selection=selection_cfg),
        _node("n_asm", "agent", "assembly", objective="装配"),
        _node("nen", "boundary_end"),
    ]
    edges = [_edge("e1", "nst", "n_sw"), _edge("e2", "n_sw", "n_rv"),
             _edge("e3", "n_rv", "n_asm"), _edge("e4", "n_asm", "nen")]
    return {"schemaVersion": "workflow_draft_v1", "projectId": "p47t",
            "workflowId": "wf_sel", "name": "选用链", "status": "draft",
            "nodes": nodes, "edges": edges}


def _make_dispatch(monkeypatch, outcomes):
    calls = []

    async def fake_dispatch(rt, project, run, node_plan, attempt, *, event_cb,
                            cancel_event, frozen_inputs=None):
        ref = node_plan["refId"]
        calls.append(ref)
        out = outcomes.get(ref)
        if isinstance(out, Exception):
            raise out
        return {"status": out.get("status", "completed"),
                "task_result": out, "session_id": "sess_" + ref,
                "skillEvidence": {"loaded": []}, "raw": {}}

    monkeypatch.setattr("src.workflow.adapter.dispatch_node", fake_dispatch)
    return calls


def _start_selection_engine(tmp_path, monkeypatch, selection_cfg, outcomes=None):
    outcomes = outcomes or {
        "script_writer": {"task_id": "t1", "status": "completed",
                          "summary": "ok", "verified": True, "n_tool_calls": 1,
                          "changed_refs": {"scenes": ["s01"]}, "warnings": []},
        "assembly": {"task_id": "t2", "status": "completed", "summary": "ok",
                     "verified": True, "n_tool_calls": 1,
                     "changed_refs": {"sequences": ["seq_1"]}, "warnings": []}}
    eng = WorkflowEngine()
    calls = _make_dispatch(monkeypatch, outcomes)
    d = _selection_chain_definition(selection_cfg)
    v = validate_definition(d, _runtime(tmp_path), "p47t")
    assert v["ok"], v["errors"]
    from src.workflow.plan import compile_plan, build_start_snapshot
    plan = compile_plan(d, v, v["catalog"])
    snap = build_start_snapshot(d, _runtime(tmp_path), "p47t", v, plan)
    out = eng.start_run(tmp_path, "p47t", d, v, plan, snap, "key-sel")
    return eng, out["run"], calls


def _drive_sync(eng, tmp_path, run_id, wait=4.0):
    async def _go():
        eng.drive(tmp_path, "p47t", run_id)
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        while loop.time() - t0 < wait:
            t = eng._workers.get(run_id)
            if t is None or t.done():
                break
            await asyncio.sleep(0.02)
    asyncio.new_event_loop().run_until_complete(_go())


def test_review_without_selection_confirmation_rejected(tmp_path, monkeypatch):
    _make_project(tmp_path)
    sel = {"kind": "video_version", "entityId": "s01_c01", "versionId": "vv1"}
    eng, run, calls = _start_selection_engine(tmp_path, monkeypatch, sel)
    _drive_sync(eng, tmp_path, run["runId"])
    r = RunStore(tmp_path, "p47t").load(run["runId"])
    assert r["status"] == M.RUN_WAITING_HUMAN
    assert r["reviews"][0]["selectionRequired"] is True
    assert r["reviews"][0]["selectionState"] == "pending"
    # 未确认选用 → 决策被拒（selection_required 409）
    with pytest.raises(WorkflowError) as ei:
        eng.decide_review(tmp_path, "p47t", run["runId"],
                          r["reviews"][0]["requestId"], "approve", "通过")
    assert ei.value.code == "selection_required"
    r2 = RunStore(tmp_path, "p47t").load(run["runId"])
    assert r2["status"] == M.RUN_WAITING_HUMAN   # 仍在等待
    assert calls == ["script_writer"]            # 装配未派发


def test_review_approve_and_select_full_flow(tmp_path, monkeypatch):
    pdir = _make_project(tmp_path, with_shot_video=True)
    sel = {"kind": "video_version", "entityId": "s01_c01", "versionId": "vv1"}
    eng, run, calls = _start_selection_engine(tmp_path, monkeypatch, sel)
    _drive_sync(eng, tmp_path, run["runId"])
    r = RunStore(tmp_path, "p47t").load(run["runId"])
    out = eng.decide_review(tmp_path, "p47t", run["runId"],
                            r["reviews"][0]["requestId"], "approve", "通过并选用",
                            with_selection=True)
    assert out["alreadyDecided"] is False
    assert out["selectionPending"] is True
    # 选用执行在 drive 恢复路径（async；路由层同语义）
    _drive_sync(eng, tmp_path, run["runId"])
    r2 = RunStore(tmp_path, "p47t").load(run["runId"])
    rv = r2["reviews"][0]
    assert rv["selectionState"] == "done"
    assert rv["selectionReceipt"]["ok"] is True
    assert rv["selectionReceipt"]["pointer"] == "final_video"
    _drive_sync(eng, tmp_path, run["runId"])
    r3 = RunStore(tmp_path, "p47t").load(run["runId"])
    assert r3["status"] == M.RUN_COMPLETED
    assert calls == ["script_writer", "assembly"]
    # 真实指针变更落盘（与用户 set-final 入口同一语义）
    from src.core.store import ProjectStore
    shot = ProjectStore(pdir).load().get_shot("s01_c01")
    assert shot.final_video == "vv1"


def test_review_selection_failure_then_recovery(tmp_path, monkeypatch):
    """选用目标缺失 → needs_reconciliation + 具体缺项；修复后 continue 幂等重试。"""
    pdir = _make_project(tmp_path, with_shot_video=False)   # 无 vv1
    sel = {"kind": "video_version", "entityId": "s01_c01", "versionId": "vv1"}
    eng, run, calls = _start_selection_engine(tmp_path, monkeypatch, sel)
    _drive_sync(eng, tmp_path, run["runId"])
    r = RunStore(tmp_path, "p47t").load(run["runId"])
    eng.decide_review(tmp_path, "p47t", run["runId"],
                      r["reviews"][0]["requestId"], "approve", "选用",
                      with_selection=True)
    _drive_sync(eng, tmp_path, run["runId"])   # 选用执行（失败 → needs_reconciliation）
    r2 = RunStore(tmp_path, "p47t").load(run["runId"])
    assert r2["status"] == M.RUN_NEEDS_RECONCILIATION
    assert r2["nodes"]["n_rv"]["status"] == M.NODE_NEEDS_RECONCILIATION
    assert "选用未完成" in (r2["nodes"]["n_rv"]["error"] or "")
    assert r2["reviews"][0]["selectionState"] == "failed"
    assert calls == ["script_writer"]   # 装配未派发（不丢失也不误推）
    # 修复事实（生成 vv1）→ continue 幂等重试选用 → 节点 success
    from src.core.models import VideoVersion
    from src.core.store import ProjectStore
    media = pdir / "output" / "videos"
    media.mkdir(parents=True, exist_ok=True)
    f = media / "vv1.mp4"
    f.write_bytes(b"0" * 128)
    store = ProjectStore(pdir)
    store.update_shot("s01_c01", lambda s: s.video_versions.append(
        VideoVersion(version_id="vv1", status="done", local=str(f))))
    eng.continue_run(tmp_path, "p47t", run["runId"], "n_rv")
    _drive_sync(eng, tmp_path, run["runId"])   # drive 恢复路径幂等重试选用
    fresh = RunStore(tmp_path, "p47t").load(run["runId"])
    assert fresh["nodes"]["n_rv"]["status"] == M.NODE_SUCCESS
    assert fresh["reviews"][0]["selectionState"] == "done"
    _drive_sync(eng, tmp_path, run["runId"])
    r3 = RunStore(tmp_path, "p47t").load(run["runId"])
    assert r3["status"] == M.RUN_COMPLETED


def test_selection_service_rejects_latest_by_default(tmp_path):
    """不指定 versionId → 拒绝（不猜最新版本）。"""
    pdir = _make_project(tmp_path, with_shot_video=True)
    r = perform_selection(pdir, "proj",
                          {"kind": "video_version", "entityId": "s01_c01"})
    assert not r["ok"] and "versionId" in r["error"]


def test_templates_all_validate_and_compile(tmp_path):
    """C批 §5.1：十个模板全部通过真实校验与编译；Skill 绑定来自真实目录。"""
    from src.workflow.templates import create_from_template, list_templates
    from src.workflow.plan import compile_plan
    rt = _runtime(tmp_path)
    ids = [t["id"] for t in list_templates()]
    assert set(ids) == {f"W{i:02d}" for i in range(10)}
    for tid in ids:
        d = create_from_template(tid, f"wf_{tid}", {})
        d["projectId"] = "p47t"
        v = validate_definition(d, rt, "p47t")
        assert v["ok"], f"{tid}: {v['errors'][:3]}"
        plan = compile_plan(d, v, v["catalog"])
        assert plan["order"], f"{tid} 空链"
        assert plan["reviewCount"] >= 1
    # Skill 绑定：W05/W09 → relay；W07 → bridge（真实 ID）
    d5 = create_from_template("W05", "wf_w05", {})
    d5["projectId"] = "p47t"
    p5 = compile_plan(d5, validate_definition(d5, rt, "p47t"),
                      validate_definition(d5, rt, "p47t")["catalog"])
    assert any(s["skillId"] == "full-story-relay-calibration"
               for pn in p5["nodes"].values() for s in pn.get("skills") or [])
    d7 = create_from_template("W07", "wf_w07", {})
    d7["projectId"] = "p47t"
    v7 = validate_definition(d7, rt, "p47t")
    p7 = compile_plan(d7, v7, v7["catalog"])
    assert any(s["skillId"] == "keyframes-bridge-repair"
               for pn in p7["nodes"].values() for s in pn.get("skills") or [])


def test_assembly_without_materials_fails_fast(tmp_path, monkeypatch):
    """§4.7：上游无 video_version 交接、scope 未声明 → 装配节点派发前失败
    （不进模型、具体缺项；不把"找最新视频"当正式输入）。"""
    from src.core.models import Project, Scene, Shot
    pdir = tmp_path
    (pdir / "output").mkdir(parents=True, exist_ok=True)
    p = Project(name="p47t")
    sc = Scene(scene_id="s01", summary="t")
    sc.shots = [Shot(shot_id="s01_c01", scene_id="s01", index=0)]
    p.scenes = [sc]
    (pdir / "output" / "project.json").write_text(
        json.dumps(p.to_dict(), ensure_ascii=False), encoding="utf-8")

    nodes = [
        _node("nst", "boundary_start"),
        _node("n_sw", "agent", "script_writer", objective="写故事"),
        _node("n_asm", "agent", "assembly", objective="装配"),
        _node("nen", "boundary_end"),
    ]
    edges = [_edge("e1", "nst", "n_sw"), _edge("e2", "n_sw", "n_asm"),
             _edge("e3", "n_asm", "nen")]
    d = {"schemaVersion": "workflow_draft_v1", "projectId": "p47t",
         "workflowId": "wf_asm", "name": "装配链", "status": "draft",
         "nodes": nodes, "edges": edges}
    eng = WorkflowEngine()
    calls = _make_dispatch(monkeypatch, {
        "script_writer": {"task_id": "t1", "status": "completed", "summary": "ok",
                          "verified": True, "n_tool_calls": 1,
                          "changed_refs": {"scenes": ["s01"]}, "warnings": []},
        "assembly": {"task_id": "t2", "status": "completed", "summary": "ok",
                     "verified": True, "n_tool_calls": 1,
                     "changed_refs": {"sequences": ["seq_1"]}, "warnings": []}})
    v = validate_definition(d, _runtime(tmp_path), "p47t")
    assert v["ok"], v["errors"]
    from src.workflow.plan import compile_plan, build_start_snapshot
    plan = compile_plan(d, v, v["catalog"])
    snap = build_start_snapshot(d, _runtime(tmp_path), "p47t", v, plan)
    run = eng.start_run(tmp_path, "p47t", d, v, plan, snap, "k-asm")["run"]
    _drive_sync(eng, tmp_path, run["runId"])
    r = RunStore(tmp_path, "p47t").load(run["runId"])
    assert r["nodes"]["n_asm"]["status"] == M.NODE_FAILED
    assert "装配前置素材缺失" in (r["nodes"]["n_asm"]["error"] or "")
    assert calls == ["script_writer"]   # 装配从未真正派发


def test_review_content_change_rejects_stale_decision(tmp_path, monkeypatch):
    """待审产物内容在审核打开后变化 → 决策 409（review_object_changed）。"""
    pdir = _make_project(tmp_path, with_look_ref=False)
    sel = None   # 纯确认型审核
    eng, run, calls = _start_selection_engine(
        tmp_path, monkeypatch, sel,
        outcomes={"script_writer": {"task_id": "t1", "status": "completed",
                                    "summary": "ok", "verified": True,
                                    "n_tool_calls": 1,
                                    "changed_refs": {"scenes": ["s01"]},
                                    "warnings": []},
                  "assembly": {"task_id": "t2", "status": "completed",
                               "summary": "ok", "verified": True,
                               "n_tool_calls": 1,
                               "changed_refs": {"sequences": ["seq_1"]},
                               "warnings": []}})
    _drive_sync(eng, tmp_path, run["runId"])
    r = RunStore(tmp_path, "p47t").load(run["runId"])
    assert r["status"] == M.RUN_WAITING_HUMAN
    # 打开审核后项目事实变化（场景 s01 摘要被改写 → story 内容 hash 变化）
    from src.core.store import ProjectStore
    ProjectStore(pdir).load()
    import src.core.store as core_store
    store = ProjectStore(pdir)
    proj = store.load()
    proj.scenes[0].summary = "被外部改写的摘要"
    store.save(proj)
    with pytest.raises(WorkflowError) as ei:
        eng.decide_review(tmp_path, "p47t", run["runId"],
                          r["reviews"][0]["requestId"], "approve", "通过")
    assert ei.value.code == "review_object_changed"
