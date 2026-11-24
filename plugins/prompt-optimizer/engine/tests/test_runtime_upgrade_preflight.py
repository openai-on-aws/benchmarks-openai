"""Offline checks for preserving runtime settings and read-only qualification."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from customer_eval import preflight

_spec = importlib.util.spec_from_file_location('deploy_agentcore_tested', Path(__file__).parents[1] / 'scripts/deploy_agentcore.py')
deploy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deploy)

ARN = 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example-abc'
FP = 'f' * 64
SHA = 'a' * 64


def runtime():
    return {'agentRuntimeArn': ARN, 'agentRuntimeId': 'example-abc', 'status': 'READY',
        'agentRuntimeVersion': '3', 'roleArn': 'arn:aws:iam::123456789012:role/existing',
        'networkConfiguration': {'networkMode': 'PUBLIC'},
        'lifecycleConfiguration': {'idleRuntimeSessionTimeout': 300, 'maxLifetime': 28800},
        'environmentVariables': {'EVAL_ALLOWED_ENDPOINT_HOSTS': 'bedrock-runtime.us-east-1.amazonaws.com,gateway.example',
            'EVAL_ARTIFACT_BUCKET': 'existing-bucket', 'EVAL_ARTIFACT_PREFIX': 'customer-evaluation',
            'EVAL_WORKER_RUNTIME_ARN': ARN, 'EVAL_MAX_CONCURRENCY': '2',
            'EVAL_MAX_TRIAL_SECONDS': '300', 'EVAL_MAX_REQUESTS_PER_TRIAL': '12',
            'CUSTOM_TOKEN': 'test-secret-never-written'},
        'requestHeaderConfiguration': {'requestHeaderAllowlist': ['X-Custom']},
        'agentRuntimeArtifact': {'codeConfiguration': {'code': {'s3': {
            'bucket': 'existing-bucket', 'prefix': 'deployment/old.zip', 'versionId': 'old-version'}},
            'runtime': 'PYTHON_3_12', 'entryPoint': ['main.py']}}}


def test_update_preserves_live_configuration_without_mutating_readback():
    before = runtime()
    snapshot = deepcopy(before)
    request = deploy.update_request(before, 'example-abc', {'bucket': 'existing-bucket', 'prefix': 'deployment/new.zip'}, 'token')
    for key in deploy.PRESERVED_RUNTIME_FIELDS:
        if key in before:
            assert request[key] == before[key]
    assert request['environmentVariables']['CUSTOM_TOKEN'] == 'test-secret-never-written'
    assert request['agentRuntimeArtifact'] != before['agentRuntimeArtifact']
    request['environmentVariables']['CUSTOM_TOKEN'] = 'changed'
    assert before == snapshot
    assert 'test-secret-never-written' not in json.dumps(deploy._public_runtime(before))


def test_update_requires_ready_runtime():
    before = runtime(); before['status'] = 'UPDATING'
    with pytest.raises(ValueError, match='READY'):
        deploy.update_request(before, 'example-abc', {}, 'token')


class Cloud:
    def __init__(self):
        self.current = runtime()
        self.updates = []
        self.puts = []
        self.reads = []
        self.uncertain = False
    def client(self, name, **kw):
        self.reads.append(name)
        return self
    def get_caller_identity(self):
        return {'Account': '123456789012'}
    def get_agent_runtime(self, **kw):
        return deepcopy(self.current)
    def get_agent_runtime_endpoint(self, **kw):
        return {'status': 'READY', 'liveVersion': self.current['agentRuntimeVersion']}
    def put_object(self, **kw):
        self.puts.append({k: v for k, v in kw.items() if k != 'Body'})
        return {'VersionId': 'new-version'}
    def update_agent_runtime(self, **kw):
        self.updates.append(deepcopy(kw))
        if self.uncertain:
            raise TimeoutError('uncertain response')
        self.current.update(agentRuntimeVersion='4', agentRuntimeArtifact=kw['agentRuntimeArtifact'])
        return {'agentRuntimeVersion': '4', 'ResponseMetadata': {'RequestId': 'request-id'}}
    def head_bucket(self, **kw):
        return {}
    def head_object(self, **kw):
        return {'Metadata': {'sha256': SHA, 'implementation-fingerprint': FP}}
    def get_role(self, **kw):
        return {'Role': {'Arn': self.current['roleArn']}}


def args(tmp_path):
    artifact = tmp_path / 'runtime.zip'; artifact.write_bytes(b'test archive')
    return SimpleNamespace(receipt_dir=str(tmp_path / 'upgrade'), runtime_id='example-abc',
        artifact=str(artifact), profile='test', region='us-east-1', name='example', wait_seconds=0)


def identity():
    return {'sha256': SHA, 'implementation_fingerprint': FP, 'verified_source_files': 40}


def test_upgrade_keeps_identity_is_idempotent_and_redacts_receipt(tmp_path):
    cloud = Cloud(); parameters = args(tmp_path)
    with patch.object(deploy, 'archive_identity', return_value=identity()):
        result = deploy.update_existing(parameters, cloud)
        again = deploy.update_existing(parameters, cloud)
    assert result == again
    assert result['status'] == 'ready' and result['runtime_version'] == '4'
    assert len(cloud.updates) == len(cloud.puts) == 1
    assert cloud.updates[0]['roleArn'] == runtime()['roleArn']
    assert cloud.updates[0]['environmentVariables'] == runtime()['environmentVariables']
    receipt = Path(result['receipt']).read_text()
    assert 'test-secret-never-written' not in receipt
    assert 'CUSTOM_TOKEN' in receipt
    assert json.loads(receipt)['previous_runtime_version'] == '3'


def test_uncertain_upgrade_does_not_resubmit_on_reentry(tmp_path):
    cloud = Cloud(); cloud.uncertain = True; parameters = args(tmp_path)
    with patch.object(deploy, 'archive_identity', return_value=identity()):
        with pytest.raises(TimeoutError):
            deploy.update_existing(parameters, cloud)
        result = deploy.update_existing(parameters, cloud)
    assert result['status'] == 'pending'
    assert len(cloud.updates) == len(cloud.puts) == 1


def project(tmp_path):
    p = tmp_path / 'project'; (p / 'prepared').mkdir(parents=True)
    state = {'prepared': {'config': 'prepared/input.yaml', 'fingerprint': 'study'}}
    (p / 'project.json').write_text(json.dumps(state))
    connection = {'cloud': {'profile': 'test', 'region': 'us-east-1', 'account': '123456789012',
        'runtime_arn': ARN, 'bucket': 'existing-bucket'}}
    (p / 'prepared/connection.json').write_text(json.dumps(connection))
    route = {'auth': 'sigv4', 'credential_ref': 'aws_profile:test', 'region': 'us-east-1',
        'model_id': 'test-model', 'base_url': 'https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1',
        'input_usd_per_million': 1, 'output_usd_per_million': 2,
        'cached_input_usd_per_million': .1, 'cache_write_usd_per_million': 1.25,
        'pricing_verified_at': '2026-11-22', 'pricing_source': 'Provider list price', 'pricing_max_input_tokens': 100000}
    cfg = {'fingerprint': 'study', 'routes': {'m1': route},
        'document': {'execution': {'concurrency': 2, 'timeout_seconds': 60}, 'runner': {'limits': {'max_requests': 1}}}}
    return p, cfg


def inspect_case(tmp_path, backend, cloud=None, mutate=None):
    path, cfg = project(tmp_path)
    if mutate: mutate(cfg)
    with patch.object(preflight, 'load_with_endpoints', return_value=cfg), patch.object(preflight, 'implementation_fingerprint', return_value=FP):
        result = preflight.inspect(path, backend=backend, session=cloud or Cloud())
    return result


def test_cloud_preflight_is_read_only_and_does_not_claim_inference(tmp_path):
    cloud = Cloud()
    result = inspect_case(tmp_path, 'agentcore', cloud)
    assert result['ready_for_qualification'] and result['model_calls'] == 0
    assert not cloud.updates and not cloud.puts
    access = next(x for x in result['checks'] if x['check'] == 'inference_access')
    assert access['status'] == 'unknown' and not access['blocking']


def test_cloud_preflight_fails_code_mismatch_and_excess_concurrency(tmp_path):
    cloud = Cloud()
    cloud.head_object = lambda **kw: {'Metadata': {'implementation-fingerprint': 'different'}}
    result = inspect_case(tmp_path, 'agentcore', cloud,
                          lambda cfg: cfg['document']['execution'].update(concurrency=3))
    assert not result['ready_for_qualification']
    assert {'implementation', 'limit:concurrency'} <= set(result['blockers'])


def test_cloud_preflight_unknown_code_does_not_pass(tmp_path):
    cloud = Cloud(); cloud.head_object = lambda **kw: {'Metadata': {}}
    result = inspect_case(tmp_path, 'agentcore', cloud)
    assert not result['ready_for_qualification'] and 'implementation' in result['blockers']


def test_local_preflight_checks_credentials_and_prices_without_cloud_runtime(tmp_path):
    cloud = Cloud()
    result = inspect_case(tmp_path, 'local', cloud)
    assert result['ready_for_qualification']
    assert cloud.reads == ['sts']
    assert not cloud.updates and not cloud.puts


def test_preflight_missing_prices_blocks(tmp_path):
    result = inspect_case(tmp_path, 'local', mutate=lambda cfg: cfg['routes']['m1'].update(cached_input_usd_per_million=None))
    assert not result['ready_for_qualification'] and 'pricing:m1' in result['blockers']


def test_archive_with_wrong_source_is_rejected(tmp_path):
    import zipfile
    archive = tmp_path / 'wrong.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('hillclimb.py', '# stale source')
    with pytest.raises(ValueError, match='differs from the current engine'):
        deploy.archive_identity(archive)


def test_missing_role_read_permission_is_explicitly_unknown(tmp_path):
    cloud = Cloud()
    def denied(**kw):
        raise PermissionError('GetRole denied')
    cloud.get_role = denied
    result = inspect_case(tmp_path, 'agentcore', cloud)
    assert result['ready_for_qualification']
    role = next(c for c in result['checks'] if c['check'] == 'execution_role')
    assert role['status'] == 'unknown' and role['blocking'] is False
    assert not cloud.updates and not cloud.puts
