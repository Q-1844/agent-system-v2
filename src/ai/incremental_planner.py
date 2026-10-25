# src/ai/incremental_planner.py
"""增量工作计划（PR C1）：从项目快照确定性导出补齐/继续清单。

设计依据：执行指令 v2 §6（继续项目：明确已存在能力，拒绝新造轮子）与
GPT 契约对 C1 的定义——resume_run / complete_project / add_assets 的执行、
output_kind=asset_set 的独立验收（不强迫 final.mp4 时长门槛）。

纪律：
- 纯数据 + 确定性推导：零 LLM、零网络、零写盘（快照与项目由调用方传入/读取）；
- reuse 证据红线（T15）：被标记复用的素材必须带可核验来源
 （shot_id + version_id + local/url 指针），不接受自然语言"已存在"；
- 补齐范围 = 项目全集（含未提及的缺失）：单场景过滤属 C2 细化；
  场景提及仍被解析并记录，供验收聚焦与审计。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

WORKPLAN_SCHEMA = "incremental-workplan-v1"

_SCENE_MENTION_RE = re.compile(r"\bs(\d{1,2})\b")


def parse_scene_targets(request_text: str) -> List[str]:
    """从任务文本确定性解析被点名的场景（如「把深海项目 s02 后面的素材补齐」）。"""
    return [f"s{m:0>2}" if len(m) < 2 else f"s{m}"
            for m in _SCENE_MENTION_RE.findall(request_text or "")]


def _shot_valid_video(shot) -> Optional[dict]:
    """镜头是否已有有效成片。返回复用证据（dict）或 None。

    有效 = final_video 指针指向一个存在的 version，且该 version 带
    local 文件路径或 url（可核验来源）。显式 None 指针 = 不得推断复活。
    """
    fid = getattr(shot, "final_video", None)
    if not fid:
        return None
    for v in (getattr(shot, "video_versions", None) or []):
        if v.version_id == fid:
            if getattr(v, "local", None) or getattr(v, "url", None):
                return {"shot_id": shot.shot_id, "version_id": fid,
                        "local": v.local, "video_id": v.video_id,
                        "seconds": v.seconds_returned}
            return None  # 指针在但版本无实体 → 无效
    return None


def build_incremental_workplan(snapshot, request_text: str, intent: str,
                                project_dir: Path = None) -> Dict[str, Any]:
    """项目快照 → 增量工作计划（确定性，无 LLM）。

    snapshot：ProjectProductionInspect.snapshot() 的 ProjectSnapshot；
    project_dir 可选（透传给 evidence 校验，保持纯函数性：仅用于存在性检查）。
    结构（即 stage0 冻结蓝图，plan_fingerprint 对它计算）：
      - scene_targets：需要生产/补齐的场景 id（state != READY，即 NEED_* 或 BLOCKED
        之外的可补状态；全 READY = 空清单，仅可能需 compose）
      - generate_missing：缺成片的镜头清单（shot 级 target）
      - reuse_assets：已有效素材（带可核验证据，T15）
      - mentioned_scenes：文本点名的场景（审计记录）
    """
    scenes = list(getattr(snapshot, "scenes", []) or [])
    scene_targets: List[str] = []
    generate_missing: List[Dict[str, str]] = []
    reuse_assets: List[Dict[str, Any]] = []
    # shot 级明细需要真实项目模型——由调用方通过 project_dir 提供
    scene_needs_work: Dict[str, bool] = {}
    if project_dir is not None:
        from src.core.store import ProjectStore
        project = ProjectStore(Path(project_dir)).load()
        for scene in project.scenes:
            needs = not scene.shots  # 无镜头 → 需要 Director 规划
            for shot in scene.shots:
                ev = _shot_valid_video(shot)
                if ev is not None:
                    reuse_assets.append({"action_kind": "reuse_asset",
                                         "target": shot.shot_id,
                                         "evidence": ev})
                else:
                    generate_missing.append({"action_kind": "generate_missing",
                                             "target": shot.shot_id})
                    needs = True  # 有镜缺成片 → 需要生产
            scene_needs_work[scene.scene_id] = needs
    for sc in scenes:
        # 工作判据 = 实际缺口（无镜头/缺成片），不是 plan 记录状态：
        # 成片齐全但缺 plan 记录的场景不需要再生产（READY 与否是旧链路的
        # 准备分类，不等于资产缺口）。
        if scene_needs_work.get(sc.scene_id, sc.state != "READY"):
            scene_targets.append(sc.scene_id)
    output_kind = "asset_set" if intent == "add_assets" else "final_video"
    return {
        "schema_version": WORKPLAN_SCHEMA,
        "kind": "incremental-workplan",
        "intent": intent,
        "project_name": getattr(snapshot, "project_name", ""),
        "output_kind": output_kind,
        "mentioned_scenes": parse_scene_targets(request_text),
        "scene_targets": scene_targets,
        "generate_missing": generate_missing,
        "reuse_assets": reuse_assets,
        "request_text": request_text,
    }


def validate_asset_set(project_dir: Path) -> Dict[str, Any]:
    """asset_set 验收（T16）：项目内每个镜头都必须有有效成片。

    与 final_video 验收的区别：无时长门槛（用户没有时长承诺），
    只验证资产完备性。返回 {complete, missing, reused_count}。
    """
    from src.core.store import ProjectStore
    project = ProjectStore(Path(project_dir)).load()
    missing: List[str] = []
    reused = 0
    for scene in project.scenes:
        for shot in scene.shots:
            if _shot_valid_video(shot) is not None:
                reused += 1
            else:
                missing.append(shot.shot_id)
    return {"complete": not missing, "missing": missing,
            "reused_count": reused,
            "shot_count": sum(len(s.shots) for s in project.scenes)}


def continuation_media_profile(project) -> Dict[str, Any]:
    """既有项目现状 → 生产段动态 profile（与固定档 FILM_PROFILES 同字段集）。

    确定性规则：
    - planned = 既有全部镜头的 target_duration_s 之和
    - 无镜头场景（Director 将现场规划）按 sidecar 预算窗口（8-60s）计入
      planned_min/max 与 shot 窗口；
    - video budget 只需覆盖**新增工作量**（缺片镜头时长 + 无镜头场景上限），
      已完成素材不重复计费（复用即免费）；
    - min_final（final_video 类）= floor(0.9 × planned_min)：成片达到既有
      规划的九成即达标（与固定档 200→180 同比例）；asset_set 类不使用该门槛。
    """
    import math
    shots_total = 0
    planned = 0.0
    unshot_scenes = 0
    missing_seconds = 0.0
    for scene in project.scenes:
        shots_total += len(scene.shots)
        for shot in scene.shots:
            planned += float(shot.target_duration_s or 0)
            if _shot_valid_video(shot) is None:
                missing_seconds += float(shot.target_duration_s or 0)
        if not scene.shots:
            unshot_scenes += 1
    planned_min = int(math.floor(planned)) + 8 * unshot_scenes
    planned_max = int(math.ceil(planned)) + 60 * unshot_scenes
    budget = max(60, int(math.ceil(missing_seconds)) + 60 * unshot_scenes)
    return {
        "scope": "full-project-continuation-v1",
        "scene_count": len(project.scenes),
        "shot_min": shots_total + 2 * unshot_scenes,
        "shot_max": shots_total + 6 * unshot_scenes,
        "planned_min": planned_min,
        "planned_max": planned_max,
        "min_final": float(math.floor(0.9 * planned_min)),
        "budget": budget,
        "image_budget": 60,
        "text_budget": 80,
    }


def continuation_sidecar_budgets(project) -> List[Dict[str, Any]]:
    """无镜头场景的默认 sidecar 预算（与 continuation_media_profile 窗口一致）。"""
    out = []
    for scene in project.scenes:
        if not scene.shots:
            out.append({"scene_id": scene.scene_id, "min_s": 8, "max_s": 60,
                        "shot_min": 2, "shot_max": 6,
                        "per_shot_min_s": 4, "per_shot_max_s": 11,
                        "target_s": None})
    return out
