"""Shared identity, completion and durable checkpoints for the single-clip runner.

This is not the Director/Planner/SceneRun project pipeline. A nonce identifies an
immutable request. An ambiguous paid submission is parked, never auto-resubmitted.
"""
from __future__ import annotations

import hashlib
import json
import re

OWNER = "Q-1844"
REPO_B = "agent-system-v2-rundata"
REPO_B1 = "agent-system-v2-rundata-1"


class RecoveryRequired(RuntimeError):
    """Existing evidence cannot safely authorize another paid submission."""


def validate_nonce(nonce):
    if not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", nonce):
        raise ValueError("nonce must be 1-64 ASCII letters, digits, underscores or hyphens")
    return nonce


def validate_task(task):
    if not isinstance(task, dict):
        raise ValueError("task must be an object")
    nonce = validate_nonce(task.get("nonce"))
    prompt = task.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
        raise ValueError("prompt must be a nonempty string of at most 12000 characters")
    return nonce, prompt


def request_digest(prompt, model):
    payload = {"prompt": prompt, "model": model, "mode": "t2v", "duration_s": 5}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def verified_record(doc, nonce):
    """Validate the exact task and archive receipt, including old rev5 records."""
    if not isinstance(doc, dict):
        return None
    projects = doc.get("projects")
    if not isinstance(projects, dict):
        return None
    rec = projects.get(f"e2e_{nonce}")
    if not isinstance(rec, dict) or rec.get("project_id") != f"e2e_{nonce}":
        return None
    ref = rec.get("archive_ref")
    if rec.get("archive_status") != "VERIFIED_RESTORABLE" or not isinstance(ref, dict):
        return None
    sha = ref.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        return None
    if (rec.get("snapshot_fingerprint") != sha or ref.get("tree_fingerprint") != sha
            or rec.get("missing_required") != []
            or ref.get("repo") != f"{OWNER}/{REPO_B}"
            or ref.get("release_tag") != f"e2e-video/{nonce}"
            or ref.get("asset_name") != f"video-{nonce}.mp4"
            or not str(ref.get("asset_id", "")).isdigit()
            or not str(ref.get("release_id", "")).isdigit()
            or type(ref.get("bytes")) is not int or ref["bytes"] <= 0):
        return None
    return rec


def task_index(token, nonce, filename="state.json", transport=None):
    from tools.relstore.github_release_adapter import GitHubContentsIndex, HttpxTransport
    validate_nonce(nonce)
    return GitHubContentsIndex.armed(
        OWNER, REPO_B1, f"test/e2e-{nonce}", f"e2e/{nonce}/{filename}",
        token, transport or HttpxTransport())


class ProductionCheckpoint:
    """Single-file CAS journal, saved before the first paid HTTP request."""

    def __init__(self, index, nonce, digest):
        self.index, self.nonce, self.digest = index, nonce, digest
        self.current = index.read()
        self.doc = {} if self.current is None else self.current["doc"]
        if self.current is not None and (
                not isinstance(self.doc, dict) or self.doc.get("schema_version") != 1
                or self.doc.get("nonce") != nonce
                or self.doc.get("request_sha256") != digest):
            raise RecoveryRequired("nonce is already bound to a different or invalid request")

    def save(self, **fields):
        # Do not refresh the expected SHA after a conflict or a lost receipt.
        # A new runner must read the authority and reconcile it first.
        candidate = dict(self.doc, **fields, schema_version=1,
                         nonce=self.nonce, request_sha256=self.digest)
        result = self.index.write_cas(
            candidate, None if self.current is None else self.current["blob_sha"],
            message=f"production checkpoint {self.nonce}: {candidate.get('phase')}")
        if not result.get("ok") or not result.get("new_sha"):
            raise RecoveryRequired("checkpoint write has no confirmed blob SHA")
        self.doc = candidate
        self.current = {"doc": candidate, "blob_sha": result["new_sha"]}
