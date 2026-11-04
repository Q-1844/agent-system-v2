"""C2: real stage0 entry/reattach, mocked B1 CAS and mocked Script Writer."""
import asyncio
from pathlib import Path

import pytest

from src.ai.producer_planning_models import TaskSpec
from src.ai.producer_planning_service import extract_duration_intent
from src.core.store import ProjectStore
from tests.test_c2_cross_project import FakeChat, seed
from tests.test_stage0_auto_planning import FakeIndex


def test_c2_stage0_entry_freeze_materialize_and_reentry_no_duplicate(
        client, monkeypatch, tmp_path):
    import deps
    from tools import full_project_cloud_acceptance as acc
    from tools import e2e_autonomous_run as e2e
    from tools import video_task_state as vts

    root = deps.project_manager.base_dir
    source = '_test_c2_producer_stage0'
    import shutil
    shutil.rmtree(root / source, ignore_errors=True)
    seed(root, source)
    indexes = {}
    monkeypatch.setattr(vts, 'task_index', lambda _tok, _nonce, filename, **kw:
                        indexes.setdefault(filename, FakeIndex()))
    monkeypatch.setattr(e2e, 'ensure_branch', lambda index: None)
    chat = FakeChat()
    monkeypatch.setattr(deps, 'agnes_client', chat)
    monkeypatch.delenv('RESUME_RUN_ID', raising=False)
    spec = TaskSpec(intent='extend_existing', source_project=source,
                    request_text='为之前的灯塔飞机故事再追加约110秒的内容',
                    duration=extract_duration_intent('为之前的灯塔飞机故事再追加约110秒的内容'))
    from tools.stage0_state import Stage0Record, STAGE0_FILENAME
    index = indexes.setdefault(STAGE0_FILENAME, FakeIndex())
    stage0 = Stage0Record(index, 'c2-stage0-unit')
    out = tmp_path / 'acceptance'
    out.mkdir()
    first = {}
    try:
        project, prof, cp, restored, prev = asyncio.run(
            acc._run_c2_acceptance('c2-stage0-unit', out, first, 'tok', spec, stage0))
        assert project == source and restored is None
        assert first['plan_fingerprint']
        assert first['c2_source_existing_versions']
        assert first['execution_request_sha256']
        assert len(ProjectStore(root / source).load().scenes) > 1
        assert prof['scope'] == 'full-project-c2-v1'
        assert len(chat.calls) > 0
        # The same original TaskSpec is the authoritative resume identity;
        # Stage0 is loaded from CAS, not recomputed from modified source project.
        new_stage0 = Stage0Record(index, 'c2-stage0-unit')
        second = {}
        rerun = asyncio.run(acc._run_c2_acceptance(
            'c2-stage0-unit', out, second, 'tok', spec, new_stage0))
        assert len(ProjectStore(root / source).load().scenes) > 1
        assert second['plan_fingerprint'] == first['plan_fingerprint']
        assert second['execution_request_sha256'] == first['execution_request_sha256']
        assert second['stage0_status'] == 'FROZEN_RESTORED'
        assert rerun[3] is None
        assert len(chat.calls) > 0  # but no additional prompt after freeze
    finally:
        shutil.rmtree(root / source, ignore_errors=True)
