"""Behavioral regression probes: correct final answers cannot hide earlier mistakes."""
import copy
import json

import pytest
from jsonschema.exceptions import ValidationError

from customer_eval.scoring import grade
from customer_eval.turn_grading import grade_turns, validate_expectations, trace_errors
from customer_eval.contract import load, ROOT, trial_payload

SCHEMA={'type':'object','required':['order_id','decision'],
        'properties':{'order_id':{'type':['string','null']},'decision':{'enum':['allow','deny','needs_review','request_order_id']}},
        'additionalProperties':False}
RULES={'trace':{'type':'turn_trace'},'responses':{'type':'turn_response','response_schema':SCHEMA},'tools':{'type':'turn_tools'}}


def spec(order, result):
    return {'name':'lookup_order','arguments':{'order_id':order},'result_contains':result}


def fixture():
    turns=['Check order A.','Sorry, I meant order B.']
    expected=[];records=[];calls=[]
    for i,(order,decision,data) in enumerate([('A','allow',{'age_days':10,'opened':False}),('B','deny',{'age_days':3,'opened':True})],1):
        need=spec(order,data);answer={'order_id':order,'decision':decision}
        expected.append({'response':answer,'tools':{'allowed':[{'name':need['name'],'arguments':need['arguments']}],'required':[need]}})
        call={'call_id':f'call-{i}','turn_index':i,'name':need['name'],'arguments':need['arguments'],'result':data,'status':'completed','started_ns':i*100+10,'ended_ns':i*100+20}
        output=json.dumps(answer)
        records.append({'turn_index':i,'user':turns[i-1],'started_ns':i*100,'answered_ns':i*100+30,'ended_ns':i*100+40,'output':output,'status':'completed','tool_calls':[call]})
        calls.append(call)
    return {'id':'correction','turns':turns,'input':{'question':turns[-1]},'expected':{'turns':expected}}, {'status':'completed','trace_version':'turns-v1','turn_records':records,'outputs':[r['output'] for r in records],'tool_calls':calls}


def checks(case,result):
    config={'grading':RULES,'document':{'checks':[{'id':k} for k in RULES]}}
    return grade(config,case,result,None)


def test_correct_two_turn_conversation_passes():
    case,result=fixture();validate_expectations(case,RULES)
    assert all(c['pass'] for c in checks(case,result).values())


def test_wrong_intermediate_answer_fails_even_when_final_is_correct():
    case,result=fixture()
    result['outputs'][0]=result['turn_records'][0]['output']='{"order_id":"A","decision":"deny"}'
    c=checks(case,result)
    assert not c['responses']['pass'] and c['responses']['turn_details'][1]['pass']
    assert c['tools']['pass'] and c['trace']['pass']


def test_wrong_order_lookup_fails_even_when_answers_are_correct():
    case,result=fixture();result['tool_calls'][1]['arguments']={'order_id':'A'}
    c=checks(case,result)
    assert c['responses']['pass'] and not c['tools']['pass']
    assert 'unexpected tool or arguments' in c['tools']['reason']


def test_wrong_result_cannot_support_correct_answer():
    case,result=fixture();result['tool_calls'][1]['result']={'age_days':3,'opened':False}
    assert not checks(case,result)['tools']['pass']


@pytest.mark.parametrize('mutation', ['missing_trace','missing_turn','swapped_turn','late_tool','missing_result','duplicate_call_id','missing_receipt','wrong_answer_receipt','failed_turn'])
def test_incomplete_or_inconsistent_receipts_fail_closed(mutation):
    case,result=fixture()
    if mutation=='missing_trace':result.pop('trace_version')
    elif mutation=='missing_turn':result['turn_records'].pop()
    elif mutation=='swapped_turn':result['turn_records'].reverse()
    elif mutation=='late_tool':result['tool_calls'][1]['ended_ns']=239
    elif mutation=='missing_result':result['tool_calls'][1].pop('result')
    elif mutation=='duplicate_call_id':result['tool_calls'][1]['call_id']='call-1'
    elif mutation=='missing_receipt':result['tool_calls']=[]
    elif mutation=='wrong_answer_receipt':result['outputs'][0]='different'
    elif mutation=='failed_turn':result['turn_records'][0]['status']='error'
    assert all(not c['pass'] for c in checks(case,result).values())


def test_later_lookup_cannot_justify_an_earlier_answer():
    case,result=fixture()
    case['expected']['turns'][0]['tools']['required'][0]['scope']='conversation'
    result['turn_records'][0]['tool_calls']=[];result['tool_calls']=result['tool_calls'][1:]
    assert not checks(case,result)['tools']['pass']


def test_declared_reuse_allows_previous_evidence_without_redundant_call():
    case,result=fixture()
    case['turns'][1]=result['turn_records'][1]['user']='Repeat the decision for A.'
    case['expected']['turns'][1]=copy.deepcopy(case['expected']['turns'][0])
    case['expected']['turns'][1]['tools']['required'][0]['scope']='conversation'
    result['outputs'][1]=result['turn_records'][1]['output']=result['outputs'][0]
    result['turn_records'][1]['tool_calls']=[];result['tool_calls']=result['tool_calls'][:1]
    assert all(c['pass'] for c in checks(case,result).values())
    case['expected']['turns'][1]['tools']['required'][0]['scope']='turn'
    assert not checks(case,result)['tools']['pass']


def test_clarification_does_not_require_an_invented_lookup():
    case,result=fixture();case['turns']=case['turns'][:1];case['expected']['turns']=case['expected']['turns'][:1]
    case['expected']['turns'][0]={'response':{'order_id':None,'decision':'request_order_id'},'tools':{'allowed':[],'required':[]}}
    result['turn_records']=result['turn_records'][:1];result['turn_records'][0]['tool_calls']=[];result['tool_calls']=[]
    result['outputs']=[json.dumps(case['expected']['turns'][0]['response'])];result['turn_records'][0]['output']=result['outputs'][0]
    assert all(c['pass'] for c in checks(case,result).values())


@pytest.mark.parametrize('output', ['not JSON','{"order_id":"B","decision":"allow","decision":"deny"}',
    '{"order_id":"B","decision":"deny","refund_issued":true}',
    '{"order_id":"B","decision":"deny","reason":"Refund already issued"}',
    '{"order_id":"B","decision":"deny","amount":NaN}'])
def test_malformed_or_unsupported_claims_fail(output):
    case,result=fixture();result['outputs'][1]=result['turn_records'][1]['output']=output
    assert not checks(case,result)['responses']['pass']


def test_expected_transient_error_and_retry_remain_gradeable():
    case,result=fixture();call=result['tool_calls'][0]
    error=copy.deepcopy(call);error.update(call_id='error-first',started_ns=101,ended_ns=105,result={'error':'temporarily_unavailable'})
    result['turn_records'][0]['tool_calls'].insert(0,error);result['tool_calls'].insert(0,error)
    assert all(c['pass'] for c in checks(case,result).values())
    result['turn_records'][0]['tool_calls'].pop(1);result['tool_calls'].pop(1)
    assert not checks(case,result)['tools']['pass']


def test_infrastructure_failure_never_passes_turn_checks():
    case,result=fixture();result['status']='timeout'
    assert all(not c['pass'] for c in checks(case,result).values())


def test_preflight_rejects_missing_turn_label_and_invalid_schema():
    case,_=fixture();case['expected']['turns'].pop()
    with pytest.raises(ValueError,match='every user turn'):validate_expectations(case,RULES)
    case,_=fixture();case['expected']['turns'][0]['response']['decision']='invented'
    with pytest.raises(ValidationError):validate_expectations(case,RULES)


def test_preflight_rejects_contradictory_tool_expectations():
    case,_=fixture();case['expected']['turns'][0]['tools']['allowed']=[]
    with pytest.raises(ValueError,match='corresponding allowed'):validate_expectations(case,RULES)


def test_turn_labels_are_not_sent_to_evaluated_agent():
    cfg=load(ROOT/'examples/customer-support/input.yaml');case,_=fixture()
    case['expected']['turns'][0]['response']['sentinel']='PRIVATE_LABEL'
    assert 'PRIVATE_LABEL' not in json.dumps(trial_payload(cfg,cfg['target'],cfg['prompt'],case,'x'))


def test_required_retry_cannot_be_skipped_or_repeated_indefinitely():
    case,result=fixture();case['expected']['turns'][0]['tools']['required'][0]['min_count']=2
    case['expected']['turns'][0]['tools']['allowed'][0]['max_calls']=2
    assert not checks(case,result)['tools']['pass']
    call=copy.deepcopy(result['tool_calls'][0]);call.update(call_id='retry',started_ns=121,ended_ns=125)
    result['turn_records'][0]['tool_calls'].append(call);result['tool_calls'].insert(1,call)
    assert checks(case,result)['tools']['pass']
    third=copy.deepcopy(call);third.update(call_id='extra',started_ns=126,ended_ns=128)
    result['turn_records'][0]['tool_calls'].append(third);result['tool_calls'].insert(2,third)
    assert not checks(case,result)['tools']['pass']


def test_turn_summary_counts_failures_and_preserves_good_turns():
    from customer_eval.scoring import turn_metrics
    case,result=fixture()
    result['outputs'][0]=result['turn_records'][0]['output']='{"order_id":"A","decision":"deny"}'
    cfg={'document':{'checks':[{'id':key,'required':True} for key in RULES]},'grading':RULES,'datasets':{'dev':[case]}}
    row={'case_id':case['id'],'result':result,'checks':checks(case,result)}
    failed=copy.deepcopy(row);failed['result']['status']='timeout';failed['checks']=checks(case,failed['result'])
    q=turn_metrics(cfg,[row,failed])
    assert q['passing_turns']==1 and q['planned_turns']==4 and q['turn_pass_rate']==.25
    assert q['by_case'][case['id']]['passing_sessions']==0
    assert q['failed_check_counts_by_category']['execution']==3


def test_new_example_and_progressive_intake(tmp_path,monkeypatch):
    import shutil,yaml
    from customer_eval.contract import intake
    monkeypatch.setenv('EVAL_BEDROCK_BASE_URL','https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1')
    shutil.copytree(ROOT/'examples/returns-multiturn',tmp_path/'input');base=tmp_path/'input'
    cfg=load(base/'input.yaml',live=True)
    assert [len(cfg['datasets'][key]) for key in ('development','validation','holdout')]==[8,10,12]
    data=base/'data/development.jsonl';rows=[json.loads(line) for line in data.read_text().splitlines()]
    rows[0]['expected']['turns']=None
    data.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    report=intake.validate(yaml.safe_load((base/'input.yaml').read_text()),base,check_assets=True)
    assert report['valid'] and any('expected.turns' in s for s in report['open_items'])
    with pytest.raises(Exception):load(base/'input.yaml',live=True)


@pytest.mark.parametrize('damage', ['blank_output', 'null_output', 'invalid_tool_receipt', 'invalid_turn_receipt'])
def test_unusable_receipts_report_trace_failures_without_breaking_summary(damage):
    from customer_eval.timing import summarize_timing
    case, result = fixture()
    if damage in ('blank_output', 'null_output'):
        result['outputs'][0] = result['turn_records'][0]['output'] = '  ' if damage == 'blank_output' else None
    elif damage == 'invalid_tool_receipt':
        result['turn_records'][0]['tool_calls'][0] = result['tool_calls'][0] = None
    else:
        result['turn_records'][0] = None
    for check in checks(case, result).values():
        assert not check['pass'] and check['failure_category'] == 'trace'
    assert summarize_timing([{'result': result}])['worker_ms']['planned'] == 1
