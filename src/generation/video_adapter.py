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


def _select_video_reference(pack: dict) -> Dict[str, Any]:
    """multi 槽参考的确定性选择（34.md §13：优先已验证的 identity reference 语义）。
    选择规则（确定性，无 LLM、无 glob）：
      1. cast 角色 look 参考（source_type=character_look）中 priority=primary 者
         （= look 的 hero approved 参考——R3 式身份参考的正式落点）；
      2. 否则任一 character_look 参考；
      3. 否则场景参考（scene_instance_view——无 cast 的环境建立镜）；
      4. 都没有 = 结构化失败（Binding READY 却无任何参考 = 上游错误）。
    顺序 = pack.references 存储顺序（cast 顺序 → look hero → extras，§34 语义序）。"""
    refs = [r for r in (pack.get("references") or [])
            if r.get("reference_asset_id")]
    char = [r for r in refs if r.get("source_type") == "character_look"]
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
    if mode == "multi":
        from src.resources.store import ResourceStore as _RS
        _multi_ref_item = _select_video_reference(pack)
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
    from src.core.prompt_builder import build_video_prompt
    from src.resources.store import ResourceStore
    rdoc = ResourceStore(project_dir, project_dir.name).load()
    creative = build_video_prompt(shot, mode)
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
        nav_pack = build_native_av_pack(
            project_dir, project, shot,
            visual_route="bound", visual_basis=bound_visual_basis(basis),
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
    if mode == "multi":
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
        used_reference_ids=used_ids, diagnostics=diagnostics,
        semantic_roles=roles, basis=basis, generation_basis=generation_basis)
