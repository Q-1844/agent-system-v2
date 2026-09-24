# src/experiments/calibration_trace.py
# Phase 37A（37.md §2/§3/§12/§13）：Calibration Trace——「尾帧接力 + 自适应校准」
# 实验记录层。每个实际生成的 Shot 一条 link 记录，因果链（为什么改下一镜）
# 可以后续 replay（37C Agnes Agent Shadow Replay 的输入）。
#
# 定位（37.md §2/§16）：
#   - 非 canonical：不进 ProjectStore / project.json；落
#     <project_dir>/output/experiments/calibration_runs/<run_id>/{run.json,links/*.json}，
#     随项目目录归档；不制造第二套 production canonical。
#   - 正式 backend/service contract：本模块即服务层；REST 契约在
#     routers/experiments.py（实验编排可用脚本，记录能力不依赖 one-off script）。
#   - MAE 不负责 narrative deviation（§3）：link 记录同时保存结构化 Vision
#     assessment（证据/建议）与人工裁定（none/minor/major）及二者差异。
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.atomic import atomic_write_json


class CalibrationTraceError(Exception):
    """结构化失败：run/link 不存在、schema 非法。调用方转 4xx，不伪装 5xx。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


# link 记录字段（37.md §2 建议字段的落地形；自由 dict 兼许，白名单键严格校验类型）
LINK_ENUMS = {
    "human_deviation_level": {"none", "minor", "major"},
    "human_action": {"continue", "add_transition", "insert_story_beat",
                     "retry_generation", "break_chain_and_reanchor", "other"},
    "retry_or_adapt": {"retry", "adapt"},
}
RUN_STATUSES = {"active", "completed"}


def _runs_dir(project_dir: Path) -> Path:
    return Path(project_dir) / "output" / "experiments" / "calibration_runs"


def _validate_link_payload(payload: dict) -> dict:
    """白名单枚举校验；其余键透传（schema 以实际数据演进，不做过度封闭）。"""
    for key, allowed in LINK_ENUMS.items():
        val = payload.get(key)
        if val is not None and val not in allowed:
            raise CalibrationTraceError(
                "invalid", f"{key} 必须 ∈ {sorted(allowed)}，得到 {val!r}")
    return payload


def new_run_id() -> str:
    return "cal_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]


def create_run(project_dir: Path, *, story_id: str, title: str = "",
               story_plan: Optional[List[dict]] = None,
               chain_root_reference_id: str = "",
               notes: str = "") -> dict:
    """创建一次校准实验 run（故事冻结声明入档：37.md §4 生成开始前冻结）。"""
    project_dir = Path(project_dir)
    run = {
        "run_id": new_run_id(),
        "story_id": story_id,
        "title": title,
        "created_at": time.time(),
        "updated_at": time.time(),
        "story_frozen": True,
        "chain_root_reference_id": chain_root_reference_id,
        "story_plan": list(story_plan or []),
        "status": "active",
        "notes": notes,
    }
    rdir = _runs_dir(project_dir) / run["run_id"]
    (rdir / "links").mkdir(parents=True, exist_ok=True)
    atomic_write_json(rdir / "run.json", run)
    return run


def list_runs(project_dir: Path) -> List[dict]:
    project_dir = Path(project_dir)
    root = _runs_dir(project_dir)
    if not root.exists():
        return []
    runs = []
    for d in sorted(root.iterdir()):
        rf = d / "run.json"
        if rf.exists():
            try:
                runs.append(json.loads(rf.read_text(encoding="utf-8")))
            except Exception:
                continue
    return runs


def get_run(project_dir: Path, run_id: str) -> dict:
    rf = _runs_dir(Path(project_dir)) / run_id / "run.json"
    if not rf.exists():
        raise CalibrationTraceError("not_found", f"run {run_id} 不存在")
    return json.loads(rf.read_text(encoding="utf-8"))


def update_run(project_dir: Path, run_id: str, patch: dict) -> dict:
    """run 级 patch（status/notes/story_plan 等；白名单枚举校验）。"""
    project_dir = Path(project_dir)
    run = get_run(project_dir, run_id)
    if patch.get("status") and patch["status"] not in RUN_STATUSES:
        raise CalibrationTraceError(
            "invalid", f"status 必须 ∈ {sorted(RUN_STATUSES)}")
    run.update(patch)
    run["updated_at"] = time.time()
    atomic_write_json(_runs_dir(project_dir) / run_id / "run.json", run)
    return run


def upsert_link(project_dir: Path, run_id: str, shot_id: str,
                record: dict) -> dict:
    """upsert 一条 link 记录（merge 语义：顶层键覆盖，dict 值深合并——
    vision_assessment / provider_timing 等可分次补齐而不丢字段）。"""
    project_dir = Path(project_dir)
    get_run(project_dir, run_id)   # 存在性校验
    if not shot_id or not str(shot_id).strip():
        raise CalibrationTraceError("invalid", "shot_id 不能为空")
    record = _validate_link_payload(dict(record or {}))
    ldir = _runs_dir(project_dir) / run_id / "links"
    ldir.mkdir(parents=True, exist_ok=True)
    lpath = ldir / f"{shot_id}.json"
    merged: Dict[str, Any] = {}
    if lpath.exists():
        try:
            merged = json.loads(lpath.read_text(encoding="utf-8"))
        except Exception:
            merged = {}
    for key, val in record.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key].update(val)
        else:
            merged[key] = val
    merged.setdefault("run_id", run_id)
    merged["story_id"] = get_run(project_dir, run_id)["story_id"]
    merged["shot_id"] = shot_id
    merged["updated_at"] = time.time()
    atomic_write_json(lpath, merged)
    return merged


def get_link(project_dir: Path, run_id: str, shot_id: str) -> dict:
    lpath = _runs_dir(Path(project_dir)) / run_id / "links" / f"{shot_id}.json"
    if not lpath.exists():
        raise CalibrationTraceError("not_found",
                                    f"link {run_id}/{shot_id} 不存在")
    return json.loads(lpath.read_text(encoding="utf-8"))


def list_links(project_dir: Path, run_id: str) -> List[dict]:
    ldir = _runs_dir(Path(project_dir)) / run_id / "links"
    if not ldir.exists():
        return []
    out = []
    for f in sorted(ldir.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out
