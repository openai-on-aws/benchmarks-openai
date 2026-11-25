"""Mixed reference/source reports describe frozen evidence without changing scores."""
from copy import deepcopy
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path

import pytest

from customer_eval.decision_report import build, write
from customer_eval.input_preview import write_preview
from customer_eval.runner import Runner
from customer_eval.scoring import summarize
from test_decision_cost_display import report_inputs
from test_reference_source_grading import ANSWER, mixed_project
from test_source_judge_intake import setup_project
from customer_eval import project


def mixed_report(config, report, *, required_pass=True, optional_pass=False, judge_error=False):
    config['document']['runner']['grading_reference_mode'] = 'references_with_source_judges'
    config['document']['checks'].append({'id': 'explanation', 'required': False, 'method': 'llm_judge', 'judge_model_id': 'judge'})
    config['grading'] = {'answer': {'type': 'json_equals'}, 'explanation': {'type': 'llm_source_rubric'}}
    for dataset in config['document']['datasets']:
        dataset['label_status'] = 'unreviewed'
    for cases in config['datasets'].values():
        for case in cases:
            case['expected'] = {'answer': 'reference'}
            case['metadata'] = {'reference_kind': 'supplied_expected', 'label_status': 'unreviewed'}
    for group in report['holdout'].values():
        for row in group['trials']:
            row['checks']['answer']['pass'] = required_pass
            row['checks']['explanation'] = {'pass': optional_pass, 'reason': 'Optional explanation assessment'}
            if judge_error:
                row['checks']['explanation']['error'] = 'Judge response could not be parsed'
        group['score'] = asdict(summarize(config, group['score']['prompt'], group['trials']))
    return config, report


@pytest.mark.parametrize('required_pass,optional_pass,judge_error', [(True, False, False), (False, True, False), (True, False, True)])
def test_mixed_reports_preserve_required_quality_and_reference_review(report_inputs, tmp_path, required_pass, optional_pass, judge_error):
    config, report = mixed_report(*report_inputs, required_pass=required_pass, optional_pass=optional_pass, judge_error=judge_error)
    before = deepcopy((config, report))
    decision = write(config, report, tmp_path)
    assert (config, report) == before
    assert decision['quality_label'] == 'Reference checks passed (labels unreviewed)'
    assert 'Expected answers have not been independently reviewed' in ' '.join(decision['reasons'])
    assert 'Optional LLM assessments' in ' '.join(decision['reasons'])
    assert 'No expected answers' not in json.dumps(decision)
    assert [row['check_pass_rate'] for row in decision['holdout']] == [float(required_pass)] * 3
    assert [row['passed_trials'] for row in decision['holdout']] == [20 if required_pass else 0] * 3
    for row in decision['holdout']:
        assert sum(row['required_failures_by_category'].values()) == (0 if required_pass else 20)
        assert sum(row['optional_failures_by_category'].values()) == (0 if optional_pass else 20)
        assert sum(row['failures_by_category'].values()) == sum(row['required_failures_by_category'].values()) + sum(row['optional_failures_by_category'].values())
    assert decision['model_comparison']['source_original'] == report['holdout']['source_original']['score']
    assert decision['prompt_comparison']['target_selected'] == report['holdout']['target_selected']['score']
    assert decision['prompt_comparison']['reason'] == report['selected_prompt']['reason']
    for filename in ('decision.json', 'decision.md', 'decision.html'):
        text = (tmp_path / filename).read_text()
        assert decision['quality_label'] in text
        assert 'No expected answers' not in text
    for filename in ('decision.md', 'decision.html'):
        text = (tmp_path / filename).read_text()
        assert ('Optional check:' if required_pass else 'Required check:') in text
        if required_pass:
            assert '0 failed required checks; 60 optional checks needing review' in text
            assert 'No failed required checks were observed' in text
    if judge_error:
        assert decision['holdout'][0]['optional_failures_by_category'] == {'grading': 20}


def test_optional_concerns_cannot_hide_required_failure_examples(report_inputs):
    config, report = mixed_report(*report_inputs, required_pass=True)
    # Forty optional failures precede this required failure in traversal order.
    row = report['holdout']['target_selected']['trials'][-1]
    row['checks']['answer']['pass'] = False
    decision = build(config, report)
    assert len(decision['failure_examples']) == 24
    assert decision['failure_examples'][0]['required'] is True
    assert decision['failure_examples'][0]['check'] == 'answer'
    assert all(not item['required'] for item in decision['failure_examples'][1:])


def test_recorded_mixed_references_stay_agreement_not_correctness(report_inputs):
    config, report = mixed_report(*report_inputs)
    config['datasets']['heldout'][0]['metadata']['reference_kind'] = 'recorded_response'
    decision = build(config, report)
    assert decision['quality_label'] == 'Agreement with recorded outputs'
    assert 'Matching previous model responses does not establish correctness.' in decision['reasons']
    assert 'Expected answers have not been independently reviewed' in ' '.join(decision['reasons'])


def test_source_only_report_retains_no_reference_wording(report_inputs):
    config, report = report_inputs
    config['grading'] = {'answer': {'type': 'llm_source_rubric'}}
    for dataset in config['document']['datasets']:
        dataset['label_status'] = 'not_provided'
    decision = build(config, report)
    assert decision['quality_label'] == 'LLM-assessed quality against source inputs'
    assert 'No expected answers were supplied' in ' '.join(decision['reasons'])
    assert 'Expected answers have not been independently reviewed' not in ' '.join(decision['reasons'])
    assert 'reference and failure review' not in decision['recommendation']


def test_reference_only_report_retains_reference_wording(report_inputs):
    config, report = report_inputs
    decision = build(config, report)
    assert decision['quality_label'] == 'Reference checks passed (labels unreviewed)'
    assert 'Expected answers have not been independently reviewed' in ' '.join(decision['reasons'])
    assert 'No expected answers were supplied' not in ' '.join(decision['reasons'])


def test_mixed_runner_report_uses_reference_scope_without_model_calls(tmp_path, monkeypatch):
    _, config = mixed_project(tmp_path)
    config['document']['execution'].update(repetitions=1, max_cases_per_dataset=1, concurrency=1)
    runner = Runner(config, tmp_path / 'offline-run')
    seen = []
    actual = deepcopy(ANSWER); actual['explanation'] = 'Explanation under assessment.'
    def execute(payload):
        return {'trial_id': payload['trial_id'], 'status': 'completed', 'outputs': [json.dumps(actual)],
                'tool_calls': [], 'route_calls': [], 'latency_ms': 1000}
    def judge(model, text, *args):
        seen.append(text)
        payload = json.loads(text.split('\n', 1)[1]); source = payload['sources'][-1]
        return json.dumps({'checks': {key: {'status': 'fail', 'reason': 'Optional explanation omits context.',
            'evidence': [{'source_id': source['source_id'], 'quote': source['text']}]}
            for key in payload['criteria']}})
    monkeypatch.setattr(runner, 'execute', execute)
    monkeypatch.setattr(runner, 'call', judge)
    try:
        report = runner.run(optimize=False)
    finally:
        runner.close()
    text = (tmp_path / 'offline-run/report.md').read_text()
    assert 'required reference checks' in text and 'Optional source-based assessments' in text
    assert 'no expected answers' not in text
    assert seen and all('REFERENCE_SENTINEL' not in text for text in seen)
    assert all(group['score']['accuracy'] == 1 for group in report['holdout'].values())
    assert all(group['source_judge_metrics']['by_check']['explanation']['failed'] > 0 for group in report['holdout'].values())


@pytest.mark.parametrize('mode', ['mixed', 'source', 'reference'])
def test_plan_labels_assessment_mode_and_makes_no_calls(tmp_path, monkeypatch, capsys, mode):
    if mode == 'source':
        directory, _ = setup_project(tmp_path)
        prepared = project.prepare(directory)
        config = project.load_with_endpoints(directory / prepared['config'])
    else:
        directory, config = mixed_project(tmp_path)
        if mode == 'reference':
            config['document']['runner'].pop('grading_reference_mode')
            config['grading'].pop('explanation')
            config['document']['checks'] = [c for c in config['document']['checks'] if c['id'] != 'explanation']
    path = Path(__file__).resolve().parents[2] / 'scripts/workflow_eval.py'
    spec = importlib.util.spec_from_file_location('reporting_workflow_cli', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'prepared_config', lambda *_: config)
    assert module.main(['plan', '--project', str(directory)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['judging']['method'] == {'mixed': 'reference_and_source', 'source': 'source_judge', 'reference': 'configured_checks'}[mode]
    assert plan['calls_made'] == 0
    assert plan['judging']['models'] == ([] if mode == 'reference' else ['judge'])
    assert ('No LLM source-judge calls' in plan['judging']['call_note']) == (mode == 'reference')


@pytest.mark.parametrize('mode', ['mixed', 'source', 'reference'])
def test_preview_discloses_judge_flow_without_discarding_reference_flow(tmp_path, mode):
    if mode == 'source':
        directory, _ = setup_project(tmp_path)
        prepared = project.prepare(directory)
        config = project.load_with_endpoints(directory / prepared['config'])
    else:
        directory, config = mixed_project(tmp_path)
        if mode == 'reference':
            config['document']['runner'].pop('grading_reference_mode')
            config['grading'].pop('explanation')
    cases = config['datasets']['holdout']
    coverage = {'found': len(cases), 'accepted': len(cases), 'dropped': 0, 'reconciles': True, 'excluded_records': []}
    before = deepcopy((config, cases))
    preview = write_preview(directory, cases, coverage, config=config)
    assert (config, cases) == before
    for filename in ('input-preview.md', 'input-preview.html', 'input-preview.json'):
        text = (directory / filename).read_text()
        assert ('separately selected judge receives' in text) == (mode != 'reference')
        assert ('no expected answers are supplied' in text) == (mode == 'source')
        if mode != 'reference':
            assert 'Expected answers and the editable prompt are excluded from the judge request' in text
        if mode != 'source':
            assert 'grading references and configuration' in text
    assert all(('expected' in case) == (mode != 'source') for case in preview['cases'])
