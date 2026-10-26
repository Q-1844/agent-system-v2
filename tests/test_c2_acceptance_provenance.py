"""C2/C1 acceptance boundaries: always offline and no provider/API traffic."""
import hashlib
from pathlib import Path

import pytest

from src.ai.cross_project_assets import AssetUnavailable
from src.ai.c2_production import materialize_c2
from src.ai.incremental_planner import validate_asset_set, validate_live_scope
from src.ai.producer_planning_models import PlanningContractError
from src.core.models import Scene, Shot, VideoVersion
from src.core.store import ProjectStore
from tools.full_project_cloud_acceptance import verify_composition_clip

from tests.test_c2_cross_project import seed, plan


def verify_old_clip(root):
    project = ProjectStore(root).load()
    shot = project.scenes[0].shots[0]
    local = Path(shot.video_versions[0].local)
    clip = {'shot_id': shot.shot_id, 'video_version_id': shot.final_video,
            'local_path': str(local)}
    return shot, clip, local


def test_existing_clip_needs_exact_frozen_version_sha_and_stays_local(tmp_path):
    dest = seed(tmp_path / 'projects')
    shot, clip, path = verify_old_clip(dest)
    accepted = set()
    with pytest.raises(RuntimeError, match='unverified'):
        verify_composition_clip(shot, clip, dest, accepted)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    proof = [{'shot_id': shot.shot_id, 'version_id': shot.final_video,
              'video_id': shot.video_versions[0].video_id, 'sha256': digest}]
    result = verify_composition_clip(shot, clip, dest, accepted, frozen_prior=proof)
    assert result['source_kind'] == 'existing_verified_video'
    assert result['sha256'] == digest
    with pytest.raises(RuntimeError, match='SHA mismatch'):
        verify_composition_clip(shot, clip, dest, accepted,
                                frozen_prior=[{**proof[0], 'sha256': 'bad'}])
    with pytest.raises(RuntimeError, match='outside the project'):
        verify_composition_clip(shot, {**clip, 'local_path': '/tmp/not-in-project.mp4'},
                                dest, accepted, frozen_prior=proof)


def test_new_provider_clip_requires_confirmed_video_receipt(tmp_path):
    dest = seed(tmp_path / 'projects')
    shot, clip, _ = verify_old_clip(dest)
    out = verify_composition_clip(shot, clip, dest, {shot.video_versions[0].video_id})
    assert out['source_kind'] == 'new_paid_receipt'


def test_c2_cross_project_copy_needs_exact_frozen_selected_asset(tmp_path):
    root = tmp_path / 'projects'
    seed(root, 'origin')
    seed(root, 'library')
    bp, _ = plan(root, 'sequel', '直接复用灯塔项目已有视频，拍约120秒续集')
    assert any(u['action'] == 'reuse_media' for u in bp['selected_assets'])
    dest, _ = materialize_c2(bp, projects_root=root, destination_name='newsequel')
    shot, clip, path = verify_old_clip(dest)
    with pytest.raises(RuntimeError, match='unverified'):
        verify_composition_clip(shot, clip, dest, set())
    result = verify_composition_clip(shot, clip, dest, set(),
                                     selected_assets=bp['selected_assets'])
    assert result['source_kind'] == 'cross_project_sha_reuse'
    path.write_bytes(b'tampered')
    with pytest.raises(RuntimeError, match='unverified'):
        verify_composition_clip(shot, clip, dest, set(),
                                selected_assets=bp['selected_assets'])


def test_unshot_scene_cannot_be_a_vacuously_complete_asset_set(tmp_path):
    dest = seed(tmp_path / 'projects')
    project = ProjectStore(dest).load()
    project.scenes.append(Scene(scene_id='s02', title='new'))
    ProjectStore(dest).save(project)
    report = validate_asset_set(dest, require_local_verified=True)
    assert report['complete'] is False
    assert report['missing_scenes'] == ['s02']


def test_scope_rejects_unapproved_extra_scenes_and_shots(tmp_path):
    dest = seed(tmp_path / 'projects')
    project = ProjectStore(dest).load()
    wp = {'authorized_scene_ids': ['s01'], 'known_shot_ids': ['s01_c01'],
          'unshot_scene_ids': [], 'digest_inputs': {'scenes': [['s01', 'old']], 'budget': 60}}
    validate_live_scope(project, wp)
    project.scenes[0].shots.append(Shot(shot_id='s01_c02', scene_id='s01', index=2))
    with pytest.raises(PlanningContractError, match='Shot outside'):
        validate_live_scope(project, wp)
    project.scenes[0].shots.pop()
    project.scenes.append(Scene(scene_id='s02', title='unapproved'))
    with pytest.raises(PlanningContractError, match='scene outside'):
        validate_live_scope(project, wp)


def test_preflight_reuse_requires_real_file_and_full_decode(tmp_path, monkeypatch):
    from tools.full_project_cloud_acceptance import preflight_frozen_reused_media
    import tools.full_project_resume as recovery
    dest = seed(tmp_path / 'projects')
    shot, clip, local = verify_old_clip(dest)
    sha = hashlib.sha256(local.read_bytes()).hexdigest()
    frozen = {'reuse_assets': [{'target': shot.shot_id, 'evidence': {
        'version_id': shot.final_video, 'video_id': shot.video_versions[0].video_id,
        'sha256': sha}}]}
    probed = []
    monkeypatch.setattr(recovery, 'validate_completed_clip',
                        lambda path, seconds: probed.append((Path(path), seconds)))
    proof = preflight_frozen_reused_media(dest, workplan=frozen)
    assert proof[0]['sha256'] == sha and probed == [(local, 8.0)]
    local.write_bytes(b'invalid altered video')
    with pytest.raises(RuntimeError, match='SHA changed'):
        preflight_frozen_reused_media(dest, workplan=frozen)
    local.unlink()
    with pytest.raises(RuntimeError, match='no local verified media'):
        preflight_frozen_reused_media(dest, workplan=frozen)


def test_preflight_uses_actual_media_decoder_and_rejects_random_bytes(tmp_path):
    from tools.full_project_cloud_acceptance import preflight_frozen_reused_media
    dest = seed(tmp_path / 'projects')
    shot, clip, local = verify_old_clip(dest)
    sha = hashlib.sha256(local.read_bytes()).hexdigest()
    frozen = {'reuse_assets': [{'target': shot.shot_id, 'evidence': {
        'version_id': shot.final_video, 'video_id': shot.video_versions[0].video_id,
        'sha256': sha}}]}
    with pytest.raises(Exception):
        preflight_frozen_reused_media(dest, workplan=frozen)  # fixture bytes are NOT MP4


def test_empty_project_is_not_a_successful_asset_set(tmp_path):
    from src.utils.project_manager import ProjectManager
    root = tmp_path / 'projects'
    project_dir = ProjectManager(str(root)).create_project('no_scenes')
    report = validate_asset_set(project_dir, require_local_verified=True)
    assert not report['complete']
    assert report['missing_scenes'] == ['__project_has_no_scenes__']
