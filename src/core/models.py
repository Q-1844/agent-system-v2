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
    # 34.md §13：multi 单参考直生（v2.0 ti2vid 单图槽，31/32/33.md 真机验证）——
    # 参考图是身份/场景语义参考而非首帧（不做 first-frame conditioning）。仅
    # BOUND 路径可用（参考由 Binding 决定，§12）；provider 能力见 agnes_adapter。
    MULTI     = "multi"


class OpeningSource(str, Enum):
    """镜头开场画面来源（Phase 6A）。与 link_mode 独立：
    link_mode 控制「镜头内部如何生成视频」，opening_source 控制「开场画面从哪来」。"""
    INDEPENDENT         = "independent"          # 独立生成
    REFERENCE_PREVIOUS  = "reference_previous"   # 参考上一镜末帧生成新首帧候选
    INHERIT_PREVIOUS    = "inherit_previous"     # 直接沿用上一镜实际视频末帧作开场


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
        """能不能送进视频接口。视频接口只吃公网 URL。
        🔴 必须已确认（self.status==done）：first_frame 生成 2 候选后 status=pending
        （等用户点选），此时 url_ready=False 挡住视频/尾帧；choose 后 status=done 才放行。
        last_frame 用 mark_done 直接置 done，故生成完即 url_ready=True。"""
        if self.status != ShotStatus.DONE.value:
            return False
        s = self.selected
        return s is not None and bool(s.url)

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
    # 生成所用视频模型（2026-09-06 新契约接入）。None=旧数据/config 默认（v2.0）——
    # 恢复/续轮询必须用同一 model_name 轮询，否则 2.5 任务会查不到。
    model: Optional[str] = None
    attempts: int = 0


# ============ 批次历史（Step 2）============
# 一个 Shot 可有多个首帧/尾帧批次 + 多个视频版本。
# 选择用独立指针（candidate_id / version_id），不靠删除其他候选。
# 旧 first_frame/last_frame/video 保留兼容；批次为新真相源（Part B 起 handler 写批次）。
@dataclass
class Candidate:
    candidate_id: str
    local: Optional[str] = None
    url: Optional[str] = None
    status: str = ShotStatus.PENDING.value
    error: Optional[str] = None
    created_at: float = 0.0


@dataclass
class ImageBatch:
    batch_id: str
    kind: str = "first_frame"            # first_frame | last_frame
    created_at: float = 0.0
    status: str = ShotStatus.PENDING.value
    prompt_snapshot: str = ""            # 生成时的提示词快照
    char_ref_snapshot: Dict[str, Any] = field(default_factory=dict)   # {角色名: {view, url}}
    based_on_first_frame: Optional[str] = None   # 尾帧批次：基于的首帧 candidate_id
    candidates: List[Candidate] = field(default_factory=list)
    origin_run_id: Optional[str] = None  # AI-4B Autopilot：本批次由哪个 Run 生成（None=手工/旧）
    # S3C.2 boundary-frame provenance（§1 Case C 最小 derived basis）：BOUND 生成路径
    # 记录生成时的 ShotReferencePack basis（binding_revision / resolved SIV / 指纹）——
    # 只描述「这个生成结果基于什么产生」，不是第二套 canonical、不替代 Binding、
    # 不自动 refresh；{} = legacy/旧数据（消费方 fail closed 不得当作 fresh）。
    generation_basis: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VideoVersion:
    version_id: str
    created_at: float = 0.0
    status: str = ShotStatus.PENDING.value
    prompt_snapshot: str = ""
    char_ref_snapshot: Dict[str, Any] = field(default_factory=dict)
    first_frame_candidate_id: Optional[str] = None
    last_frame_candidate_id: Optional[str] = None
    video_id: Optional[str] = None
    task_id: Optional[str] = None
    local: Optional[str] = None
    url: Optional[str] = None
    seconds_returned: Optional[float] = None
    size_returned: Optional[str] = None
    num_frames: int = 0
    frame_rate: int = 24
    origin_run_id: Optional[str] = None  # AI-4B Autopilot：本版本由哪个 Run 生成（None=手工/旧）
    model: Optional[str] = None          # 生成所用视频模型（None=旧数据/config 默认 v2.0）
    # Native AV C1（18.md §22/§23）：可选 generation_basis（沿用 ImageBatch
    # .generation_basis 兼容范式）——记录本版本基于什么产生（dialogue/speaker/
    # language/audio intent/visual/provider/compiler 指纹）。{} = legacy/旧数据或
    # 未走 Native AV 编译（消费方按 UNTRACKED_LEGACY 诚实处理，不当作 fresh）。
    generation_basis: Dict[str, Any] = field(default_factory=dict)
    # Phase 39D（用户定版）：本版本实际消费的输入图清单——首尾帧可以是参考。
    # 每项 {slot: first|last|reference, semantic_role: hard_start_boundary|
    # hard_end_boundary|relay_start_boundary|reference_only, url, ...来源字段}。
    # reference_only 即"作参考而非首尾帧"（系统显示时以标记区分，如框色）。
    # [] = 旧数据/未记录（诚实缺省）。
    input_visuals: List[Dict[str, Any]] = field(default_factory=list)
    # 42.md §二缺陷④：本版本提交时，上一次提交处于「结果不明」状态（generating
    # 且无 video_id——响应未及落库进程即中断）。True = 存在重复生成的可能性，
    # 消费方/验收方必须如实知晓（不得当作全新提交宣称无重复）。False = 无此历史。
    previous_submit_uncertain: bool = False


# ---- 批次解析/迁移 helper（Step 2 向后兼容：旧 first_frame/last_frame/video -> 批次）----
def _parse_candidate(c: dict) -> "Candidate":
    return Candidate(**{k: v for k, v in (c or {}).items() if k in Candidate.__dataclass_fields__})

def _parse_image_batch(b: dict, kind: str) -> "ImageBatch":
    b = dict(b or {})
    b["kind"] = b.get("kind", kind)
    b["candidates"] = [_parse_candidate(c) for c in b.get("candidates", [])]
    return ImageBatch(**{k: v for k, v in b.items() if k in ImageBatch.__dataclass_fields__})

def _parse_video_version(v: dict) -> "VideoVersion":
    return VideoVersion(**{k: v for k, v in (v or {}).items() if k in VideoVersion.__dataclass_fields__})

def _legacy_image_batch(old: "ImageAsset", kind: str) -> List["ImageBatch"]:
    """旧 ImageAsset.candidates -> 一个 legacy 批次。"""
    if not old or not old.candidates:
        return []
    cands = [Candidate(candidate_id=f"legacy_{kind}_{i+1}", local=c.local, url=c.url,
                       status=c.status, error=c.error, created_at=c.updated_at or 0.0)
             for i, c in enumerate(old.candidates)]
    return [ImageBatch(batch_id=f"legacy_{kind}", kind=kind, created_at=0.0,
                       status=old.status, candidates=cands)]

def _legacy_selected(old: "ImageAsset", batch: "ImageBatch") -> Optional[str]:
    if old.status != ShotStatus.DONE.value or not batch.candidates:
        return None
    idx = old.selected_index if 0 <= old.selected_index < len(batch.candidates) else 0
    return batch.candidates[idx].candidate_id

def _legacy_video_version(old: "VideoAsset") -> List["VideoVersion"]:
    if not old or not (old.local or old.video_id):
        return []
    return [VideoVersion(version_id="legacy_video", created_at=old.updated_at or 0.0,
                         status=old.status, local=old.local, url=old.url,
                         video_id=old.video_id, task_id=old.task_id,
                         seconds_returned=old.seconds_returned, size_returned=old.size_returned,
                         num_frames=old.num_frames, frame_rate=old.frame_rate)]


@dataclass
class PerformanceIntent:
    """Shot 表演意图（C2 22.md §13：最小 production-level 表演意图）。
    - 挂 Shot（创作 canonical：与 motion/description 同属导演填写；BOUND/UNBOUND
      两路通用——挂 binding 会把无 binding 的镜排除在外）；
    - 三项自由文本，全空 = 未表达（编译零改动，22.md §23）；
    - 编译（av_prompt_compiler v3 统一表演指令，§14：动作/表情/对白属同一场表演），
      进 generation_basis（performance_intent_fingerprint，actual dependency）。
    🔴 明确不建：EmotionVersion / FacialAnimationTrack / PerformanceTimeline /
      Emotion Agent（22.md §13）。"""
    emotion: str = ""          # e.g. "tense"（情绪）
    intensity: str = ""        # e.g. "high"（强度）
    note: str = ""             # 可选：身体/台词表达补充（delivery note）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Any) -> "PerformanceIntent":
        d = dict(d or {})
        return cls(
            emotion=str(d.get("emotion") or "").strip(),
            intensity=str(d.get("intensity") or "").strip(),
            note=str(d.get("note") or "").strip())

    def is_empty(self) -> bool:
        return not (self.emotion or self.intensity or self.note)


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

    # ---- 结构化镜头字段（提示词.md 镜头工作台）----
    # 旧字段 camera/lighting/motion/prompt_en 等保留兼容（§14 兼容规则 6/7/8），
    # 新字段与旧字段不互相覆盖：camera(机位+运镜) 与 camera_angle/camera_movement 分离；
    # prompt_en 继续作为首帧英文提示词兼容字段（§14 规则 5），first_frame 不另设 prompt_en。
    # 首帧
    opening_state: str = ""           # 主体起始状态（姿态/朝向/表情/物体初始状态）
    camera_angle: str = ""            # 机位角度（平视/低机位/高机位/...，与运镜分离）
    composition: str = ""             # 构图（专业）
    important_details: str = ""       # 重要画面细节（专业）
    required_elements: List[str] = field(default_factory=list)  # 必须保持/出现的元素（专业，多选+自定义）
    # 尾帧
    last_frame_description: str = ""  # 结束画面
    end_state: str = ""               # 主体结束状态
    frame_change: str = ""            # 相对首帧的变化
    preserve_elements: List[str] = field(default_factory=list)  # 保持一致（角色身份/面部/服装/场景/...）
    add_elements: str = ""            # 新增元素（专业）
    remove_elements: str = ""         # 删除元素（专业）
    end_composition: str = ""         # 结束构图/摄影机状态（专业）
    preserve_custom: str = ""         # 自定义保持要求（专业）
    # 视频
    camera_movement: str = ""         # 摄影机运动（与机位 camera 分离）
    motion_pace: str = ""             # 动作节奏
    environment_motion: str = ""      # 环境运动（专业）
    action_sequence: str = ""         # 动作发生顺序（专业）
    performance_intent: Optional[PerformanceIntent] = None  # 表演意图（C2 22.md §13；全空=未表达）
    camera_movement_detail: str = ""  # 摄影机运动细节（专业）
    transition_description: str = ""  # 首尾帧过渡关系（仅 keyframes，专业）
    video_preserve_elements: List[str] = field(default_factory=list)  # 视频保持稳定
    video_preserve_custom: str = ""   # 自定义稳定要求（专业）
    target_duration_s: Optional[float] = None  # 用户目标时长（秒）；None=跟随配音（audio 反推）
    # 三类独立英文提示词 + 手动 override + 状态签名（§15）
    # 首帧 = prompt_en（兼容）+ first_frame_prompt_override；尾帧/视频各一份。
    # *_prompt_sig = 上次生成该提示词时结构字段的签名，用于检测"结构字段已变化，待更新"。
    last_frame_prompt_en: str = ""
    video_prompt_en: str = ""
    first_frame_prompt_override: str = ""
    last_frame_prompt_override: str = ""
    video_prompt_override: str = ""
    first_frame_prompt_sig: str = ""
    last_frame_prompt_sig: str = ""
    video_prompt_sig: str = ""

    # ---- 生成策略 ----
    link_mode: str = LinkMode.I2V.value
    # 34.md Human Gate：multi 模式的视频参考按镜选择（必须 ∈ cast look 的 approved
    # 参考集；空/非法 → 回退 look hero 的确定性规则）。消费与校验见
    # video_adapter._select_video_reference；参与 video_prompt_creative_fingerprint。
    video_reference_asset_id: Optional[str] = None
    seed: Optional[int] = None

    # ---- 产物 ----
    first_frame: ImageAsset = field(default_factory=ImageAsset)
    last_frame: ImageAsset  = field(default_factory=ImageAsset)   # 仅 keyframes 模式需要
    audio: AudioAsset  = field(default_factory=AudioAsset)
    subtitle_srt: Optional[str] = None
    video: VideoAsset  = field(default_factory=VideoAsset)
    # 批次历史（Step 2）：旧字段保留兼容，批次为新真相源
    first_frame_batches: List[ImageBatch] = field(default_factory=list)
    last_frame_batches: List[ImageBatch] = field(default_factory=list)
    video_versions: List[VideoVersion] = field(default_factory=list)
    selected_first_frame: Optional[str] = None   # candidate_id 指针
    selected_last_frame: Optional[str] = None
    final_video: Optional[str] = None            # version_id 指针
    # ---- 镜头接力 / 开场来源（Phase 6A；与 link_mode 独立）----
    # opening_source: 当前镜头开场画面来源；source_shot_id: 接力来源镜头（同场景、位于本镜之前）
    # source_version: 上次同步时来源镜头的 final_video version_id（stale 检测；不一致即来源已更新）
    # video_end_frame: 本镜最终视频的「实际末帧」（ffmpeg 提取，非规划的 last_frame）
    # video_end_source_version: 末帧提取自哪个 video version_id
    opening_source: str = OpeningSource.INDEPENDENT.value
    source_shot_id: Optional[str] = None
    source_version: Optional[str] = None
    # Phase56 A2（reference_policy v2）：independent opening 的显式切镜依据
    # （same_scene_recam/scene_change/time_jump）——baseline_equiv_chain 策略下
    # 未声明依据的独立首帧被拒绝；本字段即「intentional cut 须显式声明」的落点。
    opening_cut_reason: Optional[str] = None
    video_end_frame: Asset = field(default_factory=Asset)
    video_end_source_version: Optional[str] = None

    # ---- 状态 ----
    status: str = ShotStatus.PENDING.value
    locked: bool = False             # 锁定后批量操作跳过
    note: str = ""
    split_next: Optional[str] = None  # 拆分后继 shot_id（split-dialogue 设；尾帧桥接拷贝给后继首帧）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Shot":
        d = dict(d)
        d["first_frame"] = ImageAsset.from_dict(d.get("first_frame", {}) or {})
        d["last_frame"]  = ImageAsset.from_dict(d.get("last_frame", {}) or {})
        d["audio"]       = AudioAsset(**d.get("audio", {}) or {})
        d["video"]       = VideoAsset(**d.get("video", {}) or {})
        # video_end_frame 是普通 Asset（非 ImageAsset），单独解析
        _vef = d.get("video_end_frame") or {}
        d["video_end_frame"] = Asset(**{k: v for k, v in _vef.items() if k in Asset.__dataclass_fields__})
        # 批次（Step 2）：已有用之；无则从旧字段迁移出 legacy 批次（向后兼容）
        ff_raw = d.get("first_frame_batches") or []
        d["first_frame_batches"] = [_parse_image_batch(b, "first_frame") for b in ff_raw] if ff_raw else _legacy_image_batch(d["first_frame"], "first_frame")
        lf_raw = d.get("last_frame_batches") or []
        d["last_frame_batches"] = [_parse_image_batch(b, "last_frame") for b in lf_raw] if lf_raw else _legacy_image_batch(d["last_frame"], "last_frame")
        # 39F（41.md §2/P3）：legacy 迁移只对「真 legacy 文档」（无 video_versions 键，
        # v1→v2 迁移器产出的形状）。键存在但为空 = 新代码的诚实空——绝不再从 video
        # 字段伪造 legacy_video 版本：视频在途保存（submit 落 video_id，versions 尚空）
        # 后的任何 reload 都会把在途 video 伪造成 status=generating 的幽灵版本坐进
        # 版本史 index 0（P40A/P39C/P39D 等生产项目实证，input_visuals=[] 且永不更新）。
        if "video_versions" in d:
            d["video_versions"] = [_parse_video_version(v) for v in (d.get("video_versions") or [])]
        else:
            d["video_versions"] = _legacy_video_version(d["video"])
        # 选择指针：无则从旧字段推断
        if d.get("selected_first_frame") is None and d["first_frame_batches"]:
            d["selected_first_frame"] = _legacy_selected(d["first_frame"], d["first_frame_batches"][0])
        if d.get("selected_last_frame") is None and d["last_frame_batches"]:
            d["selected_last_frame"] = _legacy_selected(d["last_frame"], d["last_frame_batches"][0])
        # 39F（同 video_versions 修复）：legacy 推断只对「真 legacy 文档」（无
        # final_video 键）。显式 None = 用户/系统清除了指针，不得被推断复活——
        # 否则 freshness 读模型对「无 final」镜谎报 current（39F 回归实证）。
        if "final_video" not in d and d["video_versions"] and d["video"].status == ShotStatus.DONE.value:
            d["final_video"] = d["video_versions"][0].version_id
        # 表演意图（C2 §13）：dict → 数据类；缺省/空 → None（全空 = 未表达）
        pi = d.get("performance_intent")
        d["performance_intent"] = PerformanceIntent.from_dict(pi) if isinstance(pi, dict) else None
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Scene:
    scene_id: str                    # "s01"
    title: str = ""
    location: str = ""
    time_of_day: str = ""
    summary: str = ""                # 分镜故事文本（用户手写）
    characters: List[str] = field(default_factory=list)   # 出场角色名（来自当前项目角色）
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
class NativeAVSettings:
    """Native AV 生产设置（C1 18.md §5：language 从 caller 实验参数升级为
    Project Production Policy 的最小落地）。只放项目级生产事实，不放大系统。
    spoken_language：正式对白语言（如 "Mandarin Chinese"）——Native Dialogue
    Control 的 language authoritative source；显式 request override > 本值；
    两者皆缺 + 台词非空 → language_missing fail closed（B1 已证伪让 provider 猜）。"""
    spoken_language: str = ""


@dataclass
class Project:
    name: str
    style: StyleAnchor = field(default_factory=StyleAnchor)
    native_av: NativeAVSettings = field(default_factory=NativeAVSettings)   # C1 §5（旧项目缺省 = 空 settings）
    story_text: str = ""             # 项目级完整故事（只存显，不解析/不拆分/不生成）
    characters: List[Character] = field(default_factory=list)
    scenes: List[Scene] = field(default_factory=list)
    # 分镜 id 分配水位（batch 0）：新建分镜用 s{next_scene_seq:02d} 后自增，
    # 删除不回收。身份证号语义，详见 PRODUCT_FLOW_DESIGN §一.3。
    next_scene_seq: int = 1
    schema_version: int = 2
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def all_shots(self) -> List[Shot]:
        return [s for sc in self.scenes for s in sc.shots]

    def get_shot(self, shot_id: str) -> Optional[Shot]:
        return next((s for s in self.all_shots() if s.shot_id == shot_id), None)

    def get_character(self, name: str) -> Optional[Character]:
        return next((c for c in self.characters if c.name == name), None)

    # ---- 序列化（AI-6A 公共化：ProjectStore._to_dict/_from_dict 原逻辑搬此，供 revision 层复用）----
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "style": asdict(self.style),
            "native_av": asdict(self.native_av),
            "story_text": self.story_text,
            "characters": [
                {"name": c.name, "description_en": c.description_en,
                 "views": {k: asdict(v) for k, v in c.views.items()}}
                for c in self.characters
            ],
            "scenes": [s.to_dict() for s in self.scenes],
            "next_scene_seq": self.next_scene_seq,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Project":
        """从 dict 反序列化（原 ProjectStore._from_dict 逻辑）。
        next_scene_seq 缺失时按已用 scene_id 最大数字 + 1 初始化（删除不回收）。"""
        raw = raw or {}
        style = StyleAnchor(**(raw.get("style") or {}))
        # native_av settings：字段过滤解析（旧项目无此键 → 空 settings；未知键不炸——
        # 与 video_end_frame/Asset 同口径，比 StyleAnchor 的直接 ** 更稳）
        _nav = dict(raw.get("native_av") or {})
        native_av = NativeAVSettings(**{k: v for k, v in _nav.items()
                                        if k in NativeAVSettings.__dataclass_fields__})
        characters = []
        for c in raw.get("characters", []):
            views = {k: Asset(**v) for k, v in (c.get("views") or {}).items()}
            characters.append(Character(
                name=c.get("name", ""),
                description_en=c.get("description_en", ""),
                views=views,
            ))
        scenes = [Scene.from_dict(s) for s in raw.get("scenes", [])]
        next_scene_seq = raw.get("next_scene_seq")
        if next_scene_seq is None:
            used = []
            for sc in raw.get("scenes", []):
                sid = sc.get("scene_id") if isinstance(sc, dict) else getattr(sc, "scene_id", "")
                if isinstance(sid, str) and sid.startswith("s"):
                    try:
                        used.append(int(sid[1:]))
                    except ValueError:
                        pass
            next_scene_seq = (max(used) + 1) if used else 1
        return cls(
            name=raw.get("name", "untitled"),
            style=style,
            native_av=native_av,
            story_text=raw.get("story_text", ""),
            characters=characters,
            scenes=scenes,
            next_scene_seq=next_scene_seq,
            schema_version=raw.get("schema_version", 2),
            created_at=raw.get("created_at", time.time()),
            updated_at=raw.get("updated_at", time.time()),
        )
