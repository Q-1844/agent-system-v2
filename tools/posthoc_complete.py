#!/usr/bin/env python3
"""Post-hoc archive completion for a run that failed ONLY at the
readback-verification step (e.g. the daycarry20m-20261102 finalize race,
where a 1.4GB asset 404'd ~0.7s before it became visible).

Re-verifies everything independently before any write:
  1. resolve release full-project/{nonce}, locate project.tar + final.mp4
  2. download both, compare sha256 against GitHub's server-side digests
     (and optional expected values passed as args)
  3. restore_package(): restorable_level must be "full", no missing_required
  4. tree fingerprint: manifest-declared == recomputed from restored tree
  5. ffprobe final.mp4 duration

Only then (unless --dry-run):
  6. Registry: create → activity → plan → snapshot → verified → completed
  7. B1 checkpoint: phase=COMPLETE (+saved archive_ref), CAS
  8. B1 posthoc verification report file

Fail-loud throughout; no media generation, no provider calls.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.e2e_autonomous_run import B1GitHubCas  # noqa: E402
from tools.relstore import restore_campaign  # noqa: E402
from tools.relstore.github_release_adapter import (  # noqa: E402
    GitHubReleaseAdapter, HttpxTransport)
from tools.relstore.manifest import (PAYLOAD_MANIFEST_NAME,  # noqa: E402
                                     compute_snapshot_fingerprint)
from tools.relstore.project_registry import Registry  # noqa: E402
from tools.video_task_state import ProductionCheckpoint, task_index  # noqa: E402

OWNER = "Q-1844"
RUNDATA = "agent-system-v2-rundata"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ffprobe_duration(path: Path) -> float:
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=300, check=True)
    return float(json.loads(p.stdout)["format"]["duration"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nonce", required=True)
    ap.add_argument("--token", default=None,
                    help="archive token (default: env B1_ARCHIVE_TOKEN)")
    ap.add_argument("--expected-package-sha256", default="")
    ap.add_argument("--expected-media-sha256", default="")
    ap.add_argument("--expected-duration-s", type=float, default=0.0,
                    help="optional cross-check for ffprobe duration")
    ap.add_argument("--package", default=None,
                    help="local project.tar (verification reuse; default: download)")
    ap.add_argument("--media", default=None,
                    help="local final.mp4 (verification reuse)")
    ap.add_argument("--rebuild-from", default=None,
                    help="项目目录（从 e2e-evidence artifact 解出）：当 release 的 "
                         "project.tar 为 starter 僵尸/缺失时，从此目录重打包并以 "
                         "sha 命名上传替代资产（🔴 ink-diffusion-20261107 僵尸场景）")
    ap.add_argument("--source-code-commit", default="unknown",
                    help="重打包 plan 的源码提交（填生产 run 的 source SHA）")
    ap.add_argument("--dry-run", action="store_true",
                    help="verify everything, write nothing (重打包上传除外——"
                         "无它则无法自证；资产幂等可复用)")
    args = ap.parse_args()

    import os
    token = (args.token or os.environ.get("B1_ARCHIVE_TOKEN") or "").strip()
    if not token:
        raise SystemExit("missing --token / B1_ARCHIVE_TOKEN")
    nonce = args.nonce

    # ---- 1. release & assets ----
    adapter = GitHubReleaseAdapter.armed(OWNER, RUNDATA, token, HttpxTransport())
    tag = f"full-project/{nonce}"
    # releases/tags/{tag} 查不到 draft（全量归档 release 终态即 draft），必须列表匹配
    release = adapter.get_release_by_tag(tag)
    if release is None:
        r = adapter._get("releases?per_page=100")
        if r.status != 200:
            adapter._classify(r, "releases?per_page=100")
        for item in r.json():
            if item.get("tag_name") == tag:
                release = adapter.get_release(str(item["id"]))
                break
    if release is None:
        raise SystemExit(f"release {tag} not found (published or draft)")
    release_id = str(release.id) if hasattr(release, "id") else str(release["id"])
    assets = adapter.list_assets(release_id)
    by_name = {a.name: a for a in assets}
    if "final.mp4" not in by_name:
        raise SystemExit(f"release {tag} missing final.mp4 (assets: {sorted(by_name)})")
    media_meta = by_name["final.mp4"]
    package_meta = by_name.get("project.tar")
    # 🔴 ink-diffusion-20261107：project.tar 可能为 starter 僵尸（POST 201 假回执，
    # finalize 永久卡 starter，资产 404 不可下载）。僵尸不可验证也不可复用；
    # 必须从 e2e-evidence artifact 恢复项目目录重打包。final.mp4 若同为僵尸则无救
    # （成片本体），只能回到 e2e-evidence artifact 人工重建归档。
    zombie = package_meta is not None and package_meta.state != "uploaded"
    if media_meta.state != "uploaded":
        raise SystemExit(f"final.mp4 state={media_meta.state}（starter 僵尸？）"
                         f"——成片本体资产异常，posthoc 无法补完")
    if package_meta is None or zombie:
        if not args.rebuild_from:
            raise SystemExit(
                f"project.tar {'state=' + package_meta.state if zombie else 'missing'}"
                f"（🔴 starter 僵尸，ink-diffusion-20261107）且未提供 --rebuild-from"
                f" <项目目录>；僵尸资产不可下载/复用，请从 e2e-evidence artifact 恢复"
                f"项目目录后重跑")

    # ---- 2. download + server-digest comparison ----
    work = Path(tempfile.mkdtemp(prefix=f"posthoc-{nonce}-"))
    package_path = Path(args.package) if args.package else work / "project.tar"
    media_path = Path(args.media) if args.media else work / "final.mp4"
    if package_meta is not None and not zombie and not package_path.is_file():
        package_path.write_bytes(adapter.download_asset_authenticated(package_meta.id))
    if not media_path.is_file():
        media_path.write_bytes(adapter.download_asset_authenticated(media_meta.id))

    if zombie or package_meta is None:
        # 🔴 starter 僵尸重建：e2e-evidence artifact 的项目目录 → 重打包 →
        # sha 命名上传（避开僵尸名，绝不删除/覆盖原资产——它作为故障证据保留）。
        from tools.relstore import archive_campaign
        from tools.full_project_cloud_acceptance import ensure_archive_input
        project_dir = Path(args.rebuild_from).resolve()
        if not (project_dir / "output" / "project.json").is_file():
            raise SystemExit(f"--rebuild-from 缺少 output/project.json: {project_dir}")
        ensure_archive_input(project_dir, nonce)
        opdir = work / "archive-rebuild"
        archive_campaign.plan(
            project_dir=project_dir, opdir=opdir,
            operation_id=f"posthoc-rebuild-{nonce}", project_id=nonce, revision=1,
            lifecycle_status="completed",
            source_code_commit=args.source_code_commit)
        packaged = archive_campaign.package(opdir)
        package_path = Path(packaged["package"]["path"])
        rebuilt_sha = packaged["package"]["sha256"]
        # 资产名带 run 唯一后缀：2GB POST 若再次 ReadTimeout，服务端可能留下
        # 同名半成品（starter），固定名重跑会 422→僵尸拒用而死锁
        # （🔴 2026-11-07 42384612977 实锤链条）。
        run_tag = os.environ.get("GITHUB_RUN_ID") or os.environ.get(
            "POSTHOC_RUN_TAG", "local")
        package_name = f"project-rebuilt-{rebuilt_sha[:12]}-{run_tag}.tar"
        print(f"[rebuild] repackaged from {project_dir} "
              f"({package_path.stat().st_size} bytes)")
        package_meta = adapter.upload_asset_with_recovery(
            release_id, package_name, package_path.read_bytes(), rebuilt_sha)
        redownload = work / "redownload-project.tar"
        redownload.write_bytes(adapter.download_asset_authenticated(
            package_meta.id, not_found_grace_seconds=240.0))
        if sha256_file(redownload) != rebuilt_sha:
            raise SystemExit("rebuilt package redownload sha mismatch")
        print(f"[rebuild] uploaded + redownload-verified: {package_name} "
              f"(zombie asset "
              f"{by_name['project.tar'].id if 'project.tar' in by_name else 'n/a'} "
              f"kept as evidence)")
    package_sha, media_sha = sha256_file(package_path), sha256_file(media_path)

    def digest_hex(meta):
        d = (meta.sha256 or "").removeprefix("sha256:")
        return d or None

    checks = {"package_sha256": package_sha, "media_sha256": media_sha}
    for label, got, meta, expected in (
            ("project.tar", package_sha, package_meta, args.expected_package_sha256),
            ("final.mp4", media_sha, media_meta, args.expected_media_sha256)):
        server = digest_hex(meta)
        if server and got != server:
            raise SystemExit(f"{label}: local sha256 {got} != GitHub digest {server}")
        if expected and got != expected:
            raise SystemExit(f"{label}: local sha256 {got} != expected {expected}")
        print(f"[verify] {label}: sha256 {got} (server digest "
              f"{'match' if server else 'n/a'})")

    # ---- 3. full restore verification ----
    restored = restore_campaign.restore_package(
        package_path, work / "restore-check", expected_sha256=package_sha)
    level = restored.get("restorable_level")
    if level != "full" or restored.get("missing_required"):
        raise SystemExit(f"restore verification failed: level={level}, "
                         f"missing={restored.get('missing_required')}")
    print(f"[verify] restore: level=full files_checked={restored['files_checked']} "
          f"hash_bad={restored.get('hash_bad')}")

    # ---- 4. tree fingerprint cross-check ----
    alias = restored["alias"]
    proot = next(p for p in (work / "restore-check").rglob(alias)
                 if p.is_dir() and (p / PAYLOAD_MANIFEST_NAME).is_file())
    manifest = json.loads((proot / PAYLOAD_MANIFEST_NAME).read_text(encoding="utf-8"))
    declared = manifest.get("snapshot_fingerprint")
    recomputed = compute_snapshot_fingerprint(proot, exclude={PAYLOAD_MANIFEST_NAME})
    if declared != recomputed:
        raise SystemExit(f"tree fingerprint mismatch: {declared} != {recomputed}")
    print(f"[verify] tree fingerprint: {declared} (declared == recomputed)")

    # ---- 5. duration ----
    duration_s = ffprobe_duration(media_path)
    if args.expected_duration_s and abs(duration_s - args.expected_duration_s) > 1.0:
        raise SystemExit(f"duration {duration_s} != expected {args.expected_duration_s}")
    print(f"[verify] final.mp4 duration: {duration_s:.3f}s")

    if args.dry_run:
        print("[dry-run] all verifications passed; no writes performed")
        return 0

    # ---- 6. registry sequence (mirrors archive_completed_project) ----
    registry = Registry(cas=B1GitHubCas(task_index(token, nonce, "project-registry.json")))
    registry.create_project(project_id=nonce, alias=alias, operation_id=f"{nonce}-create")
    registry.record_activity(project_id=nonce, kind="generate_video",
                             operation_id=f"{nonce}-activity")
    registry.plan_archive(project_id=nonce, operation_id=f"{nonce}-plan")
    registry.begin_snapshot(project_id=nonce, snapshot_fingerprint=declared,
                            operation_id=f"{nonce}-snapshot")
    ref = {"repo": f"{OWNER}/{RUNDATA}", "release_tag": tag,
           "release_id": release_id, "asset_id": package_meta.id,
           "asset_name": package_meta.name, "sha256": package_sha,
           "tree_fingerprint": declared, "bytes": package_path.stat().st_size,
           "final_mp4_asset_id": media_meta.id, "final_mp4_sha256": media_sha}
    registry.set_verified_restorable(project_id=nonce, archive_ref=ref,
                                     restorable_level="full",
                                     operation_id=f"{nonce}-verified")
    record = registry.get(nonce)
    registry.mark_completed(project_id=nonce, archive_ref=ref,
                            expected_activity_seq=record.last_activity_seq,
                            expected_project_revision=record.project_revision,
                            operation_id=f"{nonce}-completed")
    print("[write] registry: completed")

    # ---- 7. B1 checkpoint COMPLETE ----
    saved = {"archive_ref": ref, "registry": registry.to_doc(),
            "restore_files_checked": restored["files_checked"],
            "restorable_level": "full"}
    cp_index = task_index(token, nonce, "full-project-production.json")
    digest = cp_index.read()["doc"]["request_sha256"]
    cp = ProductionCheckpoint(cp_index, nonce, digest)
    cp.save(phase="COMPLETE", terminal_status=None, error=None, pause_reason=None,
            duration_s=duration_s, saved=saved, updated_at=now_iso())
    print("[write] B1 checkpoint: phase=COMPLETE")

    # ---- 8. posthoc report ----
    report = {"nonce": nonce, "completed_at": now_iso(),
              "reason": ("run failed only at archive readback verification "
                         "(finalize-propagation 404 race); assets were intact"
                         if not (zombie or package_meta.name != "project.tar") else
                         "project.tar was a starter zombie (🔴 ink-diffusion-"
                         "20261107: POST 201 receipt lied, finalize never "
                         "completed); repackaged from e2e-evidence artifact "
                         "and uploaded as a sha-named replacement asset"),
              "verification": {
                  "package_sha256": package_sha, "media_sha256": media_sha,
                  "restore_package": restored,
                  "tree_fingerprint": declared,
                  "final_duration_s": duration_s},
              "release": {"repo": f"{OWNER}/{RUNDATA}", "release_id": release_id,
                          "tag": tag,
                          "assets": {"final.mp4": media_meta.id,
                                     package_meta.name: package_meta.id}},
              "fix": "upload finalize confirmation + starter zombie rejection "
                     "(fix/ink-archive-starter-zombie-20261107)"}
    rep_index = task_index(token, nonce, "posthoc-archive-verification.json")
    rep_index.write_cas(report, None,
                        message=f"post-hoc archive verification {nonce}")
    print("[write] posthoc report: done")
    print(json.dumps({"phase": "COMPLETE", "nonce": nonce,
                      "duration_s": round(duration_s, 3),
                      "package_sha256": package_sha,
                      "media_sha256": media_sha}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
