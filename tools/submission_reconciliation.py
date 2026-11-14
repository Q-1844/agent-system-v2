"""Reconcile stopped-snapshot submission facts before opening production gates."""
from __future__ import annotations

import copy
import datetime as dt

from src.core.submission_recovery import (
    AUTHORIZED, SUPERSEDED, make_authorization, make_policy, policy_valid,
    validate_attempts, validate_document_policy,
)

RECOVERY_FIELDS = ('video_attempts', 'submission_intents', 'submission_recovery_policy',
                   'submission_reconciliations')


def reconcile_snapshot_submissions(checkpoint, project, jobs, nonce, provenance, *,
                                   confirm=False, actor=None, authorization_run_id=None):
    """Mutate only detached snapshot copies; callers commit updates with RESTORED.

    No missing task ID is treated as proof of non-submission. An explicit
    task-scoped policy may accept that uncertainty and reserve its possible cost.
    """
    result = copy.deepcopy(checkpoint)
    attempts = result.get('video_attempts') or []
    validate_attempts([a for a in attempts if a.get('state') == 'ACCEPTED'])
    policy = result.get('submission_recovery_policy')
    if policy is not None and not policy_valid(policy, nonce, result.get('request_sha256')):
        raise ValueError('uncertain submission policy belongs to a different/invalid task')
    if confirm and policy is None:
        policy = make_policy(nonce, result.get('request_sha256'), actor=actor,
                             run_id=authorization_run_id,
                             at=dt.datetime.now(dt.timezone.utc).isoformat())
        result['submission_recovery_policy'] = policy
    history = result.get('submission_intents')
    if history is None:
        history = copy.deepcopy(attempts)
    events = list(result.get('submission_reconciliations') or [])
    authorized_jobs = []
    shots = {sh.get('shot_id'): sh for sc in project['scenes'] for sh in sc['shots']}
    kept = []
    for attempt in attempts:
        state = attempt.get('state')
        if state in ('ACCEPTED', AUTHORIZED, SUPERSEDED):
            kept.append(attempt)
            continue
        if state not in ('SUBMITTING', 'UNKNOWN') or attempt.get('video_id') or not attempt.get('attempt_id'):
            raise ValueError('unconfirmed/duplicate paid submission requires reconciliation')
        matches = [j for j in jobs.values() if j.get('kind') == 'video'
                   and (j.get('params') or {}).get('submission_intent_id') == attempt['attempt_id']]
        if len(matches) != 1:
            raise ValueError('unconfirmed submission has no unique native intent')
        job = matches[0]
        params = job.get('params') or {}
        shot = shots.get(job.get('shot_id'))
        if (job.get('project') != project['name'] or not shot
                or attempt.get('logical_shot_id') != job.get('shot_id')
                or job.get('state') == 'cancelled'):
            raise ValueError('unconfirmed submission identity/cancellation conflict')
        intents = [i for i in history if i.get('intent_id') == attempt.get('intent_id')
                   and i.get('attempt_id') == attempt['attempt_id']]
        if len(intents) != 1:
            raise ValueError('unconfirmed submission has conflicting intent history')
        intent = intents[0]
        receipts = {r for r in (params.get('provider_task_id'),
                    (params.get('unattached_provider_receipt') or {}).get('provider_task_id'),
                    intent.get('video_id')) if r}
        if len(receipts) > 1:
            raise ValueError('conflicting native and durable provider receipts')
        receipt = next(iter(receipts), None)
        event = dict(attempt_id=attempt['attempt_id'], previous_state=state,
                     source_run_id=provenance['run_id'], job_id=job['job_id'])
        if receipt:
            # A positive receipt always wins over permission to spend again.
            attempt.update(state='ACCEPTED', video_id=receipt)
            intent.update(state='ACCEPTED', video_id=receipt)
            event.update(state='ACCEPTED', proof='native_provider_receipt', video_id=receipt)
            kept.append(attempt)
        elif (params.get('provider_submit_state') in ('not_sent', 'rejected')
              and not params.get('unattached_artifact')):
            terminal = 'NOT_SENT' if params['provider_submit_state'] == 'not_sent' else 'REJECTED'
            intent.update(state=terminal, reconciliation='native_explicit_non_acceptance')
            event.update(state=terminal, proof='native_explicit_non_acceptance')
            job.update(state='failed', finished_at=job.get('finished_at'))
        else:
            # 🔴 2026-11-14 anime750s run 329 实锤（s02_c11 ordinal 21）：UNKNOWN
            # 悬案的同镜已有【带收据的 ACCEPTED 尝试】（ordinal 20 task_hpGEVkjZ）。
            # 该形态不需要任何重提交决策——镜头的需求可由同镜收据满足（恢复期
            # reconcile_accepted_work 收养），悬案本身拒绝重发即安全：保留意图
            # 与可能成本记录并作废。旧版一律要求 confirm_uncertain_submissions，
            # 导致无人值守 wakeup 链三跳全拦（run 330/331/332），片子卡死 19
            # 小时等人工。拒绝花钱永远是安全默认，不属于操作员决策面。
            sibling = next((a for a in attempts if a is not attempt
                            and a.get('state') == 'ACCEPTED' and a.get('video_id')
                            and a.get('logical_shot_id') == attempt.get('logical_shot_id')), None)
            if sibling:
                attempt.update(state=SUPERSEDED, provider_outcome='UNKNOWN',
                               possible_charge_seconds=attempt['seconds'],
                               superseded_by_attempt_id=sibling.get('attempt_id'))
                intent.update(state=SUPERSEDED, provider_outcome='UNKNOWN',
                              possible_charge_seconds=attempt['seconds'],
                              superseded_by_attempt_id=sibling.get('attempt_id'))
                event.update(state=SUPERSEDED,
                             proof='sibling_accepted_receipt_no_resubmission')
                kept.append(attempt)
                events.append(event)
                continue
            if policy is None:
                raise ValueError('unconfirmed/duplicate paid submission requires reconciliation; '
                                 'explicit confirm_uncertain_submissions is required')
            if params.get('unattached_artifact') or shot.get('video', {}).get('video_id'):
                raise ValueError('known task/artifact must be reconciled before replacement')
            if shot.get('video', {}).get('status') != 'generating':
                raise ValueError('uncertain submission no longer owns the active shot')
            from src.core.models import Project
            from src.generation.video_commit import video_input_signature
            model = Project.from_dict(project)
            if (not params.get('dispatch_input_signature')
                    or video_input_signature(model, model.get_shot(job['shot_id'])) != params['dispatch_input_signature']
                    or shot.get('final_video') != params.get('dispatch_final_video')):
                raise ValueError('uncertain submission inputs/selection changed; reconcile before replacement')
            auth = make_authorization(attempt, job['job_id'], policy, provenance)
            attempt.update(state=AUTHORIZED, resubmit_authorization=auth,
                           provider_outcome='UNKNOWN', possible_charge_seconds=attempt['seconds'])
            intent.update(state=AUTHORIZED, resubmit_authorization=auth,
                          provider_outcome='UNKNOWN', possible_charge_seconds=attempt['seconds'])
            params.update(provider_submit_state='resubmit_authorized',
                          confirm_uncertain_resubmit=True, resubmit_authorization=auth,
                          failure_category='resubmit_authorized')
            job.update(state='failed', params=params,
                       error='Unconfirmed submission replacement explicitly authorized; original risk retained')
            event.update(state=AUTHORIZED, proof='operator_accepted_duplicate_charge',
                         authorization_id=auth['authorization_id'])
            authorized_jobs.append(job['job_id'])
            kept.append(attempt)
        events.append(event)
    validate_attempts(kept, nonce=nonce, request_sha256=result.get('request_sha256'))
    result.update(video_attempts=kept, submission_intents=history, submission_reconciliations=events)
    validate_document_policy(result)
    updates = {k: result[k] for k in RECOVERY_FIELDS if k in result and result.get(k) != checkpoint.get(k)}
    return result, updates, authorized_jobs


def recovered_checkpoint(previous, restored):
    return dict(previous, **restored.get('submission_recovery_updates', {}))
