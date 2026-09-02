# src/agents/subagent/fingerprints.py
# per-scene 派生指纹（Part 2 §4.2 正式定义）。
#
# scene_slice_fingerprint = sha256[:8] of json(sort_keys, {
#     content: SCENE_UPDATABLE_FIELDS 五字段（= SW 侧 _scene_token 同域，Errata E5），
#     shots:   [[shot_id, _shot_token(shot)] 有序]（_shot_token 覆盖 ~30 可写字段 +
#              source_shot_id —— 接力变更天然改变指纹，commit 0f106c6）})
#
# 三性质：确定性（同 canonical 同值）/ 可从 canonical 现算（读时派生，零 canonical
# schema 变更）/ 失效方向保守（任何内容或接力变更都改变指纹——宁可错杀）。
#
# 循环依赖规避：本模块对 director/script_writer 的 import 一律函数级懒 import
# （director 挂载时才 import 本包；若本包在模块级 import director 会成环）。

from __future__ import annotations

import hashlib
import json

from src.core.models import Scene, Shot

# 段内容指纹域 = SW 侧 story.update 白名单（SCENE_UPDATABLE_FIELDS），在此显式声明
# 避免 import script_writer（防环）；两处定义若漂移，tests/test_subagent_fingerprints.py
# 的一致性断言会捕获。
SCENE_CONTENT_FIELDS = ("title", "location", "time_of_day", "summary", "characters")


def _shot_token(shot: Shot) -> str:
    """单镜内容指纹（复用 director 侧实现——OCC 同源，防两套口径漂移）。"""
    from src.agents.director import _shot_token as _impl
    return _impl(shot)


def scene_content_token(scene: Scene) -> str:
    """段内容指纹（= SW 侧 _scene_token 同码形；E5 复用关系）。
    title/location/time_of_day/summary/characters 五字段；shots 不影响。
    None（段不存在）时与 SW 侧 _scene_token(None) 同值（sha256(b"")）。"""
    if scene is None:
        return hashlib.sha256(b"").hexdigest()[:8]
    payload = json.dumps(
        {k: (list(getattr(scene, k, []) or []) if k == "characters"
             else (getattr(scene, k, "") or ""))
         for k in SCENE_CONTENT_FIELDS},
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def scene_slice_fingerprint(scene: Scene) -> str:
    """per-scene 派生指纹（Part 2 §4.2）：段内容 + 有序镜头（id + 单镜内容指纹，
    含接力边）。decision memory 的 basis fingerprint 与 proposal 的 base fingerprint
    共用本函数。scene 为 None（不存在）时返回空串。"""
    if scene is None:
        return ""
    payload = json.dumps({
        "content": {k: (list(getattr(scene, k, []) or []) if k == "characters"
                        else (getattr(scene, k, "") or ""))
                    for k in SCENE_CONTENT_FIELDS},
        "shots": [[sh.shot_id, _shot_token(sh)] for sh in (scene.shots or [])],
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def shots_structure_token(scenes) -> str:
    """全项目镜头结构指纹（= _shots_token 同源；basis 三元组的结构组件，防段集变动）。"""
    from src.agents.director import _shots_token as _impl
    return _impl(scenes)


def compute_scene_basis(scene, scenes) -> dict:
    """basis 三元组（Part 2 §2.2）：dispatch 时刻从 canonical 现算。
    - scene_slice_fingerprint：防语义 lost update 的主锚（submit/commit 双时点比对）
    - scene_content_token：SW 侧 story.read 返回的 revision 同源（submit 防伪用）
    - shots_structure_token：shot.list 返回的 revision 同源（submit 防伪用）"""
    return {
        "scene_slice_fingerprint": scene_slice_fingerprint(scene),
        "scene_content_token": scene_content_token(scene),
        "shots_structure_token": shots_structure_token(scenes),
    }
