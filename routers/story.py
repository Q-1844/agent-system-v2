"""AI 整理故事路由（Phase AI-1，规格 提示词.md）。

7 个端点，同一个工作区内的连续流程：
  POST /story/organize                 AI 整理故事段 + 人物识别 + 确定性匹配，落草稿
  GET  /story/draft                    读草稿（刷新恢复，规格 §二十二）
  PUT  /story/draft                    保存草稿编辑（场景文本/排序/删除/解决状态）；不动 project.json
  POST /story/draft-character          AI 生成单个角色文字设定
  POST /story/draft-characters-batch   AI 批量生成缺失角色文字设定
  POST /story/resolve-character        match / create / ignore 一个被提及人物
  POST /story/apply                    采用故事整理 -> 写入 Scene[]（stale + 已有 Scene 闸门 + 去重 + 未解决拦截）

草稿落 projects/{p}/output/ai/story_draft.json，独立于 project.json，不用 localStorage。
模型只负责理解和写文本；角色匹配/建 Character/建 Scene 全部由本路由的普通代码完成。
"""
import json
import uuid
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import deps
from deps import get_project_manager, logger, get_agnes_client
from src.core.store import ProjectStore, _atomic_write_json
from src.core.models import Character, Scene
from src.api.agnes_client import AgnesError
from src.ai.story_workflow import (
    StoryWorkflow, StoryOrganizationDraft, DraftScene, CharacterResolutionItem,
)

router = APIRouter()


# ============ 草稿文件 IO ============
def _draft_path(project_dir: Path) -> Path:
    return Path(project_dir) / "output" / "ai" / "story_draft.json"


def _read_draft(project_dir: Path) -> Optional[StoryOrganizationDraft]:
    p = _draft_path(project_dir)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return StoryOrganizationDraft.model_validate(data)
    except Exception as e:
        logger.warning(f"读取故事草稿失败: {e}")
        return None


def _write_draft(project_dir: Path, draft: StoryOrganizationDraft):
    _atomic_write_json(_draft_path(project_dir), draft.model_dump())


def _delete_draft(project_dir: Path):
    p = _draft_path(project_dir)
    try:
        if p.exists():
            p.unlink()
    except Exception as e:
        logger.warning(f"删除故事草稿失败: {e}")


# ============ 请求模型 ============
class SaveDraftRequest(BaseModel):
    """保存草稿：只接受用户可编辑字段。draft_id / project_updated_at / story_text_snapshot
    由后端权威持有，不信任前端（防 stale 字段被覆盖）。"""
    overview: str = ""
    scenes: List[DraftScene] = Field(default_factory=list)
    characters: List[CharacterResolutionItem] = Field(default_factory=list)


class DraftCharacterRequest(BaseModel):
    mention: str


class DraftCharactersBatchRequest(BaseModel):
    mentions: Optional[List[str]] = None   # None = 所有 unresolved


class ResolveCharacterRequest(BaseModel):
    mention: str
    action: str                            # match / create / ignore
    matched_character_name: Optional[str] = None
    name: Optional[str] = None             # create 时的角色名（默认=mention，规格 §十二 角色名称可编辑）
    description: Optional[str] = None      # create 时的角色文字设定


# ============ 1. AI 整理故事 ============
@router.post("/api/project/{project_name}/story/organize")
async def story_organize(project_name: str):
    """AI 把完整故事整理成故事段 + 识别人物 + 确定性匹配项目角色，落草稿。可重复调用（重新整理）。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        story = (project.story_text or "").strip()
        if not story:
            raise HTTPException(400, "请先写下完整故事，再让 AI 帮你整理。")

        client = get_agnes_client()
        wf = StoryWorkflow(client)
        existing = [c.name for c in project.characters]
        breakdown = await wf.organize_story(story, existing)

        # 收集去重后的人物提及（保持首次出现顺序）
        mentions: list[str] = []
        seen = set()
        for s in breakdown.scenes:
            for m in s.character_mentions:
                if m not in seen:
                    seen.add(m)
                    mentions.append(m)

        # 重新整理时保留旧草稿里仍存在 mention 的非 unresolved 解决状态（规格 §七）
        old = _read_draft(project_dir)
        old_res = {r.mention: r for r in old.characters} if old else {}
        matched = StoryWorkflow.match_characters(mentions, existing)
        resolutions: list[CharacterResolutionItem] = []
        for m in mentions:
            if m in old_res and old_res[m].status != "unresolved":
                resolutions.append(old_res[m].model_copy())
            elif matched.get(m):
                resolutions.append(CharacterResolutionItem(
                    mention=m, status="matched", matched_character_name=matched[m]))
            else:
                resolutions.append(CharacterResolutionItem(mention=m, status="unresolved"))

        draft = StoryOrganizationDraft(
            draft_id=f"draft_{uuid.uuid4().hex[:8]}",
            project_name=project_name,
            project_updated_at=project.updated_at,
            story_text_snapshot=story,
            overview=breakdown.overview,
            scenes=breakdown.scenes,
            characters=resolutions,
        )
        _write_draft(project_dir, draft)
        logger.info(f"AI 整理故事: {project_name}, {len(draft.scenes)} 段, {len(resolutions)} 人物")
        return draft.model_dump()
    except AgnesError as e:
        raise HTTPException(502, f"AI 服务错误: {e}")
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(502, f"AI 返回解析失败: {e}")
    except Exception as e:
        logger.error(f"整理故事失败: {e}")
        raise HTTPException(500, str(e))


# ============ 2. 读草稿 ============
@router.get("/api/project/{project_name}/story/draft")
async def story_get_draft(project_name: str):
    """读现有草稿（刷新恢复用）。无草稿 404。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        draft = _read_draft(project_dir)
        if draft is None:
            raise HTTPException(404, "没有 AI 整理草稿")
        return draft.model_dump()
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ============ 3. 保存草稿编辑 ============
@router.put("/api/project/{project_name}/story/draft")
async def story_save_draft(project_name: str, req: SaveDraftRequest):
    """保存用户对草稿的编辑（场景文本/排序/删除/解决状态）。不动 project.json。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        old = _read_draft(project_dir)
        if old is None:
            raise HTTPException(404, "没有 AI 整理草稿，无法保存。请先整理故事。")
        old.overview = req.overview
        old.scenes = req.scenes
        old.characters = req.characters
        _write_draft(project_dir, old)
        return old.model_dump()
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存故事草稿失败: {e}")
        raise HTTPException(500, str(e))


# ============ 4. AI 生成单个角色文字设定 ============
@router.post("/api/project/{project_name}/story/draft-character")
async def story_draft_character(project_name: str, req: DraftCharacterRequest):
    """AI 为一个被提及人物生成文字设定（只返回文本，不落草稿，不建角色）。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        draft = _read_draft(project_dir)
        if draft is None:
            raise HTTPException(404, "没有 AI 整理草稿")
        mention = req.mention.strip()
        if not mention:
            raise HTTPException(400, "mention 不能为空")
        related = [s.summary for s in draft.scenes if mention in s.character_mentions]
        client = get_agnes_client()
        wf = StoryWorkflow(client)
        desc = await wf.draft_character(mention, project.story_text or "", related)
        return {"description": desc}
    except AgnesError as e:
        raise HTTPException(502, f"AI 服务错误: {e}")
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"生成角色设定失败: {e}")
        raise HTTPException(500, str(e))


# ============ 5. AI 批量生成缺失角色文字设定 ============
@router.post("/api/project/{project_name}/story/draft-characters-batch")
async def story_draft_characters_batch(project_name: str, req: DraftCharactersBatchRequest):
    """AI 一次为所有（或指定）unresolved 人物生成文字设定。只返回文本，不建角色。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        draft = _read_draft(project_dir)
        if draft is None:
            raise HTTPException(404, "没有 AI 整理草稿")
        if req.mentions:
            names = [m for m in req.mentions if m and m.strip()]
        else:
            names = [r.mention for r in draft.characters if r.status == "unresolved"]
        if not names:
            return {"characters": []}
        client = get_agnes_client()
        wf = StoryWorkflow(client)
        result = await wf.draft_characters_batch(names, project.story_text or "")
        return {"characters": [{"name": n, "description": d} for n, d in result]}
    except AgnesError as e:
        raise HTTPException(502, f"AI 服务错误: {e}")
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(502, f"AI 返回解析失败: {e}")
    except Exception as e:
        logger.error(f"批量生成角色设定失败: {e}")
        raise HTTPException(500, str(e))


# ============ 6. 解决一个被提及人物 ============
@router.post("/api/project/{project_name}/story/resolve-character")
async def story_resolve_character(project_name: str, req: ResolveCharacterRequest):
    """match / create / ignore 一个被提及人物。
    create：用户补充--再次检查项目是否已存在同名角色，已存在则转 matched 不重复创建（规格 §十二 + 用户补充）。
    """
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        draft = _read_draft(project_dir)
        if draft is None:
            raise HTTPException(404, "没有 AI 整理草稿")
        res = next((r for r in draft.characters if r.mention == req.mention), None)
        if res is None:
            raise HTTPException(400, f"草稿中不存在人物：{req.mention}")

        if req.action == "match":
            if not req.matched_character_name:
                raise HTTPException(400, "匹配已有角色需要 matched_character_name")
            res.status = "matched"
            res.matched_character_name = req.matched_character_name
            res.draft_description = None
        elif req.action == "ignore":
            res.status = "ignored"
            res.matched_character_name = None
            res.draft_description = None
        elif req.action == "create":
            # 角色名默认=mention，用户可在"角色名称"字段改写（规格 §十二）
            char_name = (req.name or req.mention).strip() or req.mention
            store = ProjectStore(project_dir)
            p = store.load()
            existing = p.get_character(char_name)
            if existing is not None:
                # 已存在同名角色 -> 转 matched，不重复创建
                res.status = "matched"
                res.matched_character_name = existing.name
                res.draft_description = None
            else:
                c = Character(name=char_name, description_en=(req.description or ""))
                p.characters.append(c)
                store.save(p)
                res.status = "created"
                res.matched_character_name = char_name
                res.draft_description = req.description
                # workflow 内变更项目，重新盖戳草稿版本（apply 时不被判 stale）
                draft.project_updated_at = p.updated_at
        else:
            raise HTTPException(400, "action 必须是 match / create / ignore")

        _write_draft(project_dir, draft)
        return draft.model_dump()
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"resolve-character 失败: {e}")
        raise HTTPException(500, str(e))


# ============ 7. 采用故事整理 -> 写入 Scene[] ============
@router.post("/api/project/{project_name}/story/apply")
async def story_apply(project_name: str):
    """采用 AI 整理结果，创建 Scene[]。stale + 已有 Scene 闸门 + 未解决拦截 + 角色去重再次检查。
    只用于项目无 Scene 的情况（用户决策 2 / 规格 §十八）。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        draft = _read_draft(project_dir)
        if draft is None:
            raise HTTPException(404, "没有 AI 整理草稿")

        # stale 检查（规格 §二十三）：完整故事变化 -> 拒绝，不用旧草稿覆盖新内容
        if (project.story_text or "").strip() != (draft.story_text_snapshot or "").strip():
            raise HTTPException(409, "完整故事或项目角色已经发生变化。请根据最新内容重新整理。")

        # 已有 Scene 闸门（用户决策 2 / 规格 §十八）：有任何 Scene -> 拒绝覆盖
        if project.scenes:
            raise HTTPException(409, "当前项目已有分镜内容。为避免删除已有镜头和生成结果，暂不支持 AI 一键覆盖现有故事段。你可以手动修改现有故事段，或在空项目中重新整理。")

        # unresolved 残留拦截（规格 §十五）
        unresolved = [r.mention for r in draft.characters if r.status == "unresolved"]
        if unresolved:
            raise HTTPException(400, f"还有 {len(unresolved)} 个角色需要处理。")

        # 角色去重再次检查（用户补充）：created/matched 的角色必须仍存在于项目
        res_by_mention = {r.mention: r for r in draft.characters}
        for r in draft.characters:
            if r.status in ("matched", "created"):
                if not r.matched_character_name or project.get_character(r.matched_character_name) is None:
                    raise HTTPException(409, f"角色 {r.mention} 已不存在，请返回角色检查步骤处理。")

        # 建 Scene（scene_id 自增不回收，与 routers/scenes.py 一致）
        new_scenes: list[Scene] = []
        for ds in draft.scenes:
            chars: list[str] = []
            for m in ds.character_mentions:
                r = res_by_mention.get(m)
                if r and r.status in ("matched", "created") and r.matched_character_name:
                    if r.matched_character_name not in chars:
                        chars.append(r.matched_character_name)
            new_sid = f"s{project.next_scene_seq:02d}"
            project.next_scene_seq += 1
            new_scenes.append(Scene(scene_id=new_sid, summary=ds.summary, characters=chars, shots=[]))
        project.scenes = new_scenes
        store.save(project)

        _delete_draft(project_dir)
        logger.info(f"AI 整理故事已采用: {project_name}, 创建 {len(new_scenes)} 个故事段")
        return {
            "ok": True,
            "scenes": [{"scene_id": sc.scene_id, "summary": sc.summary,
                        "characters": list(sc.characters)} for sc in new_scenes],
        }
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"采用故事整理失败: {e}")
        raise HTTPException(500, str(e))
