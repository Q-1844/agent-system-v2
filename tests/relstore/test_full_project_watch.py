"""The monitor must never confuse observation, acceptance and paid resubmission."""
import copy
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import urllib.request

import pytest
from tools import watch_full_project as watch

NOW = dt.datetime(2026,10,24,3,40,tzinfo=dt.timezone.utc)


def sample():
    config = json.loads((Path(watch.__file__).parent/'watch_film3m_20261024.json').read_text())
    run = {'id':config['run_id'],'head_sha':config['expected_source_sha'],'repository':{'full_name':config['repo']},
           'created_at':'2026-10-24T02:46:23Z','status':'in_progress','conclusion':None}
    cp = {'nonce':config['nonce'],'request_sha256':config['expected_request_sha256'],
          'github_run_id':str(config['run_id']),'source_sha':config['expected_source_sha'],
          'full_run_id':config['expected_full_run_id'],'updated_at':NOW.isoformat(), 'phase':'RUNNING',
          'video_attempts':[{'video_id':i,'state':'ACCEPTED','seconds':10} for i in config['preserved_video_ids']]}
    return config,run,cp


@pytest.mark.parametrize('change', ['run_source','cp_source','owner','nonce','request','native_id'])
def test_run_and_checkpoint_identity_are_pinned(change):
    config,run,cp = sample()
    if change=='run_source':run['head_sha']='0'*40
    if change=='cp_source':cp['source_sha']='0'*40
    if change=='owner':cp['github_run_id']='999'
    if change=='nonce':cp['nonce']='another'
    if change=='request':cp['request_sha256']='0'*64
    if change=='native_id':cp['full_run_id']='other-full-run'
    with pytest.raises(ValueError):watch.observed_state(config,run,cp,NOW)


def test_startup_does_not_attribute_previous_failed_checkpoint_to_new_run():
    config,run,cp = sample();cp.update(github_run_id=str(config['resume_from_run_id']),phase='FAILED')
    now=dt.datetime(2026,10,24,2,48,tzinfo=dt.timezone.utc)
    assert watch.observed_state(config,run,cp,now)['state']=='WAITING_FOR_CLAIM'
    with pytest.raises(ValueError):watch.observed_state(config,run,cp,NOW)


@pytest.mark.parametrize('state,age,expected', [('SUBMITTING',30,'ACTIVE'),('SUBMITTING',301,'RECONCILE_REQUIRED'),('UNKNOWN',30,'RECONCILE_REQUIRED')])
def test_pending_and_unknown_video_receipts_are_never_counted_as_accepted(state,age,expected):
    config,run,cp=sample()
    cp['video_attempts'].append({'state':state,'seconds':10,'started_at':(NOW-dt.timedelta(seconds=age)).isoformat()})
    out=watch.observed_state(config,run,cp,NOW)
    assert out['accepted_tasks']==2 and out['state']==expected and out['submitted_seconds']==30


@pytest.mark.parametrize('conclusion,phase,expected', [('failure','FAILED','FAILED'),('timed_out','RUNNING','INTERRUPTED_RECONCILE'),('success','RUNNING','RECONCILE_REQUIRED')])
def test_actions_terminal_does_not_imply_film_success(conclusion,phase,expected):
    config,run,cp=sample();run.update(status='completed',conclusion=conclusion);cp['phase']=phase
    assert watch.observed_state(config,run,cp,NOW)['state']==expected


@pytest.mark.parametrize('duration,expected', [(179.9,'RECONCILE_REQUIRED'),(float('nan'),'RECONCILE_REQUIRED'),(float('inf'),'RECONCILE_REQUIRED'),(200,'CLOUD_PASS_REQUIRES_LOCAL_AUDIT')])
def test_remote_success_still_requires_independent_media_audit(duration,expected):
    config,run,cp=sample();run.update(status='completed',conclusion='success')
    cp.update(phase='COMPLETE',duration_s=duration,saved={'restorable_level':'full'})
    assert watch.observed_state(config,run,cp,NOW)['state']==expected


def test_stale_heartbeat_and_duplicate_receipt_stop_monitoring():
    config,run,cp=sample();cp['updated_at']=(NOW-dt.timedelta(seconds=301)).isoformat()
    assert watch.observed_state(config,run,cp,NOW)['state']=='RECONCILE_REQUIRED'
    cp['video_attempts'].append(copy.deepcopy(cp['video_attempts'][0]))
    with pytest.raises(ValueError,match='duplicate'):watch.observed_state(config,run,cp,NOW)


def test_failed_download_preserves_old_evidence_and_strips_auth_on_redirect(tmp_path):
    api=watch.GitHub('test-token');api.open=lambda _:io.BytesIO(b'wrong bytes')
    dest=tmp_path/'evidence.zip';dest.write_bytes(b'previous verified snapshot')
    with pytest.raises(ValueError,match='SHA256'):
        api.download('/repos/owner/repo/path',dest,1024,'0'*64)
    assert dest.read_bytes()==b'previous verified snapshot' and not list(tmp_path.glob('*.part'))
    request=urllib.request.Request('https://api.github.com/repos/o/r/artifacts',headers={'Authorization':'Bearer test-token'})
    redirected=watch.Redirect().redirect_request(request,None,302,'Found',{},'https://blob.example/file?signature=value')
    assert not redirected.has_header('Authorization') and redirected.get_method()=='GET'
    with pytest.raises(ValueError):watch.Redirect().redirect_request(request,None,302,'Found',{},'http://blob.example/file')


def test_once_is_get_only_and_a_read_failure_does_not_erase_last_good_snapshot(tmp_path,monkeypatch):
    config,run,cp=sample();config_path=tmp_path/'config.json';config_path.write_text(json.dumps(config))
    calls=[]
    class API:
        def __init__(self, token):pass
        def get(self,path):calls.append(path);raise RuntimeError('GitHub GET failed: HTTP 403')
    monkeypatch.setenv('GH_TOKEN','test-token');monkeypatch.setattr(watch,'GitHub',API)
    out=tmp_path/'watch';out.mkdir();(out/'snapshot-last-good.json').write_text('preserved')
    code=watch.main(['--config',str(config_path),'--output',str(out),'--once'])
    assert code==3 and (out/'snapshot-last-good.json').read_text()=='preserved'
    assert json.loads((out/'latest.json').read_text())['state']=='READ_ERROR'
    assert calls==[f"/repos/{config['repo']}/actions/runs/{config['run_id']}"]


@pytest.mark.parametrize('corruption',[None,'wrong_run','short_media','duplicate_source','wrong_registry','digest'])
def test_terminal_collection_checks_identity_hashes_and_authoritative_registry(tmp_path,corruption):
    import base64,zipfile
    config,run,cp=sample();run.update(status='completed',conclusion='success')
    for i in range(18):cp['video_attempts'].append({'state':'ACCEPTED','video_id':f'new-{i}','seconds':10})
    ref={'sha256':'a'*64,'asset_id':123}
    cp.update(phase='COMPLETE',duration_s=200,saved={'restorable_level':'full','archive_ref':ref})
    observation={'run':run,'checkpoint':cp,'state':'CLOUD_PASS_REQUIRES_LOCAL_AUDIT'}
    summary={'status':'PASS','nonce':config['nonce'],'github_run_id':str(config['run_id']),'source_sha':config['expected_source_sha'],
             'media':{'duration_s':200,'full_decode_ok':True},'saved':{'archive_ref':ref},
             'source_videos':[{'shot_id':f's{i}','video_id':a['video_id'],'sha256':hashlib.sha256(str(i).encode()).hexdigest()} for i,a in enumerate(cp['video_attempts'])]}
    if corruption=='wrong_run':summary['github_run_id']='other'
    if corruption=='short_media':summary['media']['duration_s']=5
    if corruption=='duplicate_source':summary['source_videos'][1]=copy.deepcopy(summary['source_videos'][0])
    blob=io.BytesIO()
    with zipfile.ZipFile(blob,'w') as archive:archive.writestr(f"e2e_run/{config['nonce']}/summary.json",json.dumps(summary))
    data=blob.getvalue()
    class API(watch.GitHub):
        def open(self,path):return io.BytesIO(b'logs' if path.endswith('/logs') else data)
        def get(self,path):
            if '/artifacts?' in path:return {'artifacts':[{'id':123,'name':'e2e-evidence','expired':False,
                'digest':'sha256:'+('0'*64 if corruption=='digest' else hashlib.sha256(data).hexdigest())}]}
            actual=dict(ref,asset_id=999) if corruption=='wrong_registry' else ref
            registry={'projects':{config['nonce']:{'archive_status':'VERIFIED_RESTORABLE','archive_ref':actual}}}
            return {'encoding':'base64','content':base64.b64encode(json.dumps(registry).encode()).decode()}
    api=API('test-token')
    if corruption:
        with pytest.raises(ValueError):watch.collect_evidence(api,config,observation,tmp_path,1024**2)
        assert (tmp_path/'actions-logs.zip').read_bytes()==b'logs'
    else:
        result=watch.collect_evidence(api,config,observation,tmp_path,1024**2)
        assert result['artifact']['expected_sha256_verified'] and (tmp_path/'registry.json').exists()
