"""分镜/镜头路由：shot 级管线 + job handlers（PHASE_5 §5.2 shots 组，最大）。

shot 级接口：audio/batch/split-dialogue/shots/timing/first-frame/choose/rehost/
last-frame/video/update-shot/orchestrate。
job handlers：first_frame/last_frame/video/audio（注册到 deps.JOB_HANDLERS，供 JobQueue 调用）。
"""
import asyncio
import json
import uuid
import base64 as _base64
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel

import deps
from deps import (
    logger, _APP_CFG, _AGNES_PROXY,
    JOB_HANDLERS, task_store, _new_task,
    get_agnes_client, get_project_manager, get_job_queue, get_rehost_host,
)

router = APIRouter()


# ==================== 阶段 2：配音前置（shot 级）====================
# 配音在视频之前：TTS 拿真实音频时长 -> plan_shot_timing 反推 num_frames -> 写回。
# 走 ProjectStore（v2 project.json）+ store.update_shot。
from typing import Any, List
import time as _time


class ShotAudioRequest(BaseModel):
    voice: str = "default"
    rate: str = "+0%"
    volume: str = "+0%"
    regenerate: bool = False


def _fps_from_config() -> int:
    try:
        return int((_APP_CFG.get("agnes") or {}).get("video", {}).get("frame_rate", 24))
    except Exception:
        return 24


def _audio_out_path(project_dir: Path, shot_id: str) -> Path:
    return project_dir / "output" / "audio" / f"{shot_id}.mp3"


def _apply_audio_to_shot(shot, res, timing, voice, rate):
    """sync mutator：把 TTS 结果 + 时长反推写回 shot（供 store.update_shot）。"""
    from src.core.models import ShotStatus
    if res is None:
        shot.audio.status = ShotStatus.SKIPPED.value
    else:
        shot.audio.local = res["path"]
        shot.audio.duration_ms = res["duration_ms"]
        shot.audio.words = res["words"]
        shot.audio.voice = voice
        shot.audio.rate = rate
        shot.audio.status = ShotStatus.DONE.value
        shot.audio.updated_at = _time.time()
    # 🔴 视频参数在这一步定死（配音前置的关键）
    shot.video.num_frames = timing.num_frames
    shot.video.frame_rate = timing.frame_rate


async def _gen_one_audio(tts, project_dir, shot_id, dialogue, voice, rate, volume, fps):
    """单镜 TTS（并发用），返回 (shot_id, res_or_None, timing, error_or_None)。"""
    from src.core.timing import plan_shot_timing
    try:
        if not dialogue:
            return (shot_id, None, plan_shot_timing(0, fps), None)
        out = _audio_out_path(Path(project_dir), shot_id)
        res = await tts.synthesize_full(dialogue, voice=voice, rate=rate, volume=volume, out_path=out)
        timing = plan_shot_timing(res["duration_ms"] / 1000, fps)
        return (shot_id, res, timing, None)
    except Exception as e:
        return (shot_id, None, None, str(e))


@router.post("/api/project/{project_name}/shot/{shot_id}/audio")
async def shot_audio(project_name: str, shot_id: str, req: ShotAudioRequest,
                     background_tasks: BackgroundTasks):
    """单镜配音（TTS -> 反推 num_frames -> 写回 audio+video 参数）"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        if ProjectStore(project_dir).load().get_shot(shot_id) is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        task_id = _new_task("audio")
        background_tasks.add_task(
            _shot_audio_bg, task_id, str(project_dir), shot_id,
            req.voice, req.rate, req.volume
        )
        return {"task_id": task_id, "shot_id": shot_id}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


async def _shot_audio_bg(task_id, project_dir, shot_id, voice, rate, volume):
    from src.core.store import ProjectStore
    from src.api.edge_tts_client import EdgeTTSClient
    task_store[task_id]["status"] = "running"
    try:
        fps = _fps_from_config()
        store = ProjectStore(Path(project_dir))
        shot = store.load().get_shot(shot_id)
        tts = EdgeTTSClient()
        _sid, res, timing, err = await _gen_one_audio(
            tts, project_dir, shot_id, shot.dialogue, voice, rate, volume, fps)
        if err:
            raise RuntimeError(err)
        store.update_shot(shot_id,
                          lambda sh: _apply_audio_to_shot(sh, res, timing, voice, rate))
        msg = f"镜头 {shot_id} 配音完成"
        if timing.needs_split:
            task_store[task_id]["warning"] = {
                "needs_split": True, "suggested_splits": timing.suggested_splits,
                "message": f"台词 {timing.audio_seconds:.1f}s 超单镜上限，建议拆为 {timing.suggested_splits} 个镜头"}
            msg += f"（⚠️ 建议拆为 {timing.suggested_splits} 镜）"
        task_store[task_id]["status"] = "completed"
        task_store[task_id]["result"] = {
            "shot_id": shot_id,
            "duration_ms": res["duration_ms"] if res else 0,
            "num_frames": timing.num_frames, "needs_split": timing.needs_split}
        task_store[task_id]["message"] = msg
    except Exception as e:
        task_store[task_id]["status"] = "failed"
        task_store[task_id]["message"] = f"失败: {e}"
        logger.error(f"镜头 {shot_id} 配音失败: {e}")


class BatchAudioRequest(BaseModel):
    voice: str = "default"
    rate: str = "+0%"
    volume: str = "+0%"
    scope: Any = "all"      # "all" | "pending" | [shot_id, ...]
    skip_locked: bool = True


@router.post("/api/project/{project_name}/audio/batch")
async def batch_audio(project_name: str, req: BatchAudioRequest,
                      background_tasks: BackgroundTasks):
    """批量配音（edge-tts 不占 Agnes 限流桶，并发 4，顺序写回避免丢失更新）"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        p = ProjectStore(project_dir).load()
        shots = p.all_shots()
        if req.scope == "pending":
            ids = [s.shot_id for s in shots if s.audio.status != "done"]
        elif isinstance(req.scope, list):
            ids = list(req.scope)
        else:
            ids = [s.shot_id for s in shots]
        if req.skip_locked:
            locked = {s.shot_id for s in shots if s.locked}
            ids = [i for i in ids if i not in locked]
        if not ids:
            return {"task_id": None, "message": "无待配音镜头", "count": 0}
        task_id = _new_task("audio")
        background_tasks.add_task(
            _batch_audio_bg, task_id, str(project_dir), ids, req.voice, req.rate, req.volume
        )
        return {"task_id": task_id, "count": len(ids), "shot_ids": ids}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


async def _batch_audio_bg(task_id, project_dir, shot_ids, voice, rate, volume):
    from src.core.store import ProjectStore
    from src.api.edge_tts_client import EdgeTTSClient
    task_store[task_id]["status"] = "running"
    try:
        fps = _fps_from_config()
        store = ProjectStore(Path(project_dir))
        id2dlg = {s.shot_id: s.dialogue for s in store.load().all_shots()}
        tts = EdgeTTSClient()
        sem = asyncio.Semaphore(4)

        async def one(sid):
            async with sem:
                return await _gen_one_audio(tts, project_dir, sid, id2dlg.get(sid, ""),
                                            voice, rate, volume, fps)

        results = await asyncio.gather(*(one(sid) for sid in shot_ids), return_exceptions=True)
        done = 0
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"批量配音某镜失败: {r}"); continue
            sid, res, timing, err = r
            if err:
                logger.error(f"镜头 {sid} 配音失败: {err}"); continue
            try:
                store.update_shot(
                    sid,
                    lambda sh, res=res, timing=timing: _apply_audio_to_shot(sh, res, timing, voice, rate))
                done += 1
            except Exception as e:
                logger.error(f"镜头 {sid} 写回失败: {e}")
            task_store[task_id]["progress"] = {"percent": done / len(shot_ids)}
        task_store[task_id]["status"] = "completed"
        task_store[task_id]["result"] = {"done": done, "total": len(shot_ids)}
        task_store[task_id]["message"] = f"批量配音完成 {done}/{len(shot_ids)}"
    except Exception as e:
        task_store[task_id]["status"] = "failed"
        task_store[task_id]["message"] = f"失败: {e}"
        logger.error(f"批量配音失败: {e}")


class SplitDialogueRequest(BaseModel):
    mode: str = "auto"
    segments: Optional[List[str]] = None


@router.post("/api/project/{project_name}/shot/{shot_id}/split-dialogue")
async def split_shot_dialogue(project_name: str, shot_id: str, req: SplitDialogueRequest):
    """拆超长台词为多个镜头：原 Shot 保留第1段，后续段插入同场次新 Shot，shot_id 顺移。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        from src.core.timing import split_dialogue as _split
        from src.core.models import Shot, ImageAsset, ShotStatus
        store = ProjectStore(project_dir)
        p = store.load()
        shot = p.get_shot(shot_id)
        if shot is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        if req.segments:
            segs = [s for s in req.segments if s and s.strip()]
        else:
            segs = _split(shot.dialogue or "")
        if len(segs) <= 1:
            return {"shot_id": shot_id, "split_into": 1, "message": "无需拆分"}
        scene = next((sc for sc in p.scenes if sc.scene_id == shot.scene_id), None)
        k = next((i for i, s in enumerate(scene.shots) if s.shot_id == shot_id), None)
        shot.dialogue = segs[0]
        # 🔴 拆分即同场景同角色动作延续（PHASE_3_EXECUTION_PLAN §8.1），硬编码 keyframes，
        #    不走 auto_link_mode 通用启发式。O 与所有新镜头 link_mode=keyframes。
        shot.link_mode = "keyframes"
        new_shots = []
        for seg in segs[1:]:
            new_shots.append(Shot(
                shot_id=f"{shot.scene_id}_cTMP", scene_id=shot.scene_id, index=0,
                description=shot.description, prompt_en=shot.prompt_en,
                negative_prompt=shot.negative_prompt, shot_size=shot.shot_size,
                camera=shot.camera, lighting=shot.lighting, motion=shot.motion,
                characters=list(shot.characters), dialogue=seg, link_mode="keyframes",
                seed=shot.seed, first_frame=ImageAsset(), last_frame=ImageAsset(),
                status=ShotStatus.PENDING.value,
            ))
        scene.shots = scene.shots[:k] + [shot] + new_shots + scene.shots[k + 1:]
        for i, sh in enumerate(scene.shots):
            sh.index = i + 1
            sh.shot_id = f"{scene.scene_id}_c{i + 1:02d}"
        # 🔴 split_next 链：seg_i.split_next = seg_{i+1}.shot_id（用重排后的最终 shot_id）
        split_chain = scene.shots[k:k + len(segs)]   # [O, new1, ..., newN-1]
        for i in range(len(split_chain) - 1):
            split_chain[i].split_next = split_chain[i + 1].shot_id
        split_chain[-1].split_next = None
        store.save(p)
        # 🔴 O 已有首帧（url_ready）时立即入队 O 的尾帧（生成后桥接拷贝给 O2 首帧，保证 O->O2 无缝）；
        #    O 还没首帧则不入队（orchestrator 顺序保证 first_frame -> last_frame）。
        enqueued = None
        if shot.first_frame.url_ready and deps.job_queue is not None:
            enqueued = deps.job_queue.submit(project_name, "last_frame", shot.shot_id).job_id
        return {"split_into": len(segs),
                "shot_ids": [sh.shot_id for sh in split_chain],
                "link_mode": "keyframes",
                "enqueued_last_frame": enqueued}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/api/project/{project_name}/shots")
async def get_project_shots(project_name: str):
    """返回 v2 项目结构（scenes/shots 含 first_frame/last_frame/audio/video 产物）。
    供前端 shot 级页面加载。local 路径前端用 media.ts 转 URL；url 是 Agnes 公网 URL。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        p = ProjectStore(project_dir).load()
        from dataclasses import asdict
        return {
            "name": p.name,
            "style": asdict(p.style),
            "story_text": p.story_text,
            "characters": [{"name": c.name, "description_en": c.description_en,
                            "views": {k: asdict(v) for k, v in c.views.items()}}
                           for c in p.characters],
            "scenes": [s.to_dict() for s in p.scenes],
        }
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/api/project/{project_name}/timing")
async def get_project_timing(project_name: str):
    """时长预览：每镜 audio_seconds/planned_seconds/needs_split + 全片总时长"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        from src.core.timing import plan_shot_timing
        p = ProjectStore(project_dir).load()
        fps = _fps_from_config()
        out = []
        total = 0.0
        for sh in p.all_shots():
            audio_sec = (sh.audio.duration_ms / 1000) if sh.audio.duration_ms else 0.0
            if sh.video.num_frames and sh.video.frame_rate:
                planned = sh.video.num_frames / sh.video.frame_rate
                needs_split = False
            else:
                t = plan_shot_timing(audio_sec, fps)
                planned = t.planned_seconds
                needs_split = t.needs_split
            out.append({"shot_id": sh.shot_id, "scene_id": sh.scene_id,
                        "audio_seconds": round(audio_sec, 3),
                        "planned_seconds": round(planned, 3),
                        "num_frames": sh.video.num_frames,
                        "needs_split": needs_split,
                        "dialogue": sh.dialogue[:40]})
            total += planned
        return {"shots": out, "total_seconds": round(total, 3), "fps": fps}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ==================== 阶段 3：分镜图 / 尾帧（shot 级，走 JobQueue）====================
# first_frame：2 候选图生图（角色参考图用公网 url 或 base64），存 local+公网 url，
#   status=pending 等用户点选；choose 后 status=done，url_ready 才 True（视频唯一入口）。
# last_frame：仅 keyframes，图生图用 first_frame.url；生成后若 split_next 非空，桥接拷贝给后继首帧。
from src.core.prompt_builder import build_first_frame_prompt, build_last_frame_prompt
from src.generation.image_adapter import (
    BoundGenerationError,
    build_bound_first_frame_input,
    build_bound_last_frame_input,
    shot_has_binding,
)
from src.generation.video_adapter import build_bound_video_input


def _shot_char_refs(project, shot):
    """取出场角色参考图，返回可送图片接口的 ref 列表（公网 URL 或 data:image URI）。
    都没有返回 []（调用方走文生图）。图片接口支持 base64（FACTS §2.4），无需 ImageHost。"""
    refs = []
    for name in shot.characters:
        c = project.get_character(name)
        if not c:
            continue
        url = c.ref_url()                       # 优先 front/side/back 的公网 url
        if url:
            refs.append(url); continue
        for v in ("front", "side", "back"):     # 只有 local -> base64 data URI
            a = c.views.get(v)
            if a and a.local and Path(str(a.local)).exists():
                b64 = _base64.b64encode(Path(str(a.local)).read_bytes()).decode()
                refs.append(f"data:image/png;base64,{b64}")
                break
    return refs


# ==================== Phase 6A：镜头接力 / 开场来源 ====================
# opening_source 与 link_mode 独立：link_mode 控制「镜头内部如何生成视频」，
# opening_source 控制「开场画面从哪来」。参考图收集按来源类型组织，便于未来加 environment/prop。
def _collect_first_frame_refs(project, shot):
    """收集首帧参考图（带来源类型）。本轮实现 character + continuity。
    返回 [{'type','url','label'}, ...]。未来加 environment/prop 在此追加，不改首帧生成入口。
    - character: 出场角色参考图（公网 url 或 base64 data URI）
    - continuity: reference_previous 模式下，来源镜头实际视频末帧（url 或 base64，图片生成参考可不托管）"""
    from src.core.models import OpeningSource
    refs = []
    for name in shot.characters:
        c = project.get_character(name)
        if not c:
            continue
        url = c.ref_url()
        if url:
            refs.append({"type": "character", "url": url, "label": name}); continue
        for v in ("front", "side", "back"):
            a = c.views.get(v)
            if a and a.local and Path(str(a.local)).exists():
                b64 = _base64.b64encode(Path(str(a.local)).read_bytes()).decode()
                refs.append({"type": "character", "url": f"data:image/png;base64,{b64}", "label": name}); break
    if shot.opening_source == OpeningSource.REFERENCE_PREVIOUS.value and shot.source_shot_id:
        prev = project.get_shot(shot.source_shot_id)
        if prev:
            end = prev.video_end_frame
            if end.url:
                refs.append({"type": "continuity", "url": end.url, "label": f"{prev.shot_id}末帧"})
            elif end.local and Path(str(end.local)).exists():
                b64 = _base64.b64encode(Path(str(end.local)).read_bytes()).decode()
                refs.append({"type": "continuity", "url": f"data:image/png;base64,{b64}", "label": f"{prev.shot_id}末帧"})
    return refs


def _effective_first_frame_url(project, shot):
    """effective 首帧公网 URL（视频接口用，单一解析点，避免前后端不一致）。
    inherit_previous -> 来源镜头实际末帧 url；其余 -> 本镜已选首帧 url。不可用返回 ''。"""
    from src.core.models import OpeningSource
    if shot.opening_source == OpeningSource.INHERIT_PREVIOUS.value and shot.source_shot_id:
        prev = project.get_shot(shot.source_shot_id)
        if prev and prev.video_end_frame.url_ready:
            return prev.video_end_frame.url or ""
        return ""
    sel = shot.first_frame.selected
    return (sel.url if sel else "") or ""


def _effective_first_frame_ready(project, shot):
    return bool(_effective_first_frame_url(project, shot))


def continuity_status(project, shot):
    """接力状态：(status, message)。status: independent/ready/stale/broken。
    不静默替换下游、不自动连锁重生成，仅标记。"""
    from src.core.models import OpeningSource, ShotStatus
    if shot.opening_source == OpeningSource.INDEPENDENT.value or not shot.source_shot_id:
        return ("independent", "")
    prev = project.get_shot(shot.source_shot_id)
    if not prev:
        return ("broken", "来源镜头已删除")
    if prev.scene_id != shot.scene_id:
        return ("broken", "来源镜头不在同一场景")
    scene = next((s for s in project.scenes if s.scene_id == shot.scene_id), None)
    if scene:
        ids = [s.shot_id for s in scene.shots]
        if prev.shot_id in ids and shot.shot_id in ids and ids.index(prev.shot_id) >= ids.index(shot.shot_id):
            return ("broken", "来源镜头不再位于本镜之前")
    if not prev.final_video or prev.video.status != ShotStatus.DONE.value:
        return ("broken", "上一镜头未完成视频")
    end = prev.video_end_frame
    if shot.opening_source == OpeningSource.INHERIT_PREVIOUS.value:
        if not end.url_ready:
            return ("broken", "上一镜头末帧未就绪（无公网 URL）")
    else:  # reference_previous：local 即可（base64 可作图片生成参考）
        if not (end.status == ShotStatus.DONE.value and end.local):
            return ("broken", "上一镜头末帧未就绪")
    if shot.source_version != prev.final_video:
        return ("stale", "上一镜头已更新")
    return ("ready", "")


async def _extract_video_end_frame(project_dir, shot_id, video_local, version_id, emit=None):
    """从最终视频文件提取实际末帧 -> output/frames/{shot_id}_video_end.png，
    上传 imgbb 取公网 URL（视频接力进 Agnes 视频接口必需；保真不用 agnes_rehost）。
    提取/托管失败不抛（视频本身仍成功），返回带 error 的 Asset；允许 retry。"""
    import subprocess
    from src.core.models import Asset, ShotStatus
    frames_dir = project_dir / "output" / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    end_path = frames_dir / f"{shot_id}_video_end.png"
    # ffmpeg -sseof 从文件末尾 seek，取 1 帧（短末尾偏移避免空帧）
    cmd = ["ffmpeg", "-y", "-sseof", "-0.2", "-i", str(video_local),
           "-frames:v", "1", "-q:v", "2", str(end_path)]
    try:
        if emit:
            await emit("endframe", f"{shot_id} 提取视频末帧", None)
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=60)
        if r.returncode != 0 or not end_path.exists():
            return Asset(status=ShotStatus.FAILED.value,
                         error=f"ffmpeg 末帧提取失败: {(r.stderr or '')[:200]}"), False
    except Exception as e:
        return Asset(status=ShotStatus.FAILED.value, error=f"ffmpeg 异常: {e}"), False
    # 上传 imgbb 取公网 URL（inherit 接力进视频接口必需；reference 可走 base64）
    try:
        url = await get_rehost_host().upload(str(end_path))
        return Asset(local=str(end_path), url=url, status=ShotStatus.DONE.value), True
    except Exception as e:
        # 提取成功但托管失败：local 有（reference 可 base64）、url 无（inherit 不可用）
        return Asset(local=str(end_path), status=ShotStatus.DONE.value,
                     error=f"末帧托管失败: {e}"), False


async def _await_first_frame_url(store, shot_id, emit, timeout=180, interval=5):
    """有界等待 effective 首帧公网 URL 就绪。
    independent / reference_previous：本镜已选首帧候选 url；
    inherit_previous：来源镜头视频末帧 url（本镜 first_frame 为空，须走 effective 解析，
      否则永远等不到 first_frame.url_ready，导致尾帧生成超时失败）。
    超时返回 None（调用方报错）。"""
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        project = store.load()
        sh = project.get_shot(shot_id)
        if sh:
            url = _effective_first_frame_url(project, sh)
            if url:
                return url
        await asyncio.sleep(interval)
    return None


async def _handler_first_frame(job, emit):
    """生成 N 候选分镜图 -> 新首帧批次（append）+ 同步旧 first_frame（过渡兼容）。status=pending 等用户选。"""
    from src.core.store import ProjectStore
    from src.core.models import Asset, Candidate, ImageBatch, ShotStatus
    project_dir = get_project_manager().get_project_dir(job.project)
    store = ProjectStore(project_dir)
    project = store.load()
    shot = project.get_shot(job.shot_id)
    if shot is None:
        raise RuntimeError(f"镜头 {job.shot_id} 不存在")
    force = bool(job.params.get("force", False))
    if shot.locked and not force:
        await emit("skip", f"{shot.shot_id} 已锁定，跳过", 100); return
    # 批次：每次生成创建新批次（append，多批次历史），不再因"已有"跳过

    variants = max(1, int(job.params.get("variants", 2)))
    size_tier = project.style.size_tier or "1K"
    ratio = project.style.aspect_ratio or "16:9"
    # S3C.1 双路径（§27）：BOUND shot 必须走 Binding-aware path（READY→Pack→Adapter；
    # NOT_READY→BLOCK，绝不 fallback legacy）；UNBOUND shot 保持 legacy 行为逐字节不变。
    bound = None
    if shot_has_binding(project_dir, shot.shot_id):
        bound = build_bound_first_frame_input(project_dir, project, shot)
        refs = bound.image_refs
        prompt = bound.prompt
    else:
        typed_refs = _collect_first_frame_refs(project, shot)
        refs = [r["url"] for r in typed_refs]
        prompt = build_first_frame_prompt(project, shot)
        # reference_previous：提示词表达「保持连续但允许换机位」，不要求逐像素复制
        if any(r["type"] == "continuity" for r in typed_refs):
            prompt += (". Maintain continuity with the previous shot's ending frame: keep character identity, "
                       "environment, outfit and lighting direction; change shot size, camera angle, composition "
                       "and subject action as specified; do not pixel-copy the previous frame")
    use_i2i = bool(refs)
    frames_dir = project_dir / "output" / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    batch_id = f"ff_{uuid.uuid4().hex[:8]}"
    now = _time.time()
    cand_list = []
    asset_list = []
    for i in range(variants):
        await emit("gen", f"{shot.shot_id} 生成候选 {i+1}/{variants}", int(i / variants * 100))
        # 🔴 图片接口不支持 seed（AGNES_API_FACTS §2.1 / 提示词.md §4.1）：多候选不靠 seed 区分
        res = await get_agnes_client().generate_image(
            prompt=prompt, image_refs=refs if use_i2i else None,
            size_tier=size_tier, ratio=ratio)
        url = res.get("url")
        if not url:
            raise RuntimeError("图片生成未返回公网 URL")
        dest = frames_dir / f"{shot.shot_id}_first_v{i+1}.png"
        await get_agnes_client().download(url, dest)   # 无认证 client，复用于图片资产
        cid = f"{batch_id}_{i+1}"
        cand_list.append(Candidate(candidate_id=cid, local=str(dest), url=url,
                                   status=ShotStatus.DONE.value, created_at=now))
        asset_list.append(Asset(local=str(dest), url=url, status=ShotStatus.DONE.value))
        await emit("gen", f"{shot.shot_id} 候选 {i+1} 完成", int((i + 1) / variants * 100))

    char_snap = {name: {"url": project.get_character(name).ref_url()}
                 for name in shot.characters if project.get_character(name)}
    new_batch = ImageBatch(batch_id=batch_id, kind="first_frame", created_at=now,
                           status=ShotStatus.PENDING.value, prompt_snapshot=prompt,
                           char_ref_snapshot=char_snap, candidates=cand_list,
                           origin_run_id=job.params.get("origin_run_id"),
                           generation_basis=dict(bound.basis) if bound else {})

    def mut(s):
        s.first_frame_batches.append(new_batch)
        s.selected_first_frame = cand_list[0].candidate_id  # 自动选新批次第一候选，生成后右侧预览即时显示
        # 同步旧 first_frame（管线 preflight 读旧字段，过渡兼容）
        s.first_frame.candidates = asset_list
        s.first_frame.selected_index = 0
        s.first_frame.status = ShotStatus.PENDING.value
        s.first_frame.error = None
        s.first_frame.updated_at = now
    store.update_shot(job.shot_id, mut)
    await emit("done", f"{shot.shot_id} 已生成 {len(cand_list)} 个候选（批次 {batch_id}），待选择", 100)


async def _handler_last_frame(job, emit):
    """生成尾帧（仅 keyframes，图生图用 first_frame.url）。
    生成后若 split_next 非空，桥接拷贝给后继首帧（O2.first_frame = O.last_frame，保证 O->O2 无缝）。"""
    from src.core.store import ProjectStore
    from src.core.models import Asset, ImageAsset, Candidate, ImageBatch, ShotStatus
    project_dir = get_project_manager().get_project_dir(job.project)
    store = ProjectStore(project_dir)
    project = store.load()
    shot = project.get_shot(job.shot_id)
    if shot is None:
        raise RuntimeError(f"镜头 {job.shot_id} 不存在")
    if shot.link_mode != "keyframes":
        raise RuntimeError(f"仅 keyframes 模式需要尾帧（当前 {shot.link_mode}）")

    # 有界等待 first_frame.url_ready（bridge 场景首帧来自前驱尾帧，可能还没好）
    await emit("wait", f"{shot.shot_id} 等待首帧就绪", 5)
    ff_url = await _await_first_frame_url(store, job.shot_id, emit)
    if not ff_url:
        raise RuntimeError("首帧缺少公网 URL（未选择候选或未生成），无法生成尾帧")

    await emit("gen", f"{shot.shot_id} 生成尾帧", 20)
    # S3C.2 双路径（§21）：BOUND 尾帧走 Binding-aware path（READY→Pack→Adapter：
    # last-frame temporal 规则 end/throughout/static 进、start 排除；构图基础首帧
    # freshness fail closed；绝不 legacy fallback）；UNBOUND 保持 legacy 行为逐字节不变。
    bound_last = None
    if shot_has_binding(project_dir, shot.shot_id):
        bound_last = build_bound_last_frame_input(project_dir, project, shot,
                                                  first_frame_url=ff_url)
        prompt = bound_last.prompt
        refs = bound_last.image_refs
    else:
        prompt = build_last_frame_prompt(project, shot)
        # image_refs 顺序（提示词.md §11）：1.已选首帧（场景/构图基础）2.角色参考图（身份保持）。
        # 无角色时 char_refs 为空，只传首帧。首帧始终第一张，不让角色图取代主构图参考。
        char_refs = _shot_char_refs(project, shot)
        refs = [ff_url] + char_refs
    res = await get_agnes_client().generate_image(
        prompt=prompt, image_refs=refs,
        size_tier=project.style.size_tier or "1K", ratio=project.style.aspect_ratio or "16:9")
    url = res.get("url")
    if not url:
        raise RuntimeError("尾帧生成未返回公网 URL")
    dest = project_dir / "output" / "frames" / f"{shot.shot_id}_last.png"
    await get_agnes_client().download(url, dest)
    # 写尾帧批次（基于当前 selected_first_frame）+ 设 selected_last_frame + 同步旧 last_frame
    batch_id = f"lf_{uuid.uuid4().hex[:8]}"
    now = _time.time()
    cand = Candidate(candidate_id=f"{batch_id}_1", local=str(dest), url=url,
                     status=ShotStatus.DONE.value, created_at=now)
    char_snap = {name: {"url": project.get_character(name).ref_url()}
                 for name in shot.characters if project.get_character(name)}
    new_batch = ImageBatch(batch_id=batch_id, kind="last_frame", created_at=now,
                           status=ShotStatus.DONE.value, prompt_snapshot=prompt,
                           char_ref_snapshot=char_snap,
                           based_on_first_frame=shot.selected_first_frame, candidates=[cand],
                           origin_run_id=job.params.get("origin_run_id"),
                           generation_basis=dict(bound_last.basis) if bound_last else {})
    def mut(s, b=new_batch, cid=cand.candidate_id, d=str(dest), u=url):
        s.last_frame_batches.append(b)
        s.selected_last_frame = cid
        s.last_frame.mark_done(d, u)   # 同步旧字段（管线 preflight 读旧）
    store.update_shot(job.shot_id, mut)
    await emit("done", f"{shot.shot_id} 尾帧完成（批次 {batch_id}）", 90)

    # 🔴 桥接拷贝：split_next 非空且后继首帧未就绪时，把尾帧拷给后继首帧
    nxt = getattr(shot, "split_next", None)
    if nxt:
        succ = project.get_shot(nxt)
        if succ is not None and not succ.first_frame.ready:
            def mut_succ(s, d=str(dest), u=url):
                s.first_frame = ImageAsset(
                    candidates=[Asset(local=d, url=u, status=ShotStatus.DONE.value)],
                    selected_index=0, status=ShotStatus.DONE.value)
            store.update_shot(nxt, mut_succ)
            await emit("bridge", f"{shot.shot_id} 尾帧桥接为 {nxt} 首帧", 100)
        else:
            await emit("done", f"{shot.shot_id} 尾帧完成（后继 {nxt} 已有首帧，不覆盖）", 100)
    else:
        await emit("done", f"{shot.shot_id} 尾帧完成", 100)


JOB_HANDLERS["first_frame"] = _handler_first_frame
JOB_HANDLERS["last_frame"] = _handler_last_frame


class FirstFrameRequest(BaseModel):
    variants: int = 2
    force: bool = False


class ChooseFrameRequest(BaseModel):
    candidate_id: str   # 跨批次选择（支持从旧批次选，GPT §四）


@router.post("/api/project/{project_name}/shot/{shot_id}/first-frame")
async def shot_first_frame(project_name: str, shot_id: str, req: FirstFrameRequest):
    """生成单镜头分镜图候选（入队 first_frame 任务，进度走全局 SSE）"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        if ProjectStore(project_dir).load().get_shot(shot_id) is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        q = get_job_queue()
        job = q.submit(project_name, "first_frame", shot_id,
                       variants=req.variants, force=req.force)
        return {"job_id": job.job_id, "shot_id": shot_id}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/project/{project_name}/shot/{shot_id}/first-frame/choose")
async def choose_first_frame(project_name: str, shot_id: str, req: ChooseFrameRequest):
    """选择分镜图候选：设 selected_index + first_frame.status=done（url_ready=True，放行视频）"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        from src.core.models import ShotStatus
        store = ProjectStore(project_dir)
        shot = store.load().get_shot(shot_id)
        if shot is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        if not shot.first_frame_batches:
            raise HTTPException(400, "该镜头尚无首帧批次，请先生成")
        # 跨所有批次找候选（支持从旧批次选择）
        found_batch = None
        found_idx = -1
        for b in shot.first_frame_batches:
            for i, c in enumerate(b.candidates):
                if c.candidate_id == req.candidate_id:
                    found_batch, found_idx = b, i
                    break
            if found_batch:
                break
        if found_batch is None:
            raise HTTPException(400, f"候选 {req.candidate_id} 不存在")
        cand = found_batch.candidates[found_idx]
        def mut(s, b=found_batch, i=found_idx, cid=req.candidate_id):
            s.selected_first_frame = cid
            # 同步旧 first_frame 到所选批次（管线 preflight 读旧字段，过渡兼容）
            from src.core.models import Asset
            s.first_frame.candidates = [Asset(local=c.local, url=c.url, status=c.status) for c in b.candidates]
            s.first_frame.selected_index = i
            setattr(s.first_frame, "status", ShotStatus.DONE.value)
        store.update_shot(shot_id, mut)
        return {"shot_id": shot_id, "candidate_id": req.candidate_id, "url": cand.url}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/project/{project_name}/shot/{shot_id}/last-frame/choose")
async def choose_last_frame(project_name: str, shot_id: str, req: ChooseFrameRequest):
    """选择尾帧候选：设 selected_last_frame + 同步旧 last_frame（管线 preflight 读旧字段，过渡兼容）。
    跨所有批次找候选（支持从旧批次切回）。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        from src.core.models import ShotStatus, Asset
        store = ProjectStore(project_dir)
        shot = store.load().get_shot(shot_id)
        if shot is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        if not shot.last_frame_batches:
            raise HTTPException(400, "该镜头尚无尾帧批次，请先生成")
        found_batch = None
        found_idx = -1
        for b in shot.last_frame_batches:
            for i, c in enumerate(b.candidates):
                if c.candidate_id == req.candidate_id:
                    found_batch, found_idx = b, i
                    break
            if found_batch:
                break
        if found_batch is None:
            raise HTTPException(400, f"候选 {req.candidate_id} 不存在")
        cand = found_batch.candidates[found_idx]
        def mut(s, b=found_batch, i=found_idx, cid=req.candidate_id):
            s.selected_last_frame = cid
            s.last_frame.candidates = [Asset(local=c.local, url=c.url, status=c.status) for c in b.candidates]
            s.last_frame.selected_index = i
            setattr(s.last_frame, "status", ShotStatus.DONE.value)
        store.update_shot(shot_id, mut)
        return {"shot_id": shot_id, "candidate_id": req.candidate_id, "url": cand.url}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/project/{project_name}/shot/{shot_id}/first-frame/rehost")
async def rehost_first_frame(project_name: str, shot_id: str):
    """重新托管首帧：local 有 / url 空 -> imgbb 上传 -> 回填 url + status=done。
    100% 保真，不重新生成、不丢画面。用于 v1 迁移的本地首帧补公网 URL（送进视频接口）。
    幂等：已有 url 直接返回 rehosted=False。同步调用（几秒），不入队、不占 video 名额。
    🔴 imgbb 图为公开图（拿到链接即可访问）。上传失败明示抛错，不静默回落到重新生成。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        from src.core.models import ShotStatus
        store = ProjectStore(project_dir)
        shot = store.load().get_shot(shot_id)
        if shot is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        sel = shot.first_frame.selected
        if sel is None or not sel.local:
            raise HTTPException(400, "首帧无本地图，无法托管（请先生成首帧）")
        if sel.url:
            return {"shot_id": shot_id, "url": sel.url, "rehosted": False}
        url = await get_rehost_host().upload(sel.local)
        idx = shot.first_frame.selected_index
        def mut(s, u=url, i=idx):
            if 0 <= i < len(s.first_frame.candidates):
                s.first_frame.candidates[i].url = u
                s.first_frame.candidates[i].status = ShotStatus.DONE.value
            s.first_frame.status = ShotStatus.DONE.value
            s.first_frame.updated_at = _time.time()
        store.update_shot(shot_id, mut)
        return {"shot_id": shot_id, "url": url, "rehosted": True}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/project/{project_name}/shot/{shot_id}/last-frame")
async def shot_last_frame(project_name: str, shot_id: str):
    """生成单镜头尾帧（仅 keyframes；入队 last_frame 任务）"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        shot = ProjectStore(project_dir).load().get_shot(shot_id)
        if shot is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        if shot.link_mode != "keyframes":
            raise HTTPException(400, f"仅 keyframes 模式需要尾帧（当前 {shot.link_mode}）")
        q = get_job_queue()
        job = q.submit(project_name, "last_frame", shot_id)
        return {"job_id": job.job_id, "shot_id": shot_id}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ==================== 提示词 AI 辅助（根据上下文生成 / 优化，只返回文本，不自动生成资产）====================
class PromptAIRequest(BaseModel):
    stage: str                     # "first" | "last" | "video"
    text: Optional[str] = None     # optimize 时必填（当前编辑器内容）


def _collect_prompt_context(project, shot, stage: str) -> dict:
    """按优先级收集当前阶段上下文（结构化、只读紧邻上下镜、不累积所有历史）。
    优先级：本镜意图(description) > 故事段 > 镜头位置 > 紧邻上一镜 > 紧邻下一镜 >
            风格画幅 > 角色 > opening/link/continuity > effective ff/首尾帧状态。"""
    scene = next((s for s in project.scenes if s.scene_id == shot.scene_id), None)
    shots_in_scene = scene.shots if scene else []
    idx = next((i for i, s in enumerate(shots_in_scene) if s.shot_id == shot.shot_id), -1)
    total = len(shots_in_scene)
    prev_shot = shots_in_scene[idx - 1] if idx > 0 else None
    next_shot = shots_in_scene[idx + 1] if 0 <= idx < total - 1 else None
    cstat, cmsg = continuity_status(project, shot)
    ctx = {
        "shot_intent": (shot.description or "").strip(),
        "scene_summary": (scene.summary if scene else "").strip(),
        "index": idx + 1 if idx >= 0 else 0,
        "total": total,
        "style": project.style.suffix(),
        "aspect_ratio": project.style.aspect_ratio,
        "characters": [],
        "opening_source": shot.opening_source,
        "link_mode": shot.link_mode,
        "continuity": cstat + (f"({cmsg})" if cmsg else ""),
    }
    for name in shot.characters:
        c = project.get_character(name)
        ctx["characters"].append({"name": name, "desc": c.description_en if c and c.description_en else ""})
    # 紧邻上一镜（意图 + 采用结果，截断不累积全文）
    if prev_shot:
        ctx["prev"] = {
            "shot_id": prev_shot.shot_id,
            "intent": (prev_shot.description or "").strip(),
            "first_frame_prompt": (prev_shot.prompt_en or "").strip()[:220],
            "last_frame_prompt": (prev_shot.last_frame_prompt_en or "").strip()[:160],
            "video_end_frame_ready": bool(prev_shot.video_end_frame and prev_shot.video_end_frame.url_ready),
        }
    # 紧邻下一镜（意图，避免当前镜提前演完下一镜）
    if next_shot:
        ctx["next"] = {"shot_id": next_shot.shot_id, "intent": (next_shot.description or "").strip()}
    if stage in ("last", "video"):
        ctx["first_frame_prompt"] = (shot.prompt_en or "").strip()
    if stage == "video":
        ctx["last_frame_prompt"] = (shot.last_frame_prompt_en or "").strip()
        # 实际会送 Agnes 的时长（与 video handler 一致）：target_duration_s > num_frames/fps > 默认 3 秒
        if shot.target_duration_s is not None:
            resolved_dur = float(shot.target_duration_s)
        elif shot.video.num_frames > 0:
            resolved_dur = shot.video.num_frames / max(shot.video.frame_rate, 1)
        else:
            resolved_dur = 3.0
        ctx["target_duration_s"] = round(resolved_dur, 1)
    return ctx


def _format_context(ctx: dict, stage: str) -> str:
    """把结构化 ctx 拼成简短文本块传给文本模型（避免越积越长）。"""
    lines = []
    if ctx.get("shot_intent"):
        lines.append(f"本镜意图: {ctx['shot_intent']}")
    if ctx.get("scene_summary"):
        lines.append(f"故事段(背景): {ctx['scene_summary']}")
    if ctx.get("total"):
        lines.append(f"镜头位置: 第 {ctx['index']} 镜 / 共 {ctx['total']} 镜")
    p = ctx.get("prev")
    if p:
        parts = []
        if p["intent"]:
            parts.append(f"意图={p['intent']}")
        if p["first_frame_prompt"]:
            parts.append(f"首帧={p['first_frame_prompt']}")
        if p["video_end_frame_ready"]:
            parts.append("末帧就绪")
        lines.append(f"上一镜({p['shot_id']}): " + ("; ".join(parts) if parts else "无"))
    n = ctx.get("next")
    if n and n.get("intent"):
        lines.append(f"下一镜({n['shot_id']})意图: {n['intent']}")
    if ctx.get("characters"):
        lines.append("角色: " + ", ".join(f"{c['name']}({c['desc']})" if c['desc'] else c['name'] for c in ctx['characters']))
    if ctx.get("style") or ctx.get("aspect_ratio"):
        lines.append(f"风格: {ctx.get('style', '')}; 画幅: {ctx.get('aspect_ratio', '')}")
    lines.append(f"开场来源: {ctx.get('opening_source', '')}; 生成方式: {ctx.get('link_mode', '')}; 接力: {ctx.get('continuity', '')}")
    if stage in ("last", "video") and ctx.get("first_frame_prompt"):
        lines.append(f"本镜首帧提示词: {ctx['first_frame_prompt']}")
    if stage == "video":
        if ctx.get("last_frame_prompt"):
            lines.append(f"本镜尾帧提示词: {ctx['last_frame_prompt']}")
        dur = ctx.get("target_duration_s")
        lines.append(f"目标时长: {dur} 秒")
    return "\n".join(lines)


_FIRST_FRAME_SYSTEM_HAS_INTENT = (
    "You are a prompt engineer for an AI image generator. "
    "Write the English first-frame prompt for THIS shot based on the context. "
    "The frame MUST express the shot intent. Maintain continuity with the previous shot "
    "(character identity, environment, lighting) but do NOT repeat its framing or action. "
    "Do NOT anticipate the next shot's story. Keep project style and opening source consistent. "
    "Do not invent characters not in the context. Return ONLY the English prompt, no explanation."
)
_FIRST_FRAME_SYSTEM_INFER = (
    "You are a prompt engineer for an AI image generator. The shot intent is empty. "
    "Based on the scene, this shot's position, and the previous/next shots, infer what THIS shot should show "
    "and write the English first-frame prompt. Maintain continuity with the previous shot, do not repeat it. "
    "Do not anticipate the next shot. Do not invent characters not in the context. "
    'Return ONLY a JSON object: {"shot_intent": "<one sentence in Chinese, what this shot shows in the story>", '
    '"prompt": "<English first-frame prompt>"}. No extra text.'
)
_LAST_FRAME_SYSTEM = (
    "You are a prompt engineer for an AI image generator. "
    "Write the English last-frame prompt for THIS shot based on the shot intent, the first-frame prompt, "
    "and the next shot intent. Describe the ending state after completing this shot's action/change. "
    "Keep character and scene consistent with the first frame. Do NOT complete the next shot's intent. "
    "Return ONLY the English prompt, no explanation."
)
_VIDEO_SYSTEM = (
    "You are a prompt engineer for an AI video generator. "
    "Write the English video prompt describing ONLY the motion/action/camera movement/environment change "
    "between this shot's first frame and last frame. The target duration is given in context; "
    "scale the motion to fit this duration: short duration focuses on one clear action; long duration may include "
    "multiple sequential action phases WITHIN the same continuous shot, but do NOT switch scenes or produce multiple "
    "cuts or camera angles (one shot = one continuous take). "
    "Do NOT re-describe the whole scene or story. Do NOT repeat the previous shot's action. Do NOT anticipate the next shot. "
    "Keep character identity consistent. Return ONLY the English prompt, no explanation."
)
_PROMPT_OPT_SYSTEM = (
    "You are a prompt engineer. Refine the given English prompt: keep the original intent, "
    "improve clarity, remove redundancy and conflicting requirements, add stage-appropriate detail. "
    "Do not invent characters or elements not in the context. Do not change the story outcome. "
    "Return ONLY the refined English prompt, no explanation."
)


@router.post("/api/project/{project_name}/shot/{shot_id}/prompt/generate")
async def shot_prompt_generate(project_name: str, shot_id: str, req: PromptAIRequest):
    """根据上下文生成英文提示词（只返回文本，不自动生成资产）。
    首帧本镜意图空时返回 inferred_shot_intent 供前端确认；单镜故事段直接用故事段作意图不推断。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        project = ProjectStore(project_dir).load()
        shot = project.get_shot(shot_id)
        if shot is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        if req.stage not in ("first", "last", "video"):
            raise HTTPException(400, "stage 必须为 first/last/video")
        ctx = _collect_prompt_context(project, shot, req.stage)
        ctx_text = _format_context(ctx, req.stage)
        client = get_agnes_client()
        if req.stage == "first":
            has_intent = bool(ctx["shot_intent"])
            # 单镜故事段 + 无意图 -> 直接用故事段作意图，不推断
            if not has_intent and ctx["total"] == 1 and ctx["scene_summary"]:
                user = f"本镜意图(取自故事段): {ctx['scene_summary']}\n\nContext:\n{ctx_text}"
                en = await client.chat(
                    [{"role": "system", "content": _FIRST_FRAME_SYSTEM_HAS_INTENT},
                     {"role": "user", "content": user}],
                    temperature=0.7, max_tokens=512)
                return {"prompt": en.strip()}
            if has_intent:
                user = f"Stage: first frame\n\nContext:\n{ctx_text}"
                en = await client.chat(
                    [{"role": "system", "content": _FIRST_FRAME_SYSTEM_HAS_INTENT},
                     {"role": "user", "content": user}],
                    temperature=0.7, max_tokens=512)
                return {"prompt": en.strip()}
            # 无意图且多镜 -> 推断本镜意图，返回 inferred_shot_intent 供前端确认
            user = f"Stage: first frame (infer shot intent)\n\nContext:\n{ctx_text}"
            raw = await client.chat(
                [{"role": "system", "content": _FIRST_FRAME_SYSTEM_INFER},
                 {"role": "user", "content": user}],
                temperature=0.7, max_tokens=700, json_mode=True)
            import json as _json, re as _re
            m = _re.search(r'\{.*\}', raw, _re.DOTALL)
            if m:
                try:
                    obj = _json.loads(m.group(0))
                    return {"prompt": (obj.get("prompt") or "").strip(),
                            "inferred_shot_intent": (obj.get("shot_intent") or "").strip()}
                except Exception:
                    pass
            return {"prompt": raw.strip(), "inferred_shot_intent": ""}
        # 尾帧 / 视频
        system = _LAST_FRAME_SYSTEM if req.stage == "last" else _VIDEO_SYSTEM
        stage_desc = "last frame" if req.stage == "last" else "video"
        en = await client.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": f"Stage: {stage_desc}\n\nContext:\n{ctx_text}"}],
            temperature=0.7, max_tokens=512)
        return {"prompt": en.strip()}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/project/{project_name}/shot/{shot_id}/prompt/optimize")
async def shot_prompt_optimize(project_name: str, shot_id: str, req: PromptAIRequest):
    """优化当前英文提示词（只返回文本，不自动生成资产）"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        project = ProjectStore(project_dir).load()
        shot = project.get_shot(shot_id)
        if shot is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        if req.stage not in ("first", "last", "video"):
            raise HTTPException(400, "stage 必须为 first/last/video")
        text = (req.text or "").strip()
        if not text:
            raise HTTPException(400, "优化需要当前提示词内容（text 不能为空）")
        ctx = _format_context(_collect_prompt_context(project, shot, req.stage), req.stage)
        en = await get_agnes_client().chat(
            [{"role": "system", "content": _PROMPT_OPT_SYSTEM},
             {"role": "user", "content": f"Current prompt:\n{text}\n\nContext:\n{ctx}"}],
            temperature=0.5, max_tokens=512)
        return {"prompt": en.strip()}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ==================== 阶段 3：镜头视频生成（shot 级，走 JobQueue 串行视频 worker）====================
from src.api.agnes_client import resolve_size as _resolve_size, PollTransientError, AgnesError


def _video_preflight(shot, quota, seconds, eff_ff_ready):
    """视频前置硬校验（缺一不可，不通过就不提交，省 60s）。
    仅检查「硬失败」；keyframes 尾帧 in-progress 的有界等待在 handler 里做。
    eff_ff_ready：effective 首帧是否就绪（inherit_previous 用来源末帧 url，其余用本镜已选首帧 url）。"""
    from src.core.models import ShotStatus
    if shot.locked:
        return False, "镜头已锁定"
    if not shot.video.num_frames:
        return False, "未设置视频时长"
    if not eff_ff_ready:
        return False, "开场画面未就绪：请先生成并选择首帧，或来源镜头末帧未就绪"
    if shot.link_mode == "keyframes":
        if shot.last_frame.status == ShotStatus.FAILED.value:
            return False, "尾帧生成失败，请重新生成尾帧"
    if quota:
        ok, why = quota.can("video_seconds", seconds)
        if not ok:
            return False, f"配额不足: {why}"
    return True, ""


async def _await_last_frame_url(store, shot_id, emit, timeout=180, interval=5):
    """有界等待 last_frame.url_ready（keyframes 尾帧可能还在生成）。超时返回 None。"""
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        sh = store.load().get_shot(shot_id)
        if sh and sh.last_frame.url_ready:
            return sh.last_frame.selected.url
        await asyncio.sleep(interval)
    return None


async def _handler_video(job, emit):
    """镜头视频生成（PHASE_3 §3.4）。
    preflight -> keyframes 等尾帧 -> 提交立刻落库 video_id -> 轮询 -> 下载 -> 回读秒数/尺寸。
    🔴 已有 video_id 且 generating 时直接续轮询，不重提交、不重扣配额。"""
    from src.core.store import ProjectStore
    from src.core.prompt_builder import build_video_prompt
    from src.core.models import ShotStatus, OpeningSource
    project_dir = get_project_manager().get_project_dir(job.project)
    store = ProjectStore(project_dir)
    project = store.load()
    shot = project.get_shot(job.shot_id)
    if shot is None:
        raise RuntimeError(f"镜头 {job.shot_id} 不存在")

    # 🔴 自动重新托管：首帧 local 有 / url 空（v1 迁移数据）-> imgbb 补公网 URL（100% 保真，不丢画面）。
    #  inherit_previous 不使用本镜 first_frame（开场来自来源镜头末帧），跳过。
    sel_ff = shot.first_frame.selected
    if (shot.opening_source != OpeningSource.INHERIT_PREVIOUS.value
            and shot.first_frame.status == "done" and sel_ff and sel_ff.local
            and not sel_ff.url):
        await emit("rehost", f"{shot.shot_id} 首帧重新托管中（补公网 URL）", 3)
        url = await get_rehost_host().upload(sel_ff.local)
        def _mut_rehost(s, u=url, idx=shot.first_frame.selected_index):
            if 0 <= idx < len(s.first_frame.candidates):
                s.first_frame.candidates[idx].url = u
                s.first_frame.candidates[idx].status = "done"
            s.first_frame.status = "done"
            s.first_frame.updated_at = _time.time()
        store.update_shot(job.shot_id, _mut_rehost)
        shot = store.load().get_shot(job.shot_id)   # 取最新

    # 🔴 视频提交尺寸：16:9 直接按服务端实测输出 1280x704 提交（FACTS §3.4.1：1312x736 会被
    #  归一化成 1280x704；直接提交 1280x704 省这轮归一化）。max_frames_for(1280,704)=441。
    ratio = project.style.aspect_ratio or "16:9"
    if ratio == "16:9":
        w, h = 1280, 704
    else:
        w, h = map(int, _resolve_size(project.style.size_tier or "1K", ratio).split("x"))

    # 用户目标时长（提示词.md §12.1.5）：target_duration_s 非空 -> 转 8n+1 帧数；None -> 跟随配音。
    #  配音退出主流程后：None 且无配音（num_frames=0）-> 默认 3 秒反推，不让视频因无配音被阻止。
    #  写回 shot.video.num_frames，后续 preflight/提交/VideoVersion 都读它，保持一致。
    from src.api.agnes_client import snap_num_frames
    fps0 = max(shot.video.frame_rate, 1)
    if shot.target_duration_s is not None:
        planned = snap_num_frames(int(round(shot.target_duration_s * fps0)), w, h)
    elif not shot.video.num_frames:
        planned = snap_num_frames(int(round(3 * fps0)), w, h)
    else:
        planned = shot.video.num_frames
    if planned != shot.video.num_frames:
        store.update_shot(job.shot_id, lambda s, nf=planned: setattr(s.video, "num_frames", nf))
        shot = store.load().get_shot(job.shot_id)

    # effective 首帧（inherit_previous -> 来源末帧；其余 -> 本镜已选首帧）。preflight/提交统一用此，避免前后端不一致。
    eff_ff_url = _effective_first_frame_url(project, shot)
    seconds = shot.video.num_frames / max(shot.video.frame_rate, 1)
    ok, why = _video_preflight(shot, get_agnes_client().quota, seconds, bool(eff_ff_url))
    if not ok:
        raise RuntimeError(why)   # 不发起 API 请求

    mode = "keyframes" if shot.link_mode == "keyframes" else "i2v"
    # keyframes 尾帧 in-progress 有界等待
    if mode == "keyframes" and not shot.last_frame.url_ready:
        await emit("wait", f"{shot.shot_id} 等待尾帧就绪", 5)
        if not await _await_last_frame_url(store, job.shot_id, emit):
            raise RuntimeError("等待尾帧超时（尾帧尚未生成完成），请重试")
        shot = store.load().get_shot(job.shot_id)   # 取最新

    prompt = build_video_prompt(shot, mode)
    # boundary frames：i2v 用 effective 首帧；keyframes 用 [首帧, 已选尾帧]。
    # legacy 求值保持原位置（恢复路径不触碰；BOUND 路径由 Adapter 以 selection
    # truth + freshness 解析后覆盖）。
    first_url = eff_ff_url
    last_url = None

    # 视频模型（2026-09-06 新契约接入）：job.params.video_model 显式选择时传给
    # create_video（agnes-video-2.5-flash 走 mode/seconds 新契约）；None=config
    # 默认 v2.0，存量行为逐字节不变。模型随 shot.video.model 落库——恢复/续轮询
    # 必须用同一 model_name 轮询，否则 2.5 任务查不到。
    video_model = (job.params or {}).get("video_model") or None

    # 🔴 恢复：已有 video_id 且未 done/failed -> 续轮询，不重提交。
    #  recover_pending_videos 把轮询超时（仍在跑）的设为 queued 后入队，handler 在此续轮询；
    #  failed 的不进恢复分支 -> 走 else 重新提交，避免对失效 video_id 死循环轮询。
    if shot.video.video_id and shot.video.status not in (
            ShotStatus.DONE.value, ShotStatus.FAILED.value):
        vid = shot.video.video_id
        await emit("recover", f"{shot.shot_id} 续轮询 video_id={vid}（不重提交）", 10)
    else:
        # S3C.2 双路径（§21）：BOUND 视频重提交走 Binding-aware path（READY→Pack→
        # Adapter：boundary selection truth + generation_basis freshness + pre-dispatch
        # race guard；NOT_READY→BLOCK，绝不 fallback legacy）；UNBOUND 保持 legacy 行为
        # 逐字节不变。恢复/续轮询路径不做 Binding gate（无新生成输入，不阻断在途
        # 视频的结果回收——轮询不是 generation dispatch）。
        bound_video = None
        if shot_has_binding(project_dir, shot.shot_id):
            bound_video = build_bound_video_input(project_dir, project, shot)
            prompt = bound_video.prompt
            first_url = bound_video.first_frame
            last_url = bound_video.last_frame
        elif mode == "keyframes":
            last_url = shot.last_frame.selected.url
        await emit("submit", f"{shot.shot_id} 提交中（视频 1 RPM，可能等约 60s）", 10)
        kwargs = dict(num_frames=shot.video.num_frames, frame_rate=shot.video.frame_rate,
                      width=w, height=h, seed=shot.seed, negative_prompt=shot.negative_prompt)
        if mode == "keyframes":
            kwargs["image_urls"] = [first_url, last_url]
        else:
            kwargs["image_url"] = first_url
        if video_model:
            kwargs["model"] = video_model
            # 2.5 契约时长（秒，4-12，client 内再夹取）：由既有 8n+1 帧规划反推
            kwargs["duration_s"] = max(4, min(12, round(
                shot.video.num_frames / max(shot.video.frame_rate, 1))))
        created = await get_agnes_client().create_video(prompt, mode=mode, **kwargs)
        vid = created.get("video_id") or created.get("task_id") or created.get("id")
        if not vid:
            raise RuntimeError(f"视频任务创建无 video_id: {created}")
        # 🔴 提交成功立刻落库 video_id（崩了重启能接着轮询，不重提交、不重扣配额）
        store.update_shot(job.shot_id, lambda s, v=vid, c=created, m=video_model: (
            setattr(s.video, "video_id", v),
            setattr(s.video, "task_id", c.get("task_id")),
            setattr(s.video, "status", ShotStatus.GENERATING.value),
            setattr(s.video, "attempts", s.video.attempts + 1),
            setattr(s.video, "model", m),
        ))
        await emit("submit", f"{shot.shot_id} 已提交 video_id={vid}", 15)

    # 轮询（5s，走 text 桶不占视频桶）。model_name：2.5 任务必须用提交时同一模型名
    # （job 显式指定 > 落库值 > None=config 默认）。
    async def on_prog(pct, st, waited):
        await emit("poll", f"{shot.shot_id} {st} {pct}%（已等 {int(waited)}s）", int(pct))
    poll_model = video_model or getattr(shot.video, "model", None)
    try:
        res = await get_agnes_client().poll_video(vid, interval=5, on_progress=on_prog,
                                                  model_name=poll_model)
    except PollTransientError as e:
        # 网络/超时（连续 5 次）：不标 failed，保留 video_id+generating，
        # retry/recover 续轮询（不重提交、不丢已生成视频）
        await emit("warn", f"{shot.shot_id} 轮询暂歇（网络/超时，video_id 保留，重试续轮询）: {str(e)[:150]}", None)
        raise
    except AgnesError:
        # 服务端明确失败 / video_id 不存在：标 failed，retry 时走 else 重新提交
        store.update_shot(job.shot_id,
                          lambda s: setattr(s.video, "status", ShotStatus.FAILED.value))
        raise

    # 下载
    dest = project_dir / "output" / "videos" / f"{shot.shot_id}.mp4"
    await get_agnes_client().download(res["url"], dest)

    # 取最新 shot（含 submit 写入的 video_id/task_id），写视频版本 + 设 final_video + 同步旧 video
    shot = store.load().get_shot(job.shot_id)
    from src.core.models import VideoVersion
    vvid = f"vv_{uuid.uuid4().hex[:8]}"
    now = _time.time()
    char_snap = {name: {"url": project.get_character(name).ref_url()}
                 for name in shot.characters if project.get_character(name)}
    new_vv = VideoVersion(version_id=vvid, created_at=now, status=ShotStatus.DONE.value,
                          prompt_snapshot=prompt, char_ref_snapshot=char_snap,
                          first_frame_candidate_id=shot.selected_first_frame,
                          last_frame_candidate_id=shot.selected_last_frame,
                          video_id=vid, task_id=shot.video.task_id,
                          local=str(dest), url=res["url"],
                          seconds_returned=res["seconds"], size_returned=res["size"],
                          num_frames=shot.video.num_frames, frame_rate=shot.video.frame_rate,
                          origin_run_id=job.params.get("origin_run_id"),
                          model=poll_model or getattr(shot.video, "model", None))
    def mut(s, vv=new_vv, d=str(dest), r=res):
        s.video_versions.append(vv)
        s.final_video = vv.version_id
        s.video.mark_done(d, r["url"])           # 同步旧字段（管线/导出读旧）
        setattr(s.video, "seconds_returned", r["seconds"])
        setattr(s.video, "size_returned", r["size"])
    store.update_shot(job.shot_id, mut)
    await emit("done", f"{shot.shot_id} 完成（版本 {vvid}），实际 {res['seconds']}s / {res['size']}", 100)

    # Phase 6A：提取实际视频末帧（供下游镜头接力）。失败不影响视频成功状态，独立 error + 可 retry。
    try:
        end_asset, _ok = await _extract_video_end_frame(project_dir, job.shot_id, str(dest), vvid, emit)
        def _mut_end(s, ea=end_asset, sv=vvid):
            s.video_end_frame = ea
            s.video_end_source_version = sv
        store.update_shot(job.shot_id, _mut_end)
        if end_asset.error:
            await emit("warn", f"{shot.shot_id} 视频末帧提取/托管未完全成功: {end_asset.error[:120]}", None)
    except Exception as e:
        logger.warning(f"{shot.shot_id} 视频末帧提取异常（不影响视频完成）: {e}")


JOB_HANDLERS["video"] = _handler_video


@router.post("/api/project/{project_name}/shot/{shot_id}/video")
async def shot_video(project_name: str, shot_id: str,
                     video_model: Optional[str] = None):
    """生成单镜头视频（入队 video 任务，串行 worker，1 RPM）。
    video_model 可选：显式指定视频模型（如 agnes-video-2.5-flash，走新契约 mode/seconds）；
    缺省 = config 默认（agnes-video-v2.0），行为不变。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        if ProjectStore(project_dir).load().get_shot(shot_id) is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        extra = {"video_model": video_model} if video_model else {}
        q = get_job_queue()
        job = q.submit(project_name, "video", shot_id, **extra)
        return {"job_id": job.job_id, "shot_id": shot_id}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


class SetFinalVideoRequest(BaseModel):
    version_id: str


@router.post("/api/project/{project_name}/shot/{shot_id}/video/set-final")
async def set_final_video(project_name: str, shot_id: str, req: SetFinalVideoRequest):
    """设最终视频版本（final_video 指针）+ 同步旧 video 字段（compose 读旧）。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        from src.core.models import ShotStatus
        store = ProjectStore(project_dir)
        shot = store.load().get_shot(shot_id)
        if shot is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        vv = next((v for v in shot.video_versions if v.version_id == req.version_id), None)
        if vv is None:
            raise HTTPException(400, f"视频版本 {req.version_id} 不存在")

        def mut(s, v=vv):
            s.final_video = v.version_id
            # 同步旧 video 到所选版本（compose/导出读旧字段，过渡兼容）
            s.video.local = v.local
            s.video.url = v.url
            s.video.status = v.status
            s.video.video_id = v.video_id
            s.video.task_id = v.task_id
            s.video.seconds_returned = v.seconds_returned
            s.video.size_returned = v.size_returned
            s.video.num_frames = v.num_frames
            s.video.frame_rate = v.frame_rate
        store.update_shot(shot_id, mut)
        # 切换最终版本 -> 重新提取实际末帧（来源版本变化，下游接力需更新）
        if vv.local and Path(vv.local).exists():
            try:
                end_asset, _ok = await _extract_video_end_frame(project_dir, shot_id, vv.local, vv.version_id)
                store.update_shot(shot_id, lambda s, ea=end_asset, sv=vv.version_id: (
                    setattr(s, "video_end_frame", ea), setattr(s, "video_end_source_version", sv)))
            except Exception as e:
                logger.warning(f"{shot_id} set-final 末帧提取异常: {e}")
        return {"shot_id": shot_id, "final_video": req.version_id}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ==================== Phase 6A：镜头接力 / 开场来源 接口 ====================
class OpeningSourceRequest(BaseModel):
    source: str   # independent | reference_previous | inherit_previous


@router.post("/api/project/{project_name}/shot/{shot_id}/opening-source")
async def set_opening_source(project_name: str, shot_id: str, req: OpeningSourceRequest):
    """设置当前镜头开场来源。independent 清空来源；reference/inherit 自动取同场景上一镜为来源，
    写入 source_shot_id + source_version（= 上一镜 final_video）。前置不满足直接 400，不让用户选后撞后端错误。"""
    try:
        from src.core.store import ProjectStore
        from src.core.models import OpeningSource, ShotStatus
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        shot = project.get_shot(shot_id)
        if shot is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        if req.source not in {m.value for m in OpeningSource}:
            raise HTTPException(400, f"非法 opening_source: {req.source}")
        if req.source == OpeningSource.INDEPENDENT.value:
            store.update_shot(shot_id, lambda s: (setattr(s, "opening_source", req.source),
                                                  setattr(s, "source_shot_id", None),
                                                  setattr(s, "source_version", None)))
            return {"opening_source": req.source, "source_shot_id": None, "source_version": None,
                    "status": "independent", "message": ""}
        # reference/inherit：取同场景上一镜
        scene = next((s for s in project.scenes if s.scene_id == shot.scene_id), None)
        if not scene:
            raise HTTPException(400, "镜头不在任何场景")
        ids = [s.shot_id for s in scene.shots]
        if shot_id not in ids or ids.index(shot_id) == 0:
            raise HTTPException(400, "第一镜只能独立生成")
        prev_id = ids[ids.index(shot_id) - 1]
        prev = project.get_shot(prev_id)
        if not prev or not prev.final_video or prev.video.status != ShotStatus.DONE.value:
            raise HTTPException(400, "上一镜头完成视频后可用")
        if req.source == OpeningSource.INHERIT_PREVIOUS.value and not prev.video_end_frame.url_ready:
            raise HTTPException(400, "上一镜头末帧未就绪（无公网 URL），先在其视频页刷新末帧")
        sv = prev.final_video
        store.update_shot(shot_id, lambda s, p=prev_id, v=sv: (setattr(s, "opening_source", req.source),
                                                               setattr(s, "source_shot_id", p),
                                                               setattr(s, "source_version", v)))
        status, msg = continuity_status(store.load(), store.load().get_shot(shot_id))
        return {"opening_source": req.source, "source_shot_id": prev_id, "source_version": sv,
                "status": status, "message": msg}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/project/{project_name}/shot/{shot_id}/sync-source")
async def sync_source(project_name: str, shot_id: str):
    """更新为最新末帧：把 source_version 同步到来源镜头当前 final_video（清除 stale）。
    不自动连锁重生成视频，仅刷新接力指针，决定权留给用户。"""
    try:
        from src.core.store import ProjectStore
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        project = store.load()
        shot = project.get_shot(shot_id)
        if shot is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        if not shot.source_shot_id:
            raise HTTPException(400, "当前为独立生成，无来源可同步")
        prev = project.get_shot(shot.source_shot_id)
        if not prev or not prev.final_video:
            raise HTTPException(400, "来源不可用")
        sv = prev.final_video
        store.update_shot(shot_id, lambda s, v=sv: setattr(s, "source_version", v))
        status, msg = continuity_status(store.load(), store.load().get_shot(shot_id))
        return {"status": status, "message": msg, "source_version": sv}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/project/{project_name}/shot/{shot_id}/video-end/refresh")
async def refresh_video_end(project_name: str, shot_id: str):
    """重新提取并托管本镜实际视频末帧（末帧提取/托管失败后 retry）。不重新生成视频。"""
    try:
        from src.core.store import ProjectStore
        project_dir = get_project_manager().get_project_dir(project_name)
        store = ProjectStore(project_dir)
        shot = store.load().get_shot(shot_id)
        if shot is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        if not shot.final_video:
            raise HTTPException(400, "尚未完成视频，无末帧可提取")
        vv = next((v for v in shot.video_versions if v.version_id == shot.final_video), None)
        if not vv or not vv.local or not Path(vv.local).exists():
            raise HTTPException(400, "最终视频文件缺失")
        end_asset, _ok = await _extract_video_end_frame(project_dir, shot_id, vv.local, vv.version_id)
        store.update_shot(shot_id, lambda s, ea=end_asset, sv=vv.version_id: (
            setattr(s, "video_end_frame", ea), setattr(s, "video_end_source_version", sv)))
        return {"status": end_asset.status, "url": end_asset.url, "error": end_asset.error}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


class UpdateShotRequest(BaseModel):
    link_mode: Optional[str] = None
    locked: Optional[bool] = None
    description: Optional[str] = None
    prompt_en: Optional[str] = None
    negative_prompt: Optional[str] = None
    shot_size: Optional[str] = None
    camera: Optional[str] = None
    lighting: Optional[str] = None
    motion: Optional[str] = None
    dialogue: Optional[str] = None
    characters: Optional[List[str]] = None
    # 结构化镜头字段（提示词.md 工作台）
    opening_state: Optional[str] = None
    camera_angle: Optional[str] = None
    composition: Optional[str] = None
    important_details: Optional[str] = None
    required_elements: Optional[List[str]] = None
    last_frame_description: Optional[str] = None
    end_state: Optional[str] = None
    frame_change: Optional[str] = None
    preserve_elements: Optional[List[str]] = None
    add_elements: Optional[str] = None
    remove_elements: Optional[str] = None
    end_composition: Optional[str] = None
    preserve_custom: Optional[str] = None
    camera_movement: Optional[str] = None
    motion_pace: Optional[str] = None
    environment_motion: Optional[str] = None
    action_sequence: Optional[str] = None
    camera_movement_detail: Optional[str] = None
    transition_description: Optional[str] = None
    video_preserve_elements: Optional[List[str]] = None
    video_preserve_custom: Optional[str] = None
    target_duration_s: Optional[float] = None
    last_frame_prompt_en: Optional[str] = None
    video_prompt_en: Optional[str] = None
    first_frame_prompt_override: Optional[str] = None
    last_frame_prompt_override: Optional[str] = None
    video_prompt_override: Optional[str] = None
    first_frame_prompt_sig: Optional[str] = None
    last_frame_prompt_sig: Optional[str] = None
    video_prompt_sig: Optional[str] = None
    # 镜头接力 / 开场来源（Phase 6A）
    opening_source: Optional[str] = None
    source_shot_id: Optional[str] = None
    source_version: Optional[str] = None


@router.put("/api/project/{project_name}/shot/{shot_id}")
async def update_shot(project_name: str, shot_id: str, req: UpdateShotRequest):
    """更新镜头创作字段（工作台编辑），写回 project.json。None 字段不改动。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        from src.core.models import LinkMode
        valid = {m.value for m in LinkMode}
        def mut(s):
            if req.link_mode is not None:
                if req.link_mode not in valid:
                    raise ValueError(f"非法 link_mode: {req.link_mode}")
                s.link_mode = req.link_mode
            if req.locked is not None:
                s.locked = req.locked
            if req.description is not None:
                s.description = req.description
            if req.prompt_en is not None:
                s.prompt_en = req.prompt_en
            if req.negative_prompt is not None:
                s.negative_prompt = req.negative_prompt
            if req.shot_size is not None:
                s.shot_size = req.shot_size
            if req.camera is not None:
                s.camera = req.camera
            if req.lighting is not None:
                s.lighting = req.lighting
            if req.motion is not None:
                s.motion = req.motion
            if req.dialogue is not None:
                s.dialogue = req.dialogue
            if req.characters is not None:
                s.characters = list(req.characters)
            # 结构化字段（提示词.md）：None 不改，否则覆盖（list 拷贝；允许传 [] 清空）
            for f in (
                "opening_state", "camera_angle", "composition", "important_details",
                "required_elements", "last_frame_description", "end_state", "frame_change",
                "preserve_elements", "add_elements", "remove_elements", "end_composition",
                "preserve_custom", "camera_movement", "motion_pace", "environment_motion",
                "action_sequence", "camera_movement_detail", "transition_description",
                "video_preserve_elements", "video_preserve_custom", "target_duration_s",
                "last_frame_prompt_en", "video_prompt_en",
                "first_frame_prompt_override", "last_frame_prompt_override", "video_prompt_override",
                "first_frame_prompt_sig", "last_frame_prompt_sig", "video_prompt_sig",
                "opening_source", "source_shot_id", "source_version",
            ):
                v = getattr(req, f)
                if v is not None:
                    setattr(s, f, list(v) if isinstance(v, list) else v)
        store = ProjectStore(project_dir)
        if store.load().get_shot(shot_id) is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        store.update_shot(shot_id, mut)
        return {"shot_id": shot_id}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ==================== 阶段 3：audio/compose handler + orchestrator + 全局 SSE + 队列接口 ====================
# audio/compose 复用阶段 2 已有逻辑（_gen_one_audio / VideoComposer.compose_shots），抽成 handler。
async def _handler_audio(job, emit):
    """单镜配音（TTS -> 反推 num_frames -> 写回）。复用阶段 2 _gen_one_audio。"""
    from src.core.store import ProjectStore
    from src.api.edge_tts_client import EdgeTTSClient
    project_dir = get_project_manager().get_project_dir(job.project)
    store = ProjectStore(project_dir)
    shot = store.load().get_shot(job.shot_id)
    if shot is None:
        raise RuntimeError(f"镜头 {job.shot_id} 不存在")
    voice = job.params.get("voice", "default")
    rate = job.params.get("rate", "+0%")
    volume = job.params.get("volume", "+0%")
    fps = _fps_from_config()
    await emit("tts", f"{shot.shot_id} 配音中", 10)
    tts = EdgeTTSClient()
    _sid, res, timing, err = await _gen_one_audio(
        tts, project_dir, job.shot_id, shot.dialogue, voice, rate, volume, fps)
    if err:
        raise RuntimeError(err)
    store.update_shot(job.shot_id,
                      lambda sh: _apply_audio_to_shot(sh, res, timing, voice, rate))
    msg = f"{shot.shot_id} 配音完成 num_frames={timing.num_frames}"
    if timing.needs_split:
        msg += f"（⚠️ 建议拆为 {timing.suggested_splits} 镜）"
    await emit("done", msg, 100)


JOB_HANDLERS["audio"] = _handler_audio


def _select_shots(p, scope, skip_locked: bool):
    """orchestrate 范围选择。scope: 'all' | 'pending' | [shot_id,...]。"""
    shots = p.all_shots()
    if scope == "pending":
        sel = [s for s in shots
               if not (s.first_frame.ready
                       and (s.link_mode != "keyframes" or s.last_frame.ready)
                       and s.video.ready)]
    elif isinstance(scope, list):
        sset = set(scope)
        sel = [s for s in shots if s.shot_id in sset]
    else:
        sel = list(shots)
    if skip_locked:
        sel = [s for s in sel if not s.locked]
    return sel


class OrchestrateRequest(BaseModel):
    stages: List[str] = ["first_frame", "last_frame", "video", "compose"]
    scope: Any = "all"        # "all" | "pending" | [shot_id, ...]
    skip_locked: bool = True
    dry_run: bool = False
    voice: str = "default"
    rate: str = "+0%"
    volume: str = "+0%"
    width: int = 1280
    height: int = 720
    fps: int = 24
    burn_subtitles: bool = True


@router.post("/api/project/{project_name}/orchestrate")
async def orchestrate_project(project_name: str, req: OrchestrateRequest):
    """编排器：内部逐个入队 shot 级 handler，白捡断点续跑。
    顺序不可调换：first_frame -> last_frame -> video -> compose（audio 已退出主流程；显式传 stages 含 audio 仍可执行）。
    🔴 last_frame/video gate 带 first_frame.url_ready 前置（首帧须已选择）。
    dry_run 返回视频数量/预计时间/配额消耗，让用户在 1 RPM 现实下预判。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        p = ProjectStore(project_dir).load()
        shots = _select_shots(p, req.scope, req.skip_locked)

        plan: List[tuple] = []
        if "first_frame" in req.stages:
            plan += [("first_frame", s.shot_id) for s in shots if not s.first_frame.ready]
        if "audio" in req.stages:
            plan += [("audio", s.shot_id) for s in shots
                     if s.dialogue and not s.audio.ready]
        if "last_frame" in req.stages:
            plan += [("last_frame", s.shot_id) for s in shots
                     if s.link_mode == "keyframes" and not s.last_frame.ready
                     and s.first_frame.url_ready]
        if "video" in req.stages:
            plan += [("video", s.shot_id) for s in shots
                     if not s.video.ready and s.first_frame.url_ready
                     and s.video.num_frames > 0]

        n_video = sum(1 for k, _ in plan if k == "video")
        if req.dry_run:
            total_seconds = sum(
                s.video.num_frames / max(s.video.frame_rate, 1) for s in shots
                if not s.video.ready and s.first_frame.url_ready and s.video.num_frames > 0)
            return {"dry_run": True, "plan": plan, "plan_count": len(plan),
                    "video_count": n_video,
                    "eta_minutes": round(n_video * 70 / 60, 1),
                    "video_seconds_needed": round(total_seconds, 1)}

        q = get_job_queue()
        audio_params = {"voice": req.voice, "rate": req.rate, "volume": req.volume}
        compose_params = {"width": req.width, "height": req.height,
                          "fps": req.fps, "burn_subtitles": req.burn_subtitles}
        job_ids = []
        for kind, sid in plan:
            if kind == "audio":
                j = q.submit(project_name, "audio", sid, **audio_params)
            else:
                j = q.submit(project_name, kind, sid)
            job_ids.append(j.job_id)
        if "compose" in req.stages:
            job_ids.append(q.submit(project_name, "compose", **compose_params).job_id)
        return {"dry_run": False, "plan": plan, "plan_count": len(plan),
                "video_count": n_video, "job_ids": job_ids,
                "queue": q.stats()}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ==================== 镜头增删 + 翻译（分镜工作台 Step 1）====================
class AddShotRequest(BaseModel):
    characters: Optional[List[str]] = None   # 默认继承故事段角色


@router.post("/api/project/{project_name}/scene/{scene_id}/shot")
async def add_shot(project_name: str, scene_id: str, req: AddShotRequest):
    """在故事段下添加镜头。shot_id = {scene_id}_c{next}，默认继承故事段角色，立即持久化。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        from src.core.models import Shot, ImageAsset
        store = ProjectStore(project_dir)
        p = store.load()
        scene = next((sc for sc in p.scenes if sc.scene_id == scene_id), None)
        if scene is None:
            raise HTTPException(404, f"故事段 {scene_id} 不存在")
        used = []
        for sh in scene.shots:
            if sh.shot_id.startswith(scene_id + "_c"):
                try:
                    used.append(int(sh.shot_id[len(scene_id) + 2:]))
                except ValueError:
                    pass
        next_n = (max(used) + 1) if used else 1
        new_id = f"{scene_id}_c{next_n:02d}"
        chars = list(req.characters) if req.characters is not None else list(scene.characters)
        new_shot = Shot(shot_id=new_id, scene_id=scene_id, index=len(scene.shots) + 1,
                        characters=chars, first_frame=ImageAsset(), last_frame=ImageAsset())
        scene.shots.append(new_shot)
        store.save(p)
        logger.info(f"新增镜头 {new_id} 到故事段 {scene_id}")
        return {"shot_id": new_id, "scene_id": scene_id}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/api/project/{project_name}/shot/{shot_id}")
async def delete_shot(project_name: str, shot_id: str):
    """删除镜头 + 清理其产物文件（仅本项目目录内，防 stale 路径误删）。"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        store = ProjectStore(project_dir)
        p = store.load()
        shot = p.get_shot(shot_id)
        if shot is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        paths = []
        if shot.video and shot.video.local:
            paths.append(shot.video.local)
        if shot.audio and shot.audio.local:
            paths.append(shot.audio.local)
        for img in (shot.first_frame, shot.last_frame):
            if img and img.candidates:
                for c in img.candidates:
                    if c.local:
                        paths.append(c.local)
        for sc in p.scenes:
            sc.shots = [sh for sh in sc.shots if sh.shot_id != shot_id]
        store.save(p)
        # 保存后再清文件（仅本项目目录内）
        for pp in paths:
            try:
                ppp = Path(pp)
                if not ppp.is_absolute():
                    ppp = BASE_DIR / ppp
                ppp = ppp.resolve()
                try:
                    ppp.relative_to(project_dir.resolve())
                except ValueError:
                    continue
                if ppp.exists():
                    ppp.unlink()
            except Exception:
                pass
        logger.info(f"镜头 {shot_id} 已删除")
        return {"shot_id": shot_id, "deleted": True}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


class TranslateRequest(BaseModel):
    text: str


@router.post("/api/translate")
async def translate_text(req: TranslateRequest):
    """中文画面描述 -> 英文提示词（复用 agnes.chat）。无副作用，前端拿到填入 prompt_en。"""
    try:
        agnes = get_agnes_client()
        prompt = (f"将以下中文画面描述翻译成英文图片生成提示词，"
                  f"只返回英文提示词，不要其他文字：\n{req.text}")
        en = await agnes.chat([{"role": "user", "content": prompt}],
                              temperature=0.5, max_tokens=1024)
        return {"english": (en or "").strip()}
    except Exception as e:
        raise HTTPException(500, str(e))


# ============================================================================
# AI-4B Autopilot 共享业务 helper（§四十一：HTTP Router 与 Executor 共用，不绕 HTTP/AgnesClient）
# 提取自上方 endpoint 的核心逻辑；endpoint 行为不变，Autopilot 直接调用。
# ============================================================================

async def generate_prompt_for_stage(project_name: str, shot_id: str, stage: str,
                                    *, persist: bool = False) -> dict:
    """标准模式「根据上下文生成」英文提示词（§十二）。
    返回 {"prompt": str, "inferred_shot_intent": str}。
    persist=True 时写入 shot 字段：first->prompt_en, last->last_frame_prompt_en, video->video_prompt_en。
    shot 不存在 -> RuntimeError；stage 非法 -> ValueError。"""
    project_dir = get_project_manager().get_project_dir(project_name)
    from src.core.store import ProjectStore
    project = ProjectStore(project_dir).load()
    shot = project.get_shot(shot_id)
    if shot is None:
        raise RuntimeError(f"镜头 {shot_id} 不存在")
    if stage not in ("first", "last", "video"):
        raise ValueError("stage 必须为 first/last/video")
    ctx = _collect_prompt_context(project, shot, stage)
    ctx_text = _format_context(ctx, stage)
    client = get_agnes_client()
    inferred = ""
    if stage == "first":
        has_intent = bool(ctx["shot_intent"])
        if not has_intent and ctx["total"] == 1 and ctx["scene_summary"]:
            user = f"本镜意图(取自故事段): {ctx['scene_summary']}\n\nContext:\n{ctx_text}"
            en = await client.chat(
                [{"role": "system", "content": _FIRST_FRAME_SYSTEM_HAS_INTENT},
                 {"role": "user", "content": user}], temperature=0.7, max_tokens=512)
            prompt = en.strip()
        elif has_intent:
            user = f"Stage: first frame\n\nContext:\n{ctx_text}"
            en = await client.chat(
                [{"role": "system", "content": _FIRST_FRAME_SYSTEM_HAS_INTENT},
                 {"role": "user", "content": user}], temperature=0.7, max_tokens=512)
            prompt = en.strip()
        else:
            user = f"Stage: first frame (infer shot intent)\n\nContext:\n{ctx_text}"
            raw = await client.chat(
                [{"role": "system", "content": _FIRST_FRAME_SYSTEM_INFER},
                 {"role": "user", "content": user}], temperature=0.7, max_tokens=700, json_mode=True)
            import json as _json, re as _re
            m = _re.search(r'\{.*\}', raw, _re.DOTALL)
            if m:
                try:
                    obj = _json.loads(m.group(0))
                    prompt = (obj.get("prompt") or "").strip()
                    inferred = (obj.get("shot_intent") or "").strip()
                except Exception:
                    prompt = raw.strip()
            else:
                prompt = raw.strip()
    else:
        system = _LAST_FRAME_SYSTEM if stage == "last" else _VIDEO_SYSTEM
        stage_desc = "last frame" if stage == "last" else "video"
        en = await client.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": f"Stage: {stage_desc}\n\nContext:\n{ctx_text}"}],
            temperature=0.7, max_tokens=512)
        prompt = en.strip()

    if persist:
        field = {"first": "prompt_en", "last": "last_frame_prompt_en",
                 "video": "video_prompt_en"}[stage]
        ProjectStore(project_dir).update_shot(shot_id, lambda s, f=field, p=prompt: setattr(s, f, p))
    return {"prompt": prompt, "inferred_shot_intent": inferred}


def activate_planned_shot(store, project, shot_id, planned) -> dict:
    """Executor 真正执行 Shot 时正式应用 PlannedShot（§九）。
    写 shot.opening_source / source_shot_id / source_version / link_mode。
    reference/inherit：source_version = prev.final_video（本 Run 刚生成的版本，或 skipped_locked 的已有版本）。
    🔴 不通过 HTTP /opening-source；复用 set_opening_source 的业务逻辑。
    调用方须先 dependency_ready 校验（reference/inherit 的 prev final_video + video_end_frame 已就绪）。"""
    from src.core.models import OpeningSource, ShotStatus
    shot = project.get_shot(shot_id)
    if shot is None:
        raise RuntimeError(f"镜头 {shot_id} 不存在")
    opening = planned.opening_source
    link_mode = planned.link_mode
    if opening == OpeningSource.INDEPENDENT.value:
        store.update_shot(shot_id, lambda s, o=opening, lm=link_mode: (
            setattr(s, "opening_source", o),
            setattr(s, "source_shot_id", None),
            setattr(s, "source_version", None),
            setattr(s, "link_mode", lm)))
        return {"opening_source": opening, "source_shot_id": None, "source_version": None,
                "link_mode": link_mode, "status": "independent", "message": ""}
    # reference / inherit
    prev_id = planned.source_shot_id
    prev = project.get_shot(prev_id) if prev_id else None
    if not prev or not prev.final_video or prev.video.status != ShotStatus.DONE.value:
        raise RuntimeError(f"来源镜头 {prev_id} 最终视频未就绪，无法 activate {opening}")
    if opening == OpeningSource.INHERIT_PREVIOUS.value and not prev.video_end_frame.url_ready:
        raise RuntimeError(f"来源镜头 {prev_id} 末帧未就绪，无法 inherit")
    sv = prev.final_video
    store.update_shot(shot_id, lambda s, o=opening, p=prev_id, v=sv, lm=link_mode: (
        setattr(s, "opening_source", o),
        setattr(s, "source_shot_id", p),
        setattr(s, "source_version", v),
        setattr(s, "link_mode", lm)))
    proj2 = store.load()
    status, msg = continuity_status(proj2, proj2.get_shot(shot_id))
    return {"opening_source": opening, "source_shot_id": prev_id, "source_version": sv,
            "link_mode": link_mode, "status": status, "message": msg}


def choose_first_frame_business(store, shot_id: str, candidate_id: str) -> dict:
    """选择首帧候选的业务逻辑（extract 自 choose_first_frame endpoint）。
    跨所有批次找候选，设 selected_first_frame + 同步旧 first_frame（url_ready=True 放行视频）。"""
    from src.core.models import ShotStatus, Asset
    shot = store.load().get_shot(shot_id)
    if shot is None:
        raise RuntimeError(f"镜头 {shot_id} 不存在")
    found_batch, found_idx = None, -1
    for b in shot.first_frame_batches:
        for i, c in enumerate(b.candidates):
            if c.candidate_id == candidate_id:
                found_batch, found_idx = b, i
                break
        if found_batch:
            break
    if found_batch is None:
        raise RuntimeError(f"候选 {candidate_id} 不存在")
    cand = found_batch.candidates[found_idx]
    def mut(s, b=found_batch, i=found_idx, cid=candidate_id):
        s.selected_first_frame = cid
        s.first_frame.candidates = [Asset(local=c.local, url=c.url, status=c.status) for c in b.candidates]
        s.first_frame.selected_index = i
        setattr(s.first_frame, "status", ShotStatus.DONE.value)
    store.update_shot(shot_id, mut)
    return {"shot_id": shot_id, "candidate_id": candidate_id, "url": cand.url}


def choose_last_frame_business(store, shot_id: str, candidate_id: str) -> dict:
    """选择尾帧候选的业务逻辑（extract 自 choose_last_frame endpoint）。"""
    from src.core.models import ShotStatus, Asset
    shot = store.load().get_shot(shot_id)
    if shot is None:
        raise RuntimeError(f"镜头 {shot_id} 不存在")
    found_batch, found_idx = None, -1
    for b in shot.last_frame_batches:
        for i, c in enumerate(b.candidates):
            if c.candidate_id == candidate_id:
                found_batch, found_idx = b, i
                break
        if found_batch:
            break
    if found_batch is None:
        raise RuntimeError(f"候选 {candidate_id} 不存在")
    cand = found_batch.candidates[found_idx]
    def mut(s, b=found_batch, i=found_idx, cid=candidate_id):
        s.selected_last_frame = cid
        s.last_frame.candidates = [Asset(local=c.local, url=c.url, status=c.status) for c in b.candidates]
        s.last_frame.selected_index = i
        setattr(s.last_frame, "status", ShotStatus.DONE.value)
    store.update_shot(shot_id, mut)
    return {"shot_id": shot_id, "candidate_id": candidate_id, "url": cand.url}


async def set_final_video_business(store, project_dir, shot_id: str, version_id: str) -> dict:
    """设最终视频版本的业务逻辑（extract 自 set_final_video endpoint，§二十五）。
    设 final_video + 同步旧 video 字段 + 重新提取 video_end_frame。
    🔴 _handler_video 完成时已自动 set final + 提取末帧；本 helper 作为 Executor 显式确认/补提的复用入口。"""
    from src.core.models import ShotStatus
    shot = store.load().get_shot(shot_id)
    if shot is None:
        raise RuntimeError(f"镜头 {shot_id} 不存在")
    vv = next((v for v in shot.video_versions if v.version_id == version_id), None)
    if vv is None:
        raise RuntimeError(f"视频版本 {version_id} 不存在")
    def mut(s, v=vv):
        s.final_video = v.version_id
        s.video.local = v.local
        s.video.url = v.url
        s.video.status = v.status
        s.video.video_id = v.video_id
        s.video.task_id = v.task_id
        s.video.seconds_returned = v.seconds_returned
        s.video.size_returned = v.size_returned
        s.video.num_frames = v.num_frames
        s.video.frame_rate = v.frame_rate
    store.update_shot(shot_id, mut)
    if vv.local and Path(vv.local).exists():
        try:
            end_asset, _ok = await _extract_video_end_frame(project_dir, shot_id, vv.local, vv.version_id)
            store.update_shot(shot_id, lambda s, ea=end_asset, sv=vv.version_id: (
                setattr(s, "video_end_frame", ea), setattr(s, "video_end_source_version", sv)))
        except Exception as e:
            logger.warning(f"{shot_id} set-final 末帧提取异常: {e}")
    return {"shot_id": shot_id, "final_video": version_id}


