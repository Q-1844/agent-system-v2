"""A generated project's true canonical input must be in its full recovery package."""
import json
import pytest
from tools.full_project_cloud_acceptance import ensure_archive_input
from tools.relstore.manifest import analyze_dependencies
from tests.relstore.test_finalize_only_resume import fixture
from tools import full_project_resume as recovery


def test_materialized_canonical_story_resolves_input_dependency_without_relaxing_gate(tmp_path):
    p=tmp_path/'p';(p/'output').mkdir(parents=True)
    canonical={'name':'p','story_text':'original actual story','scenes':[{'scene_id':'s','title':'start','summary':'original scene input'}],'style':{'aspect_ratio':'16:9'}}
    (p/'output/project.json').write_text(json.dumps(canonical))
    assert analyze_dependencies(p)['missing_required']==['project_input:/input']
    ensure_archive_input(p,'same-nonce')
    doc=json.loads((p/'input/cloud-acceptance-source.json').read_text())
    assert doc['story_text']==canonical['story_text'] and doc['scenes']==canonical['scenes']
    assert doc['provenance']=='materialized_from_canonical_project_source'
    assert analyze_dependencies(p)['missing_required']==[]


def test_existing_real_input_is_preserved_and_missing_story_cannot_be_fabricated(tmp_path):
    p=tmp_path/'p';(p/'input').mkdir(parents=True);source=p/'input/brief.txt';source.write_bytes(b'actual user input')
    ensure_archive_input(p,'n');assert source.read_bytes()==b'actual user input' and len(list((p/'input').iterdir()))==1
    source.unlink();(p/'output').mkdir();(p/'output/project.json').write_text('{"name":"p","story_text":"","scenes":[]}')
    with pytest.raises(ValueError,match='canonical story'):ensure_archive_input(p,'n')
    assert not list((p/'input').iterdir())


def test_completed_full_run_can_be_restored_for_archival_without_resuming_production(tmp_path,monkeypatch):
    stage,cp,provenance=fixture(tmp_path);p=stage/'projects/cloud3min_unit';f=p/'output/full_runs/full/run.json'
    d=json.loads(f.read_text());d.update(status='completed',pause_requested=False,final_output='projects/cloud3min_unit/output/final.mp4');f.write_text(json.dumps(d))
    monkeypatch.setattr(recovery,'validate_completed_clip',lambda *a:None)
    result=recovery.restore_snapshot(stage,tmp_path/'runner','unit',cp,provenance,finalize_only=True)
    restored=json.loads((tmp_path/'runner/projects/cloud3min_unit/output/full_runs/full/run.json').read_text())
    assert restored['status']=='completed' and result['previous_video_seconds']==10
