"""Coordinator completion limits are separate from application worker limits."""
from copy import deepcopy
import io
import json
from unittest.mock import Mock

import pytest

from customer_eval.contract import digest
from runtime.coordinator import CoordinatorPaused, deadline_guard, request_reserve_seconds, start_job


def config():
    limits = {'max_requests': 1, 'max_output_tokens': 4096, 'max_request_bytes': 60000,
              'request_timeout_seconds': 180}
    return {'document': {'runner': {'limits': limits,
                                    'judge_limits': dict(limits, max_output_tokens=8192, request_timeout_seconds=600),
                                    'optimizer_limits': dict(limits, max_output_tokens=16384, request_timeout_seconds=600)},
                         'execution': {'timeout_seconds': 300, 'concurrency': 2, 'budget_usd': 10},
                         'checks': [{'method': 'llm_judge'}], 'optimization': {'enabled': True}},
            'routes': {}}


def test_long_completions_are_allowed_independently_of_worker_trial_cap():
    cfg = config()
    assert request_reserve_seconds(cfg, {'EVAL_MAX_TRIAL_SECONDS': '300'}) == 600
    deadline_guard(0, 1000, request_reserve_seconds(cfg, {}), clock=lambda: 369)
    with pytest.raises(CoordinatorPaused):
        deadline_guard(0, 1000, request_reserve_seconds(cfg, {}), clock=lambda: 370)
    # The previous worker-only reserve would still have dispatched at this point.
    deadline_guard(0, 1000, 300, clock=lambda: 370)


@pytest.mark.parametrize('purpose', ['limits', 'judge_limits', 'optimizer_limits'])
@pytest.mark.parametrize('field,value', [('max_requests', 0), ('max_output_tokens', True),
                                        ('max_request_bytes', -1), ('request_timeout_seconds', float('inf')),
                                        ('request_timeout_seconds', '600'), ('max_request_bytes', None)])
def test_invalid_purpose_limits_fail_before_dispatch(purpose, field, value):
    cfg = config(); cfg['document']['runner'][purpose][field] = value
    with pytest.raises(ValueError): request_reserve_seconds(cfg, {})


@pytest.mark.parametrize('change', ['missing_field', 'unknown_field', 'not_object'])
def test_malformed_envelope_cannot_enter_background_job(change):
    cfg = config()
    if change == 'missing_field': cfg['document']['runner']['judge_limits'].pop('max_request_bytes')
    elif change == 'unknown_field': cfg['document']['runner']['judge_limits']['timeout'] = 600
    else: cfg['document']['runner']['judge_limits'] = []
    with pytest.raises(ValueError): request_reserve_seconds(cfg, {})


def test_legacy_fallbacks_and_inactive_roles_preserve_worker_reserve():
    cfg = config(); cfg['document']['checks'] = []; cfg['document']['optimization']['enabled'] = False
    assert request_reserve_seconds(cfg, {}) == 300
    cfg['document']['checks'] = [{'method': 'llm_judge'}]
    cfg['document']['runner'].pop('judge_limits'); cfg['document']['runner'].pop('optimizer_limits')
    assert request_reserve_seconds(cfg, {}) == 300


@pytest.mark.parametrize('environ', [{'EVAL_MAX_TRIAL_SECONDS': '299'}, {'EVAL_MAX_REQUESTS_PER_TRIAL': '0'},
                                    {'EVAL_COORDINATOR_MAX_SECONDS': '630'}, {'EVAL_COORDINATOR_MAX_SECONDS': 'nan'},
                                    {'EVAL_MAX_TRIAL_SECONDS': '-1'}])
def test_existing_deployment_caps_and_lifetime_still_apply(environ):
    with pytest.raises(ValueError): request_reserve_seconds(config(), environ)


def test_request_count_cap_applies_to_worker_without_imposing_worker_timeout_on_judge():
    cfg = config(); cfg['document']['runner']['limits']['max_requests'] = 13
    with pytest.raises(ValueError, match='request count'):
        request_reserve_seconds(cfg, {'EVAL_MAX_REQUESTS_PER_TRIAL': '12'})
    cfg['document']['runner']['limits']['max_requests'] = 1
    assert request_reserve_seconds(cfg, {'EVAL_MAX_REQUESTS_PER_TRIAL': '12'}) == 600
    cfg['document']['runner']['limits']['request_timeout_seconds'] = 301
    with pytest.raises(ValueError, match='worker trial timeout'): request_reserve_seconds(cfg, {})


def test_invalid_judge_limits_are_rejected_before_job_claim_or_thread(monkeypatch):
    import boto3
    cfg = config(); cfg['document']['runner']['judge_limits']['max_output_tokens'] = 0
    cfg['fingerprint'] = digest(cfg)
    storage = Mock(); storage.get_object.return_value = {'Body': io.BytesIO(json.dumps(cfg).encode())}
    monkeypatch.setattr(boto3, 'client', lambda *_: storage)
    monkeypatch.setenv('EVAL_ARTIFACT_BUCKET', 'fixture-bucket')
    monkeypatch.setenv('EVAL_MAX_MODEL_BUDGET_USD', '100')
    app = Mock()
    with pytest.raises(ValueError, match='judge.max_output_tokens'):
        start_job({'job_id': 'fixture-job', 'input_key': 'customer-evaluation/inputs/fixture.json',
                   'fingerprint': cfg['fingerprint']}, app)
    storage.put_object.assert_not_called()
    app.add_async_task.assert_not_called()
