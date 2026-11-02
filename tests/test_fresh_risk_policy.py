import copy

import pytest

from tools.full_project_cloud_acceptance import initialize_submission_recovery_policy
from src.core.submission_recovery import policy_valid
from tests.test_day_continuation import Journal


ENV = dict(CONFIRM_UNCERTAIN_SUBMISSIONS='true', GITHUB_ACTOR='operator', GITHUB_RUN_ID='20')


def fresh():
    journal = Journal()
    journal.doc = dict(nonce='film', request_sha256='a'*64, phase='STAGE0_BOUND', github_run_id='20')
    return journal


def test_new_task_explicit_future_consent_is_persisted_and_idempotent():
    journal = fresh()
    initialize_submission_recovery_policy(journal, ENV)
    policy = journal.doc['submission_recovery_policy']
    assert policy_valid(policy, 'film', 'a'*64)
    assert policy['actor'] == 'operator' and policy['authorized_run_id'] == '20'
    assert 'video_attempts' not in journal.doc and len(journal.writes) == 1
    initialize_submission_recovery_policy(journal, ENV)
    assert len(journal.writes) == 1


@pytest.mark.parametrize('env', [{}, {'CONFIRM_UNCERTAIN_SUBMISSIONS':'false'}, dict(ENV, RESUME_RUN_ID='10')])
def test_default_and_resume_cannot_create_fresh_policy(env):
    journal = fresh();before = copy.deepcopy(journal.doc)
    initialize_submission_recovery_policy(journal, env)
    assert journal.doc == before and not journal.writes


@pytest.mark.parametrize('damage', ['started','run','wrong_policy','actor','run_identity','invalid_boolean'])
def test_fresh_policy_rejects_stale_work_or_missing_authorization_identity(damage):
    journal = fresh();env = dict(ENV)
    if damage == 'started':journal.doc['video_attempts'] = [{'state':'SUBMITTING'}]
    if damage == 'run':journal.doc['full_run_id'] = 'full'
    if damage == 'wrong_policy':journal.doc['submission_recovery_policy'] = {'nonce':'other'}
    if damage == 'actor':env.pop('GITHUB_ACTOR')
    if damage == 'run_identity':env.pop('GITHUB_RUN_ID')
    if damage == 'invalid_boolean':env['CONFIRM_UNCERTAIN_SUBMISSIONS'] = 'yes'
    with pytest.raises(ValueError):initialize_submission_recovery_policy(journal, env)
    assert not journal.writes
