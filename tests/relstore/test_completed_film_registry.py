import copy

import pytest

from tools.relstore.project_registry import Registry, FakeCAS, CasConflict
from tools.reconcile_completed_registry import completion_evidence, reconcile


REF = dict(repo='Q-1844/agent-system-v2-rundata', release_tag='full-project/film',
           sha256='a'*64, tree_fingerprint='b'*64, final_mp4_sha256='c'*64,
           release_id='1', asset_id='2', final_mp4_asset_id='3')


def archived():
    registry = Registry(FakeCAS())
    registry.create_project(project_id='film', alias='cloud3min_film')
    registry.plan_archive(project_id='film')
    registry.begin_snapshot(project_id='film', snapshot_fingerprint=REF['tree_fingerprint'])
    registry.set_verified_restorable(project_id='film', archive_ref=REF, restorable_level='full')
    return registry


def complete(registry):
    return registry.mark_completed(project_id='film', archive_ref=REF,
        expected_activity_seq=1, expected_project_revision=0, operation_id='film-completed')


def checkpoint(registry):
    return dict(schema_version=1, nonce='film', request_sha256='d'*64,
        github_run_id='10', source_sha='source', phase='COMPLETE', error=None,
        duration_s=719.7, video_attempts=[{'state':'ACCEPTED','video_id':'paid','seconds':8}],
        saved=dict(archive_ref=copy.deepcopy(REF), restorable_level='full', registry=registry.to_doc()))


def test_archiving_is_not_completion_but_finished_production_is_idempotent():
    registry = archived()
    assert registry.get('film').production_status == 'IN_PROGRESS'
    before = registry.to_doc()
    assert complete(registry)['production_status'] == 'COMPLETE'
    after = registry.to_doc()
    assert after['activity_seq'] == before['activity_seq']
    assert registry.get('film').residency == 'RESIDENT'
    assert registry.get('film').archive_ref == REF
    assert 'film' not in after['archive_queue']
    assert registry.get('film').slot_status == before['projects']['film']['slot_status']
    count = len(registry.cas.history)
    complete(Registry(registry.cas))
    assert len(registry.cas.history) == count


@pytest.mark.parametrize('damage', ['new_activity','new_revision','busy','pending_sync',
                                  'missing','superseded','different_receipt','operation'])
def test_completion_cannot_overwrite_new_or_unfinished_work(damage):
    registry = archived();record = registry.get('film')
    if damage == 'new_activity':record.last_activity_seq += 1
    if damage == 'new_revision':record.project_revision += 1
    if damage == 'busy':record.open_jobs = 1
    if damage == 'pending_sync':record.pending_sync_intents = ['pending']
    if damage == 'missing':record.missing_required = ['clip']
    if damage == 'superseded':record.archive_status = 'SUPERSEDED'
    if damage == 'different_receipt':record.archive_ref = dict(REF, asset_id='other')
    if damage == 'operation':record.active_operation_id = 'working'
    before = registry.to_doc();writes = len(registry.cas.history)
    with pytest.raises(ValueError, match='verified idle'):
        complete(registry)
    assert registry.to_doc() == before and len(registry.cas.history) == writes


def test_completion_loses_cas_without_overwriting_winner():
    registry = archived();winner = Registry(registry.cas)
    winner.record_activity(project_id='film', kind='save_shot', project_revision=1)
    actual = copy.deepcopy(registry.cas.read())
    with pytest.raises(CasConflict):complete(registry)
    assert registry.cas.read() == actual


def test_deduplicated_completion_cannot_adopt_newer_activity():
    registry = archived();complete(registry)
    registry.record_activity(project_id='film', kind='save_shot', project_revision=1)
    writes = len(registry.cas.history)
    with pytest.raises(ValueError, match='verified idle'):complete(registry)
    assert len(registry.cas.history) == writes


@pytest.mark.parametrize('damage', ['run','nonce','phase','error','partial','ref','digest'])
def test_legacy_repair_requires_authoritative_completion(damage):
    doc = checkpoint(archived())
    if damage == 'run':doc['github_run_id'] = '20'
    if damage == 'nonce':doc['nonce'] = 'other'
    if damage == 'phase':doc['phase'] = 'RUNNING'
    if damage == 'error':doc['error'] = 'archive failed'
    if damage == 'partial':doc['saved']['restorable_level'] = 'partial'
    if damage == 'ref':doc['saved']['archive_ref']['asset_id'] = 'other'
    if damage == 'digest':doc['saved']['archive_ref']['sha256'] = ''
    with pytest.raises(ValueError, match='completion evidence'):
        completion_evidence(doc, 'film', '10')


class Index:
    def __init__(self, doc):
        self.doc = copy.deepcopy(doc);self.revision = 1;self.fail = None

    def read(self):
        return dict(doc=copy.deepcopy(self.doc), blob_sha=str(self.revision))

    def write_cas(self, doc, expected, **kwargs):
        assert str(self.revision) == expected
        fail = self.fail;self.fail = None
        if fail == 'before':raise RuntimeError('storage unavailable before commit')
        self.doc = copy.deepcopy(doc);self.revision += 1
        if fail == 'after':raise RuntimeError('acknowledgement lost after commit')
        return dict(ok=True, new_sha=str(self.revision), http=200)


@pytest.mark.parametrize('failure', ['none','checkpoint','registry_ack'])
def test_legacy_repair_recovers_between_two_cas_writes_without_touching_receipts(monkeypatch, failure):
    import tools.video_task_state as state
    registry = archived();old = checkpoint(registry)
    production, index = Index(old), Index(registry.to_doc())
    monkeypatch.setattr(state, 'task_index', lambda token, nonce, filename: (
        production if filename == 'full-project-production.json' else index))
    if failure == 'checkpoint':production.fail = 'before'
    if failure == 'registry_ack':index.fail = 'after'
    if failure != 'none':
        with pytest.raises(Exception):reconcile('fake-token', 'film', '10')
    result = reconcile('fake-token', 'film', '10')
    assert result['production_status'] == 'COMPLETE'
    assert production.doc['saved']['registry']['projects']['film']['production_status'] == 'COMPLETE'
    assert production.doc['video_attempts'] == old['video_attempts']
    assert production.doc['github_run_id'] == old['github_run_id']
    assert production.doc['source_sha'] == old['source_sha']
    assert production.doc['saved']['archive_ref'] == old['saved']['archive_ref']
    revisions = (production.revision, index.revision)
    reconcile('fake-token', 'film', '10')
    assert revisions == (production.revision, index.revision)


def test_legacy_repair_rejects_newer_activity(monkeypatch):
    import tools.video_task_state as state
    registry = archived();old = checkpoint(registry)
    registry.record_activity(project_id='film', kind='save_shot', project_revision=1)
    production, index = Index(old), Index(registry.to_doc())
    monkeypatch.setattr(state, 'task_index', lambda token, nonce, filename: (
        production if filename == 'full-project-production.json' else index))
    with pytest.raises(ValueError, match='verified idle'):reconcile('fake-token', 'film', '10')
    assert production.doc == old and index.revision == 1


def test_archive_driver_returns_completed_registry_after_real_package_and_restore(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.core.models import Project, Scene, Shot, VideoVersion
    from src.core.store import ProjectStore
    from tools.full_project_cloud_acceptance import archive_completed_project
    import tools.e2e_autonomous_run as e2e
    import tools.video_task_state as state
    from tools.relstore.github_release_adapter import GitHubReleaseAdapter
    root = tmp_path/'film';video = root/'output/videos/one.mp4'
    video.parent.mkdir(parents=True);video.write_bytes(b'previously-validated-video')
    (root/'input').mkdir();(root/'input/story.txt').write_text('the original story')
    ProjectStore(root).save(Project(name='film', scenes=[Scene(scene_id='s', shots=[
        Shot(shot_id='s_c1', scene_id='s', index=1, final_video='v', video_versions=[
            VideoVersion(version_id='v', video_id='paid', status='done',
                         local='projects/film/output/videos/one.mp4')])])]))
    assets = {}
    class Archive:
        def create_release(self, *args, **kwargs):return SimpleNamespace(id='release')
        def upload_asset_with_recovery(self, release_id, name, data, sha):
            assets[name] = data;return SimpleNamespace(id=name)
        def download_asset_authenticated(self, asset_id, **kwargs):
            # kwargs：生产回读路径会传 not_found_grace_seconds（404 宽限重试）
            return assets[asset_id]
    authority = FakeCAS()
    monkeypatch.setattr(GitHubReleaseAdapter, 'armed', lambda *a:Archive())
    monkeypatch.setattr(e2e, 'B1GitHubCas', lambda index:authority)
    monkeypatch.setattr(state, 'task_index', lambda *a:None)
    result = archive_completed_project('fake-token', 'film', root, video, tmp_path/'evidence', 'a'*40)
    assert result['restorable_level'] == 'full' and result['restore_files_checked'] > 0
    assert result['registry']['projects']['film']['production_status'] == 'COMPLETE'
    assert result['registry']['archive_queue'] == []
    assert Registry(authority).get('film').production_status == 'COMPLETE'
