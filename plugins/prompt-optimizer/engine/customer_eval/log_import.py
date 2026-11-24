"""Import text request/response logs without executing the captured application.

ModelShift's pinned shape/response parsers are reused. This boundary additionally
preserves role order, refuses lossy content/settings, and accounts for every row.
"""
from collections import Counter
from copy import deepcopy
import gzip
import json
from pathlib import Path

from .replay import fingerprint, message_text, validate_replay
from .vendor.modelshift import ingest as upstream


class ImportIssue(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def strict_json(text):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise ImportIssue('duplicate_json_key')
            out[k] = v
        return out
    def constant(_):
        raise ImportIssue('nonfinite_json_number')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def records(path):
    raw = Path(path).read_bytes()
    if len(raw) > 50_000_000:
        raise ValueError('Log file exceeds 50 MB; export a smaller slice')
    if raw[:2] == b'\x1f\x8b':
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as f:
            raw = f.read(50_000_001)
        if len(raw) > 50_000_000:
            raise ValueError('Decompressed log exceeds 50 MB')
    text = raw.decode('utf-8-sig')
    try:
        obj = strict_json(text)
    except (ValueError, json.JSONDecodeError):
        for i, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = strict_json(line)
                yield i, row if isinstance(row, dict) else {'__parse_error__': 'non_object_row'}
            except (ValueError, json.JSONDecodeError) as e:
                yield i, {'__parse_error__': getattr(e, 'code', 'malformed_json')}
        return
    if isinstance(obj, list):
        rows = obj
    elif isinstance(obj, dict):
        # A single API record has arrays too; do not mistake its messages for a wrapper.
        if any(k in obj for k in ('request', 'response', 'messages', 'input', 'proxy_server_request', 'prompt')):
            rows = [obj]
        elif isinstance(obj.get('data'), list):
            rows = obj['data']
        else:
            arrays = [v for v in obj.values() if isinstance(v, list)]
            if len(arrays) != 1:
                rows = [obj]
            else:
                rows = arrays[0]
    else:
        rows = [obj]
    for i, row in enumerate(rows, 1):
        yield i, row if isinstance(row, dict) else {'__parse_error__': 'non_object_row'}


def at(obj, path):
    for part in path.split('.'):
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


def mapped_row(raw, mapping, *, reference_mode="standard"):
    required = {'request'} if reference_mode == 'source_judge' else {'request', 'response'}
    if set(mapping) - {'request', 'response', 'id', 'api', 'expected', 'group'} or not required <= set(mapping):
        raise ValueError('Mapping requires ' + '/'.join(sorted(required)) + ' paths; optional id, api, expected, group')
    try:
        row = {'request': at(raw, mapping['request'])}
        if 'response' in mapping:
            try:
                row['response'] = at(raw, mapping['response'])
            except (KeyError, IndexError, TypeError, ValueError):
                if reference_mode != 'source_judge':
                    raise
        for key in ('id', 'expected'):
            if key in mapping:
                row[key] = at(raw, mapping[key])
        if 'api' in mapping:
            row['api'] = mapping['api']
        return row
    except (KeyError, IndexError, TypeError, ValueError):
        raise ImportIssue('mapping_field_missing')


_CAPTURE_FIELDS = {'model', 'messages', 'input', 'instructions', 'response_format', 'text',
                   'temperature', 'top_p', 'reasoning', 'reasoning_effort', 'service_tier',
                   'max_tokens', 'max_completion_tokens', 'max_output_tokens', 'stream', 'store',
                   'tools', 'metadata', 'user', 'stream_options'}


def normalize(raw, *, mapping=None, prompt_location=None, reference_mode="standard"):
    if reference_mode not in ("standard", "source_judge"):
        raise ValueError("reference_mode must be standard or source_judge")
    source_judge = reference_mode == "source_judge"
    if '__parse_error__' in raw:
        raise ImportIssue(str(raw['__parse_error__']))
    row = mapped_row(raw, mapping, reference_mode=reference_mode) if mapping else deepcopy(raw)
    if source_judge and 'expected' in row:
        raise ImportIssue('source_judge_expected_answers_conflict')
    manual = 'prompt' in row and 'response' not in row and ('expected' in row or source_judge)
    shape = upstream.classify(row).value
    if manual:
        if not isinstance(row.get('prompt'), str) or not isinstance(row.get('input'), str):
            raise ImportIssue('manual_example_needs_prompt_and_text_input')
        request = {'messages': [{'role': 'system', 'content': row['prompt']},
                                {'role': 'user', 'content': row['input']}], **row.get('settings', {})}
        response = None
        if not source_judge:
            golden = row['expected']
            golden = golden if isinstance(golden, str) else json.dumps(golden, ensure_ascii=False)
            response = {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': golden}}]}
        source_format = 'manual'
    else:
        request = row.get('request')
        if request is None and 'proxy_server_request' in row:
            psr = row['proxy_server_request']
            if isinstance(psr, str):
                psr = strict_json(psr)
            request = psr.get('body', psr) if isinstance(psr, dict) else psr
        if request is None:
            unsupported = {'stop', 'seed', 'n', 'frequency_penalty', 'presence_penalty', 'logit_bias',
                           'logprobs', 'top_logprobs', 'parallel_tool_calls', 'tool_choice',
                           'previous_response_id', 'conversation', 'background', 'max_tool_calls', 'include'}
            if unsupported.intersection(row):
                raise ImportIssue('unsupported_request_setting')
            request = {k: row[k] for k in _CAPTURE_FIELDS if k in row}
            # Existing ModelShift exports wrap a Responses request in messages.
            if isinstance(request.get('messages'), dict):
                request = dict(request, **request.pop('messages'))
                request.pop('messages', None)
        response = row.get('response', row if 'choices' in row or 'output' in row else None)
        source_format = 'custom' if mapping else ('litellm_spendlogs' if 'proxy_server_request' in row
                          else 'litellm_wrapped' if shape == 'wrapped'
                          else 'responses' if isinstance(request, dict) and 'input' in request
                          else 'chat_completions')
    if isinstance(request, str):
        request = strict_json(request)
    if not source_judge and isinstance(response, str):
        response = strict_json(response)
    if not isinstance(request, dict) or (not source_judge and not isinstance(response, dict)):
        raise ImportIssue('missing_request' if source_judge else 'missing_request_or_response')
    # Do not silently discard controls such as stop, seed, tool_choice, remote history.
    if set(request) - _CAPTURE_FIELDS:
        raise ImportIssue('unsupported_request_setting')
    if request.get('tools'):
        raise ImportIssue('tool_calls_unsupported')
    if not source_judge:
        if response.get('error') or response.get('status', 'completed') != 'completed':
            raise ImportIssue('incomplete_or_failed_response')
        for c in response.get('choices', []):
            if c.get('finish_reason') not in (None, 'stop'):
                raise ImportIssue('incomplete_or_tool_response')
            if c.get('message', {}).get('tool_calls') or c.get('message', {}).get('function_call'):
                raise ImportIssue('tool_calls_unsupported')
        if len(response.get('choices', [])) > 1:
            raise ImportIssue('multiple_response_choices')
        for item in response.get('output', []):
            if item.get('type') not in ('message', 'reasoning'):
                raise ImportIssue('unsupported_response_item')
    is_responses = 'input' in request
    messages = request.get('input') if is_responses else request.get('messages')
    if isinstance(messages, str):
        messages = [{'role': 'user', 'content': messages}]
    if not isinstance(messages, list) or not messages:
        raise ImportIssue('missing_messages')
    # Verify every content part before the upstream text normalizer can omit it.
    for m in messages:
        if not isinstance(m, dict) or set(m) - {'role', 'content', 'type'} or m.get('role') not in ('system', 'developer', 'user', 'assistant'):
            raise ImportIssue('unsupported_message_or_tool_history')
        try:
            message_text(m)
        except ValueError:
            raise ImportIssue('unsupported_or_empty_content')
    messages = deepcopy(messages)
    for m in messages:
        if isinstance(m['content'], list):
            for part in m['content']:
                part['type'] = 'output_text' if m['role'] == 'assistant' else 'input_text'
    body = {'input': deepcopy(messages)}
    for key in ('instructions', 'text', 'reasoning', 'temperature', 'top_p', 'service_tier'):
        if key in request:
            body[key] = deepcopy(request[key])
    if 'reasoning_effort' in request:
        if 'reasoning' in request:
            raise ImportIssue('conflicting_reasoning_settings')
        body['reasoning'] = {'effort': request['reasoning_effort']}
    if 'response_format' in request:
        if 'text' in request:
            raise ImportIssue('conflicting_output_format')
        fmt = deepcopy(request['response_format'])
        if fmt.get('type') == 'json_schema':
            fmt = {'type': 'json_schema', **fmt['json_schema']}
        body['text'] = {'format': fmt}
    caps = [request[k] for k in ('max_tokens', 'max_completion_tokens', 'max_output_tokens') if k in request]
    if len(caps) > 1:
        raise ImportIssue('conflicting_output_limits')
    if caps:
        body['max_output_tokens'] = caps[0]
    if prompt_location is None:
        location = 'instructions' if body.get('instructions') else next(
            (i for i, m in enumerate(messages) if m['role'] in ('system', 'developer')), None)
    else:
        location = prompt_location
    replay = {'version': 1, 'request': body, 'prompt_location': location,
              'request_sha256': fingerprint(body)}
    try:
        prompt = validate_replay(replay)
    except ValueError:
        raise ImportIssue('invalid_replay_or_missing_prompt')
    if not source_judge:
        # Reuse upstream response extraction and evaluability. Its canonicalized request
        # is for detection only; the worker receives body above with all original roles.
        canonical = {'id': str(row.get('id', row.get('request_id', fingerprint(raw)[:20]))),
                     'messages': messages, 'model': request.get('model'), 'response': response}
        if is_responses or response.get('object') == 'response' or 'output' in response:
            canonical.update(call_type='responses', messages={'input': messages, 'instructions': body.get('instructions')})
        sample, dropped, _ = upstream.normalize_row(upstream.RawRow(canonical, 'local', 0))
        if dropped or sample is None:
            raise ImportIssue(dropped.eval_block_reason.value if dropped else 'normalization_failed')
        expected = {'reference': sample.golden.text}
        if 'expected' in row:
            answer = row['expected']
            try:
                expected['answer'] = strict_json(answer) if isinstance(answer, str) else answer
            except ValueError:
                expected['answer'] = answer
        else:
            try:
                expected['answer'] = strict_json(sample.golden.text)
            except ValueError:
                pass
    ident = str(row.get('id', row.get('request_id', fingerprint(raw)[:20])))
    case = {'id': ident, 'input': {'question': message_text(messages[-1])},
            'turns': [message_text(messages[-1])], 'replay': replay,
            'metadata': {'format': source_format, 'source_model': request.get('model'),
                         'source_sha256': fingerprint(raw), 'request_sha256': fingerprint(body),
                         'prompt_group': fingerprint(prompt)[:16], 'label_status': 'not_provided' if source_judge else 'unreviewed',
                         'reference_kind': 'source_judge' if source_judge else ('supplied_expected' if 'expected' in row else 'recorded_response'),
                         'intentional_changes': ['candidate model selected by run', 'store=false', 'stream=false'],
                         'captured_controls': {k: request[k] for k in ('model', 'store', 'stream') if k in request}}}
    if source_judge:
        case['metadata']['recorded_response_present'] = response is not None
    else:
        case['expected'] = expected
    if mapping and 'group' in mapping:
        case['metadata']['evaluation_group'] = at(raw, mapping['group'])
    return case


def import_logs(paths, *, mapping=None, prompt_location=None, reference_mode="standard"):
    if reference_mode not in ("standard", "source_judge"):
        raise ValueError("reference_mode must be standard or source_judge")
    cases, dropped, seen_ids, seen_requests = [], [], set(), {}
    sources = []
    found = 0
    for path in paths:
        p = Path(path)
        sources.append({'path': str(p.resolve()), 'sha256': __import__('hashlib').sha256(p.read_bytes()).hexdigest()})
        for index, raw in records(p):
            found += 1
            try:
                case = normalize(raw, mapping=mapping, prompt_location=prompt_location, reference_mode=reference_mode)
                if case['id'] in seen_ids:
                    raise ImportIssue('duplicate_case_id')
                request_hash = case['replay']['request_sha256']
                if request_hash in seen_requests:
                    reason = 'duplicate_request' if seen_requests[request_hash] == case.get('expected') else 'conflicting_reference'
                    raise ImportIssue(reason)
                seen_ids.add(case['id'])
                seen_requests[request_hash] = case.get('expected')
                case['metadata'].update(source_file=p.name, source_path=str(p.resolve()), source_row=index)
                cases.append(case)
            except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
                dropped.append({'source_file': p.name, 'source_path': str(p.resolve()), 'source_row': index,
                                'reason': getattr(exc, 'code', 'malformed_record')})
    return cases, {'found': found, 'accepted': len(cases), 'dropped': len(dropped),
                   'reconciles': found == len(cases) + len(dropped),
                   'formats': dict(Counter(c['metadata']['format'] for c in cases)),
                   'drop_reasons': dict(Counter(d['reason'] for d in dropped)),
                   'excluded_records': dropped, 'sources': sources, 'reference_mode': reference_mode,
                   'scope': 'Text request replay. No tool execution, raw PDF/image input or automatic ground truth.'}


def write_import(cases, report, destination):
    dest = Path(destination)
    if dest.exists() and any(dest.iterdir()):
        raise ValueError('Use a new import output directory; existing imports are immutable')
    dest.mkdir(parents=True, exist_ok=True)
    groups = {}
    for case in cases:
        group = case['metadata']['prompt_group']
        groups.setdefault(group, []).append(case)
    for group, members in groups.items():
        folder = dest / group
        folder.mkdir()
        (folder / 'prompt.txt').write_text(validate_replay(members[0]['replay']))
        (folder / 'cases.jsonl').write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in members))
    report = dict(report, prompt_groups={g: len(c) for g, c in groups.items()})
    (dest / 'import-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    return report
