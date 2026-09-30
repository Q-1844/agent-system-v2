# src/generation/video_adapter.py
# Shot Production Binding V1 — Phase S3C.2：Last-frame / Video Generation Adapter。
#
# 职责（12.md §3.B/§24）：把 COMPILED 的 ShotReferencePack + Shot creative motion
# intent + 已持久化的 boundary frames（first/last）转换成当前真实视频 provider
# （AgnesClient.create_video）的输入。**不重新选择生产真相**——用哪个 Look/
# Composition/Prop/SIV/Profile 全部由 Binding→Validity→Pack 决定，本模块只消费。
#
# Provider capability（§5，来自真实代码 agnes_client.py:529-645，绝不假设）：
#   - i2v：v2.0 顶层 body.image（公网 URL 字符串）/ 2.5 mode='keyframe' 同形。
#     → 只有 first frame 视觉槽；**无 last-frame-only 模式**；end_state 只能作为
#       semantic motion target 文本（§8：如实报告，绝不声称 visual last-frame
#       conditioning，绝不把 end ref 塞进 first/current 槽）。
#   - keyframes：仅 v2.0（2.5 显式拒绝），extra_body.image=[first, last]（≥2）。
#     → first slot ← start-compatible frame；last slot ← end-compatible frame（§22）。
#   - 无 arbitrary image refs 槽（multi 模式未验证且 handler 不用）→ Pack references
#     不直接进 provider；生产真相经 boundary frames（由 Pack 生成）+ prompt 生产
#     上下文进入（§33/H：如实报告真实 mapping）。
#
# Boundary freshness（§1 Case C / §17）：video 消费持久化 boundary frame——
# "Binding 现在 READY"≠"旧 frame fresh"。selected 候选所在批次的 generation_basis
# 与当前 Pack basis 比对（binding_revision + resolved SIV；缺失=fail closed），
# 不一致 → boundary_frame_stale，provider 调用数 = 0，绝不 auto regenerate。
#
# Race guard（§18）：compile→resolve→build→dispatch 全窗覆盖——复用 S3C.1
# _pre_dispatch_guard（readiness/binding revision/resolved SIV 口径），不复制指纹引擎。
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.generation.image_adapter import (
    BoundGenerationError,
    _gate_ready_pack,
    _pre_dispatch_guard,
    _production_context_text,
    _style_instruction_from_pack,
    boundary_stale_detail,
    selected_boundary_batch,
)


@dataclass
class BoundVideoRequest:
    """Adapter 结果（§24）：ephemeral、无业务 ID、不落盘、不写回任何 canonical。
    字段以真实 provider API 为准：i2v 只消费 first_frame；keyframes 消费
    first_frame + last_frame；multi（34.md §13）消费 video_reference（单图语义
    参考，非 first-frame conditioning）。无 invented 参数槽（seed/negative_prompt
    属 Shot creative/config，由 handler 既有路径传递，不在此伪造）。
    generation_basis（C1 §22/§23）：Native AV 派生 basis（dialogue/speaker/
    language/audio intent/visual/provider/compiler）——由 handler 写入
    VideoVersion.generation_basis；空 dict = 本 generation 未走 Native AV
    编译（显式 override off）。"""
    shot_id: str = ""
    prompt: str = ""
    provider_mode: str = ""                 # "i2v" | "keyframes" | "multi"
    first_frame: str = ""                   # 公网 URL（i2v/keyframes first slot）
    last_frame: Optional[str] = None        # 公网 URL（仅 keyframes last slot）
    video_reference: str = ""               # 公网 URL（仅 multi：单语义参考）
    # 42.md 缺陷⑤：Look+尾帧双参考（哨兵 prev_tail_look 时非 None）——
    # {url, reference_asset_id, subject_character}。随 used_ids/semantic_roles/
    # input_visuals 落 provenance；进 relay 指纹（identity 键）。
    identity_reference: Optional[Dict[str, Any]] = None
    # 43.md §二（凭空门查证）：场景实例视图第三参考（哨兵 prev_tail_look_scene 时
    # 非 None）——{url, reference_asset_id}。接力镜的场景元素（门/窗/家具位置样式）
    # 无法从尾帧继承（尾帧里看不到）时，场景先验必须显式进请求。随 used_ids/
    # semantic_roles/input_visuals 落 provenance；进 relay 指纹（scene 键）。
    scene_reference: Optional[Dict[str, Any]] = None
    used_reference_ids: List[str] = field(default_factory=list)   # 消费的 boundary candidate ids
    dropped_optional: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    semantic_roles: List[Dict[str, Any]] = field(default_factory=list)
    basis: Dict[str, Any] = field(default_factory=dict)
    generation_basis: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "shot_id": self.shot_id,
            "prompt": self.prompt,
            "provider_mode": self.provider_mode,
            "first_frame": self.first_frame,
            "last_frame": self.last_frame,
            "video_reference": self.video_reference,
            "identity_reference": dict(self.identity_reference)
            if self.identity_reference else None,
            "scene_reference": dict(self.scene_reference)
            if self.scene_reference else None,
            "used_reference_ids": list(self.used_reference_ids),
            "dropped_optional": list(self.dropped_optional),
            "diagnostics": list(self.diagnostics),
            "semantic_roles": list(self.semantic_roles),
            "basis": dict(self.basis),
            "generation_basis": dict(self.generation_basis),
        }

    def to_generation_request(self, *, model: Optional[str] = None):
        """provider-neutral 投影（31.md §10）：BoundVideoRequest → GenerationRequest。
        i2v/keyframes：boundary frames 即视频的真实参考输入（provider 无任意
        reference 槽，§5）——投影为 ReferenceInput(source_type=boundary_frame)。
        multi（34.md §13）：单语义参考投影为 ReferenceInput(source_type=
        video_reference)（不冒充 first frame——它不做 first-frame conditioning）。
        seed/negative_prompt 不进投影（Shot creative 字段，handler kwargs 既有
        通道传递，§24 不伪造槽位）。"""
        from src.generation.request import GenerationRequest, ReferenceInput
        inputs: List[ReferenceInput] = []
        if self.first_frame:
            inputs.append(ReferenceInput(
                source_type="boundary_frame", semantic_role="first_slot",
                temporal_scope="start", media=self.first_frame))
        if self.last_frame:
            inputs.append(ReferenceInput(
                source_type="boundary_frame", semantic_role="last_slot",
                temporal_scope="end", media=self.last_frame))
        if self.video_reference:
            inputs.append(ReferenceInput(
                source_type="video_reference", semantic_role="multi_reference",
                temporal_scope="throughout", media=self.video_reference))
        if self.identity_reference:
            # 42.md 缺陷⑤：Look 双参考第二槽（source_type=character_look——
            # compile 侧 multi 收集 video_reference+character_look+scene_instance_view
            # 来源，顺序 = reference_inputs 顺序：尾帧在前=Picture 1，Look=Picture 2，
            # 场景=Picture 3）
            inputs.append(ReferenceInput(
                source_type="character_look", semantic_role="multi_reference",
                temporal_scope="throughout",
                media=str(self.identity_reference.get("url") or "")))
        if self.scene_reference:
            # 43.md §二：场景实例视图第三槽（哨兵 prev_tail_look_scene）
            inputs.append(ReferenceInput(
                source_type="scene_instance_view", semantic_role="multi_reference",
                temporal_scope="throughout",
                media=str(self.scene_reference.get("url") or "")))
        return GenerationRequest(
            generation_type="video", shot_id=self.shot_id,
            prompt=self.prompt, reference_inputs=inputs,
            first_frame=self.first_frame or None,
            last_frame=self.last_frame or None,
            model=model,
            diagnostics=list(self.diagnostics),
            provenance={
                "basis": dict(self.basis),
                "generation_basis": dict(self.generation_basis),
                "provider_mode": self.provider_mode,
                "used_reference_ids": list(self.used_reference_ids),
                "dropped_optional": list(self.dropped_optional),
                **({"identity_reference": dict(self.identity_reference)}
                   if self.identity_reference else {}),
                **({"scene_reference": dict(self.scene_reference)}
                   if self.scene_reference else {}),
            })


def _effective_relay_first_frame(project: Any, shot: Any) -> str:
    """inherit_previous：开场画面 = 来源镜头实际视频末帧 URL（routers/shots.py
    _effective_first_frame_url 的同语义最小内联——避免 routers→src 反向依赖）。
    不可用返回 ""。"""
    from src.core.models import OpeningSource
    if shot.opening_source == OpeningSource.INHERIT_PREVIOUS.value and shot.source_shot_id:
        prev = project.get_shot(shot.source_shot_id)
        if prev and prev.video_end_frame.url_ready:
            return prev.video_end_frame.url or ""
        return ""
    return ""


# 34.md V1C（Human Gate 用户方向）：multi 按镜参考的尾帧接力哨兵——
# shot.video_reference_asset_id == "prev_tail" → 参考 = 影片顺序上一镜的
# 实际视频末帧（Phase 6A 自动提取+托管）。不要求逐像素衔接（那会锁死模型
# 发挥），只钉住"上一镜结尾的狐狸位置状态"作为开场语义。接力解析是实际
# 生成依赖——build 与 freshness（compute_current_dependency_keys）两路对称纳入。
VIDEO_REFERENCE_RELAY = "prev_tail"

# 42.md §二缺陷⑤/§三：Look+尾帧双参考哨兵（opt-in，canonical 镜头事实——
# 不是请求级 flag：freshness 读模型无请求上下文，canonical 才能两侧对称重算）。
# relay 链画风漂移根因（42.md §3：特写丢 Look 重造 → relay 逐镜传播）的传播层
# 修复——每镜除尾帧外同发本镜 hero Look，身份/画风每镜重建锚。是否设为默认
# 由 §四 对照实验（P1）与用户验收裁定，Skill 决策入口引用。
VIDEO_REFERENCE_RELAY_IDENTITY = "prev_tail_look"
# 43.md §二（凭空门查证，2026-09-30）：尾帧+Look+场景实例视图三参考哨兵——
# 接力镜需要的场景元素（门/窗/家具的位置样式）不在尾帧可见范围内时，场景先验
# 必须显式进请求（SIV 里有的门先验不进请求=模型凭空发明，实证：修理间拱形门
# 被三次生成成三种不存在的门）。opt-in，canonical 镜头事实，进 relay 指纹。
VIDEO_REFERENCE_RELAY_IDENTITY_SCENE = "prev_tail_look_scene"


def resolve_multi_relay_source(project: Any, shot: Any) -> Optional[Dict[str, Any]]:
    """解析 multi 尾帧接力来源：影片顺序（scenes→shots 展平顺序）的上一镜 +
    其 video_end_frame（url/来源版本）。⚠️ 跨场景衔接是常态（本 Pilot 每段
    1 镜）——上一镜 = 展平序列的前一项，绝不仅在同场景内找（V1C 首跑实证
    的 bug：idx>0 同场景找 prev → 每段 1 镜时永远 None → 静默回落 hero）。
    非 relay / 无上一镜 / 末帧未就绪 → None。
    identity：哨兵=prev_tail_look/prev_tail_look_scene 时 True（Look+尾帧双参考）；
    scene：哨兵=prev_tail_look_scene 时 True（再加场景实例视图第三参考，43.md §二）——
    进 relay 指纹（build 与 freshness 两侧同键对称）。"""
    _vref = str(getattr(shot, "video_reference_asset_id", "") or "")
    if _vref not in (VIDEO_REFERENCE_RELAY, VIDEO_REFERENCE_RELAY_IDENTITY,
                     VIDEO_REFERENCE_RELAY_IDENTITY_SCENE):
        return None
    flat: List[Any] = []
    scene_of: Dict[str, Any] = {}
    for sc in (getattr(project, "scenes", None) or []):
        for s in (getattr(sc, "shots", None) or []):
            flat.append(s)
            scene_of[getattr(s, "shot_id", None)] = getattr(sc, "scene_id", None)
    prev = None
    for idx, s in enumerate(flat):
        if s.shot_id == shot.shot_id:
            if idx > 0:
                prev = flat[idx - 1]
            break
    if prev is None:
        return None
    ef = getattr(prev, "video_end_frame", None)
    url = (getattr(ef, "url", None) or "") if ef is not None else ""
    if not url:
        return None
    prev_scene = scene_of.get(getattr(prev, "shot_id", None))
    shot_scene = scene_of.get(getattr(shot, "shot_id", None))
    return {"source_shot_id": prev.shot_id,
            "end_source_version": getattr(prev, "video_end_source_version", None),
            "url": url,
            # 42.md 缺陷⑤：Look+尾帧双参考模式（canonical 哨兵 prev_tail_look）
            "identity": _vref in (VIDEO_REFERENCE_RELAY_IDENTITY,
                                  VIDEO_REFERENCE_RELAY_IDENTITY_SCENE),
            # 43.md §二：场景实例视图第三参考（canonical 哨兵
            # prev_tail_look_scene）——进指纹，两侧对称
            "scene": _vref == VIDEO_REFERENCE_RELAY_IDENTITY_SCENE,
            # Phase 3 守卫：跨段接力（组首接上一组末镜）是显式决策——标记进
            # diagnostic + visual basis 指纹，绝不静默（组首 prev_tail 残留会
            # 按全片顺序自动接上一组；是否合法由 Skill 决策链裁定）。
            "cross_segment": bool(prev_scene and shot_scene
                                  and prev_scene != shot_scene)}


def _resolve_boundary_frame(shot: Any, pack_basis: dict, slot: str,
                            diagnostics: List[Dict[str, Any]]) -> str:
    """§16/§17：authoritative selection → URL + freshness gate。
    - selection：selected_{slot}_frame 指针 → owning batch（绝不 glob/mtime/文件名）；
    - freshness：batch.generation_basis vs 当前 pack basis（缺失/不一致 → fail closed）。
    （inherit_previous relay 物料在主流程先行分流，不经此路径。）"""
    batch, cand = selected_boundary_batch(shot, slot)
    if batch is None or cand is None:
        raise BoundGenerationError(
            "reference_media_unresolvable",
            {"boundary_slot": slot,
             "hint": f"selected_{slot}_frame 无法解析到批次候选（§16 selection truth）"})
    # Phase 3 守卫（truth gate，先于 freshness）：multi 接力同步进槽的图
    # （slot_semantics=multi_reference_only，见 routers/shots _sync_first_frame_slot）
    # 是语义参考记录，不是硬边界帧——i2v/keyframes 把它当第 0 帧消费 = 把
    # reference_only 冒充 hard_start_boundary，结构化拒绝。
    slot_semantics = str((getattr(batch, "generation_basis", None) or {})
                         .get("slot_semantics") or "")
    if slot_semantics == "multi_reference_only":
        raise BoundGenerationError(
            "first_frame_slot_not_hard_boundary",
            {"slot": slot, "batch_id": batch.batch_id,
             "hint": "选中首帧槽是 multi 语义参考的同步记录（非硬边界帧）——i2v/"
                     "keyframes 不得消费。硬首帧两条正路：opening_source="
                     "inherit_previous 消费来源镜头真实末帧；或重新生成/选择本镜"
                     "分镜首帧（如融合首帧起步）"})
    detail = boundary_stale_detail(batch, pack_basis)
    if detail:
        raise BoundGenerationError("boundary_frame_stale", {**detail, "slot": slot})
    if not cand.url:
        raise BoundGenerationError(
            "reference_media_unresolvable",
            {"boundary_slot": slot, "candidate_id": cand.candidate_id,
             "hint": "boundary candidate 无公网 URL（重托管/尾帧等待由 handler 既有机制负责）"})
    diagnostics.append({
        "reason": "boundary_frame_fresh",
        "detail": {"slot": slot, "candidate_id": cand.candidate_id,
                   "batch_id": batch.batch_id}})
    return cand.url


def _resolve_video_reference_media(rdoc: Any, asset_id: str) -> str:
    """multi 槽参考媒体解析（34.md §13）：ReferenceAsset → 公网 URL。
    视频接口只接受公网 URL（FACTS §3.3，无 data URI）——与 boundary frames 同一
    约束，local-only 资产 fail closed（不静默 rehost：参考资产由 Agnes 生成、
    自带 provider URL；缺失属上游登记问题）。"""
    asset = (getattr(rdoc, "reference_assets", None) or {}).get(asset_id)
    if asset is None:
        raise BoundGenerationError(
            "reference_media_unresolvable",
            {"video_reference_asset_id": asset_id,
             "hint": "multi 视频参考 asset 在 Resources 中不存在（§16 selection truth）"})
    url = getattr(asset, "url", None) or ""
    if not url:
        raise BoundGenerationError(
            "reference_media_unresolvable",
            {"video_reference_asset_id": asset_id,
             "hint": "multi 视频参考无公网 URL（视频接口无 data URI 槽，FACTS §3.3）"
                     "——先经正式 rehost/登记流程补 URL"})
    return url


def _select_video_reference(pack: dict, shot: Any = None,
                            diagnostics: Optional[List[Dict[str, Any]]] = None
                            ) -> Dict[str, Any]:
    """multi 槽参考的确定性选择（34.md §13：优先已验证的 identity reference 语义）。
    选择规则（确定性，无 LLM、无 glob）：
      0. shot.video_reference_asset_id 按镜选择（34.md Human Gate）——必须命中本
         pack 的 character_look 候选（非法/不属于本镜 cast → 记 diagnostic 并回落
         下面的确定性规则，绝不静默采用无法证明属于本镜的资产）；
      1. cast 角色 look 参考（source_type=character_look）中 priority=primary 者
         （= look 的 hero approved 参考——R3 式身份参考的正式落点）；
      2. 否则任一 character_look 参考；
      3. 否则场景参考（scene_instance_view——无 cast 的环境建立镜）；
      4. 都没有 = 结构化失败（Binding READY 却无任何参考 = 上游错误）。
    顺序 = pack.references 存储顺序（cast 顺序 → look hero → extras，§34 语义序）。"""
    refs = [r for r in (pack.get("references") or [])
            if r.get("reference_asset_id")]
    char = [r for r in refs if r.get("source_type") == "character_look"]
    override = str(getattr(shot, "video_reference_asset_id", "") or "").strip() \
        if shot is not None else ""
    if override:
        match = next((r for r in char
                      if r.get("reference_asset_id") == override), None)
        if match is not None:
            return match
        if diagnostics is not None:
            # override 非法：显式 diagnostic 后回落（不静默、不阻断）
            diagnostics.append({
                "reason": "video_reference_override_invalid",
                "detail": {"video_reference_asset_id": override,
                           "hint": "override 不属于本镜 cast 的 look 候选集——回落确定性规则"}})
    for r in char:
        if r.get("priority") == "primary":
            return r
    if char:
        return char[0]
    scene = [r for r in refs if r.get("source_type") == "scene_instance_view"]
    if scene:
        return scene[0]
    raise BoundGenerationError(
        "reference_media_unresolvable",
        {"slot": "multi_reference",
         "hint": "pack 无任何可解析参考（Binding READY 但 references 空）——上游错误"})


def _resolve_relay_identity_reference(pack: dict, rdoc: Any,
                                      diagnostics: List[Dict[str, Any]]
                                      ) -> Dict[str, Any]:
    """42.md 缺陷⑤：relay 镜身份再锚定参考的确定性解析——本镜 Binding 的
    hero Look（priority=primary 的 character_look，与 _select_video_reference
    同规则）。解析不到公网 media = 结构化失败（opt-in 语义下静默跳过 =
    谎报已锚定，绝不允许）。"""
    refs = [r for r in (pack.get("references") or []) if r.get("reference_asset_id")]
    char = [r for r in refs if r.get("source_type") == "character_look"]
    look = next((r for r in char if r.get("priority") == "primary"), None)
    if look is None and char:
        look = char[0]
    if look is None:
        raise BoundGenerationError(
            "relay_identity_reference_unresolvable",
            {"hint": "哨兵 prev_tail_look 已设但本镜 Binding 无 character_look 参考"
                     "——先补角色 Look 再生成（不静默跳过身份锚定）"})
    url = _resolve_video_reference_media(
        rdoc, str(look.get("reference_asset_id") or ""))
    if not url or str(url).startswith("data:"):
        raise BoundGenerationError(
            "relay_identity_reference_unresolvable",
            {"reference_asset_id": look.get("reference_asset_id"),
             "hint": "Look 媒体不可解析或非公网 URL——视频接口仅公网 URL"})
    diagnostics.append({
        "reason": "relay_identity_reference_applied",
        "detail": {"reference_asset_id": look.get("reference_asset_id"),
                   "subject_character": look.get("subject_character"),
                   "hint": "Look+尾帧双参考：尾帧=衔接语义，Look=身份/画风再锚定"
                           "——每镜重建身份锚，阻断 relay 链画风漂移传播（42.md §三）"}})
    return {"url": url,
            "reference_asset_id": look.get("reference_asset_id"),
            "subject_character": look.get("subject_character"),
            "priority": look.get("priority")}


def _resolve_relay_scene_reference(pack: dict, rdoc: Any,
                                   diagnostics: List[Dict[str, Any]]
                                   ) -> Dict[str, Any]:
    """43.md §二：relay 镜场景锚定参考的确定性解析——本镜 Binding 的场景实例
    视图（pack references 的 scene_instance_view 项；pinned SIV 由 Binding
    解析进 pack）。解析不到公网 media = 结构化失败（opt-in 语义下静默跳过 =
    谎报已锚定，绝不允许）。"""
    refs = [r for r in (pack.get("references") or [])
            if r.get("source_type") == "scene_instance_view"]
    siv = next((r for r in refs if r.get("priority") == "primary"), None)
    if siv is None and refs:
        siv = refs[0]
    if siv is None:
        raise BoundGenerationError(
            "relay_scene_reference_unresolvable",
            {"hint": "哨兵 prev_tail_look_scene 已设但本镜 Binding 无场景实例视图"
                     "参考——先补 SIV（判据 9：缺场景走 Scene Agent 正式入口）"
                     "再生成（不静默跳过场景锚定）"})
    url = _resolve_video_reference_media(
        rdoc, str(siv.get("reference_asset_id") or ""))
    if not url or str(url).startswith("data:"):
        raise BoundGenerationError(
            "relay_scene_reference_unresolvable",
            {"reference_asset_id": siv.get("reference_asset_id"),
             "hint": "SIV 媒体不可解析或非公网 URL——视频接口仅公网 URL"})
    diagnostics.append({
        "reason": "relay_scene_reference_applied",
        "detail": {"reference_asset_id": siv.get("reference_asset_id"),
                   "hint": "尾帧+Look+场景三参考：场景元素（门/窗/家具）位置样式"
                           "以 SIV 为准——尾帧看不到的场景先验必须显式进请求"
                           "（43.md §二凭空门查证）"}})
    return {"url": url,
            "reference_asset_id": siv.get("reference_asset_id")}


def build_bound_video_input(project_dir, project: Any, shot: Any, *,
                            native_dialogue_control: bool = True,
                            native_dialogue_language: Optional[str] = None,
                            provider_options: Optional[Dict[str, Any]] = None
                            ) -> BoundVideoRequest:
    """BOUND shot 的视频生成输入（§3.B；§21：BOUND 永不回落 legacy）。
    顺序：validity gate → compile pack（自带 pre/post 守卫）→ boundary frame 解析
    （selection truth + freshness）→ prompt（creative motion + Pack 生产上下文
    video 口径 + P1 风格指令 [+ Native AV 编译区]）→ pre-dispatch guard。
    provider_mode 沿用 handler 既有映射（keyframes=link_mode=='keyframes'，否则 i2v）
    ——Adapter 不改变 mode 选择（creative 策略字段）。
    Native AV C1（21.md §4/§17）：native_dialogue_control 默认 True=自动
    ——visual prompt 与 dialogue 语义区经同一 NativeAVGenerationPack →
    Prompt Compiler（与 UNBOUND 共用唯一编译点）；显式 False = debug/compat
    override（visual-only，零 Native AV 区、generation_basis 为空）。language =
    resolve_dialogue_language precedence（request override > project
    native_av.spoken_language；对有台词 shot 皆缺 → language_missing fail closed，
    零 provider 调用）；speaker 解析 fail closed（DialogueControlError）。"""
    from src.shot_binding import ShotBindingService
    from src.core.models import OpeningSource

    project_dir = Path(project_dir)
    sid = str(shot.shot_id)
    svc = ShotBindingService(project_dir, project_dir.name)
    pack = _gate_ready_pack(svc, sid)
    basis = pack.get("basis") or {}
    diagnostics: List[Dict[str, Any]] = []

    mode = ("keyframes" if shot.link_mode == "keyframes"
            else "multi" if shot.link_mode == "multi" else "i2v")

    # ---- multi（34.md §13）：单语义参考直生，无 boundary frame 消费 ----
    video_ref_url = ""
    _multi_ref_item: Optional[Dict[str, Any]] = None
    _relay_item: Optional[Dict[str, Any]] = None
    if mode == "multi":
        from src.generation.video_adapter import resolve_multi_relay_source
        _relay_item = resolve_multi_relay_source(project, shot)
    if mode == "multi" and _relay_item is not None:
        # V1C 尾帧接力：参考 = 影片顺序上一镜的实际视频末帧（Phase 6A 提取+托管）。
        video_ref_url = _relay_item["url"]
        diagnostics.append({
            "reason": "multi_relay_reference",
            "detail": {"source_shot_id": _relay_item["source_shot_id"],
                       "end_source_version": _relay_item.get("end_source_version"),
                       "cross_segment": bool(_relay_item.get("cross_segment")),
                       "hint": "尾帧接力（用户方向）：参考=上一镜实际视频末帧——开场"
                               "位置状态衔接上一镜结尾，动作由 prompt 驱动；不追求"
                               "逐像素一致（那会锁死模型发挥）"}})
        if _relay_item.get("cross_segment"):
            # Phase 3 守卫（可见性，不阻断——跨段显式接力合法，见 Skill 决策链）：
            # 组首镜的 prev_tail 残留会按全片顺序接上一组末镜，绝不静默。
            diagnostics.append({
                "reason": "prev_tail_cross_segment_relay",
                "detail": {"source_shot_id": _relay_item["source_shot_id"],
                           "hint": "跨段/组首接力：参考源属上一故事段——跨组延续须与"
                                   "本镜叙事意图一致；若本镜应独立起步，先清理"
                                   " video_reference_asset_id=prev_tail 再生成"}})
    elif mode == "multi":
        from src.resources.store import ResourceStore as _RS
        _multi_ref_item = _select_video_reference(pack, shot, diagnostics)
        rdoc0 = _RS(project_dir, project_dir.name).load()
        video_ref_url = _resolve_video_reference_media(
            rdoc0, str(_multi_ref_item.get("reference_asset_id") or ""))
        diagnostics.append({
            "reason": "multi_reference_direct",
            "detail": {
                "reference_asset_id": _multi_ref_item.get("reference_asset_id"),
                "source_type": _multi_ref_item.get("source_type"),
                "subject_character": _multi_ref_item.get("subject_character"),
                "priority": _multi_ref_item.get("priority"),
                "hint": "multi 单参考直生（v2.0 ti2vid 单图槽）——参考是身份/场景"
                        "语义参考，不做 first-frame conditioning（33.md R3 配方）"}})

    # ---- boundary frame 解析（§16 selection truth + §17 freshness）----
    first_url = ""
    if mode == "multi":
        pass   # multi 不消费 boundary frames——参考即视觉输入
    elif shot.opening_source == OpeningSource.INHERIT_PREVIOUS.value and shot.source_shot_id:
        # relay 物料：来源镜头实际视频末帧——creative relay（Phase 6A 既有机制），
        # 非本镜 Binding 产物 → 无 basis 可比 → diagnostic，不阻断（与 S3C.1
        # continuity 不做 basis 检查同口径）。
        first_url = _effective_relay_first_frame(project, shot)
        if not first_url:
            raise BoundGenerationError(
                "reference_media_unresolvable",
                {"boundary_slot": "first",
                 "hint": "inherit_previous 来源镜头末帧未就绪（无公网 URL）"})
        diagnostics.append({
            "reason": "boundary_relay_no_binding_basis",
            "detail": {"slot": "first",
                       "hint": "开场=来源镜头视频末帧（relay 物料）——freshness 由既有"
                               "接力机制（continuity_status/source_version）管"}})
    else:
        first_url = _resolve_boundary_frame(shot, basis, "first", diagnostics)

    last_url: Optional[str] = None
    if mode == "keyframes":
        last_url = _resolve_boundary_frame(shot, basis, "last", diagnostics)
        if last_url == first_url:
            # keyframes 首尾同图 = 过渡语义失效；不作猜测，显式暴露
            diagnostics.append({
                "reason": "keyframes_boundary_frames_identical",
                "detail": {"hint": "first/last 为同一 candidate——provider 将收到相同双帧"}})

    # ---- prompt（§13：creative motion intent + Pack 生产上下文 video 口径 + P1 风格）----
    from src.core.prompt_builder import build_video_prompt, video_prompt_freshness
    from src.resources.store import ResourceStore
    rdoc = ResourceStore(project_dir, project_dir.name).load()
    # ---- 42.md 缺陷⑤：Look+尾帧双参考（哨兵 prev_tail_look；canonical 决策，
    #      freshness 两侧经 relay 指纹 identity 键对称）----
    identity_reference: Optional[Dict[str, Any]] = None
    scene_reference: Optional[Dict[str, Any]] = None
    if mode == "multi" and _relay_item is not None and _relay_item.get("identity"):
        identity_reference = _resolve_relay_identity_reference(pack, rdoc, diagnostics)
    if mode == "multi" and _relay_item is not None and _relay_item.get("scene"):
        scene_reference = _resolve_relay_scene_reference(pack, rdoc, diagnostics)
    # 37.md §1.1 freshness gate（消费端兜底，multi 专属）：
    #   - stale/untracked：旧 *_prompt_en 不再 shadow 新字段——按「无 en」重编译
    #     （deterministic rebuild 消费端语义；legacy i2v/keyframes 不走此 gate）；
    #   - missing（无 override/en）且 description 非空：multi 编译不含 description
    #     ——静默生成 = 旧语义/无语义污染（v1g 实证：雨林校准全未生效）→ fail
    #     closed，由 handler 侧自动重建先行（本 gate 是最后防线）；
    #   - fresh / override：正常（override 语义保留，用户自负其责）。
    _fr_state = "n/a"
    if mode == "multi":
        _fr = video_prompt_freshness(shot)
        _fr_state = _fr["state"]
        if _fr["state"] in ("stale", "untracked"):
            import copy as _copy
            shot = _copy.copy(shot)
            shot.video_prompt_en = ""
            diagnostics.append({
                "reason": "video_prompt_en_stale_ignored",
                "detail": {"freshness": _fr["state"],
                           "recorded_sig": _fr["recorded"], "current_sig": _fr["sig"],
                           "hint": "旧 video_prompt_en 与当前字段签名不符——按字段重编译"}})
    creative = build_video_prompt(shot, mode)
    if mode == "multi" and _fr_state == "missing" \
            and (getattr(shot, "description", "") or "").strip():
        raise BoundGenerationError(
            "video_prompt_stale",
            {"freshness": _fr_state,
             "hint": "multi 模式 description 不会进入字段编译的 video prompt——"
                     "先重建 prompt（POST /shot/{id}/prompt/rebuild 或让生成链"
                     "自动重建）再生成，否则校准语义静默丢失（37.md §1.1）"})
    if mode == "multi":
        diagnostics.append({
            "reason": "video_prompt_freshness",
            "detail": {"state": _fr_state,
                       "has_override": bool(getattr(shot, "video_prompt_override", None))}})
    ctx = _production_context_text(pack, rdoc, "video")
    style = _style_instruction_from_pack(pack)
    tail = " ".join(x for x in (ctx, style) if x)
    prompt = f"{creative}, {tail}" if (creative and tail) else (creative or tail)

    # ---- Native AV C1（18.md §4/§21：pack + Prompt Compiler，与 UNBOUND 共用）----
    generation_basis: Dict[str, Any] = {}
    if native_dialogue_control:
        from src.generation.native_av_pack import (
            bound_visual_basis,
            build_native_av_pack,
        )
        from src.generation.av_prompt_compiler import compile_native_av_prompt
        _vb = bound_visual_basis(basis)
        if _relay_item is not None:
            # V1C 尾帧接力是实际生成依赖——进 visual basis 指纹（freshness 对称：
            # compute_current_dependency_keys 同规则重算）；cross_segment 参与指纹
            # （接力源跨段与否变化 = 依赖变化）；identity 同理（42.md 缺陷⑤：
            # Look+尾帧双参考开关是 canonical 事实，build/重算两侧同键）。
            _vb = dict(_vb)
            _vb["relay"] = {"source_shot_id": _relay_item["source_shot_id"],
                            "end_source_version": _relay_item.get("end_source_version"),
                            "cross_segment": bool(_relay_item.get("cross_segment")),
                            "identity": bool(_relay_item.get("identity")),
                            "scene": bool(_relay_item.get("scene"))}
        nav_pack = build_native_av_pack(
            project_dir, project, shot,
            visual_route="bound", visual_basis=_vb,
            provider_options=dict(provider_options or {}) | {"mode": mode},
            language_override=native_dialogue_language)
        prompt = compile_native_av_prompt(nav_pack, prompt)
        generation_basis = nav_pack.generation_basis
        dlg = nav_pack.dialogue
        diagnostics.append({
            "reason": "native_dialogue_control_applied",
            "detail": ({"resolution": dlg["resolution"],
                        "line_count": len(dlg["lines"]),
                        "silent_characters": list(dlg["silent_characters"])}
                       if dlg else {"resolution": "no_dialogue"})})

    # ---- 42.md 缺陷⑤：双参考的 <Picture N> 指称（探针实证 provider 语义采用
    #      占位符；只在双参考情形追加，不触碰既有单参考/硬边界路径与指纹）----
    if identity_reference is not None:
        prompt = (prompt or "").rstrip() + (
            "。参考图说明：<Picture 1> 是上一镜头的结尾画面（开场位置与状态须与它"
            "衔接）；<Picture 2> 是该角色的设定参考图（角色的画风、五官、毛色与"
            "服装必须与它保持一致）。")
    if scene_reference is not None:
        prompt = (prompt or "").rstrip() + (
            "<Picture 3> 是本镜所在场景的场景实例视图（空间结构、门/窗/家具等"
            "场景元素的位置与样式必须与它一致，不要发明它没有的元素）。")

    # ---- provider capability 诚实诊断（§8/§22）----
    if mode == "i2v":
        diagnostics.append({
            "reason": "end_state_semantic_target_only",
            "detail": {"hint": "真实 provider i2v 只有 first frame 视觉槽——end_state 仅"
                               "作为 motion target 文本表达，无 visual last-frame "
                               "conditioning（不伪造 last-frame 能力）"}})
    if mode != "multi":
        diagnostics.append({
            "reason": "pack_references_not_provider_consumed",
            "detail": {"hint": "真实 provider i2v/keyframes 无 arbitrary reference 槽——"
                               "Pack references 经 boundary frames（由 Pack 生成）+ prompt "
                               "生产上下文进入，不直接进 provider 请求"}})

    used_ids: List[str] = []
    roles: List[Dict[str, Any]] = []
    if mode == "multi" and _relay_item is not None:
        used_ids.append(f"relay:{_relay_item['source_shot_id']}:"
                        f"{_relay_item.get('end_source_version') or ''}")
        roles.append({"slot": "multi_reference", "temporal_semantics": "throughout",
                      "candidate_id": None, "reference_asset_id": None,
                      "source_type": "prev_tail_relay",
                      "source_shot_id": _relay_item["source_shot_id"],
                      "end_source_version": _relay_item.get("end_source_version"),
                      "batch_id": None})
        if identity_reference is not None:
            used_ids.append(str(identity_reference["reference_asset_id"]))
            roles.append({"slot": "multi_reference", "temporal_semantics": "throughout",
                          "candidate_id": None,
                          "reference_asset_id": identity_reference["reference_asset_id"],
                          "source_type": "character_look",
                          "subject_character": identity_reference.get("subject_character"),
                          "batch_id": None})
        if scene_reference is not None:
            used_ids.append(str(scene_reference["reference_asset_id"]))
            roles.append({"slot": "multi_reference", "temporal_semantics": "throughout",
                          "candidate_id": None,
                          "reference_asset_id": scene_reference["reference_asset_id"],
                          "source_type": "scene_instance_view",
                          "batch_id": None})
    elif mode == "multi":
        used_ids.append(str(_multi_ref_item.get("reference_asset_id") or ""))
        roles.append({"slot": "multi_reference", "temporal_semantics": "throughout",
                      "candidate_id": None,
                      "reference_asset_id": _multi_ref_item.get("reference_asset_id"),
                      "source_type": _multi_ref_item.get("source_type"),
                      "batch_id": None})
    else:
        if first_url:
            fb, fc = selected_boundary_batch(shot, "first")
            used_ids.append(fc.candidate_id if fc else "relay_first_frame")
            roles.append({"slot": "first", "temporal_semantics": "start",
                          "candidate_id": fc.candidate_id if fc else None,
                          "batch_id": getattr(fb, "batch_id", None)})
        if last_url:
            lb, lc = selected_boundary_batch(shot, "last")
            used_ids.append(lc.candidate_id if lc else "")
            roles.append({"slot": "last", "temporal_semantics": "end",
                          "candidate_id": lc.candidate_id if lc else None,
                          "batch_id": getattr(lb, "batch_id", None)})

    # ---- pre-dispatch race guard（§18：compile 之后的第二个时间窗）----
    _pre_dispatch_guard(svc, sid, basis)

    return BoundVideoRequest(
        shot_id=sid, prompt=prompt, provider_mode=mode,
        first_frame=first_url, last_frame=last_url,
        video_reference=video_ref_url,
        identity_reference=identity_reference,
        scene_reference=scene_reference,
        used_reference_ids=used_ids, diagnostics=diagnostics,
        semantic_roles=roles, basis=basis, generation_basis=generation_basis)
