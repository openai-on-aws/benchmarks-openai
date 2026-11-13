import pytest
from customer_eval.transport import constrain


@pytest.mark.parametrize('key,value', [
    ('reasoning', {'summary':'auto'}), ('text', {'verbosity':'low'}),
    ('temperature', 0.3), ('top_p', 0.8), ('service_tier', 'default')])
def test_explicit_null_omits_inherited_parameter_without_mutating_input(key, value):
    payload = {'input':'hello', key:value}
    limits = {'max_request_bytes':10000, 'max_output_tokens':100}
    route = {'model_id':'example', 'parameters':{key:None}}
    actual = constrain(payload, route, limits)
    assert key not in actual
    assert payload[key] == value
    route['parameters'] = {}
    assert constrain(payload, route, limits)[key] == value


def test_explicit_reasoning_effort_replaces_harness_summary():
    actual = constrain({'input':'hello', 'reasoning':{'summary':'auto'}},
        {'model_id':'example', 'parameters':{'reasoning':{'effort':'low'}}},
        {'max_request_bytes':10000, 'max_output_tokens':100})
    assert actual['reasoning'] == {'effort':'low'}


def test_null_cannot_bypass_restricted_parameter_names():
    with pytest.raises(ValueError, match='Unsupported model parameters'):
        constrain({'input':'hello'}, {'model_id':'example','parameters':{'tools':None}},
            {'max_request_bytes':10000, 'max_output_tokens':100})
