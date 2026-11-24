"""Reference correctness stays independent from optional source explanations."""
from copy import deepcopy
import json
import pytest
import yaml
from customer_eval import project
from customer_eval.contract import load
from customer_eval.json_grading import grade_json_unordered, unordered_value
from customer_eval.log_import import ImportIssue, normalize, mapped_row
from customer_eval.scoring import at, grade, summarize, source_judge_metrics
from customer_eval.source_grading import build_source_context
from test_source_judge_intake import captured, setup_project

QUALITY = {
    'kind': 'json_checks', 'reference_source': 'supplied',
    'checks': [
        {'id': 'matches', 'type': 'json_unordered_equals', 'path': 'matches'},
        {'id': 'balance', 'type': 'json_equals', 'path': 'balance'}],
    'source_checks': [{'id': 'explanation', 'rubric': 'Is the explanation supported by the source?', 'required': False}],
    'schema': {'type': 'object', 'properties': {
        'matches': {'type': 'array', 'items': {'type': 'object'}},
        'balance': {'type': 'string'}, 'explanation': {'type': 'string'}},
        'required': ['matches', 'balance', 'explanation'], 'additionalProperties': False}}
ANSWER = {'matches': [{'bank_ids': ['B2', 'B1'], 'ledger_ids': ['L1'], 'amount': '12.00'}],
          'balance': '12.00', 'explanation': 'REFERENCE_SENTINEL_NOT_FOR_JUDGE'}


def mixed_project(tmp_path):
    directory, source = setup_project(tmp_path)
    rows = [dict(captured(i), expected=deepcopy(ANSWER)) for i in range(24)]
    source.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    project.configure(directory, {'quality': deepcopy(QUALITY),
                                 'split_counts': {'development': 10, 'validation': 7, 'holdout': 7}})
    project.inspect(directory, [{'path': str(source)}], prompt_location=1)
    ready = project.status(directory)
    assert ready['ready_to_prepare'], ready['missing_decisions']
    prepared = project.prepare(directory)
    return directory, project.load_with_endpoints(directory / prepared['config'])


def test_mixed_preparation_has_refs_and_source_checks_without_leaking_references(tmp_path):
    directory, config = mixed_project(tmp_path)
    assert config['document']['runner']['grading_reference_mode'] == 'references_with_source_judges'
    assert config['intake_context']['assessment_method'] == 'reference_and_source'
    assert config['intake_context']['expected_answers_provided'] is True
    assert config['intake_context']['label_status'] == 'unreviewed'
    assert {k: len(v) for k, v in config['datasets'].items()} == {'development': 10, 'validation': 7, 'holdout': 7}
    case = config['datasets']['development'][0]
    assert case['expected']['answer'] == ANSWER
    assert 'REFERENCE_SENTINEL' not in json.dumps(case['replay']['request'])
    assert 'REFERENCE_SENTINEL' not in json.dumps(build_source_context(case))
    assert config['document']['runner']['judge_limits']['max_output_tokens'] == 4096
    assert next(c for c in config['document']['checks'] if c['id'] == 'explanation')['required'] is False


def test_optional_judge_failure_does_not_change_reference_quality_or_cost_denominator(tmp_path):
    _, config = mixed_project(tmp_path)
    case = config['datasets']['development'][0]
    actual = deepcopy(ANSWER); actual['explanation'] = 'The printed invoice needs review.'
    seen = []
    source = build_source_context(case)[-1]['text']
    def judge(model, text):
        seen.append(text)
        return json.dumps({'checks': {'explanation': {'status': 'fail', 'reason': 'The explanation omits an issue.',
            'evidence': [{'source_id': 'source_004', 'quote': source}]}}})
    # The historical assistant and final user produce four fixed sources.
    assert build_source_context(case)[-1]['source_id'] == 'source_004'
    result = {'status': 'completed', 'outputs': [json.dumps(actual)], 'tool_calls': [], 'simulated': True,
              'timing': {'conversation_ms': 1000}, 'latency_ms': 1000, 'route_calls': []}
    checks = grade(config, case, result, judge)
    assert checks['explanation']['status'] == 'fail'
    assert all(checks[k]['pass'] for k in ('matches', 'balance', 'output_schema'))
    assert 'REFERENCE_SENTINEL' not in seen[0]
    row = {'id': 'test', 'case_id': case['id'], 'split': 'development', 'repeat': 0, 'model_id': 'm2',
           'result': result, 'checks': checks}
    metrics = source_judge_metrics(config, [row])
    assert metrics['method'] == 'reference_and_source' and metrics['passed'] == 1 and metrics['failed'] == 0
    assert metrics['by_check']['explanation']['failed'] == 1
    assert metrics['quality_label'] == 'Reference check pass rate'
    assert summarize(config, config['prompt'], [row]).accuracy == 1.0


@pytest.mark.parametrize('mutate', [
    lambda d: d['runner'].pop('grading_reference_mode'),
    lambda d: next(c for c in d['checks'] if c['id'] == 'explanation').update(required=True)])
def test_mixed_contract_rejects_implicit_mode_or_required_source_check(tmp_path, mutate):
    directory, config = mixed_project(tmp_path)
    path = directory / 'prepared/input.yaml'; d = yaml.safe_load(path.read_text()); mutate(d)
    path.write_text(yaml.safe_dump(d))
    with pytest.raises(ValueError): project.load_with_endpoints(path)


def test_quality_requires_optional_source_checks_and_unique_ids():
    q = deepcopy(QUALITY); q['source_checks'][0]['required'] = True
    with pytest.raises(ValueError, match='optional'): project.quality_checks(q)
    q = deepcopy(QUALITY); q['source_checks'][0]['id'] = 'balance'
    with pytest.raises(ValueError, match='unique'): project.quality_checks(q)
    q = deepcopy(QUALITY); q['kind'] = 'source_judge'
    with pytest.raises(ValueError, match='requires kind json_checks'): project.quality_checks(q)


def test_unordered_records_accept_reordering_at_every_level():
    expected = [{'bank_ids': ['B1', 'B2'], 'ledger_ids': ['L1'], 'amount': '10.00'},
                {'bank_ids': ['B3'], 'ledger_ids': ['L2'], 'amount': '20.00'}]
    actual = [deepcopy(expected[1]), deepcopy(expected[0])]; actual[1]['bank_ids'].reverse()
    assert grade_json_unordered([json.dumps({'matches': actual})], expected, 'matches', at)['pass']


@pytest.mark.parametrize('actual', [
    [{'bank_ids': ['B1', 'B1'], 'amount': '10.00'}],
    [{'bank_ids': ['B1'], 'amount': '10.00'}, {'bank_ids': ['B1'], 'amount': '10.00'}],
    [{'bank_ids': ['B1'], 'amount': '11.00'}],
    [{'bank_ids': ['B1'], 'amount': 10.0}],
    [{'bank_ids': ['B1'], 'amount': '10.00', 'extra': True}],
    [], {'bank_ids': ['B1'], 'amount': '10.00'}])
def test_unordered_records_reject_duplicate_missing_extra_and_changed_values(actual):
    expected = [{'bank_ids': ['B1'], 'amount': '10.00'}]
    assert not grade_json_unordered([json.dumps({'matches': actual})], expected, 'matches', at)['pass']


def test_invalid_unordered_reference_is_grading_error_not_model_failure():
    with pytest.raises(ValueError, match='duplicate'):
        grade_json_unordered(['[]'], ['B1', 'B1'], '', at)
    assert unordered_value([True]) != unordered_value([1])
    assert unordered_value([1.0]) != unordered_value([1])


def test_unordered_mismatch_gives_actionable_missing_and_unexpected_records():
    expected = [{'bank_ids': ['B1'], 'amount': '10.00'}]
    actual = [{'bank_ids': ['B2'], 'amount': '10.00'}]
    result = grade_json_unordered([json.dumps(actual)], expected, '', at)
    assert result['missing_values'] == expected and result['unexpected_values'] == actual


def test_exact_split_counts_are_reproducible_and_keep_groups_intact():
    cases = [{'id': str(i), 'family': str(i)} for i in range(100)]
    counts = {'development': 40, 'validation': 30, 'holdout': 30}
    a = project._split(cases, 'seed', None, split_counts=counts)
    b = project._split(list(reversed(cases)), 'seed', None, split_counts=counts)
    assert a == b and {k: len(v) for k, v in a.items()} == counts
    with pytest.raises(ValueError, match='one-case groups'):
        project._split([dict(c, family=str(int(c['id']) // 2)) for c in cases], 'seed', 'family', split_counts=counts)
    with pytest.raises(ValueError, match='sum to all'):
        project._split(cases, 'seed', None, split_counts=dict(counts, holdout=29))


@pytest.mark.parametrize('form', ['chat', 'responses', 'custom'])
def test_captured_request_with_supplied_reference_needs_no_recorded_response(form):
    raw = dict(captured(), expected=deepcopy(ANSWER)); mapping = None
    if form == 'responses': raw['request']['input'] = raw['request'].pop('messages')
    if form == 'custom':
        raw = {'payload': raw}; mapping = {'request': 'payload.request', 'expected': 'payload.expected', 'id': 'payload.id'}
    case = normalize(raw, mapping=mapping, prompt_location=1)
    assert case['expected']['answer'] == ANSWER
    assert case['metadata']['reference_kind'] == 'supplied_expected'
    assert case['metadata']['recorded_response_present'] is False
    assert case['replay']['request']['input'][0]['role'] == 'system'
    assert case['replay']['request']['input'][1]['role'] == 'developer'
    assert 'REFERENCE_SENTINEL' not in json.dumps(case['replay']['request'])


def test_absent_or_null_reference_is_not_manufactured():
    with pytest.raises(ImportIssue, match='missing_request_or_response'): normalize(captured())
    with pytest.raises(ImportIssue, match='missing_supplied_reference'): normalize(dict(captured(), expected=None))
    with pytest.raises(ImportIssue, match='source_judge_expected_answers_conflict'):
        normalize(dict(captured(), expected=ANSWER), reference_mode='source_judge')


def test_reference_failure_is_not_overridden_by_passing_explanation(tmp_path):
    _, config = mixed_project(tmp_path)
    case = config['datasets']['development'][0]
    actual = deepcopy(ANSWER); actual['balance'] = '999.00'; actual['explanation'] = 'Supported explanation.'
    source = build_source_context(case)[-1]
    def judge(*_):
        return json.dumps({'checks': {'explanation': {'status': 'pass', 'reason': 'Explanation supported.',
            'evidence': [{'source_id': source['source_id'], 'quote': source['text']}]}}})
    result = {'status': 'completed', 'outputs': [json.dumps(actual)], 'tool_calls': [], 'simulated': True,
              'timing': {'conversation_ms': 1000}, 'route_calls': []}
    checks = grade(config, case, result, judge)
    row = {'case_id': case['id'], 'model_id': 'm2', 'result': result, 'checks': checks}
    assert checks['explanation']['pass'] and not checks['balance']['pass']
    assert source_judge_metrics(config, [row])['failed'] == 1
    assert summarize(config, config['prompt'], [row]).accuracy == 0.0


@pytest.mark.parametrize('mode', ['parse', 'transport'])
def test_optional_judge_error_remains_visible_and_is_never_a_pass(tmp_path, mode):
    _, config = mixed_project(tmp_path)
    case = config['datasets']['development'][0]
    def judge(*_):
        if mode == 'transport': raise RuntimeError('transport failed before a complete receipt')
        return 'malformed JSON'
    actual = deepcopy(ANSWER); actual['explanation'] = 'Model text.'
    result = {'status': 'completed', 'outputs': [json.dumps(actual)], 'tool_calls': [], 'route_calls': []}
    checks = grade(config, case, result, judge)
    row = {'result': result, 'checks': checks}
    assert checks['explanation']['error'] and not checks['explanation']['pass']
    metrics = source_judge_metrics(config, [row])
    assert metrics['passed'] == 1  # Required financial facts still match.
    assert metrics['by_check']['explanation']['grading_errors'] == 1


def test_uncertain_optional_judge_request_keeps_unknown_cost_and_is_not_retried(tmp_path, monkeypatch):
    from customer_eval import runner as module
    from customer_eval.runner import Runner, InterruptedAttempt
    from customer_eval.costs import run_costs
    _, config = mixed_project(tmp_path)
    count = []
    def broken(*args, **kwargs):
        count.append(1)
        raise InterruptedAttempt('Possibly dispatched; no receipt')
    monkeypatch.setattr(module, 'complete', broken)
    run = Runner(config, tmp_path / 'run', backend='local')
    for _ in range(2):
        with pytest.raises(InterruptedAttempt): run.call('judge', 'same source-and-output request')
    assert len(count) == 1
    unresolved = run.store.db.execute('SELECT count(*) FROM attempts WHERE state!=?', ('complete',)).fetchone()[0]
    assert unresolved == 1
    costs = run_costs(config, [], unresolved_attempts=unresolved)
    assert costs['estimated_model_cost_usd'] is None and costs['unpriced_requests_or_incomplete_traces'] == 1
    run.store.db.close()
