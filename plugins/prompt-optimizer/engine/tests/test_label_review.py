from copy import deepcopy
import json

import pytest

from customer_eval.label_review import (
    apply_review, dataset_label_status, review_template, write_review_template,
)


@pytest.fixture
def examples():
    return [{'id': 'invoice-' + str(i), 'turns': ['Invoice ' + str(i)],
             'expected': {'answer': {'total': i}, 'reference': json.dumps({'total': i})},
             'replay': {'request': {'input': 'Invoice ' + str(i)}},
             'metadata': {'label_status': 'unreviewed', 'provenance': 'synthetic'}}
            for i in range(3)]


def human_review(examples):
    value = review_template(examples)
    value.update(reviewer={'name': 'Human tester', 'type': 'human'},
                 reviewed_at='2026-11-22T12:00:00-08:00', attested=True)
    for item in value['reviews']:
        item['outcome'] = 'accepted'
    return value


def test_template_never_claims_human_review_and_does_not_overwrite(tmp_path, examples):
    template = review_template(examples)
    assert template['attested'] is False
    assert template['reviewer'] == {'name': None, 'type': None}
    assert {item['outcome'] for item in template['reviews']} == {'pending'}
    target = write_review_template(examples, tmp_path / 'review.json')
    assert json.loads(target.read_text()) == template
    with pytest.raises(FileExistsError):
        write_review_template(examples, target)


def test_complete_review_is_bound_to_frozen_input_and_expected_hashes(examples):
    original = deepcopy(examples)
    review = human_review(examples)
    cases, summary = apply_review(examples, review)
    assert dataset_label_status(cases) == summary['label_status'] == 'human_reviewed'
    assert not summary['issues']
    assert summary['reviewed_case_ids'] == ['invoice-0', 'invoice-1', 'invoice-2']
    evidence = cases[0]['metadata']['label_review']
    assert evidence['reviewer'] == 'Human tester'
    assert evidence['reviewed_at'] == review['reviewed_at']
    assert evidence['dataset'] == review['dataset']
    assert evidence['expected_sha256'] == review['reviews'][0]['expected_sha256']
    assert examples == original


def test_partial_review_never_promotes_unlisted_or_pending_cases(examples):
    review = human_review(examples)
    review['reviews'][1]['outcome'] = 'pending'
    review['reviews'].pop()
    cases, summary = apply_review(examples, review)
    assert summary['reviewed_case_ids'] == ['invoice-0']
    assert summary['unreviewed_case_ids'] == ['invoice-1', 'invoice-2']
    assert not summary['issues']
    assert dataset_label_status(cases) == 'unreviewed'
    assert dataset_label_status(cases[:1]) == 'human_reviewed'
    assert dataset_label_status([]) == 'unreviewed'


@pytest.mark.parametrize('change', ['expected', 'input', 'case_selection'])
def test_changed_evidence_invalidates_prior_dataset_attestation(examples, change):
    review = human_review(examples)
    if change == 'expected':
        examples[0]['expected']['answer']['total'] = 999
    elif change == 'input':
        examples[0]['replay']['request']['input'] = 'A different invoice'
    else:
        examples.append(dict(deepcopy(examples[-1]), id='invoice-new'))
    cases, summary = apply_review(examples, review)
    assert summary['issues']
    assert not summary['reviewed_case_ids']
    assert all(c['metadata']['label_status'] == 'unreviewed' for c in cases)


def test_tampered_per_case_hash_is_rejected_even_with_correct_dataset_hash(examples):
    review = human_review(examples)
    review['reviews'][0]['expected_sha256'] = '0' * 64
    cases, summary = apply_review(examples, review)
    assert cases[0]['metadata']['label_status'] == 'unreviewed'
    assert 'invoice-0' in summary['issues'][0]
    assert summary['label_status'] == 'unreviewed'


@pytest.mark.parametrize('kind', ['automated', 'simulated', None])
def test_nonhuman_review_does_not_promote_synthetic_labels(examples, kind):
    review = human_review(examples)
    review['reviewer']['type'] = kind
    cases, summary = apply_review(examples, review)
    assert not summary['reviewed_case_ids']
    assert summary['issues']
    assert all(c['metadata']['label_status'] == 'unreviewed' for c in cases)


def test_accepted_outcomes_without_human_attestation_remain_unreviewed(examples):
    review = human_review(examples)
    review['attested'] = False
    _, summary = apply_review(examples, review)
    assert not summary['reviewed_case_ids'] and summary['issues']


def test_rejected_reference_requires_repair_and_stays_unreviewed(examples):
    review = human_review(examples)
    review['reviews'][1].update(outcome='rejected', notes='Tax omitted from the reference')
    cases, summary = apply_review(examples, review)
    assert summary['rejected_case_ids'] == ['invoice-1']
    assert summary['issues']
    assert cases[1]['metadata']['label_status'] == 'unreviewed'
    assert cases[1]['metadata']['label_review']['outcome'] == 'rejected'


@pytest.mark.parametrize('problem', ['unknown', 'duplicate', 'missing_reviewer', 'naive_date'])
def test_malformed_reviews_fail_before_preparation(examples, problem):
    review = human_review(examples)
    if problem == 'unknown':
        review['reviews'][0]['case_id'] = 'not-selected'
    elif problem == 'duplicate':
        review['reviews'].append(deepcopy(review['reviews'][0]))
    elif problem == 'missing_reviewer':
        review['reviewer']['name'] = None
    else:
        review['reviewed_at'] = '2026-11-22T12:00:00'
    with pytest.raises(ValueError):
        apply_review(examples, review)


def test_review_file_and_order_independent_dataset_identity(tmp_path, examples):
    review = human_review(examples)
    path = tmp_path / 'human-review.json'
    path.write_text(json.dumps(review))
    cases, summary = apply_review(list(reversed(examples)), path)
    assert not summary['issues']
    assert dataset_label_status(cases) == 'human_reviewed'


def test_removed_review_clears_prior_status_and_provenance(examples):
    reviewed, _ = apply_review(examples, human_review(examples))
    cases, summary = apply_review(reviewed)
    assert summary['reviewed_case_ids'] == []
    assert all(c['metadata']['label_status'] == 'unreviewed' for c in cases)
    assert all('label_review' not in c['metadata'] for c in cases)


def test_duplicate_json_fields_fail_closed(tmp_path, examples):
    path = tmp_path / 'bad-review.json'
    path.write_text('{"version":1,"version":2}')
    with pytest.raises(ValueError, match='Duplicate'):
        apply_review(examples, path)


def test_report_recognizes_schema_status_and_shows_partial_review_counts(tmp_path):
    from customer_eval.contract import ROOT, load
    from customer_eval.decision_report import build
    from customer_eval.runner import Runner

    config = load(ROOT / 'examples/log-replay/input.yaml')
    for dataset in config['document']['datasets']:
        dataset['label_status'] = 'human_reviewed'
    all_cases = [case for members in config['datasets'].values() for case in members]
    for case in all_cases:
        case['metadata']['label_status'] = 'human_reviewed'
        case['metadata']['reference_kind'] = 'supplied_expected'
    runner = Runner(config, tmp_path / 'simulation', backend='simulation')
    try:
        report = runner.run(optimize=False)
    finally:
        runner.close()
    decision = build(config, report)
    assert decision['quality_label'] == 'Required quality checks passed'
    assert decision['label_review_scope']['human_reviewed_cases'] == len(all_cases)
    config['document']['datasets'][0]['label_status'] = 'unreviewed'
    all_cases[0]['metadata']['label_status'] = 'unreviewed'
    decision = build(config, report)
    assert 'labels unreviewed' in decision['quality_label']
    assert decision['label_review_scope']['unreviewed_cases'] == 1
    assert any('remaining labels are unreviewed' in reason for reason in decision['reasons'])


def test_input_preview_includes_answers_and_actual_review_provenance(tmp_path):
    from customer_eval.input_preview import write_preview
    from customer_eval.log_import import import_logs

    path = tmp_path / 'cases.json'
    path.write_text(json.dumps([{'id': 'one', 'prompt': 'Extract invoice total',
        'input': 'Total 10', 'expected': {'total': 10}}]))
    cases, report = import_logs([path])
    reviewed, _ = apply_review(cases, human_review(cases))
    preview = write_preview(tmp_path, reviewed, report)
    item = preview['cases'][0]
    assert item['expected']['answer'] == {'total': 10}
    assert item['label_review']['reviewer'] == 'Human tester'
    assert preview['label_review_scope'] == {'human_reviewed': 1}


@pytest.fixture
def review_project(tmp_path):
    """Drive public intake operations; no model requests or AWS resources."""
    import yaml
    from customer_eval import project
    from customer_eval.contract import ROOT

    logs = tmp_path / 'manual-invoices.jsonl'
    logs.write_text('\n'.join(json.dumps({
        'id': f'invoice-{i}', 'prompt': 'Extract the invoice total and currency as JSON.',
        'input': f'Invoice {i}: total USD {i + 1}',
        'expected': {'total': i + 1, 'currency': 'USD'},
    }) for i in range(6)))
    sample = yaml.safe_load((ROOT / 'examples/log-replay/input.yaml').read_text())
    connection = tmp_path / 'connection.json'
    project.save(connection, {
        'version': 1,
        'models': {model['id']: {
            'model': model, 'route': sample['runner']['routes'][model['id']],
            'base_url': 'https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1',
        } for model in sample['models']},
        'cloud': {'runtime_arn': 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/test',
                  'profile': 'test', 'region': 'us-east-1', 'account': '123456789012', 'bucket': 'test'},
    })
    directory = tmp_path / 'project'
    project.start(directory, 'Compare invoice extraction on two models')
    project.inspect(directory, [{'path': str(logs)}])
    status = project.configure(directory, {
        'owner': 'Unit test', 'source_model': 'm1', 'target_model': 'm2',
        'connection': str(connection), 'prompt_location': 0, 'provenance': 'synthetic',
        'budget_policy': 'unlimited', 'optimization_enabled': False, 'repetitions': 1,
        'quality': {'kind': 'json_equals', 'reference_source': 'supplied', 'fields': ['']},
    })
    assert status['ready_to_prepare']
    return directory


def test_project_partial_review_survives_preparation_and_results(review_project, tmp_path):
    from customer_eval import project
    from customer_eval.decision_report import build
    from customer_eval.runner import Runner

    cases, _, _ = project.prepare_cases(review_project, apply_reviews=False)
    review = human_review(cases)
    review['reviews'] = review['reviews'][:2]
    review_path = tmp_path / 'partial-review.json'
    project.save(review_path, review)
    assert project.configure(review_project, {'label_review_file': str(review_path)})['ready_to_prepare']
    prepared = project.prepare(review_project)
    config = project.load_with_endpoints(review_project / prepared['config'])
    assert config['intake_context']['reviewed_cases'] == 2
    assert config['intake_context']['label_status'] == 'unreviewed'
    assert project.read(review_project / 'prepared/label-review.json') == review
    by_id = {case['id']: case for rows in config['datasets'].values() for case in rows}
    reviewed_ids = {item['case_id'] for item in review['reviews']}
    assert {ident for ident, case in by_id.items()
            if case['metadata']['label_status'] == 'human_reviewed'} == reviewed_ids
    for dataset in config['document']['datasets']:
        assert dataset['label_status'] == dataset_label_status(config['datasets'][dataset['id']])
    preview = project.read(review_project / 'input-preview.json')
    assert preview['label_review_scope'] == {'human_reviewed': 2, 'unreviewed': 4}
    runner = Runner(config, tmp_path / 'result', backend='simulation')
    try:
        report = runner.run(optimize=False)
    finally:
        runner.close()
    decision = build(config, report)
    assert decision['label_review_scope'] == {
        'total_cases': 6, 'human_reviewed_cases': 2, 'unreviewed_cases': 4, 'rejected_case_ids': [],
    }
    assert 'labels unreviewed' in decision['quality_label']


@pytest.mark.parametrize('change', ['reference_contents', 'quality_reference_file'])
def test_project_stale_review_blocks_readiness_and_prepare_until_explicitly_removed(
        review_project, tmp_path, change):
    from customer_eval import project

    cases, _, _ = project.prepare_cases(review_project, apply_reviews=False)
    refs = {case['id']: case['expected']['answer'] for case in cases}
    original_refs = tmp_path / 'expected.json'
    project.save(original_refs, refs)
    project.configure(review_project, {'quality': {'reference_file': str(original_refs)}})
    finalized, _, _ = project.prepare_cases(review_project, apply_reviews=False)
    review = human_review(finalized)
    review_path = tmp_path / 'review.json'
    project.save(review_path, review)
    assert project.configure(review_project, {'label_review_file': str(review_path)})['ready_to_prepare']

    refs['invoice-0'] = {'total': 999, 'currency': 'USD'}
    if change == 'reference_contents':
        project.save(original_refs, refs)
    else:
        new_refs = tmp_path / 'revised-expected.json'
        project.save(new_refs, refs)
        project.configure(review_project, {'quality': {'reference_file': str(new_refs)}})

    status = project.status(review_project)
    assert not status['ready_to_prepare']
    assert any('Label review needs attention' in issue and 'stale' in issue
               for issue in status['missing_decisions'])
    blocked = project.prepare(review_project)
    assert not blocked['ready_to_prepare'] and blocked['prepared'] is None
    assert not (review_project / 'prepared').exists()

    # Removing the attestation is an explicit decision, not an implicit fallback.
    assert project.configure(review_project, {'label_review_file': None})['ready_to_prepare']
    prepared = project.prepare(review_project)
    config = project.load_with_endpoints(review_project / prepared['config'])
    assert config['intake_context']['reviewed_cases'] == 0
    assert all(dataset['label_status'] == 'unreviewed' for dataset in config['document']['datasets'])
    assert all(case['metadata']['label_status'] == 'unreviewed'
               and 'label_review' not in case['metadata']
               for rows in config['datasets'].values() for case in rows)
    assert not (review_project / 'prepared/label-review.json').exists()
