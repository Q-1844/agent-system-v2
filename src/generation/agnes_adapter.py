# src/generation/agnes_adapter.py
# Agnes request compiler（31.md §10/§20）：GenerationRequest（provider-neutral）
# → AgnesClient 真实调用形态。本模块是 Agnes 形态知识的唯一落点（image 数组 /
# size_tier 桶 / 视频公网 URL 约束 / 槽位映射）；未来新增 provider（CodeBuddy/Kling/
# Runway…）只新增同名 compiler，不改 Binding/Pack/Bound*Adapter——这正是本轮要
# 验证的架构价值（§20：如果未来必须改 Binding/Pack 才能支持新 provider，说明
# provider abstraction 做错了）。
#
# 能力诚实映射（§46：来自真实代码 AGNES_API_FACTS/agnes_client，绝不虚构支持）：
#   - 图片：image_refs 数组（公网 URL 或 data URI）；无 negative_prompt 参数
#     （req.negative_prompt 非空 → 丢弃并记 dropped diagnostic，绝不伪造传参）；
#     图片 seed 不支持（req.seed 非空 → 结构化拒绝，防静默丢参）。
#   - 视频：i2v → image_url=first_frame 槽；keyframes → image_urls=[first,last]；
#     multi → extra_body.image=[单参考]（v2.0 ti2vid 单图上限，34.md §13）；
#     视频接口图片必须公网 URL（FACTS §3.3：无 data URI 支持）→ data URI 进槽 =
#     结构化拒绝（AgnesRequestCompileError），绝不静默改写。
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.generation.request import GenerationRequest


class AgnesRequestCompileError(Exception):
    """GenerationRequest → Agnes 形态编译的结构化失败（能力边界/约束违反）。
    绝不静默丢参、绝不静默改写媒体形态。"""

    def __init__(self, code: str, detail: Any = None):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass
class AgnesImageCall:
    """AgnesClient.generate_image 的编译产物（kwargs 形态）。dropped 记录编译期
    诚实丢弃项（如 negative_prompt 无参数槽）——31.md §12：省略必须有记录。"""
    prompt: str = ""
    image_refs: Optional[List[str]] = None
    size_tier: str = "1K"
    ratio: str = "16:9"
    dropped: List[Dict[str, Any]] = field(default_factory=list)

    def kwargs(self) -> dict:
        return {"prompt": self.prompt, "image_refs": self.image_refs,
                "size_tier": self.size_tier, "ratio": self.ratio}


@dataclass
class AgnesVideoCall:
    """create_video 的 prompt + 图片槽编译产物（其余 kwargs 属 provider options，
    由 handler 既有通道传递）。i2v：image_url 单槽；keyframes：image_urls 双槽。"""
    prompt: str = ""
    image_url: Optional[str] = None
    image_urls: Optional[List[str]] = None

    def slots(self) -> Dict[str, Any]:
        if self.image_urls is not None:
            return {"image_urls": list(self.image_urls)}
        return {"image_url": self.image_url}


def compile_agnes_image_call(req: GenerationRequest) -> AgnesImageCall:
    """first_frame/last_frame 请求 → Agnes 图片调用形态。确定性：同 req 同输出。
    refs 顺序 = reference_inputs 顺序（pack 确定性序 + layout 语义原样保留）；
    全部 media 为空 → image_refs=None（t2i，与既有 use_i2i=bool(refs) 语义一致）。"""
    if req.generation_type not in ("first_frame", "last_frame"):
        raise AgnesRequestCompileError(
            "generation_type_not_image", {"generation_type": req.generation_type})
    dropped: List[Dict[str, Any]] = []
    if req.negative_prompt:
        dropped.append({"field": "negative_prompt",
                        "reason": "negative_prompt_not_supported_image_provider",
                        "hint": "Agnes 图片接口无 negative_prompt 参数——负面约束"
                                "经 P1 风格指令文本承载（image_adapter §6）"})
    if req.seed is not None:
        raise AgnesRequestCompileError(
            "seed_not_supported_image_provider",
            {"seed": req.seed,
             "hint": "图片接口不支持 seed（AGNES_API_FACTS §2.1）——绝不静默丢参"})
    refs = [ri.media for ri in req.reference_inputs if ri.media]
    return AgnesImageCall(prompt=req.prompt,
                          image_refs=refs or None,
                          size_tier=req.size_tier or "1K",
                          ratio=req.ratio or "16:9",
                          dropped=dropped)


def compile_agnes_video_call(req: GenerationRequest) -> AgnesVideoCall:
    """video 请求 → Agnes 视频槽位形态。i2v 只认 first 槽（end_state 仅语义目标，
    video_adapter §8 不伪造 last-frame conditioning）；keyframes 需 first+last 齐
    （缺 last = 结构化拒绝，不降级成 i2v——mode 是 creative 策略字段，编译层绝不
    重解释）；multi（34.md §13）需 1-5 个 video_reference（2026-09-28 起 2.5-flash
    reference 为唯一多图路径，官方 images 上限 5——缺失或超限结构化拒绝，绝不静默
    裁剪）。data URI 进槽 = 拒绝（视频接口仅公网 URL）。"""
    if req.generation_type != "video":
        raise AgnesRequestCompileError(
            "generation_type_not_video", {"generation_type": req.generation_type})
    prompt = req.prompt
    provider_mode = (req.provenance or {}).get("provider_mode")
    is_keyframes = provider_mode == "keyframes"
    is_multi = provider_mode == "multi"
    first = req.first_frame
    last = req.last_frame
    for slot, media in (("first", first), ("last", last)):
        if media and str(media).startswith("data:"):
            raise AgnesRequestCompileError(
                "data_uri_not_supported_video_provider",
                {"slot": slot,
                 "hint": "视频接口图片必须公网 URL（AGNES_API_FACTS §3.3）"})
    if is_multi:
        if first or last:
            raise AgnesRequestCompileError(
                "multi_slots_conflict",
                {"first_present": bool(first), "last_present": bool(last),
                 "hint": "multi 模式不消费 boundary frames——视觉输入只有单语义参考"})
        multi_refs = [ri.media for ri in req.reference_inputs
                      if ri.source_type in ("video_reference", "character_look",
                                            "scene_instance_view")
                      and ri.media]
        if not (1 <= len(multi_refs) <= 5):
            raise AgnesRequestCompileError(
                "multi_reference_slot_invalid",
                {"reference_count": len(multi_refs),
                 "hint": "multi 需 1-5 个公网参考（2.5-flash reference images 上限 5，"
                         "2026-09-28 官方文档核读）——缺失或超限都属上游结构化失败，"
                         "编译层不裁剪不降级"})
        for _i, _u in enumerate(multi_refs):
            if str(_u).startswith("data:"):
                raise AgnesRequestCompileError(
                    "data_uri_not_supported_video_provider",
                    {"slot": f"multi_reference[{_i}]",
                     "hint": "视频接口图片必须公网 URL（AGNES_API_FACTS §3.3）"})
        return AgnesVideoCall(prompt=prompt, image_urls=list(multi_refs))
    if is_keyframes:
        if not first or not last:
            raise AgnesRequestCompileError(
                "keyframes_slots_incomplete",
                {"first_present": bool(first), "last_present": bool(last),
                 "hint": "keyframes 需要 first+last 双槽——缺失属上游结构化失败，"
                         "编译层不降级 i2v（mode 是 creative 策略字段）"})
        return AgnesVideoCall(prompt=prompt, image_urls=[first, last])
    if not first:
        raise AgnesRequestCompileError(
            "i2v_first_slot_missing",
            {"hint": "i2v 需要 first 槽——上游 boundary 解析失败应已显式报错"})
    return AgnesVideoCall(prompt=prompt, image_url=first)
