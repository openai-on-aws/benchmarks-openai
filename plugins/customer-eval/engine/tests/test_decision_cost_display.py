"""Reporting regressions for cost denominators and validation/holdout separation."""
import copy
import json
from dataclasses import asdict

import pytest

from customer_eval.decision_report import build, write
from customer_eval.scoring import summarize
from customer_eval.selection import choose_joint
from hillclimb import CandidateScore


@pytest.fixture
def report_inputs():
    policy = {'mode': 'joint_cost_latency', 'cost_metric': 'cost_usd_per_success',
              'min_validation_sessions': 20}
    route = {'model_id': 'fixture-model', 'parameters': {}, 'base_url': 'https://example.invalid',
             'input_usd_per_million': 1, 'output_usd_per_million': 1}
    config = {'prompt': 'original', 'source': {'model_id': 'source'}, 'target': {'model_id': 'target'},
              'routes': {'source': dict(route, model_id='source-model'),
                         'target': dict(route, model_id='target-model')},
              'datasets': {'heldout': [{'id': f'case-{i}'} for i in range(5)]},
              'document': {'datasets': [{'id': 'heldout', 'label_status': 'reviewed'}],
                           'checks': [{'id': 'answer', 'required': True}], 'success_criteria': [],
                           'runner': {'selection': policy}}}

    def group(prompt, passes, tokens, latency=2000):
        rows = [{'case_id': f'case-{i % 5}', 'repeat': i // 5, 'model_id': 'target',
                 'result': {'status': 'completed', 'latency_ms': latency,
                            'route_calls': [{'usage': {'input_tokens': tokens, 'output_tokens': 0}}]},
                 'checks': {'answer': {'pass': i < passes, 'reason': 'fixture required check'}}}
                for i in range(20)]
        return {'trials': rows, 'score': asdict(summarize(config, prompt, rows))}

    # The selected configuration costs more per attempt, but less per success.
    source = group('original', 16, 100000)
    selected = group('revised', 20, 110000, latency=1000)
    validation = group('revised', 18, 90000, latency=1200)
    report = {'simulated': False, 'backend': 'fixture', 'fingerprint': 'fixture',
              'evaluation_status': 'complete',
              'selected_prompt': {'prompt': 'revised', 'reason': 'Validation selected the revision',
                                  'policy': copy.deepcopy(policy)},
              'holdout': {'source_original': source, 'target_original': copy.deepcopy(source),
                          'target_selected': selected},
              'optimization': {'generations': [{'index': 1, 'candidates': [validation['score']],
                                                'decisions': [{'selected': True, 'accepted': True,
                                                               'reasons': []}]}]},
              'model_costs': {'estimated_model_cost_usd': 8},
              'cost_note': 'Fixture list prices; no requests executed.', 'latency_scope': 'Fixture latency.'}
    return config, report


@pytest.mark.parametrize('metric, expected_reason, recommendation', [
    ('cost_usd_per_success', 'cost per success improved', 'The selected configuration is a candidate'),
    ('cost_usd_per_session', 'cost per attempt worsened', 'Review the quality, cost and latency tradeoff'),
])
def test_recommendation_uses_configured_cost_denominator(report_inputs, metric, expected_reason, recommendation):
    config, report = report_inputs
    config['document']['runner']['selection']['cost_metric'] = metric
    report['selected_prompt']['policy']['cost_metric'] = metric
    decision = build(config, report)
    assert decision['selection_cost_metric'] == metric
    assert expected_reason in decision['reasons']
    assert decision['recommendation'].startswith(recommendation)


def test_report_uses_frozen_selection_policy(report_inputs):
    config, report = report_inputs
    config['document']['runner']['selection']['cost_metric'] = 'cost_usd_per_session'
    decision = build(config, report)
    assert decision['selection_cost_metric'] == 'cost_usd_per_success'
    assert 'cost per success improved' in decision['reasons']


def test_older_report_defaults_to_attempt_cost(report_inputs):
    config, report = report_inputs
    del report['selected_prompt']['policy']
    del config['document']['runner']['selection']['cost_metric']
    decision = build(config, report)
    assert decision['selection_cost_metric'] == 'cost_usd_per_session'
    assert 'cost per attempt worsened' in decision['reasons']


def test_outputs_show_both_costs_and_keep_validation_separate(report_inputs, tmp_path):
    config, report = report_inputs
    before = copy.deepcopy(report)
    write(config, report, tmp_path)
    decision = json.loads((tmp_path / 'decision.json').read_text())
    assert report == before
    source = decision['holdout'][0]
    # Spend from all 20 attempts is divided by the 16 successes, not the 5 distinct cases.
    assert (source['planned_trials'], source['passed_trials'], source['distinct_cases']) == (20, 16, 5)
    assert source['model_usd_per_trial'] == pytest.approx(.1)
    assert source['model_usd_per_success'] == pytest.approx(.125)
    candidate = decision['prompt_comparison']['candidates'][0]
    assert candidate['model_usd_per_trial'] == pytest.approx(.09)
    assert candidate['model_usd_per_success'] == pytest.approx(.1)
    assert candidate['quality'] == .9
    assert candidate['comparison'] == 'matched validation; not holdout'
    assert decision['holdout'][-1]['model_usd_per_success'] == pytest.approx(.11)
    assert decision['selection_cost_metric'] == 'cost_usd_per_success'
    for filename in ('decision.md', 'decision.html'):
        text = (tmp_path / filename).read_text()
        assert text.count('Model USD/attempt') == 2
        assert text.count('Model USD/success') == 2
        assert 'cost per success (cost_usd_per_success)' in text
        assert 'including attempts that failed required checks' in text
        assert 'frozen before the held-out evaluation' in text
        assert 'Held-out results do not select or replace the prompt' in text
    markdown = (tmp_path / 'decision.md').read_text()
    assert '| 0.100000 | 0.125000 |' in markdown
    assert '| 0.090000 | 0.100000 |' in markdown
    html = (tmp_path / 'decision.html').read_text()
    assert '<td>0.100000</td><td>0.125000</td>' in html
    assert '<td>0.090000</td><td>0.100000</td>' in html


@pytest.mark.parametrize('missing_key', [False, True])
def test_unknown_success_cost_never_falls_back_to_attempt_cost(report_inputs, missing_key):
    config, report = report_inputs
    selected = report['holdout']['target_selected']['score']
    selected['cost_usd_per_session'] = .01
    if missing_key:
        del selected['cost_usd_per_success']
    else:
        selected['cost_usd_per_success'] = None
    decision = build(config, report)
    assert decision['holdout'][-1]['model_usd_per_success'] is None
    assert 'cost per success is not fully measured' in decision['reasons']
    assert decision['recommendation'].startswith('Review the quality, cost and latency tradeoff')


def test_zero_success_remains_unknown_and_cannot_recommend_pilot(report_inputs, tmp_path):
    config, report = report_inputs
    selected = report['holdout']['target_selected']
    for row in selected['trials']:
        row['checks']['answer']['pass'] = False
    selected['score'] = asdict(summarize(config, 'revised', selected['trials']))
    report['optimization']['generations'][0]['candidates'][0] = copy.deepcopy(selected['score'])
    decision = write(config, report, tmp_path)
    assert decision['recommendation'].startswith('Address the failing cases')
    assert decision['holdout'][-1]['model_usd_per_trial'] == pytest.approx(.11)
    assert decision['holdout'][-1]['model_usd_per_success'] is None
    assert decision['prompt_comparison']['candidates'][0]['model_usd_per_success'] is None
    saved = json.loads((tmp_path / 'decision.json').read_text())
    assert saved['holdout'][-1]['model_usd_per_success'] is None
    for filename in ('decision.md', 'decision.html'):
        text = (tmp_path / filename).read_text()
        assert 'cost per success is unknown with zero successful attempts' in text
        assert 'unknown' in text
        assert 'Address the failing cases' in text
    assert '| 0.110000 | unknown |' in (tmp_path / 'decision.md').read_text()
    assert '<td>0.110000</td><td>unknown</td>' in (tmp_path / 'decision.html').read_text()


def test_retained_prompt_is_not_promoted_by_heldout_results(report_inputs):
    config, report = report_inputs
    report['selected_prompt']['prompt'] = config['prompt']
    report['holdout']['target_challenger'] = report['holdout']['target_selected']
    report['holdout']['target_selected'] = dict(copy.deepcopy(report['holdout']['target_original']),
                                              reused_from='target_original')
    report['optimization']['generations'][0]['decisions'][0] = {
        'selected': False, 'accepted': True, 'needs_review': True, 'reasons': []}
    decision = build(config, report)
    comparison = decision['prompt_comparison']
    assert comparison['status'] == 'original_retained'
    assert comparison['target_selected']['prompt'] == config['prompt']
    assert 'Held-out results do not select or replace the prompt' in comparison['explanation']
    assert comparison['candidates'][0]['decision']['selected'] is False


def rescore_passes(config, group, passes):
    for index, row in enumerate(group['trials']):
        row['checks']['answer']['pass'] = index < passes
    group['score'] = asdict(summarize(config, group['score']['prompt'], group['trials']))


def set_validation_candidates(report, candidates, *, historical_tradeoff=False):
    baseline = CandidateScore(**report['holdout']['target_original']['score'])
    scores = [CandidateScore(**candidate) for candidate in candidates]
    winner, decisions = choose_joint(baseline, scores, report['selected_prompt']['policy'])
    if historical_tradeoff:
        # Persisted pre-ranking decisions must keep rendering without being
        # reselected by today's policy or relabeled as today's decision.
        decisions = [{'accepted': True, 'selected': False, 'needs_review': True, 'reasons': []}
                     for _ in candidates]
    else:
        assert winner is baseline
    report['optimization']['generations'] = [{'index': 1, 'candidates': candidates, 'decisions': decisions}]
    report['joint_comparisons'] = [{'generation': 1, 'scores': [asdict(baseline)] + candidates}]


@pytest.fixture
def review_inputs(report_inputs):
    config, report = report_inputs
    report['holdout']['target_challenger'] = report['holdout']['target_selected']
    rescore_passes(config, report['holdout']['target_original'], 4)
    report['holdout']['target_selected'] = dict(report['holdout']['target_original'], reused_from='target_original')
    report['selected_prompt'].update(prompt=config['prompt'], reason='Tradeoffs need customer review; incumbent retained',
                                     challenger={'prompt': 'revised', 'selected': False})
    cheaper = dict(report['optimization']['generations'][0]['candidates'][0],
                   accuracy=1., cost_usd_per_success=.09)
    faster = dict(cheaper, prompt='faster revision', cost_usd_per_session=.10,
                  cost_usd_per_success=.10, latency_p95_s=1.)
    set_validation_candidates(report, [cheaper, faster], historical_tradeoff=True)
    return config, report


def test_ranked_selection_explanation_and_faster_alternative_are_reported(review_inputs, tmp_path):
    config, report = review_inputs
    generation = report['optimization']['generations'][0]
    baseline = CandidateScore(**report['joint_comparisons'][0]['scores'][0])
    winner, decisions = choose_joint(baseline, [CandidateScore(**c) for c in generation['candidates']],
                                    report['selected_prompt']['policy'])
    generation['decisions'] = decisions
    report['selected_prompt']['prompt'] = winner.prompt
    report['holdout']['target_selected'] = report['holdout'].pop('target_challenger')
    decision = write(config, report, tmp_path)
    comparison = decision['prompt_comparison']
    assert comparison['changed'] and not comparison['review_required']
    assert 'highest required-check pass rate, then lowest cost per success, then lowest p95 latency' in comparison['explanation']
    assert comparison['candidates'][0]['decision']['selected']
    assert 'Faster alternative' in ' '.join(comparison['candidates'][1]['decision']['notes'])
    assert (tmp_path/'selected-prompt.txt').read_text() == winner.prompt
    for filename in ('decision.md', 'decision.html'):
        text = (tmp_path/filename).read_text()
        assert 'highest required-check pass rate' in text
        assert 'Faster alternative' in text


def test_retained_original_foregrounds_candidate_choice_and_separate_challenger_evidence(review_inputs, tmp_path):
    config, report = review_inputs
    before = copy.deepcopy(report)
    decision = write(config, report, tmp_path)
    assert report == before
    assert decision['recommendation'].startswith('Review candidate prompt tradeoffs')
    comparison = decision['prompt_comparison']
    assert comparison['status'] == 'original_retained' and not comparison['changed']
    assert comparison['review_required']
    assert comparison['review_candidate_ids'] == ['generation-1-candidate-1', 'generation-1-candidate-2']
    reasons = ' '.join(decision['reasons'])
    assert 'Each candidate awaiting review improves on the original prompt in matched validation' in reasons
    assert 'unresolved tradeoff is between these improved candidates' in reasons
    assert 'matched validation original, 20.0% of attempts passed all required checks' in reasons
    assert 'candidate 1 has the lowest cost per success ($0.090000)' in reasons
    assert 'candidate 2 has the lowest observed p95 (1.000 s)' in reasons
    assert 'passed 20/20 held-out attempts' in reasons
    assert 'held-out cost per success is lower' in reasons
    assert 'held-out observed p95 is lower' in reasons
    assert 'other candidates awaiting review have no held-out results' in reasons
    assert 'does not promote a candidate' in reasons
    assert comparison['target_selected']['prompt'] == config['prompt']
    assert all(not c['decision']['selected'] for c in comparison['candidates'])
    assert (tmp_path / 'selected-prompt.txt').read_text() == config['prompt']
    saved = json.loads((tmp_path / 'decision.json').read_text())
    assert saved['prompt_comparison']['review_required']
    for filename in ('decision.md', 'decision.html'):
        text = (tmp_path / filename).read_text()
        assert 'Review candidate prompt tradeoffs' in text
        assert 'No weighting is applied automatically' in text
        assert 'Candidates awaiting review met the recorded validation selection requirements' in text
        assert 'Keep the current model pending review' not in text


def test_mixed_reviewable_and_rejected_candidates_are_not_grouped_as_failed(review_inputs):
    config, report = review_inputs
    cheaper, rejected = copy.deepcopy(report['optimization']['generations'][0]['candidates'])
    cheaper['latency_p95_s'] = 3.  # Cheaper but slower than the validation incumbent.
    rejected.update(accuracy=0., cost_usd_per_success=None)
    set_validation_candidates(report, [cheaper, rejected])
    decision = build(config, report)
    assert decision['recommendation'].startswith('Review candidate prompt tradeoffs')
    assert decision['prompt_comparison']['review_candidate_ids'] == ['generation-1-candidate-1']
    assert 'No candidate in the final validation generation met' not in decision['prompt_comparison']['explanation']
    assert 'has the lowest' not in ' '.join(decision['reasons'])
    assert 'between these improved candidates' not in ' '.join(decision['reasons'])


def test_favorable_heldout_challenger_cannot_revive_rejected_validation_candidates(review_inputs):
    config, report = review_inputs
    rejected = [dict(c, accuracy=0., cost_usd_per_success=None)
                for c in report['optimization']['generations'][0]['candidates']]
    set_validation_candidates(report, rejected)
    decision = build(config, report)
    assert decision['recommendation'].startswith('Keep the current model pending review')
    assert not decision['prompt_comparison']['review_required']
    assert decision['prompt_comparison']['review_candidate_ids'] == []
    assert 'No candidate in the final validation generation met the selection requirements' in decision['prompt_comparison']['explanation']


def test_earlier_generation_tradeoffs_do_not_override_final_validation_rejections(review_inputs):
    config, report = review_inputs
    earlier = copy.deepcopy(report['optimization']['generations'][0])
    rejected = [dict(c, accuracy=0., cost_usd_per_success=None) for c in earlier['candidates']]
    set_validation_candidates(report, rejected)
    report['optimization']['generations'][0]['index'] = 2
    report['optimization']['generations'].insert(0, earlier)
    decision = build(config, report)
    assert not decision['prompt_comparison']['review_required']
    assert decision['recommendation'].startswith('Keep the current model pending review')


def test_zero_success_challenger_keeps_failure_caution_and_unknown_success_cost(review_inputs):
    config, report = review_inputs
    rescore_passes(config, report['holdout']['target_challenger'], 0)
    decision = build(config, report)
    assert 'address the held-out candidate’s failing cases' in decision['recommendation']
    assert decision['prompt_comparison']['review_required']
    reasons = ' '.join(decision['reasons'])
    assert 'passed 0/20 held-out attempts' in reasons
    assert 'held-out candidate passed no attempts' in reasons
    assert 'Held-out cost per success cannot be compared' in reasons
    assert 'held-out cost per success is lower' not in reasons
    challenger = next(r for r in decision['holdout'] if r['configuration'] == 'target_challenger')
    assert challenger['model_usd_per_success'] is None


@pytest.mark.parametrize('cost', [None, 0.])
def test_challenger_cost_comparison_preserves_unknown_and_zero(review_inputs, cost):
    config, report = review_inputs
    report['holdout']['target_challenger']['score']['cost_usd_per_success'] = cost
    decision = build(config, report)
    reasons = ' '.join(decision['reasons'])
    if cost is None:
        assert 'Held-out cost per success cannot be compared' in reasons
        assert 'held-out cost per success is lower' not in reasons
    else:
        assert 'held-out cost per success is lower' in reasons
        assert '$0.000000 versus $0.500000' in reasons


def test_failed_retained_original_does_not_hide_successful_review_candidate(review_inputs):
    config, report = review_inputs
    rescore_passes(config, report['holdout']['target_original'], 0)
    report['holdout']['target_selected'] = dict(report['holdout']['target_original'], reused_from='target_original')
    decision = build(config, report)
    assert decision['recommendation'].startswith('Review candidate prompt tradeoffs')
    reasons = ' '.join(decision['reasons'])
    assert 'retained original prompt passed no held-out attempts' in reasons
    assert 'passed 20/20 held-out attempts' in reasons
    assert 'Held-out cost per success cannot be compared' in reasons


def test_review_without_matching_heldout_challenger_does_not_borrow_other_candidate_results(review_inputs):
    config, report = review_inputs
    report['holdout']['target_challenger']['score']['prompt'] = 'a different candidate'
    decision = build(config, report)
    assert decision['recommendation'].startswith('Review candidate prompt tradeoffs')
    assert 'No held-out results are available for the candidates awaiting review.' in decision['reasons']
    assert 'passed 20/20 held-out attempts' not in ' '.join(decision['reasons'])


def test_missing_matched_validation_baseline_does_not_borrow_heldout_baseline(review_inputs):
    config, report = review_inputs
    del report['joint_comparisons']
    decision = build(config, report)
    assert decision['prompt_comparison']['review_required']
    assert 'between these improved candidates' not in ' '.join(decision['reasons'])
