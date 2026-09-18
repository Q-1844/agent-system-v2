# src/agents/review_retry_guard.py
# Per-resource review failure guard（共享 helper）。
#
# 起源：Phase C1 Work A（27.md §3）在 Resource Agent 内落地的
# 「同一资源 + 同一语义 basis + 连续 needs_fix」烧图护栏。
# 28.md §11（Phase C2）：抽成可供 Resource Agent / Scene Agent 共同复用的共享
# helper——【不要复制两套 guard】。本模块只做语义 basis 维度的重复失败拦截；
# 不做 MAX_AGENT_STEPS / 总时长 / 总生成数一类总闸（27.md §3.1 裁决不变）。
#
# 状态语义（不变）：进程内存、per-turn（message_id 维度），绝不写 canonical
# （不是资源 ontology）。needs_fix → 连续计数 +1；pass → 清零；其它
# （uncertain/provider_error）→ 不计连续。任何有效输入变化 → 新 basis → 计数归零。
# 换 seed 不是语义 basis 变化（28.md §18——本工具面本就不暴露 seed）。
from __future__ import annotations
import hashlib
import json
from typing import Any, Dict, Optional, Tuple

from .script_writer import _base_receipt

# 同一 (project, message_id, resource, basis) 连续 needs_fix 达此阈值 → 拦截。
REVIEW_RETRY_LIMIT = 3

REVIEW_RETRY_STATE: Dict[Tuple[str, str, str, str], int] = {}

# 拦截回执的通用改输入提示（Resource/Scene 域共用；按域读得通的超集）。
_DEFAULT_CHANGE_HINTS = (
    "①修改语义输入（描述/appearance/camera_zone/look_direction/visible anchors/"
    "SceneState 等任一真实变化）；②更换 reference/参考图；③解决 prompt-reference "
    "冲突（必要时向用户说明并请其选择）；④或停止并如实向用户报告模型边界"
    "（MODEL_LIMITATION）/blocker。")


def reset_review_retry_state() -> None:
    """测试/新 turn 边界清理（进程内存态，无持久化）。"""
    REVIEW_RETRY_STATE.clear()


def review_basis_sig(**inputs: Any) -> str:
    """语义 basis 指纹（sha8）：决定「同一语义输入」的判定。任何有效输入变化 →
    新 basis → 计数归零（27.md §3.1）。"""
    payload = json.dumps({k: v for k, v in sorted(inputs.items())},
                         ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def review_retry_check(project: str, message_id: Optional[str],
                       resource_key: str, basis_sig: str, op: str, *,
                       change_hints: str = _DEFAULT_CHANGE_HINTS) -> Optional[dict]:
    """入口检查：同 basis 已连续 needs_fix ≥ 3 → 返回 review_retry_exhausted 回执
    （不再 generate/review）。否则 None = 放行。"""
    key = (project, message_id or "", resource_key, basis_sig)
    if REVIEW_RETRY_STATE.get(key, 0) >= REVIEW_RETRY_LIMIT:
        return _base_receipt(
            op, ok=False, target=resource_key, before=None, after=None,
            revision=None, verified=False, error="review_retry_exhausted",
            stale=False, resource_key=resource_key, changed_resources=[],
            op_summary="审查重试已收敛（同一输入连续 needs_fix）",
            note=(f"同一资源（{resource_key}）在【相同语义输入】下已连续 "
                  f"{REVIEW_RETRY_LIMIT} 次审查 needs_fix——继续原样 regenerate 不会成功"
                  "（不是随机失败，是当前约束下的系统性结果）。必须先改变输入之一：\n"
                  + change_hints)
            )
    return None


def review_retry_record(project: str, message_id: Optional[str],
                        resource_key: str, basis_sig: str,
                        review_overall: Optional[str]) -> None:
    """审查结果记账：needs_fix → 连续计数 +1；pass（成功）→ 清零；
   其它（uncertain/provider_error 等）→ 不计入连续 needs_fix，但也保留原计数。"""
    key = (project, message_id or "", resource_key, basis_sig)
    if review_overall == "pass":
        REVIEW_RETRY_STATE.pop(key, None)
        return
    if review_overall == "needs_fix":
        if len(REVIEW_RETRY_STATE) > 4096:   # 防泄漏：超限清最旧（dict 保插入序）
            for k in list(REVIEW_RETRY_STATE.keys())[:1024]:
                REVIEW_RETRY_STATE.pop(k, None)
        REVIEW_RETRY_STATE[key] = REVIEW_RETRY_STATE.get(key, 0) + 1
