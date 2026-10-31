#!/usr/bin/env python3
"""Read-only, bounded Actions/B1 monitor. Never dispatch or submit media."""
from __future__ import annotations
import argparse
import base64
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ACTIVE = {'queued', 'in_progress', 'pending', 'waiting', 'requested'}
CODES = {'WAITING_QUOTA': 6, 'QUOTA_READY': 6, 'ACTIVE': 10, 'WAITING_FOR_CLAIM': 10, 'FINALIZING': 10,
         'CLOUD_PASS_REQUIRES_LOCAL_AUDIT': 20, 'FAILED': 2,
         'INTERRUPTED_RECONCILE': 4, 'RECONCILE_REQUIRED': 4,
         'READ_ERROR': 3, 'MONITOR_TIMEOUT': 5}


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def atomic_json(path, doc):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def load_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    for key in ('repo', 'checkpoint_repo'):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', config[key]):
            raise ValueError('invalid repository')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', config['nonce']):
        raise ValueError('invalid nonce')
    if (not str(config['run_id']).isdigit()
            or not re.fullmatch(r'[0-9a-f]{40}', config['expected_source_sha'])
            or not re.fullmatch(r'[0-9a-f]{64}', config['expected_request_sha256'])):
        raise ValueError('invalid run/source/request identity')
    return config


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        if urllib.parse.urlsplit(url).scheme != 'https':
            raise ValueError('insecure artifact redirect rejected')
        result = super().redirect_request(request, fp, code, message, headers, url)
        if urllib.parse.urlsplit(url).netloc != 'api.github.com':
            result.remove_header('Authorization')
        return result


class GitHub:
    def __init__(self, token):
        self.token = token
        self.opener = urllib.request.build_opener(Redirect())

    def open(self, path):
        if not path.startswith('/repos/'):
            raise ValueError('only repository GET endpoints are supported')
        request = urllib.request.Request('https://api.github.com' + path, method='GET',
            headers={'Authorization': 'Bearer ' + self.token,
                     'Accept': 'application/vnd.github+json', 'User-Agent': 'FullProject-read-only-watch'})
        for attempt in range(3):
            try:
                return self.opener.open(request, timeout=60)
            except urllib.error.HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise RuntimeError(f'GitHub GET failed: HTTP {error.code}') from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == 2:
                    raise RuntimeError('GitHub GET transport failed; state is unknown') from None
            time.sleep(2 ** attempt)

    def get(self, path):
        with self.open(path) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise ValueError('JSON response exceeds limit')
        return json.loads(data)

    def download(self, path, dest, max_bytes, expected_sha=None):
        dest = Path(dest); part = dest.with_suffix(dest.suffix + '.part')
        digest = hashlib.sha256(); size = 0
        try:
            with self.open(path) as response, part.open('wb') as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk: break
                    size += len(chunk)
                    if size > max_bytes: raise ValueError('download exceeds configured limit')
                    digest.update(chunk); output.write(chunk)
            sha = digest.hexdigest()
            if expected_sha and sha != expected_sha:
                raise ValueError('artifact SHA256 mismatch')
            os.replace(part, dest)
            return {'file': dest.name, 'bytes': size, 'sha256': sha,
                    'expected_sha256_verified': expected_sha is not None}
        finally:
            part.unlink(missing_ok=True)


def checkpoint_path(config, filename='full-project-production.json'):
    branch = urllib.parse.quote('test/e2e-' + config['nonce'], safe='')
    return (f"/repos/{config['checkpoint_repo']}/contents/e2e/{config['nonce']}/{filename}?ref={branch}")


def contents_document(api, path):
    value = api.get(path)
    if value.get('encoding') != 'base64' or not value.get('content'):
        raise ValueError('checkpoint content unavailable; never assume it is absent')
    return json.loads(base64.b64decode(value['content']))


def observed_state(config, run, checkpoint, now=None):
    now = now or utc_now()
    if (str(run['id']) != str(config['run_id'])
            or run['head_sha'] != config['expected_source_sha']
            or run.get('repository', {}).get('full_name') != config['repo']):
        raise ValueError('Actions run/source/repository identity mismatch')
    cp = checkpoint
    if (cp.get('nonce') != config['nonce']
            or cp.get('request_sha256') != config['expected_request_sha256']
            or cp.get('full_run_id') != config['expected_full_run_id']):
        raise ValueError('checkpoint request/native project identity mismatch')
    owner_matches = str(cp.get('github_run_id')) == str(run['id'])
    if not owner_matches:
        age = (now - dt.datetime.fromisoformat(run['created_at'].replace('Z', '+00:00'))).total_seconds()
        if (run['status'] in ACTIVE and age < 900
                and str(cp.get('github_run_id')) == str(config.get('resume_from_run_id'))):
            return {'state': 'WAITING_FOR_CLAIM', 'accepted_tasks': 0,
                    'note': 'Previous checkpoint is not the new run result.'}
        raise ValueError('checkpoint belongs to another Actions run; reconcile first')
    if cp.get('source_sha') != config['expected_source_sha']:
        raise ValueError('checkpoint source mismatch')
    attempts = cp.get('video_attempts') or []
    if any(type(a.get('seconds')) is not int or not 4 <= a['seconds'] <= 12 for a in attempts):
        raise ValueError('invalid cumulative video budget record')
    if sum(a['seconds'] for a in attempts) > config['video_budget_seconds']:
        raise ValueError('cumulative paid budget exceeded')
    ids = [a.get('video_id') for a in attempts if a.get('state') == 'ACCEPTED']
    if any(not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError('missing/duplicate accepted video ID')
    previous = config['preserved_video_ids']
    if [a.get('video_id') for a in attempts[:len(previous)]] != previous:
        raise ValueError('original paid receipts changed')
    state = {'state': 'ACTIVE', 'accepted_tasks': len(ids), 'submitted_seconds': sum(a['seconds'] for a in attempts),
             'image_calls': cp.get('image_calls'), 'text_calls': cp.get('text_calls'),
             'note': 'Accepted tasks are not completed clips or final-film duration.'}
    if any(a.get('state') not in ('ACCEPTED', 'SUBMITTING') for a in attempts):
        state['state'] = 'RECONCILE_REQUIRED'; return state
    for attempt in attempts:
        if attempt.get('state') == 'SUBMITTING':
            started = dt.datetime.fromisoformat(attempt['started_at'].replace('Z', '+00:00'))
            if (now - started).total_seconds() > 300:
                state['state'] = 'RECONCILE_REQUIRED'
                state['note'] = 'Submission has no accepted receipt after five minutes.'
                return state
    if cp.get('phase') == 'WAITING_QUOTA' or cp.get('terminal_status') == 'quota_exhausted':
        from tools.quota_gate import gate_status, parse_gate
        if parse_gate(cp.get('quota_resume_after')) is None:
            state.update(state='RECONCILE_REQUIRED', note='Quota wait has no valid reset gate.')
        else:
            ready, reason = gate_status(cp, now)
            state.update(state='QUOTA_READY' if ready else 'WAITING_QUOTA',
                         note=reason, quota_resume_after=cp['quota_resume_after'])
        return state
    if run['status'] == 'completed':
        if run.get('conclusion') != 'success':
            state['state'] = 'INTERRUPTED_RECONCILE' if cp.get('phase') in ('RUNNING', 'RESTORED') else 'FAILED'
        elif (cp.get('phase') == 'COMPLETE' and not cp.get('error')
                and cp.get('terminal_status') in (None, 'completed')
                and type(cp.get('duration_s')) in (int, float)
                and math.isfinite(cp['duration_s']) and cp['duration_s'] >= config['minimum_duration_seconds']
                and all(a.get('state') == 'ACCEPTED' for a in attempts)
                and cp.get('saved', {}).get('restorable_level') == 'full'):
            state['state'] = 'CLOUD_PASS_REQUIRES_LOCAL_AUDIT'
        else: state['state'] = 'RECONCILE_REQUIRED'
        return state
    if run['status'] not in ACTIVE:
        raise ValueError('unknown Actions state')
    updated = dt.datetime.fromisoformat(cp['updated_at'].replace('Z', '+00:00'))
    if (now - updated).total_seconds() > 300:
        state['state'] = 'RECONCILE_REQUIRED'; state['note'] = 'B1 heartbeat stale for more than five minutes.'
    elif cp.get('phase') in ('COMPLETE', 'FAILED', 'BLOCKED_PREFLIGHT', 'INTERRUPTED'):
        state['state'] = 'FINALIZING'
    elif cp.get('phase') not in ('STARTING', 'RESTORED', 'RUNNING', 'PAUSING'):
        raise ValueError('unknown checkpoint phase')
    return state


def snapshot(api, config):
    run = api.get(f"/repos/{config['repo']}/actions/runs/{config['run_id']}")
    checkpoint = contents_document(api, checkpoint_path(config))
    state = observed_state(config, run, checkpoint)
    return {'checked_at': utc_now().isoformat(), 'run': run, 'checkpoint': checkpoint, **state}


def collect_evidence(api, config, observation, output, max_bytes):
    """Download immutable evidence only after Actions finished; never extract or execute it."""
    if observation['run']['status'] != 'completed':
        return {'note': 'Run still active; terminal artifacts are not available yet.'}
    repo = '/repos/' + config['repo']; run_id = config['run_id']
    result = {'logs': api.download(f'{repo}/actions/runs/{run_id}/logs', output/'actions-logs.zip', max_bytes)}
    atomic_json(output/'evidence.json', result)
    artifacts = api.get(f'{repo}/actions/runs/{run_id}/artifacts?per_page=100')['artifacts']
    candidates = [a for a in artifacts if a['name'] == 'e2e-evidence' and not a['expired']]
    if len(candidates) != 1:
        raise ValueError('expected one retained e2e-evidence artifact; preserve logs and reconcile')
    artifact = candidates[0]; digest = artifact.get('digest', '')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('missing artifact SHA256')
    result['artifact'] = api.download(f"{repo}/actions/artifacts/{artifact['id']}/zip",
                                     output/'e2e-evidence.zip', max_bytes, digest.split(':')[1])
    result['artifact'].update(id=artifact['id'], name=artifact['name'])
    atomic_json(output/'evidence.json', result)
    if observation['state'] == 'CLOUD_PASS_REQUIRES_LOCAL_AUDIT':
        with zipfile.ZipFile(output/'e2e-evidence.zip') as archive:
            info = archive.getinfo(f"e2e_run/{config['nonce']}/summary.json")
            if info.file_size > 4 * 1024 * 1024: raise ValueError('oversized summary')
            summary = json.loads(archive.read(info))
        if (summary.get('status') != 'PASS' or summary.get('nonce') != config['nonce']
                or str(summary.get('github_run_id')) != str(run_id)
                or summary.get('source_sha') != config['expected_source_sha']):
            raise ValueError('summary/run identity or PASS mismatch')
        media = summary.get('media', {})
        duration = media.get('duration_s')
        sources = summary.get('source_videos') or []
        accepted = {a['video_id'] for a in observation['checkpoint']['video_attempts']}
        if (type(duration) not in (int, float) or not math.isfinite(duration)
                or duration < config['minimum_duration_seconds'] or media.get('full_decode_ok') is not True
                or len(sources) != config['expected_shot_count']
                or len({s.get('shot_id') for s in sources}) != len(sources)
                or len({s.get('video_id') for s in sources}) != len(sources)
                or len({s.get('sha256') for s in sources}) != len(sources)
                or any(s.get('video_id') not in accepted or not re.fullmatch(r'[0-9a-f]{64}', s.get('sha256', '')) for s in sources)
                or summary.get('saved', {}).get('archive_ref') != observation['checkpoint']['saved']['archive_ref']):
            raise ValueError('summary media/source/archival evidence mismatch')
        result['cloud_summary'] = summary
        registry = contents_document(api, checkpoint_path(config, 'project-registry.json'))
        record = registry.get('projects', {}).get(config['nonce'], {})
        if (record.get('archive_status') != 'VERIFIED_RESTORABLE'
                or record.get('archive_ref') != observation['checkpoint']['saved']['archive_ref']):
            raise ValueError('authoritative registry archive receipt mismatch')
        atomic_json(output/'registry.json', registry)
        atomic_json(output/'summary.json', summary)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=int, default=60)
    parser.add_argument('--timeout-minutes', type=int, default=180)
    parser.add_argument('--collect-on-stop', action='store_true')
    parser.add_argument('--max-download-mib', type=int, default=1024)
    args = parser.parse_args(argv)
    if not 10 <= args.interval <= 3600 or not 1 <= args.timeout_minutes <= 1440 or not 1 <= args.max_download_mib <= 4096:
        parser.error('interval/timeout/download limit out of range')
    config = load_config(args.config)
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token:
        parser.error('set GH_TOKEN or GITHUB_TOKEN locally; do not put it in config or command arguments')
    api = GitHub(token); output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    binding = {'repo': config['repo'], 'run_id': config['run_id'], 'source_sha': config['expected_source_sha'], 'nonce': config['nonce']}
    binding_file = output/'watch-binding.json'
    if binding_file.exists() and json.loads(binding_file.read_text(encoding='utf-8')) != binding:
        parser.error('output directory is bound to another run')
    atomic_json(binding_file, binding)
    deadline = time.monotonic() + args.timeout_minutes * 60
    errors = 0
    while True:
        try:
            current = snapshot(api, config); errors = 0
        except (ValueError, KeyError, TypeError) as error:
            current = {'checked_at': utc_now().isoformat(), 'state': 'RECONCILE_REQUIRED', 'error': str(error)}
        except (RuntimeError, OSError) as error:
            errors += 1
            current = {'checked_at': utc_now().isoformat(), 'state': 'READ_ERROR', 'error': str(error)}
        # Avoid allowing a read error to expose the credential through diagnostics.
        current = json.loads(json.dumps(current).replace(token, '<REDACTED>'))
        atomic_json(output/'latest.json', current)
        if 'run' in current:
            atomic_json(output/'snapshot-last-good.json', current)
        with (output/'timeline.jsonl').open('a', encoding='utf-8') as log:
            log.write(json.dumps({k:current.get(k) for k in ('checked_at','state','accepted_tasks','submitted_seconds','image_calls','text_calls','error')}, ensure_ascii=False)+'\n')
        print(json.dumps({k:current.get(k) for k in ('checked_at','state','accepted_tasks','submitted_seconds','image_calls','text_calls','error')}, ensure_ascii=False), flush=True)
        state = current['state']; terminal = CODES[state] not in (10, 3) or (state == 'READ_ERROR' and errors >= 3)
        if terminal or args.once:
            if args.collect_on_stop and current.get('run', {}).get('status') == 'completed':
                try:
                    evidence = collect_evidence(api, config, current, output, args.max_download_mib*1024**2)
                    atomic_json(output/'evidence.json', evidence)
                except (RuntimeError, OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
                    atomic_json(output/'collection-error.json', {'error': str(error).replace(token, '<REDACTED>')})
                    print('Evidence collection failed; preserve downloaded files and reconcile.', flush=True)
                    return 4
            return CODES[state]
        if time.monotonic() >= deadline:
            atomic_json(output/'monitor-timeout.json', {'state': 'MONITOR_TIMEOUT', 'checked_at': utc_now().isoformat(), 'last_state': state})
            return 5
        time.sleep(min(args.interval, max(0, deadline-time.monotonic())))


if __name__ == '__main__':
    raise SystemExit(main())
