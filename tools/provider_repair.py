"""Turn the active native job's provider rejection into a Producer finding."""
from __future__ import annotations

import json
from pathlib import Path

from src.ai.repair_revision import digest
from src.core.provider_errors import content_policy_rejection, safe_submit_retry


def provider_repair_finding(full, project_dir, *, jobs_file=None):
    """Read exact FullRun -> ProjectRun -> SceneRun -> failed Job provenance.

    Old errors, quota/network faults and uncertain paid submissions are not
    creative findings. A confirmed failed video retains its original receipt.
    """
    if full.status != 'failed' or not getattr(full, 'production_run_id', None):
        return None
    root = Path(project_dir)
    ledger = Path(jobs_file) if jobs_file is not None else Path('cache/jobs.json')
    try:
        jobs = json.loads(ledger.read_text(encoding='utf-8'))
        production = json.loads((root / 'output/production_runs' /
            full.production_run_id / 'run.json').read_text(encoding='utf-8'))
        if (production.get('run_id') != full.production_run_id or
                production.get('parent_full_run_id') != full.run_id):
            return None
        for scene in production.get('scenes') or []:
            if scene.get('status') != 'failed' or not scene.get('child_run_id'):
                continue
            child = json.loads((root / 'output/ai/autopilot_runs' /
                (scene['child_run_id'] + '.json')).read_text(encoding='utf-8'))
            if (child.get('run_id') != scene['child_run_id'] or
                    child.get('parent_project_run_id') != production['run_id'] or
                    child.get('scene_id') != scene['scene_id']):
                continue
            for state in child.get('shots') or []:
                if state.get('status') != 'failed':
                    continue
                kind = state.get('stage')
                if kind not in ('first_frame', 'last_frame', 'video'):
                    continue
                job = jobs.get(state.get('active_job_id'))
                if job is None and state.get('active_stage_key'):
                    matches = [j for j in jobs.values() if
                        (j.get('params') or {}).get('autopilot_stage_key') == state['active_stage_key']]
                    job = matches[0] if len(matches) == 1 else None
                if not job or job.get('state') != 'failed':
                    continue
                params = job.get('params') or {}
                if (job.get('project') != root.name or job.get('kind') != kind or
                        job.get('shot_id') != state.get('shot_id') or
                        params.get('origin_run_id') != child['run_id'] or
                        (state.get('active_stage_key') and
                         params.get('autopilot_stage_key') != state['active_stage_key'])):
                    continue
                error = job.get('error') or ''
                feedback = params.get('provider_content_feedback') or {}
                if not content_policy_rejection(feedback.get('error') or error):
                    continue
                if kind == 'video':
                    refused = safe_submit_retry(params, error)
                    dead_receipt = (params.get('provider_submit_state') == 'accepted' and
                        params.get('provider_task_id') and
                        (job.get('error_class') or '').endswith('.VideoGenerationFailed'))
                    if not (refused or dead_receipt):
                        continue
                evidence = {
                    'full_run_id': full.run_id, 'scene_run_id': child['run_id'],
                    'job_id': job['job_id'], 'kind': kind,
                    'error': str(feedback.get('error') or error)[:4000],
                    'provider_submit_state': params.get('provider_submit_state'),
                    'provider_task_id': params.get('provider_task_id'),
                    'error_class': job.get('error_class'),
                    'provider': job.get('error_provider') or params.get('image_provider') or params.get('provider') or 'agnes',
                    'model': job.get('error_model') or params.get('video_model') or params.get('image_model'),
                    'rejected_prompt': str(params.get('dispatch_image_prompt') or
                                           params.get('dispatch_prompt') or '')[:12000],
                }
                return {'finding_id': 'provider_' + digest(evidence)[:20],
                    'scene_id': scene['scene_id'], 'shot_id': state['shot_id'],
                    'kind': 'provider_content_rejection', 'source': 'provider_rejection',
                    'review_id': job['job_id'], 'level': 'R3',
                    'evidence': f"{kind} provider rejection for {state['shot_id']}: {evidence['error']}"[:4000],
                    'provider_feedback': evidence}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # Missing/unverifiable provenance is not permission to revise a story.
        return None
    return None
