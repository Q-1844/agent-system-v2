# tests/test_ai_7c_s4_targeted.py
# AI-7C-S S4 M4 回归测试（§21）。独立 reviewer（虽 429 死亡）留下关键疑点：
# preflight_project 查全 scene -> unaffected 未规划 scene NO_PRODUCTION_PLAN -> ready=False ->
# targeted run 在 plan-freeze 前就崩，M4 的 plan-freeze-only-scenes_to_run 修复形同虚设。
# 修复：preflight_project 加 target_scene_ids，targeted 只查 scenes_to_run。
import types

from src.core.models import Project, Scene, Shot, StyleAnchor


def _proj_2scenes():
    return Project(
        name="d", style=StyleAnchor(),
        scenes=[Scene(scene_id="s01", summary="a", characters=[],
                      shots=[Shot(shot_id="s01_c01", scene_id="s01", index=1)]),
                Scene(scene_id="s02", summary="b", characters=[],
                      shots=[Shot(shot_id="s02_c01", scene_id="s02", index=1)])])


def _patch_preflight(monkeypatch, plans):
    from src.ai.autopilot import SceneRunPreflight

    def stub_preflight(project, scene, plan, respect_locked, *, has_active_run=False):
        # 简化：有 plan -> ready；无 plan -> NO_PRODUCTION_PLAN（聚焦 scene 过滤，非 plan 内容校验）
        return SceneRunPreflight(
            scene_id=scene.scene_id, ready=plan is not None,
            shot_count=len(scene.shots), plan_id=getattr(plan, "plan_id", None),
            plan_status=getattr(plan, "status", None),
            issues=[] if plan is not None else ["NO_PRODUCTION_PLAN"])

    monkeypatch.setattr("src.ai.project_autopilot.preflight_scene_autopilot", stub_preflight)
    monkeypatch.setattr("routers.production_planner._read_plans", lambda d: plans)
    monkeypatch.setattr("src.ai.project_autopilot.get_autopilot_manager",
                        lambda: types.SimpleNamespace(find_active_run=lambda *a, **k: None))


def test_m4_preflight_targeted_skips_unplanned_unaffected_scene(tmp_path, monkeypatch):
    """M4/§53：targeted run preflight 只查 scenes_to_run；unaffected 未规划 scene 不阻断。"""
    from src.ai.project_autopilot import preflight_project
    plans = {"s01": types.SimpleNamespace(status="approved", plan_id="p01")}  # s02 无 plan
    _patch_preflight(monkeypatch, plans)

    pre_t = preflight_project(_proj_2scenes(), tmp_path, project_name="d",
                              target_scene_ids=["s01"])
    assert [s.scene_id for s in pre_t.scenes] == ["s01"], \
        "M4：targeted preflight 只查 s01，不应查 unaffected s02"
    assert pre_t.ready is True, "s01 有 plan + s02 不查 -> targeted 不阻断"

    pre_all = preflight_project(_proj_2scenes(), tmp_path, project_name="d")
    assert [s.scene_id for s in pre_all.scenes] == ["s01", "s02"], "非 targeted 仍查全部（既有行为）"
    s02 = next(s for s in pre_all.scenes if s.scene_id == "s02")
    assert "NO_PRODUCTION_PLAN" in s02.issues, "s02 无 plan 应标 NO_PRODUCTION_PLAN"
    assert pre_all.ready is False, "全查时 s02 无 plan -> 阻断（未改非 targeted 行为）"


def test_m4_preflight_non_targeted_unchanged(tmp_path, monkeypatch):
    """M4 回归保护：无 target_scene_ids 时行为不变（查全部，全 plan -> ready）。"""
    from src.ai.project_autopilot import preflight_project
    plans = {"s01": types.SimpleNamespace(status="approved", plan_id="p01"),
             "s02": types.SimpleNamespace(status="approved", plan_id="p02")}
    _patch_preflight(monkeypatch, plans)
    pre = preflight_project(_proj_2scenes(), tmp_path, project_name="d")
    assert pre.scene_count == 2
    assert pre.ready is True
