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
import time
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
    StoryWorkflow, StoryOrganizationDraft, StoryContinuationDraft,
    DraftScene, CharacterResolutionItem,
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


# ============================================================
# Phase AI-1B：AI 续写故事（append-only workflow，规格 提示词.md §一~§三十一）
# 绝不修改已有 story_text / Scene / Shot；只追加 story_text 末尾 + Scene 末尾。
# 草稿落 projects/{p}/output/ai/story_continuation_draft.json，独立文件，不用 localStorage。
# stale 围绕 story_text + scene 结构签名判断，不比 updated_at（避免视频/图片任务假 stale，§22）。
# ============================================================

def _cont_draft_path(project_dir: Path) -> Path:
    return Path(project_dir) / "output" / "ai" / "story_continuation_draft.json"


def _read_cont_draft(project_dir: Path) -> Optional[StoryContinuationDraft]:
    p = _cont_draft_path(project_dir)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return StoryContinuationDraft.model_validate(data)
    except Exception as e:
        logger.warning(f"读取续写草稿失败: {e}")
        return None


def _write_cont_draft(project_dir: Path, draft: StoryContinuationDraft):
    _atomic_write_json(_cont_draft_path(project_dir), draft.model_dump())


def _delete_cont_draft(project_dir: Path):
    p = _cont_draft_path(project_dir)
    try:
        if p.exists():
            p.unlink()
    except Exception as e:
        logger.warning(f"删除续写草稿失败: {e}")


def _scene_signature(project) -> list:
    """Scene 结构签名：[{scene_id, summary, characters}]，不含 Shot/产物。
    用于续写 stale 判断（§21/22）--视频/图片/首尾帧/views 变化不进签名，不触发假 stale。"""
    return [{"scene_id": sc.scene_id, "summary": sc.summary,
             "characters": list(sc.characters)} for sc in project.scenes]


def _append_story(existing: str, continuation: str) -> str:
    """追加续写到完整故事末尾，中间空一行分隔。"""
    if not existing.strip():
        return continuation.strip()
    return existing.rstrip() + "\n\n" + continuation.strip()


class ContinueRequest(BaseModel):
    direction: str = ""
    scale: str = "standard"   # short / standard / long


class SaveContDraftRequest(BaseModel):
    """保存续写草稿编辑：只接受用户可编辑字段。base_story_text / base_scene_signature /
    draft_id / project_name / created_at 由后端权威持有。"""
    direction: str = ""
    scale: str = "standard"
    continuation_text: str = ""
    new_scene_drafts: List[DraftScene] = Field(default_factory=list)
    character_resolution: List[CharacterResolutionItem] = Field(default_factory=list)
    stage: str = "writing"


# ---------- 1. 生成续写 ----------
@router.post("/api/project/{project_name}/story/continue")
async def story_continue(project_name: str, req: ContinueRequest):
    """AI 续写故事正文（json_mode=False，纯文本）。落续写草稿，stage=continuation_ready。
    可重复调用（重新续写）：用最新 project 状态重新生成，覆盖草稿。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        story = (project.story_text or "").strip()
        if not story:
            raise HTTPException(400, "完整故事为空，无法续写。请先写下完整故事。")
        scale = req.scale if req.scale in ("short", "standard", "long") else "standard"
        existing_chars = [(c.name, c.description_en or "") for c in project.characters]
        existing_scene_summaries = [sc.summary for sc in project.scenes]
        style_weak = project.style.suffix() if project.style else ""
        client = get_agnes_client()
        wf = StoryWorkflow(client)
        continuation = await wf.continue_story(
            story, existing_chars, existing_scene_summaries,
            direction=req.direction, scale=scale, style_weak=style_weak)
        draft = StoryContinuationDraft(
            draft_id=f"cont_{uuid.uuid4().hex[:8]}",
            project_name=project_name,
            base_story_text=story,
            base_scene_signature=_scene_signature(project),
            direction=req.direction,
            scale=scale,
            continuation_text=continuation,
            new_scene_drafts=[],
            character_resolution=[],
            stage="continuation_ready",
            created_at=time.time(),
        )
        _write_cont_draft(project_dir, draft)
        logger.info(f"AI 续写生成: {project_name}, +{len(continuation)}字, scale={scale}")
        return draft.model_dump()
    except AgnesError as e:
        raise HTTPException(502, f"AI 服务错误: {e}")
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"生成续写失败: {e}")
        raise HTTPException(500, str(e))


# ---------- 2. 读续写草稿（刷新恢复）----------
@router.get("/api/project/{project_name}/story/continue/draft")
async def continue_get_draft(project_name: str):
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        draft = _read_cont_draft(project_dir)
        if draft is None:
            raise HTTPException(404, "没有 AI 续写草稿")
        return draft.model_dump()
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ---------- 3. 保存续写草稿编辑 ----------
@router.put("/api/project/{project_name}/story/continue/draft")
async def continue_save_draft(project_name: str, req: SaveContDraftRequest):
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        old = _read_cont_draft(project_dir)
        if old is None:
            raise HTTPException(404, "没有 AI 续写草稿，无法保存。请先生成续写。")
        old.direction = req.direction
        old.scale = req.scale
        old.continuation_text = req.continuation_text
        old.new_scene_drafts = req.new_scene_drafts
        old.character_resolution = req.character_resolution
        old.stage = req.stage
        _write_cont_draft(project_dir, old)
        return old.model_dump()
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存续写草稿失败: {e}")
        raise HTTPException(500, str(e))


# ---------- 4. 整理新增正文 -> 新故事段 + 人物识别 + 匹配 ----------
@router.post("/api/project/{project_name}/story/continue/organize")
async def continue_organize(project_name: str):
    """只把 continuation_text 拆成新故事段（不整理已有内容）。复用 Phase AI-1 匹配/保留逻辑。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        draft = _read_cont_draft(project_dir)
        if draft is None:
            raise HTTPException(404, "没有 AI 续写草稿")
        if not draft.continuation_text.strip():
            raise HTTPException(400, "续写内容为空，无法整理。")
        last_scene_summaries = [s["summary"] for s in draft.base_scene_signature]
        client = get_agnes_client()
        wf = StoryWorkflow(client)
        breakdown = await wf.organize_continuation(
            draft.base_story_text, last_scene_summaries, draft.continuation_text)

        # 收集去重后的人物提及
        mentions: list[str] = []
        seen = set()
        for s in breakdown.scenes:
            for m in s.character_mentions:
                if m not in seen:
                    seen.add(m); mentions.append(m)

        # 保留旧草稿里仍存在 mention 的非 unresolved 解决状态
        old_res = {r.mention: r for r in draft.character_resolution}
        existing = [c.name for c in project.characters]
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

        draft.new_scene_drafts = breakdown.scenes
        draft.character_resolution = resolutions
        draft.stage = "scenes_ready"
        _write_cont_draft(project_dir, draft)
        logger.info(f"续写整理: {project_name}, {len(breakdown.scenes)} 新段, {len(resolutions)} 人物")
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
        logger.error(f"整理续写失败: {e}")
        raise HTTPException(500, str(e))


# ---------- 5. 解决一个被提及人物（match / create / ignore）----------
@router.post("/api/project/{project_name}/story/continue/resolve-character")
async def continue_resolve_character(project_name: str, req: ResolveCharacterRequest):
    """复用 Phase AI-1 的 resolve 语义。create 去重：已存在同名 -> matched 不重复建。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        draft = _read_cont_draft(project_dir)
        if draft is None:
            raise HTTPException(404, "没有 AI 续写草稿")
        res = next((r for r in draft.character_resolution if r.mention == req.mention), None)
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
            char_name = (req.name or req.mention).strip() or req.mention
            store = ProjectStore(project_dir)
            p = store.load()
            existing = p.get_character(char_name)
            if existing is not None:
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
        else:
            raise HTTPException(400, "action 必须是 match / create / ignore")

        _write_cont_draft(project_dir, draft)
        return draft.model_dump()
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"continue resolve-character 失败: {e}")
        raise HTTPException(500, str(e))


# ---------- 6. AI 生成单个角色文字设定 ----------
@router.post("/api/project/{project_name}/story/continue/draft-character")
async def continue_draft_character(project_name: str, req: DraftCharacterRequest):
    """AI 为一个被提及人物生成文字设定。story 上下文 = base 故事 + 续写正文。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        draft = _read_cont_draft(project_dir)
        if draft is None:
            raise HTTPException(404, "没有 AI 续写草稿")
        mention = req.mention.strip()
        if not mention:
            raise HTTPException(400, "mention 不能为空")
        related = [s.summary for s in draft.new_scene_drafts if mention in s.character_mentions]
        full_story = _append_story(draft.base_story_text, draft.continuation_text)
        client = get_agnes_client()
        wf = StoryWorkflow(client)
        desc = await wf.draft_character(mention, full_story, related)
        return {"description": desc}
    except AgnesError as e:
        raise HTTPException(502, f"AI 服务错误: {e}")
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"续写-生成角色设定失败: {e}")
        raise HTTPException(500, str(e))


# ---------- 7. AI 批量生成缺失角色文字设定 ----------
@router.post("/api/project/{project_name}/story/continue/draft-characters-batch")
async def continue_draft_characters_batch(project_name: str, req: DraftCharactersBatchRequest):
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        draft = _read_cont_draft(project_dir)
        if draft is None:
            raise HTTPException(404, "没有 AI 续写草稿")
        if req.mentions:
            names = [m for m in req.mentions if m and m.strip()]
        else:
            names = [r.mention for r in draft.character_resolution if r.status == "unresolved"]
        if not names:
            return {"characters": []}
        full_story = _append_story(draft.base_story_text, draft.continuation_text)
        client = get_agnes_client()
        wf = StoryWorkflow(client)
        result = await wf.draft_characters_batch(names, full_story)
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
        logger.error(f"续写-批量生成角色设定失败: {e}")
        raise HTTPException(500, str(e))


# ---------- 8. 采用续写 -> append-only 写入（§十七）----------
@router.post("/api/project/{project_name}/story/continue/apply")
async def continue_apply(project_name: str):
    """append-only：一次 load -> 一次修改 -> 一次 save。
    只追加 story_text 末尾 + Scene 末尾（新 scene_id 自增），不动已有 Scene/Shot。
    stale（story_text + scene 签名）+ re-match（§20）+ unresolved 拦截 + 角色存在性验证。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        draft = _read_cont_draft(project_dir)
        if draft is None:
            raise HTTPException(404, "没有 AI 续写草稿")
        if not draft.continuation_text.strip():
            raise HTTPException(400, "续写内容为空")
        if not draft.new_scene_drafts:
            raise HTTPException(400, "还没有整理新故事段，请先「采用并整理新故事段」。")

        # stale 检查（§21/22）：story_text + scene 结构签名；不比 updated_at
        if (project.story_text or "").strip() != (draft.base_story_text or "").strip():
            raise HTTPException(409, "故事在 AI 续写期间已经发生变化。请根据最新故事重新生成续写。")
        if _scene_signature(project) != draft.base_scene_signature:
            raise HTTPException(409, "故事段结构或文本在 AI 续写期间已经发生变化。请根据最新故事重新生成续写。")

        # re-match（§20）：重新读 Project.characters，unresolved 重新确定性匹配
        existing_names = [c.name for c in project.characters]
        matched = StoryWorkflow.match_characters(
            [r.mention for r in draft.character_resolution], existing_names)
        unresolved_still: list[str] = []
        for r in draft.character_resolution:
            if r.status == "ignored":
                continue
            if r.status == "unresolved":
                m = matched.get(r.mention)
                if m:
                    r.status = "matched"
                    r.matched_character_name = m
                else:
                    unresolved_still.append(r.mention)
            else:
                # matched/created：验证角色仍存在
                if not r.matched_character_name or project.get_character(r.matched_character_name) is None:
                    raise HTTPException(409, f"角色 {r.mention} 已不存在，请返回角色检查步骤处理。")
        if unresolved_still:
            raise HTTPException(400, f"还有 {len(unresolved_still)} 个角色需要处理。")

        # append-only：一次修改
        res_by_mention = {r.mention: r for r in draft.character_resolution}
        project.story_text = _append_story(project.story_text or "", draft.continuation_text)
        new_scenes_out: list[dict] = []
        for ds in draft.new_scene_drafts:
            chars: list[str] = []
            for m in ds.character_mentions:
                r = res_by_mention.get(m)
                if r and r.status != "ignored" and r.matched_character_name:
                    if r.matched_character_name not in chars:
                        chars.append(r.matched_character_name)
            new_sid = f"s{project.next_scene_seq:02d}"
            project.next_scene_seq += 1
            project.scenes.append(Scene(scene_id=new_sid, summary=ds.summary, characters=chars, shots=[]))
            new_scenes_out.append({"scene_id": new_sid, "summary": ds.summary, "characters": chars})
        store.save(project)   # 一次 save

        created_chars = [r.matched_character_name for r in draft.character_resolution
                         if r.status == "created" and r.matched_character_name]
        _delete_cont_draft(project_dir)
        logger.info(f"AI 续写已应用: {project_name}, 追加 {len(new_scenes_out)} 段, "
                    f"+{len(draft.continuation_text)}字, +{len(created_chars)}角色")
        return {
            "ok": True,
            "appended_story_chars": len(draft.continuation_text.strip()),
            "new_scenes": new_scenes_out,
            "new_characters": created_chars,
            "first_new_scene_id": new_scenes_out[0]["scene_id"] if new_scenes_out else None,
        }
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"应用续写失败: {e}")
        raise HTTPException(500, str(e))


# ---------- 9. 仅追加到完整故事（§二十五，次级操作）----------
@router.post("/api/project/{project_name}/story/continue/append-story-only")
async def continue_append_story_only(project_name: str):
    """只追加 story_text，不创建 Scene。应用前检查 base_story_text。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        draft = _read_cont_draft(project_dir)
        if draft is None:
            raise HTTPException(404, "没有 AI 续写草稿")
        if not draft.continuation_text.strip():
            raise HTTPException(400, "续写内容为空")
        if (project.story_text or "").strip() != (draft.base_story_text or "").strip():
            raise HTTPException(409, "故事在 AI 续写期间已经发生变化。请根据最新故事重新生成续写。")
        project.story_text = _append_story(project.story_text or "", draft.continuation_text)
        store.save(project)
        _delete_cont_draft(project_dir)
        logger.info(f"续写仅追加故事: {project_name}, +{len(draft.continuation_text.strip())}字")
        return {"ok": True, "appended_story_chars": len(draft.continuation_text.strip())}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"仅追加故事失败: {e}")
        raise HTTPException(500, str(e))
