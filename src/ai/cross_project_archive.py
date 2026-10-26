"""Safely hydrate a *named, previously archived* A project for C2 read-only use.

A production job starts on a fresh GitHub Actions worker, where previously
archived `cloud3min_<nonce>` projects normally do not exist under `projects/`.
This adapter reuses the existing Release archive/relstore verifier rather than
inventing a second provenance or archive format. No release is written.

Only uniquely resolved, SHA-verified archives are accepted. Nonstandard aliases
need an already-restored local project, because `alias -> release nonce` cannot
be inferred from an arbitrary friendly project name.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from src.ai.cross_project_assets import AssetUnavailable, safe_project_dir

MAX_SOURCE_ARCHIVE_BYTES = 1024 * 1024 * 1024


def _expected_release_tag(project_name: str) -> str:
    if not project_name.startswith('cloud3min_'):
        raise AssetUnavailable('Archived source alias cannot be mapped to a unique Release; '
                               'restore it locally first or provide the canonical cloud3min_<nonce> name')
    nonce = project_name[len('cloud3min_'):]
    from tools.video_task_state import validate_nonce
    try:
        validate_nonce(nonce)
    except ValueError as exc:
        raise AssetUnavailable('invalid archived source task nonce') from exc
    return f'full-project/{nonce}'


def _rebase_snapshot_paths(project_root: Path, source_name: str, destination: Path) -> None:
    """Rewrite only absolute local links under the archived `projects/<alias>`.

    Never rewrite a CDN URL, narrative text, SHA or arbitrary external path.
    Missing referenced files remain missing, so asset validation rejects them.
    """
    source_root = project_root.resolve()
    final_root = destination.resolve()

    def convert(value: Any) -> Any:
        if isinstance(value, str):
            if '://' in value or not value.startswith('/'):
                return value
            parts = PurePosixPath(value).parts
            indices = [i for i in range(len(parts) - 1)
                       if parts[i] == 'projects' and parts[i + 1] == source_name]
            if not indices:
                return value
            suffix = PurePosixPath(*parts[indices[-1] + 2:])
            if suffix.is_absolute() or '..' in suffix.parts:
                raise AssetUnavailable('unsafe path embedded in archived source')
            # Local links are rewritten only when the verified payload contains
            # the corresponding file. A missing link stays missing, never fake.
            if not (source_root / str(suffix)).resolve().is_relative_to(source_root):
                raise AssetUnavailable('archived local path escaped project root')
            return str(final_root / str(suffix))
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        return value

    for file in project_root.rglob('*.json'):
        doc = json.loads(file.read_text(encoding='utf-8'))
        update = convert(doc)
        if update != doc:
            file.write_text(json.dumps(update, ensure_ascii=False, indent=2), encoding='utf-8')


def ensure_archived_source_available(projects_root: Path, project_name: str,
                                    *, token: str = '', adapter=None,
                                    max_bytes: int = MAX_SOURCE_ARCHIVE_BYTES) -> dict:
    """Restore SHA-verified package as local source, never overwrite a project.

    Adapter injection makes the entire path independently mock-testable.
    `token` is used for an authenticated read only, not returned or logged.
    """
    root = Path(projects_root).resolve()
    destination = safe_project_dir(root, project_name, must_exist=False)
    if (destination / 'output/project.json').is_file():
        return {'origin': 'local_verified_candidate', 'project_name': project_name}
    if destination.exists():
        raise AssetUnavailable('incomplete project directory exists; cannot overwrite on archive restore')
    tag = _expected_release_tag(project_name)
    if adapter is None:
        if not token:
            raise AssetUnavailable('archive read token not available for source project recovery')
        from tools.relstore.github_release_adapter import GitHubReleaseAdapter, HttpxTransport
        adapter = GitHubReleaseAdapter.armed('Q-1844', 'agent-system-v2-rundata', token, HttpxTransport())
    release = adapter.get_release_by_tag(tag)
    if release is None:
        raise AssetUnavailable(f'no archived release found for project {project_name}')
    choices = [asset for asset in adapter.list_assets(release.id)
               if asset.name == 'project.tar' or
               (asset.name.startswith('project-') and asset.name.endswith('.tar'))]
    if len(choices) != 1:
        raise AssetUnavailable('source release has ambiguous/missing project snapshot; '
                               'restore a verified version locally instead')
    asset = choices[0]
    expected = (asset.sha256 or '').removeprefix('sha256:')
    if not re.fullmatch(r'[0-9a-fA-F]{64}', expected):
        raise AssetUnavailable('source archive lacks a verifiable SHA256 digest')
    if asset.size > max_bytes:
        raise AssetUnavailable('source archive exceeds bounded download size')
    data = adapter.download_asset_authenticated(asset.id)
    if len(data) > max_bytes or hashlib.sha256(data).hexdigest().lower() != expected.lower():
        raise AssetUnavailable('source archive body does not match the frozen SHA256')

    root.mkdir(parents=True, exist_ok=True)
    from tools.relstore.restore_campaign import restore_package
    with tempfile.TemporaryDirectory(prefix='.c2_source_', dir=root) as temporary:
        scratch = Path(temporary)
        package = scratch / 'source.tar'
        package.write_bytes(data)
        result = restore_package(package, scratch / 'restored', expected_sha256=expected,
                                 source_note=f'C2 readonly source release {tag}')
        source_root = Path(result['project_root'])
        if (result['alias'] != project_name or result['restorable_level'] != 'full' or
                not (source_root / 'output/project.json').is_file()):
            raise AssetUnavailable('restored project alias or integrity does not match requested source')
        canonical = json.loads((source_root / 'output/project.json').read_text(encoding='utf-8'))
        if canonical.get('name') != project_name:
            raise AssetUnavailable('archive canonical project name differs from expected source')
        _rebase_snapshot_paths(source_root, project_name, destination)
        # All integrity validation precedes visibility. Move same-filesystem
        # restored tree atomically; never overwrite an existing destination.
        if destination.exists():
            raise AssetUnavailable('target source project appeared during restoration')
        os.rename(source_root, destination)
    return {'origin': 'verified_archive', 'project_name': project_name,
            'release_tag': tag, 'release_id': release.id,
            'asset_id': asset.id, 'archive_sha256': expected.lower()}
