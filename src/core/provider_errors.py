"""Provider failure facts shared by HTTP, queue recovery and cloud patrol.

Only an explicit rejection proves a non-idempotent POST is safe to retry.
Absence of a task ID never proves it. Keep this module stdlib-only.
"""
from __future__ import annotations

import datetime as dt
from email.utils import parsedate_to_datetime
import re
import math

UTC = dt.timezone.utc
SUPPLIER_TZ = dt.timezone(dt.timedelta(hours=8))


def error_text(error):
    return f"{error} {getattr(error, 'body', '') or ''}".lower()


def is_uncertain(error):
    return any(c.__name__ == 'VideoSubmissionUncertain' for c in type(error).__mro__)


def explicit_rejection(error):
    status = getattr(error, 'status', None)
    return (not is_uncertain(error) and isinstance(status, int)
            and 400 <= status < 500 and status != 408)


def daily_quota_rejection(error):
    if error is None or is_uncertain(error):
        return False
    if any(c.__name__ == 'DailyQuotaExhausted' for c in type(error).__mro__):
        return True
    text = error_text(error)
    if getattr(error, 'status', None) is None:
        return str(error).startswith('今日 video_seconds 配额已用')
    if not explicit_rejection(error):
        return False
    # RPM/concurrency/character limits and account balance are not daily quota.
    return any(marker in text for marker in (
        '用量上限', 'daily quota', 'daily limit', 'quota exhausted',
        'quota exceeded', 'insufficient quota', '今日额度', '每日额度',
        '今日配额', '每日配额',
    ))


def retry_after_seconds(value, now=None):
    now = now or dt.datetime.now(UTC)
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except (TypeError, ValueError):
        try:
            until = parsedate_to_datetime(value)
            if until.tzinfo is None:
                until = until.replace(tzinfo=UTC)
            return max(0.0, (until - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def quota_resume_after(error=None, now=None):
    """Honor the provider's dated reset, plus ten minutes to clear reset jitter."""
    now = now or dt.datetime.now(UTC)
    local = now.astimezone(SUPPLIER_TZ)
    until = local.replace(hour=0, minute=10, second=0, microsecond=0)
    if local >= until:
        until += dt.timedelta(days=1)
    text = error_text(error) if error is not None else ''
    match = re.search(r'(\d{4}-\d{2}-\d{2})[ t](\d{2}:\d{2})(?::\d{2})?', text)
    if match:
        try:
            supplied = dt.datetime.fromisoformat(' '.join(match.groups())).replace(
                tzinfo=SUPPLIER_TZ) + dt.timedelta(minutes=10)
            if supplied > now:
                until = supplied
        except ValueError:
            pass
    retry = getattr(error, 'retry_after', None)
    if isinstance(retry, (int, float)):
        try:
            until = max(until, now + dt.timedelta(seconds=retry))
        except (OverflowError, ValueError):
            pass
    return until.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def legacy_quota_rejection(message):
    # Migration of actual persisted Agnes errors, not arbitrary mentions of 429.
    if not isinstance(message, str):
        return False
    if not re.search(r'(?:^|: )(?:重试 \d+ 次仍失败: |Agnes API 错误 )?429:', message):
        return False
    class Rejection(Exception):
        status = 429
    return daily_quota_rejection(Rejection(message))


def safe_submit_retry(params, error=None):
    """Positive proof of non-acceptance; contradictory paid evidence wins."""
    params = params or {}
    if (params.get('provider_task_id') or params.get('unattached_provider_receipt')
            or params.get('unattached_artifact')):
        return False
    state = params.get('provider_submit_state')
    if state in ('unknown', 'submitting', 'accepted'):
        return False
    if state in ('rejected', 'not_sent'):
        return True
    return legacy_quota_rejection(error)


def retry_gate_status(until, now=None):
    now = now or dt.datetime.now(UTC)
    try:
        gate = dt.datetime.strptime(until, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return False, 'invalid retry gate; reconcile before retry'
    return now >= gate, f'quota gate {"open since" if now >= gate else "closed until"} {until}'


def validate_intent_history(intents, paid_attempts):
    """A rejected intent costs no budget; unresolved history still blocks replay."""
    paid_ids = {a.get('video_id') for a in paid_attempts if a.get('state') == 'ACCEPTED'}
    for intent in intents:
        state = intent.get('state')
        if state not in ('REJECTED', 'NOT_SENT', 'ACCEPTED', 'COMPLETED', 'FAILED'):
            raise ValueError('unconfirmed submission intent requires reconciliation')
        if state in ('ACCEPTED', 'COMPLETED', 'FAILED'):
            if not intent.get('video_id') or intent['video_id'] not in paid_ids:
                raise ValueError('intent receipt differs from the paid ledger')
        elif intent.get('video_id'):
            raise ValueError('rejected/not-sent intent contains an accepted receipt')
