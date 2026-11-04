# tools/relstore/restore_campaign.py
# 恢复：整包对账 → 安全解包 → payload/legacy 双模式校验 → 依赖等级 → 可选改名 rebase。
# 纯本地、零网络；不依赖 src.*（对 ProjectStore/master_lease 的消费验证放在测试层）。
from __future__ import annotations

import hashlib
import json
import time
import unicodedata
from pathlib import Path

from . import manifest as M
from .errors import HashMismatch, ManifestError, MissingRequiredDependency
from .manifest import (PAYLOAD_MANIFEST_NAME, RECEIPT_NAME, analyze_dependencies,
                       build_legacy_sidecar, classify_role, human_dumps,
                       sha256_file, strict_loads, validate_payload)
from .path_rebase import apply_rebase, extract_embedded_paths, validate_new_alias
from .safe_tar import concat_parts, inspect, safe_extract


def sha256_path(path: Path) -> str:
    return sha256_file(Path(path))


def _tree_files(root: Path) -> list:
    return [unicodedata.normalize("NFC", p.relative_to(root).as_posix())
            for p in root.rglob("*") if p.is_file()]


def _lease_status(project_root: Path) -> dict:
    p = project_root / "output/agents/master_lease.json"
    if not p.exists():
        return {"present": False}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {"present": True, "holder": d.get("holder"),
                "generation": d.get("generation"),
                "expired": (d.get("expires_at") or 0) < time.time()}
    except Exception as e:
        return {"present": True, "corrupt": True, "error": repr(e)}


def _verify_payload(root: Path, alias: str) -> dict:
    payload_path = root / PAYLOAD_MANIFEST_NAME
    raw = payload_path.read_bytes()
    payload = strict_loads(raw)
    tree_files = set(_tree_files(root)) - {PAYLOAD_MANIFEST_NAME}
    validate_payload(payload, present_paths=tree_files)
    bad, missing = [], []
    for f in payload["files"]:
        p = root / f["path"]
        if not p.exists():
            missing.append(f["path"])
            continue
        if p.stat().st_size != f["bytes"] or sha256_file(p) != f["sha256"]:
            bad.append(f["path"])
    embedded = extract_embedded_paths(root, alias)
    ref_missing = sorted({r for r, ok in embedded if not ok})
    level = M.assert_restorable_level(payload)
    return {"mode": "payload-v2", "payload": payload,
            "manifest_payload_sha256": hashlib.sha256(raw).hexdigest(),
            "files_checked": len(payload["files"]),
            "hash_bad": bad, "missing": missing + ref_missing,
            "embedded_refs": len(embedded),
            "missing_required": payload["dependencies"]["missing_required"],
            "restorable_level": level}


def _verify_legacy(root: Path, alias: str, *, package_sha256: str | None,
                   package_bytes: int | None, source_note: str) -> dict:
    """旧包：无包内逐文件 manifest。只做事实型侧车，不冒充原始逐文件凭证。"""
    files = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = unicodedata.normalize("NFC", p.relative_to(root).as_posix())
        files.append({"path": rel, "bytes": p.stat().st_size,
                      "sha256": sha256_file(p), "role": classify_role(rel),
                      "required_for_restore": True})
    deps = analyze_dependencies(root)
    sidecar = build_legacy_sidecar(
        project_id=f"legacy__{alias}", legacy_alias=alias,
        source_note=source_note, files_entries=files,
        package_sha256=package_sha256 or "", package_bytes=package_bytes or 0,
        missing_required=deps["missing_required"], created_at=time.time())
    embedded = extract_embedded_paths(root, alias)
    ref_missing = sorted({r for r, ok in embedded if not ok})
    return {"mode": "legacy", "sidecar": sidecar,
            "files_checked": len(files), "hash_bad": [],
            "missing": ref_missing, "embedded_refs": len(embedded),
            "missing_required": deps["missing_required"],
            "skill_load_fail_events": deps["skill_load_fail_events"],
            "per_file_evidence":
                "整包哈希外部对账；逐文件清单为解包后侧车实算，非旧包原始 manifest",
            "restorable_level": M.assert_restorable_level(sidecar)}


def restore_package(tar_path, dest_parent, *, new_alias: str | None = None,
                    expected_sha256: str | None = None,
                    source_note: str = "legacy D/B archive") -> dict:
    """完整恢复流程（纯本地）。返回报告 dict。任何校验失败抛异常，不覆盖任何东西。"""
    tar_path = Path(tar_path)
    dest_parent = Path(dest_parent)
    if expected_sha256:
        got = sha256_file(tar_path)
        if got != expected_sha256:
            raise HashMismatch(f"整包 sha256 不符: {got} != {expected_sha256}")
    plan = inspect(tar_path)
    root = safe_extract(tar_path, dest_parent, plan)
    alias = plan.root_prefix
    project_root = root / alias

    manifest_path = project_root / PAYLOAD_MANIFEST_NAME
    if manifest_path.exists():
        result = _verify_payload(project_root, alias)
    else:
        result = _verify_legacy(
            project_root, alias,
            package_sha256=expected_sha256,
            package_bytes=plan.source.stat().st_size,
            source_note=source_note)
        # 侧车写在解包根之外（restore 根的上一级，不污染项目树）
        sc_path = root.parent / f"sidecar_{alias}.json"
        sc_path.write_bytes(human_dumps(result["sidecar"]))
        result["sidecar_path"] = str(sc_path)

    result["lease"] = _lease_status(project_root)
    result["alias"] = alias
    result["project_root"] = str(project_root)

    if new_alias and new_alias != alias:
        validate_new_alias(new_alias, project_root.parent)
        rep = apply_rebase(project_root, alias, new_alias)
        # 物理目录改名（隔离恢复区内；不触碰任何原项目）
        new_root = project_root.parent / new_alias
        project_root.rename(new_root)
        result["rebase"] = rep.to_dict()
        result["alias_after"] = new_alias
        result["project_root_after"] = str(new_root)
        # 改名后引用复查
        leftover = extract_embedded_paths(new_root, alias)
        result["rebase"]["old_prefix_refs_left"] = len(leftover)
        result["missing"] = result.get("missing", []) + \
            [r for r, ok in extract_embedded_paths(new_root, new_alias) if not ok]

    if result["hash_bad"]:
        result["restorable_level"] = "blocked"
        raise HashMismatch(
            f"恢复完整性失败，文件哈希与 manifest 不符: {result['hash_bad'][:5]}")
    if result["missing"]:
        result["restorable_level"] = "blocked"
        raise ManifestError(
            f"恢复完整性失败，manifest 声明文件/引用缺失: {result['missing'][:5]}")
    if result["missing_required"]:
        result["restorable_level"] = "partial"
    return result


def restore_from_parts(part_paths, work_dir, dest_parent, *,
                       expected_sha256: str | None = None,
                       new_alias: str | None = None) -> dict:
    """B 仓 95MiB .part-XX 只读重组 → 同一恢复流程。重组文件只写 work_dir（隔离）。"""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    joined = work_dir / "reassembled.tar"
    with open(joined, "wb") as w:
        concat_parts([Path(p) for p in part_paths], w,
                     expected_sha=None)  # 重组后由 restore_package 统一对账
    return restore_package(joined, dest_parent, new_alias=new_alias,
                           expected_sha256=expected_sha256,
                           source_note="legacy B Git split parts (reassembled)")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="restore_campaign")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("restore")
    r.add_argument("tar")
    r.add_argument("--dest", required=True)
    r.add_argument("--expected-sha", default=None)
    r.add_argument("--new-name", default=None)
    r.add_argument("--source-note", default="manual restore")
    r = sub.add_parser("parts")
    r.add_argument("--parts", nargs="+", required=True)
    r.add_argument("--work", required=True)
    r.add_argument("--dest", required=True)
    r.add_argument("--expected-sha", default=None)
    r.add_argument("--new-name", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "restore":
        rep = restore_package(args.tar, args.dest,
                              expected_sha256=args.expected_sha,
                              new_alias=args.new_name,
                              source_note=args.source_note)
    else:
        rep = restore_from_parts(args.parts, args.work, args.dest,
                                 expected_sha256=args.expected_sha,
                                 new_alias=args.new_name)
    import json
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
