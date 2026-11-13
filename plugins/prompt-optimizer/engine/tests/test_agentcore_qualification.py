from customer_eval.contract import cases, digest, load, ROOT
from scripts.qualify_agentcore import qualification_config, assess


def test_qualification_only_uses_one_development_case_and_one_repeat():
    full = load(ROOT / 'examples/retail-returns/input.yaml')
    cfg = qualification_config(full)
    assert cfg['qualification_of'] == full['fingerprint']
    assert cfg['fingerprint'] == digest({k: v for k, v in cfg.items() if k != 'fingerprint'})
    assert list(cfg['splits']) == ['development']
    assert cases(cfg, 'development') == cases(full, 'development')[:1]
    assert cfg['document']['execution']['repetitions'] == 1
    assert cfg['datasets'] != full['datasets']


def test_wrong_returned_model_fails_even_when_task_grades_pass():
    full = load(ROOT / 'examples/retail-returns/input.yaml')
    source = full['source']['model_id']
    model = full['routes'][source]['model_id']
    row = {'model_id': source, 'checks': {c['id']: {'pass': True} for c in full['document']['checks']},
           'result': {'status': 'completed', 'route_calls': [
               {'status': 'completed', 'model_requested': model, 'model_returned': 'different',
                'request_id': 'aws-request', 'usage': None}]}}
    assert assess(full, row)['required_checks_passed']
    assert not assess(full, row)['route_verified']
    assert assess(full, row)['model_cost']['estimated_cost_usd'] is None
    row['result']['route_calls'][0]['model_returned'] = model
    assert assess(full, row)['route_verified']
