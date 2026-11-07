"""Derived publication waits; creative assets and their signatures stay unchanged."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

CATEGORY = 'MEDIA_PUBLICATION_PENDING'


def publication_wait_path(project_dir, shot_id, version_id):
    key = hashlib.sha256(json.dumps([shot_id, version_id]).encode()).hexdigest()
    return Path(project_dir) / 'output' / 'infrastructure_waits' / (key + '.json')


def record_publication_wait(project_dir, shot_id, version_id, local, error):
    from .store import _atomic_write_json
    from ..resources.generation import resolve_local_file
    root = Path(project_dir).resolve()
    path = resolve_local_file(root, local)
    if path is None or not path.resolve().is_relative_to(root):
        raise ValueError('publication wait requires the extracted frame inside this project')
    doc = dict(state='pending', category=CATEGORY, shot_id=shot_id,
               video_version_id=version_id, local=path.resolve().relative_to(root).as_posix(),
               sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
               error=str(error)[:500], updated_at=time.time())
    target = publication_wait_path(root, shot_id, version_id)
    _atomic_write_json(target, doc)
    return target


def pending_publication(project_dir, shot_id, version_id, local):
    from ..resources.generation import resolve_local_file
    try:
        root = Path(project_dir).resolve()
        doc = json.loads(publication_wait_path(root, shot_id, version_id).read_text(encoding='utf-8'))
        if (not isinstance(doc, dict) or doc.get('state') != 'pending' or doc.get('category') != CATEGORY
                or doc.get('shot_id') != shot_id or doc.get('video_version_id') != version_id):
            return False
        path = resolve_local_file(root, local)
        recorded = (root / doc['local']).resolve()
        return bool(path and path.resolve() == recorded and recorded.is_relative_to(root)
                    and path.stat().st_size and hashlib.sha256(path.read_bytes()).hexdigest() == doc['sha256'])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def resolve_publication_wait(project_dir, shot_id, version_id):
    from .store import _atomic_write_json
    target = publication_wait_path(project_dir, shot_id, version_id)
    if not target.exists():
        return
    doc = json.loads(target.read_text(encoding='utf-8'))
    if not isinstance(doc, dict) or doc.get('shot_id') != shot_id or doc.get('video_version_id') != version_id:
        raise ValueError('publication wait belongs to another asset')
    doc.update(state='resolved', updated_at=time.time())
    _atomic_write_json(target, doc)
