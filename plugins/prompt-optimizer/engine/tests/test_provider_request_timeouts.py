"""Configured deadlines reach both model paths without automatic paid retries."""
from io import BytesIO
import json
import time
import urllib.error
import urllib.request

import pytest

from customer_eval import transport


ROUTE = {'model_id': 'test-model', 'parameters': {}}
LIMITS = {'max_requests': 1, 'max_output_tokens': 1000, 'max_request_bytes': 10000}
RESULT = {'status': 'completed', 'model': 'test-model', 'usage': {'input_tokens': 2, 'output_tokens': 2},
          'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'ok'}]}]}


class Response(BytesIO):
    headers = {'Content-Type': 'application/json', 'x-request-id': 'test-request'}


@pytest.mark.parametrize('configured,expected', [(None, 60), (600, 600)])
def test_completion_uses_configured_timeout_and_records_it(monkeypatch, configured, expected):
    seen = []
    def request(route, body, timeout=60):
        seen.append(timeout)
        return Response(json.dumps(RESULT).encode())
    monkeypatch.setattr(transport, 'request', request)
    limits = LIMITS | ({'request_timeout_seconds': configured} if configured else {})
    receipt = {}
    assert transport.complete(ROUTE, 'prompt', limits, receipt=receipt)[0] == 'ok'
    assert seen == [expected]
    assert receipt['request_timeout_seconds'] == expected


def test_completion_timeout_is_not_retried(monkeypatch):
    seen = []
    def request(route, body, timeout=60):
        seen.append(timeout)
        raise TimeoutError('provider did not finish')
    monkeypatch.setattr(transport, 'request', request)
    with pytest.raises(TimeoutError):
        transport.complete(ROUTE, 'prompt', LIMITS | {'request_timeout_seconds': 600})
    assert seen == [600]


@pytest.mark.parametrize('configured,expected,fail', [(None, 60, False), (300, 300, False), (300, 300, True)])
def test_application_relay_uses_timeout_and_records_failed_attempt_without_retry(monkeypatch, configured, expected, fail):
    seen = []
    def request(route, body, timeout=60, *, timing=None, connection=None):
        seen.append(timeout)
        timing.update(sent_ns=time.monotonic_ns(), auth_ms=0, headers_ms=0)
        if fail:
            raise TimeoutError('provider did not finish')
        return Response(json.dumps(RESULT).encode())
    monkeypatch.setattr(transport, 'request', request)
    limits = LIMITS | ({'request_timeout_seconds': configured} if configured else {})
    with transport.relay(ROUTE, limits) as (url, token, calls):
        req = urllib.request.Request(url + '/responses', data=json.dumps({'input': 'fixture'}).encode(),
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        if fail:
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(req, timeout=5)
        else:
            with urllib.request.urlopen(req, timeout=5) as response:
                assert json.load(response)['status'] == 'completed'
    assert seen == [expected] and len(calls) == 1
    assert calls[0]['request_timeout_seconds'] == expected
    assert calls[0]['status'] == ('TimeoutError' if fail else 'completed')
    if fail:
        assert calls[0]['usage'] is None and calls[0]['timing']['failed_elapsed_ms'] >= 0
