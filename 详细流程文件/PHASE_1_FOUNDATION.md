# 阶段 1 · 地基层（Foundation）

> **目标**：把 Scene/Shot 数据模型、全局限流器、配额账本、ImageHost 抽象、Agnes 客户端重写这五件事做完。
> **原则**：本阶段**不碰任何前端文件**，不删除任何现有接口，只新增 + 改写 `agnes_client.py`。
> **前置**：先读 `AGNES_API_FACTS.md`。
> **参考开源项目**：`reference/agnes-video-generator/core/api/rate_limiter.py`（MIT，可直接改写引用）

---

## 1.1 为什么要有这一层（不要跳过这段）

现在的项目把「生成一段视频」当成一个动作，把一致性寄希望于视频模型，把限流当成不存在。这三条在 1 RPM 的现实下每一条都会翻车。地基层解决的是：

1. **原子单位错了** → 项目的最小单位必须是 `Shot`（镜头），不是 `Scene`（场次）。一个场次常需要远景/中景/特写三个镜头。现在 `scene_{id}` 是最小单位，将来加镜头必然要改数据结构、迁移数据、改前端，代价指数级上升。
2. **公网 URL 断链** → 后端跑在 localhost，Agnes 服务端永远访问不到本地文件。任何本地处理（rembg 抠图、裁剪、手动修图）过的图片，**永久失去公网身份**，再也进不了视频接口。
3. **1 RPM** → 不做限流器，"全部生成"就是"全部失败"。

---

## 1.2 目录结构变更

新增（**只新增，不删除现有文件**）：

```
src/
├── core/                          ← 全新目录
│   ├── __init__.py
│   ├── models.py                  ← Project / Scene / Shot 数据模型
│   ├── rate_limiter.py            ← 全局令牌桶限流器
│   ├── quota.py                   ← 每日配额账本
│   ├── image_host.py              ← ImageHost 抽象 + 三种实现
│   └── store.py                   ← 项目数据读写（原子写、迁移）
└── api/
    └── agnes_client.py            ← 【重写】
```

---

## 1.3 数据模型 `src/core/models.py`

### 设计要点

- `Project → Scene[] → Shot[]` 三层
- `shot_id` 格式固定 `s01_c02`（场次 01 的第 2 个镜头），全局唯一、可排序、人类可读
- 每个产物（首帧/尾帧/音频/视频）都是一个带 `local` + `url` + `status` 的小对象，**不是裸字符串路径**
- `status` 落库，40 镜跑到第 33 镜挂了要能只重跑第 33 镜
- `locked` 字段：用户满意的镜头锁定，批量重跑时跳过

### 完整代码

```python
# src/core/models.py
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, List, Dict, Any
import time


class ShotStatus(str, Enum):
    PENDING    = "pending"      # 未开始
    QUEUED     = "queued"       # 已入队，等待限流器放行
    GENERATING = "generating"   # 正在调 API / 轮询中
    DONE       = "done"
    FAILED     = "failed"
    SKIPPED    = "skipped"


class LinkMode(str, Enum):
    KEYFRAMES = "keyframes"   # 首帧+尾帧，服务端插值（同场景连续动作）
    I2V       = "i2v"         # 单图动画化（跨场景硬切，默认）
    NONE      = "none"        # 共用参考图，各镜独立


@dataclass
class Asset:
    """一个生成产物。local 是本地路径，url 是公网 URL（送进 Agnes 用的）。"""
    local: Optional[str] = None
    url: Optional[str] = None
    status: str = ShotStatus.PENDING.value
    error: Optional[str] = None
    updated_at: float = 0.0

    def mark_done(self, local: str, url: Optional[str] = None):
        self.local, self.url = local, url
        self.status, self.error = ShotStatus.DONE.value, None
        self.updated_at = time.time()

    def mark_failed(self, error: str):
        self.status, self.error = ShotStatus.FAILED.value, str(error)[:500]
        self.updated_at = time.time()

    @property
    def ready(self) -> bool:
        return self.status == ShotStatus.DONE.value and bool(self.local)

    @property
    def url_ready(self) -> bool:
        """能不能送进视频接口。视频接口只吃公网 URL。"""
        return self.status == ShotStatus.DONE.value and bool(self.url)


@dataclass
class AudioAsset(Asset):
    duration_ms: int = 0
    # edge-tts WordBoundary 事件：[{"text":"你好","offset_ms":0,"duration_ms":320}, ...]
    words: List[Dict[str, Any]] = field(default_factory=list)
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: str = "+0%"


@dataclass
class VideoAsset(Asset):
    video_id: Optional[str] = None       # Agnes 返回的 video_id
    task_id: Optional[str] = None
    num_frames: int = 0                  # 提交值
    frame_rate: int = 24                 # 提交值
    seconds_returned: Optional[float] = None   # 🔴 服务端回读的真实时长
    size_returned: Optional[str] = None        # 🔴 服务端回读的真实尺寸
    attempts: int = 0


@dataclass
class Shot:
    shot_id: str                    # "s01_c02"
    scene_id: str                    # "s01"
    index: int                       # 场内序号，从 1 开始

    # ---- 创作字段 ----
    description: str = ""            # 中文描述，给人看
    prompt_en: str = ""              # 英文提示词，送 API（可由 LLM 生成，可手改）
    negative_prompt: str = ""
    shot_size: str = "medium shot"   # 景别：wide / medium / close-up / extreme close-up
    camera: str = ""                 # 机位 + 运镜
    lighting: str = ""
    motion: str = ""                 # 画面里什么在动（i2v 的关键）
    characters: List[str] = field(default_factory=list)
    dialogue: str = ""

    # ---- 生成策略 ----
    link_mode: str = LinkMode.I2V.value
    seed: Optional[int] = None

    # ---- 产物 ----
    first_frame: Asset = field(default_factory=Asset)
    last_frame: Asset  = field(default_factory=Asset)   # 仅 keyframes 模式需要
    audio: AudioAsset  = field(default_factory=AudioAsset)
    subtitle_srt: Optional[str] = None
    video: VideoAsset  = field(default_factory=VideoAsset)

    # ---- 状态 ----
    status: str = ShotStatus.PENDING.value
    locked: bool = False             # 锁定后批量操作跳过
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Shot":
        d = dict(d)
        d["first_frame"] = Asset(**d.get("first_frame", {}) or {})
        d["last_frame"]  = Asset(**d.get("last_frame", {}) or {})
        d["audio"]       = AudioAsset(**d.get("audio", {}) or {})
        d["video"]       = VideoAsset(**d.get("video", {}) or {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Scene:
    scene_id: str                    # "s01"
    title: str = ""
    location: str = ""
    time_of_day: str = ""
    summary: str = ""
    shots: List[Shot] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {**{k: v for k, v in asdict(self).items() if k != "shots"},
                "shots": [s.to_dict() for s in self.shots]}

    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        shots = [Shot.from_dict(s) for s in d.get("shots", [])]
        known = {f for f in cls.__dataclass_fields__} - {"shots"}
        return cls(shots=shots, **{k: v for k, v in d.items() if k in known})


@dataclass
class Character:
    name: str
    description_en: str = ""
    # 三视图：{"front": Asset, "side": Asset, "back": Asset}
    views: Dict[str, Asset] = field(default_factory=dict)

    def ref_url(self) -> Optional[str]:
        """取一张能送进 API 的公网 URL，优先 front。"""
        for v in ("front", "side", "back"):
            a = self.views.get(v)
            if a and a.url:
                return a.url
        return None


@dataclass
class StyleAnchor:
    """全局风格锚点。所有 prompt 末尾统一拼接它，保证全片风格一致。"""
    art_style: str = "anime, cinematic composition"
    palette: str = ""
    aspect_ratio: str = "16:9"        # 16:9 / 9:16 / 1:1
    size_tier: str = "1K"             # 固定 1K，见 AGNES_API_FACTS §2.3
    era: str = ""
    extra: str = "high detail, consistent art style"

    def suffix(self) -> str:
        parts = [self.art_style, self.palette, self.era, self.extra]
        return ", ".join(p for p in parts if p)


@dataclass
class Project:
    name: str
    style: StyleAnchor = field(default_factory=StyleAnchor)
    characters: List[Character] = field(default_factory=list)
    scenes: List[Scene] = field(default_factory=list)
    schema_version: int = 2
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def all_shots(self) -> List[Shot]:
        return [s for sc in self.scenes for s in sc.shots]

    def get_shot(self, shot_id: str) -> Optional[Shot]:
        return next((s for s in self.all_shots() if s.shot_id == shot_id), None)

    def get_character(self, name: str) -> Optional[Character]:
        return next((c for c in self.characters if c.name == name), None)
```

### 🔴 v1 → v2 数据迁移

旧的 `parsed_script.json` 是扁平的 `scenes[]`，每个 scene 直接带 `image_path` / `audio_path`。**不许丢数据。** 在 `store.py` 里写迁移函数：

```python
# src/core/store.py  （节选，完整实现见下）
def migrate_v1_to_v2(old: dict) -> dict:
    """把旧的 parsed_script.json 升级为 v2 结构。每个旧 scene 变成 1 个 scene + 1 个 shot。"""
    scenes = []
    for i, old_scene in enumerate(old.get("scenes", []), start=1):
        sid = f"s{i:02d}"
        shot = {
            "shot_id": f"{sid}_c01", "scene_id": sid, "index": 1,
            "description": old_scene.get("description", ""),
            "prompt_en":   old_scene.get("prompt", ""),
            "camera":      old_scene.get("camera", ""),
            "lighting":    old_scene.get("lighting", ""),
            "motion":      old_scene.get("motion", ""),
            "characters":  old_scene.get("characters", []),
            "dialogue":    old_scene.get("dialogue", ""),
            "link_mode":   "i2v",
            # 旧字段挂到新结构上，一个都不丢
            "first_frame": {"local": old_scene.get("image_path"),
                            "url":   old_scene.get("image_url"),
                            "status": "done" if old_scene.get("image_path") else "pending"},
            "audio":       {"local": old_scene.get("audio_path"),
                            "status": "done" if old_scene.get("audio_path") else "pending"},
            "video":       {"local": old_scene.get("video_path"),
                            "status": "done" if old_scene.get("video_path") else "pending"},
            "subtitle_srt": old_scene.get("subtitle_path"),
            "status": "pending",
        }
        scenes.append({"scene_id": sid, "title": old_scene.get("title", ""),
                       "summary": old_scene.get("description", ""), "shots": [shot]})
    return {
        "name": (old.get("project") or {}).get("name") or old.get("project_name") or "untitled",
        "schema_version": 2,
        "style": {"art_style": (old.get("project") or {}).get("style", "anime"),
                  "aspect_ratio": "16:9", "size_tier": "1K"},
        "characters": [
            {"name": c.get("name",""), "description_en": c.get("description",""),
             "views": {k: {"local": v, "status": "done"}
                       for k, v in (c.get("reference_images") or {}).items() if v}}
            for c in old.get("characters", [])
        ],
        "scenes": scenes,
    }
```

**迁移执行规则**：读取时若 `schema_version` 缺失或 < 2，先把原文件复制备份为 `parsed_script.v1.bak.json`，再迁移，再写入 `project.json`。**旧文件保留不删。**

### `store.py` 的原子写

```python
# src/core/store.py
import json, os, shutil, tempfile
from pathlib import Path
from typing import Optional
from .models import Project, Scene, Character, StyleAnchor, Asset

def _atomic_write_json(path: Path, data: dict):
    """原子写：先写临时文件再 rename。防止生成中途崩溃导致 json 半截损坏。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)          # Windows 上 os.replace 也是原子的
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

class ProjectStore:
    def __init__(self, project_dir: Path):
        self.dir = Path(project_dir)
        self.path = self.dir / "output" / "project.json"
        self.legacy = self.dir / "output" / "parsed_script.json"

    def load(self) -> Project:
        raw = None
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        elif self.legacy.exists():
            old = json.loads(self.legacy.read_text(encoding="utf-8"))
            shutil.copy2(self.legacy, self.legacy.with_suffix(".v1.bak.json"))
            raw = migrate_v1_to_v2(old)
            _atomic_write_json(self.path, raw)
        if raw is None:
            raise FileNotFoundError(f"项目数据不存在: {self.dir}")
        if raw.get("schema_version", 1) < 2:
            raw = migrate_v1_to_v2(raw)
            _atomic_write_json(self.path, raw)
        return self._from_dict(raw)

    def save(self, project: Project):
        import time
        project.updated_at = time.time()
        _atomic_write_json(self.path, self._to_dict(project))

    def update_shot(self, shot_id: str, mutator):
        """读-改-写单个 shot。所有生成任务写回都必须走这个方法，禁止直接改内存对象。"""
        p = self.load()
        shot = p.get_shot(shot_id)
        if shot is None:
            raise KeyError(shot_id)
        mutator(shot)
        self.save(p)
        return shot

    # _to_dict / _from_dict 按 models.py 的 to_dict/from_dict 实现，此处略
```

**🔴 硬性规则：所有生成任务在写回结果时必须调用 `store.update_shot()`，禁止「只改内存里的 data 字典」。** 现在项目里 `controller.py` 各阶段不写回 `parsed_script`，就是这个病。

---

## 1.4 限流器 `src/core/rate_limiter.py`

### 设计要点

- **令牌桶**，不是简单 sleep
- **按 bucket 分桶**：`text` / `image_1k` / `image_2k` / `video`
- 全进程单例，`asyncio.Lock` 保护
- 桶容量 = 1（不允许突发），因为视频 1 RPM 突发就是 429
- 加 **0.85 保险系数**：官方标的是"实际 RPM"，边界踩线必挂
- 提供 `estimate_wait(bucket)`，前端排队面板要显示"预计还要等 X 秒"

### 完整代码

```python
# src/core/rate_limiter.py
from __future__ import annotations
import asyncio, time, logging
from dataclasses import dataclass
from typing import Dict

log = logging.getLogger("RateLimiter")

# 依据 AGNES_API_FACTS §4.1（免费/默认用户的"实际 RPM"），再乘 0.85 保险系数
SAFETY = 0.85
DEFAULT_RPM = {
    "text":     20,
    "image_1k": 20,
    "image_2k": 10,
    "image_3k": 1,
    "image_4k": 1,
    "video":    1,     # 🔴 就是 1，不要试图调大
}

@dataclass
class _Bucket:
    rpm: float
    tokens: float
    last: float
    capacity: float = 1.0     # 不允许突发

class RateLimiter:
    """全局令牌桶限流器（进程级单例）。

    用法：
        async with limiter.slot("video"):
            resp = await client.post(...)
    """
    _instance: "RateLimiter | None" = None

    def __init__(self, rpm_map: Dict[str, int] | None = None):
        cfg = {**DEFAULT_RPM, **(rpm_map or {})}
        now = time.monotonic()
        self._buckets = {
            k: _Bucket(rpm=max(v * SAFETY, 0.1), tokens=1.0, last=now)
            for k, v in cfg.items()
        }
        self._lock = asyncio.Lock()
        self._waiting: Dict[str, int] = {k: 0 for k in cfg}

    @classmethod
    def get(cls, rpm_map=None) -> "RateLimiter":
        if cls._instance is None:
            cls._instance = cls(rpm_map)
        return cls._instance

    def _refill(self, b: _Bucket):
        now = time.monotonic()
        b.tokens = min(b.capacity, b.tokens + (now - b.last) * (b.rpm / 60.0))
        b.last = now

    async def acquire(self, bucket: str):
        b = self._buckets.get(bucket)
        if b is None:
            log.warning("未知限流桶 %s，按 text 处理", bucket)
            b = self._buckets["text"]
        self._waiting[bucket] = self._waiting.get(bucket, 0) + 1
        try:
            while True:
                async with self._lock:
                    self._refill(b)
                    if b.tokens >= 1.0:
                        b.tokens -= 1.0
                        return
                    need = (1.0 - b.tokens) / (b.rpm / 60.0)
                wait = min(max(need, 0.05), 5.0)   # 分片等待，便于取消
                log.info("[%s] 限流等待 %.1fs（队列中 %d）", bucket, need, self._waiting[bucket])
                await asyncio.sleep(wait)
        finally:
            self._waiting[bucket] -= 1

    def estimate_wait(self, bucket: str) -> float:
        """给前端排队面板用。返回预计等待秒数（不消耗令牌）。"""
        b = self._buckets.get(bucket)
        if not b:
            return 0.0
        now = time.monotonic()
        tokens = min(b.capacity, b.tokens + (now - b.last) * (b.rpm / 60.0))
        ahead = max(self._waiting.get(bucket, 0), 0)
        per = 60.0 / b.rpm
        return max(0.0, (1.0 - tokens) * per) + ahead * per

    def snapshot(self) -> dict:
        """供 /api/system/limits 接口返回。"""
        return {k: {"rpm": round(b.rpm, 2),
                    "waiting": self._waiting.get(k, 0),
                    "eta_s": round(self.estimate_wait(k), 1)}
                for k, b in self._buckets.items()}

    def slot(self, bucket: str):
        limiter = self
        class _Ctx:
            async def __aenter__(self):
                await limiter.acquire(bucket)
            async def __aexit__(self, *a):
                return False
        return _Ctx()
```

**参考实现**：开源项目的 `reference/agnes-video-generator/core/api/rate_limiter.py` 用的是全局 16 req/min 单桶。**我们的做法更细（分桶），因为它没区分视频 1 RPM 和图片 20 RPM，在视频密集的场景下它其实是踩线的。** 读它的代码学写法，但按上面这份实现。

---

## 1.5 配额账本 `src/core/quota.py`

```python
# src/core/quota.py
from __future__ import annotations
import json, threading
from datetime import date
from pathlib import Path

class QuotaLedger:
    """每日配额账本。视频按秒计，图片按张计，文本按次计。
    落盘保存，进程重启不丢。跨天自动重置。"""

    def __init__(self, path: Path, limits: dict | None = None):
        self.path = Path(path)
        # 免费用户官方未公布确切数值，这里给保守默认值，可在 config.yaml 覆盖
        self.limits = {"video_seconds": 300, "images": 500, "text_calls": 2000,
                       **(limits or {})}
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        today = date.today().isoformat()
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                if d.get("date") == today:
                    return d
            except Exception:
                pass
        return {"date": today, "video_seconds": 0.0, "images": 0, "text_calls": 0}

    def _flush(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    def _rollover(self):
        if self._data.get("date") != date.today().isoformat():
            self._data = {"date": date.today().isoformat(),
                          "video_seconds": 0.0, "images": 0, "text_calls": 0}

    def can(self, kind: str, amount: float = 1) -> tuple[bool, str]:
        with self._lock:
            self._rollover()
            used, cap = self._data.get(kind, 0), self.limits.get(kind, float("inf"))
            if used + amount > cap:
                return False, f"今日 {kind} 配额已用 {used:.0f}/{cap:.0f}，本次需 {amount:.0f}"
            return True, ""

    def consume(self, kind: str, amount: float = 1):
        with self._lock:
            self._rollover()
            self._data[kind] = self._data.get(kind, 0) + amount
            self._flush()

    def snapshot(self) -> dict:
        with self._lock:
            self._rollover()
            return {k: {"used": self._data.get(k, 0), "limit": v}
                    for k, v in self.limits.items()}
```

**接入点**：视频提交前调 `can("video_seconds", seconds)`，成功后调 `consume`。图片同理。**配额不足时，任务标记 `failed` 并给出明确文案，不要静默重试。**

---

## 1.6 ImageHost 抽象 `src/core/image_host.py`

### 🔴 这是本阶段最值钱的一个改动

问题：视频接口只吃公网 URL。你的后端在 localhost。所以：
- 分镜图一旦经过任何本地处理（rembg 抠图、裁剪、手动修图），就**永久失去公网身份**
- Agnes 返回的 `storage.googleapis.com` URL 是否长期有效**未知**，可能昨天能重跑今天就 404

一层接口，几十行代码，把这个风险永久隔离。

```python
# src/core/image_host.py
from __future__ import annotations
import abc, base64, hashlib, logging, mimetypes
from pathlib import Path
from typing import Optional
import httpx

log = logging.getLogger("ImageHost")

class ImageHost(abc.ABC):
    """把本地图片变成 Agnes 服务端能访问的公网 URL。"""

    @abc.abstractmethod
    async def upload(self, local_path: str | Path) -> str: ...

    async def ensure_url(self, local_path: Optional[str],
                         known_url: Optional[str] = None) -> str:
        """优先复用已有 URL；已有 URL 不可达时重新上传。"""
        if known_url and await self._alive(known_url):
            return known_url
        if not local_path:
            raise ValueError("没有本地文件也没有可用 URL，无法生成公网地址")
        return await self.upload(local_path)

    async def _alive(self, url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
                r = await c.head(url)
                if r.status_code == 405:                # 有些存储不支持 HEAD
                    r = await c.get(url, headers={"Range": "bytes=0-0"})
                return r.status_code < 400
        except Exception:
            return False


class AgnesPassthroughHost(ImageHost):
    """默认实现：不上传，只复用 Agnes 生成图时返回的 URL。
    仅当图片**未经任何本地修改**时可用。修过的图会在这里抛错，这是**故意的**——
    让问题在这一层暴露，而不是在视频接口返回一个看不懂的 400。"""

    async def upload(self, local_path):
        raise RuntimeError(
            "当前使用 AgnesPassthroughHost（直通模式），无法为本地文件生成公网 URL。\n"
            "这张图可能经过了本地处理（抠图/裁剪/手动修改），已失去 Agnes 原始 URL。\n"
            "解决办法（三选一）：\n"
            "  1) 在设置里切换 image_host 为 r2 / imgbb / tunnel\n"
            "  2) 重新用 Agnes 生成这张图（不做本地处理）\n"
            "  3) 用 Agnes 图生图跑一次『保持构图不变』把它洗回一个公网 URL"
        )


class ImgbbHost(ImageHost):
    """imgbb 免费图床。注册即得 key，无需信用卡。适合个人项目。"""
    def __init__(self, api_key: str, expiration: int = 0):
        self.api_key, self.expiration = api_key, expiration

    async def upload(self, local_path):
        p = Path(local_path)
        b64 = base64.b64encode(p.read_bytes()).decode()
        params = {"key": self.api_key}
        if self.expiration:
            params["expiration"] = str(self.expiration)
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post("https://api.imgbb.com/1/upload",
                             params=params, data={"image": b64})
            r.raise_for_status()
            url = r.json()["data"]["url"]
        log.info("imgbb 上传成功: %s -> %s", p.name, url)
        return url


class R2Host(ImageHost):
    """Cloudflare R2（S3 兼容）。免费额度大，适合长期项目。需要 boto3。"""
    def __init__(self, account_id, access_key, secret_key, bucket, public_base):
        import boto3
        self.bucket, self.public_base = bucket, public_base.rstrip("/")
        self.s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            region_name="auto")

    async def upload(self, local_path):
        import asyncio
        p = Path(local_path)
        digest = hashlib.sha1(p.read_bytes()).hexdigest()[:16]
        key = f"agnes/{digest}{p.suffix}"
        ctype = mimetypes.guess_type(p.name)[0] or "image/png"
        await asyncio.to_thread(
            self.s3.upload_file, str(p), self.bucket, key,
            ExtraArgs={"ContentType": ctype})
        return f"{self.public_base}/{key}"


class TunnelHost(ImageHost):
    """本地 cloudflared 隧道。零成本、零注册，但隧道断了 URL 就失效。
    适合调试，不适合长跑。"""
    def __init__(self, public_base: str, serve_root: str | Path):
        self.public_base = public_base.rstrip("/")
        self.serve_root = Path(serve_root).resolve()

    async def upload(self, local_path):
        p = Path(local_path).resolve()
        rel = p.relative_to(self.serve_root).as_posix()
        return f"{self.public_base}/{rel}"


def build_image_host(cfg: dict) -> ImageHost:
    kind = (cfg or {}).get("kind", "passthrough")
    if kind == "imgbb":   return ImgbbHost(cfg["api_key"], cfg.get("expiration", 0))
    if kind == "r2":      return R2Host(cfg["account_id"], cfg["access_key"],
                                        cfg["secret_key"], cfg["bucket"], cfg["public_base"])
    if kind == "tunnel":  return TunnelHost(cfg["public_base"], cfg["serve_root"])
    return AgnesPassthroughHost()
```

**config.yaml 新增段**：

```yaml
image_host:
  kind: "passthrough"      # passthrough | imgbb | r2 | tunnel
  # imgbb:
  # api_key: "${IMGBB_API_KEY}"
  # expiration: 0          # 0=永不过期
```

---

## 1.7 重写 `src/api/agnes_client.py`

### 必须实现的 8 件事

1. **共享 httpx 连接池**（现在每次请求新建 AsyncClient，是浪费也是不稳定源）
2. **全错误码指数退避**：408 / 429 / 500 / 502 / 503 / 504 / 520 / 522 / 524
3. **图生图 image 双位置 fallback**（extra_body → 顶层），成功位置进程内缓存
4. **`response_format` 只放 extra_body**
5. **num_frames 8n+1 夹取 + ≤441 校验**
6. **视频轮询 5s，回读 seconds/size**
7. **接入 RateLimiter + QuotaLedger**
8. **size 用档位 + ratio**

### 完整代码

```python
# src/api/agnes_client.py  （整文件替换）
from __future__ import annotations
import asyncio, logging, random
from typing import Any, Dict, List, Optional
import httpx

from src.core.rate_limiter import RateLimiter
from src.core.quota import QuotaLedger

log = logging.getLogger("AgnesClient")

RETRYABLE = {408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}

# AGNES_API_FACTS §2.3
SIZE_TIERS = {
    "1K": {"1:1":"1024x1024","3:4":"864x1152","4:3":"1152x864",
           "16:9":"1312x736","9:16":"736x1312","2:3":"832x1248",
           "3:2":"1248x832","21:9":"1568x672"},
    "2K": {"1:1":"2048x2048","16:9":"2624x1472","9:16":"1472x2624"},
}

def resolve_size(tier: str, ratio: str) -> str:
    return SIZE_TIERS.get(tier, SIZE_TIERS["1K"]).get(ratio, "1312x736")

def snap_num_frames(target_frames: int) -> int:
    """夹到最近的 8n+1，并限制在 [81, 441]。AGNES_API_FACTS §3.4"""
    n = round((target_frames - 1) / 8)
    v = 8 * int(n) + 1
    return max(81, min(441, v))


class AgnesError(Exception):
    def __init__(self, msg, status=None, body=None):
        super().__init__(msg); self.status, self.body = status, body


class AgnesClient:
    def __init__(self, config: dict, quota: QuotaLedger | None = None):
        self.cfg      = config
        self.base_url = config["base_url"].rstrip("/")
        self.api_key  = config["api_key"]
        self.models   = config["models"]
        self.proxy    = config.get("proxy") or None
        self.limiter  = RateLimiter.get(config.get("rpm"))
        self.quota    = quota
        self._client: Optional[httpx.AsyncClient] = None
        self._img_pos: Optional[str] = None      # "extra" | "top"，双位置探测结果缓存

    # ---------- 连接池 ----------
    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                timeout=httpx.Timeout(connect=30, read=360, write=60, pool=30),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                proxy=self.proxy, follow_redirects=True)
        return self._client

    async def aclose(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ---------- 带退避的请求 ----------
    async def _request(self, method: str, url: str, bucket: str,
                       max_attempts: int = 5, **kw) -> httpx.Response:
        last = None
        for attempt in range(max_attempts):
            async with self.limiter.slot(bucket):
                try:
                    c = await self.client()
                    r = await c.request(method, url, **kw)
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last = e
                    log.warning("[%s] 网络异常 %s（第 %d/%d 次）", bucket, e,
                                attempt + 1, max_attempts)
                else:
                    if r.status_code < 400:
                        return r
                    if r.status_code not in RETRYABLE:
                        raise AgnesError(
                            f"Agnes API 错误 {r.status_code}: {r.text[:400]}",
                            r.status_code, r.text)
                    last = AgnesError(f"{r.status_code}: {r.text[:200]}", r.status_code, r.text)
                    log.warning("[%s] 可重试错误 %d（第 %d/%d 次）", bucket,
                                r.status_code, attempt + 1, max_attempts)
            # 退避在限流槽之外做，避免占着令牌睡觉
            backoff = min(60, (2 ** attempt) * 3) + random.uniform(0, 2)
            await asyncio.sleep(backoff)
        raise AgnesError(f"重试 {max_attempts} 次仍失败: {last}")

    # ---------- 文本 ----------
    async def chat(self, messages: List[dict], *, temperature=0.7,
                   max_tokens=2048, thinking=False, json_mode=False) -> str:
        if self.quota:
            ok, why = self.quota.can("text_calls", 1)
            if not ok: raise AgnesError(why)
        body: Dict[str, Any] = {"model": self.models["text"], "messages": messages,
                                "temperature": temperature, "max_tokens": max_tokens}
        if thinking:
            body["chat_template_kwargs"] = {"enable_thinking": True}
        r = await self._request("POST", "/v1/chat/completions", "text", json=body)
        if self.quota: self.quota.consume("text_calls", 1)
        text = r.json()["choices"][0]["message"]["content"]
        if json_mode:
            text = text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"): text = text[4:]
            return text.strip()
        return text

    # ---------- 图片 ----------
    async def generate_image(self, prompt: str, *,
                             image_refs: Optional[List[str]] = None,
                             size_tier: str = "1K", ratio: str = "16:9",
                             seed: Optional[int] = None,
                             want_b64: bool = False) -> dict:
        """返回 {"url": str|None, "b64": str|None}。
        image_refs 支持公网 URL 或 data:image/png;base64,... """
        if self.quota:
            ok, why = self.quota.can("images", 1)
            if not ok: raise AgnesError(why)

        bucket = f"image_{size_tier.lower()}"
        size = resolve_size(size_tier, ratio)
        is_i2i = bool(image_refs)

        def build(pos: str) -> dict:
            b: Dict[str, Any] = {"model": self.models["image"],
                                 "prompt": prompt, "size": size}
            eb: Dict[str, Any] = {}
            # 🔴 response_format 只能在 extra_body（AGNES_API_FACTS §2.2 坑1）
            if is_i2i:
                eb["response_format"] = "b64_json" if want_b64 else "url"
                if pos == "extra": eb["image"] = image_refs
                else:              b["image"]  = image_refs
            else:
                if want_b64: b["return_base64"] = True
                else:        eb["response_format"] = "url"
            if seed is not None: b["seed"] = seed
            if eb: b["extra_body"] = eb
            return b

        positions = ["extra", "top"] if is_i2i else ["extra"]
        if self._img_pos in positions:
            positions = [self._img_pos] + [p for p in positions if p != self._img_pos]

        last_err = None
        for pos in positions:
            try:
                r = await self._request("POST", "/v1/images/generations",
                                        bucket, json=build(pos))
            except AgnesError as e:
                # 只有 400 才试另一个位置——其它错误换位置也没用
                if e.status == 400 and pos != positions[-1]:
                    log.warning("图生图 image 放在 %s 返回 400，尝试另一位置", pos)
                    last_err = e; continue
                raise
            if is_i2i:
                self._img_pos = pos
                log.info("图生图 image 位置确定为: %s", pos)
            if self.quota: self.quota.consume("images", 1)
            d = r.json()["data"][0]
            return {"url": d.get("url"), "b64": d.get("b64_json")}
        raise last_err or AgnesError("图片生成失败")

    # ---------- 视频 ----------
    async def create_video(self, prompt: str, *,
                           mode: str = "i2v",
                           image_url: Optional[str] = None,
                           image_urls: Optional[List[str]] = None,
                           num_frames: int = 121, frame_rate: int = 24,
                           width: int = 1312, height: int = 736,
                           seed: Optional[int] = None,
                           negative_prompt: str = "") -> dict:
        """提交视频任务。mode: 't2v' | 'i2v' | 'keyframes' | 'multi'
        返回创建响应（含 video_id / task_id / seconds / size）。"""
        num_frames = snap_num_frames(num_frames)
        seconds = num_frames / max(frame_rate, 1)
        if self.quota:
            ok, why = self.quota.can("video_seconds", seconds)
            if not ok: raise AgnesError(why)

        body: Dict[str, Any] = {"model": self.models["video"], "prompt": prompt,
                                "num_frames": num_frames, "frame_rate": frame_rate,
                                "width": width, "height": height}
        if seed is not None: body["seed"] = seed
        if negative_prompt:  body["negative_prompt"] = negative_prompt

        if mode == "i2v":
            if not image_url: raise AgnesError("i2v 模式必须提供 image_url（公网 URL）")
            body["image"] = image_url                      # 顶层字符串
        elif mode == "keyframes":
            if not image_urls or len(image_urls) < 2:
                raise AgnesError("keyframes 模式需要至少 2 张图（首帧+尾帧）")
            body["extra_body"] = {"image": image_urls, "mode": "keyframes"}
        elif mode == "multi":
            body["extra_body"] = {"image": image_urls or []}
        # t2v 不传图

        r = await self._request("POST", "/v1/videos", "video", json=body)
        data = r.json()
        if self.quota: self.quota.consume("video_seconds", seconds)
        log.info("视频任务已提交 video_id=%s seconds=%s size=%s",
                 data.get("video_id"), data.get("seconds"), data.get("size"))
        return data

    async def poll_video(self, video_id: str, *,
                         interval: float = 5.0, max_wait: float = 3600,
                         on_progress=None) -> dict:
        """轮询直到 completed / failed。官方建议间隔 5s（AGNES_API_FACTS §3.5）。
        注意：轮询走 text 桶，不占视频桶——否则第二个任务永远提交不了。"""
        waited = 0.0
        while waited < max_wait:
            await asyncio.sleep(interval); waited += interval
            r = await self._request("GET", "/agnesapi", "text",
                                    params={"video_id": video_id,
                                            "model_name": self.models["video"]},
                                    max_attempts=3)
            d = r.json()
            st = d.get("status")
            if on_progress:
                await _maybe_await(on_progress(d.get("progress", 0), st, waited))
            if st == "completed":
                # 🔴 URL 走 fallback 链：remixed_from_video_id -> url -> video_url
                # （.cn 直生成任务 remixed_from_video_id 为 null，真正产物地址在 url 字段）
                url = (d.get("remixed_from_video_id")
                       or d.get("url")
                       or d.get("video_url"))
                if not url: raise AgnesError(f"任务完成但没有视频 URL: {d}")
                return {"url": url,
                        "seconds": float(d.get("seconds") or 0),   # 🔴 回读
                        "size": d.get("size"), "raw": d}
            if st == "failed":
                raise AgnesError(f"视频生成失败: {d.get('error')}")
        raise AgnesError(f"视频生成超时（{max_wait}s），video_id={video_id}")

    async def download(self, url: str, dest) -> str:
        from pathlib import Path
        dest = Path(dest); dest.parent.mkdir(parents=True, exist_ok=True)
        c = await self.client()
        async with c.stream("GET", url) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in r.aiter_bytes(1 << 16):
                    f.write(chunk)
        return str(dest)


async def _maybe_await(x):
    if asyncio.iscoroutine(x): return await x
    return x
```

---

## 1.8 新增只读接口（前端阶段 4 要用）

在 `api_server.py` 里加两个接口，**不改任何现有接口**：

```python
@app.get("/api/system/limits")
async def system_limits():
    return {"limits": RateLimiter.get().snapshot(),
            "quota": quota_ledger.snapshot()}
```

---

## 1.9 config.yaml 变更

```yaml
agnes:
  base_url: "https://apihub.agnes-ai.cn"   # 国内入口（2026-07-29 上线，直连无需代理）；国际站 .com 需代理
  api_key: ${AGNES_API_KEY}
  proxy: "http://127.0.0.1:7890"
  models:
    text:  "agnes-2.0-flash"
    image: "agnes-image-2.1-flash"
    video: "agnes-video-v2.0"
  rpm:                    # 覆盖限流器默认值；一般不用改
    text: 20
    image_1k: 20
    video: 1              # 🔴 免费用户就是 1，别调大
  image:
    size_tier: "1K"       # 🔴 从 1152x768 改为档位制
    ratio: "16:9"
    timeout: 300
  video:
    frame_rate: 24
    polling_interval: 5   # 🔴 从 10 改为 5
    max_wait: 3600

image_host:
  kind: "passthrough"

quota:
  video_seconds: 300
  images: 500
  text_calls: 2000
```

---

## 1.10 阶段 1 验收清单

逐条跑通才算完成，**每条都要有实际输出证据，不许只说"应该没问题"**：

- [ ] `python -c "from src.core.models import Project; print('ok')"` 无报错
- [ ] 拿一个现有项目的 `parsed_script.json`，跑 `ProjectStore.load()`，检查：
  - [ ] 生成了 `project.json`，`schema_version == 2`
  - [ ] 生成了 `parsed_script.v1.bak.json` 备份
  - [ ] 每个旧 scene 变成 1 scene + 1 shot，`image_path` 落到了 `first_frame.local`
  - [ ] **原文件没被删除**
- [ ] 限流器单测：连续 `acquire("video")` 3 次，打印时间戳，间隔应 ≈ 70s（1 RPM × 0.85）
- [ ] 限流器单测：连续 `acquire("image_1k")` 5 次，总耗时应 < 20s
- [ ] `snap_num_frames(120) == 121`；`snap_num_frames(500) == 441`；`snap_num_frames(10) == 81`
- [ ] `resolve_size("1K","16:9") == "1312x736"`
- [ ] 配额账本：`consume("video_seconds", 100)` 三次后 `can("video_seconds", 50)` 返回 False
- [ ] `GET /api/system/limits` 返回正确 JSON
- [ ] **旧接口全部仍然可用**（`/api/project/{p}/script`、`/character/bind`、`/audio/{id}/generate`、`/compose` 逐个手测）

### Agnes 联网可用时的额外验收

- [ ] `generate_image` 文生图 → 拿到 `data[0].url`
- [ ] `generate_image` 图生图（传一个公网 URL）→ 日志打印出「图生图 image 位置确定为: extra 或 top」
- [ ] `create_video` t2v 模式 → 拿到 video_id
- [ ] `poll_video` → 走 fallback 链（`remixed_from_video_id` -> `url` -> `video_url`）拿到视频 URL（`.cn` 直生成任务 `remixed_from_video_id` 为 null，URL 在 `url` 字段），且 `seconds` 与提交的 `num_frames/frame_rate` 对得上

