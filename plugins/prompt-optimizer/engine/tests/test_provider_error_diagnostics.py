"""Provider diagnostics retain useful denial evidence without reflected secrets."""
from io import BytesIO
import json
import time
import urllib.error
import urllib.request

import pytest

from customer_eval import transport


ROUTE = {'model_id': 'us.openai.gpt-6.2-astra', 'base_url': 'https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1',
         'parameters': {}, 'auth': 'bearer', 'credential_ref': 'env:ERROR_DIAGNOSTIC_TEST_TOKEN'}
LIMITS = {'max_requests': 1, 'max_request_bytes': 20000, 'max_output_tokens': 100,
          'request_timeout_seconds': 300}
REQUEST_ID = '11111111-2222-3333-4444-555555555555'
ARN = 'arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.openai.gpt-6.2-astra'
SECRET = 'sk-live-never-print-this-secret'
PROMPT = 'private customer prompt never appears in diagnostics'


def denial(message=None):
    return json.dumps({'__type': 'com.amazon.coral#AccessDeniedException',
        'message': message or f'User: arn:aws:sts::123456789012:assumed-role/{SECRET}/session '
        f'is not authorized to perform: bedrock:InvokeModel on resource: {ARN} '
        'because no identity-based policy allows the bedrock:InvokeModel action. '
        f'Authorization: Bearer {SECRET}. Customer input: {PROMPT}',
        'debug': {'prompt': PROMPT, 'api_key': SECRET}}).encode()


def test_only_known_error_code_action_route_resource_and_request_id_are_emitted():
    diagnostics = transport.provider_error_diagnostics(403, {'x-amzn-requestid': REQUEST_ID}, denial(), ROUTE)
    assert diagnostics == {'http_status': 403, 'body_omitted': True, 'request_id': REQUEST_ID,
        'error_code': 'AccessDeniedException', 'authorization_category': 'missing_permission',
        'policy_type': 'identity_policy', 'denied_actions': ['bedrock:InvokeModel'], 'resource_arns': [ARN]}
    text = json.dumps(diagnostics)
    assert SECRET not in text and PROMPT not in text and 'assumed-role' not in text


@pytest.mark.parametrize('message,category,policy', [
    ('with an explicit deny in a service control policy', 'explicit_deny', 'service_control_policy'),
    ('because no permissions boundary allows the operation', 'missing_permission', 'permissions_boundary'),
    ('The model agreement has not been accepted', 'model_entitlement', None),
    ('The request was denied', 'authorization_unspecified', None),
])
def test_authorization_categories_are_fixed_values(message, category, policy):
    diagnostics = transport.provider_error_diagnostics(403, {}, denial(message), ROUTE)
    assert diagnostics['authorization_category'] == category
    assert diagnostics.get('policy_type') == policy
    assert message not in json.dumps(diagnostics)


@pytest.mark.parametrize('payload', [
    { 'error': {'code': SECRET, 'message': PROMPT}},
    { 'code': SECRET, 'message': PROMPT},
    { 'message': f'not authorized to perform: custom:{SECRET} on resource '
                  f'arn:aws:bedrock:us-east-1:123456789012:project/{SECRET}'},
    { 'message': f'arn:aws:bedrock:us-east-1:123456789012:foundation-model/{SECRET}'},
])
def test_unrecognized_codes_arns_actions_and_headers_cannot_reflect_credentials(payload):
    headers = {'x-amzn-requestid': SECRET, 'x-request-id': PROMPT, 'x-amzn-errortype': SECRET,
               'Authorization': SECRET, 'Set-Cookie': SECRET}
    diagnostics = transport.provider_error_diagnostics(403, headers, json.dumps(payload).encode(), ROUTE)
    assert diagnostics == {'http_status': 403, 'body_omitted': True, 'authorization_category': 'authorization_unspecified'}


def test_oversized_body_is_not_partially_interpreted_and_unknown_header_is_omitted():
    raw = denial() + b' ' * transport._ERROR_BODY_LIMIT
    diagnostics = transport.provider_error_diagnostics(403, {'x-amzn-errortype': 'AccessDeniedException:' + SECRET}, raw, ROUTE)
    assert diagnostics == {'http_status': 403, 'body_omitted': True, 'body_exceeded_limit': True,
                           'error_code': 'AccessDeniedException', 'authorization_category': 'authorization_unspecified'}


@pytest.mark.parametrize('raw', [b'not JSON', b'{"code":"AccessDeniedException","code":"secret"}', b'[1,2,3]', b'\xff'])
def test_malformed_error_bodies_do_not_escape(raw):
    assert transport.provider_error_diagnostics(403, {}, raw, ROUTE) == {
        'http_status': 403, 'body_omitted': True, 'authorization_category': 'authorization_unspecified'}


def test_non_authorization_error_does_not_classify_echoed_prompt_as_iam_failure():
    diagnostics = transport.provider_error_diagnostics(400, {}, denial('explicit deny in a service control policy'), ROUTE)
    assert 'authorization_category' not in diagnostics and 'policy_type' not in diagnostics


def test_urllib_error_is_bounded_closed_and_replaced_with_sanitized_exception(monkeypatch):
    reads = []
    class Body(BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)
    body = Body(denial())
    original = urllib.error.HTTPError(ROUTE['base_url'], 403, SECRET,
                                      {'x-amzn-requestid': REQUEST_ID, 'Authorization': SECRET}, body)
    class Opener:
        def open(self, *args, **kwargs):
            raise original
    monkeypatch.setattr(transport, 'authorize', lambda *args: {'Authorization': SECRET})
    monkeypatch.setattr(transport.urllib.request, 'build_opener', lambda *args: Opener())
    with pytest.raises(urllib.error.HTTPError) as captured:
        transport.request(ROUTE, {'input': PROMPT})
    assert reads == [transport._ERROR_BODY_LIMIT + 1] and body.closed
    error = captured.value
    assert SECRET not in str(error) and SECRET not in json.dumps(dict(error.headers))
    assert error.provider_diagnostics['authorization_category'] == 'missing_permission'
    assert error.read() == b''  # urllib supplies an empty buffer when fp=None.


def test_pooled_error_body_is_bounded_and_connection_closed_without_retry():
    class Reply:
        status = 403
        headers = {'x-amzn-requestid': REQUEST_ID}
        closed = released = False
        reads = []
        def read(self, size):
            self.reads.append(size)
            return denial()
        def close(self): self.closed = True
        def release_conn(self): self.released = True
    reply = Reply()
    class Pool:
        calls = []
        def request(self, *args, **kwargs):
            self.calls.append(kwargs)
            return reply
    connection = transport.ProviderConnection(); connection.pool = Pool()
    with pytest.raises(urllib.error.HTTPError) as captured:
        connection.open(ROUTE['base_url'], b'{}', {}, 300, route=ROUTE)
    assert len(connection.pool.calls) == 1 and connection.pool.calls[0]['retries'] is False
    assert reply.reads == [transport._ERROR_BODY_LIMIT + 1] and reply.closed and reply.released
    assert captured.value.provider_diagnostics['resource_arns'] == [ARN]


def test_relay_saves_safe_diagnostics_for_the_single_failed_attempt(monkeypatch):
    seen = []
    def request(route, body, timeout=60, *, timing=None, connection=None):
        seen.append(body)
        timing.update(sent_ns=time.monotonic_ns(), auth_ms=0, headers_ms=0)
        raise transport._sanitized_http_error(route['base_url'], 403, {'x-amzn-requestid': REQUEST_ID}, denial(), route)
    monkeypatch.setattr(transport, 'request', request)
    with transport.relay(ROUTE, LIMITS) as (url, token, calls):
        req = urllib.request.Request(url + '/responses', data=json.dumps({'input': PROMPT}).encode(),
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with pytest.raises(urllib.error.HTTPError) as captured:
            urllib.request.urlopen(req, timeout=5)
        returned = captured.value.read().decode()
    assert len(seen) == len(calls) == 1
    assert calls[0]['status'] == 'HTTPError' and calls[0]['usage'] is None
    assert calls[0]['provider_error']['resource_arns'] == [ARN]
    assert calls[0]['request_id'] == REQUEST_ID
    assert SECRET not in json.dumps(calls) + returned and PROMPT not in json.dumps(calls) + returned


def test_complete_keeps_failure_unknown_and_attaches_only_safe_receipt(monkeypatch):
    seen = []
    def request(route, body, timeout=60):
        seen.append(timeout)
        raise transport._sanitized_http_error(route['base_url'], 403, {'x-amzn-requestid': REQUEST_ID}, denial(), route)
    monkeypatch.setattr(transport, 'request', request)
    receipt = {}
    with pytest.raises(urllib.error.HTTPError):
        transport.complete(ROUTE, PROMPT, LIMITS, receipt=receipt)
    assert seen == [300] and receipt['usage'] is None and receipt['status'] == 'provider_error'
    assert receipt['provider_error']['denied_actions'] == ['bedrock:InvokeModel']
    assert SECRET not in json.dumps(receipt) and PROMPT not in json.dumps(receipt)
