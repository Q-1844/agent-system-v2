# tests/test_p51_a_efficiency.py
# Phase51 A批 — 效率修复定向验证（零 LLM / 零真实生成）：
#   A-T01 跨 attempt 真实写入累计（require_new 重试判死修复；P50 实证 4 次整轮重启根因）
#   A-T02 旧素材/非本节点写入不冒充（诚实性底线保持）
#   A-T03 驳回后显式修复路径（continue_run rejected；驳回停止语义不变）
#   A-T04 驳回修复目标约束（不得跳过未过审上游）
#   A-T05 视频在途服务端等待+重判（零 LLM 收货等待；不把在途判成失败）
#   A-T06 poll_video 自适应退避（text 桶竞争缓解）
#   A-T07 envelope：修复提示注入 + 视频提交纪律约束
# 派发层 monkeypatch 沿用 P46/P47 模式；项目事实走真实 tmp_path 文件底座。
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.workflow import models as M
from src.workflow.adapter import build_envelope, verify_completion
from src.workflow.executor import WorkflowEngine, WorkflowError
from src.workflow.outputs import verify_node_outputs
from src.workflow.store import EventLog, RunStore
from src.workflow.validate import validate_definition
from src.agents.runtime import AgentRuntime
from src.agents import turn_registry


# ============ 夹具（沿用 P46/P47 模式） ============
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
    monkeypatch.setattr("src.workflow.executor._project_dir_of",
                        lambda project: tmp_path)
    yield


def _node(nid, ntype, ref=None, objective=None, **cfg):
    c = dict(cfg)
    if objective is not None:
        c["objective"] = objective
    return {"id": nid, "type": ntype, "refId": ref, "refVersion": None,
            "label": None, "config": c, "position": {"x": 0, "y": 0}}


def _edge(eid, src, dst):
    return {"id": eid, "type": "execution", "source": src, "target": dst,
            "sourcePort": "out", "targetPort": "in"}


def _make_project(tmp_path: Path, *, scenes=("s01", "s02"),
                  shot_video=None) -> Path:
    """最小项目事实：场景（带 summary）+ 单镜头；shot_video=(shot_id, version_id)
    时给该镜头挂一个本地可播放视频版本。"""
    from src.core.models import Project, Scene, Shot, VideoVersion
    pdir = tmp_path
    (pdir / "output").mkdir(parents=True, exist_ok=True)
    p = Project(name="proj")
    p.scenes = []
    for sid in scenes:
        sc = Scene(scene_id=sid, summary=f"{sid} 测试场景")
        sh = Shot(shot_id=f"{sid}_c01", scene_id=sid, index=0)
        if shot_video and shot_video[0] == sh.shot_id:
            media = pdir / "output" / "videos"
            media.mkdir(parents=True, exist_ok=True)
            f = media / f"{shot_video[1]}.mp4"
            f.write_bytes(b"0" * 128)
            sh.video_versions = [VideoVersion(version_id=shot_video[1],
                                              status="done", local=str(f))]
        sc.shots = [sh]
        p.scenes.append(sc)
    (pdir / "output" / "project.json").write_text(
        json.dumps(p.to_dict(), ensure_ascii=False), encoding="utf-8")
    return pdir


def _ok_task_result(ref_id, domain, ids, *, status="completed", verified=True):
    return {"task_id": "task_x", "agent": ref_id, "status": status,
            "summary": "完成", "verified": verified, "n_tool_calls": 3,
            "changed_refs": {domain: list(ids)},
            "warnings": [], "session_id": "sess"}


def _make_dispatch(monkeypatch, outcomes):
    """outcomes: {refId: [task_result, ...]}——按派发次序逐个给出（最后一项复用）。"""
    calls = []
    idx = {ref: {"n": 0} for ref in outcomes}

    async def fake_dispatch(rt, project, run, node_plan, attempt, *, event_cb,
                            cancel_event, frozen_inputs=None):
        ref = node_plan["refId"]
        calls.append(ref)
        seq = outcomes.get(ref) or []
        i = min(idx[ref]["n"], len(seq) - 1)
        idx[ref]["n"] += 1
        out = seq[i]
        if isinstance(out, Exception):
            raise out
        return {"status": out.get("status", "completed"),
                "task_result": out,
                "session_id": "sess_" + ref,
                "skillEvidence": {"loaded": []},
                "raw": {}}

    monkeypatch.setattr("src.workflow.adapter.dispatch_node", fake_dispatch)
    return calls


def _start_engine(tmp_path, monkeypatch, outcomes, definition, project="p51t"):
    eng = WorkflowEngine()
    calls = _make_dispatch(monkeypatch, outcomes)
    d = dict(definition)
    d["projectId"] = project
    v = validate_definition(d, _runtime(tmp_path), project)
    assert v["ok"], v["errors"]
    from src.workflow.plan import compile_plan, build_start_snapshot
    plan = compile_plan(d, v, v["catalog"])
    snap = build_start_snapshot(d, _runtime(tmp_path), project, v, plan)
    out = eng.start_run(tmp_path, project, d, v, plan, snap, "key-p51")
    return eng, out["run"], calls


def _drive_sync(eng, tmp_path, run_id, project="p51t", wait=5.0):
    async def _go():
        eng.drive(tmp_path, project, run_id)
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        while loop.time() - t0 < wait:
            t = eng._workers.get(run_id)
            if t is None or t.done():
                break
            await asyncio.sleep(0.02)
    asyncio.new_event_loop().run_until_complete(_go())


# ============ A-T01：跨 attempt 真实写入累计 ============
def _chain_sw_spec():
    nodes = [
        _node("nst", "boundary_start"),
        _node("n_sw", "agent", "script_writer", objective="写故事",
              completion={"strategy": "require_new",
                          "outputs": [{"kind": "scene", "minCount": 2}]}),
        _node("n_rv", "human_review", label="故事审定",
              targetSourceNodeId="n_sw", reviewer="制作人"),
        _node("nen", "boundary_end"),
    ]
    edges = [_edge("e1", "nst", "n_sw"), _edge("e2", "n_sw", "n_rv"),
             _edge("e3", "n_rv", "nen")]
    return {"schemaVersion": "workflow_draft_v1", "projectId": "p51t",
            "workflowId": "wf_p51", "name": "P51 测试链",
            "status": "draft", "nodes": nodes, "edges": edges}


def test_a_t01_cross_attempt_credit_satisfies_require_new(tmp_path, monkeypatch):
    """att1 写 s01（spec 需 2 场景 → 缺项判 failed）；att2 只补写 s02——
    此前 attempt 的 s01 是本节点真实写入，应计入 → 节点成功。
    （P50 run1 n_cd 实证：att1 建 4 角色、att2 建 4 Look，双双判死。）"""
    _make_project(tmp_path, scenes=("s01", "s02"))
    outcomes = {"script_writer": [
        _ok_task_result("script_writer", "scenes", ["s01"]),
        _ok_task_result("script_writer", "scenes", ["s02"]),
    ]}
    eng, run, calls = _start_engine(tmp_path, monkeypatch, outcomes,
                                    _chain_sw_spec())
    _drive_sync(eng, tmp_path, run["runId"])
    r = RunStore(tmp_path, "p51t").load(run["runId"])
    # att1 缺项 → 节点 failed（现状语义）
    assert r["nodes"]["n_sw"]["status"] == M.NODE_FAILED
    assert r["status"] == M.RUN_FAILED

    # 显式重试 → att2；服务端按「本 attempt ∪ 此前 attempts」累计
    eng.continue_run(tmp_path, "p51t", run["runId"])
    _drive_sync(eng, tmp_path, run["runId"])
    r = RunStore(tmp_path, "p51t").load(run["runId"])
    assert r["nodes"]["n_sw"]["status"] == M.NODE_SUCCESS
    atts = r["nodes"]["n_sw"]["attempts"]
    assert len(atts) == 2 and atts[1]["status"] == "success"
    ov = atts[1]["outputs"]["outputsVerdict"]
    assert ov["created"] == 2
    # 节点成功后推进到审核停靠
    assert r["status"] == M.RUN_WAITING_HUMAN
    # 交接包含 prior 计入的输出记录（writeSource 标注可溯）
    srcs = {o["entityId"]: o.get("writeSource") for o in
            r["nodes"]["n_sw"]["handoff"]["outputs"]}
    assert srcs == {"s01": "prior", "s02": "current"}


def test_a_t02_prior_refs_do_not_credit_foreign_or_missing(tmp_path, monkeypatch):
    """诚实性底线：prior refs 只承认本节点任务回执覆盖的实体——
    项目里不存在 / 非本节点写入的旧素材不因此合格。"""
    _make_project(tmp_path, scenes=("s01",))
    node_plan = {"nodeId": "n_x", "refId": "script_writer",
                 "expectedDomains": ["scenes"],
                 "completion": {"strategy": "require_new",
                                "outputs": [{"kind": "scene", "minCount": 1}]}}
    # 本 attempt 无写入，prior 指向项目里不存在的 s09 → 不计入 → 缺项
    v = verify_completion(node_plan, _ok_task_result("script_writer", "scenes", []),
                          pdir=tmp_path, project="p51t",
                          prior_changed_refs={"scenes": ["s09"]})
    assert v["ok"] is False and v["code"] == "outputs_missing"

    # 本 attempt 无写入，prior 指向真实存在的 s01 → 计入（本节点此前 attempt 写入）
    v2 = verify_completion(node_plan, _ok_task_result("script_writer", "scenes", []),
                           pdir=tmp_path, project="p51t",
                           prior_changed_refs={"scenes": ["s01"]})
    assert v2["ok"] is True and v2["code"] == "outputs_verified"
    assert all(o.get("writeSource") == "prior" for o in v2["outputsVerdict"]["outputs"])


def test_a_t03_verify_node_outputs_prior_only_no_writes(tmp_path, monkeypatch):
    """零写入 + 零 prior + require_new → 维持原判死语义（回归保护）。"""
    _make_project(tmp_path, scenes=("s01",))
    node_plan = {"nodeId": "n_x", "refId": "script_writer",
                 "expectedDomains": ["scenes"],
                 "completion": {"strategy": "require_new",
                                "outputs": [{"kind": "scene", "minCount": 1}]}}
    v = verify_completion(node_plan, _ok_task_result("script_writer", "scenes", []),
                          pdir=tmp_path, project="p51t")
    assert v["ok"] is False


# ============ A-T03/A-T04：驳回后显式修复 ============
def _chain_sw_plain():
    nodes = [
        _node("nst", "boundary_start"),
        _node("n_sw", "agent", "script_writer", objective="写故事"),
        _node("n_rv", "human_review", label="故事审定",
              targetSourceNodeId="n_sw", reviewer="制作人"),
        # scope.videoVersions：显式声明既有素材（本链不含视频生产节点；
        # §4.7 装配守卫要求 assembly 有明确素材边界，本夹具声明合法既有清单）
        _node("n_asm", "agent", "assembly", objective="装配",
              scope={"videoVersions": [{"kind": "video_version",
                                        "entityId": "s01_c01",
                                        "versionId": "vv1"}]}),
        _node("nen", "boundary_end"),
    ]
    edges = [_edge("e1", "nst", "n_sw"), _edge("e2", "n_sw", "n_rv"),
             _edge("e3", "n_rv", "n_asm"), _edge("e4", "n_asm", "nen")]
    return {"schemaVersion": "workflow_draft_v1", "projectId": "p51t",
            "workflowId": "wf_p51b", "name": "P51 驳回修复链",
            "status": "draft", "nodes": nodes, "edges": edges}


def test_a_t04_continue_rejected_run_repairs_and_replays_review(tmp_path, monkeypatch):
    """驳回语义不变（停止下游）；continue_run 显式修复：目标节点重开 attempt、
    驳回记录注入 repairNote、下游复位、审核重新打开（不静默撤销用户驳回）。"""
    outcomes = {"script_writer": [_ok_task_result("script_writer", "scenes", ["s01"])],
                "assembly": [_ok_task_result("assembly", "sequences", ["seq_1"])]}
    eng, run, calls = _start_engine(tmp_path, monkeypatch, outcomes,
                                    _chain_sw_plain())
    _drive_sync(eng, tmp_path, run["runId"])
    runs = RunStore(tmp_path, "p51t")
    r = runs.load(run["runId"])
    assert r["status"] == M.RUN_WAITING_HUMAN
    eng.decide_review(tmp_path, "p51t", run["runId"],
                      r["reviews"][0]["requestId"], "reject",
                      "大副 Look 为西装商务装，需海上作业装束")
    r = runs.load(run["runId"])
    assert r["status"] == M.RUN_REJECTED
    assert r["nodes"]["n_asm"]["status"] == M.NODE_STOPPED

    # drive 不得自动重开驳回 run（Phase48 E 守卫保持）
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert r["status"] == M.RUN_REJECTED
    assert calls == ["script_writer"]

    # 显式修复：目标=驳回审核的 target（n_sw）
    eng.continue_run(tmp_path, "p51t", run["runId"])
    r = runs.load(run["runId"])
    assert r["nodes"]["n_sw"]["status"] == M.NODE_PENDING
    assert "西装商务装" in (r["nodes"]["n_sw"].get("repairNote") or "")
    assert r["nodes"]["n_rv"]["status"] == M.NODE_PENDING
    assert r["nodes"]["n_asm"]["status"] == M.NODE_PENDING
    assert r["plan"]["order"][int(r.get("cursor") or 0)] == "n_sw"

    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    # 编剧重派一次 → 推进到审核重新打开（旧 decided 审核保留为历史）
    assert calls == ["script_writer", "script_writer"]
    assert r["status"] == M.RUN_WAITING_HUMAN
    decided = [x for x in r["reviews"] if x["status"] == "decided"]
    pending = [x for x in r["reviews"] if x["status"] == "pending"]
    assert len(decided) == 1 and decided[0]["decision"] == "reject"
    assert len(pending) == 1 and pending[0]["nodeId"] == "n_rv"

    # 修复后通过 → 下游照常推进（完整修复闭环）
    eng.decide_review(tmp_path, "p51t", run["runId"],
                      pending[0]["requestId"], "approve", "修复通过")
    _drive_sync(eng, tmp_path, run["runId"])
    r = runs.load(run["runId"])
    assert r["status"] == M.RUN_COMPLETED
    assert calls == ["script_writer", "script_writer", "assembly"]


def test_a_t05_continue_rejected_run_refuses_wrong_node(tmp_path, monkeypatch):
    outcomes = {"script_writer": [_ok_task_result("script_writer", "scenes", ["s01"])]}
    eng, run, _ = _start_engine(tmp_path, monkeypatch, outcomes, _chain_sw_plain())
    _drive_sync(eng, tmp_path, run["runId"])
    r = RunStore(tmp_path, "p51t").load(run["runId"])
    eng.decide_review(tmp_path, "p51t", run["runId"],
                      r["reviews"][0]["requestId"], "reject", "驳回")
    with pytest.raises(WorkflowError):
        eng.continue_run(tmp_path, "p51t", run["runId"], node_id="n_asm")


def test_a_t06_continue_still_refuses_stopped_and_completed(tmp_path, monkeypatch):
    """回归保护：Phase51 A 只放开 rejected；stopped/completed 语义不变。"""
    outcomes = {"script_writer": [_ok_task_result("script_writer", "scenes", ["s01"])],
                "assembly": [_ok_task_result("assembly", "sequences", ["seq_1"])]}
    eng, run, _ = _start_engine(tmp_path, monkeypatch, outcomes, _chain_sw_plain())
    rid = run["runId"]
    # completed
    _drive_sync(eng, tmp_path, rid)
    r = RunStore(tmp_path, "p51t").load(rid)
    eng.decide_review(tmp_path, "p51t", rid, r["reviews"][0]["requestId"],
                      "approve", "通过")
    _drive_sync(eng, tmp_path, rid)
    r = RunStore(tmp_path, "p51t").load(rid)
    assert r["status"] == M.RUN_COMPLETED
    with pytest.raises(WorkflowError):
        eng.continue_run(tmp_path, "p51t", rid)
    # stopped
    eng2, run2, _ = _start_engine(tmp_path, monkeypatch,
                                  {"script_writer": [_ok_task_result(
                                      "script_writer", "scenes", ["s01"])]},
                                  _chain_sw_plain(), project="p51t")
    # 同 key 幂等 → 返回同一 run；换 key 起新 run
    d = dict(_chain_sw_plain())
    from src.workflow.plan import compile_plan, build_start_snapshot
    v = validate_definition(d, _runtime(tmp_path), "p51t")
    plan = compile_plan(d, v, v["catalog"])
    snap = build_start_snapshot(d, _runtime(tmp_path), "p51t", v, plan)
    out = eng2.start_run(tmp_path, "p51t", d, v, plan, snap, "key-p51-2")
    rid2 = out["run"]["runId"]
    eng2.request_stop(tmp_path, "p51t", rid2)
    with pytest.raises(WorkflowError):
        eng2.continue_run(tmp_path, "p51t", rid2)


# ============ A-T05：在途视频等待+重判 ============
def _chain_video():
    nodes = [
        _node("nst", "boundary_start"),
        _node("n_dir", "agent", "director", objective="生成视频",
              completion={"strategy": "require_new",
                          "outputs": [{"kind": "video_version", "playable": True,
                                       "minCount": 1}]}),
        _node("n_rv", "human_review", label="视频审核",
              targetSourceNodeId="n_dir", reviewer="制作人"),
        _node("nen", "boundary_end"),
    ]
    edges = [_edge("e1", "nst", "n_dir"), _edge("e2", "n_dir", "n_rv"),
             _edge("e3", "n_rv", "nen")]
    return {"schemaVersion": "workflow_draft_v1", "projectId": "p51t",
            "workflowId": "wf_p51v", "name": "P51 在途等待链",
            "status": "draft", "nodes": nodes, "edges": edges}


class _FakeJobQueue:
    def __init__(self, jobs):
        self.jobs = jobs


def _add_video_version(tmp_path: Path, shot_id: str, version_id: str):
    import time as _time
    from src.core.models import VideoVersion
    from src.core.store import ProjectStore
    store = ProjectStore(tmp_path)
    p = store.load()
    media = tmp_path / "output" / "videos"
    media.mkdir(parents=True, exist_ok=True)
    f = media / f"{version_id}.mp4"
    f.write_bytes(b"0" * 128)
    for sc in p.scenes:
        for sh in sc.shots:
            if sh.shot_id == shot_id:
                sh.video_versions = [VideoVersion(version_id=version_id,
                                                  status="done", local=str(f),
                                                  created_at=_time.time())]
    store.save(p)


def test_a_t07_inflight_video_wait_then_reverify(tmp_path, monkeypatch):
    """回合结束后视频仍在途：完成判定缺项 → 服务端等待结算（零 LLM）→ 重判通过。
    （P50 实证：批量提交纪律的前提=提前结束回合不被误判失败。）"""
    _make_project(tmp_path, scenes=("s01",), shot_video=None)
    outcomes = {"director": [_ok_task_result("director", "shots", ["s01_c01"])]}
    eng, run, calls = _start_engine(tmp_path, monkeypatch, outcomes, _chain_video())

    fake_q = _FakeJobQueue({"j1": SimpleNamespace(
        job_id="j1", project="p51t", kind="video", shot_id="s01_c01",
        state="running", progress=10)})
    monkeypatch.setattr("deps.get_job_queue", lambda: fake_q)

    async def _go():
        async def _flip():
            await asyncio.sleep(0.3)
            fake_q.jobs["j1"].state = "done"
            fake_q.jobs["j1"].progress = 100
            _add_video_version(tmp_path, "s01_c01", "vv1")
        asyncio.ensure_future(_flip())
        eng.drive(tmp_path, "p51t", run["runId"])
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        while loop.time() - t0 < 8.0:
            t = eng._workers.get(run["runId"])
            if t is None or t.done():
                break
            await asyncio.sleep(0.02)
    asyncio.new_event_loop().run_until_complete(_go())

    r = RunStore(tmp_path, "p51t").load(run["runId"])
    assert r["nodes"]["n_dir"]["status"] == M.NODE_SUCCESS, r["nodes"]["n_dir"].get("error")
    atts = r["nodes"]["n_dir"]["attempts"]
    assert atts[0]["status"] == "success"
    assert r["status"] == M.RUN_WAITING_HUMAN


def test_a_t08_inflight_video_failed_is_honest_failure(tmp_path, monkeypatch):
    """在途视频终态=failed：等待结束后重判仍缺项 → 诚实失败（不假成功）。"""
    _make_project(tmp_path, scenes=("s01",))
    outcomes = {"director": [_ok_task_result("director", "shots", ["s01_c01"])]}
    eng, run, _ = _start_engine(tmp_path, monkeypatch, outcomes, _chain_video())

    fake_q = _FakeJobQueue({"j1": SimpleNamespace(
        job_id="j1", project="p51t", kind="video", shot_id="s01_c01",
        state="running", progress=10)})
    monkeypatch.setattr("deps.get_job_queue", lambda: fake_q)

    async def _go():
        async def _flip():
            await asyncio.sleep(0.2)
            fake_q.jobs["j1"].state = "failed"   # 终态=失败，无视频版本落盘
        asyncio.ensure_future(_flip())
        eng.drive(tmp_path, "p51t", run["runId"])
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        while loop.time() - t0 < 8.0:
            t = eng._workers.get(run["runId"])
            if t is None or t.done():
                break
            await asyncio.sleep(0.02)
    asyncio.new_event_loop().run_until_complete(_go())

    r = RunStore(tmp_path, "p51t").load(run["runId"])
    assert r["nodes"]["n_dir"]["status"] == M.NODE_FAILED
    assert "目标级输出缺项" in (r["nodes"]["n_dir"].get("error") or "")
    assert r["status"] == M.RUN_FAILED


# ============ A-T06：poll_video 自适应退避 ============
def test_a_t09_poll_video_backoff_pattern(monkeypatch):
    """前 60s 5s 步进、之后 20s 步进——轮询量降 4-6 倍（text 桶竞争缓解）。"""
    import src.api.agnes_client as ac

    class _Resp:
        def __init__(self, d):
            self._d = d
        def json(self):
            return self._d

    sleeps: list = []

    async def _fake_sleep(s):
        sleeps.append(round(s, 3))

    monkeypatch.setattr(f"{ac.__name__}.asyncio.sleep", _fake_sleep)

    state = {"n": 0}

    async def _fake_request(method, path, bucket, **kw):
        state["n"] += 1
        if state["n"] >= 16:   # 第 16 次查询才完成（覆盖 5s→20s 退避切换点）
            return _Resp({"status": "completed", "url": "http://x/v.mp4",
                          "seconds": 10.0})
        return _Resp({"status": "running", "progress": state["n"] * 10})

    cfg = {"base_url": "http://test", "api_key": "k",
           "models": {"text": "t", "image": "i", "video": "v"}}
    c = ac.AgnesClient(cfg)
    monkeypatch.setattr(c, "_request", _fake_request)

    asyncio.new_event_loop().run_until_complete(
        c.poll_video("vid_x", model_name="v"))

    fast = [s for s in sleeps if s == 5.0]
    slow = [s for s in sleeps if s == 20.0]
    assert len(fast) == 12            # 前 60s：12×5s
    assert len(slow) >= 1             # 之后进入 20s 步进
    assert sleeps.index(20.0) > sleeps.index(5.0)
    # 步进序列单调：5s 段之后不再出现 5s
    first_slow = sleeps.index(20.0)
    assert all(s == 20.0 for s in sleeps[first_slow:])


# ============ A-T07：envelope 注入 ============
def test_a_t10_envelope_repair_note_injected(tmp_path):
    rt = _runtime(tmp_path)
    run = {"runId": "run_x", "snapshot": {"definition": {"name": "n"}},
           "nodes": {"n_sw": {"repairNote": "审核驳回（故事审定）：冲突不足"}}}
    node_plan = {"nodeId": "n_sw", "refId": "script_writer", "objective": "写"}
    env = build_envelope(rt, "p51t", run, node_plan)
    assert "修复提示" in env["situation"]
    assert "冲突不足" in env["situation"]
    assert "保留合格部分" in env["situation"]


def test_a_t11_envelope_video_discipline_only_for_video_nodes(tmp_path):
    rt = _runtime(tmp_path)
    run = {"runId": "run_x", "snapshot": {"definition": {"name": "n"}}, "nodes": {}}
    vid_plan = {"nodeId": "n_dir_video", "refId": "director", "objective": "生成视频",
                "completion": {"strategy": "require_new",
                               "outputs": [{"kind": "video_version",
                                            "playable": True, "minCount": 6}]}}
    env = build_envelope(rt, "p51t", run, vid_plan)
    assert any("视频提交纪律" in c for c in env["constraints"])
    board_plan = {"nodeId": "n_dir_board", "refId": "director", "objective": "分镜",
                  "completion": {"strategy": "require_new",
                                 "outputs": [{"kind": "shot", "withImage": True,
                                              "minCount": 6}]}}
    env2 = build_envelope(rt, "p51t", run, board_plan)
    assert not any("视频提交纪律" in c for c in env2["constraints"])
