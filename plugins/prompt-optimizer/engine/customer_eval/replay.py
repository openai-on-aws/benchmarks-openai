"""One captured text request in the existing worker; no additional execution engine."""
from copy import deepcopy
import hashlib
import json


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def message_text(message):
    content = message.get('content')
    if isinstance(content, str):
        return content
    if not isinstance(content, list) or not content:
        raise ValueError('Message content must be nonempty text')
    parts = []
    for part in content:
        if not isinstance(part, dict) or set(part) != {'type', 'text'} or part['type'] not in ('text', 'input_text', 'output_text') or not isinstance(part['text'], str):
            raise ValueError('Unsupported content part; files, images and tool results are not text replay')
        parts.append(part['text'])
    return '\n'.join(parts)


def validate_replay(replay, prompt=None):
    if not isinstance(replay, dict) or replay.get('version') != 1:
        raise ValueError('Unsupported replay version')
    body = replay.get('request')
    allowed = {'input', 'instructions', 'text', 'reasoning', 'temperature', 'top_p', 'service_tier', 'max_output_tokens'}
    if not isinstance(body, dict) or set(body) - allowed:
        raise ValueError('Unsupported replay request fields')
    messages = body.get('input')
    if not isinstance(messages, list) or not messages:
        raise ValueError('Replay needs recorded messages')
    for m in messages:
        if not isinstance(m, dict) or set(m) - {'role', 'content', 'type'} or m.get('type', 'message') != 'message':
            raise ValueError('Only complete text messages can be replayed')
        if m.get('role') not in ('system', 'developer', 'user', 'assistant') or not message_text(m).strip():
            raise ValueError('Invalid message role or empty content')
    if messages[-1]['role'] != 'user':
        raise ValueError('Replay must end with the user input, not the captured answer')
    if 'instructions' in body and (not isinstance(body['instructions'], str) or not body['instructions'].strip()):
        raise ValueError('Instructions must be nonempty text')
    if 'max_output_tokens' in body and (type(body['max_output_tokens']) is not int or body['max_output_tokens'] < 1):
        raise ValueError('Invalid captured output limit')
    location = replay.get('prompt_location')
    if location == 'instructions':
        original = body.get('instructions')
    elif type(location) is int and 0 <= location < len(messages) and messages[location]['role'] in ('system', 'developer'):
        original = message_text(messages[location])
    else:
        raise ValueError('Invalid editable prompt location')
    if not isinstance(original, str) or not original.strip() or (prompt is not None and original != prompt):
        raise ValueError('Recorded prompt does not match this prompt group')
    if replay.get('request_sha256') != fingerprint(body):
        raise ValueError('Captured request hash mismatch')
    return original


def request_body(replay, prompt, policy=''):
    """Replace only the declared prompt slot; preserve the remaining captured context."""
    validate_replay(replay)
    if policy.strip():
        raise ValueError('Replay policy must already be captured in the original messages')
    body = deepcopy(replay['request'])
    location = replay['prompt_location']
    if location == 'instructions':
        body['instructions'] = prompt
    else:
        original = body['input'][location]
        if message_text(original) != prompt:
            body['input'][location] = dict(original, content=prompt)
    body['stream'] = False
    return body
