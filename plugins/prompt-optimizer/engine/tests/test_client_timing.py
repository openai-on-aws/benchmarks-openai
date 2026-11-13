import json,threading,time,urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from customer_eval import transport
from customer_eval.timing import finish_worker_timing,summarize_timing


def test_worker_partition_and_unfinished_turns():
    r={'turn_records':[{'started_ns':2000000,'answered_ns':8000000}]}
    finish_worker_timing(r,1000000,10000000)
    assert [r['timing'][k] for k in ['setup_ms','conversation_ms','teardown_ms','worker_ms']]==[1,6,2,9]
    r={'turn_records':[{'started_ns':2}]};finish_worker_timing(r,1,10)
    assert r['timing']['conversation_ms'] is None


@pytest.mark.parametrize('stream,fail',[(True,False),(False,False),(True,True)])
def test_provider_auth_first_output_and_failure_timing(monkeypatch,stream,fail):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert body['reasoning']=={'effort':'low'}
            self.send_response(200);self.send_header('Content-Type','text/event-stream' if stream else 'application/json');self.send_header('x-amzn-requestid','timing-test');self.end_headers()
            result={'model':'test-model','status':'completed','service_tier':'default','output':[],'usage':{'input_tokens':10,'output_tokens':2}}
            if stream:
                self.wfile.write(b'data: {"type":"response.created"}\n\n');self.wfile.flush();time.sleep(.03)
                self.wfile.write(b'data: {"type":"response.output_text.delta","delta":"OK"}\n\n');self.wfile.flush();time.sleep(.02)
                if not fail:self.wfile.write(('data: '+json.dumps({'type':'response.completed','response':result})+'\n\n').encode())
            else:self.wfile.write(json.dumps(result).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    original=transport.authorize
    def delayed(*args):time.sleep(.02);return original(*args)
    monkeypatch.setattr(transport,'authorize',delayed);monkeypatch.setenv('TEST_TIMING_TOKEN','test')
    route={'base_url':f'http://127.0.0.1:{server.server_port}','model_id':'test-model','auth':'bearer','credential_ref':'env:TEST_TIMING_TOKEN','parameters':{'reasoning':{'effort':'low'},'service_tier':'default'}}
    try:
        with transport.relay(route,{'max_request_bytes':10000,'max_output_tokens':10,'max_requests':1}) as (url,token,calls):
            req=urllib.request.Request(url+'/responses',data=json.dumps({'input':'test','stream':stream}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            with urllib.request.urlopen(req) as response:response.read()
    finally:server.shutdown();server.server_close();thread.join()
    call=calls[0];t=call['timing'];assert t['auth_ms']>=15 and t['headers_ms']>=0 and 'sent_ns' not in t
    assert call['request_id']=='timing-test'
    if fail:assert t['provider_ms'] is None and t['failed_elapsed_ms']>=40 and call['status']!='completed'
    else:
        assert call['status']=='completed' and t['provider_ms']>=t['headers_ms']
        if stream:assert t['first_event_ms']<t['first_output_ms']<t['provider_ms']
        else:assert t['first_output_ms'] is None


def test_missing_timing_is_not_zero_latency():
    s=summarize_timing([{'result':{'route_calls':[{'status':'completed'}]}}])
    assert s['provider_ms']['observations']==0 and s['provider_ms']['planned']==1 and s['provider_ms']['mean_ms'] is None


def test_failed_sessions_have_no_imputed_latency_or_cost():
    from customer_eval.scoring import summarize
    config = {'routes': {}, 'document': {
        'checks': [{'id': 'quality', 'required': True}], 'success_criteria': [],
        'runner': {'measurement': {'primary_latency': 'conversation'}},
        'execution': {'timeout_seconds': 180}}}
    completed = {'status': 'completed', 'timing': {'conversation_ms': 500, 'worker_ms': 800},
                 'dispatch_timing': {'action': 'trial', 'client_ms': 1000}}
    # Some legacy error receipts contain a partial conversation or the timeout bound.
    failed = {'status': 'timeout', 'latency_ms': 180000, 'timing': {'conversation_ms': 5},
              'dispatch_timing': {'action': 'trial_result', 'client_ms': 12}, 'receipt_recovered': True}
    rows = [{'result': result, 'checks': {'quality': {'pass': passed}}}
            for result, passed in ((completed, True), (failed, False))]
    score = summarize(config, 'p', rows)
    assert score.accuracy == .5 and score.n_sessions == 2
    assert score.latency_p50_s is None and score.latency_p95_s is None
    assert score.cost_usd_per_session is None
    for key, value in [('conversation_ms', 500), ('worker_ms', 800), ('dispatch_ms', 1000)]:
        summary = score.timing_metrics[key]
        assert (summary['observations'], summary['planned'], summary['p95_ms']) == (1, 2, value)
    assert score.timing_metrics['receipt_lookup_ms']['p95_ms'] == 12
    config['document']['runner']['measurement']['primary_latency'] = 'worker'
    assert summarize(config, 'p', rows).latency_p95_s is None


def test_worker_does_not_publish_partial_conversation_as_complete():
    r = {'status': 'error', 'turn_records': [{'started_ns': 2, 'answered_ns': 5}]}
    finish_worker_timing(r, 1, 10)
    assert r['timing']['worker_ms'] > 0 and r['timing']['conversation_ms'] is None
