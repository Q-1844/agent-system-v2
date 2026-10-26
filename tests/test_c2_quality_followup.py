"""A/C2 creator-quality improvements on the PR22 interface.

Mocks only: these tests do not contact Agnes, GitHub or media providers.
They verify real code/dispatch semantics, including C2 planning with thinking.
"""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from src.ai.c2_production import build_c2_blueprint, choose_cross_project_assets
from src.ai.cross_project_assets import discover_project_assets, select_asset_uses
from src.ai.producer_planning_models import PlanningContractError, TaskSpec
from src.ai.producer_planning_service import extract_duration_intent
from tests.test_c2_cross_project import FakeChat, LIMITS, Ledger, seed
from tools import full_project_cloud_acceptance as acc


class ThinkingChooser(FakeChat):
    def __init__(self, *, pick_project='library', no_reuse=False, corrupt=False):
        super().__init__()
        self.options = []
        self.pick_project = pick_project
        self.no_reuse = no_reuse
        self.corrupt = corrupt

    async def chat(self, messages, **kwargs):
        self.options.append(kwargs)
        if 'candidates' in messages[-1]['content']:
            candidates = json.loads(messages[-1]['content'])['candidates']
            if self.corrupt:
                return '{"reuse_index": 9999}'
            choice = next((c['index'] for c in candidates
                           if c['project'] == self.pick_project and
                           c['direct_reuse_allowed']), None)
            if self.no_reuse:
                choice = None
            return json.dumps({'reuse_index': choice, 'reference_indices': []})
        return await super().chat(messages, **kwargs)


def _spec(text, **kw):
    return TaskSpec(intent='sequel', source_project='origin', request_text=text,
                    duration=extract_duration_intent(text), **kw)


def test_task_spec_old_identity_remains_byte_identical_without_extra_sources():
    spec = _spec('续集 大约120秒')
    raw = spec.canonical_payload()
    assert 'asset_source_projects' not in raw
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(',', ':'),
                                       ensure_ascii=False).encode()).hexdigest()
    assert digest == spec.sha256()
    assert TaskSpec.from_dict(raw).sha256() == digest


def test_extra_project_names_are_hashed_unique_sorted_and_roundtrip():
    base = _spec('续集 大约120秒')
    extra = _spec('续集 大约120秒', asset_source_projects=('library_B', 'origin', 'library_A', 'library_B'))
    assert extra.asset_source_projects == ('library_A', 'library_B')
    assert TaskSpec.from_dict(extra.to_dict()).sha256() == extra.sha256()
    assert extra.sha256() != base.sha256()
    for bad in ('../test', '..', 'broken/name', 'bad\\name'):
        with pytest.raises(PlanningContractError):
            _spec('续集 大约120秒', asset_source_projects=(bad,))


def test_workflow_passes_optional_sources_and_natural_language_source_identifiers():
    env = {'PRODUCTION_REQUEST': '给原项目拍续集，顺便使用 cloud3min_extra123 的灯塔素材，约120秒',
           'SOURCE_PROJECT': 'origin',
           'ASSET_SOURCE_PROJECTS': 'library_B,library_A,origin'}
    spec = acc.normalize_dispatch_input_to_task_spec('noncetest', environ=env)
    assert spec.intent == 'sequel'
    assert spec.asset_source_projects == ('cloud3min_extra123', 'library_A', 'library_B')
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/video-autoproduction-e2e.yml')
    text = workflow.read_text(encoding='utf-8')
    assert 'ASSET_SOURCE_PROJECTS: ${{ inputs.asset_source_projects }}' in text


def test_creator_language_can_select_clip_without_magic_reuse_phrase(tmp_path):
    root = tmp_path / 'projects'
    seed(root, 'origin')
    seed(root, 'library')
    candidates = discover_project_assets(root, '用以前的灯塔镜头制作新视频', source_project='origin')
    uses = select_asset_uses(candidates, '用以前的灯塔镜头制作新视频', direct_exclude_project='origin')
    assert any(u['action'] == 'reuse_media' and u['evidence']['source_project'] == 'library'
               for u in uses)
    refs = select_asset_uses(candidates, '仅作参考，不要复用旧视频')
    assert all(u['action'] == 'reference_only' for u in refs)


def test_thinking_chooser_prefer_relevant_existing_clip(tmp_path):
    root = tmp_path / 'projects'
    seed(root, 'origin')
    seed(root, 'library')
    client, ledger = ThinkingChooser(), Ledger()
    result = asyncio.run(build_c2_blueprint(_spec('用以前灯塔的镜头制作续集，约120秒'),
                       projects_root=root, client=client, ledger=ledger, limits=LIMITS))
    assert result['asset_decision_method'] == 'agnes_thinking'
    assert any(u['action'] == 'reuse_media' and u['evidence']['source_project'] == 'library'
               for u in result['selected_assets'])
    assert len(client.options) >= 2 and all(o.get('thinking') is True for o in client.options)
    assert all(o.get('max_tokens', 0) >= 1000 for o in client.options)
    assert ledger.count == len(client.options)


def test_thinking_chooser_respects_reference_only(tmp_path):
    root = tmp_path / 'projects'
    seed(root, 'origin')
    seed(root, 'library')
    candidates = discover_project_assets(root, '灯塔', source_project='origin')
    client, ledger = ThinkingChooser(no_reuse=False), Ledger()
    uses, method = asyncio.run(choose_cross_project_assets(
        client, ledger, candidates, '只做参考，不使用旧镜头'))
    assert method.startswith('agnes_thinking')
    assert all(u['action'] != 'reuse_media' for u in uses)


def test_malformed_agnes_response_degrades_to_heuristics_not_abort(tmp_path):
    root = tmp_path / 'projects'
    seed(root, 'origin')
    seed(root, 'library')
    candidates = discover_project_assets(root, '灯塔', source_project='origin')
    client, ledger = ThinkingChooser(corrupt=True), Ledger()
    uses, method = asyncio.run(choose_cross_project_assets(
        client, ledger, candidates, '直接复用灯塔镜头', direct_exclude_project='origin'))
    assert method == 'heuristic_fallback' and ledger.count == 1
    assert any(u['action'] == 'reuse_media' for u in uses)


def test_optional_archive_restore_resolves_one_and_skips_missing(tmp_path, monkeypatch):
    from src.ai import cross_project_archive as archive
    paths = []

    def fake_archive(root, name, *, token):
        paths.append(name)
        if name == 'missing':
            from src.ai.cross_project_assets import AssetUnavailable
            raise AssetUnavailable('not present in release')
        return {'origin': 'verified_archive'}

    monkeypatch.setattr(archive, 'ensure_archived_source_available', fake_archive)
    root = tmp_path / 'projects'
    root.mkdir()
    found, skipped = asyncio.run(acc._hydrate_c2_optional_sources(
        root, ('library', 'missing'), 'token'))
    assert paths == ['library', 'missing']
    assert found == [{'name': 'library', 'origin': 'verified_archive'}]
    assert skipped[0]['name'] == 'missing'


def test_optional_archive_transport_error_does_not_cancel_the_movie(tmp_path, monkeypatch):
    from src.ai import cross_project_archive as archive

    def unavailable(*args, **kwargs):
        raise ConnectionError('temporary network reset; possible signed URL information')

    monkeypatch.setattr(archive, 'ensure_archived_source_available', unavailable)
    root = tmp_path / 'projects'
    root.mkdir()
    found, skipped = asyncio.run(acc._hydrate_c2_optional_sources(
        root, ('cloud3min_extra123',), 'test_token'))
    assert found == []
    assert skipped == [{'name': 'cloud3min_extra123',
                        'reason': 'archive_transport_ConnectionError'}]


def test_c2_stage0_rehydrates_additional_named_archive_before_selecting_assets(
        client, monkeypatch, tmp_path):
    """Runner has only the source project; the extra material source is remote.

    The fake Release restore writes the validated destination project locally.
    The C2 planner then sees a real cross-project video rather than reference
    metadata or an invented URI. No paid API is called.
    """
    import deps
    import shutil
    from src.ai import cross_project_archive
    from tools import e2e_autonomous_run as e2e
    from tools import video_task_state as vts
    from tools.stage0_state import Stage0Record, STAGE0_FILENAME
    from tests.test_stage0_auto_planning import FakeIndex

    root = Path(deps.project_manager.base_dir)
    main_name = '_c2_quality_source_01'
    other_name = '_c2_quality_library_02'
    for name in (main_name, other_name):
        shutil.rmtree(root / name, ignore_errors=True)
    seed(root, main_name)
    restored = []

    def restore_named_source(root_dir, name, *, token):
        restored.append(name)
        if not (Path(root_dir) / name / 'output/project.json').is_file():
            seed(Path(root_dir), name)
        return {'origin': 'verified_archive', 'project_name': name}

    monkeypatch.setattr(cross_project_archive, 'ensure_archived_source_available',
                        restore_named_source)
    indexes = {}
    monkeypatch.setattr(vts, 'task_index', lambda _token, _nonce, fname, **kw:
                        indexes.setdefault(fname, FakeIndex()))
    monkeypatch.setattr(e2e, 'ensure_branch', lambda idx: None)
    monkeypatch.setattr(deps, 'agnes_client', FakeChat())
    monkeypatch.delenv('RESUME_RUN_ID', raising=False)
    text = '给灯塔故事续作，直接复用以前的灯塔镜头，再增加约100秒'
    spec = TaskSpec(intent='extend_existing', source_project=main_name,
                    asset_source_projects=(other_name,), request_text=text,
                    duration=extract_duration_intent(text))
    stage0 = Stage0Record(indexes.setdefault(STAGE0_FILENAME, FakeIndex()),
                          'quality-c2-hop')
    summary = {}
    out = tmp_path / 'run'
    out.mkdir()
    try:
        asyncio.run(acc._run_c2_acceptance(
            'quality-c2-hop', out, summary, 'test-token', spec, stage0))
        assert main_name in restored and other_name in restored
        assert summary['c2_additional_source_resolution'][0]['name'] == other_name
        assert (root / other_name / 'output/project.json').exists()
        assert any(u['action'] == 'reuse_media' and
                   u['evidence']['source_project'] == other_name
                   for u in summary['c2_asset_uses'])
        assert summary['plan_fingerprint']
        # Simulate a fresh runner losing the optional reference folder.
        # The second hop must restore the *same* frozen asset, not replan it.
        shutil.rmtree(root / other_name)
        again = Stage0Record(indexes[STAGE0_FILENAME], 'quality-c2-hop')
        next_summary = {}
        asyncio.run(acc._run_c2_acceptance(
            'quality-c2-hop', out, next_summary, 'test-token', spec, again))
        assert next_summary['plan_fingerprint'] == summary['plan_fingerprint']
        assert next_summary['execution_request_sha256'] == summary['execution_request_sha256']
        assert (root / other_name / 'output/project.json').exists()
    finally:
        for name in (main_name, other_name):
            shutil.rmtree(root / name, ignore_errors=True)


def test_explicit_reuse_instruction_overrides_model_no_reuse(tmp_path):
    root = tmp_path / 'projects'
    seed(root, 'origin')
    seed(root, 'library')
    candidates = discover_project_assets(root, '灯塔', source_project='origin')
    client, ledger = ThinkingChooser(no_reuse=True), Ledger()
    uses, method = asyncio.run(choose_cross_project_assets(
        client, ledger, candidates, '必须直接复用灯塔旧镜头', direct_exclude_project='origin'))
    assert method == 'explicit_reuse'
    assert any(u['action'] == 'reuse_media' and u['evidence']['source_project'] == 'library'
               for u in uses)


def test_long_archived_clip_is_reference_instead_of_c2_crash(tmp_path):
    from src.core.store import ProjectStore
    root = tmp_path / 'projects'
    seed(root, 'origin')
    lib = seed(root, 'library')
    project = ProjectStore(lib).load()
    project.scenes[0].shots[0].video_versions[0].seconds_returned = 19
    project.scenes[0].shots[0].target_duration_s = 19
    ProjectStore(lib).save(project)
    candidates = discover_project_assets(root, '灯塔', source_project='origin')
    uses = select_asset_uses(candidates, '必须直接复用以前灯塔的旧镜头',
                             direct_exclude_project='origin')
    assert all(u['action'] == 'reference_only' for u in uses)
    client = ThinkingChooser()
    spec = _spec('用之前的灯塔镜头作为参考，续集约120秒')
    result = asyncio.run(build_c2_blueprint(spec, projects_root=root,
                 client=client, ledger=Ledger(), limits=LIMITS))
    assert not any(u['action'] == 'reuse_media' for u in result['selected_assets'])


def test_paid_limit_counts_new_generated_target_not_free_reuse(tmp_path):
    root = tmp_path / 'projects'
    seed(root, 'origin')
    seed(root, 'library')
    limits = {**LIMITS, 'video_seconds': 240}
    text = '必须245秒，直接复用以前灯塔的镜头制作独立续集'
    result = asyncio.run(build_c2_blueprint(_spec(text), projects_root=root,
                 client=ThinkingChooser(), ledger=Ledger(), limits=limits))
    assert any(u['action'] == 'reuse_media' for u in result['selected_assets'])
    assert result['scene_budgets'][0]['target_s'] == 8
    assert sum(b['target_s'] for b in result['scene_budgets'][1:]) <= 240
    assert sum(b['target_s'] for b in result['scene_budgets']) >= 245
    # Without any reusable source, the same request would require more than
    # the authorized 240 seconds and is rejected before paid generation.
    from src.core.store import ProjectStore
    doc = ProjectStore(root / 'origin').load()
    doc.scenes[0].shots[0].final_video = None
    ProjectStore(root / 'origin').save(doc)
    from shutil import rmtree
    rmtree(root / 'library')
    with pytest.raises(PlanningContractError, match='authorized video budget'):
        asyncio.run(build_c2_blueprint(_spec(text), projects_root=root,
                    client=ThinkingChooser(), ledger=Ledger(), limits=limits))


def test_soft_duration_can_fit_budget_without_reused_media(tmp_path):
    root = tmp_path / 'projects'
    seed(root, 'origin', final_video=False)
    text = '拍一部大约245秒的独立续集，重新生成所有新镜头'
    result = asyncio.run(build_c2_blueprint(_spec(text), projects_root=root,
             client=ThinkingChooser(no_reuse=True), ledger=Ledger(),
             limits={**LIMITS, 'video_seconds': 240}))
    assert result['additional_duration_window'][1] == 240
    assert sum(b['target_s'] for b in result['scene_budgets']) <= 240
    assert not any(u['action'] == 'reuse_media' for u in result['selected_assets'])


def test_named_material_source_is_visible_even_when_simple_keywords_miss(tmp_path):
    root = tmp_path / 'projects'
    seed(root, 'origin')
    seed(root, 'library')
    request = '给森林里的故事拍续集，使用另一项目的航拍素材'
    plain = discover_project_assets(root, request, source_project='origin')
    assert not any(c['source_project'] == 'library' for c in plain)
    pinned = discover_project_assets(root, request, source_project='origin',
                                     priority_sources=['library'])
    assert any(c['source_project'] == 'library' for c in pinned)
    assert pinned[0]['source_project'] == 'library'
    # The LLM may still decline an irrelevant clip: discovery != authorization.
