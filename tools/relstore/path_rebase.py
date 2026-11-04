# tools/relstore/path_rebase.py
# 恢复改名时的项目路径重定基（G1 实测：P55 包内 93 处 JSON 值嵌入 projects\<alias>\）。
# 铁律：
# - 只改白名单 canonical JSON 的【结构化字符串值】，不做全文件文本替换；
# - JSONL 审计流/二进制/媒体永不改写（审计不可变）；
# - apply 只允许作用于新的隔离恢复副本；dry-run 默认且无副作用；
# - 原子写回（同目录临时文件+os.replace）。
from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .errors import IllegalRename
from .safe_tar import WINDOWS_RESERVED

# 允许重写的 canonical JSON（相对项目根，posix glob-lite 精确模式）
CANONICAL_JSON_WHITELIST = {
    "output/project.json",
    "output/resources.json",
    "output/scene_system.json",
    "output/sequences.json",
    "output/shot_production_bindings.json",
    "output/project_profile.json",
}
CANONICAL_JSON_PREFIXES = (
    "output/assembly/",
    "output/sequences/",
)
CANONICAL_JSON_SUFFIXES = (".json",)
# 永不重写
NEVER_REWRITE_SUFFIXES = (".jsonl", ".mp4", ".png", ".jpg", ".jpeg", ".wav",
                          ".mp3", ".srt", ".webm", ".lock")
NAME_RE = re.compile(r"^[A-Za-z0-9_\-一-鿿][A-Za-z0-9_\-一-鿿 ]*$")


def validate_new_alias(name: str, dest_parent: Path | None = None) -> None:
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise IllegalRename(f"非法项目名: {name!r}")
    if not NAME_RE.match(name):
        raise IllegalRename(f"项目名含非法字符: {name!r}")
    stem = name.split(".")[0].upper()
    if stem in WINDOWS_RESERVED:
        raise IllegalRename(f"项目名命中 Windows 保留名: {name!r}")
    if dest_parent is not None and (Path(dest_parent) / name).exists():
        raise IllegalRename(f"目标目录已存在: {name}")


def is_canonical_json(rel_posix: str) -> bool:
    if rel_posix in CANONICAL_JSON_WHITELIST:
        return True
    if rel_posix.startswith(CANONICAL_JSON_PREFIXES) and rel_posix.endswith(".json"):
        return True
    return False


@dataclass
class RebaseReport:
    old_alias: str
    new_alias: str
    files_scanned: int = 0
    files_changed: list = field(default_factory=list)
    replacements: int = 0
    by_file: dict = field(default_factory=dict)
    unknown_refs: list = field(default_factory=list)  # 非白名单文件里出现的前缀
    jsonl_untouched: list = field(default_factory=list)
    binaries_untouched: int = 0

    def to_dict(self):
        return {
            "old_alias": self.old_alias, "new_alias": self.new_alias,
            "files_scanned": self.files_scanned,
            "files_changed": self.files_changed,
            "replacements": self.replacements,
            "by_file": self.by_file,
            "unknown_refs": self.unknown_refs,
            "jsonl_untouched": self.jsonl_untouched,
            "binaries_untouched": self.binaries_untouched,
        }


def _prefixes(old: str):
    return (f"projects/{old}/", f"projects\\{old}\\")


def _rebase_string(s: str, old: str, new: str) -> tuple[str, int]:
    n = 0
    for a, b in ((f"projects/{old}/", f"projects/{new}/"),
                 (f"projects\\{old}\\", f"projects\\{new}\\")):
        cnt = s.count(a)
        if cnt:
            s = s.replace(a, b)
            n += cnt
    return s, n


def _walk(obj, old, new, counter):
    if isinstance(obj, str):
        ns, n = _rebase_string(obj, old, new)
        counter[0] += n
        return ns
    if isinstance(obj, dict):
        return {k: _walk(v, old, new, counter) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v, old, new, counter) for v in obj]
    return obj


def scan(project_dir: Path, old_alias: str, new_alias: str | None = None) -> RebaseReport:
    """只读扫描：统计白名单 canonical JSON 内嵌入路径引用；并报告未知引用（不改）。"""
    project_dir = Path(project_dir)
    new_alias = new_alias or old_alias
    rep = RebaseReport(old_alias=old_alias, new_alias=new_alias)
    pfx = _prefixes(old_alias)
    for p in sorted(project_dir.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel = unicodedata.normalize("NFC", p.relative_to(project_dir).as_posix())
        rep.files_scanned += 1
        if rel.endswith(NEVER_REWRITE_SUFFIXES):
            if rel.endswith(".jsonl"):
                rep.jsonl_untouched.append(rel)
            else:
                rep.binaries_untouched += 1
            continue
        if not rel.endswith(CANONICAL_JSON_SUFFIXES):
            # 非 JSON 文本：只探测，不修改（同时识别 JSON 转义的双反斜杠形态）
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
                probes = pfx + (f"projects\\\\{old_alias}\\\\",)
                if any(x in txt for x in probes):
                    rep.unknown_refs.append(rel)
            except Exception:
                pass
            continue
        if not is_canonical_json(rel):
            # 其它 JSON（非 canonical 白名单）：解析后探测报告但不改
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
                hits = [0]
                _walk(doc, old_alias, old_alias, hits)
                if hits[0]:
                    rep.unknown_refs.append(rel)
            except Exception:
                # 不可解析 JSON：在原始文本探测两种斜杠（含 JSON 转义形态）
                txt = p.read_text(encoding="utf-8", errors="ignore")
                esc = (f"projects/{old_alias}/", f"projects/{old_alias}/",
                       f"projects\\\\{old_alias}\\\\")
                if any(x in txt for x in (pfx + esc)):
                    rep.unknown_refs.append(f"{rel}#unparseable")
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            rep.unknown_refs.append(f"{rel}#unparseable")
            continue
        counter = [0]
        _walk(doc, old_alias, new_alias, counter)
        if counter[0]:
            rep.by_file[rel] = counter[0]
            rep.replacements += counter[0]
    return rep


def apply_rebase(project_dir: Path, old_alias: str, new_alias: str, *,
                 require_target_absent: bool = True) -> RebaseReport:
    """在隔离副本上执行重定基：只重写白名单 canonical JSON，原子写回。"""
    project_dir = Path(project_dir)
    if new_alias == old_alias:
        return RebaseReport(old_alias=old_alias, new_alias=new_alias)  # 同名 no-op
    validate_new_alias(new_alias,
                       project_dir.parent if require_target_absent else None)
    rep = scan(project_dir, old_alias, new_alias)
    for rel in rep.by_file:
        p = project_dir / rel
        doc = json.loads(p.read_text(encoding="utf-8"))
        counter = [0]
        doc2 = _walk(doc, old_alias, new_alias, counter)
        data = (json.dumps(doc2, ensure_ascii=False, sort_keys=True, indent=1)
                + "\n").encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as w:
                w.write(data)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        rep.files_changed.append(rel)
    return rep


def extract_embedded_paths(project_dir: Path, alias: str) -> list:
    """提取白名单 canonical JSON 字符串值内 projects/<alias>/ 引用，
    返回 [(项目内相对 posix 路径, 树内是否存在)]（去重）。用于恢复引用完整性检查。"""
    project_dir = Path(project_dir)
    found = set()

    def walk(obj):
        if isinstance(obj, str):
            norm = obj.replace("\\", "/")
            marker = f"projects/{alias}/"
            if marker in norm:
                tail = norm.split(marker, 1)[1].split()[0].strip("\"',)}]#?")
                if tail:
                    found.add(tail)
        elif isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    for p in project_dir.rglob("*.json"):
        if not p.is_file():
            continue
        rel = unicodedata.normalize("NFC", p.relative_to(project_dir).as_posix())
        if not is_canonical_json(rel):
            continue
        try:
            walk(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return [(t, (project_dir / t).exists()) for t in sorted(found)]
