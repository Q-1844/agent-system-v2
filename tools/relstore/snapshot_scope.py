# tools/relstore/snapshot_scope.py
# G4-A §5：整树快照范围审查。
# 纪律：默认纳入一切文件；仅一个极窄的"明确临时"白名单可不计入，
# 且每条都标注理由 + 是否可能丢失源数据；调用方可收紧不可放宽。
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 极窄临时白名单（不计入快照指纹；G4-A 保守，仅以下三类）
# — 这些是 OS/工具运行时产物，不构成项目制作数据，丢失可重建
TEMP_EXCLUDABLE_PATTERNS = [
    re.compile(r"(^|/)\.DS_Store$", re.I),
    re.compile(r"(^|/)Thumbs\.db$", re.I),
    re.compile(r"(^|/)__pycache__/", re.I),
    re.compile(r"(^|/)\.pytest_cache/", re.I),
    re.compile(r"(^|/)\.vite/", re.I),
    re.compile(r"(^|/)node_modules/", re.I),  # 可重装，非源数据
]

# 明确必须计入的关键文件（防止误排除）
MUST_INCLUDE_PATTERNS = [
    re.compile(r"^input/.*", re.I),
    re.compile(r"^output/project\.json$", re.I),
    re.compile(r"^output/resources\.json$", re.I),
    re.compile(r"^output/scene_system\.json$", re.I),
    re.compile(r"^output/sequences\.json$", re.I),
    re.compile(r"^output/shot_production_bindings\.json$", re.I),
    re.compile(r"^output/project_profile\.json$", re.I),
    re.compile(r"^output/videos/.*\.mp4$", re.I),
    re.compile(r"^output/frames/.*\.(png|jpg)$", re.I),
    re.compile(r"^output/agents/skills/trace\.jsonl$", re.I),
    re.compile(r"^output/agents/.*\.jsonl$", re.I),  # 审计流
    re.compile(r"^output/agents/master_lease\.json$", re.I),
    re.compile(r"^output/agents/grants\.json$", re.I),
    re.compile(r"^output/resources/.*", re.I),
    re.compile(r"^output/scene_system/.*", re.I),
    re.compile(r"^output/characters/.*", re.I),
    re.compile(r"^output/sequences/.*", re.I),
    re.compile(r"^cache/checkpoints/.*", re.I),  # 生成检查点
]


@dataclass
class ScopeDecision:
    included: list = field(default_factory=list)
    excluded_temp: list = field(default_factory=list)
    must_include_flagged: list = field(default_factory=list)  # 命中 MUST_INCLUDE 的（防止误排除）
    unknown: list = field(default_factory=list)  # 不在任何清单的文件（默认 included）


def classify(paths) -> ScopeDecision:
    """对一组 posix 相对路径分类。
    默认 include；仅 TEMP_EXCLUDABLE 排除；MUST_INCLUDE 标注防误删；
    未知文件 include（保守）。"""
    dec = ScopeDecision()
    for p in paths:
        norm = p.replace("\\", "/")
        is_temp = any(rx.search(norm) for rx in TEMP_EXCLUDABLE_PATTERNS)
        is_must = any(rx.search(norm) for rx in MUST_INCLUDE_PATTERNS)
        if is_temp and not is_must:
            dec.excluded_temp.append(norm)
        elif is_must:
            dec.must_include_flagged.append(norm)
            dec.included.append(norm)
        else:
            dec.included.append(norm)
            if not is_temp:
                dec.unknown.append(norm)
    return dec


def audit_exclusions(dec: ScopeDecision) -> list:
    """返回每条排除的理由 + 是否可能丢失源数据。"""
    notes = []
    for p in dec.excluded_temp:
        if "__pycache__" in p or ".pytest_cache" in p:
            notes.append((p, "Python 缓存，可重建", "不丢失源数据"))
        elif "node_modules" in p or ".vite" in p:
            notes.append((p, "可重装依赖", "不丢失源数据"))
        elif ".DS_Store" in p or "Thumbs.db" in p:
            notes.append((p, "OS 元数据", "不丢失源数据"))
        else:
            notes.append((p, "临时", "需复核"))
    return notes
