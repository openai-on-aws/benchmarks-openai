import importlib.util
import json
from unittest.mock import Mock

import boto3
import pytest
from botocore.exceptions import ClientError
from customer_eval.contract import ROOT


@pytest.fixture
def gateway(monkeypatch,tmp_path):
    monkeypatch.setattr(boto3,'client',lambda *a,**kw:Mock())
    spec=importlib.util.spec_from_file_location('test_gateway_module',ROOT/'gateway/setup_gateway.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.TEARDOWN_PATH=tmp_path/'manifest.json'
    module.RESOLVED_PATH=tmp_path/'resolved.json'
    return module


def error(code):return ClientError({'Error':{'Code':code,'Message':'test'}},'Test')


def test_endpoint_removes_mcp_suffix(gateway):
    assert gateway.inference_base_url('https://gateway.example/mcp/')=='https://gateway.example/inference/v1'


def test_target_must_be_ready(gateway):
    gateway.acc.get_gateway_target.return_value={'status':'FAILED'}
    with pytest.raises(RuntimeError):gateway._wait_target_ready('g','t')


def test_cleanup_keeps_failed_manifest_and_dependencies(gateway):
    gateway._record(gateway_id='g',bedrock_target_id='t',role_name='r')
    gateway.acc.delete_gateway_target.side_effect=error('AccessDeniedException')
    gateway.teardown()
    assert gateway.TEARDOWN_PATH.exists()
    gateway.acc.delete_gateway.assert_not_called()
    gateway.iam.delete_role.assert_not_called()


def test_cleanup_retries_missing_resources(gateway):
    gateway._record(gateway_id='g',bedrock_target_id='t',role_name='r')
    gateway.acc.delete_gateway_target.side_effect=error('ResourceNotFoundException')
    gateway.acc.delete_gateway.side_effect=error('ResourceNotFoundException')
    gateway.iam.delete_role_policy.side_effect=error('NoSuchEntity')
    gateway.teardown()
    gateway.iam.delete_role.assert_called_once_with(RoleName='r')
    assert not gateway.TEARDOWN_PATH.exists()


def test_create_does_not_overwrite_cleanup_record(gateway):
    gateway._record(gateway_id='existing')
    with pytest.raises(RuntimeError):gateway.create()
    gateway.acc.create_gateway.assert_not_called()


def test_api_key_permissions_are_scoped(gateway):
    provider='arn:aws:bedrock-agentcore:us-east-1:123456789012:token-vault/default/apikeycredentialprovider/test-key'
    secret='arn:aws:secretsmanager:us-east-1:123456789012:secret:test-key-abc123'
    prefix='arn:aws:bedrock-agentcore:us-east-1:123456789012:'
    directory=prefix+'workload-identity-directory/default'
    workload=directory+'/workload-identity/test-gateway'
    statements=gateway.api_key_policy(provider,secret,workload)['Statement']
    assert statements[1]['Resource']==[directory,workload,prefix+'token-vault/default',provider]
    assert statements[2]['Resource']==[secret]
    assert statements[0]['Resource']==[directory,workload]
    assert not any(resource=='*' for s in statements for resource in s['Resource'])


def test_credential_policy_rejects_different_account(gateway):
    with pytest.raises(ValueError,match='account and Region'):
        gateway.api_key_policy('arn:aws:bedrock-agentcore:us-east-1:123456789012:token-vault/default/apikeycredentialprovider/test',
            'arn:aws:secretsmanager:us-east-1:999999999999:secret:test',
            'arn:aws:bedrock-agentcore:us-east-1:123456789012:workload-identity-directory/default/workload-identity/test')


@pytest.mark.parametrize('suffix',['*','?','other'])
def test_credential_policy_rejects_wildcards_and_other_workload_account(gateway,suffix):
    prefix='arn:aws:bedrock-agentcore:us-east-1:123456789012:'
    workload=prefix+'workload-identity-directory/default/workload-identity/'+suffix
    if suffix=='other':workload=workload.replace('123456789012','999999999999')
    with pytest.raises(ValueError):
        gateway.api_key_policy(prefix+'token-vault/default/apikeycredentialprovider/test',
            'arn:aws:secretsmanager:us-east-1:123456789012:secret:test',workload)


def test_grant_uses_actual_gateway_identity(gateway):
    gateway._record(role_name=gateway.ROLE_NAME,gateway_id='g')
    prefix='arn:aws:bedrock-agentcore:us-east-1:123456789012:'
    workload=prefix+'workload-identity-directory/default/workload-identity/actual-gateway'
    gateway.acc.get_gateway.return_value={'workloadIdentityDetails':{'workloadIdentityArn':workload}}
    gateway._grant_api_key_access({'credentialProviderArn':prefix+'token-vault/default/apikeycredentialprovider/test',
        'apiKeySecretArn':{'secretArn':'arn:aws:secretsmanager:us-east-1:123456789012:secret:test'}})
    gateway.acc.get_gateway.assert_called_once_with(gatewayIdentifier='g')
    policy=json.loads(gateway.iam.put_role_policy.call_args.kwargs['PolicyDocument'])
    assert workload in policy['Statement'][1]['Resource']
    assert all('*' not in resource for s in policy['Statement'] for resource in s['Resource'])


def test_unowned_role_is_not_changed(gateway):
    with pytest.raises(ValueError,match='unowned'):
        gateway._grant_api_key_access({})
    gateway.iam.put_role_policy.assert_not_called()


def test_cleanup_removes_added_credential_policy(gateway):
    gateway._record(role_name='r',credential_policy_name='openai-credentials')
    gateway.teardown()
    calls=gateway.iam.delete_role_policy.call_args_list
    assert [c.kwargs['PolicyName'] for c in calls]==['openai-credentials','bedrock-invoke']
