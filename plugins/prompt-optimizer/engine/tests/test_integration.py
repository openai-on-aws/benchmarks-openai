import copy
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from customer_eval.contract import ROOT, load, trial_payload
from customer_eval.runner import BudgetExceeded, InterruptedAttempt, Runner, Store
from customer_eval.scoring import grade, summarize
from customer_eval.transport import constrain
from hillclimb import CandidateScore, run_hill_climb
from optimizer import _parse_candidates, build_meta_prompt


@pytest.fixture
def config(): return load(ROOT / 'examples/customer-support/input.yaml')


def test_complete_workflow_and_resume(config, tmp_path):
    runner = Runner(config, tmp_path)
    first = runner.run()
    assert first['simulated'] is True
    assert first['holdout']['target_original']['score']['accuracy'] == .5
    assert first['holdout']['target_selected']['score']['accuracy'] == 1
    runner.execute = lambda _: pytest.fail('Completed trials must be reused')
    assert runner.run() == first
    assert first['actual_total_cost_usd'] is None
    assert first['evaluation_status'] == 'complete'


@pytest.mark.parametrize('missing_receipt', [True, False])
def test_report_exposes_grading_failure_and_preserves_unfinished_attempt(config, tmp_path, missing_receipt):
    from customer_eval.contract import digest
    baseline = Runner(config, tmp_path/'baseline').run(optimize=False)
    runner = Runner(config, tmp_path/'failed-grading')
    evaluate_group = runner.evaluate_group
    injected = False
    def evaluate(*args, **kwargs):
        nonlocal injected
        groups = evaluate_group(*args, **kwargs)
        if not injected:
            injected = True
            row = groups[0][0]
            check = row['checks'][next(iter(row['checks']))]
            check.update({'pass':False, 'error':'HTTP 503' if missing_receipt else 'Invalid judge JSON'})
            runner.store.db.execute('UPDATE attempts SET value=? WHERE id=?',
                                    (json.dumps(row['checks']), digest(['grade',row['id']])))
            if missing_receipt:
                runner.store.db.execute('INSERT INTO attempts VALUES (?,?,?,NULL)',
                                        ('failed-judge-request','started',.01))
            runner.store.db.commit()
        return groups
    runner.evaluate_group = evaluate
    report = runner.run(optimize=False)
    assert report['evaluation_status'] == 'needs_review'
    assert report['grading_error_records'] == 1
    assert len(report['unresolved_attempts']) == int(missing_receipt)
    assert report['model_costs']['unpriced_requests_or_incomplete_traces'] == (
        baseline['model_costs']['unpriced_requests_or_incomplete_traces'] + int(missing_receipt))
    assert 'Evaluation needs review' in (tmp_path/'failed-grading/report.md').read_text()
    if missing_receipt:
        assert runner.store.db.execute('SELECT state,value FROM attempts WHERE id="failed-judge-request"').fetchone() == ('started',None)


@pytest.mark.parametrize('name', ['customer-support', 'travel'])
def test_examples(name, tmp_path):
    c = load(ROOT / f'examples/{name}/input.yaml')
    assert Runner(c, tmp_path).run()['simulated']


def test_labels_do_not_reach_agent(config):
    row = copy.deepcopy(config['datasets']['holdout'][0])
    row['expected']['secret'] = 'HOLDOUT_LABEL_SENTINEL'
    payload = trial_payload(config, config['target'], config['prompt'], row, 'test')
    assert 'HOLDOUT_LABEL_SENTINEL' not in json.dumps(payload)
    assert 'grading' not in payload and 'expected' not in payload


def test_only_development_feedback_reaches_mutation(config, tmp_path, monkeypatch):
    from customer_eval import simulation
    original = simulation.mutate
    seen = []
    def mutate(prompt, feedback, k):
        seen.extend(feedback)
        return original(prompt, feedback, k)
    monkeypatch.setattr(simulation, 'mutate', mutate)
    Runner(config, tmp_path).run()
    assert seen and all('development_' in reason for reason in seen)
    assert not any('validation_' in reason or 'holdout_' in reason for reason in seen)


@pytest.mark.parametrize('value', ['[123]', '[{}]', 'not json', '[""]', '["same","same"]'])
def test_malformed_mutations_fail(value):
    with pytest.raises(ValueError): _parse_candidates(value, 2)


def test_meta_prompt_is_customer_specific():
    text = build_meta_prompt('seed', ['bad return'], 1, 'Return-policy assistant', 'Never invent exceptions')
    assert 'travel-booking' not in text and 'Return-policy assistant' in text


def test_policy_gate_beats_accuracy():
    baseline = CandidateScore('seed', .5)
    bad = CandidateScore('bad', 1, eligible=False, rejection_reasons=['policy'])
    result = run_hill_climb(seed_prompt='seed', mutate_fn=lambda *_:['bad'],
                           fanout_score_fn=lambda p: baseline if p == 'seed' else bad,
                           candidates_per_generation=1, max_generations=1, min_accuracy_gain_to_continue=.01)
    assert result.best == baseline


def test_budget_reservations_are_atomic(tmp_path):
    store = Store(tmp_path, 'id', 1)
    def run(i):
        try: return store.perform(str(i), .6, lambda: {'ok':True})
        except BudgetExceeded: return 'blocked'
    with ThreadPoolExecutor(2) as pool: results = list(pool.map(run, [1,2]))
    assert results.count('blocked') == 1
    assert store.reserved() == .6


def test_uncertain_charge_is_not_retried(tmp_path):
    store = Store(tmp_path, 'id', 1)
    def crash(): raise RuntimeError('lost response after dispatch')
    with pytest.raises(RuntimeError): store.perform('x', .6, crash)
    with pytest.raises(InterruptedAttempt): store.perform('x', .6, lambda: {})
    assert store.reserved() == .6


def test_resume_rejects_different_assets(config, tmp_path):
    Runner(config, tmp_path)
    config['fingerprint'] = 'changed'
    with pytest.raises(ValueError): Runner(config, tmp_path)


def test_failed_trial_stays_in_denominator(config):
    case = config['datasets']['development'][0]
    result = {'status':'timeout','outputs':[]}
    checks = grade(config, case, result, lambda *_:pytest.fail('No judging on failed run'))
    score = summarize(config, 'seed', [{'result':result,'checks':checks}])
    assert score.accuracy == 0 and score.n_sessions == 1 and not score.eligible


def test_boolean_is_not_equal_to_numeric_label(config):
    config['grading']['correctness'] = {'type':'json_equals','path':'x','expected_key':'x'}
    assert not grade(config, {'expected':{'x':1}}, {'status':'completed','outputs':['{"x":true}']}, None)['correctness']['pass']


def test_empty_run_cannot_win(config):
    with pytest.raises(ValueError): summarize(config, 'seed', [])


def test_malformed_judge_is_failure(config):
    config['document']['checks'][0].update(method='llm_judge',judge_model_id='optimizer')
    config['grading']['correctness'] = {'type':'llm_rubric','rubric':'Correct decision','expected_key':'decision'}
    c = grade(config, {'expected':{'decision':'allow'},'turns':['Q']},
              {'status':'completed','outputs':['answer']},lambda *_:'{"pass":"yes","reason":"good"}')
    assert c['correctness']['error'] and not c['correctness']['pass']


@pytest.mark.parametrize('payload', [
    {'previous_response_id':'hidden-history'}, {'background':True},
    {'tools':[{'type':'web_search'}]}, {'input':[{'type':'input_image','image_url':'https://example.com'}]},
])
def test_unbounded_requests_rejected(config, payload):
    with pytest.raises(ValueError): constrain(payload, config['routes']['m1'], config['document']['runner']['limits'])


def test_request_pins_model_and_output_limit(config):
    result = constrain({'input':'hello','model':'wrong','max_output_tokens':999999},config['routes']['m1'],config['document']['runner']['limits'])
    assert result['model'] == config['routes']['m1']['model_id']
    assert result['max_output_tokens'] == 1024 and result['store'] is False


def test_wrong_remote_trial_id_is_failure(config, tmp_path):
    runner = Runner(config,tmp_path)
    runner.execute = lambda _: {'trial_id':'wrong','status':'completed','outputs':['{}']}
    rows = runner.evaluate(config['target'],config['prompt'],'development')
    assert all(row['result']['status']=='infrastructure_error' for row in rows)


def test_duplicate_input_across_splits(tmp_path):
    shutil.copytree(ROOT / 'examples/customer-support',tmp_path/'case')
    base=tmp_path/'case'
    dev=json.loads((base/'data/development.jsonl').read_text().splitlines()[0])
    dev['id']='different-id'
    (base/'data/holdout.jsonl').write_text(json.dumps(dev)+'\n')
    with pytest.raises(ValueError): load(base/'input.yaml')


def test_live_rejects_illustrative_prices():
    with pytest.raises(ValueError,match='illustrative prices'):
        load(ROOT/'examples/customer-support/input.yaml',live=True)


def test_cannot_change_workflow_after_viewing_holdout(config,tmp_path):
    runner=Runner(config,tmp_path)
    runner.run(optimize=False)
    with pytest.raises(ValueError,match='workflow mode'):runner.run(optimize=True)
