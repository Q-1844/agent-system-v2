"""AI 角色设计路由（Phase AI-2，规格 提示词.md）。

5 个端点 + 草稿 IO（character_designs.json）+ stale 检查 + 改名/删除 hook。
模型只返回 CharacterDesignDraft；应用 description/保存/三视图生成都由普通代码完成。

  POST /character/{name}/design           AI 设计一个角色，落草稿
  PUT  /character/{name}/design            保存草稿编辑（用户改 generation_prompt 等）
  POST /character/{name}/design/apply      应用 generation_prompt -> Character.description_en（stale 检查）
  POST /character/design-batch             AI 批量设计无草稿角色（逐角色，部分失败不整批失败）
  GET  /character/designs                  读所有草稿（刷新恢复）

草稿落 projects/{p}/output/ai/character_designs.json，独立于 project.json，不用 localStorage。
stale = base_character_description + base_related_scene_signature + base_project_style_signature（§21），
不比 updated_at（避免首帧/视频/其他角色三视图任务假 stale）。
"""
import json
import time
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import deps
from deps import get_project_manager, logger, get_agnes_client
from src.core.store import ProjectStore, _atomic_write_json
from src.api.agnes_client import AgnesError
from src.ai.character_designer import (
    CharacterDesigner, CharacterDesignDraft, CharacterDesignDraftState,
)

router = APIRouter()


# ============ 草稿文件 IO ============
def _designs_path(project_dir: Path) -> Path:
    return Path(project_dir) / "output" / "ai" / "character_designs.json"


def _read_designs(project_dir: Path) -> dict:
    """读所有角色设计草稿 {name: CharacterDesignDraftState}。无文件返回 {}。"""
    p = _designs_path(project_dir)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        out = {}
        for name, state_dict in (raw or {}).items():
            try:
                out[name] = CharacterDesignDraftState.model_validate(state_dict)
            except Exception as e:
                logger.warning(f"角色设计草稿 {name} 解析失败: {e}")
        return out
    except Exception as e:
        logger.warning(f"读取角色设计草稿失败: {e}")
        return {}


def _write_designs(project_dir: Path, designs: dict):
    _atomic_write_json(_designs_path(project_dir),
                       {name: state.model_dump() for name, state in designs.items()})


def migrate_design_key(project_dir: Path, old_name: str, new_name: str):
    """角色改名时迁移草稿 key（§25）。新名已存在则跳过。供 characters.py rename 调用。"""
    if old_name == new_name:
        return
    designs = _read_designs(project_dir)
    if old_name not in designs:
        return
    if new_name in designs:
        # 新名已有草稿，不覆盖；旧 draft 标记 orphan 删除
        del designs[old_name]
        _write_designs(project_dir, designs)
        return
    state = designs.pop(old_name)
    state.character_name = new_name
    state.base_character_name = new_name
    state.draft.character_name = new_name
    designs[new_name] = state
    _write_designs(project_dir, designs)
    logger.info(f"角色设计草稿迁移: {old_name} -> {new_name}")


def delete_design(project_dir: Path, name: str):
    """角色删除时清理草稿（§26）。供 characters.py delete 调用。"""
    designs = _read_designs(project_dir)
    if name in designs:
        del designs[name]
        _write_designs(project_dir, designs)
        logger.info(f"角色设计草稿已删除: {name}")


# ============ 上下文 / stale ============
def _related_scene_signature(project, name: str) -> list:
    """该角色出现的场景签名 [{scene_id, summary}]（不含 shots/产物）。"""
    return [{"scene_id": sc.scene_id, "summary": sc.summary}
            for sc in project.scenes if name in sc.characters]


async def _design_one_async(designer: CharacterDesigner, project, char) -> CharacterDesignDraftState:
    name = char.name
    related = [(sc.scene_id, sc.summary) for sc in project.scenes if name in sc.characters]
    other_chars = [(oc.name, oc.description_en or "") for oc in project.characters if oc.name != name]
    style = project.style.suffix() if project.style else ""
    draft = await designer.design_character(
        name, char.description_en or "", project.story_text or "", related, other_chars, style)
    return CharacterDesignDraftState(
        character_name=name,
        base_character_name=name,
        base_character_description=char.description_en or "",
        base_related_scene_signature=[{"scene_id": sid, "summary": summ} for sid, summ in related],
        base_project_style_signature=style,
        draft=draft,
        status="draft",
        created_at=time.time(),
    )


def _check_stale(state: CharacterDesignDraftState, project, char, name: str) -> Optional[str]:
    """stale 判断（§21）。返回 None 表示不 stale，否则返回提示文案。"""
    if (char.description_en or "").strip() != (state.base_character_description or "").strip():
        return "角色的文字描述已经变化。建议根据最新内容重新设计。"
    if _related_scene_signature(project, name) != state.base_related_scene_signature:
        return "角色相关的故事段已经变化。建议根据最新内容重新设计。"
    current_style = project.style.suffix() if project.style else ""
    if current_style != state.base_project_style_signature:
        return "项目视觉风格已经变化。建议根据最新内容重新设计。"
    return None


# ============ 请求模型 ============
class SaveDesignRequest(BaseModel):
    draft: CharacterDesignDraft


class BatchDesignRequest(BaseModel):
    names: Optional[List[str]] = None   # None = 所有无草稿角色


# ============ 1. AI 设计一个角色 ============
@router.post("/api/project/{project_name}/character/{character_name}/design")
async def design_character(project_name: str, character_name: str):
    """AI 为一个角色设计视觉设定，落草稿。可重复调用（重新设计，覆盖草稿）。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        c = project.get_character(character_name)
        if c is None:
            raise HTTPException(404, f"角色 {character_name} 不存在")
        client = get_agnes_client()
        designer = CharacterDesigner(client)
        state = await _design_one_async(designer, project, c)
        designs = _read_designs(project_dir)
        designs[character_name] = state
        _write_designs(project_dir, designs)
        logger.info(f"AI 角色设计: {project_name}/{character_name}")
        return state.model_dump()
    except AgnesError as e:
        raise HTTPException(502, f"AI 服务错误: {e}")
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(502, f"AI 返回解析失败: {e}")
    except Exception as e:
        logger.error(f"角色设计失败: {e}")
        raise HTTPException(500, str(e))


# ============ 2. 保存草稿编辑 ============
@router.put("/api/project/{project_name}/character/{character_name}/design")
async def save_design(project_name: str, character_name: str, req: SaveDesignRequest):
    """保存用户对草稿的编辑（generation_prompt 等）。不动 project.json。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        designs = _read_designs(project_dir)
        state = designs.get(character_name)
        if state is None:
            raise HTTPException(404, "没有 AI 设计草稿，无法保存。请先 AI 设计角色。")
        state.draft = req.draft
        designs[character_name] = state
        _write_designs(project_dir, designs)
        return state.model_dump()
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存角色设计草稿失败: {e}")
        raise HTTPException(500, str(e))


# ============ 3. 应用 generation_prompt -> Character.description_en ============
@router.post("/api/project/{project_name}/character/{character_name}/design/apply")
async def apply_design(project_name: str, character_name: str):
    """应用草稿的 generation_prompt 到 Character.description_en（stale 检查，§15）。
    不自动改已有三视图（前端在 views!={} 时已提示）。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        c = project.get_character(character_name)
        if c is None:
            raise HTTPException(404, f"角色 {character_name} 不存在")
        designs = _read_designs(project_dir)
        state = designs.get(character_name)
        if state is None:
            raise HTTPException(404, "没有 AI 设计草稿，请先 AI 设计角色。")
        stale = _check_stale(state, project, c, character_name)
        if stale:
            raise HTTPException(409, stale)
        prompt = state.draft.generation_prompt.strip()
        if not prompt:
            raise HTTPException(400, "generation_prompt 为空，无法应用。")
        c.description_en = prompt
        store.save(project)
        # 应用后更新 base_character_description，使后续 stale 检查以"已应用"状态为基准
        state.base_character_description = prompt
        state.status = "applied"
        designs[character_name] = state
        _write_designs(project_dir, designs)
        logger.info(f"AI 角色设计已应用: {project_name}/{character_name}")
        return {"ok": True, "description_en": prompt}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"应用角色设计失败: {e}")
        raise HTTPException(500, str(e))


# ============ 4. AI 批量设计 ============
@router.post("/api/project/{project_name}/character/design-batch")
async def design_batch(project_name: str, req: BatchDesignRequest):
    """逐角色 AI 设计（§18）。只对无草稿角色（或指定 names）。部分失败不整批失败。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        designs = _read_designs(project_dir)
        client = get_agnes_client()
        designer = CharacterDesigner(client)
        if req.names:
            targets = [c for c in project.characters if c.name in req.names]
        else:
            targets = [c for c in project.characters if c.name not in designs]
        results = []
        for c in targets:
            try:
                state = await _design_one_async(designer, project, c)
                designs[c.name] = state
                results.append({"name": c.name, "ok": True})
            except Exception as e:
                logger.warning(f"批量设计 {c.name} 失败: {e}")
                results.append({"name": c.name, "ok": False, "error": str(e)[:200]})
        _write_designs(project_dir, designs)
        return {"results": results}
    except AgnesError as e:
        raise HTTPException(502, f"AI 服务错误: {e}")
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"批量角色设计失败: {e}")
        raise HTTPException(500, str(e))


# ============ 5. 读所有草稿（刷新恢复）============
@router.get("/api/project/{project_name}/character/designs")
async def get_designs(project_name: str):
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        designs = _read_designs(project_dir)
        return {"designs": {name: state.model_dump() for name, state in designs.items()}}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        raise HTTPException(500, str(e))
