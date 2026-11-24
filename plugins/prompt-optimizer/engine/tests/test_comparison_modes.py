"""Customer-facing study modes must agree between intake, preparation and execution."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from customer_eval import project
from customer_eval.contract import ROOT, cases
from customer_eval.runner import Runner
from customer_eval.scoring import grade


def intake(tmp_path, *, count=6, optimize=False, repetitions=1):
    logs = tmp_path / 'events.jsonl'
    logs.write_text('\n'.join(json.dumps({'id': f'invoice-{i}', 'prompt': 'Extract the total as JSON.',
        'input': f'Invoice {i}: total {i}.00', 'expected': {'total': f'{i}.00'}}) for i in range(count)))
    sample = yaml.safe_load((ROOT / 'examples/log-replay/input.yaml').read_text())
    connection = {'version': 1, 'models': {m['id']: {'model': m,
        'route': sample['runner']['routes'][m['id']],
        'base_url': 'https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1'} for m in sample['models']},
        'cloud': {'runtime_arn': None, 'profile': 'test', 'region': 'us-east-1', 'account': '123456789012', 'bucket': None}}
    cp = tmp_path / 'connection.json'; project.save(cp, connection)
    directory = tmp_path / 'project'
    project.start(directory, 'Test invoice extraction')
    project.inspect(directory, [{'path': str(logs)}])
    patch = {'owner': 'Test', 'source_model': 'm1', 'target_model': 'm2',
        'connection': str(cp), 'prompt_location': 0, 'provenance': 'synthetic',
        'budget_policy': 'capped', 'budget_usd': 10, 'repetitions': repetitions,
        'optimization_enabled': optimize, 'quality': {'kind': 'json_equals', 'reference_source': 'supplied'}}
    if optimize:
        patch['optimizer_model'] = 'optimizer'
    return directory, project.configure(directory, patch)


def test_six_case_comparison_prepares_and_executes_every_case(tmp_path):
    directory, status = intake(tmp_path)
    assert status['ready_to_prepare'], status
    assert status['evaluation_plan']['split_counts'] == {'development': 0, 'validation': 3, 'holdout': 3}
    prepared = project.prepare(directory)
    config = project.load_with_endpoints(directory / prepared['config'])
    assert set(config['routes']) == {'m1', 'm2'}
    assert not cases(config, 'development')
    assert len(cases(config, 'validation')) + len(cases(config, 'holdout')) == 6
    runner = Runner(config, tmp_path / 'simulation', backend='simulation')
    report = runner.run(optimize=False)
    assert report['optimization'] is None
    assert report['selected_prompt']['prompt'] == config['prompt']
    assert not report['optimizer_requests']
    assert report['source_validation']['n_sessions'] == 3
    assert report['holdout']['source_original']['score']['n_sessions'] == 3


def test_optimizer_minimum_is_explained_during_intake(tmp_path):
    directory, status = intake(tmp_path, optimize=True)
    assert not status['ready_to_prepare']
    assert any('20 validation observations' in m for m in status['missing_decisions'])
    result = project.prepare(directory)
    assert not result['ready_to_prepare']
    assert not (directory / 'prepared').exists()


def test_explicit_optimization_preserves_independent_splits_and_model_role(tmp_path):
    directory, status = intake(tmp_path, count=60, optimize=True)
    assert status['ready_to_prepare'], status
    prepared = project.prepare(directory)
    config = project.load_with_endpoints(directory / prepared['config'])
    assert config['document']['optimization']['enabled']
    assert set(config['routes']) == {'m1', 'm2', 'optimizer'}
    assert prepared['split_counts'] == {'development': 20, 'validation': 20, 'holdout': 20}
    assert not ({r['id'] for r in cases(config, 'development')} & {r['id'] for r in cases(config, 'holdout')})


def test_grouped_requests_do_not_inflate_independent_case_count():
    rows = [{'id': str(i), 'metadata': {'document': 'same'}} for i in range(6)]
    with pytest.raises(ValueError, match='2 independent groups'):
        project._split(rows, 'seed', 'metadata.document', optimize=False)


def test_triage_extra_field_fails_the_actual_starter_check(tmp_path):
    example = ROOT.parent / 'examples/support-ticket-triage'
    records = [json.loads(line) for line in (example / 'tickets.jsonl').read_text().splitlines()]
    decisions = json.loads((example / 'decisions.template.json').read_text())
    assert decisions['quality']['fields'] == ['']
    expected = records[0]['expected']
    config = {'document': {'checks': [{'id': 'answer', 'required': True, 'method': 'deterministic'}]},
              'grading': {'answer': {'type': 'json_equals', 'path': '', 'expected_key': 'answer'}}}
    answer = dict(expected, unauthorized_extra=True)
    result = grade(config, {'expected': {'answer': expected}},
                   {'status': 'completed', 'outputs': [json.dumps(answer)]}, None)['answer']
    assert not result['pass']
    assert any(d['issue'] == 'unexpected_field' for d in result['differences'])


def test_prepared_triage_contract_keeps_whole_object_and_field_diagnostics(tmp_path):
    directory, _ = intake(tmp_path)
    example = ROOT.parent / 'examples/support-ticket-triage'
    project.inspect(directory, [{'path': str(example / 'tickets.jsonl')}])
    fields = ['', 'category', 'priority', 'queue', 'needs_review']
    project.configure(directory, {'quality': {'fields': fields}})
    prepared = project.prepare(directory)
    config = project.load_with_endpoints(directory / prepared['config'])
    case = cases(config, 'validation')[0]
    expected = case['expected']['answer']
    assert {rule['path'] for rule in config['grading'].values()} == set(fields)
    assert all(check['required'] for check in config['document']['checks'])

    missing_queue = {key: value for key, value in expected.items() if key != 'queue'}
    outputs = [
        ('correct', expected, set(), None),
        ('extra_field', dict(expected, explanation='Not allowed'), {''}, 'unexpected_field'),
        ('missing_queue', missing_queue, {'', 'queue'}, 'missing_field'),
        ('boolean_as_integer', dict(expected, needs_review=int(expected['needs_review'])),
         {'', 'needs_review'}, 'wrong_type'),
    ]
    for name, answer, failing_paths, issue in outputs:
        checks = grade(config, case, {'status': 'completed',
            'outputs': [json.dumps(answer, sort_keys=True, indent=2)]}, None)
        by_path = {config['grading'][key]['path']: check for key, check in checks.items()}
        assert {path for path, check in by_path.items() if not check['pass']} == failing_paths, name
        assert not any(check.get('error') for check in checks.values()), name
        if issue:
            assert any(d['issue'] == issue for d in by_path['']['differences']), name
