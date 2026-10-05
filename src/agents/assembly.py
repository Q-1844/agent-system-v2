# src/agents/assembly.py
# Assembly Agent（基础成片 Agent）— Phase 44B。
#
# 身份：第五个正式 Specialist（加法式扩展，registry.py SPECIALISTS 追加，
# 勿改既有 id）。用户可见名 = Assembly Agent（基础成片 / Assembly）。
#
# Canonical ownership（44B B0 设计决策，_44b/DESIGN_DECISIONS.md）：
#   拥有【且仅拥有】成片装配域写入：
#     - SequenceBlock（output/sequences.json：顺序 + trim + per-sequence 渲染 profile）
#       —— 全部经 SequenceService 正式链路（OCC revision / fail closed），绝不直写 JSON；
#     - AssemblyManifest（output/assembly/：成片输入契约 + 时间线投影，确定性构建器
#       唯一写入者，LLM 不得手写）；
#     - 剪辑提案文档（output/assembly/proposal_*.md，可评审建议，非 canonical）。
#
# 硬边界（物理保证非提示词约束——工具面里就没有越界工具）：
#   - 不调用任何视频/图片/音频生成（坏镜头 → 报告 shot_id + 症状 + 来源时间戳，
#     交视频制作线处理，绝不自己"修镜头"）；
#   - 不写 Story / Shot / Look / SIV / Shot.final_video / ProductionPlan（各自域的
#     Agent 所有物）；Shot.final_video 只读消费（Sequence §7 Source Video Truth）；
#   - 无 ffmpeg/shell 入口——ffmpeg 只存在于 src/sequence/media_ops.py 与
#     sequence_render job handler 的确定性代码中；Agent 永远不直接执行媒体命令；
#   - 无 final_render select 工具（§10 No Auto Select：select 是用户显式动作，
#     走既有 REST API）。
#
# 复用纪律：native tool-loop / SSE / session / OCC / receipts / 真相闸门 / 单写串行 /
# 重复失败护栏 / 超时恢复 / renewable window 全部沿用既有 Specialist Runtime 结构；
# Script Writer 共享基础设施直接 import 复用；tool-loop 按装配域镜像实现——
# 不改冻结的 script_writer.py / character_designer.py / scene_designer.py。
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from src.core.store import ProjectStore
from src.sequence.models import SequenceError

from .models import AgentMessage, ResultStatus
from .project_events import RESOURCE_SEQUENCE
from .runtime import AgentRuntime, AgentError
# —— Script Writer 共享基础设施（直接 import 复用，不改其文件）——
from .script_writer import (
    MAX_WEB_SEARCH,
    _TurnStopped,
    _base_receipt,
    _claims_search_success,
    _claims_success,
    _execute_web_search,
    _is_chat_turn,
    _is_explicit_write_request,
    _loose_json,
    _normalized_search_query,
    _parse_args,
    strip_reserved_fact_blocks,
)
from .revision_audit import DirectEditAuditStore

log = logging.getLogger("agents.assembly")

# 执行窗口（renewable window，同 Resource/Scene Agent 惯例）。
DEFAULT_MAX_TOOL_STEPS = 1024
# 真相收口：最终回复纠偏最多 1 次；仍不实则确定性诚实覆写。
MAX_CORRECTIVE_ROUNDS = 1
# 防失控：连续同一工具同一失败签名达此阈值 → 诚实停止。
MAX_REPEAT_FAIL = 3
# LLM read timeout 恢复次数。
MAX_TIMEOUT_RETRIES = 2

# 只读工具（不占写槽、可一次多个）。
ASSEMBLY_READ_TOOLS = {
    "assembly.briefing", "assembly.snapshot_read", "assembly.sequence_read",
    "assembly.media_probe", "assembly.timeline_preview", "assembly.render_status",
    "assembly.narration_read", "assembly.audio_plan_read",
    "assembly.audio_assets_read",
    "web.search",
}

# 写工具 → 资源锁域（唯一权威映射；流式端点的锁接线从这张表取）。
# Assembly 单写域：全部写工具锁 sequence 域（不锁 story / storyboard / 场景系统 / 资源域）。
WRITE_TOOL_DOMAIN: Dict[str, str] = {
    "assembly.sequence_create": RESOURCE_SEQUENCE,
    "assembly.sequence_update": RESOURCE_SEQUENCE,
    "assembly.render_submit": RESOURCE_SEQUENCE,
    "assembly.manifest_publish": RESOURCE_SEQUENCE,
    "assembly.proposal_write": RESOURCE_SEQUENCE,
    # Phase 44C：声音域（同一 sequence 资源锁域）
    "assembly.tts_synthesize": RESOURCE_SEQUENCE,
    "assembly.audio_plan_put": RESOURCE_SEQUENCE,
    "assembly.stem_render": RESOURCE_SEQUENCE,
}

# 冻结快照默认路径（44A §11 正式快照输入契约；相对仓库根）。
DEFAULT_SNAPSHOT_RELPATH = "_44a/assembly_input_manifest_formal.json"


# ============ system ============

def _snapshot_load(rt: AgentRuntime, project: str, relpath: Optional[str]) -> dict:
    """加载冻结快照 manifest（fail closed；路径仅允许仓库根内，防越界读）。

    Phase 47 修复（真实排障：新项目装配读到 44A 全局快照 → 跨项目对齐失败）：
    relpath 缺省时【先读本项目】output/assembly/snapshot_formal.json；
    不存在时才回落 44A 旧默认路径（保持雨灯归航等既有项目行为不变）。
    新项目应先用 assembly.snapshot_freeze 生成本项目正式快照。"""
    base: Path = rt._resolve_project_dir(project)
    # 项目根 = projects/<name> 的仓库根（上溯到含 _44a 的目录）
    repo_root = base
    for _ in range(4):
        if (repo_root / "_44a").exists() or (repo_root / "animation-storyboard" / "_44a").exists():
            break
        repo_root = repo_root.parent
    if relpath:
        p = Path(relpath)
    else:
        project_local = base / "output" / "assembly" / "snapshot_formal.json"
        p = project_local if project_local.exists() else Path(DEFAULT_SNAPSHOT_RELPATH)
    if not p.is_absolute():
        # 允许相对仓库根（animation-storyboard 子仓）或相对父仓
        cand = [repo_root / p, repo_root / "animation-storyboard" / p]
        p = next((c for c in cand if c.exists()), cand[0])
    p = p.resolve()
    roots = {Path(repo_root).resolve(), Path(repo_root / "animation-storyboard").resolve()}
    if not any(str(p).startswith(str(r)) for r in roots):
        raise AgentError("snapshot_path_denied", f"快照路径越界（仅允许仓库内）: {relpath}")
    if not p.exists():
        raise AgentError("snapshot_missing", f"快照 manifest 不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise AgentError("snapshot_invalid", f"快照 manifest 解析失败: {e}")
    if not isinstance(data, dict) or not data.get("shots"):
        raise AgentError("snapshot_invalid", "快照 manifest 缺少 shots")
    data.setdefault("_resolved_path", str(p))
    return data


def _snapshot_summary(snap: dict) -> dict:
    shots = []
    for s in snap.get("shots") or []:
        dp = s.get("duration_probe") or {}
        shots.append({
            "order": s.get("order"), "scene_id": s.get("scene_id"),
            "shot_id": s.get("shot_id"),
            "video_version_id": s.get("video_version_id"),
            "approved_status": s.get("approved_status"),
            "verdict_source": (s.get("verdict_source") or "")[:60],
            "duration_ms": dp.get("duration"),
            "width": dp.get("width"), "height": dp.get("height"),
            "has_audio": dp.get("has_audio"),
            "media_sha256": (s.get("media_sha256") or "")[:12] + "…",
        })
    return {
        "snapshot_id": snap.get("snapshot_id"), "mode": snap.get("mode"),
        "project_id": snap.get("project_id"),
        "resolved_path": snap.get("_resolved_path"),
        "shot_count": len(shots), "shots": shots,
    }


def _recommended_profile(rt: AgentRuntime, project: str, snap: dict) -> dict:
    """从冻结快照逐镜探测推导推荐渲染 profile（确定性）：
    分辨率取众数档；全部素材同档 → fill=pad（零像素损失），存在代际混档 →
    fill=crop（满幅一致，少量边缘裁切）；音频策略默认 native。"""
    from src.sequence.media_ops import probe_video
    pdir = rt._resolve_project_dir(project)
    dims: List[tuple] = []
    for s in snap.get("shots") or []:
        sm = s.get("snapshot_media") or s.get("media_uri") or ""
        f = Path(sm)
        if not f.is_absolute():
            cand = [pdir.parent.parent / sm,  # projects/<name>/../../<rel>（仓库根）
                    pdir / sm]
            f = next((c for c in cand if c.exists()), cand[0])
        try:
            pr = probe_video(str(f))
            dims.append((pr.get("width"), pr.get("height")))
        except Exception:
            continue
    if dims:
        majority = max(set(dims), key=dims.count)
        w, h = majority
        fill = "pad" if all(d == majority for d in dims) else "crop"
    else:
        w, h, fill = 1280, 720, "pad"
    return {"width": int(w), "height": int(h), "fps": 24, "vcodec": "libx264",
            "pix_fmt": "yuv420p", "acodec": "aac", "sample_rate": 48000,
            "channels": 2, "container": "mp4", "fill": fill, "audio": "native"}


def _sequences_brief(rt: AgentRuntime, project: str) -> list:
    pdir = rt._resolve_project_dir(project)
    from src.sequence.service import SequenceService
    svc = SequenceService(pdir, project)
    out = []
    for sid, entry in svc.list_entries().items():
        proj = ProjectStore(pdir).load()
        fr = svc.freshness(sid)
        out.append({
            "sequence_id": sid, "title": entry.block.title,
            "revision": entry.block.revision,
            "clip_count": len(entry.block.clips),
            "clips": [{"shot_id": c.shot_id, "trim_in_ms": c.trim_in_ms,
                       "trim_out_ms": c.trim_out_ms} for c in entry.block.clips],
            "render_profile": dict(entry.block.effective_render_profile()),
            "render_profile_is_override": entry.block.render_profile is not None,
            "render_versions": [{"version_id": r.version_id, "status": r.status,
                                 "planned_duration_ms": r.planned_duration_ms,
                                 "duration_ms": r.duration_ms,
                                 "output_file": r.output_file,
                                 "error": (r.error or "")[:120]}
                                for r in entry.render_versions],
            "final_render": entry.final_render,
            "final_freshness": fr.get("status"),
        })
    return out


def _manifest_brief(rt: AgentRuntime, project: str) -> dict:
    pdir = rt._resolve_project_dir(project)
    from src.sequence.assembly_manifest import load_assembly_index
    try:
        idx = load_assembly_index(pdir)
    except AgentError:
        idx = {}
    return {"latest_revision": idx.get("latest_revision") or 0,
            "history": [
                {k: h.get(k) for k in ("revision", "sequence_id", "sequence_revision",
                                       "doc_fingerprint", "total_duration_ms")}
                for h in (idx.get("history") or [])][-8:],
            }


def _build_system(proj, rt: AgentRuntime, project: str) -> str:
    try:
        snap = _snapshot_load(rt, project, None)
        snap_line = (f"{snap.get('snapshot_id')}（mode={snap.get('mode')}，"
                     f"{len(snap.get('shots') or [])} 镜）")
        snap_hint = "已就绪（assembly.snapshot_read 可读）"
    except AgentError as e:
        snap_line = f"不可用（{e.message}）"
        snap_hint = "不可用——如实报告用户，不得假装已读取"
    return ASSEMBLY_SYSTEM.format(
        project_name=proj.name or "（未命名）",
        scenes_count=len(proj.scenes or []),
        shots_count=sum(len(sc.shots or []) for sc in (proj.scenes or [])),
        snapshot_line=snap_line, snapshot_hint=snap_hint,
    )


ASSEMBLY_SYSTEM = """你是 AGNES 的 Assembly Agent（基础成片 Agent）——把已完成的镜头媒体装配成可观看的成片。你的工作不是改剧情、不是改镜头，而是：读取批准的项目结构与媒体 → 形成剪辑草案及理由 → 检查规则/依赖 → 发起预览渲染 → 观察导出诊断 → 修订草案 → 提交用户评审。

项目：{project_name}（{scenes_count} 个故事段，{shots_count} 个镜头）。
冻结快照：{snapshot_line}。快照状态：{snapshot_hint}。

【你只写你的域】SequenceBlock（顺序/trim/渲染 profile，经 assembly.sequence_* 工具，OCC revision）、AssemblyManifest（经 assembly.manifest_publish，确定性构建）、剪辑提案（assembly.proposal_write）。不修改剧本、不创建/修改镜头、不写角色/场景系统/生产资源、不修改 Shot.final_video。

【绝对禁止】
- 调用任何视频/图片/音频生成去"修镜头"。发现坏镜头/坏媒体：报告 shot_id、症状、来源时间戳（用 assembly.media_probe / timeline_preview 定位），交视频制作线处理。
- 用语言声称"已渲染成功"。成功只认工具回执：render version status=done + 真实 job_id + 输出路径 + ffprobe 实测（assembly.render_status 可查）。
- 给每处切点机械加溶解/桥接（renderer v1 只有硬切；转场建议写进提案，由用户裁定）。
- 把临时/未批准素材当已批准成片输出（快照 mode!=formal 时必须明确标注"工程预览"）。

【标准工作流】
1. assembly.briefing —— 一次性读全局状态（快照/sequence/manifest/推荐 profile）。
2. assembly.snapshot_read —— 逐镜版本/哈希/裁定状态。
3. 形成剪辑草案：默认按 canonical 叙事顺序【全部保留、整镜使用、硬切】；如剧情明确需要掐头去尾，用 assembly.timeline_preview 验证 trim 后再经 sequence_create/sequence_update 落 canonical（trim 依据写进提案）。
4. assembly.render_submit —— 显式发起渲染（每次渲染都是新 render version，绝不覆盖旧输出）。
5. assembly.render_status（可 deep_audit）—— 观察时长偏差/黑帧/静音/接缝诊断。
6. assembly.manifest_publish —— 渲染 done 后发布成片时间线契约（assembly_revision 自增）。
7. assembly.proposal_write —— 写提案（顺序依据/trim 依据/接缝注记/逐镜音频处理注记），提交用户评审。最终 select final_render 是用户的显式动作，你不能代替。

【声音工作流（原生音效/旁白/配乐/混音）】默认政策（Phase48 起）：全片禁止自动旁白；默认保留合格原生环境声与动作声；音乐仅按作品 brief 执行，绝不为每镜机械配乐。"没有配乐"≠"应该静音"——整条关原音只允许作为显式策略（render profile audio=silent 或用户 brief 明确要求），不得因为缺音乐/缺旁白而默认静音。
a. 先分类后决定：对每个入选镜头实测原生音轨（assembly.media_probe/ffprobe 实测 + 声学指标 + ASR 报告），标注【合格环境声/动作声】【混有音乐】【画面内对白】【意外旁白或乱语】【无音轨/近静音】之一，分类与依据写入提案。不得凭"文件有音轨"直接宣称合格，也不得只凭响度低就判静音（轻音效/近距离环境声响度也低）。
b. 默认保留：合格环境声与动作声原样保留（render profile audio=native；需要整体控增益时用 plan_config.base_gain_db）。assemble 产物必须让该保留的声音真实在场。
c. 意外旁白/乱语/不可接受音频：如实标记不合格并给修复选项（重生成该镜声音/保留原片另制音轨/局部处理方案与各自损失），交用户或政策裁定；不自动整片静音掩盖失败。
d. 画面内人物对白：原音候选保留并标注"待对白专项验收"，不静音、不替换、不当旁白处理。
e. 旁白：禁止自动生成或添加。仅当用户 brief 明确要求旁白时才走 narration_read→tts_synthesize 流程（落点纪律照旧：句内起止避开切点与转场）；无此要求时旁白工具一律不用。
f. 外部音乐/环境声层：仅按作品 brief；assembly.audio_assets_read 枚举资产库（origin 为 locally_* 的是本地测试垫乐，产物须如实标注 test_asset；正式授权音乐只能等用户提供）；source 用 kind="imported_audio" + file="audio_imports/…"（文件必须来自资产库，禁止虚构路径）；逐层 gain/fade/ducking 语义照旧；"原音静默"仍= plan_config.base_gain_db=-60（显式策略才可用）。
g. 渲染 done 后 deep_audit=true 音频段逐层核对（落窗+电平+LUFS+原生泄漏），并对照 a 的分类核查"该保留的是否真实在场、该排除的是否已处理"；不过关 → 修 plan 重渲，或在提案如实上报缺陷，绝不只凭一条通过就宣称完成。
h. assembly.stem_render 导出分轨供试听；manifest_publish 照旧投影；提案写明每镜音频处理决定、分类依据与试听方式。

当前 project 与 scope 见下方上下文。"""


# ============ 工具 schema（OpenAI function calling）============

BRIEFING_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.briefing",
        "description": ("成片任务入口：一次性读取项目全局状态——冻结快照摘要（逐镜版本/"
                        "哈希/裁定状态/时长）、全部 SequenceBlock 及渲染版本与 freshness、"
                        "AssemblyManifest 历史、以及按素材实测推导的【推荐渲染 profile】"
                        "（可直接传给 sequence_create）。开销中等（含逐镜 ffprobe）。只读工具。"),
        "parameters": {"type": "object", "properties": {}},
    },
}

SNAPSHOT_FREEZE_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.snapshot_freeze",
        "description": (
            "为本项目冻结一份正式装配快照（Phase 47）：从 live 真实状态逐镜读取 "
            "final_video 版本与文件 sha256，写入本项目 "
            "output/assembly/snapshot_formal.json。新项目（无 44A 快照）装配前"
            "必须先冻结；只 pin 当前 live final_video——不重写指针、不偷换素材、"
            "不生成媒体。shot_ids 缺省 = 全部有视频版本的镜头。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shot_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选，要冻结的镜头 id 列表（如 [\"s01_c01\"]）；缺省=全部。",
                },
                "note": {"type": "string", "description": "可选，冻结缘由（进 manifest）。",
                         },
            },
        },
    },
}

SNAPSHOT_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.snapshot_read",
        "description": ("读取冻结快照 manifest（44A 产出）：逐镜 order/scene_id/"
                        "video_version_id/approved_status/media_sha256/duration_probe/"
                        "source_receipt。shot_id 缺省 = 全量摘要；指定 = 单镜全字段。只读工具。"),
        "parameters": {
            "type": "object",
            "properties": {
                "shot_id": {"type": "string",
                            "description": "可选，单镜 id（如 s01_c04）。"},
                "relpath": {"type": "string",
                            "description": "可选，快照 manifest 相对仓库根路径。通常【留空】即读默认正式快照——不要猜路径。"},
            },
        },
    },
}

SEQUENCE_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.sequence_read",
        "description": ("读取 SequenceBlock canonical 详情：clips/trim/渲染 profile/"
                        "render versions（状态/输出/误差）/final_render/freshness/"
                        "音轨策略。sequence_id 缺省 = 列出全部（轻量）。只读工具。"),
        "parameters": {
            "type": "object",
            "properties": {
                "sequence_id": {"type": "string", "description": "可选，seq_xxxx。"},
            },
        },
    },
}

MEDIA_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.media_probe",
        "description": ("对媒体文件 ffprobe 实测（尺寸/帧率/编码/pix_fmt/时长/音轨）。"
                        "manifest=true 时对冻结快照逐镜批量探测（核对每镜尺寸/音轨/时长，"
                        "定位代际混档与异常媒体）。只读工具。"),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "可选，媒体文件路径（仓库内相对或绝对）。"},
                "manifest": {"type": "boolean",
                             "description": "true = 探测冻结快照全部镜头。"},
                "relpath": {"type": "string",
                            "description": "可选，换用其它快照 manifest。"},
            },
        },
    },
}

TIMELINE_PREVIEW_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.timeline_preview",
        "description": ("【dry-run】对剪辑草案做确定性时间线预演：解析源真相（Shot."
                        "final_video）→ ffprobe 实测 → 校验 trim/哈希/版本 pin → 输出逐镜"
                        "起止表与总片长。不写 canonical、不渲染。任何违规（trim 非法/版本"
                        "不符/哈希不符/媒体缺失）会逐镜报错定位。只读工具。"),
        "parameters": {
            "type": "object",
            "properties": {
                "clips": {
                    "type": "array",
                    "description": "顺序即播放顺序。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "shot_id": {"type": "string"},
                            "trim_in_ms": {"type": "integer",
                                           "description": "从源开头裁掉的毫秒（默认 0）。"},
                            "trim_out_ms": {"type": "integer",
                                            "description": "从源结尾裁掉的毫秒（默认 0）。"},
                        },
                        "required": ["shot_id"],
                    },
                },
                "relpath": {"type": "string",
                            "description": "可选，换用其它快照 manifest 做 pin 核验。"},
            },
            "required": ["clips"],
        },
    },
}

RENDER_STATUS_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.render_status",
        "description": ("观察渲染：sequence_id 缺省 = 列出全部 render versions（状态/"
                        "计划 vs 实测时长/误差/错误）。指定 version_id = 单版详情；"
                        "deep_audit=true 时对 done 输出做帧级审计（PTS 单调/重复帧/缺帧/"
                        "黑帧/切点检查/静音段，advisory）。只读工具。"),
        "parameters": {
            "type": "object",
            "properties": {
                "sequence_id": {"type": "string", "description": "可选。"},
                "version_id": {"type": "string", "description": "可选，srender_xxxx。"},
                "deep_audit": {"type": "boolean",
                               "description": "对 done 输出做帧级/接缝审计（较慢）。"},
            },
        },
    },
}

SEQUENCE_CREATE_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.sequence_create",
        "description": ("创建 SequenceBlock（成片 canonical）：clips 顺序即播放顺序；"
                        "创建前自动做冻结快照对齐核验（逐镜 live final==快照 pin 且 "
                        "sha256 相等，任何不符 → 拒绝并逐镜报错）。render_profile 建议"
                        "用 briefing 给的推荐值（含 fill/audio 策略）。写工具（OCC/审计）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "clips": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "shot_id": {"type": "string"},
                            "trim_in_ms": {"type": "integer"},
                            "trim_out_ms": {"type": "integer"},
                        },
                        "required": ["shot_id"],
                    },
                },
                "render_profile": {
                    "type": "object",
                    "description": ("可选，完整渲染规格（briefing 的 "
                                    "recommended_render_profile 原样传入）；缺省 = 全局默认。"),
                },
                "expected_snapshot_id": {"type": "string",
                                         "description": "可选，防快照换版（不符 → 拒绝）。"},
            },
            "required": ["clips"],
        },
    },
}

SEQUENCE_UPDATE_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.sequence_update",
        "description": ("更新 SequenceBlock：clips 整体替换（顺序即播放顺序）/trim/"
                        "渲染 profile。必须带 expected_revision（上一次 sequence_read/"
                        "写入返回的 revision），不符 → 409 OCC 冲突（返回最新 revision）。"
                        "写工具。"),
        "parameters": {
            "type": "object",
            "properties": {
                "sequence_id": {"type": "string"},
                "clips": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "shot_id": {"type": "string"},
                            "trim_in_ms": {"type": "integer"},
                            "trim_out_ms": {"type": "integer"},
                        },
                        "required": ["shot_id"],
                    },
                },
                "render_profile": {"type": "object",
                                   "description": "可选；空对象 = 清除覆盖回全局默认。"},
                "title": {"type": "string"},
                "expected_revision": {"type": "string",
                                      "description": "OCC 门：你最近一次看到的 8 位 revision。"},
            },
            "required": ["sequence_id", "expected_revision"],
        },
    },
}

RENDER_SUBMIT_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.render_submit",
        "description": ("显式发起一次成片渲染：append 新 render version（queued）+ 入队 "
                        "sequence_render 作业（三阶段：prepare→ffmpeg→commit）。返回 "
                        "job_id/render_version_id/计划时长。绝不覆盖旧输出；重试=再次调用"
                        "（新版本）。写工具。"),
        "parameters": {
            "type": "object",
            "properties": {
                "sequence_id": {"type": "string"},
            },
            "required": ["sequence_id"],
        },
    },
}

MANIFEST_PUBLISH_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.manifest_publish",
        "description": ("渲染 done 后发布 AssemblyManifest 新版（成片时间线契约：逐镜 "
                        "版本/哈希 pin + source_in/out + timeline 起止 + 跨组切点标记 + "
                        "音轨策略 + 旁白/配乐占位）。确定性构建器生成（assembly_revision "
                        "自增，只追加）。写工具。"),
        "parameters": {
            "type": "object",
            "properties": {
                "sequence_id": {"type": "string"},
                "narration_cues": {"type": "array",
                                   "description": "可选，旁白 cue（B 批通常留空，C 批接线）。"},
            },
            "required": ["sequence_id"],
        },
    },
}

PROPOSAL_WRITE_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.proposal_write",
        "description": ("写可评审剪辑提案文档（output/assembly/proposal_v{N}.md）：顺序"
                        "依据、trim 依据（列原镜头与截取范围）、接缝注记、转场【建议】、"
                        "旁白候选、需要用户终审的事项。提案不是 canonical——用户看后才生效。"
                        "写工具。"),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "提案标题。"},
                "content_md": {"type": "string",
                               "description": "Markdown 全文（依据/清单/待审事项）。"},
            },
            "required": ["title", "content_md"],
        },
    },
}

# ---- Phase 44C：旁白 / 音乐 / 混音工具面（加法式）----

NARRATION_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.narration_read",
        "description": ("旁白编排入口：读取故事段全文、时间线逐镜窗口、已合成旁白"
                        "版本（含试听文件/时长/回执）、当前 AudioPlan。用于提出 cue "
                        "候选与核对落点。传 sequence_id 时窗口按该 sequence 的 "
                        "canonical 复算（推荐：先建自己的 sequence 再排旁白）；"
                        "缺省回退最新 manifest。只读工具。"),
        "parameters": {"type": "object",
                       "properties": {"sequence_id": {"type": "string",
                                                      "description": "可选，seq_xxxx"}},
                       "required": []},
    },
}

TTS_SYNTHESIZE_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.tts_synthesize",
        "description": ("合成（或幂等复用）一句旁白：MiniMax TTS，产物 = 48kHz WAV 进 "
                        "audio_imports/narration/，回执含版本号/哈希/实测时长/trace_id。"
                        "同 cue_id+同文本+同参数 = 缓存命中零调用。cue 是短句（≤200 字）。"
                        "写工具（旁白产物仓，幂等安全）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "cue_id": {"type": "string",
                           "description": "cue 身份（如 nc01；同一 cue 改文案=新版本）。"},
                "text": {"type": "string", "description": "旁白文字稿（原样合成，不改写）。"},
                "voice_id": {"type": "string",
                             "description": "可选，缺省=温润男声 Chinese (Mandarin)_Gentleman。"},
                "speed": {"type": "number", "description": "可选，缺省 0.9（沉稳叙事）。"},
            },
            "required": ["cue_id", "text"],
        },
    },
}

AUDIO_PLAN_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.audio_plan_read",
        "description": ("读取某 sequence 的 AudioPlan（声音侧 canonical）：layers（角色/"
                        "源/时间/增益/淡入出）、base_gain_db、ducking、loudness、OCC "
                        "revision。只读工具。"),
        "parameters": {
            "type": "object",
            "properties": {"sequence_id": {"type": "string"}},
            "required": ["sequence_id"],
        },
    },
}

AUDIO_PLAN_PUT_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.audio_plan_put",
        "description": ("整体写入 AudioPlan（旁白/音乐层的 canonical，OCC）。layers 每 "
                        "层 = {role: narration|music|ambience, source:{kind:"
                        "imported_audio, file}, start_ms, duration_ms, gain_db, "
                        "fade_in_ms, fade_out_ms}；plan_config = {base_gain_db, "
                        "ducking:{threshold_db,ratio,attack_ms,release_ms}|null, "
                        "loudness:{i,tp,lra}|null}。🔴 plan_config 是【整体替换】"
                        "语义：只改响度时也必须把现有 base_gain_db 一并写回"
                        "（先 audio_plan_read 读当前值），漏写会被重置为 0。"
                        "时间窗校验在渲染前 fail-closed。"
                        "写工具（必须带 expected_revision）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "sequence_id": {"type": "string"},
                "layers": {"type": "array", "items": {"type": "object"}},
                "plan_config": {"type": "object"},
                "expected_revision": {"type": "string",
                                      "description": "audio_plan_read 返回的 revision；"
                                                     "首次写入可省略。"},
            },
            "required": ["sequence_id", "layers"],
        },
    },
}

STEM_RENDER_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.stem_render",
        "description": ("导出分轨（narration / music 独立 WAV，provenance=当前 plan "
                        "revision）。写工具（产物文件）。"),
        "parameters": {
            "type": "object",
            "properties": {
                "sequence_id": {"type": "string"},
                "roles": {"type": "array", "items": {"type": "string",
                                                     "enum": ["narration", "music",
                                                              "ambience"]}},
            },
            "required": ["sequence_id", "roles"],
        },
    },
}

# Phase 45.1：音频资产库发现（只读）——配乐/环境声选材前的正式入口。
AUDIO_ASSETS_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "assembly.audio_assets_read",
        "description": ("枚举项目音频资产库（output/audio_imports/）：文件路径、"
                        "ffprobe 实测时长/规格、sha256 前 12 位、身份档（origin/license，"
                        "若有登记）以及各 sequence AudioPlan 对它的现有引用。"
                        "选配乐/环境声素材前必读；身份档 origin 为 locally_* 的都是"
                        "本地测试素材，正式授权音乐需用户提供后替换。只读。"),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

ASSEMBLY_TOOLS = [
    BRIEFING_TOOL, SNAPSHOT_FREEZE_TOOL, SNAPSHOT_READ_TOOL, SEQUENCE_READ_TOOL, MEDIA_PROBE_TOOL,
    TIMELINE_PREVIEW_TOOL, RENDER_STATUS_TOOL, SEQUENCE_CREATE_TOOL,
    SEQUENCE_UPDATE_TOOL, RENDER_SUBMIT_TOOL, MANIFEST_PUBLISH_TOOL,
    PROPOSAL_WRITE_TOOL,
    NARRATION_READ_TOOL, TTS_SYNTHESIZE_TOOL, AUDIO_PLAN_READ_TOOL,
    AUDIO_PLAN_PUT_TOOL, STEM_RENDER_TOOL,
    AUDIO_ASSETS_READ_TOOL,
]


def _tool_surface() -> list:
    """Assembly Agent 实际下发给 LLM 的工具面：本模块 assembly.* 工具 +
    复用的只读 schema（shot.list 供对照镜头语义）。"""
    tools: list = list(ASSEMBLY_TOOLS)
    try:
        from .director import DIRECTOR_TOOLS
        tools += [t for t in DIRECTOR_TOOLS if t["function"]["name"] == "shot.list"]
    except Exception:   # noqa: BLE001 - 防御：partial-import 降级
        pass
    return tools


# ============ 执行器 ============

def _svc(pdir, project: str):
    from src.sequence.service import SequenceService
    return SequenceService(pdir, project)


def _audit(rt: AgentRuntime, project: str) -> DirectEditAuditStore:
    return DirectEditAuditStore(rt._resolve_project_dir(project), project)


def _snap_check(snap: dict, expected_snapshot_id: Optional[str]) -> None:
    if expected_snapshot_id and expected_snapshot_id != snap.get("snapshot_id"):
        raise AgentError(
            "snapshot_mismatch",
            f"expected_snapshot_id={expected_snapshot_id} 与实际 "
            f"{snap.get('snapshot_id')} 不符（快照已换版？先 snapshot_read 确认）")


def _align_block_to_snapshot(rt: AgentRuntime, project: str, snap: dict,
                             clips: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """冻结快照对齐核验（创建/更新 sequence 前）：逐镜 live final==pin 且文件
    sha256==登记哈希。任何不符 → AgentError（带 shot_id 定位），不偷换素材。"""
    from src.sequence.basis import resolve_clip_source
    from src.resources.generation import sha256_bytes
    pdir = rt._resolve_project_dir(project)
    proj = ProjectStore(pdir).load()
    snap_idx = {s.get("shot_id"): s for s in (snap.get("shots") or [])}
    problems = []
    for c in clips:
        sid = c.get("shot_id")
        s = snap_idx.get(sid)
        if s is None:
            problems.append(f"{sid}: 不在冻结快照中")
            continue
        try:
            _shot, version = resolve_clip_source(proj, SequenceClipShim(sid))
        except Exception as e:   # noqa: BLE001
            problems.append(f"{sid}: 源解析失败 {e}")
            continue
        if version.version_id != s.get("video_version_id"):
            problems.append(f"{sid}: live final={version.version_id} ≠ 快照 pin="
                            f"{s.get('video_version_id')}")
            continue
        try:
            actual = sha256_bytes(Path(str(version.local or "")).read_bytes())
        except OSError as e:
            problems.append(f"{sid}: 源文件不可读 {version.local}: {e}")
            continue
        if actual != s.get("media_sha256"):
            problems.append(f"{sid}: 源 sha256 与快照登记不符（媒体被替换/损坏）")
    if problems:
        raise AgentError("snapshot_alignment_failed",
                         "快照对齐核验失败（不偷换素材）: " + "；".join(problems))
    return clips


class SequenceClipShim:
    """resolve_clip_source 只读 clip.shot_id——这里只需要 shot_id 的轻量替身。"""

    def __init__(self, shot_id: str):
        self.shot_id = shot_id


def _resolve_preview(rt: AgentRuntime, project: str, snap: dict,
                     clips: List[Dict[str, Any]]) -> dict:
    """timeline_preview 核心路径：解析→探测→校验→时间线（零 canonical 写）。
    返回 {ok, items, total_duration_ms, violations}；单镜失败进 violations 不中断。"""
    from src.sequence.basis import resolve_clip_source
    from src.sequence.media_ops import probe_video
    from src.sequence.timeline import validate_trims_vs_source
    pdir = rt._resolve_project_dir(project)
    proj = ProjectStore(pdir).load()
    snap_idx = {s.get("shot_id"): s for s in (snap.get("shots") or [])}
    items: List[dict] = []
    violations: List[dict] = []
    cursor = 0
    prev_scene = None
    for i, c in enumerate(clips):
        sid = c.get("shot_id")
        rec: dict = {"index": i, "shot_id": sid}
        s = snap_idx.get(sid)
        if s is None:
            violations.append({"shot_id": sid, "code": "shot_not_in_snapshot",
                               "message": "不在冻结快照中（不偷换素材）"})
            items.append(rec)
            continue
        try:
            _shot, version = resolve_clip_source(proj, SequenceClipShim(sid))
            probe = probe_video(str(version.local or ""))
            src_dur = int(probe["duration_ms"])
            trim_in = int(c.get("trim_in_ms") or 0)
            trim_out = int(c.get("trim_out_ms") or 0)
            effective = validate_trims_vs_source(src_dur, trim_in, trim_out,
                                                 shot_id=sid)
            pin_ok = version.version_id == s.get("video_version_id")
            scene = str(s.get("scene_id") or "")
            rec.update({
                "scene_id": scene,
                "scene_boundary": bool(prev_scene is not None and scene != prev_scene),
                "video_version_id": version.version_id,
                "pin_match": pin_ok,
                "source_duration_ms": src_dur,
                "source_in_ms": trim_in,
                "source_out_ms": src_dur - trim_out,
                "effective_ms": effective,
                "timeline_start_ms": cursor,
                "timeline_end_ms": cursor + effective,
                "width": probe.get("width"), "height": probe.get("height"),
                "has_audio": probe.get("has_audio"),
            })
            if not pin_ok:
                violations.append({"shot_id": sid, "code": "version_mismatch",
                                   "message": f"live {version.version_id} ≠ 快照 pin "
                                              f"{s.get('video_version_id')}"})
            cursor += effective
            prev_scene = scene
        except Exception as e:   # noqa: BLE001 - 逐镜隔离，聚合报告
            violations.append({"shot_id": sid, "code": getattr(e, "code", "preview_failed"),
                               "message": str(getattr(e, "message", e))[:200]})
        items.append(rec)
    return {"items": items, "total_duration_ms": cursor,
            "violations": violations,
            "seam_times_ms": [it["timeline_start_ms"] for it in items
                              if it.get("timeline_start_ms") is not None][1:]}


def _execute_briefing(rt: AgentRuntime, project: str, arguments: str,
                      instruction, message_id) -> dict:
    pdir = rt._resolve_project_dir(project)
    proj = ProjectStore(pdir).load()
    try:
        snap = _snapshot_load(rt, project, None)
        snap_sum = _snapshot_summary(snap)
        rec_profile = _recommended_profile(rt, project, snap)
    except AgentError as e:
        snap_sum = {"error": e.message}
        rec_profile = None
    return {"ok": True,
            "project": {"name": proj.name,
                        "scenes": len(proj.scenes or []),
                        "shots": sum(len(sc.shots or []) for sc in (proj.scenes or []))},
            "snapshot": snap_sum,
            "recommended_render_profile": rec_profile,
            "sequences": _sequences_brief(rt, project),
            "assembly_manifest": _manifest_brief(rt, project)}


def _execute_snapshot_read(rt: AgentRuntime, project: str, arguments: str,
                           instruction, message_id) -> dict:
    args = _parse_args(arguments)
    snap = _snapshot_load(rt, project, args.get("relpath"))
    sid = args.get("shot_id")
    if sid:
        for s in snap.get("shots") or []:
            if s.get("shot_id") == sid:
                return {"ok": True, "snapshot_id": snap.get("snapshot_id"),
                        "mode": snap.get("mode"), "shot": s}
        return {"ok": False, "error": f"快照中无该镜头: {sid}"}
    return {"ok": True, **_snapshot_summary(snap),
            "seams_at_snapshot": snap.get("seams_at_snapshot") or []}


def _execute_snapshot_freeze(rt: AgentRuntime, project: str, arguments: str,
                             instruction, message_id) -> dict:
    """Phase 47：为本项目冻结正式装配快照（live final_video + sha256）。
    只记录真实状态（含无 final 的镜头如实标 frozen=false）；绝不改指针、不生成媒体。
    与 44A manifest 同构字段（shot_id/video_version_id/media_sha256），对齐门直接可用。"""
    import hashlib
    import time as _time
    args = _parse_args(arguments)
    want_ids = args.get("shot_ids") or None
    pdir = rt._resolve_project_dir(project)
    proj = ProjectStore(pdir).load()
    shots_out = []
    skipped = []
    for sc in (proj.scenes or []):
        for sh in (sc.shots or []):
            if want_ids and sh.shot_id not in set(want_ids):
                continue
            vid = sh.final_video
            vv = next((v for v in (sh.video_versions or [])
                       if v.version_id == vid), None) if vid else None
            if vv is None or not vv.local:
                skipped.append({"shot_id": sh.shot_id,
                                "reason": "无完成 final_video 版本或本地文件"})
                continue
            f = Path(str(vv.local))
            if not f.is_absolute():
                cand = [pdir / f, pdir.parent.parent / f]
                f = next((c for c in cand if c.exists()), cand[0])
            if not f.exists():
                skipped.append({"shot_id": sh.shot_id, "reason": f"文件缺失 {f.name}"})
                continue
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            probe = {}
            try:
                from src.sequence.media_ops import probe_video
                probe = probe_video(str(f))
            except Exception as e:   # noqa: BLE001 - 探测失败不阻塞冻结（诚实记录）
                probe = {"error": str(e)[:120]}
            shots_out.append({
                "shot_id": sh.shot_id,
                "scene_id": sh.scene_id,
                "video_version_id": vv.version_id,
                "approved_status": "frozen_from_live",
                "local": str(f),
                "media_uri": str(f),
                "media_sha256": digest,
                "duration_probe": {"duration_ms": probe.get("duration_ms"),
                                   "width": probe.get("width"),
                                   "height": probe.get("height")},
            })
    if not shots_out:
        return {"ok": False,
                "error": "无可冻结镜头（没有任何带本地文件的 final_video 版本）",
                "skipped": skipped}
    out_dir = pdir / "output" / "assembly"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "snapshot_id": f"snap_{project}_{_time.strftime('%Y%m%d_%H%M%S')}",
        "mode": "formal",
        "project": project,
        "created_at": _time.time(),
        "frozen_by": "assembly.snapshot_freeze",
        "note": (args.get("note") or "")[:200],
        "shots": shots_out,
    }
    out = out_dir / "snapshot_formal.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(out)
    return {"ok": True, "snapshot_id": manifest["snapshot_id"],
            "path": str(out), "frozen_shots": [s["shot_id"] for s in shots_out],
            "skipped": skipped}


def _execute_sequence_read(rt: AgentRuntime, project: str, arguments: str,
                           instruction, message_id) -> dict:
    args = _parse_args(arguments)
    pdir = rt._resolve_project_dir(project)
    svc = _svc(pdir, project)
    sid = args.get("sequence_id")
    if not sid:
        return {"ok": True, "sequences": _sequences_brief(rt, project)}
    entry = svc.get_entry(sid)
    fr = svc.freshness(sid)
    d = entry.block.to_dict()
    d["revision"] = entry.block.revision
    d["render_profile_is_override"] = entry.block.render_profile is not None
    d["audio_strategy"] = str(entry.block.effective_render_profile().get("audio", "native"))
    d["render_versions"] = [r.to_dict() for r in entry.render_versions]
    d["final_render"] = entry.final_render
    d["freshness"] = fr
    return {"ok": True, "sequence": d}


def _execute_media_probe(rt: AgentRuntime, project: str, arguments: str,
                         instruction, message_id) -> dict:
    from src.sequence.media_ops import probe_video
    args = _parse_args(arguments)
    pdir = rt._resolve_project_dir(project)
    if args.get("manifest"):
        snap = _snapshot_load(rt, project, args.get("relpath"))
        out = []
        for s in snap.get("shots") or []:
            sm = s.get("snapshot_media") or s.get("media_uri") or ""
            f = Path(sm)
            if not f.is_absolute():
                cand = [pdir.parent.parent / sm, pdir / sm]
                f = next((c for c in cand if c.exists()), cand[0])
            try:
                pr = probe_video(str(f))
                out.append({"shot_id": s.get("shot_id"), "file": f.name, **pr})
            except Exception as e:   # noqa: BLE001
                out.append({"shot_id": s.get("shot_id"), "file": f.name,
                            "error": str(e)[:160]})
        dims = [(o.get("width"), o.get("height")) for o in out if o.get("width")]
        return {"ok": True, "probes": out,
                "dim_distribution": {f"{w}x{h}": dims.count((w, h))
                                     for w, h in set(dims)}}
    path = str(args.get("path") or "")
    if not path:
        return {"ok": False, "error": "需要 path 或 manifest=true"}
    p = Path(path)
    if not p.is_absolute():
        p = pdir.parent.parent / path
    pr = probe_video(str(p))
    return {"ok": True, "path": str(p), **pr}


def _execute_timeline_preview(rt: AgentRuntime, project: str, arguments: str,
                              instruction, message_id) -> dict:
    args = _parse_args(arguments)
    clips = args.get("clips")
    if not isinstance(clips, list) or not clips:
        return {"ok": False, "error": "clips 必须是非空数组"}
    snap = _snapshot_load(rt, project, args.get("relpath"))
    result = _resolve_preview(rt, project, snap, clips)
    return {"ok": not result["violations"], **result,
            "snapshot_id": snap.get("snapshot_id")}


def _execute_render_status(rt: AgentRuntime, project: str, arguments: str,
                           instruction, message_id) -> dict:
    from src.sequence.media_ops import audit_output_seams, probe_video
    args = _parse_args(arguments)
    pdir = rt._resolve_project_dir(project)
    svc = _svc(pdir, project)
    sid = args.get("sequence_id")
    if not sid:
        return {"ok": True, "sequences": _sequences_brief(rt, project)}
    entry = svc.get_entry(sid)
    vid = args.get("version_id")
    versions = [r for r in entry.render_versions if r.version_id == vid] if vid \
        else list(entry.render_versions)
    rows = [{"version_id": r.version_id, "status": r.status,
             "sequence_revision": r.sequence_revision,
             "planned_duration_ms": r.planned_duration_ms,
             "duration_ms": r.duration_ms,
             "output_file": r.output_file,
             "error": (r.error or "")[:200],
             "base_render_version_id": r.base_render_version_id,
             "audio_peak_warning": r.audio_peak_warning}
            for r in versions]
    out: dict = {"ok": True, "sequence_id": sid, "final_render": entry.final_render,
                 "render_versions": rows}
    target = (next((r for r in versions if r.version_id == vid), None) if vid
              else (next((r for r in reversed(entry.render_versions)
                          if r.status == "done"), None)))
    if args.get("deep_audit"):
        if target is None or target.status != "done" or not target.output_file \
                or not Path(target.output_file).exists():
            out["deep_audit"] = {"skipped": "无 done 输出可审计（或输出文件缺失）"}
        else:
            probe = probe_video(target.output_file)
            # 接缝时间：优先取已发布 manifest（该 sequence 的最新一版）；
            # 未发布 manifest 时退化为全片审计（黑帧/PTS/重复帧，无切点窗）。
            seam_times: List[int] = []
            try:
                from src.sequence.assembly_manifest import load_assembly_manifest
                doc = load_assembly_manifest(pdir)
                if doc.get("sequence_id") == sid:
                    seam_times = [it["timeline_end_ms"] for it in
                                  doc["shot_items"][:-1]]
            except Exception:   # noqa: BLE001 - 无 manifest 属正常路径
                pass
            audit = audit_output_seams(target.output_file, seam_times_ms=seam_times)
            out["deep_audit"] = {"probe": probe,
                                 "seam_source": "manifest" if seam_times
                                 else "whole_file_only",
                                 **audit}            # Phase 45.1：逐层音频落窗审计（44C §9.3 教训——"仅首句通过"掩盖整片缺失）。
            # plan 存在且目标为 done 混音渲染时必跑；无 plan（纯 S1 native/silent）跳过。
            try:
                plan = svc.get_audio_plan(sid)
            except Exception:   # noqa: BLE001
                plan = None
            if plan is not None and plan.layers:
                try:
                    from src.sequence.audio_audit import audit_audio_render
                    out["deep_audit"]["audio"] = audit_audio_render(
                        mix_file=str(target.output_file),
                        layers=[{"layer_id": l.layer_id, "role": l.role,
                                 "source_file": l.source.file or "",
                                 "start_ms": l.start_ms,
                                 "duration_ms": l.duration_ms,
                                 "source_in_ms": l.source_in_ms,
                                 "gain_db": l.gain_db,
                                 "fade_in_ms": l.fade_in_ms,
                                 "fade_out_ms": l.fade_out_ms}
                                for l in plan.layers],
                        project_output_dir=pdir / "output",
                        base_gain_db=plan.base_gain_db,
                        render_version_id=target.version_id,
                        shot_boundaries_ms=seam_times)
                except Exception as e:   # noqa: BLE001 - 审计自身失败如实透出
                    out["deep_audit"]["audio"] = {"error": f"{type(e).__name__}: {e}",
                                                  "passed": False}
    return out


def _execute_sequence_create(rt: AgentRuntime, project: str, arguments: str,
                             instruction, message_id) -> dict:
    args = _parse_args(arguments)
    clips = args.get("clips")
    if not isinstance(clips, list) or not clips:
        return {"ok": False, "error": "clips 必须是非空数组"}
    pdir = rt._resolve_project_dir(project)
    try:
        snap = _snapshot_load(rt, project, None)
        _snap_check(snap, args.get("expected_snapshot_id"))
        _align_block_to_snapshot(rt, project, snap, clips)
    except AgentError as e:
        return _base_receipt("assembly.sequence_create", ok=False,
                             target=None, error=e.message, code=e.code)
    svc = _svc(pdir, project)
    rp = args.get("render_profile")
    block = svc.create_sequence(
        title=str(args.get("title") or ""), clips=clips,
        render_profile=(rp if isinstance(rp, dict) and rp else None))
    entry = svc.get_entry(block.sequence_id)     # 写后重读 canonical 验证
    verified = entry.block.revision == block.revision
    if verified:
        _audit(rt, project).record(
            agent_id="assembly", field=f"sequences.{block.sequence_id}",
            before=None, after={"clips": clips,
                                "render_profile": entry.block.render_profile,
                                "snapshot_id": snap.get("snapshot_id")},
            instruction=instruction, message_id=message_id)
    return _base_receipt(
        "assembly.sequence_create", ok=True, target=block.sequence_id,
        before=None, after={"clips": clips, "title": block.title,
                            "render_profile": entry.block.render_profile},
        revision=block.revision, verified=verified,
        sequence_id=block.sequence_id, clip_count=len(clips),
        snapshot_id=snap.get("snapshot_id"),
        op_summary=(f"创建 {block.sequence_id}（{len(clips)} clips，"
                    f"revision={block.revision}）"))


def _execute_sequence_update(rt: AgentRuntime, project: str, arguments: str,
                             instruction, message_id) -> dict:
    args = _parse_args(arguments)
    sid = args.get("sequence_id")
    if not sid:
        return {"ok": False, "error": "sequence_id 必填"}
    if not args.get("expected_revision"):
        return {"ok": False, "error": "expected_revision 必填（OCC）"}
    pdir = rt._resolve_project_dir(project)
    svc = _svc(pdir, project)
    before_block = svc.get_entry(sid).block
    before = {"clips": [{"shot_id": c.shot_id, "trim_in_ms": c.trim_in_ms,
                         "trim_out_ms": c.trim_out_ms} for c in before_block.clips],
              "revision": before_block.revision}
    clips = args.get("clips")
    try:
        snap = _snapshot_load(rt, project, None)
        if isinstance(clips, list) and clips:
            _align_block_to_snapshot(rt, project, snap, clips)
    except AgentError as e:
        return _base_receipt("assembly.sequence_update", ok=False, target=sid,
                             error=e.message, code=e.code,
                             current_revision=before_block.revision)
    rp = args.get("render_profile")
    try:
        block = svc.update_sequence(
            sid, title=args.get("title"),
            clips=([{"shot_id": c.get("shot_id"),
                     "trim_in_ms": int(c.get("trim_in_ms") or 0),
                     "trim_out_ms": int(c.get("trim_out_ms") or 0)}
                    for c in clips]
                   if isinstance(clips, list) else None),
            render_profile=(rp if isinstance(rp, dict) else None),
            expected_revision=args.get("expected_revision"))
    except SequenceError as e:
        # OCC 冲突/结构校验失败 → 结构化回执（loop 与直调同口径；附当前 revision
        # 供模型直接重试）
        cur = None
        try:
            cur = svc.get_entry(sid).block.revision
        except Exception:   # noqa: BLE001
            pass
        return _base_receipt("assembly.sequence_update", ok=False, target=sid,
                             error=e.message, code=e.code,
                             current_revision=cur)
    entry = svc.get_entry(sid)                   # 写后重读 canonical 验证
    verified = entry.block.revision == block.revision
    if verified:
        _audit(rt, project).record(
            agent_id="assembly", field=f"sequences.{sid}",
            before=before, after={"clips": [{"shot_id": c.shot_id,
                                             "trim_in_ms": c.trim_in_ms,
                                             "trim_out_ms": c.trim_out_ms}
                                            for c in entry.block.clips],
                                  "revision": block.revision},
            instruction=instruction, message_id=message_id)
    return _base_receipt(
        "assembly.sequence_update", ok=True, target=sid,
        before=before, after={"clips": [{"shot_id": c.shot_id,
                                         "trim_in_ms": c.trim_in_ms,
                                         "trim_out_ms": c.trim_out_ms}
                                        for c in entry.block.clips]},
        revision=block.revision, verified=verified,
        sequence_id=sid,
        op_summary=(f"更新 {sid} → revision={block.revision}"))


def _execute_render_submit(rt: AgentRuntime, project: str, arguments: str,
                           instruction, message_id) -> dict:
    args = _parse_args(arguments)
    sid = args.get("sequence_id")
    if not sid:
        return {"ok": False, "error": "sequence_id 必填"}
    pdir = rt._resolve_project_dir(project)
    svc = _svc(pdir, project)
    rv = svc.create_render(sid)
    queue = rt._resolve_job_queue_or_none()
    if queue is None:
        svc._mark_failed(sid, rv.version_id, "dispatch_failed",
                         "JobQueue 不可用（not_configured）")
        return _base_receipt("assembly.render_submit", ok=False, target=sid,
                             error="JobQueue 不可用（not_configured）",
                             code="not_configured",
                             render_version_id=rv.version_id)
    try:
        job = queue.submit(project, "sequence_render", None,
                           sequence_id=sid, render_version_id=rv.version_id)
    except Exception as e:
        svc._mark_failed(sid, rv.version_id, "dispatch_failed", str(e)[:200])
        return _base_receipt("assembly.render_submit", ok=False, target=sid,
                             error=f"JobQueue 入队失败: {e}",
                             render_version_id=rv.version_id)
    entry = svc.get_entry(sid)                   # 写后重读 canonical 验证
    row = entry.get_render(rv.version_id)
    verified = row is not None and row.status in ("queued", "rendering", "done")
    return _base_receipt(
        "assembly.render_submit", ok=True, target=sid,
        revision=entry.block.revision, verified=verified,
        sequence_id=sid, render_version_id=rv.version_id,
        job_id=job.job_id, job_state=job.state,
        op_summary=(f"渲染已受理 {rv.version_id}（job={job.job_id}，"
                    f"sequence={sid}）——完成状态以 render_status 回执为准"))


def _execute_manifest_publish(rt: AgentRuntime, project: str, arguments: str,
                              instruction, message_id) -> dict:
    args = _parse_args(arguments)
    sid = args.get("sequence_id")
    if not sid:
        return {"ok": False, "error": "sequence_id 必填"}
    pdir = rt._resolve_project_dir(project)
    snap = _snapshot_load(rt, project, None)
    proj = ProjectStore(pdir).load()
    svc = _svc(pdir, project)
    entry = svc.get_entry(sid)
    done = [r for r in entry.render_versions if r.status == "done"]
    if not done:
        return {"ok": False,
                "error": "该 sequence 尚无 done 渲染——先 render_submit 并等完成，"
                         "再发布 manifest（不得发布未渲染的时间线）"}
    from src.sequence.assembly_manifest import (
        build_assembly_manifest, derive_audio_projection, load_assembly_index,
        publish_assembly_manifest)
    index = load_assembly_index(pdir)
    # Phase 45.1：声音段由 canonical 投影（plan 缺省=纯 S1 语义不变）。
    # Agent 显式传 narration_cues 仅作覆盖（可加备注），music_bed 恒取投影。
    audio_projection = None
    try:
        plan = svc.get_audio_plan(sid)
    except Exception:   # noqa: BLE001
        plan = None
    if plan is not None and plan.layers:
        # 🔴 45.5 真机教训：plan 改了但最新 done 渲染还是旧 plan 的混音时，
        # manifest 会把「当前 plan 的 cues」与「旧 plan 的成片」绑成假谱系。
        # fail closed：要求先渲染出与当前 plan 一致的版本再发布。
        latest_done = next((r for r in reversed(entry.render_versions)
                            if r.status == "done"), None)
        if latest_done is not None:
            fr = svc.freshness(sid, version_id=latest_done.version_id)
            if fr.get("status") != "current":
                return {"ok": False, "error":
                        ("最新 done 渲染与当前 AudioPlan/Sequence 谱系无法证明一致"
                         f"（{latest_done.version_id} freshness={fr.get('status')}，"
                         f"detail={json.dumps(fr.get('detail') or {}, ensure_ascii=False)[:160]}）——"
                         "manifest 的 narration_cues/music_bed 投影自当前 plan，"
                         "发布会造成假谱系；请先 render_submit 渲染当前 plan 再发布。"),
                        "code": "render_stale_vs_plan",
                        "render_version_id": latest_done.version_id,
                        "freshness": fr.get("status"),
                        "sequence_id": sid}
        from src.audio.narration import NarrationStore
        audio_projection = derive_audio_projection(
            plan, NarrationStore(pdir).load())
    agent_cues = args.get("narration_cues") or []
    narration_cues = agent_cues if agent_cues else \
        (audio_projection or {}).get("narration_cues") or []
    doc = build_assembly_manifest(
        proj, snap, entry.block, assembly_revision=int(index.get("latest_revision") or 0) + 1,
        snapshot_path=snap.get("_resolved_path"),
        narration_cues=narration_cues,
        music_bed=((audio_projection or {}).get("layers")
                   and {k: v for k, v in audio_projection.items()
                        if k != "narration_cues"}) or None)
    published = publish_assembly_manifest(pdir, doc)
    _audit(rt, project).record(
        agent_id="assembly", field=f"assembly_manifest.v{doc['assembly_revision']}",
        before=None, after={"sequence_id": sid,
                            "sequence_revision": doc["sequence_revision"],
                            "doc_fingerprint": doc["doc_fingerprint"],
                            "total_duration_ms": doc["total_duration_ms"]},
        instruction=instruction, message_id=message_id)
    return _base_receipt(
        "assembly.manifest_publish", ok=True, target=sid,
        revision=doc["sequence_revision"], verified=True,
        sequence_id=sid, assembly_revision=doc["assembly_revision"],
        manifest_file=published["file"],
        total_duration_ms=doc["total_duration_ms"],
        seam_count=doc["seam_count"],
        audio_strategy=doc["audio_strategy"],
        audio_plan_revision=(doc.get("music_bed") or {}).get("audio_plan_revision"),
        narration_cue_count=len(doc.get("narration_cues") or []),
        music_layer_count=len((doc.get("music_bed") or {}).get("layers") or []),
        narration_cue_source=("agent" if agent_cues else
                              ("audio_plan_projection" if narration_cues
                               else "none")),
        snapshot_id=doc["snapshot_id"],
        op_summary=(f"发布 manifest_v{doc['assembly_revision']}（{len(doc['shot_items'])} 镜，"
                    f"片长 {doc['total_duration_ms']}ms，音轨策略 "
                    f"{doc['audio_strategy']}，旁白 {len(doc.get('narration_cues') or [])} 句，"
                    f"音乐层 {len((doc.get('music_bed') or {}).get('layers') or [])}）"))


def _execute_proposal_write(rt: AgentRuntime, project: str, arguments: str,
                            instruction, message_id) -> dict:
    args = _parse_args(arguments)
    title = str(args.get("title") or "").strip()
    content = str(args.get("content_md") or "").strip()
    if not title or not content:
        # 45.1 真机会话教训：LLM 传错键名时需要看到"收到了什么"才能自纠
        return {"ok": False,
                "error": ("title 与 content_md 必填（收到键: "
                          f"{sorted(k for k in args.keys()) or '无'}；"
                          '参数必须是 JSON 对象，如 {"title": "...", "content_md": "markdown 正文"}）')}
    pdir = rt._resolve_project_dir(project)
    root = pdir / "output" / "assembly"
    root.mkdir(parents=True, exist_ok=True)
    existing = sorted(root.glob("proposal_v*.md"))
    # 🔴 44C 修复：编号取最大 N（文件数会因历史文件缺失/清理而撞号覆盖旧提案）
    ns = [int(m.group(1)) for f in existing
          if (m := re.search(r"proposal_v(\d+)\.md", f.name))]
    n = max(ns or [0]) + 1
    file = root / f"proposal_v{n}.md"
    header = (f"# 剪辑提案 v{n}｜{title}\n\n"
              f"- 项目：{project}\n- 提出者：Assembly Agent\n"
              f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
              f"- 性质：提案（非 canonical；顺序/trim 变更需用户确认后才经 "
              f"assembly.sequence_update 落 canonical）\n\n")
    file.write_text(header + content + "\n", encoding="utf-8")
    _audit(rt, project).record(
        agent_id="assembly", field=f"assembly.proposal_v{n}",
        before=None, after={"title": title, "file": str(file)},
        instruction=instruction, message_id=message_id)
    return _base_receipt(
        "assembly.proposal_write", ok=True, target=str(file),
        before=None, after={"title": title}, verified=True,
        proposal_file=str(file), proposal_version=n,
        op_summary=f"提案 v{n} 已写入（{title}）——等待用户评审")


# ===== splice: 44C executors for src/agents/assembly.py =====

# ============ Phase 44C 执行器：旁白 / AudioPlan / 分轨 ============

def _execute_narration_read(rt: AgentRuntime, project: str, arguments: str,
                            instruction, message_id) -> dict:
    """旁白编排入口：故事文本 + 时间线窗口 + 已有旁白版本 + 当前 plan。只读。
    Phase 45.1：可选 sequence_id——窗口从该 sequence 的 canonical block + 快照实测
    复算（Agent 自建 sequence 后窗口应来自其 canonical，而非旧 manifest）。"""
    from src.audio.narration import NarrationStore
    args = _parse_args(arguments)
    pdir = rt._resolve_project_dir(project)
    proj = ProjectStore(pdir).load()
    scene_summaries = [{"scene_id": sc.scene_id, "summary": (sc.summary or "")[:200]}
                       for sc in (proj.scenes or [])]
    windows = []
    window_source = None
    manifest_info: Dict[str, Any] | None = None
    sid = str(args.get("sequence_id") or "").strip()
    if sid:
        try:
            windows = _sequence_timeline_windows(rt, pdir, project, sid)
            window_source = f"sequence_canonical:{sid}"
        except Exception as e:   # noqa: BLE001
            return {"ok": False, "error": f"sequence {sid} 窗口复算失败: "
                                          f"{type(e).__name__}: {str(e)[:160]}"}
    if not windows:
        try:
            from src.sequence.assembly_manifest import load_assembly_manifest
            doc = load_assembly_manifest(pdir)
            windows = [{"shot_id": it["shot_id"], "scene_id": it["scene_id"],
                        "start_ms": it["timeline_start_ms"],
                        "end_ms": it["timeline_end_ms"],
                        "scene_boundary": it["scene_boundary"]}
                       for it in doc["shot_items"]]
            window_source = f"latest_manifest_v{doc['assembly_revision']}"
            manifest_info = {"revision": doc["assembly_revision"],
                             "sequence_id": doc["sequence_id"],
                             "total_duration_ms": doc["total_duration_ms"]}
        except Exception as e:   # noqa: BLE001
            manifest_info = {"error": str(e)[:120]}
            windows = [{"shot_id": s.shot_id, "scene_id": s.scene_id,
                        "start_ms": None, "end_ms": None, "scene_boundary": None}
                       for s in proj.all_shots()]
    if manifest_info is None:
        manifest_info = {"note": "窗口来自 sequence canonical（未读 manifest）"}
    store = NarrationStore(pdir)
    cues = {}
    for cue_id, entry in store.load().items():
        vs = entry.get("versions") or []
        cues[cue_id] = [{"version_id": v.get("version_id"),
                         "text": v.get("text"),
                         "voice_id": v.get("voice_id"), "speed": v.get("speed"),
                         "file": v.get("audio_import_path"),
                         "duration_s": (v.get("duration_probe") or {}).get("duration_s"),
                         "sha256": (v.get("audio_sha256") or "")[:12] + "…",
                         "trace_id": v.get("provider_trace_id")}
                        for v in vs]
    return {"ok": True, "story_text": (proj.story_text or "")[:3000],
            "scenes": scene_summaries, "timeline_windows": windows,
            "window_source": window_source,
            "manifest": manifest_info, "narration_versions": cues}


def _sequence_timeline_windows(rt: AgentRuntime, pdir, project: str,
                               sequence_id: str) -> list:
    """从 canonical SequenceBlock + 冻结快照复算逐镜时间线窗（0ms=成片首帧）。
    trim 语义与 manifest 构建器一致（source_out = 实测时长 - trim_out）；此处只做
    窗口规划不重复哈希核验（核验属 sequence_create/render/manifest 的真相闸门）。"""
    from src.sequence.basis import resolve_clip_source
    from src.sequence.media_ops import probe_video
    snap = _snapshot_load(rt, project, None)
    svc = _svc(pdir, project)
    block = svc.get_entry(sequence_id).block
    out: list = []
    cursor = 0
    prev_scene: Optional[str] = None
    for clip in block.clips:
        _shot, version = resolve_clip_source(ProjectStore(pdir).load(), clip)
        src_dur = int(probe_video(str(version.local))["duration_ms"])
        src_in = clip.trim_in_ms
        src_out = src_dur - clip.trim_out_ms
        effective = src_out - src_in
        scene_id = str(getattr(_shot, "scene_id", "") or "")
        out.append({"shot_id": clip.shot_id, "scene_id": scene_id,
                    "start_ms": cursor, "end_ms": cursor + effective,
                    "scene_boundary": bool(prev_scene is not None
                                           and scene_id != prev_scene),
                    "video_version_id": version.version_id})
        cursor += effective
        prev_scene = scene_id
    return out


async def _execute_tts_synthesize(rt: AgentRuntime, project: str, arguments: str,
                                  instruction, message_id) -> dict:
    """合成/幂等复用一句旁白（MiniMax TTS；产物进 audio_imports/narration/）。"""
    args = _parse_args(arguments)
    cue_id = str(args.get("cue_id") or "").strip()
    text = str(args.get("text") or "").strip()
    if not cue_id or not text:
        return {"ok": False, "error": "cue_id 与 text 必填"}
    from src.audio.narration import synthesize_narration_cue
    from src.audio.minimax_adapter import VoiceProviderError
    pdir = rt._resolve_project_dir(project)
    try:
        r = await synthesize_narration_cue(
            pdir, cue_id=cue_id, text=text,
            voice_id=(str(args.get("voice_id")).strip()
                      if args.get("voice_id") else None),
            speed=(float(args["speed"]) if args.get("speed") is not None else None))
    except VoiceProviderError as e:
        return _base_receipt("assembly.tts_synthesize", ok=False, target=cue_id,
                             error=e.message, code=e.code,
                             detail={k: str(v)[:120] for k, v in (e.detail or {}).items()})
    v = r["version"]
    _audit(rt, project).record(
        agent_id="assembly", field=f"narration.{cue_id}.{v['version_id']}",
        before=None, after={"text": text, "voice_id": v["voice_id"],
                            "sha256": v["audio_sha256"][:16],
                            "trace_id": v["provider_trace_id"]},
        instruction=instruction, message_id=message_id)
    return _base_receipt(
        "assembly.tts_synthesize", ok=True, target=cue_id,
        before=None, after={"file": v["audio_import_path"],
                            "duration_s": (v.get("duration_probe") or {}).get("duration_s")},
        verified=True, cue_id=cue_id, version_id=v["version_id"],
        voice_id=v["voice_id"], speed=v["speed"], cache_hit=r["cache_hit"],
        audio_import_path=v["audio_import_path"],
        duration_probe=v.get("duration_probe"),
        provider_trace_id=v["provider_trace_id"],
        op_summary=(f"旁白 {cue_id} → {v['version_id']}"
                    f"（{'缓存复用' if r['cache_hit'] else '新合成'}，"
                    f"{(v.get('duration_probe') or {}).get('duration_s')}s，"
                    f"voice={v['voice_id']}）"))


def _execute_audio_plan_read(rt: AgentRuntime, project: str, arguments: str,
                             instruction, message_id) -> dict:
    args = _parse_args(arguments)
    sid = args.get("sequence_id")
    if not sid:
        return {"ok": False, "error": "sequence_id 必填"}
    pdir = rt._resolve_project_dir(project)
    svc = _svc(pdir, project)
    plan = svc.get_audio_plan(sid)
    if plan is None:
        return {"ok": True, "sequence_id": sid, "audio_plan": None,
                "revision": None,
                "hint": "当前无 AudioPlan（首次写入可省略 expected_revision）"}
    return {"ok": True, "sequence_id": sid, "audio_plan": plan.to_dict(),
            "revision": plan.revision}


def _execute_audio_plan_put(rt: AgentRuntime, project: str, arguments: str,
                            instruction, message_id) -> dict:
    """整体写入 AudioPlan（OCC；plan_config=混音配置整体替换）。"""
    args = _parse_args(arguments)
    sid = args.get("sequence_id")
    layers = args.get("layers")
    if not sid or not isinstance(layers, list) or not layers:
        return {"ok": False, "error": "sequence_id 与非空 layers 必填"}
    pdir = rt._resolve_project_dir(project)
    svc = _svc(pdir, project)
    before = svc.get_audio_plan(sid)
    before_rev = before.revision if before else None
    if before is not None:
        if not args.get("expected_revision"):
            return {"ok": False,
                    "error": f"AudioPlan 已存在（revision={before_rev}）——"
                             "必须带 expected_revision（OCC）；先 audio_plan_read"}
        expected = args.get("expected_revision")
    else:
        expected = None
    try:
        plan = svc.put_audio_plan(
            sid, layers, expected_revision=expected,
            plan_config=(args.get("plan_config")
                         if isinstance(args.get("plan_config"), dict) else None))
    except SequenceError as e:
        cur = svc.get_audio_plan(sid)
        return _base_receipt("assembly.audio_plan_put", ok=False, target=sid,
                             error=e.message, code=e.code,
                             current_revision=(cur.revision if cur else None))
    if plan is None:
        return _base_receipt("assembly.audio_plan_put", ok=False, target=sid,
                             error="layers 为空被归一化为删除 plan（本工具不接受空 layers）")
    plan_now = svc.get_audio_plan(sid)      # 写后重读 canonical 验证
    verified = plan_now is not None and plan_now.revision == plan.revision
    # 🔴 45.5 真机教训：narration 层 duration_ms 取整偏短（混音 atrim 截掉句尾
    # 19–253ms）+ fade_out 淡出尾字——逐字被"吃"且相关审计不可见（截断切片与源
    # 切片仍完美相关）。此处对 narration 层做确定性切片体检，回执显式告警。
    narration_slice_warnings: list = []
    if verified:
        from src.sequence.media_ops import probe_video as _pv
        out_root = pdir / "output"
        for l in plan_now.layers:
            if l.role != "narration" or l.source.kind != "imported_audio":
                continue
            f = out_root / (l.source.file or "")
            try:
                src_ms = int(_pv(str(f))["duration_ms"])
            except Exception:   # noqa: BLE001 - 文件缺失留给 render Phase 1 fail closed
                continue
            used_end = l.source_in_ms + l.duration_ms
            if used_end < src_ms:
                narration_slice_warnings.append(
                    f"{l.layer_id}: duration={l.duration_ms}ms 但源文件 {src_ms}ms"
                    f"——句尾会被截断 {src_ms - used_end}ms；整句播放请把 duration_ms "
                    f"设为 {src_ms}")
            if l.fade_out_ms or l.fade_in_ms:
                narration_slice_warnings.append(
                    f"{l.layer_id}: fade_in/out={l.fade_in_ms}/{l.fade_out_ms}ms"
                    "——旁白淡入淡出会吃掉句首/句尾字，通常应为 0")
    if verified:
        _audit(rt, project).record(
            agent_id="assembly", field=f"audio_plan.{sid}",
            before={"revision": before_rev},
            after={"revision": plan.revision, "layers": len(plan.layers)},
            instruction=instruction, message_id=message_id)
    return _base_receipt(
        "assembly.audio_plan_put", ok=True, target=sid,
        revision=plan.revision, verified=verified,
        sequence_id=sid, layer_count=len(plan.layers),
        narration_slice_warnings=narration_slice_warnings,
        base_gain_db=plan.base_gain_db,
        ducking=plan.ducking.to_dict() if plan.ducking else None,
        loudness=plan.loudness.to_dict() if plan.loudness else None,
        op_summary=(f"AudioPlan 写入 {sid}（{len(plan.layers)} 层，"
                    f"revision={plan.revision}）"))


def _run_ffmpeg_graph(graph: dict, out: Path) -> None:
    import subprocess as _sp
    cmd = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
           + graph["input_args"] + ["-filter_complex", graph["filter_complex"]]
           + graph["out_args"] + [str(out)])
    proc = _sp.run(cmd, capture_output=True, timeout=600)
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        raise SequenceError("stem_render_failed",
                            proc.stderr.decode("utf-8", errors="replace")[-300:])


def _execute_stem_render(rt: AgentRuntime, project: str, arguments: str,
                         instruction, message_id) -> dict:
    """分轨导出（provenance=plan revision；旁白/音乐独立 WAV）。"""
    args = _parse_args(arguments)
    sid = args.get("sequence_id")
    roles = args.get("roles") or []
    if not sid or not roles:
        return {"ok": False, "error": "sequence_id 与 roles 必填"}
    pdir = rt._resolve_project_dir(project)
    svc = _svc(pdir, project)
    plan = svc.get_audio_plan(sid)
    if plan is None:
        return {"ok": False, "error": "该 sequence 无 AudioPlan——先 audio_plan_put"}
    from src.sequence.audio_basis import resolve_audio_source
    from src.sequence.audio_mix import (MixLayer, build_audio_mix_plan,
                                        compile_stem_filter_graph)
    mix_layers = [MixLayer(layer_id=l.layer_id, role=l.role,
                           source_file=str(resolve_audio_source(
                               pdir, l.layer_id, l.source).file),
                           source_in_ms=l.source_in_ms, duration_ms=l.duration_ms,
                           start_ms=l.start_ms, gain_db=l.gain_db,
                           fade_in_ms=l.fade_in_ms, fade_out_ms=l.fade_out_ms)
                  for l in plan.layers]
    outdir = pdir / "output" / "assembly" / "stems"
    outdir.mkdir(parents=True, exist_ok=True)
    stems = []
    try:
        plan_ = build_audio_mix_plan("", plan.revision, mix_layers)
        g = compile_stem_filter_graph(plan_, [str(r) for r in roles])
        out = outdir / f"stem_{'_'.join(sorted(str(r) for r in roles))}_rev{plan.revision}.wav"
        _run_ffmpeg_graph(g, out)
        import hashlib as _hashlib
        stems.append({"roles": list(roles), "file": str(out),
                      "sha256": _hashlib.sha256(out.read_bytes()).hexdigest()})
    except SequenceError as e:
        return _base_receipt("assembly.stem_render", ok=False, target=sid,
                             error=e.message, code=e.code)
    _audit(rt, project).record(
        agent_id="assembly", field=f"stems.{sid}.{plan.revision}",
        before=None, after={"roles": list(roles)}, instruction=instruction,
        message_id=message_id)
    return _base_receipt(
        "assembly.stem_render", ok=True, target=sid, verified=True,
        sequence_id=sid, plan_revision=plan.revision, stems=stems,
        op_summary=f"分轨导出 {list(roles)}（plan rev {plan.revision}）")


# ============ Phase 45.1：音频资产库发现（只读）============
_AUDIO_ASSET_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}


def _execute_audio_assets_read(rt: AgentRuntime, project: str, arguments: str,
                               instruction, message_id) -> dict:
    """枚举 output/audio_imports/ 资产库：ffprobe 实测 + sha256 + 可选身份档
    （assets_meta.json）+ 当前 AudioPlan 引用关系。45.0.3 最小补口——Agent 选配乐
    /环境声前的正式只读入口，禁止经 shell 自行搬运文件。"""
    import hashlib
    pdir = rt._resolve_project_dir(project)
    out_root = pdir / "output"
    lib = out_root / "audio_imports"
    identity: dict = {}
    meta_path = lib / "assets_meta.json"
    if meta_path.is_file():
        try:
            identity = (json.loads(meta_path.read_text(encoding="utf-8"))
                        or {}).get("assets") or {}
        except Exception:   # noqa: BLE001 - 身份档损坏不阻塞枚举，如实标注
            identity = {}
    # 当前各 sequence AudioPlan 对资产的引用（file → [{sequence_id, layer_id, role}]）
    refs: Dict[str, list] = {}
    try:
        svc = _svc(pdir, project)
        for sid, _entry in svc.list_entries().items():
            plan = svc.get_audio_plan(sid)
            if plan is None:
                continue
            for l in plan.layers:
                if l.source.kind == "imported_audio" and l.source.file:
                    refs.setdefault(l.source.file, []).append(
                        {"sequence_id": sid, "layer_id": l.layer_id,
                         "role": l.role})
    except Exception:   # noqa: BLE001
        pass
    from src.sequence.media_ops import probe_video
    assets: list = []
    if lib.is_dir():
        for p in sorted(lib.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in _AUDIO_ASSET_EXTS:
                continue
            parts = p.relative_to(lib).parts
            if any(str(x).startswith(".") for x in parts):
                continue    # .raw 等留证目录不作为可选资产
            rel = p.relative_to(out_root).as_posix()
            ident = identity.get(rel) or identity.get(p.name)
            entry: Dict[str, Any] = {
                "file": rel,
                "referenced_by": refs.get(rel) or [],
                "identity": ident or {"registered": False,
                                      "note": "无身份档（assets_meta.json 未登记）"},
            }
            try:
                probe = probe_video(str(p))
                entry.update({
                    "duration_ms": probe.get("duration_ms"),
                    "acodec": probe.get("acodec"),
                    "sample_rate": probe.get("sample_rate"),
                    "channels": probe.get("channels"),
                    "sha256_12": hashlib.sha256(p.read_bytes()).hexdigest()[:12],
                    "size_bytes": p.stat().st_size,
                })
            except Exception as e:   # noqa: BLE001 - 单文件损坏不影响其余枚举
                entry["probe_error"] = str(e)[:160]
            assets.append(entry)
    return {"ok": True, "library_dir": "output/audio_imports",
            "asset_count": len(assets), "assets": assets}


TOOL_EXECUTORS: Dict[str, Any] = {

    "assembly.briefing": _execute_briefing,
    "assembly.snapshot_read": _execute_snapshot_read,
    "assembly.snapshot_freeze": _execute_snapshot_freeze,
    "assembly.sequence_read": _execute_sequence_read,
    "assembly.media_probe": _execute_media_probe,
    "assembly.timeline_preview": _execute_timeline_preview,
    "assembly.render_status": _execute_render_status,
    "assembly.sequence_create": _execute_sequence_create,
    "assembly.sequence_update": _execute_sequence_update,
    "assembly.render_submit": _execute_render_submit,
    "assembly.manifest_publish": _execute_manifest_publish,
    "assembly.proposal_write": _execute_proposal_write,
    "assembly.narration_read": _execute_narration_read,
    "assembly.tts_synthesize": _execute_tts_synthesize,
    "assembly.audio_plan_read": _execute_audio_plan_read,
    "assembly.audio_plan_put": _execute_audio_plan_put,
    "assembly.stem_render": _execute_stem_render,
    "assembly.audio_assets_read": _execute_audio_assets_read,
    "web.search": lambda rt, project, arguments, instruction, message_id:
        _execute_web_search(rt, project, arguments, instruction, message_id),
}

TOOL_LABELS = {
    "assembly.briefing": "成片全局状态一览",
    "assembly.snapshot_read": "读取冻结快照",
    "assembly.snapshot_freeze": "冻结本项目正式装配快照",
    "assembly.sequence_read": "读取 Sequence canonical",
    "assembly.media_probe": "媒体实测探测",
    "assembly.timeline_preview": "时间线 dry-run 预演",
    "assembly.render_status": "渲染状态与诊断",
    "assembly.sequence_create": "创建成片 Sequence",
    "assembly.sequence_update": "更新成片 Sequence",
    "assembly.render_submit": "发起成片渲染",
    "assembly.manifest_publish": "发布时间线契约 manifest",
    "assembly.proposal_write": "写剪辑提案",
    "assembly.narration_read": "旁白编排上下文",
    "assembly.tts_synthesize": "合成/复用一句旁白",
    "assembly.audio_plan_read": "读取混音计划",
    "assembly.audio_plan_put": "写入混音计划",
    "assembly.stem_render": "导出分轨",
    "assembly.audio_assets_read": "枚举音频资产库",
    "shot.list": "列出镜头",
    "web.search": "搜索资料",
}


def _tool_label(name: str) -> str:
    return TOOL_LABELS.get(name, name)


def _op_human(r: dict) -> str:
    if r.get("op_summary"):
        return str(r["op_summary"])
    if r.get("ok"):
        return f"{r.get('operation', '?')} 完成"
    return f"{r.get('operation', '?')} 失败：{r.get('error') or r.get('code') or '失败'}"


def _tool_summary(name: str, obs: dict) -> str:
    if obs.get("ok"):
        if obs.get("op_summary"):
            return str(obs["op_summary"])[:120]
        if obs.get("summary"):
            return str(obs["summary"])[:120]
        return f"{_tool_label(name)}完成"
    return f"{_tool_label(name)}失败：{obs.get('error') or obs.get('code') or '失败'}"


def _trace_entry(step: int, name: str, args: str, obs: dict) -> dict:
    return {"step": step, "tool": name,
            "target": (obs.get("target") or obs.get("sequence_id")
                       or obs.get("shot_id") or ""),
            "result": ("ok" if obs.get("ok") else
                       ("stale" if obs.get("code") == "revision_conflict" else "error")),
            "revision": obs.get("revision"),
            "error": (str(obs.get("error"))[:160] if obs.get("error") else None)}


def _ledger_brief(receipts: list) -> str:
    if not receipts:
        return "（本轮尚无任何写工具调用回执）"
    return "\n".join(f"- {r.get('operation', '?')}："
                     f"{('成功/verified' if r.get('ok') and r.get('verified') else '未成功')}"
                     f" {r.get('op_summary') or r.get('error') or ''}"
                     for r in receipts[-8:])


def _honest_fallback(receipts: list) -> str:
    """真相闸门纠偏次数用尽后的确定性诚实覆写（非模型文本）。"""
    real = [r for r in receipts if r.get("ok") and r.get("verified")
            and not r.get("unchanged")]
    if not real:
        return "本轮没有已确认成功的成片域修改。"
    return "本轮已确认的成片域修改：" + "；".join(
        f"{r.get('operation')}（{_op_human(r)}）" for r in real)


def _refresh_system(messages: list, rt: AgentRuntime, project: str) -> None:
    """每步工具调用后刷新 system（项目元信息/快照可用性）。镜像 scene_designer。"""
    try:
        pdir = rt._resolve_project_dir(project)
        proj = ProjectStore(pdir).load()
        sys_text = _build_system(proj, rt, project)
        if messages and messages[0].get("role") == "system":
            messages[0] = {"role": "system", "content": sys_text}
        else:
            messages.insert(0, {"role": "system", "content": sys_text})
    except Exception:
        pass


# ============ tool-loop（镜像 scene_designer 结构，装配域语义）============
async def _run_tool_loop(rt: AgentRuntime, client, project: str,
                         messages: list, instruction: str,
                         message_id: Optional[str],
                         max_steps: int = DEFAULT_MAX_TOOL_STEPS,
                         thinking: bool = False,
                         event_cb=None,
                         state: Optional[dict] = None,
                         cancel_event=None,
                         tool_surface: Optional[list] = None) -> dict:
    """原生 function-calling 循环 + 执行真实性收口（结构性，非提示词）。
    装配域语义：单写串行 / 回执账本 / zero-write recovery / 重复失败护栏 /
    超时恢复 / renewable window / 真相闸门（装配版事实摘要）。"""
    assembly_mutated = False
    if state is None:
        state = {}
    events: list = state.setdefault("events", [])
    receipts: list = state.setdefault("receipts", [])
    write_attempts = 0
    corrective_round = 0
    zero_write_recovery_used = False
    trace: list = state.setdefault("trace", [])
    reasonings: list = state.setdefault("reasonings", [])
    tools_offered = tool_surface if tool_surface is not None else _tool_surface()
    last_read_after_write = -1
    search_count = 0
    search_sources: list = state.setdefault("search_sources", [])
    search_queries_seen: set = state.setdefault("search_queries_seen", set())
    repeat_fail_streak = 0
    last_fail_sig = None
    timeout_retries = 0

    def _on_read_timeout() -> bool:
        nonlocal timeout_retries
        if timeout_retries >= MAX_TIMEOUT_RETRIES:
            return False
        timeout_retries += 1
        nudge = (
            f"[系统恢复提示 MODEL_READ_TIMEOUT] 上一次模型响应超时被中断"
            f"（read timeout，第 {timeout_retries}/{MAX_TIMEOUT_RETRIES} 次）。"
            f"本次没有形成完整工具调用、没有新增任何提交。"
            f"请【只继续尚未完成的部分，不要重做已提交项】。"
        )
        messages.append({"role": "user", "content": nudge})
        _refresh_system(messages, rt, project)
        return True

    step = -1
    window_used = 0
    window_renewals = 0
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise _TurnStopped()
        if window_used >= max_steps:
            window_renewals += 1
            window_used = 0
            timeout_retries = 0
            messages.append({"role": "user", "content": _WINDOW_SELF_CHECK_NUDGE})
            _refresh_system(messages, rt, project)
            log.info("assembly tool-loop 执行窗口续期（project=%s renewals=%s)",
                     project, window_renewals)
        step += 1
        window_used += 1
        if cancel_event is not None and cancel_event.is_set():
            raise _TurnStopped()
        content = None
        tool_calls = None
        try:
            if event_cb:
                step_reasoning_started = False
                step_reason_t0 = 0.0
                step_reasoning_buf: list[str] = []
                step_content_buf: list[str] = []
                async for ev in client.chat_completion_stream(
                        messages, temperature=0.7, max_tokens=65536,
                        thinking=thinking, tools=tools_offered,
                        tool_choice="auto"):
                    k = ev["kind"]
                    if k == "content_delta":
                        step_content_buf.append(ev["delta"])
                    elif k == "reasoning_delta":
                        if not step_reasoning_started:
                            step_reasoning_started = True
                            step_reason_t0 = time.perf_counter()
                            event_cb({"type": "reasoning.started", "step_id": step})
                        step_reasoning_buf.append(ev["delta"])
                        event_cb({"type": "reasoning.delta", "step_id": step,
                                  "delta": ev["delta"]})
                    elif k == "tool_call_pending":
                        event_cb({"type": "tool.pending", "step_id": step,
                                  "name": ev["name"], "label": _tool_label(ev["name"])})
                    elif k == "finish":
                        content = ev["content"]
                        tool_calls = ev["tool_calls"]
                if step_reasoning_started:
                    rtext = "".join(step_reasoning_buf)
                    rdur = int((time.perf_counter() - step_reason_t0) * 1000)
                    event_cb({"type": "reasoning.completed", "step_id": step,
                              "duration_ms": rdur})
                    if rtext:
                        reasonings.append({"step": step, "text": rtext, "duration_ms": rdur})
                if not tool_calls and step_content_buf:
                    for d in step_content_buf:
                        event_cb({"type": "assistant.delta", "delta": d})
            else:
                resp = await client.chat_completion(
                    messages, temperature=0.7, max_tokens=65536,
                    tools=tools_offered, tool_choice="auto")
                tool_calls = resp.get("tool_calls")
                content = resp.get("content")
        except httpx.ReadTimeout:
            if not _on_read_timeout():
                raise
            continue

        if not tool_calls:
            text = (content or "").strip()
            # ---- 真相闸门（装配域）----
            real_changes = [r for r in receipts
                            if r.get("ok") and r.get("verified") and not r.get("unchanged")]
            chat_turn = (write_attempts == 0 and _is_chat_turn(instruction)
                         and not _ASSEMBLY_EXPLICIT_ACTION_RE.search(instruction or ""))
            false_claim = _claims_success(text) and not real_changes and not chat_turn
            real_searches = [t for t in trace if t["tool"] == "web.search" and t.get("ok")]
            search_false_claim = (_claims_search_success(text) and not real_searches
                                  and not chat_turn)
            last_write_step = max((t["step"] for t in trace
                                   if t["tool"] in WRITE_TOOL_DOMAIN), default=-1)
            reply_summarizes = bool(_ASSEMBLY_SUMMARY_RE.search(text or ""))
            needs_final_read = (real_changes and _claims_success(text)
                                and reply_summarizes
                                and last_read_after_write < last_write_step)
            if (not zero_write_recovery_used
                    and write_attempts == 0 and not real_changes
                    and _is_explicit_write_request(instruction)):
                zero_write_recovery_used = True
                log.info("assembly zero-write recovery 触发（project=%s）", project)
                messages.append({"role": "assistant", "content": text or ""})
                messages.append({"role": "user", "content": _ZERO_WRITE_RECOVERY_NUDGE})
                _refresh_system(messages, rt, project)
                continue
            if false_claim or search_false_claim or needs_final_read:
                if corrective_round < MAX_CORRECTIVE_ROUNDS:
                    corrective_round += 1
                    messages.append({"role": "assistant", "content": text or ""})
                    brief = _ledger_brief(receipts)
                    if search_false_claim:
                        nudge = (
                            "你上面的回复声称做过搜索/给出了搜索结果，但本轮【没有任何一次真实成功的 "
                            "web.search 工具调用】。请要么现在真正调用 web.search，"
                            "要么如实告诉用户：本轮没有搜索，或搜索失败、资料不足。")
                    elif needs_final_read:
                        nudge = (
                            "你做了一些修改并声称完成，但最后一次真实读取发生在最后一次写入【之前】。"
                            "收尾前请先用 assembly.sequence_read / assembly.render_status 读取最新"
                            "状态，然后【以这次读取的真实结果】总结你完成了什么。")
                    elif write_attempts == 0:
                        nudge = (
                            "你上面声称已经创建/修改了成片装配，但本轮你【根本没有发起对应的"
                            "工具调用】。光用文字说「已完成」不会改动任何东西。\n"
                            + brief + "\n请要么真正调用对应 assembly.* 写工具完成修改，"
                            "要么如实告诉用户你没有改动、需要怎么做。")
                    else:
                        nudge = (
                            "你上面声称已经完成，但工具的真实返回显示没有任何一次写入成功确认：\n"
                            + brief + "\n请只依据上面真实的工具返回结果回答。可以用最新数据重新调用"
                            "工具重试；如果不打算重试，就如实告诉用户这次没有改动成功。")
                    messages.append({"role": "user", "content": nudge})
                    _refresh_system(messages, rt, project)
                    continue
                text = _honest_fallback(receipts)
            text = strip_reserved_fact_blocks(text)
            return {"text": text or "已完成。",
                    "assembly_mutated": assembly_mutated,
                    "events": events, "receipts": receipts, "trace": trace,
                    "reasonings": reasonings, "search_sources": search_sources,
                    "window_renewals": window_renewals}

        messages.append({"role": "assistant", "content": content or "",
                         "tool_calls": tool_calls})

        write_done_this_step = False   # 单写串行：一步只执行一个写操作
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            args = fn.get("arguments") or "{}"
            call_id = tc.get("id") or name

            if event_cb:
                event_cb({"type": "tool.started", "step_id": step, "call_id": call_id,
                          "name": name, "label": _tool_label(name)})

            is_write_tool = (name.startswith("assembly.")
                             and name not in ASSEMBLY_READ_TOOLS
                             and name in WRITE_TOOL_DOMAIN)
            if is_write_tool and write_done_this_step:
                obs = {
                    "ok": False, "operation": name,
                    "error": "本步已执行过一个写操作。写工具必须一个一个来：等前一个完成、"
                             "拿到返回的最新 revision/状态后，再发下一个写工具调用。"
                             "不要在同一次回复里批量发多个写工具（会用旧 revision 导致 stale）。"
                             "只读工具（assembly.briefing/snapshot_read/sequence_read/"
                             "media_probe/timeline_preview/render_status/web.search）可以一次多个。",
                }
                messages.append({"role": "tool", "tool_call_id": call_id,
                                 "content": json.dumps(obs, ensure_ascii=False)})
                trace.append(_trace_entry(step, name, args, obs))
                if event_cb:
                    event_cb({"type": "tool.completed", "step_id": step,
                              "call_id": call_id, "name": name, "ok": False,
                              "summary": "本步写工具串行，已跳过"})
                continue

            executor = TOOL_EXECUTORS.get(name)
            exc_msg = None
            if (is_write_tool and executor is not None
                    and not (str(fn.get("arguments") or "").strip().strip('"'))):
                obs = {"ok": False, "operation": name, "code": "empty_arguments",
                       "error": ("工具调用的 arguments 为空。请把参数以 JSON 对象写在 "
                                 f"arguments 字段里再调用 {name}（以该工具 schema 的必填字段为准）。")}
            elif (executor is not None and name == "web.search"
                    and search_count >= MAX_WEB_SEARCH):
                obs = {"ok": False, "operation": "web.search",
                       "code": "search_budget_exhausted",
                       "error": "本轮搜索次数已达上限。请基于已有信息完成任务。"}
            elif (executor is not None and name == "web.search"
                    and _normalized_search_query(args)
                    and _normalized_search_query(args) in search_queries_seen):
                obs = {"ok": False, "operation": "web.search",
                       "code": "duplicate_query",
                       "error": "本 turn 已用完全相同的查询词搜索过，请换角度或基于已有结果继续。"}
            elif executor is None:
                obs = {"ok": False, "error": f"未知工具：{name}", "operation": name}
            else:
                if name == "web.search":
                    search_count += 1
                    _q_norm = _normalized_search_query(args)
                    if _q_norm:
                        search_queries_seen.add(_q_norm)
                try:
                    if asyncio.iscoroutinefunction(executor):
                        obs = await executor(rt, project, args, instruction, message_id)
                    else:
                        obs = executor(rt, project, args, instruction, message_id)
                except AgentError as e:
                    exc_msg = e.message
                    obs = {"ok": False, "error": e.message, "code": e.code,
                           "operation": name}
                except Exception as e:
                    exc_msg = f"工具执行异常：{str(e)[:200]}"
                    obs = {"ok": False, "error": exc_msg,
                           "operation": name}

            ok_now = bool(obs.get("ok"))
            if ok_now:
                repeat_fail_streak = 0
                last_fail_sig = None
            else:
                sig = (name, obs.get("code") or obs.get("error") or "err")
                if sig == last_fail_sig:
                    repeat_fail_streak += 1
                else:
                    repeat_fail_streak = 1
                    last_fail_sig = sig
                if repeat_fail_streak >= MAX_REPEAT_FAIL:
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "content": json.dumps(obs, ensure_ascii=False)})
                    _refresh_system(messages, rt, project)
                    nudge = (f"「{name}」已连续 {repeat_fail_streak} 次以同样方式失败"
                             f"（{obs.get('error') or obs.get('code')}）。继续重复同样调用不会成功。"
                             "请改用只读工具（assembly.briefing / assembly.sequence_read）确认当前"
                             "真实状态，换个参数或换种做法，或如实告诉用户当前遇到的问题、"
                             "哪些已成功完成。")
                    messages.append({"role": "assistant", "content": content or ""})
                    messages.append({"role": "user", "content": nudge})
                    trace.append(_trace_entry(step, name, args, obs))
                    _refresh_system(messages, rt, project)
                    if event_cb:
                        if exc_msg:
                            event_cb({"type": "tool.failed", "step_id": step, "call_id": call_id,
                                      "name": name, "error": exc_msg})
                        else:
                            event_cb({"type": "tool.completed", "step_id": step,
                                      "call_id": call_id, "name": name, "ok": False,
                                      "summary": _tool_summary(name, obs)})
                    break

            # 写域调用记入账本（WRITE_TOOL_DOMAIN 是账本分类的唯一权威）。
            if is_write_tool:
                write_attempts += 1
                obs.setdefault("operation", name)
                receipts.append(obs)
                write_done_this_step = True
            else:
                if ok_now and name != "web.search":
                    last_read_after_write = step
                if name == "web.search" and obs.get("ok") and obs.get("sources"):
                    for s in obs["sources"]:
                        if s and (s.get("url") or s.get("title")) \
                                and s not in search_sources:
                            search_sources.append({"title": s.get("title") or "",
                                                   "url": s.get("url") or ""})

            # 据【真实回执】设置 mutated 标志 + 事件。
            if (name in WRITE_TOOL_DOMAIN and obs.get("ok")
                    and obs.get("verified") and not obs.get("unchanged")):
                assembly_mutated = True
                events.append({"kind": "sequence_change",
                               "summary": obs.get("op_summary") or f"成片装配变更（{name}）"})

            if event_cb:
                if exc_msg:
                    event_cb({"type": "tool.failed", "step_id": step, "call_id": call_id,
                              "name": name, "error": exc_msg})
                else:
                    event_cb({"type": "tool.completed", "step_id": step,
                              "call_id": call_id, "name": name, "ok": bool(obs.get("ok")),
                              "summary": _tool_summary(name, obs)})
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": json.dumps(obs, ensure_ascii=False)})
            trace.append(_trace_entry(step, name, args, obs))

        _refresh_system(messages, rt, project)


# 镜像 scene_designer 的窗口/恢复 nudge（每文件一份镜像，仓库惯例）。
_WINDOW_SELF_CHECK_NUDGE = (
    "【系统提示·执行窗口】你已经连续执行了较长时间（当前执行窗口已用满）。请检查"
    "当前用户目标与最新的工具返回结果，然后自行判断：\n"
    "① 如果任务已经完成，正常结束并汇报结果即可；\n"
    "② 如果你确认自己正在重复相同的操作、没有取得任何新进展，并且已经没有合理的"
    "新方向，可以停止并如实说明卡点；\n"
    "③ 如果任务尚未完成且仍有明确、合理的下一步，请继续执行——不要因为执行步数"
    "而停止，系统会自动为你续接新的执行窗口。"
)

_ZERO_WRITE_RECOVERY_NUDGE = (
    "【系统提示·零写入恢复】你的任务看起来要求对成片装配做实际修改，但你至今没有"
    "发起过任何 assembly.* 写工具调用。请：①先用只读工具（assembly.briefing / "
    "assembly.sequence_read）确认当前真实状态；②若确有需要创建/修改之处，用对应 "
    "assembly.* 写工具真正执行；③若你判断没有值得修改之处（例如成片已就绪），"
    "如实地向用户说明结论与依据。不要凭空声称已完成。"
)

# 回复中像在「总结成片现状/成果」的正则（needs_final_read 收尾判定用）。
_ASSEMBLY_SUMMARY_RE = re.compile(
    r"成片|装配|时间线|Sequence|渲染完成|已渲染|manifest|片长|切点|接缝")

# 显式装配动作词（真相闸门武装：指令含任一 → 非聊天轮）。
_ASSEMBLY_EXPLICIT_ACTION_RE = re.compile(
    r"创建|更新|渲染|发布|装配|成片|拼接|剪辑|提交|发起|串联|"
    r"assembly\.(sequence|render|manifest|proposal)\.")


# ============ turn 入口（镜像 scene_designer_turn 契约）============
async def assembly_turn(rt: AgentRuntime, project: str, session_id: str,
                        content: str, *, thinking: bool = False,
                        event_cb=None, cancel_event=None) -> dict:
    """Assembly Agent 原生 tool-loop turn。返回 {assistant_message, assembly_mutated,
    events, message, intent, receipts, trace, session_id, agent_id}。"""
    store = rt._session_store(project)
    session = store.load(session_id)
    if session is None:
        raise AgentError("unknown_scope", f"session 不存在: {session_id}")
    if session.agent_id != "assembly":
        raise AgentError("permission_denied", "该会话仅限 Assembly Agent")
    if not rt._is_enabled("assembly"):
        raise AgentError("disabled_agent", "Assembly Agent 已禁用")

    user_msg = AgentMessage(role="user", content=content)
    store.append_message(session_id, user_msg)
    rt._maybe_capture_preference(project, content, user_msg.message_id)
    session = store.load(session_id)

    pdir = rt._resolve_project_dir(project)
    from .session_governance import capture_brief_if_absent
    capture_brief_if_absent(pdir, project, content, "assembly")
    history = [{"role": m.role, "content": m.content}
               for m in session.messages[:-1]
               if m.role in ("user", "assistant") and m.content and m.status is None]

    client = rt._resolve_client_or_none(agent_id="assembly")
    assistant_text: str
    status: Optional[str] = None
    assembly_mutated = False
    events: list = []
    receipts: list = []
    reasoning: list = []
    trace: list = []
    search_sources: list = []

    if client is None:
        assistant_text = ("[chat 未配置：Agnes 推理模型不可用，无法执行成片装配。"
                          "请在设置中配置 API Key。]")
        status = ResultStatus.NOT_CONFIGURED.value
    else:
        proj = ProjectStore(pdir).load()
        system = _build_system(proj, rt, project)
        mem = rt._memory_supplement(project, "assembly")
        if mem:
            system += "\n\n" + mem
        messages: list[dict] = [{"role": "system", "content": system}]
        messages += history
        messages.append({"role": "user", "content": content})

        loop_state: dict = {}
        try:
            final = await _run_tool_loop(rt, client, project, messages, content,
                                         user_msg.message_id,
                                         thinking=thinking, event_cb=event_cb,
                                         state=loop_state, cancel_event=cancel_event,
                                         tool_surface=_tool_surface())
            assistant_text = final["text"]
            assembly_mutated = final.get("assembly_mutated", False)
            events = final["events"]
            receipts = final.get("receipts", [])
            trace = final.get("trace", [])
            reasoning = final.get("reasonings", [])
            search_sources = final.get("search_sources", [])
        except _TurnStopped:
            log.info("assembly_turn 被停止（project=%s session=%s）",
                     project, session_id)
            assistant_text = "[任务已停止] 已完成部分见工具时间线；其余未执行。"[:300]
            status = ResultStatus.STOPPED.value
            events = loop_state.get("events", [])
            receipts = loop_state.get("receipts", [])
            trace = loop_state.get("trace", [])
            reasoning = loop_state.get("reasonings", [])
            search_sources = loop_state.get("search_sources", [])
        except Exception as e:
            log.exception("assembly_turn 工具循环异常（project=%s session=%s）",
                          project, session_id)
            assistant_text = f"[执行失败: {type(e).__name__}: {str(e).strip()}]"[:300]
            status = ResultStatus.FAILED.value
            events = loop_state.get("events", [])
            receipts = loop_state.get("receipts", [])
            trace = loop_state.get("trace", [])
            reasoning = loop_state.get("reasonings", [])
            search_sources = loop_state.get("search_sources", [])

    intent = "sequence_change" if assembly_mutated else "conversation"

    # 回执 meta 透传（sequence 域字段——变更派生只从真实回执字段取）。
    receipts_meta = []
    for r in receipts:
        receipts_meta.append({
            "operation": r.get("operation"), "ok": bool(r.get("ok")),
            "verified": bool(r.get("verified")), "stale": bool(r.get("stale")),
            "unchanged": bool(r.get("unchanged")),
            "error": r.get("error"),
            "code": r.get("code"),
            "op_summary": r.get("op_summary"),
            "revision": r.get("revision"),
            "expected_revision": r.get("expected_revision"),
            "current_revision": r.get("current_revision"),
            "sequence_id": r.get("sequence_id"),
            "render_version_id": r.get("render_version_id"),
            "job_id": r.get("job_id"),
            "assembly_revision": r.get("assembly_revision"),
            "manifest_file": r.get("manifest_file"),
            "proposal_file": r.get("proposal_file"),
        })
    meta = {"intent": intent, "events": events, "evidence_refs": [],
            "receipts": receipts_meta,
            "write_attempts": len(receipts_meta),
            "write_success": sum(1 for r in receipts_meta
                                 if r["ok"] and r["verified"] and not r["unchanged"]),
            "trace": trace,
            "reasonings": reasoning,
            "search_sources": search_sources}
    asst = AgentMessage(role="assistant", content=assistant_text, status=status, meta=meta)
    store.append_message(session_id, asst)

    return {"assistant_message": assistant_text,
            "assembly_mutated": assembly_mutated,
            "events": events, "message": asst.to_dict(),
            "intent": intent, "receipts": receipts_meta, "trace": trace,
            "search_sources": search_sources,
            "evidence_refs": [], "linked_task_ids": [],
            "linked_change_session_id": None, "session_id": session_id,
            "agent_id": "assembly",
            "prerequisite_warnings": []}
