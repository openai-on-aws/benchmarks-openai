"""Source-only judging: complete context, strict judgments, and no reference leakage."""
from copy import deepcopy
import json
import shutil

import pytest
import yaml

from customer_eval.contract import ROOT, load, trial_payload
from customer_eval.replay import fingerprint
from customer_eval.scoring import grade, summarize
from customer_eval.source_grading import build_source_context, build_source_criteria, evidence_digest


def fixture():
    checks = [{'id': ident, 'question': f'Is {ident} supported by the invoice?', 'required': True,
               'method': 'llm_judge', 'judge_model_id': 'judge'} for ident in ('vendor', 'amount')]
    config = {'document': {'checks': checks, 'runner': {}, 'success_criteria': []},
              'policy': '', 'routes': {},
              'grading': {ident: {'type': 'llm_source_rubric', 'rubric': f'Check {ident} against the source.'}
                          for ident in ('vendor', 'amount')}}
    case = {'id': 'source-only', 'input': {'question': 'Invoice issued by Acme. Total due USD 125.00.'},
            'turns': ['Invoice issued by Acme. Total due USD 125.00.']}
    result = {'status': 'completed', 'outputs': ['{"vendor":"Acme","amount":125}'],
              'model_id': 'SECRET_MODEL_NAME', 'prompt': 'SECRET_CANDIDATE_PROMPT', 'tool_calls': []}
    answer = {'checks': {ident: {'status': 'pass', 'reason': 'The source directly supports the extracted value.',
                                 'evidence': [{'source_id': 'source_001', 'quote': quote}]}
                         for ident, quote in (('vendor', 'Acme'), ('amount', 'USD 125.00'))}}
    return config, case, result, answer


def test_one_blinded_source_call_without_expected_answers_and_verified_hashes():
    config, case, result, answer = fixture()
    case['expected'] = {'secret': 'NEVER_SEND_A_REFERENCE'}  # Grader must not consult labels, even on direct use.
    config['prompt'] = 'SECRET_ORIGINAL_PROMPT'
    calls = []
    def judge(model, request):
        calls.append((model, request))
        for secret in ('SECRET_MODEL', 'SECRET_CANDIDATE', 'SECRET_ORIGINAL', 'NEVER_SEND_A_REFERENCE'):
            assert secret not in request
        assert 'untrusted evidence' in request and 'not verified answers' in request
        payload = json.loads(request.split('\n', 1)[1])
        assert set(payload) == {'criteria', 'sources', 'candidate_output'}
        assert payload['candidate_output'] == result['outputs'][0]
        return json.dumps(answer)
    checks = grade(config, case, result, judge)
    assert len(calls) == 1 and calls[0][0] == 'judge'
    assert all(check['pass'] and check['status'] == 'pass' for check in checks.values())
    assert checks['amount']['source_sha256'] == evidence_digest(build_source_context(case))
    assert checks['amount']['rubric_sha256'] == evidence_digest(build_source_criteria(config))
    assert checks['amount']['evidence'] == answer['checks']['amount']['evidence']


def test_needs_review_is_not_a_pass_and_not_a_grader_error():
    config, case, result, answer = fixture()
    answer['checks']['amount'] = {'status': 'needs_review', 'reason': 'Currency is ambiguous.', 'evidence': []}
    checks = grade(config, case, result, lambda *_: json.dumps(answer))
    assert checks['vendor']['pass']
    assert checks['amount']['pass'] is False and checks['amount']['failure_category'] == 'needs_review'
    assert 'error' not in checks['amount']
    score = summarize(config, 'prompt', [{'result': result, 'checks': checks}])
    assert score.accuracy == 0 and score.n_sessions == 1
    assert score.criterion_pass_rates == {'vendor': 1, 'amount': 0}


def test_fail_remains_distinct_from_abstention_and_grader_error():
    config, case, result, answer = fixture()
    answer['checks']['amount']['status'] = 'fail'
    checks = grade(config, case, result, lambda *_: json.dumps(answer))
    assert checks['amount']['status'] == 'fail' and checks['amount']['failure_category'] == 'behavior'
    assert not checks['amount']['pass'] and 'error' not in checks['amount']


@pytest.mark.parametrize('mutation', [
    'missing', 'extra', 'wrong_envelope', 'string_checks', 'blank_reason', 'unknown_status', 'boolean_status',
    'extra_value', 'missing_value', 'wrong_evidence', 'bad_id', 'invented_quote', 'empty_quote',
    'no_pass_evidence', 'no_fail_evidence', 'extra_citation', 'candidate_quote', 'wrong_source_id_type',
])
def test_bad_judge_response_is_grading_error_for_all_source_criteria(mutation):
    config, case, result, answer = fixture()
    value = answer['checks']['amount']
    if mutation == 'missing': answer['checks'].pop('amount')
    elif mutation == 'extra': answer['checks']['extra'] = deepcopy(value)
    elif mutation == 'wrong_envelope': answer['extra'] = True
    elif mutation == 'string_checks': answer['checks'] = 'pass'
    elif mutation == 'blank_reason': value['reason'] = ' '
    elif mutation == 'unknown_status': value['status'] = 'probably'
    elif mutation == 'boolean_status': value['status'] = True
    elif mutation == 'extra_value': value['confidence'] = 1
    elif mutation == 'missing_value': value.pop('reason')
    elif mutation == 'wrong_evidence': value['evidence'] = 'USD 125.00'
    elif mutation == 'bad_id': value['evidence'][0]['source_id'] = 'source_099'
    elif mutation == 'invented_quote': value['evidence'][0]['quote'] = 'USD 125.01'
    elif mutation == 'empty_quote': value['evidence'][0]['quote'] = ' '
    elif mutation == 'no_pass_evidence': value['evidence'] = []
    elif mutation == 'no_fail_evidence': value.update(status='fail', evidence=[])
    elif mutation == 'extra_citation': value['evidence'][0]['page'] = 1
    elif mutation == 'candidate_quote': value['evidence'][0]['quote'] = result['outputs'][0]
    elif mutation == 'wrong_source_id_type': value['evidence'][0]['source_id'] = ['source_001']
    checks = grade(config, case, result, lambda *_: json.dumps(answer))
    assert all(check['error'] and not check['pass'] and check['failure_category'] == 'grading' for check in checks.values())


@pytest.mark.parametrize('text', ['{"checks":{},"checks":{}}', '{"checks":NaN}', 'not JSON', '[]'])
def test_strict_json_refuses_duplicate_keys_nonfinite_and_nonobjects(text):
    config, case, result, _ = fixture()
    checks = grade(config, case, result, lambda *_: text)
    assert all(check['error'] for check in checks.values())


def replay_case(location):
    messages = [
        {'role': 'system', 'content': 'Fixed source: supplier country is France.'},
        {'role': 'developer', 'content': 'SECRET_EDITABLE_PROMPT'},
        {'role': 'user', 'content': [{'type': 'input_text', 'text': 'Invoice from Acme.'},
                                     {'type': 'input_text', 'text': 'Currency EUR.'}]},
        {'role': 'assistant', 'content': 'Earlier extraction claimed a total of 999.'},
        {'role': 'user', 'content': 'Correction: source total is 125. Extract the invoice.'},
    ]
    request = {'instructions': 'Fixed source: billing address is Paris.', 'input': messages}
    if location == 'instructions':
        request['instructions'] = 'SECRET_EDITABLE_PROMPT'
        request['input'][1]['content'] = 'Fixed developer context: no tax applies.'
    replay = {'version': 1, 'prompt_location': location, 'request': request, 'request_sha256': fingerprint(request)}
    return {'id': 'replay', 'input': {'question': messages[-1]['content']},
            'turns': [messages[-1]['content']], 'replay': replay}


@pytest.mark.parametrize('location', [1, 'instructions'])
def test_all_fixed_replay_context_is_preserved_except_editable_slot(location):
    case = replay_case(location)
    sources = build_source_context(case, policy='Fixed protected policy.')
    assert sources[0] == {'source_id': 'source_001', 'role': 'policy', 'text': 'Fixed protected policy.'}
    texts = [source['text'] for source in sources]
    assert 'SECRET_EDITABLE_PROMPT' not in '\n'.join(texts)
    for required in ('Fixed source: supplier country is France.', 'Invoice from Acme.\nCurrency EUR.',
                     'Earlier extraction claimed a total of 999.', case['turns'][0]):
        assert required in texts
    assert ('Fixed source: billing address is Paris.' in texts) == (location != 'instructions')
    assert ('Fixed developer context: no tax applies.' in texts) == (location == 'instructions')
    assert [source['source_id'] for source in sources] == [f'source_{i:03d}' for i in range(1, len(sources)+1)]
    assert build_source_context(case, location, policy='Fixed protected policy.') == sources
    with pytest.raises(ValueError, match='prompt slot'):
        build_source_context(case, 'instructions' if location == 1 else 1)


def test_source_projection_rejects_missing_or_tampered_source_and_multioutput():
    with pytest.raises(ValueError): build_source_context({'input': {}})
    with pytest.raises(ValueError): build_source_context({'turns': ['one', 'two']})
    case = replay_case(1)
    case['replay']['request']['input'][0]['content'] = 'changed after freeze'
    with pytest.raises(ValueError, match='hash'): build_source_context(case)
    config, case, result, _ = fixture()
    for change in ({'outputs': ['one', 'two']}, {'outputs': ['']}, {'tool_calls': [{'name': 'lookup'}]}):
        malformed = dict(result, **change)
        checks = grade(config, case, malformed, lambda *_: pytest.fail('Invalid evidence must not call a judge'))
        assert all(check['error'] for check in checks.values())


def test_execution_failure_skips_judge_and_schema_check_does_not_need_labels():
    config, case, result, answer = fixture()
    config['document']['checks'].append({'id': 'schema', 'question': 'JSON?', 'required': True, 'method': 'deterministic'})
    config['grading']['schema'] = {'type': 'json_schema', 'schema': {'type': 'object', 'required': ['vendor', 'amount']}}
    checks = grade(config, case, result, lambda *_: json.dumps(answer))
    assert all(check['pass'] for check in checks.values())
    result['status'] = 'failed'
    checks = grade(config, case, result, lambda *_: pytest.fail('Incomplete execution must not call a judge'))
    assert all(not check['pass'] and 'error' not in check for check in checks.values())


def test_legacy_llm_rubric_still_receives_expected_and_does_not_use_new_envelope():
    config, case, result, _ = fixture()
    config['document']['checks'] = config['document']['checks'][:1]
    config['grading'] = {'vendor': {'type': 'llm_rubric', 'rubric': 'Match the expected vendor.'}}
    case['expected'] = {'vendor': 'Acme'}
    seen = []
    def judge(_, text):
        seen.append(json.loads(text.split('\n', 1)[1]))
        return '{"pass":true,"reason":"matches"}'
    assert grade(config, case, result, judge)['vendor']['pass']
    assert seen[0]['expected'] == 'Acme'
    case.pop('expected')
    assert grade(config, case, result, lambda *_: pytest.fail('Missing legacy reference'))['vendor']['error']


def contract_fixture(tmp_path):
    destination = tmp_path / 'input-package'
    shutil.copytree(ROOT / 'examples/customer-support', destination)
    path = destination / 'input.yaml'
    doc = yaml.safe_load(path.read_text())
    doc['runner'].update(mode='prompt', workload='none', max_turns=1)
    doc['agents'][0]['tool_allowlist'] = []
    check = doc['checks'][0]
    check.update(method='llm_judge', judge_model_id='m2')
    for dataset in doc['datasets']:
        rows = [{'id': f"{dataset['id']}-{i}", 'input': {'question': f"Invoice {dataset['id']}-{i}: total USD 125."}}
                for i in range(2)]
        (destination / dataset['path']).write_text('\n'.join(json.dumps(row) for row in rows))
    (destination / 'grading.json').write_text(json.dumps({'correctness': {'type': 'llm_source_rubric', 'rubric': 'Extract exact source values.'}}))
    path.write_text(yaml.safe_dump(doc))
    return path, doc


def test_contract_loads_reference_free_cases_and_keeps_grader_out_of_payload(tmp_path):
    path, _ = contract_fixture(tmp_path)
    cfg = load(path)
    case = cfg['datasets']['development'][0]
    assert 'expected' not in case
    payload = trial_payload(cfg, cfg['target'], cfg['prompt'], case, 'trial')
    assert 'expected' not in payload and 'grading' not in payload
    assert 'Extract exact source values.' not in json.dumps(payload)


@pytest.mark.parametrize('kind', ['null_reference', 'reference', 'agent', 'multiturn', 'empty_rubric', 'mixed_check', 'mixed_judges'])
def test_contract_rejects_unsupported_source_judging_inputs(tmp_path, kind):
    path, doc = contract_fixture(tmp_path)
    grading_path = path.parent/'grading.json'
    rules = json.loads(grading_path.read_text())
    data_path = path.parent / doc['datasets'][0]['path']
    rows = [json.loads(line) for line in data_path.read_text().splitlines()]
    if kind == 'null_reference': rows[0]['expected'] = None
    elif kind == 'reference': rows[0]['expected'] = {'correctness': 'hidden label'}
    elif kind == 'agent': doc['runner']['mode'] = 'agent'
    elif kind == 'multiturn':
        doc['runner']['max_turns'] = 2
        rows[0]['turns'] = ['first', 'second']
    elif kind == 'empty_rubric': rules['correctness']['rubric'] = ' '
    elif kind in ('mixed_check', 'mixed_judges'):
        check = deepcopy(doc['checks'][0]); check['id'] = 'another'
        doc['checks'].append(check)
        if kind == 'mixed_check':
            check.update(method='deterministic', judge_model_id=None)
            rules['another'] = {'type': 'contains', 'expected_key': 'value'}
        else:
            check['judge_model_id'] = 'm1'
            rules['another'] = {'type': 'llm_source_rubric', 'rubric': 'Another criterion.'}
    path.write_text(yaml.safe_dump(doc))
    grading_path.write_text(json.dumps(rules))
    data_path.write_text('\n'.join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError): load(path)


def test_source_metrics_keep_abstention_errors_and_missing_evidence_separate():
    from customer_eval.scoring import source_judge_metrics
    config, case, result, answer = fixture()
    success = grade(config, case, result, lambda *_: json.dumps(answer))
    rows = []
    for state in ('passed', 'failed', 'needs_review', 'grading_errors', 'execution_failures', 'unknown'):
        row = {'result': deepcopy(result), 'checks': deepcopy(success)}
        value = row['checks']['amount']
        if state == 'failed': value.update(status='fail', **{'pass': False})
        elif state == 'needs_review': value.update(status='needs_review', **{'pass': False})
        elif state == 'grading_errors': value.update(error=True, failure_category='grading')
        elif state == 'execution_failures': row['result']['status'] = 'failed'
        elif state == 'unknown': row['checks'].pop('amount')
        rows.append(row)
    metrics = source_judge_metrics(config, rows)
    assert metrics['attempts'] == 6
    assert all(metrics[key] == 1 for key in ('passed', 'failed', 'needs_review', 'grading_errors', 'execution_failures', 'unknown'))
    assert metrics['attempts_with_needs_review'] == 1
    assert metrics['by_check']['vendor']['passed'] == 5
    assert metrics['by_check']['amount']['grading_errors'] == 1
    assert 'not human-verified' in metrics['scope']
    assert metrics['quality_label'] == 'Source-assessed quality'


def test_source_metrics_use_required_checks_and_preserve_overlapping_review_needs():
    from customer_eval.scoring import source_judge_metrics
    config, case, result, answer = fixture()
    answer['checks']['vendor'].update(status='needs_review', evidence=[])
    answer['checks']['amount']['status'] = 'fail'
    checks = grade(config, case, result, lambda *_: json.dumps(answer))
    metrics = source_judge_metrics(config, [{'result': result, 'checks': checks}])
    assert metrics['failed'] == 1 and metrics['needs_review'] == 0 and metrics['attempts_with_needs_review'] == 1
    config['document']['checks'][0]['required'] = False
    checks['amount'].update(status='pass', **{'pass': True})
    metrics = source_judge_metrics(config, [{'result': result, 'checks': checks}])
    assert metrics['passed'] == 1 and metrics['attempts_with_needs_review'] == 0
    assert metrics['by_check']['vendor']['needs_review'] == 1
    config['grading'] = {key: {'type': 'llm_rubric'} for key in config['grading']}
    assert source_judge_metrics(config, []) is None
