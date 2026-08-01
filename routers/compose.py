"""合成路由：shot 级最终合成 + compose job handler（PHASE_5 §5.2 compose 组）。

compose_shots_video 走 BackgroundTasks + _compose_shots_bg（task_store 进度）；
_handler_compose 注册到 deps.JOB_HANDLERS，供 orchestrator 提交 "compose" job 时调用。
"""
import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, BackgroundTasks, Request

from deps import (
    get_project_manager, logger, _new_task, task_store, JOB_HANDLERS,
)

router = APIRouter()


async def _handler_compose(job, emit):
    """最终合成（concat->整音轨->烧SRT，Ken Burns 兜底）。在线程内跑 ffmpeg，不阻塞事件循环。"""
    from src.core.store import ProjectStore
    from src.pipeline.video_composer import VideoComposer
    from src.utils.file_saver import FileSaver
    project_dir = get_project_manager().get_project_dir(job.project)
    project = ProjectStore(project_dir).load()
    width = int(job.params.get("width", 1280))
    height = int(job.params.get("height", 720))
    fps = int(job.params.get("fps", 24))
    burn = bool(job.params.get("burn_subtitles", True))
    composer = VideoComposer(FileSaver(project_dir))
    await emit("compose", f"开始合成 {project.name}（{width}x{height}@{fps}）", 5)
    final = await asyncio.to_thread(
        composer.compose_shots, project,
        width=width, height=height, fps=fps, burn_subtitles=burn)
    await emit("done", f"合成完成: {final}", 100)


# 注册到全局 JOB_HANDLERS（job_queue 提交 "compose" 类型时调用；模块加载时注册，早于 startup）
JOB_HANDLERS["compose"] = _handler_compose


async def _compose_shots_bg(task_id, project_dir, width, height, fps, burn):
    from src.core.store import ProjectStore
    from src.pipeline.video_composer import VideoComposer
    from src.utils.file_saver import FileSaver
    task_store[task_id]["status"] = "running"
    task_store[task_id]["message"] = "合成中..."
    try:
        project = ProjectStore(Path(project_dir)).load()
        composer = VideoComposer(FileSaver(Path(project_dir)))

        def on_progress(msg, pct):
            task_store[task_id]["message"] = msg
            if pct is not None:
                task_store[task_id]["progress"] = {"percent": pct}

        final = composer.compose_shots(project, width=width, height=height,
                                       fps=fps, burn_subtitles=burn,
                                       on_progress=on_progress)
        task_store[task_id]["status"] = "completed"
        task_store[task_id]["result"] = {"video_path": str(final)}
        task_store[task_id]["message"] = "合成完成"
        logger.info(f"shot 级合成完成: {final}")
    except Exception as e:
        task_store[task_id]["status"] = "failed"
        task_store[task_id]["message"] = f"失败: {e}"
        logger.error(f"shot 级合成失败: {e}")


@router.post("/api/project/{project_name}/compose-shots")
async def compose_shots_video(project_name: str, request: Request,
                              background_tasks: BackgroundTasks):
    """阶段 2 shot 级合成（concat->整音轨->烧全片SRT，Ken Burns 兜底，无逐镜偏移）"""
    try:
        project_dir = get_project_manager().get_project_dir(project_name)
        try:
            body = await request.json()
        except Exception:
            body = {}
        width = int(body.get("width", 1280))
        height = int(body.get("height", 720))
        fps = int(body.get("fps", 24))
        burn = bool(body.get("burn_subtitles", True))
        task_id = _new_task("compose")
        background_tasks.add_task(
            _compose_shots_bg, task_id, str(project_dir), width, height, fps, burn
        )
        return {"task_id": task_id, "message": "shot 级合成已启动"}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
