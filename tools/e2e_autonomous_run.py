#!/usr/bin/env python3
"""E2E autonomous run — real Agnes t2v -> MP4 -> ffprobe -> save B Release + B1 Registry CAS.

For the video-autoproduction-e2e GitHub Actions workflow. Reuses:
  - src.api.agnes_client.AgnesClient (real t2v generation)
  - tools.relstore.github_release_adapter (B Release asset save + verify)
  - tools.relstore.project_registry.Registry (B1 state machine via B1GitHubCas bridge)

Zero local-only deps; secrets come from env (AGNES_API_KEY, B1_ARCHIVE_TOKEN).
Does NOT claim GitHub autonomy by itself — it is the production step the workflow runs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import time
import traceback
from pathlib import Path

from tools.video_task_state import (ProductionCheckpoint, RecoveryRequired,
                                   request_digest, task_index, validate_nonce,
                                   verified_record)

OWNER = "Q-1844"
REPO_B = "agent-system-v2-rundata"
REPO_B1 = "agent-system-v2-rundata-1"
AGNES_BASE = "https://api.agnes-ai.cn"
AGNES_VIDEO_MODEL = "agnes-video-2.5-flash"
PROMPT = os.environ.get("TASK_PROMPT") or (
    "a fluffy orange cat walking slowly across a sunlit wooden floor, "
    "cinematic, warm light, gentle camera")


def _env():
    k = os.environ.get("AGNES_API_KEY")
    t = os.environ.get("B1_ARCHIVE_TOKEN")
    if not k or not t:
        raise SystemExit("missing AGNES_API_KEY or B1_ARCHIVE_TOKEN env")
    return k, t


def _nonce():
    n = os.environ.get("TASK_NONCE")
    return validate_nonce(n if n else f"e2e-{time.time_ns()}")


class B1GitHubCas:
    """Bridge project_registry.Registry -> B1 GitHubContentsIndex single-file CAS.

    Registry calls cas.read()/commit(); we map those to B1 contents GET/PUT with
    blob_sha as the concurrency token (Registry's `revision` is the doc's
    registry_revision, used only for state; the real optimistic-concurrency token
    on GitHub is the blob sha, tracked here).
    """

    def __init__(self, idx):
        self.idx = idx
        self._cur = None  # {"doc":..., "blob_sha":...} | None

    def read(self):
        self._cur = self.idx.read()
        if self._cur is None:
            return None
        doc = self._cur["doc"]
        return {"doc": doc, "revision": doc.get("registry_revision", 0)}

    def commit(self, new_doc, expected_revision, *, operation_id):
        from tools.relstore.project_registry import CasConflict
        from tools.relstore.errors import AssetConflict
        actual = None if self._cur is None else self._cur["doc"].get("registry_revision", 0)
        if expected_revision != actual:
            raise CasConflict("Registry revision mismatch", expected=expected_revision,
                              actual=actual, candidate=new_doc)
        expected_blob = None if self._cur is None else self._cur["blob_sha"]
        try:
            r = self.idx.write_cas(new_doc, expected_blob,
                                  message=f"registry op={operation_id}")
        except AssetConflict as exc:
            raise CasConflict("Registry blob SHA conflict", expected=expected_revision,
                              actual=None, candidate=new_doc) from exc
        if not r.get("ok") or not r.get("new_sha"):
            raise RecoveryRequired("Registry write has no confirmed blob SHA")
        # Use our own receipt. A GET here could adopt another writer's newer SHA
        # and allow our stale next document to overwrite that writer.
        self._cur = {"doc": new_doc, "blob_sha": r["new_sha"]}
        return {"ok": True,
                "revision": new_doc.get("registry_revision", 0),
                "http": r.get("http"),
                "new_blob_sha": r.get("new_sha")}


async def gen_video(api_key, out_path, checkpoint):
    from src.api.agnes_client import AgnesClient
    cfg = {"base_url": AGNES_BASE, "api_key": api_key,
           "models": {"video": AGNES_VIDEO_MODEL,
                      "text": "agnes-3.0-flash",
                      "image": "agnes-image-2.1-flash"},
           "proxy": None, "rpm": None}
    client = AgnesClient(cfg)
    try:
        gen = checkpoint.doc.get("gen") or {}
        vid = gen.get("video_id")
        if not vid:
            if checkpoint.doc.get("phase") != "READY":
                raise RecoveryRequired("submission outcome unknown; reconcile before any new POST")
            checkpoint.save(phase="SUBMITTING")
            receipt = await client.create_video(PROMPT, mode="t2v", duration_s=5)
            vid = receipt.get("video_id") or receipt.get("task_id")
            if not isinstance(vid, str) or not vid:
                raise RecoveryRequired("submission has no usable video_id; do not auto-resubmit")
            gen = {"video_id": vid}
            checkpoint.save(phase="SUBMITTED", gen=gen)
        done = gen if gen.get("url") else await client.poll_video(vid, max_wait=660)
        url = done["url"]
        gen = {"video_id": vid, "url": url,
               "seconds": done.get("seconds"), "size": done.get("size")}
        # Save the provider URL before download, so a fresh runner can resume.
        checkpoint.save(phase="GENERATED", gen=gen)
        path = await client.download(url, str(out_path))
        return dict(gen, path=str(path))
    finally:
        try:
            await client.aclose()
        except Exception:
            pass


def ffprobe(path):
    def run(args):
        return subprocess.run(args, capture_output=True, text=True, timeout=20)

    dur = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    vid = run(["ffprobe", "-v", "error", "-select_streams", "v",
               "-show_entries", "stream=codec_name,width,height",
               "-of", "csv=p=0", str(path)])
    if dur.returncode or vid.returncode:
        raise RuntimeError("ffprobe failed")
    duration = float((dur.stdout or "0").strip() or 0)
    vstream = (vid.stdout or "").strip()
    return {"duration_s": duration, "video_stream": vstream,
            "playable": bool(vstream) and duration > 0}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save_and_register(token, mp4_path, mp4_sha, nonce):
    from tools.relstore.github_release_adapter import (
        GitHubReleaseAdapter, GitHubContentsIndex, HttpxTransport)
    from tools.relstore.project_registry import Registry

    t = HttpxTransport()

    # ---- B: Release + asset + re-download verify ----
    a = GitHubReleaseAdapter.armed(OWNER, REPO_B, token, t)
    tag = f"e2e-video/{nonce}"
    asset_name = f"video-{nonce}.mp4"
    rel = a.create_release(
        tag, name=f"E2E video {nonce}",
        body=f"autonomous E2E run nonce={nonce}; real Agnes t2v; sha256={mp4_sha}",
        target_commitish="main", draft=True)
    data = Path(mp4_path).read_bytes()
    meta = a.upload_asset_with_recovery(rel.id, asset_name, data, mp4_sha)
    blob = a.download_asset_authenticated(meta.id)
    dl_sha = hashlib.sha256(blob).hexdigest()
    if dl_sha != mp4_sha:
        raise RuntimeError(f"B asset sha mismatch: dl={dl_sha} != local={mp4_sha}")

    # ---- B1: Registry state machine via GitHubContentsIndex CAS bridge ----
    idx = task_index(token, nonce, transport=t)
    test_branch = f"test/e2e-{nonce}"
    ensure_branch(idx)

    cas = B1GitHubCas(idx)
    reg = Registry(cas=cas)  # _load_or_init reads B1 state.json (None first -> empty)
    pid = f"e2e_{nonce}"
    reg.create_project(project_id=pid, alias=f"E2E {nonce}",
                       operation_id=f"op_{nonce}_create")
    reg.record_activity(project_id=pid, kind="generate_video",
                       operation_id=f"op_{nonce}_act")
    reg.plan_archive(project_id=pid, operation_id=f"op_{nonce}_plan")
    reg.begin_snapshot(project_id=pid, snapshot_fingerprint=mp4_sha,
                       operation_id=f"op_{nonce}_snap")
    archive_ref = {
        "tree_fingerprint": mp4_sha, "sha256": mp4_sha,
        "release_tag": tag, "asset_name": asset_name,
        "asset_id": meta.id, "release_id": rel.id,
        "repo": f"{OWNER}/{REPO_B}", "bytes": len(data),
    }
    reg.set_verified_restorable(
        project_id=pid, archive_ref=archive_ref,
        restorable_level="full", operation_id=f"op_{nonce}_verify")
    final_doc = reg.to_doc()
    if not verified_record(final_doc, nonce):
        raise RecoveryRequired("Registry final receipt is incomplete")

    return {
        "b_release": {
            "tag": tag, "release_id": rel.id, "asset_id": meta.id,
            "asset_name": asset_name, "uploaded_bytes": meta.size,
            "download_sha256": dl_sha, "verified_bytes": True,
        },
        "b1_registry": {
            "branch": test_branch, "path": idx.path,
            "registry_revision": final_doc.get("registry_revision"),
            "activity_seq": final_doc.get("activity_seq"),
            "archive_status": "VERIFIED_RESTORABLE",
            "project_id": pid,
            "b1_url": f"https://github.com/{OWNER}/{REPO_B1}/blob/{test_branch}/{idx.path}",
        },
    }


def ensure_branch(idx):
    if idx.get_branch_head() is None:
        try:
            idx.create_branch_from("main", idx.branch)
        except Exception:
            # A concurrent creator or a lost create receipt is safe only after
            # confirming the branch exists; all authorization/read errors stop.
            if idx.get_branch_head() is None:
                raise


def prepare_checkpoint(token, nonce):
    from tools.relstore.github_release_adapter import GitHubReleaseAdapter, HttpxTransport
    idx = task_index(token, nonce, "production.json")
    ensure_branch(idx)
    cp = ProductionCheckpoint(idx, nonce, request_digest(PROMPT, AGNES_VIDEO_MODEL))
    if cp.current is None:
        # Legacy interrupted runs have no pre-submission journal. Never guess
        # that an absent state.json means the provider was never charged.
        state = task_index(token, nonce).read()
        release = GitHubReleaseAdapter.armed(OWNER, REPO_B, token, HttpxTransport())
        if state is not None or release.get_release_by_tag(f"e2e-video/{nonce}") is not None:
            raise RecoveryRequired("legacy partial run exists without a checkpoint; reconcile its saved media")
        cp.save(phase="READY")
    return cp


def main():
    Path("cache").mkdir(exist_ok=True)
    api_key, token = _env()
    n = _nonce()
    out_dir = Path("e2e_run") / n
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {"nonce": n,
               "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        state = task_index(token, n).read()
        rec = verified_record(None if state is None else state["doc"], n)
        if rec:
            ProductionCheckpoint(task_index(token, n, "production.json"), n,
                                 request_digest(PROMPT, AGNES_VIDEO_MODEL))
            summary.update(status="PASS", deduplicated=True,
                           archive_ref=rec["archive_ref"])
            return
        checkpoint = prepare_checkpoint(token, n)
        # 1. real Agnes t2v generation
        mp4 = out_dir / f"video-{n}.mp4"
        print(f"[1/4] generating real t2v video (nonce={n})...")
        gen = asyncio.run(gen_video(api_key, mp4, checkpoint))
        summary["gen"] = gen
        print(f"[1/4] done: video_id={gen['video_id']} seconds={gen.get('seconds')}")

        # 2. ffprobe playability
        print("[2/4] ffprobe check...")
        probe = ffprobe(mp4)
        summary["ffprobe"] = probe
        print(f"[2/4] done: playable={probe['playable']} duration={probe['duration_s']}s stream={probe['video_stream']}")
        if not probe["playable"]:
            raise RuntimeError(f"ffprobe says not playable: {probe}")

        # 3. sha256
        sha = sha256_file(mp4)
        old_sha = checkpoint.doc.get("mp4_sha256")
        if old_sha is not None and old_sha != sha:
            raise RecoveryRequired("provider download changed after the first verified MP4")
        checkpoint.save(phase="GENERATED", mp4_sha256=sha, mp4_bytes=mp4.stat().st_size)
        summary["mp4_sha256"] = sha
        summary["mp4_path"] = str(mp4)
        summary["mp4_bytes"] = mp4.stat().st_size
        print(f"[3/4] sha256={sha} bytes={summary['mp4_bytes']}")

        # 4. save to B Release + register on B1
        print("[4/4] save B Release + B1 registry CAS...")
        saved = save_and_register(token, mp4, sha, n)
        checkpoint.save(phase="ARCHIVED", save=saved)
        summary["save"] = saved
        print(f"[4/4] done: B release={saved['b_release']['tag']} "
              f"B1 rev={saved['b1_registry']['registry_revision']}")
        summary["status"] = "PASS"
    except Exception as e:
        summary["status"] = "FAIL"
        from tools.relstore.github_release_adapter import redact
        summary["error"] = redact(repr(e), token=token).replace(api_key, "<REDACTED>")
        summary["traceback"] = redact(traceback.format_exc(), token=token).replace(api_key, "<REDACTED>")
    finally:
        summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        summary_text = json.dumps(summary, ensure_ascii=False, indent=1, default=str)
        (out_dir / "summary.json").write_text(summary_text, encoding="utf-8")
        # Retain the original single-run evidence path for existing consumers.
        (out_dir.parent / "summary.json").write_text(summary_text, encoding="utf-8")
        print("=== E2E SUMMARY ===")
        print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
        raise SystemExit(0 if summary.get("status") == "PASS" else 1)


if __name__ == "__main__":
    main()
