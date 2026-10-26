"""C2 archive discovery must use existing relstore, SHA256, and project identity."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ai.cross_project_assets import AssetUnavailable, read_source_project
from src.ai.cross_project_archive import (
    _expected_release_tag, _rebase_snapshot_paths,
    ensure_archived_source_available,
)
from tests.test_c2_cross_project import seed


class Adapter:
    def __init__(self, body: bytes, *, bad=False):
        self.body = body
        self.bad = bad
        self.calls = []

    def get_release_by_tag(self, tag):
        self.calls.append(('tag', tag))
        return SimpleNamespace(id='98')

    def list_assets(self, rid):
        self.calls.append(('assets', rid))
        return [SimpleNamespace(id='77', size=len(self.body), name='project.tar',
               sha256='f'*64 if self.bad else hashlib.sha256(self.body).hexdigest())]

    def download_asset_authenticated(self, asset_id):
        self.calls.append(('download', asset_id))
        return self.body


def test_nonstandard_alias_must_be_restored_locally_first():
    with pytest.raises(AssetUnavailable, match='mapped'):
        _expected_release_tag('friendly_project')
    assert _expected_release_tag('cloud3min_nonce123') == 'full-project/nonce123'


def test_archive_missing_or_corrupt_sha_does_not_create_project(tmp_path):
    root = tmp_path / 'projects'
    root.mkdir()
    a = Adapter(b'fake', bad=True)
    with pytest.raises(AssetUnavailable, match='SHA256'):
        ensure_archived_source_available(root, 'cloud3min_nonce123', adapter=a)
    assert not (root / 'cloud3min_nonce123').exists()
    assert a.calls[-1] == ('download', '77')


def test_verified_recovery_checks_alias_and_rebases_local_path(tmp_path, monkeypatch):
    root = tmp_path / 'projects'
    root.mkdir()
    name = 'cloud3min_nonce123'
    def fake_native_restore(package, dest, **kwargs):
        d = seed(Path(dest), name)
        doc_path = d / 'output' / 'project.json'
        doc = json.loads(doc_path.read_text())
        doc['scenes'][0]['shots'][0]['video_versions'][0]['local'] = (
            '/old/runner/projects/' + name + '/output/videos/source.mp4')
        doc_path.write_text(json.dumps(doc))
        return {'alias': name, 'restorable_level': 'full', 'project_root': str(d)}
    import tools.relstore.restore_campaign as restore
    monkeypatch.setattr(restore, 'restore_package', fake_native_restore)
    adapter = Adapter(b'fake verified tar body')
    result = ensure_archived_source_available(root, name, adapter=adapter)
    assert result['origin'] == 'verified_archive'
    assert result['release_tag'] == 'full-project/nonce123'
    project, digest = read_source_project(root, name)
    location = Path(project.scenes[0].shots[0].video_versions[0].local)
    assert location == root / name / 'output/videos/source.mp4'
    assert location.is_file()
    # Idempotent locally cached source never downloads again.
    calls = list(adapter.calls)
    again = ensure_archived_source_available(root, name, adapter=adapter)
    assert again['origin'] == 'local_verified_candidate' and adapter.calls == calls


def test_archive_rejects_ambiguous_snapshots(tmp_path):
    root = tmp_path / 'projects'
    root.mkdir()
    class Ambiguous(Adapter):
        def list_assets(self, rid):
            base = super().list_assets(rid)[0]
            return [base, SimpleNamespace(**{**vars(base), 'name': 'project-2.tar'})]
    with pytest.raises(AssetUnavailable, match='ambiguous'):
        ensure_archived_source_available(root, 'cloud3min_nonce123', adapter=Ambiguous(b'x'))
