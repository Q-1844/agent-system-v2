"""Connect C2's append-only repair revisions to real FullProject execution.

The old Stage0 blueprint remains immutable. Repaired scenes are created as a
new FullProjectRun in the same project, with previously ACCEPTED clips locked
for reuse. Existing FullRuns and native video receipts are never rewritten.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.ai.repair_revision import (
    RepairRevisionError, append_approved_revision, assert_quiescent,
    effective_summaries, reconcile_project_revisions, expert_propose, digest,
)
from src.core.store import ProjectStore


def effective_media_profile(profile: dict, project, revisions: list) -> dict:
    """Only repair-adjustable planning windows change; never the P0 reviewer."""
    if not revisions:
        return profile
    result = dict(profile)
    durations = [int(round(s.target_duration_s or 0)) for s in project.all_shots()]
    if not durations or any(not 4 <= x <= 11 for x in durations):
        raise RepairRevisionError("revised plan has invalid provider shot durations")
    actual = sum(durations)
    result["shot_min"] = min(result["shot_min"], len(durations))
    result["shot_max"] = max(result["shot_max"], len(durations))
    result["planned_min"] = min(result["planned_min"], actual)
    result["planned_max"] = max(result["planned_max"], actual)
    # Explicit user hard duration still gets the pre-existing final-media check.
    # For unspecified/soft targets the original technical/quality checks are
    # left unchanged in run_acceptance; we do not lower min_final here.
    return result


def validate_repaired_project(project_dir: Path, blueprint: dict, events: list, fp: str):
    """Replay a missing committed edit, then enforce the *effective* C2 view."""
    if not events:
        project = ProjectStore(project_dir).load()
        actual = {sc.scene_id: sc.summary for sc in project.scenes}
        expected = effective_summaries(blueprint, events, fp)
        if any(actual.get(scene_id) != summary for scene_id, summary in expected.items()):
            raise RepairRevisionError("restored project does not match the original frozen plan")
        return {"schema_version": 1, "base_plan_fingerprint": fp,
                "applied_revision_ids": [], "effective_plan_digest": digest(expected)}
    reconciled = reconcile_project_revisions(project_dir, blueprint, events, fp)
    project = ProjectStore(project_dir).load()
    actual = {sc.scene_id: sc.summary for sc in project.scenes}
    expected = effective_summaries(blueprint, events, fp)
    if any(actual.get(scene_id) != summary for scene_id, summary in expected.items()):
        raise RepairRevisionError("restored project does not match the effective plan")
    return reconciled


def build_repair_finding(full, project_dir: Path, *, evidence_dir: Path = None) -> dict | None:
    """Accept existing verified P0 evidence, never invent a quality verdict."""
    for root in filter(None, [evidence_dir, project_dir / 'output' / 'ai']):
        path = Path(root) / 'repair_findings.json'
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(doc, list):
            candidates = doc
        else:
            candidates = [doc]
        for finding in candidates:
            if (isinstance(finding, dict) and
                    finding.get('trigger_full_run_id') in (None, full.run_id) and
                    finding.get('level') in ('R3', 'R4')):
                return finding
    # A structural Director failure is a direct, checkable planning error,
    # unlike a provider timeout or subjective quality guess. Only this type
    # of native failure can synthesize a deterministic finding here.
    if full.status != 'failed' or full.error_code != 'DIRECTOR_FAILED':
        return None
    entry = next((sc for sc in full.scenes if sc.scene_id == full.current_scene_id), None)
    if entry is None:
        entry = next((sc for sc in full.scenes if sc.status == 'failed'), None)
    if entry is None or not entry.error:
        return None
    evidence = f'FullProjectRun {full.run_id} Director planning error: {entry.error[:1300]}'
    return {'finding_id': 'director_' + digest({'run': full.run_id, 'error': evidence})[:20],
            'scene_id': entry.scene_id, 'kind': 'director_plan_failed',
            'source': 'director_failure', 'review_id': full.run_id,
            'level': 'R3', 'evidence': evidence}


def lock_preserved_shots(project_dir: Path, revised_shot_ids: set[str]) -> list:
    """New FullRun will skip accepted, unaffected footage without a new POST."""
    store = ProjectStore(project_dir)
    project = store.load()
    locked = []
    changed = False
    for shot in project.all_shots():
        if shot.shot_id in revised_shot_ids:
            continue
        if shot.final_video and any(v.version_id == shot.final_video and v.status == 'done'
                                    for v in shot.video_versions):
            if not shot.locked:
                shot.locked = True
                changed = True
            locked.append({'shot_id': shot.shot_id, 'final_video': shot.final_video})
    if changed:
        store.save(project)
    return locked


async def auto_repair_failed_full(*, manager, full, wrapper, checkpoint,
                                  blueprint, base_plan_fingerprint,
                                  evidence_dir=None):
    """One P1 repair decision. Return replacement FullRun or None.

    Before CAS any provider jobs/UNKNOWN receipts must be settled. If no
    trusted finding or Producer rejects the alternatives, do nothing; leave
    the original failure available to the operator.
    """
    if full.status not in ('failed', 'completed') or wrapper.finalize_only:
        return None
    project_dir = Path(wrapper.project_dir)
    finding = build_repair_finding(full, project_dir, evidence_dir=evidence_dir)
    if finding is None:
        return None
    # The loose P0 reviewer is unchanged. A technically completed FullRun may
    # opt into R3/R4 only when an external quality review supplies concrete
    # evidence; a completion alone must never cause a speculative rewrite.
    if full.status == 'completed' and finding.get('source') != 'verified_quality_review':
        return None
    assert_quiescent(project_dir, checkpoint.doc)
    from src.ai.repair_revision import validate_finding, REVISION_LIMIT
    finding = validate_finding(finding, scene_ids={s['scene_id'] for s in blueprint['scenes']})
    prior = checkpoint.doc.get('repair_revisions') or []
    if len(prior) >= REVISION_LIMIT or any(r['finding']['finding_id'] == finding['finding_id'] for r in prior):
        return None
    project = ProjectStore(project_dir).load()
    from src.agents.producer_orchestration import (
        producer_choose_repair_collaboration, verify_stored_collaboration,
        DEFAULT_MODE, OrchestrationError,
    )
    # A mode is a run identity, never a hot-swappable live choice. Legacy C2
    # checkpoints have no field and remain combo_recommended by default.
    mode_id = checkpoint.doc.get('producer_collab_mode_id') or DEFAULT_MODE
    plans = list(checkpoint.doc.get('repair_collaboration_plans') or [])
    pinned = next((record['plan'] for record in plans
                   if record.get('finding_id') == finding['finding_id']), None)
    if pinned is None:
        plan = await producer_choose_repair_collaboration(
            wrapper, finding, project, mode_id=mode_id)
        # Persist before the first specialist chat. A crash/resume must not
        # silently re-plan the same revision with a different team.
        checkpoint.save(repair_collaboration_plans=[*plans,
                        {'finding_id': finding['finding_id'], 'plan': plan}])
    else:
        try:
            plan = verify_stored_collaboration(pinned, finding, mode_id=mode_id)
        except OrchestrationError as exc:
            raise RepairRevisionError(f'frozen collaboration plan invalid: {exc}') from exc
    proposal, approvals = await expert_propose(wrapper, finding, project,
                                                collaboration_plan=plan)
    approvals.insert(0, {'role': 'collaboration_plan', 'plan': plan,
                         'plan_digest': plan['plan_digest']})
    # append CAS is synchronous, and must be completed before any new videos.
    event = append_approved_revision(
        checkpoint, base_plan_fingerprint=base_plan_fingerprint,
        blueprint=blueprint, finding=finding, proposal=proposal,
        approvals=approvals, project_dir=project_dir)
    changed_ids = {s['shot_id'] for s in (*proposal['replace_shots'], *proposal['add_shots'])}
    preserved = lock_preserved_shots(project_dir, changed_ids)
    checkpoint.save(repair_phase='READY_TO_RESTART',
                    repair_preserved_media=[*(checkpoint.doc.get('repair_preserved_media') or []),
                                            {'revision_id': event['revision_id'], 'shots': preserved}])
    new_full = await manager.start_run(project_dir.name)
    checkpoint.save(repair_phase='RESTARTED', repair_active_full_run_id=new_full.run_id)
    return new_full
