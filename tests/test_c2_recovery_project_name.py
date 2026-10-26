"""Recovery of C2 source projects uses a restricted, explicit project name."""
import json
from pathlib import Path

import pytest

from tools.full_project_resume import _recovery_project_name, restore_snapshot
from tests.relstore.test_media_download_resume import snapshot


def make_custom_source(tmp_path):
    stage, checkpoint, provenance = snapshot(tmp_path)
    old = stage / 'projects' / 'cloud3min_unit'
    target = stage / 'projects' / 'origin'
    old.rename(target)
    docpath = target / 'output' / 'project.json'
    doc = json.loads(docpath.read_text(encoding='utf-8'))
    doc['name'] = 'origin'
    doc['scenes'][0]['shots'][0]['video_versions'][0]['local'] = '/old/checkout/projects/origin/output/videos/one.mp4'
    docpath.write_text(json.dumps(doc), encoding='utf-8')
    runpath = target / 'output' / 'full_runs/full/run.json'
    run = json.loads(runpath.read_text(encoding='utf-8'))
    run['project_name'] = 'origin'
    runpath.write_text(json.dumps(run), encoding='utf-8')
    summary = stage / 'e2e_run/unit/summary.json'
    record = json.loads(summary.read_text(encoding='utf-8'))
    record['project_name'] = 'origin'
    record['project_dir'] = '/old/checkout/projects/origin'
    summary.write_text(json.dumps(record), encoding='utf-8')
    return stage, checkpoint, provenance


def test_custom_extension_name_recovers_without_renaming(tmp_path):
    stage, cp, prov = make_custom_source(tmp_path)
    r = restore_snapshot(stage, tmp_path / 'runner', 'unit', cp, prov, project_name='origin')
    assert r['project_name'] == 'origin'
    doc = json.loads((tmp_path / 'runner/projects/origin/output/project.json').read_text(encoding='utf-8'))
    local = doc['scenes'][0]['shots'][0]['video_versions'][0]['local']
    assert Path(local).read_bytes() == b'original-video'


def test_wrong_or_unsafe_source_binding_does_not_restore(tmp_path):
    stage, cp, prov = make_custom_source(tmp_path)
    for name in ('other-project', '../evil', '..', '/absolute', '..\\evil'):
        with pytest.raises((ValueError, FileNotFoundError)):
            restore_snapshot(stage, tmp_path / 'runner', 'unit', cp, prov, project_name=name)
    assert not (tmp_path / 'runner/projects/origin').exists()


def test_legacy_resume_project_naming_unchanged():
    assert _recovery_project_name('unit') == 'cloud3min_unit'
    assert _recovery_project_name('unit', 'origin') == 'origin'
