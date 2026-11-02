"""Event-driven continuation with non-billable GitHub environment wait timers.

Production completion arms a 20-minute environment wait; the short controller
re-arms while quota is closed and dispatches the existing recovery path once
ready. No provider calls, media generation, sleeps or production state resets.
"""
from __future__ import annotations

import argparse
import json
import os
import time

from tools import resume_patrol as patrol
from tools.quota_gate import gate_status, parse_gate
from tools.resume_dispatch import (
    REPO, MAX_AUTO_HOPS, auto_resume_allowed, default_branch, dispatch_token,
    fetch_run, post_workflow_dispatch,
)
from tools.video_task_state import validate_nonce

WORKFLOW = 'video-quota-wakeup.yml'


def read_checkpoint(nonce, token):
    from tools.video_task_state import task_index
    value = task_index(token, nonce, 'full-project-production.json').read()
    return value['doc'] if value else None


def armable(doc):
    """Check eligibility at the recorded gate, without opening it for POSTs."""
    if not isinstance(doc, dict) or not doc.get('full_run_id'):
        return False
    gate = parse_gate(doc.get('quota_resume_after'))
    return auto_resume_allowed(doc, now=gate)[0]


def record_ticket(b1_token, nonce, source_run_id, ticket):
    from tools.video_task_state import ProductionCheckpoint, task_index
    index = task_index(b1_token, nonce, 'full-project-production.json')
    source = index.read()
    if not source or str(source['doc'].get('github_run_id')) != source_run_id:
        return False
    cp = ProductionCheckpoint(index, nonce, source['doc']['request_sha256'])
    if cp.current != source or not armable(cp.doc):
        return False
    cp.save(quota_wakeup=ticket)
    return True


def save_ticket(b1_token, nonce, source_run_id, ticket):
    try:
        return record_ticket(b1_token, nonce, source_run_id, ticket)
    except Exception:
        # Dispatch may already exist. Never repeat it because its audit CAS
        # lost a race; the queued controller re-reads authoritative state.
        return False


def resolve(b1_token, token, source_run_id, nonce=''):
    if not str(source_run_id).isdigit():
        raise ValueError('source Actions run ID must be numeric')
    run = fetch_run(token, str(source_run_id))
    if (not run or run.get('status') != 'completed'
            or run.get('repository', {}).get('full_name') != REPO
            or not (run.get('path') or '').split('@')[0].endswith('/video-autoproduction-e2e.yml')):
        raise ValueError('wakeup requires the completed production workflow in this repository')
    candidates = [validate_nonce(nonce)] if nonce else patrol.list_e2e_nonces(b1_token)
    for candidate in candidates:
        doc = read_checkpoint(candidate, b1_token)
        if (not doc or doc.get('nonce') != candidate
                or str(doc.get('github_run_id')) != str(source_run_id)):
            continue  # already handed off, complete, or an unrelated movie
        if doc.get('source_sha') != run.get('head_sha'):
            raise ValueError('source checkpoint/Actions SHA mismatch')
        if not armable(doc):
            continue
        ticket = dict(state='ARMED', source_run_id=str(source_run_id),
                      wakeup_run_id=os.environ.get('GITHUB_RUN_ID'),
                      quota_resume_after=doc.get('quota_resume_after'), at=time.time())
        ticket['audit_saved'] = save_ticket(b1_token, candidate, str(source_run_id), ticket)
        return dict(enabled=True, nonce=candidate, source_run_id=str(source_run_id), ticket=ticket)
    return dict(enabled=False, reason='no matching resumable checkpoint')


def validate_delay(token, wakeup_run_id, *, smoke=False, now=None):
    """Fail closed if the environment was auto-created without its timer.

    Verifying elapsed run age also prevents a missing protection rule from
    becoming a rapid self-dispatch loop on a fresh fork.
    """
    run = fetch_run(token, str(wakeup_run_id))
    created = patrol.parse_iso((run or {}).get('created_at'))
    minimum = 60 if smoke else 20 * 60
    if (not run or run.get('repository', {}).get('full_name') != REPO
            or not (run.get('path') or '').split('@')[0].endswith('/'+WORKFLOW)
            or created is None or (time.time() if now is None else now) - created < minimum - 5):
        raise ValueError('wakeup environment wait timer was not observed; configure protection rules')


def wake_once(b1_token, token, ref, nonce, source_run_id, *, timer_hops=0):
    validate_nonce(nonce)
    source_run_id = str(source_run_id)
    if not source_run_id.isdigit() or not 0 <= timer_hops < MAX_AUTO_HOPS:
        raise ValueError('invalid or exhausted wakeup identity/hop count')
    doc = read_checkpoint(nonce, b1_token)
    if (not doc or doc.get('nonce') != nonce
            or str(doc.get('github_run_id')) != source_run_id or not armable(doc)):
        return dict(action='stop', reason='checkpoint completed, changed owner or is not resumable')
    if gate_status(doc)[0]:
        result = patrol.sweep_nonce(nonce, b1_token, token, ref, _read=doc)
        if result['action'] in ('dispatch', 'awaiting_human') or result.get('reason') == 'dispatched recently, waiting for child run':
            if result['action'] == 'dispatch':
                save_ticket(b1_token, nonce, source_run_id,
                            dict(state='FIRED', source_run_id=source_run_id, at=time.time()))
            return result
        # Source upload/liveness checks may be temporarily unsettled. Check
        # again after a real delay, never resubmit media in this process.
    receipt = post_workflow_dispatch(token, WORKFLOW, ref, dict(
        task_nonce=nonce, source_run_id=source_run_id,
        timer_hops=str(timer_hops + 1), smoke_test='false'))
    ticket = dict(state='ARMED' if receipt.get('dispatched') else 'DISPATCH_FAILED',
                  source_run_id=source_run_id, quota_resume_after=doc.get('quota_resume_after'),
                  timer_hops=timer_hops + 1, at=time.time(), receipt=receipt)
    saved = save_ticket(b1_token, nonce, source_run_id, ticket)
    return dict(action='rearm' if receipt.get('dispatched') else 'dispatch_failed',
                ticket=ticket, audit_saved=saved)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('resolve', 'run'))
    args = parser.parse_args(argv)
    b1 = os.environ.get('B1_ARCHIVE_TOKEN', '').strip()
    token = dispatch_token()
    if not b1 or not token:
        raise ValueError('archive and workflow dispatch credentials are required')
    source = os.environ.get('SOURCE_RUN_ID', '').strip()
    nonce = os.environ.get('TASK_NONCE', '').strip()
    if args.mode == 'resolve':
        result = resolve(b1, token, source, nonce)
        output = os.environ.get('GITHUB_OUTPUT')
        if output:
            with open(output, 'a', encoding='utf-8') as stream:
                stream.write('enabled='+str(result['enabled']).lower()+'\n')
                if result['enabled']:
                    stream.write('nonce='+result['nonce']+'\nsource='+result['source_run_id']+'\n')
    else:
        validate_delay(token, os.environ.get('GITHUB_RUN_ID', ''),
                       smoke=os.environ.get('SMOKE_TEST', 'false').lower() == 'true')
        ref = default_branch(token)
        if not ref:
            raise ValueError('cannot resolve repository default branch')
        result = wake_once(b1, token, ref, nonce, source,
                           timer_hops=int(os.environ.get('TIMER_HOPS') or '0'))
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get('action') == 'dispatch_failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
