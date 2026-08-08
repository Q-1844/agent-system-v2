"""Phase AI-4A：AI 制作规划测试。

重点（§二十五）：
- sanitize_plan：第一镜强制 independent；reference/inherit source 自动=紧邻上一镜；
  无效 opening/link_mode -> 默认；AI 漏 Shot -> 安全默认；AI 返回未知 shot_id -> 忽略。
- derivation：generate_first_frame = opening != inherit_previous；
  generate_last_frame = link == keyframes；requires_previous_video_end；source_shot_id。
- approve 不写正式 shot.opening_source（§六/§二十：plan 是意图，Executor 在 AI-4B 生效）。
- stale（§八/§二十二）：scene summary/characters / shot 增删/顺序 / description / characters /
  target_duration_s 变 -> stale；生成资产 / views / 候选变 -> 不 stale。
- run_seed + choose_candidate 确定性可复现（§十一）。
AI 调用用 FakeAgnes。
"""
import asyncio
import shutil
import pytest

import deps
from deps import get_project_manager
from src.core.store import ProjectStore
from src.core.models import Project, Scene, Character, StyleAnchor, Shot, Asset
from src.ai.production_planner import (
    sanitize_plan, rederive_from_edits, is_plan_stale, shot_structure_snapshot,
    new_run_seed, choose_candidate, SceneProductionPlan, PlannedShot,
    ProductionPlannerWorkflow,
)
from routers.production_planner import _plans_path, _read_plans, _write_plans

PROJECT_NAME = "_test_production_planner"


class FakeAgnes:
    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self.calls = []

    async def chat(self, messages, *, temperature=0.7, max_tokens=2048,
                   thinking=False, json_mode=False):
        self.calls.append({"json_mode": json_mode})
        if not self._responses:
            return ""
        return self._responses.pop(0)


def _make_project(pname, *, story="故事。", chars=None, scenes=None):
    pm = get_project_manager()
    shutil.rmtree(pm.base_dir / pname, ignore_errors=True)
    pdir = pm.create_project(pname)
    proj = Project(name=pname, style=StyleAnchor(art_style="anime", aspect_ratio="16:9"),
                   story_text=story,
                   characters=[Character(name=n, description_en=d) for n, d in (chars or [])],
                   scenes=scenes or [])
    ProjectStore(pdir).save(proj)
    return pdir


def _shot(scene_id, idx, desc, chars=None, dur=5.0):
    return Shot(shot_id=f"{scene_id}_c{idx:02d}", scene_id=scene_id, index=idx,
                description=desc, characters=chars or [], target_duration_s=dur)


def _mem_scene(scene_id, summary, chars, shots_spec):
    """内存 Scene（不落盘，不依赖 project_manager）。shots_spec=[(idx,desc,chars,dur)]。
    供 sanitize_plan 单元测试用；endpoint/stale 测试用 _proj（落盘 + client fixture）。"""
    return Scene(scene_id=scene_id, summary=summary, characters=list(chars),
                 shots=[_shot(scene_id, idx, desc, c, dur) for idx, desc, c, dur in shots_spec])


def _scene_A():
    return _mem_scene("sA", "机甲启动 / 城市远景 / 林夏在基地", ["赤焰守卫", "林夏"], [
        (1, "机甲在机库灯光逐级启动", ["赤焰守卫"], 5),
        (2, "城市远处全景建立镜头", [], 5),
        (3, "林夏在基地查看屏幕", ["林夏"], 5)])


def _scene_B():
    return _mem_scene("sB", "机甲在机库确认信号", ["赤焰守卫"], [
        (1, "机甲在机库中起身", ["赤焰守卫"], 5),
        (2, "近距离表现它转头确认信号", ["赤焰守卫"], 3),
        (3, "机甲准备迈步出发", ["赤焰守卫"], 5)])


def _scene_C():
    return _mem_scene("sC", "角色抬起扫描装置的连续动作", ["林夏"], [
        (1, "林夏开始抬起扫描装置", ["林夏"], 4),
        (2, "扫描装置继续展开", ["林夏"], 4),
        (3, "扫描装置完成启动", ["林夏"], 4)])


def _proj(pname):
    """3 个 Scene 覆盖 §二十五 Case A/B/C。"""
    # Case A：三个独立镜头（不同主体/地点）
    sA = Scene(scene_id="sA", summary="机甲启动 / 城市远景 / 林夏在基地",
               characters=["赤焰守卫", "林夏"], shots=[
                   _shot("sA", 1, "机甲在机库灯光逐级启动", ["赤焰守卫"], 5),
                   _shot("sA", 2, "城市远处全景建立镜头", [], 5),
                   _shot("sA", 3, "林夏在基地查看屏幕", ["林夏"], 5),
               ])
    # Case B：同角色同地点，切换视觉重点
    sB = Scene(scene_id="sB", summary="机甲在机库确认信号",
               characters=["赤焰守卫"], shots=[
                   _shot("sB", 1, "机甲在机库中起身", ["赤焰守卫"], 5),
                   _shot("sB", 2, "近距离表现它转头确认信号", ["赤焰守卫"], 3),
                   _shot("sB", 3, "机甲准备迈步出发", ["赤焰守卫"], 5),
               ])
    # Case C：一个动作拆成连续三镜（真正动作接力）
    sC = Scene(scene_id="sC", summary="角色抬起扫描装置的连续动作",
               characters=["林夏"], shots=[
                   _shot("sC", 1, "林夏开始抬起扫描装置", ["林夏"], 4),
                   _shot("sC", 2, "扫描装置继续展开", ["林夏"], 4),
                   _shot("sC", 3, "扫描装置完成启动", ["林夏"], 4),
               ])
    return _make_project(pname, chars=[("赤焰守卫", "机甲"), ("林夏", "少女")],
                         scenes=[sA, sB, sC])


# AI 返回模拟（Case A 全 independent；Case B 后两镜 reference_previous；
#  Case C 后两镜 inherit_previous）
PLAN_A = ('{"overview":"三镜独立建立","shots":['
          '{"shot_id":"sA_c01","opening_source":"independent","link_mode":"i2v","reason":"建立镜头"},'
          '{"shot_id":"sA_c02","opening_source":"independent","link_mode":"i2v","reason":"换地点远景"},'
          '{"shot_id":"sA_c03","opening_source":"independent","link_mode":"i2v","reason":"切到林夏"}]}')

PLAN_B = ('{"overview":"同场景切视觉重点","shots":['
          '{"shot_id":"sB_c01","opening_source":"independent","link_mode":"keyframes","reason":"起身有明确结束状态"},'
          '{"shot_id":"sB_c02","opening_source":"reference_previous","link_mode":"i2v","reason":"连续但需新构图近景"},'
          '{"shot_id":"sB_c03","opening_source":"reference_previous","link_mode":"keyframes","reason":"迈步有结束姿态"}]}')

PLAN_C = ('{"overview":"动作接力","shots":['
          '{"shot_id":"sC_c01","opening_source":"independent","link_mode":"keyframes","reason":"开始抬起"},'
          '{"shot_id":"sC_c02","opening_source":"inherit_previous","link_mode":"keyframes","reason":"直接继续展开"},'
          '{"shot_id":"sC_c03","opening_source":"inherit_previous","link_mode":"keyframes","reason":"完成启动"}]}')


@pytest.fixture
def planner_project(client):
    pdir = _proj(PROJECT_NAME)
    yield pdir
    shutil.rmtree(get_project_manager().get_project_dir(PROJECT_NAME), ignore_errors=True)


@pytest.fixture
def fake_agnes_swap(client):
    fake = FakeAgnes()
    real = deps.agnes_client
    deps.agnes_client = fake
    yield fake
    deps.agnes_client = real


def _ai(obj):
    """把 python dict 当 AI 返回。"""
    import json as _json
    return _json.loads(_json.dumps(obj))


# ============ 1. sanitize_plan 单元 ============
def test_first_shot_forced_independent():
    """第一镜即使 AI 说 reference/inherit 也强制 independent（§十四）。"""
    sc = _scene_B()
    ai = {"overview": "", "shots": [
        {"shot_id": "sB_c01", "opening_source": "inherit_previous", "link_mode": "i2v", "reason": "x"}]}
    plan = sanitize_plan(sc, ai, run_seed=123, project_name=PROJECT_NAME)
    assert plan.shots[0].opening_source == "independent"
    assert plan.shots[0].source_shot_id is None
    assert plan.shots[0].requires_previous_video_end is False


def test_case_A_mostly_independent():
    sc = _scene_A()
    ai = _ai({"overview": "x", "shots": [
        {"shot_id": "sA_c01", "opening_source": "independent", "link_mode": "i2v", "reason": "a"},
        {"shot_id": "sA_c02", "opening_source": "independent", "link_mode": "i2v", "reason": "b"},
        {"shot_id": "sA_c03", "opening_source": "independent", "link_mode": "i2v", "reason": "c"}]})
    plan = sanitize_plan(sc, ai, run_seed=1, project_name=PROJECT_NAME)
    assert all(s.opening_source == "independent" for s in plan.shots)
    assert all(s.source_shot_id is None for s in plan.shots)
    assert all(s.requires_previous_video_end is False for s in plan.shots)
    assert all(s.generate_first_frame is True for s in plan.shots)


def test_case_B_reference_source_is_previous():
    """reference_previous 的 source_shot_id 自动 = 紧邻上一镜（§九）。"""
    sc = _scene_B()
    plan = sanitize_plan(sc, _ai({"overview": "", "shots": [
        {"shot_id": "sB_c01", "opening_source": "independent", "link_mode": "keyframes", "reason": "x"},
        {"shot_id": "sB_c02", "opening_source": "reference_previous", "link_mode": "i2v", "reason": "y"},
        {"shot_id": "sB_c03", "opening_source": "reference_previous", "link_mode": "keyframes", "reason": "z"}]}),
        run_seed=1, project_name=PROJECT_NAME)
    assert plan.shots[0].source_shot_id is None
    assert plan.shots[1].source_shot_id == "sB_c01"
    assert plan.shots[2].source_shot_id == "sB_c02"
    assert plan.shots[1].requires_previous_video_end is True
    assert plan.shots[2].requires_previous_video_end is True
    # reference 仍生成新首帧（§三：reference 生成本镜新首帧，只用上一镜末帧作 reference）
    assert plan.shots[1].generate_first_frame is True


def test_case_C_inherit_skips_first_frame():
    """inherit_previous 不生成新首帧，直接沿用上一镜末帧（§三/§十）。"""
    sc = _scene_C()
    plan = sanitize_plan(sc, _ai({"overview": "", "shots": [
        {"shot_id": "sC_c01", "opening_source": "independent", "link_mode": "keyframes", "reason": "x"},
        {"shot_id": "sC_c02", "opening_source": "inherit_previous", "link_mode": "keyframes", "reason": "y"},
        {"shot_id": "sC_c03", "opening_source": "inherit_previous", "link_mode": "keyframes", "reason": "z"}]}),
        run_seed=1, project_name=PROJECT_NAME)
    assert plan.shots[0].generate_first_frame is True
    assert plan.shots[1].generate_first_frame is False   # inherit 不生成首帧
    assert plan.shots[2].generate_first_frame is False
    assert plan.shots[1].source_shot_id == "sC_c01"
    assert plan.shots[2].source_shot_id == "sC_c02"
    assert all(s.generate_last_frame is True for s in plan.shots)   # 全 keyframes


def test_sanitizer_invalid_values_defaulted():
    """无效 opening_source -> independent；无效 link_mode（含 none）-> i2v（§十四/§二）。"""
    sc = _scene_A()
    ai = _ai({"overview": "", "shots": [
        {"shot_id": "sA_c01", "opening_source": "garbage", "link_mode": "none", "reason": ""},
        {"shot_id": "sA_c02", "opening_source": "inherit_previous", "link_mode": "weird", "reason": ""},
        {"shot_id": "sA_c03", "opening_source": "independent", "link_mode": "keyframes", "reason": ""}]})
    plan = sanitize_plan(sc, ai, run_seed=1, project_name=PROJECT_NAME)
    # c01 第一镜强制 independent + 无效 link -> i2v
    assert plan.shots[0].opening_source == "independent"
    assert plan.shots[0].link_mode == "i2v"
    # c02 无效 link -> i2v；opening inherit 合法（i=1 非 first）-> 保留
    assert plan.shots[1].link_mode == "i2v"
    assert plan.shots[1].opening_source == "inherit_previous"
    assert plan.shots[1].generate_last_frame is False   # i2v
    # c03 keyframes 保留
    assert plan.shots[2].link_mode == "keyframes"
    assert plan.shots[2].generate_last_frame is True


def test_sanitizer_missing_and_unknown_shots():
    """AI 漏一个 Shot -> 安全默认 independent+i2v；AI 返回不存在的 shot_id -> 忽略（§十四）。"""
    sc = _scene_A()
    # 漏 sA_c02；多返回一个不存在的 sA_c99
    ai = _ai({"overview": "", "shots": [
        {"shot_id": "sA_c01", "opening_source": "independent", "link_mode": "i2v", "reason": "a"},
        {"shot_id": "sA_c99", "opening_source": "inherit_previous", "link_mode": "keyframes", "reason": "ghost"},
        {"shot_id": "sA_c03", "opening_source": "reference_previous", "link_mode": "i2v", "reason": "c"}]})
    plan = sanitize_plan(sc, ai, run_seed=1, project_name=PROJECT_NAME)
    assert len(plan.shots) == 3   # 按 scene.shots，不是 AI 返回数
    # c02 漏返回 -> 默认（第一镜逻辑不适用，i=1）independent + i2v
    assert plan.shots[1].shot_id == "sA_c02"
    assert plan.shots[1].opening_source == "independent"
    assert plan.shots[1].link_mode == "i2v"
    assert "系统默认" in plan.shots[1].reason
    # 不存在的 sA_c99 被忽略；c03 仍按 AI（reference -> source=c02）
    assert plan.shots[2].opening_source == "reference_previous"
    assert plan.shots[2].source_shot_id == "sA_c02"


# ============ 2. run_seed + choose_candidate（§十一）============
def test_new_run_seed_range():
    s = new_run_seed()
    assert isinstance(s, int)
    assert 0 <= s < 2 ** 31


def test_choose_candidate_deterministic():
    """同一 run_seed+shot_id+batch_id -> 同一选择；不同 seed 可能不同。"""
    cands = [{"status": "done", "id": i} for i in range(5)]
    a = choose_candidate(111, "sB_c02", "ff_1", cands)
    b = choose_candidate(111, "sB_c02", "ff_1", cands)
    assert a is b   # 确定性：同输入同输出（同一对象）
    # 单候选直接返回
    one = [{"status": "done", "id": 9}]
    assert choose_candidate(222, "x", "y", one)["id"] == 9
    # 0 成功候选 -> raise
    with pytest.raises(ValueError):
        choose_candidate(1, "x", "y", [{"status": "failed"}])


def test_choose_candidate_reproducible_across_processes():
    """sha256 推导跨进程稳定（不依赖 Python hash）。"""
    import hashlib
    cands = [{"status": "done", "id": i} for i in range(7)]
    raw = "555:sB_c03:lf_1".encode()
    expected = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") % 7
    got = choose_candidate(555, "sB_c03", "lf_1", cands)
    assert got["id"] == expected


# ============ 3. stale（§八/§二十二）============
def _seed_plan(pdir, scene_id="sB"):
    """直接构造一个 approved 计划写入磁盘（用于 stale 测试）。"""
    proj = ProjectStore(pdir).load()
    sc = next(s for s in proj.scenes if s.scene_id == scene_id)
    plan = sanitize_plan(sc, _ai({"overview": "x", "shots": [
        {"shot_id": f"{scene_id}_c01", "opening_source": "independent", "link_mode": "keyframes", "reason": "a"},
        {"shot_id": f"{scene_id}_c02", "opening_source": "reference_previous", "link_mode": "i2v", "reason": "b"},
        {"shot_id": f"{scene_id}_c03", "opening_source": "reference_previous", "link_mode": "keyframes", "reason": "c"}]}),
        run_seed=42, project_name=PROJECT_NAME)
    plan.status = "approved"
    plan.created_at = 1.0
    plan.approved_at = 2.0
    plans = _read_plans(pdir)
    plans[scene_id] = plan
    _write_plans(pdir, plans)
    return plan


def test_stale_on_summary_change(planner_project):
    pdir = planner_project
    plan = _seed_plan(pdir, "sB")
    p = ProjectStore(pdir).load()
    p.scenes[1].summary = "改过的故事段"
    ProjectStore(pdir).save(p)
    assert is_plan_stale(plan, p.scenes[1]) is not None


def test_stale_on_shot_description_change(planner_project):
    pdir = planner_project
    plan = _seed_plan(pdir, "sB")
    p = ProjectStore(pdir).load()
    sc = next(s for s in p.scenes if s.scene_id == "sB")
    sc.shots[1].description = "完全不同的镜头"
    ProjectStore(pdir).save(p)
    assert "描述" in is_plan_stale(plan, sc)


def test_stale_on_duration_change(planner_project):
    pdir = planner_project
    plan = _seed_plan(pdir, "sB")
    p = ProjectStore(pdir).load()
    sc = next(s for s in p.scenes if s.scene_id == "sB")
    sc.shots[0].target_duration_s = 99   # 明显变化（>0.5s）
    ProjectStore(pdir).save(p)
    assert "时长" in is_plan_stale(plan, sc)


def test_stale_on_shot_count_change(planner_project):
    pdir = planner_project
    plan = _seed_plan(pdir, "sB")
    p = ProjectStore(pdir).load()
    sc = next(s for s in p.scenes if s.scene_id == "sB")
    sc.shots.append(_shot("sB", 4, "新镜头", ["赤焰守卫"]))
    ProjectStore(pdir).save(p)
    assert "数量" in is_plan_stale(plan, sc)


def test_not_stale_on_asset_or_views(planner_project):
    """§八：生成资产 / video_end_frame / Character.views / 候选变化不触发 stale。"""
    pdir = planner_project
    plan = _seed_plan(pdir, "sB")
    p = ProjectStore(pdir).load()
    sc = next(s for s in p.scenes if s.scene_id == "sB")
    # 生成资产（first_frame / video / video_end_frame）
    sc.shots[0].first_frame.mark_done("ff.png", "https://x/ff.png")
    sc.shots[0].video.mark_done("v.mp4", "https://x/v.mp4")
    sc.shots[0].video_end_frame.mark_done("end.png", "https://x/end.png")
    sc.shots[0].status = "done"
    # 角色 views
    p.get_character("赤焰守卫").views["front"] = Asset(local="c.png", status="done")
    ProjectStore(pdir).save(p)
    assert is_plan_stale(plan, sc) is None


def test_not_stale_on_small_duration_drift(planner_project):
    """target_duration_s 微小变化（<=0.5s）不算 stale（浮点噪音容忍）。"""
    pdir = planner_project
    plan = _seed_plan(pdir, "sB")
    p = ProjectStore(pdir).load()
    sc = next(s for s in p.scenes if s.scene_id == "sB")
    sc.shots[0].target_duration_s = 5.2   # +0.2 <= 0.5
    ProjectStore(pdir).save(p)
    assert is_plan_stale(plan, sc) is None


# ============ 4. plan 端点 ============
def test_plan_endpoint_writes_plan(planner_project, fake_agnes_swap, client):
    fake_agnes_swap._responses = [PLAN_B]
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/sB/production-plan/plan")
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["shots"]) == 3
    assert d["scene_summary_snapshot"] == "机甲在机库确认信号"
    assert d["run_seed"] >= 0
    assert d["status"] == "draft"
    assert d["is_stale"] is False
    # 第一镜 independent；后两镜 reference_previous，source=前一镜
    assert d["shots"][0]["opening_source"] == "independent"
    assert d["shots"][1]["opening_source"] == "reference_previous"
    assert d["shots"][1]["source_shot_id"] == "sB_c01"
    assert d["shots"][2]["source_shot_id"] == "sB_c02"
    assert _plans_path(planner_project).exists()


def test_plan_404_unknown_scene(planner_project, fake_agnes_swap, client):
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/sXX/production-plan/plan")
    assert r.status_code == 404


def test_plan_400_when_no_shots(planner_project, fake_agnes_swap, client):
    """§十六：无 Shot 时后端兜底拦截。"""
    pdir = planner_project
    p = ProjectStore(pdir).load()
    p.scenes[0].shots = []   # sA 清空 shots
    ProjectStore(pdir).save(p)
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/sA/production-plan/plan")
    assert r.status_code == 400
    assert "请先创建镜头" in r.json()["detail"]


# ============ 5. GET + stale 派生 ============
def test_get_plan_returns_stale_flag(planner_project, client):
    pdir = planner_project
    _seed_plan(pdir, "sB")
    # 改 scene summary -> stale
    p = ProjectStore(pdir).load()
    p.scenes[1].summary = "改过"
    ProjectStore(pdir).save(p)
    r = client.get(f"/api/project/{PROJECT_NAME}/scene/sB/production-plan")
    assert r.status_code == 200
    assert r.json()["is_stale"] is True
    assert r.json()["stale_reason"] is not None


def test_get_plan_404_when_none(planner_project, client):
    r = client.get(f"/api/project/{PROJECT_NAME}/scene/sB/production-plan")
    assert r.status_code == 404


# ============ 6. PUT 保存编辑 + 重算 ============
def test_save_rederives_on_edit(planner_project, client):
    pdir = planner_project
    _seed_plan(pdir, "sB")
    # 用户把 c02 从 reference_previous+i2v 改成 inherit_previous+keyframes
    r = client.put(f"/api/project/{PROJECT_NAME}/scene/sB/production-plan", json={
        "overview": "x",
        "shots": [
            {"shot_id": "sB_c01", "opening_source": "independent", "link_mode": "keyframes", "reason": "a"},
            {"shot_id": "sB_c02", "opening_source": "inherit_previous", "link_mode": "keyframes", "reason": "改过"},
            {"shot_id": "sB_c03", "opening_source": "reference_previous", "link_mode": "i2v", "reason": "c"}]})
    assert r.status_code == 200, r.text
    d = r.json()
    # c02 inherit -> 不生成首帧；source=c01；keyframes -> 生成尾帧
    c02 = d["shots"][1]
    assert c02["opening_source"] == "inherit_previous"
    assert c02["generate_first_frame"] is False
    assert c02["generate_last_frame"] is True
    assert c02["source_shot_id"] == "sB_c01"
    assert c02["requires_previous_video_end"] is True
    # 编辑后 approved 降级 draft（§十八：编辑需重新确认）
    assert d["status"] == "draft"


def test_save_blocked_when_stale(planner_project, client):
    pdir = planner_project
    _seed_plan(pdir, "sB")
    p = ProjectStore(pdir).load()
    p.scenes[1].summary = "改过"
    ProjectStore(pdir).save(p)
    r = client.put(f"/api/project/{PROJECT_NAME}/scene/sB/production-plan",
                   json={"overview": "", "shots": []})
    assert r.status_code == 409


# ============ 7. approve 不写正式 Shot（§六/§二十 关键不变量）============
def test_approve_does_not_write_shot_opening_source(planner_project, client):
    pdir = planner_project
    _seed_plan(pdir, "sB")   # plan 含 reference_previous
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/sB/production-plan/approve")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    # 🔴 校验：正式 Shot.opening_source 仍是默认 independent，source_shot_id 仍 None
    proj = ProjectStore(pdir).load()
    sc = next(s for s in proj.scenes if s.scene_id == "sB")
    for sh in sc.shots:
        assert sh.opening_source == "independent"
        assert sh.source_shot_id is None
        assert sh.source_version is None
    # 计划文件里仍是 approved + 含 reference_previous（plan 是意图，未生效到 Shot）
    plan = _read_plans(pdir)["sB"]
    assert plan.status == "approved"
    assert plan.shots[1].opening_source == "reference_previous"


def test_approve_blocked_when_stale(planner_project, client):
    pdir = planner_project
    _seed_plan(pdir, "sB")
    p = ProjectStore(pdir).load()
    p.scenes[1].shots[0].description = "改过"
    ProjectStore(pdir).save(p)
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/sB/production-plan/approve")
    assert r.status_code == 409


def test_approve_404_when_no_plan(planner_project, client):
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/sB/production-plan/approve")
    assert r.status_code == 404


# ============ 8. delete ============
def test_delete_plan(planner_project, fake_agnes_swap, client):
    fake_agnes_swap._responses = [PLAN_B]
    client.post(f"/api/project/{PROJECT_NAME}/scene/sB/production-plan/plan")
    r = client.delete(f"/api/project/{PROJECT_NAME}/scene/sB/production-plan")
    assert r.status_code == 200
    assert "sB" not in _read_plans(planner_project)


# ============ 9. workflow plan_scene（FakeAgnes）=============
def test_plan_scene_parses_json():
    wf = ProductionPlannerWorkflow(FakeAgnes([PLAN_C]))
    proj = Project(name="x", story_text="故事",
                   characters=[Character(name="林夏", description_en="少女")],
                   scenes=[Scene(scene_id="sC", summary="抬起扫描装置", characters=["林夏"],
                                 shots=[_shot("sC", 1, "林夏开始抬起扫描装置", ["林夏"], 4),
                                        _shot("sC", 2, "扫描装置继续展开", ["林夏"], 4),
                                        _shot("sC", 3, "扫描装置完成启动", ["林夏"], 4)])])
    obj = asyncio.run(wf.plan_scene(proj, proj.scenes[0]))
    assert len(obj["shots"]) == 3
    assert obj["shots"][0]["opening_source"] == "independent"
    assert obj["shots"][1]["opening_source"] == "inherit_previous"
