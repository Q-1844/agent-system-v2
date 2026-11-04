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
    ap.add_argument("--dry-run", action="store_true",
                    help="verify everything, write nothing")
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
    if "project.tar" not in by_name or "final.mp4" not in by_name:
        raise SystemExit(f"release {tag} missing expected assets: {sorted(by_name)}")
    package_meta, media_meta = by_name["project.tar"], by_name["final.mp4"]

    # ---- 2. download + server-digest comparison ----
    work = Path(tempfile.mkdtemp(prefix=f"posthoc-{nonce}-"))
    package_path = Path(args.package) if args.package else work / "project.tar"
    media_path = Path(args.media) if args.media else work / "final.mp4"
    if not package_path.is_file():
        package_path.write_bytes(adapter.download_asset_authenticated(package_meta.id))
    if not media_path.is_file():
        media_path.write_bytes(adapter.download_asset_authenticated(media_meta.id))
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
           "asset_name": "project.tar", "sha256": package_sha,
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
                         "(finalize-propagation 404 race); assets were intact"),
              "verification": {
                  "package_sha256": package_sha, "media_sha256": media_sha,
                  "restore_package": restored,
                  "tree_fingerprint": declared,
                  "final_duration_s": duration_s},
              "release": {"repo": f"{OWNER}/{RUNDATA}", "release_id": release_id,
                          "tag": tag,
                          "assets": {"final.mp4": media_meta.id,
                                     "project.tar": package_meta.id}},
              "fix": "archive readback 404 grace retry "
                     "(fix/archive-readback-404-grace)"}
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
