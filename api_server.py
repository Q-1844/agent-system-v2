"""
API服务器
提供REST API供前端调用
"""

# 强制UTF-8编码（解决Windows GBK编码emoji报错）
import os
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

import sys
import io
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 加载 .env 文件
from pathlib import Path as _Path
_env_path = _Path(__file__).parent / ".env"
if _env_path.exists():
    with open(_env_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                _key, _val = _key.strip(), _val.strip()
                if _key and _val and not os.environ.get(_key):
                    os.environ[_key] = _val

import asyncio
import json
import uuid
import base64 as _base64
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from src.pipeline.controller import PipelineController
from src.utils.project_manager import ProjectManager

# 共享状态/单例/getter/配置/路径都在 deps（阶段5③拆分：避免 api_server <-> router 循环导入）。
# 🔴 reassigned 单例（pipeline_controller/job_queue/_rehost_host）用 deps.X 属性访问，
#    不能 from deps import（会绑死 import 时的 None，startup 赋值后看不到）。
import deps
from deps import (
    logger, BASE_DIR, projects_dir, _APP_CFG, _AGNES_PROXY,
    JOB_HANDLERS, task_store, current_task, task_progress,
    get_controller, get_job_queue, get_rehost_host, _new_task,
    _read_parsed_script, _write_parsed_script,
    _save_base64_image, _load_image_as_base64,
)

app = FastAPI(title="动画分镜工作流 API", version="1.0.0")

# 静态文件挂载（projects_dir / BASE_DIR / 配置 / 单例 / getter 都在 deps.py）
app.mount("/projects", StaticFiles(directory=str(projects_dir)), name="projects")

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局状态、任务队列单例、重新托管单例、任务存储、getter、请求模型统已在 deps.py
# （pipeline_controller / job_queue / _rehost_host 用 deps.X 属性访问，见顶部 import 注释）。

# ============ 路由挂载（阶段5③拆分：各域路由在 routers/，按 PHASE_5 §5.2 顺序逐步搬入）============
from routers import system  # noqa: E402
from routers import projects  # noqa: E402
from routers import script  # noqa: E402
from routers import characters  # noqa: E402
from routers import compose  # noqa: E402
app.include_router(system.router)
app.include_router(projects.router)
app.include_router(script.router)
app.include_router(characters.router)
app.include_router(compose.router)


@app.on_event("startup")
async def startup():
    """启动时初始化"""
    try:
        # 先初始化限流器与配额单例（用 config 的值），再创建 PipelineController
        # （其 AgnesClient 会复用这两个进程级单例）
        from src.core.rate_limiter import RateLimiter
        from src.core.quota import QuotaLedger
        _agnes_cfg = _APP_CFG.get("agnes") or {}
        RateLimiter.get(_agnes_cfg.get("rpm"))
        QuotaLedger.get_default(BASE_DIR / "cache" / "quota.json",
                                limits=_APP_CFG.get("quota"))

        config_path = os.path.join(os.path.dirname(__file__), "config", "config.yaml")
        deps.pipeline_controller = PipelineController(config_path)
        logger.info("API服务器已启动")
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        # 即使初始化失败也允许服务器运行，返回友好错误

    # 初始化默认角色到角色库
    characters._init_default_characters()

    # 阶段 3：启动全局任务队列 + 崩溃恢复（恢复在后台跑，不阻塞服务就绪）
    try:
        from src.core.job_queue import JobQueue
        deps.job_queue = JobQueue(BASE_DIR / "cache" / "jobs.json", JOB_HANDLERS)
        # 🔴 清理孤儿 job（project/shot 已删除的 running/queued）--在 start route 前，孤儿不进 worker
        purged = deps.job_queue.purge_orphans(_job_target_exists)
        if purged:
            logger.info(f"启动时清理 {purged} 个孤儿 job")
        await deps.job_queue.start()
        asyncio.create_task(recover_pending_videos())
        logger.info("全局任务队列已就绪")
    except Exception as e:
        logger.error(f"任务队列初始化失败: {e}")


async def recover_pending_videos():
    """🔴 崩溃恢复（PHASE_3 §3.4）：重启后对 video.status==generating 且有 video_id
    的镜头先 poll_video 查一次，不盲目重提交（省 60s + 不重扣配额）。
    已完成 -> 下载入库；仍在跑/失效 -> 标 queued 并入队由 video handler 续轮询。"""
    try:
        controller = deps.pipeline_controller
        if controller is None:
            return
        from src.core.store import ProjectStore
        client = controller.agnes_client
        for pdir in projects_dir.iterdir():
            if not pdir.is_dir():
                continue
            if not (pdir / "output" / "project.json").exists():
                continue
            try:
                store = ProjectStore(pdir)
                p = store.load()
            except Exception:
                continue
            for sh in p.all_shots():
                if sh.video.status != "generating" or not sh.video.video_id:
                    continue
                vid = sh.video.video_id
                logger.info(f"恢复检查: {p.name}/{sh.shot_id} video_id={vid}")
                try:
                    res = await client.poll_video(vid, interval=5, max_wait=60)
                    dest = str(pdir / "output" / "videos" / f"{sh.shot_id}.mp4")
                    await client.download(res["url"], dest)
                    store.update_shot(sh.shot_id, lambda s, d=dest, r=res: (
                        s.video.mark_done(d, r["url"]),
                        setattr(s.video, "seconds_returned", r["seconds"]),
                        setattr(s.video, "size_returned", r["size"]),
                    ))
                    logger.info(f"恢复完成: {sh.shot_id}")
                except Exception as e:
                    logger.warning(f"恢复 {sh.shot_id} 未完成（{e}），重新入队续轮询")
                    store.update_shot(sh.shot_id,
                                      lambda s: setattr(s.video, "status", "queued"))
                    if "video" in JOB_HANDLERS and deps.job_queue is not None:
                        deps.job_queue.submit(p.name, "video", sh.shot_id)
    except Exception as e:
        logger.error(f"recover_pending_videos 失败: {e}")


def _job_target_exists(project: Optional[str], shot_id: Optional[str]) -> bool:
    """判断 job 的 target 是否还存在（清理孤儿 job 用）。project/shot 任一不存在 -> False。
    无 project 的 job（如全局 compose）不算孤儿；controller 未就绪时不误清。"""
    if not project:
        return True
    ctrl = deps.pipeline_controller
    if ctrl is None:
        return True
    try:
        pdir = ctrl.project_manager.get_project_dir(project)
    except Exception:
        return False
    if not (pdir / "output" / "project.json").exists():
        return False
    if shot_id:
        from src.core.store import ProjectStore
        try:
            return ProjectStore(pdir).load().get_shot(shot_id) is not None
        except Exception:
            return False
    return True


@app.get("/character-test")
async def character_test_page():
    """角色三视图测试页"""
    html_path = BASE_DIR / "character_test.html"
    if html_path.exists():
        return FileResponse(str(html_path), media_type="text/html")
    return {"error": "character_test.html not found"}


@app.get("/storyboard-test")
async def storyboard_test_page():
    """分镜生成测试页"""
    html_path = BASE_DIR / "storyboard_test.html"
    if html_path.exists():
        return FileResponse(str(html_path), media_type="text/html")
    return {"error": "storyboard_test.html not found"}


@app.get("/scene-test")
async def scene_test_page():
    """场景库管理页"""
    html_path = BASE_DIR / "scene_test.html"
    if html_path.exists():
        return FileResponse(str(html_path), media_type="text/html")
    return {"error": "scene_test.html not found"}


@app.post("/api/generate-image")
async def generate_image(request: Request):
    """直接生成图片（供测试页调用）"""
    try:
        controller = get_controller()
        body = await request.json()
        prompt = body.get("prompt", "")
        seed = body.get("seed")
        size = body.get("size", "1024x1024")

        if not prompt:
            raise HTTPException(status_code=400, detail="prompt 不能为空")

        result = await controller.agnes_client.generate_image(
            prompt=prompt, seed=seed, size=size
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate-image-base64")
async def generate_image_base64(request: Request):
    """生成图片并返回 base64（避免 CORS 问题）"""
    try:
        controller = get_controller()
        body = await request.json()
        prompt = body.get("prompt", "")
        seed = body.get("seed")
        size = body.get("size", "1024x1024")

        if not prompt:
            raise HTTPException(status_code=400, detail="prompt 不能为空")

        result = await controller.agnes_client.generate_image(
            prompt=prompt, seed=seed, size=size
        )

        if not result or "data" not in result or not result["data"]:
            raise HTTPException(500, "图片生成失败")

        url = result["data"][0].get("url")
        if not url:
            raise HTTPException(500, "返回数据中没有URL")

        # 后端下载图片并转换为 base64
        import httpx
        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            img_resp = await client.get(url, timeout=60)
            img_resp.raise_for_status()
            image_bytes = img_resp.content

        image_base64 = _base64.b64encode(image_bytes).decode("utf-8")

        return {
            "success": True,
            "image_base64": image_base64,
            "prompt": prompt,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"生成图片失败: {e}")
        raise HTTPException(500, str(e))


# ============ 角色生成（独立操作） ============
# 这些接口用于角色生成页面，每步可单独调用

# ============ 工作流操作 ============

@app.get("/api/task/{task_id}")
async def get_task_status(task_id: str):
    """获取任务状态（先查 task_store 新任务，再回退旧 current_task）"""
    if task_id in task_store:
        return {"task_id": task_id, **task_store[task_id]}
    if not current_task or current_task["task_id"] != task_id:
        return {"task_id": task_id, "status": "not_found", "progress": {}, "message": "任务不存在"}

    return current_task


@app.get("/api/task/{task_id}/stream")
async def stream_task_status(task_id: str):
    """SSE 实时推送任务状态（每秒推送 task_store，直到 completed/failed）"""
    async def event_stream():
        while True:
            if task_id in task_store:
                t = task_store[task_id]
                payload = {
                    "task_id": task_id,
                    "status": t.get("status"),
                    "message": t.get("message", ""),
                    "progress": t.get("progress", {}),
                    "result": t.get("result"),
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if t.get("status") in ("completed", "failed"):
                    break
            elif current_task and current_task.get("task_id") == task_id:
                yield f"data: {json.dumps({'task_id': task_id, **current_task}, ensure_ascii=False)}\n\n"
                if current_task.get("status") in ("completed", "failed"):
                    break
            else:
                yield f"data: {json.dumps({'task_id': task_id, 'status': 'not_found'}, ensure_ascii=False)}\n\n"
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


@app.post("/api/project/{project_name}/shot/{shot_id}/audio")
async def shot_audio(project_name: str, shot_id: str, req: ShotAudioRequest,
                     background_tasks: BackgroundTasks):
    """单镜配音（TTS -> 反推 num_frames -> 写回 audio+video 参数）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
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


@app.post("/api/project/{project_name}/audio/batch")
async def batch_audio(project_name: str, req: BatchAudioRequest,
                      background_tasks: BackgroundTasks):
    """批量配音（edge-tts 不占 Agnes 限流桶，并发 4，顺序写回避免丢失更新）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
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


@app.post("/api/project/{project_name}/shot/{shot_id}/split-dialogue")
async def split_shot_dialogue(project_name: str, shot_id: str, req: SplitDialogueRequest):
    """拆超长台词为多个镜头：原 Shot 保留第1段，后续段插入同场次新 Shot，shot_id 顺移。"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
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


@app.get("/api/project/{project_name}/shots")
async def get_project_shots(project_name: str):
    """返回 v2 项目结构（scenes/shots 含 first_frame/last_frame/audio/video 产物）。
    供前端 shot 级页面加载。local 路径前端用 media.ts 转 URL；url 是 Agnes 公网 URL。"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
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


@app.get("/api/project/{project_name}/timing")
async def get_project_timing(project_name: str):
    """时长预览：每镜 audio_seconds/planned_seconds/needs_split + 全片总时长"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
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
    controller = get_controller()
    project_dir = controller.project_manager.get_project_dir(job.project)
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
        res = await controller.agnes_client.generate_image(
            prompt=prompt, image_refs=refs if use_i2i else None,
            size_tier=size_tier, ratio=ratio, seed=seed)
        url = res.get("url")
        if not url:
            raise RuntimeError("图片生成未返回公网 URL")
        dest = frames_dir / f"{shot.shot_id}_first_v{i+1}.png"
        await controller.agnes_client.download(url, dest)   # 无认证 client，复用于图片资产
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
    controller = get_controller()
    project_dir = controller.project_manager.get_project_dir(job.project)
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
    res = await controller.agnes_client.generate_image(
        prompt=prompt, image_refs=[ff_url],
        size_tier=project.style.size_tier or "1K", ratio=project.style.aspect_ratio or "16:9")
    url = res.get("url")
    if not url:
        raise RuntimeError("尾帧生成未返回公网 URL")
    dest = project_dir / "output" / "frames" / f"{shot.shot_id}_last.png"
    await controller.agnes_client.download(url, dest)
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


@app.post("/api/project/{project_name}/shot/{shot_id}/first-frame")
async def shot_first_frame(project_name: str, shot_id: str, req: FirstFrameRequest):
    """生成单镜头分镜图候选（入队 first_frame 任务，进度走全局 SSE）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
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


@app.post("/api/project/{project_name}/shot/{shot_id}/first-frame/choose")
async def choose_first_frame(project_name: str, shot_id: str, req: ChooseFrameRequest):
    """选择分镜图候选：设 selected_index + first_frame.status=done（url_ready=True，放行视频）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
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


@app.post("/api/project/{project_name}/shot/{shot_id}/first-frame/rehost")
async def rehost_first_frame(project_name: str, shot_id: str):
    """重新托管首帧：local 有 / url 空 -> imgbb 上传 -> 回填 url + status=done。
    100% 保真，不重新生成、不丢画面。用于 v1 迁移的本地首帧补公网 URL（送进视频接口）。
    幂等：已有 url 直接返回 rehosted=False。同步调用（几秒），不入队、不占 video 名额。
    🔴 imgbb 图为公开图（拿到链接即可访问）。上传失败明示抛错，不静默回落到重新生成。"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
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


@app.post("/api/project/{project_name}/shot/{shot_id}/last-frame")
async def shot_last_frame(project_name: str, shot_id: str):
    """生成单镜头尾帧（仅 keyframes；入队 last_frame 任务）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
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
    controller = get_controller()
    project_dir = controller.project_manager.get_project_dir(job.project)
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
    ok, why = _video_preflight(shot, controller.agnes_client.quota, seconds)
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
        created = await controller.agnes_client.create_video(prompt, mode=mode, **kwargs)
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
        res = await controller.agnes_client.poll_video(vid, interval=5, on_progress=on_prog)
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
    await controller.agnes_client.download(res["url"], dest)

    # 🔴 回读入库（seconds/size 是服务端归一化后的真实值，FACTS §3.6）
    store.update_shot(job.shot_id, lambda s, d=str(dest), r=res: (
        s.video.mark_done(d, r["url"]),
        setattr(s.video, "seconds_returned", r["seconds"]),
        setattr(s.video, "size_returned", r["size"]),
    ))
    await emit("done", f"{shot.shot_id} 完成，实际 {res['seconds']}s / {res['size']}", 100)


JOB_HANDLERS["video"] = _handler_video


@app.post("/api/project/{project_name}/shot/{shot_id}/video")
async def shot_video(project_name: str, shot_id: str):
    """生成单镜头视频（入队 video 任务，串行 worker，1 RPM）"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
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


@app.put("/api/project/{project_name}/shot/{shot_id}")
async def update_shot(project_name: str, shot_id: str, req: UpdateShotRequest):
    """更新镜头创作字段（link_mode / locked），写回 project.json。"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
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
    controller = get_controller()
    project_dir = controller.project_manager.get_project_dir(job.project)
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


@app.post("/api/project/{project_name}/orchestrate")
async def orchestrate_project(project_name: str, req: OrchestrateRequest):
    """编排器（替代 /api/run）：内部逐个入队 shot 级 handler，白捡断点续跑。
    顺序不可调换：first_frame -> audio -> last_frame -> video -> compose。
    🔴 last_frame/video gate 带 first_frame.url_ready 前置（首帧须已选择）。
    dry_run 返回视频数量/预计时间/配额消耗，让用户在 1 RPM 现实下预判。"""
    try:
        controller = get_controller()
        project_dir = controller.project_manager.get_project_dir(project_name)
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


# ---- 队列管理接口 + 全局单 SSE ----
@app.get("/api/project/{project_name}/jobs")
async def list_project_jobs(project_name: str, state: Optional[str] = None):
    """列出项目任务（支持 ?state=failed/queued/...）"""
    return {"jobs": get_job_queue().list_jobs(project=project_name, state=state),
            "queue": get_job_queue().stats()}


@app.get("/api/jobs/stream")
async def jobs_stream(request: Request, project: Optional[str] = None):
    """全局单 SSE：一条连接推送所有任务状态变化（替代按任务订阅，避免 6 连接上限卡死）。"""
    q = get_job_queue()
    sub = q.subscribe()

    async def gen():
        try:
            yield f"event: snapshot\ndata: {json.dumps(q.list_jobs(project), ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(sub.get(), timeout=15)
                    if project and payload.get("project") != project:
                        continue
                    yield f"event: job\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"   # 防代理超时断流
        finally:
            q.unsubscribe(sub)

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, force: bool = False):
    """取消任务。force=true 可取消 running（标记 cancelled + 中断 handler 释放 worker）。
    🔴 force 取消 running 需前端二次确认（handler 被中断，已生成部分可能丢失）。"""
    if not get_job_queue().cancel(job_id, force=force):
        raise HTTPException(400, "任务无法取消（可能已完成或已取消）")
    return {"job_id": job_id, "state": "cancelled"}


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str):
    j = get_job_queue().retry(job_id)
    if j is None:
        raise HTTPException(400, "任务无法重试（仅 failed/cancelled 可重试）")
    return {"job_id": job_id, "state": "queued"}


@app.post("/api/project/{project_name}/jobs/retry-failed")
async def retry_failed_jobs(project_name: str):
    ids = get_job_queue().retry_failed(project_name)
    return {"retried": ids, "count": len(ids)}


@app.delete("/api/project/{project_name}/jobs/finished")
async def clear_finished_jobs(project_name: str):
    n = get_job_queue().clear_finished(project_name)
    return {"cleared": n}


# ============ 分镜生成测试接口 ============

class StoryboardRequest(BaseModel):
    """分镜生成请求"""
    description: str           # 场景描述
    characters: list[str] = [] # 角色名称列表
    camera: str = ""           # 镜头描述
    lighting: str = ""         # 光照描述
    style: str = "anime, cinematic lighting, high quality"
    size: str = "1536x1024"    # 横版，适合场景
    image_refs: list[str] = [] # 参考图列表（base64）


class StoryboardBatchRequest(BaseModel):
    """批量分镜生成请求"""
    scenes: list[dict]         # 场景列表
    style: str = "anime, cinematic lighting, high quality"
    size: str = "1536x1024"


def _build_storyboard_prompt(
    description: str,
    characters: list[str] = None,
    camera: str = None,
    lighting: str = None,
    style: str = None,
) -> str:
    """构建分镜生成的 prompt"""
    parts = [
        description,
        style or "anime, cinematic lighting, high quality",
        "anime scene, detailed background, cinematic composition",
    ]

    if characters:
        char_str = ", ".join(characters)
        parts.append(f"featuring characters: {char_str}")

    if camera:
        parts.append(camera)

    if lighting:
        parts.append(lighting)

    return ", ".join(parts)


@app.post("/api/storyboard/generate")
async def generate_storyboard(req: StoryboardRequest):
    """
    生成单个分镜图

    返回: image_url, image_base64, prompt
    """
    try:
        controller = get_controller()
    except HTTPException:
        raise

    # 构建 prompt
    prompt = _build_storyboard_prompt(
        description=req.description,
        characters=req.characters,
        camera=req.camera,
        lighting=req.lighting,
        style=req.style,
    )

    try:
        # 构建参考图
        image_refs = None
        if req.image_refs:
            image_refs = [f"data:image/png;base64,{ref}" for ref in req.image_refs]

        # 生成图片
        result = await controller.agnes_client.generate_image(
            prompt=prompt,
            size=req.size,
            image_refs=image_refs,
        )

        if not result or "data" not in result or not result["data"]:
            raise HTTPException(500, "图片生成失败")

        url = result["data"][0].get("url")
        if not url:
            raise HTTPException(500, "返回数据中没有URL")

        # 下载图片
        import httpx
        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            img_resp = await client.get(url, timeout=60)
            img_resp.raise_for_status()
            image_bytes = img_resp.content

        image_base64 = _base64.b64encode(image_bytes).decode("utf-8")

        return {
            "success": True,
            "image_url": url,
            "image_base64": image_base64,
            "prompt": prompt,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"分镜生成失败: {e}")
        raise HTTPException(500, str(e))


@app.post("/api/storyboard/generate-with-ref")
async def generate_storyboard_with_ref(
    description: str,
    ref_image_base64: str,
    style: str = "anime, cinematic lighting, high quality",
    size: str = "1536x1024",
):
    """
    使用参考图生成分镜（图生图）
    """
    try:
        controller = get_controller()
    except HTTPException:
        raise

    # 构建 prompt
    prompt = f"Based on this character reference, create a scene: {description}, {style}, anime scene, detailed background"

    # 构建参考图
    image_ref = f"data:image/png;base64,{ref_image_base64}"

    try:
        result = await controller.agnes_client.generate_image(
            prompt=prompt,
            size=size,
            image_refs=[image_ref],
        )

        if not result or "data" not in result or not result["data"]:
            raise HTTPException(500, "图片生成失败")

        url = result["data"][0].get("url")
        if not url:
            raise HTTPException(500, "返回数据中没有URL")

        # 下载图片
        import httpx
        async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
            img_resp = await client.get(url, timeout=60)
            img_resp.raise_for_status()
            image_bytes = img_resp.content

        image_base64 = _base64.b64encode(image_bytes).decode("utf-8")

        return {
            "success": True,
            "image_url": url,
            "image_base64": image_base64,
            "prompt": prompt,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"分镜生成失败: {e}")
        raise HTTPException(500, str(e))


@app.post("/api/storyboard/batch")
async def generate_storyboard_batch(req: StoryboardBatchRequest):
    """
    批量生成分镜图

    返回: 每个场景的生成结果
    """
    try:
        controller = get_controller()
    except HTTPException:
        raise

    results = []

    for scene in req.scenes:
        scene_id = scene.get("id", 0)
        description = scene.get("description", "")
        characters = scene.get("characters", [])
        camera = scene.get("camera", "")
        lighting = scene.get("lighting", "")

        # 构建 prompt
        prompt = _build_storyboard_prompt(
            description=description,
            characters=characters,
            camera=camera,
            lighting=lighting,
            style=req.style,
        )

        try:
            result = await controller.agnes_client.generate_image(
                prompt=prompt,
                size=req.size,
            )

            if result and "data" in result and result["data"]:
                url = result["data"][0].get("url")
                if url:
                    # 下载图片
                    import httpx
                    async with httpx.AsyncClient(proxy=_AGNES_PROXY) as client:
                        img_resp = await client.get(url, timeout=60)
                        img_resp.raise_for_status()
                        image_bytes = img_resp.content

                    image_base64 = _base64.b64encode(image_bytes).decode("utf-8")

                    results.append({
                        "scene_id": scene_id,
                        "success": True,
                        "image_url": url,
                        "image_base64": image_base64,
                        "prompt": prompt,
                    })
                    continue

        except Exception as e:
            logger.error(f"场景 {scene_id} 生成失败: {e}")

        results.append({
            "scene_id": scene_id,
            "success": False,
            "error": "生成失败",
            "prompt": prompt,
        })

    return {"results": results}


# ============ 场景库系统 ============

# 场景保存目录
SAVED_SCENES_DIR = BASE_DIR / "saved_scenes"
SAVED_SCENES_DIR.mkdir(parents=True, exist_ok=True)


class SaveSceneRequest(BaseModel):
    """保存场景请求"""
    name: str                    # 场景名称
    description: str             # 场景描述
    image_base64: str            # 场景图片 base64
    tags: list[str] = []         # 标签（如：室内、室外、白天、夜晚）


def _get_scene_dir(scene_id: str) -> Path:
    """获取场景保存目录"""
    return SAVED_SCENES_DIR / scene_id


def _load_scene_meta(scene_dir: Path) -> dict:
    """加载场景元数据"""
    meta_file = scene_dir / "meta.json"
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_scene_meta(scene_dir: Path, meta: dict):
    """保存场景元数据"""
    meta_file = scene_dir / "meta.json"
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _init_default_scenes():
    """初始化默认场景（如果场景库为空）"""
    try:
        existing = list(SAVED_SCENES_DIR.iterdir())
        if any(d.is_dir() for d in existing):
            logger.info("场景库已有数据，跳过初始化")
            return

        # 默认场景描述（不生成图片，只保存描述）
        # 场景图必须无人物，是纯背景/环境
        default_scenes = [
            {
                "name": "山顶云海",
                "description": "mountain top overlooking sea of clouds, golden sunrise, panoramic view, no humans, empty landscape, anime background",
                "tags": ["室外", "山顶", "晨光"],
            },
            {
                "name": "黑暗云层",
                "description": "dark cloudy sky, ominous atmosphere, red glow, dramatic lighting, no humans, empty sky, anime background",
                "tags": ["室外", "天空", "黑暗"],
            },
            {
                "name": "风暴战场",
                "description": "stormy battlefield, lightning flashes, dramatic clouds, no humans, empty battlefield, anime background",
                "tags": ["室外", "战场", "风暴"],
            },
            {
                "name": "宁静村庄",
                "description": "peaceful anime village, traditional japanese houses, cherry blossoms, sunny day, no humans, empty village, anime background",
                "tags": ["室外", "村庄", "白天"],
            },
            {
                "name": "神秘森林",
                "description": "mystical forest, glowing mushrooms, moonlight, no humans, empty forest, fantasy anime background",
                "tags": ["室外", "森林", "夜晚"],
            },
            {
                "name": "城堡大厅",
                "description": "grand castle hall, stone pillars, torches, medieval fantasy, no humans, empty hall, anime background",
                "tags": ["室内", "城堡", "火光"],
            },
        ]

        for scene in default_scenes:
            scene_id = f"{scene['name']}_{uuid.uuid4().hex[:8]}"
            scene_dir = _get_scene_dir(scene_id)
            scene_dir.mkdir(parents=True, exist_ok=True)

            meta = {
                "id": scene_id,
                "name": scene["name"],
                "description": scene["description"],
                "tags": scene["tags"],
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "has_image": False,  # 默认场景没有图片，需要用户生成
            }
            _save_scene_meta(scene_dir, meta)
            logger.info(f"已添加默认场景: {scene['name']}")

    except Exception as e:
        logger.error(f"初始化默认场景失败: {e}")


# 在启动时初始化默认场景
@app.on_event("startup")
async def init_scenes():
    """初始化场景库"""
    _init_default_scenes()


@app.post("/api/scene/save")
async def save_scene(req: SaveSceneRequest):
    """
    保存场景到场景库

    返回: scene_id
    """
    try:
        # 生成唯一ID
        scene_id = f"{req.name}_{uuid.uuid4().hex[:8]}"
        scene_dir = _get_scene_dir(scene_id)
        scene_dir.mkdir(parents=True, exist_ok=True)

        # 保存图片
        if req.image_base64:
            _save_base64_image(req.image_base64, scene_dir / "image.png")

        # 保存元数据
        meta = {
            "id": scene_id,
            "name": req.name,
            "description": req.description,
            "tags": req.tags,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "has_image": bool(req.image_base64),
        }
        _save_scene_meta(scene_dir, meta)

        logger.info(f"场景已保存: {scene_id}")
        return {
            "success": True,
            "scene_id": scene_id,
            "message": f"场景 '{req.name}' 已保存",
        }
    except Exception as e:
        logger.error(f"保存场景失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")


@app.get("/api/scene/list")
async def list_saved_scenes():
    """获取已保存的场景列表"""
    try:
        scenes = []
        if SAVED_SCENES_DIR.exists():
            for scene_dir in SAVED_SCENES_DIR.iterdir():
                if scene_dir.is_dir():
                    meta = _load_scene_meta(scene_dir)
                    if meta:
                        scenes.append(meta)

        # 按创建时间倒序排列
        scenes.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return {"scenes": scenes}
    except Exception as e:
        logger.error(f"获取场景列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/scene/load/{scene_id}")
async def load_scene(scene_id: str):
    """加载已保存的场景（包含图片）"""
    try:
        scene_dir = _get_scene_dir(scene_id)
        if not scene_dir.exists():
            raise HTTPException(status_code=404, detail="场景不存在")

        # 加载元数据
        meta = _load_scene_meta(scene_dir)

        # 加载图片
        image_base64 = _load_image_as_base64(scene_dir / "image.png")

        return {
            "success": True,
            "scene": {
                "id": meta.get("id"),
                "name": meta.get("name"),
                "description": meta.get("description"),
                "tags": meta.get("tags", []),
                "created_at": meta.get("created_at"),
                "image_base64": image_base64,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"加载场景失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/scene/{scene_id}")
async def delete_scene(scene_id: str):
    """删除已保存的场景"""
    try:
        scene_dir = _get_scene_dir(scene_id)
        if not scene_dir.exists():
            raise HTTPException(status_code=404, detail="场景不存在")

        # 删除所有文件
        import shutil
        shutil.rmtree(scene_dir)

        logger.info(f"场景已删除: {scene_id}")
        return {"success": True, "message": "场景已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除场景失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
