"""Offline source-judge feedback, receipt accounting and identical baseline reuse."""
from copy import deepcopy
import json

import pytest

from customer_eval.contract import ROOT, load
from customer_eval.feedback import development_feedback
from customer_eval.runner import Runner
from customer_eval.source_grading import build_source_context
from customer_eval import runner as runner_module


def source_feedback_inputs():
    cases = [{'id': name, 'turns': [f'Invoice {name}: supplier Acme; total EUR 125.00.']}
             for name in ('failed', 'passing', 'uncertain', 'grader-error', 'execution-error')]
    config = {'document': {'checks': [{'id': 'currency', 'question': 'Is the currency supported?', 'required': True}],
                           'success_criteria': [], 'runner': {}},
              'policy': '', 'grading': {'currency': {'type': 'llm_source_rubric', 'rubric': 'Use the source currency; do not infer from the supplier location.'}},
              'splits': {'development': ['development']},
              'datasets': {'development': cases,
                           'validation': [{'id': 'SECRET_VALIDATION', 'turns': ['SECRET_VALIDATION_INPUT']}],
                           'holdout': [{'id': 'SECRET_HOLDOUT', 'turns': ['SECRET_HOLDOUT_INPUT']}]},
              'routes': {'target': {'model_id': 'fixture-model', 'input_usd_per_million': 1., 'output_usd_per_million': 2.}}}
    rows = []
    for case in cases:
        name = case['id']
        check = {'status': 'pass', 'pass': True, 'reason': 'Currency matches the source.',
                 'evidence': [{'source_id': 'source_001', 'quote': 'EUR 125.00'}],
                 'source_sha256': 'source-digest', 'rubric_sha256': 'rubric-digest', 'failure_category': None}
        if name == 'failed':
            check.update(status='fail', **{'pass': False}, reason='Output says USD but source explicitly says EUR.', failure_category='behavior')
        if name == 'uncertain':
            check.update(status='needs_review', **{'pass': False}, reason='The source does not resolve currency.', evidence=[], failure_category='needs_review')
        if name == 'grader-error':
            check = {'pass': False, 'error': True, 'reason': 'Judge response was malformed.', 'failure_category': 'grading'}
        if name == 'execution-error':
            check = {'pass': False, 'reason': 'Execution did not complete.'}
        rows.append({'case_id': name, 'split': 'development', 'repeat': 0, 'model_id': 'target',
                     'result': {'status': 'failed' if name == 'execution-error' else 'completed',
                                'outputs': ['{"currency":"USD"}' if name == 'failed' else '{"currency":"EUR"}'],
                                'tool_calls': [], 'latency_ms': 100,
                                'route_calls': [{'usage': {'input_tokens': 30, 'output_tokens': 10}}]},
                     'checks': {'currency': check}})
    return config, rows


def test_source_feedback_uses_actual_evidence_without_reference_answers():
    config, rows = source_feedback_inputs()
    result = development_feedback(config, 'Extract invoice fields.', rows)
    assert result['behavior_failure_count'] == 1
    assert result['infrastructure_failure_count'] == 2
    assert result['needs_review_count'] == 1
    assert result['example_selection']['distinct_failure_cases'] == 1
    assert result['example_selection']['distinct_passing_cases'] == 1
    examples = {example['case_id']: example for example in result['examples']}
    assert set(examples) == {'failed', 'passing'}
    assert examples['failed']['source_context'] == build_source_context(config['datasets']['development'][0])
    assert all('expected' not in example for example in examples.values())
    assert all('assessment' in example and 'no verified expected answers' in example['assessment'] for example in examples.values())
    diagnostic, = result['failure_diagnostics']
    assert diagnostic['case_id'] == 'failed' and diagnostic['actionable'] is True
    assert diagnostic['evidence'] == [{'source_id': 'source_001', 'quote': 'EUR 125.00'}]
    assert diagnostic['reason'] == 'Output says USD but source explicitly says EUR.'
    assert diagnostic['actual'] == '{"currency":"USD"}'
    assert diagnostic['rubric'] == config['grading']['currency']['rubric']
    assert not {'expected', 'expected_key', 'expected_status'} & set(diagnostic)
    assert 'SECRET_VALIDATION' not in json.dumps(result) and 'SECRET_HOLDOUT' not in json.dumps(result)
    assert 'not definite errors' in result['uncertainty_instruction']


def test_abstention_only_feedback_does_not_create_failure_or_passing_examples():
    config, rows = source_feedback_inputs()
    rows = [row for row in rows if row['case_id'] == 'uncertain']
    result = development_feedback(config, 'Keep unknown fields null.', rows)
    assert result['behavior_failure_count'] == result['infrastructure_failure_count'] == 0
    assert result['needs_review_count'] == 1
    assert result['failure_diagnostics'] == [] and result['examples'] == []
    assert result['example_selection']['distinct_passing_cases'] == 0
    assert result['metrics']['accuracy'] == 0


def test_validation_or_holdout_never_becomes_source_optimization_feedback():
    config, rows = source_feedback_inputs()
    for change in ({'split': 'holdout'}, {'case_id': 'SECRET_VALIDATION'}):
        changed = [dict(rows[0], **change)]
        with pytest.raises(ValueError):
            development_feedback(config, 'Prompt.', changed)


def test_judge_request_uses_separate_limits_and_saves_request_usage_and_elapsed(monkeypatch, tmp_path):
    config = load(ROOT / 'examples/customer-support/input.yaml')
    config['document']['runner']['judge_limits'] = {'max_requests': 4, 'max_output_tokens': 765,
                                                   'max_request_bytes': 9000, 'request_timeout_seconds': 88}
    config['document']['runner']['optimizer_limits'] = {'max_requests': 7, 'max_output_tokens': 1234,
                                                       'max_request_bytes': 12000, 'request_timeout_seconds': 99}
    calls = []
    def complete(route, text, limits, *, receipt):
        calls.append(deepcopy(limits))
        receipt.update(model_requested='judge-model', model_returned='judge-model', provider_request_id='fixture-receipt')
        return '{"checks":{}}', {'input_tokens': 12, 'output_tokens': 7}
    monkeypatch.setattr(runner_module, 'complete', complete)
    instance = Runner(config, tmp_path, backend='local')
    text = 'Fixed rubric plus source and candidate.'
    assert instance.call('m2', text) == '{"checks":{}}'
    record, = [json.loads(row[0]) for row in instance.store.db.execute('SELECT value FROM attempts WHERE state="complete"')]
    assert calls == [dict(config['document']['runner']['judge_limits'], max_requests=1)]
    assert record['purpose'] == 'judge' and record['request_text'] == text
    assert record['usage'] == {'input_tokens': 12, 'output_tokens': 7}
    assert record['provider_request_id'] == 'fixture-receipt'
    assert type(record['completion_elapsed_ms']) is float and record['completion_elapsed_ms'] >= 0
    assert instance.call('m2', text) == '{"checks":{}}'
    assert len(calls) == 1, 'Completed judge requests must be reused, not billed again'
    instance.call('m2', text, purpose='optimizer')
    assert calls[1] == dict(config['document']['runner']['optimizer_limits'], max_requests=1)


def test_judge_failure_keeps_unresolved_receipt_without_automatic_retry(monkeypatch, tmp_path):
    from customer_eval.runner import InterruptedAttempt
    config = load(ROOT / 'examples/customer-support/input.yaml')
    called = []
    def failure(*args, **kwargs):
        called.append(1)
        raise TimeoutError('unconfirmed provider attempt')
    monkeypatch.setattr(runner_module, 'complete', failure)
    instance = Runner(config, tmp_path, backend='local')
    with pytest.raises(TimeoutError): instance.call('m2', 'Same evidence.')
    with pytest.raises(InterruptedAttempt): instance.call('m2', 'Same evidence.')
    assert len(called) == 1
    assert instance.store.db.execute('SELECT state,value FROM attempts').fetchone() == ('started', None)


@pytest.mark.parametrize('same_route', [True, False])
def test_identical_baseline_reuses_actual_attempts_but_different_route_does_not(tmp_path, same_route):
    config = load(ROOT / 'examples/customer-support/input.yaml')
    if not same_route:
        config['routes']['m1']['parameters']['reasoning'] = {'effort': 'high'}
    instance = Runner(config, tmp_path)
    calls = []
    execute = instance.execute
    def tracked(payload):
        calls.append(payload['trial_id'])
        return execute(payload)
    instance.execute = tracked
    report = instance.run(optimize=False)
    assert report['identical_model_baseline_reused'] is same_route
    assert len(calls) == (8 if same_route else 16)
    source = report['holdout']['source_original']
    target = report['holdout']['target_original']
    if same_route:
        assert source['reused_from'] == 'target_original'
        assert source['trials'] == target['trials']
        assert report['source_validation'] == report['target_validation']
    else:
        assert 'reused_from' not in source
        assert {row['id'] for row in source['trials']}.isdisjoint({row['id'] for row in target['trials']})
