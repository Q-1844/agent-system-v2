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
    return n if n else f"e2e-{int(time.time())}"


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
        # expected_revision is the Registry's int revision; GitHub concurrency
        # uses the blob sha we captured at read time. (expected_revision ignored
        # here — single-writer E2E; the blob sha is the real guard.)
        expected_blob = None if self._cur is None else self._cur["blob_sha"]
        r = self.idx.write_cas(new_doc, expected_blob,
                               message=f"registry op={operation_id}")
        self._cur = self.idx.read()  # reload so next op sees the new blob
        return {"ok": True,
                "revision": new_doc.get("registry_revision", 0),
                "http": r.get("http"),
                "new_blob_sha": r.get("new_sha")}


async def gen_video(api_key, out_path):
    from src.api.agnes_client import AgnesClient
    cfg = {"base_url": AGNES_BASE, "api_key": api_key,
           "models": {"video": AGNES_VIDEO_MODEL,
                      "text": "agnes-3.0-flash",
                      "image": "agnes-image-2.1-flash"},
           "proxy": None, "rpm": None}
    client = AgnesClient(cfg)
    try:
        receipt = await client.create_video(PROMPT, mode="t2v", duration_s=5)
        vid = receipt.get("video_id") or receipt.get("task_id")
        if not vid:
            raise RuntimeError(f"no video_id in receipt: {receipt}")
        done = await client.poll_video(vid)
        url = done["url"]
        path = await client.download(url, str(out_path))
        return {"video_id": vid, "url": url, "path": str(path),
                "seconds": done.get("seconds"), "size": done.get("size")}
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
    meta = a.upload_asset(rel.id, asset_name, data, mp4_sha)
    blob = a.download_asset_authenticated(meta.id)
    dl_sha = hashlib.sha256(blob).hexdigest()
    if dl_sha != mp4_sha:
        raise RuntimeError(f"B asset sha mismatch: dl={dl_sha} != local={mp4_sha}")

    # ---- B1: Registry state machine via GitHubContentsIndex CAS bridge ----
    idx = GitHubContentsIndex.armed(OWNER, REPO_B1, "", "", token, t)
    test_branch = f"test/e2e-{nonce}"
    if idx.get_branch_head(test_branch) is not None:
        raise RuntimeError(f"branch exists (nonce clash): {test_branch}")
    idx.create_branch_from("main", test_branch)
    idx.branch = test_branch
    idx.path = f"e2e/{nonce}/state.json"

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


def main():
    Path("cache").mkdir(exist_ok=True)
    api_key, token = _env()
    n = _nonce()
    out_dir = Path("e2e_run")
    out_dir.mkdir(exist_ok=True)
    summary = {"nonce": n,
               "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        # 1. real Agnes t2v generation
        mp4 = out_dir / f"video-{n}.mp4"
        print(f"[1/4] generating real t2v video (nonce={n})...")
        gen = asyncio.run(gen_video(api_key, mp4))
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
        summary["mp4_sha256"] = sha
        summary["mp4_path"] = str(mp4)
        summary["mp4_bytes"] = mp4.stat().st_size
        print(f"[3/4] sha256={sha} bytes={summary['mp4_bytes']}")

        # 4. save to B Release + register on B1
        print("[4/4] save B Release + B1 registry CAS...")
        saved = save_and_register(token, mp4, sha, n)
        summary["save"] = saved
        print(f"[4/4] done: B release={saved['b_release']['tag']} "
              f"B1 rev={saved['b1_registry']['registry_revision']}")
        summary["status"] = "PASS"
    except Exception as e:
        summary["status"] = "FAIL"
        summary["error"] = repr(e)
        summary["traceback"] = traceback.format_exc()
    finally:
        summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (out_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8")
        print("=== E2E SUMMARY ===")
        print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
        raise SystemExit(0 if summary.get("status") == "PASS" else 1)


if __name__ == "__main__":
    main()
