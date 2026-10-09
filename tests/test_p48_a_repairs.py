# tests/test_p48_a_repairs.py
# Phase48 A批最小修复回归：A06 停止标签语义（cancelled ≠ failed）、
# A07 publish_character 视图文件双基准校验与具名失败、A08 修复目标钉定
# （excludeVersionIds + W06/W07 默认 require_new）、A09 场景目标资产类型点名。
# 全部零 LLM / 零生成；派发层 monkeypatch（与 p46 引擎测试同约定）。
import asyncio
import json

import pytest

from src.core.models import (Project, Scene, Shot, Character, Asset,
                             VideoVersion, ShotStatus)
from src.core.store import ProjectStore
from src.workflow import models as M
from src.workflow.adapter import verify_completion
from src.workflow.executor import WorkflowEngine
from src.workflow.outputs import (validate_completion_config, verify_node_outputs)
from src.workflow.store import RunStore
from src.agents import turn_registry
from src.agents.models import AgentTask
from src.agents.tasks import AgentTaskStore
from src.agents.runtime import AgentRuntime
from src.core.write_lock import _clear_for_test

import src.resources.shared_library as sl
from src.resources.shared_library import SharedLibraryService


# ============ 通用夹具 ============
@pytest.fixture(autouse=True)
def _clean_turn_registry():
    turn_registry.clear()
    yield
    turn_registry.clear()


def _runtime(tmp_path):
    return AgentRuntime(project_dir_resolver=lambda name: tmp_path,
                        settings_path=tmp_path / "agents_data" / "settings.json")


@pytest.fixture(autouse=True)
def _patch_heavy_io(monkeypatch, tmp_path):
    monkeypatch.setattr("src.workflow.executor.canonical_baseline",
                        lambda rt, project: {"stub": "before"})
    monkeypatch.setattr("src.workflow.executor.canonical_baseline_rt",
                        lambda project: {"stub": "after"})
    monkeypatch.setattr("src.workflow.executor._rt", lambda: object())
    yield


def _seed_running_attempt(runs, run_id, node_id, attempt_id="att_x"):
    """在持久 run 上种一个 in-flight attempt（恢复对账测试用）。"""
    att = {"attemptId": attempt_id, "status": "running", "startedAt": 0.0,
           "endedAt": None, "taskRefs": [], "result": None, "error": None,
           "inputs": {}, "outputs": None, "skillEvidence": None, "thinking": True}

    def _seed(r):
        n = r["nodes"][node_id]
        n["attempts"].append(att)
        n["currentAttemptId"] = attempt_id
    runs.update(run_id, _seed)
    return att


def _seed_agent_task(tmp_path, project, run_id, node_id, attempt_id,
                     task_id, status, error=None):
    tasks = AgentTaskStore(tmp_path, project)
    t = AgentTask(task_id=task_id, project=project, requester_agent="workflow",
                  target_agent="script_writer", capability_id="script_writer.delegate",
                  status=status, error=error,
                  workflow_ref={"runId": run_id, "nodeId": node_id,
                                "attemptId": attempt_id})
    tasks.create(t)
    return t


def _minimal_engine(tmp_path, monkeypatch):
    """最小单节点链（无审核），仅用于把 run 建起来做对账。"""
    nodes = [
        {"id": "nst", "type": "boundary_start", "refId": None, "refVersion": None,
         "label": None, "config": {}, "position": {"x": 0, "y": 0}},
        {"id": "n_sw", "type": "agent", "refId": "script_writer",
         "refVersion": None, "label": None,
         "config": {"objective": "写一段故事"}, "position": {"x": 0, "y": 0}},
        {"id": "nen", "type": "boundary_end", "refId": None, "refVersion": None,
         "label": None, "config": {}, "position": {"x": 0, "y": 0}},
    ]
    edges = [{"id": "e1", "type": "execution", "source": "nst", "target": "n_sw",
              "sourcePort": "out", "targetPort": "in"},
             {"id": "e2", "type": "execution", "source": "n_sw", "target": "nen",
              "sourcePort": "out", "targetPort": "in"}]
    d = {"schemaVersion": "workflow_draft_v1", "projectId": "p48t",
         "workflowId": "wf_p48", "name": "A批对账链", "status": "draft",
         "nodes": nodes, "edges": edges}
    from src.workflow.validate import validate_definition
    from src.workflow.plan import compile_plan, build_start_snapshot
    v = validate_definition(d, _runtime(tmp_path), "p48t")
    assert v["ok"], v["errors"]
    plan = compile_plan(d, v, v["catalog"])
    snap = build_start_snapshot(d, _runtime(tmp_path), "p48t", v, plan)
    eng = WorkflowEngine()
    out = eng.start_run(tmp_path, "p48t", d, v, plan, snap, "key-a06")
    return eng, out["run"]


# ============ A06：停止标签语义 ============
def test_reconcile_cancelled_task_yields_stopped_node(tmp_path, monkeypatch):
    """用户停止（cancelled）恢复对账 → 节点 stopped（保留停止来源），
    不得落 failed（Phase48 A06：用户停止 ≠ 真实失败）。"""
    monkeypatch.setattr("src.workflow.executor._project_dir_of", lambda p: None)
    eng, run = _minimal_engine(tmp_path, monkeypatch)
    runs = RunStore(tmp_path, "p48t")
    att = _seed_running_attempt(runs, run["runId"], "n_sw")
    _seed_agent_task(tmp_path, "p48t", run["runId"], "n_sw", att["attemptId"],
                     "task_cancelled", "cancelled", error="stopped")
    from src.workflow.store import EventLog
    asyncio.run(eng._reconcile_attempt(tmp_path, runs.load(run["runId"]), "n_sw", att,
                                       runs, EventLog(tmp_path, run["runId"])))
    r = runs.load(run["runId"])
    node = r["nodes"]["n_sw"]
    assert node["status"] == M.NODE_STOPPED
    a = next(x for x in node["attempts"] if x["attemptId"] == att["attemptId"])
    assert a["status"] == "stopped"
    assert "cancelled" in (a["error"] or "")
    assert r["status"] == M.RUN_STOPPED   # 不翻 success，也不误报 failed


def test_reconcile_failed_task_yields_failed_node(tmp_path, monkeypatch):
    """真实模型/工具失败恢复对账 → 节点 failed（既有语义保持）。"""
    monkeypatch.setattr("src.workflow.executor._project_dir_of", lambda p: None)
    eng, run = _minimal_engine(tmp_path, monkeypatch)
    runs = RunStore(tmp_path, "p48t")
    att = _seed_running_attempt(runs, run["runId"], "n_sw")
    _seed_agent_task(tmp_path, "p48t", run["runId"], "n_sw", att["attemptId"],
                     "task_failed", "failed", error="provider 500")
    from src.workflow.store import EventLog
    asyncio.run(eng._reconcile_attempt(tmp_path, runs.load(run["runId"]), "n_sw", att,
                                       runs, EventLog(tmp_path, run["runId"])))
    r = runs.load(run["runId"])
    node = r["nodes"]["n_sw"]
    assert node["status"] == M.NODE_FAILED
    a = next(x for x in node["attempts"] if x["attemptId"] == att["attemptId"])
    assert a["status"] == "failed"
    assert r["status"] == M.RUN_FAILED


def test_delegate_stopped_turn_settles_cancelled(tmp_path, monkeypatch):
    """ Specialist turn 以 stopped 结束（用户停止）→ AgentTask 落 cancelled
    （Phase48 A06 修复点：此前一律 FAILED）。"""
    _clear_for_test()
    rt = _runtime(tmp_path)

    async def fake_turn(rt_, project, sid, text, thinking=False, event_cb=None,
                        cancel_event=None):
        return {"message": {"status": "stopped", "meta": {}},
                "assistant_message": "（用户停止）",
                "receipts_raw": []}

    monkeypatch.setattr("src.agents.script_writer.script_writer_turn", fake_turn)
    from src.agents.producer import _delegate_specialist
    envelope = {"objective": "写一段故事（会被停止）", "situation": "测试",
                "constraints": [], "scope": {"free": "test"}, "user_intent": "t"}
    result = asyncio.run(_delegate_specialist(
        rt, "p48t", "script_writer", envelope, event_cb=None, cancel_event=None,
        prev_task_id=None, workflow_id="wf_test"))
    assert result["status"] == "stopped"
    tasks = AgentTaskStore(tmp_path, "p48t").list()
    assert tasks, "AgentTask 应已创建"
    t = tasks[-1]
    assert t.status == "cancelled", f"停止任务应落 cancelled，实际 {t.status}"
    assert any(w == "task_result_status=stopped" for w in (t.warnings or []))
    assert t.error == "stopped"   # stop reason 保留


# ============ A07：publish_character 视图文件校验 ============
def _char_project(pdir, name="p48char", view_locals=None):
    proj = Project(name=name)
    c = Character(name="Alice", description_en="a girl")
    c.views = {v: Asset(local=(view_locals or {}).get(v), status=ShotStatus.DONE.value)
               for v in ("front", "side", "back") if (view_locals or {}).get(v) is not None
               or view_locals}
    # 只保留显式给出的视图
    c.views = {v: a for v, a in c.views.items() if a.local is not None or a.url}
    proj.characters = [c]
    ProjectStore(pdir).save(proj)
    return proj


def _shared_service(tmp_path, name):
    return SharedLibraryService(tmp_path / name, name,
                                shared_dir=tmp_path / "_shared")


def test_publish_character_resolves_repo_root_relative(tmp_path, monkeypatch):
    """仓库根相对路径（真实生成资产形态，如 projects\\{p}\\output\\characters\\...）
    此前被误判为文件不存在 → 现在双基准解析后可发布（Phase48 A07 主缺口）。"""
    _clear_for_test()
    name = "p48rel"
    pdir = tmp_path / name
    pdir.mkdir(parents=True)
    # 写真实图片文件到仓库根相对位置（tmp 即仓库根代理：monkeypatch _repo_root）
    media = tmp_path / "projects" / name / "output" / "characters" / "_"
    media.mkdir(parents=True)
    for v in ("front", "side", "back"):
        (media / f"{v}.png").write_bytes(b"PNGDATA-" + v.encode())
    monkeypatch.setattr(sl, "_repo_root", lambda: tmp_path)
    _char_project(pdir, name, view_locals={
        v: f"projects/{name}/output/characters/_/{v}.png"
        for v in ("front", "side", "back")})
    svc = _shared_service(tmp_path, name)
    r = asyncio.run(svc.publish_character("Alice"))
    assert r["ok"], r
    assert sorted(r["views"].keys()) == ["back", "front", "side"]
    # 共享媒体真实落盘
    for aid in r["views"].values():
        f = tmp_path / "_shared" / "media" / f"{aid}.png"
        assert f.is_file() and f.stat().st_size > 0


def test_publish_character_blocks_and_names_missing_views(tmp_path, monkeypatch):
    """缺失/不可读视图阻塞发布并逐项具名；共享库零改动（无新条目、无版本递增）。"""
    _clear_for_test()
    name = "p48miss"
    pdir = tmp_path / name
    pdir.mkdir(parents=True)
    media = pdir / "output" / "characters"
    media.mkdir(parents=True)
    (media / "front.png").write_bytes(b"PNG-FRONT")
    # side 指向不存在的文件；back 为 0 字节
    (media / "back.png").write_bytes(b"")
    _char_project(pdir, name, view_locals={
        "front": "output/characters/front.png",
        "side": "output/characters/side_MISSING.png",
        "back": "output/characters/back.png"})
    svc = _shared_service(tmp_path, name)
    r = asyncio.run(svc.publish_character("Alice"))
    assert not r["ok"]
    assert r["error"] == "character_view_files_unreadable"
    codes = {p["view"]: p["code"] for p in r["failed_views"]}
    assert codes["side"] == "view_file_missing"
    assert codes["back"] == "view_file_missing"   # 0 字节也不算可读
    assert r["publishable_views"] == ["front"]
    # 共享库零改动
    sdoc_path = tmp_path / "_shared" / "resources.json"
    assert not sdoc_path.exists(), "失败发布不得写入共享库"


def test_publish_character_url_only_named_not_fake_missing(tmp_path):
    """URL-only 视图：如实具名（url_only_no_local_file），不判为不存在；阻塞发布。"""
    _clear_for_test()
    name = "p48url"
    pdir = tmp_path / name
    pdir.mkdir(parents=True)
    proj = Project(name=name)
    c = Character(name="Alice")
    c.views = {"front": Asset(local=None, url="https://x/front.png",
                              status=ShotStatus.DONE.value)}
    proj.characters = [c]
    ProjectStore(pdir).save(proj)
    svc = _shared_service(tmp_path, name)
    r = asyncio.run(svc.publish_character("Alice"))
    assert not r["ok"]
    assert r["failed_views"][0]["code"] == "url_only_no_local_file"


def test_publish_character_valid_project_relative_still_works(tmp_path):
    """既有合法路径（项目相对、绝对路径）发布保持可用（不破坏现有流程）。"""
    _clear_for_test()
    name = "p48ok"
    pdir = tmp_path / name
    pdir.mkdir(parents=True)
    media = pdir / "o"
    media.mkdir(parents=True)
    for v in ("front", "side", "back"):
        (media / f"alice_{v}.png").write_bytes(b"PNG-" + v.encode())
    _char_project(pdir, name, view_locals={
        v: f"o/alice_{v}.png" for v in ("front", "side", "back")})
    svc = _shared_service(tmp_path, name)
    r = asyncio.run(svc.publish_character("Alice"))
    assert r["ok"], r
    assert r["version"] == 1
    # republish 幂等（版本+1，同 shared id）
    r2 = asyncio.run(svc.publish_character("Alice"))
    assert r2["ok"] and r2["version"] == 2 and r2["shared_id"] == r["shared_id"]


# ============ A08：修复目标钉定 ============
def _video_project(pdir, name, versions):
    """versions: [(version_id, filename)] → s01_c01 的视频版本（真实文件）。"""
    proj = Project(name=name)
    vdir = pdir / "output" / "videos"
    vdir.mkdir(parents=True, exist_ok=True)
    vvs = []
    for vid, fn in versions:
        f = vdir / fn
        f.write_bytes(b"FAKEMP4-" + vid.encode())
        vvs.append(VideoVersion(version_id=vid, status=ShotStatus.DONE.value,
                                local=f"output/videos/{fn}", url=None))
    sh = Shot(shot_id="s01_c01", scene_id="s01", index=1)
    sh.video_versions = vvs
    proj.scenes = [Scene(scene_id="s01", shots=[sh])]
    ProjectStore(pdir).save(proj)
    return proj


def test_exclude_version_ids_validation():
    ok = validate_completion_config({"strategy": "require_new", "outputs": [
        {"kind": "video_version", "playable": True, "minCount": 1,
         "excludeVersionIds": ["vv_problem"]}]})
    assert ok == []
    for bad in ([""], ["x", 3], "vv_problem", []):
        errs = validate_completion_config({"strategy": "require_new", "outputs": [
            {"kind": "video_version", "playable": True, "minCount": 1,
             "excludeVersionIds": bad}]})
        assert errs, f"非法 excludeVersionIds 应被拒: {bad!r}"


def test_repair_target_exclusion(tmp_path):
    """问题版本被排除后不得满足修复 spec；其它合格旧产物仍可合法复用（不全局关闭 reuse）。"""
    name = "p48excl"
    pdir = tmp_path / name
    pdir.mkdir(parents=True)
    _video_project(pdir, name, [("vv_problem", "problem.mp4"),
                                ("vv_old_ok", "old_ok.mp4")])
    node_plan = {"nodeId": "n_dir_fix", "refId": "director",
                 "completion": {"strategy": "allow_verified_reuse", "outputs": [
                     {"kind": "video_version", "playable": True, "minCount": 1,
                      "excludeVersionIds": ["vv_problem"]}]}}
    # 零新写入：问题版本被排除 → 只剩 vv_old_ok 可复用（合法复用路径保持）
    ov = verify_node_outputs(pdir, name, node_plan, {"shots": []})
    assert ov["ok"], ov
    assert [r["versionId"] for r in ov["reused"]] == ["vv_old_ok"]
    # 排除所有候选 → 诚实缺项并具名排除
    node_plan2 = {"nodeId": "n_dir_fix", "refId": "director",
                  "completion": {"strategy": "allow_verified_reuse", "outputs": [
                      {"kind": "video_version", "playable": True, "minCount": 1,
                       "excludeVersionIds": ["vv_problem", "vv_old_ok"]}]}}
    ov2 = verify_node_outputs(pdir, name, node_plan2, {"shots": []})
    assert not ov2["ok"]
    assert "vv_problem" in ov2["missing"][0]["reason"]
    assert "不计入" in ov2["missing"][0]["reason"]


def test_require_new_blocks_unrelated_old_video(tmp_path):
    """A08 复现：require_new + 零新写入 → 任意旧视频不得完成修复节点
    （此前 allow_verified_reuse 默认下任意可播放旧视频即可通过——已收紧）。"""
    name = "p48rn"
    pdir = tmp_path / name
    pdir.mkdir(parents=True)
    _video_project(pdir, name, [("vv_old_unrelated", "old.mp4")])
    node_plan = {"nodeId": "n_dir_fix", "refId": "director",
                 "expectedDomains": ["shots"],
                 "completion": {"strategy": "require_new", "outputs": [
                     {"kind": "video_version", "playable": True, "minCount": 1}]}}
    verdict = verify_completion(node_plan, {"status": "completed", "verified": True,
                                            "changed_refs": {"shots": []},
                                            "summary": "什么都没写"},
                                pdir=pdir, project=name)
    assert not verdict["ok"]
    # 新写入（changed shots 命中）→ 通过
    verdict2 = verify_completion(node_plan, {"status": "completed", "verified": True,
                                             "changed_refs": {"shots": ["s01_c01"]},
                                             "summary": "生成新修复候选"},
                                 pdir=pdir, project=name)
    assert verdict2["ok"], verdict2


def test_w06_w07_template_defaults_hardened():
    from src.workflow.templates import create_from_template
    w06 = create_from_template("W06", "wf_w06", {"problemVersionId": "vv_bug"})
    fix_node = next(n for n in w06["nodes"] if n.get("id") == "n_dir_fix")
    comp = fix_node["config"]["completion"]
    assert comp["strategy"] == "require_new"
    spec = comp["outputs"][0]
    assert spec["excludeVersionIds"] == ["vv_bug"]
    w07 = create_from_template("W07", "wf_w07", {})
    bridge = next(n for n in w07["nodes"] if n.get("id") == "n_dir_bridge")
    assert bridge["config"]["completion"]["strategy"] == "require_new"
    # 模板编译仍合法（excludeVersionIds 在 SPEC_FIELDS 白名单内）
    from src.workflow.outputs import validate_completion_config
    assert validate_completion_config(comp) == []
    assert validate_completion_config(bridge["config"]["completion"]) == []


# ============ A09：场景目标资产类型点名 ============
def test_scene_objectives_name_asset_types():
    from src.workflow.templates import create_from_template
    w03 = create_from_template("W03", "wf_w03", {})
    sc = next(n for n in w03["nodes"] if n.get("id") == "n_sc")
    obj = sc["config"]["objective"]
    assert "SceneInstanceView" in obj and "scene.state_view.prepare" in obj
    assert "LocationView" in obj
    w09 = create_from_template("W09", "wf_w09", {})
    sc9 = next(n for n in w09["nodes"] if n.get("id") == "n_sc")
    assert "scene.state_view.prepare" in sc9["config"]["objective"]
