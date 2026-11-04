import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from tools import full_project_resume as recovery


def transport(monkeypatch, *, declared=None, bad_digest=False):
    content = io.BytesIO()
    with zipfile.ZipFile(content, 'w') as archive:
        archive.writestr('projects/cloud3min_unit/output/clip.mp4', b'video-bytes' * 5000)
        archive.writestr('e2e_run/unit/summary.json', '{}')
    data = content.getvalue();calls = []
    class Stream(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= recovery.COPY_CHUNK_BYTES, 'artifact must not be read into RAM in full'
            return super().read(size)
    def request(req, timeout):
        path = req.full_url;calls.append(path)
        if path.endswith('/actions/runs/10'):
            return io.BytesIO(json.dumps(dict(status='completed', conclusion='failure', head_sha='source')).encode())
        if path.endswith('/artifacts'):
            asset = dict(id=1, name='e2e-evidence', expired=False,
                         digest='sha256:'+('bad' if bad_digest else hashlib.sha256(data).hexdigest()))
            if declared != 'absent':asset['size_in_bytes'] = len(data) if declared is None else declared
            return io.BytesIO(json.dumps({'artifacts':[asset]}).encode())
        return Stream(data)
    monkeypatch.setattr(recovery.urllib.request, 'build_opener', lambda *a:SimpleNamespace(open=request))
    return data, calls


def test_snapshot_streams_network_and_zip_members_with_bounded_memory(tmp_path, monkeypatch):
    data, calls = transport(monkeypatch)
    monkeypatch.setattr(recovery, 'COPY_CHUNK_BYTES', 4096)
    # The former implementation used ZipFile.read() for each complete file.
    monkeypatch.setattr(zipfile.ZipFile, 'read', lambda *a:pytest.fail('whole-member buffering'))
    result = recovery.download_snapshot('10', 'unit', 'fake-token', tmp_path/'stage')
    assert result['artifact_bytes'] == len(data) and result['artifact_sha256'] == hashlib.sha256(data).hexdigest()
    assert (tmp_path/'stage/projects/cloud3min_unit/output/clip.mp4').read_bytes() == b'video-bytes'*5000
    assert len(calls) == 3 and not list(tmp_path.glob('.snapshot-download-*'))


@pytest.mark.parametrize('damage', ['declared_oversize','actual_oversize','size_mismatch','digest','expanded'])
def test_stream_limits_and_digest_fail_before_project_restore(tmp_path, monkeypatch, damage):
    limit = recovery.MAX_ARTIFACT_BYTES
    declared = limit+1 if damage == 'declared_oversize' else ('absent' if damage == 'actual_oversize' else None)
    if damage == 'size_mismatch':declared = 1
    _, calls = transport(monkeypatch, declared=declared, bad_digest=damage == 'digest')
    if damage == 'actual_oversize':monkeypatch.setattr(recovery, 'MAX_ARTIFACT_BYTES', 4096)
    if damage == 'expanded':monkeypatch.setattr(recovery, 'MAX_EXPANDED_BYTES', 4096)
    with pytest.raises(ValueError):recovery.download_snapshot('10', 'unit', 'fake-token', tmp_path/'stage')
    assert not (tmp_path/'stage/projects').exists()
    assert not list(tmp_path.glob('.snapshot-download-*'))
    if damage == 'declared_oversize':assert len(calls) == 2
