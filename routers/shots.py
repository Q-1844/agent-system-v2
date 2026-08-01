"""分镜/镜头路由：shot 级管线 + job handlers（PHASE_5 §5.2 shots 组，最大）。

shot 级接口：audio/batch/split-dialogue/shots/timing/first-frame/choose/rehost/
last-frame/video/update-shot/orchestrate。
job handlers：first_frame/last_frame/video/audio（注册到 deps.JOB_HANDLERS，供 JobQueue 调用）。
"""
import asyncio
import json
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
from src.core.prompt_builder import (
    build_shot_prompt, build_i2i_prompt, build_last_frame_prompt)


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


async def _await_first_frame_url(store, shot_id, emit, timeout=180, interval=5):
    """有界等待 first_frame.url_ready。bridge 场景首帧来自前驱尾帧，可能尚未就绪。
    超时返回 None（调用方报错）。"""
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        sh = store.load().get_shot(shot_id)
        if sh and sh.first_frame.url_ready:
            return sh.first_frame.selected.url
        await asyncio.sleep(interval)
    return None


async def _handler_first_frame(job, emit):
    """生成 N 候选分镜图（图生图/文生图），存 local + 公网 url，status=pending 等用户选。"""
    from src.core.store import ProjectStore
    from src.core.models import Asset, ShotStatus
    project_dir = get_project_manager().get_project_dir(job.project)
    store = ProjectStore(project_dir)
    project = store.load()
    shot = project.get_shot(job.shot_id)
    if shot is None:
        raise RuntimeError(f"镜头 {job.shot_id} 不存在")
    force = bool(job.params.get("force", False))
    if shot.locked and not force:
        await emit("skip", f"{shot.shot_id} 已锁定，跳过", 100); return
    if shot.first_frame.ready and not force:
        await emit("skip", f"{shot.shot_id} 已有分镜图，跳过（force=True 可重生成）", 100); return

    variants = max(1, int(job.params.get("variants", 2)))
    size_tier = project.style.size_tier or "1K"
    ratio = project.style.aspect_ratio or "16:9"
    refs = _shot_char_refs(project, shot)
    use_i2i = bool(refs)
    prompt = build_i2i_prompt(project, shot) if use_i2i else build_shot_prompt(project, shot)
    frames_dir = project_dir / "output" / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    candidates = []
    for i in range(variants):
        await emit("gen", f"{shot.shot_id} 生成候选 {i+1}/{variants}", int(i / variants * 100))
        seed = (shot.seed + i) if shot.seed is not None else None   # 多候选要有变化
        res = await get_agnes_client().generate_image(
            prompt=prompt, image_refs=refs if use_i2i else None,
            size_tier=size_tier, ratio=ratio, seed=seed)
        url = res.get("url")
        if not url:
            raise RuntimeError("图片生成未返回公网 URL")
        dest = frames_dir / f"{shot.shot_id}_first_v{i+1}.png"
        await get_agnes_client().download(url, dest)   # 无认证 client，复用于图片资产
        candidates.append(Asset(local=str(dest), url=url, status=ShotStatus.DONE.value))
        await emit("gen", f"{shot.shot_id} 候选 {i+1} 完成", int((i + 1) / variants * 100))

    # 写回：候选各自 done，ImageAsset.status=pending（等用户点选 -> url_ready=False）
    def mut(s):
        s.first_frame.candidates = candidates
        s.first_frame.selected_index = 0
        s.first_frame.status = ShotStatus.PENDING.value
        s.first_frame.error = None
        s.first_frame.updated_at = _time.time()
    store.update_shot(job.shot_id, mut)
    await emit("done", f"{shot.shot_id} 已生成 {len(candidates)} 个候选，待选择", 100)


async def _handler_last_frame(job, emit):
    """生成尾帧（仅 keyframes，图生图用 first_frame.url）。
    生成后若 split_next 非空，桥接拷贝给后继首帧（O2.first_frame = O.last_frame，保证 O->O2 无缝）。"""
    from src.core.store import ProjectStore
    from src.core.models import Asset, ImageAsset, ShotStatus
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
    prompt = build_last_frame_prompt(project, shot)
    res = await get_agnes_client().generate_image(
        prompt=prompt, image_refs=[ff_url],
        size_tier=project.style.size_tier or "1K", ratio=project.style.aspect_ratio or "16:9")
    url = res.get("url")
    if not url:
        raise RuntimeError("尾帧生成未返回公网 URL")
    dest = project_dir / "output" / "frames" / f"{shot.shot_id}_last.png"
    await get_agnes_client().download(url, dest)
    store.update_shot(job.shot_id, lambda s: s.last_frame.mark_done(str(dest), url))
    await emit("done", f"{shot.shot_id} 尾帧完成", 90)

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
    variant: int = 1   # 1-based


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
        if not shot.first_frame.candidates:
            raise HTTPException(400, "该镜头尚无分镜图候选，请先生成")
        idx = req.variant - 1
        if not (0 <= idx < len(shot.first_frame.candidates)):
            raise HTTPException(400, f"variant 超出范围（1~{len(shot.first_frame.candidates)}）")
        store.update_shot(shot_id, lambda s, i=idx: (
            s.first_frame.select(i),
            setattr(s.first_frame, "status", ShotStatus.DONE.value),
        ))
        return {"shot_id": shot_id, "selected_index": idx,
                "url": shot.first_frame.candidates[idx].url}
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


# ==================== 阶段 3：镜头视频生成（shot 级，走 JobQueue 串行视频 worker）====================
from src.api.agnes_client import resolve_size as _resolve_size, PollTransientError, AgnesError


def _video_preflight(shot, quota, seconds):
    """视频前置硬校验（缺一不可，不通过就不提交，省 60s）。
    仅检查「硬失败」；keyframes 尾帧 in-progress 的有界等待在 handler 里做。"""
    from src.core.models import ShotStatus
    if shot.locked:
        return False, "镜头已锁定"
    if not shot.video.num_frames:
        return False, "尚未配音，无法确定视频时长（请先配音）"
    if not shot.first_frame.url_ready:
        return False, "首帧缺少公网 URL（未选择候选或未生成），请先生成并选择首帧"
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
    from src.core.models import ShotStatus
    project_dir = get_project_manager().get_project_dir(job.project)
    store = ProjectStore(project_dir)
    project = store.load()
    shot = project.get_shot(job.shot_id)
    if shot is None:
        raise RuntimeError(f"镜头 {job.shot_id} 不存在")

    # 🔴 自动重新托管：首帧 local 有 / url 空（v1 迁移数据）-> imgbb 补公网 URL（100% 保真，不丢画面）。
    #  用户无感：不用知道公网 URL 是什么，preflight 前自动补齐。不重新生成。上传失败明示抛错。
    sel_ff = shot.first_frame.selected
    if (shot.first_frame.status == "done" and sel_ff and sel_ff.local
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

    seconds = shot.video.num_frames / max(shot.video.frame_rate, 1)
    ok, why = _video_preflight(shot, get_agnes_client().quota, seconds)
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
    # 🔴 视频提交尺寸：16:9 直接按服务端实测输出 1280x704 提交（FACTS §3.4.1：1312x736 会被
    #  归一化成 1280x704，两者宽高比不同 1.783 vs 1.818 有裁切；直接提交 1280x704 省这轮归一化）。
    #  图片仍走档位 1312x736；图片->视频的裁切是单次逐镜，keyframes 接力链传的是图片不累积。
    #  max_frames_for(1280,704)=441（long_side=1280 落 720p 档），441 实测正是在 1280x704 取得。
    ratio = project.style.aspect_ratio or "16:9"
    if ratio == "16:9":
        w, h = 1280, 704
    else:
        w, h = map(int, _resolve_size(project.style.size_tier or "1K", ratio).split("x"))

    # 🔴 恢复：已有 video_id 且未 done/failed -> 续轮询，不重提交。
    #  recover_pending_videos 把轮询超时（仍在跑）的设为 queued 后入队，handler 在此续轮询；
    #  failed 的不进恢复分支 -> 走 else 重新提交，避免对失效 video_id 死循环轮询。
    if shot.video.video_id and shot.video.status not in (
            ShotStatus.DONE.value, ShotStatus.FAILED.value):
        vid = shot.video.video_id
        await emit("recover", f"{shot.shot_id} 续轮询 video_id={vid}（不重提交）", 10)
    else:
        await emit("submit", f"{shot.shot_id} 提交中（视频 1 RPM，可能等约 60s）", 10)
        kwargs = dict(num_frames=shot.video.num_frames, frame_rate=shot.video.frame_rate,
                      width=w, height=h, seed=shot.seed, negative_prompt=shot.negative_prompt)
        if mode == "keyframes":
            kwargs["image_urls"] = [shot.first_frame.selected.url, shot.last_frame.selected.url]
        else:
            kwargs["image_url"] = shot.first_frame.selected.url
        created = await get_agnes_client().create_video(prompt, mode=mode, **kwargs)
        vid = created.get("video_id") or created.get("task_id") or created.get("id")
        if not vid:
            raise RuntimeError(f"视频任务创建无 video_id: {created}")
        # 🔴 提交成功立刻落库 video_id（崩了重启能接着轮询，不重提交、不重扣配额）
        store.update_shot(job.shot_id, lambda s, v=vid, c=created: (
            setattr(s.video, "video_id", v),
            setattr(s.video, "task_id", c.get("task_id")),
            setattr(s.video, "status", ShotStatus.GENERATING.value),
            setattr(s.video, "attempts", s.video.attempts + 1),
        ))
        await emit("submit", f"{shot.shot_id} 已提交 video_id={vid}", 15)

    # 轮询（5s，走 text 桶不占视频桶）
    async def on_prog(pct, st, waited):
        await emit("poll", f"{shot.shot_id} {st} {pct}%（已等 {int(waited)}s）", int(pct))
    try:
        res = await get_agnes_client().poll_video(vid, interval=5, on_progress=on_prog)
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

    # 🔴 回读入库（seconds/size 是服务端归一化后的真实值，FACTS §3.6）
    store.update_shot(job.shot_id, lambda s, d=str(dest), r=res: (
        s.video.mark_done(d, r["url"]),
        setattr(s.video, "seconds_returned", r["seconds"]),
        setattr(s.video, "size_returned", r["size"]),
    ))
    await emit("done", f"{shot.shot_id} 完成，实际 {res['seconds']}s / {res['size']}", 100)


JOB_HANDLERS["video"] = _handler_video


@router.post("/api/project/{project_name}/shot/{shot_id}/video")
async def shot_video(project_name: str, shot_id: str):
    """生成单镜头视频（入队 video 任务，串行 worker，1 RPM）"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        from src.core.store import ProjectStore
        if ProjectStore(project_dir).load().get_shot(shot_id) is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        q = get_job_queue()
        job = q.submit(project_name, "video", shot_id)
        return {"job_id": job.job_id, "shot_id": shot_id}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


class UpdateShotRequest(BaseModel):
    link_mode: Optional[str] = None
    locked: Optional[bool] = None


@router.put("/api/project/{project_name}/shot/{shot_id}")
async def update_shot(project_name: str, shot_id: str, req: UpdateShotRequest):
    """更新镜头创作字段（link_mode / locked），写回 project.json。"""
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
        store = ProjectStore(project_dir)
        if store.load().get_shot(shot_id) is None:
            raise HTTPException(404, f"镜头 {shot_id} 不存在")
        store.update_shot(shot_id, mut)
        return {"shot_id": shot_id, "link_mode": req.link_mode, "locked": req.locked}
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
               if not (s.first_frame.ready and s.audio.ready
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
    stages: List[str] = ["first_frame", "audio", "last_frame", "video", "compose"]
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
    顺序不可调换：first_frame -> audio -> last_frame -> video -> compose。
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


