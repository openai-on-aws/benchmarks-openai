import copy
import shutil
from dataclasses import replace

import pytest
import yaml

from customer_eval.costs import price_usage, trial_cost, run_costs
from customer_eval.selection import choose_joint
from customer_eval.scoring import summarize
from customer_eval.contract import ROOT, load
from customer_eval.runner import Runner
from customer_eval.transport import reservation
from hillclimb import CandidateScore, run_hill_climb


POLICY = dict(mode='joint_cost_latency', min_cost_improvement_fraction=.05,
              min_latency_improvement_fraction=.05, min_validation_sessions=20)
PREFERENCES = dict(mode='joint_cost_latency', min_validation_sessions=20)
ROUTE = dict(model_id='test', input_usd_per_million=2.2, output_usd_per_million=11,
             cached_input_usd_per_million=.11, cache_write_usd_per_million=2.75,
             pricing_max_input_tokens=272000)


def test_cache_and_reasoning_accounting():
    usage = dict(input_tokens=1000, output_tokens=100,
                 input_tokens_details={'cached_tokens':200,'cache_write_tokens':300},
                 output_tokens_details={'reasoning_tokens':80})
    assert price_usage(ROUTE, usage) == pytest.approx((500*2.2+200*.11+300*2.75+100*11)/1e6)


@pytest.mark.parametrize('usage', [None, {}, {'input_tokens':True,'output_tokens':1},
    {'input_tokens':20,'output_tokens':1,'input_tokens_details':{'cached_tokens':30}},
    {'input_tokens':272001,'output_tokens':1}])
def test_unknown_or_invalid_usage_never_free(usage):
    assert price_usage(ROUTE, usage) is None


def test_unpriced_cache_and_failed_trace_remain_unknown():
    route = dict(ROUTE); del route['cache_write_usd_per_million']
    assert price_usage(route, {'input_tokens':10,'output_tokens':1,
                              'input_tokens_details':{'cache_write_tokens':1}}) is None
    row = {'result':{'status':'error','model_requested':'test',
                    'route_calls':[{'usage':{'input_tokens':10,'output_tokens':1}}, {'usage':None}]}}
    costs = trial_cost({'routes':{'m':ROUTE}}, row)
    assert costs['known_cost_usd'] > 0 and costs['estimated_cost_usd'] is None


def test_reservation_includes_cache_write_premium():
    assert reservation(ROUTE, {'max_requests':2,'max_request_bytes':100,'max_output_tokens':10}) == pytest.approx(2*(100*2.75+10*11)/1e6)


def baseline():
    return CandidateScore('original',1,latency_p95_s=10,cost_usd_per_session=.1,n_sessions=24)


def test_success_cost_changes_ranking_when_improved_quality_offsets_higher_attempt_cost():
    base = replace(baseline(), accuracy=.8, cost_usd_per_success=.125)
    candidate = replace(base, prompt='more-correct', accuracy=1, latency_p95_s=9,
                        cost_usd_per_session=.11, cost_usd_per_success=.11)
    assert choose_joint(base, [candidate], PREFERENCES)[0] is base
    winner, decisions = choose_joint(base, [candidate], dict(PREFERENCES, cost_metric='cost_usd_per_success'))
    assert winner is candidate
    assert decisions[0]['relative_improvements']['cost'] == pytest.approx(.12)


def test_success_cost_does_not_replace_accuracy_requirements():
    base = replace(baseline(), accuracy=.8, cost_usd_per_success=.125)
    candidate = replace(base, prompt='cheaper-but-wrong', accuracy=.7, latency_p95_s=9,
                        cost_usd_per_session=.05, cost_usd_per_success=.05/.7)
    winner, decisions = choose_joint(base, [candidate], dict(PREFERENCES, cost_metric='cost_usd_per_success'))
    assert winner is base and 'accuracy regressed' in decisions[0]['reasons']


def test_unknown_success_cost_does_not_fall_back_to_attempt_cost():
    base = replace(baseline(), cost_usd_per_success=.1)
    candidate = replace(base, prompt='missing-usage', latency_p95_s=9,
                        cost_usd_per_session=.01, cost_usd_per_success=None)
    winner, decisions = choose_joint(base, [candidate], dict(PREFERENCES, cost_metric='cost_usd_per_success'))
    assert winner is base and 'cost measurement unavailable' in decisions[0]['reasons']


@pytest.mark.parametrize('metric', ['wrong_metric', None, 5])
def test_invalid_cost_metric_fails_closed(metric):
    with pytest.raises(ValueError, match='cost_metric'):
        choose_joint(baseline(), [], dict(PREFERENCES, cost_metric=metric))


def test_success_cost_survives_intake_import_and_labels_report(tmp_path):
    source = tmp_path/'source'
    shutil.copytree(ROOT/'examples/retail-returns', source)
    path = source/'input.yaml'
    document = yaml.safe_load(path.read_text())
    document['runner']['selection']['cost_metric'] = 'cost_usd_per_success'
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    config = load(path)
    assert config['document']['runner']['selection']['cost_metric'] == 'cost_usd_per_success'
    report = Runner(config, tmp_path/'run').run(optimize=False)
    assert report['simulated']
    assert 'Model USD/success' in (tmp_path/'run/report.md').read_text()


def test_unselected_challenger_is_frozen_before_holdout_and_not_promoted(tmp_path, monkeypatch):
    import json
    from hillclimb import Generation, HillClimbResult
    cfg = load(ROOT/'examples/customer-support/input.yaml')
    cfg['document']['runner']['report_challenger'] = True
    original = CandidateScore(cfg['prompt'], 1, cost_usd_per_session=.02, latency_p95_s=2)
    candidate = replace(original, prompt='A proposed prompt for reporting', cost_usd_per_session=.01, latency_p95_s=3)
    search = HillClimbResult(original, [Generation(1, [candidate], original, [{'accepted': True}])],
                            'Tradeoff; original retained', selected=original)
    monkeypatch.setattr('customer_eval.runner.run_hill_climb', lambda **_: search)
    evaluate = Runner.evaluate_group
    def checked(self, variants, split, phase='standard'):
        if split == 'holdout':
            frozen = json.loads((tmp_path/'selected-prompt.json').read_text())
            assert frozen['prompt'] == original.prompt
            assert frozen['challenger']['prompt'] == candidate.prompt
            assert frozen['challenger']['selected'] is False
        return evaluate(self, variants, split, phase)
    monkeypatch.setattr(Runner, 'evaluate_group', checked)
    report = Runner(cfg, tmp_path).run()
    assert report['holdout']['target_selected']['reused_from'] == 'target_original'
    assert report['holdout']['target_challenger']['score']['prompt'] == candidate.prompt


@pytest.mark.parametrize('changes', [
    {'accuracy':.99}, {'eligible':False}, {'cost_usd_per_session':None},
    {'cost_usd_per_session':.101}, {'latency_p95_s':11}, {'n_sessions':8}])
def test_regressions_and_missing_measurements_cannot_win(changes):
    base = baseline(); candidate = replace(base,prompt='new',latency_p95_s=9,cost_usd_per_session=.09,**{})
    candidate = replace(candidate,**changes)
    winner, decisions = choose_joint(base,[candidate],POLICY)
    assert winner is base and decisions[0]['reasons']


def test_joint_gain_can_win_accuracy_tie():
    base = baseline(); candidate=replace(base,prompt='new',latency_p95_s=9,cost_usd_per_session=.09)
    result=run_hill_climb(seed_prompt='original',mutate_fn=lambda *_:['new'],
        fanout_score_fn=lambda p: base if p=='original' else candidate,
        candidates_per_generation=1,max_generations=1,min_accuracy_gain_to_continue=.01,
        selection_policy=POLICY)
    assert result.best.prompt=='new' and result.accuracy_gain==0


@pytest.mark.parametrize('order', [(0, 1), (1, 0)])
def test_competing_improvements_select_lower_cost_without_weighting(order):
    base=baseline()
    cheaper=replace(base,prompt='cheap',latency_p95_s=9,cost_usd_per_session=.07)
    faster=replace(base,prompt='fast',latency_p95_s=7,cost_usd_per_session=.09)
    winner, decisions=choose_joint(base,[(cheaper, faster)[i] for i in order],POLICY)
    assert winner is cheaper and not any(d['needs_review'] for d in decisions)
    by_prompt = {d['prompt']: d for d in decisions}
    assert by_prompt['cheap']['rank'] == 1 and by_prompt['cheap']['selected']
    assert by_prompt['fast']['rank'] == 2 and not by_prompt['fast']['selected']
    assert 'Faster alternative' in ' '.join(by_prompt['fast']['notes'])


def test_accuracy_precedes_cost_and_latency_among_qualifying_improvements():
    base = replace(baseline(), accuracy=.7)
    cheaper = replace(base, prompt='cheaper', accuracy=.8, cost_usd_per_session=.05, latency_p95_s=5)
    more_correct = replace(base, prompt='more-correct', accuracy=.9, cost_usd_per_session=.09, latency_p95_s=9)
    assert choose_joint(base, [cheaper, more_correct], PREFERENCES)[0] is more_correct


@pytest.mark.parametrize('order', [(0, 1), (1, 0)])
def test_latency_breaks_accuracy_and_cost_tie(order):
    base = baseline()
    slower = replace(base, prompt='slower', cost_usd_per_session=.09, latency_p95_s=9)
    faster = replace(slower, prompt='faster', latency_p95_s=8)
    assert choose_joint(base, [(slower, faster)[i] for i in order], PREFERENCES)[0] is faster


@pytest.mark.parametrize('order', [(0, 1), (1, 0)])
def test_exact_candidate_tie_is_stable_across_submission_order(order):
    base = baseline()
    first = replace(base, prompt='a prompt', cost_usd_per_session=.09, latency_p95_s=9)
    second = replace(first, prompt='b prompt')
    assert choose_joint(base, [(first, second)[i] for i in order], PREFERENCES)[0] is first


def test_saved_validation_regression_selects_candidate_one():
    # Reduced values from the completed customer run; no holdout data is used.
    base = CandidateScore('original', 2/36, n_sessions=36, latency_p95_s=4.767696427,
                          cost_usd_per_session=.0016908833333333334, cost_usd_per_success=.0304359)
    first = replace(base, prompt='candidate-1', accuracy=1., latency_p95_s=2.888300732,
                    cost_usd_per_session=.0013643055555555555, cost_usd_per_success=.0013643055555555555)
    second = replace(base, prompt='candidate-2', accuracy=1., latency_p95_s=2.728233528,
                     cost_usd_per_session=.0014715555555555556, cost_usd_per_success=.0014715555555555556)
    winner, decisions = choose_joint(base, [first, second], dict(PREFERENCES, cost_metric='cost_usd_per_success'))
    assert winner is first
    assert all(d['selection_cost_metric'] == 'cost_usd_per_success' for d in decisions)
    assert not any(d['needs_review'] for d in decisions)


def test_ranked_winner_becomes_next_generation_incumbent():
    base = baseline()
    scores = {'original': base,
              'cheap': replace(base, prompt='cheap', cost_usd_per_session=.07, latency_p95_s=9),
              'fast': replace(base, prompt='fast', cost_usd_per_session=.09, latency_p95_s=7)}
    seen = []
    def mutate(prompt, *_):
        seen.append(prompt)
        return ['fast', 'cheap']
    result = run_hill_climb(seed_prompt='original', mutate_fn=mutate,
        fanout_score_fn=lambda p: scores[p], candidates_per_generation=2, max_generations=2,
        min_accuracy_gain_to_continue=0, selection_policy=PREFERENCES)
    assert seen == ['original', 'cheap']
    assert result.best is scores['cheap']
    assert result.generations[0].decisions[1]['selected']
    assert not any(d['selected'] for d in result.generations[1].decisions)


@pytest.mark.parametrize('order', [(0,1),(1,0)])
def test_tradeoff_does_not_veto_candidate_that_improves_both(order):
    base=baseline()
    tradeoff=replace(base,prompt='cheapest-but-slower',cost_usd_per_session=.06,latency_p95_s=12)
    improvement=replace(base,prompt='cheaper-and-faster',cost_usd_per_session=.07,latency_p95_s=9)
    candidates=[(tradeoff,improvement)[i] for i in order]
    winner,decisions=choose_joint(base,candidates,PREFERENCES)
    assert winner is improvement
    by_prompt={d['prompt']:d for d in decisions}
    assert by_prompt[improvement.prompt]['selected']
    assert by_prompt[tradeoff.prompt]['accepted'] and by_prompt[tradeoff.prompt]['needs_review']


def test_joint_hillclimb_advances_past_competing_tradeoff():
    base=baseline()
    scores={'original':base,
            'tradeoff':replace(base,prompt='tradeoff',cost_usd_per_session=.06,latency_p95_s=12),
            'improved':replace(base,prompt='improved',cost_usd_per_session=.07,latency_p95_s=9)}
    seen=[]
    def mutate(prompt,*_):
        seen.append(prompt)
        return ['tradeoff','improved']
    result=run_hill_climb(seed_prompt='original',mutate_fn=mutate,
        fanout_score_fn=lambda p:scores[p],candidates_per_generation=2,max_generations=1,
        min_accuracy_gain_to_continue=0,selection_policy=PREFERENCES)
    assert result.best.prompt=='improved'
    assert result.generations[0].decisions[0]['needs_review']


@pytest.mark.parametrize('cost,latency', [(.099,9.99),(.1,9.99),(.099,10)])
def test_preferences_allow_small_or_single_objective_improvements(cost,latency):
    base=baseline()
    candidate=replace(base,prompt='new',cost_usd_per_session=cost,latency_p95_s=latency)
    winner,decisions=choose_joint(base,[candidate],PREFERENCES)
    assert winner is candidate and decisions[0]['selected']


def test_preferences_accept_demo_gains_below_old_latency_cutoff():
    base=replace(baseline(),cost_usd_per_session=.0061613475,latency_p95_s=8.13028047)
    candidate=replace(base,prompt='new',cost_usd_per_session=.0056484505,latency_p95_s=7.744074347)
    assert choose_joint(base,[candidate],PREFERENCES)[0] is candidate
    assert choose_joint(base,[candidate],POLICY)[0] is base


def test_preferences_expose_conflicting_tradeoff_without_rejecting_it():
    base=baseline();candidate=replace(base,prompt='cheaper',cost_usd_per_session=.09,latency_p95_s=11)
    winner,decisions=choose_joint(base,[candidate],PREFERENCES)
    assert winner is base
    assert decisions[0]['accepted'] and decisions[0]['needs_review']
    assert not decisions[0]['reasons']


@pytest.mark.parametrize('cost,latency', [(.1,10),(.11,11)])
def test_preferences_retain_incumbent_for_ties_and_worse_results(cost,latency):
    base=baseline();candidate=replace(base,prompt='new',cost_usd_per_session=cost,latency_p95_s=latency)
    winner,decisions=choose_joint(base,[candidate],PREFERENCES)
    assert winner is base and not decisions[0]['needs_review']


@pytest.mark.parametrize('changes', [{'accuracy':.99},{'eligible':False},{'cost_usd_per_session':None}])
def test_preferences_preserve_quality_and_measurement_requirements(changes):
    base=baseline();candidate=replace(base,prompt='new',latency_p95_s=9,cost_usd_per_session=.09)
    winner,decisions=choose_joint(base,[replace(candidate,**changes)],PREFERENCES)
    assert winner is base and not decisions[0]['accepted']


def test_interleaved_trials_fresh_phase_resume_and_median(tmp_path):
    cfg=load(ROOT/'examples/customer-support/input.yaml')
    cfg['document']['execution']['repetitions']=2
    runner=Runner(cfg,tmp_path)
    original=runner.execute; seen=[]
    def execute(payload):
        seen.append(payload['prompt'])
        return original(payload)
    runner.execute=execute
    variants=[(cfg['target'],'one'),(cfg['target'],'two')]
    groups=runner.evaluate_group(variants,'validation','compare-1')
    assert len(seen)==len(groups[0])*2 and seen.count('one')==seen.count('two')
    assert set(r['id'] for r in groups[0]).isdisjoint(r['id'] for r in groups[1])
    runner.execute=lambda _:pytest.fail('resume must not reexecute')
    assert runner.evaluate_group(variants,'validation','compare-1')==groups
    rows=copy.deepcopy(groups[0][:2])
    rows[0]['result']['latency_ms']=1000; rows[1]['result']['latency_ms']=3000
    assert summarize(cfg,'one',rows).latency_p50_s==2
    assert summarize(cfg,'one',rows).cost_usd_per_session is None


def test_run_spend_counts_optimizer_and_agent_once():
    usage={'input_tokens':10,'output_tokens':1}
    records=[{'trial_id':'x','status':'completed','model_requested':'test','route_calls':[{'usage':usage}]},
             {'text':'prompt','usage':usage,'model_id':'m','purpose':'optimizer'}]
    costs=run_costs({'routes':{'m':ROUTE}},records)
    assert costs['estimated_model_cost_usd']==pytest.approx(2*price_usage(ROUTE,usage))
    assert costs['by_purpose_usd']['optimizer']>0


def test_unfinished_judge_keeps_known_spend_but_prevents_complete_cost_claim():
    usage = {'input_tokens':10, 'output_tokens':1}
    records = [{'text':'answer', 'usage':usage, 'model_id':'m', 'purpose':'judge'}]
    costs = run_costs({'routes':{'m':ROUTE}}, records, unresolved_attempts=1)
    assert costs['known_model_cost_usd'] == pytest.approx(price_usage(ROUTE, usage))
    assert costs['estimated_model_cost_usd'] is None
    assert costs['unpriced_requests_or_incomplete_traces'] == 1


@pytest.mark.parametrize('count', [-1, True, 1.5])
def test_invalid_unresolved_attempt_count_is_rejected(count):
    with pytest.raises(ValueError, match='unresolved_attempts'):
        run_costs({'routes':{}}, [], unresolved_attempts=count)


@pytest.mark.parametrize('value', [float('nan'),float('inf'),0,-.1,1,True])
def test_invalid_joint_thresholds_fail_closed(value):
    policy=dict(POLICY,min_cost_improvement_fraction=value)
    with pytest.raises(ValueError,match='finite fractions'):
        choose_joint(baseline(),[],policy)


@pytest.mark.parametrize('policy', [POLICY,PREFERENCES])
def test_no_case_limit_means_all_cases_with_joint_selection(tmp_path,policy):
    import shutil,yaml
    shutil.copytree(ROOT/'examples/customer-support',tmp_path/'example')
    path=tmp_path/'example/input.yaml';doc=yaml.safe_load(path.read_text())
    doc['execution'].update(max_cases_per_dataset=None,repetitions=20)
    doc['runner']['selection']=policy
    path.write_text(yaml.safe_dump(doc))
    assert load(path)['document']['execution']['max_cases_per_dataset'] is None


def test_nonfinite_threshold_is_rejected_during_preflight(tmp_path):
    import shutil,yaml
    shutil.copytree(ROOT/'examples/customer-support',tmp_path/'example')
    path=tmp_path/'example/input.yaml';doc=yaml.safe_load(path.read_text())
    doc['execution']['repetitions']=20
    doc['runner']['selection']=dict(POLICY,min_latency_improvement_fraction=float('nan'))
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(ValueError,match='NaN|finite fractions'):
        load(path)
