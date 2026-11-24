"""Customer-sized text inputs and independent structured policy-decision checks."""
from copy import deepcopy
import json

import pytest
import yaml

from customer_eval import project
from customer_eval.contract import ROOT, cases, trial_payload
from customer_eval.json_grading import schema_validator
from customer_eval.scoring import grade
from customer_eval.transport import reservation


SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {'credit_cents': {'type': 'integer', 'minimum': 0},
                   'needs_review': {'type': 'boolean'},
                   'evidence': {'type': 'array', 'items': {'type': 'string'}, 'uniqueItems': True}},
    'required': ['credit_cents', 'needs_review', 'evidence'],
}
QUALITY = {
    'kind': 'json_checks', 'reference_source': 'supplied', 'schema': SCHEMA,
    'checks': [
        {'id': 'credit', 'type': 'json_equals', 'path': 'credit_cents', 'description': 'Is the credit correct?'},
        {'id': 'review', 'type': 'json_equals', 'path': 'needs_review'},
        {'id': 'evidence', 'type': 'json_set_equals', 'path': 'evidence'},
    ],
}


def draft(tmp_path):
    source = tmp_path / 'cases.jsonl'
    source.write_text('\n'.join(json.dumps({
        'id': f'case-{i}', 'prompt': 'Resolve the case using the supplied contract and incident evidence.',
        'input': f'Contract case {i}. ' + ('A distinct operational observation. ' * 3000),
        'expected': {'credit_cents': i * 125, 'needs_review': False, 'evidence': [f'contract-{i}', f'incident-{i}']},
    }) for i in range(24)))
    base = yaml.safe_load((ROOT / 'examples/log-replay/input.yaml').read_text())
    connection = {'version': 1, 'models': {m['id']: {'model': m, 'route': base['runner']['routes'][m['id']],
        'base_url': 'https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1'} for m in base['models']},
        'cloud': {'runtime_arn': 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example',
                  'profile': 'test', 'region': 'us-east-1', 'account': '123456789012', 'bucket': 'test'}}
    connection_path = tmp_path / 'connection.json'
    connection_path.write_text(json.dumps(connection))
    path = tmp_path / 'project'
    project.start(path, 'Compare complex policy decisions')
    project.inspect(path, [{'path': str(source)}])
    project.configure(path, {'owner': 'Test', 'source_model': 'm1', 'target_model': 'm2',
        'optimizer_model': 'optimizer', 'connection': str(connection_path), 'prompt_location': 0,
        'provenance': 'synthetic', 'budget_policy': 'unlimited', 'quality': deepcopy(QUALITY),
        'limits': {'max_request_bytes': 200000, 'max_output_tokens': 4096},
        'optimizer_limits': {'max_request_bytes': 260000, 'max_output_tokens': 12288}, 'repetitions': 3})
    return path


def completed(answer):
    return {'status': 'completed', 'outputs': [json.dumps(answer)]}


def test_complex_guided_preparation_preserves_controls_and_independent_checks(tmp_path):
    path = draft(tmp_path)
    status = project.status(path)
    assert status['ready_to_prepare']
    assert status['evaluation_plan']['limits']['max_request_bytes'] == 200000
    prepared = project.prepare(path)
    cfg = project.load_with_endpoints(path / prepared['config'], live=True)
    assert cfg['document']['runner']['limits'] == {
        'max_requests': 1, 'max_output_tokens': 4096, 'max_request_bytes': 200000, 'request_timeout_seconds': 60}
    assert cfg['document']['runner']['optimizer_limits'] == {
        'max_requests': 1, 'max_output_tokens': 12288, 'max_request_bytes': 260000, 'request_timeout_seconds': 60}
    assert set(cfg['grading']) == {'output_schema', 'credit', 'review', 'evidence'}
    case = cases(cfg, 'development')[0]
    answer = deepcopy(case['expected']['answer'])
    answer['evidence'].reverse()
    checks = grade(cfg, case, completed(answer), None)
    assert all(check['pass'] for check in checks.values())
    payload = trial_payload(cfg, cfg['target'], cfg['prompt'], case, 'test')
    assert 'expected' not in payload and 'grading' not in payload
    assert len(payload['replay']['request']['input'][-1]['content']) > 80000
    assert all(d['label_status'] == 'unreviewed' for d in cfg['document']['datasets'])
    assert reservation(cfg['routes']['m2'], cfg['document']['runner']['limits']) > reservation(
        cfg['routes']['m2'], project.DEFAULT_LIMITS)


@pytest.mark.parametrize('limits', [
    {'max_output_tokens': 0}, {'max_output_tokens': True}, {'max_request_bytes': -1},
    {'max_request_bytes': 1.5}, {'max_requests': 2}, {'max_tokens': 100}, [], None,
    {'request_timeout_seconds': False}, {'request_timeout_seconds': 0}, {'request_timeout_seconds': '300'},
])
@pytest.mark.parametrize('key', ['limits', 'optimizer_limits'])
def test_invalid_envelopes_fail_before_saving(tmp_path, key, limits):
    path = tmp_path / 'project'
    before = project.start(path, 'Complex case')
    with pytest.raises(ValueError):
        project.configure(path, {key: limits})
    assert project.read(path / 'project.json') == before


def test_progressive_limit_overrides_merge_and_defaults_are_preserved(tmp_path):
    path = tmp_path / 'project'
    project.start(path, 'Complex case')
    project.configure(path, {'limits': {'max_request_bytes': 200000}})
    project.configure(path, {'limits': {'max_output_tokens': 5000}})
    assert project.replay_limits(project.read(path / 'project.json')['decisions']['limits']) == {
        'max_requests': 1, 'max_request_bytes': 200000, 'max_output_tokens': 5000, 'request_timeout_seconds': 60}
    assert project.replay_limits({}) == project.DEFAULT_LIMITS
    assert project.replay_limits({}, optimizer=True) == project.DEFAULT_OPTIMIZER_LIMITS


def grading_config():
    rules = project.quality_checks(deepcopy(QUALITY))
    return {'grading': {i: r for i, r, _, _ in rules},
            'document': {'checks': [{'id': i, 'required': required, 'method': 'deterministic'}
                                    for i, _, _, required in rules]}}


@pytest.mark.parametrize('changes,failed', [
    ({'credit_cents': 999}, {'credit'}),
    ({'credit_cents': True}, {'credit', 'output_schema'}),
    ({'needs_review': 0}, {'review', 'output_schema'}),
    ({'evidence': ['contract-A']}, {'evidence'}),
    ({'evidence': ['contract-A', 'incident-A', 'invented']}, {'evidence'}),
    ({'evidence': ['contract-A', 'incident-A', 'incident-A']}, {'evidence', 'output_schema'}),
    ({'unexpected_field': 'extra'}, {'output_schema'}),
])
def test_required_checks_catch_wrong_amounts_policy_types_and_citations(changes, failed):
    reference = {'credit_cents': 125, 'needs_review': False, 'evidence': ['contract-A', 'incident-A']}
    checks = grade(grading_config(), {'expected': {'answer': reference}}, completed(reference | changes), None)
    assert {key for key, check in checks.items() if not check['pass']} == failed
    assert all(not c.get('error') for c in checks.values())


def test_set_grading_preserves_scalar_types_and_reports_actual_missing_values():
    cfg = {'document': {'checks': [{'id': 'values', 'method': 'deterministic', 'required': True}]},
           'grading': {'values': {'type': 'json_set_equals', 'path': 'values', 'expected_key': 'answer.values'}}}
    checks = grade(cfg, {'expected': {'answer': {'values': [1]}}}, completed({'values': [True]}), None)
    assert not checks['values']['pass']
    assert checks['values']['missing_values'] == [1] and checks['values']['unexpected_values'] == [True]
    checks = grade(cfg, {'expected': {'answer': {'values': ['same', 'same']}}}, completed({'values': ['same']}), None)
    assert checks['values']['failure_category'] == 'grading' and checks['values']['error']


@pytest.mark.parametrize('schema', [
    {'$ref': 'https://example.com/schema.json'},
    {'properties': {'field': {'$dynamicRef': 'file:///tmp/schema.json'}}},
    {'type': 'not-a-type'}, {'$schema': 'https://example.com/custom-draft'}, True,
])
def test_invalid_or_external_schemas_rejected_without_fetch(schema):
    with pytest.raises(ValueError):
        schema_validator(schema)


def test_local_schema_definitions_work_without_network():
    validator = schema_validator({'type': 'object', '$defs': {'amount': {'type': 'integer'}},
                                  'properties': {'amount': {'$ref': '#/$defs/amount'}}})
    assert validator.is_valid({'amount': 42})
    assert not validator.is_valid({'amount': True})


def test_invalid_expected_evidence_rejected_before_run(tmp_path):
    path = draft(tmp_path)
    state = project.read(path / 'project.json')
    cases_path = path / state['import_path'] / 'all-cases.json'
    rows = project.read(cases_path)
    rows[0]['expected']['answer']['evidence'] = ['duplicate', 'duplicate']
    project.save(cases_path, rows)
    with pytest.raises(ValueError, match='Expected answer.*invalid'):
        project.prepare(path)


@pytest.mark.parametrize('role,app_limit,optimizer_limit,band,passes', [
    ('optimizer', 200000, 260000, 240000, False),
    ('optimizer', 200000, 120000, 150000, True),
    ('m2', 200000, 260000, 150000, False),
])
def test_priced_context_band_uses_the_model_roles_actual_envelope(tmp_path, role, app_limit, optimizer_limit, band, passes):
    path = draft(tmp_path)
    project.configure(path, {'limits': {'max_request_bytes': app_limit},
                             'optimizer_limits': {'max_request_bytes': optimizer_limit}})
    prepared = project.prepare(path)
    config_path = path / prepared['config']
    doc = yaml.safe_load(config_path.read_text())
    doc['runner']['routes'][role]['pricing_max_input_tokens'] = band
    config_path.write_text(yaml.safe_dump(doc))
    if passes:
        project.load_with_endpoints(config_path, live=True)
    else:
        with pytest.raises(ValueError, match='request envelope exceeds the priced context band'):
            project.load_with_endpoints(config_path, live=True)


def test_long_provider_timeouts_survive_prepare_and_need_long_enough_worker(tmp_path):
    path = draft(tmp_path)
    result = project.configure(path, {'limits': {'request_timeout_seconds': 300},
                                      'optimizer_limits': {'request_timeout_seconds': 600}})
    assert not result['ready_to_prepare']
    assert any('timeout_seconds must be at least' in message for message in result['missing_decisions'])
    project.configure(path, {'timeout_seconds': 360})
    prepared = project.prepare(path)
    cfg = project.load_with_endpoints(path / prepared['config'], live=True)
    assert cfg['document']['runner']['limits']['request_timeout_seconds'] == 300
    assert cfg['document']['runner']['optimizer_limits']['request_timeout_seconds'] == 600
    assert cfg['document']['execution']['timeout_seconds'] == 360


def test_failed_instruction_slot_change_reports_missing_inputs_instead_of_crashing(tmp_path):
    path = draft(tmp_path)
    result = project.configure(path, {'prompt_location': 'instructions'})
    assert not result['ready_to_prepare']
    assert any('No supported prompt group remains' in message for message in result['missing_decisions'])
