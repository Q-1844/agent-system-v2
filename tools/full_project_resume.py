"""Restore the exact failed acceptance snapshot; never mint a replacement task."""
from __future__ import annotations
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import urllib.request
import zipfile


class _DownloadRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        result = super().redirect_request(request, fp, code, message, headers, url)
        if not url.startswith('https://api.github.com/'):
            result.remove_header('Authorization')
        return result


def _recovery_project_name(nonce, project_name=None):
    """Validate the exact project binding without broadening ZIP extraction."""
    from src.ai.cross_project_assets import safe_project_dir
    # The default must remain byte-compatible with legacy 200/320 recovery.
    name = project_name or f'cloud3min_{nonce}'
    # Use a synthetic root to validate path components without touching disk.
    safe_project_dir(Path('/__recovery_project_guard__'), name, must_exist=False)
    return name


def download_snapshot(run_id, nonce, token, dest, *, project_name=None):
    if not str(run_id).isdigit() or not token:
        raise ValueError('resume requires the source Actions run ID and read token')
    opener = urllib.request.build_opener(_DownloadRedirect())
    def get(path, binary=False):
        req = urllib.request.Request('https://api.github.com/repos/Q-1844/agent-system-v2'+path,
            headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
                     'User-Agent':'FullProject-snapshot-recovery'})
        with opener.open(req, timeout=120) as response:
            data = response.read(1024 * 1024 * 1024 + 1)
        if len(data) > 1024 * 1024 * 1024:
            raise ValueError('acceptance artifact exceeds recovery limit')
        return data if binary else json.loads(data)
    run = get(f'/actions/runs/{run_id}')
    if run['status'] != 'completed' or run['conclusion'] not in ('failure', 'cancelled', 'timed_out'):
        raise ValueError('source run must have stopped before recovery')
    artifacts = get(f'/actions/runs/{run_id}/artifacts')['artifacts']
    artifacts = [a for a in artifacts if a['name']=='e2e-evidence' and not a['expired']]
    if len(artifacts) != 1:
        raise ValueError('expected exactly one retained acceptance artifact')
    artifact = artifacts[0]
    data = get(f"/actions/artifacts/{artifact['id']}/zip", True)
    digest = hashlib.sha256(data).hexdigest()
    if artifact.get('digest') != 'sha256:'+digest:
        raise ValueError('Actions artifact SHA256 mismatch')
    dest = Path(dest)
    if dest.exists():
        raise FileExistsError('recovery staging directory already exists')
    dest.mkdir(parents=True)
    pname = _recovery_project_name(nonce, project_name)
    allowed = (f'projects/{pname}/', f'e2e_run/{nonce}/')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 2*1024**3:
            raise ValueError('expanded acceptance snapshot exceeds recovery limit')
        skipped_members = 0
        for info in archive.infolist():
            name = PurePosixPath(info.filename)
            if (name.is_absolute() or '..' in name.parts or '\\' in info.filename
                    or stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError('unsafe acceptance artifact member')
            if not (info.filename.startswith(allowed) or info.filename in ('cache/jobs.json','cache/quota.json')):
                # C2 无人值守链会把按需恢复的素材源项目一并上传（workflow 上传
                # projects/cloud3min_* 通配），它们不属于恢复目标；跳过安全成员，
                # 只拒收危险路径，避免一次无害多目录把整个快照判死。
                skipped_members += 1
                continue
            archive.extract(info, dest)
    return {'run_id':str(run_id),'source_sha':run['head_sha'],'artifact_id':artifact['id'],
            'artifact_sha256':digest,'artifact_bytes':len(data),'skipped_members':skipped_members}


def validate_completed_clip(path, requested_seconds):
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
        '-of', 'json', str(path)], timeout=60))
    streams = [s for s in probe['streams'] if s.get('codec_type') == 'video'
               and not s.get('disposition', {}).get('attached_pic')]
    duration = float(streams[0].get('duration', 'nan')) if len(streams) == 1 else float('nan')
    if not math.isfinite(duration) or not requested_seconds - .5 <= duration <= requested_seconds + 1:
        raise ValueError('completed source clip has invalid moving-video duration')
    subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path), '-f', 'null', '-'],
                   capture_output=True, check=True, timeout=120)


def reconcile_finalization(project, rewritten, jobs, accepted_ids, provenance):
    """Only settle committed selected versions; never invent a version or receipt."""
    doc = rewritten[project/'output/project.json']
    selected = {}
    for scene in doc['scenes']:
        for shot in scene['shots']:
            matches = [v for v in shot.get('video_versions', [])
                       if v.get('version_id') == shot.get('final_video') and v.get('status') == 'done']
            if len(matches) != 1 or shot.get('video', {}).get('status') != 'done':
                raise ValueError('finalize-only requires every original shot selected and completed')
            selected[shot['shot_id']] = matches[0]
    if len(selected) != len(accepted_ids) or {v['video_id'] for v in selected.values()} != set(accepted_ids):
        raise ValueError('finalize-only selected clips do not match all cumulative receipts')
    reconciled = []
    for job in jobs.values():
        if job['state'] in ('done', 'failed'):
            continue
        version = selected.get(job.get('shot_id'))
        params = job.get('params') or {}
        scene_run = rewritten.get(project/'output/ai/autopilot_runs'/f"{params.get('origin_run_id')}.json", {})
        states = [s for s in scene_run.get('shots', []) if s.get('shot_id') == job.get('shot_id')]
        if (job['state'] != 'running' or job.get('kind') != 'video' or not version
                or job.get('error') or job.get('progress') != 100
                or job.get('error_stage') not in ('done', 'endframe', 'first_frame_sync')
                or params.get('provider_submit_state') != 'accepted'
                or params.get('provider_task_id') != version['video_id']
                or params.get('origin_run_id') != version.get('origin_run_id')
                or len(states) != 1 or states[0].get('active_job_id') != job['job_id']):
            raise ValueError('unresolved job is not a committed completed video; reconcile first')
        job.update(state='done', error=None, message='Completed local video reconciled from stopped snapshot')
        job['params']['snapshot_reconciliation'] = dict(provenance)
        reconciled.append(job['job_id'])
    for path, native in rewritten.items():
        if not (path.name == 'run.json' or 'autopilot_runs' in path.parts):
            continue
        if native.get('cancel_requested') or native.get('status') == 'cancelled':
            raise ValueError('finalize-only cannot resume cancelled work')
        if native.get('status') in ('running', 'pending', 'pausing', 'producing'):
            native.update(status='paused', pause_requested=False)
        elif native.get('status') not in ('completed', 'paused', 'failed'):
            raise ValueError('unsupported native controller state for finalization')
        # Failed stage checkpoints are not silently rewritten. The provider gate
        # will reject any attempt to generate missing input during native resume.
    return reconciled


def restore_snapshot(staging, workspace, nonce, checkpoint, provenance, *, finalize_only=False, project_name=None):
    """Validate identity/paid receipts before copying any state into the runner."""
    staging, workspace = Path(staging), Path(workspace).resolve()
    if (str(checkpoint.get('github_run_id')) != provenance['run_id']
            or checkpoint.get('source_sha') != provenance['source_sha']):
        raise ValueError('checkpoint belongs to a different Actions run')
    if checkpoint.get('phase') not in ('FAILED', 'RUNNING', 'RESTORED', 'BLOCKED_PREFLIGHT',
                                      'AWAITING_HUMAN',
                                      *(('PAUSING', 'INTERRUPTED') if finalize_only else ())):
        raise ValueError('checkpoint is not a failed/interrupted production run')
    summary = json.loads((staging/f'e2e_run/{nonce}/summary.json').read_text())
    # 早期失败的 run（如 BLOCKED_PREFLIGHT）在 finally 写 summary 时可能尚未
    # 建立生产上下文，source_sha 等字段缺失；此时以 checkpoint 为权威（它已
    # 通过上一组校验），只拒绝"写了但写错"的情况。
    if (summary.get('nonce') != nonce or str(summary.get('github_run_id')) != provenance['run_id']
            or (summary.get('source_sha') is not None
                and summary.get('source_sha') != provenance['source_sha'])):
        raise ValueError('snapshot summary does not match the source run')
    pname = _recovery_project_name(nonce, project_name)
    if project_name and summary.get('project_name') not in (None, project_name):
        raise ValueError('snapshot project name differs from frozen continuation task')
    project = staging/'projects'/pname
    doc = json.loads((project/'output/project.json').read_text())
    if doc['name'] != pname or (summary.get('full_run_id') is not None
                                and summary.get('full_run_id') != checkpoint.get('full_run_id')):
        raise ValueError('project/native run identity mismatch')
    full_path = project/'output/full_runs'/checkpoint['full_run_id']/'run.json'
    full = json.loads(full_path.read_text())
    if (full.get('run_id') != checkpoint['full_run_id'] or full.get('project_name') != pname
            or full.get('status') not in ('failed', 'paused', *(('pausing', 'producing', 'completed') if finalize_only else ()))):
        raise ValueError('native FullRun is not safely resumable')
    native_paths = [*project.glob('output/full_runs/*/run.json'),
                    *project.glob('output/production_runs/*/run.json'),
                    *project.glob('output/ai/autopilot_runs/*.json')]
    for native_path in native_paths:
        native = json.loads(native_path.read_text())
        if not finalize_only and native.get('status') not in ('failed','paused','completed','blocked','cancelled'):
            raise ValueError('active native controller must be reconciled before startup')
    old_root = str(summary['project_dir']) if summary.get('project_dir') else None
    relative_root = 'projects/'+pname
    if old_root and old_root != relative_root and not old_root.endswith('/'+relative_root):
        raise ValueError('snapshot project directory is invalid')
    roots = [relative_root]
    if old_root and old_root != relative_root:
        roots.append(old_root)
    elif summary.get('working_directory'):
        roots.append(str(PurePosixPath(summary['working_directory'])/relative_root))
    def project_suffix(value):
        for root in roots:
            if value.startswith(root+'/'):
                suffix = PurePosixPath(value[len(root)+1:])
                if suffix.is_absolute() or '..' in suffix.parts:
                    raise ValueError('unsafe saved local path')
                return suffix
        return None
    attempts = checkpoint.get('video_attempts') or []
    ids = [a.get('video_id') for a in attempts]
    # Legacy 200/320 still use their historical, verified limits; auto C1/C2
    # binds its own upper limit at Stage0 media authorization. Never derive
    # this cap from live missing seconds or a new runner's fresh client.
    historical = 384 if str(checkpoint.get('film_target_seconds')) == '320' else 240
    authorized = max(checkpoint.get('authorized_video_seconds', historical),
                     checkpoint.get('execution_video_authorization', 0))
    if (type(authorized) is not int or not 0 < authorized <= 1_000_000_000 or
            any(a.get('state')!='ACCEPTED' or not a.get('video_id')
                or type(a.get('seconds')) is not int or not 4<=a['seconds']<=12 for a in attempts)
            or len(ids)!=len(set(ids)) or sum(a['seconds'] for a in attempts)>authorized):
        raise ValueError('unconfirmed/duplicate paid submission requires reconciliation')
    versions = [v for sc in doc['scenes'] for shot in sc['shots'] for v in shot.get('video_versions',[])]
    verified = []
    for attempt in attempts:
        matches = [v for v in versions if v.get('video_id')==attempt['video_id'] and v.get('status')=='done']
        if len(matches)!=1:
            raise ValueError('accepted video is not a single completed local version; reconcile first')
        local = matches[0].get('local') or ''
        suffix = project_suffix(local)
        if suffix is None:
            raise ValueError('completed video path is outside the project')
        file = project/str(suffix)
        if not file.resolve().is_relative_to(project.resolve()) or not file.is_file() or not file.stat().st_size:
            raise ValueError('completed paid video is missing from the snapshot')
        if finalize_only:
            validate_completed_clip(file, attempt['seconds'])
        verified.append({'video_id':attempt['video_id'],'version_id':matches[0]['version_id'],
                         'bytes':file.stat().st_size,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
    target = workspace/'projects'/pname
    if target.exists() or (workspace/'cache/jobs.json').exists():
        raise FileExistsError('recovery cannot overwrite an existing project/job queue')
    def rebase(value):
        if isinstance(value,str):
            suffix = project_suffix(value)
            if suffix is not None:
                return str(target/str(suffix))
        if isinstance(value,list):return [rebase(v) for v in value]
        if isinstance(value,dict):return {k:rebase(v) for k,v in value.items()}
        return value
    # Validate all JSON before making the restored project visible to startup.
    rewritten = {p:rebase(json.loads(p.read_text())) for p in project.rglob('*.json')}
    jobs = json.loads((staging/'cache/jobs.json').read_text())
    if any(j['project'] != pname for j in jobs.values()):
        raise ValueError('snapshot contains unrelated jobs')
    reconciled_jobs = reconcile_finalization(project, rewritten, jobs, ids, provenance) if finalize_only else []
    if any(j['project']!=pname or j['state'] not in ('done','failed') for j in jobs.values()):
        raise ValueError('snapshot contains unrelated or unresolved queued work')
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(project,target)
    for path,value in rewritten.items():
        (target/path.relative_to(project)).write_text(json.dumps(value,ensure_ascii=False,indent=2))
    (workspace/'cache').mkdir(exist_ok=True)
    (workspace/'cache/jobs.json').write_text(json.dumps(rebase(jobs),ensure_ascii=False,indent=2))
    shutil.copyfile(staging/'cache/quota.json',workspace/'cache/quota.json')
    return {**provenance,'project_name':pname,'full_run_id':checkpoint['full_run_id'],
            'reused_completed_videos':verified,'previous_video_seconds':sum(a['seconds'] for a in attempts),
            'finalize_only':finalize_only, 'reconciled_jobs':reconciled_jobs}
