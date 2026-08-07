"""Phase AI-2：AI 角色设计测试。

重点：design JSON 解析、apply 写 generation_prompt->description_en、stale（desc/相关 scene/风格 变触发；
首帧/视频/其他角色不触发）、批量部分失败、改名迁移、删除清理。AI 调用用 FakeAgnes。
"""
import asyncio
import shutil
import pytest

import deps
from deps import get_project_manager
from src.core.store import ProjectStore
from src.core.models import Project, Scene, Character, StyleAnchor, Asset
from src.ai.character_designer import CharacterDesigner, _build_draft
from routers.character_design import _designs_path, _read_designs

PROJECT_NAME = "_test_design"


class FakeAgnes:
    def __init__(self, responses=None, fail_on=None):
        self._responses = list(responses or [])
        self.fail_on = fail_on   # 第几次 chat 抛异常
        self.calls = 0

    async def chat(self, messages, *, temperature=0.7, max_tokens=2048,
                   thinking=False, json_mode=False):
        i = self.calls
        self.calls += 1
        if self.fail_on is not None and i == self.fail_on:
            raise RuntimeError("simulated fail")
        if not self._responses:
            return ""
        return self._responses.pop(0)


def _make_project(pname, *, chars=None, scenes=None, style=None):
    pm = get_project_manager()
    shutil.rmtree(pm.base_dir / pname, ignore_errors=True)
    pdir = pm.create_project(pname)
    proj = Project(name=pname,
                   style=style or StyleAnchor(art_style="anime", palette="", aspect_ratio="16:9"),
                   story_text="赤焰守卫与林夏在废墟城市相遇。",
                   characters=[Character(name=n, description_en=d) for n, d in (chars or [])],
                   scenes=scenes or [])
    ProjectStore(pdir).save(proj)
    return pdir


def _proj_with_chars(pname):
    """林夏 出现在 s01/s02；老工程师 不出现。"""
    s01 = Scene(scene_id="s01", summary="林夏在通信塔下", characters=["林夏"], shots=[])
    s02 = Scene(scene_id="s02", summary="林夏与机甲对峙", characters=["林夏"], shots=[])
    return _make_project(pname, chars=[("林夏", "少女"), ("老工程师", "老人")], scenes=[s01, s02])


DRAFT_JSON = ('{"character_name":"林夏","role_summary":"受伤少女","visual_concept":"荒原少女",'
              '"appearance":"约18岁浅色短发","clothing_or_body":"磨损旅行服",'
              '"distinctive_features":"左腕旧通信器","color_palette":"米白、暗棕",'
              '"consistency_rules":["左腕通信器必须存在","浅色短发"],"generation_prompt":'
              '"a young girl around 18, short pale hair, worn beige travel outfit, '
              'old communicator on left wrist, anime character sheet, full body, plain background"}')


@pytest.fixture
def design_project(client):
    pdir = _proj_with_chars(PROJECT_NAME)
    yield pdir
    shutil.rmtree(get_project_manager().get_project_dir(PROJECT_NAME), ignore_errors=True)


@pytest.fixture
def fake_agnes_swap(client):
    fake = FakeAgnes()
    real = deps.agnes_client
    deps.agnes_client = fake
    yield fake
    deps.agnes_client = real


# ============ 1. design_character 单元 ============
def test_build_draft_parses_json():
    import json as _json
    obj = _json.loads(DRAFT_JSON)
    d = _build_draft(obj, "fallback")
    assert d.character_name == "林夏"
    assert d.generation_prompt.startswith("a young girl")
    assert d.consistency_rules == ["左腕通信器必须存在", "浅色短发"]
    assert d.color_palette == "米白、暗棕"


def test_design_character_returns_draft():
    wf = CharacterDesigner(FakeAgnes([DRAFT_JSON]))
    d = asyncio.run(wf.design_character(
        "林夏", "少女", "故事。", [("s01", "林夏在通信塔下")], [("赤焰守卫", "机甲")], "anime"))
    assert d.character_name == "林夏"
    assert d.generation_prompt  # 非空
    assert "荒原少女" in d.visual_concept


# ============ 2. design 端点 ============
def test_design_endpoint_writes_draft(design_project, fake_agnes_swap, client):
    fake_agnes_swap._responses = [DRAFT_JSON]
    r = client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design")
    assert r.status_code == 200, r.text
    state = r.json()
    assert state["draft"]["generation_prompt"].startswith("a young girl")
    assert state["base_character_description"] == "少女"
    assert len(state["base_related_scene_signature"]) == 2   # s01, s02
    assert _designs_path(design_project).exists()


def test_design_404_unknown_char(design_project, fake_agnes_swap, client):
    r = client.post(f"/api/project/{PROJECT_NAME}/character/不存在/design")
    assert r.status_code == 404


# ============ 3. apply ============
def test_apply_writes_generation_prompt_to_description(design_project, fake_agnes_swap, client):
    fake_agnes_swap._responses = [DRAFT_JSON]
    client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design")
    r = client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design/apply")
    assert r.status_code == 200, r.text
    assert r.json()["description_en"].startswith("a young girl")
    # 写回 project.json
    proj = ProjectStore(design_project).load()
    c = proj.get_character("林夏")
    assert c.description_en.startswith("a young girl")
    # draft 标记 applied
    state = _read_designs(design_project)["林夏"]
    assert state.status == "applied"
    assert state.base_character_description.startswith("a young girl")   # base 更新为已应用


def test_apply_no_draft_404(design_project, client):
    r = client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design/apply")
    assert r.status_code == 404


# ============ 4. stale ============
def test_apply_stale_on_description_change(design_project, fake_agnes_swap, client):
    fake_agnes_swap._responses = [DRAFT_JSON]
    client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design")
    # 用户改了角色描述
    p = ProjectStore(design_project).load()
    p.get_character("林夏").description_en = "改过的描述"
    ProjectStore(design_project).save(p)
    r = client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design/apply")
    assert r.status_code == 409
    assert "文字描述" in r.json()["detail"]


def test_apply_stale_on_related_scene_change(design_project, fake_agnes_swap, client):
    fake_agnes_swap._responses = [DRAFT_JSON]
    client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design")
    # 改林夏出现的场景摘要
    p = ProjectStore(design_project).load()
    p.scenes[0].summary = "改过的场景"
    ProjectStore(design_project).save(p)
    r = client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design/apply")
    assert r.status_code == 409
    assert "故事段" in r.json()["detail"]


def test_apply_stale_on_style_change(design_project, fake_agnes_swap, client):
    fake_agnes_swap._responses = [DRAFT_JSON]
    client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design")
    p = ProjectStore(design_project).load()
    p.style.art_style = "realistic cinematic"
    ProjectStore(design_project).save(p)
    r = client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design/apply")
    assert r.status_code == 409
    assert "风格" in r.json()["detail"]


def test_apply_not_stale_on_views_change(design_project, fake_agnes_swap, client):
    """§21：三视图/首帧/视频变化不触发 stale。"""
    fake_agnes_swap._responses = [DRAFT_JSON]
    client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design")
    # 给林夏加 views（模拟三视图生成）
    p = ProjectStore(design_project).load()
    p.get_character("林夏").views["front"] = Asset(local="output/characters/林夏_front.png", status="done")
    ProjectStore(design_project).save(p)
    r = client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design/apply")
    assert r.status_code == 200, r.text   # 不 stale


def test_apply_not_stale_on_other_char_views(design_project, fake_agnes_swap, client):
    """其他角色三视图变化不触发本角色 stale。"""
    fake_agnes_swap._responses = [DRAFT_JSON]
    client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design")
    p = ProjectStore(design_project).load()
    p.get_character("老工程师").views["front"] = Asset(local="x.png", status="done")
    ProjectStore(design_project).save(p)
    r = client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design/apply")
    assert r.status_code == 200, r.text


# ============ 5. 批量（部分失败不整批失败）============
def test_design_batch_partial_failure(design_project, fake_agnes_swap, client):
    # 3 个角色：林夏(ok) 老工程师(fail) + 加第3个
    p = ProjectStore(design_project).load()
    p.characters.append(Character(name="机械商人", description_en="商人"))
    ProjectStore(design_project).save(p)
    # 第 2 次 chat 抛异常（老工程师失败）
    fake_agnes_swap.fail_on = 1
    fake_agnes_swap._responses = [DRAFT_JSON, DRAFT_JSON.replace("林夏", "机械商人")]
    r = client.post(f"/api/project/{PROJECT_NAME}/character/design-batch", json={})
    assert r.status_code == 200, r.text
    results = {x["name"]: x for x in r.json()["results"]}
    assert results["林夏"]["ok"] is True
    assert results["老工程师"]["ok"] is False
    assert results["机械商人"]["ok"] is True
    # 成功的落草稿
    designs = _read_designs(design_project)
    assert "林夏" in designs and "机械商人" in designs
    assert "老工程师" not in designs


def test_design_batch_only_no_draft(design_project, fake_agnes_swap, client):
    """批量只处理无草稿角色（已有草稿的跳过）。"""
    # 先给林夏设计
    fake_agnes_swap._responses = [DRAFT_JSON]
    client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design")
    fake_agnes_swap.calls = 0
    # 批量：林夏已有草稿，应只设计老工程师
    fake_agnes_swap._responses = [DRAFT_JSON.replace("林夏", "老工程师")]
    r = client.post(f"/api/project/{PROJECT_NAME}/character/design-batch", json={})
    assert r.status_code == 200
    names = [x["name"] for x in r.json()["results"]]
    assert "老工程师" in names
    assert "林夏" not in names   # 已有草稿，跳过


# ============ 6. 改名迁移 ============
def test_rename_migrates_draft(design_project, fake_agnes_swap, client):
    fake_agnes_swap._responses = [DRAFT_JSON]
    client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design")
    assert "林夏" in _read_designs(design_project)
    # 改名 林夏 -> 林夏儿
    r = client.put(f"/api/project/{PROJECT_NAME}/character/林夏",
                   json={"new_name": "林夏儿"})
    assert r.status_code == 200, r.text
    designs = _read_designs(design_project)
    assert "林夏" not in designs
    assert "林夏儿" in designs
    assert designs["林夏儿"].draft.character_name == "林夏儿"


# ============ 7. 删除清理 ============
def test_delete_clears_draft(design_project, fake_agnes_swap, client):
    fake_agnes_swap._responses = [DRAFT_JSON]
    client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design")
    assert "林夏" in _read_designs(design_project)
    r = client.delete(f"/api/project/{PROJECT_NAME}/character/林夏")
    assert r.status_code == 200, r.text
    assert "林夏" not in _read_designs(design_project)


# ============ 8. 保存草稿编辑 + GET designs ============
def test_save_and_get_designs(design_project, fake_agnes_swap, client):
    fake_agnes_swap._responses = [DRAFT_JSON]
    client.post(f"/api/project/{PROJECT_NAME}/character/林夏/design")
    # 编辑 generation_prompt
    state = _read_designs(design_project)["林夏"]
    state.draft.generation_prompt = "edited prompt"
    r = client.put(f"/api/project/{PROJECT_NAME}/character/林夏/design",
                   json={"draft": state.draft.model_dump()})
    assert r.status_code == 200, r.text
    # GET 读回
    r = client.get(f"/api/project/{PROJECT_NAME}/character/designs")
    assert r.status_code == 200
    designs = r.json()["designs"]
    assert designs["林夏"]["draft"]["generation_prompt"] == "edited prompt"
