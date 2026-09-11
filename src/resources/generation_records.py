# src/resources/generation_records.py
# Production Resources V1 — Phase 1C1 Work C：generation provenance 持久化（§18）。
#
# 位置：projects/{project}/output/resources/generation_records.jsonl —— append-only JSONL。
# Phase 1B 的 ReferenceAsset 故意不带 generation receipt（最小化）；真实生成开始后，
# provenance 落在这个独立 append-only 流水里，不回填进 ReferenceAsset，也绝不写 base64。
#
# 每条至少（§18）：generation_id / operation / look_id / basis / prompt / model / seed /
#   candidate_asset_ids / started_at / completed_at / status / error / verified /
#   resource_document_revision_after。Agnes 返回的 provider 输出 id（url）记录在
#   candidate_files/candidate_urls（诊断用，不含 base64）。
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional


def records_path(project_dir) -> Path:
    return Path(project_dir) / "output" / "resources" / "generation_records.jsonl"


def review_records_path(project_dir) -> Path:
    """Phase 1C2：视觉审查证据流（与 generation_records 对称、相互独立）。"""
    return Path(project_dir) / "output" / "resources" / "review_records.jsonl"


def append_record(project_dir, record: dict, *, path: Optional[Path] = None) -> None:
    """追加一条 record（append-only；JSON 行，UTF-8，ensure_ascii=False）。
    不做文件锁：写入发生在临界区/单调用方上下文内（由调用方保证），单行 append。
    path 缺省=generation_records.jsonl；review 流传 review_records_path()。"""
    p = path or records_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=str)
    with p.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_records(project_dir, *, look_id: Optional[str] = None,
                 generation_id: Optional[str] = None,
                 prop_id: Optional[str] = None,
                 composition_id: Optional[str] = None,
                 limit: Optional[int] = None,
                 path: Optional[Path] = None) -> List[dict]:
    """读取 records（append 顺序）。容错尾部损坏行（append 半行崩溃 → 跳过，
    不让诊断数据读不出来）。look_id/generation_id/prop_id/composition_id 过滤；
    limit 取最近 N 条。Phase 1C5：prop/composition 过滤供 prop_review 与
    composition 生成/审查证据流共用同一 jsonl。"""
    p = path or records_path(project_dir)
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
            if look_id is not None and rec.get("look_id") != look_id:
                continue
            if generation_id is not None and rec.get("generation_id") != generation_id:
                continue
            if prop_id is not None and rec.get("prop_id") != prop_id:
                continue
            if composition_id is not None and rec.get("composition_id") != composition_id:
                continue
            out.append(rec)
    if limit is not None and limit >= 0:
        out = out[-limit:]
    return out
