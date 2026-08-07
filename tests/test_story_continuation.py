"""Phase AI-1B：AI 续写故事测试。

重点验证 append-only（原 Scene/Shot 不动）+ stale（story_text+scene 签名，不被视频/图片任务误触发）
+ re-match（§20）+ resolve 去重。AI 调用用 FakeAgnes，apply/resolve 不调 AI 直接预置草稿。
"""
import asyncio
import shutil
import pytest

import deps
from deps import get_project_manager
from src.core.store import ProjectStore
from src.core.models import Project, Scene, Shot, StyleAnchor, Character, ImageAsset, Asset
from src.ai.story_workflow import (
    StoryWorkflow, StoryContinuationDraft, DraftScene, CharacterResolutionItem,
)
from routers.story import _cont_draft_path, _write_cont_draft, _scene_signature

PROJECT_NAME = "_test_cont"


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


def _make_project(pname, *, story_text="原始故事。赤焰守卫在机库。", chars=None, scenes=None):
    pm = get_project_manager()
    shutil.rmtree(pm.base_dir / pname, ignore_errors=True)
    pdir = pm.create_project(pname)
    proj = Project(name=pname, style=StyleAnchor(aspect_ratio="16:9", size_tier="1K"),
                   story_text=story_text,
                   characters=[Character(name=n, description_en=d) for n, d in (chars or [])],
                   scenes=scenes or [],
                   next_scene_seq=(max([int(s.scene_id[1:]) for s in (scenes or [])], default=0) + 1))
    ProjectStore(pdir).save(proj)
    return pdir


def _proj_with_existing(pname):
    """建项目：s01 含 1 个 shot（有首帧/台词），s02 空。角色 赤焰守卫。"""
    sh1 = Shot(shot_id="s01_c01", scene_id="s01", index=1,
               description="原始镜头描述", prompt_en="orig prompt, anime",
               link_mode="i2v", dialogue="原始台词",
               first_frame=ImageAsset(
                   candidates=[Asset(local="output/frames/s01.png",
                                     url="https://mock/agnes/s01.png", status="done")],
                   selected_index=0, status="done"))
    s01 = Scene(scene_id="s01", summary="故事段一", characters=["赤焰守卫"], shots=[sh1])
    s02 = Scene(scene_id="s02", summary="故事段二", characters=[], shots=[])
    return _make_project(pname, chars=[("赤焰守卫", "机甲")], scenes=[s01, s02])


def _seed_cont_draft(pdir, *, story, continuation="续写正文：赤焰守卫进入城市，遇到林夏。广播响起。",
                     scenes=None, characters=None, stage="ready_to_apply"):
    proj = ProjectStore(pdir).load()
    draft = StoryContinuationDraft(
        draft_id="cont_test",
        project_name=PROJECT_NAME,
        base_story_text=story,
        base_scene_signature=_scene_signature(proj),   # 用当前 project 结构生成签名
        direction="",
        scale="standard",
        continuation_text=continuation,
        new_scene_drafts=scenes if scenes is not None else [
            DraftScene(draft_id="draft_1", summary="新段一", character_mentions=["赤焰守卫", "林夏"]),
            DraftScene(draft_id="draft_2", summary="新段二", character_mentions=["广播女声"]),
        ],
        character_resolution=characters if characters is not None else [
            CharacterResolutionItem(mention="赤焰守卫", status="matched", matched_character_name="赤焰守卫"),
            CharacterResolutionItem(mention="林夏", status="created", matched_character_name="林夏"),
            CharacterResolutionItem(mention="广播女声", status="ignored"),
        ],
        stage=stage,
        created_at=0.0,
    )
    _write_cont_draft(pdir, draft)
    return draft


@pytest.fixture
def cont_project(client):
    pdir = _proj_with_existing(PROJECT_NAME)
    yield pdir
    shutil.rmtree(get_project_manager().get_project_dir(PROJECT_NAME), ignore_errors=True)


@pytest.fixture
def fake_agnes_swap(client):
    fake = FakeAgnes()
    real = deps.agnes_client
    deps.agnes_client = fake
    yield fake
    deps.agnes_client = real


# ============ 1. continue_story / organize_continuation 单元（FakeAgnes）============
def test_continue_story_returns_plain_text():
    wf = StoryWorkflow(FakeAgnes(["赤焰守卫走进城市，信号来自通信塔。"]))
    text = asyncio.run(wf.continue_story("原始故事", [("赤焰守卫", "机甲")], ["故事段一"], "", "standard"))
    assert "城市" in text
    assert text == "赤焰守卫走进城市，信号来自通信塔。"


def test_organize_continuation_parses_json():
    raw = '{"overview":"2段","scenes":[{"draft_id":"draft_1","summary":"进城","character_mentions":["赤焰守卫"]},{"draft_id":"draft_2","summary":"遇林夏","character_mentions":["林夏"]}]}'
    wf = StoryWorkflow(FakeAgnes([raw]))
    res = asyncio.run(wf.organize_continuation("原始故事", ["故事段一"], "续写正文"))
    assert len(res.scenes) == 2
    assert res.scenes[1].character_mentions == ["林夏"]


def test_chat_json_retry_once_on_bad_json():
    # 第 1 次返回非 JSON，第 2 次（重试）返回合法 JSON
    wf = StoryWorkflow(FakeAgnes(["这不是JSON", '{"ok":true}']))
    obj = asyncio.run(wf._chat_json([{"role": "user", "content": "x"}], max_tokens=10))
    assert obj == {"ok": True}
    assert len(wf._client.calls) == 2   # 重试了一次


# ============ 2. apply append-only：原 Scene/Shot 完全不动 ============
def test_apply_append_only_preserves_existing(cont_project, client):
    pdir = cont_project
    # 林夏 为 created 状态 -> 模拟 workflow 已 eager 创建林夏
    proj_before = ProjectStore(pdir).load()
    proj_before.characters.append(Character(name="林夏", description_en="少女"))
    ProjectStore(pdir).save(proj_before)
    story_before = proj_before.story_text
    s01_shot_before = proj_before.scenes[0].shots[0]
    _seed_cont_draft(pdir, story=story_before)

    r = client.post(f"/api/project/{PROJECT_NAME}/story/continue/apply")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["new_scenes"]) == 2
    assert body["new_scenes"][0]["scene_id"] == "s03"   # 接现有 s01/s02 之后
    assert body["new_scenes"][1]["scene_id"] == "s04"
    # ignored 不写入；matched/created 写项目角色名
    assert body["new_scenes"][0]["characters"] == ["赤焰守卫", "林夏"]
    assert body["new_scenes"][1]["characters"] == []

    proj_after = ProjectStore(pdir).load()
    # story_text 追加了续写
    assert proj_after.story_text == story_before + "\n\n" + "续写正文：赤焰守卫进入城市，遇到林夏。广播响起。"
    # 原 Scene 数量 + 2，且前两个不变
    assert len(proj_after.scenes) == 4
    assert proj_after.scenes[0].scene_id == "s01"
    assert proj_after.scenes[0].summary == "故事段一"
    assert proj_after.scenes[1].scene_id == "s02"
    # 原 Shot 完全不动
    s01_shot_after = proj_after.scenes[0].shots[0]
    assert s01_shot_after.shot_id == "s01_c01"
    assert s01_shot_after.description == "原始镜头描述"
    assert s01_shot_after.prompt_en == "orig prompt, anime"
    assert s01_shot_after.dialogue == "原始台词"
    assert s01_shot_after.first_frame.candidates[0].url == "https://mock/agnes/s01.png"
    # next_scene_seq 推进
    assert proj_after.next_scene_seq == 5
    # 草稿删除
    assert not _cont_draft_path(pdir).exists()


# ============ 3. stale：story_text / scene 签名变化才判；shot/产物变化不判 ============
def test_apply_stale_story_changed(cont_project, client):
    pdir = cont_project
    proj = ProjectStore(pdir).load()
    _seed_cont_draft(pdir, story=proj.story_text)
    # 故事被改
    proj.story_text = proj.story_text + "（后被改过）"
    ProjectStore(pdir).save(proj)
    r = client.post(f"/api/project/{PROJECT_NAME}/story/continue/apply")
    assert r.status_code == 409
    assert "故事" in r.json()["detail"]


def test_apply_stale_scene_summary_changed(cont_project, client):
    pdir = cont_project
    proj = ProjectStore(pdir).load()
    _seed_cont_draft(pdir, story=proj.story_text)
    # 已有 scene 摘要被改
    proj.scenes[0].summary = "改过的故事段一"
    ProjectStore(pdir).save(proj)
    r = client.post(f"/api/project/{PROJECT_NAME}/story/continue/apply")
    assert r.status_code == 409
    assert "故事段" in r.json()["detail"]


def test_apply_not_stale_on_shot_asset_change(cont_project, client):
    """§22：视频/首帧/views 等任务完成不应触发 stale。改 shot 产物，签名不变。"""
    pdir = cont_project
    # 林夏 created -> 预创建
    proj = ProjectStore(pdir).load()
    proj.characters.append(Character(name="林夏", description_en="少女"))
    ProjectStore(pdir).save(proj)
    story = proj.story_text
    _seed_cont_draft(pdir, story=story)
    # 模拟视频/首帧任务完成：改 shot 产物（不在 scene 签名里）
    proj = ProjectStore(pdir).load()
    proj.scenes[0].shots[0].first_frame.candidates[0].url = "https://new/url.png"
    proj.scenes[0].shots.append(Shot(shot_id="s01_c02", scene_id="s01", index=2, link_mode="i2v"))
    ProjectStore(pdir).save(proj)
    r = client.post(f"/api/project/{PROJECT_NAME}/story/continue/apply")
    assert r.status_code == 200, r.text   # 不判 stale，apply 成功


def test_apply_not_stale_on_character_views(cont_project, client):
    """§22：角色重新生成三视图（views 变化）不触发 stale。"""
    pdir = cont_project
    proj = ProjectStore(pdir).load()
    proj.characters.append(Character(name="林夏", description_en="少女"))
    ProjectStore(pdir).save(proj)
    story = proj.story_text
    _seed_cont_draft(pdir, story=story)
    # 给赤焰守卫加 views（模拟生成了三视图）
    proj = ProjectStore(pdir).load()
    proj.characters[0].views["front"] = Asset(local="output/characters/赤焰守卫_front.png", status="done")
    ProjectStore(pdir).save(proj)
    r = client.post(f"/api/project/{PROJECT_NAME}/story/continue/apply")
    assert r.status_code == 200, r.text


# ============ 4. re-match（§20）：unresolved 在 apply 时重新匹配 ============
def test_apply_rematch_unresolved(cont_project, client):
    pdir = cont_project
    proj = ProjectStore(pdir).load()
    story = proj.story_text
    # 林夏 unresolved（项目无林夏）
    _seed_cont_draft(pdir, story=story, characters=[
        CharacterResolutionItem(mention="赤焰守卫", status="matched", matched_character_name="赤焰守卫"),
        CharacterResolutionItem(mention="林夏", status="unresolved"),
        CharacterResolutionItem(mention="广播女声", status="ignored"),
    ])
    # 期间用户在别处创建了林夏
    proj = ProjectStore(pdir).load()
    proj.characters.append(Character(name="林夏", description_en="少女"))
    ProjectStore(pdir).save(proj)
    r = client.post(f"/api/project/{PROJECT_NAME}/story/continue/apply")
    assert r.status_code == 200, r.text
    # 林夏自动转 matched，不重复创建，写进新 Scene
    proj_after = ProjectStore(pdir).load()
    assert sum(1 for c in proj_after.characters if c.name == "林夏") == 1   # 没重复
    s03 = next(s for s in proj_after.scenes if s.scene_id == "s03")
    assert s03.characters == ["赤焰守卫", "林夏"]


def test_apply_blocks_still_unresolved(cont_project, client):
    pdir = cont_project
    proj = ProjectStore(pdir).load()
    _seed_cont_draft(pdir, story=proj.story_text, characters=[
        CharacterResolutionItem(mention="赤焰守卫", status="matched", matched_character_name="赤焰守卫"),
        CharacterResolutionItem(mention="林夏", status="unresolved"),   # 仍无对应角色
    ])
    r = client.post(f"/api/project/{PROJECT_NAME}/story/continue/apply")
    assert r.status_code == 400
    assert "1 个角色" in r.json()["detail"]


# ============ 5. resolve-character create 去重 ============
def test_continue_resolve_create_dedup(cont_project, client):
    pdir = cont_project
    proj = ProjectStore(pdir).load()
    _seed_cont_draft(pdir, story=proj.story_text, characters=[
        CharacterResolutionItem(mention="林夏", status="unresolved"),
    ])
    # 项目已有林夏
    proj = ProjectStore(pdir).load()
    proj.characters.append(Character(name="林夏", description_en="已存在"))
    ProjectStore(pdir).save(proj)
    r = client.post(f"/api/project/{PROJECT_NAME}/story/continue/resolve-character",
                    json={"mention": "林夏", "action": "create", "description": "新设定"})
    assert r.status_code == 200, r.text
    res = next(x for x in r.json()["character_resolution"] if x["mention"] == "林夏")
    assert res["status"] == "matched"   # 已存在 -> 转 matched，不重复建
    proj2 = ProjectStore(pdir).load()
    assert sum(1 for c in proj2.characters if c.name == "林夏") == 1


def test_continue_resolve_create_new(cont_project, client):
    pdir = cont_project
    proj = ProjectStore(pdir).load()
    _seed_cont_draft(pdir, story=proj.story_text, characters=[
        CharacterResolutionItem(mention="林夏", status="unresolved"),
    ])
    r = client.post(f"/api/project/{PROJECT_NAME}/story/continue/resolve-character",
                    json={"mention": "林夏", "action": "create", "description": "少女"})
    assert r.status_code == 200, r.text
    res = next(x for x in r.json()["character_resolution"] if x["mention"] == "林夏")
    assert res["status"] == "created"
    proj2 = ProjectStore(pdir).load()
    assert any(c.name == "林夏" and c.description_en == "少女" for c in proj2.characters)


# ============ 6. continue / organize 端点（FakeAgnes）============
def test_continue_endpoint(cont_project, fake_agnes_swap, client):
    fake_agnes_swap._responses = ["赤焰守卫走进城市，遇到林夏。"]
    r = client.post(f"/api/project/{PROJECT_NAME}/story/continue",
                    json={"direction": "进入城市", "scale": "short"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "城市" in body["continuation_text"]
    assert body["stage"] == "continuation_ready"
    assert body["base_story_text"].startswith("原始故事")
    assert len(body["base_scene_signature"]) == 2   # s01, s02
    assert _cont_draft_path(cont_project).exists()


def test_continue_empty_story_400(client):
    _make_project(PROJECT_NAME, story_text="   ")
    try:
        r = client.post(f"/api/project/{PROJECT_NAME}/story/continue", json={})
        assert r.status_code == 400
    finally:
        shutil.rmtree(get_project_manager().get_project_dir(PROJECT_NAME), ignore_errors=True)


def test_organize_endpoint(cont_project, fake_agnes_swap, client):
    # 先 continue 落草稿
    fake_agnes_swap._responses = ["续写正文：进城。遇林夏。"]
    client.post(f"/api/project/{PROJECT_NAME}/story/continue", json={})
    # 再 organize
    fake_agnes_swap._responses = [
        '{"overview":"2段","scenes":[{"draft_id":"draft_1","summary":"进城","character_mentions":["赤焰守卫","林夏"]}]}'
    ]
    r = client.post(f"/api/project/{PROJECT_NAME}/story/continue/organize")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["new_scene_drafts"]) == 1
    assert body["stage"] == "scenes_ready"
    # 赤焰守卫 已存在 -> matched；林夏 不存在 -> unresolved
    res_by = {x["mention"]: x for x in body["character_resolution"]}
    assert res_by["赤焰守卫"]["status"] == "matched"
    assert res_by["林夏"]["status"] == "unresolved"


# ============ 7. append-story-only ============
def test_append_story_only(cont_project, client):
    pdir = cont_project
    proj = ProjectStore(pdir).load()
    story = proj.story_text
    _seed_cont_draft(pdir, story=story)
    r = client.post(f"/api/project/{PROJECT_NAME}/story/continue/append-story-only")
    assert r.status_code == 200, r.text
    proj_after = ProjectStore(pdir).load()
    assert proj_after.story_text == story + "\n\n" + "续写正文：赤焰守卫进入城市，遇到林夏。广播响起。"
    # 不创建 Scene
    assert len(proj_after.scenes) == 2
    assert not _cont_draft_path(pdir).exists()


def test_append_story_only_stale(cont_project, client):
    pdir = cont_project
    proj = ProjectStore(pdir).load()
    _seed_cont_draft(pdir, story=proj.story_text)
    proj.story_text = "改过的故事"
    ProjectStore(pdir).save(proj)
    r = client.post(f"/api/project/{PROJECT_NAME}/story/continue/append-story-only")
    assert r.status_code == 409
