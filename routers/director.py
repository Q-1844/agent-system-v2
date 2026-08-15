"""AI 导演路由（Phase AI-3，规格 提示词.md）。

5 个端点 + 草稿 IO（director_drafts.json by scene_id）+ stale 检查 + apply 创建 Shot[]。
模型只返回 DirectorDraft；draft -> Shot[] 由本路由普通代码完成（复用 Shot 模型，shot_id 自增）。
不生成首帧/尾帧/视频提示词，不写 opening_source/link_mode（用默认）。

  POST   /scene/{scene_id}/director/plan     AI 规划镜头，落草稿（body: {director_note}）
  GET    /scene/{scene_id}/director/draft     读草稿（刷新恢复）
  PUT    /scene/{scene_id}/director/draft     保存草稿编辑（intent/characters/时长/排序/删除/添加）
  DELETE /scene/{scene_id}/director/draft     删草稿
  POST   /scene/{scene_id}/director/apply     创建 Shot[]（stale + 已有 Shot 闸门 + 角色覆盖 + 一次 save）

草稿落 projects/{p}/output/ai/director_drafts.json，按 scene_id 存，不用 localStorage。
stale = scene_summary_snapshot + scene_characters_snapshot 比对（§二十四），不比 updated_at。
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
from src.core.models import Shot
from src.api.agnes_client import AgnesError
from src.ai.director import (
    DirectorWorkflow, DirectorDraft, ShotPlanItem, coverage_missing,
)

router = APIRouter()


# ============ 草稿文件 IO ============
def _drafts_path(project_dir: Path) -> Path:
    return Path(project_dir) / "output" / "ai" / "director_drafts.json"


def _read_drafts(project_dir: Path) -> dict:
    p = _drafts_path(project_dir)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        out = {}
        for sid, d in (raw or {}).items():
            try:
                out[sid] = DirectorDraft.model_validate(d)
            except Exception as e:
                logger.warning(f"导演草稿 {sid} 解析失败: {e}")
        return out
    except Exception as e:
        logger.warning(f"读取导演草稿失败: {e}")
        return {}


def _write_drafts(project_dir: Path, drafts: dict):
    _atomic_write_json(_drafts_path(project_dir),
                       {sid: d.model_dump() for sid, d in drafts.items()})


def _find_scene(project, scene_id: str):
    return next((sc for sc in project.scenes if sc.scene_id == scene_id), None)


def _check_stale(draft: DirectorDraft, scene) -> Optional[str]:
    """stale 判断（§二十四）：scene summary/characters 变化才 stale。"""
    if (scene.summary or "") != (draft.scene_summary_snapshot or ""):
        return "这个故事段已经发生变化（故事文本已改）。请根据最新内容重新规划镜头。"
    if list(scene.characters) != list(draft.scene_characters_snapshot):
        return "这个故事段已经发生变化（出场角色已改）。请根据最新内容重新规划镜头。"
    return None


# ============ 请求模型 ============
class PlanRequest(BaseModel):
    director_note: str = ""


class SaveDraftRequest(BaseModel):
    overview: str = ""
    shots: List[ShotPlanItem] = Field(default_factory=list)
    director_note: str = ""


# ============ business helper（§九：DirectorStudio 与 FullProjectManager 共用同一套
# validation / coverage / Shot 创建 / shot_id 分配；不绕 HTTP，不拼 Agnes system prompt）============
class DirectorBusinessError(Exception):
    """Director apply business 错误基类（router -> HTTP，FullProjectManager -> repair/blocked）。"""


class DirectorStaleError(DirectorBusinessError):
    """草稿 stale（scene summary/characters 变化，§二十四）。"""


class DirectorHasShotsError(DirectorBusinessError):
    """Scene 已有 Shot（§十六 禁止覆盖）。"""


class DirectorCoverageError(DirectorBusinessError):
    """角色未覆盖（§十三）。FullProjectManager 据此触发 repair（§八：最多 1 repair）。"""


async def plan_scene_business(project, scene, director_note: str, client) -> DirectorDraft:
    """AI 规划镜头（§九）。包 DirectorWorkflow.plan_scene + 填 created_at。
    不落盘 draft（落盘由调用方决定）。供 routers/director.plan 与 FullProjectManager 共用。"""
    wf = DirectorWorkflow(client)
    draft = await wf.plan_scene(project, scene, director_note)
    draft.created_at = time.time()
    return draft


def apply_director_draft_business(store, project, scene_id: str,
                                  draft: DirectorDraft) -> List[Shot]:
    """采用镜头方案 -> 创建 Shot[]（§十七/§十八）。stale + 已有 Shot 闸门 + 角色覆盖 + 一次 save。
    只用于 Scene.shots 为空（§十六：已有 Shot 禁止覆盖）。返回创建的 Shot[]（已 save）。
    🔴 不删 draft（删 draft 由调用方决定）；不写 opening_source/link_mode/prompt（用默认，§二十/二十一/二十二）。
    raises DirectorStaleError / DirectorHasShotsError / DirectorCoverageError / ValueError。"""
    scene = _find_scene(project, scene_id)
    if scene is None:
        raise ValueError(f"故事段 {scene_id} 不存在")
    if not draft.shots:
        raise ValueError("草稿中没有镜头。")

    stale = _check_stale(draft, scene)
    if stale:
        raise DirectorStaleError(stale)
    if scene.shots:
        raise DirectorHasShotsError("这个故事段已有镜头。本阶段不会自动覆盖或删除已有镜头。")
    missing = coverage_missing(draft)
    if missing:
        raise DirectorCoverageError(
            f"角色 {('、'.join(missing))} 尚未安排到任何镜头。请将其加入某个镜头或重新规划。")

    # 创建 Shot[]（§十八：复用 Shot 模型，description=intent，其他默认；占位 shot_id 下面统一重编号）
    new_shots: List[Shot] = []
    for sh in draft.shots:
        new_shots.append(Shot(
            shot_id=f"{scene_id}_cTMP",
            scene_id=scene_id,
            index=0,
            description=sh.intent,
            characters=list(sh.characters),
            target_duration_s=sh.target_duration_s,
        ))
    scene.shots = new_shots
    for i, sh in enumerate(scene.shots):
        sh.index = i + 1
        sh.shot_id = f"{scene_id}_c{i + 1:02d}"
    store.save(project)   # 一次 save（§十七：原子完成）
    return new_shots


# ============ 1. AI 规划 ============
@router.post("/api/project/{project_name}/scene/{scene_id}/director/plan")
async def director_plan(project_name: str, scene_id: str, req: PlanRequest):
    """AI 为一个故事段规划镜头，落草稿。可重复调用（重新规划，覆盖草稿）。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        scene = _find_scene(project, scene_id)
        if scene is None:
            raise HTTPException(404, f"故事段 {scene_id} 不存在")
        client = get_agnes_client()
        draft = await plan_scene_business(project, scene, req.director_note, client)
        drafts = _read_drafts(project_dir)
        drafts[scene_id] = draft
        _write_drafts(project_dir, drafts)
        logger.info(f"AI 导演规划: {project_name}/{scene_id}, {len(draft.shots)} 镜头")
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
        logger.error(f"AI 导演规划失败: {e}")
        raise HTTPException(500, str(e))


# ============ 2. 读草稿 ============
@router.get("/api/project/{project_name}/scene/{scene_id}/director/draft")
async def director_get_draft(project_name: str, scene_id: str):
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        drafts = _read_drafts(project_dir)
        draft = drafts.get(scene_id)
        if draft is None:
            raise HTTPException(404, "没有 AI 导演草稿")
        return draft.model_dump()
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ============ 3. 保存草稿编辑 ============
@router.put("/api/project/{project_name}/scene/{scene_id}/director/draft")
async def director_save_draft(project_name: str, scene_id: str, req: SaveDraftRequest):
    """保存用户对草稿的编辑（intent/characters/时长/排序/删除/添加）。不动 project.json。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        drafts = _read_drafts(project_dir)
        draft = drafts.get(scene_id)
        if draft is None:
            raise HTTPException(404, "没有 AI 导演草稿，无法保存。请先 AI 规划镜头。")
        draft.overview = req.overview
        draft.shots = req.shots
        draft.director_note = req.director_note
        _write_drafts(project_dir, drafts)
        return draft.model_dump()
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存导演草稿失败: {e}")
        raise HTTPException(500, str(e))


# ============ 4. 删草稿 ============
@router.delete("/api/project/{project_name}/scene/{scene_id}/director/draft")
async def director_delete_draft(project_name: str, scene_id: str):
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        drafts = _read_drafts(project_dir)
        if scene_id in drafts:
            del drafts[scene_id]
            _write_drafts(project_dir, drafts)
        return {"ok": True}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        raise HTTPException(500, str(e))


# ============ 5. apply -> 创建 Shot[] ============
@router.post("/api/project/{project_name}/scene/{scene_id}/director/apply")
async def director_apply(project_name: str, scene_id: str):
    """采用镜头方案 -> 创建 Shot[]（§十七）。stale + 已有 Shot 闸门 + 角色覆盖 + 一次 save。
    只用于 Scene.shots 为空的情况（§十六：已有 Shot 禁止覆盖）。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        drafts = _read_drafts(project_dir)
        draft = drafts.get(scene_id)
        if draft is None:
            raise HTTPException(404, "没有 AI 导演草稿，请先 AI 规划镜头。")

        # 业务（stale / 已有 Shot 闸门 / 角色覆盖 / 创建 Shot[] / 一次 save）走共享 helper（§九）
        try:
            new_shots = apply_director_draft_business(store, project, scene_id, draft)
        except DirectorStaleError as e:
            raise HTTPException(409, str(e))
        except DirectorHasShotsError as e:
            raise HTTPException(409, str(e))
        except DirectorCoverageError as e:
            raise HTTPException(400, str(e))
        except ValueError as e:
            msg = str(e)
            if "不存在" in msg:
                raise HTTPException(404, msg)
            raise HTTPException(400, msg)

        # 删草稿（business helper 不删，由调用方决定）
        drafts = _read_drafts(project_dir)
        drafts.pop(scene_id, None)
        _write_drafts(project_dir, drafts)

        logger.info(f"AI 导演已应用: {project_name}/{scene_id}, 创建 {len(new_shots)} 镜头")
        return {
            "ok": True,
            "shots": [{"shot_id": sh.shot_id, "scene_id": sh.scene_id, "index": sh.index,
                       "description": sh.description, "characters": list(sh.characters),
                       "target_duration_s": sh.target_duration_s} for sh in new_shots],
            "first_new_shot_id": new_shots[0].shot_id if new_shots else None,
        }
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI 导演应用失败: {e}")
        raise HTTPException(500, str(e))
