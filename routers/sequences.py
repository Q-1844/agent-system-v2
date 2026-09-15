"""Sequence Assembly 路由（24.md §25 最小 API；§24 独立 router——Shot router 只属于 Shot）。

以现有 REST 风格提供等价能力：
  create / get / update / delete sequence
  render sequence（JobQueue kind="sequence_render"——显式触发，§22 No Auto Render）
  list render versions / select render version（§10 explicit select）
  get freshness（§20 纯读模型）

🔴 不做完整 Editor API、不做几十个 timeline operation（§25）。
🔴 Sequence 是 Shot Production 的消费者（§3）：本 router 只读 Shot.final_video，
   绝不写 Shot canonical / 不触发生成。
"""
import asyncio
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from deps import JOB_HANDLERS, get_job_queue, get_project_manager, logger

router = APIRouter()

# ---- SequenceError code → HTTP status 映射（fail closed 的结构化错误外显）----
_HTTP_NOT_FOUND = {"sequence_not_found", "render_version_not_found", "shot_not_found"}
_HTTP_CONFLICT = {"revision_conflict"}


def _http_error(e: Exception) -> HTTPException:
    from src.sequence.models import SequenceError
    if isinstance(e, SequenceError):
        if e.code in _HTTP_NOT_FOUND:
            return HTTPException(404, e.message)
        if e.code in _HTTP_CONFLICT:
            return HTTPException(409, e.message)
        return HTTPException(400, e.message)
    return HTTPException(500, str(e))


# ============ JobQueue handler（三阶段，24.md §23）============

async def _handler_sequence_render(job, emit):
    """sequence render（Phase 1 prepare → Phase 2 ffmpeg 线程 → Phase 3 commit）。
    长工作全部 asyncio.to_thread，不阻塞事件循环；不持 project write lock（24.md §23
    + 25.md §30）。有 AudioPlan → 方案 A 混音（base video copy）；无 → S1 语义。"""
    from src.sequence.service import SequenceService
    project_dir = get_project_manager().get_project_dir(job.project)
    svc = SequenceService(project_dir, job.project)
    sequence_id = (job.params or {}).get("sequence_id")
    version_id = (job.params or {}).get("render_version_id")
    if not sequence_id or not version_id:
        raise RuntimeError("sequence_render job 缺 sequence_id/render_version_id")

    await emit("prepare",
               f"{sequence_id} Phase1：源真相解析 + ffprobe + basis 快照"
               f"（含 AudioPlan 解析）", 5)
    prep = await asyncio.to_thread(svc.prepare_render, sequence_id, version_id)
    mix_note = (f"+{len(prep.resolved_audio)} 音频层混音" if prep.has_layers
                else "无 AudioPlan（S1 语义）")
    await emit("render",
               f"{sequence_id} Phase2：ffmpeg 渲染 {len(prep.timeline.clips)} clips"
               f"（{mix_note}，计划 {prep.timeline.total_duration_ms}ms）", 20)
    output = await asyncio.to_thread(svc.execute_render, sequence_id, version_id, prep)
    await emit("commit", f"{sequence_id} Phase3：recompute basis + 输出校验"
                         f"{' + 峰值检查' if prep.has_layers else ''}", 90)
    result = await asyncio.to_thread(svc.commit_render, sequence_id, version_id)
    peak_note = (f"，⚠ {result['audio_peak_warning']['max_volume_db']}dB 峰值告警"
                 if result.get("audio_peak_warning") else "")
    await emit("done",
               f"{sequence_id} 完成（{result['duration_ms']}ms，"
               f"Δ{result['duration_delta_ms']:+d}ms{peak_note}）: {output}", 100)


# 模块加载即注册（早于 startup；compose handler 同先例）
JOB_HANDLERS["sequence_render"] = _handler_sequence_render


# ============ 请求模型 ============

class ClipItem(BaseModel):
    shot_id: str
    # trim 合法性由域层统一校验（SequenceError.trim_invalid → 400 fail closed，
    # 不用 Pydantic ge 抢先——保证负值/非整数的错误口径与 service 单测一致）
    trim_in_ms: int = 0
    trim_out_ms: int = 0


class CreateSequenceRequest(BaseModel):
    title: str = ""
    scene_id: Optional[str] = None
    clips: List[ClipItem]


class UpdateSequenceRequest(BaseModel):
    title: Optional[str] = None
    scene_id: Optional[str] = None
    clips: Optional[List[ClipItem]] = None      # None = 不改 clips
    expected_revision: Optional[str] = None     # OCC 门（可选）


class SelectRenderRequest(BaseModel):
    version_id: str


class AudioLayerItem(BaseModel):
    """单层（25.md §7）。source 为引用对象（{kind, dialogue_id, audio_version_id}
    或 {kind, file}）；媒体事实在 render Phase 1 以 ffprobe 校验。"""
    role: str
    source: dict
    start_ms: int
    duration_ms: int
    source_in_ms: int = 0
    gain_db: float = 0.0
    fade_in_ms: int = 0
    fade_out_ms: int = 0


class PutAudioPlanRequest(BaseModel):
    """25.md §37/§38：一个完整 AudioPlan PUT + OCC 足够第一版。
    layers=[] = 清空 plan（canonical 归一化 → S1 语义）。"""
    layers: List[AudioLayerItem]
    expected_revision: Optional[str] = None


def _clips_payload(clips: Optional[List[ClipItem]]) -> Optional[List[dict]]:
    if clips is None:
        return None
    return [{"shot_id": c.shot_id, "trim_in_ms": c.trim_in_ms,
             "trim_out_ms": c.trim_out_ms} for c in clips]


def _entry_detail(entry) -> dict:
    d = entry.block.to_dict()
    d["revision"] = entry.block.revision
    d["render_versions"] = [r.to_dict() for r in entry.render_versions]
    d["final_render"] = entry.final_render
    return d


def _service(project_name: str):
    from src.sequence.service import SequenceService
    return SequenceService(get_project_manager().get_project_dir(project_name), project_name)


# ============ canonical（§25 create/get/update/delete）============

@router.post("/api/project/{project_name}/sequences")
async def create_sequence(project_name: str, req: CreateSequenceRequest):
    """创建 SequenceBlock（只表达哪些 Shot/什么顺序/trim——§5，不复制 Shot 内容）。"""
    try:
        block = _service(project_name).create_sequence(
            title=req.title, scene_id=req.scene_id,
            clips=[c.model_dump() for c in req.clips])
        return {"sequence_id": block.sequence_id, "revision": block.revision,
                "block": block.to_dict()}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建 sequence 失败: {e}")
        raise _http_error(e)


@router.get("/api/project/{project_name}/sequences")
async def list_sequences(project_name: str):
    try:
        entries = _service(project_name).list_entries()
        return {"sequences": [
            {"sequence_id": sid, "title": e.block.title, "scene_id": e.block.scene_id,
             "revision": e.block.revision, "clip_count": len(e.block.clips),
             "render_count": len(e.render_versions), "final_render": e.final_render}
            for sid, e in entries.items()]}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        logger.error(f"列出 sequence 失败: {e}")
        raise _http_error(e)


@router.get("/api/project/{project_name}/sequences/{sequence_id}")
async def get_sequence(project_name: str, sequence_id: str):
    """detail：canonical + render versions 元数据 + final_render 指针。"""
    try:
        return {"sequence": _entry_detail(_service(project_name).get_entry(sequence_id))}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        raise _http_error(e)


@router.put("/api/project/{project_name}/sequences/{sequence_id}")
async def update_sequence(project_name: str, sequence_id: str, req: UpdateSequenceRequest):
    """更新 canonical（clips 整体替换，顺序即播放顺序；可选 OCC expected_revision）。"""
    try:
        block = _service(project_name).update_sequence(
            sequence_id, title=req.title, scene_id=req.scene_id,
            clips=_clips_payload(req.clips), expected_revision=req.expected_revision)
        return {"sequence_id": block.sequence_id, "revision": block.revision,
                "block": block.to_dict()}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        raise _http_error(e)


@router.delete("/api/project/{project_name}/sequences/{sequence_id}")
async def delete_sequence(project_name: str, sequence_id: str):
    try:
        return _service(project_name).delete_sequence(sequence_id)
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        raise _http_error(e)


# ============ render（§22 显式触发；§9/§10 版本化）============

@router.post("/api/project/{project_name}/sequences/{sequence_id}/render")
async def render_sequence(project_name: str, sequence_id: str):
    """显式 render：append 一条 queued render version + 入队 sequence_render job。
    🔴 不 auto render（§22）、不 auto select（§10）。"""
    try:
        svc = _service(project_name)
        rv = svc.create_render(sequence_id)
        try:
            job = get_job_queue().submit(project_name, "sequence_render", None,
                                         sequence_id=sequence_id,
                                         render_version_id=rv.version_id)
        except Exception:
            # 入队失败：行回标 failed，不留幽灵 queued
            svc._mark_failed(sequence_id, rv.version_id, "dispatch_failed",
                             "JobQueue 入队失败")
            raise
        return {"job_id": job.job_id, "sequence_id": sequence_id,
                "render_version_id": rv.version_id, "status": rv.status}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        raise _http_error(e)


@router.get("/api/project/{project_name}/sequences/{sequence_id}/renders")
async def list_renders(project_name: str, sequence_id: str):
    try:
        entry = _service(project_name).get_entry(sequence_id)
        return {"sequence_id": sequence_id, "final_render": entry.final_render,
                "render_versions": [r.to_dict() for r in entry.render_versions]}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        raise _http_error(e)


@router.post("/api/project/{project_name}/sequences/{sequence_id}/renders/select")
async def select_render(project_name: str, sequence_id: str, req: SelectRenderRequest):
    """显式 select final render（仅 done；§10 生成绝不自动覆盖正式结果）。"""
    try:
        entry = _service(project_name).select_render(sequence_id, req.version_id)
        return {"sequence_id": sequence_id, "final_render": entry.final_render}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        raise _http_error(e)


# ============ Audio Plan（25.md §7/§37/§38）============

@router.get("/api/project/{project_name}/sequences/{sequence_id}/audio-plan")
async def get_audio_plan(project_name: str, sequence_id: str):
    """读 AudioPlan（None = 无 plan → 渲染为 S1 语义，25.md §25）。"""
    try:
        plan = _service(project_name).get_audio_plan(sequence_id)
        return {"sequence_id": sequence_id,
                "audio_plan": plan.to_dict() if plan else None,
                "revision": plan.revision if plan else None}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        raise _http_error(e)


@router.put("/api/project/{project_name}/sequences/{sequence_id}/audio-plan")
async def put_audio_plan(project_name: str, sequence_id: str, req: PutAudioPlanRequest):
    """整体替换 AudioPlan（§37 最小能力；expected_revision OCC → 409，§38）。
    🔴 No Auto Render（§22）：PUT 只改 canonical，绝不触发渲染。"""
    try:
        plan = _service(project_name).put_audio_plan(
            sequence_id, [l.model_dump() for l in req.layers],
            expected_revision=req.expected_revision)
        return {"sequence_id": sequence_id,
                "audio_plan": plan.to_dict() if plan else None,
                "revision": plan.revision if plan else None}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        raise _http_error(e)


# ============ freshness（§20/§21 纯读；§22 No Auto Render）============

@router.get("/api/project/{project_name}/sequences/{sequence_id}/freshness")
async def sequence_freshness(project_name: str, sequence_id: str,
                             version_id: Optional[str] = None):
    """final render（或 ?version_id= 指定版本）的 freshness：
    current / stale（detail.mismatched_keys 逐键）/ untracked_legacy / missing。"""
    try:
        return _service(project_name).freshness(sequence_id, version_id)
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except Exception as e:
        raise _http_error(e)
