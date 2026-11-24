import copy,json
from dataclasses import replace
import pytest
from customer_eval.contract import ROOT,load
from customer_eval.runner import Runner
from customer_eval.feedback import development_feedback,mutation_evidence
from customer_eval.scoring import summarize
from customer_eval.selection import choose_joint
from hillclimb import CandidateScore,run_hill_climb
from optimizer import make_mutate_fn,build_meta_prompt


def fixture(tmp_path):
    config=load(ROOT/'examples/customer-support/input.yaml')
    rows=Runner(config,tmp_path).evaluate(config['target'],config['prompt'],'development')
    for row in rows:
        row['result'].update(simulated=False,route_calls=[{'usage':{'input_tokens':100,'output_tokens':20}}])
        row['checks']={k:{'pass':True,'reason':'ok'} for k in row['checks']}
    return config,rows


def test_passing_prompt_receives_measured_efficiency_feedback(tmp_path):
    c,rows=fixture(tmp_path);value=development_feedback(c,'prompt',rows)
    assert value['strategy']=='improve_efficiency'
    assert value['metrics']['input_tokens']==100*len(rows)
    assert value['metrics']['mean_model_cost_usd']>0 and value['examples']
    assert value['examples'][0]['expected']==c['datasets']['development'][0]['expected']
    seen=[]
    mutate=make_mutate_fn(lambda text:seen.append(text) or '["shorter"]',feedback_provider=lambda _:value)
    assert mutate('prompt',[],1)==['shorter']
    assert 'mean_model_cost_usd' in seen[0] and 'No observed behavior failures' in seen[0]
    assert 'push for robustness and clarity' not in seen[0]


def test_success_cost_feedback_includes_spend_on_failed_outcomes(tmp_path):
    c, rows = fixture(tmp_path)
    c['document']['runner']['selection'] = dict(mode='joint_cost_latency', min_validation_sessions=20,
                                               cost_metric='cost_usd_per_success')
    rows[0]['checks']['correctness'] = {'pass': False, 'reason': 'wrong outcome'}
    value = development_feedback(c, 'p', rows)
    metrics = value['metrics']
    assert metrics['selection_cost_metric'] == 'cost_usd_per_success'
    assert metrics['selection_cost_usd'] == pytest.approx(metrics['mean_model_cost_usd']/metrics['accuracy'])
    assert metrics['selection_cost_usd'] > metrics['mean_model_cost_usd']
    for row in rows:
        row['checks']['correctness'] = {'pass': False, 'reason': 'wrong outcome'}
    failed = development_feedback(c, 'all-wrong', rows)
    assert failed['metrics']['selection_cost_usd'] is None
    history = mutation_evidence('p', {'p': value, 'all-wrong': failed})['previous_candidates'][0]
    assert history['relative_to_incumbent']['selection_cost_usd'] is None


def test_quality_feedback_contains_actual_expected_and_tool_evidence(tmp_path):
    c,rows=fixture(tmp_path);rows[0]['checks']['correctness']={'pass':False,'reason':'wrong outcome'}
    value=development_feedback(c,'prompt',rows)
    assert value['strategy']=='repair_quality' and value['behavior_failure_count']==1
    assert all(k in value['examples'][0] for k in ('user_turns','expected','actual_outputs','tool_calls'))


@pytest.mark.parametrize('split',['validation','holdout'])
def test_feedback_rejects_non_development_even_if_case_id_is_reused(tmp_path,split):
    c,rows=fixture(tmp_path);rows[0]['split']=split
    with pytest.raises(ValueError,match='development'):development_feedback(c,'p',rows)


def test_feedback_rejects_unknown_case(tmp_path):
    c,rows=fixture(tmp_path);rows[0]['case_id']='holdout_secret'
    with pytest.raises(ValueError,match='development'):development_feedback(c,'p',rows)


def test_infrastructure_and_grader_errors_are_not_prompt_repair(tmp_path):
    c,rows=fixture(tmp_path)
    for row in rows:row['checks']['correctness']={'pass':False,'error':True,'failure_category':'grading','reason':'judge unavailable'}
    value=development_feedback(c,'p',rows)
    assert value['strategy']=='diagnose_execution' and not value['examples'] and value['behavior_failure_count']==0


def test_rejected_candidate_development_history_reaches_next_mutation(tmp_path):
    c,rows=fixture(tmp_path);a=development_feedback(c,'original',rows)
    bad=copy.deepcopy(a);bad['prompt']='longer';bad['metrics']['mean_model_cost_usd']*=2
    context=mutation_evidence('original',{'original':a,'longer':bad})
    assert context['previous_candidates'][0]['relative_to_incumbent']['mean_model_cost_usd']==1
    text=build_meta_prompt('original',[],2,feedback=context)
    assert 'longer' in text and 'Positive relative cost or latency means worse' in text
    assert 'validation_' not in text and 'holdout_' not in text


def test_patience_allows_second_generation_to_repair_first_bad_candidate():
    base=CandidateScore('original',1,latency_p95_s=10,cost_usd_per_session=.1,n_sessions=30)
    scores={'original':base,'bad':replace(base,prompt='bad',cost_usd_per_session=.2),'better':replace(base,prompt='better',cost_usd_per_session=.08,latency_p95_s=9)}
    turns=[]
    def mutate(p,r,k):turns.append(p);return ['bad' if len(turns)==1 else 'better']
    result=run_hill_climb(seed_prompt='original',mutate_fn=mutate,fanout_score_fn=scores.get,
        candidates_per_generation=1,max_generations=2,min_accuracy_gain_to_continue=.01,
        selection_policy={'mode':'joint_cost_latency','min_validation_sessions':20},patience=2)
    assert len(result.generations)==2 and result.best.prompt=='better' and turns==['original','original']
    assert result.stopped_reason == "max_generations reached"


def test_missing_conversation_timing_cannot_silently_use_worker_timing(tmp_path):
    c,rows=fixture(tmp_path);c['document']['runner']['measurement']={'primary_latency':'conversation'}
    score=summarize(c,'p',rows)
    assert score.latency_scope=='conversation' and score.latency_p95_s is None
    base=CandidateScore('base',1,latency_p95_s=10,cost_usd_per_session=.1,n_sessions=30,latency_scope='conversation')
    winner,decisions=choose_joint(base,[replace(score,n_sessions=30)],{'mode':'joint_cost_latency','min_validation_sessions':20})
    assert winner is base and 'latency measurement unavailable' in decisions[0]['reasons']


def test_different_latency_scopes_cannot_be_compared():
    a=CandidateScore('a',1,latency_p95_s=10,cost_usd_per_session=.1,n_sessions=30)
    b=replace(a,prompt='b',latency_scope='conversation',latency_p95_s=1,cost_usd_per_session=.01)
    assert choose_joint(a,[b],{'mode':'joint_cost_latency','min_validation_sessions':20})[0] is a


@pytest.mark.parametrize('protected_policy', ['', '  ', 'Always check the return-policy deadline.'])
def test_runner_optimizer_guidance_matches_injected_policy(tmp_path, protected_policy):
    from customer_eval import simulation

    config = load(ROOT / 'examples/customer-support/input.yaml')
    config['policy'] = protected_policy
    config['document']['optimization']['max_iterations'] = 1
    config['document']['runner']['candidates_per_generation'] = 1
    config['intake_context'] = {'quality': {'rules': 'Preserve all required business rules.'}}
    runner = Runner(config, tmp_path, backend='local')
    runner.execute = simulation.execute
    seen = []

    def call(model, text, purpose='judge'):
        assert purpose == 'optimizer'
        seen.append(text)
        return json.dumps(['check opened status; preserve the return-policy deadline'])

    runner.call = call
    runner.run()
    assert len(seen) == 1
    outgoing = seen[0]
    if protected_policy.strip():
        assert 'supplied separately to every application run' in outgoing
        assert 'do not duplicate them in the editable prompt' in outgoing
        assert protected_policy in outgoing
        assert 'injects no separate immutable policy' not in outgoing
    else:
        assert 'injects no separate immutable policy' in outgoing
        assert 'Preserve every required rule in the editable prompt' in outgoing
        assert 'not automatically injected into application requests' in outgoing
        assert 'do not duplicate them in the editable prompt' not in outgoing
    assert 'The immutable policy is already supplied; do not repeat it.' not in outgoing


def test_runner_passes_measured_development_history_without_split_leakage(tmp_path):
    from customer_eval import simulation
    c=load(ROOT/'examples/customer-support/input.yaml')
    c['document']['optimization']['max_iterations']=2
    c['document']['runner']['candidates_per_generation']=1
    rules = 'CUSTOMER_RULE: Preserve printed totals; flag a mismatch instead of repairing it.'
    c['intake_context'] = {'quality': {'rules': rules}, 'private_reference': 'INTAKE_PRIVATE_CANARY'}
    for split in ('validation','holdout'):
        for case in c['datasets'][split]:case['expected']['private_sentinel']=split.upper()+'_CANARY'
    runner=Runner(c,tmp_path,backend='local');seen=[]
    def execute(payload):
        assert rules not in json.dumps(payload) and 'INTAKE_PRIVATE_CANARY' not in json.dumps(payload)
        result=simulation.execute(payload)
        result.update(simulated=False,route_calls=[{'usage':{'input_tokens':100,'output_tokens':10}}])
        return result
    runner.execute=execute
    def call(model,text,purpose='judge'):
        assert purpose=='optimizer';seen.append(text)
        return json.dumps(['check opened status; candidate '+str(len(seen))])
    runner.call=call
    report=runner.run()
    assert len(seen)==2 and len(report['development_feedback'])==3
    assert all('mean_model_cost_usd' in text and 'input_tokens' in text for text in seen)
    assert 'candidate 1' in seen[1] and 'previous_candidates' in seen[1]
    assert all('VALIDATION_CANARY' not in text and 'HOLDOUT_CANARY' not in text for text in seen)
    assert all('validation_allow' not in text and 'holdout_allow' not in text for text in seen)
    assert all(rules in text and 'INTAKE_PRIVATE_CANARY' not in text for text in seen)


def test_retail_simulation_reports_unknown_timing_without_claiming_quality(tmp_path):
    c=load(ROOT/'examples/retail-returns/input.yaml')
    report=Runner(c,tmp_path).run(optimize=False)
    assert report['simulated'] and report['holdout']['target_original']['score']['latency_p95_s'] is None
    assert 'unknown' in (tmp_path/'report.md').read_text()


@pytest.mark.parametrize('missing', ['input_tokens', 'output_tokens'])
def test_incomplete_development_usage_is_unknown(tmp_path, missing):
    config, rows = fixture(tmp_path)
    del rows[0]['result']['route_calls'][0]['usage'][missing]
    metrics = development_feedback(config, 'prompt', rows)['metrics']
    assert metrics[missing] is None
    assert not metrics['usage_complete']


def test_execution_without_trace_does_not_lower_development_token_totals(tmp_path):
    config, rows = fixture(tmp_path)
    rows[0]['result'].update(status='infrastructure_error', route_calls=[])
    metrics = development_feedback(config, 'prompt', rows)['metrics']
    assert metrics['input_tokens'] is None and metrics['output_tokens'] is None


def test_runner_mutation_does_not_use_validation_to_rank_development_failures(tmp_path, monkeypatch):
    import hillclimb

    def validation_ranking(_):
        pytest.fail('Validation criterion pass rates reached mutation reasoning')

    monkeypatch.setattr(hillclimb, '_losing_reasons', validation_ranking)
    config = load(ROOT/'examples/customer-support/input.yaml')
    assert Runner(config, tmp_path).run()['optimization']['generations']
