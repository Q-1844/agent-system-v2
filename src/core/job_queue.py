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
    last_progress_at: Optional[float] = None   # 看门狗：progress 值上次变化时间


class JobQueue:
    """单进程全局队列。视频串行；图片/音频 3 并发。两条通道互不阻塞。"""

    WATCHDOG_TIMEOUT = 600     # 看门狗：running 无 progress 超时秒数（生产 600=10min，测试可调小）
    WATCHDOG_INTERVAL = 30     # 看门狗检查间隔秒数

    def __init__(self, state_path: Path, handlers: Dict[str, HandlerFn]):
        self.state_path = Path(state_path)
        self.handlers = handlers                      # 外部可变 dict，注册 handler 即增
        self.jobs: Dict[str, Job] = {}
        self._video_q: asyncio.Queue = asyncio.Queue()
        self._light_q: asyncio.Queue = asyncio.Queue()
        self._subs: List[asyncio.Queue] = []
        self._tasks: List[asyncio.Task] = []
        self._running_tasks: Dict[str, asyncio.Task] = {}   # job_id -> 执行 task（force cancel/看门狗中断用）
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
            # Windows 下 os.replace 偶发 PermissionError（文件被短暂占用，如杀软扫描/IO 延迟），重试几次
            for i in range(6):
                try:
                    os.replace(tmp, self.state_path)
                    break
                except PermissionError:
                    if i == 5:
                        raise
                    time.sleep(0.05 * (i + 1))
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

    def cancel(self, job_id: str, force: bool = False) -> bool:
        """取消任务。默认仅取消 queued；force=True 时 running 也标 cancelled + 中断 handler 释放 worker。
        🔴 params 为空的 video job 是正常状态（submit 时不传 video_id，video_id 落 shot.video.video_id），
        不是 bug——job 记录与 params 在 submit() 里是同一次 _save 原子写，不会分裂。
        僵尸成因是 running+孤儿(project/shot已删)+无清理，由 purge_orphans + 看门狗解决。"""
        j = self.jobs.get(job_id)
        if not j:
            return False
        was_running = j.state == JobState.RUNNING.value
        if j.state == JobState.QUEUED.value or (force and was_running):
            j.state = JobState.CANCELLED.value
            j.finished_at = time.time()
            self._save()
            asyncio.create_task(self._broadcast(j))
            if was_running:   # 中断正在执行的 handler，释放 worker
                task = self._running_tasks.get(job_id)
                if task:
                    task.cancel()
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

    def purge_orphans(self, exists_fn) -> int:
        """🔴 清理孤儿 job（用户要求2）：project/shot 已不存在的 running/queued -> cancelled。
        exists_fn(project, shot_id) -> bool。在 start() route 前调用，孤儿不进 worker、不占计数。"""
        n = 0
        for j in list(self.jobs.values()):
            if j.state in (JobState.RUNNING.value, JobState.QUEUED.value):
                if not exists_fn(j.project, j.shot_id):
                    j.state = JobState.CANCELLED.value
                    j.error = "孤儿任务：project/shot 已删除"
                    j.message = "已清理（孤儿）"
                    j.finished_at = time.time()
                    n += 1
        if n:
            self._save()
            log.info("清理 %d 个孤儿 job", n)
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
            job.last_progress_at = time.time()
            self._save(); await self._broadcast(job)
            task = asyncio.create_task(self._run_job(job, name))
            self._running_tasks[job_id] = task
            try:
                await task
            except asyncio.CancelledError:
                pass   # force cancel / 看门狗中断，state 已在 _run_job 设好
            except Exception as e:
                log.exception("[%s] worker 意外错误 %s", name, job_id)
            finally:
                self._running_tasks.pop(job_id, None)
                q.task_done()

    async def _run_job(self, job: Job, name: str):
        """执行单个 job 的 handler，带看门狗。force cancel / 看门狗通过 task.cancel() 中断。"""
        watchdog = asyncio.create_task(self._watchdog(job))
        try:
            handler = self.handlers.get(job.kind)
            if handler is None:
                raise RuntimeError(f"未注册的 job kind: {job.kind}")

            async def emit(stage: str, msg: str, pct: int | None = None):
                job.message = msg or stage
                # 🔴 仅 progress 值变化才刷新 last_progress_at（看门狗据此判卡死）
                if pct is not None and int(pct) != job.progress:
                    job.progress = int(pct)
                    job.last_progress_at = time.time()
                self._save()
                await self._broadcast(job)

            await handler(job, emit)
            if job.state not in (JobState.CANCELLED.value, JobState.FAILED.value):
                job.state, job.progress = JobState.DONE.value, 100
                if not job.message:
                    job.message = "完成"
        except asyncio.CancelledError:
            # force cancel / 看门狗中断：不覆盖已设的 cancelled/failed
            if job.state not in (JobState.CANCELLED.value, JobState.FAILED.value):
                job.state = JobState.CANCELLED.value
            raise
        except Exception as e:
            if job.state not in (JobState.CANCELLED.value, JobState.FAILED.value):
                log.exception("[%s] 任务失败 %s", name, job.job_id)
                job.state = JobState.FAILED.value
                job.error = str(e)[:800]
                job.message = f"失败: {str(e)[:200]}"
        finally:
            watchdog.cancel()
            job.finished_at = time.time()
            self._save()
            asyncio.create_task(self._broadcast(job))

    async def _watchdog(self, job: Job):
        """🔴 看门狗（用户要求3）：running 超 10min 且 progress 无变化 -> 标 failed + 中断 handler 释放 worker。
        progress 值变化时 emit 刷新 last_progress_at；poll 卡死/无 video_id 提交超 10min 触发。
        轻任务一般快，10min 足够；video 正常约 90s 完成，10min 无变化判定卡死。"""
        timeout = self.WATCHDOG_TIMEOUT
        while True:
            await asyncio.sleep(self.WATCHDOG_INTERVAL)
            if job.state != JobState.RUNNING.value:
                return
            base = getattr(job, "last_progress_at", None) or job.started_at or time.time()
            idle = time.time() - base
            if idle > timeout:
                log.warning("[watchdog] job %s (%s) running %ds 无 progress 变化，判定卡死 -> failed",
                            job.job_id, job.kind, int(idle))
                job.state = JobState.FAILED.value
                job.error = f"看门狗：running {int(idle)}s 无 progress 变化，判定卡死"
                job.message = "卡死（看门狗释放 worker）"
                job.finished_at = time.time()
                self._save()
                asyncio.create_task(self._broadcast(job))
                task = self._running_tasks.get(job.job_id)
                if task:
                    task.cancel()
                return

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

    # ---------- AI-4B Autopilot：按 id / origin 查询（幂等防重复提交）----------
    def get(self, job_id: str) -> Optional[Job]:
        """按 job_id 取 Job（None = 不存在）。Executor wait_for_job 用。"""
        return self.jobs.get(job_id)

    def find_by_origin(self, origin_run_id: str,
                       stage_key: Optional[str] = None) -> Optional[Job]:
        """按 Autopilot origin 元数据找 job（§十六 幂等：重启后不重复 submit）。
        扫描 jobs.params：origin_run_id 匹配；stage_key 给定时进一步匹配 autopilot_stage_key。
        返回最新创建的匹配 job（None = 无）。"""
        matches = [j for j in self.jobs.values()
                   if j.params.get("origin_run_id") == origin_run_id
                   and (stage_key is None or j.params.get("autopilot_stage_key") == stage_key)]
        if not matches:
            return None
        return max(matches, key=lambda j: j.created_at)
