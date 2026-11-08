"""Explicit, durable quota simulation policy. Production defaults stay unchanged."""
from __future__ import annotations


def validate_policy(policy, nonce):
    if (not isinstance(policy, dict) or set(policy) !=
            {'schema_version', 'nonce', 'video_seconds', 'wait_minutes'} or
            type(policy.get('schema_version')) is not int or policy['schema_version'] != 1 or
            not nonce or policy.get('nonce') != nonce or
            type(policy.get('video_seconds')) is not int or
            not 12 <= policy['video_seconds'] <= 500 or
            type(policy.get('wait_minutes')) is not int or
            not 1 <= policy['wait_minutes'] <= 60):
        raise ValueError('invalid nonce-scoped video quota test policy')
    return policy


def select_policy(nonce, previous, environ, *, restored):
    minutes = int(environ.get('QUOTA_TEST_WAIT_MINUTES') or 0)
    stored = previous.get('quota_test_policy')
    requested = (dict(schema_version=1, nonce=nonce,
                      video_seconds=int(environ.get('DAILY_VIDEO_SECONDS') or 500),
                      wait_minutes=minutes) if minutes else None)
    if requested:
        validate_policy(requested, nonce)
    if stored is not None:
        validate_policy(stored, nonce)
        if requested and requested != stored:
            raise ValueError('cannot change frozen quota test policy')
        return stored
    if restored and requested:
        raise ValueError('enable quota simulation when creating a new task')
    return requested


def short_timer(doc):
    if (doc.get('phase') != 'WAITING_QUOTA' or
            doc.get('terminal_status') != 'quota_exhausted' or
            doc.get('quota_wait_source') != 'test_window'):
        return False
    policy = validate_policy(doc.get('quota_test_policy'), doc.get('nonce'))
    return policy['wait_minutes'] < 20


def reconcile_compressed_job_gates(queue, document):
    """Honor an explicit CAS test override only for known-unpaid local rejections."""
    override = document.get('quota_test_gate_override')
    if not override:
        return 0
    from .provider_errors import safe_submit_retry
    from datetime import datetime
    lineage = {str(document.get('github_run_id')),
               *(str(h.get('run_id')) for h in document.get('resume_history', []))}
    if (override.get('nonce') != document.get('nonce') or
            str(override.get('source_run_id')) not in lineage):
        raise ValueError('compressed quota gate override identity mismatch')
    # Validate both dated values, even when the new gate is still closed.
    for value in (override.get('old_gate'), override.get('new_gate')):
        datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
    changed = 0
    for job in queue.jobs.values():
        p = job.params or {}
        if (job.project == document.get('project_name') and job.kind == 'video'
                and job.state == 'failed' and p.get('failure_category') == 'daily_quota'
                and p.get('provider_submit_state') == 'not_sent'
                and not p.get('provider_task_id')
                and p.get('quota_resume_after') == override['old_gate']
                and safe_submit_retry(p, job.error)):
            p.setdefault('quota_gate_history', []).append(dict(override))
            p['quota_resume_after'] = override['new_gate']
            changed += 1
    if changed:
        queue._save()
    return changed
