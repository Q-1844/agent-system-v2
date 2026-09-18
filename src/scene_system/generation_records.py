# src/scene_system/generation_records.py
# Scene System V1 — Phase S2A §20/§24：generation / review / approval provenance 持久化。
#
# 位置：projects/{project}/output/scene_system/{generation_records|review_records|
# approval_records}.jsonl —— append-only JSONL（模式与 resources/generation_records.py
# 一致；Scene System 侧自含，不与 Resources 流混写）。
#
# 🔴 绝不保存 base64（§20/§24）。候选图在磁盘 generation 目录，record 只记相对路径/URL。
# 🔴 review 不改 canonical；record 只是证据流。
from __future__ import annotations
import json
from pathlib import Path
from typing import List, Optional


def generation_records_path(project_dir) -> Path:
    return Path(project_dir) / "output" / "scene_system" / "generation_records.jsonl"


def review_records_path(project_dir) -> Path:
    """Phase S2A §24：视觉审查证据流（append-only，与 generation 流独立）。"""
    return Path(project_dir) / "output" / "scene_system" / "review_records.jsonl"


def approval_records_path(project_dir) -> Path:
    """Phase S2A §19/§25：显式审批 / revoke / orphaned asset 事件流（append-only）。"""
    return Path(project_dir) / "output" / "scene_system" / "approval_records.jsonl"


def append_record(project_dir, record: dict, *, path: Optional[Path] = None) -> None:
    """追加一条 record（append-only；JSON 行，UTF-8，ensure_ascii=False）。
    不做文件锁：写入发生在临界区/单调用方上下文内（由调用方保证），单行 append。"""
    p = path or generation_records_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=str)
    with p.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_records(project_dir, *, view_id: Optional[str] = None,
                 location_id: Optional[str] = None,
                 generation_id: Optional[str] = None,
                 scene_instance_view_id: Optional[str] = None,
                 limit: Optional[int] = None,
                 path: Optional[Path] = None) -> List[dict]:
    """读取 records（append 顺序）。容错尾部损坏行（append 半行崩溃 → 跳过）。
    view_id/location_id/generation_id/scene_instance_view_id 过滤；limit 取最近 N 条。"""
    p = path or generation_records_path(project_dir)
    if not p.exists():
        return []
    out: List[dict] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue   # 尾部半行/损坏行容错（append-only 语义下唯一合理选择）
            if view_id is not None and rec.get("view_id") != view_id:
                continue
            if location_id is not None and rec.get("location_id") != location_id:
                continue
            if generation_id is not None and rec.get("generation_id") != generation_id:
                continue
            if scene_instance_view_id is not None \
                    and rec.get("scene_instance_view_id") != scene_instance_view_id:
                continue
            out.append(rec)
    if limit is not None and limit >= 0:
        out = out[-limit:]
    return out
