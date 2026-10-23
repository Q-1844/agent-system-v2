# tools/relstore/manifest.py
# G2：严格 Payload Manifest（包内）与 Verification Receipt（包外）协议。
#
# 循环依赖纪律（验收意见 P0）：
#   - manifest.payload.json 只记打包前可确认的事实；files[] 排除其自身；
#   - 禁止整包 sha、release/asset id、verified_at、最终状态等"未来字段"；
#   - tar 关闭后的整包哈希、远端 ID、验证结果只进【包外】verification_receipt.json；
#   - receipt 自身不哈希自身；其摘要由更外层（B1 索引/模拟索引）记录。
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

from .errors import (ForbiddenManifestField, ManifestError,
                     MissingRequiredDependency)

FULL_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def is_full_git_sha(s) -> bool:
    """生产追溯 ID 必须用 40 位完整 SHA；短写只允许出现在测试/文档中。"""
    return bool(isinstance(s, str) and FULL_GIT_SHA_RE.fullmatch(s))


PAYLOAD_MANIFEST_NAME = "manifest.payload.json"
RECEIPT_NAME = "verification_receipt.json"
SCHEMA_VERSION = 2
PAYLOAD_KIND = "project_archive_payload"
RECEIPT_KIND = "verification_receipt"
SIDECAR_KIND = "legacy_archive_sidecar"

# 包内 payload 永不得出现的字段（未来才知道/自引用）
FORBIDDEN_PAYLOAD_KEYS = {
    "archive_sha256", "packed_sha256", "tar_sha256", "package_sha256",
    "asset_id", "release_id", "asset_name_final", "verified_at",
    "archive_status", "remote_digest", "download_url", "receipt_sha256",
    "manifest_digest", "self_sha256",
}
FINAL_STATUS_VALUES = {"verified", "verified_restorable", "uploaded",
                       "verified_bytes"}

REQUIRED_TOP_KEYS = {
    "schema_version", "manifest_kind", "archive_operation_id",
    "project_id", "legacy_alias", "revision", "lifecycle_status",
    "source_code_commit", "created_at", "project", "files",
    "skill_refs", "tool_versions", "dependencies", "external_refs",
    "security_scan", "empty_dirs", "packaging",
}

ROLE_RULES = [
    (r"^output/project\.json$", "canonical_project"),
    (r"^output/resources\.json$", "canonical_resources"),
    (r"^output/scene_system\.json$", "canonical_scene_system"),
    (r"^output/sequences\.json$", "canonical_sequences"),
    (r"^output/shot_production_bindings\.json$", "canonical_bindings"),
    (r"^output/project_profile\.json$", "project_profile"),
    (r"^output/videos/.*\.mp4$", "shot_video"),
    (r"^output/sequences/.*\.mp4$", "sequence_video"),
    (r"^output/frames/.*\.(png|jpg|jpeg)$", "frame_image"),
    (r"^output/characters/.*", "character_asset"),
    (r"^output/resources/looks/.*", "look_asset"),
    (r"^output/scene_system/.*", "scene_asset"),
    (r"^output/agents/skills/trace\.jsonl$", "skill_trace"),
    (r"^output/agents/.*\.jsonl$", "audit_jsonl"),
    (r"^input/.*", "project_input"),
]
SECRET_NAME_PATTERNS = [
    re.compile(r"(^|[/\\])\.env$", re.I),
    re.compile(r"\.(pem|key|p12|pfx|keystore)$", re.I),
]


def canonical_dumps(obj) -> bytes:
    """规范序列化：sort_keys、无注释、ensure_ascii=False、紧凑分隔、LF 结尾。"""
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def human_dumps(obj) -> bytes:
    """落盘版（仍严格 JSON，禁止注释；indent=1 便于审阅）。"""
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1,
                       allow_nan=False) + "\n").encode("utf-8")


def strict_loads(raw: bytes | str):
    """严格 JSON：json.loads 天然拒绝 // 注释、尾逗号、NaN。"""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)  # noqa: 注释在 JSON 中本就非法


def classify_role(rel_posix: str) -> str:
    for pat, role in ROLE_RULES:
        if re.match(pat, rel_posix, re.I):
            return role
    return "other"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_snapshot_fingerprint(project_dir: Path,
                                 exclude: set | frozenset | None = None) -> str:
    """整份冻结快照内容指纹（G3-A 补 G2 缺口：不能只盯 project.json）。

    规范化：NFC 相对路径排序，逐行 ``sha256<两空格>bytes<两空格>posix_relpath``
    （与 sha256sum -t 同形），再对整体求 sha256。任何媒体/工具/JSON 变化都改变指纹。
    exclude 内的 posix 相对路径（如打包时才生成的 manifest）不计入。
    """
    project_dir = Path(project_dir)
    exclude = exclude or set()
    lines = []
    for p in sorted(project_dir.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel = unicodedata.normalize("NFC", p.relative_to(project_dir).as_posix())
        if rel in exclude:
            continue
        lines.append(f"{sha256_file(p)}  {p.stat().st_size}  {rel}\n")
    h = hashlib.sha256()
    for line in sorted(lines):
        h.update(line.encode("utf-8"))
    return h.hexdigest()


def scan_secret_filenames(rel_paths) -> list:
    hits = []
    for rel in rel_paths:
        norm = rel.replace("\\", "/")
        for rx in SECRET_NAME_PATTERNS:
            if rx.search(norm):
                hits.append(norm)
    return hits


# ---------------- skill / 依赖分析 ----------------

def parse_skill_trace(trace_path: Path) -> dict:
    """读 output/agents/skills/trace.jsonl：
    返回 {loaded: [{id, version}], load_fail: 次数, hinted: [...]}。"""
    out = {"loaded": [], "load_fail": 0, "hinted": []}
    if not trace_path.exists():
        return out
    seen = set()
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev = d.get("event")
        if ev == "load" and d.get("ok"):
            key = (d.get("skill_id"), d.get("version"))
            if key not in seen:
                seen.add(key)
                out["loaded"].append({"id": d.get("skill_id"),
                                      "version": d.get("version")})
        elif ev == "load_fail":
            out["load_fail"] += 1
        elif ev == "hint":
            for sid in (d.get("detail", {}) or {}).get("hinted", []):
                if sid not in out["hinted"]:
                    out["hinted"].append(sid)
    return out


def analyze_dependencies(project_dir: Path) -> dict:
    """依赖可靠性分析（只做事实记录，不触网）：
    - input/ 是否有文件
    - trace 记录的 skill：实体不在项目内 → resolved=False（必需依赖缺口）
    """
    project_dir = Path(project_dir)
    input_dir = project_dir / "input"
    input_files = []
    if input_dir.exists():
        input_files = [p.relative_to(project_dir).as_posix()
                       for p in input_dir.rglob("*") if p.is_file()]
    trace = parse_skill_trace(
        project_dir / "output/agents/skills/trace.jsonl")
    skill_refs = []
    missing = []
    for s in trace["loaded"]:
        sid, ver = s["id"], s["version"]
        # 项目内不含 skill 实体（skills/ 下只有 trace.jsonl）→ 不可变指针缺失
        skill_refs.append({
            "id": sid, "version": ver, "resolved": False,
            "resolution": "external_required",
            "note": "G2 不触网；正式新档须随包实体或经 sha256 校验的不可变指针",
        })
        missing.append(f"skill:{sid}@{ver}")
    deps = {
        "input_files": input_files,
        "input_present": bool(input_files),
        "skill_refs": skill_refs,
        "skill_load_fail_events": trace["load_fail"],
        "skill_hinted": trace["hinted"],
        "missing_required": [] if (input_files and not missing) else missing +
                            ([] if input_files else ["project_input:/input"]),
    }
    return deps


# ---------------- payload 构建/校验 ----------------

def build_payload(*, project_dir: Path, project_id: str, revision: int,
                  lifecycle_status: str, source_code_commit: str,
                  created_at: float, archive_operation_id: str,
                  tool_versions: dict, include_tree_files: list | None = None,
                  unfinished: list | None = None,
                  open_production_tasks: int = 0) -> tuple[dict, list]:
    """从冻结树构建 payload dict 与逐文件清单。返回 (payload, files_entries)。
    include_tree_files: [(rel_posix, abs_path)]；None=自动遍历（排除 manifest 自身）。
    """
    project_dir = Path(project_dir)
    legacy_alias = project_dir.name
    if include_tree_files is None:
        include_tree_files = []
        for p in sorted(project_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = unicodedata.normalize(
                "NFC", p.relative_to(project_dir).as_posix())
            if rel == PAYLOAD_MANIFEST_NAME:
                continue  # files[] 永远排除 manifest 自身
            include_tree_files.append((rel, p))
    files = []
    rel_seen = set()
    empty_dirs = []
    for d in sorted(project_dir.rglob("*")):
        if d.is_dir() and not any(d.iterdir()):
            rel = unicodedata.normalize(
                "NFC", d.relative_to(project_dir).as_posix())
            empty_dirs.append(rel)
    secret_hits = scan_secret_filenames([r for r, _ in include_tree_files])
    for rel, abs_p in include_tree_files:
        rel = unicodedata.normalize("NFC", rel.replace("\\", "/"))
        if rel == PAYLOAD_MANIFEST_NAME:
            raise ManifestError("文件清单不得包含 manifest 自身")
        if rel in rel_seen:
            raise ManifestError(f"重复文件条目: {rel}")
        rel_seen.add(rel)
        size = Path(abs_p).stat().st_size
        files.append({
            "path": rel, "bytes": size, "sha256": sha256_file(Path(abs_p)),
            "role": classify_role(rel),
            # 整包=完整快照；可剔除的冗余临时件在打包前就不应入树
            "required_for_restore": True,
        })
    deps = analyze_dependencies(project_dir)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "manifest_kind": PAYLOAD_KIND,
        "archive_operation_id": archive_operation_id,
        "project_id": project_id,
        "legacy_alias": legacy_alias,
        "snapshot_fingerprint": compute_snapshot_fingerprint(project_dir),
        "revision": revision,
        "lifecycle_status": lifecycle_status,
        "source_code_commit": source_code_commit,
        "created_at": created_at,
        "project": {
            "name": legacy_alias,
            "unfinished": unfinished or [],
            "open_production_tasks": open_production_tasks,
        },
        "files": sorted(files, key=lambda x: x["path"]),
        "skill_refs": deps["skill_refs"],
        "tool_versions": tool_versions,
        "dependencies": {
            "input_present": deps["input_present"],
            "input_files": deps["input_files"],
            "skill_load_fail_events": deps["skill_load_fail_events"],
            "missing_required": deps["missing_required"],
            "external_pointers": [],  # 正式新档：经 sha256 校验的不可变指针
        },
        "external_refs": {"signed_or_expiring_urls": None},  # 打包前扫描时由调用方填
        "security_scan": {
            "secret_filename_hits": secret_hits,
            "secret_pattern_hits": None,  # 调用方做内容扫描后填
            "signed_urls": None,
        },
        "empty_dirs": sorted(empty_dirs),
        "packaging": {"format": "tar-pax", "compression": "none"},
    }
    validate_payload(payload)
    return payload, files


def validate_payload(doc: dict, *, present_paths: set | None = None) -> None:
    if not isinstance(doc, dict):
        raise ManifestError("payload 必须是 JSON 对象")
    forbidden = sorted(set(doc) & FORBIDDEN_PAYLOAD_KEYS)
    if forbidden:
        raise ForbiddenManifestField(f"payload 含禁止的未来字段: {forbidden}")
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(f"schema_version 必须为 {SCHEMA_VERSION}")
    if doc.get("manifest_kind") != PAYLOAD_KIND:
        raise ManifestError("manifest_kind 必须为 project_archive_payload")
    if doc.get("status") in FINAL_STATUS_VALUES:
        raise ForbiddenManifestField("最终状态不得写入包内 payload")
    missing = sorted(REQUIRED_TOP_KEYS - set(doc))
    if missing:
        raise ManifestError(f"payload 缺必填字段: {missing}")
    files = doc["files"]
    if not isinstance(files, list) or not files:
        raise ManifestError("files[] 必须是非空数组")
    names = set()
    for f in files:
        for k in ("path", "bytes", "sha256", "role"):
            if k not in f:
                raise ManifestError(f"file 条目缺字段 {k}: {f}")
        if f["path"] == PAYLOAD_MANIFEST_NAME:
            raise ForbiddenManifestField("files[] 不得包含 manifest 自身")
        if not re.fullmatch(r"[0-9a-f]{64}", f["sha256"]):
            raise ManifestError(f"非法 sha256: {f['path']}")
        if f["path"] in names:
            raise ManifestError(f"files[] 重名: {f['path']}")
        names.add(f["path"])
    if present_paths is not None:
        # 解包后：files[] 与实际文件集合必须逐一对应（manifest 自身除外）
        want = names
        got = {p.replace("\\", "/") for p in present_paths}
        if want - got:
            raise ManifestError(f"manifest 声明但解包缺失: {sorted(want - got)[:10]}")
        if got - want:
            extra = sorted(got - want)
            raise ManifestError(f"解包存在但 manifest 未声明: {extra[:10]}")


# ---------------- receipt（包外）----------------

RECEIPT_REQUIRED = {
    "schema_version", "manifest_kind", "archive_operation_id",
    "project_id", "legacy_alias", "revision", "package",
    "manifest_payload_sha256", "verification", "status", "created_at",
}


def build_receipt(*, archive_operation_id, project_id, legacy_alias, revision,
                  package_name, package_bytes, package_sha256,
                  manifest_payload_sha256, verification: dict,
                  status: str, created_at: float,
                  release_ref: dict | None = None,
                  orphan: bool = False, index_published: bool = False) -> dict:
    rcpt = {
        "schema_version": SCHEMA_VERSION,
        "manifest_kind": RECEIPT_KIND,
        "archive_operation_id": archive_operation_id,
        "project_id": project_id,
        "legacy_alias": legacy_alias,
        "revision": revision,
        "package": {
            "name": package_name, "bytes": package_bytes,
            "sha256": package_sha256,  # tar 关闭后才算，放包外
        },
        "manifest_payload_sha256": manifest_payload_sha256,
        "release_ref": release_ref,  # 模拟 id/tag/asset_id；真实写入在 G3+
        "verification": verification,
        "status": status,
        "orphan": orphan,
        "index_published": index_published,
        "created_at": created_at,
        # 注意：不含自身 sha256；其摘要由外层索引记录（无固定点哈希）
    }
    validate_receipt(rcpt)
    return rcpt


def validate_receipt(doc: dict) -> None:
    if not isinstance(doc, dict):
        raise ManifestError("receipt 必须是 JSON 对象")
    if doc.get("manifest_kind") != RECEIPT_KIND:
        raise ManifestError("manifest_kind 必须为 verification_receipt")
    missing = sorted(RECEIPT_REQUIRED - set(doc))
    if missing:
        raise ManifestError(f"receipt 缺字段: {missing}")
    if "receipt_sha256" in doc or "self_sha256" in doc:
        raise ForbiddenManifestField("receipt 不得自哈希")
    if not re.fullmatch(r"[0-9a-f]{64}", doc["package"]["sha256"]):
        raise ManifestError("receipt package.sha256 非法")


# ---------------- legacy 侧车（不改旧包）----------------

def build_legacy_sidecar(*, project_id, legacy_alias, source_note: str,
                         files_entries: list, package_sha256: str,
                         package_bytes: int, missing_required: list,
                         created_at: float) -> dict:
    """旧包无包内 manifest：在包外生成 sidecar（事实描述，不冒充逐文件原始凭证）。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_kind": SIDECAR_KIND,
        "project_id": project_id,
        "legacy_alias": legacy_alias,
        "source_note": source_note,
        "package": {"bytes": package_bytes, "sha256": package_sha256},
        "per_file_evidence": "sidecar_computed_from_actual_extract_not_original_manifest",
        "files": sorted(files_entries, key=lambda x: x["path"]),
        "missing_required": missing_required,
        "created_at": created_at,
    }


def assert_restorable_level(payload_or_sidecar: dict) -> str:
    """返回 'full' / 'partial'；缺必需依赖抛 MissingRequiredDependency 由调用方决定降级。
    - full：input 存在且无未解析必需 skill/指针
    """
    if payload_or_sidecar.get("manifest_kind") == SIDECAR_KIND:
        missing = payload_or_sidecar.get("missing_required", [])
        return "partial" if missing else "full"
    deps = payload_or_sidecar.get("dependencies", {})
    missing = deps.get("missing_required", [])
    return "partial" if missing else "full"
