"""Interrupted derived grades recover without silently replaying paid requests."""
import json

import pytest

from customer_eval import runner as runner_module
from customer_eval.contract import digest, load
from customer_eval.runner import Runner, BudgetExceeded, InterruptedAttempt
from customer_eval.scoring import grade
from runtime.coordinator import CoordinatorPaused
from test_source_grading import contract_fixture, fixture


@pytest.mark.parametrize('error', [CoordinatorPaused, BudgetExceeded, InterruptedAttempt])
def test_source_grading_propagates_dispatch_control_errors(error):
    cfg, case, result, _ = fixture()
    def stopped(*_): raise error('intentional dispatch stop')
    with pytest.raises(error): grade(cfg, case, result, stopped)


def configured(tmp_path):
    path, _ = contract_fixture(tmp_path)
    cfg = load(path)
    cfg['document']['execution'].update(repetitions=1, max_cases_per_dataset=1, concurrency=1)
    cfg.pop('fingerprint')
    cfg['fingerprint'] = digest(cfg)
    return cfg


def valid_judge_reply(text):
    payload = json.loads(text.split('\n', 1)[1])
    source = payload['sources'][-1]
    return json.dumps({'checks': {key: {'status': 'pass', 'reason': 'Value is supported by the source.',
                                      'evidence': [{'source_id': source['source_id'], 'quote': source['text']}]}
                                 for key in payload['criteria']}})


def install_completion(monkeypatch, *, error=None):
    calls = []
    def complete(route, text, limits, *, receipt):
        calls.append(text)
        if error: raise error('Unknown or stopped judge attempt')
        receipt.update(status='completed', model_requested='fixture', model_returned='fixture')
        return valid_judge_reply(text), {'input_tokens': 100, 'output_tokens': 50}
    monkeypatch.setattr(runner_module, 'complete', complete)
    return calls


def completed_application(payload):
    return {'trial_id': payload['trial_id'], 'status': 'completed', 'outputs': ['{"total":125}'],
            'tool_calls': [], 'route_calls': [{'usage': {'input_tokens': 50, 'output_tokens': 10}}]}


@pytest.mark.parametrize('error', [CoordinatorPaused, BudgetExceeded])
def test_stop_before_judge_dispatch_resumes_grade_without_repeating_application(monkeypatch, tmp_path, error):
    cfg = configured(tmp_path)
    calls = install_completion(monkeypatch)
    directory = tmp_path/'run'
    first = Runner(cfg, directory, backend='local')
    application_calls = []
    def execute(payload):
        application_calls.append(payload['trial_id'])
        def stop(): raise error('Stop before the next paid dispatch')
        first.store.before_new_attempt = stop
        return completed_application(payload)
    first.execute = execute
    with pytest.raises(error): first.evaluate(cfg['target'], cfg['prompt'], 'development')
    states = first.store.db.execute('SELECT state,reserved,value FROM attempts').fetchall()
    assert len(states) == 2 and sum(row[0] == 'started' and row[1] == 0 for row in states) == 1
    assert calls == []
    first.store.db.close()
    resumed = Runner(cfg, directory, backend='local')
    resumed.execute = lambda *_: pytest.fail('Completed application must not run again')
    rows = resumed.evaluate(cfg['target'], cfg['prompt'], 'development')
    assert rows[0]['checks']['correctness']['pass'] is True
    assert len(application_calls) == len(calls) == 1
    assert {row[0] for row in resumed.store.db.execute('SELECT state FROM attempts')} == {'complete'}


def test_crash_after_judge_receipt_recovers_grade_without_any_new_model_request(monkeypatch, tmp_path):
    cfg = configured(tmp_path)
    calls = install_completion(monkeypatch)
    directory = tmp_path/'run'
    first = Runner(cfg, directory, backend='local')
    first.execute = completed_application
    original_grade = runner_module.grade
    def stop_after_judge(*args):
        original_grade(*args)
        raise CoordinatorPaused('Stop after judge receipt, before derived grade commit')
    monkeypatch.setattr(runner_module, 'grade', stop_after_judge)
    with pytest.raises(CoordinatorPaused): first.evaluate(cfg['target'], cfg['prompt'], 'development')
    assert len(calls) == 1
    records = first.store.db.execute('SELECT state,value FROM attempts').fetchall()
    assert sum(state == 'complete' and json.loads(value).get('purpose') == 'judge' for state,value in records) == 1
    assert sum(state == 'started' for state,_ in records) == 1
    first.store.db.close()
    monkeypatch.setattr(runner_module, 'grade', original_grade)
    resumed = Runner(cfg, directory, backend='local')
    resumed.execute = lambda *_: pytest.fail('Application receipt must be reused')
    rows = resumed.evaluate(cfg['target'], cfg['prompt'], 'development')
    assert rows[0]['checks']['correctness']['pass'] is True and len(calls) == 1


def test_uncertain_paid_judge_receipt_still_blocks_grade_recovery_and_never_retries(monkeypatch, tmp_path):
    cfg = configured(tmp_path)
    calls = install_completion(monkeypatch, error=InterruptedAttempt)
    directory = tmp_path/'run'
    first = Runner(cfg, directory, backend='local')
    first.execute = completed_application
    with pytest.raises(InterruptedAttempt): first.evaluate(cfg['target'], cfg['prompt'], 'development')
    assert len(calls) == 1
    first.store.db.close()
    resumed = Runner(cfg, directory, backend='local')
    resumed.execute = lambda *_: pytest.fail('Application must not be repeated')
    with pytest.raises(InterruptedAttempt): resumed.evaluate(cfg['target'], cfg['prompt'], 'development')
    assert len(calls) == 1
    states = resumed.store.db.execute('SELECT state,reserved FROM attempts').fetchall()
    assert sum(state == 'started' and reserved > 0 for state,reserved in states) == 1
    assert sum(state == 'started' and reserved == 0 for state,reserved in states) == 1
