# src/core/link_mode.py
# 三种衔接模式自动判定（PHASE_3_VIDEO_PIPELINE §3.1）。
#   keyframes = 同场景连续动作（首帧+尾帧，服务端插值）
#   i2v       = 跨场景硬切（单图动画化，默认）
#   none      = 全片共用一张参考图，各镜独立
#
# 🔴 跨场景绝对不要强行接力：用上一场尾帧接下一场首帧会产生诡异融化过渡，
#    观感比硬切差得多。接力只适用于同场景的动作延续。
# split-dialogue 拆出的新镜头不走本函数（硬编码 keyframes，见 PHASE_3_EXECUTION_PLAN §8.1）。
from __future__ import annotations
from typing import Optional
from .models import Shot, LinkMode


def auto_link_mode(prev: Optional[Shot], cur: Shot) -> str:
    """自动推荐衔接模式。用户可在前端手动覆盖。

    - 首镜（无上文）-> i2v
    - 跨场景       -> i2v（🔴 跨场景必须硬切）
    - 同场景 + 角色集合有交集 -> keyframes（连续动作）
    - 其余          -> i2v
    """
    if prev is None:
        return LinkMode.I2V.value
    if prev.scene_id != cur.scene_id:
        return LinkMode.I2V.value
    if set(prev.characters) & set(cur.characters):
        return LinkMode.KEYFRAMES.value
    return LinkMode.I2V.value
