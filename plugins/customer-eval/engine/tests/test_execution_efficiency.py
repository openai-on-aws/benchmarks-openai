import io,json,time,threading,uuid
from types import SimpleNamespace
from unittest.mock import Mock
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from customer_eval.cloud import AgentCorePool
from customer_eval.runner import Store,InterruptedAttempt
from customer_eval.transport import ProviderConnection,authorize,_credential_source
from runtime.coordinator import CoordinatorPaused,deadline_guard
from runtime.receipts import execute_once


def test_refreshable_credentials_reused_and_profile_scoped(monkeypatch):
    import boto3
    from botocore.credentials import ReadOnlyCredentials
    _credential_source.cache_clear();created=[]
    class Credentials:
        def __init__(self):self.calls=0
        def get_frozen_credentials(self):
            self.calls+=1
            return ReadOnlyCredentials('key'+str(self.calls),'secret','token'+str(self.calls))
    def session(**kw):
        c=Credentials();created.append((kw,c));return SimpleNamespace(get_credentials=lambda:c)
    monkeypatch.setattr(boto3,'Session',session);monkeypatch.delenv('EVAL_USE_EXECUTION_ROLE',raising=False)
    route={'auth':'sigv4','credential_ref':'aws_profile:one','region':'us-east-1','sigv4_service':'bedrock'}
    a=authorize(route,'https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1/responses',b'{}')
    b=authorize(route,'https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1/responses',b'{}')
    assert len(created)==1 and a['X-Amz-Security-Token']=='token1' and b['X-Amz-Security-Token']=='token2'
    authorize(dict(route,credential_ref='aws_profile:two'),'https://example.com',b'{}')
    assert len(created)==2
    _credential_source.cache_clear()


def test_http_connection_is_reused_and_stream_not_buffered():
    ports=[]
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*a):pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']));ports.append(self.client_address[1])
            first=b'data: first\n\n';last=b'data: done\n\n'
            self.send_response(200);self.send_header('Content-Length',str(len(first+last)));self.end_headers()
            self.wfile.write(first);self.wfile.flush();time.sleep(.08);self.wfile.write(last);self.wfile.flush()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.01),daemon=True);thread.start()
    pool=ProviderConnection()
    try:
        for _ in range(2):
            start=time.monotonic()
            with pool.open(f'http://127.0.0.1:{server.server_port}',b'{}',{'Content-Type':'application/json'},3) as r:
                iterator=iter(r);assert next(iterator)==b'data: first\n'
                assert time.monotonic()-start<.07
                assert b'data: done\n' in list(iterator)
        assert ports[0]==ports[1]
    finally:pool.close();server.shutdown();server.server_close();thread.join()


def test_pool_rotates_between_trials_and_does_not_limit_job(monkeypatch):
    clock=[0.];monkeypatch.setattr('customer_eval.cloud.time.monotonic',lambda:clock[0]);client=Mock()
    def invoke(**kw):
        p=json.loads(kw['payload']);return {'runtimeSessionId':kw['runtimeSessionId'],
             'response':io.BytesIO(json.dumps({'trial_id':p['trial']['trial_id'],'status':'completed'}).encode())}
    client.invoke_agent_runtime.side_effect=invoke
    pool=AgentCorePool('arn:test',None,'us-east-1',10,1,run_id=str(uuid.uuid4()),client=client,max_session_seconds=300)
    a=pool.invoke({'trial_id':'a'});b=pool.invoke({'trial_id':'b'})
    assert a['runtime_session_id']==b['runtime_session_id'] and b['dispatch_timing']['environment_reused']
    clock[0]=3700
    c=pool.invoke({'trial_id':'c'});assert c['runtime_session_id']!=a['runtime_session_id']
    pool.close();pool.close();assert client.stop_runtime_session.call_count==2


def test_lost_response_is_not_blindly_reinvoked():
    client=Mock();client.invoke_agent_runtime.side_effect=TimeoutError()
    pool=AgentCorePool('arn:test',None,'us-east-1',10,1,run_id=str(uuid.uuid4()),client=client)
    with pytest.raises(TimeoutError):pool.invoke({'trial_id':'t'})
    assert client.invoke_agent_runtime.call_count==1
    pool.close();client.stop_runtime_session.assert_not_called()


class FakeS3:
    class Missing(Exception):pass
    exceptions=SimpleNamespace(NoSuchKey=Missing)
    def __init__(self):self.data={}
    def list_objects_v2(self,**kw):
        assert kw['MaxKeys']==1
        return {'Contents':[{'Key':k} for k in sorted(self.data) if k.startswith(kw['Prefix'])][:1]}
    def get_object(self,**kw):
        if kw['Key'] not in self.data:raise self.Missing()
        return {'Body':io.BytesIO(self.data[kw['Key']])}
    def put_object(self,**kw):
        assert kw['IfNoneMatch']=='*'
        if kw['Key'] in self.data:raise AssertionError('Duplicate write')
        self.data[kw['Key']]=kw['Body'];return {}


def test_worker_result_survives_lost_coordinator_and_is_not_reexecuted(monkeypatch):
    monkeypatch.setenv('EVAL_ARTIFACT_BUCKET','bucket');s3=FakeS3()
    payload={'run_id':str(uuid.uuid4()),'trial':{'trial_id':'a'*64,'prompt':'fixed'}}
    operation=Mock(return_value={'trial_id':'a'*64,'status':'completed','usage':{'tokens':5}})
    a=execute_once(payload,operation,s3=s3);b=execute_once(payload,operation,s3=s3)
    assert operation.call_count==1 and a['usage']==b['usage'] and b['receipt_recovered']
    with pytest.raises(ValueError,match='fingerprint'):
        execute_once(dict(payload,trial={'trial_id':'a'*64,'prompt':'changed'}),operation,s3=s3)


def test_unfinished_claim_cannot_duplicate_paid_work(monkeypatch):
    monkeypatch.setenv('EVAL_ARTIFACT_BUCKET','bucket');s3=FakeS3()
    payload={'run_id':str(uuid.uuid4()),'trial':{'trial_id':'b'*64}}
    operation=Mock(side_effect=TimeoutError())
    with pytest.raises(TimeoutError):execute_once(payload,operation,s3=s3)
    assert execute_once(payload,operation,s3=s3)['status']=='in_progress'
    assert operation.call_count==1


def test_missing_receipt_does_not_require_bucket_wide_access():
    from runtime.receipts import read
    s3=Mock();s3.list_objects_v2.return_value={'Contents':[]}
    assert read(s3,'bucket','customer-evaluation/trial-receipts/run/trial.json') is None
    s3.get_object.assert_not_called()
    s3.list_objects_v2.assert_called_once_with(Bucket='bucket',Prefix='customer-evaluation/trial-receipts/run/trial.json',MaxKeys=1)
    s3.list_objects_v2.side_effect=PermissionError('Real access denial')
    with pytest.raises(PermissionError):read(s3,'bucket','customer-evaluation/trial-receipts/run/trial.json')


def test_store_recovers_receipt_without_another_charge(tmp_path):
    store=Store(tmp_path,'identity',10)
    with pytest.raises(TimeoutError):store.perform('trial',2,lambda:(_ for _ in ()).throw(TimeoutError()))
    operation=Mock();result=store.perform('trial',2,operation,recover=lambda:{'status':'completed'})
    assert result['status']=='completed' and store.reserved()==2
    operation.assert_not_called()


def test_deadline_checkpoint_preserves_transaction_and_no_one_hour_limit(tmp_path):
    deadline_guard(0,28800,180,clock=lambda:3700)
    with pytest.raises(CoordinatorPaused):deadline_guard(0,28800,180,clock=lambda:28700)
    store=Store(tmp_path,'identity',10)
    store.before_new_attempt=lambda:deadline_guard(0,28800,180,clock=lambda:28700)
    with pytest.raises(CoordinatorPaused):store.perform('trial',2,lambda:None)
    assert store.reserved()==0 and not store.db.in_transaction
    store.before_new_attempt=lambda:None
    assert store.perform('trial',2,lambda:'ok')=='ok'


def test_runner_keeps_uncertain_trial_pending_and_recovers_without_replay(tmp_path):
    from customer_eval.contract import load,ROOT
    from customer_eval.runner import Runner
    from customer_eval.simulation import execute
    cfg=load(ROOT/'examples/customer-support/input.yaml')
    cfg['datasets']['development']=cfg['datasets']['development'][:1]
    cfg['document']['execution'].update(repetitions=1,concurrency=1)
    runner=Runner(cfg,tmp_path,backend='agentcore',runtime_arn='arn:test',region='us-east-1')
    cloud=Mock();runner._cloud_pool=cloud
    cloud.invoke.side_effect=TimeoutError();cloud.recover.return_value=None
    with pytest.raises(InterruptedAttempt):runner.evaluate(cfg['target'],cfg['prompt'],'development')
    assert runner.store.db.execute('SELECT state FROM attempts').fetchall()==[('started',)]
    reserved=runner.store.reserved()
    cloud.recover.side_effect=execute
    rows=runner.evaluate(cfg['target'],cfg['prompt'],'development')
    assert rows[0]['result']['status']=='completed' and cloud.invoke.call_count==1
    assert runner.store.reserved()==reserved


def test_restore_is_atomic_and_rejects_corrupt_checkpoint(tmp_path):
    from scripts.resume_agentcore import restore
    from customer_eval.contract import digest,implementation_fingerprint
    config={'implementation_fingerprint':implementation_fingerprint()};config['fingerprint']=digest(config)
    receipt={'bucket':'b','status_key':'job/status.json','job_id':'job',
             'request':{'input_key':'input','fingerprint':config['fingerprint']}}
    s3=FakeS3();s3.data={'job/status.json':json.dumps({'status':'paused','fingerprint':config['fingerprint']}).encode(),
        'input':json.dumps(config).encode(),'job/checkpoint.sqlite':b'incomplete download'}
    import sqlite3
    with pytest.raises(sqlite3.DatabaseError):restore(s3,receipt,tmp_path/'restored')
    assert not (tmp_path/'restored'/'run.sqlite').exists()
    source=Store(tmp_path/'source','id',10);source.perform('saved',1,lambda:{'status':'completed'})
    s3.data['job/checkpoint.sqlite']=(tmp_path/'source'/'run.sqlite').read_bytes()
    assert restore(s3,receipt,tmp_path/'restored')==config
    restored=Store(tmp_path/'restored','id',10)
    assert restored.execution_id==source.execution_id and restored.reserved()==1
    s3.data['job/checkpoint.sqlite']=b'now corrupt'
    restore(s3,receipt,tmp_path/'restored')  # Preserve the existing local run.
