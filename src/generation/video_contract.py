# src/generation/video_contract.py
# Phase55 A（任务书 §3「统一参考链与生成契约」）：视频提交统一契约闸。
#
# 背景（Phase54 实证）：六员链场景连续性来源 = BOUND 路径把 sciv 场景参考/Look
# 送进视频生成参考链；Producer 两条入口（shot.generate_videos / shot.video
# capability）不 bind 不设哨兵 → 视频退化为「首帧单图 i2v」，场景图完全缺席。
# 另有 M5 事故：storyboard 层写入的 negative_prompt 经 kwargs 直通 client，
# 全部排队视频逐个失败（提交前无编译校验）。
#
# 本模块 = 三条视频提交入口（director 工具 / producer capability / REST）共用的
# 合法性编译与校验，职责：
#   1. 提交前校验（入队前明确报错，不让全部视频排队后才一起失败）：
#      - 时长契约（2.5：4-12s；目标时长越界显式拒绝，不静默夹取）；
#      - multi 无 binding（fail closed，与 handler 同口径提前到入队前）；
#      - 未知 job 参数拒绝（handler 只读已知键，静默忽略=谎报已生效）；
#      - BOUND 生产契约（require_binding=True 的入口：UNBOUND/NOT_READY 拒绝）。
#   2. negative_prompt 编译（项目层意图 → 合法正文约束，不静默丢参、不再撞
#      client 契约墙；编译事实随 manifest 落档）。
#   3. 场景参考消费判定（provider_reference / via_first_frame / absent）——
#      「图片准备好了却只在 prompt 中提到，不算已经传图」的可审计口径。
#   4. 统一输入依据清单（input_manifest_v1）——任务书 §3 要求的最小字段集。
# 设计约束：不新增事实存储——manifest 挂 job.params 与 VideoVersion.generation_basis
# （additive）；binding 事实只读 ShotBindingService/Store；不改变 owner/OCC 语义。
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

CONTRACT_VERSION = "video_contract_v1"
MANIFEST_VERSION = "input_manifest_v1"

# 项目级参考策略（Phase55 A→B 的接缝）：显式参数 > 项目级活动 run 策略 > 默认
# bound_required（基线等价）。Batch B 协作模式 run 启动时写入本文件并随 run 快照
# 版本化；无 run 时默认基线。文件缺失/损坏一律回落默认（fail closed 到基线）。
#
# Phase56 A2（reference_policy v2 = baseline_equiv_chain）：在 bound_required 全部
# 要求之上补齐上轮看片定性缺口（bind 已强制/接续链未强制）：
#   - 同场景相邻镜 independent opening 必须显式声明切镜语义（cut_reason），
#     不允许「未声明依据」的静默独立首帧；
#   - 场景参考消费基线只认 provider_reference / via_first_frame(full)；
#     context_only = 显式豁免（须登记项目级豁免清单），不再冒充已消费。
# 显式保留 bound_required（旧语义，在途 run 快照不受影响）与 legacy_allowed。
REFERENCE_POLICY_FILE = "reference_policy.json"
REFERENCE_POLICIES = ("bound_required", "legacy_allowed", "baseline_equiv_chain")

# 切镜语义（Phase56 A2 接续三分）——opening_source=independent 时的显式依据值：
#   same_scene_recam：同场景换机位/正反打（可重建首帧，相邻镜不因此成为媒体硬依赖）
#   scene_change    ：换场
#   time_jump       ：时间跳跃
# 同一动作真实继续不在此列——那应该用 opening_source=inherit_previous /
# reference_previous（真实依赖声明），而不是 independent+理由。
OPENING_CUT_REASONS = ("same_scene_recam", "scene_change", "time_jump")
SHOT_OPENING_CUT_REASON_FIELD = "opening_cut_reason"

# 2.5 视频契约时长边界（官方参数表 2026-09-28 核读；client 内也夹取——这里
# 提前显式报错，不做静默夹取）。
MIN_VIDEO_SECONDS = 4
MAX_VIDEO_SECONDS = 12

# video job 已知参数键（handler 实际消费的集合）。未知键 = 调用方误以为某开关
# 已生效——显式拒绝，不静默忽略（任务书 §3「未知字段……在提交前明确报错」）。
VIDEO_JOB_PARAM_KEYS = {
    "origin_run_id",
    "video_model",
    "population_constraint",
    "native_dialogue_control",
    "native_dialogue_language",
    "input_manifest",          # Phase55 A：统一输入依据清单（随 job 落盘）
    "input_manifest_error",    # Phase55 A：清单构建失败原因（诚实缺失，不阻断提交）
    # Phase56 A3：提交结果不明（generating 无 video_id）待核验后的显式人工
    # 确认重发标记——handler 拒绝无此标记的新提交；有标记仍标注
    # previous_submit_uncertain（重复风险如实落库）。
    "confirm_uncertain_resubmit",
    # Phase56 D1 修复闭环（combo v2 auto_repair）：修复溯源（复审批次/第几次
    # 修复 attempt）——随 job 落档，新候选不冒充首次结果。
    "repair_of_review", "repair_attempt",
    # 测试/fake 通道（AI-6F fake durable bridge，real=False 才使用）
    "_fake_outcome", "_fake_steps", "_fake_step_sleep",
}


def resolve_reference_policy(pdir, explicit: Optional[str] = None) -> str:
    """参考策略三级解析：显式参数 > 项目级活动 run 策略 > 默认 bound_required。
    只认合法值；文件缺失/损坏/非法值一律回落默认（fail closed 到基线）。"""
    if explicit in REFERENCE_POLICIES:
        return explicit
    try:
        p = Path(pdir) / "output" / "agents" / REFERENCE_POLICY_FILE
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            pol = d.get("policy")
            if pol in REFERENCE_POLICIES:
                return pol
    except Exception:   # noqa: BLE001 —— 读失败回落默认
        pass
    return "bound_required"


def set_project_reference_policy(pdir, policy: str, *, source: str = "manual",
                                 run_id: Optional[str] = None,
                                 exemptions: Optional[dict] = None) -> dict:
    """写项目级参考策略（Batch B 模式 run 启动时调用；测试可直接用）。
    非法值抛 ValueError（策略写入必须显式合法，不静默纠正）。
    exemptions（Phase56 A2）：{shot_id: reason}——context_only/absent 场景参考的
    显式豁免登记（v2 策略下未登记的未消费场景 = 提交拒绝）。"""
    if policy not in REFERENCE_POLICIES:
        raise ValueError(f"unknown reference policy: {policy}")
    doc = {"policy": policy, "source": source, "run_id": run_id}
    if exemptions:
        doc["exemptions"] = {str(k): str(v)[:200] for k, v in exemptions.items()}
    p = Path(pdir) / "output" / "agents" / REFERENCE_POLICY_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return doc


def read_policy_exemptions(pdir) -> dict:
    """读项目级豁免清单（{shot_id: reason}；文件缺失/损坏/无字段 = 空豁免）。"""
    try:
        p = Path(pdir) / "output" / "agents" / REFERENCE_POLICY_FILE
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            ex = d.get("exemptions")
            if isinstance(ex, dict):
                return ex
    except Exception:   # noqa: BLE001 —— 读失败如实空（豁免必须显式登记才生效）
        pass
    return {}


def continuity_semantics(shot: Any, prev_shot: Any) -> dict:
    """接续语义三分判定（Phase56 A2；只读不写）：
    - same_action_continuation：opening_source ∈ (inherit_previous, reference_previous)
      ——声明对上游实际裁切末帧的硬依赖（等必要审查/选版/端点就绪由既有机制保证）；
    - declared_cut：independent + 显式 cut_reason ∈ OPENING_CUT_REASONS；
    - undeclared_independent：independent 无 cut_reason——v2 策略下违规
      （「未声明依据」的静默独立首帧 = Phase55 冒烟衔接缺陷的契约根源）。
    prev_shot=None（本镜是开场镜/无同场景前驱）时 independent 视为 scene_change
    天然依据——不违规（依赖的增加必须来自真实制作需要，不给开场镜强加链条）。"""
    opening_source = str(getattr(shot, "opening_source", "") or "")
    if opening_source in ("inherit_previous", "reference_previous"):
        return {"semantics": "same_action_continuation",
                "opening_source": opening_source,
                "source_shot_id": getattr(shot, "source_shot_id", None),
                "detail": "真实接续：声明对上游裁切末帧的媒体硬依赖"}
    if opening_source == "independent":
        reason = str(getattr(shot, SHOT_OPENING_CUT_REASON_FIELD, "") or "").strip()
        if reason in OPENING_CUT_REASONS:
            return {"semantics": "declared_cut", "cut_reason": reason,
                    "detail": f"显式切镜依据：{reason}"}
        if prev_shot is None:
            return {"semantics": "declared_cut", "cut_reason": "scene_change",
                    "auto": True,
                    "detail": "开场镜/无同场景前驱——independent 即换场天然依据（自动记录）"}
        return {"semantics": "undeclared_independent",
                "detail": ("independent opening 未声明切镜依据——同场景相邻镜默认要求"
                           "接续链（inherit_previous/reference_previous）或显式声明 "
                           f"cut_reason ∈ {OPENING_CUT_REASONS}")}
    return {"semantics": "unknown_opening_source", "opening_source": opening_source,
            "detail": f"未知 opening_source: {opening_source}"}


def find_prev_scene_shot(project: Any, shot: Any) -> Any:
    """同场景、播放顺序在本镜之前的最近邻镜头（None=开场镜/无前驱）。
    播放顺序 = canonical shots 列表顺序（scene 内相对序）；不新造第二份排序。"""
    scene_id = getattr(shot, "scene_id", "")
    if not scene_id:
        return None
    prev = None
    for s in (getattr(project, "shots", None) or []) or \
             (project.all_shots() if hasattr(project, "all_shots") else []):
        if getattr(s, "shot_id", "") == getattr(shot, "shot_id", ""):
            return prev
        if getattr(s, "scene_id", "") == scene_id:
            prev = s
    return prev


def planned_video_seconds(shot: Any) -> Optional[float]:
    """提交时刻的规划时长（秒）：target_duration_s 优先，其次 num_frames/frame_rate。
    都没有 → None（handler 会按默认 4s 规划——契约下限，规划与实际一致）。"""
    tds = getattr(shot, "target_duration_s", None)
    if tds is not None:
        try:
            return float(tds)
        except (TypeError, ValueError):
            return None
    vf = getattr(shot, "video", None)
    nf = getattr(vf, "num_frames", 0) or 0
    fr = getattr(vf, "frame_rate", 0) or 0
    if nf and fr:
        return nf / fr
    return None


def reject_unknown_video_params(params: Optional[dict]) -> List[str]:
    """返回 video job params 中的未知键列表（空列表=合法）。"""
    unknown = [k for k in (params or {}) if k not in VIDEO_JOB_PARAM_KEYS]
    return sorted(unknown)


def compile_negative_prompt(text: str) -> str:
    """negative_prompt 项目层意图 → 合法正文约束（2.5 契约无此字段）。
    确定性编译：同输入恒同输出；调用方把返回值追加进 prompt 尾部，
    并停止向 client 传 negative_prompt（client 对非空仍显式拒绝=最终防线）。"""
    t = (text or "").strip()
    if not t:
        return ""
    return f"【负面约束——画面中不得出现/不得发生】{t}"


def scene_reference_consumed(project: Any, shot: Any, project_dir, validity: Optional[dict]) -> dict:
    """场景参考真实消费判定（可审计口径，不猜）：
    - provider_reference：multi + BOUND pack 含 sciv（第三参考槽真实发图）；
    - via_first_frame：i2v/keyframes 且已选首帧的 ImageBatch.generation_basis
      记录 scene_reference_mode=full（BOUND 首帧生成时场景图真实进了首帧制作）；
    - exempted_context_only：首帧批次 scene_reference_mode=context_only——场景图
      **未**作为视觉输入（仅语义上下文）。Phase55 冒烟看片定性：此态冒充
      「已消费」是衔接缺陷根因之一；Phase56 A2 起显式豁免态（不算已消费，
      baseline_equiv_chain 策略下须登记豁免才可提交）；
    - absent：两者皆无——场景只存在于 prompt 文本（=Phase54 新模式场景漂移根因）。
    只读，任何读取异常如实标注 unknown（不静默当成 absent/有场景）。"""
    shot_id = getattr(shot, "shot_id", "")
    link_mode = str(getattr(shot, "link_mode", "") or "")
    try:
        if validity is None:
            from src.shot_binding.service import ShotBindingService
            validity = ShotBindingService(project_dir, project_dir.name).read_validity(shot_id)
        readiness = validity.get("readiness")
        if link_mode == "multi" and readiness == "READY":
            return {"state": "provider_reference",
                    "detail": "BOUND multi：sciv 经参考链第三槽真实发送"}
        if readiness == "READY" or readiness == "NOT_READY":
            # BOUND i2v/keyframes：看已选首帧的批次 basis 是否带场景参考
            batch = _selected_first_frame_batch(shot)
            srm = ((batch or {}).get("generation_basis") or {}).get("scene_reference_mode")
            if srm == "full":
                return {"state": "via_first_frame",
                        "detail": "场景经 BOUND 首帧制作真实进入（scene_reference_mode=full）"}
            if srm == "context_only":
                return {"state": "exempted_context_only",
                        "detail": ("场景仅语义上下文（context_only）——未进入视觉输入；"
                                   "baseline_equiv_chain 下须登记显式豁免才可提交")}
            return {"state": "absent",
                    "detail": "BOUND 但首帧批次无场景参考记录（场景未进任何视觉输入）"}
        return {"state": "absent",
                "detail": f"无 BOUND 绑定（readiness={readiness}）——场景未进视觉输入"
                          "（首帧是否带场景取决于首帧生成时的参考策略，legacy 首帧不含）"}
    except Exception as e:   # noqa: BLE001 —— 判定失败不阻断提交，但必须诚实
        return {"state": "unknown", "detail": f"场景参考消费判定异常: {str(e)[:120]}"}


def _selected_first_frame_batch(shot: Any) -> Optional[dict]:
    """已选首帧所属批次的 generation_basis（dict 形态；取不到返回 None）。"""
    cid = getattr(shot, "selected_first_frame", None)
    if not cid:
        return None
    for b in (getattr(shot, "first_frame_batches", None) or []):
        if getattr(b, "batch_id", None) == cid or any(
                getattr(c, "candidate_id", None) == cid
                for c in (getattr(b, "candidates", None) or [])):
            basis = getattr(b, "generation_basis", None)
            if isinstance(basis, dict):
                return {"generation_basis": basis}
            return None
    return None


def build_input_manifest(project: Any, shot: Any, *, stage: str, mode: str,
                         video_model: Optional[str], seconds: Optional[float],
                         project_dir=None,
                         validity: Optional[dict] = None,
                         population_constraint: bool = True,
                         scene_ref: Optional[dict] = None,
                         compiled_negative_prompt: bool = False,
                         extra: Optional[dict] = None) -> dict:
    """统一输入依据清单（任务书 §3 最小字段集）。落 job.params["input_manifest"]，
    并在版本收货时并入 VideoVersion.generation_basis["input_manifest"]。
    只记录 id/版本/角色/哈希——媒体字节与 prompt 全文绝不进 manifest
    （与既有 basis 纪律一致：31.md C3）。"""
    shot_id = getattr(shot, "shot_id", "")
    manifest: Dict[str, Any] = {
        "schema": MANIFEST_VERSION,
        "contract_version": CONTRACT_VERSION,
        "stage": stage,
        "method": {"mode": mode, "model": video_model, "provider": "agnes"},
        "shot": {
            "shot_id": shot_id,
            "scene_id": getattr(shot, "scene_id", ""),
            "video_prompt_sig": getattr(shot, "video_prompt_sig", "") or "",
            "description_sha1": hashlib.sha1(
                (getattr(shot, "description", "") or "").encode("utf-8")).hexdigest()[:12],
            "opening_state": (getattr(shot, "opening_state", "") or "")[:200],
            "end_state": (getattr(shot, "end_state", "") or "")[:200],
        },
        "assets": _asset_inventory(shot, project_dir, validity),
        "upstream": {
            "opening_source": getattr(shot, "opening_source", ""),
            "source_shot_id": getattr(shot, "source_shot_id", None),
            "source_version": getattr(shot, "source_version", None),
            "video_reference_asset_id": getattr(shot, "video_reference_asset_id", None),
            # Phase56 A2：接续语义三分落档（same_action_continuation/declared_cut/
            # undeclared_independent）+ 显式切镜依据——「依赖的增加必须来自真实
            # 制作需要」的可审计记录。
            "continuity": continuity_semantics(
                shot, find_prev_scene_shot(project, shot)
                if project is not None else None),
        },
        "population": {"enabled": bool(population_constraint)},
        "reference_consumption": scene_ref or {"state": "unknown", "detail": "未判定"},
        "compiled_negative_prompt": bool(compiled_negative_prompt),
        "provider_plan": {
            "num_frames": getattr(getattr(shot, "video", None), "num_frames", None),
            "frame_rate": getattr(getattr(shot, "video", None), "frame_rate", None),
            "seconds": round(seconds, 2) if seconds else None,
        },
    }
    if extra:
        manifest.update(extra)
    return manifest


def _asset_inventory(shot: Any, project_dir, validity: Optional[dict]) -> dict:
    """资产与版本清单：BOUND 从 binding 文档取（look_id/pinned sciv/prop），
    UNBOUND 如实列出名字与 None（不冒充已传图）。"""
    characters: List[dict] = []
    scene: Dict[str, Any] = {}
    props: List[dict] = []
    binding = None
    if project_dir is not None and validity is not None \
            and validity.get("binding_state") == "bound":
        try:
            from src.shot_binding.store import ShotBindingStore
            doc = ShotBindingStore(project_dir, getattr(project_dir, "name", None)).load()
            binding = doc.bindings.get(getattr(shot, "shot_id", ""))
        except Exception:   # noqa: BLE001 —— 清单失败不阻断提交，如实留空
            binding = None
    if binding is not None:
        for cb in (getattr(binding, "character_bindings", None) or []):
            characters.append({
                "name": getattr(cb, "character_name", ""),
                "look_id": getattr(cb, "look_id", None),
                "composition_id": getattr(cb, "composition_id", None),
            })
        sb = getattr(binding, "scene_binding", None)
        if sb is not None:
            scene = {
                "scene_instance_id": getattr(sb, "scene_instance_id", None),
                "location_view_id": getattr(sb, "location_view_id", None),
                "pinned_scene_instance_view_id": getattr(
                    sb, "pinned_scene_instance_view_id", None),
            }
        for pb in (getattr(binding, "prop_bindings", None) or []):
            props.append({"prop_id": getattr(pb, "prop_id", ""),
                          "relation": getattr(pb, "relation", ""),
                          "anchor": getattr(pb, "anchor", "")})
    else:
        characters = [{"name": str(n or "").strip(), "look_id": None,
                       "note": "unbound——名字仅进 prompt，无参考图入链"}
                      for n in (getattr(shot, "characters", None) or [])]
    return {"characters": characters, "scene": scene, "props": props}


def policy_requires_binding(policy: Optional[str]) -> bool:
    """参考策略是否要求 BOUND 参考链（基线等价家族=v2 与旧基线都要求；
    仅 legacy_allowed 豁免）。"""
    return policy != "legacy_allowed"


def validate_video_submission(shot: Any, *, require_binding: bool = False,
                              validity: Optional[dict] = None,
                              params: Optional[dict] = None,
                              policy: Optional[str] = None,
                              project: Any = None,
                              pdir=None) -> dict:
    """视频提交前统一校验（三条入口共用；入队前明确报错）。
    policy=baseline_equiv_chain（Phase56 A2 v2）时追加接续链校验：
      - 场景参考消费须为 provider_reference / via_first_frame(full)；
        context_only/absent 须在项目级豁免清单登记，否则拒绝；
      - independent opening 须显式声明切镜依据（continuity_semantics）。
    返回 {ok, violations: [{code, detail, fix_hint}], compiled_negative_prompt,
    scene_reference_consumed}——ok=False 时调用方必须拒绝提交。"""
    violations: List[dict] = []
    seconds = planned_video_seconds(shot)
    if seconds is not None and not (MIN_VIDEO_SECONDS <= seconds <= MAX_VIDEO_SECONDS):
        violations.append({
            "code": "duration_out_of_contract",
            "detail": (f"规划时长 {round(seconds, 1)}s 超出 2.5 契约 "
                       f"{MIN_VIDEO_SECONDS}-{MAX_VIDEO_SECONDS}s"
                       "（系统不做静默夹取——夹取会产生与目标不符的素材）"),
            "fix_hint": (f"把 target_duration_s / 视频时长调到 "
                         f"{MIN_VIDEO_SECONDS}-{MAX_VIDEO_SECONDS}s 后重试"),
        })
    link_mode = str(getattr(shot, "link_mode", "") or "")
    readiness = (validity or {}).get("readiness")
    if link_mode == "multi" and readiness != "READY":
        violations.append({
            "code": "multi_without_binding",
            "detail": "multi 模式需要 production binding（视觉参考由 Binding 决定）",
            "fix_hint": "先 shot.production.inspect 看缺什么，再 shot.production.bind",
        })
    if require_binding and readiness == "UNBOUND":
        violations.append({
            "code": "binding_required",
            "detail": ("生产契约要求 BOUND 参考链（基线等价策略）：无绑定的视频只能"
                       "退化为首帧单图 i2v——场景/Look 参考不进链（Phase54 实证="
                       "场景漂移根因），不静默降级"),
            "fix_hint": ("先 shot.production.bind（含 approved Look 与场景实例视图），"
                         "或显式传 reference_policy=legacy_allowed 承担参考链缺失后果"),
        })
    elif require_binding and readiness == "NOT_READY":
        issues = [
            {"reason": i.get("reason"), "detail": str(i.get("detail") or "")[:160]}
            for i in ((validity or {}).get("issues") or [])][:6]
        violations.append({
            "code": "binding_not_ready",
            "detail": "生产绑定存在但未就绪（见 issues）",
            "fix_hint": "逐项解决依赖问题后 shot.production.refresh；不要原样重试生成",
            "issues": issues,
        })
    unknown = reject_unknown_video_params(params)
    if unknown:
        violations.append({
            "code": "unknown_params",
            "detail": f"未知 video 任务参数（handler 不消费，静默忽略=谎报生效）: {unknown}",
            "fix_hint": "移除未知参数；如需新开关先扩展契约（VIDEO_JOB_PARAM_KEYS）",
        })
    # ---- Phase56 A2：reference_policy v2（baseline_equiv_chain）接续链校验 ----
    _scene_ref: Optional[dict] = None
    if policy == "baseline_equiv_chain" and pdir is not None:
        _scene_ref = scene_reference_consumed(project, shot, pdir, validity)
        _state = _scene_ref.get("state")
        if _state in ("exempted_context_only", "absent"):
            _exemptions = read_policy_exemptions(pdir)
            if shot.shot_id not in _exemptions:
                violations.append({
                    "code": "scene_reference_not_consumed",
                    "detail": (f"v2 接续链策略：场景参考未进入合法视觉输入"
                               f"（state={_state}）——{_scene_ref.get('detail', '')[:160]}"
                               "；context_only 不再冒充已消费"),
                    "fix_hint": ("用 scene_reference_mode=full 重新制作首帧，或改为 "
                                 "multi+BOUND 让场景图进参考槽；确属豁免场景时在项目级"
                                 " reference_policy 豁免清单显式登记（写明原因）"),
                })
        _prev = find_prev_scene_shot(project, shot) if project is not None else None
        _cont = continuity_semantics(shot, _prev)
        if _cont.get("semantics") == "undeclared_independent":
            violations.append({
                "code": "opening_cut_undeclared",
                "detail": f"v2 接续链策略：{_cont.get('detail', '')[:200]}",
                "fix_hint": ("同一动作真实继续→改 opening_source=inherit_previous/"
                             "reference_previous；同场景换机位/换场/时间跳跃→显式设置 "
                             "opening_cut_reason（same_scene_recam/scene_change/"
                             "time_jump）"),
            })
    neg = (getattr(shot, "negative_prompt", "") or "").strip()
    return {
        "ok": not violations,
        "violations": violations,
        "compiled_negative_prompt": compile_negative_prompt(neg) if neg else "",
        "scene_reference_consumed": _scene_ref,   # v2 路径已算；其余由调用方回填
    }
