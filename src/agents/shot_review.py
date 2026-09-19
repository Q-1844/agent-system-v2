# src/agents/shot_review.py
# Shot-aware Vision Review（Phase D2，30.md §10-§13）：visual.review_shot。
#
# 与 visual.review_image（director_vision_review.py，通用视觉反馈）的分工：
#   review_image = Director 的随手看图工具（focus 灵活、 verdict 是模型判断）；
#   review_shot = 分镜质量闸门——输入包含 Shot creative intent / cast / 景别 / 机位 /
#   构图 / 绑定期望 Look / 场景上下文，输出 12 个固定维度的 pass|fail|uncertain，
#   overall 由代码确定性推导（30.md §12：hard gate 失败时模型不许用整体印象翻案）。
#
# 定位纪律：
#   - 无状态、fresh-context、单次 Vision 请求（agnes-2.5-flash）+ 一次 repair；
#   - 只读：不写 canonical、不自动重生成（反馈给 Director 决策）；
#   - 核心 review_shot_core 不依赖 AgentRuntime（A/B 评测、52 镜全量审查、
#     regression fixture 均直接复用同一条代码路径）；
#   - 资产访问：本地文件→data URI（与 review_image 同口径，imgbb 对 vision 端点 500）。
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..api.agnes_client import AgnesError
from ..core.models import Project, Shot
from ..core.store import ProjectStore
from .script_writer import _base_receipt, _parse_args
from .runtime import AgentError, AgentRuntime

log = logging.getLogger("shot_review")

VISION_MODEL = "agnes-2.5-flash"
MAX_LOOK_REFS = 2          # 本镜图 + ≤2 张 approved Look 参考（probe D：4 图内可靠）
MAX_FEEDBACK = 6

# 12 个固定维度（30.md §11）。HARD 的 fail = deterministic overall 不得 PASS。
SHOT_REVIEW_DIMENSIONS = (
    "cast_cardinality",        # 1 画面人物总数是否符合意图
    "duplicate_identity",      # 2 同一角色被复制成多人（hard）
    "unexpected_people",       # 3 路人/额外人物/幻觉 silhouette（hard）
    "missing_character",       # 4 expected cast 是否都出现（hard）
    "shot_size_correctness",   # 5 景别真实成立（hard）
    "subject_scale",           # 6 人物占比是否符合镜头目的
    "framing_and_composition", # 7 构图（fail=重大错配，hard）
    "camera_semantics",        # 8 OTS/POV/角度真实成立
    "scene_scope_correctness", # 9 场景入镜范围（CU 见局部=可 pass）
    "identity_look",           # 10 角色 identity/look 一致
    "prop_fidelity",           # 11 道具保真
    "anatomy_artifact",        # 12 解剖/生成伪影（fail=严重，hard）
    "scene_identity",          # 13 环境与 Location 身份一致（软维度，D2 §5 A/B 评测）
    "overall_quality",         # 14 整体图像质量/电影可读性（软维度，永不 hard-fail）
)
SHOT_REVIEW_HARD_DIMS = (
    "duplicate_identity", "unexpected_people", "missing_character",
    "shot_size_correctness", "framing_and_composition", "anatomy_artifact",
)
_STATUSES = ("pass", "fail", "uncertain")
OVERALLS = ("pass", "needs_fix", "uncertain")

# ============ 维度评审口径（写进 system prompt；§11 逐条）============
_DIM_RUBRICS = {
    "cast_cardinality":
        "画面里的人物总数是否符合镜头意图。expected cast 人数来自镜头要求；"
        "observed 以 people_count 为准。expected=1 observed=2 → fail。",
    "duplicate_identity":
        "同一个角色是否被复制成两个人（同脸/同服装/同发型出现两份）。这是 hard gate："
        "只要发现疑似复制 → fail 并在 note 写明两人在画面中的位置。",
    "unexpected_people":
        "画面里是否有镜头没要求的人物：路人、额外士兵、幻觉 silhouette、背景闲人。"
        "镜头没要求的任何人形存在 → fail。",
    "missing_character":
        "expected cast 是否全部出现。某角色完全不在画面 → fail；部分被遮挡但可辨识 → "
        "pass 并注明。",
    "shot_size_correctness":
        "景别是否真实成立（对照 observed.shot_size_observed，不要只相信镜头要求的字段）。"
        "CU 要求脸/头肩为画面主体；若实际是全身+完整环境 → fail。"
        "EWS/WS 允许人物很小但环境必须是主体。判断口径：EWS 远小于人/WS 全身含大量环境/"
        "MS 膝上/MCU 胸上/CU 头肩/ECU 面部或局部细节。",
    "subject_scale":
        "人物在画面中的占比是否符合镜头目的：区分「人物是画面主体」与「人物只是远处小点」。"
        "不要求精确百分比；与 shot_size 目的矛盾（如 CU 中人物占比过小）已在 "
        "shot_size_correctness 判 fail，此处只评 scale 是否服务镜头情绪/目的。",
    "framing_and_composition":
        "构图是否真正符合镜头的构图要求（rule of thirds / 有意居中 / headroom / lead room / "
        "前景利用 / 纵深 / 视觉焦点层级）。轻微偏差 → pass+note；明显违背构图意图"
        "（如要求过肩却两人并排全景、要求聚焦的脸被挤在角落）→ fail（重大错配，hard）。",
    "camera_semantics":
        "机位语义是否真实成立：OTS（必须有前景肩/头遮挡 + 焦点人物）、POV、低/高机位、"
        "侧面、背面、insert。prompt 里写了不等于成立——以画面实际为准；"
        "镜头未要求特殊机位时，仅在实际明显冲突才 fail。",
    "scene_scope_correctness":
        "场景入镜范围是否符合镜头类型：近景/特写只看到一面墙/灯光/雨/门框等局部环境"
        "完全可以 pass（只要局部与地点一致）；绝不因为「没有把整个场景画出来」而 fail。"
        "只有当环境元素与镜头要求的地点/状态明显矛盾时才 fail。",
    "identity_look":
        "画面角色与所附参考图比较：发型/发色/服装/身形/标志性特征是否一致；"
        "无可靠参考图 → uncertain，不要凭空判断。",
    "prop_fidelity":
        "镜头要求的道具是否出现且形态正确（数量/朝向/状态）；镜头未要求道具时"
        "仅在意画面出现道具与要求矛盾才 fail。",
    "anatomy_artifact":
        "生成伪影：多余肢体/手融合/武器复制/不可能解剖/面部崩坏。严重 → fail（hard）；"
        "轻微（个别手指含糊、远景面部简化）→ pass+note 或 uncertain。",
    "scene_identity":
        "画面环境是否与「场景上下文（Binding）」给出的地点身份一致：建筑样式/材质/"
        "空间感/时代感。近景只看到局部时，判断局部元素是否与地点相符（这评的是身份，"
        "不是入镜范围——范围由 scene_scope_correctness 管）。无法判断 → uncertain。",
    "overall_quality":
        "整体图像质量与电影可读性：光影、层次、风格一致、作为分镜是否可用。"
        "本维度是软维度，只反映质量印象，绝不参与 hard gate。",
}

_SHOT_SIZE_VOCAB = "EWS(extreme wide)/WS(wide)/MS(medium)/MCU(medium close-up)/CU(close-up)/ECU(extreme close-up)"

SHOT_REVIEW_JSON_CONTRACT = (
    '{"observed": {'
    '"people_count": 画面中人物总数(int，含背景人形，0 表示无人),'
    '"people_notes": "每个人是什么（身份推断/是否疑似同一人复制/是否路人）",'
    '"shot_size_observed": "实际画面景别，取 ' + _SHOT_SIZE_VOCAB + ' 之一",'
    '"camera_semantics_observed": "实际成立的机位语义（OTS/POV/低机位/高机位/侧面/背面/insert/none）",'
    '"scene_visibility": "full_location|partial_environment|minimal_background"},'
    ' "dimensions": {'
    + "".join(f'"{k}": {{"status": "pass|fail|uncertain", "note": "≤60字依据"}}'
              f"{"," if i < len(SHOT_REVIEW_DIMENSIONS) - 1 else ""}"
              for i, k in enumerate(SHOT_REVIEW_DIMENSIONS)) + '},'
    ' "director_feedback": ["可执行导演反馈，每条一句（如 expected CU observed MS; '
    'character occupies too little frame）"],'
    ' "summary": "一句话总结"}'
)

SHOT_REVIEW_SYSTEM_PROMPT = (
    "你是动画分镜质量闸门审查器（shot-aware）。你拿到：目标镜头的导演意图（景别/机位/"
    "构图/cast/道具/对白）、可选的出场角色 Look 参考图、以及待审分镜图（图1）。"
    "你只依据【提供的意图/参考】与【画面实际内容】判断，不虚构看不到的信息。\n"
    "评审纪律：\n"
    "1. 逐项给出 12 个维度 status（pass/fail/uncertain）+ ≤60 字 note；口径如下：\n"
    + "".join(f"   - {k}：{_DIM_RUBRICS[k]}\n" for k in SHOT_REVIEW_DIMENSIONS) +
    "2. people_count 数的是画面里所有人形（含背景），克隆/复制要在 people_notes 标明。\n"
    "3. 不许用整体印象翻案：某个维度失败就是失败，即使图整体好看。"
    "没有问题的维度不要编造问题；无法确认的 → uncertain 并写明原因。\n"
    "4. director_feedback 给可执行反馈（面向导演：改景别/改构图/改参考策略/重生成），"
    f"最多 {MAX_FEEDBACK} 条，每条一句。\n"
    "只输出一个 JSON 对象（结构如下），不要任何其他文字或代码围栏：\n"
    + SHOT_REVIEW_JSON_CONTRACT
)


# ============ 工具 schema ============
REVIEW_SHOT_TOOL = {
    "type": "function",
    "function": {
        "name": "visual.review_shot",
        "description": (
            "Shot-aware 分镜质量审查（Phase D2）：真实查看一个镜头的首帧/尾帧，按 12 个"
            "固定维度逐项评审（cast 人数/克隆人/额外人物/缺角色/景别真实成立/主体占比/"
            "构图/机位语义/场景入镜范围/identity/道具保真/解剖伪影），overall 由代码按 "
            "hard gate 确定性推导（duplicate_identity 等失败时绝不 pass），并返回可执行"
            "导演反馈。适用：生成后的分镜质量收口、重生成前的依据、52 镜质量矩阵。"
            "不适用：修改镜头（用 shot.update）、重新生成（用 shot.generate_images）。"
            "你无法直接看到图片内容——画面判断必须来自本工具的真实返回。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shot_id": {"type": "string", "description": "镜头 id（如 s05_c03）。"},
                "frame": {"type": "string", "enum": ["first", "last"],
                          "description": "审查首帧（默认）还是尾帧。"},
            },
            "required": ["shot_id"],
        },
    },
}


# ============ deterministic overall（30.md §12：代码推导，不信模型最后一句）============

def derive_overall(dimensions: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """hard gate 任一 fail → overall=needs_fix（模型不得覆盖）；
    其余 fail → needs_fix；无 fail 有 uncertain → uncertain；否则 pass。"""
    dims = {k: (dimensions or {}).get(k) or {"status": "uncertain", "note": "维度缺失"}
            for k in SHOT_REVIEW_DIMENSIONS}
    hard_fails = [k for k in SHOT_REVIEW_HARD_DIMS if dims[k]["status"] == "fail"]
    soft_fails = [k for k, v in dims.items()
                  if v["status"] == "fail" and k not in SHOT_REVIEW_HARD_DIMS]
    uncertains = [k for k, v in dims.items() if v["status"] == "uncertain"]
    if hard_fails or soft_fails:
        overall = "needs_fix"
    elif uncertains:
        overall = "uncertain"
    else:
        overall = "pass"
    return {"overall": overall, "hard_fails": hard_fails,
            "soft_fails": soft_fails, "uncertains": uncertains}


# ============ 输入构建 ============

def _image_entry(path: Optional[Path], url: Optional[str]) -> Optional[Dict[str, str]]:
    if path and path.exists():
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        b64 = base64.b64encode(path.read_bytes()).decode()
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
    if url:
        return {"type": "image_url", "image_url": {"url": url}}
    return None


def _resolve_local(project_dir: Path, local: Optional[str]) -> Optional[Path]:
    if not local:
        return None
    p = Path(local)
    if p.exists():
        return p
    alt = project_dir / local
    return alt if alt.exists() else None


def _binding_context(project_dir: Path, shot: Shot) -> tuple:
    """(expected_cast infos, scene_line)——从 binding/pack 取期望 Look 与场景语义；
    UNBOUND / 任何失败 → ([], "")（审查继续，靠 shot 字段与 uncertain 纪律兜底）。"""
    infos: List[str] = []
    scene_line = ""
    try:
        from src.shot_binding import ShotBindingService
        svc = ShotBindingService(project_dir, project_dir.name)
        v = svc.read_validity(shot.shot_id)
        if v.get("readiness") != "READY":
            return infos, scene_line
        compiled = svc.compile_reference_pack(shot.shot_id)
        if compiled.get("status") != "COMPILED":
            return infos, scene_line
        pack = compiled["pack"]
        scene = pack.get("scene") or {}
        state = scene.get("view_relevant_scene_state") or {}
        bits = [str(state.get(k)) for k in
                ("time_of_day", "weather", "lighting", "environment_conditions")
                if state.get(k)]
        if scene.get("location_name"):
            scene_line = (f"{scene.get('location_name')} / view={scene.get('view_label') or '-'}"
                          + (f" / {' , '.join(bits)}" if bits else ""))
        from src.resources.store import ResourceStore
        rdoc = ResourceStore(project_dir, project_dir.name).load()
        for ch in pack.get("characters") or []:
            look = rdoc.looks.get(ch.get("look_id"))
            label = (look.label if look else "") or ch.get("look_label") or ""
            desc = ch.get("appearance_description") or ""
            delta = ch.get("appearance_delta") or ""
            infos.append(f"{ch.get('character_name')}（Look: {label}）：{desc}"
                         + (f" [状态差异: {delta}]" if delta else ""))
    except Exception:
        log.warning("binding context 构建失败（review 继续用 shot 字段）", exc_info=True)
    return infos, scene_line


def _look_ref_entries(project_dir: Path, shot: Shot, proj: Project,
                      max_refs: int = MAX_LOOK_REFS) -> List[Dict[str, str]]:
    """出场角色的 approved Look 参考图（identity 真相源；最多 max_refs 张）。"""
    entries: List[Dict[str, str]] = []
    try:
        from src.shot_binding import ShotBindingService
        from src.resources.store import ResourceStore
        svc = ShotBindingService(project_dir, project_dir.name)
        rdoc = ResourceStore(project_dir, project_dir.name).load()
        for cb in (svc.read_binding(shot.shot_id).get("character_bindings") or []):
            if len(entries) >= max_refs:
                break
            name = cb.get("character_name")
            look = rdoc.looks.get(cb.get("look_id"))
            hero = (look.approved_reference_asset_ids or [None])[0] if look else None
            asset = rdoc.reference_assets.get(hero) if hero else None
            if asset is None:
                continue
            p = _resolve_local(project_dir, asset.local)
            entry = _image_entry(p, asset.url)
            if entry is not None:
                entries.append({"name": name, "entry": entry,
                                "label": (look.label if look else "") or ""})
    except Exception:
        log.warning("Look 参考图解析失败（identity 维度将 uncertain）", exc_info=True)
    return entries


def build_review_user_text(shot: Shot, *, look_infos: List[str], scene_line: str,
                           frame: str, has_look_refs: bool) -> str:
    lines = ["【本镜导演意图（canonical）】",
             f"- 镜头 id：{shot.shot_id}（审查{'尾帧' if frame == 'last' else '首帧'}）",
             f"- 画面描述：{shot.description or '（无）'}",
             f"- 景别要求：{shot.shot_size or '（未指定）'}",
             f"- 机位/角度：{shot.camera or '（未指定）'} / {shot.camera_angle or '（未指定）'}",
             f"- 构图：{shot.composition or '（未指定）'}",
             f"- 画面起始状态：{shot.opening_state or '（未指定）'}",
             f"- 重要画面细节：{shot.important_details or '（未指定）'}",
             f"- 出场人物（expected cast）：{'、'.join(shot.characters) if shot.characters else '（无——画面不应出现任何人物）'}",
             f"- 对白（对白不应被画成画面内文字）：{shot.dialogue or '（无）'}"]
    if shot.required_elements:
        lines.append(f"- 必须出现的元素：{'、'.join(shot.required_elements)}")
    if scene_line:
        lines.append(f"- 场景上下文（Binding）：{scene_line}")
    if look_infos:
        lines += ["", "【期望 Look（Binding）】"] + [f"- {x}" for x in look_infos]
    else:
        lines += ["", "【期望 Look（Binding）】（不可用——identity_look 请按 uncertain 纪律）"]
    lines += ["", "【随附图片】", "- 图1=待审分镜图"]
    if has_look_refs:
        lines.append("- 图2+=出场角色 approved Look 参考图（identity 比对基准）")
    lines += ["", "逐维度评审后只输出 JSON。注意：scene_scope_correctness 绝不因"
                  "「近景没画全整个场景」而 fail；shot_size_correctness 必须按画面实际判断。"]
    return "\n".join(lines)


# ============ 输出解析（strict parse + 一次 repair）============

def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.startswith("json"):
            t = t[4:]
    return t.strip()


def _validate_shot_review(obj: Any) -> List[str]:
    errs: List[str] = []
    if not isinstance(obj, dict):
        return ["root 不是 JSON 对象"]
    obs = obj.get("observed")
    if not isinstance(obs, dict):
        errs.append("observed 缺失")
    else:
        if not isinstance(obs.get("people_count"), int):
            errs.append("observed.people_count 必须是 int")
        for k in ("people_notes", "shot_size_observed", "camera_semantics_observed",
                  "scene_visibility"):
            if not isinstance(obs.get(k), str) or not str(obs.get(k)).strip():
                errs.append(f"observed.{k} 缺失")
    dims = obj.get("dimensions")
    if not isinstance(dims, dict):
        errs.append("dimensions 缺失")
    else:
        for k in SHOT_REVIEW_DIMENSIONS:
            d = dims.get(k)
            if not isinstance(d, dict) or d.get("status") not in _STATUSES:
                errs.append(f"dimensions.{k} status 非法")
    if not isinstance(obj.get("director_feedback"), list):
        errs.append("director_feedback 不是数组")
    if not isinstance(obj.get("summary"), str) or not obj.get("summary", "").strip():
        errs.append("summary 缺失")
    return errs


def _clean_shot_review(obj: Dict[str, Any]) -> Dict[str, Any]:
    dims = {}
    for k in SHOT_REVIEW_DIMENSIONS:
        d = (obj.get("dimensions") or {}).get(k) or {}
        dims[k] = {"status": d.get("status"),
                   "note": (d.get("note") or "")[:120]}
    return {
        "observed": {
            "people_count": obj.get("observed", {}).get("people_count"),
            "people_notes": (obj.get("observed", {}).get("people_notes") or "")[:300],
            "shot_size_observed": obj.get("observed", {}).get("shot_size_observed"),
            "camera_semantics_observed": obj.get("observed", {}).get("camera_semantics_observed"),
            "scene_visibility": obj.get("observed", {}).get("scene_visibility"),
        },
        "dimensions": dims,
        "director_feedback": [str(x)[:200] for x in (obj.get("director_feedback") or [])[:MAX_FEEDBACK]],
        "summary": (obj.get("summary") or "")[:300],
    }


def _persist_telemetry(project_dir: Path, record: dict) -> None:
    try:
        d = project_dir / "output" / "agents" / "director"
        d.mkdir(parents=True, exist_ok=True)
        with (d / "shot_reviews.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        log.warning("shot_reviews.jsonl 写入失败（不影响审查结果）", exc_info=True)


# ============ 核心（供工具 / A/B / 52 镜全量 / fixture 复用）============

async def review_shot_core(client: Any, *, project_dir: Path, proj: Project,
                           shot: Shot, frame: str = "first",
                           image_local: Optional[str] = None,
                           image_url: Optional[str] = None,
                           persist: bool = True) -> dict:
    """对一张分镜图做 shot-aware 审查。image_local/image_url 可显式给候选
    （A/B 候选不在 canonical 时用）；缺省用该镜 selected first/last frame。"""
    if frame not in ("first", "last"):
        raise ValueError("frame 只允许 first/last")
    project_dir = Path(project_dir)

    if image_local or image_url:
        entry = _image_entry(_resolve_local(project_dir, image_local), image_url)
        image_source = "explicit"
    else:
        from .director_vision_review import _selected_candidate
        cand = _selected_candidate(shot, frame)
        if cand is None:
            raise AgentError("no_image", f"镜头 {shot.shot_id} 没有"
                             f"{'尾帧' if frame == 'last' else '首帧'}生成记录")
        entry = _image_entry(_resolve_local(project_dir, cand.local), cand.url)
        image_source = cand.candidate_id or "selected"
    if entry is None:
        raise AgentError("no_image",
                         f"镜头 {shot.shot_id} 的图像资产缺失（本地不存在且无 URL）")

    look_infos, scene_line = _binding_context(project_dir, shot)
    look_refs = _look_ref_entries(project_dir, shot, proj)

    images = [entry] + [r["entry"] for r in look_refs]
    user_text = build_review_user_text(
        shot, look_infos=look_infos, scene_line=scene_line, frame=frame,
        has_look_refs=bool(look_refs))
    messages = [{"role": "system", "content": SHOT_REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": [{"type": "text", "text": user_text}] + images}]

    def _vision(msgs):
        return client.chat_completion_vision(msgs, model=VISION_MODEL,
                                             temperature=0.2, max_tokens=1600)

    t0 = time.perf_counter()
    try:
        res = await _vision(messages)
    except AgnesError as e:
        raise AgentError("provider_error", f"shot review provider 失败：{e}") from e
    latency = round(time.perf_counter() - t0, 2)

    obj, repairs = None, 0
    try:
        obj = json.loads(_strip_fence(res.get("content") or ""))
    except Exception:
        pass
    errs = _validate_shot_review(obj) if obj is not None else ["parse_failed"]
    if errs:
        try:
            repair_res = await _vision(messages + [
                {"role": "assistant", "content": res.get("content") or ""},
                {"role": "user", "content":
                    "你的上次输出不符合 JSON 契约（问题：" + "；".join(errs[:4]) +
                    "）。重新只输出一个符合契约的 JSON 对象，不要任何其他文字。"}])
        except AgnesError as e:
            raise AgentError("provider_error", f"shot review provider 失败：{e}") from e
        repairs = 1
        try:
            obj = json.loads(_strip_fence(repair_res.get("content") or ""))
        except Exception:
            obj = None
        errs = _validate_shot_review(obj) if obj is not None else ["parse_failed"]
        if errs:
            raise AgentError("review_parse_failed",
                             f"shot review 两次输出均不符合 JSON 契约（{'; '.join(errs[:4])}）")

    review = _clean_shot_review(obj)
    derived = derive_overall(review["dimensions"])

    usage = res.get("usage") or {}
    telemetry = {"model": res.get("model") or VISION_MODEL,
                 "total_tokens": usage.get("total_tokens"),
                 "image_tokens": (usage.get("prompt_tokens_details") or {}).get("image_tokens"),
                 "latency_s": latency, "repairs": repairs,
                 "image_count": len(images)}
    result = {
        "shot_id": shot.shot_id, "frame": frame,
        "image_source": image_source,
        "scene_reference_mode": None,     # A/B 评测时可由调用方补记
        "expected_cast": list(shot.characters or []),
        "review": review,
        "derived": derived,
        "telemetry": telemetry,
    }
    if persist:
        _persist_telemetry(project_dir, {"ts": time.time(), **result})
    return result


# ============ 工具执行器（Director 只读工具）============

async def _execute_review_shot(rt: AgentRuntime, project: str, arguments: str,
                               instruction, message_id) -> dict:
    args = _parse_args(arguments)
    shot_id = (args.get("shot_id") or "").strip()
    if not shot_id:
        raise AgentError("bad_arguments", "缺少 shot_id")
    frame = args.get("frame") or "first"
    if frame not in ("first", "last"):
        raise AgentError("bad_arguments", "frame 只允许 first/last")

    pdir = rt._resolve_project_dir(project)
    proj = ProjectStore(pdir).load()
    shot = proj.get_shot(shot_id)
    if shot is None:
        raise AgentError("not_found", f"镜头不存在: {shot_id}")

    client = rt._resolve_client_or_none(agent_id="director")
    if client is None or not hasattr(client, "chat_completion_vision"):
        raise AgentError("not_configured", "Agnes 视觉客户端未就绪")

    result = await review_shot_core(client, project_dir=pdir, proj=proj, shot=shot,
                                    frame=frame)
    derived = result["derived"]
    n_issues = len(derived["hard_fails"]) + len(derived["soft_fails"])
    return _base_receipt(
        "visual.review_shot", ok=True, target=shot_id, shot_id=shot_id,
        review=result["review"], derived=derived,
        observed=result["review"]["observed"],
        reviewed_refs={"shot_id": shot_id, "frame": frame,
                       "image_source": result["image_source"]},
        telemetry=result["telemetry"],
        note=(f"Shot 审查完成：overall={derived['overall']}"
              + (f"（hard fail: {', '.join(derived['hard_fails'])}）" if derived["hard_fails"] else "")
              + f"，{n_issues} 个维度失败，{len(result['review']['director_feedback'])} 条导演反馈。"
                "overall 为代码确定性推导；反馈供导演决策，不自动重生成。"))
