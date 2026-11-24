"""Long studies must fit the deployment before inference is attempted."""
from copy import deepcopy
import io
import json
from types import SimpleNamespace

import pytest

from customer_eval.project_cloud import validate_runtime_capacity
from runtime.coordinator import read_study_input
from test_runtime_upgrade_preflight import deploy, Cloud, args, identity
from unittest.mock import patch


def test_full_input_larger_than_legacy_limit_is_supported_when_configured(monkeypatch):
    raw = b'x' * 10000001
    monkeypatch.setenv('EVAL_MAX_INPUT_BYTES', '11000000')
    body = io.BytesIO(raw)
    assert read_study_input(body) == raw
    assert body.closed


def test_input_read_is_bounded_and_closed_on_rejection(monkeypatch):
    monkeypatch.setenv('EVAL_MAX_INPUT_BYTES', '20')
    body = io.BytesIO(b'x' * 21)
    with pytest.raises(ValueError, match='EVAL_MAX_INPUT_BYTES'):
        read_study_input(body)
    assert body.closed


@pytest.mark.parametrize('value', ['0', '-1', '268435457', 'not-a-number'])
def test_invalid_deployed_input_limit_is_rejected(monkeypatch, value):
    monkeypatch.setenv('EVAL_MAX_INPUT_BYTES', value)
    body = io.BytesIO(b'{}')
    with pytest.raises(ValueError):
        read_study_input(body)
    assert body.closed


def test_runtime_capacity_checks_input_size_and_prefix_before_qualification():
    cfg = {'document': {'execution': {'concurrency': 4, 'timeout_seconds': 360},
                       'runner': {'limits': {'max_requests': 1}}}, 'case': 'x' * 200}
    cloud = {'bucket': 'bucket', 'prefix': 'customer-evaluation'}
    env = {'EVAL_ARTIFACT_BUCKET': 'bucket', 'EVAL_ARTIFACT_PREFIX': 'customer-evaluation',
           'EVAL_MAX_CONCURRENCY': '4', 'EVAL_MAX_TRIAL_SECONDS': '360',
           'EVAL_MAX_INPUT_BYTES': '1000'}
    validate_runtime_capacity(cfg, cloud, {'environmentVariables': env})
    for key, value in [('EVAL_MAX_INPUT_BYTES', '100'), ('EVAL_MAX_CONCURRENCY', '2'),
                       ('EVAL_MAX_TRIAL_SECONDS', '300'), ('EVAL_MAX_INPUT_BYTES', '268435457')]:
        with pytest.raises(ValueError, match=key):
            validate_runtime_capacity(cfg, cloud, {'environmentVariables': dict(env, **{key: value})})
    with pytest.raises(ValueError, match='prefix'):
        validate_runtime_capacity(cfg, dict(cloud, prefix='different'), {'environmentVariables': env})


def test_preflight_rejects_input_limit_that_coordinator_cannot_accept(tmp_path):
    from test_runtime_upgrade_preflight import inspect_case
    cloud = Cloud()
    cloud.current['environmentVariables']['EVAL_MAX_INPUT_BYTES'] = '268435457'
    result = inspect_case(tmp_path, 'agentcore', cloud)
    assert 'limit:study_input_bytes' in result['blockers']


def test_explicit_runtime_limits_preserve_other_settings_and_resume_identity(tmp_path):
    cloud = Cloud(); parameters = args(tmp_path)
    original_update = cloud.update_agent_runtime
    def apply_update(**request):
        response = original_update(**request)
        cloud.current['environmentVariables'] = deepcopy(request['environmentVariables'])
        return response
    cloud.update_agent_runtime = apply_update
    parameters.max_input_bytes = 64000000
    parameters.max_concurrency = 4
    parameters.max_trial_seconds = 360
    before = deepcopy(cloud.current)
    with patch.object(deploy, 'archive_identity', return_value=identity()):
        result = deploy.update_existing(parameters, cloud)
        assert result['status'] == 'ready'
        deploy.update_existing(parameters, cloud)
        assert len(cloud.updates) == 1
        parameters.max_concurrency = 5
        with pytest.raises(ValueError, match='different inputs'):
            deploy.update_existing(parameters, cloud)
    applied = cloud.updates[0]['environmentVariables']
    assert applied['EVAL_MAX_INPUT_BYTES'] == '64000000'
    assert applied['EVAL_MAX_CONCURRENCY'] == '4'
    assert applied['EVAL_MAX_TRIAL_SECONDS'] == '360'
    assert applied['CUSTOM_TOKEN'] == before['environmentVariables']['CUSTOM_TOKEN']
    receipt = (tmp_path / 'upgrade' / 'upgrade.json').read_text()
    assert 'test-secret-never-written' not in receipt


@pytest.mark.parametrize('key,value', [('max_input_bytes', True), ('max_concurrency', 0),
                                      ('max_trial_seconds', -1), ('max_input_bytes', 268435457)])
def test_invalid_runtime_override_cannot_change_deployment(key, value):
    with pytest.raises(ValueError):
        deploy.runtime_limit_overrides(SimpleNamespace(**{key: value}))


def test_uncertain_create_keeps_token_bound_to_exact_limit_settings(tmp_path):
    path = tmp_path / 'create-request.json'
    request = {'clientToken': 'same-token', 'environmentVariables': {'EVAL_MAX_CONCURRENCY': '4'}}
    deploy.freeze_create_request(path, request)
    before = path.read_bytes()
    deploy.freeze_create_request(path, request)
    with pytest.raises(ValueError, match='differs from the saved request'):
        deploy.freeze_create_request(path, dict(request, environmentVariables={'EVAL_MAX_CONCURRENCY': '5'}))
    assert path.read_bytes() == before
