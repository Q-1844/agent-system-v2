# tests/test_producer_planning_models.py
"""PR A：Producer 规划契约模型 + 意图分流 + 只读检视 adapter。

全部 mock：零外部 API、零媒体调用、零仓库状态写入。
对齐执行指令 v2 §2（契约）、§7 PR A 必测、§8 T01/T02/T04/T07/T13/T16 的
PR A 子集（无值守接线部分在 test_producer_planning_skill.py 的反向断言）。
"""
import json

import pytest

from src.ai.producer_planning_models import (
    APPROXIMATE_DEFAULT_TOLERANCE,
    DurationIntent,
    PlanningContractError,
    PlannedScene,
    ProducerPlanningResult,
    SceneBudget,
    TaskSpec,
    auto_scene_ids,
    canonical_json,
    suggest_intent,
)
from src.ai.project_production_inspect import (
    BLOCKER_PROJECT_DIR_MISSING,
    BLOCKER_PROJECT_STORE_MISSING,
    ProjectProductionInspect,
)
from src.core.models import Character, Project, Scene, Shot, StyleAnchor
from src.core.store import ProjectStore


# ============ 辅助 ============

def _create_spec(request_text="拍一个热气球穿越云海、在日出时抵达山谷的视频，大约两分钟",
                 mode="approximate", target=120):
    duration = DurationIntent(mode=mode, target_seconds=target) \
        if mode in ("exact", "approximate") else DurationIntent(mode=mode)
    return TaskSpec(intent="create", request_text=request_text, duration=duration)


def _three_scene_plan(ts, per_target=40, video_cap=170, output_kind="final_video",
                      intent="create"):
    ids = auto_scene_ids(3)
    return ProducerPlanningResult(
        task_spec_sha256=ts.sha256(), intent=intent, output_kind=output_kind,
        story_text="热气球穿越云海", style={"art_style": "photorealistic"},
        scenes=[PlannedScene(scene_id=i, title=f"段{i}", summary=f"内容{i}")
                for i in ids],
        scene_budgets=[SceneBudget(scene_id=i, min_s=per_target - 8,
                                   max_s=per_target + 8, target_s=per_target,
                                   shot_min=3, shot_max=5) for i in ids],
        execution_bounds={"text_calls": 60, "image_calls": 40,
                          "video_seconds": video_cap})


def _make_project_dir(tmp_path, with_shots=True):
    pdir = tmp_path / "deepsea"
    shots = [Shot(shot_id="s01_c01", scene_id="s01", index=1, description="下潜",
                  characters=[], target_duration_s=5.0)] if with_shots else []
    proj = Project(
        name="deepsea", style=StyleAnchor(),
        story_text="深海故事",
        characters=[Character(name="无人机", description_en="uav")],
        scenes=[Scene(scene_id="s01", summary="下潜段", characters=["无人机"],
                      shots=shots)])
    ProjectStore(pdir).save(proj)
    return pdir


# ============ TaskSpec：身份与哈希 ============

def test_task_spec_schema_and_roundtrip():
    ts = _create_spec()
    assert ts.to_dict()["schema_version"] == "producer-task-v1"
    ts2 = TaskSpec.from_dict(ts.to_dict())
    assert ts2.sha256() == ts.sha256()
    assert TaskSpec.from_dict(ts2.to_dict()).sha256() == ts.sha256()


def test_task_spec_hash_stable_against_key_order():
    ts = _create_spec()
    d = ts.to_dict()
    # 打乱键序后 canonical json 不变
    shuffled = dict(reversed(list(d.items())))
    assert canonical_json(shuffled) == canonical_json(d)


def test_task_spec_hash_changes_with_content():
    a, b = _create_spec(), _create_spec(request_text="换一个故事")
    assert a.sha256() != b.sha256()
    c = TaskSpec(intent="create", request_text="拍热气球视频",
                 duration=DurationIntent(mode="exact", target_seconds=120))
    d = TaskSpec(intent="create", request_text="拍热气球视频",
                 duration=DurationIntent(mode="approximate", target_seconds=120))
    assert c.sha256() != d.sha256()  # exact != approximate：身份必须区分软硬


def test_task_spec_rejects_bad_intent_and_empty_text():
    with pytest.raises(PlanningContractError):
        TaskSpec(intent="make_coffee", request_text="x")
    with pytest.raises(PlanningContractError):
        TaskSpec(intent="create", request_text="  ")


def test_task_spec_source_project_rules():
    with pytest.raises(PlanningContractError):
        TaskSpec(intent="create", request_text="x", source_project="deepsea")
    with pytest.raises(PlanningContractError):  # 继续类必须有 source_project
        TaskSpec(intent="add_assets", request_text="补素材")
    with pytest.raises(PlanningContractError):  # sequel 必须指明续谁
        TaskSpec(intent="sequel", request_text="拍新续集")
    # sequel 合法形态：带 source_project（continuity anchors 来源）
    TaskSpec(intent="sequel", request_text="给这条片子拍一个新续集",
             source_project="deepsea")


# ============ DurationIntent：四种模式 ============

def test_duration_exact_requires_target():
    with pytest.raises(PlanningContractError):
        DurationIntent(mode="exact")
    ok = DurationIntent(mode="exact", target_seconds=120)
    assert ok.effective_window() == (120, 120)


def test_duration_range_rules():
    good = DurationIntent(mode="range", range_seconds=(100, 140))
    assert good.effective_window() == (100, 140)
    with pytest.raises(PlanningContractError):
        DurationIntent(mode="range", range_seconds=(140, 100))
    with pytest.raises(PlanningContractError):
        DurationIntent(mode="range")


def test_duration_approximate_window_is_policy_not_fake_precision():
    d = DurationIntent(mode="approximate", target_seconds=120)
    ev = d.to_evidence()
    assert ev["effective_window_seconds"] == [102, 138]
    assert ev["effective_window_source"] == "policy_derived_not_user_precision"
    assert ev["tolerance_used"] == APPROXIMATE_DEFAULT_TOLERANCE
    # 显式容差策略可覆盖
    d2 = DurationIntent(mode="approximate", target_seconds=120, tolerance=0.2)
    assert d2.effective_window() == (96, 144)


def test_duration_unspecified_allows_null_and_no_window():
    d = DurationIntent(mode="unspecified")
    assert d.target_seconds is None and d.range_seconds is None
    assert d.effective_window() is None


# ============ suggest_intent：四类核心任务分流 ============

def test_intent_create_with_duration():
    intent, _ = suggest_intent(
        "拍一个热气球穿越云海的视频，大概两分钟")
    assert intent == "create"


def test_intent_create_without_duration():
    intent, _ = suggest_intent("做个卫星环绕地球的视频")
    assert intent == "create"


def test_intent_continue_project_not_create():
    """T04：complete_project 不得误分成 create。"""
    intent, _ = suggest_intent("继续这个项目，把没做完的做完成",
                               source_project="deepsea")
    assert intent == "complete_project"


def test_intent_add_assets():
    intent, _ = suggest_intent("把深海项目 s02 后面没生成出来的素材补齐",
                               source_project="deepsea")
    assert intent == "add_assets"


def test_intent_extend_and_sequel_and_resume():
    assert suggest_intent("把这条片子再延长一分钟",
                          source_project="deepsea")[0] == "extend_existing"
    assert suggest_intent("给这条片子拍一个新续集",
                          source_project="deepsea")[0] == "sequel"
    assert suggest_intent("恢复上次运行",
                          source_project="deepsea")[0] == "resume_run"


@pytest.mark.parametrize("text", [
    "继续这个项目", "接着把项目做完", "继续上次的项目",
    "把没做完的素材补齐", "把项目继续下去",
])
def test_intent_guard_continuation_never_create(text):
    """有 source_project 时，继续类语义永不判为 create（安全分流铁律）。"""
    intent, _ = suggest_intent(text, source_project="deepsea")
    assert intent != "create"


def test_intent_explicit_new_with_source_project_still_create():
    intent, _ = suggest_intent("在这个项目基础上另拍一个新短片",
                               source_project="deepsea")
    assert intent == "create"


# ============ SceneBudget：确定性可行性 ============

def test_scene_budget_valid():
    sb = SceneBudget(scene_id="s01", min_s=15, max_s=40, target_s=30,
                     shot_min=3, shot_max=5)
    ev = sb.to_evidence()
    assert ev["scene_id"] == "s01" and ev["min_s"] == 15 and ev["max_s"] == 40
    assert ev["shot_min"] == 3 and ev["shot_max"] == 5
    assert ev["target_s"] == 30


def test_scene_budget_infeasible_rejected():
    # shot_min(4) * 4s = 16 > max_s(15)：塞不下
    with pytest.raises(PlanningContractError):
        SceneBudget(scene_id="s01", min_s=15, max_s=15, shot_min=4, shot_max=5)
    # shot_max(2) * 11s = 22 < min_s(30)：盖不住
    with pytest.raises(PlanningContractError):
        SceneBudget(scene_id="s01", min_s=30, max_s=40, shot_min=1, shot_max=2)


def test_scene_budget_target_outside_window_rejected():
    with pytest.raises(PlanningContractError):
        SceneBudget(scene_id="s01", min_s=20, max_s=30, target_s=35,
                    shot_min=3, shot_max=5)


def test_scene_budget_all_zero_means_no_new_video():
    """asset_set 的纯图片/参考资产场：全零预算合法（无新增视频工作）。"""
    sb = SceneBudget(scene_id="s01", min_s=0, max_s=0, shot_min=0, shot_max=0)
    assert (sb.min_s, sb.max_s, sb.shot_min, sb.shot_max) == (0, 0, 0, 0)
    with pytest.raises(PlanningContractError):
        SceneBudget(scene_id="s01", min_s=0, max_s=0, shot_min=0, shot_max=0,
                    target_s=5)
    with pytest.raises(PlanningContractError):
        SceneBudget(scene_id="s01", min_s=0, max_s=10, shot_min=0, shot_max=0)


# ============ ProducerPlanningResult ============

def test_planning_result_valid_and_hash_stable():
    ts = _create_spec()
    res = _three_scene_plan(ts)
    assert res.status == "PROPOSED"
    assert res.output_kind == "final_video"
    assert res.plan_sha256() == _three_scene_plan(ts).plan_sha256()
    ev = res.to_evidence()
    assert len(ev["scenes"]) == 3
    assert sum(b["target_s"] for b in ev["scene_budgets"]) == 120
    assert ev["execution_bounds"]["video_seconds"] == 170
    assert ev["plan_sha256"] == res.plan_sha256()


def test_planning_result_orphan_budget_rejected():
    ts = _create_spec()
    ids = auto_scene_ids(3)
    with pytest.raises(PlanningContractError):
        ProducerPlanningResult(
            task_spec_sha256=ts.sha256(), intent="create", story_text="x",
            scenes=[PlannedScene(scene_id=i, title="t", summary="s")
                    for i in ids],
            scene_budgets=[SceneBudget(scene_id=i, min_s=20, max_s=40,
                                       shot_min=3, shot_max=5)
                           for i in ids[:2]],  # 缺 s03 的预算
            execution_bounds={"text_calls": 50, "image_calls": 30,
                              "video_seconds": 200})


def test_planning_result_planned_over_ceiling_rejected():
    ts = _create_spec()
    with pytest.raises(PlanningContractError):
        _three_scene_plan(ts, per_target=60, video_cap=170)  # 180 > 170


def test_planning_result_final_video_requires_video_cap():
    ts = _create_spec()
    ids = auto_scene_ids(2)
    with pytest.raises(PlanningContractError):
        ProducerPlanningResult(
            task_spec_sha256=ts.sha256(), intent="create", story_text="x",
            scenes=[PlannedScene(scene_id=i, title="t", summary="s")
                    for i in ids],
            scene_budgets=[SceneBudget(scene_id=i, min_s=20, max_s=40,
                                       shot_min=3, shot_max=5) for i in ids],
            execution_bounds={"text_calls": 50, "image_calls": 30})


def test_planning_result_asset_set_kind():
    """T16：补素材/纯图片任务允许 asset_set，无 final.mp4 门槛假设。"""
    ts = TaskSpec(intent="add_assets", request_text="补齐缺失素材",
                  source_project="deepsea")
    res = ProducerPlanningResult(
        task_spec_sha256=ts.sha256(), intent="add_assets",
        output_kind="asset_set", story_text="补素材",
        scenes=[PlannedScene(scene_id="s02", title="段", summary="补")],
        scene_budgets=[SceneBudget(scene_id="s02", min_s=0, max_s=0,
                                    shot_min=0, shot_max=0)],
        execution_bounds={"text_calls": 10, "image_calls": 5},
        incremental_actions=(
            {"action_kind": "reuse_asset", "target": "s02_c01",
             "evidence": {"video_id": "task_xxx"}},
            {"action_kind": "generate_missing", "target": "s02_c03"},
        ))
    assert res.output_kind == "asset_set"
    assert len(res.incremental_actions) == 2


def test_planning_result_incremental_action_contract():
    ts = TaskSpec(intent="add_assets", request_text="补",
                  source_project="deepsea")
    with pytest.raises(PlanningContractError):
        ProducerPlanningResult(
            task_spec_sha256=ts.sha256(), intent="add_assets",
            output_kind="asset_set", story_text="x",
            scenes=[PlannedScene(scene_id="s01", title="t", summary="s")],
            scene_budgets=[SceneBudget(scene_id="s01", min_s=0, max_s=0,
                                       shot_min=0, shot_max=0)],
            execution_bounds={"text_calls": 10, "image_calls": 5},
            incremental_actions=(
                {"action_kind": "reuse_asset", "target": "s01_c01"},  # 缺 evidence
            ))


def test_planning_result_status_proposed_only():
    """PR A 只产出 PROPOSED；FROZEN 属 PR B1（冻结器）。"""
    ts = _create_spec()
    ids = auto_scene_ids(2)
    with pytest.raises(PlanningContractError):
        ProducerPlanningResult(
            task_spec_sha256=ts.sha256(), intent="create", story_text="x",
            status="FROZEN",
            scenes=[PlannedScene(scene_id=i, title="t", summary="s")
                    for i in ids],
            scene_budgets=[SceneBudget(scene_id=i, min_s=20, max_s=40,
                                       shot_min=3, shot_max=5) for i in ids],
            execution_bounds={"text_calls": 50, "image_calls": 30,
                              "video_seconds": 200})


# ============ ProjectProductionInspect（只读 adapter） ============

def test_inspect_missing_dir_fail_closed(tmp_path):
    snap = ProjectProductionInspect("ghost", tmp_path / "ghost").snapshot()
    assert snap.exists is False
    assert BLOCKER_PROJECT_DIR_MISSING in snap.blockers
    assert snap.scenes == []


def test_inspect_missing_store_fail_closed(tmp_path):
    pdir = tmp_path / "empty"
    pdir.mkdir()
    snap = ProjectProductionInspect("empty", pdir).snapshot()
    assert snap.exists is False
    assert BLOCKER_PROJECT_STORE_MISSING in snap.blockers


def _manager_project(client, name="_test_planning_inspect", with_shots=True):
    """按仓库测试惯例经 app 的 ProjectManager 建项目（deps 单例由 client 启动）。"""
    import shutil
    from deps import get_project_manager
    pm = get_project_manager()
    shutil.rmtree(pm.base_dir / name, ignore_errors=True)
    pdir = pm.create_project(name)
    shots = [Shot(shot_id="s01_c01", scene_id="s01", index=1, description="下潜",
                  characters=[], target_duration_s=5.0)] if with_shots else []
    proj = Project(
        name=name, style=StyleAnchor(), story_text="深海故事",
        characters=[Character(name="无人机", description_en="uav")],
        scenes=[Scene(scene_id="s01", summary="下潜段", characters=["无人机"],
                      shots=shots)])
    ProjectStore(pdir).save(proj)
    return name, pdir


def test_inspect_real_project_snapshot(client):
    name, pdir = _manager_project(client)
    snap = ProjectProductionInspect(name, pdir).snapshot()
    assert snap.exists is True
    assert snap.scene_count == 1
    assert snap.scenes[0].scene_id == "s01"
    assert snap.scenes[0].shot_count == 1
    assert snap.scenes[0].summary == "下潜段"
    # 有镜头、无 plan -> NEED_PRODUCTION_PLAN（分类语义由 inspect_project 原样保留）
    assert snap.scenes[0].state == "NEED_PRODUCTION_PLAN"


def test_inspect_no_scenes_blocker(client):
    name, pdir = _manager_project(client, with_shots=False,
                                  name="_test_planning_bare")
    proj = ProjectStore(pdir).load()
    proj.scenes = []
    ProjectStore(pdir).save(proj)
    snap = ProjectProductionInspect(name, pdir).snapshot()
    assert snap.exists is True
    assert snap.scene_count == 0
    assert any("NO_SCENES" in b for b in snap.blockers)


def test_inspect_is_read_only(client):
    """物理只读：快照前后项目文件字节不变、目录文件集合不增。"""
    import hashlib
    name, pdir = _manager_project(client, name="_test_planning_ro")
    pj = pdir / "output" / "project.json"
    before = hashlib.sha256(pj.read_bytes()).hexdigest()
    files_before = sorted(str(p.relative_to(pdir))
                          for p in pdir.rglob("*") if p.is_file())
    ProjectProductionInspect(name, pdir).snapshot()
    assert hashlib.sha256(pj.read_bytes()).hexdigest() == before
    files_after = sorted(str(p.relative_to(pdir))
                         for p in pdir.rglob("*") if p.is_file())
    assert files_after == files_before


def test_inspect_fields_immutable_after_freeze(tmp_path):
    adapter = ProjectProductionInspect("deepsea", tmp_path)
    with pytest.raises(AttributeError):
        adapter.project_name = "hijack"


# ============ Bug F（2026-11-01 daycarry12m 派发实测回填）：意图分类否定语境 ============

def test_suggest_intent_ignores_negated_keyword_hits():
    """负面约束里的关键词不算正向意图信号。

    现场：run #58 任务文案「不得用循环、重复片段、静帧或延长音轨凑时长」
    ——「延长」被当正向延长信号 → 误判 extend_existing → 无 source_project
    32 秒即退。分句级否定检测：顿号是并列分隔符，否定词作用于整个列表。"""
    from src.ai.producer_planning_models import suggest_intent

    # run #58 原文节选（Bug F 现场）→ create
    r58 = ("请从零自主制作一部完整的电影短片，成片总时长必须在720-780秒之间。"
           "所有必要镜头采用本次新生成的真实运动视频；"
           "不得用循环、重复片段、静帧或延长音轨凑时长，"
           "不复用历史影片素材抵扣本次生成量。"
           "每日额度耗尽时保存进度并进入等待，额度刷新后沿同一个 nonce 和项目自动接续。")
    got, reason = suggest_intent(r58)
    assert got == "create", f"run #58 原文应判 create，实判 {got}（{reason}）"

    # 正反例矩阵
    assert suggest_intent("把这部片子延长到十分钟", source_project="p")[0] == "extend_existing"
    assert suggest_intent("请帮我加长视频")[0] == "extend_existing"
    assert suggest_intent("不要恢复运行")[0] == "create"
    assert suggest_intent("恢复运行")[0] == "resume_run"
    assert suggest_intent("不得用静帧或延长音轨凑时长")[0] == "create"
    assert suggest_intent("把项目做完", source_project="p")[0] == "complete_project"
    # 并列否定：否定词与关键词隔着顿号列表也覆盖
    assert suggest_intent("禁止用循环、重复片段或追加素材充数")[0] == "create"
    # 关键词多次出现：一处否定一处正向 → 仍算命中（正向那次生效）
    assert suggest_intent("不要延长音轨；请把片子延长到十分钟") == ("extend_existing", "命中延长信号: 延长")
