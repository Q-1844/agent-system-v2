"""Repair only completion metadata backed by the authoritative finished film.

No provider calls, media submission, source edits or release uploads. Registry
and checkpoint use their existing CAS writers; retries reconcile either write.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re


def completion_evidence(doc, nonce, expected_run_id):
    saved = doc.get('saved') or {}
    ref = saved.get('archive_ref') or {}
    record = ((saved.get('registry') or {}).get('projects') or {}).get(nonce) or {}
    if (doc.get('nonce') != nonce or str(doc.get('github_run_id')) != str(expected_run_id)
            or not doc.get('request_sha256') or doc.get('phase') != 'COMPLETE'
            or doc.get('terminal_status') not in (None, 'completed') or doc.get('error')
            or saved.get('restorable_level') != 'full'
            or record.get('archive_status') != 'VERIFIED_RESTORABLE'
            or record.get('archive_ref') != ref or record.get('missing_required')
            or not all(re.fullmatch(r'[0-9a-f]{64}', str(ref.get(k) or ''))
                       for k in ('sha256', 'final_mp4_sha256', 'tree_fingerprint'))
            or ref.get('repo') != 'Q-1844/agent-system-v2-rundata'
            or ref.get('release_tag') != f'full-project/{nonce}'
            or type(record.get('last_activity_seq')) is not int
            or type(record.get('project_revision')) is not int):
        raise ValueError('authoritative finished-film completion evidence is missing or inconsistent')
    return ref, record


def reconcile(token, nonce, expected_run_id):
    from tools.video_task_state import ProductionCheckpoint, task_index, validate_nonce
    from tools.e2e_autonomous_run import B1GitHubCas
    from tools.relstore.project_registry import Registry

    validate_nonce(nonce)
    if not str(expected_run_id).isdigit():
        raise ValueError('expected Actions run ID must be numeric')
    index = task_index(token, nonce, 'full-project-production.json')
    source = index.read()
    if not source:
        raise ValueError('no authoritative completed-film checkpoint')
    doc = source['doc']
    ref, original = completion_evidence(doc, nonce, expected_run_id)
    checkpoint = ProductionCheckpoint(index, nonce, doc['request_sha256'])
    # A concurrent continuation must never be overwritten with this old view.
    if checkpoint.current != source:
        raise ValueError('completion checkpoint changed; reload before reconciling')
    registry = Registry(B1GitHubCas(task_index(token, nonce, 'project-registry.json')))
    registry.mark_completed(project_id=nonce, archive_ref=ref,
        expected_activity_seq=original['last_activity_seq'],
        expected_project_revision=original['project_revision'],
        operation_id=f'{nonce}-completed')
    saved = copy.deepcopy(doc['saved'])
    saved['registry'] = registry.to_doc()
    if saved != doc['saved']:
        checkpoint.save(saved=saved)
    return dict(nonce=nonce, github_run_id=str(expected_run_id),
                phase=checkpoint.doc['phase'],
                production_status=registry.get(nonce).production_status,
                registry_revision=registry.to_doc()['registry_revision'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--expected-run-id', required=True)
    args = parser.parse_args(argv)
    token = os.environ.get('B1_ARCHIVE_TOKEN', '').strip()
    if not token:
        raise ValueError('B1_ARCHIVE_TOKEN is required')
    print(json.dumps(reconcile(token, args.nonce, args.expected_run_id), ensure_ascii=False))


if __name__ == '__main__':
    main()
