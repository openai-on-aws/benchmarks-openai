"""One bounded Responses route, including SigV4 for IAM-authenticated Gateway.

The loopback relay keeps provider credentials out of the agent configuration and
applies the same request limits to Codex, Strands, the judge and the optimizer.
Only text/function tools are supported by this initial cost envelope.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from functools import lru_cache
from datetime import datetime, timezone
from urllib.parse import urlsplit
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def reservation(route, limits):
    # Conservative reservation using UTF-8 request bytes as the text input token
    # envelope, charged at uncached input prices. Not an AWS billing receipt.
    if route['input_usd_per_million'] is None or route['output_usd_per_million'] is None:
        return 0  # Unknown pricing is allowed only for an explicitly uncapped run.
    return limits['max_requests'] * (
        limits['max_request_bytes'] * max(route['input_usd_per_million'],
                                        route.get('cache_write_usd_per_million') or 0,
                                        route.get('cached_input_usd_per_million') or 0) +
        limits['max_output_tokens'] * route['output_usd_per_million']) / 1_000_000


_credential_lock = threading.Lock()


@lru_cache(maxsize=16)
def _credential_source(profile, region, execution_role):
    # Cache the refreshable provider, never a frozen credential/token. Session
    # construction occurs under a lock; only botocore's refreshable credentials
    # are used by concurrent request handlers.
    import boto3
    return boto3.Session(profile_name=None if execution_role else profile,
                         region_name=region).get_credentials()


def authorize(route, url, data):
    headers = {'Content-Type': 'application/json', 'Accept': 'text/event-stream, application/json'}
    ref = route['credential_ref']
    if route['auth'] == 'bearer':
        token = os.environ.get(ref.removeprefix('env:'))
        if not token:
            raise ValueError('Missing credential environment variable')
        headers['Authorization'] = 'Bearer ' + token
    elif route['auth'] == 'sigv4':
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        profile = ref.removeprefix('aws_profile:')
        # Runtime containers use their execution role; local runs use the chosen profile.
        with _credential_lock:
            source = _credential_source(profile, route['region'], os.environ.get('EVAL_USE_EXECUTION_ROLE') == '1')
        if source is None:
            raise ValueError('No AWS credentials available')
        credentials = source.get_frozen_credentials()
        request = AWSRequest(method='POST', url=url, data=data, headers=headers)
        SigV4Auth(credentials, route['sigv4_service'], route['region']).add_auth(request)
        headers = dict(request.headers)
    else:
        raise ValueError('Unknown authentication mode')
    return headers


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Provider redirects are not permitted')


_ERROR_BODY_LIMIT = 16384
_ERROR_CODES = frozenset({
    'AccessDeniedException', 'UnauthorizedException', 'UnrecognizedClientException',
    'InvalidSignatureException', 'SignatureDoesNotMatch', 'ExpiredTokenException',
    'IncompleteSignatureException', 'ValidationException', 'ThrottlingException',
    'ServiceQuotaExceededException', 'ResourceNotFoundException', 'ModelNotReadyException',
    'ModelTimeoutException', 'ModelErrorException', 'InternalServerException',
    'ServiceUnavailableException', 'BadGatewayException',
    'invalid_api_key', 'permission_denied', 'insufficient_quota', 'rate_limit_exceeded',
    'model_not_found', 'employee_api_key_requires_corporate_network',
})
_AWS_DENIED_ACTIONS = frozenset({
    'bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream', 'bedrock:GetInferenceProfile',
    'bedrock:CallWithBearerToken', 'bedrock:ListFoundationModels', 'bedrock:GetFoundationModel',
    'bedrock-agentcore:InvokeAgentRuntime', 'aws-marketplace:Subscribe',
    'aws-marketplace:ViewSubscriptions', 'aws-marketplace:Unsubscribe',
})


def _known_error_code(value):
    if not isinstance(value, str) or len(value) > 256:
        return None
    code = value.rsplit('#', 1)[-1].split(':', 1)[0]
    return code if code in _ERROR_CODES else None


def _safe_request_id(headers):
    value = headers.get('x-amzn-requestid') or headers.get('x-request-id')
    if isinstance(value, str) and re.fullmatch(
            r'(?:[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}|req_[0-9a-fA-F]{32})', value):
        return value
    return None


def provider_error_diagnostics(status, headers, raw, route):
    """Emit only fixed categories and matched AWS identifiers, never error text.

    Error messages can echo credentials or customer input. Parse a bounded JSON
    body in memory; unknown codes, arbitrary ARNs/headers and messages are omitted.
    Identifiers describe provider error evidence, not an effective-IAM verdict.
    """
    headers = headers or {}
    result = {'http_status': status, 'body_omitted': True}
    request_id = _safe_request_id(headers)
    if request_id:
        result['request_id'] = request_id
    header_code = _known_error_code(headers.get('x-amzn-errortype'))
    body_code, message = None, ''
    if isinstance(raw, bytes) and len(raw) <= _ERROR_BODY_LIMIT:
        try:
            def unique(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError('duplicate error field')
                    value[key] = item
                return value
            parsed = json.loads(raw, object_pairs_hook=unique)
            if isinstance(parsed, dict):
                error = parsed.get('error') if isinstance(parsed.get('error'), dict) else parsed
                body_code = next((code for key in ('code', '__type', 'type')
                                  if (code := _known_error_code(error.get(key)))), None)
                message = error.get('message', error.get('Message', ''))
                if not isinstance(message, str):
                    message = ''
        except (ValueError, UnicodeError, RecursionError):
            pass
    elif isinstance(raw, bytes):
        result['body_exceeded_limit'] = True
    code = header_code or body_code
    if code:
        result['error_code'] = code
    # Do not interpret arbitrary validation/model text as an authorization error.
    if status not in (401, 403):
        return result
    lower = message.casefold()
    category = 'unauthenticated' if status == 401 else 'authorization_unspecified'
    if code == 'employee_api_key_requires_corporate_network':
        category = 'network_restriction'
    elif 'explicit deny' in lower:
        category = 'explicit_deny'
    elif any(p in lower for p in ('no identity-based policy allows', 'no resource-based policy allows',
                                  'no permissions boundary allows', 'no service control policy allows')):
        category = 'missing_permission'
    elif any(p in lower for p in ('not subscribed to', 'subscription is required',
                                  'model access has not been granted', 'model agreement has not been accepted',
                                  'model is not enabled for your account')):
        category = 'model_entitlement'
    result['authorization_category'] = category
    for phrase, policy in (('service control policy', 'service_control_policy'),
                           ('permissions boundary', 'permissions_boundary'),
                           ('identity-based policy', 'identity_policy'),
                           ('resource-based policy', 'resource_policy'),
                           ('session policy', 'session_policy')):
        if phrase in lower:
            result['policy_type'] = policy
            break
    actions = re.findall(r'not authorized to perform\s*:?\s*[\'\"]?([A-Za-z-]+:[A-Za-z]+)', message, re.I)
    allowed_actions = sorted({a for a in actions if a in _AWS_DENIED_ACTIONS})
    if allowed_actions:
        result['denied_actions'] = allowed_actions
    model = route.get('model_id', '')
    base_model = re.sub(r'^(?:us|eu|apac|global)\.', '', model)
    resources = {'project/default', 'foundation-model/' + base_model, 'inference-profile/' + model}
    resource_arns = []
    # Only resource IDs derived from the configured route are retained. In
    # particular, never copy arbitrary IAM principal/session names from a body.
    for arn in re.findall(r'arn:aws(?:-us-gov|-cn)?:bedrock:[a-z0-9-]+:[0-9]*:[A-Za-z0-9./_-]+', message):
        parts = arn.split(':', 5)
        if (re.fullmatch(r'[a-z]{2}(?:-gov)?-[a-z]+-\d', parts[3]) and
                (not parts[4] or re.fullmatch(r'\d{12}', parts[4])) and parts[5] in resources):
            resource_arns.append(arn)
    if resource_arns:
        result['resource_arns'] = sorted(set(resource_arns))[:8]
    return result


def _sanitized_http_error(url, status, headers, raw, route):
    diagnostics = provider_error_diagnostics(status, headers, raw, route)
    safe_headers = {}
    if diagnostics.get('request_id'):
        safe_headers['x-request-id'] = diagnostics['request_id']
    error = urllib.error.HTTPError(url, status, 'Provider request failed', safe_headers, None)
    error.provider_diagnostics = diagnostics
    return error


class ProviderConnection:
    """A run-scoped HTTP pool; no redirect or automatic paid-request replay."""
    def __init__(self):
        self.pool = None
        self.lock = threading.Lock()

    def open(self, url, data, headers, timeout, *, route=None):
        import urllib3
        with self.lock:
            if self.pool is None:
                parsed = urlsplit(url)
                proxy = None if urllib.request.proxy_bypass(parsed.hostname) else urllib.request.getproxies().get(parsed.scheme)
                options = dict(num_pools=1,maxsize=1,block=True)
                self.pool = urllib3.ProxyManager(proxy,**options) if proxy else urllib3.PoolManager(**options)
        response = self.pool.request('POST', url, body=data, headers=headers,
            preload_content=False, redirect=False, retries=False,
            timeout=urllib3.Timeout(connect=min(10, timeout), read=timeout), pool_timeout=timeout)
        if response.status >= 300:
            try:
                raw = response.read(_ERROR_BODY_LIMIT + 1)
            except Exception:
                raw = b''
            finally:
                response.close(); response.release_conn()
            raise _sanitized_http_error(url, response.status, response.headers, raw, route or {})
        return PooledResponse(response)

    def close(self):
        if self.pool: self.pool.clear()


class PooledResponse:
    def __init__(self, response):
        self.response, self.headers, self.consumed = response, response.headers, False

    def __enter__(self): return self

    def read(self):
        body = self.response.read(); self.consumed = True
        return body

    def __iter__(self):
        # read1 returns currently available bytes instead of waiting for an 8-KB
        # buffer. This matters for small, delayed SSE messages on HTTP/1.1.
        pending = b''
        while True:
            chunk = self.response.read1(65536)
            if not chunk: break
            pending += chunk
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1)
                yield line + b'\n'
        if pending: yield pending
        self.consumed = True

    def __exit__(self, *args):
        if not self.consumed: self.response.close()
        self.response.release_conn()


def request(route, payload, timeout=60, *, timing=None, connection=None):
    url = route['base_url'].rstrip('/') + '/responses'
    data = json.dumps(payload).encode()
    auth_started = time.monotonic_ns()
    headers = authorize(route, url, data)
    sent = time.monotonic_ns()
    if timing is not None:
        timing.update(auth_ms=(sent-auth_started)/1e6, sent_ns=sent)
    try:
        response = (connection.open(url, data, headers, timeout, route=route) if connection else
            urllib.request.build_opener(NoRedirect).open(
                urllib.request.Request(url, data=data, headers=headers), timeout=timeout))
    except urllib.error.HTTPError as exc:
        if getattr(exc, 'provider_diagnostics', None) is not None:
            raise
        try:
            raw = exc.read(_ERROR_BODY_LIMIT + 1)
        except Exception:
            raw = b''
        finally:
            exc.close()
        raise _sanitized_http_error(url, exc.code, exc.headers, raw, route) from None
    if timing is not None:
        timing['headers_ms'] = (time.monotonic_ns()-sent)/1e6
    return response


def constrain(payload, route, limits):
    payload = dict(payload)
    if 'allowed_tool_names' in route:
        allowed = set(route['allowed_tool_names'])
        retained = []
        for tool in payload.get('tools', []):
            if tool.get('type') == 'namespace' and tool.get('name') == 'mcp__customer':
                functions = [f for f in tool.get('tools', []) if f.get('type') == 'function' and f.get('name') in allowed]
                if functions: retained.append(dict(tool, tools=functions))
            elif tool.get('type') == 'function' and (tool.get('name') in allowed or tool.get('name', '').removeprefix('mcp__customer__') in allowed):
                retained.append(tool)
        payload['tools'] = retained
    if any(tool.get('type') not in ('function', 'namespace') for tool in payload.get('tools', [])):
        raise ValueError('Only function tools are allowed; provider-hosted tools have unbounded extra charges')
    if any(t.get('type') == 'namespace' and any(f.get('type') != 'function' for f in t.get('tools', [])) for t in payload.get('tools', [])):
        raise ValueError('Namespaces may contain function tools only')
    # No remote conversation pointers, background work or hidden input fetches.
    if payload.get('previous_response_id') or payload.get('conversation') or payload.get('background'):
        raise ValueError('Use client-side conversation history for bounded, reproducible runs')
    def text_only(node):
        if isinstance(node, dict):
            if node.get('type') in ('input_image', 'input_audio', 'input_file', 'computer', 'computer_call'):
                raise ValueError('Only text inputs are supported by this cost reservation')
            for value in node.values(): text_only(value)
        elif isinstance(node, list):
            for value in node: text_only(value)
    text_only(payload)
    # Parameters cannot replace messages, model, tools or the cost envelope.
    allowed = {'temperature', 'top_p', 'reasoning', 'text', 'service_tier'}
    if set(route.get('parameters', {})) - allowed:
        raise ValueError('Unsupported model parameters; do not silently ignore them')
    # A null route setting explicitly removes an inherited harness parameter,
    # e.g. reasoning summaries for a non-reasoning model. An absent route key
    # keeps the harness default. Apply the same rule to every allowed parameter.
    for key, value in route.get('parameters', {}).items():
        if value is None:
            payload.pop(key, None)
        else:
            payload[key] = value
    if payload.get('service_tier', 'default') != 'default':
        raise ValueError('This cost envelope is for the Standard service tier only')
    output_limit = payload.get('max_output_tokens', limits['max_output_tokens'])
    if type(output_limit) is not int or output_limit <= 0:
        raise ValueError('Invalid requested output limit')
    payload.update(model=route['model_id'], max_output_tokens=min(output_limit, limits['max_output_tokens']), store=False)
    if len(json.dumps(payload).encode()) > limits['max_request_bytes']:
        raise ValueError('Request exceeds max_request_bytes, including accumulated conversation and tools')
    return payload


@contextmanager
def relay(route, limits):
    token = secrets.token_hex(24)
    calls, lock = [], threading.Lock()
    connection = ProviderConnection()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_POST(self):
            if self.path != '/v1/responses' or self.headers.get('Authorization') != 'Bearer ' + token:
                self.send_error(403)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size <= 0 or size > limits['max_request_bytes']:
                    raise ValueError('Request length outside configured bound')
                incoming = json.loads(self.rfile.read(size))
                body = constrain(incoming, route, limits)
                with lock:
                    if len(calls) >= limits['max_requests']:
                        raise ValueError('Model request limit reached; no retry or fallback')
                    record = {'status': 'started', 'model_requested': route['model_id'], 'usage': None,
                              'request_timeout_seconds': limits.get('request_timeout_seconds', 60),
                              'started_at': datetime.now(timezone.utc).isoformat(),
                              'timing': {'first_event_ms': None, 'first_output_ms': None, 'provider_ms': None},
                              'effective_parameters': {key: body.get(key) for key in ('reasoning', 'text', 'max_output_tokens')},
                              'service_tier_requested': body.get('service_tier', 'default'),
                              'offered_tools': [{'type': t.get('type'), 'name': t.get('name')} for t in incoming.get('tools', [])],
                              'effective_tools': [dict(type=t.get('type'), name=t.get('name'),
                                  **({'functions': [f.get('name') for f in t.get('tools', [])]}
                                     if t.get('type') == 'namespace' else {})) for t in body.get('tools', [])]}
                    calls.append(record)
                with request(route, body, timeout=limits.get('request_timeout_seconds', 60),
                             timing=record['timing'], connection=connection) as response:
                    record['request_id'] = response.headers.get('x-amzn-requestid') or response.headers.get('x-request-id')
                    self.send_response(200)
                    self.send_header('Content-Type', response.headers.get('Content-Type', 'application/json'))
                    self.end_headers()
                    chunks = []
                    # readline forwards complete SSE lines immediately, preserving streaming.
                    for chunk in response:
                        chunks.append(chunk)
                        if body.get('stream') and chunk.startswith(b'data: '):
                            data = chunk[6:].strip()
                            if data and data != b'[DONE]':
                                event = json.loads(data)
                                elapsed = (time.monotonic_ns()-record['timing']['sent_ns'])/1e6
                                if record['timing']['first_event_ms'] is None:
                                    record['timing']['first_event_ms'] = elapsed
                                    record['timing']['first_event_type'] = event.get('type')
                                if event.get('type')=='response.output_item.added' and 'first_output_item_ms' not in record['timing']:
                                    record['timing']['first_output_item_ms']=elapsed
                                    record['timing']['first_output_item_type']=event.get('item',{}).get('type')
                                if (event.get('type') in ('response.output_text.delta', 'response.function_call_arguments.delta', 'response.function_call_arguments.done')
                                        and (event.get('delta') or event.get('arguments')) and record['timing']['first_output_ms'] is None):
                                    record['timing']['first_output_ms'] = elapsed
                                    record['timing']['first_output_type'] = event.get('type')
                                if event.get('type') == 'response.completed':
                                    terminal = event['response']
                                    if terminal.get('status') == 'completed':
                                        record['timing']['provider_ms'] = elapsed
                                        record['completed_at'] = datetime.now(timezone.utc).isoformat()
                                        record['response_id'] = terminal.get('id')
                                        record.update(status='completed', usage=terminal.get('usage'), model_returned=terminal.get('model'), service_tier_returned=terminal.get('service_tier'))
                        try:
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            # SDKs may stop reading immediately after response.completed.
                            if record['status'] == 'completed':
                                try: response.read()  # Drain the short SSE tail for safe connection reuse.
                                except Exception as exc:
                                    record['transport_tail_error'] = type(exc).__name__
                                return
                            raise
                    raw = b''.join(chunks).decode()
                    if body.get('stream'):
                        completed = []
                        for line in raw.splitlines():
                            if line.startswith('data: ') and line[6:] != '[DONE]':
                                event = json.loads(line[6:])
                                if event.get('type') == 'response.completed':
                                    completed.append(event['response'])
                        if not completed:
                            raise ValueError('Stream ended without response.completed')
                        result = completed[-1]
                    else:
                        result = json.loads(raw)
                        record['timing']['provider_ms'] = (time.monotonic_ns()-record['timing']['sent_ns'])/1e6
                    if result.get('status') != 'completed':
                        raise ValueError('Provider response did not complete')
                    record.update(status='completed', usage=result.get('usage'), model_returned=result.get('model'), service_tier_returned=result.get('service_tier'))
            except Exception as exc:
                if 'record' in locals(): record['status'] = type(exc).__name__
                if 'record' in locals() and isinstance(exc, urllib.error.HTTPError):
                    record['http_status'] = exc.code
                    record['request_id'] = _safe_request_id(exc.headers or {})
                    if getattr(exc, 'provider_diagnostics', None) is not None:
                        record['provider_error'] = exc.provider_diagnostics
                # Never return provider exception bodies, which may contain credentials or data.
                try: self.send_error(502, 'Evaluation route failed: ' + type(exc).__name__)
                except (BrokenPipeError, ConnectionResetError): pass
            finally:
                if 'record' in locals():
                    timing = record['timing']
                    sent = timing.pop('sent_ns', None)
                    if sent is not None and timing['provider_ms'] is None:
                        timing['failed_elapsed_ms'] = (time.monotonic_ns()-sent)/1e6

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', token, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        connection.close()


def complete(route, text, limits, *, receipt=None):
    """Return text and usage; optionally populate provider metadata after success."""
    body = constrain({'input': text, 'stream': False}, route, limits)
    try:
        with request(route, body, timeout=limits.get('request_timeout_seconds', 60)) as response:
            result = json.load(response)
            headers = getattr(response, 'headers', {})
            request_id = headers.get('x-amzn-requestid') or headers.get('x-request-id')
    except urllib.error.HTTPError as exc:
        if receipt is not None:
            receipt.update(status='provider_error', model_requested=body['model'], usage=None,
                           http_status=exc.code, request_timeout_seconds=limits.get('request_timeout_seconds', 60))
            if getattr(exc, 'provider_diagnostics', None) is not None:
                receipt['provider_error'] = exc.provider_diagnostics
        raise
    if result.get('status') != 'completed':
        raise ValueError('Incomplete model response')
    output = ''.join(c.get('text', '') for item in result.get('output', [])
                     for c in item.get('content', []) if c.get('type') == 'output_text')
    if not output:
        raise ValueError('Empty model response')
    if receipt is not None:
        receipt.update(status='completed', model_requested=body['model'], model_returned=result.get('model'),
                       request_timeout_seconds=limits.get('request_timeout_seconds', 60),
                       request_id=request_id, response_id=result.get('id'),
                       effective_parameters={key: body[key] for key in
                           ('reasoning', 'text', 'temperature', 'top_p', 'service_tier',
                            'max_output_tokens', 'store', 'stream') if key in body},
                       service_tier_requested=body.get('service_tier', 'default'),
                       service_tier_returned=result.get('service_tier'))
    return output, result.get('usage')
