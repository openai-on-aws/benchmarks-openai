"""Long-document optimization must preserve the direction of every failed check.

The business fixture is document-batch processing, independent of the support
credit demonstration. No live services or saved customer/study files are needed.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from customer_eval.feedback import development_feedback, mutation_evidence, budget_mutation_feedback
from customer_eval.scoring import grade
from customer_eval.transport import constrain
from optimizer import build_meta_prompt, make_mutate_fn


PROMPT = (
    'Reconcile the document batch with its signed manifest. Count accepted documents; '
    'do not count quarantined or superseded versions. When the release approval is '
    'missing, request_release_approval and record_processing_decision are both '
    'required. Never publish a batch without approval. Cite each operative manifest '
    'and exception record by its exact source ID. Return only the requested JSON.'
)
ROUTE = {'model_id': 'fixture-optimizer', 'parameters': {'reasoning': {'effort': 'high'}}}
LIMIT = 42000


def _packet(batch, count):
    """Distinct evidence rows, not repeated padding or a truncated real trace."""
    records = [dict(source_id=f'{batch}-DOC-{i:05d}', version=1 + i % 3,
                    received_at=f'2026-11-{1 + i % 20:02d}T{(i // 20) % 24:02d}:{i % 60:02d}:00Z',
                    document_type=('purchase_order', 'shipping_notice', 'invoice')[i % 3],
                    state=('accepted', 'quarantined', 'superseded')[i % 3],
                    file_digest=hashlib.sha256(f'{batch}:{i}'.encode()).hexdigest(),
                    account=f'account-{i % 31}', document_number=f'{batch}/{i:06d}',
                    received_bytes=1024 + i * 37)
               for i in range(count)]
    return json.dumps({'batch': batch, 'manifest': {'source_id': f'{batch}-MANIFEST',
                                                   'signed': True, 'release_approved': False},
                       'documents': records,
                       'final_exception': {'source_id': f'{batch}-EXCEPTION',
                                           'kind': 'approval_missing'}}, ensure_ascii=False)


def _inputs():
    checks = [{'id': key, 'required': True} for key in ('accepted_count', 'release_actions', 'sources')]
    config = {
        'document': {'checks': checks, 'success_criteria': [], 'runner': {}},
        'grading': {
            'accepted_count': {'type': 'json_equals', 'path': 'accepted_count',
                               'expected_key': 'answer.accepted_count'},
            'release_actions': {'type': 'json_set_equals', 'path': 'actions', 'expected_key': 'answer.actions'},
            'sources': {'type': 'json_set_equals', 'path': 'source_ids', 'expected_key': 'answer.source_ids'},
        },
        'datasets': {'development': [], 'validation': [], 'holdout': []},
        'splits': {key: [key] for key in ('development', 'validation', 'holdout')},
        'routes': {'target': {'model_id': 'fixture-target', 'input_usd_per_million': 1,
                               'output_usd_per_million': 1}},
    }
    rows = []
    for index, (case_id, size) in enumerate((('DEV-COUNT', 4), ('DEV-ACTIONS', 550),
                                            ('DEV-SOURCES', 550), ('DEV-PASS', 4))):
        expected = {'accepted_count': (size + 2) // 3,
                    'actions': ['record_processing_decision', 'request_release_approval'],
                    'source_ids': [f'{case_id}-MANIFEST', f'{case_id}-EXCEPTION']}
        actual = deepcopy(expected)
        if index == 0:
            actual['accepted_count'] = 3
        elif index == 1:
            actual['actions'] = ['record_processing_decision', 'publish_batch']
        elif index == 2:
            actual['source_ids'] = [f'{case_id}-MANIFEST', f'{case_id}-OLD-NOTE']
        case = {'id': case_id, 'turns': [_packet(case_id, size)], 'expected': {'answer': expected}}
        result = {'status': 'completed', 'outputs': [json.dumps(actual)], 'latency_ms': 1000,
                  'tool_calls': [], 'route_calls': [{'usage': {'input_tokens': size * 30, 'output_tokens': 80}}]}
        row = {'case_id': case_id, 'split': 'development', 'model_id': 'target', 'repeat': 0,
               'result': result, 'checks': grade(config, case, result, lambda *_: pytest.fail('No judge calls'))}
        config['datasets']['development'].append(case)
        rows.append(row)
    # These are present in the same config, as in a prepared real experiment.
    # They must never be collected into mutation feedback or diagnostics.
    for split in ('validation', 'holdout'):
        config['datasets'][split] = [{'id': f'{split.upper()}_CASE_SECRET',
                                     'turns': [f'{split.upper()}_INPUT_SECRET'],
                                     'expected': {'answer': f'{split.upper()}_REFERENCE_SECRET'}}]
    return config, rows


def _evidence(config, rows):
    current = development_feedback(config, PROMPT, rows)
    return mutation_evidence(PROMPT, {PROMPT: current})


def _render(evidence):
    return build_meta_prompt(PROMPT, ['One or more batch checks failed.'], 1,
                             'Preserve the manifest and release-approval policy.', '', evidence)


def _body(text, limit=1_000_000):
    return constrain({'input': text, 'stream': False}, ROUTE,
                     {'max_requests': 1, 'max_output_tokens': 4096, 'max_request_bytes': limit})


def _fits(limit):
    def check(text):
        try:
            _body(text, limit)
        except ValueError as exc:
            if str(exc).startswith('Request exceeds max_request_bytes'):
                return False
            raise
        return True
    return check


def _diagnostics(evidence):
    return {item['check_id']: item for item in evidence['current']['failure_diagnostics']}


def test_omitted_long_examples_keep_exact_missing_and_unexpected_actions_and_sources():
    config, rows = _inputs()
    original = _evidence(config, rows)
    before = deepcopy(original)
    bounded = budget_mutation_feedback(original, _render, _fits(LIMIT))
    included = set(bounded['context_budget']['included_case_ids'])
    omitted = {item['case_id'] for item in bounded['context_budget']['omitted_examples']}
    assert {'DEV-COUNT', 'DEV-PASS'} <= included
    assert {'DEV-ACTIONS', 'DEV-SOURCES'} <= omitted
    diagnostics = _diagnostics(bounded)
    assert set(diagnostics) == {'accepted_count', 'release_actions', 'sources'}
    action = diagnostics['release_actions']
    assert action['path'] == 'actions'
    assert action['missing_values'] == ['request_release_approval']
    assert action['unexpected_values'] == ['publish_batch']
    assert action['expected'] == ['record_processing_decision', 'request_release_approval']
    assert action['actual'] == ['record_processing_decision', 'publish_batch']
    assert action['actionable'] is True
    sources = diagnostics['sources']
    assert sources['missing_values'] == ['DEV-SOURCES-EXCEPTION']
    assert sources['unexpected_values'] == ['DEV-SOURCES-OLD-NOTE']
    assert sources['actionable'] is True
    assert diagnostics['accepted_count']['expected'] == 2
    assert diagnostics['accepted_count']['actual'] == 3
    assert len(json.dumps(_body(_render(bounded))).encode()) <= LIMIT
    assert original == before  # Evidence saved to the report is never rewritten.
    assert bounded == budget_mutation_feedback(original, _render, _fits(LIMIT))


def test_many_repeats_do_not_hide_a_different_failed_check():
    config, rows = _inputs()
    repeated = []
    for repeat in range(30):
        row = deepcopy(rows[0])
        row['repeat'] = repeat
        repeated.append(row)
    rows = repeated + rows[1:]
    bounded = budget_mutation_feedback(_evidence(config, rows), _render, _fits(LIMIT))
    diagnostics = _diagnostics(bounded)
    assert set(diagnostics) == {'accepted_count', 'release_actions', 'sources'}
    assert diagnostics['accepted_count']['failure_count'] == 30
    assert diagnostics['accepted_count']['distinct_case_count'] == 1
    assert diagnostics['release_actions']['failure_count'] == 1
    assert diagnostics['release_actions']['distinct_case_count'] == 1
    assert diagnostics['release_actions']['missing_values'] == ['request_release_approval']


def test_paid_call_boundary_receives_the_diagnostics_without_other_split_data():
    config, rows = _inputs()
    evidence = _evidence(config, rows)
    requests = []
    mutate = make_mutate_fn(lambda text: requests.append(text) or '["revised batch instructions"]',
                           feedback_provider=lambda _: evidence, request_fits=_fits(LIMIT))
    assert mutate(PROMPT, [], 1) == ['revised batch instructions']
    assert len(requests) == 1
    text = requests[0]
    for canary in ('VALIDATION_CASE_SECRET', 'VALIDATION_INPUT_SECRET', 'VALIDATION_REFERENCE_SECRET',
                   'HOLDOUT_CASE_SECRET', 'HOLDOUT_INPUT_SECRET', 'HOLDOUT_REFERENCE_SECRET'):
        assert canary not in text
    assert 'failure_diagnostics' in text
    assert '"missing_values": ["request_release_approval"]' in text
    assert '"unexpected_values": ["publish_batch"]' in text
    assert '"missing_values": ["DEV-SOURCES-EXCEPTION"]' in text
    assert len(json.dumps(_body(text)).encode()) <= LIMIT


@pytest.mark.parametrize('split', ['validation', 'holdout'])
def test_diagnostic_collection_rejects_an_out_of_split_row_even_with_a_development_id(split):
    config, rows = _inputs()
    rows[1]['split'] = split
    with pytest.raises(ValueError, match='development'):
        _evidence(config, rows)


@pytest.mark.parametrize('split', ['validation', 'holdout'])
def test_diagnostic_collection_rejects_a_non_development_id_labeled_development(split):
    config, rows = _inputs()
    rows[1]['case_id'] = config['datasets'][split][0]['id']
    with pytest.raises(ValueError, match='development'):
        _evidence(config, rows)


def test_grader_and_transport_errors_are_not_presented_as_behavior_repairs():
    config, rows = _inputs()
    rows[1]['checks']['release_actions'] = {'pass': False, 'error': True,
        'failure_category': 'grading', 'reason': 'GRADER_PRIVATE_ERROR_CANARY'}
    rows[2]['result'].update(status='infrastructure_error', route_calls=[])
    diagnostics = _diagnostics(_evidence(config, rows))
    assert set(diagnostics) == {'accepted_count'}
    assert 'GRADER_PRIVATE_ERROR_CANARY' not in json.dumps(diagnostics)


def test_small_boolean_and_numeric_values_retain_their_types():
    config, rows = _inputs()
    config['datasets']['development'][0]['expected']['answer']['accepted_count'] = False
    rows[0]['result']['outputs'] = [json.dumps({'accepted_count': 0,
        'actions': ['record_processing_decision', 'request_release_approval'],
        'source_ids': ['DEV-COUNT-MANIFEST', 'DEV-COUNT-EXCEPTION']})]
    rows[0]['checks'] = grade(config, config['datasets']['development'][0], rows[0]['result'],
                              lambda *_: pytest.fail('No judge calls'))
    scalar = _diagnostics(_evidence(config, rows))['accepted_count']
    assert scalar['expected'] is False
    assert type(scalar['actual']) is int and scalar['actual'] == 0
    assert scalar['differences'][0]['issue'] == 'wrong_type'


def test_oversized_expected_value_is_explicit_and_cannot_expand_feedback_unboundedly():
    config, rows = _inputs()
    enormous = 'release-event-📋-' * 20000
    case = config['datasets']['development'][1]
    case['expected']['answer']['actions'] = [enormous]
    rows[1]['checks'] = grade(config, case, rows[1]['result'], lambda *_: pytest.fail('No judge calls'))
    diagnostic = _diagnostics(_evidence(config, rows))['release_actions']
    serialized = json.dumps(diagnostic)
    assert len(serialized.encode()) < 16000
    assert enormous not in serialized
    assert '"omitted": true' in serialized and 'json_bytes' in serialized and 'sha256' in serialized
    assert 'reason' in serialized and 'value_type' in serialized


def test_generic_only_failure_cannot_silently_replace_an_omitted_actionable_check():
    config, rows = _inputs()
    # An older/custom grader may give no structured detail. A short complete
    # failure for another check must not make dropping this one seem safe.
    del config['grading']['release_actions']
    rows[1]['checks']['release_actions'] = {'pass': False, 'reason': 'Criterion failed',
                                          'failure_category': 'behavior'}
    evidence = _evidence(config, rows)
    called = []
    mutate = make_mutate_fn(lambda text: called.append(text) or '["candidate"]',
                           feedback_provider=lambda _: evidence, request_fits=_fits(LIMIT))
    with pytest.raises(ValueError, match='(?i)diagnostic|actionable|complete.*fail'):
        mutate(PROMPT, [], 1)
    assert called == []


def test_unfit_instructions_and_required_diagnostics_stop_before_any_model_call():
    config, rows = _inputs()
    evidence = _evidence(config, rows)
    called = []
    mutate = make_mutate_fn(lambda text: called.append(text) or '["candidate"]',
                           feedback_provider=lambda _: evidence, request_fits=_fits(2000))
    with pytest.raises(ValueError, match='(?i)exceed|fit|envelope'):
        mutate(PROMPT, [], 1)
    assert called == []
