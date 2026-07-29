# src/core/models.py
# Project / Scene / Shot 三层数据模型。
# 阶段 1（Foundation）：原子单位从 Scene 下沉到 Shot，每个产物都是带
# local + url + status 的小对象，status 落库以支持断点续跑与逐镜重跑。
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
class ImageAsset:
    """图片类产物：支持多候选（红线 12：生成 2 张让用户点选，而非 AI 打分）。
    candidates 为候选 Asset 列表（单张时长度为 1）；selected_index 指向当前选中。
    旧的单值 Asset 语义由 selected / mark_done(single) 兼容。"""
    candidates: List[Asset] = field(default_factory=list)
    selected_index: int = 0
    status: str = ShotStatus.PENDING.value
    error: Optional[str] = None
    updated_at: float = 0.0

    @property
    def selected(self) -> Optional[Asset]:
        if not self.candidates:
            return None
        i = self.selected_index
        if not (0 <= i < len(self.candidates)):
            i = 0
        return self.candidates[i]

    def add_candidate(self, asset: Asset):
        self.candidates.append(asset)
        self.updated_at = time.time()

    def select(self, index: int):
        if 0 <= index < len(self.candidates):
            self.selected_index = index
            self.updated_at = time.time()

    def mark_done(self, local: str, url: Optional[str] = None):
        """单张快捷：作为唯一候选并选中（兼容旧 Asset.mark_done 语义）。"""
        self.candidates = [Asset(local=local, url=url, status=ShotStatus.DONE.value)]
        self.selected_index = 0
        self.status, self.error = ShotStatus.DONE.value, None
        self.updated_at = time.time()

    def mark_failed(self, error: str):
        self.status, self.error = ShotStatus.FAILED.value, str(error)[:500]
        self.updated_at = time.time()

    @property
    def ready(self) -> bool:
        s = self.selected
        return s is not None and s.status == ShotStatus.DONE.value and bool(s.local)

    @property
    def url_ready(self) -> bool:
        """能不能送进视频接口。视频接口只吃公网 URL。"""
        s = self.selected
        return s is not None and s.status == ShotStatus.DONE.value and bool(s.url)

    @classmethod
    def from_dict(cls, d: dict) -> "ImageAsset":
        d = dict(d or {})
        if "candidates" in d:
            cands = [Asset(**{k: v for k, v in c.items() if k in Asset.__dataclass_fields__})
                     for c in (d.get("candidates") or [])]
            return cls(candidates=cands,
                       selected_index=d.get("selected_index", 0),
                       status=d.get("status", ShotStatus.PENDING.value),
                       error=d.get("error"),
                       updated_at=d.get("updated_at", 0.0))
        # 旧形状 {local, url, status, ...}（单值 Asset）-> 单候选
        a = Asset(**{k: v for k, v in d.items() if k in Asset.__dataclass_fields__})
        st = d.get("status", ShotStatus.PENDING.value)
        return cls(candidates=[a] if (a.local or a.url) else [],
                   selected_index=0, status=st, error=d.get("error"),
                   updated_at=d.get("updated_at", 0.0))


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
    first_frame: ImageAsset = field(default_factory=ImageAsset)
    last_frame: ImageAsset  = field(default_factory=ImageAsset)   # 仅 keyframes 模式需要
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
        d["first_frame"] = ImageAsset.from_dict(d.get("first_frame", {}) or {})
        d["last_frame"]  = ImageAsset.from_dict(d.get("last_frame", {}) or {})
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
