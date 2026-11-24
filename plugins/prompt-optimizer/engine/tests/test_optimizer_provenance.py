"""Offline regressions for editable instructions, threshold status and receipts."""
import copy
from email.message import Message
from io import BytesIO
import json

import pytest

from customer_eval import transport
from customer_eval.contract import ROOT, digest, load
from customer_eval.feedback import development_feedback, mutation_evidence
from customer_eval.runner import Runner
from customer_eval.scoring import summarize
from optimizer import make_mutate_fn


def test_optimizer_preserves_developer_slot_and_uses_instruction_prompt_wording():
    seen = []
    mutate = make_mutate_fn(lambda text: seen.append(text) or '["revised instructions"]',
                            context='The editable instruction is the developer message.',
                            protected='Keep the existing system safety policy.')
    assert mutate('original instructions', ['missing required field'], 1) == ['revised instructions']
    text = seen[0]
    assert 'instruction prompt' in text
    assert 'preserve its message role and all protected messages' in text
    assert 'developer message' in text and 'system safety policy' in text
    assert 'SYSTEM PROMPT' not in text and 'candidate system prompts' not in text
    assert 'original instructions' in text and 'missing required field' in text


def feedback_inputs(criteria):
    cases = [{'id': f'dev-{i}', 'turns': ['fixture task'], 'expected': {'answer': 'ok'}} for i in range(2)]
    config = {'document': {'checks': [{'id': 'answer', 'required': True}],
                           'success_criteria': criteria, 'runner': {}},
              'datasets': {'development': cases}, 'splits': {'development': ['development']},
              'routes': {'target': {'model_id': 'fixture', 'input_usd_per_million': 1,
                                    'output_usd_per_million': 1}}}
    rows = [{'case_id': case['id'], 'split': 'development', 'model_id': 'target', 'repeat': 0,
             'result': {'status': 'completed', 'outputs': ['ok' if index else 'wrong'],
                        'latency_ms': 1000, 'route_calls': [{'usage': {'input_tokens': 100, 'output_tokens': 20}}]},
             'checks': {'answer': {'pass': bool(index), 'reason': 'fixture outcome'}}}
            for index, case in enumerate(cases)]
    return config, rows


@pytest.mark.parametrize('threshold,status,eligible,unmet', [
    (None, 'not_configured', True, []),
    (.5, 'passed', True, []),
    (.75, 'not_met', False, ['customer-minimum']),
])
def test_absolute_threshold_status_is_distinct_from_required_check_pass_rate(threshold, status, eligible, unmet):
    criteria = [] if threshold is None else [{'id': 'customer-minimum', 'metric': 'check_pass_rate',
                                              'check_id': 'answer', 'operator': '>=', 'threshold': threshold}]
    config, rows = feedback_inputs(criteria)
    before = copy.deepcopy(config)
    score = summarize(config, 'original', rows)
    value = development_feedback(config, 'original', rows)
    assert config == before
    assert score.eligible is eligible
    assert value['metrics']['absolute_thresholds'] == {
        'status': status, 'configured_count': len(criteria), 'unmet_criterion_ids': unmet}
    assert 'required_quality_passed' not in value['metrics']
    # Passing an absolute threshold must not imply that all required checks passed.
    assert value['metrics']['accuracy'] == .5
    assert value['strategy'] == 'repair_quality' and value['behavior_failure_count'] == 1
    evidence = mutation_evidence('original', {'original': value})
    assert 'does not mean every required check passed' in evidence['instructions']


def test_no_thresholds_does_not_claim_success_when_every_attempt_fails():
    config, rows = feedback_inputs([])
    for row in rows:
        row['checks']['answer']['pass'] = False
    value = development_feedback(config, 'original', rows)
    assert value['metrics']['absolute_thresholds']['status'] == 'not_configured'
    assert value['metrics']['accuracy'] == 0
    assert value['strategy'] == 'repair_quality'
    assert summarize(config, 'original', rows).eligible  # Selection semantics are unchanged.


def test_configured_threshold_with_missing_measurement_is_not_reported_as_absent():
    config, rows = feedback_inputs([{'id': 'latency-bound', 'metric': 'latency_p95_ms',
                                     'operator': '<=', 'threshold': 1000}])
    config['document']['runner']['measurement'] = {'primary_latency': 'conversation'}
    value = development_feedback(config, 'original', rows)
    assert value['metrics']['latency_p95_s'] is None
    assert value['metrics']['absolute_thresholds'] == {
        'status': 'not_met', 'configured_count': 1, 'unmet_criterion_ids': ['latency-bound']}


class ProviderResponse(BytesIO):
    def __init__(self, result, headers=()):
        super().__init__(json.dumps(result).encode())
        self.headers = Message()
        for name, value in headers:
            self.headers[name] = value


@pytest.fixture
def completion_inputs():
    route = {'model_id': 'requested-optimizer', 'parameters': {
        'reasoning': {'effort': 'high'}, 'text': {'verbosity': 'low'},
        'temperature': .2, 'top_p': .8, 'service_tier': 'default'}}
    limits = {'max_requests': 1, 'max_output_tokens': 512, 'max_request_bytes': 10000}
    result = {'id': 'response-body-id', 'status': 'completed', 'model': 'returned-optimizer',
              'service_tier': 'default', 'usage': {'input_tokens': 100, 'output_tokens': 20},
              'output': [{'type': 'message', 'content': [
                  {'type': 'output_text', 'text': '["first",'}, {'type': 'output_text', 'text': '"second"]'}]}]}
    return route, limits, result


@pytest.mark.parametrize('header', ['x-amzn-requestid', 'x-request-id'])
def test_completion_receipt_preserves_provider_identity_and_effective_settings(monkeypatch, completion_inputs, header):
    route, limits, result = completion_inputs
    sent = []
    def request(actual_route, body, timeout=60):
        sent.append(copy.deepcopy(body))
        return ProviderResponse(result, [(header, 'provider-request-id')])
    monkeypatch.setattr(transport, 'request', request)
    receipt = {}
    output, usage = transport.complete(route, 'private prompt evidence', limits, receipt=receipt)
    assert output == '["first","second"]' and usage == result['usage']
    assert len(sent) == 1
    assert receipt['model_requested'] == 'requested-optimizer'
    assert receipt['model_returned'] == 'returned-optimizer'
    assert receipt['request_id'] == 'provider-request-id'
    assert receipt['response_id'] == 'response-body-id'
    assert receipt['service_tier_requested'] == receipt['service_tier_returned'] == 'default'
    assert receipt['effective_parameters'] == {key: value for key, value in sent[0].items()
                                                if key not in ('input', 'model')}
    assert receipt['effective_parameters']['reasoning'] == {'effort': 'high'}
    assert receipt['effective_parameters']['max_output_tokens'] == 512
    assert 'private prompt evidence' not in json.dumps(receipt)


def test_complete_keeps_existing_text_usage_return_contract(monkeypatch, completion_inputs):
    route, limits, result = completion_inputs
    # Existing callers need neither a receipt argument nor response-header handling.
    monkeypatch.setattr(transport, 'request', lambda *_, **__: BytesIO(json.dumps(result).encode()))
    value = transport.complete(route, 'original caller', limits)
    assert type(value) is tuple and len(value) == 2
    assert value == ('["first","second"]', result['usage'])


def test_missing_provider_metadata_remains_unknown_and_omitted_settings_stay_omitted(monkeypatch, completion_inputs):
    route, limits, result = completion_inputs
    route['parameters'] = {'reasoning': None, 'temperature': None}
    del result['model']
    del result['service_tier']
    monkeypatch.setattr(transport, 'request', lambda *_, **__: ProviderResponse(result))
    receipt = {}
    transport.complete(route, 'fixture', limits, receipt=receipt)
    assert receipt['model_returned'] is None and receipt['request_id'] is None
    assert receipt['response_id'] == 'response-body-id'  # Never substitute a response ID for a request ID.
    assert receipt['service_tier_returned'] is None
    assert 'reasoning' not in receipt['effective_parameters']
    assert 'temperature' not in receipt['effective_parameters']


@pytest.mark.parametrize('change,message', [({'status': 'incomplete'}, 'Incomplete'), ({'output': []}, 'Empty')])
def test_unsuccessful_completion_does_not_publish_success_metadata(monkeypatch, completion_inputs, change, message):
    route, limits, result = completion_inputs
    result.update(change)
    monkeypatch.setattr(transport, 'request', lambda *_, **__: ProviderResponse(result))
    receipt = {}
    with pytest.raises(ValueError, match=message):
        transport.complete(route, 'fixture', limits, receipt=receipt)
    assert receipt == {}


def test_runner_persists_optimizer_receipt_and_reuses_it_without_another_call(monkeypatch, tmp_path, completion_inputs):
    _, _, result = completion_inputs
    config = load(ROOT / 'examples/log-replay/input.yaml')
    config['routes']['optimizer']['model_id'] = 'requested-optimizer'
    seen = []
    def request(route, body, timeout=60):
        seen.append(copy.deepcopy(body))
        return ProviderResponse(result, [('x-amzn-requestid', 'provider-request-id')])
    monkeypatch.setattr(transport, 'request', request)
    runner = Runner(config, tmp_path, backend='local')
    try:
        assert runner.call('optimizer', 'mutation evidence', purpose='optimizer') == '["first","second"]'
        assert runner.call('optimizer', 'mutation evidence', purpose='optimizer') == '["first","second"]'
        assert len(seen) == 1
        ident = digest(['completion', 'optimizer', 'optimizer', 'mutation evidence'])
        state, value = runner.store.db.execute('select state,value from attempts where id=?', (ident,)).fetchone()
        receipt = json.loads(value)
        assert state == 'complete' and receipt['status'] == 'completed'
        assert receipt['model_id'] == 'optimizer' and receipt['purpose'] == 'optimizer'
        assert receipt['model_requested'] == 'requested-optimizer'
        assert receipt['model_returned'] == 'returned-optimizer'
        assert receipt['request_id'] == 'provider-request-id'
        assert receipt['effective_parameters']['reasoning'] == {'effort': 'high'}
        assert receipt['effective_parameters']['max_output_tokens'] == 8192
        assert receipt['request_text'] == 'mutation evidence' and receipt['usage'] == result['usage']
    finally:
        runner.store.db.close()


def test_runner_reuses_legacy_completion_receipt_without_inventing_metadata(monkeypatch, tmp_path):
    config = load(ROOT / 'examples/log-replay/input.yaml')
    runner = Runner(config, tmp_path, backend='local')
    ident = digest(['completion', 'optimizer', 'optimizer', 'legacy'])
    legacy = {'text': '["saved"]', 'usage': {'input_tokens': 1, 'output_tokens': 1},
              'model_id': 'optimizer', 'purpose': 'optimizer', 'request_text': 'legacy'}
    runner.store.perform(ident, 0, lambda: legacy)
    monkeypatch.setattr(transport, 'request', lambda *_, **__: pytest.fail('Saved completion must not be replayed'))
    try:
        assert runner.call('optimizer', 'legacy', purpose='optimizer') == '["saved"]'
        saved = json.loads(runner.store.db.execute('select value from attempts where id=?', (ident,)).fetchone()[0])
        assert saved == legacy
    finally:
        runner.store.db.close()
