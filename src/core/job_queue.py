# src/core/job_queue.py
# 全局任务队列（PHASE_3_VIDEO_PIPELINE §3.5）。
#
# 🔴 关键设计：视频用独立的串行 worker（并发=1），图片/音频用另一个 worker（并发=3）。
#    两条通道互不阻塞——否则一个视频任务卡 5 分钟，后面所有配音都动不了。
#    视频串行是 1 RPM 现实下的硬要求（FACTS §4：并发提交 = 全部 429）。
#
# 持久化到 cache/jobs.json（原子写），重启后 running 的非视频任务回退为 queued，
# 视频任务交给 recover_pending_videos 处理（不盲目重提交）。
from __future__ import annotations
import asyncio, json, os, tempfile, time, uuid, logging
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path
from typing import Callable, Awaitable, Optional, List, Dict, Any

log = logging.getLogger("JobQueue")


class JobKind(str, Enum):
    FIRST_FRAME = "first_frame"
    LAST_FRAME  = "last_frame"
    AUDIO       = "audio"
    VIDEO       = "video"
    COMPOSE     = "compose"


class JobState(str, Enum):
    QUEUED = "queued"; RUNNING = "running"
    DONE = "done"; FAILED = "failed"; CANCELLED = "cancelled"


# 一个 handler 是 `async def(job, emit) -> None`；emit 是 `async def(stage, msg, pct=None)`。
HandlerFn = Callable[..., Awaitable[None]]


@dataclass
class Job:
    job_id: str
    project: str
    kind: str
    shot_id: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    state: str = JobState.QUEUED.value
    progress: int = 0
    message: str = ""
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    attempts: int = 0


class JobQueue:
    """单进程全局队列。视频串行；图片/音频 3 并发。两条通道互不阻塞。"""

    def __init__(self, state_path: Path, handlers: Dict[str, HandlerFn]):
        self.state_path = Path(state_path)
        self.handlers = handlers                      # 外部可变 dict，注册 handler 即增
        self.jobs: Dict[str, Job] = {}
        self._video_q: asyncio.Queue = asyncio.Queue()
        self._light_q: asyncio.Queue = asyncio.Queue()
        self._subs: List[asyncio.Queue] = []
        self._tasks: List[asyncio.Task] = []
        self._started = False
        self._load()

    # ---------- 持久化（原子写）----------
    def _load(self):
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.jobs = {k: Job(**v) for k, v in data.items()}
            # 🔴 重启后 running 的任务重置为 queued（视频交给 recover 处理，不在此重排）
            for j in self.jobs.values():
                if j.state == JobState.RUNNING.value and j.kind != JobKind.VIDEO.value:
                    j.state = JobState.QUEUED.value
        except Exception as e:
            log.warning("队列状态读取失败，从空开始: %s", e)
            self.jobs = {}

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.state_path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({k: asdict(v) for k, v in self.jobs.items()},
                          f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.state_path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ---------- 入队 ----------
    def submit(self, project: str, kind: str, shot_id: Optional[str] = None,
               **params) -> Job:
        job = Job(job_id=uuid.uuid4().hex[:12], project=project,
                  kind=kind, shot_id=shot_id, params=params)
        self.jobs[job.job_id] = job
        self._save()
        self._route(job.job_id, kind)
        asyncio.create_task(self._broadcast(job))
        return job

    def submit_batch(self, project: str, kind: str,
                     shot_ids: List[str], **params) -> List[Job]:
        return [self.submit(project, kind, sid, **params) for sid in shot_ids]

    def _route(self, job_id: str, kind: str):
        q = self._video_q if kind == JobKind.VIDEO.value else self._light_q
        q.put_nowait(job_id)

    def cancel(self, job_id: str) -> bool:
        j = self.jobs.get(job_id)
        if j and j.state == JobState.QUEUED.value:
            j.state = JobState.CANCELLED.value
            j.finished_at = time.time()
            self._save()
            asyncio.create_task(self._broadcast(j))
            return True
        return False

    def retry(self, job_id: str) -> Optional[Job]:
        j = self.jobs.get(job_id)
        if not j or j.state not in (JobState.FAILED.value, JobState.CANCELLED.value):
            return None
        j.state, j.error, j.progress, j.message = JobState.QUEUED.value, None, 0, ""
        j.finished_at = None
        self._save()
        self._route(job_id, j.kind)
        asyncio.create_task(self._broadcast(j))
        return j

    def retry_failed(self, project: Optional[str] = None) -> List[str]:
        ids = []
        for j in list(self.jobs.values()):
            if j.state == JobState.FAILED.value and (project is None or j.project == project):
                if self.retry(j.job_id):
                    ids.append(j.job_id)
        return ids

    def clear_finished(self, project: Optional[str] = None) -> int:
        n = 0
        for jid in list(self.jobs.keys()):
            j = self.jobs[jid]
            done = j.state in (JobState.DONE.value, JobState.FAILED.value, JobState.CANCELLED.value)
            if done and (project is None or j.project == project):
                del self.jobs[jid]; n += 1
        if n:
            self._save()
        return n

    # ---------- worker ----------
    async def start(self):
        if self._started:
            return
        self._started = True
        self._tasks = [
            asyncio.create_task(self._worker(self._video_q, "video-worker")),
            *[asyncio.create_task(self._worker(self._light_q, f"light-worker-{i}"))
              for i in range(3)],
        ]
        # 重启后把残留的 queued 任务重新推进队列
        for j in self.jobs.values():
            if j.state == JobState.QUEUED.value:
                self._route(j.job_id, j.kind)
        log.info("JobQueue 已启动：1 视频串行 worker + 3 轻任务 worker")

    async def _worker(self, q: asyncio.Queue, name: str):
        while True:
            job_id = await q.get()
            job = self.jobs.get(job_id)
            if not job or job.state != JobState.QUEUED.value:
                q.task_done(); continue
            job.state = JobState.RUNNING.value
            job.started_at = time.time(); job.attempts += 1
            job.message = job.message or "开始处理"
            self._save(); await self._broadcast(job)
            try:
                handler = self.handlers.get(job.kind)
                if handler is None:
                    raise RuntimeError(f"未注册的 job kind: {job.kind}")

                async def emit(stage: str, msg: str, pct: int | None = None):
                    job.message = msg or stage
                    if pct is not None:
                        job.progress = int(pct)
                    self._save()
                    await self._broadcast(job)

                await handler(job, emit)
                job.state, job.progress = JobState.DONE.value, 100
                if not job.message:
                    job.message = "完成"
            except Exception as e:
                log.exception("[%s] 任务失败 %s", name, job_id)
                job.state = JobState.FAILED.value
                job.error = str(e)[:800]
                job.message = f"失败: {str(e)[:200]}"
            finally:
                job.finished_at = time.time()
                self._save(); await self._broadcast(job)
                q.task_done()

    # ---------- SSE ----------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subs.append(q)
        return q

    def unsubscribe(self, q):
        if q in self._subs:
            self._subs.remove(q)

    async def _broadcast(self, job: Job):
        payload = asdict(job)
        payload["queue"] = self.stats()
        for q in list(self._subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # 慢订阅者丢消息，snapshot 会补

    def stats(self) -> dict:
        vq = [j for j in self.jobs.values()
              if j.kind == JobKind.VIDEO.value and j.state == JobState.QUEUED.value]
        return {
            "video_queued": len(vq),
            "video_eta_s": len(vq) * 70,     # 1 RPM × 0.85 ≈ 70s/个
            "running": sum(1 for j in self.jobs.values() if j.state == JobState.RUNNING.value),
            "failed":  sum(1 for j in self.jobs.values() if j.state == JobState.FAILED.value),
        }

    def list_jobs(self, project: Optional[str] = None, state: Optional[str] = None,
                  limit: int = 300) -> List[dict]:
        items = [asdict(j) for j in self.jobs.values()
                 if (project is None or j.project == project)
                 and (state is None or j.state == state)]
        items.sort(key=lambda x: x["created_at"], reverse=True)
        return items[:limit]
