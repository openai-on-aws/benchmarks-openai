import copy
import gzip
import json
from contextlib import contextmanager
from io import BytesIO

import pytest
import yaml

from customer_eval.contract import ROOT, load, trial_payload
from customer_eval.log_import import ImportIssue, import_logs, normalize, records, write_import
from customer_eval.replay import fingerprint, request_body, validate_replay
from customer_eval.transport import constrain
from scripts.build_log_examples import FORMATS, PROMPT, example


@pytest.mark.parametrize('fi',range(6))
def test_each_format_preserves_context_and_structured_output(fi):
    row=example(fi,2)
    mapping={'id':'trace.id','request':'application.request_payload','response':'application.response_payload'} if fi==4 else None
    case=normalize(row,mapping=mapping)
    replay=case['replay']; original=copy.deepcopy(replay)
    assert case['metadata']['format']==FORMATS[fi]
    assert validate_replay(replay)==PROMPT
    assert case['metadata']['label_status']=='unreviewed'
    body=request_body(replay,'replacement')
    location=replay['prompt_location']
    if location=='instructions':
        assert body['instructions']=='replacement' and body['input']==replay['request']['input']
    else:
        assert body['input'][location]['content']=='replacement'
        assert body['input'][location+1:]==replay['request']['input'][location+1:]
    assert body['text']['format']['strict'] and body['max_output_tokens']==2048
    assert replay==original
    assert case['expected']['answer']['invoice_id']==f'OCR-{fi+1}-003'


@pytest.mark.parametrize('change,reason',[
    (lambda r:r['request'].update(stop=['END']),'unsupported_request_setting'),
    (lambda r:r['request'].update(previous_response_id='missing-history'),'unsupported_request_setting'),
    (lambda r:r['request'].update(tools=[{'type':'function'}]),'tool_calls_unsupported'),
    (lambda r:r['request']['messages'][-1].update(content=[{'type':'input_file','file_id':'x'}]),'unsupported_or_empty_content'),
    (lambda r:r['request']['messages'].insert(1,{'role':'tool','content':'result'}),'unsupported_message_or_tool_history'),
    (lambda r:r['response']['choices'][0].update(finish_reason='length'),'incomplete_or_tool_response'),
    (lambda r:r['response'].update(error={'message':'failure'}),'incomplete_or_failed_response'),
    (lambda r:r['request'].update(max_tokens=42),'conflicting_output_limits'),
    (lambda r:r['request']['messages'][-1].update(role='assistant'),'invalid_replay_or_missing_prompt'),
])
def test_no_silent_loss(change,reason):
    row=example(2,0);change(row)
    with pytest.raises(ImportIssue,match=reason):normalize(row)


@pytest.mark.parametrize('container',['single','array','data','jsonl','gzip'])
def test_containers_do_not_mistake_messages_for_container(tmp_path,container):
    row=example(1,0)
    value=json.dumps([row] if container=='array' else {'data':[row]} if container=='data' else row)
    path=tmp_path/'input'
    path.write_bytes(gzip.compress(value.encode()) if container=='gzip' else (value+'\n').encode())
    cases,report=import_logs([path]);assert len(cases)==1 and report['reconciles']
    assert len(cases[0]['replay']['request']['input'])==4


def test_invalid_duplicate_and_conflicting_rows_are_accounted_for(tmp_path):
    row=example(2,0);same=copy.deepcopy(row);same['id']='different-id'
    conflicting=copy.deepcopy(same);conflicting['response']['choices'][0]['message']['content']='{"wrong":true}'
    path=tmp_path/'a.jsonl'
    path.write_text('\n'.join([json.dumps(row),json.dumps(same),json.dumps(conflicting),'42','{"id":1,"id":2}','{"x":NaN}','bad']))
    cases,report=import_logs([path]);assert len(cases)==1
    assert report['found']==7 and report['dropped']==6 and report['reconciles']
    assert set(report['drop_reasons'])=={'duplicate_request','conflicting_reference','non_object_row','duplicate_json_key','nonfinite_json_number','malformed_json'}


def test_prompt_grouping_and_immutable_import(tmp_path):
    a=normalize(example(2,0));row=example(2,1);row['request']['messages'][0]['content']='A different prompt'
    b=normalize(row);report=write_import([a,b],{},tmp_path/'output')
    assert len(report['prompt_groups'])==2
    with pytest.raises(ValueError,match='immutable'):write_import([a],{},tmp_path/'output')


def test_chat_text_parts_are_translated_without_losing_role_order():
    row=example(2,0)
    for m in row['request']['messages']:m['content']=[{'type':'text','text':m['content']}]
    case=normalize(row);messages=case['replay']['request']['input']
    assert [m['role'] for m in messages]==['system','user','assistant','user']
    assert [m['content'][0]['type'] for m in messages]==['input_text','input_text','output_text','input_text']


def test_flat_sdk_settings_cannot_be_silently_dropped():
    row=example(1,0);row['stop']=['STOP']
    with pytest.raises(ImportIssue,match='unsupported_request_setting'):normalize(row)


def test_flat_chat_and_serialized_spendlog_body():
    row=example(2,0)
    flat=dict(row['request'],**row['response'])
    assert normalize(flat)['replay']['request']==normalize(row)['replay']['request']
    spend=example(0,0);spend['proxy_server_request']['body']=json.dumps(spend['proxy_server_request']['body'])
    assert normalize(spend)['metadata']['format']=='litellm_spendlogs'


def test_config_and_model_payload_exclude_labels_and_capture_budget():
    config=load(ROOT/'examples/log-replay/input.yaml')
    case=copy.deepcopy(config['datasets']['holdout'][0]);case['expected']['answer']='PRIVATE_HOLDOUT_LABEL'
    payload=trial_payload(config,config['target'],config['prompt'],case,'trial')
    assert 'PRIVATE_HOLDOUT_LABEL' not in json.dumps(payload)
    assert payload['harness']=='direct' and payload['replay']==case['replay']
    body=request_body(payload['replay'],payload['prompt'])
    route=dict(payload['route'],allowed_tool_names=[])
    effective=constrain(body,route,payload['limits'])
    assert effective['input']==body['input'] and effective['store'] is False
    assert effective['model']=='us.openai.gpt-6.1-sol'
    assert effective['max_output_tokens']==2048


def test_different_recorded_context_is_not_a_false_duplicate(tmp_path):
    import shutil
    shutil.copytree(ROOT/'examples/log-replay',tmp_path/'example')
    path=tmp_path/'example/data/validation.jsonl';rows=[json.loads(l) for l in path.read_text().splitlines()]
    # Same last question, distinct preceding evidence is a different captured request.
    rows[1]['turns']=rows[0]['turns'];rows[1]['input']=rows[0]['input']
    rows[1]['replay']['request']['input'][-1]['content']=rows[0]['turns'][0]
    rows[1]['replay']['request']['input'].insert(-1,{'role':'user','content':'Additional evidence specific to this case.'})
    rows[1]['replay']['request_sha256']=fingerprint(rows[1]['replay']['request'])
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    assert load(tmp_path/'example/input.yaml')['datasets']['validation']


def test_unmarked_replay_cannot_silently_use_conversation_mode(tmp_path):
    import shutil
    shutil.copytree(ROOT/'examples/log-replay',tmp_path/'example')
    p=tmp_path/'example/input.yaml';cfg=yaml.safe_load(p.read_text());cfg['runner'].pop('input_mode');p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError,match='input_mode'):load(p)


def test_worker_replays_prior_answers_once_and_records_overrides(monkeypatch):
    from customer_eval import worker
    config=load(ROOT/'examples/log-replay/input.yaml');case=config['datasets']['validation'][0]
    payload=trial_payload(config,config['target'],config['prompt'],case,'test-worker')
    calls=[{'status':'completed','usage':{'input_tokens':10,'output_tokens':10}}]
    @contextmanager
    def relay(*args):yield 'http://localhost','test',calls
    seen=[]
    def open_request(request):
        seen.append(json.loads(request.data))
        return BytesIO(json.dumps({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'{"ok":true}'}]}]}).encode())
    monkeypatch.setattr(worker,'relay',relay);monkeypatch.setattr(worker.urllib.request,'urlopen',open_request)
    result=worker.execute(payload)
    assert result['status']=='completed' and len(seen)==1
    assert seen[0]['input']==case['replay']['request']['input']
    assert result['outputs']==['{"ok":true}'] and result['replay']['settings_overrides']['model']['effective']==config['routes']['m2']['model_id']


def test_feedback_has_captured_context_but_only_development_cases(tmp_path):
    from customer_eval.feedback import development_feedback
    config=load(ROOT/'examples/log-replay/input.yaml');case=config['datasets']['development'][0]
    row={'case_id':case['id'],'split':'development','model_id':'m2','repeat':0,
         'checks':{'extraction':{'pass':False,'reason':'mismatch'}},
         'result':{'status':'completed','outputs':['{}'],'route_calls':[{'status':'completed','usage':{'input_tokens':100,'output_tokens':10}}],'latency_ms':1000}}
    feedback=development_feedback(config,config['prompt'],[row])
    assert feedback['examples'][0]['recorded_request']['input']==case['replay']['request']['input']
    assert all(c['id'] not in json.dumps(feedback) for c in config['datasets']['holdout'])


def test_optimizer_has_separate_output_budget(monkeypatch,tmp_path):
    from customer_eval import runner
    config=load(ROOT/'examples/log-replay/input.yaml');seen=[]
    def complete(route,text,limits,*,receipt=None):
        seen.append((route,limits));return '[]',{'input_tokens':1,'output_tokens':1}
    monkeypatch.setattr(runner,'complete',complete)
    obj=runner.Runner(config,tmp_path/'run',backend='local')
    assert obj.call('optimizer','test',purpose='optimizer')=='[]'
    assert seen[0][0]['parameters']['reasoning']['effort']=='high'
    assert seen[0][1]['max_output_tokens']==8192 and config['document']['runner']['limits']['max_output_tokens']==2048
