"""Phase AI-3：AI 导演测试。

重点：plan JSON 解析、apply 创建 Shot[]（description=intent/characters/target_duration_s/shot_id 自增）、
stale（scene summary/characters 变触发；其他 scene/角色 views 变不触发）、已有 Shot 禁止 apply、
角色覆盖拦截、characters 约束在 scene 内。AI 调用用 FakeAgnes。
"""
import asyncio
import shutil
import pytest

import deps
from deps import get_project_manager
from src.core.store import ProjectStore
from src.core.models import Project, Scene, Character, StyleAnchor, Asset
from src.ai.director import DirectorWorkflow, _build_draft, coverage_missing
from routers.director import _drafts_path, _read_drafts

PROJECT_NAME = "_test_director"


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


def _proj(pname):
    s01 = Scene(scene_id="s01", summary="机甲重启并确认信号", characters=["赤焰守卫", "林夏"], shots=[])
    s02 = Scene(scene_id="s02", summary="进入城市", characters=["赤焰守卫"], shots=[])
    return _make_project(pname, chars=[("赤焰守卫", "机甲"), ("林夏", "少女")], scenes=[s01, s02])


def _seed_draft(pdir, *, scene_id="s01", scene_summary="机甲重启并确认信号",
                scene_chars=None, shots=None):
    from src.ai.director import DirectorDraft, ShotPlanItem
    if scene_chars is None:
        scene_chars = ["赤焰守卫", "林夏"]
    if shots is None:
        shots = [
            ShotPlanItem(draft_id="draft_1", intent="机甲灯光逐级启动", characters=["赤焰守卫"], target_duration_s=5),
            ShotPlanItem(draft_id="draft_2", intent="林夏抬头观察", characters=["林夏"], target_duration_s=3),
            ShotPlanItem(draft_id="draft_3", intent="两者对峙", characters=["赤焰守卫", "林夏"], target_duration_s=8),
        ]
    draft = DirectorDraft(
        draft_id=f"director_{scene_id}", scene_id=scene_id, overview="3 镜头",
        shots=shots,
        scene_summary_snapshot=scene_summary,
        scene_characters_snapshot=scene_chars,
        director_note="", created_at=0.0,
    )
    drafts = _read_drafts(pdir)
    drafts[scene_id] = draft
    from routers.director import _write_drafts
    _write_drafts(pdir, drafts)
    return draft


PLAN_JSON = ('{"overview":"3 镜头逐步表现","shots":['
             '{"draft_id":"draft_1","intent":"机甲灯光逐级启动","characters":["赤焰守卫"],"target_duration_s":5,"purpose":"建立苏醒"},'
             '{"draft_id":"draft_2","intent":"林夏抬头观察机甲","characters":["林夏"],"target_duration_s":3,"purpose":"反应"},'
             '{"draft_id":"draft_3","intent":"两者对峙","characters":["赤焰守卫","林夏"],"target_duration_s":8,"purpose":"第一次接触"}]}')


@pytest.fixture
def director_project(client):
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


# ============ 1. plan 单元 ============
def test_build_draft_filters_external_chars():
    """AI 返回了 Scene 外的角色 -> 过滤掉（§十二）。"""
    obj = {"overview": "", "shots": [
        {"draft_id": "draft_1", "intent": "机甲启动", "characters": ["赤焰守卫", "机械犬"], "target_duration_s": 5}]}
    d = _build_draft(obj, "s01", "summary", ["赤焰守卫", "林夏"], "")
    assert d.shots[0].characters == ["赤焰守卫"]   # 机械犬被过滤
    assert d.scene_characters_snapshot == ["赤焰守卫", "林夏"]


def test_coverage_missing():
    from src.ai.director import DirectorDraft, ShotPlanItem
    d = DirectorDraft(draft_id="d", scene_id="s01", scene_summary_snapshot="",
                      scene_characters_snapshot=["赤焰守卫", "林夏"], shots=[
                          ShotPlanItem(draft_id="d1", intent="x", characters=["赤焰守卫"], target_duration_s=5)])
    assert coverage_missing(d) == ["林夏"]
    d.shots[0].characters.append("林夏")
    assert coverage_missing(d) == []


def test_plan_scene_parses_json():
    wf = DirectorWorkflow(FakeAgnes([PLAN_JSON]))
    proj = Project(name="x", story_text="故事", characters=[Character(name="赤焰守卫"), Character(name="林夏")],
                   scenes=[Scene(scene_id="s01", summary="机甲重启", characters=["赤焰守卫", "林夏"])])
    d = asyncio.run(wf.plan_scene(proj, proj.scenes[0], ""))
    assert len(d.shots) == 3
    assert d.shots[0].intent == "机甲灯光逐级启动"
    assert d.shots[2].characters == ["赤焰守卫", "林夏"]
    assert d.scene_summary_snapshot == "机甲重启"


# ============ 2. plan 端点 ============
def test_plan_endpoint_writes_draft(director_project, fake_agnes_swap, client):
    fake_agnes_swap._responses = [PLAN_JSON]
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/s01/director/plan", json={"director_note": "节奏慢一点"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["shots"]) == 3
    assert d["scene_summary_snapshot"] == "机甲重启并确认信号"
    assert d["scene_characters_snapshot"] == ["赤焰守卫", "林夏"]
    assert d["director_note"] == "节奏慢一点"
    assert _drafts_path(director_project).exists()


def test_plan_404_unknown_scene(director_project, fake_agnes_swap, client):
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/sXX/director/plan", json={})
    assert r.status_code == 404


# ============ 3. apply 创建 Shot[] ============
def test_apply_creates_shots(director_project, client):
    pdir = director_project
    _seed_draft(pdir)
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/s01/director/apply")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["shots"]) == 3
    # shot_id 自增（s01_c01/c02/c03）
    assert [s["shot_id"] for s in body["shots"]] == ["s01_c01", "s01_c02", "s01_c03"]
    assert [s["index"] for s in body["shots"]] == [1, 2, 3]
    # description = intent
    assert body["shots"][0]["description"] == "机甲灯光逐级启动"
    assert body["shots"][2]["characters"] == ["赤焰守卫", "林夏"]
    assert body["shots"][0]["target_duration_s"] == 5
    # 写回 project.json
    proj = ProjectStore(pdir).load()
    sc = next(s for s in proj.scenes if s.scene_id == "s01")
    assert len(sc.shots) == 3
    assert sc.shots[0].shot_id == "s01_c01"
    assert sc.shots[0].description == "机甲灯光逐级启动"
    # 三个提示词保持空（§二十）
    assert sc.shots[0].prompt_en == ""
    assert sc.shots[0].last_frame_prompt_en == ""
    assert sc.shots[0].video_prompt_en == ""
    # opening_source / link_mode 用默认（§二十一/二十二）
    assert sc.shots[0].opening_source == "independent"
    # 草稿删除
    assert "s01" not in _read_drafts(pdir)


def test_apply_no_draft_404(director_project, client):
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/s01/director/apply")
    assert r.status_code == 404


# ============ 4. stale ============
def test_apply_stale_on_summary_change(director_project, client):
    pdir = director_project
    _seed_draft(pdir)
    p = ProjectStore(pdir).load()
    p.scenes[0].summary = "改过的故事段"
    ProjectStore(pdir).save(p)
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/s01/director/apply")
    assert r.status_code == 409
    assert "故事文本" in r.json()["detail"]


def test_apply_stale_on_characters_change(director_project, client):
    pdir = director_project
    _seed_draft(pdir)
    p = ProjectStore(pdir).load()
    p.scenes[0].characters = ["赤焰守卫"]   # 林夏被移除
    ProjectStore(pdir).save(p)
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/s01/director/apply")
    assert r.status_code == 409
    assert "角色" in r.json()["detail"]


def test_apply_not_stale_on_other_scene_or_views(director_project, client):
    """§二十四/二十五：其他 Scene 修改 / 角色 views 更新不触发 stale。"""
    pdir = director_project
    _seed_draft(pdir)
    p = ProjectStore(pdir).load()
    p.scenes[1].summary = "改过的另一段"   # 其他 scene
    p.get_character("赤焰守卫").views["front"] = Asset(local="x.png", status="done")  # 角色 views
    ProjectStore(pdir).save(p)
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/s01/director/apply")
    assert r.status_code == 200, r.text


# ============ 5. 已有 Shot 禁止 apply（§十六）============
def test_apply_blocked_when_scene_has_shots(director_project, client):
    pdir = director_project
    _seed_draft(pdir)
    p = ProjectStore(pdir).load()
    from src.core.models import Shot
    p.scenes[0].shots.append(Shot(shot_id="s01_c01", scene_id="s01", index=1, description="已有"))
    ProjectStore(pdir).save(p)
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/s01/director/apply")
    assert r.status_code == 409
    assert "已有镜头" in r.json()["detail"]


# ============ 6. 角色覆盖拦截（§十三）============
def test_apply_blocked_on_uncovered_char(director_project, client):
    pdir = director_project
    from src.ai.director import DirectorDraft, ShotPlanItem
    from routers.director import _write_drafts
    draft = DirectorDraft(draft_id="d", scene_id="s01", overview="", shots=[
        ShotPlanItem(draft_id="d1", intent="机甲启动", characters=["赤焰守卫"], target_duration_s=5)],
        scene_summary_snapshot="机甲重启并确认信号", scene_characters_snapshot=["赤焰守卫", "林夏"],
        director_note="", created_at=0.0)
    _write_drafts(pdir, {"s01": draft})
    r = client.post(f"/api/project/{PROJECT_NAME}/scene/s01/director/apply")
    assert r.status_code == 400
    assert "林夏" in r.json()["detail"]


# ============ 7. 保存草稿 + GET ============
def test_save_and_get_draft(director_project, fake_agnes_swap, client):
    fake_agnes_swap._responses = [PLAN_JSON]
    client.post(f"/api/project/{PROJECT_NAME}/scene/s01/director/plan", json={})
    # 编辑：把第 1 镜时长改成 10
    d = _read_drafts(director_project)["s01"]
    d.shots[0].target_duration_s = 10
    r = client.put(f"/api/project/{PROJECT_NAME}/scene/s01/director/draft",
                   json={"overview": d.overview, "shots": [s.model_dump() for s in d.shots], "director_note": ""})
    assert r.status_code == 200, r.text
    # GET 读回
    r = client.get(f"/api/project/{PROJECT_NAME}/scene/s01/director/draft")
    assert r.status_code == 200
    assert r.json()["shots"][0]["target_duration_s"] == 10


def test_delete_draft(director_project, fake_agnes_swap, client):
    fake_agnes_swap._responses = [PLAN_JSON]
    client.post(f"/api/project/{PROJECT_NAME}/scene/s01/director/plan", json={})
    r = client.delete(f"/api/project/{PROJECT_NAME}/scene/s01/director/draft")
    assert r.status_code == 200
    assert "s01" not in _read_drafts(director_project)
