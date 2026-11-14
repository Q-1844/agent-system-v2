"""Nonce-scoped, explicit permission to replace an unconfirmed paid request.

An authorized replacement is not proof of non-submission. Preserve the original
intent, reserve its possible cost, and never reinterpret it as a free rejection.
"""
from __future__ import annotations

import hashlib
import json

AUTHORIZED = 'RESUBMIT_AUTHORIZED'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def policy_valid(policy, nonce, request_sha256):
    if not isinstance(policy, dict):
        return False
    payload = {k: v for k, v in policy.items() if k != 'policy_id'}
    return (policy.get('version') == 1 and policy.get('accept_duplicate_charge') is True
            and policy.get('nonce') == nonce and bool(nonce)
            and policy.get('request_sha256') == request_sha256 and bool(request_sha256)
            and policy.get('policy_id') == digest(payload))


def make_policy(nonce, request_sha256, *, actor, run_id, at):
    if not nonce or not request_sha256 or not actor or not run_id:
        raise ValueError('uncertain replacement authorization needs task and operator identity')
    policy = dict(version=1, nonce=nonce, request_sha256=request_sha256,
                  accept_duplicate_charge=True, actor=actor, authorized_run_id=run_id,
                  authorized_at=at)
    return dict(policy, policy_id=digest(policy))


def make_authorization(attempt, job_id, policy, provenance):
    if not attempt.get('attempt_id') or not attempt.get('intent_id') or not attempt.get('immutable_input_digest'):
        raise ValueError('unconfirmed receipt lacks immutable native intent identity')
    auth = dict(version=1, nonce=policy['nonce'], request_sha256=policy['request_sha256'],
                policy_id=policy['policy_id'], risk_accepted=True,
                actor=policy['actor'], authorized_at=policy['authorized_at'],
                authorized_run_id=policy['authorized_run_id'], job_id=job_id,
                original_attempt_id=attempt['attempt_id'], original_intent_id=attempt['intent_id'],
                immutable_input_digest=attempt['immutable_input_digest'],
                source_run_id=provenance['run_id'], source_sha=provenance['source_sha'],
                source_artifact_sha256=provenance.get('artifact_sha256'))
    return dict(auth, authorization_id=digest(auth))


def authorization_valid(auth, *, attempt_id=None, nonce=None, request_sha256=None):
    if not isinstance(auth, dict):
        return False
    payload = {k: v for k, v in auth.items() if k != 'authorization_id'}
    return (auth.get('version') == 1 and auth.get('risk_accepted') is True
            and bool(auth.get('nonce')) and bool(auth.get('request_sha256'))
            and bool(auth.get('original_attempt_id')) and bool(auth.get('original_intent_id'))
            and bool(auth.get('immutable_input_digest')) and bool(auth.get('job_id'))
            and bool(auth.get('source_run_id')) and bool(auth.get('source_sha'))
            and auth.get('authorization_id') == digest(payload)
            and (attempt_id is None or auth['original_attempt_id'] == attempt_id)
            and (nonce is None or auth['nonce'] == nonce)
            and (request_sha256 is None or auth['request_sha256'] == request_sha256))


def authorized_attempt(attempt, *, nonce=None, request_sha256=None):
    auth = attempt.get('resubmit_authorization')
    return (attempt.get('state') == AUTHORIZED and bool(attempt.get('attempt_id')) and not attempt.get('video_id')
            and authorization_valid(auth, attempt_id=attempt.get('attempt_id'),
                                    nonce=nonce, request_sha256=request_sha256)
            and attempt.get('intent_id') == auth.get('original_intent_id')
            and attempt.get('immutable_input_digest') == auth.get('immutable_input_digest'))


def authorized_job_retry(params):
    params = params or {}
    return (params.get('provider_submit_state') == 'resubmit_authorized'
            and bool(params.get('submission_intent_id'))
            and params.get('confirm_uncertain_resubmit') is True
            and not any(params.get(k) for k in (
                'provider_task_id', 'unattached_provider_receipt', 'unattached_artifact'))
            and authorization_valid(params.get('resubmit_authorization'),
                                    attempt_id=params.get('submission_intent_id')))


SUPERSEDED = 'SUPERSEDED'


def superseded_attempt(attempt):
    """A closed UNKNOWN whose shot is already covered by a confirmed receipt.

    2026-11-14 anime750s run 329 实锤（s02_c11）：UNKNOWN 悬案的同镜已有带收据的
    ACCEPTED 尝试。该形态不需要任何重提交决策——不授权、不重发，只保留意图与
    可能成本记录（possible_charge_seconds）并作废：拒绝花钱永远是安全默认，
    不属于需要操作员拍板的双扣费决策面。
    """
    return (attempt.get('state') == SUPERSEDED
            and isinstance(attempt.get('superseded_by_attempt_id'), str)
            and bool(attempt.get('superseded_by_attempt_id'))
            and isinstance(attempt.get('possible_charge_seconds'), int))


def validate_attempts(attempts, *, nonce=None, request_sha256=None):
    """Return confirmed receipts; possible authorized costs remain in the input."""
    confirmed = []
    for attempt in attempts:
        if type(attempt.get('seconds')) is not int or not 4 <= attempt['seconds'] <= 12:
            raise ValueError('invalid cumulative video duration')
        if attempt.get('state') == 'ACCEPTED' and isinstance(attempt.get('video_id'), str) and attempt['video_id']:
            confirmed.append(attempt)
        elif superseded_attempt(attempt):
            # 必须能对上同镜的已确认收据，否则 SUPERSEDED 本身就是对账债
            if not any(a.get('attempt_id') == attempt.get('superseded_by_attempt_id')
                       and a.get('state') == 'ACCEPTED' and a.get('video_id')
                       and a.get('logical_shot_id') == attempt.get('logical_shot_id')
                       for a in attempts):
                raise ValueError('superseded attempt lacks a confirmed sibling receipt')
        elif not authorized_attempt(attempt, nonce=nonce, request_sha256=request_sha256):
            raise ValueError('unconfirmed/duplicate paid submission requires reconciliation')
    ids = [a['video_id'] for a in confirmed]
    if len(ids) != len(set(ids)):
        raise ValueError('unconfirmed/duplicate paid submission requires reconciliation')
    return confirmed


def validate_document_policy(document):
    policy = document.get('submission_recovery_policy')
    closed = [a for a in document.get('video_attempts') or [] if a.get('state') == AUTHORIZED]
    if (policy is not None or closed) and not policy_valid(
            policy, document.get('nonce'), document.get('request_sha256')):
        raise ValueError('uncertain replacement policy is missing or belongs to a different task')
    if any((a.get('resubmit_authorization') or {}).get('policy_id') != policy['policy_id'] for a in closed):
        raise ValueError('uncertain replacement receipt differs from the authorized policy')
