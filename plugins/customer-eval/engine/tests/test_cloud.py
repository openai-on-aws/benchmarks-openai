import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from customer_eval.cloud import invoke_trial
from customer_eval.contract import ROOT, load
from runtime.app import validate_route


def test_runtime_correlates_exact_session(monkeypatch):
    import boto3
    def invoke(**kwargs):
        return {'runtimeSessionId':kwargs['runtimeSessionId'],'response':io.BytesIO(b'{"trial_id":"t","status":"completed"}')}
    client=Mock();client.invoke_agent_runtime.side_effect=invoke
    monkeypatch.setattr(boto3,'Session',lambda **kw:SimpleNamespace(client=lambda *a,**kw:client))
    result=invoke_trial({'trial_id':'t'},'arn:example',None,'us-east-1',30)
    assert len(result['runtime_session_id'])==36
    client.invoke_agent_runtime.return_value={'runtimeSessionId':'wrong','response':io.BytesIO(b'{}')}
    client.invoke_agent_runtime.side_effect=None
    with pytest.raises(ValueError,match='session'): invoke_trial({'trial_id':'t'},'arn:example',None,'us-east-1',30)


def test_endpoint_allowlist(monkeypatch):
    monkeypatch.setenv('EVAL_ALLOWED_ENDPOINT_HOSTS','bedrock-runtime.us-east-1.amazonaws.com')
    validate_route({'base_url':'https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1'})
    for url in ['http://bedrock-runtime.us-east-1.amazonaws.com','https://attacker.example',
                'https://user:password@bedrock-runtime.us-east-1.amazonaws.com']:
        with pytest.raises(ValueError):validate_route({'base_url':url})


def test_cloud_rejects_changed_bundle(monkeypatch):
    import boto3
    from runtime.coordinator import start_job
    cfg=load(ROOT/'examples/customer-support/input.yaml')
    cfg['prompt']='tampered'
    s3=Mock();s3.get_object.return_value={'Body':io.BytesIO(json.dumps(cfg).encode())}
    monkeypatch.setattr(boto3,'client',lambda *a,**kw:s3)
    monkeypatch.setenv('EVAL_ARTIFACT_BUCKET','example')
    with pytest.raises(ValueError,match='fingerprint'):
        start_job({'job_id':'example-job','input_key':'customer-evaluation/inputs/test.json','fingerprint':cfg['fingerprint']},Mock())
    s3.put_object.assert_not_called()


def test_cloud_disallows_unapproved_uncapped_budget(monkeypatch):
    import boto3
    from runtime.coordinator import start_job
    from customer_eval.contract import digest
    cfg=load(ROOT/'examples/customer-support/input.yaml')
    cfg['document']['runner']['budget_policy']='unlimited'
    del cfg['fingerprint'];cfg['fingerprint']=digest(cfg)
    s3=Mock();s3.get_object.return_value={'Body':io.BytesIO(json.dumps(cfg).encode())}
    monkeypatch.setattr(boto3,'client',lambda *a,**kw:s3)
    monkeypatch.setenv('EVAL_ARTIFACT_BUCKET','example')
    monkeypatch.delenv('EVAL_ALLOW_UNLIMITED_BUDGET',raising=False)
    with pytest.raises(ValueError,match='uncapped'):
        start_job({'job_id':'example-job','input_key':'customer-evaluation/inputs/test.json','fingerprint':cfg['fingerprint']},Mock())
    s3.put_object.assert_not_called()


@pytest.mark.parametrize('outcome', ['accepted', 'timeout', 'wrong_job'])
def test_submission_keeps_receipt_before_dispatch_without_retry(monkeypatch, tmp_path, outcome):
    import boto3
    import sys
    from scripts import submit_agentcore
    monkeypatch.setattr(submit_agentcore, 'load', lambda *a, **kw: {'fingerprint': 'frozen'})
    s3, runtime = Mock(), Mock()
    monkeypatch.setattr(boto3, 'Session', lambda **kw: SimpleNamespace(
        client=lambda service, **options: s3 if service == 's3' else runtime))
    monkeypatch.setattr(sys, 'argv', ['submit_agentcore', '--config', 'example.yaml',
        '--runtime-arn', 'arn:example', '--region', 'us-east-1', '--profile', 'test',
        '--bucket', 'test-bucket', '--receipt-dir', str(tmp_path)])

    def invoke(**kwargs):
        receipt = json.loads(next(tmp_path.glob('*.json')).read_text())
        assert receipt['status'] == 'dispatching'
        assert receipt['runtime_session_id'] == kwargs['runtimeSessionId']
        if outcome == 'timeout':
            raise TimeoutError('The response was lost after dispatch')
        request = json.loads(kwargs['payload'])
        return {'runtimeSessionId': kwargs['runtimeSessionId'], 'response': io.BytesIO(json.dumps({
            'job_id': request['job_id'] if outcome == 'accepted' else 'another-job',
            'status': 'accepted'}).encode())}

    runtime.invoke_agent_runtime.side_effect = invoke
    if outcome == 'accepted':
        submit_agentcore.main()
    else:
        with pytest.raises(TimeoutError if outcome == 'timeout' else ValueError):
            submit_agentcore.main()
    receipt = json.loads(next(tmp_path.glob('*.json')).read_text())
    assert receipt['status'] == ('accepted' if outcome == 'accepted' else 'uncertain')
    assert receipt['status_key'].endswith(receipt['job_id'] + '/status.json')
    runtime.invoke_agent_runtime.assert_called_once()
