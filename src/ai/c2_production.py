"""A/C2 autonomous continuation: stage-0 contracts and idempotent materialization.

No media calls here. The caller MUST freeze ``build_c2_blueprint`` via Stage0Record
before ``materialize_c2``. Source projects are observed without any write; only
``extend_existing`` mutates the named source project after blueprint freeze.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from src.ai.cross_project_assets import (
    AssetUnavailable, discover_project_assets, read_source_project,
    local_asset_file, safe_project_dir, select_asset_uses, verify_asset_use,
)
from src.ai.producer_planning_models import (
    PlanningContractError, SceneBudget, TaskSpec,
)
from src.ai.producer_planning_service import (
    ProducerPlanningService, allocate_scene_budgets, effective_planning_window,
)
from src.core.models import Character, Project, Scene, Shot, ShotStatus, VideoVersion
from src.core.store import ProjectStore

C2_SCHEMA = 'producer-cross-project-c2-v1'
TOTAL_MARKERS = ('总长', '总时长', '总长度', '总共', '整片到', '成片达到', '最终达到', '合计到')


def existing_planned_seconds(project: Project) -> int:
    """A deterministic fallback based on existing Shot duration metadata."""
    return int(round(sum(float(s.target_duration_s or 0)
                         for s in project.all_shots())))


def c2_duration_window(spec: TaskSpec, existing_seconds: int,
                       limits: Dict[str, int]) -> Tuple[Tuple[int, int], str]:
    window = effective_planning_window(spec.duration, limits)
    if spec.intent == 'extend_existing' and (
        spec.duration.scope == 'final_total' or
        any(word in spec.request_text for word in TOTAL_MARKERS)
    ):
        window = (window[0] - existing_seconds, window[1] - existing_seconds)
        scope = 'final_total'
    else:
        scope = 'additional_content' if spec.intent == 'extend_existing' else 'new_output'
    if window[0] < 8 or window[0] > window[1]:
        raise PlanningContractError(
            'C2 additional duration is infeasible (min 8s for two narrative scenes); '
            f'window={window}, existing={existing_seconds}s')
    if window[0] > int(limits.get('video_seconds') or 0):
        raise PlanningContractError('C2 requested new duration exceeds authorized video budget')
    return window, scope


def _source_continuity_view(project_dir: Path) -> Dict[str, Any]:
    """Access the already-authorized, read-only Scene/Resource projection.

    The Agent-domain architectural boundary only allows the shared
    production_context reader to import Scene System directly.
    """
    from src.agents.production_context import source_continuity_projection
    return source_continuity_projection(project_dir)


def _source_context(project: Project, spec: TaskSpec, uses: list[dict],
                    continuity: dict) -> dict:
    return {
        'source_project': project.name,
        'source_story_tail': project.story_text[-2600:],
        'source_scene_summaries': [s.summary for s in project.scenes[-4:]],
        'source_characters': [{'name': c.name, 'description': c.description_en[:300]}
                              for c in project.characters[:16]],
        'source_style': asdict(project.style),
        'source_continuity': continuity,
        'source_asset_references': [
            {'action': item['action'], 'source_project': item['evidence']['source_project'],
             'scene_id': item['evidence']['scene_id'], 'shot_id': item['evidence']['shot_id'],
             'description': item['evidence'].get('description')}
            for item in uses[:4]
        ],
    }


async def build_c2_blueprint(spec: TaskSpec, *, projects_root: Path,
                             client, ledger, limits: Dict[str, int]) -> Dict[str, Any]:
    """Plan new narrative scenes with a verified source, no canonical writes.

    Text LLM calls reserve their budget through ``ledger`` before dispatch.
    The returned object is a candidate; only B1 Stage0Record.freeze authorizes it.
    """
    if spec.intent not in ('extend_existing', 'sequel') or not spec.source_project:
        raise ValueError('C2 requires an explicit extension/sequel source')
    source, doc_sha = read_source_project(projects_root, spec.source_project)
    current_duration = existing_planned_seconds(source)
    existing_versions = []
    if spec.intent == 'extend_existing':
        source_dir = safe_project_dir(projects_root, spec.source_project)
        for scene in source.scenes:
            if not scene.shots:
                raise PlanningContractError(
                    'extend_existing requires finished source scenes; run complete_project first')
            for shot in scene.shots:
                if not shot.final_video:
                    raise PlanningContractError(
                        'extend_existing source contains unfinished Shots; run complete_project first')
                version = next((v for v in shot.video_versions
                                if v.version_id == shot.final_video and v.status == 'done'), None)
                if version is None:
                    raise AssetUnavailable(f'invalid final version in source Shot {shot.shot_id}')
                path = local_asset_file(source_dir, version.local)
                if path is None:
                    raise AssetUnavailable(f'source final video missing for Shot {shot.shot_id}')
                existing_versions.append({
                    'shot_id': shot.shot_id, 'version_id': version.version_id,
                    'video_id': version.video_id,
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                })
    window, scope = c2_duration_window(spec, current_duration, limits)
    candidates = discover_project_assets(projects_root, spec.request_text,
                                          source_project=spec.source_project,
                                          limit=24)
    # An extended movie already contains its source footage. Importing that
    # very footage again to inflate duration is not a second reusable asset.
    uses = select_asset_uses(
        candidates, spec.request_text,
        direct_exclude_project=(spec.source_project if spec.intent == 'extend_existing' else None))
    # Non-frozen source asset must pass SHA and version validation BEFORE
    # use; explicit direct reuse cannot degrade to a costlier new clip.
    for use in uses:
        verify_asset_use(projects_root, use)
    continuity = _source_continuity_view(safe_project_dir(projects_root, spec.source_project))
    context = _source_context(source, spec, uses, continuity)
    result = await ProducerPlanningService(client, ledger).plan(
        spec, limits, continuation_context=context, window_override=window)
    blueprint = result.canonical_payload()
    scenes = blueprint['scenes']
    budgets = blueprint['scene_budgets']
    # Directly reused video occupies one new scene as one already-finished
    # Shot. All remaining new scenes are still autonomously directed.
    reused = next((u for u in uses if u['action'] == 'reuse_media'), None)
    if reused is not None:
        sec = reused['evidence'].get('seconds')
        try:
            duration = int(round(float(sec)))
        except (TypeError, ValueError):
            raise PlanningContractError('reused clip has no trustworthy duration')
        if not 4 <= duration <= 11 or window[0] - duration < 4:
            # Not safe to silently alter a time contract to fit an imported clip.
            raise PlanningContractError('reused clip cannot fit the authorized C2 window')
        if len(budgets) < 2:
            raise PlanningContractError('direct reuse requires at least one new-generation scene')
        remaining = allocate_scene_budgets(
            [int(b.get('target_s') or 1) for b in budgets[1:]],
            (window[0] - duration, window[1] - duration),
        )
        budgets = [{**budgets[0], 'min_s': duration, 'max_s': duration,
                    'target_s': duration, 'shot_min': 1, 'shot_max': 1},
                   *[b.to_evidence() for b in remaining]]
        # Reused content must describe the actual source clip, not a newly
        # invented Shot matching an unrelated scene.
        scenes[0]['title'] = '复用源项目镜头'
        scenes[0]['summary'] = reused['evidence'].get('description') or scenes[0]['summary']
    # Global Scene IDs for extend must be append-only; never reuse s01...
    scene_high_water = max((int(sc.scene_id[1:]) for sc in source.scenes
                            if re.fullmatch(r's\d+', sc.scene_id)), default=0) + 1
    start = max(source.next_scene_seq, scene_high_water) if spec.intent == 'extend_existing' else 1
    new_ids = [f's{i:02d}' for i in range(start, start + len(scenes))]
    old_ids = {s.scene_id for s in source.scenes}
    if any(sid in old_ids for sid in new_ids) and spec.intent == 'extend_existing':
        raise PlanningContractError('source next_scene_seq collides with existing Scene ID')
    for i, sid in enumerate(new_ids):
        scenes[i]['scene_id'] = sid
        budgets[i]['scene_id'] = sid
    # Recalculate physical upper limit from the frozen scene budgets. Provider
    # budgets count only newly paid calls; preexisting clips are free.
    if sum(int(b['min_s']) for b in budgets) > int(limits['video_seconds']):
        raise PlanningContractError('C2 planned new output exceeds paid video ceiling')
    blueprint.update({
        'schema_version': C2_SCHEMA,
        'intent': spec.intent, 'source_project': spec.source_project,
        'source_document_sha256': doc_sha,
        'source_scene_ids': [s.scene_id for s in source.scenes],
        'source_shot_count': len(source.all_shots()),
        'source_existing_versions': existing_versions,
        'source_story_text': source.story_text,
        'source_character_snapshots': [
            {'name': c.name, 'description_en': c.description_en}
            for c in source.characters],
        'source_style_snapshot': asdict(source.style),
        'source_continuity_context': continuity,
        'source_duration_planned_s': current_duration,
        'duration_scope': scope,
        'additional_duration_window': list(window),
        'selected_assets': uses,
        'scenes': scenes, 'scene_budgets': budgets,
        'execution_bounds': dict(limits),
    })
    return blueprint


def _same_new_content(project: Project, blueprint: dict) -> bool:
    expected = blueprint['scenes']
    by_id = {s.scene_id: s for s in project.scenes}
    if not all(item['scene_id'] in by_id for item in expected):
        return False
    return all(by_id[item['scene_id']].summary == item['summary']
               and by_id[item['scene_id']].title == (item.get('title') or '')
               for item in expected)


def _copy_reused_clip(blueprint: dict, projects_root: Path, dest: Path,
                      scene: Scene) -> Optional[Dict[str, Any]]:
    use = next((u for u in blueprint['selected_assets']
                if u['action'] == 'reuse_media'), None)
    if use is None:
        return None
    ev = use['evidence']
    source_path = verify_asset_use(projects_root, use)
    if source_path is None:
        raise AssetUnavailable('C2 reusable video file unavailable')
    sec = float(ev['seconds'])
    if not 4 <= sec <= 11:
        raise AssetUnavailable('reused video outside 4-11s contract')
    dst_dir = dest / 'output' / 'videos' / 'cross_project'
    dst_dir.mkdir(parents=True, exist_ok=True)
    suffix = source_path.suffix.lower() if source_path.suffix.lower() in ('.mp4', '.mov', '.webm') else '.mp4'
    dst = dst_dir / f"{ev['sha256']}{suffix}"
    if not dst.is_file():
        tmp = dst.with_suffix(dst.suffix + '.tmp')
        try:
            shutil.copyfile(source_path, tmp)
            os.replace(tmp, dst)
        finally:
            tmp.unlink(missing_ok=True)
    if hashlib.sha256(dst.read_bytes()).hexdigest() != ev['sha256']:
        raise AssetUnavailable('copy-on-reuse SHA mismatch')
    new_shot_id = f'{scene.scene_id}_c01'
    new_version_id = f"c2_reuse_{ev['sha256'][:16]}"
    basis = {'cross_project_reuse': ev}
    version = VideoVersion(
        version_id=new_version_id, status=ShotStatus.DONE.value,
        video_id=ev.get('video_id'), local=str(dst),
        seconds_returned=sec, generation_basis=basis,
    )
    scene.shots = [Shot(
        shot_id=new_shot_id, scene_id=scene.scene_id, index=1,
        description=ev.get('description') or scene.summary,
        target_duration_s=sec, status=ShotStatus.DONE.value,
        video_versions=[version], final_video=new_version_id,
    )]
    return {'target_shot_id': new_shot_id,
            'target_version_id': new_version_id,
            'evidence': ev, 'local': str(dst)}


def materialize_c2(blueprint: dict, *, projects_root: Path,
                   destination_name: str,
                   plan_fingerprint: Optional[str] = None) -> tuple[Path, dict]:
    """Idempotently append or create after Stage0Record.freeze.

    Caller must verify plan_fingerprint via Stage0Record.verify before calling.
    If a previous attempt appended the scenes but crashed before writing the
    sidecar, the exact IDs/contents are recognized and are not duplicated.
    """
    if blueprint.get('schema_version') != C2_SCHEMA:
        raise PlanningContractError('not a C2 frozen blueprint')
    source_name = blueprint['source_project']
    source, source_sha = read_source_project(projects_root, source_name)
    intent = blueprint['intent']
    if intent == 'sequel':
        if destination_name == source_name:
            raise PlanningContractError('sequel must not overwrite source project')
        dest = safe_project_dir(projects_root, destination_name, must_exist=False)
        existing = (dest / 'output' / 'project.json').is_file()
        if not existing:
            if source_sha != blueprint['source_document_sha256']:
                raise AssetUnavailable('sequel source document changed before materialization')
            from src.utils.project_manager import ProjectManager
            ProjectManager(str(Path(projects_root))).create_project(destination_name)
        project = ProjectStore(dest).load()
        if existing and project.story_text != blueprint['story_text']:
            raise PlanningContractError('sequel destination already exists with unrelated story')
        if not existing:
            project.story_text = blueprint['story_text']
            style = blueprint['source_style_snapshot']
            from src.core.models import StyleAnchor
            project.style = StyleAnchor(**{k: v for k, v in style.items()
                                           if k in StyleAnchor.__dataclass_fields__})
            project.characters = [Character(name=c['name'], description_en=c['description_en'])
                                  for c in blueprint['source_character_snapshots']]
    elif intent == 'extend_existing':
        if destination_name != source_name:
            raise PlanningContractError('extension destination must be original project')
        dest = safe_project_dir(projects_root, destination_name)
        project = ProjectStore(dest).load()
        if project.story_text == blueprint['source_story_text']:
            if source_sha != blueprint['source_document_sha256']:
                raise AssetUnavailable('original source project changed after plan freeze')
            project.story_text += '\n\n' + blueprint['story_text']
        elif project.story_text != blueprint['source_story_text'] + '\n\n' + blueprint['story_text']:
            raise AssetUnavailable('original story changed externally; cannot append continuation')
    else:
        raise PlanningContractError('unsupported C2 intent')
    # Validate all new IDs before modifying. Partial overlap is a conflict.
    new = blueprint['scenes']
    have = {s.scene_id for s in project.scenes}
    present = [s['scene_id'] in have for s in new]
    if any(present) and not all(present):
        raise PlanningContractError('partial C2 scene materialization; manual reconciliation required')
    imported = []
    if all(present):
        if not _same_new_content(project, blueprint):
            raise PlanningContractError('existing C2 scenes differ from frozen blueprint')
        # A restored project must still hold the already-imported clip with
        # the exact bytes approved at freeze. Repeated C2 stage 0 never
        # downloads/replaces an existing completed video as a side effect.
        use = next((u for u in blueprint['selected_assets']
                    if u['action'] == 'reuse_media'), None)
        if use:
            first = next(s for s in project.scenes if s.scene_id == new[0]['scene_id'])
            if len(first.shots) != 1 or not first.shots[0].final_video:
                raise AssetUnavailable('previously materialized reusable video missing')
            version = next((v for v in first.shots[0].video_versions
                            if v.version_id == first.shots[0].final_video), None)
            if (version is None or not version.local or
                not Path(version.local).is_file() or
                hashlib.sha256(Path(version.local).read_bytes()).hexdigest() !=
                    use['evidence']['sha256']):
                raise AssetUnavailable('restored cross-project copy digest mismatch')
    else:
        scenes = [Scene(scene_id=s['scene_id'], title=s.get('title') or '',
                        summary=s['summary']) for s in new]
        for scene in scenes:
            if scene is scenes[0]:
                copied = _copy_reused_clip(blueprint, projects_root, dest, scene)
                if copied is not None:
                    imported.append(copied)
        project.scenes.extend(scenes)
        max_num = max((int(s.scene_id[1:]) for s in project.scenes
                       if re.fullmatch(r's\d+', s.scene_id)), default=0)
        project.next_scene_seq = max(project.next_scene_seq, max_num + 1)
        ProjectStore(dest).save(project)
    # Sidecars are evidence/policy only; existing canonical is never overwritten.
    from src.ai.full_project_autopilot import SCENE_BUDGETS_FILE
    from src.core.atomic import atomic_write_json
    ai_dir = dest / 'output' / 'ai'
    ai_dir.mkdir(parents=True, exist_ok=True)
    sidecar = ai_dir / SCENE_BUDGETS_FILE
    policy = {'schema_version': 1, 'plan_fingerprint': plan_fingerprint or blueprint['task_spec_sha256'],
              'scene_budgets': blueprint['scene_budgets']}
    if sidecar.is_file():
        old = json.loads(sidecar.read_text(encoding='utf-8'))
        # A previous continuation may have its own budgets; keep them and add
        # new budgets, but never silently overwrite a bound scene's values.
        keyed = {v['scene_id']: v for v in old.get('scene_budgets') or []}
        for b in blueprint['scene_budgets']:
            if b['scene_id'] in keyed and keyed[b['scene_id']] != b:
                raise PlanningContractError('C2 SceneBudget conflicts with existing frozen policy')
            keyed[b['scene_id']] = b
        policy['scene_budgets'] = list(keyed.values())
    atomic_write_json(sidecar, policy)
    # Reference-only image media is locally copied into the target project's
    # private workspace. The original shared library and source projects are
    # never edited. Video-only references are narrative guidance until a
    # separately authorized direct-reuse operation exists.
    imported_references = []
    for use in blueprint['selected_assets']:
        if use['action'] != 'reference_only' or use['evidence']['kind'] != 'image':
            continue
        path = verify_asset_use(projects_root, use)
        if path is None:
            continue  # URL-only candidates remain symbolic, not verified media.
        refdir = dest / 'output' / 'ai' / 'c2_reference_images'
        refdir.mkdir(parents=True, exist_ok=True)
        suffix = path.suffix.lower() if path.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp') else '.png'
        refdest = refdir / f"{use['evidence']['sha256']}{suffix}"
        if not refdest.is_file():
            tmp = refdest.with_suffix('.tmp')
            try:
                shutil.copyfile(path, tmp)
                os.replace(tmp, refdest)
            finally:
                tmp.unlink(missing_ok=True)
        if hashlib.sha256(refdest.read_bytes()).hexdigest() != use['evidence']['sha256']:
            raise AssetUnavailable('copied C2 reference image digest mismatch')
        imported_references.append({'origin': use['evidence'],
                                    'target_local': str(refdest)})
    refs = ai_dir / f"c2_asset_references_{blueprint['task_spec_sha256'][:16]}.json"
    # Same file can be rewritten by idempotent restart; contents are frozen.
    evidence = {'schema_version': C2_SCHEMA,
                'task_spec_sha256': blueprint['task_spec_sha256'],
                'plan_fingerprint': plan_fingerprint,
                'source_project': source_name,
                'selected_assets': blueprint['selected_assets'],
                'source_continuity_context': blueprint.get('source_continuity_context') or {},
                'imported_reference_images': imported_references,
                'target_scene_ids': [s['scene_id'] for s in blueprint['scenes']],
                'source_duration_planned_s': blueprint['source_duration_planned_s'],
                'duration_scope': blueprint['duration_scope']}
    if refs.is_file():
        prior = json.loads(refs.read_text(encoding='utf-8'))
        if prior.get('task_spec_sha256') != evidence['task_spec_sha256']:
            raise PlanningContractError('destination bound to a different C2 task')
    atomic_write_json(refs, evidence)
    # The old source project's existing input/ remains immutable. Persist an
    # additional archival input record for this *new* continuation so a later
    # Release restore can reconstruct why each new Scene and reused version
    # belongs to this task, not just observe the post-production canonical.
    audit = dest / 'input' / f"c2_continuation_{blueprint['task_spec_sha256'][:16]}.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit_record = {
        'schema_version': C2_SCHEMA, 'plan_fingerprint': plan_fingerprint,
        'task_spec_sha256': blueprint['task_spec_sha256'], 'intent': intent,
        'source_project': source_name,
        'source_document_sha256': blueprint['source_document_sha256'],
        'new_story_text': blueprint['story_text'],
        'new_scenes': blueprint['scenes'],
        'scene_budgets': blueprint['scene_budgets'],
        'source_asset_reuses': blueprint['selected_assets'],
        'duration_scope': blueprint['duration_scope'],
    }
    if audit.is_file() and json.loads(audit.read_text(encoding='utf-8')) != audit_record:
        raise PlanningContractError('archived C2 continuation evidence conflicts with frozen task')
    atomic_write_json(audit, audit_record)
    return dest, {'imported': imported, 'scene_ids': [s['scene_id'] for s in new],
                  'source_project': source_name, 'intent': intent}


def c2_media_profile(blueprint: dict) -> Dict[str, Any]:
    """Frozen total shot window + new-payment-only resource ceiling."""
    old = int(blueprint['source_duration_planned_s']) if blueprint['intent'] == 'extend_existing' else 0
    old_count = len(blueprint.get('source_scene_ids') or []) if blueprint['intent'] == 'extend_existing' else 0
    # Old original Shot count cannot be reconstructed from scene IDs; persist
    # the value in the blueprint before freeze (or conservatively reject).
    old_shots = int(blueprint.get('source_shot_count') or 0) if old_count else 0
    budgets = blueprint['scene_budgets']
    cap = blueprint['execution_bounds']
    return {
        'scope': 'full-project-c2-v1',
        'scene_count': old_count + len(budgets),
        'shot_min': old_shots + sum(int(b['shot_min']) for b in budgets),
        'shot_max': old_shots + sum(int(b['shot_max']) for b in budgets),
        'planned_min': old + sum(int(b['min_s']) for b in budgets),
        'planned_max': old + sum(int(b['max_s']) for b in budgets),
        'min_final': float(math.floor(0.9 * (old + sum(int(b['min_s']) for b in budgets)))),
        'budget': int(cap['video_seconds']),
        'image_budget': int(cap['image_calls']),
        'text_budget': int(cap['text_calls']),
    }
