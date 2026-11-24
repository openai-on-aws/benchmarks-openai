"""Frozen business rules supplied during intake must reach the independent judge."""
import json
from copy import deepcopy
from test_source_grading import fixture
from customer_eval.scoring import grade
from customer_eval.source_grading import build_source_context

def test_intake_rules_are_fixed_source_context_without_prompt_or_expected_leakage():
    config, case, result, answer = fixture()
    config['intake_context'] = {'quality': {'rules': 'Use the explicitly printed currency; never infer it from an address.'}}
    for check in answer['checks'].values():
        check['evidence'][0]['source_id'] = 'source_002'
    def judge(_, text):
        payload = json.loads(text.split('\n', 1)[1])
        assert payload['sources'][0]['role'] == 'policy'
        assert payload['sources'][0]['text'] == config['intake_context']['quality']['rules']
        assert len(payload['sources']) == 2
        return json.dumps(answer)
    assert all(c['pass'] for c in grade(config, case, result, judge).values())

def test_duplicate_fixed_rules_are_not_repeated_in_source_context():
    case = {'turns':['Invoice total USD 3.00']}
    assert build_source_context(case, policy='fixed contract', general_rules='fixed contract\n') == build_source_context(case, policy='fixed contract')

def test_fixed_rules_change_source_digest_while_editable_prompt_is_excluded():
    from customer_eval.source_grading import evidence_digest
    case = {'turns':['Invoice total USD 3.00']}
    assert evidence_digest(build_source_context(case, general_rules='contract one')) != evidence_digest(build_source_context(case, general_rules='contract two'))
