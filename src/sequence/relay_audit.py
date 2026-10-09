# src/sequence/relay_audit.py
# Phase51 B（B4）：接力端点 vs 实际保留端点一致性审计。
#
# 背景（B4 执行书 + P50 实证机制）：接力依据 = source 镜最终视频的「实际末帧」
# （ffmpeg 从源文件全长末尾提取，routers/shots._extract_video_end_frame，版本 id
# 在档 video_end_source_version）。而成片装配（Sequence clip）可对 source 做
# trim_out（尾部裁剪）——观众实际看到的末帧是「保留区间末帧」，不是源全长末帧。
# 裁切/选版变化后若不复核，下游接力镜的开场依据与观众所见脱节（B4：接力依据
# 必须对应最终保留片段）。
#
# 性质：纯审计（只报告不阻断，与 S2 audio_peak_warning 同模式）；零 provider、
# 零写盘；由 prepare_render 调用并把结果挂到 render version 的 advisory 字段。
from __future__ import annotations
from typing import Any, Dict, List, Optional


def audit_relay_endpoints(project, resolved) -> Optional[Dict[str, Any]]:
    """审计本 sequence 内接力链的端点一致性。
    project: src.core.models.Project（已加载 canonical）
    resolved: List[sequence.timeline.ResolvedClip]（prepare_render 已解析的源真相）
    返回 None（无风险）或 {"code": ..., "affected": [...], "note": str}。

    判定（只报两类真实脱节，不做猜测）：
    1. trim_out 脱节：source 镜在本 sequence 中被尾部裁剪（trim_out_ms>0）——
       接力端点取自源全长末帧 ≠ 观众看到的保留末帧。
    2. 版本脱节：source 镜的 video_end_source_version 与本 render 实际解析的
       version_id 不一致——接力帧来自旧版本（选版变化后未重提取）。"""
    shots = {}
    for sc in (project.scenes or []):
        for sh in (sc.shots or []):
            shots[sh.shot_id] = sh
    by_shot = {rc.shot_id: rc for rc in resolved}
    affected: List[Dict[str, Any]] = []
    for rc in resolved:
        sh = shots.get(rc.shot_id)
        if sh is None:
            continue
        src_id = getattr(sh, "source_shot_id", None)
        if not src_id:
            continue
        src = shots.get(src_id)
        if src is None:
            affected.append({"shot_id": rc.shot_id, "source_shot_id": src_id,
                             "kind": "source_missing",
                             "note": "接力来源镜头在项目内不存在（来源被删/改名）"})
            continue
        end_ver = getattr(src, "video_end_source_version", None)
        # 比对对象=来源镜自身在本 render 的 resolved clip（不是接力目标镜的 clip）
        src_rc = by_shot.get(src_id)
        # 1) 版本脱节：本 render 解析到的 source 版本 ≠ 末帧提取时的版本
        if end_ver and src_rc is not None and src_rc.version_id and \
                end_ver != src_rc.version_id:
            affected.append({"shot_id": rc.shot_id, "source_shot_id": src_id,
                             "kind": "version_mismatch",
                             "end_source_version": end_ver,
                             "resolved_version_id": src_rc.version_id,
                             "note": "接力末帧提取自旧版本，本 render 解析到新版本"
                                     "（选版变化后未重提取端点）"})
        # 2) trim_out 脱节：来源在本 sequence 中被尾部裁剪
        if src_rc is not None and (src_rc.trim_out_ms or 0) > 0:
            affected.append({"shot_id": rc.shot_id, "source_shot_id": src_id,
                             "kind": "trim_tail_cut",
                             "trim_out_ms": src_rc.trim_out_ms,
                             "source_duration_ms": src_rc.source_duration_ms,
                             "note": "接力端点=源全长末帧；本 sequence 对来源做了尾部"
                                     f"裁剪 {src_rc.trim_out_ms}ms——观众看到的保留末帧与"
                                     "接力依据不一致，需人工复核切点或重提取端点"})
    if not affected:
        return None
    kinds = sorted({a["kind"] for a in affected})
    return {"code": "relay_endpoint_mismatch_risk",
            "kinds": kinds,
            "affected": affected,
            "note": ("B4 审计：接力镜的开场依据与实际保留端点存在脱节风险"
                     "（只报告不阻断；处理选项=复核切点/重提取端点/保留并记录）")}
