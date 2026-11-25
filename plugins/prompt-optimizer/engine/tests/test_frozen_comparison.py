"""Comparison-only cloud continuation never mutates selection or retries calls."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from customer_eval import frozen_comparison as fc, runner as runner_module
from customer_eval.contract import digest, cases
from customer_eval.runner import Runner, InterruptedAttempt
from runtime import coordinator
from test_reference_source_grading import ANSWER, mixed_project


def seal(cfg):
    cfg['fingerprint'] = digest({k:v for k,v in cfg.items() if k!='fingerprint'})
    return cfg


@pytest.fixture
def fixture(tmp_path):
    _, parent = mixed_project(tmp_path)
    parent['document']['execution'].update(repetitions=2, max_cases_per_dataset=2, concurrency=1)
    parent['routes']['m1']['model_id'] = 'fixture-old-model'
    seal(parent)
    revised = 'Extract the invoice total. Cite the source in a concise explanation.'
    manifest = {'recorded_at_utc':'2026-11-24T12:00:00+00:00', 'purpose':'Comparison only',
        'candidate':'generation-1-candidate-1', 'prompt':revised,
        'raw_prompt_sha256':hashlib.sha256(revised.encode()).hexdigest(),
        'main_fingerprint':parent['fingerprint'], 'selection_basis':'Initial validation only',
        'evidence':['validation.json'], 'holdout_outputs_seen':False,
        'override_saved_main_selection':False, 'automatic_adoption':False,
        'condition':'Only if the parent omits a revised arm'}
    original = parent['prompt']
    arms = {key:{'score':{'prompt':original}, 'trials':[{'result':{'route_calls':[
        {'started_at':'2026-11-24T13:00:00+00:00'}]}}]}
        for key in ('source_original','target_original','target_selected')}
    report = {'fingerprint':parent['fingerprint'], 'selected_prompt':{'prompt':original,'sha256':digest(original)},
        'holdout':arms, 'optimization':{'generations':[{'index':1,'candidates':[{'prompt':revised}]}]},
        'evaluation_status':'needs_review', 'unresolved_attempts':[{'id':'parent-unknown','state':'started'}],
        'model_costs':{'known_model_cost_usd':5.0,'estimated_model_cost_usd':None}}
    raw = json.dumps(manifest).encode(); sha = hashlib.sha256(raw).hexdigest()
    config = fc.prepare(parent,report,manifest,'parent-job-123',sha)
    return config,report,raw


class Storage:
    def __init__(self,fixture):
        cfg,report,raw = fixture; spec=cfg['comparison_only']
        self.objects={
            'customer-evaluation/inputs/comparison.json':json.dumps(cfg).encode(),
            'customer-evaluation/jobs/parent-job-123/status.json':json.dumps({'status':'completed','fingerprint':report['fingerprint']}).encode(),
            'customer-evaluation/jobs/parent-job-123/report.json':json.dumps(report).encode(),
            f"customer-evaluation/comparison-precommits/{spec['precommit_object_sha256']}.json":raw}
        self.uploaded=datetime(2026,11,24,12,tzinfo=timezone.utc)
        self.reads=[];self.writes=[]
    def get_object(self,**kw):
        self.reads.append(kw)
        return {'Body':io.BytesIO(self.objects[kw['Key']]),'LastModified':self.uploaded}
    def put_object(self,**kw):
        if kw.get('IfNoneMatch')=='*' and kw['Key'] in self.objects:
            raise ValueError('Object already claimed')
        self.writes.append(kw['Key']);self.objects[kw['Key']]=kw['Body']
    def upload_file(self,path,bucket,key):
        self.objects[key]=Path(path).read_bytes()


def fake_models(monkeypatch, cfg, *, failed_judge=False):
    seen=[];judges=[]
    def execute(self,payload):
        seen.append((payload['route']['model_id'],payload['prompt'],payload['trial_id']))
        return {'trial_id':payload['trial_id'],'status':'completed','outputs':[json.dumps(ANSWER)],
            'tool_calls':[], 'model_requested':payload['route']['model_id'],
            'timing':{'worker_ms':110,'conversation_ms':100},
            'route_calls':[{'usage':{'input_tokens':100,'output_tokens':20},
                            'started_at':'2026-11-24T14:00:00+00:00'}]}
    def complete(route,text,limits,receipt):
        assert text.startswith('Evaluate the candidate extraction'), 'optimizer must never execute'
        judges.append(text)
        if failed_judge:
            receipt.update(status='provider_error',http_status=500,model_requested=route['model_id'],usage=None)
            raise HTTPError('https://example.invalid',500,'failed',{},None)
        evidence=json.loads(text.split('\n',1)[1]); source=evidence['sources'][0]
        answer={'checks':{key:{'status':'pass','reason':'Supported by source',
            'evidence':[{'source_id':source['source_id'],'quote':source['text']}]}
            for key in evidence['criteria']}}
        usage={'input_tokens':10,'output_tokens':10}
        receipt.update(status='completed',model_requested=route['model_id'])
        return json.dumps(answer),usage
    monkeypatch.setattr(Runner,'execute',execute)
    monkeypatch.setattr(runner_module,'complete',complete)
    monkeypatch.setattr(runner_module,'run_hill_climb',lambda **kw:pytest.fail('no search'))
    return seen,judges


def test_three_arms_interleaved_and_resume_uses_saved_receipts(fixture,tmp_path,monkeypatch):
    cfg,parent,_=fixture; before=deepcopy(parent)
    seen,judges=fake_models(monkeypatch,cfg)
    first=Runner(cfg,tmp_path/'comparison',backend='agentcore',runtime_arn='arn:fixture',region='us-east-1')
    result=fc.run(first,parent)
    assert len(seen)==12 and result['main_selection_unchanged'] and result['automatic_adoption'] is False
    original=cfg['prompt'];new=cfg['comparison_only']['candidate_manifest']['prompt']
    roles=[(cfg['routes']['m1']['model_id'],original),(cfg['routes']['m2']['model_id'],original),(cfg['routes']['m2']['model_id'],new)]
    expected=[]
    for case_index in range(2):
        for repeat in range(2):
            offset=(case_index+repeat)%3;expected.extend(roles[offset:]+roles[:offset])
    assert [(model,prompt) for model,prompt,_ in seen]==expected
    assert all(arm['score']['accuracy']==1 for arm in result['holdout'].values())
    assert all(row['phase']==fc.PHASE for arm in result['holdout'].values() for row in arm['trials'])
    assert result['parent_unresolved_attempts']==parent['unresolved_attempts']
    assert result['combined_model_costs']['estimated_model_cost_usd'] is None
    assert result['combined_model_costs']['known_model_cost_usd']>5
    assert parent==before and not (tmp_path/'comparison/selected-prompt.json').exists()
    counts=(len(seen),len(judges));execution_id=first.store.execution_id;first.store.db.close()
    resumed=Runner(cfg,tmp_path/'comparison',backend='agentcore',runtime_arn='arn:fixture',region='us-east-1')
    assert fc.run(resumed,parent)==result
    assert resumed.store.execution_id==execution_id and (len(seen),len(judges))==counts


def test_optional_provider_errors_keep_core_results_and_unknown_spend(fixture,tmp_path,monkeypatch):
    cfg,parent,_=fixture;seen,judges=fake_models(monkeypatch,cfg,failed_judge=True)
    runner=Runner(cfg,tmp_path/'failed-judges',backend='agentcore',runtime_arn='arn:fixture',region='us-east-1')
    report=fc.run(runner,parent)
    assert report['evaluation_status']=='needs_review' and report['grading_error_records']==12
    assert all(arm['score']['accuracy']==1 for arm in report['holdout'].values())
    assert report['model_costs']['estimated_model_cost_usd'] is None
    assert report['model_costs']['known_model_cost_usd']>0
    assert report['judge_requests'] and all(r['usage'] is None and r['status']=='provider_error' for r in report['judge_requests'])
    assert len(judges)==len(report['judge_requests'])==2  # Existing exact-request cache, no paid retries.
    assert not report['unresolved_attempts']


def test_pending_application_is_not_reissued(fixture,tmp_path,monkeypatch):
    cfg,parent,_=fixture;seen,_=fake_models(monkeypatch,cfg)
    runner=Runner(cfg,tmp_path/'pending',backend='agentcore',runtime_arn='arn:fixture',region='us-east-1')
    first_case=cases(cfg,'holdout')[0]
    ident=digest([cfg['fingerprint'],cfg['source'],cfg['prompt'],first_case['id'],0,'holdout',fc.PHASE])
    runner.store.db.execute('INSERT INTO attempts VALUES (?,?,?,?)',(ident,'started',1,None));runner.store.db.commit()
    pool=Mock();pool.recover.return_value=None
    monkeypatch.setattr(runner,'cloud_pool',lambda:pool)
    with pytest.raises(InterruptedAttempt):fc.run(runner,parent)
    assert ident not in [entry[2] for entry in seen]
    assert runner.store.db.execute('SELECT state,value FROM attempts WHERE id=?',(ident,)).fetchone()==('started',None)


@pytest.mark.parametrize('change',[
    lambda c:c['comparison_only'].update(version=True),
    lambda c:c['comparison_only'].update(parent_job_id='../different'),
    lambda c:c['comparison_only'].update(phase='standard'),
    lambda c:c['comparison_only'].update(extra='ignored'),
    lambda c:c['comparison_only']['candidate_manifest'].update(automatic_adoption=True),
    lambda c:c['comparison_only']['candidate_manifest'].update(prompt='different'),
    lambda c:c['document']['execution'].update(repetitions=10),
    lambda c:c['document']['runner']['limits'].update(max_output_tokens=1),
    lambda c:c['routes']['m2'].update(model_id='changed'),
    lambda c:c['datasets']['holdout'][0]['expected'].update(answer={'balance':'changed'})])
def test_input_or_manifest_changes_fail_before_execution(fixture,change):
    cfg,_,_=fixture;bad=deepcopy(cfg);change(bad);seal(bad)
    with pytest.raises(ValueError):fc.validate(bad)


@pytest.mark.parametrize('change',[
    lambda p:p['selected_prompt'].update(prompt='changed'),
    lambda p:p['holdout'].update(target_challenger={'score':{'prompt':'other prompt'}}),
    lambda p:p['optimization']['generations'][0]['candidates'][0].update(prompt='other candidate')])
def test_parent_no_longer_matches_fallback_condition(fixture,change):
    cfg,parent,_=fixture;change(parent)
    cfg['comparison_only']['parent_report_sha256']=digest(parent);seal(cfg)
    with pytest.raises(ValueError):fc.validate_parent(cfg,parent)


def test_server_precommit_must_predate_every_holdout_request(fixture):
    cfg,parent,_=fixture;storage=Storage(fixture)
    assert fc.read_parent(cfg,storage,'fixture-bucket','customer-evaluation',coordinator.read_study_input)==parent
    assert all(r['Bucket']=='fixture-bucket' for r in storage.reads)
    assert all(r['Key'].startswith('customer-evaluation/') for r in storage.reads)
    storage.uploaded=datetime(2026,11,24,13,tzinfo=timezone.utc)
    with pytest.raises(ValueError,match='before any parent holdout'):
        fc.read_parent(cfg,storage,'fixture-bucket','customer-evaluation',coordinator.read_study_input)


def test_precommit_content_and_parent_completion_required(fixture):
    cfg,_,_=fixture;storage=Storage(fixture)
    key='customer-evaluation/jobs/parent-job-123/status.json'
    status=json.loads(storage.objects[key]);status['status']='running';storage.objects[key]=json.dumps(status).encode()
    with pytest.raises(ValueError,match='completed parent'):
        fc.read_parent(cfg,storage,'fixture-bucket','customer-evaluation',coordinator.read_study_input)
    status['status']='completed';storage.objects[key]=json.dumps(status).encode()
    key=f"customer-evaluation/comparison-precommits/{cfg['comparison_only']['precommit_object_sha256']}.json"
    storage.objects[key]=b'{}'
    with pytest.raises(ValueError,match='immutable object hash'):
        fc.read_parent(cfg,storage,'fixture-bucket','customer-evaluation',coordinator.read_study_input)


def cloud_env(monkeypatch,storage):
    monkeypatch.setenv('EVAL_ARTIFACT_BUCKET','fixture-bucket')
    monkeypatch.setenv('EVAL_MAX_MODEL_BUDGET_USD','100')
    monkeypatch.setenv('EVAL_ALLOW_UNLIMITED_BUDGET','1')
    monkeypatch.setenv('EVAL_WORKER_RUNTIME_ARN','arn:fixture')
    monkeypatch.setenv('AWS_REGION','us-east-1')
    monkeypatch.setenv('EVAL_ALLOWED_ENDPOINT_HOSTS','bedrock-runtime.us-east-1.amazonaws.com')
    monkeypatch.setattr(coordinator.boto3,'client',lambda *a,**kw:storage)


def test_cloud_coordinator_uses_three_arm_mode_and_duplicate_submissions_cannot_run(fixture,monkeypatch):
    cfg,parent,_=fixture;storage=Storage(fixture);cloud_env(monkeypatch,storage)
    seen,_=fake_models(monkeypatch,cfg)
    # Only the coordinator's background dispatch is deferred. Evaluation worker
    # threads still use ThreadPoolExecutor's real threading implementation.
    queued=[]
    class DeferredThread:
        def __init__(self,target,**kw):queued.append(target)
        def start(self):pass
    from types import SimpleNamespace
    monkeypatch.setattr(coordinator,'threading',SimpleNamespace(Thread=DeferredThread))
    app=Mock();payload={'job_id':'comparison-job-1','input_key':'customer-evaluation/inputs/comparison.json','fingerprint':cfg['fingerprint']}
    ack=coordinator.start_job(payload,app);assert ack['status']=='accepted' and len(queued)==1
    queued.pop()()
    status=json.loads(storage.objects['customer-evaluation/jobs/comparison-job-1/status.json'])
    assert status['status']=='completed' and status['report_key'].endswith('/comparison-report.json')
    assert len(seen)==12
    report=json.loads(storage.objects[status['report_key']]);assert set(report['holdout'])=={'source_original','target_original','target_comparison'}
    assert not any(key.endswith('/selected-prompt.json') for key in storage.objects)
    with pytest.raises(ValueError,match='already claimed'):
        coordinator.start_job(dict(payload,job_id='comparison-job-2'),app)
    rebuilt=deepcopy(cfg);rebuilt['implementation_fingerprint']='a'*64;seal(rebuilt)
    assert rebuilt['fingerprint']!=cfg['fingerprint']
    assert fc.claim_fingerprint(rebuilt)==fc.claim_fingerprint(cfg)
    monkeypatch.setattr(fc,'implementation_fingerprint',lambda:'a'*64)
    storage.objects['customer-evaluation/inputs/rebuilt.json']=json.dumps(rebuilt).encode()
    with pytest.raises(ValueError,match='already claimed'):
        coordinator.start_job(dict(payload,job_id='comparison-job-rebuilt',
            input_key='customer-evaluation/inputs/rebuilt.json',fingerprint=rebuilt['fingerprint']),app)
    assert len(seen)==12 and not queued
    assert json.loads(storage.objects['customer-evaluation/jobs/parent-job-123/report.json'])==parent


def test_normal_submission_keeps_the_existing_config_loader(tmp_path,monkeypatch):
    from scripts import submit_agentcore
    load=Mock(return_value={'ordinary':'config'});monkeypatch.setattr(submit_agentcore,'load',load)
    assert submit_agentcore.read_config(tmp_path/'input.yaml')=={'ordinary':'config'}
    load.assert_called_once_with(tmp_path/'input.yaml',live=True)


def test_comparison_submission_validates_frozen_config_without_loading_intake(fixture,tmp_path,monkeypatch):
    from scripts import submit_agentcore
    cfg,_,_=fixture;p=tmp_path/'comparison.json';p.write_text(json.dumps(cfg))
    monkeypatch.setattr(submit_agentcore,'load',lambda *a,**kw:pytest.fail('not an intake document'))
    assert submit_agentcore.read_config(p,True)==cfg


@pytest.mark.parametrize('field,value',[('repetitions',True),('repetitions',1.5),
                                      ('repetitions',100),('max_cases_per_dataset',False)])
def test_bound_rejects_invalid_parent_counts_even_if_hashes_match(fixture,field,value):
    cfg,report,_=fixture;spec=cfg['comparison_only']
    parent={k:deepcopy(v) for k,v in cfg.items() if k not in ('fingerprint','comparison_only')}
    parent['implementation_fingerprint']=spec['parent_implementation_fingerprint']
    parent['document']['execution'][field]=value;seal(parent)
    manifest=deepcopy(spec['candidate_manifest']);manifest['main_fingerprint']=parent['fingerprint']
    report['fingerprint']=parent['fingerprint']
    with pytest.raises(ValueError):fc.prepare(parent,report,manifest,'parent-job-123','a'*64)


@pytest.mark.parametrize('change',[
    lambda p:p['holdout']['source_original']['trials'][0]['result']['route_calls'][0].update(started_at='2026-11-24T13:00:00'),
    lambda p:p['holdout']['source_original']['trials'][0]['result'].update(route_calls=[]),
    lambda p:p['holdout']['source_original']['trials'][0]['result']['route_calls'][0].update(started_at='invalid')])
def test_missing_or_naive_parent_clock_evidence_blocks_claim(fixture,change):
    cfg,parent,raw=fixture;change(parent)
    cfg['comparison_only']['parent_report_sha256']=digest(parent);seal(cfg)
    storage=Storage((cfg,parent,raw))
    with pytest.raises(ValueError,match='evidence'):
        fc.read_parent(cfg,storage,'fixture-bucket','customer-evaluation',coordinator.read_study_input)
    assert not storage.writes


def test_invalid_config_cannot_create_cloud_claim_or_background_task(fixture,monkeypatch):
    cfg,parent,raw=fixture;cfg['comparison_only']['phase']='standard';seal(cfg)
    storage=Storage((cfg,parent,raw));cloud_env(monkeypatch,storage);app=Mock()
    with pytest.raises(ValueError,match='version or phase'):
        coordinator.start_job({'job_id':'comparison-job-1','input_key':'customer-evaluation/inputs/comparison.json',
                               'fingerprint':cfg['fingerprint']},app)
    assert not storage.writes;app.add_async_task.assert_not_called()


def test_parent_job_id_cannot_be_reused(fixture,monkeypatch):
    cfg,_,_=fixture;storage=Storage(fixture);cloud_env(monkeypatch,storage);app=Mock()
    with pytest.raises(ValueError,match='new job ID'):
        coordinator.start_job({'job_id':'parent-job-123','input_key':'customer-evaluation/inputs/comparison.json',
                               'fingerprint':cfg['fingerprint']},app)
    assert not storage.writes;app.add_async_task.assert_not_called()


def test_normal_cloud_job_still_calls_runner_run_and_uploads_normal_artifacts(fixture,monkeypatch):
    from test_coordinator_completion_limits import config
    from types import SimpleNamespace
    cfg=seal(config());storage=Storage(fixture)
    storage.objects['customer-evaluation/inputs/normal.json']=json.dumps(cfg).encode()
    cloud_env(monkeypatch,storage)
    monkeypatch.setattr(fc,'read_parent',lambda *a,**kw:pytest.fail('normal jobs need no comparison parent'))
    invoked=[]
    class NormalRunner:
        def __init__(self,config,directory,**kw):
            self.store=SimpleNamespace(db=Mock(),checkpoint_event='progress')
            self.directory=Path(directory)
        def run(self):
            invoked.append('normal run')
            for name in ('report.json','report.md','selected-prompt.json','decision.json','decision.md',
                         'decision.html','original-prompt.txt','selected-prompt.txt','prompt.diff'):
                (self.directory/name).write_text('{}')
            return {'reserved_model_spend_usd':1}
    queued=[]
    class DeferredThread:
        def __init__(self,target,**kw):queued.append(target)
        def start(self):pass
    monkeypatch.setattr(coordinator,'Runner',NormalRunner)
    monkeypatch.setattr(coordinator,'threading',SimpleNamespace(Thread=DeferredThread))
    coordinator.start_job({'job_id':'normal-job-1','input_key':'customer-evaluation/inputs/normal.json',
                           'fingerprint':cfg['fingerprint']},Mock())
    queued.pop()()
    status=json.loads(storage.objects['customer-evaluation/jobs/normal-job-1/status.json'])
    assert invoked==['normal run'] and status['status']=='completed'
    assert status['report_key'].endswith('/report.json')
    assert 'customer-evaluation/jobs/normal-job-1/selected-prompt.json' in storage.objects
    assert not any('comparison-claims' in key for key in storage.writes)
