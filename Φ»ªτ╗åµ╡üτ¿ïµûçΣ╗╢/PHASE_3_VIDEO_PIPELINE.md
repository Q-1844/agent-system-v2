# 阶段 3 · 视频生成流水线（Video Pipeline）

> **目标**：分镜图 → 尾帧 → 镜头视频，三种衔接模式，全局串行任务队列，断点续跑，SSE 实时进度。
> **前置**：阶段 1、2 全部验收通过。
> **参考开源项目**：
> - `reference/agnes-video-generator/core/pipelines/creative_video.py`（10 步编排）
> - `reference/agnes-video-generator/core/task_manager.py`（断点续跑状态持久化）
> - `reference/agnes-video-generator/core/api/agnes_video.py`（视频接口封装）

---

## 3.1 三种衔接模式（link_mode）

这是长视频连贯性的核心，也是现在项目**完全没有**的东西。现在只打算做 ti2vid 单图动画 —— 那是「PPT 动起来」，不是「剧情片」。

| 模式 | 做法 | 用在哪 | Agnes 调用形态 |
|---|---|---|---|
| `keyframes` | 本镜首帧 + 本镜尾帧，服务端插值 | **同场景内连续动作**（推荐） | `extra_body.image=[first,last]` + `extra_body.mode="keyframes"` |
| `i2v` | 单张分镜图动画化 | **跨场景硬切**（默认） | 顶层 `image="<url>"` |
| `none` | 全片共用一张参考图 | 快速出片、各镜独立 | 顶层 `image="<共用参考图>"` |

### 🔴 自动判定规则（写成函数，但允许用户手动覆盖）

```python
# src/core/link_mode.py
from src.core.models import Shot, LinkMode

def auto_link_mode(prev: Shot | None, cur: Shot) -> str:
    """自动推荐衔接模式。用户可在前端手动覆盖。"""
    if prev is None:
        return LinkMode.I2V.value                # 首镜没有上文
    if prev.scene_id != cur.scene_id:
        return LinkMode.I2V.value                # 🔴 跨场景必须硬切
    # 同场景内：角色集合有交集 → 连续动作 → 用关键帧
    if set(prev.characters) & set(cur.characters):
        return LinkMode.KEYFRAMES.value
    return LinkMode.I2V.value
```

**🔴 跨场景绝对不要强行接力。** 强行用上一场的尾帧去接下一场的首帧，会产生诡异的融化过渡（画面像流体一样变形），观感比硬切差得多。接力只适用于同场景的动作延续。

---

## 3.2 分镜图生成（first_frame）

### Prompt 组装规则

```python
# src/core/prompt_builder.py
from src.core.models import Shot, Project

def build_shot_prompt(project: Project, shot: Shot) -> str:
    """组装分镜图的英文 prompt。
    结构参考 Agnes 生图文档推荐：
    [主体] + [场景/环境] + [风格] + [光照] + [构图] + [质量要求]"""
    if shot.prompt_en:                  # 用户手改过就直接用，不覆盖
        base = shot.prompt_en
    else:
        chars = ", ".join(
            c.description_en for c in project.characters
            if c.name in shot.characters and c.description_en)
        base = ", ".join(p for p in [
            shot.description, chars, shot.shot_size,
            shot.camera, shot.lighting] if p)
    # 🔴 风格锚点全局唯一，所有 prompt 统一拼它 —— 这是全片风格一致的地基
    return f"{base}, {project.style.suffix()}"

def build_i2i_prompt(project: Project, shot: Shot) -> str:
    """图生图时必须同时说明「改什么」和「保留什么」，
    这是 Agnes 生图文档明确的最佳实践。"""
    return (f"{build_shot_prompt(project, shot)}. "
            f"Keep the character identity, outfit, and art style from the "
            f"reference image strictly consistent.")

def build_last_frame_prompt(project: Project, shot: Shot) -> str:
    """尾帧 = 首帧 + 动作推进后的状态。必须强调构图和角色不变。"""
    motion = shot.motion or "the action continues naturally"
    return (f"Same scene and same character as the reference image, "
            f"but after this motion: {motion}. "
            f"Preserve the original composition, camera angle, lighting, "
            f"character identity and outfit. {project.style.suffix()}")

def build_video_prompt(shot: Shot, mode: str) -> str:
    """视频 prompt。i2v 要说明「什么动、什么不动」；
    keyframes 要说明「两帧之间怎么过渡」。见 Agnes 视频文档 Prompt 最佳实践。"""
    motion = shot.motion or "subtle natural motion"
    if mode == "keyframes":
        return (f"Create a smooth cinematic transition from the first keyframe "
                f"to the second keyframe. {motion}. Maintain character identity, "
                f"consistent camera angle and natural motion between frames.")
    return (f"Animate this image: {motion}. {shot.camera or 'subtle camera movement'}. "
            f"Keep the character's face, outfit and the overall composition "
            f"consistent and stable.")
```

### 接口

```
POST /api/project/{p}/shot/{shot_id}/first-frame
Body: {"variants": 2, "seed": null, "force": false}
```

流程：

1. Shot 已 `locked` 且 `force != true` → 直接跳过
2. 取出场角色的参考图 URL（`Character.ref_url()`）
   - 有 URL → 走图生图（`image_refs=[url]`）
   - 只有本地文件 → 通过 `ImageHost` 转 data URI（**图片接口支持 base64，见 AGNES_API_FACTS §2.4**）
   - 都没有 → 走文生图
3. **图片 20 RPM，可以抽卡**：`variants=2` 时生成 2 张
4. 🔴 **不要用 LLM 打分自动择优**。用 `agnes-2.0-flash` 给图片打分的可靠性存疑，而且多一次 API 调用。**改成生成 2 张并排让用户点选** —— 比 AI 打分更快也更准
5. 下载存 `output/frames/{shot_id}_first_v{n}.png`
6. **同时保存 Agnes 返回的公网 URL** 到 `first_frame.url`（🔴 这是视频接口的唯一入口，丢了就废了）
7. 写回 `store.update_shot`

### 候选选择接口

```
POST /api/project/{p}/shot/{shot_id}/first-frame/choose
Body: {"variant": 1}
```

---

## 3.3 尾帧生成（last_frame）— 全新环节

**只有 `link_mode == "keyframes"` 的镜头需要尾帧。**

```
POST /api/project/{p}/shot/{shot_id}/last-frame
```

流程：

1. 检查 `link_mode == "keyframes"`，否则返回 400
2. 前置条件：`first_frame.url_ready == True`
3. 用 `build_last_frame_prompt` 做**图生图**，参考图 = `first_frame.url`
4. 存 `output/frames/{shot_id}_last.png` + `last_frame.url`

开源项目把「尾帧提示词生成」和「尾帧图生成」拆成了两个独立步骤（10 步流水线里的第 7、8 步）。**我们也这么拆**，因为尾帧提示词是最需要人工调的地方，分开才能只重跑其中一步。

---

## 3.4 镜头视频生成

```
POST /api/project/{p}/shot/{shot_id}/video
```

### 前置校验（缺一不可，全部不通过就别提交，别浪费那 60 秒）

```python
def preflight(project, shot) -> tuple[bool, str]:
    if shot.locked:
        return False, "镜头已锁定"
    if not shot.video.num_frames:
        return False, "尚未配音，无法确定视频时长（请先跑阶段 2 的配音）"
    if shot.link_mode == "keyframes":
        if not shot.first_frame.url_ready:
            return False, "首帧缺少公网 URL（检查 ImageHost 配置）"
        if not shot.last_frame.url_ready:
            return False, "keyframes 模式需要尾帧，请先生成尾帧"
    elif shot.link_mode in ("i2v", "none"):
        if not shot.first_frame.url_ready:
            return False, "首帧缺少公网 URL（检查 ImageHost 配置）"
    seconds = shot.video.num_frames / shot.video.frame_rate
    ok, why = quota.can("video_seconds", seconds)
    if not ok:
        return False, why
    return True, ""
```

### 执行

```python
async def generate_shot_video(store, client, project, shot, emit):
    mode = {"keyframes": "keyframes", "i2v": "i2v", "none": "i2v"}[shot.link_mode]
    prompt = build_video_prompt(shot, mode)
    w, h = map(int, resolve_size(project.style.size_tier,
                                 project.style.aspect_ratio).split("x"))

    kwargs = dict(num_frames=shot.video.num_frames,
                  frame_rate=shot.video.frame_rate,
                  width=w, height=h, seed=shot.seed,
                  negative_prompt=shot.negative_prompt)
    if mode == "keyframes":
        kwargs["image_urls"] = [shot.first_frame.url, shot.last_frame.url]
    else:
        kwargs["image_url"] = shot.first_frame.url

    # ---- 提交 ----
    store.update_shot(shot.shot_id, lambda s: setattr(s.video, "status", "generating"))
    await emit("submitting", f"{shot.shot_id} 正在提交（视频 1 RPM，可能等待约 60s）")
    created = await client.create_video(prompt, mode=mode, **kwargs)

    # 🔴 提交成功立刻落库 video_id —— 这样即使进程崩了，重启后也能接着轮询，
    #    不需要重新提交（重新提交要再等 60s，还要再扣一次配额）
    store.update_shot(shot.shot_id, lambda s: (
        setattr(s.video, "video_id", created.get("video_id")),
        setattr(s.video, "task_id",  created.get("task_id")),
        setattr(s.video, "attempts", s.video.attempts + 1)))

    # ---- 轮询 ----
    async def on_prog(pct, st, waited):
        await emit("polling", f"{shot.shot_id} {st} {pct}%（已等 {int(waited)}s）", pct)

    res = await client.poll_video(created["video_id"], interval=5, on_progress=on_prog)

    # ---- 下载 ----
    dest = f"projects/{project.name}/output/videos/{shot.shot_id}.mp4"
    await client.download(res["url"], dest)

    # ---- 🔴 回读入库（AGNES_API_FACTS §3.6）----
    store.update_shot(shot.shot_id, lambda s: (
        s.video.mark_done(dest, res["url"]),
        setattr(s.video, "seconds_returned", res["seconds"]),
        setattr(s.video, "size_returned", res["size"])))
    await emit("done", f"{shot.shot_id} 完成，实际 {res['seconds']}s / {res['size']}")
```

### 🔴 崩溃恢复：先查有没有在跑的任务

进程重启后，对每个 `video.status == "generating"` 且 `video.video_id` 非空的 Shot，**先去 `poll_video` 查一次**，不要盲目重新提交。

```python
async def recover_pending_videos(store, client):
    p = store.load()
    for shot in p.all_shots():
        if shot.video.status == "generating" and shot.video.video_id:
            try:
                res = await client.poll_video(shot.video.video_id, interval=5, max_wait=60)
                # 已经好了，直接下载入库
            except Exception:
                # 还在跑或已失效，重新入队
                shot.video.status = "queued"
```

---

## 3.5 全局任务队列

### 为什么必须有

视频 1 RPM。前端点"全部生成"，20 个场景并发提交，结果是 19 个 429。

### 设计

```python
# src/core/job_queue.py
from __future__ import annotations
import asyncio, json, time, uuid, logging
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
    """单进程串行队列。视频任务严格串行；图片/音频任务可小并发。

    🔴 关键设计：视频用独立的串行 worker（并发=1），
    图片/音频用另一个 worker（并发=3）。两条通道互不阻塞——
    否则一个视频任务卡 5 分钟，后面所有配音都动不了。
    """

    def __init__(self, state_path: Path, handlers: Dict[str, Callable[..., Awaitable]]):
        self.state_path = Path(state_path)
        self.handlers = handlers
        self.jobs: Dict[str, Job] = {}
        self._video_q: asyncio.Queue[str] = asyncio.Queue()
        self._light_q: asyncio.Queue[str] = asyncio.Queue()
        self._subs: List[asyncio.Queue] = []
        self._tasks: List[asyncio.Task] = []
        self._load()

    # ---------- 持久化 ----------
    def _load(self):
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.jobs = {k: Job(**v) for k, v in data.items()}
                # 🔴 重启后把 running 的任务重置为 queued（除了视频——视频交给 recover 处理）
                for j in self.jobs.values():
                    if j.state == JobState.RUNNING.value and j.kind != JobKind.VIDEO.value:
                        j.state = JobState.QUEUED.value
            except Exception as e:
                log.warning("队列状态读取失败，从空开始: %s", e)

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps({k: asdict(v) for k, v in self.jobs.items()},
                       ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- 入队 ----------
    def submit(self, project: str, kind: str, shot_id=None, **params) -> Job:
        job = Job(job_id=uuid.uuid4().hex[:12], project=project,
                  kind=kind, shot_id=shot_id, params=params)
        self.jobs[job.job_id] = job
        self._save()
        (self._video_q if kind == JobKind.VIDEO.value else self._light_q).put_nowait(job.job_id)
        asyncio.create_task(self._broadcast(job))
        return job

    def submit_batch(self, project: str, kind: str, shot_ids: List[str], **params) -> List[Job]:
        return [self.submit(project, kind, sid, **params) for sid in shot_ids]

    def cancel(self, job_id: str) -> bool:
        j = self.jobs.get(job_id)
        if j and j.state == JobState.QUEUED.value:
            j.state = JobState.CANCELLED.value
            self._save(); return True
        return False

    # ---------- worker ----------
    async def start(self):
        self._tasks = [
            asyncio.create_task(self._worker(self._video_q, "video-worker")),
            *[asyncio.create_task(self._worker(self._light_q, f"light-worker-{i}"))
              for i in range(3)],
        ]
        # 重启后把残留的 queued 任务重新推进队列
        for j in self.jobs.values():
            if j.state == JobState.QUEUED.value:
                (self._video_q if j.kind == JobKind.VIDEO.value
                 else self._light_q).put_nowait(j.job_id)

    async def _worker(self, q: asyncio.Queue, name: str):
        while True:
            job_id = await q.get()
            job = self.jobs.get(job_id)
            if not job or job.state != JobState.QUEUED.value:
                q.task_done(); continue
            job.state = JobState.RUNNING.value
            job.started_at = time.time(); job.attempts += 1
            self._save(); await self._broadcast(job)
            try:
                handler = self.handlers[job.kind]

                async def emit(stage: str, msg: str, pct: int | None = None):
                    job.message = msg
                    if pct is not None: job.progress = int(pct)
                    self._save(); await self._broadcast(job)

                await handler(job, emit)
                job.state, job.progress = JobState.DONE.value, 100
            except Exception as e:
                log.exception("[%s] 任务失败 %s", name, job_id)
                job.state, job.error = JobState.FAILED.value, str(e)[:800]
                job.message = f"失败: {str(e)[:200]}"
            finally:
                job.finished_at = time.time()
                self._save(); await self._broadcast(job)
                q.task_done()

    # ---------- SSE ----------
    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=200); self._subs.append(q); return q

    def unsubscribe(self, q):
        if q in self._subs: self._subs.remove(q)

    async def _broadcast(self, job: Job):
        payload = asdict(job)
        payload["queue"] = self.stats()
        for q in list(self._subs):
            try: q.put_nowait(payload)
            except asyncio.QueueFull: pass

    def stats(self) -> dict:
        vq = [j for j in self.jobs.values()
              if j.kind == JobKind.VIDEO.value and j.state == JobState.QUEUED.value]
        return {
            "video_queued": len(vq),
            "video_eta_s": len(vq) * 70,     # 1 RPM × 0.85 ≈ 70s/个
            "running": sum(1 for j in self.jobs.values() if j.state == JobState.RUNNING.value),
            "failed":  sum(1 for j in self.jobs.values() if j.state == JobState.FAILED.value),
        }

    def list_jobs(self, project=None, limit=200) -> List[dict]:
        items = [asdict(j) for j in self.jobs.values()
                 if project is None or j.project == project]
        items.sort(key=lambda x: x["created_at"], reverse=True)
        return items[:limit]

    def retry(self, job_id: str) -> Optional[Job]:
        j = self.jobs.get(job_id)
        if not j or j.state not in (JobState.FAILED.value, JobState.CANCELLED.value):
            return None
        j.state, j.error, j.progress = JobState.QUEUED.value, None, 0
        self._save()
        (self._video_q if j.kind == JobKind.VIDEO.value else self._light_q).put_nowait(job_id)
        return j
```

### 队列相关接口

```
GET    /api/project/{p}/jobs                列出任务（支持 ?state=failed）
GET    /api/jobs/stream                     全局 SSE（替代按任务订阅）
POST   /api/jobs/{job_id}/cancel
POST   /api/jobs/{job_id}/retry
POST   /api/project/{p}/jobs/retry-failed   一键重跑所有失败
DELETE /api/project/{p}/jobs/finished       清理已完成记录
```

### 🔴 SSE 改为全局单连接

现在项目是「每个任务开一个 SSE 连接」。改成**一个项目一条全局 SSE**，推送所有任务状态变化。理由：20 个镜头 = 20 条 SSE 连接，浏览器有 6 个并发连接上限，会直接卡死。

```python
@app.get("/api/jobs/stream")
async def jobs_stream(request: Request, project: str | None = None):
    async def gen():
        q = job_queue.subscribe()
        try:
            yield f"event: snapshot\ndata: {json.dumps(job_queue.list_jobs(project))}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15)
                    if project and payload.get("project") != project:
                        continue
                    yield f"event: job\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"      # 防代理超时断流
        finally:
            job_queue.unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
```

---

## 3.6 编排器（替代 `/api/run`）

### 🔴 废弃 `/api/run`，改成 orchestrator

现在的 `/api/run` 走 `controller.py` 六阶段，各阶段不写回 `parsed_script`。**不要去修 controller 让它写回 —— 那是给一套重复逻辑打补丁。**

正确做法：写一个 orchestrator，内部逐个调**已经写回正确的单镜头接口**。白捡断点续跑，同时删掉一整套重复逻辑。

```
POST /api/project/{p}/orchestrate
Body: {
  "stages": ["first_frame","audio","last_frame","video","compose"],
  "scope": "all" | "pending" | ["s01_c01", ...],
  "skip_locked": true,
  "dry_run": false
}
```

编排逻辑：

```python
async def orchestrate(store, queue, project_name, stages, scope, skip_locked):
    p = store.load()
    shots = select_shots(p, scope, skip_locked)

    plan = []
    # 🔴 顺序不可调换：分镜图 → 配音（拿时长）→ 尾帧 → 视频 → 合成
    if "first_frame" in stages:
        plan += [("first_frame", s.shot_id) for s in shots
                 if not s.first_frame.ready]
    if "audio" in stages:
        plan += [("audio", s.shot_id) for s in shots
                 if s.dialogue and not s.audio.ready]
    if "last_frame" in stages:
        plan += [("last_frame", s.shot_id) for s in shots
                 if s.link_mode == "keyframes" and not s.last_frame.ready]
    if "video" in stages:
        plan += [("video", s.shot_id) for s in shots if not s.video.ready]

    if dry_run:
        n_video = sum(1 for k, _ in plan if k == "video")
        return {"plan": plan, "video_count": n_video,
                "eta_minutes": round(n_video * 70 / 60, 1),
                "video_seconds_needed": sum(...)}

    for kind, sid in plan:
        queue.submit(project_name, kind, sid)
    if "compose" in stages:
        queue.submit(project_name, "compose")
```

**🔴 `dry_run` 必须实现。** 用户点"全部生成"之前，要能看到「本次将生成 18 个视频，预计 21 分钟，消耗 96 秒配额」。1 RPM 的现实下，这个预估是产品的灵魂。

---

## 3.7 合成阶段改造

`VideoComposer` 改为三步（见阶段 2 §2.6）：

1. **拼视频**：每个 Shot 有 `video.local` 用视频；否则用 `first_frame.local` 做 Ken Burns 兜底，时长用 `video.num_frames/frame_rate`
   - 统一尺寸/帧率/像素格式后再 concat，避免 concat demuxer 因参数不一致失败
2. **叠音轨**：按 §2.6 的 `build_audio_track` 生成全片一条音轨，直接 `-i audio.wav -map 0:v -map 1:a`
3. **烧字幕**（可选）：全片一条 SRT，`-vf subtitles=narration.srt:force_style='...'`
   - 🔴 Windows 下 ffmpeg 的 subtitles filter 对路径极其挑剔：盘符冒号要转义成 `\:`，反斜杠要转成正斜杠。写一个 `escape_ffmpeg_path()` 处理，别硬拼
   - 中文字幕需要指定字体，用 `force_style='FontName=Microsoft YaHei'`；开源项目的做法是**在项目里自带 CJK 字体**（`resource/fonts/`），可以照抄这个思路避免用户环境缺字体

---

## 3.8 阶段 3 验收清单

- [ ] `auto_link_mode`：跨场景返回 `i2v`；同场景同角色返回 `keyframes`
- [ ] 分镜图生成 2 个候选，前端能选，选中后 `first_frame.url` 正确写回
- [ ] `first_frame.url` 是 `storage.googleapis.com` 开头的公网 URL（**不是本地路径**）
- [ ] 尾帧接口：`link_mode != keyframes` 时返回 400 并说明原因
- [ ] preflight 校验：未配音的镜头提交视频 → 明确报错「尚未配音」，**不发起 API 请求**
- [ ] **提交 3 个视频任务，观察日志**：三次提交时间戳间隔 ≈ 70s，无 429
- [ ] 提交后立刻 kill 后端进程 → 重启 → `recover_pending_videos` 能接着轮询，**没有重新提交**
- [ ] 视频完成后 `seconds_returned` / `size_returned` 已写入 `project.json`
- [ ] 一个视频任务失败 → job 状态 `failed` + 错误信息可读 → 点重试能单独重跑，**其它已完成镜头不受影响**
- [ ] `/api/jobs/stream` 单条 SSE 能收到所有任务的状态变化
- [ ] `dry_run` 返回的视频数量、预计时间、配额消耗与实际一致
- [ ] 合成：视频段 + Ken Burns 段混合的项目能正常出片，音画对齐
- [ ] 字幕烧录在 Windows 路径含中文和空格时不报错

