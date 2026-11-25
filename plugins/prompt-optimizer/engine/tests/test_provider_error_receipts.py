"""Known HTTP failure receipts are durable; uncertain requests are never replayed."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Event
from urllib.error import HTTPError

import pytest

from customer_eval import runner as runner_module, transport
from customer_eval.contract import digest
from customer_eval.costs import price_usage, run_costs
from customer_eval.errors import RecordedProviderError
from customer_eval.runner import Runner, InterruptedAttempt
from test_reference_source_grading import ANSWER, mixed_project
from test_source_grading_recovery import configured as source_configured

REQUEST_ID = '12345678-1234-1234-1234-123456789abc'
ERROR_SECRET = 'DO_NOT_RECORD_PROVIDER_BODY'


def direct_config(tmp_path):
    config = source_configured(tmp_path)
    # The older source-only fixture uses its optimizer route for judging.
    model = config['document']['checks'][0]['judge_model_id']
    config['routes']['judge'] = dict(config['routes'][model], model_id='fixture-judge-model')
    config.pop('fingerprint')
    config['fingerprint'] = digest(config)
    return config


def fail_http(monkeypatch, *, status=500, entered=None, release=None):
    calls = []
    def request(route, body, timeout=60):
        calls.append(deepcopy(body))
        if entered:
            entered.set()
            assert release.wait(5), 'test did not release the fake provider'
        raw = json.dumps({'error': {'message': ERROR_SECRET}}).encode()
        raise transport._sanitized_http_error(route['base_url'], status,
            {'x-amzn-requestid': REQUEST_ID}, raw, route)
    monkeypatch.setattr(transport, 'request', request)
    return calls


def saved_records(runner):
    return [json.loads(value) for state,value in runner.store.db.execute('SELECT state,value FROM attempts') if state == 'complete']


@pytest.mark.parametrize('purpose,status', [('judge', 500), ('optimizer', 503), ('judge', 403)])
def test_http_error_receipt_is_terminal_but_never_a_success_or_free_call(tmp_path, monkeypatch, purpose, status):
    config = direct_config(tmp_path)
    calls = fail_http(monkeypatch, status=status)
    directory = tmp_path / 'run'
    first = Runner(config, directory, backend='local')
    text = 'Fixed request without private error-body content'
    ident = digest(['completion', purpose, 'judge', text])
    with pytest.raises(RecordedProviderError) as error:
        first.call('judge', text, purpose=purpose)
    assert error.value.http_status == status and error.value.attempt_id == ident
    assert ERROR_SECRET not in str(error.value)
    state,reserved,value = first.store.db.execute('SELECT state,reserved,value FROM attempts WHERE id=?', (ident,)).fetchone()
    receipt = json.loads(value)
    assert state == 'complete' and reserved > 0
    assert receipt['status'] == 'provider_error' and receipt['usage'] is None and 'text' not in receipt
    assert receipt['http_status'] == status and receipt['purpose'] == purpose and receipt['model_id'] == 'judge'
    assert receipt['model_requested'] == config['routes']['judge']['model_id']
    assert receipt['request_text'] == text and receipt['completion_elapsed_ms'] >= 0
    assert receipt['provider_error'] == {'http_status': status, 'body_omitted': True, 'request_id': REQUEST_ID, **({'authorization_category': 'authorization_unspecified'} if status == 403 else {})}
    assert ERROR_SECRET not in value
    assert first.store.reserved() == reserved  # Reservation remains; it is not a measured charge.
    first.store.db.close()
    resumed = Runner(config, directory, backend='local')
    with pytest.raises(RecordedProviderError) as cached:
        resumed.call('judge', text, purpose=purpose)
    assert cached.value.attempt_id == ident and len(calls) == 1
    cost = run_costs(config, saved_records(resumed))
    assert cost['estimated_model_cost_usd'] is None and cost['known_model_cost_usd'] == 0
    assert cost['unpriced_requests_or_incomplete_traces'] == 1
    resumed.store.db.close()


@pytest.mark.parametrize('failure', [TimeoutError, ConnectionError])
def test_network_failure_stays_uncertain_and_cannot_retry(tmp_path, monkeypatch, failure):
    config = direct_config(tmp_path); calls = []
    def request(*args, **kwargs):
        calls.append(1)
        raise failure('request outcome unknown')
    monkeypatch.setattr(transport, 'request', request)
    runner = Runner(config, tmp_path/'run', backend='local')
    with pytest.raises(failure): runner.call('judge', 'request')
    assert runner.store.db.execute('SELECT state,value FROM attempts').fetchall() == [('started', None)]
    with pytest.raises(InterruptedAttempt): runner.call('judge', 'request')
    assert len(calls) == 1
    cost = run_costs(config, [], unresolved_attempts=1)
    assert cost['estimated_model_cost_usd'] is None and cost['unpriced_requests_or_incomplete_traces'] == 1
    runner.store.db.close()


@pytest.mark.parametrize('receipt', [{}, {'status': 'provider_error', 'http_status': 400}, {'status': 'provider_error', 'http_status': True}])
def test_http_exception_without_matching_transport_receipt_stays_uncertain(tmp_path, monkeypatch, receipt):
    config = direct_config(tmp_path)
    def incomplete_receipt(*args, **kwargs):
        kwargs['receipt'].update(receipt)
        raise HTTPError('https://example.invalid', 500, 'unsafe response omitted', {}, None)
    monkeypatch.setattr(runner_module, 'complete', incomplete_receipt)
    runner = Runner(config, tmp_path/'run', backend='local')
    with pytest.raises(HTTPError): runner.call('judge', 'request')
    assert runner.store.db.execute('SELECT state,value FROM attempts').fetchall() == [('started', None)]
    runner.store.db.close()


def test_concurrent_identical_failures_share_one_paid_request(tmp_path, monkeypatch):
    config = direct_config(tmp_path); entered,release = Event(),Event()
    calls = fail_http(monkeypatch, entered=entered, release=release)
    runner = Runner(config, tmp_path/'run', backend='local')
    def call():
        with pytest.raises(RecordedProviderError) as error:
            runner.call('judge', 'same request')
        return error.value.attempt_id
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(call) for _ in range(4)]
        assert entered.wait(5)
        release.set()
        identities = [future.result() for future in futures]
    assert len(calls) == len(set(identities)) == len(saved_records(runner)) == 1
    runner.store.db.close()


def test_known_subtotal_survives_failed_receipts_without_manufactured_usage(tmp_path):
    config = direct_config(tmp_path)
    success = {'purpose': 'judge', 'model_id': 'judge', 'status': 'completed', 'text': 'answer',
               'usage': {'input_tokens': 100, 'output_tokens': 25}}
    error = {'purpose': 'judge', 'model_id': 'judge', 'status': 'provider_error', 'http_status': 500, 'usage': None}
    expected = price_usage(config['routes']['judge'], success['usage'])
    assert expected > 0
    cost = run_costs(config, [success,error], unresolved_attempts=1)
    assert cost['known_model_cost_usd'] == cost['by_purpose_usd']['judge'] == expected
    assert cost['estimated_model_cost_usd'] is None and cost['unpriced_requests_or_incomplete_traces'] == 2
    # A failed receipt cannot become free or successful merely by gaining text/usage-shaped fields.
    cost = run_costs(config, [dict(error,text='',usage={'input_tokens':0,'output_tokens':0})])
    assert cost['estimated_model_cost_usd'] is None and cost['unpriced_requests_or_incomplete_traces'] == 1


def test_optional_http_failure_preserves_required_scores_and_is_visible_in_report(tmp_path, monkeypatch):
    _,config = mixed_project(tmp_path)
    config['document']['execution'].update(repetitions=1, max_cases_per_dataset=1, concurrency=1)
    calls = fail_http(monkeypatch)
    actual = deepcopy(ANSWER); actual['explanation'] = 'A source explanation.'
    application_calls = []
    def execute(payload):
        application_calls.append(payload['trial_id'])
        return {'trial_id': payload['trial_id'], 'status': 'completed', 'outputs': [json.dumps(actual)],
                'model_requested': payload['route']['model_id'], 'latency_ms': 1000, 'tool_calls': [],
                'route_calls': [{'usage': {'input_tokens': 100, 'output_tokens': 50}}]}
    directory = tmp_path/'run'
    first = Runner(config, directory, backend='local'); monkeypatch.setattr(first, 'execute', execute)
    report = first.run(optimize=False)
    assert report['evaluation_status'] == 'needs_review' and report['unresolved_attempts'] == []
    assert report['grading_error_records'] > 0
    assert len(report['judge_requests']) == len(calls) > 0
    assert all(r['status'] == 'provider_error' and r['usage'] is None for r in report['judge_requests'])
    assert all(group['score']['accuracy'] == 1 for group in report['holdout'].values())
    assert all(group['source_judge_metrics']['by_check']['explanation']['grading_errors'] > 0 for group in report['holdout'].values())
    costs = report['model_costs']
    assert costs['known_model_cost_usd'] > 0 and costs['estimated_model_cost_usd'] is None
    assert costs['unpriced_requests_or_incomplete_traces'] == len(calls)
    saved = json.loads((directory/'report.json').read_text())
    assert saved['judge_requests'] == report['judge_requests']
    assert all('text' not in receipt and 'model_returned' not in receipt for receipt in saved['judge_requests'])
    decision = json.loads((directory/'decision.json').read_text())
    assert decision['model_costs'] == costs
    assert all(row['check_pass_rate'] == 1 for row in decision['holdout'])
    assert 'unknown' in (directory/'decision.md').read_text()
    assert 'null' in (directory/'decision.html').read_text()
    before_count = (len(calls),len(application_calls))
    first.store.db.close()
    resumed = Runner(config, directory, backend='local')
    monkeypatch.setattr(resumed,'execute',lambda *_: pytest.fail('Saved application result must not be rerun'))
    assert resumed.run(optimize=False) == report
    assert (len(calls),len(application_calls)) == before_count
    resumed.store.db.close()


def test_grade_recovery_after_saved_http_error_never_repeats_the_paid_request(tmp_path, monkeypatch):
    from runtime.coordinator import CoordinatorPaused
    config = source_configured(tmp_path); calls = fail_http(monkeypatch)
    first = Runner(config,tmp_path/'run',backend='local')
    monkeypatch.setattr(first,'execute',lambda payload: {'trial_id':payload['trial_id'],'status':'completed',
        'outputs':['{"total":125}'],'tool_calls':[],'route_calls':[]})
    original_grade = runner_module.grade
    def interrupted_grade(*args):
        original_grade(*args)
        raise CoordinatorPaused('Pause after the failed HTTP receipt was saved')
    monkeypatch.setattr(runner_module,'grade',interrupted_grade)
    with pytest.raises(CoordinatorPaused): first.evaluate(config['target'],config['prompt'],'development')
    assert len(calls) == 1
    states = first.store.db.execute('SELECT state,reserved,value FROM attempts').fetchall()
    assert sum(state=='started' and reserved==0 for state,reserved,_ in states) == 1
    assert sum(state=='complete' and json.loads(value).get('status')=='provider_error' for state,_,value in states) == 1
    first.store.db.close()
    monkeypatch.setattr(runner_module,'grade',original_grade)
    resumed = Runner(config,tmp_path/'run',backend='local')
    monkeypatch.setattr(resumed,'execute',lambda *_: pytest.fail('Cannot repeat saved application result'))
    rows = resumed.evaluate(config['target'],config['prompt'],'development')
    assert rows[0]['checks']['correctness']['error'] and not rows[0]['checks']['correctness']['pass']
    assert len(calls) == 1
    assert {state for state, in resumed.store.db.execute('SELECT state FROM attempts')} == {'complete'}
    resumed.store.db.close()
