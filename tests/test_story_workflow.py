"""Phase AI-1：AI 整理故事工作流测试。

三层：
  1. 纯函数：normalize_name / match_characters（不依赖 TestClient / Agnes）
  2. StoryWorkflow 单元：organize_story JSON 解析（注入 FakeAgnes，用 asyncio.run 驱动，不发 HTTP）
  3. 接口：organize（swap deps.agnes_client）/ apply 闸门 / resolve-character 去重 / apply 建 Scene

apply / resolve 不调 Agnes，直接预置草稿文件测试，稳定不 flake。
"""
import asyncio
import json
import shutil
import pytest

import deps
from deps import get_project_manager
from src.core.store import ProjectStore, _atomic_write_json
from src.core.models import Project, Scene, Character, StyleAnchor
from src.ai.story_workflow import (
    StoryWorkflow, StoryOrganizationDraft, DraftScene, CharacterResolutionItem,
    normalize_name,
)
from routers.story import _draft_path, _write_draft

PROJECT_NAME = "_test_story"


# ============ Fake Agnes（只实现 chat，按顺序返回预设响应）============
class FakeAgnes:
    def __init__(self, chat_responses=None):
        self._responses = list(chat_responses or [])
        self.calls = []

    async def chat(self, messages, *, temperature=0.7, max_tokens=2048,
                   thinking=False, json_mode=False):
        self.calls.append({"messages": messages, "json_mode": json_mode})
        if not self._responses:
            return ""
        return self._responses.pop(0)


# ============ 项目构造 helper ============
def _make_project(pname: str, *, story_text: str = "", chars=None, scenes=None):
    """建临时 v2 项目，返回 project_dir。"""
    pm = get_project_manager()
    shutil.rmtree(pm.base_dir / pname, ignore_errors=True)
    pdir = pm.create_project(pname)
    proj = Project(name=pname, style=StyleAnchor(aspect_ratio="16:9", size_tier="1K"),
                   story_text=story_text,
                   characters=[Character(name=n, description_en=d) for n, d in (chars or [])],
                   scenes=scenes or [])
    ProjectStore(pdir).save(proj)
    return pdir


def _seed_draft(pdir, *, story_snapshot, scenes, characters, project_updated_at=0.0):
    """直接落一份草稿文件（绕过 organize，用于测 apply / resolve）。"""
    draft = StoryOrganizationDraft(
        draft_id="draft_test",
        project_name=PROJECT_NAME,
        project_updated_at=project_updated_at,
        story_text_snapshot=story_snapshot,
        overview="",
        scenes=scenes,
        characters=characters,
    )
    _write_draft(pdir, draft)
    return draft


# ============ fixtures ============
@pytest.fixture
def story_project(client):
    """临时项目，返回 project_dir。teardown 删目录。"""
    pdir = _make_project(PROJECT_NAME, story_text="赤焰守卫在废弃机库中重新启动。神秘女孩出现在废墟。")
    yield pdir
    shutil.rmtree(get_project_manager().get_project_dir(PROJECT_NAME), ignore_errors=True)


@pytest.fixture
def fake_agnes_swap(client):
    """替 deps.agnes_client 为 FakeAgnes（per-test），yield fake，恢复原值。"""
    fake = FakeAgnes()
    real = deps.agnes_client
    deps.agnes_client = fake
    yield fake
    deps.agnes_client = real


# ============ 1. 纯函数 ============
def test_normalize_name():
    assert normalize_name(" 赤焰守卫 ") == normalize_name("赤焰守卫")
    assert normalize_name("赤焰守卫。") == normalize_name("赤焰守卫")
    assert normalize_name("赤焰守卫，") == normalize_name("赤焰守卫")
    assert normalize_name("ABC") == normalize_name("abc")
    assert normalize_name("林夏") == normalize_name(" 林夏 ")
    assert normalize_name("") == ""
    # 不做模糊推断
    assert normalize_name("老王") != normalize_name("王师傅")


def test_match_characters():
    project_chars = ["赤焰守卫", "机械犬", "林夏"]
    mentions = ["赤焰守卫", " 神秘女孩 ", "林夏", "不存在", "赤焰守卫。"]
    m = StoryWorkflow.match_characters(mentions, project_chars)
    assert m["赤焰守卫"] == "赤焰守卫"
    assert m[" 神秘女孩 "] is None
    assert m["林夏"] == "林夏"
    assert m["不存在"] is None
    assert m["赤焰守卫。"] == "赤焰守卫"   # 标点差异仍匹配
    # 不做模糊匹配
    assert StoryWorkflow.match_characters(["老王"], ["王师傅"])["老王"] is None


# ============ 2. StoryWorkflow 单元（organize JSON 解析）============
def test_organize_parse_json_fenced():
    raw = '```json\n{"overview": "5个阶段", "scenes": [{"draft_id": "draft_1", "summary": "重启", "character_mentions": ["赤焰守卫"]}]}\n```'
    wf = StoryWorkflow(FakeAgnes([raw]))
    res = asyncio.run(wf.organize_story("故事", ["赤焰守卫"]))
    assert res.overview == "5个阶段"
    assert len(res.scenes) == 1
    assert res.scenes[0].summary == "重启"
    assert res.scenes[0].character_mentions == ["赤焰守卫"]


def test_organize_parse_json_unfenced():
    raw = '{"overview":"", "scenes": [{"draft_id":"draft_1","summary":"A","character_mentions":[]},{"draft_id":"draft_2","summary":"B","character_mentions":["神秘女孩","老工程师"]}]}'
    wf = StoryWorkflow(FakeAgnes([raw]))
    res = asyncio.run(wf.organize_story("故事", []))
    assert len(res.scenes) == 2
    assert res.scenes[1].character_mentions == ["神秘女孩", "老工程师"]


def test_organize_parse_invalid_raises():
    wf = StoryWorkflow(FakeAgnes(["这不是JSON"]))
    with pytest.raises(ValueError):
        asyncio.run(wf.organize_story("故事", []))


def test_draft_character_returns_text():
    wf = StoryWorkflow(FakeAgnes(["18岁的荒原少女，浅色短发。"]))
    desc = asyncio.run(wf.draft_character("神秘女孩", "故事", ["女孩出现"]))
    assert "荒原少女" in desc


def test_draft_characters_batch_parse():
    raw = '{"characters":[{"name":"神秘女孩","description":"少女"},{"name":"老工程师","description":"老人"}]}'
    wf = StoryWorkflow(FakeAgnes([raw]))
    out = asyncio.run(wf.draft_characters_batch(["神秘女孩", "老工程师"], "故事"))
    assert dict(out) == {"神秘女孩": "少女", "老工程师": "老人"}


# ============ 3. 接口：organize ============
def test_organize_endpoint_writes_draft(story_project, fake_agnes_swap, client):
    fake_agnes_swap._responses = [
        '{"overview":"2段","scenes":[{"draft_id":"draft_1","summary":"重启","character_mentions":["赤焰守卫"]},{"draft_id":"draft_2","summary":"女孩出现","character_mentions":["神秘女孩"]}]}'
    ]
    r = client.post(f"/api/project/{PROJECT_NAME}/story/organize")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["scenes"]) == 2
    # 项目无任何角色 -> 两个 mention 都 unresolved
    res_by = {x["mention"]: x for x in body["characters"]}
    assert res_by["赤焰守卫"]["status"] == "unresolved"
    assert res_by["神秘女孩"]["status"] == "unresolved"
    # 草稿文件已落盘
    assert _draft_path(story_project).exists()
    # snapshot 记录了 organize 时的故事
    assert body["story_text_snapshot"].startswith("赤焰守卫")


def test_organize_auto_matches_existing_char(story_project, fake_agnes_swap, client):
    # 项目已有"赤焰守卫"
    pdir = story_project
    proj = ProjectStore(pdir).load()
    proj.characters = [Character(name="赤焰守卫")]
    ProjectStore(pdir).save(proj)
    fake_agnes_swap._responses = [
        '{"overview":"","scenes":[{"draft_id":"draft_1","summary":"A","character_mentions":["赤焰守卫","神秘女孩"]}]}'
    ]
    r = client.post(f"/api/project/{PROJECT_NAME}/story/organize")
    assert r.status_code == 200, r.text
    res_by = {x["mention"]: x for x in r.json()["characters"]}
    assert res_by["赤焰守卫"]["status"] == "matched"          # 确定性自动匹配
    assert res_by["赤焰守卫"]["matched_character_name"] == "赤焰守卫"
    assert res_by["神秘女孩"]["status"] == "unresolved"


def test_organize_empty_story_400(story_project, fake_agnes_swap, client):
    pdir = story_project
    proj = ProjectStore(pdir).load()
    proj.story_text = "   "
    ProjectStore(pdir).save(proj)
    r = client.post(f"/api/project/{PROJECT_NAME}/story/organize")
    assert r.status_code == 400
    assert "先写下完整故事" in r.json()["detail"]


# ============ 3. 接口：apply 闸门 ============
def test_apply_gate_existing_scenes(story_project, client):
    pdir = story_project
    proj = ProjectStore(pdir).load()
    proj.scenes = [Scene(scene_id="s01", summary="已有", characters=[], shots=[])]
    proj.next_scene_seq = 2
    ProjectStore(pdir).save(proj)
    _seed_draft(pdir,
                story_snapshot=proj.story_text,
                scenes=[DraftScene(draft_id="draft_1", summary="A", character_mentions=[])],
                characters=[])
    r = client.post(f"/api/project/{PROJECT_NAME}/story/apply")
    assert r.status_code == 409
    assert "已有分镜内容" in r.json()["detail"]


def test_apply_gate_stale(story_project, client):
    pdir = story_project
    proj = ProjectStore(pdir).load()
    _seed_draft(pdir,
                story_snapshot="旧的完整故事",
                scenes=[DraftScene(draft_id="draft_1", summary="A", character_mentions=[])],
                characters=[])
    assert proj.story_text != "旧的完整故事"
    r = client.post(f"/api/project/{PROJECT_NAME}/story/apply")
    assert r.status_code == 409
    assert "发生变化" in r.json()["detail"]


def test_apply_gate_unresolved(story_project, client):
    pdir = story_project
    proj = ProjectStore(pdir).load()
    _seed_draft(pdir,
                story_snapshot=proj.story_text,
                scenes=[DraftScene(draft_id="draft_1", summary="A", character_mentions=["神秘女孩"])],
                characters=[CharacterResolutionItem(mention="神秘女孩", status="unresolved")])
    r = client.post(f"/api/project/{PROJECT_NAME}/story/apply")
    assert r.status_code == 400
    assert "1 个角色" in r.json()["detail"]


# ============ 3. 接口：resolve-character create 去重 ============
def test_resolve_character_create_dedup(story_project, client):
    pdir = story_project
    proj = ProjectStore(pdir).load()
    proj.characters = [Character(name="神秘女孩", description_en="旧设定")]
    ProjectStore(pdir).save(proj)
    _seed_draft(pdir,
                story_snapshot=proj.story_text,
                scenes=[DraftScene(draft_id="draft_1", summary="A", character_mentions=["神秘女孩"])],
                characters=[CharacterResolutionItem(mention="神秘女孩", status="unresolved")])
    r = client.post(f"/api/project/{PROJECT_NAME}/story/resolve-character",
                    json={"mention": "神秘女孩", "action": "create", "description": "新设定"})
    assert r.status_code == 200, r.text
    res = next(x for x in r.json()["characters"] if x["mention"] == "神秘女孩")
    assert res["status"] == "matched"          # 已存在 -> 转 matched，不重复创建
    assert res["matched_character_name"] == "神秘女孩"
    # 项目角色仍是 1 个（没新增重复）
    proj2 = ProjectStore(pdir).load()
    assert len(proj2.characters) == 1
    assert proj2.characters[0].name == "神秘女孩"


def test_resolve_character_create_new(story_project, client):
    pdir = story_project
    proj = ProjectStore(pdir).load()
    assert proj.characters == []
    _seed_draft(pdir,
                story_snapshot=proj.story_text,
                scenes=[DraftScene(draft_id="draft_1", summary="A", character_mentions=["神秘女孩"])],
                characters=[CharacterResolutionItem(mention="神秘女孩", status="unresolved")])
    r = client.post(f"/api/project/{PROJECT_NAME}/story/resolve-character",
                    json={"mention": "神秘女孩", "action": "create", "description": "荒原少女"})
    assert r.status_code == 200, r.text
    res = next(x for x in r.json()["characters"] if x["mention"] == "神秘女孩")
    assert res["status"] == "created"
    proj2 = ProjectStore(pdir).load()
    assert len(proj2.characters) == 1
    assert proj2.characters[0].name == "神秘女孩"
    assert proj2.characters[0].description_en == "荒原少女"
    assert proj2.characters[0].views == {}


def test_resolve_character_ignore(story_project, client):
    pdir = story_project
    proj = ProjectStore(pdir).load()
    _seed_draft(pdir,
                story_snapshot=proj.story_text,
                scenes=[DraftScene(draft_id="draft_1", summary="A", character_mentions=["广播女声"])],
                characters=[CharacterResolutionItem(mention="广播女声", status="unresolved")])
    r = client.post(f"/api/project/{PROJECT_NAME}/story/resolve-character",
                    json={"mention": "广播女声", "action": "ignore"})
    assert r.status_code == 200, r.text
    res = next(x for x in r.json()["characters"] if x["mention"] == "广播女声")
    assert res["status"] == "ignored"
    assert res["matched_character_name"] is None


# ============ 3. 接口：apply 建 Scene ============
def test_apply_creates_scenes(story_project, client):
    pdir = story_project
    proj = ProjectStore(pdir).load()
    # 项目已有 赤焰守卫 + 神秘女孩（matched/created 都需存在）
    proj.characters = [Character(name="赤焰守卫"), Character(name="神秘女孩")]
    ProjectStore(pdir).save(proj)
    story = proj.story_text
    _seed_draft(pdir,
                story_snapshot=story,
                scenes=[
                    DraftScene(draft_id="draft_1", summary="段一", character_mentions=["赤焰守卫", "神秘女孩"]),
                    DraftScene(draft_id="draft_2", summary="段二", character_mentions=["广播女声"]),
                ],
                characters=[
                    CharacterResolutionItem(mention="赤焰守卫", status="matched", matched_character_name="赤焰守卫"),
                    CharacterResolutionItem(mention="神秘女孩", status="created", matched_character_name="神秘女孩"),
                    CharacterResolutionItem(mention="广播女声", status="ignored"),
                ])
    r = client.post(f"/api/project/{PROJECT_NAME}/story/apply")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["scenes"]) == 2
    # scene_id 自增不回收
    assert body["scenes"][0]["scene_id"] == "s01"
    assert body["scenes"][1]["scene_id"] == "s02"
    # ignored 不写入 characters；matched/created 写项目角色名
    assert body["scenes"][0]["characters"] == ["赤焰守卫", "神秘女孩"]
    assert body["scenes"][1]["characters"] == []
    # 草稿已删
    assert not _draft_path(pdir).exists()
    # next_scene_seq 推进
    proj2 = ProjectStore(pdir).load()
    assert proj2.next_scene_seq == 3


# ============ 3. 接口：草稿保存与读取 ============
def test_put_and_get_draft(story_project, fake_agnes_swap, client):
    # 先 organize 落一份草稿
    fake_agnes_swap._responses = [
        '{"overview":"","scenes":[{"draft_id":"draft_1","summary":"A","character_mentions":[]}]}'
    ]
    client.post(f"/api/project/{PROJECT_NAME}/story/organize")
    # PUT 编辑
    r = client.put(f"/api/project/{PROJECT_NAME}/story/draft", json={
        "overview": "改过", "scenes": [{"draft_id": "draft_1", "summary": "新文本", "character_mentions": []}],
        "characters": [],
    })
    assert r.status_code == 200, r.text
    # GET 读回
    r = client.get(f"/api/project/{PROJECT_NAME}/story/draft")
    assert r.status_code == 200
    body = r.json()
    assert body["overview"] == "改过"
    assert body["scenes"][0]["summary"] == "新文本"
    # project_updated_at / story_text_snapshot 仍由后端权威持有（不被 PUT 覆盖）
    assert body["story_text_snapshot"].startswith("赤焰守卫")
