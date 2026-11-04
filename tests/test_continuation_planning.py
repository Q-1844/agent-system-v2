# tests/test_continuation_planning.py
"""PR C1：续作意图执行测试（T05-T08 / T15 / T16 + 路由与集成）。

全部 mock：client fixture 启动 app（deps 单例）；stage0/生产索引用内存
FakeIndex；零真实媒体、零网络。

覆盖（对齐执行指令 v2 §8）：
- T05 resume_run：带 RESUME_RUN_ID 的新任务 → 诚实拒绝并指路（零媒体）；
  不带 → 按项目现状继续（complete 语义）。
- T06 complete_project：workplan 从项目快照确定性导出（scene_targets=未 READY）。
- T07 add_assets：output_kind=asset_set；场景提及被解析记录（范围=全集）。
- T08 复用优先：已有效成片的镜头进 reuse_assets，不进 generate_missing。
- T15 reuse 证据：每条 reuse_asset 带可核验来源（shot_id/version_id/url|local）。
- T16 asset_set 验收：缺什么列什么，全齐才 complete；无时长门槛。
- 集成：_run_continuation_acceptance 全链（源项目不重建、digest 稳定、
  二次 hop 恢复 workplan 且不需要任务文本）。
- _resolve_production_project：auto 不重建项目（B2 潜伏 bug 的回归锁）。
"""
import asyncio
import json
from pathlib import Path

import pytest

from src.ai.producer_planning_models import TaskSpec
from src.ai.incremental_planner import (
    build_incremental_workplan, continuation_media_profile,
    continuation_sidecar_budgets, parse_scene_targets, validate_asset_set,
)
from src.ai.project_production_inspect import ProjectProductionInspect
from src.core.models import (Character, Project, Scene, Shot, StyleAnchor,
                             VideoVersion)
from src.core.store import ProjectStore

from tests.test_stage0_auto_planning import FakeIndex


# ============ 纯逻辑：场景提及解析 ============

def test_parse_scene_targets():
    assert parse_scene_targets("把深海项目 s02 后面没生成出来的素材补齐") == ["s02"]
    assert parse_scene_targets("s01 和 s3 都要补") == ["s01", "s03"]
    assert parse_scene_targets("全部补齐") == []


# ============ 路由（零 client） ============

def test_normalize_routes_continuation_intents():
    from tools import full_project_cloud_acceptance as acc
    env = {"PRODUCTION_REQUEST": "继续这个项目，把没做完的做完",
           "SOURCE_PROJECT": "deepsea", "FILM_TARGET_SECONDS": "auto"}
    ts = acc.normalize_dispatch_input_to_task_spec("unit", environ=env)
    assert ts.intent == "complete_project" and ts.source_project == "deepsea"
    env2 = {"PRODUCTION_REQUEST": "把 s02 后面没生成出来的素材补齐",
            "SOURCE_PROJECT": "deepsea", "FILM_TARGET_SECONDS": "auto"}
    ts2 = acc.normalize_dispatch_input_to_task_spec("unit", environ=env2)
    assert ts2.intent == "add_assets"


def test_t05_resume_run_with_run_id_rejected_honestly():
    """T05：新任务不得挪用别的 run id（快照恢复绑定原 nonce）。"""
    from tools import full_project_cloud_acceptance as acc
    ts = TaskSpec(intent="resume_run", request_text="恢复上次运行",
                  source_project="deepsea")
    summary = {}
    old = dict(acc.os.environ)
    acc.os.environ = {**old, "RESUME_RUN_ID": "12345", "FILM_TARGET_SECONDS": "auto"}
    try:
        with pytest.raises(RuntimeError, match="原任务 nonce"):
            asyncio.run(acc._run_continuation_acceptance("n", Path("/tmp/x"),
                                                         summary, "tok", ts))
        assert summary["no_new_media_submitted"] is True
    finally:
        acc.os.environ = old


def _seed_continuation_project(name, pdir):
    """三场景项目：s01 已完成（有效成片）、s02 有镜缺视频、s03 无镜头。"""
    v = VideoVersion(version_id="v1", video_id="task_x",
                     url="https://x/v.mp4", status="done")
    shot_done = Shot(shot_id="s01_c01", scene_id="s01", index=1, description="d",
                     target_duration_s=8, final_video="v1", video_versions=[v])
    shot_missing = Shot(shot_id="s02_c01", scene_id="s02", index=1, description="d",
                        target_duration_s=6)
    proj = Project(
        name=name, style=StyleAnchor(), story_text="x",
        characters=[Character(name="无人机", description_en="u")],
        scenes=[Scene(scene_id="s01", summary="完成段", characters=["无人机"],
                      shots=[shot_done]),
                Scene(scene_id="s02", summary="缺视频段", characters=["无人机"],
                      shots=[shot_missing]),
                Scene(scene_id="s03", summary="无镜头段", characters=["无人机"],
                      shots=[])])
    ProjectStore(pdir).save(proj)
    return proj


# ============ workplan / profile / 验收（client fixture 提供真实 deps） ============

def _ctx(client):
    import shutil
    from deps import get_project_manager
    name = "_test_c1_proj"
    pm = get_project_manager()
    shutil.rmtree(pm.base_dir / name, ignore_errors=True)
    pdir = pm.create_project(name)
    proj = _seed_continuation_project(name, pdir)
    from src.ai.project_production_inspect import ProjectProductionInspect
    snap = ProjectProductionInspect(name, pdir).snapshot()
    return name, pdir, proj, snap


def test_t06_workplan_derived_from_project_state(client):
    name, pdir, proj, snap = _ctx(client)
    wp = build_incremental_workplan(snap, "继续这个项目，把没做完的做完",
                                    "complete_project", pdir)
    assert wp["output_kind"] == "final_video"
    # s01 READY；s02 缺视频；s03 无镜头 → 目标是后两者
    assert set(wp["scene_targets"]) == {"s02", "s03"}
    assert wp["mentioned_scenes"] == []
    prof = continuation_media_profile(proj)
    assert prof["scene_count"] == 3
    assert prof["shot_min"] == 2 + 2 * 1   # 既有 2 镜（s01_c01+s02_c01）+ 无镜头场景最少 2 镜
    assert prof["planned_min"] == 14 + 8  # 8+6 既有 + s03 下界 8
    assert prof["min_final"] == 19  # floor(0.9*22)=19


def test_t07_asset_set_kind_and_mentions(client):
    name, pdir, proj, snap = _ctx(client)
    wp = build_incremental_workplan(snap, "把 s02 后面没生成出来的素材补齐",
                                    "add_assets", pdir)
    assert wp["output_kind"] == "asset_set"
    assert wp["mentioned_scenes"] == ["s02"]
    # 范围=全集（未提及的 s03 缺口也在清单里；C2 再做过滤细化）
    assert set(wp["scene_targets"]) == {"s02", "s03"}


def test_t08_t15_reuse_with_evidence(client):
    name, pdir, proj, snap = _ctx(client)
    wp = build_incremental_workplan(snap, "继续这个项目", "complete_project", pdir)
    reuse = wp["reuse_assets"]
    assert [r["target"] for r in reuse] == ["s01_c01"]
    for r in reuse:  # T15：每条 reuse 必须带可核验证据
        ev = r["evidence"]
        assert ev["version_id"] == "v1" and ev["video_id"] == "task_x"
    gen = [g["target"] for g in wp["generate_missing"]]
    assert gen == ["s02_c01"]


def test_t16_asset_set_validation(client):
    name, pdir, proj, snap = _ctx(client)
    chk = validate_asset_set(pdir)
    assert chk["complete"] is False
    assert "s02_c01" in chk["missing"] and len(chk["missing"]) == 1
    # 补齐 s02 后 → complete（无时长门槛：不检查 duration）
    proj = ProjectStore(pdir).load()
    v2 = VideoVersion(version_id="v2", video_id="task_y",
                      url="https://x/y.mp4", status="done")
    shot = proj.scenes[1].shots[0]
    shot.final_video = "v2"
    shot.video_versions.append(v2)
    ProjectStore(pdir).save(proj)
    # C1 hardening: scene without Shots is NOT a completed asset set.
    chk2 = validate_asset_set(pdir)
    assert chk2["complete"] is False and chk2["missing_scenes"] == ["s03"]
    # Once Director has added a legitimate final-video Shot, full set passes.
    proj = ProjectStore(pdir).load()
    from src.core.models import Shot
    proj.scenes[2].shots.append(Shot(shot_id="s03_c01", scene_id="s03", index=1,
                                    final_video="v3", video_versions=[VideoVersion(
                                        version_id="v3", video_id="task_z",
                                        url="https://x/z.mp4", status="done")]))
    ProjectStore(pdir).save(proj)
    chk3 = validate_asset_set(pdir)
    assert chk3["complete"] is True and chk3["missing"] == []
    assert chk3["unverified"] == []  # metadata completeness, not byte QA
    assert validate_asset_set(pdir, require_local_verified=True)["complete"] is False


def test_sidecar_budgets_only_for_unshot_scenes(client):
    name, pdir, proj, snap = _ctx(client)
    budgets = continuation_sidecar_budgets(proj)
    assert [b["scene_id"] for b in budgets] == ["s03"]
    assert budgets[0]["min_s"] == 8 and budgets[0]["max_s"] == 60


# ============ _resolve_production_project：B2 潜伏 bug 回归锁 ============

def test_resolve_production_project_auto_never_recreates(client, monkeypatch, tmp_path):
    from tools import full_project_cloud_acceptance as acc
    from deps import get_project_manager
    pm = get_project_manager()
    # auto：已有项目（Stage 0 已物化/continuation 源项目）→ 只取目录
    name = "_test_c1_resolve"
    import shutil
    shutil.rmtree(pm.base_dir / name, ignore_errors=True)
    shutil.rmtree(pm.base_dir / "cloud3min_nonce-x", ignore_errors=True)  # 上次失败残留
    pdir = pm.create_project(name)
    (pdir / "output" / "project.json").write_text("{}", encoding="utf-8")
    before = (pdir / "output" / "project.json").read_text(encoding="utf-8")
    n2, d2 = acc._resolve_production_project(
        "auto", None, name, {"scene_count": 1}, "n")
    assert n2 == name and d2 == pdir
    assert (pdir / "output" / "project.json").read_text(encoding="utf-8") == before
    # restored：项目名来自恢复信息
    n3, d3 = acc._resolve_production_project(
        "200", {"project_name": name}, "ignored", {}, "n")
    assert n3 == name
    # 固定档：物化预写剧本项目
    fake_profile = {"scenes": [("t1", "a"), ("t2", "b")], "story_text": "s"}
    n4, d4 = acc._resolve_production_project(
        "200", None, None, fake_profile, "nonce-x")
    assert n4 == "cloud3min_nonce-x"
    proj4 = ProjectStore(d4).load()
    assert len(proj4.scenes) == 2


# ============ 集成：_run_continuation_acceptance 全链 ============

def test_integration_continuation_full_chain(client, monkeypatch, tmp_path):
    from tools import video_task_state as vts
    from tools import e2e_autonomous_run as e2e
    from tools import full_project_cloud_acceptance as acc
    import deps

    name, pdir, proj, snap = _ctx(client)
    files = {}

    def fake_task_index(token, nonce, filename="state.json", transport=None):
        return files.setdefault(filename, FakeIndex())

    monkeypatch.setattr(vts, "task_index", fake_task_index)
    monkeypatch.setattr(e2e, "ensure_branch", lambda idx: None)

    env = {"PRODUCTION_REQUEST": "继续这个项目，把没做完的做完",
           "SOURCE_PROJECT": name, "FILM_TARGET_SECONDS": "auto"}
    monkeypatch.setattr(acc.os, "environ", env)
    summary, out = {}, tmp_path / "out"
    out.mkdir()
    ts = acc.normalize_dispatch_input_to_task_spec("unit", environ=env)

    project_name, profile, cp, restored, previous = asyncio.run(
        acc._run_continuation_acceptance("film-c1-int", out, summary, "tok", ts))

    # 源项目直用：不新建、不重命名
    assert project_name == name
    assert summary["output_kind"] == "final_video"
    assert set(summary["scene_targets"]) == {"s02", "s03"}
    assert summary["reuse_assets"] == 1 and summary["generate_missing"] == 1
    assert summary["plan_fingerprint"]
    assert summary["execution_request_sha256"]
    # sidecar 只给无镜头场景
    sidecar = pdir / "output" / "ai" / "scene_budgets.json"
    sc = json.loads(sidecar.read_text(encoding="utf-8"))
    assert [b["scene_id"] for b in sc["scene_budgets"]] == ["s03"]
    # checkpoint 字段
    prod = files["full-project-production.json"].blob["doc"]
    assert prod["film_target_seconds"] == "auto"
    assert prod["output_kind"] == "final_video"
    assert prod["plan_fingerprint"] == summary["plan_fingerprint"]

    # ---- 二次进入（跨 hop）：无任务文本、零 LLM、workplan 原样恢复 ----
    env2 = {"FILM_TARGET_SECONDS": "auto"}
    monkeypatch.setattr(acc.os, "environ", env2)
    summary2 = {}
    # stage0 记录存在 → 从记录恢复 TaskSpec（走 _run_auto_acceptance 的恢复入口）
    name2, prof2, cp2, restored2, prev2 = asyncio.run(
        acc._run_auto_acceptance("film-c1-int", out, summary2, "tok"))
    assert name2 == name
    assert summary2["stage0_status"] == "FROZEN_RESTORED"
    assert summary2["plan_fingerprint"] == summary["plan_fingerprint"]
    assert summary2["execution_request_sha256"] == summary["execution_request_sha256"]
    assert prof2["planned_min"] == profile["planned_min"]  # 动态 profile 跨 hop 稳定
    # 源项目未被重建（project.json 未被覆盖）
    assert ProjectStore(pdir).load().name == name

    # ---- 状态变化下的 digest 稳定性（回归锁：digest 必须来自冻结 workplan，
    #      不能按 live missing 重算——否则 hop-1 产出后 hop-2 会被 checkpoint 拒绝）----
    from src.core.models import VideoVersion as _VV
    proj = ProjectStore(pdir).load()
    miss = next(sh for sc in proj.scenes for sh in sc.shots
                if sh.final_video is None)
    miss.video_versions.append(_VV(version_id="v2", video_id="task_new",
                                   url="https://x/new.mp4", status="done"))
    miss.final_video = "v2"  # 模拟 hop-1 补齐了一镜
    ProjectStore(pdir).save(proj)
    summary3 = {}
    name3, prof3, cp3, restored3, prev3 = asyncio.run(
        acc._run_auto_acceptance("film-c1-int", out, summary3, "tok"))
    assert summary3["execution_request_sha256"] == summary["execution_request_sha256"], \
        "digest 不得随项目状态变化漂移"
    # 恢复的 workplan 是冻结时的审计记录（scene_targets 保持冻结时刻的缺口）；
    # 生产以活项目为准——活状态重新导出的工作计划只剩 s03：
    proj3 = ProjectStore(pdir).load()
    snap3 = ProjectProductionInspect(name, pdir).snapshot()
    live_wp = build_incremental_workplan(snap3, "", "complete_project", pdir)
    assert live_wp["scene_targets"] == ["s03"]  # s02 已补齐，只剩无镜头的 s03
    # C1 hardening: an unshot scene remains incomplete even when all existing
    # Shot finals are present, and the frozen workplan still authorizes s03.
    assert validate_asset_set(pdir)["complete"] is False
    assert validate_asset_set(pdir)["missing"] == []
    assert validate_asset_set(pdir)["missing_scenes"] == ["s03"]


def test_c1_cross_hop_paid_ledger_cannot_restart_without_source_snapshot(client, monkeypatch, tmp_path):
    """A new worker may not replay an archived source after a paid C1 hop."""
    from tools import video_task_state as vts
    from tools import e2e_autonomous_run as e2e
    from tools import full_project_cloud_acceptance as acc
    name, pdir, project, snap = _ctx(client)
    documents = {}
    monkeypatch.setattr(vts, 'task_index', lambda _tok, _nonce, filename, **kw:
                        documents.setdefault(filename, FakeIndex()))
    monkeypatch.setattr(e2e, 'ensure_branch', lambda index: None)
    monkeypatch.delenv('RESUME_RUN_ID', raising=False)
    task = TaskSpec(intent='complete_project', source_project=name,
                    request_text='继续完成旧项目缺失的镜头')
    out = tmp_path / 'c1-paid'
    out.mkdir()
    try:
        asyncio.run(acc._run_continuation_acceptance('c1-paid-1', out, {}, 'tok', task))
        from tools.video_task_state import ProductionCheckpoint
        index = documents['full-project-production.json']
        cp = ProductionCheckpoint(index, 'c1-paid-1',
                                  index.blob['doc']['request_sha256'])
        cp.save(phase='FAILED', video_attempts=[{
            'ordinal': 1, 'seconds': 8, 'video_id': 'paid-receipt',
            'state': 'ACCEPTED', 'prompt_sha256': 'fake'}])
        with pytest.raises(RuntimeError, match='requires RESUME_RUN_ID'):
            asyncio.run(acc._run_continuation_acceptance('c1-paid-1', out, {}, 'tok', task))
    finally:
        import shutil
        shutil.rmtree(pdir, ignore_errors=True)


def test_c1_crash_after_stage0_freeze_before_checkpoint_binding(client, monkeypatch, tmp_path):
    """Stage0 CAS can commit before the first execution-identity CAS; retry must recover."""
    from tools import full_project_cloud_acceptance as acc
    from tools import video_task_state as vts
    from tools import e2e_autonomous_run as e2e
    from tools.video_task_state import ProductionCheckpoint

    name, pdir, _, _ = _ctx(client)
    indexes = {}
    monkeypatch.setattr(vts, 'task_index', lambda token, nonce, filename='state.json',
                        transport=None: indexes.setdefault(filename, FakeIndex()))
    monkeypatch.setattr(e2e, 'ensure_branch', lambda idx: None)
    env = {'PRODUCTION_REQUEST': '继续这个项目，把没做完的做完',
           'SOURCE_PROJECT': name, 'FILM_TARGET_SECONDS': 'auto'}
    monkeypatch.setattr(acc.os, 'environ', env)
    ts = acc.normalize_dispatch_input_to_task_spec('n', environ=env)
    original_save = ProductionCheckpoint.save
    injected = {'yes': True}

    def fail_one_cas(self, **values):
        if values.get('phase') == 'STAGE0_BOUND' and injected['yes']:
            injected['yes'] = False
            raise RuntimeError('injected crash after Stage0.freeze')
        return original_save(self, **values)

    monkeypatch.setattr(ProductionCheckpoint, 'save', fail_one_cas)
    output = tmp_path / 'out'
    output.mkdir()
    with pytest.raises(RuntimeError, match='injected crash'):
        asyncio.run(acc._run_continuation_acceptance('c1-crash-gap', output,
                                                     {}, 'tok', ts))
    frozen = indexes['producer-stage0.json'].read()['doc']
    assert frozen['plan_fingerprint']
    assert indexes['full-project-production.json'].read() is None
    summary = {}
    pname, profile, cp, _, _ = asyncio.run(
        acc._run_continuation_acceptance('c1-crash-gap', output, summary, 'tok', ts))
    assert pname == name
    assert summary['stage0_checkpoint_rebound'] is True
    assert summary['plan_fingerprint'] == frozen['plan_fingerprint']
    assert cp.current['doc']['plan_fingerprint'] == frozen['plan_fingerprint']
    assert cp.current['doc']['authorized_video_seconds'] == profile['budget']


# ============ run_acceptance 固定档路径 project_name 绑定（run #43 回归锁）============
# PR #22 把 project_name 赋值收窄到 AUTO 分支，固定档非 resume 路径在生产段
# _resolve_production_project(target, restored, project_name, ...) 处 UnboundLocalError。
# run_acceptance 太重无法单测起全套，用 AST 数据流检查代替（发现即失败）。

def test_run_acceptance_fixed_profile_binds_project_name_before_use():
    import ast
    from tools import full_project_cloud_acceptance as acc
    tree = ast.parse(open(acc.__file__, encoding='utf-8').read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == 'run_acceptance')
    assigned, used = [], []
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and n.id == 'project_name':
            (assigned if isinstance(n.ctx, ast.Store) else used).append(n.lineno)
    assert used, 'run_acceptance 不再引用 project_name——回归锁过期，请复核'
    first_use = min(used)
    stores_before_use = [l for l in assigned if l <= first_use]
    assert stores_before_use, (
        'project_name 在绑定前被引用——固定档/任意路径 UnboundLocalError 复发（run #43 同因）')
