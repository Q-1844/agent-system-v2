# tools/relstore/archive_campaign.py
# 本地归档状态机：plan → package → verify(download+safe-extract+refs) → receipt → index。
# G2：只通过 FakeReleaseAdapter 运行；真实适配器写路径在 release_adapter.py 硬禁用。
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from pathlib import Path

from . import manifest as M
from . import safe_tar
from .errors import (AuthError, AssetConflict, HashMismatch, IndexPublishFailed,
                     ManifestError, RateLimited, ReceiptLost, ServerError,
                     SourceRevisionChanged, NetworkInterrupt, RelstoreError)
from .manifest import (PAYLOAD_MANIFEST_NAME, RECEIPT_NAME, build_payload,
                       build_receipt, canonical_dumps, human_dumps,
                       strict_loads, validate_payload)
from .path_rebase import scan as rebase_scan
from .safe_tar import create_archive, inspect, safe_extract

PLANNED = "PLANNED"
PACKAGED = "PACKAGED"
UPLOADING = "UPLOADING"
UPLOADED = "UPLOADED"
VERIFIED_BYTES = "VERIFIED_BYTES"
VERIFIED_RESTORABLE = "VERIFIED_RESTORABLE"
INDEX_PENDING = "INDEX_PENDING"
SUPERSEDED = "SUPERSEDED"
FAILED = "FAILED"

RETRYABLE = (RateLimited, ServerError, NetworkInterrupt, ReceiptLost)

SIGNED_URL_RE = re.compile(
    r"(X-Amz-|Signature=|&sig=|[?&]token=|[?&]sign=|sas=|skt=|jwt=)", re.I)
SECRET_VALUE_RE = re.compile(
    r"(github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|"
    r"sk-[A-Za-z0-9]{20,})")
URL_RE = re.compile(r"https?://[^\s\"'<>)\\]+")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _state_path(opdir: Path) -> Path:
    return Path(opdir) / "state.json"


def load_state(opdir: Path) -> dict:
    return json.loads(_state_path(opdir).read_text(encoding="utf-8"))


def _save_state(opdir: Path, state: dict):
    p = _state_path(opdir)
    tmp = p.with_suffix(".json.tmp")
    persisted = {k: v for k, v in state.items() if not k.startswith("_")}
    tmp.write_text(json.dumps(persisted, ensure_ascii=False, sort_keys=True, indent=1),
                   encoding="utf-8")
    tmp.replace(p)


def source_signature(project_dir: Path) -> dict:
    """源版本锚（G3-A 起含整树指纹）：
    - project_json_sha256 + updated_at：G2 既有快速锚；
    - tree_fingerprint：整份冻结快照内容指纹（媒体/工具/JSON 任一变化都可检出）。
    释放缓存前必须以整树指纹（或完整 manifest 内容指纹）做末次原子版本确认。"""
    from .manifest import compute_snapshot_fingerprint
    pj = Path(project_dir) / "output/project.json"
    data = pj.read_bytes()
    updated_at = None
    try:
        updated_at = json.loads(data).get("updated_at")
    except Exception:
        pass
    return {"project_json_sha256": hashlib.sha256(data).hexdigest(),
            "updated_at": updated_at,
            "tree_fingerprint": compute_snapshot_fingerprint(project_dir)}


def _scan_text_safety(project_dir: Path):
    """公开前检查：签名 URL / 密钥内容模式；返回统计（只报位置不录值）。"""
    secret_hits, signed_urls, hosts = [], [], set()
    for p in Path(project_dir).rglob("*"):
        if not p.is_file() or p.suffix.lower() not in {
                ".json", ".jsonl", ".md", ".txt", ".yaml", ".yml", ".srt"}:
            continue
        rel = unicodedata.normalize("NFC", p.relative_to(project_dir).as_posix())
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if SECRET_VALUE_RE.search(txt):
            secret_hits.append(rel)
        signed = len(SIGNED_URL_RE.findall(txt))
        if signed:
            signed_urls.append({"file": rel, "count": signed})
        for u in URL_RE.findall(txt):
            hosts.add(re.sub(r"^https?://([^/]+).*", r"\1", u))
    return {"secret_pattern_hits": secret_hits,
            "signed_urls": signed_urls,
            "url_hosts": sorted(hosts)}


def _retry(fn, *, max_attempts=4, sleep=time.sleep, base_delay=0.02):
    """分类重试：429 遵守 Retry-After；5xx/网络/回执丢失退避；401/403/冲突不重试。"""
    last = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except RETRYABLE as e:
            last = e
            if attempt == max_attempts:
                raise
            delay = getattr(e, "retry_after", None) or base_delay * attempt
            sleep(delay)
        except (AuthError, AssetConflict):
            raise
    raise last  # type: ignore[misc]


def plan(*, project_dir, opdir, operation_id, project_id, revision,
         lifecycle_status, source_code_commit, created_at=None,
         tool_versions=None) -> dict:
    project_dir = Path(project_dir)
    opdir = Path(opdir)
    opdir.mkdir(parents=True, exist_ok=True)
    sig = source_signature(project_dir)
    state = {
        "state": PLANNED, "operation_id": operation_id, "project_id": project_id,
        "project_dir": str(project_dir),
        "legacy_alias": project_dir.name, "revision": revision,
        "lifecycle_status": lifecycle_status,
        "source_code_commit": source_code_commit,
        "source_signature": sig, "created_at": created_at or time.time(),
        "tool_versions": tool_versions or {}, "attempts": [],
        "package": None, "release": None, "error": None,
    }
    _save_state(opdir, state)
    return state


def package(state_or_opdir) -> dict:
    state = _as_state(state_or_opdir)
    opdir = state["_opdir"]
    project_dir = Path(state["project_dir"])
    # revision 守卫
    sig_now = source_signature(project_dir)
    if sig_now != state["source_signature"]:
        state["state"] = SUPERSEDED
        state["error"] = "source_revision_changed_before_package"
        _save_state(opdir, state)
        raise SourceRevisionChanged(state["error"])
    safety = _scan_text_safety(project_dir)
    if safety["secret_pattern_hits"]:
        raise ManifestError(
            f"公开前安全扫描命中密钥模式: {safety['secret_pattern_hits']}")
    # 未完工镜头清单（best-effort 读 project.json）
    unfinished = []
    try:
        pj = json.loads((project_dir / "output/project.json").read_text(
            encoding="utf-8"))
        for sc in pj.get("scenes", []):
            for sh in sc.get("shots", []):
                if not sh.get("video_versions"):
                    unfinished.append(sh.get("shot_id"))
    except Exception:
        pass
    payload, files = build_payload(
        project_dir=project_dir, project_id=state["project_id"],
        revision=state["revision"], lifecycle_status=state["lifecycle_status"],
        source_code_commit=state["source_code_commit"],
        created_at=state["created_at"], archive_operation_id=state["operation_id"],
        tool_versions=state["tool_versions"], unfinished=sorted(filter(None, unfinished)))
    payload["external_refs"] = {
        "signed_or_expiring_urls": sum(x["count"] for x in safety["signed_urls"]),
        "url_hosts": safety["url_hosts"],
    }
    payload["security_scan"].update({
        "secret_pattern_hits": safety["secret_pattern_hits"],
        "signed_urls": safety["signed_urls"],
    })
    validate_payload(payload)
    payload_bytes = human_dumps(payload)
    strict_loads(payload_bytes)  # 双保险：落盘字节必须可严格解析

    tar_path = opdir / f"{state['operation_id']}.tar"
    info = create_archive(
        project_dir, tar_path,
        archive_root_name=state["legacy_alias"],
        extra_files={PAYLOAD_MANIFEST_NAME: payload_bytes},
        fixed_mtime=0)
    # manifest 在包内的 sha（files[] 排除其自身；receipt 用）
    manifest_sha = sha256_bytes(payload_bytes)
    state.update({"state": PACKAGED,
                 "package": {"path": str(tar_path), "bytes": info["bytes"],
                             "sha256": info["sha256"], "file_count": info["file_count"],
                             "manifest_payload_sha256": manifest_sha,
                             "payload": payload},
                 "restorable_level": M.assert_restorable_level(payload)})
    _save_state(opdir, state)
    return state


def _verify_extracted(state, root: Path) -> dict:
    """解包树校验：payload files[] 逐文件 sha/bytes + 业务路径引用存在性。"""
    payload = state["package"]["payload"]
    present, results = set(), []
    missing, bad = [], []
    for f in payload["files"]:
        p = root / state["legacy_alias"] / f["path"]
        present.add(f["path"])
        if not p.exists():
            missing.append(f["path"])
            continue
        data = p.read_bytes()
        if len(data) != f["bytes"] or hashlib.sha256(data).hexdigest() != f["sha256"]:
            bad.append(f["path"])
        results.append(f["path"])
    validate_payload(payload, present_paths=present)
    # 业务引用：canonical JSON 内 projects\<alias>\ 前缀必须在树内
    from .path_rebase import extract_embedded_paths
    rep = rebase_scan(root / state["legacy_alias"], state["legacy_alias"])
    embedded = extract_embedded_paths(root / state["legacy_alias"],
                                      state["legacy_alias"])
    ref_missing = sorted({r for r, ok in embedded if not ok})
    return {"files_checked": len(results), "missing": missing + ref_missing,
            "hash_bad": bad,
            "embedded_path_refs": rep.replacements,
            "unknown_refs": rep.unknown_refs,
            "missing_required": payload["dependencies"]["missing_required"],
            "restorable_level": M.assert_restorable_level(payload)}


def verify(state_or_opdir, adapter, *, release_tag: str, asset_name: str,
           sleep=time.sleep, publish: bool = True) -> dict:
    state = _as_state(state_or_opdir)
    opdir = state["_opdir"]
    if state.get("state") == SUPERSEDED:
        raise SourceRevisionChanged("operation 已 SUPERSEDED，拒绝继续")
    # 上传前再查一次源 revision（测试 7：期间变化不得继续）
    sig_now = source_signature(Path(state["project_dir"]))
    if sig_now != state["source_signature"]:
        state["state"] = SUPERSEDED
        state["error"] = "source_revision_changed_during_verify"
        _save_state(opdir, state)
        raise SourceRevisionChanged(state["error"])

    pkg = state["package"]
    tar_bytes = Path(pkg["path"]).read_bytes()

    def _do_upload():
        rel = adapter.create_release(release_tag, name=f"{state['legacy_alias']} {release_tag}")
        existing = adapter.get_asset(rel.id, asset_name)
        if existing is not None:
            if existing.sha256 == pkg["sha256"]:
                return rel, existing, True
            raise AssetConflict(f"同名异 sha: {asset_name}")
        meta = adapter.upload_asset(rel.id, asset_name, tar_bytes, pkg["sha256"])
        return rel, meta, False

    state["state"] = UPLOADING
    _save_state(opdir, state)
    rel, meta, reused = _retry(_do_upload, sleep=sleep)
    state["state"] = UPLOADED
    state["release"] = {"tag": release_tag, "asset_name": asset_name,
                        "release_id": rel.id, "asset_id": meta.id,
                        "idempotent_reuse": reused}
    _save_state(opdir, state)

    # 重新下载（成功判据不是上传应答，是重下+哈希）
    downloaded = _retry(lambda: adapter.download_asset(meta.id), sleep=sleep)
    if len(downloaded) != pkg["bytes"]:
        state["state"] = FAILED
        state["error"] = "download_byte_count_mismatch"
        _save_state(opdir, state)
        raise HashMismatch("二次下载字节数不符")
    dl_sha = hashlib.sha256(downloaded).hexdigest()
    if dl_sha != pkg["sha256"]:
        state["state"] = FAILED
        state["error"] = "download_hash_mismatch"
        _save_state(opdir, state)
        raise HashMismatch("二次下载 sha256 不符")
    state["state"] = VERIFIED_BYTES
    _save_state(opdir, state)

    # 安全解包 + 结构/引用校验（在 opdir 内随机目录）
    dl_tar = opdir / "redownload.tar"
    dl_tar.write_bytes(downloaded)
    plan = inspect(dl_tar)
    root = safe_extract(dl_tar, opdir / "verify_roots", plan)
    checks = _verify_extracted(state, root)
    if checks["missing"] or checks["hash_bad"]:
        state["state"] = FAILED
        state["error"] = "restore_verify_failed"
        _save_state(opdir, state)
        raise ManifestError(f"恢复校验失败: {checks}")

    level = checks["restorable_level"]
    if level == "full":
        state["state"] = VERIFIED_RESTORABLE
    # partial（缺必需依赖）停留在 VERIFIED_BYTES，不冒充完全可恢复
    receipt = build_receipt(
        archive_operation_id=state["operation_id"], project_id=state["project_id"],
        legacy_alias=state["legacy_alias"], revision=state["revision"],
        package_name=asset_name, package_bytes=pkg["bytes"],
        package_sha256=pkg["sha256"],
        manifest_payload_sha256=pkg["manifest_payload_sha256"],
        verification={
            "downloaded_bytes": len(downloaded), "download_sha256": dl_sha,
            "bytes_match": True, "safe_extract_ok": True,
            "files_checked": checks["files_checked"],
            "embedded_path_refs": checks["embedded_path_refs"],
            "missing_required": checks["missing_required"],
            "restorable_level": level,
        },
        status=state["state"], created_at=time.time(),
        release_ref={"repository": "fake-g2", "tag": release_tag,
                     "release_id": rel.id, "asset_id": meta.id,
                     "remote_digest": meta.sha256},
        index_published=False)
    (opdir / RECEIPT_NAME).write_bytes(human_dumps(receipt))

    if not publish:
        _save_state(opdir, state)
        return state
    # B1 权威索引【最后】发布；失败→孤儿登记，源件不动。
    # receipt 必须先以"将发布"形态冻结落盘，索引记录其哈希；发布失败再改写。
    try:
        receipt["index_published"] = True
        frozen_receipt_bytes = human_dumps(receipt)
        (opdir / RECEIPT_NAME).write_bytes(frozen_receipt_bytes)
        index_doc = {"project_id": state["project_id"],
                     "revision": state["revision"],
                     "asset_id": meta.id, "tag": release_tag,
                     "package_sha256": pkg["sha256"], "status": state["state"],
                     "receipt_sha256": sha256_bytes(frozen_receipt_bytes)}
        _retry(lambda: adapter.publish_index(state["project_id"], index_doc),
               sleep=sleep)
    except IndexPublishFailed:
        receipt["index_published"] = False
        (opdir / RECEIPT_NAME).write_bytes(human_dumps(receipt))
        state["state"] = INDEX_PENDING
        state["error"] = "index_publish_failed_orphan"
        _save_state(opdir, state)
        raise
    _save_state(opdir, state)
    return state


def recover_index(state_or_opdir, adapter, *, sleep=time.sleep) -> dict:
    """孤儿收养：资产已验证、仅索引发布失败时的重入。"""
    state = _as_state(state_or_opdir)
    opdir = state["_opdir"]
    if state["state"] != INDEX_PENDING:
        return state
    receipt = strict_loads((opdir / RECEIPT_NAME).read_bytes())
    rel_ref = receipt["release_ref"]
    index_doc = {"project_id": state["project_id"], "revision": state["revision"],
                 "asset_id": rel_ref["asset_id"], "tag": rel_ref["tag"],
                 "package_sha256": receipt["package"]["sha256"],
                 "status": receipt["status"],
                 "receipt_sha256": sha256_bytes(human_dumps(receipt))}
    _retry(lambda: adapter.publish_index(state["project_id"], index_doc),
           sleep=sleep)
    receipt["index_published"] = True
    (opdir / RECEIPT_NAME).write_bytes(human_dumps(receipt))
    state["state"] = receipt["status"]
    state["error"] = None
    _save_state(opdir, state)
    return state


def _as_state(state_or_opdir) -> dict:
    if isinstance(state_or_opdir, dict):
        st = state_or_opdir
        if "_opdir" not in st:
            raise RelstoreError("state 缺 _opdir")
        return st
    opdir0 = Path(state_or_opdir)
    st = load_state(opdir0)
    st["_opdir"] = opdir0
    st["project_dir"] = st.get("project_dir") or _detect_project_dir(opdir0, st)
    return st


def _detect_project_dir(opdir: Path, state: dict) -> str:
    # plan() 时把 project_dir 写入 state（见 run helpers）；兼容旧键
    raise RelstoreError("state 缺 project_dir")


# ---------------- 本地 CLI（G2 仅 fake 适配器）----------------

def main(argv=None):
    import argparse
    from .fake_release_adapter import FakeReleaseAdapter
    ap = argparse.ArgumentParser(prog="archive_campaign")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--project", required=True)
    p.add_argument("--opdir", required=True)
    p.add_argument("--operation-id", required=True)
    p.add_argument("--project-id", required=True)
    p.add_argument("--revision", type=int, default=1)
    p.add_argument("--lifecycle", default="active")
    p.add_argument("--source-commit", required=True)
    p = sub.add_parser("package")
    p.add_argument("--opdir", required=True)
    p = sub.add_parser("verify")
    p.add_argument("--opdir", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--asset", required=True)
    p.add_argument("--adapter", default="fake", choices=["fake"])
    p = sub.add_parser("status")
    p.add_argument("--opdir", required=True)
    p = sub.add_parser("recover-index")
    p.add_argument("--opdir", required=True)
    p.add_argument("--adapter", default="fake", choices=["fake"])
    args = ap.parse_args(argv)
    if args.cmd == "plan":
        st = plan(project_dir=args.project, opdir=args.opdir,
                  operation_id=args.operation_id, project_id=args.project_id,
                  revision=args.revision, lifecycle_status=args.lifecycle,
                  source_code_commit=args.source_commit)
    elif args.cmd == "package":
        st = package(args.opdir)
    elif args.cmd == "verify":
        st = verify(args.opdir, FakeReleaseAdapter(),
                    release_tag=args.tag, asset_name=args.asset)
    elif args.cmd == "status":
        st = load_state(__import__("pathlib").Path(args.opdir))
    elif args.cmd == "recover-index":
        st = recover_index(args.opdir, FakeReleaseAdapter())
    print(json.dumps({k: v for k, v in st.items() if not k.startswith("_")
                      and k != "package" or k in ("state",)},
                     ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
