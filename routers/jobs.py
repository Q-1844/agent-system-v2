"""任务/队列路由：task 状态查询 + SSE + 队列管理（PHASE_5 §5.2 jobs 组）。

- /api/task/{id}、/api/task/{id}/stream：单任务状态 + SSE（task_store + 旧 current_task 回退）
- /api/project/{p}/jobs、/api/jobs/stream：队列列表 + 全局单 SSE（snapshot + job 事件 + keepalive）
- cancel/retry/retry-failed/clear-finished：队列管理
"""
import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from deps import task_store, current_task, get_job_queue

router = APIRouter()


# ============ 单任务状态 + SSE ============
@router.get("/api/task/{task_id}")
async def get_task_status(task_id: str):
    """获取任务状态（先查 task_store 新任务，再回退旧 current_task）"""
    if task_id in task_store:
        return {"task_id": task_id, **task_store[task_id]}
    if not current_task or current_task["task_id"] != task_id:
        return {"task_id": task_id, "status": "not_found", "progress": {}, "message": "任务不存在"}

    return current_task


@router.get("/api/task/{task_id}/stream")
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


# ============ 队列管理接口 + 全局单 SSE ============
@router.get("/api/project/{project_name}/jobs")
async def list_project_jobs(project_name: str, state: Optional[str] = None):
    """列出项目任务（支持 ?state=failed/queued/...）"""
    return {"jobs": get_job_queue().list_jobs(project=project_name, state=state),
            "queue": get_job_queue().stats()}


@router.get("/api/jobs/stream")
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


@router.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, force: bool = False):
    """取消任务。force=true 可取消 running（标记 cancelled + 中断 handler 释放 worker）。
    🔴 force 取消 running 需前端二次确认（handler 被中断，已生成部分可能丢失）。"""
    if not get_job_queue().cancel(job_id, force=force):
        raise HTTPException(400, "任务无法取消（可能已完成或已取消）")
    return {"job_id": job_id, "state": "cancelled"}


@router.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str):
    j = get_job_queue().retry(job_id)
    if j is None:
        raise HTTPException(400, "任务无法重试（仅 failed/cancelled 可重试）")
    return {"job_id": job_id, "state": "queued"}


@router.post("/api/project/{project_name}/jobs/retry-failed")
async def retry_failed_jobs(project_name: str):
    ids = get_job_queue().retry_failed(project_name)
    return {"retried": ids, "count": len(ids)}


@router.delete("/api/project/{project_name}/jobs/finished")
async def clear_finished_jobs(project_name: str):
    n = get_job_queue().clear_finished(project_name)
    return {"cleared": n}
