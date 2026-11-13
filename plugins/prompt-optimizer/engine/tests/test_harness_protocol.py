"""Real harness + real MCP, simulated Responses server. No external API calls."""
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from customer_eval.worker import isolated


@pytest.mark.parametrize('harness', ['codex', 'strands', 'direct'])
@pytest.mark.parametrize('scenario', ['reuse', 'correction', 'retry'])
def test_real_harness_tool_and_continuation(harness, scenario, monkeypatch):
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            rid='resp_'+uuid.uuid4().hex
            tool=next((t for t in body.get('tools',[]) if t.get('name','').endswith('lookup_order')),None)
            namespace = next((t for t in body.get('tools',[]) if t.get('type')=='namespace'), None)
            if not tool and namespace:
                tool=next((t for t in namespace['tools'] if t['name']=='lookup_order'),None)
            needs_tool = len(requests)==1 or (scenario=='correction' and len(requests)==3) or (scenario=='retry' and len(requests)==2)
            if needs_tool and tool:
                order = 'B2' if scenario=='correction' and len(requests)==3 else 'A1'
                item={'type':'function_call','id':'fc_'+str(len(requests)),'call_id':'call_'+str(len(requests)),'name':tool['name'],'arguments':json.dumps({'order_id':order}),'status':'completed'}
                if namespace: item['namespace']=namespace['name']
            else:
                item={'type':'message','id':'msg_'+uuid.uuid4().hex,'role':'assistant','status':'completed',
                      'content':[{'type':'output_text','text':'{"decision":"deny"}','annotations':[]}]}
            response={'id':rid,'object':'response','created_at':1,'status':'completed','model':'mock-model',
                      'output':[item],'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20}}
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream' if body.get('stream') else 'application/json')
            self.end_headers()
            if body.get('stream'):
                events=[{'type':'response.created','response':dict(response,status='in_progress',output=[])},
                        {'type':'response.output_item.added','output_index':0,'item':dict(item,status='in_progress')}]
                if item['type']=='message':
                    events.extend([
                        {'type':'response.content_part.added','item_id':item['id'],'output_index':0,'content_index':0,'part':{'type':'output_text','text':'','annotations':[]}},
                        {'type':'response.output_text.delta','item_id':item['id'],'output_index':0,'content_index':0,'delta':'{"decision":"deny"}'},
                        {'type':'response.output_text.done','item_id':item['id'],'output_index':0,'content_index':0,'text':'{"decision":"deny"}'},
                        {'type':'response.content_part.done','item_id':item['id'],'output_index':0,'content_index':0,'part':item['content'][0]}])
                else:
                    events.append({'type':'response.function_call_arguments.done','item_id':item['id'],'output_index':0,'arguments':item['arguments']})
                events.extend([{'type':'response.output_item.done','output_index':0,'item':item},
                               {'type':'response.completed','response':response}])
                for sequence,event in enumerate(events):
                    event['sequence_number']=sequence
                    self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode());self.wfile.flush()
            else:
                self.wfile.write(json.dumps(response).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('EVAL_PROTOCOL_TOKEN','test-token-no-value')
    second = 'Now use order B2.' if scenario=='correction' else 'Repeat your decision.'
    payload={'trial_id':'protocol-test','harness':harness,'prompt':'Use lookup_order before answering. Respond in JSON.',
             'policy':'Do not refund opened items.','turns':['Look up order A1. Can it be returned?',second],
             'initial_state':{'orders':{'A1':{'opened':True,'age_days':5},'B2':{'opened':False,'age_days':10}},
                              'lookup_errors':{'A1':['temporarily_unavailable']} if scenario=='retry' else {}},'workload':'customer_support' if harness!='direct' else 'none',
             'route':{'model_id':'mock-model','base_url':f'http://127.0.0.1:{server.server_port}/v1',
                      'auth':'bearer','credential_ref':'env:EVAL_PROTOCOL_TOKEN','parameters':{'reasoning':None}},
             'limits':{'max_requests':6,'max_request_bytes':100000,'max_output_tokens':1024}}
    try: result=isolated(payload,45)
    finally: server.shutdown();server.server_close();thread.join()
    assert result['status']=='completed', json.dumps(result)
    assert len(result['outputs'])==2
    from customer_eval.turn_grading import trace_errors
    assert not trace_errors({'turns':payload['turns']},result)
    if harness!='direct':
        assert any(c['name']=='lookup_order' for c in result['tool_calls']),json.dumps({'result':result,'tools':[r.get('tools') for r in requests]})
        assert len(requests)>=3
        records=result['turn_records']
        assert records[0]['tool_calls'][0]['arguments']=={'order_id':'A1'}
        if scenario=='correction':
            assert records[1]['tool_calls'][0]['arguments']=={'order_id':'B2'}
        else:
            assert not records[1]['tool_calls']
        if scenario=='retry':
            assert [c['result'] for c in records[0]['tool_calls']]==[{'error':'temporarily_unavailable'},{'opened':True,'age_days':5}]
    assert second in json.dumps(requests[-1])
    assert all(r['model']=='mock-model' for r in requests)
    assert all('reasoning' not in r for r in requests)
    for request, call in zip(requests, result['route_calls']):
        expected = [dict(type=t.get('type'), name=t.get('name'),
            **({'functions': [f.get('name') for f in t.get('tools', [])]}
               if t.get('type') == 'namespace' else {})) for t in request.get('tools', [])]
        assert call['effective_tools'] == expected
    timing=result['timing']
    assert timing['worker_ms']==pytest.approx(timing['setup_ms']+timing['conversation_ms']+timing['teardown_ms'])
    assert timing['conversation_ms']>0 and timing['setup_ms']>0 and timing['teardown_ms']>=0
    for call in result['route_calls']:
        t=call['timing']
        assert t['provider_ms']>=t['headers_ms']>=0 and t['auth_ms']>=0
        assert 'sent_ns' not in t
        if harness=='codex' or harness=='strands':
            assert t['first_event_ms']<=t['first_output_ms']<=t['provider_ms']
