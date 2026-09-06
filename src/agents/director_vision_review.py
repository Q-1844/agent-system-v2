# src/agents/director_vision_review.py
# Visual Review Tool V1（2026-09-06）：visual.review_image —— Director 的只读视觉反馈能力。
#
# 定位（VISUAL_REVIEW_TOOL_V1 文档 §八/§十四/§十五/§三十）：
#   - 无状态、fresh-context、单次 Vision 请求（显式 agnes-2.5-flash + 恒走主端点）；
#   - 只做「看图 → 对照 canonical 要求 → 结构化反馈」；review 结果是 model judgment，
#     不是 canonical 事实；不写 project.json / Shot / Character / asset；
#     不自动重生成；不进 Director 聊天历史之外的任何 session。
# 资产访问（§十六，probe C2/C3 定案 2026-09-06）：
#   本地文件 → base64 data URI（Agnes 视觉后端直接消费；与 routers/shots.py 角色
#   参考图既有做法同源）。imgbb 公网 URL 对 vision 端点恒 500 "loading IMAGE
#   data"（C2/C3 隔离证实）——不要把审查图托管到 imgbb。仅当本地文件缺失时才
#   退回 candidate.url（Agnes 原始公网 URL，候选里本就保留）。
# 结构化输出（§七，probe E 定案）：Option A——JSON-only prompt + strict parse +
#   一次 repair + 明确失败；真机 10/10 解析成功且判断一致（function-calling 同为
#   10/10 但更重，故不采用）。
# Telemetry（§二十一）：每次审查把 model/usage(含 image_tokens)/latency/parse/
#   repairs/image_count 追加到 output/agents/director/visual_reviews.jsonl
#   （非 canonical 观测工件，同 bridge_messages 的 append-only 惯例）。
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..api.agnes_client import AgnesError
from ..core.store import ProjectStore
from ..core.models import Candidate, Project, Shot
from .runtime import AgentError, AgentRuntime
from .script_writer import _base_receipt, _parse_args

log = logging.getLogger("director_vision_review")

VISION_MODEL = "agnes-2.5-flash"

FOCUS_ALL = ("shot_adherence", "character_consistency", "visual_defects", "continuity")
VERDICTS = ("pass", "needs_fix", "uncertain")
SEVERITIES = ("none", "minor", "major", "critical")
CATEGORIES = ("shot_adherence", "character_consistency", "visual_defect", "continuity")

MAX_CHAR_REFS = 2          # 本镜首帧 + 邻镜首帧 + ≤2 张角色参考 = ≤4 图（probe D 实测 4 图可处理）
MAX_ISSUES = 5

# ============ 工具 schema（§十八：description 明确适用/不适用）============
VISION_REVIEW_TOOL = {
    "type": "function",
    "function": {
        "name": "visual.review_image",
        "description": (
            "视觉审查：用视觉模型真实查看一个镜头已生成的分镜图，对照该镜头的 canonical "
            "要求给出结构化反馈（verdict/severity/issues）。适用：用户要求查看/检查生成"
            "图片、判断图片是否符合镜头要求、检查角色视觉一致性、检查相邻镜头连续性。"
            "不适用：修改镜头/角色（用 shot.update）、重新生成图片（用 shot.generate_images）、"
            "纯文字层面的剧本/分镜审查。你无法直接看到图片内容——任何关于生成画面内容的"
            "判断都必须来自本工具的真实返回，禁止凭 prompt/文件名/描述假装看过图。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shot_id": {"type": "string", "description": "镜头 id（如 s05_c03）。"},
                "focus": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(FOCUS_ALL)},
                    "description": ("检查重点，默认 [shot_adherence, visual_defects]。"
                                    "character_consistency 会附角色三视图参考；"
                                    "continuity 会附同段上一镜画面。"),
                },
                "frame": {"type": "string", "enum": ["first", "last"],
                          "description": "审查首帧还是尾帧，默认 first。"},
            },
            "required": ["shot_id"],
        },
    },
}


# ---- 结构化输出契约（probe E 验证文本；§十二 Result schema）----
JSON_CONTRACT = (
    '{"verdict": "pass|needs_fix|uncertain",'
    ' "severity": "none|minor|major|critical",'
    ' "summary": "一句话总结（不超过80字）",'
    ' "issues": [{"category": "shot_adherence|character_consistency|visual_defect|continuity",'
    ' "severity": "minor|major|critical",'
    ' "observation": "画面中实际看到什么",'
    ' "expected": "要求是什么",'
    ' "suggested_fix": "建议怎么改"}]}'
)

SYSTEM_PROMPT = (
    "你是动画视觉质量审查器。只根据【提供的镜头要求/角色设定】与【实际图片】判断；"
    "不要虚构画面中看不到的信息。无法确认的项，在 observation 中写明无法确认的原因，"
    "并把 verdict 设为 uncertain，不要猜测。issues 最多 "
    f"{MAX_ISSUES} 条，每条简洁可行动。只输出一个 JSON 对象（结构如下），"
    "不要输出任何其他文字或代码围栏。\n"
    "字段纪律：verdict 只能取 pass / needs_fix / uncertain 三值之一（整体结论）；"
    "问题的严重程度一律放 severity 字段（none/minor/major/critical），"
    "绝不要把 critical/major 写进 verdict。\n"
    "JSON 结构：\n" + JSON_CONTRACT
)


# ============ 资产解析 ============
def _image_entry(path: Optional[Path], url: Optional[str]) -> Optional[Dict[str, str]]:
    """local 优先 → data URI；否则退回 url。两者皆无 → None。"""
    if path and path.exists():
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        b64 = base64.b64encode(path.read_bytes()).decode()
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
    if url:
        return {"type": "image_url", "image_url": {"url": url}}
    return None


def _resolve_asset_path(rt: AgentRuntime, project: str, local: Optional[str]) -> Optional[Path]:
    """canonical 的 local 路径以仓库根相对为主（routers/shots.py 同惯例）；
    兼容项目相对旧数据：直读不存在时再按项目目录拼一次。"""
    if not local:
        return None
    p = Path(local)
    if p.exists():
        return p
    try:
        alt = rt._resolve_project_dir(project) / local
        if alt.exists():
            return alt
    except Exception:
        pass
    return None


def _selected_candidate(shot: Shot, kind: str) -> Optional[Candidate]:
    batches = (shot.last_frame_batches if kind == "last"
               else shot.first_frame_batches) or []
    selected = shot.selected_last_frame if kind == "last" else shot.selected_first_frame
    for b in batches:
        for c in b.candidates:
            if c.candidate_id and c.candidate_id == selected:
                return c
    for b in batches:                      # 无指针/指针失效 → 第一个有资产的候选
        for c in b.candidates:
            if c.local or c.url:
                return c
    return None


def _shot_image_entry(rt, project: str, shot: Shot, kind: str) -> tuple:
    """本镜图像 entry + 来源描述。找不到任何可用图 → (None, reason)。"""
    cand = _selected_candidate(shot, kind)
    if cand is None:
        return None, f"镜头 {shot.shot_id} 没有{'尾帧' if kind == 'last' else '首帧'}生成记录"
    entry = _image_entry(_resolve_asset_path(rt, project, cand.local), cand.url)
    if entry is None:
        return None, f"镜头 {shot.shot_id} 的图像资产缺失（本地文件不存在且无可用 URL）"
    source = "local" if (entry is not None and cand.local
                         and _resolve_asset_path(rt, project, cand.local) is not None) else "url"
    return entry, {"candidate_id": cand.candidate_id, "source": source}


def _char_ref_entry(rt, project: str, proj: Project, name: str) -> tuple:
    """角色 front 视图 entry（无 front 退 side/back）。返回 (entry, view) 或 (None, reason)。"""
    char = next((c for c in proj.characters if c.name == name), None)
    if char is None:
        return None, f"角色 {name} 不在角色库"
    for view in ("front", "side", "back"):
        a = (char.views or {}).get(view)
        if not a:
            continue
        entry = _image_entry(_resolve_asset_path(rt, project, a.local), a.url)
        if entry is not None:
            return entry, view
    return None, f"角色 {name} 没有可用的三视图资产"


# ============ Context builder（§十：只发真正需要的内容）============
def _requirement_text(shot: Shot) -> str:
    parts = [f"- 镜头 id：{shot.shot_id}",
             f"- 画面描述：{shot.description or '（无）'}",
             f"- 出场人物：{'、'.join(shot.characters) if shot.characters else '（无要求）'}",
             f"- 景别：{shot.shot_size or '（未指定）'}",
             f"- 机位/运镜：{shot.camera or '（未指定）'}",
             f"- 机位角度：{shot.camera_angle or '（未指定）'}",
             f"- 构图：{shot.composition or '（未指定）'}",
             f"- 画面起始状态：{shot.opening_state or '（未指定）'}",
             f"- 重要画面细节：{shot.important_details or '（未指定）'}",
             ("- 必须出现的元素：" + "、".join(shot.required_elements)) if shot.required_elements else "- 必须出现的元素：（无）",
             f"- 对白（对白不应被画成画面内文字）：{shot.dialogue or '（无）'}"]
    return "\n".join(parts)


_FOCUS_TASKS = {
    "shot_adherence": (
        "人物是否正确（身份/数量）、动作是否符合画面起始状态、场景是否符合、"
        "景别与构图是否基本符合、关键道具/必须元素是否出现、对白文字是否被错误画进画面"),
    "character_consistency": (
        "画面中角色与所附角色参考图比较：发型/发色/服装/身形/标志性特征是否一致；"
        "没有可靠参考图时该项输出 uncertain，不要凭空判断"),
    "visual_defects": (
        "明显生成异常：多肢体/面部严重崩坏/重复人物/乱码或非预期文字/物体严重融合"),
    "continuity": (
        "与所附邻镜画面比较：服装/道具/人数/场景/大方向是否出现明显不连贯"),
}


def _build_user_text(shot: Shot, focus: List[str], char_infos: List[str],
                     prev_shot_id: Optional[str]) -> str:
    lines = ["【检查重点】"]
    for f in focus:
        lines.append(f"- {f}：{_FOCUS_TASKS[f]}")
    lines += ["", "【目标镜头 canonical 要求】", _requirement_text(shot)]
    if char_infos:
        lines += ["", "【角色设定（canonical）】"]
        lines += [f"- {ci}" for ci in char_infos]
    labels = ["图1=本镜画面"]
    idx = 2
    if "character_consistency" in focus and char_infos:
        for ci in char_infos:
            labels.append(f"图{idx}=角色参考图（{ci.split('：')[0]}）")
            idx += 1
    if "continuity" in focus and prev_shot_id:
        labels.append(f"图{idx}=邻镜 {prev_shot_id} 画面")
    lines += ["", "【随附图片（按提交顺序）】"] + [f"- {lb}" for lb in labels]
    lines += ["", "逐项检查后只输出 JSON。没有问题的项不要编造问题；"
                  "发现的问题按 severity 排序，最多 " + str(MAX_ISSUES) + " 条。"]
    return "\n".join(lines)


# ============ 输出解析（Option A：strict parse + 一次 repair）============
def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.startswith("json"):
            t = t[4:]
    return t.strip()


def _validate_review(obj: Any) -> List[str]:
    errs: List[str] = []
    if not isinstance(obj, dict):
        return ["root 不是 JSON 对象"]
    if obj.get("verdict") not in VERDICTS:
        errs.append(f"verdict 必须是 {'/'.join(VERDICTS)} 之一"
                    f"（当前为 {obj.get('verdict')!r}；严重程度请放 severity 字段）")
    if obj.get("severity") not in SEVERITIES:
        errs.append(f"severity 必须是 {'/'.join(SEVERITIES)} 之一"
                    f"（当前为 {obj.get('severity')!r}）")
    if not isinstance(obj.get("summary"), str) or not obj.get("summary", "").strip():
        errs.append("summary 缺失")
    iss = obj.get("issues")
    if not isinstance(iss, list):
        errs.append("issues 不是数组")
    else:
        for i, it in enumerate(iss[:MAX_ISSUES + 1]):
            if not isinstance(it, dict):
                errs.append(f"issues[{i}] 不是对象")
                continue
            if it.get("category") not in CATEGORIES:
                errs.append(f"issues[{i}].category 非法")
            if it.get("severity") not in SEVERITIES[1:]:
                errs.append(f"issues[{i}].severity 非法")
            if not isinstance(it.get("observation"), str) or not it["observation"].strip():
                errs.append(f"issues[{i}].observation 缺失")
    return errs


def _clean_review(obj: Dict[str, Any]) -> Dict[str, Any]:
    """白名单字段 + issues 截断（§二十：结果简洁，不把散文带进 Director 上下文）。"""
    issues = []
    for it in (obj.get("issues") or [])[:MAX_ISSUES]:
        issues.append({"category": it.get("category"),
                       "severity": it.get("severity"),
                       "observation": (it.get("observation") or "")[:300],
                       "expected": (it.get("expected") or "")[:200],
                       "suggested_fix": (it.get("suggested_fix") or "")[:200]})
    return {"verdict": obj.get("verdict"), "severity": obj.get("severity"),
            "summary": (obj.get("summary") or "")[:300], "issues": issues}


# ============ Telemetry（§二十一）============
def _persist_telemetry(rt, project: str, record: dict) -> None:
    try:
        d = rt._resolve_project_dir(project) / "output" / "agents" / "director"
        d.mkdir(parents=True, exist_ok=True)
        with (d / "visual_reviews.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        log.warning("visual_reviews.jsonl 写入失败（不影响审查结果）", exc_info=True)


# ============ 工具执行器 ============
async def _execute_visual_review(rt: AgentRuntime, project: str, arguments: str,
                                 instruction, message_id) -> dict:
    args = _parse_args(arguments)
    shot_id = (args.get("shot_id") or "").strip()
    if not shot_id:
        raise AgentError("bad_arguments", "缺少 shot_id")

    focus = args.get("focus")
    if focus is None:
        focus = ["shot_adherence", "visual_defects"]
    if isinstance(focus, str):
        focus = [focus]
    if (not isinstance(focus, list) or not focus
            or any(f not in FOCUS_ALL for f in focus)):
        raise AgentError("bad_arguments",
                         f"focus 非法（允许：{'、'.join(FOCUS_ALL)}）")

    frame = args.get("frame") or "first"
    if frame not in ("first", "last"):
        raise AgentError("bad_arguments", "frame 只允许 first/last")

    pdir = rt._resolve_project_dir(project)
    proj = ProjectStore(pdir).load()
    shot = next((s for sc in proj.scenes for s in sc.shots if s.shot_id == shot_id), None)
    if shot is None:
        raise AgentError("not_found", f"镜头不存在: {shot_id}")

    entry, meta = _shot_image_entry(rt, project, shot, frame)
    if entry is None:
        raise AgentError("no_image", str(meta))
    images: List[Dict[str, str]] = [entry]
    image_meta: List[dict] = [{"role": "shot", "shot_id": shot_id, **(meta if isinstance(meta, dict) else {})}]

    char_refs: List[str] = []          # 进 user text 的角色设定行
    if "character_consistency" in focus:
        if shot.characters:
            for name in shot.characters[:MAX_CHAR_REFS]:
                ref_entry, ref_meta = _char_ref_entry(rt, project, proj, name)
                if ref_entry is not None:
                    desc = next((c.description_en for c in proj.characters
                                 if c.name == name), "") or "（无英文设定）"
                    char_refs.append(f"{name}：{desc}")
                    images.append(ref_entry)
                    image_meta.append({"role": "char_ref", "character": name, "view": ref_meta})
                else:
                    char_refs.append(f"{name}：（参考图不可用：{ref_meta}；一致性判断请输出 uncertain）")
        else:
            char_refs.append("（镜头无出场角色字段）")

    prev_shot_id = None
    if "continuity" in focus:
        prev_shot = None
        for sc in proj.scenes:
            ids = [s.shot_id for s in sc.shots]
            if shot_id in ids:
                i = ids.index(shot_id)
                if i > 0:
                    prev_shot = sc.shots[i - 1]
                break
        if prev_shot is not None:
            pe, pm = _shot_image_entry(rt, project, prev_shot, "first")
            if pe is not None:
                images.append(pe)
                image_meta.append({"role": "prev_shot", "shot_id": prev_shot.shot_id,
                                   **(pm if isinstance(pm, dict) else {})})
                prev_shot_id = prev_shot.shot_id

    client = rt._resolve_client_or_none(agent_id="director")
    if client is None:
        raise AgentError("not_configured", "Agnes 客户端未就绪，无法执行视觉审查")
    if not hasattr(client, "chat_completion_vision"):
        raise AgentError("not_configured", "当前文本客户端不支持视觉审查请求")

    user_text = _build_user_text(shot, focus, char_refs, prev_shot_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [{"type": "text", "text": user_text}] + images}]

    def _vision(msgs):
        return client.chat_completion_vision(msgs, model=VISION_MODEL,
                                             temperature=0.2, max_tokens=1200)

    t0 = time.perf_counter()
    try:
        res = await _vision(messages)
    except AgnesError as e:
        # provider 429/5xx 已由 _request 重试耗尽；这里只做分类转译（诚实失败，不造假结论）
        raise AgentError("provider_error", f"视觉审查 provider 失败：{e}") from e
    latency = round(time.perf_counter() - t0, 2)

    obj, repairs = None, 0
    try:
        obj = json.loads(_strip_fence(res.get("content") or ""))
    except Exception:
        pass
    errs = _validate_review(obj) if obj is not None else ["parse_failed"]
    if errs:
        # 一次 repair（§七 Option A）：把原样输出退回，要求只给合法 JSON
        try:
            repair_res = await _vision(messages + [
                {"role": "assistant", "content": res.get("content") or ""},
                {"role": "user", "content":
                    "你的上次输出不是符合契约的 JSON（问题：" + "；".join(errs[:4]) +
                    "）。请重新只输出一个符合契约的 JSON 对象，不要任何其他文字。"}])
        except AgnesError as e:
            raise AgentError("provider_error", f"视觉审查 provider 失败：{e}") from e
        repairs = 1
        try:
            obj = json.loads(_strip_fence(repair_res.get("content") or ""))
        except Exception:
            obj = None
        errs = _validate_review(obj) if obj is not None else ["parse_failed"]
        if errs:
            raise AgentError(
                "review_parse_failed",
                f"视觉审查两次输出均不符合 JSON 契约（{'; '.join(errs[:4])}），已如实失败，不提供假结论")
    review = _clean_review(obj)

    usage = res.get("usage") or {}
    telemetry = {"model": res.get("model") or VISION_MODEL,
                 "prompt_tokens": usage.get("prompt_tokens"),
                 "completion_tokens": usage.get("completion_tokens"),
                 "total_tokens": usage.get("total_tokens"),
                 "image_tokens": (usage.get("prompt_tokens_details") or {}).get("image_tokens"),
                 "latency_s": latency, "repairs": repairs,
                 "image_count": len(images), "focus": focus, "frame": frame}

    reviewed_refs = {"shot_id": shot_id, "frame": frame, "images": image_meta}
    record = {"ts": time.time(), "project": project, "shot_id": shot_id,
              "review": review, "reviewed_refs": reviewed_refs,
              "telemetry": telemetry}
    _persist_telemetry(rt, project, record)

    return _base_receipt(
        "visual.review_image", ok=True, target=shot_id,
        shot_id=shot_id, review=review, reviewed_refs=reviewed_refs,
        telemetry=telemetry,
        note=(f"视觉审查完成：{review['verdict']}（{len(review['issues'])} 条问题；"
              f"{telemetry['image_count']} 图，{latency}s）。该结论是模型判断，"
              f"供参考，不是 canonical 事实。"))
