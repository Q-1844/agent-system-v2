"""Single-clip runner fault tests. No provider or GitHub network calls."""
import asyncio
import copy
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import e2e_autonomous_run as e2e, patrol_run as patrol
from tools import video_runtime_preflight as preflight
from tools.relstore.errors import AssetConflict
from tools.relstore.github_release_adapter import GitHubContentsIndex, MockHttpTransport
from tools.relstore.project_registry import CasConflict
from tools.video_task_state import (ProductionCheckpoint, RecoveryRequired,
                                   request_digest, validate_task, verified_record)


class MemoryIndex:
    def __init__(self):
        self.current = None
        self.writes = 0
        self.branch = "test/e2e-demo1"
        self.path = "e2e/demo1/state.json"

    def get_branch_head(self):
        return "existing-branch"

    def read(self):
        return copy.deepcopy(self.current)

    def write_cas(self, doc, expected_blob_sha, **kw):
        actual = None if self.current is None else self.current["blob_sha"]
        if actual != expected_blob_sha:
            raise AssetConflict("stale SHA")
        self.writes += 1
        self.current = {"doc": copy.deepcopy(doc), "blob_sha": str(self.writes)}
        return {"ok": True, "new_sha": str(self.writes)}


def complete_doc(nonce="demo1"):
    sha = "a" * 64
    return {"projects": {f"e2e_{nonce}": {
        "project_id": f"e2e_{nonce}", "snapshot_fingerprint": sha,
        "archive_status": "VERIFIED_RESTORABLE", "missing_required": [],
        "archive_ref": {"sha256": sha, "tree_fingerprint": sha,
                        "repo": "Q-1844/agent-system-v2-rundata",
                        "release_tag": f"e2e-video/{nonce}",
                        "asset_name": f"video-{nonce}.mp4", "asset_id": "22",
                        "release_id": "11", "bytes": 10}}}}


@pytest.mark.parametrize("status", ["NONE", "PLANNED", "SNAPSHOTTING", "UPLOADING", "FAILED", "SUPERSEDED"])
def test_partial_registry_is_not_done(status):
    doc = complete_doc()
    doc["projects"]["e2e_demo1"]["archive_status"] = status
    assert not verified_record(doc, "demo1")


def test_terminal_receipt_is_bound_to_task_and_bytes():
    doc = complete_doc()
    assert verified_record(doc, "demo1")
    assert not verified_record(doc, "demo2")
    doc["projects"]["e2e_demo1"]["archive_ref"]["tree_fingerprint"] = "b" * 64
    assert not verified_record(doc, "demo1")


def test_empty_queue_skips_media_setup_without_secrets(tmp_path):
    assert not preflight.needs_media("patrol", None, pending_dir=tmp_path)


@pytest.mark.parametrize("complete", [False, True])
def test_read_only_preflight_checks_real_terminal_state(monkeypatch, complete):
    idx = MemoryIndex()
    if complete:
        idx.write_cas(complete_doc(), None)
    monkeypatch.setattr(preflight, "task_index", lambda *a: idx)
    assert preflight.needs_media("e2e", "fake", nonce="demo1") is (not complete)


def test_preflight_read_error_does_not_authorize_work(monkeypatch):
    def broken(*a):
        raise RuntimeError("remote lookup unavailable")
    monkeypatch.setattr(preflight, "task_index", broken)
    with pytest.raises(RuntimeError):
        preflight.needs_media("e2e", "fake", nonce="demo1")


def test_fresh_nonce_requires_media_setup():
    assert preflight.needs_media("e2e", "fake")


@pytest.mark.parametrize("task", [[], {}, {"nonce": "../oops", "prompt": "p"},
                                 {"nonce": "x?ref=main", "prompt": "p"},
                                 {"nonce": "a", "prompt": 1},
                                 {"nonce": "a", "prompt": "  "}])
def test_malformed_task_is_rejected(task):
    with pytest.raises(ValueError):
        validate_task(task)


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_lookup_errors_cannot_authorize_generation(monkeypatch, status):
    transport = MockHttpTransport(scripted=[MockHttpTransport.json_response(status, {"message": "stop"})])
    idx = GitHubContentsIndex.armed("Q-1844", "r", "b", "p", "fake-token", transport)
    monkeypatch.setattr(patrol, "task_index", lambda *a: idx)
    with pytest.raises(Exception):
        patrol.already_produced("fake-token", "demo1")


def checkpoint(index=None):
    return ProductionCheckpoint(index or MemoryIndex(), "demo1", request_digest(e2e.PROMPT, e2e.AGNES_VIDEO_MODEL))


def test_checkpoint_has_one_CAS_winner():
    idx = MemoryIndex()
    first, second = checkpoint(idx), checkpoint(idx)
    first.save(phase="READY")
    with pytest.raises(AssetConflict):
        second.save(phase="READY")
    with pytest.raises(RecoveryRequired):
        ProductionCheckpoint(idx, "demo1", "different request")


@pytest.fixture
def provider(monkeypatch):
    class Provider:
        creates = 0
        polls = 0
        fail_create = False
        fail_download = False

        def __init__(self, cfg):
            pass

        async def create_video(self, *a, **kw):
            type(self).creates += 1
            if self.fail_create:
                raise TimeoutError("response lost after submission")
            return {"video_id": "known-video"}

        async def poll_video(self, vid, **kw):
            assert vid == "known-video"
            type(self).polls += 1
            return {"url": "https://example.invalid/video.mp4", "seconds": 5}

        async def download(self, url, path):
            if self.fail_download:
                raise TimeoutError("download interrupted")
            Path(path).write_bytes(b"existing-mp4-bytes")
            return path

        async def aclose(self):
            pass

    import src.api.agnes_client as agnes
    monkeypatch.setattr(agnes, "AgnesClient", Provider)
    return Provider


def test_ambiguous_paid_submission_is_never_repeated(provider, tmp_path):
    cp = checkpoint()
    cp.save(phase="READY")
    provider.fail_create = True
    with pytest.raises(TimeoutError):
        asyncio.run(e2e.gen_video("fake", tmp_path / "v.mp4", cp))
    provider.fail_create = False
    with pytest.raises(RecoveryRequired):
        asyncio.run(e2e.gen_video("fake", tmp_path / "v.mp4", checkpoint(cp.index)))
    assert provider.creates == 1


def test_download_interruption_resumes_same_video(provider, tmp_path):
    cp = checkpoint()
    cp.save(phase="READY")
    provider.fail_download = True
    with pytest.raises(TimeoutError):
        asyncio.run(e2e.gen_video("fake", tmp_path / "v.mp4", cp))
    assert cp.doc["gen"]["video_id"] == "known-video"
    provider.fail_download = False
    gen = asyncio.run(e2e.gen_video("fake", tmp_path / "v.mp4", checkpoint(cp.index)))
    assert gen["video_id"] == "known-video" and provider.creates == 1


@pytest.mark.parametrize("accepted", [False, True])
def test_submission_checkpoint_receipt_loss_never_duplicates_paid_POST(provider, tmp_path, accepted):
    class FaultIndex(MemoryIndex):
        calls = 0
        def write_cas(self, doc, expected_blob_sha, **kw):
            self.calls += 1
            if self.calls == 3:  # provider accepted; saving its video ID
                if accepted:
                    super().write_cas(doc, expected_blob_sha, **kw)
                raise TimeoutError("checkpoint receipt lost")
            return super().write_cas(doc, expected_blob_sha, **kw)
    cp = checkpoint(FaultIndex())
    cp.save(phase="READY")
    with pytest.raises(TimeoutError):
        asyncio.run(e2e.gen_video("fake", tmp_path / "v.mp4", cp))
    restarted = checkpoint(cp.index)
    if accepted:
        asyncio.run(e2e.gen_video("fake", tmp_path / "v.mp4", restarted))
    else:
        with pytest.raises(RecoveryRequired):
            asyncio.run(e2e.gen_video("fake", tmp_path / "v.mp4", restarted))
    assert provider.creates == 1


def test_concurrent_READY_readers_have_one_paid_submission(provider, tmp_path):
    first = checkpoint()
    first.save(phase="READY")
    second = checkpoint(first.index)
    asyncio.run(e2e.gen_video("fake", tmp_path / "v.mp4", first))
    with pytest.raises(AssetConflict):
        asyncio.run(e2e.gen_video("fake", tmp_path / "other.mp4", second))
    assert provider.creates == 1


def test_completed_nonce_rejects_changed_request(monkeypatch):
    idx = MemoryIndex()
    checkpoint(idx).save(phase="ARCHIVED")
    state = MemoryIndex()
    state.write_cas(complete_doc(), None)
    monkeypatch.setattr(patrol, "task_index", lambda *a: idx if len(a) == 3 else state)
    with pytest.raises(RecoveryRequired):
        patrol.already_produced("fake", "demo1", "different prompt")


def test_CAS_bridge_rejects_wrong_revision_and_remote_conflict():
    idx = MemoryIndex()
    bridge = e2e.B1GitHubCas(idx)
    bridge.read()
    bridge.commit({"registry_revision": 1}, None, operation_id="first")
    with pytest.raises(CasConflict):
        bridge.commit({"registry_revision": 2}, 0, operation_id="bad")
    # Another writer advances after our successful receipt.
    idx.write_cas({"registry_revision": 2}, "1")
    with pytest.raises(CasConflict):
        bridge.commit({"registry_revision": 2}, 1, operation_id="stale")


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("B1_ARCHIVE_TOKEN", "fake-token")
    monkeypatch.setenv("AGNES_API_KEY", "fake-key")
    pending = tmp_path / "tasks" / "pending"
    pending.mkdir(parents=True)
    (pending / "task.json").write_text(json.dumps({"nonce": "demo1", "prompt": "p"}))
    return pending


def test_idle_needs_no_credentials(queue, monkeypatch):
    (queue / "task.json").unlink()
    monkeypatch.delenv("B1_ARCHIVE_TOKEN")
    monkeypatch.delenv("AGNES_API_KEY")
    assert patrol.main() == 0


def test_conflicting_task_copies_are_rejected_before_generation(queue, monkeypatch):
    (queue / "other.json").write_text(json.dumps({"nonce": "demo1", "prompt": "different"}))
    def forbidden(*a):
        pytest.fail("conflicting tasks must not reach the provider")
    monkeypatch.setattr(patrol, "run_e2e", forbidden)
    assert patrol.main() == 1
    assert json.loads(Path("e2e_run/patrol-summary.json").read_text())["failed"] == 2


@pytest.mark.parametrize("returncode,nonce,write_summary", [(1, "demo1", True), (0, "other", True), (0, "demo1", False)])
def test_stale_or_wrong_PASS_does_not_hide_child_failure(queue, monkeypatch, returncode, nonce, write_summary):
    monkeypatch.setattr(patrol, "already_produced", lambda *a: False)
    stale = Path("e2e_run/demo1/summary.json")
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps({"nonce": "demo1", "status": "PASS"}))
    def child(*a):
        if write_summary:
            stale.write_text(json.dumps({"nonce": nonce, "status": "PASS"}))
        return SimpleNamespace(returncode=returncode)
    monkeypatch.setattr(patrol, "run_e2e", child)
    assert patrol.main() == 1
    assert json.loads(Path("e2e_run/patrol-summary.json").read_text())["failed"] == 1


def test_timeout_is_recorded_as_failure(queue, monkeypatch):
    monkeypatch.setattr(patrol, "already_produced", lambda *a: False)
    def child(*a):
        raise subprocess.TimeoutExpired("e2e", 900)
    monkeypatch.setattr(patrol, "run_e2e", child)
    assert patrol.main() == 1


def test_success_requires_fresh_authoritative_receipt(queue, monkeypatch):
    checks = iter([False, True])
    monkeypatch.setattr(patrol, "already_produced", lambda *a: next(checks))
    def child(*a):
        p = Path("e2e_run/demo1/summary.json")
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"nonce": "demo1", "status": "PASS"}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(patrol, "run_e2e", child)
    assert patrol.main() == 0
    assert json.loads(Path("e2e_run/patrol-summary.json").read_text())["produced"] == 1


@pytest.mark.parametrize("failed_write", range(1, 6))
@pytest.mark.parametrize("receipt_lost", [False, True])
def test_B_saved_B1_interruption_resumes_without_paid_regeneration(
        provider, tmp_path, monkeypatch, failed_write, receipt_lost):
    """Crash at every Registry commit, both before and after remote acceptance."""
    from tools.relstore import github_release_adapter as gh

    class FaultIndex(MemoryIndex):
        calls = 0
        def write_cas(self, doc, expected_blob_sha, **kw):
            self.calls += 1
            if self.calls == failed_write:
                if receipt_lost:
                    super().write_cas(doc, expected_blob_sha, **kw)
                raise TimeoutError("Registry receipt lost")
            return super().write_cas(doc, expected_blob_sha, **kw)

    class Releases:
        exists = False
        data = None
        unique_uploads = 0
        def get_release_by_tag(self, tag):
            return SimpleNamespace(id="11", tag=tag) if self.exists else None
        def create_release(self, tag, **kw):
            self.exists = True
            return SimpleNamespace(id="11", tag=tag)
        def upload_asset_with_recovery(self, rid, name, data, sha):
            assert hashlib.sha256(data).hexdigest() == sha
            if self.data is None:
                self.data = data
                self.unique_uploads += 1
            assert self.data == data
            return SimpleNamespace(id="22", size=len(data))
        def download_asset_authenticated(self, aid):
            return self.data

    state, cp_index, releases = FaultIndex(), MemoryIndex(), Releases()
    cp_index.path = "e2e/demo1/production.json"
    def index(token, nonce, filename="state.json", **kw):
        return cp_index if filename == "production.json" else state
    monkeypatch.setattr(e2e, "task_index", index)
    monkeypatch.setattr(gh, "HttpxTransport", lambda: None)
    monkeypatch.setattr(gh.GitHubReleaseAdapter, "armed", lambda *a: releases)
    monkeypatch.setattr(e2e, "ffprobe", lambda p: {"playable": True, "duration_s": 5, "video_stream": "h264,1280,720"})
    monkeypatch.setenv("TASK_NONCE", "demo1")
    monkeypatch.setenv("AGNES_API_KEY", "fake-key")
    monkeypatch.setenv("B1_ARCHIVE_TOKEN", "fake-token")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as first:
        e2e.main()
    assert first.value.code == 1
    assert releases.data is not None
    # A fresh runner has no original MP4, only the remote checkpoints.
    Path("e2e_run/demo1/video-demo1.mp4").unlink()
    with pytest.raises(SystemExit) as second:
        e2e.main()
    assert second.value.code == 0
    assert provider.creates == 1 and releases.unique_uploads == 1
    assert verified_record(state.read()["doc"], "demo1")
