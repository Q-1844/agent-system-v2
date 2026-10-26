"""A/C2: deterministic read-only discovery of media from other A projects.

A project's canonical ``output/project.json`` and ``Shot.final_video`` are
sources of truth. This is intentionally *not* another writable resource
registry: it complements the existing SharedLibraryService (character/looks)
with project Shot/Version discovery. No LLM, network, media generation, or
filesystem mutation is performed by catalog discovery.

Direct reuse requires a DONE selected version and a nonempty *local* file
inside its source project, bound to a SHA256. URL-only material is reference-
only until an independently verified archive restore makes it local.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.models import Project


class AssetUnavailable(RuntimeError):
    """A selected source asset cannot be verified against its frozen identity."""


def safe_project_dir(projects_root: Path, name: str, *, must_exist: bool = True) -> Path:
    """Disallow traversal/symlink aliases; this checks source_project from workflow."""
    if (not isinstance(name, str) or not name or len(name) > 120 or
            name in ('.', '..') or '/' in name or '\\' in name or
            any(ord(ch) < 32 for ch in name)):
        raise ValueError('invalid source project name')
    root = Path(projects_root).resolve()
    target = root / name
    if target.resolve() != target.absolute():
        raise ValueError('source project symlink/traversal is not authorized')
    if must_exist and not (target / 'output' / 'project.json').is_file():
        raise FileNotFoundError(f'project source has no canonical document: {name}')
    return target


def read_source_project(projects_root: Path, name: str) -> tuple[Project, str]:
    """Read project.json *without* ProjectStore.load's migration/backfill writes."""
    path = safe_project_dir(projects_root, name) / 'output' / 'project.json'
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or int(data.get('schema_version') or 0) < 2:
        raise ValueError('C2 requires a version-2 canonical source project')
    project = Project.from_dict(data)
    if project.name != name:
        raise ValueError('source project identity/name mismatch')
    return project, hashlib.sha256(raw).hexdigest()


def local_asset_file(project_dir: Path, local: Optional[str]) -> Optional[Path]:
    """Resolve an existing nonempty local media file without accepting escapes."""
    if not isinstance(local, str) or not local.strip():
        return None
    root = Path(project_dir).resolve()
    candidate = Path(local)
    # ProjectStore keeps older relative paths that are already prefixed with
    # `projects/<name>/...` (relative to the working directory), while some
    # versions store paths relative to the project root. Accept either only
    # if it resolves to a real file *inside the same project*.
    choices = ([candidate] if candidate.is_absolute() else
               [root / candidate, Path.cwd() / candidate])
    for item in choices:
        resolved = item.resolve()
        if (resolved.is_relative_to(root) and resolved.is_file() and
                resolved.stat().st_size > 0):
            return resolved
    return None


def _fingerprint_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _overlap_score(query: str, haystack: str) -> int:
    """Conservative, deterministic bigram matching for Chinese/English queries."""
    def terms(value: str):
        value = re.sub(r'\s+', ' ', value.lower())
        chars = ''.join(ch for ch in value if '\u4e00' <= ch <= '\u9fff')
        bi = {chars[i:i+2] for i in range(len(chars)-1)}
        latin = set(re.findall(r'[a-z][a-z0-9_-]{2,}', value))
        return bi | latin
    q = terms(query)
    return len(q & terms(haystack)) if q else 0


def discover_project_assets(projects_root: Path, request_text: str,
                            *, source_project: Optional[str] = None,
                            exclude_project: Optional[str] = None,
                            limit: int = 12) -> List[Dict[str, Any]]:
    """Return validated, provenance-bearing candidates; no writes or paid calls.

    A selected DONE video can be ``direct_reuse_eligible`` only if it has a
    local nonempty file and stable SHA. Other media is available strictly as
    reference context, never as an already-generated finished clip.
    """
    if not 0 <= limit <= 100:
        raise ValueError('invalid candidate limit')
    root = Path(projects_root)
    if source_project:
        names = [source_project]
        # Other projects can additionally supply references. No implicit reuse.
        if root.is_dir():
            names += sorted(p.name for p in root.iterdir() if p.is_dir()
                            and p.name not in (source_project, exclude_project))
    else:
        names = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    results = []
    for name in names:
        if name == exclude_project:
            continue
        try:
            project, document_sha = read_source_project(root, name)
        except (ValueError, OSError, KeyError, TypeError):
            if name == source_project:
                raise  # An explicit source cannot silently become a different project
            continue
        base = safe_project_dir(root, name)
        for scene in project.scenes:
            for shot in scene.shots:
                context = ' '.join([project.story_text[-400:], scene.title, scene.summary,
                                    shot.description, shot.prompt_en, project.style.art_style])
                score = _overlap_score(request_text, context)
                if source_project != name and score == 0:
                    continue
                for version in shot.video_versions:
                    if shot.final_video != version.version_id or version.status != 'done':
                        continue
                    verified = local_asset_file(base, version.local)
                    if verified is None and not version.url:
                        continue
                    kind = 'video'
                    item = {
                        'source_kind': 'project', 'source_project': name,
                        'source_document_sha256': document_sha,
                        'scene_id': scene.scene_id, 'shot_id': shot.shot_id,
                        'version_id': version.version_id, 'kind': kind,
                        'source_relative_path': (verified.relative_to(base).as_posix() if verified else None),
                        'sha256': _fingerprint_file(verified) if verified else None,
                        'bytes': verified.stat().st_size if verified else None,
                        'video_id': version.video_id,
                        'seconds': version.seconds_returned or shot.target_duration_s,
                        'direct_reuse_eligible': bool(verified),
                        'description': (shot.description or scene.summary)[:500],
                        'score': score + (3 if source_project == name else 0),
                    }
                    results.append(item)
                # A selected local first frame is suitable as a reference,
                # never proof that a video clip already exists.
                if shot.first_frame.selected is not None:
                    asset = shot.first_frame.selected
                    checked = local_asset_file(base, asset.local)
                    if checked or asset.url:
                        results.append({
                            'source_kind': 'project', 'source_project': name,
                            'source_document_sha256': document_sha,
                            'scene_id': scene.scene_id, 'shot_id': shot.shot_id,
                            'version_id': shot.selected_first_frame or 'legacy-first-frame',
                            'kind': 'image',
                            'source_relative_path': checked.relative_to(base).as_posix() if checked else None,
                            'sha256': _fingerprint_file(checked) if checked else None,
                            'bytes': checked.stat().st_size if checked else None,
                            'direct_reuse_eligible': False,
                            'description': (shot.description or scene.summary)[:500],
                            'score': score + (3 if source_project == name else 0),
                        })
        # Production Resources V1 holds curated reference images that are not
        # attached to a Shot. Expose them as read-only reference candidates too,
        # instead of inventing a second image registry for C2.
        from src.resources.store import ResourceStore
        from src.resources.models import ResourceSchemaError
        try:
            refs = ResourceStore(base).load().reference_assets
        except (ResourceSchemaError, OSError, ValueError) as exc:
            if source_project == name:
                raise AssetUnavailable('explicit source resource catalog is unreadable') from exc
            continue  # An unrelated broken catalog cannot block all discovery.
        for asset in refs.values():
            if asset.media_type != 'image':
                continue
            context = ' '.join([project.story_text[-400:], project.style.art_style,
                                asset.purpose, asset.view, asset.source])
            score = _overlap_score(request_text, context)
            if source_project != name and score == 0:
                continue
            checked = local_asset_file(base, asset.local)
            if checked is None and not asset.url:
                continue
            actual_sha = _fingerprint_file(checked) if checked else None
            declared_sha = (asset.checksum or '').removeprefix('sha256:').lower()
            if declared_sha and actual_sha and declared_sha != actual_sha:
                # A mismatched stored checksum is not a safe reference.
                continue
            results.append({
                'source_kind': 'resource', 'source_project': name,
                'source_document_sha256': document_sha,
                'scene_id': '', 'shot_id': asset.asset_id,
                'version_id': asset.asset_id, 'kind': 'image',
                'source_relative_path': checked.relative_to(base).as_posix() if checked else None,
                'sha256': actual_sha, 'bytes': checked.stat().st_size if checked else None,
                'direct_reuse_eligible': False,
                'description': f'{asset.purpose} {asset.view}'.strip()[:500],
                'score': score + (3 if source_project == name else 0),
            })
    # Stable ordering: favor relevant candidates while using identity tie-breaks.
    results.sort(key=lambda a: (-a['score'], a['source_project'], a['scene_id'],
                                a['shot_id'], a['kind'], a['version_id']))
    return results[:limit]


def select_asset_uses(candidates: List[Dict[str, Any]], request_text: str,
                      *, max_reference: int = 3,
                      direct_exclude_project: Optional[str] = None) -> List[Dict[str, Any]]:
    """Producer-planning candidate decisions, frozen *before* paid production.

    Direct video reuse is explicit-only; ordinary semantic matches are context
    references. The actual reuse operation validates the frozen SHA again.
    """
    direct = any(word in (request_text or '') for word in
                 ('直接复用', '直接使用原镜头', '复用原片段', '直接拼入', '直接引用原视频'))
    uses = []
    taken_direct = False
    for item in candidates:
        if (not taken_direct and direct and item['kind'] == 'video' and
                item['direct_reuse_eligible'] and
                item['source_project'] != direct_exclude_project):
            kind, taken_direct = 'reuse_media', True
        elif len([u for u in uses if u['action'] == 'reference_only']) < max_reference:
            kind = 'reference_only'
        else:
            continue
        stable = {key: item[key] for key in (
            'source_kind', 'source_project', 'scene_id', 'shot_id', 'version_id',
            'kind', 'source_relative_path', 'sha256', 'bytes', 'video_id',
            'seconds', 'description') if key in item}
        if kind == 'reuse_media' and (not stable.get('sha256') or not stable.get('source_relative_path')):
            raise AssetUnavailable('direct reuse requires a verified local source')
        uses.append({'action': kind, 'evidence': stable})
    return uses


def verify_asset_use(projects_root: Path, use: Dict[str, Any]) -> Optional[Path]:
    """Revalidate a frozen version/byte identity before any copy or use."""
    ev = use['evidence']
    source, _ = read_source_project(projects_root, ev['source_project'])
    if ev.get('source_kind') == 'resource':
        from src.resources.store import ResourceStore
        base = safe_project_dir(projects_root, ev['source_project'])
        asset = ResourceStore(base).get_reference_asset(ev['version_id'])
        if (asset is None or asset.asset_id != ev['shot_id'] or
                asset.media_type != 'image' or ev['kind'] != 'image' or
                use['action'] != 'reference_only'):
            raise AssetUnavailable('source reference asset identity has changed')
        relative = ev.get('source_relative_path')
        if relative is None:
            return None  # URL-only reference is symbolic; never direct reuse.
        originally = local_asset_file(base, asset.local)
        resolved = local_asset_file(base, relative)
        if originally is None or resolved is None or originally != resolved:
            raise AssetUnavailable('frozen reference asset local identity changed')
        sha = _fingerprint_file(resolved)
        declared = (asset.checksum or '').removeprefix('sha256:').lower()
        if sha != ev.get('sha256') or (declared and declared != sha):
            raise AssetUnavailable('frozen reference asset SHA256 mismatch')
        return resolved
    shot = source.get_shot(ev['shot_id'])
    if shot is None or (ev['kind'] == 'video' and
                        (shot.final_video != ev['version_id'] or
                         not any(v.version_id == ev['version_id'] and v.status == 'done'
                                 for v in shot.video_versions))) or (
        ev['kind'] == 'image' and
        (shot.selected_first_frame or 'legacy-first-frame') != ev['version_id']
    ):
        raise AssetUnavailable('source shot/version changed since plan freeze')
    relative = ev.get('source_relative_path')
    if relative is None:
        if use['action'] == 'reuse_media':
            raise AssetUnavailable('unresolved remote-only media cannot be reused')
        return None
    path = local_asset_file(safe_project_dir(projects_root, ev['source_project']), relative)
    if path is None or not ev.get('sha256') or _fingerprint_file(path) != ev['sha256']:
        raise AssetUnavailable('frozen source media missing or SHA256 mismatch')
    return path
