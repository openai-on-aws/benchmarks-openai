import copy
import json
from dataclasses import replace

import pytest

from customer_eval.semantic_grading import grade_semantics
from customer_eval.selection import choose_joint
from customer_eval.scoring import grade, summarize
from hillclimb import CandidateScore


def fixture():
    checks = [{'id': k, 'question': k, 'method': 'llm_judge', 'judge_model_id': 'judge', 'required': True}
              for k in ('eligibility', 'explanation')]
    config = {'document': {'checks': checks}, 'policy': 'Unopened within 45 days.',
              'grading': {k: {'type': 'turn_semantic', 'rubric': k} for k in ('eligibility', 'explanation')}}
    case = {'id': 'two-turn', 'turns': ['Missing ID', 'R-104'],
            'expected': {'turns': [{'response': {'meaning': 'ask for ID'}}, {'response': {'meaning': 'allow'}}]}}
    result = {'status': 'completed', 'trace_version': 'turns-v1', 'outputs': ['Which ID?', 'Eligible'],
              'tool_calls': [], 'turn_records': [
                  {'turn_index': i, 'user': user, 'output': output, 'status': 'completed',
                   'started_ns': i*10, 'answered_ns': i*10+2, 'ended_ns': i*10+3, 'tool_calls': []}
                  for i, (user, output) in enumerate(zip(case['turns'], ['Which ID?', 'Eligible']), 1)]}
    answer = {'turns': [{'turn_index': i, 'checks': {k: {'pass': True, 'reason': 'Matches expected behavior'}
                                                  for k in ('eligibility', 'explanation')}} for i in (1, 2)]}
    return config, case, result, answer


def test_one_blinded_call_grades_every_turn_and_criterion():
    cfg, case, result, answer = fixture()
    calls = []
    result['model_id'] = 'DO_NOT_DISCLOSE_MODEL'
    result['prompt'] = 'DO_NOT_DISCLOSE_PROMPT'
    def judge(model, text):
        calls.append((model, text))
        assert 'DO_NOT_DISCLOSE' not in text
        assert 'Missing ID' in text and 'R-104' in text
        return json.dumps(answer)
    checks = grade(cfg, case, result, judge)
    assert len(calls) == 1 and all(c['pass'] for c in checks.values())
    assert len(checks['eligibility']['turn_details']) == 2


def test_final_success_does_not_hide_first_turn_failure():
    cfg, case, result, answer = fixture()
    answer['turns'][0]['checks']['eligibility'] = {'pass': False, 'reason': 'Guessed eligibility before lookup'}
    checks = grade(cfg, case, result, lambda *_: json.dumps(answer))
    assert not checks['eligibility']['pass'] and checks['explanation']['pass']
    assert 'turn 1' in checks['eligibility']['reason']


@pytest.mark.parametrize('mutation', ['missing_turn', 'duplicate_turn', 'extra_check', 'missing_check', 'string_bool', 'blank_reason'])
def test_malformed_judge_fails_all_semantic_checks(mutation):
    cfg, case, result, answer = fixture()
    if mutation == 'missing_turn': answer['turns'].pop()
    elif mutation == 'duplicate_turn': answer['turns'][1]['turn_index'] = 1
    elif mutation == 'extra_check': answer['turns'][0]['checks']['extra'] = {'pass': True, 'reason': 'x'}
    elif mutation == 'missing_check': del answer['turns'][0]['checks']['eligibility']
    elif mutation == 'string_bool': answer['turns'][0]['checks']['eligibility']['pass'] = 'true'
    else: answer['turns'][0]['checks']['eligibility']['reason'] = ' '
    checks = grade(cfg, case, result, lambda *_: json.dumps(answer))
    assert all(not c['pass'] and c['error'] for c in checks.values())


def test_duplicate_json_keys_and_incomplete_trace_fail_closed():
    cfg, case, result, _ = fixture()
    checks = grade(cfg, case, result, lambda *_: '{"turns":[],"turns":[]}')
    assert all(c['error'] for c in checks.values())
    result['turn_records'].pop()
    checks = grade(cfg, case, result, lambda *_: pytest.fail('Do not judge invalid evidence'))
    assert all(c['error'] for c in checks.values())


def test_more_wrong_decisions_cannot_hide_under_better_aggregate_accuracy():
    base = CandidateScore('base', .8, criterion_failure_counts={'eligibility': 1},
                          latency_p95_s=5, cost_usd_per_success=.02, n_sessions=24)
    candidate = replace(base, prompt='new', accuracy=.9, latency_p95_s=4,
                        cost_usd_per_success=.01, criterion_failure_counts={'eligibility': 2})
    policy = {'mode': 'joint_cost_latency', 'min_validation_sessions': 20,
              'cost_metric': 'cost_usd_per_success', 'non_regression_checks': ['eligibility']}
    winner, decisions = choose_joint(base, [candidate], policy)
    assert winner is base and 'eligibility failures increased' in decisions[0]['reasons']
    candidate = replace(candidate, criterion_failure_counts={'eligibility': 1})
    assert choose_joint(base, [candidate], policy)[0] is candidate
    candidate = replace(candidate, criterion_failure_counts={})
    assert choose_joint(base, [candidate], policy)[0] is base
